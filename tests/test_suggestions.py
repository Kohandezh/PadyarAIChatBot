"""Context-based suggestion chips — the assistant's follow-up engine.

After (almost) every answer the chatbot offers 3-4 tappable follow-up
questions built from the conversation context, so a visitor never has to
think about what to ask next (product rule: every action <= 3 clicks).

Three layers, all wired here:
  * the engine (app/services/suggestions.py) — deterministic, zero AI calls;
  * the response field (ChatResponse.suggestions) — additive, backward safe;
  * the frontend — one #chat-suggestions container per rendered chat page,
    filled by static/chat/core.js from data.suggestions.

The frontend half follows tests/test_public_ui.py's file-reading pattern so
it runs without the FastAPI app, plus one real render through
render_theme_index() to prove the partial is actually included.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import ChatResponse
from app.services import suggestions as sug
from app.services.themes import render_theme_index

ROOT = Path(__file__).resolve().parent.parent
INOTEX = ROOT / "themes" / "inotex"
BASE = ROOT / "themes" / "base"
CORE_JS = ROOT / "static" / "chat" / "core.js"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _invariants(out: list) -> None:
    """Every rule that must hold for ANY context the router may pass."""
    assert 3 <= len(out) <= sug.MAX_SUGGESTIONS, out
    assert all(isinstance(q, str) and q.strip() for q in out), out
    assert all(len(q) <= 32 for q in out), out
    # No duplicates — a repeated chip reads as a broken answer.
    assert len(set(out)) == len(out), out


# Every kind the router can report, plus the shapes around them.
CONTEXTS = [
    {"kind": "unknown"},
    {"kind": "guide_fact"},
    {},  # no kind at all — the engine must still answer something useful
    {"kind": "entry", "entry": {"title": "شرکت فناوران پارس"},
     "hall": "سالن ۳", "category": "هوش مصنوعی"},
    {"kind": "entry", "entry": {"title": "شرکت فناوران پارس"}},
    {"kind": "entry"},  # kind says entry but nothing was served
    {"kind": "options", "options_titles": ["شرکت الف", "شرکت ب", "شرکت ج"]},
    {"kind": "converse", "conversation_kind": "smalltalk"},
    {"kind": "converse", "conversation_kind": "self_intro"},
    {"kind": "converse", "conversation_kind": "none"},
    None,  # the router may pass nothing at all
]


# The parameter is `ctx`, never `context`: pytest-playwright ships a
# `context` fixture (the browser context), and tests/test_suite_isolation.py
# forbids shadowing it suite-wide.
@pytest.mark.parametrize("ctx", CONTEXTS)
def test_every_context_kind_returns_3_to_4_short_unique_questions(ctx):
    _invariants(sug.build_suggestions(ctx))


def test_max_suggestions_is_four():
    # Four chips is the cap the UI is designed around.
    assert sug.MAX_SUGGESTIONS == 4


def test_entry_with_hall_offers_the_booth_question():
    out = sug.build_suggestions({
        "kind": "entry",
        "entry": {"title": "شرکت فناوران پارس"},
        "hall": "سالن ۳",
        "category": "هوش مصنوعی",
    })
    assert any("غرفه" in q and "کجاست" in q for q in out), out
    # The company's own web presence — the most common follow-up.
    assert any("وب‌سایت" in q for q in out), out
    # Same-category neighbours, when the category is known.
    assert any("دیگه کیا هستن" in q for q in out), out


def test_entry_without_hall_skips_the_booth_question():
    # A booth question with no hall to point at would be a dead chip.
    out = sug.build_suggestions({
        "kind": "entry", "entry": {"title": "شرکت فناوران پارس"},
    })
    assert not any("غرفه" in q for q in out), out


def test_a_very_long_title_still_fits_the_chip():
    out = sug.build_suggestions({
        "kind": "entry",
        "entry": {"title": "شرکت فناوران نوین پردازش هوشمند پارس جنوب آسیا"},
        "hall": "سالن ۳",
    })
    _invariants(out)


def test_options_list_offers_more_and_the_first_option():
    out = sug.build_suggestions({
        "kind": "options", "options_titles": ["شرکت الف", "شرکت ب"],
    })
    assert "بیشتر" in out, out
    assert any("رو بگو" in q for q in out), out


def test_converse_smalltalk_orients_the_visitor():
    # A chatty visitor gets pointed at the event itself — exactly these
    # three, not an evergreen mix.
    for kind in ("smalltalk", "self_intro"):
        out = sug.build_suggestions({"kind": "converse", "conversation_kind": kind})
        assert set(out) == set(sug.ORIENTATION_SUGGESTIONS), (kind, out)


def test_unknown_rotates_the_evergreens():
    # Rotation, not the same four every time: consecutive unknown turns
    # offer different follow-ups while staying inside the evergreen set.
    first = sug.build_suggestions({"kind": "unknown"})
    second = sug.build_suggestions({"kind": "unknown"})
    assert first != second, (first, second)
    for out in (first, second):
        assert set(out) <= set(sug.EVERGREEN_SUGGESTIONS), out


def test_evergreens_live_in_a_module_level_tuple():
    # The brief's rule: evergreens come from a module-level tuple, every
    # string a valid chip on its own.
    assert isinstance(sug.EVERGREEN_SUGGESTIONS, tuple)
    assert len(sug.EVERGREEN_SUGGESTIONS) >= sug.MAX_SUGGESTIONS
    for q in sug.EVERGREEN_SUGGESTIONS:
        assert len(q) <= 32, q
    # The orientation set leads with evergreens the visitor has not seen.
    assert set(sug.ORIENTATION_SUGGESTIONS) <= set(sug.EVERGREEN_SUGGESTIONS)


# ── ChatResponse field ────────────────────────────────────────────────────

def test_chat_response_accepts_and_dumps_suggestions():
    res = ChatResponse(
        type="entry", text="این شرکت در سالن ۳ است.",
        confidence=0.9, source="selection",
        suggestions=["غرفه کجاست؟", "ساعت بازدید چند است؟"],
    )
    dumped = res.model_dump()
    assert dumped["suggestions"] == ["غرفه کجاست؟", "ساعت بازدید چند است؟"]


def test_chat_response_without_suggestions_keeps_its_old_shape():
    # Additive field: an old caller (and an old frontend) sees an empty
    # list, everything else byte-for-byte what it always was.
    res = ChatResponse(type="entry", text="سلام", confidence=0.5, source="questions")
    dumped = res.model_dump()
    assert dumped["suggestions"] == []
    assert set(dumped) == {
        "type", "text", "video_url", "confidence", "source", "options", "suggestions",
    }


# ── Frontend wiring ───────────────────────────────────────────────────────

@pytest.mark.parametrize("theme", ["inotex", "base"])
def test_rendered_chat_page_carries_the_suggestions_container_once(theme):
    # The real render, not the partial file: if the include is removed from
    # the layout the page silently loses its chips — this must fail there.
    html = render_theme_index(theme, {})
    assert html.count('id="chat-suggestions"') == 1, theme
    assert 'class="chat-suggestions' in html, theme
    assert 'aria-live="polite"' in html, theme


def test_the_inotex_override_extends_the_base_container():
    # inotex ships its own partial; it must keep the contract core.js
    # targets (same id/class) while adding its own hook class.
    inx = read(INOTEX / "partials" / "suggestions.html")
    assert 'id="chat-suggestions"' in inx
    assert 'class="chat-suggestions' in inx


def test_the_chat_layout_includes_the_partial_by_name():
    # Same inclusion mechanism as messages.html/video.html.
    index = read(BASE / "partials" / "index.html")
    assert '{% include "suggestions.html" %}' in index


def test_inotex_chip_colors_come_from_branding_tokens():
    """The binding theme rule: every chip color reads from a --wl-* branding
    custom property (directly or through the theme's semantic tokens, which
    all feed from --wl-*), with palette fallbacks in var(). No hardcoded
    brand colors, tappable >= 40px, hidden when empty."""
    css = read(INOTEX / "static" / "style.css")
    block = css.split(".chat-suggestions", 1)[1][:2400]
    assert "min-height: 40px" in block
    assert ":empty" in css.split(".chat-suggestions", 1)[1][:600]
    # Surfaces, text and borders all via tokens.
    for prop in ("background:", "color:", "border:"):
        for line in block.splitlines():
            if line.strip().startswith(prop) and "var(--" not in line and ":" in line:
                if "0 2px 0 rgba(0, 0, 0, .5)" not in line:  # shadow only
                    pytest.fail(f"hardcoded color: {line!r}")
    assert "var(--color-surface-primary)" in block


def test_core_js_renders_and_submits_the_chips():
    """The renderer lives in the SHARED engine (both themes load core.js),
    chips submit through the existing send path, and a new turn retires the
    previous chips."""
    js = read(CORE_JS)
    assert "function renderSuggestions" in js
    # Wired to the /chat response, next to the options renderer.
    assert "renderSuggestions(data.suggestions)" in js
    # A chip tap reuses sendPreset — the same path the options chips and
    # the starter questions already take.
    renderer = js.split("function renderSuggestions", 1)[1][:1400]
    assert "sendPreset(" in renderer
    # The row belongs to the latest answer only.
    assert "renderSuggestions([])" in js


# ── Router wiring: real answers carry real chips ─────────────────────────

@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "sugg.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES ('e1', 'شرکت نمونه', 'شرکت نمونه در زمینه"
                     " هوش مصنوعی فعالیت می‌کند.', '')")
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES ('شرکت نمونه چیست؟', 'e1', '')")
        conn.commit()
        conn.close()
        # The retrieval index was built at app boot, BEFORE these rows
        # existed — rebuild so Tier 0 can see the curated question.
        from app.services.search import reindex_and_publish
        reindex_and_publish()
        yield c
    security._chat_rate_limits.clear()


def test_an_answer_served_through_chat_carries_suggestions(chat_client):
    r = chat_client.post("/chat",
                         json={"message": "شرکت نمونه چیست؟", "lang": "fa"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("suggestions"), body
    assert all(isinstance(x, str) and x for x in body["suggestions"])
    assert len(body["suggestions"]) <= 4
