# backend/app/backtest/run_cpr_e21.py

from datetime import datetime, timedelta
import uuid

from app.db.sqlite import get_conn
from app.backtest.cpr_pivot import compute_cpr_pivot
from app.backtest.cpr_e21_signal import detect_cpr_e21_signals
from app.backtest.cpr_e21_signal_filter import filter_signals
from app.backtest.price_based_option_selector import PriceBasedOptionSelector
from app.backtest.cpr_e21_exit import simulate_exit
from app.backtest.cpr_e21_entry import insert_cpr_e21_trade
from app.backtest.indicators import ema
from app.event_bus.audit_logger import write_audit_log
from app.utils.app_paths import ensure_app_dirs


# --------------------------------------------------
def load_index_5m(conn, start_ts, end_ts):
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT ts, open, high, low, close
        FROM historical_candles_index
        WHERE symbol='NIFTY'
          AND timeframe='5m'
          AND ts BETWEEN ? AND ?
        ORDER BY ts
        """,
        (start_ts, end_ts),
    ).fetchall()

    return [
        {
            "ts": r[0],
            "ts_date": datetime.fromtimestamp(r[0]).date(),
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
        }
        for r in rows
    ]


# --------------------------------------------------
def build_daily_cpr(index_candles):
    by_day = {}
    for c in index_candles:
        by_day.setdefault(c["ts_date"], []).append(c)

    days = sorted(by_day.keys())
    daily = {}

    # CPR for day D is derived from day D-1
    for i in range(1, len(days)):
        prev = by_day[days[i - 1]]
        daily[days[i]] = compute_cpr_pivot(
            {
                "open": prev[0]["open"],
                "high": max(x["high"] for x in prev),
                "low": min(x["low"] for x in prev),
                "close": prev[-1]["close"],
            }
        )

    return daily


# --------------------------------------------------
def build_ema21_map(index_candles):
    closes = [c["close"] for c in index_candles]
    ema_map = {}

    for i in range(21, len(index_candles)):
        ema_map[index_candles[i]["ts"]] = ema(
            closes[i - 21 : i + 1], 21
        )

    return ema_map


# --------------------------------------------------
def main():
    ensure_app_dirs()
    conn = get_conn()
    run_id = str(uuid.uuid4())

    write_audit_log(f"[BACKTEST][RUN] CPR_E21 run_id={run_id}")

    end_ts = int(datetime.now().timestamp())
    start_ts = int((datetime.now() - timedelta(days=30)).timestamp())

    index_candles = load_index_5m(conn, start_ts, end_ts)
    if len(index_candles) < 50:
        write_audit_log("[CPR_E21][ERROR] Not enough index candles")
        return

    cpr_by_day = build_daily_cpr(index_candles)
    ema21_by_ts = build_ema21_map(index_candles)

    raw_signals = detect_cpr_e21_signals(index_candles, cpr_by_day)
    signals = list(filter_signals(raw_signals))

    selector = PriceBasedOptionSelector(conn)

    for sig in signals:
        sig_ts = sig["ts"]
        ema_cross_ts = sig["ema_cross_ts"]

        # ---- EMA must exist (hard guard)
        if ema_cross_ts not in ema21_by_ts:
            continue

        ema_val = ema21_by_ts[ema_cross_ts]
        ema_day = datetime.fromtimestamp(ema_cross_ts).date()

        cpr = cpr_by_day.get(ema_day)
        if not cpr:
            continue

        # ---- option selection
        opt = selector.select(
            ts=sig_ts,
            direction=sig["direction"],
            min_price=150,
            max_price=200,
        )
        if not opt:
            continue

        # ---- locate signal candle
        idx = next(
            (i for i, c in enumerate(index_candles) if c["ts"] == sig_ts),
            None,
        )
        if idx is None or idx < 2:
            continue

        prev = index_candles[idx - 1]

        # ---- SL / TP (index based)
        if sig["direction"] == "BULLISH":
            sl = prev["low"]
            risk = prev["high"] - prev["low"]
            tp = prev["high"] + 2 * risk
        else:
            sl = prev["high"]
            risk = prev["high"] - prev["low"]
            tp = prev["low"] - 2 * risk

        ema_candle = next(
            c for c in index_candles if c["ts"] == ema_cross_ts
        )
        break_candle = index_candles[idx]

        signal_meta = {
            "direction": sig["direction"],
            "ema": {
                "cross_ts": ema_cross_ts,
                "ema21": ema_val,
                "open": ema_candle["open"],
                "high": ema_candle["high"],
                "low": ema_candle["low"],
                "close": ema_candle["close"],
            },
            "cpr": {
                "tc": cpr["tc"],
                "bc": cpr["bc"],
                "pivot": cpr["pivot"],
                "R": cpr["R"],
                "S": cpr["S"],
            },
            "cpr_break": {
                "ts": sig_ts,
                "reason": sig["reason"],
                "open": break_candle["open"],
                "high": break_candle["high"],
                "low": break_candle["low"],
                "close": break_candle["close"],
            },
        }

        trade_id = insert_cpr_e21_trade(
            conn,
            run_id,
            sig,
            opt,
            sl,
            tp,
            signal_meta,
        )

        simulate_exit(
            conn,
            trade_id,
            sig["direction"],
            sig_ts,
            sl,
            tp,
            ema21_by_ts,
        )

    write_audit_log(f"[BACKTEST][DONE] CPR_E21 run_id={run_id}")


# --------------------------------------------------
if __name__ == "__main__":
    main()
