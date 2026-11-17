"""
test_ui_dash_system.py — DASH_SYSTEM_20261117: the Modern Dashboard shell (header,
book summary, Fleet MTM chart, Traded today, today strip) uses ONLY the system in
components/fleet/dash.js, fits its widths, and keeps every interaction.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_dash_system.py   [CHROME=/path/to/chrome]
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

CHROME = os.environ.get("CHROME") or next((p for p in ("/usr/bin/google-chrome",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if os.path.exists(p)), None)
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
U.LICENSE.update(ui_level="admin", strategies=["*"])
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


def open_dash(browser, width=1470, height=1400, theme="dark"):
    SET.parent.mkdir(parents=True, exist_ok=True)
    SET.write_text(json.dumps({"dashboard_layout": "modern", "theme": theme}))
    ctx = browser.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.clock.set_fixed_time("2026-11-17T13:05:00+05:30")
    pg.add_init_script("localStorage.clear();localStorage.setItem('scalp.strategyHost.focusId','IC_V2')")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=FLEET))
    pg.route("**/system/license", lambda r: r.fulfill(json=dict(U.LICENSE, status="VALID")))
    pg.route("**/api/replay/**", lambda r: r.fulfill(status=404, json={"detail": "no trade"}))
    pg.goto(BASE, wait_until="domcontentloaded")
    wait_for(lambda: pg.locator(".flb-row, .flm-card").count() >= 6, 30)
    time.sleep(1.5)
    return ctx, pg


AUDIT = """
(() => {
  const parts = [...document.querySelectorAll('[data-dash-part]')];
  const vars = getComputedStyle(document.documentElement);
  const tok = {}; ['--c-text-primary','--c-text-muted','--c-profit','--c-loss','--c-border-light'].forEach(v => {
    const d = document.createElement('i'); d.style.color = vars.getPropertyValue(v); document.body.appendChild(d);
    tok[v] = getComputedStyle(d).color; d.remove(); });
  const sizes = new Set(), weights = new Set(), colors = new Set(), bad = [];
  const walk = (el) => {
    if (['STYLE','SCRIPT'].includes(el.tagName)) return;
    const cs = getComputedStyle(el);
    if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) {
      sizes.add(cs.fontSize); weights.add(cs.fontWeight); colors.add(cs.color); }
    if (cs.backgroundImage !== 'none') bad.push('gradient/image: ' + el.tagName);
    if (cs.boxShadow !== 'none' && !cs.boxShadow.includes('inset')) bad.push('shadow: ' + el.tagName + ' ' + cs.boxShadow);
    const bg = cs.backgroundColor;
    if (bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' && el.getAttribute('role') !== 'switch') bad.push('fill: ' + el.tagName + ' ' + bg + ' ' + (el.textContent||'').trim().slice(0,20));
    [...el.children].forEach(walk);
  };
  parts.forEach(walk);
  return { parts: parts.length, sizes: [...sizes], weights: [...weights], colors: [...colors], tok, bad };
})()
"""

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    errs = []

    ctx, pg = open_dash(browser)
    a = pg.evaluate(AUDIT)
    allowed = {a["tok"][k] for k in ("--c-text-primary", "--c-text-muted", "--c-profit", "--c-loss")}
    sizes = sorted(a["sizes"], key=lambda s: float(s[:-2]))
    check("TC-DSYS-0001", a["parts"] >= 4 and set(sizes) <= {"11px", "13px", "15px", "20px", "32px"},
          f"shell text uses only the 5 type steps 11/13/15/20/32 ({sizes})")
    check("TC-DSYS-0002", set(a["weights"]) <= {"400", "600"}, f"only 2 weights ({sorted(a['weights'])})")
    check("TC-DSYS-0003", set(a["colors"]) <= allowed,
          f"text colours ⊆ ink / muted / up / down ({len(a['colors'])} used: {sorted(set(a['colors']) - allowed) or 'all tokens'})")
    check("TC-DSYS-0004", not a["bad"], f"no fills, outer shadows or gradients in the shell ({a['bad'][:3]})")

    # alignment of the two book blocks
    tops = pg.evaluate("""[...document.querySelectorAll('[data-book]')].map(b => [...b.children].map(c => Math.round(c.getBoundingClientRect().top)))""")
    check("TC-DSYS-0005", len(tops) == 2 and all(abs(x - y) <= 1 for x, y in zip(*tops)),
          f"Live and Paper blocks line up row for row ({tops})")

    # only the headline figures carry P&L colour
    up_dn = pg.evaluate("""(() => { const t = [getComputedStyle(document.documentElement).getPropertyValue('--c-profit'),
        getComputedStyle(document.documentElement).getPropertyValue('--c-loss')];
      const probe = (v) => { const d = document.createElement('i'); d.style.color = v; document.body.appendChild(d); const c = getComputedStyle(d).color; d.remove(); return c; };
      const cs = t.map(probe);
      return [...document.querySelectorAll('[data-dash-part] *')].filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
        && cs.includes(getComputedStyle(e).color)).length; })()""")
    check("TC-DSYS-0006", up_dn <= 14, f"P&L colour only on headline figures: {up_dn} coloured texts (was 50)")

    # table fits
    fit = pg.evaluate("""(() => { const t = document.querySelector('.flb-row').closest('table');
      const lastCells = [...document.querySelectorAll('.flb-row td:last-child')];
      return { tw: Math.round(t.getBoundingClientRect().width), cw: Math.round(t.parentElement.getBoundingClientRect().width),
        lastW: Math.min(...lastCells.map(c => c.getBoundingClientRect().width)), doc: document.documentElement.scrollWidth }; })()""")
    check("TC-DSYS-0007", fit["tw"] <= fit["cw"] and fit["lastW"] >= 160 and fit["doc"] <= 1470,
          f"1470px: Traded today fits its card, no sideways scroll; Last event ≥160px wide ({fit})")
    errs += pg._errors

    # interactions kept
    rows = pg.locator(".flb-row")
    rows.nth(3).click()
    time.sleep(0.6)
    check("TC-DSYS-0008", rows.nth(3).get_attribute("aria-current") == "true" and "time strangle" in pg.locator("[data-testid=today-strip]").inner_text().lower(),
          "click a row → it is marked and the today strip follows it")
    pg.locator(".flb-chip").first.click()
    time.sleep(0.6)
    check("TC-DSYS-0009", pg.locator(".flb-chip").first.get_attribute("aria-current") == "true", "click an idle name → focused")
    pg.get_by_role("button", name="Grid", exact=True).click()
    time.sleep(0.5)
    grid = pg.locator(".flc-cell").count()
    pg.get_by_role("button", name="Fleet", exact=True).click()
    time.sleep(0.5)
    pg.locator(".flc-chip").first.click()
    time.sleep(0.5)
    labels = pg.evaluate("[...document.querySelectorAll('[data-dash-part] svg text')].map(t => t.textContent)")
    check("TC-DSYS-0010", grid == 6 and pg.locator(".flc-chip").first.get_attribute("aria-pressed") == "true"
          and any("·" in l for l in labels),
          f"Grid shows 6 small multiples; an overlay toggles on and is labelled at its line end ({[l for l in labels if '·' in l]})")
    pg.locator("button[aria-expanded]", has_text="Fleet MTM").click()
    time.sleep(0.5)
    check("TC-DSYS-0011", pg.locator("button[aria-expanded]", has_text="Fleet MTM").get_attribute("aria-expanded") == "false"
          and pg.locator("[data-dash-part] svg text", has_text="09:15").count() == 0,
          "collapse the chart → plot hidden, title stays")
    errs += pg._errors
    ctx.close()

    # 1280px laptop
    ctx, pg = open_dash(browser, width=1280)
    fit = pg.evaluate("""(() => { const t = document.querySelector('.flb-row').closest('table');
      return { tw: Math.round(t.getBoundingClientRect().width), cw: Math.round(t.parentElement.getBoundingClientRect().width), doc: document.documentElement.scrollWidth }; })()""")
    check("TC-DSYS-0012", fit["tw"] <= fit["cw"] and fit["doc"] <= 1280, f"1280px: no sideways scroll anywhere ({fit})")
    errs += pg._errors
    ctx.close()

    # phone
    ctx, pg = open_dash(browser, width=390, height=2400)
    ph = pg.evaluate("""(() => { const btn = [...document.querySelectorAll('button[aria-expanded]')].find(b => b.textContent.includes('Fleet MTM'));
      const grid = [...document.querySelectorAll('button[aria-pressed]')].find(b => b.textContent.trim() === 'Grid');
      return { doc: document.documentElement.scrollWidth,
        sameLine: btn && grid ? Math.abs(btn.getBoundingClientRect().top - grid.getBoundingClientRect().top) < 8 : false }; })()""")
    a = pg.evaluate(AUDIT)
    check("TC-DSYS-0013", ph["doc"] <= 390 and ph["sameLine"] and set(a["sizes"]) <= {"11px", "13px", "15px", "20px", "32px"},
          f"phone 390px: nothing wider than the screen, chart title and Fleet/Grid on one line, same 5 sizes ({ph}, {sorted(a['sizes'])})")
    errs += pg._errors
    ctx.close()

    # themes: tokens follow the theme
    seen = {}
    for th in ("dark", "light", "terminal", "kolam", "arcade", "thalapathy"):
        ctx, pg = open_dash(browser, theme=th)
        a = pg.evaluate(AUDIT)
        seen[th] = (a["tok"]["--c-text-primary"], set(a["colors"]) <= {a["tok"][k] for k in ("--c-text-primary", "--c-text-muted", "--c-profit", "--c-loss")}, not a["bad"])
        errs += pg._errors
        ctx.close()
    check("TC-DSYS-0014", all(v[1] and v[2] for v in seen.values()) and len({v[0] for v in seen.values()}) >= 3,
          f"every theme: text stays inside its own 4 text tokens, no fills; ink differs by theme ({ {k: v[0] for k, v in seen.items()} })")

    check("TC-DSYS-0015", not errs, f"no page errors ({errs[:2]})")
    browser.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
