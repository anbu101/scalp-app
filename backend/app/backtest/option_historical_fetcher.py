from datetime import datetime, timedelta, time as dt_time
import time
import pytz

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.brokers.zerodha_manager import ZerodhaManager


TIMEFRAMES = {
    "5m": "5minute",
    "15m": "15minute",
}

CHUNK_DAYS = 10
OPTION_SLEEP = 0.05

IST = pytz.timezone("Asia/Kolkata")


class OptionHistoricalFetcher:

    def __init__(self):
        manager = ZerodhaManager()
        self.kite = manager.get_data_kite() or manager.get_trade_kite()
        if not self.kite:
            raise RuntimeError("Zerodha not logged in")

        # best-effort timeout (may or may not apply internally)
        self.kite.reqsession.timeout = 15
        self.conn = get_conn()

    def fetch_contract(self, opt):
        token = opt["token"]
        symbol = opt["symbol"]

        # ---- listing date → datetime
        listing_date = opt["listing_date"]
        if isinstance(listing_date, datetime):
            start = listing_date
        else:
            start = datetime.combine(
                listing_date,
                dt_time(9, 15)
            ).replace(tzinfo=IST)

        # ---- expiry → datetime
        end = datetime.combine(
            opt["expiry"],
            dt_time(15, 30)
        ).replace(tzinfo=IST)

        for tf_key, tf_value in TIMEFRAMES.items():
            write_audit_log(
                f"[BACKTEST][OPTION][{symbol}][{tf_key}] START"
            )

            cur_from = start

            while cur_from < end:
                cur_to = min(cur_from + timedelta(days=CHUNK_DAYS), end)

                try:
                    candles = self.kite.historical_data(
                        instrument_token=token,
                        from_date=cur_from,
                        to_date=cur_to,
                        interval=tf_value,
                        continuous=False,
                        oi=True,
                    )
                except Exception as e:
                    write_audit_log(
                        f"[BACKTEST][OPTION][ERROR] {symbol} {tf_key} {e}"
                    )
                    cur_from = cur_to + timedelta(days=1)
                    continue

                if candles:
                    self._store(opt, tf_key, candles)
                    cur_from = candles[-1]["date"] + timedelta(seconds=1)
                else:
                    cur_from = cur_to + timedelta(days=1)

                time.sleep(OPTION_SLEEP)

            write_audit_log(
                f"[BACKTEST][OPTION][{symbol}][{tf_key}] DONE"
            )

    def _store(self, opt, timeframe, candles):
        cur = self.conn.cursor()

        for c in candles:
            cur.execute(
                """
                INSERT OR IGNORE INTO historical_candles_options
                (
                    symbol,
                    strike,
                    option_type,
                    expiry,
                    timeframe,
                    ts,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    oi
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opt["symbol"],
                    opt["strike"],
                    opt["option_type"],
                    opt["expiry"],
                    timeframe,
                    int(c["date"].timestamp()),
                    c["open"],
                    c["high"],
                    c["low"],
                    c["close"],
                    c.get("volume"),
                    c.get("oi"),
                ),
            )

        self.conn.commit()
