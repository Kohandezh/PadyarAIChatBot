import os
import logging

from dotenv import load_dotenv


# --- Paths ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The one place the project's environment file is resolved. Read it through
# `app.config.ENV_FILE` (not a copy taken at import time) so the test suite can
# point it at a throwaway file — no test may ever touch the real .env.
ENV_FILE = os.path.join(BASE_DIR, ".env")

# --- Load .env ---
load_dotenv(ENV_FILE)

DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "chat_history.db"))
# Logging lives in its OWN SQLite file. Deliberate: log writes are high-volume
# and bursty, and SQLite locks per FILE. Sharing chat_history.db would let an
# error storm block the chatbot's own reads. Separate file = a log storm can
# only ever slow logging down.
LOGS_DB_PATH = os.getenv("LOGS_DB_PATH", os.path.join(BASE_DIR, "application_logs.db"))

# Which engine the RUNTIME uses. "postgres" is production; "sqlite" remains for
# the test suite and as the rollback path during the transition. The SQLite
# files are still read by the migration tool, never written by the app once
# this is "postgres".
DB_BACKEND = os.getenv("DB_BACKEND", "postgres").strip().lower()
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://padyar_app:padyar_local_dev@127.0.0.1:5432/padyar")
VIDEO_DIR = os.path.join(BASE_DIR, "media", "videos")

# --- Similarity thresholds ---
# A local match (TF-IDF over titles, or the questions index) is only *trusted*
# outright at or above this score. Below it we do NOT serve the match blindly —
# the AI classifier decides intent instead (see app/routers/chat.py).
TRUSTED_MATCH_THRESHOLD = 0.70
# Used ONLY when the AI fallback is unavailable (disabled or errored): the lowest
# local score we will still answer from. Below this we tell the user to rephrase
# rather than show an unrelated video. Raised from the old 0.20, which was noise
# level and caused confident wrong answers (e.g. a cost question -> an unrelated entry).
LOCAL_FALLBACK_THRESHOLD = 0.45
QUESTIONS_FALLBACK_THRESHOLD = 0.60
# Deprecated alias kept so older imports (e.g. app/services/search.py) don't break.
# No longer used as an answer floor.
SIMILARITY_THRESHOLD = LOCAL_FALLBACK_THRESHOLD

# --- Security Config ---
MAX_LOGIN_ATTEMPTS = 5
BLOCK_TIME_MINUTES = 5
SESSION_TIMEOUT_HOURS = 1
ADMIN_COOKIE_NAME = "admin_session"

# Send the admin session cookie only over HTTPS. False for local http dev,
# Which kind of install this is: "development" | "staging" | "production".
#
# This is the ONLY thing that decides whether the production configuration gate
# runs. It deliberately does NOT read COOKIE_SECURE, which was the previous
# marker and is unsound as one: COOKIE_SECURE is itself a setting the gate has
# to CHECK, so a real production server with COOKIE_SECURE=false would classify
# itself as development, skip validation entirely, and boot with insecure
# cookies — the misconfiguration disabling the check that exists to catch it.
#
# Defaults to development so existing installs keep working. Production
# deployments must set it explicitly; see .env.example.
PADYAR_ENV = (os.getenv("PADYAR_ENV") or "development").strip().lower()

# True in production. Set COOKIE_SECURE=true in the production .env.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# A fresh installation opens with the bundled INOTEX knowledge base. Set this
# to false for an install that will import its own content, or for tests that
# need to assert against an empty knowledge base.
SEED_DEFAULT_CONTENT = os.getenv("SEED_DEFAULT_CONTENT", "true").lower() == "true"

# Dedicated secret for signing chat tokens. Leave empty to auto-generate a
# stable key (stored in the DB) on first run — zero-config but still secret.
SECRET_KEY = os.getenv("SECRET_KEY") or ""

# First-install admin account. Only used to seed a brand-new database — if an
# admin already exists, these are ignored. Leave password empty to have a
# random one generated and written to ADMIN_CREDENTIALS.txt on first run.
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "inotex@admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_SECURITY_ANSWER = os.getenv("ADMIN_SECURITY_ANSWER", "")

# --- Client IP / trusted proxy ---
# Which client address the app believes, for rate limiting and for the ip field
# on every audit and security log row.
#
# Both default to OFF, and that default is the security property: forwarding
# headers are attacker-controlled unless a proxy you own rewrites them. An
# install that sets nothing ignores the headers entirely and uses the socket
# address, so a direct-to-uvicorn deployment cannot be spoofed.
#
# TRUST_CLOUDFLARE: this deployment sits behind a Cloudflare Tunnel, and
# Cloudflare replaces CF-Connecting-IP on every request. Turn it on there.
TRUST_CLOUDFLARE = os.getenv("TRUST_CLOUDFLARE", "false").lower() == "true"

# TRUSTED_PROXY_HOPS: how many proxies you control sit in front of the app.
# The resolver counts that many entries from the RIGHT of X-Forwarded-For,
# because the rightmost entries are the ones your own infrastructure appended.
# The leftmost entry is whatever the client typed and must never be believed.
# 0 disables the header.
try:
    TRUSTED_PROXY_HOPS = max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0")))
except ValueError:
    TRUSTED_PROXY_HOPS = 0

# --- Chat Security ---
# Lifetime of the signed chat token, in seconds. Env-tunable like its rate
# siblings: a demo kiosk may want a short TTL, an all-day booth a longer one.
# NOTE: app/auth/security.py binds this at import (`from app.config import
# CHAT_TOKEN_TTL`), so env must be set before import (standard dotenv usage)
# and tests must patch app.auth.security.CHAT_TOKEN_TTL — the enforcing
# module's binding, not app.config's copy.
CHAT_TOKEN_TTL = int(os.getenv("CHAT_TOKEN_TTL", "3600"))  # 1 hour
# How long a visitor's personal contact-exchange QR stays valid (REQ-019): a
# few hours, not indefinitely — a lost or borrowed phone should not carry a
# forever-valid QR on its lock screen.
QR_PAYLOAD_TTL_SECONDS = int(os.getenv("QR_PAYLOAD_TTL_SECONDS", str(4 * 3600)))  # 4 hours
# How long past its TTL the refresh endpoint (POST /api/chat-token) still
# accepts the OLD token when minting a new one. This is what saves a visitor
# whose token expired mid-conversation: without it the endpoint would demand
# an unexpired token to issue one, a dead end. 900s (15 min) covers "expired
# a few minutes ago, presses send now" without quietly doubling the TTL for
# every other purpose — /chat and /api/transcribe still enforce the strict
# expiry.
CHAT_TOKEN_REFRESH_GRACE = int(os.getenv("CHAT_TOKEN_REFRESH_GRACE", "900"))
# Lifetime of the padyar_conv correlation cookie (24h). Constant, not env:
# an operator has no reason to tune a correlation window, and a sliding 24h
# already outlives any real conversation. "When in doubt: default."
CONV_COOKIE_MAX_AGE = 24 * 3600

# --- Visitor session (the registered visitor's identity) ---
# The cookie that carries a row in app.visitor_sessions. It is the ONLY thing
# app/main.py resolve_visitor will look at to decide who is asking; a header,
# a body field or a query parameter never counts.
#
# NOT "padyar_visitor". That name is already taken by the leads module
# (app/services/leads.py VISITOR_COOKIE), where it carries a booth STAFF
# member's personal link code on path "/" with a 12 hour lifetime. Two
# different tables, two different people, one unfortunate English word.
# Shipping both under one name means a staff member who also chats loses
# their /v panel and hands their staff credential to the session lookup.
# Do not "fix" this back.
VISITOR_COOKIE_NAME = "padyar_vs"

# How long a registered visitor stays signed in, in days. The expiry slides on
# every request that uses the session, so this is "days of inactivity", not a
# hard cap. 30 because an exhibition runs for days and the same people come
# back to the same booth; re-typing an SMS code every morning is the kind of
# friction this product exists to remove.
#
# Env-overridable so an install that shares one kiosk between strangers can
# shorten it without a deploy.
#
# NOTE: app/auth/visitor.py binds all three of these at import
# (`from app.config import VISITOR_COOKIE_NAME, VISITOR_SESSION_DAYS,
# VISITOR_SESSION_MAX_HOURS`), so a test must patch the ENFORCING module's
# binding (app.auth.visitor.X), not app.config's copy. Same trap
# CHAT_TOKEN_TTL documents above.
try:
    VISITOR_SESSION_DAYS = max(1, int(os.getenv("VISITOR_SESSION_DAYS", "30")))
except ValueError:
    VISITOR_SESSION_DAYS = 30

# The HARD cap on one session, in hours, counted from the row's `created_at`
# and never extended. The setting above is INACTIVITY, and lowering it does
# not help a booth kiosk, because a kiosk in continuous use never goes
# inactive: it is the NEXT person's traffic that slides the expiry, so the
# person who sits down second stays signed in as the person who sat down
# first. This is the only bound a kiosk can actually reach.
#
# 12 hours is one exhibition day. A visitor who comes back tomorrow types
# their SMS code again, which costs them seconds; inheriting a stranger's
# identity costs them their name on somebody else's conversation.
#
# Env-overridable and clamped to at least 1 hour, like the days above: a
# customer with a private handset per visitor can raise it, and a value of 0
# would sign every single visitor out on their next request.
try:
    VISITOR_SESSION_MAX_HOURS = max(
        1, int(os.getenv("VISITOR_SESSION_MAX_HOURS", "12")))
except ValueError:
    VISITOR_SESSION_MAX_HOURS = 12

# Rate limiting is per VISITOR IDENTITY — the nonce inside the signed chat
# token — with a loose per-IP backstop. At an exhibition a whole hall of
# visitors often arrives through one NAT'd address, and a per-IP-only limit
# throttles the entire booth, not one abuser. CHAT_RATE_LIMIT is the
# per-visitor ceiling; CHAT_IP_RATE_LIMIT (5x) bounds the trick of refreshing
# the page to mint a fresh identity (~5 refresh cycles/min per IP) while
# still giving the booth its collective headroom. One shared window — one
# number an operator can reason about. Tune per install via env.
CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT", "20"))    # max requests per window per identity
CHAT_RATE_WINDOW = int(os.getenv("CHAT_RATE_WINDOW", "60"))  # seconds — shared by every bucket below
CHAT_IP_RATE_LIMIT = int(os.getenv("CHAT_IP_RATE_LIMIT", str(CHAT_RATE_LIMIT * 5)))  # loose per-IP backstop

# OTP endpoints: identity buckets (per canonicalized destination on /request,
# per challenge everywhere else) plus a per-IP backstop. The identity buckets
# stop a booth's registration bursts from collectively locking the hall out;
# the backstop stops rotating destinations from turning the endpoints into a
# free SMS relay. The service-level caps (attempts/resends/destination-hourly)
# remain the real bounds — these just trip before the service is reached.
OTP_RATE_LIMIT = int(os.getenv("OTP_RATE_LIMIT", "10"))      # per identity per window
OTP_IP_RATE_LIMIT = int(os.getenv("OTP_IP_RATE_LIMIT", "60"))  # per IP per window

# GET / (the render that MINTS chat tokens): generous per-IP fence that
# exists only to stop render hammering — renders are cheap, but each one
# mints a fresh rate-limit identity. 120/min is far above any human or kiosk.
PAGE_RATE_LIMIT = int(os.getenv("PAGE_RATE_LIMIT", "120"))   # page renders per IP per window

# Minimum probability for the locally trained intent classifier to answer on
# its own (Tier 1.5, between local retrieval and the external AI classifier).
# 0.6 measured on holdout: answers 39% of otherwise-ambiguous queries at 91%
# precision; a wrong confident answer costs more at a booth than a deferral.
INTENT_TRUST_THRESHOLD = float(os.getenv("INTENT_TRUST_THRESHOLD", "0.6"))

# Hybrid retrieval: BM25 + dense candidates fused by the feature reranker
# (app/services/rerank.py). Set RETRIEVAL_RERANK=false to fall back to plain
# dense-argmax ranking — an instant rollback for an operator who sees the
# reranker misbehave on their own corpus, and the A/B switch the evaluation
# harness uses to prove the change earns its place.
RERANK_ENABLED = os.getenv("RETRIEVAL_RERANK", "true").lower() == "true"

# --- Allowed Origins ---
# Hostnames allowed to call the public /chat endpoint. localhost/127.0.0.1 are
# always kept for local dev. Add the customer's domain(s) via the env var
# ALLOWED_ORIGINS (comma-separated) instead of hardcoding per install.
_extra_origins = [h.strip() for h in os.getenv("ALLOWED_ORIGINS", "inotex.com").split(",") if h.strip()]
ALLOWED_ORIGINS = list(dict.fromkeys(_extra_origins + ["localhost", "127.0.0.1"]))

# --- Video Config ---
# Relative by default ("/media/videos") so video URLs resolve against whatever
# scheme+host the page was loaded with — works identically on local http and
# production https with no per-environment change. Override only to offload
# videos to a separate CDN host.
VIDEO_BASE_URL = os.getenv("VIDEO_BASE_URL", "/media/videos")
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi"}

# --- Logging Setup ---
# LOG_FORMAT=json switches to structured single-line JSON records so a log
# shipper (Loki/ELK/CloudWatch) can index them without a parser; the default
# stays human-readable for local development.
if os.getenv("LOG_FORMAT", "").lower() == "json":
    import json as _json

    class _JsonFormatter(logging.Formatter):
        def format(self, record):
            payload = {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return _json.dumps(payload, ensure_ascii=False)

    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[_handler])
else:
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PadyarAssistant")

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Any OpenAI-compatible endpoint. The install owner can point this at the
# national open AI platform (or any gateway) and enter their own key from the
# admin panel — the panel settings override both of these env values.
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.gapgpt.app/v1")

if OPENAI_API_KEY:
    # Presence only — never any part of the key itself.
    logger.info("OPENAI_API_KEY loaded from env")
else:
    # Not fatal: the key can be supplied from the admin panel (Settings → AI)
    # and is stored per-install in the settings table.
    logger.warning("OPENAI_API_KEY not in env — expecting a key from the admin panel settings.")

# --- Text to speech (Chatterbox) ---
# The Persian TTS service from deploy/tts/server.py. It listens on loopback
# only and has no authentication of its own, which is exactly why the admin
# panel proxies it (app/routers/tts.py) instead of the browser talking to it:
# port 8003 is never reachable from outside the host.
TTS_URL = os.getenv("TTS_URL", "http://127.0.0.1:8003").rstrip("/")
# Generous on purpose. A cache miss on a Tesla P40 synthesises at roughly
# real-time, so a paragraph legitimately takes tens of seconds; a short timeout
# would report a healthy service as broken to the operator most likely to be
# testing a long answer.
TTS_TIMEOUT = float(os.getenv("TTS_TIMEOUT", "180"))
# Health and voice listing are cheap. If they hang, the service is wedged, and
# the panel should say so within a few seconds rather than spin.
TTS_STATUS_TIMEOUT = float(os.getenv("TTS_STATUS_TIMEOUT", "5"))
# Warming the cache renders EVERY dataset answer, so its ceiling is a whole
# dataset's worth of generation, not one clip's. Sixteen answers on the GPU is
# about a minute; a customer with two hundred is not, which is why this is its
# own number and not TTS_TIMEOUT.
TTS_PRERENDER_TIMEOUT = float(os.getenv("TTS_PRERENDER_TIMEOUT", "1800"))

# --- Module System ---
from app.modules.registry import resolve_enabled_modules, module_enabled

# Resolve enabled modules from ENABLED_MODULES env var.
# Empty/missing = all modules enabled (backward compatible).
ENABLED_MODULES_STR = os.getenv("ENABLED_MODULES", "")
ENABLED_MODULES = resolve_enabled_modules(ENABLED_MODULES_STR)

logger.info(f"Enabled modules: {', '.join(ENABLED_MODULES)}")


def is_module_enabled(module_name: str) -> bool:
    """Check if a specific module is enabled."""
    return module_enabled(module_name, ENABLED_MODULES)


# --- The selection tier and conversation memory (2026-08-28) ---
# Plain constants, not env flags: every one of them is a product decision an
# operator should not have to reason about, and the two that a customer really
# does tune (how many names a list shows, how long chat logs are kept) are
# settings rows in the admin panel instead.

# How many retrieved records the model is shown before it chooses one.
# Measured on data/eval/golden-inotex.json (2026-08-28, embedding + rerank):
# recall@1=0.786, @3=0.857, @5=0.929, @8=0.952, @13=0.952. The curve is flat
# after 8, so eight records buy the whole ceiling and nothing beyond it.
ANSWER_TOPK = 8

# Prior turns handed to the model as context. Five covers the follow-ups
# visitors actually ask ("and the second one?") without turning every question
# into a transcript upload.
HISTORY_TURNS = 5

# How far back those turns may be read. This is a PRIVACY bound, not a
# correlation one, so it is set by what a conversation needs and not by the
# padyar_conv cookie: app/routers/chat.py re-sets that cookie with a fresh
# max_age on every answered turn, so at a busy booth it never expires and one
# conversation_id covers everyone who touches the kiosk that day. Reading 24h
# of history meant each new visitor's first question shipped the previous
# visitors' RAW messages (chat_logs is the unredacted store) to the AI
# provider — including messages a local tier had answered, which had never
# left the machine at all.
# Fifteen minutes is one visit: five turns with a video watched between them
# fits inside it comfortably. It is also the same bound as PICK_WINDOW_MINUTES
# below, on purpose — the visitor's own words must not outlive the list of
# public record ids that goes stale for exactly the same reason.
HISTORY_WINDOW_MINUTES = 15

# Most records ever offered as a numbered choice on one turn. More than five
# is a wall of text on a booth screen; the pager gives the next five.
OPTIONS_MAX = 5

# When the top candidate beats the second by this much, retrieval had already
# decided and a model asking "which one did you mean?" is overridden. Asking
# about a question we could have answered is the failure a visitor minds most.
OPTIONS_MARGIN = 0.15

# How long a stored list stays pickable. A booth kiosk is ONE browser shared
# by many people: a bare "3" typed an hour after somebody else's list must not
# resolve. Fifteen minutes is short enough to bound that and long enough for a
# visitor who reads slowly.
PICK_WINDOW_MINUTES = 15

# Ids kept in one offer for paging. Caps the column so a 169-company match
# cannot write a kilobyte into every chat_logs row.
OFFER_IDS_MAX = 50

# Longest lead sentence the model may put above a numbered list. A paragraph
# there buries the list the visitor actually has to read.
LEAD_MAX_CHARS = 160

# Per-turn truncation for the history block, and its total budget. The answer
# side is longer because a list answer is longer than the question that asked
# for it.
HISTORY_QUERY_CHARS = 300
HISTORY_ANSWER_CHARS = 400
HISTORY_BLOCK_CHARS = 2000

# How many messages a conversation may hold before the OLD part of it is
# replaced by a short summary. A message is one line: the visitor's question
# and the bot's answer are two, so twelve is six turns. Below this the last
# HISTORY_TURNS turns already carry the whole conversation word for word and a
# summary would only add a second, worse copy of it.
SUMMARIZE_AFTER_MESSAGES = 12

# Longest summary we keep. It is sent to the model inside HISTORY_BLOCK_CHARS
# together with the recent turns, so it has to leave room for them. Four
# hundred characters is a solid paragraph — enough to say what the visitor is
# here for, too short to become a second transcript.
SUMMARY_MAX_CHARS = 400
