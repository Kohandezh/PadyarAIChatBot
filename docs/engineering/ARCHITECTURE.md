# Architecture Standard

## Principle

Architecture should make the intended behavior easy to reason about and hard to misuse.

## Before Adding a New Pattern

Check:
1. existing code patterns;
2. existing modules/services/components;
3. existing configuration;
4. existing data model;
5. existing security/session lifecycle;
6. existing UI patterns;
7. whether the requested behavior can be implemented without a new abstraction.

## Boundaries

Keep responsibilities at the layer where they naturally belong. Do not create layers solely to satisfy an abstract architecture diagram.

## Modules

Padyar uses a module registry. New optional capabilities should follow the existing module pattern rather than introducing an independent feature-loading mechanism.

## Decisions

When two materially different architectural approaches remain viable, record the decision and rationale in the repository's architecture decision log.

## Target vs Current State

Architecture documents must distinguish:
- current implementation;
- target architecture;
- migration plan.

A target architecture is not permission to rewrite unrelated legacy systems opportunistically.
