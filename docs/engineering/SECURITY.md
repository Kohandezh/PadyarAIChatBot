# Security Standard

Binding standard for security review of every non-trivial change.
The *current* security model of the app (what exists today, including
known gaps) is documented in `SECURITY_MODEL.md` — keep the two in sync.

---

## Authentication vs Authorization

Authentication answers:

> Who is making this request?

Authorization answers:

> Is this user allowed to perform this operation on this resource?

Every protected resource operation performs authorization. Never assume
that because a client supplied `conversation_id`, `message_id`, or
`workspace_id`, the client owns that resource.

## Review Checklist

For every non-trivial change, explicitly consider:

- authentication
- authorization
- IDOR (insecure direct object reference)
- privilege escalation
- injection
- XSS
- CSRF where applicable
- SSRF where applicable
- rate limiting
- sensitive data exposure
- mass assignment
- insecure defaults
- information leakage

Security is never an afterthought.

## Resource Enumeration

Consider IDOR/resource enumeration attacks for every resource endpoint.
When appropriate, avoid revealing whether another user's resource exists
— prefer generic not-found responses.

## Secrets and Logging

Never log or expose:

- access tokens
- passwords
- secrets / internal credentials
- unnecessary personal data
- another user's private data

## Verification

If a security boundary is involved, the negative case is tested
explicitly (see `TESTING.md`):

```text
User A can access Conversation A.
User A cannot access Conversation B.
User B cannot modify Message A.
```
