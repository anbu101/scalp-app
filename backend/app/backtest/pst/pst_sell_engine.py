# backend/app/backtest/pst/pst_sell_engine.py
#
# ── PST_SELL ENGINE ── PST_V1's signal, INVERTED to option SELLING (SHORT).
#
# We SELL the same selected contract PST_V1 would have bought, at the same
# entry, and buy it back to exit. Because we're short, PST_V1's SL/TP levels
# SWAP ROLES (confirmed decision set D1–D8, 2026-07-13):
#
#   Seller TP (was buyer's premium SL, D2):
#     tp = entry_premium × (1 − sl_pct/100). Premium FALLING here is our
#     profit. Trigger INTRABAR on option LOW <= tp; fill AT the tp level —
#     the exact mirror of PST_V1's SL-at-trigger convention.
#     exit_reason = "TP".
#
#   Seller SL (was buyer's spot StopGain, D3):
#     CE: spot HIGH >= spot_entry + spot_tg_points
#     PE: spot LOW  <= spot_entry − spot_tg_points
#     Spot moving with the option holder is AGAINST the seller. Exit at
#     THAT minute's option CLOSE (the same honest discretization PST_V1
#     uses for SPOT_TG — the intraminute option price at the touch instant
#     is unknowable). NOTE: this loss is NOT capped at a premium level; a
#     fast spot move fills at whatever the option close prints.
#     exit_reason = "SPOT_SL".
#
#   Ambiguity (D4): premium-TP and spot-SL in the SAME minute → SL wins
#     (pessimistic — the loss wins, preserving the house convention; the
#     winner flips with the loss side) + ambiguous flag.
#
#   EOD: buy back at the close of the last option candle strictly before
#     eod_ts. exit_reason = "EOD".
#
# SIGNALS ARE NOT REDEFINED HERE. build_signals is imported from
# pst_v1_engine untouched, so PST_SELL enters on byte-identical signals to
# PST_V1 (D1). Leg config keys stay PST_V1's (`sl_pct`, `spot_tg_points`) —
# same config drives both strategies, only the ROLES swap (D6).
#
# P&L SHORT = (entry − exit) × qty; charges via charges_for_short_trade —
# both applied in the RUNNER (backtest_pst_sell_runner), not here (D5).
#
# Pure module: consumes candle dicts, returns dicts. Runner does corpus,
# selection, charges, persistence — same split as pst_v1_engine.

from __future__ import annotations

from typing import Callable, Dict, List, Optional

# ── PST_SELL_SHARED_SIGNALS BEGIN ── signal generation is PST_V1's, verbatim.
try:
    from app.backtest.pst.pst_v1_engine import build_signals  # noqa: F401
except ImportError:  # standalone tests
    from pst_v1_engine import build_signals  # type: ignore  # noqa: F401
# ── PST_SELL_SHARED_SIGNALS END ──


# ──────────────────────────────────────────────────────────────────────
# one entered SHORT position (2 legs, same option symbol) — state machine
# ──────────────────────────────────────────────────────────────────────
def simulate_position_short(legs: List[dict], side: str, entry_ts: int,
                            entry_price: float, spot_entry: float,
                            opt_candles: List[dict], spot_1m: List[dict],
                            eod_ts: int) -> Dict:
    """legs: [{id, lots, sl_pct, spot_tg_points}] — PST_V1's leg shape.
    Both legs SELL the SAME option at entry_price on the candle starting at
    entry_ts. Role swap vs simulate_position:
      TP  : premium <= entry×(1−sl_pct/100); trigger on option LOW <= tp;
            fill AT the tp level. sl_pct = 0 → no premium TP.
      SL  : CE spot HIGH >= spot_entry + spot_tg_points (PE mirrored);
            option exits at THAT minute's option CLOSE.
            spot_tg_points = 0 → no spot SL (rides to EOD).
      Both in one minute → SL wins + ambiguous flag (pessimistic).
      EOD : close of the last option candle strictly before eod_ts.
    Returns {"trades":[...], "last_exit_ts": int, "flags":{ambiguous}}.
    Trade dicts carry tp_price (premium level) and spot_sl (spot level);
    the runner persists tp = tp_price and sl = NULL (spot isn't an option
    price — mirror of PST_V1 persisting sl and tp = NULL)."""
    is_ce = side == "CE"
    opt_by_ts = {int(c["ts"]): c for c in opt_candles}
    spot_by_ts = {int(c["ts"]): c for c in spot_1m}
    all_ts = sorted(t for t in opt_by_ts if entry_ts <= t < eod_ts)

    state = {}
    for leg in legs:
        # seller TP = PST_V1's SL level (premium, below entry)
        tp_price = max(0.05, entry_price * (1 - float(leg["sl_pct"]) / 100.0)) \
            if float(leg.get("sl_pct") or 0) > 0 else None
        # seller SL = PST_V1's spot target level (spot, against the seller)
        sl_spot = (spot_entry + float(leg["spot_tg_points"])) if is_ce \
            else (spot_entry - float(leg["spot_tg_points"]))
        state[leg["id"]] = {"leg": leg, "open": True, "tp": tp_price,
                            "spot_sl": sl_spot if float(leg.get("spot_tg_points") or 0) > 0 else None,
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
            hit_tp = st_["tp"] is not None and float(oc["low"]) <= st_["tp"]
            hit_sl = False
            if st_["spot_sl"] is not None and sc is not None:
                hit_sl = (float(sc["high"]) >= st_["spot_sl"]) if is_ce \
                    else (float(sc["low"]) <= st_["spot_sl"])
            if hit_sl:
                # loss wins (pessimistic); fill at this minute's option CLOSE
                st_["open"] = False
                st_["exit"] = (ts, float(oc["close"]), "SPOT_SL", hit_tp)
                if hit_tp:
                    ambiguous += 1
            elif hit_tp:
                # profit; fill AT the tp level (mirror of V1's SL-at-trigger)
                st_["open"] = False
                st_["exit"] = (ts, st_["tp"], "TP", False)
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
            "tp_price": st_["tp"], "spot_sl": st_["spot_sl"],
            "spot_entry": spot_entry,
            "exit_ts": ets, "exit_price": epx, "exit_reason": reason,
            "ambiguous_fill": bool(amb),
        })
    trades.sort(key=lambda t: t["leg"])
    return {"trades": trades, "last_exit_ts": last_exit,
            "flags": {"ambiguous": ambiguous}}


# ──────────────────────────────────────────────────────────────────────
# day orchestration: one-at-a-time, same-day re-entry, daily cap.
# Structurally identical to pst_v1_engine.run_day — only the position
# simulator differs. Kept as a local copy (not a parameterization of the
# V1 function) so PST_V1's engine stays zero-diff (D1).
# ──────────────────────────────────────────────────────────────────────
def run_day_short(signals: List[dict], legs: List[dict],
                  select_option: Callable[[str, int], Optional[dict]],
                  spot_1m: List[dict], eod_ts: int,
                  *, side_mode: str = "BOTH", max_trades_per_day: int = 0) -> Dict:
    """select_option(side, entry_ts) -> {"symbol", "entry_price",
    "candles"} or None. ONE position at a time across BOTH sides; a new
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
        pos = simulate_position_short(legs, sig["side"], sig["ts"],
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