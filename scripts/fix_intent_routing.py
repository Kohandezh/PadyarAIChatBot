"""Idempotent data fix for intent routing.

1. Cost-intent question variants. A cost question that also named a procedure
   ("هزینه آب مروارید") used to be pulled to the procedure entry instead of the
   cost video; these explicit cost phrasings route it to the cost video at the
   local-match tier.

2. Branch-name spelling synonyms. Users type a city/branch name joined
   ("شهرری") while the dataset title has it spaced ("شهر ری"). To TF-IDF these
   are unrelated tokens, so the disambiguating location word becomes invisible
   and a branch query collides with another branch on the generic words
   (آدرس/کلینیک/نور) — e.g. "آدرس کلینیک نور شهرری" returned the Motahari clinic.
   A synonym that rewrites the joined form to the spaced form fixes the routing.

Note on greetings: the bare "سلام" question (mapped to the intro) is left in
place ON PURPOSE. The hijack it used to cause ("سلام، هزینه فمتو؟" answered with
the intro) is now prevented in code by stripping a leading greeting before
matching (app/utils/normalizer.strip_leading_greeting), so a *pure* greeting
still returns the intro while a greeting + question matches the question.

Safe to run multiple times. Run once per installation, from the project root:

    python scripts/fix_intent_routing.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DB_PATH  # noqa: E402

COST_ID = "vid_thecostoflasic"

# Cost / price phrasings that should route to the cost video.
COST_QUESTIONS = [
    "هزینه فمتو",
    "هزینه عمل فمتو",
    "قیمت عمل فمتو",
    "هزینه جراحی فمتو",
    "هزینه آب مروارید",
    "هزینه عمل آب مروارید",
    "هزینه بستری",
    "هزینه بستری در کلینیک",
    "هزینه عمل چشم",
    "قیمت عمل چشم",
    "تعرفه عمل",
    "هزینه اسمایل",
    "هزینه لیزیک",
]

# Joined -> spaced spellings so a branch's location word is matchable. The
# normalizer converts Arabic 'ي' -> Persian 'ی' before applying synonyms, so the
# Persian-yeh source covers the Arabic spelling too. (source, target)
SYNONYMS = [
    ("شهرری", "شهر ری"),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Add cost-intent variants, mirroring the cost entry's video_url. Skip any
    # that already exist so re-runs are no-ops.
    row = cur.execute("SELECT video_url FROM dataset WHERE id = ?", (COST_ID,)).fetchone()
    if row is None:
        print(f"WARNING: cost entry '{COST_ID}' not found in dataset — skipping cost questions.")
        cost_video = None
    else:
        cost_video = row[0] or ""

    added = 0
    if cost_video is not None:
        for q in COST_QUESTIONS:
            exists = cur.execute(
                "SELECT 1 FROM questions WHERE question = ? AND dataset_id = ?",
                (q, COST_ID),
            ).fetchone()
            if not exists:
                cur.execute(
                    "INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, ?)",
                    (q, COST_ID, cost_video),
                )
                added += 1

    # Add branch-name synonyms (skip existing for idempotency).
    syn_added = 0
    for source, target in SYNONYMS:
        # Matched on the pair, not on the source. A word may already have other
        # synonyms; matching on the source alone would skip this one forever.
        exists = cur.execute(
            "SELECT 1 FROM synonyms WHERE source = ? AND target = ?", (source, target)
        ).fetchone()
        if not exists:
            cur.execute(
                "INSERT INTO synonyms (source, target) VALUES (?, ?)", (source, target)
            )
            syn_added += 1

    conn.commit()
    conn.close()
    print(f"Added {added} cost question(s); {syn_added} synonym(s).")
    print("Restart the app (or reload the dataset from the admin dashboard) to rebuild the index.")


if __name__ == "__main__":
    main()
