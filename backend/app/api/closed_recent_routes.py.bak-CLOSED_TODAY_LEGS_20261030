# backend/app/api/closed_recent_routes.py
#
# ── CLOSED_RECENT_FLEET_20260921 ── GET /api/closed/{strategy_id}
# Feeds the dashboard's "Closed positions · today and recent" card for every
# strategy whose panel does not already carry one (IC_*, TMA_*, VET_V1 have
# their own sources — fence CLOSED_RECENT_20260921 / VET_PANEL_V2_20260921).
# Read-only. Never throws. Touches no engine, no manager, no other route.
#
# PARITY BY CONSTRUCTION with the Trades page: rows come from the same
# stores, and for the private-table strategies through the SAME mappers the
# Trades / Paper-Trades pages use, so a number here can never disagree with
# the number the operator already trusts there.
#     paper_trades  (strategy_name=?)  PAPER + trade_mode='LIVE' rows
#                   SCALP_V1 / BB / HA paper, TSG, BRK, ORB, GC ...
#     trades        (strategy_id=?)    LIVE rows of SCALP_V1 / BB / HA ...
#     SCALP_V3      _load_scalp_v3_paper + _query_scalp_v3_live  (hedge leg)
#     SCALP_V5      _load_scalpv5_paper  + _query_scalp_v5_live
# A strategy added later that books into paper_trades / trades shows up with
# NO edit here.
#
# One entry per POSITION, never per leg:
#   * single-leg strategies — one row is one position;
#   * TSG_V1 — a strangle basket: legs of the same book entered within
#     BASKET_WINDOW_S of the basket's first leg (TSG rows carry no group_id);
#     listed only when EVERY leg is closed, exit time = the LAST leg's exit.
#     Entry/exit are the net credit/debit per unit, wings folded into net.
# "today" = EXIT timestamp inside the current IST day (real clock).
#
# Net P&L: a row's booked total_charges/net_pnl are used when present
# (paper_trades books them at close). Where a store keeps gross only
# (trades, scalp_v3/v5 — by design, the Trades page recomputes charges
# client-side) charges come from the backtest charges model; if that fails
# the group is flagged `approx` — never an invented number shown as exact.
# A closed row with no exit price contributes 0 and flags `approx` too.
#
# Masking: exit reasons read "CLOSED" for non-admin licences, server-side
# (Phase 2b, fails closed).

from __future__ import annotations

import re
import sqlite3
import time
from typing import Dict, List, Optional

from fastapi import APIRouter

from app.event_bus.audit_logger import write_audit_log

router = APIRouter(tags=["closed-recent"])

IST_OFF = 5 * 3600 + 30 * 60
BASKET_WINDOW_S = 600            # seconds; legs of one TSG basket fill within it
ROW_LIMIT = 500                  # newest legs read per store
CACHE_TTL_S = 8                  # panel polls every 15 s; two tabs share a read
BASKET_STRATEGIES = {"TSG_V1"}
_SID_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")
_SYM_RE = re.compile(r"^([A-Z]+)(.{5})(\d+)(CE|PE)$")
_cache: Dict[str, tuple] = {}
_warned = set()


def _day_start(now: int) -> int:
    """Epoch of 00:00 IST for the IST day containing `now`."""
    return ((int(now) + IST_OFF) // 86400) * 86400 - IST_OFF


def _db_path() -> str:
    from app.db.sqlite import DB_PATH
    return str(DB_PATH)


def _is_admin() -> bool:
    try:
        from app.license import license_state
        return license_state.ui_level() == "admin"
    except Exception:
        return False                                   # fail closed


def _short(sym: str) -> str:
    m = _SYM_RE.match(str(sym or "").replace(" ", "").upper())
    return f"{m.group(3)}{m.group(4)}" if m else str(sym or "")


def _model_charges(is_short: bool, entry: float, exit_px: float, qty: int):
    try:
        from app.backtest.charges.charges_model import (
            charges_for_long_trade, charges_for_short_trade)
        fn = charges_for_short_trade if is_short else charges_for_long_trade
        cr = fn(entry_price=float(entry), exit_price=float(exit_px), qty=int(qty))
        return float(getattr(cr, "total_charges", 0.0) or 0.0), True
    except Exception:
        return 0.0, False


def _norm(d: dict, *, book: str, rid, open_: bool) -> dict:
    """Any store's row -> one leg. gross/charges/net resolved here, once."""
    direction = str(d.get("trade_direction") or "LONG").upper()
    short = direction == "SHORT"
    ent, ext = d.get("entry_price"), d.get("exit_price")
    qty = int(d.get("qty") or 0)
    approx = False
    gross = charges = net = 0.0
    if not open_:
        if ent is None or ext is None:
            approx = True
        else:
            g = d.get("pnl_value")
            gross = float(g) if g is not None else (
                (float(ent) - float(ext)) * qty if short else (float(ext) - float(ent)) * qty)
            if d.get("total_charges") is not None and d.get("net_pnl") is not None:
                charges, net = float(d["total_charges"]), float(d["net_pnl"])
            else:
                charges, ok = _model_charges(short, ent, ext, qty)
                approx = not ok
                net = gross - charges
    return {"id": f"{book}:{rid}", "book": book, "symbol": d.get("symbol") or "",
            "short": short, "qty": qty, "lots": d.get("lots"),
            "entry": ent, "exit": ext,
            "entry_ts": int(d.get("entry_time") or 0),
            "exit_ts": (int(d["exit_time"]) if d.get("exit_time") else None),
            "reason": d.get("exit_reason"), "open": open_,
            "gross": gross, "charges": charges, "net": net, "approx": approx,
            "leg": d.get("trade_class") or d.get("slot")}


def _legs(sid: str, warnings: list) -> List[dict]:
    legs: List[dict] = []
    with sqlite3.connect(_db_path(), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute(
                    "SELECT paper_trade_id, trade_mode, symbol, trade_direction, qty, lots,"
                    " entry_price, exit_price, exit_reason, entry_time, exit_time, state,"
                    " pnl_value, total_charges, net_pnl, trade_class"
                    " FROM paper_trades WHERE strategy_name=?"
                    " ORDER BY entry_time DESC LIMIT ?", (sid, ROW_LIMIT)):
                d = dict(r)
                book = "LIVE" if str(d.get("trade_mode") or "").upper() == "LIVE" else "PAPER"
                legs.append(_norm(d, book=book, rid=d["paper_trade_id"], open_=d["state"] == "OPEN"))
        except Exception as e:
            if "no such table" not in str(e):
                warnings.append(f"paper_trades: {e!r}")
        try:
            for r in conn.execute(
                    "SELECT trade_id, slot, symbol, trade_direction, qty, entry_price,"
                    " exit_price, exit_reason, entry_time, exit_time"
                    " FROM trades WHERE strategy_id=?"
                    " ORDER BY entry_time DESC LIMIT ?", (sid, ROW_LIMIT)):
                d = dict(r)
                legs.append(_norm(d, book="LIVE", rid=d["trade_id"], open_=d["exit_time"] is None))
        except Exception as e:
            if "no such table" not in str(e):
                warnings.append(f"trades: {e!r}")
        # ── private tables: through the Trades-page mappers, never re-derived ──
        if sid in ("SCALP_V3", "SCALP_V5"):
            try:
                from app.api import paper_trades_routes as _p
                load = _p._load_scalp_v3_paper if sid == "SCALP_V3" else _p._load_scalpv5_paper
                _open, _closed = load(conn)
                for d in (_closed or [])[:ROW_LIMIT]:
                    d = dict(d)
                    d["total_charges"] = d["net_pnl"] = None      # mapper net == gross, by design
                    d.setdefault("trade_direction", "LONG")
                    legs.append(_norm(d, book="PAPER", rid=d.get("paper_trade_id"), open_=False))
            except Exception as e:
                warnings.append(f"{sid} paper mapper: {e!r}")
    if sid in ("SCALP_V3", "SCALP_V5"):
        try:
            from app.api import trade_history_routes as _h
            q = _h._query_scalp_v3_live if sid == "SCALP_V3" else _h._query_scalp_v5_live
            rows = sorted(q(None, None) or [], key=lambda t: t.get("entry_time") or 0, reverse=True)
            for d in rows[:ROW_LIMIT]:
                legs.append(_norm(dict(d), book="LIVE", rid=d.get("trade_id"),
                                  open_=d.get("state") != "CLOSED"))
        except Exception as e:
            warnings.append(f"{sid} live mapper: {e!r}")
    return legs


def _baskets(legs: List[dict]) -> List[List[dict]]:
    """Cluster by book + entry time: a new basket starts when a leg entered
    more than BASKET_WINDOW_S after the basket's FIRST leg."""
    out: List[List[dict]] = []
    for book in ("PAPER", "LIVE"):
        cur: List[dict] = []
        for l in sorted((x for x in legs if x["book"] == book), key=lambda x: x["entry_ts"]):
            if cur and l["entry_ts"] - cur[0]["entry_ts"] > BASKET_WINDOW_S:
                out.append(cur)
                cur = []
            cur.append(l)
        if cur:
            out.append(cur)
    return out


def _group(legs: List[dict], day0: int, basket: bool) -> Optional[dict]:
    if any(l["open"] or not l["exit_ts"] for l in legs):
        return None                                     # still an open position
    exit_ts = max(l["exit_ts"] for l in legs)
    approx = any(l["approx"] for l in legs)
    main = next((l for l in legs if l["short"]), legs[0]) if basket else legs[0]
    g = {"group_id": legs[0]["id"], "book": legs[0]["book"],
         "kind": "S" if main["short"] else "L",
         "legs": len(legs), "lots": main.get("lots"), "qty": main["qty"],
         "entry_ts": min(l["entry_ts"] for l in legs), "exit_ts": exit_ts,
         "gross": round(sum(l["gross"] for l in legs), 2),
         "charges": round(sum(l["charges"] for l in legs), 2),
         "net": round(sum(l["net"] for l in legs), 2),
         "approx": approx, "today": exit_ts >= day0}
    if not basket or len(legs) == 1:
        g.update({"label": main["symbol"], "sub": None, "entry": main["entry"],
                  "exit": main["exit"], "reason": main["reason"]})
        return g
    shorts = sorted((l for l in legs if l["short"]), key=lambda l: str(l["leg"]))
    longs = sorted((l for l in legs if not l["short"]), key=lambda l: str(l["leg"]))
    base = max([l["qty"] for l in (shorts or legs)] or [0])
    credit = sum((1 if l["short"] else -1) * float(l["entry"] or 0) * l["qty"] for l in legs)
    debit = sum((1 if l["short"] else -1) * float(l["exit"] or 0) * l["qty"] for l in legs)
    counts: Dict[str, int] = {}
    for l in legs:
        k = l["reason"] or "—"
        counts[k] = counts.get(k, 0) + 1
    g.update({
        "label": " / ".join(_short(l["symbol"]) for l in (shorts or legs)),
        "sub": ("wings " + " / ".join(_short(l["symbol"]) for l in longs)) if longs and shorts else None,
        "entry": round(credit / base, 2) if base else None,
        "exit": round(debit / base, 2) if base and not approx else None,
        "reason": " · ".join(k if n == len(legs) else f"{k}×{n}"
                             for k, n in sorted(counts.items(), key=lambda kv: -kv[1])),
    })
    return g


@router.get("/api/closed/{strategy_id}")
def get_closed_recent(strategy_id: str, limit: int = 12):
    sid = str(strategy_id or "").upper()
    now = int(time.time())
    day0 = _day_start(now)
    out = {"ok": True, "strategy": sid, "groups": [], "today_net": 0.0,
           "closed_today": 0, "server_ts": now}
    if not _SID_RE.match(sid):
        out["ok"] = False
        out["error"] = "bad strategy id"
        return out
    try:
        limit = max(1, min(int(limit), 50))
        hit = _cache.get(sid)
        if hit and now - hit[0] < CACHE_TTL_S and hit[1] == day0:
            groups, warns = hit[2], hit[3]
        else:
            warns: list = []
            legs = _legs(sid, warns)
            basket = sid in BASKET_STRATEGIES
            sets = _baskets(legs) if basket else [[l] for l in legs]
            if basket:
                # ROW_LIMIT can cut a book's OLDEST basket in half; half a
                # strangle with every surviving leg closed would list with
                # wrong numbers. Drop it.
                for book in ("PAPER", "LIVE"):
                    if sum(1 for l in legs if l["book"] == book) >= ROW_LIMIT:
                        first = next((b for b in sets if b[0]["book"] == book), None)
                        if first is not None:
                            sets.remove(first)
            groups = [g for g in (_group(s, day0, basket) for s in sets) if g]
            groups.sort(key=lambda g: g["exit_ts"], reverse=True)
            _cache[sid] = (now, day0, groups, warns)
        admin = _is_admin()
        out["today_net"] = round(sum(g["net"] for g in groups if g["today"]), 2)
        out["closed_today"] = sum(1 for g in groups if g["today"])
        out["groups"] = [dict(g, reason=(g["reason"] if admin else "CLOSED")) for g in groups[:limit]]
        if warns:
            out["warnings"] = warns
            if sid not in _warned:                     # once per process, not per poll
                _warned.add(sid)
                write_audit_log(f"[API][CLOSED_RECENT][{sid}][WARN] {warns}")
    except Exception as e:
        out["ok"] = False
        out["error"] = repr(e)
    return out
