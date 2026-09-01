# API Standards

Binding standard for every new endpoint. Source principles:
`ENGINEERING_CONSTITUTION.md`. Current-state exceptions are listed at the
bottom of the constitution.

---

## API-First Architecture

The API must remain independent of browser-specific behavior and be
usable by web, PWA, React Native, iOS, Android, and future clients.

Do not introduce browser-specific assumptions into domain logic or API
contracts.

## Authentication

Protected APIs use explicit Bearer authentication:

```http
Authorization: Bearer <access_token>
```

Do not use cookies for authentication, user identification, conversation
identification, authorization, workflow state, or API state.
Authentication state and resource state remain separate.

## Resource Identity

Resource identity must be explicit:

```text
user_id, workspace_id, conversation_id, message_id
```

Possession of a resource ID is never proof of authorization. Every
protected resource is authorized server-side.

## API Design

Prefer resource-oriented APIs:

```http
POST /conversations
POST /conversations/{conversation_id}/messages
GET  /conversations/{conversation_id}/messages
POST /messages/{message_id}/feedback
```

Avoid frontend-specific APIs such as `/send-chat-message`,
`/get-my-chat-history`, `/submit-message-rating` unless there is a strong
architectural reason.

## Endpoint Contract

Every endpoint has an explicit contract covering:

- authentication
- authorization
- request schema
- response schema
- validation
- errors
- pagination where applicable
- idempotency where applicable
- concurrency considerations

## No Hidden State

Important identifiers are explicit in API contracts:

```json
{
  "conversation_id": "conv_123",
  "message_id": "msg_456"
}
```

Prefer explicit JSON responses over custom headers for application-level
resource identifiers.

## Concurrency

Assume every endpoint can receive concurrent requests and that clients
retry. Before finalizing an operation, consider race conditions,
duplicate requests, transaction boundaries, lost updates, ordering, and
consistency. Do not design assuming requests happen exactly once.

## Idempotency

Any operation that may be retried must be evaluated for idempotency:
message creation, payment operations, webhook processing, mutations over
unreliable networks. When appropriate, use an idempotency key or another
server-side mechanism to prevent duplicate effects.

## Error Handling

Use the repository's standard error contract. Do not introduce
inconsistent error shapes. Errors are predictable, machine-readable,
useful to clients, and safe to expose. Do not leak secrets, tokens,
internal credentials, or unnecessary implementation details.

## Pagination

Any endpoint returning an unbounded collection must define pagination.
Do not load an entire collection into memory. Do not introduce an
endpoint that assumes the dataset will remain small.
