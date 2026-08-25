#!/usr/bin/env python3
"""Reset ONE admin login on the CONFIGURED database — nothing else is touched.

Safe to run on the production server: it updates only the matching row in the
`admins` table. Chat logs, content, settings and sessions are left alone.

BACKEND
-------
Routes through the application's own connection, so it works on PostgreSQL
(production) as well as SQLite. It used to `import sqlite3` and open
`chat_history.db` directly, which meant that on a PostgreSQL install it either
failed outright or — worse — silently reset the password in a stray SQLite file
nobody reads, leaving the operator still locked out with no error to explain it.
Pass --db to force the SQLite path.

THE SECURITY ANSWER MATTERS
---------------------------
The login form requires username + password + SECURITY ANSWER on every login,
not only for recovery. Resetting the password alone therefore does NOT
guarantee a way back in: an operator who has lost the answer stays locked out.
--security-answer exists for exactly that case.

Usage:
    python reset-admin-password.py padyar
    python reset-admin-password.py padyar --password 'NewPass123'
    python reset-admin-password.py padyar --security-answer 'blue'
    python reset-admin-password.py padyar --db /path/to/chat_history.db
"""
import os
import sys
import sqlite3
import argparse
from getpass import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description="Reset one admin login on the configured database")
    ap.add_argument("username", help="Admin username to reset (must already exist)")
    ap.add_argument("--password", help="New password (omit to be prompted securely)")
    ap.add_argument("--security-answer", dest="security_answer",
                    help="Also set the security answer, which the login form requires")
    ap.add_argument("--db", default=None,
                    help="Force the SQLite path at this file instead of the configured backend")
    args = ap.parse_args()

    password = args.password
    if not password:
        password = getpass("New password: ")
        if password != getpass("Confirm password: "):
            sys.exit("✗ Passwords do not match.")
    if not password:
        sys.exit("✗ Password cannot be empty.")

    from app.auth.security import hash_password, hash_security_answer
    pwd_hash = hash_password(password)

    if args.db:
        if not os.path.exists(args.db):
            sys.exit(f"✗ Database not found: {args.db}")
        conn = sqlite3.connect(args.db)
        conn.execute("PRAGMA busy_timeout=5000")
        where = "SQLite at " + args.db
    else:
        from app.config import DB_BACKEND, DATABASE_URL
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        where = "PostgreSQL" if DB_BACKEND == "postgres" else "SQLite"
        if DB_BACKEND == "postgres" and "@" in DATABASE_URL:
            where += " (" + DATABASE_URL.rsplit("@", 1)[1] + ")"

    try:
        # Chained execute().rowcount: on PostgreSQL app/db/pg.py hands back a
        # Connection from cursor(), and rowcount lives on what execute()
        # RETURNS, not on the connection.
        if args.security_answer:
            cur = conn.execute(
                "UPDATE admins SET password_hash = ?, salt = ?, security_answer_hash = ?"
                " WHERE username = ?",
                (pwd_hash, "",
                 hash_security_answer(args.security_answer),
                 args.username))
        else:
            cur = conn.execute(
                "UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?",
                (pwd_hash, "", args.username))
        if cur.rowcount == 0:
            existing = [r[0] for r in conn.execute(
                "SELECT username FROM admins").fetchall()]
            sys.exit(f"✗ No admin named '{args.username}' in {where}. "
                     f"Existing admins: {', '.join(str(e) for e in existing) or '(none)'}")
        conn.commit()
    finally:
        conn.close()

    print(f"✓ Password reset for '{args.username}' in {where}.")
    if args.security_answer:
        print("  Security answer set too.")
    else:
        print("  NOTE: the login form also asks for the security answer. If you do")
        print("        not know it, re-run with --security-answer 'something'.")


if __name__ == "__main__":
    main()
