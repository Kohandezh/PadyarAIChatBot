"""Booth videos: every exhibitor company must play its own booth clip.

WHAT WAS BROKEN: all 169 company rows landed with video_url = '', so /chat
introduced a company with text only and the booth video never played. The
mapping from company to clip lives in ONE place — a column the organizer
prepends to the exhibitor workbook, holding the booth video number — and
nothing read it.

THE FIX under test, in scripts/import-content.py:

  * the layout is read from the HEADER ROW, not from a flag: the same
    workbook arrives both with and without that column, and a wrong flag
    would shift all 20 columns by one and import garbage.
  * a company whose video FILE is absent gets '' and a named warning. A URL
    to a file that does not exist is worse than no video: it shows the
    visitor a broken player.
  * re-importing the plain 20-column workbook must not wipe videos that are
    already attached.

The last test walks the whole way to /chat, because the requirement is not
"a column in a table" — it is "the video plays when the chat introduces the
company".
"""
import importlib.util
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
IMPORTER = ROOT / "scripts" / "import-content.py"

# The plain 20-column header the organizer has always sent. Only cell 0
# matters to the layout check; the rest is here so the fixture is the real
# file shape and not a stub.
PLAIN_HEADER = [
    "نام کاربری", "نام و نام خانوادگی ", "سمت", "نام", "نام(انگلیسی)",
    "ایمیل", "وب سایت", "تلفن", "آدرس", "آدرس(انگلیسی)", "فکس", "استان",
    "نوع", "درباره", "درباره(انگلیسی)", "نوع مجموعه", "حوزه فعالیت",
    "نوع مشارکت", "توضیحات", "موبایل ",
]
VIDEO_HEADER = ["Video number nme"] + PLAIN_HEADER

# Three companies. Each Persian name carries one token that appears in
# exactly one row, so the named-entity anchor in /chat resolves it, and each
# description is a full sentence so the corpus has enough vocabulary for the
# unknown-entity guard to stay quiet.
COMPANIES = [
    {
        "number": 4, "name": "دکیو", "name_en": "Dekio",
        "about": "شرکت دکیو سازنده سامانه های هوشمند اداری است و "
                 "محصولات خود را در غرفه خود به بازدیدکنندگان نشان می دهد.",
        "about_en": "Dekio builds smart office systems.",
    },
    {
        # Booth 88 is the file the delivery spelled without a hyphen.
        "number": 88, "name": "سپهر", "name_en": "Sepehr",
        "about": "شرکت سپهر تجهیزات آزمایشگاهی و ابزار دقیق تولید می کند و "
                 "محصولات خود را به مراکز پژوهشی عرضه می کند.",
        "about_en": "Sepehr makes laboratory equipment.",
    },
    {
        # Booth 7 has no file in the fixture directory on purpose.
        "number": 7, "name": "آوا", "name_en": "Ava",
        "about": "شرکت آوا در زمینه پردازش گفتار و هوش مصنوعی فعال است و "
                 "سامانه های خود را به سازمان ها عرضه می کند.",
        "about_en": "Ava works on speech processing.",
    },
]


def _row(c):
    """One 20-column exhibitor row (see COMPANY_COLS in the importer)."""
    r = [""] * 20
    r[3] = c["name"]
    r[4] = c["name_en"]
    r[13] = c["about"]
    r[14] = c["about_en"]
    r[6] = f"https://{c['name_en'].lower()}.example.ir"   # a profile field
    return r


def _workbook(path, with_video_column):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(VIDEO_HEADER if with_video_column else PLAIN_HEADER)
    for c in COMPANIES:
        row = _row(c)
        # openpyxl writes ints, and the real file stores them as floats. The
        # importer must survive both, so the fixture uses the float shape.
        ws.append([float(c["number"])] + row if with_video_column else row)
    wb.save(path)
    return str(path)


def _video_dir(tmp_path):
    """A stand-in delivery folder: two booth clips, one of them spelled
    without the hyphen, plus one clip no company claims."""
    d = tmp_path / "videos"
    d.mkdir(exist_ok=True)
    for name in ("ghorfe-04.mp4", "ghorfe88.mp4", "ghorfe-12.mp4"):
        (d / name).write_bytes(b"fake mp4 payload")
    return str(d)


@pytest.fixture
def importer():
    """scripts/import-content.py loaded by path — its name has a hyphen, so
    it cannot be imported the normal way."""
    spec = importlib.util.spec_from_file_location("_import_content", IMPORTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "booths.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        # TF-IDF backend: no embedding model, no trained intent classifier —
        # deterministic and offline.
        set_setting("search_backend", "tfidf")

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def _run(importer, monkeypatch, xlsx, video_dir=None, apply=False):
    """Run the importer's own main() the way the operator does."""
    argv = ["import-content.py", "--companies", xlsx]
    if video_dir is not None:
        argv += ["--video-dir", video_dir]
    if apply:
        argv.append("--apply")
    monkeypatch.setattr(importer.sys, "argv", argv)
    assert importer.main() == 0


def _stored(dataset_id):
    """A booth video is a `companies` column now (migrations/0013_companies.sql
    — companies left `dataset` entirely), not a `dataset` one."""
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    row = conn.execute("SELECT video_url FROM companies WHERE id = ?",
                       (dataset_id,)).fetchone()
    conn.close()
    return None if row is None else row["video_url"]


# ── Parsing ──────────────────────────────────────────────────────────────

def test_a_workbook_with_the_video_column_attaches_the_matching_file(
        importer, tmp_path):
    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    videos = importer.scan_videos(_video_dir(tmp_path))
    rows, errors, report = importer.load_companies(xlsx, videos)

    assert report["has_column"] is True
    assert errors == []
    by_id = {cid: ds for cid, ds, _p, _a in rows}
    # The exact string the admin upload endpoint would have written:
    # VIDEO_BASE_URL + "/" + the file's own name.
    assert by_id["dekio"]["video_url"] == "/media/videos/ghorfe-04.mp4"


def test_a_video_file_spelled_without_the_hyphen_is_still_matched(
        importer, tmp_path):
    """One file in the delivery is ghorfe88.mp4, not ghorfe-88.mp4. The booth
    NUMBER is the key, and the URL names the file as it really is on disk."""
    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    videos = importer.scan_videos(_video_dir(tmp_path))
    rows, _errors, _report = importer.load_companies(xlsx, videos)

    by_id = {cid: ds for cid, ds, _p, _a in rows}
    assert by_id["sepehr"]["video_url"] == "/media/videos/ghorfe88.mp4"


def test_a_company_whose_video_file_is_missing_gets_no_url_and_is_reported(
        importer, tmp_path):
    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    videos = importer.scan_videos(_video_dir(tmp_path))
    rows, _errors, report = importer.load_companies(xlsx, videos)

    by_id = {cid: ds for cid, ds, _p, _a in rows}
    assert by_id["ava"]["video_url"] == ""
    assert report["with_video"] == 2
    assert any("آوا" in w for w in report["warnings"])
    # And the clip nobody claimed is named too, so the operator can tell a
    # missing file from a mis-typed number.
    assert report["orphans"] == [12]


def test_a_missing_video_directory_attaches_nothing_instead_of_failing(
        importer, tmp_path):
    """The import must run on a machine that does not hold the 6.5 GB."""
    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    assert importer.scan_videos(str(tmp_path / "nope")) is None

    rows, errors, report = importer.load_companies(xlsx, None)
    assert errors == []
    assert report["with_video"] == 0
    assert all(ds["video_url"] == "" for _cid, ds, _p, _a in rows)
    assert len(report["warnings"]) == len(COMPANIES)


def test_a_workbook_without_the_video_column_imports_exactly_as_before(
        importer, tmp_path):
    """Regression: the plain 20-column file must keep working untouched."""
    xlsx = _workbook(tmp_path / "plain.xlsx", with_video_column=False)
    videos = importer.scan_videos(_video_dir(tmp_path))
    rows, errors, report = importer.load_companies(xlsx, videos)

    assert report["has_column"] is False
    assert errors == []
    assert len(rows) == len(COMPANIES)
    by_id = {cid: ds for cid, ds, _p, _a in rows}
    assert by_id["dekio"]["title"] == "دکیو"
    assert by_id["dekio"]["text"] == COMPANIES[0]["about"]
    assert by_id["dekio"]["text_en"] == COMPANIES[0]["about_en"]
    assert all(ds["video_url"] == "" for ds in by_id.values())


# ── Writing ──────────────────────────────────────────────────────────────

def test_applying_the_import_stores_the_video_url_in_the_dataset(
        importer, monkeypatch, tmp_path, client, capsys):
    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    _run(importer, monkeypatch, xlsx, _video_dir(tmp_path), apply=True)

    assert _stored("dekio") == "/media/videos/ghorfe-04.mp4"
    assert _stored("sepehr") == "/media/videos/ghorfe88.mp4"
    assert _stored("ava") == ""
    out = capsys.readouterr().out
    assert "Companies with a video:  2" in out
    assert "آوا" in out


def test_reimporting_without_the_video_column_does_not_wipe_attached_videos(
        importer, monkeypatch, tmp_path, client):
    """The organizer re-sends the plain workbook to fix a description. That
    must not cost every company its booth video."""
    with_video = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    _run(importer, monkeypatch, with_video, _video_dir(tmp_path), apply=True)
    assert _stored("dekio") == "/media/videos/ghorfe-04.mp4"

    plain = _workbook(tmp_path / "plain.xlsx", with_video_column=False)
    _run(importer, monkeypatch, plain, _video_dir(tmp_path), apply=True)
    assert _stored("dekio") == "/media/videos/ghorfe-04.mp4"


# ── End to end ───────────────────────────────────────────────────────────

def test_chat_introduces_a_company_with_its_booth_video(
        importer, monkeypatch, tmp_path, client):
    """The whole point: ask about a company, get its clip.

    The question is one of the curated anchors the importer writes for every
    company, so Tier 0 (the questions index) answers it — no AI, no
    similarity guesswork.
    """
    import app.routers.chat as chat

    async def no_ai(*_a, **_k):
        pytest.fail("the AI tier must not be reached for a curated anchor")

    monkeypatch.setattr(chat, "classify_intent", no_ai)
    monkeypatch.setattr(chat, "get_openai_response", no_ai)

    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    _run(importer, monkeypatch, xlsx, _video_dir(tmp_path), apply=True)

    r = client.post("/chat", json={"message": "درباره دکیو", "lang": "fa"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "video"
    assert body["video_url"] == "/media/videos/ghorfe-04.mp4"


def test_chat_answers_with_text_when_the_company_has_no_video(
        importer, monkeypatch, tmp_path, client):
    """No video file, no broken player: the same question about آوا answers
    with the description and no video_url at all."""
    import app.routers.chat as chat

    async def no_ai(*_a, **_k):
        pytest.fail("the AI tier must not be reached for a curated anchor")

    monkeypatch.setattr(chat, "classify_intent", no_ai)
    monkeypatch.setattr(chat, "get_openai_response", no_ai)

    xlsx = _workbook(tmp_path / "with.xlsx", with_video_column=True)
    _run(importer, monkeypatch, xlsx, _video_dir(tmp_path), apply=True)

    r = client.post("/chat", json={"message": "درباره آوا", "lang": "fa"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "text"
    assert body["video_url"] is None
