#!/usr/bin/env python3
"""Import the crawled INOTEX 2026 program content into an installed database.

    .venv/bin/python scripts/import-inotex-programs.py            # dry-run
    .venv/bin/python scripts/import-inotex-programs.py --apply    # write

WHAT LANDS WHERE
----------------
The dataset entries, curated question anchors and synonym rows defined in
app/default_content.py under the 2026 program block (crawled 2026-08-27 from
https://inotex.com/programs and the program news on https://inotex.com/fa/allnews)
are upserted by id:

  * dataset rows in INOTEX_2026_PROGRAM_IDS  -> INSERT, or UPDATE of
    title/text/title_en/text_en when the id already exists (position and
    video_url are never touched, so admin edits to those survive)
  * INOTEX_QUESTIONS rows pointing at those ids -> INSERT only when the
    question text is new; an existing question that maps elsewhere is kept
    as-is and reported (anchors are never stolen)
  * INOTEX_2026_PROGRAM_SYNONYMS -> INSERT only when the source word is new

Nothing else in the database is read-modified-written.

SAFETY
------
Dry-run by default: reports what would happen, writes nothing. --apply also
rebuilds the retrieval index (reindex_and_publish) so the new content is
served immediately. Works on the configured backend (SQLite or PostgreSQL);
--db overrides with an explicit SQLite file.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The search service imports the OpenAI client at module load; a placeholder
# key is enough for a content import (same pattern as import-content.py).
os.environ.setdefault("OPENAI_API_KEY", "import")

from app.config import DB_BACKEND  # noqa: E402
from app.default_content import (  # noqa: E402
    INOTEX_2026_PROGRAM_IDS,
    INOTEX_2026_PROGRAM_SYNONYMS,
    INOTEX_DATASET,
    INOTEX_QUESTIONS,
)

PROGRAM_ENTRIES = [item for item in INOTEX_DATASET if item["id"] in INOTEX_2026_PROGRAM_IDS]
PROGRAM_QUESTIONS = [(q, ds) for q, ds in INOTEX_QUESTIONS if ds in INOTEX_2026_PROGRAM_IDS]


def connect(args):
    if args.db:
        conn = sqlite3.connect(str(Path(args.db).resolve()))
        conn.row_factory = sqlite3.Row  # match the dict-style rows of get_db_connection
        return conn
    from app.db.connection import init_db, get_db_connection
    init_db()
    return get_db_connection()


def main() -> int:
    p = argparse.ArgumentParser(description="Import the crawled INOTEX 2026 program content.")
    p.add_argument("--apply", action="store_true",
                   help="actually write; without it everything is a dry-run")
    p.add_argument("--db", default="", help="explicit SQLite file (default: configured backend)")
    args = p.parse_args()

    missing = INOTEX_2026_PROGRAM_IDS - {item["id"] for item in PROGRAM_ENTRIES}
    if missing:
        sys.exit(f"manifest mismatch: ids listed but not defined in INOTEX_DATASET: {sorted(missing)}")

    conn = connect(args)
    backend = "SQLite (explicit)" if args.db else DB_BACKEND
    print(f"Backend  : {backend}")
    print(f"Entries  : {len(PROGRAM_ENTRIES)} dataset rows "
          f"({len(PROGRAM_QUESTIONS)} curated questions, {len(INOTEX_2026_PROGRAM_SYNONYMS)} synonyms)")

    existing_ds = {row["id"] for row in conn.execute("SELECT id FROM dataset").fetchall()}
    existing_q = {row["question"]: row["dataset_id"]
                  for row in conn.execute("SELECT question, dataset_id FROM questions").fetchall()}
    existing_syn = {row["source"] for row in conn.execute("SELECT source FROM synonyms").fetchall()}

    to_insert = [item for item in PROGRAM_ENTRIES if item["id"] not in existing_ds]
    to_update = [item for item in PROGRAM_ENTRIES if item["id"] in existing_ds]
    print(f" dataset : {len(to_insert)} new, {len(to_update)} update-in-place")
    for item in to_insert:
        print(f"   + {item['id']}: {item['title'][:60]}")
    for item in to_update:
        print(f"   ~ {item['id']}: {item['title'][:60]}")

    q_new, q_keep, q_steal = [], [], []
    for question, ds_id in PROGRAM_QUESTIONS:
        if question not in existing_q:
            q_new.append((question, ds_id))
        elif existing_q[question] == ds_id:
            q_keep.append(question)
        else:
            q_steal.append((question, existing_q[question], ds_id))
    print(f" questions: {len(q_new)} new, {len(q_keep)} already present, {len(q_steal)} owned by another entry")
    for question, owner, ds_id in q_steal:
        print(f"   ! {question!r} stays with {owner} (not remapped to {ds_id})")

    syn_new = [row for row in INOTEX_2026_PROGRAM_SYNONYMS if row[0] not in existing_syn]
    print(f" synonyms : {len(syn_new)} new of {len(INOTEX_2026_PROGRAM_SYNONYMS)}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to import.")
        conn.close()
        return 0

    max_pos = conn.execute("SELECT COALESCE(MAX(position), 0) FROM dataset").fetchone()[0]
    insert_ids = {item["id"] for item in to_insert}
    n_ds = 0
    for item in PROGRAM_ENTRIES:
        if item["id"] in insert_ids:
            max_pos += 10
            conn.execute(
                "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
                " VALUES (?, ?, ?, '', ?, ?, ?)",
                (item["id"], item["title"], item["text"],
                 item.get("title_en", ""), item.get("text_en", ""), max_pos))
        else:
            conn.execute(
                "UPDATE dataset SET title = ?, text = ?, title_en = ?, text_en = ?"
                " WHERE id = ?",
                (item["title"], item["text"], item.get("title_en", ""),
                 item.get("text_en", ""), item["id"]))
        n_ds += 1
    for question, ds_id in q_new:
        conn.execute("INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, '')",
                     (question, ds_id))
    for source, target in syn_new:
        conn.execute("INSERT INTO synonyms (source, target) VALUES (?, ?)", (source, target))
    conn.commit()
    conn.close()

    if not args.db:
        from app.services.search import reindex_and_publish
        reindex_and_publish()
        index_note = " Index rebuilt."
    else:
        index_note = " (Explicit SQLite file: restart the app to rebuild the index.)"
    print(f"\nAPPLIED: {len(PROGRAM_ENTRIES)} dataset rows, {len(q_new)} questions, "
          f"{len(syn_new)} synonyms.{index_note}")
    print("Next: .venv/bin/python scripts/run_eval.py   # re-baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
