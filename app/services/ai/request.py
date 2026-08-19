"""The provider-neutral AI request and response.

WHY THIS SHAPE (and not "an OpenAI request renamed")
----------------------------------------------------
The capability matrix (docs/engineering/ai-providers/01-capability-matrix.md §3)
found three different message containers, two different system-prompt
representations and a URL-embedded model id across the nine supported
providers. Every field here was chosen so that ALL of them fit:

  * `system_prompt` is its own field. Anthropic hard-400s on a system-role
    message at index 0; Gemini uses a top-level differently-typed field.
    Prepending it as `messages[0]` in shared code is the single easiest way
    to break two providers at once, so the neutral request never does it.
  * `temperature` / `top_p` are PREFERENCES, not commands. Five of nine
    providers reject or deprecate them today (matrix §4). The adapter decides
    per model whether the value is sent, transformed or dropped.
  * `reasoning` is first-class ("off"/"low"/"medium"/"high"), defaulting OFF
    for CLASSIFICATION. Five providers think out loud by default and bill it
    as output tokens (matrix §5) — Padyar already hit this once with
    gpt-5-nano spending its whole token budget on hidden reasoning.
  * `max_output_tokens` is always resolved to a concrete number before the
    adapter sees it. Anthropic REQUIRES it (omitting = 400), and the
    reasoning-budget incident proves a silent global default is unsafe.
  * `messages` roles are only "user" | "assistant". Gemini's "model" role,
    Anthropic's block arrays and Gemini Interactions' typed steps are all
    adapter-side translations — never leaked here.

Usage fields on the response are COMPUTED, not copied (matrix §7): Anthropic's
`input_tokens` counts only tokens after the last cache breakpoint, so the
adapter sums cache_read + cache_creation + input. Unknown stays None — never 0,
never guessed.
"""
from dataclasses import dataclass, field

# The two tasks Padyar routes today (app/routers/chat.py). STT (Whisper) is
# deliberately NOT a routed task this phase — it keeps its own path.
TASK_CHAT = "chat"
TASK_CLASSIFY = "classify"
TASKS = (TASK_CHAT, TASK_CLASSIFY)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# Reasoning preferences. "default" means "no preference stated" — the wrapper
# resolves it per task (chat: provider default, classify: off) before the
# adapter sees it.
REASONING_OFF = "off"
REASONING_LEVELS = ("off", "low", "medium", "high")

RESPONSE_TEXT = "text"
RESPONSE_JSON_OBJECT = "json_object"

# Normalized finish reasons (open set — adapters map unknowns to "other").
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_CONTENT_FILTER = "content_filter"
FINISH_TOOL_CALLS = "tool_calls"
FINISH_OTHER = "other"


@dataclass
class AIMessage:
    role: str                       # ROLE_USER | ROLE_ASSISTANT
    content: str


@dataclass
class AIRequest:
    task: str
    messages: list                                  # [AIMessage]
    system_prompt: str = ""
    max_output_tokens: int = 0                      # always resolved by the wrapper
    temperature: float | None = None                # preference only — adapter may drop
    top_p: float | None = None                      # preference only — adapter may drop
    reasoning: str = "default"                      # "default"|"off"|"low"|"medium"|"high"
    response_format: str = RESPONSE_TEXT            # text | json_object
    timeout_s: float | None = None                  # per-request cap (route default if None)
    request_id: str = ""
    correlation_id: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class AIResponse:
    content: str
    finish_reason: str = FINISH_STOP
    task: str = ""
    provider_type: str = ""
    provider_instance_id: str = ""
    provider_name: str = ""
    model: str = ""
    # Computed usage — None means the provider did not report it.
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_total: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int = 0
    provider_request_id: str = ""
    request_id: str = ""
    correlation_id: str = ""
    # Filled by the routing engine, not the adapter.
    route_priority: int = 0
    attempt_count: int = 0
    failover_count: int = 0
    # Filled by the engine from the pricing table (None = unknown pricing).
    cost: float | None = None
    currency: str = ""
