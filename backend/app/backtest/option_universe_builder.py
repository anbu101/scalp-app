# app/backtest/option_universe_builder.py

from datetime import datetime
from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df

INDEX_SYMBOL = "NIFTY"


class OptionUniverseBuilder:
    """
    Builds UNIQUE option contracts for backtesting
    using instruments.csv (historically correct).
    """

    def __init__(self):
        self.conn = get_conn()
        self.df = load_instruments_df()

    def build(
        self,
        start_ts: int,
        end_ts: int,
        atm_range: int = 800,
        strike_step: int = 50,
    ):
        cur = self.conn.cursor()

        write_audit_log(
            f"[BACKTEST][OPTION][UNIVERSE] BUILD START ts={start_ts}→{end_ts}"
        )

        rows = cur.execute(
            """
            SELECT ts, close
            FROM historical_candles_index
            WHERE symbol = ?
              AND timeframe = '5m'
              AND ts BETWEEN ? AND ?
            ORDER BY ts
            """,
            (INDEX_SYMBOL, start_ts, end_ts),
        ).fetchall()

        write_audit_log(
            f"[BACKTEST][OPTION][UNIVERSE] index_rows={len(rows)}"
        )

        contracts = {}

        for r in rows:
            ts = r["ts"]
            spot = float(r["close"])
            candle_date = datetime.fromtimestamp(ts).date()

            atm = round(spot / strike_step) * strike_step
            low = atm - atm_range
            high = atm + atm_range

            df = self.df[
                (self.df["name"] == INDEX_SYMBOL)
                & (self.df["instrument_type"].isin(["CE", "PE"]))
                & (self.df["strike"] >= low)
                & (self.df["strike"] <= high)
                & (self.df["expiry"] >= candle_date)
            ]

            for _, row in df.iterrows():
                token = int(row["instrument_token"])

                if token not in contracts:
                    contracts[token] = {
                        "token": token,
                        "symbol": row["tradingsymbol"],
                        "strike": int(row["strike"]),
                        "option_type": row["instrument_type"],
                        "expiry": row["expiry"],
                        "listing_date": row.get("listing_date", candle_date),
                    }

        write_audit_log(
            f"[BACKTEST][OPTION][UNIVERSE] unique_contracts={len(contracts)}"
        )

        return contracts
