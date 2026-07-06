# backend/app/backtest/ic/backtest_ic_runner.py
#
# ── IC_V1 RUNNER ── Iron Condor v1 over the 1m corpus. Time-entry
# premium-defined condor: SELL CE+PE nearest-below a premium cap, BUY far
# wings nearest-below a small cap, per-leg SL/TP (% or points), Move-To-Cost
# cross-leg rule, EOD square-off. One entry per day, no re-entry.
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
        norm_leg, select_strike, entry_close, simulate_day, leg_pnl,
    )
    from app.backtest.ic import ic_synth_wing as SW
except ImportError:  # standalone test harness
    from ic_v1_engine import (  # type: ignore
        norm_leg, select_strike, entry_close, simulate_day, leg_pnl,
    )
    import ic_synth_wing as SW  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65            # NIFTY

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
    as exit_reason MTC_COST."""
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
    exit_reason: Optional[str]    # SL | TP | MTC_COST | EOD
    qty: int
    condition: str                # leg tag (+ ·MTC when move-to-cost applied)
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
    strategy_id: str,           # "IC_V1"
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
      exit_time   "HH:MM"  (default "15:28" — EOD square-off)
      legs        list of up to 4 leg dicts (see DEFAULT_LEGS); lots 0
                  disables a leg; sl/tp value 0 = disabled; sl_mode/tp_mode
                  'pct' | 'pts'; mtc_other_on_sl + mtc_partner wire the
                  cross-leg Move-To-Cost between the two SHORT legs.
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
    raw_legs = cfg.get("legs") or DEFAULT_LEGS
    legs_cfg = [norm_leg(l) for l in raw_legs if int(l.get("lots") or 0) > 0]
    if not any(l["action"] == "SELL" for l in legs_cfg):
        return {"run_id": None, "aborted": True,
                "reason": "IC_V1 needs at least one SELL leg with lots > 0",
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
    }
    trades: List[ICTrade] = []

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
            write_audit_log(f"[BACKTEST][IC_V1] {d}: expected expiry "
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
        synth_specs: Dict[str, tuple] = {}   # leg id → (leg, spec) — no SL/TP, so bypasses simulate_day
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

        candles_by_leg = {
            lid: [{"ts": x.ts, "open": x.open, "high": x.high,
                   "low": x.low, "close": x.close}
                  for x in src.candles_1m_for_symbol_day(selected[lid], day_start)]
            for lid in selected
        }

        res = simulate_day(day_legs, candles_by_leg, selected, entry_ts, eod_ts)
        f = res["flags"]
        diag["days_entered"] += 1
        diag["mtc_activations"] += f["mtc_activations"]
        diag["ambiguous_fills"] += f["ambiguous"]
        diag["no_exit_data"] += f["no_exit_data"]
        if f["double_sl"]:
            diag["double_sl_days"] += 1

        for lt in res["trades"]:
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
            trades.append(ICTrade(
                tradingsymbol=lt["tradingsymbol"],
                symbol=lt["tradingsymbol"],
                instrument_type=lt["opt_type"],
                strike=m.get("strike"), expiry=m.get("expiry"),
                direction=lt["action"],
                entry_ts=lt["entry_ts"], entry_price=round(lt["entry_price"], 2),
                sl=(round(lt["sl_price"], 2) if lt["sl_price"] is not None else None),
                tp=(round(lt["tp_price"], 2) if lt["tp_price"] is not None else None),
                exit_ts=lt["exit_ts"],
                exit_price=(round(lt["exit_price"], 2)
                            if lt["exit_price"] is not None else None),
                exit_reason=lt["exit_reason"], qty=qty,
                condition=lt["leg"] + ("·MTC" if lt["mtc_applied"] else ""),
                ambiguous_fill=bool(lt["ambiguous_fill"]),
                pnl=round(gross, 2), charges=round(charges, 2),
                net_pnl=round(gross - charges, 2),
                gross=round(gross, 2), net=round(gross - charges, 2),
                ambiguous=bool(lt["ambiguous_fill"]),
            ))

        # ── WING_SYNTH ── book the modeled wings (no SL/TP → straight
        # entry→EOD trade; never passes through simulate_day)
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

    conn.close()
    try:
        src.close()
    except Exception:
        pass

    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][IC_V1] {underlying} {date_from}→{date_to}: "
        f"{diag['days_entered']}/{diag['days_total']} days entered, "
        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"MTC {diag['mtc_activations']}, doubleSL {diag['double_sl_days']}, "
        f"wingFB {diag['wing_fallback_days']}, "
        f"wingSYN {diag['wing_synth_days']}, "
        f"skips: uncovered {diag['days_uncovered']} / "
        f"noShort {diag['days_no_short_strike']} / "
        f"noEntryPx {diag['days_no_entry_price']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}