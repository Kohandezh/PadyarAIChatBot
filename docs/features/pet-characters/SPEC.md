# Pet Characters

Status: Implemented (2026-08-31, owner request)
Owner: Sina (Malik-e product)

## Scenario

Elecomp runs the same Padyar install as INOTEX but is a different brand
with its own mascot. The operator opens **Settings > Branding → «شخصیت
همراه»**, picks «الکامپ» from the dropdown, presses «ذخیره شخصیت» — and the
next visitor's companion is the elecomp bird, with its own poses (it soars
when an answer lands). No deploy, no theme edit. A new mascot is a folder
drop, not a code change.

## What shipped

- **Registry:** `static/otp/pet/characters/{name}/character.json` —
  atlas URL, cell size, **column count**, fallback, optional hide strip,
  and two pose maps (`pose_index`: pose → atlas frame; `state_poses`:
  companion state → pose). `app/services/pet_characters.py` scans,
  validates (slug names, sane ints, an `idle` mapping is mandatory) and
  skips anything defective — a half-loaded character is worse than none.
- **Setting:** `pet_character` (default `inotex`). Unknown stored value →
  the default. Baked into the cached chat shell, so its identity rides the
  page-cache key (`pet_character_cache_key`, wired in themes.py).
- **Markup:** footer.html's `#pet-canvas` data attributes come from the
  context (`pet_atlas_url`, `pet_cell`, `pet_columns`, fallback, hide
  strip, and the two pose maps as html-escaped JSON in single-quoted
  attributes).
- **Renderer:** `static/companion/companion.js` no longer hardcodes the
  INOTEX grid — `COLS` reads `data-columns`, and `POSE`/`STATE_POSE` merge
  the character's maps over the INOTEX defaults. An unmapped pose falls
  back to the character's idle frame instead of drawing nothing.
- **Admin:** GET/POST `/admin/api/pet-character` + a card on the branding
  page (dropdown + portrait preview + save). The list is the registry —
  the operator never types a name.
- **Characters bundled:** `inotex` (points at the existing flat HD assets —
  default pixels unchanged) and `elecomp` (atlas 3×4 @384 from
  Elecomp-Pet/Avatar; success → `flight-soar`, error → `front-wings`,
  flap → `flight-dive`; no hide strip → instant hide, which companion.js
  already treats as legal).

## Known bounds (deliberate)

- The **OTP page** keeps its own `otp_companion_atlas` / `otp_companion_cell`
  settings (app/routers/otp.py) — per-install already. Pointing an elecomp
  install's OTP page at the new atlas is a settings row, and threading the
  pose maps through that page is a follow-up if wanted.
- The inotex character.json intentionally points at the **flat** asset
  paths (`/static/otp/pet/inotex-*`) so the OTP default and any saved OTP
  settings keep resolving. Consolidating the files into the character
  folder can happen later with a path migration.
- Adding a NEW mascot is a developer task (folder + character.json with a
  generated atlas); the admin UI only chooses among registered ones.

## Tests

`tests/test_pet_characters.py` — registry discovery + validation fallback,
default render, cached-shell flip on save, admin API (list/save/reject/
auth), and the renderer contract (per-character columns + maps + the
never-blank idle fallback).
