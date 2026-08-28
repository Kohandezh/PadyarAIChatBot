#!/usr/bin/env python3
"""Walk up to a RUNNING install as four different people and hold a conversation.

WHY THIS EXISTS. Every test set in this repo asks ONE question at a time.
`run_eval.py` scores retrieval offline, `smoke_options.py` checks one query per
case. Both miss the whole class of failure that only appears on the SECOND turn:
a follow-up that needs the previous list, «دومی» with nothing to point at, a
pick that arrives after the visitor changed the subject.

They also miss how people type. The four failures reported from the live kiosk
on 2026-08-28 were all phrasing, not retrieval: one wrong letter («حوضه»),
one word split in two («فن آوری»), and two ordinary sentences that happened to
be longer than three words. None of them would have been caught by asking the
canonical form of the question, which is the only form a hand-written test set
ever contains.

So this holds real conversations instead. Each persona keeps its cookies across
turns, which is what makes the follow-ups real follow-ups.

    .venv/bin/python scripts/persona_probe.py --base http://127.0.0.1:8001

It is NOT part of pytest: it needs a live server, a configured provider and real
content. Exit code 0 when every asserted turn met its expectation.
"""
import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONAS = ROOT / "data" / "eval" / "personas.json"

# Which sources satisfy which expectation. Kept here rather than in the JSON:
# the JSON is what a customer rewrites for their own content, and it should not
# have to learn our tier names.
LIST_SOURCES = {"ai_options", "local_company_search"}
PICK_SOURCES = {"local_pick"}
REFUSE_SOURCES = {"refuse", "system"}


def _fetch_token(base: str, jar) -> str:
    """The chat token is HMAC-signed and injected into the page, so it has to
    be read from a real page load. Same cookie jar, because the page is also
    where padyar_conv is first set."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    with opener.open(base + "/", timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'name="chat-token"\s+content="([^"]+)"', html)
    if not m:
        sys.exit("no chat token in the page — is this the chat UI?")
    return m.group(1)


def _ask(base: str, jar, token: str, message: str, lang: str = "fa") -> dict:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"message": message, "lang": lang}).encode()
    req = urllib.request.Request(
        base + "/chat", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Chat-Token": token,
                 "Origin": base, "Referer": base + "/"})
    try:
        with opener.open(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        return {"_http": e.code, "text": detail, "source": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001 — a probe must report, never crash
        return {"_error": str(e), "text": "", "source": "ERROR"}


def _verdict(expect: str, reply: dict) -> str:
    """"" when the turn met its expectation, else why it did not."""
    source = reply.get("source") or ""
    options = reply.get("options") or []
    if expect == "any":
        return ""
    if expect == "list":
        if source in LIST_SOURCES and options:
            return ""
        return f"expected a numbered list, got source={source!r} with {len(options)} options"
    if expect == "pick":
        if source in PICK_SOURCES:
            return ""
        return f"expected the pick tier, got source={source!r}"
    if expect == "refuse":
        if source in REFUSE_SOURCES:
            return ""
        return f"expected a refusal, got source={source!r}"
    if expect == "answer":
        if source and source not in REFUSE_SOURCES and not options:
            return ""
        return f"expected one answer, got source={source!r} with {len(options)} options"
    return f"unknown expectation {expect!r}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000",
                    help="the running install (default: http://127.0.0.1:8000)")
    ap.add_argument("--personas", type=Path, default=PERSONAS)
    ap.add_argument("--only", help="run just this persona by name")
    ap.add_argument("--json", type=Path,
                    help="also write the full transcript here")
    args = ap.parse_args()

    spec = json.loads(args.personas.read_text(encoding="utf-8"))
    people = [p for p in spec["personas"]
              if not args.only or p["name"] == args.only]
    if not people:
        sys.exit(f"no persona named {args.only!r}")

    base = args.base.rstrip("/")
    transcript, misses = [], 0

    for person in people:
        print(f"\n{'=' * 72}\n{person['name']}  —  {person['who']}\n{'=' * 72}")
        # A FRESH cookie jar per persona. Sharing one would carry the previous
        # person's conversation_id, and every follow-up would then resolve
        # against a list they never saw — which is the shared-kiosk bug, not
        # the thing under test here.
        jar = http.cookiejar.CookieJar()
        try:
            token = _fetch_token(base, jar)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"cannot reach {base}: {e}")

        for i, turn in enumerate(person["turns"], 1):
            reply = _ask(base, jar, token, turn["say"])
            problem = _verdict(turn.get("expect", "any"), reply)
            text = (reply.get("text") or "").replace("\n", " / ")
            row = {"persona": person["name"], "n": i, "say": turn["say"],
                   "expect": turn.get("expect", "any"),
                   "source": reply.get("source"),
                   "options": [o.get("title") for o in reply.get("options") or []],
                   "text": reply.get("text") or "", "problem": problem}
            transcript.append(row)
            if problem:
                misses += 1
            mark = "  " if not problem else "!!"
            print(f"{mark} [{i}] {turn['say']}")
            print(f"      source={reply.get('source')}  "
                  f"options={len(reply.get('options') or [])}")
            print(f"      {text[:220]}")
            if problem:
                print(f"      ^^ {problem}")

    print(f"\n{'=' * 72}")
    print(f"{len(transcript)} turns, {misses} did not meet their expectation")
    if args.json:
        args.json.write_text(json.dumps(transcript, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"transcript -> {args.json}")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
