#!/usr/bin/env python3
"""Crawl talksiran.com (the ELECOMP 29 event platform) for training data.

    train-venv/bin/python talksiran_crawl.py --out ~/train-work/talksiran

WHAT LANDS WHERE
----------------
/events list (paginated)        -> one record per talk/panel/pitch
/public/panel-requests/{id}      -> full description + members for each event
/exhibitors list (paginated)     -> one record per exhibitor
/exhibitors/{id}                 -> CEO, field, exhibition per exhibitor

Raw HTML is kept next to the JSON so a parser bug is a reparse, never a
recrawl. robots.txt allows everything; the crawler still stays polite
(one request every 0.4 s, identifying User-Agent).
"""
import argparse
import json
import re
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE = "https://talksiran.com"
UA = "PadyarElecompTrainingBot/1.0 (chatbot training crawl; contact: admin@padyar.com)"
DELAY = 0.4

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
EVENT_LINK = re.compile(r"^(?:https?://talksiran\.com)?/public/panel-requests/(\d+)/?$")
EXHIBITOR_LINK = re.compile(r"^(?:https?://talksiran\.com)?/exhibitors/(\d+)/?$")
TYPE_WORDS = {"پنل": "panel", "تاکس": "talk", "پیچ": "pitch", "پنل‌ها": "panel"}


def _fa(s: str) -> str:
    return (s or "").translate(PERSIAN_DIGITS)


def _lines(block) -> list:
    text = block.get_text("||", strip=True)
    out = []
    for part in text.split("||"):
        part = re.sub(r"\s+", " ", part).strip()
        if part:
            out.append(part)
    dedup = []
    for p in out:
        if not dedup or dedup[-1] != p:
            dedup.append(p)
    return dedup


def fetch(client: httpx.Client, url: str, cache: Path) -> str:
    name = url.replace(BASE, "").replace("/", "_").replace("?", "_p_") or "_root"
    f = cache / (name + ".html")
    if f.exists():
        return f.read_text(encoding="utf-8")
    r = client.get(url)
    r.raise_for_status()
    f.write_text(r.text, encoding="utf-8")
    time.sleep(DELAY)
    return r.text


def _abs(href: str) -> str:
    return href if href.startswith("http") else BASE + href


def _card_blocks(soup: BeautifulSoup, pattern: re.Pattern):
    seen = set()
    for a in soup.find_all("a", href=True):
        m = pattern.match(a["href"])
        if not m:
            continue
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        # The whole card is often wrapped INSIDE the <a>; table rows hold a
        # tiny "مشاهده" link. Walk up only while the block is too small.
        block = a
        for _ in range(6):
            txt = len(block.get_text(" ", strip=True))
            if block.name in ("tr", "article") or (txt > 60 and block.name != "a") \
                    or (txt > 60 and block is a):
                break
            if block.parent is None:
                break
            block = block.parent
        yield sid, _abs(a["href"]), block


def _title_of(block) -> str:
    for tag in ("h3", "h4", "h2", "h5"):
        t = block.find(tag)
        if t and t.get_text(strip=True):
            return re.sub(r"\s+", " ", t.get_text(strip=True))
    lines = _lines(block)
    best = ""
    for ln in lines[:4]:
        if len(ln) > len(best) and not ln.startswith(("پنل", "تاکس", "پیچ")):
            best = ln
    return best


def parse_events(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for sid, url, block in _card_blocks(soup, EVENT_LINK):
        lines = _lines(block)
        blob = _fa(" ".join(lines))
        rec = {"source_id": int(sid), "url": url, "etype": "",
               "title": _title_of(block), "description": "", "jdate": "",
               "start_time": "", "hall": "", "members": ""}
        md = re.search(r"\d{4}/\d{2}/\d{2}", blob)
        if md:
            rec["jdate"] = md.group(0)
        mt = re.search(r"\d{1,2}:\d{2}", blob)
        if mt:
            rec["start_time"] = mt.group(0)
        for word, code in TYPE_WORDS.items():
            if re.search(rf"(?:^| ){word}(?: |$)", _fa(" ".join(lines[:3]))):
                rec["etype"] = code
                break
        for ln in lines:
            fa = _fa(ln)
            if ("سالن" in ln or "استیج" in ln) and not rec["hall"]:
                rec["hall"] = ln
            if "نفر" in fa and not rec["members"]:
                rec["members"] = ln
        for ln in lines:
            if ln == rec["title"] or len(ln) < 40 or ln == rec["hall"] \
                    or ln == rec["members"] or "سالن" in ln or "نفر" in ln \
                    or ln in TYPE_WORDS:
                continue
            if len(ln) > len(rec["description"]):
                rec["description"] = ln
        if not rec["etype"]:
            for word, code in TYPE_WORDS.items():
                if word in blob[:120]:
                    rec["etype"] = code
                    break
        if rec["title"] and (rec["jdate"] or rec["etype"]):
            out.append(rec)
    return out


def parse_exhibitors(html: str) -> list:
    """List rows only name the exhibitor reliably; the detail page carries
    the labeled fields, so here we just enumerate id/url/name."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for sid, url, block in _card_blocks(soup, EXHIBITOR_LINK):
        title = _title_of(block)
        lines = _lines(block)
        name = title or ""
        if not name:
            for ln in lines:
                if not ln.startswith(("نمایشگاه", "مشاهده")) and len(ln) > 2:
                    name = ln
                    break
        if name:
            out.append({"source_id": int(sid), "url": url, "name": name,
                        "ceo": "", "field": "", "exhibition": ""})
    return out


def parse_event_detail(sid: int, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for tag in ("h1", "h2", "h3"):
        t = soup.find(tag)
        if t and t.get_text(strip=True):
            title = t.get_text(strip=True)
            break
    main = soup.find("main") or soup.body or soup
    paras = [re.sub(r"\s+", " ", p.get_text(" ", strip=True))
             for p in main.find_all(["p", "li"])]
    paras = [p for p in paras if len(p) > 25]
    return {"source_id": sid, "title": title, "detail_text": "\n".join(paras)[:4000]}


def parse_exhibitor_detail(sid: int, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for tag in ("h1", "h2", "h3"):
        t = soup.find(tag)
        if t and t.get_text(strip=True):
            title = t.get_text(strip=True)
            break
    main = soup.find("main") or soup.body or soup
    text = re.sub(r"[ \t]+", " ", main.get_text("\n", strip=True))
    fields = {}
    for label, key in (("مدیرعامل", "ceo"), ("زمینه فعالیت", "field"),
                       ("غرفه", "booth"), ("سالن", "hall"), ("تلفن", "phone"),
                       ("وبسایت", "website"), ("ایمیل", "email")):
        m = re.search(rf"{label}\s*[:：]?\s*(.+)", text)
        if m:
            fields[key] = m.group(1).strip()[:300]
    return {"source_id": sid, "name": title, **fields}


def crawl_list(client, path_first, cache, parser) -> list:
    results, page = [], 1
    while True:
        url = BASE + path_first if page == 1 else f"{BASE}{path_first.split('?')[0]}?page={page}"
        html = fetch(client, url, cache)
        batch = parser(html)
        if not batch:
            break
        before = len(results)
        for rec in batch:
            key = rec["source_id"]
            if key not in {r["source_id"] for r in results}:
                results.append(rec)
        if len(results) == before:
            break
        page += 1
        if page > 40:
            break
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser()
    cache = out_dir / "html"
    cache.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers={"User-Agent": UA},
                      follow_redirects=True, timeout=30) as client:
        events = crawl_list(client, "/events", cache, parse_events)
        exhibitors = crawl_list(client, "/exhibitors", cache, parse_exhibitors)
        details_e = {}
        for e in events:
            html = fetch(client, e["url"], cache)
            details_e[e["source_id"]] = parse_event_detail(e["source_id"], html)
            print(f"event {e['source_id']}: {e['title'][:50]}")
        details_x = {}
        for x in exhibitors:
            html = fetch(client, x["url"], cache)
            details_x[x["source_id"]] = parse_exhibitor_detail(x["source_id"], html)
            print(f"exhibitor {x['source_id']}: {x['name'][:50]}")

    for e in events:
        d = details_e.get(e["source_id"], {})
        if d.get("detail_text"):
            e["detail_text"] = d["detail_text"]
        if d.get("title") and not e.get("title"):
            e["title"] = d["title"]
    for x in exhibitors:
        d = details_x.get(x["source_id"], {})
        # The detail page's <h1> is the authoritative company name; the
        # list-card heuristic sometimes returns the FIELD text as the name.
        if d.get("name"):
            x["name"] = d["name"]
        for k in ("ceo", "field", "booth", "hall", "phone", "website", "email"):
            if not x.get(k) and d.get(k):
                x[k] = d[k]

    payload = {"crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "events": events, "exhibitors": exhibitors}
    (out_dir / "talksiran.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nDONE: {len(events)} events, {len(exhibitors)} exhibitors "
          f"-> {out_dir / 'talksiran.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
