from contextlib import closing

from fastapi import APIRouter, Request, Depends, HTTPException

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
    with closing(get_db_connection()) as conn:
        rows = conn.execute('SELECT source, target FROM synonyms ORDER BY source').fetchall()
    return {"synonyms": [{"source": r["source"], "target": r["target"]} for r in rows]}


@router.post("/api/synonyms")
async def add_synonym(req: SynonymRequest, request: Request, admin: bool = Depends(verify_admin)):
    try:
        conn = get_db_connection()
        conn.execute('INSERT OR REPLACE INTO synonyms (source, target) VALUES (?, ?)', (req.source.strip(), req.target.strip()))
        conn.commit()
        conn.close()
        load_synonyms_from_db()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/synonyms/{source}")
async def delete_synonym(source: str, request: Request, admin: bool = Depends(verify_admin)):
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM synonyms WHERE source = ?', (source,))
        conn.commit()
        conn.close()
        load_synonyms_from_db()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
