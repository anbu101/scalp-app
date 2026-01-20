import os
os.environ["DB_PATH"] = "/data/app.db"

from datetime import datetime, timedelta
import pytz
import multiprocessing as mp

from app.event_bus.audit_logger import write_audit_log
from app.backtest.option_universe_builder import OptionUniverseBuilder
from app.backtest.option_historical_fetcher import OptionHistoricalFetcher

IST = pytz.timezone("Asia/Kolkata")


def run_one(opt):
    fetcher = OptionHistoricalFetcher()
    fetcher.fetch_contract(opt)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)

    now = datetime.now(IST)
    start = now - timedelta(days=30)

    builder = OptionUniverseBuilder()

    contracts = builder.build(
        start_ts=int(start.timestamp()),
        end_ts=int(now.timestamp()),
    )

    for opt in contracts.values():
        write_audit_log(
            f"[BACKTEST][OPTION][SPAWN] {opt['symbol']}"
        )

        p = mp.Process(target=run_one, args=(opt,))
        p.start()
        p.join(timeout=3)   # hard limit per contract

        if p.is_alive():
            write_audit_log(
                f"[BACKTEST][OPTION][KILL] {opt['symbol']}"
            )
            p.terminate()
            p.join()
