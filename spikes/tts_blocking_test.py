"""Does one visitor asking something NEW delay a visitor asking something known?

It should not. A cached answer is a file read; it has nothing to do with the
GPU. But `def tts()` is a synchronous handler, so FastAPI runs it in anyio's
worker pool (40 threads by default), and a cache MISS blocks its thread for the
whole generation — `_generate_pool.submit(_run).result()`. Enough simultaneous
misses and every thread is parked waiting on a queue of GPU work, with no
thread left to hand anyone a file that is already on disk.

This measures where that starts to hurt: fire M misses, then, once they are
established, fire cached requests and time them.
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "http://127.0.0.1:8003/tts"


def call(text, timeout=1200):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            res.read()
            cache = res.headers.get("x-tts-cache", "?")
        return {"ok": True, "s": time.perf_counter() - t, "cache": cache}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "s": time.perf_counter() - t,
                "cache": "?", "error": type(exc).__name__}


def scenario(name, n_miss, n_hit, corpus, stamp):
    print(f"\n=== {name}: {n_miss} soal-e JADID + {n_hit} soal-e AMADE ===",
          flush=True)
    hits = []
    misses = []

    # Short unique text: the point is the QUEUE, not how long one clip takes.
    fresh = [f"سلام این جمله شماره {stamp}{i} است" for i in range(n_miss)]

    miss_pool = ThreadPoolExecutor(max_workers=max(1, n_miss))
    started = time.perf_counter()
    futures = [miss_pool.submit(call, t) for t in fresh]

    # Let every miss actually reach the server and take its thread before the
    # cached requests arrive — otherwise this measures a race, not the queue.
    time.sleep(3.0)

    def fire_hits():
        with ThreadPoolExecutor(max_workers=n_hit) as p:
            hits.extend(p.map(lambda i: call(corpus[i % len(corpus)]),
                              range(n_hit)))

    th = threading.Thread(target=fire_hits)
    th.start()
    th.join()
    hit_done = time.perf_counter() - started

    hl = sorted(h["s"] for h in hits if h["ok"])
    bad = [h for h in hits if not h["ok"]]
    served = sum(1 for h in hits if h.get("cache") == "hit")
    print(f"  soal-e AMADE  : {len(hl)}/{n_hit} javab gereft  ({served} az cache)")
    if hl:
        print(f"    montazer    : min {hl[0]:.2f}s   p50 {hl[len(hl)//2]:.2f}s   "
              f"max {hl[-1]:.2f}s")
    if bad:
        print(f"    KHATA       : {len(bad)}  {bad[0].get('error')}")
    print(f"    hame tamum shodan bad az {hit_done:.1f}s", flush=True)

    misses.extend(f.result() for f in futures)
    miss_pool.shutdown()
    ml = sorted(m["s"] for m in misses if m["ok"])
    if ml:
        print(f"  soal-e JADID  : min {ml[0]:.1f}s   p50 {ml[len(ml)//2]:.1f}s   "
              f"max {ml[-1]:.1f}s")
    return {"name": name, "n_miss": n_miss, "n_hit": n_hit,
            "hit_latency": hl, "miss_latency": ml, "hit_failures": len(bad)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--texts", default="/tmp/inotex_texts.json")
    ap.add_argument("--out", default="/tmp/blocking-result.json")
    ap.add_argument("--cases", default="0,8,45")
    args = ap.parse_args()

    corpus = [t for t in json.load(open(args.texts, encoding="utf-8")) if t.strip()]
    stamp = int(time.time())
    out = []
    for i, n in enumerate(int(x) for x in args.cases.split(",")):
        label = "PAYE (hich soal-e jadid)" if n == 0 else f"{n} soal-e jadid"
        out.append(scenario(label, n, 20, corpus, stamp + i * 1000))
    json.dump(out, open(args.out, "w"))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
