"""deploy/55-monitoring.sh, tested without a server (SPEC monitoring-stack REQ-078).

The script needs root, systemd and apt, none of which a test has. So its
logic lives in small functions, and `main` only runs when the file is
executed. These tests source the file in bash and call one function at a
time with temp files and stub commands.

What matters most here is the `.env` reader (SEC-010). The script is the
first one in the kit that reads an install's `.env` as root, and that file
is rewritten by the app from admin-panel input. So it must never execute
any part of the file, never print a value, and stop rather than guess when
a line is written in a form it cannot read with certainty.

The functions use bash 4 features (`local -a`, `${x: -1}`). macOS ships
bash 3.2, so the tests look for a bash >= 4 on PATH and skip with a reason
when there is none. CI runs Ubuntu, where bash is 5.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "55-monitoring.sh"

SECRET = "synthetic-notreal-token-value"


def _modern_bash():
    path = shutil.which("bash")
    if not path:
        return None
    out = subprocess.run([path, "-c", "echo ${BASH_VERSINFO[0]}"],
                         capture_output=True, text=True)
    return path if out.stdout.strip().isdigit() and int(out.stdout) >= 4 else None


BASH = _modern_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash >= 4 on PATH (macOS ships 3.2)")


def run(snippet, env=None, cwd=None):
    """Source the script, then run `snippet` in the same bash."""
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [BASH, "-c", f'source "{SCRIPT}"\n{snippet}'],
        capture_output=True, text=True, env=full_env, cwd=cwd)


def env_file(tmp_path, text, name="env"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def code_lines():
    """The script without comment lines, for the static checks."""
    return [line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]


# ── REQ-078: syntax, and the .env is never sourced ─────────────────────


@needs_bash
def test_the_script_parses():
    out = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


SOURCES_ENV = re.compile(r"(^|[;&|({]\s*|\s)(source|\.)\s+\S*\.env\b")


def test_the_script_never_sources_or_evals_an_env_file():
    offenders = [line for line in code_lines() if SOURCES_ENV.search(line)]
    assert not offenders, "\n".join(offenders)
    assert not [line for line in code_lines() if re.search(r"\beval\b", line)]


@pytest.mark.parametrize("line", [
    ". /opt/padyar-x/.env",
    'source "$APP_DIR/.env"',
    "set -a; . ./.env; set +a",
    'bash -c "set -a; . \'${dir}/.env\'"',
])
def test_the_source_check_catches_each_way_of_sourcing(line):
    assert SOURCES_ENV.search(line)


def test_the_source_check_ignores_reading_the_file():
    assert not SOURCES_ENV.search('value=$(env_value "/opt/padyar-${slug}/.env" APP_PORT)')


def test_no_token_ever_goes_into_a_curl_argument():
    """REQ-005: the Authorization header comes from a file (`-H @file`)."""
    curl_lines = [line for line in code_lines() if re.search(r"\bcurl\b", line)]
    assert curl_lines, "no curl call found, so nothing was checked"
    for line in curl_lines:
        assert "Bearer" not in line, line
        assert not re.search(r"\$\{?\w*token\b", line, re.I), line


def test_the_whole_run_holds_the_lock():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "LOCK_FILE=/run/padyar-monitoring.lock" in text
    assert re.search(r'flock -n 9 \|\| die "Another run of 55-monitoring.sh is in progress', text)


# ── SEC-010 / N3: the .env reader ──────────────────────────────────────


@needs_bash
@pytest.mark.parametrize("line, value", [
    ("APP_PORT=8001", "8001"),
    ('METRICS_TOKEN="abc_DEF-123"', "abc_DEF-123"),
    ("METRICS_TOKEN='abc'", "abc"),
    ("METRICS_TOKEN=abc   \r", "abc"),
    ("METRICS_TOKEN=", ""),
    ('METRICS_TOKEN=""', ""),
    ("ASANAK_PASSWORD=enc:gAAAAABnotrealsyntheticxyz==", "enc:gAAAAABnotrealsyntheticxyz=="),
])
def test_a_value_is_read_with_one_layer_of_quotes_removed(tmp_path, line, value):
    key = line.split("=", 1)[0]
    path = env_file(tmp_path, f"# a comment\nOTHER=1\n{line}\n")
    out = run(f'env_value "{path}" {key}')
    assert out.returncode == 0, out.stderr
    assert out.stdout == value + "\n"


@needs_bash
def test_a_missing_key_is_reported_apart_from_an_empty_one(tmp_path):
    path = env_file(tmp_path, "APP_PORT=8001\n")
    out = run(f'env_value "{path}" METRICS_TOKEN')
    assert out.returncode == 1
    assert out.stdout == ""


@needs_bash
@pytest.mark.parametrize("line", [
    f"export METRICS_TOKEN={SECRET}",
    f"  METRICS_TOKEN={SECRET}",
    f"METRICS_TOKEN ={SECRET}",
    f'METRICS_TOKEN="{SECRET}',
    f'METRICS_TOKEN="{SECRET}\\"x"',
    f"METRICS_TOKEN='{SECRET}\"",
    f"METRICS_TOKEN={SECRET} # note",
    f'METRICS_TOKEN="{SECRET}$HOME"',
    f"METRICS_TOKEN=$({SECRET})",
])
def test_a_line_it_cannot_read_with_certainty_stops_without_printing_the_value(tmp_path, line):
    path = env_file(tmp_path, line + "\n")
    out = run(f'env_value "{path}" METRICS_TOKEN')
    assert out.returncode == 2
    assert "METRICS_TOKEN" in out.stderr and str(path) in out.stderr
    assert SECRET not in out.stdout + out.stderr


@needs_bash
def test_the_same_key_twice_with_different_values_stops(tmp_path):
    path = env_file(tmp_path, f"METRICS_TOKEN={SECRET}\nMETRICS_TOKEN=other\n")
    out = run(f'env_value "{path}" METRICS_TOKEN')
    assert out.returncode == 2
    assert SECRET not in out.stdout + out.stderr


@needs_bash
def test_the_same_key_twice_with_the_same_value_is_fine(tmp_path):
    path = env_file(tmp_path, "APP_PORT=8001\nAPP_PORT=\"8001\"\n")
    out = run(f'env_value "{path}" APP_PORT')
    assert (out.returncode, out.stdout) == (0, "8001\n")


@needs_bash
def test_nothing_in_a_value_is_ever_executed(tmp_path):
    marker = tmp_path / "pwned"
    path = env_file(tmp_path, f"ASANAK_USERNAME='$(touch {marker})'\nSECRET_KEY=`touch {marker}`\n")
    run(f'env_value "{path}" ASANAK_USERNAME || true; env_value "{path}" SECRET_KEY || true')
    assert not marker.exists()


# ── REQ-003 / REQ-004: the install's port and token ────────────────────


@needs_bash
def test_a_good_env_gives_the_port_and_the_token(tmp_path):
    path = env_file(tmp_path, f"APP_PORT=8001\nMETRICS_TOKEN={SECRET}\n")
    out = run(f'read_install_env inotex "{path}"; echo "$INSTALL_PORT"; '
              f'[[ "$INSTALL_TOKEN" == "{SECRET}" ]] && echo token-ok')
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["8001", "token-ok"]


@needs_bash
def test_an_empty_token_stops_with_the_sed_fix(tmp_path):
    path = env_file(tmp_path, "APP_PORT=8001\nMETRICS_TOKEN=\n")
    out = run(f'read_install_env inotex "{path}"')
    assert out.returncode == 1
    assert ('sudo sed -i "s/^METRICS_TOKEN=.*/METRICS_TOKEN=$(openssl rand -hex 32)/" '
            f'{path} && sudo systemctl restart padyar-inotex') in out.stderr


@needs_bash
def test_a_missing_token_stops_with_the_append_fix(tmp_path):
    path = env_file(tmp_path, "APP_PORT=8001\n")
    out = run(f'read_install_env inotex "{path}"')
    assert out.returncode == 1
    assert 'echo "METRICS_TOKEN=$(openssl rand -hex 32)" | sudo tee -a' in out.stderr
    assert "sudo systemctl restart padyar-inotex" in out.stderr


@needs_bash
@pytest.mark.parametrize("text", [
    f"APP_PORT=80a1\nMETRICS_TOKEN={SECRET}\n",
    f"APP_PORT=8001\nMETRICS_TOKEN={SECRET}+/\n",
])
def test_a_value_that_fails_its_pattern_stops_without_being_printed(tmp_path, text):
    path = env_file(tmp_path, text)
    out = run(f'read_install_env inotex "{path}"')
    assert out.returncode == 1
    assert SECRET not in out.stdout + out.stderr
    assert "80a1" not in out.stdout + out.stderr


# ── REQ-008: who owns the host alerts ──────────────────────────────────


@needs_bash
def test_the_first_slug_owns_the_host_alerts_on_a_fresh_host(tmp_path):
    out = run(f'choose_host_owner "{tmp_path / "owner"}" "" inotex inotex elecomp')
    assert (out.returncode, out.stdout) == (0, "inotex\n")


@needs_bash
def test_a_rerun_with_other_slugs_keeps_the_existing_owner(tmp_path):
    owner = tmp_path / "owner"
    owner.write_text("inotex\n")
    out = run(f'choose_host_owner "{owner}" "" elecomp inotex elecomp')
    assert (out.returncode, out.stdout) == (0, "inotex\n")


@needs_bash
def test_the_flag_moves_the_owner_to_a_registered_install(tmp_path):
    owner = tmp_path / "owner"
    owner.write_text("inotex\n")
    out = run(f'choose_host_owner "{owner}" elecomp inotex inotex elecomp')
    assert (out.returncode, out.stdout) == (0, "elecomp\n")


@needs_bash
def test_the_flag_refuses_an_install_that_is_not_registered(tmp_path):
    out = run(f'choose_host_owner "{tmp_path / "owner"}" ghost inotex inotex elecomp')
    assert out.returncode == 1
    assert "ghost" in out.stderr


# ── REQ-010: the domain comes from the install's nginx site ────────────


def _site(slug, domain):
    template = (REPO / "deploy" / "nginx" / "instance.conf.template").read_text(encoding="utf-8")
    return (template.replace("{{SLUG}}", slug).replace("{{DOMAIN}}", domain)
            .replace("{{PORT}}", "8001"))


@needs_bash
def test_the_domain_is_read_from_the_rendered_nginx_site(tmp_path):
    (tmp_path / "chat.example.com.conf").write_text(_site("inotex", "chat.example.com"))
    (tmp_path / "bot.example.org.conf").write_text(_site("inotex2", "bot.example.org"))
    out = run(f'domain_for_slug "{tmp_path}" inotex')
    assert (out.returncode, out.stdout) == (0, "chat.example.com\n")


@needs_bash
@pytest.mark.parametrize("sites", [[], [("a.example.com", "inotex"), ("b.example.com", "inotex")]])
def test_no_site_or_two_sites_stops_and_names_the_nginx_step(tmp_path, sites):
    for domain, slug in sites:
        (tmp_path / f"{domain}.conf").write_text(_site(slug, domain))
    out = run(f'domain_for_slug "{tmp_path}" inotex')
    assert out.returncode == 1
    assert "15-nginx-and-ssl.sh" in out.stderr


# ── REQ-016: retention, counting the TSDB's own size ───────────────────

GIB = 1024 ** 3


@needs_bash
@pytest.mark.parametrize("avail, tsdb, expected", [
    (100 * GIB, 0, "5120"),
    (10 * GIB, 0, "3072"),
    (2 * GIB, 3 * GIB, "1536"),
])
def test_the_size_cap_is_the_smaller_of_5gb_and_30_percent_of_free_plus_tsdb(avail, tsdb, expected):
    out = run(f"retention_size_mb {avail} {tsdb}")
    assert (out.returncode, out.stdout) == (0, expected + "\n")


@needs_bash
def test_a_cap_under_1gb_stops():
    out = run(f"retention_size_mb {3 * GIB} 0")
    assert out.returncode == 1


# ── REQ-020: listeners ─────────────────────────────────────────────────

GOOD_SS = """\
LISTEN 0 4096 127.0.0.1:9090 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:9093 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:9100 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:9115 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:9187 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:20241 0.0.0.0:*
LISTEN 0 511 0.0.0.0:443 0.0.0.0:*
LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:90900 0.0.0.0:*
"""


@needs_bash
def test_loopback_only_listeners_pass():
    out = run(f"public_listeners <<'SS'\n{GOOD_SS}SS")
    assert (out.returncode, out.stdout) == (0, "")


@needs_bash
@pytest.mark.parametrize("line, port", [
    ("LISTEN 0 4096 0.0.0.0:9100 0.0.0.0:*", "9100"),
    ("LISTEN 0 4096 *:9090 *:*", "9090"),
    ("LISTEN 0 4096 [::]:9093 [::]:*", "9093"),
    ("LISTEN 0 4096 127.0.0.1:9094 0.0.0.0:*", "9094"),
])
def test_a_public_listener_or_the_cluster_port_is_reported(line, port):
    out = run(f"public_listeners <<'SS'\n{GOOD_SS}{line}\nSS")
    assert out.returncode == 1
    assert port in out.stdout


# ── REQ-024: the tunnel's metrics line ─────────────────────────────────

TUNNEL = """\
tunnel: 00000000-0000-0000-0000-000000000000
credentials-file: /etc/cloudflared/credentials.json
no-autoupdate: true

ingress:
  - hostname: chat.example.com
    service: https://127.0.0.1:443
  - hostname: bot.example.org
    service: https://127.0.0.1:443
  - service: http_status:404
"""


@needs_bash
@pytest.mark.parametrize("extra, state", [
    ("", "absent"),
    ("metrics: 127.0.0.1:20241\n", "ours"),
    ("metrics: 0.0.0.0:2000\n", "other"),
])
def test_the_metrics_line_state_is_read_from_the_tunnel_config(tmp_path, extra, state):
    path = env_file(tmp_path, extra + TUNNEL, "config.yml")
    out = run(f'metrics_line_state "{path}"')
    assert (out.returncode, out.stdout) == (0, state + "\n")


@needs_bash
def test_inserting_the_metrics_line_adds_one_line_after_the_credentials(tmp_path):
    path = env_file(tmp_path, TUNNEL, "config.yml")
    out = run(f'insert_metrics_line "{path}"')
    assert out.returncode == 0, out.stderr
    lines = path.read_text().splitlines()
    assert lines == (TUNNEL.splitlines()[:2] + ["metrics: 127.0.0.1:20241"]
                     + TUNNEL.splitlines()[2:])


@needs_bash
def test_no_credentials_line_means_no_insert(tmp_path):
    path = env_file(tmp_path, TUNNEL.replace("credentials-file", "creds"), "config.yml")
    out = run(f'insert_metrics_line "{path}"')
    assert out.returncode == 1
    assert "metrics:" not in path.read_text()


# ── REQ-007 / REQ-005: the live token check ────────────────────────────


@pytest.fixture
def fake_curl(tmp_path):
    """A curl that records its argv and the header file, then answers $CODE."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{tmp_path}/argv"\n'
        'for a in "$@"; do [[ "$a" == @* ]] && cp "${a#@}" '
        f'"{tmp_path}/header" && echo "${{a#@}}" > "{tmp_path}/header-path"; done\n'
        'printf "%s" "$CODE"\n')
    curl.chmod(0o755)
    return bindir


@needs_bash
@pytest.mark.parametrize("code, rc", [("200", 0), ("403", 1), ("500", 1)])
def test_the_token_goes_in_a_header_file_never_in_argv(tmp_path, fake_curl, code, rc):
    token = tmp_path / "inotex.token"
    token.write_text(SECRET)
    out = run(f'check_metrics_token inotex 8001 "{token}"',
              env={"PATH": f"{fake_curl}:{os.environ['PATH']}", "CODE": code})
    assert out.returncode == rc, out.stderr
    argv = (tmp_path / "argv").read_text()
    assert SECRET not in argv and "http://127.0.0.1:8001/metrics" in argv
    assert (tmp_path / "header").read_text() == f"Authorization: Bearer {SECRET}\n"
    assert not Path((tmp_path / "header-path").read_text().strip()).exists(), \
        "the header file must be deleted after the check"
    assert SECRET not in out.stdout + out.stderr
    if code == "403":
        assert "sudo systemctl restart padyar-inotex" in out.stderr
    if code == "500":
        assert "500" in out.stderr


# ── REQ-023: can a host alert be texted while the database is down? ──

GOOD_SMS = ("ASANAK_USERNAME=booth\nASANAK_PASSWORD=enc:gAAAAABnotreal\n"
            "ASANAK_SOURCE=3000\nSECRET_KEY=deadbeefnotreal\nSMS_DAILY_BUDGET=0\n")


@needs_bash
def test_a_complete_env_gives_no_sms_warning(tmp_path):
    path = env_file(tmp_path, GOOD_SMS)
    out = run(f'sms_env_warnings "{path}"')
    assert (out.returncode, out.stdout) == (0, "")


@needs_bash
@pytest.mark.parametrize("old, new", [
    ("ASANAK_USERNAME=booth", "ASANAK_USERNAME="),
    ("ASANAK_PASSWORD=enc:gAAAAABnotreal", "ASANAK_API_KEY=enc:gAAAAABnotreal"),
    ("ASANAK_SOURCE=3000\n", ""),
])
def test_a_missing_send_credential_warns_even_with_an_api_key(tmp_path, old, new):
    path = env_file(tmp_path, GOOD_SMS.replace(old, new))
    out = run(f'sms_env_warnings "{path}"')
    assert out.returncode == 0
    assert "Asanak" in out.stdout and "database" in out.stdout


@needs_bash
def test_an_encrypted_password_without_secret_key_warns(tmp_path):
    path = env_file(tmp_path, GOOD_SMS.replace("SECRET_KEY=deadbeefnotreal", "SECRET_KEY="))
    out = run(f'sms_env_warnings "{path}"')
    assert out.returncode == 0
    assert "SECRET_KEY" in out.stdout


@needs_bash
def test_a_daily_budget_in_env_warns(tmp_path):
    path = env_file(tmp_path, GOOD_SMS.replace("SMS_DAILY_BUDGET=0", "SMS_DAILY_BUDGET=50"))
    out = run(f'sms_env_warnings "{path}"')
    assert out.returncode == 0
    assert "SMS_DAILY_BUDGET" in out.stdout


@needs_bash
@pytest.mark.parametrize("budget", ["5x", "-5"])
def test_a_budget_that_is_not_a_whole_number_warns_and_the_run_goes_on(tmp_path, budget):
    """REQ-023 is warn-only, and daily_budget() in app/services/sms.py reads a
    value it cannot parse as 0, so this must not stop the install."""
    path = env_file(tmp_path, GOOD_SMS.replace("SMS_DAILY_BUDGET=0", f"SMS_DAILY_BUDGET={budget}"))
    out = run(f'sms_env_warnings "{path}"')
    assert out.returncode == 0, out.stderr
    assert "SMS_DAILY_BUDGET" in out.stdout and "not a whole number" in out.stdout
    assert "as 0" in out.stdout
    assert budget not in out.stdout + out.stderr


@needs_bash
def test_a_budget_line_it_cannot_read_still_stops(tmp_path):
    path = env_file(tmp_path, GOOD_SMS.replace("SMS_DAILY_BUDGET=0", 'SMS_DAILY_BUDGET="50'))
    out = run(f'sms_env_warnings "{path}"')
    assert out.returncode == 1
    assert "SMS_DAILY_BUDGET" in out.stderr


# ── REQ-012 / REQ-013: package versions ───────────────────────────────


@needs_bash
@pytest.mark.parametrize("deb, upstream", [
    ("2.45.3+ds-2ubuntu0.3", "2.45.3"),
    ("1.7.0-1ubuntu0.3", "1.7.0"),
    ("0.15.0-1", "0.15.0"),
    ("1:0.24.0-2", "0.24.0"),
])
def test_the_upstream_version_is_read_from_a_debian_version(deb, upstream):
    out = run(f"upstream_version '{deb}'")
    assert out.stdout == upstream + "\n"


@needs_bash
@pytest.mark.parametrize("candidate, ok", [
    ("2.45.3+ds-2ubuntu0.3", True),
    ("(none)", False),
    ("3.5.0+ds-1", False),
])
def test_apt_must_offer_prometheus_2_45(candidate, ok):
    policy = f"prometheus:\n  Installed: (none)\n  Candidate: {candidate}\n  Version table:\n"
    out = run(f"apt_candidate_is_2_45 <<'P'\n{policy}P")
    assert (out.returncode == 0) is ok


# ── REQ-032 / REQ-033 / REQ-078: rendering two installs ────────────────


@pytest.fixture
def installs_dir(tmp_path):
    folder = tmp_path / "installs"
    folder.mkdir()
    (folder / "inotex.conf").write_text("PORT=8001\nDOMAIN=chat.example.com\n")
    (folder / "elecomp.conf").write_text("PORT=8002\nDOMAIN=bot.example.org\n")
    return folder


@needs_bash
def test_the_scrape_file_for_two_installs_has_no_placeholder_left():
    for slug, port in (("inotex", "8001"), ("elecomp", "8002")):
        out = run(f"render_scrape_file {slug} {port}")
        assert out.returncode == 0, out.stderr
        assert "{{" not in out.stdout
        job = yaml.safe_load(out.stdout)["scrape_configs"][0]
        assert job["job_name"] == f"padyar-{slug}"
        assert job["authorization"] == {
            "type": "Bearer", "credentials_file": f"/etc/prometheus/secrets/{slug}.token"}
        assert job["static_configs"] == [{
            "targets": [f"127.0.0.1:{port}"], "labels": {"app": "padyar", "install": slug}}]
        assert "honor_labels" not in job


@needs_bash
def test_the_blackbox_modules_cover_every_registered_install(installs_dir):
    out = run(f'render_blackbox_config "{installs_dir}"')
    assert out.returncode == 0, out.stderr
    assert "{{" not in out.stdout
    modules = yaml.safe_load(out.stdout)["modules"]
    assert set(modules) == {"origin_inotex", "origin_elecomp"}
    module = modules["origin_inotex"]
    assert module["prober"] == "http" and module["timeout"] == "10s"
    assert module["http"]["valid_status_codes"] == [200]
    assert module["http"]["headers"] == {"Host": "chat.example.com"}
    assert module["http"]["tls_config"]["server_name"] == "chat.example.com"


@needs_bash
def test_the_origin_probe_job_has_no_install_label(installs_dir):
    out = run(f'render_blackbox_job "{installs_dir}"')
    assert out.returncode == 0, out.stderr
    assert "{{" not in out.stdout
    job = yaml.safe_load(out.stdout)["scrape_configs"][0]
    assert job["job_name"] == "blackbox-origin"
    assert job["metrics_path"] == "/probe"
    groups = sorted(job["static_configs"], key=lambda g: g["labels"]["probe_install"])
    assert groups == [
        {"targets": ["https://127.0.0.1:443/api/health"],
         "labels": {"domain": "bot.example.org", "probe_install": "elecomp"}},
        {"targets": ["https://127.0.0.1:443/api/health"],
         "labels": {"domain": "chat.example.com", "probe_install": "inotex"}},
    ], "host-level alerts must not carry an install label"


@needs_bash
def test_a_bad_install_record_stops_the_render(installs_dir):
    (installs_dir / "ghost.conf").write_text("PORT=80;rm\nDOMAIN=x.example.com\n")
    out = run(f'render_blackbox_job "{installs_dir}"')
    assert out.returncode == 1


# ── REQ-037: the Alertmanager password hashes ──────────────────────────


@pytest.fixture
def sudo_stub(tmp_path):
    """A sudo that drops `-u <user>` and runs the rest as the test user."""
    bindir = tmp_path / "sudo-bin"
    bindir.mkdir()
    stub = bindir / "sudo"
    stub.write_text('#!/usr/bin/env bash\n[[ "$1" == "-u" ]] && shift 2\nexec "$@"\n')
    stub.chmod(0o755)
    return bindir


@needs_bash
def test_a_rerun_keeps_a_matching_hash_and_a_new_password_gets_a_new_one(tmp_path, sudo_stub):
    pytest.importorskip("bcrypt")
    import sys

    password = tmp_path / "pass"
    password.write_text("synthetic-notreal-one")
    web = tmp_path / "web.yml"
    prefix = (f'HASH_SLUG=x; HASH_PY="{sys.executable}"; WEB_CONFIG="{web}"; ')
    env = {"PATH": f"{sudo_stub}:{os.environ['PATH']}"}

    first = run(prefix + f'bcrypt_hash prometheus "{password}"', env=env)
    assert first.returncode == 0, first.stderr
    hashed = first.stdout.strip()
    web.write_text(f"basic_auth_users:\n  prometheus: {hashed}\n")

    again = run(prefix + f'bcrypt_hash prometheus "{password}"', env=env)
    assert again.stdout.strip() == hashed, "an unchanged password must keep its hash"

    password.write_text("synthetic-notreal-two")
    changed = run(prefix + f'bcrypt_hash prometheus "{password}"', env=env)
    import bcrypt
    assert changed.stdout.strip() != hashed
    assert bcrypt.checkpw(b"synthetic-notreal-two", changed.stdout.strip().encode())
    assert "synthetic-notreal" not in first.stderr + again.stderr + changed.stderr


@needs_bash
def test_running_without_root_asks_for_sudo():
    if os.geteuid() == 0:
        pytest.skip("this test checks the non-root path")
    out = subprocess.run([BASH, str(SCRIPT), "inotex"], capture_output=True, text=True)
    assert out.returncode == 1
    assert out.stderr.startswith("Run with sudo: sudo bash ")


# ── REQ-019: a failed check puts every replaced file back ──────────────


@needs_bash
def test_files_written_through_a_pipe_are_rolled_back(tmp_path):
    """write_config is mostly the last command of a pipe. Without lastpipe it
    runs in a subshell, its record of what changed is lost, and a failed
    promtool check would leave the new files in place."""
    old = tmp_path / "prometheus.yml"
    old.write_text("old\n")
    new = tmp_path / "scrape.yml"
    backup = tmp_path / "backup"
    out = run(
        f'RUN_BACKUP="{backup}"; me=$(id -un); grp=$(id -gn)\n'
        f'printf "new\\n" | write_config "{old}" "$me" "$grp" 0644\n'
        f'printf "added\\n" | write_config "{new}" "$me" "$grp" 0644\n'
        f'[[ "$(cat "{old}")" == new && -f "{new}" ]] || exit 3\n'
        'restore_configs')
    assert out.returncode == 0, out.stderr
    assert old.read_text() == "old\n"
    assert not new.exists()


# ── SEC-001: a failed first run must not leave Debian defaults running ──
#
# apt-get starts every package's service right away with the Debian config:
# all interfaces, Alertmanager without auth, cluster gossip on 0.0.0.0:9094.
# UFW covers that for the seconds until the restart. If the run stops before
# the restart, the services this run installed are stopped and disabled;
# packages that were there before the run are left alone.

ALL_FIVE = ["prometheus", "prometheus-alertmanager", "prometheus-node-exporter",
            "prometheus-postgres-exporter", "prometheus-blackbox-exporter"]


@pytest.fixture
def host_stubs(tmp_path):
    """dpkg-query answers from <tmp>/installed; systemctl logs to <tmp>/systemctl."""
    bindir = tmp_path / "host-bin"
    bindir.mkdir()
    (bindir / "dpkg-query").write_text(
        "#!/usr/bin/env bash\n"
        f'if grep -qx "${{@: -1}}" "{tmp_path}/installed"; then printf "install ok installed"; '
        'else echo "no packages found" >&2; exit 1; fi\n')
    (bindir / "systemctl").write_text(
        f'#!/usr/bin/env bash\necho "$*" >> "{tmp_path}/systemctl"\n')
    for name in ("dpkg-query", "systemctl"):
        (bindir / name).chmod(0o755)
    (tmp_path / "installed").write_text("")
    return {"PATH": f"{bindir}:{os.environ['PATH']}"}


def _installed(tmp_path, packages):
    (tmp_path / "installed").write_text("".join(p + "\n" for p in packages))


def _systemctl_calls(tmp_path):
    log = tmp_path / "systemctl"
    return log.read_text().splitlines() if log.exists() else []


@needs_bash
def test_only_the_packages_that_are_missing_are_counted_as_new(tmp_path, host_stubs):
    _installed(tmp_path, ["prometheus", "prometheus-node-exporter"])
    out = run("packages_to_install " + " ".join(ALL_FIVE), env=host_stubs)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [
        "prometheus-alertmanager", "prometheus-postgres-exporter", "prometheus-blackbox-exporter"]


@needs_bash
def test_a_failed_run_stops_what_it_installed_and_leaves_the_rest_alone(tmp_path, host_stubs):
    _installed(tmp_path, ALL_FIVE)
    new = ["prometheus-alertmanager", "prometheus-postgres-exporter", "prometheus-blackbox-exporter"]
    out = run(f'NEW_PACKAGES=({" ".join(new)}); trap rollback_unless_accepted EXIT\n'
              'die "a check failed"', env=host_stubs)
    assert out.returncode == 1
    calls = _systemctl_calls(tmp_path)
    for unit in new:
        assert f"stop {unit}" in calls and f"disable {unit}" in calls
    for unit in ("prometheus", "prometheus-node-exporter"):
        assert not [c for c in calls if c.endswith(" " + unit)], f"{unit} was there before; it must not be touched"
    assert all(unit in out.stderr for unit in new)
    assert "until a run of this script succeeds" in out.stderr


@needs_bash
def test_a_package_apt_did_not_install_is_not_reported_as_stopped(tmp_path, host_stubs):
    _installed(tmp_path, ["prometheus"])
    out = run('NEW_PACKAGES=(prometheus prometheus-alertmanager); trap rollback_unless_accepted EXIT\n'
              "exit 1", env=host_stubs)
    assert out.returncode == 1
    assert _systemctl_calls(tmp_path) == ["stop prometheus", "disable prometheus"]
    assert "prometheus-alertmanager" not in out.stderr


@needs_bash
@pytest.mark.parametrize("ending", ["CONFIG_ACCEPTED=1; exit 1", "exit 0"])
def test_nothing_is_stopped_after_the_config_is_accepted_or_on_success(tmp_path, host_stubs, ending):
    _installed(tmp_path, ALL_FIVE)
    run("NEW_PACKAGES=(prometheus-alertmanager); trap rollback_unless_accepted EXIT\n" + ending,
        env=host_stubs)
    assert _systemctl_calls(tmp_path) == []


def test_the_new_packages_are_recorded_and_the_trap_set_before_apt_installs():
    text = SCRIPT.read_text(encoding="utf-8")
    install = text.index("apt-get install -y --no-install-recommends")
    assert -1 < text.find("NEW_PACKAGES") < install
    assert -1 < text.find("trap rollback_unless_accepted EXIT") < install


# ── SPEC section 6: exit 2 means a public listener, nothing else ───────


def test_exit_2_is_used_only_for_a_public_listener():
    lines = code_lines()
    twos = [i for i, line in enumerate(lines) if re.search(r"\bexit 2\b", line)]
    assert len(twos) == 1, [lines[i] for i in twos]
    assert "reachable beyond this host" in " ".join(lines[twos[0] - 3:twos[0]])
