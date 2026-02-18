from fastapi import APIRouter, Query
from app.utils.selection_persistence import load_selection

router = APIRouter(prefix="/selection", tags=["Selection"])


@router.get("/current")
def get_current_selection(
    strategy_id: str = Query(..., description="Strategy ID")
):
    return load_selection(strategy_id)
