# backend/app/api/lvb_routes.py
#
# ── LVB_V1_20261023 ── "Tripwire" Levels page API (admin-only: mounted with
# _require_admin_ui in api_server — experimental feature).
#
#   GET    /api/lvb_v1/state              page payload (3 s poll)
#   POST   /api/lvb_v1/config             {"trade_execution_mode": OFF|PAPER|LIVE|PAPER_LIVE}
#   POST   /api/lvb_v1/conditions         create (valid for TODAY only)
#   PUT    /api/lvb_v1/conditions/{id}    edit an ARMED condition (re-arms it from now)
#   DELETE /api/lvb_v1/conditions/{id}    cancel an ARMED condition
#   POST   /api/lvb_v1/squareoff/{id}     manual exit of that condition's trade
#   POST   /api/lvb_v1/squareoff_all      manual exit of every open trade
#
# ── LVB_SL_SPOT_20261029 ── SL input is sl_pts (spot points from the limit);
# state carries the per-side spot stops beside the targets.
#
# Every handler is wrapped: an LVB fault returns {"ok": False, "error": …}
# and never a 500 that could disturb the rest of the app.

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Body

from app.engine.lvb.lvb_core import (STRATEGY_ID, INSTRUMENTS, DEFAULTS, EOD_MAX_ALLOWED, LOTS_MAX,
                                     validate_condition, describe, minute_of, target_spot, stop_spot)

router = APIRouter(prefix="/api/lvb_v1", tags=["LVB_V1"])
IST = timezone(timedelta(hours=5, minutes=30))
MODES = ("OFF", "PAPER", "LIVE", "PAPER_LIVE")


def _today() -> str:
    return datetime.now(IST).date().isoformat()


def _rt():
    from app.engine.lvb import lvb_runtime as rt
    return rt


def _spot(inst: str) -> Optional[float]:
    try:
        from app.marketdata.market_indices_state import MarketIndicesState
        v = (MarketIndicesState.snapshot().get(inst) or {}).get("ltp")
        if v:
            return float(v)
    except Exception:
        pass
    eng = _rt().get_lvb_engine()
    if eng is not None and inst in eng.spot:
        return float(eng.spot[inst][0])
    return None


def _cfg() -> dict:
    try:
        from app.config.strategy_loader import load_strategy_config
        return load_strategy_config(STRATEGY_ID) or {}
    except Exception:
        return {}


def _err(e) -> dict:
    return {"ok": False, "error": str(e)}


@router.get("/state")
def lvb_state():
    try:
        rt = _rt()
        repo, mgr, eng = rt.get_lvb_repo(), rt.get_lvb_manager(), rt.get_lvb_engine()
        day = _today()
        cfg = _cfg()
        live = {p["cid"]: p for p in (mgr.positions() if mgr else [])}
        conds = []
        for r in repo.for_day(day):
            r = dict(r)
            p = live.get(r["id"])
            if p:
                r["mark"], r["mark_spot"] = p.get("mark"), p.get("mark_spot")
            r["open"] = r["status"] == "TRIGGERED" and r.get("exit_ts") is None
            px = r.get("exit_px") if r.get("exit_ts") else r.get("mark")
            r["pnl"] = (round((float(px) - float(r["entry_px"])) * int(r["qty"]), 2)
                        if r.get("entry_px") and px and r.get("qty") else None)
            if r.get("exit_ts") and mgr is not None and r.get("entry_px"):
                r["net"] = mgr.net_of(r["entry_px"], r["exit_px"], int(r["qty"]))
            r["targets"] = {"CE": target_spot("CE", r.get("upper"), None, float(r.get("tp_pts") or 0)),
                            "PE": target_spot("PE", None, r.get("lower"), float(r.get("tp_pts") or 0))}
            r["stops"] = {"CE": stop_spot("CE", r.get("upper"), None, r.get("sl_pts")),
                          "PE": stop_spot("PE", None, r.get("lower"), r.get("sl_pts"))}
            r["legacy_sl"] = r.get("sl_pts") is None
            r["summary"] = describe(r)
            conds.append(r)
        stale_open = [dict(r) for r in repo.open_trades() if r["day"] != day]
        return {"ok": True, "strategy": STRATEGY_ID, "running": mgr is not None, "day": day,
                "mode": str(cfg.get("trade_execution_mode") or "PAPER").upper(),
                "engine": eng.status() if eng else None,
                "spot": {k: _spot(k) for k in INSTRUMENTS},
                "conditions": conds, "stale_open": stale_open,
                "defaults": {"prem_min": DEFAULTS["prem_min"], "prem_max": DEFAULTS["prem_max"],
                             "eod": DEFAULTS["eod"], "lots": DEFAULTS["lots"], "lots_max": LOTS_MAX,
                             "sl_pts": {k: v["sl_default"] for k, v in INSTRUMENTS.items()},
                             "tp_pts": {k: v["tp_default"] for k, v in INSTRUMENTS.items()}},
                "server_ts": int(time.time())}
    except Exception as e:
        return _err(e)


@router.post("/config")
def lvb_config(patch: Dict = Body(...)):
    try:
        mode = str(patch.get("trade_execution_mode") or "").upper().replace("+", "_")
        if mode not in MODES:
            return _err(f"mode must be one of {MODES}")
        from app.config.strategy_loader import load_strategy_config, save_strategy_config
        cfg = load_strategy_config(STRATEGY_ID) or {}
        old = str(cfg.get("trade_execution_mode") or "PAPER").upper()
        cfg["trade_execution_mode"] = mode
        save_strategy_config(STRATEGY_ID, cfg)
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[LVB][MODE] {old} → {mode} (Levels page)")
        except Exception:
            pass
        return {"ok": True, "mode": mode, "previous": old}
    except Exception as e:
        return _err(e)


def _open_for_today(now_min: int) -> Optional[str]:
    if now_min >= EOD_MAX_ALLOWED:
        return "today's session is over for new conditions (after 15:20)"
    try:
        from app.utils.market_hours import is_trading_day
        if not is_trading_day():
            return "today is not a trading day"
    except Exception:
        pass
    return None


@router.post("/conditions")
def lvb_create(body: Dict = Body(...)):
    try:
        why = _open_for_today(minute_of(int(time.time())))
        if why:
            return _err(why)
        inst = str(body.get("instrument") or "").upper()
        clean, err = validate_condition(body, spot=_spot(inst) if inst in INSTRUMENTS else None)
        if err:
            return _err(err)
        nm = minute_of(int(time.time()))
        from app.engine.lvb.lvb_core import hm_to_min
        if hm_to_min(clean["eod"]) <= nm:
            return _err(f"EOD {clean['eod']} has already passed")
        cid = _rt().get_lvb_repo().create(_today(), clean)
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[LVB][CREATE] #{cid} {describe(clean)}")
        except Exception:
            pass
        return {"ok": True, "id": cid}
    except Exception as e:
        return _err(e)


@router.put("/conditions/{cid}")
def lvb_edit(cid: int, body: Dict = Body(...)):
    try:
        repo = _rt().get_lvb_repo()
        cur = repo.get(cid)
        if not cur or cur["day"] != _today():
            return _err("no such condition today")
        if cur["status"] != "ARMED":
            return _err(f"condition is {cur['status']} — only ARMED conditions can be edited")
        merged = {**{k: cur.get(k) for k in ("instrument", "upper", "lower", "prem_min", "prem_max", "sl_pts",
                                              "tp_pts", "eod", "lots")}, **body}
        inst = str(merged.get("instrument") or "").upper()
        clean, err = validate_condition(merged, spot=_spot(inst) if inst in INSTRUMENTS else None)
        if err:
            return _err(err)
        if not repo.update_armed(cid, clean):
            return _err("condition changed state while editing — refresh")
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[LVB][EDIT] #{cid} {describe(clean)} (re-armed from now)")
        except Exception:
            pass
        return {"ok": True, "id": cid}
    except Exception as e:
        return _err(e)


@router.delete("/conditions/{cid}")
def lvb_cancel(cid: int):
    try:
        repo = _rt().get_lvb_repo()
        cur = repo.get(cid)
        if not cur:
            return _err("no such condition")
        if cur["status"] != "ARMED":
            return _err(f"condition is {cur['status']} — only ARMED conditions can be cancelled")
        if not repo.set_status(cid, "CANCELLED", "cancelled on the Levels page"):
            return _err("condition changed state — refresh")
        return {"ok": True, "id": cid}
    except Exception as e:
        return _err(e)


@router.post("/squareoff/{cid}")
def lvb_squareoff(cid: int):
    try:
        eng = _rt().get_lvb_engine()
        if eng is None:
            return _err("LVB runtime is not running")
        return eng.request_close(int(cid), "MANUAL")
    except Exception as e:
        return _err(e)


@router.post("/squareoff_all")
def lvb_squareoff_all():
    try:
        eng = _rt().get_lvb_engine()
        if eng is None:
            return _err("LVB runtime is not running")
        return {"ok": True, **eng.request_close_all("MANUAL")}
    except Exception as e:
        return _err(e)
