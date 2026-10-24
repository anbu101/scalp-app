# backend/app/engine/lvb/lvb_core.py
#
# ── LVB_V1_20261023 ── "Tripwire" level-break alerts. PURE decision logic:
# no broker, no DB, no clock — every function here is deterministic and is
# exercised directly by test_lvb.py.
#
# The rule (Anbu, 2026-10-23):
#   * A condition = instrument + Upper and/or Lower spot level, an option
#     premium band (default ₹150–200), SL as % of the option's entry premium,
#     TP in SPOT points measured FROM THE LIMIT, an EOD time and lots.
#   * A 5-minute spot candle that CLOSES above Upper buys a CE; one that
#     closes below Lower buys a PE — but only as a FRESH cross: the previous
#     5m close must have been on the inside of that level (for the day's
#     first candle, its OPEN is the reference, so a gap open beyond the
#     level never triggers). An alert created while spot is already beyond
#     its level therefore waits for spot to come back inside and cross again.
#   * The option is the weekly contract on that side with the HIGHEST
#     premium inside the band (= nearest ATM inside the band).
#   * Exits: option LTP <= entry × (1 − SL%) → SL; spot >= Upper + TP (CE) /
#     spot <= Lower − TP (PE) → TP; condition's EOD time → EOD. SL is checked
#     before TP on every poll. Manual square-off any time.
#   * One trade per condition: only a FILLED entry consumes it (TRIGGERED).
#     A breakout that cannot be traded (no strike in the band, decision too
#     late, order failure, breakout candle already past the target) leaves
#     it ARMED with a note. Conditions are valid for their day only.

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

FENCE = "LVB_V1_20261023"
STRATEGY_ID = "LVB_V1"

IST = 5 * 3600 + 30 * 60
SESSION_OPEN_MIN = 9 * 60 + 15
BAR_S = 300
EOD_MIN_ALLOWED = 9 * 60 + 20
EOD_MAX_ALLOWED = 15 * 60 + 20        # below every generic 15:25 sweep
LOTS_MAX = 20
LIMIT_MAX_DIST = 0.10                 # a limit > 10 % from spot is a typo, not a level
TICK = 0.05

INSTRUMENTS: Dict[str, dict] = {
    "NIFTY": {"exchange": "NFO", "segment": "NFO-OPT", "name": "NIFTY",
              "index_key": "NSE:NIFTY 50", "index_token": 256265, "step": 50,
              "tp_default": 50.0},
    "SENSEX": {"exchange": "BFO", "segment": "BFO-OPT", "name": "SENSEX",
               "index_key": "BSE:SENSEX", "index_token": 265, "step": 100,
               "tp_default": 150.0},
}

DEFAULTS = {"prem_min": 150.0, "prem_max": 200.0, "sl_pct": 30.0, "eod": "15:15", "lots": 1}

_HM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ── time helpers (epoch seconds, IST) ────────────────────────────────────────
def minute_of(ts: int) -> int:
    return ((int(ts) + IST) % 86400) // 60


def day_start_epoch_of(ts: int) -> int:
    t = int(ts) + IST
    return t - t % 86400 - IST


def hm_to_min(s) -> Optional[int]:
    m = _HM.match(str(s or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def min_to_hm(m: Optional[int]) -> Optional[str]:
    return None if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def round_tick(px: float) -> float:
    return round(round(float(px) / TICK) * TICK, 2)


# ── validation ───────────────────────────────────────────────────────────────
def _num(v) -> Optional[float]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return float(v)


def validate_condition(raw: dict, spot: Optional[float] = None) -> Tuple[Optional[dict], Optional[str]]:
    """Clean a create/edit payload. Returns (clean, None) or (None, error).
    `spot` (current index LTP, when known) enables the fat-finger guard."""
    try:
        inst = str(raw.get("instrument") or "").strip().upper()
        if inst not in INSTRUMENTS:
            return None, "instrument must be NIFTY or SENSEX"
        spec = INSTRUMENTS[inst]
        upper, lower = _num(raw.get("upper")), _num(raw.get("lower"))
        if upper is None and lower is None:
            return None, "give an Upper limit, a Lower limit, or both"
        for nm, v in (("Upper", upper), ("Lower", lower)):
            if v is not None and v <= 0:
                return None, f"{nm} limit must be positive"
        if upper is not None and lower is not None and upper <= lower:
            return None, "Upper limit must be above the Lower limit"
        if spot and spot > 0:
            for nm, v in (("Upper", upper), ("Lower", lower)):
                if v is not None and abs(v - spot) / spot > LIMIT_MAX_DIST:
                    return None, (f"{nm} limit {v:g} is more than {LIMIT_MAX_DIST:.0%} away from "
                                  f"{inst} {spot:,.2f} — typo?")
        pmin = _num(raw.get("prem_min"))
        pmax = _num(raw.get("prem_max"))
        pmin = DEFAULTS["prem_min"] if pmin is None else pmin
        pmax = DEFAULTS["prem_max"] if pmax is None else pmax
        if not (0 < pmin < pmax <= 5000):
            return None, "premium range needs 0 < Min < Max"
        sl = _num(raw.get("sl_pct"))
        sl = DEFAULTS["sl_pct"] if sl is None else sl
        if not (0 < sl <= 95):
            return None, "SL % must be between 0 and 95"
        tp = _num(raw.get("tp_pts"))
        tp = spec["tp_default"] if tp is None else tp
        if tp < 0:
            return None, "TP points cannot be negative (0 = no target)"
        eod_s = str(raw.get("eod") or DEFAULTS["eod"]).strip()
        eod = hm_to_min(eod_s)
        if eod is None or not (EOD_MIN_ALLOWED <= eod <= EOD_MAX_ALLOWED):
            return None, f"EOD time must be HH:MM between {min_to_hm(EOD_MIN_ALLOWED)} and {min_to_hm(EOD_MAX_ALLOWED)}"
        lots_raw = raw.get("lots")
        lots = int(float(lots_raw)) if lots_raw not in (None, "") else DEFAULTS["lots"]
        if not (1 <= lots <= LOTS_MAX):
            return None, f"lots must be 1–{LOTS_MAX}"
    except (TypeError, ValueError) as e:
        return None, f"bad number: {e}"
    return {"instrument": inst, "upper": upper, "lower": lower, "prem_min": pmin, "prem_max": pmax,
            "sl_pct": sl, "tp_pts": tp, "eod": min_to_hm(eod), "lots": lots}, None


# ── signal ───────────────────────────────────────────────────────────────────
def breakout(prev_close: Optional[float], close: float, upper: Optional[float],
             lower: Optional[float]) -> Optional[str]:
    """'CE' / 'PE' / None for one closed 5m bar. Fresh cross only: the
    reference (previous close, or the day's first bar's open) must be on the
    inside of the level. Strict > / < on the close; <= / >= on the reference."""
    if prev_close is None:
        return None
    if upper is not None and close > upper and prev_close <= upper:
        return "CE"
    if lower is not None and close < lower and prev_close >= lower:
        return "PE"
    return None


def reference_for(bars: List[dict], i: int) -> Optional[float]:
    """Reference price for bar i of TODAY's list: previous close, or the
    first bar's own open."""
    if i <= 0:
        return float(bars[0]["open"]) if bars else None
    return float(bars[i - 1]["close"])


def bar_close_ts(bar: dict) -> int:
    return int(bar["ts"]) + BAR_S


def condition_sees_bar(cond: dict, bar: dict) -> bool:
    """A condition judges only bars that CLOSE after it was created, and
    before its own EOD minute."""
    cts = bar_close_ts(bar)
    if cts <= int(cond.get("created_at") or 0):
        return False
    eod = hm_to_min(cond.get("eod"))
    return eod is None or minute_of(cts) < eod


def target_spot(side: str, upper: Optional[float], lower: Optional[float], tp_pts: float) -> Optional[float]:
    if not tp_pts or tp_pts <= 0:
        return None
    if side == "CE":
        return None if upper is None else round(float(upper) + float(tp_pts), 2)
    return None if lower is None else round(float(lower) - float(tp_pts), 2)


def target_already_past(side: str, spot_close: float, tp_spot: Optional[float]) -> bool:
    if tp_spot is None:
        return False
    return spot_close >= tp_spot if side == "CE" else spot_close <= tp_spot


def sl_price(entry_px: float, sl_pct: float) -> float:
    return max(TICK, round_tick(float(entry_px) * (1.0 - float(sl_pct) / 100.0)))


def exit_reason(side: str, opt_ltp: Optional[float], spot: Optional[float],
                sl: Optional[float], tp_spot: Optional[float]) -> Optional[str]:
    """SL before TP (pessimistic). Missing prices never fire an exit."""
    if opt_ltp is not None and opt_ltp > 0 and sl is not None and opt_ltp <= sl:
        return "SL"
    if spot is not None and spot > 0 and tp_spot is not None:
        if (side == "CE" and spot >= tp_spot) or (side == "PE" and spot <= tp_spot):
            return "TP"
    return None


# ── option selection ─────────────────────────────────────────────────────────
def chain_rows(rows: Iterable[dict], instrument: str, side: str, spot: float, today,
               n_strikes: int = 20) -> Tuple[Optional[object], List[dict]]:
    """Nearest listed expiry (>= today) contracts of one side within
    ±n_strikes steps of spot. rows = instrument-dump dicts (tradingsymbol,
    instrument_token, name, exchange, segment, instrument_type, strike,
    expiry (date), lot_size). Returns (expiry, [contract dicts])."""
    spec = INSTRUMENTS[instrument]
    band = spec["step"] * n_strikes
    mine = []
    for r in rows:
        if str(r.get("exchange") or "") != spec["exchange"]:
            continue
        if str(r.get("segment") or "") != spec["segment"]:
            continue
        if str(r.get("name") or "").upper() != spec["name"]:
            continue
        if str(r.get("instrument_type") or "") != side:
            continue
        exp = r.get("expiry")
        if exp is None or (hasattr(exp, "__lt__") and exp < today):
            continue
        try:
            k = float(r.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if k <= 0 or abs(k - spot) > band:
            continue
        mine.append(r)
    if not mine:
        return None, []
    expiry = min(r["expiry"] for r in mine)
    out = [{"sym": str(r["tradingsymbol"]), "token": int(r.get("instrument_token") or 0),
            "strike": float(r["strike"]), "expiry": expiry, "lot_size": int(float(r.get("lot_size") or 0)),
            "exchange": spec["exchange"]}
           for r in mine if r["expiry"] == expiry]
    out.sort(key=lambda c: c["strike"])
    return expiry, out


def pick_option(cands: List[dict], ltps: Dict[str, float], pmin: float, pmax: float,
                spot: float) -> Optional[dict]:
    """Highest premium inside [pmin, pmax]; tie → nearest ATM."""
    best = None
    for c in cands:
        px = float(ltps.get(c["sym"]) or 0)
        if px <= 0 or px < pmin or px > pmax:
            continue
        key = (px, -abs(c["strike"] - spot))
        if best is None or key > best[0]:
            best = (key, dict(c, ltp=px))
    return None if best is None else best[1]


def describe(cond: dict) -> str:
    """One-line human summary (alerts, Telegram, audit)."""
    parts = []
    tp = float(cond.get("tp_pts") or 0)
    if cond.get("upper") is not None:
        t = target_spot("CE", cond["upper"], None, tp)
        parts.append(f"CE > {cond['upper']:g}" + (f" (TP {t:g})" if t else ""))
    if cond.get("lower") is not None:
        t = target_spot("PE", None, cond["lower"], tp)
        parts.append(f"PE < {cond['lower']:g}" + (f" (TP {t:g})" if t else ""))
    return (f"{cond.get('instrument')} " + " | ".join(parts)
            + f" · ₹{cond.get('prem_min'):g}–{cond.get('prem_max'):g} · SL {cond.get('sl_pct'):g}%"
            + f" · EOD {cond.get('eod')} · {cond.get('lots')} lot(s)")
