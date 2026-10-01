"""Metric and label names read out of a PromQL expression.

One reader for two callers, so they cannot drift apart:

  * tests/test_monitoring_rules.py checks every alert rule against the app
    registry and the exporter allowlists (SPEC monitoring-stack REQ-070);
  * deploy/55-monitoring.sh runs this file as a script on the host, after
    the first scrape, to list rule metrics that have no series yet
    (REQ-021).

It is a regex reader, not a PromQL parser. It removes what is not a metric
name before it looks for names: string literals, `[...]` ranges and
subqueries, `by (...)` / `on (...)` style label lists, and `{...}` label
matchers. A name followed by `(` is a function, and PromQL keywords are
skipped. The control tests in tests/test_monitoring_rules.py (REQ-075)
prove each of those, so a coverage check cannot pass by reading nothing.

Standard library only: the host runs it with the system python3.
"""
import json
import re
import sys

KEYWORDS = {
    "by", "without", "on", "ignoring", "group_left", "group_right",
    "offset", "bool", "and", "or", "unless", "inf", "nan", "atan2",
}

_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`')
_RANGE = re.compile(r"\[[^\[\]]*\]")
_GROUPING = re.compile(
    r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\(([^()]*)\)")
_BRACES = re.compile(r"(?<![\w:.])([A-Za-z_:][\w:]*)?\s*\{([^{}]*)\}")
_NAME = re.compile(r"(?<![\w:.])[A-Za-z_:][\w:]*")
_MATCHER = re.compile(r"\s*([A-Za-z_]\w*)\s*(=~|!~|!=|=)\s*\x00(\d+)\x00\s*")


def _mask(expr):
    """Replace every string literal with \\x00<n>\\x00; return text, literals."""
    literals = []

    def keep(match):
        literals.append(match.group(0)[1:-1])
        return "\x00%d\x00" % (len(literals) - 1)

    return _STRING.sub(keep, expr), literals


def _matchers(body, literals):
    found = {}
    for part in body.split(","):
        if not part.strip():
            continue
        match = _MATCHER.fullmatch(part)
        if not match:
            raise ValueError("cannot read label matcher %r" % part.strip())
        label, op, index = match.groups()
        found[label] = (op, literals[int(index)])
    return found


def selectors(expr):
    """[(metric name, {label: (operator, value)})] in the order they appear."""
    text, literals = _mask(expr)
    text = _RANGE.sub(" ", text)
    text = _GROUPING.sub(" ", text)
    found = []

    def take(match):
        name, body = match.group(1), match.group(2)
        matchers = _matchers(body, literals)
        if name is None:
            op_value = matchers.pop("__name__", None)
            if op_value is None or op_value[0] != "=":
                raise ValueError("selector without a metric name: {%s}" % body)
            name = op_value[1]
        found.append((match.start(), name, matchers))
        return " " * len(match.group(0))

    text = _BRACES.sub(take, text)
    for match in _NAME.finditer(text):
        token = match.group(0)
        if token.lower() in KEYWORDS:
            continue
        if text[match.end():].lstrip().startswith("("):
            continue  # a function call, not a series
        found.append((match.start(), token, {}))
    return [(name, matchers) for _, name, matchers in sorted(found, key=lambda f: f[0])]


def metric_names(expr):
    """The set of series names an expression reads."""
    return {name for name, _ in selectors(expr)}


def grouping_labels(expr):
    """Every label named in a by/without/on/ignoring/group_* list."""
    text, _ = _mask(expr)
    labels = set()
    for match in _GROUPING.finditer(text):
        labels |= {label.strip() for label in match.group(1).split(",") if label.strip()}
    return labels


def missing_series(rules_json, names_json):
    """Rule metric names that Prometheus holds no series for.

    rules_json is GET /api/v1/rules, names_json is
    GET /api/v1/label/__name__/values.
    """
    have = set(names_json["data"])
    wanted = set()
    for group in rules_json["data"]["groups"]:
        for rule in group["rules"]:
            wanted |= metric_names(rule["query"])
    return sorted(wanted - have)


def main(argv):
    if len(argv) != 3:
        print("usage: promql_names.py RULES_JSON NAMES_JSON", file=sys.stderr)
        return 1
    with open(argv[1], encoding="utf-8") as rules_file, \
            open(argv[2], encoding="utf-8") as names_file:
        for name in missing_series(json.load(rules_file), json.load(names_file)):
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
