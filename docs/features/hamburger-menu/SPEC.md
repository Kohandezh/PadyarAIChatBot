# SPEC: Chat Header Cleanup — Hamburger Menu, RTL Input Fix, Speaker Removal

| Field | Value |
|-------|-------|
| Created | 2026-08-29 |
| Updated | 2026-08-29 |
| Status | Implemented (both phases). Phase 1 verified on all 4 themes, full test suite green. Phase 2 unblocked once `origin/main` independently shipped a real visitor-session system (see Open Questions below) — implemented on top of it, see `docs/features/visitor-chat-history/`. |
| Domain | chat |
| Author | Sina Shamsizadeh (requested), drafted by Claude |

## Problem Statement

The public chat header shows too many always-visible controls at once (tab switcher, new-chat, language, dark/light, accessibility) — across four themes, in different combinations — which fails this project's own bar of "every screen understandable in under 3 seconds." Separately, the input box doesn't consistently align text for Persian vs. English (one theme is missing the fix entirely), and the composer's speaker button conflates two unrelated jobs (read-aloud, video mute) in a way the product owner wants removed outright.

## Goals

- Header shows only identity + core navigation + one clear menu entry point, on every theme.
- Every visitor sees correctly left/right-aligned text in the input box, in both languages.
- Language, theme (dark/light), and text-size controls keep working exactly as they do today — just relocated.
- The composer no longer has a speaker/mute control of any kind.
- (Phase 2, blocked on Open Questions) Visitors can review and manage their own past conversations from the same menu.

## Non-Goals

- **Not building persistent cross-session visitor login in Phase 1.** Research found this needs a new identity mechanism, `chat.py` wiring, and new endpoints — a backend project, not a UI relocation. See Phase 2 below.
- **Not changing the chat/video tab switcher, the logo, or what "new chat" does** — only its visual presentation (icon instead of text label).
- ~~Not adding dark-mode to `base`/`minimal`~~ — product owner opted in (Open Question C, resolved 2026-08-29); done, see Open Questions below.
- **Not preserving read-aloud (TTS) under a different control.** The product owner asked to remove the speaker button "completely"; that removes TTS too, not just video-mute. Confirmed as intentional in Open Question D.
- **Not adding a language switch to the `haj` theme.** It's Persian-only by an existing, deliberate product decision — out of scope to revisit here.

## Rationale

Every control being relocated (language, theme, text-size) is already looked up by `document.getElementById(...)` from JS that doesn't care where in the DOM the element lives. So the plan is a **relocation, not a rewrite**: move the existing markup (same `id`s, same classes) into one new shared drawer partial, and the existing per-theme JS keeps working unchanged. This keeps Phase 1 low-risk and same-day, and avoids duplicating four themes' worth of toggle logic into a new abstraction that would just be a second copy of what already works.

The alternative — building a brand-new settings state/store and re-wiring every control to it — was rejected: it would touch far more code for the same visible outcome, and this project's own principle is "no unnecessary abstraction... three similar lines is better than a premature abstraction."

## Scope

**Phase 1 (in scope now):** `themes/*/partials/header.html` (all 4 themes), `themes/base/partials/index.html` and `themes/haj/partials/index.html` (to include the new drawer partial), a new `themes/base/partials/menu.html`, `static/chat/core.js` (drawer open/close wiring, TTS-flag guard), `static/chat/base.css` (drawer structural CSS, RTL/LTR input rule), each theme's `static/style.css` (drawer color theming, removal of orphaned `.tts-btn` rules), `themes/{inotex,liquid-glass,haj}/partials/input.html` and `footer.html` (remove `#tts-btn` and its logic).

**Phase 2 (blocked, not started):** would additionally touch `app/routers/chat.py`, `app/routers/otp.py`, `app/services/conversations.py`, new router endpoints, and a new cookie/identity mechanism. None of this is built until Open Questions A/B are answered.

## User Stories

- US-1: As a visitor, I want the header to show only a few things, so I'm not confused about what to tap.
- US-2: As a visitor, I want one menu where language, theme, and text-size controls live in a predictable place.
- US-3: As an English-speaking visitor, I want my typed text to read left-to-right in the input box.
- US-4: As a kiosk visitor, I want one obvious "+" to start a new chat without needing to read a label.
- US-5 *(Phase 2, blocked)*: As a returning, logged-in visitor, I want to see my past conversations and reopen or delete any of them.

## Functional Requirements

**P0 — Phase 1, must-have:**

- REQ-001: Header keeps only: logo, chat/video tab switcher (themes that have one), `#new-chat-btn` restyled icon-only (a plain "+", `aria-label`/`title` text preserved for screen readers, visible text label removed), and one hamburger-menu toggle button.
- REQ-002: New shared drawer (`menu.html`) contains, top to bottom: (1) a chat-history section — in Phase 1 this is a placeholder/not rendered, see Open Question A; (2) language switch — omitted entirely on installs/themes without one (e.g. `haj`), exactly as today; (3) dark/light toggle — omitted on themes without one today unless Open Question C changes that; (4) text-size control (today's `.accessibility-controls` A+/A-); (5) login/logout — Phase 1 relocates today's existing client-side `#visitor-logout` control as-is, only rendered when the registration module is enabled, see Open Question B.
- REQ-003: Drawer opens/closes from the hamburger button; closes on outside click, Escape, or after a navigating selection — same interaction pattern as the existing `.a11y-dropdown` open/close code in `core.js`, generalized rather than duplicated.
- REQ-004: `#user-input` aligns deterministically by UI language: `html[lang="fa"] #user-input` → right/RTL, `html[lang="en"] #user-input` → left/LTR, added to `base.css` so all themes get it once. `dir="auto" lang="fa"` added to the `base` theme's textarea (the concrete, confirmed bug — fixes `minimal`).
- REQ-005: `#tts-btn` — markup, per-theme CSS rules, and JS (both the read-aloud toggle and the video mute/unmute toggle it drove, in `inotex`, `liquid-glass`, and `haj`) are fully removed. No replacement control added.
- REQ-006: The submitted CSS snippet's `.composer-studs` and `.input-wrapper { flex-direction: row-reverse }` ideas are explicitly NOT implemented (see RESEARCH.md — not applicable / contradicts an existing deliberate no-mirroring decision).

**P1 — nice-to-have, Phase 1:**

- REQ-007: Drawer row order matches the request exactly (history → language → theme → text-size → login/logout) so the layout reads the same as what was asked for, once Phase 2 fills in row 1.

**P2 — Phase 2, blocked on Open Questions A and B:**

- REQ-008 — **Done, see `docs/features/visitor-chat-history/`.** `list_conversations_for_visitor()` and `delete_conversation_for_visitor()` in `app/services/conversations.py`, plus `GET/DELETE /api/chat/conversations[/…]` router endpoints with ownership checks.
- REQ-009 — **Superseded, done independently.** A separate security-remediation effort (unrelated to this feature) shipped `app/auth/visitor.py`'s HttpOnly session cookie and wired `chat.py` to `conversations.py`'s write functions before this requirement was ever picked up. Phase 2 built on top of that rather than building it.
- REQ-010 — **Done, see `docs/features/visitor-chat-history/`.** Drawer history section lists real conversations, click reopens one (and makes it the active conversation server-side), and each has a delete action a visitor can only use on their own conversation.

## Non-Functional Requirements

### Performance
- PER-001: Phase 1 introduces no new network calls — everything relocated is already-loaded state/markup.

### Security
- SEC-001: Phase 1 has no backend surface, so no new security review needed.
- SEC-002 *(Phase 2)*: The new persistent visitor-identity cookie needs the same treatment as the existing `padyar_conv` cookie (`httponly`, `secure` from `COOKIE_SECURE`, `samesite="lax"`) and the delete endpoint must check conversation ownership server-side, not trust a client-supplied id alone.

### Reliability
- REL-001: Removing `#tts-btn` must also stop the `localStorage`-driven "TTS on" flag from producing speech with no visible control — guard the speak function on "control exists" (or clear/ignore the flag), so a visitor who had it enabled before this change doesn't get stuck with unstoppable narration after.
- REL-002: The drawer must render correctly (no broken layout, no missing rows) on all 4 themes — each has different header chrome today.

## Technical Design

### Architecture — ADR-019 (see `docs/engineering/DECISIONS.md` for the short pointer entry)

**Decision:** one new shared partial, one shared open/close behavior, and pure relocation of existing controls — no rewrite of any control's own logic, no per-theme drawer duplication, no new JS framework/pattern.

**Options considered:**

| Option | Complexity | Why rejected / accepted |
|---|---|---|
| **A. Shared partial + relocate existing controls (chosen)** | Low | Every control is already `getElementById`-driven and DOM-position-agnostic (confirmed in research). Moving markup costs nothing behaviorally. One file to maintain. |
| B. Duplicate the drawer markup/CSS into each theme (matching how header.html is already duplicated today) | Medium-High | Rejected — 4x the markup for a component that has no theme-specific *behavior*, only color, which `currentColor` + CSS variables already solve (precedent: `#new-chat-btn` in `base.css`). Pure maintenance cost with no upside. |
| C. New JS web component (`<hamburger-drawer>`) | High | Rejected — this codebase has zero web components anywhere; introducing the pattern for one drawer is exactly the "premature abstraction" CLAUDE.md warns against. Plain DOM + one shared open/close function matches the existing `.a11y-dropdown` precedent already in `core.js`. |
| D. New client-side settings store (single source of truth object driving all toggles) | Medium | Rejected for Phase 1 — would require rewriting `InotexTTS`/theme-btn/lang-btn logic that already works per-theme with different `localStorage` keys. Real cost, zero visible benefit; revisit only if Phase 2 needs shared state the DOM can't carry. |

**Consequences:**
- *Easier later:* Phase 2's history list is just row 1 of the same `menu.html` — no new drawer plumbing needed when that work is unblocked.
- *Harder later:* because each theme's header markup isn't shared today, any FUTURE header change still needs 4 separate edits (base/minimal share one file, so really 3 edits: base, inotex+liquid-glass individually, haj). Not solved here — flagged as a possible follow-up refactor, out of scope for this feature.
- *Revisit if:* Phase 2 needs state shared between the drawer and elsewhere on the page beyond what a `MutationObserver` on `documentElement`/`body` classes already gives today's theme-btn/lang-btn code — at that point Option D becomes worth its cost.

### Component design

**New partial:** `themes/base/partials/menu.html` — one drawer `<aside>`/panel + backdrop, containing (in order) a history placeholder `<div id="menu-history" hidden>` (Phase 2 fills this in later, hidden in Phase 1 per REQ-002), the language switch markup (only theme installs that have `#lang-btn` today keep it — themes without one, e.g. `haj`, simply don't include that block in their own copy, exactly as `header.html` omits it today), the theme toggle markup, the text-size (`#a11y-btn` A+/A-) controls, and the login/logout row (`#visitor-logout`, only rendered when the registration module is enabled — reuse whatever server-side flag/Jinja conditional `header.html` already uses today to decide that, if any; otherwise this row stays JS-driven exactly as it is now).

**Include points (both required to reach every install):**
- `themes/base/partials/index.html` — add `{% include "menu.html" %}` once, inside `.app-layout` (reaches `base`, `minimal`, `inotex`, `liquid-glass`).
- `themes/haj/partials/index.html` — add the same include (reaches `haj`).

**Per-theme `header.html` changes (4 separate edits, one per theme file):** remove the language/theme/a11y control blocks; keep logo, tab switcher (where present), and `#new-chat-btn`; add one hamburger-toggle button with a **consistent contract across all 4 themes**: `id="menu-toggle"`, `aria-haspopup="dialog"`, `aria-controls="menu-drawer"`, `aria-expanded="false"` (kept in sync on toggle, matching how `#a11y-hamburger` already does it). `#new-chat-btn` keeps its `id` and `data-i18n`/`data-i18n-title="newChat"` attributes (for the tooltip/aria-label) but drops its visible text node in favor of a plus-icon SVG — `title`/`aria-label` already carry the accessible name, so no accessibility loss.

**Open/close behavior — generalizes the existing pattern, doesn't duplicate it:** the `.a11y-hamburger`/`.a11y-dropdown` open-on-click/close-on-outside-click block in `core.js` (~lines 987-1002) becomes a small reusable `bindDropdown(toggleEl, panelEl)` helper; both the a11y dropdown (if kept standalone) and the new `#menu-toggle`/`#menu-drawer` pair call it. Add `Escape`-to-close (not present in the original — small improvement, same helper). This is a refactor-in-place of ~15 existing lines, not new architecture.

**`#tts-btn` removal — file-by-file:**
- `themes/{inotex,liquid-glass,haj}/partials/input.html`: delete the `<button id="tts-btn">` block entirely.
- `themes/{inotex,liquid-glass,haj}/partials/footer.html`: delete the `InotexTTS`/`CompanionTTS`/`HajSpeech` IIFE **and** its call site (`if (type === 'bot') XxxTTS.speak(content);` inside each theme's `ChatConfig.addMessageFn`) — not a guarded stub. Deleting the whole read path means the `localStorage` "on" flag simply becomes inert dead data (never read), which satisfies REL-001 without needing a migration/cleanup step — nothing ever calls `speechSynthesis.speak()` again once the call site is gone, regardless of what's in storage.
- `themes/{inotex,liquid-glass,haj}/static/style.css`: remove `.tts-btn`-specific rules; update the combined selectors (`.mic-btn, .tts-btn, #send-btn { ... }`) to drop `.tts-btn`.
- No change needed to `core.js` itself for this removal — `unmute-btn`/`video-sound` references there are already-dead code (no matching HTML in any theme) and are left alone (out of scope; not introduced by this change, not made worse by it).

### API Changes

| Method | Endpoint | Description |
|--------|----------|--------------|
| — | — | None in Phase 1 |
| *(Phase 2, blocked)* | `GET /api/chat/conversations` | List the current visitor's own conversations |
| *(Phase 2, blocked)* | `DELETE /api/chat/conversations/{id}` | Delete one conversation the visitor owns |

### Database Changes

None in Phase 1. Phase 2 would reuse the existing `visitors`/`conversations`/`messages` tables (already migrated) — no new schema expected, only new queries and a new identity cookie.

## Dependencies

- Phase 2 is fully blocked on the product owner's answers to Open Questions A and B below — no backend code should be written until then.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| A theme's CSS assumes a relocated control sits inside the header for positioning/sizing | Broken layout on that theme | Browser-verify each of the 4 themes after the change, not just `inotex` |
| `#tts-btn` removal leaves the video tab with zero unmute control if a browser blocks autoplay-with-sound | Visitor can't hear an answer video that autoplayed muted | Accepted per explicit product request (REQ-005); flagged, not silently worked around |
| Phase 1 ships the drawer with an empty/placeholder history row, reading as "broken" rather than "not yet built" | Confusing half-feature | REQ-002 explicitly hides the history section in Phase 1 rather than rendering an empty state |

## Testing Strategy

- Unit tests: none needed for Phase 1 (no Python changes).
- Integration tests: n/a for Phase 1.
- Manual/browser verification (required per theme — `inotex`, `liquid-glass`, `minimal`, `haj`): hamburger opens/closes and closes on outside click; every relocated control (language, theme, text-size, logout-where-applicable) still works after relocation; `#user-input` aligns correctly typing in fa and en; `#tts-btn` is gone with no console errors and no orphaned "still speaking" state; existing `.venv/bin/python -m pytest` stays green (CLAUDE.md mandatory check, even though this phase is frontend-only, since Jinja partial changes are still server-rendered).

**Done (2026-08-29):** all of the above verified live in a browser against a running dev server, on all 4 themes, including the drawer's account row with a simulated signed-in visitor and logout. Three pre-existing tests encoded the old header layout as a permanent invariant (`tests/test_public_ui.py`, `tests/test_kiosk_privacy.py`, `tests/e2e/test_chat_localisation.py`) and were updated to check the new, intentional behavior — same pattern each time: `id="lang-btn"` moved from asserting on `header.html` to `menu.html`; `#new-chat-btn` assertions moved from checking a localized text node to checking only `title`/`aria-label` (REQ-001 made it icon-only); the old dual-purpose-speaker test was replaced with one asserting no sound control exists anywhere (REQ-005). Full suite: 1770 passed, 143 skipped, 15 failed — and all 15 are the exact pre-existing environment-only failures already documented in this repo's CLAUDE.md (need live PostgreSQL/network, unrelated to this change).

**Bug found during verification, not fixed here (flagged separately):** `static/companion/registration.js` is only ever `<script>`-included in `inotex/partials/footer.html` — `base`, `liquid-glass`, and `haj` never load it, so the entire visitor sign-up/OTP flow (and the login/logout row this feature adds) silently never activates on those themes today, regardless of this change. Pre-existing, unrelated to the hamburger menu — spawned as a separate task rather than folded into this one.

## Rollout Plan

1. Build `menu.html` + shared open/close JS + drawer CSS, land and verify on `inotex` (the richest theme) first.
2. Apply the shrunk `header.html` + drawer include to `liquid-glass`, `haj`, and base/`minimal`.
3. RTL/LTR input CSS fix (`base.css` + `base` theme's textarea attributes).
4. `#tts-btn` removal across `inotex`, `liquid-glass`, `haj` (markup, CSS, JS, including the REL-001 guard).
5. Browser-verify all 4 themes; run `pytest`.
6. Present Open Questions A–D to the product owner before scoping Phase 2 work.

## Success Criteria

- SC-001: Visible (non-drawer) header control count drops to 4 or fewer on every theme (logo, tabs where present, new-chat icon, hamburger).
- SC-002: Zero regressions in the existing `pytest` suite.
- SC-003: Manual check confirms correct fa/en input alignment on all 4 themes.
- SC-004: No console errors and no way to trigger speech with `#tts-btn` removed, including for a visitor with a pre-existing "TTS on" preference in `localStorage`.

## Open Questions

- **OQ-A/OQ-B — RESOLVED 2026-08-29, then REVISED 2026-08-29.** Product owner first chose "start small" (Phase 2 deferred indefinitely — that project sounded like a large, security-relevant build). Hours later, a separate, unrelated security-remediation effort landed on `main` and independently built exactly the persistent-identity/session system that decision was deferring (`app/auth/visitor.py`, `chat.py` wired to `conversations.py`'s writes). With that risk already retired by someone else's work, the product owner revisited the decision and asked for Phase 2 after all — see `docs/features/visitor-chat-history/` for what was actually built once the calculus changed.
- **OQ-C — RESOLVED 2026-08-29** (product owner chose "Ezafe kon" / add it): dark/light mode added to `base`/`minimal` too, matching the other three themes — same `inotex-light-mode` storage key as inotex's own toggle (one install only ever runs one active theme, so sharing the key is not a real conflict), `theme-btn` row added to a new `themes/minimal/partials/menu.html`, light-mode variable overrides added to `themes/minimal/static/style.css`. Verified live in the browser (round-trips light→dark→light correctly).
- **OQ-D — RESOLVED** (implemented as originally requested): the speaker button's full removal — both the read-aloud feature and the only working video-unmute control — was confirmed as intended by the original request's wording ("hazvesh kon kolan" / remove it completely) and shipped as-is in Phase 1.

## Phase 3 — Row visibility, logout restyle (2026-08-30)

Not in the original scope; a follow-up request once Phase 1+2 were live.

- **Layout**: language / theme-toggle / text-size / account are now wrapped
  in one `.menu-footer` div (`display: flex; flex-direction: column;`)
  pinned to the bottom of the drawer with `margin-top: auto`. `#menu-history`
  is the drawer's one internally-scrolling region (`flex: 1 1 auto;
  overflow-y: auto;`) — the drawer itself no longer scrolls as a whole. See
  `static/chat/base.css`.
- **Admin-toggleable rows**: `app/services/menu_settings.py` (four booleans,
  same key-value pattern as `whitelabel_*`/`idle_video_*`), a card on
  Settings → برندینگ, and `GET`/`POST /admin/api/menu-settings`. A flag only
  ever HIDES a row a theme already renders — haj still has no language row
  with the flag on, base still has no theme-toggle row. Rides the same
  rendered-page-cache-invalidation contract as branding/idle-video
  (`menu_settings_cache_key` folded into `app/services/themes.py`'s cache
  key tuples).
- **Logout restyle**: `#visitor-logout` (id unchanged — e2e tests depend on
  it) now carries `.menu-logout-btn` (`static/chat/base.css`, a fixed
  semantic red in every theme) plus an exit icon, and its Persian label is
  "خروج از سیستم" instead of "خروج" (`static/companion/registration.js`).
  Also newly admin-toggleable via `menu_show_logout` /
  `data-show-logout` on `#menu-account-section`.
- **"My chats" pagination**: see `docs/features/visitor-chat-history/SPEC.md`
  — the list that shipped un-paginated in Phase 2 now pages 10 at a time,
  loading more as the visitor scrolls `#menu-history` itself.
