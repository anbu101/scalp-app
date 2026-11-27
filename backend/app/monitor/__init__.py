# backend/app/monitor/ — ── FLEET_MONITOR_20261127 ── (TD-110)
#
# The app half of the Fleet Monitor portal: every LIVE trade event and every
# critical alert is written to a durable local outbox (outbox.py) by one-line
# taps in api/telegram_api.py (tap.py), and a supervised loop (shipper.py)
# sends the outbox plus a once-a-minute LIVE snapshot (snapshot.py) to the
# monitor service on the license droplet (monitor_server/).
#
# HARD RULES
#   * never fails, blocks or slows the trading path: every entry point
#     swallows its own errors; the tap is one small SQLite insert;
#   * LIVE only: paper trades and PAPER-mode alerts never leave the machine;
#   * allowlisted fields only: no broker token, key, login URL or button.
