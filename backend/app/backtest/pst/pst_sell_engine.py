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


# ── PST_SELL_ENTRY_FILTERS_20260828 BEGIN ── pivot-level allowlist support.
# Rank order is the VALUE order of Traditional pivots (provably monotonic:
# S3<S2<S1<PP<R1<R2<R3 for any prev-session H≥L). A 3m signal bar can cross
# several levels at once (gap bars); the ECONOMICALLY meaningful one is the
# NEAREST level in the direction of the move — for a CE (up-cross) that is
# the LOWEST crossed level, for a PE (down-cross) the HIGHEST. NOTE: this
# deliberately differs from the export's Condition string, whose level list
# is dict-insertion-ordered (PP,R1,S1,R2,S2,R3,S3 filtered), not
# nearest-first — the divergence affects only rare multi-cross bars where a
# down move sweeps levels above PP (≤3 trades in the 2020-2026 corpus).
PIVOT_RANK = {"S3": 0, "S2": 1, "S1": 2, "PP": 3, "R1": 4, "R2": 5, "R3": 6}


def nearest_crossed_level(side: str, levels_crossed) -> Optional[str]:
    """The first level the move actually met: min-rank for CE (up-cross),
    max-rank for PE (down-cross). None for an empty/unknown list."""
    names = [n for n in (levels_crossed or []) if n in PIVOT_RANK]
    if not names:
        return None
    pick = min if side == "CE" else max
    return pick(names, key=lambda n: PIVOT_RANK[n])
# ── PST_SELL_ENTRY_FILTERS_20260828 END ──


# ──────────────────────────────────────────────────────────────────────
# one entered SHORT position (2 legs, same option symbol) — state machine
# ──────────────────────────────────────────────────────────────────────
def simulate_position_short(legs: List[dict], side: str, entry_ts: int,
                            entry_price: float, spot_entry: float,
                            opt_candles: List[dict], spot_1m: List[dict],
                            eod_ts: int,
                            risk: Optional[dict] = None) -> Dict:
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
        # ── PST_RISK_LIMITS BEGIN ── intrabar clamp (V3 parity), checked
        # BEFORE the normal TP/SL logic. cum(period) = realized net + open
        # SHORT MTM = R + (entry − px)·Q; solve for the px where cum hits
        # ±limit and see whether this candle traded through it. For a SHORT,
        # the LOSS threshold sits ABOVE entry (price rising) and the PROFIT
        # threshold BELOW. Loss checks first (pessimistic); loss+profit in
        # one candle → loss wins + ambiguous. Gap-throughs fill at the open.
        if risk is not None and risk.get("enabled"):
            _open = [s_ for s_ in state.values() if s_["open"]]
            if _open:
                _q = sum(float(s_["leg"]["lots"]) for s_ in _open) * float(risk["lot_size"])
                _loss = []
                if risk["dml"]:
                    _loss.append((entry_price + (risk["dml"] + risk["day_realized"]) / _q, "DAILY_MAX_LOSS"))
                if risk["mml"]:
                    _loss.append((entry_price + (risk["mml"] + risk["month_realized"]) / _q, "MONTHLY_MAX_LOSS"))
                _prof = []
                if risk["dmp"]:
                    _prof.append((entry_price - (risk["dmp"] - risk["day_realized"]) / _q, "DAILY_MAX_PROFIT"))
                if risk["mmp"]:
                    _prof.append((entry_price - (risk["mmp"] - risk["month_realized"]) / _q, "MONTHLY_MAX_PROFIT"))
                _lpx, _lreason = min(_loss, key=lambda x: x[0]) if _loss else (None, None)
                _ppx, _preason = max(_prof, key=lambda x: x[0]) if _prof else (None, None)
                _l_hit = _lpx is not None and float(oc["high"]) >= _lpx
                _p_hit = _ppx is not None and float(oc["low"]) <= _ppx
                _risk_exit = None
                if _l_hit:
                    _px = _lpx if float(oc["open"]) < _lpx else float(oc["open"])
                    _risk_exit = (_px, _lreason, bool(_p_hit))
                elif _p_hit:
                    _px = _ppx if float(oc["open"]) > _ppx else float(oc["open"])
                    _risk_exit = (_px, _preason, False)
                if _risk_exit is not None:
                    _px, _reason, _amb = _risk_exit
                    for s_ in _open:
                        s_["open"] = False
                        s_["exit"] = (ts, _px, _reason, _amb)
                        if _amb:
                            ambiguous += 1
                        _rl_add(risk, risk["pnl_fn"](entry_price, _px, int(s_["leg"]["lots"])))
                    # force the block from the EXIT EVENT itself (charges can
                    # leave realized a hair short of a PROFIT limit)
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
                if risk is not None and risk.get("enabled"):   # ── PST_RISK_LIMITS ──
                    _rl_add(risk, risk["pnl_fn"](entry_price, float(oc["close"]), int(st_["leg"]["lots"])))
            elif hit_tp:
                # profit; fill AT the tp level (mirror of V1's SL-at-trigger)
                st_["open"] = False
                st_["exit"] = (ts, st_["tp"], "TP", False)
                if risk is not None and risk.get("enabled"):   # ── PST_RISK_LIMITS ──
                    _rl_add(risk, risk["pnl_fn"](entry_price, st_["tp"], int(st_["leg"]["lots"])))
        if all(not s["open"] for s in state.values()):
            break

    trades = []
    last_exit = entry_ts
    for lid, st_ in state.items():
        if st_["open"]:
            st_["exit"] = (st_["last_ts"], st_["last_close"], "EOD", False)
            st_["open"] = False
            if risk is not None and risk.get("enabled"):   # ── PST_RISK_LIMITS ──
                _rl_add(risk, risk["pnl_fn"](entry_price, st_["last_close"], int(st_["leg"]["lots"])))
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
                  *, side_mode: str = "BOTH", max_trades_per_day: int = 0,
                  risk: Optional[dict] = None,
                  allowed_levels: Optional[frozenset] = None,
                  confirm_minutes: int = 0) -> Dict:
    """select_option(side, entry_ts) -> {"symbol", "entry_price",
    "candles"} or None. ONE position at a time across BOTH sides; a new
    signal is taken only when signal.ts >= last position's exit."""
    positions = []
    diag = {"signals_taken": 0, "signals_skipped_busy": 0,
            "signals_skipped_side": 0, "signals_skipped_select": 0,
            "signals_skipped_cap": 0, "signals_skipped_risk": 0,
            "signals_skipped_level": 0,   # ── PST_SELL_ENTRY_FILTERS_20260828 ──
            "signals_skipped_confirm": 0,  # ── PST_SELL_CONFIRM_20260828 ──
            "ambiguous": 0}
    busy_until = -1
    # ── PST_SELL_CONFIRM_20260828 ── wait-window scan needs 1m spot by ts and the
    # TIGHTEST active leg tg (that leg dies first; whole entry is atomic).
    _cfm = max(0, int(confirm_minutes or 0))
    _spot_by = {int(c["ts"]): c for c in spot_1m} if _cfm else {}
    _tgs = [float(l["spot_tg_points"]) for l in legs
            if float(l.get("spot_tg_points") or 0) > 0]
    _tg_min = min(_tgs) if _tgs else None
    for sig in signals:
        # ── PST_RISK_LIMITS ── hard entry gate: once a period limit is
        # reached, no further entries that IST day / calendar month.
        if risk is not None and risk.get("enabled") and \
                (risk["day_blocked"] or risk["month_blocked"]):
            diag["signals_skipped_risk"] += 1
            continue
        if side_mode != "BOTH" and sig["side"] != side_mode:
            diag["signals_skipped_side"] += 1
            continue
        # ── PST_SELL_ENTRY_FILTERS_20260828 ── pivot-level allowlist (None/empty = OFF)
        if allowed_levels:
            _lvl = nearest_crossed_level(sig["side"], sig.get("levels_crossed"))
            if _lvl is None or _lvl not in allowed_levels:
                diag["signals_skipped_level"] += 1
                continue
        if sig["ts"] < busy_until:
            diag["signals_skipped_busy"] += 1
            continue
        if max_trades_per_day and diag["signals_taken"] >= max_trades_per_day:
            diag["signals_skipped_cap"] += 1
            continue
        if sig["ts"] >= eod_ts:
            continue
        # ── PST_SELL_CONFIRM_20260828 ── N-minute wait with SL-touch abort. SL
        # levels are SIGNAL-anchored (sig["spot"] ± tg) and the spot path is
        # fill-independent, so the scan sees exactly what the position's
        # first N monitored minutes would have seen. Spot falling back
        # through the crossed level is NOT an abort — that is the TP path.
        _ets = sig["ts"] + _cfm * 60
        if _cfm and _tg_min is not None:
            _is_ce = sig["side"] == "CE"
            _sl_lvl = (float(sig["spot"]) + _tg_min) if _is_ce \
                else (float(sig["spot"]) - _tg_min)
            _touch = None
            for _m in range(1, _cfm + 1):
                _sc = _spot_by.get(sig["ts"] + _m * 60)
                if _sc is None:
                    continue
                if (_is_ce and float(_sc["high"]) >= _sl_lvl) or \
                        ((not _is_ce) and float(_sc["low"]) <= _sl_lvl):
                    _touch = sig["ts"] + _m * 60
                    break
            if _touch is not None:
                diag["signals_skipped_confirm"] += 1
                busy_until = _touch + 60   # we were committed until it died
                continue
        if _ets >= eod_ts:
            diag["signals_skipped_confirm"] += 1
            continue
        sel = select_option(sig["side"], _ets)
        if sel is None:
            diag["signals_skipped_select"] += 1
            continue
        pos = simulate_position_short(legs, sig["side"], _ets,
                                      float(sel["entry_price"]), float(sig["spot"]),
                                      sel["candles"], spot_1m, eod_ts,
                                      risk=risk)
        for t in pos["trades"]:
            t["tradingsymbol"] = sel["symbol"]
            t["signal_levels"] = ",".join(sig.get("levels_crossed") or [])
        positions.append(pos)
        diag["signals_taken"] += 1
        diag["ambiguous"] += pos["flags"]["ambiguous"]
        busy_until = pos["last_exit_ts"] + 60   # flat from the NEXT minute
    trades = [t for p in positions for t in p["trades"]]
    return {"trades": trades, "diag": diag}