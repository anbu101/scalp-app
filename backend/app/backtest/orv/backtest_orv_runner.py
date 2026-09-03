# backend/app/backtest/orv/backtest_orv_runner.py
#
# ── ORV_V1 RUNNER ── "Orbit": ORB-Reversal on NIFTY/BANKNIFTY SPOT with
# weekly option BUY execution and SPOT-level SL/TP.
#
# Fence: ORV_V1_20260903
#
# SPEC OF RECORD (chat, 2026-09-03), D1-D1.7 in orv_v1_engine.py's header.
# Runner-owned decisions:
#   R1  Entry fill: signal confirms at the pattern 5m bar's close; the
#       option fills at the NEXT minute's 1m OPEN (entry_min = pattern bar
#       start + tf). entry_spot = that minute's SPOT 1m open. D1.2 exact.
#   R2  Contract: nearest premium strictly BELOW premium_max (>= premium_min
#       when set) among the EXPECTED weekly expiry, read from the 1m CLOSE
#       of the bar ENDING at the entry minute (entry_min - 1) — the LTP a
#       live engine sees at the fill instant. Fill at the chosen contract's
#       entry-minute 1m open. No unfinished-bar reads (BRK P1/P2).
#   R3  Exits are SPOT-level (D4/D1.6): the ladder walks 1m SPOT bars; a
#       breached level books at the OPTION's own 1m CLOSE of that minute
#       (CBO convention — a spot level has no premium print of its own).
#       SL and TP inside one minute -> SL. EOD square-off at eod_square_off
#       on the option 1m close. Missing option print at an exit minute =
#       last known mark, counted stale, never a silent zero.
#   R4  Budgets: one position at a time; max_trades_per_day (default 2);
#       max_trades_per_side (default 1) — D1.5's "no re-entry after SL on
#       the same side". Signals dropped by budget/overlap are counted, so
#       a sweep can see what the budget is hiding.
#   R5  ATR (D2): daily OHLC resampled ONCE from the SPOT 1m corpus over
#       [date_from - lookback, date_to]; Wilder ATR as of each session's
#       close; day d uses the value of the last session STRICTLY BEFORE d.
#       Fewer than atr_period prior sessions -> no-trade day, counted.
#
# ── WHY THE DIAGNOSTICS EXIST ─────────────────────────────────────────────
# Reversal-pattern rules are parameter-fragile and this one has FOUR gates
# in series (ATR filter -> breakout arm -> pattern -> target guard), so a
# "good" run can be one gate doing all the work. Every gate's kill count is
# attributed, the pattern histogram shows whether one shape carries the
# result, and eod_pnl_gross is the standing SCALP_V5 tripwire: if EOD exits
# carry most of net, the run describes the square-off, not the reversal.
#
# ── PARITY NOTES ─────────────────────────────────────────────────────────
#   P1  Selection/fill instants exactly as R1/R2 — live must sample the same.
#   P2  Holiday awareness via app.utils.market_hours.is_trading_day.
#   P3  ATR uses prior sessions only; the live engine reads the same series
#       from the same daily resample. No same-day leak.
#   P4  Fills use a contract's OWN 1m bars; stale marks are counted.
#   P5  Expected weekly expiry only; an uncovered day is SKIPPED, never
#       substituted with a farther expiry.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

try:
    from app.backtest.orv.orv_v1_engine import (
        OrvBar, resample_1m, compute_orb, atr_series, orv_signals,
        target_level, resolve_spot_exit, SESSION_OPEN_MIN)
except ImportError:                                        # standalone tests
    from orv_v1_engine import (  # type: ignore
        OrvBar, resample_1m, compute_orb, atr_series, orv_signals,
        target_level, resolve_spot_exit, SESSION_OPEN_MIN)

IST_OFFSET = 5 * 3600 + 30 * 60

# Index lot sizes are CONSTANTS by fleet convention (see lot_sizes.py).
INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}

DEFAULTS: dict = {
    # ── range & filter (D1, D2) ──
    "orb_minutes": 90,
    "timeframe_minutes": 5,
    "atr_period": 14,
    "atr_pct": 25.0,                   # ORB range must EXCEED this % of ATR
    "atr_method": "wilder",            # wilder | sma

    # ── patterns (D1.1) ──
    "hammer_on": True,                 # bull: hammer / bear: shooting star
    "engulf_on": True,                 # bull/bear engulfing
    "wick_body_ratio": 2.0,
    "opp_wick_ratio": 0.5,
    "engulf_need_opposite_prev": True,

    # ── state machine (D1.3, D1.7) ──
    "disarm_on_reentry": True,
    "max_wait_bars": 0,                # inert sweep axis (0 = off)

    # ── entries & budgets (D1.5, R2, R4) ──
    "premium_max": 180.0,              # nearest premium strictly below this
    "premium_min": 0.0,                # optional floor; 0 = off
    "entry_block_time": "14:30",       # no entries at-or-after this minute
    "max_trades_per_day": 2,
    "max_trades_per_side": 1,

    # ── exits (D4, D1.4, D1.6, R3) ──
    "target_mode": "T1",               # T1 | T2 | custom
    "target_points": 0.0,              # custom mode: spot points from entry
    "sl_points": 30.0,                 # spot points from entry
    "skip_if_past_target": True,       # D1.4
    "eod_square_off": "15:15",

    # ── sizing / calendar ──
    "lots": 1,
    "lot_size": 0,                     # 0 = index constant
    "skip_expiry_day": False,
}


@dataclass
class ORVTrade:
    """Attribute surface matches BRKTrade / CBOTrade so
    backtest_repo.persist_run works unchanged (reads t.symbol /
    t.max_adverse / t.ambiguous_fill by ATTRIBUTE — must stay an OBJECT).
    Deliberately NO `hedge_symbol` attribute — its presence diverts
    persist_run to the V3/V4 hedge branch."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                     # always BUY
    entry_ts: int
    entry_price: float
    sl: Optional[float]                # SPOT level (see condition string)
    tp: Optional[float]                # SPOT level
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]         # SL | TP | EOD
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
    """'HH:MM' -> minutes since midnight IST; malformed -> fallback."""
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
    cfg = dict(DEFAULTS)
    for k, v in (override or {}).items():
        cfg[k] = v
    # ── normalise: a bad UI value can never silently change semantics ──
    _tm = str(cfg.get("target_mode", "T1")).strip()
    cfg["target_mode"] = _tm if _tm in ("T1", "T2", "custom") else "T1"
    _am = str(cfg.get("atr_method", "wilder")).lower()
    cfg["atr_method"] = _am if _am in ("wilder", "sma") else "wilder"
    for k in ("orb_minutes", "timeframe_minutes", "atr_period",
              "max_wait_bars", "max_trades_per_day", "max_trades_per_side",
              "lots", "lot_size"):
        try:
            cfg[k] = max(0, int(cfg[k] or 0))
        except (TypeError, ValueError):
            cfg[k] = int(DEFAULTS[k])
    cfg["lots"] = cfg["lots"] or 1
    cfg["timeframe_minutes"] = cfg["timeframe_minutes"] or 5
    cfg["orb_minutes"] = cfg["orb_minutes"] or 90
    cfg["atr_period"] = cfg["atr_period"] or 14
    cfg["max_trades_per_day"] = cfg["max_trades_per_day"] or 1
    cfg["max_trades_per_side"] = cfg["max_trades_per_side"] or 1
    for k in ("atr_pct", "wick_body_ratio", "opp_wick_ratio", "premium_max",
              "premium_min", "target_points", "sl_points"):
        try:
            cfg[k] = abs(float(cfg[k] or 0.0))
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    for k in ("hammer_on", "engulf_on", "engulf_need_opposite_prev",
              "disarm_on_reentry", "skip_if_past_target", "skip_expiry_day"):
        cfg[k] = bool(cfg.get(k, DEFAULTS[k]))
    return cfg


# ─────────────────────────────────────────────────────────────────────────
#  PURE HELPERS (unit-tested in test_orv_runner_sim.py)
# ─────────────────────────────────────────────────────────────────────────
def pick_candidate(prints: Dict[str, float], *, below: float,
                   floor: float = 0.0) -> Optional[str]:
    """R2: of {symbol: premium}, the symbol with the HIGHEST premium
    strictly below `below` (and >= floor when floor > 0). Deterministic
    tie-break on symbol name. Same shape as BRK's D1 selector."""
    best: Optional[Tuple[float, str]] = None
    for sym, px in prints.items():
        if px is None or px <= 0:
            continue
        if px >= below:
            continue
        if floor > 0 and px < floor:
            continue
        key = (float(px), sym)
        if best is None or key > best:
            best = key
    return best[1] if best else None


# ─────────────────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────────────────
def run_orv_backtest(
    *,
    db_path: str,
    strategy_id: str,                  # "ORV_V1"
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


def _abort(cfg, strategy_id, reason) -> Dict:
    return {"run_id": None, "aborted": True, "reason": reason,
            "trades": [], "summary": _empty_summary(),
            "config": cfg, "strategy_id": strategy_id}


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    from app.backtest.charges.charges_model import charges_for_long_trade
    from app.backtest.util.lot_sizes import resolve_lot
    from app.utils.market_hours import is_trading_day
    from app.event_bus.audit_logger import write_audit_log

    cfg = _merge_cfg(config_override)

    index_lot = INDEX_LOTS.get(underlying.upper())
    if index_lot is None:
        return _abort(cfg, strategy_id,
                      f"ORV_V1 is index-only; no lot constant for {underlying}.")
    lot_size, lot_source = resolve_lot(
        underlying=underlying, is_stock=False, cfg_lot=cfg["lot_size"],
        index_lot=index_lot, db_path=db_path)
    if lot_size is None:
        return _abort(cfg, strategy_id, f"no lot size for {underlying}")
    qty = cfg["lots"] * lot_size

    tf = cfg["timeframe_minutes"]
    orb_end_min = SESSION_OPEN_MIN + cfg["orb_minutes"]
    block_min = _hhmm(cfg["entry_block_time"], 14 * 60 + 30)
    eod_min = _hhmm(cfg["eod_square_off"], 15 * 60 + 15)
    if cfg["orb_minutes"] % tf != 0:
        return _abort(cfg, strategy_id,
                      f"orb_minutes {cfg['orb_minutes']} must be a multiple "
                      f"of timeframe_minutes {tf}")
    if not (orb_end_min < block_min <= eod_min):
        return _abort(cfg, strategy_id,
                      (f"time order must be ORB end "
                       f"{orb_end_min // 60:02d}:{orb_end_min % 60:02d} < "
                       f"entry_block_time {cfg['entry_block_time']} <= "
                       f"eod_square_off {cfg['eod_square_off']}"))
    if cfg["sl_points"] <= 0:
        return _abort(cfg, strategy_id, "sl_points must be > 0")
    if cfg["target_mode"] == "custom" and cfg["target_points"] <= 0:
        return _abort(cfg, strategy_id,
                      "target_mode custom needs target_points > 0")
    if not (cfg["hammer_on"] or cfg["engulf_on"]):
        return _abort(cfg, strategy_id,
                      "at least one pattern family must be ON")
    if cfg["premium_max"] <= 0:
        return _abort(cfg, strategy_id, "premium_max must be > 0")

    src = CandleSource(db_path)
    conn = src._conn()

    days: List[date] = []
    d = date_from
    while d <= date_to:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)

    # ── R5: daily OHLC + ATR series, precomputed ONCE over the whole span.
    # Lookback covers atr_period sessions with generous holiday slack.
    lb_days = max(60, cfg["atr_period"] * 4)
    span_lo = _day_start_epoch(date_from - timedelta(days=lb_days))
    span_hi = _day_start_epoch(date_to) + 86400
    daily_ohlc: Dict[date, Tuple[float, float, float, float]] = {}
    _cur_day: Optional[date] = None
    _o = _h = _l = _c = 0.0
    for r in conn.execute(
            """SELECT ts, open, high, low, close FROM backtest_candles_1m
               WHERE underlying=? AND instrument_type='SPOT'
                 AND ts>=? AND ts<? ORDER BY ts""",
            (underlying, span_lo, span_hi)):
        rd = (datetime(1970, 1, 1)
              + timedelta(seconds=r["ts"] + IST_OFFSET)).date()
        if rd != _cur_day:
            if _cur_day is not None:
                daily_ohlc[_cur_day] = (_o, _h, _l, _c)
            _cur_day, _o, _h, _l, _c = rd, r["open"], r["high"], r["low"], r["close"]
        else:
            _h = max(_h, r["high"])
            _l = min(_l, r["low"])
            _c = r["close"]
    if _cur_day is not None:
        daily_ohlc[_cur_day] = (_o, _h, _l, _c)
    sessions = sorted(daily_ohlc.keys())
    atr_vals = atr_series([(daily_ohlc[s][1], daily_ohlc[s][2],
                            daily_ohlc[s][3]) for s in sessions],
                          period=cfg["atr_period"], method=cfg["atr_method"])
    atr_asof = dict(zip(sessions, atr_vals))   # value AS OF that session close

    def atr_before(dd: date) -> Optional[float]:
        """ATR as of the last session STRICTLY BEFORE dd (P3). If that
        session's ATR is not yet warm this returns None — FAIL-CLOSED,
        never a stale value from further back."""
        import bisect
        i = bisect.bisect_left(sessions, dd) - 1
        if i < 0:
            return None
        return atr_asof.get(sessions[i])

    trades: List[ORVTrade] = []
    diag = {
        "days_total": len(days), "days_traded": 0,
        "days_uncovered": 0, "days_skipped_expiry": 0, "days_no_spot": 0,
        "days_no_atr": 0, "days_orb_incomplete": 0,
        "days_range_filter_skip": 0, "days_no_arm": 0, "days_no_signal": 0,
        "bull_arms": 0, "bear_arms": 0, "bull_disarms": 0, "bear_disarms": 0,
        "bull_wait_disarms": 0, "bear_wait_disarms": 0,
        "signals_total": 0, "pattern_hist": {},
        "sig_dropped_open": 0, "sig_dropped_budget": 0,
        "sig_dropped_side_budget": 0, "sig_dropped_block_time": 0,
        "sig_no_candidate": 0, "sig_no_spot_bar": 0, "sig_no_fill": 0,
        "target_passed_skips": 0,
        "entries": 0, "ce_entries": 0, "pe_entries": 0,
        "entry_minute_hist": {},
        "sl_exits": 0, "tp_exits": 0, "eod_exits": 0,
        "sl_pnl_gross": 0.0, "tp_pnl_gross": 0.0, "eod_pnl_gross": 0.0,
        "stale_marks": 0, "stale_spot": 0,
        "orb_range_sum": 0.0, "orb_range_days": 0,
        "atr_sum": 0.0, "atr_days": 0,
        "underlying": underlying, "lot_size": lot_size,
        "lot_source": lot_source, "qty": qty,
        "corpus_db": str(db_path).rsplit("/", 1)[-1],
    }

    def close_trade(pos: dict, ts: int, px: float, reason: str) -> None:
        gross = (px - pos["entry_px"]) * pos["qty"]
        # ChargesResult exposes total_charges (NOT .total).
        ch = charges_for_long_trade(entry_price=pos["entry_px"],
                                    exit_price=px, qty=pos["qty"]).total_charges
        net = gross - ch
        t = pos["trade"]
        t.exit_ts, t.exit_price, t.exit_reason = ts, round(px, 2), reason
        t.pnl = t.gross = round(gross, 2)
        t.charges = round(ch, 2)
        t.net_pnl = t.net = round(net, 2)
        t.max_adverse = round(pos["mae"], 2)
        t.max_favorable = round(pos["mfe"], 2)
        key = {"SL": "sl", "TP": "tp", "EOD": "eod"}[reason]
        diag[f"{key}_exits"] += 1
        diag[f"{key}_pnl_gross"] += round(net, 2)

    for i, day in enumerate(days):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            # Contract matches VET/TMA/PST/CBO/BRK: day = 1-based index,
            # total_days = count, date = display string.
            progress_cb({"day": i + 1, "total_days": len(days),
                         "date": day.isoformat(), "trades": len(trades)})

        ds = _day_start_epoch(day)
        want = expected_expiry_for_day(day).isoformat()
        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:
            diag["days_skipped_expiry"] += 1
            continue

        # ── SPOT tape ──
        spot_1m = [OrvBar(r["ts"], r["open"], r["high"], r["low"], r["close"])
                   for r in conn.execute(
                       """SELECT ts, open, high, low, close
                          FROM backtest_candles_1m
                          WHERE underlying=? AND instrument_type='SPOT'
                            AND ts>=? AND ts<? ORDER BY ts""",
                       (underlying, ds, ds + 86400))]
        if not spot_1m:
            diag["days_no_spot"] += 1
            continue
        spot_by_min = {(b.ts - ds) // 60: b for b in spot_1m}

        # ── D2: ATR gate (prior sessions only) ──
        atr = atr_before(day)
        if atr is None or atr <= 0:
            diag["days_no_atr"] += 1
            continue

        # ── D1: ORB, fail-closed on coverage ──
        bars_tf = resample_1m(spot_1m, day_start_epoch=ds, tf_minutes=tf)
        orb = compute_orb(bars_tf, day_start_epoch=ds,
                          orb_minutes=cfg["orb_minutes"], tf_minutes=tf)
        if orb is None:
            diag["days_orb_incomplete"] += 1
            continue
        orb_high, orb_low = orb
        orb_range = orb_high - orb_low
        diag["orb_range_sum"] += orb_range
        diag["orb_range_days"] += 1
        diag["atr_sum"] += atr
        diag["atr_days"] += 1
        if not (orb_range > (cfg["atr_pct"] / 100.0) * atr):
            diag["days_range_filter_skip"] += 1
            continue

        # ── D3/D1.1/D1.3: raw signals ──
        sm_diag: dict = {}
        sigs = orv_signals(
            bars_tf, day_start_epoch=ds, orb_high=orb_high, orb_low=orb_low,
            orb_minutes=cfg["orb_minutes"],
            hammer_on=cfg["hammer_on"], engulf_on=cfg["engulf_on"],
            wick_body_ratio=cfg["wick_body_ratio"],
            opp_wick_ratio=cfg["opp_wick_ratio"],
            engulf_need_opposite_prev=cfg["engulf_need_opposite_prev"],
            disarm_on_reentry=cfg["disarm_on_reentry"],
            max_wait_bars=cfg["max_wait_bars"], diag=sm_diag)
        for k in ("bull_arms", "bear_arms", "bull_disarms", "bear_disarms",
                  "bull_wait_disarms", "bear_wait_disarms"):
            diag[k] += sm_diag.get(k, 0)
        if sm_diag.get("bull_arms", 0) + sm_diag.get("bear_arms", 0) == 0:
            diag["days_no_arm"] += 1
            continue
        if not sigs:
            diag["days_no_signal"] += 1
            continue
        diag["signals_total"] += len(sigs)

        # ── lazily-loaded option universe (only days that get this far) ──
        universe = src.contracts_active_on_day(underlying, ds, expiry=want)
        if not universe:
            # P5: no faithful contract -> skip, never substitute an expiry.
            diag["days_uncovered"] += 1
            continue
        meta: Dict[str, dict] = {}
        bars_by_sym: Dict[str, Dict[int, object]] = {}

        def bars(sym: str) -> Dict[int, object]:
            if sym not in bars_by_sym:
                bars_by_sym[sym] = {c.ts: c for c in
                                    src.candles_1m_for_symbol_day(sym, ds)}
            return bars_by_sym[sym]

        day_trades = 0
        side_trades = {"CE": 0, "PE": 0}
        open_until: Optional[int] = None   # exit minute of the open position
        traded = False

        for s in sigs:
            diag["pattern_hist"][s.pattern] = \
                diag["pattern_hist"].get(s.pattern, 0) + 1
            entry_min = (s.ts - ds) // 60 + tf     # R1: next bar's open
            if open_until is not None and entry_min <= open_until:
                diag["sig_dropped_open"] += 1
                continue
            if day_trades >= cfg["max_trades_per_day"]:
                diag["sig_dropped_budget"] += 1
                continue
            if side_trades[s.side] >= cfg["max_trades_per_side"]:
                diag["sig_dropped_side_budget"] += 1
                continue
            if entry_min >= block_min or entry_min >= eod_min:
                diag["sig_dropped_block_time"] += 1
                continue
            sb = spot_by_min.get(entry_min)
            if sb is None:
                diag["sig_no_spot_bar"] += 1
                continue
            entry_spot = float(sb.open)

            # ── D4/D1.4: target guard BEFORE spending a budget slot ──
            tp_level = target_level(
                side=s.side, mode=cfg["target_mode"], orb_high=orb_high,
                orb_low=orb_low, entry_spot=entry_spot,
                custom_pts=cfg["target_points"])
            if tp_level is None:
                if cfg["skip_if_past_target"]:
                    diag["target_passed_skips"] += 1
                    continue
                # guard disabled (sweep-only): fall through to the FULL
                # range target so the trade still has a defined exit.
                tp_level = orb_high if s.side == "CE" else orb_low
            sl_level = (entry_spot - cfg["sl_points"] if s.side == "CE"
                        else entry_spot + cfg["sl_points"])

            # ── R2: contract selection at the fill instant ──
            sel_ts = ds + (entry_min - 1) * 60     # bar ENDING at entry_min
            prints: Dict[str, float] = {}
            for c in universe:
                if c.get("instrument_type") != s.side:
                    continue
                b = bars(c["tradingsymbol"]).get(sel_ts)
                if b is None:
                    continue
                prints[c["tradingsymbol"]] = float(b.close)
                meta[c["tradingsymbol"]] = c
            sym = pick_candidate(prints, below=cfg["premium_max"],
                                 floor=cfg["premium_min"])
            if sym is None:
                diag["sig_no_candidate"] += 1
                continue
            fb = bars(sym).get(ds + entry_min * 60)
            if fb is None or not fb.open:
                diag["sig_no_fill"] += 1
                continue
            entry_px = float(fb.open)
            mc = meta[sym]
            hh = entry_min // 60
            mm = entry_min % 60
            t = ORVTrade(
                tradingsymbol=sym, symbol=sym, instrument_type=s.side,
                strike=float(mc["strike"]) if mc.get("strike") is not None else None,
                expiry=mc.get("expiry"), direction="BUY",
                entry_ts=ds + entry_min * 60, entry_price=round(entry_px, 2),
                sl=round(sl_level, 2), tp=round(tp_level, 2),
                exit_ts=None, exit_price=None, exit_reason=None, qty=qty,
                condition=(f"ORV·{s.side}·{s.pattern}·{hh:02d}:{mm:02d}"
                           f"·spotSL{cfg['sl_points']:g}"
                           f"·{cfg['target_mode']}"))
            trades.append(t)
            diag["entries"] += 1
            diag["ce_entries" if s.side == "CE" else "pe_entries"] += 1
            hk = f"{hh:02d}:{mm:02d}"
            diag["entry_minute_hist"][hk] = \
                diag["entry_minute_hist"].get(hk, 0) + 1
            day_trades += 1
            side_trades[s.side] += 1
            traded = True

            # ── R3: exit ladder on 1m SPOT levels, option fills ──
            pos = {"trade": t, "entry_px": entry_px, "qty": qty,
                   "mae": 0.0, "mfe": 0.0, "last_mark": entry_px}
            ob = bars(sym)
            exit_min = eod_min
            for m in range(entry_min, eod_min + 1):
                o = ob.get(ds + m * 60)
                if m >= eod_min:
                    px = float(o.close) if o is not None else pos["last_mark"]
                    close_trade(pos, ds + m * 60, px, "EOD")
                    exit_min = m
                    break
                if o is not None:
                    pos["last_mark"] = float(o.close)
                    pos["mae"] = min(pos["mae"],
                                     (float(o.low) - entry_px) * qty)
                    pos["mfe"] = max(pos["mfe"],
                                     (float(o.high) - entry_px) * qty)
                else:
                    diag["stale_marks"] += 1
                b = spot_by_min.get(m)
                if b is None:
                    diag["stale_spot"] += 1
                    continue
                ex = resolve_spot_exit(side=s.side, sl_level=sl_level,
                                       tp_level=tp_level, spot_bar=b)
                if ex is not None:
                    px = (float(o.close) if o is not None
                          else pos["last_mark"])
                    close_trade(pos, ds + m * 60, px, ex)
                    exit_min = m
                    break
            else:
                # Ran out of the range without EOD bar (truncated tape):
                # book at the last known mark, never leave a row open.
                last_ts = max(ob) if ob else ds + eod_min * 60
                close_trade(pos, last_ts, pos["last_mark"], "EOD")
                exit_min = eod_min
            open_until = exit_min

        if traded:
            diag["days_traded"] += 1

    src.close()
    if diag["orb_range_days"]:
        diag["orb_range_avg"] = round(
            diag["orb_range_sum"] / diag["orb_range_days"], 2)
    if diag["atr_days"]:
        diag["atr_avg"] = round(diag["atr_sum"] / diag["atr_days"], 2)
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying} {date_from}..{date_to}: "
        f"{summary['total_trades']} trades, net {summary['net_pnl']:,.0f}, "
        f"DD {summary['max_drawdown']:,.0f}, exits SL {diag['sl_exits']} / "
        f"TP {diag['tp_exits']} / EOD {diag['eod_exits']}, "
        f"days rangeSkip {diag['days_range_filter_skip']} / "
        f"noArm {diag['days_no_arm']} / noSig {diag['days_no_signal']} / "
        f"noATR {diag['days_no_atr']} / uncovered {diag['days_uncovered']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}


def _summarize(trades: List[ORVTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_orv"] = diag
        return s
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.exit_ts or 0, x.entry_ts or 0)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    nets = [t.net_pnl for t in closed]
    wins = sum(1 for n in nets if n > 0)
    net = sum(nets)
    if abs(net) > 1e-9:
        for k in ("sl", "tp", "eod"):
            diag[f"{k}_pnl_share_pct"] = round(
                100.0 * diag[f"{k}_pnl_gross"] / net, 1)
    return {
        "total_trades": len(closed), "wins": wins,
        "losses": sum(1 for n in nets if n < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),
        "ambiguous_fills": 0,
        "diag_orv": diag,
    }
