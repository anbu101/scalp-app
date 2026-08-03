# backend/app/engine/tsg/tsg_live_core.py
#
# TSG_V1 — PURE live core (no app imports, unit-tested)
# ============================================================================
# Mirrors backend/app/backtest/tsg/backtest_tsg_runner.py semantics for every
# shared rule (strike selection, MTM math, exit precedence, IV9/IV10/IV11,
# one-shot IV disarm, partial-exit day-MTM composition, exit-reason
# vocabulary), plus the LIVE-ONLY decisions locked with Anbu on 2026-08-02
# (LD1–LD10). Phase 1 scope: PAPER + dashboard; the LIVE order path reuses
# the same decisions (LD1).
#
#   LD2  Evaluation cadence : wrapper ticks once per minute at hh:mm:02 and
#        feeds the LAST COMPLETED 1m candle closes (marks) + solved IVs.
#        The core is fed data; it never fetches. This is what makes live
#        decisions equal backtest decisions given equal data.
#   LD3  Entry              : select from a REAL premium ladder only — no
#        synthetic legs live. No qualifying short strike → NO_ENTRY (skip
#        day, alert). Wing with no strike <= cap → wing ABSENT (short
#        exits alone on IV, backtest IV8 parity). Buys before sells is a
#        wrapper/executor concern.
#   LD4  Exits              : per-minute precedence MTM_SL → MTM_TARGET →
#        IV (target kept for parity; production config runs target=0).
#        TRAILING LOCK deliberately NOT ported (backtest verdict
#        2026-08-02: every grid cell lost to baseline). EOD is wrapper
#        time-driven via eod_due().
#   LD5  Expiry-day lots    : resolve_lots() picks expiry_lots on the
#        contract's expiry day (era handled by the caller via the expiry
#        calendar). Live/paper-only knob — backtest intentionally lacks it.
#   LD6  State machine      : IDLE → ENTERING → OPEN → PARTIAL → CLOSING →
#        CLOSED (ABORTED on failed entry → unwind). to_state()/from_state()
#        round-trips for restart resume.
#   LD7  Kill               : core exposes kill_exit_ids() (everything
#        open); verified-flat / mode-flip ordering is the wrapper's duty.
#
# BACKTEST-PARITY DIVERGENCE LEDGER (documented, intentional):
#   - Entry price : live fill (market, ~09:16:02) vs backtest prev-candle
#     close. Entry IV anchors solve from the ACTUAL fill.
#   - Exit fills  : live market-order fills vs backtest at that candle's
#     close. Slippage on fast moves makes live MTM_SL days worse than
#     booked backtest ones — treat backtest SL losses as a floor (noted
#     2026-08-02 KPI analysis).
#   - IV inputs   : live parity spot + tau solved off the same 1m closes
#     the wrapper feeds for marks; a data-kite gap on a minute = skipped
#     IV check that minute (backtest iv_solve_fail parity).
#
# Exit reason vocabulary (MUST stay identical to backtest):
#   MTM_SL, MTM_TARGET, IV_SL, IV_SL_HEDGE, EOD
# Live-only additions: UNWIND, KILL, MANUAL, BROKER_EXIT.
# ============================================================================

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

PRICE_FLOOR = 0.05

# ── Day / leg lifecycle states ──────────────────────────────────────────────
D_IDLE     = "IDLE"
D_ENTERING = "ENTERING"
D_OPEN     = "OPEN"          # all entered legs open
D_PARTIAL  = "PARTIAL"       # after an IV pair exit; survivors running
D_CLOSING  = "CLOSING"
D_CLOSED   = "CLOSED"
D_ABORTED  = "ABORTED"       # entry failed → unwound
D_SKIPPED  = "SKIPPED"       # no qualifying short strike today (LD3)

L_PENDING  = "PENDING_ENTRY"
L_OPEN     = "OPEN"
L_CLOSED   = "CLOSED"
L_DEAD     = "DEAD"


# ── shared selection rule (backtest select_strike parity, real-only) ───────
def select_strike(candidates: List[Tuple[str, float]], premium_max: float,
                  fallback_cheapest: bool = False
                  ) -> Optional[Tuple[str, float, bool]]:
    """Highest premium <= cap; optionally the cheapest strike as a flagged
    fallback when nothing qualifies. candidates = [(symbol, ltp/close)]."""
    pool = [(s, p) for s, p in candidates if p is not None and p > 0]
    ok = [(s, p) for s, p in pool if p <= premium_max]
    if ok:
        s, p = max(ok, key=lambda x: x[1])
        return (s, p, False)
    if fallback_cheapest and pool:
        s, p = min(pool, key=lambda x: x[1])
        return (s, p, True)
    return None


def resolve_lots(base_lots: int, expiry_lots: Optional[int],
                 is_expiry_day: bool) -> int:
    """LD5: expiry-day lots override. expiry_lots None/0 → base_lots."""
    if is_expiry_day and expiry_lots:
        return int(expiry_lots)
    return int(base_lots)


def leg_mtm(action: str, entry: float, mark: float, qty: int) -> float:
    d = (entry - mark) if action == "SELL" else (mark - entry)
    return d * qty


# ── Leg ─────────────────────────────────────────────────────────────────────
@dataclass
class TsgLeg:
    leg_id: str                    # L1..L4
    action: str                    # SELL | BUY
    opt_type: str                  # CE | PE
    symbol: str = ""
    strike: float = 0.0
    expiry: str = ""               # ISO
    qty: int = 0
    state: str = L_PENDING
    entry_price: Optional[float] = None
    entry_order_id: Optional[str] = None
    entry_iv: Optional[float] = None       # shorts only; None = unmonitored
    iv_threshold: Optional[float] = None   # decimal; None = unmonitored
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_ts: Optional[int] = None
    last_mark: Optional[float] = None      # dashboard convenience
    last_iv: Optional[float] = None        # dashboard convenience (shorts)

    @property
    def is_short(self) -> bool:
        return self.action == "SELL"

    def pnl(self, mark: Optional[float] = None) -> Optional[float]:
        px = self.exit_price if self.state == L_CLOSED else (
            mark if mark is not None else self.last_mark)
        if self.entry_price is None or px is None:
            return None
        return leg_mtm(self.action, self.entry_price, px, self.qty)

    def to_state(self) -> dict:
        return asdict(self)

    @classmethod
    def from_state(cls, d: dict) -> "TsgLeg":
        return cls(**d)


# ── Day core ────────────────────────────────────────────────────────────────
@dataclass
class TsgDayCore:
    """One trading day's basket. The wrapper owns time, data, orders and
    persistence; the core owns every DECISION. Feed it completed-candle
    marks/IVs once per minute; it answers with exits to perform."""
    mtm_sl: float = 35000.0            # ₹, 0 = off
    mtm_target: float = 0.0            # ₹, 0 = off (production: 0)
    iv_sl_pct: float = 0.0             # absolute %, 0 = off
    iv_sl_delta_pts: float = 4.0       # relative pts, precedence (IV11)
    state: str = D_IDLE
    legs: Dict[str, TsgLeg] = field(default_factory=dict)
    realized: float = 0.0              # closed-leg P&L (IV6 composition)
    peak_mtm: float = float("-inf")
    trough_mtm: float = float("inf")
    iv_armed_used: bool = False        # IV4 one-shot latch
    entry_date: str = ""               # ISO, for restart-resume sanity
    skip_reason: Optional[str] = None

    # ── entry ───────────────────────────────────────────────────────────
    def plan_entry(self, ladder: Dict[str, List[Tuple[str, float]]],
                   legs_cfg: List[dict], lot_size: int, lots: int,
                   meta: Dict[str, dict]) -> Optional[List[TsgLeg]]:
        """ladder = {"CE": [(sym, px)], "PE": [...]} from live quotes.
        legs_cfg = backtest-shaped leg dicts (id/action/opt_type/premium_max;
        lots resolved by the caller via resolve_lots — LD5).
        Returns planned legs, or None (state→SKIPPED, skip_reason set).
        Shorts: no candidate <= cap → whole day skipped (LD3, backtest
        no_short_strike parity). Wings: absent allowed (IV8 parity)."""
        planned: List[TsgLeg] = []
        for cfg in legs_cfg:
            pool = ladder.get(cfg["opt_type"], [])
            pick = select_strike(pool, float(cfg["premium_max"]))
            if pick is None:
                if cfg["action"] == "SELL":
                    self.state = D_SKIPPED
                    self.skip_reason = (
                        f"no {cfg['opt_type']} short <= "
                        f"{cfg['premium_max']} (pool={len(pool)})")
                    return None
                continue                      # wing absent — allowed
            sym = pick[0]
            m = meta.get(sym, {})
            planned.append(TsgLeg(
                leg_id=cfg["id"], action=cfg["action"],
                opt_type=cfg["opt_type"], symbol=sym,
                strike=float(m.get("strike") or 0),
                expiry=str(m.get("expiry") or ""),
                qty=int(lots) * int(lot_size)))
        self.state = D_ENTERING
        for l in planned:
            self.legs[l.leg_id] = l
        return planned

    def leg_filled(self, leg_id: str, price: float,
                   order_id: Optional[str] = None) -> None:
        l = self.legs[leg_id]
        l.entry_price = float(price)
        l.entry_order_id = order_id
        l.state = L_OPEN
        if all(x.state != L_PENDING for x in self.legs.values()):
            self.state = D_OPEN

    def leg_entry_dead(self, leg_id: str) -> List[str]:
        """Entry order failed → all-or-unwind (IC D6 parity): mark this leg
        DEAD, return ids of already-OPEN legs the wrapper must unwind."""
        self.legs[leg_id].state = L_DEAD
        self.state = D_ABORTED
        return [i for i, l in self.legs.items() if l.state == L_OPEN]

    def set_entry_iv(self, leg_id: str, iv: Optional[float]) -> None:
        """IV11 anchor from the actual fill. None → leg unmonitored today
        (backtest iv_entry_solve_fail parity)."""
        l = self.legs[leg_id]
        l.entry_iv = iv
        if not l.is_short:
            l.iv_threshold = None
        elif self.iv_sl_delta_pts > 0:
            l.iv_threshold = (None if iv is None
                              else iv + self.iv_sl_delta_pts / 100.0)
        elif self.iv_sl_pct > 0:
            l.iv_threshold = self.iv_sl_pct / 100.0
        else:
            l.iv_threshold = None

    # ── per-minute evaluation (LD2/LD4) ─────────────────────────────────
    def open_ids(self) -> List[str]:
        return [i for i, l in self.legs.items() if l.state == L_OPEN]

    def hedge_of(self, short_id: str) -> Optional[str]:
        s = self.legs[short_id]
        for i, l in self.legs.items():
            if l.action == "BUY" and l.opt_type == s.opt_type \
                    and l.state == L_OPEN:
                return i
        return None

    def day_mtm(self, marks: Dict[str, float]) -> float:
        u = 0.0
        for i in self.open_ids():
            l = self.legs[i]
            mk = marks.get(i, l.last_mark)
            if mk is not None and l.entry_price is not None:
                u += leg_mtm(l.action, l.entry_price, mk, l.qty)
        return self.realized + u

    def evaluate_minute(self, marks: Dict[str, float],
                        ivs: Dict[str, Optional[float]]
                        ) -> Optional[Tuple[str, List[str]]]:
        """One completed-candle evaluation. marks: leg_id -> close (missing
        leg → carry last_mark, backtest D11 parity). ivs: SHORT leg_id ->
        solved strike IV (IV10 done by the wrapper) or None (skip, IV2).
        Returns (reason, leg_ids_to_exit) or None. The wrapper executes the
        exits and then calls leg_exited() per confirmed fill."""
        if self.state not in (D_OPEN, D_PARTIAL):
            return None
        for i in self.open_ids():                       # carry-forward marks
            if i in marks and marks[i] is not None:
                self.legs[i].last_mark = marks[i]
        eff = {i: self.legs[i].last_mark for i in self.open_ids()}
        if any(v is None for v in eff.values()):
            return None                                  # no marks yet
        mtm = self.day_mtm(eff)
        self.peak_mtm = max(self.peak_mtm, mtm)
        self.trough_mtm = min(self.trough_mtm, mtm)
        if self.mtm_sl > 0 and mtm <= -self.mtm_sl:
            return ("MTM_SL", self.open_ids())
        if self.mtm_target > 0 and mtm >= self.mtm_target:
            return ("MTM_TARGET", self.open_ids())
        if not self.iv_armed_used:
            crossed: List[str] = []
            for i in self.open_ids():
                l = self.legs[i]
                if not l.is_short or l.iv_threshold is None:
                    continue
                iv = ivs.get(i)
                if iv is None:
                    continue                             # IV2: skip minute
                if iv >= l.iv_threshold and eff[i] > l.entry_price:  # IV9
                    crossed.append(i)
            if crossed:
                self.iv_armed_used = True                # IV4 one-shot
                out: List[str] = []
                for i in crossed:
                    out.append(i)
                    h = self.hedge_of(i)
                    if h:
                        out.append(h)
                return ("IV_SL", out)
        return None

    @staticmethod
    def eod_due(now_hhmm: str, exit_hhmm: str) -> bool:
        return now_hhmm >= exit_hhmm

    def kill_exit_ids(self) -> List[str]:
        """LD7: everything open. Reason 'KILL' applied by the wrapper;
        verified-flat then mode-flip ordering is the wrapper's duty."""
        return self.open_ids()

    # ── exit bookkeeping ────────────────────────────────────────────────
    def begin_close(self) -> None:
        if self.state in (D_OPEN, D_PARTIAL):
            self.state = D_CLOSING

    def leg_exited(self, leg_id: str, price: float, reason: str,
                   ts: Optional[int] = None) -> None:
        """Confirmed exit fill. reason for the SHORT of an IV pair is
        'IV_SL'; the wrapper passes 'IV_SL_HEDGE' for its partner
        (vocabulary parity)."""
        l = self.legs[leg_id]
        l.exit_price = float(price)
        l.exit_reason = reason
        l.exit_ts = ts
        l.state = L_CLOSED
        if l.entry_price is not None:
            self.realized += leg_mtm(l.action, l.entry_price,
                                     l.exit_price, l.qty)
        remaining = self.open_ids()
        if not remaining:
            self.state = D_CLOSED
        elif reason in ("IV_SL", "IV_SL_HEDGE"):
            self.state = D_PARTIAL

    # ── persistence (LD6) ───────────────────────────────────────────────
    def to_state(self) -> dict:
        d = asdict(self)
        d["legs"] = {i: l.to_state() for i, l in self.legs.items()}
        # inf isn't JSON-safe
        d["peak_mtm"] = None if self.peak_mtm == float("-inf") else self.peak_mtm
        d["trough_mtm"] = None if self.trough_mtm == float("inf") else self.trough_mtm
        return d

    @classmethod
    def from_state(cls, d: dict) -> "TsgDayCore":
        legs = {i: TsgLeg.from_state(x) for i, x in (d.get("legs") or {}).items()}
        c = cls(**{k: v for k, v in d.items()
                   if k not in ("legs", "peak_mtm", "trough_mtm")})
        c.legs = legs
        c.peak_mtm = d.get("peak_mtm")
        c.peak_mtm = float("-inf") if c.peak_mtm is None else c.peak_mtm
        c.trough_mtm = d.get("trough_mtm")
        c.trough_mtm = float("inf") if c.trough_mtm is None else c.trough_mtm
        return c