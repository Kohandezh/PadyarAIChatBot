"""Load-test the Persian TTS engine with VARIED sentences.

Differs from stress_test.py in the two ways this run needs:
  * every request carries a DIFFERENT sentence, split out of a real article,
    instead of one base text with a counter glued on the end;
  * every request is recorded individually (characters, latency, cache
    hit/miss), so latency can be regressed against sentence length afterwards.

The engine generates one clip at a time (max_workers=1), so concurrency only
deepens the queue. Sizing is therefore driven by the MISS count: misses are
serialised, hits are nearly free.
"""
import argparse
import json
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TTS_URL = "http://127.0.0.1:8003/tts"
ENDERS = re.compile(r"[.!?؟؛\n]+")   # . ! ? ؟ ؛ newline


def split_sentences(path, min_chars=20):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    out, seen = [], set()
    for piece in ENDERS.split(raw):
        s = " ".join(piece.split())
        if len(s) >= min_chars and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def say(text, timeout, phase):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        TTS_URL, data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            payload = res.read()
            hdr = {k.lower(): v for k, v in res.headers.items()}
            return {"ok": True, "phase": phase, "chars": len(text),
                    "seconds": time.perf_counter() - started,
                    "bytes": len(payload), "cache": hdr.get("x-tts-cache", "?")}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "phase": phase, "chars": len(text),
                "seconds": time.perf_counter() - started,
                "cache": "-", "error": f"http {exc.code}"}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "phase": phase, "chars": len(text),
                "seconds": time.perf_counter() - started,
                "cache": "-", "error": type(exc).__name__}


def pct(values):
    if not values:
        return {}
    s = sorted(values)
    pick = lambda p: s[min(len(s) - 1, int(len(s) * p))]        # noqa: E731
    return {"min": s[0], "p50": pick(.50), "p90": pick(.90),
            "p99": pick(.99), "max": s[-1], "mean": statistics.fmean(s)}


def run(phase, texts, concurrency, timeout, sink):
    print(f"\n=== {phase}: {len(texts)} requests, concurrency {concurrency} ===",
          flush=True)
    got = []
    done = threading.Event()

    def tick():
        while not done.wait(15):
            n = len(got)
            hits = sum(1 for r in got if r.get("cache") == "hit")
            print(f"    ... {n}/{len(texts)}  hits={hits}", flush=True)

    threading.Thread(target=tick, daemon=True).start()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for r in pool.map(lambda x: say(x, timeout, phase), texts):
            got.append(r)
    elapsed = time.perf_counter() - t0
    done.set()
    sink.extend(got)

    ok = [r for r in got if r["ok"]]
    bad = [r for r in got if not r["ok"]]
    hits = [r for r in ok if r["cache"] == "hit"]
    miss = [r for r in ok if r["cache"] == "miss"]
    lat = pct([r["seconds"] for r in ok])
    print(f"  wall            : {elapsed:.1f}s")
    print(f"  ok / failed     : {len(ok)} / {len(bad)}")
    print(f"  hit / miss      : {len(hits)} / {len(miss)}")
    if ok:
        print(f"  throughput      : {len(ok)/elapsed:.2f} req/s")
        print("  latency (s)     : " + "  ".join(f"{k}={v:.3f}" for k, v in lat.items()))
    if hits:
        print("  hit latency     : " + "  ".join(f"{k}={v:.3f}" for k, v in pct([r['seconds'] for r in hits]).items()))
    if miss:
        print("  miss latency    : " + "  ".join(f"{k}={v:.3f}" for k, v in pct([r['seconds'] for r in miss]).items()))
    if bad:
        kinds = {}
        for r in bad:
            kinds[r.get("error", "?")] = kinds.get(r.get("error", "?"), 0) + 1
        print(f"  failures        : {kinds}")
    return {"phase": phase, "requests": len(texts), "concurrency": concurrency,
            "wall": elapsed, "ok": len(ok), "failed": len(bad),
            "hits": len(hits), "misses": len(miss),
            "throughput": len(ok) / elapsed if ok else 0, "latency": lat,
            "hit_latency": pct([r["seconds"] for r in hits]),
            "miss_latency": pct([r["seconds"] for r in miss])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="/tmp/stress-corpus-fa.txt")
    ap.add_argument("--cached", default="/tmp/inotex_texts.json")
    ap.add_argument("--calibrate", type=int, default=0,
                    help="run N sentences serially to time chars->seconds, then stop")
    ap.add_argument("--mix", type=int, default=0, help="realistic-mix requests")
    ap.add_argument("--mix-conc", type=int, default=20)
    ap.add_argument("--mix-fresh", type=int, default=0,
                    help="how many of the mix are never-rendered sentences")
    ap.add_argument("--burst", type=int, default=0, help="pure-miss burst size")
    ap.add_argument("--burst-conc", type=int, default=8)
    ap.add_argument("--rewarm", action="store_true",
                    help="replay the mix's fresh sentences; they must all be hits now")
    ap.add_argument("--out", default="/tmp/tts-varied-result.json")
    args = ap.parse_args()

    sentences = split_sentences(args.corpus)
    with open(args.cached, encoding="utf-8") as fh:
        prerendered = [t for t in json.load(fh) if t and t.strip()]
    lens = [len(s) for s in sentences]
    print(f"corpus     : {len(sentences)} usable sentences "
          f"(min={min(lens)} p50={int(statistics.median(lens))} "
          f"max={max(lens)} mean={statistics.fmean(lens):.0f} chars)")
    print(f"prerendered: {len(prerendered)} curated answers")

    every = []
    report = {"sentences": len(sentences), "sentence_chars": lens, "phases": []}

    if args.calibrate:
        picks = sorted(sentences, key=len)
        step = max(1, len(picks) // args.calibrate)
        chosen = picks[::step][:args.calibrate]
        stamp = int(time.time())
        texts = [f"{s} مورد {stamp+i}" for i, s in enumerate(chosen)]
        report["phases"].append(run("CALIBRATE", texts, 1, 900, every))
        json.dump({"report": report, "requests": every},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\nwrote {args.out}")
        for r in every:
            print(f"   {r['chars']:4d} chars -> {r['seconds']:7.2f}s  "
                  f"{r['bytes']/1024:8.0f} KiB  {r['cache']}")
        return

    fresh_used = []
    if args.mix:
        n_fresh = min(args.mix_fresh, len(sentences))
        fresh = sentences[:n_fresh]
        fresh_used = list(fresh)
        # Hits come from the curated answers plus sentences of this same
        # article that an earlier visitor already caused to be rendered.
        hitpool = prerendered + sentences[n_fresh:]
        seq, fi, hi = [], 0, 0
        # Spread the fresh ones evenly through the stream instead of front-
        # loading them: a real hall trickles new questions in among repeats.
        gap = args.mix / max(1, n_fresh)
        nxt = gap / 2
        for i in range(args.mix):
            if fi < n_fresh and i >= nxt:
                seq.append(fresh[fi]); fi += 1; nxt += gap
            else:
                seq.append(hitpool[hi % len(hitpool)]); hi += 1
        report["phases"].append(
            run("MIX (realistic)", seq, args.mix_conc, 900, every))

    if args.rewarm and fresh_used:
        report["phases"].append(
            run("REWARM (same fresh sentences again)", fresh_used,
                args.mix_conc, 300, every))

    if args.burst:
        pool = sentences[::-1]
        stamp = int(time.time())
        texts = [f"{pool[i % len(pool)]} شماره {stamp+i}"
                 for i in range(args.burst)]
        report["phases"].append(
            run("BURST (pure miss)", texts, args.burst_conc, 1800, every))

    ok = [r for r in every if r["ok"]]
    miss = [(r["chars"], r["seconds"]) for r in ok if r["cache"] == "miss"]
    if len(miss) > 2:
        xs = [m[0] for m in miss]; ys = [m[1] for m in miss]
        try:
            r = statistics.correlation(xs, ys)
            slope, icept = statistics.linear_regression(xs, ys)
            print(f"\nlength vs latency on MISSES (n={len(miss)}): "
                  f"pearson r={r:.3f}, {slope*100:.2f}s per 100 chars, "
                  f"intercept {icept:.1f}s")
            report["length_vs_latency"] = {
                "n": len(miss), "pearson_r": r, "seconds_per_100_chars": slope * 100,
                "intercept": icept}
        except Exception as exc:                                # noqa: BLE001
            print(f"regression failed: {exc}")

    json.dump({"report": report, "requests": every},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
