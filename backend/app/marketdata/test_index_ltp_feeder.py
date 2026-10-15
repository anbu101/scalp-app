# backend/app/marketdata/test_index_ltp_feeder.py — fence INDEX_LTP_FEEDER_20261015
#
#   1. MarketIndicesState: source-aware update_ltp, ws_stale (fake + REAL clock),
#      positional callers unchanged, snapshot shape unchanged
#   2. Feeder: WS-fed machine makes ZERO calls; no-WS machine polls in one
#      request; continuous REST (no 5s gaps); hand-back to WS; market closed;
#      no kite; trade-kite fallback; rate-limit back-off and recovery; generic
#      errors; junk payloads; log throttling
#   3. async loop survives a step that raises
#   4. api_server launch wiring (static)
#
# Run: cd backend && python3 app/marketdata/test_index_ltp_feeder.py

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[2])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())   # never touch the real audit log

FENCE = "INDEX_LTP_FEEDER_20261015"
FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


from app.marketdata.market_indices_state import MarketIndicesState as M  # noqa: E402
import app.marketdata.index_ltp_feeder as F  # noqa: E402


def reset():
    with M._lock:
        M._ltp.clear()
        M._ltp_ws_ts.clear()


class Kite:
    def __init__(self, payload=None, exc=None):
        self.payload = payload if payload is not None else {
            "NSE:NIFTY 50": {"last_price": 22671.3}, "NSE:NIFTY BANK": {"last_price": 55445.75}}
        self.exc = exc
        self.calls = []

    def ltp(self, syms):
        self.calls.append(list(syms))
        if self.exc:
            raise self.exc
        return self.payload


class Mgr:
    def __init__(self, data=None, trade=None):
        self.data, self.trade = data, trade

    def get_data_kite(self):
        return self.data

    def get_trade_kite(self):
        return self.trade


class Clock:
    def __init__(self):
        self.t = time.monotonic()

    def __call__(self):
        return self.t


def feeder(mgr, open_=True, clock=None):
    logs = []
    f = F.IndexLtpFeeder(mgr, market_open=lambda: open_, clock=clock or Clock(), log=logs.append)
    return f, logs


# ═════════════════════════════════════════════════════════════════════
def t_state():
    print("── 1. MarketIndicesState ──")
    reset()
    M.update_ltp("NIFTY", 22600.0)                       # positional, as every tick engine calls it
    check("positional update_ltp still works (tick engines unchanged)", M._ltp["NIFTY"] == 22600.0)
    check("default source stamps WS freshness", "NIFTY" in M._ltp_ws_ts)
    M.update_ltp("BANKNIFTY", 55000.0, source="rest")
    check("source='rest' updates the price", M._ltp["BANKNIFTY"] == 55000.0)
    check("source='rest' does NOT stamp WS freshness", "BANKNIFTY" not in M._ltp_ws_ts)
    check("ws_stale True when any index never WS-fed", M.ws_stale(("NIFTY", "BANKNIFTY"), 5.0))
    M.update_ltp("BANKNIFTY", 55001.0)
    check("ws_stale False when both freshly WS-fed", not M.ws_stale(("NIFTY", "BANKNIFTY"), 5.0))
    now = time.monotonic()
    check("ws_stale True once older than max_age (injected now)",
          M.ws_stale(("NIFTY", "BANKNIFTY"), 5.0, now=now + 6.0))
    # REAL clock: the branch must both close and open on time.monotonic()
    M.update_ltp("NIFTY", 1.0)
    M.update_ltp("BANKNIFTY", 2.0)
    fresh = M.ws_stale(("NIFTY", "BANKNIFTY"), 0.25)
    time.sleep(0.35)
    stale = M.ws_stale(("NIFTY", "BANKNIFTY"), 0.25)
    check("REAL clock: fresh → not stale, then stale after max_age elapses", (not fresh) and stale, (fresh, stale))
    reset()
    with M._lock:
        M._prev_close["NIFTY"] = 22622.7
    M.update_ltp("NIFTY", 22671.3, source="rest")
    s = M.snapshot()
    check("snapshot shape unchanged (ltp/prev_close/change/change_pct)",
          set(s["NIFTY"]) == {"ltp", "prev_close", "change", "change_pct"} and s["NIFTY"]["change"] == 48.6, s["NIFTY"])
    with M._lock:
        M._prev_close.pop("NIFTY", None)


# ═════════════════════════════════════════════════════════════════════
def t_feeder():
    print("── 2. IndexLtpFeeder ──")
    # a) admin-style machine: WS keeps both fresh → zero REST calls
    reset()
    k = Kite()
    clk = Clock()
    f, logs = feeder(Mgr(k), clock=clk)
    for _ in range(20):
        M.update_ltp("NIFTY", 22600.0)
        M.update_ltp("BANKNIFTY", 55000.0)
        clk.t = time.monotonic()
        f.step()
    check("WS-fed machine: 20 cycles → ZERO kite.ltp calls", len(k.calls) == 0, len(k.calls))

    # b) non-admin machine: no WS → one request for both indices, prices land
    reset()
    k = Kite()
    clk = Clock()
    f, logs = feeder(Mgr(k), clock=clk)
    s = f.step()
    check("no WS feed: exactly one request", len(k.calls) == 1, k.calls)
    check("…containing both indices in a single call",
          sorted(k.calls[0]) == sorted(["NSE:NIFTY 50", "NSE:NIFTY BANK"]), k.calls)
    check("…NIFTY / BANKNIFTY updated", M._ltp.get("NIFTY") == 22671.3 and M._ltp.get("BANKNIFTY") == 55445.75)
    check("…next poll in POLL_S (3s)", s == F.POLL_S, s)
    check("…one 'REST fallback active' log line", sum("REST fallback active" in l for l in logs) == 1, logs)
    # c) REST must not mark itself fresh — continuous polling, no 5s gaps
    for _ in range(4):
        clk.t += F.POLL_S
        f.step()
    check("REST updates never count as WS-fresh → polls every cycle (5 calls)", len(k.calls) == 5, len(k.calls))
    check("…'active' logged once, not per poll", sum("REST fallback active" in l for l in logs) == 1)
    # d) hand-back: a tick engine starts publishing → feeder goes idle
    M.update_ltp("NIFTY", 22700.0)
    M.update_ltp("BANKNIFTY", 55500.0)
    clk.t = time.monotonic()
    f.step()
    f.step()
    check("WS ticks resume → feeder idle (no new calls)", len(k.calls) == 5, len(k.calls))
    check("…hand-back logged", any("WS index ticks resumed" in l for l in logs), logs)
    # only ONE index WS-fed (e.g. TMA spot) → still stale → feeder keeps BANKNIFTY alive
    reset()
    M.update_ltp("NIFTY", 22700.0)
    k = Kite()
    f, _ = feeder(Mgr(k), clock=Clock())
    f.step()
    check("only NIFTY WS-fed → feeder still polls (BANKNIFTY would freeze)", len(k.calls) == 1)

    # e) market closed / holiday → nothing
    reset()
    k = Kite()
    f, _ = feeder(Mgr(k), open_=False)
    s = f.step()
    check("market closed → zero calls, sleeps CLOSED_SLEEP_S", len(k.calls) == 0 and s == F.CLOSED_SLEEP_S, s)
    f = F.IndexLtpFeeder(Mgr(k), market_open=lambda: 1 / 0, clock=Clock(), log=lambda m: None)
    check("market-hours check raising → treated as closed, no crash", f.step() == F.CLOSED_SLEEP_S and not k.calls)

    # f) no kite session yet (pre-login) / manager raising
    reset()
    f, logs = feeder(Mgr(None, None))
    check("no kite → no crash, NO_KITE_SLEEP_S", f.step() == F.NO_KITE_SLEEP_S)
    class Boom:
        def get_data_kite(self):
            raise RuntimeError("x")
        get_trade_kite = get_data_kite
    f, _ = feeder(Boom())
    check("manager raising → treated as no kite", f.step() == F.NO_KITE_SLEEP_S)

    # g) data kite absent → trade kite used
    reset()
    kt = Kite()
    f, _ = feeder(Mgr(None, kt))
    f.step()
    check("data kite None → trade kite used", len(kt.calls) == 1 and M._ltp.get("NIFTY") == 22671.3)

    # h) rate limit → back off 30s, then recover
    reset()
    k = Kite(exc=Exception("Too many requests"))
    clk = Clock()
    f, logs = feeder(Mgr(k), clock=clk)
    f.step()
    for _ in range(9):
        clk.t += 3.0
        f.step()
    check("rate-limited → no further calls during the 30s back-off", len(k.calls) == 1, len(k.calls))
    k.exc = None
    clk.t += 3.5
    f.step()
    check("…polling resumes after back-off and prices land",
          len(k.calls) == 2 and M._ltp.get("NIFTY") == 22671.3, len(k.calls))
    check("…back-off logged", any("rate-limited" in l for l in logs))

    # i) generic error → retried next cycle; logs throttled
    reset()
    k = Kite(exc=ConnectionError("net down"))
    clk = Clock()
    f, logs = feeder(Mgr(k), clock=clk)
    for _ in range(50):
        f.step()
        clk.t += 3.0
    check("generic error: retried every cycle (50 calls, no back-off)", len(k.calls) == 50, len(k.calls))
    check("…error logged at most once per 5 min (150s of failures → 1 line)",
          sum("ltp poll failed" in l for l in logs) == 1, logs)

    # j) junk payloads never write garbage
    for payload, label in [({}, "empty dict"), ({"NSE:NIFTY 50": {"last_price": 0}}, "zero price"),
                           ({"NSE:NIFTY 50": {"last_price": None}}, "None price"),
                           ({"NSE:NIFTY 50": {"last_price": "abc"}}, "non-numeric"),
                           ({"NSE:NIFTY 50": None}, "None row"), ([], "list instead of dict")]:
        reset()
        f, _ = feeder(Mgr(Kite(payload=payload)))
        try:
            f.step()
            ok = "NIFTY" not in M._ltp
        except Exception as e:
            ok = False
        check(f"junk payload ({label}) → no update, no crash", ok)
    reset()
    f, _ = feeder(Mgr(Kite(payload={"NSE:NIFTY 50": {"last_price": 22650.0}})))
    f.step()
    check("partial payload: NIFTY updated, BANKNIFTY untouched",
          M._ltp.get("NIFTY") == 22650.0 and "BANKNIFTY" not in M._ltp)


# ═════════════════════════════════════════════════════════════════════
def t_loop():
    print("── 3. async loop ──")
    class Flaky:
        n = 0
        def step(self):
            self.n += 1
            if self.n % 2:
                raise RuntimeError("boom")
            return 0.0
    fl = Flaky()
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    asyncio.run(F.index_ltp_feeder(None, _feeder=fl, _sleep=fake_sleep, _max_cycles=6))
    check("loop survives raising steps (6 cycles completed)", fl.n == 6 and len(slept) == 6, (fl.n, slept))
    check("…raising step sleeps POLL_S, healthy step uses its own value",
          slept[0] == F.POLL_S and slept[1] == 0.0, slept)


# ═════════════════════════════════════════════════════════════════════
def t_wiring():
    print("── 4. api_server wiring (static) ──")
    src = (Path(BACKEND) / "app" / "api_server.py").read_text(encoding="utf-8")
    i_wd = src.find("INDEX_PREVCLOSE_ROLLOVER END (watchdog launch)")
    i_fd = src.find(f"{FENCE} BEGIN (feeder launch)")
    check("feeder launch present, right after the prev_close watchdog", 0 < i_wd < i_fd, (i_wd, i_fd))
    blk = src[i_fd:src.find(f"{FENCE} END (feeder launch)")]
    check("…inside its own _boot_guard", '_boot_guard("index_ltp_feeder")' in blk)
    check("…supervised as a perpetual task", "_supervise(asyncio.create_task(index_ltp_feeder(zerodha_manager))" in blk)
    def indent(marker):
        line = next(l for l in src.splitlines() if marker in l)
        return len(line) - len(line.lstrip())
    check("…NOT behind the is_trade_ready() gate (same indent as the watchdog's guard)",
          indent('_boot_guard("index_ltp_feeder")') == indent('_boot_guard("index_prev_close_watchdog")'),
          (indent('_boot_guard("index_ltp_feeder")'), indent('_boot_guard("index_prev_close_watchdog")')))
    check("dead index_polling_loop still not launched", "index_polling_loop(" not in src)


if __name__ == "__main__":
    t_state()
    t_feeder()
    t_loop()
    t_wiring()
    reset()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} INDEX LTP FEEDER CHECKS PASSED")
