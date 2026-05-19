# backend/app/engine/signal_router.py

from typing import Set, Tuple
import json
from pathlib import Path
from datetime import datetime
import threading
import traceback

from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.risk.strategy_max_loss_guard import check_strategy_max_loss
from app.utils.session_utils import is_within_session


STATE_DIR = Path.home() / ".scalp-app" / "state"


class SignalRouter:

    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self._last_routed: Set[Tuple[str, int]] = set()

        # Guards _last_routed
        self._lock = threading.Lock()

        # Serialises the entire "check → reserve → execute" sequence.
        # Both LIVE and PAPER paths use this same lock so they cannot
        # race against each other even when execution mode is mixed.
        self._entry_lock = threading.Lock()

        # Router-owned reservation flag.  Bridges the gap between
        # _entry_lock being released and the actual state mutation
        # (DB insert for PAPER, TradeStateManager flags for LIVE).
        # Always read/written under _entry_lock.
        self._trade_reserved: bool = False

    # ==================================================
    # Selection helpers
    # ==================================================

    def _load_selected_symbols(self) -> tuple[Set[str], Set[str]]:
        ce_set: Set[str] = set()
        pe_set: Set[str] = set()

        ce_file = STATE_DIR / f"{self.strategy_id}_selected_ce.json"
        pe_file = STATE_DIR / f"{self.strategy_id}_selected_pe.json"

        try:
            if ce_file.exists():
                for row in json.loads(ce_file.read_text()):
                    sym = row.get("symbol") or row.get("tradingsymbol")
                    if sym:
                        ce_set.add(sym)
        except Exception as e:
            write_audit_log(f"[ROUTER][WARN] {ce_file.name} ERR={e}")

        try:
            if pe_file.exists():
                for row in json.loads(pe_file.read_text()):
                    sym = row.get("symbol") or row.get("tradingsymbol")
                    if sym:
                        pe_set.add(sym)
        except Exception as e:
            write_audit_log(f"[ROUTER][WARN] {pe_file.name} ERR={e}")

        return ce_set, pe_set

    # ==================================================
    # Gate helpers  (all called only under _entry_lock)
    # ==================================================

    def _symbol_already_in_trade(self, symbol: str) -> bool:
        strategy_slots = TradeStateManager._REGISTRY.get(self.strategy_id, {})
        for mgr in strategy_slots.values():
            t = mgr.active_trade
            if t and t.symbol == symbol and t.state in ("BUY_PLACED", "PROTECTED"):
                return True
        return False

    def _any_slot_busy(self) -> bool:
        """
        LIVE gate: True if any TradeStateManager slot has an active or
        in-progress trade.  Checks both in_trade and selection_locked so
        the window inside on_buy_signal() (after BUY fill, before GTT) is
        also covered.
        """
        strategy_slots = TradeStateManager._REGISTRY.get(self.strategy_id, {})
        for mgr in strategy_slots.values():
            if mgr.in_trade or mgr.selection_locked:
                return True
        return False

    def _any_paper_trade_open(self) -> bool:
        """
        PAPER gate: True if any open paper trade exists for this strategy
        in the DB.  Single query — no side-specific split needed because
        the 1-trade rule is strategy-wide.

        Called under _entry_lock so the check and the subsequent insert
        (triggered after the lock is released) are logically serialised:
        no two concurrent signals can both see 'no open trade' here.
        """
        try:
            from app.db.sqlite import get_conn
            conn = get_conn()
            row = conn.execute(
                """
                SELECT 1
                FROM paper_trades
                WHERE strategy_name = ?
                  AND state = 'OPEN'
                LIMIT 1
                """,
                (self.strategy_id,),
            ).fetchone()
            return row is not None
        except Exception as e:
            # Fail safe: if we cannot check, assume busy to avoid double entry.
            write_audit_log(
                f"[ROUTER][WARN] _any_paper_trade_open query failed ERR={e} "
                f"— treating as busy (safe)"
            )
            return True

    # ==================================================
    # Public API
    # ==================================================

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
        write_audit_log(
            f"[ROUTER][{self.strategy_id}] ENTER route_buy_signal "
            f"symbol={symbol} token={token} ts={candle_ts}"
        )

        key = (symbol, candle_ts)
        cfg = load_strategy_config(self.strategy_id)

        # --------------------------------------------------
        # Fast pre-checks (no lock, cheap, read-only)
        # --------------------------------------------------
        if not load_global_config().get("trade_on", False):
            write_audit_log("[ROUTER] trade_on=FALSE → EXIT")
            return

        if check_strategy_max_loss(self.strategy_id):
            write_audit_log("[ROUTER] MAX_LOSS_HIT → EXIT")
            return

        session_cfg = cfg.get("session")
        if session_cfg:
            primary = session_cfg.get("primary")
            if primary:
                if not is_within_session(
                    datetime.now(),
                    primary.get("start"),
                    primary.get("end"),
                ):
                    write_audit_log("[ROUTER] OUTSIDE_SESSION → EXIT")
                    return

        # --------------------------------------------------
        # Dedup guard
        # --------------------------------------------------
        with self._lock:
            if key in self._last_routed:
                write_audit_log("[ROUTER] DUPLICATE_SIGNAL → EXIT")
                return
            self._last_routed.add(key)

        # --------------------------------------------------
        # Selection filter
        # --------------------------------------------------
        ce_selected, pe_selected = self._load_selected_symbols()

        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        if ce_selected or pe_selected:
            if is_ce and symbol not in ce_selected:
                write_audit_log("[ROUTER] CE_NOT_SELECTED → EXIT")
                self._safe_remove_key(key)
                return
            if is_pe and symbol not in pe_selected:
                write_audit_log("[ROUTER] PE_NOT_SELECTED → EXIT")
                self._safe_remove_key(key)
                return

        trade_execution_mode = cfg.get("trade_execution_mode", "LIVE")

        # ==================================================
        # 🔒 ATOMIC SINGLE-TRADE GATE  (LIVE + PAPER unified)
        #
        # The entire "check busy → resolve slot → reserve" sequence
        # runs under _entry_lock so it is one indivisible unit.
        #
        # _trade_reserved bridges two gaps:
        #   PAPER: between lock release and DB insert completing
        #   LIVE:  between lock release and on_buy_signal() setting
        #          selection_locked / in_trade on the slot manager
        #
        # After the reserved flag is cleared (in the finally blocks
        # below), the persistent state takes over:
        #   PAPER → open row in paper_trades table  → _any_paper_trade_open()
        #   LIVE  → in_trade=True on slot manager   → _any_slot_busy()
        # ==================================================
        slot_mgr = None   # only used by LIVE path

        with self._entry_lock:

            if self._trade_reserved:
                write_audit_log(
                    f"[ROUTER] ENTRY_IN_PROGRESS (reserved) → DROP {symbol}"
                )
                self._safe_remove_key(key)
                return

            if trade_execution_mode == "PAPER":
                if self._any_paper_trade_open():
                    write_audit_log(
                        f"[ROUTER] PAPER_TRADE_OPEN → DROP {symbol}"
                    )
                    self._safe_remove_key(key)
                    return

            else:  # LIVE
                if self._any_slot_busy():
                    write_audit_log(
                        f"[ROUTER] SINGLE_TRADE_GATE (slot busy) → DROP {symbol}"
                    )
                    self._safe_remove_key(key)
                    return

                if self._symbol_already_in_trade(symbol):
                    write_audit_log(
                        f"[ROUTER] SYMBOL_ALREADY_IN_TRADE SYMBOL={symbol}"
                    )
                    self._safe_remove_key(key)
                    return

                slot_mgr = self._resolve_slot(symbol)
                if not slot_mgr:
                    write_audit_log("[ROUTER] NO_SLOT_AVAILABLE → EXIT")
                    self._safe_remove_key(key)
                    return

            # Reserve — blocks every concurrent signal until execution
            # completes and the persistent state is written.
            self._trade_reserved = True

        # ==================================================
        # PAPER execution  (synchronous — fast DB insert)
        # ==================================================
        if trade_execution_mode == "PAPER":
            try:
                from app.trading.paper_trade_recorder import PaperTradeRecorder
                PaperTradeRecorder.record_entry(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    token=token,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    candle_ts=candle_ts,
                )
            except Exception as e:
                write_audit_log(
                    f"[PAPER][ERROR] RECORD FAILED SYMBOL={symbol} ERR={repr(e)}"
                )
                self._safe_remove_key(key)
            finally:
                # DB row is now committed (or failed).  Either way clear
                # the reservation so the correct persistent state
                # (_any_paper_trade_open) takes over for future signals.
                with self._entry_lock:
                    self._trade_reserved = False
                write_audit_log(
                    f"[ROUTER] PAPER _trade_reserved cleared SYMBOL={symbol}"
                )
            return

        # ==================================================
        # LIVE execution  (async — slow broker call)
        # ==================================================
        write_audit_log(
            f"[ROUTER] ROUTE SLOT={slot_mgr.name} SYMBOL={symbol} "
            f"reserved=True"
        )

        def _execute_buy():
            try:
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
                self._safe_remove_key(key)
                slot_mgr.selection_locked = False
            finally:
                # TradeStateManager flags (in_trade / selection_locked)
                # are now set (success) or cleared (failure).
                # Either way release the reservation so _any_slot_busy()
                # takes over as the persistent gate.
                with self._entry_lock:
                    self._trade_reserved = False
                write_audit_log(
                    f"[ROUTER] LIVE _trade_reserved cleared "
                    f"SLOT={slot_mgr.name} SYMBOL={symbol}"
                )

        threading.Thread(
            target=_execute_buy,
            daemon=True,
        ).start()

    # ==================================================
    # Helpers
    # ==================================================

    def _safe_remove_key(self, key):
        with self._lock:
            self._last_routed.discard(key)

    def _resolve_slot(self, symbol: str):
        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        cfg = load_strategy_config(self.strategy_id)
        mode = cfg.get("trade_side_mode", "BOTH")

        if mode == "CE" and is_pe:
            return None
        if mode == "PE" and is_ce:
            return None

        strategy_slots = TradeStateManager._REGISTRY.get(self.strategy_id, {})

        for name, mgr in strategy_slots.items():
            if is_ce and not name.startswith("CE"):
                continue
            if is_pe and not name.startswith("PE"):
                continue
            if not mgr.in_trade and not mgr.selection_locked:
                return mgr

        return None