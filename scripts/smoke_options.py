#!/usr/bin/env python3
"""Ask a RUNNING install the smoke queries and print expected vs actual tier.

WHY THIS EXISTS. `scripts/run_eval.py` deliberately never calls the AI — it
measures retrieval, offline, against a golden set. That leaves the selection
tier with no regression net at all: nothing else in the repo proves that a
list question still offers a numbered choice, that "3" still lands on the third
company, or that an out-of-scope question still refuses once a model is in the
loop. This is that net, and it is a committed artifact rather than a promise.

It is NOT part of pytest: it needs a live server, a configured AI provider and
real content, and it spends money. Run it against a staging install before an
exhibition opens.

    .venv/bin/python scripts/smoke_options.py --base http://127.0.0.1:8000

Exit code 0 when every query landed on its expected shape, 1 otherwise.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = ROOT / "data" / "eval" / "smoke-options.json"

# Which sources satisfy which expectation. The mapping is here, in one place,
# rather than inside the JSON: the JSON is what a customer rewrites for their
# own content, and it should not have to know our tier names.
OFFERS = {"ai_options", "local_company_search"}
PICKS = {"local_pick"}
REFUSALS = {"refuse", "system"}
ANSWERS = {"local", "local_questions", "local_entity", "local_intent",
           "local_company_field", "ai_selected", "openai_classified", "openai"}


def ask(base: str, token: str, message: str, jar) -> dict:
    body = json.dumps({"message": message, "lang": "fa"}).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": base, "X-Chat-Token": token,
                 "Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            # The conversation cookie is what makes a pick resolve on the next
            # turn, so it has to be carried by hand — urllib keeps no jar.
            for header in resp.headers.get_all("Set-Cookie") or []:
                name, _, rest = header.partition("=")
                jar[name.strip()] = rest.split(";")[0]
            return {"status": resp.status, **json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "source": "", "text": "", "options": []}


def verdict(expect: str, result: dict) -> bool:
    source = result.get("source", "")
    options = result.get("options") or []
    if expect == "options":
        return source in OFFERS and bool(options)
    if expect == "pick":
        return source in PICKS
    if expect == "refuse":
        return result["status"] == 503 or source in REFUSALS
    return source in ANSWERS


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:8000",
                   help="the running install")
    args = p.parse_args()

    from app.auth.security import generate_chat_token
    token = generate_chat_token()

    cases = json.loads(CASES.read_text(encoding="utf-8"))["queries"]
    jar: dict = {}
    passed = failed = 0
    for case in cases:
        # A pick only means anything after the list that offered it, so the
        # setup query is replayed first rather than assumed to still be live.
        if case.get("after"):
            ask(args.base, token, case["after"], jar)
        result = ask(args.base, token, case["q"], jar)
        ok = verdict(case["expect"], result)
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} [{case['cat']}] expect={case['expect']:<8}"
              f" got={result.get('source', '') or result['status']:<22} {case['q']}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
