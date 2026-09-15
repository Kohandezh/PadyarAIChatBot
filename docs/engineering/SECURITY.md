# Security Standard

## Authentication Target

New API authentication should use bearer-style authentication rather than introducing new browser-cookie authentication.

This is a target standard, not a statement that the current codebase is bearer-only.

## Current-State Exceptions

The current PadyarAIChatbot codebase contains established mechanisms including:
- admin cookie sessions;
- HMAC-signed chat tokens;
- visitor/session cookies.

These are current-state mechanisms. They should not be removed or rewritten solely because the target standard is different.

## Rules for New Work

- Do not introduce a new authentication mechanism without an architecture decision.
- Do not bypass an existing security boundary to make a feature easier to implement.
- Understand the full session/token lifecycle before modifying authentication.
- Security-sensitive changes require tests for both allowed and denied paths.
- Prefer server-side ownership of identity and authorization decisions.
- Never authorize one resource and write another resource.
- Treat identifiers used for authorization and identifiers used for persistence as an explicit binding that must be verified.

## Migration

A migration from current mechanisms to bearer-only authentication is separate work unless explicitly included in scope. Migration must define compatibility, rollout, revocation, client behavior and rollback.
