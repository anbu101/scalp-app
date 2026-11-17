# backend/app/api/strategy_visibility_routes.py
#
# ── OFF_HIDDEN_20261117 ── (TD-009) GET /api/strategies/visibility
#
# Which strategies the UI hides because they are switched OFF. ONE answer for
# every page (Dashboard, Levels page, Trades + Analytics filters), so they can
# never disagree.
#
#   hidden = mode is OFF on a CLEAN config read
#            AND no open position (either book, carried included)
#            AND no trade today (entered-and-open or exited today)
#
# Rules decided by Anbu 15 Nov 2026:
#   * an OFF strategy that still holds a position or traded today stays
#     visible — never hide a live position;
#   * history is never hidden: "All" on Trades / Analytics still counts it
#     (the UI only groups the filter chips; this route removes no data).
#
# Fail-OPEN everywhere: a degraded config read is not OFF (resolve_execution_plan
# returns PAPER), and if the fleet feed cannot say whether a strategy is active
# nothing is hidden. Read-only; touches no engine. Not admin-gated (the
# non-admin Dashboard needs it too); returns only ids and booleans.

from __future__ import annotations

import time
from typing import Dict, Optional, Set

from fastapi import APIRouter

from app.api import fleet_today_routes as _ft
from app.risk import execution_modes as _em

router = APIRouter(tags=["strategies"])

IDS = list(_ft.FLEET_IDS)   # every strategy the fleet feed knows, LVB_V1 included


def _mode_of(sid: str) -> str:
    """Canonical mode; a degraded read is never OFF (fail-open: stays visible)."""
    try:
        mode, degraded = _em.resolve_execution_plan(sid)
        return _em.PAPER if degraded else mode
    except Exception:
        return _em.PAPER


def _activity() -> Optional[Dict[str, Set[str]]]:
    """{'open': ids with an open leg, 'today': ids with a trade today}, or None
    when the fleet feed cannot answer (then nothing is hidden)."""
    feed = _ft.get_fleet_today()
    if not feed or not feed.get("ok", False):
        return None
    out: Dict[str, Set[str]] = {"open": set(), "today": set()}
    for r in feed.get("strategies") or []:
        if r.get("error"):
            return None          # one unreadable strategy → do not trust "idle" for anyone
        if r.get("state") == "OPEN":
            out["open"].add(r["id"])
        if r.get("state") in ("OPEN", "CLOSED"):
            out["today"].add(r["id"])
    return out


@router.get("/api/strategies/visibility")
def get_strategy_visibility():
    now = int(time.time())
    out = {"ok": True, "server_ts": now, "strategies": {}, "hidden": []}
    try:
        act = _activity()
    except Exception as e:
        act = None
        out["warning"] = f"activity: {e!r}"
    if act is None:
        out["ok"] = False        # fail-open: hidden stays empty
    for sid in IDS:
        mode = _mode_of(sid)
        off = mode == _em.OFF
        open_ = bool(act and sid in act["open"])
        today = bool(act and sid in act["today"])
        hidden = bool(off and act is not None and not open_ and not today)
        out["strategies"][sid] = {"mode": mode, "off": off, "open": open_,
                                  "traded_today": today, "hidden": hidden}
        if hidden:
            out["hidden"].append(sid)
    return out
