from datetime import datetime, date
from typing import List, Dict, Optional


class OptionSelector:
    """
    Selects CURRENT weekly CE and PE based on:
    - Nearest future expiry
    - User premium range (Kite REST LTP)
    - ATM proximity
    - Independent CE / PE selection
    - MAX 2 CE and MAX 2 PE

    REST-only. Deterministic.
    """

    def __init__(
        self,
        instruments: List[Dict],
        price_min: float,
        price_max: float,
        trade_mode: str,          # CE / PE / BOTH
        atm_range: int,
        strike_step: int,
        index_symbol: str = "NIFTY",
        kite=None,                # REQUIRED
    ):
        self.instruments = instruments
        self.price_min = price_min
        self.price_max = price_max
        self.trade_mode = trade_mode.upper()
        self.atm_range = atm_range
        self.strike_step = strike_step
        self.index_symbol = index_symbol
        self.kite = kite

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------

    def select(self) -> Optional[Dict]:
        today = date.today()
        valid_opts = []

        # --------------------------------------------------
        # 1️⃣ Build symbol list ONCE
        # --------------------------------------------------
        keys = []
        inst_map = {}

        for inst in self.instruments:
            ts = inst.get("tradingsymbol")
            if not ts or not ts.startswith(self.index_symbol):
                continue

            ex = inst.get("exchange", "NFO")
            key = f"{ex}:{ts}"

            keys.append(key)
            inst_map[key] = inst

        if not keys:
            return None

        # --------------------------------------------------
        # 2️⃣ Bulk LTP fetch (ONCE)
        # --------------------------------------------------
        try:
            ltp_raw = {}

            for i in range(0, len(keys), 50):
                batch = keys[i:i + 50]
                try:
                    data = self.kite.ltp(batch)
                    ltp_raw.update(data)
                except Exception:
                    continue

        except Exception:
            return None
        print("LTP COUNT =", len(ltp_raw))

        # --------------------------------------------------
        # 3️⃣ Filter instruments
        # --------------------------------------------------
        for key, q in ltp_raw.items():
            ltp = q.get("last_price")
            if ltp is None:
                continue

            inst = inst_map.get(key)
            if not inst:
                continue

            opt_type = inst.get("instrument_type") or inst.get("type")
            if opt_type not in ("CE", "PE"):
                continue

            expiry_date = self._parse_expiry(inst.get("expiry"))
            if expiry_date < today:
                continue

            if not (self.price_min <= ltp <= self.price_max):
                continue

            valid_opts.append({
                "symbol": inst["tradingsymbol"],
                "tradingsymbol": inst["tradingsymbol"],
                "strike": float(inst.get("strike")),
                "type": opt_type,
                "expiry": expiry_date.isoformat(),
                "_expiry_date": expiry_date,
                "ltp": float(ltp),
            })

        if not valid_opts:
            return None

        # --------------------------------------------------
        # 4️⃣ Nearest expiry
        # --------------------------------------------------
        nearest_expiry = min(o["_expiry_date"] for o in valid_opts)
        opts = [o for o in valid_opts if o["_expiry_date"] == nearest_expiry]
        if not opts:
            return None

        # --------------------------------------------------
        # 5️⃣ ATM + range
        # --------------------------------------------------
        atm = self._infer_atm(opts)
        lower = atm - self.atm_range
        upper = atm + self.atm_range

        opts = [o for o in opts if lower <= o["strike"] <= upper]
        if not opts:
            return None

        # --------------------------------------------------
        # 6️⃣ Pick 2 CE + 2 PE
        # --------------------------------------------------
        selected_ce, selected_pe = [], []

        for side in ("CE", "PE"):
            if self.trade_mode != "BOTH" and self.trade_mode != side:
                continue

            side_opts = [o for o in opts if o["type"] == side]
            side_opts.sort(key=lambda x: abs(x["strike"] - atm))

            chosen = side_opts[:2]
            for c in chosen:
                c.pop("_expiry_date", None)

            (selected_ce if side == "CE" else selected_pe).extend(chosen)

        if not selected_ce and not selected_pe:
            return None

        return {
            "expiry": nearest_expiry.isoformat(),
            "atm": atm,
            "CE": selected_ce,
            "PE": selected_pe,
        }

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def _parse_expiry(self, exp) -> date:
        if isinstance(exp, date):
            return exp
        return datetime.strptime(str(exp), "%Y-%m-%d").date()

    def _infer_atm(self, options: List[Dict]) -> int:
        strikes = sorted({int(o["strike"]) for o in options})
        return strikes[len(strikes) // 2]
