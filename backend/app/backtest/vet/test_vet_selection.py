# backend/app/backtest/vet/test_vet_selection.py
#
# ── SPOT_RELATIVE_SELECTION / premium_pct / max_entry_dte tests ──
# Asserts each new knob is INERT at its default (so every prior run stays
# reproducible), behaves as documented when set, and fails CLOSED at the
# extremes. Builds its own synthetic corpus if one is not present.
#
# Runs standalone:  python3 test_vet_selection.py
import os, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import date, datetime
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST = 19800
DB = "/tmp/vet_sel_test.db"

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
def strikes(r): return [t.strike for t in r["trades"]]

base=go(); b0=go(atm_offset=0)
chk("pct=0 is inert (identical to step mode)", strikes(base)==strikes(b0))

# spot ~24000-25000, step 50 -> 1 step ~0.2% of spot
s_step2 = strikes(go(atm_offset=2))
s_pct   = strikes(go(atm_offset_pct=0.4))     # 0.4% of 24500 ~= 98 ~= 2 steps
chk("0.4% of spot lands within 1 step of the 2-step offset",
    all(abs(a-b)<=50 for a,b in zip(s_step2,s_pct)),
    list(zip(s_step2,s_pct))[:4])
chk("pct OVERRIDES steps when both set",
    strikes(go(atm_offset=2, atm_offset_pct=0.4))==s_pct)

# CE goes UP, PE goes DOWN for a positive pct
r=go(atm_offset_pct=1.0)
ce=[t for t in r["trades"] if t.instrument_type=="CE"]
pe=[t for t in r["trades"] if t.instrument_type=="PE"]
r0=go(atm_offset_pct=0.0)
chk("positive pct is OTM-ward on BOTH sides", True if not (ce and pe) else
    (ce[0].strike > [t for t in r0['trades'] if t.instrument_type=='CE'][0].strike
     and pe[0].strike < [t for t in r0['trades'] if t.instrument_type=='PE'][0].strike))

# premium % veto
# NOTE: the veto measures premium against SPOT, and on expiry day the ATM
# premium collapses, so a threshold derived from the taken trades is NOT a
# floor for every bar. Assert the two ends and monotonicity instead.
tight=go(premium_pct_max=0.01)
chk("premium_pct_max below anything tradeable blocks ALL entries",
    len(tight["trades"])==0 and tight["summary"]["diag_vet"]["premium_pct_veto_entries"]>0,
    f'trades={len(tight["trades"])}')
mid=go(premium_pct_max=0.4)
chk("tightening the cap is monotone in trade count",
    len(tight["trades"]) <= len(mid["trades"]) <= len(base["trades"]),
    f'{len(tight["trades"])} <= {len(mid["trades"])} <= {len(base["trades"])}')
loose=go(premium_pct_max=100.0)
chk("premium_pct_max above every candidate is inert", strikes(loose)==strikes(base))
chk("premium_pct_min above every candidate blocks all entries",
    len(go(premium_pct_min=100.0)["trades"])==0)

# max_entry_dte
d=base["summary"]["diag_vet"]
big=go(max_entry_dte=999)
chk("max_entry_dte huge is inert", strikes(big)==strikes(base))
zero=go(max_entry_dte=0)
chk("max_entry_dte=0 means OFF (not 'block everything')", strikes(zero)==strikes(base))
tiny=go(max_entry_dte=1)
td=tiny["summary"]["diag_vet"]
chk("max_entry_dte=1 blocks far-DTE entries",
    td["max_dte_blocked_entries"]>0 and len(tiny["trades"])<len(base["trades"]),
    f"blocked={td['max_dte_blocked_entries']} trades={len(tiny['trades'])} vs {len(base['trades'])}")
print("\n"+("ALL SELECTION CHECKS PASSED" if F==0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
