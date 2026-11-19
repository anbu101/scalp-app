# backend/app/marketdata/test_ws_watchdog_fast.py
# ── WS_WATCHDOG_FAST_20261119 ── a connected-but-silent Kite socket is
# reconnected after 30 s (NIFTY-spot sockets) / 45 s (option sockets) instead
# of 120 s, checked every 5 s, never before 09:17:00 IST, 90 s between tries.
#   cd backend && PYTHONPATH=$PWD python3 app/marketdata/test_ws_watchdog_fast.py
import os, re, sys, tempfile
from datetime import datetime, timedelta, timezone
os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_wd_fast_")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
OK, FAILS = [0], []
APP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def check(n, c, d=""):
    if c:
        OK[0] += 1; print(f"  PASS  {n}")
    else:
        FAILS.append(n); print(f"  FAIL  {n}  [{str(d)[:300]}]")


IST = timezone(timedelta(hours=5, minutes=30))


def at(h, m, s=0):                     # Wed 2026-11-18, a trading day
    return datetime(2026, 11, 18, h, m, s, tzinfo=IST).timestamp()


from app.marketdata import ws_watchdog as WD                  # noqa: E402
from app.engine.tma.tma_tick_engine import TMATickEngine      # noqa: E402
from app.engine.tma2.tma2_tick_engine import TMA2TickEngine   # noqa: E402
import app.marketdata.shared_tick_feed as SF                  # noqa: E402


class FakeWS:
    def __init__(self):
        self.closes = 0

    def sendClose(self, code, reason):
        self.closes += 1


def engine(cls, connect_ts, last_tick_ts):
    e = cls.__new__(cls)
    e._kws = type("K", (), {})()
    e._kws.ws = FakeWS()
    e._stop, e._ws_connected = False, True
    e._tag, e._label = "T_TICK", "T"
    e.sent = []
    e._notify_cb = lambda msg, sev: e.sent.append((sev, msg))
    e._last_spot_tick_ts, e._last_connect_ts = last_tick_ts, connect_ts
    e._last_wd_action_ts, e._wd_strikes, e._heal_pending, e.heal_count = 0.0, 0, False, 0
    return e


print("── TC-WSWD-0001 numbers")
check("shared module: spot 30 s, option 45 s, poll 5 s, cooldown 90 s, backoff 1800 s",
      (WD.SPOT_SILENT_S, WD.OPTION_SILENT_S, WD.POLL_S, WD.COOLDOWN_S, WD.BACKOFF_S) == (30, 45, 5, 90, 1800))
for cls in (TMATickEngine, TMA2TickEngine):
    check(f"{cls.__name__} literal copies equal the shared module",
          (cls.WD_SILENT_S, cls.WD_POLL_S, cls.WD_COOLDOWN_S, cls.WD_BACKOFF_S)
          == (WD.SPOT_SILENT_S, WD.POLL_S, WD.COOLDOWN_S, WD.BACKOFF_S))

for cls in (TMATickEngine, TMA2TickEngine):
    n = cls.__name__
    print(f"── {n}")
    t0 = at(11, 49, 0)
    e = engine(cls, at(9, 13), t0)
    check(f"TC-WSWD-0002 {n}: 29 s without a NIFTY tick → no action", e._wd_check(t0 + 29) == "ok")
    r = e._wd_check(t0 + 30)
    check(f"TC-WSWD-0002 {n}: 30 s without a NIFTY tick → reconnect", r == "dropped" and e._kws.ws.closes == 1, r)
    check(f"TC-WSWD-0002 {n}: one Telegram warning on the first reconnect",
          [s for s, _ in e.sent] == ["warning"] and "no NIFTY tick for 30s" in e.sent[0][1], e.sent)
    check(f"TC-WSWD-0003 {n}: still silent 89 s later → waits (cooldown), no second reconnect",
          e._wd_check(t0 + 30 + 89) == "cooldown" and e._kws.ws.closes == 1)
    r = e._wd_check(t0 + 30 + 90)
    check(f"TC-WSWD-0003 {n}: still silent 90 s later → second reconnect, no extra Telegram",
          r == "dropped" and e._kws.ws.closes == 2 and len(e.sent) == 1, (r, e.sent))
    e._last_spot_tick_ts = t0 + 130
    e._wd_check(t0 + 135)
    check(f"TC-WSWD-0004 {n}: a tick after the reconnect → healed, strikes reset, info alert",
          e.heal_count == 1 and e._wd_strikes == 0 and e.sent[-1][0] == "info", e.sent)

    e = engine(cls, at(9, 13), 0.0)
    check(f"TC-WSWD-0005 {n}: no tick since the 09:13 connect → nothing at 09:16:59",
          e._wd_check(at(9, 16, 59)) == "ok")
    check(f"TC-WSWD-0005 {n}: … first reconnect at 09:17:00, as before",
          e._wd_check(at(9, 17, 0)) == "dropped")
    e = engine(cls, at(9, 13), at(15, 14, 0))
    check(f"TC-WSWD-0006 {n}: after 15:15 (closing auction) a silent spot is normal → no action",
          e._wd_check(at(15, 16, 0)) == "off_session")

print("── shared feed loop (one pass, fake clock)")


class Stop(BaseException):
    pass


def shared_pass(now, last_tick, last_connect, market=True):
    f = SF.SharedTickFeed.__new__(SF.SharedTickFeed)
    f.kws = type("K", (), {})()
    f.kws.ws = FakeWS()
    f._connected, f._last_tick_ts, f._last_connect_ts = True, last_tick, last_connect
    f._last_wd_action_ts, f._wd_strikes = 0.0, 0
    calls = {"sleep": []}

    class Clock:
        @staticmethod
        def time():
            return now

        @staticmethod
        def sleep(s):
            calls["sleep"].append(s)
            if len(calls["sleep"]) > 1:
                raise Stop()

    real_time, real_open = SF.time, SF._market_open
    SF.time, SF._market_open = Clock, (lambda: market)
    try:
        f._tick_watchdog()
    except Stop:
        pass
    finally:
        SF.time, SF._market_open = real_time, real_open
    return f.kws.ws.closes, calls["sleep"][0]


t0 = at(11, 49)
closes, slept = shared_pass(t0 + 44, t0, at(9, 0))
check("TC-WSWD-0007 shared feed: checks every 5 s", slept == 5, slept)
check("TC-WSWD-0007 shared feed: 44 s with zero ticks → no action", closes == 0)
closes, _ = shared_pass(t0 + 45, t0, at(9, 0))
check("TC-WSWD-0007 shared feed: 45 s with zero ticks → reconnect", closes == 1)
closes, _ = shared_pass(at(9, 16, 59), at(9, 5), at(8, 30))
check("TC-WSWD-0008 shared feed: silent since pre-open → nothing before 09:17:00", closes == 0)
closes, _ = shared_pass(at(9, 17, 0), at(9, 5), at(8, 30))
check("TC-WSWD-0008 shared feed: … reconnect at 09:17:00", closes == 1)
check("TC-WSWD-0008 open_floor_ts: silence counted from 09:17:00 minus the limit",
      WD.open_floor_ts(at(12, 0), 45) == at(9, 17) - 45 and WD.open_floor_ts(at(9, 0), 30) == at(9, 17) - 30)

print("── TC-WSWD-0009 SCALP_V1 / V3 / V5 loops read the shared numbers")
for rel in ("marketdata/zerodha_tick_engine.py", "engine/scalp_v3/scalp_v3_tick_engine.py",
            "engine/scalpv5/scalpv5_tick_engine.py", "marketdata/shared_tick_feed.py"):
    src = open(os.path.join(APP, rel), encoding="utf-8").read()
    body = src[src.index("def _tick_watchdog"):]
    body = body[:body.index("\n    def ", 10)] if "\n    def " in body[10:] else body
    check(f"{rel}: option limit, 5 s poll, 09:17 floor; no 120 s literal left",
          "_WD.OPTION_SILENT_S" in body and "_WD.POLL_S" in body and "_WD.open_floor_ts(" in body
          and not re.search(r"\b120\b", re.sub(r"#[^\n]*", "", body.split('"""')[-1])))

print(f"\n{OK[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL WS WATCHDOG FAST CHECKS PASSED")
