# backend/app/api/fleet_ready.py — ── READY_PANEL_20261118 ──
#
# Data for the Dashboard's "Ready for the open" panel that the app did not
# expose yet (logins come from /zerodha/status and /api/acc2/status):
#
#   feed           the Kite websocket(s) registered in ws_registry:
#                  connected | connecting | not_started, + seconds since the
#                  last tick batch (None before the first one)
#   carried_stops  per LIVE Icarus-family instance (IC_REGISTRY) holding legs
#                  carried from an earlier session: how many carried legs,
#                  how many have a stop GTT on record, and how many of those
#                  Kite lists as ACTIVE right now (None = Kite not readable,
#                  e.g. before the morning login). Paper books place no GTTs.
#
# READ-ONLY: never places, modifies or cancels anything; one kite.get_gtts()
# call per executor at most once a minute, and only while a live carried leg
# has a GTT on record. Never raises: every part degrades to "unknown".

import time
from typing import Dict, List, Optional

GTT_CACHE_S = 60
_gtt_cache: Dict[int, tuple] = {}          # id(executor) -> (ts, list | None)


def _gtt_list(executor) -> Optional[List[dict]]:
    key = id(executor)
    hit = _gtt_cache.get(key)
    now = time.time()
    if hit and now - hit[0] < GTT_CACHE_S:
        return hit[1]
    probe = getattr(executor, "get_gtts_or_none", None)
    try:
        lst = probe() if callable(probe) else None
    except Exception:
        lst = None
    _gtt_cache[key] = (now, lst)
    return lst


def _instances():
    from app.jobs.ic_live_eod import _instances as inst   # same isolated iterator the 09:08 job uses
    return inst()


def _ws_engines():
    from app.marketdata.ws_registry import get_ws_engines
    return list(get_ws_engines())


def feed_state() -> dict:
    try:
        engines = _ws_engines()
    except Exception:
        return {"state": "unknown", "engines": 0, "last_tick_age_s": None}
    connected = [e for e in engines if getattr(e, "_connected", False)]
    last = max((float(getattr(e, "_last_tick_ts", 0) or 0) for e in engines), default=0.0)
    return {"state": "connected" if connected else ("connecting" if engines else "not_started"),
            "engines": len(engines),
            "last_tick_age_s": round(time.time() - last, 1) if last > 0 else None}


def carried_stops() -> List[dict]:
    out = []
    try:
        insts = _instances()
    except Exception:
        return out
    for sid, gm, _cfg in insts:
        try:
            if getattr(gm, "_paper", True):
                continue
            core = getattr(gm, "_core", None)
            if core is None:
                continue
            legs = [l for l in core.open_legs() if getattr(l, "carried", False)]
            if not legs:
                continue
            rts = [(getattr(gm, "_rt", {}) or {}).get(l.leg_id) or {} for l in legs]
            rts = [rt for rt in rts if not rt.get("phantom")]
            if not rts:
                continue
            ids = [[str(g) for g in (rt.get("gtt_ids") or [])] for rt in rts]
            with_stop = sum(1 for g in ids if g)
            armed = 0 if with_stop == 0 else None
            if with_stop:
                lst = _gtt_list(getattr(gm, "executor", None))
                if lst is not None:
                    active = {str(g.get("id")) for g in lst if str(g.get("status", "")).lower() == "active"}
                    armed = sum(1 for g in ids if any(x in active for x in g))
            out.append({"id": sid, "legs": len(rts), "with_stop": with_stop, "armed": armed})
        except Exception:
            out.append({"id": sid, "legs": None, "with_stop": None, "armed": None, "error": True})
    return out


def ready_payload() -> dict:
    return {"ok": True, "ts": int(time.time()), "feed": feed_state(), "carried_stops": carried_stops()}
