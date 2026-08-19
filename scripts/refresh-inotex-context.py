#!/usr/bin/env python3
"""Automated INOTEX content-freshness checker (knowledge lifecycle, stage 1).

Reads the approved source manifest (content/sources.json), fetches every
official page, hashes the body, and compares it with the last stored snapshot:

    Discover → Fetch → Hash → Diff → Report

- A CHANGED page means the knowledge base may be stale: the operator (or CI)
  is told exactly which source moved, and the review queue gains an entry.
- Snapshots are stored under content/snapshots/ so the previous verified
  state is never lost (refresh failure keeps the last good version).
- This script never edits the database or app/default_content.py by itself:
  publication of new facts always passes through human approval
  (content/review-queue.md), which is a deliberate governance gate.

USAGE (from the project root):
    python3 scripts/refresh-inotex-context.py            # check all sources
    python3 scripts/refresh-inotex-context.py --json     # machine-readable
    python3 scripts/refresh-inotex-context.py --timeout 20

Exit codes: 0 = all sources unchanged/fresh, 2 = at least one source changed,
3 = at least one source unreachable (previous snapshot preserved).
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MANIFEST = BASE / "content" / "sources.json"
SNAP_DIR = BASE / "content" / "snapshots"
REPORT = BASE / "content" / "freshness-report.json"

UA = "PadyarKnowledgeRefresh/1.0 (+https://inotex.com contact: site operator)"


def _strip_volatile(html: str) -> str:
    """Remove trivially volatile fragments (CSRF tokens, nonces, timestamps)
    so a hash compares *content*, not per-request noise."""
    html = re.sub(r'name="csrf[^"]*"\s+value="[^"]*"', "", html, flags=re.I)
    html = re.sub(r'nonce="[^"]*"', "", html)
    html = re.sub(r"\?v=\d+", "", html)
    return html


def fetch(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    p = argparse.ArgumentParser(description="Check INOTEX official sources for changes.")
    p.add_argument("--json", action="store_true", help="Print a JSON report.")
    p.add_argument("--timeout", type=int, default=25)
    args = p.parse_args()

    if not MANIFEST.exists():
        sys.exit(f"Source manifest not found: {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    any_changed = False
    any_error = False

    for src in manifest.get("sources", []):
        sid, url = src["source_id"], src["url"]
        snap_file = SNAP_DIR / f"{sid}.html"
        hash_file = SNAP_DIR / f"{sid}.sha256"
        entry = {"source_id": sid, "url": url, "checked_at": now}
        try:
            body = fetch(url, args.timeout)
            digest = hashlib.sha256(
                _strip_volatile(body.decode("utf-8", "replace")).encode("utf-8")
            ).hexdigest()
            previous = hash_file.read_text().strip() if hash_file.exists() else ""
            if previous and previous == digest:
                entry["status"] = "unchanged"
            elif previous:
                entry["status"] = "changed"
                entry["previous_hash"] = previous[:16]
                any_changed = True
            else:
                entry["status"] = "first_snapshot"
            entry["hash"] = digest[:16]
            # Store the new snapshot only after a successful fetch — a failed
            # fetch must never clobber the last verified copy.
            snap_file.write_bytes(body)
            hash_file.write_text(digest)
        except Exception as e:  # noqa: BLE001 — report, keep last good snapshot
            entry["status"] = "unreachable"
            entry["error"] = f"{type(e).__name__}: {e}"
            any_error = True
        results.append(entry)

    report = {
        "generated_at": now,
        "knowledge_version": manifest.get("knowledge_version", ""),
        "results": results,
        "changed": any_changed,
        "errors": any_error,
        "next_step": (
            "Review changed sources, update app/default_content.py facts if needed, "
            "bump knowledge_version in content/sources.json, then reset content via "
            "scripts/reset-content-to-defaults.py."
            if any_changed else "No action needed."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"  {r['status']:<15} {r['source_id']:<15} {r['url']}")
        print(f"\nReport → {REPORT}")
        if any_changed:
            print("⚠ At least one official page changed — review content/review-queue.md")
    return 2 if any_changed else (3 if any_error else 0)


if __name__ == "__main__":
    sys.exit(main())
