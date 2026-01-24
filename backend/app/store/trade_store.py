import csv
import os
import time
from pathlib import Path
from typing import Optional


class TradeStore:
    """
    Simple CSV-based trade logger.
    One CSV per instrument token.
    """

    BASE_DIR = Path.home() / ".scalp-app" / "trades"

    def __init__(self, instrument_token: int):
        self.token = instrument_token
        self.dir = self.BASE_DIR / str(instrument_token)
        self.dir.mkdir(parents=True, exist_ok=True)

        self.file = self.dir / "trades.csv"
        self._ensure_header()

    def _ensure_header(self):
        if not self.file.exists():
            with open(self.file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["timestamp", "event", "token", "timeframe", "price", "sl", "tp"]
                )

    # =========================
    # Public API
    # =========================

    def log(
        self,
        event: str,
        timeframe: str,
        price: float,
        sl: Optional[float],
        tp: Optional[float],
        ts: Optional[int] = None,
    ):
        ts = ts or int(time.time())

        with open(self.file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    ts,
                    event,
                    self.token,
                    timeframe,
                    round(price, 2),
                    round(sl, 2) if sl is not None else "",
                    round(tp, 2) if tp is not None else "",
                ]
            )
