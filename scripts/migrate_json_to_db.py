#!/usr/bin/env python3
"""One-time migration: move legacy data/*.json content into the SQLite DB.

Older installs read the knowledge base from ``data/dataset.json`` and
``data/questions.json``. The current version uses ``chat_history.db`` as the
single source of truth and no longer reads those files. On upgrade, ``git pull``
*deletes* those tracked JSON files before the new app ever boots — so the
content must be captured BEFORE the pull, then imported into the DB AFTER it.

This one script handles both halves and figures out which to do:

    BEFORE `git pull` (old code, JSON still on disk):
        python scripts/migrate_json_to_db.py        # -> 'capture'
        # copies data/*.json (and a safety copy of the DB) into
        # migration_snapshot/, which is gitignored so the pull can't touch it.

    deploy:
        git pull
        pip install -r requirements.txt

    AFTER `git pull` (new code, JSON gone, snapshot survives):
        python scripts/migrate_json_to_db.py        # -> 'import'
        # backs up the live DB, then writes the snapshot content into it.

With no argument it auto-detects the phase. You can also force one:
    python scripts/migrate_json_to_db.py capture
    python scripts/migrate_json_to_db.py import [--force]
    python scripts/migrate_json_to_db.py import --dataset X.json --questions Y.json

Safe by design:
  * capture never imports the app (works on the OLD environment, pure stdlib).
  * import takes a backup of the live DB first, and refuses to overwrite a DB
    that already holds dataset rows unless --force is given.
"""
import os
import sys
import json
import shutil
import sqlite3
import argparse

# Make the project root importable when run as `python scripts/migrate_json_to_db.py`
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
SNAPSHOT_DIR = os.path.join(ROOT, "migration_snapshot")
DB_PATH = os.path.join(ROOT, "chat_history.db")


# --------------------------------------------------------------------------- #
# capture phase — pure stdlib, runs on the OLD install before `git pull`
# --------------------------------------------------------------------------- #
def _sqlite_safe_copy(src: str, dest: str):
    """Copy a (possibly live, WAL-mode) SQLite DB consistently via the backup API."""
    s = sqlite3.connect(src)
    s.execute("PRAGMA busy_timeout=5000")
    d = sqlite3.connect(dest)
    try:
        with d:
            s.backup(d)
    finally:
        d.close()
        s.close()


def capture(dataset_path: str, questions_path: str):
    found = [p for p in (dataset_path, questions_path) if os.path.isfile(p)]
    if not found:
        sys.exit(
            "✗ Nothing to capture — no JSON found at:\n"
            f"    {dataset_path}\n    {questions_path}\n"
            "  (Run this BEFORE `git pull`, while the legacy data/*.json still exists.)"
        )
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    for p in (dataset_path, questions_path):
        if os.path.isfile(p):
            dest = os.path.join(SNAPSHOT_DIR, os.path.basename(p))
            shutil.copy2(p, dest)
            print(f"→ captured {p}  →  {dest}")
        else:
            print(f"  · note: {p} not found, skipping")
    # Bonus safety: a consistent copy of the current DB alongside the snapshot.
    if os.path.exists(DB_PATH):
        try:
            _sqlite_safe_copy(DB_PATH, os.path.join(SNAPSHOT_DIR, "chat_history.pre_migration.db"))
            print(f"→ safety DB copy  →  {os.path.join(SNAPSHOT_DIR, 'chat_history.pre_migration.db')}")
        except Exception as e:
            print(f"  · could not copy the DB ({e}); continuing")
    print(
        "\n✓ Snapshot saved to migration_snapshot/ (gitignored — survives git pull).\n"
        "  Next:\n"
        "    git pull\n"
        "    pip install -r requirements.txt\n"
        "    python scripts/migrate_json_to_db.py     # imports the snapshot into the DB"
    )


# --------------------------------------------------------------------------- #
# import phase — uses the NEW app code, runs after `git pull` + pip install
# --------------------------------------------------------------------------- #
def _load_json_array(path: str, label: str) -> list:
    if not os.path.isfile(path):
        sys.exit(f"✗ {label} file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"✗ Could not read {label} ({path}): {e}")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        sys.exit(f"✗ {label} must be a JSON array of objects: {path}")
    return [d for d in data if isinstance(d, dict)]


def _clean_dataset(rows: list) -> list:
    out, skipped = [], 0
    for r in rows:
        item_id = str(r.get("id") or "").strip()
        title = str(r.get("title") or "").strip()
        text = str(r.get("text") or "").strip()
        video_url = str(r.get("video_url") or "").strip()
        if not item_id or not title or not text:
            skipped += 1
            continue
        out.append({"id": item_id, "title": title, "text": text, "video_url": video_url})
    if skipped:
        print(f"  · skipped {skipped} dataset row(s) missing id/title/text")
    return list({d["id"]: d for d in out}.values())  # de-dupe by id, last wins


def _clean_questions(rows: list) -> list:
    out, skipped = [], 0
    for r in rows:
        question = str(r.get("question") or "").strip()
        dataset_id = str(r.get("dataset_id") or "").strip()
        video_url = str(r.get("video_url") or "").strip()
        if not question or not dataset_id:
            skipped += 1
            continue
        out.append({"question": question, "dataset_id": dataset_id, "video_url": video_url})
    if skipped:
        print(f"  · skipped {skipped} question row(s) missing question/dataset_id")
    return [{"id": i, **q} for i, q in enumerate(out, start=1)]  # sequential int ids


def _resolve_import_sources(dataset_arg, questions_arg):
    """Pick where to read from: explicit args > migration_snapshot/ > data/."""
    if dataset_arg or questions_arg:
        return (dataset_arg or os.path.join(DATA_DIR, "dataset.json"),
                questions_arg or os.path.join(DATA_DIR, "questions.json"))
    snap_d = os.path.join(SNAPSHOT_DIR, "dataset.json")
    snap_q = os.path.join(SNAPSHOT_DIR, "questions.json")
    if os.path.isfile(snap_d):
        return snap_d, snap_q
    return os.path.join(DATA_DIR, "dataset.json"), os.path.join(DATA_DIR, "questions.json")


def do_import(dataset_arg, questions_arg, force: bool):
    dataset_path, questions_path = _resolve_import_sources(dataset_arg, questions_arg)

    # Imported here (not at top) so the capture phase never needs the app/deps,
    # and a config error gives a clear message.
    from app.db.connection import init_db, get_db_connection
    from app.db.queries import save_dataset, save_questions

    print("→ Ensuring database schema exists (non-destructive)…")
    init_db()

    conn = get_db_connection()
    existing = conn.execute("SELECT COUNT(*) AS c FROM dataset").fetchone()["c"]
    conn.close()
    if existing and not force:
        sys.exit(
            f"✗ The database already has {existing} dataset row(s).\n"
            "  Refusing to overwrite. Re-run with --force to replace it (a backup is taken first)."
        )

    try:
        from backup_db import create_backup
        if os.path.exists(DB_PATH):
            print(f"→ Safety backup written: {create_backup()}")
    except Exception as e:
        print(f"  · could not take a safety backup ({e}); continuing")

    print(f"→ Reading dataset:   {dataset_path}")
    dataset = _clean_dataset(_load_json_array(dataset_path, "dataset"))
    print(f"→ Reading questions: {questions_path}")
    questions = _clean_questions(_load_json_array(questions_path, "questions"))

    if not dataset:
        sys.exit("✗ No valid dataset rows found — aborting (nothing imported).")

    valid_ids = {d["id"] for d in dataset}
    orphans = sorted({q["dataset_id"] for q in questions if q["dataset_id"] not in valid_ids})
    if orphans:
        print(f"  · warning: {len(orphans)} question dataset_id(s) have no matching dataset entry: "
              f"{', '.join(orphans[:10])}{'…' if len(orphans) > 10 else ''}")

    print(f"→ Importing {len(dataset)} dataset entries and {len(questions)} questions…")
    save_dataset(dataset)
    save_questions(questions)

    conn = get_db_connection()
    d = conn.execute("SELECT COUNT(*) AS c FROM dataset").fetchone()["c"]
    q = conn.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
    conn.close()
    print(f"✓ Done. Database now holds {d} dataset entries and {q} questions. Restart the app.")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Migrate legacy JSON content into the SQLite DB.")
    ap.add_argument("phase", nargs="?", choices=["capture", "import"],
                    help="Force a phase. Omit to auto-detect (JSON present -> capture, else import).")
    ap.add_argument("--dataset", help="Path to dataset.json (import only; overrides auto-detection).")
    ap.add_argument("--questions", help="Path to questions.json (import only; overrides auto-detection).")
    ap.add_argument("--force", action="store_true", help="Overwrite even if the DB already has dataset rows.")
    args = ap.parse_args()

    default_ds = os.path.join(DATA_DIR, "dataset.json")
    default_qs = os.path.join(DATA_DIR, "questions.json")

    phase = args.phase
    if phase is None:
        # Auto: if the legacy JSON is still on disk we're pre-pull -> capture it.
        # Otherwise assume the snapshot exists and we're post-pull -> import.
        phase = "capture" if (os.path.isfile(default_ds) or os.path.isfile(default_qs)) else "import"
        print(f"(auto-detected phase: {phase})")

    if phase == "capture":
        capture(args.dataset or default_ds, args.questions or default_qs)
    else:
        do_import(args.dataset, args.questions, args.force)


if __name__ == "__main__":
    main()
