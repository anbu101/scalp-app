# backend/app/backtest/engine/backtest_selector.py
#
# Replays SCALP_V1 option selection FAITHFULLY: a 120s ROLLING re-selection on
# the live :30 grid (09:16:30, 09:18:30, ...), reproducing live OptionSelector
# logic against historical premiums. Each snapshot yields <=2 CE + <=2 PE.
#
# This matches the LIVE gate exactly. In live:
#   * selection_loop re-selects every 120s at phase :30 and writes
#     _selected_ce.json / _selected_pe.json (save_selection)
#   * SignalRouter._common_gates reads those files and DROPS any signal whose
#     symbol is not in the current selection (CE_NOT_SELECTED / PE_NOT_SELECTED)
# So an entry can only fire on a contract that is SELECTED at the moment the
# signal's candle closes. The runner gates entries on snapshot membership using
# the snapshot active at the candle's timestamp (the most recent :30 boundary
# at-or-before the candle).
#
# PREMIUM PROXY (1m data, agreed): live samples kite.ltp() at the :30 instant
# (mid-formation of that minute's candle). The faithful 1m proxy is the price of
# the candle CONTAINING that instant — its close is the nearest completed price.
# We use option_premium_at(sym, boundary_epoch), which snaps to the containing
# minute's candle. With session start 09:30 there is no early-open edge case.
#
# LOCK CARVE-OUT: a contract holding an open trade stays selected even if its
# premium drifts out of band (live preserves locked_ce/locked_pe). The runner
# owns the open-position book, so it injects the locked symbol into the active
# snapshot when querying membership — see runner. The selector itself produces
# the pure premium-band selection; locking is layered on at gate time.

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.backtest.engine.expiry_calendar import expected_expiry_for_day

ATM_RANGE = 800       # from selection_loop
STRIKE_STEP = 50

IST = 5 * 3600 + 30 * 60


def _infer_atm_median(strikes: List[float]) -> int:
    """EXACT replica of OptionSelector._infer_atm: median of sorted distinct
    strikes (NOT round(spot)). Load-bearing quirk — ATM is emergent from which
    strikes passed the premium band."""
    uniq = sorted({int(s) for s in strikes})
    return uniq[len(uniq) // 2]


def _select_at_boundary(
    *, src, underlying: str, universe: List[dict],
    boundary_epoch: int, sim_day: date,
    price_min: float, price_max: float, trade_mode: str,
) -> List[Dict]:
    """Run ONE selection at a :30 boundary against the premium at that instant.
    Mirrors OptionSelector.select(): premium band -> nearest expiry -> median
    ATM -> within ATM_RANGE -> per side nearest 2 to ATM."""
    candidates = []
    for c in universe:
        sym = c["tradingsymbol"]
        prem = src.option_premium_at(sym, boundary_epoch)
        if prem is None:
            continue
        if not (price_min <= prem <= price_max):
            continue
        candidates.append({
            "tradingsymbol": sym,
            "strike": float(c["strike"]),
            "type": c["instrument_type"],
            "expiry": c["expiry"],
            "ltp": prem,
        })
    if not candidates:
        return []

    # EXPECTED weekly expiry for this sim day (Tuesday rule), matching what LIVE
    # would have traded. We DO NOT fall back to a farther expiry: if the correct
    # weekly isn't present among candidates, this boundary selects nothing and
    # the day is treated as NO-COVERAGE upstream (build_selection_timeline).
    want_expiry = expected_expiry_for_day(sim_day).isoformat()
    opts = [o for o in candidates if o["expiry"] == want_expiry]
    if not opts:
        return []

    # ATM = median strike (live quirk)
    atm = _infer_atm_median([o["strike"] for o in opts])
    lower, upper = atm - ATM_RANGE, atm + ATM_RANGE
    opts = [o for o in opts if lower <= o["strike"] <= upper]
    if not opts:
        return []

    selected: List[Dict] = []
    for side in ("CE", "PE"):
        if trade_mode != "BOTH" and trade_mode != side:
            continue
        side_opts = [o for o in opts if o["type"] == side]
        side_opts.sort(key=lambda x: abs(x["strike"] - atm))
        selected.extend(side_opts[:2])
    return selected


def build_selection_timeline(
    *,
    src,                       # CandleSource
    underlying: str,
    day_start_epoch: int,
    cfg: dict,
    strategy_id: str,
    scope_to_expected_expiry: bool = False,   # ── HA_PRELOAD_SCOPE ── additive; default False = every existing caller byte-identical
) -> Dict:
    """Return a selection timeline for the day:
        {
          "boundaries": [epoch, ...],          # ascending :30 grid instants
          "snapshots": {epoch: [contract,...]},# selection at each boundary
          "all_symbols": set(...),             # union, for candle preloading
        }
    The runner uses the snapshot active at each candle (most recent boundary
    at-or-before the candle ts) to gate entries.
    """
    premium_cfg = cfg.get("option_premium", {})
    price_min = premium_cfg.get("min", 0)
    price_max = premium_cfg.get("max", 1e9)
    trade_mode = cfg.get("trade_side_mode", "BOTH").upper()

    sim_day = (datetime(1970, 1, 1) + timedelta(seconds=day_start_epoch + IST)).date()

    # ── HA_PRELOAD_SCOPE BEGIN ── opt-in expiry-scoped preload (the IC
    # PRELOAD_SCOPED lesson applied to the timeline). EQUIVALENCE: the
    # boundary selector below filters candidates to want_expiry REGARDLESS
    # (`opts = [o for o in candidates if o["expiry"] == want_expiry]`), so a
    # universe pre-scoped to want_expiry can never change which contracts are
    # selected — it only skips materialising rows the selector was going to
    # discard. Scoping also flips preload_day onto idx_bt1m_under_exp_ts
    # (no TEMP B-TREE). On uncovered days the unscoped classification query
    # runs ONCE to keep skip_reason strings identical to legacy.
    if scope_to_expected_expiry:
        _want = expected_expiry_for_day(sim_day).isoformat()
        universe = src.contracts_active_on_day(
            underlying, day_start_epoch, expiry=_want)
        if not universe:
            _full = src.contracts_active_on_day(underlying, day_start_epoch)
            if not _full:
                return {"boundaries": [], "snapshots": {}, "all_symbols": set(),
                        "covered": False, "expected_expiry": None,
                        "skip_reason": "no_contracts_for_day"}
            write_audit_log(
                f"[BACKTEST][NO_EXPIRY_COVERAGE] {underlying} {sim_day}: "
                f"expected weekly expiry {_want} is NOT in the corpus — day "
                f"SKIPPED (no faithful contract; refusing to fall back to a "
                f"farther expiry)."
            )
            return {"boundaries": [], "snapshots": {}, "all_symbols": set(),
                    "covered": False, "expected_expiry": _want,
                    "skip_reason": "expiry_not_in_corpus"}
    else:
        universe = src.contracts_active_on_day(underlying, day_start_epoch)
    # ── HA_PRELOAD_SCOPE END ──
    if not universe:
        return {"boundaries": [], "snapshots": {}, "all_symbols": set(),
                "covered": False, "expected_expiry": None,
                "skip_reason": "no_contracts_for_day"}

    # COVERAGE: the day is only faithfully backtestable if the corpus contains
    # contracts for the EXPECTED weekly expiry (the one live would have traded).
    # If not, we DO NOT silently use a farther expiry — we mark the day
    # uncovered so the runner skips it and reports it honestly.
    want_expiry = expected_expiry_for_day(sim_day).isoformat()
    have_expiry = any(c.get("expiry") == want_expiry for c in universe)
    if not have_expiry:
        write_audit_log(
            f"[BACKTEST][NO_EXPIRY_COVERAGE] {underlying} {sim_day}: expected "
            f"weekly expiry {want_expiry} is NOT in the corpus — day SKIPPED "
            f"(no faithful contract; refusing to fall back to a farther expiry)."
        )
        return {"boundaries": [], "snapshots": {}, "all_symbols": set(),
                "covered": False, "expected_expiry": want_expiry,
                "skip_reason": "expiry_not_in_corpus"}

    # :30 grid across the trading day. Live's first boundary after 09:15 is
    # 09:16:30; with session start 09:30 selection only matters from ~09:30:30.
    # We generate boundaries 09:16:30 .. 15:28:30 (every 120s) and let the
    # runner's session gate handle when entries are actually allowed.
    boundaries: List[int] = []
    # 09:16:30 in IST-day terms:
    first = day_start_epoch + (9 * 3600 + 16 * 60 + 30)
    last = day_start_epoch + (15 * 3600 + 30 * 60)
    b = first
    while b <= last:
        boundaries.append(b)
        b += 120

    snapshots: Dict[int, List[Dict]] = {}
    all_symbols = set()
    for be in boundaries:
        sel = _select_at_boundary(
            src=src, underlying=underlying, universe=universe,
            boundary_epoch=be, sim_day=sim_day,
            price_min=price_min, price_max=price_max, trade_mode=trade_mode,
        )
        snapshots[be] = sel
        for o in sel:
            all_symbols.add(o["tradingsymbol"])

    write_audit_log(
        f"[BACKTEST][SELECT] {underlying} {sim_day} 120s-timeline: "
        f"{len(boundaries)} boundaries, {len(all_symbols)} distinct symbols watched"
    )
    return {"boundaries": boundaries, "snapshots": snapshots,
            "all_symbols": all_symbols,
            "covered": True, "expected_expiry": want_expiry,
            "skip_reason": None}


def active_snapshot_for_ts(timeline: Dict, ts: int) -> List[Dict]:
    """The selection snapshot in effect at candle timestamp ts: the most recent
    :30 boundary at-or-before ts. Before the first boundary -> empty."""
    boundaries = timeline["boundaries"]
    if not boundaries or ts < boundaries[0]:
        return []
    # binary-search-ish: boundaries are sorted ascending, 120s apart
    lo, hi = 0, len(boundaries) - 1
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if boundaries[mid] <= ts:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best < 0:
        return []
    return timeline["snapshots"].get(boundaries[best], [])