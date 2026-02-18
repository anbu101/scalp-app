from fastapi import APIRouter
from app.config.strategy_loader import (
    load_strategy_config,
    save_strategy_config,
)

router = APIRouter(prefix="/strategies", tags=["Strategy"])


@router.get("/{strategy_id}/config")
def get_strategy(strategy_id: str):
    return load_strategy_config(strategy_id)


@router.post("/{strategy_id}/config")
def save_strategy(strategy_id: str, cfg: dict):
    current = load_strategy_config(strategy_id)

    current.update(cfg)

    save_strategy_config(strategy_id, current)

    return {"saved": True}
