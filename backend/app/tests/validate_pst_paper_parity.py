# backend/app/tests/validate_pst_paper_parity.py
#
# ── PST PAPER↔BACKTEST TRADE PARITY ── (Phase 1, Delivery 2 — D27 proof)
#
# The paper managers are event-driven mirrors of the backtest engines. This
# harness PROVES the mirror: randomized full trading days (multi-strike
# chains, random-walk spot, signals at 3m boundaries) are executed twice —
#
#   REFERENCE: the actual backtest path — run_day_short / run_day_hedge
#              with select_strike, exactly as a backtest of that day runs.
#   PAPER    : the manager, fed minute-by-minute through a chain view with
#              a NOW watermark (future candles unreadable — no lookahead
#              possible even by bug), signals arriving at their minute.
#
# PASS = every closed leg matches trade-for-trade: symbol, qty, entry stamp
# and price, exit ts / price / reason, ambiguity flag. Any mismatch is a
# code bug in the manager, by construction.
#
# Run anywhere:  python3 validate_pst_paper_parity.py [N_days]

from __future__ import annotations

import random
import sys
import tempfile
from datetime import date, datetime

sys.path.insert(0, ".")
try:
    from app.engine.pst.pst_sell_paper_manager import PSTSellPaperManager
    from app.engine.pst.pst_hedge_paper_manager import PSTHedgePaperManager
    from app.engine.pst.pst_common import PSTRepo
    from app.backtest.pst.pst_sell_engine import run_day_short
    from app.backtest.pst.pst_hedge_engine import run_day_hedge
    from app.backtest.ic.ic_v1_engine import select_strike
except ImportError:
    from pst_sell_paper_manager import PSTSellPaperManager
    from pst_hedge_paper_manager import PSTHedgePaperManager
    from pst_common import PSTRepo
    from pst_sell_engine import run_day_short
    from pst_hedge_engine import run_day_hedge
    from ic_v1_engine import select_strike

IST = 5 * 3600 + 30 * 60
LEGS = [{"id": "L1", "lots": 2, "sl_pct": 15, "spot_tg_points": 20},
        {"id": "L2", "lots": 1, "sl_pct": 15, "spot_tg_points": 50}]


def _day_start(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


# ── synthetic day: spot + a small weekly chain, occasional candle gaps ──
def synth_day(seed: int):
    rng = random.Random(seed)
    ds = _day_start(date(2026, 7, 10))
    open_ts = ds + (9 * 60 + 15) * 60
    n = 375
    spot0 = 25000 + rng.uniform(-200, 200)
    spot, px = [], spot0
    for i in range(n):
        ts = open_ts + 60 * i
        o = px
        c = px + rng.uniform(-10, 10)
        spot.append({"ts": ts, "open": o, "high": max(o, c) + rng.uniform(0, 5),
                     "low": min(o, c) - rng.uniform(0, 5), "close": c})
        px = c
    chain = {}
    meta = {}
    for side in ("CE", "PE"):
        for k, base in enumerate((90, 120, 145, 175)):     # some above cap 150
            sym = f"NIFTY26JUL{24800 + 100*k}{side}"
            meta[sym] = {"strike": 24800 + 100 * k, "expiry": "2026-07-16", "side": side}
            p = base + rng.uniform(-5, 5)
            cds = {}
            for i in range(n):
                ts = open_ts + 60 * i
                if rng.random() < 0.02:                     # data gaps
                    continue
                o = p
                c = max(1.0, p + rng.uniform(-6, 6))
                cds[ts] = {"ts": ts, "open": o, "high": max(o, c) + rng.uniform(0, 3),
                           "low": max(0.5, min(o, c) - rng.uniform(0, 3)), "close": c}
                p = c
            chain[sym] = cds
    # signals at some 3m boundaries (signal layer already proven — inject)
    sigs = []
    for i in range(3, n - 20, 3):
        if rng.random() < 0.08:
            ts = open_ts + 60 * i
            sigs.append({"ts": ts, "side": rng.choice(["CE", "PE"]),
                         "spot": next(c["close"] for c in spot if c["ts"] == ts),
                         "levels_crossed": ["pp"], "stale": False})
    eod_ts = ds + (15 * 60 + 25) * 60
    return ds, spot, chain, meta, sigs, eod_ts


class ChainView:
    """NOW-watermarked chain: candles later than the fed minute simply do
    not exist for the manager — structural no-lookahead."""
    def __init__(self, chain, meta):
        self._chain, self._meta, self.now = chain, meta, -1

    def candle(self, sym, ts):
        if ts > self.now:
            return None
        return self._chain.get(sym, {}).get(ts)

    def symbols(self, side):
        return [s for s, m in self._meta.items() if m["side"] == side]

    def meta(self, sym):
        return self._meta.get(sym)


def reference(kind, ds, spot, chain, meta, sigs, eod_ts):
    """The actual backtest path for this day."""
    spot_by_ts = {c["ts"]: c for c in spot}

    def _pick(side, ts):
        cands = [(s, chain[s][ts - 60]["close"]) for s in chain
                 if meta[s]["side"] == side and (ts - 60) in chain[s]]
        p = select_strike(cands, 150)
        if p is None:
            return None
        sym = p[0]
        if ts not in chain[sym]:
            return None
        return sym

    if kind == "SELL":
        def select_option(side, ts):
            sym = _pick(side, ts)
            if sym is None:
                return None
            return {"symbol": sym, "entry_price": chain[sym][ts]["close"],
                    "candles": [c for t, c in sorted(chain[sym].items()) if t >= ts + 60]}
        day = run_day_short(sigs, LEGS, select_option, spot, eod_ts)
    else:
        def select_pair(side, ts):
            ssym = _pick(side, ts)
            other = "PE" if side == "CE" else "CE"
            hsym = _pick(other, ts) if ssym else None
            if ssym is None or hsym is None:
                return None
            return {"sig_symbol": ssym, "sig_entry": chain[ssym][ts]["close"],
                    "sig_candles": [c for t, c in sorted(chain[ssym].items()) if t >= ts + 60],
                    "held_symbol": hsym, "held_side": other,
                    "held_entry": chain[hsym][ts]["close"],
                    "held_candles": [c for t, c in sorted(chain[hsym].items()) if t >= ts + 60]}
        day = run_day_hedge(sigs, LEGS, select_pair, spot, eod_ts)
    return day["trades"]


def paper(kind, ds, spot, chain, meta, sigs, eod_ts, db):
    repo = PSTRepo(db)
    cfg = {"trade_execution_mode": "PAPER", "premium_max": 150, "legs": LEGS,
           "side_mode": "BOTH", "max_trades_per_day": 0, "exit_time": "15:25"}
    mgr = (PSTSellPaperManager if kind == "SELL" else PSTHedgePaperManager)(cfg, repo)
    view = ChainView(chain, meta)
    sig_by_ts = {}
    for s in sigs:
        sig_by_ts.setdefault(s["ts"], []).append(s)
    spot_by_ts = {c["ts"]: c for c in spot}
    for c in spot:
        ts = c["ts"]
        view.now = ts
        mgr.on_minute(ts, spot_by_ts.get(ts), view)
        for s in sig_by_ts.get(ts, []):
            mgr.on_signal(s, view)
    mgr.force_eod(eod_ts)          # scheduler safety net (harmless if flat)
    table = "pst_sell_trades" if kind == "SELL" else "pst_hedge_trades"
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} ORDER BY entry_ts, leg_id")]


def key_ref(kind, t):
    return (t["tradingsymbol"], t["leg"], int(t["lots"]) * 65,
            int(t["entry_ts"]) + 60, round(float(t["entry_price"]), 2),
            int(t["exit_ts"]), round(float(t["exit_price"]), 2),
            t["exit_reason"], bool(t["ambiguous_fill"]))


def key_paper(t):
    return (t["tradingsymbol"], t["leg_id"], int(t["qty"]),
            int(t["entry_ts"]), round(float(t["entry_price"]), 2),
            int(t["exit_ts"]), round(float(t["exit_price"]), 2),
            t["exit_reason"], bool(t["ambiguous"]))


def run(n_days: int) -> int:
    fails = 0
    stats = {"SELL": {}, "HEDGE": {}}
    for seed in range(n_days):
        ds, spot, chain, meta, sigs, eod_ts = synth_day(seed)
        for kind in ("SELL", "HEDGE"):
            ref = sorted(key_ref(kind, t) for t in
                         reference(kind, ds, spot, chain, meta, sigs, eod_ts))
            with tempfile.NamedTemporaryFile(suffix=".db") as f:
                pap = sorted(key_paper(t) for t in
                             paper(kind, ds, spot, chain, meta, sigs, eod_ts, f.name))
            if ref != pap:
                fails += 1
                print(f"seed {seed} {kind}: FAIL ref={len(ref)} paper={len(pap)}")
                for a, b in list(zip(ref, pap))[:3]:
                    if a != b:
                        print(f"   ref  {a}\n   pap  {b}")
                for x in (set(ref) - set(pap)):
                    print(f"   only-ref  {x}"); break
                for x in (set(pap) - set(ref)):
                    print(f"   only-pap  {x}"); break
            else:
                for t in ref:
                    stats[kind][t[7]] = stats[kind].get(t[7], 0) + 1
    total_legs = sum(sum(v.values()) for v in stats.values())
    print(f"\nPAPER\u2194BACKTEST PARITY {'100%' if fails == 0 else 'FAILED'} \u2014 "
          f"{n_days} days \u00d7 2 strategies, {total_legs} legs matched")
    print(f"  SELL  exit mix: {stats['SELL']}")
    print(f"  HEDGE exit mix: {stats['HEDGE']}")
    return fails


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    sys.exit(1 if run(n) else 0)