"""What the public health endpoint says — and what it must never say again.

`/api/health` is unauthenticated and reachable from the internet. It used to
return the enabled-module list, the AI fallback toggle, the knowledge version
and the dataset size: a reconnaissance map of the attack surface for anyone
who typed the URL. In August 2026 that was pulled behind admin auth
(`/admin/api/ops/health`), and this file holds that line.

The deploy scripts (`deploy/padyar-deploy.sh`, `10-install-app.sh`,
`30-verify.sh`) only ever check the HTTP status, and the smoke test only
asserts `< 500` — so nothing legitimate needs the old fields, which is what
made this a pure win.
"""
import datetime
import pathlib
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    """Insert a real admin session; the ops router checks the cookie."""
    import app.config as config
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
        (token, "tester",
         (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(config.ADMIN_COOKIE_NAME, token)


# ── The public endpoint says two things, no more ────────────────────────

def test_public_health_is_status_and_version_only(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert set(res.json()) == {"status", "version"}
    assert res.json()["status"] == "ok"


def test_public_health_reports_the_running_version(client):
    """The one deliberate addition to the minimal body: which build is
    running. It must be the SAME string the repo-root VERSION file carries
    (the file a release tag is cut from), so an operator comparing
    `curl /api/health` against the GitHub release sees one version, not
    two."""
    from app import __version__
    version_file = pathlib.Path(__file__).resolve().parent.parent / "VERSION"
    assert version_file.exists(), "repo-root VERSION file is missing"
    expected = version_file.read_text(encoding="utf-8").strip()
    assert expected, "VERSION file is empty"
    assert __version__ == expected
    assert client.get("/api/health").json()["version"] == expected


def test_public_health_leaks_no_diagnostics(client):
    """The old body was a reconnaissance map. Pin that it stays gone by KEY,
    not just by full-body equality — someone adding a field "just for
    monitoring" should hit this name, not a diff in an unrelated test.

    `version` is the one deliberate exception since that pullback: the
    release identifier from the VERSION file, public by design (GitHub
    releases, CHANGELOG) and useless as reconnaissance."""
    body = client.get("/api/health").json()
    for gone in ("modules", "openai_enabled", "knowledge_version",
                 "dataset_size", "db", "config"):
        assert gone not in body, f"/api/health still exposes {gone!r}"


# ── The diagnostics moved, they did not disappear ───────────────────────

def test_ops_health_requires_an_admin_session(client):
    assert client.get("/admin/api/ops/health").status_code == 401


def test_ops_health_carries_the_post_deploy_diagnostics(client):
    """The operator verification after a deploy: is the dataset loaded, is
    AI fallback on, which modules came up. Behind the login it always lived
    behind in spirit — now also in fact."""
    _login(client)
    body = client.get("/admin/api/ops/health").json()
    assert body["status"] == "ok"
    # dataset_size is a real COUNT(*) — zero on an unseeded test DB, but present.
    assert isinstance(body["dataset_size"], int)
    assert body["openai_enabled"] in ("true", "false")
    assert isinstance(body["modules"], list)
    for key in ("knowledge_version",):
        assert key in body
