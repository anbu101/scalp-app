"""
test_ui_live_chart.py — LIVE_CHART_SYSTEM_20261117: the Dashboard's Live chart (and
the replay window it shares with Trades / Analytics / Backtest) on the Dashboard
system, behaviour unchanged. Real replay route on the synthetic scene
(livechart_scene.py): open Timberwolf spread, "now" 25 Feb 2025 13:05 IST.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_live_chart.py   [CHROME=/path/to/chrome]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from livechart_scene import page, wait_chart, drawn, sync_playwright, CHROME   # noqa: E402
import livechart_scene as S   # noqa: E402

fails = []
N = [0]


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


AUDIT = """
(sel) => {
  const root = document.querySelector(sel);
  if (!root) return null;
  const sizes = new Set(), weights = new Set();
  const walk = (el) => {
    if (['STYLE','SCRIPT','TITLE','title'].includes(el.tagName)) return;
    const cs = getComputedStyle(el);
    if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) { sizes.add(cs.fontSize); weights.add(cs.fontWeight); }
    [...el.children].forEach(walk);
  };
  walk(root);
  return { sizes: [...sizes], weights: [...weights] };
}
"""
CARD = "[data-testid=live-chart-card]"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    errs = []
    ctx, pg = page(b, {"width": 1470, "height": 2600})
    ok = wait_chart(pg)
    check("TC-LCHT-0001", ok, "the real replay route draws the open Timberwolf spread (candles on both panes)")
    a = pg.evaluate(AUDIT, CARD)
    sizes = sorted(a["sizes"], key=lambda s: float(s[:-2]))
    check("TC-LCHT-0002", set(sizes) <= {"11px", "13px", "15px", "20px"} and set(a["weights"]) <= {"400", "600"},
          f"card text on the type scale 11/13/15/20 and weights 400/600 ({sizes}, {sorted(a['weights'])})")
    hero = pg.locator(f"{CARD} [data-testid=replay-hero]")
    check("TC-LCHT-0003", hero.count() == 1 and "open P&L at LTP" in hero.inner_text() and "₹" in hero.inner_text(),
          f"one hero figure: open P&L at LTP ({hero.inner_text().replace(chr(10), ' ') if hero.count() else None})")
    title_count = pg.locator(CARD).get_by_text("Live chart · open position").count()
    check("TC-LCHT-0004", title_count == 0 and pg.locator(CARD).get_by_text("Live chart", exact=True).count() == 1,
          "the card is titled once ('Live chart'), not twice")
    legs = pg.locator(f"{CARD} [data-live-leg]")
    txt = " | ".join(legs.all_inner_texts())
    check("TC-LCHT-0005", legs.count() == 2 and "SELL" in txt and "BUY" in txt and "no price" not in txt,
          f"live strip: both legs with side as a word and a live price ({txt[:120]})")
    side_colours = pg.evaluate(f"""[...document.querySelectorAll('{CARD} [data-live-leg] > span:first-child')].map(e => getComputedStyle(e).color)""")
    ink = pg.evaluate("(() => { const d = document.createElement('i'); d.style.color = 'var(--c-text-primary)'; document.body.appendChild(d); const c = getComputedStyle(d).color; d.remove(); return c; })()")
    check("TC-LCHT-0006", side_colours and all(c == ink for c in side_colours),
          "a leg's side/name is ink — red/green is kept for P&L, not for sell/buy")
    notes = pg.locator(f"{CARD} [data-testid=replay-notes]")
    tog = pg.locator(f"{CARD} [data-testid=replay-notes-toggle]")
    closed = notes.count() == 0
    tog.click()
    time.sleep(0.4)
    check("TC-LCHT-0007", closed and notes.count() == 1 and "Exact" in notes.inner_text(),
          "data notes folded by default; one click shows them")
    tb = pg.locator(f"{CARD} [data-testid=replay-toolbar]")
    pressed = tb.locator("button[aria-pressed=true]").count()
    span0 = tb.inner_text()
    tb.get_by_role("button", name="+", exact=True).click()
    time.sleep(0.6)
    check("TC-LCHT-0008", pressed >= 3 and tb.inner_text() != span0,
          "toolbar: selected timeframe / pane / Fit marked; zoom + still changes the window")
    tb.get_by_role("button", name="1m", exact=True).first.click()
    time.sleep(1.0)
    check("TC-LCHT-0009", tb.get_by_role("button", name="1m", exact=True).first.get_attribute("aria-pressed") == "true",
          "spot timeframe switch still works (1m selected)")
    lines_before = pg.locator(f"{CARD} svg path").count()
    pg.locator(CARD).get_by_role("button", name="EMA 13").first.click()
    time.sleep(0.5)
    check("TC-LCHT-0010", pg.locator(f"{CARD} svg path").count() < lines_before,
          "legend entry hides its line")
    live_line = pg.evaluate(f"""[...document.querySelectorAll('{CARD} svg line')].filter(l => getComputedStyle(l).strokeDasharray === '3px, 3px').length""")
    check("TC-LCHT-0011", live_line >= 1, "live price drawn as a thin dashed guide, not a solid rule")
    errs += pg._errors
    # full screen
    pg.locator(CARD).get_by_role("button", name="Full screen").first.click()
    time.sleep(5)
    dlg = pg.locator("[role=dialog]")
    a2 = pg.evaluate(AUDIT, "[role=dialog]")
    shadow = pg.evaluate("(() => { const d = document.querySelector('[role=dialog] > div'); return d ? getComputedStyle(d).boxShadow : 'missing'; })()")
    check("TC-LCHT-0012", dlg.count() == 1 and "Trade replay" in dlg.inner_text() and a2 and
          set(a2["sizes"]) <= {"11px", "13px", "15px", "20px"} and shadow == "none",
          f"full screen: titled 'Trade replay', same type scale, no drop shadow ({sorted(a2['sizes']) if a2 else None}, {shadow})")
    pg.keyboard.press("Escape")
    time.sleep(0.8)
    check("TC-LCHT-0013", pg.locator("[role=dialog]").count() == 0, "Esc still closes the full-screen view")
    errs += pg._errors
    ctx.close()

    # light theme: tokens follow the theme (no hard-coded dark colours in the frame)
    import livechart_scene as S
    import json as _j
    S.SET.write_text(_j.dumps({"dashboard_layout": "modern", "theme": "light"}))
    ctx, pg = page(b, {"width": 1470, "height": 2600})
    wait_chart(pg)
    bg = pg.evaluate(f"(() => getComputedStyle(document.querySelector('{CARD} [role=region] > div')).backgroundColor)()")
    check("TC-LCHT-0014", bg in ("rgba(0, 0, 0, 0)", "transparent"), f"light theme: the card has no fill of its own ({bg})")
    errs += pg._errors
    ctx.close()
    S.SET.write_text(_j.dumps({"dashboard_layout": "modern"}))

    check("TC-LCHT-0015", not errs, f"no page errors ({errs[:2]})")

    # ── BOOT_NO_REMOUNT_20261117 ── DEF-038: the card used to blank for ~0.3 s about 2 s after its
    # first draw — the boot overlay re-mounted the whole app when it finished. Sample every 100 ms.
    ctx = b.new_context(viewport={"width": 1470, "height": 2600}, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    finds = []
    pg.on("request", lambda r: finds.append(r.url) if "/api/replay/find" in r.url else None)
    pg.clock.set_fixed_time("2025-02-25T13:05:05+05:30")
    pg.add_init_script("localStorage.clear();localStorage.setItem('scalp.strategyHost.focusId','TMA_V2');")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=S.FLEET))
    pg.route("**/system/license", lambda r: r.fulfill(json=dict(S.U.LICENSE, status="VALID")))
    pg.goto(S.BASE, wait_until="domcontentloaded")
    seq, first = [], None
    for _ in range(400):                                   # ≤ 40 s to the first draw, then 8 s of samples
        n = drawn(pg)
        if n >= 50 and first is None:
            first = len(seq)
        seq.append(n)
        if first is not None and len(seq) - first >= 80:
            break
        pg.wait_for_timeout(100)
    after = seq[first:] if first is not None else []
    blanks = sum(1 for n in after if n < 50)
    check("TC-LCHT-0016", first is not None and len(after) >= 80 and blanks == 0,
          f"no blank after the first draw: {blanks} empty of {len(after)} samples at 100 ms (DEF-038)")
    check("TC-LCHT-0017", len(finds) == 1, f"the card resolves its trade once at start-up ({len(finds)} /find calls; was 2)")
    ctx.close()
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
