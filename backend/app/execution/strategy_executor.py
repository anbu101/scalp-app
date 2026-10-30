# backend/app/execution/strategy_executor.py
#
# ── ACCT_SWITCH_20261030 ── the execution account follows Settings without a
# restart, and an open position never changes account.
#
# THE BUG (2026-10-30): IC_V2 was switched to Account 1 · Zerodha in Settings,
# but its 09:18 entry still went to Angel — ic_runtime builds the executor
# ONCE at launch and only ever replaces a None. TSG, ORB, BRK, EMC, LVB, HA,
# BB_V1/V2 and IC_V1 do the same; nothing told the user a restart was needed.
# The per-session loops (VET, TMA, TMA_V2, SCALP_V3/V5) had the mirror-image
# bug: a binding changed while a position was carried would send its EXITS
# to the new account.
#
# THE FIX: get_executor_for_strategy() returns one StrategyExecutor per
# bindable strategy. It forwards every call to the broker router it holds,
# and re-reads the binding before a call ONLY when it is safe to move:
#
#   * flat      — no open LIVE row for the strategy in any live store
#                 (trades, paper_trades LIVE rows, scalp_v3/v5, tma, tma2, vet)
#   * quiet     — no order-type call in the last STICKY_S seconds, so a
#                 multi-leg entry or an unwind can never straddle two accounts
#                 (IC books its rows only after ALL legs fill)
#
# Anything uncertain (DB locked, query error) counts as NOT flat → stay put.
# The account last used for orders is pinned to disk, so a position carried
# overnight on Angel still exits on Angel after a restart even if the binding
# was flipped in between.

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

FENCE = "ACCT_SWITCH_20261030"

STICKY_S = 600          # an entry/unwind sequence never splits across accounts
FLAT_CACHE_S = 5        # DB checks at most every 5 s per strategy
PIN_PATH = Path.home() / ".scalp-app" / "angelone" / "active_broker.json"

_ORDER_PREFIXES = ("place_", "cancel_", "modify_", "exit_", "square", "flatten")

_ACCOUNT_LABEL = {"ZERODHA": "Account 1 · Zerodha", "ANGELONE": "Account 2 · Angel One"}

# Strategy-agnostic live stores (strategy column) and per-strategy tables.
_SHARED_QUERIES = (
    "SELECT COUNT(*) FROM trades WHERE strategy_id=? AND exit_time IS NULL",
    "SELECT COUNT(*) FROM paper_trades WHERE strategy_name=? "
    "AND UPPER(COALESCE(trade_mode,''))='LIVE' AND exit_price IS NULL",
    "SELECT COUNT(*) FROM scalp_v3_trades WHERE strategy_name=? AND paper=0 AND exit_time IS NULL",
    "SELECT COUNT(*) FROM scalpv5_trades WHERE strategy_name=? AND paper=0 AND exit_time IS NULL",
)
_OWN_TABLE = {"TMA_V1": "tma_trades", "TMA_V2": "tma2_trades", "VET_V1": "vet_trades"}


def _audit(msg: str) -> None:
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(msg)
    except Exception:
        pass


def _alert(kind: str, msg: str, sid: str, severity: str = "info") -> None:
    try:
        from app.event_bus.inapp_events import record_alert
        record_alert(kind, msg, severity=severity, strategy_id=sid)
    except Exception:
        pass


def _db_path() -> str:
    try:
        from app.engine.pst.pst_common import canonical_db_path
        return str(canonical_db_path())
    except Exception:
        from app.db.sqlite import DB_PATH
        return str(DB_PATH)


def open_live_rows(strategy_id: str, db_path: Optional[str] = None) -> Optional[int]:
    """Open LIVE rows for the strategy across every live store.
    None = could not tell (caller must treat as NOT flat)."""
    path = db_path or _db_path()
    total = 0
    if not os.path.isfile(path):
        _audit(f"[ACCT_SWITCH][{strategy_id}][DB] {path} not found — treating as not flat")
        return None
    try:
        # plain connect (SELECTs only): a read-only URI open can fail on a WAL
        # database whose -shm is absent, which would freeze switching forever.
        conn = sqlite3.connect(path, timeout=5)
    except Exception as e:
        _audit(f"[ACCT_SWITCH][{strategy_id}][DB] open failed: {e!r} — treating as not flat")
        return None
    try:
        queries = [(q, (strategy_id,)) for q in _SHARED_QUERIES]
        t = _OWN_TABLE.get(strategy_id)
        if t:
            queries.append((f"SELECT COUNT(*) FROM {t} WHERE UPPER(mode)='LIVE' AND status='OPEN'", ()))
        for q, args in queries:
            try:
                total += int(conn.execute(q, args).fetchone()[0] or 0)
            except sqlite3.OperationalError as e:
                if "no such table" in str(e) or "no such column" in str(e):
                    continue            # store not present on this install
                _audit(f"[ACCT_SWITCH][{strategy_id}][DB] {e!r} — treating as not flat")
                return None
        return total
    except Exception as e:
        _audit(f"[ACCT_SWITCH][{strategy_id}][DB] {e!r} — treating as not flat")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── pin file (account last used for orders, per strategy) ───────────────
_PIN_LOCK = threading.Lock()


def read_pin(strategy_id: str, path: Path = None) -> Optional[str]:
    p = path or PIN_PATH
    try:
        return (json.loads(p.read_text()) or {}).get(strategy_id)
    except Exception:
        return None


def write_pin(strategy_id: str, broker: str, path: Path = None) -> None:
    p = path or PIN_PATH
    with _PIN_LOCK:
        try:
            try:
                data = json.loads(p.read_text()) or {}
            except Exception:
                data = {}
            if data.get(strategy_id) == broker:
                return
            data[strategy_id] = broker
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(p))
        except Exception as e:
            _audit(f"[ACCT_SWITCH][{strategy_id}][PIN] write failed: {e!r}")


class StrategyExecutor:
    """Account-aware facade over the broker ExecutionRouters for one strategy."""

    def __init__(self, strategy_id: str, *,
                 build: Callable[[str], object],
                 resolve: Callable[[], str],
                 is_open: Callable[[], Optional[int]] = None,
                 clock: Callable[[], float] = time.time,
                 pin_path: Path = None):
        self._sid = strategy_id
        self._build = build
        self._resolve = resolve
        self._is_open = is_open or (lambda: open_live_rows(strategy_id))
        self._clock = clock
        self._pin_path = pin_path
        self._routers: Dict[str, object] = {}
        self._lock = threading.RLock()
        self._last_order = 0.0
        self._flat_at = -1e18
        self._flat_val: Optional[bool] = None
        self._pending: Optional[str] = None

        want = self._safe_resolve()
        pin = read_pin(strategy_id, self._pin_path)
        n = self._is_open()
        if pin and pin != want and (n is None or n > 0):
            self._broker = pin
            self._pending = want
            _audit(f"[ACCT_SWITCH][{strategy_id}][BOOT] open position on {pin} — staying on it; "
                   f"{want} applies once flat")
        else:
            self._broker = want
        self._router(self._broker)          # build eagerly: same failure semantics as before
        _audit(f"[ACCT_SWITCH][{strategy_id}][BOOT] executor on {self._broker}")

    # ── helpers ─────────────────────────────────────────────────────────
    @property
    def broker(self) -> str:
        return self._broker

    def _safe_resolve(self) -> str:
        try:
            return str(self._resolve() or "ZERODHA").upper()
        except Exception as e:
            _audit(f"[ACCT_SWITCH][{self._sid}] binding read failed: {e!r}")
            return getattr(self, "_broker", None) or "ZERODHA"

    def _router(self, broker: str):
        r = self._routers.get(broker)
        if r is None:
            r = self._build(broker)
            self._routers[broker] = r
        return r

    def _flat(self, now: float) -> Optional[bool]:
        if now - self._flat_at < FLAT_CACHE_S:
            return self._flat_val
        n = self._is_open()
        self._flat_val = None if n is None else (n == 0)
        self._flat_at = now
        return self._flat_val

    def _select(self, order_call: bool):
        with self._lock:
            now = self._clock()
            want = self._safe_resolve()
            if want != self._broker:
                quiet = (now - self._last_order) >= STICKY_S
                if quiet and self._flat(now) is True:
                    try:
                        self._router(want)
                    except Exception as e:
                        _audit(f"[ACCT_SWITCH][{self._sid}] cannot build {want} executor "
                               f"({e!r}) — staying on {self._broker}")
                    else:
                        old, self._broker, self._pending = self._broker, want, None
                        _audit(f"[ACCT_SWITCH][{self._sid}] {old} → {want} (flat; from this call)")
                        _alert("ACCOUNT_SWITCHED",
                               f"{self._sid} now trades on {_ACCOUNT_LABEL.get(want, want)} "
                               f"(was {_ACCOUNT_LABEL.get(old, old)})", self._sid)
                elif self._pending != want:
                    self._pending = want
                    why = "an order sequence is in progress" if not quiet else "a LIVE position is open"
                    _audit(f"[ACCT_SWITCH][{self._sid}][PENDING] {want} requested but {why} on "
                           f"{self._broker} — staying until flat")
                    _alert("ACCOUNT_SWITCH_PENDING",
                           f"{self._sid}: switch to {_ACCOUNT_LABEL.get(want, want)} waits — "
                           f"open position stays on {_ACCOUNT_LABEL.get(self._broker, self._broker)} "
                           f"until it exits", self._sid)
            elif self._pending:
                self._pending = None
            if order_call:
                self._last_order = now
                self._flat_at = -1e18        # re-check after any order activity
                write_pin(self._sid, self._broker, self._pin_path)
            return self._router(self._broker)

    # ── forwarding ──────────────────────────────────────────────────────
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        attr = getattr(self._router(self._broker), name)
        if not callable(attr):
            return attr
        order_call = name.startswith(_ORDER_PREFIXES)

        def _forward(*args, **kwargs):
            return getattr(self._select(order_call), name)(*args, **kwargs)
        _forward.__name__ = name
        return _forward

    def __repr__(self):
        return f"<StrategyExecutor {self._sid} on {self._broker}>"
