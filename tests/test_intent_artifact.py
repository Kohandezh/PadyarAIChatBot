"""The trained intent model as a durable, versioned, verifiable artifact.

Every reindex trains this install's own logistic-regression head over its own
question embeddings. Before this file the weights lived only in process
memory: no file, no version, no recorded quality. An earlier write-only design
(a pickle nobody ever read back) was replaced, see ADR-025.

These tests pin the contract in docs/features/intent-model/SPEC.md:
  * a serving reindex leaves `.npz` weights plus a JSON sidecar that describes
    them, and a bounded history line per NEW version,
  * the stored model is loaded back only when it is provably the model for the
    current data: same training fingerprint, and weights whose sha256 matches
    the sidecar, parsed with numpy's allow_pickle=False,
  * every other case retrains, logs why, and never raises into the reindex,
  * an out-of-band reindex (no app lifespan) neither writes nor deletes,
  * nothing here changes what the chatbot answers.

The reindex tests run the real load_dataset_internal() over a real SQLite
database with a fake embedder, so they need no model download and no network.
Every deny test also asserts WHICH check refused, via the log line, so a deny
cannot pass because of an earlier, unrelated failure.
"""
import hashlib
import importlib.util
import io
import json
import logging
import os
import subprocess
import zlib

import numpy as np
import pytest

from app.services import embeddings, intent


ALPHA, BETA = "faq-alpha", "faq-beta"
MODEL_NAME = "test-embedding-model"


def _vector(text: str) -> np.ndarray:
    """A deterministic, linearly separable 8-dim embedding of `text`.

    Dimension 0 fires on "alpha", dimension 1 on "beta"; the rest is stable
    jitter so no two samples are identical (an all-identical class makes the
    fit degenerate and the holdout meaningless).
    """
    v = np.zeros(8, dtype=np.float32)
    v[0] = 1.0 if "alpha" in text else 0.0
    v[1] = 1.0 if "beta" in text else 0.0
    v[2] = (zlib.crc32(text.encode()) % 1000) / 5000.0
    v[3] = 0.25
    return v


def _normalized(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _corpus(per_class: int = 14, tag: str = "question"):
    """(vectors, texts, dataset_ids) that clear the 2-class/20-sample floor."""
    texts, ids = [], []
    for i in range(per_class):
        texts.append(f"alpha {tag} {i}")
        ids.append(ALPHA)
        texts.append(f"beta {tag} {i}")
        ids.append(BETA)
    vectors = _normalized(np.vstack([_vector(t) for t in texts]))
    return vectors, texts, ids


class _FakeEmbeddingModel:
    """Stands in for model2vec (a ~100MB download; the suite runs offline)."""

    def encode(self, texts):
        return np.vstack([_vector(t) for t in texts])


def _sha256_file(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read_json(path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _history_lines() -> list:
    path = intent.history_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_json(path, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


@pytest.fixture
def artifact_dir(tmp_path, monkeypatch):
    import app.config as config
    target = tmp_path / "intent-model"
    monkeypatch.setattr(config, "INTENT_MODEL_DIR", str(target))
    return target


@pytest.fixture
def recording(artifact_dir):
    """What the app lifespan does: turn recording on for this process."""
    intent.enable_recording()
    yield artifact_dir
    intent.disable_recording()


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "_get_model", lambda name: _FakeEmbeddingModel())


@pytest.fixture
def trained(artifact_dir):
    clf = intent.fit(*_corpus(), MODEL_NAME)
    assert clf is not None, "the corpus fixture must clear the training floor"
    return clf


@pytest.fixture
def indexed_corpus(tmp_path, monkeypatch, artifact_dir, fake_embedder):
    """A real reindex over a real (sqlite) database with a fake embedder."""
    import app.config as cfg
    import app.db.connection as dbc

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "reindex.db"))
    monkeypatch.setattr(cfg, "SEED_DEFAULT_CONTENT", False)
    dbc.init_db()

    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    conn.executemany(
        "INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, '')",
        [(ALPHA, "alpha topic", "alpha answer"), (BETA, "beta topic", "beta answer")],
    )
    conn.executemany(
        "INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, '')",
        [(f"alpha question {i}", ALPHA) for i in range(14)]
        + [(f"beta question {i}", BETA) for i in range(14)],
    )
    conn.commit()
    conn.close()
    return artifact_dir


@pytest.fixture
def serving(indexed_corpus):
    intent.enable_recording()
    yield indexed_corpus
    intent.disable_recording()


@pytest.fixture
def train_calls(monkeypatch):
    """Counts every call into the one training function, and still trains."""
    real_train = intent.train
    calls = []

    def spy(*args, **kwargs):
        calls.append(1)
        return real_train(*args, **kwargs)

    monkeypatch.setattr(intent, "train", spy)
    return calls


def _execute(sql, params=()):
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _current_fingerprint():
    from app.services import search
    labels = [q.get("dataset_id", "") for q in search.questions_data]
    return intent.training_fingerprint(search.normalized_questions, labels,
                                      embeddings.DEFAULT_MODEL)


PROBE_QUERIES = [f"alpha question {i}" for i in range(14)] + [
    f"beta question {i}" for i in range(14)] + ["alpha", "beta", "alpha beta"]


def _answers(classifier):
    return [classifier.classify(q) for q in PROBE_QUERIES]


def _assert_same_answers(first, second):
    for (id_a, p_a), (id_b, p_b) in zip(_answers(first), _answers(second)):
        assert id_a == id_b
        assert abs(p_a - p_b) <= 1e-9


# ── B1 / B2: the files and what the sidecar says ────────────────────────


def test_a_serving_reindex_writes_the_weights_and_the_sidecar(serving):
    from app.services import search

    search.load_dataset_internal()

    assert search.intent_classifier is not None, "the corpus must train"
    weights, sidecar = intent.artifact_paths()
    assert weights.endswith(".npz")
    assert os.path.getsize(weights) > 0
    assert os.path.getsize(sidecar) > 0


SIDECAR_KEYS = {
    "format_version", "model_version", "model_sha256", "training_fingerprint",
    "trained_at", "holdout_accuracy", "holdout_size", "sample_count",
    "class_count", "embedding_model_name", "embedding_model_revision",
    "hyperparameters", "scikit_learn_version", "numpy_version",
}


def test_the_sidecar_describes_exactly_the_weights_and_the_run(serving):
    import sklearn
    from app.services import search

    search.load_dataset_internal()
    clf = search.intent_classifier
    weights, sidecar = intent.artifact_paths()
    meta = _read_json(sidecar)

    assert SIDECAR_KEYS <= set(meta), f"missing: {SIDECAR_KEYS - set(meta)}"
    assert meta["format_version"] == intent.FORMAT_VERSION
    assert meta["model_version"] == 1
    assert meta["model_sha256"] == _sha256_file(weights)
    assert meta["training_fingerprint"] == _current_fingerprint()
    assert meta["trained_at"].endswith("+00:00"), "the timestamp must be UTC"
    assert clf.holdout_accuracy is not None
    assert meta["holdout_accuracy"] == clf.holdout_accuracy
    assert meta["holdout_size"] == clf.holdout_size > 0
    assert meta["sample_count"] == 28, "sample_count is the whole corpus"
    assert meta["class_count"] == 2
    assert meta["embedding_model_name"] == embeddings.DEFAULT_MODEL
    assert meta["embedding_model_revision"] == embeddings.DEFAULT_MODEL_REVISION
    assert meta["hyperparameters"] == intent.HYPERPARAMETERS
    assert meta["scikit_learn_version"] == sklearn.__version__
    assert meta["numpy_version"] == np.__version__


def test_the_weights_are_plain_arrays_that_load_without_pickle(serving):
    from app.services import search

    search.load_dataset_internal()
    weights, _ = intent.artifact_paths()
    with np.load(weights, allow_pickle=False) as npz:
        assert sorted(npz.files) == ["classes", "coef", "holdout", "intercept"]
        assert npz["classes"].dtype.kind == "U"
        assert npz["coef"].dtype.kind == "f"


def test_the_first_version_writes_one_history_line(serving):
    from app.services import search

    search.load_dataset_internal()
    lines = _history_lines()
    assert len(lines) == 1
    assert lines[0]["model_version"] == 1
    assert lines[0]["model_sha256"] == _read_json(intent.artifact_paths()[1])["model_sha256"]


# ── B3 / B9: an unchanged dataset is loaded, not retrained ──────────────


def test_an_unchanged_dataset_is_loaded_not_retrained(serving, train_calls):
    from app.services import search

    search.load_dataset_internal()
    first = search.intent_classifier
    assert len(train_calls) == 1
    _, sidecar = intent.artifact_paths()
    sidecar_before = open(sidecar, "rb").read()

    search.intent_classifier = None
    search.load_dataset_internal()
    second = search.intent_classifier

    assert len(train_calls) == 1, "an unchanged dataset must not retrain"
    assert second is not first
    assert second.loaded_from_artifact is True
    assert second.model_version == first.model_version == 1
    assert open(sidecar, "rb").read() == sidecar_before
    assert len(_history_lines()) == 1
    assert second.holdout_accuracy == first.holdout_accuracy
    _assert_same_answers(first, second)


def test_a_loaded_model_answers_exactly_like_the_trained_one(recording, trained, fake_embedder):
    meta = intent.record_artifact(trained)
    assert meta is not None

    loaded = intent.load_artifact(trained.training_fingerprint, MODEL_NAME, n_features=8)

    assert loaded is not None
    assert loaded.loaded_from_artifact is True
    assert list(loaded.labels) == list(trained.labels)
    vectors, _, _ = _corpus(per_class=20, tag="probe")
    diff = np.abs(loaded._model.predict_proba(vectors) - trained._model.predict_proba(vectors))
    assert diff.max() <= 1e-9
    _assert_same_answers(trained, loaded)


# ── B4: a changed dataset gets a new version ────────────────────────────


@pytest.mark.parametrize("change", [
    ("UPDATE questions SET question = ? WHERE question = ?",
     ("alpha question changed", "alpha question 0")),
    ("UPDATE questions SET dataset_id = ? WHERE question = ?",
     (BETA, "alpha question 1")),
], ids=["one-question-text", "one-mapping"])
def test_a_changed_dataset_is_retrained_as_the_next_version(serving, train_calls, change):
    from app.services import search

    search.load_dataset_internal()
    old = _read_json(intent.artifact_paths()[1])

    _execute(*change)
    search.load_dataset_internal()
    new = _read_json(intent.artifact_paths()[1])

    assert len(train_calls) == 2
    assert search.intent_classifier.loaded_from_artifact is False
    assert new["model_version"] == old["model_version"] + 1 == 2
    assert new["training_fingerprint"] != old["training_fingerprint"]
    assert new["training_fingerprint"] == _current_fingerprint()
    lines = _history_lines()
    assert [line["model_version"] for line in lines] == [1, 2]
    assert lines[-1]["training_fingerprint"] == new["training_fingerprint"]


# ── B5 / B6 / B7: the deny cases, each with its allow-control ───────────


def test_tampered_weights_are_never_served(serving, train_calls, caplog):
    from app.services import search

    search.load_dataset_internal()
    weights, _ = intent.artifact_paths()

    search.load_dataset_internal()
    assert search.intent_classifier.loaded_from_artifact is True, \
        "control: the untampered file must load, or the deny below proves nothing"
    assert len(train_calls) == 1

    data = bytearray(open(weights, "rb").read())
    data[len(data) // 2] ^= 0x01
    with open(weights, "wb") as fh:
        fh.write(bytes(data))

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert search.intent_classifier.loaded_from_artifact is False
    assert "sha256" in caplog.text
    assert _read_json(intent.artifact_paths()[1])["model_sha256"] == _sha256_file(weights)


def test_an_artifact_from_other_data_is_never_served(serving, train_calls, caplog):
    from app.services import search

    other = intent.fit(*_corpus(per_class=20, tag="elsewhere"), embeddings.DEFAULT_MODEL)
    assert intent.record_artifact(other) is not None
    train_calls.clear()

    with caplog.at_level(logging.INFO, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 1
    assert search.intent_classifier.loaded_from_artifact is False
    assert "fingerprint" in caplog.text
    assert search.intent_classifier.training_fingerprint == _current_fingerprint()

    search.load_dataset_internal()
    assert search.intent_classifier.loaded_from_artifact is True, \
        "control: the same-fingerprint artifact is loaded"


def test_an_npz_that_needs_pickle_is_never_loaded(serving, train_calls, caplog):
    from app.services import search

    search.load_dataset_internal()
    weights, sidecar = intent.artifact_paths()
    with np.load(weights, allow_pickle=False) as npz:
        coef, intercept, holdout = npz["coef"], npz["intercept"], npz["holdout"]
        classes = npz["classes"].astype(object)
    buf = io.BytesIO()
    np.savez(buf, coef=coef, intercept=intercept, classes=classes, holdout=holdout)
    crafted = buf.getvalue()
    with open(weights, "wb") as fh:
        fh.write(crafted)
    meta = _read_json(sidecar)
    meta["model_sha256"] = hashlib.sha256(crafted).hexdigest()
    _write_json(sidecar, meta)

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert search.intent_classifier.loaded_from_artifact is False
    assert "allow_pickle" in caplog.text, "the refusal must come from the pickle check"

    search.load_dataset_internal()
    assert search.intent_classifier.loaded_from_artifact is True, \
        "control: the plain numeric .npz written by the retrain is loaded"


# ── B8: every broken artifact falls back to training, never raises ──────


def _drop_weights():
    os.remove(intent.artifact_paths()[0])


def _garble_sidecar():
    with open(intent.artifact_paths()[1], "w", encoding="utf-8") as fh:
        fh.write("{not json")


def _future_format():
    path = intent.artifact_paths()[1]
    meta = _read_json(path)
    meta["format_version"] = intent.FORMAT_VERSION + 1
    _write_json(path, meta)


def _other_revision(monkeypatch):
    monkeypatch.setattr(embeddings, "DEFAULT_MODEL_REVISION", "0" * 40)


@pytest.mark.parametrize("breakage,expected_log", [
    (lambda mp: _drop_weights(), "weights"),
    (lambda mp: _garble_sidecar(), "sidecar"),
    (lambda mp: _future_format(), "format_version"),
    (_other_revision, "embedding"),
], ids=["missing-weights", "unreadable-json", "unknown-format", "changed-embedding-revision"])
def test_a_broken_artifact_falls_back_to_training(serving, train_calls, caplog, monkeypatch,
                                                  breakage, expected_log):
    from app.services import search

    search.load_dataset_internal()
    breakage(monkeypatch)

    with caplog.at_level(logging.INFO, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert search.intent_classifier is not None
    assert search.intent_classifier.loaded_from_artifact is False
    assert expected_log in caplog.text


def test_no_artifact_at_all_trains(indexed_corpus, train_calls):
    from app.services import search

    search.load_dataset_internal()
    assert len(train_calls) == 1
    assert search.intent_classifier.loaded_from_artifact is False


# ── B10: an out-of-band reindex neither writes nor deletes ──────────────


def _snapshot(directory):
    """Every file's bytes AND nanosecond mtime, so a rewrite with the same
    content still shows up."""
    out = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        with open(path, "rb") as fh:
            out[name] = (fh.read(), os.stat(path).st_mtime_ns)
    return out


def test_an_out_of_band_reindex_leaves_a_populated_directory_untouched(indexed_corpus):
    from app.services import search

    intent.enable_recording()
    search.load_dataset_internal()
    intent.disable_recording()
    before = _snapshot(indexed_corpus)
    assert {os.path.basename(p) for p in intent.artifact_paths()} <= set(before)

    _execute("UPDATE questions SET question = ? WHERE question = ?",
             ("alpha question changed", "alpha question 0"))
    search.load_dataset_internal()
    assert search.intent_classifier is not None, "the reindex must still train"
    assert _snapshot(indexed_corpus) == before, "a changed corpus must not be written"

    _execute("DELETE FROM questions WHERE dataset_id = ?", (BETA,))
    search.load_dataset_internal()
    assert search.intent_classifier is None
    assert _snapshot(indexed_corpus) == before, "no model must not delete the record"


def test_recording_is_off_unless_the_app_turns_it_on(artifact_dir):
    assert intent.recording_enabled() is False
    intent.enable_recording()
    try:
        assert intent.recording_enabled() is True
    finally:
        intent.disable_recording()
    assert intent.recording_enabled() is False


def test_an_empty_artifact_directory_disables_recording_even_in_the_app(artifact_dir, monkeypatch):
    import app.config as config

    intent.enable_recording()
    try:
        monkeypatch.setattr(config, "INTENT_MODEL_DIR", "   ")
        assert intent.recording_enabled() is False
    finally:
        intent.disable_recording()


def test_the_app_lifespan_turns_recording_on_and_back_off(tmp_path, monkeypatch, artifact_dir):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "lifespan.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from fastapi.testclient import TestClient

    assert intent.recording_enabled() is False
    with TestClient(app):
        assert intent.recording_enabled() is True
    assert intent.recording_enabled() is False


# ── B11: no test touches the real directory ─────────────────────────────


def test_the_suite_never_points_at_the_real_artifact_directory(tmp_path):
    """Guard on the autouse fixture in tests/conftest.py. If that fixture is
    removed, this fails here instead of an unrelated test run overwriting an
    operator's trained weights. Compared against this test's own tmp_path, so
    it also fails when a developer's shell exports INTENT_MODEL_DIR."""
    import app.config as config

    assert config.INTENT_MODEL_DIR == str(tmp_path / "intent-model")


def test_every_test_starts_with_recording_off():
    assert intent._recording is False


# ── B12: a write failure never breaks reindex, never leaves a mismatch ──


@pytest.mark.parametrize("failing_write", [1, 2], ids=["weights-write", "sidecar-write"])
def test_a_failed_write_breaks_nothing_and_leaves_no_mismatched_sidecar(
        serving, monkeypatch, failing_write):
    from app.services import search

    search.load_dataset_internal()
    real_write = intent._atomic_write
    calls = {"n": 0}

    def fail_once(path, payload, mode):
        calls["n"] += 1
        if calls["n"] == failing_write:
            raise OSError("no space left on device")
        real_write(path, payload, mode)

    monkeypatch.setattr(intent, "_atomic_write", fail_once)
    _execute("UPDATE questions SET question = ? WHERE question = ?",
             ("alpha question changed", "alpha question 0"))
    search.load_dataset_internal()

    assert search.intent_classifier is not None, "a write failure must not cost the model"
    assert search.classify_intent_local("alpha question 3")[0]["id"] == ALPHA
    weights, sidecar = intent.artifact_paths()
    if os.path.exists(sidecar):
        assert os.path.exists(weights)
        assert _read_json(sidecar)["model_sha256"] == _sha256_file(weights), \
            "a sidecar describes weights that are not on disk"


def test_an_unwritable_directory_does_not_break_the_reindex(indexed_corpus, tmp_path, monkeypatch):
    import app.config as config
    from app.services import search

    search.load_dataset_internal()
    expected = search.classify_intent_local("alpha question 3")
    assert expected[0] is not None and expected[0]["id"] == ALPHA

    jail = tmp_path / "readonly"
    jail.mkdir()
    monkeypatch.setattr(config, "INTENT_MODEL_DIR", str(jail / "intent-model"))
    os.chmod(jail, 0o500)
    intent.enable_recording()
    try:
        search.load_dataset_internal()
        assert search.intent_classifier is not None
        assert search.classify_intent_local("alpha question 3") == expected
    finally:
        intent.disable_recording()
        os.chmod(jail, 0o700)


def test_a_write_failure_does_not_change_what_the_classifier_answers(recording, fake_embedder,
                                                                     monkeypatch):
    healthy = intent.fit(*_corpus(), MODEL_NAME)
    good_answer = healthy.classify("alpha question 3")

    def boom(*a, **kw):
        raise OSError("nope")

    monkeypatch.setattr(intent, "_atomic_write", boom)
    broken = intent.fit(*_corpus(), MODEL_NAME)
    assert broken is not None, "a write failure must not swallow the training"
    assert intent.record_artifact(broken) is None
    assert broken.classify("alpha question 3") == good_answer


# ── Versioning and history ──────────────────────────────────────────────


def test_an_identical_model_is_not_a_new_version(recording, trained):
    first = intent.record_artifact(trained)
    again = intent.fit(*_corpus(), MODEL_NAME)
    second = intent.record_artifact(again)

    assert second["model_version"] == first["model_version"] == 1
    assert again.model_version == 1
    assert len(_history_lines()) == 1


def test_the_version_keeps_counting_after_the_record_is_removed(recording, trained):
    assert intent.record_artifact(trained)["model_version"] == 1
    intent.record_artifact(None)
    for path in intent.artifact_paths():
        assert not os.path.exists(path)
    other = intent.fit(*_corpus(per_class=20), MODEL_NAME)
    assert intent.record_artifact(other)["model_version"] == 2


def test_the_history_is_capped(recording, monkeypatch):
    monkeypatch.setattr(intent, "HISTORY_MAX_LINES", 3)
    for per_class in range(10, 15):
        intent.record_artifact(intent.fit(*_corpus(per_class=per_class), MODEL_NAME))

    lines = _history_lines()
    assert [line["model_version"] for line in lines] == [3, 4, 5]


# ── B13: the readouts ───────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, artifact_dir):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "intent-artifact.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-token")
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def test_metrics_exposes_accuracy_and_version(client, trained):
    intent.record_artifact(trained)

    res = client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})
    assert res.status_code == 200
    lines = res.text.splitlines()
    assert f"intent_holdout_accuracy {trained.holdout_accuracy}" in lines
    assert "intent_model_version 1.0" in lines


def test_api_ready_carries_accuracy_and_version(client, monkeypatch, trained):
    from app.services import search

    intent.record_artifact(trained)
    monkeypatch.setattr(search, "intent_classifier", trained)

    local = client.get("/api/ready").json()["providers"][0]

    assert local["intent_holdout_accuracy"] == pytest.approx(trained.holdout_accuracy)
    assert local["intent_model_version"] == 1
    assert "accuracy=" in local["detail"]
    assert "version=1" in local["detail"]


def test_api_ready_reports_nulls_when_no_model_is_trained(client, monkeypatch):
    from app.services import search

    monkeypatch.setattr(search, "intent_classifier", None)

    local = client.get("/api/ready").json()["providers"][0]
    assert local["intent_holdout_accuracy"] is None
    assert local["intent_model_version"] is None
    assert "intent=off" in local["detail"]


def test_both_gauges_read_nan_without_a_model(artifact_dir):
    """0.0 would claim a 0% accurate model and a version 0. NaN says "no
    measurement", which an alert rule can tell apart."""
    from app.services import metrics

    intent.record_artifact(None)
    assert np.isnan(metrics.intent_holdout_accuracy._value.get())
    assert np.isnan(metrics.intent_model_version._value.get())


# ── B14: the training CLI ───────────────────────────────────────────────


def _load_cli():
    from app.config import BASE_DIR
    path = os.path.join(BASE_DIR, "scripts", "train", "train_intent_model.py")
    spec = importlib.util.spec_from_file_location("train_intent_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_cli_trains_through_the_same_function_and_prints_the_sidecar(
        indexed_corpus, train_calls, capsys):
    cli = _load_cli()

    assert cli.main([]) == 0
    assert len(train_calls) == 1, "the CLI must train once, not twice"

    out = json.loads(capsys.readouterr().out)
    assert out == _read_json(intent.artifact_paths()[1])
    assert out["model_version"] == 1
    assert out["sample_count"] == 28
    assert out["model_sha256"] == _sha256_file(intent.artifact_paths()[0])
    assert train_calls, "the CLI must train through intent.train"
    assert intent.recording_enabled() is False, "the CLI must not leave recording on"


def test_the_cli_run_twice_on_the_same_data_keeps_the_version(indexed_corpus, train_calls,
                                                              capsys):
    cli = _load_cli()
    assert cli.main([]) == 0
    capsys.readouterr()
    train_calls.clear()

    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["model_version"] == 1
    assert train_calls == [], "the stored model is provably current: loaded, not retrained"


def test_the_cli_uses_no_private_search_helper():
    from app.config import BASE_DIR
    path = os.path.join(BASE_DIR, "scripts", "train", "train_intent_model.py")
    source = open(path, encoding="utf-8").read()
    assert "search._" not in source and "intent._" not in source


def test_the_cli_fails_cleanly_below_the_floor(indexed_corpus, capsys):
    _execute("DELETE FROM questions WHERE dataset_id = ?", (BETA,))
    cli = _load_cli()

    assert cli.main([]) == 1
    assert capsys.readouterr().out == ""
    for path in intent.artifact_paths():
        assert not os.path.exists(path)


# ── Below the floor, and per-install data ───────────────────────────────


def test_below_the_floor_no_model_and_no_artifact_claims_one(recording):
    vectors, texts, ids = _corpus(per_class=4)
    assert intent.fit(vectors, texts, ids, MODEL_NAME) is None
    assert intent.record_artifact(None) is None
    for path in intent.artifact_paths():
        assert not os.path.exists(path)


def test_a_reindex_below_the_floor_removes_the_earlier_record(serving):
    from app.services import search

    search.load_dataset_internal()
    assert os.path.exists(intent.artifact_paths()[0])

    _execute("DELETE FROM questions WHERE dataset_id = ?", (BETA,))
    search.load_dataset_internal()

    assert search.intent_classifier is None
    for path in intent.artifact_paths():
        assert not os.path.exists(path)
    assert len(_history_lines()) == 1, "the history is kept"


def test_removing_the_record_is_logged(recording, trained, caplog):
    intent.record_artifact(trained)

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        intent.record_artifact(None)

    assert "removed the model record" in caplog.text
    assert intent.WEIGHTS_FILENAME in caplog.text


def test_nothing_is_logged_when_there_was_no_record_to_remove(recording, caplog):
    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        intent.record_artifact(None)

    assert "removed the model record" not in caplog.text


def test_every_artifact_file_is_gitignored_wherever_it_lands():
    """A customer's trained weights are their data. INTENT_MODEL_DIR can move
    the directory into the repository, so the file NAMES are ignored too."""
    from app.config import BASE_DIR

    names = [intent.WEIGHTS_FILENAME, intent.METADATA_FILENAME,
             intent.HISTORY_FILENAME, intent.LOCK_FILENAME,
             intent.TEMP_FILE_PREFIX + "ab12cd"]
    for directory in (os.path.join("data", "intent-model"), os.path.join("app", "services")):
        for name in names:
            path = os.path.join(directory, name)
            result = subprocess.run(["git", "check-ignore", path],
                                    cwd=BASE_DIR, capture_output=True, text=True)
            assert result.returncode == 0, f"{path} is not gitignored"


# ── Turn 2, finding 1: every sidecar number the load uses is checked ────
#
# The trust gate in search.classify_intent_local reads holdout_accuracy. For a
# loaded model that number came from the sidecar, a 0644 file meant for
# people, and neither the sha256 nor the fingerprint covered it: "NaN",
# "0.95", 0.99 or true all loaded and opened the gate. The accuracy, holdout
# size and sample count now also live inside the sha-covered .npz, and the
# sidecar must agree with it and pass a type and range check.


def _edit_sidecar(**changes):
    path = intent.artifact_paths()[1]
    meta = _read_json(path)
    meta.update(changes)
    _write_json(path, meta)


@pytest.mark.parametrize("bad", [
    float("nan"), "0.95", True, 1.5, -0.1, 0.99,
], ids=["nan", "string", "bool", "above-one", "negative", "in-range-but-not-measured"])
def test_a_sidecar_accuracy_that_is_not_the_measured_one_is_refused(serving, train_calls,
                                                                    caplog, bad):
    from app.services import search

    search.load_dataset_internal()
    measured = search.intent_classifier.holdout_accuracy
    assert measured is not None and measured != 0.99
    _edit_sidecar(holdout_accuracy=bad)

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert search.intent_classifier.loaded_from_artifact is False
    assert search.intent_classifier.holdout_accuracy == measured
    assert "holdout_accuracy" in caplog.text

    search.load_dataset_internal()
    assert search.intent_classifier.loaded_from_artifact is True, \
        "control: the rewritten sidecar with the measured value loads"
    assert search.intent_classifier.holdout_accuracy == measured
    assert len(train_calls) == 2


@pytest.mark.parametrize("field,bad", [
    ("sample_count", "28"),
    ("sample_count", True),
    ("sample_count", 1),
    ("sample_count", 29),
    ("holdout_size", 3.5),
    ("holdout_size", -1),
    ("holdout_size", 999),
], ids=["count-string", "count-bool", "count-below-classes", "count-not-measured",
        "size-fraction", "size-negative", "size-not-measured"])
def test_a_sidecar_count_that_is_not_the_measured_one_is_refused(serving, train_calls,
                                                                 caplog, field, bad):
    from app.services import search

    search.load_dataset_internal()
    _edit_sidecar(**{field: bad})

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert search.intent_classifier.loaded_from_artifact is False
    assert field in caplog.text

    search.load_dataset_internal()
    assert search.intent_classifier.loaded_from_artifact is True


def test_a_loaded_model_gates_exactly_like_the_trained_one(serving, monkeypatch):
    """The gate's input for a loaded model is the MEASURED accuracy, read from
    the sha-covered weights file."""
    from app.services import search

    search.load_dataset_internal()
    trained = search.intent_classifier
    weights, _ = intent.artifact_paths()
    with np.load(weights, allow_pickle=False) as npz:
        stored = npz["holdout"]
    assert stored[0] == trained.holdout_accuracy
    assert stored[1] == trained.holdout_size
    assert stored[2] == trained.sample_count

    search.load_dataset_internal()
    loaded = search.intent_classifier
    assert loaded.loaded_from_artifact is True
    assert loaded.holdout_accuracy == trained.holdout_accuracy


# ── Turn 2, finding 2: a passing fault never destroys a good artifact ───


def test_a_boot_without_embeddings_keeps_the_artifact_and_its_version(serving, train_calls,
                                                                      monkeypatch):
    from app.services import search

    search.load_dataset_internal()
    kept = _snapshot(serving)

    monkeypatch.setattr(embeddings, "available", lambda: False)
    search.load_dataset_internal()
    assert search.intent_classifier is None
    assert _snapshot(serving) == kept, "a passing embedding outage deleted the model"

    monkeypatch.setattr(embeddings, "available", lambda: True)
    search.load_dataset_internal()
    assert search.intent_classifier.loaded_from_artifact is True
    assert search.intent_classifier.model_version == 1
    assert len(train_calls) == 1


def test_a_failed_questions_read_keeps_the_artifact(serving):
    from app.services import search

    search.load_dataset_internal()
    kept = _snapshot(serving)

    _execute("ALTER TABLE questions RENAME TO questions_away")
    search.load_dataset_internal()

    assert search.intent_classifier is None
    assert _snapshot(serving) == kept, "a failed read is not an empty knowledge base"


def test_a_knowledge_base_with_no_questions_left_removes_the_artifact(serving):
    from app.services import search

    search.load_dataset_internal()
    _execute("DELETE FROM questions")
    search.load_dataset_internal()

    assert search.intent_classifier is None
    for path in intent.artifact_paths():
        assert not os.path.exists(path)


def test_below_the_floor_removes_the_artifact_even_without_embeddings(serving, monkeypatch):
    """Below the floor no model can exist for this data, whatever the
    embedder's state. That is a fact about the data, not a passing fault."""
    from app.services import search

    search.load_dataset_internal()
    _execute("DELETE FROM questions WHERE dataset_id = ?", (BETA,))
    monkeypatch.setattr(embeddings, "available", lambda: False)
    search.load_dataset_internal()

    for path in intent.artifact_paths():
        assert not os.path.exists(path)


# ── Turn 2, finding 3: an empty INTENT_MODEL_DIR means no artifact at all ─


def test_an_empty_artifact_directory_loads_nothing_from_the_working_directory(
        serving, train_calls, tmp_path, monkeypatch):
    import shutil
    import app.config as config
    from app.services import search

    search.load_dataset_internal()
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    for path in intent.artifact_paths():
        shutil.copy(path, elsewhere)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(config, "INTENT_MODEL_DIR", "")

    search.load_dataset_internal()

    assert len(train_calls) == 2
    assert search.intent_classifier.loaded_from_artifact is False


# ── Turn 2, finding 7: a library upgrade retrains once ──────────────────


def test_the_fingerprint_covers_the_scikit_learn_and_numpy_versions(monkeypatch):
    _, texts, ids = _corpus()
    base = intent.training_fingerprint(texts, ids, MODEL_NAME)

    monkeypatch.setattr(intent, "_sklearn_version", lambda: "0.0.1")
    assert intent.training_fingerprint(texts, ids, MODEL_NAME) != base
    monkeypatch.undo()

    monkeypatch.setattr(np, "__version__", "0.0.1")
    assert intent.training_fingerprint(texts, ids, MODEL_NAME) != base


def test_a_scikit_learn_upgrade_retrains_once(serving, train_calls, monkeypatch):
    from app.services import search

    search.load_dataset_internal()
    monkeypatch.setattr(intent, "_sklearn_version", lambda: "99.0.0")

    search.load_dataset_internal()
    assert len(train_calls) == 2
    assert _read_json(intent.artifact_paths()[1])["scikit_learn_version"] == "99.0.0"

    search.load_dataset_internal()
    assert len(train_calls) == 2, "after the one retrain, the upgraded model loads"


# ── Turn 3, finding 1: a script that boots the app must redirect the dir ─
#
# The lifespan turns recording on for EVERY app boot, and it cannot tell a
# real server from a TestClient harness. scripts/run_eval.py --conversations
# boots the app over a throwaway database with zero questions, so before the
# redirect it DELETED the install's model record. Same spirit as
# tests/test_csrf.py: a static check that fails the build when a new script
# forgets.


def _scripts_that_boot_the_app():
    from app.config import BASE_DIR
    found = []
    for root, _, files in os.walk(os.path.join(BASE_DIR, "scripts")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                source = open(path, encoding="utf-8").read()
                if "TestClient(app" in source:
                    found.append((path, source))
    return found


def test_every_script_that_boots_the_app_redirects_the_artifact_directory():
    scripts = _scripts_that_boot_the_app()
    assert any(p.endswith(os.path.join("scripts", "run_eval.py")) for p, _ in scripts), \
        "the guard must actually see run_eval.py, or it proves nothing"
    for path, source in scripts:
        redirect = source.find('os.environ["INTENT_MODEL_DIR"]')
        boot_import = source.find("from app.main import app")
        assert redirect != -1, f"{path} boots the app without redirecting INTENT_MODEL_DIR"
        assert boot_import == -1 or redirect < boot_import, \
            f"{path} redirects INTENT_MODEL_DIR only after importing the app"
        # One redirect is not enough when a script has several entry points
        # (run_eval.py: --conversations and the benchmark). Every place that
        # moves the database to a throwaway one must move the model too.
        assert source.count('os.environ["INTENT_MODEL_DIR"]') >= \
            source.count('os.environ["DB_PATH"]'), \
            f"{path} moves DB_PATH somewhere without also moving INTENT_MODEL_DIR"


# ── Turn 3, finding 4: only the owner can write the directory ───────────


def test_the_artifact_directory_is_created_owner_writable_only(recording, trained):
    old_umask = os.umask(0o002)
    try:
        assert intent.record_artifact(trained) is not None
    finally:
        os.umask(old_umask)
    mode = os.stat(recording).st_mode & 0o777
    assert mode & 0o022 == 0, f"group or others can write the model directory: {oct(mode)}"


# ── Turn 3, finding 7: a failed training setup says so ──────────────────


def test_a_failed_fingerprint_is_logged_as_a_training_failure(serving, monkeypatch, caplog):
    from app.services import search

    def missing(*a, **kw):
        raise ImportError("No module named 'sklearn'")

    monkeypatch.setattr(intent, "_sklearn_version", missing)
    with caplog.at_level(logging.INFO, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert search.intent_classifier is None
    assert "[intent] training failed" in caplog.text
    assert "Embedding index build failed" not in caplog.text
    assert search.questions_embedding_index is not None


# ── Turn 3, findings 8 and 9: removal is locked, the lock cannot hang ───


class _HeldLock:
    """Another writer holding the artifact lock (a second open file, so
    flock treats it as a different owner even inside this process)."""

    def __init__(self, directory):
        import fcntl
        os.makedirs(directory, exist_ok=True)
        self._fh = open(os.path.join(directory, intent.LOCK_FILENAME), "a")
        fcntl.flock(self._fh, fcntl.LOCK_EX)

    def release(self):
        import fcntl
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def test_a_held_lock_never_hangs_a_reindex(serving, monkeypatch, caplog):
    from app.services import search

    search.load_dataset_internal()
    monkeypatch.setattr(intent, "LOCK_TIMEOUT_SECONDS", 0.2)
    held = _HeldLock(serving)
    try:
        _execute("UPDATE questions SET question = ? WHERE question = ?",
                 ("alpha question changed", "alpha question 0"))
        with caplog.at_level(logging.ERROR, logger="PadyarAssistant"):
            search.load_dataset_internal()
    finally:
        held.release()

    assert search.intent_classifier is not None, "the reindex must still serve a model"
    assert "lock" in caplog.text


def test_removing_the_record_waits_for_the_writer_lock(recording, trained, monkeypatch):
    intent.record_artifact(trained)
    monkeypatch.setattr(intent, "LOCK_TIMEOUT_SECONDS", 0.2)
    held = _HeldLock(recording)
    try:
        intent.record_artifact(None)
        for path in intent.artifact_paths():
            assert os.path.exists(path), "removed the record while another writer held the lock"
    finally:
        held.release()
    intent.record_artifact(None)
    for path in intent.artifact_paths():
        assert not os.path.exists(path)


# ── Turn 3, finding 10: a file that is not a plain file is refused ──────


def test_a_weights_path_that_is_not_a_regular_file_is_refused(serving, train_calls, caplog):
    from app.services import search

    search.load_dataset_internal()
    weights, _ = intent.artifact_paths()
    os.remove(weights)
    os.mkfifo(weights)

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert "not a regular file" in caplog.text


def test_an_oversized_artifact_file_is_refused(serving, train_calls, monkeypatch, caplog):
    from app.services import search

    search.load_dataset_internal()
    monkeypatch.setattr(intent, "MAX_FILE_BYTES", 64)

    with caplog.at_level(logging.WARNING, logger="PadyarAssistant"):
        search.load_dataset_internal()

    assert len(train_calls) == 2
    assert "larger than" in caplog.text


# ── Turn 3, finding 6: the model card's measurement runs from the repo ──


def _write_corpus(path):
    corpus = {
        "synonyms": [],
        "entries": [
            {"id": ALPHA, "title": "alpha topic", "text": "alpha answer",
             "title_en": "", "text_en": "",
             "questions": [f"alpha question {i}" for i in range(14)]},
            {"id": BETA, "title": "beta topic", "text": "beta answer",
             "title_en": "", "text_en": "",
             "questions": [f"beta question {i}" for i in range(14)]},
        ],
    }
    path.write_text(json.dumps(corpus), encoding="utf-8")


def test_a_corpus_file_is_loaded_into_the_database_the_cli_trains_from(
        indexed_corpus, tmp_path, capsys):
    import app.db.connection as dbc

    _execute("DELETE FROM questions")
    _execute("DELETE FROM dataset")
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    cli = _load_cli()

    cli.load_corpus(str(corpus))
    conn = dbc.get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 28
    conn.close()

    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["sample_count"] == 28


def test_the_corpus_option_refuses_to_run_inside_an_imported_app(tmp_path):
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus)
    cli = _load_cli()

    with pytest.raises(SystemExit):
        cli.main(["--corpus", str(corpus)])
