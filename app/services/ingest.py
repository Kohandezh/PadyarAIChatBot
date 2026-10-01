"""Semi-automatic knowledge ingestion, slice S3: the job, its proposals, the
AI step and the human approval (docs/features/knowledge-ingestion/SPEC.md,
REQ-024..REQ-068).

An admin gives a file or a web page. The bytes wait in a private temp file,
the S1 child process reads them, the text is cut into chunks, each chunk is
labelled against the live knowledge, and every chunk becomes one PROPOSAL.
Nothing reaches `dataset` until an admin approves a proposal (ADR-023: new
knowledge goes live through a human gate).

THE GROUNDING RULE, HERE
------------------------
A proposal's text is the document's own words (REQ-031). The model is asked
only for questions, synonyms and, when the document gave no heading, a title
(REQ-042..REQ-047). It never writes the text, every item it returns is
checked in Python, and a title it wrote reaches the reviewer marked as a
suggestion. ADR-018: the model chooses, it does not author.

THE JOB
-------
    queued -> extracting -> extracted -> proposing -> ready -> done
    extracted -> ready, with the AI off
    queued, extracting, extracted -> cancelled
    proposing -> cancelling -> cancelled
    queued, extracting, proposing -> failed (a refusal or a dead worker)

Every step is `UPDATE ... WHERE id = ? AND status = <previous>`. A step that
changes no row stops there: someone else moved the job (a cancel, the
recovery sweep). Two partial unique indexes (migrations/0030_ingest.sql) are
the only controls for "one active job per content" and "one job talks to the
model at a time". Three web workers run at once, so a SELECT would race.

Timestamps are written with an inline `datetime('now')`, which app/db/pg.py
turns into now(). Ages are compared in Python through app/db/timeutil.py,
because SQLite hands back TEXT and PostgreSQL an aware datetime.

Logs and audit rows never carry document text (SEC-023): ids, codes, counts.
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import anyio

from app.config import logger
from app.db import dberrors, queries
from app.db.connection import get_db_connection
from app.db.timeutil import to_naive_utc
from app.services import applog, embeddings, ingest_extract
from app.services.ai.errors import AIError
from app.services.ai.request import FINISH_LENGTH, AIMessage
from app.services.ai.wrapper import padyar_ai
from app.services.answer import fold_digits
from app.services.ingest_extract import Extracted, IngestRejected
from app.services.rerank import content_tokens
from app.services.synonym_suggest import clean_pair
from app.utils import normalizer

MAX_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 40
MAX_CHUNKS = 300
LOCAL_TITLE_CHARS = 80
# An estimate, not a measurement (SPEC REQ-033, open question Q3). The label
# only warns and keeps a proposal out of approve-seen, so a wrong number
# neither deletes nor publishes anything.
NEAR_COPY_COSINE = 0.90
STALE_AFTER = timedelta(minutes=5)
RETENTION = timedelta(days=30)

# The model step shares the circuit with the live chat (engine.py), so it is
# paced and stops rather than retries (REQ-049). A test sets PACE_SECONDS to 0.
PACE_SECONDS = 1.0
AI_MAX_OUTPUT_TOKENS = 1000
AI_TIMEOUT_S = 45.0
AI_STOP_CODES = ("provider_unavailable", "all_routes_failed")
AI_ERRORS_IN_A_ROW = 3

MAX_QUESTIONS = 5
MAX_SYNONYMS = 5
TITLE_MAX = 100
TEXT_MAX = 5000
REASON_MAX = 200
SOURCE_NAME_MAX = 200
LIST_LIMIT = 20
LIST_LIMIT_MAX = 50
SEEN_MAX_IDS = 50
BULK_MAX = 50

ACTIVE = ("queued", "extracting", "extracted", "proposing", "cancelling", "ready")
REVIEWABLE_AI = ("done", "failed", "local")
STATUS_FILTERS = {"pending": ("pending",), "approved": ("approved",),
                  "rejected": ("rejected",), "all": ("pending", "approved", "rejected")}

# REQ-043. Snapshot-tested: a change here is a change to what every
# configured provider is asked, so it is reviewed as one.
SYSTEM_PROMPT = (
    "تو به مدیر یک چت‌بات کمک می‌کنی. پیام کاربر یک تکه از یک سند است.\n"
    "فقط یک شیء JSON با همین شکل برگردان و هیچ متن دیگری ننویس:\n"
    '{"title": "", "questions": ["..."], "synonyms": [{"word": "...", "suggestion": "..."}]}\n'
    "title را فقط وقتی پر کن که تکه عنوان ندارد، کوتاه و فقط با کلمه‌های خود تکه.\n"
    "questions: حداکثر ۵ پرسش کوتاه فارسی که یک بازدیدکننده می‌پرسد و پاسخش در همین تکه است.\n"
    "synonyms: حداکثر ۵ جفت؛ word یک کلمه از تکه و suggestion کلمه‌ای هم‌معنی که مردم به‌جای آن می‌نویسند.\n"
    "هیچ واقعیت، عدد یا نامی که در تکه نیست ننویس."
)

MESSAGES = {
    **ingest_extract.MESSAGES,
    # Section 8 of the SPEC fixes these sentences.
    "too_many_chunks": "این فایل بیش از ۳۰۰ بخش دارد. آن را به چند فایل تقسیم کنید.",
    "interrupted": "کار این فایل نیمه‌کاره ماند. پیشنهادهای آماده را می‌توانید بررسی کنید، یا فایل را دوباره بفرستید.",
    "rate_limited": "در یک ساعت گذشته فایل‌های زیادی فرستاده‌اید. کمی بعد دوباره امتحان کنید.",
    "already_reviewed": "این پیشنهاد قبلاً بررسی شده است.",
    "duplicate": "همین پاسخ همین حالا اضافه شده است.",
    "cancelled": "کار این فایل لغو شده است.",
    # The SPEC names no sentence for these codes (decision D2 of slice S3).
    "not_found": "این مورد پیدا نشد.",
    "not_cancellable": "کار این فایل تمام شده است و دیگر لغو نمی‌شود.",
    "not_ready": "این پیشنهاد هنوز در حال آماده شدن است.",
    "no_name": "فایل نام ندارد. فایل را دوباره انتخاب کنید.",
    "too_many_ids": "فهرست پیشنهادها درست نیست یا بیش از ۵۰ مورد دارد.",
    "bad_status": "این وضعیت شناخته نشد.",
    "reason_too_long": "دلیل رد باید حداکثر ۲۰۰ نویسه باشد.",
}
FIELD_MESSAGES = {
    "title": "عنوان باید بین ۱ تا ۱۰۰ نویسه باشد.",
    "text": "متن باید بین ۱ تا ۵٬۰۰۰ نویسه باشد.",
    "questions": "یکی از پرسش‌ها پذیرفته نشد. هر پرسش باید فارسی، بین ۵ تا ۱۲۰ نویسه و تکراری‌نشده باشد؛ حداکثر ۵ پرسش.",
    "synonyms": "یکی از مترادف‌ها پذیرفته نشد. هر مورد یک کلمه و مترادفی متفاوت لازم دارد؛ حداکثر ۵ مورد.",
}

JOB_FIELDS = ("id", "source_kind", "source_name", "format", "status", "error_code", "error_message",
              "encoding_note", "chunk_count", "ai_stopped_at", "created_by", "created_at", "finished_at")
PROPOSAL_FIELDS = ("id", "job_id", "seq", "source_text", "heading", "title", "title_source", "text",
                   "questions", "synonyms", "ai_state", "similar_kind", "similar_to", "similar_title",
                   "same_title_as", "status", "edited", "seen", "reviewed_by", "reviewed_at", "dataset_id")

# What approve-seen may take (REQ-061): pending, seen, unlabelled, AI step over.
_BULK_READY = ("status = 'pending' AND seen_at IS NOT NULL AND similar_kind = ''"
               " AND ai_state IN ('done', 'failed', 'local')")
_BULK_WHERE = "job_id = ? AND " + _BULK_READY


class IngestError(Exception):
    """A refusal the admin reads. The router answers
    {"detail": message_fa, "code": code, **extra} with `status`."""

    def __init__(self, status: int, code: str, message_fa: str = "", **extra):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message_fa = message_fa or MESSAGES[code]
        self.extra = extra


def _field_error(field: str) -> IngestError:
    return IngestError(422, "invalid_field", FIELD_MESSAGES[field], field=field)


def _marks(values) -> str:
    return ", ".join("?" for _ in values)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    moment = to_naive_utc(value)
    return None if moment is None else moment.replace(tzinfo=timezone.utc).isoformat()


def _norm(text: str) -> str:
    return normalizer.normalize_persian(text or "", expand_synonyms=False)


def _unlink(path: str) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error("[ingest] could not remove a temp file: %s", type(exc).__name__)


# ── REQ-030: chunks ──────────────────────────────────────────────────────

@dataclass
class Chunk:
    heading: str
    text: str
    part: int = 1
    parts: int = 1


_SENTENCE_END = re.compile(r"[.؟!?؛](?=\s)")
_WHITESPACE = re.compile(r"\s")


def _pieces(paragraph: str) -> list:
    """Cut one paragraph into pieces of at most 900 characters, at the last
    sentence end, else the last space. Each piece carries the whitespace that
    stood before it, so the pieces join back into the paragraph exactly."""
    pieces, gap, rest = [], "", paragraph
    while len(rest) > MAX_CHUNK_CHARS:
        cut = 0
        for match in _SENTENCE_END.finditer(rest, 0, MAX_CHUNK_CHARS + 1):
            if match.end() <= MAX_CHUNK_CHARS:
                cut = match.end()
        if not cut:
            spaces = [m.start() for m in _WHITESPACE.finditer(rest, 0, MAX_CHUNK_CHARS + 1)]
            cut = spaces[-1] if spaces else 0
        if cut <= 0:
            cut = MAX_CHUNK_CHARS
        resume = cut
        while resume < len(rest) and rest[resume].isspace():
            resume += 1
        pieces.append((gap, rest[:cut]))
        gap, rest = rest[cut:resume], rest[resume:]
    if rest:
        pieces.append((gap, rest))
    return pieces


def _section_chunks(paragraphs: list) -> list:
    out = []
    for paragraph in paragraphs:
        for index, (gap, piece) in enumerate(_pieces(paragraph)):
            joint = gap if index else "\n\n"
            if out and len(out[-1][0]) + len(joint) + len(piece) <= MAX_CHUNK_CHARS:
                out[-1][0] += joint + piece
            else:
                out.append([piece, joint])
    i = 0
    while len(out) > 1 and i < len(out):
        text, joint = out[i]
        if len(text) >= MIN_CHUNK_CHARS:
            i += 1
        elif i > 0:
            out[i - 1][0] += joint + text
            del out[i]
        else:
            following, following_joint = out[1]
            out[0] = [text + following_joint + following, joint]
            del out[1]
    return [text for text, _ in out]


def chunk(extracted: Extracted) -> list:
    """REQ-030. A heading opens a section; a section's paragraphs join up to
    900 characters; a sheet row is always its own chunk."""
    sections, current = [], None
    for block in extracted.blocks:
        if block.kind == "row":
            fields = block.fields or {}
            sections.append((fields.get("title") or "", [fields.get("text") or block.text], True))
            current = None
        elif block.kind == "heading":
            current = (block.text, [], False)
            sections.append(current)
        elif block.text:
            if current is None:
                current = ("", [], False)
                sections.append(current)
            current[1].append(block.text)
    chunks = []
    for heading, paragraphs, is_row in sections:
        texts = paragraphs if is_row else _section_chunks(paragraphs)
        chunks.extend(Chunk(heading, text, part, len(texts)) for part, text in enumerate(texts, 1))
    if len(chunks) > MAX_CHUNKS:
        raise IngestRejected("too_many_chunks", MESSAGES["too_many_chunks"])
    return chunks


# ── REQ-031..REQ-033: proposals and their labels ─────────────────────────

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_FIRST_SENTENCE = re.compile(r"[.؟!?؛\n]")


def _title(piece: Chunk) -> tuple:
    if piece.heading:
        if piece.parts == 1:
            return piece.heading, "heading"
        # Numbered, so the live rows cut from one long section never share a
        # title (SPEC review input H1).
        return f"{piece.heading} (بخش {piece.part} از {piece.parts})".translate(_FA_DIGITS), "heading"
    sentence = _FIRST_SENTENCE.split(piece.text, 1)[0].strip()
    if len(sentence) > LOCAL_TITLE_CHARS:
        cut = sentence.rfind(" ", 0, LOCAL_TITLE_CHARS)
        sentence = sentence[:cut] if cut > 0 else sentence[:LOCAL_TITLE_CHARS]
    return sentence.strip() or piece.text[:LOCAL_TITLE_CHARS].strip(), "local"


def _label(rows: list) -> None:
    """REQ-032 and REQ-033, in place, in document order.

    A duplicate is decided by TEXT only: equal to a live row (exactly or after
    normalizing), or to an earlier chunk of the same document. An equal
    title is a soft note, because one long section yields several chunks
    under one heading, and common headings already exist in the knowledge.
    """
    with closing(get_db_connection()) as conn:
        live = [dict(r) for r in conn.execute("SELECT id, title, text FROM dataset ORDER BY id").fetchall()]
        companies = [dict(r) for r in conn.execute(
            "SELECT id, title, text FROM companies WHERE text <> '' ORDER BY id").fetchall()]
    by_text, by_norm, by_title, earlier = {}, {}, {}, {}
    for row in live:
        by_text.setdefault(row["text"], row["id"])
        by_norm.setdefault(_norm(row["text"]), row["id"])
        by_title.setdefault(_norm(row["title"]), row["id"])
    for proposal in rows:
        key = _norm(proposal["text"])
        target = by_text.get(proposal["text"]) or by_norm.get(key) or earlier.get(key)
        if target:
            proposal["similar_kind"], proposal["similar_to"] = "duplicate", target
        earlier.setdefault(key, proposal["id"])
        proposal["same_title_as"] = by_title.get(_norm(proposal["title"]), "")
    _label_near_copies(rows, live, companies)


def _label_near_copies(rows: list, live: list, companies: list) -> None:
    # The raw cosine, not the calibrated score: calibration saturates at 1.0
    # well below a near copy (embeddings.search_topk_raw). Companies go first,
    # so a company wins over a dataset row; a duplicate is never relabelled.
    if not embeddings.available():
        return
    unlabelled = [p for p in rows if not p["similar_kind"]]
    if not unlabelled:
        return
    model = queries.get_setting("ai_embedding_model", "") or embeddings.DEFAULT_MODEL
    for kind, table in (("company", companies), ("dataset", live)):
        index = embeddings.build_index([r["text"] for r in table], model) if table else None
        if index is None:
            continue
        for proposal in unlabelled:
            if proposal["similar_kind"]:
                continue
            best = index.search_topk_raw(proposal["text"], k=1)
            if best and best[0][1] >= NEAR_COPY_COSINE:
                proposal["similar_kind"], proposal["similar_to"] = kind, table[best[0][0]]["id"]


def _build(job_id: str, extracted: Extracted) -> list:
    rows = []
    for seq, piece in enumerate(chunk(extracted), 1):
        title, source = _title(piece)
        rows.append({"id": secrets.token_hex(12), "job_id": job_id, "seq": seq, "source_text": piece.text,
                     "heading": piece.heading, "title": title, "text": piece.text, "title_source": source,
                     "similar_kind": "", "similar_to": "", "same_title_as": ""})
    _label(rows)
    return rows


# ── Reading ──────────────────────────────────────────────────────────────

def _job_row(job_id: str):
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT * FROM ingest_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def _job_view(row: dict) -> dict:
    job = dict(row)
    job["created_at"], job["finished_at"] = _iso(job["created_at"]), _iso(job["finished_at"])
    job["error_message"] = MESSAGES.get(job["error_code"], "") if job["error_code"] else ""
    return {field: job[field] for field in JOB_FIELDS}


def get_job(job_id: str) -> dict:
    row = _job_row(job_id)
    if row is None:
        raise IngestError(404, "not_found")
    return _job_view(row)


def _page(limit, offset) -> tuple:
    return max(1, min(int(limit), LIST_LIMIT_MAX)), max(0, int(offset))


def list_jobs(limit: int = LIST_LIMIT, offset: int = 0) -> dict:
    limit, offset = _page(limit, offset)
    with closing(get_db_connection()) as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM ingest_jobs").fetchone()["n"]
        rows = conn.execute("SELECT * FROM ingest_jobs ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
    return {"items": [_job_view(dict(r)) for r in rows], "total": int(total)}


def job_detail(job_id: str) -> dict:
    job = get_job(job_id)
    with closing(get_db_connection()) as conn:
        row = dict(conn.execute(
            "SELECT"
            " SUM(CASE WHEN ai_state IN ('done', 'failed', 'local') THEN 1 ELSE 0 END) AS ai_done,"
            " SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,"
            " SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,"
            " SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,"
            " SUM(CASE WHEN status = 'pending' AND seen_at IS NOT NULL THEN 1 ELSE 0 END) AS seen_pending,"
            f" SUM(CASE WHEN {_BULK_READY} THEN 1 ELSE 0 END) AS approvable_seen"
            " FROM ingest_proposals WHERE job_id = ?", (job_id,)).fetchone())
    counts = {key: int(value or 0) for key, value in row.items()}
    counts["chunk_count"] = job["chunk_count"]
    return {"job": job, "counts": counts}


def _titles(conn, table: str, ids: set) -> dict:
    if not ids:
        return {}
    rows = conn.execute(f"SELECT id, title FROM {table} WHERE id IN ({_marks(ids)})", tuple(ids)).fetchall()
    return {r["id"]: r["title"] for r in rows}


def _proposal_views(rows: list) -> list:
    rows = [dict(r) for r in rows]
    wanted = {"dataset": set(), "companies": set(), "ingest_proposals": set()}
    for row in rows:
        if row["similar_kind"] == "company":
            wanted["companies"].add(row["similar_to"])
        elif row["similar_kind"] in ("dataset", "duplicate"):
            wanted["dataset"].add(row["similar_to"])
            if row["similar_kind"] == "duplicate":
                wanted["ingest_proposals"].add(row["similar_to"])
    with closing(get_db_connection()) as conn:
        titles = {table: _titles(conn, table, ids) for table, ids in wanted.items()}
    views = []
    for row in rows:
        kind, target = row["similar_kind"], row["similar_to"]
        if kind == "company":
            row["similar_title"] = titles["companies"].get(target, "")
        elif kind == "duplicate" and target in titles["ingest_proposals"]:
            row["similar_title"] = titles["ingest_proposals"][target]
        else:
            row["similar_title"] = titles["dataset"].get(target, "") if kind else ""
        row["questions"] = json.loads(row["questions"] or "[]")
        row["synonyms"] = json.loads(row["synonyms"] or "[]")
        row["edited"] = bool(row["edited"])
        row["seen"] = row["seen_at"] is not None
        row["reviewed_at"] = _iso(row["reviewed_at"])
        views.append({field: row[field] for field in PROPOSAL_FIELDS})
    return views


def _proposal_row(proposal_id: str):
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT * FROM ingest_proposals WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise IngestError(404, "not_found")
    return dict(row)


def get_proposal(proposal_id: str) -> dict:
    return _proposal_views([_proposal_row(proposal_id)])[0]


def list_proposals(job_id: str, status: str = "pending", limit: int = LIST_LIMIT, offset: int = 0) -> dict:
    if status not in STATUS_FILTERS:
        raise IngestError(422, "bad_status")
    get_job(job_id)
    wanted = STATUS_FILTERS[status]
    limit, offset = _page(limit, offset)
    where = f"job_id = ? AND status IN ({_marks(wanted)})"
    with closing(get_db_connection()) as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM ingest_proposals WHERE {where}",
                             (job_id, *wanted)).fetchone()["n"]
        rows = conn.execute(f"SELECT * FROM ingest_proposals WHERE {where} ORDER BY seq LIMIT ? OFFSET ?",
                            (job_id, *wanted, limit, offset)).fetchall()
    return {"items": _proposal_views(rows), "total": int(total)}


# ── REQ-025..REQ-029: creating and running a job ─────────────────────────

def display_name(filename: str) -> str:
    """REQ-016: shown to the admin, never used as a path."""
    return os.path.basename(filename or "")[:SOURCE_NAME_MAX]


def display_url(url: str) -> str:
    """The fetched address with its host in readable letters: ingest_fetch
    hands back the ASCII form (xn--...) of a Persian domain."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        readable = host.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        readable = host
    netloc = readable if parts.port is None else f"{readable}:{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


def _write_temp(data: bytes) -> str:
    # mkstemp creates the file 0600 (REQ-028). The name is random; the
    # admin's file name never becomes a path.
    handle, path = tempfile.mkstemp(prefix="padyar-ingest-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
    except BaseException:
        _unlink(path)
        raise
    return path


def create_job(source_kind: str, source_name: str, data: bytes, fmt: str, admin: str) -> tuple:
    """(job, existing). The INSERT meets ux_ingest_jobs_active_hash when the
    same bytes already have an active job; that job is returned instead
    (REQ-027). The second pass covers the moment the other job finished
    between the refused INSERT and the SELECT."""
    content_hash = hashlib.sha256(data).hexdigest()
    name = display_name(source_name) if source_kind == "file" else source_name
    for attempt in (1, 2):
        tmp_path = _write_temp(data)
        job_id = secrets.token_hex(12)
        with closing(get_db_connection()) as conn:
            try:
                conn.execute(
                    "INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size, format,"
                    " status, tmp_path, created_by) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (job_id, source_kind, name, content_hash, len(data), fmt, tmp_path, admin))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                _unlink(tmp_path)
                if attempt == 2 or not dberrors.is_unique_violation(exc):
                    raise
                row = conn.execute(
                    f"SELECT * FROM ingest_jobs WHERE content_hash = ? AND status IN ({_marks(ACTIVE)})",
                    (content_hash, *ACTIVE)).fetchone()
                if row is not None:
                    return _job_view(dict(row)), True
                continue
        applog.audit("ingest.job.created", "یک فایل یا صفحه برای ورود دانش فرستاده شد",
                     actor=admin, target=job_id,
                     metadata={"source_kind": source_kind, "format": fmt, "byte_size": len(data)})
        return get_job(job_id), False


def _move(job_id: str, before: str, after: str) -> bool:
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_jobs SET status = ?, heartbeat_at = datetime('now'), updated_at = datetime('now')"
            " WHERE id = ? AND status = ?", (after, job_id, before))
        conn.commit()
    return cur.rowcount == 1


def _heartbeat(job_id: str) -> None:
    with closing(get_db_connection()) as conn:
        conn.execute("UPDATE ingest_jobs SET heartbeat_at = datetime('now') WHERE id = ?"
                     " AND status IN ('extracting', 'proposing', 'cancelling')", (job_id,))
        conn.commit()


def _status(job_id: str) -> str:
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT status FROM ingest_jobs WHERE id = ?", (job_id,)).fetchone()
    return row["status"] if row is not None else ""


def _fail(job_id: str, before: str, code: str) -> None:
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_jobs SET status = 'failed', error_code = ?, tmp_path = '',"
            " finished_at = datetime('now'), updated_at = datetime('now') WHERE id = ? AND status = ?",
            (code, job_id, before))
        conn.commit()
    if cur.rowcount == 1:
        applog.audit("ingest.job.failed", "خواندن یک فایل برای ورود دانش متوقف شد", target=job_id,
                     outcome="failed", metadata={"error_code": code})


def _extract(job: dict, charset: str) -> Extracted:
    with open(job["tmp_path"], "rb") as handle:
        data = handle.read()
    if job["format"] == "html":
        # The S1 child reads files only. A page body is at most 5 MiB
        # (ingest_fetch) and goes through the stdlib HTML parser in this
        # worker thread (decision D7, a known limit).
        return ingest_extract.extract_html(data, charset)
    _heartbeat(job["id"])
    try:
        return ingest_extract.extract_file_isolated(data, f"upload.{job['format']}")
    finally:
        _heartbeat(job["id"])


def _write_proposals(job_id: str, extracted: Extracted, rows: list) -> bool:
    """All proposals and the move to `extracted` in ONE transaction: a job
    cancelled while its file was being read keeps no proposal."""
    with closing(get_db_connection()) as conn:
        conn.executemany(
            "INSERT INTO ingest_proposals (id, job_id, seq, source_text, heading, title, text, title_source,"
            " ai_state, similar_kind, similar_to, same_title_as)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?)",
            [(r["id"], r["job_id"], r["seq"], r["source_text"], r["heading"], r["title"], r["text"],
              r["title_source"], r["similar_kind"], r["similar_to"], r["same_title_as"]) for r in rows])
        cur = conn.execute(
            "UPDATE ingest_jobs SET status = 'extracted', chunk_count = ?, format = ?, encoding_note = ?,"
            " tmp_path = '', heartbeat_at = datetime('now'), updated_at = datetime('now')"
            " WHERE id = ? AND status = 'extracting'",
            (len(rows), extracted.format, extracted.encoding_note, job_id))
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
    return True


async def run_job(job_id: str, charset: str = "") -> None:
    """The background half of an upload (REQ-029). `charset` is the header
    charset of a fetched page (decision D6); nothing persists it, because an
    interrupted job is failed, never resumed."""
    if not _move(job_id, "queued", "extracting"):
        return
    job = _job_row(job_id)
    try:
        extracted = await anyio.to_thread.run_sync(_extract, job, charset)
        rows = await anyio.to_thread.run_sync(_build, job_id, extracted)
        written = await anyio.to_thread.run_sync(_write_proposals, job_id, extracted, rows)
    except IngestRejected as exc:
        _fail(job_id, "extracting", exc.code)
        return
    except Exception as exc:  # noqa: BLE001 (decision D13: the admin sees "interrupted", the log the type)
        # A cancel removes the temp file under a running read; that job is
        # already cancelled and this is not an error.
        if _status(job_id) == "extracting":
            logger.error("[ingest] job %s stopped: %s", job_id, type(exc).__name__)
            applog.error("content", "ingest.job_error", "کار یک فایل با خطای پیش‌بینی‌نشده متوقف شد",
                         target=job_id, error_type=type(exc).__name__)
            _fail(job_id, "extracting", "interrupted")
        return
    finally:
        _unlink(job["tmp_path"])
    if not written:
        return
    if not padyar_ai.external_ai_enabled():
        _leave_extracted_local(job_id)
        return
    await run_model_loop()


def _leave_extracted_local(job_id: str) -> None:
    """REQ-050: with the AI off a job skips the model stage."""
    with closing(get_db_connection()) as conn:
        cur = conn.execute("UPDATE ingest_jobs SET status = 'ready', updated_at = datetime('now')"
                           " WHERE id = ? AND status = 'extracted'", (job_id,))
        if cur.rowcount != 1:
            conn.rollback()
            return
        conn.execute("UPDATE ingest_proposals SET ai_state = 'local' WHERE job_id = ? AND ai_state = 'waiting'",
                     (job_id,))
        conn.commit()
    _maybe_done(job_id)


def _maybe_done(job_id: str) -> None:
    """REQ-040, and decision D5: a ready job with nothing pending is done."""
    with closing(get_db_connection()) as conn:
        conn.execute(
            "UPDATE ingest_jobs SET status = 'done', finished_at = datetime('now'), updated_at = datetime('now')"
            " WHERE id = ? AND status = 'ready' AND NOT EXISTS"
            " (SELECT 1 FROM ingest_proposals WHERE job_id = ? AND status = 'pending')", (job_id, job_id))
        conn.commit()


# ── REQ-037, REQ-038, REQ-042..REQ-051: the model stage ──────────────────

def resume_waiting(background) -> None:
    """REQ-038: from a request, schedule the model loop when a job waits in
    `extracted` and nobody holds the slot."""
    with closing(get_db_connection()) as conn:
        busy = conn.execute("SELECT id FROM ingest_jobs WHERE status IN ('proposing', 'cancelling')"
                            " LIMIT 1").fetchone()
        waiting = None if busy is not None else conn.execute(
            "SELECT id FROM ingest_jobs WHERE status = 'extracted' LIMIT 1").fetchone()
    if waiting is not None:
        background.add_task(run_model_loop)


def _oldest_extracted():
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT id FROM ingest_jobs WHERE status = 'extracted'"
                           " ORDER BY created_at, id LIMIT 1").fetchone()
    return row["id"] if row is not None else None


def _claim(job_id: str):
    """True: this loop now holds the model slot for the job. False: the job
    left `extracted` meanwhile. None: another job holds the slot, which is the
    partial unique index refusing, so this loop has nothing to do."""
    with closing(get_db_connection()) as conn:
        try:
            cur = conn.execute(
                "UPDATE ingest_jobs SET status = 'proposing', heartbeat_at = datetime('now'),"
                " updated_at = datetime('now') WHERE id = ? AND status = 'extracted'", (job_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if dberrors.is_unique_violation(exc):
                return None
            raise
    return cur.rowcount == 1


async def run_model_loop() -> None:
    """Take the oldest job in `extracted`, run its model stage, then the next,
    until the claim fails (REQ-038). The same coroutine goes on to the next
    job instead of spawning a task nobody holds a reference to. Two loops at
    once are safe: only one wins the slot, the other returns."""
    called = False
    while True:
        job_id = _oldest_extracted()
        if job_id is None:
            return
        if not padyar_ai.external_ai_enabled():
            _leave_extracted_local(job_id)
            continue
        claimed = _claim(job_id)
        if claimed is None:
            return
        if claimed:
            called = await _propose(job_id, called)


def _waiting(job_id: str) -> list:
    with closing(get_db_connection()) as conn:
        rows = conn.execute("SELECT * FROM ingest_proposals WHERE job_id = ? AND ai_state = 'waiting'"
                            " ORDER BY seq", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def _known_questions() -> set:
    with closing(get_db_connection()) as conn:
        rows = conn.execute("SELECT question FROM questions").fetchall()
    return {_norm(r["question"]) for r in rows}


async def _propose(job_id: str, called: bool) -> bool:
    """One job's model stage. Returns whether a call has been made in this
    loop, so the pacing gap also holds between two jobs."""
    seq = None
    try:
        known = _known_questions()
        errors_in_a_row = 0
        for proposal in _waiting(job_id):
            seq = proposal["seq"]
            if _status(job_id) != "proposing":
                break
            if called:
                await asyncio.sleep(PACE_SECONDS)
            called = True
            started = time.monotonic()
            try:
                reply = await padyar_ai.generate(
                    messages=[AIMessage(role="user", content=proposal["source_text"])],
                    system_prompt=SYSTEM_PROMPT, task="chat", max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                    temperature=0.2, response_format="json_object", timeout_s=AI_TIMEOUT_S)
            except AIError as exc:
                _log_call(job_id, seq, started, error=exc)
                if _status(job_id) != "proposing":
                    break
                errors_in_a_row += 1
                if exc.code in AI_STOP_CODES or errors_in_a_row >= AI_ERRORS_IN_A_ROW:
                    _leave_model_stage(job_id, stopped_at=seq)
                    return called
                _set_ai_state(proposal["id"], "failed")
                _heartbeat(job_id)
                continue
            _log_call(job_id, seq, started, reply=reply)
            # A cancel that arrived during the call discards its answer (REQ-039).
            if _status(job_id) != "proposing":
                break
            errors_in_a_row = 0
            _apply(proposal, reply, known)
            _heartbeat(job_id)
        else:
            _leave_model_stage(job_id)
            return called
    except Exception as exc:  # noqa: BLE001 (decision D13: free the slot now, not after five minutes)
        logger.error("[ingest] model stage of %s stopped: %s", job_id, type(exc).__name__)
        applog.error("content", "ingest.model_stage_error", "مرحلهٔ هوش مصنوعی یک فایل با خطا متوقف شد",
                     target=job_id, error_type=type(exc).__name__)
        _leave_model_stage(job_id, stopped_at=seq)
    _finish_cancel(job_id)
    return called


def _log_call(job_id: str, seq: int, started: float, reply=None, error=None) -> None:
    """REQ-051: one event per call with its finish reason, output tokens and
    latency, and never the text, so the 1000-token cap can be measured."""
    fields = {"target": job_id, "duration_ms": int((time.monotonic() - started) * 1000)}
    if reply is not None:
        applog.info("content", "ingest.ai_call", "یک تماس هوش مصنوعی برای ورود دانش", outcome="ok",
                    tokens_out=reply.tokens_output, provider=reply.provider_name, model=reply.model,
                    metadata={"seq": seq, "finish_reason": reply.finish_reason}, **fields)
    else:
        applog.info("content", "ingest.ai_call", "یک تماس هوش مصنوعی برای ورود دانش", outcome="failed",
                    error_code=error.code, metadata={"seq": seq, "finish_reason": ""}, **fields)


def _set_ai_state(proposal_id: str, state: str) -> None:
    with closing(get_db_connection()) as conn:
        conn.execute("UPDATE ingest_proposals SET ai_state = ? WHERE id = ? AND ai_state = 'waiting'",
                     (state, proposal_id))
        conn.commit()


def _leave_model_stage(job_id: str, stopped_at=None) -> None:
    """proposing -> ready. On a stop (REQ-049) the chunks still waiting keep
    their local proposal and the job records where the model stopped."""
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_jobs SET status = 'ready', ai_stopped_at = ?, updated_at = datetime('now')"
            " WHERE id = ? AND status = 'proposing'", (stopped_at, job_id))
        if cur.rowcount != 1:
            conn.rollback()
            return
        conn.execute("UPDATE ingest_proposals SET ai_state = 'local' WHERE job_id = ? AND ai_state = 'waiting'",
                     (job_id,))
        conn.commit()
    _maybe_done(job_id)


def _finish_cancel(job_id: str) -> None:
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_jobs SET status = 'cancelled', finished_at = datetime('now'),"
            " updated_at = datetime('now') WHERE id = ? AND status = 'cancelling'", (job_id,))
        if cur.rowcount == 1:
            conn.execute("UPDATE ingest_proposals SET ai_state = 'local' WHERE job_id = ?"
                         " AND ai_state = 'waiting'", (job_id,))
        conn.commit()


def _answer(reply):
    if reply.finish_reason == FINISH_LENGTH:
        return None
    try:
        data = json.loads(reply.content)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _model_title_ok(title, source_text: str) -> bool:
    """REQ-045. The known hole: real words of the chunk can still be put in a
    false relation, which is why the reviewer sees a model title labelled."""
    if not isinstance(title, str):
        return False
    title = title.strip()
    if not 1 <= len(title) <= TITLE_MAX:
        return False
    if any(ch.isdigit() for ch in fold_digits(title)):
        return False
    lowered = title.lower()
    if "@" in lowered or "http" in lowered or "www." in lowered:
        return False
    return content_tokens(normalizer.normalize_persian(title)) <= content_tokens(
        normalizer.normalize_persian(source_text))


_PERSIAN_LETTER = re.compile(r"[؀-ۿ]")
_DIGIT_RUN = re.compile(r"[0-9]+")


def _clean_question(item, taken: set, folded_source=None):
    """REQ-046 for one question, or None. `folded_source` None skips the digit
    rule: a digit an admin typed is the admin's fact, not the model's."""
    if not isinstance(item, str):
        return None
    question = " ".join(item.split())
    if not 5 <= len(question) <= 120 or not _PERSIAN_LETTER.search(question):
        return None
    if folded_source is not None and any(
            run not in folded_source for run in _DIGIT_RUN.findall(fold_digits(question))):
        return None
    key = _norm(question)
    if key in taken:
        return None
    taken.add(key)
    return question


def _clean_synonym(item):
    if not isinstance(item, dict):
        return None
    pair = clean_pair(item.get("word"), item.get("suggestion"))
    return None if pair is None else {"word": pair[0], "suggestion": pair[1]}


def _apply(proposal: dict, reply, known: set) -> None:
    data = _answer(reply)
    if data is None:
        _set_ai_state(proposal["id"], "failed")
        return
    title, source = proposal["title"], proposal["title_source"]
    if source == "local" and _model_title_ok(data.get("title"), proposal["source_text"]):
        title, source = data["title"].strip(), "model"
    folded = fold_digits(proposal["source_text"])
    taken = set(known)
    questions = []
    for item in data.get("questions") if isinstance(data.get("questions"), list) else []:
        question = _clean_question(item, taken, folded)
        if question is not None and len(questions) < MAX_QUESTIONS:
            questions.append(question)
    synonyms = []
    for item in data.get("synonyms") if isinstance(data.get("synonyms"), list) else []:
        pair = _clean_synonym(item)
        if pair is not None and pair not in synonyms and len(synonyms) < MAX_SYNONYMS:
            synonyms.append(pair)
    with closing(get_db_connection()) as conn:
        conn.execute(
            "UPDATE ingest_proposals SET title = ?, title_source = ?, questions = ?, synonyms = ?,"
            " ai_state = 'done' WHERE id = ? AND status = 'pending' AND ai_state = 'waiting'",
            (title, source, json.dumps(questions, ensure_ascii=False),
             json.dumps(synonyms, ensure_ascii=False), proposal["id"]))
        conn.commit()


# ── REQ-036, REQ-041: recovery and retention ─────────────────────────────

def recover_stale() -> int:
    """REQ-036. A job whose worker died: extracting or proposing with a
    heartbeat older than five minutes (queued: by its creation) is failed
    as `interrupted` and keeps its proposals reviewable; a stale cancel is
    finished. Each move is conditional on the clock value just read, so a
    heartbeat written in between wins."""
    with closing(get_db_connection()) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, status, tmp_path, created_at, COALESCE(heartbeat_at, created_at) AS beat"
            " FROM ingest_jobs WHERE status IN ('queued', 'extracting', 'proposing', 'cancelling')").fetchall()]
    cutoff = _now_utc() - STALE_AFTER
    recovered = 0
    for row in rows:
        queued = row["status"] == "queued"
        clock = row["created_at"] if queued else row["beat"]
        moment = to_naive_utc(clock)
        if moment is None or moment >= cutoff:
            continue
        after, code = ("cancelled", "") if row["status"] == "cancelling" else ("failed", "interrupted")
        column = "created_at" if queued else "COALESCE(heartbeat_at, created_at)"
        with closing(get_db_connection()) as conn:
            cur = conn.execute(
                "UPDATE ingest_jobs SET status = ?, error_code = ?, tmp_path = '', finished_at = datetime('now'),"
                f" updated_at = datetime('now') WHERE id = ? AND status = ? AND {column} = ?",
                (after, code, row["id"], row["status"], clock))
            if cur.rowcount != 1:
                conn.rollback()
                continue
            conn.execute("UPDATE ingest_proposals SET ai_state = 'local' WHERE job_id = ?"
                         " AND ai_state = 'waiting'", (row["id"],))
            conn.commit()
        _unlink(row["tmp_path"])
        recovered += 1
        if after == "failed":
            applog.audit("ingest.job.failed", "کار یک فایل نیمه‌کاره ماند", target=row["id"],
                         outcome="failed", metadata={"error_code": code})
    return recovered


def purge_expired() -> int:
    """REQ-041: a finished job older than 30 days with nothing pending goes,
    with its proposals. The dataset rows it created and the audit stay."""
    finished = ("status IN ('done', 'failed', 'cancelled') AND NOT EXISTS (SELECT 1 FROM ingest_proposals p"
                " WHERE p.job_id = ingest_jobs.id AND p.status = 'pending')")
    with closing(get_db_connection()) as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, finished_at, tmp_path FROM ingest_jobs WHERE {finished}").fetchall()]
    cutoff = _now_utc() - RETENTION
    purged = 0
    for row in rows:
        moment = to_naive_utc(row["finished_at"])
        if moment is None or moment >= cutoff:
            continue
        with closing(get_db_connection()) as conn:
            cur = conn.execute(f"DELETE FROM ingest_jobs WHERE id = ? AND {finished}", (row["id"],))
            if cur.rowcount != 1:
                conn.rollback()
                continue
            conn.execute("DELETE FROM ingest_proposals WHERE job_id = ?", (row["id"],))
            conn.commit()
        _unlink(row["tmp_path"])
        purged += 1
    return purged


# ── REQ-039, REQ-055..REQ-061: review ────────────────────────────────────

def cancel_job(job_id: str, admin: str) -> dict:
    """REQ-039. In `proposing` a model call may be in flight, so the job goes
    to `cancelling` and keeps the model slot until the loop sees it. Either
    way every pending proposal is rejected now, not when the loop exits."""
    job = _job_row(job_id)
    if job is None:
        raise IngestError(404, "not_found")
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_jobs SET status = CASE WHEN status = 'proposing' THEN 'cancelling' ELSE 'cancelled' END,"
            " finished_at = CASE WHEN status = 'proposing' THEN NULL ELSE datetime('now') END,"
            " heartbeat_at = datetime('now'), tmp_path = '', updated_at = datetime('now')"
            " WHERE id = ? AND status IN ('queued', 'extracting', 'extracted', 'proposing')", (job_id,))
        if cur.rowcount != 1:
            conn.rollback()
            state = _status(job_id)
            raise IngestError(409, "cancelled" if state in ("cancelled", "cancelling") else "not_cancellable")
        conn.execute(
            "UPDATE ingest_proposals SET status = 'rejected', reject_reason = 'cancelled',"
            " reviewed_at = datetime('now'), reviewed_by = ? WHERE job_id = ? AND status = 'pending'",
            (admin, job_id))
        conn.commit()
    _unlink(job["tmp_path"])
    applog.audit("ingest.job.cancelled", "کار یک فایل ورود دانش لغو شد", actor=admin, target=job_id)
    return {"job": get_job(job_id)}


def mark_seen(ids, admin: str) -> int:
    """REQ-055. Unknown or already reviewed ids are skipped without a word."""
    if not isinstance(ids, list) or len(ids) > SEEN_MAX_IDS or not all(isinstance(i, str) for i in ids):
        raise IngestError(422, "too_many_ids")
    unique = list(dict.fromkeys(ids))
    if not unique:
        return 0
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_proposals SET seen_at = datetime('now'), seen_by = ?"
            f" WHERE status = 'pending' AND seen_at IS NULL AND id IN ({_marks(unique)})", (admin, *unique))
        conn.commit()
    return max(0, cur.rowcount)


def _admin_questions(items) -> list:
    if not isinstance(items, list) or len(items) > MAX_QUESTIONS:
        raise _field_error("questions")
    taken = _known_questions()
    questions = [_clean_question(item, taken) for item in items]
    if any(q is None for q in questions):
        raise _field_error("questions")
    return questions


def _admin_synonyms(items) -> list:
    if not isinstance(items, list) or len(items) > MAX_SYNONYMS:
        raise _field_error("synonyms")
    pairs = [_clean_synonym(item) for item in items]
    if any(p is None for p in pairs):
        raise _field_error("synonyms")
    return pairs


def edit_proposal(proposal_id: str, changes: dict, admin: str) -> dict:
    """REQ-056. Each field is checked on its own and a refusal names it. An
    edit also counts as seeing the card."""
    proposal = _proposal_row(proposal_id)
    if proposal["status"] != "pending":
        raise IngestError(409, "already_reviewed")
    changes = changes if isinstance(changes, dict) else {}
    updates = {}
    if "title" in changes:
        title = changes["title"]
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= TITLE_MAX:
            raise _field_error("title")
        updates["title"] = title.strip()
    if "text" in changes:
        text = changes["text"]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= TEXT_MAX:
            raise _field_error("text")
        updates["text"] = text.strip()
    if "questions" in changes:
        updates["questions"] = json.dumps(_admin_questions(changes["questions"]), ensure_ascii=False)
    if "synonyms" in changes:
        updates["synonyms"] = json.dumps(_admin_synonyms(changes["synonyms"]), ensure_ascii=False)
    if updates:
        updates["edited"] = 1
    if "title" in updates and updates["title"] != proposal["title"]:
        updates["title_source"] = "admin"
    assignments = "".join(f"{column} = ?, " for column in updates)
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            f"UPDATE ingest_proposals SET {assignments}"
            " seen_by = CASE WHEN seen_at IS NULL THEN ? ELSE seen_by END,"
            " seen_at = COALESCE(seen_at, datetime('now')) WHERE id = ? AND status = 'pending'",
            (*updates.values(), admin, proposal_id))
        conn.commit()
    if cur.rowcount != 1:
        raise IngestError(409, "already_reviewed")
    if updates:
        applog.audit("ingest.proposal.edited", "یک پیشنهاد ورود دانش ویرایش شد", actor=admin,
                     target=proposal_id, metadata={"fields": sorted(set(updates) - {"edited", "title_source"})})
    return {"proposal": get_proposal(proposal_id)}


def _reviewable(proposal_id: str) -> dict:
    proposal = _proposal_row(proposal_id)
    if _status(proposal["job_id"]) in ("cancelled", "cancelling"):
        raise IngestError(409, "cancelled")
    if proposal["status"] != "pending":
        raise IngestError(409, "already_reviewed")
    if proposal["ai_state"] not in REVIEWABLE_AI:
        raise IngestError(409, "not_ready")
    return proposal


def _live_duplicate(conn, text: str) -> str:
    """REQ-032 layers 1 and 2 against the live table, inside the approve
    transaction. Layer 2 normalizes every row in Python (a known limit,
    listed for the PR: fine for today's table sizes)."""
    row = conn.execute("SELECT id FROM dataset WHERE text = ? ORDER BY id LIMIT 1", (text,)).fetchone()
    if row is not None:
        return row["id"]
    key = _norm(text)
    for row in conn.execute("SELECT id, text FROM dataset ORDER BY id").fetchall():
        if _norm(row["text"]) == key:
            return row["id"]
    return ""


def _insert_entry(conn, title: str, text: str) -> str:
    """queries.insert_dataset_entry under a savepoint, so a clashing random
    id is retried once with a new one (REQ-058 step 3). On PostgreSQL a
    failed INSERT aborts the whole transaction; only a savepoint survives it."""
    for attempt in (1, 2):
        item_id = f"ing-{secrets.token_hex(5)}"
        conn.execute("SAVEPOINT ingest_entry")
        try:
            queries.insert_dataset_entry(conn, item_id, title, text)
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT ingest_entry")
            if attempt == 2 or not dberrors.is_unique_violation(exc):
                raise
            continue
        conn.execute("RELEASE SAVEPOINT ingest_entry")
        return item_id


def _approve_in(conn, proposal_id: str, admin: str) -> tuple:
    """REQ-058 inside the caller's transaction. The claim comes first, so a
    second approve of the same proposal changes no row and writes nothing."""
    cur = conn.execute(
        "UPDATE ingest_proposals SET status = 'approved', reviewed_at = datetime('now'), reviewed_by = ?,"
        " seen_by = CASE WHEN seen_at IS NULL THEN ? ELSE seen_by END,"
        " seen_at = COALESCE(seen_at, datetime('now'))"
        " WHERE id = ? AND status = 'pending' AND ai_state IN ('done', 'failed', 'local')",
        (admin, admin, proposal_id))
    if cur.rowcount != 1:
        raise IngestError(409, "already_reviewed")
    # Read after the claim: the row is locked now, so this is the text the
    # admin last saved, not a copy read before an edit landed.
    proposal = dict(conn.execute("SELECT * FROM ingest_proposals WHERE id = ?", (proposal_id,)).fetchone())
    duplicate = _live_duplicate(conn, proposal["text"])
    if duplicate:
        raise IngestError(409, "duplicate", duplicate_of=duplicate)
    dataset_id = _insert_entry(conn, proposal["title"], proposal["text"])
    for question in json.loads(proposal["questions"] or "[]"):
        queries.insert_question(conn, question, dataset_id, "")
    pairs = [(p["word"], p["suggestion"]) for p in json.loads(proposal["synonyms"] or "[]")]
    added = queries.insert_synonym_pairs(conn, pairs) if pairs else 0
    conn.execute("UPDATE ingest_proposals SET dataset_id = ? WHERE id = ?", (dataset_id, proposal_id))
    return dataset_id, added, proposal


def _approve_one(proposal_id: str, admin: str) -> dict:
    with closing(get_db_connection()) as conn:
        try:
            dataset_id, added, proposal = _approve_in(conn, proposal_id, admin)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {"proposal_id": proposal_id, "dataset_id": dataset_id, "job_id": proposal["job_id"],
            "synonyms_added": added}


def _after_commit(approved: list, admin: str) -> None:
    """REQ-059, in its order: reload the synonyms if any were written, then
    the audit. The publish is scheduled by the router, last."""
    if any(item["synonyms_added"] for item in approved):
        _reload_synonyms()
    for item in approved:
        applog.audit("ingest.proposal.approved", "یک پیشنهاد ورود دانش تأیید و به دانش افزوده شد",
                     actor=admin, target=f"proposal:{item['proposal_id']} dataset:{item['dataset_id']}",
                     metadata={"job_id": item["job_id"], "synonyms_added": item["synonyms_added"]})


def _approve_or_label(proposal_id: str, admin: str) -> dict:
    """_approve_one, and on a live duplicate the label REQ-032 would have
    given it (decision D4), so approve-seen stops offering it."""
    try:
        return _approve_one(proposal_id, admin)
    except IngestError as exc:
        if exc.code == "duplicate":
            with closing(get_db_connection()) as conn:
                conn.execute("UPDATE ingest_proposals SET similar_kind = 'duplicate', similar_to = ?"
                             " WHERE id = ? AND status = 'pending'", (exc.extra["duplicate_of"], proposal_id))
                conn.commit()
        raise


def _reload_synonyms() -> None:
    # REQ-059: after the commit, and a failure here keeps the rows.
    try:
        normalizer.load_synonyms_from_db()
    except Exception as exc:  # noqa: BLE001
        applog.error("content", "ingest.synonym_reload_failed", "بارگذاری دوبارهٔ مترادف‌ها شکست خورد",
                     error_type=type(exc).__name__)


def approve(proposal_id: str, admin: str) -> dict:
    """REQ-057..REQ-059 for one card. The caller schedules the publish."""
    proposal = _reviewable(proposal_id)
    approved = _approve_or_label(proposal_id, admin)
    _after_commit([approved], admin)
    _maybe_done(proposal["job_id"])
    return {"proposal": get_proposal(proposal_id), "dataset_id": approved["dataset_id"]}


def approve_seen(job_id: str, admin: str) -> dict:
    """REQ-061. The server picks: pending, seen, unlabelled and ready, in
    document order, at most 50. A proposal nobody saw is never approved
    here (REQ-062: the grandmother test is look, then approve). One failure
    does not stop the rest; it stays pending."""
    job = _job_row(job_id)
    if job is None:
        raise IngestError(404, "not_found")
    if job["status"] in ("cancelled", "cancelling"):
        raise IngestError(409, "cancelled")
    with closing(get_db_connection()) as conn:
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM ingest_proposals WHERE {_BULK_WHERE} ORDER BY seq LIMIT ?",
            (job_id, BULK_MAX)).fetchall()]
    approved, skipped = [], []
    for proposal_id in ids:
        try:
            approved.append(_approve_or_label(proposal_id, admin))
        except IngestError as exc:
            skipped.append({"id": proposal_id, "reason": exc.code})
            continue
        except Exception as exc:  # noqa: BLE001 (the approved ones still need their publish)
            logger.error("[ingest] approve of %s failed: %s", proposal_id, type(exc).__name__)
            applog.error("content", "ingest.approve_failed", "تأیید یک پیشنهاد شکست خورد",
                         target=proposal_id, error_type=type(exc).__name__)
            skipped.append({"id": proposal_id, "reason": "failed"})
    _after_commit(approved, admin)
    if approved:
        applog.audit("ingest.bulk_approved", "پیشنهادهای دیده‌شدهٔ یک فایل با هم تأیید شدند", actor=admin,
                     target=job_id, metadata={"approved": len(approved), "skipped": len(skipped)})
    _maybe_done(job_id)
    with closing(get_db_connection()) as conn:
        remaining = conn.execute(f"SELECT COUNT(*) AS n FROM ingest_proposals WHERE {_BULK_WHERE}",
                                 (job_id,)).fetchone()["n"]
    return {"approved": len(approved), "skipped": skipped, "remaining": int(remaining)}


def reject(proposal_id: str, admin: str, reason="") -> dict:
    """REQ-060."""
    reason = "" if reason is None else reason
    if not isinstance(reason, str) or len(reason) > REASON_MAX:
        raise IngestError(422, "reason_too_long")
    proposal = _proposal_row(proposal_id)
    with closing(get_db_connection()) as conn:
        cur = conn.execute(
            "UPDATE ingest_proposals SET status = 'rejected', reject_reason = ?, reviewed_at = datetime('now'),"
            " reviewed_by = ? WHERE id = ? AND status = 'pending'", (reason.strip(), admin, proposal_id))
        conn.commit()
    if cur.rowcount != 1:
        raise IngestError(409, "already_reviewed")
    applog.audit("ingest.proposal.rejected", "یک پیشنهاد ورود دانش رد شد", actor=admin, target=proposal_id,
                 metadata={"job_id": proposal["job_id"], "with_reason": bool(reason.strip())})
    _maybe_done(proposal["job_id"])
    return {"proposal": get_proposal(proposal_id)}
