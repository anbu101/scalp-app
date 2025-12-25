import os
import csv
from datetime import datetime
from typing import List, Dict


class CandleStore:
    """
    CSV-based candle persistence.

    Supports Zerodha CSV format:
    start_ts,end_ts,open,high,low,close
    """

    def __init__(
        self,
        base_dir: str,
        max_candles: int = 5000,
        stale_gap_minutes: int = 1440,
    ):
        self.base_dir = os.path.expanduser(base_dir)
        self.max_candles = max_candles
        self.stale_gap_minutes = stale_gap_minutes

    # --------------------------------------------------
    # Path helpers
    # --------------------------------------------------

    def _symbol_dir(self, index: str, expiry: str, symbol: str) -> str:
        return os.path.join(self.base_dir, index, expiry, symbol)

    def _file_path(
        self,
        index: str,
        expiry: str,
        symbol: str,
        timeframe: str,
    ) -> str:
        return os.path.join(
            self._symbol_dir(index, expiry, symbol),
            f"{timeframe}.csv",
        )

    # --------------------------------------------------
    # Load candles (ZERODHA FORMAT — FIXED)
    # --------------------------------------------------

    def load(
        self,
        index: str,
        expiry: str,
        symbol: str,
        timeframe: str,
    ) -> List[Dict]:
        """
        Load candles from Zerodha CSV.
        Converts epoch timestamps → datetime.
        """
        path = self._file_path(index, expiry, symbol, timeframe)

        if not os.path.exists(path):
            return []

        candles: List[Dict] = []

        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                # ---- ZERODHA TIMESTAMP HANDLING (FINAL) ----
                if "start_ts" in row:
                    ts = int(float(row["start_ts"]))
                elif "time" in row:
                    ts = int(datetime.fromisoformat(row["time"]).timestamp())
                else:
                    raise KeyError(
                        f"No usable timestamp column found. "
                        f"Columns: {list(row.keys())}"
                    )

                candles.append(
                    {
                        "time": datetime.fromtimestamp(ts),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume", 0)),
                    }
                )

        return candles[-self.max_candles :]

    # --------------------------------------------------
    # Save candles (optional)
    # --------------------------------------------------

    def save(
        self,
        index: str,
        expiry: str,
        symbol: str,
        timeframe: str,
        candles: List[Dict],
    ):
        os.makedirs(
            self._symbol_dir(index, expiry, symbol),
            exist_ok=True,
        )

        path = self._file_path(index, expiry, symbol, timeframe)

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["start_ts", "open", "high", "low", "close", "volume"],
            )
            writer.writeheader()

            for c in candles[-self.max_candles :]:
                writer.writerow(
                    {
                        "start_ts": int(c["time"].timestamp()),
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c.get("volume", 0),
                    }
                )

    # --------------------------------------------------
    # Freshness check
    # --------------------------------------------------

    def is_stale(self, candles: List[Dict]) -> bool:
        if not candles:
            return True

        last_time = candles[-1]["time"]
        gap_minutes = (datetime.now() - last_time).total_seconds() / 60.0
        return gap_minutes > self.stale_gap_minutes
