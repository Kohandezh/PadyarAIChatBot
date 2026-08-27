"""Company profiles: what the organizer already knows about each exhibitor.

THE RELATION THIS SERVES
------------------------
One `dataset` row IS one company (the chatbot's answer sheet), and this table
hangs off it 1:1 by primary key:

    dataset.id ◄──── company_profiles.dataset_id (PK, no separate id)
                     company_leads.dataset_id  (the capture/consent events)

Three tables, three lifetimes, deliberately not merged:

  * `dataset` — what the PUBLIC reads (title, text, video). Core module.
  * `company_profiles` — what the ORGANIZER knows (contact, address, type).
    Imported in bulk, edited here, never shown to the public as-is.
  * `company_leads` — a VERIFIED CAPTURE EVENT (OTP at the booth or an admin
    vouching). A row there OWNS the company. Spreadsheet data is not consent,
    so importing it must never create a lead — that would claim all 169
    companies and lock the booth out of every one of them (search_companies
    hides owned companies).

The schema lives in migrations/0008_company_profiles.sql (PostgreSQL) and in
`_TABLES` of app/services/leads.py (the SQLite test mirror), the same split
every other table in this module uses.
"""


class ProfileError(Exception):
    """A refusal the admin panel may read."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# The editable profile fields, in the order the admin form shows them. The
# company name itself (dataset.title / title_en / text) is NOT here: it is the
# chatbot's public answer and belongs to the dataset page — one fact, one
# editor.
PROFILE_FIELDS = (
    "contact_name", "contact_position", "contact_mobile",
    "email", "website", "company_phone", "fax",
    "address", "address_en", "province",
    "company_type", "org_stage", "activity_field", "participation",
    "notes",
)

# What a VISITOR may be told. This is an ALLOWLIST on purpose: a column added
# to company_profiles later is withheld until someone deliberately adds it
# here. A denylist would publish every new column by default, and the one
# time that is wrong it is wrong about somebody's personal data.
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


def ensure_tables() -> None:
    # The DDL lives with the leads module's tables (this table is part of the
    # same feature surface and is created by the same ensure call).
    from app.services import leads
    leads.ensure_tables()


def get_profile(dataset_id: str) -> dict:
    ensure_tables()
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM company_profiles WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def public_profile(dataset_id: str) -> dict:
    """The allowlisted, non-empty fields of one company — the ONLY way profile
    data reaches a visitor.

    Everything that answers a visitor goes through here rather than reading the
    table itself, so the allowlist is one gate and not a rule each caller has
    to remember. The SELECT names the public columns explicitly: a withheld
    column is never even loaded into memory on a visitor's request path.

    Deliberately no ensure_tables() call, same as the company-list tier: an
    install without the leads module has no company_profiles table, and that
    absence just means there is nothing public to say — it is not a reason to
    grow schema the install never ordered. The caller catches the DB error.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT " + ", ".join(PUBLIC_PROFILE_FIELDS)
            + " FROM company_profiles WHERE dataset_id = ?", (dataset_id,)
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
    """Create or update the one profile of a dataset row.

    Unknown keys are dropped rather than stored: the import path and the form
    both build field dicts, and a typo surviving silently into a column nobody
    reads is how profiles drift from the schema.
    """
    ensure_tables()
    from datetime import datetime, timezone
    from app.db.connection import get_db_connection
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    clean = {k: (str(v).strip() if v is not None else "")
             for k, v in values.items() if k in PROFILE_FIELDS}

    conn = get_db_connection()
    try:
        company = conn.execute(
            "SELECT id FROM dataset WHERE id = ?", (dataset_id,)
        ).fetchone()
        if company is None:
            raise ProfileError("این شرکت در دانش‌نامه نیست.", status=404)
        # Upsert by primary key: an import re-running never duplicates a
        # profile, and the admin form overwrites what the import left.
        # Timestamps are Python-side values (naive UTC), the convention every
        # other table here uses — datetime('now') is SQLite-only.
        conn.execute(
            "INSERT INTO company_profiles (dataset_id, " + ", ".join(clean.keys())
            + ", source, created_at, updated_at)"
            " VALUES (?, " + ", ".join("?" for _ in clean) + ", 'admin', ?, ?)"
            " ON CONFLICT(dataset_id) DO UPDATE SET "
            + ", ".join(f"{k} = excluded.{k}" for k in clean)
            + ", updated_at = excluded.updated_at",
            (dataset_id, *clean.values(), now, now),
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
        ensure_tables()
        from datetime import datetime, timezone
        from app.db.connection import get_db_connection
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO company_profiles (dataset_id, contact_name,"
                " contact_position, contact_mobile, source, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'booth', ?, ?)"
                " ON CONFLICT(dataset_id) DO UPDATE SET"
                " contact_name = excluded.contact_name,"
                " contact_position = excluded.contact_position,"
                " contact_mobile = excluded.contact_mobile,"
                " source = 'booth', updated_at = excluded.updated_at",
                (lead["dataset_id"],
                 (lead.get("first_name", "") + " " + lead.get("last_name", "")).strip(),
                 lead.get("position", ""), lead.get("phone", ""), now, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        from app.config import logger
        logger.error("[profiles] sync_from_lead failed: %s", type(e).__name__)


def list_companies(query: str = "", limit: int = 500) -> list:
    """Every dataset entry beside its profile AND its capture state.

    All of `dataset`, not only rows with a profile: the operator's job is to
    see which companies still have a hole (no profile) — a list that hides
    them shows a finished page that is not finished.

    `lead_status` is the SALES lens (why this whole feature exists): which
    companies nobody has approached yet, which are being worked, which are
    done. It is the live owner's state — verified_and_waiting (a contact
    confirmed but never sent their text) or completed (text received, in
    review) — and NULL when the company is still untouched. Released or
    unverified attempts are history, not a state: the company is back to
    "not approached", which is exactly what the next visitor needs to see.
    """
    ensure_tables()
    from app.db.connection import get_db_connection
    from app.services.leads import _live_owner
    term = (query or "").strip()
    conn = get_db_connection()
    try:
        if term:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = (" WHERE (d.title LIKE ? ESCAPE '\\'"
                     " OR p.contact_name LIKE ? ESCAPE '\\'"
                     " OR p.activity_field LIKE ? ESCAPE '\\'"
                     " OR p.province LIKE ? ESCAPE '\\')")
            args = [f"%{escaped}%"] * 4
        else:
            where, args = "", []
        rows = conn.execute(
            "SELECT d.id, d.title, d.title_en,"
            " p.contact_name, p.contact_position, p.contact_mobile,"
            " p.email, p.website, p.company_phone, p.province,"
            " p.company_type, p.activity_field, p.participation,"
            " (p.dataset_id IS NOT NULL) AS has_profile,"
            " o.status AS lead_status, o.id AS lead_id"
            " FROM dataset d"
            " LEFT JOIN company_profiles p ON p.dataset_id = d.id"
            " LEFT JOIN company_leads o ON o.dataset_id = d.id AND " + _live_owner("o")
            + where + " ORDER BY d.title LIMIT ?", (*args, limit)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
