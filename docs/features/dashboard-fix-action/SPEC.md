# SPEC: Dashboard fix-action — «اصلاح» on the low-confidence queue

| Field | Value |
|-------|-------|
| Created | 2026-09-14 |
| Updated | 2026-09-14 |
| Status | Implemented |
| Domain | admin |
| Author | تیم پادیار |
| Sources | The 2026-09-14 danesh-bonyan assessment (read-only ops loops); the shipped fix flow of `static/admin/js/conversations.js` (`openFix`/`saveFix`); commits on `task/ops-loops` |

This document describes what IS shipped, not what was planned.

---

## 1. Scenario

**Who triggers what, from where.** An operator opens the admin dashboard and
sees the «سوالات با اطمینان پایین» table — the questions the bot fumbled.
Before, that screen was read-only: the operator had to leave it, open the
conversations page (or the dataset editor), retype the question, and hunt
for the right answer. Now every row carries an «اصلاح» button:

1. **Click «اصلاح»** — the fix modal opens with the visitor's question
   typed in (editable, if the sentence was incomplete) and, when the log row
   carries one, the dataset entry the bot actually served pre-selected.
2. **Click «ذخیره»** — the question → dataset mapping is POSTed to the
   existing `/admin/api/questions` endpoint, which inserts the mapping and
   reindexes on the way out. The very next visitor asking this question
   gets the right answer.

Row to saved fix in **two clicks** (three when the answer must be searched
first) — inside the dashboard's own screen, through the same endpoint and
the same reindex single-source the conversations weak view uses.

## 2. What ships

| Piece | Change |
|---|---|
| `app/routers/admin.py` | `/admin/api/low_confidence` SELECT gains `entry_id` — additive field, present in `chat_logs`, so the modal can pre-select the served answer. Empty stays empty. |
| `static/admin/js/dashboard.js` | Per-row «اصلاح» button (built with `createElement`, never `innerHTML` — row data is visitor text inside an authenticated session), plus a minimal replication of the conversations `openFix`/`saveFix` flow (repo pattern: one JS file per page, no build step). Save goes through `fetchAuth` (CSRF) to `/admin/api/questions`. |
| `templates/admin/dashboard.html` | The fix modal (same markup as the conversations page) and the fifth, header-less action column. |

Everything else on the dashboard stays read-only. The clear-history /
clear-tokens actions, charts, and stats payloads are untouched.

## 3. Wiring — reader/writer pairs

| Writer | Reader | What breaks if they drift |
|---|---|---|
| `chat_logs.entry_id` written at answer time (`app/services/conversations.py`) | `SELECT … entry_id` in `/admin/api/low_confidence` → `openFix(row.query, row.entry_id)` | The modal cannot pre-select the served answer; the operator starts every fix from a blank list |
| `fix-modal` markup in `dashboard.html` | `openFix`/`saveFix` in `dashboard.js` | The button renders but the click throws on a null element |
| `POST /admin/api/questions` (`app/routers/dataset.py`, reindexes after insert) | `saveFix` in `dashboard.js` via `fetchAuth` | A bare `fetch()` would 403 on CSRF; any other endpoint would bypass the reindex single-source |

## 4. Tests

`tests/test_dashboard_fix_action.py`:

- `test_low_confidence_payload_carries_entry_id` — behavioural: rows keep
  the `entry_id` logged in `chat_logs`; empty stays empty.
- `test_dashboard_js_wires_the_fix_to_the_questions_endpoint` — source scan:
  the save must go through `fetchAuth` to `/admin/api/questions`, and the
  «اصلاح» action must exist. A request test cannot see what the browser
  sends; same pattern as `tests/test_admin_js_csrf_conformance.py`.
- `test_dashboard_template_carries_the_fix_modal` — the modal the JS drives
  must exist on the dashboard page. Written red first (the template lacked
  it); each test fails when its wiring is removed.
