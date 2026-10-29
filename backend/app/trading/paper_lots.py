# backend/app/trading/paper_lots.py
#
# ── PAPER_LOTS_20261030 ── separate lot counts for the PAPER twin in
# PAPER_LIVE mode.
#
# The twin (trading/shadow_book.py) copies every LIVE row: same contract,
# same entry and exit minute, same prices. Only its SIZE can now differ.
# The user enters a paper value next to every lots field of a strategy;
# blank / 0 = same as live. Values live in the strategy config as
#
#     paper_lots = { "<lots path with / for .>": int, ... }
#     e.g. TMA_V2: {"s1/main/lots": 10, "s1/hedge/lots": 10}
#
# Keys mirror the friend-editable LOTS_PATHS (config/lots_whitelist.py), so
# the non-admin whitelist, the licence max_lots clamp and the config-override
# reinstate all treat them exactly like the live lots fields.
#
# Each live row is mapped to the lots field that sized it, then
#     twin_lots = max(1, round(row_lots × paper_value ÷ live_value))
# The ratio form keeps day-level adjustments the engine applied to the live
# row (DTE × lots, expiry-day lots, a BB_V1 leg split) in the same proportion.
#
# Slippage is NOT affected: it is measured per live order from the reference
# LTP at send vs the broker's average fill (slippage/slip_store.py), never
# from the twin. Nothing here places an order or touches a live row.
# Never raises: any failure → the twin keeps the live size (old behaviour).

from __future__ import annotations

from typing import Optional, Tuple

FENCE = "PAPER_LOTS_20261030"


def _path_get(obj, dotted: str):
    cur = obj
    for seg in str(dotted).split("."):
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


def _int(v) -> int:
    try:
        if isinstance(v, bool):
            return 0
        return max(0, int(float(v)))
    except Exception:
        return 0


def _side_of(side, symbol) -> str:
    s = str(side or "").upper()
    if s in ("CE", "PE"):
        return s
    u = str(symbol or "").upper()
    return "CE" if u.endswith("CE") else "PE" if u.endswith("PE") else ""


def _is_expiry_today() -> Optional[bool]:
    try:
        from datetime import date
        from app.engine.dte_live import live_dte
        d = live_dte(date.today())
        return None if d is None else (d == 0)
    except Exception:
        return None


def _row_path(strategy_id: str, cfg: dict, *, side, symbol, trade_class,
              live_lots: int) -> Optional[str]:
    """The lots field that sized this live row, or None (unknown → no scaling)."""
    from app.config.lots_whitelist import LOTS_PATHS
    paths = LOTS_PATHS.get(strategy_id) or []
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    tc = str(trade_class or "").upper()

    if strategy_id == "BB_V2":
        s = _side_of(side, symbol)
        return "ce_lots" if s == "CE" else "pe_lots" if s == "PE" else None

    if strategy_id == "TSG_V1":
        exp = _int(cfg.get("expiry_lots"))
        if exp <= 0:
            return "lots"                         # live never uses expiry_lots
        today_exp = _is_expiry_today()
        if today_exp is None:                     # calendar unknown → match size
            return "expiry_lots" if (live_lots == exp and exp != _int(cfg.get("lots"))) else "lots"
        return "expiry_lots" if today_exp else "lots"

    if strategy_id in ("TMA_V1", "TMA_V2"):
        root = "c1.sell.lots" if strategy_id == "TMA_V1" else "s1.main.lots"
        hedge = "c1.buy.lots" if strategy_id == "TMA_V1" else "s1.hedge.lots"
        if tc == "SELL":
            return root
        if tc == "BUY":
            return hedge
        return None

    if strategy_id in ("IC_V1", "IC_V2"):
        if tc.endswith("A") and tc[:-1] in ("L1", "L2"):   # adjustment leg "L1A"
            p = f"adjust.{tc[:-1]}.lots"
            return p if p in paths else None
        legs = cfg.get("legs") or []
        for i, leg in enumerate(legs):
            if isinstance(leg, dict) and str(leg.get("id") or "").upper() == tc:
                p = f"legs.{i}.lots"
                return p if p in paths else None
        return None

    return None


def paper_key(path: str) -> str:
    return str(path).replace(".", "/")


def resolve_twin_lots(strategy_id: str, live_lots: int, *, side=None, symbol=None,
                      trade_class=None, cfg: Optional[dict] = None) -> Tuple[int, Optional[str]]:
    """→ (twin_lots, path). twin_lots == live_lots when no paper value applies."""
    live_lots = _int(live_lots)
    try:
        if cfg is None:
            from app.config.strategy_loader import load_strategy_config
            cfg = load_strategy_config(strategy_id) or {}
        pl = cfg.get("paper_lots") if isinstance(cfg, dict) else None
        if not isinstance(pl, dict) or not pl or live_lots <= 0:
            return live_lots, None
        path = _row_path(strategy_id, cfg, side=side, symbol=symbol,
                         trade_class=trade_class, live_lots=live_lots)
        if not path:
            return live_lots, None
        paper = _int(pl.get(paper_key(path)))
        live_val = _int(_path_get(cfg, path))
        if paper <= 0 or live_val <= 0:
            return live_lots, path
        if live_lots == live_val:
            return paper, path
        return max(1, int(round(live_lots * paper / live_val))), path
    except Exception as e:
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[PAPER_LOTS][{strategy_id}][ERR] {e!r} — twin keeps live size")
        except Exception:
            pass
        return live_lots, None
