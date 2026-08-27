# backend/app/backtest/vet/test_vet_leg_action.py
#
# ── LEG_ACTION tests ── SELL must express the SAME signal with the opposite
# contract: up-trend -> SHORT PE, down-trend -> SHORT CE. Asserts the signal
# chain is untouched (identical trade count and timestamps), the option type
# inverts on every trade, gross sign follows the short convention, SL/TP
# levels flip to the correct side of entry, and the short charges model is
# actually used (STT moves to the entry leg).
#
# Runs standalone:  python3 test_vet_leg_action.py
import os, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import date
try:
    from app.backtest.vet.backtest_vet_runner import run_vet_backtest
except ImportError:
    from backtest_vet_runner import run_vet_backtest
IST = 19800
DB = "/tmp/vet_leg_test.db"

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
buy=go(); sell=go(leg_action="SELL")
bt,st=buy["trades"],sell["trades"]
chk("default is BUY", buy["config"]["leg_action"]=="BUY")
chk("SELL config echoes", sell["config"]["leg_action"]=="SELL")
chk("same number of trades (signal chain untouched)", len(bt)==len(st), f"{len(bt)} vs {len(st)}")
chk("entry/exit timestamps identical", [ (t.entry_ts,t.exit_ts) for t in bt]==[(t.entry_ts,t.exit_ts) for t in st])
chk("every BUY leg is direction BUY", all(t.direction=="BUY" for t in bt))
chk("every SELL leg is direction SELL", all(t.direction=="SELL" for t in st))
inv={"CE":"PE","PE":"CE"}
chk("SELL uses the OPPOSITE option type on every trade",
    [inv[t.instrument_type] for t in bt]==[t.instrument_type for t in st],
    list(zip([t.instrument_type for t in bt],[t.instrument_type for t in st]))[:5])
# sign: a short leg profits when premium falls
bad=[t for t in st if (t.exit_price<t.entry_price) != (t.pnl>0)]
chk("SHORT gross is positive iff premium FELL", not bad,
    [(t.tradingsymbol,t.entry_price,t.exit_price,t.pnl) for t in bad][:3])
bad2=[t for t in bt if (t.exit_price>t.entry_price) != (t.pnl>0)]
chk("LONG gross is positive iff premium ROSE", not bad2)
# SL levels invert
b_sl=go(sl_pct=20); s_sl=go(leg_action="SELL", sl_pct=20)
bs=[t for t in b_sl["trades"] if t.sl is not None][:1]
ss=[t for t in s_sl["trades"] if t.sl is not None][:1]
chk("LONG SL sits BELOW entry", bs and bs[0].sl < bs[0].entry_price, bs and (bs[0].entry_price,bs[0].sl))
chk("SHORT SL sits ABOVE entry", ss and ss[0].sl > ss[0].entry_price, ss and (ss[0].entry_price,ss[0].sl))
b_tp=go(tp_pct=20); s_tp=go(leg_action="SELL", tp_pct=20)
bt2=[t for t in b_tp["trades"] if t.tp is not None][:1]
st2=[t for t in s_tp["trades"] if t.tp is not None][:1]
chk("LONG TP sits ABOVE entry", bt2 and bt2[0].tp > bt2[0].entry_price)
chk("SHORT TP sits BELOW entry", st2 and st2[0].tp < st2[0].entry_price)
# charges model: STT on entry leg for shorts -> charges differ
chk("SHORT charges differ from LONG (STT moves to the entry leg)",
    abs(sum(t.charges for t in st) - sum(t.charges for t in bt)) > 0.01,
    f"{sum(t.charges for t in st):.2f} vs {sum(t.charges for t in bt):.2f}")
print(f"\n  BUY net {buy['summary']['net_pnl']:,.0f} | SELL net {sell['summary']['net_pnl']:,.0f}")
print("\n"+("ALL LEG_ACTION CHECKS PASSED" if F==0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
