from typing import Dict, Set, Tuple
import json
from pathlib import Path
from datetime import datetime

from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

from app.config.trading_config import TRADE_SIDE_MODE
from app.config.strategy_loader import load_strategy_config
from app.risk.max_loss_guard import check_max_loss
from app.utils.session_utils import is_within_session


# -------------------------
# Selection files (SOURCE OF TRUTH)
# -------------------------
STATE_DIR = Path.home() / ".scalp-app" / "state"
SELECTED_CE_FILE = STATE_DIR / "selected_ce.json"
SELECTED_PE_FILE = STATE_DIR / "selected_pe.json"


class SignalRouter:
    """
    Routes BUY signals from StrategyEngine to TradeStateManager slots.

    HARD RULES (UPDATED):
    - Router enforces SELECTION (AUTHORITATIVE)
    - Router enforces TRADE_ON / SESSION / MAX_LOSS
    - Slot availability checked ONLY AFTER selection
    - Idempotency applied ONLY AFTER successful routing
    """

    def __init__(self):
        # (symbol, candle_ts) -> routed
        self._last_routed: Set[Tuple[str, int]] = set()

    # =========================
    # Selection helpers
    # =========================

    def _load_selected_symbols(self) -> tuple[Set[str], Set[str]]:
        ce_set: Set[str] = set()
        pe_set: Set[str] = set()

        try:
            if SELECTED_CE_FILE.exists():
                data = json.loads(SELECTED_CE_FILE.read_text())
                for row in data:
                    sym = row.get("symbol") or row.get("tradingsymbol")
                    if sym:
                        ce_set.add(sym)
        except Exception as e:
            write_audit_log(
                f"[ROUTER][WARN] Failed reading selected_ce.json ERR={e}"
            )

        try:
            if SELECTED_PE_FILE.exists():
                data = json.loads(SELECTED_PE_FILE.read_text())
                for row in data:
                    sym = row.get("symbol") or row.get("tradingsymbol")
                    if sym:
                        pe_set.add(sym)
        except Exception as e:
            write_audit_log(
                f"[ROUTER][WARN] Failed reading selected_pe.json ERR={e}"
            )

        return ce_set, pe_set

    # =========================
    # Public API
    # =========================

    def route_buy_signal(
        self,
        *,
        symbol: str,
        token: int,
        candle_ts: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ):
        key = (symbol, candle_ts)

        # -------------------------
        # 🔒 HARD SAFETY GATES (NEW)
        # -------------------------

        cfg = load_strategy_config()

        if not cfg.get("trade_on", False):
            write_audit_log(
                f"[ROUTER] DROP reason=TRADE_OFF SYMBOL={symbol}"
            )
            return

        if check_max_loss():
            write_audit_log(
                f"[ROUTER] DROP reason=MAX_LOSS SYMBOL={symbol}"
            )
            return

        now = datetime.now()
        if not is_within_session(
            now,
            cfg["session"]["primary"]["start"],
            cfg["session"]["primary"]["end"],
        ):
            write_audit_log(
                f"[ROUTER] DROP reason=OUTSIDE_SESSION SYMBOL={symbol}"
            )
            return

        # -------------------------
        # Idempotency guard (SAFE)
        # -------------------------
        if key in self._last_routed:
            write_audit_log(
                f"[ROUTER] DROP reason=DUPLICATE SYMBOL={symbol}"
            )
            return

        # -------------------------
        # 🔒 SELECTION GATE (AUTHORITATIVE)
        # -------------------------
        ce_selected, pe_selected = self._load_selected_symbols()

        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        if is_ce and symbol not in ce_selected:
            write_audit_log(
                f"[ROUTER] DROP reason=NOT_SELECTED SYMBOL={symbol}"
            )
            return

        if is_pe and symbol not in pe_selected:
            write_audit_log(
                f"[ROUTER] DROP reason=NOT_SELECTED SYMBOL={symbol}"
            )
            return

        # -------------------------
        # Resolve slot
        # -------------------------
        slot_mgr = self._resolve_slot(symbol)

        if not slot_mgr:
            write_audit_log(
                f"[ROUTER] DROP reason=NO_ELIGIBLE_SLOT SYMBOL={symbol}"
            )
            return

        if slot_mgr.in_trade or slot_mgr.selection_locked:
            write_audit_log(
                f"[ROUTER] DROP reason=SLOT_BUSY "
                f"SLOT={slot_mgr.name} SYMBOL={symbol}"
            )
            return

        # -------------------------
        # Route to slot (ATOMIC)
        # -------------------------
        write_audit_log(
            f"[ROUTER] ROUTE SLOT={slot_mgr.name} SYMBOL={symbol} ENTRY={entry_price}"
        )

        slot_mgr.on_buy_signal(
            symbol=symbol,
            token=token,
            candle_ts=candle_ts,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
        )

        # -------------------------
        # ✅ Idempotency latch (AFTER success)
        # -------------------------
        self._last_routed.add(key)

    # =========================
    # Slot Resolution
    # =========================

    def _resolve_slot(self, symbol: str):
        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        # Trade side mode gate
        if TRADE_SIDE_MODE == "CE" and is_pe:
            return None

        if TRADE_SIDE_MODE == "PE" and is_ce:
            return None

        for name, mgr in TradeStateManager._REGISTRY.items():
            if is_ce and not name.startswith("CE"):
                continue
            if is_pe and not name.startswith("PE"):
                continue

            if not mgr.in_trade and not mgr.selection_locked:
                return mgr

        return None


# -------------------------
# Singleton
# -------------------------
signal_router = SignalRouter()
