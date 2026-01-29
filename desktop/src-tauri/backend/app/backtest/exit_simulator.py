from datetime import datetime

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log


class ExitSimulator:

    def __init__(self):
        self.conn = get_conn()
        self.cur = self.conn.cursor()

    # --------------------------------------------------
    def run(self):
        open_trades = self.cur.execute(
            """
            SELECT
                backtest_trade_id,
                symbol,
                entry_time,
                entry_price,
                sl_price,
                tp_price,
                side
            FROM backtest_trades
            WHERE state='OPEN'
            """
        ).fetchall()

        for t in open_trades:
            self._process_trade(t)

        self.conn.commit()

    # --------------------------------------------------
    def _process_trade(self, t):
        (
            trade_id, symbol,
            entry_ts, entry_price,
            sl, tp, side
        ) = t

        candles = self.cur.execute(
            """
            SELECT ts, high, low, close
            FROM historical_candles_options
            WHERE symbol=?
              AND timeframe='5m'
              AND ts > ?
            ORDER BY ts
            """,
            (symbol, entry_ts)
        ).fetchall()

        for c in candles:
            ts, high, low, close = c

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl and hit_tp:
                self._exit(
                    trade_id, ts, sl,
                    "SL", sl_tp_same_candle=1
                )
                return

            if hit_sl:
                self._exit(trade_id, ts, sl, "SL")
                return

            if hit_tp:
                self._exit(trade_id, ts, tp, "TP")
                return

        # ---- EOD square-off
        if candles:
            last = candles[-1]
            self._exit(trade_id, last[0], last[3], "EOD")

    # --------------------------------------------------
    def _exit(self, trade_id, ts, price, reason, sl_tp_same_candle=0):
        self.cur.execute(
            """
            UPDATE backtest_trades
            SET
                exit_time=?,
                exit_price=?,
                exit_reason=?,
                pnl_points=(? - entry_price),
                pnl_value=(? - entry_price) * qty,
                net_pnl=(? - entry_price) * qty,
                sl_tp_same_candle=?,
                state='CLOSED'
            WHERE backtest_trade_id=?
            """,
            (
                ts, price, reason,
                price, price, price,
                sl_tp_same_candle,
                trade_id
            )
        )

        write_audit_log(
            f"[INSIDE_CANDLE][EXIT] {trade_id} {reason}"
        )
