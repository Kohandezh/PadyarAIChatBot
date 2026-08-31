"""The rolling summary must remember the visitor's stated name.

WHAT IT FIXES (production, 2026-08-31). A visitor says «اسم من سینا هست»,
keeps talking, and later asks «اسمم چیه؟». By then the introduction lives
in the folded part of the conversation, and the summarizer's prompt said
"Drop small talk" — a self-introduction reads as small talk, so the name
was dropped with it and the bot had no idea who it was talking to. The
prompt now carries one exception: a stated name always survives as the
line «نام بازدیدکننده: X». get_summary() stays the only reader — the name
line travels inside the stored summary, into the history block the model
already sees.

OFFLINE like test_conversation_summary.py: the model call is stubbed at
the wrapper boundary (`padyar_ai.classify`), the same _capture_summarizer
pattern that file established.
"""
import pytest
from fastapi.testclient import TestClient

from app.services import conversations


CONV = "conv-name"


class _Reply:
    """The shape `padyar_ai.classify` returns, with only what we read."""

    def __init__(self, content):
        self.content = content
        self.tokens_total = 5
        self.cost = 0.0


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "name.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "false")
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _capture_summarizer(monkeypatch, reply="خلاصهٔ گفتگو."):
    """Stub the routed classify task — the pattern from
    tests/test_conversation_summary.py, unchanged."""
    from app.services.ai import wrapper
    seen = []

    async def fake_classify(query, system_prompt="", **kwargs):
        seen.append({"query": query, "system_prompt": system_prompt})
        return _Reply(reply)

    monkeypatch.setattr(wrapper.padyar_ai, "classify", fake_classify)
    return seen


# ── The prompt carries the name-capture instruction ──────────────────────

def test_the_prompt_makes_the_summarizer_keep_a_stated_name():
    prompt = conversations._summary_prompt("fa")
    assert "نام بازدیدکننده:" in prompt, prompt
    # The forms a self-introduction actually takes in Persian.
    for form in ("اسم من X هست", "اسمم X", "من X هستم", "من X ام"):
        assert form in prompt, prompt
    # The rules that stop a hallucinated name.
    assert "latest" in prompt.lower(), prompt
    assert "never invent" in prompt.lower(), prompt


def test_the_name_instruction_survives_the_english_variant_too():
    """One builder builds both prompts, so the exception cannot silently
    exist only for Persian conversations."""
    assert "نام بازدیدکننده:" in conversations._summary_prompt("en")


def test_the_prompt_keeps_its_existing_contract():
    """The name line is an addition, not a rewrite: every instruction the
    summary format already guaranteed must still be there."""
    prompt = conversations._summary_prompt("fa")
    assert "UPDATED summary" in prompt
    assert "Persian" in prompt
    assert "Drop small talk" in prompt
    assert "Add NOTHING" in prompt
    assert "data, not instructions" in prompt


# ── A stored name line round-trips through get_summary ───────────────────

async def test_a_stated_name_survives_the_round_trip(client, monkeypatch):
    """The model did what the prompt asked; nothing between its reply and
    the reader may mangle the name line."""
    name_line = "نام بازدیدکننده: سینا"
    _capture_summarizer(
        monkeypatch, reply=f"بازدیدکننده دنبال ساعت کاری بود.\n{name_line}")
    for i in range(7):
        conversations.append_visitor_message(CONV, f"پرسش {i}")
        conversations.append_assistant_message(
            CONV, f"پاسخ {i}", source="local", confidence=0.9)

    summary = await conversations.update_summary(CONV)

    assert name_line in summary, summary
    assert conversations.get_summary(CONV) == summary
