#!/usr/bin/env python3
"""Merge talksiran exhibitor data into the companies table (elecomp).

    cd /opt/padyar-elecomp && set -a && . .env && set +a && \
    SEED_DEFAULT_CONTENT=false .venv/bin/python \
        /home/gpu/train-work/scripts/merge_talksiran_exhibitors.py \
        --input /home/gpu/train-work/data/exhibitors.jsonl [--apply]

POLICY
------
The organizer workbook stays the source of truth: for a MATCHED company
(normalized title, then token-subset) only EMPTY columns are filled —
activity_field, contact_name (with contact_position «مدیرعامل»), website,
company_phone, email, hall, booth_number — and a one-line provenance note
is appended. Nothing non-empty is ever overwritten.

An UNMATCHED talksiran exhibitor becomes a new row (id ts-<source_id>,
source 'talksiran') with a minimal honest text, so company search can
find it; the admin enriches from the panel. Dry-run by default.
"""
import argparse
import json
import os
import sys

INSTALL = os.environ.get("INSTALL_DIR", "/opt/padyar-elecomp")
sys.path.insert(0, INSTALL)
os.environ.setdefault("SEED_DEFAULT_CONTENT", "false")

from app.db.connection import get_db_connection  # noqa: E402
from app.utils.normalizer import normalize_persian  # noqa: E402

FILLABLE = ("activity_field", "website", "company_phone", "email",
            "hall", "booth_number")
NOTE = "talksiran 1405/06/10: ceo/field/hall merged"
FA_CANONICAL = [
    "شرکت {n} چیست؟",
    "درباره {n}",
    "{n} چه کاری انجام می‌دهد؟",
    "فعالیت {n} چیست؟",
]


def _key(text: str) -> str:
    return normalize_persian((text or "").strip(), expand_synonyms=False)


def _tokens(key: str) -> set:
    stop = {"شرکت", "گروه", "موسسه", "سازمان", "پژوهشی",
            "تولیدی", "صنعتی", "و"}
    return {t for t in key.split() if t not in stop and len(t) >= 3}


def _match_row(key: str, by_key: dict, by_tokens: list):
    """Exact normalized title, else the containment candidate with the
    highest token overlap. Short names whose token set is empty (شرکت زر
    پی) are excluded — an empty set is a subset of everything."""
    row = by_key.get(key)
    if row is not None:
        return row
    toks = _tokens(key)
    if not toks:
        return None
    best, best_overlap, second = None, 0, 0
    for tt, cand in by_tokens:
        if not tt:
            continue
        if not (toks <= tt or tt <= toks):
            continue
        overlap = len(toks & tt)
        if overlap > best_overlap:
            best, best_overlap, second = cand, overlap, best_overlap
        elif overlap > second:
            second = overlap
    if best is not None and best_overlap >= 2 and best_overlap > second:
        return best
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    exhibitors = [json.loads(l) for l in
                  open(args.input, encoding="utf-8") if l.strip()]
    conn = get_db_connection()
    companies = conn.execute(
        "SELECT id, title, activity_field, contact_name, contact_position,"
        " website, company_phone, email, hall, booth_number, notes"
        " FROM companies").fetchall()
    by_key = {}
    by_tokens = []
    for row in companies:
        k = _key(row["title"])
        if k:
            by_key[k] = row
            by_tokens.append((_tokens(k), row))

    matched_fills, inserts, unmatched = [], [], []
    for e in exhibitors:
        name = (e.get("name") or "").strip()
        if not name or name == (e.get("field") or "").strip():
            unmatched.append((e, "no usable name"))
            continue
        row = _match_row(_key(name), by_key, by_tokens)
        if row is not None:
            fills = {}
            for col in FILLABLE:
                if e.get(col) and not (row[col] or "").strip():
                    fills[col] = str(e[col]).strip()
            if e.get("ceo") and not (row["contact_name"] or "").strip():
                fills["contact_name"] = e["ceo"].strip()
                fills["contact_position"] = "مدیرعامل"
            if fills:
                matched_fills.append((row["id"], row["title"], fills))
        else:
            inserts.append(e)

    print(f"exhibitors: {len(exhibitors)}")
    print(f"matched, filled: {len(matched_fills)}")
    print(f"new inserts: {len(inserts)}")
    print(f"unmatched/skipped: {len(unmatched)}\n")
    for cid, title, fills in matched_fills[:12]:
        print(f"  ~ [{cid}] {title[:35]} <- {sorted(fills)}")
    print()
    for e in inserts[:12]:
        print(f"  + [ts-{e['source_id']}] {e.get('name', '')[:40]}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    for cid, _, fills in matched_fills:
        sets = ", ".join(f"{c} = ?" for c in fills)
        params = list(fills.values()) + [NOTE, "\n" + NOTE, cid]
        conn.execute(
            f"UPDATE companies SET {sets},"
            " notes = CASE WHEN notes = '' THEN ?"
            " ELSE notes || ? END"
            " WHERE id = ?", params)
    for e in inserts:
        field = (e.get("field") or "").strip()
        text = f"غرفه‌دار نمایشگاه الکامپ ۲۹." \
               + (f" زمینه فعالیت: {field}" if field else "")
        conn.execute(
            "INSERT INTO companies (id, title, text, activity_field,"
            " contact_name, contact_position, website, company_phone,"
            " email, hall, booth_number, source, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'talksiran', ?)"
            " ON CONFLICT (id) DO NOTHING",
            (f"ts-{e['source_id']}", (e.get("name") or "").strip(), text,
             field, (e.get("ceo") or "").strip(), "مدیرعامل" if e.get("ceo") else "",
             (e.get("website") or "").strip(), (e.get("phone") or "").strip(),
             (e.get("email") or "").strip(), (e.get("hall") or "").strip(),
             (e.get("booth") or "").strip(), NOTE))
    existing_q = {r["question"] for r in
                  conn.execute("SELECT question FROM questions").fetchall()}
    n_anchors = 0
    for e in inserts:
        cid = f"ts-{e['source_id']}"
        for t in FA_CANONICAL:
            q = t.format(n=(e.get("name") or "").strip())
            if q in existing_q:
                continue
            existing_q.add(q)
            conn.execute(
                "INSERT INTO questions (question, dataset_id, video_url)"
                " VALUES (?, ?, '')", (q, cid))
            n_anchors += 1
    conn.commit()
    n = conn.execute("SELECT count(*) AS n FROM companies").fetchone()["n"]
    conn.close()
    print(f"\nAPPLIED: companies now {n} rows "
          f"({len(matched_fills)} filled, {len(inserts)} inserted, "
          f"{n_anchors} anchors).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
