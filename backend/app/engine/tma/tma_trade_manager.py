# backend/app/engine/tma/tma_trade_manager.py
#
# ── TMA_V1 TRADE MANAGER ── (paper parity + Session-C live hardening)
# ============================================================================
# One open GROUP at a time (backtest: one position per condition, C1 only):
# SELL leg (monitored — carries SL/TP, drives EVERY exit) + BUY hedge (same
# option type, deeper OTM; follows the SELL leg's exit minute). Rows in
# tma_trades linked by group_id, direction SELL|BUY, mode stamped per entry.
#
# TWO-PHASE ENTRY (PST doctrine — the paper↔backtest parity fix): the signal
# ts is the 5m-bar COMPLETION boundary; selection is priced on the candle
# stamped ts−60 (the last COMPLETED 1m option candle); the fill candle
# (stamped ts) has not completed yet — stage pending at signal time (gates +
# selection + a full config snapshot travel together), fill when minute ts
# completes. Monitoring starts at ts+60. PAPER fills at candle closes
# (parity-proven by test_tma_live_core); LIVE fills are real (divergence
# ledger: live fill ≈ candle close, documented + accepted, IC precedent).
#
# LIVE EXECUTION (Session-C hardening, per the frozen build spec):
#   * BUY hedge placed FIRST and its fill CONFIRMED before the SELL is
#     placed (margin sequencing). SELL dead/unfilled after the hedge filled
#     → IMMEDIATE hedge unwind (market sell) + loud Telegram critical.
#   * Short SL via GTT: single-trigger BUY at the SL level
#     (place_gtt_sl_only_short); gtt id persisted (sell_gtt_id — load-
#     bearing for restart adoption, V3 doctrine).
#   * TP + XOVER + MTM_CUT + EOD/expiry square-off are ENGINE-DRIVEN market
#     exits: cancel_gtt_verified FIRST, then exit. Non-forced (TP/XOVER):
#     cancel unverified → DO NOT exit (armed GTT + market buy = double-buy
#     risk, IC doctrine) — alert, retry next minute. Forced (EOD/MTM/
#     expiry): flatten even if the cancel is unverified (being short past
#     the bound is worse) + CRITICAL "delete GTT manually" alert.
#   * Tick/candle SL detection in LIVE defers to the GTT: try the verified
#     cancel once; disarmed → market-exit both legs (reason SL); still
#     armed → DEFER — the GTT + backstop monitor own the exit.
#   * Margin pre-check (spec): required from executor.get_basket_margin —
#     the SAME Zerodha basket-margin engine behind /api/backtest/
#     margin-estimate (GENERIC_LEGS), called in-process through the
#     executor instead of the HTTP route. Skip + alert if
#     available < required × 1.25. ADVISORY-FAIL-OPEN (IC D8): only a
#     CONFIRMED shortfall blocks.
#
# DYNAMIC MODE (PST/V3 doctrine): mode read fresh per SIGNAL via
# load_strategy_config_ex; degraded read → entry skipped (fail closed to
# PAPER-side inaction); the mode is STAMPED on the position and every exit
# routes by the STAMP — a Settings flip mid-position can never paper-close
# a live broker position.
#
# TRADE MODES: INTRADAY (default) hard-closes daily at exit_time.
# POSITIONAL carries overnight; hard close only on the CONTRACT's expiry day
# (era-aware — expiry stamped from the chain at entry); optional
# NEG_MTM_EOD_CUT (cut_neg_mtm_eod) re-arms each carried day.
#
# RESTART ADOPTION: OPEN tma_trades rows on boot — INTRADAY: prior-day rows
# → STALE (alerted); today's rows adopted. POSITIONAL: prior-day rows are a
# legitimate carry → adopted (watch_from=0, carried-day convention); LIVE
# adoption cross-checks broker net qty per symbol and DISABLES the manager
# on mismatch (fail closed, PST recon pattern). GTT reconciliation is LOUD:
# an adopted LIVE sell leg whose persisted GTT is missing at the broker
# raises a CRITICAL naked-short alert — never a silent no-op.
# ============================================================================

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

try:
    from app.engine.tma.tma_common import (STRATEGY_ID, TABLE, TMARepo,
                                           hm_to_min, ist_day_start, leg_net)
    from app.engine.tma.tma_live_core import (hard_close,
                                              mtm_cut_after_data_gap,
                                              new_position, sltp_levels,
                                              step_candle)
except ImportError:  # standalone tests
    from tma_common import (STRATEGY_ID, TABLE, TMARepo,      # type: ignore
                            hm_to_min, ist_day_start, leg_net)
    from tma_live_core import (hard_close, mtm_cut_after_data_gap,  # type: ignore
                               new_position, sltp_levels, step_candle)

try:
    from app.backtest.ic.ic_v1_engine import select_strike
except ImportError:
    from ic_v1_engine import select_strike  # type: ignore

try:
    from app.event_bus.audit_logger import write_audit_log
except ImportError:
    def write_audit_log(msg: str) -> None:
        print(msg)

# ── TMA_TG_NOTIFY ── best-effort trade notifications (never break trading)
try:
    from app.api.telegram_api import (notify_critical, notify_manual_exit,
                                      notify_sl_exit, notify_tp_exit,
                                      notify_trade_entry)
except ImportError:  # standalone tests
    def notify_trade_entry(d): pass
    def notify_tp_exit(d): pass
    def notify_sl_exit(d): pass
    def notify_manual_exit(d): pass
    def notify_critical(d): pass

try:
    from app.event_bus.inapp_events import record_alert
except ImportError:
    def record_alert(*a, **k): pass

# ── TMA_LTP_BACKSTOP ── the MAIN feed's LTP store (an INDEPENDENT socket
# from TMA's own KiteTicker) — used only when the sold-leg candle stream
# gaps while a position is open (2026-07-20: SL fired minutes late because
# TMA-socket candles went missing; the main feed kept pricing the contract
# on the dashboard the whole time).
try:
    from app.marketdata.ltp_store import LTPStore
except ImportError:  # standalone tests
    LTPStore = None

_ENTRY_FILL_CAP_S = 45          # IC's synchronous entry confirm cap
_ENTRY_FILL_POLL_S = 2
_DEAD_ORDER_STATUSES = {"REJECTED", "CANCELLED", "LAPSED"}
MARGIN_BUFFER = 1.25            # spec: skip if available < required × 1.25


class TMATradeManager:
    """API (called by the minute coordinator):
       on_minute(ts, spot_candle, chain)   — exits/fills FIRST
       on_signal(sig, chain)               — entries (same boundary, after)
       force_eod(ts)                       — scheduler safety net
       on_backstop_sell_exit(...)          — GTT monitor handoff
    Chain duck-type: candle(sym, ts), symbols(side), meta(sym),
    last_close_at_or_before(sym, ts)."""

    def __init__(self, cfg: dict, repo: TMARepo, executor=None, notify=None):
        self.disabled = False
        self.repo = repo
        self.executor = executor          # house ZerodhaOrderExecutor (may be None)
        self.notify = notify
        self._sid = STRATEGY_ID
        # boot snapshot (refreshed per signal via _cfg_snapshot)
        self._boot_cfg = dict(cfg or {})
        self.exit_min = hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
        # group state
        self.group: Optional[Dict] = None   # see _stage/_complete for shape
        self.pending: Optional[dict] = None
        self.busy_until: int = -1
        self.taken_today: int = 0
        self._day_key: Optional[int] = None
        self._mtm_armed_day: Optional[int] = None
        self._exiting = False               # single-close latch (IC doctrine)
        self._defer_to_gtt = False          # live SL deferred to GTT/backstop
        # ── TMA_LTP_BACKSTOP ── sold-leg candle-stream health
        self._sold_gap_streak = 0
        self._gap_alerted = False
        self.diag = {"signals_taken": 0, "skipped_stale": 0, "skipped_busy": 0,
                     "skipped_cap": 0, "skipped_select": 0, "skipped_hedge": 0,
                     "skipped_config": 0, "skipped_margin": 0,
                     "skipped_no_executor": 0, "ambiguous": 0,
                     "hedge_fallback": 0, "unwinds": 0,
                     "sold_candle_gaps": 0, "sold_gap_streak": 0,
                     "carry_first_candle_skipped": 0,
                     "last_sold_candle_ts": 0, "ltp_backstop_exits": 0}

    # ── config ───────────────────────────────────────────────────────
    def _cfg_snapshot(self) -> Optional[dict]:
        """ONE fresh read per SIGNAL: mode + every entry-shaping parameter
        travel together into the pending entry (no mixed vintages).
        Degraded read → None (entry skipped, fail closed)."""
        try:
            from app.config.strategy_loader import load_strategy_config_ex
            cfg, degraded = load_strategy_config_ex(self._sid)
            if degraded:
                write_audit_log(f"[{self._sid}] degraded config read — "
                                f"entry skipped (fail closed)")
                return None
        except ImportError:
            cfg = self._boot_cfg          # harness / standalone
        except Exception:
            return None
        cfg = cfg or self._boot_cfg
        c1 = cfg.get("c1") or {}
        sell = dict(c1.get("sell") or {})
        buy = dict(c1.get("buy") or {})
        m = str(cfg.get("trade_execution_mode", "PAPER")).upper()
        tm = str(cfg.get("trade_mode", "INTRADAY")).upper()
        wing = str(cfg.get("wing_mode", "real_fallback")).lower()
        if wing not in ("real_fallback", "skip"):
            wing = "real_fallback"        # NO synthetic in live (spec)
        snap = {
            "mode": m if m in ("PAPER", "LIVE") else "PAPER",
            "trade_mode": tm if tm in ("INTRADAY", "POSITIONAL") else "INTRADAY",
            "cut_neg_mtm_eod": bool(cfg.get("cut_neg_mtm_eod", False)),
            "wing_mode": wing,
            "lot_size": int((cfg.get("quantity") or {}).get("lot_size", 65)),
            "margin_guard": bool(cfg.get("margin_guard", True)),
            "sell_premium_max": float(sell.get("premium_max", 100) or 0),
            "sell_lots": int(sell.get("lots", 1) or 0),
            "sl_pct": float(sell.get("sl_pct", 0) or 0),
            "tp_pct": float(sell.get("tp_pct", 0) or 0),
            "sl_unit": str(sell.get("sl_unit") or sell.get("sl_tp_unit") or "PCT"),
            "tp_unit": str(sell.get("tp_unit") or sell.get("sl_tp_unit") or "PCT"),
            "buy_premium_max": float(buy.get("premium_max", 3) or 0),
            "buy_lots": int(buy.get("lots", 1) or 0),
            "max_tpd": int(c1.get("max_trades_per_day", 0) or 0),
        }
        self.exit_min = hm_to_min(cfg.get("exit_time", "15:25"), self.exit_min)
        return snap

    def _roll_day(self, ts: int) -> None:
        dk = ist_day_start(ts)
        if dk != self._day_key:
            self._day_key = dk
            self.taken_today = 0

    def _eod_ts(self, ts: int) -> int:
        return ist_day_start(ts) + self.exit_min * 60

    def _today_iso(self, ts: int) -> str:
        return datetime.utcfromtimestamp(ist_day_start(ts)
                                         + 5 * 3600 + 30 * 60 + 60
                                         ).strftime("%Y-%m-%d")

    # ── ENTRY — signal → pending (two-phase) ─────────────────────────
    def on_signal(self, sig: dict, chain) -> None:
        if self.disabled:
            return
        ts = int(sig["ts"])
        self._roll_day(ts)
        if sig.get("stale"):
            self.diag["skipped_stale"] += 1
            self._sig_log(ts, sig, "skipped_stale")
            return
        snap = self._cfg_snapshot()
        if snap is None:
            self.diag["skipped_config"] += 1
            self._sig_log(ts, sig, "skipped_config_degraded")
            return
        if snap["sell_lots"] <= 0 or snap["buy_lots"] <= 0:
            # spec: both legs must be sized — they enter together
            self.diag["skipped_config"] += 1
            self._sig_log(ts, sig, "skipped_lots_zero (both legs required)")
            return
        if ts < self.busy_until or self.group or self.pending:
            self.diag["skipped_busy"] += 1
            self._sig_log(ts, sig, "skipped_busy")
            return
        if snap["max_tpd"] and self.taken_today >= snap["max_tpd"]:
            self.diag["skipped_cap"] += 1
            self._sig_log(ts, sig, f"skipped_daily_cap ({self.taken_today})")
            return
        if ts >= self._eod_ts(ts):
            return

        # SELECTION on the candle stamped ts-60 (last COMPLETED 1m candle) —
        # backtest parity. sell_side = OPPOSITE of the trend side.
        sell_side = "PE" if sig["side"] == "CE" else "CE"
        cands = []
        for sym in chain.symbols(sell_side):
            c = chain.candle(sym, ts - 60)
            if c and float(c["close"]) > 0:
                cands.append((sym, float(c["close"])))
        pick = select_strike(cands, snap["sell_premium_max"])
        if pick is None:
            self.diag["skipped_select"] += 1
            self._sig_log(ts, sig, "skipped_selection (no sell strike ≤ cap)")
            return
        sell_sym = pick[0]
        # hedge ladder: SAME side, deeper OTM, priced at ts-60; live is
        # REAL STRIKES ONLY — wing_mode real_fallback (cheapest real,
        # flagged) or skip (drop the signal). NO synthetic (spec).
        ladder = [(s, p) for (s, p) in cands if s != sell_sym]
        hpick = select_strike(ladder, snap["buy_premium_max"])
        hedge_fb = False
        if hpick is None and snap["wing_mode"] == "real_fallback":
            hpick = select_strike(ladder, snap["buy_premium_max"],
                                  fallback_cheapest=True)
            hedge_fb = hpick is not None
        if hpick is None:
            self.diag["skipped_hedge"] += 1
            self._sig_log(ts, sig, f"skipped_hedge (wing_mode={snap['wing_mode']})")
            return
        if hedge_fb:
            self.diag["hedge_fallback"] += 1
            write_audit_log(f"[{self._sid}] hedge fell back to cheapest real "
                            f"{hpick[0]} @{hpick[1]} (cap {snap['buy_premium_max']})")

        self._sig_log(ts, sig, f"taken → pending fill SELL {sell_sym} "
                               f"+ BUY {hpick[0]}{' (FB)' if hedge_fb else ''}")
        self.pending = {"sig": dict(sig), "fill_ts": ts, "snap": snap,
                        "sell_symbol": sell_sym, "hedge_symbol": hpick[0],
                        "hedge_fallback": hedge_fb}

    def _sig_log(self, ts, sig, outcome):
        write_audit_log(f"[{self._sid}][SIG] ts={ts} trend={sig.get('side')} "
                        f"→ {outcome}")

    # ── ENTRY — fill at the boundary (paper parity / live sequencing) ─
    def _complete_pending(self, chain) -> None:
        pend, self.pending = self.pending, None
        sig = pend["sig"]
        ts = int(sig["ts"])
        snap = pend["snap"]
        sell_sym, hedge_sym = pend["sell_symbol"], pend["hedge_symbol"]
        fill_c = chain.candle(sell_sym, ts)
        hfill_c = chain.candle(hedge_sym, ts)
        if fill_c is None or hfill_c is None:      # backtest: fill None → skip
            self.diag["skipped_select"] += 1
            write_audit_log(f"[{self._sid}] fill candle missing "
                            f"(sell={fill_c is not None} hedge={hfill_c is not None}) "
                            f"— signal dropped (backtest parity)")
            return
        mode = snap["mode"]
        lot = snap["lot_size"]
        sell_qty = snap["sell_lots"] * lot
        buy_qty = snap["buy_lots"] * lot
        smeta = chain.meta(sell_sym) or {}
        hmeta = chain.meta(hedge_sym) or {}

        if mode == "LIVE":
            if self.executor is None:
                self.diag["skipped_no_executor"] += 1
                write_audit_log(f"[{self._sid}] LIVE mode but no executor — "
                                f"entry skipped (fail closed)")
                record_alert("TMA_NO_EXECUTOR",
                             "TMA_V1: LIVE mode but broker executor missing — entry skipped",
                             severity="error", strategy_id=self._sid, mode="live")
                return
            entered = self._enter_live(sig, snap, chain,
                                       sell_sym, hedge_sym, sell_qty, buy_qty,
                                       smeta, hmeta,
                                       model_sell=float(fill_c["close"]),
                                       model_hedge=float(hfill_c["close"]),
                                       hedge_fb=pend["hedge_fallback"])
            if not entered:
                return
        else:
            self._enter_paper(sig, snap, sell_sym, hedge_sym,
                              sell_qty, buy_qty, smeta, hmeta,
                              sell_fill=float(fill_c["close"]),
                              hedge_fill=float(hfill_c["close"]),
                              hedge_fb=pend["hedge_fallback"])
        self.taken_today += 1
        self.diag["signals_taken"] += 1

    def _mk_group_id(self) -> str:
        return f"TMA-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    def _build_group(self, *, sig, snap, mode, sell_sym, hedge_sym,
                     sell_qty, buy_qty, smeta, hmeta, sell_entry,
                     hedge_entry, hedge_fb, entry_order_id=None,
                     hedge_order_id=None) -> Dict:
        ts = int(sig["ts"])
        sl_level, tp_level = sltp_levels(sell_entry, snap["sl_pct"],
                                         snap["tp_pct"], snap["sl_unit"],
                                         snap["tp_unit"])
        gid = self._mk_group_id()
        pos = new_position(entry_ts=ts, entry_price=sell_entry,
                           sl_price=sl_level, tp_price=tp_level)
        group = {
            "group_id": gid, "mode": mode, "trend_side": sig["side"],
            "sig_ts": ts, "trade_mode": snap["trade_mode"],
            "cut_neg_mtm_eod": snap["cut_neg_mtm_eod"],
            "expiry": smeta.get("expiry"),
            "sell": {"symbol": sell_sym, "qty": sell_qty,
                     "token": smeta.get("token"), "strike": smeta.get("strike"),
                     "side": ("PE" if sig["side"] == "CE" else "CE"),
                     "entry": sell_entry, "sl": sl_level, "tp": tp_level,
                     "db_id": None, "gtt_id": None,
                     "order_id": entry_order_id},
            "hedge": {"symbol": hedge_sym, "qty": buy_qty,
                      "token": hmeta.get("token"), "strike": hmeta.get("strike"),
                      "side": ("PE" if sig["side"] == "CE" else "CE"),
                      "entry": hedge_entry, "db_id": None,
                      "order_id": hedge_order_id, "fallback": hedge_fb},
            "pos": pos,
        }
        return group

    def _persist_entry(self, group: Dict) -> None:
        g = group
        common = dict(group_id=g["group_id"], mode=g["mode"],
                      trend_side=g["trend_side"], expiry=g["expiry"],
                      entry_ts=g["sig_ts"] + 60,      # fill-candle completion stamp
                      condition="C1")
        g["sell"]["db_id"] = self.repo.insert_leg({
            **common, "direction": "SELL",
            "tradingsymbol": g["sell"]["symbol"], "token": g["sell"]["token"],
            "instrument_type": g["sell"]["side"], "strike": g["sell"]["strike"],
            "qty": g["sell"]["qty"], "entry_price": round(g["sell"]["entry"], 2),
            "sl": (round(g["sell"]["sl"], 2) if g["sell"]["sl"] is not None else None),
            "tp": (round(g["sell"]["tp"], 2) if g["sell"]["tp"] is not None else None),
            "entry_order_id": g["sell"].get("order_id"),
            "sell_gtt_id": g["sell"].get("gtt_id"),
        })
        g["hedge"]["db_id"] = self.repo.insert_leg({
            **common, "direction": "BUY",
            "tradingsymbol": g["hedge"]["symbol"], "token": g["hedge"]["token"],
            "instrument_type": g["hedge"]["side"], "strike": g["hedge"]["strike"],
            "qty": g["hedge"]["qty"],
            "entry_price": round(g["hedge"]["entry"], 2),
            "sl": None, "tp": None,
            "entry_order_id": g["hedge"].get("order_id"),
        })
        try:   # ── TMA_TG_NOTIFY ──
            notify_trade_entry({
                "strategy_id": self._sid, "mode": g["mode"].lower(),
                "symbol": g["sell"]["symbol"], "side": g["sell"]["side"],
                "entry_price": round(g["sell"]["entry"], 2),
                "quantity": g["sell"]["qty"], "sl": g["sell"]["sl"],
                "tp": g["sell"]["tp"], "trade_direction": "SHORT",
                "note": (f"credit spread · hedge BUY {g['hedge']['symbol']} "
                         f"@{g['hedge']['entry']:.2f} x{g['hedge']['qty']}"
                         + (" (cheapest-real fallback)" if g["hedge"]["fallback"] else "")),
            })
        except Exception:
            pass
        write_audit_log(f"[{self._sid}][ENTER][{g['mode']}] group={g['group_id']} "
                        f"SELL {g['sell']['symbol']} @{g['sell']['entry']:.2f} "
                        f"x{g['sell']['qty']} sl={g['sell']['sl']} tp={g['sell']['tp']} "
                        f"| BUY {g['hedge']['symbol']} @{g['hedge']['entry']:.2f} "
                        f"x{g['hedge']['qty']} expiry={g['expiry']}")

    def _enter_paper(self, sig, snap, sell_sym, hedge_sym, sell_qty, buy_qty,
                     smeta, hmeta, *, sell_fill, hedge_fill, hedge_fb) -> None:
        self.group = self._build_group(
            sig=sig, snap=snap, mode="PAPER", sell_sym=sell_sym,
            hedge_sym=hedge_sym, sell_qty=sell_qty, buy_qty=buy_qty,
            smeta=smeta, hmeta=hmeta, sell_entry=sell_fill,
            hedge_entry=hedge_fill, hedge_fb=hedge_fb)
        self._exiting = False
        self._defer_to_gtt = False
        self._persist_entry(self.group)

    # ── LIVE entry: margin guard → BUY first → SELL confirm → GTT ────
    def _enter_live(self, sig, snap, chain, sell_sym, hedge_sym, sell_qty,
                    buy_qty, smeta, hmeta, *, model_sell, model_hedge,
                    hedge_fb) -> bool:
        # margin pre-check (spec ×1.25; IC advisory-fail-open semantics)
        if snap["margin_guard"] and not self._margin_ok(
                sell_sym, sell_qty, hedge_sym, buy_qty):
            self.diag["skipped_margin"] += 1
            record_alert("TMA_MARGIN_BLOCK",
                         "TMA_V1 entry blocked: margin shortfall (< required × 1.25)",
                         severity="error", strategy_id=self._sid, mode="live")
            try:
                notify_critical({"message": "TMA_V1: entry SKIPPED — available "
                                            "margin below required × 1.25.",
                                 "severity": "warning"})
            except Exception:
                pass
            return False

        # 1) BUY hedge FIRST; fill CONFIRMED before any SELL exists.
        try:
            res = self.executor.place_buy(hedge_sym, int(hmeta.get("token") or 0),
                                          buy_qty)
            h_oid = res[0]
        except Exception as e:
            write_audit_log(f"[{self._sid}][LIVE][HEDGE_PLACE_FAIL] {e} — no entry")
            return False
        h_fill = self._confirm_fill(h_oid)
        if h_fill is None:
            try:
                self.executor.cancel_order(h_oid)
            except Exception:
                pass
            write_audit_log(f"[{self._sid}][LIVE][HEDGE_DEAD] order={h_oid} — no entry")
            record_alert("TMA_ENTRY_FAIL", "TMA_V1: hedge BUY never filled — no entry",
                         severity="warning", strategy_id=self._sid, mode="live")
            return False

        # 2) SELL leg; rejection after hedge fill → immediate hedge unwind.
        try:
            s_oid, s_limit, _ = self.executor.place_sell_entry(
                symbol=sell_sym, token=int(smeta.get("token") or 0), qty=sell_qty)
        except Exception as e:
            write_audit_log(f"[{self._sid}][LIVE][SELL_PLACE_FAIL] {e} — unwinding hedge")
            self._unwind_hedge(hedge_sym, buy_qty, h_fill)
            return False
        s_fill = self._confirm_fill(s_oid)
        if s_fill is None:
            try:
                self.executor.cancel_order(s_oid)
            except Exception:
                pass
            write_audit_log(f"[{self._sid}][LIVE][SELL_DEAD] order={s_oid} — unwinding hedge")
            self._unwind_hedge(hedge_sym, buy_qty, h_fill)
            return False

        # 3) group + SL GTT off the ACTUAL SELL fill (backtest computes
        #    levels off the entry premium = the fill — same anchor).
        self.group = self._build_group(
            sig=sig, snap=snap, mode="LIVE", sell_sym=sell_sym,
            hedge_sym=hedge_sym, sell_qty=sell_qty, buy_qty=buy_qty,
            smeta=smeta, hmeta=hmeta,
            sell_entry=(s_fill if s_fill > 0 else float(s_limit or model_sell)),
            hedge_entry=(h_fill if h_fill > 0 else model_hedge),
            hedge_fb=hedge_fb, entry_order_id=s_oid, hedge_order_id=h_oid)
        self._exiting = False
        self._defer_to_gtt = False
        g = self.group
        if g["sell"]["sl"] is not None:
            try:
                gid = self.executor.place_gtt_sl_only_short(
                    symbol=sell_sym, qty=sell_qty, sl_price=g["sell"]["sl"])
                if gid:
                    g["sell"]["gtt_id"] = str(gid)
            except Exception as e:
                write_audit_log(f"[{self._sid}][LIVE][GTT_FAIL] {e} — "
                                f"candle monitor is sole SL protection")
                record_alert("TMA_GTT_FAIL",
                             f"{sell_sym} SL GTT failed — app-monitored SL only",
                             severity="error", strategy_id=self._sid,
                             symbol=sell_sym, mode="live")
        self._persist_entry(g)
        if g["sell"]["gtt_id"] and g["sell"]["db_id"]:
            self.repo.update_leg(g["sell"]["db_id"],
                                 sell_gtt_id=g["sell"]["gtt_id"])
        return True

    def _unwind_hedge(self, hedge_sym: str, qty: int, entry_fill: float) -> None:
        """SELL rejected AFTER the hedge filled: flatten the hedge NOW and
        say so loudly (spec). Records an UNWIND row pair for the audit trail
        (hedge only — the SELL never existed)."""
        self.diag["unwinds"] += 1
        exit_px = entry_fill
        try:
            oid = self.executor.place_market_sell(hedge_sym, qty)
            px = self._confirm_fill(oid)
            if px:
                exit_px = px
        except Exception as e:
            write_audit_log(f"[{self._sid}][UNWIND][ORDER_FAIL] {hedge_sym} {e} "
                            f"— POSITION MAY BE LIVE, intervene manually")
        gid = self._mk_group_id()
        db_id = self.repo.insert_leg({
            "group_id": gid, "mode": "LIVE", "direction": "BUY",
            "tradingsymbol": hedge_sym, "qty": qty,
            "entry_ts": int(time.time()), "entry_price": round(entry_fill, 2),
            "condition": "C1", "status": "OPEN"})
        if db_id:
            gross, charges, net = leg_net("BUY", entry_fill, exit_px,
                                          max(1, qty // 65))
            self.repo.close_leg(db_id, exit_ts=int(time.time()),
                                exit_price=exit_px, exit_reason="UNWIND",
                                ambiguous=False, pnl=gross, charges=charges,
                                net_pnl=net)
        record_alert("TMA_UNWOUND",
                     f"TMA_V1: SELL leg rejected after hedge fill — hedge "
                     f"{hedge_sym} unwound @{exit_px:.2f}",
                     severity="error", strategy_id=self._sid, mode="live")
        try:
            notify_critical({"message": f"TMA_V1: SELL entry FAILED after the "
                                        f"hedge filled. Hedge {hedge_sym} x{qty} "
                                        f"unwound @{exit_px:.2f}. No entry.",
                             "severity": "error"})
        except Exception:
            pass

    def _confirm_fill(self, order_id) -> Optional[float]:
        """IC's synchronous confirm: avg_price on COMPLETE; None on DEAD or
        timeout."""
        if not order_id:
            return None
        deadline = time.time() + _ENTRY_FILL_CAP_S
        while time.time() < deadline:
            try:
                info = self.executor.get_order_fill(order_id) or {}
            except Exception as e:
                write_audit_log(f"[{self._sid}][FILL_POLL_ERR] {order_id} {e}")
                info = {}
            status = (info.get("status") or "").upper()
            if status == "COMPLETE":
                return float(info.get("avg_price") or 0.0)
            if status in _DEAD_ORDER_STATUSES:
                return None
            time.sleep(_ENTRY_FILL_POLL_S)
        return None

    def _margin_ok(self, sell_sym, sell_qty, hedge_sym, buy_qty) -> bool:
        """required/available from the executor's basket-margin call — the
        same Zerodha engine behind /api/backtest/margin-estimate. ADVISORY-
        FAIL-OPEN (IC D8): can't-compute ≠ shortfall. Block only a CONFIRMED
        available < required × MARGIN_BUFFER."""
        fn = getattr(self.executor, "get_basket_margin", None)
        if fn is None:
            write_audit_log(f"[{self._sid}][MARGIN] get_basket_margin "
                            f"unavailable — proceeding (fail open)")
            return True
        try:
            basket = [{"symbol": hedge_sym, "qty": buy_qty,
                       "transaction_type": "BUY"},
                      {"symbol": sell_sym, "qty": sell_qty,
                       "transaction_type": "SELL"}]
            res = fn(basket) or {}
            required = float(res.get("required") or 0.0)
            available = float(res.get("available") or 0.0)
            if required > 0 and available > 0 \
                    and available < required * MARGIN_BUFFER:
                write_audit_log(f"[{self._sid}][MARGIN][BLOCK] "
                                f"required={required:.0f} ×{MARGIN_BUFFER} "
                                f"> available={available:.0f}")
                return False
            return True
        except Exception as e:
            write_audit_log(f"[{self._sid}][MARGIN][ERR] {e} — proceeding (fail open)")
            return True

    # ── PER-MINUTE MONITORING (coordinator ordering: this runs FIRST) ─
    def on_minute(self, ts: int, spot_candle: Optional[dict], chain,
                  xover_fn=None) -> None:
        """xover_fn(trend_side, after_ts)->ts|None is injected by the
        coordinator (signal engine's xover_ts_for — same indicator stream
        as the signals)."""
        if self.disabled:
            return
        self._roll_day(ts)

        # ── SESSION_GATE BEGIN ── (2026-07-21 incident — FIRST statement
        # after the disabled check, deliberately: nothing in this method may
        # act outside market hours.)
        # The tick engine's boundary timer runs 24x7, so on_minute fires all
        # night and all weekend. Every one of those minutes has no candle
        # (the market is shut), so the gap streak climbed unbounded from
        # 15:30 to 09:15 — the 08:08 and 09:08 "socket suspect" alerts were
        # emitted at times when NIFTY options do not trade. At 09:15 the LTP
        # backstop then read a stale overnight LTP and booked a positional
        # carry as SL. The market never traded there during the guarded
        # window; the exit was manufactured by our own dead-stream logic.
        # 15:30 is the outer bound and 15:25 (EOD) sits inside it, so the
        # square-off paths below are unaffected.
        #
        # ── CAS_NOTE (2026-08-03) — DO NOT CHANGE 15:30 TO 15:40 ──────────
        # From 2026-08-03 the NFO segment closes at 15:40, NOT 15:30. It is
        # tempting to bump the bound below to (15*60+40) for "correctness".
        # DO NOT. This gate exists to stop the LTP backstop from manufacturing
        # an SL off a stale spot read (the 2026-07-21 incident described above),
        # and the CAS rollout makes the 15:35–15:40 window a SECOND legitimate
        # spot-silence period: NIFTY constituents stop continuous trading at
        # 15:15, the index goes indicative through the auction, and after CAS
        # matching (~15:35) the index is expected to stop updating entirely
        # while options keep trading to 15:40.
        #
        # Extending this bound to 15:40 would therefore let _sold_gap_streak
        # accumulate on a spot feed that is CORRECTLY silent, with the LTP
        # backstop still armed — reproducing the exact 2026-07-21 false-SL
        # failure at the other end of the day.
        #
        # Correct bound for SPOT-derived staleness logic is 15:15 (CAS start)
        # or 15:30. Correct bound for OPTION-LTP-driven logic is 15:40. Two
        # clocks, never one — see app/utils/market_hours.py
        # (is_spot_continuous_session vs is_market_open).
        #
        # 15:30 is kept (not tightened to 15:15) because TMA's exit_min is
        # 15:25 and must stay INSIDE this gate. Tightening to 15:15 would put
        # the EOD square-off outside the gate and silently disable it.
        # ── CAS_NOTE END ─────────────────────────────────────────────────
        _dk_gate = ist_day_start(ts)
        _mins = (ts - _dk_gate) // 60
        _weekday = datetime.utcfromtimestamp(ts + 5 * 3600 + 30 * 60).weekday() < 5
        if not (_weekday and (9 * 60 + 15) <= _mins <= (15 * 60 + 30)):
            self._sold_gap_streak = 0        # never accumulate off-hours
            self._gap_alerted = False
            self.diag["sold_gap_streak"] = 0
            return
        # ── SESSION_GATE END ──

        eod = self._eod_ts(ts)

        # pending fill completion (two-phase entry)
        if self.pending is not None:
            if ts >= eod:
                self.pending = None            # never fill at/after EOD
            elif ts >= self.pending["fill_ts"]:
                self._complete_pending(chain)
        g = self.group
        if not g or self._exiting:
            return

        positional = g["trade_mode"] == "POSITIONAL"
        expiry_today = (g.get("expiry") == self._today_iso(ts))
        hard_today = (not positional) or expiry_today
        dk = ist_day_start(ts)

        # ── NEG_MTM_EOD_CUT arming — positional only, re-arms daily on
        # days with no hard close (runner convention).
        if positional and g["cut_neg_mtm_eod"] and not hard_today \
                and self._mtm_armed_day != dk:
            self._mtm_armed_day = dk
            g["pos"]["mtm_pending"] = True
            g["pos"]["mtm_cut_ts"] = eod

        # hard close boundary (EOD daily / expiry-day close)
        if hard_today and ts >= eod:
            self._exit_group(hard_close(g["pos"], "EOD"), forced=True)
            return
        # data-gap MTM fallback at the boundary (engine post-loop check)
        if positional and not hard_today and ts >= eod \
                and g["pos"].get("mtm_pending"):
            r = mtm_cut_after_data_gap(g["pos"])
            g["pos"]["mtm_pending"] = False
            if r is not None:
                self._exit_group(r, forced=True)
            return

        # ── CARRY_WARMUP BEGIN ── (D-carry)
        # On a CARRIED day the first minute of the session is the gap-open
        # auction print: wildest candle of the day, routinely spiking through
        # a stop that the rest of the minute retraces. A position that
        # survived the night is not exited on that candle — monitoring
        # starts at 09:16. Applies ONLY to carried positions (entered on an
        # earlier day); a position entered today already starts mid-session.
        entered_today = (ist_day_start(g["sig_ts"]) == dk)
        if (not entered_today) and _mins <= (9 * 60 + 15):
            self.diag["carry_first_candle_skipped"] += 1
            write_audit_log(f"[{self._sid}][CARRY] first session candle skipped "
                            f"for carried group {g['group_id']} — monitoring "
                            f"resumes 09:16 (gap-open auction ignored)")
            return
        # ── CARRY_WARMUP END ──

        oc = chain.candle(g["sell"]["symbol"], ts)
        # ── TMA_LTP_BACKSTOP BEGIN ──
        # A missing candle is parity-faithful in the BACKTEST (the corpus
        # gap is real market silence). LIVE, a persistent gap on a near-ATM
        # sold strike means OUR socket starved — the market kept trading.
        # Track the streak LOUDLY, and guard SL/TP off the MAIN feed's LTP
        # (independent socket) so a dead stream can never again hold a
        # breached stop open. Level-fill convention; candle path stays the
        # primary, untouched, whenever candles flow.
        if oc is None:
            self._sold_gap_streak += 1
            self.diag["sold_candle_gaps"] += 1
            self.diag["sold_gap_streak"] = self._sold_gap_streak
            if self._sold_gap_streak >= 3 and not self._gap_alerted:
                self._gap_alerted = True
                write_audit_log(f"[{self._sid}][GAP] sold-leg candle missing "
                                f"{self._sold_gap_streak} consecutive min "
                                f"({g['sell']['symbol']}) — TMA socket suspect; "
                                f"LTP backstop guarding SL/TP")
                record_alert("TMA_CANDLE_GAP",
                             f"TMA_V1: no candles for {g['sell']['symbol']} for "
                             f"{self._sold_gap_streak} min — own socket suspect; "
                             f"SL/TP guarded via main-feed LTP",
                             severity="warning", strategy_id=self._sid,
                             symbol=g["sell"]["symbol"])
            # Freshness is the whole safety property here: a 10-second-old
            # tick is evidence, an overnight one is a trap (2026-07-21).
            ltp = self._fresh_main_ltp(g["sell"]["symbol"], max_age_s=20)
            if ltp is not None:
                sl, tp = g["sell"].get("sl"), g["sell"].get("tp")
                res = None
                if sl is not None and ltp >= float(sl):
                    res = {"exit_ts": ts, "exit_price": float(sl),
                           "exit_reason": "SL", "ambiguous": False}
                elif tp is not None and ltp <= float(tp):
                    res = {"exit_ts": ts, "exit_price": float(tp),
                           "exit_reason": "TP", "ambiguous": False}
                if res is not None:
                    self.diag["ltp_backstop_exits"] += 1
                    write_audit_log(f"[{self._sid}][BACKSTOP_LTP] candle gap + "
                                    f"main-feed ltp={ltp:.2f} breaches "
                                    f"{res['exit_reason']} — exiting at level")
                    if res["exit_reason"] == "SL" and g["mode"] == "LIVE":
                        self._live_sl_detected(res)
                    else:
                        self._exit_group(res, forced=False)
            return
        self._sold_gap_streak = 0
        self._gap_alerted = False
        self.diag["sold_gap_streak"] = 0
        self.diag["last_sold_candle_ts"] = ts
        # ── TMA_LTP_BACKSTOP END ──
        after = g["sig_ts"] if ist_day_start(g["sig_ts"]) == dk \
            else dk + (9 * 60 + 15) * 60        # carried day: session0 (runner)
        xts = xover_fn(g["trend_side"], after) if xover_fn else None
        res = step_candle(g["pos"], oc, xts)
        if res is None:
            return
        if res["exit_reason"] == "SL" and g["mode"] == "LIVE":
            self._live_sl_detected(res)
            return
        self._exit_group(res, forced=(res["exit_reason"] == "MTM_CUT"))

    # ── LIVE SL: the GTT owns it (IC defer doctrine) ─────────────────
    def _live_sl_detected(self, res: dict) -> None:
        g = self.group
        gid = g["sell"].get("gtt_id")
        if not gid:
            # no GTT ever placed → app-monitored SL is the protection
            self._exit_group(res, forced=True)
            return
        if self._defer_to_gtt:
            return                              # backstop owns it already
        # ── TMA_GTT_RACE_20260814 BEGIN ── (D1) broker-truth first.
        # 2026-08-14 incident: the GTT fired at 10:10:47 (position flat);
        # the 10:10-candle SL evaluation then ran cancel_gtt_verified(),
        # which by design reports a "triggered" GTT as gone/spent → the
        # app believed it disarmed the GTT pre-fire and placed its OWN
        # buyback at 10:11:04, opening an accidental LONG. A triggered
        # GTT means the exit ALREADY HAPPENED at the broker — hand off to
        # the GTT monitor, which books the real fill price and closes the
        # hedge (on_backstop_sell_exit, sell_already_flat=True).
        status = None
        try:
            status = self.executor.get_gtt_status(gid)
        except Exception as e:
            write_audit_log(f"[{self._sid}][SL][STATUS_ERR] gtt={gid} {e}")
        if status == "triggered":
            self._defer_to_gtt = True
            write_audit_log(f"[{self._sid}][SL][ALREADY_FIRED] gtt={gid} is "
                            f"triggered at the broker — NOT placing app exit; "
                            f"monitor will book the real fill + close hedge")
            record_alert("TMA_GTT_ALREADY_FIRED",
                         f"TMA_V1: SL GTT already executed at the broker — "
                         f"app exit suppressed (double-fire guard); booking "
                         f"follows the broker fill.",
                         severity="warning", strategy_id=self._sid,
                         mode="live")
            return
        # status None (unreadable) / "missing" / "active" / other → the
        # existing cancel-verified flow decides; the _exit_group position
        # guard (D2) closes the residual race either way.
        # ── TMA_GTT_RACE_20260814 END ──
        gone = False
        try:
            gone = self.executor.cancel_gtt_verified(gid)
        except Exception as e:
            write_audit_log(f"[{self._sid}][SL][CANCEL_ERR] gtt={gid} {e}")
        if gone:
            g["sell"]["gtt_id"] = None
            self._exit_group(res, forced=True)   # disarmed → we exit
            return
        self._defer_to_gtt = True
        write_audit_log(f"[{self._sid}][SL][DEFER] gtt={gid} still armed — "
                        f"GTT + backstop own this exit (no double-fire)")

    # ── GTT backstop handoff (monitor → here; monitor never mutates) ─
    def on_backstop_sell_exit(self, *, exit_price: float, reason: str) -> None:
        g = self.group
        if not g or self._exiting:
            return
        g["sell"]["gtt_id"] = None
        self._exit_group({"exit_ts": int(time.time()) // 60 * 60,
                          "exit_price": float(exit_price),
                          "exit_reason": reason, "ambiguous": False},
                         forced=True, sell_already_flat=True)

    # ── SINGLE AUTHORITATIVE CLOSE PATH ──────────────────────────────
    def _exit_group(self, res: dict, *, forced: bool,
                    sell_already_flat: bool = False) -> None:
        g = self.group
        if not g or self._exiting:
            return
        self._exiting = True
        reason = res["exit_reason"]
        exit_ts = int(res["exit_ts"])
        sell_px = float(res["exit_price"])
        live = g["mode"] == "LIVE"

        if live and not sell_already_flat:
            # cancel-verified first (engine-driven exits). Non-forced
            # (TP/XOVER): unverified cancel → DO NOT exit (double-buy risk).
            gid = g["sell"].get("gtt_id")
            if gid:
                gone = False
                try:
                    gone = self.executor.cancel_gtt_verified(gid)
                except Exception as e:
                    write_audit_log(f"[{self._sid}][EXIT][CANCEL_ERR] gtt={gid} {e}")
                if gone:
                    g["sell"]["gtt_id"] = None
                elif not forced:
                    self._exiting = False
                    write_audit_log(f"[{self._sid}][EXIT][RETRY] {reason}: gtt "
                                    f"{gid} still armed — retrying next minute")
                    record_alert("TMA_GTT_STUCK",
                                 f"TMA_V1 {reason} exit blocked: GTT {gid} "
                                 f"could not be cancelled — retrying",
                                 severity="warning", strategy_id=self._sid,
                                 mode="live")
                    return
                else:
                    try:
                        notify_critical({"message":
                            f"TMA_V1 {reason}: GTT {gid} on "
                            f"{g['sell']['symbol']} could not be cancelled but "
                            f"the position is being flattened NOW. DELETE THE "
                            f"GTT MANUALLY in Kite.", "severity": "error"})
                    except Exception:
                        pass
            # ── TMA_GTT_RACE_20260814 BEGIN ── (D2/D3) broker-truth guard
            # on the buyback. Covers EVERY live buy path through here (SL,
            # EOD, MTM_CUT, kill_close) against the fired-GTT / external-
            # close race: never buy back a short the broker says is gone.
            #   observed None  → positions UNREADABLE: protect the short,
            #                    place the full buyback exactly as before
            #                    (an unprotected short outranks the bounded
            #                    double-buy risk).
            #   observed >= 0  → already flat (or long): SKIP the buyback,
            #                    book the broker's actual last BUY fill
            #                    (D3), say so loudly.
            #   observed <  0  → still short: buy back min(expected, open)
            #                    so a partial broker fill is never doubled.
            buy_qty = int(g["sell"]["qty"])
            observed = None
            try:
                pos = self.executor.get_open_positions_or_none()
            except Exception as e:
                pos = None
                write_audit_log(f"[{self._sid}][EXIT][POS_READ_ERR] {e}")
            if pos is not None:
                observed = 0
                for p in pos:
                    if p.get("tradingsymbol") == g["sell"]["symbol"]:
                        observed = int(p.get("quantity") or 0)
                        break
            if observed is not None and observed >= 0:
                write_audit_log(f"[{self._sid}][EXIT][ALREADY_FLAT] "
                                f"{g['sell']['symbol']} net_qty={observed} at "
                                f"the broker — buyback SKIPPED (double-fire "
                                f"guard); booking broker fill")
                px = self._last_buy_fill_px(g["sell"]["symbol"])
                if px:
                    sell_px = px
                record_alert("TMA_BUYBACK_SKIPPED",
                             f"TMA_V1: {g['sell']['symbol']} was already flat "
                             f"at the broker at {reason} — buyback skipped, "
                             f"exit booked at the broker fill. Verify in Kite.",
                             severity="warning", strategy_id=self._sid,
                             mode="live")
                try:
                    notify_critical({"message":
                        f"TMA_V1 {reason}: {g['sell']['symbol']} already flat "
                        f"at the broker — app buyback SKIPPED (double-fire "
                        f"guard). Exit booked from the broker fill; verify "
                        f"positions in Kite.", "severity": "warning"})
                except Exception:
                    pass
            else:
                if observed is not None and -observed < buy_qty:
                    write_audit_log(f"[{self._sid}][EXIT][PARTIAL] "
                                    f"{g['sell']['symbol']} open short "
                                    f"{-observed} < expected {buy_qty} — "
                                    f"buying back only the open quantity")
                    buy_qty = -observed
                try:
                    oid = self.executor.place_buy_exit(
                        symbol=g["sell"]["symbol"], qty=buy_qty,
                        reason=reason)
                    px = self._confirm_fill(oid)
                    if px:
                        sell_px = px
                except Exception as e:
                    write_audit_log(f"[{self._sid}][EXIT][SELL_BUYBACK_FAIL] {e} "
                                    f"— booking at model price, VERIFY IN KITE")
                    try:
                        notify_critical({"message":
                            f"TMA_V1: buyback of {g['sell']['symbol']} FAILED at "
                            f"{reason} — verify/flatten manually in Kite.",
                            "severity": "error"})
                    except Exception:
                        pass
            # ── TMA_GTT_RACE_20260814 END ──

        # hedge exits the SAME minute (spec: SELL leg drives ALL exits)
        hedge_px = self._hedge_exit_price(g, exit_ts)
        if live:
            try:
                oid = self.executor.place_market_sell(g["hedge"]["symbol"],
                                                      g["hedge"]["qty"])
                px = self._confirm_fill(oid)
                if px:
                    hedge_px = px
            except Exception as e:
                write_audit_log(f"[{self._sid}][EXIT][HEDGE_SELL_FAIL] {e} "
                                f"— booking at model price, VERIFY IN KITE")
                try:
                    notify_critical({"message":
                        f"TMA_V1: hedge sell of {g['hedge']['symbol']} FAILED "
                        f"at {reason} — verify/flatten manually in Kite.",
                        "severity": "error"})
                except Exception:
                    pass

        self._book_exit(g, exit_ts, sell_px, hedge_px, reason,
                        bool(res.get("ambiguous")))
        self.busy_until = exit_ts + 60          # run-day parity
        self.group = None
        self._exiting = False
        self._defer_to_gtt = False

    # ── TMA_GTT_RACE_20260814 BEGIN ── (D3) broker fill truth
    def _last_buy_fill_px(self, symbol: str) -> Optional[float]:
        """Most recent COMPLETE BUY fill on the symbol from the broker
        order book (GTT monitor's _fill_from_orders pattern). Used when the
        buyback is skipped because the broker already closed the short —
        the booked exit must be the REAL fill (the 2026-08-14 incident
        booked the phantom second order's 143.40 instead of the GTT's
        143.90). Returns None when nothing resolvable; caller keeps the
        model price and the audit log flags it."""
        try:
            orders = self.executor.get_orders() or []
        except Exception as e:
            write_audit_log(f"[{self._sid}][EXIT][FILL_READ_ERR] {e}")
            return None
        for o in reversed(orders):
            if (o.get("tradingsymbol") == symbol
                    and o.get("transaction_type") == "BUY"
                    and (o.get("status") or "").upper() == "COMPLETE"):
                px = float(o.get("average_price") or 0.0)
                if px > 0:
                    return px
        write_audit_log(f"[{self._sid}][EXIT][FILL_UNRESOLVED] no COMPLETE "
                        f"BUY found for {symbol} — booking model price")
        return None
    # ── TMA_GTT_RACE_20260814 END ──

    def _hedge_exit_price(self, g: Dict, exit_ts: int) -> float:
        """Backtest _hedge_exit_price shape: candle at exit_ts, else last
        candle ≤ exit_ts, else entry (flagged)."""
        px = None
        chain = g.get("_chain_ref")
        try:
            if chain is not None:
                px = chain.last_close_at_or_before(g["hedge"]["symbol"], exit_ts)
        except Exception:
            px = None
        if px is None:
            px = float(g["hedge"].get("last_close") or g["hedge"]["entry"])
            write_audit_log(f"[{self._sid}] hedge exit fallback to "
                            f"{'last mark' if g['hedge'].get('last_close') else 'entry'} "
                            f"for {g['hedge']['symbol']}")
        return float(px)

    # ── TMA_LTP_BACKSTOP ──
    def _fresh_main_ltp(self, symbol: str, max_age_s: int = 30):
        """Fresh LTP from the MAIN feed's store, or None. Stale (>30s) reads
        are rejected — the LTPStore-can-be-stale house rule."""
        if LTPStore is None:
            return None
        try:
            res = LTPStore.get_with_timestamp(symbol)
            if res:
                ltp, t = res
                if ltp and ltp > 0 and (time.time() - t) <= max_age_s:
                    return float(ltp)
        except Exception:
            pass
        return None

    def note_chain(self, chain) -> None:
        """Coordinator gives the manager a chain reference each minute so
        exit pricing can read hedge candles without threading params."""
        if self.group is not None:
            self.group["_chain_ref"] = chain
            hc = chain.candle(self.group["hedge"]["symbol"], chain.now)
            if hc:
                self.group["hedge"]["last_close"] = float(hc["close"])

    def _book_exit(self, g, exit_ts, sell_px, hedge_px, reason, amb) -> None:
        # leg_net takes LOTS (pst_common.LOT_SIZE=65, the fixed NIFTY
        # constant); qty is lots*65 by construction at entry.
        s_gross, s_ch, s_net = leg_net("SELL", g["sell"]["entry"], sell_px,
                                       g["sell"]["qty"] // 65)
        h_gross, h_ch, h_net = leg_net("BUY", g["hedge"]["entry"], hedge_px,
                                       g["hedge"]["qty"] // 65)
        if g["sell"]["db_id"] is not None:
            self.repo.close_leg(g["sell"]["db_id"], exit_ts=exit_ts,
                                exit_price=sell_px, exit_reason=reason,
                                ambiguous=amb, pnl=s_gross, charges=s_ch,
                                net_pnl=s_net)
        if g["hedge"]["db_id"] is not None:
            self.repo.close_leg(g["hedge"]["db_id"], exit_ts=exit_ts,
                                exit_price=hedge_px, exit_reason=reason,
                                ambiguous=False, pnl=h_gross, charges=h_ch,
                                net_pnl=h_net)
        if amb:
            self.diag["ambiguous"] += 1
        net = round(s_net + h_net, 2)
        write_audit_log(f"[{self._sid}][EXIT][{g['mode']}] group={g['group_id']} "
                        f"{reason}{' AMB' if amb else ''} sell@{sell_px:.2f} "
                        f"hedge@{hedge_px:.2f} net={net:.0f}")
        try:   # ── TMA_TG_NOTIFY ──
            _d = {"strategy_id": self._sid, "mode": g["mode"].lower(),
                  "symbol": g["sell"]["symbol"], "side": g["sell"]["side"],
                  "entry_price": round(g["sell"]["entry"], 2),
                  "exit_price": round(sell_px, 2), "pnl": net,
                  "note": (f"spread net incl hedge · "
                           f"hedge exit @{hedge_px:.2f}"
                           + (" · AMBIGUOUS minute" if amb else ""))}
            if reason == "TP":
                notify_tp_exit(_d)
            elif reason == "SL":
                notify_sl_exit(_d)
            else:
                _d["note"] = reason + " · " + _d["note"]
                notify_manual_exit(_d)
        except Exception:
            pass

    # ── EOD safety net (cron + coordinator belt-and-braces) ──────────
    def force_eod(self, ts: int) -> None:
        """Re-runs the boundary decision the candle path makes at exit_time:
        INTRADAY / expiry-day → hard close; positional MTM-cut if armed and
        marking negative; positional non-expiry carry → deliberate no-op."""
        g = self.group
        if self.disabled or not g or self._exiting:
            return
        positional = g["trade_mode"] == "POSITIONAL"
        expiry_today = (g.get("expiry") == self._today_iso(ts))
        if (not positional) or expiry_today:
            self._exit_group(hard_close(g["pos"], "EOD"), forced=True)
            return
        if g["cut_neg_mtm_eod"] and g["pos"].get("mtm_pending"):
            r = mtm_cut_after_data_gap(g["pos"])
            g["pos"]["mtm_pending"] = False
            if r is not None:
                self._exit_group(r, forced=True)

    # ── KILL SWITCH (2026-07-26) ── ADDITIVE. force_eod deliberately
    # no-ops a POSITIONAL non-expiry carry; a human pressing KILL means
    # NOW, so this hard-closes unconditionally through the SAME forced
    # exit path (cancel-verified GTT first; unverified → the forced path's
    # own retry/alert semantics keep the group open for the next attempt
    # rather than double-firing). No parity-relevant logic is touched —
    # this only composes existing exits.
    def kill_close(self, ts: int) -> None:
        g = self.group
        if self.disabled or not g or self._exiting:
            return
        self._exit_group(hard_close(g["pos"], "EOD"), forced=True)

    # ── RESTART ADOPTION ─────────────────────────────────────────────
    def adopt_rows(self, rows: List[dict], *, kite=None) -> None:
        """Rebuild the open group from OPEN tma_trades rows (both legs of one
        group_id). Caller has already applied the INTRADAY prior-day →
        STALE rule; whatever arrives here is legitimate to adopt. LIVE
        adoption cross-checks broker net qty (fail closed → disabled) and
        GTT presence (LOUD naked-short alert if missing — never silent)."""
        if not rows:
            return
        by_gid: Dict[str, List[dict]] = {}
        for r in rows:
            by_gid.setdefault(r["group_id"], []).append(r)
        gid, legs = sorted(by_gid.items())[0]
        if len(by_gid) > 1:
            write_audit_log(f"[{self._sid}][ADOPT] {len(by_gid)} open groups "
                            f"found — adopting {gid}, others need manual review")
            record_alert("TMA_MULTI_GROUP",
                         f"TMA_V1: {len(by_gid)} open groups in tma_trades — "
                         f"adopted {gid}, review the rest manually",
                         severity="error", strategy_id=self._sid)
        sell = next((r for r in legs if r["direction"] == "SELL"), None)
        hedge = next((r for r in legs if r["direction"] == "BUY"), None)
        if sell is None:
            write_audit_log(f"[{self._sid}][ADOPT] group {gid} has no SELL "
                            f"leg — cannot adopt, review manually")
            return
        mode = str(sell.get("mode", "PAPER")).upper()

        if mode == "LIVE" and kite is not None:
            try:
                net = {}
                for p in kite.positions().get("net", []):
                    net[p.get("tradingsymbol")] = int(p.get("quantity") or 0)
                want_ok = (net.get(sell["tradingsymbol"], 0) == -int(sell["qty"]))
                if hedge is not None:
                    want_ok = want_ok and (net.get(hedge["tradingsymbol"], 0)
                                           == int(hedge["qty"]))
                if not want_ok:
                    self.disabled = True
                    write_audit_log(f"[{self._sid}][RECON] broker positions "
                                    f"do not match tma_trades rows — manager "
                                    f"DISABLED (fail closed), resolve manually")
                    try:
                        notify_critical({"message":
                            "TMA_V1 RECON MISMATCH: broker positions ≠ "
                            "tma_trades open rows — TMA disabled, resolve "
                            "manually.", "severity": "error"})
                    except Exception:
                        pass
                    return
            except Exception as e:
                self.disabled = True
                write_audit_log(f"[{self._sid}][RECON] positions check failed "
                                f"({e}) — manager DISABLED (fail closed)")
                return

        snap = self._cfg_snapshot() or {}
        entered_today = ist_day_start(int(sell["entry_ts"])) \
            == ist_day_start(int(time.time()))
        pos = new_position(
            entry_ts=int(sell["entry_ts"]) - 60,
            entry_price=float(sell["entry_price"]),
            sl_price=sell.get("sl"), tp_price=sell.get("tp"),
            watch_from=(None if entered_today else 0))   # carried-day rule
        self.group = {
            "group_id": gid, "mode": mode,
            "trend_side": sell.get("trend_side") or
                          ("CE" if sell.get("instrument_type") == "PE" else "PE"),
            "sig_ts": int(sell["entry_ts"]) - 60,
            "trade_mode": snap.get("trade_mode", "INTRADAY"),
            "cut_neg_mtm_eod": snap.get("cut_neg_mtm_eod", False),
            "expiry": sell.get("expiry"),
            "sell": {"symbol": sell["tradingsymbol"], "qty": int(sell["qty"]),
                     "token": sell.get("token"), "strike": sell.get("strike"),
                     "side": sell.get("instrument_type"),
                     "entry": float(sell["entry_price"]),
                     "sl": sell.get("sl"), "tp": sell.get("tp"),
                     "db_id": sell["id"], "gtt_id": sell.get("sell_gtt_id"),
                     "order_id": sell.get("entry_order_id")},
            "hedge": ({"symbol": hedge["tradingsymbol"], "qty": int(hedge["qty"]),
                       "token": hedge.get("token"), "strike": hedge.get("strike"),
                       "side": hedge.get("instrument_type"),
                       "entry": float(hedge["entry_price"]),
                       "db_id": hedge["id"], "order_id": hedge.get("entry_order_id"),
                       "fallback": False}
                      if hedge is not None else
                      {"symbol": sell["tradingsymbol"], "qty": 0, "token": None,
                       "strike": None, "side": sell.get("instrument_type"),
                       "entry": 0.0, "db_id": None, "order_id": None,
                       "fallback": False}),
            "pos": pos,
        }
        self._exiting = False
        self._defer_to_gtt = False
        write_audit_log(f"[{self._sid}][ADOPT] group {gid} adopted "
                        f"(mode={mode}, entered_today={entered_today}, "
                        f"gtt={self.group['sell']['gtt_id']})")

        # GTT reconciliation — LOUD, never a silent no-op (spec).
        if mode == "LIVE" and self.executor is not None \
                and self.group["sell"]["sl"] is not None:
            gid_gtt = self.group["sell"]["gtt_id"]
            present = False
            try:
                gtts = self.executor.get_gtts() or []
                present = any(str(x.get("id")) == str(gid_gtt) for x in gtts)
            except Exception as e:
                write_audit_log(f"[{self._sid}][ADOPT][GTT_CHECK_ERR] {e} — "
                                f"treating GTT as UNVERIFIED (alerting)")
            if not present:
                write_audit_log(f"[{self._sid}][ADOPT][NAKED] LIVE short "
                                f"{self.group['sell']['symbol']} adopted with "
                                f"NO verifiable SL GTT — candle SL is sole "
                                f"protection. ALERTING.")
                try:
                    notify_critical({"message":
                        f"TMA_V1: adopted LIVE short "
                        f"{self.group['sell']['symbol']} but its SL GTT "
                        f"({gid_gtt}) is NOT at the broker. App-monitored SL "
                        f"only — re-create the GTT or exit manually.",
                        "severity": "error"})
                except Exception:
                    pass