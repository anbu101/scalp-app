# ── WS_WATCHDOG_FAST_20261119 ──
# One set of numbers for every Kite WebSocket "connected but silent" watchdog
# (TMA, TMA2/VET, SCALP_V1, SCALP_V3, SCALP_V5, shared feed).
#
# 2026-11-19 (Dhivakar's Mac): the feed stalled six times between 11:49 and
# 12:02. Each time the watchdog waited 120 s of silence, plus up to 30 s for its
# next poll, before reconnecting; the reconnect itself healed it in 4–41 s.
# Waiting less is safe: NIFTY spot ticks about once a second, and an option
# socket carries many contracts, so 30–45 s of nothing is never normal.

IST_OFFSET_S = 5 * 3600 + 30 * 60

SPOT_SILENT_S = 30        # NIFTY-spot sockets (TMA, TMA2, VET) — those two files hold literal copies; test_ws_watchdog_fast.py keeps them equal
OPTION_SILENT_S = 45      # option / multi-token sockets (V1, V3, V5, shared feed)
POLL_S = 5
COOLDOWN_S = 90           # min gap between two reconnects; a reconnect can take ~40 s to heal
BACKOFF_S = 1800          # after 3 reconnects that did not help, one per 30 min

EARLIEST_ACTION_MIN = 9 * 60 + 17   # never reconnect before 09:17:00 IST (as with the old 120 s rule)


def open_floor_ts(now: float, silent_s: float) -> float:
    """Earliest point silence may be counted from today, so the first
    reconnect can come no sooner than 09:17:00 IST."""
    day = (int(now + IST_OFFSET_S) // 86400) * 86400 - IST_OFFSET_S
    return float(day + EARLIEST_ACTION_MIN * 60 - silent_s)
