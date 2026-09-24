# backend/app/backtest/vap/backtest_vap_runner.py
#
# ── VAP_V1 RUNNER ── Anchored VWAP on the OPTION premium (NIFTY weekly
# CE/PE @5m), intraday only. Option BUY (same side as the signal) or
# SELL (opposite side + same-side deeper-OTM hedge, TMA_V2 spread
# mechanics verbatim).
#
# ── DAY SHAPE ───────────────────────────────────────────────────────────
#   09:15  VWAP anchor for every contract in the corpus (implicit — the
#          runner slices ONE session per leg and never crosses days).
#   09:20  SIGNAL CONTRACT SELECTION (selection_time, D4/N1): one CE and
#          one PE, each the HIGHEST premium <= signal_premium_max and
#          >= min_premium, priced on the close of the 1m candle at
#          selection_time-60. These two contracts are HELD ALL DAY. They
#          are the only series whose VWAP is ever evaluated.
#   09:30  entry window opens (session_start). Earlier bars still arm the
#          state machine and still feed VWAP — they just cannot enter.
#   14:45  session_end: no NEW entries (exits and monitoring continue).
#   15:15  exit_time: hard square-off. Nothing carries overnight (D14).
#
# ── WHY SELECTION IS DAY-SCOPED ─────────────────────────────────────────
#   VWAP is anchored to 09:15 of a SPECIFIC contract. Re-picking the
#   strike per entry would restart the anchor on a series the arm/disarm
#   machine has never seen, and the "closed below, then closed above"
#   re-entry rule would be comparing against a VWAP with five minutes of
#   history in the middle of the afternoon. The TRADED leg in SELL mode
#   has no such constraint and IS re-selected per entry (it carries no
#   VWAP), which is why the two selections use different premium caps.
#
# ── THREE CONTRACTS IN SELL MODE (N3) ───────────────────────────────────
#   signal_premium_max  → the CE and PE whose VWAP we watch (default 200)
#   v1.main.premium_max → the SHORT leg actually traded
#   v1.hedge.premium_max→ the long wing (margin + tail)
#   In BUY mode the signal leg IS the traded leg, so main.premium_max is
#   IGNORED (N3) — two caps on one contract is a footgun, and the UI
#   hides the field rather than letting it silently do nothing.
#
# ── SIGNAL / TRADED ASYMMETRY IN SELL MODE (D7/N2) ──────────────────────
#   CE closes above ITS OWN VWAP → SELL PE + BUY PE hedge. The state
#   machine (arm on a below-close, enter on the next above-close) tracks
#   the CE series; SL/TP and the ATR that sizes them live on the PE
#   premium (N4). This is deliberate and is the single most confusing
#   thing in the module — every leg variable is therefore named either
#   sig_* or trd_*, never just "side".
#
# ── CONFLICT (N1) ───────────────────────────────────────────────────────
#   allow_both_sides=False → the legs share ONE slot, and a bar on which
#   BOTH would enter takes NEITHER (skipped_conflict). Both legs stay
#   ARMED, so the pair simply sits out while the two-sided break holds.
#
# Fill convention (D11, house standard): a signal bar COMPLETING at ts is
# filled at the CLOSE of the 1m candle STAMPED ts — the first minute
# after the bar finished. Monitoring starts at ts+60. Zero lookahead,
# identical to TMA_V1/V2/PST.
#
# Keep the dispatch chain in sync with queue_worker._dispatch_run_impl
# AND api/backtest_routes.run_start — two hand-maintained copies.

from __future__ import annotations

# PyInstaller anchors — tolerant if unavailable at module-import time.
try:
    import app.backtest.data.candle_source  # noqa: F401
except Exception:
    pass

import sqlite3
import uuid
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

try:
    from app.backtest.vap.vap_v1_engine import (
        ATR_PERIOD_DEFAULT, ATR_PERIOD_MAX, ATR_PERIOD_MIN,
        EMA_PERIOD_MAX, VOL_LOOKBACK_DEFAULT,
        atr_wilder, breached_during, bucket_volumes, decide_leg,
        ema_at_bar_ends, leg_bar_facts, monitor_position_day, size_sl_tp,
        sl_tp_levels, vwap_by_minute,
    )
    from app.backtest.tma.backtest_tma_runner import TMATrade
    from app.backtest.pst.pst_indicators import aggregate
    from app.backtest.ic.ic_v1_engine import select_strike
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone tests
    from vap_v1_engine import (  # type: ignore
        ATR_PERIOD_DEFAULT, ATR_PERIOD_MAX, ATR_PERIOD_MIN,
        EMA_PERIOD_MAX, VOL_LOOKBACK_DEFAULT,
        atr_wilder, breached_during, bucket_volumes, decide_leg,
        ema_at_bar_ends, leg_bar_facts, monitor_position_day, size_sl_tp,
        sl_tp_levels, vwap_by_minute,
    )
    from backtest_tma_runner import TMATrade  # type: ignore
    from pst_indicators import aggregate  # type: ignore
    from ic_v1_engine import select_strike  # type: ignore
    import ic_synth_wing as SW  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65          # NIFTY
TF_MIN = 5             # signal timeframe — fixed, carried in cfg
SESSION_ANCHOR_MIN = 9 * 60 + 15   # VWAP anchor, always 09:15

DEFAULT_MAIN = {"premium_max": 200, "lots": 1}
DEFAULT_HEDGE = {"premium_max": 3, "lots": 1}

LEGS = ("CE", "PE")


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


def _date_of_ts(ts: int) -> date:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ts) + IST, tz=timezone.utc).date()


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


def _sig_agg(trades: List[TMATrade], cond: str) -> dict:
    rows = [t for t in trades if t.condition == cond
            and t.exit_price is not None]
    return {"trades": len(rows),
            "wins": sum(1 for t in rows if t.net_pnl > 0),
            "net_pnl": round(sum(t.net_pnl for t in rows), 2)}


def _expiry_split(trades: List[TMATrade]) -> dict:
    """── D16 ── expiry-day rows bucketed separately. Option VWAP on
    expiry day is a different animal (gamma, collapsing extrinsic), and
    in SELL mode it can dominate the aggregate — reporting one blended
    number would hide which regime the edge actually came from."""
    exp, non = [], []
    for t in trades:
        if t.exit_price is None:
            continue
        try:
            is_exp = bool(t.expiry) and _date_of_ts(t.entry_ts).isoformat() == t.expiry
        except Exception:
            is_exp = False
        (exp if is_exp else non).append(t)

    def _agg(rows):
        return {"trades": len(rows),
                "wins": sum(1 for t in rows if t.net_pnl > 0),
                "net_pnl": round(sum(t.net_pnl for t in rows), 2)}
    return {"expiry_day": _agg(exp), "other_days": _agg(non)}


def _summarize(trades: List[TMATrade], diag: dict, mode: str) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    diag = dict(diag)
    if mode == "SELL":
        diag["sell_leg"] = _leg_agg(trades, "SELL")
        diag["hedge_leg"] = _leg_agg(trades, "BUY")
    else:
        diag["long_leg"] = _leg_agg(trades, "BUY")
    diag["ce_signal"] = _sig_agg(trades, "CE_SIG")
    diag["pe_signal"] = _sig_agg(trades, "PE_SIG")
    diag["expiry_split"] = _expiry_split(trades)   # ── D16 ──
    if not closed:
        s = _empty_summary()
        s["diag_vap"] = diag
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
        "diag_vap": diag,
    }


def _leg_cfg(raw: Optional[dict], defaults: dict) -> dict:
    raw = raw or {}
    out = dict(defaults)
    for k in out:
        if raw.get(k) is not None:
            out[k] = raw[k]
    return {"premium_max": float(out["premium_max"] or 0),
            "lots": int(out["lots"] or 0)}


def _abort(reason: str, cfg: dict, strategy_id: str) -> dict:
    return {"run_id": None, "aborted": True, "reason": reason,
            "trades": [], "summary": _empty_summary(),
            "config": cfg, "strategy_id": strategy_id}


def run_vap_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "VAP_V1"
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
    # ── shape ── {vwap:{...}, v1:{main,hedge,max_trades_per_day}, mode,
    # signal_premium_max, min_premium, selection_time, allow_both_sides,
    # require_arm_first, vwap_buffer_pct, sl_mode, sl_pct, atr_period,
    # atr_mult, max_sl_pct, tp_mode, rr, tp_pct, session/exit times,
    # wing_mode}. `vwap` + `v1` is the describeConfig detection key —
    # disjoint from ema (TMA_V1), ema4 (TMA_V2), legs (PST), signal_tf.
    v1_raw = cfg.get("v1") or {}
    main_cfg = _leg_cfg(v1_raw.get("main"), DEFAULT_MAIN)
    hedge_cfg = _leg_cfg(v1_raw.get("hedge"), DEFAULT_HEDGE)
    max_per_leg = int(v1_raw.get("max_trades_per_day") or 0)

    mode = "SELL" if str(cfg.get("mode", "BUY")).upper() == "SELL" else "BUY"

    sig_prem_max = float(cfg.get("signal_premium_max", 200) or 0)
    min_prem = float(cfg.get("min_premium", 0) or 0)
    allow_both = bool(cfg.get("allow_both_sides", True))
    require_arm = bool(cfg.get("require_arm_first", False))
    buffer_pct = float(cfg.get("vwap_buffer_pct", 0) or 0)

    sl_mode = str(cfg.get("sl_mode", "PCT") or "PCT").upper()
    if sl_mode not in ("PCT", "ATR"):
        sl_mode = "PCT"
    sl_pct = float(cfg.get("sl_pct", 0) or 0)
    atr_period = int(cfg.get("atr_period", ATR_PERIOD_DEFAULT)
                     or ATR_PERIOD_DEFAULT)
    atr_mult = float(cfg.get("atr_mult", 0) or 0)
    max_sl_pct = float(cfg.get("max_sl_pct", 0) or 0)

    tp_mode = str(cfg.get("tp_mode", "RR") or "RR").upper()
    if tp_mode not in ("RR", "PCT"):
        tp_mode = "RR"
    rr = float(cfg.get("rr", 0) or 0)
    tp_pct = float(cfg.get("tp_pct", 0) or 0)

    # ── ENTRY_FILTERS_20260820 ── both on the SIGNAL leg, both gate
    # ENTRY ONLY (never arming). ema_period 0 / vol_mult 0 = OFF.
    ema_period = int(cfg.get("ema_period", 0) or 0)
    ema_basis = int(cfg.get("ema_basis_minutes", 1) or 1)
    vol_mult = float(cfg.get("vol_mult", 0) or 0)
    vol_lookback = int(cfg.get("vol_lookback", VOL_LOOKBACK_DEFAULT)
                       or VOL_LOOKBACK_DEFAULT)

    # ── SL_GRACE_20260820 ── suspend the SL for the first N minutes after
    # entry. TP stays armed; the EOD bound always applies. See the engine
    # header for why this is NOT implemented inside monitor_position_day.
    sl_grace_min = int(cfg.get("sl_grace_min", 0) or 0)
    sl_grace_disaster_pct = float(cfg.get("sl_grace_disaster_pct", 0) or 0)

    wing_mode = str(cfg.get("wing_mode", "synthetic") or "synthetic").lower()
    if wing_mode not in ("synthetic", "real_fallback", "skip"):
        wing_mode = "synthetic"

    tf_min = int(cfg.get("tf_minutes", TF_MIN) or TF_MIN)
    tf_s = tf_min * 60

    sel_min = _hm_to_min(cfg.get("selection_time", "09:20"), 9 * 60 + 20)
    sess_start_min = _hm_to_min(cfg.get("session_start", "09:30"), 9 * 60 + 30)
    sess_end_min = _hm_to_min(cfg.get("session_end", "14:45"), 14 * 60 + 45)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:15"), 15 * 60 + 15)

    # ── FAIL LOUD ── every one of these silently produces a zero-entry or
    # a nonsense run, which is worse than an abort (the "V3 no entries"
    # lesson): an empty results table looks like "no edge", not "bad
    # config", and gets filed as evidence.
    if not (SESSION_ANCHOR_MIN <= sel_min < sess_start_min
            < sess_end_min <= exit_min):
        return _abort(
            f"VAP_V1 clock order invalid: 09:15 anchor <= selection "
            f"{cfg.get('selection_time')} < entry start "
            f"{cfg.get('session_start')} < entry cutoff "
            f"{cfg.get('session_end')} <= square-off "
            f"{cfg.get('exit_time')} must hold", cfg, strategy_id)
    if sig_prem_max <= 0:
        return _abort("VAP_V1 needs signal_premium_max > 0 — it selects "
                      "the CE/PE contracts whose VWAP is watched",
                      cfg, strategy_id)
    if min_prem >= sig_prem_max:
        return _abort(f"VAP_V1 premium band is inverted: min_premium "
                      f"{min_prem} >= signal_premium_max {sig_prem_max} — "
                      f"no strike can satisfy both", cfg, strategy_id)
    if main_cfg["lots"] <= 0:
        return _abort("VAP_V1 needs lots > 0 on the main leg",
                      cfg, strategy_id)
    if mode == "SELL" and hedge_cfg["lots"] <= 0:
        return _abort("VAP_V1 SELL mode needs lots > 0 on the hedge leg "
                      "(sold leg and hedge enter together)", cfg, strategy_id)
    if mode == "SELL" and main_cfg["premium_max"] <= 0:
        return _abort("VAP_V1 SELL mode needs v1.main.premium_max > 0 — "
                      "it selects the SHORT leg, which is a different "
                      "contract from the signal leg", cfg, strategy_id)
    if sl_mode == "ATR":
        if not (ATR_PERIOD_MIN <= atr_period <= ATR_PERIOD_MAX):
            return _abort(f"VAP_V1 atr_period {atr_period} out of range "
                          f"{ATR_PERIOD_MIN}-{ATR_PERIOD_MAX}",
                          cfg, strategy_id)
        if atr_mult <= 0:
            return _abort("VAP_V1 sl_mode=ATR needs atr_mult > 0",
                          cfg, strategy_id)
    elif sl_pct <= 0 and tp_mode == "RR":
        return _abort("VAP_V1 tp_mode=RR needs a live SL to measure R "
                      "against — set sl_pct > 0 or switch to tp_mode=PCT",
                      cfg, strategy_id)
    if ema_period < 0 or ema_period > EMA_PERIOD_MAX:
        return _abort(f"VAP_V1 ema_period {ema_period} out of range "
                      f"0-{EMA_PERIOD_MAX} (0 = filter off)",
                      cfg, strategy_id)
    if ema_basis not in (1, tf_min):
        return _abort(f"VAP_V1 ema_basis_minutes must be 1 or {tf_min}, "
                      f"got {ema_basis}", cfg, strategy_id)
    ema_warm_min = 0
    if ema_period > 0:
        # ── EMA_WARMUP_GUARD ── ema_series is SMA-seeded and warm at index
        # period-1. On 5m bars off a 09:15 anchor EMA20 is not warm until
        # 10:55 — against a 09:30-11:00 window that is ONE usable bar a
        # day, and the run would read as "the filter killed the edge" when
        # the filter had never actually run. Refuse instead of producing
        # an empty table that looks like evidence.
        ema_warm_min = SESSION_ANCHOR_MIN + (
            (ema_period - 1) if ema_basis <= 1 else ema_period * tf_min)
        if ema_warm_min >= sess_end_min:
            return _abort(
                f"VAP_V1 EMA{ema_period} on a {ema_basis}m basis is not warm "
                f"until {ema_warm_min // 60:02d}:{ema_warm_min % 60:02d}, at "
                f"or after the {cfg.get('session_end')} entry cutoff — the "
                f"filter would block every entry. Use a 1m basis, a shorter "
                f"period, or a later cutoff.", cfg, strategy_id)
    if vol_mult < 0:
        return _abort("VAP_V1 vol_mult cannot be negative (0 = filter off)",
                      cfg, strategy_id)
    if vol_lookback < 1 or vol_lookback > 120:
        return _abort(f"VAP_V1 vol_lookback {vol_lookback} out of range "
                      f"1-120 bars", cfg, strategy_id)

    if sl_grace_min < 0 or sl_grace_min > 360:
        return _abort(f"VAP_V1 sl_grace_min {sl_grace_min} out of range "
                      f"0-360 minutes", cfg, strategy_id)
    if sl_grace_disaster_pct < 0:
        return _abort("VAP_V1 sl_grace_disaster_pct cannot be negative",
                      cfg, strategy_id)
    if sl_grace_min > 0 and sl_grace_disaster_pct > 0:
        _base_sl = (sl_pct if sl_mode == "PCT" else 0)
        if _base_sl > 0 and sl_grace_disaster_pct <= _base_sl:
            return _abort(
                f"VAP_V1 sl_grace_disaster_pct {sl_grace_disaster_pct} must "
                f"be WIDER than the normal SL {_base_sl}% — a tighter "
                f"disaster stop would fire before the grace window it is "
                f"meant to backstop, silently cancelling the grace",
                cfg, strategy_id)

    if tp_mode == "RR" and rr <= 0:
        return _abort("VAP_V1 tp_mode=RR needs rr > 0", cfg, strategy_id)
    if tp_mode == "PCT" and tp_pct <= 0 and sl_pct <= 0 and sl_mode != "ATR":
        return _abort("VAP_V1 has neither an SL nor a TP — every trade "
                      "would run to the 15:15 square-off", cfg, strategy_id)

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
    if not spot_days:
        conn.close()
        return _abort("no NIFTY spot data in range — run the spot backfill "
                      "(spot defines the session calendar and prices the "
                      "synthetic wing)", cfg, strategy_id)

    diag = {"days_total": len(spot_days), "days_traded": 0,
            "days_no_options": 0, "days_uncovered": 0,
            "days_no_signal_ce": 0, "days_no_signal_pe": 0,
            "days_no_signal_leg": 0,
            "bars5_ce": 0, "bars5_pe": 0,
            "arm_events_ce": 0, "arm_events_pe": 0,
            "enter_events_ce": 0, "enter_events_pe": 0,
            "blocked_warmup": 0, "blocked_session": 0,
            "blocked_atr_warmup": 0, "blocked_cap": 0,
            "skipped_conflict": 0, "skipped_busy": 0,
            "skipped_select": 0, "skipped_hedge": 0,
            "sl_clamped": 0, "ambiguous": 0,
            # ── SL_GRACE_20260820 ── the counters that decide whether the
            # grace window earned its keep: how many trades WOULD have
            # stopped out inside the window, and what those became.
            "blocked_ema": 0, "blocked_ema_warmup": 0,
            "blocked_vol": 0, "blocked_vol_warmup": 0,
            "grace_breached": 0, "grace_breach_then_sl": 0,
            "grace_breach_then_tp": 0, "grace_breach_then_eod": 0,
            "grace_breach_then_disaster": 0, "grace_disaster_exits": 0,
            "grace_tp_in_window": 0,
            "signals_total": 0, "signals_taken": 0,
            "hedge_real": 0, "hedge_synth": 0, "hedge_cheapest_fb": 0,
            "hedge_exit_fallbacks": 0,
            "expiry_intrinsic_closes": 0,
            "mode": mode, "wing_mode": wing_mode if mode == "SELL" else "n/a",
            "sl_rule": (f"ATR{atr_period}x{atr_mult}" if sl_mode == "ATR"
                        else f"{sl_pct}%"),
            "tp_rule": (f"RR{rr}" if tp_mode == "RR" else f"{tp_pct}%"),
            "max_sl_pct": max_sl_pct if max_sl_pct > 0 else "OFF",
            "vwap_buffer_pct": buffer_pct if buffer_pct > 0 else "OFF",
            "ema_filter": (f"EMA{ema_period}@{ema_basis}m warm "
                           f"{ema_warm_min // 60:02d}:{ema_warm_min % 60:02d}"
                           if ema_period > 0 else "OFF"),
            "vol_filter": (f"{vol_mult}x mean(last {vol_lookback})"
                           if vol_mult > 0 else "OFF"),
            "sl_grace_min": sl_grace_min if sl_grace_min > 0 else "OFF",
            "sl_grace_disaster_pct": (sl_grace_disaster_pct
                                      if sl_grace_disaster_pct > 0 else "OFF"),
            "both_sides": "ON" if allow_both else "OFF (one slot)",
            "require_arm_first": "ON" if require_arm else "OFF",
            "signal_premium_max": sig_prem_max, "min_premium": min_prem,
            "selection_time": cfg.get("selection_time", "09:20")}

    trades: List[TMATrade] = []

    # ══════════════════════════════════════════════════════════════════
    #  ROW EMISSION
    # ══════════════════════════════════════════════════════════════════
    def _one_row(*, symbol, side, strike, expiry, direction, entry_ts,
                 entry_price, sl, tp, exit_ts, exit_price, reason, qty,
                 ambiguous, condition) -> None:
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
            entry_ts=entry_ts, entry_price=round(float(entry_price), 2),
            sl=(round(sl, 2) if sl is not None else None),
            tp=(round(tp, 2) if tp is not None else None),
            exit_ts=exit_ts, exit_price=round(float(exit_price), 2),
            exit_reason=reason, qty=qty, condition=condition,
            ambiguous_fill=ambiguous,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=ambiguous,
        ))

    def _emit(res: dict, pos: dict) -> None:
        # condition carries the SIGNAL leg (CE_SIG / PE_SIG), NOT the
        # traded side — in SELL mode they are opposites and the whole
        # point of the diag split is to see which signal series paid.
        cond = pos["sig_leg"] + "_SIG"
        _one_row(symbol=pos["symbol"], side=pos["side"],
                 strike=pos.get("strike"), expiry=pos.get("expiry"),
                 direction=pos["action"],
                 entry_ts=res["entry_ts"], entry_price=res["entry_price"],
                 sl=res["sl_price"], tp=res["tp_price"],
                 exit_ts=res["exit_ts"], exit_price=res["exit_price"],
                 reason=res["exit_reason"],
                 qty=int(pos["lots"]) * LOT_SIZE,
                 ambiguous=bool(res["ambiguous_fill"]), condition=cond)
        if pos["action"] == "SELL":
            hx = _hedge_exit_price(pos, res["exit_ts"])
            _one_row(symbol=pos["h_symbol"], side=pos["side"],
                     strike=pos.get("h_strike"), expiry=pos.get("expiry"),
                     direction="BUY",
                     entry_ts=res["entry_ts"], entry_price=pos["h_entry"],
                     sl=None, tp=None,
                     exit_ts=res["exit_ts"], exit_price=hx,
                     reason=res["exit_reason"],
                     qty=int(pos["h_lots"]) * LOT_SIZE,
                     ambiguous=False, condition=cond)
        if res["ambiguous_fill"]:
            diag["ambiguous"] += 1

    # ══════════════════════════════════════════════════════════════════
    #  HEDGE SOURCING (SELL only — TMA_V2 / IC_SYNTH_WING verbatim)
    # ══════════════════════════════════════════════════════════════════
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

    def _select_hedge(side: str, ts: int, ladder: List[tuple], meta: dict,
                      want_expiry: str, day_start: int) -> Optional[dict]:
        pick = select_strike(ladder, hedge_cfg["premium_max"])
        if pick is not None:
            sym = pick[0]
            fill = _price_at(sym, day_start, ts)
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
        fb = select_strike(ladder, hedge_cfg["premium_max"],
                           fallback_cheapest=True)
        if fb is None:
            return None
        sym = fb[0]
        fill = _price_at(sym, day_start, ts)
        if fill is None:
            return None
        diag["hedge_cheapest_fb"] += 1
        return {"kind": "real", "symbol": sym, "entry": fill,
                "strike": (meta.get(sym) or {}).get("strike"), "iv": None}

    def _hedge_exit_price(pos: dict, exit_ts: int) -> float:
        eday = exit_ts - ((exit_ts + IST) % 86400)
        if pos.get("h_kind") == "real":
            rows = _opt_1m(pos["h_symbol"], eday)
            px = next((c["close"] for c in rows if c["ts"] == exit_ts), None)
            if px is None:
                before = [c["close"] for c in rows if c["ts"] <= exit_ts]
                px = before[-1] if before else None
            if px is not None:
                return float(px)
        else:
            spot_x = _spot_close_at(exit_ts)
            if spot_x and pos.get("h_iv"):
                tau = SW.tau_years(exit_ts, _expiry_ts(pos["expiry"]))
                return SW.price_wing(pos["h_side_is_call"], spot_x,
                                     float(pos["h_strike"]), tau, pos["h_iv"])
        diag["hedge_exit_fallbacks"] += 1
        return float(pos["h_entry"])

    # ══════════════════════════════════════════════════════════════════
    #  CORPUS HELPERS (per-day cache — preload_day is the known hot spot)
    # ══════════════════════════════════════════════════════════════════
    _1m_cache: Dict[Tuple[str, int], List[dict]] = {}
    _5m_cache: Dict[Tuple[str, int], List[dict]] = {}

    def _opt_1m(sym: str, day_start: int) -> List[dict]:
        key = (sym, day_start)
        hit = _1m_cache.get(key)
        if hit is None:
            hit = [{"ts": x.ts, "open": x.open, "high": x.high, "low": x.low,
                    "close": x.close, "volume": x.volume}
                   for x in src.candles_1m_for_symbol_day(sym, day_start)]
            _1m_cache[key] = hit
        return hit

    def _opt_5m(sym: str, day_start: int) -> List[dict]:
        key = (sym, day_start)
        hit = _5m_cache.get(key)
        if hit is None:
            hit = [b for b in aggregate(_opt_1m(sym, day_start), tf_min,
                                        day_start) if b["complete"]]
            _5m_cache[key] = hit
        return hit

    def _price_at(sym: str, day_start: int, ts: int) -> Optional[float]:
        for c in _opt_1m(sym, day_start):
            if c["ts"] == ts:
                return float(c["close"])
        return None

    def _atr_at(sym: str, day_start: int, sig_ts: int) -> Optional[float]:
        """Wilder ATR of the TRADED leg (N4) on the last completed 5m bar
        at or before the signal. A gap in the traded series falls back to
        the newest earlier bar rather than skipping the trade — the ATR
        is a stop WIDTH, and a five-minute-stale width is materially
        better than no trade record at all."""
        if sl_mode != "ATR":
            return None
        bars = _opt_5m(sym, day_start)
        if not bars:
            return None
        vals = atr_wilder(bars, atr_period)
        best = None
        for i, b in enumerate(bars):
            if int(b["ts"]) + tf_s <= sig_ts and vals[i] is not None:
                best = vals[i]
        return best

    def _monitor(pos: dict, cands: List[dict], eod_ts: int,
                 sl_level: Optional[float]) -> Optional[dict]:
        """── SL_GRACE_20260820 ── monitor with the SL suspended for the
        first sl_grace_min minutes.

        TWO sequential calls to an UNMODIFIED monitor_position_day. That
        function is the parity reference test_tma_live_core and
        test_tma2_live_core assert the LIVE TMA V1/V2 engines against, so
        it does not get a new kwarg for a backtest-only experiment.

        Phase 1 runs with the SL disabled (or set to the wider disaster
        level) and a hard bound at the grace expiry. Phase 2 resumes with
        the real SL from that instant. If the premium is ALREADY beyond
        the SL when phase 2 opens, monitor's existing gap branch fills at
        that candle's OPEN — a market fill, which is the honest outcome
        and one we get for free by reusing rather than reimplementing.
        """
        if sl_grace_min <= 0:
            return monitor_position_day(pos, cands, None, eod_ts, "EOD")

        short = pos["action"] == "SELL"
        ep = float(pos["entry_price"])
        dis = None
        if sl_grace_disaster_pct > 0:
            dis = sl_tp_levels(ep, pos["action"], sl_grace_disaster_pct, 0,
                               "PCT", "PCT")[0]
        g_end = int(pos["entry_ts"]) + sl_grace_min * 60
        g_cands = [c for c in cands if int(c["ts"]) < g_end]

        # Diagnostic: would this trade have stopped out inside the window?
        breached = breached_during(g_cands, sl_level, short)
        if breached:
            diag["grace_breached"] += 1

        def _finish(res, tag):
            if res is not None:
                res = dict(res)
                # phase 1 ran with a different (or absent) SL — report the
                # REAL level so the trade row is not misleading.
                res["sl_price"] = sl_level
                if breached:
                    diag[tag] += 1
            return res

        p1 = dict(pos)
        p1["sl_price"] = dis
        if g_end >= eod_ts:
            # Grace covers the whole remaining session: the SL never arms.
            r = monitor_position_day(p1, cands, None, eod_ts, "EOD")
            if r is not None and r["exit_reason"] == "SL":
                diag["grace_disaster_exits"] += 1
                return _finish(r, "grace_breach_then_disaster")
            return _finish(r, "grace_breach_then_eod")

        r1 = monitor_position_day(p1, g_cands, None, g_end, "GRACE_END")
        if r1 is not None and r1["exit_reason"] != "GRACE_END":
            if r1["exit_reason"] == "SL":
                # only reachable with a disaster stop armed
                diag["grace_disaster_exits"] += 1
                return _finish(r1, "grace_breach_then_disaster")
            diag["grace_tp_in_window"] += 1
            return _finish(r1, "grace_breach_then_tp")

        p2 = dict(pos)
        p2["sl_price"] = sl_level
        p2["watch_from"] = g_end
        p2["last_close"] = p1.get("last_close", ep)
        p2["last_ts"] = p1.get("last_ts", int(pos["entry_ts"]))
        r2 = monitor_position_day(
            p2, [c for c in cands if int(c["ts"]) >= g_end],
            None, eod_ts, "EOD")
        if r2 is None:
            return None
        tag = ("grace_breach_then_sl" if r2["exit_reason"] == "SL"
               else "grace_breach_then_tp" if r2["exit_reason"] == "TP"
               else "grace_breach_then_eod")
        return _finish(r2, tag)

    def _patch_partial(res, pos, hard_ts, spot_rows):
        """── EXPIRY_INTRINSIC ── (TMA_V1 verbatim) when the held symbol's
        candles run out well before the square-off bound, mark to
        intrinsic rather than to a stale last trade — on expiry day a
        dead-quiet OTM strike would otherwise be booked at its last
        printed premium instead of at zero."""
        if (res is None or hard_ts is None
                or res["exit_reason"] != "EOD"
                or res["exit_ts"] >= hard_ts - 120):
            return res
        for c_ in reversed(spot_rows):
            if c_["ts"] < hard_ts:
                k = float(pos.get("strike") or 0)
                sp = float(c_["close"])
                intr = (sp - k) if pos["side"] == "CE" else (k - sp)
                diag["expiry_intrinsic_closes"] += 1
                res = dict(res)
                res["exit_ts"] = int(c_["ts"])
                res["exit_price"] = round(max(0.05, intr), 2)
                return res
        return res

    # ══════════════════════════════════════════════════════════════════
    #  DAY LOOP
    # ══════════════════════════════════════════════════════════════════
    for di, d in enumerate(spot_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(spot_days),
                         "date": d.isoformat()})
        _1m_cache.clear()
        _5m_cache.clear()

        day_start = _day_start_epoch(d)
        session0 = day_start + SESSION_ANCHOR_MIN * 60
        selection_ts = day_start + sel_min * 60
        entry_start_ts = day_start + sess_start_min * 60
        entry_end_ts = day_start + sess_end_min * 60
        eod_ts = day_start + exit_min * 60

        spot = [dict(r) for r in cur.execute("""
            SELECT ts, open, high, low, close FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
            ORDER BY ts""", (underlying, day_start, day_start + 86400))]
        if not spot:
            continue

        universe = src.contracts_active_on_day(underlying, day_start)
        if not universe:
            diag["days_no_options"] += 1
            continue
        want_expiry = expected_expiry_for_day(d).isoformat()
        week = [c for c in universe if c.get("expiry") == want_expiry]
        if not week:
            diag["days_uncovered"] += 1
            continue
        meta = {c["tradingsymbol"]: c for c in week}
        by_side = {s: [c["tradingsymbol"] for c in week
                       if c["instrument_type"] == s] for s in LEGS}

        # ── SIGNAL CONTRACT SELECTION (once per day, held) ────────────
        sig_sym: Dict[str, Optional[str]] = {"CE": None, "PE": None}
        for s in LEGS:
            cands = []
            for sym in by_side.get(s, []):
                px = _price_at(sym, day_start, selection_ts - 60)
                # ── min_premium ── a hard floor, applied BEFORE the cap:
                # sub-₹60 weeklies have a near-random VWAP (tick-size
                # granularity swamps the mean) and would dominate the
                # signal count without carrying information.
                if px and px >= min_prem:
                    cands.append((sym, px))
            pick = select_strike(cands, sig_prem_max)
            if pick is not None:
                sig_sym[s] = pick[0]
            else:
                diag[f"days_no_signal_{s.lower()}"] += 1
        if not any(sig_sym.values()):
            # ── D5 ── logged, never silently dropped: a premium band that
            # falls outside Dhan's ATM±10 cap early in a monthly cycle is
            # a DATA limit, and a run that quietly skips those days would
            # read as "the strategy didn't trade" instead of "we couldn't
            # see the strikes".
            diag["days_no_signal_leg"] += 1
            continue

        # ── PER-LEG VWAP + FACTS ──────────────────────────────────────
        facts_by_ts: Dict[str, Dict[int, dict]] = {"CE": {}, "PE": {}}
        for s in LEGS:
            sym = sig_sym[s]
            if not sym:
                continue
            bars1 = [c for c in _opt_1m(sym, day_start) if c["ts"] >= session0]
            bars5 = _opt_5m(sym, day_start)
            diag[f"bars5_{s.lower()}"] += len(bars5)
            vmap = vwap_by_minute(bars1)
            # ── ENTRY_FILTERS_20260820 ── EMA and volume are computed on
            # the SIGNAL contract, the same series the VWAP belongs to.
            emap = (ema_at_bar_ends(bars1, bars5, tf_s=tf_s,
                                    period=ema_period,
                                    basis_minutes=ema_basis)
                    if ema_period > 0 else None)
            volmap = (bucket_volumes(bars1, bars5, tf_s=tf_s)
                      if vol_mult > 0 else None)
            for f in leg_bar_facts(bars5, vmap, tf_s=tf_s,
                                   buffer_pct=buffer_pct,
                                   ema_at=emap, vol_at=volmap,
                                   vol_mult=vol_mult,
                                   vol_lookback=vol_lookback):
                facts_by_ts[s][f["ts_end"]] = f

        grid = sorted(set(facts_by_ts["CE"]) | set(facts_by_ts["PE"]))
        if not grid:
            continue

        armed = {s: (not require_arm) for s in LEGS}
        entries = {s: 0 for s in LEGS}
        busy_until = {s: -1 for s in LEGS}
        taken_today = 0

        def _is_busy(leg: str, ts: int) -> bool:
            # ── N1 ── with both sides disabled the legs share ONE slot,
            # so either leg being live blocks the other.
            if allow_both:
                return ts < busy_until[leg]
            return ts < max(busy_until["CE"], busy_until["PE"])

        for ts_end in grid:
            want: List[str] = []
            for s in LEGS:
                f = facts_by_ts[s].get(ts_end)
                if f is None:
                    continue
                act, armed[s] = decide_leg(
                    above=f["above"], below=f["below"], armed=armed[s],
                    busy=_is_busy(s, ts_end), entries=entries[s],
                    max_entries=max_per_leg,
                    ema_ok=f.get("ema_ok", True),
                    vol_ok=f.get("vol_ok", True))
                if act == "WARMUP":
                    diag["blocked_warmup"] += 1
                elif act == "BUSY":
                    diag["skipped_busy"] += 1
                elif act == "ARM":
                    diag[f"arm_events_{s.lower()}"] += 1
                elif act == "CAP":
                    diag["blocked_cap"] += 1
                elif act == "EMA_BLOCK":
                    diag["blocked_ema"] += 1
                elif act == "EMA_WARMUP":
                    diag["blocked_ema_warmup"] += 1
                elif act == "VOL_BLOCK":
                    diag["blocked_vol"] += 1
                elif act == "VOL_WARMUP":
                    diag["blocked_vol_warmup"] += 1
                elif act == "ENTER":
                    diag[f"enter_events_{s.lower()}"] += 1
                    diag["signals_total"] += 1
                    if not (entry_start_ts <= ts_end < entry_end_ts):
                        diag["blocked_session"] += 1
                        continue
                    want.append(s)

            # ── N1 ── simultaneous two-sided break: take NEITHER. Both
            # legs stay ARMED, so the pair sits out exactly as long as
            # the conflict holds rather than being permanently consumed.
            if len(want) > 1 and not allow_both:
                diag["skipped_conflict"] += 1
                want = []

            for sig_leg in want:
                if _is_busy(sig_leg, ts_end):
                    diag["skipped_busy"] += 1
                    continue
                # ── D7/N2 ── BUY trades the signal side; SELL trades the
                # OPPOSITE side plus a same-side deeper-OTM hedge.
                if mode == "SELL":
                    trd_leg = "PE" if sig_leg == "CE" else "CE"
                    action = "SELL"
                    ladder = []
                    for sym in by_side.get(trd_leg, []):
                        px = _price_at(sym, day_start, ts_end - 60)
                        if px:
                            ladder.append((sym, px))
                    pick = select_strike(ladder, main_cfg["premium_max"])
                    if pick is None:
                        diag["skipped_select"] += 1
                        continue
                    trd_sym = pick[0]
                else:
                    # ── N3 ── in BUY mode the signal leg IS the traded
                    # leg; main.premium_max is deliberately not consulted.
                    trd_leg = sig_leg
                    action = "BUY"
                    trd_sym = sig_sym[sig_leg]

                ep = _price_at(trd_sym, day_start, ts_end)
                if ep is None or ep <= 0:
                    diag["skipped_select"] += 1
                    continue

                sized, why = size_sl_tp(
                    entry_price=ep, sl_mode=sl_mode, sl_pct=sl_pct,
                    atr_value=_atr_at(trd_sym, day_start, ts_end),
                    atr_mult=atr_mult, max_sl_pct=max_sl_pct,
                    tp_mode=tp_mode, rr=rr, tp_pct=tp_pct)
                if sized is None:
                    # ── D9 ── ATR is unwarmed until ~09:50 with the
                    # default period. The trade is SKIPPED, never
                    # silently re-sized with a %-stop: swapping stop
                    # semantics mid-session would put two different
                    # strategies in one results row.
                    diag["blocked_atr_warmup" if why == "atr_warmup"
                         else "skipped_select"] += 1
                    continue
                if sized["clamped"]:
                    diag["sl_clamped"] += 1

                sl_level, tp_level = sl_tp_levels(
                    ep, action, sized["sl_val"], sized["tp_val"],
                    sized["sl_unit"], sized["tp_unit"])

                hedge = None
                if mode == "SELL":
                    hl = []
                    for hsym in by_side.get(trd_leg, []):
                        if hsym == trd_sym:
                            continue
                        hpx = _price_at(hsym, day_start, ts_end - 60)
                        if hpx:
                            hl.append((hsym, hpx))
                    hedge = _select_hedge(trd_leg, ts_end, hl, meta,
                                          want_expiry, day_start)
                    if hedge is None:
                        diag["skipped_hedge"] += 1
                        continue

                m = meta.get(trd_sym, {})
                pos = {"sig_leg": sig_leg, "side": trd_leg, "action": action,
                       "symbol": trd_sym, "lots": main_cfg["lots"],
                       "strike": m.get("strike"), "expiry": m.get("expiry"),
                       "entry_ts": ts_end, "entry_price": ep,
                       "sl_price": sl_level, "tp_price": tp_level,
                       "watch_from": ts_end + 60,
                       "last_close": ep, "last_ts": ts_end}
                if hedge is not None:
                    pos.update({"h_symbol": hedge["symbol"],
                                "h_entry": float(hedge["entry"]),
                                "h_strike": hedge.get("strike"),
                                "h_lots": hedge_cfg["lots"],
                                "h_kind": hedge["kind"],
                                "h_iv": hedge.get("iv"),
                                "h_side_is_call": trd_leg == "CE"})

                cands = [c for c in _opt_1m(trd_sym, day_start)
                         if c["ts"] >= ts_end + 60]
                res = _monitor(pos, cands, eod_ts, sl_level)
                res = _patch_partial(res, pos, eod_ts, spot)
                if res is None:
                    # Intraday always has a hard bound, so monitor cannot
                    # return None. Belt and braces: a None here would mean
                    # a position silently outliving the session.
                    diag["skipped_select"] += 1
                    continue
                _emit(res, pos)
                entries[sig_leg] += 1
                taken_today += 1
                diag["signals_taken"] += 1
                busy_until[sig_leg] = int(res["exit_ts"]) + 60
                if not allow_both:
                    for s in LEGS:
                        busy_until[s] = int(res["exit_ts"]) + 60
                armed[sig_leg] = False

        if taken_today:
            diag["days_traded"] += 1

    conn.close()
    try:
        src.close()
    except Exception:
        pass
    summary = _summarize(trades, diag, mode)
    write_audit_log(
        f"[BACKTEST][VAP_V1][{mode}] {underlying} {date_from}→{date_to}: "
        f"{diag['days_traded']}/{diag['days_total']} days traded, "
        f"{diag['signals_taken']}/{diag['signals_total']} signals taken "
        f"(CE {diag['enter_events_ce']} / PE {diag['enter_events_pe']}), "
        f"{len(trades)} rows, net {summary['net_pnl']}, "
        f"sl {diag['sl_rule']} tp {diag['tp_rule']} "
        f"(clamped {diag['sl_clamped']}), "
        f"bothSides {diag['both_sides']} conflictSkip "
        f"{diag['skipped_conflict']}, "
        f"ema {diag['ema_filter']} (blk {diag['blocked_ema']} "
        f"warm {diag['blocked_ema_warmup']}) "
        f"vol {diag['vol_filter']} (blk {diag['blocked_vol']} "
        f"warm {diag['blocked_vol_warmup']}), "
        f"grace {diag['sl_grace_min']} "
        f"(breach {diag['grace_breached']} -> sl {diag['grace_breach_then_sl']} "
        f"tp {diag['grace_breach_then_tp']} eod {diag['grace_breach_then_eod']} "
        f"dis {diag['grace_breach_then_disaster']}), "
        f"warmupBlk {diag['blocked_warmup']} atrBlk "
        f"{diag['blocked_atr_warmup']} sessBlk {diag['blocked_session']} "
        f"capBlk {diag['blocked_cap']} busySkip {diag['skipped_busy']}, "
        f"noSignalLeg {diag['days_no_signal_leg']} "
        f"(CE {diag['days_no_signal_ce']} / PE {diag['days_no_signal_pe']})")
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "trades": trades, "config": cfg, "strategy_id": strategy_id}