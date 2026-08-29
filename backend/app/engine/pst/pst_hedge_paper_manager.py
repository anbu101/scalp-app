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



# ── PST_LIVE_FILTERS_20260828 BEGIN ── sealed entry filters, ported from the
# backtest. Helpers are IMPORTED from the backtest engines (never
# reimplemented) so live and backtest can never drift on what
# "nearest crossed level" or "expiry day" means.
try:
    from app.backtest.pst.pst_sell_engine import nearest_crossed_level
except ImportError:  # standalone tests
    from pst_sell_engine import nearest_crossed_level  # type: ignore

# The expiry calendar is a BLOCKING filter, so its absence must FAIL CLOSED.
# An ImportError fallback returning False would silently disable the skip and
# trade expiry days unnoticed — the exact opposite of what the filter is for.
try:
    from app.backtest.engine.expiry_calendar import is_expiry_day as _is_expiry_day
    _EXPIRY_CAL_OK = True
except ImportError:  # pragma: no cover - calendar is core; absence is fatal
    _EXPIRY_CAL_OK = False

    def _is_expiry_day(_d):
        raise RuntimeError("expiry_calendar unavailable")


def _pst_filter_snap(cfg, defaults):
    """Parse the three filter keys out of a fresh config read. Unknown level
    names are DROPPED, not ignored: an allowlist that silently keeps a typo
    would widen the filter, so the surviving set is what actually gates."""
    if cfg is None:
        return defaults
    raw = [str(x).strip().upper() for x in (cfg.get("allowed_levels") or [])
           if str(x).strip()]
    valid = {"S3", "S2", "S1", "PP", "R1", "R2", "R3"}
    lv = frozenset(x for x in raw if x in valid) or None
    return {"allowed_levels": lv,
            "skip_expiry_day": bool(cfg.get("skip_expiry_day")),
            "confirm_minutes": min(30, max(0, int(cfg.get("confirm_minutes") or 0)))}


def _pst_ist_date(epoch_day_start):
    """IST calendar date of a day-start epoch. IST is imported here rather
    than assumed on the module: the managers import only ist_day_start."""
    import datetime as _dt
    try:
        from app.engine.pst.pst_common import IST as _I
    except ImportError:  # standalone tests
        from pst_common import IST as _I  # type: ignore
    return _dt.datetime.utcfromtimestamp(int(epoch_day_start) + _I).date()


def _pst_confirm_sl(sig, legs):
    """The would-be SPOT_SL level of the TIGHTEST leg (it dies first; the
    entry is atomic). None when no leg carries a spot target."""
    tgs = [float(l.get("spot_tg_points") or 0) for l in (legs or [])
           if float(l.get("spot_tg_points") or 0) > 0]
    if not tgs:
        return None
    tg = min(tgs)
    spot = float(sig["spot"])
    return (spot + tg) if sig["side"] == "CE" else (spot - tg)
# ── PST_LIVE_FILTERS_20260828 END ──

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
        # ── PST_LIVE_FILTERS_20260828 ── boot values; refreshed per signal in _cfg_snapshot
        _fb = _pst_filter_snap(cfg, {"allowed_levels": None,
                                     "skip_expiry_day": False,
                                     "confirm_minutes": 0})
        self.allowed_levels = _fb["allowed_levels"]
        self.skip_expiry_day = _fb["skip_expiry_day"]
        self.confirm_minutes = _fb["confirm_minutes"]
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
        # orders. LiveExecutor.market() blocks while confirming a fill, so
        # without this lock on_minute could re-enter the SAME leg while the
        # early path is still inside market() -> two SELLs, one position.
        # Non-reentrant: no path below takes it twice.
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
                     "signals_skipped_stale": 0,
                     "signals_skipped_level": 0,    # ── PST_LIVE_FILTERS_20260828 ──
                     "signals_skipped_expiry": 0,   # ── PST_LIVE_FILTERS_20260828 ──
                     "signals_skipped_confirm": 0,  # ── PST_LIVE_FILTERS_20260828 ──
                     "ambiguous": 0}

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
                    "max_tpd": self.max_tpd,
                    # ── PST_LIVE_FILTERS_20260828 ── boot values travel with the snapshot
                    "allowed_levels": self.allowed_levels,
                    "skip_expiry_day": self.skip_expiry_day,
                    "confirm_minutes": self.confirm_minutes}
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
                "max_tpd": int(cfg.get("max_trades_per_day", self.max_tpd) or 0),
                # ── PST_LIVE_FILTERS_20260828 ── read FRESH with everything else, so a
                # Settings save between signal and fill cannot mix vintages
                **_pst_filter_snap(cfg, {"allowed_levels": self.allowed_levels,
                                         "skip_expiry_day": self.skip_expiry_day,
                                         "confirm_minutes": self.confirm_minutes})}

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
        # ── PST_LIVE_FILTERS_20260828 ── level allowlist (None/empty = OFF)
        if snap.get("allowed_levels"):
            _lvl = nearest_crossed_level(sig["side"], sig.get("levels_crossed"))
            if _lvl is None or _lvl not in snap["allowed_levels"]:
                self._sig_log(ts, sig["side"], f"skipped_level ({_lvl})")
                self.diag["signals_skipped_level"] += 1
                return
        # ── PST_LIVE_FILTERS_20260828 ── weekly-expiry-day skip
        if snap.get("skip_expiry_day"):
            if not _EXPIRY_CAL_OK:
                # fail closed: cannot prove today is not expiry -> no entry
                self._sig_log(ts, sig["side"],
                              "skipped_expiry_day (calendar unavailable - fail closed)")
                self.diag["signals_skipped_expiry"] += 1
                return
            if _is_expiry_day(_pst_ist_date(ist_day_start(ts))):
                self._sig_log(ts, sig["side"], "skipped_expiry_day")
                self.diag["signals_skipped_expiry"] += 1
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
        # ── PST_LIVE_FILTERS_20260828 ── confirm wait: the backtest selects at
        # (ts + N*60) and fills there, so SELECTION IS DEFERRED — selecting
        # now off stale prices would be a different strategy.
        _cfm = int(snap.get("confirm_minutes") or 0)
        if _cfm > 0:
            _fill_ts = ts + _cfm * 60
            if _fill_ts >= self._eod_ts(ts):
                self._sig_log(ts, sig["side"], "skipped_confirm (wait crosses EOD)")
                self.diag["signals_skipped_confirm"] += 1
                return
            self._sig_log(ts, sig["side"], f"taken → confirm wait {_cfm}m "
                                           f"(fill {_fill_ts})")
            self.pending = {"sig": dict(sig), "sig_symbol": None, "held_symbol": None,
                            "fill_ts": _fill_ts, "select_ts": _fill_ts - 60,
                            "snap": snap,
                            "confirm_sl": _pst_confirm_sl(sig, snap["legs"]),
                            "confirm_seen": 0, "confirm_need": _cfm}
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
        # ── PST_LIVE_FILTERS_20260828 ── fill/monitor/stamp times follow the
        # (possibly delayed) FILL minute; spot_entry below stays sig["spot"]
        # because the SPOT_SL is signal-anchored in the backtest and must
        # stay so here.
        ts = int(pend.get("fill_ts") or sig["ts"])
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
            _ex = self._exec()
            held_entry, _oid = _ex.market(held_sym, "BUY", total_qty,
                                          model_price=float(hfc["close"]))
            if held_entry is None:
                # ── PST_FILL_TIMEOUT ── an UNCONFIRMED entry is the dangerous
                # mirror of the exit case: the BUY may have filled, so the
                # app must not silently walk away believing it is flat.
                # We still skip the entry (no price to book against), but we
                # alert loudly so the position can be reconciled manually.
                if getattr(_ex, "last_state", "FAILED") == "UNKNOWN" and _oid:
                    write_audit_log(f"[{self._sid}][LIVE][CRITICAL] entry BUY "
                                    f"{held_sym} order {_oid} UNCONFIRMED — the "
                                    f"app is NOT tracking it. CHECK THE BROKER.")
                    try:
                        from app.api.telegram_api import notify_system_alert
                        notify_system_alert({
                            "message": f"🚨 PST_HEDGE entry BUY {held_sym} order "
                                       f"{_oid} unconfirmed — app is not tracking "
                                       f"this position. Check the broker NOW.",
                            "severity": "error"})
                    except Exception:
                        pass
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
          * A leg with an UNRESOLVED exit order id is skipped entirely —
            _close_leg owns that resolution (PST_FILL_TIMEOUT).
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
                # ── PST_FILL_TIMEOUT ── an unresolved exit order is owned by
                # on_minute/_close_leg; the early path must not touch it.
                if st.get("_pending_exit_oid"):
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
    # ── PST_LIVE_FILTERS_20260828 BEGIN ──
    def _pst_confirm_step(self, ts: int, spot_candle, chain) -> None:
        """Drive a WAITING pending: abort on SPOT_SL touch, then perform the
        deferred selection at (fill_ts − 60). No-op for N=0 pendings."""
        p = self.pending
        if p is None or not p.get("confirm_need"):
            return
        sig_ts = int(p["sig"]["ts"])
        fill_ts = int(p["fill_ts"])
        # ── abort scan: spot candles sig_ts+60 … fill_ts inclusive, matching
        # the backtest's range(1, cfm+1). The last scanned candle IS the fill
        # candle, so a touch there aborts BEFORE the fill.
        if sig_ts < ts <= fill_ts:
            if spot_candle is None:
                # FAIL CLOSED (stricter than backtest, deliberately): a feed
                # gap means we cannot verify the wait, and entering blind on a
                # gap is the exact failure this filter exists to prevent.
                self.pending = None
                self.diag["signals_skipped_confirm"] += 1
                self._sig_log(sig_ts, p["sig"]["side"],
                              f"abandoned_confirm (no spot candle at {ts})")
                return
            p["confirm_seen"] += 1
            lvl = p.get("confirm_sl")
            if lvl is not None:
                try:
                    hi = float(spot_candle["high"])
                    lo = float(spot_candle["low"])
                except Exception:
                    self.pending = None
                    self.diag["signals_skipped_confirm"] += 1
                    self._sig_log(sig_ts, p["sig"]["side"],
                                  "abandoned_confirm (malformed spot candle)")
                    return
                touched = (hi >= lvl) if p["sig"]["side"] == "CE" else (lo <= lvl)
                if touched:
                    self.pending = None
                    self.busy_until = ts + 60   # committed until it died
                    self.diag["signals_skipped_confirm"] += 1
                    self._sig_log(sig_ts, p["sig"]["side"],
                                  f"aborted_confirm (spot touched {lvl:.2f} at {ts})")
                    return
        # ── deferred SELECTION at (fill_ts − 60), priced off THIS candle ──
        if ts >= int(p["select_ts"]) and p.get("sig_symbol") is None:
            snap = p["snap"]

            def _pick_side(side):
                cands = []
                for sym in chain.symbols(side):
                    c = chain.candle(sym, ts)
                    if c and float(c["close"]) > 0:
                        cands.append((sym, float(c["close"])))
                q = select_strike(cands, snap["prem_max"])
                return q[0] if q is not None else None
            _ss = _pick_side(p["sig"]["side"])
            _hs = _pick_side(_other(p["sig"]["side"])) if _ss else None
            if _ss is None or _hs is None:      # fail closed per signal
                self.pending = None
                self.diag["signals_skipped_select"] += 1
                self._sig_log(sig_ts, p["sig"]["side"],
                              "skipped_selection after confirm (no eligible contract)")
                return
            p["sig_symbol"], p["held_symbol"] = _ss, _hs
            self._sig_log(sig_ts, p["sig"]["side"],
                          f"confirm passed → pending fill {_hs}")
    # ── PST_LIVE_FILTERS_20260828 END ──

    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:
        if self.disabled:
            return
        self._roll_day(ts)
        eod = self._eod_ts(ts)
        if self.pending is not None:
            if ts >= eod:
                self.pending = None            # never fill at/after EOD
            else:
                self._pst_confirm_step(ts, spot_candle, chain)   # ── PST_LIVE_FILTERS_20260828 ──
            if self.pending is not None and ts < eod \
                    and ts >= self.pending["fill_ts"]:
                self._complete_pending(chain)
        if not self.open_legs:
            return
        # ── PST_FILL_TIMEOUT ── legs carrying an unresolved exit order must
        # be driven every minute regardless of candle availability, or an
        # UNKNOWN order could sit unresolved until EOD.
        if self._resolve_pending_exits(ts):
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
                # unresolved exit order — _resolve_pending_exits owns it
                if st.get("_pending_exit_oid"):
                    still.append(st)
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

    # ── PST_FILL_TIMEOUT BEGIN ──
    def _resolve_pending_exits(self, ts: int) -> bool:
        """Poll every leg holding an UNCONFIRMED exit order id.

        2026-07-21 incident: the exit SELL filled but order_history had not
        propagated inside the confirm window, so the manager believed the
        order had failed and re-placed it every minute for the rest of the
        session — against an already-flat position. Only insufficient
        margin prevented an unintended naked short.

        An unconfirmed order is resolved here, never re-placed:
          COMPLETE  -> book the real fill and close the leg
          REJECTED/
          CANCELLED -> clear the marker; normal exit logic may re-order
          anything else -> still unresolved, leave it and poll again

        Returns True if any leg was touched."""
        if not self.open_legs:
            return False
        pend = [st for st in self.open_legs if st.get("_pending_exit_oid")]
        if not pend:
            return False
        ex = self._exec()
        touched = False
        with self._lock:
            still = list(self.open_legs)
            for st in pend:
                oid = st.get("_pending_exit_oid")
                try:
                    pstate, pavg = ex.status(oid)
                except Exception:
                    continue
                if pstate == "COMPLETE" and pavg:
                    write_audit_log(f"[PST_HEDGE][LIVE] pending exit order {oid} "
                                    f"resolved COMPLETE @{pavg:.2f} — booking")
                    st["_pending_exit_oid"] = None
                    st["_still_open"] = False
                    self._book_exit(st, ts, float(pavg),
                                    st.get("_pending_reason") or "SPOT_SL",
                                    bool(st.get("_pending_amb")))
                    if st in still:
                        still.remove(st)
                    touched = True
                elif pstate in ("REJECTED", "CANCELLED"):
                    write_audit_log(f"[PST_HEDGE][LIVE] pending exit order {oid} "
                                    f"{pstate} — clearing, exit may be re-placed")
                    st["_pending_exit_oid"] = None
                    st["_still_open"] = True
                    touched = True
                # else: still unresolved — poll again next minute
            self.open_legs = still
            if touched and not self.open_legs:
                self._flat(ts)
        return touched
    # ── PST_FILL_TIMEOUT END ──

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
    def _book_exit(self, st: dict, ts: int, px: float, reason: str,
                   amb: bool) -> None:
        """Persist + notify a CONFIRMED exit. Split out of _close_leg so the
        pending-order resolver books identically (PST_FILL_TIMEOUT)."""
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

    def _close_leg(self, st: dict, ts: int, px: float, reason: str, amb: bool) -> None:
        if not self._exec().is_paper:
            ex = self._exec()
            # ── PST_FILL_TIMEOUT BEGIN ── never place a second exit order
            # for a leg whose previous exit order is unresolved. The
            # resolver (_resolve_pending_exits) owns those; if one slips
            # through to here, defer rather than re-order.
            if st.get("_pending_exit_oid"):
                st["_still_open"] = True
                return
            fill, oid = ex.market(self.held_symbol, "SELL",
                                  int(st["lots"]) * LOT_SIZE,
                                  model_price=px)
            if fill is None:
                if getattr(ex, "last_state", "FAILED") == "UNKNOWN" and oid:
                    # The order may well have filled. Park it, remember what
                    # it was for, and poll — do NOT re-order.
                    st["_pending_exit_oid"] = oid
                    st["_pending_reason"] = reason
                    st["_pending_amb"] = bool(amb)
                    st["_still_open"] = True
                    write_audit_log(f"[PST_HEDGE][LIVE] exit order {oid} on "
                                    f"{self.held_symbol} UNCONFIRMED — NOT "
                                    f"re-ordering; polling this order id")
                    try:
                        from app.api.telegram_api import notify_system_alert
                        notify_system_alert({
                            "message": f"PST_HEDGE: exit order {oid} on "
                                       f"{self.held_symbol} unconfirmed. App "
                                       f"will poll it and will NOT place "
                                       f"another exit. Check the broker if "
                                       f"this persists.",
                            "severity": "warning"})
                    except Exception:
                        pass
                    return
                st["_still_open"] = True
                return                         # genuinely failed; retry next minute
            px = fill
        st["_still_open"] = False
        self._book_exit(st, ts, px, reason, amb)

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