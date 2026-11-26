"""
test_ui_debug_menu.py — DASH_TIDY_20261126, headless Chrome on the BUILT frontend.

The Debug pill (🛠 Debug → Debug Tools drawer) moved off the Dashboard to the
Settings page, and shows only for an admin licence. Throw-away HOME, port 47321.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_debug_menu.py   [CHROME=/path/to/chrome]
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
SET = U.app_settings_api.SETTINGS_FILE
fails, N, errs = [], [0], []


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def wait_for(fn, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def open_app(b, *, path="", admin=True, layout="modern"):
    SET.parent.mkdir(parents=True, exist_ok=True)
    SET.write_text(json.dumps({"dashboard_layout": layout}))
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.add_init_script("localStorage.clear();")
    lic = dict(U.LICENSE, status="VALID", valid=True, ui_level="admin" if admin else "user",
               strategies=["*"] if admin else ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1"])
    pg.route("**/system/license", lambda r: r.fulfill(json=lic))
    pg.goto(BASE + path, wait_until="domcontentloaded")
    wait_for(lambda: pg.locator("nav a[href='#/settings']").first.inner_text().strip() != "", 30)
    time.sleep(2.0)                       # the licence read settles the admin chrome
    return ctx, pg


def pills(pg):
    return pg.locator("[data-testid=debug-open]").count()


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    for tc, layout in (("TC-DBG-0001", "modern"), ("TC-DBG-0002", "classic"), ("TC-DBG-0003", "legacy")):
        ctx, pg = open_app(b, layout=layout)
        check(tc, pills(pg) == 0, f"admin Dashboard ({layout} layout): no Debug pill ({pills(pg)})")
        errs += pg._errors
        ctx.close()

    ctx, pg = open_app(b, path="#/settings")
    wait_for(lambda: pills(pg) == 1)
    n = pills(pg)
    opened = False
    if n == 1:
        pg.locator("[data-testid=debug-open]").click()
        opened = wait_for(lambda: pg.get_by_text("Debug Tools").first.is_visible(), 5)
    check("TC-DBG-0004", n == 1 and opened, f"admin Settings: one Debug pill ({n}); clicking it opens Debug Tools ({opened})")
    errs += pg._errors
    ctx.close()

    ctx, pg = open_app(b, path="#/settings", admin=False)
    check("TC-DBG-0005", pills(pg) == 0, f"user licence Settings: no Debug pill ({pills(pg)})")
    errs += pg._errors
    ctx.close()

    check("TC-DBG-0006", not errs, f"no page errors ({errs[:2]})")
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
