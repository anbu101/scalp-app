# backend/app/backtest/ic/backtest_ic_runner.py
#
# ── IC RUNNER ── Iron Condor over the 1m corpus. Time-entry premium-defined
# condor: SELL CE+PE nearest-below a premium cap, BUY far wings nearest-below
# a small cap, per-leg SL/TP (% or points), Move-To-Cost cross-leg rule.
# One entry per day, no re-entry.
#
# Serves BOTH strategies off one code path:
#   IC_V1 — EOD square-off every day (unchanged, legacy default)
#   IC_V2 — SL-triggered ADJUSTMENT legs + overnight CARRY (see below)
#
# All decision logic lives in ic_v1_engine (pure, unit-tested); this file is
# the plumbing: corpus access via CandleSource, expected-expiry coverage gate
# (same fail-closed policy as backtest_selector), strike selection, charges,
# ICTrade rows shaped for persist_run's non-hedge branch, DIAG funnel,
# progress/cancel. Returns the standard runner payload; the CALLER persists
# (backtest_routes / queue_worker), matching HA_SELL/WICK.
#
# Selection policy (locked 2026-07-05):
#   * SHORT legs fail CLOSED: no strike with entry premium ≤ cap → day
#     SKIPPED (diag days_no_short_strike). Selling a richer premium is a
#     different trade.
#   * WING legs fail OPEN: no strike ≤ cap → cheapest available strike
#     (diag wing_fallback_days); no strikes at all → wing absent that day
#     (diag wing_absent_days). The ATM±10 corpus often lacks ₹4 wings.
#   * Expected weekly expiry must be in the corpus (expiry_calendar), else
#     the day is skipped — never a farther expiry (mirrors the selector).
#
# ══════════════════════════════════════════════════════════════════════
# ── IC_V2 BEGIN ── (2026-07-20; D1–D8 + C1/C2 locked with the user)
#
# IC_V2 = IC_V1 + two switches, both config-driven, both defaulted OFF so
# an IC_V1 run takes byte-identical code paths (engine's simulate_day
# wrapper + `positional=False` here → the day loop below is the original).
#
# 1) ADJUSTMENT LEGS (adjust_on_sl). When a SHORT exits on SL, a BUY leg of
#    the SAME opt_type opens `adjust_delay_s` later (default 60s = the next
#    1m candle, matching Quantman's ReExecute delay and MTC's own boundary).
#      * strike: select_strike(pool, adjust.premium_max) — D1 nearest-below,
#        fail-closed (no strike ≤ cap → adjustment skipped, DIAG counted).
#        Priced off the candle STAMPED at the activation minute, which is
#        also the fill candle: the engine fills at that candle's CLOSE and
#        starts monitoring the NEXT minute, so selection never peeks.
#      * SL/TP/lots/cap are per-short-leg UI config (D2).
#      * MTC_COST does NOT trigger an adjustment (D5 — Quantman's
#        `Already Exited In Loss Is True`; a cost exit is a scratch).
#      * double-SL arms BOTH (D4/C1) → the day becomes two naked longs.
#        diag double_sl_adjust_days isolates it; it is the most theta- and
#        capital-exposed state the strategy can reach.
#      * no candle at the activation minute → DROPPED, never slid to the
#        next session (C2/b), diag adjust_dropped.
#
# 2) OVERNIGHT CARRY (exit_mode=NEXT_OPEN). Open legs are NOT squared off
#    at exit_time. They close at the OPEN of the candle stamped
#    `next_open_time` on the next session that has data (D6/D3).
#      * expiry day: hard close at `expiry_exit_time` (reason EOD).
#      * last day of the range: hard close (reason EOR) — nothing outlives
#        the simulation.
#      * next session missing that symbol entirely → carry another night
#        (diag carry_gap_days).
#      * exact-ts miss → first candle at/after the target (next_open_fallbacks).
#      * gap fills: a carried leg whose candle OPENS through its level fills
#        AT THE OPEN (engine `gap_ok`), the TMA positional convention. The
#        IC_V1 intraday path keeps the at-level convention untouched.
#      * ENTRY IS BLOCKED WHILE ANY LEG IS OPEN (D7) — no stacking; the
#        condor must be flat before a new day's entry is considered.
#
# Wings under carry: the SYNTHETIC wing is a TWO-PRICE construct (entry +
# one exit) and cannot be monitored across sessions, so synthetic wings are
# only available in EOD mode. In NEXT_OPEN mode wing_mode=synthetic is
# DOWNGRADED to real_fallback and counted (diag wing_synth_disabled_v2) —
# failing open to reality rather than silently modelling a carried leg.
# ── IC_V2 END ──
# ══════════════════════════════════════════════════════════════════════

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

# try/except import: the app path in production; bare module names when the
# pure logic is exercised standalone in tests (no app package on sys.path)
try:
    from app.backtest.ic.ic_v1_engine import (
        norm_leg, norm_adjust, select_strike, entry_close,
        simulate_day, simulate_session, leg_pnl,
    )
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone test harness
    from ic_v1_engine import (  # type: ignore
        norm_leg, norm_adjust, select_strike, entry_close,
        simulate_day, simulate_session, leg_pnl,
    )
    import ic_synth_wing as SW  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65            # NIFTY

# ── CARRY_DATA_GAP ── tripwire bound: a positional condor should always be
# closed by its own weekly expiry, so any leg still carried after this many
# sessions indicates a bug. It is force-flattened and DIAG-counted rather
# than left to block entry for the rest of the range.
MAX_CARRY_SESSIONS = 10

# canonical 4-leg template (shorts are MTC partners of each other)
DEFAULT_LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 24, "premium_max": 85,
     "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": True, "mtc_partner": "L2"},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 24, "premium_max": 85,
     "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": True, "mtc_partner": "L1"},
    {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 24, "premium_max": 4},
    {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 24, "premium_max": 4},
]

# ── IC_V2 ── per-short-leg adjustment defaults (Quantman Leg6/Leg7).
# Every value is UI-configurable; these are only the shipped defaults.
DEFAULT_ADJUST = {
    "L1": {"enabled": True, "lots": 24, "premium_max": 85,
           "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
    "L2": {"enabled": True, "lots": 24, "premium_max": 85,
           "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
}


# ── house rule: session times are MINUTES, never string-compared ──
def _hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _ist_day(ep: int) -> date:
    return (datetime(1970, 1, 1) + timedelta(seconds=ep + IST)).date()


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


@dataclass
class ICTrade:
    """One leg of one day's condor — attribute surface matches persist_run's
    non-hedge INSERT. NOTE the reader's contract: persist_run reads t.SYMBOL
    (and writes it into the `tradingsymbol` COLUMN) — both names are kept
    here so any consumer works. Leg identity in persisted rows = direction +
    instrument_type (L1=SELL·CE, L2=SELL·PE, L3=BUY·CE, L4=BUY·PE); MTC shows
    as exit_reason MTC_COST.

    ── IC_V2 ── adjustment legs are emitted with condition "<Lx>·ADJ" and
    direction BUY; carried legs keep their original entry_ts (which may be a
    PRIOR session) and exit with NEXT_OPEN / EOD / EOR."""
    tradingsymbol: str
    symbol: str                   # what persist_run actually reads
    instrument_type: str          # CE | PE
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                # SELL | BUY
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]    # SL | TP | MTC_COST | EOD | NEXT_OPEN | EOR
    qty: int
    condition: str                # leg tag (+ ·MTC / ·ADJ / ·SYN)
    ambiguous_fill: bool = False
    pnl: float = 0.0              # gross
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    # aliases some readers use (HATrade parity)
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[ICTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_ic"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.entry_ts or 0, x.condition)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    return {
        "total_trades": len(closed), "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(sum(nets), 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": sum(1 for t in closed if t.ambiguous_fill),
        "diag_ic": diag,
    }


def _resolve_charges():
    """(short_fn, long_fn) from the charges model; None-safe (charges=0)."""
    try:
        from app.backtest.charges.charges_model import (
            charges_for_short_trade, charges_for_long_trade)
        return charges_for_short_trade, charges_for_long_trade
    except Exception:
        return None, None


# ── WING_SYNTH BEGIN ── two-price synthetic wing (see ic_synth_wing.py header
# for the honesty contract). Called ONLY when no real strike ≤ cap exists and
# wing_mode == "synthetic". Every failure returns (None, reason) → the caller
# counts the reason in DIAG and falls open to the real-cheapest fallback.
#
# SPOT: CandleSource.spot_at is a DOCUMENTED STUB (always None — the corpus
# has no index rows; discovered 2026-07-06 after it silently failed-open 100%
# of days). Spot is therefore inferred by PUT-CALL PARITY from the option
# chain itself: at the strike where |C−P| is smallest (the ATM straddle),
# S = C − P + K·e^(−rτ). A ₹4 wing's delta is ~0.01–0.02, so parity-level
# spot error is paise on the modeled premium. Falls back to the median strike
# when one side of the chain is missing.
def _last_close_before(src, sym: str, day_start: int, lo_ts: int, hi_ts: int):
    best = None
    for cd in src.candles_1m_for_symbol_day(sym, day_start):
        ts = cd["ts"] if isinstance(cd, dict) else cd.ts
        if lo_ts <= ts < hi_ts:
            best = (ts, float(cd["close"] if isinstance(cd, dict) else cd.close))
    return best


def _parity_spot(pairs: dict, tau: float) -> Optional[float]:
    """pairs: strike → (ce_px, pe_px). ATM straddle strike = argmin |C−P|;
    S = C − P + K·e^(−rτ)."""
    import math as _m
    usable = {k: v for k, v in pairs.items()
              if v[0] and v[1] and v[0] > 0 and v[1] > 0}
    if not usable:
        return None
    k = min(usable, key=lambda kk: abs(usable[kk][0] - usable[kk][1]))
    ce, pe = usable[k]
    return ce - pe + float(k) * _m.exp(-SW.RISK_FREE * tau)


def _synth_wing_day(*, leg: dict, cand: Dict[str, list], meta_by_sym: dict,
                    src, underlying: str, want_expiry: str, expiry_ts: int,
                    entry_ts: int, eod_ts: int, day_start: int,
                    skew_mult: float):
    """Returns (spec, None) on success or (None, fail_reason) — reasons feed
    diag wing_synth_fail_* so a silent fail-open can never happen again."""
    is_call = leg["opt_type"] == "CE"
    pool = cand.get(leg["opt_type"], [])
    if not pool:
        return None, "pool"
    edge_sym, edge_px = min(pool, key=lambda c: (c[1], c[0]))
    edge_strike = (meta_by_sym.get(edge_sym) or {}).get("strike")
    if not edge_strike or edge_px <= 0:
        return None, "edge"

    tau_e = SW.tau_years(entry_ts, expiry_ts)
    # strike → (ce_px, pe_px) from the ENTRY closes we already computed
    by_strike: Dict[float, list] = {}
    for side in ("CE", "PE"):
        for sym, px in cand.get(side, []):
            k = (meta_by_sym.get(sym) or {}).get("strike")
            if not k:
                continue
            slot = by_strike.setdefault(float(k), [None, None])
            slot[0 if side == "CE" else 1] = px
    spot_e = _parity_spot({k: tuple(v) for k, v in by_strike.items()}, tau_e)
    parity_k = None
    if spot_e is not None:
        usable = {k: v for k, v in by_strike.items() if v[0] and v[1]}
        parity_k = min(usable, key=lambda kk: abs(usable[kk][0] - usable[kk][1]))
    else:
        # one-sided chain: median strike ≈ ATM (coarse but bounded — the
        # backfill window is centered on ATM by construction)
        ks = sorted({float((meta_by_sym.get(s) or {}).get("strike") or 0)
                     for s, _ in pool if (meta_by_sym.get(s) or {}).get("strike")})
        if not ks:
            return None, "spot"
        spot_e = ks[len(ks) // 2]

    iv_e = SW.implied_vol(edge_px, is_call, spot_e, float(edge_strike), tau_e)
    if iv_e is None:
        return None, "iv"
    start = float(edge_strike) + (50.0 if is_call else -50.0)  # strictly beyond real data
    sol = SW.solve_wing_strike(is_call, spot_e, tau_e, iv_e,
                               target_premium=leg["premium_max"],
                               start_strike=start, skew_mult=skew_mult)
    if sol is None:
        return None, "solve"
    strike, entry_px = sol

    # ── EOD side: re-anchor spot via parity at the SAME straddle strike's
    # last prints before exit; re-anchor IV to the edge strike's EOD print.
    # Any missing EOD data degrades to the entry anchors (documented).
    exit_ts = eod_ts - 60
    spot_x, iv_x = spot_e, iv_e
    if parity_k is not None:
        ce_sym = next((s for s, _ in cand.get("CE", [])
                       if float((meta_by_sym.get(s) or {}).get("strike") or 0) == parity_k), None)
        pe_sym = next((s for s, _ in cand.get("PE", [])
                       if float((meta_by_sym.get(s) or {}).get("strike") or 0) == parity_k), None)
        if ce_sym and pe_sym:
            ce_last = _last_close_before(src, ce_sym, day_start, entry_ts, eod_ts)
            pe_last = _last_close_before(src, pe_sym, day_start, entry_ts, eod_ts)
            if ce_last and pe_last:
                exit_ts = max(ce_last[0], pe_last[0])
                sx = _parity_spot({parity_k: (ce_last[1], pe_last[1])},
                                  SW.tau_years(exit_ts, expiry_ts))
                if sx:
                    spot_x = sx
    edge_last = _last_close_before(src, edge_sym, day_start, entry_ts, eod_ts)
    if edge_last:
        exit_ts = max(exit_ts, edge_last[0])
        ivx = SW.implied_vol(edge_last[1], is_call, spot_x, float(edge_strike),
                             SW.tau_years(edge_last[0], expiry_ts))
        if ivx:
            iv_x = ivx
    exit_px = SW.price_wing(is_call, spot_x, strike,
                            SW.tau_years(exit_ts, expiry_ts), iv_x,
                            skew_mult=skew_mult)
    return {"symbol": SW.synth_symbol(underlying, want_expiry, strike, is_call),
            "strike": strike, "entry_px": entry_px,
            "exit_ts": exit_ts, "exit_px": exit_px}, None
# ── WING_SYNTH END ──


def run_ic_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "IC_V1" | "IC_V2"
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
            return _run_ic_backtest_impl(
                db_path=db_path, strategy_id=strategy_id, underlying=underlying,
                date_from=date_from, date_to=date_to,
                config_override=config_override,
                progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _run_ic_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb)


def _run_ic_backtest_impl(
    *,
    db_path: str,
    strategy_id: str,
    underlying: str,
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """config keys:
      entry_time  "HH:MM"  (default "09:18" — fills at the close of the
                            candle ENDING here, i.e. the 3rd 1m candle)
      exit_time   "HH:MM"  (default "15:28" — EOD square-off, IC_V1 mode)
      legs        list of up to 4 leg dicts (see DEFAULT_LEGS); lots 0
                  disables a leg; sl/tp value 0 = disabled; sl_mode/tp_mode
                  'pct' | 'pts'; mtc_other_on_sl + mtc_partner wire the
                  cross-leg Move-To-Cost between the two SHORT legs.

    ── IC_V2 keys (ignored by IC_V1 runs) ──
      exit_mode         "EOD" | "NEXT_OPEN"   (default EOD)
      next_open_time    "HH:MM"  (default "09:16" — carried legs close at
                                  the OPEN of this candle next session)
      expiry_exit_time  "HH:MM"  (default "15:28" — expiry-day square-off)
      adjust_on_sl      bool     (default False)
      adjust_delay_s    int      (default 60 — Quantman ReExecute delay)
      adjust            {"L1": {...}, "L2": {...}} per-short-leg adjustment
                        config: enabled / lots / premium_max / sl_val /
                        sl_mode / tp_val / tp_mode
    """
    from app.backtest.data.candle_source import CandleSource
    from app.event_bus.audit_logger import write_audit_log
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day  # re-export fallback

    cfg = config_override or {}
    entry_min = _hm_to_min(cfg.get("entry_time", "09:18"), 9 * 60 + 18)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:28"), 15 * 60 + 28)
    # ── WING_SYNTH ── wing policy when no real strike ≤ cap exists:
    #   real_fallback (default, today's behavior) | synthetic | skip
    wing_mode = str(cfg.get("wing_mode", "real_fallback") or "real_fallback")
    skew_mult = float(cfg.get("skew_mult", 1.0) or 1.0)

    # ── IC_V2 BEGIN ── switches. Defaults keep IC_V1 semantics exactly.
    exit_mode = str(cfg.get("exit_mode", "EOD") or "EOD").upper()
    if exit_mode not in ("EOD", "NEXT_OPEN"):
        exit_mode = "EOD"
    positional = exit_mode == "NEXT_OPEN"
    next_open_min = _hm_to_min(cfg.get("next_open_time", "09:16"), 9 * 60 + 16)
    expiry_exit_min = _hm_to_min(cfg.get("expiry_exit_time", "15:28"),
                                 15 * 60 + 28)
    adjust_on_sl = bool(cfg.get("adjust_on_sl", False))
    adjust_delay_s = int(cfg.get("adjust_delay_s", 60) or 60)
    raw_adjust = cfg.get("adjust") or (DEFAULT_ADJUST if adjust_on_sl else {})
    adjust_cfg = {k: norm_adjust(v) for k, v in raw_adjust.items()}
    # A synthetic wing is a two-price construct; it cannot be monitored
    # across sessions. Fail OPEN to reality rather than model a carried leg.
    wing_synth_disabled_v2 = False
    if positional and wing_mode == "synthetic":
        wing_mode = "real_fallback"
        wing_synth_disabled_v2 = True
    # ── IC_V2 END ──

    raw_legs = cfg.get("legs") or DEFAULT_LEGS
    legs_cfg = [norm_leg(l) for l in raw_legs if int(l.get("lots") or 0) > 0]
    if not any(l["action"] == "SELL" for l in legs_cfg):
        return {"run_id": None, "aborted": True,
                "reason": f"{strategy_id} needs at least one SELL leg with lots > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    charges_short, charges_long = _resolve_charges()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    lo_all = _day_start_epoch(date_from)
    hi_all = _day_start_epoch(date_to) + 86400
    rows = cur.execute(
        """
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d
        FROM backtest_candles_1m
        WHERE underlying = ? AND instrument_type IN ('CE','PE')
          AND ts >= ? AND ts < ?
        ORDER BY d
        """,
        (underlying, lo_all, hi_all),
    ).fetchall()
    sim_days = [date.fromisoformat(r["d"]) for r in rows]
    if not sim_days:
        conn.close()
        try:
            src.close()
        except Exception:
            pass
        return {"run_id": None, "aborted": True,
                "reason": f"no {underlying} option data in range",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    diag = {
        "days_total": len(sim_days), "days_entered": 0,
        "days_uncovered": 0, "days_no_short_strike": 0,
        "days_no_entry_price": 0,
        "wing_fallback_days": 0, "wing_absent_days": 0, "wing_synth_days": 0,
        "double_sl_days": 0, "mtc_activations": 0,
        "ambiguous_fills": 0, "no_exit_data": 0,
        # ── IC_V2 ──
        "exit_mode": exit_mode, "adjust_on_sl": adjust_on_sl,
        "adjust_triggered": 0, "adjust_no_strike": 0, "adjust_dropped": 0,
        "double_sl_adjust_days": 0,
        "carried_nights": 0, "carry_days": 0, "carry_gap_days": 0,
        # ── CARRY_DATA_GAP ──
        "carry_dark_legs": 0, "carry_intrinsic_closes": 0,
        "carry_dark_stale_close": 0, "carry_force_flat": 0,
        "next_open_closes": 0, "next_open_fallbacks": 0,
        "expiry_closes": 0, "eor_closes": 0, "gap_fills": 0,
        "days_blocked_open": 0,
        "wing_synth_disabled_v2": wing_synth_disabled_v2,
    }
    trades: List[ICTrade] = []

    # ── IC_V2 ── cross-day state. carry maps leg id → carried-state dict
    # (engine's carry_out shape). meta_carry preserves strike/expiry/lots
    # for rows emitted on a LATER day than their entry.
    carry: Dict[str, dict] = {}
    last_range_day = sim_days[-1]

    def _emit(lt: dict, meta_by_sym: dict) -> None:
        """One engine trade dict → one ICTrade row (charges + tagging)."""
        qty = int(lt["lots"]) * LOT_SIZE
        gross = leg_pnl(lt, qty)
        charges = 0.0
        fn = charges_short if lt["action"] == "SELL" else charges_long
        if fn is not None:
            try:
                cr = fn(entry_price=lt["entry_price"],
                        exit_price=lt["exit_price"], qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        m = meta_by_sym.get(lt["tradingsymbol"], {})
        # ── IC_V2 ── adjustment rows carry their own strike/expiry from the
        # engine (their symbol may not be in TODAY's meta map at all).
        strike = lt.get("strike") if lt.get("strike") is not None else m.get("strike")
        expiry = lt.get("expiry") or m.get("expiry")
        tag = lt["leg"]
        if lt.get("is_adjust"):
            tag = f"{lt.get('adjust_of') or lt['leg']}·ADJ"
        elif lt["mtc_applied"]:
            tag = f"{lt['leg']}·MTC"
        trades.append(ICTrade(
            tradingsymbol=lt["tradingsymbol"],
            symbol=lt["tradingsymbol"],
            instrument_type=lt["opt_type"],
            strike=strike, expiry=expiry,
            direction=lt["action"],
            entry_ts=lt["entry_ts"], entry_price=round(lt["entry_price"], 2),
            sl=(round(lt["sl_price"], 2) if lt["sl_price"] is not None else None),
            tp=(round(lt["tp_price"], 2) if lt["tp_price"] is not None else None),
            exit_ts=lt["exit_ts"],
            exit_price=(round(lt["exit_price"], 2)
                        if lt["exit_price"] is not None else None),
            exit_reason=lt["exit_reason"], qty=qty,
            condition=tag,
            ambiguous_fill=bool(lt["ambiguous_fill"]),
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=bool(lt["ambiguous_fill"]),
        ))

    def _fold_flags(f: dict) -> None:
        diag["mtc_activations"] += f.get("mtc_activations", 0)
        diag["ambiguous_fills"] += f.get("ambiguous", 0)
        diag["no_exit_data"] += f.get("no_exit_data", 0)
        if f.get("double_sl"):
            diag["double_sl_days"] += 1
        # ── IC_V2 ──
        diag["adjust_triggered"] += f.get("adjust_triggered", 0)
        diag["adjust_no_strike"] += f.get("adjust_no_strike", 0)
        diag["adjust_dropped"] += f.get("adjust_dropped", 0)
        diag["next_open_closes"] += f.get("next_open_closes", 0)
        diag["next_open_fallbacks"] += f.get("next_open_fallbacks", 0)
        diag["gap_fills"] += f.get("gap_fills", 0)
        diag["carried_nights"] += f.get("carried", 0)
        if f.get("double_sl_adjust"):
            diag["double_sl_adjust_days"] += 1

    def _day_candles(sym: str, day_start: int) -> List[dict]:
        return [{"ts": x.ts, "open": x.open, "high": x.high,
                 "low": x.low, "close": x.close}
                for x in src.candles_1m_for_symbol_day(sym, day_start)]

    # ── CARRY_DATA_GAP BEGIN ── helpers for legs that lose their candles.
    def _spot_close_before(bound_ts: int, day_start: int):
        """Last SPOT close strictly before bound_ts on this day. The spot
        corpus is always present (it is one series, never band-limited), so
        it is the reliable anchor when an OPTION's candles have stopped."""
        r = cur.execute(
            "SELECT ts, close FROM backtest_candles_1m WHERE underlying=? "
            "AND instrument_type='SPOT' AND ts>=? AND ts<? ORDER BY ts DESC "
            "LIMIT 1", (underlying, day_start, bound_ts)).fetchone()
        return (int(r[0]), float(r[1])) if r else None

    def _intrinsic_close(st: dict, bound_ts: int, day_start: int):
        """(ts, price) for a data-less leg at its expiry/range bound, marked
        at INTRINSIC off spot and floored at the tick. Returns None when even
        spot is missing. Time value on a far-OTM at expiry-day close is ~nil,
        so intrinsic is the honest model-free mark — and critically it is
        stamped at the ACTUAL bound, not at a stale mid-week candle."""
        sp = _spot_close_before(bound_ts, day_start)
        k = st.get("strike")
        if sp is None or not k:
            return None
        ts_, spot = sp
        side = (st.get("leg") or {}).get("opt_type") or ""
        intr = (spot - float(k)) if side == "CE" else (float(k) - spot)
        return ts_, round(max(0.05, intr), 2)

    def _emit_carried(st: dict, lid: str, exit_ts: int, exit_px: float,
                      reason: str) -> None:
        """Book a carried leg the engine never saw this session (it had no
        candles). Shapes the same dict simulate_session would have returned
        so _emit stays the single row-builder."""
        leg = st.get("leg") or {}
        _emit({"leg": lid, "tradingsymbol": st.get("symbol"),
               "action": leg.get("action", "SELL"),
               "opt_type": leg.get("opt_type", "CE"),
               "lots": leg.get("lots", 0),
               "entry_ts": st["entry_ts"], "entry_price": st["entry_price"],
               "exit_ts": exit_ts, "exit_price": exit_px,
               "exit_reason": reason,
               "sl_price": st.get("sl"), "tp_price": st.get("tp"),
               "mtc_applied": bool(st.get("mtc_applied")),
               "ambiguous_fill": False,
               "is_adjust": st.get("is_adjust"),
               "adjust_of": st.get("adjust_of"),
               "strike": st.get("strike"), "expiry": st.get("expiry")}, {})

    def _force_flat_overdue(cy: dict, d_: date) -> dict:
        """TRIPWIRE, not a feature. A leg that has carried more than
        MAX_CARRY_SESSIONS is closed at its last known mark (EOR) so a single
        stuck leg can never block entry for the rest of the range in silence.
        carry_force_flat > 0 in DIAG means there is a bug upstream — the
        expiry bound should always have closed the basket first."""
        out = {}
        for lid, st in cy.items():
            if int(st.get("carry_sessions") or 0) <= MAX_CARRY_SESSIONS:
                out[lid] = st
                continue
            _emit_carried(st, lid,
                          st.get("last_ts") or st["entry_ts"],
                          st.get("last_close", st["entry_price"]), "EOR")
            diag["carry_force_flat"] += 1
            diag["eor_closes"] += 1
            write_audit_log(
                f"[BACKTEST][{strategy_id}] {d_}: leg {lid} "
                f"({st.get('symbol')}) force-flattened after "
                f"{st.get('carry_sessions')} sessions — investigate, the "
                f"expiry bound should have closed it")
        return out
    # ── CARRY_DATA_GAP END ──

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(sim_days),
                         "date": d.isoformat()})

        day_start = _day_start_epoch(d)
        entry_ts = day_start + entry_min * 60
        eod_ts = day_start + exit_min * 60
        next_open_ts = day_start + next_open_min * 60
        expiry_eod_ts = day_start + expiry_exit_min * 60

        # ── IC_V2 BEGIN ── carry day: advance yesterday's open legs through
        # THIS session before considering any new entry (D7 blocks entry
        # while anything is open, so these are mutually exclusive anyway).
        #
        # ── CARRY_DATA_GAP (2026-07-20) ── a carried leg can lose its candles
        # mid-week: the ATM±10 capture band is centred on ATM, so a strike the
        # market ran away from simply stops being backfilled. That is exactly
        # the WINNING short/wing case, and the naive handling was fatal —
        # `if not any(c_candles.values())` passed as long as ONE leg had data,
        # then simulate_session force-closed or stranded the data-less legs and
        # the basket desynced. carry never emptied, D7 blocked entry for the
        # rest of the range, and a 6-month run silently stopped trading after
        # its first week (observed: entries stop at day 3 of 10; EOR stamped on
        # a non-final day).
        #
        # Policy (locked with the user):
        #   * NON-EXPIRY day, leg has no candles → the leg CARRIES UNTOUCHED.
        #     No data is not an exit. Only legs WITH candles are simulated.
        #   * EXPIRY / range-end day, leg has no candles → close at INTRINSIC
        #     off the spot corpus (TMA's EXPIRY_INTRINSIC convention): on the
        #     contract's own expiry, time value on a far-OTM is ~nil, so
        #     intrinsic is the honest model-free mark. Marking at a stale
        #     mid-week price would mis-price the exit by the whole decay.
        #   * A leg carried more than MAX_CARRY_SESSIONS is force-flattened at
        #     its last mark (reason EOR). This is a TRIPWIRE, not a feature —
        #     if carry_force_flat is ever non-zero there is a bug upstream, and
        #     the counter says so instead of the run going quiet.
        if positional and carry:
            diag["carry_days"] += 1
            c_syms = {lid: st["symbol"] for lid, st in carry.items()}
            c_candles = {lid: _day_candles(sym, day_start)
                         for lid, sym in c_syms.items()}

            # expiry day for a carried leg → hard close at expiry_exit_time.
            # Mixed expiries cannot occur (one condor, one weekly expiry),
            # so the first carried leg's expiry decides the whole basket.
            any_expiry = next((st.get("expiry") for st in carry.values()
                               if st.get("expiry")), None)
            hard_ts, hard_reason = None, "EOD"
            if any_expiry == d.isoformat():
                hard_ts, hard_reason = expiry_eod_ts, "EOD"
            elif d == last_range_day:
                hard_ts, hard_reason = expiry_eod_ts, "EOR"

            # ── CARRY_DATA_GAP ── split the basket by data availability.
            live = {lid: cds for lid, cds in c_candles.items() if cds}
            dark = [lid for lid, cds in c_candles.items() if not cds]

            # basket snapshot: the branches below mutate `carry`, and the
            # dark loop must still resolve every leg id it captured
            basket = dict(carry)

            if not live and hard_ts is None:
                # nothing tradable today and no bound to honour → whole
                # basket carries another night, ages one session
                diag["carry_gap_days"] += 1
                for _st in basket.values():
                    _st["carry_sessions"] = int(_st.get("carry_sessions") or 0) + 1
                carry = _force_flat_overdue(basket, d)
                if carry:
                    diag["days_blocked_open"] += 1
                continue    # nothing tradable today either way — never fall
                            # through to entry on a day the basket was dark

            if live:
                res = simulate_session(
                    [], live, {lid: c_syms[lid] for lid in live},
                    entry_ts, None,
                    exit_mode="NEXT_OPEN",
                    carry_in={lid: basket[lid] for lid in live},
                    adjust_on_sl=False,      # carried legs never re-arm
                    hard_close_ts=hard_ts, hard_close_reason=hard_reason,
                    next_open_ts=(None if hard_ts is not None else next_open_ts),
                    is_carry_day=True)
                _fold_flags(res["flags"])
                for lt in res["trades"]:
                    _emit(lt, {})
                    if lt["exit_reason"] == "EOD":
                        diag["expiry_closes"] += 1
                    elif lt["exit_reason"] == "EOR":
                        diag["eor_closes"] += 1
                survivors = res["carry_out"]
            else:
                survivors = {}

            # ── CARRY_DATA_GAP ── the data-less legs
            for lid in dark:
                st = basket[lid]
                if hard_ts is None:
                    # non-expiry: carry untouched, age one session
                    st["carry_sessions"] = int(st.get("carry_sessions") or 0) + 1
                    survivors[lid] = st
                    diag["carry_dark_legs"] += 1
                    continue
                # expiry / range end: close at intrinsic off the spot corpus
                ip = _intrinsic_close(st, hard_ts, day_start)
                if ip is None:
                    # no spot either → last known mark, still closed (the
                    # bound is absolute: nothing outlives its expiry)
                    ip = (st.get("last_ts") or st["entry_ts"],
                          st.get("last_close", st["entry_price"]))
                    diag["carry_dark_stale_close"] += 1
                else:
                    diag["carry_intrinsic_closes"] += 1
                _emit_carried(st, lid, ip[0], ip[1], hard_reason)
                if hard_reason == "EOD":
                    diag["expiry_closes"] += 1
                else:
                    diag["eor_closes"] += 1

            carry = _force_flat_overdue(survivors, d)

        # ── IC_V2 ── D7: entry is BLOCKED while anything is still open.
        # This must gate BEFORE selection, and it must be checked even on a
        # day where the carry block above did not run (e.g. a carry_gap day
        # that `continue`d), otherwise a second condor would be stacked on
        # top of the first AND `carry = res["carry_out"]` below would
        # silently discard the older legs — a state-corruption bug, not just
        # a sizing one.
        if positional and carry:
            diag["days_blocked_open"] += 1
            continue
        # ── IC_V2 END ──

        universe = src.contracts_active_on_day(underlying, day_start)
        if not universe:
            diag["days_uncovered"] += 1
            continue
        want_expiry = expected_expiry_for_day(d).isoformat()
        week = [c for c in universe if c.get("expiry") == want_expiry]
        if not week:
            diag["days_uncovered"] += 1
            write_audit_log(f"[BACKTEST][{strategy_id}] {d}: expected expiry "
                            f"{want_expiry} not in corpus — day skipped")
            continue

        # entry-candle close per candidate, per side (served from the
        # preload_day cache — cheap)
        cand: Dict[str, List] = {"CE": [], "PE": []}
        meta_by_sym: Dict[str, dict] = {}
        for c in week:
            sym = c["tradingsymbol"]
            meta_by_sym[sym] = c
            cds = src.candles_1m_for_symbol_day(sym, day_start)
            ec = entry_close([{"ts": x.ts, "close": x.close} for x in cds], entry_ts)
            if ec is not None:
                cand[c["instrument_type"]].append((sym, ec[1]))

        # per-leg selection
        expiry_d = date.fromisoformat(want_expiry)
        expiry_ts = _day_start_epoch(expiry_d) + (15 * 3600 + 30 * 60)
        selected: Dict[str, str] = {}
        synth_specs: Dict[str, tuple] = {}   # leg id → (leg, spec) — no SL/TP, so bypasses the engine
        wing_fb = False
        wing_synth = False
        skip_day = None
        day_legs: List[dict] = []
        for leg in legs_cfg:
            pool = cand.get(leg["opt_type"], [])
            if leg["action"] == "SELL":
                pick = select_strike(pool, leg["premium_max"])
                if pick is None:
                    skip_day = "no_short_strike" if pool else "no_entry_price"
                    break
            else:
                # strict pick first: a REAL strike ≤ cap always wins
                pick = select_strike(pool, leg["premium_max"])
                if pick is None:
                    if wing_mode == "skip":
                        diag["wing_absent_days"] += 1
                        continue
                    if wing_mode == "synthetic":
                        spec, why = _synth_wing_day(
                            leg=leg, cand=cand, meta_by_sym=meta_by_sym,
                            src=src, underlying=underlying,
                            want_expiry=want_expiry, expiry_ts=expiry_ts,
                            entry_ts=entry_ts, eod_ts=eod_ts,
                            day_start=day_start, skew_mult=skew_mult)
                        if spec is not None:
                            synth_specs[leg["id"]] = (leg, spec)
                            wing_synth = True
                            continue
                        diag[f"wing_synth_fail_{why}"] = \
                            diag.get(f"wing_synth_fail_{why}", 0) + 1
                        # solver failed → fail OPEN to reality below
                    pick = select_strike(pool, leg["premium_max"],
                                         fallback_cheapest=True)
                    if pick is None:
                        diag["wing_absent_days"] += 1
                        continue    # wing absent today; condor degrades to strangle
                    wing_fb = True
            selected[leg["id"]] = pick[0]
            day_legs.append(leg)
        if skip_day:
            diag[f"days_{skip_day}"] += 1
            continue
        if wing_fb:
            diag["wing_fallback_days"] += 1
        if wing_synth:
            diag["wing_synth_days"] += 1

        candles_by_leg = {lid: _day_candles(selected[lid], day_start)
                          for lid in selected}

        # ── IC_V2 BEGIN ── pre-resolve the adjustment pick for each SHORT.
        # The engine asks for it only if that short actually SLs, but the
        # corpus lives HERE, so the pick is resolved up front and handed
        # over as {symbol, strike, expiry, candles}.
        #
        # STRIKE BASIS (D1): select_strike(pool, adjust.premium_max) —
        # nearest-below, fail-closed — priced off the ENTRY-time ladder
        # (`cand`), the same closes the condor's own legs were chosen from.
        # Using entry-time quotes is deliberate: the cap is a premium BAND,
        # not a moment-specific quote, and pricing off the SL minute would
        # make the pick depend on when the stop happened to trigger. The
        # engine still FILLS at the activation candle's own close, so the
        # fill price is always the real price at the real minute.
        engine_picks: Dict[str, dict] = {}
        if positional and adjust_on_sl:
            for leg in day_legs:
                if leg["action"] != "SELL":
                    continue
                acfg = adjust_cfg.get(leg["id"])
                if not acfg or not acfg.get("enabled"):
                    continue
                pool = cand.get(leg["opt_type"], [])
                pick = select_strike(pool, float(acfg["premium_max"]))
                if pick is None:
                    # no strike ≤ cap on that side — the engine will count
                    # adjust_no_strike when/if this short actually SLs
                    continue
                sym = pick[0]
                m = meta_by_sym.get(sym, {})
                engine_picks[leg["id"]] = {
                    "symbol": sym,
                    "strike": m.get("strike"),
                    "expiry": m.get("expiry"),
                    "candles": _day_candles(sym, day_start),
                }
        # ── IC_V2 END ──

        if not positional:
            # ── IC_V1 PATH ── unchanged: the legacy wrapper, EOD close.
            res = simulate_day(day_legs, candles_by_leg, selected,
                               entry_ts, eod_ts)
            diag["days_entered"] += 1
            _fold_flags(res["flags"])
            for lt in res["trades"]:
                _emit(lt, meta_by_sym)
        else:
            # ── IC_V2 PATH ── carry-capable session. Adjustment picks were
            # resolved above; the engine opens them only on a real SL.
            hard_ts, hard_reason = None, "EOD"
            if want_expiry == d.isoformat():
                hard_ts, hard_reason = expiry_eod_ts, "EOD"
            elif d == last_range_day:
                hard_ts, hard_reason = expiry_eod_ts, "EOR"

            res = simulate_session(
                day_legs, candles_by_leg, selected, entry_ts, None,
                exit_mode="NEXT_OPEN",
                adjust_on_sl=adjust_on_sl, adjust_cfg=adjust_cfg,
                adjust_delay_s=adjust_delay_s, adjust_picks=engine_picks,
                hard_close_ts=hard_ts, hard_close_reason=hard_reason,
                next_open_ts=None,       # entered today → never closes today
                is_carry_day=False)
            diag["days_entered"] += 1
            _fold_flags(res["flags"])
            for lt in res["trades"]:
                _emit(lt, meta_by_sym)
                if lt["exit_reason"] == "EOD" and hard_ts is not None:
                    diag["expiry_closes"] += 1
                elif lt["exit_reason"] == "EOR":
                    diag["eor_closes"] += 1
            carry = res["carry_out"]
            # ── IC_V2 ── the engine is corpus-blind: it cannot know a leg's
            # expiry. Stamp it (and the strike) here so the CARRY block can
            # detect the contract's own expiry day and hard-close instead of
            # carrying a dead option into EOR. Without this every positional
            # run mislabels its expiry-day closes.
            for _lid, _st in carry.items():
                _m = meta_by_sym.get(_st.get("symbol"), {})
                if not _st.get("expiry"):
                    _st["expiry"] = _m.get("expiry") or want_expiry
                if _st.get("strike") is None:
                    _st["strike"] = _m.get("strike")
                # ── CARRY_DATA_GAP ── age counter starts at 0 on the entry
                # day; every carried session increments it (see the carry
                # block's dark/live split and _force_flat_overdue).
                _st.setdefault("carry_sessions", 0)

        # ── WING_SYNTH ── book the modeled wings (no SL/TP → straight
        # entry→EOD trade; never passes through the engine). EOD mode only:
        # positional runs downgrade wing_mode to real_fallback above.
        for lid, (leg, spec) in synth_specs.items():
            qty = int(leg["lots"]) * LOT_SIZE
            gross = (spec["exit_px"] - spec["entry_px"]) * qty   # long leg
            charges = 0.0
            if charges_long is not None:
                try:
                    cr = charges_long(entry_price=spec["entry_px"],
                                      exit_price=spec["exit_px"], qty=qty)
                    charges = float(getattr(cr, "total_charges", 0.0))
                    gross = float(getattr(cr, "gross_pnl", gross))
                except Exception:
                    charges = 0.0
            trades.append(ICTrade(
                tradingsymbol=spec["symbol"], symbol=spec["symbol"],
                instrument_type=leg["opt_type"],
                strike=spec["strike"], expiry=want_expiry,
                direction="BUY",
                entry_ts=entry_ts, entry_price=round(spec["entry_px"], 2),
                sl=None, tp=None,
                exit_ts=spec["exit_ts"], exit_price=round(spec["exit_px"], 2),
                exit_reason="EOD", qty=qty,
                condition=leg["id"] + "·SYN",
                ambiguous_fill=False,
                pnl=round(gross, 2), charges=round(charges, 2),
                net_pnl=round(gross - charges, 2),
                gross=round(gross, 2), net=round(gross - charges, 2),
                ambiguous=False,
            ))

    # ── IC_V2 ── safety net: a position can only still be open here if the
    # final range days had no usable data. Close at the last carried mark so
    # nothing outlives the simulation (EOR), mirroring TMA's carry net.
    for lid, st in list(carry.items()):
        qty = int(st["leg"]["lots"]) * LOT_SIZE
        lt = {"leg": lid, "tradingsymbol": st["symbol"],
              "action": st["leg"]["action"], "opt_type": st["leg"]["opt_type"],
              "lots": st["leg"]["lots"],
              "entry_ts": st["entry_ts"], "entry_price": st["entry_price"],
              "exit_ts": st["last_ts"], "exit_price": st["last_close"],
              "exit_reason": "EOR", "sl_price": st["sl"], "tp_price": st["tp"],
              "mtc_applied": st["mtc_applied"], "ambiguous_fill": False,
              "is_adjust": st.get("is_adjust"), "adjust_of": st.get("adjust_of"),
              "strike": st.get("strike"), "expiry": st.get("expiry")}
        _emit(lt, {})
        diag["eor_closes"] += 1
        del carry[lid]

    conn.close()
    try:
        src.close()
    except Exception:
        pass

    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}][{exit_mode}] {underlying} "
        f"{date_from}→{date_to}: "
        f"{diag['days_entered']}/{diag['days_total']} days entered, "
        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"MTC {diag['mtc_activations']}, doubleSL {diag['double_sl_days']}, "
        f"ADJ {diag['adjust_triggered']} (drop {diag['adjust_dropped']}/"
        f"noStrike {diag['adjust_no_strike']}, dblADJ "
        f"{diag['double_sl_adjust_days']}), "
        f"carry {diag['carried_nights']}n/{diag['carry_days']}d "
        f"(gap {diag['carry_gap_days']}), nextOpen {diag['next_open_closes']} "
        f"(fb {diag['next_open_fallbacks']}), gapFills {diag['gap_fills']}, "
        f"wingFB {diag['wing_fallback_days']}, "
        f"wingSYN {diag['wing_synth_days']}, "
        f"skips: uncovered {diag['days_uncovered']} / "
        f"noShort {diag['days_no_short_strike']} / "
        f"noEntryPx {diag['days_no_entry_price']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}