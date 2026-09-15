"""Default seed content for a fresh install: none.

This platform ships empty on purpose. Every fact a customer's assistant
knows arrives through the admin panel (dataset, questions, synonyms) or
its import scripts, so nothing here can go stale or leak one customer's
event into another's install. The seeding hooks below stay wired: they
are the mechanism a customer's own seed module plugs into, and tests
rely on them being idempotent no-ops while the lists are empty.
"""

DEFAULT_DATASET = []
DEFAULT_QUESTIONS = []
DEFAULT_SYNONYMS = []


def seed_default_content(cursor) -> None:
    """Insert DEFAULT_DATASET / DEFAULT_QUESTIONS if the tables are empty.

    Idempotent: safe on every boot. With empty lists it does nothing.
    """
    if cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0]:
        return

    # The order of DEFAULT_DATASET is the curated reading order the visitor
    # sees. Writing the position down explicitly means a fresh install gets
    # the intended order on either backend, instead of relying on the
    # insertion rowid (which does not exist on PostgreSQL).
    cursor.executemany(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(item["id"], item["title"], item["text"], item["video_url"],
          item.get("title_en", ""), item.get("text_en", ""), (i + 1) * 10)
         for i, item in enumerate(DEFAULT_DATASET)],
    )
    cursor.executemany(
        "INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, '')",
        DEFAULT_QUESTIONS,
    )


def seed_default_synonyms(cursor) -> None:
    """Insert DEFAULT_SYNONYMS if the synonyms table is empty."""
    if cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]:
        return
    cursor.executemany(
        "INSERT INTO synonyms (source, target) VALUES (?, ?)",
        DEFAULT_SYNONYMS,
    )
