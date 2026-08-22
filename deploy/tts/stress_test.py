"""Load-test the TTS engine and record what the machine does while it happens.

WHY THIS IS NOT "1000 CONCURRENT GENERATIONS"
---------------------------------------------
The engine generates ONE clip at a time on purpose (max_workers=1 in
server.py): the model is autoregressive, and two concurrent generations on a
P40 do not halve latency, they double both. So concurrency cannot raise
throughput here — it only lengthens the queue. At the measured ~1.7 real-time
factor a medium answer takes ~33s, which puts 1000 fresh generations at about
nine hours. That number is worth knowing, but running it teaches nothing.

So this measures the two things that actually decide whether the exhibition
survives:

  PHASE 1 — CACHE HITS. What 1000 visitors really produce. Every curated answer
  is pre-rendered, so a visitor asking a known question gets a file read, not a
  generation. This is the number that matters, and it should be enormous.

  PHASE 2 — SATURATION. Requests for text nobody has rendered before, arriving
  faster than one-at-a-time generation can serve them. Shows how deep the queue
  gets, how latency grows with queue position, and whether anything times out
  rather than waiting. This is the failure mode to design against.

Usage (on the server):
    python3 stress_test.py --phase1 1000 --concurrency 50 --phase2 24 --conc2 8
"""
import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TTS_URL = "http://127.0.0.1:8003/tts"


def say(text: str, timeout: float = 300.0) -> dict:
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        TTS_URL, data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            payload = res.read()
            # uvicorn lowercases header names.
            cache = res.headers.get("x-tts-cache") or res.headers.get("X-TTS-Cache") or "?"
            return {"ok": True, "seconds": time.perf_counter() - started,
                    "bytes": len(payload), "cache": cache}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "seconds": time.perf_counter() - started,
                "error": f"http {exc.code}"}
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "seconds": time.perf_counter() - started,
                "error": type(exc).__name__}


def percentiles(values):
    if not values:
        return {}
    s = sorted(values)
    def pick(p):
        return s[min(len(s) - 1, int(len(s) * p))]
    return {"min": s[0], "p50": pick(0.50), "p90": pick(0.90),
            "p99": pick(0.99), "max": s[-1],
            "mean": statistics.fmean(s)}


def run_phase(name, texts, concurrency, timeout):
    print(f"\n=== {name} — {len(texts)} request, concurrency {concurrency} ===",
          flush=True)
    results = []
    done = threading.Event()

    def progress():
        while not done.wait(5):
            n = len(results)
            print(f"    ... {n}/{len(texts)}", flush=True)

    t = threading.Thread(target=progress, daemon=True)
    t.start()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for r in pool.map(lambda x: say(x, timeout), texts):
            results.append(r)
    elapsed = time.perf_counter() - started
    done.set()

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    hits = sum(1 for r in ok if r["cache"] == "hit")
    lat = percentiles([r["seconds"] for r in ok])

    print(f"  total wall      : {elapsed:.1f}s")
    print(f"  ok / failed     : {len(ok)} / {len(bad)}")
    print(f"  cache hit/miss  : {hits} / {len(ok) - hits}")
    if ok:
        print(f"  throughput      : {len(ok) / elapsed:.1f} req/s")
        print("  latency (s)     : " + "  ".join(
            f"{k}={v:.3f}" for k, v in lat.items()))
    if bad:
        kinds = {}
        for r in bad:
            kinds[r.get("error", "?")] = kinds.get(r.get("error", "?"), 0) + 1
        print(f"  failures        : {kinds}")
    return {"name": name, "elapsed": elapsed, "ok": len(ok), "failed": len(bad),
            "hits": hits, "latency": lat,
            "throughput": len(ok) / elapsed if ok else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase1", type=int, default=1000, help="cached requests")
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--phase2", type=int, default=24, help="fresh generations")
    ap.add_argument("--conc2", type=int, default=8)
    ap.add_argument("--texts", default="/tmp/inotex_texts.json")
    ap.add_argument("--fresh-base", default=None,
                    help="file whose text seeds phase 2 (default: the corpus)")
    ap.add_argument("--out", default="/tmp/stress-result.json")
    args = ap.parse_args()

    with open(args.texts, encoding="utf-8") as fh:
        corpus = [t for t in json.load(fh) if t and t.strip()]
    if not corpus:
        sys.exit("no texts to test with")
    print(f"corpus: {len(corpus)} pre-rendered answers")

    # Phase 1 cycles the SAME texts, which is the point: every one is a cache
    # hit, exactly like a hall full of visitors asking the curated questions.
    cached = [corpus[i % len(corpus)] for i in range(args.phase1)]
    r1 = run_phase("PHASE 1: cache hits", cached, args.concurrency, timeout=60)

    # Phase 2 asks for text nothing has rendered, so every one must be
    # generated. Unique suffixes keep them out of the cache.
    stamp = int(time.time())
    if args.fresh_base:
        with open(args.fresh_base, encoding="utf-8") as fh:
            base = fh.read().strip()
        # A unique tail keeps every request out of the cache while leaving the
        # body — and therefore the generation cost — identical across runs.
        fresh = [f"{base} شماره {stamp + i}" for i in range(args.phase2)]
    else:
        fresh = [f"{corpus[i % len(corpus)][:70]} شماره {stamp + i}"
                 for i in range(args.phase2)]
    r2 = run_phase("PHASE 2: saturation (fresh)", fresh, args.conc2, timeout=900)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"phase1": r1, "phase2": r2}, fh, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
