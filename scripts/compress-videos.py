#!/usr/bin/env python3
"""Shrink the oversized booth clips to the size the rest of the library already is.

WHY THIS EXISTS. The 2026 INOTEX batch arrived in two shapes. The FAQ answers
are 1920x1080 at ~1.5 Mbps and weigh 6-8 MB. The booth clips are the SAME
1920x1080 at ~26 Mbps and weigh up to 202 MB. It is not that the booth clips are
longer; they are near-raw exports at roughly 17x the bitrate of every other file
in the same folder. 36 of 195 files carry 5.1 GB of the 6.5 GB total.

That matters at a booth. A visitor on exhibition wifi waits for the whole clip
before it starts, and 200 MB over shared wifi is not a wait anyone sits through.

So this re-encodes to the profile the FAQ clips already prove is good enough,
and leaves everything at or under the threshold untouched.

    python scripts/compress-videos.py ~/Desktop/FAQ

By default it writes to `<dir>/compressed/` and NEVER touches an original: a
re-encode that goes wrong is a corrupt file, and a corrupt file that overwrote
its source is unrecoverable. Use --in-place only after you have looked at the
output.

Every output is verified before it is accepted (see `_verify`). A broken encode
is usually SMALL, so "it got smaller" is not evidence that it worked.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# The house profile, read off the FAQ clips rather than picked from a blog post.
# CRF 23 is constant-quality: simple footage spends fewer bits than busy
# footage, which a fixed bitrate cannot do. The maxrate/bufsize pair caps the
# peak so one busy clip cannot come back at 40 MB anyway.
CRF = "23"
MAXRATE = "2500k"
BUFSIZE = "5000k"
PRESET = "medium"
AUDIO_BITRATE = "128k"

# Frames wider/taller than this are scaled down; smaller ones are left alone.
# -2 keeps the aspect ratio and rounds to an even number, which H.264 requires.
MAX_HEIGHT = 1080

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

# An output whose duration differs from its source by more than this is a
# truncated encode, not a compressed one.
DURATION_TOLERANCE_S = 1.0


def _probe(path: Path) -> dict:
    """Duration, size and stream count, or {} when ffprobe cannot read it."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size", "-show_entries", "stream=codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            return {}
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration": duration,
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _verify(src_info: dict, dst: Path) -> str:
    """Empty string when the output is usable, else why it is not.

    A failed encode is normally a SHORT file, so it passes a size check and
    fails a duration check. That is the whole reason this function compares
    durations rather than trusting the exit code.
    """
    if not dst.exists() or dst.stat().st_size == 0:
        return "output is missing or empty"
    dst_info = _probe(dst)
    if not dst_info:
        return "output is not readable by ffprobe"
    if not dst_info["has_video"]:
        return "output has no video stream"
    if src_info["has_audio"] and not dst_info["has_audio"]:
        return "source had audio and the output does not"
    delta = abs(dst_info["duration"] - src_info["duration"])
    if src_info["duration"] and delta > DURATION_TOLERANCE_S:
        return (f"duration changed by {delta:.1f}s "
                f"({src_info['duration']:.1f} -> {dst_info['duration']:.1f})")
    return ""


def compress(src: Path, dst: Path) -> dict:
    """Re-encode one file. Returns a row for the report; never raises."""
    row = {"name": src.name, "before_mb": src.stat().st_size / 1e6,
           "after_mb": 0.0, "status": "", "detail": ""}

    src_info = _probe(src)
    if not src_info or not src_info["has_video"]:
        row["status"] = "skipped"
        row["detail"] = "not a readable video"
        return row

    # The temp name KEEPS the real extension. ffmpeg picks its muxer from
    # the output extension, so "clip.mp4.part" fails with a bare
    # "Invalid argument" that looks like a permissions problem.
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
        "-maxrate", MAXRATE, "-bufsize", BUFSIZE,
        # Only ever scale DOWN. min() keeps an already-small frame untouched
        # instead of upscaling it into a bigger file than it started as.
        "-vf", f"scale=-2:'min({MAX_HEIGHT},ih)'",
        "-pix_fmt", "yuv420p",
        # Puts the index at the front so the player can start on the first
        # bytes instead of waiting for the whole download.
        "-movflags", "+faststart",
    ]
    cmd += (["-c:a", "aac", "-b:a", AUDIO_BITRATE] if src_info["has_audio"]
            else ["-an"])
    cmd += [str(tmp)]

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.SubprocessError as e:
        tmp.unlink(missing_ok=True)
        row["status"] = "failed"
        row["detail"] = f"ffmpeg did not run: {e}"
        return row

    if out.returncode != 0:
        tmp.unlink(missing_ok=True)
        row["status"] = "failed"
        row["detail"] = (out.stderr or "").strip().splitlines()[-1:] or "ffmpeg failed"
        row["detail"] = row["detail"][0] if isinstance(row["detail"], list) else row["detail"]
        return row

    problem = _verify(src_info, tmp)
    if problem:
        tmp.unlink(missing_ok=True)
        row["status"] = "failed"
        row["detail"] = problem
        return row

    after = tmp.stat().st_size / 1e6
    if after >= row["before_mb"]:
        # Already efficient. Keeping the bigger re-encode would be a loss on
        # both size and quality, so the original wins.
        tmp.unlink(missing_ok=True)
        row["status"] = "kept-original"
        row["detail"] = "re-encode was not smaller"
        return row

    tmp.replace(dst)
    row["after_mb"] = after
    row["status"] = "ok"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", type=Path, help="folder holding the videos")
    ap.add_argument("--threshold-mb", type=float, default=30.0,
                    help="only touch files larger than this (default: 30)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output folder (default: <directory>/compressed)")
    ap.add_argument("--in-place", action="store_true",
                    help="replace the originals once each output verifies")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 3),
                    help="files to encode at once (ffmpeg is already threaded)")
    args = ap.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg and ffprobe must be on PATH (brew install ffmpeg)",
              file=sys.stderr)
        return 2

    src_dir = args.directory.expanduser().resolve()
    if not src_dir.is_dir():
        print(f"not a directory: {src_dir}", file=sys.stderr)
        return 2

    out_dir = (args.out.expanduser().resolve() if args.out
               else src_dir / "compressed")
    out_dir.mkdir(parents=True, exist_ok=True)

    limit = args.threshold_mb * 1e6
    targets = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
        and p.stat().st_size > limit)

    if not targets:
        print(f"nothing over {args.threshold_mb:g} MB in {src_dir}")
        return 0

    total_before = sum(p.stat().st_size for p in targets) / 1e6
    print(f"{len(targets)} file(s) over {args.threshold_mb:g} MB, "
          f"{total_before / 1000:.1f} GB total")
    print(f"writing to {out_dir}")
    print(f"profile: h264 crf={CRF} maxrate={MAXRATE} max-height={MAX_HEIGHT} "
          f"+faststart, {args.jobs} at a time\n", flush=True)

    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(compress, p, out_dir / p.name) for p in targets]
        # as_completed, so a slow file does not hold up the report for the
        # ones that already finished.
        for n, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            rows.append(row)
            if row["status"] == "ok":
                saved = 100 * (1 - row["after_mb"] / row["before_mb"])
                print(f"[{n}/{len(targets)}] {row['name']}: "
                      f"{row['before_mb']:.0f} -> {row['after_mb']:.0f} MB "
                      f"({saved:.0f}% smaller)", flush=True)
            else:
                print(f"[{n}/{len(targets)}] {row['name']}: "
                      f"{row['status'].upper()} — {row['detail']}", flush=True)

    ok = [r for r in rows if r["status"] == "ok"]
    bad = [r for r in rows if r["status"] == "failed"]
    after_total = sum(r["after_mb"] for r in ok)
    before_ok = sum(r["before_mb"] for r in ok)

    print(f"\n{len(ok)} compressed, {len(bad)} failed, "
          f"{len(rows) - len(ok) - len(bad)} left as-is")
    if ok:
        print(f"{before_ok / 1000:.2f} GB -> {after_total / 1000:.2f} GB "
              f"({100 * (1 - after_total / before_ok):.0f}% smaller)")
    for r in bad:
        print(f"  FAILED {r['name']}: {r['detail']}")

    if args.in_place and ok:
        # Only after every output verified. A partial swap is worse than none:
        # half the folder would be re-encoded and half not, with no record of
        # which is which.
        if bad:
            print("\nNOT replacing originals: some files failed. "
                  "Fix those first, then re-run with --in-place.")
            return 1
        for r in ok:
            shutil.move(str(out_dir / r["name"]), str(src_dir / r["name"]))
        out_dir.rmdir()
        print(f"\nreplaced {len(ok)} original(s) in {src_dir}")

    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
