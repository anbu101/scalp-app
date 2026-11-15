# backend/app/engine/tma/test_tma_exit_paths.py
#
# ── EXIT_STATE_MACHINE_20261026 ── TMA_V1 shares TMA_V2's exit path line for
# line, so it runs the SAME 2026-10-26 incident replay (see
# tma2/test_tma2_exit_paths.py for the scenarios). Only identity, table and
# config shape (c1.sell / c1.buy) differ.
# Run standalone:  python3 test_tma_exit_paths.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.engine.tma import tma_trade_manager as TM               # noqa: E402
from app.engine.tma import tma_gtt_monitor as GM                 # noqa: E402
from app.engine.tma.tma_common import TMARepo                    # noqa: E402
from app.engine.tma2 import test_tma2_exit_paths as T            # noqa: E402

T.configure(TM=TM, GM=GM, Manager=TM.TMATradeManager, Repo=TMARepo,
            Monitor=GM.TMAGTTMonitor, table="tma_trades",
            cfg=lambda main, hedge: {"c1": {"sell": main, "buy": hedge,
                                            "max_trades_per_day": 0}})

if __name__ == "__main__":
    T.run_all("TMA_V1")
