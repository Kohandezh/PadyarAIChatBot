"""The PR governance check: scripts/check_pr_governance.py and its wiring.

Spec: docs/features/review-governance/SPEC.md (REQ-001 to REQ-017, SC-001 to
SC-013). Why the check is advisory and not required: ADR-022.

The script is imported by path, the same way the workflow runs it, because
scripts/ is not a package.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_pr_governance.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-governance.yml"
TEMPLATE = REPO_ROOT / ".github" / "pull_request_template.md"
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"

_spec = importlib.util.spec_from_file_location("check_pr_governance", SCRIPT)
gov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gov)

IN_SCOPE = ["app/routers/chat.py"]
REQUIRED = ("Root cause", "Design doc", "Tests", "Security", "AI assistance")


@pytest.fixture
def repo(tmp_path):
    """A throwaway repo root holding one real design doc."""
    doc = tmp_path / "docs" / "features" / "demo" / "SPEC.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# demo\n", encoding="utf-8")
    return tmp_path


def body(design="See docs/features/demo/SPEC.md", **overrides):
    """A complete body. Pass a section name (spaces as _) = None to drop it."""
    texts = {
        "Root cause": "The token was minted per render.",
        "Design doc": design,
        "Tests": "tests/test_x.py::test_y fails on main, passes here.",
        "Security": "No new endpoint. Denied path tested.",
        "AI assistance": "An agent wrote the fix and ran the new test.",
        "Human review": "",
    }
    for key, value in overrides.items():
        texts[key.replace("_", " ")] = value
    parts = []
    for name, text in texts.items():
        if text is None:
            continue
        parts.append(f"## {name}\n<!-- guidance -->\n{text}\n")
    return "\n".join(parts)


# --- scope (REQ-001) --------------------------------------------------------

@pytest.mark.parametrize("text", ["", None, "anything at all"])
def test_out_of_scope_files_pass_with_any_body(repo, text):
    changed = ["docs/x.md", "tests/test_a.py", "README.md"]
    assert gov.check(text, changed, str(repo)) == []


def test_complete_body_passes_for_app_change(repo):
    assert gov.check(body(), IN_SCOPE, str(repo)) == []


def test_migration_alone_is_in_scope(repo):
    assert gov.check("", ["migrations/0030_x.sql"], str(repo)) != []


def test_similar_prefix_is_not_in_scope(repo):
    # "application/" and "docs/app/" do not start with "app/".
    assert gov.check("", ["application/x.py", "docs/app/x.md"], str(repo)) == []


# --- required sections (REQ-002, REQ-005) -----------------------------------

@pytest.mark.parametrize("name", REQUIRED)
def test_missing_heading_is_named(repo, name):
    problems = gov.check(body(**{name.replace(" ", "_"): None}), IN_SCOPE, str(repo))
    assert len(problems) == 1
    assert name in problems[0]


@pytest.mark.parametrize("name", REQUIRED)
@pytest.mark.parametrize("empty", [
    "",
    "   \n\t\n",
    "<!-- only a comment -->",
    "<!--\nmulti\nline\n-->\n  ",
])
def test_heading_with_only_comments_or_blanks_is_empty(repo, name, empty):
    problems = gov.check(body(**{name.replace(" ", "_"): empty}), IN_SCOPE, str(repo))
    assert len(problems) == 1
    assert name in problems[0]


@pytest.mark.parametrize("text", ["", None])
def test_empty_body_names_all_five_sections(repo, text):
    problems = gov.check(text, IN_SCOPE, str(repo))
    for name in REQUIRED:
        assert any(name in p for p in problems), name


# --- Human review is never required (REQ-009, SEC-004) ----------------------

def test_human_review_absent_is_fine(repo):
    assert gov.check(body(Human_review=None), IN_SCOPE, str(repo)) == []


def test_human_review_empty_is_fine(repo):
    assert gov.check(body(Human_review="<!-- reviewer fills this -->"),
                     IN_SCOPE, str(repo)) == []


def test_human_review_is_not_in_required_sections():
    assert "Human review" not in gov.REQUIRED_SECTIONS
    assert gov.REQUIRED_SECTIONS == REQUIRED
    assert gov.IN_SCOPE_PREFIXES == ("app/", "migrations/")


# --- headings (REQ-003, REQ-004) --------------------------------------------

def test_headings_match_case_and_trailing_space(repo):
    text = body().replace("## Root cause", "## root CAUSE  ")
    assert gov.check(text, IN_SCOPE, str(repo)) == []


def test_crlf_body_is_parsed(repo):
    assert gov.check(body().replace("\n", "\r\n"), IN_SCOPE, str(repo)) == []


def test_duplicate_heading_uses_first(repo):
    # The first "## Tests" is empty; a later filled one does not rescue it.
    text = body(Tests="") + "\n## Tests\nfilled later\n"
    problems = gov.check(text, IN_SCOPE, str(repo))
    assert len(problems) == 1 and "Tests" in problems[0]


def test_heading_inside_comment_does_not_count(repo):
    text = body(Security=None) + "\n<!--\n## Security\nhidden\n-->\n"
    problems = gov.check(text, IN_SCOPE, str(repo))
    assert len(problems) == 1 and "Security" in problems[0]


def test_h3_is_content_not_a_heading(repo):
    assert gov.check(body(Tests="### Unit\n- tests/test_x.py"), IN_SCOPE, str(repo)) == []


# --- design doc (REQ-006 to REQ-008) ----------------------------------------

@pytest.mark.parametrize("design", [
    "docs/features/demo/SPEC.md",
    "See `docs/features/demo/SPEC.md`.",
    "[spec](docs/features/demo/SPEC.md)",
    "./docs/features/demo/SPEC.md, section 5",
])
def test_existing_doc_path_passes(repo, design):
    assert gov.check(body(design=design), IN_SCOPE, str(repo)) == []


def test_missing_doc_path_is_a_problem(repo):
    problems = gov.check(body(design="docs/features/nope/SPEC.md"), IN_SCOPE, str(repo))
    assert len(problems) == 1
    assert "Design doc" in problems[0] and "nope" in problems[0]


@pytest.mark.parametrize("design", [
    "docs/../docs/features/demo/SPEC.md",
    "../docs/features/demo/SPEC.md",
    "docs/features/demo/../demo/SPEC.md",
])
def test_dotdot_path_is_a_problem(repo, design):
    problems = gov.check(body(design=design), IN_SCOPE, str(repo))
    assert problems and all("Design doc" in p for p in problems)


def test_absolute_or_url_path_is_not_a_repo_doc(repo):
    for design in ("/tmp/docs/features/demo/SPEC.md",
                   "https://example.com/blob/main/docs/features/demo/SPEC.md"):
        problems = gov.check(body(design=design), IN_SCOPE, str(repo))
        assert len(problems) == 1 and "Design doc" in problems[0]


def test_directory_is_not_a_doc(repo):
    (repo / "docs" / "dir.md").mkdir()
    problems = gov.check(body(design="docs/dir.md"), IN_SCOPE, str(repo))
    assert len(problems) == 1


def test_symlink_out_of_repo_is_a_problem(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.md"
    outside.write_text("x", encoding="utf-8")
    (repo / "docs" / "link.md").symlink_to(outside)
    problems = gov.check(body(design="docs/link.md"), IN_SCOPE, str(repo))
    assert len(problems) == 1


def test_any_missing_path_fails_even_next_to_a_real_one(repo):
    design = "docs/features/demo/SPEC.md and docs/features/gone/SPEC.md"
    problems = gov.check(body(design=design), IN_SCOPE, str(repo))
    assert len(problems) == 1 and "gone" in problems[0]


@pytest.mark.parametrize("design", [
    "No design needed: one-line typo fix in a log message",
    "no design needed: one-line typo fix in a log message",
    "- NO DESIGN NEEDED:   renames a private helper only   ",
    "No design needed: " + "x" * 15,
])
def test_no_design_needed_with_long_reason_passes(repo, design):
    assert gov.check(body(design=design), IN_SCOPE, str(repo)) == []


@pytest.mark.parametrize("design", [
    "No design needed: typo",
    "No design needed: " + "x" * 14,
    "No design needed:",
])
def test_no_design_needed_with_short_reason_is_a_problem(repo, design):
    problems = gov.check(body(design=design), IN_SCOPE, str(repo))
    assert len(problems) == 1 and "15" in problems[0]


def test_design_doc_without_path_or_reason_is_a_problem(repo):
    problems = gov.check(body(design="we talked about it"), IN_SCOPE, str(repo))
    assert len(problems) == 1 and "Design doc" in problems[0]


# --- CLI (exit codes 0, 1, 2) ------------------------------------------------

def run_cli(tmp_path, repo, text, changed):
    body_file = tmp_path / "body.md"
    body_file.write_text(text, encoding="utf-8")
    changed_file = tmp_path / "changed.txt"
    changed_file.write_text("\n".join(changed) + "\n\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--body-file", str(body_file),
         "--changed-files", str(changed_file), "--repo-root", str(repo)],
        capture_output=True, text=True, timeout=30)


def test_cli_pass_exits_zero(tmp_path, repo):
    result = run_cli(tmp_path, repo, body(), IN_SCOPE)
    assert result.returncode == 0
    assert len(result.stdout.strip().splitlines()) == 1


def test_cli_problems_exit_one_one_line_each(tmp_path, repo):
    result = run_cli(tmp_path, repo, "", IN_SCOPE)
    assert result.returncode == 1
    lines = result.stdout.strip().splitlines()
    assert len(lines) == len(REQUIRED)


def test_cli_out_of_scope_exits_zero(tmp_path, repo):
    assert run_cli(tmp_path, repo, "", ["docs/x.md"]).returncode == 0


def test_cli_missing_input_file_exits_two(tmp_path, repo):
    changed = tmp_path / "changed.txt"
    changed.write_text("app/x.py\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--body-file", str(tmp_path / "missing.md"),
         "--changed-files", str(changed)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 2

    body_file = tmp_path / "body.md"
    body_file.write_text(body(), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--body-file", str(body_file),
         "--changed-files", str(tmp_path / "missing.txt")],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 2


def test_cli_does_not_execute_body(tmp_path, repo):
    marker = tmp_path / "pwned"
    text = body(Root_cause=f"$(touch {marker}) `touch {marker}` __import__('os')")
    run_cli(tmp_path, repo, text, IN_SCOPE)
    assert not marker.exists()


# --- wiring: workflow, template, CODEOWNERS ----------------------------------

def test_workflow_uses_pull_request_never_target():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert re.search(r"^\s*pull_request:\s*$", text, re.MULTILINE)
    for event in ("opened", "edited", "synchronize", "reopened", "ready_for_review"):
        assert event in text, event


def test_workflow_never_interpolates_body_or_title():
    # Stricter than "not inside run:": the attacker-controlled fields do not
    # appear in the file at all. The body is read from $GITHUB_EVENT_PATH.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event.pull_request.body" not in text
    assert "github.event.pull_request.title" not in text
    assert "GITHUB_EVENT_PATH" in text


def test_workflow_is_read_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    perms = re.search(r"^permissions:\n((?:[ ]+.*\n)+)", text, re.MULTILINE)
    assert perms, "top-level permissions block missing"
    granted = [line.strip() for line in perms.group(1).splitlines() if line.strip()]
    assert granted == ["contents: read"]
    assert "permissions:" not in text.split("jobs:", 1)[1]
    assert "pr-governance:" in text
    assert "fetch-depth: 0" in text


def test_template_has_six_headings_in_order():
    text = TEMPLATE.read_text(encoding="utf-8")
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    assert headings == list(REQUIRED) + ["Human review"]


def test_template_passes_its_own_check_only_when_filled(repo):
    # The raw template is guidance in comments: an agent that submits it
    # unfilled for an app/ change gets all five problems.
    text = TEMPLATE.read_text(encoding="utf-8")
    assert len(gov.check(text, IN_SCOPE, str(repo))) == len(REQUIRED)


def test_codeowners_names_only_the_two_accounts():
    text = CODEOWNERS.read_text(encoding="utf-8")
    handles = set(re.findall(r"@[\w-]+", text))
    assert handles == {"@Kohandezh", "@sinashamsizadeh"}
    assert re.search(r"^\*\s+@Kohandezh\s+@sinashamsizadeh\s*$", text, re.MULTILINE)
