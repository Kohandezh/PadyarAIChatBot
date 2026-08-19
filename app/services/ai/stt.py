"""Where speech-to-text gets its credentials.

THE PROBLEM THIS FIXES
----------------------
Chat and classification were re-pointed at the AI Control Plane, so their key
lives encrypted on a provider instance and is edited in Admin → AI → Providers.
Transcription was left behind on the legacy `ai_api_key` setting.

The result was a trap with no visible symptom until a visitor tried to speak:
an operator rotates the key in Admin → AI, chat starts working, and voice keeps
returning 401 — because nothing on that page touches `ai_api_key`. Two secrets,
one of them invisible, and the working half hides the broken half.

WHAT THIS IS NOT
----------------
It is not a general Audio Provider architecture. Transcription has exactly one
task and one shape here, so this resolves a single binding rather than building
a second control plane. If audio grows real routing needs later, this is the
seam to widen.

RESOLUTION ORDER
----------------
1. An explicit binding (`ai_stt_provider_instance_id`), if the operator set one.
2. Otherwise the single obvious control-plane instance — see `_implicit()`.
   This is what makes the fix work with NO new configuration: the migrated
   gateway is already there, so rotating its key in Admin → AI fixes voice too.
3. Only if neither exists, the legacy `ai_api_base` / `ai_api_key` settings.

Step 3 exists so an install that never migrated keeps working. Once a control
plane instance is present, it wins — the legacy values stop being authoritative
rather than silently competing.

CAPABILITY HONESTY
------------------
Transcription here speaks the OpenAI `/audio/transcriptions` shape, so only
providers that actually serve it are eligible. Anthropic and Gemini do not, and
this must never pretend otherwise by binding to them and failing at request
time — `STT_CAPABLE_TYPES` is the whole allowlist.
"""
from app.config import logger

# Provider types that serve an OpenAI-shaped /audio/transcriptions endpoint.
# Deliberately small and explicit: binding STT to a provider that cannot
# transcribe would turn a configuration mistake into a runtime 404/400 that
# looks like a broken microphone.
STT_CAPABLE_TYPES = ("openai", "openai_compatible")

SETTING_INSTANCE = "ai_stt_provider_instance_id"
SETTING_MODEL = "ai_model_stt"
DEFAULT_MODEL = "whisper-1"


class STTNotConfigured(Exception):
    """No usable transcription credentials. Carries a Persian operator message."""

    def __init__(self, message_fa: str):
        self.message_fa = message_fa
        super().__init__(message_fa)


def _base_url_of(runtime) -> str:
    """The endpoint for an instance, from its provider-specific config."""
    cfg = runtime.config or {}
    return (cfg.get("base_url") or "").strip()


def _explicit():
    """The operator's chosen instance, if they set one."""
    from app.db.queries import get_setting
    from app.services.ai import store
    iid = (get_setting(SETTING_INSTANCE, "") or "").strip()
    if not iid:
        return None
    rt = store.runtime_for(iid)
    if rt is None:
        # The binding points at an instance that no longer exists. Fail loudly
        # rather than quietly falling back — a stale binding is a configuration
        # error the operator needs to see, not something to paper over.
        raise STTNotConfigured(
            "سرویس‌دهندهٔ تبدیل گفتار به متن پیدا نشد. در بخش هوش مصنوعی «سرویس‌دهندهٔ "
            "رونویسی» را دوباره انتخاب کنید.")
    if rt.provider_type not in STT_CAPABLE_TYPES:
        raise STTNotConfigured(
            f"سرویس‌دهندهٔ «{rt.display_name}» از رونویسی صوت پشتیبانی نمی‌کند.")
    return rt


def _implicit():
    """The one control-plane instance that can obviously serve transcription.

    Returned only when the choice is UNAMBIGUOUS — exactly one enabled,
    secret-bearing, STT-capable instance. With two candidates the code refuses
    to guess and falls through, because silently picking one and billing it
    would be worse than asking the operator to choose.
    """
    from app.services.ai import store
    try:
        candidates = [i for i in store.list_instances()
                      if i.get("enabled") and i.get("has_secret")
                      and i.get("provider_type") in STT_CAPABLE_TYPES]
    except Exception:  # noqa: BLE001 — control plane unavailable, use legacy
        return None
    if len(candidates) != 1:
        return None
    return store.runtime_for(candidates[0]["id"])


def model() -> str:
    from app.db.queries import get_setting
    return (get_setting(SETTING_MODEL, "") or "").strip() or DEFAULT_MODEL


def resolve() -> tuple:
    """Return `(base_url, api_key, model, source)` for transcription.

    `source` is one of "explicit" | "implicit" | "legacy" and exists so the
    admin page and the logs can tell an operator WHERE the credential came
    from. The key itself is never logged and never returned to a client.
    """
    rt = _explicit()
    source = "explicit"
    if rt is None:
        rt = _implicit()
        source = "implicit"

    if rt is not None:
        base, key = _base_url_of(rt), (rt.secret or "")
        if base and key:
            return base, key, model(), source
        # An instance exists but is unusable. Fall through to legacy rather
        # than failing outright, so a half-configured instance cannot take
        # voice down on an install that still has working legacy settings.
        logger.warning("[stt] instance %s has no usable base_url/secret; "
                       "falling back to legacy settings", rt.instance_id)

    from app.services.openai import provider_config
    base, key = provider_config()
    if not (base and key):
        raise STTNotConfigured(
            "کلید سرویس رونویسی تنظیم نشده است. در بخش هوش مصنوعی یک سرویس‌دهنده "
            "با کلید معتبر تنظیم کنید.")
    # Declare the legacy key so `applog.scrub_text` can strip it by exact
    # value. The control-plane paths get this for free from
    # `store.runtime_for()`; without it, a provider echoing a legacy key back
    # in an error body would depend on a regex recognising its shape.
    from app.services import applog
    applog.register_secret(key)
    return base, key, model(), "legacy"


def status() -> dict:
    """Operator-facing summary for the admin page. NEVER includes the secret."""
    from app.db.queries import get_setting
    out = {"configured": False, "source": "", "model": model(),
           "instance_id": (get_setting(SETTING_INSTANCE, "") or "").strip(),
           "provider_display_name": "", "detail_fa": ""}
    try:
        _base, _key, mdl, source = resolve()
    except STTNotConfigured as exc:
        out["detail_fa"] = exc.message_fa
        return out
    except Exception:  # noqa: BLE001
        out["detail_fa"] = "وضعیت رونویسی قابل تشخیص نیست."
        return out

    out.update(configured=True, source=source, model=mdl)
    if source in ("explicit", "implicit"):
        rt = _explicit() if source == "explicit" else _implicit()
        if rt is not None:
            out["provider_display_name"] = rt.display_name
            out["instance_id"] = rt.instance_id
        out["detail_fa"] = ("از سرویس‌دهندهٔ کنترل‌پنل هوش مصنوعی استفاده می‌شود."
                            if source == "explicit" else
                            "به‌طور خودکار از تنها سرویس‌دهندهٔ فعال استفاده می‌شود.")
    else:
        out["detail_fa"] = ("از تنظیمات قدیمی استفاده می‌شود. پس از تعریف سرویس‌دهنده "
                            "در بخش هوش مصنوعی، همان کلید برای رونویسی هم به کار می‌رود.")
    return out
