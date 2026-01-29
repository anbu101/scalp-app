# app/routes/config_routes.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

from app.config.strategy_loader import (
    load_strategy_config,
    save_strategy_config,
)

ALLOWED_TRADE_SIDE_MODES = {"CE", "PE", "BOTH"}

router = APIRouter(prefix="/api", tags=["config"])

from pydantic import BaseModel
from app.config.zerodha_credentials_store import (
    load_credentials,
    save_credentials,
)
from app.brokers.zerodha_auth import clear_access_token

# -------------------------
# Models
# -------------------------

class SaveConfigRequest(BaseModel):
    config: Dict[str, Any]


class TradeSideModeRequest(BaseModel):
    mode: str

class ZerodhaCredentialsIn(BaseModel):
    api_key: str
    api_secret: str

# -------------------------
# Config APIs
# -------------------------

@router.get("/config")
def get_config():
    """
    Return current strategy config.
    """
    return {
        "config": load_strategy_config() or {}
    }


@router.post("/save_config")
def save_config(req: SaveConfigRequest):
    """
    Overwrite strategy config.
    Used for lots, lot_size, risk params, etc.
    """
    try:
        save_strategy_config(req.config)
        return {"status": "ok", "saved": req.config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Trade Side Mode (CE / PE / BOTH)
# -------------------------

@router.get("/trade_side_mode")
def get_trade_side_mode():
    cfg = load_strategy_config() or {}
    return {
        "mode": cfg.get("trade_side_mode", "BOTH")
    }


@router.post("/trade_side_mode")
def set_trade_side_mode(req: TradeSideModeRequest):
    mode = req.mode

    if mode not in ALLOWED_TRADE_SIDE_MODES:
        raise HTTPException(
            status_code=400,
            detail="Invalid trade_side_mode. Use CE / PE / BOTH"
        )

    cfg = load_strategy_config() or {}
    cfg["trade_side_mode"] = mode
    save_strategy_config(cfg)

    return {
        "status": "ok",
        "trade_side_mode": mode
    }

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
