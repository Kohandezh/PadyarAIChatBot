# Research: Visitor-facing "my chats" (list, reopen, delete)

| Field | Value |
|-------|-------|
| Feature | Let a signed-in visitor see, reopen, and delete their own past conversations from the hamburger drawer (Phase 2 of `docs/features/hamburger-menu/`) |
| Date | 2026-08-29 |
| Status | Done |

## Why This Research

`docs/features/hamburger-menu/SPEC.md` deferred this in Phase 1 because, at the time, no persistent visitor identity or message-persistence existed at all. By the time work resumed, an unrelated security-remediation effort had shipped exactly that infrastructure. This research re-checked what was actually available before writing any new code, so the feature reuses what exists instead of duplicating it.

## Questions to Answer

- Does a persistent, trustworthy visitor identity exist now, and how is it read on the backend?
- Does `chat.py` actually persist messages yet?
- Is there an existing function to list one visitor's conversations? A delete function?
- How does the existing `/chat` endpoint decide which conversation a message continues — can that same logic be reused for "reopen"?

## Findings

### The backend landscape changed completely since Phase 1

- **`app/auth/visitor.py`** — a real session: `mint()`/`resolve()`/`revoke()` against a `visitor_sessions` table, carried in an HttpOnly, `secure`, `samesite=lax` cookie. `resolve_visitor` middleware (`app/main.py`) sets `request.state.visitor_id` on every request. `require_visitor(request)` is a FastAPI dependency that returns the id or raises 401 with `{"code": "registration_required", ...}` — already used by `POST /api/auth/profile` in `app/routers/otp.py`.
- **`chat.py` now persists every message.** `conversations.get_or_create_conversation(...)`, `append_visitor_message(...)`, `append_assistant_message(...)` are wired into the `/chat` handler. Previously none of this ran.
- **`continuable_conversation_id(conversation_id, visitor_id)`** (`app/services/conversations.py`) already does the exact ownership check "reopen" needs: given the `padyar_conv` cookie value and the session's visitor_id, it returns the id unchanged if the visitor owns it (or the row doesn't exist yet), and `""` (forcing a fresh conversation) if it belongs to someone else. `/chat` already calls this on every message.
- **`conversations.list_conversations(visitor_id=...)`** exists, but is only ever called from the **admin-only** router (`app/routers/conversations_admin.py`, `Depends(verify_admin)`). No visitor-facing read endpoint existed. No delete function of any kind existed (only the bulk, time-based `purge_expired()`).

### What this means for "reopen"

Reopening a past conversation does not need new conversation-continuation logic. It only needs to **rebind the `padyar_conv` cookie** to the chosen conversation id (the same `set_cookie` call `/chat` itself makes) after an ownership check. The very next `/chat` call then runs its own existing `continuable_conversation_id()` check, sees the visitor owns it, and keeps appending to that same thread — no changes needed to `/chat` itself.

### Frontend: the skeleton already existed

Phase 1 built the drawer's history section as a hidden placeholder (`#menu-history`, `#menu-history-list`, and the `.menu-history-item`/`.menu-history-delete` CSS in `static/chat/base.css`) specifically so Phase 2 would only need to fill it in, not build new UI chrome. `document.documentElement.dataset.visitor` (`'in' | 'out' | 'unknown'`) is already written by `static/companion/registration.js` once the server answers `GET /api/auth/session` — the frontend reads that instead of probing the server a second time to decide whether to show the section at all.

## Risks Discovered

| Risk | Impact | Mitigation |
|------|--------|------------|
| A signed-in visitor could read another visitor's transcript by guessing a conversation id | Privacy breach (IDOR) | Every read/delete function takes the SESSION's visitor_id as a parameter and checks it against the row's `visitor_id` in the query itself — never trusts an id from the URL alone. A mismatch or missing row returns the same 404 either way, so the endpoint can't be used to probe which ids exist. |
| SQLite does not enforce foreign keys | Deleting a conversation could leave orphaned message rows | `delete_conversation_for_visitor()` deletes `messages` then `conversations` explicitly, same two-step order `purge_expired()` already established for exactly this reason. |
| Deleting the visitor's currently-ACTIVE conversation (the one their `padyar_conv` cookie points at) | Confusing state? | Checked against `continuable_conversation_id()`'s existing behavior: a cookie pointing at a now-deleted row is treated identically to "first message of a brand-new conversation" (its own documented behavior for "no row yet") — `get_or_create_conversation(..., visitor_id=visitor_id)` recreates the row and immediately re-stamps it with the visitor's ownership. No orphaned or insecure state; a clean, if slightly surprising, fresh start under the same id. Left as-is rather than adding client-side detection, since the cookie is HttpOnly and JS cannot even tell which conversation is "active" to special-case this. |

## Decision

Add three visitor-scoped functions to `app/services/conversations.py` (`list_conversations_for_visitor`, `get_conversation_for_visitor`, `delete_conversation_for_visitor`) that wrap the existing admin-facing reads/writes with an ownership check, three new endpoints in `app/routers/chat.py` (`GET /api/chat/conversations`, `GET /api/chat/conversations/{id}`, `DELETE /api/chat/conversations/{id}`), and fill in the Phase 1 drawer skeleton with real fetch/render/reopen/delete JS in `static/chat/core.js`. No new database tables, no new session mechanism, no changes to `/chat` itself.

## Next Step

- [x] Write spec in this folder: `features/visitor-chat-history/SPEC.md`
