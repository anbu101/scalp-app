# backend/app/monitor/tap.py — ── FLEET_MONITOR_20261127 ── (TD-110)
#
# The one-line taps api/telegram_api.py calls from its notify_* functions:
#
#   trade(kind, data)              TRADE_ENTRY / TRADE_EXIT (single-leg)
#   group(kind, data, pnl=None)    GROUP_ENTRY / GROUP_EXIT (baskets)
#   alert(kind, data, mode=None)   CRITICAL / ORDER_REJECTED / SYSTEM_ALERT
#
# Each builds an ALLOWLISTED payload (no button, URL, token or free-form
# extra key can leak) and enqueues it. LIVE only:
#   * a trade/group event ships only when its own mode is "live" (the mode
#     of the position, not the strategy's current setting, so an exit after
#     the strategy is switched OFF still ships; a PAPER_LIVE paper twin
#     reports mode "paper" and stays home);
#   * a CRITICAL ships unless notify_critical resolved it as PAPER;
#   * ORDER_REJECTED and SYSTEM_ALERT are broker/system facts: always ship.
# Never raises.
from __future__ import annotations

from typing import Any, Optional

from app.monitor import outbox

_TXT = 1000


def _s(v: Any, n: int = 120) -> Optional[str]:
    if v is None:
        return None
    return str(v)[:n]


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None or v == "" else round(float(v), 4)
    except Exception:
        return None


def _i(v: Any) -> Optional[int]:
    try:
        return None if v is None or v == "" else int(float(v))
    except Exception:
        return None


def _codename(sid: Optional[str]) -> Optional[str]:
    if not sid:
        return None
    try:
        from app.config.strategy_display import codename
        return _s(codename(sid), 60)
    except Exception:
        return _s(sid, 60)


def _is_live(mode: Any) -> bool:
    return str(mode or "live").strip().lower() == "live"


def trade(kind: str, data: dict) -> Optional[int]:
    try:
        d = data or {}
        if not _is_live(d.get("mode")):
            return None
        sid = _s(d.get("strategy_id"), 40)
        return outbox.enqueue(kind, {
            "strategy_id": sid, "codename": _codename(sid),
            "symbol": _s(d.get("symbol"), 60), "side": _s(d.get("side"), 12),
            "qty": _i(d.get("quantity") if d.get("quantity") is not None else d.get("qty")),
            "entry_price": _f(d.get("entry_price")), "exit_price": _f(d.get("exit_price")),
            "pnl": _f(d.get("pnl")), "sl": _f(d.get("sl")), "tp": _f(d.get("tp")),
            "reason": _s(d.get("exit_reason") or d.get("reason"), 60),
            "note": _s(d.get("note"), 300),
        })
    except Exception:
        return None


def _leg(leg: dict) -> dict:
    return {"leg_id": _s(leg.get("leg_id"), 40), "action": _s(leg.get("action"), 8),
            "opt_type": _s(leg.get("opt_type"), 4), "symbol": _s(leg.get("symbol"), 60),
            "strike": _f(leg.get("strike")), "qty": _i(leg.get("qty")),
            "entry_price": _f(leg.get("entry_price")), "exit_price": _f(leg.get("exit_price")),
            "pnl": _f(leg.get("pnl"))}


def group(kind: str, data: dict, pnl: Optional[float] = None) -> Optional[int]:
    try:
        d = data or {}
        if not _is_live(d.get("mode")):
            return None
        sid = _s(d.get("strategy_id"), 40)
        legs = [_leg(x) for x in (d.get("legs") or []) if isinstance(x, dict)][:12]
        return outbox.enqueue(kind, {
            "strategy_id": sid, "codename": _codename(sid),
            "expiry": _s(d.get("expiry"), 10), "lots": _i(d.get("lots")),
            "reason": _s(d.get("reason"), 60), "note": _s(d.get("note"), 300),
            "pnl": _f(pnl), "legs": legs,
        })
    except Exception:
        return None


def alert(kind: str, data: dict, mode: Optional[str] = None) -> Optional[int]:
    try:
        d = data or {}
        if kind == "CRITICAL" and str(mode or "").upper() == "PAPER":
            return None
        sid = _s(d.get("strategy_id"), 40)
        msg = d.get("message")
        if kind == "ORDER_REJECTED":
            msg = d.get("status_message") or msg or "Order rejected by broker"
        return outbox.enqueue(kind, {
            "strategy_id": sid, "codename": _codename(sid),
            "severity": _s(d.get("severity") or ("error" if kind != "SYSTEM_ALERT" else "info"), 10),
            "title": _s(d.get("title"), 60), "message": _s(msg, _TXT),
            "symbol": _s(d.get("symbol"), 60), "order_id": _s(d.get("order_id"), 40),
            "mode": _s(mode, 12),
        })
    except Exception:
        return None
