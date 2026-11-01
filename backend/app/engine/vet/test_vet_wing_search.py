# backend/app/engine/vet/test_vet_wing_search.py
#
# ── VET_WING_FAR_20261101 ── SEARCH + SUBSCRIBE side of the far-wing fix
# ============================================================================
#   A. find_far_wing (pure): dearest real <= cap, OTM of the main, this
#      expiry/underlying/side only, band contracts excluded, traded-today
#      rule, reach bound, one quote per <=500, rate-limit retry once,
#      distance facts, never raises.
#   B. TMA2TickEngine.add_live_symbol: builder + token map + chain meta;
#      subscribes LIVE when the socket is up (LTP mode), otherwise on the
#      next connect; idempotent; a REAL-epoch tick stream then prices the
#      added wing through the same chain store the manager's quote() reads;
#      ensure_symbols (TMA_V2 carry path) still writes NO meta.
#   C. the selection loop's REAL wiring block, executed against fakes:
#      band edges from the armed chain, data-kite quote, subscribe, the
#      none-found log; and its order inside the loop (after the universe,
#      before carried legs and tick.start()).
#
# Runs standalone:  PYTHONPATH=backend python3 app/engine/vet/test_vet_wing_search.py
# ============================================================================

from __future__ import annotations

import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, BACKEND)

import pandas as pd                                          # noqa: E402

import app.engine.tma2.tma2_tick_engine as TE                # noqa: E402
from app.engine.vet.vet_wing_search import (                 # noqa: E402
    QUOTE_CHUNK, WING_SEARCH_MAX_STRIKES_DEFAULT, find_far_wing)

FENCE = "VET_WING_FAR_20261101"
FAILS = []
OK = [0]


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    if cond:
        OK[0] += 1
    else:
        FAILS.append(name)


IST = timezone(timedelta(hours=5, minutes=30))
EXP = "2026-11-03"


def sym(k, side, exp="26N03"):
    return f"NIFTY{exp}{k}{side}"


def idf(lo=18000, hi=30000, expiry=EXP, extra=True):
    rows = []
    for k in range(lo, hi + 50, 50):
        for side in ("CE", "PE"):
            rows.append({"name": "NIFTY", "segment": "NFO-OPT", "instrument_type": side,
                         "expiry": pd.Timestamp(expiry).date(), "strike": float(k),
                         "tradingsymbol": sym(k, side), "instrument_token": 10_000 + k * 2
                         + (0 if side == "CE" else 1)})
            if extra:
                rows.append({"name": "NIFTY", "segment": "NFO-OPT", "instrument_type": side,
                             "expiry": pd.Timestamp("2026-11-10").date(), "strike": float(k),
                             "tradingsymbol": sym(k, side, "26N10"),
                             "instrument_token": 500_000 + k * 2})
                rows.append({"name": "BANKNIFTY", "segment": "NFO-OPT", "instrument_type": side,
                             "expiry": pd.Timestamp(expiry).date(), "strike": float(k),
                             "tradingsymbol": f"BANKNIFTY26N03{k}{side}",
                             "instrument_token": 900_000 + k * 2})
    rows.append({"name": "NIFTY", "segment": "NFO-FUT", "instrument_type": "FUT",
                 "expiry": pd.Timestamp(expiry).date(), "strike": 0.0,
                 "tradingsymbol": "NIFTY26NOVFUT", "instrument_token": 7})
    return pd.DataFrame(rows)


IDF = idf()
SPOT = 24000.0


def pe_px(k):          # band (>= 22500) never <= 3 ; 22450 → 4.20 … 22300 → 2.85
    if k >= 22500:
        return round(180 - (SPOT - k) * 0.115, 2)
    return round(max(0.3, 4.2 - (22450 - k) / 50 * 0.45), 2)


def ce_px(k):
    d = k - SPOT
    return round(max(0.3, 4.2 - (d - 1550) / 50 * 0.45), 2) if d > 1550 else round(180 - d * 0.11, 2)


class Q:
    def __init__(self, vol=None, fail=None, px=None):
        self.calls, self.vol, self.fail = [], vol or {}, list(fail or [])
        self.px = px

    def __call__(self, keys):
        self.calls.append(list(keys))
        if self.fail:
            raise self.fail.pop(0)
        out = {}
        for key in keys:
            s = key.split(":", 1)[1]
            if not s.startswith("NIFTY26N03"):
                continue
            k, side = int(s[10:-2]), s[-2:]
            p = self.px(k, side) if self.px else (pe_px(k) if side == "PE" else ce_px(k))
            out[key] = {"last_price": p, "volume": self.vol.get(s, 500)}
        return out


BAND = (22500.0, 25500.0)
BAND_SYMS = {sym(k, s) for k in range(22500, 25550, 50) for s in ("CE", "PE")}


def run(**kw):
    base = dict(instruments_df=IDF, quote_batch=Q(), side="PE", main_strike=24050,
                main_symbol=sym(24050, "PE"), expiry_iso=EXP, cap=3.0,
                max_strikes=WING_SEARCH_MAX_STRIKES_DEFAULT, exclude=BAND_SYMS,
                band=BAND, step=50, sleep=lambda s: None)
    base.update(kw)
    return find_far_wing(**base)


# ── A. pure search ────────────────────────────────────────────────────
print("── A. find_far_wing ──")
q = Q()
row, info = run(quote_batch=q)
check("PE: finds the DEAREST real wing <= 3 beyond the band (22300PE @ 2.85)",
      row and row["tradingsymbol"] == sym(22300, "PE") and row["ltp"] == 2.85, (row, info))
check("row carries token / strike / expiry / side",
      row and row["token"] == 10_000 + 22300 * 2 + 1 and row["strike"] == 22300.0
      and row["expiry"] == EXP and row["instrument_type"] == "PE")
check("distance: 4 strikes / 200 pts beyond the band edge",
      row and row["_far"]["beyond_band_strikes"] == 4
      and row["_far"]["beyond_band_points"] == 200.0 and not row["_far"]["in_band"], row and row["_far"])
check("distance: 35 strikes from the main", row and row["_far"]["strikes_from_main"] == 35)
check("ONE quote call", len(q.calls) == 1, len(q.calls))
keys = q.calls[0]
check("only this expiry / this underlying / this side were quoted",
      all(k.startswith("NFO:NIFTY26N03") and k.endswith("PE") for k in keys), keys[:3])
check("only strikes OTM of the main, within 90 strikes",
      all(24050 - 90 * 50 <= int(k[14:-2]) < 24050 for k in keys))
check("band contracts were NOT re-quoted", not (set(k[4:] for k in keys) & BAND_SYMS))
check("nearest strike first", int(keys[0][14:-2]) == 22450, keys[0])
check("info reason = found", info["reason"] == "found")

row, _ = run(side="CE", main_strike=23950, main_symbol=sym(23950, "CE"))
check("CE: mirrored search finds the dearest CE <= 3 above the band",
      row and row["instrument_type"] == "CE" and row["strike"] > BAND[1] and row["ltp"] <= 3.0
      and all(not (BAND[1] < k < row["strike"] and ce_px(k) <= 3.0) for k in range(25550, 30050, 50)),
      row)

row, info = run(cap=0.2)
check("nothing <= cap → None with a reason naming the cheapest",
      row is None and "none traded today at <= 0.2" in info["reason"]
      and "cheapest" in info["reason"], info)

row, info = run(max_strikes=30)
check("reach inside the band only → no candidates → None",
      row is None and info["candidates"] == 0 and "no unpriced PE" in info["reason"], info)
row, _ = run(max_strikes=35)
check("reach exactly to 22300 → found", row and row["strike"] == 22300.0)
row, _ = run(max_strikes=34)
check("reach one short (22350 @ 3.30 > cap) → None", row is None)

vol0 = Q(vol={sym(22300, "PE"): 0})
row, _ = run(quote_batch=vol0)
check("a strike that never traded today is skipped → next real (22250)",
      row and row["strike"] == 22250.0, row)


class QNoVol(Q):
    def __call__(self, keys):
        out = super().__call__(keys)
        for v in out.values():
            v.pop("volume", None)
        return out


row, _ = run(quote_batch=QNoVol())
check("quote without a volume field (ltp-shaped) is still accepted",
      row and row["strike"] == 22300.0)

slept = []
q429 = Q(fail=[Exception("TokenException? no: Too many requests")])
row, info = run(quote_batch=q429, sleep=slept.append)
check("rate-limit once → waits 1.1 s and retries → found",
      row is not None and slept == [1.1] and len(q429.calls) == 2, (slept, info))
q429x2 = Q(fail=[Exception("Too many requests"), Exception("Too many requests")])
row, info = run(quote_batch=q429x2)
check("rate-limit twice → None (never raises)", row is None and "quote failed" in info["reason"])
row, info = run(quote_batch=Q(fail=[RuntimeError("Connection reset")]))
check("other quote error → None, no retry", row is None and "quote failed" in info["reason"])

row, info = run(instruments_df=None)
check("no instruments list → None", row is None and "instruments" in info["reason"])
row, info = run(cap="x")
check("garbage cap → None, never raises", row is None)
row, info = run(side="FUT")
check("bad side → None", row is None)
row, info = run(max_strikes=0)
check("max_strikes 0 → search not run", row is None and info["candidates"] == 0)

row, _ = run(exclude=BAND_SYMS - {sym(22550, "PE")},
             quote_batch=Q(px=lambda k, s: 2.95 if k == 22550 else (pe_px(k) if s == "PE" else ce_px(k))))
check("an UNPRINTED in-band strike is a candidate and reported in_band",
      row and row["strike"] == 22550.0 and row["_far"]["in_band"]
      and row["_far"]["beyond_band_strikes"] == 0, row and row["_far"])

big = idf(lo=1000, hi=60000, extra=False)
qb = Q(px=lambda k, s: 50.0)                  # nothing cheap — forces a full scan
row, info = run(instruments_df=big, side="CE", main_strike=1050, main_symbol=sym(1050, "CE"),
                max_strikes=2000, quote_batch=qb, exclude=())
check(f"large chain is quoted in chunks of <= {QUOTE_CHUNK}",
      info["candidates"] > QUOTE_CHUNK and len(qb.calls) >= 2 and all(len(c) <= QUOTE_CHUNK for c in qb.calls)
      and sum(len(c) for c in qb.calls) == info["candidates"], [len(c) for c in qb.calls])

for label, ex in (("str", EXP), ("Timestamp", pd.Timestamp(EXP)), ("datetime", datetime(2026, 11, 3))):
    df = IDF.copy()
    df["expiry"] = [ex if label != "str" else EXP for _ in range(len(df))] if label != "str" \
        else df["expiry"].map(str)
    row, _ = run(instruments_df=df)
    check(f"instrument expiry as {label} matches", row and row["strike"] == 22300.0)

# ── B. tick engine ────────────────────────────────────────────────────
print("\n── B. TMA2TickEngine.add_live_symbol ──")
TE_LOG = []
_te_real = TE.write_audit_log
TE.write_audit_log = lambda m: (TE_LOG.append(m), _te_real(m))[1]


class FakeKWS:
    MODE_LTP = "ltp"

    def __init__(self, fail=False):
        self.subs, self.modes, self.fail = [], [], fail

    def subscribe(self, toks):
        if self.fail:
            raise RuntimeError("socket gone")
        self.subs.append(list(toks))

    def set_mode(self, mode, toks):
        self.modes.append((mode, list(toks)))


def engine():
    e = TE.TMA2TickEngine(None, None, lambda *a: None, log_tag="VET_TICK")
    e._spot_token = 256265
    e._builders = {256265: TE.CandleBuilder()}
    for k in range(22500, 25550, 50):                # the armed band
        for s in ("CE", "PE"):
            tok = 10_000 + k * 2 + (0 if s == "CE" else 1)
            e._builders[tok] = TE.CandleBuilder()
            e._tok2sym[tok] = sym(k, s)
            e.chain.put_meta(sym(k, s), float(k), EXP, s, tok)
    return e


e = engine()
n0 = len(e._builders)
tok = 10_000 + 22300 * 2 + 1
ok = e.add_live_symbol(sym(22300, "PE"), tok, 22300.0, EXP, "PE")
check("before the socket is up: added, not subscribed yet",
      ok and tok in e._builders and e._tok2sym[tok] == sym(22300, "PE") and len(e._builders) == n0 + 1)
check("chain meta written → selectable in the VET universe",
      (e.chain.meta(sym(22300, "PE")) or {}).get("strike") == 22300.0
      and sym(22300, "PE") in e.chain.symbols("PE"))
check("log says it subscribes on (re)connect",
      any("[VET_TICK][ADD]" in m and "subscribes on (re)connect" in m for m in TE_LOG))
kws = FakeKWS()
e._on_connect(kws, None)
check("on_connect subscribes the added token with the band",
      tok in kws.subs[-1] and kws.modes[-1] == ("ltp", kws.subs[-1]))

e = engine()
kws = FakeKWS()
e._kws, e._ws_connected = kws, True
ok = e.add_live_symbol(sym(22300, "PE"), str(tok), 22300, EXP, "PE")
check("socket up: subscribed LIVE, LTP mode, that one token only",
      ok and kws.subs == [[tok]] and kws.modes == [("ltp", [tok])], (kws.subs, kws.modes))
ok2 = e.add_live_symbol(sym(22300, "PE"), tok, 22300, EXP, "PE")
check("idempotent: a second add does not resubscribe", ok2 and kws.subs == [[tok]])
in_tok = 10_000 + 23000 * 2 + 1
before = dict(e.chain.meta(sym(23000, "PE")))
e.add_live_symbol(sym(23000, "PE"), in_tok, 99999, "1999-01-01", "PE")
check("an in-band contract is left exactly as it was",
      e.chain.meta(sym(23000, "PE")) == before and kws.subs == [[tok]])
check("bad token → False, nothing added",
      e.add_live_symbol("X", "abc", 1, EXP, "PE") is False and "X" not in e._tok2sym.values())
e2 = engine()
e2._kws, e2._ws_connected = FakeKWS(fail=True), True
check("subscribe raising → still added (True), logged, next connect covers it",
      e2.add_live_symbol(sym(22300, "PE"), tok, 22300, EXP, "PE") is True
      and tok in e2._builders and any("subscribe failed" in m for m in TE_LOG))

# REAL epoch tick stream → priced through the chain store
t0 = datetime(2026, 10, 28, 12, 5, 7, tzinfo=IST)
m0 = int(t0.timestamp()) - int(t0.timestamp()) % 60
e._on_ticks(kws, [{"instrument_token": tok, "last_price": 2.9, "exchange_timestamp": t0}])
e._on_ticks(kws, [{"instrument_token": tok, "last_price": 2.7,
                   "exchange_timestamp": t0 + timedelta(seconds=30)}])
e._on_ticks(kws, [{"instrument_token": tok, "last_price": 2.8,
                   "exchange_timestamp": t0 + timedelta(seconds=60)}])
px = e.chain.last_close_at_or_before(sym(22300, "PE"), m0 + 60, 15)
check("real-epoch ticks on the added wing build a 12:05 candle the quote path reads (close 2.7)",
      px == 2.7, px)
check("…keyed at the real minute start (12:05:00 IST)",
      datetime.fromtimestamp(m0, IST).strftime("%H:%M:%S") == "12:05:00"
      and e.chain._c[sym(22300, "PE")].get(m0) is not None)

ec = engine()
df_one = pd.DataFrame([{"tradingsymbol": sym(21000, "PE"), "instrument_token": 4242}])
ec.instruments_df = df_one
ec.ensure_symbols([sym(21000, "PE")])
check("TMA_V2 carry path (ensure_symbols) still writes NO chain meta",
      4242 in ec._builders and ec.chain.meta(sym(21000, "PE")) is None)

# ── C. selection-loop wiring ──────────────────────────────────────────
print("\n── C. selection loop wiring ──")
src = open(os.path.join(HERE, "vet_selection_loop.py"), encoding="utf-8").read()
b = src.index("# ── VET_WING_FAR_20261101 BEGIN ── far-wing search wiring")
e_ = src.index("# ── VET_WING_FAR_20261101 END ──")
block = textwrap.dedent("    " + src[b:e_].split("\n", 1)[1])
i_univ = src.index("n = tick.resolve_universe(")
i_carry = src.index("# carried strikes may sit outside today's band after a gap")
i_start = src.index("    tick.start()\n")
check("wiring sits after the universe is resolved, before carried legs and tick.start()",
      i_univ < b < i_carry < i_start)
check("manager construction precedes the wiring", src.index("manager = VetManager(") < b)


class ZM:
    def __init__(self, dk):
        self.dk = dk

    def get_data_kite(self):
        return self.dk


class K:
    def __init__(self):
        self.q = Q()

    def quote(self, keys):
        return self.q(keys)


class Mgr:
    wing_search_fn = None


LOOP_LOG = []
ew = engine()
ew._kws, ew._ws_connected = FakeKWS(), True
ns = {"tick": ew, "kite": K(), "zerodha_manager": ZM(K()), "instruments_df": IDF,
      "expiry_iso": EXP, "manager": Mgr(), "write_audit_log": LOOP_LOG.append,
      "chain_list": lambda side, ts: [{"tradingsymbol": s} for s in ew.chain.symbols(side)]}
exec(compile(block, "vet_selection_loop.py<wiring>", "exec"), ns)
check("band edges recorded from the armed chain", ns["_wing_band"] == BAND, ns["_wing_band"])
fw = ns["manager"].wing_search_fn
check("manager.wing_search_fn is wired", callable(fw))
main = {"tradingsymbol": sym(24050, "PE"), "strike": 24050.0, "expiry": EXP}
row = fw("PE", int(t0.timestamp()), main, EXP, 3.0, 90)
check("wired fn finds 22300PE and SUBSCRIBES it on the live socket",
      row and row["tradingsymbol"] == sym(22300, "PE") and ew._kws.subs == [[tok]]
      and row["_far"]["subscribed"] is True, (row, ew._kws.subs))
check("the DATA kite was used for the quote (not the trade kite)",
      len(ns["zerodha_manager"].dk.q.calls) == 1 and ns["kite"].q.calls == [])
row2 = fw("PE", int(t0.timestamp()), main, EXP, 3.0, 90)
check("next attempt: the wing is now IN the universe → not re-quoted from REST as a candidate",
      sym(22300, "PE") not in "".join(ns["zerodha_manager"].dk.q.calls[-1]))
check("…(and the search moves on to the next real one, since the band now prices it)",
      row2 is None or row2["tradingsymbol"] != sym(22300, "PE"))
none = fw("PE", int(t0.timestamp()), main, EXP, 0.1, 90)
check("none found → None + a [VET][WING_FAR] reason line",
      none is None and any(m.startswith("[VET][WING_FAR] PE: searched") for m in LOOP_LOG), LOOP_LOG[-1:])
ns2 = dict(ns, zerodha_manager=ZM(None), manager=Mgr())
exec(compile(block, "vet_selection_loop.py<wiring>", "exec"), ns2)
ns2["manager"].wing_search_fn("PE", 0, main, EXP, 3.0, 90)
check("no data kite → falls back to the session kite", len(ns2["kite"].q.calls) >= 1)
check("arm log reports wing_search and band",
      "wing_search={manager.wing_search_max_strikes" in src and "band={_wing_band}" in src)

if FAILS:
    print(f"\n{OK[0]} passed, {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print(f"\nALL {OK[0]} VET WING-SEARCH CHECKS PASSED")
