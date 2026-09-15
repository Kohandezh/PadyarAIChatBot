# Testing Standard

## Principle

Test the scenario and the contract, not just implementation details.

## Test Selection

### Small bugfix
Use focused regression coverage when the defect has a meaningful failure mode. Avoid unnecessary ceremony.

### New feature
Cover the end-to-end scenario, important failure states and the production wiring.

### Security-sensitive feature
Test both allowed and denied paths, resource binding and any relevant boundary conditions.

### Browser-visible feature
Use browser/e2e tooling to verify interaction and visible states. A passing backend test is not sufficient evidence for UI completion.

## CI

CI is the merge signal. Local checks are fast feedback only unless the repository explicitly defines them as a merge gate.

## Regression Quality

A regression test should fail if the actual fix is removed. Avoid tests that merely reproduce the implementation's current structure.

## Test Debt

If a full test is temporarily impractical, record the limitation and the reason. Do not silently downgrade confidence.
