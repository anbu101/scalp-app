# backend/app/api/test_margin_log.py — fence MARGIN_LOG_20261016
#
# Margin log sampler + summary, on a temp app.db with a fake Kite and fake
# fleet legs. No network, no broker session, no TestClient (Build-Mac rule).
#
#   1. basket: BUY legs first, NRML, closed legs and 0-qty legs dropped
#   2. base_lots = largest SELL leg (IC 10 shorts + 8 ADJ → 10); buy-only → largest
#   3. one pass writes one row per (strategy, book) with open legs; LIVE and
#      PAPER kept apart; per_lot = total ÷ base_lots
#   4. a failing margin call is stored with `error` and the pass continues
#   5. gate on the REAL epoch clock: 09:19 IST no, 09:20 yes, 15:25 yes,
#      15:26 no, market closed no; force=True bypasses
#   6. no Kite session → rows with error, never a crash
#   7. summary: median / p90 / peak / peak_at per strategy, fleet concurrent
#      = Σ per sample ts per book; errors counted, not averaged
#   8. retention purge; overlapping passes refused; job never raises
#
# Run: cd backend && python3 app/api/test_margin_log.py

from __future__ import annotations

import os
import sys
import tempfile
import calendar
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = str(HERE.parents[2])
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

FAILS: list = []
OK = [0]


def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(note)) if (note and not ok) else ''}")
    if ok:
        OK[0] += 1
    else:
        FAILS.append(name)


def ist(y, mo, d, h, mi):
    """Real epoch seconds for an IST wall-clock time."""
    return calendar.timegm((y, mo, d, h, mi, 0)) - (5 * 3600 + 30 * 60)


def leg(sym, qty, short, open_=True, book="PAPER"):
    return {"symbol": sym, "qty": qty, "short": short, "open": open_, "book": book}


class FakeKite:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def basket_order_margins(self, basket, consider_positions=False):
        self.calls.append((basket, consider_positions))
        if any(o["tradingsymbol"] in self.fail for o in basket):
            raise RuntimeError("InputException: bad symbol")
        tot = 0.0
        for o in basket:
            tot += (100000.0 if o["transaction_type"] == "SELL" else 5000.0) * o["quantity"] / 65
        return {"final": {"total": tot, "span": tot * 0.7, "exposure": tot * 0.3 - 1, "option_premium": 1}}


def main():
    import app.api.margin_log_routes as M
    import app.api.fleet_today_routes as F
    td = tempfile.mkdtemp()
    db = os.path.join(td, "app.db")
    M._db_path = lambda: db
    M.CALL_GAP_S = 0
    M._lot_size = lambda s: 65
    FLEET = {
        "IC_V2": [leg("NIFTYX24000CE", 650, True), leg("NIFTYX23000PE", 650, True),
                  leg("NIFTYX24500CE", 650, False), leg("NIFTYX22500PE", 650, False),
                  leg("NIFTYX24100CE", 520, False),                      # ·ADJ 8 lots
                  leg("NIFTYX23900CE", 650, True, open_=False)],         # closed → ignored
        "TSG_V1": [leg("NIFTYX24300CE", 650, True, book="LIVE"), leg("NIFTYX22700PE", 650, True, book="LIVE"),
                   leg("NIFTYX24200CE", 130, True), leg("NIFTYX22800PE", 130, True)],   # live 10 + paper 2
        "ORB_V1": [leg("NIFTYX23950CE", 650, False)],
        "BRK_V1": [],
    }
    F.FLEET_IDS = list(FLEET)
    F._allowed = lambda sid: True
    F._mode = lambda sid: "PAPER_LIVE" if sid == "TSG_V1" else "PAPER"
    F._legs_for = lambda sid, w: [dict(l) for l in FLEET[sid]]
    F._market_open = lambda: True

    print("── 1–2. basket + base lots ──")
    b = M.basket_for(FLEET["IC_V2"][:5] + [leg("X", 0, True)])
    check("BUY legs first", [o["transaction_type"] for o in b] == ["BUY"] * 3 + ["SELL"] * 2)
    check("NRML / NFO / MARKET", all(o["product"] == "NRML" and o["exchange"] == "NFO" for o in b))
    check("0-qty leg dropped", len(b) == 5)
    check("IC: 10 shorts + 8 ADJ → base 10 lots", M.base_lots(b) == 10)
    check("buy-only → largest leg", M.base_lots(M.basket_for([leg("A", 650, False), leg("B", 130, False)])) == 10)
    check("SENSEX → BFO", M.basket_for([leg("SENSEX24X80000CE", 20, True)])[0]["exchange"] == "BFO")

    print("── 3–4. one pass ──")
    k = FakeKite(fail={"NIFTYX23950CE"})
    t1 = ist(2026, 10, 19, 10, 0)
    r = M.sample_now(now=t1, kite=k)
    check("sampled", r.get("sampled") and r["rows"] == 4, r)
    rows = M._rows(30, now=t1)
    keys = sorted((x["strategy_id"], x["book"]) for x in rows)
    check("rows per (strategy, book) with open legs only",
          keys == [("IC_V2", "PAPER"), ("ORB_V1", "PAPER"), ("TSG_V1", "LIVE"), ("TSG_V1", "PAPER")], keys)
    ic = next(x for x in rows if x["strategy_id"] == "IC_V2")
    want = 2 * 1e6 + 2 * 5000 * 10 + 5000 * 8          # 2 shorts + 2 wings + 8-lot ADJ
    check("IC total = basket margin of its 5 open legs", abs(ic["total"] - want) < 0.01, ic["total"])
    check("IC per_lot = total ÷ 10", abs(ic["per_lot"] - want / 10) < 0.01)
    check("consider_positions=False on every call", all(c[1] is False for c in k.calls))
    tl = next(x for x in rows if x["strategy_id"] == "TSG_V1" and x["book"] == "LIVE")
    tp = next(x for x in rows if x["strategy_id"] == "TSG_V1" and x["book"] == "PAPER")
    check("LIVE and PAPER books kept apart", tl["base_lots"] == 10 and tp["base_lots"] == 2 and tl["mode"] == "PAPER_LIVE")
    orb = next(x for x in rows if x["strategy_id"] == "ORB_V1")
    check("failed call stored with error, no total", orb["total"] is None and "InputException" in (orb["error"] or ""))
    check("pass continued after the failure", r["errors"] == 1 and ic["error"] is None)
    check("day / hhmm in IST", ic["day"] == "2026-10-19" and ic["hhmm"] == "10:00", (ic["day"], ic["hhmm"]))

    print("── 5. gate (real epoch clock) ──")
    for h, mi, exp in [(9, 19, False), (9, 20, True), (15, 25, True), (15, 26, False)]:
        check(f"{h:02d}:{mi:02d} IST sampled={exp}", M._in_window(ist(2026, 10, 19, h, mi)) is exp)
    check("09:19 pass refused", M.sample_now(now=ist(2026, 10, 19, 9, 19), kite=k)["sampled"] is False)
    F._market_open = lambda: False
    check("market closed → refused", M.sample_now(now=ist(2026, 10, 19, 11, 0), kite=k)["sampled"] is False)
    check("force=True bypasses the gate", M.sample_now(now=ist(2026, 10, 19, 20, 0), force=True, kite=FakeKite())["sampled"] is True)
    F._market_open = lambda: True

    print("── 6. no session ──")
    M._kite = lambda: None
    r6 = M.sample_now(now=ist(2026, 10, 19, 10, 5))
    check("no Kite session → rows with error, no crash", r6.get("sampled") and r6["errors"] == r6["rows"] == 4, r6)

    print("── 7. summary ──")
    FLEET["IC_V2"][4]["qty"] = 650                                   # ADJ grows to 10 lots
    M.sample_now(now=ist(2026, 10, 20, 11, 0), kite=FakeKite())
    s = M.summarise(M._rows(30, now=ist(2026, 10, 20, 12, 0)))
    ics = next(x for x in s["strategies"] if x["strategy_id"] == "IC_V2")
    check("IC: 3 good samples over 2 days, 1 error counted", ics["samples"] == 3 and ics["days"] == 2 and ics["errors"] == 1, ics)
    want2 = 2 * 1e6 + 3 * 5000 * 10                    # ADJ now 10 lots
    check("IC peak = the 10-lot ADJ sample, with when", abs(ics["peak"] - want2) < 0.01 and ics["peak_at"] == "2026-10-20 11:00", ics)
    check("IC median of 3", abs(ics["median"] - want) < 0.01, ics["median"])
    check("typical lots 10", ics["lots_typical"] == 10)
    fp = s["fleet"]["PAPER"]
    check("fleet PAPER concurrent peak = Σ of one pass",
          abs(fp["peak"] - (want2 + 2 * 1e5 * 2 + 5000 * 10)) < 0.01, fp)
    check("fleet LIVE present and separate", "LIVE" in s["fleet"] and abs(s["fleet"]["LIVE"]["peak"] - 2e6) < 0.01, s["fleet"])
    check("summary route ok", M.get_summary(days=30)["ok"] is True)
    check("samples route filters by strategy", all(x["strategy_id"] == "ORB_V1" for x in M.get_samples(days=30, strategy_id="ORB_V1")["rows"]))

    print("── 8. retention / overlap / job ──")
    import sqlite3
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO margin_samples (ts, day, hhmm, strategy_id, book) VALUES (?,?,?,?,?)",
                  (ist(2026, 1, 1, 10, 0), "2026-01-01", "10:00", "IC_V2", "PAPER"))
    M.sample_now(now=ist(2026, 10, 21, 10, 0), kite=FakeKite())
    with sqlite3.connect(db) as c:
        old = c.execute("SELECT COUNT(*) FROM margin_samples WHERE day='2026-01-01'").fetchone()[0]
    check(f"rows older than {M.KEEP_DAYS} days purged", old == 0)
    M._lock.acquire()
    try:
        check("overlapping pass refused", M.sample_now(now=ist(2026, 10, 21, 10, 5), kite=FakeKite())["reason"] == "a sample is already running")
    finally:
        M._lock.release()
    F._legs_for = lambda sid, w: (_ for _ in ()).throw(RuntimeError("boom"))
    check("job never raises", M.margin_log_sample_job() is None)
    r8 = M.sample_now(now=ist(2026, 10, 21, 10, 10), kite=FakeKite())
    check("all legs unreadable → sampled, 0 rows, warnings", r8.get("sampled") and r8["rows"] == 0 and r8["warnings"], r8)

    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} MARGIN-LOG CHECKS PASSED")


if __name__ == "__main__":
    main()
