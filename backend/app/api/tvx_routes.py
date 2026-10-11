# backend/app/api/tvx_routes.py
#
# ── TVX_V1_20261007 ── TV Alerts page API (+ TVX_HILO_20261007 range columns, TVX_T2_20261007 target, TVX_TG_20261008 telegram, TVX_OPTS_20261008 options) (admin-only: mounted with
# _require_admin_ui in api_server). Reads TVX's own tables; never touches the
# fleet's paper/live books.
#   GET  /api/tvx/state      position + mark, scoreboard, recent alerts, closed trades
#   POST /api/tvx/config     validate + save settings (blank read_token = keep)
#   POST /api/tvx/squareoff  manual paper exit at the next quote (bid)
#   POST /api/tvx/export     write all closed trades to ~/.scalp-app/exports/*.csv

from __future__ import annotations

import csv
import datetime as dt
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app.engine.tvx import tvx_core as core
from app.engine.tvx.tvx_engine import get_engine, get_repo_and_store
from app.engine.tvx import tvx_engine as _tvx_engine                 # ── TVX_TG_20261008 ──

router = APIRouter(prefix="/api/tvx", tags=["tvx"])
IST = core.IST


def _score(rows: List[Dict]) -> Dict:
    n = len(rows)
    wins = sum(1 for r in rows if (r.get("net") or 0) > 0)
    net = round(sum(r.get("net") or 0 for r in rows), 2)
    gross = round(sum(r.get("gross") or 0 for r in rows), 2)
    charges = round(sum(r.get("charges") or 0 for r in rows), 2)
    spread = round(sum(((r.get("entry_ask") or 0) - (r.get("entry_bid") or 0)
                        + (r.get("exit_ask") or 0) - (r.get("exit_bid") or 0)) / 2 * (r.get("qty") or 0)
                       for r in rows if r.get("entry_bid") and r.get("exit_bid")), 2)
    by_reason: Dict[str, int] = {}
    for r in rows:
        by_reason[r.get("exit_reason") or "?"] = by_reason.get(r.get("exit_reason") or "?", 0) + 1
    return {"trades": n, "wins": wins, "win_rate": round(100 * wins / n, 1) if n else None,
            "net": net, "gross": gross, "charges": charges, "spread_cost": spread,
            "avg_net": round(net / n, 2) if n else None, "by_reason": by_reason,
            "if_t1_net": core.t1_counterfactual(rows),          # ── TVX_T2_20261007 ──
            "if_t2_net": core.target_counterfactual(rows, "T2")}   # ── TVX_OPTS_20261008 ──


def _inst(inst: Optional[str]) -> str:
    i = (inst or "CRUDE").upper()
    if i not in core.PRESETS:
        raise HTTPException(status_code=400, detail=f"unknown instrument {inst}")
    return i


@router.get("/state")
def tvx_state(inst: Optional[str] = Query(None)):          # ── TVX_MULTI_20261009 ──
    inst = _inst(inst)
    repo, store = get_repo_and_store()
    eng = get_engine()
    cfg = store.load(inst)
    now = dt.datetime.now(IST)
    today = now.date()
    t0 = int(dt.datetime.combine(today, dt.time(0, 0), IST).timestamp())
    s = core.session(today, cfg)

    pos = repo.open_position(inst)
    if pos:
        mark = (eng.mark_of(inst) if eng else None) or {}
        ltp = mark.get("ltp")
        bid = mark.get("bid")
        pos = dict(pos, mark=mark, fut_mark=(eng.ref_mark_of(inst) if eng else None) or {},   # ── TVX_HILO ──
                   mtm_ltp=round((ltp - pos["entry_price"]) * pos["qty"], 2) if ltp else None,
                   mtm_bid=round((bid - pos["entry_price"]) * pos["qty"], 2) if bid else None)

    closed_all = repo.all_closed(inst)
    summary = []
    for i in core.INSTRUMENTS:                              # tab badges
        c = store.load(i)
        op = repo.open_position(i)
        summary.append({"inst": i, "label": core.PRESETS[i]["label"], "enabled": bool(c.get("enabled")),
                        "open_side": op["side"] if op else None,
                        "today_net": round(sum(r.get("net") or 0 for r in repo.closed_between(t0, t0 + 86400, i)), 2)})
    return {
        "fence": "TVX_V1_20261007",
        "inst": inst,
        "instruments": summary,
        "meta": {k: core.PRESETS[inst][k] for k in ("label", "market", "opt_exchange", "ref", "ref_label")},
        "now": int(now.timestamp()),
        "cfg": core.public_config(cfg),
        "engine": dict(eng.status) if eng else {"running": False},
        "pending_exit": repo.get_pending_exit(inst),
        "cursor": repo.get_cursor(),
        "session": {
            "weekday": core.is_weekday(today),
            "us_dst": core.us_dst_active(today),
            "start": s["start"].strftime("%H:%M"),
            "squareoff": s["squareoff"].strftime("%H:%M"),
            "close": s["close"].strftime("%H:%M"),
            "entry_block": core.entry_block(cfg, now, repo.get_pending_exit(inst) is not None),
        },
        "position": pos,
        "today": _score(repo.closed_between(t0, t0 + 86400, inst)),
        "all_time": _score(closed_all),
        "alerts": repo.recent_alerts(40, inst),
        # ── TVX_TG_20261008 ── Telegram outbox health
        "notify": dict(repo.notify_stats(), enabled=bool(cfg.get("notify_telegram")),
                       **({k: v for k, v in (_tvx_engine.get_notifier().status.items())}
                          if _tvx_engine.get_notifier() else {"running": False})),
        "closed": repo.closed_trades(60, inst),
    }


@router.post("/config")
def tvx_config(patch: Dict = Body(...), inst: Optional[str] = Query(None)):
    patch = dict(patch or {})
    inst = _inst(inst or patch.pop("inst", None))
    _, store = get_repo_and_store()
    cfg, errs = store.save(patch, inst)
    if errs:
        raise HTTPException(status_code=400, detail="; ".join(errs))
    return {"ok": True, "cfg": core.public_config(cfg)}


@router.post("/squareoff")
def tvx_squareoff(inst: Optional[str] = Query(None)):
    inst = _inst(inst)
    eng = get_engine()
    if eng is None:
        raise HTTPException(status_code=409, detail="TVX engine is not running")
    if not eng.request_manual_exit(inst):
        raise HTTPException(status_code=409, detail=f"no open {core.PRESETS[inst]['label']} position")
    return {"ok": True, "detail": "exit requested — fills at the next quote"}


EXPORT_COLS = ["id", "inst", "side", "symbol", "strike", "expiry", "entry_signal", "signal_bar",
               "entry_time_ist", "entry_price", "entry_src", "entry_bid", "entry_ask",
               "fut_px", "alert_px", "sl_price", "exit_time_ist", "exit_price", "exit_src",
               "exit_bid", "exit_ask", "exit_reason", "lots", "qty", "pnl_points",
               "mfe_points", "mae_points", "gross", "charges", "net",
               # ── TVX_HILO_20261007 ── trade range (option + its futures)
               "opt_high", "opt_high_time_ist", "opt_low", "opt_low_time_ist",
               "fut_symbol", "fut_exit", "fut_high", "fut_high_time_ist", "fut_low", "fut_low_time_ist",
               "fut_best_pts", "fut_worst_pts", "hilo_src", "hilo_status",
               # ── TVX_T2_20261007 ── target in force + what selling at T1 would have made
               "target_sig", "t1_time_ist", "t1_px", "t1_src", "t1_fut", "t1_pnl_points",
               # ── TVX_OPTS_20261008 ── T2 recorded like T1 when no target is set; SL off = blank
               "t2_time_ist", "t2_px", "t2_src", "t2_fut", "t2_pnl_points", "sl_points"]


def _ist(ts):
    return dt.datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


@router.post("/export")
def tvx_export(inst: Optional[str] = Query(None)):
    """inst = CRUDE (default) | NIFTY | ALL — a separate file per instrument
    (TVX_MULTI_20261009); ALL keeps both, with the inst column."""
    scope = (inst or "CRUDE").upper()
    if scope != "ALL":
        scope = _inst(scope)
    repo, _ = get_repo_and_store()
    from app.utils.app_paths import APP_HOME
    out_dir = APP_HOME / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"tvx_{scope.lower()}_trades_{dt.datetime.now(IST).strftime('%Y%m%d_%H%M%S')}.csv"
    rows = repo.all_closed(None if scope == "ALL" else scope)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            best, worst = core.fut_moves(r.get("side"), r.get("fut_px"), r.get("fut_high"), r.get("fut_low"))
            w.writerow(dict(r, entry_time_ist=_ist(r.get("entry_time")),
                            exit_time_ist=_ist(r.get("exit_time")),
                            opt_high_time_ist=_ist(r.get("opt_high_ts")), opt_low_time_ist=_ist(r.get("opt_low_ts")),
                            fut_high_time_ist=_ist(r.get("fut_high_ts")), fut_low_time_ist=_ist(r.get("fut_low_ts")),
                            fut_best_pts=best, fut_worst_pts=worst,
                            t1_time_ist=_ist(r.get("t1_ts")),
                            t1_pnl_points=(round(r["t1_px"] - r["entry_price"], 2) if r.get("t1_px") else ""),
                            t2_time_ist=_ist(r.get("t2_ts")),
                            t2_pnl_points=(round(r["t2_px"] - r["entry_price"], 2) if r.get("t2_px") else "")))
    return {"ok": True, "path": str(path), "rows": len(rows)}
