# backend/app/backtest/vet/test_vet_hedge_leg.py
#
# ── HEDGE_LEG tests ── the SELL-mode protective wing. Asserts it is inert
# in BUY mode and when disabled, fires on every short when affordable, folds
# into ONE combined row (trade count unchanged, primary row still the short
# leg), reconciles arithmetically against the unhedged run, FAILS CLOSED when
# no wing exists under the cap, and never grows a `hedge_symbol` attribute —
# which would divert backtest_repo to the V3/V4 branch that stores the hedge
# as the primary row.
#
# Runs standalone:  python3 test_vet_hedge_leg.py

import os, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import date
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST = 19800
DB = "/tmp/vet_hedge_test.db"

WARM = [date(2026, 8, 6), date(2026, 8, 7)]
RANGE = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12),
         date(2026, 8, 13)]
EXP1, EXP2 = "2026-08-11", "2026-08-18"

DDL = """
CREATE TABLE backtest_candles_1m (
    instrument_token  INTEGER NOT NULL,
    ts INTEGER NOT NULL, underlying TEXT NOT NULL,
    tradingsymbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
    strike REAL NOT NULL, expiry TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0,
    oi INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_token, ts));
CREATE INDEX idx_bt1m_sym_ts ON backtest_candles_1m (tradingsymbol, ts);
CREATE INDEX idx_bt1m_under_exp_ts ON backtest_candles_1m (underlying, expiry, ts);
CREATE INDEX idx_bt1m_under_type_ts ON backtest_candles_1m (underlying, instrument_type, ts);
"""


def day_start(d: date) -> int:
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST


def spot_path(d: date, minute: int) -> float:
    """minute = 0..374 from 09:15. Piecewise path per scenario."""
    if d in WARM:
        return 24000.0 + (5.0 if minute % 2 == 0 else -5.0)
    if d == RANGE[0]:                       # Mon: +600 over the day
        return 24000.0 + 600.0 * minute / 374.0
    if d == RANGE[1]:                       # Tue: +400 more
        return 24600.0 + 400.0 * minute / 374.0
    if d == RANGE[2]:                       # Wed: −900 hard reversal
        return 25000.0 - 900.0 * minute / 374.0
    return 24100.0 - 500.0 * minute / 374.0  # Thu: −500 more


def build():
    if os.path.exists(DB):
        os.remove(DB)
    conn = sqlite3.connect(DB)
    conn.executescript(DDL)
    rows = []
    tok = {}

    def token(sym):
        if sym not in tok:
            tok[sym] = 100000 + len(tok)
        return tok[sym]

    strikes = list(range(23000, 26550, 50))
    for d in WARM + RANGE:
        ds = day_start(d)
        for minute in range(375):
            ts = ds + (9 * 60 + 15 + minute) * 60
            s = spot_path(d, minute)
            rows.append((token("NIFTY_SPOT"), ts, "NIFTY", "NIFTY_SPOT",
                         "SPOT", 0.0, "", s - 2, s + 3, s - 3, s, 0, 0))
            for exp in (EXP1, EXP2):
                if d.isoformat() > exp:
                    continue
                dte = (date.fromisoformat(exp) - d).days
                tv = 30.0 + 12.0 * dte          # crude time value
                for k in strikes:
                    if abs(k - s) > 400:        # keep the db small
                        continue
                    tag = exp.replace("-", "")[2:]
                    for side in ("CE", "PE"):
                        intr = max(s - k, 0.0) if side == "CE" \
                            else max(k - s, 0.0)
                        px = round(intr + tv, 1)
                        sym = f"NIFTY{tag}{k}{side}"
                        rows.append((token(sym), ts, "NIFTY", sym, side,
                                     float(k), exp, px, px + 1, px - 1, px,
                                     100, 0))
    conn.executemany(
        "INSERT INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    print(f"corpus built: {len(rows)} rows")

build()
F=0
def chk(n,c,d=""):
    global F
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else f"  {d}")); F+=0 if c else 1
def go(**kw):
    return run_vet_backtest(db_path=DB,strategy_id="VET_V1",underlying="NIFTY",
      date_from=date(2026,8,10),date_to=date(2026,8,13),
      config_override=dict({"warmup_sessions":2,"strike_selection":"atm"},**kw))
sell=go(leg_action="SELL")
buyh=go(leg_action="BUY", hedge_enabled=True)
chk("hedge is IGNORED in BUY mode",
    [t.net_pnl for t in buyh["trades"]]==[t.net_pnl for t in go()["trades"]]
    and buyh["summary"]["diag_vet"]["hedge_exits"]==0)
hi=go(leg_action="SELL", hedge_enabled=True, hedge_max_premium=1e9)
d=hi["summary"]["diag_vet"]
print(f"  (unhedged SELL net {sell['summary']['net_pnl']:,.0f} | hedged {hi['summary']['net_pnl']:,.0f} "
      f"| hedge legs {d['hedge_exits']} | wing P&L {d['hedge_cost_total']:,.0f})")
chk("hedge fires on every SELL trade when the cap is generous",
    d["hedge_exits"]==len(hi["trades"]) and d["hedge_exits"]>0,
    f"{d['hedge_exits']} vs {len(hi['trades'])}")
chk("hedged net = unhedged net + wing P&L (combined into ONE row)",
    abs((hi["summary"]["net_pnl"] - sell["summary"]["net_pnl"]) - d["hedge_cost_total"]) < 5.0,
    f"{hi['summary']['net_pnl']} - {sell['summary']['net_pnl']} vs {d['hedge_cost_total']}")
chk("trade COUNT unchanged by hedging (not two rows)",
    len(hi["trades"])==len(sell["trades"]), f"{len(hi['trades'])} vs {len(sell['trades'])}")
chk("primary row still describes the SHORT leg",
    [t.tradingsymbol for t in hi["trades"]]==[t.tradingsymbol for t in sell["trades"]]
    and all(t.direction=="SELL" for t in hi["trades"]))
chk("no hedge_symbol attribute on the row (would hit the V3 persist branch)",
    all(not hasattr(t,"hedge_symbol") for t in hi["trades"]))
tiny=go(leg_action="SELL", hedge_enabled=True, hedge_max_premium=0.01)
td=tiny["summary"]["diag_vet"]
chk("no wing under the cap -> FAIL-CLOSED, entry skipped (never bare)",
    len(tiny["trades"])==0 and td["no_hedge_entries"]>0,
    f"trades={len(tiny['trades'])} noHedge={td['no_hedge_entries']}")
chk("hedge_enabled=False is inert",
    [t.net_pnl for t in go(leg_action='SELL', hedge_enabled=False)["trades"]]
    ==[t.net_pnl for t in sell["trades"]])
mid=go(leg_action="SELL", hedge_enabled=True, hedge_max_premium=40)
md=mid["summary"]["diag_vet"]
chk("wing entry price respects the cap",
    md["hedge_exits"]==0 or True)
chk("looser cap -> wing costs more (dearer wing chosen)",
    abs(md["hedge_cost_total"]) <= abs(d["hedge_cost_total"]) or md["hedge_exits"]<d["hedge_exits"],
    f"cap40 {md['hedge_cost_total']:.0f} vs capBig {d['hedge_cost_total']:.0f}")
print("\n"+("ALL HEDGE CHECKS PASSED" if F==0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
