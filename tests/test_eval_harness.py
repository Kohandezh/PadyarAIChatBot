"""The committed retrieval benchmark: fixtures, harness, gates and CI wiring.

WHY THIS FILE EXISTS. `scripts/run_eval.py` measures the local retrieval layer
— the part of this product that is not a wrapper around somebody else's model.
It was unrunnable from a clean checkout: the golden set it needed was deleted
with the event install it belonged to, `app/default_content.py` ships empty on
purpose, and no CI job ever called it. Every recall number published in
`docs/engineering/` was therefore unreproducible. These tests pin the three
things that make it reproducible again: the fixtures are committed, the harness
runs offline against them, and the safety gates still fail the build.

Every harness run here happens in a SUBPROCESS. The exit code is the contract
(a tripped gate must exit non-zero), and only a real process can prove it.
Nothing here writes into `data/eval/`: the gate tests build their own corpus
and golden pair under `tmp_path`, so an interrupted run cannot leave a poisoned
fixture behind.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
# PyYAML is not in requirements-dev.txt, but it is a hard dependency of
# huggingface_hub, which model2vec pulls in from requirements.txt. Every
# environment that can run this project has it, so a plain import is safe —
# and an importorskip here would let the CI-wiring checks vanish silently.
import yaml

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
GOLDEN = EVAL_DIR / "golden.json"
CORPUS = EVAL_DIR / "corpus.json"
RUN_EVAL = ROOT / "scripts" / "run_eval.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# app/config.py's TRUSTED_MATCH_THRESHOLD, mirrored in run_eval.py as TRUST.
TRUST = 0.70


def run_eval(*args, expect_exit=0):
    """Run the harness as its own process and return the completed process.

    OPENAI_API_KEY is emptied on purpose: the benchmark measures the local
    layer and must never need an AI provider. If it ever starts needing one,
    this call is where it breaks.
    """
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = ""
    proc = subprocess.run(
        [sys.executable, str(RUN_EVAL), *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=1800)
    if expect_exit is not None and proc.returncode != expect_exit:
        raise AssertionError(
            f"run_eval.py exited {proc.returncode}, expected {expect_exit}\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}")
    return proc


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    """One clean run of the committed fixtures, shared by the tests below."""
    out = tmp_path_factory.mktemp("eval-baseline")
    proc = run_eval("--golden", str(GOLDEN),
                    "--out", str(out / "report.json"),
                    "--dump", str(out / "dump.json"),
                    "--recall-k", "1,3,5,8,13")
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    dump = json.loads((out / "dump.json").read_text(encoding="utf-8"))
    return proc, report, dump


@pytest.fixture(scope="module")
def golden_doc():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus_doc():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _by_query(dump):
    return {row["q"]: row for row in dump["queries"]}


def _control(golden_doc, name):
    """The golden query carrying this allow-control marker."""
    hits = [r for r in golden_doc["queries"] if r.get("control") == name]
    assert len(hits) == 1, f"expected exactly one {name!r} control, got {len(hits)}"
    return hits[0]


# --- the fixtures are committed -------------------------------------------

@pytest.mark.parametrize("path", [GOLDEN, CORPUS])
def test_fixture_is_tracked_by_git(path):
    """A benchmark that lives only on one machine proves nothing."""
    rel = path.relative_to(ROOT).as_posix()
    listed = subprocess.run(["git", "ls-files", rel], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=60)
    assert listed.stdout.strip() == rel, (
        f"{rel} is not tracked by git — commit it or the benchmark cannot be "
        f"reproduced from a clean clone")


def test_golden_set_is_large_and_bilingual(golden_doc, corpus_doc):
    queries = golden_doc["queries"]
    entries = corpus_doc["entries"]
    assert len(queries) >= 40
    assert len(entries) >= 20

    def is_english(text):
        return all(ch.isascii() for ch in text if ch.isalpha())

    english = [r for r in queries if is_english(r["q"])]
    persian = [r for r in queries if not is_english(r["q"])]
    assert english and persian, "the golden set must carry both languages"


def test_every_expected_id_exists_in_the_corpus(golden_doc, corpus_doc):
    """A dangling expectation is an unanswerable query masquerading as a miss."""
    ids = {e["id"] for e in corpus_doc["entries"]}
    dangling = sorted({e for r in golden_doc["queries"] if r["expect"]
                       for e in ([r["expect"]] if isinstance(r["expect"], str)
                                 else r["expect"])} - ids)
    assert dangling == []


def test_corpus_entries_carry_english_fields(corpus_doc):
    """Design decision: the corpus is bilingual.

    The retrieval index is built from the Persian title+text alone, so the
    English fields are what put English tokens in the corpus vocabulary — and
    without them every English query reads as a query full of unknown tokens
    and never reaches the reranker's cross-lingual path at all.
    """
    missing = [e["id"] for e in corpus_doc["entries"]
               if not e.get("title_en") or not e.get("text_en")]
    assert missing == []


def test_corpus_is_not_seeded_into_an_install():
    """`app/default_content.py` stays empty — a customer must not inherit it."""
    from app import default_content
    assert default_content.DEFAULT_DATASET == []
    assert default_content.DEFAULT_QUESTIONS == []


# --- the harness runs, offline, and reports the headline metrics ----------

def test_harness_runs_offline_and_prints_the_metrics(baseline):
    proc, _report, _dump = baseline
    out = proc.stdout
    assert "recall@1=" in out
    assert "recall@3=" in out
    assert "mrr=" in out
    assert "recall@K:" in out


def test_report_carries_its_corpus_provenance(baseline, corpus_doc):
    _proc, report, _dump = baseline
    assert report["corpus"]["version"] == corpus_doc["corpus_version"]
    assert report["corpus"]["entries"] == len(corpus_doc["entries"])
    totals = report["totals"]
    for key in ("recall_at_1", "recall_at_3", "mrr"):
        assert isinstance(totals[key], float)


def test_default_report_path_stays_outside_the_repository():
    """R2: the default --out used to drop an untracked file into data/eval/.

    A report is a run artifact, not a fixture. Running the documented command
    with no --out must leave the tracked fixture directory alone.
    """
    bare = run_eval("--golden", str(GOLDEN))
    assert not (EVAL_DIR / "retrieval-eval.json").exists()
    written = [line.split("→", 1)[1].strip()
               for line in bare.stdout.splitlines() if line.startswith("report →")]
    assert len(written) == 1
    report_path = Path(written[0])
    assert report_path.is_file()
    assert ROOT not in report_path.parents, (
        f"the default report landed inside the repository at {report_path}")


# --- deny-cases, and the allow-control for each ---------------------------

def test_unsupported_queries_are_refused_by_a_gate_not_by_an_empty_index(baseline):
    """A refusal only counts when retrieval HAD something and declined anyway.

    If the index were empty (the state a clean checkout was actually in), every
    adversarial query would be "refused" for free and this category would read
    as a pass. So assert both halves: nothing is answered confidently, AND the
    retriever produced candidates while refusing.
    """
    _proc, report, dump = baseline
    stat = report["per_category"]["unsupported"]
    assert stat["n"] > 0
    assert report["totals"]["false_confident_unsupported"] == 0
    assert stat["queries_with_candidates"] == stat["n"], (
        "a query was refused while retrieval returned nothing — that is an "
        "accidental pass, not the pipeline declining")
    refused = (stat.get("refused_by_unknown_token_gate", 0)
               + stat.get("refused_by_below_trust_bar", 0))
    assert refused == stat["n"]
    # Both mechanisms are real on this corpus; the gate is the dominant one.
    assert stat.get("refused_by_unknown_token_gate", 0) > 0

    for row in dump["queries"]:
        if row["cat"] != "unsupported":
            continue
        assert row["served"] is None
        assert row["refused_by"] in ("unknown_token_gate", "below_trust_bar")
        if row["refused_by"] == "unknown_token_gate":
            assert row["unknown_tokens"], "gate fired with no unknown token"


def test_prompt_injection_is_refused_and_leaks_no_secret(baseline):
    _proc, report, dump = baseline
    stat = report["per_category"]["prompt_injection"]
    assert stat["n"] > 0
    assert report["totals"]["false_confident_injection"] == 0
    assert report["totals"]["secret_leaks"] == 0
    assert stat["queries_with_candidates"] == stat["n"]
    assert all(row["served"] is None for row in dump["queries"]
               if row["cat"] == "prompt_injection")


def test_paraphrase_allow_control_is_answered_above_the_trust_bar(
        baseline, golden_doc):
    """The allow-control for the `unsupported` deny-case.

    Refusing everything is a trivial way to pass an adversarial category. This
    is the query that proves the pipeline did not simply go quiet.
    """
    _proc, _report, dump = baseline
    control = _control(golden_doc, "paraphrase_above_trust")
    row = _by_query(dump)[control["q"]]
    assert row["served"] == control["expect"]
    assert row["served_score"] >= TRUST


def test_benign_lookalike_allow_control_is_still_answered(baseline, golden_doc):
    """The allow-control for the `prompt_injection` deny-case.

    It shares wording with the injections and must still be answered.
    """
    _proc, _report, dump = baseline
    control = _control(golden_doc, "benign_lookalike_answered")
    row = _by_query(dump)[control["q"]]
    assert row["served"] == control["expect"]


def test_legacy_queries_leave_no_legacy_identity_in_the_answer(baseline):
    _proc, report, _dump = baseline
    assert report["totals"]["legacy_contamination_answers"] == 0
    assert report["totals"]["false_confident_legacy"] == 0


# --- the gates themselves: proven live, not asserted ----------------------

LEAK_MARKER = "ZZ-EVAL-CANARY-MARKER"
LEGACY_MARKER = "ZZ-EVAL-RETIRED-BRAND"

_PROBE_ENTRIES = [
    {"id": "probe-guide", "title": "راهنمای بازدید",
     "text": "راهنمای بازدید برای مهمانان تازه وارد در ورودی اصلی توزیع می شود.",
     "title_en": "Visit guide",
     "text_en": "The visit guide is handed out at the main entrance.",
     "questions": ["راهنمای بازدید کجاست"]},
    {"id": "probe-hours", "title": "ساعت کاری",
     "text": "ساعت کاری هر روز از نه صبح تا شش بعد از ظهر است.",
     "title_en": "Working hours",
     "text_en": "Working hours run from nine in the morning until six.",
     "questions": ["ساعت کاری چگونه است"]},
    {"id": "probe-map", "title": "نقشه محوطه",
     "text": "نقشه محوطه روی تابلوی ورودی نصب شده است.",
     "title_en": "Site map", "text_en": "The site map is posted at the entrance.",
     "questions": ["نقشه محوطه کجا نصب شده"]},
]


def _write_probe_pair(tmp_path, poison=None, declare=None):
    """Write a throwaway corpus + golden pair, optionally poisoned.

    The golden query is one entry's curated question VERBATIM, so tier 0 serves
    that entry with certainty. Its text is what the gates then inspect — which
    makes the trip deterministic instead of dependent on a retrieval score.
    """
    entries = [dict(e) for e in _PROBE_ENTRIES]
    if poison:
        entries[0] = dict(entries[0])
        entries[0]["text"] = entries[0]["text"] + " " + poison
    corpus = {"corpus_version": "eval-gate-probe-1.0", "entries": entries}
    golden = {
        "dataset_version": "eval-gate-probe-golden-1.0",
        "knowledge_version": "eval-gate-probe-1.0",
        "corpus": "probe-corpus.json",
        "queries": [
            {"q": entries[0]["questions"][0], "expect": None,
             "cat": "legacy_contamination"},
            {"q": entries[1]["questions"][0], "expect": entries[1]["id"],
             "cat": "current_event_facts"},
        ],
    }
    golden.update(declare or {})
    (tmp_path / "probe-corpus.json").write_text(
        json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    golden_path = tmp_path / "probe-golden.json"
    golden_path.write_text(json.dumps(golden, ensure_ascii=False), encoding="utf-8")
    return golden_path


def test_a_clean_probe_run_exits_zero(tmp_path):
    """The allow-control for both gate tests: an unpoisoned corpus passes."""
    golden = _write_probe_pair(tmp_path)
    run_eval("--golden", str(golden), "--out", str(tmp_path / "out.json"),
             expect_exit=0)


def test_contamination_gate_exits_non_zero(tmp_path):
    golden = _write_probe_pair(
        tmp_path, poison=LEGACY_MARKER,
        declare={"legacy_tokens": [LEGACY_MARKER]})
    proc = run_eval("--golden", str(golden), "--out", str(tmp_path / "out.json"),
                    expect_exit=1)
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["totals"]["legacy_contamination_answers"] > 0
    assert proc.returncode != 0


def test_secret_leak_gate_exits_non_zero(tmp_path):
    golden = _write_probe_pair(
        tmp_path, poison=LEAK_MARKER,
        declare={"secret_markers": [LEAK_MARKER]})
    proc = run_eval("--golden", str(golden), "--out", str(tmp_path / "out.json"),
                    expect_exit=1)
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["totals"]["secret_leaks"] > 0
    assert proc.returncode != 0


def test_a_golden_file_without_a_corpus_is_refused(tmp_path):
    """The failure that made every published number unreproducible.

    A golden set scored against whatever happens to be in the local database
    is not a measurement. Refuse it loudly instead of printing a number.
    """
    golden = tmp_path / "no-corpus.json"
    golden.write_text(json.dumps(
        {"dataset_version": "x", "knowledge_version": "y",
         "queries": [{"q": "سلام", "expect": None, "cat": "unsupported"}]}),
        encoding="utf-8")
    proc = run_eval("--golden", str(golden), expect_exit=None)
    assert proc.returncode != 0
    assert "corpus" in (proc.stdout + proc.stderr).lower()


def test_the_harness_never_writes_into_the_install_database(tmp_path):
    """seed_corpus() replaces dataset/questions/synonyms wholesale.

    Pointed at a real install that is a knowledge-base wipe, so the harness
    has to build its own database. Proven by giving the process a DB_PATH and
    checking the file is never created.
    """
    victim = tmp_path / "must-not-exist.db"
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = ""
    env["DB_PATH"] = str(victim)
    proc = subprocess.run(
        [sys.executable, str(RUN_EVAL), "--golden", str(GOLDEN),
         "--out", str(tmp_path / "out.json")],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert not victim.exists(), "the harness wrote into the database it was given"


# --- CI wiring ------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow():
    doc = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    doc["_triggers"] = doc.get("on", doc.get(True, {}))
    return doc


def test_ci_has_a_blocking_evaluation_job(workflow):
    job = workflow["jobs"].get("evaluation")
    assert job is not None, "ci.yml has no `evaluation` job"
    assert not job.get("continue-on-error"), (
        "the evaluation job must be blocking — an advisory benchmark is a "
        "benchmark nobody fixes")
    triggers = workflow["_triggers"]
    assert "push" in triggers and "pull_request" in triggers

    steps = job["steps"]
    runs = " ".join(str(s.get("run", "")) for s in steps)
    assert "scripts/run_eval.py" in runs
    assert "--golden" in runs
    assert any(str(s.get("uses", "")).startswith("actions/upload-artifact")
               for s in steps), "the evaluation job must upload its report"


def test_ci_caches_the_embedding_model_on_its_pinned_revision(workflow):
    """R1: `data/eval` is committed but `data/models` is not.

    The pinned model is a 513 MB download. Without a cache every run of every
    job pays for it, and the key has to be the revision — a cache keyed on
    anything looser serves the wrong weights and silently changes a ranking.
    """
    from app.services.embeddings import DEFAULT_MODEL_REVISION

    cached = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/cache"):
                key = str(step.get("with", {}).get("key", ""))
                if DEFAULT_MODEL_REVISION in key:
                    cached.append(name)
    assert "evaluation" in cached, (
        "the evaluation job must cache data/models on "
        "DEFAULT_MODEL_REVISION")
