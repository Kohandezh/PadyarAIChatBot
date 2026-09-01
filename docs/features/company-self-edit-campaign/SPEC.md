# Company Self-Edit + Bulk Confirm Campaign

Status: Implemented (2026-09-01)
Domain: leads module (`app/services/leads.py`, `app/services/campaigns.py`, `app/services/sms_outbox.py`)
Migrations: 0021 (one-time open + edit sessions), 0022 (multi-field edits), 0023 (sms_messages), 0024 (sms_campaigns)

## The scenario

A company contact receives ONE SMS — from the booth flow or from the
organizer's bulk campaign — opens its one-time link, reviews the company's
whole profile, corrects whatever is wrong, and confirms. The organizer
watches, per campaign, how many messages actually arrived.

## What shipped

### 1. The one-time link, actually one-time (migrations/0021)

- `GET /edit/{token}` serves a **gate page** (`templates/leads/begin.html`)
  with the one-time warning and one button. GET never burns: Telegram and
  WhatsApp prefetch URLs server-side, and a link that died on GET was a link
  the contact never had.
- The button `POST /api/leads/edit/{token}/begin` is the burn: a race-safe
  conditional UPDATE sets `edit_invites.used_at` + `opened_at`, and an
  `edit_sessions` row is minted (cookie `padyar_edit_s`, HttpOnly, HMAC-stored,
  TTL 2 h capped by the invite's own expiry).
- After the burn, the page works from the cookie: same browser may refresh;
  every other device (and the link itself) gets the one-sentence dead page.
- A browser carrying a `/v` visitor cookie (a booth phone) is refused at the
  button press **without burning**: the person who captured the lead may not
  spend the company's one opening either. This moves the old submit-time
  guard one step earlier.
- Re-issuing an invite, releasing a lead, or deleting a company also kills
  un-submitted sessions.

### 2. The whole profile is editable (migrations/0022)

- `EDITABLE_FIELDS` in `app/services/leads.py`: `title, text, activity_field,
  contact_name, contact_position, contact_mobile, email, website,
  company_phone, fax, address, province`. Read-only context: `booth_number`,
  `hall`. Organizer-only (never on the page): English columns, `video_url`,
  `company_type`, `org_stage`.
- Endpoints are session-cookie based: `GET /api/leads/edit/state`,
  `POST /api/leads/edit/submit`. The payload is `{"fields": {...}}` (strict
  whitelist, extra key → 400) or `{"confirm": true}`.
- Validation per field (lengths, required title/text, mobile normalization
  with Persian-digit folding, email/URL shape).
- A submission is either:
  - **change** — one pending `dataset_edits` row holding both sides
    (`old_values`/`new_values` JSON; the legacy `old_text`/`new_text` columns
    keep being filled for the `text` field);
  - **confirm** — auto-approved, the company row untouched, the lead
    completed. An unchanged form is a confirmation, not an empty draft.
    Confirming supersedes an existing pending draft, on the record.
- Approval writes every changed field; a changed `contact_mobile` also flows
  back to the lead (`company_leads.phone` + `phone_hash`) — the campaign's
  next SMS goes to the number the company itself last confirmed. Revert
  restores every field.

### 3. Delivery is proven, not assumed (migrations/0023)

- `sms_messages` — one row per gateway send: msgid, masked destination,
  kind, reference, campaign, status. Written by every send path in
  `app/services/sms.py` (best effort; telemetry never breaks a send).
- `app/services/sms_outbox.py::poll_deliveries()` asks Asanak's `msgstatus`
  for queued rows younger than 24 h: code 6 → `delivered`; any other word
  stays `queued` with the code recorded; wordless rows older than the window
  close as `unknown`. This wires `asanak_status()`, which until now had zero
  production callers.
- The poll runs in the app lifespan every 5 minutes
  (`app/main.py::_sms_delivery_loop`) and on demand:
  `POST /admin/api/sms/refresh-statuses`, ledger at
  `GET /admin/api/sms/outbox`.
- A 200 from the gateway remains what it always was: *queued*. The panel's
  word for arrival is the poller's.

### 4. The bulk confirm campaign (migrations/0024)

- Companies page card «پیامک تأیید اطلاعات شرکت‌ها»: audience count (=
  companies with a non-empty `contact_mobile`), editable text (setting
  `sms_campaign_text`, must contain `{magic_link}`), launch with a confirm
  dialog, campaign history with delivery badges and per-company detail.
- `POST /admin/api/leads/campaigns` launches; `app/services/campaigns.py::run`
  executes in a background task, paced at one message per second.
- Per company: pending draft → **skipped**; live owner → its lead, fresh
  invite (the old one dies); nobody owns it → a lead is created with
  `origin='campaign'` and the filed mobile as the owner.
- Budget exhaustion or a 1014 link refusal **stops** the campaign with the
  reason on the row — no silent retries. The per-company verdicts live on
  `sms_messages` rows with `campaign_id`, so the report (sent / skipped /
  failed / delivered / queued) is one query.

## Tests

- `tests/test_leads_edit_session.py` — the one-time semantics (burn on press,
  prefetch-safe GET, refresh, expiry, dead page).
- `tests/test_leads_edit_fields.py` — whitelist, validation, diff/review/
  revert, confirm-as-is, legacy drafts.
- `tests/test_sms_outbox.py` — record on every send path, poll outcomes,
  stale closure, admin refresh + ledger.
- `tests/test_leads_campaigns.py` — audience, run, campaign leads, reissue,
  refusal stop, endpoints.
- Updated for the new flow: `test_leads_company_tools.py`,
  `test_leads_contacts_admin.py`, `test_leads_visitor_rotation.py`.

## Known local-environment note (pre-existing, not this feature)

`verify_admin` compares a naive session expiry against **local** time
(`app/db/timeutil.py::compare_now`), so admin-session tests that insert
`utcnow()+1h` fail on any non-UTC dev machine (this one is +03:30) and pass
on UTC CI. New tests in this feature insert +12h to stay machine-independent.
A proper fix (make `compare_now`/`as_datetime` UTC-consistent for naive
values) is separate work.
