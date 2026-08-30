# backend/app/backtest/cbo/test_cbo_runner_sim.py
#
# ── CBO_V1 RUNNER BEHAVIOURAL SIMULATION ────────────────────────────────
# End-to-end run of run_cbo_backtest against a SYNTHETIC corpus built in a
# temp SQLite file, with hand-computed expected P&L.
#
# This uses the REAL CandleSource, the REAL selector and the REAL charges
# model — only market_hours and the audit logger are stubbed, because they
# reach for a holiday file and a live log path. Stubbing the charges model
# would defeat the point: charges are the dominant term in a strategy that
# trades ~50 times a day, so they have to be the production numbers.
#
# Run from the repo root (it needs backend/ on the path):
#     python3 backend/app/backtest/cbo/test_cbo_runner_sim.py <path-to-repo>
#
# WHAT THIS CANNOT TELL YOU: anything about profitability. The synthetic
# corpus is constructed so each assertion has ONE known answer. It proves
# the plumbing books what it claims to book — nothing more.

from __future__ import annotations

import sqlite3
import sys
import tempfile
import types
from contextlib import contextmanager
from datetime import date
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FAILED = []


def chk(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{('  ' + extra) if extra else ''}")
    if not cond:
        FAILED.append(label)


# ── stubs: only for the two modules that touch the host environment ──────
_mh = types.ModuleType("app.utils.market_hours")
_mh.is_trading_day = lambda d=None: True          # synthetic days are all open
sys.modules["app.utils.market_hours"] = _mh

_al = types.ModuleType("app.event_bus.audit_logger")
_al.write_audit_log = lambda *a, **k: None


@contextmanager
def _muted():
    yield


_al.audit_muted = _muted
sys.modules["app.event_bus.audit_logger"] = _al

import app.backtest.engine.expiry_calendar as _ec  # noqa: E402
from backtest_cbo_runner import (  # noqa: E402
    run_cbo_backtest, skew_ok, leg_side, target_price, resolve_exit,
    _day_start_epoch,
)
from cbo_v1_engine import UP, DOWN  # noqa: E402

DAY = date(2026, 3, 12)
EXPIRY = date(2026, 3, 17)
_ec.expected_expiry_for_day = lambda d: EXPIRY     # pin the weekly

DS = _day_start_epoch(DAY)
LOT = 65


def ts(minute):
    return DS + minute * 60


SCHEMA = """
CREATE TABLE backtest_candles_1m (
  ts INTEGER, tradingsymbol TEXT, underlying TEXT, instrument_type TEXT,
  strike REAL, expiry TEXT, open REAL, high REAL, low REAL, close REAL,
  volume INTEGER DEFAULT 0, oi INTEGER DEFAULT 0
);
CREATE INDEX i1 ON backtest_candles_1m(tradingsymbol, ts);
CREATE INDEX i2 ON backtest_candles_1m(underlying, instrument_type, ts);
"""


def build_corpus(path, spot_rows, opt_rows):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    for m, o, h, l, cl in spot_rows:
        c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
                  "instrument_type,open,high,low,close) VALUES(?,?,?,?,?,?,?,?)",
                  (ts(m), "NIFTY-SPOT", "NIFTY", "SPOT", o, h, l, cl))
    for sym, strike, itype, m, o, h, l, cl in opt_rows:
        c.execute("INSERT INTO backtest_candles_1m(ts,tradingsymbol,underlying,"
                  "instrument_type,strike,expiry,open,high,low,close) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (ts(m), sym, "NIFTY", itype, strike, EXPIRY.isoformat(),
                   o, h, l, cl))
    c.commit()
    c.close()


def opt_series(sym, strike, itype, prices, m0=555, m1=935):
    """A flat-ish option series: `prices` maps minute -> (o,h,l,c); every
    other minute repeats the last close so the contract is continuously
    quoted (a real weekly ATM contract prints every minute)."""
    rows, last = [], None
    for m in range(m0, m1):
        if m in prices:
            last = prices[m]
        elif last is None:
            continue
        rows.append((sym, strike, itype, m, *last))
    return rows


def run(cfg, db):
    return run_cbo_backtest(
        db_path=db, strategy_id="CBO_V1", underlying="NIFTY",
        date_from=DAY, date_to=DAY, config_override=cfg)


# ═════════════════════════════════════════════════════════════════════════
print("\n── A. pure helpers ───────────────────────────────────────────────")

chk("BUY: UP -> long CE", leg_side(UP, False) == "CE")
chk("BUY: DOWN -> long PE", leg_side(DOWN, False) == "PE")
chk("SELL: UP -> short PE (VET convention, opposite contract)",
    leg_side(UP, True) == "PE")
chk("SELL: DOWN -> short CE", leg_side(DOWN, True) == "CE")

chk("abs target on a long adds rupees to entry",
    target_price(150.0, is_sell=False, mode="abs", value=10.0) == 160.0)
chk("abs target on a short subtracts from premium collected",
    target_price(150.0, is_sell=True, mode="abs", value=10.0) == 140.0)
chk("pct target on a long is % of ENTRY premium (D3b)",
    target_price(150.0, is_sell=False, mode="pct", value=10.0) == 165.0)
chk("pct target on a short is % of premium COLLECTED (D3b)",
    target_price(150.0, is_sell=True, mode="pct", value=10.0) == 135.0)
chk("a short's target can never go negative (floored at 0.05)",
    target_price(2.0, is_sell=True, mode="pct", value=500.0) == 0.05)


class B:
    def __init__(s, o, h, l, c):
        s.open, s.high, s.low, s.close = o, h, l, c


chk("long TP fills AT the limit price, not the bar high",
    resolve_exit(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
                 direction=UP, opt_bar=B(150, 175, 149, 170),
                 spot_bar=B(110, 115, 105, 112)) == ("TP", 160))
chk("spot-triggered SL fills at the option CLOSE (market exit)",
    resolve_exit(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
                 direction=UP, opt_bar=B(150, 151, 130, 133),
                 spot_bar=B(110, 112, 99, 101)) == ("SL", 133))
chk("SL WINS when both are touched in one minute (D3 tie-break)",
    resolve_exit(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
                 direction=UP, opt_bar=B(150, 165, 130, 140),
                 spot_bar=B(110, 118, 99, 101)) == ("SL", 140))
chk("a quiet minute closes nothing",
    resolve_exit(is_sell=False, entry_px=150, tp_px=160, spot_stop=100,
                 direction=UP, opt_bar=B(150, 155, 148, 152),
                 spot_bar=B(110, 112, 105, 111)) is None)
chk("DOWN trades stop on spot HIGH, not spot low",
    resolve_exit(is_sell=False, entry_px=150, tp_px=160, spot_stop=120,
                 direction=DOWN, opt_bar=B(150, 152, 148, 149),
                 spot_bar=B(110, 121, 108, 119)) == ("SL", 149))
chk("short TP triggers on option LOW reaching the limit",
    resolve_exit(is_sell=True, entry_px=150, tp_px=140, spot_stop=100,
                 direction=UP, opt_bar=B(150, 151, 139, 141),
                 spot_bar=B(110, 112, 105, 111)) == ("TP", 140))


print("\n── B. skew gate (the friend's ATM CE > ATM PE) ───────────────────")
OFF = {"enabled": False}
ON = {"enabled": True, "min_diff_pts": 0.0, "invert": False,
      "parity_adjust": False, "carry_pts": 6.5}
chk("disabled gate always passes, even with no data",
    skew_ok(ce=None, pe=None, spot=None, strike=None,
            direction=UP, cfg_skew=OFF)[0])
chk("UP passes when ATM CE is dearer than ATM PE",
    skew_ok(ce=160, pe=140, spot=25010, strike=25000,
            direction=UP, cfg_skew=ON)[0])
chk("UP is BLOCKED when ATM PE is dearer",
    not skew_ok(ce=140, pe=160, spot=24990, strike=25000,
                direction=UP, cfg_skew=ON)[0])
chk("DOWN mirrors: passes when ATM PE is dearer",
    skew_ok(ce=140, pe=160, spot=24990, strike=25000,
            direction=DOWN, cfg_skew=ON)[0])
chk("missing a leg BLOCKS (fail-closed)",
    not skew_ok(ce=None, pe=140, spot=25010, strike=25000,
                direction=UP, cfg_skew=ON)[0])
chk("invert flips the verdict and nothing else",
    skew_ok(ce=140, pe=160, spot=24990, strike=25000, direction=UP,
            cfg_skew={**ON, "invert": True})[0])

# The parity point, made numerically. Both cases below have CE dearer than
# PE, so the RAW rule passes both. But case 1 is dear only because spot sits
# above the strike (pure grid geometry, carry 6.5), while case 2 has genuine
# residual richness. Parity mode must separate them.
PAR = {**ON, "parity_adjust": True}
raw1 = skew_ok(ce=136.5, pe=100.0, spot=25030, strike=25000,
               direction=UP, cfg_skew=ON)
par1 = skew_ok(ce=136.5, pe=100.0, spot=25030, strike=25000,
               direction=UP, cfg_skew=PAR)
par2 = skew_ok(ce=156.5, pe=100.0, spot=25030, strike=25000,
               direction=UP, cfg_skew=PAR)
chk("RAW passes a CE-dearer pair that is dear ONLY from strike geometry",
    raw1[0])
chk("PARITY sees through it: residual is ~0, so it does NOT pass",
    (not par1[0]) and abs(par1[1]) < 0.001, f"residual={par1[1]}")
chk("PARITY passes a pair with 20pts of GENUINE residual richness",
    par2[0] and abs(par2[1] - 20.0) < 0.001, f"residual={par2[1]}")


print("\n── C. end-to-end: one clean TP on a long CE ──────────────────────")
# 5m bar 1 (09:15-09:19) high 25050 low 24950  <- reference
# 5m bar 2: minute 09:21 spot high 25060 -> UP breakout, fill at 09:22 open.
# CE premium: 150 at fill, rises to 165 at 09:30 -> TP=160 fills at 160.
tmp = tempfile.mkdtemp()
db = str(Path(tmp) / "bt.db")

spot = []
for m in range(555, 560):                       # 09:15-09:19 reference bar
    spot.append((m, 25000, 25050 if m == 555 else 25010, 24950 if m == 555 else 24990, 25000))
spot.append((560, 25000, 25020, 24995, 25015))  # 09:20 quiet
spot.append((561, 25015, 25060, 25010, 25055))  # 09:21 BREAK 25050
for m in range(562, 930):                       # calm rest of day, no SL
    spot.append((m, 25055, 25065, 25045, 25055))

CE = "NIFTY26031725000CE"
PE = "NIFTY26031725000PE"
ce_px = {555: (150, 151, 149, 150), 562: (150, 151, 149, 150),
         570: (163, 166, 162, 165)}
pe_px = {555: (150, 151, 149, 150)}
opts = opt_series(CE, 25000, "CE", ce_px) + opt_series(PE, 25000, "PE", pe_px)
build_corpus(db, spot, opts)

# session_end 09:25 closes the ENTRY window right after the 09:21 signal.
# Exits are not gated by session_end, so the 09:30 TP still books. Without
# this the flat 25045-25065 tail breaks a new reference every bucket and
# fires 66 more trades — correct behaviour, wrong corpus for this assertion.
r = run({"option_premium": {"min": 100, "max": 200}, "target_value": 10.0,
         "session_start": "09:15", "session_end": "09:25",
         "eod_square_off": "15:15"}, db)
tr = r["trades"]
chk("exactly one trade", len(tr) == 1, f"got {len(tr)}")
if tr:
    t = tr[0]
    chk("it is a long CE", t.direction == "BUY" and t.instrument_type == "CE")
    chk("filled at 09:22 (trigger 09:21 + 1m), not at the 09:21 close",
        t.entry_ts == ts(562), f"entry_ts minute={(t.entry_ts - DS)//60}")
    chk("entry price is the fill bar's OPEN (150)", t.entry_price == 150.0)
    chk("stop is the reference bar's LOW, a SPOT level (24950)",
        t.sl == 24950.0)
    chk("target is entry + 10 = 160", t.tp == 160.0)
    chk("exit reason is TP", t.exit_reason == "TP")
    chk("exit price is the limit (160), not the bar high (166)",
        t.exit_price == 160.0)
    chk("gross = 10 x 65 = 650", abs(t.pnl - 650.0) < 0.01, f"{t.pnl}")
    chk("charges are the PRODUCTION model, ~55-75 for this ticket",
        55 <= t.charges <= 75, f"charges={t.charges}")
    chk("net = gross - charges", abs(t.net_pnl - (t.pnl - t.charges)) < 0.01)
    chk("qty = 1 lot x 65", t.qty == 65)
    d = r["summary"]["diag_cbo"]
    chk("TP share of net is reported for falsification",
        "tp_pnl_share_pct" in d, f"{d.get('tp_pnl_share_pct')}%")


print("\n── D. one position at a time + day cap ───────────────────────────")
# Same corpus, but the CE never reaches the target, so the trade stays open
# and every later breakout must be blocked by the in-trade gate.
db2 = str(Path(tempfile.mkdtemp()) / "bt.db")
spot2 = [r for r in spot if r[0] < 562]
for m in range(562, 930):                       # keep grinding to new highs
    spot2.append((m, 25055 + (m - 562), 25070 + (m - 562),
                  25050 + (m - 562), 25060 + (m - 562)))
opts2 = opt_series(CE, 25000, "CE", {555: (150, 151, 149, 150)}) + \
    opt_series(PE, 25000, "PE", pe_px)
build_corpus(db2, spot2, opts2)
r2 = run({"option_premium": {"min": 100, "max": 200}, "target_value": 999.0,
          "session_start": "09:15", "session_end": "15:00",
          "eod_square_off": "15:15"}, db2)
d2 = r2["summary"]["diag_cbo"]
chk("only ONE position is ever open (D6)", len(r2["trades"]) == 1)
chk("later signals are recorded as blocked, not silently dropped",
    d2["blocked_in_trade"] > 0, f"blocked_in_trade={d2['blocked_in_trade']}")
chk("the open trade is squared off at EOD", r2["trades"][0].exit_reason == "EOD")
chk("EOD square-off happens at 15:15, the configured time (P1)",
    (r2["trades"][0].exit_ts - DS) // 60 == 915,
    f"minute={(r2['trades'][0].exit_ts - DS)//60}")
chk("EOD P&L share is reported — the SCALP_V5 parity trap",
    "eod_pnl_share_pct" in d2, f"{d2.get('eod_pnl_share_pct')}%")

r2b = run({"option_premium": {"min": 100, "max": 200}, "target_value": 999.0,
           "session_start": "09:15", "session_end": "15:00",
           "eod_square_off": "15:15", "max_trades_per_day": 1}, db2)
chk("max_trades_per_day is honoured", len(r2b["trades"]) <= 1)


print("\n── E. MTM caps flatten and halt (D5) ─────────────────────────────")
# CE decays hard after entry: -30 pts x 65 = -1,950 open MTM. A 1,000 cap
# must fire on realised+open and close the position immediately.
db3 = str(Path(tempfile.mkdtemp()) / "bt.db")
ce3 = {555: (150, 151, 149, 150), 562: (150, 151, 149, 150),
       566: (120, 121, 119, 120)}
opts3 = opt_series(CE, 25000, "CE", ce3) + opt_series(PE, 25000, "PE", pe_px)
build_corpus(db3, spot, opts3)
base3 = {"option_premium": {"min": 100, "max": 200}, "target_value": 999.0,
         "session_start": "09:15", "session_end": "15:00",
         "eod_square_off": "15:15"}
r3 = run({**base3, "mtm_loss_cap": 1000.0}, db3)
d3 = r3["summary"]["diag_cbo"]
chk("the MTM loss cap fires", d3["mtm_cap_exits"] == 1)
chk("the day is flagged as loss-capped", d3["mtm_loss_cap_days"] == 1)
chk("the position is closed with reason MTM_CAP",
    r3["trades"][0].exit_reason == "MTM_CAP")
chk("cap fires on OPEN MTM, before any exit would have realised it",
    (r3["trades"][0].exit_ts - DS) // 60 == 566,
    f"minute={(r3['trades'][0].exit_ts - DS)//60}")
chk("no trade is opened after the halt",
    d3["blocked_mtm_halt"] > 0 or len(r3["trades"]) == 1)
chk("MTM-cap P&L share is reported (a cap that flatters results is "
    "curve-fitting a risk control)", "mtm_cap_pnl_share_pct" in d3)

r3b = run({**base3, "mtm_loss_cap": 1000.0, "mtm_include_open": False}, db3)
chk("with mtm_include_open=False the same day does NOT cap "
    "(nothing was realised)",
    r3b["summary"]["diag_cbo"]["mtm_cap_exits"] == 0)


print("\n── F. D8 pessimistic ambiguous bar ───────────────────────────────")
# 09:21 breaches the reference HIGH (25050) and LOW (24950) in one minute.
db4 = str(Path(tempfile.mkdtemp()) / "bt.db")
# NOTE spot is a list of (minute, o, h, l, c) starting at minute 555, so it
# must be filtered BY MINUTE — slicing by index silently duplicated the
# whole day the first time this was written.
spot4 = [r for r in spot if r[0] < 561]
spot4.append((561, 25000, 25060, 24940, 25000))     # outside bar
for m in range(562, 930):
    spot4.append((m, 25000, 25005, 24995, 25000))
opts4 = opt_series(CE, 25000, "CE", {555: (150, 152, 145, 150)}) + \
    opt_series(PE, 25000, "PE", {555: (150, 152, 145, 150)})
build_corpus(db4, spot4, opts4)
# Entry window closed after 09:25 for the same reason as scenario C.
r4 = run({"option_premium": {"min": 100, "max": 200}, "target_value": 10.0,
          "session_start": "09:15", "session_end": "09:25",
          "eod_square_off": "15:15", "both_side_policy": "pessimistic"}, db4)
d4 = r4["summary"]["diag_cbo"]
chk("the ambiguous bar produces a trade (not silently dropped)",
    len(r4["trades"]) >= 1)
if r4["trades"]:
    t4 = r4["trades"][0]
    chk("it is booked as AMBIGUOUS, per D8", t4.exit_reason == "AMBIGUOUS")
    chk("entry and exit are the SAME minute (stopped at entry)",
        t4.entry_ts == t4.exit_ts)
    chk("it is flagged ambiguous_fill for persist_run and the UI",
        t4.ambiguous_fill and t4.ambiguous)
    chk("a long exits at the fill bar's LOW — the pessimistic extreme",
        t4.exit_price == 145.0, f"exit={t4.exit_price}")
    chk("the trade is a LOSS", t4.net_pnl < 0, f"net={t4.net_pnl}")
    chk("condition tags it so it can be filtered in analysis",
        t4.condition.endswith("·AMB"), t4.condition)
chk("ambiguous P&L share is reported — if this dominates net, the run is "
    "an artifact of a tie-break", "ambiguous_pnl_share_pct" in d4,
    f"{d4.get('ambiguous_pnl_share_pct')}%")

r4b = run({"option_premium": {"min": 100, "max": 200}, "target_value": 10.0,
           "session_start": "09:15", "session_end": "09:25",
           "eod_square_off": "15:15", "both_side_policy": "skip"}, db4)
chk("policy=skip takes no trade on that same bar (the D8 alternative)",
    len(r4b["trades"]) == 0)


print("\n── G. SELL mode books the opposite contract ──────────────────────")
r5 = run({"option_premium": {"min": 100, "max": 200}, "target_value": 10.0,
          "session_start": "09:15", "session_end": "09:25",
          "eod_square_off": "15:15", "leg_action": "SELL"}, db)
chk("an UP signal in SELL mode shorts the PE (VET convention)",
    len(r5["trades"]) == 1 and r5["trades"][0].instrument_type == "PE"
    and r5["trades"][0].direction == "SELL")
if r5["trades"]:
    chk("a short's target is BELOW its entry premium",
        r5["trades"][0].tp < r5["trades"][0].entry_price)
    chk("the SPOT stop is unchanged by leg_action — same directional view",
        r5["trades"][0].sl == 24950.0)


print("\n── H. config guards ──────────────────────────────────────────────")
bad = run({"session_end": "15:20", "eod_square_off": "15:15"}, db)
chk("an EOD square-off at-or-before session_end is REFUSED, not silently run",
    bad.get("aborted") is True, str(bad.get("reason"))[:60])
bad2 = run_cbo_backtest(db_path=db, strategy_id="CBO_V1",
                        underlying="MIDCPNIFTY", date_from=DAY, date_to=DAY,
                        config_override={})
chk("an index with no lot constant is refused rather than guessed",
    bad2.get("aborted") is True)

print("\n" + "=" * 68)
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CBO RUNNER SIMULATION CHECKS PASSED")
