"""
Theme management API — list, activate themes.

Registered as a core module in the module registry.
"""

from fastapi import APIRouter, HTTPException, Depends

from app.models import ThemeActivateRequest
from app.auth.security import verify_admin
from app.services.themes import discover_themes, set_active_theme, get_active_theme

router = APIRouter()


@router.get("/admin/api/themes")
async def list_themes(_=Depends(verify_admin)):
    """Return all discovered themes with active status."""
    active = get_active_theme()
    all_themes = discover_themes()

    result = []
    for theme in all_themes:
        result.append({
            "name": theme["name"],
            "display_name": theme["display_name"],
            "description": theme["description"],
            "version": theme.get("version", "1.0.0"),
            "author": theme.get("author", ""),
            "screenshot": theme.get("screenshot", "screenshot.png"),
            "preview_colors": theme.get("preview_colors", {}),
            "is_active": theme["name"] == active,
        })

    return {"themes": result, "active": active}


@router.post("/admin/api/themes/activate")
async def activate_theme(body: ThemeActivateRequest, _=Depends(verify_admin)):
    """Activate a theme by name."""
    success = set_active_theme(body.name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Theme '{body.name}' not found")
    return {"status": "activated", "theme": body.name}
