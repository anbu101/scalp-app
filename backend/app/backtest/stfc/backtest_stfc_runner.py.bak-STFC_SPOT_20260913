# backend/app/backtest/stfc/backtest_stfc_runner.py
#
# ── STFC_V1 RUNNER ── "SuperTrend Flip-Confirm" on NIFTY/BANKNIFTY SPOT
# with weekly option BUY execution and premium-denominated exits.
#
# Fence: STFC_OPT_20260913
#
# Engine decisions D1–D4 in stfc_v1_engine.py. Runner-owned decisions:
#   R1  Entry fill: a trade bar (engine D3) starts at minute M; the option
#       fills at M's 1m OPEN. entry_spot = SPOT 1m open at M. No unfinished-
#       bar reads: the confirm bar has fully closed before M.
#   R2  Contract: ATM strike from entry_spot (step inferred from the day's
#       chain) shifted `strike_offset` steps OUT of the money (negative =
#       in the money), expected weekly expiry only, same side as the flip
#       (up-flip → CE, down-flip → PE). If that strike has no print at M
#       the nearest strike with a print is used (counted). Optional
#       premium band {min,max} rejects absurd fills (0 = off).
#   R3  Exits, evaluated per minute in this order (pessimistic — stop
#       before target, both before time):
#         1. PREMIUM stop  (sl_mode off|abs|pct of entry premium) → books
#            AT the level (open if gapped through).
#         2. PREMIUM target (tp_mode off|abs|pct) → books AT the level.
#         3. TIME: exit_mode "candle" → the 1m CLOSE of the trade bar's
#            last minute (the Pine "exit at candle close"); "minutes" →
#            the 1m CLOSE of the minute completing hold_minutes from entry
#            (may span several tf bars).
#         4. EOD square-off at the option 1m close.
#       A missing option bar leaves the last mark in place (counted).
#   R4  Budgets: one position at a time — a signal that fires while in a
#       position is SKIPPED, never queued (counted); max_trades_per_day
#       (0 = unlimited); no entries at/after entry_block_time.
#   R5  SuperTrend state is continuous across sessions and warmed up on
#       `warmup_sessions` sessions before date_from (no trades there).
#
# ── FALSIFICATION TRIPWIRES ─────────────────────────────────────────────
#   * time/candle_pnl_share_pct vs sl/tp — if the timed exit carries the
#     net, the edge is "hold N minutes after a flip", not the SL/TP shape.
#   * sig_dropped_open — with long holds most signals are skipped; the
#     run then describes a much sparser strategy than the chart suggests.
#   * eod_pnl_share_pct — should be ~0 with short holds.
#   * ce_net vs pe_net and the DTE breakdown — 0DTE premium decay inside
#     the hold is the first thing that can kill this.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.stfc.stfc_v1_engine import (
        StfcBar, SuperTrend, resample_session, session_signals,
        prem_level, prem_fill, strike_step, atm_strike, SESSION_OPEN_MIN)
except ImportError:                                        # standalone tests
    from stfc_v1_engine import (  # type: ignore
        StfcBar, SuperTrend, resample_session, session_signals,
        prem_level, prem_fill, strike_step, atm_strike, SESSION_OPEN_MIN)

IST_OFFSET = 5 * 3600 + 30 * 60
INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}

DEFAULTS: dict = {
    # ── signal (D1/D2/D3) ──
    "timeframe_minutes": 3,
    "st_len": 10,
    "st_mult": 2.0,
    "direction": "BOTH",               # BOTH | UP | DOWN
    "warmup_sessions": 3,

    # ── contract (R2) ──
    "strike_offset": 0,                # steps OTM (negative = ITM)
    "premium_min": 0.0,                # 0 = off
    "premium_max": 0.0,                # 0 = off
    "lots": 1,
    "lot_size": 0,                     # 0 = index constant

    # ── exits (R3) ──
    "sl_mode": "pct",                  # off | abs | pct (of entry premium)
    "sl_value": 20.0,
    "tp_mode": "pct",                  # off | abs | pct
    "tp_value": 30.0,
    "exit_mode": "candle",             # candle | minutes
    "hold_minutes": 5,
    "eod_square_off": "15:20",

    # ── budgets & session (R4) ──
    "entry_block_time": "15:00",
    "max_trades_per_day": 0,           # 0 = unlimited
    "skip_expiry_day": False,
}


@dataclass
class STFCTrade:
    """persist_run-compatible attribute surface (object, no hedge_symbol)."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                     # always BUY
    entry_ts: int
    entry_price: float
    sl: Optional[float]                # PREMIUM stop level
    tp: Optional[float]                # PREMIUM target level
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]         # SL | TP | TIME | CANDLE | EOD
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


def _hhmm(s: str, fallback: int) -> int:
    try:
        h, m = str(s).split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 24 * 60 else fallback
    except (ValueError, AttributeError):
        return fallback


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET


def _merge_cfg(override: Optional[dict]) -> dict:
    cfg = dict(DEFAULTS)
    for k, v in (override or {}).items():
        cfg[k] = v
    _d = str(cfg.get("direction", "BOTH")).upper()
    cfg["direction"] = _d if _d in ("BOTH", "UP", "DOWN") else "BOTH"
    for k in ("sl_mode", "tp_mode"):
        m = str(cfg.get(k, "off")).lower()
        cfg[k] = m if m in ("off", "abs", "pct") else "off"
    cfg["exit_mode"] = ("minutes" if str(cfg.get("exit_mode", "candle")
                                         ).lower() == "minutes" else "candle")
    for k in ("timeframe_minutes", "st_len", "hold_minutes", "warmup_sessions",
              "max_trades_per_day", "lots", "lot_size"):
        try:
            cfg[k] = max(0, int(cfg[k] or 0))
        except (TypeError, ValueError):
            cfg[k] = int(DEFAULTS[k])
    try:
        cfg["strike_offset"] = int(cfg.get("strike_offset") or 0)
    except (TypeError, ValueError):
        cfg["strike_offset"] = 0
    for k in ("st_mult", "sl_value", "tp_value", "premium_min", "premium_max"):
        try:
            cfg[k] = abs(float(cfg[k] or 0.0))
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    cfg["lots"] = cfg["lots"] or 1
    cfg["timeframe_minutes"] = cfg["timeframe_minutes"] or 3
    cfg["st_len"] = cfg["st_len"] or 10
    cfg["st_mult"] = cfg["st_mult"] or 2.0
    cfg["hold_minutes"] = cfg["hold_minutes"] or 5
    cfg["skip_expiry_day"] = bool(cfg.get("skip_expiry_day", False))
    from app.backtest.engine.dte_lots import parse_dte_lot_mult   # ── DTE_LOT_MULT_20260911 ──
    cfg["dte_lot_mult"] = parse_dte_lot_mult(cfg.get("dte_lot_mult"))
    return cfg


def run_stfc_backtest(
    *, db_path: str, strategy_id: str, underlying: str,
    date_from: date, date_to: date,
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


def pick_contract(universe: List[dict], side: str, *, target_strike: float,
                  has_print) -> tuple:
    """R2: the side's contract at target_strike with a print at the entry
    minute; else the nearest strike that has one. Returns (contract, fallback)."""
    cands = [c for c in universe
             if c.get("instrument_type") == side and c.get("strike") is not None]
    exact = [c for c in cands if float(c["strike"]) == target_strike]
    for c in exact:
        if has_print(c["tradingsymbol"]):
            return c, False
    best = None
    for c in sorted(cands, key=lambda x: (abs(float(x["strike"]) - target_strike),
                                          float(x["strike"]))):
        if has_print(c["tradingsymbol"]):
            best = c
            break
    return best, True


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
                      f"STFC_V1 is index-only; no lot constant for {underlying}.")
    lot_size, lot_source = resolve_lot(
        underlying=underlying, is_stock=False, cfg_lot=cfg["lot_size"],
        index_lot=index_lot, db_path=db_path)
    if lot_size is None:
        return _abort(cfg, strategy_id, f"no lot size for {underlying}")
    qty = cfg["lots"] * lot_size

    tf = cfg["timeframe_minutes"]
    block_min = _hhmm(cfg["entry_block_time"], 15 * 60)
    eod_min = _hhmm(cfg["eod_square_off"], 15 * 60 + 20)
    if not (SESSION_OPEN_MIN < block_min <= eod_min <= 15 * 60 + 29):
        return _abort(cfg, strategy_id,
                      f"time order must be 09:15 < entry_block_time "
                      f"{cfg['entry_block_time']} <= eod_square_off "
                      f"{cfg['eod_square_off']} <= 15:29")
    if not (1 <= tf <= 60):
        return _abort(cfg, strategy_id, "timeframe_minutes must be 1..60")
    if not (2 <= cfg["st_len"] <= 200):
        return _abort(cfg, strategy_id, "st_len must be 2..200")
    if not (0.1 <= cfg["st_mult"] <= 20):
        return _abort(cfg, strategy_id, "st_mult must be 0.1..20")
    for k in ("sl", "tp"):
        if cfg[f"{k}_mode"] != "off" and cfg[f"{k}_value"] <= 0:
            return _abort(cfg, strategy_id, f"{k}_mode {cfg[f'{k}_mode']} needs {k}_value > 0")
        if cfg[f"{k}_mode"] == "pct" and cfg[f"{k}_value"] > 100:
            return _abort(cfg, strategy_id,
                          f"{k}_value {cfg[f'{k}_value']} in pct mode looks like rupees")
    if cfg["exit_mode"] == "minutes" and not (1 <= cfg["hold_minutes"] <= 375):
        return _abort(cfg, strategy_id, "hold_minutes must be 1..375")
    if cfg["premium_max"] > 0 and cfg["premium_min"] >= cfg["premium_max"]:
        return _abort(cfg, strategy_id, "premium band needs min < max")
    if abs(cfg["strike_offset"]) > 10:
        return _abort(cfg, strategy_id, "strike_offset must be within ±10 steps")

    src = CandleSource(db_path)
    conn = src._conn()

    def spot_day(ds: int) -> list:
        return [StfcBar(r["ts"], r["open"], r["high"], r["low"], r["close"])
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

    # ── R5: warm the SuperTrend on sessions before date_from ──
    st = SuperTrend(cfg["st_len"], cfg["st_mult"])
    warm_days: List[date] = []
    d = date_from - timedelta(days=1)
    guard = 0
    while len(warm_days) < cfg["warmup_sessions"] and guard < 30:
        if is_trading_day(d):
            warm_days.append(d)
        d -= timedelta(days=1)
        guard += 1
    warmup_bars = 0
    for wd in reversed(warm_days):
        for b in resample_session(spot_day(_day_start_epoch(wd)),
                                  day_start_epoch=_day_start_epoch(wd), tf_minutes=tf):
            st.update(b)
            warmup_bars += 1

    trades: List[STFCTrade] = []
    diag = {
        "days_total": len(days), "days_traded": 0, "days_no_spot": 0,
        "days_uncovered": 0, "days_skipped_expiry": 0, "days_no_signal": 0,
        # ── DTE_LOT_MULT_20260911 ──
        "dte_lot_mult": {str(k): v for k, v in cfg["dte_lot_mult"].items()},
        "dte_skipped_days": 0, "dte_scaled_days": 0, "dte_unknown_days": 0,
        "warmup_bars": warmup_bars, "tf_bars": 0,
        "flips_up": 0, "flips_dn": 0, "arm_lost_eod": 0,
        "signals_total": 0,
        "sig_dropped_open": 0, "sig_dropped_budget": 0,
        "sig_dropped_block_time": 0, "sig_no_spot_bar": 0,
        "sig_no_candidate": 0, "sig_no_fill": 0, "sig_band_reject": 0,
        "strike_fallbacks": 0,
        "entries": 0, "ce_entries": 0, "pe_entries": 0,
        "ce_net": 0.0, "pe_net": 0.0, "entry_hour_hist": {},
        "sl_exits": 0, "tp_exits": 0, "time_exits": 0, "candle_exits": 0,
        "eod_exits": 0,
        "sl_pnl_gross": 0.0, "tp_pnl_gross": 0.0, "time_pnl_gross": 0.0,
        "candle_pnl_gross": 0.0, "eod_pnl_gross": 0.0,
        "hold_sum": 0, "stale_marks": 0,
        "underlying": underlying, "lot_size": lot_size,
        "lot_source": lot_source, "qty": qty,
        "corpus_db": str(db_path).rsplit("/", 1)[-1],
    }

    def close_trade(pos: dict, ts: int, px: float, reason: str) -> None:
        gross = (px - pos["entry_px"]) * pos["qty"]
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
        t.hold_min = int((ts - t.entry_ts) // 60) + 1
        key = reason.lower()
        diag[f"{key}_exits"] += 1
        diag[f"{key}_pnl_gross"] += round(net, 2)
        diag["hold_sum"] += t.hold_min
        diag["ce_net" if t.instrument_type == "CE" else "pe_net"] += round(net, 2)

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
            progress_cb({"day": i + 1, "total_days": len(days),
                         "date": day.isoformat(), "trades": len(trades)})

        ds = _day_start_epoch(day)
        want = expected_expiry_for_day(day).isoformat()
        spot_1m = spot_day(ds)
        if not spot_1m:
            diag["days_no_spot"] += 1
            continue
        spot_by_min = {(b.ts - ds) // 60: b for b in spot_1m}
        bars_tf = resample_session(spot_1m, day_start_epoch=ds, tf_minutes=tf)
        diag["tf_bars"] += len(bars_tf)
        # the indicator ALWAYS advances (R5), even on days we do not trade
        dirs = [st.update(b)[1] for b in bars_tf]

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

        sm: dict = {}
        sigs = session_signals(bars_tf, dirs, direction=cfg["direction"], diag=sm)
        for k in ("flips_up", "flips_dn", "arm_lost_eod"):
            diag[k] += sm.get(k, 0)
        if not sigs:
            diag["days_no_signal"] += 1
            continue
        diag["signals_total"] += len(sigs)

        universe = src.contracts_active_on_day(underlying, ds, expiry=want)
        if not universe:
            diag["days_uncovered"] += 1
            continue
        step = strike_step([c.get("strike") for c in universe])
        bars_by_sym: Dict[str, Dict[int, object]] = {}

        def bars(sym: str) -> Dict[int, object]:
            if sym not in bars_by_sym:
                bars_by_sym[sym] = {c.ts: c for c in
                                    src.candles_1m_for_symbol_day(sym, ds)}
            return bars_by_sym[sym]

        day_trades = 0
        open_until: Optional[int] = None
        traded = False

        for s in sigs:
            tb = bars_tf[s.bar_index]
            entry_min = (tb.ts - ds) // 60                 # R1: trade bar's first minute
            if open_until is not None and entry_min <= open_until:
                diag["sig_dropped_open"] += 1
                continue
            if cfg["max_trades_per_day"] and day_trades >= cfg["max_trades_per_day"]:
                diag["sig_dropped_budget"] += 1
                continue
            if entry_min >= block_min or entry_min >= eod_min:
                diag["sig_dropped_block_time"] += 1
                continue
            sb = spot_by_min.get(entry_min)
            if sb is None:
                diag["sig_no_spot_bar"] += 1
                continue
            entry_spot = float(sb.open)
            fill_ts = ds + entry_min * 60
            sign = 1 if s.side == "CE" else -1
            target_strike = atm_strike(entry_spot, step) + sign * cfg["strike_offset"] * step

            def has_print(sym: str) -> bool:
                b = bars(sym).get(fill_ts)
                return b is not None and bool(b.open)

            mc, fallback = pick_contract(universe, s.side,
                                         target_strike=target_strike, has_print=has_print)
            if mc is None:
                diag["sig_no_candidate"] += 1
                continue
            if fallback:
                diag["strike_fallbacks"] += 1
            sym = mc["tradingsymbol"]
            fb = bars(sym).get(fill_ts)
            if fb is None or not fb.open:
                diag["sig_no_fill"] += 1
                continue
            entry_px = float(fb.open)
            if (cfg["premium_max"] > 0 and entry_px >= cfg["premium_max"]) or \
               (cfg["premium_min"] > 0 and entry_px < cfg["premium_min"]):
                diag["sig_band_reject"] += 1
                continue
            sl_prem = prem_level(entry_px, cfg["sl_mode"], cfg["sl_value"], is_stop=True)
            tp_prem = prem_level(entry_px, cfg["tp_mode"], cfg["tp_value"], is_stop=False)
            hh, mm = entry_min // 60, entry_min % 60
            sl_tag = ("SLoff" if sl_prem is None else
                      f"SL{'P' if cfg['sl_mode'] == 'pct' else 'A'}{cfg['sl_value']:g}")
            tp_tag = ("TPoff" if tp_prem is None else
                      f"TP{'P' if cfg['tp_mode'] == 'pct' else 'A'}{cfg['tp_value']:g}")
            ex_tag = (f"hold{cfg['hold_minutes']}m" if cfg["exit_mode"] == "minutes"
                      else "candle")
            t = STFCTrade(
                tradingsymbol=sym, symbol=sym, instrument_type=s.side,
                strike=float(mc["strike"]) if mc.get("strike") is not None else None,
                expiry=mc.get("expiry"), direction="BUY",
                entry_ts=fill_ts, entry_price=round(entry_px, 2),
                sl=round(sl_prem, 2) if sl_prem is not None else None,
                tp=round(tp_prem, 2) if tp_prem is not None else None,
                exit_ts=None, exit_price=None, exit_reason=None, qty=day_qty,
                entry_spot=round(entry_spot, 2),
                condition=(f"STFC·{s.side}·{hh:02d}:{mm:02d}·{tf}m·ST{cfg['st_len']},"
                           f"{cfg['st_mult']:g}·{sl_tag}·{tp_tag}·{ex_tag}"
                           f"{'·FB' if fallback else ''}"))
            trades.append(t)
            diag["entries"] += 1
            diag["ce_entries" if s.side == "CE" else "pe_entries"] += 1
            hk = f"{hh:02d}"
            diag["entry_hour_hist"][hk] = diag["entry_hour_hist"].get(hk, 0) + 1
            day_trades += 1
            traded = True

            pos = {"trade": t, "entry_px": entry_px, "qty": day_qty,
                   "mae": 0.0, "mfe": 0.0, "last_mark": entry_px}
            ob = bars(sym)
            exit_min = eod_min
            # R3 time boundary: candle mode → trade bar's last minute;
            # minutes mode → the minute completing hold_minutes from entry
            if cfg["exit_mode"] == "minutes":
                time_min = entry_min + cfg["hold_minutes"] - 1
                time_reason = "TIME"
            else:
                time_min = tb.end_min
                time_reason = "CANDLE"

            for m in range(entry_min, eod_min + 1):
                o = ob.get(ds + m * 60)
                if m >= eod_min:
                    px = float(o.close) if o is not None else pos["last_mark"]
                    close_trade(pos, ds + m * 60, px, "EOD")
                    exit_min = m
                    break
                if o is not None:
                    pos["last_mark"] = float(o.close)
                    pos["mae"] = min(pos["mae"], (float(o.low) - entry_px) * day_qty)
                    pos["mfe"] = max(pos["mfe"], (float(o.high) - entry_px) * day_qty)
                    if sl_prem is not None:
                        px = prem_fill(level=sl_prem, bar=o, side_is_stop=True)
                        if px is not None:
                            close_trade(pos, ds + m * 60, px, "SL")
                            exit_min = m
                            break
                    if tp_prem is not None:
                        px = prem_fill(level=tp_prem, bar=o, side_is_stop=False)
                        if px is not None:
                            close_trade(pos, ds + m * 60, px, "TP")
                            exit_min = m
                            break
                else:
                    diag["stale_marks"] += 1
                if m >= time_min:
                    px = float(o.close) if o is not None else pos["last_mark"]
                    close_trade(pos, ds + m * 60, px, time_reason)
                    exit_min = m
                    break
            else:
                last_ts = max(ob) if ob else ds + eod_min * 60
                close_trade(pos, last_ts, pos["last_mark"], "EOD")
                exit_min = eod_min
            open_until = exit_min

        if traded:
            diag["days_traded"] += 1

    src.close()
    if diag["entries"]:
        diag["avg_hold_min"] = round(diag["hold_sum"] / diag["entries"], 1)
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying} {date_from}..{date_to}: "
        f"{summary['total_trades']} trades, net {summary['net_pnl']:,.0f}, "
        f"DD {summary['max_drawdown']:,.0f}, exits SL {diag['sl_exits']} / "
        f"TP {diag['tp_exits']} / TIME {diag['time_exits']} / CANDLE "
        f"{diag['candle_exits']} / EOD {diag['eod_exits']}, skipped-open "
        f"{diag['sig_dropped_open']} of {diag['signals_total']} signals"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}


def _summarize(trades: List[STFCTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_stfc"] = diag
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
        for k in ("sl", "tp", "time", "candle", "eod"):
            diag[f"{k}_pnl_share_pct"] = round(100.0 * diag[f"{k}_pnl_gross"] / net, 1)
    return {
        "total_trades": len(closed), "wins": wins,
        "losses": sum(1 for n in nets if n < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),
        "ambiguous_fills": 0,
        "diag_stfc": diag,
    }
