"""
test_ui_index.py — INDEX_PATH_20261117: NIFTY / BANKNIFTY drawn behind the Dashboard's MTM chart
(option C) in a real browser on the BUILT frontend. Scenes: calm_fixture (index lines 09:15 →
13:05, live badge feed). The route itself: backend/app/api/test_index_path.py.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_index.py   [CHROME=/path/to/chrome]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
from dash_fixture import FLEET                                         # noqa: E402
from calm_fixture import INDICES, MARKET, PRE_INDICES, pre_open        # noqa: E402

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


def open_dash(b, fleet=FLEET, indices=INDICES, width=1440, height=1100, clock="2026-11-17T13:05:00+05:30", keep_storage=False, ctx=None):
    SET.parent.mkdir(parents=True, exist_ok=True)
    SET.write_text(json.dumps({"dashboard_layout": "modern"}))
    ctx = ctx or b.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors, pg._idx = [], []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.on("request", lambda r: pg._idx.append(r.url) if "/api/fleet/indices" in r.url else None)
    pg.clock.set_fixed_time(clock)
    if not keep_storage:
        pg.add_init_script("if (!sessionStorage.getItem('t')) { localStorage.clear(); sessionStorage.setItem('t', '1'); }")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=fleet))
    pg.route("**/api/fleet/indices", lambda r: r.fulfill(json=indices))
    pg.route("**/market_indices", lambda r: r.fulfill(json=MARKET))
    pg.route("**/system/license", lambda r: r.fulfill(json=dict(U.LICENSE, status="VALID", valid=True, ui_level="admin", strategies=["*"])))
    pg.route("**/api/replay/**", lambda r: r.fulfill(status=404, json={"detail": "no trade"}))
    pg.goto(BASE, wait_until="domcontentloaded")
    return ctx, pg


def overlays(pg):
    return pg.evaluate("[...document.querySelectorAll('[data-testid=calm-chart] [data-overlay]')].map(p => p.dataset.overlay)")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    ctx, pg = open_dash(b)
    pg.wait_for_selector("[data-testid=calm-index-chips] [data-index=NIFTY]", timeout=60000)
    pg.wait_for_timeout(1500)
    chips = pg.inner_text("[data-testid=calm-index-chips]").replace("\n", " ")
    check("TC-IDX-0001", "NIFTY 25,492.70 +0.80%" in chips and "BANKNIFTY 61,982.40 +0.66%" in chips,
          f"chips above the MTM chart: price and % from yesterday's close ({chips})")
    check("TC-IDX-0002", pg.get_attribute("[data-index=NIFTY]", "aria-pressed") == "true" and overlays(pg) == ["NIFTY"]
          and "0%" in pg.inner_text("[data-testid=calm-chart-pct-axis]"),
          f"NIFTY drawn behind the MTM line by default, on its own % scale ({overlays(pg)})")
    lines = pg.evaluate("[...document.querySelectorAll('[data-testid=calm-chart] svg path[data-line]')].length")
    check("TC-IDX-0003", lines == 1 and "Dashed: NIFTY % from yesterday's close" in pg.inner_text("[data-testid=calm-chart]"),
          "the MTM line is unchanged and the dashed line is explained under the chart")
    box = pg.locator("[data-testid=calm-chart] svg").bounding_box()
    pg.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
    pg.wait_for_timeout(300)
    tip = pg.inner_text("[data-testid=calm-chart-tip]") if pg.locator("[data-testid=calm-chart-tip]").count() else ""
    check("TC-IDX-0004", "Live" in tip and "NIFTY 25," in tip and "%" in tip, f"hover: MTM and NIFTY (price, %) at the same minute ({tip.replace(chr(10), ' · ')})")
    pg.mouse.move(5, 5)
    pg.click("[data-index=BANKNIFTY]")
    pg.wait_for_timeout(300)
    two = overlays(pg)
    pg.click("[data-index=NIFTY]")
    pg.wait_for_timeout(300)
    one = overlays(pg)
    check("TC-IDX-0005", two == ["NIFTY", "BANKNIFTY"] and one == ["BANKNIFTY"], f"tap a chip to add / remove its line ({two} → {one})")
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_selector("[data-testid=calm-index-chips] [data-index=NIFTY]", timeout=60000)
    pg.wait_for_timeout(1200)
    check("TC-IDX-0006", overlays(pg) == ["BANKNIFTY"], f"the choice is remembered ({overlays(pg)})")
    n0 = len(pg._idx)
    pg.wait_for_timeout(10000)
    check("TC-IDX-0007", n0 >= 1 and len(pg._idx) - n0 == 0, f"index line read once, then every 30 s ({n0} at load, {len(pg._idx) - n0} in 10 s)")
    check("TC-IDX-0008", "▲" not in pg.inner_text("[data-testid=calm-root]"), "the old index badges are gone (the chips replace them)")
    errs += pg._errors
    ctx.close()

    # before the open: no line yet, chips show yesterday's close
    ctx, pg = open_dash(b, fleet=pre_open(), indices=PRE_INDICES, clock="2026-11-17T08:53:00+05:30")
    pg.wait_for_selector("[data-testid=calm-index-chips] [data-index=NIFTY]", timeout=60000)
    pg.wait_for_timeout(1200)
    check("TC-IDX-0009", pg.locator("[data-testid=calm-chart]").count() == 0, "before the open: chips only, no empty chart")
    errs += pg._errors
    ctx.close()

    # phone
    ctx, pg = open_dash(b, width=390, height=844)
    pg.wait_for_selector("[data-testid=calm-index-chips] [data-index=NIFTY]", timeout=60000)
    pg.wait_for_timeout(1200)
    geo = pg.evaluate("({sw: document.documentElement.scrollWidth, vw: innerWidth, r: Math.round(document.querySelector('[data-testid=calm-index-chips]').getBoundingClientRect().right)})")
    check("TC-IDX-0010", geo["sw"] <= 390 and geo["r"] <= 390 and overlays(pg) == ["NIFTY"], f"phone: chips wrap inside 390 px, NIFTY on the chart {geo}")
    errs += pg._errors
    ctx.close()

    # ── SELECT_ACCENT_20261117 ── selected tab / chip / filter use the theme's accent (as the selected card does), not white
    ACC = """(() => { const t = document.createElement('i'); t.style.color = 'var(--c-primary)'; document.body.appendChild(t);
      const acc = getComputedStyle(t).color; t.remove();
      const bc = (q) => { const e = document.querySelector(q); return e ? getComputedStyle(e).borderTopColor : null; };
      return { acc, today: bc('[data-period=today]'), week: bc('[data-period=week]'), nifty: bc('[data-index=NIFTY]'),
               bank: bc('[data-index=BANKNIFTY]'), fAll: bc('[data-filter=ALL]'), fLive: bc('[data-filter=LIVE]'), ink: getComputedStyle(document.body).color }; })()"""
    seen = {}
    for th in ("dark", "arcade", "light"):
        SET.write_text(json.dumps({"dashboard_layout": "modern", "theme": th}))
        ctx = b.new_context(viewport={"width": 1440, "height": 1100}, timezone_id="Asia/Kolkata")
        ctx2, pg = open_dash(b, ctx=ctx, keep_storage=True)
        SET.write_text(json.dumps({"dashboard_layout": "modern", "theme": th}))
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_selector("[data-testid=calm-index-chips] [data-index=NIFTY]", timeout=60000)
        pg.wait_for_timeout(1200)
        seen[th] = pg.evaluate(ACC)
        errs += pg._errors
        ctx.close()
    ok = all(v["today"] == v["acc"] and v["nifty"] == v["acc"] and v["week"] != v["acc"] and v["bank"] != v["acc"] and v["fAll"] == v["acc"] and v["fLive"] != v["acc"] for v in seen.values())
    check("TC-IDX-0024", ok and len({v["acc"] for v in seen.values()}) >= 2,
          f"selected Today tab, NIFTY chip and All filter take the theme's accent colour, unselected ones don't ({ {k: (v['acc'], v['today'], v['nifty']) for k, v in seen.items()} })")
    check("TC-IDX-0011", not errs, f"no page errors ({errs[:2]})")
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
