---
name: code-review
description: Perform comprehensive code review covering functionality, quality, security, and performance. Use this when reviewing PRs or code changes.
---

You are a senior software engineer reviewing code change. Carefully analyze the code using the following guidelines:

### Validate Functionality

1. **Confirm Intended Behavior**
   - Does the code deliver what was requested in the requirement or PR description?
   - Have you mentally traced through the logic to verify correctness?

2. **Exercise Edge Cases**
   - Have you considered boundary conditions and guard conditions?
   - Are null/undefined/empty values handled gracefully?
   - Are error paths exercised (locally or mentally)?

3. **Check Error Handling & Logging**
   - Are error handling paths clear and appropriate?
   - Is logging helpful for debugging without being excessive?
   - Are errors propagated or handled appropriately at each level?

### Assess Quality

1. **Code Design & Clarity**
   - Are functions focused and single-purpose?
   - Are names descriptive and consistent throughout?
   - Is the code readable and maintainable?

2. **Watch for Anti-patterns**
   - Is there unnecessary duplication that could be refactored?
   - Is there dead code that should be removed?
   - Are there overly complex or nested structures that could be simplified?

3. **Documentation**
   - Does documentation and comments reflect the latest changes?
   - Are complex sections or non-obvious logic explained?
   - Are function signatures and return types clear?

### Review Security and Risk

1. **Injection Points & Validation**
   - Are there potential injection points (SQL, XSS, command injection)?
   - Is all user input validated and sanitized?
   - Are there insecure defaults that could be exploited?

2. **Credentials & Secrets**
   - Are secrets, API keys, or credentials exposed in the code?
   - Are sensitive values properly stored and retrieved from environment variables or secure storage?

3. **Performance & Scalability**
   - Could this change introduce performance bottlenecks?
   - Will it scale with growing data or user load?
   - Are there resource leaks or unbounded operations?

### General Code Quality

1. **Readability**: Is the code easy to understand? Are function/variable names descriptive and consistent?
2. **Code Style**: Does the code follow the project's style guide or common conventions?
3. **Modularity**: Are functions/classes small and focused on a single responsibility?
4. **Error Handling**: Are potential errors and edge cases properly handled?

### Security-Focused Review

1. **Input Validation & Sanitization**
   - Are all inputs (especially user inputs) validated and sanitized before processing or storage?
   - Are there protections against injection attacks (e.g. SQL injection, command injection, XSS)?

2. **Authentication & Authorization**
   - Is access to protected resources properly gated by authentication checks?
   - Are authorization checks enforced correctly and consistently?

3. **Sensitive Data Handling**
   - Are secrets, tokens, or passwords stored securely (e.g., using environment variables, encryption)?
   - Is any sensitive data exposed in logs, responses, or frontend code?

4. **Third-party Dependencies**
   - Are libraries and packages up-to-date and reputable?
   - Are any packages known to have security vulnerabilities?

5. **Secure Defaults**
   - Does the code opt-in to secure settings and defaults (e.g., HTTPS, secure cookies, CSP headers)?
   - Are default configurations safe if the developer forgets to change them?

6. **Audit Trails & Logging**
   - Are key actions logged securely without exposing sensitive information?
   - Can logs help detect suspicious behavior?

7. **Data Exposure**
   - Are API responses or front-end data leaks prevented?
   - Are there any unintentional data exposures (e.g., excessive permissions, unnecessary fields returned)?

### Anti-Pattern Check

1. **Architecture & Codebase Integration**
   - Does the new feature follow existing architectural patterns (router → service → db/auth layering)?
   - Are existing utilities and helpers reused instead of duplicated (e.g. `app/utils/normalizer.py`, `app/db/queries.py`)?
   - Does the code integrate with established systems (admin auth, chat security, settings, SQLite queries) correctly?
   - Are imports consistent with the rest of the codebase (no parallel ad-hoc helpers)?

2. **Consistency with Established Patterns**
   - Are database reads/writes going through the query functions in `app/db/queries.py` rather than inline SQL scattered across routers?
   - Are admin endpoints gated with `Depends(verify_admin)` (or the project's equivalent) from `app/auth/security.py`?
   - Are request/response shapes defined as Pydantic models in `app/models.py` rather than passing raw dicts?
   - Are white-label / runtime settings read via `get_setting(key, default)` with sensible defaults, not hardcoded?

3. **Code Organization & Structure**
   - Is business logic in `app/services/` and route wiring in `app/routers/`, not mixed?
   - Are standalone dev/ops scripts in `scripts/` rather than imported into the app?
   - Does shared chat behavior live in `static/chat/core.js` with themes overriding via `ChatConfig`, instead of forking the JS per theme?

4. **Conventions Over Invention**
   - Does the code introduce new patterns that contradict existing conventions?
   - Are naming conventions consistent with the rest of the codebase?
   - Does the feature reuse existing infrastructure (module registry, theme partials, settings table) rather than creating parallel systems?

5. **Commented Code & Dead Code**
   - **CRITICAL**: Flag any commented-out code blocks — remove unused code instead of commenting it out.
   - Are there disabled features implemented as commented code that should be removed?
   - Check for `#` or `"""..."""` blocks (and `//` / `/* */` in JS/CSS) containing production code that was commented out.

### This Project's Checklist (PadyarAIChatbot)

Run through these on every change — they reflect the real conventions in `CLAUDE.md`:

1. **SQL safety** — All `sqlite3` queries use parameterized placeholders (`?`), never string-built/f-string SQL with user input. This is the #1 thing to flag.
2. **Admin route protection** — Every admin/management route requires the admin session dependency (`Depends(verify_admin)` from `app/auth/security.py`). Fail closed: missing the dependency is a security bug, not a style nit.
3. **Public chat security** — The `/chat` flow still validates the HMAC chat token, checks `Origin`/`Referer` against the allowlist, and enforces the rate limit (2 req / 30 s per IP). A change must not weaken or bypass any of the three.
4. **Input validation** — Request bodies are validated by Pydantic models (`app/models.py`); user text is normalized via `app/utils/normalizer.py` where the pipeline expects it.
5. **Secrets via env** — No API keys/credentials in code, logs, responses, or templates. `OPENAI_API_KEY` and friends come from the environment; `.env` is gitignored. Flag anything that prints or returns secrets.
6. **Simplicity / grandmother test** — User-facing UI and admin flows must be understandable in seconds, no jargon, no needless config. Flag premature abstraction and feature flags for simple features (per the CLAUDE.md simplicity rules).
7. **Persian / RTL correctness** — Strings render correctly in RTL, Vazirmatn font is used, and Persian normalization isn't accidentally stripped. Admin templates and chat themes must not break direction.
8. **Modules over one-offs** — A new feature is added as a module in `app/modules/registry.py` (optional unless every customer needs it), with its own router/service, not bolted onto an unrelated file.
9. **Dependencies** — New packages are installed with `pip` and recorded in `requirements.txt`.
10. **Pre-commit syntax** — Changed `.py` files pass `python -m py_compile` (mandatory before commit). Note: there is no linter/formatter configured in this repo — don't flag for a tool that isn't set up.

### Performance & Resource Review

1. **Search pipeline** — Retrieval is BM25 (`app/services/bm25.py`) plus local model2vec embeddings (`app/services/embeddings.py`), fused by the feature reranker (`app/services/rerank.py`). There is no TF-IDF vectorizer and no `search_backend` setting; both were removed. The BM25 and embedding indexes are built once per reindex and must not be rebuilt on every chat request. Watch for repeated full-dataset work in the hot path (`app/services/search.py`).
2. **Database** — Queries are bounded (no unbounded `SELECT *` over growing `chat_logs` without limits/pagination); connections are opened and closed correctly.
3. **External calls** — GapGPT/OpenAI calls have timeouts and are only made on the Tier-2 fallback path, not eagerly; failures degrade gracefully.
4. **Media & static** — Uploaded videos/images are size/type validated; large files aren't loaded fully into memory unnecessarily.
5. **Frontend (chat/admin)** — No obvious leaks (timers/event listeners cleaned up in `core.js`); no blocking heavy work on the main thread for long lists.

Call out any inline comments that should be removed.
