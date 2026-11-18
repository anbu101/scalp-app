"""
test_ui_calm.py — DASH_CALM_20261117: the redesigned Dashboard (layout "modern") in a real
browser on the BUILT frontend. Scenes from calm_fixture (dash_fixture rows: Live + Paper at
13:05; canned meters in the /api/replay/meters shape). The real meters route has its own
backend suite (backend/app/api/test_dash_calm_routes.py).

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_calm.py   [CHROME=/path/to/chrome]
"""
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
from dash_fixture import FLEET                                         # noqa: E402
from calm_fixture import METERS, paper_only, pre_open, mix_scene                # noqa: E402

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or "/usr/bin/google-chrome"
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
fails, N, errs = [], [0], []


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def open_dash(b, fleet=FLEET, width=1440, height=1400, admin=True, layout="modern", theme="dark", clock="2026-11-17T13:05:00+05:30"):
    SET.parent.mkdir(parents=True, exist_ok=True)
    SET.write_text(json.dumps({"dashboard_layout": layout, "theme": theme} if layout else {"theme": theme}))
    ctx = b.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors, pg._meters = [], []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.on("request", lambda r: pg._meters.append(r.url) if "/api/replay/meter" in r.url else None)
    pg.clock.set_fixed_time(clock)
    pg.add_init_script("localStorage.clear();")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=fleet))
    lic = dict(U.LICENSE, status="VALID", valid=True, ui_level="admin" if admin else "user", strategies=["*"])
    pg.route("**/system/license", lambda r: r.fulfill(json=lic))

    def meters(route):
        q = urllib.parse.unquote(route.request.url.split("strategies=")[-1].split("&")[0])
        route.fulfill(json={"ok": True, "meters": {s: METERS[s] for s in q.split(",") if s in METERS}, "errors": {}})
    pg.route("**/api/replay/meters*", meters)
    pg.route("**/api/replay/find*", lambda r: r.fulfill(status=404, json={"detail": "no trade"}))
    pg.goto(BASE, wait_until="domcontentloaded")
    return ctx, pg


def cards(pg):
    return pg.evaluate("""[...document.querySelectorAll('[data-testid=calm-card],[data-testid=calm-idle]')].map(c => ({
      id: c.dataset.strategy, idle: c.dataset.testid === 'calm-idle', text: c.innerText.replace(/\\s+/g, ' '),
      w: Math.round(c.getBoundingClientRect().width), meter: !!c.querySelector('[data-testid=calm-meter]'),
      cur: c.getAttribute('aria-current') === 'true', badge: (c.querySelector('[data-badge]') || {}).textContent || '' }))""")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    # ── layouts
    ctx, pg = open_dash(b, layout=None)
    pg.wait_for_selector("[data-testid=calm-root]", timeout=60000)
    check("TC-CALM-0001", pg.locator("[data-testid=calm-root]").count() == 1 and pg.locator("[data-testid=dash-books]").count() == 0,
          "no layout saved → the redesigned Dashboard (Modern)")
    ctx.close()
    ctx, pg = open_dash(b, layout="classic")
    pg.wait_for_selector("[data-testid=dash-books]", timeout=60000)
    check("TC-CALM-0002", pg.locator("[data-testid=calm-root]").count() == 0, "layout Classic → the previous Modern page (book summary + table)")
    ctx.close()

    # ── Live + Paper day, desktop
    ctx, pg = open_dash(b)
    pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
    pg.wait_for_timeout(1500)
    hero = pg.locator("[data-testid=calm-hero]")
    check("TC-CALM-0003", hero.get_attribute("data-book") == "LIVE" and pg.inner_text("[data-testid=calm-hero-figure]") == "+₹24,690"
          and "+₹9,710" in pg.inner_text("[data-testid=calm-hero-other]"),
          "Live traded → Live is the big number, Paper beside it")
    sent = pg.inner_text("[data-testid=calm-hero-sentence]")
    check("TC-CALM-0004", "below today's high" in sent and "never added together" in sent and "₹15,132" in sent,
          f"sentence explains the number ({sent[:90]}…)")
    pg.click("[data-period=week]")
    wk = (pg.inner_text("[data-testid=calm-hero-figure]"), pg.inner_text("[data-testid=calm-hero-sentence]"))
    pg.click("[data-period=today]")
    check("TC-CALM-0005", wk[0] == "+₹41,250" and "exited since 11 Nov" in wk[1], f"This week tab → week's net and its trade count ({wk})")
    lines = pg.evaluate("[...document.querySelectorAll('[data-testid=calm-chart] svg path')].length")
    endlbl = pg.locator("[data-testid=calm-chart]").inner_text()
    check("TC-CALM-0006", lines == 2 and "Now 13:05" in endlbl and "09:15" in endlbl, f"chart: Live + Paper lines, axis 09:15 → Now 13:05 ({lines} lines)")
    cs = cards(pg)
    ids = [c["id"] for c in cs if not c["idle"]]
    check("TC-CALM-0007", ids == ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1", "ORB_V1"] and [c["id"] for c in cs if c["idle"]] == ["EMC_V1", "BRK_V1"],
          f"one card per strategy (TMA Live + Paper share one), biggest move first, then the waiting ones ({ids})")
    tma = next(c for c in cs if c["id"] == "TMA_V2")
    check("TC-CALM-0008", "Live + Paper" in tma["text"] and "Paper twin +₹6,410" in tma["text"] and "+₹6,240" in tma["text"],
          "TMA card: Live figure big, Paper twin under it")
    bd = {c["id"]: c["badge"] for c in cs}
    check("TC-CALM-0009", bd["VET_V1"] == "Stopped out" and bd["ORB_V1"] == "Done" and bd["TMA_V2"] == "Exit near" and bd["IC_V2"] == "In trade · 4 legs",
          f"state badges ({bd})")
    q = urllib.parse.unquote(pg._meters[0].split("strategies=")[-1]) if pg._meters else ""
    check("TC-CALM-0010", len(pg._meters) == 1 and set(q.split(",")) == {"IC_V2", "TMA_V2", "TSG_V1", "EMC_V1", "BRK_V1"},
          f"every open / waiting card's meter in ONE call; closed ones not asked ({len(pg._meters)} call: {q})")
    check("TC-CALM-0011", {c["id"] for c in cs if c["meter"]} == {"IC_V2", "TMA_V2", "TSG_V1", "EMC_V1", "BRK_V1"},
          "meter bar on open and waiting cards only")
    w0 = tma["w"]
    pg.click("[data-strategy=TMA_V2] [data-testid=calm-meter]")
    pg.wait_for_selector("[data-strategy=TMA_V2] [data-testid=calm-why]", timeout=5000)
    why = pg.inner_text("[data-strategy=TMA_V2] [data-testid=calm-why]")
    w1 = next(c for c in cards(pg) if c["id"] == "TMA_V2")["w"]
    check("TC-CALM-0012", "EXIT NEAR" in why and "EMA13" in why and "70%" in why and w1 > w0 * 1.8
          and pg.get_attribute("[data-strategy=TMA_V2] [data-testid=calm-meter]", "aria-expanded") == "true",
          f"tap the meter → the card widens ({w0}→{w1}px) and shows the dial and conditions")
    pg.wait_for_timeout(5000)
    fast = len(pg._meters)
    pg.click("[data-strategy=TMA_V2] [data-testid=calm-meter]")
    pg.wait_for_timeout(400)
    check("TC-CALM-0013", pg.locator("[data-testid=calm-why]").count() == 0 and fast >= 3,
          f"tap again closes it; while open the meters refresh every 4 s ({fast} calls)")
    pg.click("[data-strategy=TSG_V1] .calm-card-main")
    pg.wait_for_timeout(800)
    cur = [c["id"] for c in cards(pg) if c["cur"]]
    focus_txt = pg.inner_text("[data-testid=calm-focus]")
    check("TC-CALM-0014", cur == ["TSG_V1"] and "Time Strangle" in focus_txt and pg.evaluate("localStorage.getItem('scalp.strategyHost.focusId')") == "TSG_V1",
          f"tap a card → it is marked and its panel opens below ({cur})")
    pg.click("[data-filter=LIVE]")
    pg.wait_for_timeout(300)
    live = cards(pg)
    pg.click("[data-filter=PAPER]")
    pg.wait_for_timeout(300)
    pap = cards(pg)
    pg.click("[data-filter=ALL]")
    tmap = next(c for c in pap if c["id"] == "TMA_V2")
    check("TC-CALM-0015", [c["id"] for c in live] == ["IC_V2", "TMA_V2"] and "+₹6,410" in tmap["text"] and "Live twin +₹6,240" in tmap["text"]
          and not any(c["idle"] for c in pap),
          "filter Live → Live cards only; Paper → TMA shows its Paper figure, Live as the twin")
    cnt = pg.inner_text("[data-testid=calm-count]")
    check("TC-CALM-0016", "6 of 8 traded today" in cnt and "3 in a trade" in cnt, f"count line ({cnt})")
    geo = pg.evaluate("({sw: document.documentElement.scrollWidth, vw: innerWidth})")
    check("TC-CALM-0017", geo["sw"] <= geo["vw"], f"1440 px: no sideways scroll {geo}")
    errs += pg._errors
    ctx.close()

    # ── Paper-only day
    ctx, pg = open_dash(b, fleet=paper_only())
    pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
    pg.wait_for_timeout(1000)
    check("TC-CALM-0018", pg.get_attribute("[data-testid=calm-hero]", "data-book") == "PAPER" and pg.inner_text("[data-testid=calm-hero-figure]") == "+₹9,710"
          and pg.locator("[data-filter]").count() == 0 and pg.locator("[data-testid=calm-hero-other]").count() == 0
          and pg.evaluate("[...document.querySelectorAll('[data-testid=calm-chart] svg path')].length") == 1,
          "Paper only: Paper is the big number, one line, no book filter")
    errs += pg._errors
    ctx.close()

    # ── before the open, nothing traded
    ctx, pg = open_dash(b, fleet=pre_open(), clock="2026-11-17T08:53:00+05:30")
    pg.wait_for_selector("[data-testid=calm-idle]", timeout=60000)
    h = pg.inner_text("[data-testid=calm-hero]")
    check("TC-CALM-0019", "₹0" in h and "No trades yet today. 3 strategies are armed." in h and "market opens in 22m" in h
          and pg.locator("[data-testid=calm-idle]").count() == 3 and "0 of 3 traded today" in pg.inner_text("[data-testid=calm-count]"),
          "before the open: ₹0, armed count, a waiting card per armed strategy")
    errs += pg._errors
    ctx.close()

    # ── non-admin
    ctx, pg = open_dash(b, admin=False)
    pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
    pg.wait_for_timeout(2500)
    check("TC-CALM-0020", not pg._meters and pg.locator("[data-testid=calm-meter]").count() == 0,
          "non-admin: no meter calls, no meter bars (the route is admin-only)")
    errs += pg._errors
    ctx.close()

    # ── phone
    ctx, pg = open_dash(b, width=390, height=844)
    pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
    pg.wait_for_timeout(1500)
    geo = pg.evaluate("({sw: document.documentElement.scrollWidth, vw: innerWidth})")
    pg.click("[data-testid=calm-hero] [aria-pressed=false]")
    pg.wait_for_timeout(300)
    flip = pg.get_attribute("[data-testid=calm-hero]", "data-book")
    pg.click("[data-strategy=TMA_V2] [data-testid=calm-meter]")
    pg.wait_for_selector("[data-testid=calm-why]", timeout=5000)
    whyw = pg.evaluate("document.querySelector('[data-testid=calm-why]').getBoundingClientRect().right")
    check("TC-CALM-0021", geo["sw"] <= 390 and flip == "PAPER" and whyw <= 390 and pg.locator("[data-testid=phone-meter]").count() == 0,
          f"phone: fits 390 px, Live | Paper switch flips the big number, meter opens inside its card, no second meter card ({geo}, {flip})")
    errs += pg._errors
    ctx.close()

    # ── themes
    inks = {}
    for th in ("dark", "light", "terminal", "kolam", "arcade", "thalapathy"):
        ctx, pg = open_dash(b, theme=th)
        pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
        inks[th] = pg.evaluate("getComputedStyle(document.querySelector('[data-testid=calm-hero-sentence]')).color")
        errs += pg._errors
        ctx.close()
    check("TC-CALM-0022", len(set(inks.values())) >= 3, f"every theme renders and the text follows the theme ({inks})")
    # ── CARD_HEAD_20261117 / END_LABELS_20261117 (DEF-043, DEF-042): 17 Nov mock-up scene
    for w in (1440, 1180):
        ctx, pg = open_dash(b, fleet=mix_scene(), width=w)
        pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
        pg.wait_for_timeout(1200)
        heads = pg.evaluate("""[...document.querySelectorAll('[data-testid=calm-card]')].map(c => {
          const k = c.querySelector('[data-testid=calm-card-books]') || c.querySelector('[data-accent]').parentElement.children[2]; const m = c.querySelector('[data-testid=calm-card-mtm]');
          return { id: c.dataset.strategy, books: k.textContent, cut: k.scrollWidth > k.clientWidth + 1, badge: (c.querySelector('[data-badge]') || {}).textContent || '',
                   over: c.scrollWidth > c.clientWidth + 1, mtm: m.textContent }; })""")
        check("TC-CALM-0034" if w == 1440 else "TC-CALM-0036",
              len(heads) == 4 and not any(h["cut"] or h["over"] for h in heads) and all(h["badge"] for h in heads)
              and any(h["id"] == "IC_V2" and h["books"].startswith("Paper") for h in heads),
              f"card header at {w} px: the book label is never cut ('Paper · carried' whole) and the state badge is still on the card ({[(h['id'], h['books'], h['cut']) for h in heads]})")
        if w == 1440:
            lab = pg.evaluate("""(() => { const r = (t) => { const e = [...document.querySelectorAll('[data-testid=calm-chart] span')].find(x => x.textContent === t && getComputedStyle(x).position === 'absolute'); return e ? e.getBoundingClientRect() : null; };
              const a = r('Live'), c = r('Paper');
              if (!a || !c) return { missing: true, a: !!a, c: !!c };
              const hit = !(a.right <= c.left || c.right <= a.left || a.bottom <= c.top || c.bottom <= a.top);
              return { hit, a: [a.top, a.bottom], c: [c.top, c.bottom] }; })()""")
            check("TC-CALM-0035", not lab.get("missing") and not lab["hit"],
                  f"Live and Paper end labels do not touch each other when Paper ends above Live ({lab})")
        errs += pg._errors
        ctx.close()
    # ── WIDE_SCREEN_20261117 (DEF-057): on a 16-inch MacBook Pro (1728 px, or 2056 "more space") the page fills the width
    # and four strategies make four equal columns with no empty fifth column on the right
    MEASURE = """(() => { const cards = [...document.querySelectorAll('[data-testid=calm-card],[data-testid=calm-idle-card]')].map(c => c.getBoundingClientRect());
      const sec = document.querySelector('[data-testid=calm-strategies]').getBoundingClientRect();
      const lefts = [...new Set(cards.map(r => Math.round(r.left)))].sort((a, b) => a - b);
      return { w: window.innerWidth, secL: sec.left, secR: sec.right, n: cards.length, cols: lefts.length,
               firstL: Math.min(...cards.map(r => r.left)), lastR: Math.max(...cards.map(r => r.right)),
               widths: cards.map(r => Math.round(r.width)) }; })()"""
    for w, cap in ((1728, 70), (2056, 70)):
        ctx, pg = open_dash(b, fleet=mix_scene(), width=w, height=1100)
        pg.wait_for_selector("[data-testid=calm-card]", timeout=60000)
        pg.wait_for_timeout(1200)
        m = pg.evaluate(MEASURE)
        ok = (m["firstL"] <= cap and m["w"] - m["lastR"] <= cap + 16 and m["cols"] == 4 and m["lastR"] >= m["secR"] - 3
              and max(m["widths"]) - min(m["widths"]) <= 2)
        check("TC-CALM-0037" if w == 1728 else "TC-CALM-0038", ok,
              f"{w} px screen: content fills the width (sides {m['firstL']:.0f} / {m['w'] - m['lastR']:.0f} px), 4 equal columns, no empty column on the right ({m})")
        errs += pg._errors
        ctx.close()
    check("TC-CALM-0023", not errs, f"no page errors ({errs[:2]})")
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks" + (f" — {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)
