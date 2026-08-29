#!/usr/bin/env python3
# validate_pst_paper_parity_filters.py
#
# ── PST_LIVE_FILTERS_20260828 PARITY PROOF ──
#
# The stock harness (validate_pst_paper_parity.py) proves paper == backtest
# with the filters OFF. This extension proves it with them ON, which is the
# case that actually matters now: the sealed configs run with a level
# allowlist and a confirm wait, and the confirm wait RETIMES both selection
# and fill in the live manager. A timing error there would not show up in
# the filters-off run at all.
#
# Reuses the stock harness's synthetic days, ChainView, NOW watermark and
# comparison keys verbatim — only the config differs, so any mismatch is
# attributable to the filters alone.
#
# SCOPE NOTE: skip_expiry_day is a RUNNER-level filter (whole-day skip), not
# an engine-level one, so it has no counterpart inside run_day_short and
# cannot be parity-tested here. It is covered by the manager unit tests
# (including its fail-closed path when the expiry calendar is unavailable).

import sys
import tempfile

sys.path.insert(0, ".")

# The stock harness exposes synth_day / ChainView / key_ref / key_paper /
# LEGS at module level — reuse them verbatim so only the config differs.
import app.tests.validate_pst_paper_parity as H          # noqa: E402
from app.backtest.pst.pst_sell_engine import run_day_short    # noqa: E402
from app.backtest.pst.pst_hedge_engine import run_day_hedge   # noqa: E402
from app.backtest.ic.ic_v1_engine import select_strike        # noqa: E402
from app.engine.pst.pst_common import PSTRepo                 # noqa: E402
from app.engine.pst.pst_sell_paper_manager import PSTSellPaperManager    # noqa: E402
from app.engine.pst.pst_hedge_paper_manager import PSTHedgePaperManager  # noqa: E402

# Sealed configs (PDF, 28-Aug-2026). skip_expiry_day omitted — see scope note.
SEALED = {
    "SELL":  {"allowed_levels": ["PP", "S1", "S3", "R3"], "confirm_minutes": 4},
    "HEDGE": {"allowed_levels": ["PP", "R3"], "confirm_minutes": 3},
}


def reference(kind, ds, spot, chain, meta, sigs, eod_ts, filt):
    """Backtest path WITH filters — mirrors the stock harness's reference()
    exactly, plus the two engine-level filter kwargs."""
    # copied verbatim from the stock harness's reference() so selection is
    # provably identical; only the filter kwargs below differ
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

    kw = {"allowed_levels": frozenset(filt["allowed_levels"]),
          "confirm_minutes": filt["confirm_minutes"]}
    if kind == "SELL":
        def select_option(side, ts):
            sym = _pick(side, ts)
            if sym is None:
                return None
            return {"symbol": sym, "entry_price": chain[sym][ts]["close"],
                    "candles": [c for t, c in sorted(chain[sym].items()) if t >= ts + 60]}
        day = run_day_short(sigs, LEGS_, select_option, spot, eod_ts, **kw)
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
        day = run_day_hedge(sigs, LEGS_, select_pair, spot, eod_ts, **kw)
    return day["trades"]


def paper(kind, ds, spot, chain, meta, sigs, eod_ts, db, filt):
    repo = PSTRepo(db)
    cfg = {"trade_execution_mode": "PAPER", "premium_max": 150, "legs": LEGS_,
           "side_mode": "BOTH", "max_trades_per_day": 0, "exit_time": "15:25",
           "allowed_levels": filt["allowed_levels"],
           "confirm_minutes": filt["confirm_minutes"]}
    mgr = (PSTSellPaperManager if kind == "SELL" else PSTHedgePaperManager)(cfg, repo)
    view = H.ChainView(chain, meta)
    sig_by_ts = {}
    for s in sigs:
        sig_by_ts.setdefault(s["ts"], []).append(s)
    spot_by_ts = {c["ts"]: c for c in spot}
    for c in spot:
        ts = c["ts"]
        view.now = ts
        mgr.on_minute(ts, spot_by_ts.get(ts), view)
        for s in sig_by_ts.get(ts + 60, []):
            mgr.on_signal(s, view)
    mgr.force_eod(eod_ts)
    table = "pst_sell_trades" if kind == "SELL" else "pst_hedge_trades"
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} ORDER BY entry_ts, leg_id")]


LEGS_ = H.LEGS


# The stock harness fabricates levels_crossed as lowercase 'pp'. Production
# emits UPPERCASE (pst_indicators.PIVOT_NAMES / traditional_pivots), verified
# directly against crosses(). Normalising here keeps the fixture faithful to
# the real signal stream — without it every signal is filtered out and the
# parity run passes VACUOUSLY on zero trades.
def _realistic_levels(sigs, rng_names=("PP", "S1", "S3", "R3", "R1", "S2", "R2")):
    import random
    out = []
    for i, s in enumerate(sigs):
        s = dict(s)
        r = random.Random(hash((s["ts"], s["side"])) & 0xFFFF)
        # mix allowed and blocked levels so the filter has real work to do,
        # and occasionally a multi-level (gap-bar) cross
        picks = [r.choice(rng_names)]
        if r.random() < 0.25:
            picks.append(r.choice(rng_names))
        s["levels_crossed"] = sorted(set(picks))
        out.append(s)
    return out


def run(n_days):
    fails = 0
    stats = {"SELL": {}, "HEDGE": {}}
    for seed in range(n_days):
        ds, spot, chain, meta, sigs, eod_ts = H.synth_day(seed)
        sigs = _realistic_levels(sigs)
        for kind in ("SELL", "HEDGE"):
            filt = SEALED[kind]
            ref = sorted(H.key_ref(kind, t) for t in
                         reference(kind, ds, spot, chain, meta, sigs, eod_ts, filt))
            with tempfile.NamedTemporaryFile(suffix=".db") as f:
                pap = sorted(H.key_paper(t) for t in
                             paper(kind, ds, spot, chain, meta, sigs, eod_ts,
                                   f.name, filt))
            if ref != pap:
                fails += 1
                print(f"seed {seed} {kind}: FAIL ref={len(ref)} paper={len(pap)}")
                for a, b in list(zip(ref, pap))[:3]:
                    if a != b:
                        print(f"   ref  {a}\n   pap  {b}")
                for x in sorted(set(ref) - set(pap))[:2]:
                    print(f"   only-ref  {x}")
                for x in sorted(set(pap) - set(ref))[:2]:
                    print(f"   only-pap  {x}")
            else:
                for t in ref:
                    stats[kind][t[7]] = stats[kind].get(t[7], 0) + 1
    total = sum(sum(v.values()) for v in stats.values())
    ok = "100%" if fails == 0 else "FAILED"
    print(f"\nPAPER\u2194BACKTEST PARITY (SEALED FILTERS ON) {ok} \u2014 "
          f"{n_days} days \u00d7 2 strategies, {total} legs matched")
    print(f"  SELL  levels={SEALED['SELL']['allowed_levels']} "
          f"confirm={SEALED['SELL']['confirm_minutes']}m  exit mix: {stats['SELL']}")
    print(f"  HEDGE levels={SEALED['HEDGE']['allowed_levels']} "
          f"confirm={SEALED['HEDGE']['confirm_minutes']}m  exit mix: {stats['HEDGE']}")
    return fails


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    sys.exit(1 if run(n) else 0)
