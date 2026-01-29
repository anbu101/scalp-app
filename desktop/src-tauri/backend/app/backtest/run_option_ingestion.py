# backend/app/backtest/run_option_ingestion.py

from datetime import datetime, timedelta
import pytz
import multiprocessing as mp

from app.utils.app_paths import ensure_app_dirs, export_env
from app.event_bus.audit_logger import write_audit_log
from app.backtest.option_universe_builder import OptionUniverseBuilder
from app.backtest.option_historical_fetcher import OptionHistoricalFetcher

IST = pytz.timezone("Asia/Kolkata")


def run_one(opt: dict):
    """
    Fetch historical candles for a single option contract.
    Runs in isolated process (SQLite + Kite safety).
    """
    try:
        fetcher = OptionHistoricalFetcher()
        fetcher.fetch_contract(opt)
    except Exception as e:
        write_audit_log(
            f"[BACKTEST][OPTION][ERROR] {opt.get('symbol')} err={e}"
        )


def main():
    # --------------------------------------------------
    # APP HOME / ENV (CRITICAL)
    # --------------------------------------------------
    ensure_app_dirs()
    export_env()

    mp.set_start_method("spawn", force=True)

    now = datetime.now(IST)
    start = now - timedelta(days=30)

    builder = OptionUniverseBuilder()

    contracts = builder.build(
        start_ts=int(start.timestamp()),
        end_ts=int(now.timestamp()),
    )

    write_audit_log(
        f"[BACKTEST][OPTION] contracts={len(contracts)}"
    )

    for opt in contracts.values():
        symbol = opt.get("symbol")

        write_audit_log(
            f"[BACKTEST][OPTION][SPAWN] {symbol}"
        )

        p = mp.Process(
            target=run_one,
            args=(opt,),
            daemon=True,
        )

        p.start()
        p.join(timeout=3)   # ⏱ hard limit per contract

        if p.is_alive():
            write_audit_log(
                f"[BACKTEST][OPTION][KILL] {symbol}"
            )
            p.terminate()
            p.join()

    write_audit_log("[BACKTEST][OPTION] DONE")


# --------------------------------------------------
if __name__ == "__main__":
    main()
