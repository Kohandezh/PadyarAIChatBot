"""Safe local reading of an uploaded file (knowledge ingestion, slice S1).

Unknown bytes from an admin's upload become structured text here and nowhere
else. Every number below is a contract value from
docs/features/knowledge-ingestion/SPEC.md (REQ-001..REQ-016, SEC-005..SEC-011),
and each check closes an attack RESEARCH.md measured: a zip bomb whose header
lies about its size took a parser to 806 MB (X3), and an entity-expansion DTD
cost lxml and expat seconds of CPU (X2).

This module is also the child process of extract_file_isolated
(`python -m app.services.ingest_extract`). So at module level it imports only
the standard library and defusedxml, and openpyxl only when an XLSX is read
(REQ-014): app.config would load .env and the database inside a process that
parses hostile bytes.
"""
import csv
import io
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
import zlib
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree.ElementTree import ParseError

import defusedxml
from defusedxml import ElementTree as DefusedET

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ZIP_MEMBERS = 2000
MAX_ZIP_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 20
MAX_CHARS = 250_000
MIN_LETTERS = 20
PDF_MAX_PAGES = 200
PDF_TIMEOUT_S = 45
CHILD_MEMORY_BYTES = 1024 ** 3

FORMATS = ("pdf", "docx", "xlsx", "csv", "txt", "md")

MESSAGES = {
    "too_large": "این فایل بزرگ‌تر از ۲۰ مگابایت است. آن را به چند فایل کوچک‌تر تقسیم کنید.",
    "bad_type": "این نوع فایل پشتیبانی نمی‌شود. فایل Word، PDF، Excel، CSV یا متن بفرستید.",
    "bad_zip": "این فایل خراب است یا قالبش شناخته نشد.",
    "zip_too_big": "محتوای این فایل بیش از اندازه بزرگ است.",
    "encrypted": "این فایل رمز دارد. نسخهٔ بدون رمز را بفرستید.",
    "dtd_forbidden": "این فایل ساختار غیرعادی دارد و خوانده نشد.",
    "bad_encoding": "حروف این فایل خوانده نشد. آن را با رمزگذاری UTF-8 ذخیره کنید.",
    "missing_columns": "ستون پاسخ پیدا نشد. ردیف اول فایل باید عنوان ستون‌ها باشد، مثلاً «عنوان» و «پاسخ».",
    "too_many_rows": "این فایل بیش از ۵٬۰۰۰ ردیف دارد. آن را تقسیم کنید.",
    "too_long": "متن این فایل خیلی طولانی است. آن را به چند فایل تقسیم کنید.",
    "no_text": "در این فایل یا صفحه متن قابل خواندن پیدا نشد. اگر فایل اسکن‌شده است، نسخهٔ متنی آن را بفرستید.",
    "pdf_tool_missing": "خواندن PDF روی این سرور فعال نیست. فایل Word یا متن بفرستید.",
    "timeout": "خواندن این فایل بیش از حد طول کشید.",
    "parse_failed": "این فایل خوانده نشد.",
}


class IngestRejected(Exception):
    def __init__(self, code: str, message_fa: str):
        super().__init__(code)
        self.code = code
        self.message_fa = message_fa


def _reject(code: str) -> IngestRejected:
    return IngestRejected(code, MESSAGES[code])


@dataclass
class Block:
    kind: str
    text: str
    level: int = 0
    fields: dict | None = None


@dataclass
class Extracted:
    format: str
    blocks: list[Block]
    encoding_note: str = ""


# ── Type detection (REQ-002), the only call the upload route makes ───────

_ZIP_ERRORS = (zipfile.BadZipFile, EOFError, OSError, ValueError, OverflowError,
               struct.error, NotImplementedError)
_MEMBER_ERRORS = _ZIP_ERRORS + (KeyError, zlib.error)
_EOCD = b"PK\x05\x06"
_EOCD_SIZE = 22
_ZIP64_LOCATOR = b"PK\x06\x07"


def _extension(filename: str) -> str:
    return os.path.splitext(os.path.basename(filename or ""))[1].lower().lstrip(".")


def _check_end_record(data: bytes) -> None:
    # zipfile would trust this record to size its directory read, so it is
    # checked first. The lookup mirrors zipfile._EndRecData, so both read the
    # same record. A reviewer measured namelist() on 230,000 empty members at
    # 4.04 s and 102 MiB of the web worker; a zip64 locator reaches the same
    # cost with small EOCD fields (D2), so it counts as zip64 too.
    start = len(data) - _EOCD_SIZE
    if not (start >= 0 and data[start:start + 4] == _EOCD and data[-2:] == b"\x00\x00"):
        start = data.rfind(_EOCD, max(len(data) - _EOCD_SIZE - 0xFFFF, 0))
        if start < 0 or len(data) - start < _EOCD_SIZE:
            raise _reject("bad_type")
    entries, directory_bytes = struct.unpack_from("<HL", data, start + 10)
    zip64 = (entries == 0xFFFF or directory_bytes == 0xFFFFFFFF
             or _ZIP64_LOCATOR in data[max(start - 20, 0):start])
    if zip64 or entries > MAX_ZIP_MEMBERS or directory_bytes > MAX_ZIP_DIRECTORY_BYTES:
        raise _reject("zip_too_big")


def _zip_names(data: bytes) -> list[str]:
    _check_end_record(data)
    try:
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    except _ZIP_ERRORS as exc:
        raise _reject("bad_type") from exc
    # zipfile walks the directory by its byte size, not by the EOCD count, so
    # a lying count is caught here, after at most 4 MiB of directory.
    if len(names) > MAX_ZIP_MEMBERS:
        raise _reject("zip_too_big")
    return names


def detect_format(data: bytes, filename: str) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise _reject("too_large")
    ext = _extension(filename)
    if not data or ext not in FORMATS:
        raise _reject("bad_type")
    is_pdf = data[:5] == b"%PDF-"
    is_zip = data[:4] == b"PK\x03\x04"
    if ext == "pdf":
        if not is_pdf:
            raise _reject("bad_type")
        return ext
    if ext in ("docx", "xlsx"):
        if not is_zip:
            raise _reject("bad_type")
        required = "word/document.xml" if ext == "docx" else "xl/workbook.xml"
        names = set(_zip_names(data))
        if "[Content_Types].xml" not in names or required not in names:
            raise _reject("bad_type")
        return ext
    if is_pdf or is_zip:
        raise _reject("bad_type")
    return ext


# ── The zip gate (REQ-003), before any DOCX or XLSX parser ───────────────

_INFLATE_CHUNK = 65536


def _inflated_size(view: memoryview, info: zipfile.ZipInfo, budget: int) -> int:
    offset = info.header_offset
    header = bytes(view[offset:offset + 30])
    if len(header) < 30 or header[:4] != b"PK\x03\x04":
        raise _reject("bad_zip")
    name_len, extra_len = struct.unpack_from("<HH", header, 26)
    start = offset + 30 + name_len + extra_len
    if info.compress_type == zipfile.ZIP_STORED:
        stored = max(min(info.compress_size, len(view) - start), 0)
        if stored > budget:
            raise _reject("zip_too_big")
        return stored
    # The header's file_size is never read: X3 forged it and a parser still
    # grew to 806 MB. The raw stream is inflated 64 KiB at a time instead.
    inflater = zlib.decompressobj(-15)
    produced = 0
    position = start
    pending = b""
    try:
        while not inflater.eof:
            if not pending:
                pending = view[position:position + _INFLATE_CHUNK]
                position += len(pending)
                if not pending:
                    break
            produced += len(inflater.decompress(pending, _INFLATE_CHUNK))
            pending = inflater.unconsumed_tail
            if produced > budget:
                raise _reject("zip_too_big")
    except zlib.error as exc:
        raise _reject("bad_zip") from exc
    return produced


def _open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        members = archive.infolist()
    except _ZIP_ERRORS as exc:
        raise _reject("bad_zip") from exc
    if len(members) > MAX_ZIP_MEMBERS:
        raise _reject("zip_too_big")
    if any(info.flag_bits & 0x1 for info in members):
        raise _reject("encrypted")
    if any(info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for info in members):
        raise _reject("bad_zip")
    view = memoryview(data)
    total = 0
    for info in members:
        total += _inflated_size(view, info, MAX_UNZIPPED_BYTES - total)
    return archive


def _read_member(archive: zipfile.ZipFile, name: str, size: int = -1) -> bytes:
    try:
        with archive.open(name) as member:
            return member.read(size)
    except _MEMBER_ERRORS as exc:
        raise _reject("bad_zip") from exc


# ── Text rules shared by every format (REQ-004, REQ-005, REQ-012) ────────

_ALLOWED_CONTROLS = frozenset("\t\n\r\u200c\u200d\u200e\u200f")
_HEADING_ENDINGS = (".", "؟", "!", ":")


def _clean(text: str) -> str:
    # REQ-012: only runs of spaces shrink. ZWNJ is not whitespace to str, and
    # Arabic ي/ك and digits are left for normalize_persian at compare time.
    # U+0000 goes too, because PostgreSQL text cannot store it.
    lines = (re.sub(r"[^\S\n]+", " ", line).strip() for line in text.replace("\x00", "").split("\n"))
    return "\n".join(line for line in lines if line)


def _plausible_persian(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not text or not letters:
        return False
    controls = sum(1 for ch in text
                   if unicodedata.category(ch) in ("Cc", "Cf") and ch not in _ALLOWED_CONTROLS)
    arabic = sum(1 for ch in letters if "\u0600" <= ch <= "\u06ff")
    return controls / len(text) < 0.01 and arabic / len(letters) >= 0.5


def _decode_text(data: bytes) -> tuple[str, str]:
    # cp1256 maps all 256 byte values, so without the plausibility gate any
    # non-UTF-8 file (UTF-16 without a BOM, a binary renamed .txt) would turn
    # silently into Persian-looking noise (RESEARCH.md, F1). The CSV import in
    # app/routers/dataset.py uses errors="replace"; that is not copied here.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise _reject("bad_encoding") from exc
    if b"\x00" in data:
        raise _reject("bad_encoding")
    try:
        return data.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        pass
    text = data.decode("cp1256")
    if _plausible_persian(text):
        return text, "cp1256"
    raise _reject("bad_encoding")


def _newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _paragraph_blocks(text: str) -> list[Block]:
    blocks = []
    for chunk in re.split(r"\n\s*\n", text):
        para = _clean(chunk)
        if not para:
            continue
        if "\n" not in para and len(para) <= 60 and not para.endswith(_HEADING_ENDINGS):
            blocks.append(Block("heading", para, 2))
        else:
            blocks.append(Block("para", para))
    return blocks


_MD_HEADING = re.compile(r"(#{1,3})(?!#)\s*(.*)")


def _markdown_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines: list[str] = []

    def flush() -> None:
        para = _clean("\n".join(lines))
        if para:
            blocks.append(Block("para", para))
        lines.clear()

    for line in text.split("\n"):
        heading = _MD_HEADING.fullmatch(line.strip())
        if heading and heading.group(2).strip():
            flush()
            blocks.append(Block("heading", _clean(heading.group(2)), len(heading.group(1))))
        elif not line.strip():
            flush()
        else:
            lines.append(line)
    flush()
    return blocks


# ── Formats ──────────────────────────────────────────────────────────────

def _extract_text(data: bytes, fmt: str) -> Extracted:
    text, note = _decode_text(data)
    text = _newlines(text)
    blocks = _markdown_blocks(text) if fmt == "md" else _paragraph_blocks(text)
    return Extracted(fmt, blocks, note)


def pdf_available() -> bool:
    return shutil.which("pdftotext") is not None


def _extract_pdf(data: bytes) -> Extracted:
    tool = shutil.which("pdftotext")
    if not tool:
        raise _reject("pdf_tool_missing")
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            proc = subprocess.run(
                [tool, "-enc", "UTF-8", "-l", str(PDF_MAX_PAGES), path, "-"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=PDF_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise _reject("timeout") from exc
        except OSError as exc:
            raise _reject("pdf_tool_missing") from exc
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    if proc.returncode != 0:
        # poppler says "Incorrect password" for a user password, and exit 3
        # is its permissions error.
        errors = proc.stderr.decode("latin-1")
        if proc.returncode == 3 or re.search(r"password|encrypt|security handler", errors, re.IGNORECASE):
            raise _reject("encrypted")
        raise _reject("parse_failed")
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _reject("parse_failed") from exc
    # pdftotext wraps RTL runs in U+202A..U+202E (X1); a form feed ends a page.
    text = re.sub("[\u202a-\u202e]", "", _newlines(text)).replace("\f", "\n\n")
    return Extracted("pdf", _paragraph_blocks(text))


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(paragraph) -> str:
    parts = []
    for child in paragraph:
        if child.tag == f"{_W}pPr":
            continue
        for node in child.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append(" ")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
    return "".join(parts)


def _docx_paragraph(paragraph) -> Block | None:
    text = _clean(_docx_text(paragraph))
    if not text:
        return None
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    name = style.get(f"{_W}val", "") if style is not None else ""
    if name.startswith(("Heading", "Title")):
        level = int(name[-1]) if name[-1:].isdigit() else 1
        return Block("heading", text, max(1, min(level, 3)))
    return Block("para", text)


def _docx_blocks(body) -> list[Block]:
    # An explicit stack, not recursion: nesting depth is the uploader's choice.
    blocks = []
    stack = [iter(body)]
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
        elif child.tag == f"{_W}p":
            block = _docx_paragraph(child)
            if block:
                blocks.append(block)
        elif child.tag == f"{_W}tbl":
            for row in child.findall(f"{_W}tr"):
                cells = (_clean(" ".join(_docx_text(p) for p in cell.iter(f"{_W}p")))
                         for cell in row.findall(f"{_W}tc"))
                text = " | ".join(cell for cell in cells if cell)
                if text:
                    blocks.append(Block("para", text))
        else:
            stack.append(iter(child))
    return blocks


def _extract_docx(data: bytes) -> Extracted:
    archive = _open_zip(data)
    xml = _read_member(archive, "word/document.xml")
    try:
        root = DefusedET.fromstring(xml, forbid_dtd=True)
    except (defusedxml.DTDForbidden, defusedxml.EntitiesForbidden,
            defusedxml.ExternalReferenceForbidden) as exc:
        raise _reject("dtd_forbidden") from exc
    except ParseError as exc:
        raise _reject("parse_failed") from exc
    body = root.find(f"{_W}body")
    return Extracted("docx", _docx_blocks(body) if body is not None else [])


_TITLE_HEADERS = frozenset({"title", "question", "عنوان", "سوال", "سؤال", "پرسش"})
_TEXT_HEADERS = frozenset({"text", "answer", "پاسخ", "جواب", "متن", "توضیح"})


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _table_blocks(rows) -> list[Block]:
    rows = iter(rows)
    header = [_cell(value).strip().lower() for value in list(next(rows, ()))[:MAX_COLUMNS]]
    title_col = next((i for i, name in enumerate(header) if name in _TITLE_HEADERS), None)
    text_col = next((i for i, name in enumerate(header) if name in _TEXT_HEADERS), None)
    if text_col is None:
        raise _reject("missing_columns")
    blocks = []
    count = 0
    for row in rows:
        cells = [_cell(value) for value in list(row)[:MAX_COLUMNS]]
        if not any(cell.strip() for cell in cells):
            continue
        count += 1
        if count > MAX_ROWS:
            raise _reject("too_many_rows")
        text = _clean(cells[text_col]) if text_col < len(cells) else ""
        if not text:
            continue
        title = _clean(cells[title_col]) if title_col is not None and title_col < len(cells) else ""
        blocks.append(Block("row", text, 0, {"title": title, "text": text}))
    return blocks


def _extract_csv(data: bytes) -> Extracted:
    text, note = _decode_text(data)
    try:
        return Extracted("csv", _table_blocks(csv.reader(io.StringIO(text, newline=""))), note)
    except csv.Error as exc:
        raise _reject("parse_failed") from exc


_DOCTYPE_MARKERS = tuple("<!DOCTYPE".encode(codec) for codec in ("utf-8", "utf-16-le", "utf-16-be"))


def _extract_xlsx(data: bytes) -> Extracted:
    archive = _open_zip(data)
    # openpyxl wraps a DTD error into its own generic failure, so the DTD is
    # looked for first to give the admin a definite code (REQ-007).
    for info in archive.infolist():
        if info.filename.endswith((".xml", ".rels")):
            head = _read_member(archive, info.filename, 4096)
            if any(marker in head for marker in _DOCTYPE_MARKERS):
                raise _reject("dtd_forbidden")
    import openpyxl
    import openpyxl.xml
    if not openpyxl.xml.DEFUSEDXML:
        raise _reject("parse_failed")
    workbook = None
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = workbook.worksheets[0].iter_rows(max_col=MAX_COLUMNS, values_only=True)
        return Extracted("xlsx", _table_blocks(rows))
    except IngestRejected:
        raise
    except Exception as exc:
        # openpyxl raises a dozen unrelated types for a malformed workbook,
        # and wraps defusedxml's refusal of a DTD found past 4 KiB in one of
        # them; the entities are still never expanded.
        raise _reject("parse_failed") from exc
    finally:
        if workbook is not None:
            workbook.close()


# ── HTML (REQ-010) ───────────────────────────────────────────────────────

_SKIP_TAGS = frozenset({"script", "style", "noscript", "nav", "header", "footer", "form", "svg"})
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3}
_PARA_TAGS = frozenset({"p", "li", "td", "dd", "blockquote", "pre"})
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)


class _PageBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._skip = 0
        self._open: list[str] = []
        self._parts: list[str] = []

    def _flush(self) -> None:
        text = _clean("".join(self._parts))
        self._parts = []
        if not text or not self._open:
            return
        tag = self._open[-1]
        if tag in _HEADING_TAGS:
            self.blocks.append(Block("heading", text, _HEADING_TAGS[tag]))
        else:
            self.blocks.append(Block("para", text))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif self._skip:
            return
        elif tag in _HEADING_TAGS or tag in _PARA_TAGS:
            self._flush()
            self._open.append(tag)
        elif tag == "br" and self._open:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(self._skip - 1, 0)
        elif self._skip or tag not in self._open:
            return
        else:
            self._flush()
            while self._open.pop() != tag:
                pass

    def handle_data(self, data):
        if not self._skip and self._open:
            self._parts.append(data)

    def close(self):
        super().close()
        self._flush()


def _decode_html(data: bytes, charset: str) -> tuple[str, str]:
    meta = _META_CHARSET.search(data[:4096])
    for declared in (charset, meta.group(1).decode("ascii") if meta else ""):
        if not declared:
            continue
        try:
            return data.decode(declared), ""
        except (LookupError, UnicodeError):
            continue
    return _decode_text(data)


def extract_html(data: bytes, charset: str = "") -> Extracted:
    text, note = _decode_html(data, charset)
    parser = _PageBlocks()
    parser.feed(_newlines(text))
    parser.close()
    return _finish(Extracted("html", parser.blocks, note))


# ── REQ-011 and the entry points ─────────────────────────────────────────

def _finish(extracted: Extracted) -> Extracted:
    total = sum(len(b.text) + (len(b.fields["title"]) if b.fields else 0) for b in extracted.blocks)
    if total > MAX_CHARS:
        raise _reject("too_long")
    letters = sum(1 for b in extracted.blocks for ch in b.text if ch.isalpha())
    if letters < MIN_LETTERS:
        raise _reject("no_text")
    return extracted


def extract_file(data: bytes, filename: str) -> Extracted:
    fmt = detect_format(data, filename)
    if fmt == "pdf":
        extracted = _extract_pdf(data)
    elif fmt == "docx":
        extracted = _extract_docx(data)
    elif fmt == "xlsx":
        extracted = _extract_xlsx(data)
    elif fmt == "csv":
        extracted = _extract_csv(data)
    else:
        extracted = _extract_text(data, fmt)
    return _finish(extracted)


# ── The child process (REQ-013, REQ-014, SEC-009) ────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MEMORY_LIMIT_WARNING = "ingest_extract: memory limit not set: "


def _limit_memory() -> None:
    if not sys.platform.startswith("linux"):
        return
    import resource
    try:
        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_BYTES, CHILD_MEMORY_BYTES))
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"{_MEMORY_LIMIT_WARNING}{exc}\n")


def _kill_group(proc: subprocess.Popen) -> None:
    # The child runs in its own session, so this also stops a pdftotext it
    # started; killing only the child would leave pdftotext running unwatched.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _from_child(returncode: int, output: bytes) -> Extracted:
    try:
        payload = json.loads(output)
        if returncode != 0 or not isinstance(payload, dict):
            raise ValueError("unusable child output")
        if not payload.get("ok"):
            code = payload.get("code")
            raise _reject(code if code in MESSAGES else "parse_failed")
        blocks = [Block(b["kind"], b["text"], b["level"], b["fields"]) for b in payload["blocks"]]
        return Extracted(payload["format"], blocks, payload["encoding_note"])
    except IngestRejected:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise _reject("parse_failed") from exc


def extract_file_isolated(data: bytes, filename: str, timeout_s: float = 60.0) -> Extracted:
    ext = _extension(filename)
    label = f"upload.{ext}" if ext in FORMATS else "upload"
    # Only PATH (to find pdftotext) and TMPDIR cross over: the parser handles
    # hostile bytes and has no use for DATABASE_URL or an API key.
    env = {"PATH": os.environ.get("PATH", os.defpath)}
    if "TMPDIR" in os.environ:
        env["TMPDIR"] = os.environ["TMPDIR"]
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.services.ingest_extract", label],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=_REPO_ROOT, env=env, start_new_session=True,
        )
    except OSError as exc:
        raise _reject("parse_failed") from exc
    try:
        output, errors = proc.communicate(data, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _kill_group(proc)
        proc.communicate()
        raise _reject("timeout") from exc
    if _MEMORY_LIMIT_WARNING.encode() in errors:
        from app.services import applog
        applog.warning("content", "ingest_memory_limit_unavailable",
                       "The extraction child runs without RLIMIT_AS.")
    return _from_child(proc.returncode, output)


def _child_main() -> int:
    _limit_memory()
    filename = sys.argv[1] if len(sys.argv) > 1 else ""
    data = sys.stdin.buffer.read()
    try:
        extracted = extract_file(data, filename)
        payload = {"ok": True, "format": extracted.format, "encoding_note": extracted.encoding_note,
                   "blocks": [asdict(block) for block in extracted.blocks]}
    except IngestRejected as exc:
        payload = {"ok": False, "code": exc.code}
    except Exception:
        # This is the process boundary: a parser fed hostile bytes can raise
        # anything, MemoryError under RLIMIT_AS included, and the admin gets
        # one code for all of it.
        payload = {"ok": False, "code": "parse_failed"}
    sys.stdout.buffer.write(json.dumps(payload).encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(_child_main())
