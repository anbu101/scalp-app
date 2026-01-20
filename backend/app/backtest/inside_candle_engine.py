from datetime import datetime
import uuid

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.backtest.indicators import bullish_ema_ok, bearish_ema_ok
from app.backtest.price_based_option_selector import PriceBasedOptionSelector

RR = 1.5
LOT_SIZE = 50
LOTS = 1


class InsideCandleEngine:

    def __init__(self, backtest_run_id):
        self.conn = get_conn()
        self.cur = self.conn.cursor()
        self.backtest_run_id = backtest_run_id
        self.selector = PriceBasedOptionSelector()   # ✅ FIX


    # --------------------------------------------------
    def run(self, start_ts, end_ts):
        index_candles = self._load_index_candles(start_ts, end_ts)

        stats = {
            "total": 0,
            "same_day": 0,
            "inside": 0,
            "volume": 0,
            "ema": 0,
            "signals": 0,
        }

        for i in range(50, len(index_candles)):
            stats["total"] += 1

            c1 = index_candles[i - 2]
            c2 = index_candles[i - 1]

            if self._day(c1["ts"]) != self._day(c2["ts"]):
                continue
            stats["same_day"] += 1

            # inside candle
            if not (c2["high"] < c1["high"] and c2["low"] > c1["low"]):
                continue
            stats["inside"] += 1

            # volume
            if c1["volume"] <= c2["volume"]:
                continue
            stats["volume"] += 1

            recent = index_candles[i - 50 : i]

            bullish = bullish_ema_ok(recent) and c1["close"] > c1["open"]
            bearish = bearish_ema_ok(recent) and c1["close"] < c1["open"]

            if not (bullish or bearish):
                continue
            stats["ema"] += 1

            direction = "BULLISH" if bullish else "BEARISH"
            stats["signals"] += 1

            self._spawn_trade(c2, direction)

        write_audit_log(f"[INSIDE_CANDLE][STATS] {stats}")


    # --------------------------------------------------
    def _spawn_trade(self, signal_candle, direction):
        ts = signal_candle["ts"]

        opt = self.selector.select(
            direction=direction,
            ts=ts,
            min_price=150,
            max_price=200,
        )

        if not opt:
            return

        self._insert_trade(opt, ts)


    # --------------------------------------------------
    def _insert_trade(self, opt, ts):
        row = self.cur.execute(
            """
            SELECT 1 FROM backtest_trades
            WHERE symbol=? AND state='OPEN'
            """,
            (opt["symbol"],)
        ).fetchone()

        if row:
            return

        c = self.cur.execute(
            """
            SELECT ts, close
            FROM historical_candles_options
            WHERE symbol=? AND timeframe='5m' AND ts >= ?
            ORDER BY ts
            LIMIT 1
            """,
            (opt["symbol"], ts)
        ).fetchone()

        if not c:
            return

        entry_ts, entry_price = c

        sl = entry_price * 0.9
        tp = entry_price + (entry_price - sl) * RR

        trade_id = str(uuid.uuid4())

        self.cur.execute(
            """
            INSERT INTO backtest_trades (
                backtest_trade_id, backtest_run_id, strategy_name,
                symbol, token, side,
                entry_time, entry_price, candle_ts,
                sl_price, tp_price, rr,
                lots, lot_size, qty,
                state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                self.backtest_run_id,
                "INSIDE_CANDLE",
                opt["symbol"],
                opt["token"],
                opt["option_type"],
                entry_ts,
                entry_price,
                ts,
                sl,
                tp,
                RR,
                LOTS,
                LOT_SIZE,
                LOTS * LOT_SIZE,
                "OPEN",
                ts,
            )
        )

        self.conn.commit()
        write_audit_log(f"[INSIDE_CANDLE][ENTRY] {opt['symbol']}")


    # --------------------------------------------------
    def _load_index_candles(self, start_ts, end_ts):
        rows = self.cur.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM historical_candles_index
            WHERE symbol='NIFTY'
              AND timeframe='5m'
              AND ts BETWEEN ? AND ?
            ORDER BY ts
            """,
            (start_ts, end_ts)
        ).fetchall()

        return [
            {
                "ts": r[0],
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
            }
            for r in rows
        ]


    def _day(self, ts):
        return datetime.fromtimestamp(ts).date()
