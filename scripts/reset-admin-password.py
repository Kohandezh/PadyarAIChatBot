#!/usr/bin/env python3
"""Reset ONE admin password in a live chat_history.db — nothing else is touched.

Safe to run on the production server: it updates only the matching row in the
`admins` table (password_hash + salt). Chat logs, content, settings, sessions
are left exactly as they are.

The hash written is salted SHA-256, which is accepted by BOTH the old app and
the new app (the new app verifies legacy salted-SHA-256 and auto-upgrades it to
bcrypt on the next successful login). So this works before or after the upgrade.

Usage (run from anywhere; point --db at the live DB if not in the project root):

    python reset-admin-password.py inotex@admin
    python reset-admin-password.py inotex@admin --password 'NewPass123'
    python reset-admin-password.py inotex@admin --db /path/to/chat_history.db

With no --password it prompts (hidden input). The username must already exist —
this never creates a new admin, so a typo can't silently add an account.
"""
import os
import sys
import sqlite3
import hashlib
import secrets
import argparse
from getpass import getpass


def main():
    ap = argparse.ArgumentParser(description="Reset one admin's password in chat_history.db")
    ap.add_argument("username", help="Admin username to reset (must already exist)")
    ap.add_argument("--password", help="New password (omit to be prompted securely)")
    ap.add_argument("--db", default="chat_history.db",
                    help="Path to the live chat_history.db (default: ./chat_history.db)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"✗ Database not found: {args.db}\n"
                 "  Run this next to the live chat_history.db, or pass --db /full/path.")

    password = args.password
    if not password:
        password = getpass("New password: ")
        if password != getpass("Confirm password: "):
            sys.exit("✗ Passwords do not match.")
    if not password:
        sys.exit("✗ Password cannot be empty.")

    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((salt + password).encode()).hexdigest()

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.execute(
            "UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?",
            (pwd_hash, salt, args.username),
        )
        if cur.rowcount == 0:
            existing = [r[0] for r in conn.execute("SELECT username FROM admins").fetchall()]
            sys.exit(f"✗ No admin named '{args.username}'. Existing admins: {', '.join(existing) or '(none)'}")
        conn.commit()
    finally:
        conn.close()

    print(f"✓ Password reset for '{args.username}'. Log in, then change it from the admin panel.")


if __name__ == "__main__":
    main()
