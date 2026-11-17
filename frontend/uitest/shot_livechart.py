"""
shot_livechart.py — screenshots of the Dashboard's Live chart (card + Full screen)
on the shared scene in livechart_scene.py (real replay route, synthetic corpus,
open Timberwolf spread, "now" 25 Feb 2025 13:05 IST).

    PYTHONPATH=../backend python3 uitest/shot_livechart.py OUT_PREFIX
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from livechart_scene import page, drawn, sync_playwright, CHROME       # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "/tmp/live"
TEST = "--test" in sys.argv
METRICS = """
(() => {
  const root = document.querySelector('[data-testid=live-chart-card]');
  if (!root) return null;
  const sizes = {}, colors = {}, weights = {}, radii = {};
  const walk = (el) => {
    if (['STYLE','SCRIPT','TITLE','title'].includes(el.tagName)) return;
    const cs = getComputedStyle(el);
    if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) {
      sizes[cs.fontSize] = (sizes[cs.fontSize] || 0) + 1; colors[cs.color] = (colors[cs.color] || 0) + 1;
      weights[cs.fontWeight] = (weights[cs.fontWeight] || 0) + 1; }
    if (cs.borderRadius !== '0px') radii[cs.borderRadius] = (radii[cs.borderRadius] || 0) + 1;
    [...el.children].forEach(walk);
  };
  walk(root);
  return { sizes, colors, weights, radii, h: Math.round(root.getBoundingClientRect().height),
           svgs: root.querySelectorAll('svg').length, candles: root.querySelectorAll('svg rect').length };
})()
"""


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    errs = []
    ctx, pg = page(b, {"width": 1470, "height": 2600})
    t0 = time.time()
    while time.time() - t0 < 60 and drawn(pg) < 50:
        time.sleep(1)
    time.sleep(3)
    card = pg.locator("[data-testid=live-chart-card]")
    card.scroll_into_view_if_needed()
    bb = card.bounding_box()
    pad = 16
    pg.screenshot(path=f"{OUT}-card.png", clip={"x": 0, "y": max(0, bb["y"] - pad), "width": 1470, "height": bb["height"] + 2 * pad})
    m = pg.evaluate(METRICS)
    open(f"{OUT}-metrics.json", "w").write(json.dumps(m, indent=1))
    # full screen
    pg.locator("[data-testid=live-chart-card] button", has_text="Full screen").first.click()
    time.sleep(6)
    pg.set_viewport_size({"width": 1470, "height": 920})
    time.sleep(2)
    pg.screenshot(path=f"{OUT}-full.png")
    errs += pg._errors
    ctx.close()
    # phone
    ctx, pg = page(b, {"width": 390, "height": 3200})
    t0 = time.time()
    while time.time() - t0 < 60 and pg.locator("[data-testid=live-chart-card]").count() == 0:
        time.sleep(1)
    time.sleep(6)
    if pg.locator("[data-testid=live-chart-card]").count():
        bb = pg.locator("[data-testid=live-chart-card]").bounding_box()
        pg.screenshot(path=f"{OUT}-phone.png", clip={"x": 0, "y": max(0, bb["y"] - 16), "width": 390, "height": min(bb["height"] + 32, 1400)})
    else:
        pg.screenshot(path=f"{OUT}-phone.png", full_page=False)
    errs += pg._errors
    ctx.close()
    b.close()
print(json.dumps({"metrics": m, "errors": errs[:3]}, indent=1)[:1500])
if TEST:
    sys.exit(0 if (m and m["candles"] >= 50 and not errs) else 1)
