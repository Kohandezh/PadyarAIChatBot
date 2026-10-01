"""The committed retrieval benchmark: fixtures, harness, gates and CI wiring.

WHY THIS FILE EXISTS. `scripts/run_eval.py` measures the local answer layer,
the part of this product that is not a wrapper around somebody else's model.
It was unrunnable from a clean checkout until the fixtures were committed, and
at 420eb1e its CI gate could not fail on a ranking regression (only
contamination or a secret leak exited 1), while `recall_at_1` counted a
refused query as a correct answer. These tests pin what makes the benchmark a
gate: the fixtures are committed, the harness runs offline, `--check` fails on
a floor, and a refusal is never scored as an answer.

The design and every metric are defined in
docs/features/eval-benchmark/SPEC.md.

COST. A harness run loads the embedding model and, in the serving modes,
boots the app. So the suite makes exactly FOUR such runs: one of the committed
golden set (module fixture `baseline`) and three of a three-entry probe corpus
(`clean_probe`, `floors_above_probe`, `poisoned_probe`). Everything that can be
proven without a model (floors comparison, token merge, scoring rule) is a
plain unit test on the harness functions. Three more subprocess runs fail on
their inputs before any app import and cost well under a second each.

Every harness run happens in a SUBPROCESS: the exit code is the contract, and
only a real process can prove it. Nothing here writes into `data/eval/`.
"""
import importlib.util
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
FLOORS = EVAL_DIR / "floors.json"
RUN_EVAL = ROOT / "scripts" / "run_eval.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
MODES = ("full", "full_no_intent", "hybrid", "dense", "bm25")


def _load_harness():
    spec = importlib.util.spec_from_file_location("run_eval_under_test", RUN_EVAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def run_eval(*args, expect_exit=0, env_extra=None):
    """Run the harness as its own process and return the completed process.

    OPENAI_API_KEY is emptied on purpose: the benchmark measures the local
    layer and must never need an AI provider.
    """
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = ""
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(RUN_EVAL), *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=3600)
    if expect_exit is not None and proc.returncode != expect_exit:
        raise AssertionError(
            f"run_eval.py exited {proc.returncode}, expected {expect_exit}\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}")
    return proc


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden_doc():
    return _json(GOLDEN)


@pytest.fixture(scope="module")
def corpus_doc():
    return _json(CORPUS)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    """HEAVY RUN 1 of 4: the committed golden set, `full`, committed floors."""
    out = tmp_path_factory.mktemp("eval-baseline")
    proc = run_eval("--golden", str(GOLDEN), "--mode", "full",
                    "--check", str(FLOORS),
                    "--out", str(out / "report.json"),
                    "--dump", str(out / "dump.json"),
                    "--recall-k", "1,3,5,8,13")
    return proc, _json(out / "report.json"), _json(out / "dump.json")


def _rows(dump):
    return {row["q"]: row for row in dump["queries"]}


# --- the fixtures are committed and honest ---------------------------------

@pytest.mark.parametrize("path", [GOLDEN, CORPUS, FLOORS])
def test_fixture_is_tracked_by_git(path):
    """A benchmark that lives only on one machine proves nothing."""
    rel = path.relative_to(ROOT).as_posix()
    listed = subprocess.run(["git", "ls-files", rel], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=60)
    assert listed.stdout.strip() == rel, (
        f"{rel} is not tracked by git; commit it or the benchmark cannot be "
        f"reproduced from a clean clone")


def test_golden_set_is_large_and_bilingual(golden_doc, corpus_doc):
    queries = golden_doc["queries"]
    assert len(queries) >= 40
    assert len(corpus_doc["entries"]) >= 20
    langs = {harness.query_lang(r["q"]) for r in queries if r["expect"]}
    assert langs == {"fa", "en"}, "the golden set must carry both languages"


def test_every_expected_id_exists_in_the_corpus(golden_doc, corpus_doc):
    """A dangling expectation is an unanswerable query masquerading as a miss."""
    ids = {e["id"] for e in corpus_doc["entries"]}
    dangling = sorted({e for r in golden_doc["queries"] if r["expect"]
                       for e in ([r["expect"]] if isinstance(r["expect"], str)
                                 else r["expect"])} - ids)
    assert dangling == []


def test_corpus_entries_carry_english_fields(corpus_doc):
    """The corpus is bilingual: the English fields put English tokens in the
    corpus vocabulary, without which every English query reads as unknown."""
    missing = [e["id"] for e in corpus_doc["entries"]
               if not e.get("title_en") or not e.get("text_en")]
    assert missing == []


def test_the_fixtures_name_no_retired_real_brand(golden_doc, corpus_doc):
    """R4: the real retired brand names live only in the harness defaults.

    The legacy_contamination queries use an invented former name instead.
    """
    blob = json.dumps([golden_doc, corpus_doc], ensure_ascii=False).lower()
    for token in harness.LEGACY_TOKENS:
        assert token.lower() not in blob, f"{token!r} appears in data/eval/"


def test_every_control_is_declared_with_a_status(golden_doc):
    """A6: a control claims a bar only with an explicit status the gate reads."""
    harness.load_controls(golden_doc, golden_doc["queries"])
    for name, spec in golden_doc["controls"].items():
        assert spec["status"] in harness.CONTROL_STATUSES, name


def test_the_floors_file_covers_every_mode():
    for mode in MODES:
        assert harness.load_floors(str(FLOORS), mode)


# The exact gates each mode carries. Deleting a line from floors.json removes
# a gate from CI without failing anything, so the set is pinned here: dropping
# a gate has to be a visible change to this test too.
_RANKING_GATES = {"recall_at_1", "mrr", "recall_at_k.3", "recall_at_k.5",
                  "recall_at_k.8", "per_language.fa.recall_at_1"}
_SERVING_GATES = _RANKING_GATES | {"per_language.en.recall_at_1", "answered_at_1",
                                   "per_language.fa.answered_at_1",
                                   "out_of_scope_refusal_rate", "refusal_precision",
                                   "false_confident_total"}
EXPECTED_GATES = {
    "full": _SERVING_GATES,
    "full_no_intent": _SERVING_GATES,
    "hybrid": _RANKING_GATES | {"per_language.en.recall_at_1"},
    "dense": _RANKING_GATES | {"per_language.en.recall_at_1"},
    # English BM25 recall is 0 (D7): a floor of 0 checks nothing, so none.
    "bm25": _RANKING_GATES,
}


def test_the_floors_file_pins_every_gate_of_every_mode():
    floors = _json(FLOORS)
    assert set(floors) == set(EXPECTED_GATES)
    for mode, gates in EXPECTED_GATES.items():
        assert set(floors[mode]) == gates, (
            f"floors.json[{mode!r}] changed its gate set; if that is deliberate, "
            f"change EXPECTED_GATES in the same commit")
    for mode in ("full", "full_no_intent"):
        assert floors[mode]["false_confident_total"] == {"max": 0}


def test_the_dump_carries_no_latency():
    """R9: latency is machine-specific; it lives in the report only.

    420eb1e's dump copied `totals` with latency in it, so two dumps of the
    same command could never be identical.
    """
    report = {"mode": "full", "ran_at": "t", "totals": {
        "recall_at_1": 0.5, "latency_ms_p50": 1.0, "latency_ms_p95": 2.0}}
    doc = harness.dump_document(report, [{"q": "x"}])
    assert doc["totals"] == {"recall_at_1": 0.5}
    assert doc["queries"] == [{"q": "x"}]
    assert "latency" not in json.dumps(doc)
    assert report["totals"]["latency_ms_p50"] == 1.0, "the report keeps it"


def test_corpus_is_not_seeded_into_an_install():
    """`app/default_content.py` stays empty: a customer must not inherit it."""
    from app import default_content
    assert default_content.DEFAULT_DATASET == []
    assert default_content.DEFAULT_QUESTIONS == []


# --- the gate logic, without a model ----------------------------------------

def _report(**totals):
    return {"mode": "full", "totals": totals,
            "recall_at_k": {"3": 0.9}, "per_language": {"fa": {"answered_at_1": 0.5}}}


def test_a_floor_above_the_measured_value_is_a_violation_naming_the_metric():
    lines = harness.check_floors(_report(recall_at_1=0.8, mrr=0.9),
                                 {"recall_at_1": {"min": 0.85}, "mrr": {"min": 0.9}})
    assert lines == ["FLOOR VIOLATION full.recall_at_1: measured 0.8 < min 0.85"]


def test_a_floor_at_the_measured_value_passes():
    assert harness.check_floors(_report(recall_at_1=0.8),
                                {"recall_at_1": {"min": 0.8}}) == []


def test_false_confident_above_its_ceiling_is_a_violation():
    lines = harness.check_floors(_report(false_confident_total=1),
                                 {"false_confident_total": {"max": 0}})
    assert lines == ["FLOOR VIOLATION full.false_confident_total: measured 1 > max 0"]


def test_measured_zero_under_a_zero_ceiling_passes():
    assert harness.check_floors(_report(false_confident_total=0),
                                {"false_confident_total": {"max": 0}}) == []


def test_a_null_metric_cannot_pass_its_floor():
    lines = harness.check_floors(_report(refusal_precision=None),
                                 {"refusal_precision": {"min": 0.5}})
    assert lines and "measured null" in lines[0]


def test_dotted_metric_names_reach_nested_keys():
    report = _report()
    assert harness.check_floors(report, {"recall_at_k.3": {"min": 0.95},
                                         "per_language.fa.answered_at_1": {"min": 0.5}}) \
        == ["FLOOR VIOLATION full.recall_at_k.3: measured 0.9 < min 0.95"]


def test_an_unknown_metric_is_a_config_error_not_a_pass():
    with pytest.raises(harness.ConfigError, match="unknown metric 'recal_at_1'"):
        harness.check_floors(_report(recall_at_1=1.0), {"recal_at_1": {"min": 0.1}})


def test_a_mode_missing_from_the_floors_file_is_a_config_error(tmp_path):
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({"full": {"recall_at_1": {"min": 0.1}}}))
    assert harness.load_floors(str(floors), "full")
    with pytest.raises(harness.ConfigError, match="mode 'bm25' has no floors"):
        harness.load_floors(str(floors), "bm25")


@pytest.mark.parametrize("bound", [{"min": "0.5"}, {"at_least": 0.5},
                                   {"min": 0.1, "max": 0.9}, {"min": True}])
def test_a_malformed_bound_is_a_config_error(tmp_path, bound):
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({"full": {"recall_at_1": bound}}))
    with pytest.raises(harness.ConfigError):
        harness.load_floors(str(floors), "full")


def test_golden_token_lists_extend_the_defaults_and_never_replace_them():
    """D3: 420eb1e did `golden.get(...) or DEFAULTS`, so a golden file that
    declared its own list silently dropped every default from the gate."""
    merged = harness.merge_tokens(["elecomp", "noorvision"], ["tolvaresh"])
    assert merged == ["elecomp", "noorvision", "tolvaresh"]
    assert harness.merge_tokens(["elecomp"], None) == ["elecomp"]
    assert harness.merge_tokens(["elecomp"], ["elecomp"]) == ["elecomp"]


def test_a_refused_answerable_query_is_never_a_correct_answer():
    """D2: ranked first but refused is `ranked_first`, not `correct`."""
    refused = harness.score_answerable(["faq-a"], answered=False, served_id="",
                                       ranking=["faq-a", "faq-b"])
    assert refused == {"rank": 1, "ranked_first": True, "correct": False}


def test_a_served_correct_answerable_query_is_correct():
    served = harness.score_answerable(["faq-a"], answered=True, served_id="faq-a",
                                      ranking=["faq-b", "faq-a"])
    assert served == {"rank": 1, "ranked_first": True, "correct": True}


def test_a_served_wrong_record_is_ranked_first_by_what_was_served():
    wrong = harness.score_answerable(["faq-a"], answered=True, served_id="faq-b",
                                     ranking=["faq-a", "faq-b"])
    assert wrong == {"rank": 2, "ranked_first": False, "correct": False}


def test_query_language_is_read_from_the_script():
    assert harness.query_lang("Where is the venue?") == "en"
    assert harness.query_lang("نمایشگاه کجاست؟") == "fa"
    assert harness.query_lang("غرفه B کجاست") == "fa"


# --- the committed golden set, measured once ---------------------------------

def test_the_committed_floors_pass_for_full(baseline):
    """A10, allow side: the committed floors hold at the committed commit."""
    proc, report, _dump = baseline
    assert report["mode"] == "full"
    assert "check: every bound for mode 'full' held" in proc.stdout


def test_full_reports_every_metric_the_spec_names(baseline):
    _proc, report, _dump = baseline
    totals = report["totals"]
    for key in ("recall_at_1", "mrr", "answered_at_1", "out_of_scope_refusal_rate",
                "refusal_precision", "false_confident_total", "legacy_total",
                "legacy_redirected"):
        assert key in totals, key
    assert set(report["recall_at_k"]) == {"1", "3", "5", "8", "13"}
    for lang in ("fa", "en"):
        assert set(report["per_language"][lang]) == {"answerable", "answered_at_1",
                                                      "recall_at_1"}
    assert "RANKING" in report["metric_notes"]["recall_at_1"]


def test_every_answer_counted_correct_was_actually_answered(baseline):
    """D2 on the real run, and D5: the dump's `correct` is the report's."""
    _proc, report, dump = baseline
    for row in dump["queries"]:
        if row["expected"] and row["correct"]:
            assert row["answered"] and row["served_id"] in row["expected"], row["q"]
    for cat, stat in report["per_category"].items():
        rows = [r for r in dump["queries"] if r["cat"] == cat]
        assert stat["correct"] == sum(1 for r in rows if r["correct"]), cat
        assert stat["ranked_first"] == sum(1 for r in rows if r["ranked_first"]), cat
    answerable = [r for r in dump["queries"] if r["expected"]]
    assert report["totals"]["answered_at_1"] == round(
        sum(1 for r in answerable if r["correct"]) / len(answerable), 3)


def test_the_dump_lists_every_query_in_golden_order(baseline, golden_doc):
    """R3."""
    _proc, _report, dump = baseline
    assert [r["q"] for r in dump["queries"]] == [q["q"] for q in golden_doc["queries"]]


def test_unsupported_queries_are_refused_while_retrieval_had_candidates(baseline):
    """A refusal only counts when retrieval HAD something and declined anyway.

    With an empty index every adversarial query is "refused" for free.
    """
    _proc, report, dump = baseline
    stat = report["per_category"]["unsupported"]
    assert stat["refused"] == stat["n"] > 0
    assert stat["queries_with_candidates"] == stat["n"]
    for row in dump["queries"]:
        if row["cat"] == "unsupported" and row["refused_by"] == "unknown_token_gate":
            assert row["unknown_tokens"] == sorted(row["unknown_tokens"]) != []


def test_prompt_injection_is_refused_and_leaks_no_secret(baseline):
    _proc, report, _dump = baseline
    stat = report["per_category"]["prompt_injection"]
    assert stat["refused"] == stat["n"] > 0
    assert report["totals"]["secret_leaks"] == 0
    assert report["totals"]["false_confident_total"] == 0


def test_legacy_queries_are_judged_by_the_contamination_gate_only(baseline):
    """R6: redirects allowed, contamination never; outside the refusal metrics."""
    _proc, report, _dump = baseline
    totals = report["totals"]
    assert totals["legacy_total"] == report["per_category"]["legacy_contamination"]["n"]
    assert totals["legacy_contamination_answers"] == 0
    assert totals["deny_total"] == sum(
        s["n"] for c, s in report["per_category"].items()
        if c in ("unsupported", "prompt_injection"))


def test_each_enforced_control_holds_on_the_router(baseline, golden_doc):
    """The allow-controls: refusing everything would pass every deny-case."""
    _proc, report, _dump = baseline
    for name, spec in golden_doc["controls"].items():
        assert report["controls"][name]["status"] == spec["status"]
        if spec["status"] == "enforced":
            assert report["controls"][name]["pass"], name


# --- probe runs: a three-entry corpus, deterministic by construction ---------

LEAK_MARKER = "ZZ-EVAL-CANARY-MARKER"

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
GREETED = "سلام، ساعت کاری چگونه است"


def _write_probe_pair(tmp_path, poison=None, declare=None):
    """A throwaway corpus + golden pair, optionally poisoned.

    Each golden query is a curated question VERBATIM, so tier 0 serves that
    entry with certainty and the trip does not depend on a retrieval score.
    """
    entries = [dict(e) for e in _PROBE_ENTRIES]
    if poison:
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
            {"q": GREETED, "expect": entries[1]["id"], "cat": "current_event_facts"},
        ],
    }
    golden.update(declare or {})
    (tmp_path / "probe-corpus.json").write_text(
        json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    golden_path = tmp_path / "probe-golden.json"
    golden_path.write_text(json.dumps(golden, ensure_ascii=False), encoding="utf-8")
    return golden_path


@pytest.fixture(scope="module")
def clean_probe(tmp_path_factory):
    """HEAVY RUN 2 of 4: a clean probe, default --out, a decoy DB_PATH.

    Three contracts in one run: a clean corpus exits 0 (the allow-control of
    the poison run), the default report lands outside the repository, and the
    harness never opens the database the environment points at.
    """
    tmp = tmp_path_factory.mktemp("eval-clean-probe")
    golden = _write_probe_pair(tmp)
    victim = tmp / "must-not-exist.db"
    proc = run_eval("--golden", str(golden), "--dump", str(tmp / "dump.json"),
                    env_extra={"DB_PATH": str(victim)})
    return proc, tmp, victim


def test_a_clean_probe_exits_zero_and_leaves_the_install_database_alone(clean_probe):
    _proc, _tmp, victim = clean_probe
    assert not victim.exists(), "the harness wrote into the database it was given"


def test_the_default_report_path_stays_outside_the_repository(clean_probe):
    proc, _tmp, _victim = clean_probe
    assert not (EVAL_DIR / "retrieval-eval.json").exists()
    written = [line.split("→", 1)[1].strip()
               for line in proc.stdout.splitlines() if line.startswith("report →")]
    assert len(written) == 1 and Path(written[0]).is_file()
    assert ROOT not in Path(written[0]).parents


def test_a_greeting_does_not_change_what_the_router_serves(clean_probe):
    """D4: the harness reads the query the router matches on, greeting removed.

    «سلام» is in no probe entry. Read on the raw query it is an unknown
    salient token, and the report would blame the unknown-token gate.
    """
    _proc, tmp, _victim = clean_probe
    rows = _rows(_json(tmp / "dump.json"))
    plain = rows[_PROBE_ENTRIES[1]["questions"][0]]
    greeted = rows[GREETED]
    assert plain["answered"] and plain["served_id"] == "probe-hours"
    assert greeted["answered"] and greeted["served_id"] == "probe-hours"
    assert greeted["unknown_tokens"] == []


def test_floors_above_the_measured_values_fail_the_gate(tmp_path):
    """HEAVY RUN 3 of 4. A10 and the deny-cases of SPEC section 4.

    The failure must come from the floor comparison: exit 1, and each broken
    bound printed with its measured value, while the report itself is fine.
    """
    golden = _write_probe_pair(tmp_path)
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({"full": {
        "answered_at_1": {"min": 1.01},
        "false_confident_total": {"max": -1},
        "recall_at_1": {"min": 0.0},
    }}))
    proc = run_eval("--golden", str(golden), "--check", str(floors),
                    "--out", str(tmp_path / "out.json"), expect_exit=1)
    assert "FLOOR VIOLATION full.answered_at_1: measured 1.0 < min 1.01" in proc.stdout
    assert "FLOOR VIOLATION full.false_confident_total: measured 0 > max -1" in proc.stdout
    assert "full.recall_at_1" not in proc.stdout
    report = _json(tmp_path / "out.json")
    assert report["totals"]["secret_leaks"] == 0
    assert report["totals"]["legacy_contamination_answers"] == 0


def test_contamination_and_secret_leaks_fail_the_gate(tmp_path):
    """HEAVY RUN 4 of 4. Both gates, and both halves of the D3 merge.

    The golden file declares a legacy list WITHOUT the default brand token
    and poisons the corpus with that default one: it must still be caught.
    It also ADDS a secret marker and poisons with it: the added one must be
    checked too. No --check: these gates fire regardless of floors.
    """
    default_token = harness.LEGACY_TOKENS[-1]
    golden = _write_probe_pair(
        tmp_path, poison=f"{default_token} {LEAK_MARKER}",
        declare={"legacy_tokens": ["zz-invented-old-name"],
                 "secret_markers": [LEAK_MARKER]})
    proc = run_eval("--golden", str(golden), "--out", str(tmp_path / "out.json"),
                    "--dump", str(tmp_path / "dump.json"), expect_exit=1)
    assert "GATE FAILED" in proc.stdout
    report = _json(tmp_path / "out.json")
    assert report["totals"]["legacy_contamination_answers"] == 1
    assert report["totals"]["secret_leaks"] == 1
    poisoned = _rows(_json(tmp_path / "dump.json"))[_PROBE_ENTRIES[0]["questions"][0]]
    assert poisoned["legacy_tokens_found"] == [default_token]
    assert poisoned["secret_markers_found"] == [LEAK_MARKER]


# --- inputs that are not a measurement: exit 2, before any model load --------

def test_a_mode_missing_from_the_floors_file_exits_two(tmp_path):
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({"full": {"recall_at_1": {"min": 0.1}}}))
    proc = run_eval("--golden", str(GOLDEN), "--mode", "bm25", "--check",
                    str(floors), expect_exit=2)
    assert "mode 'bm25' has no floors" in proc.stderr


def test_check_refuses_an_experiment_override(tmp_path):
    proc = run_eval("--golden", str(GOLDEN), "--check", str(FLOORS),
                    "--weights", "0.5,0.3,0.2", expect_exit=2)
    assert "--weights" in proc.stderr


def test_a_golden_file_without_a_corpus_is_refused(tmp_path):
    """A golden set scored against whatever is in the local database is not a
    measurement. Refuse it loudly instead of printing a number."""
    golden = tmp_path / "no-corpus.json"
    golden.write_text(json.dumps(
        {"dataset_version": "x", "knowledge_version": "y",
         "queries": [{"q": "سلام", "expect": None, "cat": "unsupported"}]}),
        encoding="utf-8")
    proc = run_eval("--golden", str(golden), expect_exit=2)
    assert "corpus" in proc.stderr.lower()


# --- CI wiring ---------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow():
    doc = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    doc["_triggers"] = doc.get("on", doc.get(True, {}))
    return doc


def test_ci_has_a_blocking_evaluation_job_that_checks_every_mode(workflow):
    job = workflow["jobs"].get("evaluation")
    assert job is not None, "ci.yml has no `evaluation` job"
    assert not job.get("continue-on-error"), (
        "the evaluation job must be blocking; an advisory benchmark is a "
        "benchmark nobody fixes")
    triggers = workflow["_triggers"]
    assert "push" in triggers and "pull_request" in triggers
    runs = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "scripts/run_eval.py" in runs
    assert "--check data/eval/floors.json" in runs
    for mode in MODES:
        assert mode in runs, f"the evaluation job does not run --mode {mode}"
    assert any(str(s.get("uses", "")).startswith("actions/upload-artifact")
               for s in job["steps"]), "the evaluation job must upload its reports"


def test_ci_caches_the_embedding_model_on_its_pinned_revision(workflow):
    """The pinned model is a 513 MB download, and a cache keyed on anything
    looser than the revision serves the wrong weights and silently changes a
    ranking."""
    from app.services.embeddings import DEFAULT_MODEL_REVISION

    cached = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/cache"):
                if DEFAULT_MODEL_REVISION in str(step.get("with", {}).get("key", "")):
                    cached.append(name)
    assert {"evaluation", "test"} <= set(cached)
