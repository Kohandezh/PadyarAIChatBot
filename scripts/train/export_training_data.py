#!/usr/bin/env python3
"""Export the elecomp install's knowledge into training artifacts.

    cd /opt/padyar-elecomp && set -a && . .env && set +a && \
    SEED_DEFAULT_CONTENT=false .venv/bin/python \
        /home/gpu/train-work/scripts/export_training_data.py \
        --out /home/gpu/train-work/data [--talksiran /home/gpu/train-work/talksiran/talksiran.json]

Outputs (all under --out):
  entries.jsonl      dataset entries: {"id", "text"} (title + body)
  pairs_train.jsonl  {"query", "positive", "dataset_id"} — the training pairs
  holdout.jsonl      same shape, ~120 queries held OUT of pairs_train
  corpus.txt         every knowledge text (incl. talksiran crawl) for distillation
  events.jsonl       talksiran talks/panels/pitches for the guide-table import
  exhibitors.jsonl   talksiran exhibitor rows
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

INSTALL = os.environ.get("INSTALL_DIR", "/opt/padyar-elecomp")
sys.path.insert(0, INSTALL)
os.environ.setdefault("SEED_DEFAULT_CONTENT", "false")

from app.db.connection import get_db_connection  # noqa: E402

HOLDOUT_TARGET = 120
TITLE_COLS = ("title", "name", "headline")
BODY_COLS = ("text", "body", "about", "description", "summary", "content", "value")


def _table_text_columns(conn, table: str) -> list:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'app' AND table_name = ?"
        " ORDER BY ordinal_position", (table,)).fetchall()
    return [r["column_name"] for r in rows]


def _row_text(row: dict, cols: list) -> str:
    title = next((str(row[c]) for c in cols
                  if c in TITLE_COLS and row.get(c)), "")
    body = next((str(row[c]) for c in cols
                 if c in BODY_COLS and row.get(c)), "")
    parts = [p for p in (title, body) if p]
    return "\n".join(parts).strip()


def dump_table(conn, table: str) -> list:
    try:
        cols = _table_text_columns(conn, table)
    except Exception:
        return []
    if not cols:
        return []
    wanted = [c for c in cols if c in TITLE_COLS or c in BODY_COLS]
    if not wanted:
        return []
    rows = conn.execute(
        f"SELECT {', '.join(wanted)} FROM {table}").fetchall()
    return [_row_text(r, wanted) for r in rows if _row_text(r, wanted)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--talksiran", default="")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection()

    entries = {}
    for r in conn.execute("SELECT id, title, text FROM dataset").fetchall():
        text = "\n".join(p for p in (r["title"], r["text"]) if p).strip()
        if text:
            entries[r["id"]] = text

    # Company-anchored questions are the bulk of the corpus: companies are
    # their own table (migrations/0013), not dataset rows, but they are part
    # of the same retrieval surface the embedding model must serve.
    co_cols = _table_text_columns(conn, "companies")
    co_wanted = [c for c in co_cols
                 if c in ("id",) or c in TITLE_COLS or c in BODY_COLS]
    if "id" in co_cols:
        co_names = [c for c in co_cols if c in TITLE_COLS][:1] or []
        co_bodies = [c for c in co_cols if c in BODY_COLS][:1] or []
        sel = ["id"] + co_names + co_bodies
        for r in conn.execute(
                f"SELECT {', '.join(sel)} FROM companies").fetchall():
            name = r[co_names[0]] if co_names else ""
            body = r[co_bodies[0]] if co_bodies else ""
            text = "\n".join(p for p in (name, body) if p).strip()
            if text:
                entries[r["id"]] = text

    by_entry = {}
    for r in conn.execute(
            "SELECT question, dataset_id FROM questions").fetchall():
        did = r["dataset_id"]
        if did in entries and (r["question"] or "").strip():
            by_entry.setdefault(did, []).append(r["question"].strip())

    rng = random.Random(7)
    holdout, train = [], []
    for did, qs in sorted(by_entry.items()):
        uniq = list(dict.fromkeys(qs))
        if len(uniq) >= 4 and len(holdout) < HOLDOUT_TARGET:
            holdout.append({"query": uniq.pop(rng.randrange(len(uniq))),
                            "positive": entries[did], "dataset_id": did})
        for q in uniq:
            train.append({"query": q, "positive": entries[did], "dataset_id": did})

    corpus_parts = list(entries.values())
    for table in ("companies", "news", "restaurants", "stations", "guide_facts"):
        corpus_parts.extend(dump_table(conn, table))
    conn.close()

    events, exhibitors = [], []
    if args.talksiran and Path(args.talksiran).exists():
        data = json.loads(Path(args.talksiran).read_text(encoding="utf-8"))
        events = data.get("events", [])
        exhibitors = data.get("exhibitors", [])
        for e in events:
            corpus_parts.append("\n".join(
                p for p in (e.get("title"), e.get("description"),
                            e.get("detail_text")) if p))
        for x in exhibitors:
            corpus_parts.append("\n".join(
                p for p in (x.get("name"), x.get("field")) if p))

    with (out / "entries.jsonl").open("w", encoding="utf-8") as f:
        for did, text in entries.items():
            f.write(json.dumps({"id": did, "text": text},
                               ensure_ascii=False) + "\n")
    with (out / "pairs_train.jsonl").open("w", encoding="utf-8") as f:
        for p in train:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with (out / "holdout.jsonl").open("w", encoding="utf-8") as f:
        for p in holdout:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with (out / "corpus.txt").open("w", encoding="utf-8") as f:
        f.write("\n".join(corpus_parts))
    with (out / "events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with (out / "exhibitors.jsonl").open("w", encoding="utf-8") as f:
        for x in exhibitors:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    print(f"entries: {len(entries)}")
    print(f"train pairs: {len(train)}  holdout pairs: {len(holdout)}")
    print(f"corpus lines: {len(corpus_parts)}")
    print(f"talksiran: {len(events)} events, {len(exhibitors)} exhibitors")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
