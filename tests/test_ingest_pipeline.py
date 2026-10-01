"""Knowledge ingestion, slice S3: the job, the chunks, the duplicate labels and
the AI step (docs/features/knowledge-ingestion/SPEC.md, REQ-024..REQ-051).

What this file protects:

- the entry text is the document's own words, cut by REQ-030 and never
  rewritten by the model (ADR-018: the model chooses, it does not author);
- a duplicate is decided by TEXT; a title shared with a live row is only a
  soft note, because one long section yields several chunks under one heading;
- the AI step is paced, stops on an open circuit or three errors in a row, and
  never retries in a loop (REQ-049), and with the AI off a job still finishes;
- one job at a time holds the model slot, enforced by a partial unique index,
  and a cancel keeps the slot until the call in flight ends (REQ-037, REQ-039);
- a dead worker's job is recovered, and old finished jobs are purged without
  touching a pending proposal or a live dataset row (REQ-036, REQ-041).

The database is a real throwaway SQLite file. The network is never touched:
the AI is faked on the `padyar_ai` instance and the call receives AIMessage
objects (decisions D1 and D3 of the S3 contract), extraction is replaced at
`ingest_extract.extract_file_isolated` where the test is not about the child
process, and the embedding model is a small deterministic fake behind
`embeddings._get_model`. The PostgreSQL half of the slot and the unique hash
is in tests/postgres/test_ingest_pg.py.
"""
import asyncio
import json
import os
import stat
import tempfile

import numpy as np
import pytest

from app.services.ingest_extract import Block, Extracted


ZWNJ = "‌"


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.config as config
    from app.services import embeddings, ingest
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ingest.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: False)
    monkeypatch.setattr(ingest, "PACE_SECONDS", 0)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))
    from app.db.connection import init_db
    init_db()
    yield uploads


def _rows(query, params=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _exec(query, params=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _later(job_id, seconds):
    _exec(f"UPDATE ingest_jobs SET created_at = datetime('now', '+{seconds} seconds') WHERE id = ?",
          (job_id,))


def _job(job_id):
    return _rows("SELECT * FROM ingest_jobs WHERE id = ?", (job_id,))[0]


def _proposals(job_id):
    rows = _rows("SELECT * FROM ingest_proposals WHERE job_id = ? ORDER BY seq", (job_id,))
    for row in rows:
        row["questions"] = json.loads(row["questions"])
        row["synonyms"] = json.loads(row["synonyms"])
    return rows


def _para(text):
    return Block("para", text)


def _heading(text):
    return Block("heading", text, 2)


def _sentences(count, stem="جمله"):
    """`count` sentences of exactly 100 characters each, space-separated."""
    out = []
    for i in range(count):
        body = f"{stem} شماره {i:03d} از متن نمونه"
        out.append((body + " " + "ب" * 100)[:99] + ".")
    return " ".join(out)


@pytest.fixture
def extract_as(monkeypatch):
    from app.services import ingest_extract
    seen = []

    def use(extracted):
        def fake(data, filename, timeout_s=60.0):
            seen.append(filename)
            if isinstance(extracted, Exception):
                raise extracted
            return extracted
        monkeypatch.setattr(ingest_extract, "extract_file_isolated", fake)
        return seen
    return use


async def _run(blocks, extract_as, name="doc.docx", fmt="docx"):
    from app.services import ingest
    extract_as(Extracted(fmt, blocks))
    job, existing = ingest.create_job("file", name, os.urandom(32), fmt, "admin")
    assert existing is False
    await ingest.run_job(job["id"])
    return job["id"]


async def _extracted_job(blocks, extract_as, monkeypatch):
    """A job left in `extracted`: the AI counts as on, so run_job skips the
    local shortcut, but it does not enter the model loop."""
    from app.services import ingest
    from app.services.ai.wrapper import padyar_ai

    async def no_loop():
        return None
    real_loop, real_enabled = ingest.run_model_loop, padyar_ai.external_ai_enabled
    monkeypatch.setattr(ingest, "run_model_loop", no_loop)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: True)
    try:
        return await _run(blocks, extract_as)
    finally:
        monkeypatch.setattr(ingest, "run_model_loop", real_loop)
        monkeypatch.setattr(padyar_ai, "external_ai_enabled", real_enabled)


class FakeAI:
    """Stands in for padyar_ai.generate: records every call, answers in order."""

    def __init__(self, *replies):
        self.calls = []
        self.replies = list(replies)

    async def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        reply = self.replies.pop(0) if self.replies else _reply()
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _reply(title="", questions=(), synonyms=(), finish="stop", content=None, **extra):
    from app.services.ai.request import AIResponse
    body = {"title": title, "questions": list(questions),
            "synonyms": [{"word": w, "suggestion": s} for w, s in synonyms], **extra}
    return AIResponse(content=json.dumps(body, ensure_ascii=False) if content is None else content,
                      finish_reason=finish, tokens_output=17, latency_ms=4)


def _ai_error(code):
    from app.services.ai.errors import AIError
    return AIError(code=code)


@pytest.fixture
def ai(monkeypatch):
    from app.services.ai.wrapper import padyar_ai

    def install(*replies):
        fake = FakeAI(*replies)
        monkeypatch.setattr(padyar_ai, "generate", fake)
        monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: True)
        return fake
    return install


def _five_chunks():
    return [_heading(f"بخش {i}") if i % 2 == 0 else _para(_sentences(5, f"متن{i}"))
            for i in range(10)]


# ── REQ-030: chunking ────────────────────────────────────────────────────

def test_paragraphs_of_one_section_join_until_the_900_character_limit():
    from app.services import ingest
    first, second, third = "الف" * 100, "ب" * 300, "پ" * 600
    chunks = ingest.chunk(Extracted("docx", [_heading("سرفصل"), _para(first), _para(second), _para(third)]))
    assert [c.text for c in chunks] == [first + "\n\n" + second, third]
    assert {c.heading for c in chunks} == {"سرفصل"}


def test_a_long_paragraph_is_cut_at_the_last_sentence_end_before_900():
    from app.services import ingest
    text = _sentences(20)
    chunks = ingest.chunk(Extracted("docx", [_heading("سرفصل"), _para(text)]))
    assert len(chunks) == 3
    assert all(len(c.text) <= 900 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)
    assert " ".join(c.text for c in chunks) == text


def test_a_paragraph_without_a_sentence_end_is_cut_at_the_last_space():
    from app.services import ingest
    words = " ".join(["کلمه"] * 300)
    chunks = ingest.chunk(Extracted("docx", [_para(words)]))
    assert len(chunks[0].text) <= 900
    assert not chunks[0].text.endswith(" ")
    assert " ".join(c.text for c in chunks) == words


def test_a_short_chunk_joins_its_neighbour_in_the_section_or_stays_alone():
    from app.services import ingest
    long_one = "ا" * 894 + "."
    tail = "پایان کوتاه."
    joined = ingest.chunk(Extracted("docx", [_para(long_one), _para(tail)]))
    assert [c.text for c in joined] == [long_one + "\n\n" + tail]

    head = ingest.chunk(Extracted("docx", [_heading("الف"), _para(tail), _para(long_one)]))
    assert [c.text for c in head] == [tail + "\n\n" + long_one]

    alone = ingest.chunk(Extracted("docx", [_heading("الف"), _para(tail), _heading("ب"), _para(long_one)]))
    assert [c.text for c in alone] == [tail, long_one]


def test_each_row_of_a_sheet_is_one_chunk_and_rows_never_merge():
    from app.services import ingest
    rows = [Block("row", "کوتاه", 0, {"title": "پرسش یک", "text": "کوتاه"}),
            Block("row", "هم کوتاه", 0, {"title": "", "text": "هم کوتاه"})]
    chunks = ingest.chunk(Extracted("csv", rows))
    assert [(c.heading, c.text) for c in chunks] == [("پرسش یک", "کوتاه"), ("", "هم کوتاه")]


def test_a_heading_opens_a_new_section():
    from app.services import ingest
    a, b = "الف" * 100, "ب" * 100
    chunks = ingest.chunk(Extracted("docx", [_heading("یک"), _para(a), _heading("دو"), _para(b)]))
    assert [(c.heading, c.text) for c in chunks] == [("یک", a), ("دو", b)]


async def test_more_than_300_chunks_fail_the_job_with_its_sentence(db, extract_as):
    blocks = []
    for i in range(301):
        blocks += [_heading(f"سرفصل {i}"), _para("متن " * 20)]
    job_id = await _run(blocks, extract_as)
    job = _job(job_id)
    assert (job["status"], job["error_code"]) == ("failed", "too_many_chunks")
    assert _proposals(job_id) == []
    from app.services import ingest
    shown = ingest.job_detail(job_id)["job"]
    assert shown["error_message"] == "این فایل بیش از ۳۰۰ بخش دارد. آن را به چند فایل تقسیم کنید."


# ── REQ-031: one proposal per chunk ──────────────────────────────────────

async def test_a_proposal_is_the_chunk_word_for_word(db, extract_as):
    text = f"ساعت کاری نمایشگاه{ZWNJ}ها از ۹ صبح تا ۱۸ است و ورود رایگان است."
    job_id = await _run([_heading("ساعت کاری"), _para(text)], extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["source_text"] == proposal["text"] == text
    assert ZWNJ in proposal["text"]
    assert (proposal["title"], proposal["title_source"], proposal["heading"]) == (
        "ساعت کاری", "heading", "ساعت کاری")
    assert (proposal["status"], proposal["questions"], proposal["synonyms"]) == ("pending", [], [])
    assert proposal["seq"] == 1


async def test_chunks_of_one_section_get_numbered_titles_in_persian_digits(db, extract_as):
    job_id = await _run([_heading("قوانین غرفه"), _para(_sentences(20))], extract_as)
    titles = [p["title"] for p in _proposals(job_id)]
    assert titles == ["قوانین غرفه (بخش ۱ از ۳)", "قوانین غرفه (بخش ۲ از ۳)", "قوانین غرفه (بخش ۳ از ۳)"]


async def test_a_split_title_keeps_the_heading_digits_as_written(db, extract_as):
    job_id = await _run([_heading("Expo 2024 Plan"), _para(_sentences(12))], extract_as)
    assert [p["title"] for p in _proposals(job_id)] == ["Expo 2024 Plan (بخش ۱ از ۲)", "Expo 2024 Plan (بخش ۲ از ۲)"]
    job_id = await _run([_heading("Expo 2024 Plan"), _para("متن کوتاه این بخش است.")], extract_as)
    assert [p["title"] for p in _proposals(job_id)] == ["Expo 2024 Plan"]

async def test_a_chunk_without_a_heading_is_titled_by_its_first_sentence(db, extract_as):
    first = "این جمله اول است و عنوان پیشنهاد از همین جمله ساخته می‌شود تا مدیر بداند درباره چیست و بیشتر"
    job_id = await _run([_para(first + ". جمله دوم.")], extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["title_source"] == "local"
    assert len(proposal["title"]) < 80
    assert first.startswith(proposal["title"])
    assert not proposal["title"].endswith(" ")


@pytest.mark.parametrize("text,title", [
    ("قیمت بلیت 2.5 میلیون تومان است. جمله دوم این بخش.", "قیمت بلیت 2.5 میلیون تومان است"),
    ("نشانی سایت www.example.com است و ثبت‌نام همان‌جاست. جمله دوم.",
     "نشانی سایت www.example.com است و ثبت‌نام همان‌جاست"),
    ("ساعت کاری نمایشگاه چیست؟ هر روز از نه صبح باز است.", "ساعت کاری نمایشگاه چیست"),
    ("ورود به سالن اصلی رایگان است. بلیت لازم نیست.", "ورود به سالن اصلی رایگان است"),
    ("این بخش فقط یک جمله دارد و نقطهٔ آن آخر متن است.", "این بخش فقط یک جمله دارد و نقطهٔ آن آخر متن است"),
])
async def test_a_local_title_ends_at_a_real_sentence_end_only(db, extract_as, text, title):
    job_id = await _run([_para(text)], extract_as)
    [proposal] = _proposals(job_id)
    assert (proposal["title"], proposal["title_source"]) == (title, "local")


# ── REQ-032, REQ-033: duplicate labels ───────────────────────────────────

def _live_entry(item_id, title, text):
    _exec("INSERT INTO dataset (id, title, text, position) VALUES (?, ?, ?, 10)", (item_id, title, text))


async def test_text_equal_to_a_live_entry_is_a_duplicate_before_and_after_normalizing(db, extract_as):
    _live_entry("d-1", "ورود", "ورود به نمایشگاه رایگان است.")
    _live_entry("d-2", "ساعت", "نمايشگاه ساعت ۹ باز مي‌شود.")
    job_id = await _run([_heading("یک"), _para("ورود به نمایشگاه رایگان است."),
                         _heading("دو"), _para("نمایشگاه ساعت ۹ باز می‌شود."),
                         _heading("سه"), _para("این متن تازه است و در دانش نیست.")], extract_as)
    labels = [(p["similar_kind"], p["similar_to"]) for p in _proposals(job_id)]
    assert labels == [("duplicate", "d-1"), ("duplicate", "d-2"), ("", "")]


async def test_a_later_chunk_repeating_an_earlier_one_points_at_the_earlier_proposal(db, extract_as):
    job_id = await _run([_heading("یک"), _para("متن تکراری در این سند است."),
                         _heading("دو"), _para("متن تکراری در این سند است.")], extract_as)
    first, second = _proposals(job_id)
    assert first["similar_kind"] == ""
    assert (second["similar_kind"], second["similar_to"]) == ("duplicate", first["id"])


async def test_an_equal_title_is_only_a_soft_note(db, extract_as):
    _live_entry("d-9", "تماس با ما", "شماره تلفن دفتر مرکزی.")
    job_id = await _run([_heading("تماس با ما"), _para("نشانی ایمیل پشتیبانی نمایشگاه.")], extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["same_title_as"] == "d-9"
    assert proposal["similar_kind"] == ""


class _FakeModel:
    """Maps a few known texts to fixed unit vectors; anything else is orthogonal."""

    def __init__(self, table):
        self.table = table

    def encode(self, texts):
        out = []
        for text in texts:
            out.append(self.table.get(text, [0.0, 0.0, 0.0, 1.0]))
        return np.asarray(out, dtype=np.float32)


@pytest.fixture
def fake_embeddings(monkeypatch):
    from app.services import embeddings

    def install(table):
        model = _FakeModel(table)
        monkeypatch.setattr(embeddings, "available", lambda: True)
        monkeypatch.setattr(embeddings, "_get_model", lambda name: model)
        return model
    return install


async def test_a_near_copy_uses_the_raw_cosine_and_a_company_wins(db, extract_as, fake_embeddings):
    _live_entry("d-1", "خدمات", "متن زنده درباره خدمات")
    _exec("INSERT INTO companies (id, title, text) VALUES ('c-1', 'شرکت آلفا', 'متن شرکت آلفا')")
    _exec("INSERT INTO companies (id, title, text) VALUES ('c-2', 'شرکت خالی', '')")
    fake_embeddings({
        "متن زنده درباره خدمات": [1.0, 0.0, 0.0, 0.0],
        "متن شرکت آلفا": [0.8, 0.6, 0.0, 0.0],
        "کپی نزدیک خدمات": [0.99, -0.141, 0.0, 0.0],
        "کپی نزدیک هر دو": [0.95, 0.312, 0.0, 0.0],
        "متن دور": [0.0, 0.0, 1.0, 0.0],
    })
    job_id = await _run([_heading("یک"), _para("کپی نزدیک خدمات"), _heading("دو"), _para("کپی نزدیک هر دو"),
                         _heading("سه"), _para("متن دور")], extract_as)
    labels = [(p["similar_kind"], p["similar_to"]) for p in _proposals(job_id)]
    assert labels == [("dataset", "d-1"), ("company", "c-1"), ("", "")]


async def test_a_duplicate_outranks_a_company_label(db, extract_as, fake_embeddings):
    _live_entry("d-1", "خدمات", "متن تکراری")
    _exec("INSERT INTO companies (id, title, text) VALUES ('c-1', 'شرکت آلفا', 'متن شرکت')")
    fake_embeddings({"متن تکراری": [0.0, 1.0], "متن شرکت": [0.0, 1.0]})
    job_id = await _run([_para("متن تکراری")], extract_as)
    [proposal] = _proposals(job_id)
    assert (proposal["similar_kind"], proposal["similar_to"]) == ("duplicate", "d-1")


async def test_the_near_copy_layer_is_skipped_without_embeddings(db, extract_as, monkeypatch):
    from app.services import embeddings

    def must_not_load(name):
        raise AssertionError("the embedding model must not load when embeddings are unavailable")
    monkeypatch.setattr(embeddings, "_get_model", must_not_load)
    _live_entry("d-1", "خدمات", "متن زنده")
    job_id = await _run([_para("متن تازهٔ پیشنهاد")], extract_as)
    assert _job(job_id)["status"] == "ready"
    assert _proposals(job_id)[0]["similar_kind"] == ""


def test_search_topk_raw_returns_the_cosine_that_search_topk_calibrates(monkeypatch):
    from app.services import embeddings
    model = _FakeModel({"a": [1.0, 0.0], "b": [0.6, 0.8], "q": [0.96, 0.28]})
    monkeypatch.setattr(embeddings, "_get_model", lambda name: model)
    index = embeddings.EmbeddingIndex(["a", "b"])
    raw = index.search_topk_raw("q", k=2)
    assert [i for i, _ in raw] == [0, 1]
    assert raw[0][1] == pytest.approx(0.96, abs=1e-5)
    assert raw[1][1] == pytest.approx(0.8, abs=1e-5)
    assert index.search_topk("q", k=1)[0][1] == 1.0
    assert index.search_topk_raw("q") == raw[:1]


# ── REQ-028, REQ-029, REQ-035: the job ───────────────────────────────────

async def test_with_the_ai_off_a_job_ends_ready_with_local_proposals_and_no_call(db, extract_as, monkeypatch):
    from app.services.ai.wrapper import padyar_ai
    fake = FakeAI()
    monkeypatch.setattr(padyar_ai, "generate", fake)
    job_id = await _run([_heading("سرفصل"), _para("متن یک پاسخ در این سند.")], extract_as)
    assert _job(job_id)["status"] == "ready"
    assert [(p["ai_state"], p["title"]) for p in _proposals(job_id)] == [("local", "سرفصل")]
    assert fake.calls == []


async def test_a_file_the_reader_refuses_fails_the_job_with_its_code(db, extract_as):
    from app.services import ingest
    from app.services.ingest_extract import IngestRejected, MESSAGES
    extract_as(IngestRejected("encrypted", MESSAGES["encrypted"]))
    job, _ = ingest.create_job("file", "secret.docx", b"PK\x03\x04 bytes", "docx", "admin")
    tmp_path = _job(job["id"])["tmp_path"]
    await ingest.run_job(job["id"])
    row = _job(job["id"])
    assert (row["status"], row["error_code"]) == ("failed", "encrypted")
    assert row["finished_at"]
    assert _proposals(job["id"]) == []
    assert not os.path.exists(tmp_path)
    assert ingest.job_detail(job["id"])["job"]["error_message"] == MESSAGES["encrypted"]


async def test_upload_bytes_wait_in_a_private_temp_file_until_extraction(db, extract_as):
    from app.services import ingest
    extract_as(Extracted("docx", [_para("متن کوتاه برای آزمون.")]))
    job, _ = ingest.create_job("file", "a.docx", b"secret bytes", "docx", "admin")
    path = _job(job["id"])["tmp_path"]
    assert open(path, "rb").read() == b"secret bytes"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    await ingest.run_job(job["id"])
    assert not os.path.exists(path)
    assert _job(job["id"])["tmp_path"] == ""


async def test_extraction_is_told_the_detected_format_not_the_display_name(db, extract_as):
    from app.services import ingest
    seen = extract_as(Extracted("docx", [_para("متن کوتاه برای آزمون.")]))
    job, _ = ingest.create_job("file", "C" * 250 + ".docx", b"abc", "docx", "admin")
    assert len(job["source_name"]) == 200
    await ingest.run_job(job["id"])
    assert seen == ["upload.docx"]


def test_the_display_name_is_the_base_name_only(db):
    from app.services import ingest
    job, _ = ingest.create_job("file", "../../etc/passwd.txt", b"abc", "txt", "admin")
    assert job["source_name"] == "passwd.txt"


@pytest.mark.parametrize("url", ["https://[2606:4700:4700::1111]/about",
                                 "https://[2606:4700:4700::1111]:443/about?x=1"])
def test_an_ipv6_page_address_keeps_its_brackets(url):
    from app.services import ingest
    assert ingest.display_url(url) == url


async def test_a_web_page_is_read_with_the_charset_its_server_declared(db):
    from app.services import ingest
    page = "<html><body><h1>درباره ما</h1><p>اين صفحه درباره نمايشگاه و خدمات آن است.</p></body></html>"
    job, _ = ingest.create_job("url", "https://example.com/", page.encode("cp1256"), "html", "admin")
    await ingest.run_job(job["id"], charset="windows-1256")
    [proposal] = _proposals(job["id"])
    assert proposal["title"] == "درباره ما"
    assert proposal["text"] == "اين صفحه درباره نمايشگاه و خدمات آن است."


async def test_a_cancel_during_extraction_leaves_no_proposal(db, monkeypatch):
    from app.services import ingest, ingest_extract
    job, _ = ingest.create_job("file", "a.docx", b"abc", "docx", "admin")

    def cancel_then_read(data, filename, timeout_s=60.0):
        ingest.cancel_job(job["id"], "admin")
        return Extracted("docx", [_para("متن کوتاه برای آزمون.")])
    monkeypatch.setattr(ingest_extract, "extract_file_isolated", cancel_then_read)
    await ingest.run_job(job["id"])
    assert _job(job["id"])["status"] == "cancelled"
    assert _proposals(job["id"]) == []


async def test_an_unexpected_error_fails_the_job_as_interrupted(db, extract_as):
    from app.services import ingest
    extract_as(RuntimeError("broken install"))
    job, _ = ingest.create_job("file", "a.docx", b"abc", "docx", "admin")
    await ingest.run_job(job["id"])
    assert (_job(job["id"])["status"], _job(job["id"])["error_code"]) == ("failed", "interrupted")


def test_the_same_bytes_while_a_job_is_active_return_that_job(db):
    from app.services import ingest
    first, existing = ingest.create_job("file", "a.txt", b"same", "txt", "admin")
    again, existing_again = ingest.create_job("file", "b.txt", b"same", "txt", "other")
    assert (existing, existing_again) == (False, True)
    assert again["id"] == first["id"]
    assert len(_rows("SELECT id FROM ingest_jobs")) == 1
    assert os.listdir(db) == [os.path.basename(_job(first["id"])["tmp_path"])]


def test_the_temp_file_goes_when_the_database_cannot_be_reached(db, monkeypatch):
    from app.services import ingest

    def unreachable():
        raise RuntimeError("pool timeout")
    monkeypatch.setattr(ingest, "get_db_connection", unreachable)
    with pytest.raises(RuntimeError):
        ingest.create_job("file", "doc.txt", b"private bytes", "txt", "admin")
    assert os.listdir(db) == []

def test_the_same_bytes_after_a_job_finished_start_a_new_job(db):
    from app.services import ingest
    first, _ = ingest.create_job("file", "a.txt", b"same", "txt", "admin")
    _exec("UPDATE ingest_jobs SET status = 'done' WHERE id = ?", (first["id"],))
    second, existing = ingest.create_job("file", "a.txt", b"same", "txt", "admin")
    assert existing is False and second["id"] != first["id"]


# ── REQ-042..REQ-051: the AI step ────────────────────────────────────────

async def test_an_open_circuit_on_the_third_call_stops_the_model_stage(db, extract_as, ai):
    fake = ai(_reply(questions=["ساعت کاری چیست؟"]), _reply(), _ai_error("all_routes_failed"))
    job_id = await _run(_five_chunks(), extract_as)
    job = _job(job_id)
    assert len(fake.calls) == 3
    assert (job["status"], job["ai_stopped_at"]) == ("ready", 3)
    assert [p["ai_state"] for p in _proposals(job_id)] == ["done", "done", "local", "local", "local"]


async def test_provider_unavailable_stops_at_the_first_call(db, extract_as, ai):
    fake = ai(_ai_error("provider_unavailable"))
    job_id = await _run(_five_chunks(), extract_as)
    assert len(fake.calls) == 1
    assert _job(job_id)["ai_stopped_at"] == 1
    assert {p["ai_state"] for p in _proposals(job_id)} == {"local"}


async def test_three_other_errors_in_a_row_stop_and_a_success_resets_the_count(db, extract_as, ai):
    fake = ai(_ai_error("timeout"), _ai_error("rate_limited"), _reply(),
              _ai_error("timeout"), _ai_error("server_error"))
    job_id = await _run(_five_chunks(), extract_as)
    assert len(fake.calls) == 5
    assert _job(job_id)["ai_stopped_at"] is None
    assert [p["ai_state"] for p in _proposals(job_id)] == ["failed", "failed", "done", "failed", "failed"]

    fake = ai(_ai_error("timeout"), _ai_error("timeout"), _ai_error("timeout"))
    job_id = await _run(_five_chunks(), extract_as)
    assert len(fake.calls) == 3
    assert _job(job_id)["ai_stopped_at"] == 3
    assert [p["ai_state"] for p in _proposals(job_id)] == ["failed", "failed", "local", "local", "local"]


async def test_every_call_is_a_paced_chat_call_with_explicit_limits(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    from app.services.ai.request import AIMessage
    sleeps = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)
    monkeypatch.setattr(ingest, "PACE_SECONDS", 1.0)
    monkeypatch.setattr(ingest.asyncio, "sleep", recording_sleep)
    fake = ai()
    job_id = await _run(_five_chunks(), extract_as)
    assert len(fake.calls) == 5
    for call, proposal in zip(fake.calls, _proposals(job_id)):
        assert call["task"] == "chat"
        assert call["max_output_tokens"] == 1000
        assert call["response_format"] == "json_object"
        assert call["temperature"] == 0.2
        assert call["timeout_s"] == 45.0
        assert call["system_prompt"] == ingest.SYSTEM_PROMPT
        [message] = call["messages"]
        assert isinstance(message, AIMessage)
        assert (message.role, message.content) == ("user", proposal["source_text"])
    assert len([s for s in sleeps if s >= 1.0]) >= 4


def test_the_system_prompt_is_the_fixed_persian_text_that_asks_for_json():
    from app.services import ingest
    assert "JSON" in ingest.SYSTEM_PROMPT
    assert ingest.SYSTEM_PROMPT == (
        "تو به مدیر یک چت‌بات کمک می‌کنی. پیام کاربر یک تکه از یک سند است.\n"
        "فقط یک شیء JSON با همین شکل برگردان و هیچ متن دیگری ننویس:\n"
        '{"title": "", "questions": ["..."], "synonyms": [{"word": "...", "suggestion": "..."}]}\n'
        "title را فقط وقتی پر کن که تکه عنوان ندارد، کوتاه و فقط با کلمه‌های خود تکه.\n"
        "questions: حداکثر ۵ پرسش کوتاه فارسی که یک بازدیدکننده می‌پرسد و پاسخش در همین تکه است.\n"
        "synonyms: حداکثر ۵ جفت؛ word یک کلمه از تکه و suggestion کلمه‌ای هم‌معنی که مردم به‌جای آن می‌نویسند.\n"
        "هیچ واقعیت، عدد یا نامی که در تکه نیست ننویس."
    )


async def test_the_model_never_writes_the_text_or_a_number_the_chunk_lacks(db, extract_as, ai):
    text = "بلیت ورود ۵۰ هزار تومان است و کودکان رایگان وارد می‌شوند."
    ai(_reply(title="عنوان مدل", text="متن ساختگی مدل",
              questions=["قیمت بلیت ۵۰ هزار تومان است؟", "قیمت بلیت ۸۰ هزار تومان است؟",
                         "کودکان رایگان وارد می‌شوند؟"]))
    job_id = await _run([_heading("بلیت"), _para(text)], extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["text"] == proposal["source_text"] == text
    assert (proposal["title"], proposal["title_source"]) == ("بلیت", "heading")
    assert proposal["questions"] == ["قیمت بلیت ۵۰ هزار تومان است؟", "کودکان رایگان وارد می‌شوند؟"]
    assert proposal["ai_state"] == "done"


async def test_a_model_title_is_used_only_when_its_words_come_from_the_chunk(db, extract_as, ai):
    text = "ورود کودکان زیر ده سال به سالن اصلی رایگان است و نیازی به بلیت نیست."
    cases = [("ورود رایگان کودکان", "model"), ("ورود کودکان ۱۰ ساله", "local"),
             ("ورود رایگان کودکان www.x.ir", "local"), ("پارکینگ رایگان", "local"),
             ("ک" * 101, "local"), ("", "local")]
    for title, source in cases:
        ai(_reply(title=title))
        job_id = await _run([_para(text)], extract_as)
        [proposal] = _proposals(job_id)
        assert proposal["title_source"] == source, title
        if source == "model":
            assert proposal["title"] == title


@pytest.mark.parametrize("title", ["؟!", "و از به", "  "])
async def test_a_model_title_without_a_content_word_is_not_used(db, extract_as, ai, title):
    ai(_reply(title=title))
    job_id = await _run([_para("ورود کودکان زیر ده سال به سالن اصلی رایگان است و نیازی به بلیت نیست.")],
                        extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["title_source"] == "local"

async def test_a_truncated_or_broken_answer_keeps_the_local_proposal(db, extract_as, ai):
    ai(_reply(questions=["ساعت کاری چیست؟"], finish="length"),
       _reply(content="{not json"),
       _reply(content='["a list, not an object"]'),
       _reply(questions=["ساعت کاری نمایشگاه چیست؟"]),
       _reply(content="null"))
    job_id = await _run(_five_chunks(), extract_as)
    proposals = _proposals(job_id)
    assert [p["ai_state"] for p in proposals] == ["failed", "failed", "failed", "done", "failed"]
    assert [p["questions"] for p in proposals] == [[], [], [], ["ساعت کاری نمایشگاه چیست؟"], []]


async def test_questions_and_synonyms_are_checked_one_by_one_and_capped_at_five(db, extract_as, ai):
    _exec("INSERT INTO questions (question, dataset_id) VALUES ('ساعت کاری چیست؟', 'x')")
    good = [f"پرسش شماره {n} درباره این متن؟" for n in "یک دو سه چهار پنج شش".split()]
    ai(_reply(questions=["ساعت کاري چیست؟", "What time?", "کوتا", good[0], good[0], 42, *good[1:]],
              synonyms=[("غرفه", "غرفه"), ("غرفه", "استند"), ("", "x"), ("نمایشگاه", "اکسپو"),
                        ("الف", "ب"), ("پ", "ت"), ("ث", "ج"), ("چ", "ح"), ("خ", "د")]))
    job_id = await _run([_para("متن این بخش درباره غرفه و نمایشگاه است.")], extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["questions"] == good[:5]
    assert proposal["synonyms"] == [{"word": "غرفه", "suggestion": "استند"},
                                    {"word": "نمایشگاه", "suggestion": "اکسپو"},
                                    {"word": "الف", "suggestion": "ب"},
                                    {"word": "پ", "suggestion": "ت"},
                                    {"word": "ث", "suggestion": "ج"}]


async def test_a_question_number_must_be_a_whole_number_of_the_chunk(db, extract_as, ai):
    ai(_reply(questions=["سالن ۳ کجاست؟", "آیا نمایشگاه ۵ روز است؟", "قیمت بلیت ۲۳۴ تومان است؟",
                         "نمایشگاه ۱۵ روز طول می‌کشد؟", "تلفن غرفه ۰۲۱-۱۲۳۴۵۶۷۸ است؟"]))
    job_id = await _run([_para("تلفن غرفه ۰۲۱۱۲۳۴۵۶۷۸ است و نمایشگاه ۱۵ روز طول می‌کشد.")], extract_as)
    [proposal] = _proposals(job_id)
    assert proposal["questions"] == ["نمایشگاه ۱۵ روز طول می‌کشد؟", "تلفن غرفه ۰۲۱-۱۲۳۴۵۶۷۸ است؟"]

    ai(_reply(questions=["سالن ۳ کجاست؟"]))
    job_id = await _run([_para("سالن ۳ در ضلع شمالی نمایشگاه است.")], extract_as)
    assert _proposals(job_id)[0]["questions"] == ["سالن ۳ کجاست؟"]

async def test_each_call_is_logged_without_the_document_text(db, extract_as, ai, monkeypatch):
    from app.services import applog
    logged = []
    monkeypatch.setattr(applog, "info", lambda category, event, message="", **f: logged.append(
        (category, event, message, f)))
    secret = "رمز محرمانهٔ سند که نباید در لاگ بیاید."
    ai(_reply(), _ai_error("timeout"))
    await _run([_heading("یک"), _para(secret), _heading("دو"), _para(secret + " دوم")], extract_as)
    calls = [entry for entry in logged if entry[1] == "ingest.ai_call"]
    assert len(calls) == 2
    for category, event, message, fields in calls:
        assert "finish_reason" in json.dumps(fields.get("metadata"))
        assert "duration_ms" in fields
        assert secret not in json.dumps([message, fields], ensure_ascii=False, default=str)
    assert calls[0][3]["tokens_out"] == 17


# ── REQ-056 against the model stage: the admin's edit wins ──────────────

async def test_an_edit_made_before_the_model_stage_survives_its_answer(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    job_id = await _extracted_job([_heading("بخش اول"), _para("متن بخش اول درباره ساعت کاری نمایشگاه.")],
                                  extract_as, monkeypatch)
    [proposal] = _proposals(job_id)
    ingest.edit_proposal(proposal["id"], {"title": "عنوان مدیر", "questions": ["پرسش نوشتهٔ مدیر چیست؟"],
                                          "synonyms": [{"word": "نمایشگاه", "suggestion": "اکسپو"}]}, "admin")
    fake = ai(_reply(title="عنوان مدل", questions=["پرسش ساختهٔ مدل چیست؟"], synonyms=[("ساعت", "زمان")]))
    await ingest.run_model_loop()
    [after] = _proposals(job_id)
    assert len(fake.calls) == 1
    assert (after["title"], after["title_source"], after["edited"]) == ("عنوان مدیر", "admin", 1)
    assert after["questions"] == ["پرسش نوشتهٔ مدیر چیست؟"]
    assert after["synonyms"] == [{"word": "نمایشگاه", "suggestion": "اکسپو"}]
    assert after["ai_state"] == "done"
    assert _job(job_id)["status"] == "ready"


async def test_an_edit_made_while_the_model_answers_another_chunk_survives(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    from app.services.ai.wrapper import padyar_ai
    job_id = await _extracted_job([_heading("بخش یک"), _para("متن بخش یک درباره بلیت ورود."),
                                   _heading("بخش دو"), _para("متن بخش دو درباره پارکینگ.")],
                                  extract_as, monkeypatch)
    first, second = _proposals(job_id)
    fake = ai(_reply(questions=["بلیت ورود چند است؟"]), _reply(questions=["پارکینگ کجاست؟"]))
    inner = fake.__call__

    async def editing_during_the_first_call(messages, **kwargs):
        if len(fake.calls) == 0:
            ingest.edit_proposal(second["id"], {"title": "پارکینگ (ویرایش مدیر)"}, "admin")
        return await inner(messages, **kwargs)
    monkeypatch.setattr(padyar_ai, "generate", editing_during_the_first_call)
    await ingest.run_model_loop()
    one, two = _proposals(job_id)
    assert one["questions"] == ["بلیت ورود چند است؟"]
    assert (two["title"], two["title_source"], two["edited"]) == ("پارکینگ (ویرایش مدیر)", "admin", 1)
    assert two["questions"] == []
    assert two["ai_state"] == "done"


# ── REQ-037, REQ-038: one job in the model stage ─────────────────────────

async def test_two_ready_jobs_never_share_the_model_stage(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    first = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    second = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    _later(second, 1)
    order = []
    fake = ai()
    inner = fake.__call__

    async def watching(messages, **kwargs):
        busy = _rows("SELECT id FROM ingest_jobs WHERE status IN ('proposing', 'cancelling')")
        assert len(busy) == 1
        order.append(busy[0]["id"])
        return await inner(messages, **kwargs)
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(padyar_ai, "generate", watching)
    await ingest.run_model_loop()
    assert order == [first, first, second, second]
    assert (_job(first)["status"], _job(second)["status"]) == ("ready", "ready")


async def test_two_model_loops_at_once_handle_every_chunk_exactly_once(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    jobs = [await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch) for _ in range(3)]
    fake = ai()
    inner = fake.__call__

    async def yielding(messages, **kwargs):
        await asyncio.sleep(0)
        return await inner(messages, **kwargs)
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(padyar_ai, "generate", yielding)
    await asyncio.gather(ingest.run_model_loop(), ingest.run_model_loop())
    assert len(fake.calls) == 6
    assert {_job(j)["status"] for j in jobs} == {"ready"}


def test_the_slot_index_refuses_a_second_job_in_the_model_stage(db):
    from app.db import dberrors
    from app.db.connection import get_db_connection
    for job_id, status in (("a", "proposing"), ("b", "extracted"), ("c", "extracted")):
        _exec("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size,"
              " status, created_by) VALUES (?, 'file', 'x', ?, 1, ?, 'admin')", (job_id, job_id, status))
    conn = get_db_connection()
    try:
        with pytest.raises(Exception) as caught:
            conn.execute("UPDATE ingest_jobs SET status = 'proposing' WHERE id = 'b' AND status = 'extracted'")
        assert dberrors.is_unique_violation(caught.value)
        conn.rollback()
        conn.execute("UPDATE ingest_jobs SET status = 'cancelling' WHERE id = 'a'")
        with pytest.raises(Exception):
            conn.execute("UPDATE ingest_jobs SET status = 'cancelling' WHERE id = 'c'")
        conn.rollback()
    finally:
        conn.close()


async def test_resume_waiting_schedules_the_loop_only_when_the_slot_is_free(db, extract_as, monkeypatch):
    from fastapi import BackgroundTasks
    from app.services import ingest
    job_id = await _extracted_job([_para("متن یک پاسخ کوتاه.")], extract_as, monkeypatch)
    tasks = BackgroundTasks()
    ingest.resume_waiting(tasks)
    assert [t.func for t in tasks.tasks] == [ingest.run_model_loop]

    _exec("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size,"
          " status, created_by) VALUES ('busy', 'file', 'x', 'h-busy', 1, 'cancelling', 'admin')")
    tasks = BackgroundTasks()
    ingest.resume_waiting(tasks)
    assert tasks.tasks == []

    _exec("DELETE FROM ingest_jobs WHERE id IN ('busy', ?)", (job_id,))
    tasks = BackgroundTasks()
    ingest.resume_waiting(tasks)
    assert tasks.tasks == []


async def test_a_job_that_waits_while_the_ai_is_turned_off_ends_local(db, extract_as, monkeypatch):
    from app.services import ingest
    job_id = await _extracted_job([_para("متن یک پاسخ کوتاه.")], extract_as, monkeypatch)
    await ingest.run_model_loop()
    assert _job(job_id)["status"] == "ready"
    assert _proposals(job_id)[0]["ai_state"] == "local"


# ── REQ-039: cancel while the model stage runs (SC-029) ──────────────────

async def test_a_cancel_mid_call_holds_the_slot_until_the_call_returns(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    from app.services.ai.wrapper import padyar_ai
    first = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    second = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    _later(second, 1)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow(messages, **kwargs):
        calls.append(messages[0].content)
        started.set()
        await release.wait()
        return _reply(questions=["پرسشی که باید دور ریخته شود؟"])
    monkeypatch.setattr(padyar_ai, "generate", slow)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: True)

    loop = asyncio.create_task(ingest.run_model_loop())
    await started.wait()
    ingest.cancel_job(first, "admin")
    assert _job(first)["status"] == "cancelling"
    assert {(p["status"], p["reject_reason"]) for p in _proposals(first)} == {("rejected", "cancelled")}

    await ingest.run_model_loop()
    assert _job(second)["status"] == "extracted"
    assert len(calls) == 1

    release.set()
    await loop
    assert _job(first)["status"] == "cancelled"
    assert _job(first)["finished_at"]
    assert all(p["questions"] == [] for p in _proposals(first))
    assert _job(second)["status"] == "ready"
    assert len(calls) == 3


async def test_a_cancel_during_the_pacing_gap_sends_no_further_chunk(db, extract_as, ai, monkeypatch):
    from app.services import ingest
    job_id = await _extracted_job(_five_chunks()[:6], extract_as, monkeypatch)
    real_sleep = asyncio.sleep
    sleeps = []

    async def cancelling_sleep(seconds):
        sleeps.append(seconds)
        if seconds >= 1.0 and len([s for s in sleeps if s >= 1.0]) == 1:
            ingest.cancel_job(job_id, "admin")
        await real_sleep(0)
    monkeypatch.setattr(ingest, "PACE_SECONDS", 1.0)
    monkeypatch.setattr(ingest.asyncio, "sleep", cancelling_sleep)
    fake = ai()
    await ingest.run_model_loop()
    assert len(fake.calls) == 1
    assert _job(job_id)["status"] == "cancelled"


@pytest.mark.parametrize("last_reply", ["answer", "open_circuit"])
async def test_a_cancel_after_the_last_status_read_still_frees_the_slot(db, extract_as, ai, monkeypatch, last_reply):
    from app.services import ingest
    first = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    second = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    _later(second, 1)
    real_status = ingest._status
    armed = {"on": False}

    def status_then_cancel(job_id):
        state = real_status(job_id)
        if armed["on"] and job_id == first:
            armed["on"] = False
            ingest.cancel_job(first, "admin")
        return state
    monkeypatch.setattr(ingest, "_status", status_then_cancel)
    last = _reply() if last_reply == "answer" else _ai_error("all_routes_failed")
    fake = ai(_reply(), last)
    inner = fake.__call__

    async def arming(messages, **kwargs):
        if len(fake.calls) == 1:
            armed["on"] = True
        return await inner(messages, **kwargs)
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(padyar_ai, "generate", arming)
    await ingest.run_model_loop()
    assert _job(first)["status"] == "cancelled"
    assert _job(second)["status"] == "ready"

# ── REQ-036: recovery ────────────────────────────────────────────────────

async def test_a_proposing_job_with_a_stale_heartbeat_is_failed_as_interrupted(db, extract_as, monkeypatch):
    from app.services import ingest
    job_id = await _extracted_job(_five_chunks()[:4], extract_as, monkeypatch)
    _exec("UPDATE ingest_jobs SET status = 'proposing', heartbeat_at = datetime('now', '-6 minutes')"
          " WHERE id = ?", (job_id,))
    assert ingest.recover_stale() == 1
    job = _job(job_id)
    assert (job["status"], job["error_code"]) == ("failed", "interrupted")
    assert {p["ai_state"] for p in _proposals(job_id)} == {"local"}
    assert {p["status"] for p in _proposals(job_id)} == {"pending"}


def test_recovery_cancels_a_stale_cancel_and_fails_a_stale_queue_only(db):
    from app.services import ingest
    rows = [("cancel-old", "cancelling", "datetime('now', '-6 minutes')", "datetime('now')"),
            ("queued-old", "queued", "NULL", "datetime('now', '-6 minutes')"),
            ("busy-fresh", "extracting", "datetime('now', '-1 minutes')", "datetime('now')"),
            ("extract-old", "extracting", "datetime('now', '-7 minutes')", "datetime('now')")]
    for job_id, status, beat, created in rows:
        _exec("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size, status,"
              f" created_by, heartbeat_at, created_at) VALUES (?, 'file', 'x', ?, 1, ?, 'admin', {beat}, {created})",
              (job_id, job_id, status))
    assert ingest.recover_stale() == 3
    status = {r["id"]: (r["status"], r["error_code"]) for r in _rows("SELECT * FROM ingest_jobs")}
    assert status == {"cancel-old": ("cancelled", ""), "queued-old": ("failed", "interrupted"),
                      "busy-fresh": ("extracting", ""), "extract-old": ("failed", "interrupted")}
    assert ingest.recover_stale() == 0


def test_recovery_reads_an_aware_iso_heartbeat_too(db):
    import datetime
    from app.services import ingest
    beat = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=6)).isoformat()
    _exec("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size, status,"
          " created_by, heartbeat_at) VALUES ('j', 'file', 'x', 'h', 1, 'proposing', 'admin', ?)", (beat,))
    assert ingest.recover_stale() == 1


# ── REQ-041: retention ───────────────────────────────────────────────────

def test_purge_removes_old_finished_jobs_but_never_a_pending_proposal_or_a_live_row(db):
    from app.services import ingest
    for job_id, status, age, pending in (("old-done", "done", 31, False), ("old-pending", "failed", 31, True),
                                         ("young", "cancelled", 29, False), ("old-ready", "ready", 40, False)):
        _exec("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size, status,"
              f" created_by, finished_at) VALUES (?, 'file', 'x', ?, 1, ?, 'admin', datetime('now', '-{age} days'))",
              (job_id, job_id, status))
        _exec("INSERT INTO ingest_proposals (id, job_id, seq, source_text, title, text, title_source,"
              " ai_state, status, dataset_id) VALUES (?, ?, 1, 's', 't', 's', 'local', 'local', ?, ?)",
              (f"p-{job_id}", job_id, "pending" if pending else "approved", f"ing-{job_id}"))
    _live_entry("ing-old-done", "t", "s")
    assert ingest.purge_expired() == 1
    assert sorted(r["id"] for r in _rows("SELECT id FROM ingest_jobs")) == ["old-pending", "old-ready", "young"]
    assert sorted(r["job_id"] for r in _rows("SELECT job_id FROM ingest_proposals")) == [
        "old-pending", "old-ready", "young"]
    assert _rows("SELECT id FROM dataset WHERE id = 'ing-old-done'")
