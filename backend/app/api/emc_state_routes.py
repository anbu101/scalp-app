# backend/app/api/emc_state_routes.py
#
# ── EMC_V1 STATE ROUTES ── Fence: EMC_V1_LIVE_20261011
# Read-only dashboard payload composed from manager + engine + day core
# (isolated try/except; a sane payload when the runtime never launched).
# Prices tick on the frontend via LTPStore (the engine publishes every quote).

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter

router = APIRouter(prefix="/api/emc_v1", tags=["EMC_V1"])
IST = timezone(timedelta(hours=5, minutes=30))

_EMPTY = {"ok": True, "running": False, "strategy": "EMC_V1", "mode": "OFF", "phase": "OFFLINE",
          "position": None, "setup": None, "spot": None, "ema": None, "last_cross": None, "day": {},
          "cfg": {}, "heartbeat_ts": None, "trades": [], "frozen": False, "refused": None}


def _hm(m):
    return None if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"


@router.get("/state")
def emc_state():
    try:
        from app.engine.emc.emc_runtime import get_emc_manager, get_emc_engine
        mgr, eng = get_emc_manager(), get_emc_engine()
        if mgr is None:
            return dict(_EMPTY)
        out = dict(_EMPTY)
        cfg = mgr.cfg() or {}
        out.update({"running": True, "mode": mgr.mode(), "heartbeat_ts": getattr(eng, "last_poll_ts", None),
                    "cfg": {k: cfg.get(k) for k in ("ema_fast", "ema_slow", "spot_tf", "opt_tf", "sel_prem_min",
                                                    "sel_prem_max", "sl_pts", "tp_pts", "entry_cutoff",
                                                    "eod_square_off", "max_losses_per_day", "arm_skip_from",
                                                    "arm_skip_to", "lots")},
                    "day": dict(mgr.day_stats or {})})
        if eng is not None:
            out["phase"] = eng.phase()
            out["refused"] = eng.refused
            sp = eng.spot
            if sp is not None:
                out["frozen"] = bool(sp.guard.frozen)
                if sp.today:
                    b = sp.today[-1]
                    out["spot"] = {"close": b.close, "ts": b.ts}
                f, s = sp.last_vals
                out["ema"] = {"fast": f, "slow": s}
                out["gap_cross"] = _hm(sp.gap_cross_min)
            if eng.last_cross:
                k, x = eng.last_cross
                out["last_cross"] = {"at": _hm(k), "dir": "UP" if x > 0 else "DOWN"}
            c = eng.core
            if c is not None:
                out["day"].update({"entries": c.day_entries, "losses": c.day_losses, "halted": c.halted,
                                   "diag": dict(eng.diag)})
                if c.setup is not None:
                    st = c.setup
                    out["setup"] = {"side": st["side"], "symbol": st["c"]["sym"], "sel_px": st["c"]["price"],
                                    "armed_at": _hm(st["k"]), "warming": bool(st.get("unready"))}
            out["dte"] = getattr(eng, "dte", None)
            out["dte_tag"] = getattr(eng, "dte_tag", None)
        p = mgr.pos
        if p is not None:
            out["position"] = {"symbol": p.symbol, "side": p.side, "mode": p.mode, "entry": p.entry_px,
                               "qty": p.qty, "lots": p.lots, "sl": p.sl, "tp": p.tp, "entry_ts": p.entry_ts,
                               "mark": p.mark, "row_id": p.row_id}
        try:
            from app.db.sqlite import get_conn
            now = datetime.now(IST)
            day0 = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            rows = get_conn().execute(
                "SELECT paper_trade_id, symbol, side, trade_mode, entry_price, exit_price, exit_reason, qty,"
                " state, entry_time, exit_time, sl_price, tp_price, pnl_value, net_pnl FROM paper_trades"
                " WHERE strategy_name='EMC_V1' AND candle_ts >= ? AND paper_trade_id NOT LIKE 'SHD:%'"
                " ORDER BY candle_ts DESC LIMIT 20", (day0,)).fetchall()
            out["trades"] = [dict(r) for r in rows]
        except Exception:
            pass
        return out
    except Exception as e:
        return dict(_EMPTY, ok=False, error=repr(e))


@router.post("/square_off")
def emc_square_off():
    try:
        from app.engine.emc.emc_runtime import get_emc_manager
        mgr = get_emc_manager()
        if mgr is None:
            return {"ok": False, "error": "runtime not up"}
        return {"ok": True, "closed": mgr.kill_all()}
    except Exception as e:
        return {"ok": False, "error": repr(e)}
