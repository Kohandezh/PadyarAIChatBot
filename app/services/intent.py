"""Trained intent classifier — this installation's own model, on its own data.

A multinomial logistic-regression head is trained over the local sentence
embeddings of every question in the knowledge base, mapping a visitor query
straight to a dataset entry with a probability. Training runs at every
reindex (dataset edits in the panel retrain it automatically) and takes well
under a second at this corpus size; inference is one matrix product.

Every training run holds out a stratified sample and measures its accuracy, so
the quality of the deployed classifier is measured, never assumed.

THE MODEL IS A VERSIONED ARTIFACT, LOADED ONLY WHEN PROVABLY CURRENT
--------------------------------------------------------------------
See docs/features/intent-model/SPEC.md and ADR-025. In config.INTENT_MODEL_DIR:

    intent-classifier.npz            the weights: plain numeric arrays plus the
                                     class labels as strings. No pickle.
    intent-classifier.json           the sidecar: accuracy, corpus size,
                                     version, sha256 of the weights, training
                                     fingerprint. Readable with `cat`.
    intent-classifier.history.jsonl  one line per NEW version, capped.

A model read from disk that no longer matches the dataset is a silent
wrong-answer bug. So a stored model is served only when its training
fingerprint (a sha256 over everything that determines the fit) equals the
fingerprint of the current data AND its weights match the sha256 in its
sidecar. In every other case the reindex trains, exactly as before this file
had a load path. The weights are parsed with numpy's allow_pickle=False:
unpickling a file is running code from it, and this runs on the boot path.

Writing is opt-in. The app lifespan (app/main.py) turns it on, and so does
scripts/train/train_intent_model.py, on purpose. A plain call to
load_dataset_internal() (scripts/debug_similarity.py, run_eval.py --golden)
never writes or deletes these files. The lifespan cannot tell a real server
from a TestClient harness, so a script that boots the app with TestClient
must point INTENT_MODEL_DIR at a temp directory first (run_eval.py
--conversations does; tests/test_intent_artifact.py checks every script).

Nothing here may break a reindex. Every read, check, load and write is
wrapped, logged and swallowed; the reindex then trains in memory. The wrap is
narrow: it never covers train() itself.
"""
import hashlib
import io
import json
import logging
import math
import os
import stat
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from app import config
from app.config import logger
from app.services import embeddings

try:
    import fcntl
except ImportError:  # pragma: no cover — not POSIX; writers then go unlocked
    fcntl = None

# Bump when the files change shape. An artifact with any other value is not
# read, it is retrained over.
FORMAT_VERSION = 1

WEIGHTS_FILENAME = "intent-classifier.npz"
METADATA_FILENAME = "intent-classifier.json"
HISTORY_FILENAME = "intent-classifier.history.jsonl"
LOCK_FILENAME = ".intent-artifact.lock"
# Distinctive on purpose: INTENT_MODEL_DIR can put these files anywhere,
# including inside the repository, so .gitignore names this prefix exactly
# rather than ignoring every dot-tmp file in the tree.
TEMP_FILE_PREFIX = ".intent-artifact-tmp-"

# The directory is created owner-writable only. File modes stop reading;
# replacing a file (what a forger does) is governed by the directory's write
# bit, and a umask of 002 would otherwise make it group-writable.
DIR_MODE = 0o755

# A writer waits at most this long for another writer's lock, then gives up
# recording (the reindex still serves its model). The write window is
# milliseconds; this only bounds a writer that hangs, e.g. a suspended CLI.
LOCK_TIMEOUT_SECONDS = 10.0

# Largest weights or sidecar file the load path will read. Real weights are
# kilobytes to a few megabytes; this stops a planted huge file from filling
# memory on every boot.
MAX_FILE_BYTES = 64 * 1024 * 1024

# One line per new version. 500 lines is years of admin edits at a few
# hundred bytes each; older lines are dropped, the newest are kept.
HISTORY_MAX_LINES = 500

# Everything train() is configured with. It is part of the training
# fingerprint, so changing any value here retrains every install once.
HYPERPARAMETERS = {
    # C=50: with 60 classes over 256-dim static embeddings the default
    # regularization flattens the softmax so far that no prediction can
    # clear a trust threshold; measured holdout accuracy rose from 0.43
    # to 0.62 at C=50 with usable probability separation.
    "C": 50,
    "max_iter": 3000,
    "holdout_fraction": 0.15,
    "holdout_random_state": 7,
    "min_classes": 2,
    "min_samples": 20,
}

_recording = False


class IntentClassifier:
    """A trained head plus everything needed to describe the run that made it.

    `sample_count` is the WHOLE training corpus — the same N the run logs as
    "trained on N questions" — and `holdout_size` is the held-out slice on its
    own, so a reader of the sidecar can never mistake one for the other.

    `model_version` and `model_sha256` are None until the model is recorded
    or loaded. They name the record on disk this exact model matches, which is
    what the admin panel checks the files against (read_record).
    """

    def __init__(self, model, labels: List[str], embedding_model_name: str,
                 holdout_accuracy: Optional[float], sample_count: int = 0,
                 holdout_size: int = 0):
        self._model = model
        self.labels = labels
        self.embedding_model_name = embedding_model_name
        self.holdout_accuracy = holdout_accuracy
        self.sample_count = sample_count
        self.holdout_size = holdout_size
        self.training_fingerprint: Optional[str] = None
        self.model_version: Optional[int] = None
        self.model_sha256: Optional[str] = None
        self.loaded_from_artifact = False

    def classify(self, query: str) -> Tuple[Optional[str], float]:
        """Return (dataset_id, probability) for a raw query string."""
        m = embeddings._get_model(self.embedding_model_name)
        vec = np.asarray(m.encode([query]), dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm == 0:
            return None, 0.0
        probs = self._model.predict_proba(vec / norm)[0]
        best = int(np.argmax(probs))
        return self._model.classes_[best], float(probs[best])


def _new_estimator():
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=HYPERPARAMETERS["C"],
                              max_iter=HYPERPARAMETERS["max_iter"])


def train(vectors: np.ndarray, dataset_ids: List[str],
          embedding_model_name: str) -> Optional[IntentClassifier]:
    """Train on pre-computed normalized question vectors; None on failure so
    the pipeline silently keeps its other tiers.

    The ONLY training implementation. The reindex and
    scripts/train/train_intent_model.py both reach it through fit().
    """
    try:
        from sklearn.model_selection import train_test_split

        y = np.array(dataset_ids)
        classes, counts = np.unique(y, return_counts=True)
        if below_training_floor(dataset_ids):
            logger.warning("[intent] corpus too small to train a classifier")
            return None

        holdout_accuracy = None
        holdout_size = 0
        # Stratified holdout needs every class at least twice AND at least
        # one test sample per class — with many short intents (e.g. the 2026
        # program block: 31 intents / ~193 questions) 0.15*n can undershoot
        # the class count and the split raises. Bump the holdout just enough
        # to stay stratifiable; measure when possible, then refit on all.
        if counts.min() >= 2:
            n_holdout = max(math.ceil(HYPERPARAMETERS["holdout_fraction"] * len(y)),
                            len(classes))
            if n_holdout <= len(y) - len(classes):
                X_tr, X_te, y_tr, y_te = train_test_split(
                    vectors, y, test_size=n_holdout, stratify=y,
                    random_state=HYPERPARAMETERS["holdout_random_state"]
                )
                probe = _new_estimator()
                probe.fit(X_tr, y_tr)
                holdout_accuracy = float(probe.score(X_te, y_te))
                holdout_size = int(len(y_te))

        model = _new_estimator()
        model.fit(vectors, y)
        acc = f"{holdout_accuracy:.3f}" if holdout_accuracy is not None else "n/a"
        logger.info(
            f"[intent] trained on {len(y)} questions / {len(classes)} intents, "
            f"holdout accuracy={acc}"
        )
        return IntentClassifier(model, list(classes), embedding_model_name,
                                holdout_accuracy, len(y), holdout_size)
    except Exception as e:
        logger.error(f"[intent] training failed: {e}")
        return None


def below_training_floor(dataset_ids: List[str]) -> bool:
    """True when no model can be trained from this data, whatever else holds.

    A fact about the data alone, so a reindex that cannot even embed (a passing
    fault) can still tell "this install has no model" from "not this time"."""
    return (len(set(dataset_ids)) < HYPERPARAMETERS["min_classes"]
            or len(dataset_ids) < HYPERPARAMETERS["min_samples"])


def training_fingerprint(texts: List[str], dataset_ids: List[str],
                         embedding_model_name: str) -> str:
    """sha256 over everything that determines the trained model.

    The sorted (normalized question text, dataset_id) pairs, the embedding
    model and its revision, the hyperparameters, the file format, and the
    scikit-learn and numpy versions. Sorted, so the same questions in another
    row order are the same training data. The library versions make "this is
    the model the current code would train" strictly true: an upgrade
    retrains once, which costs under a second.
    """
    canonical = json.dumps({
        "format_version": FORMAT_VERSION,
        "embedding_model_name": embedding_model_name,
        "embedding_model_revision": embeddings.DEFAULT_MODEL_REVISION,
        "hyperparameters": HYPERPARAMETERS,
        "scikit_learn_version": _sklearn_version(),
        "numpy_version": np.__version__,
        "pairs": sorted([str(t), str(i)] for t, i in zip(texts, dataset_ids)),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fit(vectors: np.ndarray, texts: List[str], dataset_ids: List[str],
        embedding_model_name: str) -> Optional[IntentClassifier]:
    """train(), plus the fingerprint of the data it was trained on."""
    classifier = train(vectors, dataset_ids, embedding_model_name)
    if classifier is not None:
        classifier.training_fingerprint = training_fingerprint(
            texts, dataset_ids, embedding_model_name)
    return classifier


def load_or_train(vectors: np.ndarray, texts: List[str], dataset_ids: List[str],
                  embedding_model_name: str) -> Optional[IntentClassifier]:
    """The reindex's entry point: the stored model if it is provably the model
    for this data, else a freshly trained one."""
    try:
        fingerprint = training_fingerprint(texts, dataset_ids, embedding_model_name)
    except Exception as e:  # noqa: BLE001 — e.g. scikit-learn missing
        # Same log line and same outcome as a failure inside train(): no
        # classifier. Left unwrapped it surfaced in search.py as "Embedding
        # index build failed", which was false.
        logger.error(f"[intent] training failed: {e}")
        return None
    loaded = load_artifact(fingerprint, embedding_model_name,
                           n_features=int(vectors.shape[1]) if vectors.ndim == 2 else None)
    if loaded is not None:
        return loaded
    return fit(vectors, texts, dataset_ids, embedding_model_name)


# ── Where the files live, and who may write them ────────────────────────


def artifact_paths() -> Tuple[str, str]:
    """(weights path, sidecar path), resolved through config.INTENT_MODEL_DIR
    at call time so an operator or a test can repoint it without a restart."""
    directory = config.INTENT_MODEL_DIR
    return (os.path.join(directory, WEIGHTS_FILENAME),
            os.path.join(directory, METADATA_FILENAME))


def history_path() -> str:
    return os.path.join(config.INTENT_MODEL_DIR, HISTORY_FILENAME)


def enable_recording() -> None:
    """Called by the serving app's lifespan. Nothing else should need to."""
    global _recording
    _recording = True


def disable_recording() -> None:
    global _recording
    _recording = False


def recording_enabled() -> bool:
    """True only after the app lifespan (or the training CLI) turned it on,
    and only with a directory set.

    Off by default so a plain out-of-band reindex (the debug script, a test
    without a lifespan) can neither overwrite the install's model nor, when
    its corpus is too small to train, delete it. An empty INTENT_MODEL_DIR
    turns it off even in the app. Any app boot turns it on, including a
    TestClient harness: see the module docstring.
    """
    return _recording and bool((config.INTENT_MODEL_DIR or "").strip())


# ── Loading ─────────────────────────────────────────────────────────────


def load_artifact(fingerprint: str, embedding_model_name: str,
                  n_features: Optional[int] = None) -> Optional[IntentClassifier]:
    """The stored model, or None with the reason logged. NEVER raises.

    Read-only, so it runs whether or not recording is on. Every check below
    must pass; any single one failing means "retrain", never "serve anyway".

    An empty INTENT_MODEL_DIR means "no artifact at all". Without this check
    the paths would be relative, and the app would serve whatever pair of
    files sat in the directory it happened to start in.
    """
    if not (config.INTENT_MODEL_DIR or "").strip():
        return None
    try:
        return _load_checked(fingerprint, embedding_model_name, n_features)
    except _NotLoadable as e:
        logger.log(e.level, f"[intent] not loading the stored model, retraining: {e}")
    except Exception as e:  # noqa: BLE001 — a bad file must never break a reindex
        logger.warning(f"[intent] not loading the stored model, retraining: "
                       f"{type(e).__name__}: {e}")
    return None


class _NotLoadable(Exception):
    def __init__(self, message: str, level: int = logging.WARNING):
        super().__init__(message)
        self.level = level


def _load_checked(fingerprint, embedding_model_name, n_features):
    weights_path, meta_path = artifact_paths()
    if not os.path.exists(meta_path):
        raise _NotLoadable("no stored model yet", level=logging.INFO)
    try:
        meta = json.loads(_read_regular_file(meta_path).decode("utf-8"))
    except (OSError, ValueError):
        meta = None
    if not isinstance(meta, dict):
        raise _NotLoadable("the sidecar is not a readable JSON object")
    if meta.get("format_version") != FORMAT_VERSION:
        raise _NotLoadable(f"unknown format_version {meta.get('format_version')!r}")
    if (meta.get("embedding_model_name") != embedding_model_name
            or meta.get("embedding_model_revision") != embeddings.DEFAULT_MODEL_REVISION):
        raise _NotLoadable("the embedding model or its revision changed")
    if meta.get("training_fingerprint") != fingerprint:
        # The normal case after every content edit, so not a warning.
        raise _NotLoadable("the training fingerprint differs from the current data",
                           level=logging.INFO)

    try:
        payload = _read_regular_file(weights_path)
    except FileNotFoundError:
        raise _NotLoadable("the weights file is missing")
    # Hash and parse the SAME bytes, so nothing can change between the check
    # and the load.
    if hashlib.sha256(payload).hexdigest() != meta.get("model_sha256"):
        raise _NotLoadable("the weights do not match the sha256 in the sidecar")

    model, (accuracy, holdout_size, sample_count) = _deserialize(payload, n_features)
    if meta.get("class_count") != len(model.classes_) or not _is_int(meta.get("class_count")):
        raise _NotLoadable("class_count in the sidecar does not match the weights")
    version = meta.get("model_version")
    if not _is_int(version) or version < 1:
        raise _NotLoadable(f"invalid model_version {version!r}")
    # The trust gate in search.classify_intent_local reads the accuracy, so
    # the served number is the one inside the sha-covered weights file. The
    # sidecar is for people; it must say the same thing or it is refused.
    _check_same(meta, "holdout_accuracy", accuracy)
    _check_same(meta, "holdout_size", holdout_size)
    _check_same(meta, "sample_count", sample_count)

    classifier = IntentClassifier(
        model, list(model.classes_), embedding_model_name,
        accuracy, sample_count, holdout_size)
    classifier.training_fingerprint = fingerprint
    classifier.model_version = version
    classifier.model_sha256 = meta.get("model_sha256")
    classifier.loaded_from_artifact = True
    logger.info(f"[intent] loaded model version {version} from {weights_path} "
                f"(training skipped, data unchanged)")
    return classifier


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_same(meta: dict, field: str, measured) -> None:
    """The sidecar's `field` must be a plain number (or null for an accuracy
    that was never measured) and equal to the measured value in the weights.
    A string, a bool, NaN or a value nobody measured is refused."""
    value = meta.get(field)
    if measured is None:
        ok = value is None
    elif isinstance(measured, int):
        ok = _is_int(value) and value == measured
    else:
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value) and value == measured)
    if not ok:
        raise _NotLoadable(f"{field} in the sidecar is {value!r}, "
                           f"the weights file says {measured!r}")


def _read_regular_file(path: str, limit: Optional[int] = None) -> bytes:
    """The file's bytes, refusing anything that is not a plain file of at
    most `limit` bytes (default MAX_FILE_BYTES, read at call time). Opened
    non-blocking and checked on the open descriptor, so a FIFO or a device
    planted at the path cannot hang the boot, and a swap between the check
    and the read is not possible."""
    if limit is None:
        limit = MAX_FILE_BYTES
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _NotLoadable(f"{os.path.basename(path)} is not a regular file")
        if info.st_size > limit:
            raise _NotLoadable(f"{os.path.basename(path)} is larger than "
                               f"{limit} bytes")
        with os.fdopen(fd, "rb") as fh:
            fd = None
            payload = fh.read(limit + 1)
    finally:
        if fd is not None:
            os.close(fd)
    if len(payload) > limit:
        raise _NotLoadable(f"{os.path.basename(path)} is larger than {limit} bytes")
    return payload


def _read_sidecar(meta_path: str) -> Optional[dict]:
    try:
        meta = json.loads(_read_regular_file(meta_path).decode("utf-8"))
    except (OSError, ValueError, _NotLoadable):
        return None
    return meta if isinstance(meta, dict) else None


def _serialize(classifier: IntentClassifier) -> bytes:
    """Plain arrays only. np.savez output is byte-identical for identical
    arrays, which is what lets an identical retrain keep its version.

    The weights keep the dtype the fit produced. scikit-learn fits float32
    embeddings in float32; casting to float64 here would make the loaded
    model's probabilities differ from the trained one's in the 8th digit.

    `holdout` is [accuracy (NaN = not measured), holdout size, sample count].
    It lives here, under the sha256, because the trust gate reads the
    accuracy: editing it must cost as much as rewriting the weights."""
    model = classifier._model
    accuracy = classifier.holdout_accuracy
    buf = io.BytesIO()
    np.savez(buf,
             coef=np.asarray(model.coef_),
             intercept=np.asarray(model.intercept_),
             classes=np.asarray(model.classes_).astype(str),
             holdout=np.array([np.nan if accuracy is None else accuracy,
                               classifier.holdout_size, classifier.sample_count],
                              dtype=np.float64))
    return buf.getvalue()


def _deserialize(payload: bytes, n_features: Optional[int]):
    """Rebuild the estimator and the measured holdout numbers from plain
    arrays. allow_pickle=False makes numpy refuse any object array, which is
    the only way a pickle gets in."""
    with np.load(io.BytesIO(payload), allow_pickle=False) as npz:
        if sorted(npz.files) != ["classes", "coef", "holdout", "intercept"]:
            raise _NotLoadable(f"unexpected arrays {sorted(npz.files)}")
        coef, intercept, classes = npz["coef"], npz["intercept"], npz["classes"]
        holdout = npz["holdout"]

    n_classes = len(classes) if classes.ndim == 1 else -1
    rows = 1 if n_classes == 2 else n_classes
    if (coef.dtype.kind != "f" or intercept.dtype.kind != "f"
            or classes.dtype.kind != "U" or coef.ndim != 2
            or intercept.ndim != 1 or n_classes < 2
            or coef.shape[0] != rows or intercept.shape[0] != rows
            or not np.all(np.isfinite(coef)) or not np.all(np.isfinite(intercept))):
        raise _NotLoadable("the weights have the wrong types or shapes")
    if n_features is not None and coef.shape[1] != n_features:
        raise _NotLoadable(f"the weights expect {coef.shape[1]} features, "
                           f"the embeddings have {n_features}")

    model = _new_estimator()
    model.coef_ = coef
    model.intercept_ = intercept
    model.classes_ = classes
    model.n_features_in_ = coef.shape[1]
    return model, _holdout_numbers(holdout, n_classes)


def _holdout_numbers(holdout, n_classes: int):
    """(accuracy or None, holdout size, sample count), checked the way train()
    produces them: an accuracy exists exactly when a holdout ran, it is a
    fraction of that holdout, and the counts are whole and consistent."""
    if holdout.dtype.kind != "f" or holdout.shape != (3,):
        raise _NotLoadable("the holdout numbers have the wrong type or shape")
    raw_accuracy, raw_size, raw_count = (float(v) for v in holdout)
    if not (math.isfinite(raw_size) and math.isfinite(raw_count)
            and raw_size.is_integer() and raw_count.is_integer()):
        raise _NotLoadable("holdout_size and sample_count must be whole numbers")
    size, count = int(raw_size), int(raw_count)
    if count < max(n_classes, HYPERPARAMETERS["min_samples"]) or not 0 <= size < count:
        raise _NotLoadable(f"implausible holdout_size {size} / sample_count {count}")
    if math.isnan(raw_accuracy):
        if size != 0:
            raise _NotLoadable("holdout_accuracy is missing for a holdout that ran")
        return None, size, count
    if size == 0 or not 0.0 <= raw_accuracy <= 1.0:
        raise _NotLoadable(f"implausible holdout_accuracy {raw_accuracy!r}")
    correct = raw_accuracy * size
    if abs(correct - round(correct)) > 1e-6:
        raise _NotLoadable(f"holdout_accuracy {raw_accuracy!r} is not a fraction "
                           f"of {size} held-out questions")
    return raw_accuracy, size, count


# ── Reading the record for people ───────────────────────────────────────

# The history rows the admin card shows. The file keeps HISTORY_MAX_LINES.
HISTORY_SHOWN = 10

# Largest history file the panel reads. _append_history keeps
# HISTORY_MAX_LINES rows of well under 1 KB each; 4 KB a row leaves room for
# any future field, and a bigger file was not written by this code. The
# panel reads it on every page load, so the 64 MB weights cap is far too big.
HISTORY_MAX_BYTES = HISTORY_MAX_LINES * 4096

_HISTORY_FIELDS = ("model_version", "trained_at", "holdout_accuracy",
                   "holdout_size", "sample_count", "class_count")


def read_record(serving: Optional[IntentClassifier]) -> dict:
    """What the admin panel shows about this install's own model. NEVER raises.

    `serving` is the classifier this process answers with
    (search.intent_classifier). It came through the checked load path or a
    fresh training, so it is the source of truth; the files only confirm it.

    `state` is one of:
      - "trained":    a model is serving, its sidecar says exactly what the
                      serving model says (version, sha256, fingerprint,
                      holdout numbers, classes, embedding model, library
                      versions), and the weights file hashes to the serving
                      sha256. Only `trained_at` comes from the sidecar alone;
      - "not_saved":  INTENT_MODEL_DIR is empty, so by the operator's choice
                      the install keeps no record. A model is serving, and
                      its facts come from memory; there is no version, sha256
                      or date to show, and nothing is read from disk;
      - "none":       no model is serving and there is no sidecar;
      - "unreadable": anything else: a sidecar edited to plausible values,
                      other weights, a record with no serving model, a serving
                      model that was never recorded. The chatbot still answers;
                      the card only refuses to vouch for numbers it cannot
                      confirm.

    The weights are hashed, never parsed. Nothing is written, no lock is
    taken, the directory is never created, nothing is trained.

    The history stands apart: each row is checked alone, a row that fails is
    skipped, and the history never changes `state`. When the model is
    confirmed, a row newer than it, or a row for its version that disagrees
    with it, is skipped too. `history_status` is "ok", "partial" (rows were
    skipped) or "unreadable" (the file itself).

    The result holds no path, no directory, no file name, no exception text.
    """
    needs = {"questions": HYPERPARAMETERS["min_samples"],
             "topics": HYPERPARAMETERS["min_classes"]}
    try:
        state, model = _confirm_serving_model(serving)
    except Exception as e:  # noqa: BLE001 — the panel must never 500 on a bad file
        logger.warning(f"[intent] could not read the model record for the panel: "
                       f"{type(e).__name__}")
        state, model = "unreadable", None
    try:
        history, history_status = _read_history_for_display(
            model if state == "trained" else None)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[intent] could not read the model history for the panel: "
                       f"{type(e).__name__}")
        history, history_status = [], "unreadable"
    return {"state": state, "model": model, "history": history,
            "history_status": history_status, "needs": needs}


def _confirm_serving_model(serving: Optional[IntentClassifier]):
    """(state, model facts or None). See read_record."""
    if not (config.INTENT_MODEL_DIR or "").strip():
        # Checked first: with no directory the paths below are relative, and
        # would read whatever files sit where the app happened to start.
        if serving is None:
            return "none", None
        return "not_saved", {**_describe_serving(serving, recorded=False),
                             "trained_at": None}
    weights_path, meta_path = artifact_paths()
    has_sidecar = os.path.lexists(meta_path)
    if serving is None:
        return ("unreadable" if has_sidecar else "none"), None
    expected = _describe_serving(serving)
    if expected is None or not has_sidecar:
        return "unreadable", None
    meta = _read_sidecar(meta_path)
    if (meta is None or meta.get("format_version") != FORMAT_VERSION
            or not all(_same(meta.get(k), v) for k, v in expected.items())
            or not _is_iso_time(meta.get("trained_at"))):
        return "unreadable", None
    try:
        weights = _read_regular_file(weights_path)
    except (OSError, _NotLoadable):
        return "unreadable", None
    if hashlib.sha256(weights).hexdigest() != expected["model_sha256"]:
        return "unreadable", None
    return "trained", {**expected, "trained_at": meta["trained_at"]}


def _describe_serving(serving: IntentClassifier, recorded: bool = True) -> Optional[dict]:
    """The sidecar fields as the serving model states them, or None for a
    model that was never recorded (recording off, or the write failed).
    With `recorded=False` the version and sha256 are None: a model trained
    only in memory has neither.

    The revision and the library versions are this process's own: they are
    inside the training fingerprint, so a serving model whose fingerprint
    matches the sidecar was trained under exactly these."""
    version, sha = serving.model_version, serving.model_sha256
    if not recorded:
        version, sha = None, None
    elif not _is_int(version) or not isinstance(sha, str) or not serving.training_fingerprint:
        return None
    return {
        "model_version": version,
        "model_sha256": sha,
        "training_fingerprint": serving.training_fingerprint,
        "holdout_accuracy": serving.holdout_accuracy,
        "holdout_size": serving.holdout_size,
        "sample_count": serving.sample_count,
        "class_count": len(serving.labels),
        "embedding_model_name": serving.embedding_model_name,
        "embedding_model_revision": embeddings.DEFAULT_MODEL_REVISION,
        "scikit_learn_version": _sklearn_version(),
        "numpy_version": np.__version__,
    }


def _same(value, expected) -> bool:
    """Equal AND the same type, so `true` is not 1 and "1" is not 1."""
    return type(value) is type(expected) and value == expected


def _is_iso_time(value) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _read_history_for_display(confirmed: Optional[dict]) -> Tuple[list, str]:
    """(the newest HISTORY_SHOWN valid rows, newest first; history_status).

    `confirmed` is the model the files were just confirmed against, or None.
    Rows are streamed into a deque, so only HISTORY_SHOWN are ever kept."""
    if not (config.INTENT_MODEL_DIR or "").strip():
        return [], "ok"
    try:
        raw = _read_regular_file(history_path(), HISTORY_MAX_BYTES)
    except FileNotFoundError:
        return [], "ok"
    except (OSError, _NotLoadable):
        return [], "unreadable"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], "unreadable"
    rows, skipped = deque(maxlen=HISTORY_SHOWN), 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if _is_history_row(row) and _agrees_with(row, confirmed):
            rows.append({f: row[f] for f in _HISTORY_FIELDS})
        else:
            skipped += 1
    return list(reversed(rows)), ("partial" if skipped else "ok")


def _agrees_with(row: dict, confirmed: Optional[dict]) -> bool:
    """A row cannot be newer than the confirmed model, and the row for the
    confirmed version must say what the confirmed model says. Older rows
    have no weights left to check, so they pass."""
    if confirmed is None:
        return True
    if row["model_version"] > confirmed["model_version"]:
        return False
    if row["model_version"] == confirmed["model_version"]:
        return all(_same(row[f], confirmed[f]) for f in _HISTORY_FIELDS)
    return True


def _is_history_row(row) -> bool:
    """A row is shown when its format is one this code knows (any version up
    to the current one: the file keeps its rows across a format bump) and the
    six shown fields hold values train() could have written. The holdout
    numbers go through _holdout_numbers, the load path's own check.

    History rows describe past versions whose weights are gone, so unlike
    the current record they cannot be cross-checked, only sanity-checked."""
    if not isinstance(row, dict):
        return False
    fmt = row.get("format_version")
    if not _is_int(fmt) or not 1 <= fmt <= FORMAT_VERSION:
        return False
    if any(f not in row for f in _HISTORY_FIELDS) or not _is_iso_time(row["trained_at"]):
        return False
    version, size, count, classes = (row["model_version"], row["holdout_size"],
                                     row["sample_count"], row["class_count"])
    if not all(_is_int(v) for v in (version, size, count, classes)):
        return False
    if version < 1 or classes < HYPERPARAMETERS["min_classes"]:
        return False
    accuracy = row["holdout_accuracy"]
    # Inside the weights NaN means "not measured"; a JSON row says that with
    # null, so a NaN here is an edit.
    if accuracy is not None and (isinstance(accuracy, bool)
                                 or not isinstance(accuracy, (int, float))
                                 or not math.isfinite(accuracy)):
        return False
    try:
        _holdout_numbers(np.array([np.nan if accuracy is None else accuracy, size, count],
                                  dtype=np.float64), classes)
    except _NotLoadable:
        return False
    return True


# ── Recording ───────────────────────────────────────────────────────────


def record_artifact(classifier: Optional[IntentClassifier],
                    no_model_for_this_data: bool = True) -> Optional[dict]:
    """Publish what this reindex produced: the files, then the gauges.

    - recording off: the files are not touched at all;
    - None and `no_model_for_this_data`: training refused this data (below
      the floor) or failed on it. An earlier record is removed, so no file
      claims a model this install does not have. The history stays;
    - None otherwise: a passing fault (no embeddings this boot, a failed
      read). The data may be unchanged, so the record is kept; the next
      healthy reindex loads it under the same version;
    - a model loaded from the files: nothing to write;
    - a trained model: recorded as the next version, unless the identical
      model (same fingerprint, same bytes) is already the one on disk.

    Returns the sidecar describing the model, or None. NEVER raises: reindex
    runs on the boot path and on every admin content edit.
    """
    meta = None
    if recording_enabled():
        try:
            if classifier is None:
                if no_model_for_this_data:
                    # Under the writers' lock, so a removal cannot interleave
                    # with another worker's write. No directory, nothing to
                    # remove, and no reason to create one.
                    if os.path.isdir(config.INTENT_MODEL_DIR):
                        with _WriterLock():
                            _remove_artifact("no classifier can be trained on this data")
                else:
                    logger.info("[intent] no classifier on this reindex (no embeddings "
                                "or no question rows read); the stored model is kept")
            elif classifier.loaded_from_artifact:
                meta = _read_sidecar(artifact_paths()[1])
            else:
                with _WriterLock():
                    meta = _record_new_version(classifier)
        except Exception as e:  # noqa: BLE001 — a record is not worth a reindex
            logger.error(f"[intent] could not record the model artifact: {e}")
            meta = None
    _publish_gauges(classifier)
    return meta


def _publish_gauges(classifier: Optional[IntentClassifier]) -> None:
    """NaN, not 0.0, when there is no measurement or no recorded model. 0.0
    would read as "0% accurate" or "version 0", which are false statements an
    alert rule would act on."""
    accuracy = classifier.holdout_accuracy if classifier is not None else None
    version = classifier.model_version if classifier is not None else None
    try:
        from app.services import metrics
        metrics.intent_holdout_accuracy.set(
            float("nan") if accuracy is None else float(accuracy))
        metrics.intent_model_version.set(
            float("nan") if version is None else float(version))
    except Exception as e:  # noqa: BLE001
        logger.error(f"[intent] could not publish the model gauges: {e}")


class _WriterLock:
    """flock on a file in the artifact directory, held while one writer
    decides the next version and writes it.

    Every worker reindexes after an admin edit (the version poll in
    search.py). Without the lock two of them could both write version N+1,
    or leave one worker's weights beside the other's sidecar.
    """

    def __enter__(self):
        # mode only applies to a directory created here; an existing one keeps
        # the mode the operator gave it.
        os.makedirs(config.INTENT_MODEL_DIR, mode=DIR_MODE, exist_ok=True)
        self._fh = open(os.path.join(config.INTENT_MODEL_DIR, LOCK_FILENAME), "a")
        if fcntl is None:
            return self
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    raise TimeoutError(f"another writer held the model lock for more "
                                       f"than {LOCK_TIMEOUT_SECONDS}s; not recording")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
        return False


def _record_new_version(classifier: IntentClassifier) -> dict:
    weights_path, meta_path = artifact_paths()
    payload = _serialize(classifier)
    sha = hashlib.sha256(payload).hexdigest()

    current = _read_sidecar(meta_path)
    if (current is not None and current.get("model_sha256") == sha
            and _record_is_loadable(classifier)):
        # The identical model is already recorded (another worker, or a
        # retrain of unchanged data), and the record passes every check a
        # load makes. Not a new version. A record that fails any check (an
        # edited sidecar, say) is replaced, even when the weights are equal.
        classifier.model_version = current.get("model_version")
        classifier.model_sha256 = sha
        return current

    version = max(_version_of(current), _last_history_version()) + 1
    meta = {
        "format_version": FORMAT_VERSION,
        "model_version": version,
        "model_file": WEIGHTS_FILENAME,
        "model_sha256": sha,
        "training_fingerprint": classifier.training_fingerprint,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holdout_accuracy": classifier.holdout_accuracy,
        "holdout_size": classifier.holdout_size,
        "sample_count": classifier.sample_count,
        "class_count": len(classifier.labels),
        "embedding_model_name": classifier.embedding_model_name,
        "embedding_model_revision": embeddings.DEFAULT_MODEL_REVISION,
        "hyperparameters": HYPERPARAMETERS,
        "scikit_learn_version": _sklearn_version(),
        "numpy_version": np.__version__,
    }
    _write_pair(payload, meta)
    classifier.model_version = version
    classifier.model_sha256 = sha
    logger.info(f"[intent] recorded model version {version} at {weights_path} "
                f"({len(payload)} bytes, sha256 {sha[:12]})")
    try:
        _append_history(meta)
    except Exception as e:  # noqa: BLE001 — the pair is already consistent
        logger.error(f"[intent] could not append to the model history: {e}")
    return meta


def _write_pair(payload: bytes, meta: dict) -> None:
    """Weights first, then the sidecar. Each write is atomic; the pair is not.
    If either fails, both files are cleared before re-raising: an absent
    record is honest, a sidecar describing other weights is not."""
    weights_path, meta_path = artifact_paths()
    try:
        # 0600 for the weights: they are derived from the customer's content.
        _atomic_write(weights_path, payload, 0o600)
        # 0644 for the sidecar: people read it, and the numbers are not secret.
        _atomic_write(meta_path, _json_bytes(meta, indent=2), 0o644)
    except Exception:
        try:
            _remove_artifact("the write failed part-way")
        except Exception as cleanup_error:  # noqa: BLE001
            logger.error(f"[intent] could not clear the mismatched record: {cleanup_error}")
        raise


def _record_is_loadable(classifier: IntentClassifier) -> bool:
    try:
        _load_checked(classifier.training_fingerprint,
                      classifier.embedding_model_name, None)
        return True
    except Exception:  # noqa: BLE001 — any failed check means "write anew"
        return False


def _append_history(meta: dict) -> None:
    """Append one line, keep the newest HISTORY_MAX_LINES. Rewritten whole
    through _atomic_write so a reader never sees a torn line."""
    path = history_path()
    lines = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [line.rstrip("\n") for line in fh if line.strip()]
    except FileNotFoundError:
        pass
    lines.append(_json_bytes(meta).decode("utf-8").rstrip("\n"))
    lines = lines[-HISTORY_MAX_LINES:]
    _atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"), 0o644)


def _last_history_version() -> int:
    try:
        with open(history_path(), "r", encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            return _version_of(json.loads(line))
        except ValueError:
            continue
    return 0


def _version_of(meta) -> int:
    version = meta.get("model_version") if isinstance(meta, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def _json_bytes(data: dict, indent: Optional[int] = None) -> bytes:
    text = json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _sklearn_version() -> str:
    import sklearn
    return sklearn.__version__


def _atomic_write(path: str, payload: bytes, mode: int) -> None:
    """Write through a temp file in the same directory, then os.replace.

    A reindex can run in a background thread (the cross-worker version poll in
    search.py) while an operator reads the sidecar. os.replace is atomic on one
    filesystem, so a reader sees the old file or the new one, never half.
    `mode` is explicit because mkstemp creates 0600.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=TEMP_FILE_PREFIX)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _remove_artifact(reason: str) -> None:
    """Drop the weights and the sidecar. The history is kept. Raises; the
    caller swallows.

    Logged whenever it removes something: these are weights trained from the
    customer's content, and an operator who finds the directory empty must be
    able to read why. Silent when there was nothing to remove, or a BM25-only
    install would log it on every reindex.
    """
    removed = []
    for path in artifact_paths():
        try:
            os.remove(path)
            removed.append(os.path.basename(path))
        except FileNotFoundError:
            pass
    if removed:
        logger.warning(f"[intent] removed the model record ({', '.join(removed)}): "
                       f"{reason}")
