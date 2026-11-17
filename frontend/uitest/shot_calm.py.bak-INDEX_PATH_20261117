"""
shot_calm.py — DASH_CALM_20261117: screenshots of the redesigned Dashboard on the BUILT
frontend with calm_fixture scenes, clock fixed at 13:05 IST.
    PYTHONPATH=../backend python3 uitest/shot_calm.py OUT_PREFIX [--width N]
Writes OUT_PREFIX-{both,paper,open,pre}-desktop.png and OUT_PREFIX-both-phone.png.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
from dash_fixture import FLEET                                         # noqa: E402
from calm_fixture import METERS, paper_only, pre_open                  # noqa: E402

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

_a = sys.argv[1:]
W = int(_a[_a.index("--width") + 1]) if "--width" in _a else 1440
OUT = next((a for i, a in enumerate(_a) if not a.startswith("--") and (i == 0 or _a[i - 1] != "--width")), "/tmp/calm")
CHROME = os.environ.get("CHROME") or "/usr/bin/google-chrome"
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
SET.parent.mkdir(parents=True, exist_ok=True)
SET.write_text(json.dumps({"dashboard_layout": "modern"}))
U.LICENSE.update(ui_level="admin", strategies=["*"])


def meters(route):
    q = route.request.url.split("strategies=")[-1].split("&")[0]
    ids = [s for s in __import__("urllib.parse").parse.unquote(q).split(",") if s]
    route.fulfill(json={"ok": True, "meters": {s: METERS[s] for s in ids if s in METERS}, "errors": {}})


def shoot(b, name, fleet, vp, clock="2026-11-17T13:05:00+05:30", expand=None, tall=2400):
    ctx = b.new_context(viewport=vp, timezone_id="Asia/Kolkata", device_scale_factor=1)
    pg = ctx.new_page()
    pg.clock.set_fixed_time(clock)
    pg.add_init_script("localStorage.clear();localStorage.setItem('scalp.strategyHost.focusId','TMA_V2');")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=fleet))
    pg.route("**/system/license", lambda r: r.fulfill(json=dict(U.LICENSE, status="VALID", valid=True)))
    pg.route("**/api/replay/meters*", meters)
    pg.route("**/api/replay/find*", lambda r: r.fulfill(status=404, json={"detail": "no trade"}))
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_selector("[data-testid=calm-hero]", timeout=60000)
    pg.wait_for_timeout(2500)
    if expand:
        pg.click(f"[data-strategy={expand}] [data-testid=calm-meter]")
        pg.wait_for_timeout(800)
    pg.set_viewport_size({"width": vp["width"], "height": tall})
    pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{OUT}-{name}.png")
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    shoot(b, "both-desktop", FLEET, {"width": W, "height": 900})
    shoot(b, "open-desktop", FLEET, {"width": W, "height": 900}, expand="TMA_V2")
    shoot(b, "paper-desktop", paper_only(), {"width": W, "height": 900})
    shoot(b, "pre-desktop", pre_open(), {"width": W, "height": 900}, clock="2026-11-17T08:53:00+05:30", tall=1500)
    shoot(b, "both-phone", FLEET, {"width": 390, "height": 844}, expand="TMA_V2", tall=2600)
    b.close()
print("ok")
