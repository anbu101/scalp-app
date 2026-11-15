# test_ui_fleet.py — tracker T5: the two UI pieces the live fleet relies on,
# checked in a real browser (headless Chrome) on the BUILT frontend:
#   1. the alert bell — fed by the REAL in-app event feed (the same
#      record_alert / notify_critical calls the strategies make);
#   2. the Dashboard live chart's open-position rule — the REAL replay route on
#      a real app.db with live Icarus legs booked as the manager books them.
#
#   cd frontend && CI=false npm run build
#   PYTHONPATH=../backend python3 uitest/test_ui_fleet.py   [CHROME=/path/to/chrome]
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_server as U                                                  # noqa: E402

PASSED, FAILS = [0], []
IST = timezone(timedelta(hours=5, minutes=30))


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


def wait_for(fn, timeout=12.0, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


from app.event_bus import inapp_events as EV                          # noqa: E402
from app.db.sqlite import get_conn                                     # noqa: E402
import app.db.trades_repo as TR                                        # noqa: E402

if os.environ.get("UITEST_OLD_OPEN_RULE") == "1":     # falsify: the rule before 11 Nov
    import app.backtest.replay.live_replay as _LR
    _LR._row_open = lambda r: str(r.get("state") or r.get("status") or "").upper() == "OPEN"
U.start()
from playwright.sync_api import sync_playwright                        # noqa: E402

CHROME = os.environ.get("CHROME") or next((p for p in ("/usr/bin/google-chrome",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if os.path.exists(p)), None)
BASE = "http://127.0.0.1:47321/"


def open_page(browser, *, focus="IC_V2", admin=False):
    U.LICENSE["ui_level"] = "admin" if admin else "user"
    U.LICENSE["strategies"] = ["*"] if admin else ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1"]
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()
    pg._errors = []
    pg.on("pageerror", lambda e: pg._errors.append(str(e)[:300]))
    pg.add_init_script(f"localStorage.setItem('scalp.strategyHost.focusId','{focus}')")
    pg.goto(BASE, wait_until="domcontentloaded")
    wait_for(lambda: pg.locator("button[title=Notifications]").count() > 0, 20)
    time.sleep(1.5)                       # the feed's first poll sets the cursor
    return ctx, pg


def badge(pg):
    b = pg.locator("button[title=Notifications] span").filter(has_text=re.compile(r"^\d+\+?$"))
    if b.count() == 0:
        return None, None
    return b.first.inner_text().strip(), b.first.evaluate("e => getComputedStyle(e).backgroundColor")


def rgb(c):
    m = re.findall(r"\d+", c or "")
    return tuple(int(x) for x in m[:3]) if len(m) >= 3 else (0, 0, 0)


def is_red(c):
    r, g, b = rgb(c)
    return r >= 200 and g < 110 and b < 110


def is_amber(c):
    r, g, b = rgb(c)
    return r >= 200 and 100 <= g <= 200 and b < 80

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    # ═══ 1. the alert bell (the friend's view: user licence, the four strategies) ═══
    ctx, pg = open_page(browser)
    check("U1: the app opens on the Dashboard with the bell, no badge, no page errors",
          badge(pg)[0] is None and not pg._errors, (badge(pg), pg._errors[:2]))
    check("U1: the friend's view shows the Icarus panel", pg.get_by_text("Icarus").count() > 0)

    EV.record_alert("TSG_EXIT_STUCK", "TSG_V1 KILL: exit of L1 NOT CONFIRMED — SHORT STILL OPEN.",
                    severity="error", strategy_id="TSG_V1", mode="live")
    got = wait_for(lambda: badge(pg)[0] == "1" and badge(pg), 10)
    check("U2: a stuck-exit ERROR from a strategy reaches the bell within one poll: badge 1, RED",
          got and is_red(got[1]), badge(pg))
    EV.record_alert("TMA_BUYBACK_SKIPPED", "TMA_V2: already flat at the broker — buy-back skipped. Verify in Kite.",
                    severity="warning", strategy_id="TMA_V2", mode="live")
    got = wait_for(lambda: badge(pg)[0] == "2" and badge(pg), 10)
    check("U3: a warning on top: badge 2, stays RED while an error is unread", got and is_red(got[1]), badge(pg))

    from app.api import telegram_api as TG
    TG._send = getattr(TG, "_send", None)
    try:
        TG.notify_critical({"message": "IC_V2: L1 short is OPEN but its SL GTT is GONE from Kite.",
                            "severity": "error"})
    except Exception as e:
        print("   (notify_critical raised outside the in-app mirror:", repr(e)[:120], ")")
    got = wait_for(lambda: badge(pg)[0] == "3", 10)
    check("U4: a critical page (notify_critical — GTT gone, naked short) is mirrored to the bell: badge 3",
          bool(got), badge(pg))

    from app.license import license_state as LIC
    LIC.ENTITLEMENTS = {"strategies": ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1"]}
    EV.record_alert("SOME_WARN", "BB_V1: not in this licence", severity="warning",
                    strategy_id="BB_V1", mode="live")
    EV.record_alert("SOME_ERR", "BB_V1: an ERROR is never hidden by the licence", severity="error",
                    strategy_id="BB_V1", mode="live")
    got = wait_for(lambda: badge(pg)[0] == "4", 10)
    time.sleep(3.5)
    check("U5: licence gate: a warning of an unlicensed strategy is held back, its ERROR still shows (4, not 5)",
          badge(pg)[0] == "4", badge(pg))

    pg.locator("button[title=Notifications]").click()
    panel_txt = wait_for(lambda: "NOT CONFIRMED" in pg.inner_text("body") and pg.inner_text("body"), 5) or ""
    check("U6: opening the bell lists the alerts in full (stuck exit, buy-back skipped, GTT gone)",
          all(s in panel_txt for s in ("NOT CONFIRMED", "buy-back skipped", "SL GTT is GONE")),
          [s for s in ("NOT CONFIRMED", "buy-back skipped", "SL GTT is GONE") if s not in panel_txt])
    check("U6: …errors are marked with the red ✕", "✕" in panel_txt)
    check("U7: opening the bell marks them read: the badge is gone", wait_for(lambda: badge(pg)[0] is None, 5))
    check("U8: no page errors while alerts arrived", not pg._errors, pg._errors[:3])
    ctx.close()

    ctx, pg = open_page(browser)
    time.sleep(4)
    check("U9: a reload does not replay old alerts as new (no badge)", badge(pg)[0] is None, badge(pg))
    EV.record_alert("IC_EXIT_FAIL", "IC_V2: exit NOT CONFIRMED", severity="error", strategy_id="IC_V2", mode="live")
    check("U9: …and a new alert after the reload still arrives", wait_for(lambda: badge(pg)[0] == "1", 10), badge(pg))
    pg.locator("button[title=Notifications]").click()
    pg.locator("button[title=Notifications]").click()
    EV.record_alert("VET_NOTE", "VET_V1: wing re-priced", severity="warning", strategy_id="VET_V1", mode="live")
    got = wait_for(lambda: badge(pg)[0] == "1" and badge(pg), 10)
    check("U10: only a WARNING unread: the badge is AMBER, not red", got and is_amber(got[1]), badge(pg))
    ctx.close()

    # ═══ 2. the Dashboard live chart (admin view only, by design) ═══
    ctx, pg = open_page(browser)
    check("C0: the friend's (user) view has no live chart card (admin-only by design)",
          pg.locator("[data-testid=live-chart-card]").count() == 0)
    ctx.close()

    def card_text(pg):
        c = pg.locator("[data-testid=live-chart-card] button").first
        return c.inner_text() if c.count() else ""

    ctx, pg = open_page(browser, admin=True)
    t = wait_for(lambda: "·" in card_text(pg) and card_text(pg), 15) or card_text(pg)
    check("C1: no Icarus position: 'Live chart · watching for an entry'", "watching for an entry" in t, t)
    ctx.close()

    now = datetime.now(IST)
    t0 = int((now - timedelta(minutes=20)).timestamp())
    conn = get_conn()
    gid = "IC-UITEST-1"
    for tid, slot, sym, px, short in (("u-l1", "L1", "NIFTY26N1725750CE", 93.5, True),
                                      ("u-l2", "L2", "NIFTY26N1725000PE", 93.4, True),
                                      ("u-l3", "L3", "NIFTY26N1726800CE", 7.5, False),
                                      ("u-l4", "L4", "NIFTY26N1723850PE", 7.8, False)):
        TR.insert_trade(trade_id=tid, strategy_id="IC_V2", slot=f"IC_V2_{slot}", symbol=sym, token=0,
                        entry_price=px, qty=195, buy_order_id="OID", sl_price=0.0, tp_price=0.0,
                        tp_mode="GTT", state="PROTECTED", trade_direction="SHORT" if short else "LONG",
                        group_id=gid, trade_class=slot)
        conn.execute("UPDATE trades SET entry_time=? WHERE trade_id=?", (t0, tid))
    conn.commit()
    ctx, pg = open_page(browser, admin=True)
    t = wait_for(lambda: "·" in card_text(pg) and card_text(pg), 15) or card_text(pg)
    check("C2: a LIVE Icarus condor booked as the manager books it (state PROTECTED): "
          "'Live chart · open position' (11 Nov: said 'watching')", "open position" in t, t)
    check("C2: no page errors", not pg._errors, pg._errors[:2])
    ctx.close()

    conn.execute("UPDATE trades SET exit_time=?, exit_price=?, state='CLOSED' WHERE group_id=?",
                 (int(now.timestamp()) - 60, 50.0, gid))
    conn.commit()
    ctx, pg = open_page(browser, admin=True)
    t = wait_for(lambda: "·" in card_text(pg) and card_text(pg), 15) or card_text(pg)
    check("C3: every leg closed: back to 'watching for an entry'", "watching for an entry" in t, t)
    ctx.close()
    browser.close()

print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL UI CHECKS PASSED")
