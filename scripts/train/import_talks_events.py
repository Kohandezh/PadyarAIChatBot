#!/usr/bin/env python3
"""Load the talksiran crawl into app.talks_events (elecomp install).

    cd /opt/padyar-elecomp && set -a && . .env && set +a && \
    SEED_DEFAULT_CONTENT=false .venv/bin/python \
        /home/gpu/train-work/scripts/import_talks_events.py \
        --input /home/gpu/train-work/talksiran/talksiran.json [--apply]

Upserts by source_id, so re-running after a fresh crawl refreshes rows in
place. The guide tier reads this table on every query — there is nothing to
reindex. Dry-run by default.
"""
import argparse
import json
import os
import sys

INSTALL = os.environ.get("INSTALL_DIR", "/opt/padyar-elecomp")
sys.path.insert(0, INSTALL)
os.environ.setdefault("SEED_DEFAULT_CONTENT", "false")

from app.db.connection import get_db_connection  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    data = json.loads(open(args.input, encoding="utf-8").read())
    events = data.get("events", [])
    rows = []
    for e in events:
        desc = e.get("detail_text") or e.get("description") or ""
        rows.append((int(e["source_id"]), (e.get("etype") or "")[:16],
                     (e.get("title") or "").strip(),
                     desc.strip(), (e.get("jdate") or "").strip(),
                     (e.get("start_time") or "").strip(),
                     (e.get("hall") or "").strip(),
                     (e.get("members") or "").strip(),
                     (e.get("url") or "").strip()))

    print(f"events in crawl: {len(rows)}")
    for r in rows[:5]:
        print(f"  [{r[0]}] {r[4]} {r[5]} {r[1]}: {r[2][:60]}")
    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
        return 0

    conn = get_db_connection()
    for r in rows:
        conn.execute(
            "INSERT INTO talks_events (source_id, etype, title, description,"
            " jdate, start_time, hall, members, url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (source_id) DO UPDATE SET"
            " etype = excluded.etype, title = excluded.title,"
            " description = excluded.description, jdate = excluded.jdate,"
            " start_time = excluded.start_time, hall = excluded.hall,"
            " members = excluded.members, url = excluded.url", r)
    conn.commit()
    n = conn.execute("SELECT count(*) AS n FROM talks_events").fetchone()["n"]
    conn.close()
    print(f"APPLIED: talks_events now holds {n} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
