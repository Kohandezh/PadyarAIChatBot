"""Company profiles: what the organizer already knows about each exhibitor.

THE RELATION THIS SERVES
------------------------
A company is one row of `app.companies` — see
migrations/0013_companies.sql and docs/features/companies-own-table/RESEARCH.md
for why: it used to be a `dataset` row (what the PUBLIC reads) plus a
`company_profiles` row (what the ORGANIZER knows), joined 1:1 by id, and
every reader had to remember to do that join. Now it is one row, one table.
This module is what remains of the old `company_profiles` service: the same
functions, reading and writing the profile-shaped COLUMNS of that one row
instead of a whole separate table.

`company_leads` stays a separate table, deliberately: a lead is a VERIFIED
CAPTURE EVENT (OTP at the booth or an admin vouching), not a fact about the
company. Importing a spreadsheet must never create a lead — that would claim
every company and lock the booth out of all of them (search_companies hides
owned companies, see app/services/leads.py).
"""


class ProfileError(Exception):
    """A refusal the admin panel may read."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# The editable profile fields, in the order the admin form shows them. The
# company name itself (companies.title / title_en / text) is NOT here: it is
# the chatbot's public answer and belongs to the dataset-style editor, not
# this one — one fact, one editor.
PROFILE_FIELDS = (
    "contact_name", "contact_position", "contact_mobile",
    "email", "website", "company_phone", "fax",
    "address", "address_en", "province",
    "company_type", "org_stage", "activity_field", "participation",
    "notes",
)

# What a VISITOR may be told. This is an ALLOWLIST on purpose: a column added
# to `companies` later is withheld until someone deliberately adds it here. A
# denylist would publish every new column by default, and the one time that
# is wrong it is wrong about somebody's personal data.
#
# Read against the workbook mapping (PROFILE_MAP in scripts/import-content.py)
# these are the COMPANY's own coordinates: the workbook `phone` column lands in
# `company_phone` — the company landline printed on its own letterhead — plus
# its website, fax, address, province, type, stage, field and participation.
PUBLIC_PROFILE_FIELDS = (
    "website", "company_phone", "fax",
    "address", "address_en", "province",
    "company_type", "org_stage", "activity_field", "participation",
)

# WITHHELD, and why: contact_name, contact_position, contact_mobile and email
# are not the company's — they are ONE named person's details. The workbook
# gives their name, their job title, their personal mobile (`mobile`, falling
# back to the login `username` when the sheet left it blank) and their email
# address. That person handed those to the organizer so the organizer could
# reach them, never so the chatbot could read them out to any visitor who
# asks. `notes` is the organizer's private remark about the company. None of
# these five may ever leave this module.


def get_profile(dataset_id: str) -> dict:
    """The profile-shaped columns of one company, or {} if it has none yet.

    `companies` is a core table (created in init_db(), unlike the old
    company_profiles which lived behind the leads module's ensure_tables()),
    so there is no table-creation step here any more. But a company row now
    always exists (it's the same row `dataset` used to hold), so "no profile"
    can no longer mean "no row" the way it did with a separate
    company_profiles table — it means no profile FIELD has ever been filled
    in. Same {} contract for that case, computed instead of read off a
    missing row.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id AS dataset_id, " + ", ".join(PROFILE_FIELDS)
            + ", source, created_at, updated_at"
            + " FROM companies WHERE id = ?", (dataset_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    profile = dict(row)
    if not any((profile.get(f) or "").strip() for f in PROFILE_FIELDS):
        return {}
    return profile


def public_profile(dataset_id: str) -> dict:
    """The allowlisted, non-empty fields of one company — the ONLY way profile
    data reaches a visitor.

    Everything that answers a visitor goes through here rather than reading the
    table itself, so the allowlist is one gate and not a rule each caller has
    to remember. The SELECT names the public columns explicitly: a withheld
    column is never even loaded into memory on a visitor's request path.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(PUBLIC_PROFILE_FIELDS)
            + " FROM companies WHERE id = ?", (dataset_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    # Empty columns are dropped, not returned blank: "" is not an answer, and
    # the caller decides to stay quiet by finding the key missing.
    return {k: str(v).strip() for k, v in dict(row).items()
            if v is not None and str(v).strip()}


def upsert_profile(dataset_id: str, values: dict) -> dict:
    """Update the profile columns of an existing company row.

    Unknown keys are dropped rather than stored: the import path and the form
    both build field dicts, and a typo surviving silently into a column nobody
    reads is how profiles drift from the schema.

    "Upsert" is a holdover name from when this wrote a separate table keyed on
    dataset_id; a company row always already exists in `companies` by the time
    this runs (companies are created on the dataset-style editor / by import,
    never by this form), so this is always an UPDATE.
    """
    from datetime import datetime, timezone
    from app.db.connection import get_db_connection
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    clean = {k: (str(v).strip() if v is not None else "")
             for k, v in values.items() if k in PROFILE_FIELDS}

    conn = get_db_connection()
    try:
        company = conn.execute(
            "SELECT id FROM companies WHERE id = ?", (dataset_id,)
        ).fetchone()
        if company is None:
            raise ProfileError("این شرکت در دانش‌نامه نیست.", status=404)
        # Timestamps are Python-side values (naive UTC), the convention every
        # other table here uses — datetime('now') is SQLite-only.
        conn.execute(
            "UPDATE companies SET "
            + ", ".join(f"{k} = ?" for k in clean)
            + ", source = 'admin', updated_at = ? WHERE id = ?",
            (*clean.values(), now, dataset_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_profile(dataset_id)


def sync_from_lead(lead: dict) -> None:
    """Fold a verified capture's contact data into the company's profile.

    The spreadsheet's phone was a guess; the booth's phone is OTP-verified.
    So once a lead verifies, its contact name/position/mobile become the
    profile's — overwriting whatever the import left there. This is the
    "profile = best known state" rule, and it only ever flows one way:
    profile data never becomes a lead (that would fake a consent).

    Failure is swallowed deliberately: the lead flow is mid-transaction and
    must not roll back because a display table could not be refreshed.
    """
    try:
        from datetime import datetime, timezone
        from app.db.connection import get_db_connection
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn = get_db_connection()
        try:
            conn.execute(
                "UPDATE companies SET contact_name = ?, contact_position = ?,"
                " contact_mobile = ?, source = 'booth', updated_at = ?"
                " WHERE id = ?",
                ((lead.get("first_name", "") + " " + lead.get("last_name", "")).strip(),
                 lead.get("position", ""), lead.get("phone", ""), now,
                 lead["dataset_id"]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        from app.config import logger
        logger.error("[profiles] sync_from_lead failed: %s", type(e).__name__)


def list_companies(query: str = "", limit: int = 500) -> list:
    """Every company beside what is known about it AND its capture state.

    All of `companies`: every row here IS a company (unlike the old `dataset`,
    which also held FAQ rows), so there is no LEFT JOIN of "which dataset rows
    are companies" left to do.

    `has_profile` used to mean "a company_profiles row exists at all" — now
    every company row exists by construction, so the operator-facing question
    it answers ("which companies still have a hole") is reframed as "does this
    company have ANY recorded profile data yet" — true when at least one of
    the profile columns is non-empty.

    `lead_status` is the SALES lens (why this whole feature exists): which
    companies nobody has approached yet, which are being worked, which are
    done. It is the live owner's state — verified_and_waiting (a contact
    confirmed but never sent their text) or completed (text received, in
    review) — and NULL when the company is still untouched. Released or
    unverified attempts are history, not a state: the company is back to
    "not approached", which is exactly what the next visitor needs to see.

    `company_leads` is still a `leads`-module table (see app/services/leads.py
    `_TABLES`), unlike `companies` itself, so this — unlike get_profile/
    upsert_profile/public_profile, which only ever touch `companies` — still
    needs the ensure call.
    """
    from app.db.connection import get_db_connection
    from app.services.leads import _live_owner, ensure_tables
    ensure_tables()
    term = (query or "").strip()
    conn = get_db_connection()
    try:
        if term:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = (" WHERE (c.title LIKE ? ESCAPE '\\'"
                     " OR c.contact_name LIKE ? ESCAPE '\\'"
                     " OR c.activity_field LIKE ? ESCAPE '\\'"
                     " OR c.province LIKE ? ESCAPE '\\')")
            args = [f"%{escaped}%"] * 4
        else:
            where, args = "", []
        rows = conn.execute(
            "SELECT c.id, c.title, c.title_en, " + ", ".join(f"c.{f}" for f in PROFILE_FIELDS) + ","
            " o.status AS lead_status, o.id AS lead_id"
            " FROM companies c"
            " LEFT JOIN company_leads o ON o.dataset_id = c.id AND " + _live_owner("o")
            + where + " ORDER BY c.title LIMIT ?", (*args, limit)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["has_profile"] = any((d.get(f) or "").strip() for f in PROFILE_FIELDS)
        out.append(d)
    return out
