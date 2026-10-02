#!/usr/bin/env python3
"""Local-LLM selection bench: how well does a self-hosted model do Tier 2's job?

Tier 2 (the selection tier, app/services/answer.py) shows the model the top
ANSWER_TOPK records from local retrieval and asks for ONE JSON object naming
record ids. This script replays a golden set through exactly that call against
any OpenAI-compatible endpoint (a llama.cpp `llama-server`, typically) and
writes what happened, per query and in summary, to a JSON file.

HOW THE MODEL IS CALLED (also written to the output's `method` field):
  * Candidates come from THIS repo's retrieval, `search.find_top_matches`
    (BM25 + local embeddings + feature reranker), over the given corpus seeded
    into a THROWAWAY SQLite database. Never the developer's database, never
    PostgreSQL: the corpus seed deletes the dataset tables it writes to.
    Each run's candidates are built for every query BEFORE the timed,
    concurrent part, so neither `latency_ms` nor a run's `wall_s` includes
    retrieval time.
  * The real `select_records` runs: the real `build_selection_prompt`, the
    real parse, the real grounding gate. Only `padyar_ai.generate` is replaced
    for the run, because the wrapper resolves the "chat" route from the
    DB-backed provider store. The replacement calls the real
    `OpenAICompatibleAdapter.invoke` directly. So there is no routing engine
    here: no retry, no failover, no circuit breaker.
  * `select_records` sends: temperature 0, max_output_tokens 400,
    response_format json_object, timeout 45 s. The harness does not choose
    those values; it receives them from `select_records` and records them.
  * The adapter drops llama-server's `timings` object. A subclass overrides
    ONLY `BaseAdapter.http` to keep that field from the raw reply, so
    tokens/s is the server's own generation rate whenever it is reported.

WHAT IS NOT REPRODUCED from app/routers/chat.py: the unknown-salient-token
skip and the conversational gate that decide whether Tier 2 runs at all. Every
query that retrieval returns candidates for is sent to the model; a query with
no candidates is recorded as skipped (select_records makes no call for it).
History is empty (the golden set is single-turn). `lang` is "fa" when the
query holds Arabic-script letters, else "en".

DEFINITIONS (per model reply):
  json_valid   finish_reason is not "length" AND `_parse_json_object` returns
               a dict. A cut-off reply is invalid even if it parses, because
               select_records discards it before parsing.
  mode_valid   json_valid AND mode is one of answer/options/converse/none.
  grounded     mode_valid AND every id in "ids" is a string from the candidate
               list, AND mode answer/options names at least one id.
  correct      read off the decision select_records RETURNS (after its gates),
               i.e. what the product would act on:
                 expect set  : mode answer with ids[0] == expect, or mode
                               options with expect among the ids.
                 expect null : mode none or converse (nothing served).
               A None decision (invalid reply, provider error, no candidates)
               is never correct.

The summary gives each strict rate and a narrower one next to it, so a
reader can tell the failure kinds apart:
  grounded               over every model call (a malformed reply fails it)
  grounded_of_mode_valid over calls whose reply parsed with a valid mode
  ids_out_of_set_replies calls whose reply named an id outside the candidates
  correct                over every query (a query with no candidates fails it)
  correct_of_sent        over the queries the model actually saw

USAGE (from the project root):
    .venv/bin/python scripts/bench_local_llm.py \\
        --base-url http://127.0.0.1:18010/v1 --model gemma-4-12b \\
        --api-key-file ~/.padyar-llm-key \\
        --golden data/eval/golden.json --corpus data/eval/corpus.json \\
        --out result.json --repeat 3 [--concurrency 4] [--disable-thinking]

The API key is only ever read from a file. It is sent as the Bearer header and
scrubbed from everything this script prints or writes.

Exit code 0 when the bench ran (it is a measurement, not a gate); 2 on a
configuration error, before any model call.
"""
import argparse
import asyncio
import contextvars
import json
import math
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# SQLite-only, the same pin scripts/run_eval.py uses: app/config.py resolves
# DB_BACKEND once, at import time, so it must be set before the first app
# import. A process variable asking for another backend is refused, not
# silently overridden.
_requested_backend = os.environ.get("DB_BACKEND", "").strip().lower()
if _requested_backend and _requested_backend != "sqlite":
    print(f"bench_local_llm.py is SQLite-only and cannot run with "
          f"DB_BACKEND={_requested_backend!r}. Unset it for this command.",
          file=sys.stderr)
    raise SystemExit(2)
os.environ["DB_BACKEND"] = "sqlite"

MODES = ("answer", "options", "converse", "none")

METHOD = (
    "real select_records (app/services/answer.py) with build_selection_prompt; "
    "padyar_ai.generate replaced for the run by a direct call to the real "
    "OpenAICompatibleAdapter.invoke, because the wrapper resolves the 'chat' "
    "route from the DB-backed provider store (no routing engine: no retry, "
    "failover or circuit breaker). A subclass overrides only BaseAdapter.http "
    "to read llama-server's 'timings' from the raw HTTP reply, which the "
    "adapter drops. Candidates: search.find_top_matches(strip_leading_greeting"
    "(q), k=ANSWER_TOPK) over the corpus in a throwaway SQLite DB, built for "
    "every query before the timed concurrent part. Not "
    "reproduced: the router's unknown-token skip and conversational gate. "
    "History empty; lang 'fa' if the query has Arabic-script letters else 'en'."
)

# Per-request capture, filled by the replaced generate() and the http()
# override. A ContextVar because --concurrency runs several requests at once.
_CAPTURE = contextvars.ContextVar("bench_capture", default=None)


class ConfigError(Exception):
    pass


# ── Pure scoring helpers (unit-tested) ──────────────────────────────────

def percentile(values, p):
    """Linear interpolation between closest ranks (numpy's default method).

    rank = (n - 1) * p / 100 over the sorted values. None for no values.
    """
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def latency_summary(records):
    """p50 / p95 / max over model calls that returned a reply (no error)."""
    xs = [r["latency_ms"] for r in records
          if r.get("sent") and r.get("latency_ms") is not None
          and not r.get("error_code")]
    if not xs:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {"n": len(xs),
            "p50_ms": round(percentile(xs, 50), 3),
            "p95_ms": round(percentile(xs, 95), 3),
            "max_ms": round(max(xs), 3)}


def judge_reply(content, finish_reason, allowed_ids):
    """The per-reply checks, on the RAW model reply (see DEFINITIONS)."""
    from app.services.answer import _parse_json_object

    data = None
    if content is not None and finish_reason != "length":
        data = _parse_json_object(content)
    json_valid = data is not None
    mode = data.get("mode") if json_valid else None
    mode_valid = json_valid and mode in MODES
    raw_ids = data.get("ids") if json_valid else None
    ids_list = raw_ids if isinstance(raw_ids, list) else []
    out_of_set = [str(i)[:80] for i in ids_list
                  if not (isinstance(i, str) and i in allowed_ids)]
    grounded = (mode_valid
                and (raw_ids is None or isinstance(raw_ids, list))
                and not out_of_set
                and (mode not in ("answer", "options") or len(ids_list) >= 1))
    return {"json_valid": json_valid, "mode_valid": bool(mode_valid),
            "raw_mode": mode if isinstance(mode, str) else None,
            "raw_ids": [str(i)[:80] for i in ids_list][:20],
            "ids_out_of_set": out_of_set[:20],
            "grounded": bool(grounded)}


def is_correct(decision, expect):
    """Correctness of the decision select_records returned (see DEFINITIONS)."""
    if not decision:
        return False
    mode, ids = decision.get("mode"), decision.get("ids") or []
    if expect is None:
        return mode in ("none", "converse")
    if mode == "answer":
        return bool(ids) and ids[0] == expect
    if mode == "options":
        return expect in ids
    return False


def _rate(records, key):
    n = len(records)
    k = sum(1 for r in records if r.get(key))
    return {"count": k, "of": n, "rate": round(k / n, 4) if n else None}


def tokens_per_second(records):
    """Server generation rate when llama-server reported `timings`, else end-to-end."""
    timed = [r["server_timings"] for r in records
             if r.get("server_timings")
             and r["server_timings"].get("predicted_ms")]
    if timed:
        gen_n = sum(t.get("predicted_n") or 0 for t in timed)
        gen_s = sum(t.get("predicted_ms") or 0.0 for t in timed) / 1000.0
        pr = [t for t in timed if t.get("prompt_ms")]
        pr_n = sum(t.get("prompt_n") or 0 for t in pr)
        pr_s = sum(t["prompt_ms"] for t in pr) / 1000.0
        return {"basis": "server_timings", "n": len(timed),
                "generation": round(gen_n / gen_s, 3) if gen_s else None,
                "prompt": round(pr_n / pr_s, 3) if pr_s else None}
    done = [r for r in records if r.get("sent") and not r.get("error_code")
            and r.get("completion_tokens") and r.get("latency_ms")]
    secs = sum(r["latency_ms"] for r in done) / 1000.0
    toks = sum(r["completion_tokens"] for r in done)
    return {"basis": "end_to_end", "n": len(done),
            "generation": round(toks / secs, 3) if secs else None,
            "prompt": None}


def summarise(records):
    sent = [r for r in records if r.get("sent")]
    by_cat = {}
    for r in records:
        by_cat.setdefault(r.get("cat") or "", []).append(r)
    answerable = [r for r in records if r.get("expect") is not None]
    return {
        "n": len(records),
        "sent": len(sent),
        "skipped_no_candidates": sum(1 for r in records if r.get("skipped")),
        "errors": sum(1 for r in sent if r.get("error_code")),
        "json_valid": _rate(sent, "json_valid"),
        "mode_valid": _rate(sent, "mode_valid"),
        "grounded": _rate(sent, "grounded"),
        # `grounded` above also fails every malformed reply. These two isolate
        # invented ids: over replies that parsed with a valid mode, and as a
        # count of replies naming an id outside the candidate list.
        "grounded_of_mode_valid": _rate([r for r in sent if r.get("mode_valid")], "grounded"),
        "ids_out_of_set_replies": _rate(sent, "ids_out_of_set"),
        "correct": _rate(records, "correct"),
        # A query retrieval found no candidates for never reached the model.
        # `correct` counts it wrong; these leave it out.
        "correct_of_sent": _rate(sent, "correct"),
        "expect_in_candidates": _rate(answerable, "expect_in_candidates"),
        "correct_by_category": {c: _rate(rs, "correct") for c, rs in sorted(by_cat.items())},
        "correct_by_category_of_sent": {
            c: _rate([r for r in rs if r.get("sent")], "correct")
            for c, rs in sorted(by_cat.items()) if any(r.get("sent") for r in rs)},
        "latency": latency_summary(records),
        "tokens_per_second": tokens_per_second(records),
        "prompt_tokens_total": sum(r.get("prompt_tokens") or 0 for r in sent),
        "completion_tokens_total": sum(r.get("completion_tokens") or 0 for r in sent),
    }


# ── Inputs ──────────────────────────────────────────────────────────────

def _parser():
    # allow_abbrev=False: otherwise `--api-key SECRET` would be accepted as an
    # abbreviation of --api-key-file and put a key on the command line.
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    p.add_argument("--base-url", required=True,
                   help="OpenAI-compatible base, e.g. http://127.0.0.1:18010/v1")
    p.add_argument("--model", required=True, help="model id sent in the request body")
    p.add_argument("--api-key-file",
                   help="file holding the API key (never pass the key itself)")
    p.add_argument("--golden", required=True, help="golden JSON with a 'queries' list")
    p.add_argument("--corpus", required=True, help="corpus JSON with an 'entries' list")
    p.add_argument("--out", required=True, help="where to write the result JSON")
    p.add_argument("--repeat", type=int, default=1, help="full passes over the golden set")
    p.add_argument("--concurrency", type=int, default=1,
                   help="requests in flight at once (a load probe; 1 = sequential)")
    p.add_argument("--disable-thinking", action="store_true",
                   help="send chat_template_kwargs.enable_thinking=false the way the "
                        "adapter does (reasoning_param=enable_thinking, reasoning off)")
    return p


def _load_inputs(args):
    golden_path, corpus_path = Path(args.golden), Path(args.corpus)
    for path in (golden_path, corpus_path):
        if not path.is_file():
            raise ConfigError(f"file not found: {path}")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    queries = golden.get("queries") if isinstance(golden, dict) else None
    if not queries or not all(isinstance(q, dict) and q.get("q") for q in queries):
        raise ConfigError(f"{golden_path} needs a non-empty 'queries' list of {{q, expect}}")
    entries = corpus.get("entries") if isinstance(corpus, dict) else None
    if not entries or not all(isinstance(e, dict) and e.get("id") for e in entries):
        raise ConfigError(f"{corpus_path} needs a non-empty 'entries' list with ids")
    secret = ""
    if args.api_key_file:
        key_path = Path(args.api_key_file).expanduser()
        if not key_path.is_file():
            raise ConfigError(f"api key file not found: {key_path}")
        secret = key_path.read_text(encoding="utf-8").strip()
    if args.repeat < 1 or args.concurrency < 1:
        raise ConfigError("--repeat and --concurrency must be at least 1")
    return golden, corpus, secret


def _seed_corpus(corpus):
    """Write the corpus into the throwaway database (same columns as run_eval)."""
    from app.db.connection import get_db_connection

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM questions")
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM synonyms")
        conn.executemany(
            "INSERT INTO dataset (id, title, text, video_url, title_en,"
            " text_en, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(e["id"], e.get("title", ""), e.get("text", ""), "",
              e.get("title_en", ""), e.get("text_en", ""), (i + 1) * 10)
             for i, e in enumerate(corpus["entries"])])
        conn.executemany(
            "INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, '')",
            [(q, e["id"]) for e in corpus["entries"] for q in e.get("questions", [])])
        conn.executemany(
            "INSERT INTO synonyms (source, target) VALUES (?, ?)",
            [(s, t) for s, t in corpus.get("synonyms", [])])
        conn.commit()
    finally:
        conn.close()


def _lang(text):
    return "fa" if any("؀" <= ch <= "ۿ" for ch in text) else "en"


# ── The run ─────────────────────────────────────────────────────────────

def _make_generate(adapter, rt, model, reasoning_override):
    """Stand-in for padyar_ai.generate: the wrapper's request, the real adapter."""
    from app.services.ai.errors import AIError
    from app.services.ai.request import AIMessage, AIRequest

    async def generate(messages, system_prompt="", task="chat", max_output_tokens=None,
                       temperature=None, top_p=None, reasoning="default",
                       response_format="text", timeout_s=None, metadata=None):
        req = AIRequest(
            task=task,
            messages=[m if isinstance(m, AIMessage) else AIMessage(role=m[0], content=m[1])
                      for m in messages],
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens or 0,
            temperature=temperature, top_p=top_p,
            reasoning=reasoning_override or reasoning,
            response_format=response_format,
            timeout_s=timeout_s, metadata=metadata or {})
        cap = _CAPTURE.get()
        if cap is not None:
            cap["request"] = {"temperature": temperature,
                              "max_output_tokens": max_output_tokens,
                              "response_format": response_format,
                              "timeout_s": timeout_s,
                              "reasoning": req.reasoning}
        t0 = time.perf_counter()
        try:
            resp = await adapter.invoke(rt, model, req)
        except AIError as e:
            if cap is not None:
                cap["error_code"] = e.code
                cap["http_status"] = e.status_code
            raise
        finally:
            if cap is not None:
                cap["latency_ms"] = (time.perf_counter() - t0) * 1000.0
        if cap is not None:
            cap["content"] = resp.content
            cap["finish_reason"] = resp.finish_reason
            cap["prompt_tokens"] = resp.tokens_input
            cap["completion_tokens"] = resp.tokens_output
        return resp

    return generate


def _make_adapter():
    from app.services.ai.adapters.openai_compatible import OpenAICompatibleAdapter

    class TimingsAdapter(OpenAICompatibleAdapter):
        async def http(self, rt, method, url, **kw):
            status, body, headers = await super().http(rt, method, url, **kw)
            cap = _CAPTURE.get()
            if cap is not None and isinstance(body, dict):
                t = body.get("timings")
                if isinstance(t, dict):
                    cap["server_timings"] = {k: t.get(k) for k in (
                        "prompt_n", "prompt_ms", "predicted_n", "predicted_ms",
                        "prompt_per_second", "predicted_per_second")}
            return status, body, headers

    return TimingsAdapter()


def _candidates(q):
    """The router's candidate list for one query (see `method`).

    Called for every query BEFORE the concurrent, timed part of a run: it is
    synchronous CPU work, and inside the event loop it would sit in a sibling
    request's latency window.
    """
    from app.config import ANSWER_TOPK
    from app.services import search
    from app.utils.normalizer import strip_leading_greeting

    core, only_greeting = strip_leading_greeting(q)
    match_query = q if only_greeting else core
    return [{**entry, "score": float(score)}
            for entry, score, _signals in search.find_top_matches(match_query, k=ANSWER_TOPK)]


async def _one(run, index, item, candidates, sem, say):
    from app.services.answer import select_records

    async with sem:
        q, expect = item["q"], item.get("expect")
        cand_ids = [str(c.get("id", "")) for c in candidates]
        cap = {}
        _CAPTURE.set(cap)
        decision = await select_records(q, candidates, [], _lang(q))
        _CAPTURE.set(None)

        sent = "latency_ms" in cap
        verdict = judge_reply(cap.get("content"), cap.get("finish_reason"), set(cand_ids))
        rec = {
            "run": run, "index": index, "q": q, "cat": item.get("cat"),
            "expect": expect, "lang": _lang(q),
            "candidate_ids": cand_ids,
            "candidate_scores": [round(c["score"], 4) for c in candidates],
            "expect_in_candidates": (expect in cand_ids) if expect is not None else None,
            "sent": sent, "skipped": not candidates,
            "latency_ms": round(cap["latency_ms"], 3) if sent else None,
            "error_code": cap.get("error_code"), "http_status": cap.get("http_status"),
            "finish_reason": cap.get("finish_reason"),
            "prompt_tokens": cap.get("prompt_tokens"),
            "completion_tokens": cap.get("completion_tokens"),
            "server_timings": cap.get("server_timings"),
            "request": cap.get("request"),
            "raw_content": (cap.get("content") or "")[:2000] if sent else None,
            **verdict,
            "decision_mode": decision["mode"] if decision else None,
            "decision_ids": list(decision["ids"]) if decision else None,
            "correct": is_correct(decision, expect),
        }
        say(f"run {run} #{index:02d} {'OK ' if rec['correct'] else 'BAD'} "
            f"json={int(rec['json_valid'])} grounded={int(rec['grounded'])} "
            f"mode={rec['decision_mode']} ms={rec['latency_ms']} err={rec['error_code']}")
        return rec


async def _bench(args, queries, secret, say):
    from app.services.ai import wrapper
    from app.services.ai.adapters.base import ProviderRuntime, aclose_shared_client

    config = {"base_url": args.base_url}
    if args.disable_thinking:
        config["reasoning_param"] = "enable_thinking"
    rt = ProviderRuntime(instance_id="bench", provider_type="openai_compatible",
                         display_name="bench", enabled=True, trust_class="internal",
                         config=config, secret=secret, timeout_s=45.0)
    generate = _make_generate(_make_adapter(), rt, args.model,
                              "off" if args.disable_thinking else None)
    had_attr = "generate" in vars(wrapper.padyar_ai)
    wrapper.padyar_ai.generate = generate
    records, per_run = [], []
    try:
        for run in range(1, args.repeat + 1):
            sem = asyncio.Semaphore(args.concurrency)
            prepared = [_candidates(item["q"]) for item in queries]
            t0 = time.perf_counter()
            recs = await asyncio.gather(*[_one(run, i, item, prepared[i], sem, say)
                                          for i, item in enumerate(queries)])
            wall = time.perf_counter() - t0
            recs = sorted(recs, key=lambda r: r["index"])
            per_run.append({"run": run, "wall_s": round(wall, 3),
                            "queries_per_s": round(len(recs) / wall, 3) if wall else None,
                            **summarise(recs)})
            records.extend(recs)
    finally:
        if not had_attr:
            del wrapper.padyar_ai.generate
        await aclose_shared_client()
    return records, per_run


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    secret = ""

    def scrub(text):
        if not secret:
            return text
        # The output is JSON: a key holding a quote, a backslash or a control
        # character is written escaped, and only the escaped form would match.
        for form in {secret, json.dumps(secret)[1:-1],
                     json.dumps(secret, ensure_ascii=False)[1:-1]}:
            text = text.replace(form, "[REDACTED]")
        return text

    def say(msg):
        print(scrub(msg), file=sys.stderr, flush=True)

    try:
        golden, corpus, secret = _load_inputs(args)
    except (ConfigError, ValueError) as e:
        print(f"bench_local_llm: {e}", file=sys.stderr)
        raise SystemExit(2)

    # Throwaway database, before and around every app import that reads it.
    # app/config.py resolves DB_PATH at import time (CLI run), and a test
    # process has already imported it (so the attributes are set too, and put
    # back afterwards).
    tmp = tempfile.mkdtemp(prefix="padyar-llmbench-")
    saved_env = {k: os.environ.get(k) for k in ("DB_PATH", "LOGS_DB_PATH")}
    os.environ["DB_PATH"] = os.path.join(tmp, "bench.db")
    os.environ["LOGS_DB_PATH"] = os.path.join(tmp, "application_logs.db")
    def restore_env():
        # A later subprocess in the same process tree must not inherit a path
        # into the throwaway folder, which is deleted below.
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    import app.config as config
    if str(getattr(config, "DB_BACKEND", "sqlite")).lower() != "sqlite":
        restore_env()
        shutil.rmtree(tmp, ignore_errors=True)
        print("bench_local_llm: the app is configured for "
              f"DB_BACKEND={config.DB_BACKEND!r}; this bench only runs on a "
              "throwaway SQLite database", file=sys.stderr)
        raise SystemExit(2)
    saved = {k: getattr(config, k) for k in ("DB_PATH", "LOGS_DB_PATH", "INTENT_MODEL_DIR")}
    config.DB_PATH = os.environ["DB_PATH"]
    config.LOGS_DB_PATH = os.environ["LOGS_DB_PATH"]
    # Empty = no intent-model artifact is read or written (app/services/intent.py),
    # so a bench over a test corpus can never touch the install's own model.
    config.INTENT_MODEL_DIR = ""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        from app.db.connection import init_db
        from app.db.queries import clear_settings_cache
        from app.services import embeddings, search

        clear_settings_cache()
        init_db()
        _seed_corpus(corpus)
        search.load_dataset_internal()
        if not search.dataset:
            print("bench_local_llm: the corpus produced an empty index", file=sys.stderr)
            raise SystemExit(2)
        say(f"bench: {len(golden['queries'])} queries x {args.repeat} run(s), "
            f"concurrency {args.concurrency}, model {args.model}")
        records, per_run = asyncio.run(_bench(args, golden["queries"], secret, say))
        retrieval = {"answer_topk": config.ANSWER_TOPK,
                     "rerank": bool(getattr(config, "RERANK_ENABLED", False)),
                     "embeddings": search.dataset_embedding_index is not None,
                     "embeddings_available": embeddings.available()}
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
        restore_env()
        clear_settings_cache()
        shutil.rmtree(tmp, ignore_errors=True)

    result = {
        "harness": "scripts/bench_local_llm.py",
        "method": METHOD,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url, "model": args.model,
        "api_key": "file" if secret else "none",
        "repeat": args.repeat, "concurrency": args.concurrency,
        "disable_thinking": args.disable_thinking,
        "golden": {"path": str(args.golden),
                   "dataset_version": golden.get("dataset_version"),
                   "n_queries": len(golden["queries"])},
        "corpus": {"path": str(args.corpus),
                   "corpus_version": corpus.get("corpus_version"),
                   "n_entries": len(corpus["entries"])},
        "retrieval": retrieval,
        "summary": summarise(records),
        "per_run": per_run,
        "records": records,
    }
    text = scrub(json.dumps(result, ensure_ascii=False, indent=2))
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    s = result["summary"]
    say(f"bench: n={s['n']} json_valid={s['json_valid']['rate']} "
        f"grounded={s['grounded']['rate']} correct={s['correct']['rate']} "
        f"p50={s['latency']['p50_ms']}ms p95={s['latency']['p95_ms']}ms "
        f"tok/s={s['tokens_per_second']['generation']} ({s['tokens_per_second']['basis']}) "
        f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
