#!/usr/bin/env bash
# Decide whether an old commit is a safe rollback target, and list the
# migrations the old code has never seen.
#
#   deploy/rollback-plan.sh <repo-dir> <target-sha> <current-sha> <main-ref>
#
# <repo-dir>     the install's git checkout (/opt/padyar-<slug>)
# <target-sha>   the commit to go back to: 7 to 40 hex characters
# <current-sha>  the commit running now (the checkout's HEAD)
# <main-ref>     main as just fetched (padyar-deploy passes
#                refs/remotes/deploy/main)
#
# Needs no root and no network. It only reads git objects: it never changes
# the checkout, the index or any ref. padyar-deploy.sh runs it in
# `--rollback` mode before it touches anything.
#
# THE RULES (exit 1 with a message on stderr when one is broken)
# ---------
#   - the target is a commit that exists in <repo-dir>;
#   - the target is on main (an ancestor of <main-ref>), so it was reviewed
#     and merged, not a commit from a side branch;
#   - the target is not the current commit;
#   - the target is an ancestor of the current commit. A rollback goes back,
#     never forward and never sideways.
#
# OUTPUT (stable: padyar-deploy.sh parses it)
# ------
# On success the exit code is 0 and stdout has one line per file under
# migrations/ that exists at <current-sha> but not at <target-sha>, in git's
# name order (byte order, so 0030 comes before 0031):
#
#   migrations/0030_ingest.sql additive
#   migrations/0031_drop_old.sql destructive
#
# The second field is `destructive` when the file at <current-sha> contains
# DROP, TRUNCATE, DELETE FROM or RENAME as a whole word, in any case (this
# covers `ALTER TABLE ... DROP` and `ALTER TABLE ... RENAME`). Otherwise it
# is `additive`. The check is a word match, not a SQL parser. A keyword
# inside a comment also counts, so it can flag a safe file, never miss a
# drop. No line means the database has nothing the old code has not seen.
# Nothing else is ever printed on stdout; messages go to stderr.
#
# WHY THE MIGRATIONS MATTER
# -------------------------
# A rollback moves the CODE only. The database keeps every migration that is
# already applied, and the old code can neither apply nor undo a newer one.
# A newer additive migration is harmless to old code. A newer destructive one
# means the old code may look for a column or table that is gone, and data
# the newer code wrote may be lost. The way back then is the pre-deploy dump
# (admin panel, Infrastructure > Backups). docs/engineering/DATABASE.md,
# "Destructive migrations: expand, then contract", is the rule that keeps
# this list harmless.
set -euo pipefail

die() { printf 'rollback-plan: %s\n' "$*" >&2; exit 1; }

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <repo-dir> <target-sha> <current-sha> <main-ref>" >&2
  exit 1
fi
REPO="$1"
TARGET_ARG="$2"
CURRENT_ARG="$3"
MAIN_REF="$4"

# A branch name or `HEAD~1` would also resolve, but it can point somewhere
# else by the time anyone reads the log. A rollback names one exact commit.
[[ "$TARGET_ARG" =~ ^[0-9a-f]{7,40}$ ]] || die "'$TARGET_ARG' is not a commit sha (7 to 40 hex characters)."
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "'$REPO' is not a git checkout."

commit_of() { git -C "$REPO" rev-parse --verify --quiet "$1^{commit}"; }

TARGET=$(commit_of "$TARGET_ARG") || die "'$TARGET_ARG' is not a commit in $REPO."
CURRENT=$(commit_of "$CURRENT_ARG") || die "current commit '$CURRENT_ARG' is not a commit in $REPO."
MAIN=$(commit_of "$MAIN_REF") || die "main ref '$MAIN_REF' does not name a commit in $REPO."

git -C "$REPO" merge-base --is-ancestor "$TARGET" "$MAIN" \
  || die "$TARGET is not on main ($MAIN_REF). Only a commit that main contains can be a rollback target."
[[ "$TARGET" != "$CURRENT" ]] || die "$TARGET is the current commit. There is nothing to roll back."
git -C "$REPO" merge-base --is-ancestor "$TARGET" "$CURRENT" \
  || die "$TARGET is not behind the current commit $CURRENT. A rollback only goes back, never forward or sideways."

# The keyword must stand alone: `dropoff_point` or `renamed_idx` is a name,
# not a statement. Newlines become spaces first, so `DELETE` and `FROM` on
# two lines still match.
W='[^[:alnum:]_]'
DESTRUCTIVE="(^|$W)(drop|truncate|rename)($W|\$)|(^|$W)delete[[:space:]]+from($W|\$)"

# Read into variables, not through pipes into the loop: a failed git call
# inside `<(...)` or a pipe would be silent, and an empty list reads as "safe".
# The grep reads a here-string for the same reason: `git show | grep -q` can
# end in SIGPIPE under pipefail and turn a drop into "additive".
CURRENT_FILES=$(git -C "$REPO" ls-tree -r --name-only "$CURRENT" -- migrations/)
while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  git -C "$REPO" cat-file -e "$TARGET:$file" 2>/dev/null && continue
  body=$(git -C "$REPO" show "$CURRENT:$file")
  if grep -Eiq "$DESTRUCTIVE" <<<"${body//$'\n'/ }"; then
    echo "$file destructive"
  else
    echo "$file additive"
  fi
done <<<"$CURRENT_FILES"
