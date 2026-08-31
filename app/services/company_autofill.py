"""Fill a company's empty حوزهٔ فعالیت from its own intro text, via the model.

WHY THIS EXISTS
---------------
Companies arrive with empty `activity_field` from both live sources: a
workbook whose cell was blank (scripts/import-content.py) and a visitor's
booth proposal that an admin approved (app/services/leads.py:propose_company
writes the text but nothing else). A company with no facet value is INVISIBLE
in every field-filtered chat list («شرکت‌های فعال در زمینهٔ …»), because
app/services/company_search.py builds the facet vocabulary from these values
— measured on the elecomp install on 2026-08-31, 28 of 670 rows were dark
this way, including the one sponsor the organizer had boosted and could not
understand why nothing happened.

The organizer is not a taxonomist. This module is one button on the companies
page: for every company that HAS an intro text but NO activity field, ask the
model for up to three short labels, validate them against the same hard rules
the facet reader enforces, and write them into the empty field only.

THE CONTRACT THE MODEL CANNOT BREAK
----------------------------------
The model SUGGESTS; the code decides. Every label passes _valid_label(), the
exact shape company_search will later accept (≤ 8 tokens, ≤ 70 chars), a
maximum of three survive, and the UPDATE carries
`AND COALESCE(activity_field,'')=''` so a label is never written over
organizer data and the whole run is re-runnable. A company the model fails on
lands in the report, not in the table.
"""
import json

from app.config import logger

# Same numbers the facet reader enforces (company_search._FACET_MAX_*). They
# are repeated here, not imported, so a future edit to the facet limits fails
# THIS module's tests too, not just silently wider labels.
MAX_LABEL_TOKENS = 8
MAX_LABEL_CHARS = 70
MAX_LABELS_PER_COMPANY = 3
# One POST per click processes at most this many companies: a run stays well
# under the nginx proxy timeout, and the UI loops until nothing is pending.
BATCH_LIMIT = 25
# The vocabulary block of the prompt is capped so a 600-label install does
# not turn every call into a token-metered dump of its own taxonomy.
VOCABULARY_LIMIT = 120


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
    """Keep only labels the facet reader would accept, at most three."""
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


def _pending_rows():
    """Companies that can be auto-filled: an intro text but no activity field.

    Rows with NO text are counted but never sent: there is nothing to read a
    field out of, and guessing from a bare title is how a wrong facet gets
    written. They belong to the organizer, not the model.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        fillable = conn.execute(
            "SELECT id, title, text FROM companies"
            " WHERE COALESCE(activity_field, '') = '' AND COALESCE(text, '') <> ''"
            " ORDER BY title"
        ).fetchall()
        no_text = conn.execute(
            "SELECT COUNT(*) AS n FROM companies"
            " WHERE COALESCE(activity_field, '') = '' AND COALESCE(text, '') = ''"
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


def _prompt(company: dict, vocabulary: list) -> str:
    vocab_text = "\n".join(f"- {label}" for label in vocabulary) or "—"
    return (
        "تو دسته‌بند شرکت‌های یک نمایشگاهی. برای هر شرکت از روی متن معرفی‌اش"
        " حداکثر ۳ برچسب کوتاه برای «حوزهٔ فعالیت» پیدا کن.\n"
        "قواعد:\n"
        "۱. هر برچسب حداکثر ۸ کلمه و حداکثر ۷۰ نویسه باشد.\n"
        "۲. اگر از فهرست زیر برچسب مناسب هست، همان را برگردان؛ فقط وقتی هیچ"
        " مناسب نیست برچسب تازه بساز.\n"
        "۳. فارسی ساده بنویس؛ بدون نام شرکت و بدون علامت |.\n"
        "۴. فقط JSON برگردان: {\"labels\": [\"...\", \"...\"]}\n"
        f"فهرست برچسب‌های موجود:\n{vocab_text}\n\n"
        f"نام شرکت: {company.get('title') or ''}\n"
        f"متن معرفی:\n{(company.get('text') or '')[:900]}"
    )


async def _classify(company: dict, vocabulary: list) -> tuple:
    """Ask the model for this company's labels. (labels, usage) — never raises."""
    from app.services.ai.wrapper import padyar_ai
    from app.services.ai.request import AIMessage, FINISH_LENGTH
    from app.services.ai.errors import AIError

    try:
        resp = await padyar_ai.generate(
            [AIMessage(role="user",
                       content=f"حوزهٔ فعالیت شرکت «{company.get('title') or ''}» چیست؟")],
            system_prompt=_prompt(company, vocabulary),
            # The routed chat task: no new task name means no routing-table
            # migration and no admin change for one button.
            task="chat",
            max_output_tokens=200,
            temperature=0.0,
            response_format="json_object",
            timeout_s=45.0,
        )
    except AIError as e:
        raise AutofillUnavailable(f"هوش مصنوعی در دسترس نیست ({e.code}).") from e

    if getattr(resp, "finish_reason", "") == FINISH_LENGTH:
        return [], resp
    try:
        data = json.loads(resp.content)
    except (ValueError, TypeError):
        return [], resp
    return _clean_labels(data.get("labels")), resp


def _write_labels(company_id: str, labels: list) -> bool:
    """Write the joined labels, only if the field is still empty."""
    from app.db.connection import get_db_connection
    joined = " | ".join(labels)
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "UPDATE companies SET activity_field = ?"
            " WHERE id = ? AND COALESCE(activity_field, '') = ''",
            (joined, company_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


async def run(actor: str = "", limit: int = BATCH_LIMIT) -> dict:
    """One button press: fill up to `limit` empty fields, report everything."""
    from app.services import applog

    fillable, no_text = _pending_rows()
    batch = fillable[:max(1, min(int(limit or BATCH_LIMIT), BATCH_LIMIT))]
    vocabulary = _vocabulary() if batch else []

    filled, failed = [], []
    tokens = cost = 0
    for company in batch:
        try:
            labels, resp = await _classify(company, vocabulary)
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
        labels = _clean_labels(labels)
        if not labels:
            failed.append({"id": company["id"], "title": company["title"],
                           "reason": "برچسبی از پاسخ مدل نماند"})
            applog.warning("content", "companies.autofill.company_failed",
                           "برای این شرکت برچسبی نماند",
                           actor=actor or None, target=str(company["id"])[:60],
                           metadata={"title": str(company["title"])[:120]})
            continue
        if _write_labels(company["id"], labels):
            filled.append({"id": company["id"], "title": company["title"],
                           "labels": labels})
        else:
            # The field stopped being empty while we ran: an organizer edit
            # won the race, and theirs stays.
            failed.append({"id": company["id"], "title": company["title"],
                           "reason": "حوزهٔ فعالیت در همین لحظه دستی پر شد"})

    remaining = max(0, len(fillable) - len(batch))
    applog.info("content", "companies.autofill.run",
                "پرکردن خودکار حوزهٔ فعالیت انجام شد",
                actor=actor or None, outcome="ok",
                tokens_in=tokens or None, cost=cost or None,
                metadata={"filled": filled, "failed": failed,
                          "no_text": no_text, "remaining": remaining})
    logger.info("[autofill] filled=%d failed=%d remaining=%d no_text=%d",
                len(filled), len(failed), remaining, no_text)
    return {"filled": filled, "failed": failed, "no_text": no_text,
            "remaining": remaining, "tokens": tokens, "cost": cost}
