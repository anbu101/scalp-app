from datetime import datetime, timedelta
import time

from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.backtest.backtest_instrument_resolver import BacktestInstrumentResolver
from app.brokers.zerodha_manager import ZerodhaManager


INDEX_SYMBOL = "NIFTY"

TIMEFRAMES = {
    "5m": "5minute",
    "15m": "15minute",
}

CHUNK_DAYS = 10        # Zerodha-safe chunk
OPTION_SLEEP = 0.05   # Zerodha rate safety

FETCH_OPTIONS = False   # 🔴 PHASE-1: MUST BE FALSE


class HistoricalFetcher:
    """
    Backtest-only historical data fetcher.

    DESIGN (PHASE 1):
    ✔ Index candles fetched CONTINUOUSLY (correct cursor advancement)
    ✔ Options fetched ONLY at index candle timestamps (Option-A design)
    ✔ Handles option non-existence (pre-listing / post-expiry)
    ✔ ATM ± range per candle (large but intentional)
    ✔ Safe to stop & resume (INSERT OR IGNORE)
    ✔ Zero impact on live trading
    """

    def __init__(self):
        manager = ZerodhaManager()

        # Prefer DATA session, fallback to TRADE session
        self.kite = manager.get_data_kite() or manager.get_trade_kite()
        if not self.kite:
            raise RuntimeError(
                "Zerodha not logged in. Login via UI before running backtest."
            )

        self.conn = get_conn()

    # --------------------------------------------------
    # PUBLIC ENTRY
    # --------------------------------------------------
    def fetch(
        self,
        from_date: datetime,
        to_date: datetime,
        atm_range: int = 800,
        strike_step: int = 50,
    ):
        write_audit_log(
            f"[BACKTEST][FETCH] from={from_date.date()} to={to_date.date()}"
        )

        index_token = self._get_index_token()

        for tf_key, tf_value in TIMEFRAMES.items():
            write_audit_log(f"[BACKTEST][INDEX][{tf_key}] START")

            cur_from = from_date

            while cur_from < to_date:
                cur_to = min(cur_from + timedelta(days=CHUNK_DAYS), to_date)

                write_audit_log(
                    f"[BACKTEST][INDEX][{tf_key}] "
                    f"{cur_from.date()} → {cur_to.date()}"
                )

                try:
                    candles = self.kite.historical_data(
                        instrument_token=index_token,
                        from_date=cur_from,
                        to_date=cur_to,
                        interval=tf_value,
                        continuous=False,
                        oi=False,
                    )
                except Exception as e:
                    write_audit_log(
                        f"[BACKTEST][INDEX][ERROR] {tf_key} err={e}"
                    )
                    # Hard advance to avoid infinite loop
                    cur_from = cur_to + timedelta(days=1)
                    continue

                if not candles:
                    # No data in this window → advance safely
                    cur_from = cur_to + timedelta(days=1)
                    continue

                # -------------------------------
                # STORE INDEX CANDLES
                # -------------------------------
                self._store_index_candles(
                    INDEX_SYMBOL,
                    tf_key,
                    candles,
                )

                write_audit_log(
                    f"[BACKTEST][INDEX][{tf_key}] inserted={len(candles)}"
                )

                # -------------------------------
                # OPTION A:
                # Options only at index timestamps
                # -------------------------------
                if FETCH_OPTIONS:
                    for c in candles:
                        candle_dt = c["date"]
                        candle_date = candle_dt.date()
                        spot = float(c["close"])

                        resolver = BacktestInstrumentResolver(
                            as_of_date=candle_date,
                            spot_price=spot,
                            index_name=INDEX_SYMBOL,
                        )

                        try:
                            universe = resolver.get_option_universe(
                                atm_range=atm_range,
                                strike_step=strike_step,
                            )
                        except Exception as e:
                            write_audit_log(
                                f"[BACKTEST][OPTION][RESOLVER][SKIP] "
                                f"{candle_date} err={e}"
                            )
                            continue

                        for opt in universe:
                            # ----------------------------------
                            # HARD GUARDS — VERY IMPORTANT
                            # ----------------------------------

                            # 1️⃣ Option expired before this candle
                            if opt["expiry"] < candle_date:
                                continue

                            # 2️⃣ Option listed AFTER this candle
                            # (Resolver should handle this, but double-guard)
                            if opt.get("listing_date") and candle_date < opt["listing_date"]:
                                continue

                            for tf2_key, tf2_value in TIMEFRAMES.items():
                                try:
                                    # NOTE:
                                    # from_date == to_date intentionally
                                    # We want the candle aligned to index timestamp
                                    opt_candles = self.kite.historical_data(
                                        instrument_token=opt["token"],
                                        from_date=candle_dt,
                                        to_date=candle_dt,
                                        interval=tf2_value,
                                        continuous=False,
                                        oi=True,
                                    )
                                except Exception as e:
                                    write_audit_log(
                                        f"[BACKTEST][OPTION][SKIP] "
                                        f"{opt['symbol']} {tf2_key} err={e}"
                                    )
                                    continue

                                if not opt_candles:
                                    # Perfectly normal:
                                    # - illiquid option
                                    # - just listed
                                    # - expired intraday
                                    continue

                                self._store_option_candles(
                                    symbol=opt["symbol"],
                                    strike=opt["strike"],
                                    option_type=opt["option_type"],
                                    expiry=opt["expiry"],
                                    timeframe=tf2_key,
                                    candles=opt_candles,
                                )

                                time.sleep(OPTION_SLEEP)

                # --------------------------------------------------
                # CRITICAL FIX:
                # Advance cursor using LAST candle timestamp
                # --------------------------------------------------
                last_ts = candles[-1]["date"]

                # --------------------------------------------------
                # HARD LOOP BREAKER (very important near to_date)
                # --------------------------------------------------
                if last_ts <= cur_from:
                    # Zerodha returned the same candle again
                    # Force advance by 1 day to escape infinite loop
                    cur_from = cur_from + timedelta(days=1)
                else:
                    # Normal forward progress
                    cur_from = last_ts + timedelta(seconds=1)
                
                if cur_from <= last_ts:
                    raise RuntimeError(
                        f"[BACKTEST][FATAL] Cursor did not advance: cur_from={cur_from}, last_ts={last_ts}"
                    )


            write_audit_log(f"[BACKTEST][INDEX][{tf_key}] DONE")

    # --------------------------------------------------
    # DB INSERTS
    # --------------------------------------------------
    def _store_index_candles(self, symbol, timeframe, candles):
        cur = self.conn.cursor()

        for c in candles:
            cur.execute(
                """
                INSERT OR IGNORE INTO historical_candles_index
                (symbol, timeframe, ts, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    timeframe,
                    int(c["date"].timestamp()),
                    c["open"],
                    c["high"],
                    c["low"],
                    c["close"],
                    c.get("volume"),
                ),
            )

        self.conn.commit()

    def _store_option_candles(
        self,
        symbol,
        strike,
        option_type,
        expiry,
        timeframe,
        candles,
    ):
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
                    symbol,
                    strike,
                    option_type,
                    expiry,
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

    # --------------------------------------------------
    # INDEX TOKEN (CSV BASED)
    # --------------------------------------------------
    def _get_index_token(self) -> int:
        from app.fetcher.zerodha_instruments import load_instruments_df

        df = load_instruments_df()

        row = df[
            (df["exchange"] == "NSE")
            & (df["tradingsymbol"] == "NIFTY 50")
        ]

        if row.empty:
            raise RuntimeError("NIFTY index token not found in instruments.csv")

        return int(row.iloc[0]["instrument_token"])
