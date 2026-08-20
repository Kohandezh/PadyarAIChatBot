"""Adapter contract + shared transport for AI providers.

ONE ADAPTER PER PROVIDER TYPE, REGISTERED IN `app/services/ai/adapters/__init__.py`.
Application code (and the routing engine) never imports an adapter directly —
it goes through the registry, so "which adapter serves zai" is a one-line fact.

WHAT AN ADAPTER OWNS
--------------------
  * provider metadata (display name, docs, whether discovery exists)
  * its Admin configuration form (`configuration_schema`) — there is no
    universal form: Qwen needs region + workspace + domain family, Kimi and
    Z.AI need a platform choice, Gemini needs an API-surface choice
  * request translation FROM the neutral `AIRequest` TO the provider wire
  * response parsing that NEVER assumes text exists (Gemini safety blocks
    arrive as HTTP 200 with a valid body and no text; OpenAI can 200 with
    status:"failed")
  * error normalization from HTTP status + parsed provider body
  * usage extraction with `tokens_input` COMPUTED (Anthropic's input_tokens
    counts only tokens after the last cache breakpoint)
  * capability gating: whether temperature/top_p may be sent to THIS model,
    whether reasoning can be disabled, at what level

WHAT THE SHARED TRANSPORT PROVIDES
----------------------------------
`http()` validates every URL against the SSRF endpoint policy (trust class
from the instance row), never follows redirects (a 3xx is surfaced as an
error; honouring one must go through endpoint_policy.assert_safe_redirect),
maps httpx transport failures onto the normalized taxonomy, and hands the
adapter (status, parsed-body-or-text, headers). Adapters never open their
own httpx clients.

Connections are PINNED: the policy resolves the hostname once, validates every
answer, and the client is handed an already-validated IP so it never resolves
again. `Host` and TLS SNI keep the original name, so certificate verification
is unchanged.
"""
import json

import anyio
import httpx

from ..errors import (
    AIError, AUTHENTICATION_FAILED, CONNECTION_FAILED, CONTEXT_LIMIT_EXCEEDED,
    INVALID_REQUEST, INVALID_RESPONSE, MODEL_NOT_FOUND, PERMISSION_DENIED,
    PROVIDER_UNAVAILABLE, QUOTA_EXCEEDED, RATE_LIMITED, SERVER_ERROR,
    TIMEOUT, from_status,
)
from .. import endpoint_policy
from ..request import (
    AIRequest, AIResponse, FINISH_CONTENT_FILTER, FINISH_LENGTH,
    FINISH_STOP, FINISH_TOOL_CALLS, RESPONSE_JSON_OBJECT,
)

# ── Metadata / configuration dataclasses ────────────────────────────────


class ProviderMetadata:
    def __init__(self, type_key: str, display_name: str, docs_url: str = "",
                 native: bool = False, supports_discovery: bool = True,
                 note_fa: str = ""):
        self.type_key = type_key
        self.display_name = display_name
        self.docs_url = docs_url
        self.native = native                # True = dedicated wire protocol
        self.supports_discovery = supports_discovery
        self.note_fa = note_fa


class ConfigField:
    """One field of a provider's Admin configuration form.

    `secret=True` values are stored via secure_store and are NEVER echoed back
    by any API — the admin sees "set / not set" only.
    """

    def __init__(self, key: str, label_fa: str, type_: str = "string",
                 required: bool = False, default: str = "", options=None,
                 help_fa: str = "", secret: bool = False):
        self.key = key
        self.label_fa = label_fa
        self.type = type_                   # string | password | url | enum | int | bool
        self.required = required
        self.default = default
        self.options = options or []        # [(value, fa label)] for enum
        self.help_fa = help_fa
        self.secret = secret

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label_fa, "type": self.type,
                "required": self.required, "default": self.default,
                "options": [{"value": v, "label": l} for v, l in self.options],
                "help": self.help_fa, "secret": self.secret}


class ProviderRuntime:
    """Everything an adapter may use about one configured provider instance.

    Built by the store from a DB row; `secret` is the decrypted API key and
    must never be logged, stored in an error, or echoed in a response.
    """

    __slots__ = ("instance_id", "provider_type", "display_name", "enabled",
                 "trust_class", "config", "secret", "timeout_s")

    def __init__(self, instance_id, provider_type, display_name, enabled,
                 trust_class, config, secret, timeout_s=45.0):
        self.instance_id = instance_id
        self.provider_type = provider_type
        self.display_name = display_name
        self.enabled = enabled
        self.trust_class = trust_class
        self.config = config or {}
        self.secret = secret or ""
        self.timeout_s = timeout_s


# The 60-second URL-validation cache that used to live here has been removed.
#
# It cached a validated URL STRING, which the HTTP client then resolved itself
# — so the cache made the DNS-rebinding window wider rather than narrower: for
# a full minute the code believed a hostname had been checked while every
# request re-resolved it freely. `http()` now calls `endpoint_policy.pin()`,
# which resolves once and hands the connection a validated IP.
#
# That costs one `getaddrinfo` per provider call. Against a request that spends
# seconds waiting on a language model, a warm-resolver lookup is not a cost
# worth trading correctness for — but it is a BLOCKING call, so it is run in a
# worker thread. Left on the event loop it would stall every other concurrent
# request in the process whenever a resolver was slow, not merely the request
# waiting on it.


# ── Finish-reason normalization tables ──────────────────────────────────
# OpenAI-compatible baseline; adapters override with provider extras.
OPENAI_FINISH_MAP = {
    "stop": FINISH_STOP, "length": FINISH_LENGTH,
    "content_filter": FINISH_CONTENT_FILTER, "tool_calls": FINISH_TOOL_CALLS,
    "tool_use": FINISH_TOOL_CALLS, "function_call": FINISH_TOOL_CALLS,
    "end_turn": FINISH_STOP, "model_length": FINISH_LENGTH,
}


class BaseAdapter:
    """Contract every provider adapter implements.

    Subclasses set PROVIDER_TYPE and override what differs. The three native
    adapters (OpenAI Responses, Anthropic Messages, Gemini Interactions)
    override `invoke()` entirely; the six compatible providers subclass
    OpenAICompatibleAdapter and override only their divergences.
    """

    PROVIDER_TYPE = ""

    # ── Identity / configuration ────────────────────────────────────────

    def metadata(self) -> ProviderMetadata:
        raise NotImplementedError

    def configuration_schema(self) -> list:
        return []

    def default_config(self) -> dict:
        return {f.key: f.default for f in self.configuration_schema()
                if f.default != "" and f.type != "password"}

    def validate_config(self, cfg: dict, trust_class: str = "public") -> dict:
        """Validate and normalize an admin-submitted config.

        Raises AIError(INVALID_REQUEST) with a Persian detail on any problem.
        URL fields are run through the SSRF policy so a bad endpoint is
        rejected at save time, not at first call.
        """
        cfg = cfg or {}
        cleaned = {}
        for f in self.configuration_schema():
            if f.type == "password":
                # Password fields (api_key) live in the dedicated secret
                # column, not in config JSONB. Presence is enforced by the
                # store at save time; nothing about them is validated here.
                continue
            raw = (cfg.get(f.key) or "").strip() if isinstance(cfg.get(f.key), str) else cfg.get(f.key)
            raw = "" if raw is None else raw
            if f.type == "int":
                try:
                    cleaned[f.key] = int(raw) if str(raw).strip() != "" else None
                except (TypeError, ValueError):
                    raise AIError(code=INVALID_REQUEST, provider_type=self.PROVIDER_TYPE,
                                  provider_detail=f"bad integer for {f.key}")
            elif f.type == "bool":
                cleaned[f.key] = str(raw).lower() in ("1", "true", "yes", "on")
            else:
                cleaned[f.key] = str(raw).strip()
            value = cleaned[f.key]
            if f.required and (value == "" or value is None):
                raise AIError(code=INVALID_REQUEST, provider_type=self.PROVIDER_TYPE,
                              provider_detail=f"missing required field {f.key}")
            if f.type == "enum" and value != "" and value is not None:
                valid = [v for v, _l in f.options]
                if value not in valid:
                    raise AIError(code=INVALID_REQUEST, provider_type=self.PROVIDER_TYPE,
                                  provider_detail=f"invalid choice for {f.key}")
            if f.type == "url" and value:
                try:
                    endpoint_policy.validate(str(value), trust_class)
                except endpoint_policy.EndpointRejected as e:
                    raise AIError(code=INVALID_REQUEST, provider_type=self.PROVIDER_TYPE,
                                  provider_detail=f"{f.key}: {e.reason}")
        return cleaned

    def endpoint_url(self, rt: ProviderRuntime) -> str:
        """The base endpoint for SSRF validation and diagnostics."""
        raise NotImplementedError

    # ── Capabilities ────────────────────────────────────────────────────

    def sampling_policy(self, model_id: str) -> dict:
        """Which sampling preferences this model accepts.

        Returns {"temperature": bool, "top_p": bool}. Defaults to accepting
        both; adapters for providers that reject them (Anthropic 4.7+,
        Kimi K-series, DeepSeek-with-thinking, Gemini 3.x-deprecated)
        override per model. The adapter DROPS what is not accepted — it never
        sends a parameter the model will reject.
        """
        return {"temperature": True, "top_p": True}

    def reasoning_control(self, model_id: str) -> dict:
        """How to express the reasoning preference on this provider.

        Returns {"can_disable": bool, "param": str}.  "param" is one of:
          ""                 — no reasoning control exists (send nothing)
          "thinking"         — body {"thinking": {"type": "enabled"|"disabled"}}
          "thinking_level"   — generation_config.thinking_level (Gemini)
          "reasoning_effort" — body {"reasoning_effort": level}
          "effort"           — output_config.effort (Anthropic)
        """
        return {"can_disable": True, "param": ""}

    def supports_json_object(self, model_id: str) -> bool:
        return True

    # ── Discovery / health ──────────────────────────────────────────────

    async def list_models(self, rt: ProviderRuntime) -> list:
        """Discovered models as catalog rows (dicts). Only called when
        metadata().supports_discovery is True."""
        raise NotImplementedError

    async def test_connection(self, rt: ProviderRuntime) -> dict:
        """Cheapest meaningful connectivity check, provider-neutral result.

        Returns {"ok": bool, "status": <normalized>, "detail": str,
        "latency_ms": int}. NEVER enables the provider as a side effect.
        """
        raise NotImplementedError

    # ── Generation ──────────────────────────────────────────────────────

    async def invoke(self, rt: ProviderRuntime, model_id: str,
                     req: AIRequest) -> AIResponse:
        raise NotImplementedError

    # ── Shared transport ────────────────────────────────────────────────

    async def http(self, rt: ProviderRuntime, method: str, url: str,
                   *, headers: dict = None, body: dict = None,
                   timeout_s: float = None) -> tuple:
        """One validated HTTP call. Returns (status, parsed_body, headers).

        Transport failures raise AIError(TIMEOUT | CONNECTION_FAILED).
        HTTP error statuses are NOT raised here — the adapter classifies them
        with its provider-specific error map (a 429 can mean "slow down" or
        "you are out of credit"; only the body distinguishes them).
        A 3xx raises immediately: redirects are never followed blindly.
        """
        # PIN the connection to the address that was validated.
        #
        # Validating a URL and then handing the hostname to httpx leaves a
        # TOCTOU window: validation resolves DNS, httpx resolves again, and a
        # hostile DNS server can return a public address to the first lookup
        # and 169.254.169.254 to the second. The classifier is then perfectly
        # correct and completely bypassed. So the client is never given the
        # chance to resolve: it is handed an IP that already passed policy.
        #
        # TLS IS NOT WEAKENED. `Host` and `sni_hostname` keep the original
        # hostname, so SNI and certificate verification still target the real
        # name — verified empirically: a pinned request reaches the API and
        # validates its certificate, while a mismatched SNI is refused.
        # `pin` resolves DNS, which blocks. Off the loop it goes.
        pinned = await anyio.to_thread.run_sync(
            endpoint_policy.pin, url, rt.trust_class)
        timeout_s = timeout_s or rt.timeout_s or 45.0

        # Case-insensitive merge. HTTP header names are case-insensitive but a
        # dict is not, so a caller passing `host` in any other casing would
        # leave TWO host headers in the mapping and httpx would refuse the
        # request with LocalProtocolError. Ours is the only one that may
        # survive: it is what the connection was pinned to.
        req_headers = {k: v for k, v in (headers or {}).items()
                       if k.lower() != "host"}
        req_headers["Host"] = pinned["authority"]

        try:
            # Transport hardening mirrors app/services/openai.py: no retries
            # (retry policy belongs to the routing engine), no HTTP/2, no
            # redirect following (SSRF policy), bounded keepalive.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=2),
                http2=False,
                follow_redirects=False,
            ) as client:
                # Try every validated address, in resolution order — the
                # fallback httpx would have done itself had it been given the
                # hostname. `localhost` resolves to ['::1', '127.0.0.1'], and
                # an Ollama server bound to 127.0.0.1 is unreachable if only
                # the first is tried. A CONNECT failure moves to the next
                # candidate; anything else (timeout, TLS, an HTTP status) is
                # the endpoint answering and is returned as-is.
                candidates = pinned["connect_urls"]
                for i, candidate in enumerate(candidates):
                    try:
                        resp = await client.request(
                            method, candidate, headers=req_headers,
                            json=body if body is not None else None,
                            extensions={"sni_hostname": pinned["host"]})
                        break
                    except httpx.ConnectError:
                        if i == len(candidates) - 1:
                            raise
        except httpx.TimeoutException as e:
            raise AIError(code=TIMEOUT, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          provider_detail=f"{type(e).__name__} after {timeout_s}s")
        except httpx.HTTPError as e:
            raise AIError(code=CONNECTION_FAILED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          provider_detail=type(e).__name__)

        if 300 <= resp.status_code < 400:
            # A permitted host redirecting to a forbidden one is the standard
            # SSRF bypass; we refuse rather than follow, and name the target
            # (redacted) so an operator can see what happened.
            loc = resp.headers.get("location", "")[:200]
            raise AIError(code=INVALID_RESPONSE, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          status_code=resp.status_code,
                          provider_detail=f"unexpected redirect to {loc}")

        try:
            parsed = json.loads(resp.text) if resp.text else {}
        except json.JSONDecodeError:
            parsed = {"_raw": resp.text[:2000]}
        return resp.status_code, parsed, dict(resp.headers)

    # ── Error helpers ───────────────────────────────────────────────────

    def error_code_from_body(self, status: int, body) -> str:
        """Map a provider error body onto the taxonomy, or "" to use the
        HTTP-status fallback. Default: OpenAI-style {"error": {...}}."""
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if not isinstance(err, dict):
            return ""
        code = str(err.get("code") or "")
        etype = str(err.get("type") or "")
        msg = str(err.get("message") or "")
        # OpenAI documents billing 429s that must NOT be retried.
        if code in ("credit_balance_exhausted", "organization_spend_limit_exceeded",
                    "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
                    "insufficient_quota", "billing_error"):
            return QUOTA_EXCEEDED
        if (code in ("invalid_api_key", "incorrect_api_key")
                or etype == "authentication_error"
                or "incorrect api key" in msg.lower()):
            return AUTHENTICATION_FAILED
        if etype == "permission_error" or "permission" in etype:
            return PERMISSION_DENIED
        if code == "model_not_found" or "model" in msg.lower() and "not found" in msg.lower():
            return MODEL_NOT_FOUND
        if "context length" in msg.lower() or "maximum context" in msg.lower():
            return CONTEXT_LIMIT_EXCEEDED
        if etype == "rate_limit_error" or code == "rate_limit_exceeded":
            return RATE_LIMITED
        if etype in ("server_error", "api_error"):
            return SERVER_ERROR
        if code == "overloaded_error":
            return PROVIDER_UNAVAILABLE
        return ""

    def http_error(self, rt: ProviderRuntime, status: int, body,
                   request_id: str = "") -> AIError:
        """Build the normalized AIError for an HTTP error status."""
        code = self.error_code_from_body(status, body)
        if not code:
            err = from_status(status)
            code = err.code
        detail = self._error_text(body)
        return AIError(code=code, provider_type=rt.provider_type,
                       provider_instance_id=rt.instance_id,
                       status_code=status, provider_detail=detail,
                       provider_request_id=request_id)

    @staticmethod
    def _error_text(body) -> str:
        """Best-effort human text out of any provider error shape."""
        if not isinstance(body, dict):
            return str(body)[:400]
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:400]
        if isinstance(err, str):
            return err[:400]
        for k in ("message", "msg", "detail"):
            if body.get(k):
                return str(body[k])[:400]
        return json.dumps(body, ensure_ascii=False)[:400]

    # ── Usage helpers ───────────────────────────────────────────────────

    @staticmethod
    def _int(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def extract_usage(self, body) -> dict:
        """{tokens_in, tokens_out, tokens_total, cached, reasoning} — all
        optional. Subclasses override per provider vocabulary."""
        u = (body or {}).get("usage") or {}
        return {
            "tokens_in": self._int(u.get("prompt_tokens", u.get("input_tokens"))),
            "tokens_out": self._int(u.get("completion_tokens", u.get("output_tokens"))),
            "tokens_total": self._int(u.get("total_tokens")),
            "cached": self._int((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
                      if isinstance(u.get("prompt_tokens_details"), dict) else None,
            "reasoning": self._int((u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
                         if isinstance(u.get("completion_tokens_details"), dict) else None,
        }

    # ── Test-connection result helper ───────────────────────────────────

    @staticmethod
    def test_result(ok: bool, status: str, detail: str, latency_ms: int) -> dict:
        return {"ok": ok, "status": status, "detail": detail[:300],
                "latency_ms": int(latency_ms)}
