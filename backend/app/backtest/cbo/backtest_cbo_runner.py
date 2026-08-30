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

# ── CBO_SKEW_ATM_FIX_20260830 ── strike grid per index, for locating the
# TRUE ATM strike (nearest to spot). The skew rule is measured there.
STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100}

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
    # ── CBO_PREM_SL_20260830 ── premium stop, ADDITIVE to the spot stop
    # (tighter-wins). Replaces sl_premium_pct, which shipped unread.
    "sl_prem_mode": "off",             # off | abs (premium ₹) | pct (of entry)
    # ── CBO_D10_FILTERS_20260830 ──
    "tp_fill_through_pts": 0.0,        # ε: TP books only if traded THROUGH
    "vwap_filter": {"enabled": False, "min_pts": 0.0, "invert": False},
    "ema_gate": {"enabled": False, "period": 144, "slope_window": 10,
                 "min_slope": 0.0, "invert": False},
    "sl_prem_value": 0.0,

    # ── session (D7) ──
    "session_start": "09:20",
    "session_end": "15:00",
    "eod_square_off": "15:15",

    # ── risk (D5, D6) ──
    "max_trades_per_day": 0,           # 0 = unlimited
    "mtm_loss_cap": 0.0,               # rupees, positive; 0 = off
    "mtm_profit_cap": 0.0,
    "mtm_include_open": True,
    # ── CBO_MONTH_BREAKER_20260830 ── calendar-month circuit breakers.
    "monthly_loss_breaker": 0.0,       # ₹, 0=off: stand down for the month
    "monthly_profit_lock": 0.0,        # ₹, 0=off: lock a green month
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
    exit_reason: Optional[str]         # SL_SPOT | SL_PREM | TP | EOD | MTM_CAP | AMBIGUOUS
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
    # ── CBO_PREM_SL_20260830 ── store the lowered value (the "SKIP" lesson)
    _slm = str(cfg.get("sl_prem_mode", "off")).lower()
    cfg["sl_prem_mode"] = _slm if _slm in ("off", "abs", "pct") else "off"
    # STORE the lowered value, do not merely validate it. Validating a
    # lowered copy while keeping the original meant "SKIP" from a UI select
    # passed this check and then raised ValueError inside the engine — after
    # the corpus was loaded and the run was underway.
    _tsrc = str(cfg["trigger_source"]).lower()
    cfg["trigger_source"] = _tsrc if _tsrc in (
        "high", "close", "tf_close") else "high"   # ── CBO_TF_CLOSE_20260830 ──
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
              "tp_fill_through_pts",                 # CBO_D10_FILTERS_20260830
              "sl_prem_value", "mtm_loss_cap", "mtm_profit_cap",
              "monthly_loss_breaker", "monthly_profit_lock"):   # CBO_MONTH_BREAKER_20260830
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
                 opt_bar, spot_bar,
                 sl_prem_px: Optional[float] = None,
                 tp_eps: float = 0.0) -> Optional[Tuple[str, float]]:
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
    # ── CBO_D10_FILTERS_20260830 ── ε=0: a touch fills (today's model,
    # best case). ε>0: the bar must trade THROUGH the limit by ε — the
    # microstructure rule that a limit traded through almost certainly
    # filled, while a touch is a queue lottery. Fill price stays tp_px in
    # both cases: a limit never fills better than its price; ε changes
    # WHETHER, never AT WHAT PRICE.
    tp_hit = (opt_bar.low <= tp_px - tp_eps) if is_sell \
        else (opt_bar.high >= tp_px + tp_eps)
    # ── CBO_PREM_SL_20260830 ── premium stop: triggers when the option
    # trades at-or-through its level; fills AT the level (stop-trigger
    # convention, the mirror of TP-at-limit). Additive to the spot stop.
    prem_hit = False
    if sl_prem_px is not None:
        prem_hit = (opt_bar.high >= sl_prem_px) if is_sell \
            else (opt_bar.low <= sl_prem_px)
    if sl_hit and prem_hit:
        # both stops in one minute -> the WORSE fill (pessimistic). For a
        # long, worse = lower; for a short, worse = higher.
        spot_fill = float(opt_bar.close)
        worse = max(spot_fill, float(sl_prem_px)) if is_sell \
            else min(spot_fill, float(sl_prem_px))
        return ("SL_SPOT" if worse == spot_fill else "SL_PREM"), worse
    if sl_hit:
        return "SL_SPOT", float(opt_bar.close)
    if prem_hit:
        return "SL_PREM", float(sl_prem_px)
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


def sl_prem_price(entry_px: float, *, is_sell: bool, mode: str,
                  value: float) -> Optional[float]:
    """── CBO_PREM_SL_20260830 ── the premium level at which the stop
    triggers, or None when the stop is off. Mirrors target_price exactly:
    'pct' is % of ENTRY premium (of the premium COLLECTED on a short, D3b).
    A long stops BELOW entry; a short stops ABOVE (its loss direction).
    Floored at 0.05 on the long side — a stop at a negative premium can
    never trigger and would silently disable itself."""
    if mode == "off" or value <= 0:
        return None
    delta = (entry_px * value / 100.0) if mode == "pct" else value
    return (entry_px + delta) if is_sell else max(0.05, entry_px - delta)


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
        # ── CBO_SKEW_ATM_FIX_20260830 ── every kill path gets a counter:
        # signals_raw must equal entries + Σ blocked_* on every run.
        "blocked_skew_unmeasurable": 0, "blocked_after_eod": 0,
        # ── CBO_D10_FILTERS_20260830 ── verdict blocks vs data blocks,
        # separately, per gate — a silent day-killer must be impossible.
        "blocked_vwap": 0, "blocked_vwap_unmeasurable": 0,
        "blocked_ema": 0, "blocked_ema_unmeasurable": 0,
        "blocked_no_spot_bar": 0,
        "sl_exits": 0, "tp_exits": 0, "eod_exits": 0,
        "sl_spot_exits": 0, "sl_prem_exits": 0,           # CBO_PREM_SL_20260830
        "sl_spot_pnl_gross": 0.0, "sl_prem_pnl_gross": 0.0,
        "mtm_cap_exits": 0, "ambiguous_exits": 0,
        "mtm_loss_cap_days": 0, "mtm_profit_cap_days": 0,
        # ── CBO_MONTH_BREAKER_20260830 ──
        "months_loss_breaker_hit": 0, "months_profit_lock_hit": 0,
        "month_cap_exits": 0, "month_cap_pnl_gross": 0.0,
        "blocked_month_halt": 0,
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

    # ── CBO_MONTH_BREAKER_20260830 ── calendar-month accumulators. These
    # OUTLIVE the day loop on purpose: a halted month stays halted until the
    # month key changes, unlike the daily `halted` which resets every day.
    month_key = None
    month_realised = 0.0
    month_halted = False

    for i, day in enumerate(days):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            # ── CBO_PROGRESS_20260829 ── the status endpoint's _eta() reads
            # progress["day"]/["total_days"] as done/total counters and the
            # UI renders `day X/total · date`. Emitting the iso date under
            # "day" showed `day 2026-05-22/undefined` and killed the ETA
            # (total_days absent -> frac 0 -> eta None). Contract matches
            # VET/TMA/PST: day = 1-based index, total_days = count,
            # date = display string.
            progress_cb({"day": i + 1, "total_days": len(days),
                         "date": day.isoformat(), "trades": len(trades)})

        ds = _day_start_epoch(day)
        # ── CBO_MONTH_BREAKER_20260830 ── month rollover resets the
        # accumulator and re-arms the breaker.
        _mk = (day.year, day.month)
        if _mk != month_key:
            month_key = _mk
            month_realised = 0.0
            month_halted = False
        spot = spot_bars_for(ds)
        # ── CBO_D10_FILTERS_20260830 ── per-day SPOT indicator series,
        # computed once, keyed by bar ts (the value KNOWN at that bar's
        # close — a gate at trigger_ts reads its own bar, never a later
        # one). VWAP = SCALP_V1's session cumulative typical-price mean
        # ((H+L+C)/3, equal weight, day-reset). EMA = standard
        # alpha 2/(n+1) on closes; slope over slope_window bars; None
        # until warm (fail-closed at the gate, counted).
        vwap_at: Dict[int, float] = {}
        ema_slope_at: Dict[int, Optional[float]] = {}
        if cfg["vwap_filter"].get("enabled") or cfg["ema_gate"].get("enabled"):
            _pv = _n = 0.0
            _per = max(2, int(cfg["ema_gate"].get("period", 144) or 144))
            _win = max(1, int(cfg["ema_gate"].get("slope_window", 10) or 10))
            _al = 2.0 / (_per + 1.0)
            _ema = None
            _cnt = 0
            _hist: List[float] = []
            for _b in spot:
                if (_b.ts - ds) // 60 < GRID_ANCHOR_MIN:
                    continue                     # pre-open prints: no session
                _pv += (_b.high + _b.low + _b.close) / 3.0
                _n += 1.0
                vwap_at[_b.ts] = _pv / _n
                _ema = _b.close if _ema is None else \
                    _al * _b.close + (1.0 - _al) * _ema
                _cnt += 1
                _hist.append(_ema)
                if _cnt >= _per + _win:
                    ema_slope_at[_b.ts] = _ema - _hist[-1 - _win]
                else:
                    ema_slope_at[_b.ts] = None   # warming: unmeasurable
        spot_close_at = {b.ts: b.close for b in spot}
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

        # ── CBO_SKEW_ATM_FIX_20260830 ── (strike, side) -> tradingsymbol
        # for the WHOLE expected-expiry chain, band-independent. The skew
        # gate measures at the true ATM strike, whose contracts may be
        # nowhere near the premium band — so the band snapshot is the wrong
        # universe for this lookup, and using it was the bug: below-band ATM
        # premium made the band's CE and PE strike sets disjoint, the
        # opposite-leg lookup returned None, and fail-closed killed every
        # signal of every such day as blocked_skew.
        atm_sym: Dict[tuple, str] = {}
        if cfg["atm_skew_filter"].get("enabled"):
            _exp = timeline.get("expected_expiry")
            for _c in src.contracts_active_on_day(underlying, ds, expiry=_exp):
                atm_sym[(float(_c["strike"]), _c["instrument_type"])] = \
                    _c["tradingsymbol"]
        _step = STRIKE_STEPS.get(underlying.upper(), 50)

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
            nonlocal pos, realised, month_realised   # CBO_MONTH_BREAKER_20260830
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
            month_realised += net   # CBO_MONTH_BREAKER_20260830
            # ── CBO_PREM_SL_20260830 ── SL_SPOT / SL_PREM are attributed
            # separately (and also into the legacy sl_* aggregates so every
            # existing report keeps working).
            key = {"SL_SPOT": "sl_spot_pnl_gross", "SL_PREM": "sl_prem_pnl_gross",
                   "TP": "tp_pnl_gross",
                   "EOD": "eod_pnl_gross", "MTM_CAP": "mtm_cap_pnl_gross",
                   "MONTH_CAP": "month_cap_pnl_gross",   # CBO_MONTH_BREAKER_20260830
                   "AMBIGUOUS": "ambiguous_pnl_gross"}.get(reason)
            if key:
                diag[key] += round(net, 2)
            if reason in ("SL_SPOT", "SL_PREM"):
                diag["sl_pnl_gross"] += round(net, 2)
                diag["sl_exits"] += 1
            diag[{"SL_SPOT": "sl_spot_exits", "SL_PREM": "sl_prem_exits",
                  "TP": "tp_exits", "EOD": "eod_exits",
                  "MTM_CAP": "mtm_cap_exits",
                  "MONTH_CAP": "month_cap_exits",   # CBO_MONTH_BREAKER_20260830
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
                # ── CBO_SKEW_ATM_FIX_20260830 ── these signals can never
                # trade, but silence is not an option: the trace ledger
                # (signals_raw vs entries+blocked) must balance.
                diag["blocked_after_eod"] += len(sig_by_ts.get(bar.ts, []))
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
                        direction=pos["dir"], opt_bar=ob, spot_bar=bar,
                        sl_prem_px=pos["sl_prem_px"],
                        tp_eps=cfg["tp_fill_through_pts"])
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

            # ── 3b. CBO_MONTH_BREAKER_20260830 ── calendar-month breaker on
            # month-to-date realised + open MTM (same include_open rule as
            # the daily caps). A breach flattens NOW and stands the strategy
            # down until the month rolls — the worst month is bounded at
            # roughly −X plus one flatten's slippage, by construction.
            if not month_halted and (cfg["monthly_loss_breaker"] > 0
                                     or cfg["monthly_profit_lock"] > 0):
                _mlive = month_realised + (
                    mtm_of_open(pos, pos["last_mark"])
                    if (pos is not None and cfg["mtm_include_open"]) else 0.0)
                _mhl = (cfg["monthly_loss_breaker"] > 0
                        and _mlive <= -cfg["monthly_loss_breaker"])
                _mhp = (cfg["monthly_profit_lock"] > 0
                        and _mlive >= cfg["monthly_profit_lock"])
                if _mhl or _mhp:
                    month_halted = True
                    diag["months_loss_breaker_hit" if _mhl
                         else "months_profit_lock_hit"] += 1
                    if pos is not None:
                        ob = opt_bars(pos["symbol"]).get(bar.ts)
                        close_pos(bar.ts,
                                  float(ob.close) if ob else pos["last_mark"],
                                  "MONTH_CAP")

            # ── 4. entries ──
            for s in sig_by_ts.get(bar.ts, []):
                if month_halted:   # CBO_MONTH_BREAKER_20260830
                    diag["blocked_month_halt"] += 1
                    continue
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
                    # ── CBO_SKEW_ATM_FIX_20260830 ── the rule as written:
                    # ATM CE vs ATM PE, both at the strike nearest SPOT,
                    # symbols from the full expiry chain. Verdict blocks and
                    # data blocks are counted separately so an unmeasurable
                    # gate is visible in the diag, never a silent day-kill.
                    _spot = src.spot_at(underlying, s.trigger_ts + 60)
                    if _spot is None:
                        diag["blocked_skew_unmeasurable"] += 1
                        continue
                    _k = round(_spot / _step) * _step
                    ce = pe = None
                    _cs = atm_sym.get((float(_k), "CE"))
                    _ps = atm_sym.get((float(_k), "PE"))
                    if _cs:
                        ce = src.option_premium_at(_cs, s.trigger_ts)
                    if _ps:
                        pe = src.option_premium_at(_ps, s.trigger_ts)
                    if ce is None or pe is None:
                        diag["blocked_skew_unmeasurable"] += 1
                        continue
                    ok, _ = skew_ok(ce=ce, pe=pe, spot=_spot, strike=_k,
                                    direction=s.direction, cfg_skew=sk)
                    if not ok:
                        diag["blocked_skew"] += 1
                        continue

                # ── CBO_D10_FILTERS_20260830 ── VWAP filter (SCALP_V1
                # semantics on the SPOT): UP needs close >= VWAP + min_pts
                # at the trigger bar, DOWN mirrored; invert flips the
                # verdict only. Unmeasurable BLOCKS, counted separately.
                vf = cfg["vwap_filter"]
                if vf.get("enabled"):
                    _vw = vwap_at.get(s.trigger_ts)
                    _cl = spot_close_at.get(s.trigger_ts)
                    if _vw is None or _cl is None:
                        diag["blocked_vwap_unmeasurable"] += 1
                        continue
                    try:
                        _vmin = float(vf.get("min_pts", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        _vmin = 0.0
                    _vok = (_cl - _vw >= _vmin) if s.direction == UP \
                        else (_vw - _cl >= _vmin)
                    if bool(vf.get("invert", False)):
                        _vok = not _vok
                    if not _vok:
                        diag["blocked_vwap"] += 1
                        continue

                # ── CBO_D10_FILTERS_20260830 ── EMA slope gate (SCALP_V1
                # shape on SPOT closes): UP needs slope >= +min_slope, DOWN
                # <= -min_slope; invert flips; warmup/None BLOCKS, counted.
                eg = cfg["ema_gate"]
                if eg.get("enabled"):
                    _sl = ema_slope_at.get(s.trigger_ts)
                    if _sl is None:
                        diag["blocked_ema_unmeasurable"] += 1
                        continue
                    try:
                        _emin = float(eg.get("min_slope", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        _emin = 0.0
                    _eok = (_sl >= _emin) if s.direction == UP \
                        else (_sl <= -_emin)
                    if bool(eg.get("invert", False)):
                        _eok = not _eok
                    if not _eok:
                        diag["blocked_ema"] += 1
                        continue

                fb = opt_bars(pick["tradingsymbol"]).get(s.fill_ts)
                if fb is None or not fb.open:
                    diag["blocked_no_fill"] += 1
                    continue
                entry_px = float(fb.open)
                tp_px = target_price(entry_px, is_sell=is_sell,
                                     mode=cfg["target_mode"],
                                     value=cfg["target_value"])
                # ── CBO_PREM_SL_20260830 ── computed per trade from the
                # actual fill, same as the target.
                _slp = sl_prem_price(entry_px, is_sell=is_sell,
                                     mode=cfg["sl_prem_mode"],
                                     value=cfg["sl_prem_value"])
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
                       "sl_prem_px": _slp,
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

        # ── CBO_SKEW_ATM_FIX_20260830 ── a signal whose fill_ts minute
        # has no SPOT bar is never reached by the bar loop above (sig_by_ts
        # is keyed by fill_ts). Dense corpora make this rare; count it so it
        # can never be silent.
        _seen = {b.ts for b in spot}
        for _fts, _ss in sig_by_ts.items():
            if _fts not in _seen:
                diag["blocked_no_spot_bar"] += len(_ss)

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
        for k in ("ambiguous", "eod", "mtm_cap", "month_cap", "tp", "sl",   # CBO_MONTH_BREAKER_20260830
                  "sl_spot", "sl_prem"):                  # CBO_PREM_SL_20260830
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
