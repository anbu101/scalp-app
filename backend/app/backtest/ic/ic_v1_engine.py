# backend/app/backtest/strategies/ic_v1_engine.py
#
# ── IC_V1_ENGINE ── Iron Condor v1: time-entry premium-defined condor on
# NIFTY weeklies. Sell CE+PE nearest-below a premium cap (default ₹85) at a
# fixed entry time, buy far wings nearest-below a small cap (default ₹4),
# per-leg SL/TP in percent OR points, Move-To-Cost (MTC) cross-leg rule,
# EOD square-off.
#
# PURE MODULE by design: no app imports, no DB, no I/O. The runner shim
# (ic_v1_runner) feeds it candles + config and persists what comes back —
# so every branch of the cross-leg state machine is unit-tested against
# synthetic candles with hand-computed expectations, per house rule.
#
# LOCKED CONVENTIONS (confirmed 2026-07-05):
#   * Entry price = CLOSE of the candle ENDING at entry time (09:18 entry →
#     close of the 09:17 candle, the day's 3rd 1m candle). Strike selection
#     uses that same close per candidate.
#   * Strike pick = highest premium ≤ cap ("premium lesser than", Quantman
#     semantics). SHORT legs fail CLOSED when nothing ≤ cap exists (selling a
#     richer premium changes the risk profile — skip the day, DIAG counts).
#     WING legs fall back to the cheapest available strike (fallback flagged,
#     DIAG counts) because the ATM±10 corpus often lacks ₹4 wings.
#   * Intrabar SL: candle range touching the trigger fills AT the trigger.
#     SL and TP inside one candle → SL fill + ambiguous_fill flag.
#   * MTC: when a short leg exits on SL, its partner short's SL is re-pinned
#     to the partner's OWN entry price, effective from the NEXT 1m candle —
#     same-candle sequencing on 1m data would be lookahead. TP (if any)
#     stays live after MTC. MTC is one-shot, not trailing.
#   * Both partner shorts breach their ORIGINAL SLs in the same candle →
#     both exit at their own SLs, MTC never activates, day flagged
#     double_sl (per-candle decisions are snapshotted BEFORE exits apply).
#   * EOD: any open leg exits at the close of its last candle strictly
#     before exit_time. Legs 3/4 (wings) have no SL/TP by default and always
#     ride to EOD.

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

PRICE_FLOOR = 0.05


# ──────────────────────────────────────────────────────────────────────
# leg config normalization
# ──────────────────────────────────────────────────────────────────────
def norm_leg(raw: dict) -> dict:
    """Normalize a leg config. sl/tp value 0 or None = disabled.
    mode: 'pct' | 'pts'."""
    return {
        "id": str(raw.get("id")),
        "action": str(raw.get("action", "SELL")).upper(),      # SELL | BUY
        "opt_type": str(raw.get("opt_type", "CE")).upper(),    # CE | PE
        "lots": int(raw.get("lots") or 0),
        "premium_max": float(raw.get("premium_max") or 0),
        "sl_val": float(raw.get("sl_val") or 0),
        "sl_mode": str(raw.get("sl_mode", "pct")),
        "tp_val": float(raw.get("tp_val") or 0),
        "tp_mode": str(raw.get("tp_mode", "pct")),
        "mtc_other_on_sl": bool(raw.get("mtc_other_on_sl")),
        "mtc_partner": raw.get("mtc_partner"),                 # partner leg id
    }


def sl_price(action: str, entry: float, val: float, mode: str) -> Optional[float]:
    if not val or val <= 0:
        return None
    if action == "SELL":   # loss when premium RISES
        return entry * (1 + val / 100.0) if mode == "pct" else entry + val
    return max(PRICE_FLOOR, entry * (1 - val / 100.0) if mode == "pct" else entry - val)


def tp_price(action: str, entry: float, val: float, mode: str) -> Optional[float]:
    if not val or val <= 0:
        return None
    if action == "SELL":   # profit when premium FALLS
        return max(PRICE_FLOOR, entry * (1 - val / 100.0) if mode == "pct" else entry - val)
    return entry * (1 + val / 100.0) if mode == "pct" else entry + val


# ──────────────────────────────────────────────────────────────────────
# strike selection
# ──────────────────────────────────────────────────────────────────────
def select_strike(candidates: List[Tuple[str, float]], premium_max: float,
                  fallback_cheapest: bool = False):
    """candidates: [(tradingsymbol, entry_candle_close)]. Returns
    (symbol, price, fallback_used) or None.
    Pick = HIGHEST premium ≤ cap; deterministic tie-break on symbol."""
    live = [(s, p) for s, p in candidates if p and p > 0]
    eligible = [(s, p) for s, p in live if p <= premium_max]
    if eligible:
        sym, px = sorted(eligible, key=lambda c: (-c[1], c[0]))[0]
        return sym, px, False
    if fallback_cheapest and live:
        sym, px = sorted(live, key=lambda c: (c[1], c[0]))[0]
        return sym, px, True
    return None


def entry_close(candles: List[dict], entry_ts: int,
                max_stale_s: int = 180) -> Optional[Tuple[int, float]]:
    """Close of the candle ENDING at entry time = latest candle with
    ts < entry_ts, within a staleness window. Returns (ts, close) or None."""
    best = None
    for cd in candles:
        if cd["ts"] < entry_ts and cd["ts"] >= entry_ts - max_stale_s:
            if best is None or cd["ts"] > best[0]:
                best = (cd["ts"], float(cd["close"]))
    return best


# ──────────────────────────────────────────────────────────────────────
# day simulation
# ──────────────────────────────────────────────────────────────────────
def simulate_day(legs: List[dict], candles_by_leg: Dict[str, List[dict]],
                 symbols_by_leg: Dict[str, str],
                 entry_ts: int, eod_ts: int) -> dict:
    """Simulate one day of an entered condor.

    legs: norm_leg() dicts (only legs with lots > 0 and a selected symbol).
    candles_by_leg: leg id → ascending 1m candles [{ts, open, high, low,
    close}] for that leg's tradingsymbol.
    entry_ts: epoch of the entry minute (fills at close of the candle
    BEFORE it). eod_ts: epoch of the square-off minute (fills at close of
    the last candle strictly before it).

    Returns {"trades": [...], "flags": {double_sl, mtc_activations,
    ambiguous, no_exit_data}}. Never raises on data gaps — degrades with
    flags (fail-open on analytics, matching the report side)."""
    flags = {"double_sl": False, "mtc_activations": 0, "ambiguous": 0,
             "no_exit_data": 0}

    state: Dict[str, dict] = {}
    for leg in legs:
        lid = leg["id"]
        ec = entry_close(candles_by_leg.get(lid) or [], entry_ts)
        if ec is None:
            continue    # runner pre-validates; belt-and-braces
        _, epx = ec
        state[lid] = {
            "leg": leg, "entry_price": epx,
            "sl": sl_price(leg["action"], epx, leg["sl_val"], leg["sl_mode"]),
            "tp": tp_price(leg["action"], epx, leg["tp_val"], leg["tp_mode"]),
            "mtc_applied": False,
            "open": True, "last_close": epx, "last_ts": entry_ts,
            "exit": None,   # (ts, price, reason, ambiguous)
        }

    # pending MTC: partner leg id → activation ts (next candle after trigger)
    pending_mtc: Dict[str, int] = {}

    all_ts = sorted({cd["ts"] for lid in state for cd in candles_by_leg.get(lid, [])
                     if entry_ts <= cd["ts"] < eod_ts})
    by_leg_ts = {lid: {cd["ts"]: cd for cd in candles_by_leg.get(lid, [])}
                 for lid in state}

    for ts in all_ts:
        # 1) apply due MTC re-pins BEFORE this candle's checks
        for lid, act_ts in list(pending_mtc.items()):
            st = state.get(lid)
            if st and st["open"] and ts >= act_ts and not st["mtc_applied"]:
                st["sl"] = st["entry_price"]          # cost; TP stays live
                st["mtc_applied"] = True
                flags["mtc_activations"] += 1
                del pending_mtc[lid]

        # 2) SNAPSHOT decisions for every open leg at this candle (so a
        #    same-candle double SL is decided on pre-exit state)
        decisions = []
        for lid, st in state.items():
            if not st["open"]:
                continue
            cd = by_leg_ts[lid].get(ts)
            if cd is None:
                continue
            st["last_close"] = float(cd["close"])
            st["last_ts"] = ts
            action = st["leg"]["action"]
            slp, tpp = st["sl"], st["tp"]
            if action == "SELL":
                hit_sl = slp is not None and float(cd["high"]) >= slp
                hit_tp = tpp is not None and float(cd["low"]) <= tpp
            else:
                hit_sl = slp is not None and float(cd["low"]) <= slp
                hit_tp = tpp is not None and float(cd["high"]) >= tpp
            if hit_sl:
                reason = "MTC_COST" if st["mtc_applied"] else "SL"
                decisions.append((lid, slp, reason, hit_tp))
            elif hit_tp:
                decisions.append((lid, tpp, "TP", False))

        # 3) double-SL detection among MTC partner pairs (original SLs only)
        sl_ids = {lid for lid, _p, r, _a in decisions if r == "SL"}
        double_pairs = set()
        for lid in sl_ids:
            partner = state[lid]["leg"].get("mtc_partner")
            if partner in sl_ids:
                double_pairs.add(lid)

        # 4) apply exits, then schedule MTC for survivors
        for lid, px, reason, ambiguous in decisions:
            st = state[lid]
            st["open"] = False
            st["exit"] = (ts, px, reason, ambiguous)
            if ambiguous:
                flags["ambiguous"] += 1
        if double_pairs:
            flags["double_sl"] = True
        for lid, _px, reason, _a in decisions:
            if reason != "SL" or lid in double_pairs:
                continue
            st = state[lid]
            if not st["leg"]["mtc_other_on_sl"]:
                continue
            partner = st["leg"].get("mtc_partner")
            pst = state.get(partner)
            if pst and pst["open"] and not pst["mtc_applied"]:
                pending_mtc[partner] = ts + 60      # NEXT candle, not this one

    # 5) EOD square-off for anything still open
    trades = []
    for lid, st in state.items():
        leg = st["leg"]
        if st["open"]:
            if st["last_ts"] <= entry_ts and st["last_close"] == st["entry_price"]:
                flags["no_exit_data"] += 1
            # EOD_MTC: survivor whose SL was moved to cost and never breached —
            # distinguishes "MTC rode to EOD" from a plain EOD leg in the audit
            # trail and the Exit Reasons split (scratch vs profitable ride).
            eod_reason = "EOD_MTC" if st["mtc_applied"] else "EOD"
            st["exit"] = (st["last_ts"], st["last_close"], eod_reason, False)
            st["open"] = False
        ets, epx, reason, ambiguous = st["exit"]
        trades.append({
            "leg": lid,
            "tradingsymbol": symbols_by_leg.get(lid),
            "action": leg["action"], "opt_type": leg["opt_type"],
            "lots": leg["lots"],
            "entry_ts": entry_ts, "entry_price": st["entry_price"],
            "exit_ts": ets, "exit_price": epx, "exit_reason": reason,
            "sl_price": st["sl"], "tp_price": st["tp"],
            "mtc_applied": st["mtc_applied"],
            "ambiguous_fill": bool(ambiguous),
        })
    trades.sort(key=lambda t: t["leg"])
    return {"trades": trades, "flags": flags}


def leg_pnl(trade: dict, qty: int) -> float:
    """Gross P&L for one leg. SELL profits when premium falls."""
    d = trade["entry_price"] - trade["exit_price"]
    return (d if trade["action"] == "SELL" else -d) * qty