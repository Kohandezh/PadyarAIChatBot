"""Answer one question: if N visitors arrive at once, when does visitor #k get audio?

The percentile summaries hide the thing an operator actually asks. p50 and p99
describe the SHAPE of the distribution; they do not say what the 800th person
in the hall experienced. This records every request's position, its own
latency, and — the number that matters — how long after the rush began it was
answered.

Those two are different and the difference is the whole point. With
concurrency C, visitors do not stand in a single line: C of them are served at
once. Visitor #1000's wait is not 999 people long.
"""
import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "http://127.0.0.1:8003/tts"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--texts", default="/tmp/inotex_texts.json")
    ap.add_argument("--out", default="/tmp/position-result.json")
    args = ap.parse_args()

    corpus = [t for t in json.load(open(args.texts, encoding="utf-8")) if t.strip()]
    t0 = time.perf_counter()

    def one(i):
        text = corpus[i % len(corpus)]
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(URL, data=body,
                                     headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                payload = res.read()
                cache = (res.headers.get("x-tts-cache")
                         or res.headers.get("X-TTS-Cache") or "?")
            ok, err = True, None
        except Exception as exc:                  # noqa: BLE001
            payload, cache, ok, err = b"", "?", False, type(exc).__name__
        done = time.perf_counter()
        return {"n": i + 1,
                "queued_at": started - t0,       # when this visitor was served
                "answered_at": done - t0,        # since the rush began
                "latency": done - started,       # what THIS visitor waited
                "cache": cache, "bytes": len(payload), "ok": ok, "error": err}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(one, range(args.count)))
    total = time.perf_counter() - t0

    ok = [r for r in rows if r["ok"]]
    print(f"{len(ok)}/{len(rows)} ok in {total:.2f}s   "
          f"({len(ok)/total:.0f} req/s, concurrency {args.concurrency})")
    print(f"cache: {sum(1 for r in ok if r['cache'] == 'hit')} hit / "
          f"{sum(1 for r in ok if r['cache'] == 'miss')} miss\n")

    print(f"{'visitor':>8} {'waited':>9} {'answered at':>13}")
    for n in (1, 10, 50, 100, 300, 500, 800, 900, 999, 1000):
        if n <= len(rows):
            r = rows[n - 1]
            print(f"{n:>8} {r['latency']*1000:>7.0f}ms {r['answered_at']:>11.2f}s")

    lat = sorted(r["latency"] for r in ok)
    print(f"\nlatency  p50 {lat[len(lat)//2]*1000:.0f}ms   "
          f"p95 {lat[int(len(lat)*0.95)]*1000:.0f}ms   "
          f"p99 {lat[int(len(lat)*0.99)]*1000:.0f}ms   max {lat[-1]*1000:.0f}ms")
    print(f"mean {statistics.fmean(lat)*1000:.0f}ms")

    json.dump({"total": total, "rows": rows}, open(args.out, "w"))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
