"""read_settings_strict on real PostgreSQL: the multi-key IN query.

The watchdog reads its two settings in ONE query, `WHERE key IN (?, ?)`, so a
database outage costs one pool wait and not two. On SQLite that query is
trivially right. Here it goes through app/db/pg.py, which rewrites each `?` to
`%s`, and through psycopg's dict rows, so the main suite cannot prove it.
"""


def test_several_keys_come_back_from_one_read(conn):
    from app.db.queries import read_settings_strict

    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                 ("alert_critical_phone", "+989121234567"))
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                 ("alert_credit_threshold_toman", "250000"))
    conn.commit()

    values = read_settings_strict(
        ["alert_critical_phone", "alert_credit_threshold_toman", "never_written"])

    assert values == {"alert_critical_phone": "+989121234567",
                      "alert_credit_threshold_toman": "250000"}


def test_get_setting_agrees_with_the_strict_read(conn):
    from app.db.queries import get_setting, read_settings_strict

    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                 ("whitelabel_app_name", "دستیار پادیار"))
    conn.commit()

    assert read_settings_strict(["whitelabel_app_name"]) == {
        "whitelabel_app_name": "دستیار پادیار"}
    assert get_setting("whitelabel_app_name", fresh=True) == "دستیار پادیار"
    assert get_setting("never_written", "default", fresh=True) == "default"
