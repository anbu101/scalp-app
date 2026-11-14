# backend/app/engine/ic/ic_gtt_monitor.py
#
# IC (shared V1/V2) — GTT Backstop Monitor
# ============================================================================
# Port of scalp_v2_gtt_monitor doctrine to IC's multi-GTT legs (freeze
# slicing means a short can carry SEVERAL SL GTTs at the same trigger):
#
#   * NEVER mutates strategy state. Single handoff:
#     group_manager.on_backstop_leg_exit(leg_id=…, exit_price=…, reason=…) —
#     the group manager owns closing AND the MTC consequence (GT9-pinned).
#   * NEVER closes on a fetch error. Network failure → log, wait, retry.
#   * GTT race (house learning): Zerodha flips status="triggered" BEFORE
#     orders[].result is populated. Fill resolution chain:
#       (1) gtt.orders[].result.order_result.order_id → get_order_fill()
#       (2) recent broker orders scan (BUY COMPLETE on the symbol)
#       (3) after FILL_CONFIRM_RETRIES sweeps: reason=BROKER_EXIT,
#           price ← LTPStore fresh / leg.sl as the conservative stamp
#   * A leg is handed off when its FIRST GTT confirms — with slices, sibling
#     GTTs share the same trigger and fire together at the broker; state
#     closes once, the group manager's _exiting latch absorbs the rest.
#   * MISSING GTT ≠ exited. Only ALL of a leg's GTTs missing for
#     MISSING_THRESHOLD consecutive sweeps AND the broker position gone →
#     BROKER_EXIT (someone exited manually in Kite / GTT deleted).
#     Position still open with GTTs missing → CRITICAL alert (naked short!)
#     but NO state change — the human decides.
#
# ── IC_V2 (2026-07-26) ── TRIGGERED-BUT-UNFILLED ESCALATION (gap defence
#   layer 2): a Zerodha GTT, once triggered, places a LIMIT order. On a
#   violent move past the limit-buffer the limit rests off-market UNFILLED
#   and the GTT is CONSUMED — the position is open, unprotected, silent.
#   New sweep step: status=triggered + resulting order NOT COMPLETE after
#   ESCALATE_AFTER_SWEEPS → cancel the stale limit (best-effort) and hand
#   the leg to the group manager for a MARKET-OUT via the ordinary
#   escalation handoff (gm.escalate_unfilled_gtt → single close path).
#   Also NEW: ·ADJ long legs are monitored the same way (they carry sell-
#   side SL GTTs); reason inference is direction-aware.
#
# ── FILL_TRUTH_20261102 ── the FILL RESOLUTION was not direction-aware: the
#   order-book fallback took "newest COMPLETE BUY on the symbol", which for an
#   ·ADJ LONG leg is its own ENTRY. 2026-11-02 L1·ADJ 23350CE: real exit SELL
#   55.85, booked 80.15 (= entry) → ₹0 instead of −₹1,579.50. Fill resolution
#   now goes through execution/fill_pick.pick_exit_fill: exit side from the
#   leg's direction, at/after the leg's entry, never an order id that is any
#   leg's entry or another leg's exit, and ONLY the GTT's own order ids when
#   the GTT exposes them. The confirmed order id is passed to the manager
#   (stored as the trade's exit_order_id). A fill far from every trigger is
#   alerted (it is booked — the broker fill is the truth — but a human looks).
# ============================================================================

import threading
import time
from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.marketdata.ltp_store import LTPStore


# ── FILL_TRUTH_20261102 BEGIN ── shared exit-fill rule (app/execution/fill_pick.py).
# Loaded by path when the package is stubbed (standalone test harnesses).
def _fill_pick():
    global _FP
    try:
        return _FP
    except NameError:
        pass
    try:
        from app.execution import fill_pick as fp
    except Exception:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_fill_truth_fill_pick",
            Path(__file__).resolve().parents[2] / "execution" / "fill_pick.py")
        fp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fp)
    _FP = fp
    return fp
# ── FILL_TRUTH_20261102 END ──

# ── BROKER_TRUTH_20261114 BEGIN ── shared broker-truth rules (strict reads,
# session gate, live ledger). Loaded by path when the package is stubbed.
def _bt():
    global _BT
    try:
        return _BT
    except NameError:
        pass
    try:
        from app.execution import broker_truth as bt
    except Exception:
        import importlib.util
        import sys as _sys
        from pathlib import Path
        bt = _sys.modules.get("_broker_truth")
        if bt is None:
            spec = importlib.util.spec_from_file_location(
                "_broker_truth",
                Path(__file__).resolve().parents[2] / "execution" / "broker_truth.py")
            bt = importlib.util.module_from_spec(spec)
            _sys.modules["_broker_truth"] = bt
            spec.loader.exec_module(bt)
    _BT = bt
    return bt
# ── BROKER_TRUTH_20261114 END ──


class ICGTTMonitor:

    # ── BROKER_TRUTH_20261114 ── WHAT CHANGED AND WHY
    # 13 Nov 07:43 (app start, before the day's Kite login) and 14 Nov 05:00
    # (overnight session flush): get_gtts() and get_open_positions() both
    # answered [] because they could not READ. Three sweeps later this
    # monitor concluded "GTTs gone AND position gone" and booked BROKER_EXIT
    # on live carried shorts that were still open at the broker; the 09:16
    # square-off then had nothing to close. (Until 12 Nov a second bug hid
    # this one: the pre-market teardown wrongly "confirmed" every GTT gone
    # within seconds of start-up and emptied the ids this monitor watches.)
    #   * the GTT list and the positions are STRICT reads: unreadable = do
    #     nothing this sweep, never "empty";
    #   * outside the derivatives session (09:15–15:40, trading days) the
    #     monitor infers nothing — a GTT cannot fire and nobody can exit;
    #   * the position is THIS strategy's only when no other strategy holds
    #     the contract; shared or unreadable = unknown = no inference;
    #   * "GTTs gone and position gone" is booked ONLY with the exit order
    #     that proves it. No order → alert once, change nothing.

    POLL_INTERVAL        = 20
    FILL_CONFIRM_RETRIES = 3
    MISSING_THRESHOLD    = 3
    LTP_STALENESS_SEC    = 30

    def __init__(self, executor, group_manager):
        self.executor = executor
        self.gm = group_manager
        # ── IC_SPLIT ── identity from the manager (per-instance monitor)
        self.sid = group_manager.strategy_id
        self._running = False
        self._missing = {}        # leg_id -> consecutive all-missing sweeps
        self._pending = {}        # gtt_id -> fill-confirm retry count
        self._unproven = set()    # ── BROKER_TRUTH_20261114 ── legs alerted "gone, no order"
        self._unread_logged = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True,
                         name="ICV1GTTMonitor").start()
        write_audit_log("[IC_GTT_MONITOR] started")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._sweep()
            except Exception as e:
                write_audit_log(f"[IC_GTT_MONITOR][ERROR] {repr(e)}")
            time.sleep(self.POLL_INTERVAL)

    # ------------------------------------------------------------------
    def _sweep(self):
        core = self.gm.current_group()
        if core is None or self.gm.is_paper():
            return
        watch = [l for l in core.open_legs()
                 if l.is_short or l.is_adjust]
        if not watch:
            return

        # ── BROKER_TRUTH_20261114 ── nothing can fire or fill outside the
        # session: infer NOTHING (and forget half-counted evidence).
        if not self._session_open():
            self._missing.clear()
            self._pending.clear()
            self._unproven.clear()        # a new session pages afresh
            return

        # one broker fetch per sweep. STRICT: unreadable → touch NOTHING.
        gtts = _bt().read_gtts(self.executor)
        if gtts is None:
            self._missing.clear()
            if time.time() - self._unread_logged > 300:
                self._unread_logged = time.time()
                write_audit_log(f"[IC_GTT_MONITOR][{self.sid}] GTT list unreadable "
                                f"(session / token / network) — no action, retrying")
            return
        by_id = {str(g.get("id")): g for g in gtts}

        for leg in watch:
            rt = self.gm.leg_runtime(leg.leg_id)
            if rt.get("phantom"):
                continue   # ADJ_ONLY phantom: nothing at the broker
            gids = list(rt.get("gtt_ids") or [])
            if not gids:
                continue   # unprotected leg: engine tick-poll is sole guard
            self._check_leg(leg, gids, by_id)

    def _check_leg(self, leg, gids, by_id):
        found_any = False
        for gid in gids:
            gtt = by_id.get(str(gid))
            if gtt is None:
                continue
            found_any = True
            status = gtt.get("status", "")
            if status not in ("triggered", "disabled"):
                self._pending.pop(gid, None)
                continue

            # triggered → confirm a real fill before handing off
            price, oid = self._resolve_fill(gtt, leg)   # ── FILL_TRUTH_20261102 ──
            if oid is None:
                retry = self._pending.get(gid, 0) + 1
                self._pending[gid] = retry
                write_audit_log(f"[IC_GTT_MONITOR] gtt={gid} triggered, fill "
                                f"unconfirmed retry={retry}/{self.FILL_CONFIRM_RETRIES}")
                if retry < self.FILL_CONFIRM_RETRIES:
                    continue
                # ── IC_V2 ── TRIGGERED-BUT-UNFILLED: the GTT is consumed but
                # no COMPLETE order exists → the limit is resting off-market
                # after a gap/fast move. If the position is still open at the
                # broker, ESCALATE: cancel the stale limit, market-out via the
                # single close path. Only when the position is genuinely gone
                # do we fall back to the old BROKER_EXIT stamp.
                own = self._own_qty(leg)          # ── BROKER_TRUTH_20261114 ──
                if own is None:
                    # positions unreadable, or the contract is shared with
                    # another strategy: cannot tell "filled" from "resting".
                    # Keep the count at the threshold and look again.
                    self._pending[gid] = self.FILL_CONFIRM_RETRIES - 1
                    write_audit_log(f"[IC_GTT_MONITOR] gtt={gid} triggered, fill "
                                    f"unconfirmed and {leg.symbol} position not "
                                    f"attributable — no action, retrying")
                    continue
                if self._is_open(leg, own):
                    # sliced legs: sibling GTTs share the trigger and fired
                    # together — their resting limits are the same double-
                    # fill hazard. Sweep them ALL before the market-out.
                    for gid2 in gids:
                        g2 = by_id.get(str(gid2))
                        if g2 is not None and g2.get("status", "") in ("triggered", "disabled"):
                            self._cancel_stale_gtt_order(g2)
                    self._pending.pop(gid, None)
                    write_audit_log(f"[IC_GTT_MONITOR][ESCALATE] {leg.leg_id} "
                                    f"{leg.symbol} GTT {gid} triggered but "
                                    f"UNFILLED and position open → MARKET-OUT")
                    try:
                        from app.api.telegram_api import notify_critical
                        notify_critical({"message":
                            f"{self.sid}: SL GTT on {leg.symbol} triggered but its "
                            f"limit did NOT fill (gap past buffer). "
                            f"Market-out escalation firing now.",
                            "severity": "error"})
                    except Exception:
                        pass
                    self.gm.escalate_unfilled_gtt(leg_id=leg.leg_id)
                    return
                price = price or self._price_fallback(leg)
                reason = "BROKER_EXIT"
            else:
                reason = self._infer_reason(leg, price)

            write_audit_log(f"[IC_GTT_MONITOR] CONFIRMED {leg.leg_id} "
                            f"{leg.symbol} reason={reason} price={price} order={oid}")
            self._pending.pop(gid, None)
            self._missing.pop(leg.leg_id, None)
            self._plausibility(leg, price, reason, oid)   # ── FILL_TRUTH_20261102 ──
            self._handoff(leg, price, reason, oid)
            return   # one handoff per leg; manager owns the rest

        if found_any:
            self._missing.pop(leg.leg_id, None)
            return

        # ALL of this leg's GTTs missing from a CLEAN fetch
        count = self._missing.get(leg.leg_id, 0) + 1
        self._missing[leg.leg_id] = count
        write_audit_log(f"[IC_GTT_MONITOR] {leg.leg_id} all GTTs missing "
                        f"({count}/{self.MISSING_THRESHOLD})")
        if count < self.MISSING_THRESHOLD:
            return

        own = self._own_qty(leg)                  # ── BROKER_TRUTH_20261114 ──
        if own is None:
            self._missing[leg.leg_id] = self.MISSING_THRESHOLD - 1
            write_audit_log(f"[IC_GTT_MONITOR] {leg.leg_id} GTTs missing but "
                            f"{leg.symbol} position unreadable / shared — no "
                            f"action, retrying")
            return
        if self._is_open(leg, own):
            # NAKED SHORT: protection deleted but position alive. Alert loudly,
            # change nothing — the tick poll still guards the SL level.
            write_audit_log(f"[IC_GTT_MONITOR][NAKED] {leg.symbol} position "
                            f"open with NO GTT — alerting, not closing")
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({"message":
                    f"{self.sid}: {leg.symbol} ({leg.leg_id}) has an OPEN short "
                    f"position but its SL GTT(s) are GONE from Kite. Engine "
                    f"tick-SL still active; re-create the GTT or exit manually.",
                    "severity": "error"})
            except Exception:
                pass
            self._missing.pop(leg.leg_id, None)
            return

        price, oid = self._fill_from_orders(leg)        # ── FILL_TRUTH_20261102 ──
        # ── BROKER_TRUTH_20261114 ── no order, no booking. A real manual exit
        # leaves a COMPLETE order in today's book; "gone" with no order is a
        # read we cannot trust (or a prior-day exit a human must confirm).
        if not oid or not price:
            self._missing.pop(leg.leg_id, None)
            if leg.leg_id not in self._unproven:
                self._unproven.add(leg.leg_id)
                write_audit_log(f"[IC_GTT_MONITOR][UNPROVEN] {leg.leg_id} {leg.symbol} "
                                f"GTTs and position read as gone but NO exit order "
                                f"found — NOT booked")
                try:
                    from app.api.telegram_api import notify_critical
                    notify_critical({"message":
                        f"{self.sid}: {leg.symbol} ({leg.leg_id}) reads as gone at the "
                        f"broker (no GTT, no position) but there is NO exit order for "
                        f"it today. The app has NOT booked a close. Check Kite "
                        f"Positions and GTT now.", "severity": "error"})
                except Exception:
                    pass
            return
        write_audit_log(f"[IC_GTT_MONITOR] {leg.leg_id} GTTs gone AND position "
                        f"gone → BROKER_EXIT @{price} order={oid}")
        self._missing.pop(leg.leg_id, None)
        self._unproven.discard(leg.leg_id)
        self._handoff(leg, price, "BROKER_EXIT", oid)

    # ------------------------------------------------------------------
    # fill / price resolution (multi-level, house GTT-race chain)
    # ------------------------------------------------------------------
    # ── FILL_TRUTH_20261102 BEGIN ──
    @staticmethod
    def _gtt_order_ids(gtt: dict):
        out = []
        for o in gtt.get("orders") or []:
            res = (o.get("result") or {})
            oid = ((res.get("order_result") or {}).get("order_id")) or res.get("order_id")
            if oid:
                out.append(str(oid))
        return out

    def _resolve_fill(self, gtt: dict, leg):
        """(price, order_id) of THIS leg's GTT exit, or (None, None) to retry."""
        gids = self._gtt_order_ids(gtt)
        for oid in gids:
            try:
                info = self.executor.get_order_fill(oid) or {}
                if (info.get("status") or "").upper() == "COMPLETE":
                    px = float(info.get("avg_price") or 0.0)
                    if px > 0:
                        return px, oid
            except Exception as e:
                write_audit_log(f"[IC_GTT_MONITOR][FILL_READ_ERR] {oid} {e}")
        # The GTT named its order(s): only THEY can be this exit. Not complete
        # yet → retry (the escalation path handles a resting limit).
        return self._fill_from_orders(leg, only_ids=gids or None)

    def _fill_from_orders(self, leg, only_ids=None):
        try:
            orders = self.executor.get_orders() or []
        except Exception:
            return None, None
        fp = _fill_pick()
        meta = self._leg_meta(leg)
        px, ids = fp.pick_exit_fill(
            orders, symbol=leg.symbol, exit_side=fp.exit_side_for(leg.is_short),
            not_before_ts=meta["entry_ts"], exclude_ids=meta["exclude"],
            only_ids=only_ids, want_qty=int(getattr(leg, "qty", 0) or 0) or None)
        if px is None:
            return None, None
        return float(px), ",".join(ids)

    def _leg_meta(self, leg) -> dict:
        """entry time + every order id that must never close this leg."""
        rt = {}
        try:
            rt = self.gm.leg_runtime(leg.leg_id) or {}
        except Exception:
            pass
        excl = set()
        try:
            excl = set(self.gm.known_order_ids(except_exit_of=leg.leg_id))
        except Exception:
            excl = {str(x) for x in (rt.get("order_ids") or [])}
        return {"entry_ts": rt.get("entry_ts"), "exclude": excl}

    def _handoff(self, leg, price, reason, oid):
        try:
            self.gm.on_backstop_leg_exit(leg_id=leg.leg_id, exit_price=price,
                                         reason=reason, exit_order_id=oid)
        except TypeError:          # manager without the FILL_TRUTH signature
            self.gm.on_backstop_leg_exit(leg_id=leg.leg_id, exit_price=price,
                                         reason=reason)

    def _plausibility(self, leg, price, reason, oid):
        try:
            why = _fill_pick().implausible_exit(price, entry=leg.entry_price, sl=leg.sl,
                                                tp=leg.tp, reason=reason)
        except Exception:
            why = None
        if not why:
            return
        write_audit_log(f"[IC_GTT_MONITOR][IMPLAUSIBLE_FILL] {leg.leg_id} {leg.symbol} "
                        f"{why} order={oid} — booked; VERIFY in Kite")
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"message":
                f"{self.sid}: {leg.symbol} ({leg.leg_id}) GTT exit booked at "
                f"{price} — {why}. Check Kite Orders; the broker check will "
                f"flag any P&L difference.", "severity": "warning"})
        except Exception:
            pass
    # ── FILL_TRUTH_20261102 END ──

    def _price_fallback(self, leg) -> float:
        try:
            res = LTPStore.get_with_timestamp(leg.symbol)
            if res:
                ltp, ts = res
                if ltp and ltp > 0 and (time.time() - ts) <= self.LTP_STALENESS_SEC:
                    return float(ltp)
        except Exception:
            pass
        return float(leg.sl or leg.entry_price)

    def _infer_reason(self, leg, price: Optional[float]) -> str:
        """SL-only GTTs → SL. With a TP GTT in play, attribute by proximity.
        Direction-agnostic: proximity works for shorts and ·ADJ longs."""
        if not leg.tp or not price:
            return "SL"
        return "TP" if abs(price - leg.tp) < abs(price - (leg.sl or price)) else "SL"

    def _cancel_stale_gtt_order(self, gtt: dict):
        """Best-effort cancel of the resting limit order a triggered GTT
        left behind — MUST precede the market-out (a resting BUY limit +
        a market BUY both filling = 2x qty = accidental long)."""
        for o in gtt.get("orders") or []:
            res = (o.get("result") or {})
            oid = ((res.get("order_result") or {}).get("order_id")) or res.get("order_id")
            if not oid:
                continue
            try:
                info = self.executor.get_order_fill(oid) or {}
                status = (info.get("status") or "").upper()
                if status in ("OPEN", "TRIGGER PENDING", "PENDING", "AMO REQ RECEIVED"):
                    self.executor.cancel_order(oid)
                    write_audit_log(f"[IC_GTT_MONITOR] cancelled stale GTT "
                                    f"order {oid}")
            except Exception as e:
                write_audit_log(f"[IC_GTT_MONITOR][STALE_CANCEL_ERR] {oid} {e}")

    # ── BROKER_TRUTH_20261114 BEGIN ──
    def _session_open(self) -> bool:
        return _bt().session_open()

    def _own_qty(self, leg) -> Optional[int]:
        """This strategy's net quantity on the leg's contract, or None when
        the positions are unreadable or another strategy holds it too."""
        return _bt().own_net_qty(self.executor, leg.symbol, self.sid,
                                 log=write_audit_log)

    @staticmethod
    def _is_open(leg, own: int) -> bool:
        return own < 0 if leg.is_short else own > 0

    def _position_open(self, leg) -> bool:
        """Kept for callers outside this file: unknown counts as OPEN."""
        own = self._own_qty(leg)
        return True if own is None else self._is_open(leg, own)
    # ── BROKER_TRUTH_20261114 END ──
