# Engineering Constitution

## 1. Outcome Over Mechanism

The requirement describes the outcome. The proposed implementation is a hypothesis.

Engineers may reject the proposed mechanism when evidence shows a better approach is safer, simpler, more maintainable or more consistent with the system.

## 2. Root Cause Before Compensation

Do not add a workaround to conceal a broken lifecycle.

When a request proposes a compensating mechanism, trace the data, state and ownership lifecycle first. Fix the underlying boundary when practical.

## 3. Minimum Necessary Complexity

Prefer the smallest architecture that can safely support the real requirement and expected evolution.

Avoid:
- speculative abstractions;
- premature configurability;
- unnecessary feature flags;
- duplicated frameworks;
- wrappers with no independent responsibility;
- one-off infrastructure for one caller.

## 4. Consistency Is a Feature

Existing repository patterns are a default. A new pattern requires a reason.

Consistency reduces cognitive load for users, maintainers and future agents.

## 5. Every Capability Must Be Reachable

A capability that is not wired to a real production caller is incomplete.

Reader/writer, producer/consumer and route/UI pairs must close in the same change.

## 6. Tests Protect Scenarios

A test should fail when the behavior that matters is removed or regresses. Tests that merely exercise internal code without protecting the scenario provide weak evidence.

## 7. Documentation Must Describe Reality

Current state, target state and planned work must be distinguishable.

Never use documentation to make an implementation appear more complete than it is.

## 8. UX Is Engineering

User-facing work is complete only when the intended user can discover, understand and complete the task successfully, including relevant failure and edge states.

## 9. Exceptions Are Explicit

When the repository temporarily violates a standard, document:
- what exists today;
- why it exists;
- what the target is;
- what new work must do;
- whether migration is planned.

Do not silently normalize exceptions into permanent architecture.
