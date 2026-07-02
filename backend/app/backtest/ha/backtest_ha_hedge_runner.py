# backend/app/backtest/ha/backtest_ha_hedge_runner.py
#
# HA_V2 — HA HEDGE VARIANT backtest runner. A V3-style hedge built on the HA_V1
# signal. Option BUYING on NIFTY weekly options, 1-MINUTE Heikin Ashi candles.
#
# ============================================================================
# THE IDEA (differs from HA_V1 — read before editing):
#
#   HA_V1 fires a signal on a contract (e.g. 23500CE) and BUYS that contract.
#   HA_V2 fires the SAME signal on the SAME contract (the "signal" contract),
#   but instead BUYS the highest-premium OPPOSITE-side contract in the selection
#   (e.g. 23550PE) — the "hedge". This mirrors SCALP_V3's design: track one
#   contract for exit timing, hold another for P&L.
#
#   SIGNAL contract  → tracked for SL/TP; NEVER traded. Drives WHEN to exit.
#   HEDGE contract   → the LONG position actually bought; carries P&L.
#
# ENTRY:
#   * HA signal fires on the signal contract (same HAConditionEvaluator as V1).
#   * signal levels: entry_ltp / sl (red-candle low) / tp (RR or override) —
#     all computed on the SIGNAL contract exactly as HA_V1 does.
#   * hedge = highest-premium opposite-side contract in the active selection
#     snapshot at the signal bar (mirrors scalp_v3 _pick_hedge). Hedge entry
#     price = hedge contract's CLOSE on the signal bar.
#
# EXIT (FIXED-LEVEL model — the hedge is tracked as its OWN long trade):
#   The signal supplies only the RISK GEOMETRY (point distances):
#       sl_distance = signal_entry - signal_sl
#       tp_distance = sl_distance * RR   (or the fixed target-override points)
#   These distances are applied DIRECTLY to the hedge entry:
#       hedge_sl = hedge_entry - sl_distance
#       hedge_tp = hedge_entry + tp_distance
#   After entry the signal is IGNORED. The hedge is tracked against its own
#   fixed levels, INTRABAR, and the exit is booked AT THE LEVEL (no haircut):
#   * TP  — hedge HIGH >= hedge_tp → exit AT hedge_tp.
#   * SL  — hedge LOW  <= hedge_sl → exit AT hedge_sl.
#   * Ambiguous bar (hedge high>=tp AND hedge low<=sl in one 1m) → pessimistic
#           SL-first, flagged ambiguous, exit AT hedge_sl.
#   * EOD — neither level hit → exit at the hedge's OWN last close.
#   Guard: if hedge_sl <= 0 (sl_distance > hedge premium) the entry is rejected.
#
# P&L: LONG hedge = (hedge_exit - hedge_entry) * qty, qty = lots * 65.
#
# ============================================================================
# CAVEAT — NOT LIVE-WIRED. This is a BACKTEST-ONLY experiment (HA_V2). There is
# no live HA_V2 engine yet; numbers here are exploratory. The exit uses 1m OHLC
# (signal high for TP, signal close for SL) — same 1m-bar limitations as HA_V1.
#
# ============================================================================
# CONCURRENCY — SINGLE GLOBAL open trade (same as the HA_V1 runner). Same-1m
# arbitration across BOTH sides elects the highest SIGNAL entry premium. The
# per-side daily cap (max_trades_per_side) is counted on the SIGNAL side via the
# real HASignalEngine (live counting). trade_side_mode gates the SIGNAL side;
# the hedge is always the opposite side.
#
# Read-only on the corpus. Reuses the SAME backtest_selector.py, HA signal
# engine, and charges resolver as the HA_V1 runner.

from __future__ import annotations

try:
    import app.backtest.data.candle_source  # noqa: F401
    import app.backtest.engine.backtest_selector  # noqa: F401
except Exception:
    pass

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
LOT_SIZE = 65
STRIKE_STEP = 50
TIMEFRAME_SEC = 60
EMA_PERIOD = 20
WARMUP_CANDLES = 100
SL_SLIPPAGE = 0.98      # hedge exit haircut (we SELL to exit the LONG hedge)


@dataclass
class HAV2Trade:
    # ── signal contract (tracked, never traded) ──
    signal_side: str                 # CE | PE (the side the signal fired on)
    signal_symbol: str
    signal_strike: float
    signal_entry_price: float        # signal entry_ltp (for reference)
    signal_sl: float
    signal_tp: float
    # ── hedge contract (bought, LONG, carries P&L) ──
    side: str                        # hedge side (opposite of signal) — "side"
                                     # name kept so persist_run/serializer match
    symbol: str                      # hedge symbol (the traded instrument)
    strike: float                    # hedge strike
    entry_ts: int
    entry_price: float               # hedge entry (PE close of signal bar)
    sl: Optional[float] = None       # not used for hedge exit (signal-driven);
    tp: Optional[float] = None       #   kept for serializer compatibility
    qty: int = 0
    condition: Optional[str] = None
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross: Optional[float] = None
    charges: Optional[float] = None
    net: Optional[float] = None
    ambiguous: bool = False
    instrument_type: str = "CE"      # hedge side (mirrors `side`)
    expiry: str = ""
    direction: str = "LONG"
    max_adverse: float = 0.0
    max_favorable: float = 0.0

    @property
    def pnl(self) -> Optional[float]:
        return self.gross

    @property
    def net_pnl(self) -> Optional[float]:
        return self.net

    @property
    def ambiguous_fill(self) -> bool:
        return self.ambiguous

    # ── HEDGE-DISPLAY ALIASES ──
    # persist_run detects a hedge trade via hasattr(t, "hedge_symbol") and then
    # takes the V3/V4-style branch that stores BOTH the hedge (traded) contract
    # AND the signal contract + its SL/TP into the hedge-specific columns
    # (signal_symbol/signal_side/signal_sl/signal_tp/hedge_side). Exposing these
    # two aliases makes HA_V2 rows persist + render exactly like V3 (Signal +
    # Hedge columns, signal SL/TP shown). The hedge itself has no SL/TP leg
    # (exit is signal-driven), so t.sl/t.tp stay None — the "Hedge SL" column is
    # intentionally blank. No P&L math changes; these are read-only views.
    @property
    def hedge_symbol(self) -> str:
        return self.symbol

    @property
    def hedge_side(self) -> str:
        return self.side


# ----------------------------------------------------------------------
# Helpers (shared shape with the HA_V1 runner)
# ----------------------------------------------------------------------
def _ist_day(ep: int) -> date:
    return datetime.fromtimestamp(ep, IST).date()


def _day_bounds(d: date) -> Tuple[int, int]:
    lo = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return lo, lo + 86400


def _hm(ep: int) -> str:
    dt = datetime.fromtimestamp(ep, IST)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _in_session(ep: int, start_hm: str, end_hm: str) -> bool:
    hm = _hm(ep)
    return start_hm <= hm <= end_hm


def _empty_summary() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
        "max_drawdown": 0.0, "ambiguous_fills": 0,
    }


def _snapshot_symbols(snap: List[dict], side: Optional[str] = None) -> set:
    out = set()
    for o in snap or []:
        if side is not None and o.get("type") != side:
            continue
        sym = o.get("tradingsymbol") or o.get("symbol")
        if sym:
            out.add(sym)
    return out


def _snapshot_side_rows(snap: List[dict], side: str) -> List[dict]:
    """Selection rows for one side, so we can pick the highest-premium hedge."""
    return [o for o in (snap or []) if o.get("type") == side]


class _HAState:
    """Mirrors the HA_V1 runner's _HAState (heikin_ashi + EMA + evaluator)."""
    def __init__(self, symbol: str):
        from app.indicators.heikin_ashi import HeikinAshiConverter
        from app.indicators.ema import EMA
        from app.engine.ha_options.ha_signal_engine import HAConditionEvaluator
        self.symbol = symbol
        self.ha_converter = HeikinAshiConverter()
        self._ema_low = EMA(EMA_PERIOD)
        self.ema_low_value: Optional[float] = None
        self.evaluator = HAConditionEvaluator()
        self.last_ha = None

    def warmup(self, bars_1m: List[dict]) -> None:
        for b in bars_1m:
            try:
                ha = self.ha_converter.update(
                    ts=int(b["ts"]), o=float(b["open"]), h=float(b["high"]),
                    l=float(b["low"]), c=float(b["close"]),
                )
            except ValueError:
                continue
            ema_val = self._ema_low.update(ha.low)
            self.ema_low_value = ema_val
            self.evaluator.push(ha, ema_val)
            self.last_ha = ha

    def on_bar(self, b: dict):
        ha = self.ha_converter.update(
            ts=int(b["ts"]), o=float(b["open"]), h=float(b["high"]),
            l=float(b["low"]), c=float(b["close"]),
        )
        ema_val = self._ema_low.update(ha.low)
        self.ema_low_value = ema_val
        self.last_ha = ha
        signal = self.evaluator.push(ha, ema_val)
        return ha, ema_val, signal


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run_ha_v2_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "HA_V2"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Public entry — mutes audit logging for the duration of the replay."""
    # ── AUDIT_MUTE BEGIN ──
    from app.event_bus.audit_logger import audit_muted
    with audit_muted():
        return _run_ha_v2_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )
    # ── AUDIT_MUTE END ──


def _run_ha_v2_backtest_impl(
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
    """HA_V2 hedge-variant backtest. Signal on one side, buy the opposite-side
    hedge; exit driven by the signal contract's SL/TP, priced on the hedge.

    config keys: identical to HA_V1 (option_premium, risk_reward_ratio,
    target_override, session, quantity, trade_side_mode, max_trades_per_side,
    min_sl_points, max_loss, max_profit). trade_side_mode gates the SIGNAL side.
    """
    from app.engine.ha_options.ha_signal_engine import HASignalEngine
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts,
    )
    charges_for_long_trade = _resolve_charges_fn()

    cfg = config_override or {}
    prem = cfg.get("option_premium", {}) or {}
    prem_min = float(prem.get("min", 0) or 0)
    prem_max = float(prem.get("max", 1e9) or 1e9)
    rr = float(cfg.get("risk_reward_ratio", 2.0) or 2.0)
    override = cfg.get("target_override", {}) or {}
    override_on = bool(override.get("enabled")) and float(override.get("points", 0) or 0) > 0
    override_pts = float(override.get("points", 0) or 0)
    lots = int((cfg.get("quantity", {}) or {}).get("lots", 1) or 1)
    qty = lots * LOT_SIZE
    sess = ((cfg.get("session", {}) or {}).get("primary", {}) or {})
    sess_start = sess.get("start", "09:15")
    sess_end = sess.get("end", "15:20")
    side_mode = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()
    max_trades_per_side = int(cfg.get("max_trades_per_side", 10) or 10)
    max_loss = abs(float(cfg.get("max_loss", 0) or 0))
    max_profit = abs(float(cfg.get("max_profit", 0) or 0))
    min_sl = abs(float(cfg.get("min_sl_points", 0) or 0))

    sel_cfg = {
        "option_premium": {"min": prem_min, "max": prem_max},
        "trade_side_mode": "BOTH",
    }

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    lo_all, hi_all = _day_bounds(date_from)[0], _day_bounds(date_to)[1]
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

    trades: List[HAV2Trade] = []
    total_days = len(sim_days)

    _diag = {
        "sim_days": total_days, "days_with_data": 0, "days_uncovered": 0,
        "contracts_seen": 0, "signals": 0, "accepted": 0,
        "arb_contests": 0, "arb_dropped": 0,
        "rej_single_gate": 0, "rej_session": 0, "rej_side_mode": 0,
        "rej_not_selected": 0, "rej_mtm_block": 0, "rej_cap": 0,
        "rej_sl_ge_ltp": 0, "rej_no_sl": 0, "rej_min_sl": 0,
        "rej_no_hedge": 0, "rej_no_hedge_data": 0, "rej_bad_hedge_sl": 0,
        "exit_tp": 0, "exit_sl": 0, "exit_eod": 0, "exit_ambiguous": 0,
        "hedge_carry_fwd": 0, "mtm_exits": 0, "day_mtm_blocked": 0,
        "prem_seen_min": None, "prem_seen_max": None,
    }

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break
        lo, hi = _day_bounds(d)

        realised_running = 0.0
        day_blocked = False

        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=lo,
            cfg=sel_cfg, strategy_id=strategy_id,
        )
        if not timeline.get("covered"):
            _diag["days_uncovered"] += 1
            continue

        watched = timeline.get("all_symbols") or set()
        if not watched:
            continue
        _diag["days_with_data"] += 1
        current_expiry = timeline.get("expected_expiry")

        meta_map = {
            c["tradingsymbol"]: {"side": c["instrument_type"], "strike": float(c["strike"])}
            for c in src.contracts_active_on_day(underlying, lo)
        }

        signal_engine = HASignalEngine(max_trades_per_side=max_trades_per_side)

        # Build per-symbol HA state + per-symbol ts→bar lookup (need hedge bars
        # priced at arbitrary ts while a trade is open).
        states: Dict[str, _HAState] = {}
        one_min_by_sym: Dict[str, List[dict]] = {}
        bars_by_sym_ts: Dict[str, Dict[int, dict]] = {}

        for sym in sorted(watched):
            day_candles = src.candles_1m_for_symbol_day(sym, lo)
            if not day_candles:
                continue
            if sym not in meta_map:
                continue
            bars_1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in day_candles]
            st = _HAState(sym)
            warm = src.warmup_candles_before(sym, day_candles[0].ts, WARMUP_CANDLES)
            if warm:
                w1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in warm]
                st.warmup(w1m)
            states[sym] = st
            one_min_by_sym[sym] = bars_1m
            bars_by_sym_ts[sym] = {b["ts"]: b for b in bars_1m}

        if not states:
            continue
        _diag["contracts_seen"] += len(states)

        by_bucket: Dict[int, List[Tuple[str, dict]]] = {}
        for sym, bars in one_min_by_sym.items():
            for b in bars:
                by_bucket.setdefault(b["ts"], []).append((sym, b))
        ordered_buckets = sorted(by_bucket.keys())

        open_trade: Optional[HAV2Trade] = None
        hedge_last_close: Optional[float] = None   # carry-forward for missing hedge bars

        for bucket_start in ordered_buckets:
            if cancel_cb and cancel_cb():
                break

            items = sorted(by_bucket[bucket_start], key=lambda t: t[0])
            snap_end_ts = bucket_start + TIMEFRAME_SEC
            snap = active_snapshot_for_ts(timeline, snap_end_ts)
            sel_ce = _snapshot_symbols(snap, "CE")
            sel_pe = _snapshot_symbols(snap, "PE")
            locked_sym = open_trade.signal_symbol if open_trade is not None else None

            # ── Advance HA state for EVERY watched symbol this bar ──
            signals_this_bar: Dict[str, object] = {}
            for sym, b1 in items:
                _ha, _ema, sig = states[sym].on_bar(b1)
                signals_this_bar[sym] = sig

            # ── If a trade is open: check exit on the SIGNAL contract ──
            if open_trade is not None:
                sig_bar = bars_by_sym_ts.get(open_trade.signal_symbol, {}).get(bucket_start)
                hedge_bar = bars_by_sym_ts.get(open_trade.symbol, {}).get(bucket_start)
                if hedge_bar is not None:
                    hedge_last_close = float(hedge_bar["close"])

                # NEW EXIT MODEL: the hedge is tracked as its OWN long trade
                # against FIXED levels (hedge_sl/hedge_tp = hedge_entry -/+ the
                # signal's point distances). The signal is NOT consulted after
                # entry. TP/SL trigger INTRABAR on the hedge's own high/low, and
                # the exit is booked AT THE LEVEL (no candle-close, no haircut) —
                # per the fixed-level design.
                if hedge_bar is not None:
                    hedge_last_close = float(hedge_bar["close"])

                    # ── MTM force-close parity (hedge open MTM at hedge close) ──
                    if (max_loss > 0 or max_profit > 0):
                        open_gross = (hedge_last_close - open_trade.entry_price) * open_trade.qty
                        mtm_now = realised_running + open_gross
                        if (max_loss > 0 and mtm_now <= -max_loss) or \
                           (max_profit > 0 and mtm_now >= max_profit):
                            reason = "MAX_LOSS" if (max_loss > 0 and mtm_now <= -max_loss) else "MAX_PROFIT"
                            _close_hedge_at(open_trade, exit_ts=snap_end_ts,
                                            exit_price=hedge_last_close, reason=reason,
                                            charges_fn=charges_for_long_trade)
                            realised_running += (open_trade.net or 0.0)
                            _diag["mtm_exits"] += 1
                            signal_engine.notify_exit(open_trade.signal_side)
                            trades.append(open_trade)
                            open_trade = None
                            hedge_last_close = None
                            day_blocked = True
                            break

                    h_hi = float(hedge_bar["high"])
                    h_lo = float(hedge_bar["low"])
                    hit_tp = open_trade.tp is not None and h_hi >= float(open_trade.tp)
                    hit_sl = open_trade.sl is not None and h_lo <= float(open_trade.sl)

                    exited = False
                    if hit_tp and hit_sl:
                        # Both levels inside one 1m bar — can't order from OHLC.
                        # Pessimistic SL-first, flagged. Exit AT the SL level.
                        open_trade.ambiguous = True
                        _diag["exit_ambiguous"] += 1
                        _close_hedge_at(open_trade, exit_ts=snap_end_ts,
                                        exit_price=float(open_trade.sl), reason="SL",
                                        charges_fn=charges_for_long_trade)
                        _diag["exit_sl"] += 1
                        exited = True
                    elif hit_tp:
                        # Book AT the TP level (no haircut — fixed level fill).
                        _close_hedge_at(open_trade, exit_ts=snap_end_ts,
                                        exit_price=float(open_trade.tp), reason="TP",
                                        charges_fn=charges_for_long_trade)
                        _diag["exit_tp"] += 1
                        exited = True
                    elif hit_sl:
                        # Book AT the SL level (no haircut — fixed level fill).
                        _close_hedge_at(open_trade, exit_ts=snap_end_ts,
                                        exit_price=float(open_trade.sl), reason="SL",
                                        charges_fn=charges_for_long_trade)
                        _diag["exit_sl"] += 1
                        exited = True

                    if exited:
                        realised_running += (open_trade.net or 0.0)
                        signal_engine.notify_exit(open_trade.signal_side)
                        trades.append(open_trade)
                        open_trade = None
                        hedge_last_close = None
                        if _day_cap_hit(realised_running, max_loss, max_profit):
                            day_blocked = True
                            break
                elif hedge_last_close is not None:
                    # Hedge has no bar this minute — carry the last known close so
                    # MTM/EOD still have a price. No level check without a bar.
                    _diag["hedge_carry_fwd"] += 1


            # ── Entry evaluation (only when flat — global gate) ──
            if open_trade is not None:
                continue

            entry_candidates: List[Tuple[float, str, dict]] = []
            for sym, b1 in items:
                sig = signals_this_bar.get(sym)
                if sig is None or not sig.should_enter:
                    continue
                _diag["signals"] += 1

                sig_entry_ltp = float(b1["close"])
                if _diag["prem_seen_min"] is None or sig_entry_ltp < _diag["prem_seen_min"]:
                    _diag["prem_seen_min"] = round(sig_entry_ltp, 2)
                if _diag["prem_seen_max"] is None or sig_entry_ltp > _diag["prem_seen_max"]:
                    _diag["prem_seen_max"] = round(sig_entry_ltp, 2)

                if not _in_session(snap_end_ts, sess_start, sess_end):
                    _diag["rej_session"] += 1
                    continue

                sig_side = meta_map[sym]["side"]
                if side_mode in ("CE", "PE") and side_mode != sig_side:
                    _diag["rej_side_mode"] += 1
                    continue

                in_selected = (sym in sel_ce) if sig_side == "CE" else (sym in sel_pe)
                if not in_selected and sym != locked_sym:
                    _diag["rej_not_selected"] += 1
                    continue

                allowed, _reason = signal_engine.can_enter(sig_side)
                if not allowed:
                    _diag["rej_cap"] += 1
                    continue

                if sig.sl_price is None:
                    _diag["rej_no_sl"] += 1
                    continue
                if sig.sl_price >= sig_entry_ltp:
                    _diag["rej_sl_ge_ltp"] += 1
                    continue
                if min_sl > 0 and (sig_entry_ltp - float(sig.sl_price)) < min_sl:
                    _diag["rej_min_sl"] += 1
                    continue

                if day_blocked or _day_cap_hit(realised_running, max_loss, max_profit):
                    day_blocked = True
                    _diag["rej_mtm_block"] += 1
                    continue

                sl_price = float(sig.sl_price)
                # RISK GEOMETRY from the SIGNAL: distances only. The hedge below
                # inherits these POINT distances (not the signal's price levels).
                sl_distance = sig_entry_ltp - sl_price          # signal risk (pts)
                tp_distance = override_pts if override_on else (sl_distance * rr)
                # (signal tp/sl levels kept only for reference/display)
                sig_tp_level = (sig_entry_ltp + override_pts) if override_on else (sig_entry_ltp + sl_distance * rr)

                # ── Pick the HEDGE: highest-premium OPPOSITE-side selected
                #    contract with a bar on THIS ts (so we can price entry). ──
                hedge_side = "PE" if sig_side == "CE" else "CE"
                hedge_rows = _snapshot_side_rows(snap, hedge_side)
                hedge = _pick_hedge(hedge_rows, bars_by_sym_ts, bucket_start, meta_map)
                if hedge is None:
                    _diag["rej_no_hedge"] += 1
                    continue
                hedge_sym, hedge_strike, hedge_entry = hedge
                if hedge_entry is None:
                    _diag["rej_no_hedge_data"] += 1
                    continue

                # APPLY the signal's point distances DIRECTLY to the hedge entry.
                # hedge_sl = hedge_entry - sl_distance ; hedge_tp = hedge_entry + tp_distance
                hedge_sl_level = round(round((hedge_entry - sl_distance) / 0.05) * 0.05, 2) #sl_distance
                hedge_tp_level = round(round((hedge_entry + tp_distance) / 0.05) * 0.05, 2) #tp_distance
                # Guard: an SL distance larger than the hedge premium yields a
                # non-positive stop — impossible to hold. Reject the entry.
                if hedge_sl_level <= 0:
                    _diag["rej_bad_hedge_sl"] += 1
                    continue

                entry_candidates.append((sig_entry_ltp, sym, {
                    "sig_side": sig_side, "sig_strike": meta_map[sym]["strike"],
                    "sig_entry": sig_entry_ltp, "sl": sl_price, "tp": sig_tp_level,
                    "condition": sig.condition,
                    "hedge_side": hedge_side, "hedge_symbol": hedge_sym,
                    "hedge_strike": hedge_strike, "hedge_entry": hedge_entry,
                    "hedge_sl": hedge_sl_level, "hedge_tp": hedge_tp_level,
                    "entry_ts": snap_end_ts,
                }))

            if open_trade is None and entry_candidates:
                if len(entry_candidates) > 1:
                    _diag["arb_contests"] += 1
                    _diag["arb_dropped"] += (len(entry_candidates) - 1)
                # elect highest SIGNAL premium (symbol tie-break), like V1/V3.
                winner = max(entry_candidates, key=lambda c: (c[0], c[1]))
                _ep, _sym, ctx = winner
                _diag["accepted"] += 1
                signal_engine.confirm_entry(ctx["sig_side"])
                open_trade = HAV2Trade(
                    signal_side=ctx["sig_side"], signal_symbol=_sym,
                    signal_strike=ctx["sig_strike"], signal_entry_price=ctx["sig_entry"],
                    signal_sl=ctx["sl"], signal_tp=ctx["tp"],
                    side=ctx["hedge_side"], symbol=ctx["hedge_symbol"],
                    strike=ctx["hedge_strike"], entry_ts=ctx["entry_ts"],
                    entry_price=ctx["hedge_entry"], qty=qty,
                    sl=ctx["hedge_sl"], tp=ctx["hedge_tp"],
                    condition=ctx["condition"],
                    instrument_type=ctx["hedge_side"], expiry=current_expiry,
                    direction="LONG",
                )
                hedge_last_close = ctx["hedge_entry"]

            if open_trade is None and (day_blocked or _day_cap_hit(realised_running, max_loss, max_profit)):
                day_blocked = True
                _diag["day_mtm_blocked"] += 1
                break

        # EOD — neither hedge TP nor hedge SL hit: exit at the hedge's OWN last close.
        if open_trade is not None:
            hedge_bars = one_min_by_sym.get(open_trade.symbol)
            if hedge_bars:
                last = hedge_bars[-1]
                _close_hedge(open_trade, exit_ts=int(last["ts"]) + TIMEFRAME_SEC,
                             hedge_exit_raw=float(last["close"]), reason="EOD",
                             charges_fn=charges_for_long_trade, haircut=1.0)  # no haircut on EOD close
                _diag["exit_eod"] += 1
                realised_running += (open_trade.net or 0.0)
                signal_engine.notify_exit(open_trade.signal_side)
                trades.append(open_trade)
            elif hedge_last_close is not None:
                _close_hedge(open_trade, exit_ts=bucket_start + TIMEFRAME_SEC,
                             hedge_exit_raw=hedge_last_close, reason="EOD",
                             charges_fn=charges_for_long_trade, haircut=1.0)
                _diag["exit_eod"] += 1
                realised_running += (open_trade.net or 0.0)
                signal_engine.notify_exit(open_trade.signal_side)
                trades.append(open_trade)
            open_trade = None
            hedge_last_close = None

        if progress_cb:
            progress_cb({"day": di, "total_days": total_days, "date": d.isoformat(),
                         "trades": len(trades)})

    try:
        src.close()
    except Exception:
        pass
    conn.close()

    summary = _summarize(trades)
    summary["diagnostics"] = _diag
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(
            "[BACKTEST][HA_V2][DIAG] "
            f"days={_diag['sim_days']} with_data={_diag['days_with_data']} "
            f"contracts={_diag['contracts_seen']} signals={_diag['signals']} "
            f"accepted={_diag['accepted']} | exits: tp={_diag['exit_tp']} "
            f"sl={_diag['exit_sl']} eod={_diag['exit_eod']} amb={_diag['exit_ambiguous']} "
            f"| rej: no_hedge={_diag['rej_no_hedge']} no_hedge_data={_diag['rej_no_hedge_data']} "
            f"bad_hedge_sl={_diag['rej_bad_hedge_sl']} "
            f"cap={_diag['rej_cap']} not_sel={_diag['rej_not_selected']} "
            f"session={_diag['rej_session']} carry_fwd={_diag['hedge_carry_fwd']} "
            f"| signal_prem={_diag['prem_seen_min']}..{_diag['prem_seen_max']}"
        )
    except Exception:
        pass

    import uuid as _uuid
    return {
        "run_id": str(_uuid.uuid4()),
        "strategy_id": strategy_id,
        "config": cfg,
        "summary": summary,
        "trades": trades,
    }


# ----------------------------------------------------------------------
# Hedge selection + exit helpers
# ----------------------------------------------------------------------
def _pick_hedge(hedge_rows, bars_by_sym_ts, bucket_start, meta_map):
    """Highest-premium opposite-side selected contract that HAS a bar at this ts.
    Premium source: the bar's CLOSE at bucket_start (the hedge entry price).
    Returns (symbol, strike, entry_price) or None if no priced candidate.
    Mirrors scalp_v3 _pick_hedge (highest premium)."""
    best = None
    for r in hedge_rows:
        sym = r.get("tradingsymbol") or r.get("symbol")
        if not sym:
            continue
        bar = bars_by_sym_ts.get(sym, {}).get(bucket_start)
        if bar is None:
            continue
        prem = float(bar["close"])
        if prem <= 0:
            continue
        strike = float(meta_map.get(sym, {}).get("strike", 0.0))
        if best is None or prem > best[2]:
            best = (sym, strike, prem)
    return best


def _close_hedge(trade: HAV2Trade, *, exit_ts: int, hedge_exit_raw: float,
                 reason: str, charges_fn, haircut: float = 1.0) -> None:
    """Close the LONG hedge. exit_price = hedge_exit_raw * haircut, tick-rounded.
    P&L LONG = (exit - entry) * qty. Charges via charges_for_long_trade."""
    exit_price = round(round((float(hedge_exit_raw) * haircut) / 0.05) * 0.05, 2)
    trade.exit_ts = exit_ts
    trade.exit_price = exit_price
    trade.exit_reason = reason
    gross = (exit_price - trade.entry_price) * trade.qty
    charges = 0.0
    if charges_fn is not None:
        try:
            res = charges_fn(entry_price=trade.entry_price, exit_price=exit_price, qty=trade.qty)
            charges = float(getattr(res, "total_charges", 0.0))
            gross = float(getattr(res, "gross_pnl", gross))
        except Exception:
            charges = 0.0
    trade.gross = round(gross, 2)
    trade.charges = round(charges, 2)
    trade.net = round(gross - charges, 2)


def _close_hedge_at(trade: HAV2Trade, *, exit_ts: int, exit_price: float,
                    reason: str, charges_fn) -> None:
    """Close the LONG hedge AT an exact price (tick-rounded), no slippage haircut.
    Used by the fixed-level exit model: TP/SL are booked at the level itself when
    the hedge's intrabar high/low touches it. P&L LONG = (exit - entry) * qty."""
    exit_price = round(round(float(exit_price) / 0.05) * 0.05, 2)
    trade.exit_ts = exit_ts
    trade.exit_price = exit_price
    trade.exit_reason = reason
    gross = (exit_price - trade.entry_price) * trade.qty
    charges = 0.0
    if charges_fn is not None:
        try:
            res = charges_fn(entry_price=trade.entry_price, exit_price=exit_price, qty=trade.qty)
            charges = float(getattr(res, "total_charges", 0.0))
            gross = float(getattr(res, "gross_pnl", gross))
        except Exception:
            charges = 0.0
    trade.gross = round(gross, 2)
    trade.charges = round(charges, 2)
    trade.net = round(gross - charges, 2)


def _day_cap_hit(realised_net_today: float, max_loss: float, max_profit: float) -> bool:
    if max_loss > 0 and realised_net_today <= -max_loss:
        return True
    if max_profit > 0 and realised_net_today >= max_profit:
        return True
    return False


_CHARGES_PATHS = (
    "app.backtest.charges.charges_model",
    "app.backtest.data.charges_model",
    "app.backtest.engine.charges_model",
    "app.backtest.charges_model",
)
_CHARGES_FN = None
_CHARGES_RESOLVED = False


def _resolve_charges_fn():
    global _CHARGES_FN, _CHARGES_RESOLVED
    if _CHARGES_RESOLVED:
        return _CHARGES_FN
    _CHARGES_RESOLVED = True
    import importlib
    for path in _CHARGES_PATHS:
        try:
            mod = importlib.import_module(path)
            fn = getattr(mod, "charges_for_long_trade", None)
            if fn is not None:
                _CHARGES_FN = fn
                try:
                    from app.event_bus.audit_logger import write_audit_log
                    write_audit_log(f"[BACKTEST][HA_V2][CHARGES] using {path}.charges_for_long_trade")
                except Exception:
                    pass
                return _CHARGES_FN
        except Exception:
            continue
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(
            "[BACKTEST][HA_V2][CHARGES][WARN] charges_for_long_trade NOT FOUND — "
            "charges will be ZERO."
        )
    except Exception:
        pass
    _CHARGES_FN = None
    return _CHARGES_FN


# ----------------------------------------------------------------------
# Summary + serialization
# ----------------------------------------------------------------------
def _trade_to_dict(t: HAV2Trade) -> dict:
    return {
        "tradingsymbol": t.symbol,          # hedge symbol (traded)
        "signal_symbol": t.signal_symbol,   # signal contract (tracked)
        "signal_side": t.signal_side,
        "side": t.side,                     # hedge side
        "strike": t.strike,
        "entry_ts": t.entry_ts,
        "entry_price": t.entry_price,
        "sl": t.signal_sl,                  # show the SIGNAL levels for context
        "tp": t.signal_tp,
        "qty": t.qty,
        "condition": t.condition,
        "exit_ts": t.exit_ts,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "pnl": t.gross,
        "charges": t.charges,
        "net_pnl": t.net,
        "ambiguous_fill": t.ambiguous,
    }


def _summarize(trades: List[HAV2Trade]) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return _empty_summary()
    nets = [t.net or 0.0 for t in closed]
    gross = sum(t.gross or 0.0 for t in closed)
    charges = sum(t.charges or 0.0 for t in closed)
    net = sum(nets)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    amb = sum(1 for t in closed if t.ambiguous)

    eq = 0.0; peak = 0.0; mdd = 0.0
    for t in sorted(closed, key=lambda x: x.entry_ts or 0):
        eq += (t.net or 0.0)
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)

    return {
        "total_trades": len(closed),
        "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2) if closed else 0.0,
        "gross_pnl": round(gross, 2),
        "total_charges": round(charges, 2),
        "net_pnl": round(net, 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": amb,
    }