# backend/app/backtest/gc/backtest_gc_runner.py
#
# ── GC_V1 RUNNER ── first-candle breakout-retest with SL-flip re-entry,
# SIGNALS + SL ON SPOT (selectable timeframe), EXECUTION ON NIFTY WEEKLY
# OPTIONS. Decision logic lives in gc_v1_engine (pure, unit-tested); this
# shim owns corpus access, strike selection, option fills, the MTM day
# caps, charges, DIAG and persistence shape — the IC/TSG doctrine.
#
# LOCKED CONVENTIONS (D1–D11, confirmed 2026-08-14):
#   * TIMEFRAME (D9). Spot signals run on candles resampled from the 1m
#     corpus, session-anchored at 09:15 (buckets 09:15+k·tf). Option fills
#     are ALWAYS priced off the 1m option candle at the decision minute
#     (the tf candle's last 1m bar) — tf changes the decision grid, never
#     the fill fidelity.
#   * FILLS (D5). Entry = option 1m CLOSE at the entry-decision minute
#     (selection uses that same close, the IC "close of the candle ending
#     at entry" convention). Exit = option 1m close at the exit-decision
#     minute; a missing exit print falls back to the most recent option
#     close ≤ that minute in the same day (diag stale_exit_fills) — an
#     exit must never be dropped.
#   * STRIKE (D6). IC semantics via ic_v1_engine.select_strike: highest
#     premium ≤ premium_max on the expected weekly expiry. FAIL-CLOSED:
#     no strike ≤ cap → that entry is SKIPPED (diag no_strike_entries) but
#     the SPOT chain continues — signals are spot-truth, execution is
#     best-effort, and a skipped fill must not silence later flips.
#   * MODE (D11). mode=BUY: signal CE → BUY CE, signal PE → BUY PE.
#     mode=SELL: signal CE → SELL PE, signal PE → SELL CE. The spot SL
#     stays a SPOT-CLOSE rule in both modes — never a premium stop.
#   * DAY CAPS (D8). max_profit_day / max_loss_day (₹ GROSS, 0 = off) are
#     checked at every tf-candle close while a position is open, on
#     realized + open MTM (option marks at the tf close minute, stale
#     marks carried forward). A breach FORCE-EXITS at that minute (reason
#     MAX_PROFIT_DAY / MAX_LOSS_DAY) and HALTS the day — the rest of the
#     engine's chain is discarded (fail-closed).
#   * PREV-DAY TAIL (D1=a). The first entry's SL lookback window is the
#     previous SESSION's last `sl_lookback` tf candles. The runner carries
#     yesterday's resample forward; for the range's first day it fetches
#     the last spot session before date_from from the corpus. No prior
#     session in the corpus → empty window → SL falls back to H1/L1
#     (diag first_day_no_prevtail) — fail-safe, never fail-crash.
#
# Read-only on the corpus. LONG P&L = (exit-entry)·qty, SHORT = (entry-
# exit)·qty, qty = lots × 65. Charges via charges_model (direction-aware).

from __future__ import annotations

# PyInstaller anchors — tolerant if unavailable at module-import time.
try:
    import app.backtest.data.candle_source  # noqa: F401
except Exception:
    pass

import sqlite3
import uuid
from datetime import date
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.ic.ic_v1_engine import select_strike
    from app.backtest.ic.backtest_ic_runner import (
        ICTrade, IST, LOT_SIZE, _day_start_epoch, _hm_to_min,
    )
    from app.backtest.gc.gc_v1_engine import (
        TFCandle, resample_spot, simulate_gc_day,
    )
except ImportError:  # standalone test harness
    from ic_v1_engine import select_strike                        # type: ignore
    from backtest_ic_runner import (                              # type: ignore
        ICTrade, IST, LOT_SIZE, _day_start_epoch, _hm_to_min,
    )
    from gc_v1_engine import (                                    # type: ignore
        TFCandle, resample_spot, simulate_gc_day,
    )

# ── STOCK_LOT_AUTO_20260828 ── auto lot resolution. Stock lots come from the Dhan scrip-master
# cache (weekly refresh, stale-tolerant, offline-safe); indexes keep LOT_SIZE.
try:
    from app.backtest.util.lot_sizes import resolve_lot, unresolved_reason
except ImportError:  # standalone test harness
    from lot_sizes import resolve_lot, unresolved_reason   # type: ignore

SESSION_OPEN_MIN = 9 * 60 + 15          # 09:15 IST — C1 anchor
VALID_TF = (1, 3, 5, 10, 15)

# ── GC_STOCK_MODE (2026-08-15) ── GC runs on stock corpora too. Indexes use
# the main backtest.db + weekly expiries + LOT_SIZE; stocks resolve to
# corpus/<U>.db (sibling of the main db), MONTHLY expiries via the SAME
# calendar function that stamped the corpus (self-consistency doctrine), and
# a per-underlying lot. Unknown stock lot = ABORT, never a guessed qty.
INDEX_UNDERLYINGS = ("NIFTY", "BANKNIFTY")
STOCK_LOT_SIZES = {
    "DIXON": 50,        # scrip-master, 2026-08-15 backfill run
}


def _resolve_corpus_db(db_path: str, underlying: str) -> str:
    """Index → the given main db. Stock → corpus/<U>.db beside it."""
    if underlying in INDEX_UNDERLYINGS:
        return db_path
    from pathlib import Path
    return str(Path(db_path).parent / "corpus" / f"{underlying}.db")

DEFAULT_GC_CONFIG = {
    "exit_time": "15:15",
    "entry_cutoff_time": "13:00",   # GC_ENTRY_CUTOFF: no NEW entries (incl. flips)
                                    # whose decision candle closes after this;
                                    # open trades still run to SL/EOD
    "max_trades_per_day": 4,
    "premium_max": 200,
    "lots": 1,
    "mode": "BUY",                  # BUY | SELL
    "max_profit_day": 0,            # ₹ gross, 0 = off
    "max_loss_day": 0,              # ₹ gross, 0 = off
    "timeframe_minutes": 1,         # 1 | 3 | 5 | 10 | 15
    "signal_mode": "latest",        # latest | first (D4)
    "sl_lookback": 10,
    "c1_range_max_pct": 0.3,        # C1 (H-L) > pct% of prev close → skip day; 0 = off
    "c1_skip_candles": 0,           # ── GC_C1_SKIP ── drop N opening candles before C1
    "max_sl_pct": 0.3,              # GC_SL_CAP: prev-day anchor farther than pct% of
                                    # prev close from entry spot → L1/H1 fallback; 0 = off
    "hedge_premium_max": 5,         # GC_HEDGE (SELL only): BUY same-side deeper-OTM
                                    # hedge ≤ this premium at entry; 0 = no hedge
    "max_loss_per_trade": 0,        # GC_TRADE_CAPS: ₹ gross combined (sold+hedge)
    "max_profit_per_trade": 0,      # MTM per trade at tf closes; 0 = off
    "max_loss_month": 0,            # GC_MONTH_CAP: month-to-date net + today's MTM
                                    # floor → halt rest of the calendar month; 0 = off
    "underlying": "NIFTY",          # GC_STOCK_MODE: overrides the route arg so the
                                    # queue/sweep paths need no plumbing; stock names
                                    # resolve corpus/<U>.db + monthly expiries
    "lot_size": 0,                  # 0 = auto (index → LOT_SIZE, known stock → map);
                                    # unknown stock with 0 → fail-closed abort
    "min_entry_volume": 0,          # GC_LIQ_GATE: entry-minute option volume floor for
                                    # BOTH strike selection and the hedge; 0 = off
    "premium_max_pct": 0,           # GC_PREM_PCT: premium cap as % of SPOT at the
                                    # entry-decision minute; both caps set → TIGHTER
                                    # wins; 0 = pct off (absolute-only, legacy)
    "strike_selection": "premium",  # GC_ATM_SELECT: "premium" (legacy: highest ≤ cap)
                                    # | "atm" (moneyness: ATM + atm_offset strikes OTM
                                    # — theta-invariant, the right mode for MONTHLIES
                                    # where premiums shift ~4x across the cycle)
    "atm_offset": 0,                # "atm" mode: 0 = at-the-money, +N = N strikes
                                    # OTM-ward, -N = N strikes ITM-ward
    "hedge_offset": 2,              # GC_HEDGE_V2 (atm mode, SELL): hedge = N strikes
                                    # FURTHER OTM than the sold strike; 0 = no hedge.
                                    # hedge_premium_max is IGNORED in atm mode
}


def _norm_cfg(raw: Optional[dict]) -> dict:
    cfg = dict(DEFAULT_GC_CONFIG)
    for k, v in (raw or {}).items():
        if k in cfg and v is not None:
            cfg[k] = v
    cfg["max_trades_per_day"] = max(0, int(cfg["max_trades_per_day"] or 0))
    cfg["premium_max"] = float(cfg["premium_max"] or 0)
    cfg["lots"] = max(0, int(cfg["lots"] or 0))
    cfg["mode"] = "SELL" if str(cfg["mode"]).upper() == "SELL" else "BUY"
    cfg["max_profit_day"] = abs(float(cfg["max_profit_day"] or 0))
    cfg["max_loss_day"] = abs(float(cfg["max_loss_day"] or 0))
    tf = int(cfg["timeframe_minutes"] or 1)
    cfg["timeframe_minutes"] = tf if tf in VALID_TF else 1
    cfg["signal_mode"] = ("first" if str(cfg["signal_mode"]).lower() == "first"
                          else "latest")
    cfg["sl_lookback"] = max(1, int(cfg["sl_lookback"] or 10))
    cfg["c1_range_max_pct"] = abs(float(cfg["c1_range_max_pct"] or 0))
    cfg["c1_skip_candles"] = max(0, int(cfg.get("c1_skip_candles") or 0))
    cfg["max_sl_pct"] = abs(float(cfg["max_sl_pct"] or 0))   # ── GC_SL_CAP ──
    cfg["entry_cutoff_time"] = str(cfg["entry_cutoff_time"] or "13:00")   # ── GC_ENTRY_CUTOFF ──
    cfg["hedge_premium_max"] = abs(float(cfg["hedge_premium_max"] or 0))  # ── GC_HEDGE ──
    cfg["max_loss_per_trade"] = abs(float(cfg["max_loss_per_trade"] or 0))     # ── GC_TRADE_CAPS ──
    cfg["max_profit_per_trade"] = abs(float(cfg["max_profit_per_trade"] or 0))
    cfg["max_loss_month"] = abs(float(cfg["max_loss_month"] or 0))   # ── GC_MONTH_CAP ──
    cfg["underlying"] = str(cfg["underlying"] or "NIFTY").upper().strip()   # ── GC_STOCK_MODE ──
    cfg["lot_size"] = max(0, int(cfg["lot_size"] or 0))
    cfg["min_entry_volume"] = max(0, int(cfg["min_entry_volume"] or 0))     # ── GC_LIQ_GATE ──
    cfg["premium_max_pct"] = abs(float(cfg["premium_max_pct"] or 0))        # ── GC_PREM_PCT ──
    cfg["strike_selection"] = ("atm" if str(cfg["strike_selection"]).lower() == "atm"
                               else "premium")                              # ── GC_ATM_SELECT ──
    cfg["atm_offset"] = int(cfg["atm_offset"] or 0)
    cfg["hedge_offset"] = max(0, int(cfg["hedge_offset"] or 0))   # ── GC_HEDGE_V2 ──
    return cfg


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[ICTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_gc"] = diag
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
        "ambiguous_fills": 0,   # GC fills are close-of-minute → never ambiguous
        "diag_gc": diag,
    }


def run_gc_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "GC_V1"
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
            return _run_gc_backtest_impl(
                db_path=db_path, strategy_id=strategy_id,
                underlying=underlying, date_from=date_from, date_to=date_to,
                config_override=config_override,
                progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _run_gc_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb)


def _run_gc_backtest_impl(
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
    """config keys (see DEFAULT_GC_CONFIG):
      exit_time           "HH:MM" (default "15:15") — EOD square-off boundary
      max_trades_per_day  int (default 4; 0 = unlimited) — entries incl. flips
      premium_max         float ₹ — highest premium ≤ cap (IC semantics),
                          fail-closed per entry when nothing qualifies
      lots                int — NIFTY lots (qty = lots × 65)
      mode                "BUY" | "SELL" (D11 side mapping)
      max_profit_day      float ₹ gross (0 = off) — force-exit + halt (D8)
      max_loss_day        float ₹ gross (0 = off) — force-exit + halt (D8)
      timeframe_minutes   1|3|5|10|15 (D9)
      signal_mode         "latest" | "first" (D4)
      sl_lookback         int (default 10) — SL window size (D1/D2)
      c1_range_max_pct    float percent (default 0.3; 0 = off) — skip the
                          day when C1's (high - low) is strictly greater
                          than pct% of the PREVIOUS session's last spot
                          close. Gate on + no prev close in the corpus →
                          day skipped fail-closed (diag).
      entry_cutoff_time   "HH:MM" (default "13:00") — GC_ENTRY_CUTOFF: no
                          NEW entries (initial or flip) whose decision
                          candle closes after this time; an already-open
                          trade still runs to its SL/EOD. Set = exit_time
                          to disable.
      hedge_premium_max   float ₹ (default 5; 0 = off) — GC_HEDGE, SELL
                          mode only: BUY a same-side deeper-OTM hedge at
                          the sold leg's entry minute (highest premium ≤
                          cap among the remaining strikes; cheapest real
                          as a flagged fallback). The hedge has NO own
                          SL/TP — it exits at the sold leg's exit minute
                          at its own price. FAIL-CLOSED: hedge wanted but
                          none fillable → the entry is SKIPPED. Ignored in
                          BUY mode.
      max_loss_per_trade  float ₹ gross (default 0 = off) — GC_TRADE_CAPS:
                          per-trade floor on the COMBINED (sold + hedge)
                          open MTM at every tf close → cut (MAX_LOSS_TRADE)
                          without halting the day; later flips still run.
      max_profit_per_trade float ₹ gross (default 0 = off) — same, ceiling
                          (MAX_PROFIT_TRADE).
      max_loss_month      float ₹ (default 0 = off) — GC_MONTH_CAP monthly
                          circuit breaker, per user spec: NET P&L of the
                          month's prior completed days + today's realized
                          NET + open GROSS MTM, evaluated at every tf close
                          (open MTM is gross because today's exit charges
                          are unknown until the exit). Breach → force-exit
                          (MAX_LOSS_MONTH) and HALT every remaining day of
                          the calendar month (prev-day tails still carry so
                          next month's D1 windows are correct). Month
                          rollover resets. Checked BEFORE the day caps —
                          the outermost guard wins a same-close tie.
      premium_max_pct     float percent of SPOT (default 0 = off) —
                          GC_PREM_PCT: price-level-invariant premium cap
                          against the decision-minute spot close; with the
                          absolute premium_max both set, the TIGHTER wins.
                          ~1.5% of spot ≈ near-ATM at any price level.
                          NOTE (2026-08-15 DIXON month-tail incident): on
                          MONTHLY contracts a static premium cap is theta-
                          bound — early-cycle ATM ≈ 3.5-5% of spot, final
                          week ≈ 1-1.5% — so premium selection trades only
                          near expiry. Use strike_selection="atm" for
                          monthlies.
      strike_selection    "premium" (legacy) | "atm" — GC_ATM_SELECT:
                          moneyness selection, theta- and price-invariant:
                          ATM = strike nearest the decision-minute spot,
                          then atm_offset strikes OTM-ward (CE: higher, PE:
                          lower; negative = ITM-ward). Liquidity gate still
                          applies; the premium caps become an optional VETO
                          (selected strike's premium above cap → entry
                          skipped, diag no_strike_entries). Offset beyond
                          the day's ladder → fail-closed skip.
      atm_offset          int (default 0) — see above.
      max_sl_pct          float percent (default 0.3; 0 = off) — GC_SL_CAP
                          gap-day protection (D12/D13): a PREV-DAY-donated
                          SL anchor farther than pct% of prev close from
                          the ENTRY SPOT is rejected → SL = L1/H1 instead.
                          Today-donated anchors are never capped.
    """
    try:
        from app.event_bus.audit_logger import write_audit_log
    except ImportError:
        def write_audit_log(msg: str) -> None:   # type: ignore
            print(msg)
    try:
        from app.backtest.data.candle_source import CandleSource
    except ImportError:
        from data.candle_source import CandleSource               # type: ignore
    try:
        from app.backtest.engine.expiry_calendar import (
            expected_expiry_for_day, expected_stock_monthly_expiry_for_day)
    except ImportError:
        from engine.expiry_calendar import (                      # type: ignore
            expected_expiry_for_day, expected_stock_monthly_expiry_for_day)
    try:
        from app.backtest.charges.charges_model import (
            charges_for_long_trade, charges_for_short_trade)
    except Exception:
        charges_for_long_trade = charges_for_short_trade = None

    cfg = _norm_cfg(config_override)
    # ── GC_STOCK_MODE ── config underlying wins over the route arg (queue
    # and sweep POST "NIFTY" unconditionally; the config travels everywhere).
    underlying = cfg["underlying"] or underlying
    is_stock = underlying not in INDEX_UNDERLYINGS
    db_path = _resolve_corpus_db(db_path, underlying)
    tf = cfg["timeframe_minutes"]
    tf_s = tf * 60
    exit_min = _hm_to_min(cfg.get("exit_time", "15:15"), 15 * 60 + 15)
    cutoff_min = _hm_to_min(cfg.get("entry_cutoff_time", "13:00"), 13 * 60)   # ── GC_ENTRY_CUTOFF ──
    # ── STOCK_LOT_AUTO_20260828 ── lot: explicit config > index constant > live scrip
    # master > corpus stamp > stale cache > legacy static map > fail-closed.
    # A wrong qty is silent P&L corruption; no guessing, ever.
    lot_size, lot_source = resolve_lot(
        underlying=underlying, is_stock=is_stock, cfg_lot=cfg["lot_size"],
        index_lot=LOT_SIZE, db_path=db_path, static_map=STOCK_LOT_SIZES)
    if lot_size is None:
        return {"run_id": None, "aborted": True,
                "reason": unresolved_reason(underlying),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── FRAME_BREAK_GUARD_20260828 ── refuse a range that crosses a recorded
    # price-frame break. On the as-traded side of a split/bonus the underlying
    # genuinely changes scale overnight; a carried position books an
    # artificial gap that will dominate the P&L and look like a real trade.
    # Fail closed — the override is an explicit corpus_meta edit, not a flag.
    if is_stock:
        try:
            from app.backtest.util.corpus_health import frame_break_reason
        except ImportError:                              # standalone harness
            from corpus_health import frame_break_reason  # type: ignore
        _fb = frame_break_reason(db_path, date_from, date_to)
        if _fb:
            return {"run_id": None, "aborted": True,
                    "reason": f"{underlying}: {_fb}",
                    "trades": [], "summary": _empty_summary(),
                    "config": cfg, "strategy_id": strategy_id}
    mode = cfg["mode"]
    hedge_cap = cfg["hedge_premium_max"]              # ── GC_HEDGE ── SELL only
    hedge_off = cfg["hedge_offset"]                   # ── GC_HEDGE_V2 ──
    # ── GC_HEDGE / GC_HEDGE_V2 ── premium mode keys the hedge on the ₹ cap
    # (legacy NIFTY behavior); atm mode keys it on hedge_offset ≥ 1 and
    # selects by MONEYNESS from the sold strike (₹4-on-DIXON incident).
    use_hedge = (mode == "SELL" and
                 ((cfg["strike_selection"] == "atm" and cfg["hedge_offset"] >= 1)
                  or (cfg["strike_selection"] != "atm" and hedge_cap > 0)))
    max_lt = cfg["max_loss_per_trade"]                # ── GC_TRADE_CAPS ──
    max_pt = cfg["max_profit_per_trade"]
    max_lm = cfg["max_loss_month"]                    # ── GC_MONTH_CAP ──
    lots = cfg["lots"]
    qty = lots * lot_size   # ── GC_STOCK_MODE ──
    min_vol = cfg["min_entry_volume"]   # ── GC_LIQ_GATE ──
    prem_pct = cfg["premium_max_pct"]   # ── GC_PREM_PCT ──
    sel_mode = cfg["strike_selection"]  # ── GC_ATM_SELECT ──
    atm_off = cfg["atm_offset"]

    if lots <= 0:
        return {"run_id": None, "aborted": True,
                "reason": "lots must be > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    if exit_min * 60 <= SESSION_OPEN_MIN * 60 + tf_s:
        return {"run_id": None, "aborted": True,
                "reason": f"exit_time {cfg['exit_time']} leaves no room after "
                          f"C1 at {tf}m — nothing to simulate",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── GC_STOCK_MODE ── a stock run against a missing corpus aborts with
    # the fix in the message, never an empty-range mystery.
    import os as _os
    if is_stock and not _os.path.exists(db_path):
        return {"run_id": None, "aborted": True,
                "reason": f"{underlying}: corpus db not found at {db_path} — "
                          f"run: python3 -m app.backtest.dhan.stock_backfill "
                          f"--underlying {underlying} --db {db_path}",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    lo, hi = _day_start_epoch(date_from), _day_start_epoch(date_to) + 86400
    spot_days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
        ORDER BY d""", (underlying, lo, hi))]
    if not spot_days:
        conn.close()
        try:
            src.close()
        except Exception:
            pass
        return {"run_id": None, "aborted": True,
                "reason": "no NIFTY spot data in range — run the spot backfill",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── GC_SPOT_IDX ── this query needs idx_bt1m_under_type_ts
    # (underlying, instrument_type, ts): without it a single-stock corpus
    # full-scans per day. stock_backfill creates it; see maintenance one-liner
    # in the incident notes for pre-existing corpora.
    def spot_1m_for(d: date) -> List[dict]:
        ds = _day_start_epoch(d)
        return [dict(r) for r in cur.execute("""
            SELECT ts, open, high, low, close FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
            ORDER BY ts""", (underlying, ds, ds + 86400))]

    # ── PREV_TAIL SEED (D1=a) ── last spot session strictly before the range.
    # The tail's LAST candle close doubles as the C1-range gate's reference
    # (previous session's closing spot).
    prev_tail: List[TFCandle] = []
    row = cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT' AND ts<?
        ORDER BY d DESC LIMIT 1""", (underlying, lo)).fetchone()
    first_day_no_prevtail = 0
    if row:
        pd = date.fromisoformat(row["d"])
        pcs = resample_spot(spot_1m_for(pd), tf,
                            _day_start_epoch(pd) + SESSION_OPEN_MIN * 60)
        prev_tail = pcs[-cfg["sl_lookback"]:]
    else:
        first_day_no_prevtail = 1

    diag = {
        "days_total": len(spot_days), "days_traded": 0,
        "days_no_options": 0, "days_uncovered": 0,
        "days_no_breakout": 0, "days_armed_no_retrace": 0,
        "spot_entries": 0, "flip_entries": 0, "same_candle_sl": 0,
        "sl_fallback_entries": 0, "sl_cap_fallbacks": 0,
        "cap_blocked_flips": 0, "cutoff_blocked_entries": 0,
        "hedge_entries": 0, "hedge_cheapest_fb": 0, "hedge_skip_entries": 0,
        "hedge_geom_skips": 0,   # ── GC_HEDGE_V2 ── offset off-ladder or not cheaper
        "trade_loss_cuts": 0, "trade_profit_cuts": 0,
        "month_loss_halts": 0, "days_month_halted": 0,
        "liq_gate_entries": 0,   # ── GC_LIQ_GATE ── entries lost to the gate
        "rearm_switches": 0,
        "no_strike_entries": 0, "no_entry_price": 0,
        "sl_exits": 0, "eod_exits": 0,
        "max_profit_halts": 0, "max_loss_halts": 0,
        "halt_dropped_trades": 0,
        "stale_exit_fills": 0, "stale_marks": 0,
        "days_c1_range_skip": 0, "days_c1_range_no_ref": 0,
        "first_day_no_prevtail": first_day_no_prevtail,
        "c1_range_max_pct": cfg["c1_range_max_pct"],
        "c1_skip_candles": cfg["c1_skip_candles"],
        "max_sl_pct": cfg["max_sl_pct"],   # ── GC_SL_CAP ──
        "entry_cutoff_time": cfg["entry_cutoff_time"],   # ── GC_ENTRY_CUTOFF ──
        "hedge_premium_max": cfg["hedge_premium_max"],   # ── GC_HEDGE ──
        "max_loss_per_trade": cfg["max_loss_per_trade"],
        "max_profit_per_trade": cfg["max_profit_per_trade"],
        "max_loss_month": max_lm,   # ── GC_MONTH_CAP ──
        "underlying": underlying, "is_stock": is_stock,   # ── GC_STOCK_MODE ──
        "lot_size": lot_size, "lot_source": lot_source,   # STOCK_LOT_AUTO_20260828
        "min_entry_volume": min_vol,
        "premium_max_pct": prem_pct,   # ── GC_PREM_PCT ──
        "strike_selection": sel_mode, "atm_offset": atm_off,   # ── GC_ATM_SELECT ──
        "hedge_offset": hedge_off if (mode == "SELL" and sel_mode == "atm") else 0,
        "corpus_db": db_path.rsplit("/", 1)[-1],
        "timeframe_minutes": tf, "mode": mode,
        "signal_mode": cfg["signal_mode"], "sl_lookback": cfg["sl_lookback"],
    }
    trades: List[ICTrade] = []

    charges_long = charges_for_long_trade
    charges_short = charges_for_short_trade

    def _emit(*, opt_side: str, action: str, symbol: str, strike, expiry,
              entry_ts: int, entry_price: float, sl_spot: float,
              exit_ts: int, exit_price: float, exit_reason: str,
              tag: str) -> None:
        gross = ((exit_price - entry_price) if action == "BUY"
                 else (entry_price - exit_price)) * qty
        charges = 0.0
        fn = charges_short if action == "SELL" else charges_long
        if fn is not None:
            try:
                cr = fn(entry_price=entry_price, exit_price=exit_price, qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        trades.append(ICTrade(
            tradingsymbol=symbol, symbol=symbol,
            instrument_type=opt_side,
            strike=strike, expiry=expiry,
            direction=action,
            entry_ts=entry_ts, entry_price=round(entry_price, 2),
            sl=round(sl_spot, 2), tp=None,      # SL is the SPOT level (info)
            exit_ts=exit_ts, exit_price=round(exit_price, 2),
            exit_reason=exit_reason, qty=qty,
            condition=tag, ambiguous_fill=False,
            pnl=round(gross, 2), charges=round(charges, 2),
            net_pnl=round(gross - charges, 2),
            gross=round(gross, 2), net=round(gross - charges, 2),
            ambiguous=False, synthetic=False, synth_kind=None,
        ))

    # ── GC_MONTH_CAP ── month accumulator: NET P&L of completed days in
    # the current calendar month; halted flag survives until rollover.
    month_key = None
    month_net = 0.0
    month_halted = False

    for di, d in enumerate(spot_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(spot_days),
                         "date": d.isoformat()})

        day_start = _day_start_epoch(d)
        session0 = day_start + SESSION_OPEN_MIN * 60
        exit_epoch = day_start + exit_min * 60
        cutoff_epoch = day_start + cutoff_min * 60   # ── GC_ENTRY_CUTOFF ──

        today_tf = resample_spot(spot_1m_for(d), tf, session0)
        # carry the tail forward BEFORE any skip below — tomorrow's D1 window
        # is "the previous session", options coverage notwithstanding.
        next_tail = today_tf[-cfg["sl_lookback"]:] if today_tf else prev_tail

        # ── GC_MONTH_CAP ── rollover resets; a halted month skips the whole
        # day BUT the tail still carries (next month's D1 window needs it).
        mk = (d.year, d.month)
        if mk != month_key:
            month_key, month_net, month_halted = mk, 0.0, False
        if month_halted:
            diag["days_month_halted"] += 1
            prev_tail = next_tail
            continue

        sim = simulate_gc_day(today_tf, prev_tail, {
            "tf_s": tf_s, "exit_epoch": exit_epoch,
            "max_trades": cfg["max_trades_per_day"],
            "entry_cutoff_epoch": cutoff_epoch,   # ── GC_ENTRY_CUTOFF ──
            "signal_mode": cfg["signal_mode"],
            "sl_lookback": cfg["sl_lookback"],
            # ── GC_C1_RANGE_GATE ── reference = previous session's close
            "c1_range_max_pct": cfg["c1_range_max_pct"],
        "c1_skip_candles": cfg["c1_skip_candles"],
            "max_sl_pct": cfg["max_sl_pct"],   # ── GC_SL_CAP ──
            "prev_close": (float(prev_tail[-1].close) if prev_tail else None),
        })
        prev_tail = next_tail
        sd = sim["diag"]
        for k_src, k_dst in ((("no_breakout"), "days_no_breakout"),
                             (("armed_no_retrace"), "days_armed_no_retrace")):
            diag[k_dst] += sd.get(k_src, 0)
        for k in ("flip_entries", "same_candle_sl", "sl_fallback_entries",
                  "sl_cap_fallbacks", "cap_blocked_flips",
                  "cutoff_blocked_entries", "rearm_switches"):
            diag[k] += sd.get(k, 0)
        diag["spot_entries"] += sd.get("entries", 0)
        diag["days_c1_range_skip"] += sd.get("c1_range_skip", 0)
        diag["days_c1_range_no_ref"] += sd.get("c1_range_no_ref", 0)
        if not sim["trades"]:
            continue

        # ── GC_STOCK_MODE ── SAME calendar function that stamped the corpus
        # (self-consistency doctrine: selector and corpus cannot disagree).
        want_expiry = (expected_stock_monthly_expiry_for_day(d)
                       if is_stock else expected_expiry_for_day(d)).isoformat()
        # ── GC_PRELOAD_SCOPED (2026-08-15) ── expiry-scoped universe: the
        # scoped predicate is a clean (underlying, expiry, ts) index prefix.
        # UNSCOPED on a single-stock corpus, `underlying=?` prefixes the
        # ENTIRE db and ts cannot be bounded — a full-corpus scan PER DAY
        # (the DIXON 13.5s/day incident; measured 381→5 ms/day at 2.6M rows).
        week = src.contracts_active_on_day(underlying, day_start,
                                           expiry=want_expiry)
        if not week:
            diag["days_uncovered"] += 1
            write_audit_log(f"[BACKTEST][{strategy_id}] {d}: expected expiry "
                            f"{want_expiry} absent from corpus — day skipped")
            continue
        meta = {c["tradingsymbol"]: c for c in week}
        by_side = {"CE": [c["tradingsymbol"] for c in week
                          if c["instrument_type"] == "CE"],
                   "PE": [c["tradingsymbol"] for c in week
                          if c["instrument_type"] == "PE"]}
        opt_1m: Dict[str, Dict[int, float]] = {}
        opt_vol: Dict[str, Dict[int, int]] = {}   # ── GC_LIQ_GATE ──

        def _opt_closes(sym: str) -> Dict[int, float]:
            m = opt_1m.get(sym)
            if m is None:
                bars = src.candles_1m_for_symbol_day(sym, day_start)
                m = {c.ts: float(c.close) for c in bars}
                opt_1m[sym] = m
                opt_vol[sym] = {c.ts: int(c.volume or 0) for c in bars}
            return m

        def _vol_at(sym: str, minute_ts: int) -> int:
            _opt_closes(sym)
            return opt_vol.get(sym, {}).get(minute_ts, 0)

        def _fill_at(sym: str, minute_ts: int,
                     allow_stale: bool) -> Optional[float]:
            m = _opt_closes(sym)
            px = m.get(minute_ts)
            if px is not None:
                return px
            if not allow_stale:
                return None
            older = [t for t in m if t <= minute_ts]
            if not older:
                return None
            diag["stale_exit_fills"] += 1
            return m[max(older)]

        # ── DAY WALK ── engine chain → option fills, gross day-cap checks
        # at every tf close between entry and exit (D8).
        day_realized = 0.0
        day_net = 0.0        # ── GC_MONTH_CAP ── today's realized NET
        halted = False
        traded_day = False
        max_p, max_l = cfg["max_profit_day"], cfg["max_loss_day"]

        for tn, st in enumerate(sim["trades"]):
            if halted:
                diag["halt_dropped_trades"] += 1
                continue
            opt_side = st.signal_side if mode == "BUY" else \
                ("PE" if st.signal_side == "CE" else "CE")
            action = "BUY" if mode == "BUY" else "SELL"
            tag = "GC" if st.flip_seq == 0 else f"GC·FLIP{st.flip_seq}"

            # selection + fill at the entry-decision minute close (D5/D6)
            cands = []
            gated = 0
            for sym in by_side.get(opt_side, []):
                px = _opt_closes(sym).get(st.entry_ts)
                if not px:
                    continue
                # ── GC_LIQ_GATE ── a priced-but-untraded minute is a stale
                # print wearing a price; on stock options that is the norm,
                # not the exception. Gate BEFORE selection so the premium cap
                # can only pick strikes that actually traded this minute.
                if min_vol > 0 and _vol_at(sym, st.entry_ts) < min_vol:
                    gated += 1
                    continue
                cands.append((sym, px))
            if min_vol > 0 and gated and not cands:
                diag["liq_gate_entries"] += 1
            # ── GC_PREM_PCT ── effective cap: pct-of-spot at THIS entry's
            # decision spot; tighter of {absolute, pct} when both set.
            eff_cap = cfg["premium_max"]
            if prem_pct > 0:
                pct_cap = float(st.entry_spot) * prem_pct / 100.0
                eff_cap = min(eff_cap, pct_cap) if eff_cap > 0 else pct_cap
            if sel_mode == "atm":
                # ── GC_ATM_SELECT ── moneyness selection: sort the gated
                # ladder by strike, anchor at the strike nearest the spot,
                # step atm_offset OTM-ward (CE up, PE down). The caps are a
                # VETO here, not the selector.
                ladder = sorted(
                    ((float(meta[c[0]].get("strike") or 0), c[0], c[1])
                     for c in cands if meta.get(c[0], {}).get("strike")),
                    key=lambda x: x[0])
                pick = None
                atm_ladder, atm_ti = ladder, None   # ── GC_HEDGE_V2 ──
                if ladder:
                    spot_now = float(st.entry_spot)
                    ai = min(range(len(ladder)),
                             key=lambda i: abs(ladder[i][0] - spot_now))
                    ti = ai + (atm_off if opt_side == "CE" else -atm_off)
                    if 0 <= ti < len(ladder):
                        atm_ti = ti
                        _, psym, ppx = ladder[ti]
                        if eff_cap > 0 and ppx > eff_cap:
                            pick = None          # cap veto
                        else:
                            pick = (psym, ppx, False)
            else:
                pick = select_strike(cands, eff_cap)
            if pick is None:
                diag["no_strike_entries" if cands else "no_entry_price"] += 1
                continue
            sym, entry_px, _fb = pick
            m = meta.get(sym, {})

            # ── GC_HEDGE BEGIN ── SELL mode: BUY same-side deeper-OTM hedge
            # at the same entry minute (TMA pattern: highest ≤ cap among the
            # REMAINING strikes; cheapest real as flagged fallback). FAIL-
            # CLOSED: hedge configured but none fillable → skip the entry —
            # a naked short must never appear where a hedged one was asked.
            hsym = hentry_px = None
            hm = {}
            if use_hedge and sel_mode == "atm":
                # ── GC_HEDGE_V2 ── moneyness hedge: hedge_offset strikes
                # FURTHER OTM than the sold strike, same gated ladder. HARD
                # GUARDS, both fail-closed (skip the entry): off-ladder
                # offset, or a hedge not strictly cheaper than the short —
                # a "hedge" pricier than its short is a second position,
                # not protection (2026-08-15 DIXON pair audit: hedges gave
                # back ₹38k of the shorts' ₹113k).
                hi = (atm_ti + hedge_off) if opt_side == "CE"                     else (atm_ti - hedge_off)
                if atm_ti is None or not (0 <= hi < len(atm_ladder)):
                    diag["hedge_geom_skips"] += 1
                    continue
                _, hsym, hpx = atm_ladder[hi]
                if float(hpx) >= float(entry_px):
                    diag["hedge_geom_skips"] += 1
                    hsym = None
                    continue
                hentry_px = float(hpx)
                hm = meta.get(hsym, {})
            elif use_hedge:
                rest = [c for c in cands if c[0] != sym]   # cands already gated
                hpick = select_strike(rest, hedge_cap)
                if hpick is None:
                    hpick = select_strike(rest, hedge_cap,
                                          fallback_cheapest=True)
                    if hpick is not None:
                        diag["hedge_cheapest_fb"] += 1
                if hpick is None:
                    diag["hedge_skip_entries"] += 1
                    continue
                hsym, hentry_px = hpick[0], float(hpick[1])
                hm = meta.get(hsym, {})
            # ── GC_HEDGE END ──

            exit_ts, exit_reason = st.exit_ts, st.exit_reason
            exit_px = None
            hexit_px = None

            # tf-close marks inside the trade for the day caps (gross).
            # INDEX INVARIANT: the engine's sess list is a strict PREFIX of
            # today_tf (its filter `end <= exit_epoch` is monotonic in ts),
            # so entry_idx/exit_idx index today_tf directly. The in-loop
            # `end > exit_epoch` break is the belt to that brace.
            # ── GC_TRADE_CAPS ── open MTM is the COMBINED book: sold leg
            # + hedge (when present, hedge is long: (mark − entry) · qty).
            # Day caps (D8) are checked FIRST — the outer guard wins a
            # same-close tie and HALTS; per-trade caps cut THIS trade only,
            # the engine chain's later flips still run (spot signals are
            # unaffected by a premium-side cut).
            walk_caps = (max_p > 0 or max_l > 0 or max_lt > 0 or max_pt > 0
                         or max_lm > 0)   # ── GC_MONTH_CAP ── month cap alone must still walk
            if walk_caps and st.exit_idx > st.entry_idx:
                last_mark = entry_px
                h_last_mark = hentry_px
                for c in today_tf[st.entry_idx + 1: st.exit_idx + 1]:
                    if (c.ts + tf_s) > exit_epoch:
                        break
                    mark = _opt_closes(sym).get(c.last1m_ts)
                    if mark is None:
                        diag["stale_marks"] += 1
                        mark = last_mark
                    last_mark = mark
                    open_mtm = ((mark - entry_px) if action == "BUY"
                                else (entry_px - mark)) * qty
                    if hsym is not None:
                        hmark = _opt_closes(hsym).get(c.last1m_ts)
                        if hmark is None:
                            diag["stale_marks"] += 1
                            hmark = h_last_mark
                        h_last_mark = hmark
                        open_mtm += (hmark - hentry_px) * qty
                    day_mtm = day_realized + open_mtm
                    cut = None
                    if max_lm > 0 and (month_net + day_net + open_mtm) <= -max_lm:
                        # ── GC_MONTH_CAP ── outermost guard, checked first
                        cut = "MAX_LOSS_MONTH"
                        diag["month_loss_halts"] += 1
                        halted = True
                        month_halted = True
                    elif max_p > 0 and day_mtm >= max_p:
                        cut = "MAX_PROFIT_DAY"
                        diag["max_profit_halts"] += 1
                        halted = True
                    elif max_l > 0 and day_mtm <= -max_l:
                        cut = "MAX_LOSS_DAY"
                        diag["max_loss_halts"] += 1
                        halted = True
                    elif max_pt > 0 and open_mtm >= max_pt:
                        cut = "MAX_PROFIT_TRADE"
                        diag["trade_profit_cuts"] += 1
                    elif max_lt > 0 and open_mtm <= -max_lt:
                        cut = "MAX_LOSS_TRADE"
                        diag["trade_loss_cuts"] += 1
                    if cut:
                        exit_ts, exit_px = c.last1m_ts, mark
                        hexit_px = h_last_mark
                        exit_reason = cut
                        break

            if exit_px is None:
                exit_px = _fill_at(sym, exit_ts, allow_stale=True)
            if exit_px is None:
                # no print at or before the exit minute — degenerate day;
                # fail-closed: scratch the fill at entry (P&L = -charges)
                exit_px = entry_px
                exit_reason = (exit_reason or "EOD") + "_NOFILL"

            if exit_reason == "SL":
                diag["sl_exits"] += 1
            elif exit_reason == "EOD":
                diag["eod_exits"] += 1

            _emit(opt_side=opt_side, action=action, symbol=sym,
                  strike=m.get("strike"), expiry=m.get("expiry"),
                  entry_ts=st.entry_ts, entry_price=entry_px,
                  sl_spot=st.sl_level,
                  exit_ts=exit_ts, exit_price=exit_px,
                  exit_reason=exit_reason, tag=tag)
            day_realized += trades[-1].pnl
            day_net += trades[-1].net_pnl   # ── GC_MONTH_CAP ──
            # ── GC_HEDGE ── the bought leg exits at the sold leg's exit
            # minute at its own price; no own SL/TP by design (it follows).
            if hsym is not None:
                if hexit_px is None:
                    hexit_px = _fill_at(hsym, exit_ts, allow_stale=True)
                if hexit_px is None:
                    hexit_px = hentry_px    # scratch, mirrors sold-leg rule
                _emit(opt_side=opt_side, action="BUY", symbol=hsym,
                      strike=hm.get("strike"), expiry=hm.get("expiry"),
                      entry_ts=st.entry_ts, entry_price=hentry_px,
                      sl_spot=st.sl_level,
                      exit_ts=exit_ts, exit_price=hexit_px,
                      exit_reason=exit_reason, tag=tag + "·H")
                diag["hedge_entries"] += 1
                day_realized += trades[-1].pnl
                day_net += trades[-1].net_pnl   # ── GC_MONTH_CAP ──
            traded_day = True
            # D8 realized-only breach: a same-candle SL (or a large closed
            # loss) can cross the cap with NOTHING open — nothing to force-
            # exit, but the day still halts (no further entries).
            if not halted:
                if max_lm > 0 and (month_net + day_net) <= -max_lm:
                    # ── GC_MONTH_CAP ── realized-only breach (same-candle
                    # SL / closed losses) with nothing open: halt month.
                    diag["month_loss_halts"] += 1
                    halted = True
                    month_halted = True
                elif max_p > 0 and day_realized >= max_p:
                    diag["max_profit_halts"] += 1
                    halted = True
                elif max_l > 0 and day_realized <= -max_l:
                    diag["max_loss_halts"] += 1
                    halted = True

        if traded_day:
            diag["days_traded"] += 1
        month_net += day_net   # ── GC_MONTH_CAP ── completed day → accumulator

    conn.close()
    try:
        src.close()
    except Exception:
        pass

    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying}"
        f"{' (stock, lot ' + str(lot_size) + ')' if is_stock else ''} "
        f"{date_from}→{date_to}: "
        f"{diag['days_traded']}/{diag['days_total']} days traded, "
        f"{len(trades)} trades ({diag['flip_entries']} flips, "
        f"{diag['same_candle_sl']} same-candle SL), net {summary['net_pnl']}, "
        f"tf {tf}m mode {mode} sig {cfg['signal_mode']}, "
        f"exits SL {diag['sl_exits']} / EOD {diag['eod_exits']} / "
        f"dayCap +{diag['max_profit_halts']}/-{diag['max_loss_halts']} "
        f"(dropped {diag['halt_dropped_trades']}), "
        f"cutoffBlocked {diag['cutoff_blocked_entries']}, "
        f"tradeCuts +{diag['trade_profit_cuts']}/-{diag['trade_loss_cuts']}, "
        f"monthHalts {diag['month_loss_halts']} "
        f"(daysSkipped {diag['days_month_halted']}), "
        f"liqGate {diag['liq_gate_entries']}, "
        f"hedges {diag['hedge_entries']} "
        f"(fb {diag['hedge_cheapest_fb']}, skip {diag['hedge_skip_entries']}), "
        f"skips: c1Range {diag['days_c1_range_skip']}"
        f"+noRef {diag['days_c1_range_no_ref']} / "
        f"noBreakout {diag['days_no_breakout']} / "
        f"armedNoRetrace {diag['days_armed_no_retrace']} / "
        f"noStrike {diag['no_strike_entries']} / "
        f"uncovered {diag['days_uncovered']}, "
        f"SL-fallback {diag['sl_fallback_entries']} "
        f"(capped {diag['sl_cap_fallbacks']}), "
        f"staleFills {diag['stale_exit_fills']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}