"""
test_ui_chart_card.py — CHART_SAME_MTM_20261117 / CHART_BELOW_PANEL_20261117: on the Dashboard the
focused strategy's live chart sits BELOW its panel (legs), shows the same MTM-today figure as the
strategy's card, and leaves the Entry/Exit meter to the card. Real /api/replay routes on the
synthetic replay scene (livechart_scene.py: open Timberwolf spread, 25 Feb 2025 13:05 IST).

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_chart_card.py   [CHROME=/path/to/chrome]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livechart_scene as S                                            # noqa: E402
from livechart_scene import sync_playwright, CHROME, BASE, FLEET, wait_chart   # noqa: E402

fails, N, errs = [], [0], []
CARD = "[data-testid=live-chart-card]"


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def fmt(v):
    n = round(v)
    return ("+" if n > 0 else "−" if n < 0 else "") + "₹" + f"{abs(n):,}"   # en-IN grouping is the same below 1 lakh


ORDER = f"""(() => {{ const c = document.querySelector('{CARD}'); if (!c) return null;
  const p = c.previousElementSibling; const r = c.getBoundingClientRect(), q = p ? p.getBoundingClientRect() : null;
  return {{ prevText: p ? p.innerText.slice(0, 200) : '', prevBottom: q ? Math.round(q.bottom) : null, top: Math.round(r.top),
           strip: !!(p && p.closest('[data-testid=today-strip]')) }}; }})()"""


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    # ── the redesigned Dashboard (layout modern)
    S.SET.write_text(json.dumps({"dashboard_layout": "modern"}))
    ctx, pg = S.page(b, {"width": 1470, "height": 2600})
    wait_chart(pg)
    o = pg.evaluate(ORDER)
    check("TC-CHRT-0001", o and o["prevBottom"] is not None and o["prevBottom"] <= o["top"] and "TMA" in o["prevText"].upper() and not o["strip"],
          f"live chart sits below the strategy's panel (panel bottom {o and o['prevBottom']} ≤ chart top {o and o['top']})")
    book = pg.evaluate("fetch('/api/replay/find?strategy=TMA_V2').then(r => r.json()).then(d => d.book)")
    row = next(r for r in FLEET["strategies"] if r["id"] == "TMA_V2" and r["book"] == book)
    hero = pg.inner_text(f"{CARD} [data-testid=replay-hero]")
    check("TC-CHRT-0002", fmt(row["gross"]) in hero and "MTM today · same as the card above" in hero
          and ("Paper" if book == "PAPER" else "Live") in hero,
          f"chart hero = the Dashboard's {book} MTM today {fmt(row['gross'])} ({hero.replace(chr(10), ' ')[:90]})")
    cards = pg.inner_text("[data-strategy=TMA_V2]")
    check("TC-CHRT-0003", fmt(row["gross"]) in cards, "…the same figure the TMA card shows (big, or as its twin)")
    sub = pg.inner_text(f"{CARD} [data-testid=replay-hero-sub]") if pg.locator(f"{CARD} [data-testid=replay-hero-sub]").count() else ""
    check("TC-CHRT-0004", "open position" in sub and "open P&L at LTP" not in hero,
          f"its parts under it, no second headline number ({sub})")
    check("TC-CHRT-0005", pg.locator(f"{CARD} [data-testid=replay-meter]").count() == 0
          and pg.locator(f"{CARD} [data-testid=replay-waiting]").count() == 0
          and pg.locator("[data-strategy=TMA_V2] [data-testid=calm-meter]").count() == 1,
          "no Entry/Exit meter in the chart; the card above keeps it")
    errs += pg._errors
    ctx.close()
    ctx, pg = S.page(b, {"width": 1470, "height": 900})   # a real window height: the page can scroll to the panel
    pg.wait_for_selector("[data-strategy=TMA_V2] .calm-card-main", timeout=60000)
    pg.wait_for_timeout(1500)
    pg.evaluate("window.scrollTo(0, 0)")
    pg.click("[data-strategy=TMA_V2] .calm-card-main")
    pg.wait_for_timeout(2000)
    top = pg.evaluate("Math.round(document.querySelector('[data-testid=calm-focus]').getBoundingClientRect().top)")
    check("TC-CHRT-0006", 0 <= top <= 120, f"tap a card → the page scrolls to the panel (legs first), not past it to the chart (top {top}px)")
    errs += pg._errors
    ctx.close()

    # ── Classic page: same order; the chart keeps its own hero and meter there
    S.SET.write_text(json.dumps({"dashboard_layout": "classic"}))
    ctx, pg = S.page(b, {"width": 1470, "height": 2600})
    wait_chart(pg)
    o = pg.evaluate(ORDER)
    hero = pg.inner_text(f"{CARD} [data-testid=replay-hero]")
    check("TC-CHRT-0007", o and o["prevBottom"] is not None and o["prevBottom"] <= o["top"]
          and "open P&L at LTP" in hero and pg.locator(f"{CARD} [data-testid=replay-meter]").count() == 1,
          "Classic page: chart below the panel too; its own hero and meter unchanged (no cards there)")
    errs += pg._errors
    ctx.close()
    check("TC-CHRT-0008", not errs, f"no page errors ({errs[:2]})")
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks")
sys.exit(1 if fails else 0)
