"""The ingest module on a real PostgreSQL 16 (migrations/0030_ingest.sql).

The SQLite suite (tests/test_ingest_pipeline.py, tests/test_ingest_api.py)
cannot see the bugs that only exist here, and S3 leans on three of them:

- the two partial unique indexes are the ONLY controls for "one active job
  per content" and "one job in the model stage"; psycopg raises
  UniqueViolation, not sqlite3.IntegrityError, and the refused statement
  aborts the whole transaction (SC-005, SC-006, SC-029);
- approve writes dataset, questions and synonyms in one transaction, and a
  retry after a clashing dataset id is only possible from a SAVEPOINT
  (SC-014, REQ-058 step 3);
- every timestamp is TIMESTAMPTZ and comes back as an aware datetime, so the
  five-minute and thirty-day clocks are tested here on the real type.

Opt-in like the rest of this directory (RUN_POSTGRES_TESTS=1, see
tests/postgres/conftest.py). The AI is faked on the padyar_ai instance and
the embedding layer is off, so nothing reaches the network.
"""
import asyncio
import json
import secrets
import threading

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    import tempfile
    from app.services import embeddings, ingest
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: False)
    monkeypatch.setattr(ingest, "PACE_SECONDS", 0)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))


def _rows(conn, query, params=()):
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _job(conn, status, job_id=None, **columns):
    job_id = job_id or secrets.token_hex(12)
    conn.execute("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size, status,"
                 " created_by) VALUES (?, 'file', 'doc.docx', ?, 1, ?, 'pgadmin')",
                 (job_id, secrets.token_hex(8), status))
    for name, value in columns.items():
        conn.execute(f"UPDATE ingest_jobs SET {name} = {value} WHERE id = ?", (job_id,))
    conn.commit()
    return job_id


def _proposal(conn, job_id, seq, text, ai_state="local", questions=(), synonyms=()):
    proposal_id = secrets.token_hex(12)
    conn.execute("INSERT INTO ingest_proposals (id, job_id, seq, source_text, title, text, title_source,"
                 " questions, synonyms, ai_state) VALUES (?, ?, ?, ?, ?, ?, 'heading', ?, ?, ?)",
                 (proposal_id, job_id, seq, text, f"عنوان {seq}", text,
                  json.dumps(list(questions), ensure_ascii=False),
                  json.dumps([{"word": w, "suggestion": s} for w, s in synonyms], ensure_ascii=False),
                  ai_state))
    conn.commit()
    return proposal_id


def test_the_migration_creates_both_partial_unique_indexes(raw):
    rows = raw.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'ingest_jobs'").fetchall()
    defs = {name: definition for name, definition in rows}
    assert "UNIQUE" in defs["ux_ingest_jobs_active_hash"] and "WHERE" in defs["ux_ingest_jobs_active_hash"]
    assert "UNIQUE" in defs["ux_ingest_jobs_one_proposing"] and "cancelling" in defs["ux_ingest_jobs_one_proposing"]
    types = dict(raw.execute("SELECT column_name, data_type FROM information_schema.columns"
                             " WHERE table_name = 'ingest_jobs'").fetchall())
    assert types["heartbeat_at"] == "timestamp with time zone"


def test_the_same_bytes_from_two_workers_at_once_make_one_job(conn):
    from app.services import ingest
    barrier = threading.Barrier(2)
    results = []

    def create():
        barrier.wait()
        results.append(ingest.create_job("file", "a.txt", b"same bytes", "txt", "pgadmin"))
    threads = [threading.Thread(target=create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(existing for _, existing in results) == [False, True]
    assert results[0][0]["id"] == results[1][0]["id"]
    assert len(_rows(conn, "SELECT id FROM ingest_jobs")) == 1


def test_a_refused_claim_leaves_the_job_waiting_and_the_connection_usable(conn, monkeypatch):
    from app.services import ingest
    from app.services.ai.wrapper import padyar_ai
    calls = []

    async def fake(messages, **kwargs):
        calls.append(messages)
    monkeypatch.setattr(padyar_ai, "generate", fake)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: True)
    _job(conn, "proposing", job_id="holder")
    waiting = _job(conn, "extracted")
    _proposal(conn, waiting, 1, "متن", ai_state="waiting")
    asyncio.run(ingest.run_model_loop())
    assert calls == []
    assert _rows(conn, "SELECT status FROM ingest_jobs WHERE id = ?", (waiting,))[0]["status"] == "extracted"
    assert ingest.list_jobs()["total"] == 2


def test_a_failing_synonym_insert_rolls_back_the_whole_approval(conn, monkeypatch):
    from app.db import queries
    from app.services import ingest
    proposal_id = _proposal(conn, _job(conn, "ready"), 1, "متن تازه", questions=["این پرسش است؟"],
                            synonyms=[("الف", "ب")])

    def broken(connection, pairs):
        raise RuntimeError("synonym insert failed")
    monkeypatch.setattr(queries, "insert_synonym_pairs", broken)
    with pytest.raises(RuntimeError):
        ingest.approve(proposal_id, "pgadmin")
    assert _rows(conn, "SELECT id FROM dataset") == []
    assert _rows(conn, "SELECT id FROM questions") == []
    assert _rows(conn, "SELECT status, dataset_id FROM ingest_proposals")[0] == {"status": "pending",
                                                                                 "dataset_id": ""}


def test_a_clashing_dataset_id_is_retried_from_a_savepoint(conn, monkeypatch):
    from app.services import ingest
    conn.execute("INSERT INTO dataset (id, title, text, position) VALUES ('ing-0000000000', 'قبلی', 'متن قبلی', 10)")
    conn.commit()
    proposal_id = _proposal(conn, _job(conn, "ready"), 1, "متن تازه", questions=["این پرسش است؟"])
    real = secrets.token_hex
    tokens = iter(["0000000000", "1111111111"])
    monkeypatch.setattr(ingest.secrets, "token_hex", lambda n=None: next(tokens) if n == 5 else real(n))
    assert ingest.approve(proposal_id, "pgadmin")["dataset_id"] == "ing-1111111111"
    assert _rows(conn, "SELECT dataset_id FROM questions") == [{"dataset_id": "ing-1111111111"}]


def test_a_duplicate_found_at_approve_time_is_a_clean_409(conn):
    from app.services import ingest
    conn.execute("INSERT INTO dataset (id, title, text, position) VALUES ('d-1', 'زنده', 'متن مشترك', 10)")
    conn.commit()
    proposal_id = _proposal(conn, _job(conn, "ready"), 1, "متن مشترک")
    with pytest.raises(ingest.IngestError) as refused:
        ingest.approve(proposal_id, "pgadmin")
    assert (refused.value.status, refused.value.code, refused.value.extra) == (409, "duplicate", {"duplicate_of": "d-1"})
    assert _rows(conn, "SELECT similar_kind, status FROM ingest_proposals")[0] == {"similar_kind": "duplicate",
                                                                                   "status": "pending"}


def test_a_cancel_mid_call_holds_the_slot_on_postgres(conn, monkeypatch):
    from app.services import ingest
    from app.services.ai.request import AIResponse
    from app.services.ai.wrapper import padyar_ai
    first = _job(conn, "extracted")
    second = _job(conn, "extracted", created_at="now() + interval '1 second'")
    for job_id in (first, second):
        _proposal(conn, job_id, 1, "متن یک", ai_state="waiting")
        _proposal(conn, job_id, 2, "متن دو", ai_state="waiting")
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: True)
    calls = []

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(messages, **kwargs):
            calls.append(messages[0].content)
            started.set()
            await release.wait()
            return AIResponse(content=json.dumps({"questions": ["پرسش دور ریختنی است؟"]}))
        monkeypatch.setattr(padyar_ai, "generate", slow)
        loop = asyncio.create_task(ingest.run_model_loop())
        await started.wait()
        ingest.cancel_job(first, "pgadmin")
        await ingest.run_model_loop()
        held = _rows(conn, "SELECT id, status FROM ingest_jobs ORDER BY id")
        release.set()
        await loop
        return held

    held = dict((r["id"], r["status"]) for r in asyncio.run(scenario()))
    assert held == {first: "cancelling", second: "extracted"}
    status = dict((r["id"], r["status"]) for r in _rows(conn, "SELECT id, status FROM ingest_jobs"))
    assert status == {first: "cancelled", second: "ready"}
    assert len(calls) == 3
    assert {r["status"] for r in _rows(conn, "SELECT status FROM ingest_proposals WHERE job_id = ?", (first,))} == {
        "rejected"}


def test_the_clocks_read_timestamptz(conn):
    from app.services import ingest
    stale = _job(conn, "proposing", heartbeat_at="now() - interval '6 minutes'")
    fresh = _job(conn, "extracting", heartbeat_at="now() - interval '1 minute'")
    old = _job(conn, "done", finished_at="now() - interval '31 days'")
    young = _job(conn, "done", finished_at="now() - interval '29 days'")
    assert ingest.recover_stale() == 1
    status = dict((r["id"], r["status"]) for r in _rows(conn, "SELECT id, status FROM ingest_jobs"))
    assert (status[stale], status[fresh]) == ("failed", "extracting")
    assert ingest.purge_expired() == 1
    remaining = {r["id"] for r in _rows(conn, "SELECT id FROM ingest_jobs")}
    assert old not in remaining and young in remaining
    shown = ingest.get_job(stale)
    assert shown["finished_at"].endswith("+00:00")


def test_approve_through_the_api_publishes_once_after_the_commit(client, monkeypatch):
    from app.db.connection import get_db_connection
    from app.services import search
    published = []

    def recorder(timeout_s=120.0):
        db = get_db_connection()
        try:
            published.append([r["id"] for r in db.execute("SELECT id FROM dataset").fetchall()])
        finally:
            db.close()
        return True
    monkeypatch.setattr(search, "reindex_and_publish_until_done", recorder)
    db = get_db_connection()
    try:
        job_id = _job(db, "ready")
        proposal_id = _proposal(db, job_id, 1, "متن تازه برای انتشار", questions=["این پرسش است؟"])
    finally:
        db.close()
    response = client.post(f"/admin/api/ingest/proposals/{proposal_id}/approve")
    assert response.status_code == 200, response.text
    dataset_id = response.json()["dataset_id"]
    assert published == [[dataset_id]]
    listed = client.get(f"/admin/api/ingest/jobs/{job_id}/proposals", params={"status": "all"}).json()
    assert listed["items"][0]["reviewed_at"].endswith("+00:00")
    assert client.get(f"/admin/api/ingest/jobs/{job_id}").json()["job"]["status"] == "done"
