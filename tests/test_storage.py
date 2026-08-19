"""Infrastructure → Storage: the numbers must be real and the alerts must be rare.

Three properties this file exists to hold:

  * The percentages are arithmetic on `shutil.disk_usage`, not a guess, and the
    thresholds that colour them are operator-configurable within sane bounds.
  * `has_space_for` answers False when it cannot measure. The backup and VACUUM
    paths consult it before writing; "I do not know" must not read as "yes".
  * A full disk must not produce a log storm. Every alert row costs the space
    the alert is complaining about, so the alert is rate-limited to once per
    hour per state — and the test proves the second call writes nothing.
"""
import os
import shutil
import sqlite3

import pytest


class _Usage:
    """What shutil.disk_usage returns, with the numbers a test wants."""

    def __init__(self, total, used, free):
        self.total, self.used, self.free = total, used, free


@pytest.fixture(scope="module")
def schema_template(tmp_path_factory):
    """Both databases, built once and copied per test.

    `init_db()` hashes a password with a deliberately slow KDF; doing it per
    test is the difference between a fast module and a slow one. The autouse
    fixtures in conftest.py are function-scoped and not in force here, so both
    paths are redirected explicitly — no real database is ever opened.
    """
    folder = tmp_path_factory.mktemp("schema")
    with pytest.MonkeyPatch.context() as mp:
        import app.config as config
        mp.setattr(config, "DB_PATH", str(folder / "chat_history.db"))
        mp.setattr(config, "LOGS_DB_PATH", str(folder / "application_logs.db"))
        mp.setattr(config, "SEED_DEFAULT_CONTENT", False)

        from app.db.connection import init_db
        init_db()
        from app.services import applog
        applog.ensure_tables()

        for path in (config.DB_PATH, config.LOGS_DB_PATH):
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.close()
    return folder


@pytest.fixture
def st(tmp_path, monkeypatch, schema_template):
    import app.config as config
    for name in ("chat_history.db", "application_logs.db"):
        shutil.copyfile(schema_template / name, tmp_path / name)
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat_history.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "application_logs.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.services import applog
    applog._recent.clear()

    from app.services import storage
    storage.reset_alert_state()
    return storage


def _fake_disk(st, monkeypatch, total, used, free):
    monkeypatch.setattr(st, "_disk_usage", lambda _path: _Usage(total, used, free))


def _storage_log_rows():
    from app.services import applog
    rows, _total = applog.query(tables=["app_logs"], limit=500)
    return [r for r in rows if str(r["event_name"]).startswith("storage.disk.")]


# ── Percent maths ───────────────────────────────────────────────────────

@pytest.mark.parametrize("total,used,expected", [
    (1000, 0, 0.0),
    (1000, 250, 25.0),
    (1000, 500, 50.0),
    (1000, 999, 99.9),
    (1000, 1000, 100.0),
    (3, 1, 33.3),
])
def test_percent_used_is_arithmetic_not_a_guess(st, monkeypatch, total, used, expected):
    _fake_disk(st, monkeypatch, total, used, total - used)
    assert st.disk()["percent_used"] == expected


def test_a_zero_sized_volume_does_not_divide_by_zero(st, monkeypatch):
    _fake_disk(st, monkeypatch, 0, 0, 0)
    assert st.disk()["percent_used"] == 0.0


def test_an_unreadable_volume_reports_unknown_rather_than_zero_percent(st, monkeypatch):
    def boom(_path):
        raise OSError("volume went away")

    monkeypatch.setattr(st, "_disk_usage", boom)
    info = st.disk()
    assert info["state"] == "unknown"
    assert info["total_bytes"] == 0
    assert info["state_label_fa"]


# ── Thresholds ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("percent,expected", [
    (0.0, "ok"), (79.9, "ok"),
    (80.0, "warning"), (89.9, "warning"),
    (90.0, "critical"), (100.0, "critical"),
])
def test_the_shipped_thresholds_classify_at_80_and_90(st, percent, expected):
    assert st.classify(percent) == expected


def test_the_thresholds_are_configurable_from_the_settings_table(st):
    from app.db.queries import set_setting
    set_setting(st.WARN_SETTING_KEY, "50")
    set_setting(st.CRITICAL_SETTING_KEY, "60")
    assert st.thresholds() == (50.0, 60.0)
    assert st.classify(55.0) == "warning"
    assert st.classify(65.0) == "critical"
    assert st.classify(49.0) == "ok"


@pytest.mark.parametrize("bad", ["", "   ", "abc", "0", "-5", "101", "1e9"])
def test_a_nonsense_threshold_falls_back_to_the_shipped_default(st, bad):
    from app.db.queries import set_setting
    set_setting(st.WARN_SETTING_KEY, bad)
    assert st.thresholds()[0] == st.DEFAULT_WARN_PERCENT


def test_critical_is_never_reported_below_warning(st):
    """An operator who types them the wrong way round gets a usable panel, not
    a state machine that can never reach 'warning'."""
    from app.db.queries import set_setting
    set_setting(st.WARN_SETTING_KEY, "90")
    set_setting(st.CRITICAL_SETTING_KEY, "70")
    warn, critical = st.thresholds()
    assert warn == 90.0
    assert critical == 90.0


# ── has_space_for ───────────────────────────────────────────────────────

def test_has_space_for_compares_against_real_free_space(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 900, 100)
    assert st.has_space_for(99) is True
    assert st.has_space_for(100) is True      # exactly enough is enough
    assert st.has_space_for(101) is False


def test_has_space_for_treats_nothing_as_always_available(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 1000, 0)
    assert st.has_space_for(0) is True
    assert st.has_space_for(-1) is True


def test_has_space_for_says_no_when_it_cannot_measure(st, monkeypatch):
    def boom(_path):
        raise OSError("volume went away")

    monkeypatch.setattr(st, "_disk_usage", boom)
    assert st.has_space_for(1) is False
    assert st.free_bytes() == -1


def test_has_space_for_rejects_a_value_that_is_not_a_number(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 0, 1000)
    assert st.has_space_for("lots") is False
    assert st.has_space_for(None) is False


# ── Categories ──────────────────────────────────────────────────────────

def test_the_categories_are_the_directories_this_project_actually_has(st):
    keys = {c["key"] for c in st.categories()}
    # Exactly what the repository creates — no invented cache, queue or vector store.
    assert {"database_app", "database_logs", "videos", "uploads",
            "backups", "models", "data"} <= keys
    assert not (keys - {"database_app", "database_logs", "videos", "uploads",
                        "backups", "models", "data", "graphify"})


def test_a_category_never_carries_a_filesystem_path(st):
    for cat in st.categories():
        assert set(cat) == {"key", "label_fa", "kind", "exists", "bytes"}
        assert cat["bytes"] >= 0
        assert cat["label_fa"]
        assert not cat["label_fa"].startswith("/")


def test_a_database_category_counts_the_wal_and_shm_files_too(st, tmp_path):
    """A busy SQLite database is three files. Reporting only the first
    understates it by however large the write-ahead log has grown."""
    import app.config as config
    path = config.DB_PATH
    open(path + "-wal", "wb").write(b"0" * 5000)
    open(path + "-shm", "wb").write(b"0" * 1000)
    sizes = {c["key"]: c["bytes"] for c in st.categories()}
    assert sizes["database_app"] >= os.path.getsize(path) + 6000


def test_dir_size_skips_an_excluded_subdirectory(st, tmp_path):
    """data/ must not count data/models twice — the cached model is a gigabyte."""
    root = tmp_path / "tree"
    (root / "keep").mkdir(parents=True)
    (root / "skip").mkdir()
    (root / "keep" / "a.bin").write_bytes(b"x" * 100)
    (root / "skip" / "b.bin").write_bytes(b"x" * 900)

    assert st._dir_size(str(root)) == 1000
    assert st._dir_size(str(root), (str(root / "skip"),)) == 100
    assert st._dir_size(str(tmp_path / "does-not-exist")) == 0


# ── Alerts, rate limited ────────────────────────────────────────────────

def test_a_healthy_disk_writes_no_alert(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 100, 900)
    st.overview()
    assert _storage_log_rows() == []


def test_crossing_the_warning_threshold_writes_one_row(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 850, 150)
    st.overview()
    rows = _storage_log_rows()
    assert len(rows) == 1
    assert rows[0]["event_name"] == "storage.disk.low"
    assert rows[0]["level"] == "warning"


def test_crossing_the_critical_threshold_writes_one_critical_row(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 950, 50)
    st.overview()
    rows = _storage_log_rows()
    assert len(rows) == 1
    assert rows[0]["event_name"] == "storage.disk.critical"
    assert rows[0]["level"] == "critical"


def test_a_full_disk_does_not_produce_a_log_storm(st, monkeypatch):
    """The failure this prevents: a panel polling every few seconds while the
    disk is full, each poll writing another row onto the disk that is full."""
    _fake_disk(st, monkeypatch, 1000, 990, 10)
    for _ in range(25):
        st.overview()
    assert len(_storage_log_rows()) == 1


def test_the_alert_window_reopens_once_it_has_expired(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 990, 10)
    st.overview()
    assert len(_storage_log_rows()) == 1
    # Push the recorded alert an hour into the past.
    for state in list(st._last_alert):
        st._last_alert[state] -= (st.ALERT_INTERVAL_SECONDS + 1)
    from app.services import applog
    applog._recent.clear()          # identical rows are otherwise storm-collapsed
    st.overview()
    assert len(_storage_log_rows()) == 2


def test_warning_and_critical_are_rate_limited_independently(st, monkeypatch):
    _fake_disk(st, monkeypatch, 1000, 850, 150)
    st.overview()
    _fake_disk(st, monkeypatch, 1000, 950, 50)
    st.overview()
    events = sorted(r["event_name"] for r in _storage_log_rows())
    assert events == ["storage.disk.critical", "storage.disk.low"]


# ── The whole payload ───────────────────────────────────────────────────

def test_overview_returns_everything_the_page_needs(st, monkeypatch):
    _fake_disk(st, monkeypatch, 2000, 500, 1500)
    data = st.overview()
    assert set(data) == {"disk", "categories", "tracked_bytes", "thresholds"}
    assert data["disk"]["percent_used"] == 25.0
    assert data["disk"]["state"] == "ok"
    assert data["thresholds"] == {"warn_percent": 80.0, "critical_percent": 90.0}
    assert data["tracked_bytes"] == sum(c["bytes"] for c in data["categories"])
