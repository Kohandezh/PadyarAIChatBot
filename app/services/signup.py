"""The server-owned half of visitor sign-up: what is still missing, which
answers are acceptable, and how one field reaches the `visitors` row.

SPEC: docs/features/signup-integrity/SPEC.md — §3 (the row IS the state),
§5 (validation), §6 (enforcement). The frontend mirrors these rules for
UX only; every decision here is final.

Three consumers share ONE definition of "complete" (is_complete): /chat's
403 gate, the /api/signup/* endpoints, and /api/auth/profile. Three
private copies of that rule is the bug class this file exists to prevent.
"""
import re

from app.config import logger

INCOMPLETE_CODE = "signup_incomplete"
WRONG_STEP_CODE = "wrong_step"
INVALID_CODE = "invalid_answer"

ORDER = ("name", "job", "position", "interests")
_LIST_KEY = {"job": "jobs", "position": "positions", "interests": "interests"}
_CAPS = {"first_name": 60, "last_name": 60, "job": 80,
         "position": 80, "interests": 400}

PROMPTS = {
    "fa": {
        "name": "نام و نام خانوادگی شما چیست؟",
        "job": "شغل یا حوزهٔ فعالیت شما چیست؟",
        "position": "سمت شما چیست؟",
        "interests": "به کدام زمینه‌ها علاقه دارید؟",
    },
    "en": {
        "name": "What is your full name?",
        "job": "What is your field of work?",
        "position": "What is your job title?",
        "interests": "Which topics do you care about?",
    },
}


def _norm(value) -> str:
    return str(value or "").strip()


def _split(value) -> list:
    return [p.strip() for p in re.split(r"[،,]", str(value or "")) if p.strip()]


def _doc():
    from app.services import taxonomy
    return taxonomy.document()


def _label_ids(items) -> dict:
    """label (fa and en, casefolded) -> id. Persian has no case; English
    labels must match regardless of the visitor's keyboard."""
    out = {}
    for item in items:
        for v in (item.get("fa"), item.get("en")):
            v = _norm(v)
            if v:
                out[v.casefold()] = item["id"]
    return out


def _none_position_label(doc=None) -> str:
    doc = doc or _doc()
    for p in doc.get("positions", []):
        if p.get("id") == "none":
            return _norm(p.get("fa"))
    return ""


def _is_no_position_job(job_label: str, doc=None) -> bool:
    doc = doc or _doc()
    jid = _label_ids(doc["jobs"]).get(_norm(job_label).casefold(), "")
    return bool(jid) and jid in doc.get("no_position_jobs", [])


# ── The one definition of "complete" ──────────────────────────────────────

def is_complete(row: dict) -> bool:
    """All four answers present AND valid per the CURRENT taxonomy.

    Fail-open: with no taxonomy lists (missing file / builtin minimum) only
    presence is checked, so an unconfigured install never locks its
    visitors out of the chat."""
    if not (_norm(row.get("first_name")) or _norm(row.get("last_name"))):
        return False
    job = _norm(row.get("job"))
    position = _norm(row.get("position"))
    interests = _norm(row.get("interests"))
    if not (job and position and interests):
        return False
    doc = _doc()
    if doc["jobs"] and job.casefold() not in _label_ids(doc["jobs"]):
        return False
    positions = doc.get("positions", [])
    if positions:
        if position.casefold() not in _label_ids(positions):
            return False
        none = _none_position_label(doc)
        if _is_no_position_job(job, doc) and none and position != none:
            return False
    if doc["interests"] or doc["flags"]:
        valid = (set(_label_ids(doc["interests"]))
                 | set(_label_ids(doc["flags"])))
        if any(i.casefold() not in valid for i in _split(interests)):
            return False
    return True


def _field_done(row: dict, key: str) -> bool:
    if key == "name":
        return bool(_norm(row.get("first_name")) or _norm(row.get("last_name")))
    return bool(_norm(row.get(key)))


def pending_step(row: dict, lang: str = "fa") -> dict:
    """The next question the visitor owes, with its options, or complete."""
    from app.services import taxonomy
    lang = "en" if str(lang).lower().startswith("en") else "fa"
    if is_complete(row):
        return {"complete": True}
    opts = taxonomy.form_options(lang)
    for key in ORDER:
        if _field_done(row, key):
            continue
        step = {"key": key, "multi": key == "interests",
                "prompt": PROMPTS[lang][key]}
        if key == "position" and _is_no_position_job(_norm(row.get("job"))):
            # One chip: the only answer the validator accepts for this job.
            none = _none_position_label()
            step["options"] = ([{"id": "none", "label": none}] if none else [])
        elif key != "name":
            step["options"] = opts.get(_LIST_KEY[key], [])
        else:
            step["options"] = []
        return {"step": step}
    return {"complete": True}


# ── Per-field rules, shared by the flow and the edit endpoint ────────────

def _check_job(job: str, doc) -> str:
    if doc["jobs"] and _norm(job).casefold() not in _label_ids(doc["jobs"]):
        return "شغل انتخابی در فهرست نیست — از گزینه‌های بالا انتخاب کنید."
    return ""


def _check_position(position: str, job: str, doc) -> str:
    positions = doc.get("positions", [])
    if positions and _norm(position).casefold() not in _label_ids(positions):
        return "سمت انتخابی در فهرست نیست — از گزینه‌های بالا انتخاب کنید."
    none = _none_position_label(doc)
    if none and _is_no_position_job(job, doc) and _norm(position) != none:
        return "برای این شغل سمت سازمانی ندارید — گزینهٔ «سمت سازمانی ندارم» را انتخاب کنید."
    return ""


def _check_interests(value: str, doc) -> str:
    if doc["interests"] or doc["flags"]:
        valid = (set(_label_ids(doc["interests"]))
                 | set(_label_ids(doc["flags"])))
        bad = [i for i in _split(value) if i.casefold() not in valid]
        if bad:
            return f"«{bad[0]}» در فهرست نیست — از گزینه‌های بالا انتخاب کنید."
    return ""


def validate_answer(row: dict, key: str, value: str):
    """One answer against the taxonomy. Returns (ok, message, fields)."""
    doc = _doc()
    value = _norm(value)
    if key == "name":
        full = " ".join(value.split())[:120]
        if not full:
            return False, "نام را بنویسید و دکمهٔ ارسال را بزنید.", {}
        parts = full.split(" ")
        return True, "", {"first_name": parts[0], "last_name": " ".join(parts[1:])}
    if key == "job":
        job = value[:_CAPS["job"]]
        problem = _check_job(job, doc)
        if problem:
            return False, problem, {}
        fields = {"job": job}
        if _is_no_position_job(job, doc):
            none = _none_position_label(doc)
            if none:
                # Resolves the سمت question in the same write; the flow
                # then never asks it (spec REQ-009).
                fields["position"] = none
        return True, "", fields
    if key == "position":
        position = value[:_CAPS["position"]]
        problem = _check_position(position, _norm(row.get("job")), doc)
        if problem:
            return False, problem, {}
        return True, "", {"position": position}
    if key == "interests":
        items = _split(value)
        problem = _check_interests("، ".join(items), doc)
        if problem:
            return False, problem, {}
        joined = "، ".join(items)[:_CAPS["interests"]]
        if not joined:
            return False, "حداقل یک زمینه را انتخاب کنید.", {}
        return True, "", {"interests": joined}
    return False, "پرسش نامعتبر است.", {}


def validate_profile_edit(job: str, position: str, interests: str) -> str:
    """The edit endpoint's trio, as a unit: position pairs with the NEW job."""
    doc = _doc()
    return (_check_job(job, doc)
            or _check_position(position, job, doc)
            or _check_interests(interests, doc))


def sanitize_registration(record: dict) -> dict:
    """Filter a challenge's carried fields through the same rules.

    A visitor who just proved their phone must never be told the verify
    failed over a job string — invalid values are DROPPED here, leaving
    the field empty so the signup flow asks it properly."""
    doc = _doc()
    out = dict(record)
    job = _norm(record.get("job"))
    if job and _check_job(job, doc):
        out["job"] = ""
        job = ""
    if _norm(record.get("position")) and _check_position(
            _norm(record.get("position")), job, doc):
        out["position"] = ""
    interests = _norm(record.get("interests"))
    if interests:
        # Multi-select: a carried list keeps its valid items and only the
        # unknown ones fall off — unlike job/position, one bad chip must
        # not cost the visitor their good picks.
        if doc["interests"] or doc["flags"]:
            valid = (set(_label_ids(doc["interests"]))
                     | set(_label_ids(doc["flags"])))
            kept = [i for i in _split(interests) if i.casefold() in valid]
        else:
            kept = _split(interests)
        out["interests"] = "، ".join(kept)[:_CAPS["interests"]]
    return out


# ── Writes ────────────────────────────────────────────────────────────────

def write_fields(visitor_id: str, fields: dict) -> bool:
    """Write the given profile columns for ONE visitor row, by id only."""
    from app.db.connection import get_db_connection
    sets, params = [], []
    for column in ("first_name", "last_name", "job", "position", "interests"):
        if column in fields:
            sets.append(f"{column} = ?")
            params.append(str(fields[column])[:_CAPS[column]])
    if not sets:
        return False
    # datetime('now') is INLINE on purpose — app/db/pg.py rewrites the literal.
    sets.append("last_seen_at = datetime('now')")
    try:
        conn = get_db_connection()
        try:
            changed = conn.execute(
                "UPDATE visitors SET " + ", ".join(sets) + " WHERE id = ?",
                (*params, visitor_id)).rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return changed > 0
    except Exception as e:  # noqa: BLE001 — a write fault is reported, not raised
        logger.error("[signup] profile write failed: %s: %s", type(e).__name__, e)
        return False


def visitor_complete(visitor_id: str) -> bool:
    """Fail-open on storage trouble: a DB hiccup must not 403 the chat."""
    if not visitor_id:
        return True
    from app.services import conversations
    try:
        row = conversations.get_visitor(visitor_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[signup] visitor unreadable: %s: %s", type(e).__name__, e)
        return True
    return is_complete(row) if row else True
