# backend/app/backtest/vet/test_vet_wing_liquidity.py
#
# ── WING SOURCING REGRESSION (two scenarios) ─────────────────────────────
#
# PART 1 — SPORADIC PRINTS. Cheap far strikes print only once every 7
#   minutes, which is how deep-OTM options really trade. The first hedge
#   implementation demanded an exact-minute print, so on most bars it
#   concluded "no wing exists" and — under the fail-closed rule — silently
#   DELETED the entry. On the live NIFTY corpus that removed 56% of all
#   trades, 28-69% varying by year, turning a 7.42 net/DD run into 2.41 and
#   breaking all-years-positive. Verified to FAIL against the pre-fix logic.
#
# PART 2 — NARROW LISTED BAND. Nothing on the chain is ever cheap enough, so
#   no real wing exists at any minute and only the SYNTHETIC path can serve.
#   This is what IC's ic_synth_wing exists for; VET reuses those primitives
#   verbatim (_synth_leg_at / _synth_mark_at) rather than inventing a second
#   convention. Asserts the wing is modelled, the row is FLAGGED synthetic
#   with synth_kind="hedge", model-attributed P&L is reported, and disabling
#   synth reverts to the old destructive fail-closed behaviour.
#
# Runs standalone:  python3 test_vet_wing_liquidity.py

import os
import sqlite3
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')))
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest

IST = 19800
DDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER,PRIMARY KEY(instrument_token,ts));
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""
DAYS = [date(2026, 6, d) for d in (1, 2, 3, 4, 5, 8, 9)]
EXPS = ("2026-06-02", "2026-06-09", "2026-06-16")
SPORADIC = 7
F = 0


def ds(d):
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def chk(name, cond, detail=""):
    global F
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else f"  {detail}"))
    F += 0 if cond else 1


def build(db, band, floor_px, sporadic):
    """band = max |strike-spot| listed; floor_px = cheapest premium allowed;
    sporadic = print cheap strikes only every Nth minute (0 = always)."""
    if os.path.exists(db):
        os.remove(db)
    c = sqlite3.connect(db)
    c.executescript(DDL)
    rows, tok = [], {}

    def T(s):
        tok.setdefault(s, 100000 + len(tok))
        return tok[s]

    for d in DAYS:
        base = ds(d)
        for mi in range(375):
            ts = base + (9 * 60 + 15 + mi) * 60
            sp = 24000 + 300 * DAYS.index(d) + 250 * mi / 374.0
            rows.append((T("SPOT"), ts, "NIFTY", "NIFTY_SPOT", "SPOT", 0.0, "",
                         sp - 2, sp + 3, sp - 3, sp, 0, 0))
            for exp in EXPS:
                if d.isoformat() > exp:
                    continue
                dte = (date.fromisoformat(exp) - d).days
                tag = exp.replace("-", "")[2:]
                for k in range(23000, 26100, 50):
                    dist = abs(k - sp)
                    if dist > band:
                        continue
                    for side in ("CE", "PE"):
                        intr = max(sp - k, 0) if side == "CE" else max(k - sp, 0)
                        decay = max(0.02 if sporadic else 0.35,
                                    1.0 - dist / 900.0)
                        px = round(max(floor_px, intr + (25 + 8 * dte) * decay), 2)
                        cheap = px <= 5.0
                        if sporadic and cheap and (mi % sporadic) != 0:
                            continue
                        sym = f"NIFTY{tag}{k}{side}"
                        rows.append((T(sym), ts, "NIFTY", sym, side, float(k), exp,
                                     px, px + 0.05, max(0.05, px - 0.05), px,
                                     10 if cheap else 500, 0))
    c.executemany(
        "INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return len(rows)


def run(db, **kw):
    return run_vet_backtest(
        db_path=db, strategy_id="VET_V1", underlying="NIFTY",
        date_from=DAYS[3], date_to=DAYS[-1],
        config_override=dict({"warmup_sessions": 3, "strike_selection": "atm",
                              "leg_action": "SELL", "eod_square": True}, **kw))


# ══ PART 1 — sporadic cheap prints, real wings DO exist ══════════════════
DB1 = "/tmp/vet_wing_sporadic.db"
print(f"PART 1 corpus: {build(DB1, 1500, 0.15, SPORADIC)} rows "
      f"(cheap wings print 1 minute in {SPORADIC})")
naked = run(DB1)
hedged = run(DB1, hedge_enabled=True, hedge_max_premium=5)
d1 = hedged["summary"]["diag_vet"]
print(f"  naked {len(naked['trades'])} | hedged {len(hedged['trades'])} | "
      f"real {d1['hedge_real']} synth {d1['hedge_synth']} "
      f"stale {d1['hedge_stale_fills']} noHedge {d1['no_hedge_entries']}")
chk("sporadic prints do NOT delete entries (>=95% retained)",
    len(hedged["trades"]) >= 0.95 * len(naked["trades"]),
    f"{len(hedged['trades'])} vs {len(naked['trades'])}")
chk("every hedged trade carries a wing",
    d1["hedge_exits"] == len(hedged["trades"]))
chk("REAL wings are preferred when they exist", d1["hedge_real"] > 0)

# ══ PART 2 — narrow band, NO real wing can ever be cheap enough ══════════
DB2 = "/tmp/vet_wing_narrow.db"
print(f"\nPART 2 corpus: {build(DB2, 300, 12.0, 0)} rows "
      f"(cheapest listed option ~₹12, cap ₹5)")
naked2 = run(DB2)
synth = run(DB2, hedge_enabled=True, hedge_max_premium=5)
off = run(DB2, hedge_enabled=True, hedge_max_premium=5,
          hedge_synth_enabled=False)
d2 = synth["summary"]["diag_vet"]
o2 = off["summary"]["diag_vet"]
print(f"  naked {len(naked2['trades'])} | synth-on {len(synth['trades'])} | "
      f"synth-off {len(off['trades'])} | real {d2['hedge_real']} "
      f"synth {d2['hedge_synth']} fail {d2['hedge_synth_fail']} "
      f"modelPnL {d2['hedge_synth_pnl_gross']:,.0f}")
chk("no REAL wing exists under the cap", d2["hedge_real"] == 0, d2["hedge_real"])
chk("SYNTHETIC wing serves every entry",
    d2["hedge_synth"] > 0 and len(synth["trades"]) == len(naked2["trades"]),
    f"synth={d2['hedge_synth']} {len(synth['trades'])} vs {len(naked2['trades'])}")
chk("synth OFF -> fail-closed, entries deleted (the old destructive path)",
    len(off["trades"]) == 0 and o2["no_hedge_entries"] > 0,
    f"trades={len(off['trades'])}")
chk("synthetic rows FLAGGED synthetic with synth_kind='hedge'",
    all(t.synthetic and t.synth_kind == "hedge" for t in synth["trades"]),
    [(t.synthetic, t.synth_kind) for t in synth["trades"][:3]])
chk("model-attributed P&L reported (IC honesty convention)",
    d2["hedge_synth_exits"] > 0 and "hedge_synth_pnl_gross" in d2)
chk("naked rows are NOT flagged synthetic",
    all(not t.synthetic for t in naked2["trades"]))
chk("a cap below any modellable premium still fails closed",
    len(run(DB2, hedge_enabled=True, hedge_max_premium=0.001)["trades"]) == 0)

print("\n" + ("ALL WING CHECKS PASSED (real + synthetic)" if F == 0
              else f"{F} FAILURES"))
sys.exit(1 if F else 0)
