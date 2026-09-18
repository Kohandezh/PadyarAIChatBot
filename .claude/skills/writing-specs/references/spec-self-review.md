# Spec Self-Review Checklist

Run this after writing the spec and before anyone else reads it. Fix every finding inline. Do not hand over a spec with known gaps.

Section numbers refer to `docs/engineering/templates/SPEC.md`, which has 13 sections.

## 1. Placeholder scan

- No "TBD", "TODO", or "fill in later" anywhere in the document.
- No empty template section. All 13 headings have content, even if the content is "N/A, this feature has no UI".
- No vague requirement. "The system should handle errors gracefully" is not a requirement. "A request without a visitor session cookie returns 401, sets no cookie, and shows the standard Persian refusal line" is.
- If the spec uses ids, no requirement is missing one. `REQ-NNN` functional, `SEC-NNN` security, `US-NNN` user story, `SC-NNN` success criterion, all padded to three digits. Ids are optional on a small spec. Half-applied ids are worse than none.

## 2. Internal consistency

- Section 13 names the gate evidence: the PRD, the research doc, or one line naming the existing pattern this reuses.
- Section 4 (flow) and section 5 (behavior) describe the same thing. A step in one that is missing from the other means one of them is stale.
- Section 6 (API) and section 7 (data model) support the requirements. If a requirement says the export is a CSV download, the endpoint and the storage design have to allow it.
- Every in-scope item in section 2 has at least one requirement that implements it.
- Every out-of-scope item is phrased as an exclusion ("this phase does not support X"), not as a missing capability.
- No requirement contradicts an out-of-scope item.
- Every acceptance criterion in section 12 validates at least one requirement, and every requirement that matters is covered by one.
- Section 9 (security) carries whatever the research found, even when the answer is "no new trust boundary".
- The header table's Status and Domain match the row you are about to add to `docs/features/INDEX.md`.

## 3. Repo rules the spec cannot spec its way around

These come from `docs/engineering/ENGINEERING_CONSTITUTION.md` and the topic standards. A spec that breaks one gets rejected at review, so catch it here.

- **Independent authorization.** Every endpoint in section 6 authenticates and authorizes on its own. None of them infers authorization from possession of a resource id. Section 3 names the real mechanism (`verify_admin`, the chat trio, one of the three leads doors), not "the user must be logged in".
- **Pagination.** Any endpoint returning an unbounded collection defines its paging. No route loads a whole table into memory.
- **One authoritative implementation.** A business rule in the spec is not duplicated across two route handlers. If it looks like it has to be, that is a sign the rule belongs in a service.
- **Migrations land twice.** A schema change names both the new numbered file in `migrations/` and the mirror edit in `init_db()` in `app/db/connection.py`. It never edits an applied migration, because `scripts/apply_migrations.py` checksums them and aborts the deploy.
- **Reader/writer pairs close together.** Anything the spec writes has a named reader, and anything it reads has a named writer. A capability not wired to a real production caller is incomplete (constitution rule 5).
- **Kiosk state.** The spec says explicitly what, if anything, the next person at the same booth browser inherits. "Nothing" is a fine answer. Silence is not.
- **Module placement.** The feature is an optional module unless every customer needs it, and the spec says what an install without the module sees.

## 4. Product and UX check

From `CLAUDE.md` and `docs/engineering/UI_UX.md`. Skip only if nothing in the spec is user-facing.

- Section 10 lists all ten states the template asks for: default, loading, success, empty, error, disabled, permission denied, long content / overflow, mobile / narrow viewport, accessibility.
- No jargon in any visitor-facing string. No English technical term on a Persian screen. No error code shown to a visitor.
- Every screen in the flow is understandable in about 3 seconds, and every action takes 3 clicks or fewer.
- The layout is RTL and the copy is Persian. Long Persian text and a narrow phone are both covered.
- Keyboard operation, focus visibility and accessible labels are answered, not assumed. `UI_UX.md` lists them in what the review must check.
- A destructive action has a confirmation and a way back.
- Nothing in the flow requires the visitor to read an explanation first. If it does, the design is wrong, not the wording.

## 5. Scope check

- The spec is deliverable as a realistic set of pull requests, each scoped to one root cause. If it clearly spans two independent areas with separate lifecycles, split it now.
- A large spec groups its requirements into separately mergeable pieces, the way `docs/features/exhibition-lead-capture/SPEC.md` does.
- The out-of-scope list captures the exclusions explicitly.
- Open questions: ideally none left. Any that remain are named as risks and accepted deliberately, and the reviewer has to accept them too.

## 6. Ambiguity check

- Every requirement passes the two-interpretation test. Read it again as an implementer who has not seen the research. If it could mean two things, rewrite it so it can only mean one.
- Every acceptance criterion is binary. "The export finishes within 30 seconds for a 10,000-row dataset" is binary. "Exports are reasonably fast" is not.
- Every acceptance criterion is something a test could check. If you cannot picture the pytest that fails when it regresses, it is not a criterion yet.
- Section 11 names a real rollout: which existing callers depend on today's behaviour, what breaks, and whether anything needs a migration or a re-run after deploy.

## After the review

If you found and fixed something, re-scan that one section before committing. Do not re-run the whole checklist, just confirm the fix did not introduce a new inconsistency.

If you found nothing, go to step 7 in the skill: add the row to the Active Features table in `docs/features/INDEX.md` in the same commit, then open the pull request.
