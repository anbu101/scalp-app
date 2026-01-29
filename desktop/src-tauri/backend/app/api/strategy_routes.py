from fastapi import APIRouter
from app.config.strategy_loader import (
    load_strategy_config,
    save_strategy_config,
)

router = APIRouter(prefix="/config", tags=["Strategy"])


@router.get("/strategy")
def get_strategy():
    return load_strategy_config()


from app.engine.selection_engine import recompute_selection

@router.post("/strategy")
def save_strategy(cfg: dict):
    current = load_strategy_config()

    # 🔒 Merge instead of overwrite
    current.update(cfg)

    save_strategy_config(current)

    try:
        recompute_selection()
    except Exception as e:
        return {"saved": True, "selection_error": str(e)}

    return {"saved": True}

