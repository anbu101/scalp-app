"""
test_ui_settings_others.py — SETTINGS_OTHERS_20261117, headless Chrome on the BUILT frontend.

Settings page rail and App Settings sound grid fold switched-off strategies
(OFF + flat + no trade today, two or more) into one "Others" group.

Real /api/strategies/visibility (seams set by the test) and real
/api/app/settings; strategy configs are ui_server fixtures, a mode save is
captured so the route sees it. Throw-away HOME, port 47321.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_settings_others.py   [CHROME=/path/to/chrome]
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
OFF = set()
V._mode_of = lambda sid: "OFF" if sid in OFF else U.MODES.get(sid, "PAPER")
V._activity = lambda: {"open": set(), "today": set()}

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or next((p for p in ("/usr/bin/google-chrome",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if os.path.exists(p)), None)
BASE = "http://127.0.0.1:47321/"
SETTINGS = U.app_settings_api.SETTINGS_FILE
NINE = {"SCALP_V1", "SCALP_V3", "SCALP_V5", "BB_V1", "BB_V2", "HA_V1", "IC_V1", "TMA_V1", "LVB_V1"}
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
        except Exception:
            pass
        time.sleep(0.25)
    return False


def scenario(off):
    OFF.clear()
    OFF.update(off)
    for k in list(U.MODES):
        if U.MODES[k] == "OFF":
            del U.MODES[k]
    for k in off:
        U.MODES[k] = "OFF"


def cfg_route(route):
    req = route.request
    sid = req.url.split("strategy_id=")[-1].split("&")[0]
    if req.method == "POST":
        try:
            body = json.loads(req.post_data or "{}")
            mode = (body.get("config") or body).get("trade_execution_mode")
            if mode == "OFF":
                OFF.add(sid)
            elif mode:
                OFF.discard(sid)
            U.MODES[sid] = mode
        except Exception:
            pass
        route.fulfill(json={"ok": True})
    else:
        route.fulfill(json={"config": {"trade_execution_mode": "OFF" if sid in OFF else U.MODES.get(sid, "PAPER")}})


def open_app(browser, path="#/settings", mobile=False, keep_storage=None):
    U.LICENSE.update(ui_level="admin", strategies=["*"])
    ctx = keep_storage or browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1500, "height": 1100})
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.route("**/api/config?strategy_id=*", cfg_route)
    pg.route("**/api/lvb_v1/config", lambda r: r.fulfill(json={"ok": True}))

    def save_cfg(route):   # real save path: POST /api/save_config {strategy_id, config}
        body = json.loads(route.request.post_data or "{}")
        sid, mode = body.get("strategy_id"), (body.get("config") or {}).get("trade_execution_mode")
        (OFF.add if mode == "OFF" else OFF.discard)(sid)
        U.MODES[sid] = mode
        route.fulfill(json={"success": True})
    pg.route("**/api/save_config", save_cfg)
    pg.goto(BASE + path, wait_until="domcontentloaded")
    wait_for(lambda: pg.locator("[data-testid=settings-others], text=Strategy Settings").count() > 0, 30)
    time.sleep(2.0)
    return ctx, pg


def rail_ids(pg, inside=False):
    sel = "[data-testid=settings-others-list] button" if inside else "button"
    out = []
    for t in pg.locator(sel).all_inner_texts():
        for line in t.splitlines():
            if "·" in line and line.split("·")[0].strip().isupper():
                out.append(line.split("·")[0].strip())
    return out


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    errs = []

    # ── Settings rail ──────────────────────────────────────────────────
    scenario(NINE)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"audio_rules": {"SCALP_V1": {"PAPER": False, "LIVE": True}}}))
    ctx, pg = open_app(browser)
    oth = pg.locator("[data-testid=settings-others]")
    ok = wait_for(lambda: oth.count() == 1)
    lst = pg.locator("[data-testid=settings-others-list]")
    check("TC-SETO-0001", ok and "9 switched off" in oth.inner_text().lower() and oth.get_attribute("aria-expanded") == "false"
          and lst.get_attribute("data-open") == "false",
          "Settings rail: 9 switched-off strategies fold into one closed 'Others · 9 switched off' row")
    shown = [i for i in rail_ids(pg) if i not in rail_ids(pg, inside=True)]
    check("TC-SETO-0002", not (set(shown) & NINE) and "IC_V2" in shown,
          f"rail shows only active strategies above Others ({shown})")
    head = pg.locator("h2, [style*='font-size: 18']").all_inner_texts()
    check("TC-SETO-0003", pg.locator("[data-testid=settings-others-list]").get_attribute("data-open") == "false"
          and not any("Scalp V1" in h for h in head),
          "default selection is the first active strategy, not a switched-off one (group stays closed)")
    oth.click()
    ok = wait_for(lambda: lst.get_attribute("data-open") == "true")
    inside = rail_ids(pg, inside=True)
    check("TC-SETO-0004", ok and set(inside) == NINE and oth.get_attribute("aria-expanded") == "true",
          f"click Others → its 9 strategies slide open ({sorted(inside)})")
    lst.get_by_role("button").first.click()
    time.sleep(0.6)
    oth.click()
    time.sleep(0.6)
    check("TC-SETO-0005", lst.get_attribute("data-open") == "true",
          "while a strategy inside Others is selected the group cannot close")
    errs += pg._errors
    ctx.close()

    # remembered open state (nothing selected inside)
    ctx = browser.new_context(viewport={"width": 1500, "height": 1100})
    c1, pg = open_app(browser, keep_storage=ctx)
    pg.locator("[data-testid=settings-others]").click()
    time.sleep(0.5)
    pg.reload(wait_until="domcontentloaded")
    wait_for(lambda: pg.locator("[data-testid=settings-others]").count() == 1, 30)
    time.sleep(1.5)
    check("TC-SETO-0006", pg.locator("[data-testid=settings-others-list]").get_attribute("data-open") == "true",
          "an opened Others group is still open after a reload")
    errs += pg._errors
    ctx.close()

    # saving a strategy OFF moves it into Others right after Save
    ctx, pg = open_app(browser)
    pg.get_by_role("button", name="TSG V1").first.click()
    time.sleep(0.8)
    pg.get_by_role("button", name="⏸ Off", exact=True).first.click()
    pg.get_by_role("button", name="Save", exact=True).first.click()
    moved = wait_for(lambda: "TSG_V1" in rail_ids(pg, inside=True)
                     and "10 switched off" in pg.locator("[data-testid=settings-others]").inner_text().lower(), 15)
    check("TC-SETO-0007", moved and pg.locator("[data-testid=settings-others-list]").get_attribute("data-open") == "true",
          "TSG V1 saved as Off → moves into Others (10) at once; group open because it is selected")
    errs += pg._errors
    ctx.close()
    OFF.discard("TSG_V1")
    U.MODES["TSG_V1"] = "PAPER"

    # one switched-off strategy → no group
    scenario({"SCALP_V1"})
    ctx, pg = open_app(browser)
    time.sleep(1.0)
    check("TC-SETO-0008", pg.locator("[data-testid=settings-others]").count() == 0 and "SCALP_V1" in rail_ids(pg),
          "only ONE switched-off strategy → no Others group, it keeps its own rail row")
    errs += pg._errors
    ctx.close()

    # phone: chip row
    scenario(NINE)
    ctx, pg = open_app(browser, mobile=True)
    chip = pg.locator("[data-testid=settings-others]")
    ok = wait_for(lambda: chip.count() == 1)
    before = pg.locator("[data-testid=settings-others-list]").count()
    chip.click()
    time.sleep(0.5)
    check("TC-SETO-0009", ok and "Others · 9" in chip.inner_text() and before == 0
          and pg.locator("[data-testid=settings-others-list] button").count() == 9,
          "phone: 'Others · 9' chip; tapping it shows the 9 chips in a dashed group")
    errs += pg._errors
    ctx.close()

    # ── App Settings sound grid ────────────────────────────────────────
    SETTINGS.write_text(json.dumps({"audio_rules": {"SCALP_V1": {"PAPER": False, "LIVE": True}}}))
    ctx, pg = open_app(browser)
    pg.get_by_text("App Settings", exact=True).first.click()
    ok = wait_for(lambda: pg.locator("[data-testid=sound-others]").count() == 1)
    rows_main = pg.locator("[data-sound-row]").count() - pg.locator("[data-testid=sound-others-list] [data-sound-row]").count()
    gp = pg.locator("[data-testid=sound-others-paper]")
    check("TC-SETO-0010", ok and "9 switched off" in pg.locator("[data-testid=sound-others]").inner_text().lower()
          and pg.locator("[data-testid=sound-others-list]").get_attribute("data-open") == "false"
          and gp.get_attribute("aria-checked") == "mixed" and rows_main == 7,
          f"sound grid: 7 active rows + closed 'Others' row; its Paper switch shows MIXED (Scalp V1 paper off) "
          f"({gp.get_attribute('aria-checked')}, {rows_main} rows)")
    gp.click()
    time.sleep(1.0)
    rules = json.loads(SETTINGS.read_text()).get("audio_rules", {})
    check("TC-SETO-0011", all(rules.get(s, {}).get("PAPER") is True for s in NINE) and gp.get_attribute("aria-checked") == "true",
          "group Paper switch from mixed → every Others strategy's paper sound ON, saved")
    gp.click()
    time.sleep(1.0)
    rules = json.loads(SETTINGS.read_text()).get("audio_rules", {})
    check("TC-SETO-0012", all(rules.get(s, {}).get("PAPER") is False for s in NINE)
          and all(rules.get(s, {}).get("PAPER", True) is True for s in ("IC_V2", "TMA_V2", "TSG_V1"))
          and all(rules.get(s, {}).get("LIVE", True) is True for s in NINE),
          "again → all their paper sounds OFF; active strategies and the Live column untouched")
    pg.locator("[data-testid=sound-others-toggle]").click()
    ok = wait_for(lambda: pg.locator("[data-testid=sound-others-list]").get_attribute("data-open") == "true")
    check("TC-SETO-0013", ok and pg.locator("[data-testid=sound-others-list] [data-sound-row]").count() == 9,
          "click Others → the 9 rows open below it, each with its own switches")
    errs += pg._errors
    ctx.close()

    check("TC-SETO-0014", not errs, f"no page errors ({errs[:2]})")
    browser.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
