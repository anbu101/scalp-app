# backend/app/backtest/pst/pst_v1_engine.py
#
# ── PST_V1 ENGINE ── Pivot + SMA + SuperTrend spot-signal option scalper.
# Signals computed on NIFTY SPOT; execution on weekly option premium.
# Replicates the Quantman pair "N50 CE/PE PPSTEMA(2/1 LOTS)" as ONE strategy.
#
# LOCKED SPEC (2026-07-06):
#   * Signal TF 3m (aggregated from 1m spot, session-aligned); evaluated on
#     COMPLETED bars only. Entry executes on the NEXT 1m candle after the
#     signal bar closes (option side; runner selects premium <150
#     nearest-below at that minute, both legs typically same strike).
#   * BULL event: 3m close crosses ABOVE any Traditional pivot level AND
#     close > SMA(9 on 5m, last completed 5m) AND close > SuperTrend(10,×2 on
#     3m) → buy CE. BEAR mirrored (crosses below / < / <) → buy PE.
#   * Legs: L1 2 lots + L2 1 lot, SL 15% on PREMIUM (intrabar at trigger),
#     StopGain in SPOT POINTS from spot-at-entry: L1 +20, L2 +50 (CE; minus
#     for PE). Spot target triggers intrabar on the 1m SPOT candle; the
#     option leg exits at that same minute's option CLOSE (the honest
#     discretization — the intraminute option price at the touch instant is
#     unknowable).
#   * Premium-SL and spot-TG in the SAME minute → SL wins + ambiguous flag
#     (house convention).
#   * ONE position at a time (global across sides); re-entry allowed the
#     same day once flat (signal ts >= last exit ts). Optional daily cap.
#   * EOD square-off at exit_time. No signal-based exit (Quantman: Is Empty).
#   * Indicator warmup is real: SuperTrend(10)@3m needs 10 bars (~09:45),
#     SMA(9)@5m needs 9 bars (~10:00) — the first legal signal of a day is
#     ~10:00. The engine returns warmup diagnostics so this is visible, not
#     mysterious.
#
# Pure module: consumes candle dicts, returns dicts. Runner does corpus,
# selection, charges, persistence.

from __future__ import annotations

from typing import Callable, Dict, List, Optional

try:
    from app.backtest.pst.pst_indicators import (
        aggregate, sma, supertrend, traditional_pivots, crosses,
    )
except ImportError:  # standalone tests
    from pst_indicators import (  # type: ignore
        aggregate, sma, supertrend, traditional_pivots, crosses,
    )


# ──────────────────────────────────────────────────────────────────────
# signal generation (spot only)
# ──────────────────────────────────────────────────────────────────────
def build_signals(spot_1m: List[dict], day_start: int,
                  prev_hlc: Dict[str, float],
                  *, signal_tf: int = 3, sma_period: int = 9, sma_tf: int = 5,
                  st_period: int = 10, st_mult: float = 2.0,
                  session_start_min: int = 9 * 60 + 15,
                  entry_cutoff_min: int = 15 * 60 + 0) -> Dict:
    """Returns {"signals": [{ts, side, bar_close, spot, levels_crossed}],
    "diag": {...}}. `ts` is the SIGNAL BAR COMPLETION time (bar.ts + tf) —
    the runner enters on the 1m candle starting at ts. `spot` is the signal
    bar's close (the spot anchor for the point-targets)."""
    piv = traditional_pivots(prev_hlc["high"], prev_hlc["low"], prev_hlc["close"])
    bars3 = [b for b in aggregate(spot_1m, signal_tf, day_start) if b["complete"]]
    bars5 = [b for b in aggregate(spot_1m, sma_tf, day_start) if b["complete"]]
    st = supertrend(bars3, period=st_period, mult=st_mult)
    sma5 = sma([b["close"] for b in bars5], sma_period)
    ts5 = [b["ts"] for b in bars5]

    def sma_at(ts_end: int) -> Optional[float]:
        # last completed 5m bar whose END <= signal bar END (no lookahead)
        best = None
        for j, t in enumerate(ts5):
            if t + sma_tf * 60 <= ts_end and sma5[j] is not None:
                best = sma5[j]
        return best

    signals = []
    diag = {"bars3": len(bars3), "bull_events": 0, "bear_events": 0,
            "blocked_warmup": 0, "blocked_gate": 0}
    cutoff = day_start + entry_cutoff_min * 60
    for i in range(1, len(bars3)):
        close = bars3[i]["close"]
        prev_close = bars3[i - 1]["close"]
        x = crosses(prev_close, close, piv)
        bull, bear = bool(x["above"]), bool(x["below"])
        if not (bull or bear):
            continue
        diag["bull_events" if bull else "bear_events"] += 1
        ts_end = bars3[i]["ts"] + signal_tf * 60
        if ts_end >= cutoff:
            continue
        s = sma_at(ts_end)
        stv = st[i]
        if s is None or stv is None:
            diag["blocked_warmup"] += 1
            continue
        if bull and close > s and close > stv["st"]:
            signals.append({"ts": ts_end, "side": "CE", "spot": close,
                            "levels_crossed": x["above"]})
        elif bear and close < s and close < stv["st"]:
            signals.append({"ts": ts_end, "side": "PE", "spot": close,
                            "levels_crossed": x["below"]})
        else:
            diag["blocked_gate"] += 1
    diag["signals"] = len(signals)
    return {"signals": signals, "pivots": piv, "diag": diag}


# ──────────────────────────────────────────────────────────────────────
# one entered position (2 legs, same option symbol) — trade state machine
# ──────────────────────────────────────────────────────────────────────
def simulate_position(legs: List[dict], side: str, entry_ts: int,
                      entry_price: float, spot_entry: float,
                      opt_candles: List[dict], spot_1m: List[dict],
                      eod_ts: int) -> Dict:
    """legs: [{id, lots, sl_pct, spot_tg_points}]. Both legs enter the SAME
    option at entry_price on the candle starting at entry_ts.
      SL leg  : premium <= entry*(1-sl_pct/100)? NO — long option: SL BELOW
                entry: trigger when option LOW <= sl_price; fill AT trigger.
      Spot TG : CE target = spot_entry + pts (spot HIGH >= target);
                PE target = spot_entry - pts (spot LOW  <= target);
                option exits at THAT minute's option CLOSE.
      Both in one minute → SL wins + ambiguous flag.
      EOD     : close of the last option candle strictly before eod_ts.
    Returns {"trades":[...], "last_exit_ts": int, "flags":{ambiguous}}."""
    is_ce = side == "CE"
    opt_by_ts = {int(c["ts"]): c for c in opt_candles}
    spot_by_ts = {int(c["ts"]): c for c in spot_1m}
    all_ts = sorted(t for t in opt_by_ts if entry_ts <= t < eod_ts)

    state = {}
    for leg in legs:
        sl_price = max(0.05, entry_price * (1 - float(leg["sl_pct"]) / 100.0)) \
            if float(leg.get("sl_pct") or 0) > 0 else None
        tgt = (spot_entry + float(leg["spot_tg_points"])) if is_ce \
            else (spot_entry - float(leg["spot_tg_points"]))
        state[leg["id"]] = {"leg": leg, "open": True, "sl": sl_price,
                            "spot_target": tgt if float(leg.get("spot_tg_points") or 0) > 0 else None,
                            "exit": None, "last_close": entry_price, "last_ts": entry_ts}

    ambiguous = 0
    for ts in all_ts:
        oc = opt_by_ts[ts]
        sc = spot_by_ts.get(ts)
        for lid, st_ in state.items():
            if not st_["open"]:
                continue
            st_["last_close"] = float(oc["close"])
            st_["last_ts"] = ts
            hit_sl = st_["sl"] is not None and float(oc["low"]) <= st_["sl"]
            hit_tg = False
            if st_["spot_target"] is not None and sc is not None:
                hit_tg = (float(sc["high"]) >= st_["spot_target"]) if is_ce \
                    else (float(sc["low"]) <= st_["spot_target"])
            if hit_sl:
                st_["open"] = False
                st_["exit"] = (ts, st_["sl"], "SL", hit_tg)
                if hit_tg:
                    ambiguous += 1
            elif hit_tg:
                st_["open"] = False
                st_["exit"] = (ts, float(oc["close"]), "SPOT_TG", False)
        if all(not s["open"] for s in state.values()):
            break

    trades = []
    last_exit = entry_ts
    for lid, st_ in state.items():
        if st_["open"]:
            st_["exit"] = (st_["last_ts"], st_["last_close"], "EOD", False)
            st_["open"] = False
        ets, epx, reason, amb = st_["exit"]
        last_exit = max(last_exit, ets)
        trades.append({
            "leg": lid, "side": side, "lots": int(st_["leg"]["lots"]),
            "entry_ts": entry_ts, "entry_price": entry_price,
            "sl_price": st_["sl"], "spot_target": st_["spot_target"],
            "spot_entry": spot_entry,
            "exit_ts": ets, "exit_price": epx, "exit_reason": reason,
            "ambiguous_fill": bool(amb),
        })
    trades.sort(key=lambda t: t["leg"])
    return {"trades": trades, "last_exit_ts": last_exit,
            "flags": {"ambiguous": ambiguous}}


# ──────────────────────────────────────────────────────────────────────
# day orchestration: one-at-a-time, same-day re-entry, daily cap
# ──────────────────────────────────────────────────────────────────────
def run_day(signals: List[dict], legs: List[dict],
            select_option: Callable[[str, int], Optional[dict]],
            spot_1m: List[dict], eod_ts: int,
            *, side_mode: str = "BOTH", max_trades_per_day: int = 0) -> Dict:
    """select_option(side, entry_ts) -> {"symbol", "entry_price",
    "candles"} or None (no eligible strike / no entry price → signal
    skipped, counted). ONE position at a time across BOTH sides; a new
    signal is taken only when signal.ts >= last position's exit."""
    positions = []
    diag = {"signals_taken": 0, "signals_skipped_busy": 0,
            "signals_skipped_side": 0, "signals_skipped_select": 0,
            "signals_skipped_cap": 0, "ambiguous": 0}
    busy_until = -1
    for sig in signals:
        if side_mode != "BOTH" and sig["side"] != side_mode:
            diag["signals_skipped_side"] += 1
            continue
        if sig["ts"] < busy_until:
            diag["signals_skipped_busy"] += 1
            continue
        if max_trades_per_day and diag["signals_taken"] >= max_trades_per_day:
            diag["signals_skipped_cap"] += 1
            continue
        if sig["ts"] >= eod_ts:
            continue
        sel = select_option(sig["side"], sig["ts"])
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position(legs, sig["side"], sig["ts"],
                                float(sel["entry_price"]), float(sig["spot"]),
                                sel["candles"], spot_1m, eod_ts)
        for t in pos["trades"]:
            t["tradingsymbol"] = sel["symbol"]
            t["signal_levels"] = ",".join(sig.get("levels_crossed") or [])
        positions.append(pos)
        diag["signals_taken"] += 1
        diag["ambiguous"] += pos["flags"]["ambiguous"]
        busy_until = pos["last_exit_ts"] + 60   # flat from the NEXT minute
    trades = [t for p in positions for t in p["trades"]]
    return {"trades": trades, "diag": diag}