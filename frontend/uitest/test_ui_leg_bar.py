"""
test_ui_leg_bar.py — LEG_BAR_20261120: in the expanded strategy card each open leg's SL/TP
items become one bar ("Tug from entry": entry line in the middle, red toward SL, green toward
TP, a figure for how far it has gone). Real browser, BUILT frontend, the Modern Dashboard with
canned meters in the /api/replay/meters shape. The legs are Iron Condor V2's from 20 Nov 10:09.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_leg_bar.py   [CHROME=/path/to/chrome]
"""
import copy
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
from dash_fixture import FLEET                                         # noqa: E402
from calm_fixture import METERS as BASE_METERS                         # noqa: E402

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or "/usr/bin/google-chrome"
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
fails, N, errs = [], [0], []


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def bar(leg, name, sl, tp, lab, entry, ltp):
    return {"leg": leg, "name": name, "sl": sl, "tp": tp, "tp_label": lab, "entry": entry, "ltp": ltp}


PE = bar("NIFTY25300PE", "S 25300PE", 127.37, 0, "0", 63.69, 61.40)
CE = bar("NIFTY25600CE", "B 25600CE", 60.83, 173.80, "TP", 86.90, 84.40)
HIT = bar("NIFTY26250CE", "S 26250CE", 41.20, 0, "0", 20.60, 43.10)
ITEMS = [
    {"ok": None, "text": "basket MTM (gross)", "value": "−₹9,652"},
    {"ok": True, "text": "SL 127.37 · S 25300PE (LTP 61.40)", "value": "65.97 away · 0% of the way", "bar": PE},
    {"ok": True, "text": "SL 60.83 · B 25600CE (LTP 84.40)", "value": "23.57 away · 10% of the way", "bar": CE},
    {"ok": None, "text": "TP 173.80 · B 25600CE (LTP 84.40)", "value": "89.40 away · 0% of the way", "bar": CE},
    {"ok": False, "text": "SL 41.20 · S 26250CE (LTP 43.10)", "value": "1.90 THROUGH · 109% of the way", "bar": HIT},
    {"ok": None, "text": "time exit 15:28", "value": None},
]
METERS = copy.deepcopy(BASE_METERS)
METERS["IC_V2"]["waiting"].update({"headline": "In trade since 09:18 — iron condor; exits at 15:28", "items": ITEMS})


def open_dash(b, width=1440, height=1400):
    SET.parent.mkdir(parents=True, exist_ok=True)
    SET.write_text(json.dumps({"dashboard_layout": "modern", "theme": "dark"}))
    ctx = b.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.clock.set_fixed_time("2026-11-17T13:05:00+05:30")
    pg.add_init_script("localStorage.clear();")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=FLEET))
    lic = dict(U.LICENSE, status="VALID", valid=True, ui_level="admin", strategies=["*"])
    pg.route("**/system/license", lambda r: r.fulfill(json=lic))

    def meters(route):
        q = urllib.parse.unquote(route.request.url.split("strategies=")[-1].split("&")[0])
        route.fulfill(json={"ok": True, "meters": {s: METERS[s] for s in q.split(",") if s in METERS}, "errors": {}})
    pg.route("**/api/replay/meters*", meters)
    pg.route("**/api/replay/find*", lambda r: r.fulfill(status=404, json={"detail": "no trade"}))
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_selector("[data-strategy=IC_V2] [data-testid=calm-meter]", timeout=60000)
    pg.click("[data-strategy=IC_V2] [data-testid=calm-meter]")
    pg.wait_for_selector("[data-strategy=IC_V2] [data-testid=leg-bar]", timeout=5000)
    return ctx, pg


BARS = """[...document.querySelectorAll('[data-strategy=IC_V2] [data-testid=leg-bar]')].map(e => ({
  leg: e.dataset.leg, side: e.dataset.side, pct: +e.dataset.pct, title: e.getAttribute('title'),
  word: e.querySelector('[data-testid=leg-bar-pct]').textContent, text: e.innerText.replace(/\\s+/g, ' '),
  w: e.getBoundingClientRect().width }))"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    ctx, pg = open_dash(b)
    bars = pg.evaluate(BARS)
    by = {x["leg"]: x for x in bars}
    why = pg.inner_text("[data-strategy=IC_V2] [data-testid=calm-why]")
    check("TC-LEGBAR-0101", [x["leg"] for x in bars] == ["NIFTY25300PE", "NIFTY25600CE", "NIFTY26250CE"]
          and "basket MTM" in why and "time exit 15:28" in why and "of the way" not in why,
          f"one bar per leg (B 25600CE's SL and TP fold into one); the other items stay as text ({[x['leg'] for x in bars]})")
    ce, pe = by.get("NIFTY25600CE", {}), by.get("NIFTY25300PE", {})
    check("TC-LEGBAR-0102", ce.get("side") == "sl" and ce.get("word") == "SL 10%" and "SL 60.83" in ce.get("text", "")
          and "TP 173.80" in ce.get("text", ""),
          f"bought CE below entry: grows toward SL, 10% of the way, SL and TP prices at the ends ({ce.get('word')}, {ce.get('text')})")
    check("TC-LEGBAR-0103", pe.get("side") == "tp" and pe.get("word") == "TP 4%" and "SL 127.37" in pe.get("text", ""),
          f"sold PE below entry is in profit: grows toward its right end 0 ({pe.get('word')}, {pe.get('text')})")
    hit = by.get("NIFTY26250CE", {})
    check("TC-LEGBAR-0104", hit.get("word") == "SL hit" and hit.get("side") == "sl",
          f"LTP through the SL reads 'SL hit' ({hit.get('word')})")
    check("TC-LEGBAR-0105", "23.57 away · 10% of the way" in ce.get("title", "") and "89.40 away" in ce.get("title", ""),
          "the old SL/TP text stays in the bar's tooltip")
    errs += pg._errors
    ctx.close()

    ctx, pg = open_dash(b, width=390, height=1600)
    bars = pg.evaluate(BARS)
    geo = pg.evaluate("({ sw: document.documentElement.scrollWidth, vw: window.innerWidth })")
    card = pg.evaluate("document.querySelector('[data-strategy=IC_V2]').getBoundingClientRect().width")
    check("TC-LEGBAR-0106", len(bars) == 3 and geo["sw"] <= geo["vw"] and all(x["w"] <= card for x in bars),
          f"phone 390 px: bars fit inside the card, no sideways scroll ({geo}, card {card:.0f}, bars {[round(x['w']) for x in bars]})")
    errs += pg._errors
    ctx.close()
    check("TC-LEGBAR-0107", not errs, f"no page errors ({errs[:2]})")
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks" + (f" — {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)
