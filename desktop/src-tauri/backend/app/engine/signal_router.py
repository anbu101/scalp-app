from typing import Set, Tuple
import json
from pathlib import Path
from datetime import datetime
import threading
import traceback

from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

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
    Routes BUY signals from StrategyEngine.

    HARD RULES:
    - Router enforces SELECTION (AUTHORITATIVE)
    - Router enforces TRADE_ON / SESSION / MAX_LOSS
    - Router enforces TRADE SIDE MODE (CE / PE / BOTH)
    - Router enforces GLOBAL SYMBOL LOCK (LIVE ONLY)
    - BUY execution is isolated and FAILURE-SAFE

    EXTENSION:
    - PAPER trading is WRITE-ONLY and NON-INTRUSIVE
    """

    def __init__(self):
        # (symbol, candle_ts)
        self._last_routed: Set[Tuple[str, int]] = set()

    # =========================
    # Selection helpers
    # =========================

    def _load_selected_symbols(self) -> tuple[Set[str], Set[str]]:
        ce_set: Set[str] = set()
        pe_set: Set[str] = set()

        try:
            if SELECTED_CE_FILE.exists():
                for row in json.loads(SELECTED_CE_FILE.read_text()):
                    sym = row.get("symbol") or row.get("tradingsymbol")
                    if sym:
                        ce_set.add(sym)
        except Exception as e:
            write_audit_log(f"[ROUTER][WARN] selected_ce.json ERR={e}")

        try:
            if SELECTED_PE_FILE.exists():
                for row in json.loads(SELECTED_PE_FILE.read_text()):
                    sym = row.get("symbol") or row.get("tradingsymbol")
                    if sym:
                        pe_set.add(sym)
        except Exception as e:
            write_audit_log(f"[ROUTER][WARN] selected_pe.json ERR={e}")

        return ce_set, pe_set

    # =========================
    # GLOBAL SYMBOL LOCK (LIVE ONLY)
    # =========================

    def _symbol_already_in_trade(self, symbol: str) -> bool:
        """
        Prevent duplicate LIVE trades on the SAME symbol
        across ALL slots.
        """
        for mgr in TradeStateManager._REGISTRY.values():
            t = mgr.active_trade
            if (
                t
                and t.symbol == symbol
                and t.state in ("BUY_PLACED", "PROTECTED")
            ):
                return True
        return False

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
        # 🔍 ENTRY TRACE
        write_audit_log(
            f"[ROUTER][DEBUG] ENTER route_buy_signal "
            f"symbol={symbol} token={token} ts={candle_ts}"
        )

        key = (symbol, candle_ts)
        cfg = load_strategy_config()

        # -------------------------
        # HARD SAFETY GATES
        # -------------------------

        if not cfg.get("trade_on", False):
            write_audit_log("[ROUTER][DEBUG] trade_on=FALSE → EXIT")
            return

        if check_max_loss():
            write_audit_log("[ROUTER][DEBUG] MAX_LOSS_HIT → EXIT")
            return

        if not is_within_session(
            datetime.now(),
            cfg["session"]["primary"]["start"],
            cfg["session"]["primary"]["end"],
        ):
            write_audit_log("[ROUTER][DEBUG] OUTSIDE_SESSION → EXIT")
            return

        # -------------------------
        # IDEMPOTENCY GUARD
        # -------------------------

        if key in self._last_routed:
            write_audit_log("[ROUTER][DEBUG] DUPLICATE_SIGNAL → EXIT")
            return

        # -------------------------
        # SELECTION GATE
        # -------------------------

        ce_selected, pe_selected = self._load_selected_symbols()

        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        if is_ce and symbol not in ce_selected:
            write_audit_log("[ROUTER][DEBUG] CE_NOT_SELECTED → EXIT")
            return

        if is_pe and symbol not in pe_selected:
            write_audit_log("[ROUTER][DEBUG] PE_NOT_SELECTED → EXIT")
            return

        # -------------------------------------------------
        # 📄 PAPER TRADING (NON-INTRUSIVE, TERMINAL)
        # -------------------------------------------------

        trade_execution_mode = cfg.get("trade_execution_mode", "LIVE")

        write_audit_log(
            f"[ROUTER][DEBUG] MODE_CHECK "
            f"mode={trade_execution_mode} trade_on={cfg.get('trade_on')}"
        )

        if trade_execution_mode == "PAPER":
            try:
                from app.trading.paper_trade_recorder import PaperTradeRecorder

                write_audit_log(
                    f"[ROUTER][DEBUG] CALLING PAPER RECORDER "
                    f"symbol={symbol} entry={entry_price} sl={sl_price} tp={tp_price}"
                )

                PaperTradeRecorder.record_entry(
                    symbol=symbol,
                    token=token,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    candle_ts=candle_ts,
                )

                write_audit_log(
                    f"[ROUTER][DEBUG] PAPER RECORDER RETURNED OK symbol={symbol}"
                )

                # 🔒 PAPER MODE MUST STOP HERE
                self._last_routed.add(key)
                return

            except Exception as e:
                write_audit_log(
                    f"[PAPER][ERROR] RECORD FAILED SYMBOL={symbol} ERR={repr(e)}"
                )
                return

        # -------------------------------------------------
        # 🔒 GLOBAL SYMBOL LOCK (LIVE ONLY)
        # -------------------------------------------------

        if self._symbol_already_in_trade(symbol):
            write_audit_log(
                f"[ROUTER][SKIP] SYMBOL_ALREADY_IN_TRADE SYMBOL={symbol}"
            )
            return

        # -------------------------
        # SLOT RESOLUTION (LIVE)
        # -------------------------

        slot_mgr = self._resolve_slot(symbol)
        if not slot_mgr:
            write_audit_log("[ROUTER][DEBUG] NO_SLOT_AVAILABLE → EXIT")
            return

        write_audit_log(
            f"[ROUTER] ROUTE SLOT={slot_mgr.name} SYMBOL={symbol} ENTRY={entry_price}"
        )

        # -------------------------------------------------
        # 🔒 LATCH IDEMPOTENCY BEFORE THREAD
        # -------------------------------------------------

        self._last_routed.add(key)

        # -------------------------
        # THREAD-SAFE BUY (LIVE)
        # -------------------------

        def _execute_buy():
            try:
                write_audit_log(
                    f"[ROUTER][THREAD] BUY EXEC slot={slot_mgr.name} "
                    f"thread={threading.current_thread().name}"
                )

                slot_mgr.on_buy_signal(
                    symbol=symbol,
                    token=token,
                    candle_ts=candle_ts,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                )

            except Exception as e:
                write_audit_log(
                    f"[ROUTER][FATAL] BUY THREAD FAILED "
                    f"SLOT={slot_mgr.name} SYMBOL={symbol} ERR={repr(e)}"
                )
                write_audit_log(traceback.format_exc())

                self._last_routed.discard(key)
                slot_mgr.selection_locked = False

        threading.Thread(
            target=_execute_buy,
            daemon=True,
        ).start()

    # =========================
    # Slot Resolution (LIVE)
    # =========================

    def _resolve_slot(self, symbol: str):
        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        mode = load_strategy_config().get("trade_side_mode", "BOTH")

        if mode == "CE" and is_pe:
            return None

        if mode == "PE" and is_ce:
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
