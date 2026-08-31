"""Fill a company's EMPTY profile fields from its own intro text, via the model.

WHY THIS EXISTS
---------------
Companies arrive half-empty from both live sources: a workbook whose cells
were blank (scripts/import-content.py) and a visitor's booth proposal that an
admin approved (app/services/leads.py:propose_company writes the text but
nothing else). A company with no facet value is INVISIBLE in every
field-filtered chat list («شرکت‌های فعال در زمینهٔ …»), because
app/services/company_search.py builds the facet vocabulary from these values
— measured on the elecomp install on 2026-08-31, 28 of 670 rows were dark
this way, including the one sponsor the organizer had boosted and could not
understand why nothing happened.

The organizer is not a typist. This module is one button on the companies
page: for every company that HAS an intro text but EMPTY fields, ask the
model ONCE for everything the text mentions — the contact person and their
title, phones, email, website, address, province, booth, hall, the
classification fields — plus the three English fields the model must
translate rather than extract (title_en, text_en, address_en). Validate each
value's shape, and write it into the empty column only. What the text does
not mention comes back empty and stays empty: absence is not an error, it is
the honest answer, and the organizer fills that hole by hand.

THE CONTRACT THE MODEL CANNOT BREAK
----------------------------------
The model SUGGESTS; the code decides. Every value passes a per-field
validator (emails must look like emails, phones like phones, activity labels
the exact shape the facet reader enforces), and every UPDATE carries
`AND COALESCE(field,'')=''` per written column so nothing is ever written
over organizer data and the whole run is re-runnable. A company the model
fails on lands in the report, not in the table. A field the organizer fills
mid-run is dropped from that company's write — their value wins and the
OTHER fields still land, so one manual edit never blocks the rest of the row
(and the run still terminates: every successful write fills at least one
empty column, and a company leaves the backlog the moment none are left).
"""
import json

from app.config import logger

# Same numbers the facet reader enforces (company_search._FACET_MAX_*). They
# are repeated here, not imported, so a future edit to the facet limits fails
# THIS module's tests too, not just silently wider labels.
MAX_LABEL_TOKENS = 8
MAX_LABEL_CHARS = 70
MAX_LABELS_PER_COMPANY = 3
# One POST per click processes at most this many companies. It used to be 25
# when the answer was three short labels; one answer now carries every field
# including a full English translation of the intro, so the batch shrinks to
# keep one POST well under the nginx proxy timeout while the UI loops.
BATCH_LIMIT = 10
# The vocabulary block of the prompt is capped so a 600-label install does
# not turn every call into a token-metered dump of its own taxonomy.
VOCABULARY_LIMIT = 120

# Free-text extraction targets: (column -> char cap). Length is the ONLY
# shape check here on purpose: the intro text either names a person, an
# address, a hall or it does not, and a plausible-but-possibly-imperfect
# string in an empty column is something the organizer can correct in the
# same modal — an empty column is something nobody can use at all. Over-cap
# values are REJECTED whole, never truncated: a silently shortened address
# is wrong data wearing a correct-looking shape.
_FREE_TEXT_FIELDS = {
    "contact_name": 80, "contact_position": 80,
    "address": 200, "province": 50,
    "booth_number": 30, "hall": 60,
    "company_type": 40, "org_stage": 40, "participation": 40,
}

# Machine-shaped fields: a wrong shape here is not "plausible", it is
# garbage — an email without @ cannot be mailed, a phone made of letters
# cannot be dialed — so each gets its own validator instead of a length cap.
_PHONE_FIELDS = ("contact_mobile", "company_phone", "fax")
_PHONE_CHARS = 30
_EMAIL_CHARS = 120
_WEBSITE_CHARS = 200

# The three English fields cannot be extracted from a Persian text — the
# model translates/transliterates them (آرمان تجارت مهرکالا → Arman Tejarat
# Mehrkala). Length-capped like every field the organizer may later edit by
# hand; text_en's cap is generous because it holds a full translation.
_EN_FIELDS = {"title_en": 120, "text_en": 1500, "address_en": 200}

# Every column this module may write, in the JSON order the prompt asks for.
EXTRACT_FIELDS = (tuple(_FREE_TEXT_FIELDS) + _PHONE_FIELDS
                  + ("email", "website") + tuple(_EN_FIELDS)
                  + ("activity_field",))

# Persian and Arabic-Indian digits both normalize to Latin before a phone
# shape is judged, so «۰۹۱۲…» written by the model passes the same check a
# "0912…" does.
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class AutofillUnavailable(Exception):
    """The model could not be reached at all — nothing was written."""


def _valid_label(label) -> bool:
    if not isinstance(label, str):
        return False
    label = label.strip()
    if not label or len(label) > MAX_LABEL_CHARS:
        return False
    if "|" in label:
        return False
    return len(label.split()) <= MAX_LABEL_TOKENS


def _clean_labels(raw) -> list:
    """Keep only labels the facet reader would accept, at most three.

    Accepts either the model's list or an already-joined «a | b» string, so
    cleaning a cleaned dict again (run() re-validates at the write layer) is
    a no-op rather than a silent drop.
    """
    if isinstance(raw, str):
        raw = raw.split("|")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, str):
            continue
        label = " ".join(item.split())
        if _valid_label(label) and label not in seen:
            seen.add(label)
            out.append(label)
        if len(out) == MAX_LABELS_PER_COMPANY:
            break
    return out


def _valid_email(value: str) -> bool:
    at = value.count("@")
    return (at == 1 and " " not in value
            and "." in value.split("@", 1)[1] and len(value) <= _EMAIL_CHARS)


def _valid_phone(value: str) -> bool:
    """Digits (Persian or Latin) plus the separators people actually write."""
    translated = value.translate(_FA_DIGITS)
    digits = translated
    for sep in (" ", "-", "(", ")"):
        digits = digits.replace(sep, "")
    stripped_plus = digits.lstrip("+")
    return (translated.count("+") <= 1 and stripped_plus.isdigit()
            and 7 <= len(stripped_plus) <= 15 and len(value) <= _PHONE_CHARS)


def _valid_website(value: str) -> bool:
    if " " in value or "@" in value or len(value) > _WEBSITE_CHARS:
        return False
    body = value
    lowered = body.lower()
    for prefix in ("https://", "http://", "www."):
        while lowered.startswith(prefix):
            body = body[len(prefix):]
            lowered = body.lower()
    # ASCII-only after the scheme: a Persian string with a dot in it is not
    # a URL, it is a sentence the model misfiled.
    return "." in body and body.isascii()


def _clean_fields(raw) -> dict:
    """Keep only values their own validator accepts, keyed by column.

    A value that fails its shape check is DROPPED, not repaired: the code
    never guesses what the model meant, it just refuses to store it.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}

    def _text(value, cap: int) -> str:
        if value is None or isinstance(value, (list, dict, bool)):
            return ""
        s = " ".join(str(value).split())
        return s if len(s) <= cap else ""

    for field, cap in _FREE_TEXT_FIELDS.items():
        if (s := _text(raw.get(field), cap)):
            out[field] = s
    for field in _PHONE_FIELDS:
        if (s := _text(raw.get(field), _PHONE_CHARS)) and _valid_phone(s):
            out[field] = s
    if (s := _text(raw.get("email"), _EMAIL_CHARS)) and _valid_email(s):
        out["email"] = s
    if (s := _text(raw.get("website"), _WEBSITE_CHARS)) and _valid_website(s):
        out["website"] = s
    for field, cap in _EN_FIELDS.items():
        if (s := _text(raw.get(field), cap)):
            out[field] = s
    if (labels := _clean_labels(raw.get("activity_field"))):
        out["activity_field"] = " | ".join(labels)
    return out


def _pending_rows():
    """Companies that can be auto-filled: an intro text but an empty target.

    Rows with NO text are counted but never sent: there is nothing to read a
    field out of, and guessing from a bare title is how a wrong fact gets
    written. They belong to the organizer, not the model.
    """
    from app.db.connection import get_db_connection
    empty_any = " OR ".join(f"COALESCE({f}, '') = ''" for f in EXTRACT_FIELDS)
    conn = get_db_connection()
    try:
        fillable = conn.execute(
            "SELECT id, title, text FROM companies"
            f" WHERE COALESCE(text, '') <> '' AND ({empty_any})"
            " ORDER BY title"
        ).fetchall()
        no_text = conn.execute(
            "SELECT COUNT(*) AS n FROM companies"
            f" WHERE COALESCE(text, '') = '' AND ({empty_any})"
        ).fetchone()["n"]
    finally:
        conn.close()
    return [dict(r) for r in fillable], int(no_text or 0)


def pending() -> dict:
    """What the companies-page button shows before anything runs."""
    fillable, no_text = _pending_rows()
    return {"fillable": len(fillable), "no_text": no_text}


def _vocabulary() -> list:
    """The install's own valid facet labels, most used first.

    Reusing the organizer's own words keeps the taxonomy from forking: the
    model sees what fields already exist and prefers them over inventing a
    synonym nobody filters by.
    """
    from collections import Counter
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT activity_field FROM companies"
            " WHERE COALESCE(activity_field, '') <> ''"
        ).fetchall()
    finally:
        conn.close()
    counts = Counter()
    for r in rows:
        for part in str(r["activity_field"]).split("|"):
            part = " ".join(part.split())
            if _valid_label(part):
                counts[part] += 1
    return [label for label, _ in counts.most_common(VOCABULARY_LIMIT)]


_JSON_KEYS = ("{\"contact_name\":\"\", \"contact_position\":\"\","
              " \"contact_mobile\":\"\", \"email\":\"\", \"website\":\"\","
              " \"company_phone\":\"\", \"fax\":\"\", \"address\":\"\","
              " \"province\":\"\", \"booth_number\":\"\", \"hall\":\"\","
              " \"company_type\":\"\", \"org_stage\":\"\","
              " \"activity_field\":[\"…\"], \"participation\":\"\","
              " \"address_en\":\"\", \"title_en\":\"\", \"text_en\":\"\"}")


def _prompt(company: dict, vocabulary: list) -> str:
    vocab_text = "\n".join(f"- {label}" for label in vocabulary) or "—"
    return (
        "تو دستیار ثبت اطلاعات شرکت‌های یک نمایشگاهی. متن معرفی یک شرکت را"
        " می‌خوانی و اطلاعاتی که در آن هست را در فیلدها استخراج می‌کنی.\n"
        "قواعد:\n"
        "۱. فقط چیزی که واقعاً در متن نوشته شده را استخراج کن. چیزی که در"
        " متن نیست را خالی بگذار — حدس نزن و از خودت نساز.\n"
        "۲. سه فیلد انگلیسی (address_en و title_en و text_en) را خودت به"
        " انگلیسی برگردان: نام شرکت را حروف‌نگاری کن و متن معرفی و نشانی"
        " را ترجمه کن.\n"
        "۳. برای «حوزهٔ فعالیت» حداکثر ۳ برچسب کوتاه بده؛ هر برچسب حداکثر"
        " ۸ کلمه و ۷۰ نویسه، بدون علامت |. اگر از فهرست زیر برچسب مناسب"
        " هست، همان را برگردان؛ فقط وقتی هیچ مناسب نیست برچسب تازه بساز.\n"
        "۴. شماره‌ها و نشانی‌ها را دقیقاً همان‌طور که در متن آمده برگردان؛"
        " شماره نساز.\n"
        "۵. فقط JSON برگردان با همین کلیدها:\n"
        f"{_JSON_KEYS}\n"
        f"فهرست برچسب‌های موجود:\n{vocab_text}\n\n"
        f"نام شرکت: {company.get('title') or ''}\n"
        f"متن معرفی:\n{(company.get('text') or '')[:900]}"
    )


async def _classify(company: dict, vocabulary: list) -> tuple:
    """Ask the model for this company's fields. (fields, usage) — never raises."""
    from app.services.ai.wrapper import padyar_ai
    from app.services.ai.request import AIMessage, FINISH_LENGTH
    from app.services.ai.errors import AIError

    try:
        resp = await padyar_ai.generate(
            [AIMessage(role="user",
                       content=f"اطلاعات شرکت «{company.get('title') or ''}» چیست؟")],
            system_prompt=_prompt(company, vocabulary),
            # The routed chat task: no new task name means no routing-table
            # migration and no admin change for one button.
            task="chat",
            # Every field plus a full English translation of the intro, not
            # three short labels any more — the ceiling grew with the answer.
            max_output_tokens=800,
            temperature=0.0,
            response_format="json_object",
            timeout_s=45.0,
        )
    except AIError as e:
        raise AutofillUnavailable(f"هوش مصنوعی در دسترس نیست ({e.code}).") from e

    if getattr(resp, "finish_reason", "") == FINISH_LENGTH:
        return {}, resp
    try:
        data = json.loads(resp.content)
    except (ValueError, TypeError):
        return {}, resp
    return _clean_fields(data), resp


def _write_fields(company_id: str, fields: dict) -> dict:
    """Write each field into its column, ONLY where that column is still empty.

    The row is re-read at write time and a field the organizer has since
    filled is dropped from the write — their value stays AND the other
    fields still land. The UPDATE's per-column guard covers the gap between
    that read and the write itself. Returns the fields actually written.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(fields) + " FROM companies WHERE id = ?",
            (company_id,)).fetchone()
        if row is None:
            return {}
        still_empty = [f for f in fields if not str(row[f] or "").strip()]
        if not still_empty:
            return {}
        cur = conn.execute(
            "UPDATE companies SET " + ", ".join(f"{f} = ?" for f in still_empty)
            + " WHERE id = ? AND "
            + " AND ".join(f"COALESCE({f}, '') = ''" for f in still_empty),
            (*(fields[f] for f in still_empty), company_id))
        conn.commit()
        return {f: fields[f] for f in still_empty} if cur.rowcount else {}
    finally:
        conn.close()


async def run(actor: str = "", limit: int = BATCH_LIMIT) -> dict:
    """One button press: fill up to `limit` companies, report everything."""
    from app.services import applog

    fillable, no_text = _pending_rows()
    batch = fillable[:max(1, min(int(limit or BATCH_LIMIT), BATCH_LIMIT))]
    vocabulary = _vocabulary() if batch else []

    filled, failed = [], []
    tokens = cost = 0
    for company in batch:
        try:
            fields, resp = await _classify(company, vocabulary)
        except AutofillUnavailable as e:
            applog.error("content", "companies.autofill.ai_unavailable",
                         "پرکردن خودکار لغو شد — هوش مصنوعی در دسترس نیست",
                         actor=actor or None, outcome="unavailable",
                         error_code="ai_unavailable",
                         metadata={"processed_so_far": len(filled)})
            # Nothing is half-written: every write is its own guarded UPDATE.
            raise
        tokens += int(getattr(resp, "tokens_total", 0) or 0)
        cost += float(getattr(resp, "cost", 0.0) or 0.0)
        # Validated HERE, not only inside _classify: the write loop is the
        # layer that owns the contract, so no caller can bypass it — the
        # JSON parse in _classify cleaning first is a convenience, not the
        # guarantee.
        fields = _clean_fields(fields)
        if not fields:
            failed.append({"id": company["id"], "title": company["title"],
                           "reason": "هیچ فیلدی از پاسخ مدل نماند"})
            applog.warning("content", "companies.autofill.company_failed",
                           "برای این شرکت هیچ فیلدی نماند",
                           actor=actor or None, target=str(company["id"])[:60],
                           metadata={"title": str(company["title"])[:120]})
            continue
        written = _write_fields(company["id"], fields)
        if written:
            filled.append({"id": company["id"], "title": company["title"],
                           "fields": sorted(written)})
        else:
            # Every field the model suggested was filled by hand while we
            # ran: the organizer's values stay, and this is a report line,
            # not a fight.
            failed.append({"id": company["id"], "title": company["title"],
                           "reason": "همهٔ فیلدهای پیشنهادی همین حالا دستی پر شده بودند"})

    remaining = max(0, len(fillable) - len(batch))
    applog.info("content", "companies.autofill.run",
                "پرکردن خودکار اطلاعات شرکت‌ها انجام شد",
                actor=actor or None, outcome="ok",
                tokens_in=tokens or None, cost=cost or None,
                metadata={"filled": filled, "failed": failed,
                          "no_text": no_text, "remaining": remaining})
    logger.info("[autofill] filled=%d failed=%d remaining=%d no_text=%d",
                len(filled), len(failed), remaining, no_text)
    return {"filled": filled, "failed": failed, "no_text": no_text,
            "remaining": remaining, "tokens": tokens, "cost": cost}
