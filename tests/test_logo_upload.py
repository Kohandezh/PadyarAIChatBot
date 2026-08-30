"""Logo upload endpoint — validation is the security story here.

The uploaded file becomes the install's logo, served to every visitor from
our own origin. So the contract under test is not "it stores a file" but:
  * only real images pass (magic bytes, not the declared type, decide)
  * SVG never passes (stored XSS via our own origin)
  * size is capped
  * nothing lands on disk when validation fails
  * the whole route is behind the admin session
"""
import datetime
import io
import os
import secrets

import pytest
from fastapi.testclient import TestClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64          # valid signature, junk body
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "logo.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
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
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})
    return token


def _upload(client, name, content, content_type):
    return client.post(
        "/admin/api/upload_logo",
        files={"file": (name, io.BytesIO(content), content_type)},
    )


def test_valid_png_is_stored_and_returns_url(client, tmp_path):
    _login(client)
    res = _upload(client, "logo.png", PNG, "image/png")
    assert res.status_code == 200
    url = res.json()["url"]
    assert url.startswith("/media/uploads/")
    assert url.endswith(".png")
    on_disk = tmp_path / "uploads" / url.split("/media/uploads/")[1]
    assert on_disk.read_bytes() == PNG
    assert (on_disk.stat().st_mode & 0o777) == 0o644


def test_valid_jpeg_and_gif_pass(client):
    _login(client)
    assert _upload(client, "a.jpg", JPEG, "image/jpeg").status_code == 200
    assert _upload(client, "a.gif", GIF, "image/gif").status_code == 200


def test_renamed_text_file_is_rejected(client, tmp_path):
    _login(client)
    res = _upload(client, "evil.png", b"#!/bin/sh\nrm -rf /", "image/png")
    assert res.status_code == 400
    assert not (tmp_path / "uploads").exists()


def test_svg_is_rejected(client):
    _login(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    res = _upload(client, "logo.svg", svg, "image/svg+xml")
    assert res.status_code == 400


def test_declared_type_must_match_extension(client):
    _login(client)
    res = _upload(client, "logo.png", PNG, "image/gif")
    assert res.status_code == 400


def test_content_must_match_extension(client):
    _login(client)
    res = _upload(client, "logo.gif", PNG, "image/gif")
    assert res.status_code == 400


def test_oversize_is_rejected(client, tmp_path):
    import app.config as config
    _login(client)
    blob = PNG + b"\x00" * (config.LOGO_MAX_BYTES + 1)
    res = _upload(client, "big.png", blob, "image/png")
    assert res.status_code == 400
    assert not (tmp_path / "uploads").exists()


def test_requires_admin_session(client):
    res = _upload(client, "logo.png", PNG, "image/png")
    assert res.status_code in (401, 403)
