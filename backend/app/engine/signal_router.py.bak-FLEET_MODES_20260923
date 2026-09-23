# backend/app/engine/signal_router.py

from typing import Set, Tuple, Dict, List
import json
from pathlib import Path
from datetime import datetime
import threading
import traceback
import time

from app.trading.trade_state_manager import TradeStateManager
from app.event_bus.audit_logger import write_audit_log

from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.risk.strategy_max_loss_guard import (
    check_strategy_max_loss,
    resolve_execution_mode,
)
from app.event_bus.inapp_events import record_alert
from app.utils.session_utils import is_within_session


STATE_DIR = Path.home() / ".scalp-app" / "state"

# ============================================================================
# SAME-CANDLE SIGNAL ARBITRATION (SELL path only)
# ----------------------------------------------------------------------------
# When >1 selected contract fires a SELL on the SAME candle, all machines must
# deterministically pick the SAME contract, or the traded strike (and its
# entry/SL/TP and exit timing) diverges across friends. Whichever thread
# happened to win the _entry_lock race used to claim the single live slot —
# nondeterministic across machines.
#
# Fix: buffer same-candle SELL candidates that PASS the per-candidate gates,
# wait a short window, then elect the HIGHEST signal premium (entry_price, the
# closed-candle premium — identical on every machine) with the symbol string as
# a stable tie-break. Only the elected winner proceeds to the existing
# reserve -> slot -> on_sell_signal path. Every original gate is preserved; the
# arbiter only decides WHICH surviving candidate reaches those gates.
#
# This is in front of the existing _entry_lock / _trade_reserved / _resolve_slot
# machinery, which stays intact as the post-election safety gate. The 0.4s wait
# runs in a daemon thread; the tick path is never blocked.
# ============================================================================
_SIG_ARB_WINDOW_S = 0.4    # collection window from first same-candle candidate
_SIG_ARB_FIRED_MAX = 512   # bound the fired-set over a session


class SignalRouter:

    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self._last_routed: Set[Tuple[str, int]] = set()
        self._lock        = threading.Lock()
        self._entry_lock  = threading.Lock()
        self._trade_reserved: bool = False
        self._sel_read_ok: bool = True

        # ── same-candle SELL arbitration state ──
        self._sig_arb_lock      = threading.Lock()
        self._sig_arb_candle_ts = None     # candle_ts currently being collected
        self._sig_arb_buffer: List[dict] = []
        self._sig_arb_fired: Set[int] = set()   # candle_ts values already elected

    # ==================================================
    # Selection helpers
    # ==================================================

    def _load_selected_symbols(self) -> tuple[Set[str], Set[str]]:
        """
        Returns (ce_set, pe_set). With atomic writes on the writer side, a
        partial read should never happen. If a read DOES fail, we set
        self._sel_read_ok = False so _common_gates skips the selection filter
        for this signal — a transient read error must never drop a genuinely-
        selected strike (that previously caused spurious CE_NOT_SELECTED /
        PE_NOT_SELECTED drops). Better to let it through (the slot/paper gate
        still bounds risk) than to reject a selected strike.
        """
        ce_set: Set[str] = set()
        pe_set: Set[str] = set()

        ce_file = STATE_DIR / f"{self.strategy_id}_selected_ce.json"
        pe_file = STATE_DIR / f"{self.strategy_id}_selected_pe.json"

        ce_ok = True
        pe_ok = True

        try:
            if ce_file.exists():
                raw = ce_file.read_text().strip()
                if raw:
                    for row in json.loads(raw):
                        sym = row.get("symbol") or row.get("tradingsymbol")
                        if sym:
                            ce_set.add(sym)
        except Exception as e:
            ce_ok = False
            write_audit_log(f"[ROUTER][WARN] {ce_file.name} READ_ERR={e!r}")

        try:
            if pe_file.exists():
                raw = pe_file.read_text().strip()
                if raw:
                    for row in json.loads(raw):
                        sym = row.get("symbol") or row.get("tradingsymbol")
                        if sym:
                            pe_set.add(sym)
        except Exception as e:
            pe_ok = False
            write_audit_log(f"[ROUTER][WARN] {pe_file.name} READ_ERR={e!r}")

        # Stash read-health so _common_gates can skip the filter on a bad read
        self._sel_read_ok = ce_ok and pe_ok
        return ce_set, pe_set

    # ==================================================
    # Mode resolution (AUTHORITATIVE — fail-closed-to-PAPER)
    # ==================================================

    def _resolve_mode(self, symbol: str, candle_ts: int) -> str:
        """
        Resolve PAPER vs LIVE for THIS entry via the hardened resolver. Returns
        "LIVE" only on a clean, explicit LIVE config; everything else → "PAPER".

        If the strategy is CONFIGURED for live but the read was degraded (so we
        defensively dropped to paper), fire a LOUD in-app + Telegram alert. A
        live strategy silently going to paper means missed live trades — the
        user must know instantly. (The opposite, the old behaviour, silently
        went LIVE and punched real orders; that is the failure we are removing.)
        """
        mode, degraded = resolve_execution_mode(self.strategy_id)

        if degraded:
            write_audit_log(
                f"[ROUTER][{self.strategy_id}] MODE_DEGRADED_TO_PAPER {symbol} "
                f"ts={candle_ts} — config indicated LIVE but could not be read "
                f"cleanly; routing PAPER this cycle (NO live order). Fix the "
                f"machine's config/disk issue."
            )
            try:
                record_alert(
                    code="MODE_DEGRADED",
                    message=(
                        f"{self.strategy_id}: execution mode could not be read "
                        f"cleanly — running in PAPER to avoid an unintended live "
                        f"order. Live trading is PAUSED until the config reads "
                        f"cleanly again. Check this machine's disk/config."
                    ),
                    severity="error",
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    mode="paper",
                )
            except Exception:
                pass
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({
                    "message": (
                        f"{self.strategy_id} execution mode UNREADABLE — running "
                        f"PAPER (live paused). No live orders will be placed until "
                        f"the config reads cleanly. Check the machine."
                    ),
                    "severity": "warning",
                })
            except Exception:
                pass

        return mode

    # ==================================================
    # Gate helpers
    # ==================================================

    def _symbol_already_in_trade(self, symbol: str) -> bool:
        strategy_slots = TradeStateManager._REGISTRY.get(self.strategy_id, {})
        for mgr in strategy_slots.values():
            t = mgr.active_trade
            # SELL_PLACED added for SCALP_V1 short trades
            if t and t.symbol == symbol and t.state in (
                "BUY_PLACED", "SELL_PLACED", "PROTECTED"
            ):
                return True
        return False

    def _any_slot_busy(self) -> bool:
        strategy_slots = TradeStateManager._REGISTRY.get(self.strategy_id, {})
        for mgr in strategy_slots.values():
            if mgr.in_trade or mgr.selection_locked:
                return True
        return False

    def _any_paper_trade_open(self) -> bool:
        try:
            from app.db.sqlite import get_conn
            conn = get_conn()
            row = conn.execute(
                "SELECT paper_trade_id, symbol FROM paper_trades "
                "WHERE strategy_name = ? "
                "AND state = 'OPEN' AND exit_price IS NULL "   # both must agree
                "LIMIT 1",
                (self.strategy_id,),
            ).fetchone()
            if row is not None:
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] PAPER_TRADE_OPEN "
                    f"holder={row['symbol']} id={row['paper_trade_id']}"
                )
                return True
            return False
        except Exception as e:
            write_audit_log(
                f"[ROUTER][WARN] _any_paper_trade_open ERR={e!r} "
                f"strategy={self.strategy_id} — treating as busy (DROPPING signal)"
            )
            return True

    # ==================================================
    # Shared pre-check + dedup + selection filter
    # Returns True if signal should proceed, False to drop.
    # Adds key to _last_routed on success.
    #
    # INVARIANT: every path that is not an explicit drop MUST fall through to
    # the final `return True`. Do not indent that return into any branch.
    # ==================================================

    def _common_gates(self, symbol: str, token: int, candle_ts: int, key: tuple) -> bool:
        cfg = load_strategy_config(self.strategy_id)

        if not load_global_config().get("trade_on", False):
            write_audit_log(
                f"[ROUTER][{self.strategy_id}] trade_on=FALSE → EXIT "
                f"{symbol} ts={candle_ts}"
            )
            return False

        if check_strategy_max_loss(self.strategy_id):
            write_audit_log(
                f"[ROUTER][{self.strategy_id}] MAX_LOSS_HIT → EXIT "
                f"{symbol} ts={candle_ts}"
            )
            return False

        session_cfg = cfg.get("session")
        if session_cfg:
            primary = session_cfg.get("primary")
            if primary:
                if not is_within_session(
                    datetime.now(),
                    primary.get("start"),
                    primary.get("end"),
                ):
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] OUTSIDE_SESSION → EXIT "
                        f"{symbol} ts={candle_ts} "
                        f"window={primary.get('start')}–{primary.get('end')}"
                    )
                    return False

        with self._lock:
            if key in self._last_routed:
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] DUPLICATE_SIGNAL → EXIT "
                    f"key={key}"
                )
                return False
            self._last_routed.add(key)

        ce_selected, pe_selected = self._load_selected_symbols()
        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        # If the selection files could not be read cleanly this instant, do NOT
        # apply the selection filter — a transient read error must never drop a
        # genuinely-selected strike. With atomic writes this should be rare.
        if not getattr(self, "_sel_read_ok", True):
            write_audit_log(
                f"[ROUTER][{self.strategy_id}] SELECTION_READ_DEGRADED — "
                f"skipping selection filter for {symbol} ts={candle_ts}"
            )
        elif ce_selected or pe_selected:
            if is_ce and symbol not in ce_selected:
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] CE_NOT_SELECTED → EXIT "
                    f"{symbol} ts={candle_ts}"
                )
                self._safe_remove_key(key)
                return False
            if is_pe and symbol not in pe_selected:
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] PE_NOT_SELECTED → EXIT "
                    f"{symbol} ts={candle_ts}"
                )
                self._safe_remove_key(key)
                return False

        # Default success path. MUST stay at method level (not inside any
        # branch above) so every non-drop path returns True.
        return True

    # ==================================================
    # route_buy_signal — PRESERVED (used by legacy callers;
    # SCALP_V1 now uses route_sell_signal exclusively)
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

        if not self._common_gates(symbol, token, candle_ts, key):
            return

        # AUTHORITATIVE mode resolution: LIVE only on a clean explicit LIVE.
        # Everything else (PAPER / OFF / unknown / degraded read) → PAPER.
        trade_execution_mode = self._resolve_mode(symbol, candle_ts)
        slot_mgr             = None

        with self._entry_lock:

            if self._trade_reserved:
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] ENTRY_IN_PROGRESS → DROP "
                    f"{symbol} ts={candle_ts}"
                )
                self._safe_remove_key(key)
                return

            # POSITIVE CHECK: live path runs ONLY for an explicit LIVE. Any other
            # value falls through to the PAPER branch — the safe default.
            if trade_execution_mode == "LIVE":
                if self._any_slot_busy():
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] SINGLE_TRADE_GATE → DROP "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return
                if self._symbol_already_in_trade(symbol):
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] SYMBOL_IN_TRADE → DROP "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return
                slot_mgr = self._resolve_slot(symbol)
                if not slot_mgr:
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] NO_SLOT_AVAILABLE → EXIT "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return
            else:
                if self._any_paper_trade_open():
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] PAPER_TRADE_OPEN → DROP "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return

            self._trade_reserved = True

        # PAPER branch (everything that is not an explicit LIVE).
        if trade_execution_mode != "LIVE":
            try:
                from app.trading.paper_trade_recorder import PaperTradeRecorder
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] PAPER_ENTRY_WIN "
                    f"{symbol} ts={candle_ts} entry={entry_price} dir=LONG"
                )
                PaperTradeRecorder.record_entry(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    token=token,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    candle_ts=candle_ts,
                    trade_direction="LONG",
                )
            except Exception as e:
                write_audit_log(f"[PAPER][ERROR] RECORD FAILED SYMBOL={symbol} ERR={repr(e)}")
                self._safe_remove_key(key)
            finally:
                with self._entry_lock:
                    self._trade_reserved = False
            return

        write_audit_log(f"[ROUTER] ROUTE SLOT={slot_mgr.name} SYMBOL={symbol}")

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
                with self._entry_lock:
                    self._trade_reserved = False

        threading.Thread(target=_execute_buy, daemon=True).start()

    # ==================================================
    # route_sell_signal — SCALP_V1 SHORT (with same-candle arbitration)
    #
    # REFACTORED: the per-candidate gates (_common_gates) run synchronously
    # here exactly as before. A surviving candidate is then BUFFERED for its
    # candle_ts rather than entering immediately. After a short collection
    # window the highest-premium candidate is elected and the original
    # reserve -> slot -> on_sell_signal path runs for that ONE winner via
    # _execute_sell_winner(). All non-elected candidates that passed the gates
    # have their dedup key released so a later, distinct candle can re-route
    # them normally.
    #
    # The post-election reserve/slot logic is byte-for-byte the prior body,
    # moved into _execute_sell_winner. route_buy_signal is unchanged.
    # ==================================================

    def route_sell_signal(
        self,
        *,
        symbol: str,
        token: int,
        candle_ts: int,
        entry_price: float,
        sl_price: float,   # ABOVE entry — premium rising = loss
        tp_price: float,   # BELOW entry — premium falling = profit
    ):
        write_audit_log(
            f"[ROUTER][{self.strategy_id}] ENTER route_sell_signal "
            f"symbol={symbol} token={token} ts={candle_ts} "
            f"entry={entry_price} sl={sl_price} tp={tp_price}"
        )

        key = (symbol, candle_ts)

        # Per-candidate gates run UNCHANGED (trade_on, max-loss, session, dedup,
        # selection-membership). A dropped candidate never enters the buffer.
        if not self._common_gates(symbol, token, candle_ts, key):
            return

        # ── BUFFER for same-candle arbitration ──────────────────────────
        self._register_sell_candidate(
            symbol=symbol, token=token, candle_ts=candle_ts,
            entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
            key=key,
        )

    # --------------------------------------------------
    # Same-candle arbitration (SELL)
    # --------------------------------------------------

    def _register_sell_candidate(self, *, symbol, token, candle_ts,
                                 entry_price, sl_price, tp_price, key):
        """
        Collect gate-passing same-candle SELL candidates; the first registrant
        for a candle_ts arms a single arbitration timer. Determinism: ranking
        key is (entry_price, symbol), both identical on every machine for the
        same closed candle.
        """
        late = False
        arm = False
        with self._sig_arb_lock:
            # Already elected for this candle → this signal missed the window.
            # DO NOT drop it. We never want to miss a trade for the sake of
            # uniformity: route it straight through to the normal entry path
            # outside the lock. The existing single-trade / slot gates decide
            # whether it actually enters (today's behaviour) — worst case it
            # enters a different strike, which is acceptable; being ignored is
            # NOT. The arbiter only REDUCES same-candle divergence; it does not
            # guarantee uniformity at the cost of a missed trade.
            if candle_ts in self._sig_arb_fired:
                late = True

            if not late:
                # New candle → reset buffer; release any keys still buffered for the
                # previous candle_ts (they lost / never elected and must be re-routable).
                if self._sig_arb_candle_ts != candle_ts:
                    if self._sig_arb_buffer:
                        for c in self._sig_arb_buffer:
                            self._safe_remove_key(c["key"])
                    self._sig_arb_candle_ts = candle_ts
                    self._sig_arb_buffer = []

                self._sig_arb_buffer.append({
                    "symbol": symbol, "token": token, "candle_ts": candle_ts,
                    "entry_price": float(entry_price),
                    "sl_price": sl_price, "tp_price": tp_price, "key": key,
                })
                if len(self._sig_arb_buffer) == 1:
                    arm = True

        # Late straggler: window already fired for this candle. Route it through
        # to the normal entry path (outside the lock) — never miss a trade.
        if late:
            write_audit_log(
                f"[ROUTER][{self.strategy_id}] SIG_ARB_LATE → ROUTE_THROUGH "
                f"{symbol} ts={candle_ts} (missed window; entering on its own gates)"
            )
            self._execute_sell_winner(
                symbol=symbol, token=token, candle_ts=candle_ts,
                entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
                key=key,
            )
            return

        if arm:
            threading.Thread(
                target=self._arbitrate_sell_after_window,
                args=(candle_ts,),
                daemon=True,
                name=f"{self.strategy_id.lower()}-sigarb-{candle_ts}",
            ).start()

    def _arbitrate_sell_after_window(self, candle_ts: int):
        """Wait the collection window, elect the highest-premium candidate, run it."""
        time.sleep(_SIG_ARB_WINDOW_S)

        with self._sig_arb_lock:
            if self._sig_arb_candle_ts != candle_ts:
                return
            if candle_ts in self._sig_arb_fired:
                return
            candidates = list(self._sig_arb_buffer)
            if not candidates:
                return
            # Mark fired BEFORE releasing the lock so stragglers are ignored.
            self._sig_arb_fired.add(candle_ts)
            if len(self._sig_arb_fired) > _SIG_ARB_FIRED_MAX:
                for old in sorted(self._sig_arb_fired)[:-(_SIG_ARB_FIRED_MAX // 2)]:
                    self._sig_arb_fired.discard(old)
            self._sig_arb_buffer = []

        # Elect: highest signal premium, symbol as stable tie-break.
        winner = max(candidates, key=lambda c: (c["entry_price"], c["symbol"]))

        # Release the dedup keys of the losers so they can re-route on a later,
        # distinct candle. The winner KEEPS its key (it is being routed now).
        for c in candidates:
            if c is not winner:
                self._safe_remove_key(c["key"])

        if len(candidates) > 1:
            losers = ", ".join(
                f"{c['symbol']}@{c['entry_price']}" for c in candidates if c is not winner
            )
            write_audit_log(
                f"[ROUTER][{self.strategy_id}] SIG_ARB ts={candle_ts} "
                f"{len(candidates)} signals → elected {winner['symbol']}"
                f"@{winner['entry_price']} (dropped: {losers})"
            )

        self._execute_sell_winner(
            symbol=winner["symbol"], token=winner["token"], candle_ts=candle_ts,
            entry_price=winner["entry_price"], sl_price=winner["sl_price"],
            tp_price=winner["tp_price"], key=winner["key"],
        )

    def _execute_sell_winner(self, *, symbol, token, candle_ts,
                             entry_price, sl_price, tp_price, key):
        """
        Post-election entry path — the prior body of route_sell_signal, moved
        here verbatim (reserve -> slot -> on_sell_signal). Runs for ONE winner.
        """
        # AUTHORITATIVE mode resolution: LIVE only on a clean explicit LIVE.
        # Everything else (PAPER / OFF / unknown / degraded read) → PAPER.
        trade_execution_mode = self._resolve_mode(symbol, candle_ts)
        slot_mgr             = None

        with self._entry_lock:

            if self._trade_reserved:
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] ENTRY_IN_PROGRESS → DROP "
                    f"{symbol} ts={candle_ts}"
                )
                self._safe_remove_key(key)
                return

            # POSITIVE CHECK: live path runs ONLY for an explicit LIVE. Any other
            # value falls through to the PAPER branch — the safe default.
            if trade_execution_mode == "LIVE":
                if self._any_slot_busy():
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] SINGLE_TRADE_GATE → DROP "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return
                if self._symbol_already_in_trade(symbol):
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] SYMBOL_IN_TRADE → DROP "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return
                slot_mgr = self._resolve_slot(symbol)
                if not slot_mgr:
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] NO_SLOT_AVAILABLE → EXIT "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return
            else:
                if self._any_paper_trade_open():
                    write_audit_log(
                        f"[ROUTER][{self.strategy_id}] PAPER_TRADE_OPEN → DROP "
                        f"{symbol} ts={candle_ts}"
                    )
                    self._safe_remove_key(key)
                    return

            self._trade_reserved = True

        # PAPER branch (everything that is not an explicit LIVE).
        if trade_execution_mode != "LIVE":
            try:
                from app.trading.paper_trade_recorder import PaperTradeRecorder
                write_audit_log(
                    f"[ROUTER][{self.strategy_id}] PAPER_ENTRY_WIN "
                    f"{symbol} ts={candle_ts} entry={entry_price} dir=SHORT"
                )
                PaperTradeRecorder.record_entry(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    token=token,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    candle_ts=candle_ts,
                    trade_direction="SHORT",
                )
            except Exception as e:
                write_audit_log(
                    f"[PAPER][ERROR] SELL RECORD FAILED SYMBOL={symbol} ERR={repr(e)}"
                )
                self._safe_remove_key(key)
            finally:
                with self._entry_lock:
                    self._trade_reserved = False
            return

        write_audit_log(f"[ROUTER] SELL ROUTE SLOT={slot_mgr.name} SYMBOL={symbol}")

        def _execute_sell():
            try:
                slot_mgr.on_sell_signal(
                    symbol=symbol,
                    token=token,
                    candle_ts=candle_ts,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                )
            except Exception as e:
                write_audit_log(
                    f"[ROUTER][FATAL] SELL THREAD FAILED "
                    f"SLOT={slot_mgr.name} SYMBOL={symbol} ERR={repr(e)}"
                )
                write_audit_log(traceback.format_exc())
                self._safe_remove_key(key)
                slot_mgr.selection_locked = False
            finally:
                with self._entry_lock:
                    self._trade_reserved = False

        threading.Thread(target=_execute_sell, daemon=True).start()

    # ==================================================
    # Helpers
    # ==================================================

    def _safe_remove_key(self, key):
        with self._lock:
            self._last_routed.discard(key)

    def _resolve_slot(self, symbol: str):
        is_ce = symbol.endswith("CE")
        is_pe = symbol.endswith("PE")

        cfg  = load_strategy_config(self.strategy_id)
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