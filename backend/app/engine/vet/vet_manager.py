# backend/app/engine/vet/vet_manager.py
#
# ── VET_V1 TRADE MANAGER ── position lifecycle, one position at a time
# ============================================================================
# Consumes decisions from vet_live_signal_engine (which are parity-proven
# against the backtest) and turns them into legs. All signal logic lives
# upstream; nothing here re-derives a trend.
#
# ── THE ORDERING RULE THAT ONLY EXISTS IN LIVE (LD5) ────────────────────
# In SELL mode with a wing, the WING IS BOUGHT FIRST and the short is sold
# second. Reverse that order and the account is briefly naked-short and is
# margined as such — a SPAN spike that can reject the second order and leave
# a genuinely unhedged position open. The backtest cannot see this because it
# prices both legs at one timestamp. So:
#     wing BUY fills  →  then short SELL
#     wing BUY fails  →  NO TRADE (nothing to unwind)
#     short SELL fails after the wing filled → the wing is SOLD BACK
#                                              immediately, position abandoned
# On exit the order reverses: close the short FIRST (removing the risk),
# then sell the wing. Never leave a naked short at either boundary.
#
# ── NEVER SYNTHETIC (wing_mode) ─────────────────────────────────────────
# A real listed contract at or under hedge_max_premium, or no trade. The
# backtest's synthetic wing has no live equivalent; the divergence is
# documented in the config, not papered over here.
#
# ── NO SL/TP, NO GTT (LD3/LD6) ──────────────────────────────────────────
# All sealed configs run sl_pct=0/tp_pct=0. Exits are FLIP, SIGNAL_EXIT,
# EXPIRY_EXIT, EOD and KILL — all decided by the engine at 5m closes or by a
# boundary. There is no GTT layer to race, which is why the kill path is a
# plain flatten.
#
# ── RESTART (mandatory smoke leg) ───────────────────────────────────────
# resume_from_db() rebuilds the in-memory position from vet_trades so a
# mid-session restart does not open a second position alongside a live one.
# A DB row the broker cannot confirm is marked STALE, never silently traded
# around.
# ============================================================================

from __future__ import annotations

import uuid
from typing import Callable, Dict, List, Optional

try:
    from app.engine.vet.vet_common import VetRepo, now_ts
    from app.engine.vet.vet_live_core import (
        ENTER, EXIT, FLIP, HOLD, R_EOD, R_EXPIRY)
    from app.event_bus.audit_logger import write_audit_log
except ImportError:                                        # standalone tests
    from vet_common import VetRepo, now_ts                  # type: ignore
    from vet_live_core import (                             # type: ignore
        ENTER, EXIT, FLIP, HOLD, R_EOD, R_EXPIRY)

    def write_audit_log(msg: str) -> None:                  # type: ignore
        print(msg)

R_KILL = "KILL"


class VetManager:
    """One open position at a time, in either direction.

    Injected collaborators keep this testable without a broker:
      chain_fn(side, ts)   -> list of {tradingsymbol, token, strike, expiry,
                                       instrument_type, ltp}
      quote_fn(symbol)     -> float | None      (last price)
      executor             -> object with place_buy / place_sell_entry /
                              place_market_sell / place_buy_exit
    """

    def __init__(self, cfg: Dict, *, repo: Optional[VetRepo] = None,
                 chain_fn: Optional[Callable] = None,
                 quote_fn: Optional[Callable] = None,
                 executor=None, mode: str = "PAPER"):
        self.cfg = dict(cfg or {})
        self.repo = repo or VetRepo()
        self.chain_fn = chain_fn
        self.quote_fn = quote_fn
        self.executor = executor
        self.mode = str(mode or "PAPER").upper()
        self.pos: Optional[Dict] = None        # {group_id, side, main, wing}
        self.frozen: bool = False
        self.freeze_reason: Optional[str] = None
        self._day_entries: int = 0

    # ── config helpers ──────────────────────────────────────────────────
    @property
    def is_sell(self) -> bool:
        return str(self.cfg.get("leg_action", "BUY")).upper() == "SELL"

    @property
    def wants_wing(self) -> bool:
        return (self.is_sell and bool(self.cfg.get("hedge_enabled"))
                and float(self.cfg.get("hedge_max_premium") or 0) > 0)

    @property
    def qty(self) -> int:
        q = self.cfg.get("quantity") or {}
        return int(q.get("lots", 10)) * int(q.get("lot_size", 65))

    def freeze(self, reason: str) -> None:
        """Stop opening anything. Existing positions still exit — freezing
        entry is a refusal to add risk, not a refusal to remove it."""
        if not self.frozen:
            self.frozen = True
            self.freeze_reason = reason
            write_audit_log(f"[VET][MGR] FROZEN: {reason}")

    # ── contract selection ──────────────────────────────────────────────
    def _select_main(self, side: str, ts: int) -> Optional[Dict]:
        """ATM ± offset on the live chain. Mirrors the backtest's ladder walk:
        nearest strike to spot, then offset steps, positive = OTM-ward."""
        chain = [c for c in (self.chain_fn(side, ts) if self.chain_fn else [])
                 if c.get("instrument_type") == side and c.get("ltp")]
        if not chain:
            return None
        ladder = sorted(chain, key=lambda c: float(c["strike"]))
        spot = self.cfg.get("_spot")
        if spot is None:
            return None
        ai = min(range(len(ladder)),
                 key=lambda i: abs(float(ladder[i]["strike"]) - float(spot)))
        off = int(self.cfg.get("atm_offset") or 0)
        ti = ai + (off if side == "CE" else -off)
        if not (0 <= ti < len(ladder)):
            write_audit_log(f"[VET][MGR] offset {off} walks off the {side} "
                            f"ladder (len {len(ladder)}) — no entry")
            return None
        pick = ladder[ti]
        pmin = float(self.cfg.get("premium_min") or 0)
        pmax = float(self.cfg.get("premium_max") or 0)
        px = float(pick["ltp"])
        if (pmax > 0 and px > pmax) or (pmin > 0 and px < pmin):
            write_audit_log(f"[VET][MGR] {pick['tradingsymbol']} premium {px} "
                            f"outside [{pmin},{pmax}] — no entry")
            return None
        return pick

    def _select_wing(self, side: str, ts: int, main_sym: str,
                     expiry: Optional[str]) -> Optional[Dict]:
        """DEAREST REAL contract at or under the cap, same side and expiry.

        Real only. If the chain has nothing cheap enough the caller must skip
        the ENTIRE entry — selling bare because the wing was unavailable is a
        different strategy with a different risk profile.
        """
        cap = float(self.cfg.get("hedge_max_premium") or 0)
        chain = self.chain_fn(side, ts) if self.chain_fn else []
        best = None
        for c in chain:
            if c.get("instrument_type") != side:
                continue
            if c.get("tradingsymbol") == main_sym:
                continue
            if expiry and c.get("expiry") and c["expiry"] != expiry:
                continue
            px = c.get("ltp")
            if px is None or float(px) <= 0 or float(px) > cap:
                continue
            if best is None or float(px) > float(best["ltp"]):
                best = c
        return best

    # ── order plumbing ──────────────────────────────────────────────────
    def _buy(self, sym: str, token: int) -> Optional[str]:
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return self.executor.place_buy(sym, token, self.qty)
        except Exception as e:
            write_audit_log(f"[VET][EXEC] BUY {sym} FAILED: {e}")
            return None

    def _sell_entry(self, sym: str, token: int) -> Optional[str]:
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return self.executor.place_sell_entry(sym, token, self.qty)
        except Exception as e:
            write_audit_log(f"[VET][EXEC] SELL {sym} FAILED: {e}")
            return None

    def _close_long(self, sym: str) -> Optional[str]:
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return self.executor.place_market_sell(sym, self.qty)
        except Exception as e:
            write_audit_log(f"[VET][EXEC] close-long {sym} FAILED: {e}")
            return None

    def _close_short(self, sym: str, reason: str) -> Optional[str]:
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return self.executor.place_buy_exit(sym, self.qty, reason)
        except Exception as e:
            write_audit_log(f"[VET][EXEC] close-short {sym} FAILED: {e}")
            return None

    # ── entry ───────────────────────────────────────────────────────────
    def open_position(self, side: str, *, ts: int, bar_ts: int,
                      condition: int) -> Optional[Dict]:
        if self.frozen or self.pos is not None:
            return None
        cap = int(self.cfg.get("max_trades_per_day") or 0)
        if cap > 0 and self._day_entries >= cap:
            write_audit_log(f"[VET][MGR] daily entry cap {cap} reached")
            return None
        main = self._select_main(side, ts)
        if main is None:
            return None
        gid = uuid.uuid4().hex[:12]
        wing_row = None
        wing_oid = None

        # ── WING FIRST (LD5) ── the account must never be briefly naked.
        if self.wants_wing:
            wing = self._select_wing(side, ts, main["tradingsymbol"],
                                     main.get("expiry"))
            if wing is None:
                write_audit_log(
                    f"[VET][MGR] no REAL wing <= "
                    f"{self.cfg.get('hedge_max_premium')} on {side} — entry "
                    f"SKIPPED (never bare). This is the documented live/"
                    f"backtest divergence.")
                return None
            wing_oid = self._buy(wing["tradingsymbol"], wing.get("token"))
            if wing_oid is None:
                write_audit_log("[VET][MGR] wing BUY failed — entry abandoned "
                                "before any risk was taken")
                return None
            wing_row = dict(wing)
            wing_row["order_id"] = wing_oid

        # ── then the main leg ──
        if self.is_sell:
            main_oid = self._sell_entry(main["tradingsymbol"], main.get("token"))
        else:
            main_oid = self._buy(main["tradingsymbol"], main.get("token"))
        if main_oid is None:
            if wing_row is not None:
                # unwind the wing rather than sit on a naked long hedge
                self._close_long(wing_row["tradingsymbol"])
                write_audit_log("[VET][MGR] main leg failed AFTER wing filled "
                                "— wing sold back, position abandoned")
            return None

        rows: List[Dict] = []
        if wing_row is not None:
            rows.append({
                "group_id": gid, "leg_role": "WING", "mode": self.mode,
                "direction": "LONG",
                "tradingsymbol": wing_row["tradingsymbol"],
                "token": wing_row.get("token"), "instrument_type": side,
                "strike": wing_row.get("strike"),
                "expiry": wing_row.get("expiry"), "qty": self.qty,
                "lots": (self.cfg.get("quantity") or {}).get("lots"),
                "lot_size": (self.cfg.get("quantity") or {}).get("lot_size"),
                "entry_ts": ts, "entry_price": float(wing_row["ltp"]),
                "entry_order_id": wing_oid, "signal_bar_ts": bar_ts,
                "condition": condition, "leg_action": "BUY"})
        rows.append({
            "group_id": gid, "leg_role": "MAIN", "mode": self.mode,
            "direction": "SHORT" if self.is_sell else "LONG",
            "tradingsymbol": main["tradingsymbol"], "token": main.get("token"),
            "instrument_type": side, "strike": main.get("strike"),
            "expiry": main.get("expiry"), "qty": self.qty,
            "lots": (self.cfg.get("quantity") or {}).get("lots"),
            "lot_size": (self.cfg.get("quantity") or {}).get("lot_size"),
            "entry_ts": ts, "entry_price": float(main["ltp"]),
            "entry_order_id": main_oid, "signal_bar_ts": bar_ts,
            "condition": condition,
            "leg_action": "SELL" if self.is_sell else "BUY"})

        ids = {}
        for r in rows:
            db_id = self.repo.insert_leg(r)
            ids[r["leg_role"]] = db_id
            r["db_id"] = db_id
        self.pos = {"group_id": gid, "side": side,
                    "main": next(r for r in rows if r["leg_role"] == "MAIN"),
                    "wing": next((r for r in rows
                                  if r["leg_role"] == "WING"), None)}
        self._day_entries += 1
        write_audit_log(
            f"[VET][MGR] OPEN {gid} {side} "
            f"{'SHORT' if self.is_sell else 'LONG'} "
            f"{self.pos['main']['tradingsymbol']} @ "
            f"{self.pos['main']['entry_price']}"
            + (f" + wing {self.pos['wing']['tradingsymbol']} @ "
               f"{self.pos['wing']['entry_price']}" if self.pos["wing"] else ""))
        return self.pos

    # ── exit ────────────────────────────────────────────────────────────
    def close_position(self, reason: str, *, ts: int) -> Optional[Dict]:
        """Close MAIN first, then the wing.

        Order matters: the main leg carries the risk (in SELL mode it IS the
        short). Selling the wing first would leave a naked short for however
        long the second order takes.
        """
        if self.pos is None:
            return None
        pos = self.pos
        main, wing = pos["main"], pos["wing"]

        mpx = self._mark(main["tradingsymbol"], main["entry_price"])
        if self.is_sell:
            moid = self._close_short(main["tradingsymbol"], reason)
            gross = (float(main["entry_price"]) - mpx) * self.qty
        else:
            moid = self._close_long(main["tradingsymbol"])
            gross = (mpx - float(main["entry_price"])) * self.qty
        if main.get("db_id"):
            self.repo.close_leg(main["db_id"], exit_ts=ts, exit_price=mpx,
                                exit_reason=reason, pnl=round(gross, 2),
                                exit_order_id=moid)
        total = gross
        if wing is not None:
            wpx = self._mark(wing["tradingsymbol"], wing["entry_price"])
            woid = self._close_long(wing["tradingsymbol"])
            wg = (wpx - float(wing["entry_price"])) * self.qty
            total += wg
            if wing.get("db_id"):
                self.repo.close_leg(wing["db_id"], exit_ts=ts, exit_price=wpx,
                                    exit_reason=reason, pnl=round(wg, 2),
                                    exit_order_id=woid)
        self.pos = None
        write_audit_log(f"[VET][MGR] CLOSE {pos['group_id']} {reason} "
                        f"gross {total:,.0f}")
        return {"group_id": pos["group_id"], "reason": reason,
                "gross": round(total, 2)}

    def _mark(self, sym: str, fallback: float) -> float:
        if self.quote_fn:
            try:
                q = self.quote_fn(sym)
                if q is not None and float(q) > 0:
                    return float(q)
            except Exception:
                pass
        write_audit_log(f"[VET][MGR] no quote for {sym} — exiting at last "
                        f"known {fallback}")
        return float(fallback)

    # ── the per-bar entry point ─────────────────────────────────────────
    def on_decision(self, decision: Dict, *, ts: int) -> Optional[Dict]:
        """Apply one engine decision. Returns a summary of what happened."""
        action = decision.get("action", HOLD)
        if action == HOLD:
            return None
        side = decision.get("side")
        bar_ts = decision.get("bar_ts") or ts
        cond = decision.get("condition", 0)
        if action == EXIT:
            return self.close_position(decision.get("reason") or "SIGNAL_EXIT",
                                       ts=ts)
        if action == ENTER:
            return self.open_position(side, ts=ts, bar_ts=bar_ts,
                                      condition=cond)
        if action == FLIP:
            out = self.close_position(decision.get("reason") or "FLIP", ts=ts)
            opened = self.open_position(side, ts=ts, bar_ts=bar_ts,
                                        condition=cond)
            # A flip whose re-entry is refused (no wing, no strike, cap hit)
            # correctly leaves the book FLAT — the exit still stands.
            return {"flip": True, "closed": out,
                    "opened": bool(opened)}
        return None

    # ── boundaries ──────────────────────────────────────────────────────
    def eod_square_off(self, ts: int) -> Optional[Dict]:
        """Only meaningful when eod_square is ON; positional mode carries."""
        if not self.cfg.get("eod_square", True):
            return None
        return self.close_position(R_EOD, ts=ts)

    def expiry_exit(self, ts: int) -> Optional[Dict]:
        """A contract is never held past its own expiry, in either mode."""
        return self.close_position(R_EXPIRY, ts=ts)

    def kill(self, ts: Optional[int] = None) -> Optional[Dict]:
        """Kill-switch adapter target: flatten and refuse to reopen."""
        self.freeze("kill switch")
        return self.close_position(R_KILL, ts=ts or now_ts())

    # ── restart ─────────────────────────────────────────────────────────
    def resume_from_db(self) -> Optional[Dict]:
        """Rebuild the open position after a restart.

        Without this a mid-session restart opens a SECOND position beside the
        one already in the market — the failure the checklist makes a
        mandatory smoke leg.
        """
        g = self.repo.open_group(self.mode)
        if not g or not g.get("main"):
            self.pos = None
            return None
        main, wing = dict(g["main"]), (dict(g["wing"]) if g.get("wing") else None)
        main["db_id"] = main.get("id")
        if wing:
            wing["db_id"] = wing.get("id")
        self.pos = {"group_id": g["group_id"],
                    "side": main.get("instrument_type"),
                    "main": main, "wing": wing}
        write_audit_log(f"[VET][MGR] resumed {g['group_id']} "
                        f"{main.get('tradingsymbol')} from DB")
        return self.pos

    def state(self) -> Dict:
        p = self.pos
        return {
            "mode": self.mode, "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
            "leg_action": self.cfg.get("leg_action"),
            "eod_square": self.cfg.get("eod_square"),
            "hedged": self.wants_wing, "qty": self.qty,
            "day_entries": self._day_entries,
            "position": None if not p else {
                "group_id": p["group_id"], "side": p["side"],
                "symbol": p["main"].get("tradingsymbol"),
                "entry_price": p["main"].get("entry_price"),
                "direction": p["main"].get("direction"),
                "wing": (p["wing"] or {}).get("tradingsymbol"),
                "wing_entry": (p["wing"] or {}).get("entry_price"),
            }}