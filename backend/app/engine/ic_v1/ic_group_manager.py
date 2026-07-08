# backend/app/engine/ic_v1/ic_group_manager.py
#
# IC_V1 — Group Manager (live + paper)
# ============================================================================
# Modeled on scalp_v2_group_manager.py (single authoritative close path,
# GTT cancel-verified-before-flatten, REST-primary price resolution, backstop
# handoff ownership rule). The strategy STATE MACHINE itself (MTC, unwind
# ordering, exit-reason vocabulary) lives in ic_live_core.GroupCore — this
# class only does I/O around it. Decisions D1–D9 locked 2026-07-06.
#
# ENTRY MODEL (differs from V2 on purpose):
#   V2 enters on a WS-tick signal → background fill confirm (tick thread must
#   never block). IC_V1 enters ONCE per day at a scheduled time on its OWN
#   thread (ic_engine spawns it), so entry is SYNCHRONOUS: place a leg's
#   slices, poll fills to a cap, then the next leg. That makes D6
#   (all-or-unwind) deterministic — we always know exactly what filled.
#   Sequencing (D2): wings L3,L4 first (hedge-first margin), then shorts
#   L1,L2. Any dead/unfilled slice → unwind everything filled, shorts first.
#
# SHORT PROTECTION (D4): per-slice SL GTTs via executor.place_gtt_sl_only_short
#   (additive method, lands with wiring) — falls back to place_gtt_oco
#   direction="SHORT" when the leg has a TP. Tick path is the fast exit;
#   ic_gtt_monitor is the slow backstop (handoff via on_backstop_leg_exit).
#
# MTC (D5) — decision tree, deviations flagged:
#   Short A SL fill confirmed → core decides:
#     REPIN  → cancel_gtt_verified ALL of partner's GTTs FIRST, then place new
#              SL-only GTT(s) at partner's entry (cost stop).
#              * cancel-first is a DELIBERATE inversion of the house
#                "place-new-then-cancel" GTT rule: both GTTs here are BUY
#                orders on the SAME short — two armed BUYs on a spike would
#                fill 2× qty and leave us accidentally LONG.
#              * any cancel UNVERIFIED (still armed) → partner KEEPS its
#                ORIGINAL SL (position remains protected), CRITICAL alert to
#                delete manually. We do NOT market-out against an armed GTT
#                (double-buy risk). This is the one bounded deviation from
#                pure D5, chosen because "protected at original SL" beats
#                "possibly long 1560 units".
#              * cancels verified but new GTT placement fails → partner is now
#                UNPROTECTED → MARKET_OUT immediately (pure D5).
#     MARKET_OUT → flatten partner now (cancel-verified its GTTs first; if
#              still armed, same keep-original-SL fallback as above).
#
# FORCED EXITS (EOD / MTM / UNWIND): V2 house pattern — flatten even if a GTT
#   cancel could not be verified (being short past close is worse), with a
#   CRITICAL "delete GTT manually" alert. Shorts flatten before wings.
#
# D7 latch: persisted JSON (atomic tempfile+fsync+os.replace), set BEFORE the
#   first order of the day — a crash mid-entry must never re-enter.
# D8 margin guard: executor.get_basket_margin if present; ADVISORY-FAIL-OPEN
#   on API/absence errors (can't-compute ≠ shortfall); a CONFIRMED shortfall
#   blocks entry + alerts. Live mode only.
# D9: paper legs run this exact class — fills at resolved LTP, broker calls
#   skipped via leg.paper, MTC/unwind/EOD logic identical.
#
# ANALYTICS GROUPING (IC_GROUPING): the four legs of one condor are separate
#   trades rows (shared `trades` table, one row per leg). To let the Analytics
#   page collapse them into a single logical condor, every leg of one entry
#   shares a per-condor `group_id` (a UUID minted once per enter_day) and
#   carries its leg_id as `trade_class`. Legs close at DIFFERENT times (a short
#   can SL out mid-session while its wing rides to EOD), so a condor can have
#   both CLOSED and OPEN legs at once — the frontend keys on group_id, not on
#   uniform lifecycle. See _new_group_id() / _insert_row().
#
# ISOLATION: owns only IC_V1 state. TradeStateManager._REGISTRY untouched.
# ============================================================================

import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_loader import load_strategy_config
from app.config.global_loader import load_global_config
from app.risk.strategy_max_loss_guard import check_strategy_max_loss
from app.risk.risk_mtm_guard import is_day_blocked
from app.marketdata.ltp_store import LTPStore
from app.event_bus.inapp_events import record_alert
from app.db.trades_repo import insert_trade, close_trade
from app.db.paper_trades_repo import insert_paper_trade, close_paper_trade
from app.api.telegram_api import (
    notify_trade_entry,
    notify_sl_exit,
    notify_tp_exit,
    notify_manual_exit,
    notify_critical,
)

from app.engine.ic_v1.ic_live_core import (
    GroupCore, LegCore, slice_qty, sl_price, tp_price,
    G_OPEN, G_CLOSING, G_CLOSED, G_ABORTED,
    L_OPEN, L_CLOSED,
    MTC_REPIN, MTC_MARKET_OUT,
)
from app.engine.ic_v1.ic_selection import ICSelection

STRATEGY_ID = "IC_V1"

LTP_STALENESS_SEC = 30

# Synchronous entry fill confirmation. Protected limits at 09:18 fill in
# seconds; a leg that hasn't filled in 45s is dead for our purposes (D6).
_ENTRY_FILL_CAP_S       = 45
_ENTRY_FILL_POLL_S      = 2
_DEAD_ORDER_STATUSES    = {"REJECTED", "CANCELLED", "LAPSED"}

STATE_DIR  = Path.home() / ".scalp-app" / "state"
LATCH_PATH = STATE_DIR / "IC_V1_day_latch.json"

DEFAULT_LEGS = [
    {"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 24, "premium_max": 85,
     "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": True, "mtc_partner": "L2"},
    {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 24, "premium_max": 85,
     "sl_val": 42, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": True, "mtc_partner": "L1"},
    {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 24, "premium_max": 4,
     "sl_val": 0, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": False, "mtc_partner": None},
    {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 24, "premium_max": 4,
     "sl_val": 0, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct",
     "mtc_other_on_sl": False, "mtc_partner": None},
]


class ICGroupManager:

    def __init__(self, executor=None, ltp_resolver: Optional[Callable] = None):
        """
        executor     : ZerodhaOrderExecutor (None in pure-paper startup;
                       runtime may attach later via attach_executor()).
        ltp_resolver : callable(symbol)->float|None. REST-primary resolver
                       injected by the runtime (data kite). LTPStore-fresh is
                       the fallback inside _premium().
        """
        self.executor      = executor
        self._ltp_resolver = ltp_resolver
        self._core: Optional[GroupCore] = None
        self._rt: Dict[str, dict] = {}     # leg_id -> runtime extras
        self._paper = True
        self._mutex = threading.RLock()
        self._entry_lock = threading.Lock()
        # ── IC_GROUPING ── per-condor key shared by all four legs of the
        # current entry. Minted in _enter_day_impl, read in _insert_row.
        self._group_id: Optional[str] = None

    def attach_executor(self, executor):
        self.executor = executor

    def attach_ltp_resolver(self, fn):
        self._ltp_resolver = fn

    # ==================================================================
    # CONFIG
    # ==================================================================

    def _cfg(self) -> dict:
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception as e:
            write_audit_log(f"[IC][CFG_READ_FAIL] {e} — using safe defaults")
            return {}

    def _legs_cfg(self, cfg) -> List[dict]:
        legs = cfg.get("legs") or DEFAULT_LEGS
        return [dict(l) for l in legs]

    def _lot_size(self, cfg) -> int:
        return int(cfg.get("quantity", {}).get("lot_size", 65))

    def _freeze_qty(self, cfg) -> int:
        return int(cfg.get("freeze_qty", 1800))

    # ── IC_GROUPING ── mint one condor key per entry ──────────────────
    def _new_group_id(self) -> str:
        """One shared key for all four legs of a single condor entry. Prefixed
        + dated for human-readability in the DB / audit log; the UUID tail
        guarantees uniqueness across same-day re-entries (should D7 ever be
        relaxed to allow more than one condor per day)."""
        return f"IC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    # ==================================================================
    # D7 — persisted one-entry-per-day latch
    # ==================================================================

    def _latch_today(self) -> bool:
        try:
            if not LATCH_PATH.exists():
                return False
            d = json.loads(LATCH_PATH.read_text())
            return d.get("date") == datetime.now().strftime("%Y-%m-%d")
        except Exception as e:
            # Unreadable latch: FAIL CLOSED (assume entered). A missed day is
            # opportunity cost; a double condor is doubled live risk.
            write_audit_log(f"[IC][LATCH_READ_FAIL] {e} — assuming ENTERED")
            return True

    def _set_latch(self, mode: str):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "entered_at": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
        })
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".ic_latch_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, LATCH_PATH)
        except Exception as e:
            write_audit_log(f"[IC][LATCH_WRITE_FAIL] {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ==================================================================
    # ENTRY — called by ic_engine at entry_time on a dedicated thread
    # ==================================================================

    def enter_day(self, selection: ICSelection, *, mode: str) -> bool:
        """
        mode: "PAPER" | "LIVE" (already resolved fail-closed by the runtime
        via resolve_execution_mode; OFF never reaches here).
        Returns True if a group was opened.
        """
        if not self._entry_lock.acquire(blocking=False):
            write_audit_log("[IC][ENTRY] re-entrant call blocked")
            return False
        try:
            return self._enter_day_impl(selection, mode=mode)
        finally:
            self._entry_lock.release()

    def _enter_day_impl(self, selection: ICSelection, *, mode: str) -> bool:
        cfg = self._cfg()

        # ── gates ──────────────────────────────────────────────────────
        if self._core is not None and self._core.state not in (G_CLOSED, G_ABORTED):
            write_audit_log("[IC][ENTRY] group already active → drop")
            return False
        if self._latch_today():
            write_audit_log("[IC][ENTRY] day latch set → drop (D7)")
            return False
        if is_day_blocked(STRATEGY_ID):
            write_audit_log("[IC][ENTRY] MTM day-block → drop")
            return False
        try:
            if not load_global_config().get("trade_on", False):
                write_audit_log("[IC][ENTRY] trade_on=FALSE → drop")
                return False
        except Exception:
            return False
        try:
            if check_strategy_max_loss(STRATEGY_ID):
                write_audit_log("[IC][ENTRY] RISK_LIMIT_HIT → drop")
                return False
        except Exception:
            return False   # fail closed

        # ── selection outcome ─────────────────────────────────────────
        if not selection.ok:
            write_audit_log(f"[IC][ENTRY] NO ENTRY TODAY — {selection.skip_reason}")
            record_alert("IC_NO_ENTRY", f"IC_V1 skipped: {selection.skip_reason}",
                         severity="warning", strategy_id=STRATEGY_ID, mode=mode.lower())
            return False

        if selection.wing_absent and not cfg.get("allow_strangle_degrade", False):
            write_audit_log(
                f"[IC][ENTRY] wings absent {selection.wing_absent} and "
                f"strangle-degrade disabled → skip day (D6 policy)"
            )
            record_alert("IC_WING_ABSENT",
                         f"IC_V1 skipped: wings absent {selection.wing_absent}",
                         severity="warning", strategy_id=STRATEGY_ID, mode=mode.lower())
            return False

        legs_cfg  = self._legs_cfg(cfg)
        lot_size  = self._lot_size(cfg)
        freeze    = self._freeze_qty(cfg)
        self._paper = (mode != "LIVE")

        # ── IC_GROUPING ── mint the shared condor key BEFORE any row is
        # written, so every leg (paper or live) persists with the same value.
        self._group_id = self._new_group_id()

        # ── build core legs from picks (SL/TP off INTENDED entry = pick LTP;
        #    fill-independent, house pattern) ─────────────────────────────
        core = GroupCore()
        rt: Dict[str, dict] = {}
        entry_ts = int(time.time())

        for lc in legs_cfg:
            lid = lc["id"]
            if int(lc.get("lots", 0)) <= 0 or lid not in selection.picks:
                continue
            pick = selection.picks[lid]
            qty  = int(lc["lots"]) * lot_size
            try:
                slices = slice_qty(qty, freeze, lot_size)
            except ValueError as e:
                write_audit_log(f"[IC][ENTRY][CONFIG_FAIL] {e} → skip day")
                return False
            sl = sl_price(lc["action"], pick.ltp, float(lc.get("sl_val") or 0), lc.get("sl_mode", "pct"))
            tp = tp_price(lc["action"], pick.ltp, float(lc.get("tp_val") or 0), lc.get("tp_mode", "pct"))
            core.legs[lid] = LegCore(
                leg_id=lid, action=lc["action"], opt_type=lc["opt_type"],
                symbol=pick.symbol, qty=qty, entry_price=pick.ltp,
                sl=sl, tp=tp,
                mtc_partner=lc.get("mtc_partner"),
                wing_fallback=pick.fallback,
            )
            rt[lid] = {
                "token": selection.tokens.get(lid, 0),
                "slices": slices,
                "order_ids": [],
                "gtt_ids": [],
                "db_id": None,
                "paper": self._paper,
            }

        shorts = [l for l in core.legs.values() if l.is_short]
        if len(shorts) != 2:
            write_audit_log(f"[IC][ENTRY] expected 2 shorts, got {len(shorts)} → skip")
            return False

        # ── D8 margin guard (live only; advisory-fail-open) ────────────
        if not self._paper and not self._margin_ok(core, cfg):
            record_alert("IC_MARGIN_BLOCK", "IC_V1 entry blocked: margin shortfall",
                         severity="error", strategy_id=STRATEGY_ID, mode="live")
            return False

        # ── D7: latch BEFORE the first order ───────────────────────────
        self._set_latch(mode)

        with self._mutex:
            self._core = core
            self._rt = rt
            core.begin_entry()

        write_audit_log(
            f"[IC][ENTRY][{mode}] group_id={self._group_id} expiry={selection.expiry} "
            + " ".join(f"{l.leg_id}={l.symbol}@{l.entry_price}" for l in core.legs.values())
        )

        if self._paper:
            return self._enter_paper(entry_ts)
        return self._enter_live(entry_ts)

    # ── PAPER entry (D9: same object graph, no broker) ─────────────────
    def _enter_paper(self, entry_ts: int) -> bool:
        core = self._core
        for leg in core.legs.values():
            self._insert_row(leg, entry_ts, order_id="PAPER")
            core.leg_filled(leg.leg_id)
        self._notify_entry("paper")
        write_audit_log("[IC][ENTRY][PAPER] group OPEN")
        return True

    # ── LIVE entry: D2 wings→shorts, synchronous confirm, D6 unwind ────
    def _enter_live(self, entry_ts: int) -> bool:
        core = self._core
        order = ["L3", "L4", "L1", "L2"]          # wings first (D2)
        for lid in order:
            leg = core.legs.get(lid)
            if leg is None:
                continue
            ok = self._place_and_confirm(leg)
            if not ok:
                self._unwind_after_dead(lid)
                return False
            core.leg_filled(lid)

        # shorts protected only after ALL legs confirmed (margin benefit kept,
        # and no GTT can exist for a group we might still unwind)
        for leg in core.legs.values():
            if leg.is_short:
                self._protect_short(leg)

        self._insert_all_rows(entry_ts)
        self._notify_entry("live")
        write_audit_log("[IC][ENTRY][LIVE] group OPEN")
        return True

    def _place_and_confirm(self, leg: LegCore) -> bool:
        """Place every slice of one leg, confirm all fills synchronously.
        Patches leg.entry_price to the qty-weighted avg fill."""
        rt = self._rt[leg.leg_id]
        fills: List[tuple] = []      # (qty, avg_price)
        for chunk in rt["slices"]:
            try:
                if leg.is_short:
                    oid, limit_px, _ = self.executor.place_sell_entry(
                        symbol=leg.symbol, token=rt["token"], qty=chunk)
                else:
                    oid, limit_px, _ = self._place_wing_buy(
                        leg.symbol, rt["token"], chunk)
            except Exception as e:
                write_audit_log(f"[IC][ENTRY][{leg.leg_id}][PLACE_FAIL] {e}")
                return False
            rt["order_ids"].append(oid)

            avg = self._confirm_fill(oid)
            if avg is None:
                # dead or unfilled at cap — cancel best-effort, then unwind
                try:
                    self.executor.cancel_order(oid)
                except Exception:
                    pass
                write_audit_log(f"[IC][ENTRY][{leg.leg_id}][DEAD] order={oid}")
                return False
            fills.append((chunk, avg if avg > 0 else limit_px))

        total = sum(q for q, _ in fills)
        if total > 0:
            leg.entry_price = sum(q * p for q, p in fills) / total
        return True

    def _place_wing_buy(self, symbol, token, qty):
        """place_buy returns (order_id, avg_price, filled_qty) in the current
        executor; normalize to (order_id, provisional_price, qty)."""
        res = self.executor.place_buy(symbol, token, qty)
        oid, px = res[0], (res[1] if len(res) > 1 else 0.0)
        return oid, float(px or 0.0), qty

    def _confirm_fill(self, order_id: str) -> Optional[float]:
        """Poll get_order_fill to the cap. avg_price on COMPLETE; None on
        DEAD/timeout."""
        deadline = time.time() + _ENTRY_FILL_CAP_S
        while time.time() < deadline:
            try:
                info = self.executor.get_order_fill(order_id) or {}
            except Exception as e:
                write_audit_log(f"[IC][FILL_POLL_ERR] {order_id} {e}")
                info = {}
            status = (info.get("status") or "").upper()
            if status == "COMPLETE":
                return float(info.get("avg_price") or 0.0)
            if status in _DEAD_ORDER_STATUSES:
                return None
            time.sleep(_ENTRY_FILL_POLL_S)
        return None

    def _unwind_after_dead(self, dead_leg_id: str):
        """D6: all-or-unwind, shorts first (core supplies the order)."""
        core = self._core
        to_unwind = core.leg_entry_dead(dead_leg_id)
        write_audit_log(f"[IC][UNWIND] dead={dead_leg_id} unwinding={to_unwind}")
        for lid in to_unwind:
            leg = core.legs[lid]
            _, px = self._flatten_live(leg, reason="UNWIND", force=True)
            core.record_unwind(lid, exit_price=px if px is not None else leg.entry_price)
        record_alert("IC_UNWOUND",
                     f"IC_V1 entry failed at {dead_leg_id} — group unwound",
                     severity="error", strategy_id=STRATEGY_ID, mode="live")
        try:
            notify_critical({"message": f"IC_V1 entry FAILED at {dead_leg_id}; "
                                        f"all filled legs unwound. No entry today.",
                             "severity": "error"})
        except Exception:
            pass

    # ==================================================================
    # SHORT PROTECTION (D4)
    # ==================================================================

    def _protect_short(self, leg: LegCore):
        rt = self._rt[leg.leg_id]
        for chunk in rt["slices"]:
            gid = None
            try:
                if leg.tp and leg.tp > 0:
                    gid = self.executor.place_gtt_oco(
                        symbol=leg.symbol, qty=chunk,
                        sl_price=leg.sl, tp_price=leg.tp,
                        direction="SHORT",
                    )
                else:
                    gid = self.executor.place_gtt_sl_only_short(
                        symbol=leg.symbol, qty=chunk, sl_price=leg.sl,
                    )
            except Exception as e:
                write_audit_log(f"[IC][GTT_FAIL][{leg.leg_id}] {e} — tick monitor is sole protection")
                record_alert("IC_GTT_FAIL",
                             f"{leg.symbol} SL GTT failed — tick-monitor only",
                             severity="error", strategy_id=STRATEGY_ID,
                             symbol=leg.symbol, mode="live")
            if gid:
                rt["gtt_ids"].append(str(gid))

    # ==================================================================
    # TICK FAST PATH — called by ic_engine per tick (token, ltp)
    # ==================================================================

    def on_tick(self, token: int, ltp: float):
        core = self._core
        if core is None or core.state != G_OPEN or not ltp or ltp <= 0:
            return
        for leg in core.open_legs():
            rt = self._rt.get(leg.leg_id) or {}
            if rt.get("token") != token:
                continue
            if leg.is_short:
                if leg.sl and ltp >= leg.sl:
                    self._short_sl_path(leg, ltp)
                elif leg.tp and ltp <= leg.tp:
                    self._close_leg(leg.leg_id, reason="TP", ltp_hint=ltp)
            else:
                if leg.sl and ltp <= leg.sl:
                    self._close_leg(leg.leg_id, reason="SL", ltp_hint=ltp)
                elif leg.tp and ltp >= leg.tp:
                    self._close_leg(leg.leg_id, reason="TP", ltp_hint=ltp)
            return

    # ── short SL: single authoritative path (tick AND backstop land here) ──
    def _short_sl_path(self, leg: LegCore, ltp_hint: Optional[float],
                       already_filled_at: Optional[float] = None):
        """
        already_filled_at: set by the backstop when the broker GTT already
        filled (exit price known). Tick path flattens first.
        """
        core = self._core
        with self._mutex:
            if leg.state != L_OPEN:
                return
            exiting = self._rt[leg.leg_id].setdefault("_exiting", False)
            if exiting:
                return
            self._rt[leg.leg_id]["_exiting"] = True

        if already_filled_at is not None:
            exit_px = already_filled_at
        elif self._paper:
            exit_px = self._premium(leg.symbol, ltp_hint) or leg.sl
        else:
            status, px = self._flatten_live(leg, reason="SL", force=False)
            if status == "DEFER":
                return   # GTT still armed — backstop will land here with the fill
            exit_px = px or self._premium(leg.symbol, ltp_hint) or leg.sl

        partner_id = leg.mtc_partner
        partner = core.legs.get(partner_id) if partner_id else None
        partner_ltp = self._premium(partner.symbol, None) if partner is not None else None

        action = core.on_short_sl_filled(
            leg.leg_id, exit_price=exit_px,
            partner_ltp=partner_ltp, ts=int(time.time()),
        )
        self._finish_close(leg)   # DB + telegram for the SL'd leg

        if action is None:
            core.finalize_if_done()
            return
        if action["action"] == MTC_MARKET_OUT:
            self._close_leg(action["partner"], reason="MTC_MARKET_OUT", ltp_hint=None)
        elif action["action"] == MTC_REPIN:
            self._repin_partner(action["partner"], action["cost_stop"])
        core.finalize_if_done()

    def _repin_partner(self, partner_id: str, cost_stop: float):
        core = self._core
        partner = core.legs[partner_id]
        rt = self._rt[partner_id]

        if self._paper:
            core.confirm_repin(partner_id)
            write_audit_log(f"[IC][MTC][PAPER] {partner.symbol} SL re-pinned to cost {cost_stop}")
            return

        # 1) cancel ALL partner GTTs, verified (see header for why cancel-first)
        for gid in list(rt["gtt_ids"]):
            gone = False
            try:
                gone = self.executor.cancel_gtt_verified(gid)
            except Exception as e:
                write_audit_log(f"[IC][MTC][CANCEL_ERR] gtt={gid} {e}")
            if not gone:
                write_audit_log(f"[IC][MTC][CANCEL_UNVERIFIED] gtt={gid} — "
                                f"KEEPING ORIGINAL SL (no repin, no market-out)")
                try:
                    notify_critical({"message":
                        f"IC_V1 MTC: could not cancel GTT {gid} on {partner.symbol}. "
                        f"Partner stays on ORIGINAL SL {partner.sl}. "
                        f"DELETE MANUALLY in Kite if you want the cost stop.",
                        "severity": "error"})
                except Exception:
                    pass
                return   # protected at original SL — bounded deviation from D5
            rt["gtt_ids"].remove(gid)

        # 2) place cost-stop GTT(s); failure here = UNPROTECTED → market out
        placed = []
        try:
            for chunk in rt["slices"]:
                gid = self.executor.place_gtt_sl_only_short(
                    symbol=partner.symbol, qty=chunk, sl_price=cost_stop)
                placed.append(str(gid))
        except Exception as e:
            write_audit_log(f"[IC][MTC][REPIN_PLACE_FAIL] {e} → MARKET_OUT partner (D5)")
            for gid in placed:      # don't leave partial protection armed
                try:
                    self.executor.cancel_gtt_verified(gid)
                except Exception:
                    pass
            self._close_leg(partner_id, reason="MTC_MARKET_OUT", ltp_hint=None)
            return

        rt["gtt_ids"] = placed
        core.confirm_repin(partner_id)
        write_audit_log(f"[IC][MTC][LIVE] {partner.symbol} SL re-pinned to cost {cost_stop} "
                        f"gtts={placed}")
        record_alert("IC_MTC", f"{partner.symbol} SL moved to cost {cost_stop}",
                     severity="info", strategy_id=STRATEGY_ID,
                     symbol=partner.symbol, mode="live")

    # ==================================================================
    # BACKSTOP HANDOFF (ic_gtt_monitor → here; monitor never mutates)
    # ==================================================================

    def on_backstop_leg_exit(self, *, leg_id: str, exit_price: float, reason: str):
        core = self._core
        if core is None:
            return
        leg = core.legs.get(leg_id)
        if leg is None or leg.state != L_OPEN:
            return
        if leg.is_short and reason == "SL":
            # GTT filled at broker → run the FULL MTC path with a known fill
            self._short_sl_path(leg, ltp_hint=exit_price, already_filled_at=exit_price)
            return
        with self._mutex:
            if leg.state != L_OPEN:
                return
        core.close_leg(leg_id, exit_price, reason)
        self._finish_close(leg)
        core.finalize_if_done()

    # ==================================================================
    # GENERIC CLOSE / FORCED EXITS
    # ==================================================================

    def _close_leg(self, leg_id: str, *, reason: str, ltp_hint=None, force=False):
        core = self._core
        leg = core.legs.get(leg_id)
        if leg is None:
            return
        with self._mutex:
            if leg.state != L_OPEN:
                return
            if self._rt[leg_id].get("_exiting"):
                return
            self._rt[leg_id]["_exiting"] = True

        if self._paper:
            px = self._premium(leg.symbol, ltp_hint) or leg.entry_price
        else:
            status, px = self._flatten_live(leg, reason=reason, force=force)
            if status == "DEFER":
                return   # armed GTT owns this exit; backstop will close it
            if px is None:
                px = self._premium(leg.symbol, ltp_hint) or leg.entry_price
        core.close_leg(leg_id, px, reason)
        self._finish_close(leg)

    def force_square_off_all(self, reason: str = "EOD") -> int:
        """EOD / MTM / manual — flatten everything, shorts first."""
        core = self._core
        if core is None or core.state not in (G_OPEN, G_CLOSING):
            return 0
        with self._mutex:
            core.state = G_CLOSING
        legs = sorted(core.open_legs(), key=lambda l: (0 if l.is_short else 1, l.leg_id))
        n = 0
        for leg in legs:
            self._close_leg(leg.leg_id, reason=reason, force=True)
            n += 1
        core.finalize_if_done()
        write_audit_log(f"[IC][SQUAREOFF] reason={reason} closed={n} state={core.state}")
        return n

    def _flatten_live(self, leg: LegCore, *, reason: str, force: bool) -> Optional[float]:
        """
        Cancel this leg's GTTs (verified), then flatten at market/protected.
        force=True (EOD/MTM/UNWIND): flatten even if a GTT stays armed
        (V2 house pattern + CRITICAL alert). force=False (tick SL/TP path):
        an armed GTT means DON'T double-fire — DEFER and let the GTT +
        backstop resolve it.
        Returns ("FLAT", price_hint_or_None) or ("DEFER", None).
        """
        rt = self._rt[leg.leg_id]
        for gid in list(rt["gtt_ids"]):
            gone = False
            try:
                gone = self.executor.cancel_gtt_verified(gid)
            except Exception as e:
                write_audit_log(f"[IC][FLATTEN][CANCEL_ERR] gtt={gid} {e}")
            if gone:
                rt["gtt_ids"].remove(gid)
                continue
            if not force:
                write_audit_log(f"[IC][FLATTEN][DEFER] {leg.symbol} gtt={gid} still armed "
                                f"— deferring to GTT/backstop (no double-fire)")
                rt["_exiting"] = False    # allow backstop to run the close
                return ("DEFER", None)
            try:
                notify_critical({"message":
                    f"IC_V1 {reason}: GTT {gid} on {leg.symbol} could not be cancelled "
                    f"but position is being flattened NOW. DELETE THE GTT MANUALLY in Kite.",
                    "severity": "error"})
            except Exception:
                pass

        try:
            for chunk in self._rt[leg.leg_id]["slices"]:
                if leg.is_short:
                    self.executor.place_buy_exit(symbol=leg.symbol, qty=chunk, reason=reason)
                else:
                    self.executor.place_market_sell(leg.symbol, chunk)
        except Exception as e:
            write_audit_log(f"[IC][FLATTEN][ORDER_FAIL] {leg.symbol} {e}")
        return ("FLAT", self._premium(leg.symbol, None))

    # ==================================================================
    # PRICE / DB / NOTIFY plumbing
    # ==================================================================

    def _premium(self, symbol: str, hint: Optional[float]) -> Optional[float]:
        if hint and hint > 0:
            return float(hint)
        if self._ltp_resolver is not None:
            try:
                v = self._ltp_resolver(symbol)
                if v and v > 0:
                    return float(v)
            except Exception as e:
                write_audit_log(f"[IC][LTP_RESOLVER_ERR] {symbol} {e}")
        try:
            res = LTPStore.get_with_timestamp(symbol)
            if res:
                ltp, ts = res
                if ltp and ltp > 0 and (time.time() - ts) <= LTP_STALENESS_SEC:
                    return float(ltp)
        except Exception:
            pass
        return None

    def _insert_all_rows(self, entry_ts: int):
        for leg in self._core.legs.values():
            rt = self._rt[leg.leg_id]
            oid = rt["order_ids"][0] if rt["order_ids"] else ""
            self._insert_row(leg, entry_ts, order_id=oid)

    def _insert_row(self, leg: LegCore, entry_ts: int, *, order_id: str):
        rt = self._rt[leg.leg_id]
        direction = "SHORT" if leg.is_short else "LONG"
        cfg = self._cfg()
        lot_size = self._lot_size(cfg)
        # ── IC_GROUPING ── all four legs of this entry share self._group_id;
        # trade_class carries the leg role (L1..L4) so the frontend can label
        # short-body vs wing without re-deriving from direction+side.
        group_id = self._group_id
        try:
            if self._paper:
                pid = str(uuid.uuid4())
                insert_paper_trade(
                    paper_trade_id=pid, strategy_name=STRATEGY_ID,
                    trade_mode="PAPER", symbol=leg.symbol, token=rt["token"],
                    side=leg.opt_type, entry_price=leg.entry_price,
                    candle_ts=entry_ts, sl_price=leg.sl or 0.0,
                    tp_price=leg.tp or 0.0, rr=0.0,
                    lots=leg.qty // max(1, lot_size), lot_size=lot_size,
                    qty=leg.qty, trade_direction=direction,
                    group_id=group_id, trade_class=leg.leg_id,
                )
                rt["db_id"] = pid
            else:
                tid = str(uuid.uuid4())
                insert_trade(
                    trade_id=tid, strategy_id=STRATEGY_ID, slot=leg.leg_id,
                    symbol=leg.symbol, token=rt["token"],
                    entry_price=leg.entry_price, qty=leg.qty,
                    buy_order_id=order_id, sl_price=leg.sl or 0.0,
                    tp_price=leg.tp or 0.0, tp_mode="GTT",
                    state="PROTECTED", trade_direction=direction,
                    group_id=group_id, trade_class=leg.leg_id,
                )
                rt["db_id"] = tid
        except Exception as e:
            write_audit_log(f"[IC][DB_INSERT_FAIL][{leg.leg_id}] {e}")

    def _finish_close(self, leg: LegCore):
        rt = self._rt.get(leg.leg_id) or {}
        db_id = rt.get("db_id")
        try:
            if db_id:
                if self._paper:
                    close_paper_trade(paper_trade_id=db_id,
                                      exit_price=leg.exit_price or 0.0,
                                      exit_reason=leg.exit_reason or "")
                else:
                    close_trade(trade_id=db_id,
                                exit_price=leg.exit_price,
                                exit_order_id=None,
                                exit_reason=leg.exit_reason or "")
        except Exception as e:
            write_audit_log(f"[IC][DB_CLOSE_FAIL][{leg.leg_id}] {e}")

        payload = {
            "strategy_id": STRATEGY_ID,
            "mode": "paper" if self._paper else "live",
            "symbol": leg.symbol, "side": leg.leg_id,
            "entry_price": leg.entry_price, "exit_price": leg.exit_price,
            "quantity": leg.qty, "pnl": leg.pnl(),
            "reason": leg.exit_reason,
        }
        try:
            r = leg.exit_reason or ""
            if r in ("SL", "MTC_COST", "MTC_MARKET_OUT"):
                notify_sl_exit(payload)
            elif r == "TP":
                notify_tp_exit(payload)
            else:
                notify_manual_exit(payload)
        except Exception as e:
            write_audit_log(f"[IC][TG_EXIT_FAIL] {e}")
        write_audit_log(
            f"[IC][LEG_CLOSE] {leg.leg_id} {leg.symbol} reason={leg.exit_reason} "
            f"exit={leg.exit_price} pnl={leg.pnl()}"
        )

    def _notify_entry(self, mode: str):
        for leg in self._core.legs.values():
            try:
                notify_trade_entry({
                    "strategy_id": STRATEGY_ID, "mode": mode,
                    "symbol": leg.symbol, "side": leg.leg_id,
                    "entry_price": leg.entry_price, "quantity": leg.qty,
                    "sl": leg.sl, "tp": leg.tp,
                })
            except Exception as e:
                write_audit_log(f"[IC][TG_ENTRY_FAIL] {e}")

    def _margin_ok(self, core: GroupCore, cfg) -> bool:
        """D8 — ADVISORY-FAIL-OPEN: only a CONFIRMED shortfall blocks."""
        if not cfg.get("margin_guard", True):
            return True
        fn = getattr(self.executor, "get_basket_margin", None)
        if fn is None:
            write_audit_log("[IC][MARGIN] get_basket_margin unavailable — proceeding (fail open)")
            return True
        try:
            basket = [
                {"symbol": l.symbol, "qty": l.qty,
                 "transaction_type": "SELL" if l.is_short else "BUY"}
                for l in core.legs.values()
            ]
            res = fn(basket) or {}
            required  = float(res.get("required") or 0.0)
            available = float(res.get("available") or 0.0)
            if required > 0 and available > 0 and required > available:
                write_audit_log(f"[IC][MARGIN][BLOCK] required={required:.0f} "
                                f"available={available:.0f}")
                return False
            return True
        except Exception as e:
            write_audit_log(f"[IC][MARGIN][ERR] {e} — proceeding (fail open)")
            return True

    # ==================================================================
    # INSPECTION (API / UI / monitor)
    # ==================================================================

    def current_group(self) -> Optional[GroupCore]:
        return self._core

    def leg_runtime(self, leg_id: str) -> dict:
        return dict(self._rt.get(leg_id) or {})

    def is_paper(self) -> bool:
        return self._paper

    def has_open_group(self) -> bool:
        return self._core is not None and self._core.state in (G_OPEN, G_CLOSING)