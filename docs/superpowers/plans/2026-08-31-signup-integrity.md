# Signup Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the server own the visitor signup flow — no message reaches `/chat` before the profile is complete, and no invalid/inconsistent profile value is ever persisted.

**Architecture:** Stateless server-driven flow (spec §3): the `visitors` row IS the state; "complete" is derived from it by one function. Two new endpoints (`GET /api/signup/next`, `POST /api/signup/answer`) in the registration module collect and validate answers one at a time against the taxonomy. `/chat` returns `403 signup_incomplete` for signed-in-but-incomplete visitors; `/api/auth/profile` refuses writes until complete. The frontend becomes a thin renderer of server-owned steps.

**Tech Stack:** FastAPI + Pydantic (existing patterns in `app/routers/otp.py`), vanilla ES5 JS (`static/companion/registration.js`, `static/chat/core.js`), pytest + TestClient.

**Spec:** `docs/features/signup-integrity/SPEC.md` — the plan argues from the spec; read §4 (API contract), §5 (validation rules), §6 (enforcement) before each task.

## Global Constraints

- Persian UI strings verbatim as given in this plan (they are product copy).
- SQL uses `?` placeholders with `datetime('now')` inline (app/db/pg.py translates) — same idiom as `app/routers/otp.py:225-231`.
- Repo test rule (AGENTS.md): run single test files locally while developing, but the pass/fail signal is CI (`gh run watch`). Run `python -m py_compile <file>` on every touched Python file before committing.
- Comments/docstrings in code are English, explaining WHY (repo style).
- No new dependencies. No new tables/columns.
- Fail-open rules (spec §3): empty taxonomy lists ⇒ only non-empty checks apply; storage faults must never lock a verified visitor out.

---

### Task 1: Taxonomy learns `no_position_jobs`

**Files:**
- Modify: `app/services/taxonomy.py` (`_validate`, `_MINIMUM`, `form_options`)
- Modify: `data/visit-taxonomy.json`
- Modify: `app/routers/otp.py:637-672` (`_rows_the_loader_would_drop`)
- Test: `tests/test_taxonomy.py`, `tests/test_taxonomy_admin.py`

**Interfaces:**
- Produces: `document()["no_position_jobs"]` → `list[str]` of job ids; `form_options(lang)` gains keys `no_position_jobs` (list of job LABELS in that lang) and `no_position_label` (label of position id `"none"`, `""` if absent). Later tasks rely on exactly these names.

- [ ] **Step 1: Write the failing tests**

In `tests/test_taxonomy.py` (it has fixtures building docs as dicts — follow its existing `GOOD`-style construction; if `GOOD` is a dict, copy-and-extend it):

```python
def test_no_position_jobs_is_normalised_to_known_job_ids():
    """Unknown ids are dropped by the loader, so the rule can never point at
    a job the form does not offer."""
    from app.services import taxonomy
    doc = taxonomy._validate(_DOC_WITH(
        no_position_jobs=["school-student", "ghost-job"]))
    assert doc["no_position_jobs"] == ["school-student"]


def test_form_options_exposes_the_rule_in_labels():
    from app.services import taxonomy
    taxonomy._doc = taxonomy._validate(_DOC_WITH(no_position_jobs=["school-student"]))
    try:
        opts = taxonomy.form_options("fa")
        assert "دانش‌آموز" in opts["no_position_jobs"]
        assert opts["no_position_label"] == "سمت سازمانی ندارم"
        en = taxonomy.form_options("en")
        assert en["no_position_label"] == "No organisational title"
    finally:
        taxonomy._doc = taxonomy._MINIMUM
        taxonomy._mtime = -1.0
```

Where `_DOC_WITH` is a helper added to the test file building the minimal valid document (copy the shape used by the file's other tests; it must contain jobs `school-student`/`دانش‌آموز`/`School student`, a positions list containing `{"id": "none", "fa": "سمت سازمانی ندارم", "en": "No organisational title"}`, and at least one section with keywords — `_validate` refuses a doc with no sections). Note `_doc`/`_mtime` are module-private; if the file's existing tests already swap documents another way (a `VISIT_TAXONOMY_PATH` tmp file via `monkeypatch`), use THAT way instead of assigning privates — consistency with the file wins.

In `tests/test_taxonomy_admin.py` (its `GOOD` doc + `_text()` helper exist at the top):

```python
def test_save_rejects_no_position_jobs_ids_not_in_jobs():
    bad = _text(dict(GOOD, no_position_jobs=["not-a-job"]))
    res = _save(bad)   # the file's existing save helper; adjust name if different
    assert res.status_code == 400
    assert "not-a-job" in res.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_taxonomy.py tests/test_taxonomy_admin.py -k no_position -v`
Expected: FAIL (`KeyError: 'no_position_jobs'` / 200 instead of 400).

- [ ] **Step 3: Implement**

`app/services/taxonomy.py` — three edits:

(a) `_MINIMUM` gains `"no_position_jobs": [],` after `"fallback_ids": [],`.

(b) `_validate`: before the `return`, compute the cleaned lists once and build the new key:

```python
    jobs = _clean_items(raw.get("jobs"), required=("id", "fa"))
    positions = _clean_items(raw.get("positions"), required=("id", "fa"))
    job_ids = {j["id"] for j in jobs}
    no_position = [i for i in raw.get("no_position_jobs", [])
                   if isinstance(i, str) and i in job_ids]
```

then in the returned dict replace the inline `jobs`/`positions` expressions with the precomputed ones and add:

```python
        "no_position_jobs": no_position,
```

(c) `form_options` — after the `localise` def, add:

```python
    def label_of(item):
        return (item.get("fa") if lang == "fa" else item.get("en")) or item.get("fa", "")

    job_labels = {j["id"]: label_of(j) for j in doc["jobs"]}
    no_position_label = ""
    for p in doc.get("positions", []):
        if p.get("id") == "none":
            no_position_label = label_of(p)
```

and in the returned dict add:

```python
        "no_position_jobs": [job_labels[i] for i in doc.get("no_position_jobs", [])
                             if i in job_labels],
        "no_position_label": no_position_label,
```

`data/visit-taxonomy.json` — add after the `"positions"` list (and one readme line under the `positions` entry in `_readme`):

```json
  "no_position_jobs": ["school-student", "university-student", "jobseeker"],
```

readme line: `"  no_position_jobs [job-id,...]  -> jobs whose only valid سمت is the"`, `"     position with id \"none\" (auto-answered, never asked)"`.

`app/routers/otp.py` `_rows_the_loader_would_drop` — append before `return problems`:

```python
    raw_np = doc.get("no_position_jobs")
    if raw_np is not None:
        if not isinstance(raw_np, list):
            problems.append("«شغل‌های بدون سمت» باید یک فهرست باشد.")
        else:
            job_ids = {str(r.get("id", "")).strip() for r in (doc.get("jobs") or [])
                       if isinstance(r, dict)}
            for jid in raw_np:
                if jid not in job_ids:
                    problems.append(
                        f"«no_position_jobs»: شناسهٔ «{jid}» در فهرست شغل‌ها نیست.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_taxonomy.py tests/test_taxonomy_admin.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add app/services/taxonomy.py data/visit-taxonomy.json app/routers/otp.py tests/test_taxonomy.py tests/test_taxonomy_admin.py
git commit -m "feat(taxonomy): no_position_jobs rule, exposed in form_options"
```

---

### Task 2: The signup service (`app/services/signup.py`)

**Files:**
- Create: `app/services/signup.py`
- Test: `tests/test_signup_service.py` (new)

**Interfaces:**
- Consumes: `taxonomy.document()` / `taxonomy.form_options()`, `conversations.get_visitor()` (returns dict with `first_name, last_name, job, position, interests` or None).
- Produces (later tasks import exactly these):
  - `INCOMPLETE_CODE = "signup_incomplete"`, `WRONG_STEP_CODE = "wrong_step"`, `INVALID_CODE = "invalid_answer"`
  - `is_complete(row: dict) -> bool`
  - `pending_step(row: dict, lang: str = "fa") -> dict` — `{"complete": True}` or `{"step": {"key", "prompt", "options", "multi"}}`
  - `validate_answer(row: dict, key: str, value: str) -> (bool, str, dict)` — `(ok, persian_message, fields_to_write)`
  - `validate_profile_edit(job: str, position: str, interests: str) -> str` — `""` or a Persian problem sentence
  - `sanitize_registration(record: dict) -> dict` — drops invalid carried fields
  - `write_fields(visitor_id: str, fields: dict) -> bool`
  - `visitor_complete(visitor_id: str) -> bool` — fail-open

- [ ] **Step 1: Write the failing tests**

`tests/test_signup_service.py`:

```python
"""Unit level: the rules the signup flow enforces, against the real
taxonomy file. The API contract around them is tests/test_signup_flow.py."""
import pytest

from app.services import signup


@pytest.fixture()
def row():
    return {"first_name": "زهرا", "last_name": "کریمی", "job": "خبرنگار / رسانه",
            "position": "کارشناس", "interests": "هوش مصنوعی"}


def test_a_complete_valid_row_is_complete(row):
    assert signup.is_complete(row)


def test_missing_field_is_incomplete(row):
    assert not signup.is_complete({**row, "job": ""})


def test_stale_label_is_incomplete(row):
    """Admin renamed the label: the visitor re-answers one question."""
    assert not signup.is_complete({**row, "job": "ژورنالیست"})


def test_student_with_a_title_is_incomplete(row):
    assert not signup.is_complete({**row, "job": "دانش‌آموز"})


def test_student_with_no_title_is_complete(row):
    assert signup.is_complete({**row, "job": "دانش‌آموز",
                               "position": "سمت سازمانی ندارم"})


def test_pending_step_order_and_skip(row):
    """name/job/position filled ⇒ interests asked; the no-position job
    offers exactly one chip."""
    p = signup.pending_step({**row, "interests": ""}, "fa")
    assert p["step"]["key"] == "interests" and p["step"]["multi"] is True
    q = signup.pending_step({"first_name": "", "last_name": "", "job": "دانش‌آموز",
                             "position": "", "interests": ""}, "fa")
    assert q["step"]["key"] == "name"
    p2 = signup.pending_step({"first_name": "آ", "last_name": "", "job": "دانش‌آموز",
                              "position": "", "interests": ""}, "fa")
    assert p2["step"]["key"] == "position"
    assert [o["label"] for o in p2["step"]["options"]] == ["سمت سازمانی ندارم"]


def test_validate_answer_accepts_list_labels_only():
    ok, msg, fields = signup.validate_answer({}, "job", "دانش‌آموز")
    assert ok and fields["job"] == "دانش‌آموز"
    assert fields["position"] == "سمت سازمانی ندارم"   # auto-written
    ok, msg, _ = signup.validate_answer({}, "job", "فضانورد")
    assert not ok and msg
    ok, msg, fields = signup.validate_answer(
        {"job": "دانش‌آموز"}, "position", "کارشناس")
    assert not ok and msg
    ok, msg, fields = signup.validate_answer({}, "interests", "هوش مصنوعی، فضانورد")
    assert not ok and "فضانورد" in msg
    ok, msg, fields = signup.validate_answer({}, "interests",
                                             "هوش مصنوعی، به آموزش و یادگیری هوش مصنوعی علاقه دارم")
    assert ok   # flags are valid interest items


def test_validate_answer_name_splits_and_caps():
    ok, msg, fields = signup.validate_answer({}, "name", "  زهرا   کریمی نژاد  ")
    assert ok and fields == {"first_name": "زهرا", "last_name": "کریمی نژاد"}


def test_validate_profile_edit():
    assert signup.validate_profile_edit("دانش‌آموز", "کارشناس", "هوش مصنوعی")
    assert not signup.validate_profile_edit("دانش‌آموز", "سمت سازمانی ندارم", "هوش مصنوعی")
    assert signup.validate_profile_edit("خبرنگار / رسانه", "کارشناس", "فضانورد")


def test_sanitize_registration_drops_invalid_keeps_valid():
    out = signup.sanitize_registration({
        "job": "دانش‌آموز", "position": "کارشناس", "interests": "هوش مصنوعی، فضانورد"})
    assert out["job"] == "دانش‌آموز"
    assert out["position"] == ""      # inconsistent with the job ⇒ dropped
    assert out["interests"] == "هوش مصنوعی"


def test_fail_open_without_taxonomy(monkeypatch):
    """An install with no taxonomy file must not lock anybody out."""
    from app.services import taxonomy
    monkeypatch.setattr(taxonomy, "document",
                        lambda: dict(taxonomy._MINIMUM))
    assert signup.is_complete({"first_name": "آ", "job": "هر چیزی",
                               "position": "هر چیزی", "interests": "هر چیزی"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signup_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.signup'`.

- [ ] **Step 3: Implement**

Create `app/services/signup.py` (complete file):

```python
"""The server-owned half of visitor sign-up: what is still missing, which
answers are acceptable, and how one field reaches the `visitors` row.

SPEC: docs/features/signup-integrity/SPEC.md — §3 (the row IS the state),
§5 (validation), §6 (enforcement). The frontend mirrors these rules for
UX only; every decision here is final.

Three consumers share ONE definition of "complete" (is_complete): /chat's
403 gate, the /api/signup/* endpoints, and /api/auth/profile. Three
private copies of that rule is the bug class this file exists to prevent.
"""
import re

from app.config import logger

INCOMPLETE_CODE = "signup_incomplete"
WRONG_STEP_CODE = "wrong_step"
INVALID_CODE = "invalid_answer"

ORDER = ("name", "job", "position", "interests")
_LIST_KEY = {"job": "jobs", "position": "positions", "interests": "interests"}
_CAPS = {"first_name": 60, "last_name": 60, "job": 80,
         "position": 80, "interests": 400}

PROMPTS = {
    "fa": {
        "name": "نام و نام خانوادگی شما چیست؟",
        "job": "شغل یا حوزهٔ فعالیت شما چیست؟",
        "position": "سمت شما چیست؟",
        "interests": "به کدام زمینه‌ها علاقه دارید؟",
    },
    "en": {
        "name": "What is your full name?",
        "job": "What is your field of work?",
        "position": "What is your job title?",
        "interests": "Which topics do you care about?",
    },
}


def _norm(value) -> str:
    return str(value or "").strip()


def _split(value) -> list:
    return [p.strip() for p in re.split(r"[،,]", str(value or "")) if p.strip()]


def _doc():
    from app.services import taxonomy
    return taxonomy.document()


def _label_ids(items) -> dict:
    """label (fa and en, casefolded) -> id. Persian has no case; English
    labels must match regardless of the visitor's keyboard."""
    out = {}
    for item in items:
        for v in (item.get("fa"), item.get("en")):
            v = _norm(v)
            if v:
                out[v.casefold()] = item["id"]
    return out


def _none_position_label(doc=None) -> str:
    doc = doc or _doc()
    for p in doc.get("positions", []):
        if p.get("id") == "none":
            return _norm(p.get("fa"))
    return ""


def _is_no_position_job(job_label: str, doc=None) -> bool:
    doc = doc or _doc()
    jid = _label_ids(doc["jobs"]).get(_norm(job_label).casefold(), "")
    return bool(jid) and jid in doc.get("no_position_jobs", [])


# ── The one definition of "complete" ──────────────────────────────────────

def is_complete(row: dict) -> bool:
    """All four answers present AND valid per the CURRENT taxonomy.

    Fail-open: with no taxonomy lists (missing file / builtin minimum) only
    presence is checked, so an unconfigured install never locks its
    visitors out of the chat."""
    if not (_norm(row.get("first_name")) or _norm(row.get("last_name"))):
        return False
    job = _norm(row.get("job"))
    position = _norm(row.get("position"))
    interests = _norm(row.get("interests"))
    if not (job and position and interests):
        return False
    doc = _doc()
    if doc["jobs"] and job.casefold() not in _label_ids(doc["jobs"]):
        return False
    positions = doc.get("positions", [])
    if positions:
        if position.casefold() not in _label_ids(positions):
            return False
        none = _none_position_label(doc)
        if _is_no_position_job(job, doc) and none and position != none:
            return False
    if doc["interests"] or doc["flags"]:
        valid = (set(_label_ids(doc["interests"]))
                 | set(_label_ids(doc["flags"])))
        if any(i.casefold() not in valid for i in _split(interests)):
            return False
    return True


def _field_done(row: dict, key: str) -> bool:
    if key == "name":
        return bool(_norm(row.get("first_name")) or _norm(row.get("last_name")))
    return bool(_norm(row.get(key)))


def pending_step(row: dict, lang: str = "fa") -> dict:
    """The next question the visitor owes, with its options, or complete."""
    from app.services import taxonomy
    lang = "en" if str(lang).lower().startswith("en") else "fa"
    if is_complete(row):
        return {"complete": True}
    opts = taxonomy.form_options(lang)
    for key in ORDER:
        if _field_done(row, key):
            continue
        step = {"key": key, "multi": key == "interests",
                "prompt": PROMPTS[lang][key]}
        if key == "position" and _is_no_position_job(_norm(row.get("job"))):
            # One chip: the only answer the validator accepts for this job.
            none = _none_position_label()
            step["options"] = ([{"id": "none", "label": none}] if none else [])
        elif key != "name":
            step["options"] = opts.get(_LIST_KEY[key], [])
        else:
            step["options"] = []
        return {"step": step}
    return {"complete": True}


# ── Per-field rules, shared by the flow and the edit endpoint ────────────

def _check_job(job: str, doc) -> str:
    if doc["jobs"] and _norm(job).casefold() not in _label_ids(doc["jobs"]):
        return "شغل انتخابی در فهرست نیست — از گزینه‌های بالا انتخاب کنید."
    return ""


def _check_position(position: str, job: str, doc) -> str:
    positions = doc.get("positions", [])
    if positions and _norm(position).casefold() not in _label_ids(positions):
        return "سمت انتخابی در فهرست نیست — از گزینه‌های بالا انتخاب کنید."
    none = _none_position_label(doc)
    if none and _is_no_position_job(job, doc) and _norm(position) != none:
        return "برای این شغل سمت سازمانی ندارید — گزینهٔ «سمت سازمانی ندارم» را انتخاب کنید."
    return ""


def _check_interests(value: str, doc) -> str:
    if doc["interests"] or doc["flags"]:
        valid = (set(_label_ids(doc["interests"]))
                 | set(_label_ids(doc["flags"])))
        bad = [i for i in _split(value) if i.casefold() not in valid]
        if bad:
            return f"«{bad[0]}» در فهرست نیست — از گزینه‌های بالا انتخاب کنید."
    return ""


def validate_answer(row: dict, key: str, value: str):
    """One answer against the taxonomy. Returns (ok, message, fields)."""
    doc = _doc()
    value = _norm(value)
    if key == "name":
        full = " ".join(value.split())[:120]
        if not full:
            return False, "نام را بنویسید و دکمهٔ ارسال را بزنید.", {}
        parts = full.split(" ")
        return True, "", {"first_name": parts[0], "last_name": " ".join(parts[1:])}
    if key == "job":
        job = value[:_CAPS["job"]]
        problem = _check_job(job, doc)
        if problem:
            return False, problem, {}
        fields = {"job": job}
        if _is_no_position_job(job, doc):
            none = _none_position_label(doc)
            if none:
                # Resolves the سمت question in the same write; the flow
                # then never asks it (spec REQ-009).
                fields["position"] = none
        return True, "", fields
    if key == "position":
        position = value[:_CAPS["position"]]
        problem = _check_position(position, _norm(row.get("job")), doc)
        if problem:
            return False, problem, {}
        return True, "", {"position": position}
    if key == "interests":
        items = _split(value)
        problem = _check_interests("، ".join(items), doc)
        if problem:
            return False, problem, {}
        joined = "، ".join(items)[:_CAPS["interests"]]
        if not joined:
            return False, "حداقل یک زمینه را انتخاب کنید.", {}
        return True, "", {"interests": joined}
    return False, "پرسش نامعتبر است.", {}


def validate_profile_edit(job: str, position: str, interests: str) -> str:
    """The edit endpoint's trio, as a unit: position pairs with the NEW job."""
    doc = _doc()
    return (_check_job(job, doc)
            or _check_position(position, job, doc)
            or _check_interests(interests, doc))


def sanitize_registration(record: dict) -> dict:
    """Filter a challenge's carried fields through the same rules.

    A visitor who just proved their phone must never be told the verify
    failed over a job string — invalid values are DROPPED here, leaving
    the field empty so the signup flow asks it properly."""
    doc = _doc()
    out = dict(record)
    job = _norm(record.get("job"))
    if job and _check_job(job, doc):
        out["job"] = ""
        job = ""
    if _norm(record.get("position")) and _check_position(
            _norm(record.get("position")), job, doc):
        out["position"] = ""
    interests = _norm(record.get("interests"))
    if interests and _check_interests(interests, doc):
        out["interests"] = ""
    elif interests:
        out["interests"] = "، ".join(_split(interests))[:_CAPS["interests"]]
    return out


# ── Writes ────────────────────────────────────────────────────────────────

def write_fields(visitor_id: str, fields: dict) -> bool:
    """Write the given profile columns for ONE visitor row, by id only."""
    from app.db.connection import get_db_connection
    sets, params = [], []
    for column in ("first_name", "last_name", "job", "position", "interests"):
        if column in fields:
            sets.append(f"{column} = ?")
            params.append(str(fields[column])[:_CAPS[column]])
    if not sets:
        return False
    # datetime('now') is INLINE on purpose — app/db/pg.py rewrites the literal.
    sets.append("last_seen_at = datetime('now')")
    try:
        conn = get_db_connection()
        try:
            changed = conn.execute(
                "UPDATE visitors SET " + ", ".join(sets) + " WHERE id = ?",
                (*params, visitor_id)).rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return changed > 0
    except Exception as e:  # noqa: BLE001 — a write fault is reported, not raised
        logger.error("[signup] profile write failed: %s: %s", type(e).__name__, e)
        return False


def visitor_complete(visitor_id: str) -> bool:
    """Fail-open on storage trouble: a DB hiccup must not 403 the chat."""
    if not visitor_id:
        return True
    from app.services import conversations
    try:
        row = conversations.get_visitor(visitor_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[signup] visitor unreadable: %s: %s", type(e).__name__, e)
        return True
    return is_complete(row) if row else True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signup_service.py -v`
Expected: PASS. Also `python -m py_compile app/services/signup.py`.

- [ ] **Step 5: Commit**

```bash
git add app/services/signup.py tests/test_signup_service.py
git commit -m "feat(signup): server-owned validation and step service"
```

---

### Task 3: The signup endpoints + sanitize on promote

**Files:**
- Modify: `app/routers/otp.py` (new endpoints after `registration_options`; `_promote_to_visitor` at ~line 140)
- Test: `tests/test_signup_flow.py` (new)

**Interfaces:**
- Consumes: everything from Task 2; `visitor_auth.require_visitor`, `validate_request_origin`, `check_rate_limit` (already imported in otp.py); `conversations.get_visitor`.
- Produces: `GET /api/signup/next?lang=` → `{"complete": true}` | `{"step": {...}}` | 401 `{code: "registration_required"}`; `POST /api/signup/answer` `{key, value, lang}` → `200 {ok, next}` | `400 {code: "invalid_answer", message}` | `409 {code: "wrong_step", message, step}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_signup_flow.py` (setup copied from `tests/test_registration_chat_signup.py:34-70` — same fixtures `outbox`, `client`, `_no_ip_throttle`, `_cleanup`, same DEST):

```python
"""The signup flow's API contract: one question at a time, server-owned
order, every answer validated against the taxonomy before it is kept."""
import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_db_connection
from app.main import app
from app.services import otp as otp_service

DEST = "+989120000066"


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(otp_service, "_deliver", lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.headers.update({"Origin": "http://localhost",
                          "User-Agent": "pytest-agent/1.0"})
        yield c


@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM otp_challenges WHERE destination = ?", (DEST,))
        conn.execute("DELETE FROM visitors WHERE phone = ?", (DEST,))
        conn.commit()
    finally:
        conn.close()


def _signed_in(client, outbox, **carried):
    r = client.post("/api/auth/otp/request", json={
        "destination": DEST, "first_name": "", "last_name": "",
        "job": carried.get("job", ""), "position": carried.get("position", ""),
        "interests": carried.get("interests", "")})
    assert r.status_code == 200, r.text
    cid = r.json()["challenge_id"]
    v = client.post("/api/auth/otp/verify",
                    json={"challenge_id": cid, "code": outbox[-1][1]})
    assert v.status_code == 200, v.text


def _row():
    from app.services import conversations
    return conversations.find_visitor_by_phone(DEST)


def test_next_is_401_for_anonymous(client):
    assert client.get("/api/signup/next").status_code == 401


def test_the_full_flow_collects_and_persists_each_answer(client, outbox):
    _signed_in(client, outbox)
    n1 = client.get("/api/signup/next?lang=fa").json()
    assert n1["step"]["key"] == "name"
    assert n1["step"]["prompt"] == "نام و نام خانوادگی شما چیست؟"
    r = client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    assert r.status_code == 200, r.text
    assert _row()["first_name"] == "زهرا"          # persisted per answer (REQ-004)
    for key, value in (("job", "خبرنگار / رسانه"),
                       ("position", "کارشناس"),
                       ("interests", "هوش مصنوعی، رسانه و محتوا")):
        assert client.post("/api/signup/answer",
                           json={"key": key, "value": value}).status_code == 200
    assert client.get("/api/signup/next").json() == {"complete": True}


def test_resume_starts_at_the_missing_field(client, outbox):
    """The row IS the state: name+job already stored ⇒ سمت asked next."""
    _signed_in(client, outbox, job="خبرنگار / رسانه")
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    assert client.get("/api/signup/next").json()["step"]["key"] == "position"


def test_invalid_answer_is_rejected_and_not_persisted(client, outbox):
    _signed_in(client, outbox)
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    r = client.post("/api/signup/answer", json={"key": "job", "value": "فضانورد"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_answer"
    assert _row()["job"] == ""


def test_student_cannot_hold_a_title(client, outbox):
    """The reported incident, as a regression test (REQ-002)."""
    _signed_in(client, outbox)
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    client.post("/api/signup/answer", json={"key": "job", "value": "دانش‌آموز"})
    r = client.post("/api/signup/answer", json={"key": "position", "value": "کارشناس"})
    assert r.status_code == 400
    assert _row()["position"] == ""


def test_student_job_auto_answers_position(client, outbox):
    _signed_in(client, outbox)
    client.post("/api/signup/answer", json={"key": "name", "value": "زهرا کریمی"})
    r = client.post("/api/signup/answer", json={"key": "job", "value": "دانش‌آموز"})
    assert r.status_code == 200
    assert _row()["position"] == "سمت سازمانی ندارم"      # REQ-009
    assert r.json()["next"]["step"]["key"] == "interests"  # سمت never asked


def test_wrong_step_resyncs_the_client(client, outbox):
    _signed_in(client, outbox)
    r = client.post("/api/signup/answer", json={"key": "interests", "value": "هوش مصنوعی"})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "wrong_step" and body["step"]["key"] == "name"


def test_promote_drops_inconsistent_carried_fields(client, outbox):
    """The OTP request body is not a bypass: bad values never reach the row."""
    _signed_in(client, outbox, job="دانش‌آموز", position="کارشناس")
    row = _row()
    assert row["job"] == "دانش‌آموز"
    assert row["position"] == ""


def test_answer_after_complete_is_a_wrong_step(client, outbox):
    test_the_full_flow_collects_and_persists_each_answer(client, outbox)
    r = client.post("/api/signup/answer", json={"key": "job", "value": "دانش‌آموز"})
    assert r.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signup_flow.py -v`
Expected: FAIL — 404s on `/api/signup/*`.

- [ ] **Step 3: Implement**

In `app/routers/otp.py`:

(a) Import the service next to the other service imports (top of file):

```python
from app.services import signup as signup_service
```

(b) `_promote_to_visitor` — sanitize before writing (right after `if not record: return ""`):

```python
    # The challenge body is not a bypass around the signup flow's rules:
    # anything invalid is dropped here, leaving the question to be asked
    # properly in the chat (spec §6).
    record = signup_service.sanitize_registration(record)
```

(c) New endpoints after `registration_options` (before `/api/auth/profile`):

```python
class SignupAnswerBody(BaseModel):
    key: str = Field(..., min_length=2, max_length=16)
    value: str = Field(..., min_length=1, max_length=500)
    lang: str = Field("fa", max_length=8)


@router.get("/api/signup/next")
async def signup_next(lang: str = "fa",
                      visitor_id: str = Depends(visitor_auth.require_visitor)):
    """The question this visitor still owes, or complete.

    Anonymous gets the same machine-readable 401 as /chat, so the browser
    opens the sign-up card rather than printing an error sentence. The
    order is the SERVER's (name → job → position → interests, skipping
    what is stored); the browser renders, it does not decide."""
    row = conversations.get_visitor(visitor_id) or {}
    lang = "en" if lang.lower().startswith("en") else "fa"
    return signup_service.pending_step(row, lang)


@router.post("/api/signup/answer",
             dependencies=[Depends(validate_request_origin)])
async def signup_answer(body: SignupAnswerBody, request: Request,
                        visitor_id: str = Depends(visitor_auth.require_visitor)):
    """Take ONE answer, validate it against the taxonomy, keep it or refuse
    it. This — not /api/auth/profile — is the only writer while signup is
    incomplete (spec §6.2)."""
    request.state.otp_limit_identity = f"otp:visitor:{visitor_id}"
    check_rate_limit(request)
    row = conversations.get_visitor(visitor_id) or {}
    pending = signup_service.pending_step(row, body.lang)
    if pending.get("complete") or pending["step"]["key"] != body.key:
        detail = {"code": signup_service.WRONG_STEP_CODE,
                  "message": "این پرسش الان نوبتِ او نیست.",
                  "step": {"complete": True} if pending.get("complete")
                  else pending["step"]}
        raise HTTPException(status_code=409, detail=detail)
    ok, message, fields = signup_service.validate_answer(row, body.key, body.value)
    if not ok:
        raise HTTPException(status_code=400, detail={
            "code": signup_service.INVALID_CODE, "message": message})
    if not signup_service.write_fields(visitor_id, fields):
        raise HTTPException(status_code=403, detail="این نشست معتبر نیست.")
    row = conversations.get_visitor(visitor_id) or {}
    lang = "en" if body.lang.lower().startswith("en") else "fa"
    return {"ok": True, "next": signup_service.pending_step(row, lang)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signup_flow.py tests/test_signup_service.py -v`
Expected: PASS. Also `python -m py_compile app/routers/otp.py`.

- [ ] **Step 5: Commit**

```bash
git add app/routers/otp.py tests/test_signup_flow.py
git commit -m "feat(signup): /api/signup/next + /answer endpoints, promote sanitized"
```

---

### Task 4: Enforcement — `/chat` 403, `/api/auth/profile` gate + validation

**Files:**
- Modify: `app/routers/chat.py:276-278` (the registration gate block)
- Modify: `app/routers/otp.py:448-475` (`update_profile`) — delete `_write_visitor_profile` (its only caller goes away)
- Test: `tests/test_signup_flow.py` (append), `tests/test_registration_chat_signup.py`, `tests/test_profile_edit.py` (fixture repair)

**Interfaces:**
- Consumes: `signup_service.visitor_complete`, `validate_profile_edit`, `write_fields`, `INCOMPLETE_CODE` (Task 2).
- Produces: `/chat` ⇒ `403 {code: "signup_incomplete"}` for signed-in-incomplete; `/api/auth/profile` ⇒ same 403 while incomplete, `400` with a Persian sentence on invalid edits, otherwise unchanged response shape.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signup_flow.py` (uses the chat-test pattern from `tests/test_chat_visitor_identity.py:79-110` — real app, tmp DB, seeded dataset, no network):

```python
# ── /chat and /api/auth/profile enforcement ──────────────────────────────

DATASET = [("faq-hours", "ساعت کاری", "نمایشگاه هر روز از ۹ صبح تا ۱۸ باز است.", "")]
CHAT_BODY = {"message": "ساعت کاری نمایشگاه چیست؟", "lang": "fa"}


@pytest.fixture()
def gated_app(tmp_path, monkeypatch):
    """Real app + registration switched on + a Tier-1 answer, no network."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "signup-chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app as fastapi_app
    with TestClient(fastapi_app) as boot:
        from app.db.queries import set_setting
        set_setting("registration_enabled", "true")
        conn = get_db_connection()
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM questions")
        for entry_id, title, text, video in DATASET:
            conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                         " VALUES (?, ?, ?, ?)", (entry_id, title, text, video))
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES (?, ?, '')", (CHAT_BODY["message"], "faq-hours"))
        conn.commit()
        conn.close()
        from app.services import search
        search.load_dataset_internal()
        yield fastapi_app
    search.load_dataset_internal()


@pytest.fixture()
def chat_client(gated_app):
    from app.auth.security import generate_chat_token
    c = TestClient(gated_app)
    c.headers.update({"Origin": "http://localhost",
                      "X-Chat-Token": generate_chat_token(),
                      "User-Agent": "KioskBrowser/1.0"})
    return c


def _complete_row(phone="09120000099"):
    from app.services.conversations import upsert_visitor
    return upsert_visitor(first_name="کامل", last_name="کاربر", phone=phone,
                          job="خبرنگار / رسانه", position="کارشناس",
                          interests="هوش مصنوعی")


def _incomplete_row(phone="09120000098"):
    from app.services.conversations import upsert_visitor
    return upsert_visitor(first_name="ناقص", last_name="کاربر", phone=phone,
                          job="", position="", interests="")


def _session_cookie(client, visitor_id):
    from app.auth import visitor as visitor_auth
    token = visitor_auth.mint(visitor_id)
    client.cookies.delete(visitor_auth.VISITOR_COOKIE_NAME)
    client.cookies.set(visitor_auth.VISITOR_COOKIE_NAME, token)


def test_chat_refuses_an_incomplete_signup(chat_client):
    _session_cookie(chat_client, _incomplete_row())
    r = chat_client.post("/chat", json=CHAT_BODY)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "signup_incomplete"


def test_chat_serves_a_complete_signup(chat_client):
    _session_cookie(chat_client, _complete_row())
    r = chat_client.post("/chat", json=CHAT_BODY)
    assert r.status_code == 200


def test_profile_refuses_until_complete_then_validates(client, outbox):
    _signed_in(client, outbox)
    r = client.post("/api/auth/profile", json={
        "job": "خبرنگار / رسانه", "position": "کارشناس",
        "interests": "هوش مصنوعی"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "signup_incomplete"
    for key, value in (("name", "زهرا کریمی"), ("job", "خبرنگار / رسانه"),
                       ("position", "کارشناس"), ("interests", "هوش مصنوعی")):
        assert client.post("/api/signup/answer",
                           json={"key": key, "value": value}).status_code == 200
    bad = client.post("/api/auth/profile", json={
        "job": "دانش‌آموز", "position": "کارشناس", "interests": "هوش مصنوعی"})
    assert bad.status_code == 400 and bad.json()["detail"]
    good = client.post("/api/auth/profile", json={
        "job": "سرمایه‌گذار", "position": "مدیر", "interests": "جذب سرمایه"})
    assert good.status_code == 200
```

Note: check how `tests/test_chat_visitor_identity.py` seeds its app fixture for the exact `set_setting` import path and whether `is_module_enabled("registration")` is true in tests (that file's registration-gate tests pass with the module loaded, so the default test app loads it — copy whatever that file does).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signup_flow.py -v -k "chat_ or profile_refuses"`
Expected: FAIL — `/chat` returns 200 for the incomplete visitor; profile returns 200.

Also run the three soon-to-break files and record the damage:

Run: `python -m pytest tests/test_registration_chat_signup.py tests/test_profile_edit.py -v`
Expected: several FAILs after implementation — this is why Step 4 repairs them.

- [ ] **Step 3: Implement**

`app/routers/chat.py` — extend the existing block (lines 276-278):

```python
    if (is_module_enabled("registration")
            and get_setting("registration_enabled", "false") == "true"):
        visitor_auth.require_visitor(http_request)
        # Signed in is not signed UP. An incomplete profile must finish the
        # signup flow first: its message is an answer the flow still owes,
        # not a question the pipeline may answer (spec §6.1, REQ-001).
        from app.services import signup as _signup_service
        if not _signup_service.visitor_complete(visitor_id):
            raise HTTPException(status_code=403, detail={
                "code": _signup_service.INCOMPLETE_CODE,
                "message": "برای ادامه، به چند پرسش کوتاه پاسخ دهید.",
            })
```

`app/routers/otp.py` `update_profile` — replace the `_write_visitor_profile` call with:

```python
    request.state.otp_limit_identity = f"otp:visitor:{visitor_id}"
    check_rate_limit(request)
    # While signup is incomplete this endpoint is closed: /api/signup/answer
    # is the only writer, so the flow cannot be bypassed by posting a
    # "complete" profile in one call (spec §6.2, REQ-006).
    if not signup_service.visitor_complete(visitor_id):
        raise HTTPException(status_code=403, detail={
            "code": signup_service.INCOMPLETE_CODE,
            "message": "برای ادامه، به چند پرسش کوتاه پاسخ دهید.",
        })
    problem = signup_service.validate_profile_edit(
        body.job, body.position, body.interests)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    fields = {"job": body.job, "position": body.position,
              "interests": body.interests}
    if body.first_name.strip():
        fields["first_name"] = body.first_name
    if body.last_name.strip():
        fields["last_name"] = body.last_name
    if not signup_service.write_fields(visitor_id, fields):
        raise HTTPException(status_code=403, detail="این نشست معتبر نیست.")
    return {"updated": True, "profile": _visitor_profile(visitor_id)}
```

Delete `_write_visitor_profile` (lines 190-239) — its only caller was the code just replaced. Keep `_visitor_profile`.

- [ ] **Step 4: Repair the fixtures and run everything**

`tests/test_registration_chat_signup.py`:
1. Add a helper after `_signed_up`:

```python
def _complete_signup(client, name="زهرا کریمی", job="خبرنگار / رسانه",
                     position="کارشناس", interests="هوش مصنوعی، رسانه و محتوا"):
    """The in-chat questions, driven through the endpoint that now owns
    them — the profile endpoint below then tests EDITS, not first writes."""
    for key, value in (("name", name), ("job", job),
                       ("position", position), ("interests", interests)):
        r = client.post("/api/signup/answer", json={"key": key, "value": value})
        assert r.status_code == 200, r.text
```

2. In every test that posts `/api/auth/profile` after `_signed_up` (`test_the_name_given_in_chat_reaches_the_profile_endpoint`, `test_the_three_chat_answers_reach_the_existing_profile_endpoint`, `test_the_signup_checkbox_survives_the_chat_answers`, `test_every_interest_at_once_still_fits_the_profile_endpoint`, `test_the_longest_job_and_position_fit_their_fields`): call `_complete_signup(client, outbox)` right after `_signed_up(...)`, and change the posted interests `"رسانه و ارتباطات"` → `"رسانه و محتوا"` (not a taxonomy label). Where a test asserts `stored["job"] == "خبرنگار / رسانه"` after the FIRST post, keep the assertion but move the value into the `_complete_signup` call.
3. `test_signup_needs_only_a_number_and_the_checkbox` stays as-is (it asserts the row is empty right after verify — still true).

`tests/test_profile_edit.py` — `_verified` calls pass partial profiles that the promote path now sanitizes. Update the calls to full valid sets:

- line 89: `_verified(client, outbox, job="خبرنگار / رسانه", position="کارشناس", interests="رسانه و محتوا")`
- line 109: same full set
- line 163 (`test_signup_checkbox...` is the other file) — here: any other `_verified(...)` calls in the file get `position="کارشناس"` + valid `interests=` added so the row lands complete and the edit tests test edits again.

Run: `python -m pytest tests/test_signup_flow.py tests/test_signup_service.py tests/test_registration_chat_signup.py tests/test_profile_edit.py tests/test_taxonomy.py tests/test_otp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/chat.py app/routers/otp.py tests/test_signup_flow.py tests/test_registration_chat_signup.py tests/test_profile_edit.py
git commit -m "feat(signup): /chat and profile endpoint enforce completeness + taxonomy validation"
```

---

### Task 5: `core.js` — 403 marker branch, `chat:new` event, signup hook

**Files:**
- Modify: `static/chat/core.js:26-36` (ChatConfig), `:662-671` (403 branch), `:1264-1267` (New chat)
- Test: `tests/test_registration_chat_signup.py` (append source asserts)

**Interfaces:**
- Produces: `ChatConfig.signupRequiredFn` (called with `{text}` when `/chat` answers 403 `signup_incomplete`; returns true if claimed); DOM event `chat:new` dispatched on `document` after a successful New chat. Task 6 consumes both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registration_chat_signup.py` (uses its existing `CORE_JS` / `REGISTRATION_JS` reads):

```python
# ── The engine's seam for the incomplete-signup 403 ─────────────────────

def test_core_hands_signup_incomplete_to_the_registration_module():
    assert "signupRequiredFn: null" in CORE_JS
    assert "detail.code === 'signup_incomplete'" in CORE_JS
    assert "ChatConfig.signupRequiredFn" in CORE_JS


def test_new_chat_announces_itself():
    """The registration module re-renders its pending question after the
    transcript wipe — the event is the reader/writer pair that closes the
    'chips gone, gate still swallowing messages' hole."""
    assert "new CustomEvent('chat:new')" in CORE_JS
    assert "addEventListener('chat:new'" in REGISTRATION_JS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_registration_chat_signup.py -k "signup_incomplete or announces" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

(a) `static/chat/core.js` ChatConfig block (~line 36, after `signInRequiredFn: null,`):

```js
    // The server's second door: signed in but signup incomplete (/chat 403
    // signup_incomplete). The registration module takes the message back,
    // asks the missing questions, and delivers it once complete.
    signupRequiredFn: null,
```

(b) The 403 branch (~line 668) — the marker check MUST come before the token refresh, or the retry burns the one refresh on a 403 that refreshing can never fix:

```js
        if (response.status === 403) {
            // signup_incomplete is NOT a token problem: check the marker
            // before the refresh below spends its one retry on it.
            const detail = await response.json()
                .then(d => (d && d.detail) || {})
                .catch(() => ({}));
            if (detail.code === 'signup_incomplete'
                && typeof ChatConfig.signupRequiredFn === 'function') {
                loadingBubble.style.opacity = '0';
                let taken = false;
                try { taken = ChatConfig.signupRequiredFn({ text: text }) === true; }
                catch (e) { console.error('signup gate failed:', e); }
                if (taken) return;
            }
            const refreshed = await refreshChatToken();
            if (refreshed) response = await doSend();
        }
```

(c) After `showQuestions();` in the New chat handler (~line 1267):

```js
        // Anyone owning per-conversation UI (the signup module's pending
        // question) re-renders on this; the DOM it lived in is now gone.
        document.dispatchEvent(new CustomEvent('chat:new'));
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_registration_chat_signup.py -v`
Expected: the two new tests PASS (`chat:new` listener assert still fails — it lands in Task 6; if so, move that one line of the test to Task 6's step instead of leaving it red, see Task 6 Step 1).

- [ ] **Step 5: Commit**

```bash
git add static/chat/core.js tests/test_registration_chat_signup.py
git commit -m "feat(chat): signup_incomplete 403 branch + chat:new event"
```

---

### Task 6: `registration.js` — server-driven questions, resume, input lock

**Files:**
- Modify: `static/companion/registration.js` (the whole in-chat questions machinery, ~lines 1112-1418), `static/companion/registration.js` edit modal (`fillSelect` ~line 676, `multiSelect` ~line 434)
- Test: `tests/test_registration_chat_signup.py` (rewrite the flow asserts)

**Interfaces:**
- Consumes: `GET /api/signup/next`, `POST /api/signup/answer` (Task 3), `ChatConfig.signupRequiredFn` + `chat:new` (Task 5).
- Produces: gate behavior — a message typed while a question is open becomes an answer; wrong/out-of-list input never advances; refresh/new-chat resumes from the server's pending step.

- [ ] **Step 1: Write the failing tests**

In `tests/test_registration_chat_signup.py`, REPLACE these now-obsolete tests:
- `test_job_and_position_take_one_answer_and_interests_takes_many` (chatSteps is client-side no more)
- `test_the_interests_question_is_one_line_to_switch_off` (ASK_INTERESTS gone — the server decides by the empty field)
- `test_the_name_is_asked_in_the_chat_before_the_other_questions` (order is server-side now — covered by `test_the_full_flow_collects_and_persists_each_answer`)

with:

```python
# ── The chat engine's seam, now server-driven ───────────────────────────

def test_the_signup_questions_come_from_the_server():
    assert "'/api/signup/next?lang='" in REGISTRATION_JS
    assert "'/api/signup/answer'" in REGISTRATION_JS
    assert "function chatSteps" not in REGISTRATION_JS
    assert "saveChatAnswers" not in REGISTRATION_JS


def test_list_answers_are_checked_against_the_options_first():
    """UX-only pre-check: anything not on the list is bounced with a hint
    and the question stays open. The server re-validates regardless."""
    assert "chooseFromList" in REGISTRATION_JS
    assert "tapOne: 'یکی را لمس کنید و دکمهٔ ارسال را بزنید.'" in REGISTRATION_JS
    assert "(یا خودتان بنویسید)" not in REGISTRATION_JS


def test_wrong_step_resyncs_from_the_server():
    assert "409" in REGISTRATION_JS
    assert "fetchNext()" in REGISTRATION_JS


def test_boot_and_new_chat_resume_the_pending_question():
    assert "addEventListener('chat:new'" in REGISTRATION_JS
    boot = REGISTRATION_JS[REGISTRATION_JS.index("fetch('/api/auth/registration-status')"):]
    assert "fetchNext()" in boot
```

Also move `assert "addEventListener('chat:new'" in REGISTRATION_JS` out of Task 5's test if it was deferred there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_registration_chat_signup.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

All in `static/companion/registration.js` (keep the file's ES5 style — `function () {}`, `var`):

(a) T dict: `fa.tapOne` → `'یکی را لمس کنید و دکمهٔ ارسال را بزنید.'`; `en.tapOne` → `'Tap one, then press send.'`; add to both dicts:

```js
            chooseFromList: 'یکی را از گزینه‌های بالا انتخاب کنید و دکمهٔ ارسال را بزنید.',
```

```js
            chooseFromList: 'Pick one of the options above, then press send.',
```

(b) Delete `ASK_INTERESTS` (line 83), `chatSteps` (~1123-1155), `startChatQuestions` (~1200-1214), `saveChatAnswers` (~1311-1355). Replace the `ask` state (~1157) with:

```js
    var ask = { current: null, box: null, watcher: null };
```

(c) Add the server-driven machinery where `startChatQuestions` was:

```js
    function signupLang() { return isFa() ? 'fa' : 'en'; }

    function fetchNext() {
        return fetch('/api/signup/next?lang=' + signupLang(),
                     { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
    }

    function askNext(payload) {
        if (!payload || payload.complete || !payload.step) return;
        ask.current = payload.step;
        botSay(payload.step.prompt);
        renderChoices(payload.step);
    }

    function answerAccepted(data) {
        if (data && data.next && data.next.complete) {
            ask.current = null;
            botSay(t().profileSaved);
            deliverHeld();
            return;
        }
        askNext(data && data.next);
    }

    function mergeFlags(value) {
        /* The sign-up checkbox lives in the same stored field as the
           interests, so the posted value must carry it — answering the
           question must never silently untick it. */
        const flags = (state.flags && state.flags.length)
            ? state.flags : splitInterests((server.profile || {}).interests || '');
        const all = flags.concat(splitInterests(value));
        return all.filter(function (v, i) {
            let first = -1;
            all.forEach(function (o, j) { if (first === -1 && sameLabel(o, v)) first = j; });
            return first === i;
        }).join('، ').slice(0, MAX_INTERESTS);
    }

    function answerOnList(step, value) {
        const items = splitInterests(value);
        if (!items.length) return false;
        return items.every(function (item) {
            return (step.options || []).some(function (o) { return sameLabel(o.label, item); });
        });
    }

    function acceptAnswer(text) {
        const step = ask.current;
        if (!step) return;
        const value = String(text || '').trim().slice(0, 500);
        /* UX-only pre-check — the server re-validates everything, so a
           crafted client gains nothing by skipping this. */
        if (step.key !== 'name' && (step.options || []).length
            && !answerOnList(step, value)) {
            botSay(t().chooseFromList);
            renderChoices(step);
            return;
        }
        clearChoices();
        if (value) visitorSaid(value);
        setInput('');
        post('/api/signup/answer', {
            key: step.key,
            value: step.key === 'interests' ? mergeFlags(value) : value,
            lang: signupLang()
        })
            .then(answerAccepted)
            .catch(function (err) {
                if (err && err.status === 409) {
                    /* Out of step with the server: resync to whatever it
                       says is still owed, and continue from there. */
                    ask.current = null;
                    fetchNext().then(askNext);
                    return;
                }
                botSay(err.detail || t().network);
                renderChoices(step);
            });
    }
```

(d) `renderChoices`/`toggleChoice`/`paintChoices`/`clearChoices`/`chosenNow` stay as they are (they already render from `step.options`, which the server payload provides in the same `{id, label}` shape).

(e) `gate()` — same shape, minus the steps array:

```js
    function gate(text) {
        if (ask.current) { acceptAnswer(text); return true; }
        if (isSignedIn()) return false;
        if (!server.known) return false;
        holdAndAsk(text, false);
        return true;
    }
```

(f) New 403 handler + listener, next to `serverGate`:

```js
    /** ChatConfig.signupRequiredFn — the server answered 403: signed in,
        signup incomplete. The message was already sent, so it is already
        on screen (heldEchoed) and is delivered once the flow completes. */
    function signupIncomplete(info) {
        heldMessage = (info && info.text) || '';
        heldEchoed = true;
        setInput('');
        fetchNext().then(askNext);
        return true;
    }

    document.addEventListener('chat:new', function () {
        /* New chat wiped the DOM the chips lived in. Mid-question ⇒
           re-ask the same step; otherwise an incomplete profile resumes
           (the refresh/new-chat hole the spec's incident 1 was). */
        if (ask.current) {
            botSay(ask.current.prompt);
            renderChoices(ask.current);
            return;
        }
        if (isSignedIn()) fetchNext().then(askNext);
    });
```

(g) Boot block — install the hook unconditionally (mirroring `signInRequiredFn`) and resume:

```js
    if (typeof ChatConfig !== 'undefined') ChatConfig.signInRequiredFn = serverGate;
    if (typeof ChatConfig !== 'undefined') ChatConfig.signupRequiredFn = signupIncomplete;
```

and inside the registration-status `.then`, after `ChatConfig.sendGateFn = gate;`:

```js
                // Resume: a visitor whose signup was interrupted (refresh,
                // New chat, a closed tab) is asked exactly what is missing.
                if (server.signed_in) {
                    fetchNext().then(function (p) { if (p && !p.complete) askNext(p); });
                }
```

(h) The verify success handler (~1048-1063) — the server decides completeness now:

```js
                setTimeout(function () {
                    closeModal();
                    sessionReady.then(function () {
                        fetchNext().then(function (p) {
                            if (p && !p.complete) askNext(p);
                            else deliverHeld();
                        });
                    });
                }, 900);
```

(i) Edit modal hardening — `fillSelect`: replace the kept-extra-option block so a value that left the taxonomy no longer renders as pickable (the backend would refuse it):

```js
            if (saved && Array.prototype.some.call(sel.options, function (op) { return op.value === saved; })) {
                sel.value = saved;
            }
```

(delete the `kept` option creation). `multiSelect(items, preselected)` gains a third param `allowAdd`; in `paintList`, gate the add-button on it:

```js
            const canAdd = allowAdd !== false && typed.length > 1 && !has(typed) &&
                !items.some(function (i) { return i.label.toLowerCase() === typed.toLowerCase(); });
```

and the edit modal's picker call becomes `multiSelect(o.interests || [], saved.filter(...), false)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_registration_chat_signup.py tests/test_signup_flow.py -v`
Expected: PASS. Sanity: `node --check static/companion/registration.js && node --check static/chat/core.js`.

- [ ] **Step 5: Commit**

```bash
git add static/companion/registration.js tests/test_registration_chat_signup.py
git commit -m "feat(signup): server-driven in-chat questions, resume, option-only answers"
```

---

### Task 7: Docs, graph, CI

**Files:**
- Modify: `docs/features/INDEX.md`, `docs/features/signup-integrity/SPEC.md` (status row), `CLAUDE.md` + `AGENTS.md` (services tables)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update docs**

- `docs/features/signup-integrity/SPEC.md`: `Status` → `Implemented`.
- `docs/features/INDEX.md`: the `signup-integrity` row → `Implemented | Ready`.
- `CLAUDE.md` and `AGENTS.md`: add `signup.py # Server-owned signup flow: validation + steps` to the services list in the project-structure block (same line style as `themes.py`).

- [ ] **Step 2: Keep the code graph current**

Run: `graphify update .`

- [ ] **Step 3: Full verification**

Run: `python -m py_compile app/services/signup.py app/routers/otp.py app/routers/chat.py app/services/taxonomy.py`
Run: `python -m pytest tests/test_signup_flow.py tests/test_signup_service.py tests/test_registration_chat_signup.py tests/test_profile_edit.py tests/test_taxonomy.py tests/test_taxonomy_admin.py tests/test_chat_visitor_identity.py tests/test_otp.py -v`
Expected: PASS locally (env/network-dependent files excluded on purpose — CI is the gate).

Push and watch the real signal:

```bash
git push -u origin HEAD && gh run watch
```

Expected: CI green. If a suite fails only on CI (live PostgreSQL etc.), read the log before touching anything — AGENTS.md: 15 tests always fail locally and pass on CI; the reverse deserves a look, not a guess.

- [ ] **Step 4: Commit**

```bash
git add docs/features/INDEX.md docs/features/signup-integrity/SPEC.md CLAUDE.md AGENTS.md
git commit -m "docs(signup-integrity): spec implemented, index and service tables updated"
```
