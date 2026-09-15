# API Standards

## General

APIs must be explicit about ownership, authentication, authorization, validation and error behavior.

## Design Rules

- Prefer existing route and schema conventions.
- Validate at the boundary and keep business rules in the appropriate service layer.
- Do not add an endpoint with no production consumer.
- Do not add parameters with no caller.
- Avoid API behavior that depends accidentally on browser cookies unless that dependency is intentional and documented.
- Preserve backwards compatibility when required by existing clients.
- New authentication designs follow the bearer-style target standard documented in `SECURITY.md`.

## Resource Binding

Authorization must be bound to the resource actually being written or read. Never authorize a resource selected from one identifier and persist a different resource selected from another identifier.

## Errors

Errors should be actionable for clients, safe for users, and stable enough for documented consumers. Avoid leaking secrets, internal stack traces or sensitive resource information.

## Testing

Every new endpoint needs tests for:
- happy path;
- validation failure;
- unauthorized/forbidden access where applicable;
- important edge conditions;
- production consumer/wiring where practical.
