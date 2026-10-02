"""GET /admin/api/ai/own-model: the read-only view of this install's own model.

The trained intent classifier is a versioned artifact on disk (see
docs/features/intent-model/SPEC.md). Staff, and an evaluator sitting next to
them, must be able to see in the admin panel that this install has its own
trained model, which version, when it was trained and how well it measured.
The card on Admin -> AI -> Models reads this endpoint.

What these tests pin:
  * the endpoint sits behind the admin session like the rest of its router,
  * the card describes the model this process SERVES. It says "trained" only
    when the sidecar agrees with the serving classifier on every shown
    number, and the weights file hashes to the serving model's sha256. A
    sidecar edited to plausible values is caught, not shown,
  * at most ten history rows, newest first. A row that cannot be validated
    (an unknown format, a torn line) is skipped, and the history alone never
    hides the model,
  * with no model and no files it answers 200 with an empty state,
  * a corrupt, tampered or mismatched record answers 200 with an
    "unreadable" state, never a 500 and never exception text,
  * the response carries no filesystem path and no directory name,
  * reading never writes, deletes or retrains, and never parses the weights.

Artifacts are recorded with the real intent.fit() and intent.record_artifact()
over a small separable corpus and a fake embedder, so nothing is downloaded.
They are recorded INSIDE the TestClient block: the app lifespan reindexes an
empty test database on boot, and a corpus below the training floor removes
any record that is already there. `_record` then installs the classifier as
`search.intent_classifier`, which is what a serving reindex does; an autouse
fixture puts the previous value back after every test.
"""
import datetime
import json
import os
import secrets
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.services import intent, search

ROUTE = "/admin/api/ai/own-model"
MODEL_NAME = "test-embedding-model"


def _vector(text: str) -> np.ndarray:
    v = np.zeros(8, dtype=np.float32)
    v[0] = 1.0 if "alpha" in text else 0.0
    v[1] = 1.0 if "beta" in text else 0.0
    v[2] = (zlib.crc32(text.encode()) % 1000) / 5000.0
    v[3] = 0.25
    return v


def _corpus(alpha: int = 14, beta: int = 14, tag: str = "question"):
    texts = [f"alpha {tag} {i}" for i in range(alpha)]
    texts += [f"beta {tag} {i}" for i in range(beta)]
    ids = ["faq-alpha"] * alpha + ["faq-beta"] * beta
    vectors = np.vstack([_vector(t) for t in texts])
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors, texts, ids


def _record(tag: str = "question", alpha: int = 14, beta: int = 14,
            serve: bool = True) -> dict:
    """Train and record one model version the way a serving reindex does,
    and (unless `serve` is False) make it the model this process serves."""
    classifier = intent.fit(*_corpus(alpha, beta, tag), MODEL_NAME)
    assert classifier is not None
    intent.enable_recording()
    meta = intent.record_artifact(classifier)
    assert meta is not None, "recording must be on for these tests"
    if serve:
        search.intent_classifier = classifier
    return meta


def _dir() -> str:
    import app.config as config
    return config.INTENT_MODEL_DIR


def _sidecar_path() -> str:
    return intent.artifact_paths()[1]


def _read_sidecar() -> dict:
    with open(_sidecar_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _write_sidecar(data) -> None:
    with open(_sidecar_path(), "w", encoding="utf-8") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


def _snapshot(directory):
    out = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isdir(path):
            out[name] = ("<dir>", os.stat(path).st_mtime_ns)
            continue
        with open(path, "rb") as fh:
            out[name] = (fh.read(), os.stat(path).st_mtime_ns)
    return out


@pytest.fixture(autouse=True)
def _serving_model_is_restored(monkeypatch):
    monkeypatch.setattr(search, "intent_classifier", None)


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "own-model.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield


@pytest.fixture
def anon(app_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(app_db):
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash,"
                     " salt, security_question, security_answer_hash)"
                     " VALUES ('panel','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "panel",
                      (datetime.datetime.now()
                       + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c




# ── C1: the admin door ──────────────────────────────────────────────────


def test_an_anonymous_caller_is_refused_like_the_rest_of_the_router(anon):
    _record()
    res = anon.get(ROUTE)
    sibling = anon.get("/admin/api/ai/providers")
    assert res.status_code in (401, 403)
    assert res.status_code == sibling.status_code
    assert "model_version" not in res.text


def test_an_expired_session_is_refused(client):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("UPDATE admin_sessions SET expiry = ?",
                 ((datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat(),))
    conn.commit()
    conn.close()
    _record()
    assert client.get(ROUTE).status_code == 401


# ── C2: the serving model, confirmed by its record ──────────────────────

MODEL_FIELDS = {
    "model_version", "trained_at", "holdout_accuracy", "holdout_size",
    "sample_count", "class_count", "embedding_model_name",
    "embedding_model_revision", "model_sha256", "training_fingerprint",
    "scikit_learn_version", "numpy_version"}


def test_the_serving_model_is_described_when_its_record_matches(client):
    meta = _record()
    serving = search.intent_classifier
    res = client.get(ROUTE)
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "trained"
    model = body["model"]
    for field in MODEL_FIELDS:
        assert model[field] == meta[field], field
    assert model["model_version"] == serving.model_version == 1
    assert model["model_sha256"] == serving.model_sha256
    assert model["holdout_accuracy"] == serving.holdout_accuracy
    assert model["sample_count"] == serving.sample_count == 28
    assert model["class_count"] == len(serving.labels) == 2
    assert model["holdout_size"] == serving.holdout_size > 0
    assert body["history"] == [{
        "model_version": 1,
        "trained_at": meta["trained_at"],
        "holdout_accuracy": meta["holdout_accuracy"],
        "holdout_size": meta["holdout_size"],
        "sample_count": meta["sample_count"],
        "class_count": meta["class_count"],
    }]
    assert body["history_status"] == "ok"


def test_a_model_loaded_from_the_files_is_confirmed_too(client):
    """The boot path: the stored model is loaded, not retrained."""
    meta = _record(serve=False)
    vectors, texts, ids = _corpus()
    loaded = intent.load_or_train(vectors, texts, ids, MODEL_NAME)
    assert loaded.loaded_from_artifact
    search.intent_classifier = loaded
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["model"]["model_version"] == meta["model_version"]
    assert body["model"]["model_sha256"] == meta["model_sha256"]


def test_only_the_listed_fields_leave_the_server(client):
    _record()
    body = client.get(ROUTE).json()
    assert set(body) == {"state", "model", "history", "history_status", "needs"}
    assert set(body["model"]) == MODEL_FIELDS
    assert "model_file" not in json.dumps(body)
    assert "hyperparameters" not in json.dumps(body)


def test_the_history_is_the_latest_ten_versions_newest_first(client):
    for i in range(12):
        _record(tag=f"round{i}")
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["model"]["model_version"] == 12
    assert [row["model_version"] for row in body["history"]] == list(range(12, 2, -1))


def test_an_unmeasured_accuracy_is_null_never_zero(client):
    # One beta question: the stratified holdout cannot run, so nothing was measured.
    _record(alpha=20, beta=1)
    res = client.get(ROUTE)
    body = res.json()
    assert body["state"] == "trained"
    assert body["model"]["holdout_accuracy"] is None
    assert body["model"]["holdout_size"] == 0
    assert body["history"][0]["holdout_accuracy"] is None
    assert "NaN" not in res.text


def test_the_weights_are_never_parsed_for_the_response(client, monkeypatch):
    _record()

    def refuse(*a, **kw):
        raise AssertionError("the read view must not parse the weights")

    monkeypatch.setattr(np, "load", refuse)
    monkeypatch.setattr(intent, "_deserialize", refuse)
    monkeypatch.setattr(intent, "_load_checked", refuse)
    assert client.get(ROUTE).json()["state"] == "trained"


# ── C3: nothing trained ─────────────────────────────────────────────────


def test_no_model_and_no_files_is_a_clear_empty_state(client):
    res = client.get(ROUTE)
    assert res.status_code == 200
    assert res.json() == {
        "state": "none",
        "model": None,
        "history": [],
        "history_status": "ok",
        "needs": {"questions": intent.HYPERPARAMETERS["min_samples"],
                  "topics": intent.HYPERPARAMETERS["min_classes"]},
    }


def test_an_empty_model_directory_setting_with_no_model_is_the_empty_state(client, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "INTENT_MODEL_DIR", "")
    body = client.get(ROUTE).json()
    assert body["state"] == "none"
    assert body["model"] is None


def test_a_removed_model_keeps_its_history_under_the_empty_state(client):
    _record()
    intent.enable_recording()
    intent.record_artifact(None, no_model_for_this_data=True)
    search.intent_classifier = None
    assert not os.path.exists(_sidecar_path())
    body = client.get(ROUTE).json()
    assert body["state"] == "none"
    assert body["model"] is None
    assert [row["model_version"] for row in body["history"]] == [1]


# ── C4: a record that does not match the serving model ──────────────────


def _garble_sidecar():
    _write_sidecar("{not json")


def _sidecar_is_a_list():
    _write_sidecar([1, 2, 3])


def _set(field, value):
    def apply():
        meta = _read_sidecar()
        meta[field] = value(meta) if callable(value) else value
        _write_sidecar(meta)
    return apply


def _drop(field):
    def apply():
        meta = _read_sidecar()
        del meta[field]
        _write_sidecar(meta)
    return apply


def _plausible_other_accuracy(meta):
    """Another fraction of the SAME holdout, so train() could have produced it."""
    size = meta["holdout_size"]
    correct = round(meta["holdout_accuracy"] * size)
    return (correct - 1 if correct > 0 else correct + 1) / size


def _nan_accuracy():
    meta = _read_sidecar()
    meta["holdout_accuracy"] = float("nan")
    _write_sidecar(json.dumps(meta))


def _sidecar_is_a_directory():
    os.remove(_sidecar_path())
    os.mkdir(_sidecar_path())


def _weights_missing():
    os.remove(intent.artifact_paths()[0])


def _weights_edited():
    with open(intent.artifact_paths()[0], "wb") as fh:
        fh.write(b"not an npz at all")


def _weights_one_byte_flipped():
    path = intent.artifact_paths()[0]
    with open(path, "rb") as fh:
        payload = bytearray(fh.read())
    payload[len(payload) // 2] ^= 0xFF
    with open(path, "wb") as fh:
        fh.write(bytes(payload))


def _both_files_from_another_model():
    """A forger replaces the pair with another genuine, self-consistent pair."""
    _record(tag="other", serve=False)


TAMPERS = {
    # Plausible: every one of these is a value train() could have written.
    "plausible accuracy edit": _set("holdout_accuracy", _plausible_other_accuracy),
    "plausible sample count edit": _set("sample_count", 5000),
    "plausible holdout size edit": _set("holdout_size", lambda m: m["holdout_size"] + 1),
    "plausible class count edit": _set("class_count", 3),
    "plausible version edit": _set("model_version", 999),
    "plausible sha edit": _set("model_sha256", "0" * 64),
    "plausible fingerprint edit": _set("training_fingerprint", "0" * 64),
    "embedding name edit": _set("embedding_model_name", "another-model"),
    "embedding revision edit": _set("embedding_model_revision", "f" * 40),
    "library version edit": _set("scikit_learn_version", "0.1.0"),
    "weights edited": _weights_edited,
    "weights one byte flipped": _weights_one_byte_flipped,
    "both files from another model": _both_files_from_another_model,
    # Implausible or corrupt.
    "garbled sidecar": _garble_sidecar,
    "sidecar is a JSON list": _sidecar_is_a_list,
    "version is a string": _set("model_version", "1"),
    "version is a bool": _set("model_version", True),
    "accuracy is a string": _set("holdout_accuracy", "0.9"),
    "accuracy is NaN": _nan_accuracy,
    "sample count missing": _drop("sample_count"),
    "trained_at not a date": _set("trained_at", "yesterday"),
    "trained_at missing": _drop("trained_at"),
    "unknown format": _set("format_version", 99),
    "sidecar is a directory": _sidecar_is_a_directory,
    "weights file missing": _weights_missing,
}


@pytest.mark.parametrize("tamper", list(TAMPERS.values()), ids=list(TAMPERS))
def test_a_record_that_does_not_match_the_serving_model_is_never_trained(client, tamper):
    meta = _record()
    tamper()
    res = client.get(ROUTE)
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "unreadable"
    assert body["model"] is None
    text = res.text
    for leak in ("Traceback", "Error", "Exception", "Expecting", "codec",
                 meta["model_sha256"], "yesterday", "another-model"):
        assert leak not in text, leak


def test_an_intact_record_is_trained_the_allow_control_for_the_tampers(client):
    _record()
    assert client.get(ROUTE).json()["state"] == "trained"


def test_a_record_with_no_serving_model_is_not_trained(client):
    _record(serve=False)
    body = client.get(ROUTE).json()
    assert body["state"] == "unreadable"
    assert body["model"] is None


def test_a_serving_model_newer_than_its_record_is_not_trained(client):
    """Recording failed (a lock timeout, a full disk): the files still hold
    version 1, the process serves a model that was never recorded."""
    _record()
    search.intent_classifier = intent.fit(*_corpus(tag="unrecorded"), MODEL_NAME)
    assert client.get(ROUTE).json()["state"] == "unreadable"


def test_a_serving_model_with_a_directory_but_no_files_is_not_trained(client):
    """The directory is set, so a record was expected; none was written."""
    search.intent_classifier = intent.fit(*_corpus(), MODEL_NAME)
    body = client.get(ROUTE).json()
    assert body["state"] == "unreadable"
    assert body["model"] is None


# ── An install that keeps no record (INTENT_MODEL_DIR empty) ────────────


def test_an_install_that_keeps_no_record_shows_the_model_from_memory(client, monkeypatch):
    """INTENT_MODEL_DIR= is a documented setting: the model is trained in
    memory at every reindex and never written. The model is real, so its
    measured facts are shown; there is no version, sha256 or date to show,
    and no warning, because nothing failed."""
    import app.config as config
    monkeypatch.setattr(config, "INTENT_MODEL_DIR", "")
    serving = intent.fit(*_corpus(), MODEL_NAME)
    search.intent_classifier = serving
    res = client.get(ROUTE)
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "not_saved"
    model = body["model"]
    assert set(model) == MODEL_FIELDS
    assert model["model_version"] is None
    assert model["model_sha256"] is None
    assert model["trained_at"] is None
    assert model["holdout_accuracy"] == serving.holdout_accuracy
    assert model["holdout_size"] == serving.holdout_size
    assert model["sample_count"] == serving.sample_count == 28
    assert model["class_count"] == 2
    assert model["training_fingerprint"] == serving.training_fingerprint
    assert model["embedding_model_name"] == MODEL_NAME
    assert body["history"] == []
    assert body["history_status"] == "ok"


def test_an_install_that_keeps_no_record_reads_no_file(client, monkeypatch, tmp_path):
    """An empty setting must not make the reader fall back to relative paths
    in whatever directory the app started in."""
    import app.config as config
    _record()
    monkeypatch.chdir(_dir())
    monkeypatch.setattr(config, "INTENT_MODEL_DIR", "")
    body = client.get(ROUTE).json()
    assert body["state"] == "not_saved"
    assert body["history"] == []


def test_an_unexpected_failure_inside_the_reader_is_unreadable_not_a_500(client, monkeypatch):
    _record()

    calls = []

    def explode(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("secret internal detail /var/lib/padyar")

    monkeypatch.setattr(intent, "_read_regular_file", explode)
    res = client.get(ROUTE)
    assert calls, "the stub never ran, so this test would prove nothing"
    assert res.status_code == 200
    assert res.json()["state"] == "unreadable"
    assert "secret internal detail" not in res.text
    assert "/var/lib" not in res.text


# ── History: rows stand alone, and never hide the model ─────────────────


def _history_rows():
    with open(intent.history_path(), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_history_lines(lines):
    with open(intent.history_path(), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def test_rows_from_an_older_known_format_are_still_shown(client, monkeypatch):
    """After a future format bump the file still holds the old rows. They are
    a known format, so they stay in the table; only the current record is
    judged against the current format."""
    _record()
    _record(tag="second")
    monkeypatch.setattr(intent, "FORMAT_VERSION", intent.FORMAT_VERSION + 1)
    body = client.get(ROUTE).json()
    assert [row["model_version"] for row in body["history"]] == [2, 1]
    assert body["history_status"] == "ok"


def test_a_row_in_an_unknown_format_is_skipped_and_the_model_still_shown(client):
    _record()
    _record(tag="second")
    rows = _history_rows()
    rows[0]["format_version"] = 0
    _write_history_lines([json.dumps(r) for r in rows])
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["model"]["model_version"] == 2
    assert [row["model_version"] for row in body["history"]] == [2]
    assert body["history_status"] == "partial"


HISTORY_DAMAGE = {
    "torn line": lambda rows: [json.dumps(r) for r in rows] + ["{half a line"],
    "version is a string": lambda rows: [json.dumps({**rows[0], "model_version": "one"})]
                                        + [json.dumps(r) for r in rows[1:]],
    "accuracy is not a fraction": lambda rows: [json.dumps({**rows[0], "holdout_accuracy": 0.123})]
                                               + [json.dumps(r) for r in rows[1:]],
    "not an object": lambda rows: ["[1, 2]"] + [json.dumps(r) for r in rows],
}


@pytest.mark.parametrize("damage", list(HISTORY_DAMAGE.values()), ids=list(HISTORY_DAMAGE))
def test_a_bad_history_row_is_skipped_and_the_model_facts_stay(client, damage):
    _record()
    _record(tag="second")
    _write_history_lines(damage(_history_rows()))
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["model"]["model_version"] == 2
    assert body["history_status"] == "partial"
    assert 2 in [row["model_version"] for row in body["history"]]
    for row in body["history"]:
        assert isinstance(row["model_version"], int)


def test_an_unreadable_history_file_hides_only_the_table(client):
    _record()
    with open(intent.history_path(), "ab") as fh:
        fh.write(b"\xff\xfe\xfa\n")
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["model"]["model_version"] == 1
    assert body["history"] == []
    assert body["history_status"] == "unreadable"


def test_a_history_that_is_a_directory_hides_only_the_table(client):
    _record()
    os.remove(intent.history_path())
    os.mkdir(intent.history_path())
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["history_status"] == "unreadable"


# ── History rows are bounded by the confirmed model ─────────────────────


def _append_history_row(row):
    with open(intent.history_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_a_history_row_newer_than_the_serving_model_is_skipped(client):
    _record()
    row = _history_rows()[0]
    _append_history_row({**row, "model_version": 7, "holdout_accuracy": 1.0})
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert [r["model_version"] for r in body["history"]] == [1]
    assert body["history_status"] == "partial"


@pytest.mark.parametrize("field,change", [
    ("holdout_accuracy", _plausible_other_accuracy),
    ("sample_count", lambda row: row["sample_count"] + 100),
    ("holdout_size", lambda row: row["holdout_size"] + 1),
    ("class_count", lambda row: row["class_count"] + 1),
    ("trained_at", lambda row: "2019-01-01T00:00:00+00:00"),
])
def test_a_history_row_for_the_serving_version_must_agree_with_it(client, field, change):
    _record()
    _record(tag="second")
    rows = _history_rows()
    rows[1][field] = change(rows[1])
    _write_history_lines([json.dumps(r) for r in rows])
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["model"]["model_version"] == 2
    assert [r["model_version"] for r in body["history"]] == [1]
    assert body["history_status"] == "partial"


def test_history_rows_are_not_bounded_when_nothing_is_confirmed(client):
    """With no confirmed model there is nothing to bound the rows by, so the
    sanity-checked rows are shown as before."""
    _record()
    _record(tag="second", serve=False)
    search.intent_classifier = None
    _garble_sidecar()
    body = client.get(ROUTE).json()
    assert body["state"] == "unreadable"
    assert [r["model_version"] for r in body["history"]] == [2, 1]
    assert body["history_status"] == "ok"


# ── The history read has its own small cap ──────────────────────────────


def test_the_history_cap_fits_the_line_cap_with_room_to_spare():
    _row_bytes = 1024
    assert intent.HISTORY_MAX_BYTES >= intent.HISTORY_MAX_LINES * _row_bytes * 2
    assert intent.HISTORY_MAX_BYTES < intent.MAX_FILE_BYTES


def test_a_history_larger_than_its_cap_hides_only_the_table(client):
    _record()
    line = open(intent.history_path(), "rb").read()
    copies = intent.HISTORY_MAX_BYTES // len(line) + 1
    with open(intent.history_path(), "wb") as fh:
        fh.write(line * copies)
    assert os.path.getsize(intent.history_path()) > intent.HISTORY_MAX_BYTES
    body = client.get(ROUTE).json()
    assert body["state"] == "trained"
    assert body["history"] == []
    assert body["history_status"] == "unreadable"


def test_a_history_just_under_its_cap_still_shows_the_newest_rows(client):
    for i in range(3):
        _record(tag=f"round{i}")
    rows = _history_rows()
    older = json.dumps(rows[0]).encode() + b"\n"
    newest = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
    room = intent.HISTORY_MAX_BYTES - len(newest)
    with open(intent.history_path(), "wb") as fh:
        fh.write(older * (room // len(older)) + newest)
    assert os.path.getsize(intent.history_path()) <= intent.HISTORY_MAX_BYTES
    body = client.get(ROUTE).json()
    assert body["history_status"] == "ok"
    assert [r["model_version"] for r in body["history"]][:3] == [3, 2, 1]
    assert len(body["history"]) == intent.HISTORY_SHOWN


# ── Multi-worker: the endpoint polls the index version like a query ─────


def test_the_endpoint_polls_the_index_version_before_it_reads(client, monkeypatch):
    calls = []
    monkeypatch.setattr(search, "_maybe_refresh", lambda: calls.append("poll"))
    real_read = intent.read_record
    monkeypatch.setattr(intent, "read_record",
                        lambda serving: calls.append("read") or real_read(serving))
    assert client.get(ROUTE).status_code == 200
    assert calls == ["poll", "read"]


def test_a_stale_worker_catches_up_through_the_endpoint(client, monkeypatch):
    """Production runs several uvicorn workers. Worker A takes the admin edit,
    records version 2 and bumps the index version; this worker still serves
    version 1. Before this fix only a visitor's chat query ran the version
    poll, so on a quiet install the card here stayed on the warning forever.

    The poll hands the rebuild to a background thread, so the GET that sees
    the new version still answers from the old model; the reload after the
    rebuild finishes shows version 2. The rebuild is a spy that does what a
    real one does for this model (load_or_train on the new data), because a
    real rebuild of the empty test database would remove the record."""
    import threading
    from app.db.queries import set_setting

    _record()
    _record(tag="second", serve=False)
    assert client.get(ROUTE).json()["state"] == "unreadable"

    rebuilt = threading.Event()
    rebuilds = []

    def rebuild(publish, version_floor=0):
        try:
            rebuilds.append((publish, version_floor))
            vectors, texts, ids = _corpus(tag="second")
            search.intent_classifier = intent.load_or_train(vectors, texts, ids, MODEL_NAME)
        finally:
            rebuilt.set()

    monkeypatch.setattr(search, "_rebuild", rebuild)
    monkeypatch.setattr(search, "_index_version", search._index_version)
    monkeypatch.setattr(search, "_last_version_check", 0.0)
    set_setting(search.INDEX_VERSION_KEY, str(search._index_version + 5))

    first = client.get(ROUTE).json()
    assert rebuilt.wait(timeout=10), "the endpoint never started the rebuild"
    assert rebuilds == [(False, search._read_index_version())]
    assert first["state"] in ("unreadable", "trained")

    after = client.get(ROUTE).json()
    assert after["state"] == "trained"
    assert after["model"]["model_version"] == 2
    assert search.intent_classifier.loaded_from_artifact
    assert len(rebuilds) == 1, "a current worker must not rebuild again"


# ── C5: no path, no directory name ──────────────────────────────────────


@pytest.mark.parametrize("prepare", [lambda: None, _record,
                                     lambda: (_record(), _garble_sidecar())],
                         ids=["none", "trained", "unreadable"])
def test_the_response_never_names_a_path_or_the_directory(client, prepare):
    prepare()
    res = client.get(ROUTE)
    assert res.status_code == 200
    text = res.text
    directory = _dir()
    for needle in (directory, os.path.basename(directory), os.path.dirname(directory),
                   intent.WEIGHTS_FILENAME, intent.METADATA_FILENAME,
                   intent.HISTORY_FILENAME, intent.LOCK_FILENAME):
        assert needle and needle not in text, needle


# ── C6: reading never writes ────────────────────────────────────────────


def _garble_history():
    with open(intent.history_path(), "a", encoding="utf-8") as fh:
        fh.write("{half a line\n")


@pytest.mark.parametrize("prepare", [lambda: None, _garble_sidecar, _garble_history,
                                     _weights_edited],
                         ids=["intact", "garbled sidecar", "garbled history",
                              "weights edited"])
def test_reading_leaves_the_directory_byte_identical(client, prepare, monkeypatch):
    _record()
    _record(tag="second")
    prepare()
    fit_calls = []
    monkeypatch.setattr(intent, "fit", lambda *a, **kw: fit_calls.append(a))
    monkeypatch.setattr(intent, "train", lambda *a, **kw: fit_calls.append(a))
    serving = search.intent_classifier
    before = _snapshot(_dir())
    for _ in range(3):
        assert client.get(ROUTE).status_code == 200
    assert _snapshot(_dir()) == before
    assert fit_calls == []
    assert search.intent_classifier is serving


def test_reading_an_absent_directory_does_not_create_it(client):
    assert not os.path.exists(_dir())
    assert client.get(ROUTE).json()["state"] == "none"
    assert not os.path.exists(_dir())


def test_the_endpoint_is_read_only_over_http(client):
    _record()
    for method in ("post", "put", "patch", "delete"):
        res = getattr(client, method)(ROUTE)
        assert res.status_code == 405, method
