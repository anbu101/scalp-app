"""
test_ui_ready.py — READY_PANEL_20261118 (TD-030, DEF-061): before the open the Dashboard
shows This week / This month and the "Ready for the open" panel (Zerodha / Angel login,
market feed, engine, carried stops at the broker). Built frontend, real browser; the
status routes are canned here (their backend: app/api/test_fleet_ready.py).

    cd frontend && CI=false npm run build
    PYTHONPATH=../backend python3 uitest/test_ui_ready.py   [CHROME=/path/to/chrome]
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_server as U                                                  # noqa: E402
from calm_fixture import pre_open, mix_scene, MARKET, PRE_INDICES                           # noqa: E402

U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or "/usr/bin/google-chrome"
BASE = "http://127.0.0.1:47321/"
SET = U.app_settings_api.SETTINGS_FILE
fails, N, errs = [], [0], []
PRE = "2026-11-17T08:53:00+05:30"          # Tuesday, 22 min before the open

Z_OK = {"configured": True, "connected": True, "session_expired": False, "login_at": "2026-11-17T08:41:07+05:30", "trading_enabled": True}
Z_OFF = {"configured": True, "connected": False, "session_expired": True, "login_at": "2026-11-16T08:39:00+05:30", "trading_enabled": False}
A_OFF = {"broker": "ANGELONE", "connected": False, "last_login": None, "last_error": None, "configured": True, "enabled": True, "client_code": "X1"}
A_ON = dict(A_OFF, connected=True, last_login="2026-11-17T08:54:10+05:30")
A_NONE = {"broker": "ANGELONE", "connected": False, "last_login": None, "last_error": None, "configured": False, "enabled": False, "client_code": None}
R_OK = {"ok": True, "feed": {"state": "connected", "engines": 1, "last_tick_age_s": 2.0},
        "carried_stops": [{"id": "IC_V2", "legs": 2, "with_stop": 2, "armed": 2}]}


def check(tc, cond, msg):
    N[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def fmt(v):
    n = round(v)
    s = f"{abs(n):,}" if abs(n) < 100000 else f"{abs(n) // 100000},{(abs(n) % 100000) // 1000:02d},{abs(n) % 1000:03d}"
    return ("+" if n > 0 else "−" if n < 0 else "") + "₹" + s


def open_dash(b, fleet=None, z=Z_OK, a=A_OFF, r=R_OK, width=1440, height=1100, clock=PRE, admin=True, theme="dark", market=False):   # READY_LOGIN_LABEL_20261118: theme; INDEX_LEFT_20261118: market
    SET.parent.mkdir(parents=True, exist_ok=True)
    SET.write_text(json.dumps({"dashboard_layout": "modern", "theme": theme}))
    ctx = b.new_context(viewport={"width": width, "height": height}, timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    pg._errors, pg._calls = [], []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.clock.set_fixed_time(clock)
    pg.add_init_script("localStorage.clear(); window.open = (u) => { window.__opened = u; return null; };")
    st = {"z": z, "a": a, "r": r}
    pg._st = st
    pg.route("**/api/fleet/today", lambda q: q.fulfill(json=fleet or pre_open()))
    lic = dict(U.LICENSE, status="VALID", valid=True, ui_level="admin" if admin else "user", strategies=["*"])
    pg.route("**/system/license", lambda q: q.fulfill(json=lic))
    pg.route("**/zerodha/status", lambda q: q.fulfill(json=st["z"]))
    pg.route("**/api/acc2/status", lambda q: q.fulfill(json=st["a"]))
    pg.route("**/api/fleet/ready", lambda q: q.fulfill(json=st["r"]))
    pg.route("**/api/replay/**", lambda q: q.fulfill(status=404, json={"detail": "none"}))
    if market:   # INDEX_LEFT_20261118: the NIFTY / BANKNIFTY chips need the index feed
        pg.route("**/market_indices", lambda q: q.fulfill(json=MARKET))
        pg.route("**/api/fleet/indices", lambda q: q.fulfill(json=PRE_INDICES))

    def login_url(q):
        pg._calls.append("login-url")
        q.fulfill(json={"login_url": "https://kite.zerodha.com/connect/login?v=3&api_key=x"})
    pg.route("**/zerodha/login-url", login_url)

    def force(q):
        pg._calls.append("force-login")
        st["a"] = A_ON
        q.fulfill(json={"connected": True, **A_ON})
    pg.route("**/api/acc2/force-login", force)
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_selector("[data-testid=calm-hero]", timeout=60000)
    pg.wait_for_timeout(1500)
    return ctx, pg


def row(pg, rid):
    return pg.evaluate(f"""(() => {{ const r = document.querySelector('[data-testid=ready-panel] [data-ready={rid}]');
      return r ? {{ state: r.dataset.state, text: r.innerText.replace(/\\n/g, ' ') }} : null; }})()""")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    # ── 1. 08:53, nothing traded: chips + panel beside the hero
    ctx, pg = open_dash(b)
    f = pre_open()["totals"]["LIVE"]
    chips = pg.inner_text("[data-testid=calm-period-chips]") if pg.locator("[data-testid=calm-period-chips]").count() else ""
    check("TC-RDY-0001", "This week · Live" in chips and fmt(f["week_net"]) in chips and "This month · Live" in chips and fmt(f["month_net"]) in chips,
          f"This week / This month (Live) shown before the first trade ({chips.replace(chr(10), ' ')})")
    pos = pg.evaluate("""(() => { const p = document.querySelector('[data-testid=ready-panel]'); const s = document.querySelector('[data-testid=calm-hero] p');
      if (!p || !s) return null; const a = p.getBoundingClientRect(), c = s.getBoundingClientRect(); return { pl: a.left, sr: c.right, pt: a.top, st: c.top, w: a.width }; })()""")
    check("TC-RDY-0002", pos and pos["pl"] > pos["sr"] and abs(pos["pt"] - pos["st"]) < 220 and pos["w"] >= 400,
          f"'Ready for the open' panel sits beside the hero, not under it ({pos})")
    rz, ra, rf, re_, rs = (row(pg, k) for k in ("zerodha", "angel", "feed", "engine", "stops"))
    check("TC-RDY-0003", rz and rz["state"] == "ok" and "08:41" in rz["text"], f"Zerodha login: green with the login time ({rz})")
    check("TC-RDY-0004", ra and ra["state"] == "warn" and "Log in" in ra["text"], f"Angel login: amber with a Log in button ({ra})")
    check("TC-RDY-0005", rf and rf["state"] == "ok" and "connected" in rf["text"], f"Market feed: connected ({rf})")
    armed = pre_open()["active"] + len(pre_open()["idle"])
    check("TC-RDY-0006", re_ and re_["state"] == "ok" and f"{armed} armed" in re_["text"], f"Engine: on · {armed} armed ({re_})")
    check("TC-RDY-0007", rs and rs["state"] == "ok" and "2 of 2 armed" in rs["text"], f"Carried stops at broker: 2 of 2 armed ({rs})")
    pg.click("[data-ready=angel] [data-testid=ready-login]")
    pg.wait_for_timeout(1500)
    ra = row(pg, "angel")
    check("TC-RDY-0008", "force-login" in pg._calls and ra and ra["state"] == "ok" and "08:54" in ra["text"],
          f"Angel Log in → POST /api/acc2/force-login, row turns green with the time ({ra}, {pg._calls})")
    errs += pg._errors
    ctx.close()

    # ── 2. Zerodha not logged in yet: Log in opens Kite; feed and stops say why they wait
    ctx, pg = open_dash(b, z=Z_OFF, a=A_NONE, r={"ok": True, "feed": {"state": "not_started", "engines": 0, "last_tick_age_s": None},
                                                    "carried_stops": [{"id": "IC_V2", "legs": 2, "with_stop": 2, "armed": None}]})
    rz, ra, rf, rs = (row(pg, k) for k in ("zerodha", "angel", "feed", "stops"))
    pg.click("[data-ready=zerodha] [data-testid=ready-login]")
    pg.wait_for_timeout(1200)
    opened = pg.evaluate("window.__opened || null")
    check("TC-RDY-0009", rz and rz["state"] == "warn" and "login-url" in pg._calls and opened and "kite.zerodha.com" in opened,
          f"Zerodha not logged in: amber, Log in opens the Kite login page ({rz}, {opened})")
    check("TC-RDY-0010", ra is None, "Angel not set up → no Angel row")
    check("TC-RDY-0011", rf and "after the Zerodha login" in rf["text"] and rs and rs["state"] == "warn" and "log in to Zerodha" in rs["text"],
          f"feed and stops say they wait for the Zerodha login ({rf}, {rs})")
    errs += pg._errors
    ctx.close()

    # ── 3. a stop missing at the broker is red; no carried legs → no stops row
    ctx, pg = open_dash(b, r=dict(R_OK, carried_stops=[{"id": "IC_V2", "legs": 2, "with_stop": 2, "armed": 1}]))
    rs = row(pg, "stops")
    check("TC-RDY-0012", rs and rs["state"] == "bad" and "1 of 2 armed" in rs["text"], f"one GTT gone → red, 1 of 2 ({rs})")
    pg._st["r"] = dict(R_OK, carried_stops=[])
    pg.wait_for_timeout(16500)
    check("TC-RDY-0013", row(pg, "stops") is None, "no live carried legs → no 'Carried stops' row (after the 15 s refresh)")
    errs += pg._errors
    ctx.close()

    # ── 4. carried positions open (the hero has a book): the panel sits in the right column
    ctx, pg = open_dash(b, fleet=mix_scene())
    pos = pg.evaluate("""(() => { const p = document.querySelector('[data-testid=ready-panel]'); const f = document.querySelector('[data-testid=calm-hero-figure]');
      return p && f ? { pl: p.getBoundingClientRect().left, fr: f.getBoundingClientRect().right } : null; })()""")
    check("TC-RDY-0014", pos and pos["pl"] > pos["fr"], f"with carried positions the panel is beside the big number ({pos})")
    errs += pg._errors
    ctx.close()

    # ── 5. not shown in session or at the weekend
    ctx, pg = open_dash(b, clock="2026-11-17T13:05:00+05:30")
    check("TC-RDY-0015", pg.locator("[data-testid=ready-panel]").count() == 0, "13:05 (market open): no panel")
    errs += pg._errors
    ctx.close()
    ctx, pg = open_dash(b, clock="2026-11-21T08:53:00+05:30")
    check("TC-RDY-0016", pg.locator("[data-testid=ready-panel]").count() == 0, "Saturday 08:53: no panel")
    errs += pg._errors
    ctx.close()

    # ── 6. phone: panel below the hero, nothing wider than the screen
    ctx, pg = open_dash(b, width=390, height=900)
    o = pg.evaluate("""(() => { const p = document.querySelector('[data-testid=ready-panel]'); const s = document.querySelector('[data-testid=calm-hero] p');
      return p && s ? { pt: p.getBoundingClientRect().top, sb: s.getBoundingClientRect().bottom, pr: p.getBoundingClientRect().right,
                        sw: document.documentElement.scrollWidth } : null; })()""")
    check("TC-RDY-0017", o and o["pt"] > o["sb"] and o["pr"] <= 390 and o["sw"] <= 392, f"phone: panel under the hero, no sideways scroll ({o})")
    errs += pg._errors
    ctx.close()

    # ── READY_LOGIN_LABEL_20261118 (DEF-062): the Log in label is readable on every theme (Arcade showed an empty pink block)
    LBL = """(() => { const b = document.querySelector('[data-ready=zerodha] [data-testid=ready-login]'); if (!b) return null;
      const cs = getComputedStyle(b); const p = (s) => (s.match(/[\\d.]+/g) || []).map(Number);
      const lum = (c) => { const [r, g, bl] = c.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * bl; };
      const fg = p(cs.color), bg = p(cs.backgroundColor); const a = fg.length > 3 ? fg[3] : 1;
      const L1 = lum(fg), L2 = lum(bg); return { text: b.innerText, fg: cs.color, bg: cs.backgroundColor, alpha: a,
        ratio: Math.round(((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)) * 10) / 10 }; })()"""
    bad = {}
    for th in ("dark", "light", "terminal", "kolam", "arcade"):
        ctx, pg = open_dash(b, z=Z_OFF, theme=th)
        o = pg.evaluate(LBL)
        if not (o and o["text"].strip() == "Log in" and o["alpha"] > 0.9 and o["ratio"] >= 3):
            bad[th] = o
        errs += pg._errors
        ctx.close()
    # ── FEED_PREOPEN_20261118 (DEF-063): before 09:15 a built-but-unconnected feed is ready, not "connecting"
    ctx, pg = open_dash(b, r=dict(R_OK, feed={"state": "connecting", "engines": 1, "last_tick_age_s": None}))
    rf = row(pg, "feed")
    check("TC-RDY-0020", rf and rf["state"] == "ok" and "connects at 09:15" in rf["text"] and "connecting" not in rf["text"],
          f"feed built, waiting for the open → green 'ready · connects at 09:15' ({rf})")
    errs += pg._errors
    ctx.close()
    # ── INDEX_LEFT_20261118 (DEF-064): the NIFTY / BANKNIFTY chips sit left, under This week / This month (not floated right)
    ctx, pg = open_dash(b, market=True)
    pg.wait_for_selector("[data-testid=calm-index-chips] [data-index=NIFTY]", timeout=60000)
    g = pg.evaluate("""(() => { const l = document.querySelector('[data-testid=calm-hero] p').getBoundingClientRect(),
      c = document.querySelector('[data-testid=calm-index-chips] [data-index=NIFTY]').getBoundingClientRect(),
      w = document.querySelector('[data-testid=calm-period-chips]').getBoundingClientRect(); return { pl: l.left, cl: c.left, wl: w.left, wb: w.bottom, ct: c.top }; })()""")
    check("TC-RDY-0021", g and abs(g["cl"] - g["pl"]) <= 4 and abs(g["cl"] - g["wl"]) <= 4 and g["ct"] >= g["wb"] - 1,
          f"index chips start at the left edge, under This week / This month ({g})")
    errs += pg._errors
    ctx.close()
    check("TC-RDY-0019", not bad, f"Log in label readable (opaque, contrast ≥ 3:1) on dark, light, terminal, kolam, arcade ({bad})")

    check("TC-RDY-0018", not errs, f"no page errors ({errs[:2]})")
    b.close()

print(f"\n{'FAIL' if fails else 'OK'}: {N[0] - len(fails)}/{N[0]} checks" + (f" — {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)
