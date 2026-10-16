# backend/app/api/test_margin_log.py — fences MARGIN_LOG_20261016 + MARGIN_LOG_ENTRY_20261016
#                                      + MARGIN_LOG_FLEET_SEL_20261016
#
# Margin log sampler + summary, on a temp app.db with a fake Kite and fake
# fleet legs. No network, no broker session, no TestClient (Build-Mac rule).
# Every time below is a REAL epoch second for an IST wall-clock time.
#
#   1. basket: BUY legs first, NRML; signature ignores leg order
#   2. base_lots = largest SELL leg (IC 10 shorts + 8 ADJ → 10); buy-only → largest
#   3. gate: 09:16 nothing; 09:17 ENTRY samples; 09:18 unchanged → no Kite call;
#      a new leg → CHANGE; 09:20 TIMER samples every open book; 15:26 CLOSED
#      rows; 15:30 nothing; market closed nothing; force = MANUAL any time
#   4. cap: 8 entries in one minute → 6 calls (entries first), 2 deferred to
#      the next minute, none lost
#   5. CLOSED = zero row with NO margin call; an unreadable strategy is never
#      marked closed; restart seeds the recorded legs from the DB (no re-ENTRY)
#   6. failures: a failing call is stored with its error, the pass continues;
#      no Kite session → error rows, no crash
#   7. summary: per strategy median / p90 / peak + at-entry median / peak;
#      CLOSED rows excluded; fleet "at once" carries each strategy's latest
#      value forward within a day (an entry minute counts) and a CLOSED zeroes
#   8. migration: a first-version table (no trigger column) is upgraded and
#      its rows still count; retention purge; overlap refused; job never raises
#   9. FIRST vs ENTRY: legs opened long ago (restart / carried / pre-open
#      manual sample) are FIRST and stay out of the at-entry figures; a fresh
#      leg is ENTRY; the upgrade relabels pre-open ENTRY rows once
#  10. fleet selection: ?fleet= limits "Fleet at once" to the chosen
#      strategies (per-strategy rows unchanged); '-' = none; absent = all
#
# Run: cd backend && python3 app/api/test_margin_log.py

from __future__ import annotations

import calendar
import os
import sqlite3
import sys
import tempfile
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


def ist(h, mi, d=19, mo=10, y=2026):
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
        tot = sum((100000.0 if o["transaction_type"] == "SELL" else 5000.0) * o["quantity"] / 65 for o in basket)
        return {"final": {"total": tot, "span": tot * 0.7, "exposure": tot * 0.3, "option_premium": 1}}


def setup():
    import app.api.margin_log_routes as M
    import app.api.fleet_today_routes as F
    td = tempfile.mkdtemp()
    db = os.path.join(td, "app.db")
    M._db_path = lambda: db
    M.CALL_GAP_S = 0
    M._lot_size = lambda s: 65
    M._last.clear(); M._seeded[0] = False
    FLEET: dict = {}
    F.FLEET_IDS = ["IC_V2", "TSG_V1", "ORB_V1", "BRK_V1", "HA_V1", "EMC_V1", "VET_V1", "TMA_V2", "BB_V1"]
    F._allowed = lambda sid: True
    F._mode = lambda sid: "PAPER"
    F._legs_for = lambda sid, w: [dict(l) for l in FLEET.get(sid, [])]
    F._market_open = lambda: True
    return M, F, FLEET, db


def rows_at(M, ts):
    return [r for r in M._rows(30, now=ts + 60) if r["ts"] == ts]


IC = [leg("ICX24000CE", 650, True), leg("ICX23000PE", 650, True), leg("ICX24500CE", 650, False), leg("ICX22500PE", 650, False)]
TSG = [leg("TSGX24300CE", 130, True), leg("TSGX22700PE", 130, True)]


def main():
    print("── 1–2. basket / signature / lots ──")
    M, F, FLEET, db = setup()
    b = M.basket_for(IC + [leg("ADJX24100CE", 520, False)])
    check("BUY legs first, NRML", [o["transaction_type"] for o in b] == ["BUY"] * 3 + ["SELL"] * 2 and all(o["product"] == "NRML" for o in b))
    check("signature ignores leg order", M.signature(M.basket_for(IC)) == M.signature(M.basket_for(list(reversed(IC)))))
    check("IC 10 shorts + 8 ADJ → base 10 lots", M.base_lots(b) == 10)
    check("buy-only → largest leg", M.base_lots(M.basket_for([leg("A", 650, False), leg("B", 130, False)])) == 10)

    print("── 3. gate + triggers ──")
    k = FakeKite()
    FLEET["IC_V2"] = IC; FLEET["TSG_V1"] = TSG
    r = M.sample_now(now=ist(9, 16), kite=k)
    check("09:16 → nothing, no Kite call", r["sampled"] is False and not k.calls, r)
    r = M.sample_now(now=ist(9, 17), kite=k)
    rs = rows_at(M, ist(9, 17))
    check("09:17 → ENTRY row for each new position", r["sampled"] and sorted((x["strategy_id"], x["trigger"]) for x in rs)
          == [("IC_V2", "ENTRY"), ("TSG_V1", "ENTRY")], rs)
    check("ENTRY margin recorded (IC 2 shorts + 2 wings)", abs(next(x for x in rs if x["strategy_id"] == "IC_V2")["total"] - 2.1e6) < 0.01)
    n = len(k.calls)
    r = M.sample_now(now=ist(9, 18), kite=k)
    check("09:18 unchanged → no rows, no Kite call", r["rows"] == 0 and len(k.calls) == n and r["reason"] == "no change", r)
    FLEET["IC_V2"] = IC + [leg("ADJX24100CE", 520, False)]
    M.sample_now(now=ist(9, 19), kite=k)
    rs = rows_at(M, ist(9, 19))
    check("new leg (adjustment) → one CHANGE row, IC only", [(x["strategy_id"], x["trigger"]) for x in rs] == [("IC_V2", "CHANGE")], rs)
    M.sample_now(now=ist(9, 20), kite=k)
    rs = rows_at(M, ist(9, 20))
    check("09:20 → TIMER row for every open book", sorted((x["strategy_id"], x["trigger"]) for x in rs) == [("IC_V2", "TIMER"), ("TSG_V1", "TIMER")], rs)
    n = len(k.calls)
    M.sample_now(now=ist(9, 21), kite=k)
    check("09:21 (not a timer minute, no change) → no call", len(k.calls) == n)
    check("consider_positions=False on every call", all(c[1] is False for c in k.calls))
    FLEET["TSG_V1"] = []
    n = len(k.calls)
    M.sample_now(now=ist(15, 26), kite=k)
    rs = rows_at(M, ist(15, 26))
    check("15:26 exit → CLOSED row, zero, no margin call",
          [(x["strategy_id"], x["trigger"], x["n_legs"], x["total"]) for x in rs] == [("TSG_V1", "CLOSED", 0, 0.0)] and len(k.calls) == n, rs)
    FLEET["TSG_V1"] = TSG
    check("15:30 → nothing", M.sample_now(now=ist(15, 30), kite=k)["sampled"] is False)
    F._market_open = lambda: False
    check("market closed → nothing", M.sample_now(now=ist(10, 0), kite=k)["sampled"] is False)
    r = M.sample_now(now=ist(20, 0), force=True, kite=k)
    rs = rows_at(M, ist(20, 0))
    check("force → full pass any time; after-hours re-appearance is FIRST, IC MANUAL",
          sorted((x["strategy_id"], x["trigger"]) for x in rs) == [("IC_V2", "MANUAL"), ("TSG_V1", "FIRST")], rs)
    F._market_open = lambda: True

    print("── 4. per-minute cap ──")
    M, F, FLEET, db = setup()
    k = FakeKite()
    for i, sid in enumerate(F.FLEET_IDS[:8]):
        FLEET[sid] = [leg(f"{sid}X{i}CE", 650, i % 2 == 0)]
    r = M.sample_now(now=ist(9, 17), kite=k)
    check(f"8 entries at 09:17 → {M.MAX_CHANGE_CALLS} calls, 2 deferred", r["calls"] == M.MAX_CHANGE_CALLS and r["deferred"] == 2, r)
    r = M.sample_now(now=ist(9, 18), kite=k)
    check("the 2 deferred are sampled at 09:18 as ENTRY", r["calls"] == 2 and r["triggers"] == ["ENTRY"], r)
    got = {x["strategy_id"] for x in M._rows(30, now=ist(9, 30)) if x["trigger"] == "ENTRY"}
    check("all 8 entries recorded, none lost", len(got) == 8, got)

    print("── 5. CLOSED rules + restart seed ──")
    M, F, FLEET, db = setup()
    k = FakeKite()
    FLEET["IC_V2"] = IC; FLEET["ORB_V1"] = [leg("ORBX1CE", 650, False)]
    M.sample_now(now=ist(9, 17), kite=k)
    F._legs_for = lambda sid, w: (_ for _ in ()).throw(RuntimeError("db busy")) if sid == "ORB_V1" else [dict(l) for l in FLEET.get(sid, [])]
    M.sample_now(now=ist(9, 18), kite=k)
    check("unreadable strategy is NOT marked closed", not rows_at(M, ist(9, 18)))
    F._legs_for = lambda sid, w: [dict(l) for l in FLEET.get(sid, [])]
    M._last.clear(); M._seeded[0] = False                      # app restart
    n = len(k.calls)
    r = M.sample_now(now=ist(9, 19), kite=k)
    check("restart: recorded legs seeded from the DB → no re-ENTRY, no call", r["rows"] == 0 and len(k.calls) == n, r)

    print("── 6. failures ──")
    M, F, FLEET, db = setup()
    FLEET["IC_V2"] = IC; FLEET["ORB_V1"] = [leg("BADX1CE", 650, False)]
    r = M.sample_now(now=ist(9, 17), kite=FakeKite(fail={"BADX1CE"}))
    rs = rows_at(M, ist(9, 17))
    orb = next(x for x in rs if x["strategy_id"] == "ORB_V1")
    check("failed call stored with error; IC still recorded", "InputException" in (orb["error"] or "") and r["errors"] == 1
          and next(x for x in rs if x["strategy_id"] == "IC_V2")["total"] is not None, rs)
    M2, F2, FLEET2, _ = setup()
    M2._kite = lambda: None
    FLEET2["IC_V2"] = IC
    r = M2.sample_now(now=ist(9, 17))
    check("no Kite session → error row, no crash", r["sampled"] and r["errors"] == 1, r)

    print("── 7. summary + fleet at once ──")
    M, F, FLEET, db = setup()
    k = FakeKite()
    FLEET["IC_V2"] = IC
    M.sample_now(now=ist(9, 17), kite=k)                       # IC ENTRY 2.1e6
    FLEET["TSG_V1"] = TSG
    M.sample_now(now=ist(9, 18), kite=k)                       # TSG ENTRY 4e5  → fleet 2.5e6
    M.sample_now(now=ist(9, 20), kite=k)                       # TIMER both
    FLEET["IC_V2"] = []
    M.sample_now(now=ist(9, 21), kite=k)                       # IC CLOSED → fleet 4e5
    rows = M._rows(30, now=ist(10, 0))
    fc = M.fleet_concurrent(rows, "PAPER")
    check("fleet at 09:17 = IC alone", fc[ist(9, 17)] == 2.1e6, fc)
    check("fleet at 09:18 = IC carried forward + TSG entry", fc[ist(9, 18)] == 2.5e6, fc)
    check("fleet at 09:21 = TSG only (IC CLOSED zeroes it)", fc[ist(9, 21)] == 4e5, fc)
    s = M.summarise(rows)
    ic = next(x for x in s["strategies"] if x["strategy_id"] == "IC_V2")
    check("CLOSED rows excluded from strategy stats", ic["samples"] == 2 and ic["errors"] == 0, ic)
    check("at-entry stats: 1 entry, median = peak = 2.1e6", ic["entries"] == 1 and ic["entry_median"] == 2.1e6 and ic["entry_peak"] == 2.1e6, ic)
    check("fleet PAPER peak = 2.5e6 at 09:18 (an entry minute)", s["fleet"]["PAPER"]["peak"] == 2.5e6 and s["fleet"]["PAPER"]["peak_at"].endswith("09:18"), s["fleet"])
    check("fleet resets at a new day", M.fleet_concurrent([{"ts": 1, "day": "a", "strategy_id": "X", "book": "PAPER", "n_legs": 1, "total": 5.0},
                                                            {"ts": 2, "day": "b", "strategy_id": "Y", "book": "PAPER", "n_legs": 1, "total": 3.0}], "PAPER")[2] == 3.0)
    check("summary route ok", M.get_summary(days=30)["ok"] is True)

    print("── 8. migration / retention / overlap / job ──")
    M, F, FLEET, db = setup()
    with sqlite3.connect(db) as c:                             # first-version table, no trigger column
        c.execute("CREATE TABLE margin_samples (ts INTEGER NOT NULL, day TEXT NOT NULL, hhmm TEXT NOT NULL,"
                  " strategy_id TEXT NOT NULL, book TEXT NOT NULL, mode TEXT, n_legs INTEGER, base_lots REAL,"
                  " total REAL, span REAL, exposure REAL, premium REAL, per_lot REAL, legs_json TEXT, error TEXT)")
        c.execute("INSERT INTO margin_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ist(10, 0, d=16), "2026-10-16", "10:00", "IC_V2", "PAPER", "PAPER", 4, 10, 1.1e6, None, None, None, 1.1e5, "[]", None))
        c.execute("INSERT INTO margin_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ist(10, 0, d=1, mo=1), "2026-01-01", "10:00", "IC_V2", "PAPER", "PAPER", 4, 10, 1e6, None, None, None, 1e5, "[]", None))
    FLEET["IC_V2"] = IC
    M.sample_now(now=ist(9, 17), kite=FakeKite())
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(margin_samples)")}
        old = c.execute("SELECT COUNT(*) FROM margin_samples WHERE day='2026-01-01'").fetchone()[0]
    check("v1 table upgraded with a trigger column", "trigger" in cols)
    s = M.summarise(M._rows(30, now=ist(10, 0)))
    check("v1 rows still count as samples", next(x for x in s["strategies"] if x["strategy_id"] == "IC_V2")["samples"] == 2, s)
    check(f"rows older than {M.KEEP_DAYS} days purged", old == 0)
    M._lock.acquire()
    try:
        check("overlapping pass refused", M.sample_now(now=ist(9, 25), kite=FakeKite())["reason"] == "a sample is already running")
    finally:
        M._lock.release()
    F._legs_for = lambda sid, w: (_ for _ in ()).throw(RuntimeError("boom"))
    check("job never raises", M.margin_log_sample_job() is None)
    r = M.sample_now(now=ist(9, 25), kite=FakeKite())
    check("all strategies unreadable → no rows, nothing closed", r.get("sampled") and r["rows"] == 0, r)

    print("── 9. FIRST vs ENTRY ──")
    M, F, FLEET, db = setup()
    old_leg = dict(leg("OLDX24000CE", 650, True), entry_ts=ist(9, 18) - 3600)
    new_leg = dict(leg("NEWX24000CE", 650, True), entry_ts=ist(10, 2))
    FLEET["IC_V2"] = [old_leg]; FLEET["TSG_V1"] = [new_leg]
    M.sample_now(now=ist(10, 3), kite=FakeKite())
    rs = {x["strategy_id"]: x for x in rows_at(M, ist(10, 3))}
    check("leg opened an hour ago, first seen now → FIRST", rs["IC_V2"]["trigger"] == "FIRST", rs["IC_V2"])
    check("leg opened a minute ago → ENTRY", rs["TSG_V1"]["trigger"] == "ENTRY", rs["TSG_V1"])
    check("entry_ts stored (newest leg)", rs["TSG_V1"]["entry_ts"] == ist(10, 2))
    s = M.summarise(M._rows(30, now=ist(11, 0)))
    ic = next(x for x in s["strategies"] if x["strategy_id"] == "IC_V2")
    check("FIRST counts as a sample, not as an entry", ic["samples"] == 1 and ic["entries"] == 0 and ic["entry_median"] is None, ic)
    M, F, FLEET, db = setup()
    with sqlite3.connect(db) as c:                             # second-version table: trigger, no entry_ts
        c.execute("CREATE TABLE margin_samples (ts INTEGER NOT NULL, day TEXT NOT NULL, hhmm TEXT NOT NULL,"
                  " strategy_id TEXT NOT NULL, book TEXT NOT NULL, mode TEXT, n_legs INTEGER, base_lots REAL,"
                  " total REAL, span REAL, exposure REAL, premium REAL, per_lot REAL, legs_json TEXT, error TEXT,"
                  " trigger TEXT)")
        for hh, trig in (("08:40", "ENTRY"), ("09:17", "ENTRY"), ("15:40", "ENTRY"), ("08:40", "MANUAL")):
            h, m = map(int, hh.split(":"))
            c.execute("INSERT INTO margin_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ist(h, m, d=16), "2026-10-16", hh, "TMA_V1", "PAPER", "PAPER", 2, 10, 9.19e5, None, None, None,
                       9.19e4, "[]", None, trig))
    got = [(x["hhmm"], x["trigger"]) for x in M._rows(30, now=ist(10, 0))]
    check("upgrade relabels pre-open / after-close ENTRY → FIRST; 09:17 ENTRY kept; MANUAL untouched",
          sorted(got) == [("08:40", "FIRST"), ("08:40", "MANUAL"), ("09:17", "ENTRY"), ("15:40", "FIRST")], got)
    M._rows(30, now=ist(10, 0))
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO margin_samples (ts, day, hhmm, strategy_id, book, n_legs, total, trigger)"
                  " VALUES (?,?,?,?,?,?,?,?)", (ist(8, 41, d=17), "2026-10-17", "08:41", "TMA_V1", "PAPER", 2, 1.0, "ENTRY"))
    check("relabel runs once (only at the upgrade)",
          any(x["hhmm"] == "08:41" and x["trigger"] == "ENTRY" for x in M._rows(30, now=ist(10, 0, d=17))))

    print("── 10. fleet selection ──")
    M, F, FLEET, db = setup()
    k = FakeKite()
    FLEET["IC_V2"] = IC; FLEET["TSG_V1"] = TSG; FLEET["ORB_V1"] = [leg("ORBX1CE", 650, False)]
    M.sample_now(now=ist(9, 20), kite=k)                       # one TIMER pass, three strategies
    rows = M._rows(30, now=ist(10, 0))
    all_ = M.summarise(rows)["fleet"]["PAPER"]["peak"]
    only = M.summarise(rows, {"IC_V2", "TSG_V1"})
    check("all strategies: fleet = IC + TSG + ORB", all_ == 2.1e6 + 4e5 + 5e4, all_)
    check("selected IC + TSG: fleet = IC + TSG only", only["fleet"]["PAPER"]["peak"] == 2.5e6
          and only["fleet"]["PAPER"]["strategies"] == ["IC_V2", "TSG_V1"], only["fleet"])
    check("per-strategy rows unchanged by the selection", len(only["strategies"]) == 3)
    check("empty selection → no fleet row", M.summarise(rows, set())["fleet"] == {})
    check("?fleet parsing: absent / '-' / '' / list",
          M._fleet_ids(None) is None and M._fleet_ids("-") == set() and M._fleet_ids("") == set()
          and M._fleet_ids("ic_v2, TSG_V1") == {"IC_V2", "TSG_V1"})
    g = M.get_summary(days=30, fleet="IC_V2")
    check("route echoes the selection and filters the fleet",
          g["ok"] and g["fleet_ids"] == ["IC_V2"] and g["fleet"]["PAPER"]["peak"] == 2.1e6, g.get("fleet"))
    check("route without ?fleet → all", M.get_summary(days=30)["fleet_ids"] is None)

    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} MARGIN-LOG CHECKS PASSED")


if __name__ == "__main__":
    main()
