#!/usr/bin/env python3
"""Import the exhibition content: FAQ workbook + exhibitor workbook.

    .venv/bin/python scripts/import-content.py --faq X.xlsx --companies Y.xlsx
    .venv/bin/python scripts/import-content.py ... --apply      # writes

WHAT LANDS WHERE
----------------
FAQ workbook (پرسش/پاسخ): each row → one dataset entry (id faq-<n>) + every
  newline-separated question variant in the cell → one curated question row.
  Multi-question cells are the workbook's own way of saying "these all ask
  the same thing" — one entry, several anchors.

Exhibitor workbook (20 columns): each row →
  * dataset:            نام/نام(انگلیسی)/درباره/درباره(انگلیسی) (the public
                        answer — the only fields the chatbot may ever serve)
  * company_profiles:   contact/comms/address/classification fields
  * curated anchors:    4 Persian templates + 1 English per company, because
                        measured retrieval on company names is exactly where
                        the corpus without anchors failed
  * NO company_leads:   spreadsheet data is not consent; a profile never
                        owns a company (search_companies must keep showing it)

SAFETY
------
Dry-run by default: validates, reports, writes nothing. --apply performs
row-level upserts (existing rows with other ids are untouched), rebuilds the
retrieval index, and prints the follow-ups (eval, golden-set expansion).
"""
import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Same pin as run_eval.py, for the same reason: this tool is SQLite-driven
# here and the app imports must not pick a production backend by accident.
os.environ.setdefault("DB_BACKEND", "sqlite")

import openpyxl  # noqa: E402


def _cell(v):
    if v is None:
        return ""
    return re.sub(r"_x000D_|\r", "", str(v)).strip()


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:50]


def load_faq(path: str):
    """[(faq_id, title, text, [question_variants])] + report lines."""
    rows, errors, notes = [], [], []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        q_raw, a = (_cell(c) for c in row[:2])
        variants = [v.strip() for v in re.split(r"[\n\r]+", q_raw) if v.strip()]
        if not variants and not a:
            continue  # fully empty row
        if not variants:
            errors.append(f"faq row {i}: no question (answer discarded)")
            continue
        if not a:
            errors.append(f"faq row {i}: empty answer — skipped ({variants[0][:40]!r})")
            continue
        if "out of sco" in variants[0].lower():
            notes.append(f"faq row {i}: refusal pair imported as-is ({variants[0][:40]!r})")
        rows.append((f"faq-{i:02d}", variants[0][:120], a, variants))
    return rows, errors, notes


FA_CANONICAL = [
    "شرکت {n} چیست؟",
    "درباره {n}",
    "{n} چه کاری انجام می‌دهد؟",
    "فعالیت {n} چیست؟",
]

# Exhibitor workbook column order (verified against the file, 2026-08-26).
COMPANY_COLS = [
    "username", "contact_name", "contact_position", "name", "name_en",
    "email", "website", "phone", "address", "address_en", "fax", "province",
    "company_type", "about", "about_en", "org_stage", "activity_field",
    "participation", "notes", "mobile",
]
PROFILE_MAP = {  # workbook key → company_profiles column
    "contact_name": "contact_name", "contact_position": "contact_position",
    "email": "email", "website": "website", "phone": "company_phone",
    "fax": "fax", "address": "address", "address_en": "address_en",
    "province": "province", "company_type": "company_type",
    "org_stage": "org_stage", "activity_field": "activity_field",
    "participation": "participation", "notes": "notes",
}


def load_companies(path: str):
    """[(dataset_id, dataset_fields, profile_fields, anchors)] + report."""
    rows, errors = [], []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    seen_titles = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        c = dict(zip(COMPANY_COLS, (_cell(v) for v in row)))
        if not c["name"] and not c["name_en"] and not c["about"]:
            continue
        if not c["name"]:
            errors.append(f"company row {i}: no Persian name — skipped")
            continue
        key = re.sub(r"\s+", " ", c["name"])
        if key in seen_titles:
            errors.append(f"company row {i}: duplicate name {key!r} "
                          f"(first at row {seen_titles[key]})")
            continue
        seen_titles[key] = i
        cid = _slug(c["name_en"]) or f"co-{i:03d}"
        mobile = c["mobile"] or c["username"]
        mobile = re.sub(r"^(98|0098)", "0", mobile) if mobile else ""
        profile = {dst: c[src] for src, dst in PROFILE_MAP.items() if c.get(src)}
        if mobile:
            profile["contact_mobile"] = mobile
        anchors = [t.format(n=c["name"]) for t in FA_CANONICAL]
        if c["name_en"]:
            anchors.append(f"What is {c['name_en']}?")
        rows.append((cid, {"id": cid, "title": c["name"], "title_en": c["name_en"],
                           "text": c["about"], "text_en": c["about_en"]},
                     profile, anchors))
    return rows, errors


def main() -> int:
    p = argparse.ArgumentParser(description="Import FAQ + exhibitor workbooks.")
    p.add_argument("--faq", help="FAQ workbook (پرسش/پاسخ)")
    p.add_argument("--companies", help="exhibitor workbook (20 columns)")
    p.add_argument("--apply", action="store_true",
                   help="actually write; without it everything is a dry-run")
    args = p.parse_args()
    if not args.faq and not args.companies:
        sys.exit("nothing to do: pass --faq and/or --companies")

    faq, faq_errors, faq_notes = (load_faq(args.faq) if args.faq else ([], [], []))
    companies, co_errors = (load_companies(args.companies) if args.companies else ([], []))

    os.environ.setdefault("OPENAI_API_KEY", "import")
    from app.db.connection import init_db, get_db_connection
    init_db()
    conn = get_db_connection()
    existing = {r["id"] for r in conn.execute("SELECT id FROM dataset").fetchall()}
    existing_q = {r["question"] for r in conn.execute("SELECT question FROM questions").fetchall()}
    conn.close()

    faq_clash = [r[0] for r in faq if r[0] in existing]
    co_clash = [r[0] for r in companies if r[0] in existing]

    print(f"FAQ rows to import:      {len(faq)}")
    for fid, title, _, variants in faq[:5]:
        print(f"   {fid}: {title[:60]!r} ({len(variants)} question variants)")
    if len(faq) > 5:
        print(f"   … and {len(faq) - 5} more")
    for e in faq_errors:
        print(f"   ERROR {e}")
    for n in faq_notes:
        print(f"   NOTE  {n}")
    print(f"Companies to import:     {len(companies)}")
    for cid, ds, profile, anchors in companies[:5]:
        print(f"   {cid}: {ds['title'][:40]!r} profile_fields="
              f"{sum(1 for v in profile.values() if v)} anchors={len(anchors)}")
    if len(companies) > 5:
        print(f"   … and {len(companies) - 5} more")
    for e in co_errors:
        print(f"   ERROR {e}")
    print(f"Id collisions with DB:   faq={len(faq_clash)} companies={len(co_clash)}")
    if faq_clash or co_clash:
        print(f"   {faq_clash + co_clash}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to import.")
        return 0

    from app.db.connection import get_db_connection
    from app.services import company_profiles
    conn = get_db_connection()
    n_ds = n_q = 0
    for fid, title, text, variants in faq:
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url, title_en, text_en)"
            " VALUES (?, ?, ?, '', '', '')"
            " ON CONFLICT(id) DO UPDATE SET title=excluded.title, text=excluded.text",
            (fid, title, text))
        n_ds += 1
        for v in variants:
            if v in existing_q:
                continue
            conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                         " VALUES (?, ?, '')", (v, fid))
            n_q += 1
    for cid, ds, profile, anchors in companies:
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url, title_en, text_en)"
            " VALUES (?, ?, ?, '', ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET title=excluded.title, text=excluded.text,"
            " title_en=excluded.title_en, text_en=excluded.text_en",
            (cid, ds["title"], ds["text"], ds["title_en"], ds["text_en"]))
        n_ds += 1
        if profile:
            company_profiles.upsert_profile(cid, profile)
        for a in anchors:
            if a in existing_q:
                continue
            conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                         " VALUES (?, ?, '')", (a, cid))
            n_q += 1
    conn.commit()
    conn.close()

    from app.services.search import reindex_and_publish
    reindex_and_publish()
    print(f"\nAPPLIED: {n_ds} dataset rows, {n_q} curated questions, "
          f"{len(companies)} profiles. Index rebuilt.")
    print("Next: .venv/bin/python scripts/run_eval.py   # re-baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
