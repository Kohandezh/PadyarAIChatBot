#!/usr/bin/env python3
"""Padyar retrieval evaluation harness: offline, reproducible, no external AI.

Runs a golden dataset (passed with --golden) against the LOCAL answer layer
and measures it in one of five modes (--mode). The design and every metric
definition live in docs/features/eval-benchmark/SPEC.md; the measured numbers
live in docs/features/eval-benchmark/RESULTS.md.

  bm25            the BM25 index alone (the lexical input the reranker gets)
  dense           the model2vec dense index alone (the dense input it gets)
  hybrid          search.find_top_matches: the reranked union of both
  full            the real POST /chat, AI disabled: every local tier the
                  router runs, at each tier's own bar. The served record is
                  ranked first, then the hybrid ranking.
  full_no_intent  full with the trained intent head (Tier 1.5) removed

Every mode writes recall_at_1, mrr and recall_at_k. recall_at_1 is a RANKING
metric. The two serving modes also write answered_at_1 (what a visitor
actually receives) and the refusal metrics.

Nothing here re-implements a score. Each mode calls the function the product
calls; `full` calls the router itself. A copy of the tier walk drifted from
the router before (it served Tier 1 at 0.6 where the router needs 0.70), and
a copy of find_best_match drifted from the retriever. Neither can happen to
a harness that owns no copy.

The external AI fallback is deliberately NOT called: this measures the
proprietary local layer, and CI must not depend on external providers.

It always runs on SQLite, whatever backend the install uses at runtime, and
pins DB_BACKEND itself so no caller has to remember (see below).

A golden file NAMES ITS CORPUS (`"corpus": "corpus.json"`, resolved next to
the golden file) and the harness seeds that corpus into a THROWAWAY SQLite
database of its own. It never reads or writes the install's database: the
numbers must not depend on which machine ran them, and seeding replaces the
dataset/questions/synonyms tables wholesale, which against a real install
would be a knowledge-base wipe.

USAGE (from the project root):
    .venv/bin/python scripts/run_eval.py --golden data/eval/golden.json
    .venv/bin/python scripts/run_eval.py --golden data/eval/golden.json \\
        --mode hybrid --check data/eval/floors.json --out results.json
    .venv/bin/python scripts/run_eval.py --conversations data/eval/conversations.json

The report goes to a temp file unless --out says otherwise: it is a run
artifact, not a fixture, and it must not land in the tracked data/eval/ tree.

EXIT CODES
    0  measured, and every gate held
    1  a gate failed: contamination, a secret leak, or (with --check) a floor,
       a ceiling or an enforced control. Also a failed --conversations step.
    2  not a measurement: a configuration or usage error (bad golden file,
       floors file missing, mode missing from it, unknown metric, ...)
Keeping 1 and 2 apart is what lets a test prove a failure came from the
floor comparison and not from a typo.

The --conversations mode is the MEASUREMENT BASELINE for the multi-turn
conversation work: it drives scripted two-turn dialogues through the real
POST /chat endpoint (offline: the documented `openai_enabled` kill switch
flips the AI tiers to their "AI unavailable" leg), on a throwaway SQLite
database, and checks textual expectation operators per step. Several
scenarios are RED today on purpose: this file documents TARGET behaviour,
and a red row is the to-do list, never a reason to weaken an assertion.
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# This harness is SQLite-only, whatever backend the install uses at runtime.
# app/config.py resolves DB_BACKEND once, at import time, and defaults it
# to "postgres", so the pin has to happen before the first import that pulls
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
    print(f"run_eval.py is a SQLite-only harness and cannot run with "
          f"DB_BACKEND={_requested_backend!r}. Unset it for this command, "
          f"or set it to 'sqlite'.", file=sys.stderr)
    sys.exit(2)
os.environ["DB_BACKEND"] = "sqlite"

# The default report lands OUTSIDE the repository. It used to default to
# data/eval/retrieval-eval.json, which drops an untracked file into a tracked
# fixture directory on every single run: `git status` is dirty after a
# measurement, and one `git add data/eval` commits a machine-specific result
# as if it were a fixture. A report is a run artifact, not source. CI passes
# --out explicitly and uploads that file.
DEFAULT_OUT = Path(tempfile.gettempdir()) / "padyar-eval" / "retrieval-eval.json"
CONVERSATIONS_FIXTURE = ROOT / "data" / "eval" / "conversations.json"

# The DEFAULTS of the contamination and secret-leak gates. A golden file may
# declare its own `legacy_tokens` / `secret_markers`, and those are MERGED
# into these (union), never substituted for them: a golden file that swapped
# the list out would quietly narrow the gate, which is how 420eb1e's golden
# stopped checking the retired brand names below. Read when main() runs, so a
# test can extend them. They are why .github/workflows/ci.yml allowlists this
# file in the retired-brand guard.
LEGACY_TOKENS = ["الکامپ", "elecomp", "نورا", "noorvision"]
SECRET_MARKERS = ["OPENAI_API_KEY", "SECRET_KEY", "sk-", "api.gapgpt"]

MODES = ("full", "full_no_intent", "hybrid", "dense", "bm25")
SERVING_MODES = ("full", "full_no_intent")

# Judged by the contamination gate ONLY (ruling R6): a question about a
# retired event may be answered with the current event's facts, a
# "redirect". It is kept out of every refusal metric, numerator and
# denominator, and counted on its own as legacy_redirected / legacy_total.
LEGACY_CATEGORY = "legacy_contamination"

# What each named allow-control requires. A golden file may only name these.
CONTROL_RULES = {
    "paraphrase_above_trust":
        "answered with an accepted id, confidence >= TRUSTED_MATCH_THRESHOLD",
    "benign_lookalike_answered": "answered with an accepted id",
}
CONTROL_STATUSES = ("enforced", "known_gap")

METRIC_NOTES = {
    "recall_at_1": "RANKING metric: share of answerable queries whose accepted "
                   "id is ranked first. In the serving modes the ranking starts "
                   "with the answered record; a refused query still counts here "
                   "when its ranking was right. Read it next to answered_at_1.",
    "recall_at_k": "RANKING metric: share of answerable queries with an "
                   "accepted id in the top K.",
    "mrr": "RANKING metric: mean of 1/rank over answerable queries, 0 when absent.",
    "answered_at_1": "Answerable queries the router answered from a local tier "
                     "with an accepted id, over answerable queries. What a "
                     "visitor receives.",
    "out_of_scope_refusal_rate": "Deny queries (no expected id, legacy "
                                 "excluded) NOT answered, over deny queries.",
    "refusal_precision": "Of all non-legacy queries not answered, the share "
                         "that were deny queries. null when nothing was refused.",
    "false_confident_total": "Deny queries (legacy excluded) the router answered.",
    "latency_ms": "Machine-specific. Never gated, never a benchmark result.",
}


class ConfigError(Exception):
    """Not a measurement: the inputs are wrong. Exit code 2."""


def config_error(message: str):
    print(message, file=sys.stderr)
    sys.exit(2)


# The expectation operators a conversation step may carry. Kept textual and
# few on purpose: these fixtures document TARGET behaviour for work still in
# flight, so every operator has to be checkable against the plain answer
# text offline — no provider round-trip, no source-tier internals.
_STEP_KEYS = {"say", "expect_contains", "expect_not_contains", "expect_options"}


def merge_tokens(defaults, declared):
    """The defaults plus whatever the golden file adds, in that order, no repeats."""
    out = []
    for tok in list(defaults) + list(declared or []):
        if tok and tok not in out:
            out.append(tok)
    return out


def query_lang(q: str) -> str:
    """`en` for a query with Latin letters and no Persian ones, else `fa`.

    The golden file carries no language field; this rule is deterministic and
    matches what a kiosk visitor does (the English UI sends English text).
    """
    if re.search("[A-Za-z]", q) and not re.search("[؀-ۿ]", q):
        return "en"
    return "fa"


def match_query(q: str) -> str:
    """The query the router actually matches on (app/routers/chat.py).

    A leading greeting is stripped, unless the message is only a greeting.
    """
    from app.utils.normalizer import strip_leading_greeting
    core, only_greeting = strip_leading_greeting(q)
    return q if only_greeting else core


def ranking_for(mode: str, mq: str, search, depth: int):
    """(ranked ids, title_overlap_head fired) for one query in a ranking mode.

    bm25/dense call search._dual_hits exactly as find_top_matches does, so
    each arm is the input the reranker receives, on both query forms.
    """
    from app.utils.normalizer import normalize_persian

    if mode == "hybrid" or mode in SERVING_MODES:
        ranked = search.find_top_matches(mq, k=depth)
        fired = bool(ranked) and "title_overlap" in ranked[0][2]
        return [entry["id"] for entry, _score, _sig in ranked], fired
    normalized = normalize_persian(mq)
    coverage = normalize_persian(mq, expand_synonyms=False)
    index = (search.dataset_bm25_index if mode == "bm25"
             else search.dataset_embedding_index)
    retrieve = index.top_k if mode == "bm25" else index.search_topk
    hits = search._dual_hits(retrieve, coverage, normalized, depth)
    ids = [search.dataset[i]["id"] for i, _s in hits if 0 <= i < len(search.dataset)]
    return ids, False


def diagnose_query(mq, search):
    """Everything each retrieval stage saw, for ONE query: per-question evidence.

    Calls the same service objects /chat calls. Exists so a tuning decision can
    cite a row ("dense was 0.000 on #6, BM25 was 1.000") instead of an
    aggregate. Emitted by --dump; never a gate.
    """
    from app.utils.normalizer import normalize_persian
    from app.services import rerank as _rr
    nq = normalize_persian(mq)
    cov_q = normalize_persian(mq, expand_synonyms=False)

    def ids(hits):
        return [[search.dataset[i]["id"], round(float(s), 4)] for i, s in hits[:5]]

    out = {"normalized": nq, "coverage_query": cov_q}
    e0, s0 = search.find_similar_question(mq, exact_only=True)
    out["t0_exact"] = {"entry": e0["id"] if e0 else None, "score": round(s0, 4)}
    k = search.RERANK_CANDIDATES
    dense = (search._dual_hits(search.dataset_embedding_index.search_topk, cov_q, nq, k)
             if search.dataset_embedding_index else [])
    lexical = (search._dual_hits(search.dataset_bm25_index.top_k, cov_q, nq, k)
               if search.dataset_bm25_index else [])
    out["dense_top5"] = ids(dense)
    out["bm25_top5"] = ids(lexical)
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


def load_corpus(golden_path: Path, golden: dict):
    """Read and validate the corpus fixture the golden file names.

    WHY THE GOLDEN FILE CARRIES ITS CORPUS. Until this existed, the golden
    mode measured whatever happened to be in the install's own database.
    On the machine that published `recall@1 = 0.786` that was a live event
    knowledge base; on a clean checkout it is nothing at all, because
    app/default_content.py ships empty on purpose. Same command, same golden
    file, a different number on every machine — and no third party could
    reproduce any of it. A golden set is only a measurement when the corpus
    it was written against travels with it, so `corpus` is required rather
    than optional: a golden file without one cannot be reproduced and this
    script refuses to pretend otherwise.

    The path is resolved relative to the golden file, so the pair moves
    together.
    """
    name = golden.get("corpus")
    if not name:
        config_error(f"{golden_path} has no 'corpus' key. A golden set must name "
                 f"the corpus fixture it was written against, e.g. "
                 f'"corpus": "corpus.json" (resolved next to the golden file).')
    path = Path(name)
    if not path.is_absolute():
        path = golden_path.parent / path
    if not path.is_file():
        config_error(f"corpus fixture not found: {path} (named by {golden_path})")

    corpus = json.loads(path.read_text(encoding="utf-8"))
    entries = corpus.get("entries")
    if not isinstance(entries, list) or not entries:
        config_error(f"{path}: 'entries' must be a non-empty list")
    seen = set()
    for i, row in enumerate(entries, 1):
        for key in ("id", "title", "text"):
            if not row.get(key):
                config_error(f"{path}: entry {i} is missing a non-empty {key!r}")
        if row["id"] in seen:
            config_error(f"{path}: duplicate entry id {row['id']!r}")
        seen.add(row["id"])
        if not isinstance(row.get("questions", []), list):
            config_error(f"{path}: entry {row['id']!r} has a non-list 'questions'")
    for pair in corpus.get("synonyms", []):
        if not (isinstance(pair, list) and len(pair) == 2 and all(pair)):
            config_error(f"{path}: every synonym must be a [source, target] pair")

    declared = corpus.get("corpus_version")
    wanted = golden.get("knowledge_version")
    if declared and wanted and declared != wanted:
        # A golden set scored against a corpus it was not written for is a
        # silently wrong number, which is worse than no number.
        config_error(f"{golden_path} expects knowledge_version {wanted!r} but "
                 f"{path} declares corpus_version {declared!r}")
    return path, corpus


def seed_corpus(corpus: dict) -> None:
    """Write the corpus into the throwaway harness database.

    Plain SQL rather than app.db.queries.save_dataset: that writer does not
    carry title_en/text_en (only the admin dataset router does, inline), and
    the English fields are the whole reason the corpus is bilingual — without
    them the corpus vocabulary has no English in it and every English query
    reads as a query full of unknown tokens. Adding a writer to app/ is out
    of this change's scope, so the columns are named here instead.
    """
    from app.db.connection import get_db_connection

    entries = corpus["entries"]
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM questions")
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM synonyms")
        conn.executemany(
            "INSERT INTO dataset (id, title, text, video_url, title_en,"
            " text_en, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(row["id"], row["title"], row["text"], "",
              row.get("title_en", ""), row.get("text_en", ""), (i + 1) * 10)
             for i, row in enumerate(entries)])
        conn.executemany(
            "INSERT INTO questions (question, dataset_id, video_url)"
            " VALUES (?, ?, '')",
            [(q, row["id"]) for row in entries
             for q in row.get("questions", [])])
        conn.executemany(
            "INSERT INTO synonyms (source, target) VALUES (?, ?)",
            [(src, dst) for src, dst in corpus.get("synonyms", [])])
        conn.commit()
    finally:
        conn.close()


def _isolate_database() -> str:
    """Point the app at a throwaway SQLite database, before any app import.

    app/config.py resolves DB_PATH and LOGS_DB_PATH once, at import time, so
    this has to run first — the same rule --conversations already follows.

    It is not only about reproducibility. seed_corpus() replaces the dataset,
    questions and synonyms tables wholesale; against the install's real
    database that command would delete a customer's knowledge base. The
    benchmark must never be able to do that.
    """
    if "app.config" in sys.modules:
        config_error("run_eval.py configures its own database before any app "
                 "import; run it as its own command")
    tmp = tempfile.mkdtemp(prefix="padyar-eval-")
    os.environ["DB_PATH"] = os.path.join(tmp, "eval.db")
    os.environ["LOGS_DB_PATH"] = os.path.join(tmp, "application_logs.db")
    return tmp


# --- floors (--check) -------------------------------------------------------

def load_floors(path: str, mode: str) -> dict:
    """The bounds for ONE mode, validated before anything is measured.

    Checked up front so a broken floors file costs no benchmark run, and so a
    mode that has no floors is an error, never a silent pass.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"floors file not found: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"{p} is not valid JSON: {e}")
    if not isinstance(doc, dict):
        raise ConfigError(f"{p} must be a JSON object keyed by mode")
    unknown_modes = sorted(set(doc) - set(MODES))
    if unknown_modes:
        raise ConfigError(f"{p} names unknown mode(s): {unknown_modes}")
    bounds = doc.get(mode)
    if not isinstance(bounds, dict) or not bounds:
        raise ConfigError(f"mode {mode!r} has no floors in {p}; a mode without "
                          f"floors is not checked, so it cannot pass")
    for name, bound in bounds.items():
        if (not isinstance(bound, dict) or len(bound) != 1
                or next(iter(bound)) not in ("min", "max")
                or isinstance(next(iter(bound.values())), bool)
                or not isinstance(next(iter(bound.values())), (int, float))):
            raise ConfigError(f"{p}: {mode}.{name} must be {{\"min\": n}} or "
                              f"{{\"max\": n}}, got {bound!r}")
    return bounds


def resolve_metric(report: dict, name: str):
    """A bare name is totals.<name>; a dotted name is a path from the root."""
    path = name.split(".") if "." in name else ["totals", name]
    node = report
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"unknown metric {name!r} for mode "
                              f"{report.get('mode')!r}")
        node = node[part]
    if isinstance(node, dict) or isinstance(node, bool):
        raise ConfigError(f"metric {name!r} is not a number")
    return node


def check_floors(report: dict, bounds: dict) -> list:
    """Every violated bound, as one printable line each. Empty means pass.

    A metric whose value is null cannot be shown to meet its bound, so it is
    a violation, not a skip.
    """
    violations = []
    mode = report.get("mode")
    for name, bound in bounds.items():
        value = resolve_metric(report, name)
        kind, limit = next(iter(bound.items()))
        if value is None:
            violations.append(f"FLOOR VIOLATION {mode}.{name}: measured null, "
                              f"{kind} {limit}")
        elif kind == "min" and value < limit:
            violations.append(f"FLOOR VIOLATION {mode}.{name}: measured {value} "
                              f"< min {limit}")
        elif kind == "max" and value > limit:
            violations.append(f"FLOOR VIOLATION {mode}.{name}: measured {value} "
                              f"> max {limit}")
    return violations


def load_controls(golden: dict, queries: list) -> dict:
    """The golden `controls` block, validated against the queries that carry them."""
    block = golden.get("controls") or {}
    controls = {}
    for name, spec in block.items():
        if name not in CONTROL_RULES:
            raise ConfigError(f"unknown control {name!r}; known: {sorted(CONTROL_RULES)}")
        if (not isinstance(spec, dict) or spec.get("status") not in CONTROL_STATUSES
                or not spec.get("claim")):
            raise ConfigError(f"control {name!r} must be {{\"claim\": ..., \"status\": "
                              f"one of {list(CONTROL_STATUSES)}}}")
        controls[name] = spec
    for item in queries:
        name = item.get("control")
        if name and name not in controls:
            raise ConfigError(f"query {item['q']!r} names control {name!r}, which the "
                              f"golden `controls` block does not declare")
    return controls


# --- the serving modes: the router itself -----------------------------------

class RouterDriver:
    """Sends one golden query at a time to the real POST /chat.

    AI is off in the throwaway database (`openai_enabled=false`) and the key
    is empty, so no provider can be reached. Two more in-process settings,
    both on module globals the router reads at call time:

    * The AI-off fallback leg is disabled (its two thresholds set above 1).
      That leg serves a sub-trust record only because the AI tier is down;
      in a real install the query goes to the AI tier. It is not an answer of
      the local layer (ruling R5), and with it disabled the router says "no
      answer" instead, so `answered` needs no copy of any bar here.
    * In full_no_intent the router's own classify_intent_local is replaced by
      a function that never answers: Tier 1.5 is gone, nothing else moves.

    The served record id is read from chat_logs.entry_id, the column the
    router itself writes for the conversation (its pick/follow-up memory).
    Every query gets a fresh conversation. The cookie jar is CLEARED first:
    the response sets padyar_conv for the TestClient host, and a jar that
    kept it would send the previous query's conversation id instead of ours.
    That is exactly what an early probe of this design did (every turn
    landed in conversation eval-1), and it looked like a missing entry_id.
    """

    def __init__(self, mode: str):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.auth import security
        import app.routers.chat as chat_router

        # Same sanctioned tuning point as --conversations: the rate ceilings
        # are module globals the router reads at call time.
        security.CHAT_RATE_LIMIT = 10 ** 6
        security.CHAT_IP_RATE_LIMIT = 10 ** 6
        chat_router.LOCAL_FALLBACK_THRESHOLD = 2.0
        chat_router.QUESTIONS_FALLBACK_THRESHOLD = 2.0
        if mode == "full_no_intent":
            chat_router.classify_intent_local = lambda _query: (None, 0.0)
        self._client_cm = TestClient(app)
        self.client = self._client_cm.__enter__()
        from app.db import queries
        queries.set_setting("openai_enabled", "false")
        self.headers = {"X-Chat-Token": security.generate_chat_token(),
                        "Origin": "http://localhost"}

    def close(self):
        self._client_cm.__exit__(None, None, None)

    def ask(self, n: int, q: str, lang: str) -> dict:
        from app.db.connection import get_db_connection
        conv = f"eval-{n:03d}"
        self.client.cookies.clear()
        self.client.cookies.set("padyar_conv", conv)
        resp = self.client.post("/chat", json={"message": q, "lang": lang},
                                headers=self.headers)
        if resp.status_code != 200:
            raise ConfigError(f"POST /chat returned {resp.status_code} for query "
                              f"{n} ({q!r}): {resp.text[:200]}")
        body = resp.json()
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT conversation_id, entry_id FROM chat_logs"
                " WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
                (conv,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ConfigError(f"the router logged no turn for conversation {conv} "
                              f"(query {n}); the served id cannot be read")
        return {"entry_id": row["entry_id"] or "", "source": body.get("source") or "",
                "confidence": round(float(body.get("confidence") or 0.0), 4),
                "text": body.get("text") or ""}


# --- scoring ----------------------------------------------------------------

def score_answerable(accepted, answered, served_id, ranking):
    """rank, ranked_first and correct for one answerable query.

    `correct` needs an ANSWER: a refused query is never correct, even when its
    ranking had the right id first (defect D2). That case is `ranked_first`.
    """
    if answered:
        ranking = [served_id] + [i for i in ranking if i != served_id]
    found = [ranking.index(e) + 1 for e in accepted if e in ranking]
    rank = min(found) if found else None
    return {"rank": rank, "ranked_first": rank == 1,
            "correct": bool(answered and served_id in accepted)}


def _rate(num, den):
    return round(num / den, 3) if den else None


def measure(mode, golden, corpus, search, recall_ks, dump):
    """Run every golden query in `mode`. Returns (report parts, rows)."""
    from app.config import TRUSTED_MATCH_THRESHOLD

    serving = mode in SERVING_MODES
    queries = golden["queries"]
    controls = load_controls(golden, queries)
    legacy_tokens = merge_tokens(LEGACY_TOKENS, golden.get("legacy_tokens"))
    secret_markers = merge_tokens(SECRET_MARKERS, golden.get("secret_markers"))
    depth = len(search.dataset)
    driver = RouterDriver(mode) if serving else None

    rows, latencies = [], []
    title_head = 0
    try:
        for n, item in enumerate(queries, 1):
            for key in ("q", "expect", "cat"):
                if key not in item:
                    raise ConfigError(f"golden query {n} is missing {key!r}")
            q, expect, cat = item["q"], item["expect"], item["cat"]
            accepted = ([expect] if isinstance(expect, str) else list(expect)) if expect else []
            lang = query_lang(q)
            mq = match_query(q)
            t0 = time.perf_counter()
            served = driver.ask(n, q, lang) if serving else None
            ranking, fired = ranking_for(mode, mq, search, depth)
            latencies.append((time.perf_counter() - t0) * 1000)
            title_head += int(fired)

            row = {"n": n, "q": q, "lang": lang, "cat": cat,
                   "expected": accepted or None, "control": item.get("control")}
            answered = bool(serving and served["entry_id"])
            if serving:
                unknown = sorted(search.unknown_salient_tokens(mq))
                row.update({
                    "answered": answered,
                    "served_id": served["entry_id"] or None,
                    "served_source": served["source"],
                    "served_confidence": served["confidence"],
                    "refused_by": None if answered else (
                        "unknown_token_gate" if unknown else served["source"]),
                    "unknown_tokens": unknown,
                    "has_candidates": bool(ranking),
                })
                text = served["text"].lower()
                row["legacy_tokens_found"] = sorted(
                    t for t in legacy_tokens if t.lower() in text)
                row["secret_markers_found"] = sorted(
                    t for t in secret_markers if t in served["text"])
            if accepted:
                row.update(score_answerable(accepted, answered,
                                            served and served["entry_id"], ranking))
                if not serving:
                    row["correct"] = None
            else:
                row.update({"rank": None, "ranked_first": None})
                if serving:
                    clean = not (row["legacy_tokens_found"] or row["secret_markers_found"])
                    # Legacy: judged by the contamination gate only (R6).
                    row["correct"] = clean if cat == LEGACY_CATEGORY else (
                        clean and not answered)
                else:
                    row["correct"] = None
            if serving and item.get("control"):
                name = item["control"]
                ok = bool(answered and row["served_id"] in accepted)
                if name == "paraphrase_above_trust":
                    ok = ok and served["confidence"] >= TRUSTED_MATCH_THRESHOLD
                row["control_pass"] = ok
            if dump:
                row["diagnostics"] = diagnose_query(mq, search)
            rows.append(row)
    finally:
        if driver:
            driver.close()

    answerable = [r for r in rows if r["expected"]]
    ranks = [r["rank"] for r in answerable]
    totals = {
        "queries": len(rows),
        "answerable": len(answerable),
        "recall_at_1": _rate(sum(1 for r in ranks if r == 1), len(answerable)),
        "mrr": (round(sum(1.0 / r for r in ranks if r) / len(ranks), 3)
                if ranks else None),
    }
    report = {"totals": totals}
    report["recall_at_k"] = {
        str(k): _rate(sum(1 for r in ranks if r and r <= k), len(answerable))
        for k in recall_ks}

    per_language = {}
    for lang in ("fa", "en"):
        a = [r for r in answerable if r["lang"] == lang]
        entry = {"answerable": len(a),
                 "recall_at_1": _rate(sum(1 for r in a if r["rank"] == 1), len(a))}
        if serving:
            entry["answered_at_1"] = _rate(sum(1 for r in a if r["correct"]), len(a))
        per_language[lang] = entry
    report["per_language"] = per_language

    per_category = {}
    for r in rows:
        c = per_category.setdefault(r["cat"], {"n": 0, "ranked_first": 0,
                                               "correct": 0 if serving else None,
                                               "refused": 0 if serving else None})
        c["n"] += 1
        if r["ranked_first"]:
            c["ranked_first"] += 1
        if serving:
            c["correct"] += int(bool(r["correct"]))
            c["refused"] += int(not r["answered"])
            if not r["expected"]:
                if r["has_candidates"]:
                    c["queries_with_candidates"] = c.get("queries_with_candidates", 0) + 1
                if not r["answered"]:
                    key = f"refused_by_{r['refused_by']}"
                    c[key] = c.get(key, 0) + 1
    report["per_category"] = per_category

    if serving:
        deny = [r for r in rows if not r["expected"] and r["cat"] != LEGACY_CATEGORY]
        legacy = [r for r in rows if not r["expected"] and r["cat"] == LEGACY_CATEGORY]
        refused = [r for r in rows if not r["answered"] and r["cat"] != LEGACY_CATEGORY]
        totals.update({
            "answered_at_1": _rate(sum(1 for r in answerable if r["correct"]),
                                   len(answerable)),
            "deny_total": len(deny),
            "out_of_scope_refusal_rate": _rate(
                sum(1 for r in deny if not r["answered"]), len(deny)),
            "refusal_precision": _rate(
                sum(1 for r in refused if not r["expected"]), len(refused)),
            "false_confident_total": sum(1 for r in deny if r["answered"]),
            "legacy_total": len(legacy),
            "legacy_redirected": sum(1 for r in legacy if r["answered"]),
            "legacy_contamination_answers": sum(
                1 for r in rows if r["legacy_tokens_found"]),
            "secret_leaks": sum(1 for r in rows if r["secret_markers_found"]),
        })
        report["served_by_source"] = dict(sorted(
            {s: sum(1 for r in rows if r["served_source"] == s)
             for s in {r["served_source"] for r in rows}}.items()))
        report["controls"] = {
            name: {"status": spec["status"], "claim": spec["claim"],
                   "requirement": CONTROL_RULES[name],
                   "queries": [{"q": r["q"], "served_id": r["served_id"],
                                "served_confidence": r["served_confidence"],
                                "pass": r["control_pass"]}
                               for r in rows if r.get("control") == name],
                   "pass": all(r["control_pass"] for r in rows
                               if r.get("control") == name)}
            for name, spec in controls.items()}
    else:
        totals.update({"legacy_contamination_answers": None, "secret_leaks": None})
    if mode == "hybrid":
        report["title_overlap_head"] = title_head

    lat = sorted(latencies)
    totals["latency_ms_p50"] = round(statistics.median(lat), 1)
    totals["latency_ms_p95"] = round(lat[max(0, math.ceil(len(lat) * 0.95) - 1)], 1)

    failures = []
    for r in rows:
        if r["expected"] and not (r["correct"] if serving else r["rank"] == 1):
            failures.append({"q": r["q"], "expected": r["expected"],
                             "served_id": r.get("served_id"), "rank": r["rank"]})
        elif not r["expected"] and serving and not r["correct"]:
            failures.append({"q": r["q"], "cat": r["cat"], "served_id": r["served_id"],
                             "legacy_tokens_found": r["legacy_tokens_found"],
                             "secret_markers_found": r["secret_markers_found"]})
    report["failures"] = failures
    return report, rows


def main() -> int:
    p = argparse.ArgumentParser(description="Run the retrieval benchmark.")
    p.add_argument("--golden", default="",
                   help="Path to a golden JSON file (required unless --conversations)")
    p.add_argument("--mode", choices=MODES, default="full",
                   help="what to measure (see the module docstring); default full")
    p.add_argument("--check", default="",
                   help="floors file (data/eval/floors.json): exit 1 when this "
                        "mode breaks any bound")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--conversations", default="",
                   help="run the multi-turn conversation scenarios from this "
                        "fixture (see data/eval/conversations.json) instead "
                        "of the single-query benchmark")
    p.add_argument("--dump", default="",
                   help="write per-query rows and tier diagnostics to this JSON file")
    p.add_argument("--weights", default="",
                   help="reranker weights as dense,bm25,coverage — e.g. 0.50,0.30,0.20. "
                        "Overrides the module defaults for THIS run only (no writes).")
    p.add_argument("--cosine-floor", dest="cosine_floor", default="",
                   help="embedding cosine calibration floor for THIS run (span stays).")
    # WHY recall@K and not only recall@1: the selection tier shows the model K
    # retrieved records and lets it choose. Its ceiling is the chance the right
    # record is anywhere in those K, so ANSWER_TOPK has to be picked from a
    # measured curve, not guessed. The curve is in
    # docs/features/eval-benchmark/RESULTS.md.
    p.add_argument("--recall-k", dest="recall_k", default="1,3,5,8,13",
                   help="comma-separated K values for the recall@K table "
                        "(default 1,3,5,8,13)")
    args = p.parse_args()

    # The conversation mode is its own command: it points DB_PATH at a
    # throwaway database before the first app import, so it must branch
    # before any part of the golden path below ever runs.
    if args.conversations:
        return run_conversations(args.conversations)
    if not args.golden:
        p.error("--golden is required")

    try:
        recall_ks = sorted({int(x) for x in args.recall_k.split(",") if x.strip()})
    except ValueError:
        config_error("--recall-k needs whole numbers, e.g. 1,3,5,8,13")
    if not recall_ks or any(k < 1 for k in recall_ks):
        config_error("--recall-k needs at least one K of 1 or more")
    if args.check and (args.weights or args.cosine_floor):
        config_error("--check measures the shipped configuration; it cannot be "
                     "combined with --weights or --cosine-floor")

    try:
        bounds = load_floors(args.check, args.mode) if args.check else None
    except ConfigError as e:
        config_error(str(e))

    # Read the golden file and its corpus FIRST, then isolate the database,
    # both before the first app import below, because app/config.py resolves
    # DB_PATH once at import time.
    golden_path = Path(args.golden)
    if not golden_path.is_file():
        config_error(f"golden set not found: {golden_path}")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    if not golden.get("queries"):
        config_error(f"{golden_path} has no queries")
    corpus_path, corpus = load_corpus(golden_path, golden)
    tmp_db_dir = _isolate_database()

    # Experiment overrides: applied to the MODULE GLOBALS the services read at
    # call time. The embedding matrix is prebuilt and calibration happens on
    # the query side, so a floor change needs no rebuild, and nothing is
    # persisted, so a sweep can never leak a config into the product.
    if args.weights:
        parts = [float(x) for x in args.weights.split(",")]
        if len(parts) != 3 or any(x < 0 for x in parts) or sum(parts) <= 0:
            config_error("--weights needs dense,bm25,coverage (three non-negative numbers)")
        from app.services import rerank as _rr
        total = sum(parts)
        _rr.W_DENSE, _rr.W_LEXICAL, _rr.W_COVERAGE = (x / total for x in parts)
    if args.cosine_floor:
        from app.services import embeddings as _emb
        _emb.COSINE_FLOOR = float(args.cosine_floor)

    # init_db() is the same initialiser the application uses at startup, so
    # the benchmark measures the schema the product ships, on a clean runner
    # too (there is no database file there at all).
    from app.db.connection import init_db
    init_db()

    from app.config import RERANK_ENABLED
    from app.services import search
    seed_corpus(corpus)
    search.load_dataset_internal()
    if not search.dataset:
        config_error(f"the corpus at {corpus_path} produced an empty index; "
                     f"nothing to measure")
    if args.mode in ("hybrid",) + SERVING_MODES and not RERANK_ENABLED:
        # find_top_matches silently degrades to BM25 alone with reranking off;
        # a "hybrid" number from that run would be a bm25 number.
        config_error(f"--mode {args.mode} needs RETRIEVAL_RERANK=true")
    if args.mode == "dense" and search.dataset_embedding_index is None:
        config_error("--mode dense needs the embedding model; the dense index "
                     "was not built")
    if args.mode == "bm25" and search.dataset_bm25_index is None:
        config_error("--mode bm25: the BM25 index was not built")
    print(f"mode={args.mode}  corpus={corpus_path}  entries={len(search.dataset)}  "
          f"questions={len(search.questions_data)}  db={tmp_db_dir}")

    try:
        parts, rows = measure(args.mode, golden, corpus, search, recall_ks,
                              bool(args.dump))
    except ConfigError as e:
        config_error(str(e))

    report = {
        "dataset_version": golden.get("dataset_version", ""),
        "knowledge_version": golden.get("knowledge_version", ""),
        # Provenance: which corpus these numbers were measured against. A
        # recall figure without its corpus size is not reproducible.
        "corpus": {
            "path": str(corpus_path),
            "version": corpus.get("corpus_version", ""),
            "entries": len(search.dataset),
            "questions": len(search.questions_data),
        },
        "mode": args.mode,
        "rerank_enabled": RERANK_ENABLED,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "metric_notes": METRIC_NOTES,
        **parts,
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
            {"mode": args.mode, "ran_at": report["ran_at"],
             "experiment": report.get("experiment", "default"),
             "totals": report["totals"], "queries": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"diagnostics → {dump_path}")

    t = report["totals"]
    print(f"queries={t['queries']}  recall@1={t['recall_at_1']}  mrr={t['mrr']}")
    print("recall@K: " + "  ".join(
        f"@{k}={report['recall_at_k'][str(k)]}" for k in recall_ks))
    if args.mode in SERVING_MODES:
        print(f"answered@1={t['answered_at_1']}  "
              f"out_of_scope_refusal_rate={t['out_of_scope_refusal_rate']}  "
              f"refusal_precision={t['refusal_precision']}  "
              f"false_confident_total={t['false_confident_total']}")
        print(f"legacy redirected={t['legacy_redirected']}/{t['legacy_total']}  "
              f"contaminated answers={t['legacy_contamination_answers']}  "
              f"secret leaks={t['secret_leaks']}")
        for lang, v in report["per_language"].items():
            print(f"  {lang}: answerable={v['answerable']}  "
                  f"answered@1={v['answered_at_1']}  recall@1={v['recall_at_1']}")
    print(f"latency p50={t['latency_ms_p50']}ms  p95={t['latency_ms_p95']}ms "
          f"(machine-specific, never gated)")
    print(f"report → {out}")
    if report["failures"]:
        print(f"\n{len(report['failures'])} miss(es); first 5:")
        for f in report["failures"][:5]:
            print("  ", json.dumps(f, ensure_ascii=False))

    exit_code = 0
    if args.mode in SERVING_MODES and (t["legacy_contamination_answers"]
                                       or t["secret_leaks"]):
        print(f"GATE FAILED: contaminated answers={t['legacy_contamination_answers']} "
              f"secret leaks={t['secret_leaks']}")
        exit_code = 1
    if bounds is not None:
        try:
            violations = check_floors(report, bounds)
        except ConfigError as e:
            config_error(str(e))
        for name, c in report.get("controls", {}).items():
            if c["status"] == "enforced" and not c["pass"]:
                violations.append(f"CONTROL VIOLATION {args.mode}.{name}: "
                                  f"{c['requirement']} failed")
        for line in violations:
            print(line)
        if violations:
            exit_code = 1
        else:
            print(f"check: every bound for mode {args.mode!r} held")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
