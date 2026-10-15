#!/usr/bin/env python3
# apply_index_ltp_feeder.py — fence INDEX_LTP_FEEDER_20261015
#
# BUG (non-admin machines): the NIFTY / BANKNIFTY strip on the Dashboard froze
# at the startup seed all day while every strategy traded normally.
# MarketIndicesState LTP was written ONLY by the tick engines that subscribe the
# index tokens (SCALP_V1's ZerodhaTickEngine, SCALP_V3, SCALP_V5) plus a single
# REST seed at boot. A licence that runs none of them (Icarus / Tigris /
# Timberwolf / Velvet) had nothing refreshing the indices. Same class as
# TSG_LTP_PUBLISH (2026-08-12): the display depended on which engines ran.
#
# FIX
#   * NEW backend/app/marketdata/index_ltp_feeder.py — perpetual task: polls
#     kite.ltp() for NIFTY 50 + NIFTY BANK in ONE request every 3s, ONLY while
#     no tick engine is feeding them (no WS index tick for 5s). WS-fed machines
#     (yours) make zero extra calls; it hands back to WS automatically. Market
#     hours only (holiday-aware), data kite → trade kite fallback, 30s back-off
#     on a rate-limit reply, throttled logs, never dies.
#   * MarketIndicesState.update_ltp(index, price, source="ws") — default source
#     stamps WS freshness (every existing positional caller unchanged); the
#     feeder writes source="rest" so it never mistakes itself for a WS feed.
#     New ws_stale(indices, max_age_s). snapshot() / API shape unchanged.
#   * api_server: feeder launched right after the prev_close watchdog, own
#     _boot_guard, _supervise'd, outside the is_trade_ready() gate (self-heals
#     after the morning login like the watchdog).
#   Display-only for the machines it affects: no trading path changes.
#
#  EDIT  backend/app/marketdata/market_indices_state.py (4)
#        backend/app/api_server.py (1)
#  NEW   backend/app/marketdata/index_ltp_feeder.py
#        backend/app/marketdata/test_index_ltp_feeder.py
#  Mirrored to desktop/src-tauri/backend/ when that tree exists.
#  Gates: py_compile, imports, the new suite (incl. a real-clock freshness test).
#  Any failure → every file restored.
#
# Usage: python3 apply_index_ltp_feeder.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script (the new module and its tests are carried inside it).

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "INDEX_LTP_FEEDER_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/"}
MIS = "backend/app/marketdata/market_indices_state.py"
API = "backend/app/api_server.py"
FEED = "backend/app/marketdata/index_ltp_feeder.py"
T = "backend/app/marketdata/test_index_ltp_feeder.py"
PY = [MIS, API]
PREREQ = {MIS: ["INDEX_PREVCLOSE_ROLLOVER BEGIN (import)"],
          API: ["INDEX_PREVCLOSE_ROLLOVER END (watchdog launch)", "def _supervise(", "def _boot_guard("]}
SUITES = ["app/marketdata/test_index_ltp_feeder.py"]

EDITS = [
    (MIS, '# ── INDEX_PREVCLOSE_ROLLOVER BEGIN (import) ──\nfrom datetime import date\n# ── INDEX_PREVCLOSE_ROLLOVER END (import) ──\n',
     '# ── INDEX_PREVCLOSE_ROLLOVER BEGIN (import) ──\nfrom datetime import date\n# ── INDEX_PREVCLOSE_ROLLOVER END (import) ──\nimport time  # ── INDEX_LTP_FEEDER_20261015 ──\n', 1),
    (MIS, '    Written by:\n      - ZerodhaTickEngine (live LTP)\n',
     '    Written by:\n      - ZerodhaTickEngine (live LTP)\n      - index_ltp_feeder REST fallback, only while no tick engine feeds the\n        indices (INDEX_LTP_FEEDER_20261015)\n', 1),
    (MIS, '    # Live LTP\n    _ltp: Dict[str, float] = {}\n',
     '    # Live LTP\n    _ltp: Dict[str, float] = {}\n\n    # ── INDEX_LTP_FEEDER_20261015 ── monotonic time of the last NON-REST\n    # update per index (tick engines + boot seed). The REST fallback feeder\n    # runs only while this is stale, and never stamps it itself.\n    _ltp_ws_ts: Dict[str, float] = {}\n', 1),
    (MIS, '    @classmethod\n    def update_ltp(cls, index: str, price: float):\n        with cls._lock:\n            cls._ltp[index] = price\n',
     '    @classmethod\n    def update_ltp(cls, index: str, price: float, source: str = "ws"):\n        # ── INDEX_LTP_FEEDER_20261015 ── source="ws" (default: every tick\n        # engine + the boot seed, all positional callers unchanged) stamps WS\n        # freshness; source="rest" (fallback feeder) updates the price only.\n        with cls._lock:\n            cls._ltp[index] = price\n            if source != "rest":\n                cls._ltp_ws_ts[index] = time.monotonic()\n\n    # ── INDEX_LTP_FEEDER_20261015 ──\n    @classmethod\n    def ws_stale(cls, indices, max_age_s: float, now: Optional[float] = None) -> bool:\n        """True when ANY of `indices` has had no tick-engine update within\n        max_age_s (or never had one). `now` is time.monotonic()-based."""\n        now = time.monotonic() if now is None else now\n        with cls._lock:\n            for idx in indices:\n                ts = cls._ltp_ws_ts.get(idx)\n                if ts is None or now - ts > max_age_s:\n                    return True\n            return False\n', 1),
    (API, '    # ── INDEX_PREVCLOSE_ROLLOVER END (watchdog launch) ──\n',
     '    # ── INDEX_PREVCLOSE_ROLLOVER END (watchdog launch) ──\n\n    # ── INDEX_LTP_FEEDER_20261015 BEGIN (feeder launch) ──\n    # Index LTP was written only by the SCALP_V1/V3/V5 tick engines; a licence\n    # running none of them froze the Dashboard index strip at the boot seed.\n    # REST fallback, active ONLY while no tick engine feeds the indices (zero\n    # calls on WS-fed machines). Outside is_trade_ready() on purpose: it\n    # waits politely for the morning login, like the watchdog above.\n    with _boot_guard("index_ltp_feeder"):\n        from app.marketdata.index_ltp_feeder import index_ltp_feeder\n        _supervise(asyncio.create_task(index_ltp_feeder(zerodha_manager)), "index_ltp_feeder")\n        write_audit_log("[SYSTEM] Index LTP feeder launched (REST fallback when no WS index ticks)")\n    # ── INDEX_LTP_FEEDER_20261015 END (feeder launch) ──\n', 1),
]

NEW_FILES = {FEED: '# backend/app/marketdata/index_ltp_feeder.py\n#\n# ── INDEX_LTP_FEEDER_20261015 ── REST fallback for the dashboard index strip.\n#\n# BUG: MarketIndicesState LTP (NIFTY / BANKNIFTY header, /market_indices) was\n# written ONLY by the tick engines that subscribe index tokens — SCALP_V1\'s\n# ZerodhaTickEngine, SCALP_V3, SCALP_V5 — plus one REST seed at startup.\n# A machine whose licence runs none of those (e.g. a non-admin running\n# Icarus / Tigris / Timberwolf / Velvet) froze the strip at the boot seed all\n# day, while every strategy kept trading normally (each prices its own legs).\n# Same class as TSG_LTP_PUBLISH (2026-08-12): what a machine shows depended\n# on which tick engines happened to run there.\n#\n# FIX: one feeder that polls kite.ltp() for the two indices in a SINGLE request,\n# but ONLY while no tick engine is feeding them:\n#   * WS-fed machine (admin): ws_stale() is False → zero REST calls, ever.\n#   * No WS index feed (non-admin): one request every POLL_S during market\n#     hours. Hands back to WS automatically the moment index ticks resume.\n#   * Market closed / holiday / no kite session → no calls.\n#   * Rate-limited reply → back off BACKOFF_S (the quote endpoint is shared\n#     with strategy quoting; the feeder must always yield to it).\n#   * Errors are logged at most once per ERR_LOG_EVERY_S; the loop never dies.\n# Display-only: no strategy reads this state for a trading decision on the\n# machines it affects (SCALP_V1\'s skew gate reads it, but SCALP_V1 runs the\n# WS engine that keeps it fresh, so the feeder stays idle there).\n#\n# index_rest_updater.index_polling_loop() remains DEAD CODE (unconditional\n# 1 req/s on the quote endpoint — never launched, deliberately not revived).\n\nfrom __future__ import annotations\n\nimport asyncio\nimport time\nfrom typing import Callable, Optional\n\nfrom app.event_bus.audit_logger import write_audit_log\nfrom app.marketdata.market_indices_state import MarketIndicesState\n\nFENCE = "INDEX_LTP_FEEDER_20261015"\n\nINDEX_SYMBOLS = {\n    "NIFTY": "NSE:NIFTY 50",\n    "BANKNIFTY": "NSE:NIFTY BANK",\n}\n\nPOLL_S = 3.0            # REST cadence while WS index ticks are absent\nWS_STALE_S = 5.0        # no WS index tick for this long → REST takes over\nCLOSED_SLEEP_S = 30.0   # market closed: just re-check the clock\nNO_KITE_SLEEP_S = 10.0  # no broker session yet (pre-login)\nBACKOFF_S = 30.0        # after a rate-limit reply\nERR_LOG_EVERY_S = 300.0\n\n\ndef _default_market_open() -> bool:\n    from app.utils.market_hours import is_market_open\n    return is_market_open()\n\n\ndef _is_rate_limited(e: Exception) -> bool:\n    m = repr(e).lower()\n    return "too many requests" in m or "429" in m\n\n\nclass IndexLtpFeeder:\n    def __init__(self, zerodha_manager,\n                 market_open: Optional[Callable[[], bool]] = None,\n                 clock: Callable[[], float] = time.monotonic,\n                 log: Callable[[str], None] = write_audit_log):\n        self.mgr = zerodha_manager\n        self.market_open = market_open or _default_market_open\n        self.clock = clock\n        self.log = log\n        self.mode = "init"            # "ws" | "rest" | "init"\n        self.backoff_until = 0.0\n        self._last_err_log = -1e18\n        self.calls = 0                # kite.ltp requests made (tests/diagnostics)\n\n    def _log_err(self, msg: str, now: float):\n        if now - self._last_err_log >= ERR_LOG_EVERY_S:\n            self._last_err_log = now\n            self.log(msg)\n\n    def _kite(self):\n        try:\n            return self.mgr.get_data_kite() or self.mgr.get_trade_kite()\n        except Exception:\n            return None\n\n    def step(self) -> float:\n        """One cycle. Blocking (kite.ltp) — run via asyncio.to_thread.\n        Returns the number of seconds to sleep before the next cycle."""\n        now = self.clock()\n        try:\n            if not self.market_open():\n                return CLOSED_SLEEP_S\n        except Exception:\n            return CLOSED_SLEEP_S\n\n        if not MarketIndicesState.ws_stale(tuple(INDEX_SYMBOLS), WS_STALE_S, now=now):\n            if self.mode != "ws":\n                if self.mode == "rest":\n                    self.log("[INDEX][FEEDER] WS index ticks resumed — REST fallback idle")\n                self.mode = "ws"\n            return POLL_S\n\n        if now < self.backoff_until:\n            return min(POLL_S, self.backoff_until - now) or POLL_S\n\n        kite = self._kite()\n        if kite is None:\n            self._log_err("[INDEX][FEEDER] no kite session yet — index strip waits", now)\n            return NO_KITE_SLEEP_S\n\n        try:\n            self.calls += 1\n            data = kite.ltp(list(INDEX_SYMBOLS.values())) or {}\n        except Exception as e:\n            if _is_rate_limited(e):\n                self.backoff_until = now + BACKOFF_S\n                self._log_err(f"[INDEX][FEEDER] rate-limited — backing off {BACKOFF_S:.0f}s", now)\n            else:\n                self._log_err(f"[INDEX][FEEDER] ltp poll failed: {e!r}", now)\n            return POLL_S\n\n        got = 0\n        for name, sym in INDEX_SYMBOLS.items():\n            row = data.get(sym) if isinstance(data, dict) else None\n            try:\n                v = float((row or {}).get("last_price") or 0)\n            except (TypeError, ValueError):\n                v = 0.0\n            if v > 0:\n                MarketIndicesState.update_ltp(name, v, source="rest")\n                got += 1\n\n        if got and self.mode != "rest":\n            self.mode = "rest"\n            self.log(f"[INDEX][FEEDER] no WS index ticks — REST fallback active "\n                     f"(every {POLL_S:.0f}s, single request)")\n        return POLL_S\n\n\nasync def index_ltp_feeder(zerodha_manager, _feeder: Optional[IndexLtpFeeder] = None,\n                           _sleep=asyncio.sleep, _max_cycles: Optional[int] = None):\n    """Perpetual task (launched under _supervise in api_server). Never returns\n    in production; `_max_cycles` exists for tests only."""\n    f = _feeder or IndexLtpFeeder(zerodha_manager)\n    write_audit_log(f"[INDEX][FEEDER] started ({FENCE})")\n    n = 0\n    while _max_cycles is None or n < _max_cycles:\n        n += 1\n        try:\n            s = await asyncio.to_thread(f.step)\n        except Exception as e:\n            # The feeder must never die: a dead feeder silently re-creates\n            # the frozen-index-strip bug on every non-WS machine.\n            write_audit_log(f"[INDEX][FEEDER] cycle error: {e!r}")\n            s = POLL_S\n        await _sleep(s)\n',
             T: '# backend/app/marketdata/test_index_ltp_feeder.py — fence INDEX_LTP_FEEDER_20261015\n#\n#   1. MarketIndicesState: source-aware update_ltp, ws_stale (fake + REAL clock),\n#      positional callers unchanged, snapshot shape unchanged\n#   2. Feeder: WS-fed machine makes ZERO calls; no-WS machine polls in one\n#      request; continuous REST (no 5s gaps); hand-back to WS; market closed;\n#      no kite; trade-kite fallback; rate-limit back-off and recovery; generic\n#      errors; junk payloads; log throttling\n#   3. async loop survives a step that raises\n#   4. api_server launch wiring (static)\n#\n# Run: cd backend && python3 app/marketdata/test_index_ltp_feeder.py\n\nfrom __future__ import annotations\n\nimport asyncio\nimport os\nimport sys\nimport tempfile\nimport time\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve()\nBACKEND = str(HERE.parents[2])\nif BACKEND not in sys.path:\n    sys.path.insert(0, BACKEND)\nos.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())   # never touch the real audit log\n\nFENCE = "INDEX_LTP_FEEDER_20261015"\nFAILS: list = []\nOK = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nfrom app.marketdata.market_indices_state import MarketIndicesState as M  # noqa: E402\nimport app.marketdata.index_ltp_feeder as F  # noqa: E402\n\n\ndef reset():\n    with M._lock:\n        M._ltp.clear()\n        M._ltp_ws_ts.clear()\n\n\nclass Kite:\n    def __init__(self, payload=None, exc=None):\n        self.payload = payload if payload is not None else {\n            "NSE:NIFTY 50": {"last_price": 22671.3}, "NSE:NIFTY BANK": {"last_price": 55445.75}}\n        self.exc = exc\n        self.calls = []\n\n    def ltp(self, syms):\n        self.calls.append(list(syms))\n        if self.exc:\n            raise self.exc\n        return self.payload\n\n\nclass Mgr:\n    def __init__(self, data=None, trade=None):\n        self.data, self.trade = data, trade\n\n    def get_data_kite(self):\n        return self.data\n\n    def get_trade_kite(self):\n        return self.trade\n\n\nclass Clock:\n    def __init__(self):\n        self.t = time.monotonic()\n\n    def __call__(self):\n        return self.t\n\n\ndef feeder(mgr, open_=True, clock=None):\n    logs = []\n    f = F.IndexLtpFeeder(mgr, market_open=lambda: open_, clock=clock or Clock(), log=logs.append)\n    return f, logs\n\n\n# ═════════════════════════════════════════════════════════════════════\ndef t_state():\n    print("── 1. MarketIndicesState ──")\n    reset()\n    M.update_ltp("NIFTY", 22600.0)                       # positional, as every tick engine calls it\n    check("positional update_ltp still works (tick engines unchanged)", M._ltp["NIFTY"] == 22600.0)\n    check("default source stamps WS freshness", "NIFTY" in M._ltp_ws_ts)\n    M.update_ltp("BANKNIFTY", 55000.0, source="rest")\n    check("source=\'rest\' updates the price", M._ltp["BANKNIFTY"] == 55000.0)\n    check("source=\'rest\' does NOT stamp WS freshness", "BANKNIFTY" not in M._ltp_ws_ts)\n    check("ws_stale True when any index never WS-fed", M.ws_stale(("NIFTY", "BANKNIFTY"), 5.0))\n    M.update_ltp("BANKNIFTY", 55001.0)\n    check("ws_stale False when both freshly WS-fed", not M.ws_stale(("NIFTY", "BANKNIFTY"), 5.0))\n    now = time.monotonic()\n    check("ws_stale True once older than max_age (injected now)",\n          M.ws_stale(("NIFTY", "BANKNIFTY"), 5.0, now=now + 6.0))\n    # REAL clock: the branch must both close and open on time.monotonic()\n    M.update_ltp("NIFTY", 1.0)\n    M.update_ltp("BANKNIFTY", 2.0)\n    fresh = M.ws_stale(("NIFTY", "BANKNIFTY"), 0.25)\n    time.sleep(0.35)\n    stale = M.ws_stale(("NIFTY", "BANKNIFTY"), 0.25)\n    check("REAL clock: fresh → not stale, then stale after max_age elapses", (not fresh) and stale, (fresh, stale))\n    reset()\n    with M._lock:\n        M._prev_close["NIFTY"] = 22622.7\n    M.update_ltp("NIFTY", 22671.3, source="rest")\n    s = M.snapshot()\n    check("snapshot shape unchanged (ltp/prev_close/change/change_pct)",\n          set(s["NIFTY"]) == {"ltp", "prev_close", "change", "change_pct"} and s["NIFTY"]["change"] == 48.6, s["NIFTY"])\n    with M._lock:\n        M._prev_close.pop("NIFTY", None)\n\n\n# ═════════════════════════════════════════════════════════════════════\ndef t_feeder():\n    print("── 2. IndexLtpFeeder ──")\n    # a) admin-style machine: WS keeps both fresh → zero REST calls\n    reset()\n    k = Kite()\n    clk = Clock()\n    f, logs = feeder(Mgr(k), clock=clk)\n    for _ in range(20):\n        M.update_ltp("NIFTY", 22600.0)\n        M.update_ltp("BANKNIFTY", 55000.0)\n        clk.t = time.monotonic()\n        f.step()\n    check("WS-fed machine: 20 cycles → ZERO kite.ltp calls", len(k.calls) == 0, len(k.calls))\n\n    # b) non-admin machine: no WS → one request for both indices, prices land\n    reset()\n    k = Kite()\n    clk = Clock()\n    f, logs = feeder(Mgr(k), clock=clk)\n    s = f.step()\n    check("no WS feed: exactly one request", len(k.calls) == 1, k.calls)\n    check("…containing both indices in a single call",\n          sorted(k.calls[0]) == sorted(["NSE:NIFTY 50", "NSE:NIFTY BANK"]), k.calls)\n    check("…NIFTY / BANKNIFTY updated", M._ltp.get("NIFTY") == 22671.3 and M._ltp.get("BANKNIFTY") == 55445.75)\n    check("…next poll in POLL_S (3s)", s == F.POLL_S, s)\n    check("…one \'REST fallback active\' log line", sum("REST fallback active" in l for l in logs) == 1, logs)\n    # c) REST must not mark itself fresh — continuous polling, no 5s gaps\n    for _ in range(4):\n        clk.t += F.POLL_S\n        f.step()\n    check("REST updates never count as WS-fresh → polls every cycle (5 calls)", len(k.calls) == 5, len(k.calls))\n    check("…\'active\' logged once, not per poll", sum("REST fallback active" in l for l in logs) == 1)\n    # d) hand-back: a tick engine starts publishing → feeder goes idle\n    M.update_ltp("NIFTY", 22700.0)\n    M.update_ltp("BANKNIFTY", 55500.0)\n    clk.t = time.monotonic()\n    f.step()\n    f.step()\n    check("WS ticks resume → feeder idle (no new calls)", len(k.calls) == 5, len(k.calls))\n    check("…hand-back logged", any("WS index ticks resumed" in l for l in logs), logs)\n    # only ONE index WS-fed (e.g. TMA spot) → still stale → feeder keeps BANKNIFTY alive\n    reset()\n    M.update_ltp("NIFTY", 22700.0)\n    k = Kite()\n    f, _ = feeder(Mgr(k), clock=Clock())\n    f.step()\n    check("only NIFTY WS-fed → feeder still polls (BANKNIFTY would freeze)", len(k.calls) == 1)\n\n    # e) market closed / holiday → nothing\n    reset()\n    k = Kite()\n    f, _ = feeder(Mgr(k), open_=False)\n    s = f.step()\n    check("market closed → zero calls, sleeps CLOSED_SLEEP_S", len(k.calls) == 0 and s == F.CLOSED_SLEEP_S, s)\n    f = F.IndexLtpFeeder(Mgr(k), market_open=lambda: 1 / 0, clock=Clock(), log=lambda m: None)\n    check("market-hours check raising → treated as closed, no crash", f.step() == F.CLOSED_SLEEP_S and not k.calls)\n\n    # f) no kite session yet (pre-login) / manager raising\n    reset()\n    f, logs = feeder(Mgr(None, None))\n    check("no kite → no crash, NO_KITE_SLEEP_S", f.step() == F.NO_KITE_SLEEP_S)\n    class Boom:\n        def get_data_kite(self):\n            raise RuntimeError("x")\n        get_trade_kite = get_data_kite\n    f, _ = feeder(Boom())\n    check("manager raising → treated as no kite", f.step() == F.NO_KITE_SLEEP_S)\n\n    # g) data kite absent → trade kite used\n    reset()\n    kt = Kite()\n    f, _ = feeder(Mgr(None, kt))\n    f.step()\n    check("data kite None → trade kite used", len(kt.calls) == 1 and M._ltp.get("NIFTY") == 22671.3)\n\n    # h) rate limit → back off 30s, then recover\n    reset()\n    k = Kite(exc=Exception("Too many requests"))\n    clk = Clock()\n    f, logs = feeder(Mgr(k), clock=clk)\n    f.step()\n    for _ in range(9):\n        clk.t += 3.0\n        f.step()\n    check("rate-limited → no further calls during the 30s back-off", len(k.calls) == 1, len(k.calls))\n    k.exc = None\n    clk.t += 3.5\n    f.step()\n    check("…polling resumes after back-off and prices land",\n          len(k.calls) == 2 and M._ltp.get("NIFTY") == 22671.3, len(k.calls))\n    check("…back-off logged", any("rate-limited" in l for l in logs))\n\n    # i) generic error → retried next cycle; logs throttled\n    reset()\n    k = Kite(exc=ConnectionError("net down"))\n    clk = Clock()\n    f, logs = feeder(Mgr(k), clock=clk)\n    for _ in range(50):\n        f.step()\n        clk.t += 3.0\n    check("generic error: retried every cycle (50 calls, no back-off)", len(k.calls) == 50, len(k.calls))\n    check("…error logged at most once per 5 min (150s of failures → 1 line)",\n          sum("ltp poll failed" in l for l in logs) == 1, logs)\n\n    # j) junk payloads never write garbage\n    for payload, label in [({}, "empty dict"), ({"NSE:NIFTY 50": {"last_price": 0}}, "zero price"),\n                           ({"NSE:NIFTY 50": {"last_price": None}}, "None price"),\n                           ({"NSE:NIFTY 50": {"last_price": "abc"}}, "non-numeric"),\n                           ({"NSE:NIFTY 50": None}, "None row"), ([], "list instead of dict")]:\n        reset()\n        f, _ = feeder(Mgr(Kite(payload=payload)))\n        try:\n            f.step()\n            ok = "NIFTY" not in M._ltp\n        except Exception as e:\n            ok = False\n        check(f"junk payload ({label}) → no update, no crash", ok)\n    reset()\n    f, _ = feeder(Mgr(Kite(payload={"NSE:NIFTY 50": {"last_price": 22650.0}})))\n    f.step()\n    check("partial payload: NIFTY updated, BANKNIFTY untouched",\n          M._ltp.get("NIFTY") == 22650.0 and "BANKNIFTY" not in M._ltp)\n\n\n# ═════════════════════════════════════════════════════════════════════\ndef t_loop():\n    print("── 3. async loop ──")\n    class Flaky:\n        n = 0\n        def step(self):\n            self.n += 1\n            if self.n % 2:\n                raise RuntimeError("boom")\n            return 0.0\n    fl = Flaky()\n    slept = []\n\n    async def fake_sleep(s):\n        slept.append(s)\n\n    asyncio.run(F.index_ltp_feeder(None, _feeder=fl, _sleep=fake_sleep, _max_cycles=6))\n    check("loop survives raising steps (6 cycles completed)", fl.n == 6 and len(slept) == 6, (fl.n, slept))\n    check("…raising step sleeps POLL_S, healthy step uses its own value",\n          slept[0] == F.POLL_S and slept[1] == 0.0, slept)\n\n\n# ═════════════════════════════════════════════════════════════════════\ndef t_wiring():\n    print("── 4. api_server wiring (static) ──")\n    src = (Path(BACKEND) / "app" / "api_server.py").read_text(encoding="utf-8")\n    i_wd = src.find("INDEX_PREVCLOSE_ROLLOVER END (watchdog launch)")\n    i_fd = src.find(f"{FENCE} BEGIN (feeder launch)")\n    check("feeder launch present, right after the prev_close watchdog", 0 < i_wd < i_fd, (i_wd, i_fd))\n    blk = src[i_fd:src.find(f"{FENCE} END (feeder launch)")]\n    check("…inside its own _boot_guard", \'_boot_guard("index_ltp_feeder")\' in blk)\n    check("…supervised as a perpetual task", "_supervise(asyncio.create_task(index_ltp_feeder(zerodha_manager))" in blk)\n    def indent(marker):\n        line = next(l for l in src.splitlines() if marker in l)\n        return len(line) - len(line.lstrip())\n    check("…NOT behind the is_trade_ready() gate (same indent as the watchdog\'s guard)",\n          indent(\'_boot_guard("index_ltp_feeder")\') == indent(\'_boot_guard("index_prev_close_watchdog")\'),\n          (indent(\'_boot_guard("index_ltp_feeder")\'), indent(\'_boot_guard("index_prev_close_watchdog")\')))\n    check("dead index_polling_loop still not launched", "index_polling_loop(" not in src)\n\n\nif __name__ == "__main__":\n    t_state()\n    t_feeder()\n    t_loop()\n    t_wiring()\n    reset()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} INDEX LTP FEEDER CHECKS PASSED")\n'}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        staged[rel] = txt
    for rel, fences in PREREQ.items():
        for f in fences:
            if f not in staged[rel]:
                die(f"prerequisite {f!r} not found in {rel}")
    if "Optional" not in staged[MIS].split("class MarketIndicesState")[0]:
        die(f"{MIS}: 'Optional' not imported from typing — layout changed, aborting")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — mixed state; remove it (or git checkout) and re-run")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    for rel in targets:
        if FENCE not in staged[rel]:
            die(f"internal: {rel} edited without the fence marker")
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in [(r, staged[r]) for r in PY] + list(NEW_FILES.items()):
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print(f"  ✓ py_compile ({len(PY)} edited files + {len(NEW_FILES)} new)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    for rel, txt in NEW_FILES.items():
        (repo / rel).write_text(txt, encoding="utf-8")
        print(f"  ✓ created {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   SCALP_APP_HOME=tempfile.mkdtemp(prefix="scalp_test_home_"),
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c",
                            "import app.marketdata.market_indices_state, app.marketdata.index_ltp_feeder; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("a touched module no longer imports")
        print("  ✓ market_indices_state / index_ltp_feeder import")
        for suite in SUITES:
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL  " in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    for rel in targets + list(NEW_FILES):
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied. Rebuild (deploy-scalp.command), then on a NON-admin machine:\n"
          f"  • log shows '[INDEX][FEEDER] no WS index ticks — REST fallback active' after 09:15\n"
          f"  • Dashboard NIFTY / BANKNIFTY tick every ~3s.\n"
          f"  On your machine the feeder stays idle (no 'REST fallback active' line).\n"
          f"  Display-only change; safe to ship today.")


if __name__ == "__main__":
    main()
