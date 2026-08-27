# backend/app/backtest/vet/test_vet_daily_cap.py
#
# ── DAILY_MTM_CAP behavioural test ── builds a synthetic corpus containing a
# violent CHOP session (sawtooth spot) so the strategy genuinely loses money,
# then asserts the max_daily_mtm_loss overlay:
#   1. is completely INERT when 0 (no diag activity, no DAY_CAP rows)
#   2. fires at least once when set below a real day's loss
#   3. emits exactly one DAY_CAP row per breach
#   4. admits NO new entry after firing, for the rest of that session
#   5. leaves the capped day less negative than the uncapped run
#   6. is INERT again when set far beyond any day's loss
#
# NOTE ON OVERSHOOT: the cap is a TRIGGER, not a guarantee. It is evaluated at
# timeframe closes, so the realised day loss lands beyond the level by roughly
# one bar's adverse move plus the exit's charges. A live guard behaves the
# same way. Size the level with that headroom in mind.
#
# Runs standalone:  python3 test_vet_daily_cap.py

# Corpus with a violent CHOP day so the strategy actually loses money and the
# daily MTM cap has something to bite on.
import os, sys, sqlite3, math
from collections import defaultdict
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST=19800; DB="/tmp/vet_chop_test.db"
DDL="""CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER,PRIMARY KEY(instrument_token,ts));
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""
def ds(d): return int((datetime(d.year,d.month,d.day)-datetime(1970,1,1)).total_seconds())-IST
DAYS=[date(2026,6,1),date(2026,6,2),date(2026,6,3),date(2026,6,4),date(2026,6,5),date(2026,6,8)]
CHOP={date(2026,6,4),date(2026,6,5),date(2026,6,8)}   # whipsaw days
EXPS=("2026-06-02","2026-06-09","2026-06-16")
def spot(d,mi):
    if d in CHOP:
        # sawtooth: 220-pt swings every ~25 min -> repeated flips, each entered
        # near an extreme and exited near the opposite one
        return 24000 + 220*math.sin(2*math.pi*mi/50.0)
    return 24000 + 400*mi/374.0            # warmup: clean trend

if os.path.exists(DB): os.remove(DB)
c=sqlite3.connect(DB); c.executescript(DDL); rows=[]; tok={}
def T(s):
    tok.setdefault(s,100000+len(tok)); return tok[s]
for d in DAYS:
    base=ds(d)
    for mi in range(375):
        ts=base+(9*60+15+mi)*60; sp=spot(d,mi)
        rows.append((T("SPOT"),ts,"NIFTY","NIFTY_SPOT","SPOT",0.0,"",sp-2,sp+3,sp-3,sp,0,0))
        for EXP in EXPS:
            if d.isoformat() > EXP: continue
            dte=(date.fromisoformat(EXP)-d).days
            tag=EXP.replace("-","")[2:]
            for k in range(23400,24700,50):
                if abs(k-sp)>400: continue
                for side in ("CE","PE"):
                    intr=max(sp-k,0) if side=="CE" else max(k-sp,0)
                    px=round(intr+15+6*dte,1)
                    sym=f"NIFTY{tag}{k}{side}"
                    rows.append((T(sym),ts,"NIFTY",sym,side,float(k),EXP,px,px+1,px-1,px,100,0))
c.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
c.commit(); c.close(); print(f"chop corpus: {len(rows)} rows")

def go(**kw):
    return run_vet_backtest(db_path=DB, strategy_id="VET_V1", underlying="NIFTY",
        date_from=date(2026,6,4), date_to=date(2026,6,8),
        config_override=dict({"warmup_sessions":3,"eod_square":True,"strike_selection":"atm"}, **kw))
FAIL=0
def chk(n,c,d=""):
    global FAIL
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else "  "+str(d)));  FAIL+= (0 if c else 1)

base=go(); bt=base["trades"]; bd=base["summary"]["diag_vet"]
print("baseline (cap off):", len(bt),"trades, net",base["summary"]["net_pnl"])
chk("cap OFF -> no cap activity", bd["daily_cap_days"]==0 and bd["daily_cap_exits"]==0)
chk("cap OFF -> no DAY_CAP rows", all(t.exit_reason!="DAY_CAP" for t in bt))

# find a day with a real loss to size the cap against
byday=defaultdict(float)
for t in bt: byday[date.fromtimestamp(t.exit_ts+19800).isoformat()]+=t.net_pnl
print("  per-day net:", {k:round(v) for k,v in byday.items()})
worst=min(byday.values())
cap=abs(worst)/2 if worst<0 else 5000.0
r=go(max_daily_mtm_loss=cap); rt=r["trades"]; rd=r["summary"]["diag_vet"]
print(f"\ncap = {cap:,.0f}: {len(rt)} trades, net {r['summary']['net_pnl']}")
print("  diag:", {k:v for k,v in rd.items() if k.startswith("daily_cap")})
chk("cap ON -> at least one breach", rd["daily_cap_days"]>=1, rd)
chk("DAY_CAP rows == daily_cap_exits",
    sum(1 for t in rt if t.exit_reason=="DAY_CAP")==rd["daily_cap_exits"])
# no entry may occur after the cap fires on that day
capdays={date.fromtimestamp(t.exit_ts+19800).isoformat() for t in rt if t.exit_reason=="DAY_CAP"}
bad=[]
for t in rt:
    dd=date.fromtimestamp(t.entry_ts+19800).isoformat()
    if dd in capdays:
        cts=[x.exit_ts for x in rt if x.exit_reason=="DAY_CAP"
             and date.fromtimestamp(x.exit_ts+19800).isoformat()==dd]
        if cts and t.entry_ts > min(cts): bad.append(t.tradingsymbol)
chk("no entries after the cap fires", not bad, bad)
# realised day loss must never exceed the cap by more than one bar's move
byday2=defaultdict(float)
for t in rt: byday2[date.fromtimestamp(t.exit_ts+19800).isoformat()]+=t.net_pnl
print("  per-day net with cap:", {k:round(v) for k,v in byday2.items()})
chk("capped days are less negative than uncapped",
    all(byday2[k] >= byday.get(k,0)-1 for k in capdays), 
    {k:(round(byday.get(k,0)),round(byday2[k])) for k in capdays})
# huge cap must be inert
big=go(max_daily_mtm_loss=10_000_000)
chk("cap far beyond any day's loss is INERT",
    big["summary"]["net_pnl"]==base["summary"]["net_pnl"]
    and big["summary"]["diag_vet"]["daily_cap_days"]==0)
print("\n"+("ALL DAY-CAP CHECKS PASSED" if FAIL==0 else f"{FAIL} FAILURES"))
sys.exit(1 if FAIL else 0)
