# backend/app/backtest/tsg/backtest_tsg_runner.py
#
# ── TSG_V1 RUNNER ── Time StranGle + hedges over the 1m corpus.
# ══════════════════════════════════════════════════════════════════════
# ── TSG_V1 BEGIN ── (2026-07-31; D1–D11 locked with the user)
#
# STRATEGY (deliberately simple — the point is a clean MTM-target baseline):
#   * Every day, at a configurable entry time (default 09:16), enter 4 legs:
#       L1 = SELL CE, highest premium ≤ sell cap   (default ₹85)
#       L2 = SELL PE, highest premium ≤ sell cap   (default ₹85)
#       L3 = BUY  CE, highest premium ≤ hedge cap  (default ₹5)
#       L4 = BUY  PE, highest premium ≤ hedge cap  (default ₹5)
#     Per-leg lots (D8). Entry fills at the close of the candle ENDING at
#     entry time — identical convention to IC ("prev-candle close").
#   * NO per-leg SL, NO per-leg TP, NO MTC, NO adjustments, NO carry.
#   * COMBINED MTM TARGET (D5): on every 1m candle close after entry, the
#     gross MTM of ALL open legs is summed. The first minute it reaches
#     mtm_target (₹, default 5000; 0 = disabled) ALL legs exit at that
#     minute's marks, reason MTM_TARGET.
#   * COMBINED MTM SL: same evaluation, opposite side. mtm_sl is entered as
#     a POSITIVE ₹ number (0 = disabled); the first minute combined MTM
#     ≤ -mtm_sl ALL legs exit at that minute's marks, reason MTM_SL. One
#     number can't satisfy both bounds, so target/SL can never collide on
#     the same candle. No intra-candle touch detection for either — candle
#     closes only, consistent throughout.
#   * IV SL (per-leg, IV1–IV8): iv_sl_pct (percent, 0 = off) monitors the
#     SELL legs' implied vol per 1m close (mark + parity spot + tau via
#     SW.implied_vol). A short at/above the level exits with its same-side
#     hedge (IV_SL / IV_SL_HEDGE); the FIRST IV exit disarms IV checks for
#     the day (one-shot), survivors keep running under the day-MTM TP/SL
#     (day MTM = realized + unrealized) until EOD. Precedence per minute:
#     MTM SL → MTM target → IV. Synthetic shorts hold entry IV flat, so in
#     practice only real shorts can trigger.
#   * IV9 (LOSING-SIDE GATE): the IV SL fires ONLY on a short currently in
#     loss (mark > entry). Rationale: in a directional move the whole
#     strike surface's vol spikes; without this gate the exit lands on the
#     WINNING far-OTM short — the only side whose IV is numerically
#     solvable — while the losing deep-ITM short (intrinsic-dominated,
#     solver → None) sails on. Observed live on a crash day; the gate
#     encodes the actual intent: cut the leg that is blowing up.
#   * IV10 (STRIKE VOL VIA OTM SIDE): each short's monitored IV is its
#     STRIKE's vol, solved from whichever option at that strike is OTM vs
#     parity spot (parity makes CE/PE IV equal at a strike; the OTM one is
#     the solvable one), falling back to the short's own type. This keeps
#     the losing ITM short's vol measurable exactly when it matters.
#   * IV11 (RELATIVE MODE): iv_sl_delta_pts > 0 switches the trigger from
#     an absolute level to vol EXPANSION: per-leg threshold = that leg's
#     OWN entry IV + delta points. Motivated by 6y of data where the
#     absolute 25% level fired within 5 minutes of entry on 66% of its
#     exits — in a high-vol regime the level is already breached at the
#     bell, making it a regime filter, not a circuit breaker. Precedence
#     over iv_sl_pct when both set. A short whose entry IV can't be
#     solved is unmonitored that day (diag iv_entry_solve_fail).
#   * TRAILING DAY-MTM LOCK (TL1-TL6): mtm_trail_arm (₹, 0=off) +
#     mtm_trail_giveback (₹). The running day-MTM peak (realized +
#     unrealized — composes with partial IV exits) ARMS the trail once it
#     reaches the arm level; thereafter the first 1m close where day MTM
#     <= peak - giveback exits ALL open legs (MTM_TRAIL). Motivated by 6y
#     of data: the ₹1L hard target fired twice ever, while EOD days
#     dominate — good mornings decayed into mediocre closes. The trail
#     banks the middle of the distribution without capping the right
#     tail. Per-minute precedence: MTM SL → target → TRAIL → IV.
#   * IV12 (2026-08-03, backtest-only experiment): iv_keep_hedge=true
#     changes the IV exit from PAIR (short + its hedge, IV3) to SHORT-ONLY:
#     the crossing short closes as IV_SL, its wing STAYS OPEN and exits
#     later via MTM_SL / MTM_TARGET / TRAIL / EOD like any survivor. On a
#     genuine vol event the kept wing is long convexity in the blowup's
#     direction. One-shot latch (IV4) and losing-gate (IV9) unchanged.
#   * IV13 (2026-08-03): min_entry_iv (decimal, 0 = off) — ENTRY-IV FLOOR.
#     After the IV11 anchors are solved, if the MEAN of the shorts' entry
#     IVs is below the floor the day is SKIPPED (no trades). Motivation:
#     offline decile analysis of the validated run — the sub-0.11 entry-IV
#     decile was the ONLY negative decile (−₹484/day avg): premium-capped
#     strikes sit too close to spot to pay for the obligation. skip<0.10
#     added +4.8% net at unchanged day-DD and passed walk-forward. Uses
#     the SAME solve as IV11, so live parity is automatic when ported.
#     Requires delta mode (iv_sl_delta_pts > 0) for the anchors; both
#     shorts unsolvable → FAIL-OPEN (enter; diag iv_filter_open_days).
#   * Otherwise ALL legs exit at the EOD candle (default 15:25), reason EOD.
#   * Nearest weekly expiry only, expected-expiry fail-closed gate — the
#     exact policy IC uses (D7). Trades every day incl. expiry day.
#
# SYNTH POLICY (D9/D10 — user chose "synthetic like IC" for missing strikes):
#   * Real strike ≤ cap wins always. When none exists:
#       SHORTS → synthesise via the IC synth primitive (_synth_leg_at);
#                if the solver fails, the day is SKIPPED (selling a richer
#                premium is a different trade — same doctrine as IC).
#       WINGS  → synthesise; solver failure fails OPEN to the cheapest real
#                strike; nothing at all → wing absent (strangle) + diag.
#   * A ₹5 wing will be synthetic on MOST non-expiry days: the ATM±10
#     corpus band rarely contains sub-₹5 strikes mid-week. syn_pnl_share
#     in the summary says how much of the curve is model-attributed.
#
# INTRADAY SYNTH MARKS (differs from IC, deliberately — documented):
#   IC only ever marks a synthetic leg at discrete exits, so a fresh IV
#   anchor per mark is affordable. TSG needs a mark EVERY MINUTE for the
#   combined-MTM check; re-anchoring IV per minute would be hundreds of
#   thousands of implied-vol solves per multi-year run. Policy here:
#     * imply the leg's OWN IV once at entry (comes free from
#       _synth_leg_at), HOLD it flat all day;
#     * per minute: parity spot from the in-memory week ladder + tau decay
#       → BS re-price. (This is IC's dark-mark tier-(b) applied intraday.)
#     * the BOOKED exit uses the SAME mark that triggered (or the EOD mark
#       from the same series) so booked P&L at an MTM exit ≈ mtm_target —
#       trigger and book are never allowed to diverge.
#     * no parity spot at a minute (thin ladder) → carry the last mark,
#       diag mtm_no_spot_minutes.
#   ⚠ Same bias warning as IC: held-IV marks are a model. Read
#   syn_pnl_net / syn_pnl_share_pct before believing an equity curve.
#
# MISSING-CANDLE MTM (D11): a real leg with no candle at a minute is
# marked at its last known close (carry-forward), diag mtm_stale_marks.
#
# REUSE, NOT COPY: strike selection, entry fill, synth pricing, charges
# and the persisted row shape are IMPORTED from the IC backtest module
# (ic_v1_engine + backtest_ic_runner + ic_synth_wing). Nothing in the IC
# files is modified — TSG is import-only downstream of them. If an IC
# refactor ever renames those helpers this file fails LOUD at import.
# ── TSG_V1 END ──
# ══════════════════════════════════════════════════════════════════════

from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

# try/except import: app path in production; bare names for standalone tests
try:
    from app.backtest.ic.ic_v1_engine import select_strike, entry_close
    from app.backtest.ic.backtest_ic_runner import (
        ICTrade, IST, LOT_SIZE, _day_start_epoch, _hm_to_min,
        _resolve_charges, _spot_from_ladder, _anchor_iv, _synth_leg_at,
    )
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone test harness
    from ic_v1_engine import select_strike, entry_close          # type: ignore
    from backtest_ic_runner import (                             # type: ignore
        ICTrade, IST, LOT_SIZE, _day_start_epoch, _hm_to_min,
        _resolve_charges, _spot_from_ladder, _anchor_iv, _synth_leg_at,
    )
    import ic_synth_wing as SW                                   # type: ignore

# canonical 4-leg template (no SL/TP/MTC fields — TSG has none by design)
DEFAULT_TSG_LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 1, "premium_max": 85},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 1, "premium_max": 85},
    {"id": "L3", "action": "BUY",  "opt_type": "CE", "lots": 1, "premium_max": 5},
    {"id": "L4", "action": "BUY",  "opt_type": "PE", "lots": 1, "premium_max": 5},
]


def norm_tsg_leg(raw: dict) -> dict:
    return {
        "id": str(raw.get("id")),
        "action": str(raw.get("action", "SELL")).upper(),   # SELL | BUY
        "opt_type": str(raw.get("opt_type", "CE")).upper(),  # CE | PE
        "lots": int(raw.get("lots") or 0),
        "premium_max": float(raw.get("premium_max") or 0),
    }


# ──────────────────────────────────────────────────────────────────────
# PURE decision core — no I/O, unit-testable (house pattern: decision
# logic pure, plumbing in the runner).
# ──────────────────────────────────────────────────────────────────────
def leg_mtm(action: str, entry: float, mark: float, qty: int) -> float:
    """Gross rupee MTM of one leg at a mark. SELL profits when premium
    falls; BUY profits when it rises."""
    d = (entry - mark) if action == "SELL" else (mark - entry)
    return d * qty


def simulate_tsg_day(
    leg_specs: List[dict],
    minutes: List[int],
    marks_by_minute: Dict[int, Dict[str, float]],
    mtm_target: float,
    mtm_sl: float = 0.0,
    iv_sl_pct: float = 0.0,
    iv_by_minute: Optional[Dict[int, Dict[str, float]]] = None,
    hedge_map: Optional[Dict[str, str]] = None,
    iv_thresholds: Optional[Dict[str, float]] = None,
    mtm_trail_arm: float = 0.0,
    mtm_trail_giveback: float = 0.0,
    iv_keep_hedge: bool = False,
    mtm_sl_basis: str = "DAILY",   # ── TSG_MTM_BASIS_20260821 ── "DAILY"|"POSITION" (SL only)
) -> dict:
    """Per-leg exit engine for the basket.

    leg_specs: [{id, action, entry_price, qty}]
    minutes:   ascending mark timestamps; the LAST one is the EOD bound.
    marks_by_minute[m][leg_id] = resolved mark (carry-forward already done).
    iv_by_minute[m][sell_leg_id] = implied vol (decimal) for monitored SELL
        legs at minute m; a missing entry means the solve failed → that leg
        skips the IV check that minute (IV2).
    hedge_map[sell_id] = the BUY leg that exits alongside it (IV3/IV8).

    Semantics (IV1–IV8 locked with the user):
      * per minute, in order: day-MTM SL → day-MTM target → IV check (IV5);
        the MTM exits close EVERYTHING open at that minute's marks.
      * day MTM = realized P&L of already-closed legs + unrealized of open
        legs (IV6) — so after a partial IV exit the MTM TP/SL keeps
        governing the survivors.
      * IV level rule (IV3): a monitored SELL with iv >= iv_sl_pct/100
        exits (IV_SL) together with its hedge (IV_SL_HEDGE). ONE-SHOT
        (IV4): the first IV exit disarms IV monitoring for the day; both
        shorts crossing on the SAME candle exit together on that candle.
      * LOSING-SIDE GATE (IV9): the IV trigger additionally requires the
        short to be in loss at that candle (mark > entry). A winning
        short is never IV-closed regardless of its vol reading.
      * IV12 keep-hedge: iv_keep_hedge=True closes ONLY the crossing
        short on IV_SL; its hedge stays open (exits MTM/TRAIL/EOD with the
        rest). Pair semantics (IV3) when False.
      * TRAILING LOCK (TL1-TL6): peak of day MTM >= mtm_trail_arm arms
        the trail; thereafter day MTM <= peak - mtm_trail_giveback exits
        ALL open legs (MTM_TRAIL). Checked after SL/target, before IV.
        Either knob <= 0 disables. Returns "trail_armed" for diag.
      * survivors run to EOD (reason EOD) unless MTM TP/SL fires first.
      * mtm_target/mtm_sl/iv_sl_pct <= 0 disable their respective checks.

    Returns {"exits": {leg_id: {"ts","reason","price"}},
             "day_exit_reason", "mtm_final", "peak_mtm", "trough_mtm"}.
    day_exit_reason is the reason that closed the LAST open leg(s)."""
    thr = (iv_sl_pct or 0.0) / 100.0
    # ── IV11 ── per-leg thresholds (decimal) override the flat level; legs
    # absent from a provided dict are unmonitored (no anchor, no trigger).
    def _thr(i: str) -> float:
        return (iv_thresholds.get(i, 0.0) if iv_thresholds is not None
                else thr)
    iv_armed = ((thr > 0 or bool(iv_thresholds))
                and iv_by_minute is not None)
    hedge_map = hedge_map or {}
    spec = {ls["id"]: ls for ls in leg_specs}
    open_ids = [ls["id"] for ls in leg_specs]
    exits: Dict[str, dict] = {}
    realized = 0.0
    peak = float("-inf")
    trough = float("inf")
    last_m = minutes[-1]

    def _unreal(marks: Dict[str, float]) -> float:
        return sum(leg_mtm(spec[i]["action"], spec[i]["entry_price"],
                           marks[i], spec[i]["qty"]) for i in open_ids)

    def _close(i: str, m: int, reason: str, marks: Dict[str, float]) -> None:
        nonlocal realized
        realized += leg_mtm(spec[i]["action"], spec[i]["entry_price"],
                            marks[i], spec[i]["qty"])
        exits[i] = {"ts": m, "reason": reason, "price": marks[i]}
        open_ids.remove(i)

    day_reason = "EOD"
    for m in minutes:
        marks = marks_by_minute[m]
        mtm = realized + _unreal(marks)
        peak = max(peak, mtm)
        trough = min(trough, mtm)
        if not open_ids:
            continue          # all closed mid-day; keep peak/trough honest
        # ── TSG_MTM_BASIS_20260821 BEGIN ── SL basis (D2: SL only).
        # DAILY = realized + unrealized (IV6, unchanged default);
        # POSITION = unrealized of OPEN legs only (= mtm - realized) —
        # after a partial IV exit the survivors get a fresh SL runway.
        # mtm_target / trail / peak / trough stay on day MTM by design.
        _sl_mtm = mtm if mtm_sl_basis != "POSITION" else (mtm - realized)
        if mtm_sl > 0 and _sl_mtm <= -mtm_sl:
            # ── TSG_MTM_BASIS_20260821 END ──
            for i in list(open_ids):
                _close(i, m, "MTM_SL", marks)
            day_reason = "MTM_SL"
            break
        if mtm_target > 0 and mtm >= mtm_target:
            for i in list(open_ids):
                _close(i, m, "MTM_TARGET", marks)
            day_reason = "MTM_TARGET"
            break
        if (mtm_trail_arm > 0 and mtm_trail_giveback > 0
                and peak >= mtm_trail_arm
                and mtm <= peak - mtm_trail_giveback):
            for i in list(open_ids):
                _close(i, m, "MTM_TRAIL", marks)
            day_reason = "MTM_TRAIL"
            break
        if iv_armed:
            ivs = iv_by_minute.get(m) or {}
            crossed = [i for i in list(open_ids)
                       if spec[i]["action"] == "SELL"
                       and ivs.get(i) is not None
                       and _thr(i) > 0 and ivs[i] >= _thr(i)
                       and marks[i] > spec[i]["entry_price"]]   # IV9/IV11
            if crossed:
                for i in crossed:
                    if i in open_ids:
                        _close(i, m, "IV_SL", marks)
                    if iv_keep_hedge:
                        continue          # IV12: wing rides on
                    h = hedge_map.get(i)
                    if h and h in open_ids:
                        _close(h, m, "IV_SL_HEDGE", marks)
                iv_armed = False                      # IV4: one-shot
                if not open_ids:
                    day_reason = "IV_SL"
    for i in list(open_ids):                          # EOD square-off
        _close(i, last_m, "EOD", marks_by_minute[last_m])
    mtm_final = realized
    peak = max(peak, mtm_final)
    trough = min(trough, mtm_final)
    return {"exits": exits, "day_exit_reason": day_reason,
            "mtm_final": mtm_final, "peak_mtm": peak, "trough_mtm": trough,
            "trail_armed": (mtm_trail_arm > 0 and peak >= mtm_trail_arm)}


# ──────────────────────────────────────────────────────────────────────
# summary — same key surface as IC's so every downstream reader
# (persist_run summary_json, results panel, run comparison) just works.
# ──────────────────────────────────────────────────────────────────────
def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[ICTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_tsg"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.entry_ts or 0, x.condition)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)

    syn = [t for t in closed if t.synthetic]
    real = [t for t in closed if not t.synthetic]
    diag["syn_legs"] = len(syn)
    diag["real_legs"] = len(real)
    diag["syn_pnl_gross"] = round(sum(t.pnl for t in syn), 2)
    diag["real_pnl_gross"] = round(sum(t.pnl for t in real), 2)
    diag["syn_pnl_net"] = round(sum(t.net_pnl for t in syn), 2)
    diag["real_pnl_net"] = round(sum(t.net_pnl for t in real), 2)
    _tot = sum(abs(t.pnl) for t in closed) or 1.0
    diag["syn_pnl_share_pct"] = round(
        100.0 * sum(abs(t.pnl) for t in syn) / _tot, 2)

    return {
        "total_trades": len(closed), "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(sum(nets), 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": 0,     # TSG has no level fills → never ambiguous
        "syn_pnl_net": diag["syn_pnl_net"],
        "real_pnl_net": diag["real_pnl_net"],
        "syn_pnl_share_pct": diag["syn_pnl_share_pct"],
        "diag_tsg": diag,
    }


# ──────────────────────────────────────────────────────────────────────
# runner
# ──────────────────────────────────────────────────────────────────────
def run_tsg_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "TSG_V1"
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
            return _run_tsg_backtest_impl(
                db_path=db_path, strategy_id=strategy_id,
                underlying=underlying, date_from=date_from, date_to=date_to,
                config_override=config_override,
                progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _run_tsg_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb)


def _run_tsg_backtest_impl(
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
      entry_time       "HH:MM"  (default "09:16") — fills at prev-candle close
      exit_time        "HH:MM"  (default "15:25") — EOD square-off
      mtm_target       float ₹  (default 5000; 0 = disabled)
      mtm_sl           float ₹  POSITIVE (default 0 = disabled); exit ALL
                       legs when combined MTM <= -mtm_sl (reason MTM_SL)
      mtm_sl_basis     "DAILY"|"POSITION" (default DAILY); DAILY = day
                       MTM (realized + unrealized, IV6); POSITION =
                       unrealized of open legs only. SL only
                       (── TSG_MTM_BASIS_20260821 ──)
      iv_sl_pct        float %  (default 0 = disabled); per-minute implied
                       vol of a SELL leg >= this level exits that short +
                       its hedge (IV_SL / IV_SL_HEDGE), one-shot per day
      min_entry_iv     decimal (default 0 = off); IV13 entry-IV floor:
                       mean of shorts' solved ENTRY IVs below this → day
                       skipped (needs iv_sl_delta_pts > 0 for anchors)
      iv_keep_hedge    bool (default false); IV12: IV_SL closes ONLY the
                       crossing short — its hedge wing stays open to
                       MTM/EOD (no IV_SL_HEDGE rows in this mode)
      mtm_trail_arm    float ₹ (default 0 = off); day-MTM peak >= arm
                       activates the trail (TL1-TL6)
      mtm_trail_giveback float ₹; once armed, day MTM <= peak - giveback
                       exits ALL open legs (MTM_TRAIL)
      iv_sl_delta_pts  float vol-points (default 0 = off); RELATIVE IV SL:
                       per-leg trigger = entry IV + delta/100 (IV11).
                       Precedence over iv_sl_pct. IV9/IV10 unchanged.
      parallel_workers int (default 1 = serial). N>1 shards the date range
                       into N contiguous chunks run in separate processes
                       (days are independent in TSG — no carry). Results
                       are IDENTICAL to serial; only wall-clock changes.
                       Requires the freeze_support() guard in main.py in
                       the running bundle. Children are forced serial.
      legs             list of up to 4 leg dicts (see DEFAULT_TSG_LEGS)
      skew_mult        float (default 1.0) — WING synthetic premiums
      short_skew_mult  float (default 1.0) — SHORT synthetic premiums
                       (IC's adjust_skew_mult doctrine: one multiplier tuned
                       for a ₹5 wing is the wrong correction for an ₹85 leg)
    """
    from app.backtest.data.candle_source import CandleSource
    from app.event_bus.audit_logger import write_audit_log
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    cfg = config_override or {}
    entry_min = _hm_to_min(cfg.get("entry_time", "09:16"), 9 * 60 + 16)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
    mtm_target = float(cfg.get("mtm_target", 5000) or 0)
    mtm_sl = abs(float(cfg.get("mtm_sl", 0) or 0))   # sign-tolerant: -2500 ≡ 2500
    # ── TSG_MTM_BASIS_20260821 ── anything not exactly "POSITION"
    # normalizes to "DAILY" (fail-closed to current IV6 semantics).
    mtm_sl_basis = ("POSITION" if str(cfg.get("mtm_sl_basis", "DAILY")
                    or "DAILY").strip().upper() == "POSITION" else "DAILY")
    iv_sl_pct = abs(float(cfg.get("iv_sl_pct", 0) or 0))
    iv_sl_delta_pts = abs(float(cfg.get("iv_sl_delta_pts", 0) or 0))
    mtm_trail_arm = abs(float(cfg.get("mtm_trail_arm", 0) or 0))
    iv_keep_hedge = bool(cfg.get("iv_keep_hedge", False))
    min_entry_iv = abs(float(cfg.get("min_entry_iv", 0) or 0))
    mtm_trail_giveback = abs(float(cfg.get("mtm_trail_giveback", 0) or 0))
    iv_active = iv_sl_delta_pts > 0 or iv_sl_pct > 0
    parallel_workers = int(cfg.get("parallel_workers", 1) or 1)
    skew_mult = float(cfg.get("skew_mult", 1.0) or 1.0)
    short_skew_mult = float(cfg.get("short_skew_mult", 1.0) or 1.0)

    raw_legs = cfg.get("legs") or DEFAULT_TSG_LEGS
    legs_cfg = [norm_tsg_leg(l) for l in raw_legs if int(l.get("lots") or 0) > 0]
    if not any(l["action"] == "SELL" for l in legs_cfg):
        return {"run_id": None, "aborted": True,
                "reason": f"{strategy_id} needs at least one SELL leg with lots > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    if exit_min <= entry_min:
        return {"run_id": None, "aborted": True,
                "reason": f"{strategy_id}: exit_time must be after entry_time",
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

    # ── TSG_PARALLEL BEGIN ── shard the day range across processes.
    # Each worker re-enters THIS impl over a contiguous date slice with
    # workers forced to 1 — so per-day logic is byte-identical to serial by
    # construction. Parent merges trades + integer diag counters and
    # re-summarizes. Cancel is honoured between chunk completions.
    if parallel_workers > 1 and len(sim_days) >= parallel_workers * 2:
        conn.close()
        try:
            src.close()
        except Exception:
            pass
        import math as _math
        import multiprocessing as _mp
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n = min(parallel_workers, 8)
        step = _math.ceil(len(sim_days) / n)
        chunks = [sim_days[i:i + step] for i in range(0, len(sim_days), step)]
        child_cfg = dict(cfg)
        child_cfg["parallel_workers"] = 1
        merged_trades: List[ICTrade] = []
        merged_diag: Dict[str, float] = {}
        days_done = 0
        try:
            with ProcessPoolExecutor(
                    max_workers=n,
                    mp_context=_mp.get_context("spawn")) as pool:
                futs = {pool.submit(
                    _tsg_parallel_worker, db_path, strategy_id, underlying,
                    ch[0].isoformat(), ch[-1].isoformat(), child_cfg): ch
                    for ch in chunks}
                for fut in as_completed(futs):
                    if cancel_cb and cancel_cb():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    out = fut.result()
                    merged_trades.extend(out["trades"])
                    for k, v in out["diag"].items():
                        if isinstance(v, bool) or not isinstance(v, int):
                            continue          # params/floats: parent's own
                        merged_diag[k] = merged_diag.get(k, 0) + v
                    days_done += len(futs[fut])
                    if progress_cb:
                        progress_cb({"day": days_done,
                                     "total_days": len(sim_days),
                                     "date": futs[fut][-1].isoformat()})
        except Exception as exc:
            # spawn unavailable / worker crash → loud, not silent-serial:
            # a silent fallback would mask a missing freeze_support guard.
            return {"run_id": None, "aborted": True,
                    "reason": f"{strategy_id} parallel execution failed: "
                              f"{exc!r} — rerun with parallel_workers=1",
                    "trades": [], "summary": _empty_summary(),
                    "config": cfg, "strategy_id": strategy_id}
        merged_trades.sort(key=lambda t: (t.entry_ts or 0, t.condition))
        base_diag = {
            "days_total": len(sim_days),
            "mtm_target": mtm_target, "mtm_sl": mtm_sl,
            "mtm_sl_basis": mtm_sl_basis,   # ── TSG_MTM_BASIS_20260821 ──
            "iv_sl_pct": iv_sl_pct, "parallel_workers": n,
            "skew_mult": skew_mult, "short_skew_mult": short_skew_mult,
        }
        for k, v in merged_diag.items():
            if k != "days_total":
                base_diag[k] = v
        summary = _summarize(merged_trades, base_diag)
        write_audit_log(
            f"[BACKTEST][{strategy_id}] {underlying} {date_from}→{date_to}: "
            f"PARALLEL x{n}, {base_diag.get('days_entered', 0)}/"
            f"{len(sim_days)} days entered, {len(merged_trades)} leg-trades, "
            f"net {summary['net_pnl']}")
        return {"run_id": str(uuid.uuid4()), "summary": summary,
                "config": cfg, "trades": merged_trades,
                "strategy_id": strategy_id}
    # ── TSG_PARALLEL END ──

    diag = {
        "days_total": len(sim_days), "days_entered": 0,
        "days_uncovered": 0, "days_no_short_strike": 0,
        "days_no_entry_price": 0,
        "wing_fallback_days": 0, "wing_absent_days": 0,
        "mtm_target": mtm_target, "mtm_sl": mtm_sl,
        "mtm_sl_basis": mtm_sl_basis,   # ── TSG_MTM_BASIS_20260821 ──
        "iv_sl_pct": iv_sl_pct, "iv_sl_delta_pts": iv_sl_delta_pts,
        "iv_mode": ("delta" if iv_sl_delta_pts > 0 else
                    "absolute" if iv_sl_pct > 0 else "off"),
        "iv_entry_solve_fail": 0,
        "mtm_trail_arm": mtm_trail_arm,
        "mtm_trail_giveback": mtm_trail_giveback,
        "mtm_trail_exit_days": 0, "trail_armed_days": 0,
        "iv_keep_hedge": iv_keep_hedge, "iv_kept_hedges": 0,
        "min_entry_iv": min_entry_iv,
        "iv_filter_skipped_days": 0, "iv_filter_open_days": 0,
        "mtm_exit_days": 0, "mtm_sl_exit_days": 0, "eod_exit_days": 0,
        "iv_sl_days": 0, "iv_sl_legs": 0, "iv_sl_hedge_legs": 0,
        "iv_solve_fail_minutes": 0,
        "mtm_stale_marks": 0,        # real-leg carry-forward marks (D11)
        "mtm_no_spot_minutes": 0,    # synth mark carried: no parity spot
        "skew_mult": skew_mult, "short_skew_mult": short_skew_mult,
        "syn_short_days": 0, "syn_short_legs": 0, "syn_short_fail": 0,
        "syn_wing_days": 0, "syn_wing_legs": 0, "syn_wing_fail": 0,
        # populated by _summarize
        "syn_legs": 0, "real_legs": 0,
        "syn_pnl_gross": 0.0, "real_pnl_gross": 0.0,
        "syn_pnl_net": 0.0, "real_pnl_net": 0.0, "syn_pnl_share_pct": 0.0,
    }
    trades: List[ICTrade] = []

    def _emit(*, leg: dict, symbol: str, strike, expiry, entry_ts: int,
              entry_price: float, exit_ts: int, exit_price: float,
              exit_reason: str, synthetic: bool, synth_kind: Optional[str]
              ) -> None:
        """One leg → one ICTrade row. Charges direction-aware, exactly the
        IC convention, so persist_run's non-hedge branch reads it as-is."""
        qty = int(leg["lots"]) * LOT_SIZE
        gross = leg_mtm(leg["action"], entry_price, exit_price, qty)
        charges = 0.0
        fn = charges_short if leg["action"] == "SELL" else charges_long
        if fn is not None:
            try:
                cr = fn(entry_price=entry_price, exit_price=exit_price, qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        tag = leg["id"] + ("·SYN" if synthetic else "")
        trades.append(ICTrade(
            tradingsymbol=symbol, symbol=symbol,
            instrument_type=leg["opt_type"],
            strike=strike, expiry=expiry,
            direction=leg["action"],
            entry_ts=entry_ts, entry_price=round(entry_price, 2),
            sl=None, tp=None,                      # TSG: no per-leg levels
            exit_ts=exit_ts, exit_price=round(exit_price, 2),
            exit_reason=exit_reason, qty=qty,
            condition=tag, ambiguous_fill=False,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=False,
            synthetic=synthetic, synth_kind=synth_kind,
        ))

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(sim_days),
                         "date": d.isoformat()})

        day_start = _day_start_epoch(d)
        entry_ts = day_start + entry_min * 60
        eod_ts = day_start + exit_min * 60

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

        # ── in-memory candle cache for the whole week list. preload_day
        # bulk-loads the entire day in ONE query into CandleSource's cache,
        # so the per-symbol calls below are dict lookups, not SQL (~40
        # queries/day → 1). Every ladder below is built from this cache —
        # zero DB hits inside the minute loop.
        try:
            src.preload_day(underlying, day_start)
        except Exception:
            pass          # cache miss path still works, just slower
        candles_by_sym: Dict[str, List[dict]] = {}
        meta_by_sym: Dict[str, dict] = {}
        for c in week:
            sym = c["tradingsymbol"]
            meta_by_sym[sym] = c
            candles_by_sym[sym] = [
                {"ts": x.ts, "close": float(x.close)}
                for x in src.candles_1m_for_symbol_day(sym, day_start)]

        def _ladder_at(ts: int) -> Dict[str, list]:
            """{"CE": [(sym, px)], "PE": [...]} from the cache — latest
            close of a candle ENDING at-or-before ts (candle ts < ts),
            same convention as entry_close."""
            out: Dict[str, list] = {"CE": [], "PE": []}
            for sym, cds in candles_by_sym.items():
                best = None
                for cd in cds:
                    if cd["ts"] < ts:
                        if best is None or cd["ts"] > best[0]:
                            best = (cd["ts"], cd["close"])
                    else:
                        break          # cds are ts-ascending
                if best and best[1] > 0:
                    out[meta_by_sym[sym]["instrument_type"]].append(
                        (sym, best[1]))
            return out

        expiry_d = date.fromisoformat(want_expiry)
        expiry_ts = _day_start_epoch(expiry_d) + (15 * 3600 + 30 * 60)

        # ── entry ladder + selection ──
        cand = _ladder_at(entry_ts)

        selected: Dict[str, dict] = {}   # leg_id -> spec
        skip_day = None
        day_legs: List[dict] = []
        day_syn_short = False
        day_syn_wing = False
        wing_fb = False

        for leg in legs_cfg:
            pool = cand.get(leg["opt_type"], [])
            pick = select_strike(pool, leg["premium_max"])
            if pick is not None:
                sym = pick[0]
                m = meta_by_sym.get(sym, {})
                selected[leg["id"]] = {
                    "symbol": sym, "price": float(pick[1]),
                    "strike": m.get("strike"), "expiry": m.get("expiry"),
                    "synthetic": False, "synth_kind": None,
                }
                day_legs.append(leg)
                continue

            # nothing real ≤ cap → synthesise (D9/D10)
            sk = short_skew_mult if leg["action"] == "SELL" else skew_mult
            spec, why = _synth_leg_at(
                src=src, week=week, meta_by_sym=meta_by_sym,
                day_start=day_start, ts=entry_ts, expiry_ts=expiry_ts,
                opt_type=leg["opt_type"], cap=leg["premium_max"],
                underlying=underlying, want_expiry=want_expiry,
                skew_mult=sk, ladder=cand)
            if spec is not None:
                selected[leg["id"]] = {
                    "symbol": spec["symbol"], "price": float(spec["price"]),
                    "strike": spec["strike"], "expiry": want_expiry,
                    "synthetic": True,
                    "synth_kind": ("short" if leg["action"] == "SELL"
                                   else "wing"),
                    "iv": spec["iv"],
                }
                day_legs.append(leg)
                if leg["action"] == "SELL":
                    diag["syn_short_legs"] += 1
                    day_syn_short = True
                else:
                    diag["syn_wing_legs"] += 1
                    day_syn_wing = True
                continue

            if leg["action"] == "SELL":
                diag["syn_short_fail"] += 1
                diag[f"syn_short_fail_{why}"] = \
                    diag.get(f"syn_short_fail_{why}", 0) + 1
                skip_day = "no_short_strike" if pool else "no_entry_price"
                break

            # wing: solver failed → fail OPEN to cheapest real
            diag["syn_wing_fail"] += 1
            diag[f"syn_wing_fail_{why}"] = \
                diag.get(f"syn_wing_fail_{why}", 0) + 1
            fpick = select_strike(pool, leg["premium_max"],
                                  fallback_cheapest=True)
            if fpick is None:
                diag["wing_absent_days"] += 1
                continue      # wing absent today; degrades to strangle
            sym = fpick[0]
            m = meta_by_sym.get(sym, {})
            selected[leg["id"]] = {
                "symbol": sym, "price": float(fpick[1]),
                "strike": m.get("strike"), "expiry": m.get("expiry"),
                "synthetic": False, "synth_kind": None,
            }
            day_legs.append(leg)
            wing_fb = True

        if skip_day:
            diag[f"days_{skip_day}"] += 1
            continue
        if wing_fb:
            diag["wing_fallback_days"] += 1
        if day_syn_short:
            diag["syn_short_days"] += 1
        if day_syn_wing:
            diag["syn_wing_days"] += 1

        # ── per-minute mark series, entry+60s .. eod_ts inclusive ──
        minutes = list(range(entry_ts + 60, eod_ts + 1, 60))
        if not minutes:
            diag["days_no_entry_price"] += 1
            continue

        # real legs: pointer-walk their own candles (O(minutes+candles));
        # synth legs: parity spot per minute + held entry IV + tau decay.
        marks_by_minute: Dict[int, Dict[str, float]] = {m: {} for m in minutes}

        # precompute parity spot per minute ONCE — shared by synth marks
        # AND by per-minute IV solves for the shorts (IV2).
        any_synth = any(s["synthetic"] for s in selected.values())
        need_spot = any_synth or iv_active
        spot_by_minute: Dict[int, Optional[float]] = {}
        if need_spot:
            # ONE incremental pass: a cursor per symbol advances monotonically
            # across the minute loop (O(minutes·syms + candles) total), vs the
            # old _ladder_at-per-minute which rescanned every symbol's candles
            # from the top each minute (O(minutes·syms·candles) — the dominant
            # cost of a run once synth marks or the IV SL are active).
            _syms = list(candles_by_sym.keys())
            _typ = {s: meta_by_sym[s]["instrument_type"] for s in _syms}
            _cur = {s: 0 for s in _syms}
            _lastpx: Dict[str, float] = {}
            last_spot: Optional[float] = None
            for m in minutes:
                for s in _syms:
                    cds = candles_by_sym[s]
                    i = _cur[s]
                    n = len(cds)
                    while i < n and cds[i]["ts"] < m:
                        _lastpx[s] = cds[i]["close"]
                        i += 1
                    _cur[s] = i
                lad: Dict[str, list] = {"CE": [], "PE": []}
                for s, px in _lastpx.items():
                    if px > 0:
                        lad[_typ[s]].append((s, px))
                sp = _spot_from_ladder(lad, meta_by_sym,
                                       SW.tau_years(m, expiry_ts))
                if sp is None or sp <= 0:
                    diag["mtm_no_spot_minutes"] += 1
                    sp = last_spot
                else:
                    last_spot = sp
                spot_by_minute[m] = sp

        for lid, spec in selected.items():
            leg = next(l for l in day_legs if l["id"] == lid)
            if not spec["synthetic"]:
                cds = candles_by_sym.get(spec["symbol"], [])
                idx = 0
                last_px = spec["price"]
                last_from_candle_ts: Optional[int] = None
                for m in minutes:
                    while idx < len(cds) and cds[idx]["ts"] < m:
                        last_px = cds[idx]["close"]
                        last_from_candle_ts = cds[idx]["ts"]
                        idx += 1
                    if (last_from_candle_ts is None
                            or m - last_from_candle_ts > 60):
                        diag["mtm_stale_marks"] += 1     # D11 carry-forward
                    marks_by_minute[m][lid] = last_px
            else:
                iv = float(spec["iv"])
                is_call = leg["opt_type"] == "CE"
                sk = (short_skew_mult if leg["action"] == "SELL"
                      else skew_mult)
                last_px = spec["price"]
                for m in minutes:
                    sp = spot_by_minute.get(m)
                    if sp is not None:
                        px = SW.price_wing(is_call, sp,
                                           float(spec["strike"]),
                                           SW.tau_years(m, expiry_ts), iv,
                                           skew_mult=sk)
                        if px is not None and px > 0:
                            last_px = px
                    marks_by_minute[m][lid] = last_px

        # ── per-minute IV for monitored shorts (IV2) ──
        # Real shorts: solve from that minute's mark + parity spot + tau.
        # Synthetic shorts: held entry IV by construction (IV7) — included
        # for consistency but it can only "cross" if already >= level.
        iv_by_minute: Optional[Dict[int, Dict[str, float]]] = None
        iv_thresholds: Optional[Dict[str, float]] = None   # ── IV11 ──
        if iv_active:
            iv_by_minute = {m: {} for m in minutes}
            if iv_sl_delta_pts > 0:
                iv_thresholds = {}
            entry_tau = SW.tau_years(entry_ts, expiry_ts)
            entry_spot = _spot_from_ladder(cand, meta_by_sym, entry_tau)
            for l in day_legs:
                if l["action"] != "SELL":
                    continue
                lid = l["id"]
                spec = selected[lid]
                if spec["synthetic"]:
                    for m in minutes:
                        iv_by_minute[m][lid] = float(spec["iv"])
                    if iv_thresholds is not None:
                        iv_thresholds[lid] = (float(spec["iv"])
                                              + iv_sl_delta_pts / 100.0)
                    continue
                is_call = l["opt_type"] == "CE"
                k = float(spec["strike"])
                # ── IV10 ── the strike's OTM sibling carries the solvable
                # vol once this short goes ITM; precompute its mark series
                # (same pointer-walk as real-leg marks, carry-forward).
                opp_sym = None
                for sym2, m2 in meta_by_sym.items():
                    if (float(m2.get("strike") or 0) == k
                            and m2.get("instrument_type") != l["opt_type"]):
                        opp_sym = sym2
                        break
                opp_marks: Dict[int, float] = {}
                if opp_sym and candles_by_sym.get(opp_sym):
                    cds2 = candles_by_sym[opp_sym]
                    j = 0
                    lastp = None
                    for m in minutes:
                        while j < len(cds2) and cds2[j]["ts"] < m:
                            lastp = cds2[j]["close"]
                            j += 1
                        if lastp is not None:
                            opp_marks[m] = lastp
                _entry_px = float(spec["price"])
                # ── IV11 ── per-leg threshold anchored to ENTRY IV, solved
                # with the same OTM-preference as the minute loop below.
                if iv_thresholds is not None:
                    e_iv = None
                    if entry_spot is not None and entry_spot > 0:
                        e_own = (_entry_px, is_call)
                        e_opp = None
                        for s2, px2 in cand.get(
                                "PE" if is_call else "CE", []):
                            if float(meta_by_sym.get(s2, {}).get("strike")
                                     or 0) == k:
                                e_opp = (px2, not is_call)
                                break
                        e_own_otm = ((k > entry_spot) if is_call
                                     else (k < entry_spot))
                        for cand2 in ([e_own, e_opp] if e_own_otm or
                                      e_opp is None else [e_opp, e_own]):
                            if cand2 is None:
                                continue
                            e_iv = SW.implied_vol(cand2[0], cand2[1],
                                                  entry_spot, k, entry_tau)
                            if e_iv is not None:
                                break
                    if e_iv is None:
                        diag["iv_entry_solve_fail"] += 1
                        continue      # leg unmonitored today (no anchor)
                    iv_thresholds[lid] = e_iv + iv_sl_delta_pts / 100.0
                for m in minutes:
                    if marks_by_minute[m][lid] <= _entry_px:
                        continue      # IV9 would reject anyway — skip solve
                    sp = spot_by_minute.get(m)
                    if sp is None:
                        diag["iv_solve_fail_minutes"] += 1
                        continue
                    tau = SW.tau_years(m, expiry_ts)
                    own = (marks_by_minute[m][lid], is_call)
                    opp = ((opp_marks[m], not is_call)
                           if m in opp_marks else None)
                    own_otm = (k > sp) if is_call else (k < sp)
                    order = [own, opp] if own_otm or opp is None else [opp, own]
                    iv = None
                    for _c in order:      # NB: never rebind `cand` (entry ladder)
                        if _c is None:
                            continue
                        iv = SW.implied_vol(_c[0], _c[1], sp, k, tau)
                        if iv is not None:
                            break
                    if iv is None:
                        diag["iv_solve_fail_minutes"] += 1
                    else:
                        iv_by_minute[m][lid] = iv

        # sell -> same-opt_type hedge pairing (IV3); absent wing → short
        # exits alone (IV8, hedge_map simply has no entry).
        # ── IV13 ── entry-IV floor: mean of shorts' entry IVs (raw
        # anchor = stored threshold − Δ; identical for real and synthetic
        # shorts) below the floor → dead low-vol day, skip before booking
        # anything. No anchors solvable → fail-open with a diag mark.
        if min_entry_iv > 0:
            _eivs = [v - iv_sl_delta_pts / 100.0
                     for v in (iv_thresholds or {}).values()]
            if _eivs:
                if (sum(_eivs) / len(_eivs)) < min_entry_iv:
                    diag["iv_filter_skipped_days"] += 1
                    continue
            else:
                diag["iv_filter_open_days"] += 1

        hedge_map = {}
        for l in day_legs:
            if l["action"] != "SELL":
                continue
            for h in day_legs:
                if h["action"] == "BUY" and h["opt_type"] == l["opt_type"]:
                    hedge_map[l["id"]] = h["id"]
                    break

        # ── pure basket simulation (per-leg exits) ──
        leg_specs = [{"id": l["id"], "action": l["action"],
                      "entry_price": selected[l["id"]]["price"],
                      "qty": int(l["lots"]) * LOT_SIZE}
                     for l in day_legs]
        res = simulate_tsg_day(leg_specs, minutes, marks_by_minute,
                               mtm_target, mtm_sl,
                               iv_sl_pct=iv_sl_pct,
                               iv_by_minute=iv_by_minute,
                               hedge_map=hedge_map,
                               iv_thresholds=iv_thresholds,
                               mtm_trail_arm=mtm_trail_arm,
                               mtm_trail_giveback=mtm_trail_giveback,
                               iv_keep_hedge=iv_keep_hedge,
                               mtm_sl_basis=mtm_sl_basis)   # ── TSG_MTM_BASIS_20260821 ──

        diag["days_entered"] += 1
        if res["day_exit_reason"] == "MTM_TARGET":
            diag["mtm_exit_days"] += 1
        elif res["day_exit_reason"] == "MTM_SL":
            diag["mtm_sl_exit_days"] += 1
        elif res["day_exit_reason"] == "MTM_TRAIL":
            diag["mtm_trail_exit_days"] += 1
        elif res["day_exit_reason"] == "IV_SL":
            pass          # counted below via iv_sl_days; no EOD survivor day
        else:
            diag["eod_exit_days"] += 1
        if res.get("trail_armed"):
            diag["trail_armed_days"] += 1
        if iv_keep_hedge:
            diag["iv_kept_hedges"] += sum(
                1 for e in res["exits"].values() if e["reason"] == "IV_SL")
        _iv_legs = sum(1 for e in res["exits"].values()
                       if e["reason"] == "IV_SL")
        if _iv_legs:
            diag["iv_sl_days"] += 1
            diag["iv_sl_legs"] += _iv_legs
            diag["iv_sl_hedge_legs"] += sum(
                1 for e in res["exits"].values()
                if e["reason"] == "IV_SL_HEDGE")

        for l in day_legs:
            spec = selected[l["id"]]
            ex = res["exits"][l["id"]]
            _emit(leg=l, symbol=spec["symbol"], strike=spec.get("strike"),
                  expiry=spec.get("expiry"), entry_ts=entry_ts,
                  entry_price=spec["price"], exit_ts=ex["ts"],
                  exit_price=ex["price"], exit_reason=ex["reason"],
                  synthetic=bool(spec["synthetic"]),
                  synth_kind=spec.get("synth_kind"))

    conn.close()
    try:
        src.close()
    except Exception:
        pass

    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying} {date_from}→{date_to}: "
        f"{diag['days_entered']}/{diag['days_total']} days entered, "
        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"IVfilter skipped {diag['iv_filter_skipped_days']}d, "
        f"MTM exits {diag['mtm_exit_days']} / SL {diag['mtm_sl_exit_days']} "
        f"/ TRAIL {diag['mtm_trail_exit_days']} "
        f"(armed {diag['trail_armed_days']}d) "
        f"/ EOD {diag['eod_exit_days']}, "
        f"IV-SL days {diag['iv_sl_days']} (shorts {diag['iv_sl_legs']}, "
        f"hedges {diag['iv_sl_hedge_legs']}, "
        f"solveFail {diag['iv_solve_fail_minutes']}m), "
        f"wingFB {diag['wing_fallback_days']} / absent "
        f"{diag['wing_absent_days']}, "
        f"SYN short {diag['syn_short_legs']}/wing {diag['syn_wing_legs']} "
        f"(fail {diag['syn_short_fail']}/{diag['syn_wing_fail']}), "
        f"staleMarks {diag['mtm_stale_marks']}, "
        f"noSpotMin {diag['mtm_no_spot_minutes']}, "
        f"SYN net {diag['syn_pnl_net']} of {summary['net_pnl']} "
        f"({diag['syn_pnl_share_pct']}% by |P&L|), "
        f"skips: uncovered {diag['days_uncovered']} / "
        f"noShort {diag['days_no_short_strike']} / "
        f"noEntryPx {diag['days_no_entry_price']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}


# ── TSG_PARALLEL ── module-level worker (must be importable by spawn).
def _tsg_parallel_worker(db_path: str, strategy_id: str, underlying: str,
                         date_from_iso: str, date_to_iso: str,
                         cfg: dict) -> dict:
    out = run_tsg_backtest(
        db_path=db_path, strategy_id=strategy_id, underlying=underlying,
        date_from=date.fromisoformat(date_from_iso),
        date_to=date.fromisoformat(date_to_iso),
        config_override=cfg, progress_cb=None, cancel_cb=None)
    if out.get("aborted"):
        raise RuntimeError(f"chunk {date_from_iso}..{date_to_iso} aborted: "
                           f"{out.get('reason')}")
    return {"trades": out["trades"],
            "diag": out["summary"].get("diag_tsg", {})}