# backend/app/monitor/snapshot.py — ── FLEET_MONITOR_20261127 ── (TD-110)
#
# The once-a-minute LIVE picture of this install, from the SAME feed the
# Dashboard uses (api/fleet_today_routes: get_fleet_today for the rows and
# totals, _legs_for for the legs), so a number in the portal can never
# disagree with a number on the machine. LIVE book only.
#
#   build() -> {"day", "market_open", "totals", "rows", "legs", "warnings"}
#
# `legs` are today's LIVE legs read from the trade tables (open, entered
# today, or exited today). The portal stores them by leg id, so its trade
# history comes from the database and a trade whose notify call never ran
# still shows up. Never raises: on any error returns {"error": ...}.
from __future__ import annotations

import time
from typing import Any, Dict, List

ROW_KEYS = ("id", "mode", "state", "open_legs", "closed_trades", "carried", "realised",
            "unrealised", "gross", "charges", "net", "ltp_missing", "approx",
            "marks_stale", "stop", "first_entry_ts")
TOTAL_KEYS = ("gross", "net", "realised", "unrealised", "charges", "open_legs",
              "closed_trades", "risk_to_stops", "stops_known", "approx", "marks_stale",
              "week_net", "week_trades", "month_net", "month_trades")
LEG_KEYS = ("id", "symbol", "short", "qty", "lots", "entry", "exit", "entry_ts",
            "exit_ts", "reason", "open", "gross", "charges", "net", "approx", "leg")
MAX_LEGS = 400


def _pick(d: Dict[str, Any], keys) -> Dict[str, Any]:
    return {k: d.get(k) for k in keys}


def build() -> Dict[str, Any]:
    try:
        from app.api import fleet_today_routes as F
        from app.api import closed_recent_routes as CR
        from app.config.strategy_display import codename
        now = int(time.time())
        feed = F.get_fleet_today()
        if not feed.get("ok"):
            return {"error": str(feed.get("error") or "fleet feed not ok")[:300]}
        rows = []
        for r in feed.get("strategies") or []:
            if r.get("book") != "LIVE":
                continue
            row = _pick(r, ROW_KEYS)
            row["codename"] = codename(r.get("id"))
            ev = r.get("last_event")
            row["last_event"] = ({"ts": ev.get("ts"), "kind": ev.get("kind"), "text": str(ev.get("text") or "")[:160]}
                                 if isinstance(ev, dict) else None)
            rows.append(row)
        day0 = CR._day_start(now)
        warns: List[str] = []
        legs = []
        for row in rows:
            sid = row["id"]
            try:
                for lg in F._legs_for(sid, warns):
                    if lg.get("book") != "LIVE":
                        continue
                    if not (lg.get("open") or int(lg.get("entry_ts") or 0) >= day0
                            or int(lg.get("exit_ts") or 0) >= day0):
                        continue
                    x = _pick(lg, LEG_KEYS)
                    x["strategy_id"] = sid
                    legs.append(x)
            except Exception as e:
                warns.append(f"{sid} legs: {e!r}"[:200])
        legs.sort(key=lambda x: int(x.get("entry_ts") or 0), reverse=True)
        return {"day": feed.get("day"),
                "market_open": bool((feed.get("session") or {}).get("market_open")),
                "totals": _pick((feed.get("totals") or {}).get("LIVE") or {}, TOTAL_KEYS),
                "rows": rows, "legs": legs[:MAX_LEGS],
                "warnings": [str(w)[:200] for w in (list(feed.get("warnings") or []) + warns)][:20]}
    except Exception as e:
        return {"error": repr(e)[:300]}
