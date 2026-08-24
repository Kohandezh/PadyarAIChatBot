"""Interactively create or update an admin account.

Run from the project root:  python scripts/change-admin.py

Works on the configured backend: PostgreSQL in production, SQLite for the
test suite / rollback (`--db`-style file overrides live in
reset-admin-password.py).

Passwords are hashed with SHA-256 + a per-account salt, matching how the app
seeds admins and how the login endpoint verifies them. The security answer is
hashed with SHA-256, matching how the login and "change security question"
endpoints verify it. No credentials are hardcoded — everything is prompted.
"""

import os
import sys
import sqlite3
import hashlib
import secrets
from getpass import getpass

# Make the `app` package importable no matter where this is launched from.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.config import DB_PATH

DEFAULT_QUESTION = "What is your favorite color?"


def prompt_nonempty(label):
    while True:
        value = input(label).strip()
        if value:
            return value
        print("  ! Cannot be empty. Try again.")


def prompt_password():
    while True:
        pwd = getpass("New password: ")
        if not pwd:
            print("  ! Cannot be empty. Try again.")
            continue
        if getpass("Confirm password: ") != pwd:
            print("  ! Passwords do not match. Try again.")
            continue
        return pwd


def main():
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        where = "PostgreSQL"
    else:
        if not os.path.exists(DB_PATH):
            print(f"Database not found at {DB_PATH}. Start the app once to create it.")
            sys.exit(1)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        where = f"SQLite at {DB_PATH}"

    cur = conn.cursor()
    print(f"Database : {where}")

    existing = [r["username"] for r in cur.execute("SELECT username FROM admins").fetchall()]
    if existing:
        print("Existing admins: " + ", ".join(existing))
    else:
        print("No admins yet — this will create the first one.")

    username = prompt_nonempty("\nUsername: ")
    password = prompt_password()

    question = input(f"Security question [{DEFAULT_QUESTION}]: ").strip() or DEFAULT_QUESTION
    answer = prompt_nonempty("Security answer: ")

    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((salt + password).encode()).hexdigest()  # matches app seeding
    ans_hash = hashlib.sha256(answer.encode()).hexdigest()             # matches login verification

    # Upsert: username is the PRIMARY KEY, so ON CONFLICT updates in place.
    cur.execute(
        """
        INSERT INTO admins (username, password_hash, salt, security_question, security_answer_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            password_hash = excluded.password_hash,
            salt = excluded.salt,
            security_question = excluded.security_question,
            security_answer_hash = excluded.security_answer_hash
        """,
        (username, pwd_hash, salt, question, ans_hash),
    )
    conn.commit()
    conn.close()

    action = "Updated" if username in existing else "Created"
    print(f"\n✓ {action} admin '{username}' (password hashed with SHA-256 + salt).")


if __name__ == "__main__":
    main()
