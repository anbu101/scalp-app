from datetime import datetime, timedelta, date
from app.marketdata.market_indices_state import MarketIndicesState
from app.event_bus.audit_logger import write_audit_log
from app.fetcher.zerodha_instruments import load_instruments_df

# ── INDEX_PREVCLOSE_ROLLOVER BEGIN (imports) ──
import asyncio
import time
# ── INDEX_PREVCLOSE_ROLLOVER END (imports) ──


INDEX_MAP = {
    "NIFTY":     "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
}


def seed_index_ltp_once(kite):
    """
    Seed index LTP once at startup so UI never sees None on first load.
    """
    try:
        data = kite.ltp([
            "NSE:NIFTY 50",
            "NSE:NIFTY BANK",
        ])

        if "NSE:NIFTY 50" in data:
            ltp = float(data["NSE:NIFTY 50"]["last_price"])
            if ltp > 0:
                MarketIndicesState.update_ltp("NIFTY", ltp)
                write_audit_log(f"[INDEX] NIFTY LTP seeded: {ltp}")

        if "NSE:NIFTY BANK" in data:
            ltp = float(data["NSE:NIFTY BANK"]["last_price"])
            if ltp > 0:
                MarketIndicesState.update_ltp("BANKNIFTY", ltp)
                write_audit_log(f"[INDEX] BANKNIFTY LTP seeded: {ltp}")

    except Exception as e:
        write_audit_log(f"[INDEX][WARN] Failed to seed index LTP: {e}")


def load_index_prev_close_once(kite):
    """
    Load previous trading day's close for NIFTY & BANKNIFTY.

    KEY FIX: We find the last candle whose date is strictly BEFORE today.
    This correctly handles:
      - Market holidays (Good Friday etc.) - skipped naturally
      - Monday morning - picks Thursday, not Friday if Friday was holiday
      - Intraday restarts - today's partial candle is excluded

    Always overwrites whatever is in MarketIndicesState so the polling
    loop can never corrupt it with ltp().ohlc data.

    ROLLOVER NOTE (2026-08-13): each prev_close is stamped with
    valid_for=today. index_prev_close_watchdog() re-runs this loader
    when the process crosses a date boundary, so a backend left running
    overnight no longer serves yesterday's reference (which flipped
    BANKNIFTY's change sign on the dashboard).
    """
    try:
        df = load_instruments_df()
        today = date.today()

        for index_name, trading_symbol in INDEX_MAP.items():
            row = df[
                (df["segment"] == "INDICES")
                & (df["tradingsymbol"] == trading_symbol)
            ]

            if row.empty:
                write_audit_log(f"[INDEX][ERROR] Instrument not found for {index_name}")
                continue

            token = int(row.iloc[0]["instrument_token"])

            # Fetch last 15 days to cover long weekends + back-to-back holidays
            to_date   = datetime.now()
            from_date = to_date - timedelta(days=15)

            candles = kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day",
            )

            if not candles:
                write_audit_log(f"[INDEX][ERROR] No historical candles for {index_name}")
                continue

            write_audit_log(
                f"[INDEX] {index_name} got {len(candles)} daily candles, "
                f"last 5 dates: {[str(c['date'].date()) for c in candles[-5:]]}"
            )

            # ── Find last candle strictly before today ──────────────────
            # Works regardless of holidays, weekends, or whether Zerodha
            # has included today's partial candle in the response.
            prev_candle = None
            for candle in reversed(candles):
                candle_date = (
                    candle["date"].date()
                    if hasattr(candle["date"], "date")
                    else candle["date"]
                )
                if candle_date < today:
                    prev_candle = candle
                    break

            if prev_candle is None:
                write_audit_log(f"[INDEX][ERROR] No previous trading day found for {index_name}")
                continue

            prev_close = float(prev_candle["close"])
            prev_date  = (
                prev_candle["date"].date()
                if hasattr(prev_candle["date"], "date")
                else prev_candle["date"]
            )

            # ── INDEX_PREVCLOSE_ROLLOVER BEGIN (stamp) ──
            MarketIndicesState.set_prev_close(index_name, prev_close, valid_for=today)
            # ── INDEX_PREVCLOSE_ROLLOVER END (stamp) ──

            write_audit_log(
                f"[INDEX] {index_name} prev_close={prev_close} "
                f"from trading day {prev_date}"
            )

    except Exception as e:
        write_audit_log(f"[INDEX][FATAL] Failed to load prev close: {e}")


# ── INDEX_PREVCLOSE_ROLLOVER BEGIN (watchdog) ──
# Cadence: rollover CHECK every 60s (clearing stale prev_close is
# instant + in-memory, fail-closed). Reload ATTEMPTS throttled to one
# per 300s: historical_data is rate-limited, and after the overnight
# Zerodha token expiry every attempt fails until the morning login, so
# the watchdog must idle politely and self-heal once a fresh kite
# appears.
_ROLLOVER_CHECK_SECS  = 60
_RELOAD_ATTEMPT_SECS  = 300


async def index_prev_close_watchdog(zerodha_manager):
    """
    Daily rollover watchdog for index prev_close.

    Bug this kills: load_index_prev_close_once() ran ONCE at process
    startup. A backend left running past midnight kept serving the old
    reference, so dashboard change/% was computed vs a two-day-old
    close (2026-08-13: BANKNIFTY shown +158 while Kite showed -276).

    Behavior:
      - Every 60s: MarketIndicesState.prev_close_reload_needed(today)
        clears any stale-dated prev_close immediately (UI shows "—",
        never a wrong number) and reports whether a reload is due.
      - Reload attempts run at most every 300s, pulling a FRESH kite
        from ZerodhaManager each time — the startup kite dies with the
        overnight token, the manager's post-login kite does not.
      - loader runs via asyncio.to_thread: kite.historical_data() is
        blocking and must not stall the event loop.
      - Also self-heals a startup where is_trade_ready() was False and
        the gated startup loader never ran (store empty counts as
        reload-needed).

    NOTE: index_rest_updater.index_polling_loop() looks like the natural
    home for this but is DEAD CODE — never launched. Live LTP comes from
    the tick engines. Do not wire the check there.
    """
    write_audit_log("[INDEX] prev_close rollover watchdog started")

    last_attempt = 0.0  # monotonic; 0 → first eligible attempt fires immediately

    while True:
        try:
            today = date.today()

            if MarketIndicesState.prev_close_reload_needed(today):
                now = time.monotonic()
                if now - last_attempt >= _RELOAD_ATTEMPT_SECS:
                    last_attempt = now

                    kite = None
                    try:
                        kite = (
                            zerodha_manager.get_data_kite()
                            or zerodha_manager.get_trade_kite()
                        )
                    except Exception as e:
                        write_audit_log(f"[INDEX][WARN] watchdog kite lookup failed: {e}")

                    if kite is None:
                        write_audit_log(
                            "[INDEX] prev_close reload due but no kite session yet "
                            "— will retry"
                        )
                    else:
                        write_audit_log(
                            f"[INDEX] prev_close missing/stale for {today} — reloading"
                        )
                        await asyncio.to_thread(load_index_prev_close_once, kite)

        except Exception as e:
            # Watchdog must never die; a dead watchdog re-creates the
            # original stale-reference bug silently.
            write_audit_log(f"[INDEX][WARN] rollover watchdog cycle error: {e}")

        await asyncio.sleep(_ROLLOVER_CHECK_SECS)
# ── INDEX_PREVCLOSE_ROLLOVER END (watchdog) ──