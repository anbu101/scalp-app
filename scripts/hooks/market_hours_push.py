#!/usr/bin/env python3
# scripts/hooks/market_hours_push.py  — ── PRECOMMIT_20261117 ── (TD-042)
#
# pre-push hook: no push Mon–Fri 09:00–15:30 IST (a push to main can start a
# release build that friends' apps pick up mid-session). Override for one
# push:  SCALP_PUSH_OK=1 git push
# IST is UTC+05:30 all year (no DST), so no time-zone database is needed.
# NSE holidays are not known here: on a holiday use the override.
# SCALP_HOOK_NOW=<ISO time with offset> fakes the clock (tests only).
# Stdlib only. Exit 0 = push allowed, 1 = blocked.
import datetime as dt
import os
import sys

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")
OPEN, CLOSE = (9, 0), (15, 30)          # both minutes inclusive


def now_ist():
    fake = os.environ.get("SCALP_HOOK_NOW")
    if fake:
        t = dt.datetime.fromisoformat(fake.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=IST)
        return t.astimezone(IST)
    return dt.datetime.now(dt.timezone.utc).astimezone(IST)


def blocked(t):
    return t.weekday() < 5 and OPEN <= (t.hour, t.minute) <= CLOSE


def main():
    t = now_ist()
    if not blocked(t):
        return 0
    if os.environ.get("SCALP_PUSH_OK") == "1":
        print(f"market hours ({t:%a %H:%M} IST) — pushing anyway: SCALP_PUSH_OK=1")
        return 0
    print(f"Push blocked: it is {t:%a %d %b %H:%M} IST, inside market hours (Mon–Fri 09:00–15:30).")
    print("Push after 15:30, or for this one push:  SCALP_PUSH_OK=1 git push")
    return 1


if __name__ == "__main__":
    sys.exit(main())
