# backend/app/engine/vet/test_vet_wing_far.py
#
# ── VET_WING_FAR_20261101 ── MANAGER SIDE of the far-wing search
# ============================================================================
# Drives the REAL VetManager (stub chain / executor, real VetRepo on a temp
# DB, real find_far_wing on a pandas instrument list) through the 28 Oct
# shape: the armed ±30 band has no PE at or under Rs 3, the instrument list
# does. Asserted:
#   * band has a wing          → the search is NEVER called (unchanged path)
#   * band has none, list has  → entry opens WITH the far wing, wing BOUGHT
#                                 before the short is sold, log names the
#                                 strike and how far out it was
#   * band has none, list none → entry SKIPPED, no orders (never bare)
#   * collaborator lies        → wrong side / ITM / dear / other expiry /
#                                 the main itself are all rejected
#   * collaborator raises      → skip, never bare
#   * wing_search_max_strikes 0, BUY mode, no fn → search never runs
#   * the 28 Oct FLIP: CE short with a band wing → FLIP to PE → far wing
#   * OFF ≡ ORIGINAL: without a search fn (and whenever the band has a wing)
#     the patched manager makes the same orders and the same position as the
#     pre-patch file (read from its .bak — skipped, not failed, if absent)
# Timestamps are REAL epochs (28 Oct 2026, IST) — real-clock rule.
#
# Runs standalone:  PYTHONPATH=backend python3 app/engine/vet/test_vet_wing_far.py
# ============================================================================

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

import pandas as pd                                          # noqa: E402

import app.engine.vet.vet_manager as VM                      # noqa: E402
from app.engine.vet.vet_common import VetRepo                # noqa: E402
from app.engine.vet.vet_live_core import FLIP                # noqa: E402
from app.engine.vet.vet_manager import VetManager            # noqa: E402
from app.engine.vet.vet_wing_search import find_far_wing     # noqa: E402

FENCE = "VET_WING_FAR_20261101"
F = 0
OK = [0]


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    if cond:
        OK[0] += 1
    else:
        F += 1


IST = timezone(timedelta(hours=5, minutes=30))
TS_1200 = int(datetime(2026, 10, 28, 12, 0, tzinfo=IST).timestamp())
TS_1205 = TS_1200 + 300
SPOT = 24000.0
EXP = "2026-11-03"
BAND_LO, BAND_HI = 22500.0, 25500.0          # ±30 strikes around ATM 24000


def premium(k, side):
    """CE side: monotone OTM decay that reaches Rs 3 INSIDE the band."""
    d = k - SPOT
    if d <= 0:
        return round(180 + abs(d) * 0.9, 2)
    return round(max(0.05, 180 - d * 0.12), 2)


# explicit PE premium table beyond/inside the band (the 28 Oct shape)
PE_TABLE = {}
for k in range(18000, 30050, 50):
    d = SPOT - k
    if d <= 0:
        PE_TABLE[k] = round(180 + abs(d) * 0.9, 2)
    elif k >= BAND_LO:                 # inside the band: never <= 3
        PE_TABLE[k] = round(max(4.5, 180 - d * 0.115), 2)
    else:                              # beyond: 22450 → 4.2 … 22300 → 2.85 …
        PE_TABLE[k] = round(max(0.3, 4.2 - (BAND_LO - 50 - k) / 50 * 0.45), 2)


def px(k, side):
    return PE_TABLE[k] if side == "PE" else premium(k, side)


def sym(k, side):
    return f"NIFTY26N03{k}{side}"


def band_chain(side, ts):
    """What the armed universe prices: ±30 strikes only."""
    return [{"tradingsymbol": sym(k, side), "token": 50000 + k + (0 if side == "CE" else 1),
             "strike": float(k), "expiry": EXP, "instrument_type": side,
             "ltp": px(k, side)}
            for k in range(int(BAND_LO), int(BAND_HI) + 50, 50)]


def instruments():
    rows = []
    for k in range(18000, 30050, 50):
        for side in ("CE", "PE"):
            rows.append({"name": "NIFTY", "segment": "NFO-OPT",
                         "instrument_type": side, "expiry": pd.Timestamp(EXP).date(),
                         "strike": float(k), "tradingsymbol": sym(k, side),
                         "instrument_token": 50000 + k + (0 if side == "CE" else 1)})
        # noise: another expiry and another underlying at the same strikes
        rows.append({"name": "NIFTY", "segment": "NFO-OPT", "instrument_type": "PE",
                     "expiry": pd.Timestamp("2026-11-10").date(), "strike": float(k),
                     "tradingsymbol": f"NIFTY26N10{k}PE", "instrument_token": 90000 + k})
    return pd.DataFrame(rows)


IDF = instruments()
QUOTE_CALLS = []


def quote_stub(keys):
    QUOTE_CALLS.append(list(keys))
    out = {}
    for key in keys:
        s = key.split(":", 1)[1]
        side = s[-2:]
        k = int(s[len("NIFTY26N03"):-2]) if s.startswith("NIFTY26N03") else None
        if k is None:
            continue
        out[key] = {"last_price": px(k, side), "volume": 1000}
    return out


SEARCH_CALLS = []


def loop_like_search(side, ts, main, expiry, cap, max_strikes):
    """What the selection loop's far_wing does, minus the socket."""
    SEARCH_CALLS.append((side, ts, main.get("tradingsymbol"), cap, max_strikes))
    priced = {c["tradingsymbol"] for c in band_chain(side, ts)}
    row, info = find_far_wing(instruments_df=IDF, quote_batch=quote_stub,
                              side=side, main_strike=float(main["strike"]),
                              main_symbol=main["tradingsymbol"],
                              expiry_iso=str(expiry)[:10], cap=cap,
                              max_strikes=max_strikes, exclude=priced,
                              band=(BAND_LO, BAND_HI), step=50)
    if row is None:
        return None
    row["_far"]["subscribed"] = True
    return row


class Exec:
    def __init__(self):
        self.calls = []

    def place_buy(self, symbol, token, qty):
        self.calls.append(("BUY", symbol, qty))
        return f"OB-{len(self.calls)}"

    def place_sell_entry(self, symbol, token, qty):
        self.calls.append(("SELL", symbol, qty))
        return f"OS-{len(self.calls)}"

    def place_market_sell(self, symbol, qty):
        self.calls.append(("CLOSE_LONG", symbol, qty))
        return f"OC-{len(self.calls)}"

    def place_buy_exit(self, symbol, qty, reason):
        self.calls.append(("CLOSE_SHORT", symbol, qty))
        return f"OX-{len(self.calls)}"

    def get_order_fill(self, oid):
        return {"status": "COMPLETE", "avg_price": 0.0, "filled_qty": 0,
                "found": True}


LOG = []
_real_log = VM.write_audit_log
VM.write_audit_log = lambda m: (LOG.append(m), _real_log(m))[1]

TMP = tempfile.mkdtemp(prefix="vet_wing_far_")
_n = [0]


def mk(extra=None, *, search=loop_like_search, live=True, chain=band_chain,
       cls=VetManager):
    _n[0] += 1
    cfg = {"leg_action": "SELL", "hedge_enabled": True, "hedge_max_premium": 3.0,
           "atm_offset": -1, "eod_square": False,
           "quantity": {"lots": 10, "lot_size": 65}, "_spot": SPOT}
    cfg.update(extra or {})
    repo = VetRepo(os.path.join(TMP, f"m{_n[0]}.db"))
    repo.ensure_schema()
    ex = Exec() if live else None
    m = cls(cfg, repo=repo, chain_fn=chain, quote_fn=lambda s: 100.0,
            executor=ex, mode="LIVE" if live else "PAPER")
    m._sleep = lambda s: None
    if search is not None and hasattr(m, "wing_search_fn"):
        m.wing_search_fn = search
    return m, ex


def reset():
    LOG.clear()
    SEARCH_CALLS.clear()
    QUOTE_CALLS.clear()


# ── 1. CE: the band HAS a wing → search never called ───────────────────
print("── 1. band has a real wing → unchanged path, no search ──")
reset()
m, ex = mk()
p = m.open_position("CE", ts=TS_1200, bar_ts=TS_1200, condition=-1)
chk("CE opens with an in-band wing", p and p["wing"] is not None)
chk("the far search was NOT called", SEARCH_CALLS == [], SEARCH_CALLS)
chk("no REST quote spent", QUOTE_CALLS == [])
chk("in-band wing is the dearest <= 3",
    p and p["wing"]["entry_price"] <= 3.0
    and p["wing"]["entry_price"] == max(c["ltp"] for c in band_chain("CE", 0)
                                        if c["ltp"] <= 3.0))

# ── 2. PE: band has none, list has → far wing ──────────────────────────
print("\n── 2. 28 Oct shape: band has no PE <= 3, the list does ──")
reset()
m, ex = mk()
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
want = max((k for k in PE_TABLE if k < BAND_LO and PE_TABLE[k] <= 3.0))
chk("entry OPENS (was: SKIPPED every 5 min)", p is not None)
chk(f"wing is the DEAREST real PE <= 3 beyond the band ({sym(want, 'PE')})",
    p and p["wing"]["tradingsymbol"] == sym(want, "PE"),
    p and p["wing"]["tradingsymbol"])
chk("wing price <= cap and > 0", p and 0 < p["wing"]["entry_price"] <= 3.0)
chk("wing is OTM of the short",
    p and float(p["wing"]["strike"]) < float(p["main"]["strike"]))
chk("wing BOUGHT before the short is SOLD",
    [c[0] for c in ex.calls] == ["BUY", "SELL"]
    and ex.calls[0][1] == sym(want, "PE"), ex.calls)
chk("wing and short carry the same qty", ex.calls[0][2] == ex.calls[1][2] == 650)
far_line = next((ln for ln in LOG if ln.startswith("[VET][WING_FAR] PE: no real wing")), "")
beyond = int((BAND_LO - want) / 50)
chk("log names the strike found", sym(want, "PE") in far_line, far_line)
chk(f"log says how far out ({beyond} strikes beyond the edge)",
    f"{beyond} strikes ({beyond * 50}" in far_line, far_line)
chk("log gives the distance from the main", "strikes from the main" in far_line)
chk("search reached at most the configured strikes",
    SEARCH_CALLS and SEARCH_CALLS[0][4] == 90, SEARCH_CALLS)
chk("one REST quote call for the whole candidate set", len(QUOTE_CALLS) == 1,
    len(QUOTE_CALLS))
chk("band contracts were excluded from the quote",
    not any(sym(k, "PE") in "".join(QUOTE_CALLS[0])
            for k in range(int(BAND_LO), int(BAND_HI) + 50, 50)))
chk("persisted WING row has the far contract",
    any(r["leg_role"] == "WING" and r["tradingsymbol"] == sym(want, "PE")
        for r in m.repo.open_legs("LIVE")), m.repo.open_legs("LIVE"))
chk("state exposes last_far_wing", (m.last_far_wing or {}).get("tradingsymbol")
    == sym(want, "PE"))
chk("no 'entry SKIPPED' line", not any("entry SKIPPED" in ln for ln in LOG))

print("\n── 2b. PAPER mode takes the same far wing ──")
reset()
m, _ = mk(live=False)
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("paper opens with the far wing", p and p["wing"]["tradingsymbol"] == sym(want, "PE"))

# ── 3. list has nothing either → skip, never bare ──────────────────────
print("\n── 3. nothing <= cap anywhere → skipped, never bare ──")
reset()
m, ex = mk({"hedge_max_premium": 0.2})
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("no position", p is None and m.pos is None)
chk("no orders at all", ex.calls == [], ex.calls)
skip = next((ln for ln in LOG if "entry SKIPPED" in ln), "")
chk("skip line says the far search found none", "Far search out to 90" in skip, skip)
chk("skip line keeps the greppable prefix",
    skip.startswith("[VET][MGR] no REAL wing <= 0.2 on PE"), skip)

print("\n── 3b. reach too short → none found, skipped ──")
reset()
m2, ex2 = mk({"wing_search_max_strikes": 30})     # 22550: still inside the fat band
p2 = m2.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("reach 30 from the main does not get past the band → skip", p2 is None and ex2.calls == [])

# ── 4. collaborator lies → rejected ────────────────────────────────────
print("\n── 4. invalid rows from the collaborator are rejected ──")
BASE = {"tradingsymbol": sym(22300, "PE"), "token": 1, "strike": 22300.0,
        "expiry": EXP, "instrument_type": "PE", "ltp": 2.5, "_far": {}}
cases = {
    "wrong side": dict(BASE, instrument_type="CE"),
    "dearer than the cap": dict(BASE, ltp=3.5),
    "zero price": dict(BASE, ltp=0),
    "ITM of the short (strike above a PE main)": dict(BASE, strike=24500.0,
                                                      tradingsymbol=sym(24500, "PE")),
    "another expiry": dict(BASE, expiry="2026-11-10"),
    "the main itself": None,      # filled below
    "no symbol": dict(BASE, tradingsymbol=None),
}
for name, row in cases.items():
    reset()

    def lying(side, ts, main, expiry, cap, mx, _row=row, _name=name):
        return (dict(BASE, tradingsymbol=main["tradingsymbol"],
                     strike=main["strike"]) if _name == "the main itself"
                else dict(_row))
    m, ex = mk(search=lying)
    p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
    chk(f"{name} → rejected, no orders",
        p is None and ex.calls == []
        and any("[VET][WING_FAR] PE: rejected" in ln for ln in LOG), (ex.calls, LOG[-2:]))

print("\n── 5. collaborator raises → skip ──")
reset()


def boom(*a):
    raise RuntimeError("kite down")


m, ex = mk(search=boom)
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("raise → no position, no orders", p is None and ex.calls == [])
chk("raise is logged", any("search raised" in ln for ln in LOG))

# ── 6. switches ────────────────────────────────────────────────────────
print("\n── 6. off switches ──")
reset()
m, ex = mk({"wing_search_max_strikes": 0})
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("max_strikes 0 → search never called, skip as before",
    p is None and SEARCH_CALLS == [] and ex.calls == [])
chk("…with the ORIGINAL skip sentence",
    any("This is the documented live/backtest divergence." in ln for ln in LOG))
reset()
m, ex = mk(search=None)
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("no wing_search_fn (harness default) → skip as before", p is None and ex.calls == [])
reset()
m, ex = mk({"leg_action": "BUY"})
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("BUY mode → no wing, no search", p and p["wing"] is None and SEARCH_CALLS == [])
reset()
m, ex = mk({"hedge_enabled": False})
p = m.open_position("PE", ts=TS_1205, bar_ts=TS_1205, condition=1)
chk("hedge disabled → no search (naked by explicit Settings choice)",
    p and p["wing"] is None and SEARCH_CALLS == [])
chk("max_strikes garbage → default 90",
    mk({"wing_search_max_strikes": "x"})[0].wing_search_max_strikes == 90)
chk("max_strikes None → default 90",
    mk({"wing_search_max_strikes": None})[0].wing_search_max_strikes == 90)

# ── 7. the 28 Oct FLIP ─────────────────────────────────────────────────
print("\n── 7. 28 Oct: CE short → 12:00 FLIP → PE re-entry with a far wing ──")
reset()
m, ex = mk()
m.open_position("CE", ts=TS_1200 - 3600, bar_ts=TS_1200 - 3600, condition=-1)
res = m.on_decision({"action": FLIP, "side": "PE", "reason": "FLIP",
                     "bar_ts": TS_1200, "condition": 1}, ts=TS_1200)
chk("FLIP closes the CE and OPENS the PE", res and res["closed"] and res["opened"]
    and m.pos and m.pos["side"] == "PE", res)
chk("PE wing came from the far search", m.pos and m.pos["wing"]["tradingsymbol"]
    == sym(want, "PE"))
seq = [c[0] for c in ex.calls]
chk("order sequence: BUY wing, SELL CE | CLOSE_SHORT CE, CLOSE_LONG wing | BUY far wing, SELL PE",
    seq == ["BUY", "SELL", "CLOSE_SHORT", "CLOSE_LONG", "BUY", "SELL"], seq)

# ── 8. OFF ≡ ORIGINAL ──────────────────────────────────────────────────
print("\n── 8. OFF ≡ ORIGINAL (pre-patch manager from its .bak) ──")
bak = os.path.join(HERE, f"vet_manager.py.bak-{FENCE}")
if not os.path.exists(bak):
    print(f"  SKIP  {os.path.basename(bak)} not present (run via the apply script)")
else:
    ldr = SourceFileLoader("vet_manager_orig", bak)
    spec = importlib.util.spec_from_loader("vet_manager_orig", ldr)
    ORIG = importlib.util.module_from_spec(spec)
    ldr.exec_module(ORIG)
    chk("the .bak really is the pre-patch file", FENCE not in open(bak).read())
    for label, extra, side, search in (
            ("SELL, band wing (CE), search wired", {}, "CE", loop_like_search),
            ("SELL, no band wing (PE), no search fn", {}, "PE", None),
            ("SELL, no band wing (PE), max_strikes 0", {"wing_search_max_strikes": 0}, "PE",
             loop_like_search),
            ("BUY (PE)", {"leg_action": "BUY"}, "PE", loop_like_search)):
        reset()
        a, ea = mk(extra, search=search)
        b, eb = mk(extra, search=None, cls=ORIG.VetManager)
        pa = a.open_position(side, ts=TS_1205, bar_ts=TS_1205, condition=1)
        pb = b.open_position(side, ts=TS_1205, bar_ts=TS_1205, condition=1)

        def shape(p):
            if not p:
                return None
            return (p["side"], p["main"]["tradingsymbol"], p["main"]["entry_price"],
                    (p["wing"] or {}).get("tradingsymbol"),
                    (p["wing"] or {}).get("entry_price"))
        chk(f"{label}: same orders and same position",
            ea.calls == eb.calls and shape(pa) == shape(pb), (ea.calls, eb.calls))

print(f"\n{OK[0]} passed, {F} failed")
print("ALL VET WING-FAR MANAGER CHECKS PASSED" if F == 0 else f"{F} FAILURES")
sys.exit(1 if F else 0)
