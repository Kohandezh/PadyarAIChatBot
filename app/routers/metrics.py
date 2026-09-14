"""GET /metrics — the Prometheus scrape endpoint. NEVER public.

Two auth modes, both ending in 403 for anyone who fails (see
docs/engineering/MONITORING.md for the security model):

  * METRICS_TOKEN set  → require `Authorization: Bearer <token>`, compared
    with secrets.compare_digest — the same timing-safe compare the repo
    uses for chat tokens (app/auth/security.py). This is the mode a
    scraper on another host uses; nginx must still not proxy /metrics to
    the internet (the token is a second layer, not the gate).
  * METRICS_TOKEN empty → an authenticated admin session, via the exact
    same verify_admin every admin API uses. A 401 from verify_admin is
    re-raised as 403: an unauthenticated probe must get "forbidden", not
    "unauthorized", so it learns nothing about which credential would work.

Read at REQUEST time through `config.METRICS_TOKEN`, never imported
by value, so an operator (or a test) can switch modes without a restart.
"""
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import generate_latest

from app import config
from app.services import metrics

router = APIRouter()

MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


async def _require_metrics_auth(request: Request) -> None:
    token = (config.METRICS_TOKEN or "").strip()
    if token:
        header = request.headers.get("authorization", "")
        supplied = ""
        if header.lower().startswith("bearer "):
            supplied = header[len("bearer "):].strip()
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=403, detail="Forbidden.")
        return
    from app.auth.security import verify_admin
    try:
        await verify_admin(request)
    except HTTPException:
        raise HTTPException(status_code=403, detail="Forbidden.")


@router.get("/metrics")
async def scrape_metrics(request: Request):
    await _require_metrics_auth(request)
    return Response(content=generate_latest(metrics.registry),
                    media_type=MEDIA_TYPE)
