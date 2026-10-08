-- Only the install's own role may connect to this database.
--
-- Every install has its own database (and its <database>_drill) on ONE shared
-- PostgreSQL cluster. PostgreSQL gives CONNECT on every new database to
-- PUBLIC, so without these two lines any install's role can open any other
-- install's database, and only the schema permissions stop it from reading.
-- This is the second layer (docs/features/db-maturity/RESEARCH.md 5.8,
-- ADR-028 item 7). The postgres superuser can always connect; nothing else
-- needs to: the monitoring exporter uses the `postgres` database only.
--
-- deploy/05-create-databases.sh runs this once for <database> and once for
-- <database>_drill, as the postgres superuser:
--
--   sudo -u postgres psql -v ON_ERROR_STOP=1 -v db=padyar_<slug> -v role=padyar_<slug> \
--     < deploy/05-connect-isolation.sql
--
-- For an install made before this file existed, run that same command (two
-- statements, nothing else changes). Do NOT re-run 05 for that: 05 also
-- resets the role's password. Safe to run again: revoking a privilege PUBLIC
-- no longer has, or granting one the role already holds, changes nothing.
-- :"db" and :"role" are psql variables, quoted as identifiers.

REVOKE CONNECT ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"role";
