"""
shot_dashboard.py — full-page Dashboard screenshots on the BUILT frontend with
dash_fixture data, clock fixed at 13:05 IST.  Usage:
    PYTHONPATH=../backend python3 uitest/shot_dashboard.py OUT_PREFIX
Writes OUT_PREFIX-desktop.png (1470 wide), OUT_PREFIX-phone.png (390 wide) and
OUT_PREFIX-metrics.json (font sizes / colours actually rendered in the shell).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
from dash_fixture import FLEET                                         # noqa: E402

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dash"
CHROME = os.environ.get("CHROME") or "/usr/bin/google-chrome"
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
SET.parent.mkdir(parents=True, exist_ok=True)
SET.write_text(json.dumps({"dashboard_layout": "modern"}))
U.LICENSE.update(ui_level="admin", strategies=["*"])

METRICS_JS = """
(() => {
  // the Dashboard SHELL only (header, books, chart, Traded today, today strip) — not the strategy panels.
  // Before the rebuild there are no markers: take the root's first five blocks (same parts).
  let parts = [...document.querySelectorAll('[data-dash-part]')];
  if (!parts.length) {   // pre-rebuild build: find the page root from the date line, and the today strip by its text
    let el = [...document.querySelectorAll('div')].find(e => e.children.length === 0 && /^\w+day, \d+ \w+ \d{4}$/.test(e.textContent.trim()));
    while (el && el.parentElement && el.parentElement.children.length < 5) el = el.parentElement;
    const root = el ? el.parentElement : document.body;
    parts = [...root.children].slice(0, 5);
    const strip = [...document.querySelectorAll('span')].find(e => e.textContent.trim().startsWith('Today ·'));
    if (strip) parts.push(strip.closest('div').parentElement);
  }
  const sizes = {}, colors = {}, weights = {};
  const walk = (el) => {
    const cs = getComputedStyle(el);
    if (!['STYLE','SCRIPT'].includes(el.tagName) && el.childNodes && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) {
      sizes[cs.fontSize] = (sizes[cs.fontSize] || 0) + 1;
      colors[cs.color] = (colors[cs.color] || 0) + 1;
      weights[cs.fontWeight] = (weights[cs.fontWeight] || 0) + 1;
    }
    [...el.children].forEach(walk);
  };
  parts.forEach(walk);
  return { sizes, colors, weights };
})()
"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    for name, vp in (("desktop", {"width": 1470, "height": 900}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=vp, timezone_id="Asia/Kolkata", device_scale_factor=1)
        pg = ctx.new_page()
        pg.clock.set_fixed_time("2026-11-17T13:05:00+05:30")
        pg.add_init_script("localStorage.setItem('scalp.strategyHost.focusId','IC_V2');"
                           "localStorage.setItem('scalp.dashboard.fleetChartView','\"fleet\"');")
        pg.route("**/api/fleet/today", lambda r: r.fulfill(json=FLEET))
        pg.route("**/system/license", lambda r: r.fulfill(json=dict(U.LICENSE, status="VALID", valid=True)))
        pg.route("**/api/replay/**", lambda r: r.fulfill(status=404, json={"detail": "no trade"}))
        pg.goto(BASE, wait_until="domcontentloaded")
        time.sleep(6)
        # tall viewport instead of full_page: fixed bars stay at the real bottom, nothing overlays content
        pg.set_viewport_size({"width": vp["width"], "height": 2300 if name == "desktop" else 2600})
        time.sleep(1.5)
        pg.screenshot(path=f"{OUT}-{name}.png")
        if name == "desktop":
            m = pg.evaluate(METRICS_JS)
            open(f"{OUT}-metrics.json", "w").write(json.dumps(m, indent=1))
        ctx.close()
    b.close()
print("ok")
