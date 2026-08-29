# SPEC: Visitor-Facing "My Chats" (List, Reopen, Delete)

| Field | Value |
|-------|-------|
| Created | 2026-08-29 |
| Updated | 2026-08-30 |
| Status | Implemented |
| Domain | chat |
| Author | Sina Shamsizadeh (requested), drafted by Claude |

## Problem Statement

Phase 1 of `docs/features/hamburger-menu/` built the drawer's history section as a hidden placeholder, since no persistent visitor identity or message storage existed yet. Both now exist (built independently, for security reasons — see RESEARCH.md), so the placeholder can be filled in: a signed-in visitor can see their own past conversations, reopen one, and delete one.

## Goals

- A signed-in visitor sees a list of their own past conversations in the drawer, newest first.
- Clicking one replaces the visible chat with that conversation's messages and makes it the active conversation, so typing continues that thread.
- A visitor can delete their own conversation from the list.
- Nobody — not even a signed-in visitor — can read or delete a conversation that isn't theirs, by any id they might guess or be given.

## Non-Goals

- Not changing anonymous (not-signed-in) behavior at all — the history section stays hidden for them, exactly as Phase 1 left it.
- ~~Not adding pagination~~ — **superseded 2026-08-30**: the drawer now pages 10 at a time, loading more as the visitor scrolls `#menu-history` itself; see `docs/features/hamburger-menu/SPEC.md`'s Phase 3 note. `list_conversations_for_visitor`'s 100-row cap and offset cap stand regardless.
- Not touching `/chat` itself — reopening reuses its existing `continuable_conversation_id()` ownership check via a cookie rebind, rather than adding new continuation logic.
- Not handling "delete the conversation you're currently mid-typing-in" as a special case in the UI — see RESEARCH.md's Risks table for why the existing backend behavior already degrades safely there.

## Rationale

Every read/write needed already existed in an admin-only form (`list_conversations`, `conversation_messages`, the delete pattern from `purge_expired`). The visitor-facing versions are thin wrappers that add exactly one thing the admin versions don't need: binding every query to the session's own `visitor_id`, never a value from the URL or request body. This matches `app/auth/visitor.py`'s own stated rule ("a visitor is whoever the SERVER says they are") applied to conversation ownership instead of session identity.

Reopening a conversation via cookie rebind (rather than inventing a parallel "active conversation" concept) means `/chat`'s existing, already-tested ownership logic (`continuable_conversation_id`) is the only place that decides what a message continues — one rule, not two that could drift apart.

## Scope

`app/services/conversations.py` (three new functions), `app/routers/chat.py` (three new endpoints, one import added), `static/chat/core.js` (fetch/render/reopen/delete JS, filling in the Phase 1 markup/CSS). No database migration — reuses the existing `conversations`/`messages` schema.

## User Stories

- US-1: As a signed-in visitor, I want to see a list of my own past conversations, so I can find one again without re-asking the same question.
- US-2: As a signed-in visitor, I want to tap a past conversation and keep talking in it, so my next message has the same context as before.
- US-3: As a signed-in visitor, I want to delete a conversation I don't want kept, so it stops showing up and stops being answerable-from.
- US-4: As any visitor, I want it to be impossible for someone to read or delete a conversation of mine just by knowing or guessing its id.

## Functional Requirements

- REQ-001: `GET /api/chat/conversations?offset=N` returns one page (`MENU_HISTORY_PAGE_SIZE` = 10) of the current session's own conversations (id, timestamps, message count, a short text preview) plus `has_more`, gated by `Depends(visitor_auth.require_visitor)` — anonymous gets a 401 with the `registration_required` marker, same as every other visitor-only endpoint. Added 2026-08-30 — see `docs/features/hamburger-menu/SPEC.md`'s Phase 3 note.
- REQ-002: `GET /api/chat/conversations/{id}` returns one conversation's full message list, only when `id` belongs to the session's visitor_id (checked in the service layer, never trusting the URL). A non-owned or nonexistent id returns the same 404 either way. As a side effect, it rebinds the `padyar_conv` cookie to `id`.
- REQ-003: `DELETE /api/chat/conversations/{id}` deletes the conversation's messages then the conversation row, only when owned by the session's visitor_id. Same 404-either-way rule as REQ-002.
- REQ-004: The drawer's `#menu-history` section is fetched and rendered once per drawer-open (not on every page load), and stays hidden whenever `document.documentElement.dataset.visitor !== 'in'` or the list is empty.
- REQ-005: Clicking a history row clears the visible chat, replays that conversation's messages instantly (no typewriter animation), switches to the chat tab, and closes the drawer.
- REQ-006: Clicking a row's delete button asks for confirmation, then removes it from both the server and the visible list on success.

## Non-Functional Requirements

### Security
- SEC-001: Every new function/endpoint takes the visitor_id as the SESSION's own resolved id, never as a parameter from the URL, query, or body — matching `app/auth/visitor.py`'s stated identity rule.
- SEC-002: Ownership mismatches and "doesn't exist" are indistinguishable from the outside (same 404), so the endpoints can't be used to enumerate other visitors' conversation ids.
- SEC-003: All three new endpoints validate request origin (`validate_request_origin`), matching the existing per-visitor endpoint convention in this router, even though two of the three are reads.

### Reliability
- REL-001: Message deletion removes from `messages` before `conversations` (SQLite enforces no foreign keys), matching the existing `purge_expired()` pattern — no orphaned rows on either backend.

## Technical Design

### Architecture

Three new service functions in `app/services/conversations.py`, placed in a new "Visitor-facing reads / writes" section ahead of the existing "Admin reads" section:
- `list_conversations_for_visitor(visitor_id, *, limit=30)` — wraps `list_conversations(visitor_id=...)`, adds a `preview` field per row (first message's text, trimmed to 60 chars) via one extra `conversation_messages(id, limit=1)` call per row — accepted N+1, same trade-off `conversations_admin.py`'s `/conversations/weak` already makes for a short, per-page list.
- `get_conversation_for_visitor(conversation_id, visitor_id)` — wraps `get_conversation()` + `conversation_messages()`, returns `{}` unless `conv["visitor_id"] == visitor_id`.
- `delete_conversation_for_visitor(conversation_id, visitor_id)` — checks ownership with one `SELECT`, then deletes `messages` then `conversations` on the same connection, matching `purge_expired()`'s two-step order.

Three new endpoints in `app/routers/chat.py`, placed right after the existing `POST /api/chat/new-conversation`:
- `GET /api/chat/conversations`
- `GET /api/chat/conversations/{conversation_id}` — also calls `response.set_cookie("padyar_conv", conversation_id, ...)` with the same attributes `/chat` itself uses, making the opened conversation the active one.
- `DELETE /api/chat/conversations/{conversation_id}`

Frontend: `static/chat/core.js` gained `refreshMenuHistory()`, `renderMenuHistory()`, `openMenuHistoryItem()`, `deleteMenuHistoryItem()` as top-level functions (matching the file's existing mix of IIFEs and plain functions), called from the drawer's existing `openMenu()` — one line added there (`refreshMenuHistory();`), no other Phase 1 code touched.

### API Changes

| Method | Endpoint | Description |
|--------|----------|--------------|
| GET | `/api/chat/conversations?offset=N` | One 10-row page of the current visitor's own conversations, plus `has_more` |
| GET | `/api/chat/conversations/{id}` | Replay one conversation's messages; makes it active |
| DELETE | `/api/chat/conversations/{id}` | Delete one conversation the visitor owns |

### Database Changes

None — reuses the existing `conversations`/`messages` tables from migration 0010.

## Dependencies

- `app/auth/visitor.py` (session/identity) and `chat.py`'s message-persistence wiring, both delivered by an unrelated, already-merged security effort.

## Risks

See RESEARCH.md's Risks table — IDOR via guessed ids (mitigated), SQLite FK non-enforcement (mitigated), deleting the active conversation (degrades safely, not specially handled).

## Testing Strategy

- Manual/browser verification (required): sign in as a visitor, send a few messages, open the drawer and confirm the conversation appears with a preview; open a second conversation, delete the first from the list, confirm it's gone; reopen the second, send a new message, confirm it's appended to that same conversation (check via the admin conversations panel or the DB directly); confirm a signed-out visitor never sees the history section at all.
- `.venv/bin/python -m pytest` — full suite, comparing against the known 15 pre-existing environment-only failures (CLAUDE.md).

## Rollout Plan

1. Add the three service functions, `py_compile` check.
2. Add the three router endpoints, `py_compile` check.
3. Fill in the frontend JS, `node --check` on `core.js`.
4. Browser-verify the full list → reopen → continue → delete loop.
5. Run the full test suite.

## Success Criteria

- SC-001: A signed-in visitor can list, reopen, and delete their own conversations from the drawer; an anonymous visitor sees no history section.
- SC-002: No way to read or delete another visitor's conversation via a guessed or supplied id (returns 404 either way).
- SC-003: Zero regressions in the existing `pytest` suite.

## Open Questions

None — this phase was fully scoped by `docs/features/hamburger-menu/SPEC.md`'s already-answered OQ-A/OQ-B (revised 2026-08-29, see that file).
