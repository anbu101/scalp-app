# backend/app/engine/ic/ic_live_core.py
#
# IC (shared V1/V2) — PURE live core (no app imports, unit-tested)
# ============================================================================
# Mirrors backend/app/backtest/ic/ic_v1_engine.py semantics wherever a rule
# is shared (price math, strike selection, exit-reason vocabulary), plus the
# LIVE-ONLY decisions locked with Anbu on 2026-07-06 and the IC_V2 semantics
# amendment locked 2026-07-26:
#
#   D1  Entry order type   : protected limits (wrapper/executor concern).
#   D2  Leg sequencing     : wings (L3,L4) first, then shorts (L1,L2).
#   D3  Freeze slicing     : per-order cap = floor(freeze_qty/lot_size)*lot_size.
#   D5  MTC live semantics : ── AMENDED (IC_V2, D2=a lock) ── on confirmed
#                            stop FILL of one short, the partner's cost-stop
#                            re-pin is SCHEDULED at fill_ts + 60s (backtest
#                            "next-minute effective" parity). Until activation
#                            the partner keeps its ORIGINAL SL (protected).
#                            At activation the wrapper asks
#                            mtc_activation_decision(): partner LTP already
#                            at/through cost, or unknown → MARKET_OUT;
#                            else REPIN to partner's own entry.
#   D6  Partial fills      : all-or-unwind.
#   D7  One-entry-per-day  : latch owned by wrapper; PLUS the open-book gate —
#                            no new entry while anything IC-opened is open.
#
# ── IC_V2 SEMANTICS (locked 2026-07-26, backtest-validated) ────────────────
#   ADJ_ON_MTC : a SHORT leg's stop exit — reason "SL" *or* "MTC_COST"
#     (2026-07-24 reversal of D5) — arms an adjustment BUY on the same option
#     type, activating adjust_delay_s later. Adjustment legs (id "<src>A")
#     are BUY legs with their own SL/TP, never MTC partners, never arm
#     further adjustments.
#   ONE_NIGHT_MAX : exit_mode "NEXT_OPEN" — legs open at session end CARRY
#     exactly one night and close at the next session's next_open_time
#     (09:16) unconditionally, including on their expiry day
#     (MORNING_SQUARE_OFF fix 2026-07-22). The expiry-day intraday square-off
#     (15:28) applies ONLY to legs entered THAT day.
#   FIRST-CANDLE RULE (live-only, user lock 2026-07-26): NO exits in
#     09:15–09:16. Carried-leg GTTs are cancelled pre-market; the 09:16
#     market close (wrapper) is the sole exit executor on a carry morning.
#
# BACKTEST-PARITY DIVERGENCE LEDGER (documented, intentional):
#   - Entry price : live LTP at entry_time vs backtest candle close.
#   - SL fills    : live at market/limit fill vs backtest at-trigger.
#   - MTC timing  : PARITY RESTORED — both schedule the re-pin at +60s.
#     Residual: live validates partner LTP vs cost AT ACTIVATION and
#     market-outs when already through (backtest re-pins unconditionally and
#     fills at-trigger); live cannot rest a stop above market.
#   - CARRY MORNING: backtest evaluates carried SL/TP on the 09:15 candle
#     (gap fills); live deliberately does NOT (first-candle rule) — every
#     carried leg closes at 09:16 first prints. Consequence: gap-morning SL
#     exits never arm adjustments in live (close reason is NEXT_OPEN).
#
# Exit reason vocabulary (MUST stay identical to backtest):
#   SL, TP, MTC_COST, EOD_MTC, EOD, NEXT_OPEN
# Live-only additions: UNWIND, MTC_MARKET_OUT, BROKER_EXIT, MANUAL.
# ============================================================================

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

PRICE_FLOOR = 0.05

# ── Group / leg lifecycle states ────────────────────────────────────────────
G_IDLE      = "IDLE"
G_ENTERING  = "ENTERING"
G_OPEN      = "OPEN"
G_CLOSING   = "CLOSING"
G_CLOSED    = "CLOSED"
G_ABORTED   = "ABORTED"      # entry failed → unwound

L_PENDING   = "PENDING_ENTRY"
L_OPEN      = "OPEN"
L_CLOSED    = "CLOSED"
L_DEAD      = "DEAD"         # entry order never opened a position

# ── MTC actions (D5) ────────────────────────────────────────────────────────
MTC_REPIN      = "REPIN"        # cancel partner GTT (verified) + new SL at cost
MTC_MARKET_OUT = "MARKET_OUT"   # cost stop not guaranteeable → exit partner now

# ── IC_V2 ── scheduling constants (backtest parity: next 1m candle)
MTC_DELAY_S = 60

# Reasons that arm an adjustment (ADJ_ON_MTC, 2026-07-24 reversal)
ADJ_ARM_REASONS = ("SL", "MTC_COST")


# ============================================================================
# Price math — IDENTICAL formulas to ic_v1_engine.py (parity-pinned by tests)
# ============================================================================

def sl_price(action: str, entry: float, val: float, mode: str) -> Optional[float]:
    if not val or val <= 0:
        return None
    if action == "SELL":
        return entry * (1 + val / 100.0) if mode == "pct" else entry + val
    # BUY
    p = entry * (1 - val / 100.0) if mode == "pct" else entry - val
    return max(PRICE_FLOOR, p)


def tp_price(action: str, entry: float, val: float, mode: str) -> Optional[float]:
    if not val or val <= 0:
        return None
    if action == "SELL":
        p = entry * (1 - val / 100.0) if mode == "pct" else entry - val
        return max(PRICE_FLOOR, p)
    # BUY
    return entry * (1 + val / 100.0) if mode == "pct" else entry + val


# ============================================================================
# Strike selection — same rule as backtest select_strike:
#   highest premium <= cap (nearest below).
#   SHORT legs fail CLOSED  → None => the whole day is skipped by the wrapper.
#   WING  legs fail OPEN    → cheapest available fallback (flagged).
#   ADJUSTMENT legs fail CLOSED (DA3 lock 2026-07-26): never buy richer.
# candidates: list of (strike:int, symbol:str, ltp:float), ltp > 0 only.
# ============================================================================

@dataclass
class StrikePick:
    strike: int
    symbol: str
    ltp: float
    fallback: bool = False      # wing fell back to cheapest-available


def select_strike(
    candidates: List[Tuple[int, str, float]],
    cap: float,
    fallback_cheapest: bool,
) -> Optional[StrikePick]:
    valid = [(s, sym, p) for (s, sym, p) in candidates if p and p > 0]
    if not valid:
        return None

    under = [c for c in valid if c[2] <= cap]
    if under:
        # highest premium <= cap; deterministic tie-break on strike
        s, sym, p = max(under, key=lambda c: (c[2], -c[0]))
        return StrikePick(strike=s, symbol=sym, ltp=p, fallback=False)

    if not fallback_cheapest:
        return None   # short / adjustment: fail CLOSED

    s, sym, p = min(valid, key=lambda c: (c[2], c[0]))
    return StrikePick(strike=s, symbol=sym, ltp=p, fallback=True)


# ============================================================================
# Freeze slicing (D3) — lots and lot_size come from config/Settings.
# ============================================================================

def per_order_cap(freeze_qty: int, lot_size: int) -> int:
    """Highest lot-multiple <= freeze_qty. 0 if lot_size invalid."""
    if lot_size <= 0 or freeze_qty <= 0:
        return 0
    return (freeze_qty // lot_size) * lot_size


def slice_qty(total_qty: int, freeze_qty: int, lot_size: int) -> List[int]:
    """
    Split total_qty into per-order chunks, each <= per_order_cap and a
    lot multiple. Raises on qty that is not a lot multiple (config error —
    fail closed rather than round silently).
    """
    if total_qty <= 0:
        return []
    if lot_size <= 0 or total_qty % lot_size != 0:
        raise ValueError(f"qty {total_qty} not a multiple of lot_size {lot_size}")
    cap = per_order_cap(freeze_qty, lot_size)
    if cap <= 0:
        raise ValueError(f"invalid freeze/lot: freeze={freeze_qty} lot={lot_size}")
    out = []
    remaining = total_qty
    while remaining > 0:
        chunk = min(cap, remaining)
        out.append(chunk)
        remaining -= chunk
    return out


# ============================================================================
# Leg / group state
# ============================================================================

# Fields serialized for the ONE_NIGHT_MAX carry snapshot (DA1). Kept as an
# explicit list so a future field addition is a conscious carry decision.
_CARRY_LEG_FIELDS = (
    "leg_id", "action", "opt_type", "symbol", "qty", "entry_price",
    "sl", "tp", "mtc_partner", "mtc_repinned", "wing_fallback",
    "is_adjust", "adjust_of", "entry_date", "expiry",
)


@dataclass
class LegCore:
    leg_id: str                  # "L1".."L4", adjustments "L1A"/"L2A"
    action: str                  # "SELL" | "BUY"
    opt_type: str                # "CE" | "PE"
    symbol: str = ""
    qty: int = 0
    entry_price: float = 0.0     # provisional (limit) then patched to fill
    sl: Optional[float] = None
    tp: Optional[float] = None
    mtc_partner: Optional[str] = None
    state: str = L_PENDING
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    mtc_repinned: bool = False   # this leg's SL now sits at its own entry
    wing_fallback: bool = False
    # ── IC_V2 ──
    is_adjust: bool = False      # this is an "<src>A" adjustment BUY leg
    adjust_of: Optional[str] = None
    carried: bool = False        # carried INTO today (set on restore)
    entry_date: str = ""         # "YYYY-MM-DD" IST — DA5/DA6 scoping
    expiry: str = ""             # "YYYY-MM-DD" — DA5 expiry-day scoping

    @property
    def is_short(self) -> bool:
        return self.action == "SELL"

    def pnl(self) -> Optional[float]:
        if self.exit_price is None or self.state != L_CLOSED:
            return None
        if self.is_short:
            return (self.entry_price - self.exit_price) * self.qty
        return (self.exit_price - self.entry_price) * self.qty

    # ── ONE_NIGHT_MAX carry serialization ───────────────────────────────
    def to_carry(self) -> dict:
        d = asdict(self)
        return {k: d[k] for k in _CARRY_LEG_FIELDS}

    @classmethod
    def from_carry(cls, d: dict) -> "LegCore":
        leg = cls(
            leg_id=str(d["leg_id"]), action=str(d["action"]),
            opt_type=str(d["opt_type"]), symbol=str(d.get("symbol") or ""),
            qty=int(d.get("qty") or 0),
            entry_price=float(d.get("entry_price") or 0.0),
            sl=(float(d["sl"]) if d.get("sl") is not None else None),
            tp=(float(d["tp"]) if d.get("tp") is not None else None),
            mtc_partner=d.get("mtc_partner"),
            mtc_repinned=bool(d.get("mtc_repinned")),
            wing_fallback=bool(d.get("wing_fallback")),
            is_adjust=bool(d.get("is_adjust")),
            adjust_of=d.get("adjust_of"),
            entry_date=str(d.get("entry_date") or ""),
            expiry=str(d.get("expiry") or ""),
        )
        leg.state = L_OPEN
        leg.carried = True
        return leg


@dataclass
class GroupCore:
    legs: Dict[str, LegCore] = field(default_factory=dict)
    state: str = G_IDLE
    mtc_fired: bool = False           # one-shot latch (spec: MTC is one-shot)
    double_sl_minute: bool = False    # both shorts stop-filled within 60s
    _first_short_sl_ts: Optional[int] = None

    # ── entry lifecycle ────────────────────────────────────────────────────
    def begin_entry(self):
        assert self.state == G_IDLE
        self.state = G_ENTERING

    def leg_filled(self, leg_id: str):
        leg = self.legs[leg_id]
        assert leg.state == L_PENDING
        leg.state = L_OPEN
        if all(l.state == L_OPEN for l in self.legs.values()):
            self.state = G_OPEN

    def leg_entry_dead(self, leg_id: str) -> List[str]:
        """
        D6: any leg's entry order is DEAD (rejected/cancelled/lapsed/unfilled
        at cap) → return the leg_ids that must be UNWOUND (market-out), in
        shorts-first order (kill risk first). Group goes ABORTED.
        """
        dead = self.legs[leg_id]
        dead.state = L_DEAD
        self.state = G_ABORTED
        filled = [l for l in self.legs.values() if l.state == L_OPEN]
        # unwind shorts before wings: naked short risk dies first
        filled.sort(key=lambda l: (0 if l.is_short else 1, l.leg_id))
        return [l.leg_id for l in filled]

    def record_unwind(self, leg_id: str, exit_price: float):
        leg = self.legs[leg_id]
        leg.state = L_CLOSED
        leg.exit_price = exit_price
        leg.exit_reason = "UNWIND"

    # ── IC_V2 ── mid-session adjustment leg joins the group ───────────────
    def add_adjust_leg(self, leg: LegCore):
        """An adjustment leg opened mid-session. Group must be OPEN or
        CLOSING (a condor can be fully closed leg-wise while an adjustment
        rides — the group is NOT finalized until every leg incl. ·ADJ is
        closed, matching the backtest's all-legs group accounting)."""
        assert leg.is_adjust and leg.leg_id not in self.legs
        leg.state = L_OPEN
        self.legs[leg.leg_id] = leg
        if self.state in (G_CLOSED,):
            # a condor that finalized before the delayed adjustment opened
            # re-opens at group level: there is live exposure again.
            self.state = G_OPEN

    # ── MTC (D5, AMENDED: scheduled next-minute) ──────────────────────────
    def on_short_stop_filled(
        self,
        leg_id: str,
        exit_price: float,
        ts: int,
    ) -> dict:
        """
        A short's stop (original SL *or* cost stop) has a CONFIRMED FILL.
        Close it, translate the reason (MTC_COST when this leg was
        re-pinned), and return what the wrapper must now schedule:

          {"reason": "SL" | "MTC_COST",
           "mtc_pending":    None | {"partner": id, "activate_ts": ts+60},
           "adjust_pending": None | {"src": leg_id}}

        adjust_pending only says the reason QUALIFIES (ADJ_ARM_REASONS on a
        non-adjust short); the wrapper decides against config
        (adjust_on_sl / enabled / lots) and applies adjust_delay_s.

        Rules pinned:
          - MTC is ONE-SHOT: the latch fires when the re-pin is SCHEDULED.
          - Partner already CLOSED → no MTC scheduling.
          - double_sl_minute flagged when both shorts stop-fill within 60s.
        """
        leg = self.legs[leg_id]
        if not leg.is_short or leg.state != L_OPEN:
            return {"reason": None, "mtc_pending": None, "adjust_pending": None}

        reason = "MTC_COST" if leg.mtc_repinned else "SL"
        leg.state = L_CLOSED
        leg.exit_price = exit_price
        leg.exit_reason = reason

        # double-SL minute flag (comparability with backtest double_sl)
        if self._first_short_sl_ts is None:
            self._first_short_sl_ts = ts
        elif ts - self._first_short_sl_ts <= 60:
            self.double_sl_minute = True

        adjust_pending = None
        if reason in ADJ_ARM_REASONS and not leg.is_adjust:
            adjust_pending = {"src": leg_id}

        mtc_pending = None
        if not self.mtc_fired and leg.mtc_partner:
            partner = self.legs.get(leg.mtc_partner)
            if partner is not None and partner.state == L_OPEN and partner.is_short:
                self.mtc_fired = True   # one-shot latch fires on SCHEDULING
                mtc_pending = {
                    "partner": leg.mtc_partner,
                    "activate_ts": ts + MTC_DELAY_S,
                }

        return {"reason": reason, "mtc_pending": mtc_pending,
                "adjust_pending": adjust_pending}

    def mtc_activation_decision(
        self, partner_id: str, partner_ltp: Optional[float]
    ) -> Optional[Dict]:
        """
        Called by the wrapper AT ACTIVATION TIME (fill_ts + 60s). Decides the
        partner action against the price at that moment:

          None                          — partner no longer open (nothing to do)
          {"action": MTC_REPIN, "partner", "cost_stop"}
          {"action": MTC_MARKET_OUT, "partner"}

        Partner LTP already at/through cost, or unknown/stale → MARKET_OUT
        (cost stop can't be guaranteed / can't be validated — fail
        conservative, exactly the pre-amendment D5 rule, evaluated later).
        """
        partner = self.legs.get(partner_id)
        if partner is None or partner.state != L_OPEN or not partner.is_short:
            return None
        if partner.mtc_repinned:
            return None
        if partner_ltp is None or partner_ltp >= partner.entry_price:
            return {"action": MTC_MARKET_OUT, "partner": partner_id}
        return {
            "action": MTC_REPIN,
            "partner": partner_id,
            "cost_stop": partner.entry_price,
        }

    def confirm_repin(self, partner_id: str):
        """Wrapper confirmed the new cost-stop GTT is live."""
        p = self.legs[partner_id]
        p.sl = p.entry_price
        p.mtc_repinned = True

    def repin_failed(self, partner_id: str) -> Dict:
        """D5: re-pin could not be guaranteed → market-out the partner."""
        return {"action": MTC_MARKET_OUT, "partner": partner_id}

    # ── generic exits ──────────────────────────────────────────────────────
    def close_leg(self, leg_id: str, exit_price: float, reason: str):
        leg = self.legs[leg_id]
        if leg.state != L_OPEN:
            return
        leg.state = L_CLOSED
        leg.exit_price = exit_price
        # exit-reason parity with backtest vocabulary:
        if reason == "SL" and leg.mtc_repinned:
            reason = "MTC_COST"          # survivor scratched at its cost stop
        if reason == "EOD" and leg.mtc_repinned:
            reason = "EOD_MTC"           # MTC survivor rode to EOD
        # NEXT_OPEN passes through untranslated (backtest parity: carried
        # legs close as NEXT_OPEN regardless of MTC state).
        leg.exit_reason = reason

    def open_legs(self) -> List[LegCore]:
        return [l for l in self.legs.values() if l.state == L_OPEN]

    def all_closed(self) -> bool:
        return all(l.state in (L_CLOSED, L_DEAD) for l in self.legs.values())

    def finalize_if_done(self):
        if self.state in (G_OPEN, G_CLOSING) and self.all_closed():
            self.state = G_CLOSED

    # ── IC_V2 ── ONE_NIGHT_MAX carry serialization ────────────────────────
    def carry_snapshot(self) -> List[dict]:
        """Carry dicts for every OPEN leg. DA5 hard assert: a leg whose
        expiry == its entry_date must NEVER carry (the expiry-day 15:28
        square-off should have closed it) — raise loudly, never silently
        carry a corpse position past its own expiry."""
        out = []
        for leg in self.open_legs():
            if leg.expiry and leg.entry_date and leg.expiry == leg.entry_date:
                raise RuntimeError(
                    f"CARRY_ASSERT: {leg.leg_id} {leg.symbol} entered on its "
                    f"own expiry day ({leg.expiry}) is still open at carry "
                    f"commit — expiry square-off failed"
                )
            out.append(leg.to_carry())
        return out

    @classmethod
    def restore_carry(cls, leg_dicts: List[dict], *, mtc_fired: bool,
                      double_sl_minute: bool) -> "GroupCore":
        core = cls()
        for d in leg_dicts:
            leg = LegCore.from_carry(d)
            core.legs[leg.leg_id] = leg
        core.state = G_OPEN if core.legs else G_CLOSED
        core.mtc_fired = bool(mtc_fired)
        core.double_sl_minute = bool(double_sl_minute)
        return core

    # ── IC_RESTART ── mid-session snapshot (2026-07-31): FULL state, every
    # leg incl. CLOSED (exit price/reason matter for the panel, finalize
    # logic, and the MTC/double-SL latches after a restart). Distinct from
    # the carry format (v1, open-legs-only, locked) on purpose.
    def session_snapshot(self) -> dict:
        return {
            "state": self.state,
            "mtc_fired": self.mtc_fired,
            "double_sl_minute": self.double_sl_minute,
            "legs": [asdict(l) for l in self.legs.values()],
        }

    @classmethod
    def restore_session(cls, snap: dict) -> "GroupCore":
        core = cls()
        for d in (snap.get("legs") or []):
            leg = LegCore(
                leg_id=str(d["leg_id"]), action=str(d["action"]),
                opt_type=str(d["opt_type"]), symbol=str(d.get("symbol") or ""),
                qty=int(d.get("qty") or 0),
                entry_price=float(d.get("entry_price") or 0.0),
                sl=(float(d["sl"]) if d.get("sl") is not None else None),
                tp=(float(d["tp"]) if d.get("tp") is not None else None),
                mtc_partner=d.get("mtc_partner"),
                mtc_repinned=bool(d.get("mtc_repinned")),
                wing_fallback=bool(d.get("wing_fallback")),
                is_adjust=bool(d.get("is_adjust")),
                adjust_of=d.get("adjust_of"),
                entry_date=str(d.get("entry_date") or ""),
                expiry=str(d.get("expiry") or ""),
            )
            leg.state = str(d.get("state") or L_OPEN)
            leg.exit_price = (float(d["exit_price"])
                              if d.get("exit_price") is not None else None)
            leg.exit_reason = d.get("exit_reason")
            leg.carried = bool(d.get("carried"))
            core.legs[leg.leg_id] = leg
        core.state = str(snap.get("state") or (G_OPEN if core.legs else G_CLOSED))
        core.mtc_fired = bool(snap.get("mtc_fired"))
        core.double_sl_minute = bool(snap.get("double_sl_minute"))
        return core
