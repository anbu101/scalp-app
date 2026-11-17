"""
test_ui_phone_meter.py — PHONE_METER_20261117: on a phone the Dashboard shows the
focused strategy's Entry/Exit meter ALONE (admin); the Live chart stays desktop-only.
Real /api/replay/meter on the synthetic scene (livechart_scene.py): open Timberwolf
spread, "now" 25 Feb 2025 13:05 IST.

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_phone_meter.py   [CHROME=/path/to/chrome]
"""
import inspect
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from livechart_scene import sync_playwright, CHROME, BASE, FLEET, U          # noqa: E402

fails = []
N = [0]
PHONE = {"width": 390, "height": 844}
DESK = {"width": 1470, "height": 1400}
CARD = "[data-testid=phone-meter]"
CHIP = f"{CARD} [data-testid=replay-meter]"


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def page(b, vp, focus="TMA_V2", admin=True):
    ctx = b.new_context(viewport=vp, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors, pg._meter, pg._replay = [], [], []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.on("request", lambda r: (pg._replay.append(r.url), "/api/replay/meter" in r.url and pg._meter.append(time.time()))
          if "/api/replay/" in r.url else None)
    pg.clock.set_fixed_time("2025-02-25T13:05:05+05:30")
    pg.add_init_script(f"localStorage.clear();localStorage.setItem('scalp.strategyHost.focusId','{focus}');")
    pg.route("**/api/fleet/today", lambda r: r.fulfill(json=FLEET))
    lic = dict(U.LICENSE, status="VALID", ui_level="admin" if admin else "user")
    pg.route("**/system/license", lambda r: r.fulfill(json=lic))
    pg.goto(BASE, wait_until="domcontentloaded")
    return ctx, pg


# NOTE: waits use pg.wait_for_timeout, never time.sleep — a bare sleep stops Playwright
# servicing its route handlers, so the licence poll hangs and timers look stalled.


def get(path):
    raw = urllib.request.urlopen(BASE + path).read()
    return raw, json.loads(raw)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    # ── backend: the slim route
    raw, d = get("api/replay/meter?strategy=TMA_V2")
    m = (d.get("waiting") or {}).get("meter") or {}
    check("TC-MMTR-0001", d.get("ok") and d.get("watch") is False and m.get("side") == "exit" and m.get("score") == 70,
          f"open position → exit meter 70 ({m.get('side')} {m.get('score')})")
    full = urllib.request.urlopen(BASE + "api/replay/trade?scope=trades&key=" + urllib.request.quote(d["key"].split("|", 1)[1]) + "&live=1").read()
    check("TC-MMTR-0002", len(raw) < 5000 and not ({"spot", "legs_bars", "mtm"} & set(d)),
          f"meter payload {len(raw):,} B, no candles (chart payload {len(full):,} B)")
    _, w = get("api/replay/meter?strategy=VET_V1")
    check("TC-MMTR-0003", w.get("watch") is True and (w["waiting"]["meter"] or {}).get("side") == "entry",
          f"no open position → watch, entry meter ({(w['waiting']['meter'] or {}).get('word')})")
    src = open(os.path.join(os.path.dirname(inspect.getsourcefile(U.live_replay_routes)), "..", "api_server.py")).read()   # read, not imported (scheduler deps)
    from app.api import live_replay_routes as LRR                            # noqa: E402
    paths = [r.path for r in LRR.router.routes]
    check("TC-MMTR-0004", "/api/replay/meter" in paths and "include_router(live_replay_router, dependencies=[Depends(_require_admin_ui)])" in src,
          "route sits on the admin-walled replay router")

    # ── phone, admin
    ctx, pg = page(b, PHONE)
    pg.wait_for_selector(CHIP, timeout=60000)
    pg.wait_for_timeout(1000)
    check("TC-MMTR-0005", pg.query_selector("[data-testid=live-chart-card]") is None
          and not any("/api/replay/trade" in u or "/api/replay/watch" in u or "/api/replay/find" in u for u in pg._replay),
          "phone: meter only — no Live chart, no candle payload fetched")
    txt = pg.inner_text(CARD).lower()
    check("TC-MMTR-0006", "entry / exit" in txt and "open position" in txt and "exit" in txt and "70%" in txt and "imminent" in txt,
          "card reads Entry / Exit · open position · EXIT 70% imminent")
    check("TC-MMTR-0007", pg.query_selector(f"{CARD} [data-testid=replay-waiting]") is None
          and pg.get_attribute(CHIP, "aria-expanded") == "false", "starts collapsed")
    pg.click(CHIP)
    pg.wait_for_selector(f"{CARD} [data-testid=replay-waiting]", timeout=5000)
    wt = pg.inner_text(f"{CARD} [data-testid=replay-waiting]").lower()
    pg.click(CHIP)
    pg.wait_for_timeout(300)
    check("TC-MMTR-0008", "exit near" in wt and "xover" in wt and pg.query_selector(f"{CARD} [data-testid=replay-waiting]") is None,
          "tap opens the conditions (dial + checklist), tap again closes")
    check("TC-MMTR-0009", pg.query_selector(f"{CARD} [data-testid=phone-meter-stale]") is None, "fresh feed → no stale label")
    geo = pg.evaluate(f"""(() => {{ const c = document.querySelector('{CARD}').getBoundingClientRect();
      return {{ l: c.left, r: c.right, vw: innerWidth, sw: document.documentElement.scrollWidth }}; }})()""")
    check("TC-MMTR-0010", geo["l"] >= 0 and geo["r"] <= geo["vw"] and geo["sw"] <= geo["vw"], f"fits the phone width {geo}")
    sizes = pg.evaluate(f"""(() => {{ const s = new Set(); const walk = (el) => {{
        if (el.closest('svg')) return;
        if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) s.add(getComputedStyle(el).fontSize);
        [...el.children].forEach(walk); }};
      document.querySelector('{CHIP}').click(); walk(document.querySelector('{CARD}')); return [...s]; }})()""")
    check("TC-MMTR-0011", set(sizes) <= {"11px", "13px", "15px", "20px", "32px"}, f"type on the Dashboard scale {sorted(sizes)}")
    # polling: live in session → every poll_ms (2 s)
    n0 = len(pg._meter)
    pg.wait_for_timeout(6500)
    check("TC-MMTR-0012", len(pg._meter) - n0 >= 2, f"live in session → re-read every 2 s ({len(pg._meter) - n0} in 6.5 s)")
    # a failed poll keeps the last reading and says so
    pg.route("**/api/replay/meter*", lambda r: r.fulfill(status=500, body='{"detail":"boom"}'))
    pg.wait_for_timeout(3000)
    check("TC-MMTR-0013", "70%" in pg.inner_text(CARD) and pg.query_selector(f"{CARD} [data-testid=phone-meter-stale]") is not None,
          "failed poll → last reading stays, marked stale")
    pg.unroute("**/api/replay/meter*")
    # backgrounded → no polling
    pg.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })")
    pg.wait_for_timeout(2500)
    n1 = len(pg._meter)
    pg.wait_for_timeout(4500)
    check("TC-MMTR-0014", len(pg._meter) == n1, f"app in the background → no reads ({len(pg._meter) - n1})")
    check("TC-MMTR-0015", not pg._errors, f"no page errors {pg._errors[:1]}")
    ctx.close()

    # ── phone, a strategy with no open position → watching for an entry
    ctx, pg = page(b, PHONE, focus="VET_V1")
    pg.wait_for_selector(CHIP, timeout=60000)
    t = pg.inner_text(CARD).lower()
    check("TC-MMTR-0016", "watching for an entry" in t and "entry" in t, "no open position → entry meter, watching")
    ctx.close()

    # ── phone, non-admin → nothing, no replay calls
    ctx, pg = page(b, PHONE, admin=False)
    pg.wait_for_selector("text=Traded today", timeout=60000)
    pg.wait_for_timeout(3000)
    check("TC-MMTR-0017", pg.query_selector(CARD) is None and not pg._replay, "non-admin phone: no meter, no replay calls")
    ctx.close()

    # ── desktop, admin → Live chart as before, no phone meter
    ctx, pg = page(b, DESK)
    pg.wait_for_selector("[data-testid=live-chart-card]", timeout=60000)
    pg.wait_for_timeout(3000)
    check("TC-MMTR-0018", pg.query_selector(CARD) is None and not pg._meter, "desktop: Live chart unchanged, no phone meter")
    ctx.close()
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks" + (f" — {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)
