#!/usr/bin/env python3
"""Package the OTP (SMS sign-up / verification) module for another install.

Copies exactly the files the module owns into a self-contained folder, with an
install README, so the same verification step can be added to a different
Padyar-based chatbot (e.g. another event's installation) without carrying this
project's branding or content.

The module is brand-neutral by construction: name, mark, palette, background
and companion avatar all come from settings (app/routers/otp.py :: branding()),
so the destination install customizes it from its own admin settings table
rather than by editing the copied code.

USAGE (from the project root)
-----------------------------
    python3 scripts/export-otp-module.py                 # → dist/otp-module/
    python3 scripts/export-otp-module.py --out /tmp/otp  # custom destination
    python3 scripts/export-otp-module.py --zip           # also produce a .zip

Nothing is deleted or modified in this project — the script only reads.
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (source path, destination path inside the package)
FILES = [
    ("app/services/otp.py", "app/services/otp.py"),
    ("app/routers/otp.py", "app/routers/otp.py"),
    ("templates/otp/verify.html", "templates/otp/verify.html"),
    ("static/otp/otp.css", "static/otp/otp.css"),
    ("static/otp/otp.js", "static/otp/otp.js"),
    ("static/companion/companion.js", "static/companion/companion.js"),
    # SMS gateway adapter + the sign-up CTA that drives the flow from a chat
    # page. `app/routers/otp.py` imports the planner, so it travels too.
    ("app/services/sms.py", "app/services/sms.py"),
    ("app/services/visit_plan.py", "app/services/visit_plan.py"),
    ("static/companion/registration.js", "static/companion/registration.js"),
    ("tests/test_otp.py", "tests/test_otp.py"),
    ("tests/test_visit_plan.py", "tests/test_visit_plan.py"),
]

# Optional extras: the companion avatar. A destination install with its own
# character (or none) skips these.
OPTIONAL = [
    ("static/otp/pet/inotex-pose-atlas-hd.webp", "static/otp/pet/inotex-pose-atlas-hd.webp"),
    ("static/otp/pet/inotex-fallback-hd.webp", "static/otp/pet/inotex-fallback-hd.webp"),
]

README = """# OTP Verification Module (Padyar)

SMS-based verification / sign-up step: a `/verify` page plus
`/api/auth/otp/{request,verify,resend,status}`.

Self-contained and brand-neutral — no content, colors or character of the
source installation are baked in.

## Install

1. Copy the folders in this package over the destination project root, keeping
   the same layout (`app/`, `templates/`, `static/`, `tests/`).

2. Register the module in `app/modules/registry.py`:

   ```python
   "otp": ModuleDef(
       name="otp",
       description="OTP verification step (/verify + /api/auth/otp/*)",
       is_core=False,
       router_module="app.routers.otp",
   ),
   ```

3. Enable it. Either leave `ENABLED_MODULES` empty (all optional modules load)
   or add `otp` to the list:

   ```
   ENABLED_MODULES=theme,voice,video,otp
   ```

4. Restart. The `otp_challenges` table is created automatically on first use.

## Requirements in the destination project

The module reuses primitives every Padyar install already has:

| Needs | Provided by |
|---|---|
| `get_db_connection()` | `app/db/connection.py` |
| `get_setting()` | `app/db/queries.py` |
| `check_rate_limit()`, `_get_hmac_key()` | `app/auth/security.py` |
| `BASE_DIR`, `COOKIE_SECURE`, `logger` | `app/config.py` |

No new Python dependency.

## Branding (all optional, stored in the `settings` table)

| Key | Default | Meaning |
|---|---|---|
| `otp_brand_name` | `INOTEX` | Wordmark next to the logo |
| `otp_brand_mark` | INOTEX hexagon SVG | Inline SVG for the logo |
| `otp_companion_atlas` | INOTEX atlas path | **Empty string = no companion avatar** |
| `otp_companion_cell` | `512` | Atlas cell size in px (4 columns, 12 poses) |
| `otp_color_primary` / `_hover` | `#FCB715` / `#FEBE27` | Primary action |
| `otp_color_blue` / `_navy` / `_teal` | INOTEX palette | Surfaces and accents |
| `otp_color_background` | `#000000` | Page background |
| `otp_background_image` | INOTEX bricks | Background image URL (may be empty) |

Set these from the destination install's admin/settings layer — do not edit the
copied source.

## Behavior configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `OTP_LENGTH` | `6` | Digits in the code |
| `OTP_TTL_SECONDS` | `120` | Code lifetime |
| `OTP_RESEND_COOLDOWN` | `45` | Seconds before resend is allowed |
| `OTP_MAX_ATTEMPTS` | `5` | Wrong attempts per challenge |
| `OTP_MAX_RESENDS` | `3` | Resends per challenge |
| `OTP_DEST_HOURLY_LIMIT` | `5` | New challenges per destination per hour |
| `OTP_DELIVERY` | `dev` | Delivery provider (see below) |

## Delivery provider — READ BEFORE PRODUCTION

Only the `dev` provider ships with this package: it appends codes to
`data/otp-dev-outbox.log` for local development and **refuses to run when
`COOKIE_SECURE=true`** (the project's production marker), returning 503
instead of writing codes to disk.

A real SMS gateway plugs into `_deliver()` in `app/services/otp.py`. The code
is never returned by any API response in any provider.

## Security properties (covered by tests/test_otp.py)

- Codes generated with `secrets`; only a keyed HMAC-SHA256 is stored
- Constant-time comparison; single-use; replay refused
- Expiry, attempt limit, resend limit, per-destination rate limit — all
  enforced server-side; the UI timer is presentation only
- Raw code never stored, never logged, never returned
- Destinations masked in responses and audit lines

Run `pytest tests/test_otp.py` in the destination project to verify.

## Targeted visit — REPLACE THE TAXONOMY

`app/services/visit_plan.py` ranks an event's sections against what the visitor
said about their work. The matching logic is generic; the section list is NOT —
it is INOTEX 2026's official programme.

**Before using this in another event, replace `SECTIONS` and `FALLBACK_IDS`
with that event's own verified sections**, and keep the `note` honest about
what has and has not been published. `tests/test_visit_plan.py` pins the
behaviour (a plan is never empty, never names something outside the taxonomy,
and an empty profile never produces a fake match) — update its Persian
fixtures alongside the taxonomy.

Removing the feature instead: drop `visit_plan.py`, its test, and the
`/api/visit-plan` endpoint from `app/routers/otp.py`. Nothing else depends on it.
"""


def main() -> int:
    p = argparse.ArgumentParser(description="Export the OTP module as a portable package.")
    p.add_argument("--out", default=str(ROOT / "dist" / "otp-module"))
    p.add_argument("--no-avatar", action="store_true", help="Skip the companion avatar assets.")
    p.add_argument("--zip", action="store_true", help="Also write a .zip archive next to the folder.")
    args = p.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    missing = []
    for src, dst in FILES:
        s = ROOT / src
        if not s.exists():
            missing.append(src)
            continue
        d = out / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        print(f"  + {dst}")

    if missing:
        sys.exit("✗ missing module files: " + ", ".join(missing))

    if not args.no_avatar:
        for src, dst in OPTIONAL:
            s = ROOT / src
            if s.exists():
                d = out / dst
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
                print(f"  + {dst}  (optional avatar)")

    (out / "README.md").write_text(README, encoding="utf-8")
    print(f"  + README.md")

    if args.zip:
        archive = shutil.make_archive(str(out), "zip", root_dir=out)
        print(f"\n✓ package  {out}\n✓ archive  {archive}")
    else:
        print(f"\n✓ package  {out}")
    print("  Install steps are in the package README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
