# backend/app/engine/ic_v1/ic_live_core.py
#
# IC_V1 — PURE live core (no app imports, unit-tested)
# ============================================================================
# Mirrors backend/app/backtest/ic/ic_v1_engine.py semantics wherever a rule
# is shared (price math, strike selection, exit-reason vocabulary), and adds
# the LIVE-ONLY decisions locked with Anbu on 2026-07-06:
#
#   D1  Entry order type   : protected limits (handled by wrapper/executor;
#                            this core only plans, never places).
#   D2  Leg sequencing     : wings (L3,L4) first, then shorts (L1,L2).
#   D3  Freeze slicing     : per-order cap = floor(freeze_qty/lot_size)*lot_size,
#                            freeze_qty from config (default 1800), lot_size
#                            from Settings — NEVER hardcoded.
#   D5  MTC live semantics : on confirmed SL FILL of one short, IMMEDIATELY
#                            re-pin partner's SL to partner's OWN entry.
#                            If partner LTP already at/through cost, or the
#                            re-pin cannot be guaranteed → MARKET_OUT partner.
#   D6  Partial fills      : all-or-unwind. Any SHORT entry dead → unwind all
#                            filled legs. Wing ORDER failure → unwind (order
#                            failure is an ops fault, distinct from the
#                            backtest's "no strike in data" strangle-degrade).
#   D7  One-entry-per-day  : latch owned by wrapper (persisted); core exposes
#                            the group FSM only.
#
# BACKTEST-PARITY DIVERGENCE LEDGER (documented, intentional):
#   - Entry price: live LTP at entry_time vs backtest candle close.
#   - MTC timing : live IMMEDIATE on fill vs backtest next-candle.
#   - SL fills   : live at market/limit fill vs backtest at-trigger.
#
# Exit reason vocabulary (MUST stay identical to backtest):
#   SL, TP, MTC_COST, EOD_MTC, EOD
# Live-only additions: UNWIND (entry-failure unwind), MTC_MARKET_OUT
#   (D5 fallback when the cost stop could not be guaranteed).
# ============================================================================

from dataclasses import dataclass, field
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
        return None   # short: fail CLOSED

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

@dataclass
class LegCore:
    leg_id: str                  # "L1".."L4"
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

    @property
    def is_short(self) -> bool:
        return self.action == "SELL"

    def pnl(self) -> Optional[float]:
        if self.exit_price is None or self.state != L_CLOSED:
            return None
        if self.is_short:
            return (self.entry_price - self.exit_price) * self.qty
        return (self.exit_price - self.entry_price) * self.qty


@dataclass
class GroupCore:
    legs: Dict[str, LegCore] = field(default_factory=dict)
    state: str = G_IDLE
    mtc_fired: bool = False           # one-shot latch (spec: MTC is one-shot)
    double_sl_minute: bool = False    # both shorts SL-filled within 60s (log parity)
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

    # ── MTC (D5) — the heart of the strategy ──────────────────────────────
    def on_short_sl_filled(
        self,
        leg_id: str,
        exit_price: float,
        partner_ltp: Optional[float],
        ts: int,
    ) -> Optional[Dict]:
        """
        A short's SL has a CONFIRMED FILL. Close it, then decide the partner
        action. Returns None (no partner action) or:
          {"action": MTC_REPIN,      "partner": id, "cost_stop": partner.entry}
          {"action": MTC_MARKET_OUT, "partner": id}

        Rules pinned:
          - MTC is ONE-SHOT: second SL fill does nothing to anyone else.
          - Partner already CLOSED (double-SL resolved by fill order) → no
            action; flag double_sl_minute if within 60s of the first.
          - Partner LTP already at/through the cost stop → MARKET_OUT
            (cost stop can't be guaranteed; MTC intent = no further loss).
          - Partner LTP unknown (stale/absent) → MARKET_OUT. Fail
            conservative: never place a stop we can't validate against price.
        """
        leg = self.legs[leg_id]
        if not leg.is_short or leg.state != L_OPEN:
            return None

        leg.state = L_CLOSED
        leg.exit_price = exit_price
        leg.exit_reason = "SL"

        # double-SL minute flag (comparability with backtest double_sl)
        if self._first_short_sl_ts is None:
            self._first_short_sl_ts = ts
        elif ts - self._first_short_sl_ts <= 60:
            self.double_sl_minute = True

        if self.mtc_fired:
            return None
        pid = leg.mtc_partner
        if not pid:
            return None
        partner = self.legs.get(pid)
        if partner is None or partner.state != L_OPEN or not partner.is_short:
            return None

        self.mtc_fired = True   # one-shot latch fires on the DECISION

        if partner_ltp is None or partner_ltp >= partner.entry_price:
            return {"action": MTC_MARKET_OUT, "partner": pid}

        return {
            "action": MTC_REPIN,
            "partner": pid,
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
        leg.exit_reason = reason

    def open_legs(self) -> List[LegCore]:
        return [l for l in self.legs.values() if l.state == L_OPEN]

    def all_closed(self) -> bool:
        return all(l.state in (L_CLOSED, L_DEAD) for l in self.legs.values())

    def finalize_if_done(self):
        if self.state in (G_OPEN, G_CLOSING) and self.all_closed():
            self.state = G_CLOSED