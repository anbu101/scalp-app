# backend/app/marketdata/index_ltp_feeder.py
#
# ── INDEX_LTP_FEEDER_20261015 ── REST fallback for the dashboard index strip.
#
# BUG: MarketIndicesState LTP (NIFTY / BANKNIFTY header, /market_indices) was
# written ONLY by the tick engines that subscribe index tokens — SCALP_V1's
# ZerodhaTickEngine, SCALP_V3, SCALP_V5 — plus one REST seed at startup.
# A machine whose licence runs none of those (e.g. a non-admin running
# Icarus / Tigris / Timberwolf / Velvet) froze the strip at the boot seed all
# day, while every strategy kept trading normally (each prices its own legs).
# Same class as TSG_LTP_PUBLISH (2026-08-12): what a machine shows depended
# on which tick engines happened to run there.
#
# FIX: one feeder that polls kite.ltp() for the two indices in a SINGLE request,
# but ONLY while no tick engine is feeding them:
#   * WS-fed machine (admin): ws_stale() is False → zero REST calls, ever.
#   * No WS index feed (non-admin): one request every POLL_S during market
#     hours. Hands back to WS automatically the moment index ticks resume.
#   * Market closed / holiday / no kite session → no calls.
#   * Rate-limited reply → back off BACKOFF_S (the quote endpoint is shared
#     with strategy quoting; the feeder must always yield to it).
#   * Errors are logged at most once per ERR_LOG_EVERY_S; the loop never dies.
# Display-only: no strategy reads this state for a trading decision on the
# machines it affects (SCALP_V1's skew gate reads it, but SCALP_V1 runs the
# WS engine that keeps it fresh, so the feeder stays idle there).
#
# index_rest_updater.index_polling_loop() remains DEAD CODE (unconditional
# 1 req/s on the quote endpoint — never launched, deliberately not revived).

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.market_indices_state import MarketIndicesState

FENCE = "INDEX_LTP_FEEDER_20261015"

INDEX_SYMBOLS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
}

POLL_S = 3.0            # REST cadence while WS index ticks are absent
WS_STALE_S = 5.0        # no WS index tick for this long → REST takes over
CLOSED_SLEEP_S = 30.0   # market closed: just re-check the clock
NO_KITE_SLEEP_S = 10.0  # no broker session yet (pre-login)
BACKOFF_S = 30.0        # after a rate-limit reply
ERR_LOG_EVERY_S = 300.0


def _default_market_open() -> bool:
    from app.utils.market_hours import is_market_open
    return is_market_open()


def _is_rate_limited(e: Exception) -> bool:
    m = repr(e).lower()
    return "too many requests" in m or "429" in m


class IndexLtpFeeder:
    def __init__(self, zerodha_manager,
                 market_open: Optional[Callable[[], bool]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = write_audit_log):
        self.mgr = zerodha_manager
        self.market_open = market_open or _default_market_open
        self.clock = clock
        self.log = log
        self.mode = "init"            # "ws" | "rest" | "init"
        self.backoff_until = 0.0
        self._last_err_log = -1e18
        self.calls = 0                # kite.ltp requests made (tests/diagnostics)

    def _log_err(self, msg: str, now: float):
        if now - self._last_err_log >= ERR_LOG_EVERY_S:
            self._last_err_log = now
            self.log(msg)

    def _kite(self):
        try:
            return self.mgr.get_data_kite() or self.mgr.get_trade_kite()
        except Exception:
            return None

    def step(self) -> float:
        """One cycle. Blocking (kite.ltp) — run via asyncio.to_thread.
        Returns the number of seconds to sleep before the next cycle."""
        now = self.clock()
        try:
            if not self.market_open():
                return CLOSED_SLEEP_S
        except Exception:
            return CLOSED_SLEEP_S

        if not MarketIndicesState.ws_stale(tuple(INDEX_SYMBOLS), WS_STALE_S, now=now):
            if self.mode != "ws":
                if self.mode == "rest":
                    self.log("[INDEX][FEEDER] WS index ticks resumed — REST fallback idle")
                self.mode = "ws"
            return POLL_S

        if now < self.backoff_until:
            return min(POLL_S, self.backoff_until - now) or POLL_S

        kite = self._kite()
        if kite is None:
            self._log_err("[INDEX][FEEDER] no kite session yet — index strip waits", now)
            return NO_KITE_SLEEP_S

        try:
            self.calls += 1
            data = kite.ltp(list(INDEX_SYMBOLS.values())) or {}
        except Exception as e:
            if _is_rate_limited(e):
                self.backoff_until = now + BACKOFF_S
                self._log_err(f"[INDEX][FEEDER] rate-limited — backing off {BACKOFF_S:.0f}s", now)
            else:
                self._log_err(f"[INDEX][FEEDER] ltp poll failed: {e!r}", now)
            return POLL_S

        got = 0
        for name, sym in INDEX_SYMBOLS.items():
            row = data.get(sym) if isinstance(data, dict) else None
            try:
                v = float((row or {}).get("last_price") or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                MarketIndicesState.update_ltp(name, v, source="rest")
                got += 1

        if got and self.mode != "rest":
            self.mode = "rest"
            self.log(f"[INDEX][FEEDER] no WS index ticks — REST fallback active "
                     f"(every {POLL_S:.0f}s, single request)")
        return POLL_S


async def index_ltp_feeder(zerodha_manager, _feeder: Optional[IndexLtpFeeder] = None,
                           _sleep=asyncio.sleep, _max_cycles: Optional[int] = None):
    """Perpetual task (launched under _supervise in api_server). Never returns
    in production; `_max_cycles` exists for tests only."""
    f = _feeder or IndexLtpFeeder(zerodha_manager)
    write_audit_log(f"[INDEX][FEEDER] started ({FENCE})")
    n = 0
    while _max_cycles is None or n < _max_cycles:
        n += 1
        try:
            s = await asyncio.to_thread(f.step)
        except Exception as e:
            # The feeder must never die: a dead feeder silently re-creates
            # the frozen-index-strip bug on every non-WS machine.
            write_audit_log(f"[INDEX][FEEDER] cycle error: {e!r}")
            s = POLL_S
        await _sleep(s)
