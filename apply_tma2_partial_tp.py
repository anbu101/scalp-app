#!/usr/bin/env python3
# apply_tma2_partial_tp.py — fence TMA2_PARTIAL_TP_20261014
#
# TMA_V2 BACKTEST: optional two-target partial profit booking.
#
#   OFF (default) → single TP exactly as before (proven row-for-row identical).
#   ON            → T1 books `T1 lots` of the monitored leg; the remaining lots
#                   run to T2 (the existing TP field) / SL / crossover / EOD.
#
# DESIGN (backtest only — paper/live untouched)
#   * T1 is in the SAME unit as the TP column (% / ₹ offset / ₹ level). It must be
#     the nearer target: SELL + ₹ level → T1 ABOVE T2 (sold premium falls through
#     T1 first); every other combination → T1 smaller than T2. Inverted → the run
#     aborts loudly. T2 = 0 is allowed: book T1, let the rest ride to xover/SL/EOD.
#   * T1 lots: 1 … lots−1. Scales with DTE × lots and lot compounding by the same
#     ratio (round, clamped to 1 … day_lots−1); an entry sized to 1 lot cannot
#     split and runs single-TP (DIAG pp_skipped_lots).
#   * Fill conventions unchanged (monitor_position_day reused, not edited): T1 is
#     simply the target until it fills (gap → open). SL + T1 in one candle → SL
#     wins for the WHOLE position (house rule). T1 and T2 in one candle → both
#     fill (both are resting profit orders); a crossover due on the T1 candle
#     closes the rest at that candle's close — same ordering as the monitor.
#   * After T1, SL: KEEP the original level (default) or BE = move to entry.
#     A breakeven stop-out is labelled SL_BE and does NOT count toward the
#     SL-streak brake.
#   * Hedge at T1 (SELL): PROP (default) closes round(T1 × hedge/main) hedge lots
#     at T1, always leaving ≥1 hedge lot under the remaining short (spread stays a
#     spread); KEEP holds the full wing to the final exit.
#   * Exit reasons: TP1 (partial rows, main + hedge), TP2 (rest at T2), SL_BE.
#     Entries whose T1 lands on the wrong side of entry (e.g. SELL @ ₹35 with
#     T1 ₹40) run single-TP and are counted in pp_skipped_level.
#   * Rows: every partial is its own trade row, so Total Trades / win-rate count
#     legs, not positions. Compare runs on Net, DD and Net/DD.
#   * Config: s1.partial = {t1, lots, after_t1_sl: KEEP|BE, hedge: PROP|KEEP};
#     emitted only when ON. Diag keys pp_* only when ON (OFF diag key-identical).
#     CSV export now carries TMA_V2's DIAG section.
#
#  EDIT  backend/app/backtest/tma/tma_v2_engine.py (pure monitor_partial_day appended)
#        backend/app/backtest/tma/backtest_tma_v2_runner.py (7 anchored edits)
#        frontend/src/pages/Backtest.jsx (10) · backtest/BacktestQueue.jsx (1)
#        backtest/RunComparison.jsx (1) · backtest/SweepBuilder.jsx (1)
#  NEW   backend/app/backtest/tma/test_tma2_partial_tp.py
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Gates: py_compile, esbuild parse of the 4 JSX files, runner import, the new
#  suite (incl. OFF ≡ ORIGINAL against the .bak), test_tma_v2_dte_lots,
#  test_tma_v2_engine, test_lot_comp_runners. Any failure → every file restored.
#
# Usage: python3 apply_tma2_partial_tp.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script (the test file is carried inside it).

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "TMA2_PARTIAL_TP_20261014"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
R = "backend/app/backtest/tma/backtest_tma_v2_runner.py"
E = "backend/app/backtest/tma/tma_v2_engine.py"
B = "frontend/src/pages/Backtest.jsx"
Q = "frontend/src/pages/backtest/BacktestQueue.jsx"
C = "frontend/src/pages/backtest/RunComparison.jsx"
W = "frontend/src/pages/backtest/SweepBuilder.jsx"
T = "backend/app/backtest/tma/test_tma2_partial_tp.py"
PREREQ = {R: ["TMA2_DTE_LOTS_20260922", "LOT_COMP_EQ_20260924"], B: ["TMA2_DTE_LOTS_20260922"]}
SUITES = ["app/backtest/tma/test_tma2_partial_tp.py", "app/backtest/tma/test_tma_v2_dte_lots.py",
          "app/backtest/tma/test_tma_v2_engine.py", "app/backtest/engine/test_lot_comp_runners.py"]

EDITS = [
    # ───────────────────────────── runner ─────────────────────────────
    (R, '''        sl_tp_levels, warmup_bars, xover_exit_ts_v2,
    )''', '''        sl_tp_levels, warmup_bars, xover_exit_ts_v2,
        monitor_partial_day,   # ── TMA2_PARTIAL_TP_20261014 ──
    )''', 2),
    (E, '''            else:
                tp_level = ep * (1 + tpv / 100.0)

    return sl_level, tp_level''', '''            else:
                tp_level = ep * (1 + tpv / 100.0)

    return sl_level, tp_level


# ──────────────────────────────────────────────────────────────────────
# ── TMA2_PARTIAL_TP_20261014 ── two-target partial booking, one session.
# Pure; reuses monitor_position_day (NOT edited) for every fill decision.
# ──────────────────────────────────────────────────────────────────────
def monitor_partial_day(pos: Dict, opt_candles: List[dict],
                        xover_ts: Optional[int],
                        hard_close_ts: Optional[int],
                        hard_close_reason: str = "EOD",
                        mtm_cut_ts: Optional[int] = None
                        ) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Returns (t1_exit, final_exit); each None when it did not happen today.

    pos keys (on top of monitor_position_day's): pp_armed (T1 still open),
    pp_t1 (T1 premium level), pp_be (move SL to entry after T1), pp_booked
    (set here once T1 filled — persists across carried sessions).

    Phase 1 (armed): T1 is passed as THE target, so every convention is the
    monitor's own — gap through T1 fills at the open; SL + T1 in one candle
    → SL for the WHOLE position (t1_exit None, ambiguous set); xover/EOD/
    MTM_CUT before T1 close the whole position.
    On a T1 fill (exit_reason 'TP1', tp_price = T1, sl_price = SL at fill):
      * pos.pp_armed False, pos.pp_booked True; with pp_be the SL moves to
        entry (pp_be_on) — never evaluated on the T1 candle itself.
      * The T1 candle is checked for T2 (both are resting profit orders →
        both fill; gap through T2 → open) and then for a due crossover
        (close) — the monitor's own SL→TP→XOVER order.
      * Otherwise phase 2 runs the remainder from the next candle with T2;
        an MTM_CUT already evaluated at/before T1 is not re-evaluated.
    Remainder labels once booked: TP → TP2, SL after a BE move → SL_BE.
    The caller sizes the rows (T1 lots, hedge share) — this function does
    not know lots."""
    def _label(res):
        if res is None or not pos.get("pp_booked"):
            return res
        if res["exit_reason"] == "TP":
            return dict(res, exit_reason="TP2")
        if res["exit_reason"] == "SL" and pos.get("pp_be_on"):
            return dict(res, exit_reason="SL_BE")
        return res

    if not pos.get("pp_armed"):
        return None, _label(monitor_position_day(
            pos, opt_candles, xover_ts, hard_close_ts, hard_close_reason,
            mtm_cut_ts=mtm_cut_ts))
    t2 = pos.get("tp_price")
    pos["tp_price"] = pos["pp_t1"]                 # phase 1: T1 is the target
    try:
        r1 = monitor_position_day(pos, opt_candles, xover_ts, hard_close_ts,
                                  hard_close_reason, mtm_cut_ts=mtm_cut_ts)
    finally:
        pos["tp_price"] = t2
    if r1 is None or r1["exit_reason"] != "TP":
        return None, (dict(r1, tp_price=t2) if r1 is not None else None)

    t1 = dict(r1, exit_reason="TP1", tp_price=pos["pp_t1"])
    pos["pp_armed"], pos["pp_booked"] = False, True
    if pos.get("pp_be"):
        pos["sl_price"] = float(pos["entry_price"])
        pos["pp_be_on"] = True
    ts1 = int(r1["exit_ts"])
    short = str(pos.get("action", "BUY")).upper() == "SELL"
    c1 = next((c for c in opt_candles if int(c["ts"]) == ts1), None)
    base = {"side": pos["side"], "action": ("SELL" if short else "BUY"),
            "entry_ts": pos["entry_ts"], "entry_price": pos["entry_price"],
            "sl_price": pos.get("sl_price"), "tp_price": t2,
            "exit_ts": ts1, "ambiguous_fill": False}
    if c1 is not None and t2 is not None:
        o, h, l = float(c1["open"]), float(c1["high"]), float(c1["low"])
        if (l <= t2) if short else (h >= t2):
            gap = (o <= t2) if short else (o >= t2)
            return t1, dict(base, exit_price=(o if gap else float(t2)),
                            exit_reason="TP2")
    if c1 is not None and xover_ts is not None and ts1 >= xover_ts:
        return t1, dict(base, exit_price=float(c1["close"]),
                        exit_reason="XOVER")
    pos["watch_from"] = ts1 + 60                   # phase 2: the remainder
    mc2 = mtm_cut_ts if (mtm_cut_ts is not None and ts1 < mtm_cut_ts) else None
    return t1, _label(monitor_position_day(
        pos, opt_candles, xover_ts, hard_close_ts, hard_close_reason,
        mtm_cut_ts=mc2))
''', 1),
    (R, '''#   calendar is empty (base lots, counted in dte_unknown_days).
#
''', '''#   calendar is empty (base lots, counted in dte_unknown_days).
#
# ── TMA2_PARTIAL_TP_20261014 ── optional two-target partial booking on the
#   monitored leg: s1.partial = {t1, lots, after_t1_sl KEEP|BE, hedge PROP|KEEP}.
#   T1 (same unit as TP) books `lots`; the rest runs to T2 (= TP, 0 = ride) /
#   SL / XOVER / EOD. monitor_position_day is NOT edited: T1 is passed as the
#   target until it fills, then the remainder is monitored from the next candle
#   with T2 — except that T2 and a due crossover are also checked on the T1
#   candle itself (resting profit orders both fill; xover keeps monitor order).
#   SL + T1 in one candle → SL for the whole position (house rule). T1 lots and
#   hedge share scale with the day's lots; hedge PROP always leaves ≥1 wing lot.
#   Exit reasons TP1 / TP2 / SL_BE (SL_BE is not whipsaw evidence → no streak).
#   Absent / {} = byte-identical to before (no pp_* diag keys).
#
''', 1),
    (R, '''    dte_lot_mult = _parse_dlm(cfg.get("dte_lot_mult"))
''', '''    dte_lot_mult = _parse_dlm(cfg.get("dte_lot_mult"))
    # ── TMA2_PARTIAL_TP_20261014 ── two-target partial booking (absent = OFF)
    _pp_raw = s1_raw.get("partial") or {}
    pp_on = bool(_pp_raw) and bool(_pp_raw.get("enabled", True))
    pp_t1 = float(_pp_raw.get("t1") or 0) if pp_on else 0.0
    pp_lots = int(_pp_raw.get("lots") or 0) if pp_on else 0
    pp_be = str(_pp_raw.get("after_t1_sl") or "KEEP").upper() == "BE"
    pp_hedge_keep = str(_pp_raw.get("hedge") or "PROP").upper() == "KEEP"
    _pp_tu = str((s1_raw.get("main") or {}).get("tp_unit") or "PCT").upper()
''', 1),
    (R, '''    diag["max_loss_per_trade"] = max_loss_rs if max_loss_rs > 0 else "OFF"
''', '''    diag["max_loss_per_trade"] = max_loss_rs if max_loss_rs > 0 else "OFF"
    # ── TMA2_PARTIAL_TP_20261014 ── validate loudly + diag keys only when ON
    if pp_on:
        _sell = str(cfg.get("mode", "BUY")).upper() == "SELL"
        _t2 = float(main_cfg["tp_pct"] or 0)
        _why = None
        if pp_t1 <= 0:
            _why = "T1 must be > 0"
        elif not (1 <= pp_lots < int(main_cfg["lots"])):
            _why = (f"T1 lots must be 1-{int(main_cfg['lots']) - 1} "
                    f"(main leg has {int(main_cfg['lots'])}; the rest go to T2)")
        elif _t2 > 0:
            if _pp_tu == "ABS" and _sell:
                if pp_t1 <= _t2:
                    _why = (f"T1 level {pp_t1:g} must be ABOVE T2 {_t2:g} — a sold "
                            f"premium falls through the higher level first")
            elif pp_t1 >= _t2:
                _why = f"T1 {pp_t1:g} must be nearer than T2 {_t2:g} (smaller)"
        if _why:
            return {"run_id": None, "aborted": True,
                    "reason": f"TMA_V2 partial booking: {_why}",
                    "trades": [], "summary": _empty_summary(),
                    "config": cfg, "strategy_id": strategy_id}
        diag["partial_booking"] = (f"T1 {pp_t1:g}{_pp_tu} x{pp_lots}L / T2 "
                                   f"{_t2:g} / SL {'BE' if pp_be else 'KEEP'} / "
                                   f"hedge {'KEEP' if pp_hedge_keep else 'PROP'}")
        for _k in ("pp_armed", "pp_t1_hits", "pp_t2_hits", "pp_be_exits",
                   "pp_skipped_lots", "pp_skipped_level"):
            diag[_k] = 0
''', 1),
    (R, '''    # ── TMA2_XDAY_WARMUP ── rolling window of the prior WARMUP_DAYS sessions
''', '''    # ── TMA2_PARTIAL_TP_20261014 BEGIN ── the two-phase decision lives in the
    # pure engine (monitor_partial_day); this wrapper only books the T1 rows
    # (main + hedge share), shrinks the position and counts DIAG. A position
    # with no pp_* keys goes straight through monitor_position_day verbatim.
    def _pp_book_t1(pos: dict, t1: dict) -> None:
        pos_cond_ref["v"] = pos.get("cond", "S1")
        t1_l = int(pos["pp_t1_lots"])
        _one_row(symbol=pos["symbol"], side=pos["side"],
                 strike=pos.get("strike"), expiry=pos.get("expiry"),
                 direction=pos["action"], entry_ts=pos["entry_ts"],
                 entry_price=pos["entry_price"], sl=t1.get("sl_price"),
                 tp=t1.get("tp_price"), exit_ts=t1["exit_ts"],
                 exit_price=t1["exit_price"], reason="TP1",
                 qty=t1_l * LOT_SIZE, ambiguous=False)
        h_l = int(pos.get("pp_t1_h_lots") or 0)
        if pos["action"] == "SELL" and h_l > 0:
            _one_row(symbol=pos["h_symbol"], side=pos["side"],
                     strike=pos.get("h_strike"), expiry=pos.get("expiry"),
                     direction="BUY", entry_ts=pos["entry_ts"],
                     entry_price=pos["h_entry"], sl=None, tp=None,
                     exit_ts=t1["exit_ts"],
                     exit_price=_hedge_exit_price(pos, t1["exit_ts"]),
                     reason="TP1", qty=h_l * LOT_SIZE, ambiguous=False)
            pos["h_lots"] = int(pos["h_lots"]) - h_l
        pos["lots"] = int(pos["lots"]) - t1_l
        diag["pp_t1_hits"] += 1

    def _pp_monitor(pos, cands, xts, hard_ts, hard_reason, mtm_cut_ts=None):
        if not pp_on:
            return monitor_position_day(pos, cands, xts, hard_ts, hard_reason,
                                        mtm_cut_ts=mtm_cut_ts)
        t1, res = monitor_partial_day(pos, cands, xts, hard_ts, hard_reason,
                                      mtm_cut_ts=mtm_cut_ts)
        if t1 is not None:
            _pp_book_t1(pos, t1)    # BEFORE the caller emits the remainder
        if res is not None and res["exit_reason"] == "TP2":
            diag["pp_t2_hits"] += 1
        elif res is not None and res["exit_reason"] == "SL_BE":
            diag["pp_be_exits"] += 1
        return res
    # ── TMA2_PARTIAL_TP_20261014 END ──

    # ── TMA2_XDAY_WARMUP ── rolling window of the prior WARMUP_DAYS sessions
''', 1),
    (R, '''                res = monitor_position_day(
''', '''                res = _pp_monitor(   # ── TMA2_PARTIAL_TP_20261014 ──
''', 1),
    (R, '''            res = monitor_position_day(
''', '''            res = _pp_monitor(   # ── TMA2_PARTIAL_TP_20261014 ──
''', 1),
    (R, '''                            "h_side_is_call": leg_side == "CE"})
''', '''                            "h_side_is_call": leg_side == "CE"})
            # ── TMA2_PARTIAL_TP_20261014 ── arm T1 on this entry (same unit as
            # TP; scaled with the day's lots; wrong-side T1 → single-TP entry)
            if pp_on:
                _, t1_lvl = sl_tp_levels(ep, action, 0, pp_t1, _su, _tu)
                if day_lots_m < 2:
                    diag["pp_skipped_lots"] += 1
                elif t1_lvl is None or (tp_level is not None and (
                        (t1_lvl <= tp_level) if action == "SELL"
                        else (t1_lvl >= tp_level))):
                    diag["pp_skipped_level"] += 1
                else:
                    t1_l = int(round(pp_lots * day_lots_m / main_cfg["lots"]))
                    t1_l = max(1, min(t1_l, int(day_lots_m) - 1))
                    h_t1 = 0
                    if hedge is not None and not pp_hedge_keep:
                        _hl = int(pos.get("h_lots") or 0)
                        h_t1 = max(0, min(int(round(t1_l * _hl / day_lots_m)),
                                          _hl - 1))
                    pos.update({"pp_armed": True, "pp_t1": t1_lvl,
                                "pp_t1_lots": t1_l, "pp_t1_h_lots": h_t1,
                                "pp_be": pp_be})
                    diag["pp_armed"] += 1
''', 1),
    # ───────────────────────────── Backtest.jsx ─────────────────────────────
    (B, '''  const [tma2WingMode, setTma2WingMode] = useState(tma2Saved.wingMode ?? "synthetic");
''', '''  const [tma2WingMode, setTma2WingMode] = useState(tma2Saved.wingMode ?? "synthetic");
  // ── TMA2_PARTIAL_TP_20261014 ── two-target partial booking (OFF ≡ single TP)
  const [tma2Pp, setTma2Pp] = useState({ on: false, t1: 0, lots: 0, sl: "KEEP", hedge: "PROP", ...(tma2Saved.pp || {}) });
''', 1),
    (B, '''slUnit: tma2SlUnit, tpUnit: tma2TpUnit }));''', '''slUnit: tma2SlUnit, tpUnit: tma2TpUnit, pp: tma2Pp }));''', 1),
    (B, '''tma2WingMode, tma2SlUnit, tma2TpUnit]);''', '''tma2WingMode, tma2SlUnit, tma2TpUnit, tma2Pp]);''', 1),
    (B, '''tma2WingMode, tma2SlUnit, tma2TpUnit,   // ── TMA_V2 ──''', '''tma2WingMode, tma2SlUnit, tma2TpUnit, tma2Pp,   // ── TMA_V2 ── ── TMA2_PARTIAL_TP_20261014 ──''', 1),
    (B, '''          max_trades_per_day: Number(tma2MaxDay) || 0,
''', '''          max_trades_per_day: Number(tma2MaxDay) || 0,
          // ── TMA2_PARTIAL_TP_20261014 ── emitted only when ON (OFF runs keep the old shape)
          ...(tma2Pp.on ? { partial: { t1: Number(tma2Pp.t1) || 0, lots: Number(tma2Pp.lots) || 0,
            after_t1_sl: tma2Pp.sl === "BE" ? "BE" : "KEEP", hedge: tma2Pp.hedge === "KEEP" ? "KEEP" : "PROP" } } : {}),
''', 1),
    (B, '''`TP ${u(tma2TpUnit)} (0=off)`''', '''`${tma2Pp.on ? "T2" : "TP"} ${u(tma2TpUnit)} (0=off)`''', 1),
    (B, '''              {/* ── Capital ── SELL mode only (longs are premium-outlay) */}
''', '''              {/* ── TMA2_PARTIAL_TP_20261014 ── optional two-target partial booking */}
              <div style={tmaSecLabel}>Partial profit booking</div>
              <div style={tmaSecRow}>
                <Field label="Targets">
                  <select style={{ ...inputStyle, width: 190 }} value={tma2Pp.on ? "ON" : "OFF"} onChange={(e) => setTma2Pp((p) => ({ ...p, on: e.target.value === "ON" }))}>
                    <option value="OFF">OFF — single TP</option>
                    <option value="ON">ON — T1 + T2</option>
                  </select>
                </Field>
                {tma2Pp.on && (<>
                  <Field label={`T1 ${tma2TpUnit === "PTS" ? "₹ off" : tma2TpUnit === "ABS" ? "₹ lvl" : "%"}`}>
                    <input type="number" style={{ ...inputStyle, width: 90 }} value={tma2Pp.t1} onChange={(e) => setTma2Pp((p) => ({ ...p, t1: Number(e.target.value) }))}
                      title="First target, same unit as the TP column. It must be the NEARER target: for a SOLD leg with ₹ level, T1 is ABOVE T2 (e.g. T1 40, T2 10)." />
                  </Field>
                  <Field label="T1 lots">
                    <input type="number" min="1" style={{ ...inputStyle, width: 70 }} value={tma2Pp.lots} onChange={(e) => setTma2Pp((p) => ({ ...p, lots: Number(e.target.value) }))}
                      title="Lots booked at T1; the rest run to T2. Scales with DTE × lots and lot compounding by the same ratio." />
                  </Field>
                  <Field label="After T1, SL">
                    <select style={{ ...inputStyle, width: 180 }} value={tma2Pp.sl} onChange={(e) => setTma2Pp((p) => ({ ...p, sl: e.target.value }))}>
                      <option value="KEEP">Keep original SL</option>
                      <option value="BE">Move to entry (cost)</option>
                    </select>
                  </Field>
                  {tma2Mode === "SELL" && (
                    <Field label="Hedge at T1">
                      <select style={{ ...inputStyle, width: 180 }} value={tma2Pp.hedge} onChange={(e) => setTma2Pp((p) => ({ ...p, hedge: e.target.value }))}>
                        <option value="PROP">Close in proportion</option>
                        <option value="KEEP">Keep full hedge</option>
                      </select>
                    </Field>
                  )}
                </>)}
                <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 520, lineHeight: 1.45 }}>
                  {(() => {
                    if (!tma2Pp.on) return "One target for all lots (the TP column). Turn ON to book part of the position at T1 and run the rest to T2.";
                    const sell = tma2Mode === "SELL", L = Number(tma2Main.lots) || 0, l = Number(tma2Pp.lots) || 0;
                    const t1 = Number(tma2Pp.t1) || 0, t2 = Number(tma2Main.tp_pct) || 0;
                    const lvl = (v) => tma2TpUnit === "ABS" ? `₹${v}` : tma2TpUnit === "PTS" ? `entry ${sell ? "−" : "+"} ₹${v}` : `entry ${sell ? "−" : "+"}${v}%`;
                    let err = null;
                    if (t1 <= 0) err = "Set T1 (> 0).";
                    else if (!(l >= 1 && l < L)) err = `T1 lots must be 1–${Math.max(1, L - 1)} (the rest go to T2).`;
                    else if (t2 > 0 && (tma2TpUnit === "ABS" && sell ? t1 <= t2 : t1 >= t2))
                      err = tma2TpUnit === "ABS" && sell ? `T1 must be ABOVE T2 (₹${t2}) — the sold premium falls through the higher level first.` : `T1 must be nearer than T2 (${t2}).`;
                    if (err) return <span style={{ color: colors.loss, fontWeight: 600 }}>⚠ {err} The run will abort.</span>;
                    return `T1: ${sell ? "buy back" : "sell"} ${l} of ${L} lots at ${lvl(t1)} · T2: the other ${L - l} at ${t2 > 0 ? lvl(t2) : "— (no T2: they ride to crossover / SL / EOD)"} · after T1 the SL ${tma2Pp.sl === "BE" ? "moves to entry (exit tagged SL_BE)" : "stays where it was"}${sell ? ` · hedge ${tma2Pp.hedge === "KEEP" ? "held in full to the final exit" : "closed in proportion at T1 (≥1 lot kept)"}` : ""}. SL and T1 in one candle → SL for the whole position. Each partial is its own trade row (TP1 / TP2).`;
                  })()}
                </div>
              </div>

              {/* ── Capital ── SELL mode only (longs are premium-outlay) */}
''', 1),
    (B, '''    if (cfg.mode === "SELL") add("Hedge", `<${hd.premium_max} ${hd.lots}L`);''', '''    if (cfg.s1.partial) { const pp = cfg.s1.partial, s = mn.tp_unit === "PTS" ? "p" : mn.tp_unit === "ABS" ? "@" : "%";   // ── TMA2_PARTIAL_TP_20261014 ──
      add("T1", `${s === "@" ? "@" + pp.t1 : pp.t1 + s}×${pp.lots}L${pp.after_t1_sl === "BE" ? " BE" : ""}${cfg.mode === "SELL" && pp.hedge === "KEEP" ? " keepH" : ""}`); }
    if (cfg.mode === "SELL") add("Hedge", `<${hd.premium_max} ${hd.lots}L`);''', 1),
    (B, '''summary?.diag_emc || summary?.diag_vet);''', '''summary?.diag_emc || summary?.diag_vet || summary?.diag_tma2);   /* ── TMA2_PARTIAL_TP_20261014 ── TMA_V2 diag (incl. pp_* counters) in the CSV */''', 1),
    (B, '''const good = R === "TP" || R === "SIG_TP";''', '''const good = R === "TP" || R === "SIG_TP" || R === "TP1" || R === "TP2";   /* ── TMA2_PARTIAL_TP_20261014 ── */''', 2),
    # ───────────────────────────── queue / compare / sweep ─────────────────────────────
    (Q, '''TP${f(mn.tp_pct, mn.tp_unit)}`); }''', '''TP${f(mn.tp_pct, mn.tp_unit)}`);
      if (cfg.s1.partial) p.push(`T1${f(cfg.s1.partial.t1, mn.tp_unit)}×${cfg.s1.partial.lots}L${cfg.s1.partial.after_t1_sl === "BE" ? "-BE" : ""}`); }   // ── TMA2_PARTIAL_TP_20261014 ──''', 1),
    (C, '''return `<${c.premium_max} ${c.lots}L SL${f(c.sl_pct, c.sl_unit)} TP${f(c.tp_pct, c.tp_unit)}`; } },   // ── SLTP_UNITS ──
''', '''return `<${c.premium_max} ${c.lots}L SL${f(c.sl_pct, c.sl_unit)} TP${f(c.tp_pct, c.tp_unit)}`; } },   // ── SLTP_UNITS ──
  { key: "tma2_partial", label: "TMA2 partial T1", get: (r) => { const s1 = r.config?.ema4 ? r.config?.s1 : null; if (!s1) return null; const pp = s1.partial; if (!pp) return "OFF"; const u = s1.main?.tp_unit; const v = u === "ABS" ? `@${pp.t1}` : u === "PTS" ? `${pp.t1}p` : `${pp.t1}%`; return `${v}×${pp.lots}L${pp.after_t1_sl === "BE" ? " BE" : ""}${pp.hedge === "KEEP" ? " keepH" : ""}`; } },   // ── TMA2_PARTIAL_TP_20261014 ──
''', 1),
    (W, '''    apply: (c, v) => { if (c.s1?.main) c.s1.main.tp_pct = v; }, fmt: (v) => `TP${v}` },
''', '''    apply: (c, v) => { if (c.s1?.main) c.s1.main.tp_pct = v; }, fmt: (v) => `TP${v}` },
  // ── TMA2_PARTIAL_TP_20261014 ── T1 level/lots in ONE token (T1 in the run's TP
  // unit); keeps the form's after-T1 SL / hedge choices. OFF removes the block.
  { key: "tma2_partial", label: "Partial T1 (level/lots)", strategies: [TMA2],
    hint: "OFF, 40/5, 30/5, 40/3", parse: (tok) => {
      const t = tok.trim().toUpperCase();
      if (t === "OFF") return { v: null };
      const m = t.match(/^([0-9]*\\.?[0-9]+)\\s*\\/\\s*(\\d+)$/);
      return m && Number(m[1]) > 0 && Number(m[2]) >= 1
        ? { v: { t1: Number(m[1]), lots: Number(m[2]) } }
        : { err: `"${tok}" must be OFF or T1/LOTS like 40/5` };
    },
    apply: (c, v) => { if (!c.s1) return; if (!v) { delete c.s1.partial; return; }
      c.s1.partial = { after_t1_sl: "KEEP", hedge: "PROP", ...(c.s1.partial || {}), t1: v.t1, lots: v.lots }; },
    fmt: (v) => (v ? `T1@${v.t1}×${v.lots}L` : "T1off") },
''', 1),
]

NEW_FILES = {T: r'''# backend/app/backtest/tma/test_tma2_partial_tp.py
#
# ── TMA2_PARTIAL_TP_20261014 ── two-target partial booking on the TMA_V2 runner,
# end-to-end on the synthetic corpus of test_tma_v2_dte_lots (real epoch clock,
# real expiry calendar / symbol builder / CandleSource).
#
# Decisive checks:
#   * OFF ≡ ORIGINAL against the apply script's backup of the runner.
#   * KEEP-SL invariance: booking T1 must not move the REST of the position —
#     every baseline trade either reappears unchanged (T1 never filled) or splits
#     into a TP1 row + a remainder row with the baseline's exit time and price.
#
# Run from backend/:  python app/backtest/tma/test_tma2_partial_tp.py

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3]))          # backend/
sys.path.insert(0, str(HERE.parent))

from app.backtest.tma import backtest_tma_v2_runner as NEWR   # noqa: E402
from app.backtest.tma.tma_v2_engine import monitor_partial_day as MPD   # noqa: E402
import test_tma_v2_dte_lots as H                              # noqa: E402  (corpus builder)

FENCE = "TMA2_PARTIAL_TP_20261014"
LOT = 65
_TZ = timezone(timedelta(seconds=19800))
OK = [0]
FAILS = []


def check(name, cond):
    if cond:
        OK[0] += 1
        print("ok  ", name)
    else:
        FAILS.append(name)
        print("FAIL", name)


BASE = {"mode": "SELL", "trade_mode": "POSITIONAL", "xover_exit_ref": 55,
        "max_extension_pct": 0.8, "ema144_slope_gate": True,
        "s1": {"main": {"premium_max": 200, "lots": 10, "sl_pct": 12, "tp_pct": 10,
                        "sl_unit": "PCT", "tp_unit": "ABS"},
               "hedge": {"premium_max": 5, "lots": 10}}}


def run(mod, db, extra=None, partial=None, main=None):
    cfg = json.loads(json.dumps(BASE))
    cfg.update(extra or {})
    if main:
        cfg["s1"]["main"].update(main)
    if partial is not None:
        cfg["s1"]["partial"] = partial
    return mod.run_tma_v2_backtest(
        db_path=db, strategy_id="TMA_V2", underlying="NIFTY",
        date_from=date(2024, 1, 1), date_to=date(2024, 3, 31), config_override=cfg)


def day(ts):
    return datetime.fromtimestamp(ts, _TZ).date()


def by_pos(r):
    """{(entry_ts, direction): [rows]} — one position = one entry minute"""
    g = defaultdict(list)
    for t in r["trades"]:
        g[(t.entry_ts, t.direction)].append(t)
    return g


def split_ok(base, pp, lots_t1, rest_reason_map, hedge_t1=None):
    """KEEP-SL invariance on the monitored (SELL) leg + hedge accounting."""
    bg, pg = by_pos(base), by_pos(pp)
    booked = untouched = bad = 0
    for (ets, dr), brows in bg.items():
        if dr != "SELL":
            continue
        b = brows[0]
        rows = sorted(pg.get((ets, "SELL"), []), key=lambda t: (t.exit_ts, t.exit_reason != "TP1"))
        if len(rows) == 1 and asdict(rows[0]) == asdict(b):
            untouched += 1
            continue
        if len(rows) == 1:
            r0 = rows[0]
            if (r0.exit_ts, r0.exit_price, r0.exit_reason, r0.qty) == (b.exit_ts, b.exit_price, b.exit_reason, b.qty):
                untouched += 1          # same exit, only the ambiguity flag may differ
                continue
            bad += 1
            continue
        t1r, rest = rows
        ok = (t1r.exit_reason == "TP1" and t1r.qty == lots_t1 * LOT and rest.qty == b.qty - lots_t1 * LOT
              and t1r.exit_ts <= rest.exit_ts and rest.exit_ts == b.exit_ts
              and abs(rest.exit_price - b.exit_price) < 1e-6
              and rest.exit_reason == rest_reason_map.get(b.exit_reason, b.exit_reason)
              and abs(t1r.pnl - (t1r.entry_price - t1r.exit_price) * t1r.qty) < 2.0)   # charges model rounds to tick
        if hedge_t1 is not None:
            hr = sorted(pg.get((ets, "BUY"), []), key=lambda t: t.exit_ts)
            want = [hedge_t1 * LOT, 10 * LOT - hedge_t1 * LOT] if hedge_t1 else [10 * LOT]
            ok = ok and [h.qty for h in hr] == want
            if hedge_t1:
                ok = ok and hr[0].exit_reason == "TP1" and hr[0].exit_ts == t1r.exit_ts
        booked += ok
        bad += (not ok)
    return booked, untouched, bad


def main_tests(db):
    base = run(NEWR, db)
    check("fixture: baseline has SELL positions", sum(1 for t in base["trades"] if t.direction == "SELL") >= 8)

    # ── 1. OFF ≡ ORIGINAL ────────────────────────────────────────────────
    bak = HERE.parent / f"backtest_tma_v2_runner.py.bak-{FENCE}"
    if bak.exists():
        tmp = HERE.parent / "_tma2_orig_for_pp_test.py"
        tmp.write_text(bak.read_text())
        try:
            spec = importlib.util.spec_from_file_location("app.backtest.tma._tma2_orig_for_pp_test", tmp)
            OLDR = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(OLDR)
            for extra in ({}, {"trade_mode": "INTRADAY"}, {"mode": "BUY"},
                          {"dte_lot_mult": "4:2", "max_loss_per_trade": 5000, "sl_streak_count": 2}):
                a = run(OLDR, db, extra)
                for p in (None, {}):
                    b = run(NEWR, db, extra, partial=p)
                    tag = f"{extra or 'sealed-shape'} partial={p}"
                    check(f"OFF ≡ ORIGINAL rows {tag}",
                          [asdict(t) for t in a["trades"]] == [asdict(t) for t in b["trades"]] and len(a["trades"]) > 0)
                    check(f"OFF ≡ ORIGINAL summary+diag {tag}", a["summary"] == b["summary"])
        finally:
            tmp.unlink(missing_ok=True)
    else:
        print(f"note: {bak.name} not found — OFF≡ORIGINAL skipped (the apply script runs it)")
    check("OFF: no pp diag keys", not any(k.startswith("pp_") or k == "partial_booking"
                                          for k in base["summary"]["diag_tma2"]))

    # ── 2. ON, KEEP SL, PROP hedge: T1 ₹60 × 4 lots, T2 ₹10 ──────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4})
    dg = r["summary"]["diag_tma2"]
    t1rows = [t for t in r["trades"] if t.exit_reason == "TP1" and t.direction == "SELL"]
    check("ON: T1 fired on several positions", len(t1rows) >= 3 and dg["pp_t1_hits"] == len(t1rows))
    check("ON: TP1 fills at or through ₹60 (gap → open, never worse than T1)",
          all(t.exit_price <= 60 + 1e-9 and t.tp == 60 for t in t1rows))
    bk, un, bad = split_ok(base, r, 4, {"TP": "TP2"}, hedge_t1=4)
    check(f"ON/KEEP: the rest exits exactly like the baseline (booked {bk}, untouched {un}, bad {bad})",
          bad == 0 and bk == len(t1rows) and bk + un == sum(1 for t in base["trades"] if t.direction == "SELL"))
    check("ON: per-position SELL qty always sums to 10 lots",
          all(sum(t.qty for t in rows) == 10 * LOT for (e, d_), rows in by_pos(r).items() if d_ == "SELL"))
    check("ON: per-position hedge qty always sums to 10 lots",
          all(sum(t.qty for t in rows) == 10 * LOT for (e, d_), rows in by_pos(r).items() if d_ == "BUY"))
    tp2 = [t for t in r["trades"] if t.exit_reason == "TP2" and t.direction == "SELL"]
    check("ON: TP2 rows exist and are counted", tp2 and dg["pp_t2_hits"] == len(tp2))
    check("ON: a plain 'TP' only on positions where T1 was never armed/hit",
          all(len(by_pos(r)[(t.entry_ts, "SELL")]) == 1 for t in r["trades"] if t.exit_reason == "TP" and t.direction == "SELL"))
    check("ON: pp_armed ≥ T1 hits, diag string present",
          dg["pp_armed"] >= dg["pp_t1_hits"] and "T1 60ABS x4L" in dg["partial_booking"])
    check("ON: a T1 booked one day with the rest carried overnight exists",
          any(day(t.exit_ts) < day(u.exit_ts) for t in t1rows for u in by_pos(r)[(t.entry_ts, "SELL")] if u is not t))
    check("ON: entries identical to baseline (booking never changes the slot)",
          sorted({t.entry_ts for t in base["trades"]}) == sorted({t.entry_ts for t in r["trades"]}))

    # ── 3. T1 next to T2 → same-candle T1+T2 must be handled ─────────────
    r = run(NEWR, db, partial={"t1": 11, "lots": 5})
    bk, un, bad = split_ok(base, r, 5, {"TP": "TP2"}, hedge_t1=5)
    check(f"T1 11 / T2 10: invariance holds (booked {bk}, bad {bad}) — same-candle cases are unit-tested below",
          bad == 0 and bk >= 1)

    # ── 4. BE: SL moves to entry after T1 ────────────────────────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4, "after_t1_sl": "BE"})
    dg = r["summary"]["diag_tma2"]
    g = by_pos(r)
    rests = [rows for (e, d_), rows in g.items() if d_ == "SELL" and len(rows) == 2]
    rest_rows = [max(rows, key=lambda t: (t.exit_ts, t.exit_reason != "TP1")) for rows in rests]
    check("BE: remainder rows carry SL = entry", rest_rows and all(abs(t.sl - t.entry_price) < 0.01 for t in rest_rows))
    check("BE: no plain 'SL' after a booked T1 — it is SL_BE",
          all(t.exit_reason != "SL" for t in rest_rows))
    be = [t for t in rest_rows if t.exit_reason == "SL_BE"]
    check(f"BE: SL_BE exits ({len(be)}) fill at/through entry and match the diag count",
          all(t.exit_price >= t.entry_price - 1e-9 for t in be) and dg["pp_be_exits"] == len(be))

    # ── 5. hedge KEEP: wing held in full ─────────────────────────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4, "hedge": "KEEP"})
    bk, un, bad = split_ok(base, r, 4, {"TP": "TP2"}, hedge_t1=0)
    check(f"hedge KEEP: no TP1 hedge rows, full wing to the final exit (bad {bad})",
          bad == 0 and bk >= 3 and not any(t.exit_reason == "TP1" and t.direction == "BUY" for t in r["trades"]))

    # ── 6. scaling with DTE × lots ───────────────────────────────────────
    r = run(NEWR, db, {"dte_lot_mult": "4:2"}, partial={"t1": 60, "lots": 4})
    g = by_pos(r)
    big = [rows for (e, d_), rows in g.items() if d_ == "SELL" and sum(t.qty for t in rows) == 20 * LOT and len(rows) == 2]
    check("DTE 4:2: 20-lot entries split 8 / 12", big and all(sorted(t.qty for t in rows) == [8 * LOT, 12 * LOT] for rows in big))

    # ── 7. BUY mode, % units ─────────────────────────────────────────────
    r = run(NEWR, db, {"mode": "BUY"}, partial={"t1": 20, "lots": 3},
            main={"premium_max": 150, "sl_pct": 30, "tp_pct": 60, "tp_unit": "PCT"})
    t1b = [t for t in r["trades"] if t.exit_reason == "TP1"]
    check("BUY: T1 rows at ≥ entry × 1.2, 3 lots, no hedge rows",
          t1b and all(t.direction == "BUY" and t.qty == 3 * LOT and t.exit_price >= t.entry_price * 1.2 - 0.01 for t in t1b)
          and all(t.direction == "BUY" for t in r["trades"]))

    # ── 8. T2 = 0: the rest rides (no TP2 anywhere) ──────────────────────
    r = run(NEWR, db, partial={"t1": 60, "lots": 4}, main={"tp_pct": 0})
    check("T2 off: T1 books, remainder never exits on a target",
          any(t.exit_reason == "TP1" for t in r["trades"]) and not any(t.exit_reason in ("TP", "TP2") for t in r["trades"]))

    # ── 9. loud aborts ───────────────────────────────────────────────────
    for p, why in (({"t1": 60, "lots": 10}, "lots"), ({"t1": 8, "lots": 4}, "ABOVE"), ({"t1": 0, "lots": 4}, "> 0")):
        a = run(NEWR, db, partial=p)
        check(f"abort: {p} → {why}", a.get("aborted") and why in a.get("reason", ""))
    a = run(NEWR, db, {"mode": "BUY"}, partial={"t1": 70, "lots": 3}, main={"tp_pct": 60, "tp_unit": "PCT"})
    check("abort: BUY % T1 beyond T2", a.get("aborted") and "nearer" in a.get("reason", ""))


# ── pure monitor_partial_day units (hand-built candles; every branch) ────
D0 = 1704252600          # 2024-01-03 09:00 IST on the real epoch clock
def _c(i, o, h, l, c):
    return {"ts": D0 + 60 * i, "open": o, "high": h, "low": l, "close": c}


def _pos(action="SELL", sl=120.0, tp=10.0, t1=60.0, be=False):
    return {"side": "CE", "action": action, "entry_ts": D0 - 60, "entry_price": 100.0,
            "sl_price": sl, "tp_price": tp, "watch_from": D0, "last_close": 100.0,
            "last_ts": D0 - 60, "pp_armed": True, "pp_t1": t1, "pp_be": be}


def unit_tests():
    # T1 then later T2
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 50, 55), _c(1, 55, 56, 30, 31), _c(2, 31, 32, 5, 6)], None, None)
    check("unit: T1 at level, T2 later at level",
          t1["exit_reason"] == "TP1" and t1["exit_price"] == 60 and t1["exit_ts"] == D0
          and r["exit_reason"] == "TP2" and r["exit_price"] == 10 and r["exit_ts"] == D0 + 120 and p["pp_booked"])
    # T1 + T2 in ONE candle → both
    t1, r = MPD(_pos(), [_c(0, 95, 96, 5, 7)], None, None)
    check("unit: T1+T2 same candle → both fill at their levels, same minute",
          t1["exit_price"] == 60 and r["exit_reason"] == "TP2" and r["exit_price"] == 10 and r["exit_ts"] == t1["exit_ts"])
    # gap through T1 only / through both
    t1, r = MPD(_pos(), [_c(0, 40, 45, 35, 38)], None, None)
    check("unit: gap through T1 fills at open, rest survives", t1["exit_price"] == 40 and r is None)
    t1, r = MPD(_pos(), [_c(0, 8, 9, 6, 7)], None, None)
    check("unit: gap through T1 AND T2 → both at the open", t1["exit_price"] == 8 and r["exit_price"] == 8 and r["exit_reason"] == "TP2")
    # SL + T1 same candle → SL whole, ambiguous
    p = _pos(); t1, r = MPD(p, [_c(0, 100, 125, 55, 90)], None, None)
    check("unit: SL + T1 one candle → whole position SL, ambiguous, T1 not booked",
          t1 is None and r["exit_reason"] == "SL" and r["ambiguous_fill"] and r["tp_price"] == 10 and not p.get("pp_booked"))
    # BE: not on the T1 candle, fires after; gap above entry fills at open
    p = _pos(be=True); t1, r = MPD(p, [_c(0, 95, 101, 55, 70), _c(1, 70, 99, 65, 98)], None, None)
    check("unit: BE never evaluated on the T1 candle itself; SL moved to entry",
          t1["exit_price"] == 60 and t1["sl_price"] == 120 and r is None and p["sl_price"] == 100.0)
    t1, r = MPD(p, [_c(2, 99, 100.5, 97, 100)], None, None)
    check("unit: BE stop next candle → SL_BE at entry", t1 is None and r["exit_reason"] == "SL_BE" and r["exit_price"] == 100.0)
    p = _pos(be=True); MPD(p, [_c(0, 95, 96, 55, 70)], None, None)
    t1, r = MPD(p, [_c(1, 105, 106, 104, 105)], None, None)
    check("unit: BE gap above entry fills at the open (105)", r["exit_reason"] == "SL_BE" and r["exit_price"] == 105)
    # KEEP: original SL stays, labelled plain SL
    p = _pos(); MPD(p, [_c(0, 95, 96, 55, 70)], None, None)
    t1, r = MPD(p, [_c(1, 110, 121, 109, 118)], None, None)
    check("unit: KEEP → remainder SL at the original level, plain 'SL'", r["exit_reason"] == "SL" and r["exit_price"] == 120)
    # XOVER due on the T1 candle → rest at that close
    t1, r = MPD(_pos(), [_c(0, 95, 96, 55, 58)], D0, None)
    check("unit: crossover due on the T1 candle → T1 at level, rest XOVER at close",
          t1["exit_price"] == 60 and r["exit_reason"] == "XOVER" and r["exit_price"] == 58)
    # XOVER before T1 → whole position
    t1, r = MPD(_pos(), [_c(0, 95, 96, 80, 85), _c(1, 85, 86, 50, 52)], D0, None)
    check("unit: crossover before T1 → whole position XOVER", t1 is None and r["exit_reason"] == "XOVER" and r["exit_price"] == 85)
    # carry: T1 day 1, armed off; day 2 plain TP → TP2
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 55, 58)], None, None)
    p["watch_from"] = 0
    t1b, r2 = MPD(p, [_c(1440, 30, 31, 9, 9.5)], None, None)
    check("unit: carried remainder hits T2 next session → TP2, no second T1",
          t1 is not None and r is None and t1b is None and r2["exit_reason"] == "TP2" and r2["exit_price"] == 10)
    # hard close after T1
    t1, r = MPD(_pos(), [_c(0, 95, 96, 55, 58), _c(1, 58, 59, 50, 52)], None, D0 + 120, "EOD")
    check("unit: hard close after T1 → rest EOD at last close", r["exit_reason"] == "EOD" and r["exit_price"] == 52)
    # MTM cut: already evaluated (disarmed) before T1 → not re-run; T1 before the cut → still evaluated
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 80, 85), _c(1, 85, 86, 55, 101), _c(2, 101, 102, 100.5, 101)], None, None, mtm_cut_ts=D0)
    check("unit: MTM cut disarmed before T1 is not re-evaluated afterwards", t1 is not None and r is None)
    p = _pos(); t1, r = MPD(p, [_c(0, 95, 96, 55, 101), _c(1, 101, 103, 100.5, 102), _c(2, 102, 103, 101, 102)], None, None, mtm_cut_ts=D0 + 120)
    check("unit: T1 before the cut → cut still applies to the rest (MTM_CUT)", t1 is not None and r["exit_reason"] == "MTM_CUT")
    # BUY mirror
    p = _pos("BUY", sl=70.0, tp=200.0, t1=130.0)
    t1, r = MPD(p, [_c(0, 105, 135, 104, 133), _c(1, 133, 205, 132, 201)], None, None)
    check("unit: BUY mirror — T1 above, T2 above", t1["exit_price"] == 130 and r["exit_reason"] == "TP2" and r["exit_price"] == 200)
    # unarmed = monitor verbatim
    from app.backtest.tma.tma_v2_engine import monitor_position_day as MPOS
    cs = [_c(0, 95, 96, 55, 58), _c(1, 58, 59, 5, 6)]
    q1 = {k: v for k, v in _pos().items() if not k.startswith("pp_")}
    q2 = dict(q1)
    check("unit: no pp keys ≡ monitor_position_day", MPD(q1, cs, None, None) == (None, MPOS(q2, cs, None, None)))


if __name__ == "__main__":
    unit_tests()
    with tempfile.TemporaryDirectory() as td:
        dbp = os.path.join(td, "bt.db")
        print("building synthetic corpus…")
        H.build_corpus(dbp)
        main_tests(dbp)
    if FAILS:
        print(f"\n{len(FAILS)} FAILURES: {FAILS}")
        sys.exit(1)
    print(f"\nALL {OK[0]} TMA_V2 PARTIAL-TP CHECKS PASSED")
'''}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        staged[rel] = txt
    for rel, fences in PREREQ.items():
        for f in fences:
            if f not in staged[rel]:
                die(f"prerequisite fence {f} not found in {rel}")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — mixed state; remove it (or git checkout) and re-run")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in [(R, staged[R]), (E, staged[E])] + list(NEW_FILES.items()):
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print("  ✓ py_compile (engine + runner + test)")
        esb = next((str(c) for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild")
                    if c.exists()), None) or shutil.which("esbuild")
        jsx = [B, Q, C, W]
        if esb:
            for rel in jsx:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print("  ✓ esbuild parsed Backtest / BacktestQueue / RunComparison / SweepBuilder")
        else:
            print("  ! esbuild not found — parse gate skipped (the frontend build will check)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    for rel, txt in NEW_FILES.items():
        (repo / rel).write_text(txt, encoding="utf-8")
        print(f"  ✓ created {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c", "import app.backtest.tma.backtest_tma_v2_runner; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("TMA_V2 runner no longer imports")
        print("  ✓ TMA_V2 runner imports")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                print(f"  ! {suite} not found — skipped")
                continue
            print(f"  … {Path(suite).name} (builds a synthetic corpus — a few minutes; all four suites ≈ 8–12 min)")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=3600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "FAIL " in r.stdout or "\nFAIL" in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    for rel in targets + list(NEW_FILES):
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied. Rebuild (./desktop/build-scalp.sh both) → Backtest → TMA_V2 → Legs →"
          f" 'Partial profit booking'. Backtest only; paper/live unchanged.")


if __name__ == "__main__":
    main()
