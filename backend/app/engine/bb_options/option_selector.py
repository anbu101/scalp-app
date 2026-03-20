from typing import Optional, Tuple, List
from datetime import datetime
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
        self.max_premium = max_premium
        self.scan_strikes = scan_strikes
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
    ) -> Optional[Tuple[str, float]]:

        if direction not in ("CE", "PE"):
            raise ValueError(f"Invalid direction: {direction}")

        atm = int((futures_price + 50) // 100) * 100
        monthly_expiry = resolve_current_monthly_expiry()

        write_audit_log(
            f"[BB_SELECTOR] ATM={atm} direction={direction} "
            f"scan_strikes={self.scan_strikes} max_premium={self.max_premium}"
        )

        # ==================================================
        # STEP 1 — READ ATM PREMIUM (for estimation)
        # ==================================================

        atm_symbol = self._find_option_symbol(atm, direction, monthly_expiry)
        atm_ltp = None

        if atm_symbol:
            atm_ltp = self._resolve_ltp(atm_symbol)
            write_audit_log(f"[BB_ESTIMATE] ATM {atm_symbol} ltp={atm_ltp}")

        # ==================================================
        # STEP 2 — ESTIMATE STRIKE DISTANCE
        # ==================================================

        estimated_strike = None

        if atm_ltp and atm_ltp > self.max_premium:

            premium_gap = atm_ltp - self.max_premium

            # empirical decay approximation
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

        # --- first scan around estimated strike
        if estimated_strike:

            for i in range(-5, 6):
                strike = estimated_strike + (i * 100)
                candidate_strikes.append(strike)

        # --- fallback scan (original logic)
        fallback_strikes = self._build_strike_list(atm, direction)

        for s in fallback_strikes:
            if s not in candidate_strikes:
                candidate_strikes.append(s)

        # ==================================================
        # STEP 3b — BATCH PREFETCH LTPs (PERFORMANCE)
        #
        # Resolve all candidate symbols upfront and fetch any
        # that are missing from LTPStore in a SINGLE REST call
        # instead of one call per symbol inside the loop below.
        #
        # This is purely additive — it only seeds LTPStore.
        # Step 4's logic (including _resolve_ltp) is unchanged.
        # If the batch call fails, Step 4 falls back to
        # individual REST calls exactly as before.
        # ==================================================

        candidate_symbols = [
            sym for strike in candidate_strikes
            if (sym := self._find_option_symbol(strike, direction, monthly_expiry))
        ]

        self._batch_prefetch_ltps(candidate_symbols)

        # ==================================================
        # STEP 4 — FIND BEST PREMIUM
        # ==================================================

        best_symbol = None
        best_price = None
        best_diff = float("inf")

        for strike in candidate_strikes:

            symbol = self._find_option_symbol(strike, direction, monthly_expiry)

            if not symbol:
                write_audit_log(f"[BB_DEBUG] strike={strike} symbol_not_found")
                continue

            ltp = self._resolve_ltp(symbol)

            #write_audit_log(f"[BB_DEBUG] {symbol} ltp={ltp}")

            if ltp is None or ltp <= 0:
                continue

            if ltp <= self.max_premium:

                diff = self.max_premium - ltp

                if diff < best_diff:
                    best_diff = diff
                    best_symbol = symbol
                    best_price = ltp

        if best_symbol:
            write_audit_log(f"[BB] Selected {best_symbol} @ {best_price}")
            return best_symbol, best_price

        write_audit_log("[BB] No option selected within premium constraints")
        return None

    # ==================================================
    # BATCH LTP PREFETCH
    #
    # Fetches LTPs for all symbols not already in LTPStore
    # in a single kite.ltp() call (supports up to 500 instruments).
    # Seeds LTPStore so _resolve_ltp() hits cache in Step 4.
    # Completely safe: any error is logged and silently ignored —
    # Step 4 will still fall back to individual REST calls.
    # ==================================================

    def _batch_prefetch_ltps(self, symbols: List[str]) -> None:

        # Only fetch symbols genuinely missing from LTPStore
        missing = [s for s in symbols if LTPStore.get(s) is None]

        if not missing:
            return

        try:
            kite = self._broker.get_trade_kite()
            if not kite:
                return

            instruments = [f"NFO:{s}" for s in missing]

            # kite.ltp() accepts up to 500 instruments per call
            quotes = kite.ltp(instruments)

            seeded = 0
            for sym in missing:
                key  = f"NFO:{sym}"
                data = quotes.get(key)
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
            # Non-fatal — Step 4 will fall back to individual REST calls
            write_audit_log(f"[BB_SELECTOR] Batch prefetch failed (non-fatal): {e}")

    # ==================================================
    # LTP RESOLUTION (WS → REST FALLBACK)
    # Unchanged — still works exactly as before.
    # After batch prefetch, most symbols hit the cache
    # immediately and never reach the REST fallback.
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

        strikes = []

        if direction == "CE":
            for i in range(1, self.scan_strikes + 1):
                strikes.append(atm + i * 100)
        else:
            for i in range(1, self.scan_strikes + 1):
                strikes.append(atm - i * 100)

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
            write_audit_log(
                f"[BB_DEBUG][SYMBOL_NOT_FOUND] "
                f"strike={strike} "
                f"direction={direction} "
                f"expiry={expiry} "
                f"expiry_type={type(expiry)}"
            )
            return None

        return opt_df.iloc[0]["tradingsymbol"]