"""Safe file reading for knowledge ingestion (slice S1).

The admin uploads bytes the server has never seen. This file pins the contract
in docs/features/knowledge-ingestion/SPEC.md, section A (REQ-001..REQ-016),
the Foreman decisions D1..D4 for slice S1, and the hostile fixtures H1..H10:
every attack RESEARCH.md measured (X2 entity expansion, X3 zip bomb with a
lying header) must be refused with its section 8 code before a parser runs,
and a normal Persian file of each format must come back with its text
untouched (ZWNJ, Arabic ي/ك and Persian digits included).

The small Persian fixtures in tests/fixtures/ingest were built once from
SAMPLE below (that folder's README says how). Hostile inputs are built here,
so no large binary is committed. Tests that run the real pdftotext skip with a
reason when poppler is missing; CI installs poppler-utils. SC-024 (RLIMIT_AS)
is only enforced on Linux, so it skips on macOS and is NOT RUN there.

The child-timeout test runs its fake interpreter once before the timed call:
on a cold run here, the first exec of a freshly written script took longer
than the one-second timeout, and the test then measured the OS, not the code.
"""
import csv
import io
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path

import openpyxl
import pytest

from app.services import ingest_extract as ie
from app.services.ingest_extract import (
    Block,
    Extracted,
    IngestRejected,
    detect_format,
    extract_file,
    extract_file_isolated,
    extract_html,
    pdf_available,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "ingest"
MIB = 1024 * 1024

SAMPLE = [
    ("heading", "ساعت‌های بازدید"),
    ("para", "نمایشگاه هر روز از ساعت ۹ صبح تا ۱۸ باز است و بازدیدکننده‌ها می‌توانند بلیت را در ورودی بخرند."),
    ("heading", "ثبت‌نام غرفه‌ها"),
    ("para", "شرکت‌ها باید فرم ثبت‌نام را تا ۱۵ مهر پر کنند. هزینهٔ هر غرفه ۲٬۵۰۰٬۰۰۰ تومان است."),
    ("para", "براي پرسش درباره پاركينگ با دبيرخانه تماس بگيريد."),
    ("para", "برای پرسش‌های دیگر به info@example.com نامه بفرستید."),
]

MESSAGES_FA = {
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

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452") + b"\x00" * 64
LONG_PARA = "این یک پاراگراف معمولی است که برای آزمون خواندن فایل نوشته شده است."

needs_pdftotext = pytest.mark.skipif(
    not shutil.which("pdftotext"),
    reason="poppler's pdftotext is not installed here; CI installs poppler-utils",
)
linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="SC-024: RLIMIT_AS is set and enforced only on Linux; NOT RUN on this OS",
)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(IngestRejected) as exc:
        fn(*args, **kwargs)
    return exc.value.code


def _shape(extracted: Extracted) -> list[tuple]:
    return [(b.kind, b.text, b.level) for b in extracted.blocks]


def _zip(members: dict, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _p(text: str, style: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _document(body: str, prolog: str = "") -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
            f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>')


def _docx(body: str = "", extra: dict | None = None, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    members = {"[Content_Types].xml": CONTENT_TYPES,
               "word/document.xml": _document(body or _p(LONG_PARA))}
    members.update(extra or {})
    return _zip(members, method)


def _xlsx(rows: list, second_sheet: list | None = None) -> bytes:
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    if second_sheet is not None:
        other = wb.create_sheet("دوم")
        for row in second_sheet:
            other.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(rows: list) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode("utf-8")


def _bomb_docx(inflated: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        with zf.open("word/document.xml", "w") as fh:
            chunk = b"\x00" * MIB
            for _ in range(inflated // MIB):
                fh.write(chunk)
    return buf.getvalue()


def _central_entries(data: bytes):
    eocd = data.rfind(b"PK\x05\x06")
    count, _size, pos = struct.unpack_from("<HLL", data, eocd + 10)
    for _ in range(count):
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        local = struct.unpack_from("<L", data, pos + 42)[0]
        yield data[pos + 46:pos + 46 + name_len].decode(), pos, local
        pos += 46 + name_len + extra_len + comment_len


def _patch_member(data: bytes, name: str, *, flags: int = 0, size: int | None = None,
                  crc: int | None = None) -> bytes:
    buf = bytearray(data)
    for member, central, local in _central_entries(data):
        if member != name:
            continue
        if flags:
            struct.pack_into("<H", buf, central + 8, struct.unpack_from("<H", buf, central + 8)[0] | flags)
            struct.pack_into("<H", buf, local + 6, struct.unpack_from("<H", buf, local + 6)[0] | flags)
        if size is not None:
            struct.pack_into("<L", buf, central + 24, size)
            struct.pack_into("<L", buf, local + 22, size)
        if crc is not None:
            struct.pack_into("<L", buf, central + 16, crc)
            struct.pack_into("<L", buf, local + 14, crc)
    return bytes(buf)


def _patch_eocd(data: bytes, *, entries: int | None = None, directory_bytes: int | None = None) -> bytes:
    buf = bytearray(data)
    eocd = data.rfind(b"PK\x05\x06")
    if entries is not None:
        struct.pack_into("<HH", buf, eocd + 8, entries, entries)
    if directory_bytes is not None:
        struct.pack_into("<L", buf, eocd + 12, directory_bytes)
    return bytes(buf)


def _pdf(content: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def _fake_interpreter(tmp_path: Path, script: str) -> str:
    path = tmp_path / "fake-python"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return str(path)


# ── REQ-001: the public contract and the section 8 sentences ─────────────

@pytest.mark.parametrize("code", sorted(MESSAGES_FA))
def test_every_s1_code_carries_its_section_8_sentence(code):
    assert ie.MESSAGES[code] == MESSAGES_FA[code]


def test_a_rejection_carries_the_code_and_the_admin_sentence():
    with pytest.raises(IngestRejected) as exc:
        detect_format(PNG, "scan.pdf")
    assert exc.value.code == "bad_type"
    assert exc.value.message_fa == MESSAGES_FA["bad_type"]


def test_the_upload_cap_is_20_mib_and_defined_here():
    assert ie.MAX_FILE_BYTES == 20 * MIB


# ── REQ-002, SEC-005, D3: extension and magic bytes together ─────────────

@pytest.mark.parametrize("name, expected", [
    ("fa-sample.pdf", "pdf"),
    ("fa-sample.docx", "docx"),
    ("fa-sample.xlsx", "xlsx"),
    ("fa-sample.csv", "csv"),
    ("fa-sample-cp1256.txt", "txt"),
])
def test_a_valid_small_file_of_each_format_is_recognised(name, expected):
    assert detect_format(_fixture(name), name) == expected
    assert detect_format(_fixture(name), name.upper()) == expected


def test_markdown_is_recognised_by_its_extension():
    assert detect_format("# عنوان\n\nمتن".encode(), "notes.md") == "md"


@pytest.mark.parametrize("data, name", [
    (PNG, "scan.pdf"),                                        # H6
    (b"%PDF-1.4\n", "report.docx"),
    ("docx", "sheet.xlsx"),
    ("xlsx", "letter.docx"),
    (b"%PDF-1.4\n", "notes.txt"),
    ("docx", "table.csv"),
    ("docx", "readme.md"),
    ("متن ساده".encode(), "program.exe"),
    ("متن ساده".encode(), "no-extension"),
    (b"", "empty.txt"),
], ids=["png-as-pdf", "pdf-as-docx", "docx-as-xlsx", "xlsx-as-docx", "pdf-as-txt",
        "zip-as-csv", "zip-as-md", "unknown-ext", "no-ext", "empty"])
def test_an_extension_that_does_not_match_the_bytes_is_bad_type(data, name):
    if data == "docx":
        data = _fixture("fa-sample.docx")
    elif data == "xlsx":
        data = _fixture("fa-sample.xlsx")
    assert _code(detect_format, data, name) == "bad_type"


def test_a_zip_without_the_office_members_is_not_a_docx():
    assert _code(detect_format, _zip({"word/document.xml": _document(_p("x"))}), "a.docx") == "bad_type"
    assert _code(detect_format, _zip({"[Content_Types].xml": CONTENT_TYPES}), "a.docx") == "bad_type"


def test_a_file_over_20_mib_is_too_large_and_exactly_20_mib_is_accepted():
    assert detect_format(b"a" * ie.MAX_FILE_BYTES, "big.txt") == "txt"
    assert _code(detect_format, b"a" * (ie.MAX_FILE_BYTES + 1), "big.txt") == "too_large"
    assert _code(extract_file, b"a" * (ie.MAX_FILE_BYTES + 1), "big.txt") == "too_large"


# ── REQ-001 EOCD pre-check and D2: refuse before zipfile reads the list ──

@pytest.fixture
def no_zipfile(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("zipfile.ZipFile must not be built for this input")
    monkeypatch.setattr(zipfile, "ZipFile", refuse)


@pytest.mark.parametrize("patch", [
    {"entries": 2001},
    {"entries": 0xFFFF},
    {"directory_bytes": 4 * MIB + 1},
    {"directory_bytes": 0xFFFFFFFF},
], ids=["2001-entries", "zip64-count", "directory-over-4mib", "zip64-size"])
def test_a_hostile_end_record_is_zip_too_big_before_listing(patch, no_zipfile):
    data = _patch_eocd(_fixture("fa-sample.docx"), **patch)
    assert _code(detect_format, data, "a.docx") == "zip_too_big"


def test_a_zip64_locator_counts_as_zip64(no_zipfile):
    data = _fixture("fa-sample.docx")
    eocd = data.rfind(b"PK\x05\x06")
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
    assert _code(detect_format, data[:eocd] + locator + data[eocd:], "a.docx") == "zip_too_big"


def test_a_zip_without_an_end_record_is_bad_type(no_zipfile):
    data = _fixture("fa-sample.docx")
    assert _code(detect_format, data[:data.rfind(b"PK\x05\x06")], "a.docx") == "bad_type"


def test_a_lying_entry_count_is_caught_by_the_real_list_length():
    members = {f"word/media/m{i}.xml": b"" for i in range(1999)}
    data = _docx(extra=members)
    assert len(zipfile.ZipFile(io.BytesIO(data)).namelist()) == 2001
    assert _code(detect_format, _patch_eocd(data, entries=5), "a.docx") == "zip_too_big"


def test_exactly_2000_members_is_accepted():
    members = {f"word/media/m{i}.xml": b"" for i in range(1998)}
    assert detect_format(_docx(extra=members), "a.docx") == "docx"


# ── REQ-003, SEC-006: the zip gate counts real bytes ─────────────────────

@pytest.fixture
def parsers_must_not_run(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a parser ran before the zip gate refused the file")
    monkeypatch.setattr("defusedxml.ElementTree.fromstring", refuse)
    monkeypatch.setattr(openpyxl, "load_workbook", refuse)


def test_h1_an_honest_zip_bomb_is_refused_before_the_docx_parser(monkeypatch, parsers_must_not_run):
    monkeypatch.setattr(ie, "MAX_UNZIPPED_BYTES", 1 * MIB)
    bomb = _bomb_docx(2 * MIB)
    assert detect_format(bomb, "bomb.docx") == "docx", "detection only reads the list, it inflates nothing"
    assert _code(extract_file, bomb, "bomb.docx") == "zip_too_big"


def test_h1_a_bomb_with_a_lying_size_and_crc_is_still_refused(monkeypatch, parsers_must_not_run):
    monkeypatch.setattr(ie, "MAX_UNZIPPED_BYTES", 1 * MIB)
    lying = _patch_member(_bomb_docx(2 * MIB), "word/document.xml",
                          size=1024, crc=zlib.crc32(b"\x00" * 1024))
    info = zipfile.ZipFile(io.BytesIO(lying)).getinfo("word/document.xml")
    assert info.file_size == 1024, "the header must really lie for this test to mean anything"
    assert _code(extract_file, lying, "bomb.docx") == "zip_too_big"


def test_the_real_100_mib_cap_refuses_a_real_bomb(parsers_must_not_run):
    bomb = _bomb_docx(101 * MIB)
    assert len(bomb) < MIB
    started = time.monotonic()
    assert _code(extract_file, bomb, "bomb.docx") == "zip_too_big"
    assert time.monotonic() - started < 15


def test_a_file_under_the_cap_is_counted_and_accepted(monkeypatch):
    monkeypatch.setattr(ie, "MAX_UNZIPPED_BYTES", 1 * MIB)
    data = _docx(extra={"word/media/pad.bin": b"\x00" * (MIB // 2)})
    assert _shape(extract_file(data, "a.docx")) == [("para", LONG_PARA, 0)]


def test_h4_a_member_with_the_encryption_flag_is_encrypted(parsers_must_not_run):
    data = _patch_member(_docx(), "word/document.xml", flags=0x1)
    assert _code(extract_file, data, "locked.docx") == "encrypted"


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
def test_a_compression_method_other_than_stored_or_deflate_is_bad_zip(method, parsers_must_not_run):
    assert _code(extract_file, _docx(method=method), "odd.docx") == "bad_zip"


def test_stored_members_are_read_normally():
    assert _shape(extract_file(_docx(method=zipfile.ZIP_STORED), "a.docx")) == [("para", LONG_PARA, 0)]


# ── REQ-006, REQ-007, SEC-007: XML without a DTD ─────────────────────────

def test_h2_a_billion_laughs_docx_is_dtd_forbidden_quickly():
    assert detect_format(_fixture("lol.docx"), "lol.docx") == "docx", "detection parses no XML"
    started = time.monotonic()
    assert _code(extract_file, _fixture("lol.docx"), "lol.docx") == "dtd_forbidden"
    assert time.monotonic() - started < 2


def test_any_dtd_in_a_docx_is_forbidden_even_without_entities():
    data = _zip({"[Content_Types].xml": CONTENT_TYPES,
                 "word/document.xml": _document(_p(LONG_PARA), prolog="<!DOCTYPE w:document>")})
    assert _code(extract_file, data, "a.docx") == "dtd_forbidden"


def test_h3_a_billion_laughs_xlsx_is_dtd_forbidden_before_openpyxl(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("openpyxl must not see a workbook with a DTD")
    monkeypatch.setattr(openpyxl, "load_workbook", refuse)
    assert _code(extract_file, _fixture("lol.xlsx"), "lol.xlsx") == "dtd_forbidden"


def test_a_dtd_hidden_past_the_first_4_kib_of_a_sheet_is_refused_without_expansion():
    padding = "<!--" + "x" * 5000 + "-->"
    source = zipfile.ZipFile(io.BytesIO(_fixture("lol.xlsx")))
    members = {}
    for info in source.infolist():
        payload = source.read(info)
        if info.filename == "xl/worksheets/sheet1.xml":
            payload = payload.replace(b"?><!DOCTYPE", b"?>" + padding.encode() + b"<!DOCTYPE", 1)
            assert b"<!DOCTYPE" not in payload[:4096]
        members[info.filename] = payload
    started = time.monotonic()
    assert _code(extract_file, _zip(members), "hidden.xlsx") == "parse_failed"
    assert time.monotonic() - started < 2, "defusedxml must refuse the entities, not expand them"


def test_an_xlsx_is_refused_when_openpyxl_lost_its_defusedxml(monkeypatch):
    monkeypatch.setattr("openpyxl.xml.DEFUSEDXML", False)
    assert _code(extract_file, _fixture("fa-sample.xlsx"), "fa-sample.xlsx") == "parse_failed"


# ── REQ-006, SC-001 (extraction half): DOCX structure ───────────────────

def test_sc001_a_persian_docx_keeps_two_headings_and_four_paragraphs_exactly():
    result = extract_file(_fixture("fa-sample.docx"), "fa-sample.docx")
    assert result.format == "docx"
    assert result.encoding_note == ""
    assert _shape(result) == [(kind, text, 2 if kind == "heading" else 0) for kind, text in SAMPLE]
    assert sum(b.text.count("\u200c") for b in result.blocks) == 8


@pytest.mark.parametrize("style, level", [
    ("Title", 1), ("Heading1", 1), ("Heading2", 2), ("Heading3", 3), ("Heading5", 3), ("Heading", 1),
])
def test_heading_styles_become_headings_with_a_level_up_to_3(style, level):
    result = extract_file(_docx(_p("عنوان بخش", style) + _p(LONG_PARA)), "a.docx")
    assert _shape(result)[0] == ("heading", "عنوان بخش", level)


def test_a_normal_style_is_a_paragraph():
    assert _shape(extract_file(_docx(_p(LONG_PARA, "Normal")), "a.docx")) == [("para", LONG_PARA, 0)]


def test_runs_tabs_and_breaks_join_into_one_paragraph():
    body = ('<w:p><w:r><w:t>شرکت</w:t></w:r><w:r><w:t>\u200cها</w:t><w:tab/>'
            '<w:t>ساعت نه تا هجده باز هستند</w:t><w:br/><w:t>و روز جمعه تعطیل است.</w:t></w:r></w:p>')
    assert _shape(extract_file(_docx(body), "a.docx")) == [
        ("para", "شرکت\u200cها ساعت نه تا هجده باز هستند\nو روز جمعه تعطیل است.", 0)]


def test_each_table_row_is_one_paragraph_joined_with_a_bar():
    def cell(text):
        return f"<w:tc>{_p(text)}</w:tc>"
    table = ("<w:tbl>"
             f"<w:tr>{cell('نام غرفه')}{cell('ساعت کاری')}</w:tr>"
             f"<w:tr>{cell('غرفهٔ اول')}{cell('۹ تا ۱۸')}</w:tr>"
             "</w:tbl>")
    assert _shape(extract_file(_docx(_p(LONG_PARA) + table), "a.docx")) == [
        ("para", LONG_PARA, 0),
        ("para", "نام غرفه | ساعت کاری", 0),
        ("para", "غرفهٔ اول | ۹ تا ۱۸", 0),
    ]


def test_deeply_nested_docx_content_is_read_without_recursion():
    depth = 3000
    body = "<w:sdt><w:sdtContent>" * depth + _p(LONG_PARA) + "</w:sdtContent></w:sdt>" * depth
    assert _shape(extract_file(_docx(body), "deep.docx")) == [("para", LONG_PARA, 0)]


def test_a_docx_with_an_empty_body_has_no_text():
    assert _code(extract_file, _docx(_p("   ")), "a.docx") == "no_text"


# ── REQ-007, REQ-008: XLSX and CSV tables ───────────────────────────────

def test_a_persian_xlsx_becomes_rows_with_title_and_text():
    result = extract_file(_fixture("fa-sample.xlsx"), "fa-sample.xlsx")
    assert result.format == "xlsx"
    assert [(b.kind, b.text, b.fields) for b in result.blocks] == [
        ("row", SAMPLE[1][1], {"title": SAMPLE[0][1], "text": SAMPLE[1][1]}),
        ("row", SAMPLE[3][1], {"title": SAMPLE[2][1], "text": SAMPLE[3][1]}),
        ("row", SAMPLE[4][1], {"title": "", "text": SAMPLE[4][1]}),
        ("row", SAMPLE[5][1], {"title": "", "text": SAMPLE[5][1]}),
    ]


def test_a_persian_csv_gives_the_same_rows():
    result = extract_file(_fixture("fa-sample.csv"), "fa-sample.csv")
    assert result.format == "csv"
    assert [b.fields for b in result.blocks] == [
        b.fields for b in extract_file(_fixture("fa-sample.xlsx"), "fa-sample.xlsx").blocks]


def test_only_the_first_sheet_is_read():
    data = _xlsx([["پرسش", "جواب"], ["ساعت کاری", LONG_PARA]],
                 second_sheet=[["پرسش", "جواب"], ["برگهٔ دوم", "این متن نباید خوانده شود و دیده نشود."]])
    assert [b.fields["title"] for b in extract_file(data, "a.xlsx").blocks] == ["ساعت کاری"]


def test_a_formula_without_a_stored_value_is_an_empty_cell():
    data = _xlsx([["عنوان", "پاسخ"], ["فرمول", "=1+1"], ["ساعت کاری", LONG_PARA]])
    assert [b.fields["title"] for b in extract_file(data, "a.xlsx").blocks] == ["ساعت کاری"]


def test_an_xlsx_without_an_answer_column_is_missing_columns():
    assert _code(extract_file, _xlsx([["نام", "تلفن"], ["علی", "0912"]]), "a.xlsx") == "missing_columns"


@pytest.mark.parametrize("title_header, text_header", [
    ("title", "text"), ("Question", "ANSWER"), (" عنوان ", "پاسخ"), ("سوال", "جواب"),
    ("سؤال", "متن"), ("پرسش", "توضیح"),
])
def test_every_header_synonym_finds_its_column(title_header, text_header):
    data = _csv([["شناسه", title_header, text_header], ["1", "ساعت کاری", LONG_PARA]])
    assert extract_file(data, "a.csv").blocks[0].fields == {"title": "ساعت کاری", "text": LONG_PARA}


def test_the_title_column_is_optional():
    data = _csv([["پاسخ"], [LONG_PARA]])
    assert extract_file(data, "a.csv").blocks[0].fields == {"title": "", "text": LONG_PARA}


def test_a_csv_without_an_answer_column_is_missing_columns():
    assert _code(extract_file, _csv([["نام", "تلفن"], ["علی", "0912"]]), "a.csv") == "missing_columns"


def test_only_the_first_20_columns_are_read():
    header = [f"ستون {i}" for i in range(21)] + ["پاسخ"]
    assert _code(extract_file, _csv([header, ["x"] * 21 + [LONG_PARA]]), "a.csv") == "missing_columns"


def test_rows_with_an_empty_answer_and_blank_lines_are_skipped():
    data = ("عنوان,پاسخ\n\nبی\u200cپاسخ,\n,\nساعت کاری," + LONG_PARA + "\n").encode()
    assert [b.fields["title"] for b in extract_file(data, "a.csv").blocks] == ["ساعت کاری"]


def test_5000_data_rows_are_accepted():
    rows = [["عنوان", "پاسخ"]] + [[f"ردیف {i}", "پاسخ کوتاه ردیف"] for i in range(5000)]
    assert len(extract_file(_csv(rows), "a.csv").blocks) == 5000


def test_h10_5001_data_rows_is_too_many_rows():
    rows = [["عنوان", "پاسخ"]] + [[f"ردیف {i}", "پاسخ کوتاه ردیف"] for i in range(5001)]
    assert _code(extract_file, _csv(rows), "a.csv") == "too_many_rows"


# ── REQ-004, SEC-010, D4: one decode rule for CSV, TXT and MD ────────────

def _txt(text: str) -> str:
    return text + "\n\n" + LONG_PARA


def test_utf8_text_is_read_without_a_note():
    result = extract_file(_txt("سلام به همه").encode("utf-8"), "a.txt")
    assert result.encoding_note == ""
    assert result.blocks[0].text == "سلام به همه"


def test_a_utf8_bom_is_dropped():
    result = extract_file(b"\xef\xbb\xbf" + _txt("سلام به همه").encode("utf-8"), "a.txt")
    assert result.blocks[0].text == "سلام به همه"
    assert result.encoding_note == ""


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_with_a_bom_is_read_and_noted(codec):
    result = extract_file(("\ufeff" + _txt("سلام به همه")).encode(codec), "a.txt")
    assert result.encoding_note == "utf-16"
    assert result.blocks[0].text == "سلام به همه"


def test_plausible_cp1256_persian_is_read_and_noted():
    result = extract_file(_fixture("fa-sample-cp1256.txt"), "fa-sample-cp1256.txt")
    assert result.encoding_note == "cp1256"
    text = "\n".join(b.text for b in result.blocks)
    assert "ثبت\u200cنام" in text
    assert "دبيرخانه" in text
    assert "info@example.com" in text
    assert [b.kind for b in result.blocks] == ["heading", "para", "heading", "para", "para", "para"]


def test_h7_persian_utf16_without_a_bom_is_bad_encoding():
    assert _code(extract_file, _txt(SAMPLE[1][1]).encode("utf-16-le"), "a.txt") == "bad_encoding"


def test_english_utf16_without_a_bom_is_bad_encoding():
    data = "Opening hours are nine to six every day of the fair.".encode("utf-16-le")
    assert data.decode("utf-8"), "valid UTF-8 with NUL bytes: only D4 stops it"
    assert _code(extract_file, data, "a.txt") == "bad_encoding"


def test_a_single_nul_byte_in_utf8_text_is_bad_encoding():
    assert _code(extract_file, _txt("سلام\x00 به همه").encode("utf-8"), "a.md") == "bad_encoding"


def test_h8_random_bytes_are_bad_encoding():
    data = random.Random(8).randbytes(4096)
    assert detect_format(data, "noise.csv") == "csv", "the decode rule is not part of detection (REQ-002)"
    assert _code(extract_file, data, "noise.csv") == "bad_encoding"


def test_random_bytes_without_nul_fail_the_plausibility_gate():
    data = bytes(b for b in random.Random(9).randbytes(4096) if b)
    assert b"\x00" not in data
    assert _code(extract_file, data, "noise.txt") == "bad_encoding"


def test_latin_text_that_is_not_utf8_is_not_mistaken_for_cp1256():
    data = ("Café crème, déjà vu et à bientôt à la foire. " * 5).encode("latin-1")
    assert _code(extract_file, data, "a.txt") == "bad_encoding"


def test_binary_bytes_renamed_to_txt_are_bad_encoding():
    assert _code(extract_file, PNG, "image.txt") == "bad_encoding"


def test_decoding_never_invents_a_replacement_character():
    for name in ("fa-sample-cp1256.txt", "fa-sample.csv"):
        result = extract_file(_fixture(name), name)
        assert all("�" not in b.text for b in result.blocks)


# ── REQ-005, REQ-009: paragraph and heading rules for TXT, MD, PDF ───────

def test_txt_paragraphs_and_the_short_line_heading_rule():
    one_line_61 = "ب" * 61
    text = "\n\n".join([
        "عنوان کوتاه",
        "این پاراگراف دو خط دارد\nو خط دوم آن اینجاست.",
        "آیا این پرسش است؟",
        "نکته:",
        one_line_61,
    ])
    assert _shape(extract_file(text.encode(), "a.txt")) == [
        ("heading", "عنوان کوتاه", 2),
        ("para", "این پاراگراف دو خط دارد\nو خط دوم آن اینجاست.", 0),
        ("para", "آیا این پرسش است؟", 0),
        ("para", "نکته:", 0),
        ("para", one_line_61, 0),
    ]


def test_markdown_hashes_give_heading_levels_1_to_3():
    text = "# یک\nمتن بند اول که کمی بلندتر است.\n\n## دو\n### سه\n#### چهار\nمتن بند آخر."
    result = extract_file(text.encode(), "a.md")
    assert result.format == "md"
    assert _shape(result) == [
        ("heading", "یک", 1),
        ("para", "متن بند اول که کمی بلندتر است.", 0),
        ("heading", "دو", 2),
        ("heading", "سه", 3),
        ("para", "#### چهار\nمتن بند آخر.", 0),
    ]


# ── REQ-011, REQ-012: limits after extraction, whitespace only ───────────

def test_spaces_collapse_but_zwnj_arabic_letters_and_digits_stay():
    text = "این   متن\tبا    فاصله\u200cهای زیاد و حروف عربي ك و رقم ۱۲۳ و 45 است."
    assert extract_file(text.encode(), "a.txt").blocks[0].text == (
        "این متن با فاصله\u200cهای زیاد و حروف عربي ك و رقم ۱۲۳ و 45 است.")


def test_250000_characters_are_accepted_and_one_more_is_too_long():
    assert len(extract_file(("ب" * 250_000).encode(), "a.txt").blocks[0].text) == 250_000
    assert _code(extract_file, ("ب" * 250_001).encode(), "a.txt") == "too_long"


def test_fewer_than_20_letters_is_no_text():
    assert _code(extract_file, ("ب" * 19 + " ۱۲۳۴۵ 678").encode(), "a.txt") == "no_text"
    assert extract_file(("ب" * 20).encode(), "a.txt").blocks[0].text == "ب" * 20


# ── REQ-005, SC-002: PDF through pdftotext ───────────────────────────────

@needs_pdftotext
def test_sc002_the_persian_pdf_keeps_its_numbers_and_loses_bidi_controls():
    result = extract_file(_fixture("fa-sample.pdf"), "fa-sample.pdf")
    text = "\n".join(b.text for b in result.blocks)
    assert result.format == "pdf"
    assert "۲٬۵۰۰٬۰۰۰" in text
    assert "۱۸" in text
    assert "info@example.com" in text
    assert not any("\u202a" <= ch <= "\u202e" for ch in text)


@needs_pdftotext
def test_h5_an_encrypted_pdf_is_encrypted():
    assert _code(extract_file, _fixture("encrypted.pdf"), "locked.pdf") == "encrypted"


@needs_pdftotext
def test_an_image_only_pdf_has_no_text():
    drawing = _pdf(b"0 0 1 rg 20 20 160 160 re f")
    assert _code(extract_file, drawing, "scan.pdf") == "no_text"


@needs_pdftotext
def test_a_broken_pdf_is_parse_failed():
    assert _code(extract_file, b"%PDF-1.4\nthis is not a pdf body\n", "broken.pdf") == "parse_failed"


def test_a_missing_pdftotext_is_pdf_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert pdf_available() is False
    assert _code(extract_file, _fixture("fa-sample.pdf"), "a.pdf") == "pdf_tool_missing"


@needs_pdftotext
def test_pdf_available_is_true_when_pdftotext_is_on_path():
    assert pdf_available() is True


@pytest.fixture
def fake_pdftotext(monkeypatch):
    calls = []
    outcome = {"returncode": 0, "stdout": ("\u202b" + LONG_PARA + "\u202c\n").encode(), "stderr": b""}

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs, "mode": os.stat(argv[5]).st_mode & 0o777})
        if outcome.get("timeout"):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, outcome["returncode"], outcome["stdout"], outcome["stderr"])

    monkeypatch.setattr(shutil, "which", lambda name: "/opt/poppler/bin/pdftotext" if name == "pdftotext" else None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, outcome


def test_pdftotext_runs_with_the_contract_flags_and_a_private_temp_file(fake_pdftotext):
    calls, _ = fake_pdftotext
    result = extract_file(b"%PDF-1.4 fake", "../../../etc/evil.pdf")
    argv = calls[0]["argv"]
    assert argv[:5] == ["/opt/poppler/bin/pdftotext", "-enc", "UTF-8", "-l", "200"]
    assert argv[6] == "-"
    assert "evil" not in argv[5]
    assert calls[0]["kwargs"]["timeout"] == 45
    assert calls[0]["mode"] == 0o600
    assert not os.path.exists(argv[5]), "the temp copy of the upload must be deleted"
    assert _shape(result) == [("para", LONG_PARA, 0)]


@pytest.mark.parametrize("outcome, code", [
    ({"returncode": 1, "stderr": b"Command Line Error: Incorrect password\n"}, "encrypted"),
    ({"returncode": 3, "stderr": b"Permission Error\n"}, "encrypted"),
    ({"returncode": 1, "stderr": b"Syntax Error: Couldn't read xref table\n"}, "parse_failed"),
    ({"timeout": True}, "timeout"),
], ids=["password", "permissions", "broken", "timeout"])
def test_pdftotext_failures_map_to_their_codes(fake_pdftotext, outcome, code):
    calls, current = fake_pdftotext
    current.update(outcome)
    assert _code(extract_file, b"%PDF-1.4 fake", "a.pdf") == code
    assert not os.path.exists(calls[0]["argv"][5])


# ── REQ-010, D1: one web page ────────────────────────────────────────────

PAGE = """<html><head><title>عنوان زبانه</title><style>p { color: red }</style></head><body>
<header><p>منوی بالای صفحه</p></header><nav><ul><li>خانه</li></ul></nav>
<h1>درباره ما</h1><p>ما یک   شرکت کوچک هستیم &amp; از دیدن شما خوشحالیم.</p>
<script>var note = "متن اسکریپت";</script><noscript><p>جاوااسکریپت را روشن کنید</p></noscript>
<ul><li>ساعت کاری ۹ تا ۱۸</li><li><p>پاسخ درون فهرست</p></li></ul>
<h2>تماس</h2><table><tr><td>تلفن دفتر</td></tr></table><dl><dd>توضیح کوتاه</dd></dl>
<blockquote>نقل قول مشتری</blockquote><pre>کد   پیش\u200cقالب</pre>
<form><p>فرم ثبت\u200cنام</p></form><svg><text>متن تصویر</text></svg>
<footer><p>پانویس صفحه</p></footer><h4>عنوان چهارم</h4>
</body></html>"""


def test_a_page_keeps_headings_and_text_blocks_and_drops_page_chrome():
    result = extract_html(PAGE.encode("utf-8"))
    assert result.format == "html"
    assert _shape(result) == [
        ("heading", "درباره ما", 1),
        ("para", "ما یک شرکت کوچک هستیم & از دیدن شما خوشحالیم.", 0),
        ("para", "ساعت کاری ۹ تا ۱۸", 0),
        ("para", "پاسخ درون فهرست", 0),
        ("heading", "تماس", 2),
        ("para", "تلفن دفتر", 0),
        ("para", "توضیح کوتاه", 0),
        ("para", "نقل قول مشتری", 0),
        ("para", "کد پیش\u200cقالب", 0),
    ]


def test_d1_the_header_charset_is_tried_first():
    data = f"<p>{LONG_PARA}</p>".encode("utf-16-le")
    assert extract_html(data, charset="utf-16-le").blocks[0].text == LONG_PARA
    assert _code(extract_html, data) == "bad_encoding"


@pytest.mark.parametrize("meta", [
    '<meta charset="windows-1256">',
    '<meta http-equiv="Content-Type" content="text/html; charset=windows-1256">',
])
def test_a_meta_charset_decodes_the_page(meta):
    text = "سلام به همه دوستان عزيز در نمايشگاه امسال"
    result = extract_html(f"<html><head>{meta}</head><body><p>{text}</p></body></html>".encode("cp1256"))
    assert result.blocks[0].text == text
    assert result.encoding_note == ""


def test_a_wrong_declared_charset_falls_back_to_the_decode_rule():
    text = "سلام به همه دوستان عزيز در نمايشگاه امسال"
    data = f'<meta charset="utf-8"><p>{text}</p>'.encode("cp1256")
    result = extract_html(data)
    assert result.blocks[0].text == text
    assert result.encoding_note == "cp1256"


def test_a_page_built_by_javascript_has_no_text():
    page = "<html><body><div id=app></div><script>render('متن بسیار طولانی ساخته\u200cشده با جاوااسکریپت')</script></body></html>"
    assert _code(extract_html, page.encode()) == "no_text"


# ── REQ-013, SEC-009: the child process ─────────────────────────────────

def test_the_child_returns_the_same_result_as_in_process():
    for name in ("fa-sample.docx", "fa-sample.csv", "fa-sample-cp1256.txt"):
        assert extract_file_isolated(_fixture(name), name) == extract_file(_fixture(name), name)


@pytest.mark.parametrize("name, data, code", [
    ("lol.docx", "lol.docx", "dtd_forbidden"),
    ("scan.pdf", PNG, "bad_type"),
    ("big.txt", b"a" * (20 * MIB + 1), "too_large"),
], ids=["dtd", "bad-type", "too-large"])
def test_rejection_codes_cross_the_process_boundary(name, data, code):
    if isinstance(data, str):
        data = _fixture(data)
    assert _code(extract_file_isolated, data, name) == code


def test_a_slow_child_is_killed_with_its_children_and_gives_timeout(monkeypatch, tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    fake = _fake_interpreter(tmp_path, f'[ "$1" = warm ] && exit 0\nsleep 30 &\necho $! > \'{pid_file}\'\nwait\n')
    subprocess.run([fake, "warm"], check=True, timeout=30)
    monkeypatch.setattr(sys, "executable", fake)
    started = time.monotonic()
    assert _code(extract_file_isolated, _fixture("fa-sample.docx"), "a.docx", timeout_s=2.0) == "timeout"
    assert time.monotonic() - started < 10
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the child's own child (pdftotext's place) outlived the timeout")


@pytest.mark.parametrize("script", [
    "exit 3\n",
    "echo 'not json at all'\n",
    """echo '{"ok": false, "code": "rm -rf /"}'\n""",
], ids=["crash", "garbage", "unknown-code"])
def test_a_broken_child_is_parse_failed(monkeypatch, tmp_path, script):
    monkeypatch.setattr(sys, "executable", _fake_interpreter(tmp_path, script))
    assert _code(extract_file_isolated, _fixture("fa-sample.docx"), "a.docx") == "parse_failed"


def test_the_child_gets_no_secrets_from_the_parent_environment(monkeypatch, tmp_path):
    seen = tmp_path / "env.txt"
    monkeypatch.setenv("DATABASE_URL", "postgresql://padyar:secret@127.0.0.1/padyar")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(sys, "executable", _fake_interpreter(tmp_path, f"env > '{seen}'\nexit 1\n"))
    _code(extract_file_isolated, _fixture("fa-sample.docx"), "a.docx")
    env = seen.read_text()
    assert "DATABASE_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert "PATH=" in env


def test_a_refused_memory_limit_is_logged_by_the_parent(monkeypatch, tmp_path):
    from app.services import applog

    logged = []
    monkeypatch.setattr(applog, "warning", lambda *args, **kwargs: logged.append((args, kwargs)))
    ok = '{"ok": true, "format": "txt", "encoding_note": "", "blocks": [{"kind": "para", "text": "x", "level": 0, "fields": null}]}'
    monkeypatch.setattr(sys, "executable", _fake_interpreter(
        tmp_path, f"echo '{ie._MEMORY_LIMIT_WARNING}invalid argument' >&2\necho '{ok}'\n"))
    result = extract_file_isolated(b"x", "a.txt")
    assert result.blocks == [Block("para", "x", 0, None)]
    assert len(logged) == 1


@linux_only
def test_sc024_a_2_gib_allocation_fails_under_the_child_memory_cap():
    code = ("from app.services import ingest_extract as m\n"
            "m._limit_memory()\n"
            "block = bytearray(2 * 1024 ** 3)\n")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, timeout=60)
    assert proc.returncode != 0
    assert b"MemoryError" in proc.stderr or proc.returncode < 0


@linux_only
def test_sc024_the_real_child_sets_the_limit_without_a_warning(monkeypatch):
    from app.services import applog

    logged = []
    monkeypatch.setattr(applog, "warning", lambda *args, **kwargs: logged.append((args, kwargs)))
    extract_file_isolated(_fixture("fa-sample.csv"), "fa-sample.csv")
    assert logged == []


# ── REQ-014: the child imports nothing that opens a database or network ─

def test_the_extractor_module_imports_no_app_config_database_or_http_client():
    probe = "import sys, app.services.ingest_extract\nprint('\\n'.join(sorted(sys.modules)))"
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    modules = set(proc.stdout.split())
    assert {m for m in modules if m == "app" or m.startswith("app.")} == {
        "app", "app.services", "app.services.ingest_extract"}
    assert not modules & {"sqlite3", "psycopg", "httpx", "openai", "dotenv"}


# ── REQ-016, SEC-011: the uploaded name is only a label ─────────────────

def test_a_path_like_name_is_never_touched_on_disk(tmp_path):
    target = tmp_path / "planted.txt"
    result = extract_file(_txt("سلام به همه").encode(), str(target))
    assert result.format == "txt"
    assert not target.exists()
    result = extract_file_isolated(_txt("سلام به همه").encode(), "../../" + str(target) + "\x00.txt")
    assert result.format == "txt"
    assert list(tmp_path.iterdir()) == []


# ── REQ-015: dependencies and poppler reach every install and CI job ────

def test_parsers_are_base_dependencies_and_poppler_is_installed_everywhere():
    def requirements(name):
        lines = (ROOT / name).read_text().splitlines()
        return {line.split("#")[0].strip().lower() for line in lines} - {""}

    assert {"openpyxl", "defusedxml"} <= requirements("requirements.txt")
    assert "openpyxl" not in requirements("requirements-dev.txt")
    assert "poppler-utils" in (ROOT / "deploy" / "00-bootstrap-server.sh").read_text()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    test_job = ci.split("\n  test:\n", 1)[1].split("\n  postgres-tests:\n", 1)[0]
    postgres_job = ci.split("\n  postgres-tests:\n", 1)[1].split("\n  dependency-audit:\n", 1)[0]
    assert "poppler-utils" in test_job
    assert "poppler-utils" in postgres_job
