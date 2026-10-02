"""The watchdog must be able to import the app the way systemd runs it.

padyar-watchdog@.service runs `/opt/padyar-<slug>/.venv/bin/python
/opt/padyar-watchdog/watchdog.py` with WorkingDirectory=/opt/padyar-<slug>.
Python puts the SCRIPT's directory on sys.path, not the working directory, so
the lazy `from app...` imports (alert phone, SMS sender, credit) failed with
ModuleNotFoundError on every cycle and the watchdog could never send an SMS.
The other watchdog tests load the script with the repo already on sys.path,
so they could not see it.

This test rebuilds that shape: a temp install dir holds only a symlink to the
app package, the script is copied to a separate directory, and it runs as a
subprocess with the app's python, cwd = the install dir, and only the
environment the unit gives it (its Environment= lines, with /opt/padyar-%i
mapped to the temp install dir). The database is a throwaway SQLite file, so
nothing reaches the network.

It must also stay hermetic: app.config loads `<install dir>/.env`, and with
cwd = the repo that would be the developer's real .env. A sitecustomize.py in
the temp install dir (on the child's PYTHONPATH) records every `.env` the
child opens and the ENV_FILE app.config settled on, so the test can prove the
repo's .env is never read.
"""
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UNIT = REPO / "deploy" / "systemd" / "padyar-watchdog@.service"
WATCHDOG = REPO / "deploy" / "watchdog" / "watchdog.py"
INSTALL = "myevent"

SPY = '''
import atexit, json, os, sys

_LOG = os.environ["WATCHDOG_TEST_ENV_LOG"]
_opened = []


def _hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        if os.path.basename(path) == ".env":
            _opened.append(os.path.abspath(path))


def _dump():
    config = sys.modules.get("app.config")
    with open(_LOG, "w", encoding="utf-8") as fh:
        json.dump({"opened": _opened,
                   "env_file": getattr(config, "ENV_FILE", None)}, fh)


sys.addaudithook(_hook)
atexit.register(_dump)
'''


def _unit_lines():
    return [line.strip() for line in UNIT.read_text(encoding="utf-8").splitlines()]


def _unit_value(key):
    return [line.split("=", 1)[1] for line in _unit_lines() if line.startswith(key + "=")]


def _unit_environment(app_dir):
    env = {}
    for assignment in _unit_value("Environment"):
        name, _, value = assignment.strip('"').partition("=")
        env[name] = value.replace("/opt/padyar-%i", str(app_dir)).replace("%i", INSTALL)
    return env


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_like_the_unit(tmp_path):
    app_dir = tmp_path / f"padyar-{INSTALL}"
    app_dir.mkdir()
    (app_dir / "app").symlink_to(REPO / "app", target_is_directory=True)
    (app_dir / "sitecustomize.py").write_text(SPY, encoding="utf-8")
    script_dir = tmp_path / "padyar-watchdog"
    script_dir.mkdir()
    shutil.copy(WATCHDOG, script_dir / "watchdog.py")
    env_log = tmp_path / "env-log.json"
    env = {
        "APP_PORT": str(_closed_port()),
        "DB_BACKEND": "sqlite",
        "DB_PATH": str(tmp_path / "app.db"),
        "LOGS_DB_PATH": str(tmp_path / "logs.db"),
        "HOME": str(tmp_path),
        "WATCHDOG_TEST_ENV_LOG": str(env_log),
    }
    env.update(_unit_environment(app_dir))
    result = subprocess.run(
        [sys.executable, str(script_dir / "watchdog.py"), "--install", INSTALL],
        cwd=app_dir, env=env, capture_output=True, text=True, timeout=120,
    )
    spy = json.loads(env_log.read_text(encoding="utf-8")) if env_log.exists() else None
    return result, app_dir, spy


def test_the_unit_runs_the_script_from_outside_the_app_dir():
    assert _unit_value("WorkingDirectory") == ["/opt/padyar-%i"]
    exec_start = _unit_value("ExecStart")
    assert len(exec_start) == 1
    assert "/opt/padyar-watchdog/watchdog.py" in exec_start[0], (
        "this test copies the script outside the app dir because the unit runs it from there"
    )


def test_watchdog_run_like_the_unit_can_import_the_app(tmp_path):
    result, app_dir, spy = _run_like_the_unit(tmp_path)

    assert result.returncode == 0, result.stderr
    journal = result.stdout + result.stderr
    assert "ModuleNotFoundError" not in journal, journal
    assert "cannot import the app" not in journal, journal

    assert spy is not None, "the unit's PYTHONPATH must reach the install dir"
    assert spy["env_file"] == str(app_dir / ".env"), spy
    assert str(REPO / ".env") not in spy["opened"], spy
    assert all(Path(p).is_relative_to(tmp_path) for p in spy["opened"]), spy
