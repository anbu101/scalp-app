# backend/app/backtest/pst/backtest_pst_hedge_runner.py
#
# ── PST_HEDGE RUNNER (v2 — SIGNAL-TRACKED, D17-amended 2026-07-13) ──
# REPLACES the v1 side-flip runner IN PLACE (D22). Old-semantics PST_HEDGE
# runs must be deleted in Compare Runs — same strategy id, different
# semantics, comparing them would be quietly misleading.
#
# CONSTRUCT: PST_V1's signals; PST_SELL's exit EVENT STREAM; a LONG in the
# app's already-selected opposite-side contract.
#
#   * SELECTION at a signal (ts = 3m bar completion), BOTH sides priced off
#     the last COMPLETED 1m candle (ts-60), premium < cap nearest-below:
#       signal side  → SIGNAL contract (never traded; its close at ts is
#                      the virtual entry that anchors the 20% TP level —
#                      the exact price PST_SELL would have shorted at)
#       opposite side→ HELD contract (BOUGHT at its close at ts)
#     Either side unselectable, or either fill candle missing at ts →
#     signal skipped, counted (fail closed).
#   * MONITORING starts at ts+60 for both contracts.
#   * Events (in pst_hedge_engine, byte-identical triggers to PST_SELL):
#       SIG_TP  — signal contract 1m LOW <= sig_entry×(1−sl_pct/100)
#       SPOT_SL — spot ±spot_tg_points WITH the signal
#       same minute → SPOT_SL wins + ambiguous flag
#     Exits fill at the HELD contract's close of the event minute.
#   * PERSISTED ROW: direction=BUY on the held symbol; tp = the SIGNAL
#     contract's TP level (an external tripwire, NOT a held price — the
#     held exit will never equal it); sl = NULL (the SL is a SPOT level).
#     exit_reason set: SIG_TP | SPOT_SL | EOD. gross=(exit−entry)×qty,
#     charges_for_long_trade.
#   * side_mode filters the SIGNAL side (D21) so CE-only reproduces
#     PST_SELL's CE-only event stream. DIAG side-skips count accordingly.
#
# Everything else — expected weekly expiry fail-closed, cross-day warmup,
# one-position-at-a-time, entry cutoff, EOD — is PST_V1's, unchanged.

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

try:
    from app.backtest.pst.pst_v1_engine import build_signals
    from app.backtest.pst.pst_hedge_engine import run_day_hedge
    from app.backtest.ic.ic_v1_engine import select_strike
except ImportError:  # standalone tests
    from pst_v1_engine import build_signals  # type: ignore
    from pst_hedge_engine import run_day_hedge  # type: ignore
    from ic_v1_engine import select_strike  # type: ignore

IST = 5 * 3600 + 30 * 60
LOT_SIZE = 65

DEFAULT_LEGS = [
    {"id": "L1", "lots": 2, "sl_pct": 15, "spot_tg_points": 20},
    {"id": "L2", "lots": 1, "sl_pct": 15, "spot_tg_points": 50},
]


def _hm_to_min(hm: str, default_min: int) -> int:
    try:
        h, m = str(hm).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _day_start_epoch(d: date) -> int:
    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)
                ).total_seconds()) - IST


def _other(side: str) -> str:
    return "PE" if side == "CE" else "CE"


@dataclass
class PSTHedgeTrade:
    """persist_run non-hedge attribute surface (t.symbol is what it reads)."""
    tradingsymbol: str            # HELD contract
    symbol: str
    instrument_type: str          # HELD side
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                # always BUY
    entry_ts: int
    entry_price: float            # HELD fill
    sl: Optional[float]           # NULL — the SL is a SPOT level
    tp: Optional[float]           # SIGNAL contract's TP level (tripwire)
    exit_ts: Optional[int]
    exit_price: Optional[float]   # HELD close at the event minute
    exit_reason: Optional[str]    # SIG_TP | SPOT_SL | EOD
    qty: int
    condition: str                # leg id + SIGNAL side + levels
    ambiguous_fill: bool = False
    pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _summarize(trades: List[PSTHedgeTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_pst"] = diag
        return s
    nets = [t.net_pnl for t in closed]
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.entry_ts or 0, x.condition)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    wins = sum(1 for n in nets if n > 0)
    return {
        "total_trades": len(closed), "wins": wins,
        "losses": sum(1 for n in nets if n < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(sum(nets), 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": sum(1 for t in closed if t.ambiguous_fill),
        "diag_pst": diag,
    }


def run_pst_hedge_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "PST_HEDGE"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _impl(db_path=db_path, strategy_id=strategy_id,
                         underlying=underlying, date_from=date_from,
                         date_to=date_to, config_override=config_override,
                         progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _impl(db_path=db_path, strategy_id=strategy_id,
                     underlying=underlying, date_from=date_from,
                     date_to=date_to, config_override=config_override,
                     progress_cb=progress_cb, cancel_cb=cancel_cb)


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.event_bus.audit_logger import write_audit_log
    try:
        from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    except ImportError:
        from app.backtest.engine.backtest_selector import expected_expiry_for_day

    cfg = config_override or {}
    prem_max = float(cfg.get("premium_max", 150) or 150)
    legs = [l for l in (cfg.get("legs") or DEFAULT_LEGS)
            if int(l.get("lots") or 0) > 0]
    side_mode = str(cfg.get("side_mode", "BOTH") or "BOTH")
    max_tpd = int(cfg.get("max_trades_per_day", 0) or 0)
    exit_min = _hm_to_min(cfg.get("exit_time", "15:25"), 15 * 60 + 25)
    cutoff_min = _hm_to_min(cfg.get("entry_cutoff_time", "15:00"), 15 * 60)
    sig_tf = int(cfg.get("signal_tf", 3) or 3)
    sma_cfg = cfg.get("sma") or {}
    st_cfg = cfg.get("supertrend") or {}
    if not legs:
        return {"run_id": None, "aborted": True,
                "reason": "PST_HEDGE needs at least one leg with lots > 0",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # LONG held contract: STT on the exit/sell leg — same as PST_V1.
    try:
        from app.backtest.charges.charges_model import charges_for_long_trade
    except Exception:
        charges_for_long_trade = None

    # ── PST_RISK_LIMITS BEGIN ── daily/monthly ₹ P&L guards (V3 semantics;
    # 0/absent = off). Basis: realized NET (post-charge) per IST calendar
    # day / month; intrabar clamp + entry gate live in the engine; the month
    # buckets and stats live here and persist across the day loop.
    _rl_dml = max(0.0, float(cfg.get("daily_max_loss") or 0))
    _rl_dmp = max(0.0, float(cfg.get("daily_max_profit") or 0))
    _rl_mml = max(0.0, float(cfg.get("monthly_max_loss") or 0))
    _rl_mmp = max(0.0, float(cfg.get("monthly_max_profit") or 0))
    _rl_enabled = any(v > 0 for v in (_rl_dml, _rl_dmp, _rl_mml, _rl_mmp))
    _month_realized = {}
    _month_blocked = set()
    _rl_stats = {"risk_exits": 0, "days_blocked": 0, "months_blocked": []}

    def _leg_net(entry, exit_px, lots):
        """(gross, charges, net) for ONE leg — the SINGLE formula used by
        BOTH the engine's risk gate (via pnl_fn) and the persistence loop,
        so gate math and persisted numbers can never drift apart."""
        qty = int(lots) * LOT_SIZE
        # LONG gross = (exit − entry) × qty.
        gross = (float(exit_px) - float(entry)) * qty
        charges = 0.0
        if charges_for_long_trade is not None:
            try:
                cr = charges_for_long_trade(entry_price=float(entry),
                                  exit_price=float(exit_px), qty=qty)
                charges = float(getattr(cr, "total_charges", 0.0))
                gross = float(getattr(cr, "gross_pnl", gross))
            except Exception:
                charges = 0.0
        return gross, charges, gross - charges
    # ── PST_RISK_LIMITS END ──

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    src = CandleSource(db_path)

    lo, hi = _day_start_epoch(date_from), _day_start_epoch(date_to) + 86400
    spot_days = [date.fromisoformat(r["d"]) for r in cur.execute("""
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') d
        FROM backtest_candles_1m
        WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
        ORDER BY d""", (underlying, lo, hi))]
    if len(spot_days) < 2:
        conn.close()
        return {"run_id": None, "aborted": True,
                "reason": "not enough NIFTY spot data — run the spot backfill",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    def spot_1m_for(d: date) -> List[dict]:
        ds = _day_start_epoch(d)
        return [dict(r) for r in cur.execute("""
            SELECT ts, open, high, low, close FROM backtest_candles_1m
            WHERE underlying=? AND instrument_type='SPOT' AND ts>=? AND ts<?
            ORDER BY ts""", (underlying, ds, ds + 86400))]

    diag = {"days_total": len(spot_days), "days_traded": 0,
            "days_no_prev_session": 0, "days_uncovered": 0,
            "days_no_options": 0, "signals_total": 0, "signals_taken": 0,
            "signals_skipped_busy": 0, "signals_skipped_select": 0,
            "signals_skipped_side": 0, "signals_skipped_cap": 0,
            "signals_skipped_risk": 0,   # ── PST_RISK_LIMITS ──
            "blocked_warmup": 0, "blocked_gate": 0, "ambiguous": 0}
    trades: List[PSTHedgeTrade] = []
    prev_hlc: Optional[dict] = None
    prev_day: Optional[date] = None
    # PST_XDAY_WARMUP BEGIN
    prev_spot: Optional[List[dict]] = None      # prior session's raw 1m spot
    prev_day_start: Optional[int] = None        # its day_start epoch
    # PST_XDAY_WARMUP END

    for di, d in enumerate(spot_days, start=1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb({"day": di, "total_days": len(spot_days),
                         "date": d.isoformat()})
        spot = spot_1m_for(d)
        hlc = {"high": max(c["high"] for c in spot),
               "low": min(c["low"] for c in spot),
               "close": spot[-1]["close"]} if spot else None
        # PST_XDAY_WARMUP BEGIN — capture prior session BEFORE rotating state
        this_prev = prev_hlc
        this_prev_spot = prev_spot
        this_prev_day_start = prev_day_start
        prev_hlc, prev_day = hlc, d
        prev_spot = spot if spot else None
        prev_day_start = _day_start_epoch(d)
        # PST_XDAY_WARMUP END
        if this_prev is None:
            diag["days_no_prev_session"] += 1
            continue
        if not spot:
            continue

        day_start = _day_start_epoch(d)
        eod_ts = day_start + exit_min * 60
        universe = src.contracts_active_on_day(underlying, day_start)
        want_expiry = expected_expiry_for_day(d).isoformat()
        week = [c for c in universe if c.get("expiry") == want_expiry]
        if not universe:
            diag["days_no_options"] += 1
            continue
        if not week:
            diag["days_uncovered"] += 1
            continue
        meta = {c["tradingsymbol"]: c for c in week}
        by_side = {"CE": [c["tradingsymbol"] for c in week if c["instrument_type"] == "CE"],
                   "PE": [c["tradingsymbol"] for c in week if c["instrument_type"] == "PE"]}

        # PST_XDAY_WARMUP BEGIN — one prior session is ample warmup for
        # SuperTrend(10)@3m and SMA(9)@5m (~125 / 75 completed bars).
        warmup_sessions = []
        if this_prev_spot and this_prev_day_start is not None:
            warmup_sessions = [(this_prev_spot, this_prev_day_start)]
        # PST_XDAY_WARMUP END

        sig_res = build_signals(spot, day_start, this_prev,
                                signal_tf=sig_tf,
                                sma_period=int(sma_cfg.get("period", 9) or 9),
                                sma_tf=int(sma_cfg.get("tf", 5) or 5),
                                st_period=int(st_cfg.get("period", 10) or 10),
                                st_mult=float(st_cfg.get("mult", 2.0) or 2.0),
                                entry_cutoff_min=cutoff_min,
                                warmup_sessions=warmup_sessions)
        diag["signals_total"] += sig_res["diag"]["signals"]
        diag["blocked_warmup"] += sig_res["diag"]["blocked_warmup"]
        diag["blocked_gate"] += sig_res["diag"]["blocked_gate"]
        if not sig_res["signals"]:
            continue

        # ── PST_HEDGE_PAIR_SELECT BEGIN ── select BOTH sides at ts-60 with
        # the SAME premium<cap nearest-below rule; signal side anchors the
        # levels (virtual entry = its close at ts), opposite side is BOUGHT
        # (fill = its close at ts). Either half missing → None (fail closed).
        def _pick(side: str, ts: int) -> Optional[dict]:
            cands = []
            for sym in by_side.get(side, []):
                cds = src.candles_1m_for_symbol_day(sym, day_start)
                px = None
                for x in cds:
                    if x.ts == ts - 60:
                        px = float(x.close)
                        break
                if px:
                    cands.append((sym, px))
            pick = select_strike(cands, prem_max)
            if pick is None:
                return None
            sym = pick[0]
            cds = src.candles_1m_for_symbol_day(sym, day_start)
            fill = next((float(x.close) for x in cds if x.ts == ts), None)
            if fill is None:
                return None
            return {"symbol": sym, "entry": fill,
                    "candles": [{"ts": x.ts, "open": x.open, "high": x.high,
                                 "low": x.low, "close": x.close}
                                for x in cds if x.ts >= ts + 60]}

        def select_pair(sig_side: str, ts: int) -> Optional[dict]:
            sig_pick = _pick(sig_side, ts)
            if sig_pick is None:
                return None
            held_pick = _pick(_other(sig_side), ts)
            if held_pick is None:
                return None
            return {"sig_symbol": sig_pick["symbol"],
                    "sig_entry": sig_pick["entry"],
                    "sig_candles": sig_pick["candles"],
                    "held_symbol": held_pick["symbol"],
                    "held_side": _other(sig_side),
                    "held_entry": held_pick["entry"],
                    "held_candles": held_pick["candles"]}
        # ── PST_HEDGE_PAIR_SELECT END ──

        # ── PST_RISK_LIMITS BEGIN ── per-day risk state; month buckets carry
        _mk = d.strftime("%Y-%m")
        risk = None
        if _rl_enabled:
            risk = {"enabled": True, "dml": _rl_dml, "dmp": _rl_dmp,
                    "mml": _rl_mml, "mmp": _rl_mmp,
                    "day_realized": 0.0,
                    "month_realized": _month_realized.get(_mk, 0.0),
                    "day_blocked": False,
                    "month_blocked": _mk in _month_blocked,
                    "pnl_fn": (lambda e, x, l: _leg_net(e, x, l)[2]),
                    "lot_size": LOT_SIZE, "risk_exits": 0}
        # ── PST_RISK_LIMITS END ──
        day = run_day_hedge(sig_res["signals"], legs, select_pair, spot, eod_ts,
                            side_mode=side_mode, max_trades_per_day=max_tpd,
                            risk=risk)
        # ── PST_RISK_LIMITS BEGIN ── sync month buckets + stats back
        if risk is not None:
            _month_realized[_mk] = risk["month_realized"]
            if risk["month_blocked"]:
                _month_blocked.add(_mk)
            _rl_stats["risk_exits"] += risk["risk_exits"]
            if risk["day_blocked"] or risk["month_blocked"]:
                _rl_stats["days_blocked"] += 1
        # ── PST_RISK_LIMITS END ──
        dd = day["diag"]
        for k in ("signals_taken", "signals_skipped_busy",
                  "signals_skipped_select", "signals_skipped_side",
                  "signals_skipped_cap", "signals_skipped_risk", "ambiguous"):
            diag[k] += dd[k]
        if dd["signals_taken"]:
            diag["days_traded"] += 1

        for lt in day["trades"]:
            qty = int(lt["lots"]) * LOT_SIZE
            # ── PST_RISK_LIMITS ── via _leg_net (single formula, risk-gate parity)
            gross, charges, _net = _leg_net(lt["entry_price"], lt["exit_price"], lt["lots"])
            m = meta.get(lt["tradingsymbol"], {})
            trades.append(PSTHedgeTrade(
                tradingsymbol=lt["tradingsymbol"], symbol=lt["tradingsymbol"],
                instrument_type=lt["held_side"], strike=m.get("strike"),
                expiry=m.get("expiry"), direction="BUY",
                entry_ts=lt["entry_ts"] + 60,   # fill-candle completion
                entry_price=round(float(lt["entry_price"]), 2),
                sl=None,   # the SL is a SPOT level, not an option price
                tp=(round(lt["sig_tp_level"], 2) if lt["sig_tp_level"] is not None else None),
                exit_ts=lt["exit_ts"],
                exit_price=round(float(lt["exit_price"]), 2),
                exit_reason=lt["exit_reason"], qty=qty,
                condition=f"{lt['leg']}·{lt['sig_side']}·{lt.get('signal_levels','')}",
                ambiguous_fill=bool(lt["ambiguous_fill"]),
                pnl=round(gross, 2), charges=round(charges, 2),
                net_pnl=round(gross - charges, 2),
                gross=round(gross, 2), net=round(gross - charges, 2),
                ambiguous=bool(lt["ambiguous_fill"]),
            ))

    conn.close()
    try:
        src.close()
    except Exception:
        pass
    summary = _summarize(trades, diag)
    # ── PST_RISK_LIMITS ── surface guard activity in the persisted summary
    _rl_stats["months_blocked"] = sorted(_month_blocked)
    summary["risk_limits"] = _rl_stats
    write_audit_log(
        f"[BACKTEST][PST_HEDGE] {underlying} {date_from}→{date_to}: "
        f"{diag['days_traded']}/{diag['days_total']} days traded, "
        f"{diag['signals_taken']}/{diag['signals_total']} signals taken, "
        f"{len(trades)} leg-trades, net {summary['net_pnl']}, "
        f"warmupBlk {diag['blocked_warmup']} gateBlk {diag['blocked_gate']}")
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}