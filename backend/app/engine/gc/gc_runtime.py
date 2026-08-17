# backend/app/engine/gc/gc_runtime.py
#
# ── GC_V1 RUNTIME ── standalone async runtime (LD5): DAY_CYCLE perpetual
# loop (arm → one trading day → teardown → wait). Spot source = Kite
# HISTORICAL minute candles for NIFTY 50 (token 256265), re-fetched every
# minute: the full day rebuilds on every poll, which makes the replay-diff
# core restart-proof by construction and keeps the live feed in the same
# data family as the backtest corpus (LD7 divergence ledger).
#
# Chain: snapshot_weekly_chain (IC/TSG donor) refreshed every minute while
# no position is open (entries need fresh LTPs) and reused for marks while
# a position is open (the manager quotes its own legs via quote_fn).

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.utils.day_cycle import wait_for_arm_window, wait_for_teardown
from app.engine.gc.gc_manager import GcManager
from app.engine.gc.gc_live_core import STRATEGY_ID, norm_live_cfg, _hm_to_min

IST = timezone(timedelta(minutes=330))
NIFTY_INDEX_TOKEN = 256265
SESSION_OPEN_MIN = 9 * 60 + 15

_manager: Optional[GcManager] = None
_engine_up = False


def get_gc_manager() -> Optional[GcManager]:
    return _manager


def gc_engine_up() -> bool:
    return _engine_up


def _now() -> datetime:
    return datetime.now(IST)


def _day_start_epoch(d) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())


def _hist_minutes(kite, day) -> list:
    """Today's NIFTY 1m candles, START-stamped dict rows (ascending)."""
    frm = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    to = _now()
    rows = kite.historical_data(NIFTY_INDEX_TOKEN, frm, to, "minute") or []
    out = []
    for r in rows:
        ts = int(r["date"].timestamp())
        out.append({"ts": ts, "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"]})
    return out


def _prev_session_tail(kite, day, lookback: int):
    """Last `lookback` 1m candles + close of the PREVIOUS session (D1/C1
    references). Looks back up to 7 calendar days for the prior session."""
    from app.engine.gc.gc_live_core import to_tf_candles
    frm = datetime(day.year, day.month, day.day, tzinfo=IST) \
        - timedelta(days=7)
    to = datetime(day.year, day.month, day.day, tzinfo=IST)
    rows = kite.historical_data(NIFTY_INDEX_TOKEN, frm, to, "minute") or []
    if not rows:
        return [], None
    tail = [{"ts": int(r["date"].timestamp()), "open": r["open"],
             "high": r["high"], "low": r["low"], "close": r["close"]}
            for r in rows[-max(1, lookback):]]
    return to_tf_candles(tail), float(rows[-1]["close"])


def _chain_snapshot(broker_manager):
    """(rows [{symbol, opt_type, token}], quote_fn) — fail-closed empties."""
    try:
        from app.engine.ic.ic_selection import snapshot_weekly_chain
        kite = broker_manager.get_data_kite()
        if kite is None:
            write_audit_log("[GC][CHAIN_FAIL] data kite unavailable")
            return [], None
        api_key = getattr(kite, "api_key", None)
        token = getattr(kite, "access_token", None)
        expiry, rows, ltp = snapshot_weekly_chain(kite, api_key, token)
        out = [{"symbol": r.get("tradingsymbol") or r.get("symbol"),
                "opt_type": r.get("instrument_type") or r.get("opt_type"),
                "token": r.get("instrument_token") or r.get("token") or 0}
               for r in (rows or [])]

        def quote_fn(symbols):
            q = kite.ltp([f"NFO:{s}" for s in symbols]) or {}
            return {s: float((q.get(f"NFO:{s}") or {}).get("last_price") or 0)
                    for s in symbols}
        return out, quote_fn
    except Exception as e:
        write_audit_log(f"[GC][CHAIN_FAIL] {e!r}")
        return [], None


async def gc_v1_runtime(broker_manager, *args, **kwargs):
    """Perpetual DAY_CYCLE (TMA/PST pattern)."""
    global _manager, _engine_up
    _manager = GcManager()
    _engine_up = True
    write_audit_log("[GC][RUNTIME] up")

    # kill adapter registration (LD12) — best effort, never fatal
    try:
        from app.execution.kill_switch import register_adapter
        register_adapter(STRATEGY_ID, lambda: _manager.kill_adapter())
    except Exception as e:
        write_audit_log(f"[GC][KILL_REG_FAIL] {e!r}")

    last_run_day = None
    while True:
        try:
            day = await wait_for_arm_window("GC", last_run_day)
            cfg = _manager.cfg()
            kite = broker_manager.get_data_kite()
            if kite is None:
                write_audit_log("[GC][ARM_FAIL] data kite unavailable — "
                                "idle until teardown")
                last_run_day = day
                await wait_for_teardown()
                continue

            prev_tail, prev_close = _prev_session_tail(
                kite, day, cfg["sl_lookback"])
            chain_rows, quote_fn = _chain_snapshot(broker_manager)
            _manager.quote_fn = quote_fn
            try:
                from app.execution.zerodha_executor import ZerodhaOrderExecutor
                if _manager.executor is None:
                    _manager.executor = get_executor_for_strategy('GC_V1')
            except Exception as e:
                write_audit_log(f"[GC][EXECUTOR_BUILD_FAIL] {e!r} — PAPER "
                                f"unaffected; LIVE will alert at entry")
            _manager.arm_day(day.isoformat(), prev_tail, prev_close,
                             chain_rows)

            day_start = _day_start_epoch(day)
            exit_min = _hm_to_min(cfg["exit_time"], 15 * 60 + 15)
            while True:
                now = _now()
                minute_of_day = now.hour * 60 + now.minute
                if minute_of_day > exit_min + 2:
                    break
                if minute_of_day >= SESSION_OPEN_MIN + 1:
                    try:
                        rows = _hist_minutes(kite, day)
                        closed = [r for r in rows
                                  if r["ts"] + 60 <= int(now.timestamp())]
                        # refresh chain LTP universe while flat (entry prep)
                        if not _manager.position:
                            cr, qf = _chain_snapshot(broker_manager)
                            if cr:
                                _manager.chain_rows = cr
                            if qf:
                                _manager.quote_fn = qf
                        _manager.on_minute(closed, day_start)
                    except Exception as e:
                        write_audit_log(f"[GC][MINUTE_ERR] {e!r}")
                # sleep to just past the next minute boundary
                await asyncio.sleep(61 - now.second)
            _manager.teardown_day()
            write_audit_log(f"[GC][DAY_DONE] {day}")
            last_run_day = day
            await wait_for_teardown()
        except asyncio.CancelledError:
            _engine_up = False
            raise
        except Exception as e:
            write_audit_log(f"[GC][RUNTIME_ERR] {e!r}")
            await asyncio.sleep(60)