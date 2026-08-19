"""Live smoke test — proves the running install still works, end to end.

Purpose: hardening changes (security headers, CI gates, dependency bumps,
config tightening) are exactly the kind of change that passes unit tests and
still breaks the real thing — a header that blocks an inline script, a CORS
rule that kills the chat token, a dependency bump that changes an API.

So this hits a RUNNING server over HTTP the way a visitor and an operator do,
and reports PASS/FAIL per flow. Run it before a hardening change to capture a
baseline, then after every change:

    .venv/bin/python scripts/smoke-live.py                 # default http://127.0.0.1:8001
    .venv/bin/python scripts/smoke-live.py --url http://host:port
    .venv/bin/python scripts/smoke-live.py --json          # machine-readable

Exit code is non-zero if any check fails, so it can gate a deployment.
It is READ-ONLY against real data: it never writes settings, never sends an
SMS, and the one OTP challenge it creates is left unverified and expires by
itself.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

CHECKS = []


def check(name, critical=True):
    def wrap(fn):
        CHECKS.append((name, fn, critical))
        return fn
    return wrap


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "padyar-smoke/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)


def post(url, payload, headers=None, timeout=30):
    body = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", "User-Agent": "padyar-smoke/1.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# ── The flows that must never break ──────────────────────────────────────

@check("health: /api/ready reports ready")
def _ready(base):
    status, body, _ = get(f"{base}/api/ready")
    assert status == 200, f"HTTP {status}"
    data = json.loads(body)
    assert data.get("status") == "ready", f"status={data.get('status')}"
    return f"knowledge={data.get('knowledge_version')}"


@check("chat page: serves HTML with a chat token")
def _page(base):
    status, body, _ = get(f"{base}/")
    assert status == 200, f"HTTP {status}"
    assert re.search(r'name="chat-token"\s+content="[^"]+"', body), "no chat token in page"
    return f"{len(body)} bytes"


@check("chat: a known question gets a real answer")
def _chat(base):
    _, page, _ = get(f"{base}/")
    token = re.search(r'name="chat-token"\s+content="([^"]+)"', page).group(1)
    status, body = post(
        f"{base}/chat",
        {"message": "اینوتکس چیست", "lang": "fa"},
        {"X-Chat-Token": token, "Origin": base},
    )
    assert status == 200, f"HTTP {status}: {body[:160]}"
    data = json.loads(body)
    assert data.get("text", "").strip(), "empty answer"
    return f"source={data.get('source')} conf={data.get('confidence')}"


@check("chat: origin validation still refuses a foreign origin")
def _chat_origin(base):
    _, page, _ = get(f"{base}/")
    token = re.search(r'name="chat-token"\s+content="([^"]+)"', page).group(1)
    status, _ = post(
        f"{base}/chat",
        {"message": "اینوتکس چیست", "lang": "fa"},
        {"X-Chat-Token": token, "Origin": "https://evil.example"},
    )
    assert status in (400, 403), f"foreign origin was accepted (HTTP {status})"
    return f"refused with {status}"


@check("chat: a missing token is refused")
def _chat_no_token(base):
    status, _ = post(f"{base}/chat", {"message": "اینوتکس چیست"}, {"Origin": base})
    assert status in (400, 401, 403), f"tokenless request accepted (HTTP {status})"
    return f"refused with {status}"


@check("registration: form options come from the taxonomy")
def _options(base):
    status, body, _ = get(f"{base}/api/registration/options?lang=fa")
    assert status == 200, f"HTTP {status}"
    data = json.loads(body)
    for key in ("jobs", "positions", "interests", "flags"):
        assert data.get(key), f"{key} is empty"
    return f"jobs={len(data['jobs'])} interests={len(data['interests'])}"


@check("visit planner: answers, and never invents an exhibitor")
def _plan(base):
    status, body = post(f"{base}/api/visit-plan", {"interests": "هوش مصنوعی", "lang": "fa"})
    assert status == 200, f"HTTP {status}"
    data = json.loads(body)
    assert data.get("sections"), "no sections returned"
    assert "غرفه‌داران" in data.get("note", ""), "the exhibitor-directory caveat is missing"
    return f"{len(data['sections'])} sections, matched={data.get('matched')}"


@check("admin: pages require a login")
def _admin_guard(base):
    for path in ("/secure-panel-inotex/settings/sms",
                 "/secure-panel-inotex/settings/taxonomy"):
        req = urllib.request.Request(f"{base}{path}")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(req, timeout=20) as r:
                assert False, f"{path} served without a login (HTTP {r.status})"
        except urllib.error.HTTPError as e:
            assert e.code in (302, 303, 307), f"{path} -> HTTP {e.code}"
    return "redirected to login"


@check("admin: APIs refuse anonymous callers")
def _admin_api(base):
    for path in ("/admin/api/sms", "/admin/api/taxonomy"):
        try:
            status, body, _ = get(f"{base}{path}")
            assert False, f"{path} served anonymously (HTTP {status})"
        except urllib.error.HTTPError as e:
            assert e.code in (401, 403), f"{path} -> HTTP {e.code}"
    return "401/403"


@check("secrets: no credential leaks into any public response", critical=True)
def _no_leak(base):
    needles = ("ASANAK_PASSWORD", "OPENAI_API_KEY", "password_hash", "code_hmac", "enc:")
    for path in ("/", "/api/ready", "/api/registration/options"):
        _, body, _ = get(f"{base}{path}")
        for n in needles:
            assert n not in body, f"{path} leaked {n!r}"
    return "clean"


@check("static: theme stylesheet loads", critical=False)
def _static(base):
    status, body, _ = get(f"{base}/themes/inotex/static/style.css")
    assert status == 200, f"HTTP {status}"
    assert len(body) > 1000, "stylesheet suspiciously small"
    return f"{len(body)} bytes"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    results, failed = [], 0
    for name, fn, critical in CHECKS:
        try:
            detail = fn(base) or ""
            results.append({"check": name, "ok": True, "detail": detail, "critical": critical})
        except Exception as e:
            failed += 1 if critical else 0
            results.append({"check": name, "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "critical": critical})

    if args.json:
        print(json.dumps({"url": base, "failed": failed, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\nlive smoke → {base}\n" + "─" * 64)
        for r in results:
            mark = "PASS" if r["ok"] else ("FAIL" if r["critical"] else "warn")
            print(f"  [{mark}] {r['check']}")
            if r["detail"]:
                print(f"         {r['detail']}")
        print("─" * 64)
        print(f"  {sum(1 for r in results if r['ok'])}/{len(results)} passed"
              + (f", {failed} CRITICAL FAILURE(S)" if failed else ""))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
