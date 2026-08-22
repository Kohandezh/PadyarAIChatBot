"""Admin → dashboard → «منابع سرور» (server resources).

The gauges read /proc and nvidia-smi. Both are absent on the machines this is
developed on, and nvidia-smi is present-but-broken often enough on the machines
it runs on that "broken" is a state the UI has to render, not an exception the
page has to survive. So the questions here are:

  * can anyone but a logged-in admin read the host's load?      (no)
  * does a host with no /proc — every dev macOS box — degrade
    to "unavailable" instead of a 500?                          (yes)
  * do "no nvidia-smi", "nvidia-smi failed" and "nvidia-smi
    hung" each come back as a REASON the operator can act on?   (yes)
  * is memory "used" the number a human means by used?          (yes:
    total − MemAvailable, not total − MemFree)
  * does polling every five seconds fork a subprocess every
    five seconds?                                               (no — cached)

/proc is faked by pointing the module's two path constants at fixture files,
which is why they are constants. nvidia-smi is faked at shutil.which and
subprocess.run, so no test needs a GPU or a driver.
"""
import datetime
import secrets
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.services import resources


# ── Fixtures ────────────────────────────────────────────────────────────

# A real /proc/stat aggregate line, 8-core host. The trailing 0 0 are guest and
# guest_nice, which the kernel ALSO counts inside user and nice.
STAT_A = ("cpu  1000 200 300 8000 100 10 20 5 0 0\n"
          "cpu0 100 20 30 800 10 1 2 0 0 0\n"
          "intr 12345\n")
# 100 more jiffies of user time, 100 more of idle: half the window was busy.
STAT_B = ("cpu  1100 200 300 8100 100 10 20 5 0 0\n"
          "cpu0 110 20 30 810 10 1 2 0 0 0\n"
          "intr 99999\n")

MEMINFO = (
    "MemTotal:       32768000 kB\n"
    "MemFree:          512000 kB\n"
    "MemAvailable:   16384000 kB\n"
    "Buffers:          256000 kB\n"
    "Cached:         15000000 kB\n"
    "SwapTotal:       4096000 kB\n"
    "SwapFree:        3096000 kB\n"
    "HugePages_Total:       0\n"
    "DirectMap4k:      123456 kB\n"
)


@pytest.fixture(autouse=True)
def _clean_module_state():
    """The cache and the CPU baseline are module state shared by the process.

    Without this, whichever test ran first decides what every later test sees —
    exactly the failure mode conftest.py already documents for the OTP quota.
    """
    resources.reset()
    yield
    resources.reset()


@pytest.fixture()
def proc(tmp_path, monkeypatch):
    """A fake /proc. Returns a writer so a test can move the counters."""
    stat = tmp_path / "stat"
    meminfo = tmp_path / "meminfo"
    stat.write_text(STAT_A, encoding="utf-8")
    meminfo.write_text(MEMINFO, encoding="utf-8")
    monkeypatch.setattr(resources, "PROC_STAT", str(stat))
    monkeypatch.setattr(resources, "PROC_MEMINFO", str(meminfo))
    return stat


@pytest.fixture()
def no_proc(tmp_path, monkeypatch):
    """A host without /proc at all — i.e. every macOS development machine."""
    monkeypatch.setattr(resources, "PROC_STAT", str(tmp_path / "nope" / "stat"))
    monkeypatch.setattr(resources, "PROC_MEMINFO", str(tmp_path / "nope" / "meminfo"))


def fake_smi(monkeypatch, *, installed=True, stdout="", stderr="", returncode=0,
             raises=None):
    """Replace the nvidia-smi lookup and invocation."""
    monkeypatch.setattr(resources.shutil, "which",
                        lambda name: "/usr/bin/nvidia-smi" if installed else None)

    def run(cmd, **kwargs):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(resources.subprocess, "run", run)


@pytest.fixture()
def anon(tmp_path, monkeypatch):
    """The app on a throwaway database, with nobody logged in."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client(anon):
    """The same app with a real admin session cookie and a CSRF token."""
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection

    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    anon.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    anon.headers.update({'X-CSRF-Token': token_for_session(token)})
    return anon


# ── The endpoint is admin-only ──────────────────────────────────────────

def test_an_anonymous_caller_cannot_read_the_hosts_load(anon):
    """Core counts, RAM size and GPU model are a map of the machine. They are
    not public, and a 200 here would hand a stranger the reconnaissance."""
    res = anon.get("/admin/api/ops/resources")
    assert res.status_code in (401, 403)


def test_an_admin_gets_every_section(client):
    res = client.get("/admin/api/ops/resources")
    assert res.status_code == 200
    body = res.json()
    for section in ("cpu", "memory", "gpu", "disk"):
        assert section in body, f"{section} missing from the snapshot"
        assert "available" in body[section]
    assert isinstance(body["ts"], int)


def test_the_endpoint_does_not_run_on_the_event_loop():
    """snapshot() sleeps and waits up to five seconds on a subprocess.

    Declared `async def` it would block the single event loop, so a wedged
    GPU driver would freeze every chat request on the server. A plain `def`
    hands it to FastAPI's threadpool. This is a regression guard, not a style
    check — the difference is invisible until the driver hangs.
    """
    import inspect
    from app.routers import ops
    assert not inspect.iscoroutinefunction(ops.system_resources)


# ── A host without /proc degrades, it does not explode ──────────────────

def test_a_host_without_proc_reports_unavailable_rather_than_raising(no_proc):
    """This is the developer's macOS laptop, and it must still render."""
    fake = resources.snapshot()
    assert fake["cpu"]["available"] is False
    assert fake["memory"]["available"] is False
    # Core count comes from the interpreter, not /proc, so it survives — the
    # gauge can still say what the machine has even if not what it is doing.
    assert fake["cpu"]["cores"] >= 1


def test_the_whole_endpoint_still_answers_200_without_proc(client, no_proc):
    res = client.get("/admin/api/ops/resources")
    assert res.status_code == 200
    assert res.json()["memory"]["available"] is False


# ── CPU ─────────────────────────────────────────────────────────────────

def test_cpu_percent_is_the_delta_between_two_readings(proc):
    """A single /proc/stat reading is the average since boot — a flat, useless
    number on a server with months of uptime. Utilisation is the delta."""
    first = resources.cpu()
    assert first["available"] is True

    proc.write_text(STAT_B, encoding="utf-8")
    second = resources.cpu()
    # 100 busy jiffies against 100 idle jiffies in the window = 50%.
    assert second["percent"] == 50.0
    assert second["cores"] >= 1
    assert second["busy_cores"] == round(second["cores"] * 0.5, 1)


def test_guest_time_is_not_counted_twice(proc):
    """The kernel reports guest inside user and guest_nice inside nice, then
    prints them again as fields 9 and 10. Summing all ten inflates the
    denominator and understates load on any host running VMs."""
    proc.write_text("cpu  1000 200 300 8000 100 10 20 5 400 100\n", encoding="utf-8")
    idle, total = resources._read_cpu_times()
    assert idle == 8100                       # idle + iowait
    assert total == 1000 + 200 + 300 + 8000 + 100 + 10 + 20 + 5


def test_a_truncated_cpu_line_is_not_a_crash(proc):
    proc.write_text("cpu  hello world\nintr 1\n", encoding="utf-8")
    assert resources.cpu()["available"] is False


# ── Memory ──────────────────────────────────────────────────────────────

def test_used_memory_is_total_minus_available_not_total_minus_free(proc):
    """Linux fills idle RAM with page cache on purpose, so MemFree on a healthy
    server sits near zero. Sizing "used" off it would paint a permanently full
    bar and send an operator hunting a leak that is not there."""
    mem = resources.memory()
    total = 32768000 * 1024
    assert mem["total"] == total
    assert mem["used"] == total - 16384000 * 1024      # MemAvailable
    assert mem["used"] != total - 512000 * 1024        # MemFree
    assert mem["percent"] == 50.0
    assert mem["swap_used"] == (4096000 - 3096000) * 1024


def test_memory_falls_back_to_memfree_on_a_kernel_without_memavailable(proc,
                                                                       tmp_path,
                                                                       monkeypatch):
    """MemAvailable arrived in Linux 3.14. Older kernels get the worse number
    rather than no memory gauge at all."""
    old = tmp_path / "meminfo_old"
    old.write_text("MemTotal: 1000 kB\nMemFree: 250 kB\n", encoding="utf-8")
    monkeypatch.setattr(resources, "PROC_MEMINFO", str(old))
    mem = resources.memory()
    assert mem["available"] is True
    assert mem["used"] == 750 * 1024
    assert mem["swap_total"] == 0
    assert mem["swap_percent"] == 0.0


def test_a_meminfo_line_that_is_not_a_number_does_not_lose_the_file(proc, tmp_path,
                                                                    monkeypatch):
    odd = tmp_path / "meminfo_odd"
    odd.write_text("SomeFlag:  yes\nMemTotal: 1000 kB\nMemAvailable: 400 kB\n",
                   encoding="utf-8")
    monkeypatch.setattr(resources, "PROC_MEMINFO", str(odd))
    assert resources.memory()["used"] == 600 * 1024


# ── GPU ─────────────────────────────────────────────────────────────────

# Verbatim shape of `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`
# on the two-P40 host this ships to.
SMI_TWO_CARDS = ("0, Tesla P40, 37, 8192, 24576, 62\n"
                 "1, Tesla P40, 0, 11, 24576, 34\n")


def test_a_real_nvidia_smi_line_parses_into_the_numbers_the_gauge_needs(monkeypatch):
    fake_smi(monkeypatch, stdout=SMI_TWO_CARDS)
    gpu = resources.gpus()
    assert gpu["available"] is True
    assert len(gpu["gpus"]) == 2

    first = gpu["gpus"][0]
    assert first == {
        "index": 0,
        "name": "Tesla P40",
        "percent": 37.0,
        "memory_used": 8192 * 1024 * 1024,
        "memory_total": 24576 * 1024 * 1024,
        "memory_percent": round(8192 / 24576 * 100, 1),
        "temperature": 62.0,
    }
    # An idle card is 0%, and 0 must not be mistaken for "unknown".
    assert gpu["gpus"][1]["percent"] == 0.0


def test_a_card_that_does_not_report_a_sensor_keeps_its_other_numbers(monkeypatch):
    """nvidia-smi prints `[N/A]` and `[Not Supported]`, brackets included, for
    counters a card does not expose — routine on vGPU. Parsing those as floats
    threw and dropped the entire row, so one missing temperature sensor took
    the card's memory and load off the dashboard with it."""
    fake_smi(monkeypatch,
             stdout="0, Tesla P40, [Not Supported], 8192, 24576, [N/A]\n")
    gpu = resources.gpus()
    assert len(gpu["gpus"]) == 1
    card = gpu["gpus"][0]
    assert card["temperature"] is None
    assert card["percent"] == 0.0
    assert card["memory_used"] == 8192 * 1024 * 1024


def test_nvidia_smi_absent_is_a_reason_not_an_exception(monkeypatch):
    fake_smi(monkeypatch, installed=False)
    gpu = resources.gpus()
    assert gpu["available"] is False
    assert gpu["gpus"] == []
    assert "not installed" in gpu["reason"]


def test_nvidia_smi_failing_reports_what_the_driver_said(monkeypatch):
    """The commonest real failure: the tool is installed, the driver is loaded,
    and it cannot talk to the card. Silence here cost a day."""
    fake_smi(monkeypatch, returncode=9,
             stderr="NVIDIA-SMI has failed because it couldn't communicate "
                    "with the NVIDIA driver.\n")
    gpu = resources.gpus()
    assert gpu["available"] is False
    assert "couldn't communicate" in gpu["reason"]


def test_nvidia_smi_timing_out_is_a_reason_not_an_exception(monkeypatch):
    fake_smi(monkeypatch,
             raises=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5))
    gpu = resources.gpus()
    assert gpu["available"] is False
    assert "timed out" in gpu["reason"]


def test_nvidia_smi_that_cannot_be_executed_is_a_reason_not_an_exception(monkeypatch):
    fake_smi(monkeypatch, raises=PermissionError(13, "Permission denied"))
    gpu = resources.gpus()
    assert gpu["available"] is False
    assert gpu["reason"]
    assert gpu["gpus"] == []


def test_a_working_nvidia_smi_that_lists_no_card_says_so(monkeypatch):
    fake_smi(monkeypatch, stdout="\n")
    gpu = resources.gpus()
    assert gpu["available"] is False
    assert gpu["reason"] == "no GPU reported"


def test_the_endpoint_answers_200_when_the_gpu_is_broken(client, monkeypatch):
    """The point of the whole reason/available contract: a broken GPU renders,
    it does not 500 the dashboard."""
    fake_smi(monkeypatch, returncode=9, stderr="driver not loaded\n")
    res = client.get("/admin/api/ops/resources")
    assert res.status_code == 200
    assert res.json()["gpu"]["available"] is False


# ── The cache ───────────────────────────────────────────────────────────

def test_a_second_poll_inside_the_ttl_is_served_from_cache(proc, monkeypatch):
    """The dashboard polls, several tabs may be open, and nvidia-smi costs
    roughly half a second per fork. Without this every tab tick forks."""
    forks = []

    def run(cmd, **kwargs):
        forks.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, SMI_TWO_CARDS, "")

    monkeypatch.setattr(resources.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(resources.subprocess, "run", run)

    first = resources.snapshot()
    second = resources.snapshot()
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(forks) == 1
    # Same numbers and the same timestamp — a cached answer must not pretend
    # to have been measured now.
    assert second["ts"] == first["ts"]
    assert second["gpu"] == first["gpu"]


def test_force_bypasses_the_cache(proc, monkeypatch):
    fake_smi(monkeypatch, installed=False)
    resources.snapshot()
    assert resources.snapshot(force=True)["cached"] is False


def test_an_expired_cache_is_measured_again(proc, monkeypatch):
    monkeypatch.setattr(resources, "CACHE_TTL_SECONDS", 0.0)
    fake_smi(monkeypatch, installed=False)
    assert resources.snapshot()["cached"] is False
    assert resources.snapshot()["cached"] is False


def test_concurrent_readers_do_not_interleave_into_a_nonsense_delta(proc, monkeypatch):
    """Two threads polling at once must not each take half of the other's CPU
    window. The service holds one re-entrant lock across the cache AND the
    stored sample; the visible consequence is that percentages stay in range.
    """
    import threading

    fake_smi(monkeypatch, installed=False)
    results = []

    def poll():
        for _ in range(5):
            results.append(resources.snapshot(force=True))

    threads = [threading.Thread(target=poll) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 20
    for snap in results:
        assert 0.0 <= snap["cpu"]["percent"] <= 100.0


# ── Disk ────────────────────────────────────────────────────────────────

def test_disk_of_a_path_that_does_not_exist_is_unavailable():
    assert resources.disk("/definitely/not/a/mount/point")["available"] is False


# ── The page that shows the gauges ──────────────────────────────────────

def test_the_dashboard_ships_its_scripts_with_a_working_cache_buster(client):
    """`?v=` empty is not a cosmetic bug.

    Nothing sends Cache-Control for /static, so browsers cache it
    heuristically. A dashboard shipped with new HTML and a cached old
    dashboard.js has markup for gauges and no code to fill them — a card that
    sits there empty and looks like a server problem. The template renders
    `?v={{ js_version }}`; if the route forgets to pass it, Jinja renders
    nothing at all and every visit gets the same cacheable URL forever.
    """
    import re
    res = client.get("/secure-panel-inotex")
    assert res.status_code == 200
    for script in ("dashboard.js", "resources.js"):
        match = re.search(rf"/static/admin/js/{script}\?v=(\d*)", res.text)
        assert match, f"{script} is not imported with a cache-buster"
        assert match.group(1), f"{script} rendered an EMPTY ?v= — see this test"

    # And the card itself is on the page, in Persian, with all three canvases.
    assert "منابع سرور" in res.text
    for canvas in ("cpuGauge", "memGauge", "gpuGauge"):
        assert canvas in res.text


def test_one_cache_buster_helper_serves_every_admin_page(tmp_path, monkeypatch):
    """Two pages needed this and briefly had two copies of it. The version is
    the NEWEST mtime across a page's scripts, so touching any one of them
    invalidates that page."""
    from app.routers import public

    js = tmp_path / "static" / "admin" / "js"
    js.mkdir(parents=True)
    (js / "old.js").write_text("//", encoding="utf-8")
    (js / "new.js").write_text("//", encoding="utf-8")
    import os
    os.utime(js / "old.js", (1_000_000, 1_000_000))
    os.utime(js / "new.js", (2_000_000, 2_000_000))
    monkeypatch.setattr(public, "BASE_DIR", str(tmp_path))

    assert public.admin_js_version("old.js") == "1000000"
    assert public.admin_js_version("old.js", "new.js") == "2000000"
    # A missing file costs freshness; it must never break the page.
    assert public.admin_js_version("gone.js") == "0"
    assert public.admin_js_version("gone.js", "new.js") == "2000000"


def test_no_admin_router_keeps_a_private_copy_of_the_cache_buster():
    """app/routers/tts.py grew its own `_js_version()` doing exactly this job.
    Two copies means one of them gets fixed and the other does not."""
    import inspect
    from app.routers import tts
    source = inspect.getsource(tts)
    assert "def _js_version" not in source
    assert "admin_js_version" in source
