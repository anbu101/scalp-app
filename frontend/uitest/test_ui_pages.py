"""
test_ui_pages.py — PAGE_VISIBILITY_20261117 (TD-020), headless Chrome on the BUILT frontend.

Backtest, Crypto Lab and TV Alerts are hidden by default for every user (admin
included) and are switched on in Settings → App Settings → Pages. Real
/api/app/settings route (ui_server.py), throw-away HOME, port 47321 (close the app).

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_pages.py   [CHROME=/path/to/chrome]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or next((p for p in ("/usr/bin/google-chrome",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if os.path.exists(p)), None)
BASE = "http://127.0.0.1:47321/"
SETTINGS = U.app_settings_api.SETTINGS_FILE
OPTIONAL = {"/backtest": "Backtest", "/crypto-lab": "Crypto Lab", "/tv-alerts": "TV Alerts"}
fails = []


def check(tc, cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def wait_for(fn, timeout=12.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def set_pages(pages):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    cur = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if pages is None:
        cur.pop("pages", None)
    else:
        cur["pages"] = pages
    SETTINGS.write_text(json.dumps(cur))


def open_app(browser, *, admin, mobile=False):
    U.LICENSE["ui_level"] = "admin" if admin else "user"
    U.LICENSE["strategies"] = ["*"] if admin else ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1"]
    ctx = browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1400, "height": 900})
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.goto(BASE, wait_until="domcontentloaded")
    wait_for(lambda: pg.locator("nav a[href='#/settings']").count() > 0, 20)
    time.sleep(1.0)
    return ctx, pg


def nav_paths(pg):
    return [a.get_attribute("href").lstrip("#") for a in pg.locator("nav a").all()]


def shown(pg):
    hs = nav_paths(pg)
    return {p for p in OPTIONAL if p in hs}


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    # TC-PAGES-0008 fresh settings: admin sees none of the three
    set_pages(None)
    ctx, pg = open_app(browser, admin=True)
    check("TC-PAGES-0008", shown(pg) == set() and "/levels" in nav_paths(pg),
          f"admin, no 'pages' saved → no Backtest / Crypto Lab / TV Alerts in the nav (nav: {nav_paths(pg)})")

    # TC-PAGES-0009 typed URL of a hidden page → Dashboard, page not rendered
    pg.goto(BASE + "#/backtest")
    time.sleep(1.5)
    body = pg.locator("body").inner_text()
    check("TC-PAGES-0009", "Backtest" not in [l.strip() for l in body.splitlines()][:3] and
          pg.locator("nav a[href='#/backtest']").count() == 0,
          "admin opens #/backtest while it is off → lands on the Dashboard")

    # TC-PAGES-0010 App Settings shows three switches, all off; turning Backtest on shows it in the nav
    pg.goto(BASE + "#/settings")
    wait_for(lambda: pg.get_by_text("App Settings", exact=True).count() > 0)
    pg.get_by_text("App Settings", exact=True).first.click()
    ok = wait_for(lambda: pg.locator("[data-testid=pages-rows]").count() == 1)
    rows = pg.locator("[data-testid=pages-rows] [role=switch]")
    states = [rows.nth(i).get_attribute("aria-checked") for i in range(rows.count())] if ok else []
    titles = pg.locator("[data-testid=pages-rows]").inner_text() if ok else ""
    check("TC-PAGES-0010", ok and states == ["false"] * 3 and all(f"Show {v}" in titles for v in OPTIONAL.values()),
          f"admin App Settings → Pages: Show Backtest / Crypto Lab / TV Alerts, all off ({states})")
    if ok:
        rows.nth(0).click()
    got = wait_for(lambda: "/backtest" in shown(pg))
    saved = json.loads(SETTINGS.read_text()).get("pages", {}) if SETTINGS.exists() else {}
    check("TC-PAGES-0011", got and shown(pg) == {"/backtest"} and saved.get("backtest") is True
          and saved.get("crypto_lab") is False and saved.get("tv_alerts") is False,
          f"switch Backtest on → it appears in the nav at once and is saved ({saved})")
    pg.locator("nav a[href='#/backtest']").click()
    time.sleep(1.5)
    check("TC-PAGES-0012", "#/backtest" in pg.url and pg.locator("nav a[href='#/backtest']").count() == 1,
          "Backtest page opens from the nav once switched on")
    errs = list(pg._errors)
    ctx.close()

    # TC-PAGES-0013 survives a restart (reload reads the saved file); switching off hides it again
    ctx, pg = open_app(browser, admin=True)
    check("TC-PAGES-0013", shown(pg) == {"/backtest"}, "after a reload Backtest is still shown, the other two hidden")
    set_pages({"backtest": False, "crypto_lab": True, "tv_alerts": True})
    ctx.close()
    ctx, pg = open_app(browser, admin=True)
    check("TC-PAGES-0014", shown(pg) == {"/crypto-lab", "/tv-alerts"},
          f"pages saved as Crypto Lab + TV Alerts on → exactly those two shown ({shown(pg)})")
    errs += pg._errors
    ctx.close()

    # TC-PAGES-0015 non-admin: nothing shows even when all three are saved on
    # (CRYPTO_ADMIN_20261117: Crypto Lab is admin-only too, so no page is open to non-admins)
    set_pages({"backtest": True, "crypto_lab": True, "tv_alerts": True})
    ctx, pg = open_app(browser, admin=False)
    check("TC-PAGES-0015", shown(pg) == set(),
          f"non-admin with all three saved on → none shown (all admin-only) ({nav_paths(pg)})")
    pg.goto(BASE + "#/settings")
    wait_for(lambda: pg.get_by_text("App Settings", exact=True).count() > 0)
    pg.get_by_text("App Settings", exact=True).first.click()
    time.sleep(1.5)
    check("TC-PAGES-0016", pg.locator("[data-testid=pages-rows]").count() == 0,
          "non-admin App Settings shows no Pages section (no switch it could use)")
    pg.goto(BASE + "#/crypto-lab")
    time.sleep(1.5)
    check("TC-PAGES-0017", pg.locator("nav a[href='#/crypto-lab']").count() == 0 and "#/crypto-lab" not in nav_paths(pg),
          "non-admin opens #/crypto-lab with it saved on → Dashboard, not in the nav")
    errs += pg._errors
    ctx.close()

    # TC-PAGES-0018 mobile tab bar: TV Alerts only when switched on
    set_pages(None)
    ctx, pg = open_app(browser, admin=True, mobile=True)
    off = [a.get_attribute("href") for a in pg.locator("nav a").all()]
    ctx.close()
    set_pages({"tv_alerts": True})
    ctx, pg = open_app(browser, admin=True, mobile=True)
    on = [a.get_attribute("href") for a in pg.locator("nav a").all()]
    check("TC-PAGES-0018", "#/tv-alerts" not in off and "#/tv-alerts" in on and on.index("#/levels") < on.index("#/settings")
          and off.index("#/levels") < off.index("#/settings"),
          f"mobile tab bar: TV Alerts hidden by default, shown when on; Levels stays before Settings ({off} / {on})")
    errs += pg._errors
    ctx.close()

    check("TC-PAGES-0019", not errs, f"no page errors ({errs[:2]})")
    browser.close()

print(f"\n{'FAIL' if fails else 'OK'}: {12 - len(fails)}/12 checks")
sys.exit(1 if fails else 0)
