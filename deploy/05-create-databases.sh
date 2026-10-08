#!/usr/bin/env bash
# Create one PostgreSQL database + least-privilege role per install, plus an
# empty "<database>_drill" database for the nightly restore drill.
#
# The migrations in migrations/ assume the `app` and `observability` schemas
# already exist (0001_initial.sql opens with `CREATE TABLE app.schema_migrations`),
# so this script creates them and hands ownership to the app role.
#
# Several installs share one cluster, so each database (and its drill database)
# accepts connections from its own role only: deploy/05-connect-isolation.sql.
#
# Passwords are generated here and printed ONCE. Copy them into the .env
# file immediately; they are not stored anywhere else.
#
#   sudo bash deploy/05-create-databases.sh <slug>
# (slug: lowercase letters, digits, hyphens — e.g. myevent)
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0 <slug>" >&2; exit 1; fi

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 "$@"; }

# Read here, by root, and handed to psql on stdin: the postgres user may not be
# allowed to read the checkout. Checked before anything is created.
ISOLATION_SQL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/05-connect-isolation.sql"
if [[ ! -r "$ISOLATION_SQL" ]]; then
  echo "Missing ${ISOLATION_SQL}; run this script from the repository checkout." >&2; exit 1
fi

INSTALLS=("$@")
if [[ ${#INSTALLS[@]} -eq 0 ]]; then
  echo "Usage: sudo bash $0 <slug>..." >&2; exit 1
fi
for slug in "${INSTALLS[@]}"; do
  if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "Bad slug '$slug' — lowercase letters, digits and hyphens only." >&2; exit 1
  fi
done

declare -A PASSWORDS

for slug in "${INSTALLS[@]}"; do
  db="padyar_${slug}"
  role="padyar_${slug}"
  pass=$(openssl rand -base64 24 | tr -d '/+=' | head -c 28)
  PASSWORDS[$slug]=$pass

  log "Creating role and database for ${slug}"

  if psql_su -tAc "SELECT 1 FROM pg_roles WHERE rolname='${role}'" | grep -q 1; then
    echo "  role ${role} exists — resetting its password"
    psql_su -c "ALTER ROLE ${role} WITH LOGIN PASSWORD '${pass}';"
  else
    psql_su -c "CREATE ROLE ${role} WITH LOGIN PASSWORD '${pass}';"
  fi

  # NOSUPERUSER/NOCREATEDB is the default for CREATE ROLE, stated here so a
  # future edit does not quietly grant more than the app needs.
  psql_su -c "ALTER ROLE ${role} NOSUPERUSER NOCREATEDB NOCREATEROLE;"

  # One stuck admin query must not hold a connection forever (.env.example).
  psql_su -c "ALTER ROLE ${role} SET statement_timeout = '30s';"
  psql_su -c "ALTER ROLE ${role} SET idle_in_transaction_session_timeout = '60s';"

  if psql_su -tAc "SELECT 1 FROM pg_database WHERE datname='${db}'" | grep -q 1; then
    echo "  database ${db} already exists — leaving its contents alone"
  else
    # Persian content is stored directly, so UTF8 is not optional.
    psql_su -c "CREATE DATABASE ${db} OWNER ${role} ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';"
  fi

  # Schemas the migrations expect, owned by the app role.
  psql_su -d "${db}" -c "CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION ${role};"
  psql_su -d "${db}" -c "CREATE SCHEMA IF NOT EXISTS observability AUTHORIZATION ${role};"
  psql_su -d "${db}" -c "REVOKE ALL ON SCHEMA public FROM PUBLIC;"
  psql_su -d "${db}" -c "ALTER DATABASE ${db} SET search_path = app, observability, public;"

  # The restore drill database: an empty copy target for the nightly drill.
  # Every night the app restores the newest backup here and checks it, to prove
  # a backup really brings a database back (app/services/restore_drill.py).
  # The app role is NOCREATEDB, so the app cannot make this database itself.
  # It must exist ahead of time, and this script is where it is made.
  # No schemas are created here. The restore creates them, and the drill drops
  # them again after each run, so this database is empty between drills.
  # Re-running this script on an existing install is safe. It only adds the
  # missing drill database. It still resets the role password (see above), so
  # copy the new DATABASE_URL into .env and restart the app.
  drill_db="${db}_drill"
  if psql_su -tAc "SELECT 1 FROM pg_database WHERE datname='${drill_db}'" | grep -q 1; then
    echo "  database ${drill_db} already exists, leaving it alone"
  else
    psql_su -c "CREATE DATABASE ${drill_db} OWNER ${role} ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';"
  fi
  psql_su -d "${drill_db}" -c "REVOKE ALL ON SCHEMA public FROM PUBLIC;"
  # Same search_path as the live database. A check that uses a table name
  # without its schema then works the same here as on the live database. If
  # this differed, such a check could pass live and fail only in the drill,
  # and that would look like a bad backup.
  psql_su -d "${drill_db}" -c "ALTER DATABASE ${drill_db} SET search_path = app, observability, public;"

  # Only this install's role may connect to its two databases. Without this,
  # PUBLIC keeps CONNECT and another install's role can open them. Safe to
  # repeat on an existing install (see the SQL file).
  for target in "${db}" "${drill_db}"; do
    psql_su -q -v db="${target}" -v role="${role}" < "${ISOLATION_SQL}"
  done
  echo "  only ${role} may connect to ${db} and ${drill_db}"
done

log "Checking the connection budget"
maxconn=$(psql_su -tAc 'SHOW max_connections;')
echo "  max_connections = ${maxconn}"
echo "  planned usage   = per install: WEB_CONCURRENCY(3) x DB_POOL_MAX_SIZE(5) = 15" \
     "(multiply by the installs hosted on this host)"
if (( maxconn < 60 )); then
  echo "  WARNING: raise max_connections, or lower WEB_CONCURRENCY/DB_POOL_MAX_SIZE." >&2
fi

echo
for slug in "${INSTALLS[@]}"; do
  cat <<BANNER

============================================================
 DATABASE CREDENTIALS — COPY THESE NOW, THEY ARE NOT STORED
============================================================
 ${slug}  DATABASE_URL=postgresql://padyar_${slug}:${PASSWORDS[$slug]}@127.0.0.1:5432/padyar_${slug}
============================================================
BANNER
done

echo
echo "Next: sudo bash deploy/10-install-app.sh ${INSTALLS[0]}"
