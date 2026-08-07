# backend/app/engine/ic/ic_group_manager.py
#
# IC (shared V1/V2) — Group Manager (live + paper) — IC_V2 SEMANTICS available
# per config (2026-07-26 lock); IC_SPLIT 2026-08-04: one instance per strategy.
# ============================================================================
# Modeled on scalp_v2_group_manager.py (single authoritative close path,
# GTT cancel-verified-before-flatten, REST-primary price resolution, backstop
# handoff ownership rule). The strategy STATE MACHINE lives in
# ic_live_core.GroupCore — this class only does I/O around it.
# Decisions D1–D9 locked 2026-07-06; IC_V2 amendment DA1–DA6 + first-candle
# rule locked 2026-07-26.
#
# ENTRY MODEL: one scheduled entry per day at entry_time (09:18) on the
#   engine's thread; synchronous slice-by-slice fill confirm; D2 wings-first;
#   D6 all-or-unwind. UNCHANGED by the IC_V2 amendment.
#
# ── IC_V2 SEMANTICS OWNED HERE ─────────────────────────────────────────────
#   MTC (D2=a)   : on a short's confirmed stop fill, the partner re-pin is
#     SCHEDULED at fill+60s (_pending_mtc). Until activation the partner
#     keeps its ORIGINAL SL GTT — protected throughout. At activation,
#     core.mtc_activation_decision() decides REPIN (cancel-verified partner
#     GTTs first — two armed BUYs on one short would fill 2× and leave us
#     LONG — then place the cost-stop GTT) or MARKET_OUT (LTP at/through
#     cost, or unknown). Deviation ladder unchanged from D5:
#       cancel unverified → keep ORIGINAL SL + CRITICAL (never market-out
#       against an armed GTT); cancels ok but placement fails → UNPROTECTED
#       → MARKET_OUT immediately.
#   ADJ_ON_MTC   : any short stop exit with reason SL *or* MTC_COST arms an
#     adjustment BUY on the same option type (_pending_adjust), activating
#     adjust_delay_s later. Strike selected AT ACTIVATION from a fresh chain
#     snapshot (DA3): highest premium <= adjust.premium_max, FAIL CLOSED.
#     Activation past ADJ_CUTOFF_HM is DROPPED (the backtest's C2/b "no
#     candle at the activation minute" analog). Adjustment legs get their
#     own SL/TP protection (sell-side GTT for the long) and can carry.
#   ONE_NIGHT_MAX: exit_mode "NEXT_OPEN". Session end on a non-expiry entry
#     day → commit_carry(): DA5 assert, persist snapshot (ic_carry_store),
#     legs stay open in memory, GTTs stay armed at the broker overnight
#     (D3: broker-side protection is exactly what an overnight hold needs —
#     the app/machine is likely asleep). NRML product → legally holdable.
#   CARRY MORNING (first-candle rule): pre-market (before 09:15) every
#     carried leg's GTTs are cancel-verified (premarket_cancel_gtts) so
#     nothing can fire inside 09:15–09:16. From next_open_time (09:16) the
#     morning_square_off() retry loop is the SOLE exit executor — market
#     orders, STRICT mode (an order failure leaves the leg OPEN for the next
#     retry, unlike the legacy EOD path), shorts first. GTTs are NEVER
#     re-armed while the retry loop runs: an armed buy-back GTT concurrent
#     with market-close retries is the double-buy accidental-long hazard.
#   EXPIRY SCOPING (DA5): the 15:28 square-off closes ONLY legs entered
#     TODAY whose expiry is TODAY (expiry_square_off). A leg carried INTO
#     its expiry day was already closed at 09:16.
#   ADJ_ONLY     : the condor legs run as PHANTOMS — full state machine
#     (SLs fire, MTC re-pins, adjustments arm on the identical timeline)
#     but no broker orders, no GTTs, no DB rows. Only ·ADJ legs are booked
#     (paper row or live orders per mode). Backtest parity: the runner's
#     core_legs_suppressed behavior.
#
# D7 latch: unchanged (persisted, set BEFORE the first order). PLUS the
#   open-book gate: an active group (incl. a restored carry) blocks entry.
# D8 margin guard: unchanged (advisory-fail-open).
# D9 paper: unchanged — paper legs run this exact class.
# IC_GROUPING: unchanged; adjustment legs share the condor's group_id with
#   trade_class "<src>A" so the Analytics CondorCard shows them in-context.
# ISOLATION: owns only its OWN strategy_id's state. BB/HA/SCALP/PST/TMA untouched.
# ============================================================================

import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
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
    notify_group_entry,          # ── GROUP_ENTRY ── one message per basket
    notify_sl_exit,
    notify_tp_exit,
    notify_manual_exit,
    notify_critical,
)

from app.engine.ic.ic_live_core import (
    GroupCore, LegCore, StrikePick, slice_qty, sl_price, tp_price,
    select_strike,
    G_OPEN, G_CLOSING, G_CLOSED, G_ABORTED,
    L_OPEN, L_CLOSED,
    MTC_REPIN, MTC_MARKET_OUT, MTC_DELAY_S, ADJ_ARM_REASONS,
)
from app.engine.ic.ic_selection import ICSelection
from app.engine.ic import ic_carry_store

IST = timezone(timedelta(minutes=330))    # house rule: fixed offset, no pytz

LTP_STALENESS_SEC = 30

# Synchronous entry fill confirmation. Protected limits at 09:18 fill in
# seconds; a leg that hasn't filled in 45s is dead for our purposes (D6).
_ENTRY_FILL_CAP_S       = 45
_ENTRY_FILL_POLL_S      = 2
_DEAD_ORDER_STATUSES    = {"REJECTED", "CANCELLED", "LAPSED"}

# ── IC_V2 ── adjustment activation cutoff (IST minutes-from-midnight).
# The backtest drops an adjustment whose activation minute has no candle
# (session candles end 15:29); live mirrors it with a clock cutoff.
ADJ_CUTOFF_MIN = 15 * 60 + 29

STATE_DIR  = Path.home() / ".scalp-app" / "state"


# ── IC_SPLIT (2026-08-04) ── the manager is INSTANTIATED PER STRATEGY
# ("IC_V1" | "IC_V2"); every persisted artifact (latch/carry/session), DB
# row and alert is scoped by self.strategy_id. No module-level identity.
def _latch_path(strategy_id: str) -> Path:
    return STATE_DIR / f"{strategy_id}_day_latch.json"

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

# Backtest DEFAULT_ADJUST parity (backtest_ic_runner.py)
DEFAULT_ADJUST = {
    "L1": {"enabled": True, "lots": 24, "premium_max": 85,
           "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
    "L2": {"enabled": True, "lots": 24, "premium_max": 85,
           "sl_val": 25, "sl_mode": "pct", "tp_val": 0, "tp_mode": "pct"},
}


def _now_ist() -> datetime:
    return datetime.now(IST)


def _min_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


class ICGroupManager:

    def __init__(self, strategy_id: str, executor=None,
                 ltp_resolver: Optional[Callable] = None,
                 chain_provider: Optional[Callable] = None):
        """
        strategy_id    : "IC_V1" | "IC_V2" — REQUIRED, no default: silently
                         misrouting a condor between instances is the exact
                         failure this parameter exists to prevent (IC_SPLIT).
        executor       : ZerodhaOrderExecutor (None in pure-paper startup).
        ltp_resolver   : callable(symbol)->float|None. REST-primary.
        chain_provider : callable()->(expiry, ce_cands, pe_cands, tokens) —
                         fresh weekly-chain snapshot for adjustment strike
                         selection at activation time (DA3). Injected by the
                         runtime/engine; None → adjustments alert + drop.
        """
        self.strategy_id     = str(strategy_id)
        self.executor        = executor
        self._ltp_resolver   = ltp_resolver
        self._chain_provider = chain_provider
        self._core: Optional[GroupCore] = None
        self._rt: Dict[str, dict] = {}     # leg_id -> runtime extras
        self._paper = True
        self._mutex = threading.RLock()
        self._entry_lock = threading.Lock()
        # ── IC_GROUPING ── per-condor key shared by all legs (incl. ·ADJ).
        self._group_id: Optional[str] = None
        # ── IC_V2 ── phantom condor (ADJ_ONLY)
        self._adjust_only = False
        # ── IC_V2 ── scheduled actions (intraday only; DROPPED at carry
        # commit — backtest parity: pending re-pins/adjustments do not
        # survive the session).
        self._pending_mtc:    Dict[str, int] = {}   # partner_id -> activate_ts
        self._pending_adjust: Dict[str, int] = {}   # src_leg_id -> activate_ts
        # ── IC_V2 ── carry lifecycle
        self._carry_committed = False   # this session committed a carry
        self._carry_entry_date: Optional[str] = None   # "YYYY-MM-DD" of the
                                        # carried legs' entry day — the 09:16
                                        # close is legal only STRICTLY AFTER it
        self._carry_hold = False        # engine-set: suppress carried-leg
                                        # tick exits before next_open (first-
                                        # candle rule; engine also gates)

    def attach_executor(self, executor):
        self.executor = executor

    def attach_ltp_resolver(self, fn):
        self._ltp_resolver = fn

    def attach_chain_provider(self, fn):
        self._chain_provider = fn

    def set_carry_hold(self, hold: bool):
        self._carry_hold = bool(hold)

    # ==================================================================
    # CONFIG
    # ==================================================================

    def _cfg(self) -> dict:
        try:
            return load_strategy_config(self.strategy_id) or {}
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][CFG_READ_FAIL] {e} — using safe defaults")
            return {}

    def _legs_cfg(self, cfg) -> List[dict]:
        legs = cfg.get("legs") or DEFAULT_LEGS
        return [dict(l) for l in legs]

    def _lot_size(self, cfg) -> int:
        return int(cfg.get("quantity", {}).get("lot_size", 65))

    def _freeze_qty(self, cfg) -> int:
        return int(cfg.get("freeze_qty", 1800))

    def _gtt_buffer(self, cfg) -> float:
        """SL-GTT limit buffer multiplier (gap defence layer 1). Default 5%
        — the pre-amendment 0.3% factor fails on any fast move."""
        pct = float(cfg.get("gtt_limit_buffer_pct", 5) or 5)
        return 1.0 + max(0.0, pct) / 100.0

    def _adjust_enabled(self, cfg) -> bool:
        return bool(cfg.get("adjust_on_sl", False))

    def _adjust_delay_s(self, cfg) -> int:
        return int(cfg.get("adjust_delay_s", 60) or 60)

    def _adjust_cfg_for(self, cfg, src_leg_id: str) -> Optional[dict]:
        raw = cfg.get("adjust") or (DEFAULT_ADJUST if self._adjust_enabled(cfg) else {})
        a = raw.get(src_leg_id)
        if not a:
            return None
        if not bool(a.get("enabled", True)) or int(a.get("lots") or 0) <= 0:
            return None
        return dict(a)

    # ── IC_GROUPING ── mint one condor key per entry ──────────────────
    def _new_group_id(self) -> str:
        return f"IC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    # ==================================================================
    # D7 — persisted one-entry-per-day latch
    # ==================================================================

    def _latch_today(self) -> bool:
        try:
            lp = _latch_path(self.strategy_id)
            if not lp.exists():
                return False
            d = json.loads(lp.read_text())
            return d.get("date") == datetime.now().strftime("%Y-%m-%d")
        except Exception as e:
            # Unreadable latch: FAIL CLOSED (assume entered).
            write_audit_log(f"[IC][{self.strategy_id}][LATCH_READ_FAIL] {e} — assuming ENTERED")
            return True

    def _set_latch(self, mode: str):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "entered_at": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
        })
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR),
                                   prefix=f".{self.strategy_id.lower()}_latch_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, _latch_path(self.strategy_id))
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][LATCH_WRITE_FAIL] {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ==================================================================
    # ENTRY — called by ic_engine at entry_time on a dedicated thread
    # ==================================================================

    def enter_day(self, selection: ICSelection, *, mode: str) -> bool:
        if not self._entry_lock.acquire(blocking=False):
            write_audit_log(f"[IC][{self.strategy_id}][ENTRY] re-entrant call blocked")
            return False
        try:
            return self._enter_day_impl(selection, mode=mode)
        finally:
            self._entry_lock.release()

    def _enter_day_impl(self, selection: ICSelection, *, mode: str) -> bool:
        cfg = self._cfg()

        # ── gates ──────────────────────────────────────────────────────
        # Open-book gate (D8 amendment): ANY active group — today's, or a
        # carried group whose 09:16 close has not fully reconciled — blocks.
        if self._core is not None and self._core.state not in (G_CLOSED, G_ABORTED):
            write_audit_log(f"[IC][{self.strategy_id}][ENTRY] group already active → drop (open-book gate)")
            return False
        if self._latch_today():
            write_audit_log(f"[IC][{self.strategy_id}][ENTRY] day latch set → drop (D7)")
            return False
        if is_day_blocked(self.strategy_id):
            write_audit_log(f"[IC][{self.strategy_id}][ENTRY] MTM day-block → drop")
            return False
        try:
            if not load_global_config().get("trade_on", False):
                write_audit_log(f"[IC][{self.strategy_id}][ENTRY] trade_on=FALSE → drop")
                return False
        except Exception:
            return False
        try:
            if check_strategy_max_loss(self.strategy_id):
                write_audit_log(f"[IC][{self.strategy_id}][ENTRY] RISK_LIMIT_HIT → drop")
                return False
        except Exception:
            return False   # fail closed

        # ── selection outcome ─────────────────────────────────────────
        if not selection.ok:
            write_audit_log(f"[IC][{self.strategy_id}][ENTRY] NO ENTRY TODAY — {selection.skip_reason}")
            record_alert("IC_NO_ENTRY", f"{self.strategy_id} skipped: {selection.skip_reason}",
                         severity="warning", strategy_id=self.strategy_id, mode=mode.lower())
            return False

        if selection.wing_absent and not cfg.get("allow_strangle_degrade", False):
            write_audit_log(
                f"[IC][ENTRY] wings absent {selection.wing_absent} and "
                f"strangle-degrade disabled → skip day (D6 policy)"
            )
            record_alert("IC_WING_ABSENT",
                         f"{self.strategy_id} skipped: wings absent {selection.wing_absent}",
                         severity="warning", strategy_id=self.strategy_id, mode=mode.lower())
            return False

        legs_cfg  = self._legs_cfg(cfg)
        lot_size  = self._lot_size(cfg)
        freeze    = self._freeze_qty(cfg)
        self._paper = (mode != "LIVE")
        # ── IC_V2 ── ADJ_ONLY: condor legs are phantoms regardless of mode
        self._adjust_only = bool(cfg.get("adjust_only", False)) and \
            self._adjust_enabled(cfg)

        self._group_id = self._new_group_id()

        core = GroupCore()
        rt: Dict[str, dict] = {}
        entry_ts   = int(time.time())
        today_str  = _now_ist().strftime("%Y-%m-%d")
        expiry_str = selection.expiry.isoformat() if selection.expiry else ""

        for lc in legs_cfg:
            lid = lc["id"]
            if int(lc.get("lots", 0)) <= 0 or lid not in selection.picks:
                continue
            pick = selection.picks[lid]
            qty  = int(lc["lots"]) * lot_size
            try:
                slices = slice_qty(qty, freeze, lot_size)
            except ValueError as e:
                write_audit_log(f"[IC][{self.strategy_id}][ENTRY][CONFIG_FAIL] {e} → skip day")
                return False
            sl = sl_price(lc["action"], pick.ltp, float(lc.get("sl_val") or 0), lc.get("sl_mode", "pct"))
            tp = tp_price(lc["action"], pick.ltp, float(lc.get("tp_val") or 0), lc.get("tp_mode", "pct"))
            core.legs[lid] = LegCore(
                leg_id=lid, action=lc["action"], opt_type=lc["opt_type"],
                symbol=pick.symbol, qty=qty, entry_price=pick.ltp,
                sl=sl, tp=tp,
                mtc_partner=lc.get("mtc_partner"),
                wing_fallback=pick.fallback,
                entry_date=today_str, expiry=expiry_str,
            )
            rt[lid] = {
                "token": selection.tokens.get(lid, 0),
                "slices": slices,
                "order_ids": [],
                "gtt_ids": [],
                "db_id": None,
                "paper": self._paper,
                "phantom": self._adjust_only,
            }

        shorts = [l for l in core.legs.values() if l.is_short]
        if len(shorts) != 2:
            write_audit_log(f"[IC][{self.strategy_id}][ENTRY] expected 2 shorts, got {len(shorts)} → skip")
            return False

        # ── D8 margin guard (live only, real orders only) ──────────────
        if not self._paper and not self._adjust_only and not self._margin_ok(core, cfg):
            record_alert("IC_MARGIN_BLOCK", f"{self.strategy_id} entry blocked: margin shortfall",
                         severity="error", strategy_id=self.strategy_id, mode="live")
            return False

        # ── D7: latch BEFORE the first order ───────────────────────────
        self._set_latch(mode)

        with self._mutex:
            self._core = core
            self._rt = rt
            self._carry_committed = False
            self._pending_mtc.clear()
            self._pending_adjust.clear()
            core.begin_entry()

        write_audit_log(
            f"[IC][ENTRY][{mode}]{'[ADJ_ONLY]' if self._adjust_only else ''} "
            f"group_id={self._group_id} expiry={expiry_str} "
            + " ".join(f"{l.leg_id}={l.symbol}@{l.entry_price}" for l in core.legs.values())
        )

        if self._adjust_only:
            return self._enter_phantom()
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
        self._persist_session()   # IC_RESTART
        write_audit_log(f"[IC][{self.strategy_id}][ENTRY][PAPER] group OPEN")
        return True

    # ── IC_V2 ── PHANTOM entry (ADJ_ONLY): full state machine, zero I/O ─
    def _enter_phantom(self) -> bool:
        core = self._core
        for leg in core.legs.values():
            core.leg_filled(leg.leg_id)
        record_alert("IC_ADJ_ONLY",
                     f"{self.strategy_id} ADJ_ONLY: condor is SIMULATED (no orders, no "
                     "rows) — only adjustment legs will be booked.",
                     severity="info", strategy_id=self.strategy_id,
                     mode="paper" if self._paper else "live")
        self._persist_session()   # IC_RESTART
        write_audit_log(f"[IC][{self.strategy_id}][ENTRY][PHANTOM] condor simulated (ADJ_ONLY) — group OPEN")
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

        # shorts protected only after ALL legs confirmed
        for leg in core.legs.values():
            if leg.is_short:
                self._protect_short(leg)

        self._insert_all_rows(entry_ts)
        self._notify_entry("live")
        self._persist_session()   # IC_RESTART
        write_audit_log(f"[IC][{self.strategy_id}][ENTRY][LIVE] group OPEN")
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
                write_audit_log(f"[IC][{self.strategy_id}][ENTRY][{leg.leg_id}][PLACE_FAIL] {e}")
                return False
            rt["order_ids"].append(oid)

            avg = self._confirm_fill(oid)
            if avg is None:
                try:
                    self.executor.cancel_order(oid)
                except Exception:
                    pass
                write_audit_log(f"[IC][{self.strategy_id}][ENTRY][{leg.leg_id}][DEAD] order={oid}")
                return False
            fills.append((chunk, avg if avg > 0 else limit_px))

        total = sum(q for q, _ in fills)
        if total > 0:
            leg.entry_price = sum(q * p for q, p in fills) / total
        return True

    def _place_wing_buy(self, symbol, token, qty):
        res = self.executor.place_buy(symbol, token, qty)
        oid, px = res[0], (res[1] if len(res) > 1 else 0.0)
        return oid, float(px or 0.0), qty

    def _confirm_fill(self, order_id: str) -> Optional[float]:
        deadline = time.time() + _ENTRY_FILL_CAP_S
        while time.time() < deadline:
            try:
                info = self.executor.get_order_fill(order_id) or {}
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][FILL_POLL_ERR] {order_id} {e}")
                info = {}
            status = (info.get("status") or "").upper()
            if status == "COMPLETE":
                return float(info.get("avg_price") or 0.0)
            if status in _DEAD_ORDER_STATUSES:
                return None
            time.sleep(_ENTRY_FILL_POLL_S)
        return None

    def _unwind_after_dead(self, dead_leg_id: str):
        core = self._core
        to_unwind = core.leg_entry_dead(dead_leg_id)
        write_audit_log(f"[IC][{self.strategy_id}][UNWIND] dead={dead_leg_id} unwinding={to_unwind}")
        for lid in to_unwind:
            leg = core.legs[lid]
            _, px = self._flatten_live(leg, reason="UNWIND", force=True)
            core.record_unwind(lid, exit_price=px if px is not None else leg.entry_price)
        record_alert("IC_UNWOUND",
                     f"{self.strategy_id} entry failed at {dead_leg_id} — group unwound",
                     severity="error", strategy_id=self.strategy_id, mode="live")
        try:
            notify_critical({"message": f"{self.strategy_id} entry FAILED at {dead_leg_id}; "
                                        f"all filled legs unwound. No entry today.",
                             "severity": "error"})
        except Exception:
            pass

    # ==================================================================
    # SHORT PROTECTION (D4) — gap-buffered limits
    # ==================================================================

    def _protect_short(self, leg: LegCore):
        rt = self._rt[leg.leg_id]
        if rt.get("phantom"):
            return
        buf = self._gtt_buffer(self._cfg())
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
                        limit_buffer=buf,
                    )
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][GTT_FAIL][{leg.leg_id}] {e} — tick monitor is sole protection")
                record_alert("IC_GTT_FAIL",
                             f"{leg.symbol} SL GTT failed — tick-monitor only",
                             severity="error", strategy_id=self.strategy_id,
                             symbol=leg.symbol, mode="live")
            if gid:
                rt["gtt_ids"].append(str(gid))

    def _protect_adjust_long(self, leg: LegCore):
        """SL/TP protection for an ·ADJ long. Sell-side GTT(s)."""
        rt = self._rt[leg.leg_id]
        if rt.get("phantom") or self._paper:
            return
        buf = self._gtt_buffer(self._cfg())
        for chunk in rt["slices"]:
            gid = None
            try:
                if leg.sl and leg.tp:
                    gid = self.executor.place_gtt_oco(
                        symbol=leg.symbol, qty=chunk,
                        sl_price=leg.sl, tp_price=leg.tp,
                    )
                elif leg.sl:
                    gid = self.executor.place_gtt_sl_only_long(
                        symbol=leg.symbol, qty=chunk, sl_price=leg.sl,
                        limit_buffer=buf,
                    )
                elif leg.tp:
                    gid = self.executor.place_gtt_tp_only_long(
                        symbol=leg.symbol, qty=chunk, tp_price=leg.tp)
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][ADJ][GTT_FAIL][{leg.leg_id}] {e} — "
                                f"tick monitor is sole protection")
                record_alert("IC_GTT_FAIL",
                             f"{leg.symbol} ·ADJ SL GTT failed — tick-monitor only",
                             severity="error", strategy_id=self.strategy_id,
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
            # ── first-candle rule (belt-and-braces; engine gates too):
            # carried legs take NO tick exits — the 09:16 morning close is
            # the sole exit executor on a carry morning.
            if leg.carried and self._carry_hold:
                return
            if leg.is_short:
                if leg.sl and ltp >= leg.sl:
                    self._short_stop_path(leg, ltp)
                elif leg.tp and ltp <= leg.tp:
                    if self._close_leg(leg.leg_id, reason="TP", ltp_hint=ltp):
                        core.finalize_if_done()
                        self._after_close_housekeeping()
            else:
                if leg.sl and ltp <= leg.sl:
                    closed = self._close_leg(leg.leg_id, reason="SL", ltp_hint=ltp)
                    if closed:
                        core.finalize_if_done()
                        self._after_close_housekeeping()
                elif leg.tp and ltp >= leg.tp:
                    if self._close_leg(leg.leg_id, reason="TP", ltp_hint=ltp):
                        core.finalize_if_done()
                        self._after_close_housekeeping()
            return

    # ── IC_V2 ── monitor escalation entry point (triggered-unfilled GTT)
    def escalate_unfilled_gtt(self, *, leg_id: str):
        """The GTT monitor confirmed: GTT triggered, limit UNFILLED,
        position still open (gap past the limit buffer). Market-out through
        the single close path. Shorts route the full stop path so MTC /
        adjustment consequences still fire; longs (·ADJ) close generically.
        force-flavored: consumed/armed GTTs are cancel-attempted but never
        allowed to DEFER (a consumed GTT will never fill this exit)."""
        core = self._core
        if core is None:
            return
        leg = core.legs.get(leg_id)
        if leg is None or leg.state != L_OPEN:
            return
        if leg.is_short:
            self._short_stop_path(leg, ltp_hint=None, escalate=True)
        else:
            self._close_leg(leg_id, reason="SL", force=True)
            self._core.finalize_if_done()
            self._after_close_housekeeping()

    # ── short stop: single authoritative path (tick AND backstop) ──────
    def _short_stop_path(self, leg: LegCore, ltp_hint: Optional[float],
                         already_filled_at: Optional[float] = None,
                         escalate: bool = False):
        """
        Handles BOTH the original SL and the moved-to-cost stop (the core
        translates the reason). already_filled_at: set by the backstop when
        the broker GTT already filled (exit price known). escalate: monitor-
        confirmed triggered-unfilled GTT — flatten with force (no DEFER).
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
        elif self._paper or self._rt[leg.leg_id].get("phantom"):
            exit_px = self._premium(leg.symbol, ltp_hint) or leg.sl
        else:
            status, px = self._flatten_live(leg, reason="SL", force=escalate)
            if status == "DEFER":
                return   # GTT still armed — backstop will land here with the fill
            exit_px = px or self._premium(leg.symbol, ltp_hint) or leg.sl

        ts = int(time.time())
        res = core.on_short_stop_filled(leg.leg_id, exit_price=exit_px, ts=ts)
        self._finish_close(leg)   # DB + telegram (skips phantoms)

        # ── IC_V2 ── schedule the partner re-pin (next-minute effective)
        mp = res.get("mtc_pending")
        if mp:
            self._pending_mtc[mp["partner"]] = int(mp["activate_ts"])
            write_audit_log(f"[IC][{self.strategy_id}][MTC][SCHEDULED] partner={mp['partner']} "
                            f"activate_ts={mp['activate_ts']} (+{MTC_DELAY_S}s)")

        # ── IC_V2 ── ADJ_ON_MTC: arm the adjustment (config-gated)
        ap = res.get("adjust_pending")
        if ap:
            self._schedule_adjust(ap["src"], ts)

        core.finalize_if_done()
        self._after_close_housekeeping()   # persists session when still open

    # ==================================================================
    # IC_V2 — SCHEDULED ACTIONS (engine calls process_due every iteration)
    # ==================================================================

    def process_due(self, now_ts: Optional[int] = None):
        """Execute due MTC activations and adjustment placements. Engine
        thread only. Poll cadence (4–5s) bounds activation slack; documented
        as +60s (+poll) — the 'next minute' in a tick world."""
        if self._core is None:
            return
        now_ts = int(now_ts if now_ts is not None else time.time())

        for partner_id, act_ts in list(self._pending_mtc.items()):
            if now_ts < act_ts:
                continue
            del self._pending_mtc[partner_id]
            self._activate_mtc(partner_id)

        for src, act_ts in list(self._pending_adjust.items()):
            if now_ts < act_ts:
                continue
            del self._pending_adjust[src]
            self._activate_adjust(src)

    # ── MTC activation (decision AT activation, D2=a) ──────────────────
    def _activate_mtc(self, partner_id: str):
        core = self._core
        partner = core.legs.get(partner_id)
        if partner is None:
            return
        partner_ltp = self._premium(partner.symbol, None)
        action = core.mtc_activation_decision(partner_id, partner_ltp)
        if action is None:
            write_audit_log(f"[IC][{self.strategy_id}][MTC][ACTIVATE] partner={partner_id} no "
                            f"longer actionable — no-op")
            return
        if action["action"] == MTC_MARKET_OUT:
            write_audit_log(f"[IC][{self.strategy_id}][MTC][ACTIVATE] partner={partner_id} "
                            f"ltp={partner_ltp} at/through cost → MARKET_OUT")
            self._close_leg(partner_id, reason="MTC_MARKET_OUT", ltp_hint=partner_ltp)
            self._after_close_housekeeping()
            return
        self._repin_partner(partner_id, action["cost_stop"])

    def _repin_partner(self, partner_id: str, cost_stop: float):
        core = self._core
        partner = core.legs[partner_id]
        rt = self._rt[partner_id]

        if self._paper or rt.get("phantom"):
            core.confirm_repin(partner_id)
            write_audit_log(f"[IC][{self.strategy_id}][MTC][{'PHANTOM' if rt.get('phantom') else 'PAPER'}] "
                            f"{partner.symbol} SL re-pinned to cost {cost_stop}")
            self._persist_session()   # IC_RESTART
            return

        # 1) cancel ALL partner GTTs, verified (cancel-first: see header)
        for gid in list(rt["gtt_ids"]):
            gone = False
            try:
                gone = self.executor.cancel_gtt_verified(gid)
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][MTC][CANCEL_ERR] gtt={gid} {e}")
            if not gone:
                write_audit_log(f"[IC][{self.strategy_id}][MTC][CANCEL_UNVERIFIED] gtt={gid} — "
                                f"KEEPING ORIGINAL SL (no repin, no market-out)")
                try:
                    notify_critical({"message":
                        f"{self.strategy_id} MTC: could not cancel GTT {gid} on {partner.symbol}. "
                        f"Partner stays on ORIGINAL SL {partner.sl}. "
                        f"DELETE MANUALLY in Kite if you want the cost stop.",
                        "severity": "error"})
                except Exception:
                    pass
                return   # protected at original SL — bounded deviation from D5
            rt["gtt_ids"].remove(gid)

        # 2) place cost-stop GTT(s); failure here = UNPROTECTED → market out
        placed = []
        buf = self._gtt_buffer(self._cfg())
        try:
            for chunk in rt["slices"]:
                gid = self.executor.place_gtt_sl_only_short(
                    symbol=partner.symbol, qty=chunk, sl_price=cost_stop,
                    limit_buffer=buf)
                placed.append(str(gid))
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][MTC][REPIN_PLACE_FAIL] {e} → MARKET_OUT partner (D5)")
            for gid in placed:      # don't leave partial protection armed
                try:
                    self.executor.cancel_gtt_verified(gid)
                except Exception:
                    pass
            self._close_leg(partner_id, reason="MTC_MARKET_OUT", ltp_hint=None)
            self._after_close_housekeeping()
            return

        rt["gtt_ids"] = placed
        core.confirm_repin(partner_id)
        self._persist_session()   # IC_RESTART
        write_audit_log(f"[IC][{self.strategy_id}][MTC][LIVE] {partner.symbol} SL re-pinned to cost {cost_stop} "
                        f"gtts={placed}")
        record_alert("IC_MTC", f"{partner.symbol} SL moved to cost {cost_stop}",
                     severity="info", strategy_id=self.strategy_id,
                     symbol=partner.symbol, mode="live")

    # ── ADJ scheduling + activation ────────────────────────────────────
    def _schedule_adjust(self, src_leg_id: str, trigger_ts: int):
        cfg = self._cfg()
        if not self._adjust_enabled(cfg):
            return
        if self._adjust_cfg_for(cfg, src_leg_id) is None:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ] {src_leg_id} stop exit — adjustment "
                            f"disabled/zero-lots for this leg → not armed")
            return
        aid = f"{src_leg_id}A"
        if self._core is not None and aid in self._core.legs:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ] {aid} already exists → not re-armed")
            return
        act_ts = trigger_ts + self._adjust_delay_s(cfg)
        self._pending_adjust[src_leg_id] = act_ts
        write_audit_log(f"[IC][{self.strategy_id}][ADJ][ARMED] src={src_leg_id} activate_ts={act_ts}")
        self._persist_session()   # IC_RESTART (pending survives a restart)

    def _activate_adjust(self, src_leg_id: str):
        core = self._core
        cfg  = self._cfg()
        src  = core.legs.get(src_leg_id)
        acfg = self._adjust_cfg_for(cfg, src_leg_id)
        if src is None or acfg is None:
            return

        now = _now_ist()
        if _min_of_day(now) >= ADJ_CUTOFF_MIN:
            # C2/b analog: activation past the last tradable minute → DROP.
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][DROPPED] src={src_leg_id} activation "
                            f"past {ADJ_CUTOFF_MIN//60:02d}:{ADJ_CUTOFF_MIN%60:02d} IST")
            record_alert("IC_ADJ_DROPPED",
                         f"{self.strategy_id} adjustment for {src_leg_id} dropped — "
                         f"activation past session cutoff",
                         severity="warning", strategy_id=self.strategy_id)
            return

        # ── DA3: fresh chain snapshot, fail CLOSED ─────────────────────
        if self._chain_provider is None:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][NO_CHAIN_PROVIDER] cannot select strike → drop")
            record_alert("IC_ADJ_NO_STRIKE",
                         f"{self.strategy_id} adjustment for {src_leg_id} dropped — no chain provider",
                         severity="error", strategy_id=self.strategy_id)
            return
        try:
            expiry, ce_cands, pe_cands, tokens = self._chain_provider()
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][CHAIN_FAIL] {e!r} → drop")
            record_alert("IC_ADJ_NO_STRIKE",
                         f"{self.strategy_id} adjustment for {src_leg_id} dropped — chain snapshot failed",
                         severity="error", strategy_id=self.strategy_id)
            return
        if expiry is None:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][CHAIN_EMPTY] → drop")
            record_alert("IC_ADJ_NO_STRIKE",
                         f"{self.strategy_id} adjustment for {src_leg_id} dropped — empty chain",
                         severity="error", strategy_id=self.strategy_id)
            return

        cands = ce_cands if src.opt_type == "CE" else pe_cands
        pick = select_strike(cands, cap=float(acfg.get("premium_max") or 0),
                             fallback_cheapest=False)
        if pick is None:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][NO_STRIKE] src={src_leg_id} "
                            f"cap={acfg.get('premium_max')} → drop (fail closed)")
            record_alert("IC_ADJ_NO_STRIKE",
                         f"{self.strategy_id} adjustment for {src_leg_id}: no strike ≤ "
                         f"₹{acfg.get('premium_max')} — dropped",
                         severity="warning", strategy_id=self.strategy_id)
            return

        lot_size = self._lot_size(cfg)
        freeze   = self._freeze_qty(cfg)
        qty      = int(acfg["lots"]) * lot_size
        try:
            slices = slice_qty(qty, freeze, lot_size)
        except ValueError as e:
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][CONFIG_FAIL] {e} → drop")
            return

        aid = f"{src_leg_id}A"
        leg = LegCore(
            leg_id=aid, action="BUY", opt_type=src.opt_type,
            symbol=pick.symbol, qty=qty, entry_price=pick.ltp,
            sl=sl_price("BUY", pick.ltp, float(acfg.get("sl_val") or 0),
                        acfg.get("sl_mode", "pct")),
            tp=tp_price("BUY", pick.ltp, float(acfg.get("tp_val") or 0),
                        acfg.get("tp_mode", "pct")),
            is_adjust=True, adjust_of=src_leg_id,
            entry_date=now.strftime("%Y-%m-%d"),
            expiry=expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry),
        )
        self._rt[aid] = {
            "token": tokens.get(pick.symbol, 0),
            "slices": slices, "order_ids": [], "gtt_ids": [],
            "db_id": None, "paper": self._paper, "phantom": False,
        }
        entry_ts = int(time.time())

        if self._paper:
            core.add_adjust_leg(leg)
            self._insert_row(leg, entry_ts, order_id="PAPER")
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][PAPER] {aid} {leg.symbol}@{leg.entry_price} "
                            f"qty={qty} sl={leg.sl} tp={leg.tp}")
        else:
            # LIVE: place + confirm synchronously (engine thread, house-OK)
            ok = self._place_and_confirm(leg)
            if not ok:
                self._rt.pop(aid, None)
                write_audit_log(f"[IC][{self.strategy_id}][ADJ][LIVE][DEAD] {aid} entry failed → dropped")
                record_alert("IC_ADJ_DROPPED",
                             f"{self.strategy_id} adjustment {aid} entry failed — dropped",
                             severity="error", strategy_id=self.strategy_id, mode="live")
                return
            core.add_adjust_leg(leg)
            # recompute SL/TP off the INTENDED entry (pick LTP) — fill-
            # independent, house pattern (already set at construction).
            self._protect_adjust_long(leg)
            self._insert_row(leg, entry_ts, order_id=(
                self._rt[aid]["order_ids"][0] if self._rt[aid]["order_ids"] else ""))
            write_audit_log(f"[IC][{self.strategy_id}][ADJ][LIVE] {aid} {leg.symbol}@{leg.entry_price} "
                            f"qty={qty} sl={leg.sl} tp={leg.tp} "
                            f"gtts={self._rt[aid]['gtt_ids']}")

        try:
            notify_trade_entry({
                "strategy_id": self.strategy_id,
                "mode": "paper" if self._paper else "live",
                "symbol": leg.symbol, "side": aid,
                "entry_price": leg.entry_price, "quantity": leg.qty,
                "sl": leg.sl, "tp": leg.tp,
            })
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][TG_ENTRY_FAIL] {e}")
        record_alert("IC_ADJUST",
                     f"{self.strategy_id} adjustment {aid}: BUY {leg.symbol} @ {leg.entry_price}",
                     severity="info", strategy_id=self.strategy_id,
                     symbol=leg.symbol,
                     mode="paper" if self._paper else "live")
        self._persist_session()   # IC_RESTART

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
            # GTT filled at broker → run the FULL stop path with a known fill
            self._short_stop_path(leg, ltp_hint=exit_price,
                                  already_filled_at=exit_price)
            return
        with self._mutex:
            if leg.state != L_OPEN:
                return
        core.close_leg(leg_id, exit_price, reason)
        self._finish_close(leg)
        core.finalize_if_done()
        self._after_close_housekeeping()

    # ==================================================================
    # GENERIC CLOSE / FORCED EXITS
    # ==================================================================

    def _close_leg(self, leg_id: str, *, reason: str, ltp_hint=None,
                   force=False, strict=False) -> bool:
        """Returns True when the leg is CLOSED after this call (or already
        was). strict=True (morning square-off): a live order failure leaves
        the leg OPEN for retry instead of booking a fictitious close."""
        core = self._core
        leg = core.legs.get(leg_id)
        if leg is None:
            return True
        with self._mutex:
            if leg.state != L_OPEN:
                return True
            if self._rt[leg_id].get("_exiting"):
                return False
            self._rt[leg_id]["_exiting"] = True

        if self._paper or self._rt[leg_id].get("phantom"):
            px = self._premium(leg.symbol, ltp_hint) or leg.entry_price
        else:
            status, px = self._flatten_live(leg, reason=reason, force=force,
                                            strict=strict)
            if status == "DEFER":
                return False   # armed GTT owns this exit; backstop closes it
            if status == "FAIL":
                # strict path: order could not be placed — leg stays OPEN,
                # _exiting cleared for the next retry iteration.
                with self._mutex:
                    self._rt[leg_id]["_exiting"] = False
                return False
            if px is None:
                px = self._premium(leg.symbol, ltp_hint) or leg.entry_price
        core.close_leg(leg_id, px, reason)
        self._finish_close(leg)
        return True

    def force_square_off_all(self, reason: str = "EOD") -> int:
        """MTM / MANUAL / legacy-EOD — flatten everything, shorts first."""
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
        self._after_close_housekeeping()
        write_audit_log(f"[IC][{self.strategy_id}][SQUAREOFF] reason={reason} closed={n} state={core.state}")
        return n

    # ── IC_V2 ── DA5: expiry-day 15:28 closes ONLY legs entered today ──
    def expiry_square_off(self, today_str: str) -> int:
        core = self._core
        if core is None or core.state not in (G_OPEN, G_CLOSING):
            return 0
        targets = [l for l in core.open_legs()
                   if l.expiry == today_str and l.entry_date == today_str]
        if not targets:
            return 0
        targets.sort(key=lambda l: (0 if l.is_short else 1, l.leg_id))
        n = 0
        for leg in targets:
            if self._close_leg(leg.leg_id, reason="EOD", force=True):
                n += 1
        core.finalize_if_done()
        self._after_close_housekeeping()
        write_audit_log(f"[IC][{self.strategy_id}][EXPIRY_SQUAREOFF] date={today_str} closed={n}")
        return n

    # ==================================================================
    # IC_V2 — ONE_NIGHT_MAX: carry commit / restore / morning close
    # ==================================================================

    def commit_carry(self, mode: str) -> bool:
        """Session end, non-expiry entry day: persist the open legs. Legs
        stay OPEN in memory (evening display); GTTs stay armed at the broker
        (D3). Pending MTC/ADJ are DROPPED (backtest parity)."""
        core = self._core
        if core is None or core.state not in (G_OPEN, G_CLOSING):
            return False
        if self._carry_committed:
            return True
        if self._pending_mtc or self._pending_adjust:
            write_audit_log(f"[IC][{self.strategy_id}][CARRY] dropping pending at commit "
                            f"mtc={list(self._pending_mtc)} "
                            f"adjust={list(self._pending_adjust)} (parity: "
                            f"pendings never survive the session)")
            self._pending_mtc.clear()
            self._pending_adjust.clear()
        try:
            leg_dicts = core.carry_snapshot()   # DA5 assert inside
        except RuntimeError as e:
            write_audit_log(f"[IC][{self.strategy_id}][CARRY][ASSERT_FAIL] {e}")
            try:
                notify_critical({"message": f"{self.strategy_id} CARRY ASSERT: {e}. "
                                            f"NOT carrying — investigate now.",
                                 "severity": "error"})
            except Exception:
                pass
            return False
        if not leg_dicts:
            return False
        rt_out = {}
        for d in leg_dicts:
            lid = d["leg_id"]
            rt = self._rt.get(lid) or {}
            rt_out[lid] = {
                "token": rt.get("token", 0),
                "slices": list(rt.get("slices") or []),
                "gtt_ids": list(rt.get("gtt_ids") or []),
                "db_id": rt.get("db_id"),
                "phantom": bool(rt.get("phantom")),
            }
        payload = {
            "entry_date": _now_ist().strftime("%Y-%m-%d"),
            "committed_at": _now_ist().isoformat(timespec="seconds"),
            "group_id": self._group_id,
            "paper": self._paper,
            "mode": mode,
            "adjust_only": self._adjust_only,
            "mtc_fired": core.mtc_fired,
            "double_sl_minute": core.double_sl_minute,
            "legs": leg_dicts,
            "rt": rt_out,
        }
        ok = ic_carry_store.save_carry(self.strategy_id, payload)
        if not ok:
            try:
                notify_critical({"message":
                    f"{self.strategy_id}: CARRY SNAPSHOT SAVE FAILED — an app restart "
                    "tonight will FORGET the open overnight position. Fix "
                    "disk/permissions or square off manually.",
                    "severity": "error"})
            except Exception:
                pass
            record_alert("IC_CARRY_FAIL", f"{self.strategy_id} carry snapshot save FAILED",
                         severity="error", strategy_id=self.strategy_id)
            return False
        self._carry_committed = True
        self._carry_entry_date = payload["entry_date"]
        ic_carry_store.clear_session(self.strategy_id)   # IC_RESTART: carry file takes over
        record_alert("IC_CARRY",
                     f"{self.strategy_id} carrying {len(leg_dicts)} leg(s) overnight — "
                     f"closes at next session open window.",
                     severity="info", strategy_id=self.strategy_id,
                     mode="paper" if self._paper else "live")
        try:
            notify_manual_exit({   # reuse generic notifier as an FYI channel
                "strategy_id": self.strategy_id,
                "mode": "paper" if self._paper else "live",
                "symbol": ", ".join(d["symbol"] for d in leg_dicts),
                "side": "CARRY",
                "entry_price": None, "exit_price": None,
                "quantity": sum(int(d["qty"]) for d in leg_dicts),
                "pnl": None, "reason": "OVERNIGHT_CARRY_COMMITTED",
            })
        except Exception:
            pass
        return True

    def restore_carry_payload(self, payload: dict) -> bool:
        """Boot-time restore (DA1). Rebuilds core+rt with carried legs."""
        with self._mutex:
            if self._core is not None and self._core.state in (G_OPEN, G_CLOSING):
                write_audit_log(f"[IC][{self.strategy_id}][CARRY][RESTORE] active group present — "
                                "refusing to overwrite")
                return False
            try:
                legs = payload.get("legs") or []
                core = GroupCore.restore_carry(
                    legs,
                    mtc_fired=bool(payload.get("mtc_fired")),
                    double_sl_minute=bool(payload.get("double_sl_minute")),
                )
                if not core.legs:
                    return False
                rt_in = payload.get("rt") or {}
                rt: Dict[str, dict] = {}
                for lid in core.legs:
                    r = rt_in.get(lid) or {}
                    rt[lid] = {
                        "token": int(r.get("token") or 0),
                        "slices": list(r.get("slices") or []),
                        "order_ids": [],
                        "gtt_ids": [str(g) for g in (r.get("gtt_ids") or [])],
                        "db_id": r.get("db_id"),
                        "paper": bool(payload.get("paper", True)),
                        "phantom": bool(r.get("phantom")),
                    }
                self._core = core
                self._rt = rt
                self._paper = bool(payload.get("paper", True))
                self._adjust_only = bool(payload.get("adjust_only"))
                self._group_id = payload.get("group_id")
                self._carry_committed = True   # already persisted
                self._carry_entry_date = str(payload.get("entry_date") or "") or None
                self._pending_mtc.clear()
                self._pending_adjust.clear()
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][CARRY][RESTORE_FAIL] {e!r}")
                return False
        write_audit_log(
            f"[IC][CARRY][RESTORED] group_id={self._group_id} "
            f"entry_date={payload.get('entry_date')} "
            f"legs={list(self._core.legs)} paper={self._paper}"
        )
        return True

    # ── IC_RESTART (2026-07-31) ── mid-session persistence. The 12:07
    # restart incident: the group lived only in memory (carry file exists
    # only after 15:30:30), so a mid-day restart left open legs completely
    # unmanaged — no SL evaluation, no MTC, no adjustments, no session-end
    # handling. Session snapshot is written on EVERY group mutation and
    # cleared when the group finalizes (or when the carry commit
    # supersedes it).

    def _persist_session(self):
        core = self._core
        if core is None:
            return
        if core.state in (G_CLOSED, G_ABORTED):
            ic_carry_store.clear_session(self.strategy_id)
            return
        rt_out = {}
        for lid in core.legs:
            rt = self._rt.get(lid) or {}
            rt_out[lid] = {
                "token": rt.get("token", 0),
                "slices": list(rt.get("slices") or []),
                "gtt_ids": list(rt.get("gtt_ids") or []),
                "db_id": rt.get("db_id"),
                "phantom": bool(rt.get("phantom")),
            }
        ic_carry_store.save_session(self.strategy_id, {
            "entry_date": (next((l.entry_date for l in core.legs.values()
                                 if l.entry_date), None)
                           or _now_ist().strftime("%Y-%m-%d")),
            "saved_at": _now_ist().isoformat(timespec="seconds"),
            "group_id": self._group_id,
            "paper": self._paper,
            "adjust_only": self._adjust_only,
            "core": core.session_snapshot(),
            "rt": rt_out,
            "pending_mtc": dict(self._pending_mtc),
            "pending_adjust": dict(self._pending_adjust),
        })

    def restore_session_payload(self, payload: dict, *,
                                adopt_as_carry: bool = False) -> bool:
        """Boot-time mid-session restore. adopt_as_carry=True (prior-day
        snapshot without a carry commit — the app died in the evening): the
        open legs are marked carried and the carry-morning machine closes
        them at the next open window (ONE_NIGHT_MAX enforcement on a
        crashed book). Pendings restore only for same-day (a prior-day
        pending MTC/ADJ is meaningless)."""
        with self._mutex:
            if self._core is not None and self._core.state in (G_OPEN, G_CLOSING):
                write_audit_log(f"[IC][{self.strategy_id}][SESSION][RESTORE] active group present "
                                "— refusing to overwrite")
                return False
            try:
                core = GroupCore.restore_session(payload.get("core") or {})
                if not core.legs:
                    return False
                rt_in = payload.get("rt") or {}
                rt: Dict[str, dict] = {}
                for lid in core.legs:
                    r = rt_in.get(lid) or {}
                    rt[lid] = {
                        "token": int(r.get("token") or 0),
                        "slices": list(r.get("slices") or []),
                        "order_ids": [],
                        "gtt_ids": [str(g) for g in (r.get("gtt_ids") or [])],
                        "db_id": r.get("db_id"),
                        "paper": bool(payload.get("paper", True)),
                        "phantom": bool(r.get("phantom")),
                    }
                self._core = core
                self._rt = rt
                self._paper = bool(payload.get("paper", True))
                self._adjust_only = bool(payload.get("adjust_only"))
                self._group_id = payload.get("group_id")
                self._pending_mtc.clear()
                self._pending_adjust.clear()
                if adopt_as_carry:
                    for leg in core.open_legs():
                        leg.carried = True
                    self._carry_committed = True
                    self._carry_entry_date = \
                        str(payload.get("entry_date") or "") or None
                else:
                    self._carry_committed = False
                    self._carry_entry_date = None
                    self._pending_mtc.update(
                        {str(k): int(v) for k, v in
                         (payload.get("pending_mtc") or {}).items()})
                    self._pending_adjust.update(
                        {str(k): int(v) for k, v in
                         (payload.get("pending_adjust") or {}).items()})
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][SESSION][RESTORE_FAIL] {e!r}")
                return False
        write_audit_log(
            f"[IC][SESSION][RESTORED] group_id={self._group_id} "
            f"entry_date={payload.get('entry_date')} "
            f"legs={list(self._core.legs)} adopt_as_carry={adopt_as_carry} "
            f"pending_mtc={list(self._pending_mtc)} "
            f"pending_adjust={list(self._pending_adjust)}"
        )
        return True

    def has_carried_open(self) -> bool:
        core = self._core
        if core is None:
            return False
        return any(l.carried for l in core.open_legs())

    # ── first-candle rule: pre-market GTT teardown ─────────────────────
    def premarket_cancel_gtts(self) -> bool:
        """Cancel (verified) every carried leg's GTTs so nothing can fire in
        09:15–09:16. Returns True when NO GTT remains armed. Paper/phantom:
        trivially True. Idempotent."""
        core = self._core
        if core is None:
            return True
        all_clear = True
        for leg in core.open_legs():
            if not leg.carried:
                continue
            rt = self._rt.get(leg.leg_id) or {}
            if self._paper or rt.get("phantom"):
                rt["gtt_ids"] = []
                continue
            for gid in list(rt.get("gtt_ids") or []):
                gone = False
                try:
                    gone = self.executor.cancel_gtt_verified(gid)
                except Exception as e:
                    write_audit_log(f"[IC][{self.strategy_id}][PREMARKET][CANCEL_ERR] gtt={gid} {e}")
                if gone:
                    rt["gtt_ids"].remove(gid)
                else:
                    all_clear = False
                    write_audit_log(f"[IC][{self.strategy_id}][PREMARKET][CANCEL_UNVERIFIED] "
                                    f"gtt={gid} on {leg.symbol} still armed")
        if not all_clear:
            try:
                notify_critical({"message":
                    f"{self.strategy_id}: could not cancel all overnight GTT(s) pre-market. "
                    "A GTT may fire inside 09:15–09:16 (first-candle rule "
                    "violated). Check Kite GTTs now.",
                    "severity": "error"})
            except Exception:
                pass
            record_alert("IC_PREMARKET_GTT",
                         f"{self.strategy_id}: overnight GTT cancel incomplete pre-market",
                         severity="error", strategy_id=self.strategy_id)
        else:
            write_audit_log(f"[IC][{self.strategy_id}][PREMARKET] overnight GTTs cleared")
        return all_clear

    def morning_square_off(self) -> int:
        """From next_open_time: STRICT market close of every carried leg,
        shorts first. The engine calls this repeatedly until it returns 0
        (retry loop, DA2). GTTs are never re-armed while this runs (double-
        buy hazard). Returns the number of carried legs STILL OPEN."""
        core = self._core
        if core is None:
            return 0
        carried = [l for l in core.open_legs() if l.carried]
        if not carried:
            core.finalize_if_done()
            self._after_close_housekeeping()
            return 0
        with self._mutex:
            if core.state == G_OPEN:
                core.state = G_CLOSING
        carried.sort(key=lambda l: (0 if l.is_short else 1, l.leg_id))
        for leg in carried:
            self._close_leg(leg.leg_id, reason="NEXT_OPEN", force=True, strict=True)
        core.finalize_if_done()
        self._after_close_housekeeping()
        remaining = sum(1 for l in core.open_legs() if l.carried)
        if remaining == 0:
            write_audit_log(f"[IC][{self.strategy_id}][MORNING] carry fully closed (NEXT_OPEN)")
        return remaining

    def _after_close_housekeeping(self):
        """Whenever the group could have finalized: a finalized group with a
        carry/session snapshot on disk must clear it, else a stale snapshot
        restores ghost legs at the next boot. A still-open group persists
        its session snapshot here (IC_RESTART)."""
        core = self._core
        if core is None:
            return
        if core.state in (G_CLOSED, G_ABORTED):
            if ic_carry_store.carry_exists(self.strategy_id):
                ic_carry_store.clear_carry(self.strategy_id)
            ic_carry_store.clear_session(self.strategy_id)
        else:
            self._persist_session()

    def _flatten_live(self, leg: LegCore, *, reason: str, force: bool,
                      strict: bool = False):
        """
        Cancel this leg's GTTs (verified), then flatten at market/protected.
        force=True (EOD/MTM/UNWIND/NEXT_OPEN): flatten even if a GTT stays
        armed (CRITICAL alert). force=False (tick SL/TP path): armed GTT →
        DEFER. strict=True: an ORDER-PLACEMENT failure returns ("FAIL",None)
        so the caller leaves the leg OPEN for retry (morning close).
        Returns ("FLAT", price_hint|None) | ("DEFER", None) | ("FAIL", None).
        """
        rt = self._rt[leg.leg_id]
        for gid in list(rt["gtt_ids"]):
            gone = False
            try:
                gone = self.executor.cancel_gtt_verified(gid)
            except Exception as e:
                write_audit_log(f"[IC][{self.strategy_id}][FLATTEN][CANCEL_ERR] gtt={gid} {e}")
            if gone:
                rt["gtt_ids"].remove(gid)
                continue
            if not force:
                write_audit_log(f"[IC][{self.strategy_id}][FLATTEN][DEFER] {leg.symbol} gtt={gid} still armed "
                                f"— deferring to GTT/backstop (no double-fire)")
                rt["_exiting"] = False    # allow backstop to run the close
                return ("DEFER", None)
            try:
                notify_critical({"message":
                    f"{self.strategy_id} {reason}: GTT {gid} on {leg.symbol} could not be cancelled "
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
            write_audit_log(f"[IC][{self.strategy_id}][FLATTEN][ORDER_FAIL] {leg.symbol} {e}"
                            + (" — STRICT: leg stays OPEN for retry" if strict else ""))
            if strict:
                return ("FAIL", None)
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
                write_audit_log(f"[IC][{self.strategy_id}][LTP_RESOLVER_ERR] {symbol} {e}")
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
        if rt.get("phantom"):
            return   # ADJ_ONLY: suppressed core legs are never booked
        direction = "SHORT" if leg.is_short else "LONG"
        cfg = self._cfg()
        lot_size = self._lot_size(cfg)
        group_id = self._group_id
        try:
            if self._paper:
                pid = str(uuid.uuid4())
                insert_paper_trade(
                    paper_trade_id=pid, strategy_name=self.strategy_id,
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
                    trade_id=tid, strategy_id=self.strategy_id,
                    # ── IC_SLOT_NS ── uniq_open_trade_per_slot is UNIQUE(slot)
                    # WHERE exit_time IS NULL — on slot ALONE. Bare "L1".."L4"
                    # collides across IC_V1/IC_V2 (and with V2 overnight-carry
                    # rows), silently rejecting the second entrant's inserts.
                    # Prefix with strategy_id (TSG_LIVE_BOOK convention); closes
                    # are unaffected — they go by rt["db_id"], never by slot.
                    slot=f"{self.strategy_id}_{leg.leg_id}",
                    symbol=leg.symbol, token=rt["token"],
                    entry_price=leg.entry_price, qty=leg.qty,
                    buy_order_id=order_id, sl_price=leg.sl or 0.0,
                    tp_price=leg.tp or 0.0, tp_mode="GTT",
                    state="PROTECTED", trade_direction=direction,
                    group_id=group_id, trade_class=leg.leg_id,
                )
                rt["db_id"] = tid
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][DB_INSERT_FAIL][{leg.leg_id}] {e}")

    def _finish_close(self, leg: LegCore):
        rt = self._rt.get(leg.leg_id) or {}
        if rt.get("phantom"):
            write_audit_log(
                f"[IC][LEG_CLOSE][PHANTOM] {leg.leg_id} {leg.symbol} "
                f"reason={leg.exit_reason} exit={leg.exit_price} pnl={leg.pnl()} "
                f"(ADJ_ONLY simulation — not booked)"
            )
            return
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
            write_audit_log(f"[IC][{self.strategy_id}][DB_CLOSE_FAIL][{leg.leg_id}] {e}")

        payload = {
            "strategy_id": self.strategy_id,
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
            write_audit_log(f"[IC][{self.strategy_id}][TG_EXIT_FAIL] {e}")
        write_audit_log(
            f"[IC][LEG_CLOSE] {leg.leg_id} {leg.symbol} reason={leg.exit_reason} "
            f"exit={leg.exit_price} pnl={leg.pnl()}"
        )

    def _notify_entry(self, mode: str):
        # ── GROUP_ENTRY BEGIN ──
        # WAS: one notify_trade_entry per leg -> 4 near-identical messages
        # with no basket context. NOW: one composite basket message. The
        # in-app feed also gets ONE event (fired inside notify_group_entry),
        # so a condor entry is one tone, not four.
        # Phantom legs stay excluded, exactly as before.
        legs = []
        for leg in self._core.legs.values():
            if (self._rt.get(leg.leg_id) or {}).get("phantom"):
                continue
            legs.append({
                "leg_id":      leg.leg_id,
                "action":      leg.action,
                "opt_type":    leg.opt_type,
                "symbol":      leg.symbol,
                "qty":         leg.qty,
                "entry_price": leg.entry_price,
            })
        if not legs:
            return
        sl_bits = [f"{l.leg_id} {l.sl:,.2f}"
                   for l in self._core.legs.values()
                   if l.is_short and l.sl
                   and not (self._rt.get(l.leg_id) or {}).get("phantom")]
        expiry = next((l.expiry for l in self._core.legs.values()
                       if l.expiry), "")
        try:
            notify_group_entry({
                "strategy_id":    self.strategy_id,   # label = codename (UI_MASK)
                "mode":           mode,
                "expiry":         expiry,
                "risk":           ([["Short SL", " · ".join(sl_bits)]]
                                   if sl_bits else []),
                "legs":           legs,
            })
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][TG_ENTRY_FAIL] {e}")
        # ── GROUP_ENTRY END ──

    def _margin_ok(self, core: GroupCore, cfg) -> bool:
        """D8 — ADVISORY-FAIL-OPEN: only a CONFIRMED shortfall blocks."""
        if not cfg.get("margin_guard", True):
            return True
        fn = getattr(self.executor, "get_basket_margin", None)
        if fn is None:
            write_audit_log(f"[IC][{self.strategy_id}][MARGIN] get_basket_margin unavailable — proceeding (fail open)")
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
                write_audit_log(f"[IC][{self.strategy_id}][MARGIN][BLOCK] required={required:.0f} "
                                f"available={available:.0f}")
                return False
            return True
        except Exception as e:
            write_audit_log(f"[IC][{self.strategy_id}][MARGIN][ERR] {e} — proceeding (fail open)")
            return True

    # ==================================================================
    # KILL SWITCH (2026-07-26 lock: overrides EVERYTHING, incl. the
    # first-candle rule — a human pressing KILL means NOW)
    # ==================================================================

    def kill_all(self) -> dict:
        """
        Emergency stop for the active group. Sequence (approved doctrine):
          1. Drop pending MTC/ADJ (nothing new may fire mid-kill).
          2. Cancel EVERY open leg's GTTs, cancel-VERIFIED. ANY unverified
             cancel → ABORT before flattening (never market-out against an
             armed GTT — the double-fire accidental-position hazard) and
             report which GTTs survived for manual deletion in Kite.
          3. force_square_off_all(reason="MANUAL") — shorts first; carried
             legs close too (kill overrides the 09:16 wait).
          4. Housekeeping clears the carry snapshot once finalized;
             pendings stay cleared.
        Returns {"ok", "closed", "remaining", "stuck_gtts": [...]}.
        Never flips the mode — the kill route owns the PAPER flip, and only
        after this reports fully flat.
        """
        core = self._core
        if core is None or core.state not in (G_OPEN, G_CLOSING):
            return {"ok": True, "closed": 0, "remaining": 0, "stuck_gtts": []}

        write_audit_log(f"[IC][{self.strategy_id}][KILL] initiated — pendings dropped "
                        f"mtc={list(self._pending_mtc)} "
                        f"adjust={list(self._pending_adjust)}")
        self._pending_mtc.clear()
        self._pending_adjust.clear()

        # ── 2: full GTT sweep, abort on ANY survivor ──
        stuck: list = []
        if not self._paper:
            for leg in core.open_legs():
                rt = self._rt.get(leg.leg_id) or {}
                if rt.get("phantom"):
                    continue
                for gid in list(rt.get("gtt_ids") or []):
                    gone = False
                    try:
                        gone = self.executor.cancel_gtt_verified(gid)
                    except Exception as e:
                        write_audit_log(f"[IC][{self.strategy_id}][KILL][CANCEL_ERR] gtt={gid} {e}")
                    if gone:
                        rt["gtt_ids"].remove(gid)
                    else:
                        stuck.append({"leg_id": leg.leg_id,
                                      "symbol": leg.symbol, "gtt_id": str(gid)})
        if stuck:
            write_audit_log(f"[IC][{self.strategy_id}][KILL][ABORT] unverified GTT cancels: {stuck} "
                            f"— NOT flattening (armed-GTT double-fire hazard)")
            try:
                notify_critical({"message":
                    f"{self.strategy_id} KILL ABORTED: could not cancel GTT(s) "
                    + ", ".join(f"{s['gtt_id']}({s['symbol']})" for s in stuck)
                    + ". DELETE THEM MANUALLY in Kite, then press KILL again.",
                    "severity": "error"})
            except Exception:
                pass
            record_alert("IC_KILL_ABORT",
                         f"{self.strategy_id} kill aborted — {len(stuck)} GTT(s) still "
                         f"armed. Delete manually, then retry.",
                         severity="error", strategy_id=self.strategy_id)
            remaining = len(core.open_legs())
            return {"ok": False, "closed": 0, "remaining": remaining,
                    "stuck_gtts": stuck}

        # ── 3: flatten everything (reason MANUAL — existing DB vocabulary) ──
        closed = self.force_square_off_all(reason="MANUAL")
        remaining = len(core.open_legs())
        write_audit_log(f"[IC][{self.strategy_id}][KILL] square-off closed={closed} "
                        f"remaining={remaining} state={core.state}")
        record_alert("IC_KILL",
                     f"{self.strategy_id} KILL: {closed} leg(s) closed"
                     + (f", {remaining} STILL OPEN" if remaining else ""),
                     severity="error" if remaining else "info",
                     strategy_id=self.strategy_id)
        return {"ok": remaining == 0, "closed": closed,
                "remaining": remaining, "stuck_gtts": []}

    # ==================================================================
    # INSPECTION (API / UI / monitor)
    # ==================================================================

    def current_group(self) -> Optional[GroupCore]:
        return self._core

    def leg_runtime(self, leg_id: str) -> dict:
        return dict(self._rt.get(leg_id) or {})

    def is_paper(self) -> bool:
        return self._paper

    def is_adjust_only(self) -> bool:
        return self._adjust_only

    def carry_committed(self) -> bool:
        return self._carry_committed

    def carry_entry_date(self):
        """'YYYY-MM-DD' the carried legs were entered, or None."""
        return self._carry_entry_date

    def pending_view(self) -> dict:
        return {
            "mtc": dict(self._pending_mtc),
            "adjust": dict(self._pending_adjust),
        }

    def has_open_group(self) -> bool:
        return self._core is not None and self._core.state in (G_OPEN, G_CLOSING)