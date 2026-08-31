"""Contract tests for the conversation-scenario fixture and its loader.

The full run is a measurement baseline whose scenarios are deliberately red
today (that red IS the to-do list), so scripts/run_eval.py --conversations
is run on demand rather than wired into pytest — the harness itself carries
the behavioural coverage. What must never drift silently is the fixture's
shape and the verbatim strings the conversation-state work is built
against; these tests pin both.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_eval import _validate_conversation_spec  # noqa: E402

FIXTURE = ROOT / "data" / "eval" / "conversations.json"


def _spec():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_a_valid_scenario_array():
    spec = _spec()
    # Raises SystemExit on any shape error, including unknown operators.
    _validate_conversation_spec(spec)
    assert [s["name"] for s in spec] == [
        "self-intro-vs-company",
        "affirmative-replay",
        "name-recall",
        "gibberish",
        "smalltalk",
    ]


def test_rejects_unknown_step_operator():
    bad = [{"name": "x", "steps": [{"say": "سلام", "expect_source": "local"}]}]
    try:
        _validate_conversation_spec(bad)
    except SystemExit:
        return
    raise AssertionError("unknown operator was accepted")


def test_fixture_keeps_the_verbatim_target_strings():
    spec = {s["name"]: s for s in _spec()}

    # Self-intro must not trigger a company lookup, and the gate must not
    # break the real lookup that follows it.
    intro = spec["self-intro-vs-company"]
    assert "روحانی نژاد" in intro["steps"][0]["expect_not_contains"]
    assert "غرفه" in intro["steps"][0]["expect_not_contains"]
    assert intro["steps"][1]["expect_contains"] == ["روحانی نژاد"]
    assert intro["seed"][0]["questions"] == ["شرکت سینا چیست؟"]

    # «بگو» must replay the offered list, not re-introduce the bot.
    replay = spec["affirmative-replay"]
    assert replay["steps"][0].get("expect_options") is True
    assert replay["steps"][1].get("expect_options") is True
    assert len(replay["seed"]) == 3

    # Name recall, gibberish, smalltalk — the exact sentences from the spec.
    assert spec["name-recall"]["steps"][1]["expect_contains"] == ["سینا"]
    assert spec["gibberish"]["steps"][0]["expect_contains"] == ["متوجه"]
    smalltalk = spec["smalltalk"]["steps"][0]["expect_not_contains"]
    assert "غرفه" in smalltalk
    # The cold no-answer refusal (scope.no_answer_text) is today's failure
    # and must stay fenced off: smalltalk wants a warm reply, not this
    # sentence.
    assert "متاسفانه در این خصوص نمی‌توانم پاسخی به شما بدهم" in smalltalk
    # Every seeded company name, from every scenario, is fenced off here.
    seeded_names = [r["title"] for s in _spec() for r in s.get("seed", [])]
    for name in seeded_names:
        assert name in smalltalk, f"smalltalk does not fence off {name!r}"
