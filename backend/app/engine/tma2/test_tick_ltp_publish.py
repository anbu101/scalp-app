# backend/app/engine/tma2/test_tick_ltp_publish.py — fence TMA_TICK_LTP_PUBLISH_20261015
#
#   1. Both tick engines (TMA2TickEngine — used by Timberwolf AND Velvet — and
#      TMA_V1's clone) publish every option tick into LTPStore; spot, unknown
#      tokens and zero prices are not published
#   2. Candle parity: a 3,000-tick randomized stream produces IDENTICAL option
#      and spot candles through the patched engine and the ORIGINAL (.bak)
#   3. A failing LTPStore never touches the candle path
#   4. Consumers now see live marks: Velvet route _mark_for → "LTP" (panel
#      ticks), fleet _mark → live (not "marks as of"), TMA2 backstop's
#      _fresh_main_ltp reads a fresh print but still rejects a stale one
#
# Run: cd backend && python3 app/engine/tma2/test_tick_ltp_publish.py

from __future__ import annotations

import importlib.util
import os
import random
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[3])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())

FENCE = "TMA_TICK_LTP_PUBLISH_20261015"
FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


from app.marketdata.ltp_store import LTPStore  # noqa: E402
import app.engine.tma2.tma2_tick_engine as E2  # noqa: E402
import app.engine.tma.tma_tick_engine as E1  # noqa: E402

SPOT = 256265
OPTS = {9001: "NIFTY26O1922800PE", 9002: "NIFTY26O1921700PE", 9003: "NIFTY26O1922650CE"}


def clear():
    with LTPStore._lock:
        for s in list(OPTS.values()) + ["NIFTY 50", "NIFTY50", str(SPOT), "UNKNOWN"]:
            LTPStore._prices.pop(s, None)
            LTPStore._timestamps.pop(s, None)


def engine(mod, cls_name):
    e = getattr(mod, cls_name)(None, None, lambda *a: None, capture_dir=None)
    e._spot_token = SPOT
    e._builders = {SPOT: mod.CandleBuilder()}
    e._tok2sym = {}
    for tok, sym in OPTS.items():
        e._builders[tok] = mod.CandleBuilder()
        e._tok2sym[tok] = sym
    return e


def tick(tok, px, epoch=None):
    t = {"instrument_token": tok, "last_price": px}
    if epoch is not None:
        class _TS:
            def __init__(self, v): self.v = v
            def timestamp(self): return self.v
        t["exchange_timestamp"] = _TS(epoch)
    return t


def load_orig(rel, name):
    bak = Path(BACKEND) / rel
    bak = bak.with_name(bak.name + f".bak-{FENCE}")
    if not bak.exists():
        return None
    ldr = SourceFileLoader(name, str(bak))
    spec = importlib.util.spec_from_loader(name, ldr)
    mod = importlib.util.module_from_spec(spec)
    ldr.exec_module(mod)
    return mod


def t_publish(mod, cls_name, label):
    print(f"── 1. {label}: publish ──")
    clear()
    e = engine(mod, cls_name)
    t0 = time.time()
    e._on_ticks(None, [tick(9001, 170.6), tick(9002, 4.65), tick(SPOT, 22671.3),
                       tick(4242, 99.0), tick(9003, 0), tick(9003, None)])
    got = LTPStore.get_with_timestamp(OPTS[9001])
    check(f"{label}: sold leg published (170.60)", got is not None and got[0] == 170.6, got)
    check(f"{label}: hedge leg published (4.65)", LTPStore.get(OPTS[9002]) == 4.65)
    check(f"{label}: stamp is wall-clock now (fresh for every consumer)",
          got is not None and abs(got[1] - t0) < 5, got)
    check(f"{label}: zero / None price not published", LTPStore.get(OPTS[9003]) is None)
    check(f"{label}: spot token NOT written into LTPStore",
          all(LTPStore.get(k) is None for k in ("NIFTY 50", "NIFTY50", str(SPOT))))
    check(f"{label}: token outside the universe ignored", LTPStore.get("UNKNOWN") is None)
    e._on_ticks(None, [tick(9001, 171.25)])
    check(f"{label}: next tick overwrites (live, tick-by-tick)", LTPStore.get(OPTS[9001]) == 171.25)

    # failing store never touches candles
    clear()
    real = LTPStore.update
    try:
        LTPStore.update = classmethod(lambda cls, s, p: (_ for _ in ()).throw(RuntimeError("store down")))
        e = engine(mod, cls_name)
        base = (int(time.time()) // 60) * 60 - 600
        err = None
        try:
            e._on_ticks(None, [tick(9001, 100.0, base + 1), tick(9001, 101.0, base + 30)])
            e._on_ticks(None, [tick(9001, 102.0, base + 61)])      # rolls the minute
        except Exception as x:
            err = x
    finally:
        LTPStore.update = real
    c = e.chain._c.get(OPTS[9001], {}).get(base)
    check(f"{label}: LTPStore raising → no exception, candle still built",
          err is None and c is not None and c["close"] == 101.0, (err, c))


def t_parity(mod, orig, cls_name, label):
    print(f"── 2. {label}: candle parity vs ORIGINAL ──")
    if orig is None:
        print("  ! .bak not found — parity vs original skipped")
        return
    random.seed(11)
    new_e, old_e = engine(mod, cls_name), engine(orig, cls_name)
    base = (int(time.time()) // 60) * 60 - 3 * 3600
    toks = list(OPTS) + [SPOT, 4242]
    px = {t: 100.0 for t in toks}
    px[SPOT] = 22600.0
    ep = base
    for _ in range(300):
        batch = []
        for _ in range(10):
            tk = random.choice(toks)
            px[tk] = max(0.05, px[tk] + random.gauss(0, 1.5))
            ep += random.choice((0, 1, 2, 3))
            batch.append(tick(tk, round(px[tk], 2) if random.random() > 0.03 else 0, ep))
        new_e._on_ticks(None, [dict(t) for t in batch])
        old_e._on_ticks(None, [dict(t) for t in batch])
    same_opts = new_e.chain._c == old_e.chain._c
    n = sum(len(v) for k, v in new_e.chain._c.items() if k in OPTS.values())
    check(f"{label}: 3,000 random ticks → identical option candles ({n} bars)", same_opts and n > 50, n)
    check(f"{label}: identical spot candles", new_e._spot_candles == old_e._spot_candles
          and len(new_e._spot_candles) > 10, len(new_e._spot_candles))
    check(f"{label}: identical builder state at the end",
          all(vars(new_e._builders[t]) == vars(old_e._builders[t]) for t in toks if t in new_e._builders))


def t_consumers():
    print("── 4. consumers see live marks ──")
    clear()
    e = engine(E2, "TMA2TickEngine")
    e._on_ticks(None, [tick(9003, 148.75), tick(9001, 170.6)])
    try:
        import app.api.vet_state_routes as V
        px, src = V._mark_for(OPTS[9003], lambda s: 140.0)
        check("Velvet route: mark_src 'LTP' → panel ticks live (was QUOTE_1M)", src == "LTP" and px == 148.75, (px, src))
        with LTPStore._lock:
            LTPStore._timestamps[OPTS[9003]] = time.time() - 3600
        px, src = V._mark_for(OPTS[9003], lambda s: 140.0)
        check("Velvet route: a stale print still loses to its own 1m quote", src == "QUOTE_1M" and px == 140.0, (px, src))
    except ImportError as x:
        print(f"  ! vet_state_routes not importable here ({x}) — skipped")
    try:
        import app.api.fleet_today_routes as F
        now = int(time.time())
        ltp, ts, stale = F._mark(OPTS[9001], now)
        check("Dashboard fleet row: Timberwolf leg marked LIVE (not 'marks as of')",
              ltp == 170.6 and stale is False, (ltp, ts, stale))
    except ImportError as x:
        print(f"  ! fleet_today_routes not importable here ({x}) — skipped")
    import app.engine.tma2.tma2_trade_manager as TM
    m = TM.TMA2TradeManager.__new__(TM.TMA2TradeManager) if hasattr(TM, "TMA2TradeManager") else None
    if m is not None and hasattr(m, "_fresh_main_ltp"):
        check("TMA2 backstop reads a fresh print (≤20s)", m._fresh_main_ltp(OPTS[9001], max_age_s=20) == 170.6)
        with LTPStore._lock:
            LTPStore._timestamps[OPTS[9001]] = time.time() - 120
        check("TMA2 backstop still REJECTS a stale print (overnight-trap guard intact)",
              m._fresh_main_ltp(OPTS[9001], max_age_s=20) is None)
    else:
        print("  ! TMA2 trade manager class not found by name — backstop check skipped")
    clear()


if __name__ == "__main__":
    o2 = load_orig("app/engine/tma2/tma2_tick_engine.py", "tma2_tick_orig")
    o1 = load_orig("app/engine/tma/tma_tick_engine.py", "tma_tick_orig")
    t_publish(E2, "TMA2TickEngine", "TMA2 engine (Timberwolf + Velvet)")
    t_publish(E1, "TMATickEngine", "TMA_V1 engine")
    t_parity(E2, o2, "TMA2TickEngine", "TMA2 engine")
    t_parity(E1, o1, "TMATickEngine", "TMA_V1 engine")
    t_consumers()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} TICK LTP PUBLISH CHECKS PASSED")
