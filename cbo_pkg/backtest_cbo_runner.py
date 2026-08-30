# backend/app/backtest/cbo/backtest_cbo_runner.py
#
# ── CBO_V1 RUNNER ── maps cbo_v1_engine's previous-candle breakout signals
# onto NIFTY/BANKNIFTY weekly option legs, BUYING or SELLING.
#
# Fence: CBO_V1_INIT_20260829
#
# SPEC OF RECORD (WhatsApp, 2026-08-29), decisions D1-D8 locked before code:
#   D1  intrabar detection on 1m sub-bars; fill at the NEXT 1m open
#   D2  contract chosen by the SCALP_V1 selection loop under a configurable
#       premium band (option_premium.min / .max)
#   D3  SL = the previous tf candle's opposite extreme (a SPOT level);
#       TP = an OPTION premium move, absolute rupees or % of entry.
#       Both hit inside one minute -> SL WINS.
#   D4  leg_action BUY | SELL. SELL expresses the SAME directional view with
#       the OPPOSITE contract (VET convention): UP -> short PE, DOWN -> short CE
#   D5  daily MTM caps on realised + OPEN P&L; a breach FLATTENS immediately
#       and halts new entries for the rest of that day
#   D6  one position at a time; configurable max trades per day
#   D7  session start / session end / EOD square-off all configurable
#   D8  an ambiguous outside sub-bar (both levels breached in one minute)
#       is taken PESSIMISTICALLY: the stop level was touched in the same
#       minute as the entry level, so the trade is entered and immediately
#       booked as a stop-out at that minute's ADVERSE extreme.
#
# ── WHY THE DIAGNOSTICS ARE HEAVY ───────────────────────────────────────
# This rule fires ~50 times a day on a driftless random walk, so a run
# produces tens of thousands of trades and any single artifact can dominate
# net P&L while staying invisible in a summary line. Three counters exist
# specifically to falsify the run rather than to describe it:
#
#   ambiguous_pnl_gross   total P&L of D8 forced stop-outs. If this is a
#                         large share of net, the result is an artifact of
#                         a tie-break convention, not of the strategy.
#   eod_pnl_gross         total P&L of EOD square-offs. SCALP_V5's parity
#                         break hid here: 267 EOD trades carried 100% of
#                         net P&L. Always check this before believing a run.
#   mtm_cap_pnl_gross     P&L booked by forced MTM-cap flattens. A cap that
#                         "improves" results by truncating the loss tail is
#                         curve-fitting a risk control, not finding an edge.
#
# ── PARITY NOTES ─────────────────────────────────────────────────────────
#   P1  EOD square-off time MUST equal the live cron's. It is config, not a
#       constant, so backtest and live read the same value.
#   P2  Holiday awareness via app.utils.market_hours.is_trading_day — the
#       single choke point (the fleet-wide calendar-blindness bug).
#   P3  No cross-day state: the reference resets daily, so unlike PST/TMA/
#       SCALP there is NO warmup-seeding requirement and no cold-start gap.
#   P4  Fills use a contract's OWN 1m bars. A minute with no print is a
#       stale mark, counted; it never silently becomes a zero.

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

try:
    from app.backtest.cbo.cbo_v1_engine import CboBar, cbo_signals, UP
except ImportError:                                        # standalone tests
    from cbo_v1_engine import CboBar, cbo_signals, UP  # type: ignore

IST_OFFSET = 5 * 3600 + 30 * 60
GRID_ANCHOR_MIN = 9 * 60 + 15          # NSE index bar grid opens 09:15

# Index lot sizes are CONSTANTS by fleet convention (see lot_sizes.py: the
# module records OPTIDX lots for eyeballing but deliberately does not wire
# them, because every sealed backtest is locked against these numbers).
INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}

DEFAULTS: dict = {
    # ── signal (engine) ──
    "timeframe_minutes": 5,
    "trigger_source": "high",          # high (spec) | close (strict)
    "both_side_policy": "pessimistic",  # D8 | skip | up | down
    "breakout_buffer_pts": 0.0,
    "min_ref_range_pts": 0.0,
    "require_full_ref": False,
    "direction": "BOTH",               # BOTH | UP | DOWN

    # ── contract ──
    "leg_action": "BUY",               # BUY | SELL
    "option_premium": {"min": 100.0, "max": 200.0},
    "lots": 1,
    "lot_size": 0,                     # 0 = auto

    # ── exits ──
    "target_mode": "abs",              # abs (premium rupees) | pct (of entry)
    "target_value": 10.0,
    "sl_premium_pct": 0.0,             # 0 = spot-reference stop only

    # ── session (D7) ──
    "session_start": "09:20",
    "session_end": "15:00",
    "eod_square_off": "15:15",

    # ── risk (D5, D6) ──
    "max_trades_per_day": 0,           # 0 = unlimited
    "mtm_loss_cap": 0.0,               # rupees, positive; 0 = off
    "mtm_profit_cap": 0.0,
    "mtm_include_open": True,
    "cooldown_minutes": 0,

    # ── filters ──
    "skip_expiry_day": False,
    "atm_skew_filter": {
        "enabled": False,              # the friend's "ATM CE > ATM PE"
        "min_diff_pts": 0.0,
        "invert": False,
        "parity_adjust": False,        # strip the strike-grid geometry
        "carry_pts": 6.5,              # MEASURED on this corpus, not fitted
    },
}


@dataclass
class CBOTrade:
    """Attribute surface matches ICTrade so backtest_repo.persist_run works
    unchanged. persist_run reads t.symbol / t.max_adverse / t.ambiguous_fill
    by ATTRIBUTE, so this must stay an OBJECT — a dict dies after the whole
    run completes, the most expensive possible place to fail."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                     # BUY | SELL (the LEG's action)
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]         # SL | TP | EOD | MTM_CAP | AMBIGUOUS | SESSION
    qty: int
    condition: str
    ambiguous_fill: bool = False
    pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)
    synthetic: bool = field(default=False)
    synth_kind: Optional[str] = field(default=None)


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _hhmm(s: str, fallback: int) -> int:
    """'HH:MM' -> minutes since midnight IST. Malformed input falls back
    LOUDLY-BY-DEFAULT rather than silently shifting the session."""
    try:
        h, m = str(s).split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 24 * 60 else fallback
    except (ValueError, AttributeError):
        return fallback


def _day_start_epoch(d: date) -> int:
    """Epoch of 00:00 IST for `d`, matching the corpus's day bucketing."""
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET


def _merge_cfg(override: Optional[dict]) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in DEFAULTS.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v

    # ── normalise, so a bad UI value can never silently change semantics ──
    cfg["leg_action"] = "SELL" if str(cfg["leg_action"]).upper() == "SELL" else "BUY"
    cfg["direction"] = str(cfg["direction"]).upper()
    if cfg["direction"] not in ("BOTH", "UP", "DOWN"):
        cfg["direction"] = "BOTH"
    cfg["target_mode"] = "pct" if str(cfg["target_mode"]).lower() == "pct" else "abs"
    # STORE the lowered value, do not merely validate it. Validating a
    # lowered copy while keeping the original meant "SKIP" from a UI select
    # passed this check and then raised ValueError inside the engine — after
    # the corpus was loaded and the run was underway.
    _tsrc = str(cfg["trigger_source"]).lower()
    cfg["trigger_source"] = _tsrc if _tsrc in ("high", "close") else "high"
    _pol = str(cfg["both_side_policy"]).lower()
    cfg["both_side_policy"] = _pol if _pol in (
        "pessimistic", "skip", "up", "down") else "pessimistic"
    for k in ("timeframe_minutes", "lots", "lot_size", "max_trades_per_day",
              "cooldown_minutes"):
        try:
            cfg[k] = max(0, int(cfg[k] or 0))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    cfg["timeframe_minutes"] = cfg["timeframe_minutes"] or 5
    cfg["lots"] = cfg["lots"] or 1
    for k in ("breakout_buffer_pts", "min_ref_range_pts", "target_value",
              "sl_premium_pct", "mtm_loss_cap", "mtm_profit_cap"):
        try:
            cfg[k] = abs(float(cfg[k] or 0.0))     # tolerate "-50000" input
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    return cfg


# ─────────────────────────────────────────────────────────────────────────
#  ATM SKEW GATE (the friend's "ATM CE should be greater than ATM PE")
# ─────────────────────────────────────────────────────────────────────────
def skew_ok(*, ce: Optional[float], pe: Optional[float],
            spot: Optional[float], strike: Optional[float],
            direction: str, cfg_skew: dict) -> Tuple[bool, Optional[float]]:
    """Return (allowed, measured_diff).

    RAW mode is the rule exactly as specified: an UP signal needs the ATM CE
    dearer than the ATM PE. Put-call parity makes that nearly equivalent to
    `spot > strike`, i.e. `spot mod strike_step < step/2` — a property of
    where the strike grid happens to fall, not of the market's direction.

    PARITY mode removes that geometry:
        C - P = S - K·e^(-rT)   =>   (C - P) - (S - K) ≈ K·rT ≡ carry
    so the residual (C - P) - (S - K) - carry is centred on zero and is what
    is left after the strike-grid artifact is subtracted. carry_pts is
    MEASURED on this corpus (SCALP_V1 found 6.57), never fitted per run.

    A paired run differs from its partner in ONE flag, so raw-vs-parity and
    normal-vs-invert are each a clean falsification test.

    FAIL-CLOSED: anything unmeasurable BLOCKS. A blocked entry costs one
    trade; an unfiltered one costs whatever the filter existed to prevent.
    """
    if not bool(cfg_skew.get("enabled", False)):
        return True, None
    if ce is None or pe is None:
        return False, None
    diff = float(ce) - float(pe)
    if bool(cfg_skew.get("parity_adjust", False)):
        if spot is None or strike is None:
            return False, None
        try:
            carry = float(cfg_skew.get("carry_pts", 6.5) or 0.0)
        except (TypeError, ValueError):
            carry = 6.5
        diff = diff - (float(spot) - float(strike)) - carry
    signed = diff if direction == UP else -diff
    if bool(cfg_skew.get("invert", False)):
        signed = -signed
    try:
        min_diff = float(cfg_skew.get("min_diff_pts", 0.0) or 0.0)
    except (TypeError, ValueError):
        min_diff = 0.0
    # STRICTLY greater, matching SCALP_V1's `if _diff <= skew_min: continue`.
    # With min_diff_pts = 0 this makes "exactly no residual richness" a FAIL
    # rather than a pass, which is the whole point of the parity mode: a pair
    # that is dear only from strike geometry has residual 0 and must not
    # qualify as a directional signal.
    return (signed > min_diff), round(diff, 2)


# ─────────────────────────────────────────────────────────────────────────
#  EXIT RESOLUTION
# ─────────────────────────────────────────────────────────────────────────
def resolve_exit(*, is_sell: bool, entry_px: float, tp_px: float,
                 spot_stop: float, direction: str,
                 opt_bar, spot_bar) -> Optional[Tuple[str, float]]:
    """Decide whether this minute closes the trade, and at what price.

    TP is an OPTION limit: a long fills AT tp_px once the option's high
    reaches it; a short fills AT tp_px once the option's low reaches it.
    Limit orders fill at their price, so no slippage is assumed there.

    SL is a SPOT trigger driving a MARKET exit on the option, so the fill is
    the option's CLOSE for that minute — not its favourable extreme. The
    spot level is breached when spot trades at-or-through it: an UP trade
    stops on spot low <= stop, a DOWN trade on spot high >= stop.

    BOTH IN ONE MINUTE -> SL WINS (D3). At 1m resolution the order is
    unknowable, and assuming the target came first would flatter every
    result by exactly the cases that matter most.
    """
    sl_hit = (spot_bar.low <= spot_stop) if direction == UP \
        else (spot_bar.high >= spot_stop)
    tp_hit = (opt_bar.low <= tp_px) if is_sell else (opt_bar.high >= tp_px)
    if sl_hit:
        return "SL", float(opt_bar.close)
    if tp_hit:
        return "TP", float(tp_px)
    return None


def leg_side(direction: str, is_sell: bool) -> str:
    """VET convention: SELL expresses the SAME directional view with the
    OPPOSITE contract. UP -> long CE, or short PE. DOWN -> long PE, or
    short CE. Directional exposure is identical; only the contract flips."""
    if direction == UP:
        return "PE" if is_sell else "CE"
    return "CE" if is_sell else "PE"


def target_price(entry_px: float, *, is_sell: bool, mode: str,
                 value: float) -> float:
    """TP in OPTION premium. 'pct' is a percentage of the entry premium —
    for a short, of the premium COLLECTED (D3b). Floored at 0.05 so a short
    can never target a negative premium."""
    delta = (entry_px * value / 100.0) if mode == "pct" else value
    return max(0.05, entry_px - delta) if is_sell else entry_px + delta


def mtm_of_open(pos: dict, mark: Optional[float]) -> float:
    """Open-position MTM in rupees at `mark`. A missing mark carries the last
    known one — never zero, which would read as a flat position and could
    release an MTM cap that should have fired."""
    if mark is None:
        mark = pos["last_mark"]
    d = (pos["entry_px"] - mark) if pos["is_sell"] else (mark - pos["entry_px"])
    return d * pos["qty"]


# ─────────────────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────────────────
def run_cbo_backtest(
    *,
    db_path: str,
    strategy_id: str,                  # "CBO_V1"
    underlying: str,
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _impl(db_path=db_path, strategy_id=strategy_id,
                         underlying=underlying, date_from=date_from,
                         date_to=date_to, config_override=config_override,
                         progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _impl(db_path=db_path, strategy_id=strategy_id,
                     underlying=underlying, date_from=date_from,
                     date_to=date_to, config_override=config_override,
                     progress_cb=progress_cb, cancel_cb=cancel_cb)


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts)
    from app.backtest.charges.charges_model import (
        charges_for_long_trade, charges_for_short_trade)
    from app.backtest.util.lot_sizes import resolve_lot
    from app.utils.market_hours import is_trading_day
    from app.event_bus.audit_logger import write_audit_log

    cfg = _merge_cfg(config_override)
    is_sell = cfg["leg_action"] == "SELL"
    tf = cfg["timeframe_minutes"]

    # Index lots are deliberately CONSTANTS, not scrip-master lookups: every
    # sealed result in the fleet is locked against 65 / 35, and sourcing them
    # live would silently restate history the next time NSE moves a lot.
    index_lot = INDEX_LOTS.get(underlying.upper())
    if index_lot is None:
        return {"run_id": None, "aborted": True,
                "reason": (f"CBO_V1 is index-only; no lot constant for "
                           f"{underlying}. Add it to INDEX_LOTS deliberately."),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    lot_size, lot_source = resolve_lot(
        underlying=underlying, is_stock=False, cfg_lot=cfg["lot_size"],
        index_lot=index_lot, db_path=db_path)
    if lot_size is None:
        return {"run_id": None, "aborted": True,
                "reason": f"no lot size for {underlying}",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
    qty = cfg["lots"] * lot_size

    start_min = _hhmm(cfg["session_start"], 9 * 60 + 20)
    end_min = _hhmm(cfg["session_end"], 15 * 60)
    eod_min = _hhmm(cfg["eod_square_off"], 15 * 60 + 15)
    if eod_min <= end_min:
        # An EOD square-off at-or-before the entry cutoff would flatten every
        # trade the instant it opened. Refuse rather than silently produce a
        # run whose every row is a scratch.
        return {"run_id": None, "aborted": True,
                "reason": (f"eod_square_off {cfg['eod_square_off']} must be "
                           f"AFTER session_end {cfg['session_end']}"),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    src = CandleSource(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    days: List[date] = []
    d = date_from
    while d <= date_to:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)

    trades: List[CBOTrade] = []
    diag = {
        "days_total": len(days), "days_traded": 0, "days_skipped_expiry": 0,
        "days_uncovered": 0, "days_no_spot": 0,
        "signals_raw": 0, "signals_ambiguous": 0,
        "entries": 0,
        "blocked_direction": 0, "blocked_session": 0, "blocked_in_trade": 0,
        "blocked_day_cap": 0, "blocked_cooldown": 0, "blocked_mtm_halt": 0,
        "blocked_no_selection": 0, "blocked_no_fill": 0, "blocked_skew": 0,
        "sl_exits": 0, "tp_exits": 0, "eod_exits": 0,
        "mtm_cap_exits": 0, "ambiguous_exits": 0,
        "mtm_loss_cap_days": 0, "mtm_profit_cap_days": 0,
        "stale_marks": 0,
        # ── falsification counters: see the module header ──
        "ambiguous_pnl_gross": 0.0, "eod_pnl_gross": 0.0,
        "mtm_cap_pnl_gross": 0.0, "tp_pnl_gross": 0.0, "sl_pnl_gross": 0.0,
        "underlying": underlying, "lot_size": lot_size,
        "lot_source": lot_source, "qty": qty,
        "leg_action": cfg["leg_action"], "timeframe_minutes": tf,
        "corpus_db": str(db_path).rsplit("/", 1)[-1],
    }

    def spot_bars_for(ds: int) -> List[CboBar]:
        return [CboBar(r["ts"], r["open"], r["high"], r["low"], r["close"])
                for r in conn.execute(
                    """SELECT ts, open, high, low, close
                       FROM backtest_candles_1m
                       WHERE underlying=? AND instrument_type='SPOT'
                         AND ts>=? AND ts<? ORDER BY ts""",
                    (underlying, ds, ds + 86400))]

    for i, day in enumerate(days):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": day.isoformat(), "i": i, "n": len(days),
                         "trades": len(trades)})

        ds = _day_start_epoch(day)
        spot = spot_bars_for(ds)
        if not spot:
            diag["days_no_spot"] += 1
            continue

        src.preload_day(underlying, ds)
        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=ds,
            cfg={"option_premium": cfg["option_premium"],
                 "trade_side_mode": "BOTH"},
            strategy_id=strategy_id, scope_to_expected_expiry=True)
        if not timeline.get("covered"):
            diag["days_uncovered"] += 1
            continue
        if cfg["skip_expiry_day"]:
            exp = timeline.get("expected_expiry")
            if exp and date.fromisoformat(exp) == day:
                diag["days_skipped_expiry"] += 1
                continue

        anchor = ds + GRID_ANCHOR_MIN * 60
        engine_policy = ("up" if cfg["both_side_policy"] == "pessimistic"
                         else cfg["both_side_policy"])
        # D8: "pessimistic" needs the engine to SURFACE the ambiguous bar
        # rather than drop it, so it runs the engine in a forced-side mode
        # and the runner books the forced stop-out. Side choice is fixed and
        # arbitrary; under a forced stop it changes only which contract eats
        # the loss, never whether one is booked.
        sigs = cbo_signals(
            spot, anchor_ts=anchor, tf_minutes=tf,
            trigger_source=cfg["trigger_source"],
            both_side_policy=engine_policy,
            breakout_buffer_pts=cfg["breakout_buffer_pts"],
            min_ref_range_pts=cfg["min_ref_range_pts"],
            require_full_ref=cfg["require_full_ref"])
        diag["signals_raw"] += len(sigs)

        if cfg["both_side_policy"] == "pessimistic":
            # An ambiguous bar is one where the "both" policy emits TWO
            # signals at the same trigger_ts. Computed ONCE per day: an
            # inline recount here would be quadratic in the day's signals
            # and this rule fires ~50 times a day over ~1,600 days.
            seen: Dict[int, int] = {}
            for s2 in cbo_signals(
                    spot, anchor_ts=anchor, tf_minutes=tf,
                    trigger_source=cfg["trigger_source"],
                    both_side_policy="both",
                    breakout_buffer_pts=cfg["breakout_buffer_pts"],
                    min_ref_range_pts=cfg["min_ref_range_pts"],
                    require_full_ref=cfg["require_full_ref"]):
                seen[s2.trigger_ts] = seen.get(s2.trigger_ts, 0) + 1
            amb = {t for t, n in seen.items() if n > 1}
        else:
            amb = set()

        opt_cache: Dict[str, Dict[int, object]] = {}

        def opt_bars(sym: str) -> Dict[int, object]:
            if sym not in opt_cache:
                opt_cache[sym] = {c.ts: c for c in
                                  src.candles_1m_for_symbol_day(sym, ds)}
            return opt_cache[sym]

        realised = 0.0
        halted = False
        n_today = 0
        cooldown_until = -1
        pos: Optional[dict] = None
        day_had_trade = False

        sig_by_ts: Dict[int, list] = {}
        for s in sigs:
            sig_by_ts.setdefault(s.fill_ts, []).append(s)

        def close_pos(ts: int, px: float, reason: str) -> None:
            nonlocal pos, realised
            fn = charges_for_short_trade if pos["is_sell"] else charges_for_long_trade
            gross = ((pos["entry_px"] - px) if pos["is_sell"]
                     else (px - pos["entry_px"])) * pos["qty"]
            # ChargesResult exposes total_charges (NOT .total) — an attribute
            # typo here would book every trade at zero cost and only surface
            # as an impossibly good result.
            ch = fn(entry_price=pos["entry_px"], exit_price=px,
                    qty=pos["qty"]).total_charges
            net = gross - ch
            t = pos["trade"]
            t.exit_ts, t.exit_price, t.exit_reason = ts, round(px, 2), reason
            t.pnl = t.gross = round(gross, 2)
            t.charges = round(ch, 2)
            t.net_pnl = t.net = round(net, 2)
            t.max_adverse = round(pos["mae"], 2)
            t.max_favorable = round(pos["mfe"], 2)
            realised += net
            key = {"SL": "sl_pnl_gross", "TP": "tp_pnl_gross",
                   "EOD": "eod_pnl_gross", "MTM_CAP": "mtm_cap_pnl_gross",
                   "AMBIGUOUS": "ambiguous_pnl_gross"}.get(reason)
            if key:
                diag[key] += round(net, 2)
            diag[{"SL": "sl_exits", "TP": "tp_exits", "EOD": "eod_exits",
                  "MTM_CAP": "mtm_cap_exits",
                  "AMBIGUOUS": "ambiguous_exits"}.get(reason, "eod_exits")] += 1
            pos = None

        for bar in spot:
            minute = (bar.ts - ds) // 60
            if minute < GRID_ANCHOR_MIN:
                continue

            # ── 1. EOD square-off (P1: same clock as the live cron) ──
            if pos is not None and minute >= eod_min:
                ob = opt_bars(pos["symbol"]).get(bar.ts)
                close_pos(bar.ts, float(ob.close) if ob else pos["last_mark"],
                          "EOD")
            if minute >= eod_min:
                continue

            # ── 2. mark the open position and run the exit ladder ──
            if pos is not None:
                ob = opt_bars(pos["symbol"]).get(bar.ts)
                if ob is None:
                    diag["stale_marks"] += 1
                else:
                    pos["last_mark"] = float(ob.close)
                    m = mtm_of_open(pos, float(ob.close))
                    pos["mae"] = min(pos["mae"], m)
                    pos["mfe"] = max(pos["mfe"], m)
                    ex = resolve_exit(
                        is_sell=pos["is_sell"], entry_px=pos["entry_px"],
                        tp_px=pos["tp_px"], spot_stop=pos["spot_stop"],
                        direction=pos["dir"], opt_bar=ob, spot_bar=bar)
                    if ex is not None:
                        close_pos(bar.ts, ex[1], ex[0])
                        cooldown_until = bar.ts + cfg["cooldown_minutes"] * 60

            # ── 3. daily MTM caps on realised + OPEN (D5) ──
            if not halted and (cfg["mtm_loss_cap"] > 0 or cfg["mtm_profit_cap"] > 0):
                live = realised + (mtm_of_open(pos, pos["last_mark"])
                                   if (pos is not None and cfg["mtm_include_open"])
                                   else 0.0)
                hit_loss = cfg["mtm_loss_cap"] > 0 and live <= -cfg["mtm_loss_cap"]
                hit_prof = cfg["mtm_profit_cap"] > 0 and live >= cfg["mtm_profit_cap"]
                if hit_loss or hit_prof:
                    halted = True
                    diag["mtm_loss_cap_days" if hit_loss
                         else "mtm_profit_cap_days"] += 1
                    if pos is not None:
                        ob = opt_bars(pos["symbol"]).get(bar.ts)
                        close_pos(bar.ts,
                                  float(ob.close) if ob else pos["last_mark"],
                                  "MTM_CAP")

            # ── 4. entries ──
            for s in sig_by_ts.get(bar.ts, []):
                if halted:
                    diag["blocked_mtm_halt"] += 1
                    continue
                if cfg["direction"] != "BOTH" and s.direction != cfg["direction"]:
                    diag["blocked_direction"] += 1
                    continue
                sig_min = (s.trigger_ts - ds) // 60
                if not (start_min <= sig_min < end_min):
                    diag["blocked_session"] += 1
                    continue
                if pos is not None:
                    diag["blocked_in_trade"] += 1
                    continue
                if cfg["max_trades_per_day"] and n_today >= cfg["max_trades_per_day"]:
                    diag["blocked_day_cap"] += 1
                    continue
                if bar.ts < cooldown_until:
                    diag["blocked_cooldown"] += 1
                    continue

                want = leg_side(s.direction, is_sell)
                snap = active_snapshot_for_ts(timeline, s.trigger_ts)
                cands = [o for o in snap if o["type"] == want]
                if not cands:
                    diag["blocked_no_selection"] += 1
                    continue
                pick = cands[0]

                sk = cfg["atm_skew_filter"]
                if sk.get("enabled"):
                    strike = float(pick["strike"])
                    ce = next((src.option_premium_at(o["tradingsymbol"],
                                                     s.trigger_ts)
                               for o in snap if o["type"] == "CE"
                               and float(o["strike"]) == strike), None)
                    pe = next((src.option_premium_at(o["tradingsymbol"],
                                                     s.trigger_ts)
                               for o in snap if o["type"] == "PE"
                               and float(o["strike"]) == strike), None)
                    ok, _ = skew_ok(ce=ce, pe=pe,
                                    spot=src.spot_at(underlying,
                                                     s.trigger_ts + 60),
                                    strike=strike, direction=s.direction,
                                    cfg_skew=sk)
                    if not ok:
                        diag["blocked_skew"] += 1
                        continue

                fb = opt_bars(pick["tradingsymbol"]).get(s.fill_ts)
                if fb is None or not fb.open:
                    diag["blocked_no_fill"] += 1
                    continue
                entry_px = float(fb.open)
                tp_px = target_price(entry_px, is_sell=is_sell,
                                     mode=cfg["target_mode"],
                                     value=cfg["target_value"])
                is_amb = s.trigger_ts in amb
                if is_amb:
                    diag["signals_ambiguous"] += 1

                t = CBOTrade(
                    tradingsymbol=pick["tradingsymbol"],
                    symbol=pick["tradingsymbol"],
                    instrument_type=want, strike=float(pick["strike"]),
                    expiry=pick["expiry"],
                    direction="SELL" if is_sell else "BUY",
                    entry_ts=s.fill_ts, entry_price=round(entry_px, 2),
                    sl=round(s.stop_level, 2), tp=round(tp_px, 2),
                    exit_ts=None, exit_price=None, exit_reason=None,
                    qty=qty,
                    condition=f"CBO·{s.direction}" + ("·AMB" if is_amb else ""),
                    ambiguous_fill=is_amb, ambiguous=is_amb)
                trades.append(t)
                diag["entries"] += 1
                n_today += 1
                day_had_trade = True
                pos = {"symbol": pick["tradingsymbol"], "trade": t,
                       "entry_px": entry_px, "tp_px": tp_px,
                       "spot_stop": s.stop_level, "dir": s.direction,
                       "is_sell": is_sell, "qty": qty,
                       "last_mark": entry_px, "mae": 0.0, "mfe": 0.0}

                if is_amb:
                    # D8: the stop level was touched in the SAME minute as
                    # the entry level, so under the D3 tie-break this trade
                    # is stopped out at entry. Booked at the fill bar's
                    # ADVERSE extreme — the pessimistic reading.
                    adverse = float(fb.high) if is_sell else float(fb.low)
                    close_pos(s.fill_ts, adverse, "AMBIGUOUS")
                    cooldown_until = s.fill_ts + cfg["cooldown_minutes"] * 60

        if pos is not None:
            # Ran out of prints before the EOD minute (a truncated session).
            ob = opt_bars(pos["symbol"]).get(spot[-1].ts)
            close_pos(spot[-1].ts,
                      float(ob.close) if ob else pos["last_mark"], "EOD")
        if day_had_trade:
            diag["days_traded"] += 1

    src.close()
    conn.close()
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying} {date_from}..{date_to}: "
        f"{summary['total_trades']} trades, net {summary['net_pnl']:,.0f}, "
        f"DD {summary['max_drawdown']:,.0f}, tf {diag['timeframe_minutes']}m "
        f"{diag['leg_action']}, exits SL {diag['sl_exits']} / "
        f"TP {diag['tp_exits']} / EOD {diag['eod_exits']} / "
        f"MTM {diag['mtm_cap_exits']} / AMB {diag['ambiguous_exits']}, "
        f"blocked: inTrade {diag['blocked_in_trade']} / "
        f"skew {diag['blocked_skew']} / dayCap {diag['blocked_day_cap']} / "
        f"halt {diag['blocked_mtm_halt']} / noSel {diag['blocked_no_selection']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}


def _summarize(trades: List[CBOTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_cbo"] = diag
        return s
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.exit_ts or 0, x.entry_ts or 0)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    nets = [t.net_pnl for t in closed]
    wins = sum(1 for n in nets if n > 0)
    net = sum(nets)
    # Share of net carried by each exit family. When one of these is near
    # 100%, the run is describing that mechanism, not the breakout rule.
    if abs(net) > 1e-9:
        for k in ("ambiguous", "eod", "mtm_cap", "tp", "sl"):
            diag[f"{k}_pnl_share_pct"] = round(
                100.0 * diag[f"{k}_pnl_gross"] / net, 1)
    return {
        "total_trades": len(closed), "wins": wins,
        "losses": sum(1 for n in nets if n < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),
        "ambiguous_fills": sum(1 for t in closed if t.ambiguous_fill),
        "diag_cbo": diag,
    }
