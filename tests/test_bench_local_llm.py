"""Contract tests for scripts/bench_local_llm.py, the local-LLM selection bench.

The bench produces the first measured numbers for a self-hosted model behind
the `openai_compatible` adapter (docs/features/local-inference/BENCH.md). A
bench that miscounts is worse than no bench: a truncated reply counted as
valid JSON, or an invented record id counted as grounded, would publish a
pass for a model the selection tier would reject on every turn.

So every deny-case here sits next to its closest allow-control: the fenced
JSON that IS accepted next to the prose that is not, the complete reply next
to the cut-off one, the in-set id next to the invented one.

The model is a fake OpenAI-compatible server on 127.0.0.1, inside this test
process. The harness reaches it through the real adapter and the real
select_records, so the request shape it asserts is the one production sends.
No GPU, no network (the embedding model is switched off, so retrieval runs
BM25-only and never downloads anything).
"""
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import bench_local_llm as bench  # noqa: E402

SECRET = "bench-sentinel-notreal-key-7f3a9c"

CORPUS = {
    "corpus_version": "bench-fixture-1",
    "synonyms": [],
    "entries": [
        {"id": "faq-dates", "title": "تاریخ برگزاری نمایشگاه",
         "text": "نمایشگاه از ده تا دوازده آبان برگزار می شود.",
         "title_en": "Fair dates", "text_en": "The fair runs in November.",
         "questions": ["نمایشگاه کی برگزار می شود"]},
        {"id": "faq-venue", "title": "محل برگزاری نمایشگاه",
         "text": "نمایشگاه در سالن شماره سه برگزار می شود.",
         "title_en": "Fair venue", "text_en": "The fair is held in hall three.",
         "questions": ["نمایشگاه کجاست"]},
        {"id": "faq-parking", "title": "پارکینگ نمایشگاه",
         "text": "پارکینگ رایگان کنار در شمالی است.",
         "title_en": "Parking", "text_en": "Free parking is by the north gate.",
         "questions": ["پارکینگ دارد"]},
    ],
}

Q_DATES = "تاریخ برگزاری نمایشگاه"
Q_VENUE = "محل برگزاری نمایشگاه"
Q_GOLD = "قیمت طلا امروز چند است؟ نمایشگاه"


def _reply(content, finish_reason="stop", usage=None, timings=None, status=200):
    return {"content": content, "finish_reason": finish_reason,
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20,
                               "total_tokens": 120},
            "timings": timings, "status": status}


def _answer(record_id):
    return _reply(json.dumps({"mode": "answer", "ids": [record_id],
                              "lead": "", "reason": "test"}))


class FakeModel:
    """A scripted OpenAI-compatible /v1/chat/completions on 127.0.0.1:0.

    `script` maps the visitor's message to a reply dict (see _reply). Every
    request body and Authorization header is kept for assertions.
    """

    def __init__(self, script):
        self.script = script
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append({"path": self.path, "body": body,
                                      "auth": self.headers.get("Authorization")})
                user = [m for m in body.get("messages", []) if m["role"] == "user"]
                spec = fake.script.get(user[-1]["content"] if user else "",
                                       _reply('{"mode": "none", "ids": []}'))
                if spec["status"] != 200:
                    payload = {"error": {"message": spec["content"],
                                         "type": "invalid_request_error"}}
                else:
                    payload = {"id": "fake-1", "object": "chat.completion",
                               "choices": [{"index": 0, "finish_reason": spec["finish_reason"],
                                            "message": {"role": "assistant",
                                                        "content": spec["content"]}}],
                               "usage": spec["usage"]}
                    if spec["timings"]:
                        payload["timings"] = spec["timings"]
                raw = json.dumps(payload).encode()
                self.send_response(spec["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    """BM25-only retrieval and a sentinel 'developer' database.

    The sentinel is what the harness must never write to: after a run it must
    still hold exactly the one row put there.
    """
    import app.config as config
    from app.services import embeddings

    monkeypatch.setattr(embeddings, "available", lambda: False)
    dev_db = tmp_path / "developer.db"
    conn = sqlite3.connect(dev_db)
    conn.execute("CREATE TABLE dataset (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO dataset VALUES ('customer-row', 'do not touch')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(dev_db))
    return dev_db


def _run(tmp_path, fake, queries, extra=(), key=SECRET):
    golden = {"dataset_version": "bench-golden-1", "queries": queries}
    (tmp_path / "golden.json").write_text(json.dumps(golden, ensure_ascii=False), "utf-8")
    (tmp_path / "corpus.json").write_text(json.dumps(CORPUS, ensure_ascii=False), "utf-8")
    key_file = tmp_path / "key"
    key_file.write_text(key + "\n")
    out = tmp_path / "result.json"
    code = bench.main([
        "--base-url", fake.base_url, "--model", "fake-model",
        "--api-key-file", str(key_file),
        "--golden", str(tmp_path / "golden.json"),
        "--corpus", str(tmp_path / "corpus.json"),
        "--out", str(out), *extra])
    assert code == 0
    return json.loads(out.read_text("utf-8"))


def _q(q, expect, cat="current_event_facts"):
    return {"q": q, "expect": expect, "cat": cat}


def test_a_grounded_json_answer_is_valid_grounded_and_correct(tmp_path, hermetic):
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    rec = result["records"][0]
    assert "faq-dates" in rec["candidate_ids"]
    assert rec["json_valid"] and rec["mode_valid"] and rec["grounded"]
    assert rec["correct"] is True
    assert rec["decision_mode"] == "answer"
    assert rec["prompt_tokens"] == 100 and rec["completion_tokens"] == 20
    assert rec["finish_reason"] == "stop"
    assert rec["latency_ms"] > 0
    summary = result["summary"]
    assert summary["n"] == 1
    for key in ("json_valid", "grounded", "correct"):
        assert summary[key]["rate"] == 1.0, key
    assert result["method"], "the output must say how the model was called"


def test_the_request_is_sent_the_way_select_records_sends_it(tmp_path, hermetic):
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    (req,) = fake.requests
    body = req["body"]
    assert req["path"] == "/v1/chat/completions"
    assert body["model"] == "fake-model"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 400
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"].startswith("You are the retrieval assistant")
    assert "faq-dates |" in body["messages"][0]["content"], "candidates are in the prompt"
    assert body["messages"][-1] == {"role": "user", "content": Q_DATES}
    assert "chat_template_kwargs" not in body
    assert req["auth"] == f"Bearer {SECRET}", "the key file is actually used"


def test_disable_thinking_sends_the_adapter_chat_template_switch(tmp_path, hermetic):
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")], extra=["--disable-thinking"])

    assert fake.requests[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("content,valid", [
    ("Sure! The answer is faq-dates.", False),
    ('```json\n{"mode": "answer", "ids": ["faq-dates"]}\n```', True),
])
def test_prose_is_invalid_json_but_a_fenced_object_is_accepted(tmp_path, hermetic,
                                                               content, valid):
    with FakeModel({Q_DATES: _reply(content)}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    rec = result["records"][0]
    assert rec["json_valid"] is valid
    assert rec["correct"] is valid


@pytest.mark.parametrize("content,finish,valid", [
    ('{"mode": "answer", "ids": ["faq-da', "length", False),
    ('{"mode": "answer", "ids": ["faq-dates"]}', "length", False),
    ('{"mode": "answer", "ids": ["faq-dates"]}', "stop", True),
])
def test_a_truncated_reply_is_invalid_even_when_it_happens_to_parse(tmp_path, hermetic,
                                                                     content, finish, valid):
    with FakeModel({Q_DATES: _reply(content, finish_reason=finish)}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    rec = result["records"][0]
    assert rec["finish_reason"] == finish
    assert rec["json_valid"] is valid
    assert rec["correct"] is valid


@pytest.mark.parametrize("ids,grounded", [
    (["invented-99"], False),
    (["faq-dates", "invented-99"], False),
    (["faq-dates"], True),
])
def test_an_id_outside_the_candidate_set_is_not_grounded(tmp_path, hermetic, ids, grounded):
    reply = _reply(json.dumps({"mode": "answer", "ids": ids}))
    with FakeModel({Q_DATES: reply}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    rec = result["records"][0]
    assert rec["json_valid"] is True
    assert rec["grounded"] is grounded
    assert rec["ids_out_of_set"] == [i for i in ids if i == "invented-99"]


def test_an_invented_id_alone_is_never_counted_correct(tmp_path, hermetic):
    reply = _reply(json.dumps({"mode": "answer", "ids": ["invented-99"]}))
    with FakeModel({Q_DATES: reply}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "invented-99")])

    rec = result["records"][0]
    assert rec["grounded"] is False
    assert rec["correct"] is False, "a golden expect outside the candidates cannot be won"


@pytest.mark.parametrize("mode,valid", [("maybe", False), ("none", True)])
def test_an_unknown_mode_is_invalid(tmp_path, hermetic, mode, valid):
    reply = _reply(json.dumps({"mode": mode, "ids": []}))
    with FakeModel({Q_GOLD: reply}) as fake:
        result = _run(tmp_path, fake, [_q(Q_GOLD, None, "unsupported")])

    rec = result["records"][0]
    assert rec["json_valid"] is True
    assert rec["mode_valid"] is valid
    assert rec["grounded"] is valid


@pytest.mark.parametrize("reply,correct", [
    (_reply('{"mode": "none", "ids": []}'), True),
    (_reply('{"mode": "converse", "ids": [], "lead": "سلام، خوش آمدید"}'), True),
    (_answer("faq-dates"), False),
])
def test_an_out_of_scope_query_is_correct_only_when_nothing_is_served(tmp_path, hermetic,
                                                                     reply, correct):
    with FakeModel({Q_GOLD: reply}) as fake:
        result = _run(tmp_path, fake, [_q(Q_GOLD, None, "unsupported")])

    assert result["records"][0]["correct"] is correct


@pytest.mark.parametrize("decision,expect,correct", [
    ({"mode": "answer", "ids": ["a"]}, "a", True),
    ({"mode": "answer", "ids": ["b"]}, "a", False),
    ({"mode": "options", "ids": ["b", "a"]}, "a", True),
    ({"mode": "options", "ids": ["b", "c"]}, "a", False),
    ({"mode": "none", "ids": []}, "a", False),
    (None, "a", False),
    ({"mode": "none", "ids": []}, None, True),
    ({"mode": "converse", "ids": []}, None, True),
    ({"mode": "options", "ids": ["b", "c"]}, None, False),
    (None, None, False),
])
def test_correctness_rule(decision, expect, correct):
    assert bench.is_correct(decision, expect) is correct


def test_percentiles_interpolate_between_closest_ranks():
    assert bench.percentile([], 50) is None
    assert bench.percentile([7.0], 95) == 7.0
    assert bench.percentile([4, 1, 3, 2], 50) == 2.5
    assert bench.percentile(list(range(1, 21)), 95) == pytest.approx(19.05)
    assert bench.percentile([3, 9, 1], 100) == 9
    assert bench.percentile([3, 9, 1], 0) == 1


def test_latency_summary_uses_the_percentile_rule():
    records = [{"latency_ms": v, "sent": True, "completion_tokens": 10}
               for v in (100.0, 200.0, 300.0, 400.0)]
    lat = bench.latency_summary(records)
    assert lat == {"n": 4, "p50_ms": 250.0, "p95_ms": pytest.approx(385.0),
                   "max_ms": 400.0}


def test_tokens_per_second_prefers_server_generation_timings(tmp_path, hermetic):
    timings = {"prompt_n": 100, "prompt_ms": 50.0, "predicted_n": 20, "predicted_ms": 400.0}
    script = {Q_DATES: _reply(json.dumps({"mode": "answer", "ids": ["faq-dates"]}),
                              timings=timings)}
    with FakeModel(script) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    tps = result["summary"]["tokens_per_second"]
    assert tps["basis"] == "server_timings"
    assert tps["generation"] == pytest.approx(50.0)
    assert tps["prompt"] == pytest.approx(2000.0)


def test_tokens_per_second_is_labelled_end_to_end_without_server_timings(tmp_path, hermetic):
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    tps = result["summary"]["tokens_per_second"]
    assert tps["basis"] == "end_to_end"
    assert tps["generation"] > 0
    assert tps["prompt"] is None


def test_repeat_and_concurrency_run_every_query_every_time(tmp_path, hermetic):
    queries = [_q(Q_DATES, "faq-dates"), _q(Q_VENUE, "faq-venue"),
               _q(Q_GOLD, None, "unsupported"), _q(Q_DATES + " لطفا", "faq-dates")]
    script = {Q_DATES: _answer("faq-dates"), Q_VENUE: _answer("faq-venue"),
              Q_DATES + " لطفا": _answer("faq-dates")}
    with FakeModel(script) as fake:
        result = _run(tmp_path, fake, queries, extra=["--repeat", "2", "--concurrency", "4"])

    assert len(result["records"]) == 8
    assert sorted((r["run"], r["index"]) for r in result["records"]) == [
        (run, i) for run in (1, 2) for i in range(4)]
    assert len(result["per_run"]) == 2
    assert result["summary"]["correct"] == {"count": 8, "of": 8, "rate": 1.0}
    assert len(fake.requests) == 8


def test_the_api_key_never_reaches_the_output_or_the_logs(tmp_path, hermetic, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    script = {
        Q_DATES: _reply(f"echo: Authorization: Bearer {SECRET}", status=401),
        Q_VENUE: _reply(f'{{"mode": "none", "ids": [], "reason": "{SECRET}"}}'),
    }
    with FakeModel(script) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates"), _q(Q_VENUE, "faq-venue")])

    assert all(r["auth"] == f"Bearer {SECRET}" for r in fake.requests)
    captured = capsys.readouterr()
    assert SECRET not in (tmp_path / "result.json").read_text("utf-8")
    assert SECRET not in captured.out + captured.err
    assert "[selection] provider failed" in caplog.text, (
        "the app logger is captured here, so the next line can see a leak")
    assert SECRET not in caplog.text
    assert result["records"][0]["error_code"], "the 401 is recorded by code"
    assert result["records"][0]["json_valid"] is False


def test_a_key_that_json_escapes_is_scrubbed_in_its_escaped_form(tmp_path, hermetic, capsys):
    key = 'bench-notreal-"quoted\\key'
    script = {Q_DATES: _reply(f"echo: Bearer {key}")}
    with FakeModel(script) as fake:
        _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")], key=key)

    text = (tmp_path / "result.json").read_text("utf-8")
    assert fake.requests[0]["auth"] == f"Bearer {key}"
    assert key not in text
    assert json.dumps(key)[1:-1] not in text, "the JSON-escaped form is the one written"
    assert "[REDACTED]" in text
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err


def test_an_api_key_on_the_command_line_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        bench.main(["--base-url", "http://127.0.0.1:1/v1", "--model", "m",
                    "--api-key", SECRET, "--golden", "g", "--corpus", "c",
                    "--out", str(tmp_path / "o.json")])
    assert exc.value.code == 2
    assert "unrecognized arguments: --api-key" in capsys.readouterr().err, (
        "--api-key must not be accepted as an abbreviation of --api-key-file")


def test_the_developer_database_is_never_written(tmp_path, hermetic, monkeypatch):
    import os

    import app.config as config

    monkeypatch.setenv("DB_PATH", "/sentinel/db-path")
    monkeypatch.delenv("LOGS_DB_PATH", raising=False)
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        _run(tmp_path, fake, [_q(Q_DATES, "faq-dates")])

    assert config.DB_PATH == str(hermetic), "the harness restores the path it replaced"
    assert os.environ.get("DB_PATH") == "/sentinel/db-path", (
        "a later subprocess must not inherit the deleted throwaway path")
    assert "LOGS_DB_PATH" not in os.environ
    conn = sqlite3.connect(hermetic)
    rows = conn.execute("SELECT id FROM dataset").fetchall()
    conn.close()
    assert rows == [("customer-row",)]


def test_a_postgres_backend_is_refused_before_any_model_call(tmp_path, hermetic, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        golden = {"queries": [_q(Q_DATES, "faq-dates")]}
        (tmp_path / "g.json").write_text(json.dumps(golden), "utf-8")
        (tmp_path / "c.json").write_text(json.dumps(CORPUS), "utf-8")
        (tmp_path / "k").write_text(SECRET)
        with pytest.raises(SystemExit) as exc:
            bench.main(["--base-url", fake.base_url, "--model", "m",
                        "--api-key-file", str(tmp_path / "k"),
                        "--golden", str(tmp_path / "g.json"),
                        "--corpus", str(tmp_path / "c.json"),
                        "--out", str(tmp_path / "o.json")])
    assert exc.value.code == 2
    assert fake.requests == []


def test_grounding_is_also_reported_over_replies_that_parsed(tmp_path, hermetic):
    q_bad = Q_DATES + " لطفا"
    script = {
        Q_DATES: _reply("not json at all"),
        q_bad: _reply(json.dumps({"mode": "answer", "ids": ["invented-99"]})),
        Q_VENUE: _answer("faq-venue"),
    }
    with FakeModel(script) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates"), _q(q_bad, "faq-dates"),
                                       _q(Q_VENUE, "faq-venue")])

    summary = result["summary"]
    assert summary["grounded"] == {"count": 1, "of": 3, "rate": 0.3333}
    assert summary["grounded_of_mode_valid"] == {"count": 1, "of": 2, "rate": 0.5}
    assert summary["ids_out_of_set_replies"] == {"count": 1, "of": 3, "rate": 0.3333}


def test_correctness_is_also_reported_over_queries_the_model_saw(tmp_path, hermetic):
    nothing_in_common = "zzqx wvvk"
    with FakeModel({Q_DATES: _answer("faq-dates")}) as fake:
        result = _run(tmp_path, fake, [_q(Q_DATES, "faq-dates"),
                                       _q(nothing_in_common, None, "unsupported")])

    skipped = result["records"][1]
    assert skipped["skipped"] is True and skipped["sent"] is False
    assert len(fake.requests) == 1
    summary = result["summary"]
    assert summary["correct"] == {"count": 1, "of": 2, "rate": 0.5}
    assert summary["correct_of_sent"] == {"count": 1, "of": 1, "rate": 1.0}
    assert "unsupported" not in summary["correct_by_category_of_sent"]
    assert summary["correct_by_category"]["unsupported"]["of"] == 1


def test_retrieval_runs_outside_every_measured_window_under_concurrency(tmp_path, hermetic,
                                                                       monkeypatch):
    """Ordering, not wall-clock: the result must not depend on machine load.

    A request's latency window opens at the call into adapter.invoke (the
    harness starts its timer right before it, with no await in between) and
    closes when invoke returns. Deny-case: a retrieval that ends after any
    window opened would sit inside a sibling's measured latency. Allow-control:
    each window still contains its own model call (the fake server saw the
    request between that window's open and close).
    """
    import time

    from app.services import search

    retrieval_ends, windows, arrivals = [], [], []
    real_find = search.find_top_matches

    def stamped_find(query, k=8):
        try:
            return real_find(query, k=k)
        finally:
            retrieval_ends.append(time.monotonic())

    monkeypatch.setattr(search, "find_top_matches", stamped_find)

    real_make = bench._make_adapter

    def stamped_adapter():
        adapter = real_make()
        real_invoke = adapter.invoke

        async def invoke(rt, model, req):
            opened = time.monotonic()
            try:
                return await real_invoke(rt, model, req)
            finally:
                windows.append((opened, time.monotonic()))

        adapter.invoke = invoke
        return adapter

    monkeypatch.setattr(bench, "_make_adapter", stamped_adapter)

    class StampedModel(FakeModel):
        def __init__(self, script):
            super().__init__(script)
            handler = self.server.RequestHandlerClass
            original = handler.do_POST

            def do_post(handler_self):
                arrivals.append(time.monotonic())
                original(handler_self)

            handler.do_POST = do_post

    queries = [_q(Q_DATES, "faq-dates"), _q(Q_VENUE, "faq-venue"),
               _q(Q_DATES + " لطفا", "faq-dates"), _q(Q_VENUE + " لطفا", "faq-venue")]
    with StampedModel({}) as fake:
        _run(tmp_path, fake, queries, extra=["--concurrency", "4"])

    assert len(retrieval_ends) == 4 and len(windows) == 4 and len(arrivals) == 4
    assert max(retrieval_ends) <= min(opened for opened, _ in windows), (
        "a retrieval ended after a measured window opened, so its CPU time "
        "counts as model latency")
    for opened, closed in windows:
        assert any(opened <= t <= closed for t in arrivals), (
            "a measured window must contain its own request to the model")


def test_a_non_sqlite_backend_in_the_environment_exits_2(tmp_path):
    env = {**os.environ, "DB_BACKEND": "postgres"}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench_local_llm.py"), "--help"],
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "SQLite-only" in proc.stderr
