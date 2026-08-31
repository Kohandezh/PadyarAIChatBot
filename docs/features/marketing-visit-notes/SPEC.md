# Marketing Visit Notes

**Slug:** `marketing-visit-notes` · **Status:** Implemented · **Domain:** leads
**Created:** 2026-08-31

## The scenario

The marketing field team walks the hall and WhatsApp-messages what they see:

```
شرکت مبین فناوران جی/ دیجی‌سان سالن 38A
شرکت داخل سامانه ثبت بود و همکاری کردند فرم براشون ارسال شد
بشدت هم مشتاق همکاری با شرکت کهن سیستم فردا هستند
مسئول اداری غرفه سرکار خانم زهرا باقری
شماره تماس: 09936495001
```

Every line had a home in the lead pipeline except the observation itself
(«بشدت مشتاق همکاری…») and the contact who would not do OTP on the spot.
Those lived and died in the group chat. This feature is the note's home.

## What ships

| Piece | Where |
| ----- | ----- |
| Table | `marketing_notes` — migrations/0018_marketing_notes.sql (PG) + the SQLite half in `leads.py` `_TABLES` |
| Service | `app/services/leads.py` — `create_note()`, `list_notes()` |
| Agent API | `POST /api/leads/notes` (field-agent cookie, per-visitor rate limit) |
| Admin API | `GET /admin/api/leads/notes?dataset_id=&q=` · `GET .../export` (CSV, formula-injection neutralized) |
| Agent UI | `/v` panel step 2 — «فعلاً فقط یادداشت می‌کنم»: eagleness (سرد/معمولی/داغ) + note + optional UNVERIFIED contact (name/position/phone, no OTP) |
| Admin UI | Leads page — «یادداشت‌های بازدید» card: newest-first feed, filter box (one company's timeline, or full-text), CSV button |
| Tests | `tests/test_marketing_notes.py` (6) |

## The contract

- **A note never claims a company.** company_leads' ownership is untouched
  by note writes — the tested rule: after noting, the company is still in
  the booth search, so the formal registration can still happen.
- **The contact block is note-grade.** Stored, fed, exported — but never a
  lead, never OTP-consented. Formalizing a contact stays with
  `register_contact` (OTP) or `admin_add_contact` (operator vouches).
- Warmth is one of `low | medium | high`, rejected otherwise; Persian digits
  in the phone fold to ASCII at write time.
- Everything the agent types is escaped in the admin feed and apostrophe-
  defused in the CSV (`'=CMD…`).
- Per-visitor rate limit, same key as `register()` — the NAT'd hall shares
  one IP by design.

## Deliberately not done

- No agent-facing note history (owner's choice: only the admin sees notes).
- No editing/deleting notes from the UI — append-only like an observation
  log; a wrong note is corrected by the next note.
