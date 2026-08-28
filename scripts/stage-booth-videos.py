#!/usr/bin/env python3
"""Copy the booth videos a workbook actually references into media/videos.

    .venv/bin/python scripts/stage-booth-videos.py --companies Y.xlsx \
        --source ~/Desktop/FAQ
    .venv/bin/python scripts/stage-booth-videos.py ... --apply   # copies

The delivered video folder holds more files than the exhibition needs (extra
FAQ clips, a booth number with no company). This stages ONLY the files the
workbook maps to a company, so the rsync to the server carries what is used
and nothing else.

WHY IT DOES NOT RENAME
----------------------
One delivered file is spelled ghorfe88.mp4 with no hyphen. Renaming it would
be tidier, but scripts/import-content.py stores the file's OWN name in
dataset.video_url, so a rename here and an import there would disagree and
the player would 404. Copy under the source name, flag the odd spelling in
the manifest, and the two scripts can never drift apart.

SAFETY
------
Dry-run by default. --apply copies. It never deletes and never overwrites a
file that is already there with the same size, so re-running it is free.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_BACKEND", "sqlite")

import importlib.util  # noqa: E402

# scripts/import-content.py has a hyphen in its name, so it cannot be a normal
# import. Loading it by path keeps ONE definition of the workbook layout and
# the ghorfe-<n>.mp4 pattern — two copies would drift.
_spec = importlib.util.spec_from_file_location(
    "_import_content", ROOT / "scripts" / "import-content.py")
_import_content = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_import_content)

load_companies = _import_content.load_companies
scan_videos = _import_content.scan_videos
VIDEO_RE = _import_content.VIDEO_RE


def plan(companies_xlsx: str, source_dir: str):
    """[(filename, source_path, reason)] for every file that must be staged."""
    videos = scan_videos(source_dir)
    if videos is None:
        sys.exit(f"source directory not found: {source_dir}")
    companies, errors, report = load_companies(companies_xlsx, videos)
    if not report["has_column"]:
        sys.exit("that workbook has no booth-video-number column — "
                 "nothing to stage")

    wanted = []
    for _cid, ds, _profile, _anchors in companies:
        url = ds["video_url"]
        if not url:
            continue
        name = url.rsplit("/", 1)[-1]
        wanted.append((name, os.path.join(source_dir, name), ds["title"]))
    return wanted, errors, report


def main() -> int:
    p = argparse.ArgumentParser(description="Stage booth videos for deployment.")
    p.add_argument("--companies", required=True,
                   help="exhibitor workbook with the booth-video-number column")
    p.add_argument("--source", required=True,
                   help="directory holding the delivered ghorfe-*.mp4 files")
    p.add_argument("--target", default="media/videos",
                   help="where to stage them (default: media/videos)")
    p.add_argument("--apply", action="store_true",
                   help="actually copy; without it everything is a dry-run")
    args = p.parse_args()

    wanted, errors, report = plan(args.companies, args.source)
    target = Path(args.target)

    copied = skipped = replaced = 0
    total_bytes = 0
    print(f"Videos referenced by the workbook: {len(wanted)}")
    for name, src, title in wanted:
        size = os.path.getsize(src)
        dst = target / name
        if dst.exists() and dst.stat().st_size == size:
            skipped += 1
            continue
        # Present but a different size: the copy is incomplete or the file was
        # re-cut. Copy again — still no delete, shutil.copy2 overwrites in place.
        action = "REPLACE" if dst.exists() else "COPY   "
        if dst.exists():
            replaced += 1
        else:
            copied += 1
        total_bytes += size
        print(f"   {action} {name}  ({size / 1e6:.1f} MB)  ← {title[:40]}")
        if args.apply:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    odd = [n for n, _s, _t in wanted if not n.startswith("ghorfe-")]
    if odd:
        print(f"Non-standard filenames:  {len(odd)} — {odd}")
        print("   (kept as-is on purpose; see the module docstring)")
    for w in report["warnings"]:
        print(f"   WARNING {w}")
    for n in report["orphans"]:
        print(f"   WARNING booth video {n} ({report['files'][n]}) "
              f"matches no company — not staged")
    for e in errors:
        print(f"   ERROR {e}")

    print(f"\nTo copy: {copied} new + {replaced} replaced "
          f"({total_bytes / 1e9:.2f} GB). Already staged: {skipped}.")
    if not args.apply:
        print("DRY RUN — nothing copied. Re-run with --apply to stage.")
    else:
        print(f"STAGED into {target}. Nothing was deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
