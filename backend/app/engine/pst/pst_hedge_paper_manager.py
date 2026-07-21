# backend/app/engine/pst/pst_hedge_paper_manager.py
#
# ── PST_HEDGE PAPER MANAGER ── (Phase 1 — PAPER-HARDWIRED, D26)
#
# Event-driven mirror of pst_hedge_engine v2 (signal-tracked, D17-amended):
#   * dual-side SELECTION at ts−60 (select_strike on BOTH sides — the same
#     function/prices the backtest uses); either side missing → skip signal.
#   * SIGNAL contract: never traded; virtual entry = its close at ts;
#     SIG_TP level = sig_entry×(1−sl_pct/100), trigger on its 1m LOW.
#   * HELD contract (opposite side): BOUGHT at its close at ts; every exit
#     fills at the HELD close of the event minute (or last known held close
#     when the held contract printed no candle that minute — engine gap rule).
#   * SPOT_SL: spot ±spot_tg_points WITH the signal; same-minute collision →
#     SPOT_SL wins + ambiguous. EOD at last held close. Monitoring from
#     ts+60 over the UNION of held/sig candle minutes.
#   * side_mode filters the SIGNAL side (D21). One position at a time; busy
#     until last_exit+60. Risk entry-gate only (Phase 1, D28).
#
# Equivalence to simulate_position_hedge is PROVEN by
# tests/validate_pst_paper_parity.py, not assumed.

from __future__ import annotations

from typing import List, Optional

try:
    from app.engine.pst.pst_common import (LOT_SIZE, PSTRepo, RiskGate,
                                           hm_to_min, ist_day_start, leg_net)
except ImportError:  # standalone tests
    from pst_common import (LOT_SIZE, PSTRepo, RiskGate,
                                       hm_to_min, ist_day_start, leg_net)

try:
    from app.backtest.ic.ic_v1_engine import select_strike
except ImportError:
    from ic_v1_engine import select_strike  # type: ignore

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)


# ── PST_TG_NOTIFY BEGIN ── best-effort trade notifications (never break trading)
try:
    from app.api.telegram_api import (notify_trade_entry, notify_tp_exit,
                                      notify_sl_exit, notify_manual_exit)
except ImportError:  # standalone tests
    def notify_trade_entry(d): pass
    def notify_tp_exit(d): pass
    def notify_sl_exit(d): pass
    def notify_manual_exit(d): pass
# ── PST_TG_NOTIFY END ──

TABLE = "pst_hedge_trades"


def _other(side: str) -> str:
    return "PE" if side == "CE" else "CE"


class PSTHedgePaperManager:
    def __init__(self, cfg: dict, repo: PSTRepo, executor=None, live_executor=None):
        # ── DYNAMIC MODE (house pattern, V3 _cfg() parity) ── the mode is no
        # longer frozen at loop start. It is read FRESH at each ENTRY decision
        # (_entry_mode) and STAMPED on the position; every exit routes through
        # the executor of the STAMPED mode, so flipping Settings mid-position
        # can never paper-close a live broker position or fire real orders
        # for a paper one. No app restart needed after a mode change.
        self.disabled = False
        try:
            from app.engine.pst.pst_order_executor import PaperExecutor
        except ImportError:  # standalone tests
            from pst_order_executor import PaperExecutor
        self.paper_exec = executor if (executor is not None and
                                       getattr(executor, "is_paper", False)) \
            else PaperExecutor()
        self.live_exec = live_executor
        self.pos_mode = "PAPER"          # mode of the CURRENT position (stamp)
        self.repo = repo
        self._sid = "PST_HEDGE"
        self.prem_max = float(cfg.get("premium_max", 150) or 150)
        self.legs_cfg = [l for l in (cfg.get("legs") or []) if int(l.get("lots") or 0) > 0]
        self.side_mode = str(cfg.get("side_mode", "BOTH") or "BOTH")
        self.max_tpd = int(cfg.get("max_trades_per_day", 0) or 0)
        self.exit_min = hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
        self.risk = RiskGate(dml=cfg.get("daily_max_loss"), dmp=cfg.get("daily_max_profit"),
                             mml=cfg.get("monthly_max_loss"), mmp=cfg.get("monthly_max_profit"))
        # position state (mirrors simulate_position_hedge's `state`)
        self.open_legs: List[dict] = []
        self.sig_side: Optional[str] = None
        self.held_symbol: Optional[str] = None
        self.sig_symbol: Optional[str] = None
        self.held_entry: Optional[float] = None
        self.last_close: Optional[float] = None    # last known HELD close
        self.last_ts: Optional[int] = None
        self.monitor_from: Optional[int] = None
        self.pending: Optional[dict] = None      # staged entry awaiting fill candles
        # ── PST_EARLY_EXIT BEGIN ── the pre-boundary thread and the minute
        # boundary thread both mutate open_legs and both can place broker
        # orders. LiveExecutor.market() blocks up to ~8s confirming a fill,
        # so without this lock on_minute could re-enter the SAME leg while
        # the early path is still inside market() -> two SELLs, one
        # position. Non-reentrant: no path below takes it twice.
        import threading as _threading
        self._lock = _threading.RLock()
        self._early_closed_ts: dict = {}     # db_id -> bar_ts closed early
        # ── PST_EARLY_EXIT END ──
        self.busy_until: int = -1
        self.taken_today: int = 0
        self._day_key: Optional[int] = None
        self.diag = {"signals_taken": 0, "signals_skipped_busy": 0,
                     "signals_skipped_side": 0, "signals_skipped_select": 0,
                     "signals_skipped_cap": 0, "signals_skipped_risk": 0,
                     "signals_skipped_stale": 0, "ambiguous": 0}

    def _roll_day(self, ts: int) -> None:
        dk = ist_day_start(ts)
        if dk != self._day_key:
            self._day_key = dk
            self.taken_today = 0

    def _eod_ts(self, ts: int) -> int:
        return ist_day_start(ts) + self.exit_min * 60


    # ── dynamic mode plumbing ────────────────────────────────────────

    def _cfg_snapshot(self):
        """ONE fresh config read per SIGNAL (V3's per-iteration reload,
        atomically): mode + every entry-shaping parameter travel together
        into the pending entry, so a Settings save between signal and fill
        can't mix vintages. Degraded read → None (entry skipped, fail
        closed). Loader absent (parity harness) → boot values."""
        try:
            from app.config.strategy_loader import load_strategy_config_ex
            cfg, degraded = load_strategy_config_ex(self._sid)
            if degraded:
                write_audit_log(f"[{self._sid}] degraded config read — "
                                f"entry skipped (fail closed)")
                return None
        except ImportError:
            cfg = None
        except Exception:
            return None
        if cfg is None:                       # harness / boot fallback
            return {"mode": "PAPER", "legs": self.legs_cfg,
                    "prem_max": self.prem_max, "side_mode": self.side_mode,
                    "max_tpd": self.max_tpd}
        m = str(cfg.get("trade_execution_mode", "PAPER")).upper()
        legs = [l for l in (cfg.get("legs") or []) if int(l.get("lots") or 0) > 0]
        # risk thresholds refresh live against the running accumulators
        for k, a in (("daily_max_loss", "dml"), ("daily_max_profit", "dmp"),
                     ("monthly_max_loss", "mml"), ("monthly_max_profit", "mmp")):
            setattr(self.risk, a, max(0.0, float(cfg.get(k) or 0)))
        self.risk.enabled = any(v > 0 for v in
                                (self.risk.dml, self.risk.dmp,
                                 self.risk.mml, self.risk.mmp))
        return {"mode": m if m in ("PAPER", "LIVE") else "PAPER",
                "legs": legs or self.legs_cfg,
                "prem_max": float(cfg.get("premium_max", self.prem_max) or self.prem_max),
                "side_mode": str(cfg.get("side_mode", self.side_mode) or self.side_mode),
                "max_tpd": int(cfg.get("max_trades_per_day", self.max_tpd) or 0)}

    def _sig_log(self, ts, side, outcome):
        write_audit_log(f"[{self._sid}][SIG] ts={ts} side={side} → {outcome}")

    def _entry_mode(self) -> str:
        """Fresh config read at entry time (V3's _cfg() pattern). Degraded
        read → PAPER (house fail-closed rule). Unknown value → PAPER."""
        try:
            from app.config.strategy_loader import load_strategy_config_ex
            cfg, degraded = load_strategy_config_ex(self._sid)
            if degraded:
                write_audit_log(f"[{self._sid}] degraded config read — "
                                f"entry mode forced PAPER (fail closed)")
                return "PAPER"
            m = str(cfg.get("trade_execution_mode", "PAPER")).upper()
            return m if m in ("PAPER", "LIVE") else "PAPER"
        except Exception:
            return "PAPER"

    def _exec(self):
        """Executor for the CURRENT position, by its stamped mode."""
        return self.live_exec if (self.pos_mode == "LIVE"
                                  and self.live_exec is not None) \
            else self.paper_exec

    # ── entries ──────────────────────────────────────────────────────
    def on_signal(self, sig: dict, chain) -> None:
        if self.disabled or not self.legs_cfg:
            return
        ts = int(sig["ts"])
        self._roll_day(ts)
        snap = self._cfg_snapshot()
        if snap is None:
            self._sig_log(ts, sig["side"], "skipped_config_degraded")
            self.diag["signals_skipped_risk"] += 1
            return
        if sig.get("stale"):
            self._sig_log(ts, sig["side"], "skipped_stale")
            self.diag["signals_skipped_stale"] += 1
            return
        if self.risk.blocked(ts):
            self._sig_log(ts, sig["side"], "skipped_risk_limit")
            self.diag["signals_skipped_risk"] += 1
            return
        if snap["side_mode"] != "BOTH" and sig["side"] != snap["side_mode"]:   # D21: SIGNAL side
            self._sig_log(ts, sig["side"], "skipped_side_filter")
            self.diag["signals_skipped_side"] += 1
            return
        if ts < self.busy_until or self.open_legs or self.pending:
            self._sig_log(ts, sig["side"], "skipped_busy (position open or pending)")
            self.diag["signals_skipped_busy"] += 1
            return
        if snap["max_tpd"] and self.taken_today >= snap["max_tpd"]:
            self._sig_log(ts, sig["side"], f"skipped_daily_cap ({self.taken_today})")
            self.diag["signals_skipped_cap"] += 1
            return
        if ts >= self._eod_ts(ts):
            return

        def pick(side: str):
            cands = []
            for sym in chain.symbols(side):
                c = chain.candle(sym, ts - 60)
                if c and float(c["close"]) > 0:
                    cands.append((sym, float(c["close"])))
            p = select_strike(cands, snap["prem_max"])
            return p[0] if p is not None else None

        sig_sym = pick(sig["side"])
        held_sym = pick(_other(sig["side"])) if sig_sym else None
        if sig_sym is None or held_sym is None:         # fail closed per signal
            self._sig_log(ts, sig["side"], "skipped_selection (no eligible contract)")
            self.diag["signals_skipped_select"] += 1
            return
        # ── TWO-PHASE ENTRY (backtest timeline) ── fill candles for minute
        # ts do not exist yet; stage now, fill when minute ts completes.
        self._sig_log(ts, sig["side"], f"taken → pending fill {held_sym}")
        self.pending = {"sig": dict(sig), "sig_symbol": sig_sym,
                        "held_symbol": held_sym, "fill_ts": ts, "snap": snap}
        return

    def _complete_pending(self, chain) -> None:
        pend = self.pending
        self.pending = None
        sig = pend["sig"]
        ts = int(sig["ts"])
        sig_sym, held_sym = pend["sig_symbol"], pend["held_symbol"]
        sfc = chain.candle(sig_sym, ts)
        hfc = chain.candle(held_sym, ts)
        if sfc is None or hfc is None:         # backtest: fill None → skip
            self.diag["signals_skipped_select"] += 1
            return
        sig_entry = float(sfc["close"])
        snap = pend.get("snap") or self._cfg_snapshot() or {"mode": "PAPER", "legs": self.legs_cfg}
        mode = snap["mode"]                    # from the signal-time snapshot
        if mode == "LIVE" and self.live_exec is None:
            write_audit_log(f"[{self._sid}] LIVE mode but no live executor — "
                            f"entry skipped (fail closed)")
            self.diag["signals_skipped_select"] += 1
            return
        self.pos_mode = mode
        if self._exec().is_paper:
            held_entry = float(hfc["close"])   # model fill — backtest parity
        else:
            total_qty = sum(int(l["lots"]) for l in snap["legs"]) * LOT_SIZE
            held_entry, _oid = self._exec().market(held_sym, "BUY", total_qty,
                                                   model_price=float(hfc["close"]))
            if held_entry is None:
                self.diag["signals_skipped_select"] += 1
                return
        spot_entry = float(sig["spot"])
        is_ce_sig = sig["side"] == "CE"
        meta = chain.meta(held_sym) or {}
        self.sig_side, self.sig_symbol = sig["side"], sig_sym
        self.held_symbol, self.held_entry = held_sym, held_entry
        self.last_close, self.last_ts = held_entry, ts
        self.monitor_from = ts + 60
        self.open_legs = []
        for leg in snap["legs"]:
            tp_level = max(0.05, sig_entry * (1 - float(leg["sl_pct"]) / 100.0)) \
                if float(leg.get("sl_pct") or 0) > 0 else None
            pts = float(leg.get("spot_tg_points") or 0)
            spot_sl = (spot_entry + pts if is_ce_sig else spot_entry - pts) if pts > 0 else None
            db_id = self.repo.insert_leg(TABLE, {
                "mode": self.pos_mode, "leg_id": leg["id"], "tradingsymbol": held_sym,
                "instrument_type": _other(sig["side"]), "strike": meta.get("strike"),
                "expiry": meta.get("expiry"), "direction": "BUY",
                "qty": int(leg["lots"]) * LOT_SIZE,
                "entry_ts": ts + 60,
                "entry_price": round(held_entry, 2),
                "sl": None,
                "tp": (round(tp_level, 2) if tp_level is not None else None),
                "sig_symbol": sig_sym, "sig_entry": round(sig_entry, 2),
                "spot_entry": spot_entry, "spot_sl": spot_sl,
                "condition": f"{leg['id']}\u00b7{sig['side']}\u00b7{','.join(sig.get('levels_crossed') or [])}",
            })
            self.open_legs.append({"db_id": db_id, "leg_id": leg["id"],
                                   "lots": int(leg["lots"]), "sig_tp": tp_level,
                                   "spot_sl": spot_sl})
        self.taken_today += 1
        self.diag["signals_taken"] += 1
        write_audit_log(f"[PST_HEDGE][PAPER] ENTER LONG {held_sym} @{held_entry:.2f} "
                        f"tracking {sig_sym} (sig_entry {sig_entry:.2f}) sig_ts={ts}")
        try:   # ── PST_TG_NOTIFY ──
            notify_trade_entry({
                "strategy_id": "PST_HEDGE", "mode": self.pos_mode.lower(),
                "symbol": held_sym, "side": _other(sig["side"]),
                "entry_price": round(float(held_entry), 2),
                "quantity": sum(int(l["lots"]) for l in snap["legs"]) * LOT_SIZE,
                "sl": None, "tp": None, "trade_direction": "LONG",
                "note": "SL is on SPOT; TP tracked on the SIGNAL contract",
            })
        except Exception:
            pass

    # ── PST_EARLY_EXIT BEGIN ──
    def on_pre_boundary(self, bar_ts: int, spot_peek: Optional[dict],
                        chain) -> None:
        """T-1s early exit — LIVE POSITIONS ONLY.

        Backtest/paper convention: trigger on the bar's intrabar extreme,
        fill at that bar's CLOSE. In live, on_minute runs at bar_end+1.5s,
        so the market order lands ~62s after the close it is modelled on —
        on a fast minute that is the whole divergence (2026-07-20: modelled
        95.60, filled 93.30 after a 20-point collapse).

        This path evaluates the SAME trigger tests against the in-progress
        bar and fires ~1s BEFORE the close, so the real fill lands near the
        price the backtest assumes.

        SAFETY:
          * PAPER returns immediately -> paper<->backtest parity untouched.
          * The partial bar's high/low is a SUBSET of the final bar's, so
            anything firing here would also have fired in on_minute. Never
            spurious, only earlier.
          * Legs closed here are recorded in _early_closed_ts and skipped
            by on_minute for the same bar (no double close).
          * Fill price: REST kite.ltp() primary (house doctrine — LTPStore
            can be stale), peeked running close as fallback.
        """
        if self.disabled or self.pos_mode != "LIVE" or not self.open_legs:
            return
        if self.monitor_from is None or bar_ts < self.monitor_from:
            return
        if bar_ts >= self._eod_ts(bar_ts):
            return
        if not self._lock.acquire(blocking=False):
            return                      # on_minute is mid-flight; it will handle it
        try:
            sg_pk = None
            try:
                sg_pk = chain.peek(self.sig_symbol, bar_ts)
            except Exception:
                sg_pk = None
            is_ce_sig = self.sig_side == "CE"
            armed = []
            for st in self.open_legs:
                if self._early_closed_ts.get(st["db_id"]) == bar_ts:
                    continue
                hit_tp = (st["sig_tp"] is not None and sg_pk is not None
                          and float(sg_pk["low"]) <= st["sig_tp"])
                hit_sl = False
                if st["spot_sl"] is not None and spot_peek is not None:
                    hit_sl = (float(spot_peek["high"]) >= st["spot_sl"]) if is_ce_sig \
                        else (float(spot_peek["low"]) <= st["spot_sl"])
                if hit_sl or hit_tp:
                    armed.append((st, "SPOT_SL" if hit_sl else "SIG_TP",
                                  bool(hit_sl and hit_tp)))
            if not armed:
                return
            px = self._live_ltp(self.held_symbol)
            if px is None:
                hp = None
                try:
                    hp = chain.peek(self.held_symbol, bar_ts)
                except Exception:
                    hp = None
                px = float(hp["close"]) if hp is not None else self.last_close
            if px is None:
                write_audit_log(f"[PST_HEDGE][EARLY] no price for "
                                f"{self.held_symbol} — deferring to on_minute")
                return
            still = list(self.open_legs)
            for st, reason, amb in armed:
                write_audit_log(f"[PST_HEDGE][EARLY] {reason} armed on partial "
                                f"bar {bar_ts} — exiting at T-1s @{px:.2f}")
                self._close_leg(st, bar_ts, px, reason, amb)
                if amb:
                    self.diag["ambiguous"] += 1
                if not st.get("_still_open"):
                    self._early_closed_ts[st["db_id"]] = bar_ts
                    if st in still:
                        still.remove(st)
            self.open_legs = still
            if not self.open_legs:
                self._flat(bar_ts)
        finally:
            self._lock.release()

    def _live_ltp(self, symbol: str) -> Optional[float]:
        """REST LTP for the held contract — authoritative at exit time
        (house rule: LTPStore can be stale). None on any failure."""
        try:
            ex = self._exec()
            bm = getattr(ex, "bm", None)
            kite = bm.get_trade_kite() if bm is not None else None
            if kite is None:
                return None
            key = f"NFO:{symbol}"
            q = kite.ltp([key]) or {}
            px = float((q.get(key) or {}).get("last_price") or 0)
            return px if px > 0 else None
        except Exception:
            return None
    # ── PST_EARLY_EXIT END ──

    # ── per-minute monitoring (mirrors simulate_position_hedge loop) ──
    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        if self.disabled:
            return
        self._roll_day(ts)
        eod = self._eod_ts(ts)
        if self.pending is not None:
            if ts >= eod:
                self.pending = None            # never fill at/after EOD
            elif ts >= self.pending["fill_ts"]:
                self._complete_pending(chain)
        if not self.open_legs:
            return
        if ts >= eod:
            self._close_all(self.last_ts, self.last_close, "EOD", False)
            return
        if self.monitor_from is None or ts < self.monitor_from:
            return
        hc = chain.candle(self.held_symbol, ts)
        sg = chain.candle(self.sig_symbol, ts)
        if hc is None and sg is None:
            return                       # engine iterates the held∪sig union
        if hc is not None:
            self.last_close, self.last_ts = float(hc["close"]), ts
        fill = float(hc["close"]) if hc is not None else self.last_close
        is_ce_sig = self.sig_side == "CE"
        # ── PST_EARLY_EXIT ── serialize against the pre-boundary thread.
        # Blocking acquire: this path must not be skipped.
        with self._lock:
            still = []
            for st in self.open_legs:
                # already closed by the T-1s path for THIS bar — skip
                if self._early_closed_ts.get(st["db_id"]) == ts:
                    continue
                hit_tp = (st["sig_tp"] is not None and sg is not None
                          and float(sg["low"]) <= st["sig_tp"])
                hit_sl = False
                if st["spot_sl"] is not None and spot_candle is not None:
                    hit_sl = (float(spot_candle["high"]) >= st["spot_sl"]) if is_ce_sig \
                        else (float(spot_candle["low"]) <= st["spot_sl"])
                if hit_sl:
                    self._close_leg(st, ts, fill, "SPOT_SL", hit_tp)
                    if hit_tp:
                        self.diag["ambiguous"] += 1
                    # POSITION-LEAK FIX: a LIVE SELL that fails sets
                    # _still_open; without this the leg fell out of
                    # open_legs entirely and the broker position was left
                    # untracked. _close_all already did this; on_minute
                    # did not.
                    if st.get("_still_open"):
                        still.append(st)
                elif hit_tp:
                    self._close_leg(st, ts, fill, "SIG_TP", False)
                    if st.get("_still_open"):
                        still.append(st)
                else:
                    still.append(st)
            self.open_legs = still
            if not self.open_legs:
                self._flat(ts)


    # ── restart adoption (same-day OPEN rows) ─────────────────────────
    def adopt_rows(self, rows) -> None:
        if not rows:
            return
        r0 = rows[0]
        self.pos_mode = str(r0.get("mode", "PAPER")).upper()   # exits follow the row's mode
        self.held_symbol = r0["tradingsymbol"]
        self.sig_symbol = r0["sig_symbol"]
        self.sig_side = "PE" if r0["instrument_type"] == "CE" else "CE"
        self.held_entry = float(r0["entry_price"])
        self.last_close = float(r0["entry_price"])
        self.last_ts = int(r0["entry_ts"]) - 60
        self.monitor_from = int(r0["entry_ts"])
        self.open_legs = [{"db_id": r["id"], "leg_id": r["leg_id"],
                           "lots": int(r["qty"]) // LOT_SIZE,
                           "sig_tp": r["tp"], "spot_sl": r["spot_sl"]}
                          for r in rows]
        write_audit_log(f"[PST_HEDGE] adopted {len(rows)} OPEN leg(s) on "
                        f"{self.held_symbol} (tracking {self.sig_symbol}) after restart")
    def force_eod(self, ts: int) -> None:
        if not self.disabled and self.open_legs:
            self._close_all(self.last_ts, self.last_close, "EOD", False)

    # ── close paths ──────────────────────────────────────────────────
    def _close_leg(self, st: dict, ts: int, px: float, reason: str, amb: bool) -> None:
        if not self._exec().is_paper:
            fill, _oid = self._exec().market(self.held_symbol, "SELL",
                                          int(st["lots"]) * LOT_SIZE,
                                          model_price=px)
            if fill is None:
                st["_still_open"] = True
                return                         # alerted; retry next minute
            px = fill
        st["_still_open"] = False
        gross, charges, net = leg_net("BUY", self.held_entry, px, st["lots"])
        if st["db_id"] is not None:
            self.repo.close_leg(TABLE, st["db_id"], exit_ts=ts, exit_price=px,
                                exit_reason=reason, ambiguous=amb,
                                pnl=gross, charges=charges, net_pnl=net)
        self.risk.on_close(net, ts)
        write_audit_log(f"[PST_HEDGE][PAPER] EXIT {st['leg_id']} {self.held_symbol} "
                        f"@{px:.2f} {reason}{' AMB' if amb else ''} net={net:.0f}")
        try:   # ── PST_TG_NOTIFY ── TP→tp, SPOT_SL→sl, EOD/other→manual
            _d = {"strategy_id": "PST_HEDGE", "mode": self.pos_mode.lower(),
                  "symbol": self.held_symbol, "side": None,
                  "entry_price": round(float(self.held_entry), 2),
                  "exit_price": round(float(px), 2), "pnl": round(net, 2),
                  "note": ("AMBIGUOUS fill minute" if amb else "")}
            if reason == "SIG_TP":
                notify_tp_exit(_d)
            elif reason == "SPOT_SL":
                notify_sl_exit(_d)
            else:
                _d["note"] = (reason + (" · " + _d["note"] if _d["note"] else ""))
                notify_manual_exit(_d)
        except Exception:
            pass

    def _close_all(self, ts: int, px: float, reason: str, amb: bool) -> None:
        before = list(self.open_legs)
        self.open_legs = []
        for st in before:
            self._close_leg(st, ts, px, reason, amb)
            if st.get("_still_open"):
                self.open_legs.append(st)
        if not self.open_legs:
            self._flat(ts)

    def _flat(self, last_exit_ts: int) -> None:
        self.busy_until = int(last_exit_ts) + 60
        self.sig_side = self.held_symbol = self.sig_symbol = None
        self.held_entry = self.monitor_from = None
        self._early_closed_ts = {}       # ── PST_EARLY_EXIT ── fresh per position