"""Tier 2 "converse" mode: small talk answered BY THE MODEL, inside firewalls.

PRODUCT DECISION (2026-08-31): every greeting, self-introduction, meta
question about the assistant (its name, who it is, what it can do), thanks,
goodbye and yes/no reply is answered BY THE MODEL — never by canned local
text. Before this, the selection prompt only knew "answer" | "options" |
"none", so small talk deferring to Tier 2 died as "none" and a visitor who
said «سلام» met the cold out-of-scope refusal.

THE FEATURE under test: `build_selection_prompt` teaches the model a fourth
mode "converse" with its own grounding firewall, and `select_records`
accepts it, vets the lead, and flags explicit offer-questions with
"proposal" so the router can store the replay query for a "yes" answer.

THE AI PROVIDER IS STUBBED IN EVERY TEST. `padyar_ai.generate` is replaced
on the process-wide instance (the pattern tests/test_grounded_selection.py
established), so it does not matter whether answer.py imports the wrapper at
module level or inside the function — no test in this file can reach a
network.
"""
import json

import pytest
from fastapi.testclient import TestClient


# The exact strings under test. Pinned here verbatim so a wording regression
# is a test failure, not a silent tone change.
WARM_REFUSAL_FA = "راستش متوجه منظورت نشدم. می‌تونی سؤالت رو یه جور دیگه بپرسی؟"
OLD_REFUSAL_FA_FRAGMENT = "فقط می‌توانم"
OLD_REFUSAL_EN_FRAGMENT = "I can only help"

GREETING_LEAD = "سلام! من دستیار پادیار هستم."
OFFER_LEAD = "می‌خوای لیست شرکت‌ها رو بگم؟"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh install with no content: converse must not need a corpus."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "converse.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        yield c
    security._chat_rate_limits.clear()


def _stub_provider(monkeypatch, payload: str):
    """Replace `padyar_ai.generate` on the ONE process-wide wrapper instance.

    Patching the instance attribute (not a module global) means the stub is
    reached whether answer.py imports the wrapper at module scope or inside
    the function — the test can never accidentally hit a provider.
    """
    from app.services.ai import wrapper
    from app.services.ai.request import AIResponse

    async def fake_generate(messages, **kw):
        return AIResponse(content=payload, finish_reason="stop",
                          task=kw.get("task", "chat"), provider_type="stubprov",
                          model="stub-model", tokens_total=42, cost=0.001)

    monkeypatch.setattr(wrapper.padyar_ai, "generate", fake_generate)


def _decision(client, monkeypatch, payload, history=None):
    """Run answer.select_records() directly against the scripted provider.

    Candidates are plain dicts on purpose: the converse path must not depend
    on retrieval having found anything meaningful — a greeting matches
    nothing well, which is exactly why it defers to the model.
    """
    import asyncio
    from app.services import answer

    _stub_provider(monkeypatch, payload)
    cands = [
        {"id": "co-alfa", "title": "شرکت آلفا",
         "text": "معرفی شرکت آلفا: فعال در هوش مصنوعی.", "score": 0.5},
        {"id": "co-beta", "title": "شرکت بتا",
         "text": "معرفی شرکت بتا: سازنده سامانه‌های صنعتی.", "score": 0.4},
    ]
    return asyncio.run(
        answer.select_records("سلام", cands, history or [], "fa"))


# ── The prompt teaches the fourth mode and its firewall ──────────────────

def test_the_prompt_teaches_the_converse_mode_and_its_firewall(client):
    """With candidates AND without them, the prompt must carry the converse
    mode line and the grounding firewall sentence. A greeting often matches
    nothing, so the prompt is asserted on the empty-candidates shape too."""
    from app.services.answer import build_selection_prompt

    for candidates in ([], [{"id": "co-alfa", "title": "شرکت آلفا",
                             "text": "معرفی شرکت آلفا.", "score": 0.5}]):
        prompt = build_selection_prompt(candidates, [], "fa")
        assert 'mode "converse"' in prompt, prompt
        assert "greeting" in prompt, prompt
        # The firewall sentence: the lead is limited to the assistant's own
        # identity plus what HISTORY already said — never record facts.
        assert "ONLY the assistant's own name" in prompt, prompt
        assert ("no numbers, dates, booth numbers, phone numbers, prices, "
                "or company names") in prompt, prompt
    # And the schema line offers all four modes.
    assert '"answer" | "options" | "converse" | "none"' in \
        build_selection_prompt([], [], "fa")


# ── The parser: what a converse reply must survive ───────────────────────

def test_a_clean_converse_lead_is_accepted_without_a_proposal(client, monkeypatch):
    """The greeting case: accepted, ids empty, and no replay proposal —
    «سلام! من دستیار پادیار هستم.» offers nothing, so the router must not
    store a replay query for it."""
    decision = _decision(client, monkeypatch, json.dumps(
        {"mode": "converse", "ids": [], "lead": GREETING_LEAD,
         "reason": "greeting"}))
    assert decision is not None, decision
    assert decision["mode"] == "converse", decision
    assert decision["lead"] == GREETING_LEAD, decision
    assert decision["ids"] == [], decision
    assert decision["proposal"] is False, decision


def test_an_explicit_offer_question_sets_the_proposal_flag(client, monkeypatch):
    """«می‌خوای لیست شرکت‌ها رو بگم؟» is an offer-question: it ends in a
    question mark AND offers to tell something. Only that shape may set
    "proposal" — the router stores the replay query on it."""
    decision = _decision(client, monkeypatch, json.dumps(
        {"mode": "converse", "ids": [], "lead": OFFER_LEAD,
         "reason": "offered the list"}))
    assert decision["mode"] == "converse", decision
    assert decision["proposal"] is True, decision


def test_an_empty_converse_lead_falls_back_to_mode_none(client, monkeypatch):
    """A converse turn with nothing to say is not a decision — it degrades to
    "none" so the turn still walks the ordinary answer path."""
    decision = _decision(client, monkeypatch, json.dumps(
        {"mode": "converse", "ids": [], "lead": "   ", "reason": ""}))
    assert decision is not None, decision
    assert decision["mode"] == "none", decision
    assert decision["ids"] == [], decision
    assert "proposal" not in decision, decision


def test_an_oversized_converse_lead_falls_back_to_mode_none(client, monkeypatch):
    """The lead is the whole reply, but it is still 1-2 sentences, not a
    paragraph: 250 characters is past the 200-char bound and must degrade to
    "none", not ship as a wall of model-written text."""
    decision = _decision(client, monkeypatch, json.dumps(
        {"mode": "converse", "ids": [], "lead": "س" * 250, "reason": ""}))
    assert decision["mode"] == "none", decision
    assert "proposal" not in decision, decision


def test_a_converse_lead_carrying_a_digit_is_rejected(client, monkeypatch):
    """No digits in a converse lead, in ANY script. A number is the one fact
    shape this product has been burned by repeatedly (hall numbers, phone
    numbers, prices), and a greeting has no legitimate use for one."""
    for lead in ("خوشحالم که 2 بار اینجا بودم.",  # ASCII digit
                 "در غرفه ۳ منتظرت هستم."):  # Persian digit
        decision = _decision(client, monkeypatch, json.dumps(
            {"mode": "converse", "ids": [], "lead": lead, "reason": ""}))
        assert decision["mode"] == "none", (lead, decision)
        assert "proposal" not in decision, (lead, decision)


def test_converse_drops_any_id_the_model_attached(client, monkeypatch):
    """The model may hedge by naming a record next to its small talk. A
    converse turn serves no record — the ids are dropped, not trusted."""
    decision = _decision(client, monkeypatch, json.dumps(
        {"mode": "converse", "ids": ["co-alfa"], "lead": GREETING_LEAD,
         "reason": "greeting"}))
    assert decision["mode"] == "converse", decision
    assert decision["ids"] == [], decision


def test_the_converse_decision_carries_the_accounting_columns(client, monkeypatch):
    """The provider billed for the call whatever it decided; the router sums
    these columns, so a converse decision carries them like every other
    mode."""
    decision = _decision(client, monkeypatch, json.dumps(
        {"mode": "converse", "ids": [], "lead": GREETING_LEAD,
         "reason": "greeting"}))
    for key in ("mode", "lead", "proposal", "tokens", "cost",
                "provider", "model"):
        assert key in decision, (key, decision)


# ── The warm refusal ──────────────────────────────────────────────────────

def test_the_out_of_scope_refusal_is_now_the_warm_rephrase(client):
    """The exact string the out-of-scope path serves (chat.py reads
    scope.refusal_text) is the new warm sentence, in both languages, and the
    cold sentence is gone from the module's user-facing constants."""
    from app.services import scope

    assert scope.refusal_text("fa") == WARM_REFUSAL_FA
    assert scope.refusal_text("en"), "the English equivalent must exist"
    assert OLD_REFUSAL_FA_FRAGMENT not in scope.refusal_text("fa")
    assert OLD_REFUSAL_EN_FRAGMENT not in scope.refusal_text("en")

    blob = json.dumps(
        {k: str(v) for k, v in vars(scope).items() if k.isupper()},
        ensure_ascii=False)
    assert OLD_REFUSAL_FA_FRAGMENT not in blob, blob
    assert OLD_REFUSAL_EN_FRAGMENT not in blob, blob

    # Still a DIFFERENT sentence from no-answer and hedge (scope.py's own
    # contract): three messages, three meanings.
    assert scope.refusal_text("fa") != scope.no_answer_text("fa")
    assert scope.refusal_text("fa") != scope.hedge_text("fa")


def test_the_system_prompt_instructs_the_model_with_the_same_warm_refusal(client):
    """The string the model is TOLD to say and the string the code says must
    stay one string: build_system_prompt() embeds scope.refusal_text()
    verbatim, so both flip together."""
    from app.services import openai as ai_service
    from app.services import scope

    prompt = ai_service.build_system_prompt()
    assert scope.refusal_text("fa") in prompt, prompt[:400]
    assert scope.refusal_text("en") in prompt, prompt[:400]
