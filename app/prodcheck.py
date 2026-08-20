"""Refuse to start a production install that is configured like a dev one.

WHY
---
Several settings are correct for local development and dangerous in
production, and every one of them fails SILENTLY: the app boots, serves
traffic, and looks healthy. An admin cookie without `Secure` still works over
HTTP — it just also travels in clear text. A `trust`-auth database still
connects — it just accepts anyone. The absence of a symptom is the problem.

WHICH INSTALLS ARE "PRODUCTION"
-------------------------------
`PADYAR_ENV`, and nothing else.

An earlier version of this module keyed off `COOKIE_SECURE=true`. That was
unsound, and the flaw is worth spelling out because it is easy to reintroduce:
COOKIE_SECURE is one of the settings this gate CHECKS. A real production server
that forgot to set it would therefore classify itself as development, skip the
validation entirely, and boot happily with insecure cookies — the exact
misconfiguration silently switching off the check that exists to catch it. A
gate must never be disabled by the thing it is guarding against.

`PADYAR_ENV` is independent: it describes what the install IS, not whether any
particular setting happens to be correct.

BEHAVIOUR
---------
development (the default)  -> nothing blocks; findings are informational.
staging                    -> nothing blocks; blockers are logged as warnings,
                              so a staging host can run on placeholder content
                              and a dev SMS outbox while still surfacing what
                              would stop production.
production                 -> refuses to start, naming every problem at once.
unknown value              -> refuses to start. A typo like `PADYAR_ENV=prod`
                              must not silently degrade to development.

Failing closed is deliberate for the blockers: an install that reaches real
visitors with a placeholder credential or an unpinned signing key is worse
than an install that will not start and says why. Refusing at boot is loud,
immediate, and happens before any traffic arrives.

Everything here is read-only. It never edits `.env`, never contacts the
network, and never prints a secret — only whether one is set and whether it
looks like a placeholder.
"""
import os
import re

# Values that mean "nobody has set this yet". Matched case-insensitively as a
# substring, so `sk-your-api-key-here` and `changeme123` are both caught.
_PLACEHOLDER_HINTS = ("your-", "your_", "changeme", "change-me", "placeholder",
                      "example", "xxxx", "todo", "replace", "dummy", "sample",
                      "local-", "dev-", "test-")


def _looks_like_placeholder(value: str) -> bool:
    v = (value or "").strip().strip('"\'')
    if not v:
        return True
    low = v.lower()
    if any(h in low for h in _PLACEHOLDER_HINTS):
        return True
    return len(set(v)) <= 3          # "xxxxxxxx", "00000000"


DEVELOPMENT, STAGING, PRODUCTION = "development", "staging", "production"
ENVIRONMENTS = (DEVELOPMENT, STAGING, PRODUCTION)


class InvalidEnvironment(RuntimeError):
    """`PADYAR_ENV` is set to something this application does not understand."""


def environment() -> str:
    """The declared environment. Raises on an unrecognised value.

    Failing on a typo is deliberate. Silently treating `PADYAR_ENV=prod` as
    development would reproduce the exact class of bug this replaced: an
    install that believes it is not production and skips every check.
    """
    raw = (os.getenv("PADYAR_ENV") or DEVELOPMENT).strip().lower()
    if raw not in ENVIRONMENTS:
        raise InvalidEnvironment(
            f"PADYAR_ENV={raw!r} is not recognised. "
            f"Use one of: {', '.join(ENVIRONMENTS)}.")
    return raw


def is_production() -> bool:
    """True only when the install DECLARES itself production.

    Never derived from COOKIE_SECURE or any other checked setting — see the
    module docstring for why that was unsound.
    """
    return environment() == PRODUCTION


def audit() -> dict:
    """Inspect the environment. Returns {"blockers": [...], "warnings": [...]}.

    Pure inspection: safe to call from a health endpoint or a CLI as well as
    from startup.
    """
    blockers, warnings = [], []
    env = environment()
    prod = env == PRODUCTION
    # Staging evaluates the SAME rules as production so an operator can see
    # what would stop a launch — it just never refuses to boot. Development
    # skips them, so a developer keeps SQLite, no origins and the dev outbox.
    strict = env in (STAGING, PRODUCTION)

    # --- cookies ---------------------------------------------------------
    # Now a CHECKED setting rather than the thing deciding whether to check.
    # Without Secure, the admin session cookie travels in clear text on any
    # plain-http request — including one an attacker can induce.
    if strict and os.getenv("COOKIE_SECURE", "false").strip().lower() != "true":
        blockers.append(
            "COOKIE_SECURE must be true in production, or the admin session "
            "cookie is sent over plain HTTP.")

    # --- signing key -----------------------------------------------------
    # Empty is NOT automatically wrong: the app generates one and persists it
    # in `settings.app_secret_key`, so it survives restarts. It becomes a
    # problem when the database is rebuilt or a second host is added, because
    # each would mint its own — invalidating sessions and, worse, making every
    # stored `enc:` secret undecryptable on the other host.
    if not (os.getenv("SECRET_KEY") or "").strip():
        warnings.append(
            "SECRET_KEY is not set. One is generated and stored in the "
            "database, so a single host is fine — but pin it explicitly "
            "before running more than one host or rebuilding the database, "
            "or stored provider/SMS secrets become undecryptable.")

    # --- database --------------------------------------------------------
    dsn = os.getenv("DATABASE_URL", "")
    if os.getenv("DB_BACKEND", "").strip().lower() != "postgres":
        (blockers if strict else warnings).append(
            "DB_BACKEND is not 'postgres'. SQLite is the test backend and a "
            "rollback artifact, not a production store.")
    if dsn:
        m = re.match(r"\w+://([^:/@]+):([^@]*)@", dsn)
        if m and _looks_like_placeholder(m.group(2)):
            (blockers if strict else warnings).append(
                "DATABASE_URL carries a placeholder or trivially weak "
                "password.")
        if m and not m.group(2):
            (blockers if strict else warnings).append(
                "DATABASE_URL has no password. If the server uses `trust` "
                "auth, anyone who can reach the port is a superuser; use "
                "scram-sha-256.")
        host = re.search(r"@([^:/?]+)", dsn)
        remote = bool(host and host.group(1) not in ("127.0.0.1", "localhost", "::1"))
        if strict and remote and "sslmode=" not in dsn:
            warnings.append(
                "DATABASE_URL points at a remote host with no sslmode. Add "
                "sslmode=require (or verify-full) so credentials and Persian "
                "content are not sent in clear text.")

    # --- origins ---------------------------------------------------------
    origins = (os.getenv("ALLOWED_ORIGINS") or "").strip()
    if strict and (not origins or origins == "*"):
        blockers.append(
            "ALLOWED_ORIGINS is empty or '*'. The public chat endpoint "
            "validates Origin against this list; without it any site can "
            "embed the bot and spend the AI budget.")

    # --- admin bootstrap -------------------------------------------------
    if strict and _looks_like_placeholder(os.getenv("ADMIN_PASSWORD", "")) and \
            (os.getenv("ADMIN_PASSWORD") or "").strip():
        blockers.append("ADMIN_PASSWORD is a placeholder value.")

    # --- SMS -------------------------------------------------------------
    if strict and os.getenv("OTP_DELIVERY", "dev").strip().lower() == "dev":
        blockers.append(
            "OTP_DELIVERY=dev writes verification codes to a local log file "
            "instead of sending them. Registration would silently not work.")

    # --- visitor-facing content -----------------------------------------
    # The registration form and the visit planner read their whole vocabulary
    # from this file. Shipping the bundled placeholder means real visitors are
    # offered job titles and interests that were reconstructed from the public
    # website, not supplied by the customer — wrong content, presented with
    # full confidence and no error anywhere.
    #
    # A WARNING, not a blocker: the operator may deliberately run a staging or
    # demo install on placeholder content, and refusing to boot would make that
    # impossible. It is loud, and it is reported as a launch blocker in the
    # readiness docs.
    try:
        import json
        from app.config import BASE_DIR
        path = os.getenv("VISIT_TAXONOMY_PATH") or os.path.join(
            BASE_DIR, "data", "visit-taxonomy.json")
        with open(path, encoding="utf-8") as fh:
            if (json.load(fh) or {}).get("status") == "placeholder":
                warnings.append(
                    "visit-taxonomy.json is still the bundled PLACEHOLDER. "
                    "Registration and the visit planner will offer content the "
                    "customer never approved. Replace it before a public "
                    "launch (no restart needed — it hot-reloads).")
    except FileNotFoundError:
        warnings.append("visit-taxonomy.json not found; registration options "
                        "and the visit planner will be empty.")
    except Exception:  # noqa: BLE001 — a malformed file is the loader's problem
        pass

    # --- connection budget ----------------------------------------------
    try:
        pool_max = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
        workers = int(os.getenv("WEB_CONCURRENCY", "1"))
        if pool_max * workers > 80:
            warnings.append(
                f"DB_POOL_MAX_SIZE({pool_max}) x workers({workers}) = "
                f"{pool_max * workers} connections. The pool is PER WORKER; "
                "keep the total well under the server's max_connections and "
                "leave headroom for backups and psql.")
    except ValueError:
        warnings.append("DB_POOL_MAX_SIZE or WEB_CONCURRENCY is not a number.")

    return {"environment": env, "production": prod,
            "blockers": blockers, "warnings": warnings}


def enforce_at_startup(logger) -> None:
    """Log the findings; refuse to boot a production install with a blocker.

    An unrecognised `PADYAR_ENV` propagates out of `audit()` and stops startup
    too — a typo must not quietly become development.
    """
    result = audit()                       # raises InvalidEnvironment on a typo
    env = result["environment"]
    for w in result["warnings"]:
        logger.warning("[config] %s", w)
    if not result["blockers"]:
        logger.info("[config] %s configuration check passed (%d warning(s))",
                    env, len(result["warnings"]))
        return

    for b in result["blockers"]:
        logger.error("[config] BLOCKER: %s", b)
    if result["production"]:
        # Only the KEYS and the problem, never a value — an operator reads this
        # off a terminal or a log aggregator that may be shared.
        raise RuntimeError(
            f"Production configuration invalid ({len(result['blockers'])} "
            "problem(s)):\n- " + "\n- ".join(result["blockers"]))
    logger.warning(
        "[config] %d blocker(s) above are tolerated because PADYAR_ENV=%s. "
        "They WILL stop startup under PADYAR_ENV=production.",
        len(result["blockers"]), env)
