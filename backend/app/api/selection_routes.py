from fastapi import APIRouter
from app.utils.selection_persistence import load_selection

router = APIRouter(prefix="/selection", tags=["Selection"])


@router.get("/current")
def get_current_selection():
    # MUST return LIST (UI + OpenAPI expects list)
    return load_selection()
