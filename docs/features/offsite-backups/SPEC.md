# SPEC: Off-site copy of verified backups

| Field | Value |
|-------|-------|
| Created | 2026-09-14 |
| Updated | 2026-09-14 |
| Status | Implemented |
| Domain | infrastructure |
| Author | تیم پادیار |
| Sources | The 2026-09-14 danesh-bonyan assessment finding «backups live on the same host»; `app/services/backup_offsite.py`; `app/services/pg_backup.py` (verify hook); `tests/test_backup_offsite.py` |

This document describes what IS shipped, not what was planned. Numbers are
quoted from the code and are the contract.

---

## 1. Scenario

**The gap.** `pg_backup` writes every dump under `backups/postgres/` — on the
same host, and usually the same disk, as the PostgreSQL it protects. One
host-level event (disk death, ransomware, a bad `rm -rf`) then destroys the
database AND every copy of it together. A backup that cannot survive the
loss of its own host is not a backup; it is a copy.

**Who triggers what, from where:**

- **The scheduler** — the nightly 03:00 run (`app/services/backup.py`) takes
  a dump, then verifies it. Verify success is the single point where a second
  copy is worth taking, and it is the single point the copy hangs from.
- **An admin** — pressing «بررسی» (verify) on an existing backup in the admin
  panel reaches the same hook. Restore's own pre-check does NOT: a restore is
  an emergency and must never sit behind a network copy or its timeout
  (`offsite=False`).
- **The operator** — sets `OFFSITE_BACKUP_TARGET` in the install's env once;
  nothing else is needed. Empty (the default) = the feature is off.

## 2. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OFFSITE_BACKUP_TARGET` | empty (off) | `rsync:user@host:/path` or `dir:/mounted/path` |
| `OFFSITE_BACKUP_TIMEOUT` | `600` | ceiling for the rsync subprocess, seconds |

Both are read in `app/config.py`. Documented with a commented placeholder in
`.env.example` and both `deploy/env/*.env.template`. rsync over ssh requires
key-based login — the copy runs unattended at 03:00.

## 3. Behavior

Two target forms, dispatched by strict prefix:

- `rsync:user@host:/srv/padyar-backups` → `rsync -a --chmod=F600 <dump>
  <target>/<backup_id>.dump`, a FIXED argv list — no shell, nothing
  interpolated except the dump path and the operator's own target string
  (the same discipline `pg_backup` applies to `pg_dump`/`pg_restore`).
- `dir:/mnt/offsite-backup` → `shutil.copyfile` onto an ALREADY-MOUNTED path,
  then `chmod 0600` and `fsync` of file and directory. The directory must
  exist: a missing mount FAILS the copy instead of quietly writing to the
  local disk under the mountpoint — which would be the exact single-host
  failure this feature exists to remove.

An unrecognized scheme (anything without `rsync:` / `dir:`) fails softly.

**Every result is recorded** in the backup's own `manifest.json`, as an
`offsite` block: `target`, `source_sha256`, `attempted_at`, `status`
(`copied` / `failed`), `exit_code`, `error`, `duration_ms`. One service
event follows: `backup.offsite.copied` (info) or `backup.offsite.failed`
(error, message states the local backup is still valid).

**Failure is non-fatal — by design.** The local backup is valid the moment
verify passes; the off-site copy is hardening, not a gate. A full remote
disk, a dead network or a wrong target must never turn a good nightly backup
into a failed job or raise into the backup pipeline: the failure is
recorded, the event is logged, and the caller gets a success-with-warning.

## 4. Wiring (the anti-scaffold contract)

| Piece | Production caller |
|---|---|
| `backup_offsite.copy_verified_dump` | `pg_backup.verify()` success path only |
| scheduler verify → offsite | `backup._run_backup_now` verifies the fresh dump (verify failure logged, never fatal) so the nightly run actually reaches the hook |
| restore pre-check | `verify(..., offsite=False)` — opted OUT by design |

`tests/test_backup_offsite.py` pins all three: remove the hook and
`test_verify_success_wires_the_offsite_copy` fails; remove the scheduler's
verify call and `test_a_scheduled_pg_backup_is_verified_so_it_reaches_the_offsite_target`
(tests/test_backup_scheduler_dispatch.py) fails. Both were watched red first.

## 5. Out of scope (recorded, not shipped)

- Admin-UI surfacing of the `offsite` block — the manifest already carries
  it and `backup.offsite.failed` is visible in the logs page; a dedicated
  panel column was deliberately not built (the assessment item was the
  second copy, not a new screen).
- Automated restore-from-off-site: a restore pulls from the local
  `backups/postgres` tree; fetching from the off-site target is a documented
  manual step (copy the dump back, place it under `backups/postgres/<id>/`).
- Encryption of the copy at rest beyond `F600`/`0600` permissions — the
  local dump has the same posture; encrypting one copy only would be
  security theater.
