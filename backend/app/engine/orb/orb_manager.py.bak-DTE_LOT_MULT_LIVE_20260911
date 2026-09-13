# backend/app/engine/orb/orb_manager.py
#
# ── ORB_V1 MANAGER ── orders + persistence for "Outrider". Fence: ORB_LIVE_20260903
#
# Decisions live in orb_live_core (parity-by-construction); this class owns
# fills, paper rows, notifications and the kill path. Cloned from BrkManager
# (the fleet's newest long-option donor) with the LD-sheet differences:
#   * NO GTT LAYER (LD4 rev B, VET doctrine): all three exits — premium TP,
#     spot-close SL, 13:00 EOD — are ENGINE exits at 1m closes, market-sold.
#     Divergence ledger: backtest books TP AT the level intrabar; live/paper
#     book at the first minute CLOSE ≥ level (fills later/equal — the
#     conservative side). Paper books AT the level on that close (backtest
#     convention); LIVE takes the real market fill.
#   * Generic paper_trades storage (LD8) — checklist 2.9/2.12 no-ops.

from __future__ import annotations

import time
import uuid
from typing import Callable, Optional

STRATEGY_ID = "ORB_V1"
FILL_TIMEOUT_S = 20
FILL_POLL_S = 1.0

# ── ORB_DAY1SCARS_20260904 ── import fallbacks are SPLIT PER CONCERN and
# every engaged fallback is recorded (BRK day-1 scar: one blanket except
# hid a nonexistent import; a manager traded LIVE with persistence and
# audit logging silently disabled). _DEGRADED non-empty => open_trade
# REFUSES (persistence-down never trades from memory).
_DEGRADED = []

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    _DEGRADED.append("audit_logger")
    def write_audit_log(msg):                              # type: ignore
        print(msg)

try:
    from app.event_bus.inapp_events import record_alert
except ImportError:
    _DEGRADED.append("inapp_events")
    def record_alert(**k):                                 # type: ignore
        print("ALERT", k)

try:
    from app.db.paper_trades_repo import (insert_paper_trade,
                                          close_paper_trade, get_conn)
except ImportError:
    _DEGRADED.append("paper_trades_repo")
    insert_paper_trade = close_paper_trade = get_conn = None  # type: ignore

try:
    # ── ORB_SILENTZERO_20260905 ── the original cfg() imported a
    # STRATEGY_CONFIG global that DOES NOT EXIST in strategy_loader; the
    # lazy ImportError made cfg() return {} and the whole day died as a
    # swallowed KeyError per bar. load_strategy_config is the real API
    # (defaults merged with the saved file — Settings edits apply live).
    from app.config.strategy_loader import load_strategy_config
except ImportError:
    _DEGRADED.append("strategy_loader")
    load_strategy_config = None  # type: ignore

try:
    from app.engine.orb.orb_live_core import OrbLiveDay
except ImportError:
    from orb_live_core import OrbLiveDay                   # type: ignore


class OrbPositionRow:
    def __init__(self, **k):
        self.__dict__.update(k)


class OrbManager:
    """One instance per process. The engine calls open_trade / close_trade /
    mark_minute; state routes read the public surface; kill_switch calls
    kill_all()."""

    def __init__(self, executor=None, *, cfg_fn: Optional[Callable] = None,
                 notifier=None):
        self.executor = executor
        self.cfg_fn = cfg_fn
        self.notifier = notifier
        self.pos: Optional[OrbPositionRow] = None
        self._allow_degraded = False        # tests only — never set live
        self._close_backoff_n = 0
        self._close_backoff_until = 0.0
        self.day: Optional[OrbLiveDay] = None
        self.day_stats = {"signals": 0, "entries": 0, "exits": {},
                          "refused": None, "frozen": None}

    # ── config / mode ──
    def cfg(self) -> dict:
        if self.cfg_fn:
            return self.cfg_fn() or {}
        if load_strategy_config is None:
            return {}
        try:
            return load_strategy_config(STRATEGY_ID) or {}
        except Exception:
            return {}

    def mode(self) -> str:
        m = str(self.cfg().get("trade_execution_mode", "PAPER")).upper()
        return m if m in ("PAPER", "LIVE", "OFF") else "PAPER"

    def attach_executor(self, executor) -> None:
        self.executor = executor

    def _qty(self):
        cfg = self.cfg()
        lots = int(cfg.get("lots") or 1)
        lot_size = int(cfg.get("lot_size") or 0) or 65
        return lots, lot_size, lots * lot_size

    def _alert(self, code, msg, severity="warning"):
        write_audit_log(f"[ORB][{code}] {msg}")
        try:
            # ── ORB_DAY1SCARS_20260904 ── call copied from the REAL
            # signature (inapp_events.record_alert), not from a donor:
            # BRK's source= kwarg TypeErrors silently (found 2026-09-04).
            record_alert(code, msg, severity=severity,
                         strategy_id=STRATEGY_ID)
        except Exception:
            pass

    def _notify(self, fn_name, payload):
        if not self.notifier:
            return
        try:
            getattr(self.notifier, fn_name)(payload)
        except Exception:
            pass

    # ── entry (engine calls on a core SIGNAL after candidate selection) ──
    def open_trade(self, *, symbol: str, token: int, side: str,
                   ltp: float, entry_spot: float, sig_ts: int) -> bool:
        if "paper_trades_repo" in _DEGRADED and not self._allow_degraded:
            self._alert("PERSIST_DOWN", "persistence layer degraded "
                        f"({_DEGRADED}) — REFUSING to trade (BRK day-1 "
                        "scar: never trade from memory)", "critical")
            if self.day:
                self.day.on_entry_abandoned()
            return False
        if self.pos is not None or self.day is None:
            self._alert("DOUBLE_ENTRY", f"{symbol} refused — position open "
                        f"or no day", "error")
            if self.day:
                self.day.on_entry_abandoned()
            return False
        mode = self.mode()
        lots, lot_size, qty = self._qty()
        entry_px = float(ltp)
        if mode == "LIVE":
            if self.executor is None:
                self._alert("NO_EXECUTOR", "LIVE entry impossible — executor "
                            "missing; signal forfeited", "error")
                self.day.on_entry_abandoned()
                return False
            # exit contract preflighted at ENTRY (BRK day-1 scar: a broken
            # reconcile primitive must block the position, not the exit)
            required = ("place_buy", "place_market_sell", "get_order_fill",
                        "get_open_positions_or_none")
            missing = [m for m in required
                       if not callable(getattr(self.executor, m, None))]
            if missing:
                self._alert("EXEC_CONTRACT", f"LIVE blocked — executor "
                            f"missing {missing}; ZERO orders placed", "error")
                self.day.on_entry_abandoned()
                return False
            try:
                order_id, avg, filled = self.executor.place_buy(symbol, token, qty)
            except Exception as e:
                self._alert("BUY_FAIL", f"{symbol}: {e!r}", "error")
                self.day.on_entry_abandoned()
                return False
            fill_px, ok = float(avg or 0.0), (filled == qty and avg)
            t0 = time.time()
            while not ok and time.time() - t0 < FILL_TIMEOUT_S:
                time.sleep(FILL_POLL_S)
                try:
                    st = self.executor.get_order_fill(order_id)
                except Exception:
                    continue
                status = (st or {}).get("status")
                if status == "COMPLETE":
                    fill_px, ok = float(st.get("avg_price") or 0.0), True
                elif status in ("REJECTED", "CANCELLED") and (st or {}).get("found"):
                    self._alert("BUY_DEAD", f"{symbol} order {status}", "error")
                    self.day.on_entry_abandoned()
                    return False
            if not ok or fill_px <= 0:
                self._alert("FILL_TIMEOUT", f"{symbol} unfilled after "
                            f"{FILL_TIMEOUT_S}s — abandoning", "error")
                self.day.on_entry_abandoned()
                return False
            entry_px = fill_px
        core_pos = self.day.on_entry_fill(side=side, symbol=symbol,
                                          entry_px=entry_px,
                                          entry_spot=entry_spot,
                                          entry_ts=sig_ts + 60)
        pid = None
        if insert_paper_trade is not None:
            try:
                pid = str(uuid.uuid4())
                rr = round((core_pos.tp_prem - entry_px)
                           / max(0.01, entry_px * 0.05), 2)
                insert_paper_trade(
                    paper_trade_id=pid, strategy_name=STRATEGY_ID,
                    trade_mode=mode, symbol=symbol, token=int(token or 0),
                    side=side, entry_price=float(entry_px),
                    candle_ts=sig_ts + 60,
                    sl_price=float(core_pos.sl_spot),        # SPOT level (display)
                    tp_price=float(core_pos.tp_prem), rr=rr,
                    lots=lots, lot_size=lot_size, qty=qty,
                    trade_direction="LONG", group_id="ORB",
                    trade_class=None)
            except Exception as e:
                self._alert("ROW_FAIL", f"{symbol}: {e!r}"
                            + (" — POSITION OPEN AT BROKER, row missing"
                               if mode == "LIVE" else ""),
                            "critical" if mode == "LIVE" else "error")
                pid = None
        self.pos = OrbPositionRow(row_id=pid, symbol=symbol, token=token,
                                  side=side, entry_px=entry_px, qty=qty,
                                  lots=lots, mode=mode,
                                  sl_spot=core_pos.sl_spot,
                                  tp_prem=core_pos.tp_prem,
                                  entry_ts=sig_ts + 60)
        self.day_stats["entries"] += 1
        write_audit_log(f"[ORB][ENTRY][{mode}] {side} {symbol} @ {entry_px} "
                        f"slSpot={core_pos.sl_spot:.2f} tp={core_pos.tp_prem:.2f} "
                        f"qty={qty}")
        self._notify("notify_trade_entry", {
            # ── ORB_DAY1SCARS_20260904 ── formatters read strategy_id; the
            # key "strategy" renders "Strategy: Unknown" (BRK, 2026-09-03).
            "strategy_id": STRATEGY_ID, "mode": mode, "symbol": symbol,
            "side": side, "entry_price": entry_px, "quantity": qty,
            "sl": round(core_pos.sl_spot, 2), "tp": round(core_pos.tp_prem, 2)})
        return True

    # ── exits ──
    def close_trade(self, *, reason: str, ltp: Optional[float] = None) -> bool:
        pos = self.pos
        if pos is None:
            return False
        px = float(ltp if ltp is not None else pos.entry_px)
        if pos.mode == "LIVE" and self.executor is not None:
            # ── ORB_DAY1SCARS_20260904 ── reconcile-first flat-gate:
            # NEVER place_market_sell without a positive holdings read
            # (BRK sold 464 times into a flat book). None => unreadable
            # broker state => do nothing risky, back off (5s·n, cap 60s);
            # the engine retries next minute and the EOD job backstops.
            import time as _t
            if _t.time() < self._close_backoff_until:
                return False
            probe = getattr(self.executor, "get_open_positions_or_none", None)
            holding = None
            if callable(probe):
                try:
                    positions = probe()
                except Exception:
                    positions = None
                if positions is None:
                    self._close_backoff_n += 1
                    self._close_backoff_until = _t.time() + min(
                        60.0, 5.0 * self._close_backoff_n)
                    self._alert("BROKER_UNREADABLE",
                                f"{pos.symbol}: holdings read failed — NO "
                                f"order placed, backoff "
                                f"{min(60, 5 * self._close_backoff_n)}s",
                                "error")
                    return False
                holding = any(
                    (p.get("tradingsymbol") or p.get("symbol")) == pos.symbol
                    and int(p.get("quantity") or p.get("qty") or 0) > 0
                    for p in positions)
                if not holding:
                    self._alert("BROKER_FLAT",
                                f"{pos.symbol}: broker shows no holding — "
                                f"closing the ROW only, placing NOTHING "
                                f"(reconcile-first)", "warning")
                    self._close_row(pos, px, reason)
                    if self.day is not None:
                        self.day.on_position_closed()
                    self.pos = None
                    self._close_backoff_n = 0
                    return True
            self._close_backoff_n = 0
            try:
                order_id = self.executor.place_market_sell(pos.symbol, pos.qty)
                t0 = time.time()
                while time.time() - t0 < FILL_TIMEOUT_S:
                    try:
                        st = self.executor.get_order_fill(order_id)
                    except Exception:
                        time.sleep(FILL_POLL_S)
                        continue
                    if (st or {}).get("status") == "COMPLETE":
                        px = float(st.get("avg_price") or px)
                        break
                    time.sleep(FILL_POLL_S)
            except Exception as e:
                self._alert("SELL_FAIL", f"{pos.symbol}: {e!r} — POSITION MAY "
                            f"BE OPEN AT THE BROKER", "critical")
        self._close_row(pos, px, reason)
        if self.day is not None:
            self.day.on_position_closed()
        self.pos = None
        return True

    def _close_row(self, pos, px: float, reason: str) -> None:
        if close_paper_trade is not None and pos.row_id:
            try:
                close_paper_trade(paper_trade_id=pos.row_id,
                                  exit_price=float(px), exit_reason=reason)
            except Exception as e:
                self._alert("CLOSE_ROW_FAIL", f"{pos.symbol}: {e!r}", "error")
        self.day_stats["exits"][reason] = \
            self.day_stats["exits"].get(reason, 0) + 1
        gross = (px - pos.entry_px) * pos.qty
        write_audit_log(f"[ORB][EXIT][{pos.mode}] {reason} {pos.symbol} @ "
                        f"{px} gross={gross:,.0f}")
        self._notify("notify_trade_exit", {
            "strategy_id": STRATEGY_ID, "mode": pos.mode, "symbol": pos.symbol,
            "exit_price": px, "reason": reason,
            "pnl": round(gross, 2)})

    # ── restart (checklist smoke leg) ──
    def resume_from_db(self, rows=None) -> None:
        """Rebuild self.pos from open ORB_V1 paper_trades rows. `rows` is
        injectable for tests; default reads the canonical DB."""
        if rows is None:
            if get_conn is None:
                return
            try:
                cur = get_conn().execute(
                    "SELECT paper_trade_id, symbol, token, side, entry_price,"
                    " qty, lots, trade_mode, sl_price, tp_price, candle_ts"
                    " FROM paper_trades WHERE strategy_name=? AND"
                    " exit_price IS NULL", (STRATEGY_ID,))
                rows = [dict(zip([c[0] for c in cur.description], r))
                        for r in cur.fetchall()]
            except Exception as e:
                self._alert("RESUME_FAIL", f"{e!r}", "error")
                return
        for r in rows or []:
            self.pos = OrbPositionRow(
                row_id=r.get("paper_trade_id"), symbol=r["symbol"],
                token=r.get("token"), side=r["side"],
                entry_px=float(r["entry_price"]), qty=int(r["qty"]),
                lots=int(r.get("lots") or 1),
                mode=str(r.get("trade_mode") or "PAPER"),
                sl_spot=float(r.get("sl_price") or 0.0),
                tp_prem=float(r.get("tp_price") or 0.0),
                entry_ts=int(r.get("candle_ts") or 0))
            write_audit_log(f"[ORB][RESUME] open {self.pos.side} "
                            f"{self.pos.symbol} @ {self.pos.entry_px} "
                            f"({self.pos.mode})")
            break                                          # one at a time

    # ── ORB_DAY_COUNTERS_20260911 ────────────────────────────────────
    def restore_day_counters(self, rows=None) -> dict:
        """After a restart, put TODAY's already-CLOSED trades back into the
        core's per-day budget (max_trades_per_day / max_trades_per_side).
        A replay rebuilds levels, not history; without this a restart after
        a closed trade could admit one extra trade. Call BEFORE
        adopt_resumed_position() — that one counts the still-open row.
        rows= injectable for tests. Best-effort: on a DB failure the
        counters stay 0 (pre-existing behaviour) and it is audited."""
        out = {"CE": 0, "PE": 0}
        if self.day is None:
            return out
        try:
            if rows is None:
                if get_conn is None:
                    raise RuntimeError("paper_trades_repo degraded")
                cur = get_conn().execute(
                    "SELECT side, COUNT(*) AS n FROM paper_trades"
                    " WHERE strategy_name=? AND candle_ts >= ?"
                    " AND exit_price IS NOT NULL GROUP BY side",
                    (STRATEGY_ID, int(self.day.day_start_epoch)))
                rows = [dict(zip([c[0] for c in cur.description], r))
                        for r in cur.fetchall()]
            else:
                # injected raw rows: count closed ones from today by side
                agg: dict = {}
                for r in rows:
                    if (r.get("exit_price") is not None
                            and int(r.get("candle_ts") or 0) >= int(self.day.day_start_epoch)):
                        agg[r.get("side")] = agg.get(r.get("side"), 0) + 1
                rows = [{"side": s, "n": n} for s, n in agg.items()]
            for r in rows or []:
                s = str(r.get("side") or "").upper()
                if s in out:
                    out[s] += int(r.get("n") or 0)
            self.day.day_trades = out["CE"] + out["PE"]
            self.day.side_trades = dict(out)
            self.day_stats["entries"] = self.day.day_trades
            write_audit_log(f"[ORB][RESUME] day counters restored: "
                            f"trades={self.day.day_trades} CE={out['CE']} "
                            f"PE={out['PE']} (closed rows today)")
        except Exception as ex:
            write_audit_log(f"[ORB][RESUME][COUNTERS_FAIL] {ex!r} — day budget "
                            f"starts at 0 (may admit one extra trade)")
        return out
    # ── ORB_DAY_COUNTERS_20260911 END ────────────────────────────────

    def adopt_resumed_position(self) -> None:
        """After warm-replay rebuilt the day's core, graft the resumed row
        back into it so exits evaluate (restart parity)."""
        if self.pos is None or self.day is None:
            return
        self.day.pending_side = self.pos.side
        cp = self.day.on_entry_fill(
            side=self.pos.side, symbol=self.pos.symbol,
            entry_px=self.pos.entry_px,
            entry_spot=0.0, entry_ts=self.pos.entry_ts)
        # the row's persisted levels are the truth (entry_spot lost) —
        cp.sl_spot = self.pos.sl_spot
        cp.tp_prem = self.pos.tp_prem

    def eod_squareoff(self, ltp: Optional[float] = None) -> int:
        return 1 if self.close_trade(reason="EOD", ltp=ltp) else 0

    def kill_all(self) -> int:
        n = 1 if self.close_trade(reason="KILL", ltp=None) else 0
        write_audit_log(f"[ORB][KILL] flattened {n} position(s)")
        return n

    # ── panel surface ──
    def state(self) -> dict:
        return {
            "strategy": STRATEGY_ID, "mode": self.mode(),
            "position": None if self.pos is None else {
                "symbol": self.pos.symbol, "side": self.pos.side,
                "entry_price": self.pos.entry_px, "qty": self.pos.qty,
                "sl_spot": self.pos.sl_spot, "tp_prem": self.pos.tp_prem},
            "levels": (None if not self.day or self.day.orb_high is None
                       else {"high": self.day.orb_high,
                             "low": self.day.orb_low}),
            "day": dict(self.day_stats),
            "frozen": bool(self.day and self.day.guard.frozen),
        }
