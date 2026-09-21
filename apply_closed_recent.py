#!/usr/bin/env python3
# apply_closed_recent.py — "Closed positions · today and recent" for IC_V1,
# IC_V2, TMA_V1 and TMA_V2 (the section VETPanel v2 introduced).
#
# Fence: CLOSED_RECENT_20260921
# Independent of VET_PANEL_V2_20260921 (touches none of its files).
# Builds on main as committed: ICPanel "IC_SPLIT 2026-08-04" with ACC2_W3
# BrokerChip import; TMAPanel/TMA2Panel with the "Open + closed-today legs"
# table; ic_state_routes.py with /state + /square_off and IC_MTM.
#
# WHAT THIS DOES
#   NEW  frontend/src/components/ClosedRecent.jsx — ONE shared card (+
#        useClosedGroups poller, groupSpreadLegs, isTodayIST). One row per
#        POSITION, never per leg; today's exits full strength, recent tail
#        dimmed; "today" = EXIT timestamp, IST day; reasons masked for
#        non-admin.
#   EDIT TMAPanel.jsx + TMA2Panel.jsx (same 4 anchored edits each): the
#        per-leg "Open + closed-today legs" table is REPLACED by ClosedRecent
#        (one row per spread: Sold @ / Covered @, hedge folded into net).
#        No backend change — built from the /trades rows the panel already
#        polls. "Closed today" in the header now counts spreads, not legs;
#        "Net today" is unchanged (exact per-leg sum). Open legs stay on the
#        spread card; NEW red banner when the book has OPEN legs but the
#        manager holds no group (the old table was the only place those
#        showed).
#   EDIT ICPanel.jsx (4 anchored edits): ClosedRecent card under the condor
#        card, fed by the new route. One row per CONDOR: net Credit in, net
#        Debit out, wings/adjustments folded into net.
#   EDIT backend/app/api/ic_state_routes.py (append-only, both trees):
#        GET /api/ic/{sid}/closed — PAPER rows from paper_trades (booked
#        net/charges), LIVE rows from trades (gross derived, charges
#        modelled, flagged approx when a leg has no exit price). A condor
#        is listed only when EVERY leg is closed. /state, /square_off
#        byte-identical. Engines/managers NOT touched.
#   NEW  backend/app/api/test_ic_closed_route.py (both trees) — real-clock
#        behavioural suite; no TestClient.
#
# Runs the suite after writing and ROLLS BACK every file if it fails.
# Files only — nothing restarts. TSG is live: rebuild + restart after 15:30.
#
# USAGE: python3 apply_closed_recent.py --check && python3 apply_closed_recent.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone

FENCE = "CLOSED_RECENT_20260921"
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

IC_ROUTE = "backend/app/api/ic_state_routes.py"
IC_TEST = "backend/app/api/test_ic_closed_route.py"
COMP = "frontend/src/components/ClosedRecent.jsx"
IC_PANEL = "frontend/src/strategies/ic/ICPanel.jsx"
TMA_PANEL = "frontend/src/strategies/tma/TMAPanel.jsx"
TMA2_PANEL = "frontend/src/strategies/tma2/TMA2Panel.jsx"

IC_BLOCK = '\n\n# ════════════════════════════════════════════════════════════════════════════\n# ── CLOSED_RECENT_20260921 ── GET /api/ic/{sid}/closed — closed condors for\n# the panel\'s "Closed positions · today and recent" section (VET panel v2\n# parity). Additive: /state and /square_off above are untouched. Read-only,\n# never throws, reads ONLY that sid\'s rows.\n#\n# One entry per CONDOR (group_id), not per leg: a leg is not a trade. A group\n# is listed only when EVERY booked leg is closed — a condor with one short\n# stopped out and three legs still open is an OPEN position and belongs to\n# the leg table above, not here. Group exit time = the LAST leg\'s exit.\n# "today" is by that exit timestamp, IST calendar day (real clock), so a\n# NEXT_OPEN condor entered yesterday and closed 09:16 today counts today.\n#\n# PAPER rows come from paper_trades (net_pnl/total_charges as booked at\n# close). LIVE rows come from trades, which stores no P&L: gross is\n# direction-aware from entry/exit, charges from the backtest charges model\n# (best effort — a failure books charges 0 and flags the group `approx`).\n# A LIVE leg closed without an exit price (BROKER_EXIT) contributes 0 and\n# flags `approx` too — never an invented number presented as exact.\n#\n# Masking: exit reasons are engine vocabulary — "CLOSED" for non-admin\n# licenses, server-side (Phase 2b, fails closed).\n# ════════════════════════════════════════════════════════════════════════════\nimport re as _re\nimport sqlite3 as _sqlite3\n\n_IC_IST_OFF = 5 * 3600 + 30 * 60\n_ic_warned = set()\n_IC_SYM_RE = _re.compile(r"^([A-Z]+)(.{5})(\\d+)(CE|PE)$")\n\n\ndef _ic_day_start(now: int) -> int:\n    """Epoch of 00:00 IST for the IST day containing `now`."""\n    return ((int(now) + _IC_IST_OFF) // 86400) * 86400 - _IC_IST_OFF\n\n\ndef _ic_db_path() -> str:\n    from app.db.sqlite import DB_PATH\n    return str(DB_PATH)\n\n\ndef _ic_is_admin() -> bool:\n    try:\n        from app.license import license_state\n        return license_state.ui_level() == "admin"\n    except Exception:\n        return False                                   # fail closed\n\n\ndef _ic_short(sym: str) -> str:\n    """NIFTY2692223400CE -> 23400CE (weekly YYMDD and monthly YYMMM codes are\n    both 5 chars). Unparseable symbols come back whole."""\n    m = _IC_SYM_RE.match(str(sym or "").replace(" ", "").upper())\n    return f"{m.group(3)}{m.group(4)}" if m else str(sym or "")\n\n\ndef _ic_live_charges(is_short: bool, entry: float, exit_px: float, qty: int):\n    try:\n        from app.backtest.charges.charges_model import (\n            charges_for_long_trade, charges_for_short_trade)\n        fn = charges_for_short_trade if is_short else charges_for_long_trade\n        cr = fn(entry_price=float(entry), exit_price=float(exit_px), qty=int(qty))\n        return float(getattr(cr, "total_charges", 0.0) or 0.0), True\n    except Exception:\n        return 0.0, False\n\n\ndef _ic_rows(conn, sid: str, limit_legs: int, warnings: list):\n    """Normalised leg rows for one sid from both books, newest first. A\n    missing table is normal (book never used); anything else — a renamed\n    column, say — is surfaced in `warnings`, never swallowed."""\n    out = []\n    try:\n        for r in conn.execute(\n                "SELECT group_id, trade_class, symbol, trade_direction, qty, lots,"\n                " entry_price, exit_price, exit_reason, entry_time, exit_time,"\n                " state, pnl_value, total_charges, net_pnl"\n                " FROM paper_trades WHERE strategy_name=? AND group_id IS NOT NULL"\n                " ORDER BY entry_time DESC LIMIT ?", (sid, limit_legs)):\n            d = dict(r)\n            d["book"] = "PAPER"\n            d["open"] = d["state"] == "OPEN"\n            out.append(d)\n    except Exception as e:\n        if "no such table" not in str(e):\n            warnings.append(f"paper_trades: {e!r}")\n    try:\n        for r in conn.execute(\n                "SELECT group_id, trade_class, symbol, trade_direction, qty,"\n                " entry_price, exit_price, exit_reason, entry_time, exit_time, state"\n                " FROM trades WHERE strategy_id=? AND group_id IS NOT NULL"\n                " ORDER BY entry_time DESC LIMIT ?", (sid, limit_legs)):\n            d = dict(r)\n            d["book"] = "LIVE"\n            d["lots"] = None\n            d["open"] = d["exit_time"] is None\n            d["pnl_value"] = d["total_charges"] = d["net_pnl"] = None\n            out.append(d)\n    except Exception as e:\n        if "no such table" not in str(e):\n            warnings.append(f"trades: {e!r}")\n    # A LIMIT can cut the OLDEST group of a book in half; half a condor with\n    # every surviving leg closed would list with wrong numbers. Drop it.\n    for book in ("PAPER", "LIVE"):\n        mine = [r for r in out if r["book"] == book]\n        if len(mine) >= limit_legs:\n            cut = mine[-1]["group_id"]\n            out = [r for r in out if not (r["book"] == book and r["group_id"] == cut)]\n    return out\n\n\ndef _ic_groups(rows, day0: int):\n    by = {}\n    for r in rows:\n        by.setdefault((r["book"], r["group_id"]), []).append(r)\n    out = []\n    for (book, gid), legs in by.items():\n        if any(l["open"] for l in legs) or not all(l.get("exit_time") for l in legs):\n            continue                                    # still an open position\n        approx = False\n        gross = charges = net = 0.0\n        credit = debit = 0.0\n        for l in legs:\n            short = str(l.get("trade_direction") or "").upper() == "SHORT"\n            qty = int(l.get("qty") or 0)\n            ent, ext = l.get("entry_price"), l.get("exit_price")\n            sign = 1.0 if short else -1.0\n            credit += sign * float(ent or 0) * qty\n            if ext is None:\n                approx = True\n            else:\n                debit += sign * float(ext) * qty\n            if l["book"] == "PAPER" and l.get("net_pnl") is not None:\n                g = float(l.get("pnl_value") or 0)\n                c = float(l.get("total_charges") or 0)\n                n = float(l["net_pnl"])\n            elif ext is None:\n                g = c = n = 0.0\n            else:\n                g = (float(ent) - float(ext)) * qty if short else (float(ext) - float(ent)) * qty\n                c, ok = _ic_live_charges(short, ent, ext, qty)\n                approx = approx or not ok\n                n = g - c\n            gross, charges, net = gross + g, charges + c, net + n\n        shorts = sorted((l for l in legs if str(l.get("trade_direction")).upper() == "SHORT"\n                         and not str(l.get("trade_class") or "").endswith("A")),\n                        key=lambda l: str(l.get("trade_class")))\n        longs = sorted((l for l in legs if str(l.get("trade_direction")).upper() != "SHORT"\n                        and not str(l.get("trade_class") or "").endswith("A")),\n                       key=lambda l: str(l.get("trade_class")))\n        adj = [l for l in legs if str(l.get("trade_class") or "").endswith("A")]\n        base_qty = max([int(l.get("qty") or 0) for l in (shorts or legs)] or [0])\n        reasons = {}\n        for l in legs:\n            k = l.get("exit_reason") or "—"\n            reasons[k] = reasons.get(k, 0) + 1\n        reason = " · ".join(k if n == len(legs) else f"{k}×{n}"\n                            for k, n in sorted(reasons.items(), key=lambda kv: -kv[1]))\n        exit_ts = max(int(l["exit_time"]) for l in legs)\n        lots = next((l.get("lots") for l in shorts if l.get("lots")), None)\n        sub = []\n        if longs:\n            sub.append("wings " + " / ".join(_ic_short(l["symbol"]) for l in longs))\n        if adj:\n            sub.append(f"+{len(adj)} adj")\n        out.append({\n            "group_id": f"{book}:{gid}", "book": book, "kind": "IC",\n            "label": " / ".join(_ic_short(l["symbol"]) for l in (shorts or legs)),\n            "sub": " · ".join(sub) or None,\n            "legs": len(legs), "lots": lots, "qty": base_qty,\n            "entry": round(credit / base_qty, 2) if base_qty else None,\n            "exit": (round(debit / base_qty, 2) if base_qty and not approx else None),\n            "entry_ts": min(int(l["entry_time"]) for l in legs),\n            "exit_ts": exit_ts, "reason": reason,\n            "gross": round(gross, 2), "charges": round(charges, 2),\n            "net": round(net, 2), "approx": approx, "today": exit_ts >= day0,\n        })\n    out.sort(key=lambda g: g["exit_ts"], reverse=True)\n    return out\n\n\n@router.get("/api/ic/{sid}/closed")\ndef get_ic_closed(sid: str, limit: int = 12):\n    sid = _require_sid(sid)\n    now = int(_time.time())\n    day0 = _ic_day_start(now)\n    out = {"ok": True, "strategy": sid, "groups": [], "today_net": 0.0,\n           "closed_today": 0, "server_ts": now}\n    try:\n        limit = max(1, min(int(limit), 50))\n        warns = []\n        with _sqlite3.connect(_ic_db_path(), timeout=30) as conn:\n            conn.row_factory = _sqlite3.Row\n            rows = _ic_rows(conn, sid, 400, warns)\n        groups = _ic_groups(rows, day0)\n        if not _ic_is_admin():\n            for g in groups:\n                g["reason"] = "CLOSED"\n        out["today_net"] = round(sum(g["net"] for g in groups if g["today"]), 2)\n        out["closed_today"] = sum(1 for g in groups if g["today"])\n        out["groups"] = groups[:limit]\n        if warns:\n            out["warnings"] = warns\n            if sid not in _ic_warned:                  # once per process, not per poll\n                _ic_warned.add(sid)\n                write_audit_log(f"[API][IC_CLOSED][{sid}][WARN] {warns}")\n    except Exception as e:\n        out["ok"] = False\n        out["error"] = repr(e)\n        write_audit_log(f"[API][IC_CLOSED][{sid}][ERR] {e}")\n    return out\n# ── CLOSED_RECENT_20260921 END ──\n'

COMPONENT = '// frontend/src/components/ClosedRecent.jsx\n//\n// ── CLOSED_RECENT_20260921 ── "Closed positions · today and recent"\n// The section VETPanel v2 introduced, as ONE shared component so IC_V1/IC_V2\n// and TMA_V1/TMA_V2 render the identical table instead of four diverging\n// copies (the paramFormat.js lesson).\n//\n// One row per POSITION, never per leg — a hedge/wing is not a trade; its P&L\n// is folded into the position\'s net. Today\'s exits render at full strength,\n// the recent tail dimmed. "Today" is by EXIT timestamp, IST calendar day:\n// these are positional strategies, so an entry-day filter would hide exactly\n// the rows that matter (the lifetime-totals-mislabelled-as-today bug family\n// starts with entry-day filters).\n//\n// Normalised group shape (all optional except group_id / exit_ts / net):\n//   { group_id, kind: "S"|"L"|"IC", label, sub, lots, qty, entry, exit,\n//     entry_ts, exit_ts, reason, charges, net, today, approx }\n//\n// Exports:\n//   default ClosedRecent      presentational card\n//   useClosedGroups(url)      poller for routes that return {groups, today_net}\n//   groupSpreadLegs(trades)   tma_trades / tma2_trades legs -> groups\n//   isTodayIST(ts)            the one "today" rule, shared\n//\n// UI_MASK: `showParams=false` replaces every exit reason with "CLOSED".\n\nimport { useEffect, useState } from "react";\nimport { colors, spacing, typography, pnlStyle, alpha } from "../tokens";\n\nconst IST = { timeZone: "Asia/Kolkata" };\nconst dayKey = (d) => d.toLocaleDateString("en-CA", IST);              // YYYY-MM-DD in IST\n\nexport function isTodayIST(ts) {\n  return !!ts && dayKey(new Date(ts * 1000)) === dayKey(new Date());\n}\n\nfunction hhmm(ts) {\n  return new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, ...IST });\n}\n// Positional: a bare "10:21" is ambiguous across days — other-day stamps carry the date.\nfunction fmtTs(ts) {\n  if (!ts) return "—";\n  if (isTodayIST(ts)) return hhmm(ts);\n  return `${new Date(ts * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short", ...IST })} · ${hhmm(ts)}`;\n}\nfunction fmtInr(v) {\n  if (v == null || isNaN(v)) return "—";\n  const a = Math.abs(Math.round(v));\n  return `${v < 0 ? "−" : v > 0 ? "+" : ""}₹${a.toLocaleString("en-IN")}`;\n}\nconst fmt2 = (v) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(2));\n\n/* tma_trades / tma2_trades rows (one per LEG: direction SELL = the short,\n * BUY = the hedge, linked by group_id) -> one group per spread. A spread is\n * closed only when every leg of it is; its exit time is the LAST leg\'s. */\nexport function groupSpreadLegs(trades) {\n  const by = new Map();\n  for (const t of trades || []) {\n    if (!t || t.group_id == null) continue;\n    if (!by.has(t.group_id)) by.set(t.group_id, []);\n    by.get(t.group_id).push(t);\n  }\n  const out = [];\n  for (const [gid, legs] of by) {\n    if (legs.some((l) => l.status !== "CLOSED" || !l.exit_ts)) continue;\n    const main = legs.find((l) => l.direction === "SELL") || legs[0];\n    const hedge = legs.find((l) => l !== main);\n    const lotSize = main.lot_size || null;\n    const exitTs = Math.max(...legs.map((l) => l.exit_ts));\n    out.push({\n      group_id: gid,\n      kind: main.direction === "SELL" ? "S" : "L",\n      label: main.tradingsymbol,\n      sub: hedge ? `+ hedge ${hedge.tradingsymbol}` : null,\n      lots: main.lots ?? (lotSize ? Math.round(main.qty / lotSize) : null),\n      qty: main.qty,\n      entry: main.entry_price, exit: main.exit_price,\n      entry_ts: main.entry_ts, exit_ts: exitTs,\n      reason: main.exit_reason,\n      charges: legs.reduce((a, l) => a + (l.charges || 0), 0),\n      net: legs.reduce((a, l) => a + (l.net_pnl ?? l.pnl ?? 0), 0),\n      today: isTodayIST(exitTs),\n    });\n  }\n  return out.sort((a, b) => b.exit_ts - a.exit_ts);\n}\n\n/* Poller for a route returning { groups, today_net }. Keeps the last good\n * payload when the backend blips; never throws into the panel. */\nexport function useClosedGroups(url, pollMs = 15000) {\n  const [data, setData] = useState({ groups: [], today_net: 0, loaded: false });\n  useEffect(() => {\n    if (!url) return undefined;\n    let alive = true;\n    const pull = async () => {\n      try {\n        const r = await fetch(url);\n        if (!r.ok) return;\n        const d = await r.json();\n        if (alive && d && Array.isArray(d.groups)) setData({ groups: d.groups, today_net: d.today_net ?? 0, loaded: true });\n      } catch { /* keep last */ }\n    };\n    pull();\n    const t = setInterval(pull, pollMs);\n    return () => { alive = false; clearInterval(t); };\n  }, [url, pollMs]);\n  return data;\n}\n\nconst KIND = {\n  S:  ["S",  "loss",    "SHORT — option sold"],\n  L:  ["L",  "profit",  "LONG — option bought"],\n  IC: ["IC", "primary", "Iron condor — all legs, one row"],\n};\n\nexport default function ClosedRecent({\n  groups = [],\n  todayNet,                       // omit -> summed from groups flagged today\n  showParams = true,              // UI_MASK: false -> reasons read "CLOSED"\n  limit = 12,\n  entryLabel = "Entry",\n  exitLabel = "Exit px",\n  sizeLabel = "Lots",\n  emptyText = "No closed positions yet.",\n  style,\n}) {\n  const rows = groups.slice(0, limit);\n  const net = todayNet ?? groups.filter((g) => g.today).reduce((a, g) => a + (g.net || 0), 0);\n  const num = { ...typography.mono };\n  const heads = [["Exit", "left"], ["", "left"], ["Position", "left"], [sizeLabel, "right"], [entryLabel, "right"],\n                 [exitLabel, "right"], ["Reason", "left"], ["Charges", "right"], ["Net P&L", "right"]];\n  const td = (align = "left") => ({ padding: "5px 6px", textAlign: align, verticalAlign: "top" });\n\n  return (\n    <div style={{ background: colors.bg.secondary, borderRadius: 10, padding: spacing.lg,\n                  border: `1px solid ${colors.border.light}`, ...style }}>\n      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>\n        <div style={{ ...typography.label, fontSize: 10.5, color: colors.text.muted }}>Closed positions · today and recent</div>\n        <div style={{ fontSize: 12, ...num }}>\n          <span style={{ color: colors.text.muted }}>today </span><b style={pnlStyle(net)}>{fmtInr(net)}</b>\n        </div>\n      </div>\n      {rows.length === 0 && <div style={{ fontSize: 12, color: colors.text.muted }}>{emptyText}</div>}\n      {rows.length > 0 && (\n        <div style={{ overflowX: "auto" }}>\n          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, ...num }}>\n            <thead>\n              <tr style={{ color: colors.text.muted }}>\n                {heads.map(([h, align], i) => (\n                  <th key={i} style={{ fontWeight: 600, fontSize: 10.5, letterSpacing: 0.4, textTransform: "uppercase",\n                                       padding: "4px 6px", textAlign: align, whiteSpace: "nowrap",\n                                       borderBottom: `1px solid ${colors.border.light}` }}>{h}</th>\n                ))}\n              </tr>\n            </thead>\n            <tbody>\n              {rows.map((g) => {\n                const [kText, kTone, kTitle] = KIND[g.kind] || KIND.S;\n                const tone = colors[kTone];\n                return (\n                  <tr key={g.group_id} style={{ borderBottom: `1px solid ${colors.border.dark}`, opacity: g.today ? 1 : 0.62 }}>\n                    <td style={{ ...td(), whiteSpace: "nowrap" }}>{fmtTs(g.exit_ts)}</td>\n                    <td style={td()}>\n                      <span title={kTitle} style={{ background: alpha(tone, 16), color: tone, border: `1px solid ${alpha(tone, 35)}`,\n                                                    borderRadius: 6, padding: "1px 7px", fontSize: 10.5, fontWeight: 700 }}>{kText}</span>\n                    </td>\n                    <td style={td()}>\n                      <b>{g.label || "—"}</b>\n                      {g.book === "LIVE" && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, color: colors.loss }}>LIVE</span>}\n                      {g.sub && <div style={{ fontSize: 10.5, color: colors.text.muted, fontWeight: 400 }}>{g.sub}</div>}\n                    </td>\n                    <td style={td("right")}>{g.lots ?? g.qty ?? "—"}</td>\n                    <td style={td("right")}>{fmt2(g.entry)}</td>\n                    <td style={td("right")}>{fmt2(g.exit)}</td>\n                    <td style={{ ...td(), color: colors.text.secondary }}>{showParams ? (g.reason || "—") : "CLOSED"}</td>\n                    <td style={{ ...td("right"), color: colors.text.muted }}>{g.charges ? `₹${Math.round(g.charges).toLocaleString("en-IN")}` : "—"}</td>\n                    <td style={{ ...td("right"), fontWeight: 700, ...pnlStyle(g.net) }}\n                        title={g.approx ? "approximate — a leg closed without an exit price, or charges could not be modelled" : undefined}>\n                      {g.approx ? "≈ " : ""}{fmtInr(g.net)}\n                    </td>\n                  </tr>\n                );\n              })}\n            </tbody>\n          </table>\n        </div>\n      )}\n    </div>\n  );\n}\n'

TEST_BODY = '# backend/app/api/test_ic_closed_route.py\n#\n# ── CLOSED_RECENT_20260921 ── behavioural suite for GET /api/ic/{sid}/closed.\n# Calls the route function directly (no TestClient — build-Mac httpx pin).\n# Timestamps are on the REAL epoch clock: "today" is compared to time.time().\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/api/test_ic_closed_route.py\n\nimport os\nimport sqlite3\nimport sys\nimport tempfile\nimport time\n\nimport app.api.ic_state_routes as R\nfrom app.license import license_state\n\nFAILS = []\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if not cond:\n        FAILS.append(name)\n\n\nNOW = int(time.time())\nDAY0 = R._ic_day_start(NOW)\ndb = os.path.join(tempfile.mkdtemp(prefix="ic_closed_"), "app.db")\nR._ic_db_path = lambda: db\n_real_level = license_state.ui_level\n\n\ndef set_admin(on):\n    license_state.ui_level = (lambda: "admin") if on else (lambda: "standard")\n\n\nwith sqlite3.connect(db) as c:\n    c.executescript("""\n    CREATE TABLE paper_trades (paper_trade_id TEXT, strategy_name TEXT, symbol TEXT,\n      trade_direction TEXT, qty INTEGER, lots INTEGER, entry_price REAL, exit_price REAL,\n      exit_reason TEXT, entry_time INTEGER, exit_time INTEGER, state TEXT,\n      pnl_value REAL, total_charges REAL, net_pnl REAL, group_id TEXT, trade_class TEXT);\n    CREATE TABLE trades (trade_id TEXT, strategy_id TEXT, symbol TEXT, trade_direction TEXT,\n      qty INTEGER, entry_price REAL, exit_price REAL, exit_reason TEXT, entry_time INTEGER,\n      exit_time INTEGER, state TEXT, group_id TEXT, trade_class TEXT);\n    """)\n\n\ndef paper(sid, gid, cls, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=650):\n    state = "OPEN" if ext_ts is None else "CLOSED"\n    g = c_ = n = None\n    if ext is not None:\n        g = (ent - ext) * qty if direction == "SHORT" else (ext - ent) * qty\n        c_ = 50.0\n        n = g - c_\n    with sqlite3.connect(db) as c:\n        c.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",\n                  (f"{gid}{cls}", sid, sym, direction, qty, qty // 65, ent, ext, reason,\n                   ent_ts, ext_ts, state, g, c_, n, gid, cls))\n\n\ndef live(sid, gid, cls, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=650):\n    with sqlite3.connect(db) as c:\n        c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",\n                  (f"{gid}{cls}", sid, sym, direction, qty, ent, ext, reason, ent_ts, ext_ts,\n                   "PROTECTED" if ext_ts is None else "CLOSED", gid, cls))\n\n\ndef condor(book, sid, gid, ent_ts, exits, prices):\n    """prices: {cls: (sym, dir, entry, exit)}, exits: {cls: (reason, ts)}"""\n    for cls, (sym, d, ent, ext) in prices.items():\n        reason, ts = exits.get(cls, (None, None))\n        book(sid, gid, cls, sym, d, ent, ext if ts else None, reason, ent_ts, ts)\n\n\nLEGS = {"L1": ("NIFTY2692223400CE", "SHORT", 63.5, 40.0), "L2": ("NIFTY2692223400PE", "SHORT", 85.0, 60.0),\n        "L3": ("NIFTY2692223750CE", "LONG", 2.8, 1.0),    "L4": ("NIFTY2692222850PE", "LONG", 3.75, 1.5)}\nALL_NEXT = lambda ts: {k: ("NEXT_OPEN", ts) for k in LEGS}\n\nprint("── 1. helpers")\ncheck("IST day start on the real clock", DAY0 <= NOW < DAY0 + 86400 and (DAY0 + 19800) % 86400 == 0)\ncheck("weekly symbol shortens", R._ic_short("NIFTY2692223400CE") == "23400CE")\ncheck("monthly symbol shortens", R._ic_short("NIFTY26SEP23400PE") == "23400PE")\ncheck("unparseable symbol comes back whole", R._ic_short("WEIRD") == "WEIRD")\n\nprint("── 2. paper condor entered yesterday, NEXT_OPEN today → counts TODAY")\nset_admin(True)\ncondor(paper, "IC_V2", "gA", DAY0 - 50000, ALL_NEXT(DAY0 + 60), LEGS)\ns = R.get_ic_closed("IC_V2")\ng = s["groups"][0] if s["groups"] else {}\ncheck("one group, not four legs", s["ok"] and len(s["groups"]) == 1 and g.get("legs") == 4)\ncheck("label = shorts, sub = wings", g.get("label") == "23400CE / 23400PE" and g.get("sub") == "wings 23750CE / 22850PE", str(g))\ncheck("credit = 63.5+85-2.8-3.75 = 141.95", g.get("entry") == 141.95, str(g.get("entry")))\ncheck("debit = 40+60-1-1.5 = 97.5", g.get("exit") == 97.5, str(g.get("exit")))\nexp_gross = (141.95 - 97.5) * 650\ncheck("gross = (credit-debit)*qty", abs(g.get("gross", 0) - exp_gross) < 0.01, f"{g.get(\'gross\')} vs {exp_gross}")\ncheck("net = gross - booked charges (4×50)", abs(g.get("net", 0) - (exp_gross - 200)) < 0.01)\ncheck("today by EXIT ts, single reason collapsed", g.get("today") is True and g.get("reason") == "NEXT_OPEN")\ncheck("today_net / closed_today", abs(s["today_net"] - (exp_gross - 200)) < 0.01 and s["closed_today"] == 1)\ncheck("lots from the short legs", g.get("lots") == 10)\n\nprint("── 3. partially closed condor is NOT a closed position")\ncondor(paper, "IC_V2", "gB", DAY0 + 100, {"L1": ("SL", DAY0 + 500)}, LEGS)\ns = R.get_ic_closed("IC_V2")\ncheck("group with open legs excluded", [x["group_id"] for x in s["groups"]] == ["PAPER:gA"])\n\nprint("── 4. mixed reasons, exit 1s before IST midnight → not today")\nex = ALL_NEXT(DAY0 - 1); ex["L1"] = ("SL", DAY0 - 4000)\ncondor(paper, "IC_V2", "gC", DAY0 - 90000, ex, LEGS)\ns = R.get_ic_closed("IC_V2")\ngc = next(x for x in s["groups"] if x["group_id"] == "PAPER:gC")\ncheck("group exit = LAST leg exit; 1s before midnight is not today", gc["exit_ts"] == DAY0 - 1 and gc["today"] is False)\ncheck("reason summary counts legs", gc["reason"] == "NEXT_OPEN×3 · SL×1", gc["reason"])\ncheck("newest exit first", [x["group_id"] for x in s["groups"]] == ["PAPER:gA", "PAPER:gC"])\ncheck("yesterday\'s net not in today_net", s["closed_today"] == 1)\n\nprint("── 5. sid isolation + unknown sid fails closed")\ncondor(paper, "IC_V1", "gV1", DAY0 + 10, ALL_NEXT(DAY0 + 20), LEGS)\ncheck("IC_V1 sees only its own group", [x["group_id"] for x in R.get_ic_closed("IC_V1")["groups"]] == ["PAPER:gV1"])\ncheck("IC_V2 unchanged by IC_V1 rows", len(R.get_ic_closed("IC_V2")["groups"]) == 2)\ntry:\n    R.get_ic_closed("IC_V9"); ok = False\nexcept Exception as e:\n    ok = getattr(e, "status_code", None) == 404\ncheck("unknown sid → 404", ok)\n\nprint("── 6. LIVE book: P&L derived, charges modelled, missing exit price → approx")\ncondor(live, "IC_V1", "gL", DAY0 + 30, ALL_NEXT(DAY0 + 90), LEGS)\ns = R.get_ic_closed("IC_V1")\ngl = next(x for x in s["groups"] if x["group_id"] == "LIVE:gL")\ncheck("live gross is direction-aware", abs(gl["gross"] - exp_gross) < 0.01, str(gl["gross"]))\ncheck("live charges modelled (>0) and net = gross - charges", gl["charges"] > 0 and abs(gl["net"] - (gl["gross"] - gl["charges"])) < 0.01)\ncheck("exact live group not flagged approx", gl["approx"] is False and gl["book"] == "LIVE")\nlp = dict(LEGS); lp["L1"] = ("NIFTY2692223400CE", "SHORT", 63.5, None)\nfor cls, (sym, d, ent, ext) in lp.items():\n    live("IC_V1", "gM", cls, sym, d, ent, ext, "BROKER_EXIT" if ext is None else "NEXT_OPEN", DAY0 + 40, DAY0 + 95)\ngm = next(x for x in R.get_ic_closed("IC_V1")["groups"] if x["group_id"] == "LIVE:gM")\ncheck("leg without exit price → approx, debit withheld", gm["approx"] is True and gm["exit"] is None)\n\nprint("── 7. adjustment legs")\nadj = dict(LEGS); adj["L1A"] = ("NIFTY2692223500CE", "LONG", 20.0, 30.0)\ncondor(paper, "IC_V2", "gD", DAY0 + 200, {k: ("EOD", DAY0 + 300) for k in adj}, adj)\ngd = next(x for x in R.get_ic_closed("IC_V2")["groups"] if x["group_id"] == "PAPER:gD")\ncheck("adj leg in sub + folded into net, not into the label", gd["sub"].endswith("+1 adj") and gd["label"] == "23400CE / 23400PE" and gd["legs"] == 5)\n\nprint("── 8. masking + limit + resilience")\nset_admin(False)\ncheck("standard licence: reasons masked", all(x["reason"] == "CLOSED" for x in R.get_ic_closed("IC_V2")["groups"]))\nset_admin(True)\ncheck("limit honoured", len(R.get_ic_closed("IC_V2", limit=1)["groups"]) == 1)\ncheck("today_net independent of limit", R.get_ic_closed("IC_V2", limit=1)["today_net"] == R.get_ic_closed("IC_V2")["today_net"])\nR._ic_db_path = lambda: os.path.join(os.path.dirname(db), "missing", "nope.db")\nr = R.get_ic_closed("IC_V2")\ncheck("unreadable DB never raises", r["groups"] == [] and "today_net" in r)\nR._ic_db_path = lambda: db\n\nwith sqlite3.connect(db) as c:\n    c.execute("ALTER TABLE trades RENAME COLUMN trade_class TO leg_tag")\nr = R.get_ic_closed("IC_V2")\ncheck("schema drift is SURFACED, paper book still served", r.get("warnings") and len(r["groups"]) == 3, str(r.get("warnings")))\n\nprint("── 9. existing endpoints untouched")\nst = R.get_ic_state("IC_V2")\ncheck("/state still answers with its keys", {"mode", "engine_up", "group", "latched_today"} <= set(st))\n\nlicense_state.ui_level = _real_level\nprint()\nif FAILS:\n    print(f"  {len(FAILS)} FAILED: {FAILS}")\n    sys.exit(1)\nprint("  ALL CHECKS PASSED")\n'

TMA_EDITS = [('after', 'import BrokerChip from "../../components/BrokerChip"; // ACC2_W3\n', 'import ClosedRecent, { groupSpreadLegs } from "../../components/ClosedRecent";   // ── CLOSED_RECENT_20260921 ──\n'),
 ('replace',
  '  const listed = [...open, ...closedToday];\n',
  '  // ── CLOSED_RECENT_20260921 ── one entry per SPREAD (closed only when every\n'
  '  // leg is); replaces the per-leg "open + closed-today" list. netToday stays\n'
  '  // the exact per-leg sum above.\n'
  '  const closedGroups = groupSpreadLegs(trades);\n'
  '  const closedTodayN = closedGroups.filter((g) => g.today).length;\n'),
 ('replace',
  '<div><div style={label}>Closed today</div><div style={{ fontSize: 20, fontWeight: 700 }}>{closedToday.length}</div></div>',
  '<div><div style={label}>Closed today</div><div style={{ fontSize: 20, fontWeight: 700 }}>{closedTodayN}</div></div>'),
 ('span_to_eof',
  '      <div style={{ ...card, padding: 0 }}>\n',
  '      {/* ── CLOSED_RECENT_20260921 ── open legs the manager does not hold: the\n'
  "          spread card above is drawn from the MANAGER's group, so an OPEN row\n"
  '          in the book with no group (loop not up / still booting / disabled)\n'
  '          used to be visible only in the old mixed table. Say it loudly. */}\n'
  '      {open.length > 0 && !status.group && (\n'
  '        <div style={{ ...card, fontSize: 12, borderColor: colors.loss }}>\n'
  '          <b style={{ color: colors.loss }}>⚠ {open.length} open leg{open.length === 1 ? "" : "s"} on the book, but the manager holds no position</b>\n'
  '          <span style={{ color: colors.text.secondary }}> — the loop is not up (booting, waiting for the broker session, or disabled). Nothing is managing these until it starts.</span>\n'
  '          <div style={{ marginTop: 6, ...typography.mono, color: colors.text.tertiary }}>\n'
  '            {open.map((t) => `${t.direction === "SELL" ? "SHORT" : "HEDGE"} ${t.tradingsymbol} @ ${t.entry_price?.toFixed(2)} · ${fmtTs(t.entry_ts)}`).join("   ·   ")}\n'
  '          </div>\n'
  '        </div>\n'
  '      )}\n'
  '\n'
  '      {/* ── CLOSED_RECENT_20260921 ── one row per SPREAD (hedge folded into net),\n'
  "          today's exits + the recent tail — VET panel v2 parity. */}\n"
  '      <ClosedRecent\n'
  '        groups={closedGroups}\n'
  '        todayNet={netToday}\n'
  '        showParams={showParams}\n'
  '        sizeLabel="Qty"\n'
  '        entryLabel="Sold @"\n'
  '        exitLabel="Covered @"\n'
  '        emptyText={showParams\n'
  '          ? `No closed spreads yet — signals fire at 5m boundaries inside the entry window${sigEng.candles != null ? ` · engine fed ${sigEng.candles} candles, ${sigEng.signals_emitted || 0} '
  'signals` : ""}.`\n'
  '          : "No closed positions yet."}\n'
  '      />\n'
  '    </div>\n'
  '  );\n'
  '}')]

IC_EDITS = [('after',
  'import BrokerChip from "../../components/BrokerChip"; // ACC2_W3\n',
  'import { getApiBase } from "../../api/base";                       // ── CLOSED_RECENT_20260921 ──\n'
  'import ClosedRecent, { useClosedGroups } from "../../components/ClosedRecent";   // ── CLOSED_RECENT_20260921 ──\n'),
 ('after',
  '  const showParams = !licenseLoaded || isAdminUi;\n',
  '  // ── CLOSED_RECENT_20260921 ── closed condors (own slow poll; hook sits above\n'
  '  // the loading early-return so hook order never changes)\n'
  '  const closedIc = useClosedGroups(`${getApiBase()}/api/ic/${strategyId}/closed`);\n'),
 ('replace',
  '  return (\n    <div style={{\n      background: C.bgCard, border: `1px solid ${C.border}`,\n      borderTop: `3px solid ${ACCENT}`, borderRadius: 10,\n',
  '  return (\n'
  '    <>{/* ── CLOSED_RECENT_20260921 ── fragment: condor card + closed card */}\n'
  '    <div style={{\n'
  '      background: C.bgCard, border: `1px solid ${C.border}`,\n'
  '      borderTop: `3px solid ${ACCENT}`, borderRadius: 10,\n'),
 ('replace',
  '        </>\n      )}\n    </div>\n  );\n}',
  '        </>\n'
  '      )}\n'
  '    </div>\n'
  '    {/* ── CLOSED_RECENT_20260921 ── one row per CONDOR: net credit in, net debit\n'
  '        out, every leg folded into the net — VET panel v2 parity. */}\n'
  '    <ClosedRecent\n'
  '      groups={closedIc.groups}\n'
  '      todayNet={closedIc.today_net}\n'
  '      showParams={showParams}\n'
  '      entryLabel="Credit"\n'
  '      exitLabel="Debit"\n'
  '      emptyText={closedIc.loaded ? "No closed condors yet." : "Loading closed condors…"}\n'
  '      style={{ marginTop: spacing?.md ?? 12 }}\n'
  '    />\n'
  '    </>\n'
  '  );\n'
  '}')]


def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def apply_edits(text, edits, rel):
    for kind, anchor, payload in edits:
        n = text.count(anchor)
        if n != 1:
            fail(f"{rel}: anchor x{n}, expected x1: {anchor[:70]!r}")
        if kind == "replace":
            text = text.replace(anchor, payload)
        elif kind == "after":
            text = text.replace(anchor, anchor + payload)
        elif kind == "span_to_eof":
            # everything from the anchor to EOF is the old table + the
            # component's closing lines; payload carries the new closing.
            head, tail = text.split(anchor)
            if not "".join(tail.split()).endswith("</div></div>);}"):
                fail(f"{rel}: unexpected file tail after the table anchor — inspect by hand")
            if "Open + closed-today legs" not in tail:
                fail(f"{rel}: the span to replace is not the legs table — inspect by hand")
            text = head + payload
        else:
            fail(f"unknown edit kind {kind}")
    return text


def git_dirty(rels):
    """Working-copy drift scar (ORB_RECONCILE): an 'M' is a stop sign."""
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--"] + rels,
                           cwd=ROOT, capture_output=True, text=True, timeout=20)
        return None if r.returncode != 0 else [ln for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry run, write nothing")
    ap.add_argument("--single-tree", action="store_true", help="CI checkout without desktop/src-tauri/backend")
    ap.add_argument("--allow-dirty", action="store_true", help="proceed although git shows local edits on the targets")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard requirement "
             "locally; pass --single-tree only on a CI checkout")

    edited = [IC_ROUTE, IC_PANEL, TMA_PANEL, TMA2_PANEL]
    for rel in edited:
        if not os.path.exists(os.path.join(ROOT, rel)):
            fail(f"{rel} not found — pull main first")
    src = {rel: read(os.path.join(ROOT, rel)) for rel in edited}

    # ── idempotency: all-or-nothing ──
    comp_abs = os.path.join(ROOT, COMP)
    have = [rel for rel in edited if FENCE in src[rel]]
    if os.path.exists(comp_abs):
        have.append(COMP)
    if len(have) == len(edited) + 1:
        print("  SKIP   closed-recent sections already present — nothing to do")
        return
    if have:
        fail("fence/component present in SOME targets only — mixed state:\n         "
             + "\n         ".join(have)
             + "\n         do NOT hand-place files; this script carries everything."
               "\n         undo with: git checkout -- <those files>  (and delete "
               "ClosedRecent.jsx / test_ic_closed_route.py if you copied them), then re-run")

    # ── prerequisites ──
    rt = src[IC_ROUTE]
    for needle in ('@router.get("/api/ic/{sid}/state")', "def _require_sid(sid: str)",
                   "import time as _time", "from app.event_bus.audit_logger import write_audit_log"):
        if rt.count(needle) != 1:
            fail(f"{IC_ROUTE}: prerequisite {needle!r} x{rt.count(needle)}, expected x1")
    if "/closed" in rt:
        fail(f"{IC_ROUTE} already defines a /closed route from another patch — reconcile first")
    for rel in ("frontend/src/tokens.js", "frontend/src/api/base.js",
                "backend/app/backtest/charges/charges_model.py", "backend/app/license/license_state.py"):
        if not os.path.exists(os.path.join(ROOT, rel)):
            fail(f"prerequisite file missing: {rel}")
    if "export const alpha" not in read(os.path.join(ROOT, "frontend/src/tokens.js")):
        fail("tokens.js has no alpha() — THEME_PHASE1 not applied?")

    dirty = git_dirty(edited)
    if dirty is None:
        print("  WARN   git status unavailable — cannot check for uncommitted drift")
    elif dirty and not a.allow_dirty:
        fail("uncommitted local edits on the targets (another session's patch?):\n         "
             + "\n         ".join(dirty) + "\n         commit/inspect first, or re-run with --allow-dirty")
    else:
        print("  OK     targets clean in git")

    # ── stage in memory ──
    staged = {}
    new_route = rt + IC_BLOCK
    for p in both_trees(IC_ROUTE, a.single_tree):
        if not os.path.exists(p):
            fail(f"dual-tree copy missing: {p}")
        if read(p) != rt:
            fail(f"dual-tree drift: {p} differs from {IC_ROUTE} — sync the trees first")
        staged[p] = new_route
    for p in both_trees(IC_TEST, a.single_tree):
        staged[p] = TEST_BODY
    staged[comp_abs] = COMPONENT
    staged[os.path.join(ROOT, IC_PANEL)] = apply_edits(src[IC_PANEL], IC_EDITS, IC_PANEL)
    staged[os.path.join(ROOT, TMA_PANEL)] = apply_edits(src[TMA_PANEL], TMA_EDITS, TMA_PANEL)
    staged[os.path.join(ROOT, TMA2_PANEL)] = apply_edits(src[TMA2_PANEL], TMA_EDITS, TMA2_PANEL)
    for rel in (TMA_PANEL, TMA2_PANEL):          # no dangling reference to removed locals
        body = staged[os.path.join(ROOT, rel)]
        if "listed" in body.split("export default function")[1]:
            fail(f"internal: {rel} still references `listed`")
    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── compile gates ──
    tmp = tempfile.mkdtemp(prefix="clr_gate_")
    jsx = []
    for i, (p, body) in enumerate(staged.items()):
        t = os.path.join(tmp, f"{i}_{os.path.basename(p)}")
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        else:
            jsx.append((p, t))
    print("  OK     py_compile gate passed")
    esb, npx = shutil.which("esbuild"), shutil.which("npx")
    cmd = ([esb] if esb else [npx, "--yes", "esbuild"] if npx else None)
    if cmd is None:
        print("  WARN   esbuild unavailable — JSX gate skipped")
    else:
        for p, t in jsx:
            r = subprocess.run(cmd + ["--loader:.jsx=jsx", t, "--outfile=" + os.devnull],
                               capture_output=True, text=True, cwd=os.path.join(ROOT, "frontend"))
            if r.returncode != 0:
                fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
        print(f"  OK     esbuild JSX gate passed ({len(jsx)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  {'edit  ' if os.path.exists(p) else 'create'} {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write ──
    created, backed = [], []
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
            backed.append(p)
        else:
            created.append(p)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    def rollback(why):
        for p in backed:
            shutil.copy2(p + f".bak-{FENCE}", p)
        for p in created:
            try:
                os.remove(p)
            except OSError:
                pass
        fail(f"{why} — ALL files restored to their pre-patch state")

    for rel, needle, n in ((IC_ROUTE, FENCE, 2), (IC_ROUTE, '@router.get("/api/ic/{sid}/state")', 1),
                           (IC_ROUTE, '@router.get("/api/ic/{sid}/closed")', 1),
                           (IC_PANEL, FENCE, 4), (TMA_PANEL, FENCE, 4), (TMA2_PANEL, FENCE, 4),
                           (TMA_PANEL, "<ClosedRecent", 1), (TMA2_PANEL, "<ClosedRecent", 1),
                           (IC_PANEL, "<ClosedRecent", 1), (COMP, FENCE, 1), (IC_TEST, FENCE, 1)):
        got = read(os.path.join(ROOT, rel)).count(needle)
        print(f"  {'OK ' if got >= n else 'BAD'}    {rel}: {needle!r} x{got} (need >= {n})")
        if got < n:
            rollback("verification failed")

    if a.skip_tests:
        print("  WARN   --skip-tests: behavioural suite NOT run")
    else:
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "backend"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, IC_TEST)], cwd=os.path.join(ROOT, "backend"),
                           env=env, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or "ALL CHECKS PASSED" not in r.stdout:
            print(r.stdout[-3000:]); print(r.stderr[-2000:])
            rollback("behavioural suite failed")
        print(f"  OK     behavioural suite: {r.stdout.count('  PASS  ')} checks — ALL CHECKS PASSED")

    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    print()
    print("  DONE   closed-recent sections applied (files only — nothing was restarted).")
    if ist.weekday() < 5 and (9, 0) <= (ist.hour, ist.minute) <= (15, 30):
        print("  NOTE   market session is open and the fleet has a LIVE strategy:")
        print("         rebuild + restart AFTER 15:30 IST (no-live-deploy rule).")
    print("  NEXT   ./desktop/build-scalp.sh both  → restart → Dashboard → IC V1/V2, TMA V1/V2")
    print("         curl -s localhost:<port>/api/ic/IC_V2/closed | python3 -m json.tool")
    print(f"         then commit (fence {FENCE}); keep *.bak-* out of git")


if __name__ == "__main__":
    main()
