# backend/app/backtest/emc/backtest_emc_runner.py
#
# ── EMC_V1 RUNNER ── "Ember": NIFTY SPOT EMA(9/21) cross arms a weekly option
# picked by premium band; the option's own EMA(9/21) alignment buys it.
#
# Fence: EMC_V1_20261011
#
# Decision core (E1–E8) lives in emc_v1_engine.py (simulate_day). This
# module owns data access and bookkeeping only:
#   R1  Spot: 1m SPOT → spot_tf bars; the spot EMA pair is continuous across
#       sessions, warmed on `warmup_sessions` sessions before date_from, and
#       advanced on EVERY session with spot data — including sessions the
#       strategy does not trade (skip-expiry, DTE skip, no chain).
#   R2  Chain: the expected weekly expiry for the day (same calendar as every
#       other runner), contracts with any print that day.
#   R3  Option EMA warm-up: the selected contract's 1m prints from up to
#       `opt_warmup_sessions` earlier sessions (whatever the corpus holds for
#       that symbol — on the first session of a weekly cycle the new front
#       contract usually has none), resampled per session to opt_tf.
#       Fewer than opt_min_bars bars = not ready = keep waiting (counted).
#   R4  Charges: charges_for_long_trade on every round trip; a "losing trade"
#       for the daily cap is net of charges < 0.
#   R6  First-candle cross (EMC_V1_FIX1_20261011): the session's first spot
#       candle's cross is measured against YESTERDAY's last candle, so it is
#       made by the overnight gap. first_candle_cross=False drops it (counted
#       in gap_crosses_ignored); gap_crosses counts them either way.
#   R7  Carry overnight (EMC_CARRY_20261012, backtest only, default OFF): the
#       position open at the square-off is carried unless today is its
#       contract's expiry day; a carried position keeps its entry-day qty, is
#       managed on every later session — including sessions skipped for
#       entries (skip-expiry, DTE skip, no cross: entries blocked, exits live)
#       — and counts toward the losing-trade cap of the day it closes.
#       A session with no spot data cannot manage it (counted; carried on).
#       Open at the end of the run → closed at its last mark (RUN_END).
#   R5  Sizing: lots × lot size, then DTE × lots, then lot compounding — the
#       fleet order (DTE_LOT_MULT_20260911, LOT_COMP_20260924).
#
# ── FALSIFICATION TRIPWIRES (read the run diag before the P&L) ──────────
#   * setup_no_band vs setups — a band that rejects most crosses tests a
#     much sparser strategy than the chart suggests (STFC scar: 75%).
#   * entries_aligned_at_setup vs entries_after_wait — if almost every entry
#     is "already aligned", the option-EMA gate is decorative.
#   * xover_pnl_share_pct vs sl/tp — which exit actually carries the net.
#   * entries_after_warmup_wait / setups_expired_unready — weekly-cycle day
#     one has no option history in the corpus; the gate behaves differently.
#   * ce_net vs pe_net and the Sessions-to-Expiry breakdown.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.emc.emc_v1_engine import (
        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,
        simulate_day, SESSION_OPEN_MIN, SESSION_LAST_MIN)
except ImportError:                                        # standalone tests
    from emc_v1_engine import (  # type: ignore
        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,
        simulate_day, SESSION_OPEN_MIN, SESSION_LAST_MIN)

IST_OFFSET = 5 * 3600 + 30 * 60
INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}
MIN_PER_SESSION = 375

DEFAULTS: dict = {
    # ── signal ──
    "spot_tf": 5,                      # spot bar minutes
    "opt_tf": 5,                       # option bar minutes
    "ema_fast": 9,
    "ema_slow": 21,
    "warmup_sessions": 5,              # spot EMA warm-up before date_from
    "opt_warmup_sessions": 3,          # option history sessions loaded
    "opt_min_bars": 0,                 # 0 = ema_slow
    "opt_check": "intrabar",           # intrabar | bar_close
    "direction": "BOTH",               # BOTH | CE | PE

    # ── selection ──
    "sel_prem_min": 150.0,
    "sel_prem_max": 200.0,
    "sel_pick": "highest",             # highest | lowest

    # ── execution & exits ──
    "fill_mode": "next_open",          # next_open | close
    "sl_pts": 20.0,                    # premium points; 0 = off
    "tp_pts": 40.0,                    # premium points; 0 = off
    "trail_on": False,
    "trail_trigger_pts": 20.0,         # gain (pts) that arms the trail; 0 = from entry
    "trail_gap_pts": 15.0,             # trail distance below the peak
    "xover_exit": True,                # exit on the opposite spot cross

    # ── session & budgets ──
    "entry_cutoff": "14:30",
    "eod_square_off": "15:15",
    "max_trades_per_day": 0,           # 0 = unlimited
    "max_losses_per_day": 0,           # 0 = unlimited
    "lots": 1,
    "lot_size": 0,                     # 0 = index constant
    "skip_expiry_day": False,
    # ── EMC_V1_FIX1_20261011 ── a cross on the session's FIRST spot candle is
    # usually made by the overnight gap (yesterday's last candle → today's
    # first). True = count it (TradingView-continuous); False = it arms nothing.
    "first_candle_cross": True,
    # ── EMC_CARRY_20261012 ── BACKTEST ONLY. True = a position still open at
    # the square-off is carried to the next session (managed 15:15→15:29 and
    # from the next open: SL / TP on bars, gap through a level books the open,
    # opposite-cross exit). On its contract's EXPIRY day it exits at the
    # square-off as usual. Default False = the sealed intraday strategy.
    "carry_overnight": False,
    # ── EMC_SELL_20261012 ── BACKTEST ONLY. Leg action, VET V1's convention:
    # BUY = an up cross buys the CE, a down cross buys the PE (the sealed
    # strategy). SELL = an up cross SELLS the PE, a down cross SELLS the CE;
    # selection band / pick apply to the sold contract; the sold option's
    # own EMA fast must be BELOW slow (its premium falling) to trigger; SL is
    # sl_pts ABOVE the entry premium, TP tp_pts BELOW; the opposite spot
    # cross still exits. Optional protective wing (hedge) — VET's rule: a
    # long option, same type & expiry, the DEAREST one at or under
    # hedge_max_premium (real print first, model-priced fallback); no wing →
    # no trade. One row per position carrying the PAIR's P&L.
    "leg_action": "BUY",
    "hedge_enabled": False,
    "hedge_max_premium": 5.0,
    "hedge_synth_enabled": True,
    "hedge_skew_mult": 1.0,
    # ── EMC_V1_BLACKOUT_20261011 ── skip setups armed in [from, to) — HH:MM,
    # both blank = off. Matches on the arming candle's LAST minute (the "arm"
    # time in the trade condition), e.g. 09:30–10:00 skips arms 09:30…09:59.
    "arm_skip_from": "",
    "arm_skip_to": "",
}


@dataclass
class EMCTrade:
    """persist_run-compatible attribute surface (same shape as STFCTrade)."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                     # always BUY
    entry_ts: int
    entry_price: float
    sl: Optional[float]                # initial premium stop
    tp: Optional[float]                # premium target
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]         # SL | TSL | TP | XOVER | EOD
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
    entry_spot: Optional[float] = None
    hold_min: int = 0
    # ── EMC_SELL_20261012 ── the protective wing of a SELL, for transparency.
    # Deliberately NOT named hedge_symbol (backtest_repo switches on that name
    # and would store the wing as the primary row — VET's note).
    wing_symbol: Optional[str] = None
    wing_entry: Optional[float] = None
    wing_exit: Optional[float] = None


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _hhmm(s, fallback: int) -> int:
    try:
        h, m = str(s).strip().split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 24 * 60 else fallback
    except (ValueError, AttributeError):
        return fallback


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET


def _ist_date(ts: int) -> date:
    return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST_OFFSET)).date()


def _merge_cfg(override: Optional[dict]) -> dict:
    cfg = dict(DEFAULTS)
    for k, v in (override or {}).items():
        cfg[k] = v
    _d = str(cfg.get("direction", "BOTH")).upper()
    cfg["direction"] = _d if _d in ("BOTH", "CE", "PE") else "BOTH"
    cfg["opt_check"] = "bar_close" if str(cfg.get("opt_check", "intrabar")).lower() == "bar_close" else "intrabar"
    cfg["sel_pick"] = "lowest" if str(cfg.get("sel_pick", "highest")).lower() == "lowest" else "highest"
    cfg["fill_mode"] = "close" if str(cfg.get("fill_mode", "next_open")).lower() == "close" else "next_open"
    for k in ("spot_tf", "opt_tf", "ema_fast", "ema_slow", "warmup_sessions", "opt_warmup_sessions",
              "opt_min_bars", "max_trades_per_day", "max_losses_per_day", "lots", "lot_size"):
        try:
            cfg[k] = max(0, int(cfg[k] or 0))
        except (TypeError, ValueError):
            cfg[k] = int(DEFAULTS[k])
    for k in ("sel_prem_min", "sel_prem_max", "sl_pts", "tp_pts", "trail_trigger_pts", "trail_gap_pts"):
        try:
            cfg[k] = abs(float(cfg[k] or 0.0))
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    for k in ("trail_on", "xover_exit", "skip_expiry_day", "first_candle_cross", "carry_overnight",
              "hedge_enabled", "hedge_synth_enabled"):   # ── EMC_CARRY_20261012 ── ── EMC_SELL_20261012 ──   # ── EMC_V1_FIX1_20261011 ──
        v = cfg.get(k, DEFAULTS[k])
        cfg[k] = (str(v).strip().lower() in ("1", "true", "yes", "on")) if isinstance(v, str) else bool(v)
    for k in ("arm_skip_from", "arm_skip_to"):   # ── EMC_V1_BLACKOUT_20261011 ──
        cfg[k] = str(cfg.get(k) or "").strip()
    cfg["leg_action"] = "SELL" if str(cfg.get("leg_action", "BUY")).strip().upper() == "SELL" else "BUY"   # ── EMC_SELL_20261012 ──
    for k in ("hedge_max_premium", "hedge_skew_mult"):
        try:
            cfg[k] = abs(float(cfg.get(k) if cfg.get(k) is not None else DEFAULTS[k]))
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    cfg["hedge_skew_mult"] = cfg["hedge_skew_mult"] or 1.0
    cfg["lots"] = cfg["lots"] or 1
    cfg["spot_tf"] = cfg["spot_tf"] or 5
    cfg["opt_tf"] = cfg["opt_tf"] or 5
    cfg["ema_fast"] = cfg["ema_fast"] or 9
    cfg["ema_slow"] = cfg["ema_slow"] or 21
    from app.backtest.engine.dte_lots import parse_dte_lot_mult   # ── DTE_LOT_MULT_20260911 ──
    cfg["dte_lot_mult"] = parse_dte_lot_mult(cfg.get("dte_lot_mult"))
    return cfg


def run_emc_backtest(
    *, db_path: str, strategy_id: str, underlying: str,
    date_from: date, date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _impl(db_path=db_path, strategy_id=strategy_id, underlying=underlying,
                         date_from=date_from, date_to=date_to, config_override=config_override,
                         progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _impl(db_path=db_path, strategy_id=strategy_id, underlying=underlying,
                     date_from=date_from, date_to=date_to, config_override=config_override,
                     progress_cb=progress_cb, cancel_cb=cancel_cb)


def _abort(cfg, strategy_id, reason) -> Dict:
    return {"run_id": None, "aborted": True, "reason": reason, "trades": [],
            "summary": _empty_summary(), "config": cfg, "strategy_id": strategy_id}


def validate(cfg: dict) -> Optional[str]:
    """Config guards — a wrong unit must ABORT, never run."""
    block = _hhmm(cfg["entry_cutoff"], -1)
    eod = _hhmm(cfg["eod_square_off"], -1)
    if block < 0 or eod < 0:
        return "entry_cutoff / eod_square_off must be HH:MM"
    if not (SESSION_OPEN_MIN < block <= eod <= 15 * 60 + 29):
        return (f"time order must be 09:15 < entry_cutoff {cfg['entry_cutoff']} <= "
                f"eod_square_off {cfg['eod_square_off']} <= 15:29")
    for k in ("spot_tf", "opt_tf"):
        if not (1 <= cfg[k] <= 60):
            return f"{k} must be 1..60 minutes"
    if not (1 <= cfg["ema_fast"] < cfg["ema_slow"] <= 200):
        return f"EMA periods need 1 <= fast ({cfg['ema_fast']}) < slow ({cfg['ema_slow']}) <= 200"
    if cfg["sel_prem_max"] <= 0 or cfg["sel_prem_min"] >= cfg["sel_prem_max"]:
        return f"premium band needs 0 <= min ({cfg['sel_prem_min']:g}) < max ({cfg['sel_prem_max']:g})"
    if cfg["sl_pts"] >= cfg["sel_prem_max"]:
        return f"sl_pts {cfg['sl_pts']:g} is at/above the band top — a premium-point stop, not a %?"
    if cfg["trail_on"] and cfg["trail_gap_pts"] <= 0:
        return "trailing SL needs trail_gap_pts > 0"
    # ── EMC_V1_BLACKOUT_20261011 ──
    sf, st = cfg.get("arm_skip_from", ""), cfg.get("arm_skip_to", "")
    if bool(sf) != bool(st):
        return "arming blackout needs both 'from' and 'to' (or neither)"
    if sf:
        a, b = _hhmm(sf, -1), _hhmm(st, -1)
        if a < 0 or b < 0:
            return "arming blackout times must be HH:MM"
        if not (SESSION_OPEN_MIN <= a < b <= 15 * 60 + 30):
            return f"arming blackout needs 09:15 <= from ({sf}) < to ({st}) <= 15:30"
    # ── EMC_SELL_20261012 ──
    if cfg.get("leg_action") == "SELL":
        if cfg["tp_pts"] > 0 and cfg["tp_pts"] >= cfg["sel_prem_min"]:
            return (f"SELL: TP {cfg['tp_pts']:g} pts is at/above the band bottom ₹{cfg['sel_prem_min']:g} — the "
                    "target would sit at or below zero premium (unreachable)")
        if cfg.get("hedge_enabled") and cfg["hedge_max_premium"] <= 0:
            return "SELL hedge needs hedge_max_premium > 0 (₹ cap for the protective wing)"
        if cfg.get("hedge_enabled") and cfg["hedge_max_premium"] >= cfg["sel_prem_min"]:
            return (f"hedge cap ₹{cfg['hedge_max_premium']:g} reaches the sold band (≥ ₹{cfg['sel_prem_min']:g}) — "
                    "the wing could be the short itself or nearer the money")
    elif cfg.get("hedge_enabled"):
        return "hedge (protective wing) applies only to leg_action SELL"
    if cfg["warmup_sessions"] > 30 or cfg["opt_warmup_sessions"] > 10:
        return "warm-up too long (spot <= 30 sessions, option <= 10)"
    if cfg["opt_min_bars"] and cfg["opt_min_bars"] > 400:
        return "opt_min_bars must be <= 400"
    return None


def option_warm_bars(src, sym: str, ds: int, sessions: int, tf: int) -> List[EmcBar]:
    """R3: the contract's closed opt_tf bars from up to `sessions` earlier
    sessions held in the corpus (per-session resample on the 09:15 grid)."""
    if sessions <= 0:
        return []
    prior = src.warmup_candles_before(sym, ds, sessions * MIN_PER_SESSION)
    by_day: Dict[date, list] = {}
    for c in prior:
        by_day.setdefault(_ist_date(c.ts), []).append(c)
    out: List[EmcBar] = []
    for d in sorted(by_day)[-sessions:]:
        out.extend(resample_session(by_day[d], day_start_epoch=_day_start_epoch(d), tf_minutes=tf))
    return out


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    from app.backtest.charges.charges_model import charges_for_long_trade
    from app.backtest.util.lot_sizes import resolve_lot
    from app.utils.market_hours import is_trading_day
    from app.event_bus.audit_logger import write_audit_log

    cfg = _merge_cfg(config_override)
    bad = validate(cfg)
    if bad:
        return _abort(cfg, strategy_id, bad)
    index_lot = INDEX_LOTS.get(underlying.upper())
    if index_lot is None:
        return _abort(cfg, strategy_id, f"EMC_V1 is index-only; no lot constant for {underlying}.")
    lot_size, lot_source = resolve_lot(underlying=underlying, is_stock=False, cfg_lot=cfg["lot_size"],
                                       index_lot=index_lot, db_path=db_path)
    if lot_size is None:
        return _abort(cfg, strategy_id, f"no lot size for {underlying}")
    qty = cfg["lots"] * lot_size
    from app.backtest.engine.lot_compounding import LotCompounder as _LotComp   # ── LOT_COMP_20260924 ──
    _comp = _LotComp(config_override, date_from, cfg["lots"])

    stf, otf = cfg["spot_tf"], cfg["opt_tf"]
    block_min = _hhmm(cfg["entry_cutoff"], 14 * 60 + 30)
    eod_min = _hhmm(cfg["eod_square_off"], 15 * 60 + 15)
    opt_min_bars = cfg["opt_min_bars"] or cfg["ema_slow"]

    src = CandleSource(db_path)
    conn = src._conn()

    def spot_day(ds: int) -> list:
        return [EmcBar(r["ts"], r["open"], r["high"], r["low"], r["close"])
                for r in conn.execute(
                    """SELECT ts, open, high, low, close FROM backtest_candles_1m
                       WHERE underlying=? AND instrument_type='SPOT'
                         AND ts>=? AND ts<? ORDER BY ts""",
                    (underlying, ds, ds + 86400))]

    days: List[date] = []
    d = date_from
    while d <= date_to:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)

    # ── R1: warm the spot EMA pair on sessions before date_from ──
    spot_pair = EmaPair(cfg["ema_fast"], cfg["ema_slow"])
    warm_days: List[date] = []
    d, guard = date_from - timedelta(days=1), 0
    while len(warm_days) < cfg["warmup_sessions"] and guard < 60:
        if is_trading_day(d):
            warm_days.append(d)
        d -= timedelta(days=1)
        guard += 1
    warmup_bars = 0
    for wd in reversed(warm_days):
        for b in resample_session(spot_day(_day_start_epoch(wd)), day_start_epoch=_day_start_epoch(wd),
                                  tf_minutes=stf):
            spot_pair.update(b.close)
            warmup_bars += 1

    params = DayParams(
        sel_lo=cfg["sel_prem_min"], sel_hi=cfg["sel_prem_max"], sel_pick=cfg["sel_pick"],
        direction=cfg["direction"], intrabar=cfg["opt_check"] == "intrabar", opt_min_bars=opt_min_bars,
        fill=cfg["fill_mode"], sl_pts=cfg["sl_pts"], tp_pts=cfg["tp_pts"], trail_on=cfg["trail_on"],
        trail_trigger=cfg["trail_trigger_pts"], trail_gap=cfg["trail_gap_pts"], xover_exit=cfg["xover_exit"],
        block_min=block_min, eod_min=eod_min, max_trades=cfg["max_trades_per_day"],
        max_losses=cfg["max_losses_per_day"],
        skip_from=_hhmm(cfg["arm_skip_from"], -1) if cfg["arm_skip_from"] else -1,   # ── EMC_V1_BLACKOUT_20261011 ──
        skip_to=_hhmm(cfg["arm_skip_to"], -1) if cfg["arm_skip_to"] else -1,
        sell=cfg["leg_action"] == "SELL")   # ── EMC_SELL_20261012 ──

    trades: List[EMCTrade] = []
    sim: dict = {}
    diag = {
        "days_total": len(days), "days_traded": 0, "days_no_spot": 0, "days_uncovered": 0,
        "days_skipped_expiry": 0, "days_no_cross": 0,
        "gap_crosses": 0, "gap_crosses_ignored": 0,   # ── EMC_V1_FIX1_20261011 ──
        # ── DTE_LOT_MULT_20260911 ──
        "dte_lot_mult": {str(k): v for k, v in cfg["dte_lot_mult"].items()},
        "dte_skipped_days": 0, "dte_scaled_days": 0, "dte_unknown_days": 0,
        "warmup_bars": warmup_bars, "spot_bars": 0,
        "opt_min_bars": opt_min_bars, "opt_warm_bars_sum": 0, "opt_tracks": 0, "opt_tracks_cold": 0,
        "sel_px_sum": 0.0, "entry_px_sum": 0.0, "wait_min_sum": 0,
        "ce_entries": 0, "pe_entries": 0, "ce_net": 0.0, "pe_net": 0.0,
        "entry_hour_hist": {},
        "sl_exits": 0, "tsl_exits": 0, "tp_exits": 0, "xover_exits": 0, "eod_exits": 0,
        "sl_pnl": 0.0, "tsl_pnl": 0.0, "tp_pnl": 0.0, "xover_pnl": 0.0, "eod_pnl": 0.0,
        # ── EMC_CARRY_20261012 ──
        "run_end_exits": 0, "run_end_pnl": 0.0, "carried_trades": 0, "carried_pnl": 0.0,
        "carry_no_data_days": 0, "carry_managed_skip_days": 0,
        # ── EMC_SELL_20261012 ── leg action / wing economics (VET names)
        "leg_action": cfg["leg_action"], "hedge_enabled": cfg["hedge_enabled"],
        "hedge_max_premium": cfg["hedge_max_premium"], "hedge_synth_enabled": cfg["hedge_synth_enabled"],
        "hedge_real": 0, "hedge_synth": 0, "hedge_synth_fail": 0, "hedge_stale_fills": 0,
        "no_hedge_entries": 0, "hedge_exits": 0, "hedge_cost_total": 0.0, "hedge_synth_exits": 0,
        "hedge_synth_pnl_gross": 0.0, "hedge_synth_mark_fallbacks": 0, "hedge_entry_px_sum": 0.0,
        "hold_sum": 0, "trail_armed_trades": 0,
        "underlying": underlying, "lot_size": lot_size, "lot_source": lot_source, "qty": qty,
        "corpus_db": str(db_path).rsplit("/", 1)[-1],
    }

    # ── DTE_LOT_MULT_20260911 ── corpus trading-day calendar (sessions)
    _dte_cal: List[str] = []
    if cfg["dte_lot_mult"]:
        from app.backtest.repo.trading_calendar import trading_dates as _td
        _dte_cal = _td(underlying, db_path)
        if not _dte_cal:
            write_audit_log(f"[BACKTEST][{strategy_id}] dte_lot_mult set but the "
                            f"corpus calendar is empty — multiplier ignored")
    from app.backtest.engine.dte_lots import lots_for_day as _lots_for_day, dte_for_day as _dte_for_day

    # ── EMC_SELL_20261012 ── leg action + wing (VET V1's rule, the IC synthetic primitives verbatim)
    is_sell = cfg["leg_action"] == "SELL"
    hedge_on = is_sell and bool(cfg["hedge_enabled"])
    from app.backtest.charges.charges_model import charges_for_short_trade
    if hedge_on:
        from app.backtest.ic.backtest_ic_runner import _minute_ladder, _synth_leg_at, _synth_mark_at
    wings: Dict[tuple, dict] = {}            # (entry day start, short sym, entry minute) → wing

    def _leg_charges(entry: float, exitp: float, q: int, short: bool) -> float:
        fn = charges_for_short_trade if short else charges_for_long_trade
        return fn(entry_price=entry, exit_price=exitp, qty=q).total_charges

    carry_on = bool(cfg["carry_overnight"])   # ── EMC_CARRY_20261012 ──
    carry_pos: Optional[dict] = None           # position dict carried into the next session
    carry_exp: Dict[str, str] = {}             # carried symbol → its expiry (ISO)
    carry_last: Optional[tuple] = None         # (day_start, minute) of the carried position's last mark
    import dataclasses as _dc
    params_no_entry = _dc.replace(params, block_min=SESSION_OPEN_MIN)

    for i, day in enumerate(days):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": i + 1, "total_days": len(days), "date": day.isoformat(),
                         "trades": len(trades)})
        ds = _day_start_epoch(day)
        want = expected_expiry_for_day(day).isoformat()
        spot_1m = spot_day(ds)
        if not spot_1m:
            diag["days_no_spot"] += 1
            if carry_pos is not None:
                diag["carry_no_data_days"] += 1
            continue
        spot_by_min = {(b.ts - ds) // 60: b for b in spot_1m}
        bars_tf = resample_session(spot_1m, day_start_epoch=ds, tf_minutes=stf)
        diag["spot_bars"] += len(bars_tf)
        cross_at = spot_crosses(bars_tf, spot_pair)       # R1: ALWAYS advances
        # ── EMC_V1_FIX1_20261011 ── first-candle (overnight-gap) cross
        if bars_tf and (bars_tf[0].ts - ds) // 60 == SESSION_OPEN_MIN and bars_tf[0].end_min in cross_at:
            diag["gap_crosses"] += 1
            if not cfg["first_candle_cross"]:
                del cross_at[bars_tf[0].end_min]
                diag["gap_crosses_ignored"] += 1

        entries_ok = True                          # ── EMC_CARRY_20261012 ── False = manage a carry only
        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:
            diag["days_skipped_expiry"] += 1
            if carry_pos is None:
                continue
            entries_ok = False
        day_qty = qty
        if cfg["dte_lot_mult"]:
            _dte = _dte_for_day(_dte_cal, day.isoformat(), want) if _dte_cal else None
            _lots, _tag = _lots_for_day(cfg["lots"], cfg["dte_lot_mult"], _dte)
            if _tag == "skip":
                diag["dte_skipped_days"] += 1
                if carry_pos is None:
                    continue
                entries_ok = False
            if _tag == "scaled":
                diag["dte_scaled_days"] += 1
            elif _tag == "unknown":
                diag["dte_unknown_days"] += 1
            day_qty = _lots * lot_size
        _comp.begin_day(day, trades)   # ── LOT_COMP_EQ_20260924 ── equity mode re-sizes from realised net
        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty (on top of DTE)
            day_qty = _comp.scale_qty(day_qty, lot_size, day)

        if not any(k < eod_min for k in cross_at):
            diag["days_no_cross"] += 1
            if carry_pos is None:
                continue
            entries_ok = False
        if not entries_ok:
            diag["carry_managed_skip_days"] += 1

        universe_cache: dict = {}
        bars_cache: Dict[str, Dict[int, object]] = {}

        def universe() -> List[dict]:
            if "u" not in universe_cache:
                universe_cache["u"] = src.contracts_active_on_day(underlying, ds, expiry=want)
            return universe_cache["u"]

        def bars_of(sym: str) -> Dict[int, object]:
            if sym not in bars_cache:
                bars_cache[sym] = {(c.ts - ds) // 60: c for c in src.candles_1m_for_symbol_day(sym, ds)}
            return bars_cache[sym]

        def quotes_at(k: int, side: str) -> List[dict]:
            out = []
            for c in universe():
                if c.get("instrument_type") != side or c.get("strike") is None:
                    continue
                bm = bars_of(c["tradingsymbol"])
                px = None
                for m in range(k, k - stf, -1):        # E4: close at k, else last print in the spot bar
                    b = bm.get(m)
                    if b is not None and b.close:
                        px = float(b.close)
                        break
                if px is not None:
                    out.append({"sym": c["tradingsymbol"], "strike": float(c["strike"]), "price": px,
                                "expiry": c.get("expiry")})
            return out

        def track_of(c: dict) -> OptionEmaTrack:
            warm = option_warm_bars(src, c["sym"], ds, cfg["opt_warmup_sessions"], otf)
            diag["opt_tracks"] += 1
            diag["opt_warm_bars_sum"] += len(warm)
            if not warm:
                diag["opt_tracks_cold"] += 1
            today = src.candles_1m_for_symbol_day(c["sym"], ds)
            return OptionEmaTrack(fast=cfg["ema_fast"], slow=cfg["ema_slow"], tf=otf, ds=ds,
                                  warm_bars=warm, today_1m=today)

        def spot_at(k: int) -> Optional[float]:
            b = spot_by_min.get(k)
            return float(b.close) if b is not None else None

        def net_of(entry: float, exitp: float, q: Optional[int] = None) -> float:
            q = day_qty if q is None else q            # ── EMC_CARRY_20261012 ── a carried trade passes its own qty
            ch = charges_for_long_trade(entry_price=entry, exit_price=exitp, qty=q).total_charges
            return round((exitp - entry) * q - ch, 2)

        # ── EMC_SELL_20261012 ── wing helpers for THIS session
        closes_cache: Dict[str, Dict[int, float]] = {}

        def _closes(sym: str) -> Dict[int, float]:
            m = closes_cache.get(sym)
            if m is None:
                m = {c.ts: float(c.close) for c in src.candles_1m_for_symbol_day(sym, ds)}
                closes_cache[sym] = m
            return m

        def _px_at_or_before(sym: str, ts: int):
            mm = _closes(sym)
            if ts in mm:
                return mm[ts], False
            older = [t_ for t_ in mm if t_ <= ts]
            if not older:
                return None, False
            return mm[max(older)], True

        def _expiry_ts(exp_iso: str) -> int:
            d_ = date.fromisoformat(str(exp_iso))
            return _day_start_epoch(d_) + 15 * 3600 + 30 * 60

        def _week(exp_iso: str) -> list:
            if str(exp_iso) == want:
                return universe()
            return src.contracts_active_on_day(underlying, ds, expiry=str(exp_iso))

        def _select_wing(side: str, short_sym: str, ts: int, exp_iso: str) -> Optional[dict]:
            best = None
            for c in _week(exp_iso):
                if c.get("instrument_type") != side or c["tradingsymbol"] == short_sym:
                    continue
                px, stale = _px_at_or_before(c["tradingsymbol"], ts)
                if px is None or px <= 0 or px > cfg["hedge_max_premium"]:
                    continue
                if best is None or px > best["px"]:
                    best = {"sym": c["tradingsymbol"], "px": float(px), "stale": stale,
                            "strike": float(c.get("strike") or 0), "synth": False}
            if best is not None:
                diag["hedge_real"] += 1
                if best["stale"]:
                    diag["hedge_stale_fills"] += 1
                return best
            if not cfg["hedge_synth_enabled"]:
                return None
            wk = _week(exp_iso)
            meta = {c["tradingsymbol"]: {"strike": c.get("strike")} for c in wk}
            try:
                spec, reason = _synth_leg_at(src=src, week=wk, meta_by_sym=meta, day_start=ds, ts=ts,
                                             expiry_ts=_expiry_ts(exp_iso), opt_type=side,
                                             cap=cfg["hedge_max_premium"], underlying=underlying,
                                             want_expiry=str(exp_iso), skew_mult=cfg["hedge_skew_mult"],
                                             ladder=_minute_ladder(src, wk, ds, ts))
            except Exception:
                spec, reason = None, "error"
            if spec is None:
                diag["hedge_synth_fail"] += 1
                return None
            diag["hedge_synth"] += 1
            return {"sym": spec["symbol"], "px": float(spec["price"]), "stale": False,
                    "strike": float(spec["strike"]), "synth": True}

        def _mark_wing(w: dict, ts: int) -> float:
            """Wing exit price at `ts` this session — model if modelled in, a
            real print otherwise (never mixed); last mark when neither exists."""
            px = None
            if w["synth"]:
                wk = _week(w["expiry"])
                try:
                    px = _synth_mark_at(src=src, week=wk, meta_by_sym={c["tradingsymbol"]: {"strike": c.get("strike")}
                                                                       for c in wk},
                                        day_start=ds, ts=ts, expiry_ts=_expiry_ts(w["expiry"]), opt_type=w["side"],
                                        strike=w["strike"], skew_mult=cfg["hedge_skew_mult"])
                except Exception:
                    px = None
            else:
                px, stale = _px_at_or_before(w["sym"], ts)
                if px is not None and stale:
                    diag["hedge_stale_fills"] += 1
            if px is None or px <= 0:
                diag["hedge_synth_mark_fallbacks"] += 1 if w["synth"] else 0
                px = w.get("last", w["px"])
            w["last"] = float(px)
            return float(px)

        def entry_hook(sym: str, kmin: int, px: float) -> bool:
            if not hedge_on:
                return True
            side_ = "CE" if sym.endswith("CE") else "PE"
            exp_iso = next((str(c.get("expiry")) for c in universe() if c["tradingsymbol"] == sym), want)
            w = _select_wing(side_, sym, ds + kmin * 60, exp_iso)
            if w is None:
                diag["no_hedge_entries"] += 1
                return False
            w.update(side=side_, expiry=exp_iso, qty=day_qty)
            wings[(ds, sym, kmin)] = w
            return True

        def pair_pnl(t, exit_px: float, k_exit: int):
            """→ (gross, charges, wing or None, wing exit px) for a position
            closed at k_exit THIS session — the trade row and the loss cap
            use this one function."""
            q = t.qty if t.qty is not None else day_qty
            if t.sell:
                gross = (t.entry_px - exit_px) * q
                ch = _leg_charges(t.entry_px, exit_px, q, True)
            else:
                gross = (exit_px - t.entry_px) * q
                ch = _leg_charges(t.entry_px, exit_px, q, False)
            w = wings.get((t.entry_ds if t.entry_ds is not None else ds, t.sym, t.entry_min))
            wx = None
            if w is not None:
                wx = _mark_wing(w, ds + min(k_exit, SESSION_LAST_MIN) * 60)
                gross += (wx - w["px"]) * q
                ch += _leg_charges(w["px"], wx, q, False)
            return gross, ch, w, wx

        trade_net = None
        if is_sell:
            def trade_net(t, exit_px, k_exit):
                g, c_, _w, _wx = pair_pnl(t, exit_px, k_exit)
                return round(g - c_, 2)

        carry = None
        if carry_on:                                   # ── EMC_CARRY_20261012 ──
            def carry_ok(t, _d=day.isoformat(), _want=want):
                exp = carry_exp.get(t.sym)
                if exp is None:
                    exp = next((str(c.get("expiry")) for c in universe() if c["tradingsymbol"] == t.sym), _want)
                return str(exp) > _d                   # never carry on the contract's own expiry day
            carry = {"in": carry_pos, "ok": carry_ok}
        before = sim.get("setup_no_universe", 0)
        day_trades = simulate_day(p=params if entries_ok else params_no_entry, cross_at=cross_at,
                                  quotes_at=quotes_at, bars_of=bars_of, track_of=track_of, net_of=net_of,
                                  spot_at=spot_at, diag=sim, carry=carry,
                                  trade_net=trade_net, entry_hook=entry_hook if hedge_on else None)   # ── EMC_SELL_20261012 ──
        if carry is not None:                          # ── EMC_CARRY_20261012 ──
            out = carry.get("out")
            if out is not None:
                ct = out["t"]
                if ct.qty is None:
                    ct.qty = day_qty
                if ct.entry_ds is None:
                    ct.entry_ds = ds
                if ct.sym not in carry_exp:
                    carry_exp[ct.sym] = next((str(c.get("expiry")) for c in universe()
                                              if c["tradingsymbol"] == ct.sym), want)
                ct.nights += 1
                carry_last = (ds, SESSION_LAST_MIN)
            carry_pos = out
        if sim.get("setup_no_universe", 0) > before and not universe():
            diag["days_uncovered"] += 1
        if day_trades:
            diag["days_traded"] += 1
        exp_of = {c["tradingsymbol"]: c.get("expiry") for c in universe_cache.get("u", [])}
        for dt in day_trades:
            tq = dt.qty if dt.qty is not None else day_qty            # ── EMC_CARRY_20261012 ──
            eds = dt.entry_ds if dt.entry_ds is not None else ds
            texp = carry_exp.pop(dt.sym, None) if dt.nights else None
            if is_sell:                                # ── EMC_SELL_20261012 ── the pair, as the loss cap saw it
                gross, ch, wing, wing_x = pair_pnl(dt, dt.exit_px, dt.exit_min)
                if wing is not None:
                    w_net = (wing_x - wing["px"]) * tq - _leg_charges(wing["px"], wing_x, tq, False)
                    diag["hedge_exits"] += 1
                    diag["hedge_cost_total"] += round(w_net, 2)
                    diag["hedge_entry_px_sum"] += wing["px"]
                    if wing["synth"]:
                        diag["hedge_synth_exits"] += 1
                        diag["hedge_synth_pnl_gross"] += round((wing_x - wing["px"]) * tq, 2)
                    wings.pop((dt.entry_ds if dt.entry_ds is not None else ds, dt.sym, dt.entry_min), None)
            else:
                wing = None
                gross = (dt.exit_px - dt.entry_px) * tq
                ch = charges_for_long_trade(entry_price=dt.entry_px, exit_price=dt.exit_px,
                                            qty=tq).total_charges
            net = gross - ch
            hh, mm = dt.entry_min // 60, dt.entry_min % 60
            sl_tag = f"SL{cfg['sl_pts']:g}" if cfg["sl_pts"] > 0 else "SLoff"
            tp_tag = f"TP{cfg['tp_pts']:g}" if cfg["tp_pts"] > 0 else "TPoff"
            tr_tag = f"·TSL{cfg['trail_trigger_pts']:g}/{cfg['trail_gap_pts']:g}" if cfg["trail_on"] else ""
            wait_tag = "aligned" if dt.aligned_at_setup else f"wait{dt.waited}m"
            arm_tag = f"arm{dt.sel_min // 60:02d}:{dt.sel_min % 60:02d}"   # ── EMC_V1_FIX1_20261011 ── the arming spot cross (its candle's last minute)
            carry_tag = f"·carry{dt.nights}n" if dt.nights else ""
            if is_sell:                                # ── EMC_SELL_20261012 ──
                carry_tag += "·SELL" + (f"·wing{('~' if wing['synth'] else '')}₹{wing['px']:g}" if wing else "·naked")
            t = EMCTrade(
                tradingsymbol=dt.sym, symbol=dt.sym, instrument_type=dt.side, strike=dt.strike,
                expiry=texp or exp_of.get(dt.sym, want), direction="SELL" if is_sell else "BUY",
                entry_ts=eds + dt.entry_min * 60, entry_price=round(dt.entry_px, 2),
                sl=dt.sl, tp=dt.tp, exit_ts=ds + dt.exit_min * 60, exit_price=round(dt.exit_px, 2),
                exit_reason=dt.reason, qty=tq,
                entry_spot=round(dt.entry_spot, 2) if dt.entry_spot is not None else None,
                condition=(f"EMC·{dt.side}·{hh:02d}:{mm:02d}·{stf}m/{otf}m·EMA{cfg['ema_fast']}/"
                           f"{cfg['ema_slow']}·sel₹{dt.sel_px:g}·{arm_tag}·{wait_tag}·{sl_tag}·{tp_tag}{tr_tag}{carry_tag}"))
            t.pnl = t.gross = round(gross, 2)
            t.charges = round(ch, 2)
            t.net_pnl = t.net = round(net, 2)
            t.max_adverse = round(dt.mae * tq, 2)
            t.max_favorable = round(dt.mfe * tq, 2)
            t.hold_min = max(1, (t.exit_ts - t.entry_ts) // 60)
            if wing is not None:                       # ── EMC_SELL_20261012 ──
                t.wing_symbol, t.wing_entry, t.wing_exit = wing["sym"], round(wing["px"], 2), round(wing_x, 2)
                if wing["synth"]:                      # IC/VET convention: model-priced wing
                    t.synthetic, t.synth_kind = True, "hedge"
            trades.append(t)
            if dt.nights:
                diag["carried_trades"] += 1
                diag["carried_pnl"] += round(net, 2)
            key = dt.reason.lower()
            diag[f"{key}_exits"] += 1
            diag[f"{key}_pnl"] += round(net, 2)
            diag["hold_sum"] += t.hold_min
            diag["sel_px_sum"] += dt.sel_px
            diag["entry_px_sum"] += dt.entry_px
            diag["wait_min_sum"] += dt.waited
            diag["trail_armed_trades"] += 1 if dt.trail_armed else 0
            diag["ce_entries" if dt.side == "CE" else "pe_entries"] += 1
            diag["ce_net" if dt.side == "CE" else "pe_net"] += round(net, 2)
            hk = f"{hh:02d}"
            diag["entry_hour_hist"][hk] = diag["entry_hour_hist"].get(hk, 0) + 1

    if carry_pos is not None:                      # ── EMC_CARRY_20261012 ── open at the end of the run
        ct = carry_pos["t"]
        lds = carry_last[0] if carry_last else _day_start_epoch(days[-1])
        px = float(carry_pos.get("last") or ct.entry_px)
        tq = ct.qty or qty
        if ct.sell:                                    # ── EMC_SELL_20261012 ── short + wing at their last marks
            gross = (ct.entry_px - px) * tq
            ch = _leg_charges(ct.entry_px, px, tq, True)
            w = wings.get((ct.entry_ds, ct.sym, ct.entry_min))
            if w is not None:
                wx = float(w.get("last", w["px"]))
                gross += (wx - w["px"]) * tq
                ch += _leg_charges(w["px"], wx, tq, False)
        else:
            ch = charges_for_long_trade(entry_price=ct.entry_px, exit_price=px, qty=tq).total_charges
            gross = (px - ct.entry_px) * tq
        t = EMCTrade(tradingsymbol=ct.sym, symbol=ct.sym, instrument_type=ct.side, strike=ct.strike,
                     expiry=carry_exp.get(ct.sym), direction=("SELL" if ct.sell else "BUY"),
                     entry_ts=(ct.entry_ds or lds) + ct.entry_min * 60, entry_price=round(ct.entry_px, 2),
                     sl=ct.sl, tp=ct.tp, exit_ts=lds + SESSION_LAST_MIN * 60, exit_price=round(px, 2),
                     exit_reason="RUN_END", qty=tq,
                     condition=f"EMC·{ct.side}·carry{ct.nights}n·open at the end of the run — closed at its last mark")
        t.pnl = t.gross = round(gross, 2)
        t.charges = round(ch, 2)
        t.net_pnl = t.net = round(gross - ch, 2)
        t.max_adverse = round(ct.mae * tq, 2)
        t.max_favorable = round(ct.mfe * tq, 2)
        t.hold_min = max(1, (t.exit_ts - t.entry_ts) // 60)
        trades.append(t)
        diag["run_end_exits"] += 1
        diag["run_end_pnl"] += t.net_pnl
        diag["carried_trades"] += 1
        diag["carried_pnl"] += t.net_pnl
    src.close()
    for k, v in sim.items():
        diag[k] = v
    n = len(trades)
    if n:
        diag["avg_hold_min"] = round(diag["hold_sum"] / n, 1)
        diag["avg_sel_px"] = round(diag["sel_px_sum"] / n, 2)
        diag["avg_entry_px"] = round(diag["entry_px_sum"] / n, 2)
        diag["avg_wait_min"] = round(diag["wait_min_sum"] / n, 1)
    if diag["opt_tracks"]:
        diag["avg_opt_warm_bars"] = round(diag["opt_warm_bars_sum"] / diag["opt_tracks"], 1)
    for k in ("ce_net", "pe_net", "sl_pnl", "tsl_pnl", "tp_pnl", "xover_pnl", "eod_pnl", "sel_px_sum",
              "entry_px_sum", "run_end_pnl", "carried_pnl"):
        diag[k] = round(diag[k], 2)
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying} {date_from}..{date_to}: "
        f"{summary['total_trades']} trades, net {summary['net_pnl']:,.0f}, "
        f"DD {summary['max_drawdown']:,.0f}, exits SL {diag['sl_exits']} / TSL {diag['tsl_exits']} / "
        f"TP {diag['tp_exits']} / XOVER {diag['xover_exits']} / EOD {diag['eod_exits']}, "
        f"setups {diag.get('setups', 0)} (no band {diag.get('setup_no_band', 0)})")
    return {"run_id": str(uuid.uuid4()), "summary": summary, "config": cfg, "trades": trades,
            "strategy_id": strategy_id}


def _summarize(trades: List[EMCTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_emc"] = diag
        return s
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.exit_ts or 0, x.entry_ts or 0)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    nets = [t.net_pnl for t in closed]
    wins = sum(1 for v in nets if v > 0)
    net = sum(nets)
    if abs(net) > 1e-9:
        for k in ("sl", "tsl", "tp", "xover", "eod"):
            diag[f"{k}_pnl_share_pct"] = round(100.0 * diag[f"{k}_pnl"] / net, 1)
    return {
        "total_trades": len(closed), "wins": wins, "losses": sum(1 for v in nets if v < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),
        "ambiguous_fills": 0, "diag_emc": diag,
    }
