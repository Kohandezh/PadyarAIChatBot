# When a spike needs an ADR

Read this at the **outcome** step of a spike. The spike is the research. The ADR is the durable record of the decision it produced. Most spikes route straight to a spec. Escalate to an ADR only when the recommendation carries lasting architectural weight.

## Where an ADR lives here

There is no `docs/rfc/` directory and no RFC tier in this repo. Architectural decisions go into **`docs/engineering/DECISIONS.md`** as a new numbered entry, `ADR-NNN`. That file already holds 21 of them, and most are short: decision, why, alternative rejected, consequence accepted. Check the last number in the file before you pick one.

`docs/engineering/templates/ADR.md` is the fuller form (Context, Decision, Why, Alternatives, Consequences, Migration, Revisit Conditions, Related Artifacts). Use it when the decision is big enough to deserve the structure. Match the surrounding entries in `DECISIONS.md` when it is not. Both are correct. An entry that no one can read is not.

`DECISIONS.md` is written in Persian today. Write a new entry in the language of the surrounding file unless the user asks otherwise.

## The escalation test

`docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md` says an ADR is for a decision that "introduces or changes a durable architectural choice". Route on the recommendation:

| Recommendation                                                         | Next                   |
| ----------------------------------------------------------------------- | ---------------------- |
| Clear approach, reuses an existing pattern, no lasting risk             | → **SPEC** or **PLAN** |
| New dependency or trust boundary, cross-cutting, or hard to reverse     | → **ADR**, then a spec |

Concretely, write an ADR when the recommendation introduces any of these:

- **A new persistence model or a new table that other features will build on.** ADR-013 (the synonym table's composite key) is the shape of this: a schema detail that changed what two backends did to a visitor.
- **A new external dependency or service.** A new Python package in `requirements.txt` that becomes load-bearing, a new provider behind the AI wrapper, a new host service like the TTS engine. ADR-007 and ADR-008 are both this.
- **A new integration or trust boundary.** A new auth surface, a new place user data lives, a new way something reaches the app from outside. In a kiosk product this is the one to be careful about.
- **A new cross-cutting pattern.** Something every future feature will copy.
- **A change to the answer pipeline's shape or its thresholds.** The tiers and their gates are the product. ADR-003, ADR-004 and ADR-005 are all threshold and tier decisions, and each records the measurement behind the number.
- **A one-way door.** Expensive to migrate off later. Remember there is no migration downgrade path here: rolling back a schema change means restoring a backup.

If you are unsure, write the ADR. It is a few paragraphs in an existing file, which is far cheaper than an unreviewed architectural mistake that the next three years of work is built on.

**Worked example from this repo.** ADR-012 (`TTS_WORKERS`, one model instance per GPU) came out of a performance investigation. It fired two triggers: it changed how a host service allocates hardware, and it set a default (`1`) that a single-card customer install depends on. The measurements are in the entry, which is why it is still readable a month later.

**Counter-example.** A new optional module that reuses `app/modules/registry.py`, an existing router pattern and an existing auth dependency needs **no** ADR. The pattern is already decided. Adding an entry for it adds noise and makes the real decisions harder to find.

## What the ADR adds, and what the spike records

- The **spike** records the outcome `→ ADR` with a one-line reason naming which trigger fired, and lists the questions the ADR has to settle. It does **not** write the ADR.
- The **ADR** records the decision itself: the context and the constraint that forced it, what was chosen, why it fits this repository and this product, the alternatives rejected and why, and the consequences including the ones you did not want. Say what it costs, not only what it buys. The best entries in `DECISIONS.md` name the price out loud ("this invalidates every cached entry, re-run `deploy/45-prerender.sh` after deploy").
- An ADR that changes an existing decision does not delete the old one. Mark the old entry superseded or retired with a date, the way ADR-006 was. History is the point.

## At the outcome step: checklist

1. Apply the escalation test above.
2. **If → SPEC or → PLAN:** record it and move on. The spike is now the source the downstream author writes from.
3. **If → ADR:** record the outcome, write a one-line reason naming the trigger, list the questions the ADR must settle, and stop. Point the author at `docs/engineering/DECISIONS.md` and `docs/engineering/templates/ADR.md`. Do not start the ADR from inside the spike.
4. Either way, update the feature's row in `docs/features/INDEX.md`.

## What this repo does not have

Said plainly so nobody looks for it: there is no RFC tier, no `docs/rfc/`, no Accepted/Rejected RFC lifecycle, and no rule that a spec must link one before it can leave Draft. An ADR here is a record of a decision, not a gate that a separate reviewer signs off before design can start. The review happens in the PR, like every other change.
