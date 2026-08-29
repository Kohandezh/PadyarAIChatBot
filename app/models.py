from typing import Optional

from pydantic import BaseModel, Field


class VisitorProfile(BaseModel):
    """What the visitor said about their work — never who they are.

    Registration collects a name and phone too; those deliberately do not
    travel with chat messages, because nothing in the answer depends on them.

    Still here, and still exactly this shape, but it is now built by the
    SERVER: app/auth/visitor.py fills it from the `visitors` row behind the
    session cookie. The targeted-visit planner reads the same three fields it
    always did, so nothing downstream changed — only where they come from.
    """
    job: str = ""
    position: str = ""
    interests: str = ""


class ChatRequest(BaseModel):
    message: str
    # UI language. "en" serves the English side of the bilingual knowledge base;
    # anything else falls back to Persian, which is always populated.
    lang: str = "fa"
    # `visitor: Optional[VisitorProfile]` was here, and it was a hole: the
    # CALLER stated their own job, position and interests, and the planner
    # believed them. Four extra fields in a POST body made anybody a
    # registered visitor. The profile now comes from the padyar_vs session
    # cookie, resolved by the `resolve_visitor` middleware in app/main.py, and
    # the router reads http_request.state.visitor.
    #
    # Nothing replaces the field, on purpose. Pydantic ignores body keys it
    # does not declare, so a browser still running the old frontend keeps
    # chatting and its `visitor` object is simply dropped — which is the whole
    # point, not a leftover.


class ChatOption(BaseModel):
    """One tappable choice in a numbered list.

    `video_url` rides along even though the frontend does not need it today —
    a chip tap round-trips through /chat and the pick tier attaches the clip —
    so a title and its booth video can never drift apart.
    """
    n: int
    id: str
    title: str
    video_url: Optional[str] = None


class ChatResponse(BaseModel):
    type: str
    text: str
    video_url: Optional[str] = None
    confidence: float
    source: str
    # Additive: defaults to an empty list, so every answer the chatbot gives
    # today keeps its exact shape.
    options: list[ChatOption] = []


class LoginRequest(BaseModel):
    username: str
    password: str
    sec_answer: str


class ToggleRequest(BaseModel):
    enabled: bool


class SynonymRequest(BaseModel):
    source: str
    target: str


class ThemeActivateRequest(BaseModel):
    name: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class ChangeSecurityQuestionRequest(BaseModel):
    current_answer: str
    new_question: str
    new_answer: str


class BackupScheduleRequest(BaseModel):
    enabled: bool
    interval_hours: int
    time: str  # "HH:MM", used when interval >= 24h


class AIConnectionRequest(BaseModel):
    api_base: str = ""
    api_key: str = ""          # empty = keep the currently stored key
    model_chat: str = ""
    model_classify: str = ""
    model_stt: str = ""
    feature_tts: bool = True
    feature_stt: bool = True
    # `search_backend` was removed 2026-08-28 (TF-IDF is gone; there is one
    # engine). An older admin page may still POST it — Pydantic ignores an
    # unknown field here, so an out-of-date browser tab keeps working.
    default_lang: str = "fa"        # first-visit chat language: "fa" | "en"


class TTSPreviewRequest(BaseModel):
    """One "listen to this" request from the Text-to-Speech admin page.

    The three bounds are Chatterbox's own, repeated here so an out-of-range
    slider is refused by this app with a 422 instead of travelling to the
    speech service and coming back as a validation error from a component the
    admin has never heard of.
    """
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str = ""                 # empty = the model's built-in voice
    exaggeration: float = Field(0.5, ge=0.25, le=2.0)
    cfg_weight: float = Field(0.5, ge=0.2, le=1.0)
    temperature: float = Field(0.8, ge=0.05, le=5.0)


class AssistantContentRequest(BaseModel):
    name: str
    org: str
    phone: str
    website: str
    knowledge: str
    personality: str
    tone: str
    medical_safety: str
    password: Optional[str] = None  # required only when medical_safety changes
    # The keys that make a deployment in a different category a DATA job.
    # Optional so an older admin page (or a scripted POST written before these
    # existed) keeps working and simply leaves them unchanged.
    domain: Optional[str] = None          # what this assistant is about, fa
    domain_en: Optional[str] = None       # ... and en
    refusal_fa: Optional[str] = None      # what it says when a question is not
    refusal_en: Optional[str] = None
    collection_noun_fa: Optional[str] = None   # «شرکت» / "companies" in lists
    collection_noun_en: Optional[str] = None
    options_shown: Optional[int] = None        # names per numbered list (1..15)
    chat_log_retention_days: Optional[int] = None  # 0 = keep forever


class WhitelabelBrandingRequest(BaseModel):
    """The 5 white-label keys (see app/services/branding.py for the contract).

    Field names are the admin-form / API names; the router maps them onto the
    `whitelabel_*` setting keys and validates every value server-side — the
    native color picker always emits #rrggbb, but the API is the backstop.
    All fields default to empty so a partial POST can never 422 on a missing
    key; the router rejects empties where empty is not legal (app_name).
    """
    app_name: str = ""
    logo_url: str = ""
    primary_color: str = ""
    accent_color: str = ""
    welcome_text: str = ""


class IdleVideosRequest(BaseModel):
    """The avatar's idle loop: one main clip plus up to
    idle_video.IDLE_VIDEO_EXTRA_MAX random extras. Every URL must already be a
    file uploaded through /admin/api/upload_video — the router checks that
    with idle_video.is_valid_video_url before saving."""
    main: str = ""
    extra: list[str] = []


class SmsSettingsRequest(BaseModel):
    """Registration/SMS settings.

    `password` and `api_key` are write-only: an empty string means "keep what
    is stored", so the panel never has to receive a secret back in order to
    save the rest of the form. Both are encrypted before they are stored.

    One field here = one row in `ASANAK_FIELDS` (app/services/sms.py) = one
    input in templates/admin/settings_sms.html. That is the whole checklist
    for adding a gateway field.
    """
    enabled: bool = False
    provider: str = "asanak"
    username: str = ""
    password: str = ""
    # Not used by Asanak's documented send path (username + password in the
    # body is the only published scheme). Kept because an account may need it
    # for another Asanak product.
    api_key: str = ""
    source: str = ""
    # An approved template's id. A SERVICE line carries only approved content:
    # with this set, the code is sent as a template parameter instead of free
    # text — see send_asanak_template. Empty = plain sendsms.
    template_id: str = ""
    # Two more approved templates, both carrying a LINK instead of a code.
    # They are separate ids because Asanak approves one template per text, and
    # neither may stand in for the other: a contact told "your text was not
    # approved" when they were only being invited is worse than no SMS at all.
    invite_template_id: str = ""
    reject_template_id: str = ""
    # Messages allowed per day across every kind of SMS. "0" means no cap.
    # A string, like every other field here, so the whole form is one shape.
    # The router turns it into a number and refuses anything else.
    daily_budget: str = "0"
    url: str = ""
    status_url: str = ""
    credit_url: str = ""
    template_url: str = ""
    trim: bool = True
    # Asanak's own default is 1 (deliver to blacklisted numbers too).
    send_to_blacklist: bool = True
    sms_host: str = ""


class SmsTestRequest(BaseModel):
    destination: str


class LogTruncateRequest(BaseModel):
    """A destructive log deletion. Every field narrows what is removed.

    `table` is accepted so an operator can clear one store, but the router
    refuses "audit_logs" — the evidence of an administrator's own actions must
    not be erasable from the screen that performs them.
    """
    category: str = ""
    level: str = ""
    table: str = ""
    older_than_days: int = 0


class LogSettingsRequest(BaseModel):
    """Retention is three independent windows on purpose: lowering the
    operational one must not shorten the audit or security trail."""
    retention_days: int = 90
    audit_retention_days: int = 365
    security_retention_days: int = 365
    debug_enabled: bool = False
    min_level: str = "info"
    # metadata | redacted | full — "full" persists conversation text and is an
    # explicit, audited operator decision, never a default.
    content_policy: str = "redacted"


class ServiceActionRequest(BaseModel):
    """`action` is matched against an allowlist dict server-side. It never
    reaches a shell, a path or an attribute lookup — see service_control."""
    action: str


class SessionRevokeRequest(BaseModel):
    """Sessions are revoked by the 8-char fingerprint the listing returns, not
    by the full token, so a leaked listing cannot be replayed as a cookie."""
    fingerprint: str = ""
    all_others: bool = False
