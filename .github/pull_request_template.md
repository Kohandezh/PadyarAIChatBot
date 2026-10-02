## Root cause
<!--
What was broken or missing, and WHY. The cause, not only the change.
One PR = one root cause (see .claude/skills/scoped-pr/SKILL.md).
چه چیزی خراب بود یا کم بود، و چرا. علت، نه فقط تغییر.
-->

## Design doc
<!--
Give the path of a doc under docs/, for example
docs/features/<slug>/SPEC.md or an ADR in docs/engineering/DECISIONS.md.
The file must exist in this branch.
OR write one line:
No design needed: <reason, at least 15 characters>
مسیر یک سند زیر docs/ بدهید، یا یک خط «No design needed: <دلیل>» بنویسید.
-->

## Tests
<!--
Which tests prove the change. Name the files and test functions.
Say which test fails without the fix. Say what you ran, and what you did NOT run.
کدام تست‌ها تغییر را ثابت می‌کنند، و چه چیزی اجرا نشد.
-->

## Security
<!--
New endpoint or door? Who can call it (verify_admin, chat token, a leads door)?
Denied path tested? Secrets, PII, injection, the kiosk question.
"No security impact: <reason>" is a valid answer.
اثر امنیتی. اگر ندارد، با دلیل بگویید.
-->

## AI assistance
<!--
Which parts an AI agent wrote (files, functions, docs).
What the agent itself verified (commands it ran, with the result).
Do not write that a human reviewed anything here.
کدام بخش را عامل AI نوشت و خود عامل چه چیزی را بررسی کرد.
-->

## Human review
<!--
ONLY the human reviewer fills this section, at review time.
An AI agent leaves it EMPTY: it does not tick a box or write a name here.
فقط بازبین انسانی این بخش را پر می‌کند. عامل AI آن را خالی می‌گذارد.
If this section is empty (a PR body written by an agent), the reviewer copies
the checklist below from .github/pull_request_template.md.
Process: docs/engineering/REVIEW_PROCESS.md
Pre-release checklist: docs/engineering/HUMAN_REVIEW_CHECKLIST.md
-->
- [ ] I read the full diff, every file.
- [ ] I ran or reproduced the test the PR claims.
- [ ] The design doc matches what the code does.
- [ ] The Security section is true for this diff.
- [ ] The AI assistance section is honest.
- [ ] If this PR changes `scripts/check_pr_governance.py` or `.github/workflows/`, I read those changes line by line.

Reviewer account and date:
