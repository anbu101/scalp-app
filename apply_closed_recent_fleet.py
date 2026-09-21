#!/usr/bin/env python3
# apply_closed_recent_fleet.py — "Closed positions · today and recent" for the
# REST of the fleet: SCALP_V1, SCALP_V3, SCALP_V5, TSG_V1, BB_V1, BB_V2,
# HA_V1, BRK_V1, ORB_V1 (and any strategy added later).
#
# Fence: CLOSED_RECENT_FLEET_20260921
# PREREQUISITE: CLOSED_RECENT_20260921 applied (frontend/src/components/
#   ClosedRecent.jsx with useClosedGroups) — asserted below. Run
#   apply_closed_recent.py first if it is not.
#
# DESIGN — no panel is edited. Nine panels with nine layouts (BBPanel is
# 1,500 lines and BB_V1 is change-controlled) would mean nine sets of
# anchors and nine ways to drift. Instead:
#   NEW  backend/app/api/closed_recent_routes.py (both trees)
#        GET /api/closed/{strategy_id} — one generic, read-only route over
#        the SAME stores and, for SCALP_V3/V5, the SAME mappers the Trades
#        pages use (parity by construction). One row per POSITION; TSG legs
#        are clustered into strangle baskets (credit/debit, wings folded
#        in); booked charges used where a store books them, modelled where
#        it keeps gross only, `approx` when neither is possible; reasons
#        masked server-side for non-admin; schema drift surfaced in
#        `warnings`, never swallowed.
#   NEW  backend/app/api/test_closed_recent_routes.py (both trees)
#   NEW  frontend/src/components/ClosedRecentFor.jsx — mounts the shared
#        ClosedRecent card for a strategy id; renders nothing for the five
#        panels that carry their own (IC_V1/V2, TMA_V1/V2, VET_V1).
#   EDIT backend/app/api_server.py (both trees): import + include_router.
#   EDIT frontend/src/components/StrategyHost.jsx: import + ONE line under
#        the focused panel at both render sites (mobile / desktop).
# Existing "today" tables inside ORB/BRK/TSG/Scalp panels are left alone —
# they list OPEN rows and strategy-specific columns this card does not.
#
# Runs the suite after writing and ROLLS BACK every file if it fails.
# Files only — nothing restarts. TSG is live: rebuild + restart after 15:30.
#
# USAGE: python3 apply_closed_recent_fleet.py --check && python3 apply_closed_recent_fleet.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone

FENCE = "CLOSED_RECENT_FLEET_20260921"
PARENT_FENCE = "CLOSED_RECENT_20260921"
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

ROUTE = "backend/app/api/closed_recent_routes.py"
TEST = "backend/app/api/test_closed_recent_routes.py"
SERVER = "backend/app/api_server.py"
WRAP = "frontend/src/components/ClosedRecentFor.jsx"
HOST = "frontend/src/components/StrategyHost.jsx"
SHARED = "frontend/src/components/ClosedRecent.jsx"

# (kind, anchor, payload, expected anchor count)
SERVER_EDITS = [
    ("after_line", "from app.api.vet_state_routes import router as vet_state_router",
     "from app.api.closed_recent_routes import router as closed_recent_router   # ── CLOSED_RECENT_FLEET_20260921 ──\n", 1),
    ("after_line", "app.include_router(vet_state_router)",
     "app.include_router(closed_recent_router)   # ── CLOSED_RECENT_FLEET_20260921 ──\n", 1),
]
HOST_EDITS = [
    ("after_line", 'import KillSwitch   from "./KillSwitch.jsx";',
     'import ClosedRecentFor from "./ClosedRecentFor.jsx";   // ── CLOSED_RECENT_FLEET_20260921 ──\n', 1),
    ("replace", "{renderPanel(effectiveFocus, ltpMap)}\n",
     "{renderPanel(effectiveFocus, ltpMap)}\n"
     "          <ClosedRecentFor strategyId={effectiveFocus} />   {/* ── CLOSED_RECENT_FLEET_20260921 ── */}\n", 2),
]

ROUTE_BODY = '# backend/app/api/closed_recent_routes.py\n#\n# ── CLOSED_RECENT_FLEET_20260921 ── GET /api/closed/{strategy_id}\n# Feeds the dashboard\'s "Closed positions · today and recent" card for every\n# strategy whose panel does not already carry one (IC_*, TMA_*, VET_V1 have\n# their own sources — fence CLOSED_RECENT_20260921 / VET_PANEL_V2_20260921).\n# Read-only. Never throws. Touches no engine, no manager, no other route.\n#\n# PARITY BY CONSTRUCTION with the Trades page: rows come from the same\n# stores, and for the private-table strategies through the SAME mappers the\n# Trades / Paper-Trades pages use, so a number here can never disagree with\n# the number the operator already trusts there.\n#     paper_trades  (strategy_name=?)  PAPER + trade_mode=\'LIVE\' rows\n#                   SCALP_V1 / BB / HA paper, TSG, BRK, ORB, GC ...\n#     trades        (strategy_id=?)    LIVE rows of SCALP_V1 / BB / HA ...\n#     SCALP_V3      _load_scalp_v3_paper + _query_scalp_v3_live  (hedge leg)\n#     SCALP_V5      _load_scalpv5_paper  + _query_scalp_v5_live\n# A strategy added later that books into paper_trades / trades shows up with\n# NO edit here.\n#\n# One entry per POSITION, never per leg:\n#   * single-leg strategies — one row is one position;\n#   * TSG_V1 — a strangle basket: legs of the same book entered within\n#     BASKET_WINDOW_S of the basket\'s first leg (TSG rows carry no group_id);\n#     listed only when EVERY leg is closed, exit time = the LAST leg\'s exit.\n#     Entry/exit are the net credit/debit per unit, wings folded into net.\n# "today" = EXIT timestamp inside the current IST day (real clock).\n#\n# Net P&L: a row\'s booked total_charges/net_pnl are used when present\n# (paper_trades books them at close). Where a store keeps gross only\n# (trades, scalp_v3/v5 — by design, the Trades page recomputes charges\n# client-side) charges come from the backtest charges model; if that fails\n# the group is flagged `approx` — never an invented number shown as exact.\n# A closed row with no exit price contributes 0 and flags `approx` too.\n#\n# Masking: exit reasons read "CLOSED" for non-admin licences, server-side\n# (Phase 2b, fails closed).\n\nfrom __future__ import annotations\n\nimport re\nimport sqlite3\nimport time\nfrom typing import Dict, List, Optional\n\nfrom fastapi import APIRouter\n\nfrom app.event_bus.audit_logger import write_audit_log\n\nrouter = APIRouter(tags=["closed-recent"])\n\nIST_OFF = 5 * 3600 + 30 * 60\nBASKET_WINDOW_S = 600            # seconds; legs of one TSG basket fill within it\nROW_LIMIT = 500                  # newest legs read per store\nCACHE_TTL_S = 8                  # panel polls every 15 s; two tabs share a read\nBASKET_STRATEGIES = {"TSG_V1"}\n_SID_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")\n_SYM_RE = re.compile(r"^([A-Z]+)(.{5})(\\d+)(CE|PE)$")\n_cache: Dict[str, tuple] = {}\n_warned = set()\n\n\ndef _day_start(now: int) -> int:\n    """Epoch of 00:00 IST for the IST day containing `now`."""\n    return ((int(now) + IST_OFF) // 86400) * 86400 - IST_OFF\n\n\ndef _db_path() -> str:\n    from app.db.sqlite import DB_PATH\n    return str(DB_PATH)\n\n\ndef _is_admin() -> bool:\n    try:\n        from app.license import license_state\n        return license_state.ui_level() == "admin"\n    except Exception:\n        return False                                   # fail closed\n\n\ndef _short(sym: str) -> str:\n    m = _SYM_RE.match(str(sym or "").replace(" ", "").upper())\n    return f"{m.group(3)}{m.group(4)}" if m else str(sym or "")\n\n\ndef _model_charges(is_short: bool, entry: float, exit_px: float, qty: int):\n    try:\n        from app.backtest.charges.charges_model import (\n            charges_for_long_trade, charges_for_short_trade)\n        fn = charges_for_short_trade if is_short else charges_for_long_trade\n        cr = fn(entry_price=float(entry), exit_price=float(exit_px), qty=int(qty))\n        return float(getattr(cr, "total_charges", 0.0) or 0.0), True\n    except Exception:\n        return 0.0, False\n\n\ndef _norm(d: dict, *, book: str, rid, open_: bool) -> dict:\n    """Any store\'s row -> one leg. gross/charges/net resolved here, once."""\n    direction = str(d.get("trade_direction") or "LONG").upper()\n    short = direction == "SHORT"\n    ent, ext = d.get("entry_price"), d.get("exit_price")\n    qty = int(d.get("qty") or 0)\n    approx = False\n    gross = charges = net = 0.0\n    if not open_:\n        if ent is None or ext is None:\n            approx = True\n        else:\n            g = d.get("pnl_value")\n            gross = float(g) if g is not None else (\n                (float(ent) - float(ext)) * qty if short else (float(ext) - float(ent)) * qty)\n            if d.get("total_charges") is not None and d.get("net_pnl") is not None:\n                charges, net = float(d["total_charges"]), float(d["net_pnl"])\n            else:\n                charges, ok = _model_charges(short, ent, ext, qty)\n                approx = not ok\n                net = gross - charges\n    return {"id": f"{book}:{rid}", "book": book, "symbol": d.get("symbol") or "",\n            "short": short, "qty": qty, "lots": d.get("lots"),\n            "entry": ent, "exit": ext,\n            "entry_ts": int(d.get("entry_time") or 0),\n            "exit_ts": (int(d["exit_time"]) if d.get("exit_time") else None),\n            "reason": d.get("exit_reason"), "open": open_,\n            "gross": gross, "charges": charges, "net": net, "approx": approx,\n            "leg": d.get("trade_class") or d.get("slot")}\n\n\ndef _legs(sid: str, warnings: list) -> List[dict]:\n    legs: List[dict] = []\n    with sqlite3.connect(_db_path(), timeout=30) as conn:\n        conn.row_factory = sqlite3.Row\n        try:\n            for r in conn.execute(\n                    "SELECT paper_trade_id, trade_mode, symbol, trade_direction, qty, lots,"\n                    " entry_price, exit_price, exit_reason, entry_time, exit_time, state,"\n                    " pnl_value, total_charges, net_pnl, trade_class"\n                    " FROM paper_trades WHERE strategy_name=?"\n                    " ORDER BY entry_time DESC LIMIT ?", (sid, ROW_LIMIT)):\n                d = dict(r)\n                book = "LIVE" if str(d.get("trade_mode") or "").upper() == "LIVE" else "PAPER"\n                legs.append(_norm(d, book=book, rid=d["paper_trade_id"], open_=d["state"] == "OPEN"))\n        except Exception as e:\n            if "no such table" not in str(e):\n                warnings.append(f"paper_trades: {e!r}")\n        try:\n            for r in conn.execute(\n                    "SELECT trade_id, slot, symbol, trade_direction, qty, entry_price,"\n                    " exit_price, exit_reason, entry_time, exit_time"\n                    " FROM trades WHERE strategy_id=?"\n                    " ORDER BY entry_time DESC LIMIT ?", (sid, ROW_LIMIT)):\n                d = dict(r)\n                legs.append(_norm(d, book="LIVE", rid=d["trade_id"], open_=d["exit_time"] is None))\n        except Exception as e:\n            if "no such table" not in str(e):\n                warnings.append(f"trades: {e!r}")\n        # ── private tables: through the Trades-page mappers, never re-derived ──\n        if sid in ("SCALP_V3", "SCALP_V5"):\n            try:\n                from app.api import paper_trades_routes as _p\n                load = _p._load_scalp_v3_paper if sid == "SCALP_V3" else _p._load_scalpv5_paper\n                _open, _closed = load(conn)\n                for d in (_closed or [])[:ROW_LIMIT]:\n                    d = dict(d)\n                    d["total_charges"] = d["net_pnl"] = None      # mapper net == gross, by design\n                    d.setdefault("trade_direction", "LONG")\n                    legs.append(_norm(d, book="PAPER", rid=d.get("paper_trade_id"), open_=False))\n            except Exception as e:\n                warnings.append(f"{sid} paper mapper: {e!r}")\n    if sid in ("SCALP_V3", "SCALP_V5"):\n        try:\n            from app.api import trade_history_routes as _h\n            q = _h._query_scalp_v3_live if sid == "SCALP_V3" else _h._query_scalp_v5_live\n            rows = sorted(q(None, None) or [], key=lambda t: t.get("entry_time") or 0, reverse=True)\n            for d in rows[:ROW_LIMIT]:\n                legs.append(_norm(dict(d), book="LIVE", rid=d.get("trade_id"),\n                                  open_=d.get("state") != "CLOSED"))\n        except Exception as e:\n            warnings.append(f"{sid} live mapper: {e!r}")\n    return legs\n\n\ndef _baskets(legs: List[dict]) -> List[List[dict]]:\n    """Cluster by book + entry time: a new basket starts when a leg entered\n    more than BASKET_WINDOW_S after the basket\'s FIRST leg."""\n    out: List[List[dict]] = []\n    for book in ("PAPER", "LIVE"):\n        cur: List[dict] = []\n        for l in sorted((x for x in legs if x["book"] == book), key=lambda x: x["entry_ts"]):\n            if cur and l["entry_ts"] - cur[0]["entry_ts"] > BASKET_WINDOW_S:\n                out.append(cur)\n                cur = []\n            cur.append(l)\n        if cur:\n            out.append(cur)\n    return out\n\n\ndef _group(legs: List[dict], day0: int, basket: bool) -> Optional[dict]:\n    if any(l["open"] or not l["exit_ts"] for l in legs):\n        return None                                     # still an open position\n    exit_ts = max(l["exit_ts"] for l in legs)\n    approx = any(l["approx"] for l in legs)\n    main = next((l for l in legs if l["short"]), legs[0]) if basket else legs[0]\n    g = {"group_id": legs[0]["id"], "book": legs[0]["book"],\n         "kind": "S" if main["short"] else "L",\n         "legs": len(legs), "lots": main.get("lots"), "qty": main["qty"],\n         "entry_ts": min(l["entry_ts"] for l in legs), "exit_ts": exit_ts,\n         "gross": round(sum(l["gross"] for l in legs), 2),\n         "charges": round(sum(l["charges"] for l in legs), 2),\n         "net": round(sum(l["net"] for l in legs), 2),\n         "approx": approx, "today": exit_ts >= day0}\n    if not basket or len(legs) == 1:\n        g.update({"label": main["symbol"], "sub": None, "entry": main["entry"],\n                  "exit": main["exit"], "reason": main["reason"]})\n        return g\n    shorts = sorted((l for l in legs if l["short"]), key=lambda l: str(l["leg"]))\n    longs = sorted((l for l in legs if not l["short"]), key=lambda l: str(l["leg"]))\n    base = max([l["qty"] for l in (shorts or legs)] or [0])\n    credit = sum((1 if l["short"] else -1) * float(l["entry"] or 0) * l["qty"] for l in legs)\n    debit = sum((1 if l["short"] else -1) * float(l["exit"] or 0) * l["qty"] for l in legs)\n    counts: Dict[str, int] = {}\n    for l in legs:\n        k = l["reason"] or "—"\n        counts[k] = counts.get(k, 0) + 1\n    g.update({\n        "label": " / ".join(_short(l["symbol"]) for l in (shorts or legs)),\n        "sub": ("wings " + " / ".join(_short(l["symbol"]) for l in longs)) if longs and shorts else None,\n        "entry": round(credit / base, 2) if base else None,\n        "exit": round(debit / base, 2) if base and not approx else None,\n        "reason": " · ".join(k if n == len(legs) else f"{k}×{n}"\n                             for k, n in sorted(counts.items(), key=lambda kv: -kv[1])),\n    })\n    return g\n\n\n@router.get("/api/closed/{strategy_id}")\ndef get_closed_recent(strategy_id: str, limit: int = 12):\n    sid = str(strategy_id or "").upper()\n    now = int(time.time())\n    day0 = _day_start(now)\n    out = {"ok": True, "strategy": sid, "groups": [], "today_net": 0.0,\n           "closed_today": 0, "server_ts": now}\n    if not _SID_RE.match(sid):\n        out["ok"] = False\n        out["error"] = "bad strategy id"\n        return out\n    try:\n        limit = max(1, min(int(limit), 50))\n        hit = _cache.get(sid)\n        if hit and now - hit[0] < CACHE_TTL_S and hit[1] == day0:\n            groups, warns = hit[2], hit[3]\n        else:\n            warns: list = []\n            legs = _legs(sid, warns)\n            basket = sid in BASKET_STRATEGIES\n            sets = _baskets(legs) if basket else [[l] for l in legs]\n            if basket:\n                # ROW_LIMIT can cut a book\'s OLDEST basket in half; half a\n                # strangle with every surviving leg closed would list with\n                # wrong numbers. Drop it.\n                for book in ("PAPER", "LIVE"):\n                    if sum(1 for l in legs if l["book"] == book) >= ROW_LIMIT:\n                        first = next((b for b in sets if b[0]["book"] == book), None)\n                        if first is not None:\n                            sets.remove(first)\n            groups = [g for g in (_group(s, day0, basket) for s in sets) if g]\n            groups.sort(key=lambda g: g["exit_ts"], reverse=True)\n            _cache[sid] = (now, day0, groups, warns)\n        admin = _is_admin()\n        out["today_net"] = round(sum(g["net"] for g in groups if g["today"]), 2)\n        out["closed_today"] = sum(1 for g in groups if g["today"])\n        out["groups"] = [dict(g, reason=(g["reason"] if admin else "CLOSED")) for g in groups[:limit]]\n        if warns:\n            out["warnings"] = warns\n            if sid not in _warned:                     # once per process, not per poll\n                _warned.add(sid)\n                write_audit_log(f"[API][CLOSED_RECENT][{sid}][WARN] {warns}")\n    except Exception as e:\n        out["ok"] = False\n        out["error"] = repr(e)\n    return out\n'

TEST_BODY = '# backend/app/api/test_closed_recent_routes.py\n#\n# ── CLOSED_RECENT_FLEET_20260921 ── behavioural suite for\n# GET /api/closed/{strategy_id}. Route function called directly (no\n# TestClient — build-Mac httpx pin). Timestamps on the REAL epoch clock.\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/api/test_closed_recent_routes.py\n\nimport os\nimport sqlite3\nimport sys\nimport tempfile\nimport time\n\nimport app.api.closed_recent_routes as R\nimport app.api.trade_history_routes as H\nfrom app.license import license_state\n\nFAILS = []\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if not cond:\n        FAILS.append(name)\n\n\nNOW = int(time.time())\nDAY0 = R._day_start(NOW)\ndb = os.path.join(tempfile.mkdtemp(prefix="closed_fleet_"), "app.db")\nR._db_path = lambda: db\nH.DB_PATH = db                                  # the V3/V5 live mappers open their own conn\nR.CACHE_TTL_S = 0                               # every call re-reads in this suite\n_real_level = license_state.ui_level\nlicense_state.ui_level = lambda: "admin"\n\nwith sqlite3.connect(db) as c:\n    c.executescript("""\n    CREATE TABLE paper_trades (paper_trade_id TEXT, strategy_name TEXT, trade_mode TEXT, symbol TEXT,\n      trade_direction TEXT, qty INTEGER, lots INTEGER, entry_price REAL, exit_price REAL,\n      exit_reason TEXT, entry_time INTEGER, exit_time INTEGER, state TEXT,\n      pnl_value REAL, total_charges REAL, net_pnl REAL, group_id TEXT, trade_class TEXT);\n    CREATE TABLE trades (trade_id TEXT, strategy_id TEXT, slot TEXT, symbol TEXT, trade_direction TEXT,\n      qty INTEGER, entry_price REAL, exit_price REAL, exit_reason TEXT, entry_time INTEGER,\n      exit_time INTEGER, state TEXT);\n    CREATE TABLE scalp_v3_trades (v3_trade_id TEXT, strategy_name TEXT, paper INTEGER, hedge_symbol TEXT,\n      hedge_side TEXT, hedge_qty INTEGER, hedge_entry_price REAL, hedge_sl REAL, hedge_gtt_id TEXT,\n      entry_time INTEGER, exit_time INTEGER, exit_price REAL, exit_reason TEXT, realized_pnl REAL, state TEXT);\n    """)\n_n = [0]\n\n\ndef paper(sid, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=650, mode="PAPER", cls=None, booked=True):\n    _n[0] += 1\n    g = ch = n = None\n    if ext is not None and ext_ts and booked:\n        g = (ent - ext) * qty if direction == "SHORT" else (ext - ent) * qty\n        ch, n = 50.0, g - 50.0\n    with sqlite3.connect(db) as c:\n        c.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",\n                  (f"p{_n[0]}", sid, mode, sym, direction, qty, qty // 65, ent, ext, reason, ent_ts, ext_ts,\n                   "OPEN" if not ext_ts else "CLOSED", g, ch, n, None, cls))\n\n\ndef live(sid, sym, direction, ent, ext, reason, ent_ts, ext_ts, qty=65):\n    _n[0] += 1\n    with sqlite3.connect(db) as c:\n        c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",\n                  (f"t{_n[0]}", sid, "CE_1", sym, direction, qty, ent, ext, reason, ent_ts, ext_ts,\n                   "CLOSED" if ext_ts else "PROTECTED"))\n\n\ndef ids(sid, **kw):\n    return [g["label"] for g in R.get_closed_recent(sid, **kw)["groups"]]\n\n\nprint("── 1. clock + ids")\ncheck("IST day start on the real clock", DAY0 <= NOW < DAY0 + 86400 and (DAY0 + 19800) % 86400 == 0)\ncheck("bad strategy id refused, never queried", R.get_closed_recent("x\'; DROP")["ok"] is False)\ncheck("unknown-but-valid id → empty, ok", R.get_closed_recent("NEW_V9")["groups"] == [])\n\nprint("── 2. single-leg LONG (ORB): booked charges, today by EXIT ts")\npaper("ORB_V1", "NIFTY2692223400CE", "LONG", 180.0, 270.0, "TP", DAY0 + 100, DAY0 + 900)\npaper("ORB_V1", "NIFTY2691523300PE", "LONG", 170.0, 150.0, "SL", DAY0 - 90000, DAY0 - 1)\npaper("ORB_V1", "NIFTY2692223500CE", "LONG", 160.0, None, None, DAY0 + 1000, None)          # open\ns = R.get_closed_recent("ORB_V1")\ng0 = s["groups"][0]\ncheck("open row excluded; newest exit first", [g["label"] for g in s["groups"]] == ["NIFTY2692223400CE", "NIFTY2691523300PE"])\ncheck("net = booked net_pnl", g0["net"] == 90 * 650 - 50 and g0["charges"] == 50 and g0["approx"] is False)\ncheck("kind L, reason, entry/exit", (g0["kind"], g0["reason"], g0["entry"], g0["exit"]) == ("L", "TP", 180.0, 270.0))\ncheck("exit 1s before IST midnight is NOT today", s["groups"][1]["today"] is False and g0["today"] is True)\ncheck("today_net / closed_today", s["today_net"] == 90 * 650 - 50 and s["closed_today"] == 1)\n\nprint("── 3. SHORT sign + LIVE book from `trades` (gross only → modelled charges)")\nlive("SCALP_V1", "NIFTY2692223600CE", "SHORT", 100.0, 60.0, "TP", DAY0 + 50, DAY0 + 500)\npaper("SCALP_V1", "NIFTY2692223200PE", "SHORT", 100.0, 130.0, "SL", DAY0 + 60, DAY0 + 600)\ns = R.get_closed_recent("SCALP_V1")\nlv = next(g for g in s["groups"] if g["book"] == "LIVE")\npp = next(g for g in s["groups"] if g["book"] == "PAPER")\ncheck("live short: gross = (entry-exit)*qty", lv["gross"] == 40 * 65 and lv["kind"] == "S", str(lv["gross"]))\ncheck("live charges modelled > 0, net = gross - charges, exact", lv["charges"] > 0 and abs(lv["net"] - (lv["gross"] - lv["charges"])) < 0.01 and not lv["approx"])\ncheck("paper short loss is negative", pp["net"] == -30 * 650 - 50)\ncheck("both books merged into one today_net", abs(s["today_net"] - (lv["net"] + pp["net"])) < 0.01)\n\nprint("── 4. paper_trades rows with trade_mode=LIVE (BRK/ORB/TSG live) tagged LIVE")\npaper("BRK_V1", "NIFTY2692223400PE", "LONG", 180.0, 200.0, "TP", DAY0 + 10, DAY0 + 20, mode="LIVE")\ncheck("book = LIVE", R.get_closed_recent("BRK_V1")["groups"][0]["book"] == "LIVE")\n\nprint("── 5. TSG basket: legs cluster by entry time; closed only when ALL legs are")\nT0 = DAY0 - 86400 + 33360                      # yesterday 09:16 IST\nfor cls, sym, d, ent, ext in (("L1", "NIFTY2692223600CE", "SHORT", 85.0, 40.0), ("L2", "NIFTY2692223100PE", "SHORT", 85.0, 50.0),\n                              ("L3", "NIFTY2692224000CE", "LONG", 5.0, 1.0), ("L4", "NIFTY2692222700PE", "LONG", 5.0, 2.0)):\n    paper("TSG_V1", sym, d, ent, ext, "EOD", T0 + (0 if d == "LONG" else 20), T0 + 22200, cls=cls)\nT1 = DAY0 + 33360                              # today 09:16 IST (may be in the future — irrelevant to grouping)\npaper("TSG_V1", "NIFTY2692223650CE", "SHORT", 80.0, 90.0, "MTM_SL", T1, T1 + 3000, cls="L1")\npaper("TSG_V1", "NIFTY2692223050PE", "SHORT", 80.0, None, None, T1 + 5, None, cls="L2")     # still open\ns = R.get_closed_recent("TSG_V1")\ncheck("yesterday\'s 4 legs = ONE row; today\'s half-open basket absent", len(s["groups"]) == 1 and s["groups"][0]["legs"] == 4, str(s["groups"]))\nb = s["groups"][0]\ncheck("label shorts / sub wings", b["label"] == "23600CE / 23100PE" and b["sub"] == "wings 24000CE / 22700PE", f"{b[\'label\']} | {b[\'sub\']}")\ncheck("credit 85+85-5-5=160, debit 40+50-1-2=87", b["entry"] == 160.0 and b["exit"] == 87.0, f"{b[\'entry\']} {b[\'exit\']}")\ncheck("net = (160-87)*650 - 4*50", b["net"] == 73 * 650 - 200 and b["reason"] == "EOD")\ncheck("two baskets on different days never merge", b["today"] is False)\n\nprint("── 6. SCALP_V3 through the Trades-page mappers (hedge leg, gross-only store)")\nwith sqlite3.connect(db) as c:\n    c.execute("INSERT INTO scalp_v3_trades VALUES (\'v1\',\'SCALP_V3\',1,\'NIFTY2692223400CE\',\'CE\',65,100,80,NULL,?,?,130,\'TP\',1950,\'CLOSED\')", (DAY0 + 5, DAY0 + 50))\n    c.execute("INSERT INTO scalp_v3_trades VALUES (\'v2\',\'SCALP_V3\',0,\'NIFTY2692223400PE\',\'PE\',65,100,80,\'g1\',?,?,90,\'SL\',-650,\'CLOSED\')", (DAY0 + 6, DAY0 + 60))\n    c.execute("INSERT INTO scalp_v3_trades VALUES (\'v3\',\'SCALP_V3\',1,\'NIFTY2692223300PE\',\'PE\',65,100,80,NULL,?,NULL,NULL,NULL,NULL,\'OPEN\')", (DAY0 + 7,))\ns = R.get_closed_recent("SCALP_V3")\nbk = {g["book"]: g for g in s["groups"]}\ncheck("paper + live rows, open excluded", set(bk) == {"PAPER", "LIVE"} and len(s["groups"]) == 2, str(s))\ncheck("symbol is the HEDGE; gross = realized_pnl", bk["PAPER"]["label"] == "NIFTY2692223400CE" and bk["PAPER"]["gross"] == 1950)\ncheck("mapper\'s net==gross is NOT trusted: charges modelled", bk["PAPER"]["charges"] > 0 and bk["PAPER"]["net"] < 1950)\ncheck("no mapper warnings", "warnings" not in s, str(s.get("warnings")))\n\nprint("── 7. honesty: missing exit price → approx; schema drift surfaced")\npaper("HA_V1", "NIFTY2692223400CE", "LONG", 100.0, None, "BROKER_EXIT", DAY0 + 1, DAY0 + 2)\nwith sqlite3.connect(db) as c:\n    c.execute("UPDATE paper_trades SET state=\'CLOSED\' WHERE strategy_name=\'HA_V1\'")\nh = R.get_closed_recent("HA_V1")["groups"][0]\ncheck("closed without exit price: net 0, flagged approx", h["approx"] is True and h["net"] == 0)\nlicense_state.ui_level = lambda: "standard"\ncheck("standard licence: reasons masked", all(g["reason"] == "CLOSED" for g in R.get_closed_recent("ORB_V1")["groups"]))\nlicense_state.ui_level = lambda: "admin"\ncheck("limit honoured; today_net independent of it", len(R.get_closed_recent("ORB_V1", limit=1)["groups"]) == 1\n      and R.get_closed_recent("ORB_V1", limit=1)["today_net"] == R.get_closed_recent("ORB_V1")["today_net"])\nwith sqlite3.connect(db) as c:\n    c.execute("ALTER TABLE trades RENAME COLUMN slot TO slot_x")\nr = R.get_closed_recent("SCALP_V1")\ncheck("renamed column is SURFACED; the other book still served", r.get("warnings") and len(r["groups"]) == 1, str(r.get("warnings")))\nR._db_path = lambda: os.path.join(os.path.dirname(db), "missing", "nope.db")\nr = R.get_closed_recent("ORB_V1")\ncheck("unreadable DB never raises", r["groups"] == [] and r["ok"] is False)\nR._db_path = lambda: db\n\nprint("── 8. cache: same day reuses, never across the masking boundary")\nR.CACHE_TTL_S = 60\nR._cache.clear()\na = R.get_closed_recent("ORB_V1")\npaper("ORB_V1", "NIFTY2692223450CE", "LONG", 100.0, 110.0, "TP", DAY0 + 3000, DAY0 + 3100)\ncheck("within TTL the cached read is served", len(R.get_closed_recent("ORB_V1")["groups"]) == len(a["groups"]))\nlicense_state.ui_level = lambda: "standard"\ncheck("masking applied AFTER the cache (admin read must not leak)", all(g["reason"] == "CLOSED" for g in R.get_closed_recent("ORB_V1")["groups"]))\nlicense_state.ui_level = lambda: "admin"\ncheck("...and the cached copy itself was not mutated", R.get_closed_recent("ORB_V1")["groups"][0]["reason"] == "TP")\n\nlicense_state.ui_level = _real_level\nprint()\nif FAILS:\n    print(f"  {len(FAILS)} FAILED: {FAILS}")\n    sys.exit(1)\nprint("  ALL CHECKS PASSED")\n'

WRAP_BODY = '// frontend/src/components/ClosedRecentFor.jsx\n//\n// ── CLOSED_RECENT_FLEET_20260921 ── the "Closed positions · today and recent"\n// card for every strategy whose panel does not already carry one. Mounted\n// ONCE by StrategyHost under the focused panel — nine panels get the section\n// without nine panel edits (BBPanel among them stays untouched), and a\n// strategy added later gets it with no work at all as long as it books into\n// paper_trades / trades.\n//\n// Panels that render their own (own data source, own columns):\n//   IC_V1 / IC_V2 / TMA_V1 / TMA_V2   fence CLOSED_RECENT_20260921\n//   VET_V1                            fence VET_PANEL_V2_20260921\n// Add an id here if a panel ever brings its own — otherwise it shows twice.\n//\n// Data: GET /api/closed/{id} (15 s poll, last good payload kept). Reasons are\n// masked server-side for non-admin licences; showParams is the curtain.\n\nimport { getApiBase } from "../api/base";\nimport { spacing } from "../tokens";\nimport { useEntitlements } from "../hooks/useEntitlements";       // ── UI_MASK ──\nimport ClosedRecent, { useClosedGroups } from "./ClosedRecent";\n\nconst SELF_RENDERING = new Set(["IC_V1", "IC_V2", "TMA_V1", "TMA_V2", "VET_V1"]);\n\n// Column wording per strategy; everything else is the shared default.\nconst LABELS = {\n  TSG_V1:   { entryLabel: "Credit", exitLabel: "Debit", emptyText: "No closed strangles yet." },\n  SCALP_V1: { entryLabel: "Sold @", exitLabel: "Covered @" },\n};\n\nexport default function ClosedRecentFor({ strategyId }) {\n  const own = !strategyId || SELF_RENDERING.has(strategyId);\n  const { loaded: licenseLoaded, isAdminUi } = useEntitlements();\n  // hooks run unconditionally; a null url makes the poller a no-op\n  const data = useClosedGroups(own ? null : `${getApiBase()}/api/closed/${strategyId}`);\n  if (own) return null;\n  const l = LABELS[strategyId] || {};\n  return (\n    <ClosedRecent\n      groups={data.groups}\n      todayNet={data.today_net}\n      showParams={!licenseLoaded || isAdminUi}\n      entryLabel={l.entryLabel}\n      exitLabel={l.exitLabel}\n      emptyText={data.loaded ? (l.emptyText || "No closed positions yet.") : "Loading closed positions…"}\n      style={{ marginTop: spacing.md }}\n    />\n  );\n}\n'


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
    for kind, anchor, payload, count in edits:
        n = text.count(anchor)
        if n != count:
            fail(f"{rel}: anchor x{n}, expected x{count}: {anchor[:70]!r}")
        if kind == "replace":
            text = text.replace(anchor, payload)
        elif kind == "after_line":
            i = text.index(anchor)
            j = text.find("\n", i)
            j = len(text) if j < 0 else j + 1
            text = text[:j] + ("" if text[:j].endswith("\n") else "\n") + payload + text[j:]
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

    for rel in (SERVER, HOST):
        if not os.path.exists(os.path.join(ROOT, rel)):
            fail(f"{rel} not found — pull main first")
    server_src, host_src = read(os.path.join(ROOT, SERVER)), read(os.path.join(ROOT, HOST))

    # ── idempotency: all-or-nothing ──
    have = [rel for rel, txt in ((SERVER, server_src), (HOST, host_src)) if FENCE in txt]
    have += [rel for rel in (ROUTE, WRAP) if os.path.exists(os.path.join(ROOT, rel))]
    if len(have) == 4:
        print("  SKIP   fleet closed-recent already present — nothing to do")
        return
    if have:
        fail("fence/files present in SOME targets only — mixed state:\n         "
             + "\n         ".join(have)
             + "\n         do NOT hand-place files; this script carries everything."
               "\n         undo with: git checkout -- <edited files>  and delete the new"
               "\n         files listed above, then re-run")

    # ── parent fence + prerequisites ──
    shared = os.path.join(ROOT, SHARED)
    if not os.path.exists(shared):
        fail(f"{SHARED} missing — apply_closed_recent.py ({PARENT_FENCE}) must be applied first")
    stext = read(shared)
    for needle in (PARENT_FENCE, "export function useClosedGroups(", "export default function ClosedRecent("):
        if needle not in stext:
            fail(f"{SHARED}: {needle!r} not found — not the {PARENT_FENCE} component")
    for rel in ("backend/app/api/paper_trades_routes.py", "backend/app/api/trade_history_routes.py",
                "backend/app/backtest/charges/charges_model.py", "frontend/src/hooks/useEntitlements.js"):
        if not os.path.exists(os.path.join(ROOT, rel)):
            fail(f"prerequisite file missing: {rel}")
    ptxt = read(os.path.join(ROOT, "backend/app/api/paper_trades_routes.py"))
    htxt = read(os.path.join(ROOT, "backend/app/api/trade_history_routes.py"))
    for txt, fn, rel in ((ptxt, "def _load_scalp_v3_paper(conn)", "paper_trades_routes.py"),
                         (ptxt, "def _load_scalpv5_paper(conn)", "paper_trades_routes.py"),
                         (htxt, "def _query_scalp_v3_live(from_ts, to_ts)", "trade_history_routes.py"),
                         (htxt, "def _query_scalp_v5_live(from_ts, to_ts)", "trade_history_routes.py")):
        if fn not in txt:
            fail(f"{rel}: mapper {fn!r} not found — the route reuses it (parity by construction)")
    if '"/api/closed/' in server_src:
        fail("another patch already serves /api/closed — reconcile first")

    dirty = git_dirty([SERVER, HOST])
    if dirty is None:
        print("  WARN   git status unavailable — cannot check for uncommitted drift")
    elif dirty and not a.allow_dirty:
        fail("uncommitted local edits on the targets (another session's patch?):\n         "
             + "\n         ".join(dirty) + "\n         commit/inspect first, or re-run with --allow-dirty")
    else:
        print("  OK     targets clean in git")

    # ── stage in memory ──
    staged = {}
    new_server = apply_edits(server_src, SERVER_EDITS, SERVER)
    for p in both_trees(SERVER, a.single_tree):
        if not os.path.exists(p):
            fail(f"dual-tree copy missing: {p}")
        if read(p) != server_src:
            fail(f"dual-tree drift: {p} differs from {SERVER} — sync the trees first")
        staged[p] = new_server
    for rel, body in ((ROUTE, ROUTE_BODY), (TEST, TEST_BODY)):
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                fail(f"{p} already exists — mixed state, delete it and re-run")
            staged[p] = body
    staged[os.path.join(ROOT, WRAP)] = WRAP_BODY
    staged[os.path.join(ROOT, HOST)] = apply_edits(host_src, HOST_EDITS, HOST)
    # edits are pure insertions: removing the fenced lines must give back the original
    for rel, new, old in ((SERVER, new_server, server_src), (HOST, staged[os.path.join(ROOT, HOST)], host_src)):
        back = "".join(ln for ln in new.splitlines(keepends=True) if FENCE not in ln)
        if back != old:
            fail(f"internal: {rel} edit is not insert-only")
    print(f"  OK     all anchors verified ({len(staged)} file writes staged; edits are insert-only)")

    # ── compile gates ──
    tmp = tempfile.mkdtemp(prefix="clrf_gate_")
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

    for rel, needle, n in ((SERVER, FENCE, 2), (SERVER, "app.include_router(closed_recent_router)", 1),
                           (SERVER, "app.include_router(vet_state_router)", 1),
                           (HOST, FENCE, 3), (HOST, "<ClosedRecentFor strategyId={effectiveFocus} />", 2),
                           (ROUTE, FENCE, 1), (TEST, FENCE, 1), (WRAP, FENCE, 1)):
        got = read(os.path.join(ROOT, rel)).count(needle)
        print(f"  {'OK ' if got >= n else 'BAD'}    {rel}: {needle!r} x{got} (need >= {n})")
        if got < n:
            rollback("verification failed")

    if a.skip_tests:
        print("  WARN   --skip-tests: behavioural suite NOT run")
    else:
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "backend"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, TEST)], cwd=os.path.join(ROOT, "backend"),
                           env=env, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or "ALL CHECKS PASSED" not in r.stdout:
            print(r.stdout[-3000:]); print(r.stderr[-2000:])
            rollback("behavioural suite failed")
        print(f"  OK     behavioural suite: {r.stdout.count('  PASS  ')} checks — ALL CHECKS PASSED")

    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    print()
    print("  DONE   fleet closed-recent applied (files only — nothing was restarted).")
    if ist.weekday() < 5 and (9, 0) <= (ist.hour, ist.minute) <= (15, 30):
        print("  NOTE   market session is open and the fleet has a LIVE strategy:")
        print("         rebuild + restart AFTER 15:30 IST (no-live-deploy rule).")
    print("  NEXT   ./desktop/build-scalp.sh both  → restart → Dashboard → any strategy")
    print("         curl -s localhost:<port>/api/closed/TSG_V1 | python3 -m json.tool")
    print(f"         then commit (fence {FENCE}); keep *.bak-* out of git")


if __name__ == "__main__":
    main()
