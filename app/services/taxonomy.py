"""Single source of truth for the registration form and the visit planner.

Everything the visitor can pick (jobs, interests, checkboxes) and everything
the planner can recommend (sections) comes from `data/visit-taxonomy.json`.
Nothing here is hardcoded in the form, the planner, or the stylesheet — so a
replacement taxonomy is a file drop, not a code change.

Two guarantees the rest of the app relies on:

* **A broken file never reaches the product.** The loader validates the whole
  document before publishing it; on any failure it logs and keeps serving the
  last good version (or the built-in minimum on first load). A malformed edit
  degrades the suggestions, it never takes registration down.
* **Edits appear without a restart.** The mtime is checked per read, and the
  parsed document is swapped in atomically — a concurrent request sees the old
  document or the new one, never a half-built one.
"""
import json
import os
import threading
from typing import List, Optional

from app.config import BASE_DIR, logger

TAXONOMY_PATH = os.environ.get(
    "VISIT_TAXONOMY_PATH", os.path.join(BASE_DIR, "data", "visit-taxonomy.json")
)

# Served when the file is missing or has never parsed. Deliberately minimal:
# an installation with no taxonomy should look obviously unconfigured rather
# than quietly pretending to have content.
_MINIMUM = {
    "version": "builtin-minimum",
    "status": "missing-file",
    "jobs": [],
    "positions": [],
    "interests": [],
    "flags": [],
    "fallback_ids": [],
    "no_position_jobs": [],
    "sections": [],
}

_lock = threading.Lock()
_doc: dict = _MINIMUM
_mtime: float = -1.0
_loaded_once = False


def _clean_items(raw, *, required: tuple) -> List[dict]:
    """Keep only well-formed entries; skip and log the rest.

    One bad row must not cost the whole list — a taxonomy is edited by hand
    and a single typo in item 40 should not empty the dropdown.
    """
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if any(not str(item.get(k, "")).strip() for k in required):
            logger.error("[taxonomy] skipping entry missing %s: %r", required, item)
            continue
        if item["id"] in seen:
            logger.error("[taxonomy] skipping duplicate id: %s", item["id"])
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def _validate(raw: dict) -> Optional[dict]:
    """Return a normalised document, or None if it is unusable."""
    if not isinstance(raw, dict):
        logger.error("[taxonomy] root is not an object")
        return None

    sections = []
    for s in _clean_items(raw.get("sections"), required=("id", "fa", "en")):
        kws = s.get("keywords")
        if not isinstance(kws, list) or not kws:
            logger.error("[taxonomy] section %s has no keywords — skipped", s["id"])
            continue
        sections.append({
            "id": s["id"], "fa": s["fa"], "en": s["en"],
            "keywords": [str(k) for k in kws],
            "why_fa": s.get("why_fa", ""), "why_en": s.get("why_en", ""),
        })

    if not sections:
        logger.error("[taxonomy] no usable sections — keeping previous taxonomy")
        return None

    known = {s["id"] for s in sections}
    fallback = [i for i in raw.get("fallback_ids", []) if i in known]
    if not fallback:
        # Never leave the planner with nothing to say.
        fallback = [sections[0]["id"]]
        logger.error("[taxonomy] fallback_ids empty or unknown — using %s", fallback)

    jobs = _clean_items(raw.get("jobs"), required=("id", "fa"))
    positions = _clean_items(raw.get("positions"), required=("id", "fa"))
    job_ids = {j["id"] for j in jobs}
    # Jobs whose only valid سمت is "none". Unknown ids are dropped silently —
    # the same forgiving rule as everywhere else here — so the rule can never
    # point at a job the form does not offer.
    no_position = [i for i in raw.get("no_position_jobs", [])
                   if isinstance(i, str) and i in job_ids]

    return {
        "version": str(raw.get("version", "unversioned")),
        "status": str(raw.get("status", "")),
        "jobs": jobs,
        # Optional: a taxonomy with no positions leaves the سمت field a free-text
        # input rather than an empty dropdown.
        "positions": positions,
        "interests": _clean_items(raw.get("interests"), required=("id", "fa")),
        "flags": _clean_items(raw.get("flags"), required=("id", "fa")),
        "fallback_ids": fallback,
        "no_position_jobs": no_position,
        "sections": sections,
    }


def _reload_if_changed() -> None:
    global _doc, _mtime, _loaded_once
    try:
        mtime = os.path.getmtime(TAXONOMY_PATH)
    except OSError:
        if not _loaded_once:
            logger.error("[taxonomy] file not found: %s", TAXONOMY_PATH)
            _loaded_once = True
        return

    if mtime == _mtime:
        return

    with _lock:
        if mtime == _mtime:  # another thread won the race
            return
        try:
            with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
                parsed = _validate(json.load(f))
        except Exception as e:  # noqa: BLE001 — bad JSON must not take the app down
            logger.error("[taxonomy] load failed (%s): %s", type(e).__name__, e)
            _mtime = mtime  # don't retry the same broken bytes on every request
            _loaded_once = True
            return

        if parsed is None:
            _mtime = mtime
            _loaded_once = True
            return

        _doc = parsed
        _mtime = mtime
        _loaded_once = True
        logger.info(
            "[taxonomy] loaded v%s — %d sections, %d jobs, %d interests",
            parsed["version"], len(parsed["sections"]),
            len(parsed["jobs"]), len(parsed["interests"]),
        )


def document() -> dict:
    """The current taxonomy. Cheap: an mtime stat, then a dict reference."""
    _reload_if_changed()
    return _doc


def sections() -> List[dict]:
    return document()["sections"]


def fallback_ids() -> List[str]:
    return document()["fallback_ids"]


def form_options(lang: str = "fa") -> dict:
    """What the registration form needs, in the visitor's language."""
    doc = document()

    def localise(items):
        return [
            {
                "id": i["id"],
                "label": (i.get("fa") if lang == "fa" else i.get("en")) or i.get("fa", ""),
            }
            for i in items
        ]

    def label_of(item):
        return (item.get("fa") if lang == "fa" else item.get("en")) or item.get("fa", "")

    job_labels = {j["id"]: label_of(j) for j in doc["jobs"]}
    no_position_label = ""
    for p in doc.get("positions", []):
        if p.get("id") == "none":
            no_position_label = label_of(p)

    return {
        "version": doc["version"],
        "jobs": localise(doc["jobs"]),
        "positions": localise(doc.get("positions", [])),
        "interests": localise(doc["interests"]),
        "flags": localise(doc["flags"]),
        "no_position_jobs": [job_labels[i] for i in doc.get("no_position_jobs", [])
                             if i in job_labels],
        "no_position_label": no_position_label,
    }


def expand_interests(text: str) -> str:
    """Append the extra keywords of any interest the visitor selected.

    A label and a section can name the same thing differently ("IoT" vs
    «اینترنت اشیا»). The taxonomy may carry those synonyms per interest; this
    folds them into the text the planner matches, so a picked interest is not
    lost to wording. Free-text interests pass through untouched.
    """
    if not text:
        return text
    extra = []
    lowered = text.lower()
    for item in document()["interests"]:
        label = str(item.get("fa", ""))
        if label and label.lower() in lowered:
            extra.extend(str(k) for k in item.get("keywords", []) or [])
    return text + (" " + " ".join(extra) if extra else "")
