# backend/app/backtest/bb/backtest_bb_runner.py
#
# BB_V1 / BB_V2 backtest runner. Option BUYING on BANKNIFTY.
#
# FLOW (mirrors the live BBTradeManager, on corpus data):
#   1) Drive BANKNIFTYFUT 3m bars through the REAL indicator bundle + confluence
#      signal engine (bt_indicator_driver) → ENTER_CE/PE, EXIT_CE/PE per candle.
#   2) On ENTER: select an option leg (side CE/PE) around ATM whose premium fits
#      max_premium, BUY at that minute's option close.
#   3) Manage the long option: SL = fill*(1-sl_pct/100), TP = fill*(1+tp_pct/100).
#      Exit on the FIRST of: ST exit signal (EXIT_CE/PE) at a later 3m bar,
#      SL hit (option 1m low <= sl), TP hit (option 1m high >= tp), or EOD 15:25.
#      Ambiguous SL+TP in the same 1m → pessimistic SL-first (+ flag).
#   4) Charges via the corpus charges model (BUY then SELL). P&L = (exit-entry)*qty.
#
# Read-only on the corpus. Reuses: CandleSource (option premiums + FUT spot),
# bt_candle_agg, bt_pivots, bt_indicator_driver, expiry calendar (BANKNIFTY
# monthly), charges_model.

from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
FUT_SYMBOL = "BANKNIFTYFUT"
LOT_SIZE = 30
STRIKE_STEP = 100
WARMUP_DAYS = 4          # prior trading days fed for indicator convergence


@dataclass
class BBTrade:
    side: str                  # CE | PE
    symbol: str
    strike: int
    entry_ts: int
    entry_price: float
    sl: float
    tp: float
    qty: int
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross: Optional[float] = None
    charges: Optional[float] = None
    net: Optional[float] = None
    ambiguous: bool = False


def _ist_day(ep: int) -> date:
    return datetime.fromtimestamp(ep, IST).date()


def _day_bounds(d: date):
    lo = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return lo, lo + 86400


def _hm(ep: int) -> str:
    dt = datetime.fromtimestamp(ep, IST)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def run_bb_backtest(
    *,
    db_path: str,
    strategy_id: str,           # BB_V1 | BB_V2
    date_from: date,
    date_to: date,
    config: dict,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Run a BB backtest. config keys: max_premium, sl_pct, tp_pct, lots,
    session_start, session_end, max_trades_per_side, scan_strikes."""
    from app.backtest.bb.bt_candle_agg import Bar, aggregate_1m_to_3m
    from app.backtest.bb.bt_pivots import pivots_for_day
    from app.backtest.bb.bt_indicator_driver import BBSignalReplay
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day  # monthly for BNF
    try:
        from app.backtest.charges.charges_model import charges_for_long_trade
    except Exception:
        charges_for_long_trade = None

    max_premium = float(config.get("max_premium", 300))
    sl_pct = float(config.get("sl_pct", 0))
    tp_pct = float(config.get("tp_pct", 0))
    lots = int(config.get("lots", 1))
    qty = lots * LOT_SIZE
    sess_start = config.get("session_start", "09:15")
    sess_end = config.get("session_end", "15:15")
    max_tps = int(config.get("max_trades_per_side", 10))
    scan = int(config.get("scan_strikes", 60))

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Build the list of sim days that have FUT data.
    rows = cur.execute(
        """
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d
        FROM backtest_candles_1m WHERE tradingsymbol = ?
          AND ts >= ? AND ts < ?
        ORDER BY d
        """,
        (FUT_SYMBOL, _day_bounds(date_from)[0], _day_bounds(date_to)[1]),
    ).fetchall()
    sim_days = [date.fromisoformat(r["d"]) for r in rows]
    if not sim_days:
        conn.close()
        return {"aborted": True, "reason": "no BANKNIFTYFUT data in range",
                "trades": [], "summary": _empty_summary()}

    driver = BBSignalReplay(strategy_id, FUT_SYMBOL, max_trades_per_side=max_tps)

    # --- WARMUP: feed prior days' 3m bars (no acting) so indicators converge ---
    warmup_from = sim_days[0] - timedelta(days=WARMUP_DAYS * 3)  # generous lookback
    _feed_fut_3m(cur, driver, warmup_from, sim_days[0] - timedelta(days=1), act=False)

    trades: List[BBTrade] = []
    days_total = len(sim_days)

    for di, sim_day in enumerate(sim_days):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di + 1, "total_days": days_total,
                         "date": sim_day.isoformat(), "trades": len(trades)})

        # per-day pivots + daily reset (matches live 09:15)
        piv = pivots_for_day(cur.connection if hasattr(cur, "connection") else conn, sim_day)
        driver.set_day_pivots(piv)
        driver.reset_daily()

        expiry = expected_expiry_for_day(sim_day)   # BANKNIFTY monthly expiry

        # feed this day's FUT 3m bars; act on signals
        bars = _fut_3m_for_day(cur, sim_day)
        open_trade: Optional[BBTrade] = None

        for bar in bars:
            hm = _hm(bar.start_ts)
            # EOD square-off
            if open_trade and hm >= "15:25":
                _close_trade(cur, open_trade, bar.start_ts, "EOD", qty,
                             charges_for_long_trade)
                trades.append(open_trade); open_trade = None
                driver.notify_exit(open_trade.side if open_trade else _last_side(trades))
                continue

            ind, sig = driver.feed(bar.start_ts, bar.open, bar.high, bar.low, bar.close, act=True)

            # manage an open trade against THIS 3m bar's underlying signal first
            if open_trade:
                # ST exit signal for the held side
                want_exit = (sig and sig.action == f"EXIT_{open_trade.side}")
                if want_exit:
                    px = _opt_close_at(cur, open_trade.symbol, bar.start_ts)
                    _settle(open_trade, bar.start_ts, px if px else open_trade.entry_price,
                            "ST_EXIT", qty, charges_for_long_trade)
                    trades.append(open_trade)
                    driver.notify_exit(open_trade.side)
                    open_trade = None
                else:
                    # intrabar SL/TP scan over the next 3 one-min option candles
                    hit = _scan_sl_tp(cur, open_trade, bar.start_ts)
                    if hit:
                        trades.append(open_trade)
                        driver.notify_exit(open_trade.side)
                        open_trade = None

            # entries (only when flat and within session)
            if not open_trade and sig and sig.action in ("ENTER_CE", "ENTER_PE"):
                if not (sess_start <= hm < sess_end):
                    continue
                side = "CE" if sig.action == "ENTER_CE" else "PE"
                sel = _select_option(cur, side, bar.close, expiry, max_premium,
                                     scan, bar.start_ts)
                if not sel:
                    continue
                symbol, strike, fill = sel
                sl = fill * (1 - sl_pct / 100) if sl_pct > 0 else 0.0
                tp = fill * (1 + tp_pct / 100) if tp_pct > 0 else 0.0
                open_trade = BBTrade(side=side, symbol=symbol, strike=strike,
                                     entry_ts=bar.start_ts, entry_price=fill,
                                     sl=sl, tp=tp, qty=qty)
                driver.confirm_entry(side)

        # close any trade still open at end of day (safety EOD)
        if open_trade:
            last_ts = bars[-1].start_ts if bars else _day_bounds(sim_day)[0]
            _close_trade(cur, open_trade, last_ts, "EOD", qty, charges_for_long_trade)
            trades.append(open_trade)
            driver.notify_exit(open_trade.side)
            open_trade = None

    conn.close()
    return {"trades": [t.__dict__ for t in trades],
            "summary": _summarize(trades),
            "days_total": days_total}


# ---------- helpers ----------

def _fut_3m_for_day(cur, sim_day: date):
    from app.backtest.bb.bt_candle_agg import Bar, aggregate_1m_to_3m
    lo, hi = _day_bounds(sim_day)
    rows = cur.execute(
        """SELECT ts,open,high,low,close,volume,oi FROM backtest_candles_1m
           WHERE tradingsymbol=? AND ts>=? AND ts<? ORDER BY ts ASC""",
        (FUT_SYMBOL, lo, hi),
    ).fetchall()
    bars1 = [Bar(r["ts"], r["open"], r["high"], r["low"], r["close"],
                 r["volume"] or 0, r["oi"] or 0) for r in rows]
    return aggregate_1m_to_3m(bars1)


def _feed_fut_3m(cur, driver, d0: date, d1: date, act: bool):
    cur2 = cur
    cur_day = d0
    while cur_day <= d1:
        bars = _fut_3m_for_day(cur2, cur_day)
        for b in bars:
            driver.feed(b.start_ts, b.open, b.high, b.low, b.close, act=act)
        cur_day += timedelta(days=1)


def _opt_close_at(cur, symbol: str, ts3m: int) -> Optional[float]:
    """Option close at the 3m bar's CLOSE minute (last 1m of the bucket)."""
    minute = ts3m + 120  # third minute of the 3m bar
    row = cur.execute(
        "SELECT close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",
        (symbol, minute),
    ).fetchone()
    if row:
        return float(row["close"])
    # fallback: any close within the bucket
    row = cur.execute(
        """SELECT close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts>=? AND ts<?
           ORDER BY ts DESC LIMIT 1""",
        (symbol, ts3m, ts3m + 180),
    ).fetchone()
    return float(row["close"]) if row else None


def _scan_sl_tp(cur, t: BBTrade, ts3m: int) -> bool:
    """Scan the three 1m option candles in this 3m bar for SL/TP. Long option:
    SL is BELOW entry (low<=sl), TP ABOVE (high>=tp). Pessimistic SL-first on
    ambiguous. Returns True if exited (mutates t)."""
    if t.sl <= 0 and t.tp <= 0:
        return False
    rows = cur.execute(
        """SELECT ts,high,low FROM backtest_candles_1m WHERE tradingsymbol=?
           AND ts>=? AND ts<? ORDER BY ts ASC""",
        (t.symbol, ts3m, ts3m + 180),
    ).fetchall()
    for r in rows:
        hi, lo = r["high"], r["low"]
        sl_hit = t.sl > 0 and lo <= t.sl
        tp_hit = t.tp > 0 and hi >= t.tp
        if sl_hit and tp_hit:
            _settle(t, r["ts"], t.sl, "SL", t.qty, _charges_fn())
            t.ambiguous = True
            return True
        if sl_hit:
            _settle(t, r["ts"], t.sl, "SL", t.qty, _charges_fn()); return True
        if tp_hit:
            _settle(t, r["ts"], t.tp, "TP", t.qty, _charges_fn()); return True
    return False


def _charges_fn():
    try:
        from app.backtest.charges.charges_model import charges_for_long_trade
        return charges_for_long_trade
    except Exception:
        return None


def _select_option(cur, side: str, fut_close: float, expiry: date,
                   max_premium: float, scan: int, ts3m: int):
    """Pick the option leg: scan strikes around ATM, take the one whose premium
    (at the entry minute) is the HIGHEST that is <= max_premium (BB buys the
    richest affordable option — closest to ATM within budget). Returns
    (symbol, strike, premium) or None."""
    from app.backtest.util.bnf_symbol import build_banknifty_symbol
    atm = int(round(fut_close / STRIKE_STEP) * STRIKE_STEP)
    minute = ts3m + 120
    best = None  # (premium, symbol, strike)
    # scan from ATM outward; CE strikes >= ATM cheaper as they go up, etc.
    for i in range(-scan, scan + 1):
        strike = atm + i * STRIKE_STEP
        sym = build_banknifty_symbol(expiry, strike, side)
        row = cur.execute(
            "SELECT close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",
            (sym, minute),
        ).fetchone()
        if not row:
            continue
        prem = float(row["close"])
        if prem <= 0 or prem > max_premium:
            continue
        if best is None or prem > best[0]:
            best = (prem, sym, strike)
    if not best:
        return None
    return best[1], best[2], best[0]


def _settle(t: BBTrade, exit_ts: int, exit_price: float, reason: str, qty: int,
            charges_fn):
    t.exit_ts = exit_ts
    t.exit_price = exit_price
    t.exit_reason = reason
    t.gross = (exit_price - t.entry_price) * qty
    ch = 0.0
    if charges_fn:
        try:
            ch = charges_fn(entry=t.entry_price, exit=exit_price, qty=qty)
        except Exception:
            ch = 0.0
    t.charges = ch
    t.net = t.gross - ch


def _close_trade(cur, t: BBTrade, ts3m: int, reason: str, qty: int, charges_fn):
    px = _opt_close_at(cur, t.symbol, ts3m) or t.entry_price
    _settle(t, ts3m, px, reason, qty, charges_fn)


def _last_side(trades):
    return trades[-1].side if trades else "CE"


def _empty_summary():
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "ambiguous_fills": 0, "max_drawdown": 0.0}


def _summarize(trades: List[BBTrade]) -> dict:
    closed = [t for t in trades if t.net is not None]
    if not closed:
        return _empty_summary()
    wins = sum(1 for t in closed if t.net > 0)
    losses = sum(1 for t in closed if t.net <= 0)
    gross = sum(t.gross for t in closed)
    charges = sum(t.charges for t in closed)
    net = sum(t.net for t in closed)
    # max drawdown on cumulative net
    peak = 0.0; cum = 0.0; mdd = 0.0
    for t in sorted(closed, key=lambda x: x.entry_ts):
        cum += t.net
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {"total_trades": len(closed), "wins": wins, "losses": losses,
            "win_rate": 100.0 * wins / len(closed) if closed else 0.0,
            "gross_pnl": gross, "total_charges": charges, "net_pnl": net,
            "ambiguous_fills": sum(1 for t in closed if t.ambiguous),
            "max_drawdown": mdd}