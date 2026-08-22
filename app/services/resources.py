"""Live CPU, memory and GPU usage for the admin dashboard.

NO NEW DEPENDENCY ON PURPOSE
----------------------------
psutil would be the obvious answer and is not in requirements.txt. Everything
here comes from `/proc` (Linux, which is what this ships on) and `nvidia-smi`,
both already present on the host. CLAUDE.md asks that a new file justify its
existence; this one exists so the dashboard does not have to shell out and
parse in a request handler.

WHY THE RESULT IS CACHED
------------------------
`nvidia-smi` costs roughly half a second per call. The dashboard polls, two
installations share one machine, and every poll would otherwise fork a process:
a handful of admins with the page open would keep a subprocess running
permanently for numbers that do not change meaningfully within a second.

WHY CPU IS NOT A SINGLE READING
-------------------------------
/proc/stat holds counters since boot, so one reading gives the average since
the machine started — which on a long-lived server is a flat, useless number.
Utilisation is the DELTA between two readings. The previous sample is kept in
module state and reused, so a polled endpoint costs nothing; only a first call
(or one after a long gap) pays for a short measurement window.

WHY EVERYTHING HERE BLOCKS, AND WHO MAY CALL IT
-----------------------------------------------
`snapshot()` sleeps (the CPU measurement window) and waits on a subprocess (up
to NVIDIA_SMI_TIMEOUT). It must therefore NEVER be awaited on the event loop —
a hung nvidia-smi would stall every chat request on the server for five
seconds. The endpoint in app/routers/ops.py is a plain `def` for exactly this
reason, so FastAPI runs it in a worker thread.

`_lock` is re-entrant and covers BOTH the cache and the previous CPU sample, so
concurrent threads cannot interleave two readings into a nonsense delta. It is
deliberately held across the sleep and the subprocess: callers that pile up
behind it wake to a fresh cached answer instead of forking nvidia-smi again.

The state is per-process. Under multiple uvicorn workers each worker keeps its
own baseline and its own cache — still correct, since /proc/stat counters are
system-wide and any two readings of them form a valid window, but the cache
coalesces per worker rather than per machine. Sharing it would mean a shared
store for numbers that expire in three seconds; not worth it.
"""
import os
import shutil
import subprocess
import threading
import time
from typing import Optional

CACHE_TTL_SECONDS = 3.0
# Above this the stored sample describes a window too old to be "now".
MAX_SAMPLE_AGE_SECONDS = 60.0
NVIDIA_SMI_TIMEOUT = 5.0

# Named so tests can point them at fixture files. The dev machines are macOS
# and have no /proc at all, so the degraded path has to be reachable without
# one — and the healthy path has to be reachable without a Linux box.
PROC_STAT = "/proc/stat"
PROC_MEMINFO = "/proc/meminfo"

_lock = threading.RLock()
_cache: Optional[dict] = None
_cache_at = 0.0
_prev_cpu: Optional[tuple] = None      # (monotonic, idle_jiffies, total_jiffies)


# ── CPU ────────────────────────────────────────────────────────────────────

def _read_cpu_times() -> Optional[tuple]:
    """(idle, total) jiffies from the aggregate 'cpu' line of /proc/stat."""
    try:
        with open(PROC_STAT, encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("cpu "):
                    continue
                try:
                    parts = [int(v) for v in line.split()[1:]]
                except ValueError:
                    return None
                if len(parts) < 4:
                    return None
                # user nice system idle iowait irq softirq steal guest guest_nice
                # Only the first eight are summed: the kernel already counts
                # guest inside user and guest_nice inside nice, so summing all
                # ten double-counts them and understates usage on a hypervisor.
                parts = parts[:8]
                idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
                return idle, sum(parts)
    except OSError:
        pass
    return None


def cpu() -> dict:
    """Utilisation over the window since the last call, or a fresh short one."""
    global _prev_cpu

    cores = os.cpu_count() or 1
    with _lock:
        sample = _read_cpu_times()
        if sample is None:
            return {"available": False, "cores": cores}

        now = time.monotonic()
        prev = _prev_cpu
        if prev is None or (now - prev[0]) > MAX_SAMPLE_AGE_SECONDS:
            # No usable previous reading: measure a short window rather than
            # reporting the since-boot average, which would be wrong and stable
            # enough to look believable.
            time.sleep(0.12)
            first, sample = sample, _read_cpu_times()
            if sample is None:
                return {"available": False, "cores": cores}
            prev = (now, first[0], first[1])
            now = time.monotonic()

        idle_delta = sample[0] - prev[1]
        total_delta = sample[1] - prev[2]
        _prev_cpu = (now, sample[0], sample[1])

        # total_delta <= 0 means both readings landed inside the same jiffy, or
        # the counters were reset. There is no window to divide by, so report
        # nothing happened rather than a division by zero or a negative rate.
        percent = 0.0
        if total_delta > 0:
            percent = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))

    busy = cores * percent / 100.0
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = 0.0

    return {
        "available": True,
        "percent": round(percent, 1),
        "cores": cores,
        # What "3.4 of 40 cores" means: utilisation expressed in whole cores,
        # which is the number an operator can actually reason about.
        "busy_cores": round(busy, 1),
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
    }


# ── Memory ─────────────────────────────────────────────────────────────────

def memory() -> dict:
    """Totals from /proc/meminfo, in bytes.

    'used' is total minus MemAvailable, not minus MemFree: Linux deliberately
    fills free memory with cache, so MemFree on a healthy server is close to
    zero and would render as a permanently full bar.
    """
    values = {}
    try:
        with open(PROC_MEMINFO, encoding="utf-8") as fh:
            for line in fh:
                key, sep, rest = line.partition(":")
                if not sep:
                    continue
                fields = rest.split()
                if not fields:
                    continue
                try:
                    amount = int(fields[0])
                except ValueError:
                    # One odd line must not lose the whole file; the keys this
                    # function actually reads are all plain "<n> kB".
                    continue
                if len(fields) > 1 and fields[1].lower() == "kb":
                    amount *= 1024
                values[key] = amount
    except OSError:
        return {"available": False}

    total = values.get("MemTotal", 0)
    if not total:
        return {"available": False}
    avail = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - avail)

    swap_total = values.get("SwapTotal", 0)
    swap_used = max(0, swap_total - values.get("SwapFree", 0))

    return {
        "available": True,
        "total": total,
        "used": used,
        "free": avail,
        "percent": round(used / total * 100.0, 1),
        "swap_total": swap_total,
        "swap_used": swap_used,
        "swap_percent": round(swap_used / swap_total * 100.0, 1) if swap_total else 0.0,
    }


# ── GPU ────────────────────────────────────────────────────────────────────

_SMI_FIELDS = ("index", "name", "utilization.gpu", "memory.used",
               "memory.total", "temperature.gpu")


def _num(raw: str) -> Optional[float]:
    """A number from one nvidia-smi CSV cell, or None.

    nounits mode prints unsupported counters as `[N/A]` or `[Not Supported]` —
    real output on vGPU and on some older cards. Parsing those as numbers threw
    and dropped the whole row, so a card whose temperature sensor is not
    exposed disappeared from the dashboard along with its memory and its load.
    """
    raw = raw.strip()
    if not raw or raw.startswith("[") or raw.upper() == "N/A":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def gpus() -> dict:
    """Per-card usage via nvidia-smi.

    Absent hardware, an absent driver and a driver that is installed but not
    working are three different states an operator needs to tell apart — the
    P40s in this deployment spent a day in the third one — so the reason is
    returned rather than an empty list.
    """
    if not shutil.which("nvidia-smi"):
        return {"available": False, "reason": "nvidia-smi not installed", "gpus": []}

    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(_SMI_FIELDS)}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT,
            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "nvidia-smi timed out", "gpus": []}
    except OSError as exc:
        # which() found it a moment ago, so this is a permission problem or a
        # binary that cannot execute — worth saying out loud, not swallowing.
        return {"available": False, "reason": str(exc), "gpus": []}

    if proc.returncode != 0:
        # The common case is a driver that cannot talk to the card at all.
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"available": False,
                "reason": first[0] if first else "nvidia-smi failed",
                "gpus": []}

    out = []
    for line in (proc.stdout or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(_SMI_FIELDS):
            continue
        index, used_mb, total_mb = _num(parts[0]), _num(parts[3]), _num(parts[4])
        if index is None or used_mb is None or total_mb is None:
            continue
        util = _num(parts[2])
        out.append({
            "index": int(index),
            "name": parts[1],
            "percent": util if util is not None else 0.0,
            "memory_used": int(used_mb * 1024 * 1024),
            "memory_total": int(total_mb * 1024 * 1024),
            "memory_percent": round(used_mb / total_mb * 100.0, 1) if total_mb else 0.0,
            "temperature": _num(parts[5]),
        })

    return {"available": bool(out), "gpus": out,
            "reason": "" if out else "no GPU reported"}


# ── Disk ───────────────────────────────────────────────────────────────────

def disk(path: str = "/") -> dict:
    try:
        st = os.statvfs(path)
    except (OSError, AttributeError):
        return {"available": False}
    total = st.f_blocks * st.f_frsize
    # f_bavail, not f_bfree: the blocks reserved for root are not headroom the
    # application will ever get, so counting them as free overstates the slack.
    free = st.f_bavail * st.f_frsize
    used = max(0, total - free)
    return {
        "available": True,
        "total": total, "used": used, "free": free,
        "percent": round(used / total * 100.0, 1) if total else 0.0,
    }


# ── Public entry point ─────────────────────────────────────────────────────

def snapshot(force: bool = False) -> dict:
    """Everything the dashboard needs, at most once every CACHE_TTL_SECONDS.

    Blocking. See the module docstring — call it from a worker thread, never
    directly from an async handler.
    """
    global _cache, _cache_at

    with _lock:
        # monotonic, not wall clock: an NTP step backwards would otherwise make
        # the age negative and freeze the cache until the clock caught up.
        if not force and _cache is not None and (time.monotonic() - _cache_at) < CACHE_TTL_SECONDS:
            return {**_cache, "cached": True}

        data = {
            "cpu": cpu(),
            "memory": memory(),
            "gpu": gpus(),
            "disk": disk(),
            "ts": int(time.time()),
        }
        _cache, _cache_at = data, time.monotonic()
        return {**data, "cached": False}


def reset() -> None:
    """Drop the cache and the CPU baseline. For tests and for reload paths."""
    global _cache, _cache_at, _prev_cpu
    with _lock:
        _cache, _cache_at, _prev_cpu = None, 0.0, None
