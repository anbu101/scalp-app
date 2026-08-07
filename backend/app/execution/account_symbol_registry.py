# backend/app/execution/account_symbol_registry.py
# ============================================================
# ACC2 BEGIN — D8 layer 2: per-account symbol netting tripwire
#
# ALERT-ONLY. Never blocks, never touches orders. When two different
# strategies hold/attempt the same tradingsymbol on the same broker
# account, positions net at the broker and every symbol-keyed safety
# check goes blind — this registry makes that loudly visible.
#
# Wiring (W3): managers call register() after a confirmed entry and
# release() after a verified exit; recovery rebuilds from open-trade
# state at boot via rebuild(). alert_fn is injected at wiring time
# (Telegram notifier); default is audit-log only, so this module stays
# dependency-free and safe to import anywhere.
# ============================================================

import threading
from typing import Callable, Dict, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log

_ALERT_TAG = "ACC2_NETTING_RISK"


class AccountSymbolRegistry:

    def __init__(self, alert_fn: Optional[Callable[[str], None]] = None):
        self._lock = threading.Lock()
        # (broker, tradingsymbol) -> {strategy_id, ...}
        self._holders: Dict[Tuple[str, str], set] = {}
        self._alert_fn = alert_fn
        self._alerted: set = set()  # dedupe per (broker, symbol) collision

    def set_alert_fn(self, alert_fn: Callable[[str], None]) -> None:
        self._alert_fn = alert_fn

    def _alert(self, msg: str) -> None:
        write_audit_log(f"[{_ALERT_TAG}] {msg}")
        if self._alert_fn:
            try:
                self._alert_fn(f"⚠️ {_ALERT_TAG}: {msg}")
            except Exception as e:
                write_audit_log(f"[{_ALERT_TAG}][WARN] alert_fn failed ERR={e}")

    def register(self, broker: str, tradingsymbol: str,
                 strategy_id: str) -> None:
        key = (broker.upper(), tradingsymbol.upper())
        with self._lock:
            holders = self._holders.setdefault(key, set())
            holders.add(strategy_id)
            if len(holders) > 1 and key not in self._alerted:
                self._alerted.add(key)
                self._alert(
                    f"{'/'.join(sorted(holders))} hold the SAME symbol "
                    f"{key[1]} on account {key[0]} — positions are NETTED "
                    f"at the broker; GTT/exit checks for these strategies "
                    f"are unreliable until one exits.")

    def release(self, broker: str, tradingsymbol: str,
                strategy_id: str) -> None:
        key = (broker.upper(), tradingsymbol.upper())
        with self._lock:
            holders = self._holders.get(key)
            if not holders:
                return
            holders.discard(strategy_id)
            if len(holders) <= 1:
                self._alerted.discard(key)
            if not holders:
                self._holders.pop(key, None)

    def rebuild(self, entries) -> None:
        """entries: iterable of (broker, tradingsymbol, strategy_id)."""
        with self._lock:
            self._holders.clear()
            self._alerted.clear()
        for broker, sym, sid in entries:
            self.register(broker, sym, sid)

    def snapshot(self) -> Dict[str, list]:
        with self._lock:
            return {
                f"{b}:{s}": sorted(h)
                for (b, s), h in self._holders.items()
            }


# Process-wide singleton (mirrors executor_factory singleton style)
symbol_registry = AccountSymbolRegistry()

# ACC2 END