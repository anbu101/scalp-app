"""
livechart_scene.py — shared scene for the Live chart screenshots and tests: rendered
by the REAL replay route on a deterministic synthetic corpus (replay_synth: NIFTY
1m + a Black-76 weekly chain), an OPEN Timberwolf (TMA_V2) paper spread entered
24 Feb 2025 10:30, "now" = Tue 25 Feb 2025 13:05 IST, live prices fed from the
corpus. Same scene for every before / after render.

    PYTHONPATH=../backend python3 uitest/shot_livechart.py OUT_PREFIX [--test]

Writes OUT_PREFIX-card.png, -full.png, -phone.png, -metrics.json.
--test also asserts the chart actually drew (used by the suite).
"""
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402  (sets HOME, runs_db_path)
from pathlib import Path                                               # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
ts_of = lambda d, hh, mm, ss=0: int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST).timestamp())   # noqa: E731
D24, D25 = date(2025, 2, 24), date(2025, 2, 25)
NOW = ts_of(D25, 13, 5, 5)

import app.backtest.repo.backtest_repo as BR                           # noqa: E402
import app.backtest.data.candle_source as CS                           # noqa: E402
import app.backtest.replay.replay_core as RC                           # noqa: E402
import app.backtest.replay.live_replay as LR                           # noqa: E402
from app.backtest.replay.replay_synth import build                     # noqa: E402
from app.db.sqlite import get_conn                                     # noqa: E402

DB = os.path.join(U.TMP, "backtest.db")
BR._db_path = lambda: Path(DB)
CS._backtest_db_path = lambda: Path(DB)
RC.runs_db_path = lambda: DB
CACHE = "/tmp/livechart_corpus.db"
if not os.path.exists(CACHE):
    build(CACHE, date(2025, 1, 1), date(2025, 2, 28))
shutil.copy(CACHE, DB)

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
c.execute("DELETE FROM backtest_candles_1m WHERE ts>=?", (ts_of(D25, 13, 5),))     # nothing after "now"
c.commit()
exp = c.execute("SELECT expiry FROM backtest_candles_1m WHERE instrument_type='CE' AND ts>=? AND ts<? LIMIT 1",
                (ts_of(D24, 9, 15), ts_of(D24, 9, 16))).fetchone()[0]
spot = c.execute("SELECT close FROM backtest_candles_1m WHERE instrument_type='SPOT' AND ts=?", (ts_of(D24, 10, 30),)).fetchone()[0]
atm = int(round(spot / 50) * 50)
yy, mc, dd = int(exp[2:4]), str(int(exp[5:7])), exp[8:10]
S = lambda k, t: f"NIFTY{yy}{mc}{dd}{k}{t}"                            # noqa: E731
SELL, WING = S(atm + 100, "CE"), S(atm + 400, "CE")
TOK = {r[0]: r[1] for r in c.execute("SELECT DISTINCT tradingsymbol, instrument_token FROM backtest_candles_1m")}
px = lambda s, t: c.execute("SELECT close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts<=? ORDER BY ts DESC LIMIT 1", (s, t)).fetchone()[0]   # noqa: E731

APP = get_conn()
for dirn, s in (("SELL", SELL), ("BUY", WING)):
    ent = px(s, ts_of(D24, 10, 30))
    APP.execute("INSERT INTO tma2_trades (group_id, mode, direction, tradingsymbol, token, instrument_type, strike, expiry, qty, "
                "entry_ts, entry_price, sl, tp, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("TG1", "PAPER", dirn, s, TOK[s], s[-2:], 0, exp, 650, ts_of(D24, 10, 30), ent,
                 (round(ent * 1.6, 1) if dirn == "SELL" else None), (10.0 if dirn == "SELL" else None), "OPEN"))
APP.commit()

DUMP = {s: {"token": t, "underlying": "NIFTY", "instrument_type": s[-2:], "strike": float(s[len(f"NIFTY{yy}{mc}{dd}"):-2]),
            "expiry": exp} for s, t in TOK.items() if s.startswith("NIFTY") and s[-2:] in ("CE", "PE")}
LR._dump_by_sym = lambda: DUMP
LR._kite = lambda: None
LR._now_ts = lambda: NOW
LR._TRACKER_THREAD = False


def _ltp(ident):   # live prices = the last corpus close (the forming bar sits on it); own connection per call
    try:                # (the route runs in a worker thread: a sqlite connection must not cross threads)
        with sqlite3.connect(DB) as cc:
            if "SPOT" in str(ident).upper():
                v = cc.execute("SELECT close FROM backtest_candles_1m WHERE instrument_type='SPOT' ORDER BY ts DESC LIMIT 1").fetchone()[0]
            else:
                v = cc.execute("SELECT close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                               (ident, NOW)).fetchone()[0]
        return (float(v), float(NOW))
    except Exception:
        return None
LR._read_ltp = _ltp

from dash_fixture import FLEET                                         # noqa: E402
FLEET = json.loads(json.dumps(FLEET))
for r in FLEET["strategies"]:
    r["path"] = [[m, v] for m, v in r["path"]]

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402,F401

CHROME = os.environ.get("CHROME") or next((p for p in ("/usr/bin/google-chrome",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if os.path.exists(p)), None)
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
SET.parent.mkdir(parents=True, exist_ok=True)
SET.write_text(json.dumps({"dashboard_layout": "classic"}))
U.LICENSE.update(ui_level="admin", strategies=["*"])


def page(b, vp):
    ctx = b.new_context(viewport=vp, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.clock.set_fixed_time("2025-02-25T13:05:05+05:30")
    pg.add_init_script("localStorage.clear();localStorage.setItem('scalp.strategyHost.focusId','TMA_V2');")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=FLEET))
    pg.route("**/system/license", lambda r: r.fulfill(json=dict(U.LICENSE, status="VALID")))
    pg.goto(BASE, wait_until="domcontentloaded")
    return ctx, pg


def drawn(pg):
    return pg.evaluate("(() => { const r = document.querySelector('[data-testid=live-chart-card]'); return r ? r.querySelectorAll('svg rect').length : 0; })()")


def wait_chart(pg, timeout=60):
    """Drawn AND steady for 3 s (DEF-038, the one-off blank ~2 s after the first draw, is fixed
    by BOOT_NO_REMOUNT_20261117; the steady wait stays as a cheap guard)."""
    t0, steady = time.time(), None
    while time.time() - t0 < timeout:
        if drawn(pg) >= 50:
            steady = steady or time.time()
            if time.time() - steady >= 3:
                return True
        else:
            steady = None
        time.sleep(0.25)
    return False
