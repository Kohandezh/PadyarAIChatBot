# UI/UX Engineering Standard

## Purpose

UI/UX is part of product engineering. The goal is not merely visual polish; the goal is a clear, predictable, accessible and efficient experience.

## Mandatory Gate

For every user-facing feature or browser UI change:

1. Inspect existing screens and patterns.
2. Load the repository UI/UX skill before implementation.
3. Load **UI-UX Pro Max** when it is installed in the repository/tooling.
4. Define the user flow and interaction model.
5. Implement using existing design patterns unless a new pattern is justified.
6. Cover relevant states and responsive behavior.
7. Verify with browser/e2e tooling.
8. Perform a post-implementation UI/UX review.
9. Fix findings before completion.

The agent must not wait for the user to explicitly say "use UI-UX Pro Max".

## What the Review Must Check

- information hierarchy;
- discoverability;
- interaction clarity;
- copy and terminology;
- loading, empty, success and error states;
- disabled and permission states;
- long content and high-volume states;
- mobile/narrow layouts;
- keyboard operation;
- focus visibility;
- accessible labels and semantics;
- visual consistency with existing themes/components;
- unnecessary steps or decisions;
- destructive action confirmation and recovery;
- whether the UI solves the actual user scenario.

## Do Not Blindly Implement UI Requests

"Add notifications" does not automatically mean "add a notification page".

First determine whether the product need is better served by a toast, inline status, badge, notification center, modal, email/push notification or another interaction.

Use product intent and existing patterns to make this decision. Ask the product owner only when ambiguity materially changes behavior or scope.

## Design Skill Precedence

UI-UX Pro Max is a design/review aid. It does not override:
1. product intent;
2. accessibility and correctness;
3. the repository's established design system;
4. security and technical constraints.

Do not introduce visual novelty for its own sake.

## Definition of Done

A UI change is complete only when it works behaviorally, visually and responsively, and when the resulting interaction is understandable without technical knowledge.
