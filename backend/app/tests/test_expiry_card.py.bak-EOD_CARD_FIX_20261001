# backend/app/tests/test_expiry_card.py
#
# ── EXPIRY_CARD_20260929 ── behavioural suite for the week + month expiry card:
#   renderer   three fixtures render, fail-open, compact formatting
#   data       buckets from synthetic legs on the REAL epoch clock through the
#              dashboard's own window functions; baskets, approx, truncation,
#              cells, positive days, one failing strategy does not blank the card
#   sender     caption / fallback text, expiry-day gate, photo-fail -> text
#   scheduler  fires once at 15:40 on an expiry day, never on other days,
#              skips when the calendar is unavailable
#
#   cd backend && PYTHONPATH=$PWD python3 app/tests/test_expiry_card.py

import io
import struct
import sys
import time
from datetime import date, datetime, timedelta, timezone

import app.api.telegram_expiry_card as C
import app.api.telegram_expiry_data as D
import app.api.telegram_expiry_send as S
import app.api.fleet_today_routes as F
import app.api.closed_recent_routes as CR
import app.services.telegram_scheduler as SCH

FAILS = []
IST = timezone(timedelta(hours=5, minutes=30))


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ───────────────────────────── renderer ─────────────────────────────

def test_renderer():
    print("renderer:")
    heights = {}
    for kind in ("empty", "live", "fleet"):
        png = C.build_expiry_card_png(C.fixture_expiry_data(kind))
        check(f"{kind}: renders", bool(png))
        if not png:
            continue
        check(f"{kind}: PNG signature", png[:8] == b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        heights[kind] = h
        check(f"{kind}: width \u2248 {C.W_PX}px", abs(w - C.W_PX) <= 2, str(w))
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(png)).convert("RGB")
            check(f"{kind}: Midnight background", im.getpixel((3, 3)) == hex_rgb(C.BG_CARD))
            if kind != "empty":
                colours = {c for _, c in (im.getcolors(1 << 22) or [])}
                check(f"{kind}: profit/loss ink present", hex_rgb(C.UP) in colours or hex_rgb(C.DN) in colours)
        except ImportError:
            print("  skip  PIL not available")
    check("height: empty < live < fleet", heights.get("empty", 0) < heights.get("live", 0) < heights.get("fleet", 0), str(heights))
    check("fleet (14 rows) \u2212 live (2 rows) = 12 table rows",
          abs(heights.get("fleet", 0) - heights.get("live", 0) - 12 * C.ROW_H) <= 2, str(heights))

    orig = C._render
    C._render = lambda d: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        check("render error \u2192 None (fail-open)", C.build_expiry_card_png(C.fixture_expiry_data("fleet")) is None)
    finally:
        C._render = orig

    check("_fmt_k", (C._fmt_k(18150), C._fmt_k(-8220), C._fmt_k(655), C._fmt_k(-130250), C._fmt_k(0))
          == ("+18.2k", "-8.2k", "+655", "-130k", "+0"))
    d = C.fixture_expiry_data("fleet")
    check("best session = Tue +33,705 Paper", d.best_session[0].net == 33705 and d.best_session[1] == "PAPER")
    check("worst session = Thu \u22128,220 Paper", d.worst_session[0].net == -8220 and d.worst_session[0].label == "Thu")
    cells = C._footer_cells(d)
    check("footer labels", [c[0] for c in cells] == ["Best session", "Worst session", "Positive days", "Charges"])
    check("footer positive days P 12/20, L 8/17", cells[2][1] == "P 12/20" and cells[2][3].startswith("L 8/17"))
    check("footer charges L/P split", cells[3][3] == "L \u20b91,860 \u00b7 P \u20b939,440", cells[3][3])
    e = C.fixture_expiry_data("empty")
    check("empty: no trades anywhere, footer dashes", not e.any_trades and all(c[1] == "\u2014" for c in C._footer_cells(e)))
    # cells missing on one book still render (template borrowed from the other book)
    d2 = C.fixture_expiry_data("live")
    d2.week["PAPER"].cells = []
    check("book without cells renders", bool(C.build_expiry_card_png(d2)))


# ───────────────────────────── data layer ───────────────────────────

def _leg(book, entry_ts, exit_ts, gross, charges=50.0, approx=False, open_=False, short=True):
    return {"id": f"{book}:{entry_ts}", "book": book, "symbol": "NIFTY", "short": short, "qty": 65,
            "lots": 1, "entry": 100.0, "exit": 90.0, "entry_ts": int(entry_ts), "exit_ts": (None if open_ else int(exit_ts)),
            "reason": "SL", "open": open_, "gross": float(gross), "charges": float(charges),
            "net": float(gross) - float(charges), "approx": approx, "leg": None}


def test_data():
    print("data (synthetic legs, real clock, dashboard windows):")
    now = int(time.time())
    day0 = CR._day_start(now)
    week_from = F._week_from(day0)
    month_from = F._month_from(day0)
    H = 3600
    D_ = 86400

    legs = {
        # VET_V1: paper — today (in week), 3 days ago (in week), 12 days ago (month only), 40 days ago (out)
        "VET_V1": [
            _leg("PAPER", now - 2 * H, now - H, 64562.0, 412.0),
            _leg("PAPER", now - 3 * D_ - 2 * H, now - 3 * D_ - H, -1200.0, 90.0),
            _leg("PAPER", now - 12 * D_ - 2 * H, now - 12 * D_ - H, 300.0, 80.0),
            _leg("PAPER", now - 40 * D_ - 2 * H, now - 40 * D_ - H, 9999.0, 80.0),
            _leg("PAPER", now - 600, None, 0.0, 0.0, open_=True),                     # open — ignored
            _leg("LIVE", now - 5 * D_ - 2 * H, now - 5 * D_ - H, 700.0, 60.0),        # live, in week
        ],
        # TSG_V1: paper basket — two legs entered in the same minute 10 days ago = ONE position
        "TSG_V1": [
            _leg("PAPER", now - 10 * D_ - 4 * H, now - 10 * D_ - H, -3000.0, 100.0),
            _leg("PAPER", now - 10 * D_ - 4 * H + 30, now - 10 * D_ - H, 500.0, 100.0),
        ],
        # ORB_V1: live, modelled charges (approx) 8 days ago
        "ORB_V1": [_leg("LIVE", now - 8 * D_ - 2 * H, now - 8 * D_ - H, -655.0, 94.0, approx=True)],
    }

    saved = F._legs_for
    F._legs_for = lambda sid, warns: legs.get(sid, [])
    try:
        data = D.build_expiry_card_data(now, expiry=True)
    finally:
        F._legs_for = saved

    names = {(r.name, r.mode): r for r in data.rows}
    v = names.get(("Velvet", "PAPER"))
    check("Velvet paper row present (codenamed)", v is not None)
    if v:
        check("Velvet week: 2 positions, net 64,562\u2212412 + (\u22121,200\u221290)", v.week_trades == 2 and abs(v.week_net - (64150.0 - 1290.0)) < 1e-6, f"{v.week_trades} {v.week_net}")
        check("Velvet month: 3 positions (40-day-old leg excluded, open leg excluded)", v.month_trades == 3 and abs(v.month_net - (64150.0 - 1290.0 + 220.0)) < 1e-6, f"{v.month_trades} {v.month_net}")
        check("Velvet not approx", not v.approx)
    vl = names.get(("Velvet", "LIVE"))
    check("Velvet live row separate from paper", vl is not None and vl.week_trades == 1 and vl.month_net == 640.0)
    t = names.get(("Tigris", "PAPER"))
    check("TSG basket counted as ONE position, net \u22122,700", t is not None and t.month_trades == 1 and t.month_net == -2700.0 and t.week_trades == 0, str(t))
    o = names.get(("Outrider", "LIVE"))
    check("ORB live row approx (modelled charges)", o is not None and o.approx and o.month_net == -749.0)
    check("rows: Live first, then |month| desc",
          [r.mode for r in data.rows][:2] == ["LIVE", "LIVE"]
          and [r.name for r in data.rows if r.mode == "PAPER"] == ["Velvet", "Tigris"], str([(r.name, r.mode) for r in data.rows]))

    wp, wl = data.book("week", "PAPER"), data.book("week", "LIVE")
    mp, ml = data.book("month", "PAPER"), data.book("month", "LIVE")
    check("week PAPER totals", wp.trades == 2 and wp.wins == 1 and abs(wp.net - 62860.0) < 1e-6 and abs(wp.charges - 502.0) < 1e-6, f"{wp}")
    check("week LIVE totals", wl.trades == 1 and wl.wins == 1 and wl.net == 640.0)
    check("month PAPER totals (Velvet 3 + TSG basket 1)", mp.trades == 4 and mp.wins == 2 and abs(mp.net - (63080.0 - 2700.0)) < 1e-6, f"{mp.net} {mp.trades} {mp.wins}")
    check("month LIVE totals (Velvet + Outrider)", ml.trades == 2 and abs(ml.net - (640.0 - 749.0)) < 1e-6 and ml.approx)
    check("month PAPER positive days 2 of 3 (today +, 3d ago \u2212, 12d ago +, TSG 10d ago \u2212 => 4 days, 2 positive)",
          mp.sessions == 4 and mp.positive_sessions == 2, f"{mp.sessions}/{mp.positive_sessions}")
    check("week strip: today's cell carries +64,150 for PAPER",
          any(c.key == D._ist_date(now - H).isoformat() and c.net == 64150.0 and c.trades == 1 for c in wp.cells), str([(c.key, c.net) for c in wp.cells]))
    check("week strip: sessions in window only (\u2264 7 cells), all keys within window",
          0 < len(wp.cells) <= 7 and all(c.key >= D._ist_date(week_from).isoformat() for c in wp.cells))
    exp_key = D._expected_expiry(D._ist_date(now - 12 * D_ - H)).isoformat()
    check("month strip: 12-day-old leg lands in its expected-expiry cell",
          any(c.key == exp_key and c.trades >= 1 for c in mp.cells), str([(c.key, c.net) for c in mp.cells]))
    check("month strip keys are Tuesdays (expected expiry era)",
          all(date.fromisoformat(c.key).weekday() == 1 for c in mp.cells), str([c.key for c in mp.cells]))
    check("templates: both books share the same cell keys",
          [c.key for c in wp.cells] == [c.key for c in wl.cells] and [c.key for c in mp.cells] == [c.key for c in ml.cells])
    check("subtitle ends with 'weekly expiry'", data.subtitle.endswith("IST \u00b7 weekly expiry"), data.subtitle)
    check("week_range mentions session count", "session" in data.week_range, data.week_range)
    check("month_range 'since <day> \u00b7 by expiry week'", data.month_range.startswith("since ") and data.month_range.endswith("by expiry week"), data.month_range)
    check("no warnings", data.warnings == [], str(data.warnings))
    check("renders", bool(C.build_expiry_card_png(data)))

    # truncation => approx: ROW_LIMIT legs for one (sid, book), oldest entered inside the window
    many = [_leg("PAPER", now - 2 * D_ - 4 * H - i, now - 2 * D_ - H, 10.0, 1.0) for i in range(CR.ROW_LIMIT)]
    F._legs_for = lambda sid, warns: many if sid == "IC_V2" else []
    try:
        d2 = D.build_expiry_card_data(now)
    finally:
        F._legs_for = saved
    r = next(r for r in d2.rows if r.name == "Icarus")
    check("legs read at ROW_LIMIT inside the window \u2192 approx", r.approx and d2.book("month", "PAPER").approx)

    # one strategy's read raising does not blank the card
    def boom(sid, warns):
        if sid == "IC_V2":
            raise RuntimeError("db locked")
        return legs.get(sid, [])
    F._legs_for = boom
    try:
        d3 = D.build_expiry_card_data(now)
    finally:
        F._legs_for = saved
    check("one failing strategy \u2192 warning, other rows intact", len(d3.rows) == len(data.rows) and any("IC_V2" in w for w in d3.warnings), str(d3.warnings))

    # manual (non-expiry) subtitle
    F._legs_for = lambda sid, warns: []
    try:
        d4 = D.build_expiry_card_data(now, expiry=False)
    finally:
        F._legs_for = saved
    check("manual run subtitle", d4.subtitle.endswith("not an expiry day (manual)") and d4.rows == [] and not d4.any_trades)
    check("empty real-window card renders", bool(C.build_expiry_card_png(d4)))


# ───────────────────────────── sender ───────────────────────────────

def test_sender():
    print("sender:")
    d = C.fixture_expiry_data("fleet")
    cap = S.expiry_caption(d)
    check("caption: week line", "Week: <b>Live +\u20b9965 \u00b7 Paper +\u20b945,612</b>" in cap, cap)
    check("caption: month line", "Month: <b>Live -\u20b91,395 \u00b7 Paper +\u20b9130,250</b>" in cap, cap)
    check("caption never sums the books", "Combined" not in cap and "Total" not in cap)
    lv = C.fixture_expiry_data("live")
    check("idle book reads 'no trades'", "Paper no trades" in S.expiry_caption(lv))
    check("text fallback = caption + note", S.expiry_text(d).startswith(cap) and "fallback" in S.expiry_text(d))

    import app.engine.dte_live as DL
    saved = DL.live_dte
    try:
        DL.live_dte = lambda day: 0
        check("live_dte 0 \u2192 expiry day", S.is_expiry_day(date(2026, 9, 29)) is True)
        DL.live_dte = lambda day: 3
        check("live_dte 3 \u2192 not expiry day", S.is_expiry_day(date(2026, 9, 25)) is False)
        DL.live_dte = lambda day: None
        check("live_dte None \u2192 None (calendar unavailable)", S.is_expiry_day(date(2026, 9, 25)) is None)
        DL.live_dte = lambda day: (_ for _ in ()).throw(RuntimeError("x"))
        check("live_dte raising \u2192 None", S.is_expiry_day(date(2026, 9, 25)) is None)
    finally:
        DL.live_dte = saved
    # real calendar: 29 Sep 2026 is a Tuesday expiry, 28 Sep a Monday 1DTE
    check("real calendar: Tue 29 Sep 2026 is expiry day", S.is_expiry_day(date(2026, 9, 29)) is True)
    check("real calendar: Mon 28 Sep 2026 is not", S.is_expiry_day(date(2026, 9, 28)) is False)

    sent = []
    msgs = []
    sp, sm = S._send_photo, S.send_telegram_message
    S._send_photo = lambda tok, chat, png, caption: (sent.append((chat, len(png), caption)) or True)
    S.send_telegram_message = lambda tok, chat, msg, parse_mode="HTML": (msgs.append((chat, msg)) or True)
    try:
        ok = S.send_expiry_summary_card(bot_token="t", chat_id="c1", data=d)
        check("photo path: sendPhoto called once with caption, no text", ok and len(sent) == 1 and sent[0][2] == cap and msgs == [])
        sent.clear()
        S._send_photo = lambda tok, chat, png, caption: False
        ok = S.send_expiry_summary_card(bot_token="t", chat_id="c2", data=d)
        check("sendPhoto fails \u2192 text fallback to the same chat", not ok and msgs and msgs[-1][0] == "c2" and "fallback" in msgs[-1][1])
        msgs.clear()
        ok = S.send_expiry_summary_card(bot_token="t", chat_id="c3", data=None)
        check("data None \u2192 one-line notice", not ok and msgs and "could not be built" in msgs[-1][1])
        msgs.clear()
        orig = C._render
        C._render = lambda dd: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            ok = S.send_expiry_summary_card(bot_token="t", chat_id="c4", data=d)
        finally:
            C._render = orig
        check("render None \u2192 text fallback", not ok and msgs and msgs[-1][0] == "c4")
        check("missing chat_id \u2192 False, nothing sent", S.send_expiry_summary_card(bot_token="t", chat_id="", data=d) is False)
    finally:
        S._send_photo, S.send_telegram_message = sp, sm


# ───────────────────────────── scheduler ────────────────────────────

def test_scheduler():
    print("scheduler gate:")
    sch = SCH.TelegramScheduler()
    calls = []
    saved = (SCH.is_expiry_day, SCH._iter_active_channels, SCH.send_expiry_summary_card, SCH.build_expiry_card_data_once)
    SCH._iter_active_channels = lambda key, **kw: [("tok", "chat1", {}), ("tok", "chat2", {})]
    SCH.send_expiry_summary_card = lambda **kw: calls.append(kw) or True
    SCH.build_expiry_card_data_once = lambda **kw: C.fixture_expiry_data("fleet")
    try:
        at = datetime(2026, 9, 29, sch.SUMMARY_HOUR, sch.SUMMARY_MINUTE, 5, tzinfo=IST)
        SCH.is_expiry_day = lambda day: True
        sch._handle_expiry_summary(at.replace(hour=10))
        check("not the summary minute \u2192 nothing", calls == [])
        sch._handle_expiry_summary(at)
        check("15:40 on an expiry day \u2192 one send per channel", len(calls) == 2 and {c["chat_id"] for c in calls} == {"chat1", "chat2"})
        sch._handle_expiry_summary(at.replace(second=35))
        check("same day, next tick \u2192 not again", len(calls) == 2)
        check("data built once and shared", calls[0]["data"] is calls[1]["data"])

        sch2 = SCH.TelegramScheduler()
        SCH.is_expiry_day = lambda day: False
        sch2._handle_expiry_summary(at)
        check("non-expiry day \u2192 nothing, day marked", len(calls) == 2 and sch2._last_expiry_date == "2026-09-29")

        sch3 = SCH.TelegramScheduler()
        SCH.is_expiry_day = lambda day: None
        sch3._handle_expiry_summary(at)
        check("calendar unavailable \u2192 skipped, day marked", len(calls) == 2 and sch3._last_expiry_date == "2026-09-29")

        sch4 = SCH.TelegramScheduler()
        SCH.is_expiry_day = lambda day: False
        sch4.run_expiry_summary_now()
        check("manual trigger sends on a non-expiry day too", len(calls) == 4)
        check("scheduler tick order: daily then expiry", "self._handle_daily_summary(now)" in open(SCH.__file__, encoding="utf-8").read().split("self._handle_expiry_summary(now)")[0])
    finally:
        SCH.is_expiry_day, SCH._iter_active_channels, SCH.send_expiry_summary_card, SCH.build_expiry_card_data_once = saved


if __name__ == "__main__":
    test_renderer()
    test_data()
    test_sender()
    test_scheduler()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
