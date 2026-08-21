#!/usr/bin/env bash
# Create one PostgreSQL database + least-privilege role per install.
#
# The migrations in migrations/ assume the `app` and `observability` schemas
# already exist (0001_initial.sql opens with `CREATE TABLE app.schema_migrations`),
# so this script creates them and hands ownership to the app role.
#
# Passwords are generated here and printed ONCE. Copy them into the two .env
# files immediately; they are not stored anywhere else.
#
#   sudo bash deploy/05-create-databases.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0" >&2; exit 1; fi

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 "$@"; }

declare -A PASSWORDS

for slug in inotex elecomp; do
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
done

log "Checking the connection budget"
maxconn=$(psql_su -tAc 'SHOW max_connections;')
echo "  max_connections = ${maxconn}"
echo "  planned usage   = 2 apps x WEB_CONCURRENCY(3) x DB_POOL_MAX_SIZE(5) = 30"
if (( maxconn < 60 )); then
  echo "  WARNING: raise max_connections, or lower WEB_CONCURRENCY/DB_POOL_MAX_SIZE." >&2
fi

cat <<BANNER

============================================================
 DATABASE CREDENTIALS — COPY THESE NOW, THEY ARE NOT STORED
============================================================
 inotex   DATABASE_URL=postgresql://padyar_inotex:${PASSWORDS[inotex]}@127.0.0.1:5432/padyar_inotex
 elecomp  DATABASE_URL=postgresql://padyar_elecomp:${PASSWORDS[elecomp]}@127.0.0.1:5432/padyar_elecomp
============================================================

Next: deploy/10-install-app.sh inotex   (then elecomp)
BANNER
