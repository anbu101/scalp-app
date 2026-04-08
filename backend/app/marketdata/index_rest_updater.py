import time
from kiteconnect import KiteConnect
from app.marketdata.market_indices_state import MarketIndicesState
from app.event_bus.audit_logger import write_audit_log

INDEX_SYMBOLS = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX":    "BSE:SENSEX",
}


def index_polling_loop(kite: KiteConnect):
    """
    Polls kite.ltp() every second to keep index LTP fresh in MarketIndicesState.

    IMPORTANT — prev_close is intentionally NOT set here.
    kite.ltp() returns ohlc.close which is unreliable (sometimes 0,
    sometimes the current price). The authoritative prev_close comes
    from kite.historical_data() in load_index_prev_close_once() which
    runs at startup. We must never overwrite it with ltp() ohlc data.
    """
    write_audit_log("[INDEX] REST polling started")

    while True:
        try:
            data = kite.ltp(list(INDEX_SYMBOLS.values()))

            for name, symbol in INDEX_SYMBOLS.items():
                d = data.get(symbol)
                if not d:
                    continue

                ltp = d.get("last_price")
                if ltp is not None and ltp > 0:
                    MarketIndicesState.update_ltp(name, ltp)

                # ── prev_close is NOT set here ──
                # load_index_prev_close_once() sets it from historical_data()
                # at startup. That is the only authoritative source.

        except Exception as e:
            write_audit_log(f"[INDEX] REST poll error: {e}")

        time.sleep(1)