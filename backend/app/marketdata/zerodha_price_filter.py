import time
from typing import List, Dict
from kiteconnect import KiteConnect


class ZerodhaPriceFilter:
    """
    Periodically fetches LTPs and marks options as active/inactive
    based on user-defined price range.
    """

    def __init__(
        self,
        kite: KiteConnect,
        options: List[Dict],
        min_price: float,
        max_price: float,
        option_mode: str = "BOTH",  # CE / PE / BOTH
    ):
        if option_mode not in ("CE", "PE", "BOTH"):
            raise ValueError("option_mode must be CE, PE, or BOTH")

        self.kite = kite
        self.options = options
        self.min_price = min_price
        self.max_price = max_price
        self.option_mode = option_mode

    # --------------------------------------------------
    # Single evaluation pass
    # --------------------------------------------------

    def evaluate_once(self) -> List[Dict]:
        """
        Fetch LTPs and return options with active flag + ltp.
        """
        symbols = [
            f"{opt['exchange']}:{opt['tradingsymbol']}"
            for opt in self.options
        ]

        quotes = self.kite.quote(symbols)

        results = []
        for opt in self.options:
            key = f"{opt['exchange']}:{opt['tradingsymbol']}"
            data = quotes.get(key)

            if not data or "last_price" not in data:
                # Keep inactive if no data
                results.append(
                    {
                        **opt,
                        "ltp": None,
                        "active": False,
                    }
                )
                continue

            ltp = float(data["last_price"])

            # Option type filter
            if self.option_mode != "BOTH" and opt["type"] != self.option_mode:
                active = False
            else:
                active = self.min_price <= ltp <= self.max_price

            results.append(
                {
                    **opt,
                    "ltp": ltp,
                    "active": active,
                }
            )

        return results

    # --------------------------------------------------
    # Periodic loop
    # --------------------------------------------------

    def run(
        self,
        interval_sec: int = 30,
        iterations: int | None = None,
    ):
        """
        Periodically evaluate prices.
        If iterations is None → run forever.
        """
        count = 0
        while True:
            evaluated = self.evaluate_once()

            active_count = sum(1 for o in evaluated if o["active"])
            print(
                f"[PRICE] Active options: {active_count}/{len(evaluated)}"
            )

            for o in evaluated:
                status = "ACTIVE" if o["active"] else "inactive"
                print(
                    f"  {o['tradingsymbol']:>20} | "
                    f"LTP={o['ltp']} | {status}"
                )

            print("-" * 60)

            count += 1
            if iterations is not None and count >= iterations:
                break

            time.sleep(interval_sec)
