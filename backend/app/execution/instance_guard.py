"""instance_guard.py — INSTANCE_GUARD_20261115 (tracker O7)

Two Scalp Terminals trading ONE Zerodha account double every order, and each
sees the other's positions as foreign. On one machine the desktop app already
kills an older backend; across machines nothing stopped it.

Every app order carries a tag "ST" + <machine id, 3 chars> + time + counter
(ORDER_ONCE_20261115). Whenever the order book is read, tags with ANOTHER
machine id, placed after this app started, mean a second instance is trading
this account. Then:
  * a critical page (again every 10 minutes while it lasts);
  * NEW short entries are refused (place_sell_entry raises) — every strategy
    already unwinds an entry whose sell is refused. Exits are never blocked.
The block lifts 30 minutes after the other instance's last order.

Cost on the order path: none. The book is read where the app already reads
it; a background read every 2 minutes covers quiet stretches (market hours,
live only, never on an order path).
"""
from __future__ import annotations

import os
import random
import threading
import time
from pathlib import Path
from typing import Dict, Optional

_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TAG_LEN = 15                       # "ST" + id(3) + time(8) + counter(2)
BLOCK_S = 1800.0
REPAGE_S = 600.0
POLL_S = 120.0

STARTED = time.time()
_LOCK = threading.RLock()
_IID: Optional[str] = None
_FOREIGN: Dict[str, float] = {}    # other machine id -> its last order time
_LAST_PAGE = [0.0]
_LAST_READ = [0.0]
_POLL = {"started": False}


def _state_dir() -> Path:
    if os.environ.get("SCALP_TEST_MODE") == "1":
        import tempfile
        d = Path(tempfile.gettempdir()) / f"scalp_iid_{os.getpid()}"
    else:
        d = Path.home() / ".scalp-app" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def iid() -> str:
    """This machine's 3-character id; kept across restarts (a restart is the
    same instance, its earlier orders are its own)."""
    global _IID
    with _LOCK:
        if _IID:
            return _IID
        env = os.environ.get("SCALP_INSTANCE_ID")
        if env and len(env) == 3 and env.isalnum():
            _IID = env.upper()
            return _IID
        p = _state_dir() / "instance_id"
        try:
            v = p.read_text().strip().upper()
            if len(v) == 3 and v.isalnum():
                _IID = v
                return _IID
        except Exception:
            pass
        _IID = "".join(random.choice(_DIGITS) for _ in range(3))
        try:
            p.write_text(_IID)
        except Exception:
            pass
        return _IID


def tag_prefix() -> str:
    return "ST" + iid()


def _order_ts(o) -> Optional[float]:
    try:
        from app.execution import fill_pick as _fp
        t = _fp.order_ts(o)
        return float(t) if t is not None else None
    except Exception:
        return None


def observe(orders, now: Optional[float] = None) -> None:
    """Scan an order-book read. Never raises, never calls the broker."""
    try:
        now = float(now if now is not None else time.time())
        _LAST_READ[0] = now
        me = iid()
        hit = None
        for o in orders or []:
            tag = str(o.get("tag") or "")
            if len(tag) != TAG_LEN or not tag.startswith("ST") or not tag.isalnum():
                continue
            other = tag[2:5].upper()
            if other == me:
                continue
            ts = _order_ts(o)
            if ts is not None and ts < STARTED - 5:
                continue                   # placed before this app started (e.g. the old machine)
            ts = ts if ts is not None else now
            with _LOCK:
                if ts > _FOREIGN.get(other, 0.0):
                    _FOREIGN[other] = ts
            hit = other
        if hit:
            _maybe_page(now)
    except Exception:
        pass


def blocked(now: Optional[float] = None) -> Optional[str]:
    """Why new entries are refused, or None."""
    now = float(now if now is not None else time.time())
    with _LOCK:
        live = sorted(k for k, t in _FOREIGN.items() if now - t < BLOCK_S)
    if not live:
        return None
    return (f"ANOTHER SCALP TERMINAL ({', '.join(live)}) is placing orders on this "
            f"Zerodha account — new entries refused on this one ({iid()}); exits continue")


def _maybe_page(now: float) -> None:
    msg = blocked(now)
    if not msg:
        return
    with _LOCK:
        if now - _LAST_PAGE[0] < REPAGE_S:
            return
        _LAST_PAGE[0] = now
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(f"[INSTANCE_GUARD] {msg}")
    except Exception:
        pass
    try:
        from app.api.telegram_api import notify_critical
        notify_critical({"message": f"{msg}. Two instances double every order. "
                                    f"Close the app on one machine NOW and check Kite positions.",
                         "severity": "error"})
    except Exception:
        pass


def _in_session(now: float) -> bool:
    from datetime import datetime, timedelta, timezone
    t = datetime.fromtimestamp(now, timezone(timedelta(hours=5, minutes=30)))
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return 9 * 60 <= m <= 15 * 60 + 35


def start_poll(read_orders) -> None:
    """Background read of the order book every 2 minutes in market hours, only
    when nothing else read it meanwhile. Started on the first live order."""
    if os.environ.get("SCALP_TEST_MODE") == "1":
        return
    with _LOCK:
        if _POLL["started"]:
            return
        _POLL["started"] = True

    def loop():
        while True:
            time.sleep(POLL_S)
            try:
                now = time.time()
                if _in_session(now) and now - _LAST_READ[0] > POLL_S - 10:
                    read_orders()          # the executor's read observes it
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="instance-guard").start()


def reset_for_tests() -> None:
    global _IID
    with _LOCK:
        _FOREIGN.clear()
        _LAST_PAGE[0] = 0.0
        _LAST_READ[0] = 0.0
        _IID = None
