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


# ── UI_MASK BEGIN ──────────────────────────────────────────────────────
# Phase 2c: config masking for non-admin (STANDARD) licenses.
#
# GET  /api/config       → non-admin receives ONLY the lots whitelist below
#                          plus trade_execution_mode. Every SL/TP/premium/
#                          session/indicator param stays server-side.
# POST /api/save_config  → non-admin payloads are WHITELIST-MERGED onto the
#                          currently stored config: only the lots paths and
#                          trade_execution_mode can change; everything else
#                          is preserved from the stored (admin-authored /
#                          default) config. The response echoes the config
#                          that was ACTUALLY saved so the client can verify.
#
# Whitelist (not blacklist) on purpose — a param added later is masked by
# default instead of leaking. ADMIN ui_level takes the pre-existing code
# paths byte-for-byte (BB_V1 isolation argument). ui_level() fails closed
# to "standard" when the license is not usable, matching Phase 2b; note
# this means an UNACTIVATED backend also gets whitelist-merged saves.

from copy import deepcopy

from app.license import license_state

_MODE_KEY = "trade_execution_mode"
_MODE_VALUES = {"OFF", "PAPER", "LIVE"}

# Dotted paths a non-admin may READ and WRITE, per strategy — moved to a
# dependency-free module so the license applier can share it without
# importing this router's FastAPI/broker stack. Same name, same contents.
from app.config.lots_whitelist import LOTS_PATHS as _LOTS_PATHS


def _ui_masked() -> bool:
    return license_state.ui_level() != "admin"


def _path_get(obj, dotted):
    cur = obj
    for seg in dotted.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        else:
            return None
    return cur


def _path_set(obj, dotted, value):
    """Set dotted path, creating dicts as needed. List indices must already
    exist (we never invent legs for a strategy)."""
    segs = dotted.split(".")
    cur = obj
    for seg in segs[:-1]:
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return
        elif isinstance(cur, dict):
            if seg not in cur or not isinstance(cur[seg], (dict, list)):
                cur[seg] = {}
            cur = cur[seg]
        else:
            return
    last = segs[-1]
    if isinstance(cur, list):
        try:
            cur[int(last)] = value
        except (ValueError, IndexError):
            return
    elif isinstance(cur, dict):
        cur[last] = value


def _mask_config_response(strategy_id: str, cfg: dict) -> dict:
    """Reduce a full config to the non-admin whitelist view."""
    out: dict = {}
    for p in _LOTS_PATHS.get(strategy_id, []):
        v = _path_get(cfg, p)
        if v is not None:
            _path_set(out, p, v)
    if isinstance(cfg, dict) and _MODE_KEY in cfg:
        out[_MODE_KEY] = cfg[_MODE_KEY]
    return out


def _clamp_lots(v):
    """Coerce/validate a lots value from an untrusted client. Returns int
    or None (= reject silently, keep stored value)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = int(v)
    if v < 0:
        return None
    max_lots = license_state.ENTITLEMENTS.get("max_lots") or 0
    if max_lots and v > max_lots:
        v = int(max_lots)
    return v


def _whitelist_merge(strategy_id: str, current: dict, incoming: dict) -> dict:
    """Non-admin save: start from the STORED config, apply only whitelisted
    lots paths + mode from the incoming payload."""
    merged = deepcopy(current) if isinstance(current, dict) else {}
    for p in _LOTS_PATHS.get(strategy_id, []):
        v = _clamp_lots(_path_get(incoming, p))
        if v is not None:
            _path_set(merged, p, v)

    m = incoming.get(_MODE_KEY) if isinstance(incoming, dict) else None
    if m in _MODE_VALUES:
        if m == "LIVE" and not license_state.ENTITLEMENTS.get("live_trading", False):
            m = "PAPER"          # live_trading entitlement is the wall
        merged[_MODE_KEY] = m

    # BB_V1 leg-split invariant — mirror of Settings.handleLotsChange:
    # leg1 = min(prev leg1, total-1) || 1 ; leg2 = total - leg1.
    if strategy_id == "BB_V1":
        total = merged.get("lots")
        if isinstance(total, int) and total >= 1:
            prev_l1 = merged.get("lots_leg1")
            prev_l1 = prev_l1 if isinstance(prev_l1, int) and prev_l1 > 0 else 1
            l1 = min(prev_l1, total - 1) or 1
            merged["lots_leg1"] = l1
            merged["lots_leg2"] = total - l1

    return merged
# ── UI_MASK END ────────────────────────────────────────────────────────


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

    UI_MASK: non-admin licenses receive only the lots whitelist + mode.
    """
    cfg = load_strategy_config(strategy_id)
    # ── UI_MASK BEGIN ──
    if _ui_masked():
        cfg = _mask_config_response(strategy_id, cfg or {})
    # ── UI_MASK END ──
    return {
        "strategy_id": strategy_id,
        "config": cfg,
    }


@router.post("/save_config")
def save_config(req: SaveConfigRequest):
    """
    Overwrite strategy config (ADMIN ui_level — pre-existing behavior,
    byte-identical).

    UI_MASK: non-admin saves are whitelist-merged onto the stored config —
    only lots paths and trade_execution_mode can change. The response's
    `saved` field always echoes what was ACTUALLY persisted.
    """
    try:
        # ── UI_MASK BEGIN ──
        if _ui_masked():
            current = load_strategy_config(req.strategy_id) or {}
            merged = _whitelist_merge(req.strategy_id, current, req.config or {})
            save_strategy_config(req.strategy_id, merged)
            return {
                "status": "ok",
                "strategy_id": req.strategy_id,
                "saved": _mask_config_response(req.strategy_id, merged),
            }
        # ── UI_MASK END ──

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
    # ── UI_MASK BEGIN ── side mode is outside the lots-only surface.
    if _ui_masked():
        raise HTTPException(status_code=403, detail="Not permitted for this license")
    # ── UI_MASK END ──
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
