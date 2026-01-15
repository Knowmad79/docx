from fastapi import APIRouter, Query

from app.services.grid import get_triage_grid

router = APIRouter()


@router.get("/api/state/grid")
async def state_grid(owner: str | None = Query(default=None), preview_limit: int = Query(default=8, ge=1, le=50)):
    result = get_triage_grid(owner_id=owner, preview_limit=preview_limit)
    result["owner"] = owner
    return result
