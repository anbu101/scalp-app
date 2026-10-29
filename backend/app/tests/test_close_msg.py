# backend/app/tests/test_close_msg.py — ── CLOSE_MSG_FIX_20261029 ──
#   cd backend && PYTHONPATH=$PWD python3 app/tests/test_close_msg.py
import os
import sys
import tempfile

os.environ["HOME"] = tempfile.mkdtemp(prefix="closemsg_")
import app.api.telegram_api as T                            # noqa: E402

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL  {name}  [{str(detail)[:400]}]")


SENT, EVENTS = [], []
T._fanout = lambda key, msg, **kw: SENT.append((key, msg, kw))
T.record_event = lambda *a, **k: EVENTS.append((a, k))
T.codename = lambda sid: {"IC_V2": "Icarus", "IC_V1": "Indica", "TMA_V2": "Timberwolf"}.get(sid, sid)


def last():
    return SENT[-1][1]


base = {"strategy_id": "IC_V1", "mode": "paper", "symbol": "NIFTY26N0321450PE", "pnl": -1235.0}
T.notify_manual_exit(dict(base, reason="EOD"))
check("IC passes 'reason' → shown (was always 'Manual')", "Reason: EOD" in last() and "Manual" not in last(), last())
T.notify_manual_exit(dict(base, exit_reason="TIME_EXIT"))
check("'exit_reason' still works", "Reason: TIME_EXIT" in last(), last())
T.notify_manual_exit(dict(base, strategy_id="TMA_V2", note="EXPIRY · spread net incl hedge · hedge exit @1.20"))
check("TMA_V2 puts the reason in 'note' → shown as the reason, no duplicate note line",
      "Reason: EXPIRY · spread net incl hedge" in last() and "Note:" not in last(), last())
T.notify_manual_exit(dict(base, reason="SIGNAL", note="hedge exit @2.10"))
check("reason + a separate note → both shown", "Reason: SIGNAL" in last() and "Note: hedge exit @2.10" in last(), last())
T.notify_manual_exit(dict(base))
check("nothing given → 'Manual' (unchanged default)", "Reason: Manual" in last(), last())
check("a real close still records its EXIT event", len(EVENTS) == 5 and EVENTS[-1][0][0] == T.EVENT_EXIT, EVENTS[-1:])

n_ev = len(EVENTS)
legs = ["NIFTY26N0322500PE", "NIFTY26N0323650CE", "NIFTY26N0321450PE", "NIFTY26N0323000CE"]
T.notify_overnight_carry({"strategy_id": "IC_V2", "mode": "live", "symbol": ", ".join(legs), "side": "CARRY",
                          "quantity": 2600, "pnl": None, "reason": "OVERNIGHT_CARRY_COMMITTED"})
m = last()
check("carry message says CARRYING OVERNIGHT, LIVE badge, Icarus", "CARRYING OVERNIGHT" in m and "🟢 LIVE" in m and "Icarus" in m, m)
check("…lists all 4 legs, says they stay open", all(f"<code>{s}</code>" in m for s in legs) and "Still OPEN" in m, m)
check("…never 'POSITION CLOSED', 'P&L ₹0' or 'Manual'", "POSITION CLOSED" not in m and "₹0" not in m and "Manual" not in m, m)
check("…records no EXIT event (not a close)", len(EVENTS) == n_ev)
check("…goes to the trade-activity channel like the old message", SENT[-1][0] == T.NOTIF_TRADE_ACTIVITY)

print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL CLOSE_MSG CHECKS PASSED")
