from contextlib import closing

from fastapi import APIRouter, Request, Depends, HTTPException, Query

from app.models import SynonymRequest
from app.auth.security import verify_admin
from app.db.connection import get_db_connection
from app.utils.normalizer import load_synonyms_from_db


router = APIRouter()

# One apply may carry at most this many pairs — a checkbox list is bounded by
# the suggest limit anyway, and an unbounded loop of INSERTs in one request is
# a payload concern, not a synonym concern.
MAX_APPLY_PAIRS = 50


def _insert_synonym_pairs(pairs) -> int:
    """Insert (source, target) rows, then reload + bump the index ONCE.

    The single write path for the synonyms table: the manual add form, the
    suggestion apply, any future bulk writer. `load_synonyms_from_db()` and
    `bump_index_version()` live HERE so no new writer can forget them
    (ADR-017 — one place reindexes, every path reindexes).
    """
    conn = get_db_connection()
    try:
        inserted = 0
        # Both columns are the primary key, so there is nothing to update:
        # saving the same pair twice is a no-op instead of a duplicate row.
        for source, target in pairs:
            cur = conn.execute('INSERT OR IGNORE INTO synonyms (source, target)'
                               ' VALUES (?, ?)', (source, target))
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    load_synonyms_from_db()
    from app.services.search import bump_index_version
    bump_index_version()
    return inserted


@router.get("/api/synonyms")
async def get_synonyms(request: Request, admin: bool = Depends(verify_admin)):
    # Read from the DB (the single source of truth). Returning the in-memory
    # `active_synonyms` was broken: each gunicorn worker keeps its own copy, and
    # `load_synonyms_from_db()` rebinds the module variable — so a name imported
    # into this module pointed at the original (empty) list forever.
    #
    # One row per (source, target). A word with three synonyms is three rows,
    # and the second sort key keeps them in a stable order on both backends.
    with closing(get_db_connection()) as conn:
        rows = conn.execute('SELECT source, target FROM synonyms ORDER BY source, target').fetchall()
    return {"synonyms": [{"source": r["source"], "target": r["target"]} for r in rows]}


@router.post("/api/synonyms")
async def add_synonym(req: SynonymRequest, request: Request, admin: bool = Depends(verify_admin)):
    source = req.source.strip()
    target = req.target.strip()
    # An empty source would make the expansion pass insert the target between
    # every character of every query, and an empty target cannot be named on
    # the delete route, so neither is storable.
    if not source or not target:
        raise HTTPException(status_code=400, detail="کلمه اصلی و جایگزین هر دو لازم است.")
    try:
        _insert_synonym_pairs([(source, target)])
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/synonyms/{source}")
async def delete_synonym(source: str, request: Request,
                         target: str = Query(..., min_length=1),
                         admin: bool = Depends(verify_admin)):
    """Delete ONE mapping. `target` is required, and that is the whole point.

    This used to be `DELETE FROM synonyms WHERE source = ?`. Under the pair key
    that erases every synonym of the word, which is what production did each
    time an operator removed a single row. Naming the target is the only way
    the caller can say which mapping it means.
    """
    try:
        conn = get_db_connection()
        cur = conn.execute('DELETE FROM synonyms WHERE source = ? AND target = ?',
                           (source, target.strip()))
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        load_synonyms_from_db()
        from app.services.search import bump_index_version
        bump_index_version()
        return {"status": "success", "deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/synonyms/bulk-delete")
async def bulk_delete_synonyms(payload: dict, request: Request,
                               admin: bool = Depends(verify_admin)):
    """Delete many (source, target) pairs in one call, same identity rule as
    the single-delete route above: a pair, never a bare source, so removing a
    batch can never take out every synonym of a word the caller kept.
    """
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise HTTPException(status_code=400, detail="کلمه اصلی و جایگزین هر دو لازم است.")

    cleaned = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise HTTPException(status_code=400, detail="کلمه اصلی و جایگزین هر دو لازم است.")
        source = str(pair.get("source") or "").strip()
        target = str(pair.get("target") or "").strip()
        if not source or not target:
            raise HTTPException(status_code=400, detail="کلمه اصلی و جایگزین هر دو لازم است.")
        cleaned.append((source, target))

    try:
        conn = get_db_connection()
        deleted = 0
        for source, target in cleaned:
            cur = conn.execute('DELETE FROM synonyms WHERE source = ? AND target = ?',
                               (source, target))
            deleted += cur.rowcount
        conn.commit()
        conn.close()
        load_synonyms_from_db()
        from app.services.search import bump_index_version
        bump_index_version()
        return {"status": "success", "deleted": deleted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── AI suggestions (the model proposes, the operator approves) ────────────
# See app/services/synonym_suggest.py for the contract. Suggest spends one
# paid-model call and stores NOTHING; apply writes the operator's picks
# through _insert_synonym_pairs(), the same path as the manual add form.

@router.post("/admin/api/synonyms/suggest")
async def admin_suggest_synonyms(request: Request,
                                 admin: str = Depends(verify_admin)):
    """Ask the model once for synonym candidates. Returns them WITHOUT
    saving — nothing reaches the synonyms table until the operator picks
    rows and apply says so."""
    from app.services import synonym_suggest
    try:
        suggestions = await synonym_suggest.suggest_synonyms(actor=admin)
    except synonym_suggest.SuggestCooldownActive as e:
        raise HTTPException(
            status_code=429,
            detail=f"درخواست زیاد است؛ چند لحظه صبر کنید و دوباره امتحان کنید"
                   f" (حدود {e.seconds_left} ثانیه).")
    except synonym_suggest.SynonymSuggestUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"suggestions": suggestions}


@router.post("/admin/api/synonyms/suggest/apply")
async def admin_apply_suggested_synonyms(payload: dict, request: Request,
                                         admin: str = Depends(verify_admin)):
    """Insert the operator's selected pairs through the existing add-synonym
    write path (normalization reload + index bump stay single-source)."""
    from app.services import synonym_suggest

    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise HTTPException(status_code=400,
                            detail="فهرست پیشنهادها خالی است.")
    if len(pairs) > MAX_APPLY_PAIRS:
        raise HTTPException(status_code=400,
                            detail="فهرست پیشنهادها بیش از حد مجاز است.")

    cleaned, seen = [], set()
    for pair in pairs:
        if not isinstance(pair, dict):
            raise HTTPException(status_code=400,
                                detail="کلمه اصلی و جایگزین هر دو لازم است.")
        cleaned_pair = synonym_suggest.clean_pair(pair.get("word"),
                                                  pair.get("suggestion"))
        if cleaned_pair is None:
            raise HTTPException(status_code=400,
                                detail="کلمه اصلی و جایگزین هر دو لازم است.")
        key = (cleaned_pair[0], cleaned_pair[1])
        if key not in seen:
            seen.add(key)
            cleaned.append(cleaned_pair)

    try:
        added = _insert_synonym_pairs(cleaned)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "added": added}
