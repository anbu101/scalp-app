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
#
# ── VET_DTE_LIVE_20261005 ── per-DTE lots / day skip (dte_lot_mult, the
# BRK/ORB live key and resolver). apply_day_lots() runs once per session at
# arm: skip → no NEW entries (open/carried positions are still managed; a
# FLIP degrades to exit-only); scaled → entry orders + rows at the day's
# lots. Exits, P&L and charges use the LEG's stored qty, so a carried
# position always closes exactly what it opened.
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

# ── VET_LIVE_CONFIRM_20261026 ── LIVE orders are confirmed, never assumed.
# Found 2026-10-26 (pre-go-live review after the TMA_V2 incident):
#   * place_buy / place_sell_entry return (order_id, limit, qty) TUPLES; the
#     tuple was stored as entry_order_id → sqlite "type 'tuple' is not
#     supported" → insert_leg FAILED → NO vet_trades row for any LIVE leg
#     (no restart adoption, no EOD card, no shadow twin).
#   * no fill confirmation anywhere: the short was sold without knowing the
#     wing filled (defeating LD5), a REJECTED short was still "held" (the
#     exit would then BUY → an accidental long), exits were fire-and-forget
#     and the wing was sold in the same breath as the buy-back (naked short /
#     margin rejection), and every leg was booked at the chain mark.
# Now: entries wait for fills (cancel + confirm on timeout), exits are
# DRIVEN (re-peg / cancel / confirm — exit_order_driver), the wing is sold
# only once the short is confirmed flat, a stuck exit books nothing and is
# retried every minute (retry_exit), and LIVE bookings use broker fills.
try:
    from app.execution.exit_order_driver import drive_exit_order
except ImportError:                                        # standalone tests
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _P
    _sp = _ilu.spec_from_file_location(
        "_exit_order_driver",
        _P(__file__).resolve().parents[2] / "execution" / "exit_order_driver.py")
    _m = _ilu.module_from_spec(_sp)
    _sys.modules["_exit_order_driver"] = _m
    _sp.loader.exec_module(_m)
    drive_exit_order = _m.drive_exit_order

ENTRY_CONFIRM_S = 20          # main-leg entry: wait this long, then cancel+confirm
WING_CHASE_S = 15             # wing BUY: re-pegged (protection must fill)
EXIT_CHASE_S = 25             # exits: re-peg window before cancel
EXIT_REPEG_EVERY_S = 4
EXIT_REALERT_EVERY = 5        # stuck exit: re-page every N attempts (≈ minutes)


def _oid(res) -> Optional[str]:
    """Executors return (order_id, limit, qty) for entries and a bare id for
    exits; harness fakes return bare ids. Always a str (or None)."""
    if res is None:
        return None
    if isinstance(res, (tuple, list)):
        return str(res[0]) if res and res[0] is not None else None
    return str(res)


R_KILL = "KILL"
R_SL = "SL"   # ── VET_SLTP_LIVE_20261015 ──
R_TP = "TP"


def _fresh_vet_cfg():
    """── VET_SLTP_LIVE_20261015 ── Settings as saved NOW (SL/TP % for the next entry)."""
    from app.config.strategy_loader import load_strategy_config
    return load_strategy_config("VET_V1")


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
                 executor=None, mode: str = "PAPER",
                 cfg_reader: Optional[Callable] = None):   # ── VET_SLTP_LIVE_20261015 ──
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
        # ── FLEET_MODES_20260923 ── OFF gate, injected by the selection loop (fresh config
        # read per entry); None in harnesses.
        self.entries_gate: Optional[Callable[[], bool]] = None
        # ── VET_DTE_LIVE_20261005 ── set by apply_day_lots(); None/False = base
        self.day_lots: Optional[int] = None
        self.day_skip: bool = False
        self.day_dte: Optional[int] = None
        self.day_dte_tag: str = "base"
        # ── VET_SLTP_LIVE_20261015 ── Settings reader for SL/TP % at entry
        # (None → this manager's own cfg) + the runner's block_target, held as
        # the blocked option side
        # Injected ONLY by the live selection loop; a harness / test manager
        # keeps using its own cfg and never reads the machine's Settings.
        self.cfg_reader: Optional[Callable] = cfg_reader
        self.sltp_block_side: Optional[str] = None
        import time as _t                     # ── VET_LIVE_CONFIRM_20261026 ──
        self._sleep, self._clock = _t.sleep, _t.time   # test-injectable

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

    # ── VET_DTE_LIVE_20261005 ── sizing for NEW entries / existing legs
    @property
    def entry_qty(self) -> int:
        if self.day_lots:
            q = self.cfg.get("quantity") or {}
            return int(self.day_lots) * int(q.get("lot_size", 65))
        return self.qty

    @property
    def entry_lots(self):
        if self.day_lots:
            return int(self.day_lots)
        return (self.cfg.get("quantity") or {}).get("lots")

    def _leg_qty(self, leg: Optional[Dict]) -> int:
        try:
            q = int((leg or {}).get("qty") or 0)
        except (TypeError, ValueError):
            q = 0
        return q if q > 0 else self.qty

    def apply_day_lots(self, day=None) -> str:
        """Resolve today's lots from cfg['dte_lot_mult'] (BRK/ORB resolver).
        Never raises; any failure leaves base lots and entries allowed."""
        self.day_lots, self.day_skip = None, False
        self.day_dte, self.day_dte_tag = None, "base"
        try:
            from app.engine.dte_live import resolve_day_lots
            base = int((self.cfg.get("quantity") or {}).get("lots", 10) or 10)
            lots, dte, tag = resolve_day_lots(self.cfg, base, day, "[VET]")
        except Exception as e:
            write_audit_log(f"[VET][DTE] resolve failed ({e!r}) — base lots")
            return "base"
        self.day_dte, self.day_dte_tag = dte, tag
        if tag == "scaled":
            self.day_lots = int(lots)
        elif tag == "skip":
            self.day_skip = True
            msg = (f"VET_V1: today is {dte}DTE — no new entries "
                   f"(DTE × lots = 0). Open positions are still managed.")
            write_audit_log(f"[VET][DTE_SKIP] {msg}")
            try:
                from app.event_bus.inapp_events import record_alert
                record_alert("DTE_SKIP", msg, severity="info",
                             strategy_id="VET_V1", mode=self.mode.lower())
            except Exception:
                pass
        return tag

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
    def _buy(self, sym: str, token: int, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return _oid(self.executor.place_buy(sym, token, self.qty if qty is None else int(qty)))
        except Exception as e:
            write_audit_log(f"[VET][EXEC] BUY {sym} FAILED: {e}")
            return None

    def _sell_entry(self, sym: str, token: int, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return _oid(self.executor.place_sell_entry(sym, token, self.qty if qty is None else int(qty)))
        except Exception as e:
            write_audit_log(f"[VET][EXEC] SELL {sym} FAILED: {e}")
            return None

    def _close_long(self, sym: str, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return _oid(self.executor.place_market_sell(sym, self.qty if qty is None else int(qty)))
        except Exception as e:
            write_audit_log(f"[VET][EXEC] close-long {sym} FAILED: {e}")
            return None

    def _close_short(self, sym: str, reason: str, qty: Optional[int] = None) -> Optional[str]:   # ── VET_DTE_LIVE_20261005 ──
        if self.mode != "LIVE" or self.executor is None:
            return f"PAPER-{uuid.uuid4().hex[:8]}"
        try:
            return _oid(self.executor.place_buy_exit(sym, self.qty if qty is None else int(qty), reason))
        except Exception as e:
            write_audit_log(f"[VET][EXEC] close-short {sym} FAILED: {e}")
            return None

    # ── entry ───────────────────────────────────────────────────────────
    def open_position(self, side: str, *, ts: int, bar_ts: int,
                      condition: int) -> Optional[Dict]:
        if self.frozen or self.pos is not None:
            return None
        if self.entries_gate is not None and not self.entries_gate():   # ── FLEET_MODES_20260923 ──
            write_audit_log("[VET][MGR] entry suppressed — strategy OFF (no new entries)")
            return None
        if self.day_skip:   # ── VET_DTE_LIVE_20261005 ──
            write_audit_log(f"[VET][MGR] entry suppressed — {self.day_dte}DTE "
                            f"skip day (dte_lot_mult)")
            return None
        _eq, _el = self.entry_qty, self.entry_lots   # ── VET_DTE_LIVE_20261005 ──
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
            wing_oid = self._buy(wing["tradingsymbol"], wing.get("token"), _eq)
            if wing_oid is None:
                write_audit_log("[VET][MGR] wing BUY failed — entry abandoned "
                                "before any risk was taken")
                return None
            wing_row = dict(wing)
            wing_row["order_id"] = wing_oid
            if self._live:
                # ── VET_LIVE_CONFIRM_20261026 ── the short is sold ONLY once
                # the wing is CONFIRMED filled (LD5 needs a fill, not an order).
                r = self._drive(wing_oid, wing["tradingsymbol"], "BUY",
                                chase_s=WING_CHASE_S, repeg=True)
                wq = (r.filled_qty or (_eq if r.filled else 0))
                if r.outcome == "UNRESOLVED":
                    self._page(f"VET_V1: wing BUY order {wing_oid} on "
                               f"{wing['tradingsymbol']} could not be confirmed "
                               f"filled OR cancelled — check Kite Orders → Open. "
                               f"No short placed; entries FROZEN.")
                    self.freeze("wing entry order unresolved")
                    return None
                if wq <= 0:
                    write_audit_log(f"[VET][MGR] wing BUY {wing_oid} not filled — "
                                    f"entry abandoned before any risk was taken")
                    return None
                wing_row["ltp"] = r.avg_price or wing_row["ltp"]
                if wq < _eq:
                    write_audit_log(f"[VET][MGR] wing partial {wq}/{_eq} — sizing "
                                    f"the short to the wing")
                    _eq = wq

        # ── then the main leg ──
        if self.is_sell:
            main_oid = self._sell_entry(main["tradingsymbol"], main.get("token"), _eq)
        else:
            main_oid = self._buy(main["tradingsymbol"], main.get("token"), _eq)
        main_px = float(main["ltp"])
        if main_oid is not None and self._live:
            r = self._drive(main_oid, main["tradingsymbol"],
                            "SELL" if self.is_sell else "BUY",
                            chase_s=ENTRY_CONFIRM_S, repeg=False)
            mq = (r.filled_qty or (_eq if r.filled else 0))
            if r.outcome == "UNRESOLVED":
                self._page(f"VET_V1: main entry order {main_oid} on "
                           f"{main['tradingsymbol']} could not be confirmed "
                           f"filled OR cancelled — the position may EXIST. Check "
                           f"Kite NOW." + (f" Wing {wing_row['tradingsymbol']} "
                                           f"KEPT (it covers a possible short)."
                                           if wing_row else "")
                           + " Entries FROZEN.")
                self.freeze("main entry order unresolved")
                return None
            if mq <= 0:
                write_audit_log(f"[VET][MGR] main order {main_oid} not filled "
                                f"({r.status} {r.message})")
                main_oid = None
            else:
                main_px = r.avg_price or main_px
                if mq < _eq:
                    write_audit_log(f"[VET][MGR] main partial {mq}/{_eq} — "
                                    f"tracking the filled qty (wing covers it)")
                main["_filled_qty"] = mq
        if main_oid is None:
            if wing_row is not None:
                # unwind the wing rather than sit on a naked long hedge
                ok = self._unwind_long(wing_row["tradingsymbol"], _eq)
                write_audit_log("[VET][MGR] main leg failed AFTER wing filled "
                                + ("— wing sold back, position abandoned" if ok
                                   else "— wing UNWIND NOT CONFIRMED (paged)"))
            return None

        rows: List[Dict] = []
        if wing_row is not None:
            rows.append({
                "group_id": gid, "leg_role": "WING", "mode": self.mode,
                "direction": "LONG",
                "tradingsymbol": wing_row["tradingsymbol"],
                "token": wing_row.get("token"), "instrument_type": side,
                "strike": wing_row.get("strike"),
                "expiry": wing_row.get("expiry"), "qty": _eq,
                "lots": _el,
                "lot_size": (self.cfg.get("quantity") or {}).get("lot_size"),
                "entry_ts": ts, "entry_price": float(wing_row["ltp"]),
                "entry_order_id": _oid(wing_oid), "signal_bar_ts": bar_ts,
                "condition": condition, "leg_action": "BUY"})
        rows.append({
            "group_id": gid, "leg_role": "MAIN", "mode": self.mode,
            "direction": "SHORT" if self.is_sell else "LONG",
            "tradingsymbol": main["tradingsymbol"], "token": main.get("token"),
            "instrument_type": side, "strike": main.get("strike"),
            "expiry": main.get("expiry"), "qty": int(main.get("_filled_qty") or _eq),
            "lots": _el,
            "lot_size": (self.cfg.get("quantity") or {}).get("lot_size"),
            "entry_ts": ts, "entry_price": float(main_px),
            "entry_order_id": _oid(main_oid), "signal_bar_ts": bar_ts,
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
        self._stamp_levels(self.pos["main"], "entry")   # ── VET_SLTP_LIVE_20261015 ──
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
        if self._live:
            return self._close_live(reason, ts=ts)   # ── VET_LIVE_CONFIRM_20261026 ──
        pos = self.pos
        main, wing = pos["main"], pos["wing"]

        mpx = self._mark(main["tradingsymbol"], main["entry_price"])
        mq = self._leg_qty(main)   # ── VET_DTE_LIVE_20261005 ── close what was opened
        if self.is_sell:
            moid = self._close_short(main["tradingsymbol"], reason, mq)
            gross = (float(main["entry_price"]) - mpx) * mq
        else:
            moid = self._close_long(main["tradingsymbol"], mq)
            gross = (mpx - float(main["entry_price"])) * mq
        # ── FLEET_EOD_ALERT_FIX_20260908 ── persist charges + net_pnl.
        # The EOD card (_merge_tma over vet_trades) sums net_pnl per group
        # and skips groups where it is NULL — Velvet's paper day vanished
        # from the summary on 2026-09-08 for exactly that reason.
        m_ch = self._leg_charges(self.is_sell, main["entry_price"], mpx, mq)
        if main.get("db_id"):
            self.repo.close_leg(main["db_id"], exit_ts=ts, exit_price=mpx,
                                exit_reason=reason, pnl=round(gross, 2),
                                charges=round(m_ch, 2),
                                net_pnl=round(gross - m_ch, 2),
                                exit_order_id=moid)
        total = gross
        total_net = gross - m_ch
        if wing is not None:
            wpx = self._mark(wing["tradingsymbol"], wing["entry_price"])
            wq = self._leg_qty(wing)   # ── VET_DTE_LIVE_20261005 ──
            woid = self._close_long(wing["tradingsymbol"], wq)
            wg = (wpx - float(wing["entry_price"])) * wq
            total += wg
            w_ch = self._leg_charges(False, wing["entry_price"], wpx, wq)   # ── FLEET_EOD_ALERT_FIX_20260908 ──
            total_net += wg - w_ch
            if wing.get("db_id"):
                self.repo.close_leg(wing["db_id"], exit_ts=ts, exit_price=wpx,
                                    exit_reason=reason, pnl=round(wg, 2),
                                    charges=round(w_ch, 2),
                                    net_pnl=round(wg - w_ch, 2),
                                    exit_order_id=woid)
        self.pos = None
        write_audit_log(f"[VET][MGR] CLOSE {pos['group_id']} {reason} "
                        f"gross {total:,.0f} net {total_net:,.0f}")
        return {"group_id": pos["group_id"], "reason": reason,
                "gross": round(total, 2), "net": round(total_net, 2)}

    # ── FLEET_EOD_ALERT_FIX_20260908 ── same charges model as the VET
    # backtest runner (charges_for_short_trade for the SELL main leg,
    # charges_for_long_trade otherwise). Best-effort: any failure is logged
    # and charged as 0 so the close is never blocked; net then equals gross
    # (still NOT NULL, so the EOD card counts the trade).
    def _leg_charges(self, is_short: bool, entry: float, exit_px: float,
                     qty: Optional[int] = None) -> float:   # ── VET_DTE_LIVE_20261005 ──
        try:
            from app.backtest.charges.charges_model import (
                charges_for_long_trade, charges_for_short_trade)
            fn = charges_for_short_trade if is_short else charges_for_long_trade
            cr = fn(entry_price=float(entry), exit_price=float(exit_px),
                    qty=int(self.qty if qty is None else qty))
            return float(getattr(cr, "total_charges", 0.0) or 0.0)
        except Exception as e:
            write_audit_log(f"[VET][MGR] charges calc failed ({e!r}) — "
                            f"booking net = gross")
            return 0.0

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

    # ── VET_SLTP_LIVE_20261015 BEGIN ──────────────────────────────────────
    # The Settings page has always offered "SL % / TP % of entry premium",
    # and the BACKTEST runner honours them (backtest_vet_runner step 1c), but
    # live never did — a configured TP 90% silently did nothing. Live now
    # mirrors the runner exactly:
    #   * levels from the % of the MAIN leg's entry premium (SHORT: stop =
    #     premium RISING, target = premium FALLING), stamped on the leg at
    #     entry (sl / tp columns) from the CURRENT Settings — a later edit
    #     never moves an open position's levels;
    #   * checked at each 5-minute decision close, BEFORE the signal, on the
    #     same mark the exits use (last 1m close, stale-carried); SL wins
    #     over TP; exit at that mark, wing closed with it;
    #   * after an SL/TP exit, no re-entry while the trend target is
    #     unchanged (runner block_target) unless reenter_after_sltp.
    # Both % = 0 (every sealed config) → no levels → behaviour unchanged.
    @property
    def reenter_after_sltp(self) -> bool:
        return bool(self.cfg.get("reenter_after_sltp", False))

    def _sltp_pcts(self):
        src = None
        try:
            src = self.cfg_reader() if self.cfg_reader is not None else None
        except Exception:
            src = None
        if not isinstance(src, dict):
            src = self.cfg

        def _f(k):
            try:
                return abs(float(src.get(k) or 0))
            except (TypeError, ValueError):
                return 0.0
        return _f("sl_pct"), _f("tp_pct")

    def sltp_levels(self, entry, sl_pct=None, tp_pct=None):
        """(sl, tp) price levels for a MAIN-leg entry premium; None = off."""
        if sl_pct is None or tp_pct is None:
            sl_pct, tp_pct = self._sltp_pcts()
        e = float(entry)
        # UNROUNDED on purpose: the runner compares the mark to exactly this
        # float (step 1c); rounding would flip ties at the 0.05 tick grid.
        if self.is_sell:
            sl = e * (1 + sl_pct / 100.0) if sl_pct > 0 else None
            tp = e * (1 - tp_pct / 100.0) if tp_pct > 0 else None
        else:
            sl = e * (1 - sl_pct / 100.0) if sl_pct > 0 else None
            tp = e * (1 + tp_pct / 100.0) if tp_pct > 0 else None
        return sl, tp

    def _stamp_levels(self, main: Dict, why: str) -> None:
        sl, tp = self.sltp_levels(main["entry_price"])
        main["sl"], main["tp"] = sl, tp
        if main.get("db_id"):
            self.repo.set_levels(main["db_id"], sl, tp)
        if sl is not None or tp is not None:
            write_audit_log(f"[VET][SLTP] {main.get('tradingsymbol')} entry "
                            f"{main['entry_price']} → SL {None if sl is None else round(sl, 2)} · "
                            f"TP {None if tp is None else round(tp, 2)} ({why})")

    def check_sltp(self, ts: int) -> Optional[str]:
        """Runner step 1c. Call at a 5m decision close, before decide().
        Returns R_SL / R_TP when it closed the position, else None."""
        p = self.pos
        if p is None:
            return None
        main = p["main"]
        sl, tp = main.get("sl"), main.get("tp")
        if sl is None and tp is None:
            return None
        mark = None
        if self.quote_fn is not None:
            try:
                q = self.quote_fn(main["tradingsymbol"])
                mark = float(q) if q is not None and float(q) > 0 else None
            except Exception:
                mark = None
        if mark is None:
            write_audit_log(f"[VET][SLTP] no mark for {main['tradingsymbol']} at "
                            f"this close — levels not evaluated")
            return None
        if self.is_sell:
            sl_hit = sl is not None and mark >= float(sl)
            tp_hit = tp is not None and mark <= float(tp)
        else:
            sl_hit = sl is not None and mark <= float(sl)
            tp_hit = tp is not None and mark >= float(tp)
        hit = R_SL if sl_hit else (R_TP if tp_hit else None)
        if hit is None:
            return None
        write_audit_log(f"[VET][SLTP] {hit} {main['tradingsymbol']} mark {mark} "
                        f"(SL {sl} · TP {tp}) — closing")
        self.close_position(hit, ts=ts)
        return hit

    def sltp_gate(self, decision: Dict, hit: Optional[str]) -> bool:
        """Runner block_target parity. False = suppress this decision's
        entry. Call after decide(), with check_sltp()'s result."""
        want = decision.get("side") if decision.get("action") == ENTER else None
        if hit and not self.reenter_after_sltp:
            self.sltp_block_side = want
        if self.pos is None and self.sltp_block_side:
            if want == self.sltp_block_side:
                return False
            write_audit_log(f"[VET][SLTP] trend changed — re-entry block on "
                            f"{self.sltp_block_side} cleared")
            self.sltp_block_side = None
        return True

    def restore_sltp_block(self, now: Optional[int] = None) -> Optional[str]:
        """After a restart: today's last exit was SL/TP and nothing was
        entered since → block that side again (the runner's block survives
        the bar loop; a restart must not erase it)."""
        if self.pos is not None or self.reenter_after_sltp:
            return None
        now = int(now or now_ts())
        d0 = ((now + 19800) // 86400) * 86400 - 19800
        rows = [r for r in self.repo.trades_on(d0 - 10 * 86400, now + 1, self.mode)
                if r.get("leg_role") == "MAIN"]
        if not rows:
            return None
        last = max(rows, key=lambda r: max(int(r.get("entry_ts") or 0),
                                           int(r.get("exit_ts") or 0)))
        if (str(last.get("status") or "") == "CLOSED"
                and str(last.get("exit_reason") or "") in (R_SL, R_TP)
                and int(last.get("exit_ts") or 0) >= d0):
            self.sltp_block_side = last.get("instrument_type")
            write_audit_log(f"[VET][SLTP] restart: today's last exit was "
                            f"{last.get('exit_reason')} — no re-entry on "
                            f"{self.sltp_block_side} until the trend changes")
        return self.sltp_block_side
    # ── VET_SLTP_LIVE_20261015 END ────────────────────────────────────────

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
            if self.pos is not None:     # ── VET_LIVE_CONFIRM_20261026 ── close stuck
                return {"flip": True, "closed": None, "opened": False}
            opened = self.open_position(side, ts=ts, bar_ts=bar_ts,
                                        condition=cond)
            # A flip whose re-entry is refused (no wing, no strike, cap hit)
            # correctly leaves the book FLAT — the exit still stands.
            return {"flip": True, "closed": out,
                    "opened": bool(opened)}
        return None

    # ── VET_LIVE_CONFIRM_20261026 BEGIN ── live order confirmation ─────────
    @property
    def _live(self) -> bool:
        return self.mode == "LIVE" and self.executor is not None

    @property
    def exit_pending(self) -> bool:
        return bool(self.pos and self.pos.get("exit_state"))

    def retry_exit(self, ts: int) -> Optional[Dict]:
        """Coordinator hook, every minute: finish a LIVE exit that could not
        complete (the stuck leg is re-driven; nothing new is decided)."""
        if not self.exit_pending:
            return None
        return self._close_live(self.pos["exit_state"]["reason"], ts=ts)

    def _drive(self, oid, sym: str, side: str, *, chase_s: float = EXIT_CHASE_S,
               repeg: bool = True):
        return drive_exit_order(self.executor, str(oid), symbol=sym, side=side,
                                chase_s=chase_s, repeg_every_s=EXIT_REPEG_EVERY_S,
                                repeg=repeg, log=write_audit_log,
                                sleep=self._sleep, clock=self._clock)

    def _page(self, msg: str, code: str = "VET_LIVE_ORDER") -> None:
        write_audit_log(f"[VET][PAGE] {msg}")
        try:
            from app.event_bus.inapp_events import record_alert
            record_alert(code, msg, severity="error", strategy_id="VET_V1",
                         mode="live")
        except Exception:
            pass
        try:
            from app.api.telegram_api import notify_critical
            notify_critical({"message": msg, "severity": "error"})
        except Exception:
            pass

    def _net_qty(self, sym: str) -> Optional[int]:
        fn = getattr(self.executor, "get_open_positions_or_none", None)
        if not callable(fn):
            return None
        try:
            pos = fn()
        except Exception:
            return None
        if pos is None:
            return None
        for p in pos:
            if p.get("tradingsymbol") == sym:
                return int(p.get("quantity") or 0)
        return 0

    def _last_fill(self, sym: str, txn: str) -> Optional[float]:
        try:
            for o in reversed(self.executor.get_orders() or []):
                if (o.get("tradingsymbol") == sym and o.get("transaction_type") == txn
                        and (o.get("status") or "").upper() == "COMPLETE"
                        and float(o.get("average_price") or 0) > 0):
                    return float(o["average_price"])
        except Exception:
            pass
        return None

    def _unwind_long(self, sym: str, qty: int) -> bool:
        oid = self._close_long(sym, qty)
        if oid is not None:
            r = self._drive(oid, sym, "SELL")
            if r.filled:
                return True
            n = self._net_qty(sym)
            if n is not None and n <= 0:
                return True
        self._page(f"VET_V1: wing unwind of {sym} x{qty} NOT confirmed — the "
                   f"LONG may still be open. SELL it manually in Kite (check "
                   f"Orders → Open first).")
        return False

    def _exit_leg(self, st: Dict, key: str, leg: Dict, reason: str) -> bool:
        """Close one leg, confirmed. True = flat (price in st[key+'_px'])."""
        sym = leg["tradingsymbol"]
        short = str(leg.get("direction") or "").upper() == "SHORT"
        side = "BUY" if short else "SELL"
        qty = self._leg_qty(leg)
        fills = st.setdefault(key + "_fills", [])
        oid = st["working"].get(key)
        if oid:                                  # a previous attempt's order
            r = self._drive(oid, sym, side)
            if r.outcome == "UNRESOLVED":
                st["why"] = f"order {oid} could not be confirmed filled OR cancelled — CANCEL IT in Kite"
                return False
            st["working"].pop(key, None)
            if r.avg_price > 0 and (r.filled_qty or r.filled):
                fills.append((r.filled_qty or qty, r.avg_price))
        obs = self._net_qty(sym)
        flat = obs is not None and ((obs >= 0) if short else (obs <= 0))
        if not flat:
            if obs is None and fills and sum(f[0] for f in fills) >= qty:
                flat = True
            else:
                left = qty if obs is None else min(qty, abs(obs))
                oid = (self._close_short(sym, reason, left) if short
                       else self._close_long(sym, left))
                if oid is None:
                    st["why"] = "exit order was rejected at placement"
                    return False
                st["working"][key] = oid
                r = self._drive(oid, sym, side)
                if r.outcome == "UNRESOLVED":
                    st["why"] = f"order {oid} could not be confirmed filled OR cancelled — CANCEL IT in Kite"
                    return False
                st["working"].pop(key, None)
                if r.avg_price > 0 and (r.filled_qty or r.filled):
                    fills.append((r.filled_qty or left, r.avg_price))
                obs = self._net_qty(sym)
                flat = (obs is not None and ((obs >= 0) if short else (obs <= 0))) \
                    or (obs is None and r.filled)
                if not flat:
                    st["why"] = f"exit did not fill ({r.status})"
                    return False
        q = sum(f[0] for f in fills)
        px = (sum(f[0] * f[1] for f in fills) / q) if q else \
            (self._last_fill(sym, side) or self._mark(sym, leg["entry_price"]))
        st[key + "_px"] = float(px)
        st[key + "_oid"] = oid
        return True

    def _close_live(self, reason: str, *, ts: int) -> Optional[Dict]:
        pos = self.pos
        main, wing = pos["main"], pos["wing"]
        st = pos.get("exit_state")
        if st is None:
            st = pos["exit_state"] = {"reason": reason, "ts": ts, "working": {},
                                      "main_done": False, "wing_done": wing is None,
                                      "attempts": 0, "alerted": False}
            write_audit_log(f"[VET][MGR] EXIT START {pos['group_id']} {reason}")
        st["attempts"] += 1
        if not st["main_done"]:
            st["main_done"] = self._exit_leg(st, "main", main, st["reason"])
        if st["main_done"] and not st["wing_done"]:
            st["wing_done"] = self._exit_leg(st, "wing", wing, st["reason"])
        if not (st["main_done"] and st["wing_done"]):
            stuck = "main" if not st["main_done"] else "wing"
            write_audit_log(f"[VET][MGR] EXIT STUCK {pos['group_id']} leg={stuck} "
                            f"attempt={st['attempts']} {st.get('why')} — nothing "
                            f"booked, retrying next minute")
            if not st["alerted"] or st["attempts"] % EXIT_REALERT_EVERY == 0:
                st["alerted"] = True
                if stuck == "main":
                    msg = (f"VET_V1 {st['reason']}: exit of {main['tradingsymbol']} "
                           f"NOT CONFIRMED ({st.get('why')}) — "
                           f"{main.get('direction')} STILL OPEN."
                           + (f" Wing {wing['tradingsymbol']} KEPT. To flatten "
                              f"manually: BUY {main['tradingsymbol']} FIRST, then "
                              f"SELL {wing['tradingsymbol']}." if wing else "")
                           + " App retries every minute.")
                else:
                    msg = (f"VET_V1 {st['reason']}: main leg closed, but the wing "
                           f"{wing['tradingsymbol']} sell is NOT CONFIRMED "
                           f"({st.get('why')}) — long wing still open. App retries "
                           f"every minute, or SELL it manually.")
                self._page(msg, code="VET_EXIT_STUCK")
            return None
        # ── book from fills ──
        mq = self._leg_qty(main)
        mpx = st["main_px"]
        short = str(main.get("direction") or "").upper() == "SHORT"
        gross = ((float(main["entry_price"]) - mpx) if short
                 else (mpx - float(main["entry_price"]))) * mq
        m_ch = self._leg_charges(short, main["entry_price"], mpx, mq)
        if main.get("db_id"):
            self.repo.close_leg(main["db_id"], exit_ts=ts, exit_price=mpx,
                                exit_reason=st["reason"], pnl=round(gross, 2),
                                charges=round(m_ch, 2),
                                net_pnl=round(gross - m_ch, 2),
                                exit_order_id=st.get("main_oid"))
        total, total_net = gross, gross - m_ch
        if wing is not None:
            wq, wpx = self._leg_qty(wing), st["wing_px"]
            wg = (wpx - float(wing["entry_price"])) * wq
            w_ch = self._leg_charges(False, wing["entry_price"], wpx, wq)
            total += wg
            total_net += wg - w_ch
            if wing.get("db_id"):
                self.repo.close_leg(wing["db_id"], exit_ts=ts, exit_price=wpx,
                                    exit_reason=st["reason"], pnl=round(wg, 2),
                                    charges=round(w_ch, 2),
                                    net_pnl=round(wg - w_ch, 2),
                                    exit_order_id=st.get("wing_oid"))
        if st["alerted"]:
            self._page(f"VET_V1: exit of {pos['group_id']} now CONFIRMED after "
                       f"{st['attempts']} attempts — all legs flat.",
                       code="VET_EXIT_RECOVERED")
        self.pos = None
        write_audit_log(f"[VET][MGR] CLOSE {pos['group_id']} {st['reason']} "
                        f"gross {total:,.0f} net {total_net:,.0f} (broker fills)")
        return {"group_id": pos["group_id"], "reason": st["reason"],
                "gross": round(total, 2), "net": round(total_net, 2)}
    # ── VET_LIVE_CONFIRM_20261026 END ──────────────────────────────────────

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
        # ── VET_SLTP_LIVE_20261015 ── a position with no levels (opened before
        # this fix, or with both % at 0) takes them from CURRENT Settings once;
        # a stamped position keeps its own.
        if main.get("sl") is None and main.get("tp") is None:
            self._stamp_levels(main, "adopted — levels from current Settings")
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
            # ── VET_DTE_LIVE_20261005 ──
            "day_lots": self.day_lots, "dte": self.day_dte,
            "dte_tag": self.day_dte_tag, "entry_qty": self.entry_qty,
            "position": None if not p else {
                "group_id": p["group_id"], "side": p["side"],
                "symbol": p["main"].get("tradingsymbol"),
                "entry_price": p["main"].get("entry_price"),
                "direction": p["main"].get("direction"),
                "wing": (p["wing"] or {}).get("tradingsymbol"),
                "wing_entry": (p["wing"] or {}).get("entry_price"),
            }}