# backend/app/backtest/tma/backtest_tma_v2_runner.py
#
# ── TMA_V2 RUNNER ── Four-EMA STACK (13/55/89/144 @5m) spot-signal
# strategy, option execution BUY or SELL (D1-D8, confirmed 2026-08-16):
#   E1 bearish stack (13<55<89<144) → BUY PE (mode BUY) | SELL CE + BUY
#   deeper-OTM CE hedge (mode SELL). E2 bullish stack mirrored (BUY CE |
#   SELL PE + PE hedge). SELL mode is the TMA_V1 SPREAD mechanics
#   VERBATIM: both legs enter at the SAME signal minute and exit at the
#   SAME minute; only the SELL leg carries SL/TP and drives every exit;
#   the hedge follows at its own price (wing_mode synthetic default,
#   IC_SYNTH_WING). BUY mode is a SINGLE long leg — no hedge (nothing to
#   margin-hedge on a long), long SL/TP semantics (SL below entry, TP
#   above), same fill/monitor machinery via monitor_position_day's
#   action flag.
#   ONE open position at a time, either direction (D2 — single "S1" slot,
#   unlike V1's per-condition independence).
#   Optional crossover exit (D3/D4, xover_exit_enabled default ON):
#   E1 exits at EMA13 >= EMA89 (inclusive), E2 at EMA13 <= EMA89 — 55 and
#   144 play no exit role. Toggle OFF → SL/TP/EOD (+EOR/expiry) only.
# All decision logic lives in tma_v2_engine (pure, tested); this file is
# plumbing: SPOT + option corpus access, per-signal option selection with
# a premium cap, charges, TMATrade rows for persist_run, DIAG,
# progress/cancel. Caller persists (routes / queue_worker), same as V1.
#
# FILL CONVENTIONS (PST pair, verbatim — same as TMA_V1):
#   * SELECTION at a signal (ts = 5m bar completion): premium < cap,
#     highest-below, priced off the last COMPLETED 1m option candle — the
#     candle stamped ts-60.
#   * ENTRY FILL: close of the NEXT 1m candle (stamped ts, completes ts+60).
#   * MONITORING starts at ts+60.
#   * SL/TP on PREMIUM via sl_tp_levels (units PCT|PTS|ABS per field, 0=off).
#   * XOVER exit: decided on a COMPLETED 5m spot bar; fills at the close of
#     the 1m option candle stamped at that bar's completion.
#   * EOD: close of the last option candle strictly before exit_time.
#   * Day requires BOTH spot and option data + expected weekly expiry in
#     corpus (fail closed, DIAG).
#
# ── CROSS-DAY WARMUP (TMA2_XDAY_WARMUP) ────────────────────────────────
#   EMA144@5m needs 144 bars for the SMA seed alone (~2 sessions). FIVE
#   prior spot sessions (D5 — vs V1's three) are passed to the engine as
#   warmup_sessions: each aggregated against its OWN day_start, completed
#   bars concatenated, EMAs run continuous. Days 1-2 of a range emit
#   little/nothing (blocked_warmup reports it honestly); day 3+ carries a
#   converged EMA144 from 09:15.
#
# ── 2026-CHOP KNOBS ─────────────────────────────────────────────────────
#   Three optional entry/exit refinements from the 2026 drawdown
#   post-mortem, all default-OFF (unset config = original V2 semantics):
#   xover_exit_ref (ANY EMA period; 89|55 are the studied presets),
#   min_extension_pct / max_extension_pct (the entry-extension BAND on
#   the 13-89 fan width — floor rejects undeveloped stacks, ceiling
#   rejects exhausted ones; both 0=off), ema144_slope_gate
#   (bool). Rationale + mechanics documented in tma_v2_engine.py.
#
# ── SL_STREAK_COOLDOWN (2026-08-16) ─────────────────────────────────────
#   Optional chop circuit-breaker from the 2026 monthly-shape study:
#   after sl_streak_count CONSECUTIVE SL exits, skip NEW ENTRIES for
#   sl_streak_cooldown_days CALENDAR days from the triggering exit.
#   Default sl_streak_count=0 = OFF (byte-identical). Design notes:
#   * Time-bounded, never month-bounded — a monthly loss brake was
#     tested and REJECTED (it locks in the trough: this strategy's
#     recovery winners cluster right after loss streaks, so month-scoped
#     stops amputate exactly the trades that pay).
#   * ONLY SL exits count toward the streak (not MTM_CUT/EOD/XOVER —
#     those are not whipsaw evidence); any non-SL exit resets it.
#   * Blocks ENTRIES only. Exits, carries and carry-monitoring are never
#     touched (fail-open on exit). A cooldown triggered while a carried
#     position is open lets it manage/exit normally.
#   * Streak resets to 0 when a cooldown fires (one pause per streak).
#   * CSV-approximation caveat from the study: the position-stream sim
#     could not re-open the slot for later signals; this implementation
#     is exact, so sweep results supersede the sim numbers.
#
# ── MAX_LOSS_PER_TRADE (2026-08-17) ─────────────────────────────────────
#   Optional rupee cap on the MONITORED leg's loss, implemented as the
#   TIGHTER of the %-SL level and the cap-implied premium level
#   (SELL: entry + cap/qty; BUY: entry − cap/qty). 0 = OFF.
#   HONEST SCOPE — read before relying on it: this bounds INTRADAY loss
#   paths only. The frozen-run tail study showed 100% of >₹50k losses
#   were OVERNIGHT GAPS on positional carries (median intraday SL slip:
#   0.00 pts); a premium level cannot bind while the market is closed,
#   so gap fills exceed ANY cap exactly as they exceed the %-SL. Gap
#   containment is the hedge's job (hedge premium budget / wing
#   distance), not this knob's. Hedge PnL is NOT netted into the cap —
#   the cap is a level on the monitored leg, consistent with every other
#   exit trigger in this runner. Exit reason stays "SL";
#   rupee_sl_capped counts entries where the cap was the binding level.
#
# ── POSITIONAL / NEG_MTM_EOD_CUT / EXPIRY_INTRINSIC ────────────────────
#   Carried verbatim from the V1 runner (same monitor, same carry state,
#   same intrinsic re-mark at hard-close bounds) — one slot instead of
#   per-condition slots is the only structural difference.

from __future__ import annotations

# PyInstaller anchors — tolerant if unavailable at module-import time.
try:
    import app.backtest.data.candle_source  # noqa: F401
except Exception:
    pass

import sqlite3
import uuid
from dataclasses import replace  # noqa: F401  (parity with V1 imports)
from datetime import date
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.tma.tma_v2_engine import (
        EXIT_REF_MAX, EXIT_REF_MIN, REF_KEYS,
        build_signals_v2, compute_state_v2, monitor_position_day,
        sl_tp_levels, warmup_bars, xover_exit_ts_v2,
    )
    from app.backtest.tma.backtest_tma_runner import TMATrade
    from app.backtest.pst.pst_indicators import aggregate
    from app.backtest.ic.ic_v1_engine import select_strike
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone tests
    from tma_v2_engine import (  # type: ignore
        EXIT_REF_MAX, EXIT_REF_MIN, REF_KEYS,
        build_signals_v2, compute_state_v2, monitor_position_day,
        sl_tp_levels, warmup_bars, xover_exit_ts_v2,
    )
    from backtest_tma_runner import TMATrade  # type: ignore
    from pst_indicators import aggregate  # type: ignore
    from ic_v1_engine import select_strike  # type: ignore
    import ic_synth_wing as SW  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65          # NIFTY
TF_MIN = 5             # signal timeframe (5m) — fixed, carried in cfg
WARMUP_DAYS = 5        # ── TMA2_XDAY_WARMUP ── EMA144 needs the depth (D5)
EMA_PERIODS = (13, 55, 89, 144)   # ── D7 ── hardcoded, carried for display

DEFAULT_MAIN = {"premium_max": 100, "lots": 1, "sl_pct": 30, "tp_pct": 50}
DEFAULT_HEDGE = {"premium_max": 3, "lots": 1}


def _hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _day_start_epoch(d: date) -> int:
    from datetime import datetime
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _leg_agg(trades: List[TMATrade], direction: str) -> dict:
    rows = [t for t in trades if t.direction == direction
            and t.exit_price is not None]
    return {"trades": len(rows),
            "wins": sum(1 for t in rows if t.net_pnl > 0),
            "net_pnl": round(sum(t.net_pnl for t in rows), 2)}


def _summarize(trades: List[TMATrade], diag: dict, mode: str) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    diag = dict(diag)
    if mode == "SELL":
        diag["sell_leg"] = _leg_agg(trades, "SELL")
        diag["hedge_leg"] = _leg_agg(trades, "BUY")
    else:
        diag["long_leg"] = _leg_agg(trades, "BUY")
    if not closed:
        s = _empty_summary()
        s["diag_tma2"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.entry_ts or 0, x.direction)):
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
        "diag_tma2": diag,
    }


def run_tma_v2_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "TMA_V2"
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
    # ── shape ── s1: {main: {premium_max, lots, sl_pct, tp_pct, sl_unit,
    # tp_unit}, hedge: {premium_max, lots}, max_trades_per_day}; top-level
    # mode, xover_exit_enabled, wing_mode, trade_mode, session/exit times.
    s1_raw = cfg.get("s1") or {}
    main_cfg = _leg_cfg(s1_raw.get("main"), DEFAULT_MAIN)
    hedge_cfg = _leg_cfg(s1_raw.get("hedge"), DEFAULT_HEDGE)
    max_per_day = int(s1_raw.get("max_trades_per_day") or 0)

    mode = "SELL" if str(cfg.get("mode", "BUY")).upper() == "SELL" else "BUY"
    # ── XOVER_TOGGLE (D3) ── ON by default; OFF → SL/TP/EOD-family only
    xover_enabled = bool(cfg.get("xover_exit_enabled", True))
    # ── 2026-CHOP / EXIT_REF_CUSTOM ── exit reference EMA period. 89 is
    # the D4 default, 55 the studied alternative, and ANY period in
    # [EXIT_REF_MIN, EXIT_REF_MAX] is allowed (e.g. 70). Non-numeric
    # input falls back to 89; out-of-range aborts LOUDLY below rather
    # than silently trading a degenerate exit line.
    try:
        xover_ref = int(cfg.get("xover_exit_ref", 89) or 89)
    except (TypeError, ValueError):
        xover_ref = 89
    # ── 2026-CHOP ── entry-freshness gate, % of spot; 0 = off
    max_ext_pct = float(cfg.get("max_extension_pct", 0) or 0)
    # ── EXT_BAND ── floor: reject stacks whose fan hasn't opened yet
    min_ext_pct = float(cfg.get("min_extension_pct", 0) or 0)
    # ── 2026-CHOP ── EMA144 slope gate (see engine SLOPE_BARS)
    slope_gate = bool(cfg.get("ema144_slope_gate", False))
    # ── SL_STREAK_COOLDOWN ── K consecutive SLs → pause entries N days
    sl_streak_k = max(0, int(cfg.get("sl_streak_count", 0) or 0))
    sl_cd_days = max(1, int(cfg.get("sl_streak_cooldown_days", 5) or 5))
    # ── MAX_LOSS_PER_TRADE ── ₹ cap → tighter SL level; 0 = off
    max_loss_rs = max(0.0, float(cfg.get("max_loss_per_trade", 0) or 0))

    wing_mode = str(cfg.get("wing_mode", "synthetic") or "synthetic").lower()
    if wing_mode not in ("synthetic", "real_fallback", "skip"):
        wing_mode = "synthetic"
    trade_mode = str(cfg.get("trade_mode", "INTRADAY") or "INTRADAY").upper()
    if trade_mode not in ("INTRADAY", "POSITIONAL"):
        trade_mode = "INTRADAY"
    positional = trade_mode == "POSITIONAL"
    cut_neg_mtm = positional and bool(cfg.get("cut_neg_mtm_eod", False))

    tf_min = int(cfg.get("tf_minutes", TF_MIN) or TF_MIN)
    tf_s = tf_min * 60

    sess_start_min = _hm_to_min(cfg.get("session_start", "09:15"), 9 * 60 + 15)
    sess_end_min = _hm_to_min(cfg.get("session_end", "15:00"), 15 * 60)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)

    # Fail LOUD on a nonsense session window (the "V3 no entries" lesson).
    if not (sess_start_min < sess_end_min <= exit_min):
        return {"run_id": None, "aborted": True,
                "reason": (f"TMA_V2 session window invalid: start "
                           f"{cfg.get('session_start')} < end "
                           f"{cfg.get('session_end')} <= EOD "
                           f"{cfg.get('exit_time')} must hold"),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── EXIT_REF_CUSTOM ── fail loud on a degenerate/unwarmable ref
    if xover_enabled and not (EXIT_REF_MIN <= xover_ref <= EXIT_REF_MAX):
        return {"run_id": None, "aborted": True,
                "reason": (f"TMA_V2 crossover exit reference EMA"
                           f"{xover_ref} is out of range — must be "
                           f"{EXIT_REF_MIN}-{EXIT_REF_MAX} (EMA13 is the "
                           f"fast line, so a ref at or below it would "
                           f"exit on the entry bar; a ref beyond "
                           f"{EXIT_REF_MAX} cannot warm up in "
                           f"{WARMUP_DAYS} sessions)"),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    # ── EXT_BAND ── an inverted band admits nothing; fail loud rather
    # than return a silent zero-entry run
    if min_ext_pct > 0 and max_ext_pct > 0 and min_ext_pct >= max_ext_pct:
        return {"run_id": None, "aborted": True,
                "reason": (f"TMA_V2 extension band is inverted: min "
                           f"{min_ext_pct}% >= max {max_ext_pct}% — no "
                           f"signal can satisfy both"),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    if main_cfg["lots"] <= 0:
        return {"run_id": None, "aborted": True,
                "reason": "TMA_V2 needs lots > 0 on the main leg",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    # SELL mode is a spread: both legs enter together (V1 D11/D12 rule)
    if mode == "SELL" and hedge_cfg["lots"] <= 0:
        return {"run_id": None, "aborted": True,
                "reason": "TMA_V2 SELL mode needs lots > 0 on the hedge "
                          "leg (sold leg and hedge enter together)",
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
            "bars5_today": 0, "e1_events": 0, "e2_events": 0,
            "blocked_warmup": 0, "blocked_session": 0,
            "blocked_extension": 0, "blocked_slope": 0,   # ── 2026-CHOP ──
            "blocked_extension_min": 0,   # ── EXT_BAND ──
            "skipped_cooldown": 0, "cooldowns_triggered": 0,   # ── SL_STREAK_COOLDOWN ──
            "rupee_sl_capped": 0,   # ── MAX_LOSS_PER_TRADE ── cap was the binding SL
            "signals_total": 0, "signals_taken": 0,
            "skipped_busy": 0, "skipped_cap": 0,
            "skipped_select": 0, "skipped_hedge": 0, "ambiguous": 0,
            # hedge sourcing funnel (all zero in BUY mode)
            "hedge_real": 0, "hedge_synth": 0, "hedge_cheapest_fb": 0,
            "hedge_exit_fallbacks": 0, "wing_mode": None,
            "expiry_intrinsic_closes": 0,
            # positional funnel (all zero in INTRADAY runs)
            "trade_mode": None, "carried_nights": 0, "expiry_closes": 0,
            "eor_closes": 0, "carry_gap_days": 0, "mtm_cuts": 0,
            "mode": None, "xover_exit": None}
    diag["trade_mode"] = trade_mode
    diag["wing_mode"] = wing_mode if mode == "SELL" else "n/a"
    diag["mode"] = mode
    diag["xover_exit"] = "ON" if xover_enabled else "OFF"
    # ── 2026-CHOP ── settings echoed for the report / DIAG funnel
    diag["xover_ref"] = str(xover_ref)
    diag["max_extension_pct"] = max_ext_pct
    diag["min_extension_pct"] = min_ext_pct
    diag["ema144_slope_gate"] = "ON" if slope_gate else "OFF"
    diag["sl_streak_cooldown"] = (f"{sl_streak_k}SL/{sl_cd_days}d"
                                  if sl_streak_k > 0 else "OFF")
    diag["max_loss_per_trade"] = max_loss_rs if max_loss_rs > 0 else "OFF"
    trades: List[TMATrade] = []
    # ── D2 ── ONE slot, either direction; busy_until persists ACROSS days.
    carry: Dict[str, dict] = {}
    pos_busy = {"S1": -1}
    # ── SL_STREAK_COOLDOWN ── streak + entries-blocked-until (epoch s).
    # Chronology is guaranteed by the single slot: within a day exits
    # precede the next entry (busy-blocking), and carried exits are
    # processed in the carry loop BEFORE the day's entry loop.
    cd_state = {"streak": 0, "until": -1}
    last_range_day = spot_days[-1]

    def _one_row(*, symbol, side, strike, expiry, direction, entry_ts,
                 entry_price, sl, tp, exit_ts, exit_price, reason, qty,
                 ambiguous) -> None:
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
            exit_reason=reason, qty=qty, condition=str(pos_cond_ref["v"]),
            ambiguous_fill=ambiguous,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=ambiguous,
        ))

    # condition label for the row being emitted ("E1" | "E2") — set by the
    # emit paths right before _one_row runs (avoids threading it through
    # every V1-shaped signature).
    pos_cond_ref = {"v": "S1"}

    def _emit_pos_trade(res: dict, pos: dict) -> None:
        pos_cond_ref["v"] = pos.get("cond", "S1")
        # main row (monitored leg; SELL in SELL mode, BUY in BUY mode)
        _one_row(symbol=pos["symbol"], side=pos["side"],
                 strike=pos.get("strike"),
                 expiry=pos.get("expiry"), direction=pos["action"],
                 entry_ts=res["entry_ts"], entry_price=res["entry_price"],
                 sl=res["sl_price"], tp=res["tp_price"],
                 exit_ts=res["exit_ts"], exit_price=res["exit_price"],
                 reason=res["exit_reason"], qty=int(pos["lots"]) * LOT_SIZE,
                 ambiguous=bool(res["ambiguous_fill"]))
        if pos["action"] == "SELL":     # hedge exists in SELL mode only
            hx = _hedge_exit_price(pos, res["exit_ts"])
            _one_row(symbol=pos["h_symbol"], side=pos["side"],
                     strike=pos.get("h_strike"),
                     expiry=pos.get("expiry"), direction="BUY",
                     entry_ts=res["entry_ts"], entry_price=pos["h_entry"],
                     sl=None, tp=None,
                     exit_ts=res["exit_ts"], exit_price=hx,
                     reason=res["exit_reason"],
                     qty=int(pos["h_lots"]) * LOT_SIZE,
                     ambiguous=False)
        # ── SL_STREAK_COOLDOWN ── streak accounting on the MONITORED
        # leg's exit (the hedge mirrors it and must not double-count)
        if sl_streak_k > 0:
            if res["exit_reason"] == "SL":
                cd_state["streak"] += 1
                if cd_state["streak"] >= sl_streak_k:
                    cd_state["until"] = int(res["exit_ts"]) \
                        + sl_cd_days * 86400
                    cd_state["streak"] = 0
                    diag["cooldowns_triggered"] += 1
            else:
                cd_state["streak"] = 0
        if res["exit_reason"] == "EOD" and pos.get("expiry_close"):
            diag["expiry_closes"] += 1
        if res["exit_reason"] == "EOR":
            diag["eor_closes"] += 1
        if res["exit_reason"] == "MTM_CUT":
            diag["mtm_cuts"] += 1
        if res["ambiguous_fill"]:
            diag["ambiguous"] += 1

    # ── hedge sourcing + exit pricing (SELL mode only; V1 SPREAD verbatim) ─
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
        return _day_start_epoch(date.fromisoformat(expiry_iso)) \
            + (15 * 60 + 30) * 60

    def _select_hedge(side: str, ts: int, ladder: List[tuple],
                      meta: dict, want_expiry: str,
                      day_start: int) -> Optional[dict]:
        # ladder = [(sym, close@ts-60)] for the SAME side (real strikes).
        pick = select_strike(ladder, hedge_cfg["premium_max"])
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
        if wing_mode == "synthetic" and ladder:
            edge_sym, edge_px = min(ladder, key=lambda c: (c[1], c[0]))
            edge_k = (meta.get(edge_sym) or {}).get("strike")
            spot_now = _spot_close_at(ts)
            if edge_k and edge_px > 0 and spot_now:
                is_call = side == "CE"
                tau = SW.tau_years(ts, _expiry_ts(want_expiry))
                iv = SW.implied_vol(edge_px, is_call, spot_now,
                                    float(edge_k), tau)
                if iv is not None:
                    start = float(edge_k) + (50.0 if is_call else -50.0)
                    sol = SW.solve_wing_strike(
                        is_call, spot_now, tau, iv,
                        target_premium=hedge_cfg["premium_max"],
                        start_strike=start)
                    if sol is not None:
                        k, px = sol
                        diag["hedge_synth"] += 1
                        return {"kind": "synth",
                                "symbol": SW.synth_symbol(
                                    underlying, want_expiry, k, is_call),
                                "entry": px, "strike": k, "iv": iv}
        # fail open to reality: cheapest real strike, flagged
        fb = select_strike(ladder, hedge_cfg["premium_max"],
                           fallback_cheapest=True)
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

    # ── TMA2_XDAY_WARMUP ── rolling window of the prior WARMUP_DAYS sessions
    warm_hist: List[tuple] = []   # [(spot_1m, day_start), ...] oldest-first

    for di, d in enumerate(spot_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(spot_days),
                         "date": d.isoformat()})
        spot = spot_1m_for(d)
        day_start = _day_start_epoch(d)
        warmup_sessions = list(warm_hist)
        if spot:
            warm_hist.append((spot, day_start))
            if len(warm_hist) > WARMUP_DAYS:
                warm_hist.pop(0)
        if not spot:
            diag["carry_gap_days"] += len(carry)
            continue

        session0 = day_start + (9 * 60 + 15) * 60
        entry_start_ts = day_start + sess_start_min * 60
        entry_end_ts = day_start + sess_end_min * 60
        eod_ts = day_start + exit_min * 60

        # continuous 5m stream: prior sessions then today (completed bars).
        # Computed BEFORE the option-universe gate: POSITIONAL carry
        # monitoring needs today's crossover state even on entry-less days.
        warm5 = warmup_bars(warmup_sessions, tf_min)
        today5 = [b for b in aggregate(spot, tf_min, day_start)
                  if b["complete"]]
        bars5 = warm5 + today5
        # ── EXIT_REF_CUSTOM ── ref_period builds state["eref"] only when
        # the reference is not already a stack EMA (55/89 cost nothing)
        state = compute_state_v2(
            bars5, ref_period=(xover_ref if xover_ref not in REF_KEYS
                               else None))

        def xover_fn(side: str, after_ts: int) -> Optional[int]:
            # ── XOVER_TOGGLE (D3) ── OFF returns None on every call: the
            # monitor never sees a crossover bound, SL/TP/EOD-family only.
            if not xover_enabled:
                return None
            return xover_exit_ts_v2(bars5, state, side, after_ts, tf_s=tf_s,
                                    exit_ref=xover_ref)   # ── 2026-CHOP ──

        def opt_day_candles(sym: str) -> List[dict]:
            return [{"ts": x.ts, "open": x.open, "high": x.high,
                     "low": x.low, "close": x.close}
                    for x in src.candles_1m_for_symbol_day(sym, day_start)]

        # ── EXPIRY_INTRINSIC ── intrinsic mark at hard-close bounds when
        # the held symbol's candles are missing or ran out (V1 verbatim).
        def _intrinsic_at_bound(pos, bound_ts):
            for c_ in reversed(spot):
                if c_["ts"] < bound_ts:
                    k = float(pos.get("strike") or 0)
                    sp = float(c_["close"])
                    intr = (sp - k) if pos["side"] == "CE" else (k - sp)
                    return int(c_["ts"]), round(max(0.05, intr), 2)
            return None

        def _patch_partial_day_close(res, pos, hard_ts):
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

        # ── POSITIONAL ── advance the carried position through today
        # BEFORE any entry gating (V1 pattern, single slot).
        if positional and carry:
            for slot in list(carry.keys()):
                pos = carry[slot]
                hard_ts, hard_reason = None, "EOD"
                if pos.get("expiry") == d.isoformat():
                    hard_ts, pos["expiry_close"] = eod_ts, True
                elif d == last_range_day:
                    hard_ts, hard_reason = eod_ts, "EOR"
                cands = opt_day_candles(pos["symbol"])
                if not cands and hard_ts is None:
                    diag["carry_gap_days"] += 1
                    diag["carried_nights"] += 1
                    continue                 # no data, position persists
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
                        pos_busy[slot] = sp_ts + 60
                        del carry[slot]
                        continue
                pos["watch_from"] = 0
                xts = xover_fn(pos["trend_side"], session0)
                res = monitor_position_day(
                    pos, cands, xts, hard_ts, hard_reason,
                    mtm_cut_ts=(eod_ts if (cut_neg_mtm and hard_ts is None)
                                else None))
                res = _patch_partial_day_close(res, pos, hard_ts)
                if res is not None:
                    _emit_pos_trade(res, pos)
                    pos_busy[slot] = res["exit_ts"] + 60
                    del carry[slot]
                else:
                    diag["carried_nights"] += 1
                    pos_busy[slot] = day_start + 86400   # busy all day

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
        by_side = {"CE": [c["tradingsymbol"] for c in week
                          if c["instrument_type"] == "CE"],
                   "PE": [c["tradingsymbol"] for c in week
                          if c["instrument_type"] == "PE"]}

        sig_res = build_signals_v2(bars5, len(warm5), session0,
                                   entry_start_ts, entry_end_ts,
                                   tf_s=tf_s, state=state,
                                   min_extension_pct=min_ext_pct,   # ── EXT_BAND ──
                                   max_extension_pct=max_ext_pct,   # ── 2026-CHOP ──
                                   slope_gate=slope_gate)
        for k in ("bars5_today", "e1_events", "e2_events",
                  "blocked_warmup", "blocked_session",
                  "blocked_extension", "blocked_slope",   # ── 2026-CHOP ──
                  "blocked_extension_min"):   # ── EXT_BAND ──
            diag[k] += sig_res["diag"][k]
        diag["signals_total"] += sig_res["diag"]["signals"]
        if not sig_res["signals"]:
            continue

        def select_option(side: str, ts: int, prem_max: float
                          ) -> Optional[dict]:
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

        # ── ENTRY LOOP ── ONE slot (D2), both modes. leg side per D6:
        # BUY → trend side; SELL → opposite of trend side (+ same-side
        # hedge deeper OTM). Both legs fill at the signal minute.
        taken_today = 0
        _sraw = (cfg.get("s1", {}) or {}).get("main", {}) or {}
        _su = str(_sraw.get("sl_unit") or "PCT").upper()
        _tu = str(_sraw.get("tp_unit") or "PCT").upper()
        for sig in sorted(sig_res["signals"], key=lambda x: x["ts"]):
            slot = "S1"
            if sig["ts"] < pos_busy[slot]:
                diag["skipped_busy"] += 1
                continue
            # ── SL_STREAK_COOLDOWN ── entries only; exits/carries above
            # this line are untouched by design
            if sl_streak_k > 0 and sig["ts"] < cd_state["until"]:
                diag["skipped_cooldown"] += 1
                continue
            if max_per_day and taken_today >= max_per_day:
                diag["skipped_cap"] += 1
                continue
            if mode == "SELL":
                leg_side = "PE" if sig["side"] == "CE" else "CE"
                action = "SELL"
            else:
                leg_side = sig["side"]
                action = "BUY"
            sel = select_option(leg_side, sig["ts"], main_cfg["premium_max"])
            if sel is None:
                diag["skipped_select"] += 1
                continue
            hedge = None
            if mode == "SELL":
                # hedge ladder: same side as the SOLD leg, priced at the
                # selection minute (ts-60)
                ladder = []
                for hsym in by_side.get(leg_side, []):
                    if hsym == sel["symbol"]:
                        continue
                    hpx = next((float(x.close) for x in
                                src.candles_1m_for_symbol_day(hsym, day_start)
                                if x.ts == sig["ts"] - 60), None)
                    if hpx:
                        ladder.append((hsym, hpx))
                hedge = _select_hedge(leg_side, sig["ts"], ladder, meta,
                                      want_expiry, day_start)
                if hedge is None:
                    diag["skipped_hedge"] += 1
                    continue
            m = meta.get(sel["symbol"], {})
            ep = float(sel["entry_price"])
            sl_level, tp_level = sl_tp_levels(
                ep, action, main_cfg["sl_pct"], main_cfg["tp_pct"], _su, _tu)
            # ── MAX_LOSS_PER_TRADE ── cap-implied level; tighter one wins.
            # Bounds intraday paths only — overnight gaps fill beyond any
            # level (see header). Applies to the monitored leg's premium.
            if max_loss_rs > 0:
                qty_m = int(main_cfg["lots"]) * LOT_SIZE
                if qty_m > 0:
                    if action == "SELL":
                        cap_level = ep + max_loss_rs / qty_m
                        if sl_level is None or cap_level < sl_level:
                            sl_level = cap_level
                            diag["rupee_sl_capped"] += 1
                    else:
                        cap_level = ep - max_loss_rs / qty_m
                        if cap_level > 0 and (sl_level is None
                                              or cap_level > sl_level):
                            sl_level = max(0.05, cap_level)
                            diag["rupee_sl_capped"] += 1
            pos = {"cond": sig["cond"], "side": leg_side,
                   "trend_side": sig["side"], "action": action,
                   "symbol": sel["symbol"], "lots": main_cfg["lots"],
                   "strike": m.get("strike"), "expiry": m.get("expiry"),
                   "entry_ts": sig["ts"], "entry_price": ep,
                   "sl_price": sl_level, "tp_price": tp_level,
                   "watch_from": sig["ts"] + 60,
                   "last_close": ep, "last_ts": sig["ts"]}
            if hedge is not None:
                pos.update({"h_symbol": hedge["symbol"],
                            "h_entry": float(hedge["entry"]),
                            "h_strike": hedge.get("strike"),
                            "h_lots": hedge_cfg["lots"],
                            "h_kind": hedge["kind"], "h_iv": hedge.get("iv"),
                            "h_side_is_call": leg_side == "CE"})
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
            xts = xover_fn(sig["side"], sig["ts"])
            res = monitor_position_day(
                pos, sel["candles"], xts, hard_ts, hard_reason,
                mtm_cut_ts=(eod_ts if (cut_neg_mtm and hard_ts is None)
                            else None))
            res = _patch_partial_day_close(res, pos, hard_ts)
            taken_today += 1
            diag["signals_taken"] += 1
            if res is not None:
                _emit_pos_trade(res, pos)
                pos_busy[slot] = res["exit_ts"] + 60
            else:
                diag["carried_nights"] += 1
                carry[slot] = pos
                pos_busy[slot] = day_start + 86400
        if taken_today:
            diag["days_traded"] += 1

    # ── POSITIONAL ── safety net: nothing outlives the simulation.
    for slot, pos in list(carry.items()):
        _emit_pos_trade({"side": pos["side"], "entry_ts": pos["entry_ts"],
                         "entry_price": pos["entry_price"],
                         "sl_price": pos.get("sl_price"),
                         "tp_price": pos.get("tp_price"),
                         "exit_ts": pos.get("last_ts", pos["entry_ts"]),
                         "exit_price": pos.get("last_close",
                                               pos["entry_price"]),
                         "exit_reason": "EOR", "ambiguous_fill": False}, pos)
        del carry[slot]

    conn.close()
    try:
        src.close()
    except Exception:
        pass
    summary = _summarize(trades, diag, mode)
    write_audit_log(
        f"[BACKTEST][TMA_V2][{mode}][{trade_mode}] {underlying} "
        f"{date_from}→{date_to}: "
        f"{diag['days_traded']}/{diag['days_total']} days traded, "
        f"{diag['signals_taken']}/{diag['signals_total']} signals taken "
        f"(E1 {diag['e1_events']} / E2 {diag['e2_events']}), "
        f"{len(trades)} rows, net {summary['net_pnl']}, "
        f"xover {diag['xover_exit']}/ref{diag['xover_ref']}, "
        f"cooldown {diag['sl_streak_cooldown']} "
        f"(trig {diag['cooldowns_triggered']} skip {diag['skipped_cooldown']}), "
        f"rupeeCap {diag['max_loss_per_trade']} "
        f"(bound {diag['rupee_sl_capped']}), "
        f"extBlk {diag['blocked_extension']} "
        f"extMinBlk {diag['blocked_extension_min']} "
        f"slopeBlk {diag['blocked_slope']} "
        f"warmupBlk {diag['blocked_warmup']} "
        f"sessBlk {diag['blocked_session']}")
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "trades": trades, "config": cfg, "strategy_id": strategy_id}