"""The alert rules under deploy/monitoring/ (SPEC monitoring-stack REQ-070..075).

Prometheus does not reject a rule that names a metric nobody emits. It
loads it, evaluates it against nothing, and stays silent forever, so the
operator believes they are covered while the alert can never fire. The
same silence follows a label the metric does not carry
(`{outcome="failed"}` on `backup_outcome_total`, whose label is `result`).

So every name a rule reads must come from a real source:

  * the app's own registry (`app.services.metrics.registry`), with the
    sample suffixes its type really exports;
  * the allowlist of the exporter behind the selector's `job`, one file
    per exporter VERSION, each name linked to its source at that tag;
  * the few series Prometheus makes itself (`up`, `scrape_*`).

The PromQL reader is deploy/monitoring/promql_names.py, the same file the
install script runs on the host after the first scrape. Its controls below
prove it skips functions, keywords, label names and values, and durations,
so the coverage checks cannot pass by reading nothing.

promtool and amtool are not Python dependencies. The tests that need them
skip with a stated reason when the binary is missing or is not the version
the host runs (2.45 / 0.26). The CI job `monitoring-rules` installs the
Ubuntu noble `prometheus` package, so the promtool cases run there; the
amtool case runs where amtool 0.26 is on PATH, and on the host the install
script runs `amtool check-config` itself before any restart.
"""
import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
MONITORING = REPO / "deploy" / "monitoring"
RULES_FILE = MONITORING / "rules" / "padyar.rules.yml"
RULES_TEST_FILE = MONITORING / "tests" / "padyar_rules_test.yml"
ALLOWLISTS = MONITORING / "exporter-metrics"
SCRIPT = REPO / "deploy" / "55-monitoring.sh"

# R01-R10 and R14 of the SPEC: the alerts the ticket asks for.
REQUIRED_ALERTS = {
    "PadyarAppDown", "PadyarHigh5xxRate", "PadyarChatLatencyHigh",
    "PadyarAICircuitOpen", "PadyarBackupFailed", "PadyarBackupStale",
    "HostDiskLow", "HostPostgresDown", "HostCertExpiring", "HostTunnelDown",
    "HostOriginProbeFailed",
}

# A rule whose metric is not merged yet, or whose enabling change has not
# landed, stays OUT of the rules file and is listed here with what it waits
# for (SPEC 11.1). The change that enables it adds the rule and deletes its
# line here. Empty since WU6 added PadyarAICircuitOpen, PadyarBackupFailed
# and PadyarBackupStale.
WAITING = {}

# job label of a selector -> the allowlist that names its metrics.
JOB_SOURCES = {
    "node": "node_exporter",
    "postgres": "postgres_exporter",
    "blackbox-origin": "blackbox_exporter",
    "cloudflared": "cloudflared",
}

PROMETHEUS_OWN = {
    "up", "scrape_duration_seconds", "scrape_samples_scraped",
    "scrape_samples_post_metric_relabeling", "scrape_series_added",
}

# Labels Prometheus attaches to every app series from the scrape target.
TARGET_LABELS = {"app", "install", "job", "instance"}

# With honor_labels: false (the default), an app label that collides with a
# target label is renamed exported_<name> (SPEC REQ-034).
RENAMED = {"instance": "exported_instance"}


def _load_promql_names():
    spec = importlib.util.spec_from_file_location(
        "promql_names", MONITORING / "promql_names.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


promql = _load_promql_names()


# ── the sources of truth ───────────────────────────────────────────────


def app_series():
    """{series name: label names} for every series the app registry exports.

    Family names come from registry.collect(), the public API. Label names
    come from the collector objects, because a labelled family with no
    child yet (ai_circuit_state before any state change) has no samples to
    read them from.
    """
    from prometheus_client.metrics import MetricWrapperBase

    from app.services import metrics

    families = {family.name: family.type for family in metrics.registry.collect()}
    collectors = {c._name: c for c in vars(metrics).values()
                  if isinstance(c, MetricWrapperBase)}
    assert set(collectors) == set(families), "registry and module disagree"

    series = {}
    for name, kind in families.items():
        labels = set(collectors[name]._labelnames)
        if kind == "counter":
            series[name + "_total"] = labels
        elif kind == "histogram":
            series[name + "_bucket"] = labels | {"le"}
            series[name + "_sum"] = labels
            series[name + "_count"] = labels
        else:
            series[name] = labels
    return series


def script_exporter_versions():
    """{exporter: version} from the EXPORTER_PACKAGES table in the script."""
    text = SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"^EXPORTER_PACKAGES=\((.*?)^\)", text, re.S | re.M)
    assert block, "EXPORTER_PACKAGES table not found in deploy/55-monitoring.sh"
    rows = re.findall(r'"(\S+) (\S+) (\S+)"', block.group(1))
    assert rows, "EXPORTER_PACKAGES is empty"
    return {exporter: version for exporter, _package, version in rows}


def allowlists():
    """{exporter: (version, {metric name: source url})}."""
    out = {}
    for path in sorted(ALLOWLISTS.glob("*.txt")):
        match = re.fullmatch(r"(\w+)-(\d[\w.]*)\.txt", path.name)
        assert match, f"{path.name}: expected <exporter>-<version>.txt"
        exporter, version = match.groups()
        names = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, url = line.split()
            names[name] = url
        out[exporter] = (version, names)
    return out


def rules():
    document = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    return [rule for group in document["groups"] for rule in group["rules"]]


def unknown_names(expr, series, lists):
    """Every selector in expr whose name its source does not have."""
    problems = []
    for name, matchers in promql.selectors(expr):
        if name in PROMETHEUS_OWN:
            continue
        if matchers.get("app") == ("=", "padyar"):
            if name not in series:
                problems.append(f"{name}: not in the app registry")
            continue
        job = matchers.get("job")
        if job is None or job[0] != "=":
            problems.append(f"{name}: needs app=\"padyar\" or an exact job label")
            continue
        exporter = JOB_SOURCES.get(job[1])
        if exporter is None:
            problems.append(f"{name}: job {job[1]!r} has no allowlist")
        elif name not in lists.get(exporter, ("", {}))[1]:
            problems.append(f"{name}: not in the {exporter} allowlist")
    return problems


def unknown_labels(expr, series):
    """Labels a rule uses on an app metric that the metric does not carry."""
    problems = []
    app_labels = []
    found = promql.selectors(expr)
    for name, matchers in found:
        if matchers.get("app") != ("=", "padyar") or name not in series:
            continue
        own = {RENAMED.get(label, label) for label in series[name]}
        allowed = own | TARGET_LABELS
        app_labels.append(allowed)
        for label in matchers:
            if label not in allowed:
                problems.append(f"{name} has no label {label!r}")
    # Grouping labels are only attributable when every selector is an app one.
    if app_labels and len(app_labels) == len(found):
        union = set().union(*app_labels)
        for label in promql.grouping_labels(expr):
            if label not in union:
                problems.append(f"grouping label {label!r} is on no metric here")
    return problems


@pytest.fixture(scope="module")
def series():
    return app_series()


@pytest.fixture(scope="module")
def lists():
    return allowlists()


# ── REQ-075: the reader reads names, and only names ───────────────────


def test_functions_keywords_labels_and_durations_are_not_metric_names():
    expr = ('histogram_quantile(0.95, sum by (le, install) '
            '(rate(http_request_duration_seconds_bucket{app="padyar",route="/chat"}[10m]))) > 8 '
            'and on (install) max(up{job="node"}) or vector(1) unless time() < 3')
    assert promql.metric_names(expr) == {"http_request_duration_seconds_bucket", "up"}


def test_a_label_value_that_looks_like_a_metric_is_not_read_as_one():
    assert promql.metric_names('up{job="node_filesystem_size_bytes"} == 0') == {"up"}


def test_a_duration_suffix_and_an_offset_are_not_metric_names():
    assert promql.metric_names("rate(http_requests_total[5m] offset 1h)") == {"http_requests_total"}


def test_a_bare_selector_without_braces_is_still_read():
    assert promql.metric_names("health_score < 50") == {"health_score"}


def test_matchers_are_read_with_their_operator():
    found = promql.selectors('http_requests_total{app="padyar",status=~"5.."}')
    assert found == [("http_requests_total", {"app": ("=", "padyar"), "status": ("=~", "5..")})]


def test_grouping_labels_are_read_from_by_and_on():
    expr = "sum by (le, install) (x) and on (install) sum without (pid) (y)"
    assert promql.grouping_labels(expr) == {"le", "install", "pid"}


def test_label_replace_arguments_are_strings_not_metric_names():
    expr = 'label_replace(up{app="padyar"} == 0, "probe_install", "$1", "install", "(.*)")'
    assert promql.metric_names(expr) == {"up"}


# ── REQ-070: every name has a real source ──────────────────────────────


def test_the_app_families_are_read_from_the_registry(series):
    """If this breaks, every check below measures nothing."""
    assert {"http_requests_total", "http_request_duration_seconds_bucket",
            "http_request_duration_seconds_count", "ai_circuit_state",
            "backup_outcome_total", "health_score"} <= set(series)


def test_every_rule_names_only_metrics_its_source_has(series, lists):
    problems, read = [], set()
    for rule in rules():
        names = promql.metric_names(rule["expr"])
        read |= names
        if rule["alert"] != "MonitoringHeartbeat":
            assert names, f"{rule['alert']}: no metric name read from its expression"
        problems += [f"{rule['alert']}: {p}" for p in unknown_names(rule["expr"], series, lists)]
    assert len(read) >= 10, f"only {sorted(read)} read from the whole rules file"
    assert not problems, "\n".join(problems)


def test_a_misspelled_app_metric_is_rejected(series, lists):
    assert unknown_names('rate(http_requsets_total{app="padyar"}[5m])', series, lists)


def test_a_real_app_metric_is_accepted(series, lists):
    assert not unknown_names('rate(http_requests_total{app="padyar"}[5m])', series, lists)


def test_an_exporter_metric_under_the_wrong_job_is_rejected(series, lists):
    assert unknown_names('node_filesystem_avail_bytes{job="postgres"}', series, lists)


def test_an_exporter_metric_under_its_own_job_is_accepted(series, lists):
    assert not unknown_names('node_filesystem_avail_bytes{job="node"}', series, lists)


def test_a_selector_with_no_source_label_is_rejected(series, lists):
    assert unknown_names("node_filesystem_avail_bytes < 1", series, lists)


# ── REQ-071: the allowlists, and their versions ────────────────────────


def test_every_allowlist_line_links_to_the_source_at_its_own_version(lists):
    assert set(lists) == set(JOB_SOURCES.values())
    for exporter, (version, names) in lists.items():
        assert names, f"{exporter} allowlist is empty"
        for name, url in names.items():
            assert re.fullmatch(
                rf"https://github\.com/[\w.-]+/[\w.-]+/blob/v?{re.escape(version)}/\S+#L\d+(-L\d+)?",
                url), f"{exporter} {name}: {url} is not a source link at tag {version}"


def test_allowlist_versions_match_the_versions_the_script_installs(lists):
    pinned = script_exporter_versions()
    assert {e: v for e, (v, _) in lists.items()} == pinned, (
        "an exporter version changed in deploy/55-monitoring.sh without a new "
        "allowlist (or the other way round)")


# ── REQ-073: label names ───────────────────────────────────────────────


def test_every_label_a_rule_uses_on_an_app_metric_exists(series):
    problems = []
    for rule in rules():
        problems += [f"{rule['alert']}: {p}" for p in unknown_labels(rule["expr"], series)]
    assert not problems, "\n".join(problems)


def test_a_label_the_metric_does_not_have_is_rejected(series):
    expr = 'sum(increase(backup_outcome_total{app="padyar",outcome="failed"}[26h])) > 0'
    assert unknown_labels(expr, series) == ["backup_outcome_total has no label 'outcome'"]


def test_the_real_label_is_accepted(series):
    expr = 'sum by (install) (increase(backup_outcome_total{app="padyar",result="failed"}[26h])) > 0'
    assert not unknown_labels(expr, series)


def test_the_app_instance_label_is_read_as_exported_instance(series):
    ok = 'max by (install, exported_instance) (ai_circuit_state{app="padyar"}) == 2'
    assert not unknown_labels(ok, series)
    assert unknown_labels('max by (provider) (ai_circuit_state{app="padyar"}) == 2', series)


# ── REQ-074: the eleven required alerts ────────────────────────────────


def test_each_required_alert_is_in_the_rules_file_or_waiting_with_its_reason():
    present = {rule["alert"] for rule in rules()}
    both = sorted(present & set(WAITING))
    neither = sorted(REQUIRED_ALERTS - present - set(WAITING))
    assert not both, f"in the rules file AND waiting: {both}"
    assert not neither, f"neither in the rules file nor waiting: {neither}"
    for name, reason in WAITING.items():
        assert name in REQUIRED_ALERTS
        assert re.match(r"DEP-\d", reason), f"{name}: the reason must name its DEP"


def test_nothing_is_waiting_and_every_required_alert_is_in_the_rules_file():
    """WU6 enabled the last three (SPEC Work breakdown). The ticket's alerts
    are all real rules now, none is only a promise in a list."""
    assert WAITING == {}
    present = {rule["alert"] for rule in rules()}
    assert REQUIRED_ALERTS <= present, f"missing: {sorted(REQUIRED_ALERTS - present)}"


# ── SPEC 5.3: the label rule and the annotations ───────────────────────


def test_critical_means_sms_except_app_down():
    """severity="critical" iff page="sms". PadyarAppDown is critical without
    page: the watchdog's own probe already texts "the app is down"."""
    for rule in rules():
        labels = rule.get("labels", {})
        critical = labels.get("severity") == "critical"
        sms = labels.get("page") == "sms"
        if rule["alert"] == "PadyarAppDown":
            assert critical and "page" not in labels
        else:
            assert critical == sms, f"{rule['alert']}: severity and page disagree"


def test_every_rule_has_a_summary_a_description_and_a_runbook_section():
    for rule in rules():
        annotations = rule.get("annotations", {})
        for key in ("summary", "description", "runbook"):
            assert annotations.get(key), f"{rule['alert']} has no {key}"
        assert annotations["runbook"].endswith("#" + rule["alert"].lower())


def test_the_heartbeat_never_texts_anyone():
    heartbeat = [r for r in rules() if r["alert"] == "MonitoringHeartbeat"]
    assert len(heartbeat) == 1
    assert heartbeat[0]["expr"].strip() == "vector(1)"
    assert "page" not in heartbeat[0]["labels"]


# ── REQ-072: promtool and amtool, when the host's version is here ─────


WHERE = {
    "promtool": "the CI job monitoring-rules installs it (Ubuntu package prometheus)",
    "amtool": "it ships with the Ubuntu package prometheus-alertmanager, which no CI job installs",
}


def _tool(name, version_prefix):
    path = shutil.which(name)
    if not path:
        pytest.skip(f"{name} is not on PATH; {WHERE[name]}")
    out = subprocess.run([path, "--version"], capture_output=True, text=True)
    text = out.stdout + out.stderr
    match = re.search(r"version (\S+)", text)
    found = match.group(1) if match else "unknown"
    if not found.startswith(version_prefix):
        pytest.skip(f"{name} {found} is not the host's version ({version_prefix}x); "
                    "a pass on another version proves nothing about the host")
    return path


def _render(tmp_path):
    """The host layout under tmp_path: prometheus.yml with its /etc paths
    moved, rules, two rendered installs, the blackbox job, and secrets."""
    root = tmp_path / "etc-prometheus"
    for sub in ("rules", "scrape.d", "secrets"):
        (root / sub).mkdir(parents=True)
    text = (MONITORING / "prometheus.yml").read_text(encoding="utf-8")
    (root / "prometheus.yml").write_text(text.replace("/etc/prometheus/", f"{root}/"))
    shutil.copy(RULES_FILE, root / "rules" / "padyar.rules.yml")
    for name in ("inotex.token", "elecomp.token", "alertmanager-prometheus.pass"):
        (root / "secrets" / name).write_text("synthetic-notreal")
    for slug, port in (("inotex", "8001"), ("elecomp", "8002")):
        rendered = (MONITORING / "templates" / "scrape-install.yml.template").read_text(
            encoding="utf-8").replace("{{SLUG}}", slug).replace("{{PORT}}", port)
        (root / "scrape.d" / f"padyar-{slug}.yml").write_text(
            rendered.replace("/etc/prometheus/", f"{root}/"))
    job = (MONITORING / "templates" / "blackbox-origin.yml.template").read_text(encoding="utf-8")
    target = (MONITORING / "templates" / "blackbox-origin-target.yml.template").read_text(encoding="utf-8")
    for slug, domain in (("inotex", "chat.example.com"), ("elecomp", "bot.example.org")):
        job += target.replace("{{SLUG}}", slug).replace("{{DOMAIN}}", domain)
    (root / "scrape.d" / "blackbox-origin.yml").write_text(job)
    return root


def test_promtool_check_rules_accepts_the_rules_file():
    promtool = _tool("promtool", "2.45.")
    out = subprocess.run([promtool, "check", "rules", str(RULES_FILE)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_promtool_test_rules_passes_every_on_and_off_case():
    promtool = _tool("promtool", "2.45.")
    out = subprocess.run([promtool, "test", "rules", str(RULES_TEST_FILE)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_rule_has_an_on_case_and_all_but_the_heartbeat_an_off_case():
    """SC-010. MonitoringHeartbeat is vector(1): it can never be off, so it
    has an on case only."""
    document = yaml.safe_load(RULES_TEST_FILE.read_text(encoding="utf-8"))
    on, off = set(), set()
    for group in document["tests"]:
        for case in group.get("alert_rule_test", []):
            (on if case.get("exp_alerts") else off).add(case["alertname"])
    names = {rule["alert"] for rule in rules()}
    assert names <= on, f"no on case for {sorted(names - on)}"
    assert names - {"MonitoringHeartbeat"} <= off, \
        f"no off case for {sorted(names - {'MonitoringHeartbeat'} - off)}"


def test_promtool_check_config_accepts_prometheus_yml_with_two_installs(tmp_path):
    promtool = _tool("promtool", "2.45.")
    root = _render(tmp_path)
    out = subprocess.run([promtool, "check", "config", str(root / "prometheus.yml")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_amtool_check_config_accepts_alertmanager_yml():
    amtool = _tool("amtool", "0.26.")
    out = subprocess.run([amtool, "check-config", str(MONITORING / "alertmanager.yml")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# ── REQ-035 / REQ-036: Alertmanager sends nothing anywhere ─────────────


def test_alertmanager_has_one_route_and_a_receiver_with_no_destination():
    config = yaml.safe_load((MONITORING / "alertmanager.yml").read_text(encoding="utf-8"))
    assert config["route"]["receiver"] == "ui-only"
    assert config["route"]["group_by"] == ["alertname", "install"]
    assert config["receivers"] == [{"name": "ui-only"}]
    assert "routes" not in config["route"]


def test_inhibit_rules_match_the_spec():
    config = yaml.safe_load((MONITORING / "alertmanager.yml").read_text(encoding="utf-8"))
    app_down, db_down = config["inhibit_rules"]
    assert app_down["source_matchers"] == ['alertname="PadyarAppDown"']
    assert app_down["equal"] == ["install"]
    assert db_down["source_matchers"] == ['alertname="HostPostgresDown"']
    assert "equal" not in db_down, "a database outage must inhibit every install"
