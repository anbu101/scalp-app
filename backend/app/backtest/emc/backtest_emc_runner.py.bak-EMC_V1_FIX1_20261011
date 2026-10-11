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
        simulate_day, SESSION_OPEN_MIN)
except ImportError:                                        # standalone tests
    from emc_v1_engine import (  # type: ignore
        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,
        simulate_day, SESSION_OPEN_MIN)

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
    for k in ("trail_on", "xover_exit", "skip_expiry_day"):
        v = cfg.get(k, DEFAULTS[k])
        cfg[k] = (str(v).strip().lower() in ("1", "true", "yes", "on")) if isinstance(v, str) else bool(v)
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
        max_losses=cfg["max_losses_per_day"])

    trades: List[EMCTrade] = []
    sim: dict = {}
    diag = {
        "days_total": len(days), "days_traded": 0, "days_no_spot": 0, "days_uncovered": 0,
        "days_skipped_expiry": 0, "days_no_cross": 0,
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
            continue
        spot_by_min = {(b.ts - ds) // 60: b for b in spot_1m}
        bars_tf = resample_session(spot_1m, day_start_epoch=ds, tf_minutes=stf)
        diag["spot_bars"] += len(bars_tf)
        cross_at = spot_crosses(bars_tf, spot_pair)       # R1: ALWAYS advances

        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:
            diag["days_skipped_expiry"] += 1
            continue
        day_qty = qty
        if cfg["dte_lot_mult"]:
            _dte = _dte_for_day(_dte_cal, day.isoformat(), want) if _dte_cal else None
            _lots, _tag = _lots_for_day(cfg["lots"], cfg["dte_lot_mult"], _dte)
            if _tag == "skip":
                diag["dte_skipped_days"] += 1
                continue
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
            continue

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

        def net_of(entry: float, exitp: float) -> float:
            ch = charges_for_long_trade(entry_price=entry, exit_price=exitp, qty=day_qty).total_charges
            return round((exitp - entry) * day_qty - ch, 2)

        before = sim.get("setup_no_universe", 0)
        day_trades = simulate_day(p=params, cross_at=cross_at, quotes_at=quotes_at, bars_of=bars_of,
                                  track_of=track_of, net_of=net_of, spot_at=spot_at, diag=sim)
        if sim.get("setup_no_universe", 0) > before and not universe():
            diag["days_uncovered"] += 1
        if day_trades:
            diag["days_traded"] += 1
        exp_of = {c["tradingsymbol"]: c.get("expiry") for c in universe_cache.get("u", [])}
        for dt in day_trades:
            gross = (dt.exit_px - dt.entry_px) * day_qty
            ch = charges_for_long_trade(entry_price=dt.entry_px, exit_price=dt.exit_px,
                                        qty=day_qty).total_charges
            net = gross - ch
            hh, mm = dt.entry_min // 60, dt.entry_min % 60
            sl_tag = f"SL{cfg['sl_pts']:g}" if cfg["sl_pts"] > 0 else "SLoff"
            tp_tag = f"TP{cfg['tp_pts']:g}" if cfg["tp_pts"] > 0 else "TPoff"
            tr_tag = f"·TSL{cfg['trail_trigger_pts']:g}/{cfg['trail_gap_pts']:g}" if cfg["trail_on"] else ""
            wait_tag = "aligned" if dt.aligned_at_setup else f"wait{dt.waited}m"
            t = EMCTrade(
                tradingsymbol=dt.sym, symbol=dt.sym, instrument_type=dt.side, strike=dt.strike,
                expiry=exp_of.get(dt.sym, want), direction="BUY",
                entry_ts=ds + dt.entry_min * 60, entry_price=round(dt.entry_px, 2),
                sl=dt.sl, tp=dt.tp, exit_ts=ds + dt.exit_min * 60, exit_price=round(dt.exit_px, 2),
                exit_reason=dt.reason, qty=day_qty,
                entry_spot=round(dt.entry_spot, 2) if dt.entry_spot is not None else None,
                condition=(f"EMC·{dt.side}·{hh:02d}:{mm:02d}·{stf}m/{otf}m·EMA{cfg['ema_fast']}/"
                           f"{cfg['ema_slow']}·sel₹{dt.sel_px:g}·{wait_tag}·{sl_tag}·{tp_tag}{tr_tag}"))
            t.pnl = t.gross = round(gross, 2)
            t.charges = round(ch, 2)
            t.net_pnl = t.net = round(net, 2)
            t.max_adverse = round(dt.mae * day_qty, 2)
            t.max_favorable = round(dt.mfe * day_qty, 2)
            t.hold_min = max(1, dt.exit_min - dt.entry_min)
            trades.append(t)
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
              "entry_px_sum"):
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
