#!/usr/bin/env python3
"""Harvest served visitor queries from chat_logs into the questions table.

    cd /opt/padyar-elecomp && set -a && . .env && set +a && \
    SEED_DEFAULT_CONTENT=false .venv/bin/python \
        /home/gpu/train-work/scripts/harvest_chat_questions.py [--apply]

A real visitor query that a local tier already served confidently is the
cheapest training signal this install owns: it becomes a curated question
row, so Tier 0 (exact), Tier 1 (BM25 + embeddings) and the intent head all
learn it at the next reindex. Dry-run by default; --apply writes.

Guards: only rows whose entry_id names the dataset record actually served,
confidence >= 0.70, sources that serve entries (never free-text AI turns),
normalized dedup against every existing question (a hand mapping always
wins), and a junk filter for greetings/ordinals/pager words.
"""
import argparse
import os
import sys

INSTALL = os.environ.get("INSTALL_DIR", "/opt/padyar-elecomp")
sys.path.insert(0, INSTALL)
os.environ.setdefault("SEED_DEFAULT_CONTENT", "false")

from app.db.connection import get_db_connection  # noqa: E402
from app.utils.normalizer import normalize_persian  # noqa: E402

MIN_CONFIDENCE = 0.70
GOOD_SOURCES = {"local_questions", "local", "local_intent", "local_entity",
                "local_pick", "ai_selected", "openai_classified"}
JUNK_EXACT = {"بیشتر", "سلام", "درود", "خداحافظ", "مرسی", "ممنون", "بله",
              "خیر", "نه", "باشه", "ok", "okay", "yes", "no", "hello", "hi"}
ORDINALS = {"اول", "دوم", "سوم", "چهارم", "پنجم", "ششم", "هفتم", "هشتم",
            "نهم", "دهم", "یکی", "دوتا", "یک"}
MIN_LEN = 6


def _is_junk(norm: str) -> bool:
    if len(norm) < MIN_LEN:
        return True
    if norm in JUNK_EXACT or norm in ORDINALS:
        return True
    if norm.replace(" ", "").isdigit():
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-confidence", type=float, default=MIN_CONFIDENCE)
    args = ap.parse_args()

    conn = get_db_connection()
    existing = {}
    for r in conn.execute("SELECT question, dataset_id FROM questions").fetchall():
        key = normalize_persian(r["question"], expand_synonyms=False)
        existing.setdefault(key, r["dataset_id"])

    rows = conn.execute(
        "SELECT query, entry_id, confidence, source FROM chat_logs"
        " WHERE entry_id IS NOT NULL AND entry_id <> ''"
        "   AND confidence IS NOT NULL"
    ).fetchall()

    valid_entries = {r["id"] for r in
                     conn.execute("SELECT id FROM dataset").fetchall()}

    candidates, skipped = [], {"junk": 0, "dup": 0, "source": 0,
                               "conf": 0, "entry": 0}
    for r in rows:
        if r["source"] not in GOOD_SOURCES:
            skipped["source"] += 1
            continue
        if float(r["confidence"] or 0) < args.min_confidence:
            skipped["conf"] += 1
            continue
        if r["entry_id"] not in valid_entries:
            skipped["entry"] += 1
            continue
        raw = (r["query"] or "").strip()
        if not raw:
            continue
        norm = normalize_persian(raw, expand_synonyms=False)
        if _is_junk(norm):
            skipped["junk"] += 1
            continue
        if norm in existing:
            skipped["dup"] += 1
            continue
        existing[norm] = r["entry_id"]
        candidates.append((raw, r["entry_id"]))

    print(f"chat_logs scanned: {len(rows)}")
    print(f"skipped: {skipped}")
    print(f"harvestable: {len(candidates)}\n")
    for q, did in candidates:
        print(f"  + [{did}] {q}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    for q, did in candidates:
        conn.execute(
            "INSERT INTO questions (question, dataset_id, video_url)"
            " VALUES (?, ?, '')", (q, did))
    conn.commit()
    conn.close()
    print(f"\nAPPLIED: {len(candidates)} question rows written.")
    print("Next: reindex happens at deploy; verify with the admin Questions page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
