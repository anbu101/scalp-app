"""
test_ui_lvb_nav.py — LVB_SETTINGS_20261117 + NAV_CENTER_20261117, headless Chrome on the BUILT frontend.

Tripwire (LVB_V1) can be switched OFF / back on from Settings (its Levels page is
hidden while it is OFF and flat, TD-009). The top menu sits at the centre of the
window and never overlaps the brand or the right cluster.

The real /api/strategies/visibility route runs; POST /api/lvb_v1/config is a
fixture that flips the mode the route reads. Throw-away HOME, port 47321.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_lvb_nav.py   [CHROME=/path/to/chrome]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
import app.api.strategy_visibility_routes as V                         # noqa: E402

U.app.include_router(V.router)
U.app.router.routes.insert(0, U.app.router.routes.pop())
MODES = {"LVB_V1": "PAPER"}
V._mode_of = lambda sid: MODES.get(sid, "PAPER")
V._activity = lambda: {"open": set(), "today": set()}

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or next((p for p in ("/usr/bin/google-chrome",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if os.path.exists(p)), None)
BASE = "http://127.0.0.1:47321/"
SETTINGS = U.app_settings_api.SETTINGS_FILE
POSTS = []
fails = []
N = [0]


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def wait_for(fn, timeout=12.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if fn():
                return True
        except Exception as e:
            wait_for.last = repr(e)[:200]
        time.sleep(0.25)
    return False


def lvb_config(route):
    req = route.request
    if req.method == "POST":
        body = json.loads(req.post_data or "{}")
        POSTS.append(body)
        MODES["LVB_V1"] = body.get("trade_execution_mode")
        route.fulfill(json={"ok": True, "mode": MODES["LVB_V1"]})
    else:
        route.fulfill(json={})


def open_app(browser, *, width=1500, path="", angel=False, pages=True):
    U.LICENSE["ui_level"] = "admin"
    U.LICENSE["strategies"] = ["*"]
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"pages": {"backtest": pages, "crypto_lab": pages, "tv_alerts": pages}}))
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.route("**/api/lvb_v1/config", lvb_config)
    pg.route("**/api/config?strategy_id=LVB_V1", lambda r: r.fulfill(json={"config": {"trade_execution_mode": MODES["LVB_V1"]}}))
    if angel:   # worst case for width: both account chips with balances
        pg.route("**/api/acc2/status", lambda r: r.fulfill(json={"configured": True, "connected": True}))
        pg.route("**/api/acc2/funds", lambda r: r.fulfill(json={"available": 12345678}))
        pg.route("**/account/balance*", lambda r: r.fulfill(json={"net": 98765432}))
    pg.goto(BASE + path, wait_until="domcontentloaded")
    wait_for(lambda: pg.locator("nav a[href='#/settings']").first.inner_text().strip() != "", 30)
    time.sleep(1.5)
    return ctx, pg


def nav(pg):   # one atomic read: a link vanishing mid-read must not stall the check
    return pg.evaluate("[...document.querySelectorAll('nav a')].map(a => a.getAttribute('href'))")


def box(pg, sel):
    return pg.locator(sel).first.bounding_box()


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    errs = []

    # ── Tripwire on the Settings page ──────────────────────────────────
    ctx, pg = open_app(browser, path="#/settings")
    rail = pg.get_by_role("button", name="LVB V1")
    check("TC-LVBS-0001", wait_for(lambda: rail.count() >= 1) and "#/levels" in nav(pg),
          "admin Settings lists LVB V1 (Tripwire); Levels shown while it is PAPER")
    rail.first.click()
    wait_for(lambda: pg.locator("[data-testid=lvb-settings-note]").count() == 1)
    pg.get_by_role("button", name="⏸ Off", exact=True).first.click()
    pg.get_by_role("button", name="Save", exact=True).first.click()
    gone = wait_for(lambda: "#/levels" not in nav(pg)
                    and "hidden" in pg.locator("[data-testid=lvb-settings-note]").inner_text(), 20)
    note = pg.locator("[data-testid=lvb-settings-note]").inner_text()
    check("TC-LVBS-0002", POSTS[-1:] == [{"trade_execution_mode": "OFF"}] and gone and "hidden" in note,
          f"Mode Off + Save → POST /api/lvb_v1/config OFF; Levels leaves the nav within seconds; note says how to bring it back ({POSTS[-1:]}, gone={gone}, err={getattr(wait_for, 'last', None)}, note={note[:60]!r}, nav={nav(pg)})")
    pg.get_by_role("button", name="🧪 Paper", exact=True).first.click()
    pg.get_by_role("button", name="Save", exact=True).first.click()
    back = wait_for(lambda: "#/levels" in nav(pg), 20)
    check("TC-LVBS-0003", POSTS[-1:] == [{"trade_execution_mode": "PAPER"}] and back
          and pg.locator("[data-testid=lvb-settings-note] a[href='#/levels']").count() == 1,
          "Mode Paper + Save → Levels back in the nav; note links to the Levels page")
    errs += pg._errors
    ctx.close()

    # ── menu centred, no overlap ───────────────────────────────────────
    def layout(pg):
        b = box(pg, "nav [data-testid=nav-bar] > div:first-child")
        m, r = box(pg, "[data-testid=nav-menu]"), box(pg, "[data-testid=nav-right]")
        return b, m, r, pg.locator("[data-testid=nav-bar]").get_attribute("data-centring")

    for tc, width, angel, pages in (("TC-NAV-0001", 1920, False, True), ("TC-NAV-0002", 1440, False, False)):
        ctx, pg = open_app(browser, width=width, angel=angel, pages=pages)
        b, m, r, mode = layout(pg)
        mid = m["x"] + m["width"] / 2
        ok = b["x"] + b["width"] <= m["x"] and m["x"] + m["width"] <= r["x"]
        ok = ok and m["x"] - (b["x"] + b["width"]) >= 24 and r["x"] - (m["x"] + m["width"]) >= 24   # comfortable, not touching
        check(tc, mode == "exact" and abs(mid - width / 2) <= 2 and ok,
              f"{width}px{', 3 optional pages off' if not pages else ''}: room to spare → menu centre {mid:.0f} "
              f"= window centre {width / 2:.0f} ({mode}); no overlap {ok}")
        if tc == "TC-NAV-0001":
            pg.screenshot(path=os.environ.get("NAV_SHOT", "/tmp/nav_1920.png"), clip={"x": 0, "y": 0, "width": width, "height": 60})
        errs += pg._errors
        ctx.close()

    for tc, width in (("TC-NAV-0003", 1920), ("TC-NAV-0004", 1470)):
        ctx, pg = open_app(browser, width=width, angel=True, pages=True)
        b, m, r, mode = layout(pg)
        lgap, rgap = m["x"] - (b["x"] + b["width"]), r["x"] - (m["x"] + m["width"])
        exact = abs(m["x"] + m["width"] / 2 - width / 2) <= 2
        ok = lgap >= 0 and rgap >= 0 and r["x"] + r["width"] <= width + 1
        check(tc, ok and min(lgap, rgap) >= 24 and (exact if mode == "exact" else abs(lgap - rgap) <= 2),
              f"{width}px, every page on + both account chips: {mode} — gaps brand→menu {lgap:.0f}px, menu→right {rgap:.0f}px; "
              f"no overlap / overflow {ok}")
        if tc == "TC-NAV-0004":
            pg.screenshot(path=os.environ.get("NAV_SHOT2", "/tmp/nav_1470.png"), clip={"x": 0, "y": 0, "width": width, "height": 60})
        errs += pg._errors
        ctx.close()

    # P&L digits change → exact-centred menu does not move
    ctx, pg = open_app(browser, width=1920, pages=False)
    x0 = box(pg, "[data-testid=nav-menu]")["x"]
    pg.evaluate("document.querySelector('[data-testid=nav-right]').insertAdjacentHTML('afterbegin', '<span style=\"width:120px;display:inline-block\"></span>')")
    time.sleep(0.5)
    x1 = box(pg, "[data-testid=nav-menu]")["x"]
    check("TC-NAV-0005", abs(x1 - x0) < 1, f"right cluster grows 24px (a bigger P&L) → exact-centred menu stays put ({x0:.0f} → {x1:.0f})")
    errs += pg._errors
    ctx.close()

    check("TC-NAV-0006", not errs, f"no page errors ({errs[:2]})")
    browser.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
