# backend/app/api/vet_state_routes.py
#
# ── VET_V1 STATE API ── read-only, feeds the VET dashboard panel + curl-level
# inspection (tma2_state_routes pattern). Touches no other strategy.

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/vet", tags=["vet"])


def _repo():
    from app.engine.vet.vet_common import VetRepo
    return VetRepo()


@router.get("/trades")
def vet_trades_route(limit: int = Query(200, le=1000),
                     status: str = Query("all")):
    repo = _repo()
    try:
        import sqlite3
        with sqlite3.connect(repo.db_path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            q = "SELECT * FROM vet_trades"
            if status in ("OPEN", "CLOSED", "STALE"):
                q += f" WHERE status='{status}'"
            q += " ORDER BY entry_ts DESC, leg_role ASC LIMIT ?"
            rows = [dict(r) for r in c.execute(q, (limit,))]
        closed = [r for r in rows if r["status"] == "CLOSED"]
        mains = [r for r in closed if r["leg_role"] == "MAIN"]
        return {"trades": rows,
                "summary": {
                    "open": sum(1 for r in rows if r["status"] == "OPEN"),
                    "closed": len(closed),
                    # positions, not legs — a wing is not an independent trade
                    "closed_positions": len(mains),
                    "net_pnl": round(sum(r["net_pnl"] or r["pnl"] or 0
                                         for r in closed), 2),
                }}
    except Exception as e:
        return {"error": str(e), "trades": []}


@router.get("/status")
def vet_status():
    """Liveness for the panel: manager position, signal-engine health
    (frozen flag, warmup depth, last bar), mode and leg action. Everything a
    'why is it not trading?' question needs, in one call."""
    out = {"manager": None, "signal_engine": None, "open_legs": 0}
    try:
        from app.engine.vet.vet_selection_loop import get_engine, get_manager
        m = get_manager()
        e = get_engine()
        if m is not None:
            out["manager"] = m.state()
        if e is not None:
            out["signal_engine"] = e.status()
    except Exception as exc:
        out["error"] = str(exc)
    try:
        out["open_legs"] = len(_repo().open_legs())
    except Exception:
        pass
    return out

# ════════════════════════════════════════════════════════════════════════════
# ── VET_PANEL_V2_20260921 ── /api/vet/state — ONE composed payload for the
# dashboard panel (ORB_PANEL_V2 pattern). Additive: /trades and /status above
# are untouched. Read-only, never throws, never touches manager state.
#
# Why v1 showed "nothing": the panel only knew symbol + entry price. This adds
# the mark (so MTM exists even when no other engine happens to publish the
# symbol to LTPStore — VET's tick engine keeps its own chain store and does
# not feed LTPStore), quantity, entry time, expiry, wing marks, today's closed
# groups, and a DB FALLBACK for the position: a carried positional row must be
# visible while the loop is still booting / waiting for the Zerodha session,
# and the panel must say loudly that nobody is managing it yet.
#
# Masking: the regime/condition block is the signal itself — stripped
# server-side for non-admin licenses (Phase 2b convention, fails closed).
# ════════════════════════════════════════════════════════════════════════════
import time as _time

_IST_OFF = 5 * 3600 + 30 * 60
_LTP_FRESH_S = 90          # seconds (wall clock, same unit as LTPStore stamps)


def _ist_day_start(now: int) -> int:
    """Epoch of 00:00 IST for the IST day containing `now` (real clock)."""
    return ((int(now) + _IST_OFF) // 86400) * 86400 - _IST_OFF


def _is_admin() -> bool:
    try:
        from app.license import license_state
        return license_state.ui_level() == "admin"
    except Exception:
        return False                                   # fail closed


def _mark_for(sym, quote_fn):
    """(price, source). LTP = a FRESH LTPStore print (someone is publishing
    the symbol — the panel may tick on it); QUOTE_1M = VET's own chain store,
    last 1m close; LTP_STALE = an old LTPStore print, last resort. A stale
    print must never outrank VET's own quote: LTPStore keeps whatever another
    engine wrote hours ago. Never logs, never raises."""
    if not sym:
        return None, None
    stale = None
    try:
        from app.marketdata.ltp_store import LTPStore
        got = LTPStore.get_with_timestamp(sym)
        if got and got[0] and float(got[0]) > 0:
            if _time.time() - float(got[1] or 0) <= _LTP_FRESH_S:
                return float(got[0]), "LTP"
            stale = float(got[0])
    except Exception:
        pass
    if quote_fn is not None:
        try:
            q = quote_fn(sym)
            if q is not None and float(q) > 0:
                return float(q), "QUOTE_1M"
        except Exception:
            pass
    if stale is not None:
        return stale, "LTP_STALE"
    return None, None


def _leg_view(leg, quote_fn):
    if not leg:
        return None
    mark, src = _mark_for(leg.get("tradingsymbol"), quote_fn)
    return {
        "symbol": leg.get("tradingsymbol"),
        "direction": leg.get("direction"),
        "side": leg.get("instrument_type"),
        "strike": leg.get("strike"),
        "expiry": (str(leg.get("expiry"))[:10] if leg.get("expiry") else None),
        "entry": leg.get("entry_price"),
        "entry_ts": leg.get("entry_ts"),
        "qty": leg.get("qty"),
        "lots": leg.get("lots"),
        "lot_size": leg.get("lot_size"),
        "mark": mark, "mark_src": src,
    }


def _closed_groups(rows, day0):
    """Closed legs -> one entry per position (wing folded into net)."""
    groups = {}
    for r in rows:
        if r.get("status") != "CLOSED" or not r.get("exit_ts"):
            continue
        groups.setdefault(r["group_id"], []).append(r)
    out = []
    for gid, legs in groups.items():
        main = next((l for l in legs if l.get("leg_role") == "MAIN"), legs[0])

        def _n(l):
            v = l.get("net_pnl")
            return float(v if v is not None else (l.get("pnl") or 0))
        out.append({
            "group_id": gid,
            "symbol": main.get("tradingsymbol"),
            "direction": main.get("direction"),
            "side": main.get("instrument_type"),
            "qty": main.get("qty"), "lots": main.get("lots"),
            "entry": main.get("entry_price"), "exit": main.get("exit_price"),
            "entry_ts": main.get("entry_ts"), "exit_ts": main.get("exit_ts"),
            "reason": main.get("exit_reason"),
            "gross": round(sum(float(l.get("pnl") or 0) for l in legs), 2),
            "charges": round(sum(float(l.get("charges") or 0) for l in legs), 2),
            "net": round(sum(_n(l) for l in legs), 2),
            "has_wing": any(l.get("leg_role") == "WING" for l in legs),
            "today": int(main.get("exit_ts") or 0) >= day0,
        })
    out.sort(key=lambda g: g["exit_ts"] or 0, reverse=True)
    return out


@router.get("/state")
def vet_state():
    now = int(_time.time())
    day0 = _ist_day_start(now)
    out = {"ok": True, "strategy": "VET_V1", "running": False, "mode": None,
           "leg_action": None, "positional": None, "hedged": False,
           "frozen": False, "freeze_reason": None, "engine": None,
           "signal": None, "spot": None, "cfg": {}, "day": {},
           "position": None, "position_source": None,
           "closed": [], "today_net": 0.0, "server_ts": now}
    admin = _is_admin()
    m = e = None
    try:
        from app.engine.vet.vet_selection_loop import get_engine, get_manager
        m, e = get_manager(), get_engine()
    except Exception as exc:
        out["error"] = repr(exc)

    # ── config: from the running manager, else from disk (loop not up) ──
    cfg = {}
    try:
        if m is not None:
            cfg = dict(m.cfg or {})
        else:
            from app.config.strategy_loader import load_strategy_config
            cfg = dict(load_strategy_config("VET_V1") or {})
    except Exception:
        cfg = {}
    q = cfg.get("quantity") or {}
    eod_square = bool(cfg.get("eod_square", True))
    out["mode"] = (m.mode if m is not None
                   else str(cfg.get("trade_execution_mode") or "").upper() or None)
    out["leg_action"] = str(cfg.get("leg_action") or "BUY").upper()
    out["positional"] = not eod_square
    out["cfg"] = {
        "lots": q.get("lots"), "lot_size": q.get("lot_size"),
        "entry_cutoff": cfg.get("entry_cutoff", "15:00"),
        "exit_time": cfg.get("exit_time", "15:15"),
        "max_trades_per_day": int(cfg.get("max_trades_per_day") or 0),
        "hedge_enabled": bool(cfg.get("hedge_enabled")),
        "hedge_max_premium": cfg.get("hedge_max_premium"),
    }

    quote_fn = None
    if m is not None:
        out["running"] = True
        quote_fn = getattr(m, "quote_fn", None)
        try:
            out["hedged"] = bool(m.wants_wing)
            out["frozen"] = bool(m.frozen)
            out["freeze_reason"] = m.freeze_reason
            out["day"] = {"entries": int(getattr(m, "_day_entries", 0) or 0)}
            sp = (m.cfg or {}).get("_spot")
            if sp:
                out["spot"] = {"ltp": float(sp), "src": "1M_CLOSE"}
        except Exception:
            pass

    if e is not None:
        try:
            es = e.status()
            out["engine"] = {k: es.get(k) for k in (
                "warmup_sessions", "warmup_required", "warmup_ok",
                "bars_today", "last_bar_ts")}
            if es.get("frozen"):
                out["frozen"] = True
                out["freeze_reason"] = es.get("freeze_reason") or out["freeze_reason"]
            sig = e.latest_signal()
            if sig and admin:                          # the signal IS the recipe
                out["signal"] = {k: sig.get(k) for k in (
                    "bar_ts", "condition", "in_range", "dir_trend", "spot")}
        except Exception:
            pass

    # ── position: manager truth first, DB truth when the loop is not up ──
    main = wing = None
    gid = None
    try:
        if m is not None and m.pos is not None:
            gid, main, wing = m.pos.get("group_id"), m.pos.get("main"), m.pos.get("wing")
            out["position_source"] = "MANAGER"
        elif m is None:
            g = _repo().open_group(None)
            if g and g.get("main"):
                gid, main, wing = g["group_id"], g["main"], g.get("wing")
                out["position_source"] = "DB"
    except Exception as exc:
        out["error"] = repr(exc)
    if main:
        pv = _leg_view(main, quote_fn)
        pv["group_id"] = gid
        pv["mode"] = main.get("mode")
        pv["carried"] = int(main.get("entry_ts") or now) < day0
        pv["signal_bar_ts"] = main.get("signal_bar_ts")
        if admin:
            pv["condition"] = main.get("condition")
        pv["wing"] = _leg_view(wing, quote_fn)
        out["position"] = pv

    # ── closed positions: today's + the recent tail (positional strategy —
    # "today" is often empty, the last few exits are the useful context) ──
    try:
        import sqlite3
        repo = _repo()
        with sqlite3.connect(repo.db_path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM vet_trades WHERE status='CLOSED' "
                "ORDER BY exit_ts DESC LIMIT 40")]
        groups = _closed_groups(rows, day0)
        if not admin:
            for g in groups:
                g["reason"] = "CLOSED"
        out["closed"] = groups[:12]
        out["today_net"] = round(sum(g["net"] for g in groups if g["today"]), 2)
        out["day"]["closed_today"] = sum(1 for g in groups if g["today"])
    except Exception:
        pass
    return out
# ── VET_PANEL_V2_20260921 END ──
