from typing import Optional, Tuple, List, Dict
from datetime import datetime
import time
from app.fetcher.zerodha_instruments import load_instruments_df
from app.marketdata.ltp_store import LTPStore
from app.event_bus.audit_logger import write_audit_log
from app.engine.bb_options.monthly_expiry_resolver import resolve_current_monthly_expiry
from app.brokers.zerodha_manager import ZerodhaManager


class OptionSelector:

    def __init__(
        self,
        max_premium: float,
        scan_strikes: int,
        ltp_stale_seconds: int = 10,
    ):
        self.max_premium       = max_premium   # construction-time default only
        self.scan_strikes      = scan_strikes
        self.ltp_stale_seconds = ltp_stale_seconds

        self.instruments_df = load_instruments_df()
        self._broker = ZerodhaManager()

    # ==================================================
    # PUBLIC
    # ==================================================

    def select(
        self,
        futures_price: float,
        direction: str,
        max_premium_override: Optional[float] = None,
    ) -> Optional[Tuple[str, float]]:
        """
        Select the best option for the given direction.

        max_premium_override: when provided (e.g. read live from config
        by the caller), this value is used instead of self.max_premium.
        This allows Settings UI changes to take effect on the next trade
        without restarting the engine.
        """

        if direction not in ("CE", "PE"):
            raise ValueError(f"Invalid direction: {direction}")

        # Resolve effective max_premium: caller override wins
        effective_max_premium = (
            max_premium_override
            if max_premium_override is not None
            else self.max_premium
        )

        atm = int((futures_price + 50) // 100) * 100
        monthly_expiry = resolve_current_monthly_expiry()

        write_audit_log(
            f"[BB_SELECTOR] ATM={atm} direction={direction} "
            f"scan_strikes={self.scan_strikes} "
            f"max_premium={effective_max_premium}"
        )

        # ==================================================
        # STEP 1 — READ ATM PREMIUM (for estimation)
        # ==================================================

        atm_symbol = self._find_option_symbol(atm, direction, monthly_expiry)
        atm_ltp = None

        if atm_symbol:
            atm_ltp = self._resolve_ltp(atm_symbol)
            write_audit_log(
                f"[BB_ESTIMATE] ATM {atm_symbol} ltp={atm_ltp}"
            )

        # ==================================================
        # STEP 2 — ESTIMATE STRIKE DISTANCE
        # ==================================================

        estimated_strike = None

        if atm_ltp and atm_ltp > effective_max_premium:
            premium_gap = atm_ltp - effective_max_premium

            # Empirical decay approximation (~30-40 points per strike)
            approx_strikes = int(premium_gap / 35)

            if direction == "CE":
                estimated_strike = atm + (approx_strikes * 100)
            else:
                estimated_strike = atm - (approx_strikes * 100)

            write_audit_log(
                f"[BB_ESTIMATE] gap={premium_gap:.2f} "
                f"approx_strikes={approx_strikes} "
                f"estimated_strike={estimated_strike}"
            )

        # ==================================================
        # STEP 3 — BUILD CANDIDATE STRIKE LIST
        # ==================================================

        candidate_strikes: List[int] = []

        if estimated_strike:
            for i in range(-5, 6):
                strike = estimated_strike + (i * 100)
                candidate_strikes.append(strike)

        fallback_strikes = self._build_strike_list(atm, direction)

        for s in fallback_strikes:
            if s not in candidate_strikes:
                candidate_strikes.append(s)

        # ==================================================
        # STEP 3b — BUILD STRIKE → SYMBOL MAP (ONCE)
        # ==================================================

        strike_symbol_map: Dict[int, str] = {}

        for strike in candidate_strikes:
            sym = self._find_option_symbol(strike, direction, monthly_expiry)
            if sym:
                strike_symbol_map[strike] = sym

        # ==================================================
        # STEP 3c — BATCH PREFETCH LTPs
        # ==================================================

        self._batch_prefetch_ltps(list(strike_symbol_map.values()))

        # ==================================================
        # STEP 4 — FIND BEST PREMIUM
        # ==================================================

        best_symbol = None
        best_price  = None
        best_diff   = float("inf")

        for strike, symbol in strike_symbol_map.items():

            ltp = self._resolve_ltp(symbol)

            if ltp is None or ltp <= 0:
                continue

            if ltp <= effective_max_premium:
                diff = effective_max_premium - ltp

                if diff < best_diff:
                    best_diff   = diff
                    best_symbol = symbol
                    best_price  = ltp

        if best_symbol:
            write_audit_log(
                f"[BB] Selected {best_symbol} @ {best_price}"
            )
            return best_symbol, best_price

        write_audit_log(
            "[BB] No option selected within premium constraints"
        )
        return None

    # ==================================================
    # BATCH LTP PREFETCH
    # ==================================================

    _STALE_SECONDS = 60

    def _batch_prefetch_ltps(self, symbols: List[str]) -> None:

        now = time.time()

        def is_stale(sym: str) -> bool:
            data = LTPStore.get_with_timestamp(sym)
            if data is None:
                return True
            _, ts = data
            return (now - ts) > self._STALE_SECONDS

        missing = [s for s in symbols if is_stale(s)]

        if not missing:
            return

        try:
            kite = self._broker.get_trade_kite()
            if not kite:
                return

            quotes = kite.ltp([f"NFO:{s}" for s in missing])

            seeded = 0
            for sym in missing:
                data = quotes.get(f"NFO:{sym}")
                if data:
                    price = data.get("last_price")
                    if price and price > 0:
                        LTPStore.update(sym, price)
                        seeded += 1

            write_audit_log(
                f"[BB_SELECTOR] Batch LTP prefetch: "
                f"{len(missing)} fetched, {seeded} seeded into LTPStore"
            )

        except Exception as e:
            write_audit_log(
                f"[BB_SELECTOR] Batch prefetch failed (non-fatal): {e}"
            )

    # ==================================================
    # LTP RESOLUTION (WS → REST FALLBACK)
    # ==================================================

    def _resolve_ltp(self, symbol: str) -> Optional[float]:

        ltp_data = LTPStore.get(symbol)

        if ltp_data is not None:

            if isinstance(ltp_data, tuple):
                ltp, ts = ltp_data

                if ts is None:
                    return None

                if (datetime.utcnow().timestamp() - ts) > self.ltp_stale_seconds:
                    return None

                return ltp

            write_audit_log(f"[BB][LTP_MISSING] {symbol}")
            return ltp_data

        try:
            kite = self._broker.get_trade_kite()
            if not kite:
                return None

            quote = kite.ltp(f"NFO:{symbol}")
            return quote[f"NFO:{symbol}"]["last_price"]

        except Exception:
            return None

    # ==================================================
    # INTERNAL
    # ==================================================

    def _build_strike_list(self, atm: int, direction: str) -> List[int]:
        """
        Build the candidate strike list for option selection.

        Scans BOTH OTM and ITM strikes for each direction.

        WHY ITM IS NEEDED:
        On normal days, OTM options have enough time value to carry
        meaningful premium (100–500+), so scanning only OTM works fine.

        On expiry day, time value collapses to near zero for all OTM
        options. With ATM at 55900 and max_premium=900, every OTM PE
        has sub-200 premium — the selector always picks the nearest OTM
        (highest premium in the range) regardless of max_premium setting.

        ITM options on expiry day carry intrinsic value only, e.g.:
          - 56600PE with ATM=55900 → ~700 intrinsic → within 900 cap
          - 56700PE → ~800 intrinsic → within 900 cap
          - 56800PE → ~900 intrinsic → at/over cap, filtered out

        On normal days (>5 days to expiry), ITM options carry
        intrinsic + time value and typically exceed max_premium,
        so they are filtered out harmlessly in Step 4.

        OTM count  : self.scan_strikes (user-configured, e.g. 60)
        ITM count  : capped at 20 — enough to cover the full
                     intrinsic range up to max_premium without
                     exploding the batch LTP prefetch size.
        """
        strikes = []

        itm_count = min(20, self.scan_strikes)

        if direction == "CE":
            # OTM CE: strikes above ATM (higher strike = more OTM)
            for i in range(1, self.scan_strikes + 1):
                strikes.append(atm + i * 100)
            # ITM CE: strikes below ATM (lower strike = more ITM)
            # On expiry day these carry intrinsic value within max_premium range.
            for i in range(1, itm_count + 1):
                strikes.append(atm - i * 100)
        else:
            # OTM PE: strikes below ATM (lower strike = more OTM)
            for i in range(1, self.scan_strikes + 1):
                strikes.append(atm - i * 100)
            # ITM PE: strikes above ATM (higher strike = more ITM)
            # On expiry day these carry intrinsic value within max_premium range.
            for i in range(1, itm_count + 1):
                strikes.append(atm + i * 100)

        return strikes

    def _find_option_symbol(self, strike, direction, expiry):

        df = self.instruments_df

        if not hasattr(self, "_expiry_normalized"):
            df["expiry_norm"] = df["expiry"].apply(
                lambda x: x.date() if hasattr(x, "date") else x
            )
            self._expiry_normalized = True

        opt_df = df[
            (df["segment"] == "NFO-OPT")
            & (df["name"] == "BANKNIFTY")
            & (df["strike"] == strike)
            & (df["instrument_type"] == direction)
            & (df["expiry_norm"] == expiry)
        ]

        if opt_df.empty:
            return None

        return opt_df.iloc[0]["tradingsymbol"]