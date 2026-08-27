# backend/app/backtest/vet/test_vet_roll_coverage.py
#
# ── ROLL_COVERAGE regression ── corpus where the CALENDAR-next weekly is absent
# (exactly the 2026-06-16 case: 06-16 present, 06-23 MISSING, 06-30 present).
import os, sqlite3, sys
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
IST=19800; DB="/tmp/vet_roll.db"
DDL="""CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,
tradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,
close REAL,volume INTEGER,oi INTEGER,PRIMARY KEY(instrument_token,ts));
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);
CREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""
def ds(d): return int((datetime(d.year,d.month,d.day)-datetime(1970,1,1)).total_seconds())-IST
DAYS=[date(2026,6,d) for d in (9,10,11,12,15,16,17,18,19)]   # 16th = Tue expiry
EXP_NEAR="2026-06-16"; EXP_SKIP="2026-06-23"; EXP_FAR="2026-06-30"
def build():
    if os.path.exists(DB): os.remove(DB)
    c=sqlite3.connect(DB); c.executescript(DDL); rows=[]; tok={}
    def T(s):
        tok.setdefault(s,100000+len(tok)); return tok[s]
    for d in DAYS:
        base=ds(d)
        for mi in range(375):
            ts=base+(9*60+15+mi)*60
            spot=24000+ (DAYS.index(d)*120) + 300*mi/374.0     # steady uptrend
            rows.append((T("SPOT"),ts,"NIFTY","NIFTY_SPOT","SPOT",0.0,"",spot-2,spot+3,spot-3,spot,0,0))
            for exp in (EXP_NEAR,EXP_SKIP,EXP_FAR):
                if exp==EXP_SKIP:            # <-- the gap: never written
                    continue
                if d.isoformat()>exp: continue
                dte=(date.fromisoformat(exp)-d).days
                for k in range(23400,25100,50):
                    if abs(k-spot)>500: continue
                    tag=exp.replace("-","")[2:]
                    for side in ("CE","PE"):
                        intr=max(spot-k,0) if side=="CE" else max(k-spot,0)
                        px=round(intr+20+10*dte,1)
                        sym=f"NIFTY{tag}{k}{side}"
                        rows.append((T(sym),ts,"NIFTY",sym,side,float(k),exp,px,px+1,px-1,px,100,0))
    c.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
    c.commit(); c.close(); print(f"corpus: {len(rows)} rows; expiries {EXP_NEAR}, {EXP_FAR} (SKIPPED: {EXP_SKIP})")
build()
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
r=run_vet_backtest(db_path=DB,strategy_id="VET_V1",underlying="NIFTY",
    date_from=DAYS[4],date_to=DAYS[-1],
    config_override={"warmup_sessions":4,"strike_selection":"atm"})
d=r["summary"]["diag_vet"]
tr=r["trades"]
print("\nroll_entries:",d["roll_entries"]," roll_exits:",d["roll_exits"],
      " rolls_no_next_expiry:",d["rolls_no_next_expiry"])
print("probes:",d["roll_expiry_probes"]," gap>week:",d["roll_expiry_gap_gt_week"])
for t in tr:
    print(f"  {t.condition:<11} {t.tradingsymbol:<20} exp {t.expiry} -> {t.exit_reason}")
ok = d["roll_entries"]>=1 and any(t.expiry==EXP_FAR for t in tr) and d["rolls_no_next_expiry"]==0
print("\nRESULT:", "PASS — rolled over the corpus gap onto", EXP_FAR if ok else "FAIL")
sys.exit(0 if ok else 1)
