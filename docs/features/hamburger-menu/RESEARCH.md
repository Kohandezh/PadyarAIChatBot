# Research: Chat header cleanup (hamburger menu, RTL input fix, speaker removal)

| Field | Value |
|-------|-------|
| Feature | Move the cluttered public-chat header into a hamburger drawer; fix RTL/LTR input alignment; remove the composer's speaker button |
| Date | 2026-08-29 |
| Status | Done |

## Why This Research

The request read like a same-day UI reshuffle, but one part of it ("show the visitor's past chats, let them delete their own chats, add login/logout") depends entirely on what the chat backend already persists per visitor. That had to be checked before any scope or timeline could be promised.

## Questions to Answer

- What does each theme's header currently contain, and which controls are already wired in a way that's safe to relocate without rewriting?
- Does `dir="auto"`/RTL handling already work for the input box, or is there a real bug — and where?
- What does the composer's speaker button actually control, and is anything else relying on it?
- Is there a working, persistent visitor identity today? Can visitors' past chats already be listed/deleted server-side?

## Findings

### Header contents, per theme

| Control | base/minimal | inotex | liquid-glass | haj |
|---|---|---|---|---|
| Logo | yes | yes | yes | yes |
| Chat/video tab switcher | no | yes | yes | yes |
| `#new-chat-btn` (text pill) | yes | yes | yes | yes |
| `#lang-btn` (fa/en) | yes | yes | yes | **no — Persian-only by design** |
| `#theme-btn` / `#haj-theme-toggle` (dark/light) | **no** | yes | yes | yes |
| `.accessibility-controls` (font size, behind its own mini hamburger) | yes | yes | yes | yes |

Every one of these is wired by `document.getElementById('...')` in `core.js` or the theme's own `footer.html` script. None of that lookup code cares where in the DOM the element lives — so relocating the markup into a shared drawer, keeping the same `id`, is a move, not a rewrite. The `haj` theme has no language switch on purpose (code comment: "No language control: this install is Persian-only... an English switch could only ever lead to Persian answers under an English label") — the drawer must preserve that, not add one back.

One more thing found: `.accessibility-controls` already opens its own small dropdown from its own hamburger-glyph icon. Today's header can already show **two different hamburger icons** side by side (a11y's and, after this change, the new main one) unless the a11y control is folded into the same drawer. Folding it in also removes that ambiguity.

### RTL/LTR: real bug is narrower than the request implied

The document `dir` is hard-coded to `rtl` everywhere, always — `setLang()` in `core.js` only ever writes `html.setAttribute('dir', 'rtl')`; only `lang` changes between `fa`/`en`. This is a deliberate decision (code comment: "The layout never mirrors: switching language changes the words, not the room").

- `inotex`, `liquid-glass`, `haj` textareas already carry `dir="auto" lang="fa"` on `#user-input`.
- **`base`'s textarea (inherited by `minimal`) has neither attribute** — a real, confirmed bug: in the `minimal` theme, English text currently renders right-aligned/RTL-flowed regardless of language.
- The pasted CSS's `.composer-studs` worry is a non-issue: that's a decorative dot cluster in `inotex` only, already positioned with the logical property `inset-inline-start`, which already adapts to direction correctly. No fix needed there.
- The pasted CSS's `flex-direction: row-reverse` for `.input-wrapper` in English mode would reorder the mic/send buttons specifically in English — that directly contradicts the "layout never mirrors" decision above. Not implementing it.

### The speaker button (`#tts-btn`) is genuinely dual-purpose, and it's the ONLY working mute control

Exists in `inotex`, `liquid-glass`, `haj` (not in `base`/`minimal`, which never had it). By design (code comment) it does two different things depending on the active tab: on the chat tab it toggles "read answers aloud" (SpeechSynthesis TTS); on the video tab it mutes/unmutes the video. The other mute-related ids referenced in the JS — `#video-sound`, `#unmute-btn` — have **no matching HTML in any current theme**; they're dead references. So removing `#tts-btn` removes both the read-aloud feature and the only functioning way to unmute a video that autoplayed muted (browsers sometimes block unmuted autoplay even on a user-gesture-triggered send).

Also found a latent bug worth fixing as part of the removal, not after: the TTS "on" flag is read straight from `localStorage` regardless of whether the button exists. If the button markup is deleted but the toggle logic isn't, a returning visitor who previously turned it on would get unstoppable narration with no control left to turn it off.

### Backend: chat history / login is NOT a small addition

A sub-agent traced this in full (see its report in session context). Headline: `visitors`, `conversations`, `messages` tables exist (recent migration) but **nothing writes to them today** — `chat.py` never calls the conversation-logging service; the transcript a visitor sees on reload is replayed from `localStorage` only. Identity today is an anonymous, single-browser, 24-hour `padyar_conv` cookie — there is no persistent, cross-session visitor identity. `app/services/conversations.py` has no visitor-scoped list function and no single-conversation delete function (only a bulk, time-based retention purge) — both would be new. The OTP/registration flow's `/api/auth/otp/verify` returns a display-only object that the frontend just stores in `localStorage['inotex-visitor']`; that's what today's UI treats as "logged in" — it's not a real server session and can't be trusted as an ownership boundary for deleting data. The registration flow doesn't even call the DB's visitor-creation function today.

"Visitors can see and delete their own past chats, gated by login" therefore means: design a persistent visitor-identity mechanism, wire `chat.py` to actually persist messages, and build new list/delete endpoints with ownership checks — a backend project with real security implications, not a header reshuffle.

## Sources

- Direct reads: `themes/*/partials/{header,input,footer,video,index}.html`, `static/chat/core.js`, `static/chat/base.css`, `themes/*/static/style.css` (relevant sections).
- Sub-agent investigation of `app/db/connection.py`, `app/services/conversations.py`, `app/routers/chat.py`, `app/routers/otp.py`, `static/companion/registration.js`, `migrations/0009_conversation_memory.sql`, `migrations/0010_conversations.sql`.

## Risks Discovered

| Risk | Impact | Mitigation |
|------|--------|------------|
| Treating "chat history + login" as same-day UI work | Underscoped promise, half-built auth surface | Split into Phase 1 (pure frontend) / Phase 2 (backend, blocked on product decisions) — see SPEC.md |
| Deleting `#tts-btn` markup without removing its `localStorage`-driven logic | Speech starts with no way to stop it for visitors who had it on | REQ-005/REL-001 in SPEC.md: remove the logic, not just the button |
| Two hamburger-glyph buttons in one header after adding the new one | Fails the project's own 3-second-clarity bar | Fold `.accessibility-controls` into the same new drawer |

## Decision

Split the work into two phases. Phase 1 (this spec's P0) is a same-day, backend-free frontend reorg: shared hamburger-drawer partial, RTL input fix, `#new-chat-btn` icon-ification, full `#tts-btn` removal. Phase 2 (chat history + real login) is deliberately left as an open-question-gated follow-up — see `SPEC.md`.

## Next Step

- [x] Write spec in this folder: `features/hamburger-menu/SPEC.md`
