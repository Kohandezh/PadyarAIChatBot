"""The rollback planner, and the deploy script's explicit rollback mode.

Before this, `padyar-deploy <slug> <port> <old-sha>` could not roll back: it
exits SUPERSEDED for any sha that is not main's tip. The only manual rollback
was `git revert` on main plus a green CI run, in the middle of an incident.
`--rollback <sha>` is the fast path, and `deploy/rollback-plan.sh` is the
part that decides whether that sha is a safe target and which migrations the
old code has never seen (docs/features/db-maturity/RESEARCH.md 5.6, item 6).

The planner is git only, so every rule is exercised for real against
throwaway repositories in `tmp_path`. Each deny case sits next to the allow
case that proves the rule, not some other rule, is what refused it.

The deploy script itself needs root and a live install, so it is never run
for real here. It is checked for syntax, for refusing bad arguments before
its root check, and for the wiring of the rollback branch.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLANNER = REPO_ROOT / "deploy" / "rollback-plan.sh"
DEPLOY = REPO_ROOT / "deploy" / "padyar-deploy.sh"

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo, *args):
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=GIT_ENV, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _commit(repo, files, message):
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _plan(repo, target, current, main):
    return subprocess.run(
        ["bash", str(PLANNER), str(repo), target, current, main],
        env=GIT_ENV, capture_output=True, text=True,
    )


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "install"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


@pytest.fixture
def linear(repo):
    """A -> B on main. A ships the first migration, B changes only code."""
    a = _commit(repo, {"app.py": "v1\n",
                       "migrations/0001_initial.sql": "CREATE TABLE t (id int);\n"}, "A")
    b = _commit(repo, {"app.py": "v2\n"}, "B")
    return repo, a, b


# ── 1a: the target must be a known commit on main, and not the current one ──


def test_one_commit_back_with_no_new_migration_is_a_valid_empty_plan(linear):
    repo, a, b = linear
    out = _plan(repo, a, b, b)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "", "no migration is newer than the target, so nothing is listed"


def test_a_short_sha_is_accepted_like_a_full_one(linear):
    repo, a, b = linear
    out = _plan(repo, a[:7], b, b)
    assert out.returncode == 0, out.stderr


def test_rolling_back_to_the_commit_already_running_is_refused(linear):
    repo, a, b = linear
    out = _plan(repo, b, b, b)
    assert out.returncode != 0
    assert "current" in out.stderr.lower()


def test_rolling_back_to_a_short_form_of_the_current_commit_is_refused(linear):
    repo, a, b = linear
    out = _plan(repo, b[:8], b, b)
    assert out.returncode != 0


@pytest.mark.parametrize("bad", ["main", "HEAD~1", "not-a-sha", "zzzzzzz", "--help", ""])
def test_a_target_that_is_not_a_hex_sha_is_refused(linear, bad):
    repo, a, b = linear
    out = _plan(repo, bad, b, b)
    assert out.returncode != 0


def test_an_unknown_sha_is_refused(linear):
    repo, a, b = linear
    out = _plan(repo, "deadbeefdeadbeef", b, b)
    assert out.returncode != 0
    assert "deadbeefdeadbeef" in out.stderr


def test_a_sha_that_names_a_file_not_a_commit_is_refused(linear):
    repo, a, b = linear
    blob = _git(repo, "rev-parse", f"{b}:app.py")
    out = _plan(repo, blob, b, b)
    assert out.returncode != 0


def test_a_target_that_is_not_on_main_is_refused(repo):
    """Isolates the main rule: the target IS an ancestor of the running
    commit (a side branch was deployed), but main never contained it."""
    a = _commit(repo, {"app.py": "v1\n"}, "A")
    b = _commit(repo, {"app.py": "v2\n"}, "B")
    _git(repo, "checkout", "-q", "-b", "side", a)
    s1 = _commit(repo, {"side.py": "1\n"}, "S1")
    s2 = _commit(repo, {"side.py": "2\n"}, "S2")

    refused = _plan(repo, s1, s2, b)
    assert refused.returncode != 0
    assert "main" in refused.stderr.lower()

    allowed = _plan(repo, a, s2, b)
    assert allowed.returncode == 0, allowed.stderr


def test_a_main_ref_that_does_not_exist_is_refused(linear):
    repo, a, b = linear
    out = _plan(repo, a, b, "refs/remotes/deploy/main")
    assert out.returncode != 0


def test_a_directory_that_is_not_a_git_checkout_is_refused(tmp_path, linear):
    repo, a, b = linear
    out = _plan(tmp_path / "nowhere", a, b, b)
    assert out.returncode != 0


@pytest.mark.parametrize("argc", [0, 1, 2, 3, 5])
def test_the_wrong_number_of_arguments_is_refused(linear, argc):
    repo, a, b = linear
    args = [str(repo), a, b, b, "extra"][:argc]
    out = subprocess.run(["bash", str(PLANNER), *args],
                         env=GIT_ENV, capture_output=True, text=True)
    assert out.returncode != 0
    assert "usage" in out.stderr.lower()


# ── 1b: rollback goes back, never forward and never sideways ─────────────


def test_a_target_ahead_of_the_running_commit_is_refused(linear):
    repo, a, b = linear
    refused = _plan(repo, b, a, b)
    assert refused.returncode != 0
    allowed = _plan(repo, a, b, b)
    assert allowed.returncode == 0, allowed.stderr


def test_a_target_on_a_side_branch_of_the_running_commit_is_refused(repo):
    """S was merged into main, so it IS on main. It is still not behind the
    running commit B, so going there would be a sideways move."""
    a = _commit(repo, {"app.py": "v1\n"}, "A")
    b = _commit(repo, {"app.py": "v2\n"}, "B")
    _git(repo, "checkout", "-q", "-b", "side", a)
    s = _commit(repo, {"side.py": "1\n"}, "S")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "M", "side")
    m = _git(repo, "rev-parse", "HEAD")

    refused = _plan(repo, s, b, m)
    assert refused.returncode != 0

    allowed = _plan(repo, a, b, m)
    assert allowed.returncode == 0, allowed.stderr


# ── 1c: the migrations the old code has never seen ───────────────────────


def test_each_newer_migration_is_listed_once_with_its_verdict(repo):
    a = _commit(repo, {"migrations/0001_initial.sql": "CREATE TABLE t (id int);\n"}, "A")
    _commit(repo, {"migrations/0002_add.sql": "CREATE TABLE u (id int);\n"}, "B")
    _commit(repo, {"migrations/0003_drop_col.sql": "ALTER TABLE t DROP COLUMN old;\n"}, "C")
    d = _commit(repo, {"migrations/0004_drop_tbl.sql": "drop table u;\n"}, "D")

    out = _plan(repo, a, d, d)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "migrations/0002_add.sql additive",
        "migrations/0003_drop_col.sql destructive",
        "migrations/0004_drop_tbl.sql destructive",
    ], "0001 exists at the target, so the old code already knows it"


def test_only_migrations_newer_than_the_target_are_listed(repo):
    _commit(repo, {"migrations/0001_initial.sql": "CREATE TABLE t (id int);\n"}, "A")
    b = _commit(repo, {"migrations/0002_drop.sql": "DROP TABLE x;\n"}, "B")
    c = _commit(repo, {"migrations/0003_add.sql": "CREATE TABLE y (id int);\n"}, "C")

    out = _plan(repo, b, c, c)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["migrations/0003_add.sql additive"]


@pytest.mark.parametrize("sql", [
    "ALTER TABLE t DROP COLUMN c;",
    "drop table t;",
    "ALTER TABLE t DROP CONSTRAINT ck;",
    "TRUNCATE t;",
    "truncate table t;",
    "DELETE FROM app.settings WHERE key = 'x';",
    "delete\n  from t where 1 = 1;",
    "ALTER TABLE t RENAME COLUMN a TO b;",
    "alter table t rename to u;",
])
def test_a_destructive_statement_is_flagged_in_any_case(repo, sql):
    a = _commit(repo, {"app.py": "v1\n"}, "A")
    b = _commit(repo, {"migrations/0002_x.sql": sql + "\n"}, "B")
    out = _plan(repo, a, b, b)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["migrations/0002_x.sql destructive"]


@pytest.mark.parametrize("sql", [
    "CREATE TABLE t (id int);",
    "ALTER TABLE t ADD COLUMN IF NOT EXISTS dropoff_point text;",
    "CREATE INDEX renamed_idx ON t (id);",
    "INSERT INTO app.settings (key, value) VALUES ('deleted_from_ui', '0');",
])
def test_a_word_that_only_contains_a_keyword_is_not_flagged(repo, sql):
    a = _commit(repo, {"app.py": "v1\n"}, "A")
    b = _commit(repo, {"migrations/0002_x.sql": sql + "\n"}, "B")
    out = _plan(repo, a, b, b)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["migrations/0002_x.sql additive"]


def test_the_planner_changes_nothing_in_the_checkout(linear):
    repo, a, b = linear
    (repo / "dirty.txt").write_text("operator scratch\n")
    before = (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"))
    _plan(repo, a, b, b)
    after = (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"))
    assert before == after


# ── The deploy script: syntax, argument checks, rollback wiring ──────────


@pytest.mark.parametrize("script", [DEPLOY, PLANNER])
def test_the_scripts_parse(script):
    out = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def _deploy(*args):
    return subprocess.run(["bash", str(DEPLOY), *args],
                          capture_output=True, text=True, timeout=30)


needs_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="the allow-control relies on the script's own root check",
)


@needs_non_root
@pytest.mark.parametrize("args", [
    (),
    ("Bad_Slug", "8010", "abcdef1"),
    ("myevent", "notaport", "abcdef1"),
    ("myevent", "8010", "not-a-sha"),
    ("myevent", "8010", "--rollback"),
    ("myevent", "8010", "--rollback", "main"),
    ("myevent", "8010", "--rollback", "abcdef1", "extra"),
    ("myevent", "8010", "--rollbak", "abcdef1"),
])
def test_bad_arguments_are_refused_before_the_root_check(args):
    out = _deploy(*args)
    assert out.returncode == 1
    assert "Run with sudo" not in out.stderr, "argument checks must run first"


@needs_non_root
@pytest.mark.parametrize("args", [
    ("myevent", "8010", "abcdef1"),
    ("myevent", "8010", "--rollback", "abcdef1"),
    ("myevent", "8010", "--rollback", "abcdef1234567890abcdef1234567890abcdef12"),
])
def test_good_arguments_get_as_far_as_the_root_check_and_no_further(args):
    out = _deploy(*args)
    assert out.returncode == 1
    assert "Run with sudo" in out.stderr


def _deploy_text():
    return DEPLOY.read_text()


def test_the_rollback_branch_runs_the_planner_and_checks_the_confirmation():
    text = _deploy_text()
    assert "--rollback" in text
    assert "rollback-plan.sh" in text
    assert "PADYAR_ROLLBACK_CONFIRM" in text
    assert "refs/remotes/deploy/main" in text


def test_the_running_commit_is_read_only_after_the_lock_is_held():
    """A HEAD read before `flock` can belong to a deploy that is still
    running. Planning (or reverting) against it would use a stale commit."""
    lines = _deploy_text().splitlines()
    flock = next(i for i, l in enumerate(lines) if l.lstrip().startswith("if ! flock"))
    reads = [i for i, l in enumerate(lines) if l.startswith("CURRENT_SHA=")]
    assert reads, "CURRENT_SHA is still read somewhere"
    assert all(i > flock for i in reads)


def test_the_health_rollback_log_no_longer_claims_migrations_are_additive():
    text = _deploy_text()
    assert "they are additive" not in text
    assert "The workflow asked for this exact sha" not in text
