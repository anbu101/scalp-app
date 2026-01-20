import time
from kiteconnect import KiteConnect
from app.marketdata.market_indices_state import MarketIndicesState
from app.event_bus.audit_logger import write_audit_log

INDEX_SYMBOLS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX": "BSE:SENSEX",
}

def index_polling_loop(kite: KiteConnect):
    write_audit_log("[INDEX] REST polling started")

    while True:
        try:
            data = kite.ltp(list(INDEX_SYMBOLS.values()))

            for name, symbol in INDEX_SYMBOLS.items():
                d = data.get(symbol)
                if not d:
                    continue

                ltp = d.get("last_price")
                prev = d.get("ohlc", {}).get("close")

                if ltp is not None:
                    MarketIndicesState.update_ltp(name, ltp)

                if prev is not None:
                    MarketIndicesState.set_prev_close(name, prev)

        except Exception as e:
            write_audit_log(f"[INDEX] REST poll error: {e}")

        time.sleep(1)  # 1s is more than enough
