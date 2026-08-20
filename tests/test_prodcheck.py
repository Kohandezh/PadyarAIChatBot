"""A production install must not boot configured like a development one.

Every setting checked here fails SILENTLY. An admin cookie without `Secure`
still works over HTTP — it just also travels in clear text. A `trust`-auth
database still connects. `OTP_DELIVERY=dev` still "sends" codes — into a log
file nobody reads. The app looks healthy in all three cases, which is exactly
why the check has to run before traffic arrives rather than being noticed later.

The marker is `PADYAR_ENV`, and these tests pin the reason it has to be
separate from everything it checks: the previous version keyed off
`COOKIE_SECURE`, so a production server that forgot to set it classified
itself as development and skipped validation entirely — the misconfiguration
disabling the check built to catch it.
"""
import pytest

from app import prodcheck


@pytest.fixture
def env(monkeypatch):
    """A minimally-valid PRODUCTION environment. Tests break one thing each."""
    good = {
        "PADYAR_ENV": "production",
        "COOKIE_SECURE": "true",
        "DB_BACKEND": "postgres",
        "DATABASE_URL": "postgresql://u:S0me-Str0ng-Passw0rd@127.0.0.1:5432/padyar",
        "ALLOWED_ORIGINS": "inotex.com",
        "OTP_DELIVERY": "asanak",
        "SECRET_KEY": "a-real-pinned-secret-value-0123456789",
        "ADMIN_PASSWORD": "",
        "DB_POOL_MAX_SIZE": "10",
        "WEB_CONCURRENCY": "4",
        "VISIT_TAXONOMY_PATH": "/nonexistent-so-content-check-is-neutral.json",
    }
    for k, v in good.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def _blockers():
    return prodcheck.audit()["blockers"]


# ── The marker ──────────────────────────────────────────────────────────

def test_the_environment_marker_decides_production(env):
    assert prodcheck.is_production() is True
    env.setenv("PADYAR_ENV", "development")
    assert prodcheck.is_production() is False


def test_no_marker_defaults_to_development(env):
    env.delenv("PADYAR_ENV", raising=False)
    assert prodcheck.environment() == prodcheck.DEVELOPMENT
    assert prodcheck.is_production() is False


def test_cookie_secure_cannot_change_the_environment_classification(env):
    """THE point of this refactor.

    The previous version keyed production off COOKIE_SECURE — one of the very
    settings it checks. A real production server that forgot to set it
    classified itself as development, skipped validation, and booted with
    insecure cookies: the misconfiguration switching off the check meant to
    catch it. Classification must depend only on the explicit marker.
    """
    for cookie in ("true", "false"):
        env.setenv("COOKIE_SECURE", cookie)
        env.setenv("PADYAR_ENV", "production")
        assert prodcheck.is_production() is True, cookie
        env.setenv("PADYAR_ENV", "development")
        assert prodcheck.is_production() is False, cookie


def test_cookie_secure_false_is_now_a_production_blocker(env):
    """It stopped being the switch and became a checked setting."""
    env.setenv("COOKIE_SECURE", "false")
    assert any("COOKIE_SECURE" in b for b in _blockers())


@pytest.mark.parametrize("bad", ["prod", "PRODUCTION_", "live", "staging2", "x"])
def test_an_unrecognised_environment_value_fails_loudly(env, bad):
    """A typo must not silently degrade to development — that would recreate
    the same class of bug from the other direction."""
    env.setenv("PADYAR_ENV", bad)
    with pytest.raises(prodcheck.InvalidEnvironment):
        prodcheck.environment()


@pytest.mark.parametrize("value", ["Production", "  production  ", "PRODUCTION"])
def test_the_marker_is_case_and_space_tolerant(env, value):
    env.setenv("PADYAR_ENV", value)
    assert prodcheck.is_production() is True


def test_staging_reports_blockers_without_refusing_to_start(env):
    """A staging host may legitimately run on placeholder content and a dev SMS
    outbox, while still surfacing what would stop production."""
    import logging
    env.setenv("PADYAR_ENV", "staging")
    env.setenv("OTP_DELIVERY", "dev")
    result = prodcheck.audit()
    assert result["environment"] == "staging"
    assert result["production"] is False
    assert result["blockers"]                       # still reported
    prodcheck.enforce_at_startup(logging.getLogger("t"))   # must NOT raise


# ── Production blockers ─────────────────────────────────────────────────

def test_a_clean_production_environment_has_no_blockers(env):
    """Guards the fixture: if this failed, every test below would pass for
    the wrong reason."""
    assert prodcheck.audit()["blockers"] == []


def test_sqlite_is_refused_in_production(env):
    env.setenv("DB_BACKEND", "sqlite")
    assert any("DB_BACKEND" in b for b in _blockers())


def test_a_passwordless_database_is_refused(env):
    """`trust` auth means anyone who reaches the port is a superuser."""
    env.setenv("DATABASE_URL", "postgresql://u:@127.0.0.1:5432/padyar")
    assert any("no password" in b for b in _blockers())


def test_a_placeholder_database_password_is_refused(env):
    env.setenv("DATABASE_URL", "postgresql://u:changeme@127.0.0.1:5432/padyar")
    assert any("placeholder" in b for b in _blockers())


@pytest.mark.parametrize("value", ["", "*"])
def test_open_or_missing_allowed_origins_is_refused(env, value):
    """Without it any site can embed the bot and spend the AI budget."""
    env.setenv("ALLOWED_ORIGINS", value)
    assert any("ALLOWED_ORIGINS" in b for b in _blockers())


def test_the_dev_otp_outbox_is_refused_in_production(env):
    """Registration would silently not work: codes go to a local log file."""
    env.setenv("OTP_DELIVERY", "dev")
    assert any("OTP_DELIVERY" in b for b in _blockers())


def test_a_placeholder_admin_password_is_refused(env):
    env.setenv("ADMIN_PASSWORD", "changeme")
    assert any("ADMIN_PASSWORD" in b for b in _blockers())


# ── Development must never be blocked ───────────────────────────────────

@pytest.mark.parametrize("key, value", [
    ("DB_BACKEND", "sqlite"),
    ("ALLOWED_ORIGINS", ""),
    ("OTP_DELIVERY", "dev"),
])
def test_development_is_never_blocked_by_these(env, key, value):
    """A developer must be able to run SQLite, no origins and the dev outbox.
    Blocking that would make the check something people disable."""
    env.setenv("PADYAR_ENV", "development")
    env.setenv(key, value)
    result = prodcheck.audit()
    assert result["production"] is False
    # They are still reported — just not fatal.
    assert result["blockers"] or result["warnings"]


def test_enforce_raises_only_in_production(env):
    import logging
    log = logging.getLogger("prodcheck-test")

    env.setenv("OTP_DELIVERY", "dev")
    with pytest.raises(RuntimeError) as exc:
        prodcheck.enforce_at_startup(log)
    assert "Production configuration invalid" in str(exc.value)

    env.setenv("PADYAR_ENV", "development")
    prodcheck.enforce_at_startup(log)          # must NOT raise


def test_the_refusal_names_every_problem_at_once(env):
    """An operator fixing one setting at a time across three restarts is a
    worse experience than one message listing all three."""
    import logging
    env.setenv("DB_BACKEND", "sqlite")
    env.setenv("ALLOWED_ORIGINS", "")
    env.setenv("OTP_DELIVERY", "dev")
    assert len(prodcheck.audit()["blockers"]) == 3
    with pytest.raises(RuntimeError) as exc:
        prodcheck.enforce_at_startup(logging.getLogger("t"))
    msg = str(exc.value)
    assert "3 problem(s)" in msg
    assert "DB_BACKEND" in msg and "ALLOWED_ORIGINS" in msg and "OTP_DELIVERY" in msg


# ── Warnings that must not block ────────────────────────────────────────

def test_an_unpinned_secret_key_warns_but_does_not_block(env):
    """It is generated and persisted, so one host is fine. It matters when the
    database is rebuilt or a second host appears — then each mints its own and
    stored `enc:` secrets stop decrypting."""
    env.delenv("SECRET_KEY", raising=False)
    result = prodcheck.audit()
    assert any("SECRET_KEY" in w for w in result["warnings"])
    assert not any("SECRET_KEY" in b for b in result["blockers"])


def test_a_remote_database_without_tls_warns(env):
    env.setenv("DATABASE_URL", "postgresql://u:S0me-Str0ng-Pass@db.example.com:5432/padyar")
    assert any("sslmode" in w for w in prodcheck.audit()["warnings"])


def test_a_local_database_without_tls_does_not_warn(env):
    assert not any("sslmode" in w for w in prodcheck.audit()["warnings"])


def test_an_oversized_connection_budget_warns(env):
    """The pool is PER WORKER — the multiplication is what exhausts
    max_connections, and it is easy to miss."""
    env.setenv("DB_POOL_MAX_SIZE", "20")
    env.setenv("WEB_CONCURRENCY", "8")
    assert any("max_connections" in w for w in prodcheck.audit()["warnings"])


def test_placeholder_taxonomy_warns_but_does_not_block(env, tmp_path):
    """A staging install may legitimately run on placeholder content, so this
    must not refuse the boot — but a public launch on it would show visitors
    content the customer never approved."""
    import json
    p = tmp_path / "tax.json"
    p.write_text(json.dumps({"status": "placeholder", "jobs": []}), encoding="utf-8")
    env.setenv("VISIT_TAXONOMY_PATH", str(p))
    result = prodcheck.audit()
    assert any("PLACEHOLDER" in w for w in result["warnings"])
    assert result["blockers"] == []


def test_a_real_taxonomy_produces_no_content_warning(env, tmp_path):
    import json
    p = tmp_path / "tax.json"
    p.write_text(json.dumps({"status": "final", "jobs": ["a"]}), encoding="utf-8")
    env.setenv("VISIT_TAXONOMY_PATH", str(p))
    assert not any("PLACEHOLDER" in w for w in prodcheck.audit()["warnings"])


def test_the_audit_never_returns_a_secret_value(env):
    """It reports whether things are set and whether they look like
    placeholders — never the values themselves."""
    env.setenv("SECRET_KEY", "SENTINEL-should-never-appear-xyz")
    env.setenv("DATABASE_URL",
               "postgresql://u:SENTINELPASSWORD@127.0.0.1:5432/padyar")
    blob = repr(prodcheck.audit())
    assert "SENTINEL" not in blob


def test_the_startup_refusal_never_prints_a_value(env):
    """An operator reads this off a terminal or a shared log aggregator."""
    import logging
    env.setenv("DATABASE_URL",
               "postgresql://u:SENTINELPASSWORD@127.0.0.1:5432/padyar")
    env.setenv("OTP_DELIVERY", "dev")
    with pytest.raises(RuntimeError) as exc:
        prodcheck.enforce_at_startup(logging.getLogger("t"))
    assert "SENTINELPASSWORD" not in str(exc.value)
