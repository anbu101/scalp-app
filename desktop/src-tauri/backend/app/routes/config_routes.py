# app/routes/config_routes.py

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict

from app.config.strategy_loader import (
    load_strategy_config,
    save_strategy_config,
)

from app.config.global_loader import (
    load_global_config,
    save_global_config,
)


from app.config.zerodha_credentials_store import (
    load_credentials,
    save_credentials,
)
from app.brokers.zerodha_auth import clear_access_token


ALLOWED_TRADE_SIDE_MODES = {"CE", "PE", "BOTH"}

router = APIRouter(prefix="/api", tags=["config"])


# =========================================================
# Models
# =========================================================

class SaveConfigRequest(BaseModel):
    strategy_id: str
    config: Dict[str, Any]


class TradeSideModeRequest(BaseModel):
    strategy_id: str
    mode: str


class GlobalTradeSwitchRequest(BaseModel):
    trade_on: bool


class ZerodhaCredentialsIn(BaseModel):
    api_key: str
    api_secret: str


# =========================================================
# GLOBAL CONFIG (trade_on ONLY)
# =========================================================

@router.get("/global_config")
def get_global_config():
    return load_global_config()


@router.post("/global_config")
def save_global_trade_switch(req: GlobalTradeSwitchRequest):
    cfg = load_global_config()
    cfg["trade_on"] = req.trade_on
    save_global_config(cfg)

    return {
        "status": "ok",
        "trade_on": req.trade_on,
    }


# =========================================================
# STRATEGY CONFIG
# =========================================================

@router.get("/config")
def get_config(strategy_id: str = Query(...)):
    """
    Return config for a specific strategy.
    """
    return {
        "strategy_id": strategy_id,
        "config": load_strategy_config(strategy_id),
    }


@router.post("/save_config")
def save_config(req: SaveConfigRequest):
    """
    Overwrite strategy config.
    Used for lots, lot_size, risk params, etc.
    """
    try:
        save_strategy_config(req.strategy_id, req.config)

        return {
            "status": "ok",
            "strategy_id": req.strategy_id,
            "saved": req.config,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
# TRADE SIDE MODE (Strategy-Specific)
# =========================================================

@router.get("/trade_side_mode")
def get_trade_side_mode(strategy_id: str = Query(...)):
    cfg = load_strategy_config(strategy_id)
    return {
        "strategy_id": strategy_id,
        "mode": cfg.get("trade_side_mode", "BOTH"),
    }


@router.post("/trade_side_mode")
def set_trade_side_mode(req: TradeSideModeRequest):
    if req.mode not in ALLOWED_TRADE_SIDE_MODES:
        raise HTTPException(
            status_code=400,
            detail="Invalid trade_side_mode. Use CE / PE / BOTH",
        )

    cfg = load_strategy_config(req.strategy_id)
    cfg["trade_side_mode"] = req.mode
    save_strategy_config(req.strategy_id, cfg)

    return {
        "status": "ok",
        "strategy_id": req.strategy_id,
        "trade_side_mode": req.mode,
    }


# =========================================================
# ZERODHA CREDENTIALS (UNCHANGED)
# =========================================================

@router.get("/zerodha")
def get_zerodha_config():
    creds = load_credentials()

    if not creds:
        return {
            "configured": False,
            "api_key": None,
            "has_secret": False,
        }

    return {
        "configured": True,
        "api_key": creds.get("api_key"),
        "has_secret": bool(creds.get("api_secret")),
        "updated_at": creds.get("updated_at"),
    }


@router.post("/zerodha")
def save_zerodha_config(payload: ZerodhaCredentialsIn):
    save_credentials(
        api_key=payload.api_key,
        api_secret=payload.api_secret,
    )

    # 🔒 Force re-login on credential change
    clear_access_token()

    return {
        "status": "ok",
        "message": "Zerodha credentials saved. Please login again.",
    }
