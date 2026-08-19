---
name: implement
description: Full Research → Plan → Implement → Verify workflow for features, refactors, or bugfixes. Use when asked to implement, build, refactor, or make a non-trivial change that benefits from structured phases.
disable-model-invocation: true
allowed-tools: Bash(git *) Bash(.venv/bin/python *) Bash(python3 *) Bash(pip *) Bash(cat *) Bash(ls *) Bash(find *) Bash(grep *) Bash(pytest *) Bash(curl *) Read Edit Write Grep Glob Agent
argument-hint: "[description of what to implement or refactor]"
---

# Implement: $ARGUMENTS

You are executing a structured 4-phase implementation workflow for: **$ARGUMENTS**

Complete each phase fully before moving to the next. After each phase, output a clear **phase summary** before proceeding.

---

## Current Context

- **Branch:** !`git branch --show-current`
- **Changed files:** !`git diff --name-only`
- **Recent commits:** !`git log --oneline -5`

---

## Phase 1: Research

> Goal: Understand before you act.

1. **Search the codebase** — Use Grep and Glob to find every file relevant to "$ARGUMENTS". Cast a wide net first, then narrow.
2. **Read key files** — Understand existing patterns, imports, naming conventions, and architecture. Follow the code's style.
3. **Check dependencies** — Look at imports, `app/config.py`, and `requirements.txt` for relevant libraries. New deps go in via `pip install` plus a `requirements.txt` update.
4. **Check tests** — Find existing test files related to the change area. Understand the test patterns used.
5. **External context** — If the change depends on external APIs or libraries, use WebSearch/WebFetch to read docs.

**Exit criteria:** You can name every file that will need to change and explain why.

Output:
```
### RESEARCH SUMMARY
- **Files found:** <list>
- **Architecture understanding:** <brief>
- **Patterns to follow:** <naming, style, error handling>
- **Existing tests:** <relevant test files>
- **External dependencies:** <any>
- **Files that will need changes:** <list with reasons>
```

---

## Phase 2: Plan

> Goal: Design before you code.

Based on the research, create a concrete plan:

1. **List every file** you will create or modify — with a one-line description of what changes in each.
2. **Order of changes** — Sequence them to avoid breaking intermediate states. Dependency-safe order.
3. **Identify risks** — Shared state, migration needs, backward compatibility, edge cases.
4. **Tests to write/update** — Specify what tests are needed.

**Exit criteria:** Every file, every test, and every risk is covered.

Output:
```
### IMPLEMENTATION PLAN
| Step | File | Action | Description |
|------|------|--------|-------------|
| 1    | ...  | Create/Modify | ... |
| ...  | ...  | ...    | ... |

**Risks:** <list>
**Tests:** <what to add/update>
**Order rationale:** <why this sequence>
```

**IMPORTANT:** Do NOT start coding. Wait for user acknowledgment before proceeding to Phase 3.

---

## Phase 3: Implement

> Goal: Execute the plan precisely.

Execute the plan step by step:

1. **Follow the plan order** — Make changes in the exact sequence from Phase 2.
2. **Match existing conventions** — Use the naming, style, and patterns discovered in Phase 1. Write code that reads like the surrounding code.
3. **Small atomic edits** — Use Edit for targeted changes, Write for new files. Verify each edit is correct.
4. **Keep commits atomic** — After each logical unit of work, suggest a commit (but **only commit if the user explicitly asks**; see the `commit` skill).
5. **Handle surprises** — If you discover something unexpected, pause and inform the user before deviating from the plan.

**Exit criteria:** All code changes are complete and saved.

Output:
```
### IMPLEMENTATION SUMMARY
- **Files created:** <list>
- **Files modified:** <list>
- **Lines added/removed:** <approximate>
- **Deviation from plan:** <any, and why>
```

---

## Phase 4: Verify

> Goal: Prove it works.

Confirm the implementation is correct:

1. **Syntax check (mandatory)** — Run `python -m py_compile` on the core entry points plus every `.py` file you changed (per CLAUDE.md):
   ```bash
   python -m py_compile app/main.py
   python -m py_compile app/routers/chat.py
   # ...plus any other .py file touched
   ```
2. **Run tests (if any exist)** — There is no formal suite yet; if pytest tests are present for the change area, run them with `pytest`. Report results honestly.
3. **Manual verification** — Start the app with `python main.py` (serves at `http://127.0.0.1:8000`) and exercise the new behavior in the chat UI or admin panel. There is no linter/formatter configured — don't invent one.
4. **Regression check** — Verify nothing else broke.
5. **Fix failures** — If anything fails, fix it and re-verify. Report what was wrong and how it was fixed.

**Exit criteria:** `py_compile` passes, any tests pass, app runs, change behaves as expected.

Output:
```
### VERIFICATION RESULTS
- **py_compile:** <pass/fail, which files>
- **Tests:** <pass/fail, which ones — or "no tests for this area">
- **Manual check:** <what was verified by running python main.py>
- **Regressions:** <none found / list>
```

---

## Final Output

```
## IMPLEMENTATION COMPLETE ✓
**Task:** $ARGUMENTS
**Phase 1 (Research):** ✓ <brief>
**Phase 2 (Plan):** ✓ <brief>
**Phase 3 (Implement):** ✓ <file count> files changed
**Phase 4 (Verify):** ✓ <test results>
**Ready for:** code-review, commit, PR
```
