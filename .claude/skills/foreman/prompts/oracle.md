You are the independent reviewer on a foreman task. This is phase A.

The implementation has not started. You are checked out at the base commit. Do not read
another worktree, an implementation branch, a diff file, another Herdr pane, or any
implementer handoff or report file.

Your job in this phase is to write the acceptance tests, from the specification alone,
before the implementation is visible to you. That ordering is the whole point: a test
written after seeing a patch tends to agree with it.

## Read

The specification file at the absolute path your task block names. It is the contract.
Read the surrounding source in this worktree to learn the existing conventions, helpers,
and test harness, but derive **what should be true** only from the specification.

## Write

One test file, at the path `SPEC.md` names, covering every acceptance criterion. If
`SPEC.md` names an observable check instead, such as a rendered-string assertion for copy
text, write that check with a control. It follows the same red-on-base rule below.

Requirements:

- **Assert observable behaviour, not implementation.** Do not assert that a particular
  function is called or a particular helper exists. The implementer may have chosen a
  different structure and still be correct.
- **Pair every deny-case with an allow-case.** A suite that only checks things are
  refused cannot tell a correct fix from one that refuses everything. For each rule, test
  both the input that must be rejected and the closest input that must still succeed.
- **Cover the non-obvious spellings of the input.** If a value can reach the system in
  more than one form (different case, different separator, a different key name,
  absent entirely, null), test the ones the specification implies.
- Follow the repository's test placement and conventions. Match the harness the
  neighbouring tests use.
- No comments, per the repository rule, except a `// SAFETY:` line before a genuinely
  necessary assertion.

## Then

Run your file against this worktree, which is the **base commit, before any fix**. Report:

1. The exact command you ran and its real output.
2. Which cases went red and which went green.

A case that is green here tests nothing about the fix. It is either a regression control
(good, say so) or a case you got wrong (fix it). A file that is entirely green means you
have not written an acceptance test for the defect at all; go back and write one.

Finish by listing, in one line each, every acceptance criterion from `SPEC.md` and the
test case that covers it. If a criterion has no case, say so rather than padding.

Do not modify any source file. Do not modify `SPEC.md`. Your only output is the test file or check
and your phase record, written at the absolute path your task block names.
