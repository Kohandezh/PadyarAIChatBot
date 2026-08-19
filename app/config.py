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

# --- Chat Security ---
CHAT_TOKEN_TTL = 3600  # 1 hour
# Rate limiting is per client IP, and at an exhibition a whole hall of
# visitors often arrives through one NAT'd address — a strict per-IP limit
# would throttle the entire booth, not one abuser. Sized for that; tune per
# install via env.
CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT", "20"))    # max requests per window per IP
CHAT_RATE_WINDOW = int(os.getenv("CHAT_RATE_WINDOW", "60"))  # seconds

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
    logger.info(f"OPENAI_API_KEY loaded from env: {OPENAI_API_KEY[:5]}...")
else:
    # Not fatal: the key can be supplied from the admin panel (Settings → AI)
    # and is stored per-install in the settings table.
    logger.warning("OPENAI_API_KEY not in env — expecting a key from the admin panel settings.")

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
