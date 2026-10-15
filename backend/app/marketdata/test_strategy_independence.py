# backend/app/marketdata/test_strategy_independence.py — fence STRATEGY_INDEPENDENCE_20261015
#
#   A. SharedTickFeed — BB_V1 / BB_V2 / HA_V1 without SCALP_V1
#      need matrix · registry (engines[0]) · the attributes BB/HA use ·
#      fan-out of every tick (incl. extra tokens) · index → MarketIndicesState
#      · CE/PE → LTPStore · one failing consumer never starves the others ·
#      on_connect subscribes initial+index+extra and notifies reconnect ·
#      HA's own _symbol_for_token resolves through the feed · launcher paths
#   B. NIFTY50 alias in MarketDataContext (static)
#   C. TMA / TMA2 carried legs outside today's band: subscribed, candles +
#      LTPStore, NOT selection candidates · expiry_hint · loop wiring ·
#      accurate empty-universe alerts (TMA, TMA2, VET)
#
# Run: cd backend && python3 app/marketdata/test_strategy_independence.py

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = Path(HERE.parents[2])
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())

import pandas as pd  # noqa: E402

FENCE = "STRATEGY_INDEPENDENCE_20261015"
FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


import app.marketdata.shared_tick_feed as SF  # noqa: E402
from app.marketdata.ltp_store import LTPStore  # noqa: E402
from app.marketdata.market_indices_state import MarketIndicesState as MIS  # noqa: E402
from app.marketdata import ws_registry  # noqa: E402
from app.core.engine_registry import BB_ENGINE_REGISTRY  # noqa: E402
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY  # noqa: E402

DF = pd.DataFrame([
    {"instrument_token": 256265, "tradingsymbol": "NIFTY 50", "segment": "INDICES", "name": "NIFTY 50",
     "expiry": None, "strike": 0, "instrument_type": "EQ"},
    {"instrument_token": 260105, "tradingsymbol": "NIFTY BANK", "segment": "INDICES", "name": "NIFTY BANK",
     "expiry": None, "strike": 0, "instrument_type": "EQ"},
    {"instrument_token": 265, "tradingsymbol": "SENSEX", "segment": "BSE-INDICES", "name": "SENSEX",
     "expiry": None, "strike": 0, "instrument_type": "EQ"},
    {"instrument_token": 11, "tradingsymbol": "NIFTY26O1922600CE", "segment": "NFO-OPT", "name": "NIFTY",
     "expiry": "2026-10-19", "strike": 22600, "instrument_type": "CE"},
    {"instrument_token": 12, "tradingsymbol": "NIFTY26O1922600PE", "segment": "NFO-OPT", "name": "NIFTY",
     "expiry": "2026-10-19", "strike": 22600, "instrument_type": "PE"},
    {"instrument_token": 13, "tradingsymbol": "NIFTY26O1924500CE", "segment": "NFO-OPT", "name": "NIFTY",
     "expiry": "2026-10-19", "strike": 24500, "instrument_type": "CE"},
    {"instrument_token": 14, "tradingsymbol": "NIFTY26O2722600CE", "segment": "NFO-OPT", "name": "NIFTY",
     "expiry": "2026-10-27", "strike": 22600, "instrument_type": "CE"},
    {"instrument_token": 99, "tradingsymbol": "BANKNIFTY26OCTFUT", "segment": "NFO-FUT", "name": "BANKNIFTY",
     "expiry": "2026-10-27", "strike": 0, "instrument_type": "FUT"},
])


class Ticker:
    MODE_FULL = "full"

    def __init__(self):
        self.subs, self.modes, self.connected = [], [], 0

    def subscribe(self, toks):
        self.subs.append(list(toks))

    def set_mode(self, mode, toks):
        self.modes.append((mode, list(toks)))

    def connect(self, threaded=True):
        self.connected += 1


class Consumer:
    def __init__(self, boom=False):
        self.ticks, self.reconnects, self.boom = [], 0, boom

    def on_tick(self, token, ltp, ts):
        if self.boom:
            raise RuntimeError("consumer down")
        self.ticks.append((token, ltp))

    def on_ws_reconnect(self):
        self.reconnects += 1


def clean():
    ws_registry._WS_ENGINE_REGISTRY.clear()
    BB_ENGINE_REGISTRY.clear()
    HA_ENGINE_REGISTRY.clear()
    with LTPStore._lock:
        for s in DF["tradingsymbol"]:
            LTPStore._prices.pop(s, None)
            LTPStore._timestamps.pop(s, None)
    with MIS._lock:
        MIS._ltp.clear()
        MIS._ltp_ws_ts.clear()


def feed(tokens=(11, 12, 99)):
    t = Ticker()
    f = SF.SharedTickFeed(None, list(tokens), instruments_df=DF, ticker_factory=lambda: t)
    return f, t


# ═════════════════════════════════════════════════════════════════════
def t_need():
    print("── A1. when is the shared feed needed ──")
    reg = {s: {"enabled": True} for s in ("SCALP_V1", "BB_V1", "BB_V2", "HA_V1")}
    case = lambda lic, r=reg: SF.needs_shared_feed(allows=lambda s: s in lic, registry=r)[0]   # noqa: E731
    check("HA_V1 without SCALP_V1 → needed", case({"HA_V1"}))
    check("BB_V1 + BB_V2 without SCALP_V1 → needed", case({"BB_V1", "BB_V2"}))
    check("HA_V1 + SCALP_V1 → NOT needed (SCALP_V1 owns the socket — your machine)", not case({"HA_V1", "SCALP_V1"}))
    check("IC / TSG / TMA / VET only (your friend) → NOT needed", not case({"IC_V2", "TSG_V1", "TMA_V2", "VET_V1"}))
    check("SCALP_V1 only → NOT needed", not case({"SCALP_V1"}))
    r2 = dict(reg, SCALP_V1={"enabled": False})
    check("SCALP_V1 licensed but disabled in the registry + HA → needed", case({"HA_V1", "SCALP_V1"}, r2))
    r3 = dict(reg, HA_V1={"enabled": False})
    check("HA licensed but disabled, no SCALP_V1 → NOT needed", not case({"HA_V1"}, r3))
    check("licence check raising → treated as not licensed (no feed, no crash)",
          SF.needs_shared_feed(allows=lambda s: 1 / 0, registry=reg)[0] is False)


def t_feed():
    print("── A2. SharedTickFeed ──")
    clean()
    f, t = feed()
    check("registers itself → get_ws_engines()[0] (what BB / HA read)", ws_registry.get_ws_engines()[0] is f)
    check("index tokens resolved (NIFTY / BANKNIFTY / SENSEX)",
          f.index_tokens == {256265: "NIFTY", 260105: "BANKNIFTY", 265: "SENSEX"}, f.index_tokens)
    check(".builders = the universe tokens", sorted(f.builders) == [11, 12, 99])
    check(".strategies[token].symbol (HA's resolver)", f.strategies[11].symbol == "NIFTY26O1922600CE")
    try:
        from app.engine.ha_options.ha_tick_engine import _symbol_for_token
        check("HA's own _symbol_for_token resolves through the feed",
              _symbol_for_token(12, f) == "NIFTY26O1922600PE")
    except ImportError as e:
        print(f"  ! ha_tick_engine not importable here ({e}) — skipped")
    check("no SCALP_V1 machinery on the feed (no indicators / signal_router / paper exits)",
          not any(hasattr(f, a) for a in ("indicators", "signal_router", "condition_engine", "_maybe_mtm_squareoff")))

    # subscribe before connect → carried into on_connect
    f.subscribe_additional_tokens([14, 13])
    time.sleep(0.5)
    check("subscribe before connect: queued, not sent", t.subs == [] and {13, 14} <= f._extra_tokens)
    bb1, bb2, ha = Consumer(), Consumer(), Consumer()
    BB_ENGINE_REGISTRY.extend([bb1, bb2])
    HA_ENGINE_REGISTRY.append(ha)
    f._on_connect(t, {})
    sent = set(t.subs[-1])
    check("on_connect subscribes universe + indices + queued extras",
          sent == {11, 12, 99, 256265, 260105, 265, 13, 14}, sorted(sent))
    check("…in MODE_FULL (as the shared engine)", t.modes[-1][0] == "full" and set(t.modes[-1][1]) == sent)
    check("…and notifies BB + HA on_ws_reconnect", bb1.reconnects == bb2.reconnects == ha.reconnects == 1)
    # subscribe after connect → sent live
    f.subscribe_additional_tokens([77])
    time.sleep(0.6)
    check("subscribe after connect: sent immediately", [77] in t.subs)

    ticks = [{"instrument_token": 99, "last_price": 55100.0},       # BB FUT
             {"instrument_token": 11, "last_price": 120.5},
             {"instrument_token": 256265, "last_price": 22680.0},   # index
             {"instrument_token": 14, "last_price": 150.0},         # extra token
             {"instrument_token": None, "last_price": 1.0},
             {"instrument_token": 12, "last_price": None}]
    f._on_ticks(t, ticks)
    want = [(99, 55100.0), (11, 120.5), (256265, 22680.0), (14, 150.0)]
    check("every valid tick fans out to BOTH BB engines", bb1.ticks == want and bb2.ticks == want, bb1.ticks)
    check("…and to HA (index + extra tokens included, as the shared engine)", ha.ticks == want, ha.ticks)
    check("index tick → MarketIndicesState (WS-fresh → index REST feeder stays idle)",
          MIS._ltp.get("NIFTY") == 22680.0 and not MIS.ws_stale(("NIFTY",), 5.0))
    check("universe CE → LTPStore", LTPStore.get("NIFTY26O1922600CE") == 120.5)
    check("FUT not written to LTPStore (shared engine: CE/PE only; BB publishes its own)",
          LTPStore.get("BANKNIFTY26OCTFUT") is None)
    # one consumer failing
    clean()
    f, t = feed()
    bad, good, ha = Consumer(boom=True), Consumer(), Consumer()
    BB_ENGINE_REGISTRY.extend([bad, good])
    HA_ENGINE_REGISTRY.append(ha)
    f._on_ticks(t, [{"instrument_token": 11, "last_price": 121.0}])
    check("a raising BB engine never starves HA or LTPStore",
          ha.ticks == [(11, 121.0)] and LTPStore.get("NIFTY26O1922600CE") == 121.0)
    check("start() connects once, only when the market is open (threaded wait)",
          hasattr(f, "_wait_and_connect") and hasattr(f, "_tick_watchdog"))
    clean()


def t_launcher():
    print("── A3. launcher ──")

    class Mgr:
        def __init__(self, ready=True):
            self.ready = ready
        def is_ready(self):
            return self.ready
        def get_trade_kite(self):
            return object()
        def get_data_kite(self):
            return object()

    slept = []

    async def sl(s):
        slept.append(s)

    built = []

    class FakeFeed:
        def __init__(self, kd, toks):
            built.append(toks)
            self.started = False
        def start(self):
            self.started = True

    r = asyncio.run(SF.shared_tick_feed_loop(Mgr(), _need=lambda: (False, "x"), _feed_cls=FakeFeed,
                                             _engines=lambda: [], _sleep=sl, _max_cycles=3))
    check("not needed → returns at once, builds nothing", r is None and not built and not slept)
    r = asyncio.run(SF.shared_tick_feed_loop(Mgr(), _need=lambda: (True, "HA"), _build=lambda k: [11, 12],
                                             _feed_cls=FakeFeed, _engines=lambda: [], _sleep=sl, _max_cycles=3))
    check("needed → builds once with the universe, starts, returns the feed",
          isinstance(r, FakeFeed) and r.started and built == [[11, 12]], built)
    built.clear(); slept.clear()
    r = asyncio.run(SF.shared_tick_feed_loop(Mgr(ready=False), _need=lambda: (True, "HA"), _build=lambda k: [11],
                                             _feed_cls=FakeFeed, _engines=lambda: [], _sleep=sl, _max_cycles=3))
    check("broker not ready → waits (30s steps), builds nothing", r is None and not built and slept == [30, 30, 30])
    slept.clear()
    r = asyncio.run(SF.shared_tick_feed_loop(Mgr(), _need=lambda: (True, "HA"), _build=lambda k: [],
                                             _feed_cls=FakeFeed, _engines=lambda: [], _sleep=sl, _max_cycles=2))
    check("empty universe → retry every 120s", r is None and slept == [SF.RETRY_S, SF.RETRY_S])
    slept.clear()
    r = asyncio.run(SF.shared_tick_feed_loop(Mgr(), _need=lambda: (True, "HA"), _build=lambda k: 1 / 0,
                                             _feed_cls=FakeFeed, _engines=lambda: [], _sleep=sl, _max_cycles=2))
    check("build raising → logged, retried, loop alive", r is None and slept == [SF.RETRY_S, SF.RETRY_S])
    built.clear()
    r = asyncio.run(SF.shared_tick_feed_loop(Mgr(), _need=lambda: (True, "HA"), _build=lambda k: [11],
                                             _feed_cls=FakeFeed, _engines=lambda: ["zte"], _sleep=sl, _max_cycles=2))
    check("an engine already registered → stands down (never a second socket)", r is None and not built)


# ═════════════════════════════════════════════════════════════════════
def t_frontend():
    print("── B. NIFTY50 alias (static) ──")
    p = REPO / "frontend/src/context/MarketDataContext.jsx"
    if not p.exists():
        print("  ! frontend tree not found — skipped")
        return
    s = p.read_text(encoding="utf-8")
    blk = s[s.find("const mergedLtpMap"):s.find("// ── SPOT_IN_LTPMAP END ──")]
    check("mergedLtpMap aliases NIFTY50 from indices.NIFTY", FENCE in blk and "m.NIFTY50" in blk)
    check("…only when nothing publishes NIFTY50 (ORB's own print wins)", "m.NIFTY50 == null" in blk)


# ═════════════════════════════════════════════════════════════════════
def t_carry(modname, cls, label):
    print(f"── C. {label}: carried legs ──")
    import importlib
    M = importlib.import_module(modname)
    clean()
    e = getattr(M, cls)(None, DF, lambda *a: None, capture_dir=None)
    e._spot_token = 256265
    e._builders = {256265: M.CandleBuilder(), 11: M.CandleBuilder()}
    e._tok2sym = {11: "NIFTY26O1922600CE"}
    e.chain.put_meta("NIFTY26O1922600CE", 22600, "2026-10-19", "CE", 11)
    added = e.ensure_symbols(["NIFTY26O1924500CE", "NIFTY26O1922600CE", None, "NOPE"])
    check(f"{label}: out-of-band carried leg subscribed (builder + token map)",
          added == ["NIFTY26O1924500CE"] and 13 in e._builders and e._tok2sym.get(13) == "NIFTY26O1924500CE", added)
    check(f"{label}: in-band / None / unknown symbols skipped", 11 in e._builders and len(e._builders) == 3)
    check(f"{label}: NOT a selection candidate (no chain meta → backtest band parity)",
          e.chain.symbols("CE") == ["NIFTY26O1922600CE"])
    check(f"{label}: idempotent", e.ensure_symbols(["NIFTY26O1924500CE"]) == [])
    base = (int(time.time()) // 60) * 60 - 600

    class TS:
        def __init__(self, v): self.v = v
        def timestamp(self): return self.v
    e._on_ticks(None, [{"instrument_token": 13, "last_price": 3.1, "exchange_timestamp": TS(base + 5)},
                       {"instrument_token": 13, "last_price": 3.4, "exchange_timestamp": TS(base + 65)}])
    c = e.chain._c.get("NIFTY26O1924500CE", {}).get(base)
    check(f"{label}: carried leg builds candles (manager prices exits off them)", c is not None and c["close"] == 3.1, c)
    check(f"{label}: carried leg published to LTPStore (TMA_TICK_LTP_PUBLISH)", LTPStore.get("NIFTY26O1924500CE") == 3.4)
    import datetime as _d
    h = e.expiry_hint("2026-10-20", today=_d.date(2026, 10, 15))
    check(f"{label}: expiry_hint names computed vs listed expiries",
          "2026-10-20" in h and "2026-10-19" in h and "2026-10-27" in h, h)
    clean()


def t_wiring():
    print("── C/A. wiring (static) ──")
    for rel, tag in (("app/engine/tma2/tma2_selection_loop.py", "TMA2"), ("app/engine/tma/tma_selection_loop.py", "TMA")):
        s = (BACKEND / rel).read_text(encoding="utf-8")
        i_e = s.find("engine.ensure_symbols(")
        i_s = s.find("    engine.start()")
        check(f"{tag} loop: carried legs subscribed BEFORE engine.start()", 0 < i_e < i_s, (i_e, i_s))
        check(f"{tag} loop: misleading 'only refreshes when MISSING' alert gone",
              "only refreshes when the file is MISSING" not in s and "engine.expiry_hint(expiry_iso)" in s)
    s = (BACKEND / "app/engine/vet/vet_selection_loop.py").read_text(encoding="utf-8")
    check("VET loop: empty-universe alert names the expiries", "tick.expiry_hint(expiry_iso)" in s)
    s = (BACKEND / "app/api_server.py").read_text(encoding="utf-8")
    i_f = s.find("INDEX_LTP_FEEDER_20261015 END (feeder launch)")
    i_s = s.find(f"{FENCE} BEGIN (shared feed launch)")
    blk = s[i_s:s.find(f"{FENCE} END (shared feed launch)")]
    check("api_server: shared feed launched after the index feeder", 0 < i_f < i_s)
    check("…own _boot_guard + _supervise, decision inside the loop",
          '_boot_guard("shared_tick_feed")' in blk and "shared_tick_feed_loop(zerodha_manager)" in blk)


if __name__ == "__main__":
    t_need()
    t_feed()
    t_launcher()
    t_frontend()
    t_carry("app.engine.tma2.tma2_tick_engine", "TMA2TickEngine", "TMA2 engine (Timberwolf + Velvet)")
    t_carry("app.engine.tma.tma_tick_engine", "TMATickEngine", "TMA_V1 engine")
    t_wiring()
    clean()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} STRATEGY INDEPENDENCE CHECKS PASSED")
