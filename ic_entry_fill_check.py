#!/usr/bin/env python3
# ic_entry_fill_check.py — ── IC_ENTRY_FILL_CHECK_20261101 ── READ-ONLY, writes nothing.
#
# ic_sl_gap_diag.py (1 Nov) settled 28 + 29 Oct as VERDICT A on both days: the
# stop check is intrabar in both engines, the SL LEVELS differ because the
# ENTRY prices differ — backtest 96.15 vs paper 94.00 (28 Oct 22850CE),
# 92.00 vs 89.25 (29 Oct 22900CE). Same strike, SL 42 % → a 3–4 pt higher stop.
#
#   backtest entry = CLOSE of the 09:17 bar (the bar ENDING at entry time) —
#                    strike pick on the same closes   (ic_v1_engine.entry_close)
#   paper entry    = LTP from the REST chain snapshot taken at ~09:18:04
#                    (ic_engine → snapshot_weekly_chain → pick.ltp)
#
# Two days cannot decide a convention, so this measures EVERY recorded IC_V2
# paper leg against the bar conventions a backtest could use, before anything
# is changed (falsify-first):
#
#   core legs (entered at the entry minute E):
#     PREV_CLOSE  close of bar E−1   ← today's runner
#     OPEN        open  of bar E     ← first print at/after the entry minute
#     CLOSE       close of bar E
#   adjustment legs (opened ~60 s after the stop, at wall time W):
#     PREV_CLOSE  close of bar ⌊W⌋−1
#     OPEN        open  of bar ⌊W⌋
#     CLOSE       close of bar ⌊W⌋   ← today's runner books the close of the
#                                      activation-minute bar
#   plus, for the shorts: which strike each convention would have PICKED
#   (highest premium ≤ cap) vs the strike paper actually sold.
#
# PRE-REGISTERED BAR (written before the run): a candidate replaces today's
# convention only if, over ≥ 15 core legs, its mean |error| is at most half of
# PREV_CLOSE's, its mean signed error is within ±1 pt, and it picks paper's
# short strike at least as often as PREV_CLOSE. Otherwise the entry convention
# stays and the gap is reported as slippage/noise.
#
#   cd <repo root> && python3 ic_entry_fill_check.py                 # every day with bars
#   cd <repo root> && python3 ic_entry_fill_check.py --since=2026-09-01 [--sid=IC_V2] [--cap=100]

import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def _d(ts):
    return datetime.fromtimestamp(int(ts), IST).date()


def _hm(ts):
    return datetime.fromtimestamp(int(ts), IST).strftime("%H:%M:%S")


def _arg(name, default=None):
    return next((x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith(f"--{name}=")), default)


def paper_rows(app_db, sid, since):
    c = sqlite3.connect(f"file:{app_db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT symbol, trade_class, trade_direction, entry_time, candle_ts, entry_price, exit_reason "
            "FROM paper_trades WHERE strategy_name=? AND entry_time>=? ORDER BY entry_time",
            (sid, int(datetime(since.year, since.month, since.day, tzinfo=IST).timestamp())))]
    finally:
        c.close()


def configs(sid):
    """day → config from the recorder's 09:14 snapshots (from 29 Oct)."""
    out = {}
    try:
        from app.recorder import rec_db as RD
        if not RD.db_path().exists():
            return out
        c = RD.connect()
        try:
            for day, blob in c.execute("SELECT day, config_json FROM session_config WHERE strategy_id=? ORDER BY day", (sid,)):
                out[day] = json.loads(blob)
        finally:
            c.close()
    except Exception as e:
        print(f"  (recorder configs unavailable: {type(e).__name__}: {e})")
    return out


def cap_for(day, leg_id, cfgs, live, override):
    if override:
        return float(override), "--cap"
    srcs = sorted(cfgs)
    pick = next((d for d in reversed(srcs) if d <= day), None) or next((d for d in srcs if d > day), None)
    cfg, src = (cfgs[pick], f"snapshot {pick}") if pick else (live, "live config")
    for l in (cfg or {}).get("legs") or []:
        if str(l.get("id")) == str(leg_id):
            return float(l.get("premium_max") or 0), src
    return None, src


class Bars:
    def __init__(self):
        from app.recorder import rec_db as RD
        self.c = RD.open_union_ro(RD.main_db_path(), "NIFTY")
        self.c.row_factory = sqlite3.Row
        self._sym = {}

    def bar(self, sym, ts):
        r = self.c.execute("SELECT open, high, low, close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?",
                           (sym, ts)).fetchone()
        return dict(r) if r else None

    def chain(self, ts, otype, day):
        """[(symbol, open, close)] of the front weekly expiry for bars stamped ts."""
        rows = self.c.execute("SELECT tradingsymbol, expiry, open, close FROM backtest_candles_1m "
                              "WHERE underlying='NIFTY' AND instrument_type=? AND ts=?", (otype, ts)).fetchall()
        exps = sorted({r["expiry"] for r in rows if r["expiry"] and str(r["expiry"])[:10] >= day})
        if not exps:
            return []
        fe = exps[0]
        return [(r["tradingsymbol"], float(r["open"]), float(r["close"])) for r in rows if r["expiry"] == fe]


def pick(cands, cap):
    el = [(s, p) for s, p in cands if p and 0 < p <= cap]
    return sorted(el, key=lambda x: (-x[1], x[0]))[0][0] if el else None


def summ(errs):
    if not errs:
        return "n=0"
    n = len(errs)
    return f"n={n:<3} mean {sum(errs) / n:+6.2f}   mean|e| {sum(abs(e) for e in errs) / n:5.2f}"


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    be = os.path.join(root, "backend")
    if not os.path.isdir(os.path.join(be, "app")):
        print("run from the scalp-app repo root")
        sys.exit(1)
    sys.path.insert(0, be)
    sid = _arg("sid", "IC_V2")
    since = date.fromisoformat(_arg("since", "2020-01-01"))
    from app.utils.app_paths import DB_PATH
    app_db = os.environ.get("SCALP_APP_DB") or str(DB_PATH)
    rows = paper_rows(app_db, sid, since)
    cfgs = configs(sid)
    live = {}
    try:
        from app.config.strategy_loader import load_strategy_config
        live = load_strategy_config(sid) or {}
    except Exception:
        pass
    B = Bars()
    print(f"── {sid} entry-fill check (read-only) · {len(rows)} paper legs since {since} · app.db {app_db}")
    print("   error = paper entry − candidate   (positive = paper paid/received MORE than the bar)")

    err = {"core": defaultdict(list), "adj": defaultdict(list)}
    picks = defaultdict(lambda: [0, 0])          # conv → [matched, tested]
    no_bar, days = 0, set()
    print(f"\n   {'day':<10} {'leg':<5} {'symbol':<20} {'booked':>8} {'paper':>8} {'PREV_C':>8} {'OPEN':>8} {'CLOSE':>8}  pick(PREV/OPEN)")
    for r in rows:
        sym, epx = r["symbol"], float(r["entry_price"] or 0)
        wall = int(r["entry_time"] or 0)
        cts = int(r["candle_ts"] or 0)
        leg = str(r["trade_class"] or "")
        core = leg in ("L1", "L2", "L3", "L4") and cts and abs(wall - cts) <= 120
        m = (cts - cts % 60) if core else (wall - wall % 60)
        pb, b = B.bar(sym, m - 60), B.bar(sym, m)
        day = _d(m).isoformat()
        if not (pb and b):
            no_bar += 1
            continue
        days.add(day)
        cand = {"PREV_CLOSE": pb["close"], "OPEN": b["open"], "CLOSE": b["close"]}
        grp = "core" if core else "adj"
        for k, v in cand.items():
            err[grp][k].append(epx - float(v))
        ptxt = ""
        if core and r["trade_direction"] == "SHORT":
            cap, _src = cap_for(day, leg, cfgs, live, _arg("cap"))
            if cap and cap >= epx:
                ot = "CE" if sym.endswith("CE") else "PE"
                cp = pick([(s, c) for s, _o, c in B.chain(m - 60, ot, day)], cap)
                op = pick([(s, o) for s, o, _c in B.chain(m, ot, day)], cap)
                for k, p in (("PREV_CLOSE", cp), ("OPEN", op)):
                    picks[k][1] += 1
                    picks[k][0] += int(p == sym)
                ptxt = f"  {'✓' if cp == sym else '✗'}/{'✓' if op == sym else '✗'}  cap {cap:g}"
            else:
                ptxt = f"  (cap {cap} < paper entry — config unknown that day)"
        print(f"   {day:<10} {('ADJ' if not core else leg):<5} {sym:<20} {_hm(wall)[:8]:>8} {epx:8.2f} "
              f"{cand['PREV_CLOSE']:8.2f} {cand['OPEN']:8.2f} {cand['CLOSE']:8.2f}{ptxt}")

    print(f"\n── summary over {len(days)} day(s); {no_bar} leg(s) skipped for missing bars")
    for grp, title in (("core", "core legs (entry minute)"), ("adj", "adjustment legs (~60 s after the stop)")):
        print(f"   {title}")
        for k in ("PREV_CLOSE", "OPEN", "CLOSE"):
            mark = "  ← today's runner" if (grp, k) in (("core", "PREV_CLOSE"), ("adj", "CLOSE")) else ""
            print(f"     {k:<11} {summ(err[grp][k])}{mark}")
    for k, (ok, n) in picks.items():
        print(f"   short strike picked = paper's: {k:<11} {ok}/{n}")

    pc, op = err["core"]["PREV_CLOSE"], err["core"]["OPEN"]
    if len(op) >= 15:
        mae = lambda e: sum(abs(x) for x in e) / len(e)   # noqa: E731
        ok = (mae(op) <= 0.5 * mae(pc) and abs(sum(op) / len(op)) <= 1.0
              and picks["OPEN"][0] >= picks["PREV_CLOSE"][0])
        print(f"\n   PRE-REGISTERED BAR (core, OPEN vs PREV_CLOSE): {'PASSES' if ok else 'FAILS'}")
    else:
        print(f"\n   PRE-REGISTERED BAR: only {len(op)} core legs with bars — need ≥ 15, no verdict")
    print("\nPaste this whole output back into the chat.")


if __name__ == "__main__":
    main()
