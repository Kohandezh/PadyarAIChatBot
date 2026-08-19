"""Speech-to-text credential binding, on real PostgreSQL.

The trap this guards: chat moved to the AI Control Plane, transcription stayed
on the legacy `ai_api_key` setting. An operator rotating the key in
Admin → AI fixed chat and left voice returning 401, with nothing on the page
to hint why.

NO SECRET IS EVER PRINTED HERE. Every assertion compares against a sentinel
this file chose itself, or against a length — never a repr of a real key, and
never a bare `assert key == something` where the something came from config.
"""
import pytest

from app.services.ai import store

BASE = "https://93.184.216.34/v1"
CONTROL_PLANE_SECRET = "sk-controlplane-sentinel-0001"
LEGACY_SECRET = "sk-legacy-sentinel-0002"
ROTATED_SECRET = "sk-controlplane-sentinel-ROTATED"


@pytest.fixture
def legacy_settings(conn):
    """A fully configured LEGACY install: the state the control plane must win
    against, not merely coexist with."""
    from app.db.queries import set_setting
    set_setting("ai_api_base", BASE)
    set_setting("ai_api_key", LEGACY_SECRET)
    return LEGACY_SECRET


def _cp_instance(secret=CONTROL_PLANE_SECRET, ptype="openai", enabled=True):
    iid = store.create_instance(ptype, "Control Plane GW", {"base_url": BASE},
                                secret, enabled=enabled, actor="pgtest")
    store._invalidate_runtime()
    return iid


# ── The control plane wins ──────────────────────────────────────────────

def test_resolve_prefers_the_control_plane_instance_over_the_legacy_key(
        conn, legacy_settings):
    from app.services.ai import stt

    _cp_instance()
    base, key, model, source = stt.resolve()
    assert source == "implicit"
    assert base == BASE
    assert key == CONTROL_PLANE_SECRET
    assert key != legacy_settings
    assert model == stt.DEFAULT_MODEL


def test_an_explicit_binding_is_used_when_the_operator_sets_one(
        conn, legacy_settings):
    from app.db.queries import set_setting
    from app.services.ai import stt

    iid = _cp_instance()
    # A second enabled instance makes the implicit choice ambiguous, so only an
    # explicit binding can still resolve to the control plane.
    other = store.create_instance("openai", "Second GW", {"base_url": BASE},
                                  "sk-other-sentinel-0003", enabled=True,
                                  actor="pgtest")
    store._invalidate_runtime()
    assert other != iid
    assert stt.resolve()[3] == "legacy", "two candidates must not be guessed between"

    set_setting(stt.SETTING_INSTANCE, iid)
    _base, key, _m, source = stt.resolve()
    assert source == "explicit"
    assert key == CONTROL_PLANE_SECRET


def test_rotating_the_instance_secret_changes_what_resolve_returns(
        conn, legacy_settings):
    from app.services.ai import stt

    iid = _cp_instance()
    _b, before, _m, _s = stt.resolve()
    assert before == CONTROL_PLANE_SECRET

    store.update_instance(iid, secret=ROTATED_SECRET, actor="pgtest")
    _b, after, _m, source = stt.resolve()

    assert source == "implicit"
    assert after == ROTATED_SECRET
    assert after != before
    assert len(after) == len(ROTATED_SECRET)


def test_the_resolved_secret_is_the_decrypted_column_not_the_ciphertext(conn):
    from app.services.ai import stt

    iid = _cp_instance()
    stored = conn.execute("SELECT secret_enc FROM ai_provider_instances"
                          " WHERE id = ?", (iid,)).fetchone()["secret_enc"]
    _b, key, _m, _s = stt.resolve()
    assert stored.startswith("enc:")
    assert key != stored
    assert key == CONTROL_PLANE_SECRET


# ── Fallbacks and refusals ──────────────────────────────────────────────

def test_legacy_settings_are_used_when_no_instance_exists(conn, legacy_settings):
    from app.services.ai import stt

    _b, key, _m, source = stt.resolve()
    assert source == "legacy"
    assert key == LEGACY_SECRET


def test_a_disabled_instance_does_not_win_implicitly(conn, legacy_settings):
    from app.services.ai import stt

    _cp_instance(enabled=False)
    _b, key, _m, source = stt.resolve()
    assert source == "legacy"
    assert key == LEGACY_SECRET


def test_a_stale_explicit_binding_fails_loudly_instead_of_falling_back(
        conn, legacy_settings):
    """A binding pointing at a deleted instance is a configuration error the
    operator must see, not something to paper over with the legacy key."""
    from app.db.queries import set_setting
    from app.services.ai import stt

    iid = _cp_instance()
    set_setting(stt.SETTING_INSTANCE, iid)
    store.delete_instance(iid, actor="pgtest")
    store._invalidate_runtime()
    with pytest.raises(stt.STTNotConfigured):
        stt.resolve()


def test_binding_to_a_provider_that_cannot_transcribe_is_refused(
        conn, legacy_settings):
    from app.db.queries import set_setting
    from app.services.ai import stt

    iid = store.create_instance("anthropic", "Claude", {}, "sk-anthropic-0004",
                                enabled=True, actor="pgtest")
    store._invalidate_runtime()
    set_setting(stt.SETTING_INSTANCE, iid)
    with pytest.raises(stt.STTNotConfigured):
        stt.resolve()


def test_nothing_configured_at_all_raises_sttnotconfigured(conn, monkeypatch):
    from app.services.ai import stt

    monkeypatch.setattr("app.services.openai.OPENAI_API_BASE", "")
    monkeypatch.setattr("app.services.openai.OPENAI_API_KEY", "")
    with pytest.raises(stt.STTNotConfigured):
        stt.resolve()


# ── Status surface must never leak the key ──────────────────────────────

def test_status_reports_the_source_without_exposing_the_secret(
        conn, legacy_settings):
    from app.services.ai import stt

    _cp_instance()
    payload = stt.status()
    assert payload["configured"] is True
    assert payload["source"] == "implicit"
    flat = repr(payload)
    assert CONTROL_PLANE_SECRET not in flat
    assert LEGACY_SECRET not in flat
