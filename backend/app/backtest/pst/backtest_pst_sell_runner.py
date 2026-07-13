# backend/app/backtest/pst/backtest_pst_sell_runner.py
#
# ── PST_SELL RUNNER ── PST_V1's signal inverted to option SELLING (SHORT).
# All decision logic lives in pst_sell_engine (position/day) + pst_v1_engine
# (build_signals, imported untouched) + pst_indicators; this file is plumbing:
# SPOT + option corpus access, per-signal option selection, charges, PSTTrade
# rows for persist_run, DIAG, progress/cancel. Caller persists (routes /
# queue_worker), same as PST_V1 / HA_SELL.
#
# FILL CONVENTIONS — IDENTICAL to PST_V1 (D6, no-lookahead pair):
#   * SELECTION at a signal (ts = 3m bar completion): premium < cap,
#     nearest-below, priced off the last COMPLETED 1m option candle (ts-60).
#   * ENTRY FILL: close of the NEXT 1m candle (stamped ts) — we SELL there.
#   * MONITORING starts at ts+60.
#   * spot anchor for the point-levels = the SIGNAL BAR's close (engine).
#   * Both legs fill on the SAME symbol; leg identity in persisted rows =
#     qty (L1 = 2×lots vs L2 = 1×lots; direction always SELL).
#   * Pivots need the PREVIOUS session's spot H/L/C; cross-day warmup via
#     warmup_sessions — all unchanged, it lives in build_signals/PST_V1.
#
# ROLE SWAP vs PST_V1 (D2/D3/D4 — full rationale in pst_sell_engine):
#   * tp column  = the premium level entry×(1−sl_pct/100) (seller TP).
#   * sl column  = NULL — the seller SL is a SPOT level, not an option
#     price (exact mirror of PST_V1, where tp was NULL for the same reason).
#     exit_reason SPOT_SL carries the story.
#   * exit_reason set: TP | SPOT_SL | EOD.
#   * gross = (entry − exit) × qty; charges_for_short_trade (STT on the
#     entry/sell leg).

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.pst.pst_v1_engine import build_signals
    from app.backtest.pst.pst_sell_engine import run_day_short
    from app.backtest.ic.ic_v1_engine import select_strike
except ImportError:  # standalone tests
    from pst_v1_engine import build_signals  # type: ignore
    from pst_sell_engine import run_day_short  # type: ignore
    from ic_v1_engine import select_strike  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65

DEFAULT_LEGS = [
    {"id": "L1", "lots": 2, "sl_pct": 15, "spot_tg_points": 20},
    {"id": "L2", "lots": 1, "sl_pct": 15, "spot_tg_points": 50},
]


def _hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


@dataclass
class PSTSellTrade:
    """persist_run non-hedge attribute surface (t.symbol is what it reads)."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                # always SELL
    entry_ts: int
    entry_price: float
    sl: Optional[float]           # NULL — seller SL is a SPOT level
    tp: Optional[float]           # premium TP level (entry × (1−sl_pct/100))
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]    # TP | SPOT_SL | EOD
    qty: int
    condition: str                # leg id + side + levels (in-memory only)
    ambiguous_fill: bool = False
    pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[PSTSellTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_pst"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.entry_ts or 0, x.condition)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    wins = sum(1 for n in nets if n > 0)
    return {
        "total_trades": len(closed), "wins": wins,
        "losses": sum(1 for n in nets if n < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(sum(nets), 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": sum(1 for t in closed if t.ambiguous_fill),
        "diag_pst": diag,
    }


def run_pst_sell_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "PST_SELL"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _impl(db_path=db_path, strategy_id=strategy_id,
                         underlying=underlying, date_from=date_from,
                         date_to=date_to, config_override=config_override,
                         progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _impl(db_path=db_path, strategy_id=strategy_id,
                     underlying=underlying, date_from=date_from,
                     date_to=date_to, config_override=config_override,
                     progress_cb=progress_cb, cancel_cb=cancel_cb)


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.event_bus.audit_logger import write_audit_log
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    cfg = config_override or {}
    prem_max = float(cfg.get("premium_max", 150) or 150)
    legs = [l for l in (cfg.get("legs") or DEFAULT_LEGS)
            if int(l.get("lots") or 0) > 0]
    side_mode = str(cfg.get("side_mode", "BOTH") or "BOTH")
    max_tpd = int(cfg.get("max_trades_per_day", 0) or 0)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
    cutoff_min = _hm_to_min(cfg.get("entry_cutoff_time", "15:00"), 15 * 60)
    sig_tf = int(cfg.get("signal_tf", 3) or 3)
    sma_cfg = cfg.get("sma") or {}
    st_cfg = cfg.get("supertrend") or {}
    if not legs:
        return {"run_id": None, "aborted": True,
                "reason": "PST_SELL needs at least one leg with lots > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── PST_SELL_CHARGES ── SHORT trade: STT on the entry/sell leg.
    try:
        from app.backtest.charges.charges_model import charges_for_short_trade
    except Exception:
        charges_for_short_trade = None

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    lo, hi = _day_start_epoch(date_from), _day_start_epoch(date_to) + 86400
    spot_days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
        ORDER BY d""", (underlying, lo, hi))]
    if len(spot_days) < 2:
        conn.close()
        return {"run_id": None, "aborted": True,
                "reason": "not enough NIFTY spot data — run the spot backfill",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    def spot_1m_for(d: date) -> List[dict]:
        ds = _day_start_epoch(d)
        return [dict(r) for r in cur.execute("""
            SELECT ts, open, high, low, close FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
            ORDER BY ts""", (underlying, ds, ds + 86400))]

    diag = {"days_total": len(spot_days), "days_traded": 0,
            "days_no_prev_session": 0, "days_uncovered": 0,
            "days_no_options": 0, "signals_total": 0, "signals_taken": 0,
            "signals_skipped_busy": 0, "signals_skipped_select": 0,
            "signals_skipped_side": 0, "signals_skipped_cap": 0,
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}
    trades: List[PSTSellTrade] = []
    prev_hlc: Optional[dict] = None
    prev_day: Optional[date] = None
    # PST_XDAY_WARMUP BEGIN
    prev_spot: Optional[List[dict]] = None      # prior session's raw 1m spot
    prev_day_start: Optional[int] = None        # its day_start epoch
    # PST_XDAY_WARMUP END

    for di, d in enumerate(spot_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(spot_days),
                         "date": d.isoformat()})
        spot = spot_1m_for(d)
        hlc = {"high": max(c["high"] for c in spot),
               "low": min(c["low"] for c in spot),
               "close": spot[-1]["close"]} if spot else None
        # PST_XDAY_WARMUP BEGIN — capture prior session BEFORE rotating state
        this_prev = prev_hlc
        this_prev_spot = prev_spot
        this_prev_day_start = prev_day_start
        prev_hlc, prev_day = hlc, d
        prev_spot = spot if spot else None
        prev_day_start = _day_start_epoch(d)
        # PST_XDAY_WARMUP END
        if this_prev is None:
            diag["days_no_prev_session"] += 1
            continue
        if not spot:
            continue

        day_start = _day_start_epoch(d)
        eod_ts = day_start + exit_min * 60
        universe = src.contracts_active_on_day(underlying, day_start)
        want_expiry = expected_expiry_for_day(d).isoformat()
        week = [c for c in universe if c.get("expiry") == want_expiry]
        if not universe:
            diag["days_no_options"] += 1
            continue
        if not week:
            diag["days_uncovered"] += 1
            continue
        meta = {c["tradingsymbol"]: c for c in week}
        by_side = {"CE": [c["tradingsymbol"] for c in week if c["instrument_type"] == "CE"],
                   "PE": [c["tradingsymbol"] for c in week if c["instrument_type"] == "PE"]}

        # PST_XDAY_WARMUP BEGIN — one prior session is ample warmup for
        # SuperTrend(10)@3m and SMA(9)@5m (~125 / 75 completed bars).
        warmup_sessions = []
        if this_prev_spot and this_prev_day_start is not None:
            warmup_sessions = [(this_prev_spot, this_prev_day_start)]
        # PST_XDAY_WARMUP END

        sig_res = build_signals(spot, day_start, this_prev,
                                signal_tf=sig_tf,
                                sma_period=int(sma_cfg.get("period", 9) or 9),
                                sma_tf=int(sma_cfg.get("tf", 5) or 5),
                                st_period=int(st_cfg.get("period", 10) or 10),
                                st_mult=float(st_cfg.get("mult", 2.0) or 2.0),
                                entry_cutoff_min=cutoff_min,
                                warmup_sessions=warmup_sessions)
        diag["signals_total"] += sig_res["diag"]["signals"]
        diag["blocked_warmup"] += sig_res["diag"]["blocked_warmup"]
        diag["blocked_gate"] += sig_res["diag"]["blocked_gate"]
        if not sig_res["signals"]:
            continue

        def select_option(side: str, ts: int) -> Optional[dict]:
            # SELECTION on the last completed candle (ts-60); FILL at the
            # next candle's close (ts); monitoring from ts+60. Identical to
            # PST_V1 — the seller sells the exact contract the buyer bought.
            cands = []
            for sym in by_side.get(side, []):
                cds = src.candles_1m_for_symbol_day(sym, day_start)
                px = None
                for x in cds:
                    if x.ts == ts - 60:
                        px = float(x.close)
                        break
                if px:
                    cands.append((sym, px))
            pick = select_strike(cands, prem_max)
            if pick is None:
                return None
            sym = pick[0]
            cds = src.candles_1m_for_symbol_day(sym, day_start)
            fill = next((float(x.close) for x in cds if x.ts == ts), None)
            if fill is None:
                return None
            return {"symbol": sym, "entry_price": fill,
                    "candles": [{"ts": x.ts, "open": x.open, "high": x.high,
                                 "low": x.low, "close": x.close}
                                for x in cds if x.ts >= ts + 60]}

        day = run_day_short(sig_res["signals"], legs, select_option, spot, eod_ts,
                            side_mode=side_mode, max_trades_per_day=max_tpd)
        dd = day["diag"]
        for k in ("signals_taken", "signals_skipped_busy",
                  "signals_skipped_select", "signals_skipped_side",
                  "signals_skipped_cap", "ambiguous"):
            diag[k] += dd[k]
        if dd["signals_taken"]:
            diag["days_traded"] += 1

        for lt in day["trades"]:
            qty = int(lt["lots"]) * LOT_SIZE
            # ── PST_SELL_PNL ── SHORT gross = (entry − exit) × qty.
            gross = (float(lt["entry_price"]) - float(lt["exit_price"])) * qty
            charges = 0.0
            if charges_for_short_trade is not None:
                try:
                    cr = charges_for_short_trade(
                        entry_price=lt["entry_price"],
                        exit_price=lt["exit_price"], qty=qty)
                    charges = float(getattr(cr, "total_charges", 0.0))
                    gross = float(getattr(cr, "gross_pnl", gross))
                except Exception:
                    charges = 0.0
            m = meta.get(lt["tradingsymbol"], {})
            trades.append(PSTSellTrade(
                tradingsymbol=lt["tradingsymbol"], symbol=lt["tradingsymbol"],
                instrument_type=lt["side"], strike=m.get("strike"),
                expiry=m.get("expiry"), direction="SELL",
                entry_ts=lt["entry_ts"] + 60,   # fill-candle completion
                entry_price=round(float(lt["entry_price"]), 2),
                sl=None,   # seller SL is a SPOT level, not an option price
                tp=(round(lt["tp_price"], 2) if lt["tp_price"] is not None else None),
                exit_ts=lt["exit_ts"],
                exit_price=round(float(lt["exit_price"]), 2),
                exit_reason=lt["exit_reason"], qty=qty,
                condition=f"{lt['leg']}·{lt['side']}·{lt.get('signal_levels','')}",
                ambiguous_fill=bool(lt["ambiguous_fill"]),
                pnl=round(gross, 2), charges=round(charges, 2),
                net_pnl=round(gross - charges, 2),
                gross=round(gross, 2), net=round(gross - charges, 2),
                ambiguous=bool(lt["ambiguous_fill"]),
            ))

    conn.close()
    try:
        src.close()
    except Exception:
        pass
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][PST_SELL] {underlying} {date_from}→{date_to}: "
        f"{diag['days_traded']}/{diag['days_total']} days traded, "
        f"{diag['signals_taken']}/{diag['signals_total']} signals taken, "
        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"warmupBlk {diag['blocked_warmup']} gateBlk {diag['blocked_gate']}")
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}