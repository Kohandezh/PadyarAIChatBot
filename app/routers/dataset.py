import os
import io
import csv
import json
import asyncio
import mimetypes
import shutil
from contextlib import closing
from datetime import datetime

from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Response

from app.config import (
    logger, VIDEO_DIR, VIDEO_BASE_URL, ALLOWED_VIDEO_EXTENSIONS,
    is_module_enabled,
)
from app.auth.security import verify_admin
from app.db import dberrors
from app.db.connection import get_db_connection
from app.db.queries import save_dataset, save_questions


router = APIRouter()


def _trigger_reindex():
    """Rebuild this worker's indexes after a write and publish a new version
    stamp so every other worker rebuilds too (see search.reindex_and_publish).

    Dataset/questions live in the DB (the single source of truth). Each write
    below is a targeted INSERT/UPDATE/DELETE so concurrent admin edits across
    workers can't clobber each other.
    """
    from app.services.search import reindex_and_publish
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, reindex_and_publish)
    except RuntimeError:
        reindex_and_publish()


# --- Video Management (gated by 'video' module) ---

@router.get("/admin/api/videos", dependencies=[Depends(verify_admin)])
async def list_videos():
    if not is_module_enabled("video"):
        raise HTTPException(status_code=404, detail="Video module is not enabled")
    if not os.path.exists(VIDEO_DIR):
        return []
    files = []
    for f in sorted(os.listdir(VIDEO_DIR)):
        ext = os.path.splitext(f)[1].lower()
        if ext not in ALLOWED_VIDEO_EXTENSIONS:
            continue
        filepath = os.path.join(VIDEO_DIR, f)
        files.append({
            "filename": f,
            "url": f"{VIDEO_BASE_URL}/{f}",
            "size": os.path.getsize(filepath),
            "modified": os.path.getmtime(filepath),
        })
    return files


@router.post("/admin/api/upload_video", dependencies=[Depends(verify_admin)])
async def upload_video(file: UploadFile = File(...)):
    if not is_module_enabled("video"):
        raise HTTPException(status_code=404, detail="Video module is not enabled")

    ALLOWED_MIMETYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-msvideo", "video/avi"}

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Invalid extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}")

    mime_type, _ = mimetypes.guess_type(file.filename or "")
    if mime_type and mime_type not in ALLOWED_MIMETYPES:
        raise HTTPException(status_code=400, detail=f"Invalid MIME type '{mime_type}'. Only video files are allowed.")

    header = await file.read(12)
    if len(header) < 12:
        raise HTTPException(status_code=400, detail="File is too small to be a valid video")

    video_signatures = {
        b'\x00\x00\x00\x1c\x66\x74\x79\x70': "mp4",
        b'\x00\x00\x00\x20\x66\x74\x79\x70': "mp4",
        b'\x00\x00\x00\x18\x66\x74\x79\x70': "mp4",
        b'\x1a\x45\xdf\xa3': "webm/mkv",
        b'\x00\x00\x00\x14\x66\x74\x79\x70': "mp4",
        b'RIFF': "avi",
    }
    if not any(header.startswith(sig) for sig in video_signatures):
        raise HTTPException(status_code=400, detail="File content does not match a valid video format")

    await file.seek(0)
    os.makedirs(VIDEO_DIR, exist_ok=True)

    safe_name = os.path.basename(file.filename or "")
    save_path = os.path.join(VIDEO_DIR, safe_name)

    if os.path.exists(save_path):
        raise HTTPException(status_code=400, detail=f"File '{safe_name}' already exists")

    def save_file():
        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

    await asyncio.to_thread(save_file)

    video_url = f"{VIDEO_BASE_URL}/{safe_name}"
    logger.info(f"Video uploaded: {safe_name}")
    return {"status": "uploaded", "filename": safe_name, "video_url": video_url}


@router.delete("/admin/api/videos/{filename}", dependencies=[Depends(verify_admin)])
async def delete_video(filename: str):
    if not is_module_enabled("video"):
        raise HTTPException(status_code=404, detail="Video module is not enabled")

    safe_name = os.path.basename(filename)
    if os.path.splitext(safe_name)[1].lower() not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file type")
    filepath = os.path.join(VIDEO_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(filepath)
    logger.info(f"Video deleted: {safe_name}")

    # A deleted file can never linger as a broken idle-avatar clip on the
    # public page — drop it from the idle-video setup if it was in use there.
    from app.services import idle_video
    idle_video.forget_video(f"{VIDEO_BASE_URL}/{safe_name}")

    return {"status": "deleted", "filename": safe_name}


# --- Dataset CRUD ---

@router.get("/admin/api/dataset", dependencies=[Depends(verify_admin)])
async def list_dataset():
    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            'SELECT id, title, text, video_url, title_en, text_en FROM dataset ORDER BY id'
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/admin/api/dataset/{item_id}", dependencies=[Depends(verify_admin)])
async def update_dataset_item(item_id: str, item: dict):
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            'UPDATE dataset SET title = ?, text = ?, video_url = ?, title_en = ?, text_en = ? WHERE id = ?',
            (item.get("title", ""), item.get("text", ""), item.get("video_url", ""),
             item.get("title_en", ""), item.get("text_en", ""), item_id),
        )
        conn.commit()
        rowcount = cur.rowcount
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    _trigger_reindex()
    return {"status": "updated"}


@router.post("/admin/api/dataset", dependencies=[Depends(verify_admin)])
async def create_dataset_item(item: dict):
    item_id = (item.get("id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="ID is required")
    # Insert directly and let the PRIMARY KEY constraint reject duplicates. A
    # SELECT-then-INSERT check would race two concurrent creates of the same id
    # into an unhandled IntegrityError (500); catching it here is atomic.
    try:
        with closing(get_db_connection()) as conn:
            # A new entry goes to the END of the public display order. Leaving
            # `position` NULL would also sort it last, but only by the
            # COALESCE fallback in the read query — every unpositioned row
            # would then share one sort key and be ordered by id among
            # themselves. An explicit position keeps the order stable and lets
            # an operator reorder later.
            nxt = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 10 FROM dataset").fetchone()[0]
            conn.execute(
                'INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?)',
                (item_id, item.get("title", ""), item.get("text", ""), item.get("video_url", ""),
                 item.get("title_en", ""), item.get("text_en", ""), nxt),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — narrowed immediately below
        # Backend-neutral. This used to catch `sqlite3.IntegrityError`, which
        # PostgreSQL never raises — it raises `psycopg.errors.UniqueViolation`,
        # so on the production backend a duplicate id fell through as an
        # unhandled 500 instead of a clean refusal. Anything that is NOT a
        # unique violation is re-raised untouched, so a real database fault
        # still surfaces rather than being mislabelled "ID already exists".
        if not dberrors.is_unique_violation(exc):
            raise
        # 409, not 400: the request is well-formed, it conflicts with the
        # current state of the resource. The existing row is left untouched —
        # no upsert, no silent overwrite of someone else's entry.
        raise HTTPException(status_code=409, detail="ID already exists")
    _trigger_reindex()
    return {"status": "created"}


@router.delete("/admin/api/dataset/{item_id}", dependencies=[Depends(verify_admin)])
async def delete_dataset_item(item_id: str):
    with closing(get_db_connection()) as conn:
        cur = conn.execute('DELETE FROM dataset WHERE id = ?', (item_id,))
        conn.commit()
        rowcount = cur.rowcount
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    _trigger_reindex()
    return {"status": "deleted"}


# --- Questions CRUD ---

@router.get("/admin/api/questions", dependencies=[Depends(verify_admin)])
async def list_questions(dataset_id: Optional[str] = None):
    with closing(get_db_connection()) as conn:
        if dataset_id:
            rows = conn.execute(
                'SELECT id, question, dataset_id, video_url FROM questions WHERE dataset_id = ? ORDER BY id',
                (dataset_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT id, question, dataset_id, video_url FROM questions ORDER BY id'
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("/admin/api/questions", dependencies=[Depends(verify_admin)])
async def create_question(q: dict):
    if not q.get("question") or not q.get("dataset_id"):
        raise HTTPException(status_code=400, detail="question and dataset_id are required")
    with closing(get_db_connection()) as conn:
        cursor = conn.execute(
            'INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, ?)',
            (q.get('question', ''), q.get('dataset_id', ''), q.get('video_url', ''))
        )
        new_id = cursor.lastrowid
        conn.commit()
    _trigger_reindex()
    return {"status": "created", "id": new_id}


@router.put("/admin/api/questions/{question_id}", dependencies=[Depends(verify_admin)])
async def update_question(question_id: int, q: dict):
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            'UPDATE questions SET question = ?, dataset_id = ?, video_url = ? WHERE id = ?',
            (q.get("question", ""), q.get("dataset_id", ""), q.get("video_url", ""), question_id),
        )
        conn.commit()
        rowcount = cur.rowcount
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="Question not found")
    _trigger_reindex()
    return {"status": "updated"}


@router.delete("/admin/api/questions/{question_id}", dependencies=[Depends(verify_admin)])
async def delete_question(question_id: int):
    with closing(get_db_connection()) as conn:
        cur = conn.execute('DELETE FROM questions WHERE id = ?', (question_id,))
        conn.commit()
        rowcount = cur.rowcount
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="Question not found")
    _trigger_reindex()
    return {"status": "deleted"}


# --- Import / Export ---
#
# Both dataset and questions can be exported and imported as JSON or CSV.
# CSV is written with a UTF-8 BOM so Excel opens Persian text correctly.
# Import has two modes the admin chooses each time:
#   "add"     -> merge into existing data (no data lost)
#   "replace" -> wipe and load only the file's contents

def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection in exported cells (a leading
    = + - @ or tab/CR would execute when the operator opens the file)."""
    s = "" if value is None else str(value)
    if s.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + s
    return s


def _to_csv(rows: list, cols: list) -> str:
    """Serialize rows to CSV text with a BOM so Excel reads Persian correctly."""
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM for Excel (explicit escape; a literal BOM is easy to strip/corrupt)
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: _csv_safe(r.get(c, "")) for c in cols})
    return buf.getvalue()


def _decode_rows(raw: bytes, filename: str) -> list:
    """Parse an uploaded file (JSON array or CSV) into a list of dict rows.

    Format is taken from the file extension; if unknown, the content is
    sniffed (leading ``[`` or ``{`` -> JSON, otherwise CSV).
    """
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return []
    name = (filename or "").lower()

    def _json_rows(t):
        data = json.loads(t)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise ValueError("JSON باید یک آرایه از موارد باشد.")
        return [d for d in data if isinstance(d, dict)]

    def _csv_rows(t):
        try:
            reader = csv.DictReader(io.StringIO(t))
            return [dict(r) for r in reader]
        except csv.Error as e:
            raise ValueError(f"قالب CSV نامعتبر است: {e}")

    if name.endswith(".json"):
        return _json_rows(text)
    if name.endswith(".csv"):
        return _csv_rows(text)
    # Unknown extension: sniff the content.
    return _json_rows(text) if text[0] in "[{" else _csv_rows(text)


def _export_response(content: str, kind: str, ext: str) -> Response:
    media = "text/csv; charset=utf-8" if ext == "csv" else "application/json; charset=utf-8"
    fname = f"{kind}_{datetime.now():%Y%m%d}.{ext}"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/admin/api/dataset/export", dependencies=[Depends(verify_admin)])
async def export_dataset(format: str = "json"):
    cols = ["id", "title", "text", "video_url"]
    with closing(get_db_connection()) as conn:
        db_rows = conn.execute('SELECT id, title, text, video_url FROM dataset ORDER BY id').fetchall()
    rows = [{c: r[c] for c in cols} for r in db_rows]
    if format == "csv":
        return _export_response(_to_csv(rows, cols), "dataset", "csv")
    return _export_response(json.dumps(rows, ensure_ascii=False, indent=2), "dataset", "json")


@router.get("/admin/api/questions/export", dependencies=[Depends(verify_admin)])
async def export_questions(format: str = "json"):
    cols = ["id", "question", "dataset_id", "video_url"]
    with closing(get_db_connection()) as conn:
        db_rows = conn.execute('SELECT id, question, dataset_id, video_url FROM questions ORDER BY id').fetchall()
    rows = [{c: r[c] for c in cols} for r in db_rows]
    if format == "csv":
        return _export_response(_to_csv(rows, cols), "questions", "csv")
    return _export_response(json.dumps(rows, ensure_ascii=False, indent=2), "questions", "json")


@router.post("/admin/api/dataset/import", dependencies=[Depends(verify_admin)])
async def import_dataset(file: UploadFile = File(...), mode: str = Form("add")):
    if mode not in ("add", "replace"):
        raise HTTPException(status_code=400, detail="حالت وارد کردن نامعتبر است.")
    raw = await file.read()
    try:
        rows = _decode_rows(raw, file.filename)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"خطا در خواندن فایل: {e}")
    if not rows:
        raise HTTPException(status_code=400, detail="فایل خالی است یا قالب آن نامعتبر است.")

    skipped = 0
    clean = []
    def _field(r, key):
        # Explicit None check (not `or ""`) so a legitimate 0 / 0.0 id survives.
        v = r.get(key)
        return str(v if v is not None else "").strip()

    for r in rows:
        item_id = _field(r, "id")
        title = _field(r, "title")
        text = _field(r, "text")
        video_url = _field(r, "video_url")
        if not item_id or not title or not text:
            skipped += 1
            continue
        clean.append({"id": item_id, "title": title, "text": text, "video_url": video_url})

    if not clean:
        raise HTTPException(status_code=400, detail="هیچ مورد معتبری در فایل یافت نشد (شناسه، عنوان و متن الزامی هستند).")

    imported = updated = 0
    if mode == "replace":
        deduped = {c["id"]: c for c in clean}  # last wins on duplicate id
        save_dataset(list(deduped.values()))
        imported = len(deduped)
    else:  # add / upsert by id
        # Merge against the current DB state (the single source of truth), not
        # this worker's in-memory copy — otherwise a stale snapshot would drop
        # rows added via other workers when save_dataset() rewrites the table.
        with closing(get_db_connection()) as conn:
            db_rows = conn.execute('SELECT id, title, text, video_url FROM dataset ORDER BY id').fetchall()
        ds = [dict(d) for d in db_rows]
        by_id = {d.get("id"): d for d in ds}
        for c in clean:
            if c["id"] in by_id:
                by_id[c["id"]].update(c)
                updated += 1
            else:
                ds.append(c)
                by_id[c["id"]] = c
                imported += 1
        save_dataset(ds)

    logger.info(f"Dataset import ({mode}): +{imported} new, {updated} updated, {skipped} skipped")
    return {"status": "ok", "mode": mode, "imported": imported, "updated": updated, "skipped": skipped}


@router.post("/admin/api/questions/import", dependencies=[Depends(verify_admin)])
async def import_questions(file: UploadFile = File(...), mode: str = Form("add")):
    if mode not in ("add", "replace"):
        raise HTTPException(status_code=400, detail="حالت وارد کردن نامعتبر است.")
    raw = await file.read()
    try:
        rows = _decode_rows(raw, file.filename)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"خطا در خواندن فایل: {e}")
    if not rows:
        raise HTTPException(status_code=400, detail="فایل خالی است یا قالب آن نامعتبر است.")

    def _field(r, key):
        # Explicit None check (not `or ""`) so a legitimate 0 dataset_id survives.
        v = r.get(key)
        return str(v if v is not None else "").strip()

    skipped = 0
    clean = []
    for r in rows:
        question = _field(r, "question")
        dataset_id = _field(r, "dataset_id")
        video_url = _field(r, "video_url")
        if not question or not dataset_id:
            skipped += 1
            continue
        clean.append({"question": question, "dataset_id": dataset_id, "video_url": video_url})

    if not clean:
        raise HTTPException(status_code=400, detail="هیچ سوال معتبری در فایل یافت نشد (سوال و شناسه دیتاست الزامی هستند).")

    # Warn about questions pointing at a dataset id that doesn't exist.
    with closing(get_db_connection()) as conn:
        valid_ids = {r["id"] for r in conn.execute('SELECT id FROM dataset').fetchall()}
    unknown = sorted({c["dataset_id"] for c in clean if c["dataset_id"] not in valid_ids})

    # Assign concrete sequential ids instead of None — save_questions also
    # writes questions.json, and null ids there break client features and
    # round-trip export.
    if mode == "replace":
        new_rows = [{"id": i, **c} for i, c in enumerate(clean, start=1)]
        save_questions(new_rows)
    else:  # add / append
        # Append onto the current DB state, not this worker's in-memory copy.
        with closing(get_db_connection()) as conn:
            db_rows = conn.execute('SELECT id, question, dataset_id, video_url FROM questions ORDER BY id').fetchall()
        existing = [dict(r) for r in db_rows]
        existing_ids = [q.get("id") for q in existing if isinstance(q.get("id"), int)]
        next_id = (max(existing_ids) + 1) if existing_ids else 1
        new_rows = []
        for c in clean:
            new_rows.append({"id": next_id, **c})
            next_id += 1
        save_questions(existing + new_rows)

    imported = len(clean)
    logger.info(f"Questions import ({mode}): +{imported} added, {skipped} skipped, {len(unknown)} unknown dataset ids")
    return {
        "status": "ok",
        "mode": mode,
        "imported": imported,
        "skipped": skipped,
        "unknown_dataset_ids": unknown,
    }
