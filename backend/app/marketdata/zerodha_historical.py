from pathlib import Path
from datetime import datetime, timedelta
import csv

from app.marketdata.candle_builder import Candle


class ZerodhaHistoricalFetcher:
    def __init__(self, kite, base_dir: Path, days: int = 2):
        self.kite = kite
        self.base_dir = base_dir.expanduser()
        self.days = days

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------

    def fetch(
        self,
        symbol: str,
        instrument_token: int,
        expiry: str,
        timeframe_sec: int,
    ):
        tf_label = self._tf_label(timeframe_sec)
        csv_path = self._csv_path(symbol, expiry, tf_label)
        print(f"[DEBUG] ZerodhaHistoricalFetcher CSV path = {csv_path}")

        from_dt = datetime.now() - timedelta(days=self.days)
        to_dt = datetime.now()

        print(
            f"[HIST] {symbol} | TF={tf_label} | "
            f"from={from_dt} to={to_dt}"
        )

        candles = []

        try:
            data = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_dt,
                to_date=to_dt,
                interval=tf_label,
                continuous=False,
                oi=False,
            )

            for row in data:
                start_ts = int(row["date"].timestamp())
                end_ts = start_ts + timeframe_sec

                candles.append(
                    Candle(
                        start_ts=start_ts,
                        end_ts=end_ts,
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        source="ZERODHA",
                    )
                )

            self._append_to_csv(csv_path, candles)

        except Exception as e:
            print(f"[HIST] ERROR fetching candles: {e}")

        # Always load from CSV (single source of truth)
        return self._load_from_csv(csv_path)

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def _tf_label(self, timeframe_sec: int) -> str:
        if timeframe_sec == 60:
            return "minute"
        if timeframe_sec == 180:
            return "3minute"
        if timeframe_sec == 300:
            return "5minute"
        raise ValueError(f"Unsupported timeframe: {timeframe_sec}")


    def _csv_path(self, symbol: str, expiry: str, tf_label: str) -> Path:
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").date()

        path = (
            self.base_dir
            / "NIFTY"
            / expiry_dt.isoformat()
            / symbol
        )
        path.mkdir(parents=True, exist_ok=True)

        return path / f"{tf_label}.csv"

    def _append_to_csv(self, csv_path: Path, candles):
        write_header = not csv_path.exists()

        with csv_path.open("a", newline="") as f:
            writer = csv.writer(f)

            if write_header:
                writer.writerow(
                    ["start_ts", "end_ts", "open", "high", "low", "close"]
                )

            for c in candles:
                writer.writerow(
                    [
                        c.start_ts,
                        c.end_ts,
                        c.open,
                        c.high,
                        c.low,
                        c.close,
                    ]
                )

    def _load_from_csv(self, csv_path: Path):
        candles = []

        if not csv_path.exists():
            return candles

        with csv_path.open() as f:
            reader = csv.DictReader(f)

            for row in reader:
                candles.append(
                    Candle(
                        start_ts=int(row["start_ts"]),
                        end_ts=int(row["end_ts"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        source="ZERODHA",
                    )
                )

        return candles
