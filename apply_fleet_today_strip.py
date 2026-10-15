#!/usr/bin/env python3
# apply_fleet_today_strip.py — fence FLEET_TODAY_STRIP_20261015
#
# Goal: a user never sees two unexplained numbers for one strategy.
# The "Traded today" row and the strategy's panel disagreed for three reasons:
#   1. SCOPE — row = everything today (open legs + every leg closed today,
#      incl. a carried position closed this morning); panel = the current
#      position from its entry. (IC V2: +1,04,293 vs +95,290 — the +9,003 gap
#      was yesterday's position closed at 09:16.)
#   2. GROSS vs NET — the row is gross with charges in their own column; some
#      panels' day figures are net.
#   3. STALE PRICES (a bug) — the row priced open legs from LTPStore with no
#      age check, so a symbol last published by ANOTHER strategy kept that
#      strategy's old print (Velvet's wing at Icarus' 09:16 exit 2.50 all day
#      while live was 4.55).
#
# FIX
#   * fleet_today_routes: during market hours a print older than 90 s is not
#     live — the newest of (that print, last seen mark, a batched REST quote,
#     once per 60 s, outside 09:15–09:20) is used and the row says "marks as
#     of". After the close: unchanged.
#   * NEW TodayStrip directly above the focused panel on the Dashboard: the
#     row's own numbers from the SAME /api/fleet/today payload (MTM today =
#     realised + unrealised · charges · net) and one sentence saying what it
#     counts and what the panel below measures.
#   * Panel headline labels say what they are: "Position MTM" (IC, TSG, VET,
#     BRK), "Closed today · net" (TMA V1/V2), "Day total · net" (VET),
#     "Day P&L · net" (EMC), "Day P&L · gross" (ORB).
#
#  NEW   frontend/src/components/fleet/TodayStrip.jsx
#        backend/app/api/test_fleet_fresh_marks.py
#  EDIT  backend/app/api/fleet_today_routes.py (3) · frontend ModernDashboard (2)
#        IC / TSG / TMA / TMA2 / VET / ORB / EMC / BRK panels (labels)
#  Gates: py_compile, esbuild, imports, fresh-marks suite, the fleet suite
#  (no failure beyond its known pre-existing set), static wiring checks.
#
# Usage: python3 apply_fleet_today_strip.py [--repo PATH] [--allow-dirty] [--skip-tests] [--skip-esbuild]
# Run ONLY this script.

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "FLEET_TODAY_STRIP_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
FR = "backend/app/api/fleet_today_routes.py"
MD = "frontend/src/pages/ModernDashboard.jsx"
P_IC = "frontend/src/strategies/ic/ICPanel.jsx"
P_TSG = "frontend/src/strategies/tsg_v1/TSGV1Panel.jsx"
P_TMA = "frontend/src/strategies/tma/TMAPanel.jsx"
P_TMA2 = "frontend/src/strategies/tma2/TMA2Panel.jsx"
P_VET = "frontend/src/strategies/vet/VETPanel.jsx"
P_ORB = "frontend/src/strategies/orb/ORBPanel.jsx"
P_EMC = "frontend/src/strategies/emc/EMCPanel.jsx"
P_BRK = "frontend/src/strategies/brk/BRKPanel.jsx"
STRIP = "frontend/src/components/fleet/TodayStrip.jsx"
T = "backend/app/api/test_fleet_fresh_marks.py"
PREREQ = {FR: ["FLEET_MARKS_REST_20260928", "def _rest_fill(", "def _mark("], MD: ["<StrategyHost ltpMap={ltpMap}"]}
SUITES = ["app/api/test_fleet_fresh_marks.py"]
# the fleet suite has pre-existing licence-count failures on some machines;
# it gates on "no failure OUTSIDE this set"
BASELINE_SUITE = "app/api/test_fleet_today_routes.py"
KNOWN_FAILS = {"fleet 14, active 0", "active counts strategies, not rows (TSG once)",
               "a strategy the licence excludes is absent from rows AND idle"}

EDITS = [
    (FR, 'def _ltp(symbol: str) -> Optional[float]:\n',
     '# ── FLEET_TODAY_STRIP_20261015 ── a print older than this is not "live"\n# during market hours: LTPStore keeps whatever its LAST publisher wrote, and\n# that may be another strategy hours ago (Velvet\'s wing at Icarus\' 09:16 exit).\nMARK_FRESH_S = 90\n\n\ndef _print_ts(symbol: str) -> Optional[float]:\n    """Age source for _ltp(): the LTPStore print time, or None when unknown\n    (an unknown age is treated as live — exactly the old behaviour)."""\n    try:\n        from app.marketdata.ltp_store import LTPStore\n    except Exception:\n        return None\n    for key in (symbol, str(symbol or "").replace(" ", "").upper()):\n        if not key:\n            continue\n        try:\n            res = LTPStore.get_with_timestamp(key)\n        except Exception:\n            res = None\n        if res and res[0] is not None and res[1]:\n            return float(res[1])\n    return None\n\n\ndef _ltp(symbol: str) -> Optional[float]:\n', 1),
    (FR, '    missing = sorted({l["symbol"] for l in legs if l["open"] and l["symbol"]\n                      and _ltp(l["symbol"]) is None\n                      and l["symbol"] not in _seen_marks and l["symbol"] not in _db_marks})\n',
     '    mkt = _market_open()   # ── FLEET_TODAY_STRIP_20261015 ── + stale prints in market hours\n    missing = sorted({l["symbol"] for l in legs if l["open"] and l["symbol"]\n                      and _needs_rest(l["symbol"], now, mkt)})\n', 1),
    (FR, 'def _mark(symbol: str, now: int):\n    """(ltp, mark_ts, stale) — live mark first, else the last seen one."""\n    ltp = _ltp(symbol)\n    if ltp is not None:\n        _seen_marks[symbol] = (ltp, now)\n        _marks_dirty[symbol] = (ltp, now)\n        return ltp, now, False\n    m = _seen_marks.get(symbol) or _db_marks.get(symbol)\n    if m:\n        return float(m[0]), int(m[1]), True\n    return None, None, False\n',
     'def _needs_rest(sym: str, now: int, mkt: bool) -> bool:\n    """── FLEET_TODAY_STRIP_20261015 ── never marked anywhere (the original\n    rule), or — in market hours only — every known mark is older than\n    MARK_FRESH_S (a print of unknown age counts as live)."""\n    px = _ltp(sym)\n    if px is None and sym not in _seen_marks and sym not in _db_marks:\n        return True\n    if not mkt:\n        return False\n    pts = _print_ts(sym) if px is not None else None\n    if px is not None and (pts is None or now - pts <= MARK_FRESH_S):\n        return False\n    ts = [pts] if pts else []\n    for src in (_seen_marks, _db_marks):\n        if sym in src:\n            ts.append(int(src[sym][1]))\n    return not ts or now - max(ts) > MARK_FRESH_S\n\n\ndef _mark(symbol: str, now: int):\n    """(ltp, mark_ts, stale) — live mark first, else the last seen one.\n    ── FLEET_TODAY_STRIP_20261015 ── in market hours a print is live only when\n    it is younger than MARK_FRESH_S; an older one competes with the last seen\n    mark and a REST quote, newest wins, flagged stale when old. After the\n    close: exactly the original behaviour."""\n    ltp = _ltp(symbol)\n    mkt = _market_open()\n    pts = _print_ts(symbol) if (ltp is not None and mkt) else None\n    if ltp is not None and (not mkt or pts is None or now - pts <= MARK_FRESH_S):\n        _seen_marks[symbol] = (ltp, now)\n        _marks_dirty[symbol] = (ltp, now)\n        return ltp, now, False\n    if not mkt:\n        m = _seen_marks.get(symbol) or _db_marks.get(symbol)\n        if m:\n            return float(m[0]), int(m[1]), True\n        return None, None, False\n    cands = [(ltp, int(pts))] if ltp is not None else []\n    for src in (_seen_marks, _db_marks):\n        m = src.get(symbol)\n        if m:\n            cands.append((float(m[0]), int(m[1])))\n    if not cands:\n        return None, None, False\n    px, ts = max(cands, key=lambda c: c[1])\n    return px, ts, now - ts > MARK_FRESH_S\n', 1),
    (MD, 'import FleetBlotter from "../components/fleet/FleetBlotter";\n',
     'import FleetBlotter from "../components/fleet/FleetBlotter";\nimport TodayStrip from "../components/fleet/TodayStrip";   // ── FLEET_TODAY_STRIP_20261015 ──\n', 1),
    (MD, '        <StrategyHost ltpMap={ltpMap} focus={focusId} onFocus={pick} rail={false} recent="collapsed" />\n',
     '        {/* ── FLEET_TODAY_STRIP_20261015 ── the row\'s own numbers + what the panel below measures */}\n        <TodayStrip rows={rows.filter((r) => r.id === focusId)} name={stratName(focusId, isAdminUi, META[focusId]?.name)} compact={isMobile} />\n        <StrategyHost ltpMap={ltpMap} focus={focusId} onFocus={pick} rail={false} recent="collapsed" />\n', 1),
    (P_IC, 'letterSpacing: "0.6px", marginRight: 8 }}>MTM</span>',
     'letterSpacing: "0.6px", marginRight: 8 }}>Position MTM{/* ── FLEET_TODAY_STRIP_20261015 ── */}</span>', 1),
    (P_TSG, '<span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>MTM</span>',
     '<span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>Position MTM{/* ── FLEET_TODAY_STRIP_20261015 ── */}</span>', 1),
    (P_TMA, '<div><div style={label}>Net today</div>',
     '<div><div style={label}>Closed today · net{/* ── FLEET_TODAY_STRIP_20261015 ── */}</div>', 1),
    (P_TMA2, '<div><div style={label}>Net today</div>',
     '<div><div style={label}>Closed today · net{/* ── FLEET_TODAY_STRIP_20261015 ── */}</div>', 1),
    (P_VET, '<Stat big k="Open MTM" ',
     '<Stat big k="Position MTM" ', 1),
    (P_VET, '<Stat k="Day total" v={fmtInr(dayTotal)}',
     '<Stat k="Day total · net" v={fmtInr(dayTotal)}', 1),
    (P_VET, '  const sltpHeader = ',
     '  // ── FLEET_TODAY_STRIP_20261015 ── "Position MTM" / "Day total · net" labels\n  const sltpHeader = ', 1),
    (P_ORB, '<Stat k="Day P&L" v={fmtInr(dayTotal)}',
     '<Stat k="Day P&L · gross" v={fmtInr(dayTotal)}', 1),
    (P_ORB, '  const dayTotal = closedPnl + (openMtm ?? 0);\n',
     '  const dayTotal = closedPnl + (openMtm ?? 0);   // ── FLEET_TODAY_STRIP_20261015 ── gross (label says so)\n', 1),
    (P_EMC, '<Stat k="Day P&L" v={fmtInr(dayTotal)}',
     '<Stat k="Day P&L · net" v={fmtInr(dayTotal)}', 1),
    (P_EMC, '  const dayTotal = closedPnl + (openMtm ?? 0);\n',
     '  const dayTotal = closedPnl + (openMtm ?? 0);   // ── FLEET_TODAY_STRIP_20261015 ── net (label says so)\n', 1),
    (P_BRK, '<span style={label}>Open MTM <b',
     '<span style={label}>Position MTM{/* ── FLEET_TODAY_STRIP_20261015 ── */} <b', 1),
]

NEW_FILES = {STRIP: '// frontend/src/components/fleet/TodayStrip.jsx\n//\n// ── FLEET_TODAY_STRIP_20261015 ── one number per strategy, explained.\n//\n// The "Traded today" row and a strategy\'s own panel measure different things:\n//   row   = EVERYTHING the strategy did today — legs open now + every leg\n//           closed today, including a carried position closed this morning;\n//           gross, charges in their own column;\n//   panel = the CURRENT position, from its own entry.\n// Shown side by side with no explanation they read as two answers to one\n// question (IC V2, 15 Oct: +1,04,293 vs +95,290 — the gap was yesterday\'s\n// position closed at 09:16). This strip sits directly above the focused\n// panel, repeats the row\'s numbers from the SAME /api/fleet/today payload\n// (so it can never disagree with the table) and says in words what the panel\n// below measures.\n\nimport { fmtInr } from "./fleetFormat";\nimport { colors, spacing, typography, pnlStyle } from "../../tokens";\n\nconst clock = (ts) => {\n  if (!ts) return null;\n  const d = new Date(Number(ts) * 1000);\n  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;\n};\n\nexport function todayExplain(r) {\n  if (!r) return null;\n  const closed = Number(r.closed_trades || 0);\n  const open = Number(r.open_legs || 0);\n  const parts = [];\n  if (closed > 0) parts.push(`${closed} position${closed === 1 ? "" : "s"} closed today`);\n  if (open > 0) parts.push(`${open} open leg${open === 1 ? "" : "s"}`);\n  const scope = parts.length ? parts.join(" + ") : "nothing traded";\n  let panel;\n  if (open > 0 && closed > 0) {\n    panel = "The panel below follows only the OPEN position, from its own entry — so its MTM leaves out what was closed earlier today.";\n  } else if (open > 0) {\n    panel = r.carried\n      ? "The open position was carried from a previous session: its P&L here and below is measured from its entry, not from today\'s open."\n      : "The panel below follows this same open position; its figures differ only by charges (net) or a few seconds of price.";\n  } else {\n    panel = "Flat now — the panel below shows the closed trades.";\n  }\n  return { scope, panel };\n}\n\nexport default function TodayStrip({ rows = [], name, compact = false }) {\n  if (!rows.length) return null;\n  return (\n    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: spacing.sm }}>\n      {rows.map((r) => {\n        const ex = todayExplain(r);\n        const asof = r.marks_stale > 0 ? clock(r.mark_asof) : null;\n        return (\n          <div key={r.key} style={{ background: colors.bg.secondary, border: `1px solid ${colors.border.light}`,\n                                    borderRadius: 8, padding: compact ? "8px 10px" : "8px 14px" }}>\n            <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: compact ? 10 : 16 }}>\n              <span style={{ fontSize: 10.5, letterSpacing: "0.6px", textTransform: "uppercase", color: colors.text.muted }}>\n                Today · {name} · {r.book === "LIVE" ? "Live" : "Paper"}\n              </span>\n              <b style={{ fontSize: 18, ...typography.mono, ...pnlStyle(r.gross) }}>{fmtInr(r.gross)}</b>\n              <span style={{ fontSize: 12, color: colors.text.secondary, ...typography.mono }}>\n                = realised <b style={pnlStyle(r.realised)}>{fmtInr(r.realised)}</b>\n                {" "}+ unrealised <b style={pnlStyle(r.unrealised)}>{fmtInr(r.unrealised)}</b>\n                {" "}· charges ₹{Math.round(Number(r.charges) || 0).toLocaleString("en-IN")}\n                {" "}· net <b style={pnlStyle(r.net)}>{fmtInr(r.net)}</b>\n              </span>\n              <span style={{ fontSize: 11, color: colors.text.muted, marginLeft: compact ? 0 : "auto" }}>\n                same numbers as its row in Traded today{asof ? ` · marks as of ${asof}` : ""}\n              </span>\n            </div>\n            {ex && (\n              <div style={{ fontSize: 11.5, color: colors.text.tertiary, marginTop: 4 }}>\n                Counts {ex.scope}. {ex.panel}\n              </div>\n            )}\n          </div>\n        );\n      })}\n    </div>\n  );\n}\n',
             T: '# backend/app/api/test_fleet_fresh_marks.py — fence FLEET_TODAY_STRIP_20261015\n#\n# The Traded-today table priced open legs from LTPStore with NO age check.\n# A print can be hours old when the symbol\'s last publisher was ANOTHER\n# strategy: 15 Oct, Velvet\'s wing 21400PE was valued at 2.50 all day — Icarus\'\n# 09:16 exit print — while the live price was 4.55 (row ≈ ₹1,330 below the\n# panel). During market hours a print older than MARK_FRESH_S is no longer\n# "live": the newest of (that print, the feed\'s last seen mark, a batched REST\n# quote) is used, and the row says "marks as of".\n#\n# Run: cd backend && python3 app/api/test_fleet_fresh_marks.py\n\nfrom __future__ import annotations\n\nimport os\nimport sys\nimport tempfile\nimport time\nfrom pathlib import Path\n\nBACKEND = Path(__file__).resolve().parents[2]\nif str(BACKEND) not in sys.path:\n    sys.path.insert(0, str(BACKEND))\nos.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())\n\nimport app.api.fleet_today_routes as F  # noqa: E402\nfrom app.marketdata.ltp_store import LTPStore  # noqa: E402\n\nFAILS: list = []\nOK = [0]\nIST = 19800\nDAY0 = ((int(time.time()) + IST) // 86400) * 86400 - IST\nT11 = DAY0 + 11 * 3600                  # 11:00 IST today (outside the 09:15–09:20 REST quiet window)\nWING, MAIN, NEVER = "NIFTY26O1921400PE", "NIFTY26O1922800PE", "NIFTY26O1924000CE"\nREST = {"calls": [], "answer": {}}\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\ndef reset(market=True):\n    F._seen_marks.clear(); F._db_marks.clear(); F._marks_dirty.clear()\n    F._rest_last[0] = 0.0\n    F._market_open = lambda: market\n    F._load_db_marks = lambda: None\n    F._persist_marks = lambda now, force=False: 0\n    REST["calls"].clear(); REST["answer"] = {}\n    F._rest_quote = lambda syms: (REST["calls"].append(sorted(syms)), dict(REST["answer"]))[1]\n    with LTPStore._lock:\n        for s in (WING, MAIN, NEVER):\n            LTPStore._prices.pop(s, None); LTPStore._timestamps.pop(s, None)\n\n\ndef put(sym, px, ts):\n    with LTPStore._lock:\n        LTPStore._prices[sym] = px\n        LTPStore._timestamps[sym] = ts\n\n\ndef leg(sym, entry, short, qty=650):\n    return {"open": True, "symbol": sym, "entry": entry, "short": short, "qty": qty, "gross": 0.0,\n            "charges": 0.0, "approx": False, "entry_ts": DAY0 + 9 * 3600 + 1440, "exit_ts": None,\n            "reason": None, "book": "PAPER"}\n\n\nif __name__ == "__main__":\n    print("── fresh print during market hours ──")\n    reset()\n    put(MAIN, 80.35, T11 - 5)\n    px, ts, stale = F._mark(MAIN, T11)\n    check("fresh print → live mark, not stale", (px, stale) == (80.35, False))\n    F._rest_fill([leg(MAIN, 182.40, True)], T11)\n    check("a freshly published symbol is never REST-quoted", REST["calls"] == [])\n\n    print("── the Velvet wing: another strategy\'s hours-old print ──")\n    reset()\n    put(MAIN, 80.35, T11 - 5)\n    put(WING, 2.50, DAY0 + 9 * 3600 + 960)          # Icarus\' 09:16 exit print\n    REST["answer"] = {WING: (4.55, T11 - 3)}\n    legs = [leg(MAIN, 182.40, True), leg(WING, 2.85, False)]\n    n = F._rest_fill(legs, T11)\n    check("stale print → batched REST quote for that symbol only", REST["calls"] == [[WING]] and n == 1, REST["calls"])\n    px, ts, stale = F._mark(WING, T11)\n    check("wing valued at the REST price 4.55, not Icarus\' 2.50", px == 4.55 and stale is False, (px, ts, stale))\n    row = F._row("VET_V1", "PAPER", "PAPER", legs, T11, DAY0)\n    want = (182.40 - 80.35) * 650 + (4.55 - 2.85) * 650\n    check("row unrealised = panel\'s spread (main + wing at live prices)", abs(row["unrealised"] - want) < 0.01,\n          (row["unrealised"], want))\n    check("…and the row is not flagged stale", row["marks_stale"] == 0)\n\n    print("── REST down: honest fallback ──")\n    reset()\n    put(WING, 2.50, DAY0 + 9 * 3600 + 960)\n    F._rest_quote = lambda syms: {}\n    F._rest_fill([leg(WING, 2.85, False)], T11)\n    px, ts, stale = F._mark(WING, T11)\n    check("no fresher source → old print used but FLAGGED stale with its time",\n          px == 2.50 and stale is True and ts == DAY0 + 9 * 3600 + 960, (px, ts, stale))\n\n    print("── throttle / quiet window / old rules ──")\n    reset()\n    put(WING, 2.50, DAY0 + 9 * 3600 + 960)\n    F._rest_fill([leg(WING, 2.85, False)], T11)\n    F._rest_fill([leg(WING, 2.85, False)], T11 + 30)\n    check("REST at most once per REST_EVERY_S", len(REST["calls"]) == 1, REST["calls"])\n    reset()\n    put(WING, 2.50, DAY0 + 9 * 3600 + 960)\n    F._rest_fill([leg(WING, 2.85, False)], DAY0 + 9 * 3600 + 17 * 60)\n    check("no REST inside the 09:15–09:20 quiet window", REST["calls"] == [])\n    reset()\n    F._rest_fill([leg(NEVER, 5.0, False)], T11)\n    check("never-seen symbol still priced (FLEET_MARKS_REST rule kept)", REST["calls"] == [[NEVER]])\n\n    print("── after the close: unchanged behaviour ──")\n    reset(market=False)\n    put(WING, 2.50, DAY0 + 9 * 3600 + 960)\n    px, ts, stale = F._mark(WING, DAY0 + 16 * 3600)\n    check("market closed: last print used as before (no stale flag, no REST)", (px, stale) == (2.50, False))\n    F._rest_fill([leg(WING, 2.85, False)], DAY0 + 16 * 3600)\n    check("…and no REST after the close for a symbol that has a print", REST["calls"] == [])\n\n    reset()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} FLEET FRESH-MARK CHECKS PASSED")\n'}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-esbuild", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        staged[rel] = txt
    for rel, fences in PREREQ.items():
        for f in fences:
            if f not in staged[rel]:
                die(f"prerequisite {f!r} not found in {rel}")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — mixed state; remove it (or git checkout) and re-run")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    for rel in targets:
        if FENCE not in staged[rel]:
            die(f"internal: {rel} edited without the fence marker")
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in [(r, staged[r]) for r in targets if r.endswith(".py")] + [(k, v) for k, v in NEW_FILES.items() if k.endswith(".py")]:
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print(f"  ✓ py_compile (edited + {len(NEW_FILES)} new)")
        jsx = [(r, staged[r]) for r in targets if r.endswith((".js", ".jsx"))] + [(k, v) for k, v in NEW_FILES.items() if k.endswith((".js", ".jsx"))]
        if jsx and not a.skip_esbuild:
            esb = None
            for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild"):
                if c.exists():
                    esb = str(c); break
            esb = esb or shutil.which("esbuild")
            if not esb:
                die("esbuild not found (frontend/node_modules/.bin) — run npm install, or --skip-esbuild")
            for rel, _txt in jsx:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(_txt, encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.js=jsx", "--loader:.jsx=jsx", "--log-level=error"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print(f"  ✓ esbuild parsed {len(jsx)} staged JSX file(s)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    for rel, txt in NEW_FILES.items():
        (repo / rel).write_text(txt, encoding="utf-8")
        print(f"  ✓ created {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", SCALP_TEST_MODE="1",
                   SCALP_APP_HOME=tempfile.mkdtemp(prefix="scalp_test_home_"),
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c",
                            "import app.api.fleet_today_routes; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("a touched module no longer imports")
        print("  ✓ fleet_today_routes imports")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                print(f"  ! {suite} not found — skipped")
                continue
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL  " in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    if not a.skip_tests and (repo / "backend" / BASELINE_SUITE).exists():
        print(f"  … {Path(BASELINE_SUITE).name} (vs its known pre-existing failures)")
        r = subprocess.run([sys.executable, BASELINE_SUITE], cwd=repo / "backend", capture_output=True,
                           text=True, env=env, timeout=900)
        fails = {l.split("FAIL", 1)[1].strip() for l in r.stdout.splitlines() if l.startswith("  FAIL  ")}
        new = sorted(f for f in fails if f not in KNOWN_FAILS)
        if new or (r.returncode != 0 and not fails):
            print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"{BASELINE_SUITE}: NEW failures {new}")
        print(f"  ✓ {Path(BASELINE_SUITE).name}: no new failures ({len(fails)} known pre-existing)")
    for rel in targets + list(NEW_FILES):
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied. Rebuild (deploy-scalp.command). On the Dashboard, the focused\n"
          f"  strategy now has a 'Today · …' strip above its panel with the table row's exact numbers\n"
          f"  and one line on what the panel measures; panel headlines are labelled Position / net / gross.")

if __name__ == "__main__":
    main()
