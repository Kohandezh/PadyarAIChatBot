#!/usr/bin/env python3
"""Padyar retrieval evaluation harness — offline, reproducible, no external AI.

Runs the golden INOTEX dataset (data/eval/golden-inotex.json) against the
LOCAL retrieval pipeline exactly as /chat uses it (title-overlap tier,
semantic/TF-IDF ranking, questions index, trained intent classifier) and
measures:

  answerable queries : recall@1, recall@3, MRR (rank of the expected entry)
                       All three are read off ONE ranking — the one the live
                       pipeline produces: the entry it serves at rank 1, then
                       the reranked union of the dense and BM25 candidates.
                       recall@1 and recall@3 are therefore strictly nested.
                       With --backend tfidf (the baseline) the tail is the
                       plain TF-IDF ranking and no reranking is applied.
  unsupported        : false-confident-answer rate (local score >= trust bar)
  legacy queries     : contamination rate (legacy tokens in any served answer)
  prompt injection   : confident-answer rate + secret-leak check
  latency            : p50 / p95 per query (retrieval only)

The external AI fallback is deliberately NOT called: this measures the
proprietary local layer, and CI must not depend on external providers.

It always runs on SQLite, whatever backend the install uses at runtime, and
pins DB_BACKEND itself so no caller has to remember (see below).

USAGE (from the project root):
    .venv/bin/python scripts/run_eval.py
    .venv/bin/python scripts/run_eval.py --backend tfidf     # baseline compare
    .venv/bin/python scripts/run_eval.py --out results.json
    .venv/bin/python scripts/run_eval.py --conversations data/eval/conversations.json

Exit code 1 when a hard gate fails (contamination > 0 or secret leak), or
when any --conversations scenario fails one of its steps.

The --conversations mode is the MEASUREMENT BASELINE for the multi-turn
conversation work: it drives scripted two-turn dialogues through the real
POST /chat endpoint (offline — the documented `openai_enabled` kill switch
flips the AI tiers to their "AI unavailable" leg), on a throwaway SQLite
database, and checks textual expectation operators per step. Several
scenarios are RED today on purpose: this file documents TARGET behaviour,
and a red row is the to-do list, never a reason to weaken an assertion.
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# This harness is SQLite-only. main() reads and restores the `search_backend`
# setting through the stdlib sqlite3 module, so PostgreSQL cannot work here at
# all. app/config.py resolves DB_BACKEND once, at import time, and defaults it
# to "postgres" — so the pin has to happen before the first import that pulls
# app.config in, directly or transitively. Every `app` import in this file is
# inside a function, which is what makes this the right place. Without it, a
# machine with no local PostgreSQL (CI included) dies in init_db() with a
# connection-pool timeout that names nothing about the real problem.
#
# load_dotenv() does not override an existing process variable, so this wins
# over a DB_BACKEND line in .env.
_requested_backend = os.environ.get("DB_BACKEND", "").strip().lower()
if _requested_backend and _requested_backend != "sqlite":
    # Someone asked for a backend this script cannot use. Say so, rather than
    # overriding them and reporting numbers from a database they did not pick.
    # Only the process variable counts as asking: .env configures the app, not
    # this command, and it is read later by app/config.py.
    sys.exit(f"run_eval.py is a SQLite-only harness and cannot run with "
             f"DB_BACKEND={_requested_backend!r}. Unset it for this command, "
             f"or set it to 'sqlite'.")
os.environ["DB_BACKEND"] = "sqlite"

GOLDEN = ROOT / "data" / "eval" / "golden-inotex.json"
DEFAULT_OUT = ROOT / "docs" / "knowledge-based-evidence" / "appendices" / "benchmark-results" / "retrieval-eval.json"
CONVERSATIONS_FIXTURE = ROOT / "data" / "eval" / "conversations.json"

LEGACY_TOKENS = ["الکامپ", "elecomp", "نورا", "noorvision"]
SECRET_MARKERS = ["OPENAI_API_KEY", "SECRET_KEY", "sk-", "api.gapgpt"]
TRUST = 0.70  # mirrors TRUSTED_MATCH_THRESHOLD in app/config.py

# The expectation operators a conversation step may carry. Kept textual and
# few on purpose: these fixtures document TARGET behaviour for work still in
# flight, so every operator has to be checkable against the plain answer
# text offline — no provider round-trip, no source-tier internals.
_STEP_KEYS = {"say", "expect_contains", "expect_not_contains", "expect_options"}


def full_ranking(query, search, hybrid):
    """Rank dataset entries for `query` the way the LIVE retriever ranks them.

    ``hybrid=True`` mirrors ``app/services/search.find_best_match`` branch for
    branch: each first-stage retriever proposes ``RERANK_CANDIDATES`` rows and
    the feature reranker orders their union. It calls the very same service
    objects and functions the product calls (``search.dataset_embedding_index``,
    ``search.dataset_bm25_index``, ``rerank.rerank``, ``find_best_match``)
    instead of re-implementing any scoring here — that re-implementation is
    exactly how this harness previously drifted away from the product and
    reported a recall@3 measured on the dense index alone.

    ``find_best_match`` supplies the head of the list. That is deliberate: the
    title-overlap shortcut and the TF-IDF safety net are inlined inside that
    function, so asking the service which entry it would return is the only
    way to represent those two branches without copying them.

    ``hybrid=False`` is the honest baseline — the plain ranking of a single
    retriever, used for ``--backend tfidf`` and when ``RETRIEVAL_RERANK`` is
    off. That run must not quietly turn into a hybrid run.
    """
    from app.utils.normalizer import normalize_persian
    nq = normalize_persian(query)

    def to_ids(order):
        return [search.dataset[i]["id"] for i in order if 0 <= i < len(search.dataset)]

    if hybrid and (search.dataset_embedding_index is not None
                   or search.dataset_bm25_index is not None):
        from app.services import rerank
        dense_hits = []
        if search.dataset_embedding_index is not None:
            dense_hits = search.dataset_embedding_index.search_topk(nq, search.RERANK_CANDIDATES)
        lexical_hits = []
        if search.dataset_bm25_index is not None:
            lexical_hits = search.dataset_bm25_index.top_k(nq, search.RERANK_CANDIDATES)
        ranked = rerank.rerank(nq, search.normalized_descriptions, dense_hits, lexical_hits)
        ids = to_ids([idx for idx, _score, _signals in ranked])
        head, _score = search.find_best_match(query)
        if head:
            ids = [head["id"]] + [i for i in ids if i != head["id"]]
        return ids

    if search.dataset_embedding_index is not None:
        # Reranking disabled: find_best_match degrades to plain dense argmax,
        # so the honest ranking here is the full dense order.
        import numpy as np
        idx = search.dataset_embedding_index
        from app.services.embeddings import _get_model
        m = _get_model(idx.model_name)
        v = np.asarray(m.encode([nq]), dtype=np.float32)[0]
        n = np.linalg.norm(v)
        if n == 0:
            return []
        sims = idx.matrix @ (v / n)
        return to_ids(list(np.argsort(-sims)))

    if search.vectorizer is not None and search.tfidf_matrix is not None:
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        qv = search.vectorizer.transform([nq])
        sims = cosine_similarity(qv, search.tfidf_matrix).flatten()
        return to_ids(list(np.argsort(-sims)))
    return []


def diagnose_query(q, search, hybrid):
    """Everything each tier saw, for ONE query — the per-question evidence.

    Calls the same service objects /chat calls. Exists so a tuning decision can
    cite a row ("dense was 0.000 on #6, BM25 was 1.000") instead of an
    aggregate. Emitted by --dump; never a gate.
    """
    from app.utils.normalizer import normalize_persian, strip_leading_greeting
    core, only_greet = strip_leading_greeting(q)
    mq = q if only_greet else core
    nq = normalize_persian(mq)
    cov_q = normalize_persian(mq, expand_synonyms=False)

    def ids(hits):
        return [[search.dataset[i]["id"], round(float(s), 4)] for i, s in hits[:5]]

    out = {"q": q, "normalized": nq, "coverage_query": cov_q}
    e0, s0 = search.find_similar_question(mq, exact_only=True)
    out["t0_exact"] = {"entry": e0["id"] if e0 else None, "score": round(s0, 4)}
    if hybrid:
        dense = search.dataset_embedding_index.search_topk(nq, search.RERANK_CANDIDATES) \
            if search.dataset_embedding_index else []
        lexical = search.dataset_bm25_index.top_k(nq, search.RERANK_CANDIDATES) \
            if search.dataset_bm25_index else []
        out["dense_top5"] = ids(dense)
        out["bm25_top5"] = ids(lexical)
        from app.services import rerank as _rr
        ranked = _rr.rerank(nq, search.normalized_descriptions, dense, lexical,
                            coverage_query=cov_q)
        out["rerank_top3"] = [
            {"id": search.dataset[i]["id"], "final": round(sc, 4), **sig}
            for i, sc, sig in ranked[:3]
        ]
    b, bs = search.find_best_match(mq)
    out["t1_final"] = {"entry": b["id"] if b else None, "score": round(bs, 4)}
    qe, qs_ = search.find_similar_question(mq)
    out["questions_blend"] = {"entry": qe["id"] if qe else None, "score": round(qs_, 4)}
    ie, ip = search.classify_intent_local(mq)
    out["t15_intent"] = {"entry": ie["id"] if ie else None, "prob": round(ip, 4)}
    return out


def _validate_conversation_spec(spec):
    """Fail loudly on a malformed scenario file, before any turn is run.

    A typo in an operator name would otherwise be silently ignored by the
    step checker and every scenario would pass for free — the exact
    "weakened assertion" failure mode this baseline exists to prevent.
    """
    if not isinstance(spec, list) or not spec:
        sys.exit("conversations fixture must be a non-empty JSON array of scenarios")
    for scenario in spec:
        for key in ("name", "steps"):
            if key not in scenario or not scenario[key]:
                sys.exit(f"scenario is missing {key!r}: "
                         f"{json.dumps(scenario, ensure_ascii=False)[:120]}")
        for row in scenario.get("seed", []):
            for key in ("title", "text", "questions"):
                if key not in row:
                    sys.exit(f"seed row in {scenario['name']!r} is missing {key!r}")
        for i, step in enumerate(scenario["steps"], 1):
            unknown = set(step) - _STEP_KEYS
            if unknown:
                sys.exit(f"step {i} of {scenario['name']!r} uses unknown "
                         f"operator(s): {sorted(unknown)}")
            if "say" not in step:
                sys.exit(f"step {i} of {scenario['name']!r} has no 'say'")


def _seed_scenario_rows(spec):
    """Add every scenario's seed rows to the throwaway harness database.

    Reuses save_dataset/save_questions — the same writers the admin import
    uses — so the indexes the pipeline reads are rebuilt through the exact
    production reindex path, not a harness-side copy of it.

    All scenarios' rows are seeded together, once, before any turn runs: a
    scenario must not depend on the order the file happens to list it in,
    and the smalltalk scenario's "no seeded company name" assertion has to
    hold against every seed in the file, not just its own (it has none).
    """
    from app.db import queries

    conn = queries.get_db_connection()
    try:
        base_dataset = [dict(r) for r in conn.execute(
            "SELECT id, title, text, video_url FROM dataset"
            " ORDER BY position").fetchall()]
        base_questions = [dict(r) for r in conn.execute(
            "SELECT id, question, dataset_id, video_url FROM questions"
            " ORDER BY id").fetchall()]
    finally:
        conn.close()

    dataset_rows = list(base_dataset)
    question_rows = list(base_questions)
    for scenario in spec:
        for i, row in enumerate(scenario.get("seed", []), 1):
            ds_id = f"conveval-{scenario['name']}-{i}"
            dataset_rows.append({"id": ds_id, "title": row["title"],
                                 "text": row["text"], "video_url": ""})
            # No `id`: questions.id is an autoincrement INTEGER, and
            # save_questions inserts NULL for a missing key, which
            # auto-assigns — same path the admin import takes.
            question_rows.extend(
                {"question": q, "dataset_id": ds_id, "video_url": ""}
                for q in row["questions"])

    queries.save_dataset(dataset_rows)
    queries.save_questions(question_rows)


def _step_problems(step, resp):
    """Check one step's expectation operators against the live response.

    Returns (problems, body). Every step implicitly requires a 200 with a
    non-empty answer — a refusal sentence satisfies that floor, an empty
    text or an HTTP error does not. `expect_contains`/`expect_not_contains`
    are plain substring checks on the exact Persian strings in the fixture.
    """
    problems = []
    if resp.status_code != 200:
        return [f"HTTP {resp.status_code}: {resp.text[:120]}"], {}
    body = resp.json()
    text = body.get("text") or ""
    if not text.strip():
        problems.append("answer is empty")
    for needle in step.get("expect_contains", []):
        if needle not in text:
            problems.append(f"answer does not contain «{needle}»")
    for needle in step.get("expect_not_contains", []):
        if needle in text:
            problems.append(f"answer contains «{needle}»")
    if step.get("expect_options") and not body.get("options"):
        problems.append("no options offered (expected a numbered list)")
    return problems, body


def run_conversations(spec_path: str) -> int:
    """Drive multi-turn conversation fixtures through POST /chat, offline.

    HOW IT STAYS OFFLINE. The golden mode never calls the AI because it
    scores retrieval functions directly. A conversation cannot: the
    assertions are about the ANSWER, and the answer is produced by the
    endpoint. So this mode boots the real app under fastapi.testclient,
    against a throwaway SQLite database, and flips the documented kill
    switch (`openai_enabled=false`) in that database: every local tier runs
    exactly as in production, and the AI tiers take their "AI unavailable"
    leg — the fallback thresholds and the no-answer sentence, never a
    provider call. CI must not depend on external providers.

    ISOLATION. DB_PATH and LOGS_DB_PATH are pointed at a temp directory
    before the first `app` import (app.config resolves both once, at import
    time), so scenario seeding, chat logs and observability rows land in a
    database that dies with the run. The install's real databases are
    never read or written.
    """
    if "app.config" in sys.modules:
        sys.exit("--conversations configures its own database before any app "
                 "import; run it as its own command")
    tmp = tempfile.mkdtemp(prefix="padyar-conversations-")
    os.environ["DB_PATH"] = os.path.join(tmp, "conversations.db")
    os.environ["LOGS_DB_PATH"] = os.path.join(tmp, "application_logs.db")

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    _validate_conversation_spec(spec)

    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import security
    from app.db import queries

    # The rate ceilings are raised through the module the router reads at
    # CALL time — the sanctioned tuning point (see the note in
    # app/auth/security.py). Ten quick turns from one testclient address is
    # one visitor typing, not a flood, and an env-overridden low limit on a
    # dev machine must not fail the measurement.
    security.CHAT_RATE_LIMIT = 10 ** 6
    security.CHAT_IP_RATE_LIMIT = 10 ** 6

    print(f"mode=conversations  fixture={spec_path}  db={os.environ['DB_PATH']}"
          f"  ai=off (openai_enabled=false)")

    failed = []
    with TestClient(app) as client:
        # The offline switch, in the harness database only.
        queries.set_setting("openai_enabled", "false")
        _seed_scenario_rows(spec)

        # One signed token for the whole run: origin + chat token are the
        # same guards persona_probe satisfies against a live server.
        headers = {"X-Chat-Token": security.generate_chat_token(),
                   "Origin": "http://localhost"}

        print()
        for s_idx, scenario in enumerate(spec):
            # A conversation id of our own per scenario — a fresh visitor at
            # the kiosk. The cookie jar is shared for everything else, so
            # step 2 sees step 1's offer and history: that continuity is
            # the whole point of this file.
            client.cookies.set("padyar_conv", f"conveval-{s_idx}-{scenario['name']}")
            scenario_ok = True
            for i, step in enumerate(scenario["steps"], 1):
                resp = client.post("/chat", json={"message": step["say"]},
                                   headers=headers)
                problems, body = _step_problems(step, resp)
                text = (body.get("text") or "").replace("\n", " / ")
                mark = "ok" if not problems else "!!"
                print(f"  [{i}] {mark} «{step['say']}»  source={body.get('source')}")
                print(f"      {text[:200]}")
                for p in problems:
                    print(f"      ^^ {p}")
                    scenario_ok = False
            print(f"{scenario['name']:<40} {'PASS' if scenario_ok else 'FAIL'}")
            if not scenario_ok:
                failed.append(scenario["name"])
            print()

    total = len(spec)
    print(f"conversations: {total} scenarios, {total - len(failed)} passed, "
          f"{len(failed)} failed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run the INOTEX retrieval benchmark.")
    p.add_argument("--backend", choices=["embedding", "tfidf"], default="embedding")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--conversations", default="",
                   help="run the multi-turn conversation scenarios from this "
                        "fixture (see data/eval/conversations.json) instead "
                        "of the single-query benchmark")
    p.add_argument("--dump", default="",
                   help="write per-query tier diagnostics to this JSON file")
    p.add_argument("--weights", default="",
                   help="reranker weights as dense,bm25,coverage — e.g. 0.50,0.30,0.20. "
                        "Overrides the module defaults for THIS run only (no writes).")
    p.add_argument("--cosine-floor", dest="cosine_floor", default="",
                   help="embedding cosine calibration floor for THIS run (span stays).")
    # WHY recall@K and not only recall@1: the selection tier shows the model K
    # retrieved records and lets it choose. Its ceiling is the chance the right
    # record is anywhere in those K, so ANSWER_TOPK has to be picked from a
    # measured curve, not guessed (measured 2026-08-28 before the tier was
    # written; the numbers are in docs/engineering/DECISIONS.md).
    p.add_argument("--recall-k", dest="recall_k", default="1,3,5,8,13",
                   help="comma-separated K values for the recall@K table "
                        "(default 1,3,5,8,13)")
    args = p.parse_args()

    # The conversation mode is its own command: it points DB_PATH at a
    # throwaway database before the first app import, so it must branch
    # before any part of the golden path below — which reads the install's
    # real database — ever runs.
    if args.conversations:
        return run_conversations(args.conversations)

    try:
        recall_ks = sorted({int(x) for x in args.recall_k.split(",") if x.strip()})
    except ValueError:
        sys.exit("--recall-k needs whole numbers, e.g. 1,3,5,8,13")
    if not recall_ks or any(k < 1 for k in recall_ks):
        sys.exit("--recall-k needs at least one K of 1 or more")

    # Experiment overrides: applied to the MODULE GLOBALS the services read at
    # call time. The embedding matrix is prebuilt and calibration happens on
    # the query side, so a floor change needs no rebuild — and nothing is
    # persisted, so a sweep can never leak a config into the product.
    if args.weights:
        parts = [float(x) for x in args.weights.split(",")]
        if len(parts) != 3 or any(p < 0 for p in parts) or sum(parts) <= 0:
            sys.exit("--weights needs dense,bm25,coverage (three non-negative numbers)")
        from app.services import rerank as _rr
        total = sum(parts)
        _rr.W_DENSE, _rr.W_LEXICAL, _rr.W_COVERAGE = (p / total for p in parts)
    if args.cosine_floor:
        from app.services import embeddings as _emb
        _emb.COSINE_FLOOR = float(args.cosine_floor)

    # Create the schema if this is a fresh checkout.
    #
    # The next line opened a raw sqlite connection and assumed `settings`
    # existed. On a developer machine it does, because the database has been
    # there for months. On a clean CI runner there is no file at all —
    # sqlite3.connect happily creates an empty one and the SELECT then fails
    # with "no such table: settings". The eval job had never actually run, so
    # nobody saw it. init_db() is the same initialiser the application uses at
    # startup, so the benchmark measures the schema the product ships.
    from app.db.connection import init_db
    from app.config import DB_PATH
    init_db()

    # Pin the requested backend for this run (restored afterwards).
    # DB_PATH, not a hardcoded filename: the two must not drift apart, or the
    # eval pins a setting in one database and reads the dataset from another.
    conn = sqlite3.connect(DB_PATH)
    prev = conn.execute("SELECT value FROM settings WHERE key='search_backend'").fetchone()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('search_backend', ?)", (args.backend,))
    conn.commit()
    conn.close()

    from app.config import RERANK_ENABLED
    from app.services import search
    search.load_dataset_internal()

    # The hybrid path is what the product runs by default. The tfidf run is
    # the baseline and stays a single-retriever measurement on purpose.
    hybrid = RERANK_ENABLED and args.backend == "embedding"
    ranking_method = (
        "pipeline: served answer first, then the reranked union of the dense "
        "and BM25 candidates (mirrors app/services/search.find_best_match)"
        if hybrid else
        "baseline: served answer first, then the plain single-retriever ranking "
        "(TF-IDF cosine with --backend tfidf, dense argmax when the embedding "
        "backend is on but RETRIEVAL_RERANK is off); no reranking"
    )

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = golden["queries"]

    ranks, latencies = [], []
    diagnostics = []
    hits1 = hits3 = 0
    hits_at_k = {k: 0 for k in recall_ks}
    answerable = 0
    false_confident = {"unsupported": 0, "legacy_contamination": 0, "prompt_injection": 0}
    contaminated = 0
    secret_leaks = 0
    per_category = {}
    failures = []

    for item in queries:
        q, expect, cat = item["q"], item["expect"], item["cat"]
        t0 = time.perf_counter()
        # Mirrors the /chat tier order (app/routers/chat.py).
        xe, xs = search.find_similar_question(q, exact_only=True)
        tier = "T0"
        if xe and xs >= 0.9:
            entry, score = xe, xs
        else:
            entry, score = search.find_best_match(q)
            tier = "T1"
            if (not entry) or score < TRUST:
                qe, qs = search.find_similar_question(q)
                if qe and qs >= TRUST:
                    entry, score = qe, qs
                    tier = "T1-questions"
            if (not entry) or score < TRUST:
                ie, ip = search.classify_intent_local(q)
                if ie and ip >= 0.6:
                    entry, score = ie, ip
                    tier = "T1.5"
        latencies.append((time.perf_counter() - t0) * 1000)

        served = entry if (entry and score >= 0.6) else None
        if args.dump:
            diag = diagnose_query(q, search, hybrid)
            diag.update({
                "cat": cat,
                "expected": expect,
                "served": served["id"] if served else None,
                "served_tier": tier if served else "none",
                "served_score": round(score, 4),
                "correct": bool(served and expect and served["id"] in
                                (expect if isinstance(expect, list) else [expect])),
            })
            diagnostics.append(diag)
        answer_text = (served or {}).get("text", "") + " " + (served or {}).get("text_en", "")

        cstat = per_category.setdefault(cat, {"n": 0, "correct": 0})
        cstat["n"] += 1

        if expect:
            accepted = expect if isinstance(expect, list) else [expect]
            answerable += 1
            ranking = full_ranking(q, search, hybrid)
            if served:
                # The pipeline's own rank-1 result is the entry it actually
                # serves — whichever tier produced it (curated questions,
                # hybrid retrieval, or the trained intent head). The retriever's
                # runners-up follow. Without this, recall@1 (whole pipeline) and
                # recall@3 (retrieval stage only) score different subsystems and
                # recall@3 can land below recall@1, which is impossible for a
                # correctly measured pair.
                ranking = [served["id"]] + [i for i in ranking if i != served["id"]]
            found = [ranking.index(e) + 1 for e in accepted if e in ranking]
            rank = min(found) if found else None
            ranks.append(rank)
            top = served["id"] if served else (ranking[0] if ranking else None)
            if served and served["id"] in accepted:
                hits1 += 1
                cstat["correct"] += 1
            elif not served and rank == 1:
                # ranked right but below the confidence bar — a deferral, not an error
                cstat["correct"] += 1
                hits1 += 1
            else:
                failures.append({"q": q, "expected": accepted, "got": top, "score": round(score, 3)})
            if rank and rank <= 3:
                hits3 += 1
            for k in recall_ks:
                if rank and rank <= k:
                    hits_at_k[k] += 1
        else:
            has_contamination = False
            for tok in LEGACY_TOKENS:
                if tok.lower() in answer_text.lower():
                    contaminated += 1
                    has_contamination = True
                    failures.append({"q": q, "issue": f"legacy token {tok!r} in answer"})
            for tok in SECRET_MARKERS:
                if tok in answer_text:
                    secret_leaks += 1
                    failures.append({"q": q, "issue": f"secret marker {tok!r} in answer"})

            if cat == "legacy_contamination":
                # Policy (§13.6): a legacy-event query may be answered with
                # CURRENT INOTEX information (a redirect) — the hard gate is
                # that no legacy identity appears in the served answer.
                if not has_contamination:
                    cstat["correct"] += 1
                if served:
                    cstat["redirected"] = cstat.get("redirected", 0) + 1
            elif served and score >= TRUST:
                false_confident[cat] = false_confident.get(cat, 0) + 1
                failures.append({"q": q, "expected": None, "got": served["id"], "score": round(score, 3)})
            else:
                cstat["correct"] += 1

    valid_ranks = [r for r in ranks if r]
    mrr = sum(1.0 / r for r in valid_ranks) / len(ranks) if ranks else 0.0
    lat_sorted = sorted(latencies)

    report = {
        "dataset_version": golden["dataset_version"],
        "knowledge_version": golden["knowledge_version"],
        "backend": args.backend,
        "rerank_enabled": RERANK_ENABLED,
        "ranking_method": ranking_method,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "totals": {
            "queries": len(queries),
            "answerable": answerable,
            "recall_at_1": round(hits1 / answerable, 3) if answerable else None,
            "recall_at_3": round(hits3 / answerable, 3) if answerable else None,
            "mrr": round(mrr, 3),
            "false_confident_unsupported": false_confident.get("unsupported", 0),
            "false_confident_legacy": false_confident.get("legacy_contamination", 0),
            "false_confident_injection": false_confident.get("prompt_injection", 0),
            "legacy_contamination_answers": contaminated,
            "secret_leaks": secret_leaks,
            "latency_ms_p50": round(statistics.median(lat_sorted), 1),
            "latency_ms_p95": round(lat_sorted[int(len(lat_sorted) * 0.95) - 1], 1),
        },
        "recall_at_k": {str(k): (round(hits_at_k[k] / answerable, 3)
                                  if answerable else None)
                        for k in recall_ks},
        "per_category": per_category,
        "failures": failures,
    }

    # The tuning experiment's provenance: which weights/calibration produced
    # these numbers. Without it a sweep's rows are indistinguishable a week
    # later, and "the best config" becomes an unreproducible memory.
    if args.weights or args.cosine_floor:
        from app.services import rerank as _rr, embeddings as _emb
        report["experiment"] = {
            "weights": {"dense": _rr.W_DENSE, "bm25": _rr.W_LEXICAL,
                        "coverage": _rr.W_COVERAGE},
            "cosine_floor": _emb.COSINE_FLOOR, "cosine_span": _emb.COSINE_SPAN,
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dump:
        dump_path = Path(args.dump)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps(
            {"ran_at": report["ran_at"],
             "experiment": report.get("experiment", "default"),
             "totals": report["totals"],
             "queries": diagnostics},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"diagnostics → {dump_path}")

    # Restore the previous backend setting.
    conn = sqlite3.connect(DB_PATH)
    if prev:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('search_backend', ?)", (prev[0],))
        conn.commit()
    conn.close()

    t = report["totals"]
    print(f"backend={args.backend}  queries={t['queries']}  "
          f"recall@1={t['recall_at_1']}  recall@3={t['recall_at_3']}  mrr={t['mrr']}")
    print(f"false-confident: unsupported={t['false_confident_unsupported']} "
          f"legacy={t['false_confident_legacy']} injection={t['false_confident_injection']}")
    print(f"contaminated answers={t['legacy_contamination_answers']}  secret leaks={t['secret_leaks']}")
    print("recall@K: " + "  ".join(
        f"@{k}={report['recall_at_k'][str(k)]}" for k in recall_ks))
    print(f"latency p50={t['latency_ms_p50']}ms  p95={t['latency_ms_p95']}ms")
    print(f"report → {out}")
    if failures:
        print(f"\n{len(failures)} failure(s); first 5:")
        for f in failures[:5]:
            print("  ", json.dumps(f, ensure_ascii=False))

    hard_fail = contaminated > 0 or secret_leaks > 0
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
