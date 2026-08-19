# backend/app/backtest/tma/backtest_tma_runner.py
#
# ── TMA_V1 RUNNER (v2 SPREAD) ── Triple-EMA (5/13/89 @5m) spot-signal
# trend-following CREDIT SPREADS (D11-D17, 2026-07-16):
#   C1 bullish (both EMAs above EMA89) → SELL PE (highest premium ≤ sell
#   cap) + BUY deeper-OTM PE hedge (≤ buy cap, e.g. ₹3); bearish → SELL CE
#   + BUY deeper CE. Both legs enter at the SAME signal minute and exit at
#   the SAME minute. Only the SELL leg carries SL/TP (short semantics: SL
#   when premium RISES, TP when it FALLS) and drives every exit (SL/TP/
#   XOVER/EOD/EOR/MTM_CUT on the SELL leg's mark); the hedge follows.
#   Per-leg lots (D12). HEDGE DEPTH (D15, IC_SYNTH_WING pattern): a ₹2-3
#   hedge often has no real strike in the ATM±10 corpus — wing_mode:
#   synthetic (default: BS-modeled, IV anchored to the cheapest REAL
#   same-side strike, spot from the REAL spot corpus, SYN- symbols, DIAG
#   counters, fail-open to real on any solver failure) | real_fallback
#   (cheapest real, flagged) | skip (signal skipped). Both trade modes run
#   the SAME monitor: INTRADAY is a hard close at exit_time every day.
# All decision logic lives in tma_v1_engine (pure, tested); this file is
# plumbing: SPOT + option corpus access, per-signal option selection with a
# PER-CONDITION premium cap, charges, TMATrade rows for persist_run, DIAG,
# progress/cancel. Caller persists (routes / queue_worker), same as PST/WICK.
#
# FILL CONVENTIONS (PST pair, verbatim — locked 2026-07-06):
#   * SELECTION at a signal (ts = 5m bar completion): premium < cap
#     (per-condition), highest-below, priced off the last COMPLETED 1m option
#     candle — the candle stamped ts-60.
#   * ENTRY FILL: close of the NEXT 1m candle (stamped ts, completes ts+60).
#   * MONITORING starts at ts+60.
#   * SL/TP on PREMIUM (% of entry, 0=off): SL on 1m LOW fills at level, TP
#     on 1m HIGH fills at level, same-minute collision → SL wins + ambiguous.
#   * XOVER exit: decided on a COMPLETED 5m spot bar; fills at the close of
#     the 1m option candle stamped at that bar's completion.
#   * EOD: close of the last option candle strictly before exit_time.
#   * Day requires BOTH spot and option data + expected weekly expiry in
#     corpus (fail closed, DIAG).
#
# ── CROSS-DAY WARMUP (TMA_XDAY_WARMUP) ─────────────────────────────────
#   EMA89@5m cannot warm inside one session (~75 bars < 89-bar seed). The
#   prior THREE spot sessions' 1m candles are passed to the engine as
#   warmup_sessions (PST_XDAY_WARMUP pattern): each aggregated against its
#   OWN day_start, completed bars concatenated, EMAs run continuous. Day 1 of
#   a range emits nothing (no valid EMA89), day 2 warms mid-session, day 3+
#   is fully warm at 09:15 — blocked_warmup / c1_stale report it honestly.

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.tma.tma_v1_engine import (
        build_signals, compute_state, monitor_position_day,
        warmup_bars, xover_exit_ts,
    )
    from app.backtest.pst.pst_indicators import aggregate
    from app.backtest.ic.ic_v1_engine import select_strike
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone tests
    from tma_v1_engine import (  # type: ignore
        build_signals, compute_state, monitor_position_day,
        warmup_bars, xover_exit_ts,
    )
    from pst_indicators import aggregate  # type: ignore
    from ic_v1_engine import select_strike  # type: ignore
    import ic_synth_wing as SW  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65          # NIFTY
TF_MIN = 5             # signal timeframe (5m) — fixed in v1, carried in cfg
WARMUP_DAYS = 3        # prior sessions fed to the EMA warmup (DEFAULT)
# ── TMA1_WARMUP_CFG 20260819 ── warmup depth is now sweepable via
# cfg["warmup_days"] (default WARMUP_DAYS = 3, i.e. unchanged behaviour).
# WHY: the 2026-08-18 spot stamp repair moved every 5m bucket by one
# minute; V1 lost 24% of net and gained 30% drawdown, while V2 — which
# feeds FIVE warmup sessions for its EMA144 seed — moved <5%. V2's own
# note says three sessions leave the seed "barely converged", so V1's
# sensitivity is plausibly seed convergence, not signal quality. This
# makes that testable. LIVE PARITY: tma_live_warmup.WARMUP_DAYS must be
# set to whatever value wins here, or live and backtest diverge.

DEFAULT_SELL = {"premium_max": 100, "lots": 1, "sl_pct": 30, "tp_pct": 50}
DEFAULT_BUY = {"premium_max": 3, "lots": 1}


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
class TMATrade:
    """persist_run non-hedge attribute surface (t.symbol is what it reads)."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                # SELL (monitored leg) | BUY (hedge)
    entry_ts: int
    entry_price: float
    sl: Optional[float]           # premium SL level (None = disabled)
    tp: Optional[float]           # premium TP level (None = disabled)
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]    # SL | TP | XOVER | EOD
    qty: int
    condition: str                # always "C1" in v2
    ambiguous_fill: bool = False
    pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)
    # NOTE: leg identity IS persisted per-row via direction (SELL = monitored
    # leg, BUY = hedge); per-leg aggregates live in summary_json
    # (diag_tma.sell_leg / diag_tma.hedge_leg).


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _leg_agg(trades: List[TMATrade], direction: str) -> dict:
    rows = [t for t in trades if t.direction == direction and t.exit_price is not None]
    return {"trades": len(rows),
            "wins": sum(1 for t in rows if t.net_pnl > 0),
            "net_pnl": round(sum(t.net_pnl for t in rows), 2)}


def _summarize(trades: List[TMATrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    diag = dict(diag)
    diag["sell_leg"] = _leg_agg(trades, "SELL")   # ── SPREAD_V2 ──
    diag["hedge_leg"] = _leg_agg(trades, "BUY")
    if not closed:
        s = _empty_summary()
        s["diag_tma"] = diag
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
        "diag_tma": diag,
    }


def run_tma_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "TMA_V1"
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


def _leg_cfg(raw: Optional[dict], defaults: dict) -> dict:
    raw = raw or {}
    out = dict(defaults)
    for k in out:
        if raw.get(k) is not None:
            out[k] = raw[k]
    typed = {"premium_max": float(out["premium_max"] or 0),
             "lots": int(out["lots"] or 0)}
    if "sl_pct" in out:
        typed["sl_pct"] = float(out["sl_pct"] or 0)
        typed["tp_pct"] = float(out["tp_pct"] or 0)
    return typed


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.event_bus.audit_logger import write_audit_log
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    cfg = config_override or {}
    # ── SPREAD_V2 ── c1: {sell: {premium_max, lots, sl_pct, tp_pct},
    # buy: {premium_max, lots}, max_trades_per_day}; wing_mode top-level.
    c1_raw = cfg.get("c1") or {}
    sell_cfg = _leg_cfg(c1_raw.get("sell"), DEFAULT_SELL)
    buy_cfg = _leg_cfg(c1_raw.get("buy"), DEFAULT_BUY)
    max_per_day = int(c1_raw.get("max_trades_per_day") or 0)
    wing_mode = str(cfg.get("wing_mode", "synthetic") or "synthetic").lower()
    if wing_mode not in ("synthetic", "real_fallback", "skip"):
        wing_mode = "synthetic"
    # ── POSITIONAL BEGIN ── INTRADAY (default) squares off daily at
    # exit_time; POSITIONAL carries positions overnight and applies exit_time
    # only on the contract's own expiry day (or the final range day, reason
    # EOR). Expiry dates come from the CONTRACT (corpus label), which the
    # era-aware expected_expiry_for_day gates at entry.
    trade_mode = str(cfg.get("trade_mode", "INTRADAY") or "INTRADAY").upper()
    if trade_mode not in ("INTRADAY", "POSITIONAL"):
        trade_mode = "INTRADAY"
    positional = trade_mode == "POSITIONAL"
    # ── NEG_MTM_EOD_CUT ── positional-only opt-in: every day at exit_time,
    # an open position marking below its entry premium (gross) is closed at
    # that mark (reason MTM_CUT); flat/positive positions carry as usual.
    cut_neg_mtm = positional and bool(cfg.get("cut_neg_mtm_eod", False))
    # ── POSITIONAL END ──
    ema_cfg = cfg.get("ema") or {}
    fast = int(ema_cfg.get("fast", 5) or 5)
    mid = int(ema_cfg.get("mid", 13) or 13)
    slow = int(ema_cfg.get("slow", 89) or 89)
    tf_min = int(cfg.get("tf_minutes", TF_MIN) or TF_MIN)
    tf_s = tf_min * 60

    sess_start_min = _hm_to_min(cfg.get("session_start", "09:15"), 9 * 60 + 15)
    sess_end_min = _hm_to_min(cfg.get("session_end", "15:00"), 15 * 60)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)

    # Fail LOUD on a nonsense session window (the "V3 no entries" lesson:
    # session misconfiguration must abort, not silently zero-trade).
    if not (sess_start_min < sess_end_min <= exit_min):
        return {"run_id": None, "aborted": True,
                "reason": (f"TMA_V1 session window invalid: start "
                           f"{cfg.get('session_start')} < end "
                           f"{cfg.get('session_end')} <= EOD "
                           f"{cfg.get('exit_time')} must hold"),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── SPREAD_V2 ── both legs must be sized: they enter together (D11/D12)
    if sell_cfg["lots"] <= 0 or buy_cfg["lots"] <= 0:
        return {"run_id": None, "aborted": True,
                "reason": "TMA_V1 spread needs lots > 0 on BOTH legs "
                          "(sell leg and buy hedge enter together)",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    try:
        from app.backtest.charges.charges_model import (
            charges_for_long_trade, charges_for_short_trade)
    except Exception:
        charges_for_long_trade = charges_for_short_trade = None

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
            "days_uncovered": 0, "days_no_options": 0,
            "bars5_today": 0, "c1_events": 0,
            "c1_stale": 0, "blocked_warmup": 0, "blocked_session": 0,
            "signals_total": 0, "signals_taken": 0,
            "skipped_busy": 0, "skipped_cap": 0,
            "skipped_select": 0, "skipped_hedge": 0, "ambiguous": 0,
            # ── SPREAD_V2 ── hedge sourcing funnel
            "hedge_real": 0, "hedge_synth": 0, "hedge_cheapest_fb": 0,
            "hedge_exit_fallbacks": 0, "wing_mode": None,
            "expiry_intrinsic_closes": 0,   # ── EXPIRY_INTRINSIC ──
            # ── POSITIONAL ── carry funnel (all zero in INTRADAY runs)
            "trade_mode": None, "carried_nights": 0, "expiry_closes": 0,
            "eor_closes": 0, "carry_gap_days": 0, "mtm_cuts": 0}
    diag["trade_mode"] = trade_mode
    diag["wing_mode"] = wing_mode
    trades: List[TMATrade] = []
    # ── POSITIONAL BEGIN ── cross-day state: one open position per condition
    # (D5 unchanged); busy_until persists ACROSS days.
    carry: Dict[str, dict] = {}
    pos_busy = {"C1": -1}
    last_range_day = spot_days[-1]

    def _one_row(*, symbol, side, strike, expiry, direction, entry_ts,
                 entry_price, sl, tp, exit_ts, exit_price, reason, qty,
                 ambiguous) -> None:
        # ── SPREAD_V2 ── SELL gross = (entry - exit) * qty; BUY = inverse.
        if direction == "SELL":
            gross = (float(entry_price) - float(exit_price)) * qty
            fn = charges_for_short_trade
        else:
            gross = (float(exit_price) - float(entry_price)) * qty
            fn = charges_for_long_trade
        charges = 0.0
        if fn is not None:
            try:
                cr = fn(entry_price=entry_price, exit_price=exit_price, qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        trades.append(TMATrade(
            tradingsymbol=symbol, symbol=symbol, instrument_type=side,
            strike=strike, expiry=expiry, direction=direction,
            entry_ts=entry_ts + 60, entry_price=round(float(entry_price), 2),
            sl=(round(sl, 2) if sl is not None else None),
            tp=(round(tp, 2) if tp is not None else None),
            exit_ts=exit_ts, exit_price=round(float(exit_price), 2),
            exit_reason=reason, qty=qty, condition="C1",
            ambiguous_fill=ambiguous,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=ambiguous,
        ))

    def _emit_pos_trade(res: dict, pos: dict) -> None:
        # ── SPREAD_V2 ── SELL row (monitored) + BUY hedge row at the SAME
        # timestamps. The hedge exits at its own price at the sell exit ts.
        _one_row(symbol=pos["symbol"], side=res["side"], strike=pos.get("strike"),
                 expiry=pos.get("expiry"), direction="SELL",
                 entry_ts=res["entry_ts"], entry_price=res["entry_price"],
                 sl=res["sl_price"], tp=res["tp_price"],
                 exit_ts=res["exit_ts"], exit_price=res["exit_price"],
                 reason=res["exit_reason"], qty=int(pos["lots"]) * LOT_SIZE,
                 ambiguous=bool(res["ambiguous_fill"]))
        hx = _hedge_exit_price(pos, res["exit_ts"])
        _one_row(symbol=pos["h_symbol"], side=res["side"], strike=pos.get("h_strike"),
                 expiry=pos.get("expiry"), direction="BUY",
                 entry_ts=res["entry_ts"], entry_price=pos["h_entry"],
                 sl=None, tp=None,
                 exit_ts=res["exit_ts"], exit_price=hx,
                 reason=res["exit_reason"], qty=int(pos["h_lots"]) * LOT_SIZE,
                 ambiguous=False)
        if res["exit_reason"] == "EOD" and pos.get("expiry_close"):
            diag["expiry_closes"] += 1
        if res["exit_reason"] == "EOR":
            diag["eor_closes"] += 1
        if res["exit_reason"] == "MTM_CUT":       # ── NEG_MTM_EOD_CUT ──
            diag["mtm_cuts"] += 1
        if res["ambiguous_fill"]:
            diag["ambiguous"] += 1
    # ── POSITIONAL END ──

    # ── SPREAD_V2 HEDGE BEGIN ── sourcing + exit pricing for the buy leg.
    def _spot_close_at(ts: int) -> Optional[float]:
        r = cur.execute(
            "SELECT close FROM backtest_candles_1m WHERE underlying=? AND "
            "instrument_type='SPOT' AND ts=?", (underlying, ts)).fetchone()
        if r:
            return float(r[0])
        r = cur.execute(
            "SELECT close FROM backtest_candles_1m WHERE underlying=? AND "
            "instrument_type='SPOT' AND ts<? ORDER BY ts DESC LIMIT 1",
            (underlying, ts)).fetchone()
        return float(r[0]) if r else None

    def _expiry_ts(expiry_iso: str) -> int:
        return _day_start_epoch(date.fromisoformat(expiry_iso)) + (15 * 60 + 30) * 60

    def _select_hedge(side: str, ts: int, ladder: List[tuple],
                      meta: dict, want_expiry: str, day_start: int) -> Optional[dict]:
        # ladder = [(sym, close@ts-60)] for the SAME side (real strikes).
        pick = select_strike(ladder, buy_cfg["premium_max"])
        if pick is not None:
            sym = pick[0]
            cds = src.candles_1m_for_symbol_day(sym, day_start)
            fill = next((float(x.close) for x in cds if x.ts == ts), None)
            if fill is not None:
                diag["hedge_real"] += 1
                return {"kind": "real", "symbol": sym, "entry": fill,
                        "strike": (meta.get(sym) or {}).get("strike"),
                        "iv": None}
        if wing_mode == "skip":
            return None
        # synthetic: IV anchored to the cheapest REAL same-side strike at
        # the selection minute; spot from the REAL spot corpus; strike walked
        # OTM strictly beyond real data (IC pattern).
        if wing_mode == "synthetic" and ladder:
            edge_sym, edge_px = min(ladder, key=lambda c: (c[1], c[0]))
            edge_k = (meta.get(edge_sym) or {}).get("strike")
            spot_now = _spot_close_at(ts)
            if edge_k and edge_px > 0 and spot_now:
                is_call = side == "CE"
                tau = SW.tau_years(ts, _expiry_ts(want_expiry))
                iv = SW.implied_vol(edge_px, is_call, spot_now, float(edge_k), tau)
                if iv is not None:
                    start = float(edge_k) + (50.0 if is_call else -50.0)
                    sol = SW.solve_wing_strike(is_call, spot_now, tau, iv,
                                               target_premium=buy_cfg["premium_max"],
                                               start_strike=start)
                    if sol is not None:
                        k, px = sol
                        diag["hedge_synth"] += 1
                        return {"kind": "synth",
                                "symbol": SW.synth_symbol(underlying, want_expiry,
                                                          k, is_call),
                                "entry": px, "strike": k, "iv": iv}
        # fail open to reality: cheapest real strike, flagged
        fb = select_strike(ladder, buy_cfg["premium_max"], fallback_cheapest=True)
        if fb is None:
            return None
        sym = fb[0]
        cds = src.candles_1m_for_symbol_day(sym, day_start)
        fill = next((float(x.close) for x in cds if x.ts == ts), None)
        if fill is None:
            return None
        diag["hedge_cheapest_fb"] += 1
        return {"kind": "real", "symbol": sym, "entry": fill,
                "strike": (meta.get(sym) or {}).get("strike"), "iv": None}

    def _hedge_exit_price(pos: dict, exit_ts: int) -> float:
        # exit day may differ from entry day (positional): price on the exit
        # day. Real: candle at exit_ts, else last candle ≤ exit_ts that day.
        # Synth: BS at the exit minute, IV = entry anchor (re-anchoring per
        # exit would need that day's ladder; entry IV is the documented
        # approximation). Any failure → entry price, DIAG-flagged.
        eday = exit_ts - ((exit_ts + IST) % 86400)
        if pos.get("h_kind") == "real":
            cds = src.candles_1m_for_symbol_day(pos["h_symbol"], eday)
            px = next((float(x.close) for x in cds if x.ts == exit_ts), None)
            if px is None:
                before = [float(x.close) for x in cds if x.ts <= exit_ts]
                px = before[-1] if before else None
            if px is not None:
                return px
        else:
            spot_x = _spot_close_at(exit_ts)
            if spot_x and pos.get("h_iv"):
                tau = SW.tau_years(exit_ts, _expiry_ts(pos["expiry"]))
                return SW.price_wing(pos["h_side_is_call"], spot_x,
                                     float(pos["h_strike"]), tau, pos["h_iv"])
        diag["hedge_exit_fallbacks"] += 1
        return float(pos["h_entry"])
    # ── SPREAD_V2 HEDGE END ──
    # TMA_XDAY_WARMUP BEGIN — rolling window of the prior WARMUP_DAYS sessions
    warm_hist: List[tuple] = []   # [(spot_1m, day_start), ...] oldest-first
    # ── TMA1_WARMUP_CFG ── clamp 1..10; absent key == legacy 3
    _warmup_days = max(1, min(10, int(cfg.get("warmup_days") or WARMUP_DAYS)))
    # TMA_XDAY_WARMUP END

    for di, d in enumerate(spot_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(spot_days),
                         "date": d.isoformat()})
        spot = spot_1m_for(d)
        day_start = _day_start_epoch(d)
        # TMA_XDAY_WARMUP BEGIN — capture prior sessions BEFORE rotating
        warmup_sessions = list(warm_hist)
        if spot:
            warm_hist.append((spot, day_start))
            if len(warm_hist) > _warmup_days:   # ── TMA1_WARMUP_CFG ──
                warm_hist.pop(0)
        # TMA_XDAY_WARMUP END
        if not spot:
            diag["carry_gap_days"] += len(carry)   # ── POSITIONAL ──
            continue

        session0 = day_start + (9 * 60 + 15) * 60
        entry_start_ts = day_start + sess_start_min * 60
        entry_end_ts = day_start + sess_end_min * 60
        eod_ts = day_start + exit_min * 60

        # continuous 5m stream: prior sessions then today (completed bars).
        # Computed BEFORE the option-universe gate: POSITIONAL carry
        # monitoring needs today's crossover state even on days where entries
        # are impossible (uncovered/no-options days). Pure computation — the
        # INTRADAY result path is unchanged.
        warm5 = warmup_bars(warmup_sessions, tf_min)
        today5 = [b for b in aggregate(spot, tf_min, day_start) if b["complete"]]
        bars5 = warm5 + today5
        state = compute_state(bars5, fast=fast, mid=mid, slow=slow)

        def xover_fn(cond: str, side: str, after_ts: int) -> Optional[int]:
            return xover_exit_ts(bars5, state, cond, side, after_ts, tf_s=tf_s)

        def opt_day_candles(sym: str) -> List[dict]:
            return [{"ts": x.ts, "open": x.open, "high": x.high,
                     "low": x.low, "close": x.close}
                    for x in src.candles_1m_for_symbol_day(sym, day_start)]

        # ── EXPIRY_INTRINSIC ── intrinsic mark at the hard-close bound off
        # the spot corpus; used when the held symbol's candles are missing OR
        # ran out before the bound (band-exit mid-day: the sold strike went
        # far OTM and stopped being captured — exactly the winning case).
        def _intrinsic_at_bound(pos, bound_ts):
            for c_ in reversed(spot):
                if c_["ts"] < bound_ts:
                    k = float(pos.get("strike") or 0)
                    sp = float(c_["close"])
                    intr = (sp - k) if pos["side"] == "CE" else (k - sp)
                    return int(c_["ts"]), round(max(0.05, intr), 2)
            return None

        def _patch_partial_day_close(res, pos, hard_ts):
            # monitor exhausted the day's candles BEFORE the bound and fell
            # back to a stale last mark → re-mark at intrinsic at the bound
            if (res is None or hard_ts is None
                    or res["exit_reason"] not in ("EOD", "EOR")
                    or res["exit_ts"] >= hard_ts - 120):
                return res
            ip = _intrinsic_at_bound(pos, hard_ts)
            if ip is None:
                return res
            diag["expiry_intrinsic_closes"] += 1
            res = dict(res)
            res["exit_ts"], res["exit_price"] = ip
            return res

        # ── POSITIONAL BEGIN ── advance carried positions through today
        # BEFORE any entry gating: exits release the slot (busy from the
        # next minute); survivors block their condition for the whole day.
        if positional and carry:
            for cond in list(carry.keys()):
                pos = carry[cond]
                hard_ts, hard_reason = None, "EOD"
                if pos.get("expiry") == d.isoformat():
                    hard_ts, pos["expiry_close"] = eod_ts, True
                elif d == last_range_day:
                    hard_ts, hard_reason = eod_ts, "EOR"
                cands = opt_day_candles(pos["symbol"])
                if not cands and hard_ts is None:
                    diag["carry_gap_days"] += 1
                    diag["carried_nights"] += 1
                    continue                     # no data, position persists
                # ── EXPIRY_INTRINSIC BEGIN ── the held strike slid OUT of
                # the capture band by its own expiry day (winning shorts do
                # this systematically — spot ran away from them). Marking
                # the close at the previous day's stale price mis-prices the
                # exit by the whole overnight decay. At exit_time on expiry
                # day, time value on a far-OTM is ~nil, so the honest,
                # model-free mark is INTRINSIC off the spot corpus (always
                # present), floored at 0.05, stamped at the actual expiry-
                # day bound — not at yesterday's last candle.
                if not cands and hard_ts is not None:
                    ip = _intrinsic_at_bound(pos, hard_ts)
                    if ip is not None:
                        sp_ts, px = ip
                        diag["expiry_intrinsic_closes"] += 1
                        _emit_pos_trade({"side": pos["side"],
                                         "entry_ts": pos["entry_ts"],
                                         "entry_price": pos["entry_price"],
                                         "sl_price": pos.get("sl_price"),
                                         "tp_price": pos.get("tp_price"),
                                         "exit_ts": sp_ts,
                                         "exit_price": px,
                                         "exit_reason": hard_reason,
                                         "ambiguous_fill": False}, pos)
                        pos_busy[cond] = sp_ts + 60
                        del carry[cond]
                        continue
                # ── EXPIRY_INTRINSIC END ──
                pos["watch_from"] = 0
                xts = xover_fn(cond, pos["trend_side"], session0)
                res = monitor_position_day(
                    pos, cands, xts, hard_ts, hard_reason,
                    # ── NEG_MTM_EOD_CUT ── only on days with no hard close
                    mtm_cut_ts=(eod_ts if (cut_neg_mtm and hard_ts is None)
                                else None))
                res = _patch_partial_day_close(res, pos, hard_ts)   # ── EXPIRY_INTRINSIC ──
                if res is not None:
                    _emit_pos_trade(res, pos)
                    pos_busy[cond] = res["exit_ts"] + 60
                    del carry[cond]
                else:
                    diag["carried_nights"] += 1
                    pos_busy[cond] = day_start + 86400   # busy all day
        # ── POSITIONAL END ──

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

        sig_res = build_signals(bars5, len(warm5), session0,
                                entry_start_ts, entry_end_ts,
                                tf_s=tf_s, state=state,
                                fast=fast, mid=mid, slow=slow)
        for k in ("bars5_today", "c1_events", "c1_stale",
                  "blocked_warmup", "blocked_session"):
            diag[k] += sig_res["diag"][k]
        diag["signals_total"] += sig_res["diag"]["signals"]
        if not sig_res["signals"]:
            continue

        def select_option(side: str, ts: int, prem_max: float) -> Optional[dict]:
            # SELECTION on the last completed candle (ts-60); FILL at the
            # next candle's close (ts); monitoring from ts+60.
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

        # ── SPREAD_V2 ENTRY LOOP ── one slot, both modes. INTRADAY is the
        # same machinery with a hard close at exit_time EVERY day. sell_side
        # = OPPOSITE of the trend side (bullish → sell PE); the hedge is the
        # same option type deeper OTM. Both legs fill at the signal minute.
        taken_today = 0
        for sig in sorted(sig_res["signals"], key=lambda x: x["ts"]):
            cond = "C1"
            if sig["ts"] < pos_busy[cond]:
                diag["skipped_busy"] += 1
                continue
            if max_per_day and taken_today >= max_per_day:
                diag["skipped_cap"] += 1
                continue
            sell_side = "PE" if sig["side"] == "CE" else "CE"
            sel = select_option(sell_side, sig["ts"], sell_cfg["premium_max"])
            if sel is None:
                diag["skipped_select"] += 1
                continue
            # hedge ladder: same side, priced at the selection minute (ts-60)
            ladder = []
            for hsym in by_side.get(sell_side, []):
                if hsym == sel["symbol"]:
                    continue
                hpx = next((float(x.close) for x in
                            src.candles_1m_for_symbol_day(hsym, day_start)
                            if x.ts == sig["ts"] - 60), None)
                if hpx:
                    ladder.append((hsym, hpx))
            hedge = _select_hedge(sell_side, sig["ts"], ladder, meta,
                                  want_expiry, day_start)
            if hedge is None:
                diag["skipped_hedge"] += 1
                continue
            m = meta.get(sel["symbol"], {})
            ep = float(sel["entry_price"])
            slp, tpp = sell_cfg["sl_pct"], sell_cfg["tp_pct"]
            # ── SLTP_UNITS ── per-field: sl_unit / tp_unit = PCT | PTS
            # (falling back to the legacy shared sl_tp_unit, then PCT). PTS
            # = absolute rupee offsets on the SOLD premium. Same short
            # semantics either way — SL above entry, TP below, 0 = off,
            # TP floored at 0.05. Mixing (e.g. SL % + TP pts) is supported.
            # units per field: PCT (% of entry) | PTS (₹ offset from entry)
            # | ABS (absolute premium LEVEL — e.g. TP 10 = buy back when the
            # sold premium decays TO ₹10; SL 200 = exit when it RISES to
            # ₹200). ABS levels on the wrong side of entry are fail-loud
            # nonsense: an ABS TP >= entry or ABS SL <= entry would trigger
            # instantly, so they're clamped off (None) rather than fired.
            _sraw = cfg.get("c1", {}).get("sell", {})
            _legacy = str(_sraw.get("sl_tp_unit") or "PCT").upper()
            _su = str(_sraw.get("sl_unit") or _legacy).upper()
            _tu = str(_sraw.get("tp_unit") or _legacy).upper()
            if slp > 0:
                if _su == "ABS":
                    sl_level = slp if slp > ep else None
                elif _su == "PTS":
                    sl_level = ep + slp
                else:
                    sl_level = ep * (1 + slp / 100.0)
            else:
                sl_level = None
            if tpp > 0:
                if _tu == "ABS":
                    tp_level = max(0.05, tpp) if tpp < ep else None
                elif _tu == "PTS":
                    tp_level = max(0.05, ep - tpp)
                else:
                    tp_level = max(0.05, ep * (1 - tpp / 100.0))
            else:
                tp_level = None
            pos = {"cond": cond, "side": sell_side, "trend_side": sig["side"],
                   "action": "SELL",
                   "symbol": sel["symbol"], "lots": sell_cfg["lots"],
                   "strike": m.get("strike"), "expiry": m.get("expiry"),
                   "entry_ts": sig["ts"], "entry_price": ep,
                   # SHORT premium: SL when it RISES, TP when it FALLS
                   "sl_price": sl_level,   # ── SLTP_UNITS ──
                   "tp_price": tp_level,
                   "watch_from": sig["ts"] + 60,
                   "last_close": ep, "last_ts": sig["ts"],
                   # hedge leg (follows the sell leg's timestamps)
                   "h_symbol": hedge["symbol"], "h_entry": float(hedge["entry"]),
                   "h_strike": hedge.get("strike"), "h_lots": buy_cfg["lots"],
                   "h_kind": hedge["kind"], "h_iv": hedge.get("iv"),
                   "h_side_is_call": sell_side == "CE"}
            if positional:
                hard_ts, hard_reason = None, "EOD"
                if pos["expiry"] == d.isoformat():
                    hard_ts, pos["expiry_close"] = eod_ts, True
                elif d == last_range_day:
                    hard_ts, hard_reason = eod_ts, "EOR"
            else:
                hard_ts, hard_reason = eod_ts, "EOD"   # daily square-off
                if pos["expiry"] == d.isoformat():
                    pos["expiry_close"] = True
            xts = xover_fn(cond, sig["side"], sig["ts"])
            res = monitor_position_day(
                pos, sel["candles"], xts, hard_ts, hard_reason,
                mtm_cut_ts=(eod_ts if (cut_neg_mtm and hard_ts is None)
                            else None))
            res = _patch_partial_day_close(res, pos, hard_ts)   # ── EXPIRY_INTRINSIC ──
            taken_today += 1
            diag["signals_taken"] += 1
            if res is not None:
                _emit_pos_trade(res, pos)
                pos_busy[cond] = res["exit_ts"] + 60
            else:
                diag["carried_nights"] += 1
                carry[cond] = pos
                pos_busy[cond] = day_start + 86400
        if taken_today:
            diag["days_traded"] += 1

    # ── POSITIONAL ── safety net: a position can only still be open here
    # if the final range days had no usable data; close at the last carried
    # price so nothing outlives the simulation.
    for cond, pos in list(carry.items()):
        _emit_pos_trade({"side": pos["side"], "entry_ts": pos["entry_ts"],
                         "entry_price": pos["entry_price"],
                         "sl_price": pos.get("sl_price"),
                         "tp_price": pos.get("tp_price"),
                         "exit_ts": pos.get("last_ts", pos["entry_ts"]),
                         "exit_price": pos.get("last_close", pos["entry_price"]),
                         "exit_reason": "EOR", "ambiguous_fill": False}, pos)
        del carry[cond]

    conn.close()
    try:
        src.close()
    except Exception:
        pass
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][TMA_V1][{trade_mode}] {underlying} {date_from}→{date_to}: "
        f"{diag['days_traded']}/{diag['days_total']} days traded, "
        f"{diag['signals_taken']}/{diag['signals_total']} signals taken, "
        f"{len(trades)} rows ({diag['hedge_real']}R/{diag['hedge_synth']}S"
        f"/{diag['hedge_cheapest_fb']}FB hedges), net {summary['net_pnl']}, "
        f"warmupBlk {diag['blocked_warmup']} stale {diag['c1_stale']} "
        f"sessBlk {diag['blocked_session']}")
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}