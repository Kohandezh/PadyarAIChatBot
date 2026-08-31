"""Editing the taxonomy from the admin panel.

The taxonomy decides what the registration form offers and what the visit
planner may recommend, and it is edited by a person, live, during an event.
So these tests answer three questions:

  * can anyone but a logged-in admin reach it?           (no)
  * does a good save actually reach visitors?            (yes, without a restart)
  * does a bad save leave the running file alone?        (yes — the critical one)

Every test writes to a scratch file via `taxonomy.TAXONOMY_PATH`; the real
`data/visit-taxonomy.json` is never touched, and the app runs against a
throwaway database.
"""
import datetime
import json
import os
import secrets

import pytest
from fastapi.testclient import TestClient

from app.services import taxonomy


GOOD = {
    "_readme": ["a note the editor must not eat"],
    "version": "admin-test-1",
    "jobs": [{"id": "student", "fa": "دانشجو", "en": "Student"}],
    "positions": [{"id": "lead", "fa": "سرپرست", "en": "Lead"}],
    "interests": [{"id": "ai", "fa": "هوش مصنوعی", "en": "AI"}],
    "flags": [{"id": "learn", "fa": "علاقه به یادگیری", "en": "Wants to learn"}],
    "fallback_ids": ["stage"],
    "sections": [{
        "id": "stage", "fa": "استیج", "en": "Stage",
        "keywords": ["سخنرانی"], "why_fa": "چون…", "why_en": "Because…",
    }],
}


def _text(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2)


@pytest.fixture()
def taxonomy_file(tmp_path, monkeypatch):
    """A scratch taxonomy file in its own folder, loaded like the real one."""
    folder = tmp_path / "data"
    folder.mkdir()
    path = folder / "visit-taxonomy.json"
    path.write_text(_text(GOOD), encoding="utf-8")
    monkeypatch.setattr(taxonomy, "TAXONOMY_PATH", str(path))
    monkeypatch.setattr(taxonomy, "_doc", taxonomy._MINIMUM, raising=False)
    monkeypatch.setattr(taxonomy, "_mtime", -1.0, raising=False)
    monkeypatch.setattr(taxonomy, "_loaded_once", False, raising=False)
    assert taxonomy.document()["version"] == "admin-test-1"
    return path


@pytest.fixture()
def anon(tmp_path, monkeypatch):
    """The app on a throwaway database, with nobody logged in."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client(anon):
    """The same app with a real admin session cookie.

    A real session row rather than a dependency override, because the page
    route checks the cookie directly (same as the other admin pages).
    """
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection

    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    anon.cookies.set(ADMIN_COOKIE_NAME, token)
    # Admin mutations require a CSRF token. These tests exercise the
    # endpoints, not the CSRF guard itself (see tests/test_csrf.py).
    from app.auth.csrf import token_for_session
    anon.headers.update({'X-CSRF-Token': token_for_session(token)})
    return anon


# ── Nobody unauthenticated gets near the file ────────────────────────────

def test_reading_the_taxonomy_requires_a_login(anon, taxonomy_file):
    res = anon.get("/admin/api/taxonomy")
    assert res.status_code in (401, 403)
    assert "دانشجو" not in res.text  # the file itself never leaks


def test_saving_the_taxonomy_requires_a_login(anon, taxonomy_file):
    before = taxonomy_file.read_text(encoding="utf-8")
    res = anon.post("/admin/api/taxonomy", json={"text": _text(dict(GOOD, version="hacked"))})
    assert res.status_code in (401, 403)
    assert taxonomy_file.read_text(encoding="utf-8") == before


def test_the_page_sends_anonymous_visitors_to_the_login_screen(anon):
    res = anon.get("/secure-panel-inotex/settings/taxonomy", follow_redirects=False)
    assert res.status_code in (302, 303, 307)
    assert "login" in res.headers["location"]


def test_the_page_renders_for_an_admin(client):
    res = client.get("/secure-panel-inotex/settings/taxonomy")
    assert res.status_code == 200
    assert "گزینه‌های فرم ثبت‌نام" in res.text
    assert "settings_taxonomy.js" in res.text


# ── Reading ──────────────────────────────────────────────────────────────

def test_get_returns_the_file_and_what_is_live(client, taxonomy_file):
    body = client.get("/admin/api/taxonomy").json()
    assert body["data"]["jobs"][0]["fa"] == "دانشجو"
    assert body["parse_error"] == ""
    assert body["live_version"] == "admin-test-1"
    assert body["live_counts"]["sections"] == 1
    assert body["using_fallback"] is False


# ── A good save round-trips and goes live ────────────────────────────────

def test_a_valid_save_is_written_and_immediately_live(client, taxonomy_file):
    doc = json.loads(json.dumps(GOOD))
    doc["version"] = "admin-test-2"
    doc["jobs"].append({"id": "nurse", "fa": "پرستار", "en": "Nurse"})

    res = client.post("/admin/api/taxonomy", json={"text": _text(doc)})
    assert res.status_code == 200, res.text

    on_disk = json.loads(taxonomy_file.read_text(encoding="utf-8"))
    assert [j["id"] for j in on_disk["jobs"]] == ["student", "nurse"]

    # No restart, no cache poke: the loader's mtime watch must pick this up.
    live = taxonomy.document()
    assert live["version"] == "admin-test-2"
    assert [j["fa"] for j in live["jobs"]] == ["دانشجو", "پرستار"]
    # …and the form the visitor sees agrees.
    options = client.get("/api/registration/options").json()
    assert [j["label"] for j in options["jobs"]] == ["دانشجو", "پرستار"]


def test_saved_file_stays_human_readable(client, taxonomy_file):
    """A save must not collapse the taxonomy onto one line.

    The friendly editor posts compact JSON. Writing that through verbatim made
    the file unreadable to hand-edit and turned every diff into a single
    enormous line, so the handler re-serialises it pretty-printed.
    """
    compact = json.dumps(dict(GOOD, version="compact-post"), ensure_ascii=False)
    assert "\n" not in compact, "fixture must actually be compact for this test to mean anything"

    res = client.post("/admin/api/taxonomy", json={"text": compact})
    assert res.status_code == 200, res.text

    written = taxonomy_file.read_text(encoding="utf-8")
    assert written.count("\n") > 10, "file was written as one line"
    assert written.endswith("\n")
    assert '"fa": "دانشجو"' in written, "Persian must stay literal, not \\u escapes"
    # Still valid and live.
    assert taxonomy.document()["version"] == "compact-post"


def test_two_saves_in_a_row_both_go_live(client, taxonomy_file):
    """Nothing invalidates the loader's cache by hand — an edit made seconds
    after another must still be picked up by the mtime watch alone."""
    for version in ("edit-1", "edit-2"):
        res = client.post("/admin/api/taxonomy", json={"text": _text(dict(GOOD, version=version))})
        assert res.status_code == 200, res.text
        assert res.json()["live_version"] == version
        assert taxonomy.document()["version"] == version


def test_the_readme_key_survives_a_save(client, taxonomy_file):
    doc = json.loads(json.dumps(GOOD))
    doc["jobs"][0]["fa"] = "دانش‌آموز"
    assert client.post("/admin/api/taxonomy", json={"text": _text(doc)}).status_code == 200
    assert json.loads(taxonomy_file.read_text(encoding="utf-8"))["_readme"] == GOOD["_readme"]


def test_every_save_leaves_a_timestamped_backup(client, taxonomy_file):
    res = client.post("/admin/api/taxonomy", json={"text": _text(dict(GOOD, version="v2"))})
    name = res.json()["backup"]
    assert name.startswith("visit-taxonomy.backup.") and name.endswith(".json")

    backup = taxonomy_file.parent / name
    assert backup.exists()
    # The backup holds the PREVIOUS content, which is the whole point.
    assert json.loads(backup.read_text(encoding="utf-8"))["version"] == "admin-test-1"


@pytest.fixture()
def ticking_clock(monkeypatch):
    """Give the save handler a clock that advances a minute per save.

    Backup names are stamped to the second, so a burst of saves inside one
    millisecond would all reuse a single filename and a retention test would
    prove nothing. A minute apart is what a person editing the form during an
    event actually produces.
    """
    import app.routers.otp as otp_router

    class Clock:
        moment = datetime.datetime(2026, 1, 1, 9, 0, 0)

        @classmethod
        def now(cls):
            cls.moment += datetime.timedelta(minutes=1)
            return cls.moment

    monkeypatch.setattr(otp_router, "datetime", Clock)


def test_only_the_ten_newest_backups_survive(client, taxonomy_file, ticking_clock):
    """Backups are capped at 10 — data/ must not fill up with them."""
    for i in range(15):
        res = client.post("/admin/api/taxonomy", json={"text": _text(dict(GOOD, version=f"v{i}"))})
        assert res.status_code == 200, res.text

    backups = sorted(n for n in os.listdir(taxonomy_file.parent) if ".backup." in n)
    assert len(backups) == 10, backups

    # The survivors must be the newest ones — backup n holds what the file said
    # before save n, so the last ten saves left versions v4…v13 behind.
    kept = [json.loads((taxonomy_file.parent / n).read_text(encoding="utf-8"))["version"]
            for n in backups]
    assert kept == [f"v{i}" for i in range(4, 14)]
    # And the file itself is the newest save, untouched by the cleanup.
    assert taxonomy.document()["version"] == "v14"


def test_a_backup_that_cannot_be_deleted_does_not_break_the_save(
    client, taxonomy_file, ticking_clock, monkeypatch
):
    """Pruning is housekeeping. If a stale backup refuses to go, the save —
    which already reached disk — must still succeed."""
    import app.routers.otp as otp_router

    def boom(_path):
        raise OSError("read-only volume")

    monkeypatch.setattr(otp_router.os, "remove", boom)

    for i in range(12):
        res = client.post("/admin/api/taxonomy", json={"text": _text(dict(GOOD, version=f"n{i}"))})
        assert res.status_code == 200, res.text
    assert taxonomy.document()["version"] == "n11"
    # Nothing got deleted, which is exactly the failure being tolerated.
    assert len([n for n in os.listdir(taxonomy_file.parent) if ".backup." in n]) == 12


def test_the_raw_editor_can_change_sections(client, taxonomy_file):
    """The delicate list is editable — but only through the raw panel, which
    posts the same text to the same endpoint."""
    doc = json.loads(json.dumps(GOOD))
    doc["sections"].append({
        "id": "hall-b", "fa": "سالن ب", "en": "Hall B",
        "keywords": ["رباتیک"], "why_fa": "چون…", "why_en": "Because…",
    })
    assert client.post("/admin/api/taxonomy", json={"text": _text(doc)}).status_code == 200
    assert [s["id"] for s in taxonomy.sections()] == ["stage", "hall-b"]


# ── A bad save must never touch the file ─────────────────────────────────

@pytest.mark.parametrize("payload,why", [
    ("{ this is not json", "broken JSON"),
    ("[1, 2, 3]", "root is not an object"),
    (_text(dict(GOOD, sections=[])), "no sections at all"),
    (_text(dict(GOOD, sections=[{"id": "x", "fa": "بی‌کلیدواژه", "en": "No keywords",
                                 "keywords": []}])), "section without keywords"),
    (_text(dict(GOOD, jobs=[{"id": "student", "fa": "دانشجو"},
                            {"id": "", "fa": "بی‌شناسه"}])), "a row the loader would drop"),
    (_text(dict(GOOD, jobs=[{"id": "a", "fa": "یک"}, {"id": "a", "fa": "دو"}])), "duplicate id"),
])
def test_an_invalid_save_is_refused_and_the_file_is_unchanged(client, taxonomy_file, payload, why):
    before = taxonomy_file.read_text(encoding="utf-8")

    res = client.post("/admin/api/taxonomy", json={"text": payload})
    assert res.status_code == 400, f"{why}: expected a refusal, got {res.status_code}"
    assert res.json()["detail"], f"{why}: refusal must say why"

    assert taxonomy_file.read_text(encoding="utf-8") == before, f"{why}: the file was modified"
    assert taxonomy.document()["version"] == "admin-test-1", f"{why}: the live taxonomy changed"
    # A refused save must not leave debris behind either.
    assert not [n for n in os.listdir(taxonomy_file.parent) if "backup" in n], \
        f"{why}: a refused save created a backup"
    assert not [n for n in os.listdir(taxonomy_file.parent) if n.startswith(".visit-taxonomy-")], \
        f"{why}: a refused save left a temp file"


def test_the_refusal_names_the_row_that_is_wrong(client, taxonomy_file):
    bad = dict(GOOD, jobs=[{"id": "student", "fa": "دانشجو"}, {"id": "x", "fa": "  "}])
    detail = client.post("/admin/api/taxonomy", json={"text": _text(bad)}).json()["detail"]
    assert "شغل‌ها" in detail, detail   # which list
    assert "2" in detail, detail        # which row


def test_save_rejects_no_position_jobs_ids_not_in_jobs(client, taxonomy_file):
    bad = _text(dict(GOOD, no_position_jobs=["not-a-job"]))
    res = client.post("/admin/api/taxonomy", json={"text": bad})
    assert res.status_code == 400
    assert "not-a-job" in res.text


def test_a_refused_save_does_not_stop_the_next_good_one(client, taxonomy_file):
    assert client.post("/admin/api/taxonomy", json={"text": "{oops"}).status_code == 400
    assert client.post(
        "/admin/api/taxonomy", json={"text": _text(dict(GOOD, version="recovered"))}
    ).status_code == 200
    assert taxonomy.document()["version"] == "recovered"
