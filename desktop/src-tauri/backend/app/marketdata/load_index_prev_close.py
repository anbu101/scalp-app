from datetime import datetime, timedelta, date
from app.marketdata.market_indices_state import MarketIndicesState
from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df


INDEX_MAP = {
    "NIFTY":     "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
}


def seed_index_ltp_once(kite):
    """
    Seed index LTP once at startup so UI never sees None on first load.
    """
    try:
        data = kite.ltp([
            "NSE:NIFTY 50",
            "NSE:NIFTY BANK",
        ])

        if "NSE:NIFTY 50" in data:
            ltp = float(data["NSE:NIFTY 50"]["last_price"])
            if ltp > 0:
                MarketIndicesState.update_ltp("NIFTY", ltp)
                write_audit_log(f"[INDEX] NIFTY LTP seeded: {ltp}")

        if "NSE:NIFTY BANK" in data:
            ltp = float(data["NSE:NIFTY BANK"]["last_price"])
            if ltp > 0:
                MarketIndicesState.update_ltp("BANKNIFTY", ltp)
                write_audit_log(f"[INDEX] BANKNIFTY LTP seeded: {ltp}")

    except Exception as e:
        write_audit_log(f"[INDEX][WARN] Failed to seed index LTP: {e}")


def load_index_prev_close_once(kite):
    """
    Load previous trading day's close for NIFTY & BANKNIFTY.

    KEY FIX: We find the last candle whose date is strictly BEFORE today.
    This correctly handles:
      - Market holidays (Good Friday etc.) - skipped naturally
      - Monday morning - picks Thursday, not Friday if Friday was holiday
      - Intraday restarts - today's partial candle is excluded

    Always overwrites whatever is in MarketIndicesState so the polling
    loop can never corrupt it with ltp().ohlc data.
    """
    try:
        df = load_instruments_df()
        today = date.today()

        for index_name, trading_symbol in INDEX_MAP.items():
            row = df[
                (df["segment"] == "INDICES")
                & (df["tradingsymbol"] == trading_symbol)
            ]

            if row.empty:
                write_audit_log(f"[INDEX][ERROR] Instrument not found for {index_name}")
                continue

            token = int(row.iloc[0]["instrument_token"])

            # Fetch last 15 days to cover long weekends + back-to-back holidays
            to_date   = datetime.now()
            from_date = to_date - timedelta(days=15)

            candles = kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day",
            )

            if not candles:
                write_audit_log(f"[INDEX][ERROR] No historical candles for {index_name}")
                continue

            write_audit_log(
                f"[INDEX] {index_name} got {len(candles)} daily candles, "
                f"last 5 dates: {[str(c['date'].date()) for c in candles[-5:]]}"
            )

            # ── Find last candle strictly before today ──────────────────
            # Works regardless of holidays, weekends, or whether Zerodha
            # has included today's partial candle in the response.
            prev_candle = None
            for candle in reversed(candles):
                candle_date = (
                    candle["date"].date()
                    if hasattr(candle["date"], "date")
                    else candle["date"]
                )
                if candle_date < today:
                    prev_candle = candle
                    break

            if prev_candle is None:
                write_audit_log(f"[INDEX][ERROR] No previous trading day found for {index_name}")
                continue

            prev_close = float(prev_candle["close"])
            prev_date  = (
                prev_candle["date"].date()
                if hasattr(prev_candle["date"], "date")
                else prev_candle["date"]
            )

            MarketIndicesState.set_prev_close(index_name, prev_close)

            write_audit_log(
                f"[INDEX] {index_name} prev_close={prev_close} "
                f"from trading day {prev_date}"
            )

    except Exception as e:
        write_audit_log(f"[INDEX][FATAL] Failed to load prev close: {e}")