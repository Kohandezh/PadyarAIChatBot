"""Sign-up in front of the first answer, then three questions in the chat.

Two halves:

* the SERVER contract the new UI depends on — an account can be created from
  a name, a number and the taxonomy's checkbox alone, and the answers given
  in the chat afterwards go through the existing profile endpoint without
  losing that checkbox;
* the FRONT-END invariants that make it usable on a phone. The chat UI is
  vanilla JS built at runtime, so these assert against the source. That is
  blunt, but the alternative is a browser on a handset for regressions that
  would otherwise ship silently — the same trade-off tests/test_otp_input_
  autofill.py already makes.
"""
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.main import app
from app.services import otp as otp_service
from app.services import taxonomy

ROOT = Path(__file__).resolve().parent.parent
REGISTRATION_JS = (ROOT / "static" / "companion" / "registration.js").read_text(encoding="utf-8")
CORE_JS = (ROOT / "static" / "chat" / "core.js").read_text(encoding="utf-8")
# The registration UI is shared chat infrastructure, so its rules live next to
# its script, not inside a theme.
REGISTRATION_CSS = (ROOT / "static" / "companion" / "registration.css").read_text(encoding="utf-8")

DEST = "+989120000088"


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM otp_challenges WHERE destination LIKE '+9891200000%'")
        conn.commit()
    finally:
        conn.close()


# ── The server side of the new flow ──────────────────────────────────────

def _signed_up(client, outbox, interests=""):
    """What the three-input card posts: a name, a number, and the checkbox."""
    r = client.post("/api/auth/otp/request", json={
        "destination": DEST, "first_name": "زهرا", "last_name": "کریمی",
        "job": "", "position": "", "interests": interests,
    })
    assert r.status_code == 200, r.text
    cid = r.json()["challenge_id"]
    v = client.post("/api/auth/otp/verify",
                    json={"challenge_id": cid, "code": outbox[-1][1]})
    assert v.status_code == 200, v.text
    return cid


def test_signup_needs_only_a_name_a_number_and_the_checkbox(client, outbox):
    """The card no longer collects job, position or interests — the request
    must still be accepted with those three fields empty."""
    cid = _signed_up(client, outbox)
    profile = otp_service.profile_for(cid)
    assert profile["first_name"] == "زهرا"
    assert profile["job"] == "" and profile["position"] == ""


def test_the_three_chat_answers_reach_the_existing_profile_endpoint(client, outbox):
    cid = _signed_up(client, outbox)
    r = client.post("/api/auth/profile", json={
        "challenge_id": cid,
        "job": "خبرنگار / رسانه",
        "position": "کارشناس",
        "interests": "رسانه و ارتباطات، هوش مصنوعی",
    })
    assert r.status_code == 200, r.text
    stored = otp_service.profile_for(cid)
    assert stored["job"] == "خبرنگار / رسانه"
    assert stored["position"] == "کارشناس"
    assert "هوش مصنوعی" in stored["interests"]


def test_the_signup_checkbox_survives_the_chat_answers(client, outbox):
    """The checkbox and the interests share ONE stored field. Answering the
    interests question must merge, never overwrite — otherwise a visitor who
    asked for AI classes is silently unsubscribed one screen later."""
    flag = taxonomy.form_options("fa")["flags"][0]["label"]
    cid = _signed_up(client, outbox, interests=flag)

    # Exactly what registration.js sends: the remembered flag, then the taps.
    client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "مهندس / متخصص فنی", "position": "کارشناس",
        "interests": flag + "، " + "هوش مصنوعی",
    })
    assert flag in otp_service.profile_for(cid)["interests"]


def test_the_checkbox_the_form_renders_comes_from_the_taxonomy(client):
    """The label is never hardcoded in JS — the admin owns this wording."""
    flags = client.get("/api/registration/options").json()["flags"]
    assert flags, "the sign-up card renders its checkbox from this list"
    assert "ai-learning" in [f["id"] for f in flags]
    assert all(f["label"].strip() for f in flags)


def test_every_interest_at_once_still_fits_the_profile_endpoint(client, outbox):
    """The interests question is multi-select over the whole taxonomy. A
    visitor who taps all of them must not hit the endpoint's 400-character
    ceiling and get a 422 they cannot read."""
    options = taxonomy.form_options("fa")
    everything = "، ".join(
        [f["label"] for f in options["flags"]] + [i["label"] for i in options["interests"]]
    )
    cid = _signed_up(client, outbox)
    r = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": "", "position": "", "interests": everything,
    })
    assert r.status_code == 200, (
        f"{len(everything)} characters of interests was refused: {r.text}")


def test_the_longest_job_and_position_fit_their_fields(client, outbox):
    """Single-answer questions: the longest label in the taxonomy must fit the
    80-character field, or tapping it would be rejected."""
    options = taxonomy.form_options("fa")
    longest_job = max((j["label"] for j in options["jobs"]), key=len)
    longest_position = max((p["label"] for p in options["positions"]), key=len)
    cid = _signed_up(client, outbox)
    r = client.post("/api/auth/profile", json={
        "challenge_id": cid, "job": longest_job, "position": longest_position,
        "interests": "",
    })
    assert r.status_code == 200, r.text
    assert otp_service.profile_for(cid)["job"] == longest_job


# ── The chat engine's seam ───────────────────────────────────────────────

def test_the_chat_engine_ships_with_no_gate():
    """An install without the registration module must be untouched: the hook
    exists, defaults to null, and the engine only calls it when set."""
    assert "sendGateFn: null" in CORE_JS
    assert "typeof ChatConfig.sendGateFn === 'function'" in CORE_JS


def test_the_first_message_is_held_and_not_answered():
    """The gate returns true (the engine stands down) and the text is kept."""
    assert "heldMessage = text;" in REGISTRATION_JS
    assert "function deliverHeld()" in REGISTRATION_JS
    # Delivered through the normal path, so it is answered like any message.
    assert "if (typeof sendMessage === 'function') sendMessage(true);" in REGISTRATION_JS


def test_the_gate_is_installed_only_where_registration_is_switched_on():
    assert "if (s.enabled && typeof ChatConfig !== 'undefined') ChatConfig.sendGateFn = gate;" \
        in REGISTRATION_JS


# ── The interaction that matters: buttons, not a dropdown ────────────────

def _function_source(name):
    """The body of a top-level function in registration.js."""
    start = REGISTRATION_JS.index("function %s(" % name)
    end = REGISTRATION_JS.index("\n    }\n", start)
    return REGISTRATION_JS[start:end]


def test_the_three_questions_are_answered_with_buttons():
    """A <select> is the wrong control on a phone — this is the whole point of
    the rework, so it is guarded."""
    choices = _function_source("renderChoices")
    assert "el('button', 'reg-ask-option'" in choices
    assert "select" not in choices.lower()


def test_tapping_an_option_writes_into_the_message_box_and_does_not_send():
    toggle = _function_source("toggleChoice")
    assert "setInput(" in toggle, "a tap must fill the message box"
    assert "join('، ')" in toggle, "chosen labels join with the Persian comma"
    assert "sendMessage" not in toggle, "a tap must never send on its own"
    # The visitor presses the normal send button; that is what the gate reads.
    assert "getElementById('user-input')" in REGISTRATION_JS


def test_tapping_a_chosen_option_again_removes_it():
    toggle = _function_source("toggleChoice")
    assert "if (at !== -1) next = current.filter" in toggle


def test_job_and_position_take_one_answer_and_interests_takes_many():
    steps = _function_source("chatSteps")
    assert "key: 'job', list: 'jobs'" in steps and "multi: false" in steps
    assert "key: 'position', list: 'positions'" in steps
    assert "key: 'interests', list: 'interests'" in steps and "multi: true" in steps


def test_the_interests_question_is_one_line_to_switch_off():
    assert re.search(r"^\s*const ASK_INTERESTS = (true|false);\s*$",
                     REGISTRATION_JS, re.MULTILINE), (
        "the owner may move interests to the sign-up card — keep it a one-liner")
    # …and the step really is behind it.
    assert "if (ASK_INTERESTS) {" in REGISTRATION_JS


def test_the_options_come_from_the_taxonomy_endpoint_not_from_js():
    """An admin edits data/visit-taxonomy.json and it hot-reloads; a list baked
    into JavaScript would quietly ignore them."""
    assert "'/api/registration/options?lang='" in REGISTRATION_JS
    live = taxonomy.form_options("fa")
    for item in live["jobs"] + live["interests"] + live["positions"]:
        assert item["label"] not in REGISTRATION_JS, (
            f"«{item['label']}» is hardcoded in the front end")


# ── The sign-up card: three inputs, no more ──────────────────────────────

def test_the_signup_card_has_exactly_three_inputs():
    signup = _function_source("renderSignupStep")
    assert signup.count("= field(") == 2, "name and mobile — nothing else"
    assert "t().fullName" in signup and "t().phone" in signup
    assert "type = 'checkbox'" in signup
    # The old form's controls must not have come back.
    for gone in ("multiSelect(", "reg-select", "o.jobs", "o.interests"):
        assert gone not in signup, f"{gone} belongs in the chat now, not the card"


def test_the_signup_card_still_asks_the_existing_otp_endpoints():
    assert "'/api/auth/otp/request'" in REGISTRATION_JS
    assert "'/api/auth/otp/verify'" in REGISTRATION_JS
    assert "'/api/auth/otp/resend'" in REGISTRATION_JS


# ── Mobile layout ────────────────────────────────────────────────────────

def test_the_option_buttons_are_thumb_sized_and_wrap():
    # The rule that defines the button, not the high-contrast override of it.
    block = REGISTRATION_CSS[REGISTRATION_CSS.index("\n.reg-ask-option {"):]
    block = block[:block.index("}")]
    assert "min-height: 44px" in block
    options = REGISTRATION_CSS[REGISTRATION_CSS.index(".reg-ask-options {"):]
    options = options[:options.index("}")]
    assert "flex-wrap: wrap" in options, "18 interests must wrap, never scroll sideways"


# ── One home for the sign-up UI, every theme ─────────────────────────────
# It used to be a <script> tag in themes/inotex/partials/footer.html, so a
# customer who switched theme silently lost the ability to register, and a new
# theme had to know the tag existed. Both assets are injected by
# render_theme_index() now. These pin that, for every theme in themes/.


def _themes_on_disk():
    from app.services.themes import THEMES_DIR
    for name in sorted(os.listdir(THEMES_DIR)):
        meta = Path(THEMES_DIR) / name / "theme.json"
        if not meta.is_file():
            continue
        if json.loads(meta.read_text(encoding="utf-8")).get("selectable") is False:
            continue
        yield name


def _render(name):
    from app.services.themes import render_theme_index
    return render_theme_index(name, {
        "theme_name": name,
        "chat_token": "test-token",
        "app_title": "test",
        "asset_version": "424242",
    })


@pytest.mark.parametrize("theme", list(_themes_on_disk()))
def test_every_theme_gets_the_signup_script_and_stylesheet(theme):
    html = _render(theme)
    assert "/static/companion/registration.js?v=424242" in html, theme
    assert "/static/companion/registration.css?v=424242" in html, theme


@pytest.mark.parametrize("theme", list(_themes_on_disk()))
def test_the_script_runs_after_the_chat_is_started(theme):
    """It installs ChatConfig.sendGateFn, which core.js must already have made."""
    html = _render(theme)
    assert html.index("registration.js") > html.index("chat/core.js"), theme


@pytest.mark.parametrize("theme", list(_themes_on_disk()))
def test_an_install_without_the_module_gets_neither_asset(theme, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "ENABLED_MODULES",
                        [m for m in config.ENABLED_MODULES if m != "registration"])
    assert "companion/registration" not in _render(theme), theme


def test_no_theme_carries_the_tag_itself():
    """A duplicate would boot the script twice and build two overlays."""
    from app.services.themes import THEMES_DIR
    for path in Path(THEMES_DIR).rglob("*.html"):
        assert "companion/registration.js" not in path.read_text(encoding="utf-8"), path


def test_the_shared_stylesheet_keeps_no_theme_palette():
    """Its colours come from --reg-* tokens, so a foreign theme stays legible.

    A raw INOTEX variable here would paint the card in one customer's brand on
    every other customer's site.
    """
    assert "--inotex-" not in REGISTRATION_CSS
    assert "--color-" not in REGISTRATION_CSS
