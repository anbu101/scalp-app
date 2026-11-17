# backend/app/api/live_replay_routes.py
#
# ── TRADE_REPLAY_LIVE_20261017 ── GET /api/replay/trade?scope=trades|analytics&key=…
# Trade Replay for the Trades and Analytics pages. ADMIN ONLY: api_server
# mounts this router with dependencies=[Depends(_require_admin_ui)] (the
# Backtest router's wall), so a non-admin licence gets 403 before this code
# runs. Payload = the Backtest replay payload (see app/backtest/replay/).

from fastapi import APIRouter, HTTPException, Query

from app.event_bus.audit_logger import write_audit_log

router = APIRouter(prefix="/api/replay", tags=["trade-replay"])


@router.get("/trade")
def live_trade_replay(scope: str = Query(..., description="trades | analytics"),
                      key: str = Query(..., description="the page's row id"),
                      fill: int = Query(1, description="1 = add missing bars from Kite to the corpus"),
                      live: int = Query(0, description="1 = forming bar + provisional indicators (open trade, in session)")):   # ── TRADE_REPLAY_STREAM_20261017 ──
    from app.backtest.replay.live_replay import build_live_replay
    try:
        out = build_live_replay(scope, key, fill=bool(fill), live=bool(live))
    except Exception as e:
        write_audit_log(f"[REPLAY][LIVE] {scope}/{key} failed: {e!r}")
        raise HTTPException(500, f"replay failed: {type(e).__name__}: {e}")
    if not out.get("ok"):
        raise HTTPException(int(out.get("status") or 404), out.get("error") or "not found")
    return out


# ── TRADE_REPLAY_STREAM_20261017 ── Dashboard: which Trades/Analytics row is
# this? → composite key ("t|…" Trades, "a|…" Analytics) + the strategy's rows
# for Prev/Next. By strategy (+book): its newest open group, else newest.
# By symbol + entry time (±90 s): that row. Same admin wall (router-level).
@router.get("/find")
def find_trade(strategy: str = Query(None), symbol: str = Query(None),
               entry_ts: int = Query(None), book: str = Query(None)):
    from app.backtest.replay.live_replay import find_keys
    try:
        out = find_keys(strategy=strategy, symbol=symbol, entry_ts=entry_ts, book=book)
    except Exception as e:
        write_audit_log(f"[REPLAY][FIND] {strategy}/{symbol}/{entry_ts} failed: {e!r}")
        raise HTTPException(500, f"lookup failed: {type(e).__name__}: {e}")
    if not out.get("ok"):
        raise HTTPException(int(out.get("status") or 404), out.get("error") or "not found")
    return out


# ── TRADE_REPLAY_WATCH_20261021 ── the strategy's live chart with NO open
# position: spot + its indicators (forming bar + provisional values in
# session), so an entry can be anticipated. Same admin wall (router-level).
# ── PHONE_METER_20261117 ── the phone Dashboard's Entry/Exit meter: the SAME
# lookup and builders as the Live chart (find → open position live, else WATCH),
# but only the meter, its checklist and the live clock go back — ~2 KB, not the
# ~100 KB candle payload. Same admin wall (router-level).
METER_LIVE_KEYS = ("active", "poll_ms", "now_ts", "stale", "no_feed", "spot_ltp", "spot_age_s")


@router.get("/meter")
def strategy_meter(strategy: str = Query(...)):
    from app.backtest.replay.live_replay import build_live_replay, build_watch, find_keys
    try:
        f = find_keys(strategy=strategy)
        if f.get("ok"):
            pre, _, rest = str(f["key"]).partition("|")
            out = (build_watch(f.get("strategy_id") or strategy, fill=True, live=True) if pre == "w"
                   else build_live_replay("analytics" if pre == "a" else "trades", rest, fill=True, live=True))
        else:
            out = f
    except Exception as e:
        write_audit_log(f"[REPLAY][METER] {strategy} failed: {e!r}")
        raise HTTPException(500, f"meter failed: {type(e).__name__}: {e}")
    if not out.get("ok"):
        raise HTTPException(int(out.get("status") or 404), out.get("error") or "not found")
    lv = out.get("live") or {}
    return {"ok": True, "strategy_id": out.get("strategy_id") or f.get("strategy_id"), "key": f.get("key"),
            "watch": str(f.get("key", "")).startswith("w|"), "waiting": out.get("waiting"),
            "live": {k: lv.get(k) for k in METER_LIVE_KEYS}}


# ── DASH_CALM_20261117 ── every card's meter on the redesigned Dashboard in ONE call:
# GET /api/replay/meters?strategies=IC_V2,TMA_V2,… → {ok, meters: {sid: <same shape as
# /meter>}, errors: {sid: text}}. A strategy that fails does not fail the others.
# Guard rails for a machine that also trades: at most METERS_MAX strategies a call;
# each strategy's reading is cached METERS_TTL_S; a Kite top-up of its bars (fill) runs
# at most once per METERS_FILL_EVERY_S per strategy — the other reads use the corpus
# + the live forming bar (fill off), so a card grid never multiplies Kite calls.
import threading as _threading
import time as _time

METERS_MAX = 24
METERS_TTL_S = 8.0
METERS_FILL_EVERY_S = 60.0
_METERS = {}                 # sid -> (monotonic ts, payload)
_METERS_FILLED = {}          # sid -> monotonic ts of the last fill=True build
_METERS_LOCK = _threading.Lock()


def _meter_slim(strategy: str, fill: bool) -> dict:
    from app.backtest.replay.live_replay import build_live_replay, build_watch, find_keys
    f = find_keys(strategy=strategy)
    if not f.get("ok"):
        return {"ok": False, "error": f.get("error") or "not found"}
    pre, _, rest = str(f["key"]).partition("|")
    out = (build_watch(f.get("strategy_id") or strategy, fill=fill, live=True) if pre == "w"
           else build_live_replay("analytics" if pre == "a" else "trades", rest, fill=fill, live=True))
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error") or "not found"}
    lv = out.get("live") or {}
    return {"ok": True, "strategy_id": out.get("strategy_id") or f.get("strategy_id"), "key": f.get("key"),
            "watch": pre == "w", "waiting": out.get("waiting"),
            "live": {k: lv.get(k) for k in METER_LIVE_KEYS}}


@router.get("/meters")
def strategy_meters(strategies: str = Query(..., description="comma-separated strategy ids")):
    ids = []
    for s in (strategies or "").split(","):
        s = s.strip()
        if s and s not in ids:
            ids.append(s)
    ids = ids[:METERS_MAX]
    meters, errors = {}, {}
    for sid in ids:
        now = _time.monotonic()
        with _METERS_LOCK:
            hit = _METERS.get(sid)
            if hit and now - hit[0] < METERS_TTL_S:
                meters[sid] = hit[1]
                continue
            fill = now - _METERS_FILLED.get(sid, -1e9) >= METERS_FILL_EVERY_S
        try:
            m = _meter_slim(sid, fill)
        except Exception as e:
            write_audit_log(f"[REPLAY][METERS] {sid} failed: {e!r}")
            errors[sid] = f"{type(e).__name__}: {e}"[:200]
            continue
        if not m.get("ok"):
            errors[sid] = m.get("error") or "not found"
            continue
        with _METERS_LOCK:
            _METERS[sid] = (_time.monotonic(), m)
            if fill:
                _METERS_FILLED[sid] = _time.monotonic()
        meters[sid] = m
    return {"ok": True, "meters": meters, "errors": errors}


@router.get("/watch")
def watch_strategy(strategy: str = Query(...), fill: int = Query(1), live: int = Query(0)):
    from app.backtest.replay.live_replay import build_watch
    try:
        out = build_watch(strategy, fill=bool(fill), live=bool(live))
    except Exception as e:
        write_audit_log(f"[REPLAY][WATCH] {strategy} failed: {e!r}")
        raise HTTPException(500, f"watch failed: {type(e).__name__}: {e}")
    if not out.get("ok"):
        raise HTTPException(int(out.get("status") or 404), out.get("error") or "not found")
    return out
