from contextlib import closing

from fastapi import APIRouter, Request, Depends, HTTPException, Query

from app.models import SynonymRequest
from app.auth.security import verify_admin
from app.db.connection import get_db_connection
from app.utils.normalizer import load_synonyms_from_db


router = APIRouter()


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
        conn = get_db_connection()
        # Both columns are the primary key, so there is nothing to update:
        # saving the same pair twice is a no-op instead of a duplicate row.
        conn.execute('INSERT OR IGNORE INTO synonyms (source, target) VALUES (?, ?)',
                     (source, target))
        conn.commit()
        conn.close()
        load_synonyms_from_db()
        from app.services.search import bump_index_version
        bump_index_version()
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
