from pathlib import Path
from datetime import datetime
from typing import Dict, Optional


class CandleDebugLogger:
    """
    Append-only TSV logger.
    MUST NEVER crash the trading engine.
    """

    HEADER = (
        "log_ts\tcandle_ts\tsymbol\tslot\t"
        "open\thigh\tlow\tclose\t"
        "ema8\tema20_low\tema20_high\trsi\t"
        "cond_all\tbuy_allowed\tsignal\n"
    )

    def __init__(self, symbol: str, slot: str):
        try:
            self.symbol = symbol
            self.slot = slot

            date = datetime.now().strftime("%Y-%m-%d")
            self.path = Path(f"app/state/logs/debug/candle_debug_{date}.tsv")
            self.path.parent.mkdir(parents=True, exist_ok=True)

            if not self.path.exists():
                self.path.write_text(self.HEADER)
        except Exception:
            # 🔒 HARD FAILSAFE — disable logger completely
            self.path = None

    # --------------------------------------------------

    def log(
        self,
        candle_ts,
        o,
        h,
        l,
        c,
        ind: Dict,
        checks: Dict[str, bool],
        buy_allowed: bool,
        signal: Optional[str] = None,
    ):
        """
        Absolutely safe logger.
        Any exception here MUST be swallowed.
        """
        try:
            if self.path is None:
                return

            now_ts = datetime.now().strftime("%H:%M:%S")

            # 🔒 Normalize candle_ts (int | str | iso)
            candle_time = ""
            try:
                if isinstance(candle_ts, (int, float)):
                    candle_time = datetime.fromtimestamp(
                        int(candle_ts)
                    ).strftime("%H:%M:%S")
                elif isinstance(candle_ts, str):
                    # ISO / DB timestamp support
                    candle_time = datetime.fromisoformat(
                        candle_ts
                    ).strftime("%H:%M:%S")
            except Exception:
                candle_time = str(candle_ts)

            def f(v):
                try:
                    return "" if v is None else f"{float(v):.2f}"
                except Exception:
                    return ""

            line = (
                f"{now_ts}\t"
                f"{candle_time}\t"
                f"{self.symbol}\t"
                f"{self.slot}\t"
                f"{f(o)}\t{f(h)}\t{f(l)}\t{f(c)}\t"
                f"{f(ind.get('ema8'))}\t"
                f"{f(ind.get('ema20_low'))}\t"
                f"{f(ind.get('ema20_high'))}\t"
                f"{f(ind.get('rsi_smoothed'))}\t"
                f"{checks.get('cond_all', False)}\t"
                f"{buy_allowed}\t"
                f"{signal or ''}\n"
            )

            with self.path.open("a") as fh:
                fh.write(line)

        except Exception:
            # 🔥 NEVER propagate logging errors
            return
