# Engineering Standards

## Core Principle

Do not implement the smallest change that makes the current
requirement pass.

Before implementing a feature, consider:

- system-wide consistency
- existing architecture
- future clients
- security
- concurrency
- failure modes
- observability
- backwards compatibility
- testability
- maintainability
- scalability

Prefer a coherent system-level solution over a local patch.

---

## Architecture

This application is API-first.

The API MUST NOT depend on browser-specific state.

Do not use cookies for:

- authentication
- user identification
- conversation identification
- authorization
- API state
- workflow state

Authentication MUST use explicit Bearer access tokens.

Resource identity MUST use explicit IDs.

Authorization MUST be performed server-side for every protected
resource operation.

Never trust client-provided resource IDs without ownership/access
validation.

---

## API Design

Design APIs around resources rather than frontend actions.

Prefer:

```
POST /conversations
POST /conversations/{id}/messages
GET  /conversations/{id}/messages
POST /messages/{id}/feedback
```

Avoid frontend-specific endpoints such as:

```
/send-chat-message
/get-my-chat-history
/submit-message-rating
```

unless there is a strong architectural reason.

---

## Client Independence

The API must be usable by:

- Web
- PWA
- React Native
- iOS
- Android
- future clients

Do not introduce browser-only assumptions into the API.

---

## Security

Never rely on obscurity of IDs.

Never assume that because a user supplied a conversation_id,
they are allowed to access it.

Every resource lookup must be followed by authorization.

Do not leak whether another user's resource exists.

Prefer generic not-found responses where appropriate.

---

## Error Handling

Do not only handle the happy path.

For every endpoint consider:

- invalid authentication
- expired authentication
- unauthorized resource
- missing resource
- malformed input
- duplicate request
- retry
- timeout
- partial failure
- concurrent requests
- race conditions

---

## Data Integrity

Database invariants must be enforced at the database level
where practical.

Do not rely exclusively on application-level checks.

Use transactions where multiple writes must remain atomic.

---

## Idempotency

Any operation that can be retried by a client must be evaluated
for idempotency.

Do not assume network requests happen exactly once.

---

## Performance

Do not optimize prematurely.

But do not introduce obviously inefficient patterns such as:

- N+1 queries
- repeated full-table scans
- loading entire collections when pagination is required
- unnecessary serialization/deserialization
- blocking operations in async request paths

Use existing project patterns when they are sound.

---

## Testing

Do not only write tests for the happy path.

Every feature should consider:

1. happy path
2. validation failure
3. authorization failure
4. missing resource
5. concurrency
6. regression
7. backwards compatibility

Tests should validate behavior, not implementation details.

---

## Before Coding

Before modifying code:

1. Inspect the relevant architecture.
2. Find existing implementations of the same pattern.
3. Identify inconsistencies.
4. Determine whether the requested change exposes
   a broader architectural problem.
5. Propose the cleanest system-level approach.
6. Only then implement.

Do not start coding immediately after reading the task.

---

## Senior Engineering Rule

If the requested implementation appears to require a local
workaround, STOP and ask:

"Why does the architecture require this workaround?"

Fix the underlying abstraction when reasonable instead of
adding another special case.

Avoid accumulating:

- flags
- one-off headers
- special cases
- duplicated logic
- endpoint-specific hacks
- client-specific behavior

Every new abstraction should reduce complexity rather than
move complexity somewhere else.
