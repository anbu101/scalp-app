from pathlib import Path
from datetime import datetime

from backend.app.marketdata.zerodha_auth_dont_use import load_kite
from app.marketdata.zerodha_historical import ZerodhaHistoricalFetcher
from app.marketdata.zerodha_instruments import load_instruments_df
from app.marketdata.zerodha_resolver import ZerodhaInstrumentResolver
from app.engine.strategy_engine import StrategyEngine


SYMBOL = "NIFTY25D1625800CE"
TIMEFRAME_SEC = 60  # 1 minute


def main():
    print("\n=== VERIFY TRADINGVIEW PARITY ===\n")

    # -------------------------------------------------
    # Kite + Historical fetcher
    # -------------------------------------------------
    kite = load_kite()

    hist = ZerodhaHistoricalFetcher(
        kite=kite,
        base_dir=Path("~/.scalp-app/candles"),
        days=2,
    )

    # -------------------------------------------------
    # Resolve instrument
    # -------------------------------------------------
    print("[INSTRUMENTS] Loading Zerodha instruments dump...")
    instruments_df = load_instruments_df()
    resolver = ZerodhaInstrumentResolver(instruments_df)

    inst = resolver.find_by_tradingsymbol(SYMBOL)

    print("[INSTRUMENT]")
    print(f"  Symbol : {inst.tradingsymbol}")
    print(f"  Token  : {inst.instrument_token}")
    print(f"  Expiry : {inst.expiry}")
    print(f"  Strike : {inst.strike}")
    print()

    # -------------------------------------------------
    # Fetch candles (⚠️ NO from_ts / to_ts)
    # -------------------------------------------------
    candles = hist.fetch(
        symbol=inst.tradingsymbol,
        instrument_token=inst.instrument_token,
        expiry=inst.expiry.strftime("%Y-%m-%d"),
        timeframe_sec=TIMEFRAME_SEC,
    )

    print(f"[DATA] Candles loaded: {len(candles)}\n")

    # -------------------------------------------------
    # Run strategy
    # -------------------------------------------------
    engine = StrategyEngine(timeframe_sec=TIMEFRAME_SEC)

    buy_signals = []

    for c in candles:
        signal = engine.on_candle(c)
        if signal.is_buy:
            buy_signals.append(c)

    # -------------------------------------------------
    # Output
    # -------------------------------------------------
    print("=== BUY SIGNALS ===")
    for c in buy_signals:
        ts = datetime.fromtimestamp(c.end_ts)
        print(
            f"[BUY] {ts}  "
            f"O={c.open} H={c.high} L={c.low} C={c.close}"
        )

    print(f"\nTOTAL BUY SIGNALS: {len(buy_signals)}")
    print("\n=== VERIFY COMPLETE ===\n")


if __name__ == "__main__":
    main()
