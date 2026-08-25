#!/usr/bin/env python3
"""Load-test the chat pipeline the way an exhibition crowd does.

The TTS service already had a stress tool (deploy/tts/stress_test.py); the
chatbot itself never did. This exercises the real HTTP surface — page render,
dataset bootstrap, and a mix of chat questions across the retrieval tiers —
against a RUNNING server and reports latency percentiles plus throughput.

Usage (server must already be up, e.g. `python main.py`):

    python scripts/stress_chat.py --url http://127.0.0.1:8000 \
        --users 20 --duration 60

Notes
-----
* Questions are read from the live /api/questions endpoint, so the mix matches
  what this install actually knows. One in five is deliberately garbled to
  force the low-confidence path (Tier 2 or the 503) — a booth always has some.
* Rate limiting applies: with defaults (20 req / 60 s / IP) each virtual user
  stays well under its own bucket. 429s are reported, not treated as errors.
* The chat token is scraped from the rendered page exactly the way the real
  frontend obtains it.
"""
import argparse
import json
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PAGE_RE = re.compile(r'name="chat-token" content="([^"]+)"')


def http_json(url, payload, headers, timeout=60):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json",
                                          **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def http_get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies = []
        self.counts = {}
        self.errors = []

    def record(self, name, seconds, status, extra=""):
        with self.lock:
            self.latencies.append(seconds)
            self.counts[(name, status)] = self.counts.get((name, status), 0) + 1
            if status >= 500 and len(self.errors) < 20:
                self.errors.append(f"{name} -> {status} {extra[:200]}")

    def snapshot(self):
        with self.lock:
            lat = sorted(self.latencies)
            counts = dict(self.counts)
            errors = list(self.errors)
        if not lat:
            return {}
        def pct(p):
            return round(lat[min(len(lat) - 1, int(len(lat) * p))] * 1000, 1)
        return {
            "requests": len(lat),
            "rps": None,
            "p50_ms": pct(0.50), "p90_ms": pct(0.90),
            "p99_ms": pct(0.99), "max_ms": round(lat[-1] * 1000, 1),
            "mean_ms": round(statistics.mean(lat) * 1000, 1),
            "by_endpoint_status": {f"{k[0]} {k[1]}": v for k, v in sorted(counts.items())},
            "sample_5xx": errors,
        }


def args_url_origin(base):
    """Referer the endpoint's origin check will accept: the base URL itself."""
    return base.rstrip("/") + "/"


def user_session(base, questions, stats, deadline, seed):
    rng = random.Random(seed)
    status, body = http_get(base + "/")
    stats.record("GET /", 0, status)
    match = PAGE_RE.search(body.decode("utf-8", "replace"))
    if not match:
        stats.record("bootstrap", 0, 0, "no chat token in page")
        return
    headers = {"X-Chat-Token": match.group(1),
               "User-Agent": "padyar-stress/1.0",
               "Referer": args_url_origin(base)}
    status, _ = http_get(base + "/api/dataset", timeout=30)
    stats.record("GET /api/dataset", 0, status)

    i = 0
    while time.monotonic() < deadline:
        i += 1
        if questions and rng.random() < 0.8:
            message = rng.choice(questions)
        else:
            message = "sdkfj hqwkejh kajshd " + str(rng.randint(1000, 9999))
        t0 = time.monotonic()
        try:
            status, payload = http_json(
                base + "/chat", {"message": message, "lang": "fa"}, headers)
            stats.record("POST /chat", time.monotonic() - t0, status,
                         str(payload.get("source", "")))
        except urllib.error.HTTPError as e:
            stats.record("POST /chat", time.monotonic() - t0, e.code, e.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001 — transport failure is a result
            stats.record("POST /chat", time.monotonic() - t0, -1, type(e).__name__)
        time.sleep(rng.uniform(2.0, 6.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--duration", type=int, default=60)
    args = ap.parse_args()

    try:
        _, body = http_get(args.url + "/api/questions", timeout=30)
        questions = [q["question"] for q in json.loads(body) if q.get("question")]
    except Exception as e:  # noqa: BLE001
        print(f"could not read /api/questions ({type(e).__name__}: {e}) — using builtin mix")
        questions = ["سلام", "هزینه غرفه چقدر است؟", "نمایشگاه کجاست؟",
                     "ساعات بازدید چیست؟"]
    print(f"loaded {len(questions)} questions; {args.users} users for {args.duration}s")

    stats = Stats()
    deadline = time.monotonic() + args.duration
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(user_session, args.url, questions, stats, deadline, s)
                   for s in range(args.users)]
        for f in futures:
            f.result()
    elapsed = time.monotonic() - started

    snap = stats.snapshot()
    snap["rps"] = round(snap["requests"] / elapsed, 1)
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
