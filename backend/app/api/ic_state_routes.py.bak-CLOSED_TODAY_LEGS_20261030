# backend/app/api/ic_state_routes.py
#
# IC panel state (shared V1/V2) — purpose-built for ICPanel.
#
# ── IC_SPLIT (2026-08-04) ── routes are per-strategy:
#   GET  /api/ic/{sid}/state        sid ∈ {IC_V1, IC_V2} (validated, 404 else)
#   POST /api/ic/{sid}/square_off
# Unknown sid FAILS CLOSED (404) — a typo must never silently read/flatten
# the other instance.
#
# Response shape (GET /api/ic/{sid}/state):
# {
#   "mode": "OFF" | "PAPER" | "LIVE",
#   "engine_up": true/false,
#   "group": {                          # null when no group today
#       "state": "ENTERING|OPEN|CLOSING|CLOSED|ABORTED",
#       "paper": true/false,
#       "mtc_fired": true/false,
#       "double_sl_minute": true/false,
#       "legs": [ { "leg_id","action","opt_type","symbol","qty",
#                   "entry_price","sl","tp","state","exit_price",
#                   "exit_reason","mtc_repinned","wing_fallback",
#                   "carried","is_adjust","adjust_of","entry_date","expiry",
#                   "phantom","gtt_ids":[...], "pnl": float|null } ],
#       "adjust_only": bool, "carry_committed": bool,
#       "pending": {"mtc": {...}, "adjust": {...}}
#   },
#   "entry_time","exit_time","latched_today": true/false,
#   "exit_mode","next_open_time","expiry_exit_time"
# }
#
# POST /api/ic/{sid}/square_off — manual flatten (reason=MANUAL). Same code
# path as EOD; safe no-op when nothing is open.
#
# Isolated: reads only that sid's runtime registry entry + config. If the
# runtime has never launched, returns mode from config with group=null.

from fastapi import APIRouter, HTTPException

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config

router = APIRouter(tags=["ic"])

_VALID_SIDS = ("IC_V1", "IC_V2")


def _require_sid(sid: str) -> str:
    sid = (sid or "").upper()
    if sid not in _VALID_SIDS:
        raise HTTPException(status_code=404, detail=f"unknown IC strategy {sid!r}")
    return sid

# ── IC_MTM BEGIN ── live LTP / unrealized enrichment (2026-07-30). The
# engine REST-polls open-leg LTPs into LTPStore every 4s; the panel polls
# this route every 5s — so open legs can show LTP, live P&L, and a group
# MTM without any new data path. Isolated try/excepts: pricing enrichment
# must never break the state response.
import time as _time
try:
    from app.marketdata.ltp_store import LTPStore as _LTPStore
except Exception:
    _LTPStore = None


def _leg_ltp(symbol: str):
    """(ltp, age_s) from LTPStore; (None, None) when unavailable."""
    if _LTPStore is None or not symbol:
        return None, None
    try:
        res = _LTPStore.get_with_timestamp(symbol)
        if not res:
            return None, None
        ltp, ts = res
        if not ltp or ltp <= 0:
            return None, None
        return float(ltp), max(0, int(_time.time() - ts))
    except Exception:
        return None, None
# ── IC_MTM END ──


def _cfg(sid: str) -> dict:
    try:
        return load_strategy_config(sid) or {}
    except Exception:
        return {}


@router.get("/api/ic/{sid}/state")
def get_ic_state(sid: str):
    sid = _require_sid(sid)
    cfg = _cfg(sid)
    mode = (cfg.get("trade_execution_mode", "OFF") or "OFF").upper()

    engine_up = False
    group_out = None
    latched = False
    try:
        from app.engine.ic.ic_runtime import get_ic_manager, get_ic_engine
        gm = get_ic_manager(sid)
        engine_up = get_ic_engine(sid) is not None
        if gm is not None:
            try:
                latched = bool(gm._latch_today())
            except Exception:
                latched = False
            core = gm.current_group()
            if core is not None:
                legs = []
                for leg in core.legs.values():
                    rt = gm.leg_runtime(leg.leg_id)
                    legs.append({
                        "leg_id":        leg.leg_id,
                        "action":        leg.action,
                        "opt_type":      leg.opt_type,
                        "symbol":        leg.symbol,
                        "qty":           leg.qty,
                        "entry_price":   leg.entry_price,
                        "sl":            leg.sl,
                        "tp":            leg.tp,
                        "state":         leg.state,
                        "exit_price":    leg.exit_price,
                        "exit_reason":   leg.exit_reason,
                        "mtc_repinned":  leg.mtc_repinned,
                        "wing_fallback": leg.wing_fallback,
                        "carried":       bool(getattr(leg, "carried", False)),
                        "is_adjust":     bool(getattr(leg, "is_adjust", False)),
                        "adjust_of":     getattr(leg, "adjust_of", None),
                        "entry_date":    getattr(leg, "entry_date", ""),
                        "expiry":        getattr(leg, "expiry", ""),
                        "phantom":       bool(rt.get("phantom")),
                        "gtt_ids":       list(rt.get("gtt_ids") or []),
                        "pnl":           leg.pnl(),
                        # ── IC_MTM ── live enrichment for OPEN legs
                        "ltp":           None,
                        "ltp_age_s":     None,
                        "open_pnl":      None,
                    })
                    if leg.state == "OPEN":
                        _ltp, _age = _leg_ltp(leg.symbol)
                        if _ltp is not None:
                            _row = legs[-1]
                            _row["ltp"] = round(_ltp, 2)
                            _row["ltp_age_s"] = _age
                            if leg.action == "SELL":
                                _row["open_pnl"] = round((leg.entry_price - _ltp) * leg.qty, 2)
                            else:
                                _row["open_pnl"] = round((_ltp - leg.entry_price) * leg.qty, 2)
                legs.sort(key=lambda l: l["leg_id"])
                # ── IC_MTM ── group aggregates. Booked legs only (phantom
                # sim legs excluded, matching the panel's realised line).
                # mtm is None while any open booked leg lacks a price — a
                # partial MTM presented as total would be a lie.
                _realized = sum((l["pnl"] or 0.0) for l in legs
                                if l["state"] == "CLOSED" and not l["phantom"])
                _uvals = [l["open_pnl"] for l in legs
                          if l["state"] == "OPEN" and not l["phantom"]
                          and l["open_pnl"] is not None]
                _open_total = sum(1 for l in legs
                                  if l["state"] == "OPEN" and not l["phantom"])
                _unreal = sum(_uvals) if _uvals else (0.0 if _open_total == 0 else None)
                _mtm = None
                if _open_total == len(_uvals):
                    _mtm = _realized + (sum(_uvals) if _uvals else 0.0)
                group_out = {
                    "realized_pnl":     round(_realized, 2),
                    "unrealized_pnl":   (round(_unreal, 2) if _unreal is not None else None),
                    "mtm":              (round(_mtm, 2) if _mtm is not None else None),
                    "open_legs_priced": len(_uvals),
                    "open_legs_total":  _open_total,
                    "state":            core.state,
                    "paper":            gm.is_paper(),
                    "mtc_fired":        core.mtc_fired,
                    "double_sl_minute": core.double_sl_minute,
                    "adjust_only":      bool(getattr(gm, "is_adjust_only", lambda: False)()),
                    "carry_committed":  bool(getattr(gm, "carry_committed", lambda: False)()),
                    "pending":          (gm.pending_view() if hasattr(gm, "pending_view") else {}),
                    "legs":             legs,
                }
    except Exception as e:
        write_audit_log(f"[API][IC_STATE][{sid}][ERR] {e}")

    return {
        "mode":             mode,
        "engine_up":        engine_up,
        "group":            group_out,
        "entry_time":       cfg.get("entry_time", "09:18"),
        "exit_time":        cfg.get("exit_time", "15:28"),
        "exit_mode":        str(cfg.get("exit_mode", "NEXT_OPEN") or "NEXT_OPEN").upper(),
        "next_open_time":   cfg.get("next_open_time", "09:16"),
        "expiry_exit_time": cfg.get("expiry_exit_time", "15:28"),
        "latched_today":    latched,
    }


@router.post("/api/ic/{sid}/square_off")
def post_ic_square_off(sid: str):
    sid = _require_sid(sid)
    try:
        from app.engine.ic.ic_runtime import get_ic_manager
        gm = get_ic_manager(sid)
        if gm is None:
            return {"ok": False, "closed": 0, "detail": "runtime not initialized"}
        n = gm.force_square_off_all(reason="MANUAL")
        write_audit_log(f"[API][IC_SQUAREOFF][{sid}] manual — closed={n}")
        return {"ok": True, "closed": n}
    except Exception as e:
        write_audit_log(f"[API][IC_SQUAREOFF][{sid}][ERR] {e}")
        return {"ok": False, "closed": 0, "detail": str(e)}


# ════════════════════════════════════════════════════════════════════════════
# ── CLOSED_RECENT_20260921 ── GET /api/ic/{sid}/closed — closed condors for
# the panel's "Closed positions · today and recent" section (VET panel v2
# parity). Additive: /state and /square_off above are untouched. Read-only,
# never throws, reads ONLY that sid's rows.
#
# One entry per CONDOR (group_id), not per leg: a leg is not a trade. A group
# is listed only when EVERY booked leg is closed — a condor with one short
# stopped out and three legs still open is an OPEN position and belongs to
# the leg table above, not here. Group exit time = the LAST leg's exit.
# "today" is by that exit timestamp, IST calendar day (real clock), so a
# NEXT_OPEN condor entered yesterday and closed 09:16 today counts today.
#
# PAPER rows come from paper_trades (net_pnl/total_charges as booked at
# close). LIVE rows come from trades, which stores no P&L: gross is
# direction-aware from entry/exit, charges from the backtest charges model
# (best effort — a failure books charges 0 and flags the group `approx`).
# A LIVE leg closed without an exit price (BROKER_EXIT) contributes 0 and
# flags `approx` too — never an invented number presented as exact.
#
# Masking: exit reasons are engine vocabulary — "CLOSED" for non-admin
# licenses, server-side (Phase 2b, fails closed).
# ════════════════════════════════════════════════════════════════════════════
import re as _re
import sqlite3 as _sqlite3

_IC_IST_OFF = 5 * 3600 + 30 * 60
_ic_warned = set()
_IC_SYM_RE = _re.compile(r"^([A-Z]+)(.{5})(\d+)(CE|PE)$")


def _ic_day_start(now: int) -> int:
    """Epoch of 00:00 IST for the IST day containing `now`."""
    return ((int(now) + _IC_IST_OFF) // 86400) * 86400 - _IC_IST_OFF


def _ic_db_path() -> str:
    from app.db.sqlite import DB_PATH
    return str(DB_PATH)


def _ic_is_admin() -> bool:
    try:
        from app.license import license_state
        return license_state.ui_level() == "admin"
    except Exception:
        return False                                   # fail closed


def _ic_short(sym: str) -> str:
    """NIFTY2692223400CE -> 23400CE (weekly YYMDD and monthly YYMMM codes are
    both 5 chars). Unparseable symbols come back whole."""
    m = _IC_SYM_RE.match(str(sym or "").replace(" ", "").upper())
    return f"{m.group(3)}{m.group(4)}" if m else str(sym or "")


def _ic_live_charges(is_short: bool, entry: float, exit_px: float, qty: int):
    try:
        from app.backtest.charges.charges_model import (
            charges_for_long_trade, charges_for_short_trade)
        fn = charges_for_short_trade if is_short else charges_for_long_trade
        cr = fn(entry_price=float(entry), exit_price=float(exit_px), qty=int(qty))
        return float(getattr(cr, "total_charges", 0.0) or 0.0), True
    except Exception:
        return 0.0, False


def _ic_rows(conn, sid: str, limit_legs: int, warnings: list):
    """Normalised leg rows for one sid from both books, newest first. A
    missing table is normal (book never used); anything else — a renamed
    column, say — is surfaced in `warnings`, never swallowed."""
    out = []
    try:
        for r in conn.execute(
                "SELECT group_id, trade_class, symbol, trade_direction, qty, lots,"
                " entry_price, exit_price, exit_reason, entry_time, exit_time,"
                " state, pnl_value, total_charges, net_pnl"
                " FROM paper_trades WHERE strategy_name=? AND group_id IS NOT NULL"
                " ORDER BY entry_time DESC LIMIT ?", (sid, limit_legs)):
            d = dict(r)
            d["book"] = "PAPER"
            d["open"] = d["state"] == "OPEN"
            out.append(d)
    except Exception as e:
        if "no such table" not in str(e):
            warnings.append(f"paper_trades: {e!r}")
    try:
        for r in conn.execute(
                "SELECT group_id, trade_class, symbol, trade_direction, qty,"
                " entry_price, exit_price, exit_reason, entry_time, exit_time, state"
                " FROM trades WHERE strategy_id=? AND group_id IS NOT NULL"
                " ORDER BY entry_time DESC LIMIT ?", (sid, limit_legs)):
            d = dict(r)
            d["book"] = "LIVE"
            d["lots"] = None
            d["open"] = d["exit_time"] is None
            d["pnl_value"] = d["total_charges"] = d["net_pnl"] = None
            out.append(d)
    except Exception as e:
        if "no such table" not in str(e):
            warnings.append(f"trades: {e!r}")
    # A LIMIT can cut the OLDEST group of a book in half; half a condor with
    # every surviving leg closed would list with wrong numbers. Drop it.
    for book in ("PAPER", "LIVE"):
        mine = [r for r in out if r["book"] == book]
        if len(mine) >= limit_legs:
            cut = mine[-1]["group_id"]
            out = [r for r in out if not (r["book"] == book and r["group_id"] == cut)]
    return out


def _ic_groups(rows, day0: int):
    by = {}
    for r in rows:
        by.setdefault((r["book"], r["group_id"]), []).append(r)
    out = []
    for (book, gid), legs in by.items():
        if any(l["open"] for l in legs) or not all(l.get("exit_time") for l in legs):
            continue                                    # still an open position
        approx = False
        gross = charges = net = 0.0
        credit = debit = 0.0
        for l in legs:
            short = str(l.get("trade_direction") or "").upper() == "SHORT"
            qty = int(l.get("qty") or 0)
            ent, ext = l.get("entry_price"), l.get("exit_price")
            sign = 1.0 if short else -1.0
            credit += sign * float(ent or 0) * qty
            if ext is None:
                approx = True
            else:
                debit += sign * float(ext) * qty
            if l["book"] == "PAPER" and l.get("net_pnl") is not None:
                g = float(l.get("pnl_value") or 0)
                c = float(l.get("total_charges") or 0)
                n = float(l["net_pnl"])
            elif ext is None:
                g = c = n = 0.0
            else:
                g = (float(ent) - float(ext)) * qty if short else (float(ext) - float(ent)) * qty
                c, ok = _ic_live_charges(short, ent, ext, qty)
                approx = approx or not ok
                n = g - c
            gross, charges, net = gross + g, charges + c, net + n
        shorts = sorted((l for l in legs if str(l.get("trade_direction")).upper() == "SHORT"
                         and not str(l.get("trade_class") or "").endswith("A")),
                        key=lambda l: str(l.get("trade_class")))
        longs = sorted((l for l in legs if str(l.get("trade_direction")).upper() != "SHORT"
                        and not str(l.get("trade_class") or "").endswith("A")),
                       key=lambda l: str(l.get("trade_class")))
        adj = [l for l in legs if str(l.get("trade_class") or "").endswith("A")]
        base_qty = max([int(l.get("qty") or 0) for l in (shorts or legs)] or [0])
        reasons = {}
        for l in legs:
            k = l.get("exit_reason") or "—"
            reasons[k] = reasons.get(k, 0) + 1
        reason = " · ".join(k if n == len(legs) else f"{k}×{n}"
                            for k, n in sorted(reasons.items(), key=lambda kv: -kv[1]))
        exit_ts = max(int(l["exit_time"]) for l in legs)
        lots = next((l.get("lots") for l in shorts if l.get("lots")), None)
        sub = []
        if longs:
            sub.append("wings " + " / ".join(_ic_short(l["symbol"]) for l in longs))
        if adj:
            sub.append(f"+{len(adj)} adj")
        out.append({
            "group_id": f"{book}:{gid}", "book": book, "kind": "IC",
            "label": " / ".join(_ic_short(l["symbol"]) for l in (shorts or legs)),
            "sub": " · ".join(sub) or None,
            "legs": len(legs), "lots": lots, "qty": base_qty,
            "entry": round(credit / base_qty, 2) if base_qty else None,
            "exit": (round(debit / base_qty, 2) if base_qty and not approx else None),
            "entry_ts": min(int(l["entry_time"]) for l in legs),
            "exit_ts": exit_ts, "reason": reason,
            "gross": round(gross, 2), "charges": round(charges, 2),
            "net": round(net, 2), "approx": approx, "today": exit_ts >= day0,
        })
    out.sort(key=lambda g: g["exit_ts"], reverse=True)
    return out


@router.get("/api/ic/{sid}/closed")
def get_ic_closed(sid: str, limit: int = 12):
    sid = _require_sid(sid)
    now = int(_time.time())
    day0 = _ic_day_start(now)
    out = {"ok": True, "strategy": sid, "groups": [], "today_net": 0.0,
           "closed_today": 0, "server_ts": now}
    try:
        limit = max(1, min(int(limit), 50))
        warns = []
        with _sqlite3.connect(_ic_db_path(), timeout=30) as conn:
            conn.row_factory = _sqlite3.Row
            rows = _ic_rows(conn, sid, 400, warns)
        groups = _ic_groups(rows, day0)
        if not _ic_is_admin():
            for g in groups:
                g["reason"] = "CLOSED"
        out["today_net"] = round(sum(g["net"] for g in groups if g["today"]), 2)
        out["closed_today"] = sum(1 for g in groups if g["today"])
        out["groups"] = groups[:limit]
        if warns:
            out["warnings"] = warns
            if sid not in _ic_warned:                  # once per process, not per poll
                _ic_warned.add(sid)
                write_audit_log(f"[API][IC_CLOSED][{sid}][WARN] {warns}")
    except Exception as e:
        out["ok"] = False
        out["error"] = repr(e)
        write_audit_log(f"[API][IC_CLOSED][{sid}][ERR] {e}")
    return out
# ── CLOSED_RECENT_20260921 END ──
