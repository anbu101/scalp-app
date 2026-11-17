"""
dash_fixture.py — a realistic /api/fleet/today payload for Dashboard screenshots
(13:05 IST, Live + Paper books, 6 traded rows, 2 idle). Same data for every
before / after render. Deterministic (seeded walk).
"""
import random
import time

OPEN_MIN, NOW_MIN = 555, 785          # 09:15 → 13:05
DAY0 = int(time.mktime(time.strptime("2026-11-17 00:00:00", "%Y-%m-%d %H:%M:%S"))) - 19800 + 19800


def walk(end, seed, start_min=OPEN_MIN, vol=None):
    rnd = random.Random(seed)
    n = NOW_MIN - start_min + 1
    vol = vol or max(120, abs(end) / 9)
    v, out = 0.0, []
    for i in range(n):
        drift = (end - v) / max(1, n - i)
        v += drift + rnd.gauss(0, vol / 6)
        out.append([start_min + i, round(v, 2)])
    out[-1][1] = end
    return out


def ev(sid, text):
    return {"text": text}


def row(sid, book, mode, state, gross, realised, unrealised, charges, open_legs, closed, carried=False,
        stop=None, text="", seed=1, start=OPEN_MIN, approx=False):
    path = walk(gross, seed, start)
    vals = [p[1] for p in path]
    return {"id": sid, "book": book, "key": f"{sid}:{book}", "mode": mode, "state": state,
            "open_legs": open_legs, "closed_trades": closed, "carried": carried,
            "realised": realised, "unrealised": unrealised, "gross": gross, "charges": charges,
            "net": round(gross - charges, 2), "ltp_missing": 0, "approx": approx, "marks_stale": 0,
            "mark_asof": None, "stop": stop, "first_entry_ts": None, "last_event": {"text": text},
            "peak": max(vals), "trough": min(vals), "path": path,
            "week": {"net": 0, "trades": 0, "approx": False}, "month": {"net": 0, "trades": 0, "approx": False}}


ROWS = [
    row("IC_V2", "LIVE", "LIVE", "OPEN", 18450, 15132, 3318, 412, 4, 1, carried=True,
        text="Bought back 24950 PE ×195 @ 19.30 · +₹15,132", seed=11),
    row("TMA_V2", "LIVE", "PAPER_LIVE", "OPEN", 6240, 0, 6240, 0, 2, 0, carried=True,
        text="Holding 25050 CE short + 26100 CE wing", seed=12),
    row("TMA_V2", "PAPER", "PAPER_LIVE", "OPEN", 6410, 0, 6410, 0, 2, 0, carried=True,
        text="Paper twin of the live spread", seed=13),
    row("TSG_V1", "PAPER", "PAPER", "OPEN", 3920, 0, 3920, 0, 4, 0, stop=-35000,
        text="Strangle 24600 PE / 25700 CE + wings @ 09:16", seed=14, start=556),
    row("ORB_V1", "PAPER", "PAPER", "CLOSED", 1560, 1560, 0, 118, 0, 1,
        text="Exited 25300 CE @ TP · +₹1,560", seed=15, start=571),
    row("VET_V1", "PAPER", "PAPER", "CLOSED", -2180, -2180, 0, 96, 0, 1,
        text="Stopped out 24900 PE @ 11:42 · SL", seed=16, start=600),
]
ROWS[4]["last_event"] = {"text": "Exited 25300 CE @ TP · +₹1,560", "reason": "TP", "ts": DAY0 + 648 * 60 - 19800}
ROWS[5]["last_event"] = {"text": "Stopped out 24900 PE @ 11:42", "reason": "SL", "ts": DAY0 + 702 * 60 - 19800}


def _sum(book, k):
    return round(sum(r[k] for r in ROWS if r["book"] == book), 2)


def _book_path(book):
    keys = [r for r in ROWS if r["book"] == book]
    out = []
    for m in range(OPEN_MIN, NOW_MIN + 1):
        v = 0.0
        for r in keys:
            last = None
            for p in r["path"]:
                if p[0] > m:
                    break
                last = p[1]
            v += last or 0.0
        out.append([m, round(v, 2)])
    return out


PATHS = {"LIVE": _book_path("LIVE"), "PAPER": _book_path("PAPER")}


def _totals(book, week, month):
    p = PATHS[book]
    pk = max(p, key=lambda x: x[1])
    return {"rows": sum(1 for r in ROWS if r["book"] == book), "gross": _sum(book, "gross"),
            "realised": _sum(book, "realised"), "unrealised": _sum(book, "unrealised"),
            "charges": _sum(book, "charges"), "net": round(_sum(book, "gross") - _sum(book, "charges"), 2),
            "peak": pk[1], "peak_min": pk[0], "approx": False, "marks_stale": 0,
            "week_net": week, "week_trades": 9, "week_from": "11 Nov", "month_net": month, "month_trades": 31,
            "month_from": "17 Oct"}


FLEET = {"ok": True, "server_ts": int(time.time()), "day": "2026-11-17",
         "session": {"open_min": 555, "close_min": 930, "now_min": NOW_MIN, "market_open": True},
         "strategies": ROWS,
         "idle": [{"id": "EMC_V1", "mode": "PAPER"}, {"id": "BRK_V1", "mode": "PAPER"}],
         "totals": {"LIVE": _totals("LIVE", 41250, 1.86e5), "PAPER": _totals("PAPER", 12840, 64320)},
         "paths": PATHS, "fleet": 8, "active": 6}
