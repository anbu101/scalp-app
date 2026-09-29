# backend/app/tests/test_eod_card_v2.py
#
# ── EOD_CARD_V2_20260929 ── behavioural suite for the redesigned EOD card:
#   renderer   four synthetic days render to PNG, fail-open on error
#   data       readers on a temp SQLite (real epoch clock), footer stats,
#              exit classification, fleet MTM path read (forward-filled)
#   caption    Live · Paper, never summed
#
#   cd backend && PYTHONPATH=$PWD python3 app/tests/test_eod_card_v2.py

import io
import os
import re
import sqlite3
import struct
import sys
import tempfile
import time

import app.api.telegram_summary_card as C
import app.api.telegram_summary_data as D
import app.api.telegram_summary_send as S
import app.api.fleet_today_routes as F

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def png_size(png: bytes):
    return struct.unpack(">II", png[16:24])


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ───────────────────────────── renderer ─────────────────────────────

def test_renderer():
    print("renderer:")
    heights = {}
    for kind in ("empty", "one", "fleet", "big"):
        png = C.build_summary_card_png(C.fixture_card_data(kind))
        check(f"{kind}: renders", bool(png))
        if not png:
            continue
        check(f"{kind}: PNG signature", png[:8] == b"\x89PNG\r\n\x1a\n")
        w, h = png_size(png)
        heights[kind] = h
        check(f"{kind}: width ≈ {C.W_PX}px", abs(w - C.W_PX) <= 2, f"{w}")
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(png)).convert("RGB")
            px = im.getpixel((3, 3))
            check(f"{kind}: background is Midnight {C.BG_CARD}", px == hex_rgb(C.BG_CARD), f"{px}")
            # something teal (profit) or rose (loss) must be drawn on a day with trades
            if kind != "empty":
                colours = {c for _, c in (im.getcolors(1 << 22) or [])}
                check(f"{kind}: profit/loss ink present",
                      hex_rgb(C.UP) in colours or hex_rgb(C.DN) in colours)
        except ImportError:
            print("  skip  PIL not available — pixel checks skipped")
    check("height grows with tiles: empty < one < fleet < big",
          heights.get("empty", 0) < heights.get("one", 0) < heights.get("fleet", 0) < heights.get("big", 0),
          str(heights))
    # fixed pixel layout: every extra tile row adds TILE_H + TILE_GAP (±2 px
    # for matplotlib's inch→px rounding of the figure size)
    check("fleet (10 tiles) − one (1 tile) = 4 tile rows",
          abs(heights.get("fleet", 0) - heights.get("one", 0) - 4 * (C.TILE_H + C.TILE_GAP)) <= 2,
          str(heights))
    check("big (15 tiles) − fleet (10 tiles) = 3 tile rows",
          abs(heights.get("big", 0) - heights.get("fleet", 0) - 3 * (C.TILE_H + C.TILE_GAP)) <= 2,
          str(heights))

    # fail-open contract
    orig = C._render
    C._render = lambda d: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        check("render error → None (fail-open)", C.build_summary_card_png(C.fixture_card_data("fleet")) is None)
    finally:
        C._render = orig

    # a path with garbage points and a book with a single sample still render
    d = C.fixture_card_data("one")
    d.mtm_paths = {"LIVE": [[600, "x"], [601, -12.0], None, [601, -15.0]], "PAPER": [[700, 3.5]]}
    check("dirty / single-sample paths render", bool(C.build_summary_card_png(d)))
    check("_clean_path dedupes by minute, keeps last, drops garbage",
          C._clean_path(d.mtm_paths["LIVE"]) == [(601, -15.0)])

    # dataclass backwards compatibility (positional six-field construction)
    r = C.StrategyRow("X", 3, 2, 1, 10.0, "LIVE")
    check("StrategyRow positional (pre-V2) still constructs",
          r.gross == 0.0 and r.charges == 0.0 and r.best is None and r.exit_other == 0)
    cd = C.CardData("21 Sep 2026")
    check("CardData(date_str) still constructs; combined kept",
          cd.subtitle == "" and cd.mtm_paths == {} and cd.combined == 0.0)
    check("win_rate", abs(r.win_rate - 66.666) < 0.01)

    # footer helpers
    d = C.fixture_card_data("fleet")
    check("best trade = Velvet +64,562", d.best_trade[0] == 64562 and d.best_trade[1].name == "Velvet")
    check("worst trade = Baobab −14,210", d.worst_trade[0] == -14210 and d.worst_trade[1].name == "Baobab")
    check("exit counts sum to trades",
          sum(d.exit_counts) == sum(r.trades for r in d.all_rows), str(d.exit_counts))
    cells = C._footer_cells(d)
    check("footer: 4 cells, labels", [c[0] for c in cells] == ["Best trade", "Worst trade", "Exits", "Charges"])
    check("footer: charges split L/P", cells[3][3].startswith("L \u20b994 \u00b7 P "), cells[3][3])
    e = C.fixture_card_data("empty")
    check("footer on empty day = dashes", all(c[1] == "\u2014" for c in C._footer_cells(e)))

    # formatting
    check("_fmt_signed_rs", C._fmt_signed_rs(64562) == "+\u20b964,562" and C._fmt_signed_rs(-655) == "-\u20b9655")
    check("_hhmm", C._hhmm(555) == "09:15" and C._hhmm(930) == "15:30")


# ───────────────────────────── data layer ───────────────────────────

def test_classify():
    print("exit classification:")
    tp = ["TP", "GTT_TP", "SIG_TP", "MAX_PROFIT", "TARGET", "tp_trail"]
    sl = ["SL", "GTT_SL", "HEDGE_SL", "SIG_SL", "MAX_LOSS", "MTM_SL", "TRAIL_STOP", "MTM"]
    other = ["EOD", "EOD_SQUARE_OFF", "MANUAL", "XOVER", "FLIP", "SIGNAL_EXIT", "EMA_EXIT",
             "EXPIRY_EXIT", "BROKER_EXIT", "KILL", "STALE_RECONCILE", "TIME", None, ""]
    check("target words → tp", all(D._classify_exit(r) == "tp" for r in tp))
    check("stop words → sl", all(D._classify_exit(r) == "sl" for r in sl))
    check("everything else → other", all(D._classify_exit(r) == "other" for r in other))
    check("TP wins over SL when both appear (e.g. group 'TP|HEDGE_SL')", D._classify_exit("TP|HEDGE_SL") == "tp")


def _mk_db(path: str, now: int):
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE trades (trade_id TEXT PRIMARY KEY, strategy_id TEXT, entry_price REAL,
        exit_price REAL, qty INTEGER, trade_direction TEXT, exit_reason TEXT,
        state TEXT, entry_time INTEGER, exit_time INTEGER);
    CREATE TABLE paper_trades (paper_trade_id TEXT PRIMARY KEY, strategy_name TEXT,
        trade_mode TEXT, entry_time INTEGER, exit_time INTEGER, exit_price REAL,
        exit_reason TEXT, total_charges REAL, net_pnl REAL, state TEXT);
    CREATE TABLE tma2_trades (id INTEGER PRIMARY KEY, group_id TEXT, mode TEXT, status TEXT,
        entry_ts INTEGER, exit_ts INTEGER, pnl REAL, charges REAL, net_pnl REAL, exit_reason TEXT);
    """)
    y = now - 86400
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("t1", "SCALP_V1", 100.0, 90.0, 65, "SHORT", "GTT_SL", "CLOSED", now - 3600, now - 60),
        ("t2", "SCALP_V1", 100.0, 90.0, 65, "SHORT", "GTT_SL", "CLOSED", y - 3600, y),       # yesterday
        ("t3", "SCALP_V1", 100.0, None, 65, "SHORT", None, "PROTECTED", now - 600, None),    # open
    ])
    conn.executemany("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("p1", "VET_V1", "PAPER", now - 7200, now - 120, 10.0, "SIGNAL_EXIT", 412.0, 64562.0, "CLOSED"),
        ("p2", "VET_V1", "PAPER", now - 7000, now - 100, 10.0, "SL", 90.0, -1200.0, "CLOSED"),
        ("p3", "VET_V1", "PAPER", now - 6000, now - 90, 10.0, "TP", 80.0, 300.0, "CLOSED"),
        ("p4", "VET_V1", "PAPER", y - 6000, y, 10.0, "TP", 80.0, 999.0, "CLOSED"),            # yesterday
        ("p5", "VET_V1", "PAPER", now - 600, None, None, None, 0.0, None, "OPEN"),           # open
        ("p6", "BRK_V1", "LIVE", now - 3000, now - 30, 10.0, "TP", 60.0, 500.0, "CLOSED"),   # LIVE in paper table
    ])
    conn.executemany("INSERT INTO tma2_trades VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, "g1", "PAPER", "CLOSED", now - 9000, now - 200, -3000.0, 300.0, -3300.0, "XOVER"),
        (2, "g1", "PAPER", "CLOSED", now - 9000, now - 200, 500.0, 200.0, 300.0, "XOVER"),
        (3, "g2", "PAPER", "OPEN", now - 900, None, None, None, None, None),                 # open group
    ])
    conn.commit()
    conn.close()


def test_data_readers():
    print("data readers (temp DB, real clock):")
    now = int(time.time())
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "app.db")
    _mk_db(path, now)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    saved = (D.get_conn, D.get_closed_v3_trades_today_with_prices, D.get_closed_v5_trades_today_with_prices)
    D.get_conn = lambda: conn
    D.get_closed_v3_trades_today_with_prices = lambda paper: (
        [{"hedge_entry_price": 100.0, "exit_price": 110.0, "hedge_qty": 65,
          "realized_pnl": 650.0, "exit_reason": "SIG_TP", "hedge_symbol": "NIFTY26SEP25000CE"}]
        if paper else [])
    D.get_closed_v5_trades_today_with_prices = lambda paper: []
    try:
        live = {r.name: r for r in D._live_rows()}
        paper = {r.name: r for r in D._paper_rows()}
    finally:
        D.get_conn, D.get_closed_v3_trades_today_with_prices, D.get_closed_v5_trades_today_with_prices = saved

    # LIVE: SCALP_V1 (Scala) — one SHORT closed today; yesterday's + open excluded
    s = live.get("Scala")
    check("live: Scala present (codenamed)", s is not None)
    if s:
        check("live: 1 trade today only", s.trades == 1 and s.losses == 0 and s.wins == 1)
        check("live: gross 650 (SHORT 100→90 × 65)", abs(s.gross - 650.0) < 1e-6, str(s.gross))
        check("live: net = gross − charges, charges > 0", s.charges > 0 and abs(s.net - (650.0 - s.charges)) < 1e-6)
        check("live: GTT_SL → exit_sl", (s.exit_tp, s.exit_sl, s.exit_other) == (0, 1, 0))
        check("live: best == worst == net on a single trade", s.best == s.worst == s.net)
    b = live.get("Breaker")
    check("live: Breaker from paper_trades LIVE union", b is not None)
    if b:
        check("live: Breaker net 500, charges 60, TP", b.net == 500.0 and b.charges == 60.0 and b.exit_tp == 1)
        check("live: Breaker gross = net (unknown gross, pre-V2 behaviour)", b.gross == 500.0)
    check("live: no paper strategy leaked into LIVE", "Velvet" not in live and "Timberwolf" not in live)

    # PAPER: VET_V1 (Velvet) — three closed today
    v = paper.get("Velvet")
    check("paper: Velvet present", v is not None)
    if v:
        check("paper: 3 trades, 2W 1L", (v.trades, v.wins, v.losses) == (3, 2, 1))
        check("paper: net 63,662", abs(v.net - 63662.0) < 1e-6, str(v.net))
        check("paper: charges 582", abs(v.charges - 582.0) < 1e-6, str(v.charges))
        check("paper: best 64,562 / worst −1,200", v.best == 64562.0 and v.worst == -1200.0)
        check("paper: exits tp/sl/other = 1/1/1", (v.exit_tp, v.exit_sl, v.exit_other) == (1, 1, 1))
    check("paper: BRK_V1 LIVE row not in PAPER", "Breaker" not in paper)
    sc = paper.get("Scenic")
    check("paper: SCALP_V3 row via repo seam (Scenic)", sc is not None)
    if sc:
        check("paper: V3 gross 650, net = 650 − charges, SIG_TP → tp",
              abs(sc.gross - 650.0) < 1e-6 and sc.charges > 0 and sc.exit_tp == 1)
    tw = paper.get("Timberwolf")
    check("paper: TMA_V2 group (Timberwolf) counted once", tw is not None and tw.trades == 1)
    if tw:
        check("paper: TMA_V2 net −3,000 gross −2,500 charges 500 (gross − net)",
              tw.net == -3000.0 and tw.gross == -2500.0 and abs(tw.charges - 500.0) < 1e-6,
              f"{tw.net} {tw.gross} {tw.charges}")
        check("paper: XOVER → other", (tw.exit_tp, tw.exit_sl, tw.exit_other) == (0, 0, 1))

    # end-to-end build_card_data with the fleet path seam stubbed
    saved2 = (D.get_conn, D.get_closed_v3_trades_today_with_prices,
              D.get_closed_v5_trades_today_with_prices, D._fleet_paths)
    D.get_conn = lambda: conn
    D.get_closed_v3_trades_today_with_prices = lambda paper: []
    D.get_closed_v5_trades_today_with_prices = lambda paper: []
    D._fleet_paths = lambda now=None: {"PAPER": [[555, 0.0], [600, 12.5]]}
    try:
        cd = D.build_card_data()
    finally:
        (D.get_conn, D.get_closed_v3_trades_today_with_prices,
         D.get_closed_v5_trades_today_with_prices, D._fleet_paths) = saved2
    check("build_card_data: subtitle 'Ddd DD Mon YYYY · HH:MM IST'",
          re.match(r"^[A-Z][a-z]{2} \d{2} [A-Z][a-z]{2} \d{4} \u00b7 \d{2}:\d{2} IST$", cd.subtitle) is not None,
          cd.subtitle)
    check("build_card_data: date_str unchanged format", re.match(r"^\d{2} [A-Z][a-z]{2} \d{4}$", cd.date_str) is not None)
    check("build_card_data: mtm_paths passthrough", cd.mtm_paths == {"PAPER": [[555, 0.0], [600, 12.5]]})
    check("build_card_data: live/paper rows populated", len(cd.live_rows) == 2 and len(cd.paper_rows) == 2)
    png = C.build_summary_card_png(cd)
    check("build_card_data → render OK", bool(png))
    conn.close()


def test_fleet_paths():
    print("fleet MTM path (fleet_mtm_samples, real clock):")
    now = int(time.time())
    day = F._day_key(now)
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "app.db")
    with sqlite3.connect(path) as c:
        F._ensure_table(c)
        c.executemany("INSERT INTO fleet_mtm_samples VALUES (?,?,?,?)", [
            (day, "VET_V1:PAPER", 600, 100.0),
            (day, "VET_V1:PAPER", 602, 150.0),
            (day, "TSG_V1:PAPER", 601, -50.0),
            (day, "ORB_V1:LIVE", 601, -20.0),
            (day, "__PAPER__", 600, 999.0),                 # legacy total key — must be ignored
            (F._day_key(now - 86400), "VET_V1:PAPER", 600, 555.0),   # yesterday — must be ignored
        ])
        c.commit()
    saved = F._db_path
    F._db_path = lambda: path
    try:
        paths = D._fleet_paths(now)
    finally:
        F._db_path = saved
    check("books present", set(paths) == {"LIVE", "PAPER"}, str(paths))
    check("PAPER forward-filled sum: 600→100, 601→50, 602→100",
          paths.get("PAPER") == [[600, 100.0], [601, 50.0], [602, 100.0]], str(paths.get("PAPER")))
    check("LIVE path", paths.get("LIVE") == [[601, -20.0]], str(paths.get("LIVE")))
    check("legacy __PAPER__ key ignored", all(v != 999.0 and v < 500 for _, v in paths.get("PAPER", [])))

    # failure → {} (fail-open), never raises
    F._db_path = lambda: "/nonexistent/dir/app.db"
    try:
        check("unreadable DB → {}", D._fleet_paths(now) == {})
    finally:
        F._db_path = saved
    # empty day → {}
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM fleet_mtm_samples")
        c.commit()
    F._db_path = lambda: path
    try:
        check("no samples → {}", D._fleet_paths(now) == {})
    finally:
        F._db_path = saved


# ───────────────────────────── caption ──────────────────────────────

def test_caption():
    print("caption:")
    d = C.fixture_card_data("fleet")
    check("Live · Paper, both books",
          S._book_caption(d) == "Live -\u20b9655 \u00b7 Paper +\u20b933,703", S._book_caption(d))
    o = C.fixture_card_data("one")
    check("idle Paper reads 'no trades'", S._book_caption(o) == "Live -\u20b9655 \u00b7 Paper no trades", S._book_caption(o))
    e = C.fixture_card_data("empty")
    check("both idle", S._book_caption(e) == "Live no trades \u00b7 Paper no trades")
    check("no 'Combined' anywhere in the sender", "Combined" not in open(S.__file__, encoding="utf-8").read())


if __name__ == "__main__":
    test_renderer()
    test_classify()
    test_data_readers()
    test_fleet_paths()
    test_caption()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")
