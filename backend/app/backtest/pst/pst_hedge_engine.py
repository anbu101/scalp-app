# backend/app/backtest/pst/pst_hedge_engine.py
#
# ── PST_HEDGE ENGINE (v2 — SIGNAL-TRACKED, D17-amended 2026-07-13) ──
#
# PST_HEDGE buys the app's already-selected OPPOSITE-side contract (same
# premium<cap nearest-below rule PST_SELL uses, run on the opposite side)
# while tracking BOTH exit events on PST_SELL's exact event stream:
#
#   SIG_TP  : the SIGNAL contract's premium falls to
#             sig_entry × (1 − sl_pct/100). Trigger intrabar on the SIGNAL
#             contract's 1m LOW <= level — byte-identical to PST_SELL's TP
#             trigger. sig_entry is the signal contract's close at ts (the
#             price PST_SELL would have filled its short at).
#   SPOT_SL : spot moves spot_tg_points WITH the signal (CE signal: spot
#             HIGH >= spot_entry + pts; PE mirrored) — byte-identical to
#             PST_SELL's SPOT_SL trigger.
#   Both in one minute → SPOT_SL wins + ambiguous flag (D20, mirrors SELL).
#   EOD     : close of the last HELD candle strictly before eod_ts.
#
# FILLS: every exit books at the HELD contract's CLOSE of the event minute
# (the levels live on instruments we don't hold — the signal contract and
# spot — so the held close is the only honest fill). If the held contract
# has no candle in the event minute, the last known held close is used.
#
# CONSEQUENCE (stated up front, not a bug): the event stream — which trades
# exist, when they exit, and why — reproduces PST_SELL trade-for-trade by
# construction. The P&L per event does NOT, because the held option collects
# only the delta share of each event and pays theta instead of collecting
# it. This build exists precisely to measure that gap.
#
# side_mode filters the SIGNAL side (D21 — reverts v1's traded-side rule),
# so side_mode="CE" reproduces PST_SELL's CE-only event stream.
#
# Pure module: consumes candle dicts, returns dicts. Runner does corpus,
# dual-side selection, charges, persistence.

from __future__ import annotations

from typing import Callable, Dict, List, Optional

# Signal generation is PST_V1's, verbatim (re-export for the runner).
try:
    from app.backtest.pst.pst_v1_engine import build_signals  # noqa: F401
except ImportError:  # standalone tests
    from pst_v1_engine import build_signals  # type: ignore  # noqa: F401


# ── PST_RISK_LIMITS BEGIN ── shared accumulator (V3 _rl_on_close parity):
# add a JUST-closed leg's NET to the day and month buckets and refresh the
# block flags. Mutates the runner-owned risk dict in place.
def _rl_add(risk: dict, net: float) -> None:
    risk["day_realized"] += net
    risk["month_realized"] += net
    if (risk["dml"] and risk["day_realized"] <= -risk["dml"]) or \
       (risk["dmp"] and risk["day_realized"] >= risk["dmp"]):
        risk["day_blocked"] = True
    if (risk["mml"] and risk["month_realized"] <= -risk["mml"]) or \
       (risk["mmp"] and risk["month_realized"] >= risk["mmp"]):
        risk["month_blocked"] = True
# ── PST_RISK_LIMITS END ──


# ──────────────────────────────────────────────────────────────────────
# one entered position: HELD = opposite-side long; levels on SIGNAL+spot
# ──────────────────────────────────────────────────────────────────────
def simulate_position_hedge(legs: List[dict], sig_side: str, entry_ts: int,
                            held_entry_price: float, sig_entry_price: float,
                            spot_entry: float,
                            held_candles: List[dict], sig_candles: List[dict],
                            spot_1m: List[dict], eod_ts: int,
                            risk: Optional[dict] = None) -> Dict:
    """legs: [{id, lots, sl_pct, spot_tg_points}] — PST_V1's leg shape,
    PST_SELL's level semantics:
      SIG_TP  : sig 1m LOW <= sig_entry×(1−sl_pct/100)   (sl_pct=0 → off)
      SPOT_SL : CE sig → spot HIGH >= spot_entry + pts;
                PE sig → spot LOW  <= spot_entry − pts    (pts=0 → off)
      Same minute → SPOT_SL wins + ambiguous flag.
      EOD     : last held close strictly before eod_ts.
    All exits fill at the HELD close of the event minute (or the last known
    held close if the held contract printed no candle that minute).
    Trade dicts carry sig_tp_level and spot_sl for persistence/debug."""
    is_ce_sig = sig_side == "CE"
    held_by_ts = {int(c["ts"]): c for c in held_candles}
    sig_by_ts = {int(c["ts"]): c for c in sig_candles}
    spot_by_ts = {int(c["ts"]): c for c in spot_1m}
    # iterate the union of minutes where ANY event/fill info exists
    all_ts = sorted(t for t in set(held_by_ts) | set(sig_by_ts)
                    if entry_ts <= t < eod_ts)

    state = {}
    for leg in legs:
        tp_level = max(0.05, sig_entry_price * (1 - float(leg["sl_pct"]) / 100.0)) \
            if float(leg.get("sl_pct") or 0) > 0 else None
        sl_spot = (spot_entry + float(leg["spot_tg_points"])) if is_ce_sig \
            else (spot_entry - float(leg["spot_tg_points"]))
        state[leg["id"]] = {"leg": leg, "open": True, "sig_tp": tp_level,
                            "spot_sl": sl_spot if float(leg.get("spot_tg_points") or 0) > 0 else None,
                            "exit": None, "last_close": held_entry_price,
                            "last_ts": entry_ts}

    ambiguous = 0
    for ts in all_ts:
        hc = held_by_ts.get(ts)
        sg = sig_by_ts.get(ts)
        sc = spot_by_ts.get(ts)
        # ── PST_RISK_LIMITS BEGIN ── intrabar clamp (V3 parity), checked
        # BEFORE the normal event logic; needs a HELD candle this minute
        # (mirrors V3 requiring hed_c). cum(period) = realized net + open
        # LONG MTM = R + (px − entry)·Q; LOSS threshold sits BELOW entry,
        # PROFIT above. Loss first (pessimistic); collision → loss wins +
        # ambiguous. Gap-throughs fill at the open.
        if risk is not None and risk.get("enabled") and hc is not None:
            _open = [s_ for s_ in state.values() if s_["open"]]
            if _open:
                _q = sum(float(s_["leg"]["lots"]) for s_ in _open) * float(risk["lot_size"])
                _loss = []
                if risk["dml"]:
                    _loss.append((held_entry_price - (risk["dml"] + risk["day_realized"]) / _q, "DAILY_MAX_LOSS"))
                if risk["mml"]:
                    _loss.append((held_entry_price - (risk["mml"] + risk["month_realized"]) / _q, "MONTHLY_MAX_LOSS"))
                _prof = []
                if risk["dmp"]:
                    _prof.append((held_entry_price + (risk["dmp"] - risk["day_realized"]) / _q, "DAILY_MAX_PROFIT"))
                if risk["mmp"]:
                    _prof.append((held_entry_price + (risk["mmp"] - risk["month_realized"]) / _q, "MONTHLY_MAX_PROFIT"))
                _lpx, _lreason = max(_loss, key=lambda x: x[0]) if _loss else (None, None)
                _ppx, _preason = min(_prof, key=lambda x: x[0]) if _prof else (None, None)
                _l_hit = _lpx is not None and float(hc["low"]) <= _lpx
                _p_hit = _ppx is not None and float(hc["high"]) >= _ppx
                _risk_exit = None
                if _l_hit:
                    _px = _lpx if float(hc["open"]) > _lpx else float(hc["open"])
                    _risk_exit = (_px, _lreason, bool(_p_hit))
                elif _p_hit:
                    _px = _ppx if float(hc["open"]) < _ppx else float(hc["open"])
                    _risk_exit = (_px, _preason, False)
                if _risk_exit is not None:
                    _px, _reason, _amb = _risk_exit
                    for s_ in _open:
                        s_["open"] = False
                        s_["exit"] = (ts, _px, _reason, _amb)
                        if _amb:
                            ambiguous += 1
                        _rl_add(risk, risk["pnl_fn"](held_entry_price, _px, int(s_["leg"]["lots"])))
                    if _reason.startswith("DAILY"):
                        risk["day_blocked"] = True
                    else:
                        risk["month_blocked"] = True
                    risk["risk_exits"] += 1
                    break
        # ── PST_RISK_LIMITS END ──
        for lid, st_ in state.items():
            if not st_["open"]:
                continue
            if hc is not None:
                st_["last_close"] = float(hc["close"])
                st_["last_ts"] = ts
            fill = float(hc["close"]) if hc is not None else st_["last_close"]
            hit_tp = (st_["sig_tp"] is not None and sg is not None
                      and float(sg["low"]) <= st_["sig_tp"])
            hit_sl = False
            if st_["spot_sl"] is not None and sc is not None:
                hit_sl = (float(sc["high"]) >= st_["spot_sl"]) if is_ce_sig \
                    else (float(sc["low"]) <= st_["spot_sl"])
            if hit_sl:
                # loss-event wins (pessimistic, mirrors PST_SELL's D4/D20)
                st_["open"] = False
                st_["exit"] = (ts, fill, "SPOT_SL", hit_tp)
                if hit_tp:
                    ambiguous += 1
                if risk is not None and risk.get("enabled"):   # ── PST_RISK_LIMITS ──
                    _rl_add(risk, risk["pnl_fn"](held_entry_price, fill, int(st_["leg"]["lots"])))
            elif hit_tp:
                st_["open"] = False
                st_["exit"] = (ts, fill, "SIG_TP", False)
                if risk is not None and risk.get("enabled"):   # ── PST_RISK_LIMITS ──
                    _rl_add(risk, risk["pnl_fn"](held_entry_price, fill, int(st_["leg"]["lots"])))
        if all(not s["open"] for s in state.values()):
            break

    trades = []
    last_exit = entry_ts
    for lid, st_ in state.items():
        if st_["open"]:
            st_["exit"] = (st_["last_ts"], st_["last_close"], "EOD", False)
            st_["open"] = False
            if risk is not None and risk.get("enabled"):   # ── PST_RISK_LIMITS ──
                _rl_add(risk, risk["pnl_fn"](held_entry_price, st_["last_close"], int(st_["leg"]["lots"])))
        ets, epx, reason, amb = st_["exit"]
        last_exit = max(last_exit, ets)
        trades.append({
            "leg": lid, "sig_side": sig_side, "lots": int(st_["leg"]["lots"]),
            "entry_ts": entry_ts, "entry_price": held_entry_price,
            "sig_tp_level": st_["sig_tp"], "spot_sl": st_["spot_sl"],
            "sig_entry": sig_entry_price, "spot_entry": spot_entry,
            "exit_ts": ets, "exit_price": epx, "exit_reason": reason,
            "ambiguous_fill": bool(amb),
        })
    trades.sort(key=lambda t: t["leg"])
    return {"trades": trades, "last_exit_ts": last_exit,
            "flags": {"ambiguous": ambiguous}}


# ──────────────────────────────────────────────────────────────────────
# day orchestration — one-at-a-time, same-day re-entry, daily cap.
# side_mode filters the SIGNAL side (D21). select_pair must return BOTH
# contracts or None (fail closed per signal).
# ──────────────────────────────────────────────────────────────────────
def run_day_hedge(signals: List[dict], legs: List[dict],
                  select_pair: Callable[[str, int], Optional[dict]],
                  spot_1m: List[dict], eod_ts: int,
                  *, side_mode: str = "BOTH", max_trades_per_day: int = 0,
                  risk: Optional[dict] = None) -> Dict:
    """select_pair(sig_side, entry_ts) -> {"sig_symbol", "sig_entry",
    "sig_candles", "held_symbol", "held_side", "held_entry",
    "held_candles"} or None (either side unselectable → signal skipped,
    counted). ONE position at a time across BOTH sides; a new signal is
    taken only when signal.ts >= last position's exit."""
    positions = []
    diag = {"signals_taken": 0, "signals_skipped_busy": 0,
            "signals_skipped_side": 0, "signals_skipped_select": 0,
            "signals_skipped_cap": 0, "signals_skipped_risk": 0,
            "ambiguous": 0}
    busy_until = -1
    for sig in signals:
        # ── PST_RISK_LIMITS ── hard entry gate (V3 parity)
        if risk is not None and risk.get("enabled") and \
                (risk["day_blocked"] or risk["month_blocked"]):
            diag["signals_skipped_risk"] += 1
            continue
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
        sel = select_pair(sig["side"], sig["ts"])
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position_hedge(
            legs, sig["side"], sig["ts"],
            float(sel["held_entry"]), float(sel["sig_entry"]),
            float(sig["spot"]),
            sel["held_candles"], sel["sig_candles"], spot_1m, eod_ts,
            risk=risk)
        for t in pos["trades"]:
            t["tradingsymbol"] = sel["held_symbol"]
            t["held_side"] = sel["held_side"]
            t["sig_symbol"] = sel["sig_symbol"]
            t["signal_levels"] = ",".join(sig.get("levels_crossed") or [])
        positions.append(pos)
        diag["signals_taken"] += 1
        diag["ambiguous"] += pos["flags"]["ambiguous"]
        busy_until = pos["last_exit_ts"] + 60   # flat from the NEXT minute
    trades = [t for p in positions for t in p["trades"]]
    return {"trades": trades, "diag": diag}