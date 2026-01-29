from datetime import datetime, timedelta
from app.marketdata.market_indices_state import MarketIndicesState
from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df


INDEX_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
}

def seed_index_ltp_once(kite):
    """
    Seed index LTP once at startup so UI never sees None.
    """
    try:
        data = kite.ltp([
            "NSE:NIFTY 50",
            "NSE:NIFTY BANK",
        ])

        if "NSE:NIFTY 50" in data:
            MarketIndicesState.update_ltp(
                "NIFTY", float(data["NSE:NIFTY 50"]["last_price"])
            )

        if "NSE:NIFTY BANK" in data:
            MarketIndicesState.update_ltp(
                "BANKNIFTY", float(data["NSE:NIFTY BANK"]["last_price"])
            )

    except Exception as e:
        write_audit_log(f"[INDEX][WARN] Failed to seed index LTP: {e}")


def load_index_prev_close_once(kite):
    """
    Load previous trading day's close for NIFTY & BANKNIFTY.
    Uses DAILY historical candles.
    Runs safely once per day.
    """

    # 🔒 Do not reload if already set
    if MarketIndicesState.is_ready():
        write_audit_log("[INDEX] Prev close already loaded — skipping")
        return

    try:
        df = load_instruments_df()

        for index_name, trading_symbol in INDEX_MAP.items():
            row = df[
                (df["segment"] == "INDICES")
                & (df["tradingsymbol"] == trading_symbol)
            ]

            if row.empty:
                write_audit_log(
                    f"[INDEX][ERROR] Instrument not found for {index_name}"
                )
                continue

            token = int(row.iloc[0]["instrument_token"])

            to_date = datetime.now()
            from_date = to_date - timedelta(days=10)

            candles = kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day",
            )

            if not candles or len(candles) < 2:
                write_audit_log(
                    f"[INDEX][ERROR] Not enough candles for {index_name}"
                )
                continue

            prev_close = float(candles[-2]["close"])

            MarketIndicesState.set_prev_close(index_name, prev_close)

            write_audit_log(
                f"[INDEX] {index_name} prev_close set to {prev_close}"
            )

    except Exception as e:
        write_audit_log(f"[INDEX][FATAL] Failed to load prev close: {e}")
