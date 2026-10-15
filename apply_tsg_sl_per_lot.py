#!/usr/bin/env python3
# apply_tsg_sl_per_lot.py — fence TSG_SL_PER_LOT_20261015
#
# TSG_V1: the MTM SL is entered PER LOT on both the Settings page (paper/live)
# and the Backtest page. Set ₹3,500 once; the basket stop is computed from the
# lots the day actually trades.
#
# LIVE / PAPER
#   * New config key `mtm_sl_per_lot` (₹, 0 = off). Day SL = per-lot × the day's
#     lots — Lots on a normal day, Expiry-Day Lots on the contract's expiry day.
#     This replaces the LD5a ratio for the SL (same numbers, no base to keep in
#     sync). Target is NOT changed: still ₹ basket × lots ratio (LD5a).
#   * Hard stop (× 1.05) rides on the per-lot SL as before.
#   * Migration (strategy_loader, read-time, file untouched): a file saved before
#     this patch has only `mtm_sl` (the total at its own `lots`) → per-lot is
#     derived once as mtm_sl ÷ lots, so today's stop is unchanged to the rupee
#     (35,000 @ 10 → 3,500/lot). The first Save on Settings writes the key.
#   * `mtm_sl` stays in the config as a READ-ONLY mirror (= per-lot × lots,
#     recomputed on every load) so the Dashboard row / panel / any older reader
#     keep showing the right total. The engine never reads it once per-lot exists.
#   * Settings: "MTM SL ₹ per lot" field with a live preview of today's and the
#     expiry-day total; Expiry-Day Lots helper updated. Panel shows "(₹x/lot)".
#   * A session already open when the app restarts keeps the SL it entered with
#     (persisted in the day core, unchanged behaviour).
#
# BACKTEST
#   * Form field "MTM SL ₹ per lot" (label shows the resulting total at the SELL
#     legs' lots). Emits `mtm_sl_per_lot` and NO `mtm_sl`. Saved form values are
#     converted once (17,500 @ 5 lots → 3,500).
#   * Runner: `mtm_sl_per_lot` present → each day's SL = per-lot × that day's
#     SELL-leg lots AFTER DTE × lots and lot compounding (the 'fixed ₹' toggle
#     no longer freezes the SL — it still governs target/trail/bank). Absent →
#     the old `mtm_sl` total path, byte-identical (archived runs reproduce).
#     Note the one deliberate difference: with DTE × lots, the legacy total never
#     scaled with the scaled lots; per-lot does (matches live).
#   * Lots are counted on the SELL legs (max of L1/L2) — wings don't add risk.
#   * Run card / Queue chip / Compare column show "₹x/lot"; Trade Replay draws
#     the level from the trade's own SELL quantity.
#   * Sweep: new axis "MTM SL ₹/lot" (tsg_mtm_sl_lot). The old "MTM SL ₹" axis
#     keeps its key and meaning (basket total) so saved sweeps still mean what
#     they meant — it removes the per-lot key from that combo.
#
#  EDIT  backend/app/engine/tsg/tsg_live_core.py (helper) · tsg_manager.py (2)
#        backend/app/config/strategy_loader.py (default + migration)
#        backend/app/api/tsg_v1_state_routes.py (1)
#        backend/app/backtest/tsg/backtest_tsg_runner.py (6)
#        backend/app/backtest/replay/overlays.py (2)
#        frontend/src/pages/Settings.jsx (4) · Backtest.jsx (7)
#        frontend/src/pages/backtest/{BacktestQueue,RunComparison,SweepBuilder}.jsx
#        frontend/src/strategies/tsg_v1/TSGV1Panel.jsx
#  NEW   backend/app/engine/tsg/test_tsg_sl_per_lot.py
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Gates: py_compile, esbuild parse of the 6 JSX files, imports, the new suite
#  (legacy ≡ ORIGINAL runner via the .bak, per-lot ≡ legacy at equal lots, DTE,
#  compounding, parallel, loader migration, manager entry, overlay), then
#  test_tsg_runner / test_tsg_live_core / test_trade_replay. Any failure → every
#  file restored.
#
# Usage: python3 apply_tsg_sl_per_lot.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script (the test file is carried inside it).

from __future__ import annotations
import argparse, os, py_compile, re, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "TSG_SL_PER_LOT_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
CORE = "backend/app/engine/tsg/tsg_live_core.py"
MGR = "backend/app/engine/tsg/tsg_manager.py"
LDR = "backend/app/config/strategy_loader.py"
ST = "backend/app/api/tsg_v1_state_routes.py"
R = "backend/app/backtest/tsg/backtest_tsg_runner.py"
OV = "backend/app/backtest/replay/overlays.py"
SJ = "frontend/src/pages/Settings.jsx"
B = "frontend/src/pages/Backtest.jsx"
Q = "frontend/src/pages/backtest/BacktestQueue.jsx"
C = "frontend/src/pages/backtest/RunComparison.jsx"
W = "frontend/src/pages/backtest/SweepBuilder.jsx"
P = "frontend/src/strategies/tsg_v1/TSGV1Panel.jsx"
T = "backend/app/engine/tsg/test_tsg_sl_per_lot.py"
PY = [CORE, MGR, LDR, ST, R, OV]
JSX = [SJ, B, Q, C, W, P]
PREREQ = {MGR: ["TSG_HARD_STOP_20260911"], R: ["LOT_COMP_20260924", "DTE_LOT_MULT_20260911", "TSG_BANK_FIX1_20260911"],
          SJ: ["TSG_HARD_STOP_20260911"], OV: ["def _tsg(ctx)"], B: ["DTE_LOT_MULT_20260911"]}
SUITES = ["app/engine/tsg/test_tsg_sl_per_lot.py", "app/backtest/tsg/test_tsg_runner.py",
          "app/engine/tsg/test_tsg_live_core.py", "app/backtest/replay/test_trade_replay.py"]

EDITS = [
    # ───────────────────────────── live core ─────────────────────────────
    (CORE, '''    if is_expiry_day and expiry_lots:
        return int(expiry_lots)
    return int(base_lots)
''', '''    if is_expiry_day and expiry_lots:
        return int(expiry_lots)
    return int(base_lots)


# ── TSG_SL_PER_LOT_20261015 ── the MTM SL is entered PER LOT.
def resolve_mtm_sl(cfg: dict, day_lots: int, base_lots: int) -> float:
    """The day's basket MTM SL in ₹ (positive; 0 = off).

    `mtm_sl_per_lot` present (any number, 0 included) → per-lot × day_lots.
    It always wins over `mtm_sl`, so a stale basket total can never leak back.
    Absent / blank (config never migrated) → the legacy LD5a rule, unchanged:
    mtm_sl × (day_lots ÷ base_lots)."""
    raw = cfg.get("mtm_sl_per_lot")
    if raw is not None and str(raw).strip() != "":
        return abs(float(raw or 0)) * max(0, int(day_lots))
    total = abs(float(cfg.get("mtm_sl", 35000) or 0))
    return total * ((day_lots / base_lots) if base_lots > 0 else 1.0)
''', 1),
    # ───────────────────────────── manager ─────────────────────────────
    (MGR, '''    TsgDayCore, TsgLeg, resolve_lots,
''', '''    TsgDayCore, TsgLeg, resolve_lots,
    resolve_mtm_sl,   # ── TSG_SL_PER_LOT_20261015 ──
''', 1),
    (MGR, '''            mtm_sl=abs(float(cfg.get("mtm_sl", 35000) or 0)) * risk_scale,
''', '''            # ── TSG_SL_PER_LOT_20261015 ── per-lot × the day's lots (expiry lots
            # included); a never-migrated config keeps the LD5a ratio rule.
            mtm_sl=resolve_mtm_sl(cfg, lots, base_lots),
''', 1),
    (MGR, '''            entry_date=today,
        )
        planned = core.plan_entry(''', '''            entry_date=today,
        )
        write_audit_log(   # ── TSG_SL_PER_LOT_20261015 ──
            f"[TSG][SL] day SL ₹{core.mtm_sl:,.0f} = "
            + (f"₹{abs(float(cfg.get('mtm_sl_per_lot') or 0)):,.0f}/lot × {lots} lots"
               if cfg.get("mtm_sl_per_lot") is not None and str(cfg.get("mtm_sl_per_lot")).strip() != ""
               else f"legacy total × {risk_scale:.2f}")
            + (" [EXPIRY-DAY]" if is_expiry else ""))
        planned = core.plan_entry(''', 1),
    # ───────────────────────────── loader ─────────────────────────────
    (LDR, '''        "mtm_sl": 3500,
        "mtm_sl_hard_mult": 1.05,''', '''        "mtm_sl_per_lot": 3500,   # ── TSG_SL_PER_LOT_20261015 ── ₹ PER LOT; day SL = this × the day's lots (0 = off)
        "mtm_sl": 35000,          # read-only mirror = per-lot × lots (recomputed on every load; never read by the engine)
        "mtm_sl_hard_mult": 1.05,''', 1),
    (LDR, '''    # BACKTEST override (no-op when unset) — LAST step so it wins over disk.
    return _apply_bt_override(strategy_id, merged), False''', '''    # --------------------------------------------------
    # ── TSG_SL_PER_LOT_20261015 ── MTM SL is entered PER LOT.
    # A file saved before this patch holds only the basket total `mtm_sl`
    # at its own `lots`: derive per-lot from it (read-time, file untouched)
    # so today's stop is unchanged to the rupee. `mtm_sl` is then rewritten
    # as a read-only mirror (per-lot × lots) for readers showing the total.
    # --------------------------------------------------
    if strategy_id == "TSG_V1":
        try:
            _lots = max(1, int(merged.get("lots") or 1))
            if (str(cfg.get("mtm_sl_per_lot") if cfg.get("mtm_sl_per_lot") is not None else "").strip() == ""
                    and str(cfg.get("mtm_sl") if cfg.get("mtm_sl") is not None else "").strip() != ""):
                merged["mtm_sl_per_lot"] = round(abs(float(cfg.get("mtm_sl") or 0)) / _lots, 2)
            _pl = abs(float(merged.get("mtm_sl_per_lot") or 0))
            merged["mtm_sl_per_lot"] = _pl
            merged["mtm_sl"] = round(_pl * _lots, 2)
        except Exception as e:
            write_audit_log(f"[CONFIG][TSG_SL_PER_LOT] migration skipped: {e!r}")

    # BACKTEST override (no-op when unset) — LAST step so it wins over disk.
    return _apply_bt_override(strategy_id, merged), False''', 1),
    # ───────────────────────────── state route ─────────────────────────────
    (ST, '''        "mtm_sl": cfg.get("mtm_sl", 35000),
''', '''        "mtm_sl": cfg.get("mtm_sl", 35000),
        "mtm_sl_per_lot": cfg.get("mtm_sl_per_lot"),   # ── TSG_SL_PER_LOT_20261015 ──
''', 1),
    # ───────────────────────────── runner ─────────────────────────────
    (R, '''      mtm_sl_basis     "DAILY"|"POSITION" (default DAILY); DAILY = day
''', '''      mtm_sl_per_lot   float ₹ PER LOT (── TSG_SL_PER_LOT_20261015 ──).
                       Present → each day's SL = this × that day's SELL-leg
                       lots (after DTE × lots and lot compounding; the
                       'fixed ₹' toggle does not freeze it) and mtm_sl is
                       ignored. 0 = off. Absent → mtm_sl, unchanged.
      mtm_sl_basis     "DAILY"|"POSITION" (default DAILY); DAILY = day
''', 1),
    (R, '''    mtm_sl = abs(float(cfg.get("mtm_sl", 0) or 0))   # sign-tolerant: -2500 ≡ 2500
''', '''    mtm_sl = abs(float(cfg.get("mtm_sl", 0) or 0))   # sign-tolerant: -2500 ≡ 2500
    # ── TSG_SL_PER_LOT_20261015 ── per-lot SL wins over the basket total
    _sl_pl_raw = cfg.get("mtm_sl_per_lot")
    sl_per_lot = (None if _sl_pl_raw is None or str(_sl_pl_raw).strip() == ""
                  else abs(float(_sl_pl_raw or 0)))

    def _tsg_sell_lots(_legs) -> int:
        return max([int(l.get("lots") or 0) for l in _legs
                    if str(l.get("action", "")).upper() == "SELL"] or [0])
''', 1),
    (R, '''    _comp_rs0 = (mtm_target, mtm_sl, mtm_trail_arm, mtm_trail_giveback, mtm_bank_target)
''', '''    if sl_per_lot is not None:   # ── TSG_SL_PER_LOT_20261015 ── base-day total (reporting)
        mtm_sl = sl_per_lot * _tsg_sell_lots(legs_cfg)
    _comp_rs0 = (mtm_target, mtm_sl, mtm_trail_arm, mtm_trail_giveback, mtm_bank_target)
''', 1),
    (R, '''            mtm_bank_target = _comp.scale_rs(_comp_rs0[4], d)
''', '''            mtm_bank_target = _comp.scale_rs(_comp_rs0[4], d)
        # ── TSG_SL_PER_LOT_20261015 ── the SL follows the lots this day really
        # trades (DTE × lots + compounding above) — per-lot × SELL lots.
        if sl_per_lot is not None:
            mtm_sl = sl_per_lot * _tsg_sell_lots(_legs_day)
''', 1),
    (R, '''            "skew_mult": skew_mult, "short_skew_mult": short_skew_mult,
        }
        for k, v in merged_diag.items():''', '''            "skew_mult": skew_mult, "short_skew_mult": short_skew_mult,
        }
        if sl_per_lot is not None:   # ── TSG_SL_PER_LOT_20261015 ──
            base_diag["mtm_sl_per_lot"] = sl_per_lot
        for k, v in merged_diag.items():''', 1),
    (R, '''        "mtm_target": mtm_target, "mtm_sl": mtm_sl,
        "mtm_sl_basis": mtm_sl_basis,   # ── TSG_MTM_BASIS_20260821 ──
        "iv_sl_pct": iv_sl_pct, "iv_sl_delta_pts": iv_sl_delta_pts,''', '''        "mtm_target": mtm_target, "mtm_sl": mtm_sl,
        **({"mtm_sl_per_lot": sl_per_lot} if sl_per_lot is not None else {}),   # ── TSG_SL_PER_LOT_20261015 ──
        "mtm_sl_basis": mtm_sl_basis,   # ── TSG_MTM_BASIS_20260821 ──
        "iv_sl_pct": iv_sl_pct, "iv_sl_delta_pts": iv_sl_delta_pts,''', 1),
    # ───────────────────────────── replay overlay ─────────────────────────────
    (OV, '''    mult, how = _rs_mult(ctx, cfg, base_lots, d, qty_ratio)
    lv = []
    if mtm_sl:
        lv.append({"type": "level", "label": f"SL −{mtm_sl * mult:,.0f}", "value": -mtm_sl * mult, "color": "loss"})
        hm = float(cfg.get("mtm_sl_hard_mult", 0) or 0)
        if hm >= 1.0:
            lv.append({"type": "level", "label": "hard stop (live only)", "value": -mtm_sl * mult * hm,''', '''    mult, how = _rs_mult(ctx, cfg, base_lots, d, qty_ratio)
    # ── TSG_SL_PER_LOT_20261015 ── per-lot SL: level from this trade's own
    # SELL lots (already includes DTE × lots / compounding) — no ratio.
    sl_mult, sl_brief = mult, f"{mtm_sl:,.0f}"
    _pl = cfg.get("mtm_sl_per_lot")
    if _pl is not None and str(_pl).strip() != "":
        _sell = [int(t.get("qty") or 0) // lot for t in ctx.legs
                 if str(t.get("direction") or "").upper() == "SELL" or "·SELL" in str(t.get("condition") or "")]
        _n = max(_sell or [0]) or max([int(x.get("lots") or 0) for x in legs_cfg
                                       if str(x.get("action") or "").upper() == "SELL"] or [0])
        mtm_sl, sl_mult = abs(float(_pl or 0)) * _n, 1.0
        sl_brief = f"{abs(float(_pl or 0)):,.0f}/lot"
    lv = []
    if mtm_sl:
        lv.append({"type": "level", "label": f"SL −{mtm_sl * sl_mult:,.0f}", "value": -mtm_sl * sl_mult, "color": "loss"})
        hm = float(cfg.get("mtm_sl_hard_mult", 0) or 0)
        if hm >= 1.0:
            lv.append({"type": "level", "label": "hard stop (live only)", "value": -mtm_sl * sl_mult * hm,''', 1),
    (OV, '''                            f"MTM SL {mtm_sl:,.0f} · target {mtm_target:,.0f}",''',
     '''                            f"MTM SL {sl_brief} · target {mtm_target:,.0f}",''', 1),
    # ───────────────────────────── Settings.jsx ─────────────────────────────
    (SJ, '''  mtm_sl: 35000,
  mtm_sl_hard_mult: 1.05,   // ── TSG_HARD_STOP_20260911 ──''', '''  mtm_sl_per_lot: 3500,   // ── TSG_SL_PER_LOT_20261015 ── ₹ PER LOT; day SL = this × the day's lots
  mtm_sl: 35000,          // read-only mirror (per-lot × lots) for older readers
  mtm_sl_hard_mult: 1.05,   // ── TSG_HARD_STOP_20260911 ──''', 1),
    (SJ, '''      await saveStrategyConfig("TSG_V1", tsgConfig);''', '''      // ── TSG_SL_PER_LOT_20261015 ── per-lot is the input; mtm_sl is its mirror
      const _slLot = Math.abs(Number(tsgConfig.mtm_sl_per_lot) || 0);
      await saveStrategyConfig("TSG_V1", { ...tsgConfig, mtm_sl_per_lot: _slLot,
        mtm_sl: _slLot * Math.max(1, Number(tsgConfig.lots) || 1) });''', 1),
    (SJ, '''MTM SL/target auto-scale by the lots ratio on those days (LD5a) — e.g. 15 lots @ base 10 → SL ₹52,500.''',
     '''The MTM SL follows automatically (₹ per lot × these lots — e.g. 15 × ₹3,500 → ₹52,500); the MTM target scales by the lots ratio (LD5a).''', 1),
    (SJ, '''                <Field label="MTM SL ₹" helper="Exit ALL legs the first 1m close where combined day MTM (realized + unrealized) ≤ −this. Validated: 35000 @ 10 lots. AUTO-SCALES with lots on expiry days (LD5a): effective SL = this × (day lots ÷ Lots), keeping per-lot risk identical. Target scales the same way; IV knobs don't (vol pts are lot-independent).">
                  <Input type="number" value={tsgConfig.mtm_sl}
                    onChange={(e) => updateTSG(["mtm_sl"], Number(e.target.value))}
                    style={{ maxWidth: 110 }} />
                </Field>''', '''                {/* ── TSG_SL_PER_LOT_20261015 ── entered once per lot; the basket stop follows the lots */}
                <Field label="MTM SL ₹ per lot" helper="Set once — the basket stop is computed from the lots the day actually trades: exit ALL legs the first 1m close where combined day MTM (realized + unrealized) ≤ −(this × the day's lots). Lots on a normal day, Expiry-Day Lots on expiry day. Validated: ₹3,500/lot (= ₹35,000 @ 10 lots). 0 = off. IV knobs don't scale (vol pts are lot-independent).">
                  {(() => {
                    const pl = Math.abs(Number(tsgConfig.mtm_sl_per_lot) || 0);
                    const n = Math.max(1, Number(tsgConfig.lots) || 1);
                    const ex = Number(tsgConfig.expiry_lots) || 0;
                    const inr = (v) => "₹" + Math.round(v).toLocaleString("en-IN");
                    return (
                      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                        <Input type="number" min="0" step="100" value={tsgConfig.mtm_sl_per_lot ?? 0}
                          onChange={(e) => updateTSG(["mtm_sl_per_lot"], Math.abs(Number(e.target.value) || 0))}
                          style={{ maxWidth: 110 }} />
                        <span style={{ fontSize: 11, color: colors.text.muted, fontVariantNumeric: "tabular-nums" }}>
                          {pl > 0
                            ? <>= <b style={{ color: colors.text.secondary }}>{inr(pl * n)}</b> at {n} lot{n === 1 ? "" : "s"}{ex > 0 && ex !== n ? <> · <b style={{ color: colors.text.secondary }}>{inr(pl * ex)}</b> on expiry day ({ex} lots)</> : null}</>
                            : "SL off"}
                        </span>
                      </div>
                    );
                  })()}
                </Field>''', 1),
    # ───────────────────────────── Backtest.jsx ─────────────────────────────
    (B, '''  if (Number(cfg.mtm_sl) > 0) add("MTM SL", `-₹${cfg.mtm_sl}${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === "POSITION" ? " ·pos" : " ·day") : ""}`);   // ── TSG_MTM_SL / TSG_MTM_BASIS_20260821 ──''',
     '''  if (cfg.mtm_sl_per_lot != null && cfg.mtm_sl_per_lot !== "") { if (Number(cfg.mtm_sl_per_lot) > 0) add("MTM SL", `-₹${cfg.mtm_sl_per_lot}/lot${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === "POSITION" ? " ·pos" : " ·day") : ""}`); }   // ── TSG_SL_PER_LOT_20261015 ──
  else if (Number(cfg.mtm_sl) > 0) add("MTM SL", `-₹${cfg.mtm_sl}${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === "POSITION" ? " ·pos" : " ·day") : ""}`);   // ── TSG_MTM_SL / TSG_MTM_BASIS_20260821 ──''', 1),
    (B, '''  const [tsgMtmSl, setTsgMtmSl] = useState(tsgSaved.mtmSl ?? 0);   // ── TSG_MTM_SL ── positive ₹; 0 = off
''', '''  // ── TSG_SL_PER_LOT_20261015 ── MTM SL entered PER LOT; the runner multiplies by each
  // day's SELL-leg lots (after DTE × lots / compounding). A form saved before this patch
  // holds a basket total → converted once at its own SELL lots (17,500 @ 5 → 3,500).
  const [tsgMtmSlLot, setTsgMtmSlLot] = useState(() => {
    if (tsgSaved.mtmSlPerLot != null) return tsgSaved.mtmSlPerLot;
    if (tsgSaved.mtmSl != null) {
      const ls = Array.isArray(tsgSaved.legs) && tsgSaved.legs.length === 4 ? tsgSaved.legs : DEFAULT_TSG_LEGS;
      const n = Math.max(0, ...ls.filter((l) => l.action === "SELL").map((l) => Number(l.lots) || 0));
      return n > 0 ? Math.round(Math.abs(Number(tsgSaved.mtmSl) || 0) / n) : 0;
    }
    return 3500;
  });
''', 1),
    (B, '''mtmSl: tsgMtmSl, mtmSlBasis: tsgMtmSlBasis''', '''mtmSlPerLot: tsgMtmSlLot, mtmSlBasis: tsgMtmSlBasis''', 1),
    (B, '''tsgMtmSl, tsgMtmSlBasis''', '''tsgMtmSlLot, tsgMtmSlBasis''', 2),
    (B, '''        mtm_sl: Math.abs(Number(tsgMtmSl)) || 0,
''', '''        mtm_sl_per_lot: Math.abs(Number(tsgMtmSlLot)) || 0,   // ── TSG_SL_PER_LOT_20261015 ── × each day's SELL lots; no basket mtm_sl key
''', 1),
    (B, '''${Number(tsgMtmSl) > 0 ? ` or ≤ -₹${Math.abs(tsgMtmSl)}${''', '''${Number(tsgMtmSlLot) > 0 ? ` or ≤ -₹${Math.abs(tsgMtmSlLot)}/lot × lots${''', 1),
    (B, '''                <Field label="MTM SL ₹ (all 4 legs, 0 = off)"><input type="number" style={inputStyle} value={tsgMtmSl} onChange={(e) => setTsgMtmSl(Number(e.target.value))} title="Enter as a POSITIVE rupee amount, e.g. 2500 exits ALL legs the first 1m close where combined MTM ≤ -₹2500 (MTM_SL). Same candle-close evaluation as the target — no intra-candle touch. 0 disables." /></Field>''',
     '''                {(() => {   /* ── TSG_SL_PER_LOT_20261015 ── per lot; label shows the basket total at the SELL legs' lots */
                  const n = Math.max(0, ...tsgLegs.filter((l) => l.action === "SELL").map((l) => Number(l.lots) || 0));
                  const pl = Math.abs(Number(tsgMtmSlLot) || 0);
                  return (
                    <Field label={`MTM SL ₹ per lot (0 = off)${pl > 0 && n > 0 ? ` → ₹${(pl * n).toLocaleString("en-IN")} @ ${n}L` : ""}`}><input type="number" min="0" step="100" style={inputStyle} value={tsgMtmSlLot} onChange={(e) => setTsgMtmSlLot(Math.abs(Number(e.target.value) || 0))} title="PER LOT, positive ₹ — e.g. 3500. Each day the basket SL = this × that day's SELL-leg lots (L1/L2), AFTER DTE × lots and lot compounding, so the stop follows the size actually traded. Exits ALL legs the first 1m close where combined MTM ≤ −(per-lot × lots) (MTM_SL). Same candle-close evaluation as the target — no intra-candle touch. 0 disables." /></Field>
                  );
                })()}''', 1),
    # ───────────────────────────── Queue / Compare / Sweep ─────────────────────────────
    (Q, '''    if (Number(cfg.mtm_sl) > 0) p.push(`MTMSL₹${cfg.mtm_sl}${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === "POSITION" ? "·pos" : "·day") : ""}`);   // ── TSG_MTM_SL / TSG_MTM_BASIS_20260821 ──''',
     '''    if (cfg.mtm_sl_per_lot != null && cfg.mtm_sl_per_lot !== "") { if (Number(cfg.mtm_sl_per_lot) > 0) p.push(`MTMSL₹${cfg.mtm_sl_per_lot}/lot${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === "POSITION" ? "·pos" : "·day") : ""}`); }   // ── TSG_SL_PER_LOT_20261015 ──
    else if (Number(cfg.mtm_sl) > 0) p.push(`MTMSL₹${cfg.mtm_sl}${cfg.mtm_sl_basis ? (cfg.mtm_sl_basis === "POSITION" ? "·pos" : "·day") : ""}`);   // ── TSG_MTM_SL / TSG_MTM_BASIS_20260821 ──''', 1),
    (C, '''  { key: "tsg_mtm_sl",       label: "MTM SL ₹",     get: (r) => Number(r.config?.mtm_sl) > 0 ? `-${r.config.mtm_sl}` : null },   // ── TSG_MTM_SL ──''',
     '''  { key: "tsg_mtm_sl",       label: "MTM SL ₹",     get: (r) => (r.config?.mtm_sl_per_lot != null && r.config.mtm_sl_per_lot !== "") ? (Number(r.config.mtm_sl_per_lot) > 0 ? `-${r.config.mtm_sl_per_lot}/lot` : null) : (Number(r.config?.mtm_sl) > 0 ? `-${r.config.mtm_sl}` : null) },   // ── TSG_MTM_SL / TSG_SL_PER_LOT_20261015 ──''', 1),
    (W, '''  { key: "tsg_mtm_sl", label: "MTM SL ₹", strategies: [TSG],
    hint: "1500, 2500, 4000, 6000", parse: _num,
    apply: (c, v) => { c.mtm_sl = Math.abs(v); }, fmt: (v) => `MTMSL ${Math.abs(v)}` },   // ── TSG_MTM_SL ──''',
     '''  // ── TSG_SL_PER_LOT_20261015 ── per-lot axis (the form's own unit). The old
  // basket-total axis keeps its key + meaning so saved sweeps still mean what they
  // meant: it drops the per-lot key from that combo so the total actually applies.
  { key: "tsg_mtm_sl_lot", label: "MTM SL ₹/lot", strategies: [TSG],
    hint: "2500, 3000, 3500, 4500", parse: _num,
    apply: (c, v) => { c.mtm_sl_per_lot = Math.abs(v); delete c.mtm_sl; }, fmt: (v) => `SL ${Math.abs(v)}/lot` },
  { key: "tsg_mtm_sl", label: "MTM SL ₹ (basket total, legacy)", strategies: [TSG],
    hint: "15000, 25000, 35000", parse: _num,
    apply: (c, v) => { c.mtm_sl = Math.abs(v); delete c.mtm_sl_per_lot; }, fmt: (v) => `MTMSL ${Math.abs(v)}` },   // ── TSG_MTM_SL ──''', 1),
    # ───────────────────────────── panel ─────────────────────────────
    (P, '''              {Number(slEff) !== Number(st.mtm_sl) ? " (scaled)" : ""}</span>''',
     '''              {Number(slEff) !== Number(st.mtm_sl) ? " (scaled)" : ""}{Number(st.mtm_sl_per_lot) > 0 ? ` · ₹${Number(st.mtm_sl_per_lot).toLocaleString("en-IN")}/lot` : ""}{/* ── TSG_SL_PER_LOT_20261015 ── */}</span>''', 1),
]

NEW_FILES = {T: '# backend/app/engine/tsg/test_tsg_sl_per_lot.py — fence TSG_SL_PER_LOT_20261015\n#\n# TSG_V1: MTM SL entered PER LOT (Settings + Backtest).\n#\n#   1. resolve_mtm_sl (live, pure)       per-lot × day lots; legacy total × ratio\n#   2. loader migration (real loader)    old file → per-lot derived once, mirror\n#   3. TsgManager._enter_day_impl        the day\'s core.mtm_sl on normal and\n#                                        expiry days (expiry_lots), legacy file\n#   4. backtest runner on a synthetic corpus (never touches backtest.db):\n#        legacy config ≡ ORIGINAL runner (.bak) row-for-row\n#        per-lot × lots ≡ legacy total at the same lots (10 and 5 lots)\n#        DTE × lots: scaled days stop at per-lot × scaled lots\n#        lot compounding: per-lot ≡ legacy scaled; \'fixed ₹\' does not freeze it\n#        per-lot 0 wins over a stale legacy total (SL off)\n#        parallel shards ≡ serial\n#   5. Trade Replay overlay level from the trade\'s own SELL lots\n#\n# Run: cd backend && python3 app/engine/tsg/test_tsg_sl_per_lot.py\n\nfrom __future__ import annotations\n\nimport importlib.util\nimport json\nimport os\nimport random\nimport sqlite3\nimport sys\nimport tempfile\nfrom datetime import date, datetime, timedelta, timezone\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve()\nBACKEND = str(HERE.parents[3])\nif BACKEND not in sys.path:\n    sys.path.insert(0, BACKEND)\nos.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")\n\nFENCE = "TSG_SL_PER_LOT_20261015"\nFAILS: list = []\nOK = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\n# ═════════════════════════════════════════════════════════════════════\n# 1. resolve_mtm_sl\n# ═════════════════════════════════════════════════════════════════════\ndef t_resolve():\n    print("── 1. resolve_mtm_sl ──")\n    from app.engine.tsg.tsg_live_core import resolve_mtm_sl as R\n    check("per-lot 3500 × 10 lots = 35,000", R({"mtm_sl_per_lot": 3500}, 10, 10) == 35000)\n    check("per-lot 3500 × 12 expiry lots = 42,000 (no ratio needed)", R({"mtm_sl_per_lot": 3500}, 12, 10) == 42000)\n    check("per-lot 3500 × 1 lot = 3,500", R({"mtm_sl_per_lot": 3500, "mtm_sl": 99999}, 1, 1) == 3500)\n    check("per-lot wins over a stale legacy total", R({"mtm_sl_per_lot": 3000, "mtm_sl": 35000}, 10, 10) == 30000)\n    check("per-lot 0 = SL off even with a legacy total present", R({"mtm_sl_per_lot": 0, "mtm_sl": 35000}, 10, 10) == 0)\n    check("negative per-lot is sign-tolerant", R({"mtm_sl_per_lot": -2500}, 4, 4) == 10000)\n    check("legacy (no per-lot key): total × day/base (LD5a unchanged)", R({"mtm_sl": 35000}, 15, 10) == 52500)\n    check("legacy: blank per-lot string counts as absent", R({"mtm_sl_per_lot": "", "mtm_sl": 35000}, 10, 10) == 35000)\n    check("legacy default when nothing set = 35,000 × ratio", R({}, 10, 10) == 35000)\n\n\n# ═════════════════════════════════════════════════════════════════════\n# 2. loader migration\n# ═════════════════════════════════════════════════════════════════════\ndef t_loader():\n    print("── 2. strategy_loader migration ──")\n    import app.config.strategy_loader as L\n    saved_dir = L.STRATEGY_DIR\n    with tempfile.TemporaryDirectory() as td:\n        L.STRATEGY_DIR = Path(td)\n        p = Path(td) / "TSG_V1.json"\n        try:\n            def load(obj):\n                if obj is None:\n                    p.unlink(missing_ok=True)\n                else:\n                    p.write_text(json.dumps(obj))\n                return L.load_strategy_config("TSG_V1")\n\n            c = load({"mtm_sl": 35000, "lots": 10, "trade_execution_mode": "LIVE"})\n            check("old file 35,000 @ 10 lots → 3,500/lot, mirror 35,000",\n                  c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 35000, c)\n            check("migration is read-only: file on disk untouched",\n                  json.loads(p.read_text()) == {"mtm_sl": 35000, "lots": 10, "trade_execution_mode": "LIVE"})\n            c = load({"mtm_sl": 3500, "lots": 1})\n            check("old file 3,500 @ 1 lot → 3,500/lot", c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 3500, c)\n            c = load({"mtm_sl": 35000, "lots": 12})\n            check("non-integer division rounds to paise, base-day stop within ₹1",\n                  c["mtm_sl_per_lot"] == 2916.67 and abs(c["mtm_sl"] - 35000) < 1, c)\n            c = load({"mtm_sl_per_lot": 3000, "mtm_sl": 35000, "lots": 10})\n            check("saved per-lot wins; mirror recomputed (30,000)", c["mtm_sl_per_lot"] == 3000 and c["mtm_sl"] == 30000, c)\n            c = load({"mtm_sl_per_lot": 3500, "lots": 5})\n            check("lots changed elsewhere → mirror follows (17,500)", c["mtm_sl"] == 17500, c)\n            c = load({"mtm_sl_per_lot": 0, "mtm_sl": 35000, "lots": 10})\n            check("per-lot 0 → SL off, mirror 0", c["mtm_sl_per_lot"] == 0 and c["mtm_sl"] == 0, c)\n            c = load({"lots": 10})\n            check("file with neither key → default 3,500/lot", c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 35000, c)\n            c = load(None)\n            check("first run (no file) → seeded default carries per-lot 3,500",\n                  c["mtm_sl_per_lot"] == 3500 and json.loads(p.read_text()).get("mtm_sl_per_lot") == 3500, c)\n            # Settings save path: POST merges into the loaded (migrated) config\n            p.write_text(json.dumps({"mtm_sl": 35000, "lots": 10}))\n            cur = L.load_strategy_config("TSG_V1")\n            cur.update({"lots": 6})                       # a lots-only POST\n            L.save_strategy_config("TSG_V1", cur)\n            c = L.load_strategy_config("TSG_V1")\n            check("lots-only save keeps 3,500/lot → 6 lots = 21,000",\n                  c["mtm_sl_per_lot"] == 3500 and c["mtm_sl"] == 21000, c)\n            other = L.load_strategy_config("TMA_V2")\n            check("other strategies untouched (no per-lot key injected)", "mtm_sl_per_lot" not in other)\n        finally:\n            L.STRATEGY_DIR = saved_dir\n\n\n# ═════════════════════════════════════════════════════════════════════\n# 3. TsgManager entry\n# ═════════════════════════════════════════════════════════════════════\ndef t_manager():\n    print("── 3. TsgManager._enter_day_impl ──")\n    import threading\n    import app.engine.tsg.tsg_manager as TM\n    TM.is_day_blocked = lambda sid: False\n    TM.check_strategy_max_loss = lambda sid: False\n    today = TM._now_ist().date()\n\n    def entered(cfg, expiry):\n        m = TM.TsgManager.__new__(TM.TsgManager)\n        m.executor, m._quote_fn, m._lock = None, None, threading.RLock()\n        m._core, m._paper, m._entry_date, m._expiry_iso = None, True, None, ""\n        m._token_by_sym, m._sibling, m._paper_row_ids, m._live_row_ids = {}, {}, {}, {}\n        m._group_id, m._lot_size = None, 65\n        m._cfg = lambda: dict(cfg, min_entry_iv=0)\n        m._alert = lambda *a, **k: None\n        m._persist = lambda: None\n        m._enter_paper = lambda planned, px: False           # stop before any row / notify\n        rows, ltp = [], {}\n        for k, (ce, pe) in enumerate([(120, 20), (80, 30), (40, 60), (4, 110)]):\n            for it, px in (("CE", ce), ("PE", pe)):\n                s = f"NIFTYX{24000 + 50 * k}{it}"\n                rows.append({"tradingsymbol": s, "instrument_type": it, "strike": 24000 + 50 * k,\n                             "expiry": expiry, "instrument_token": k})\n                ltp[s] = px\n        m._enter_day_impl((expiry, rows, ltp), mode="PAPER")\n        return m._core\n\n    nxt = (today + timedelta(days=3)).isoformat()\n    tdy = today.isoformat()\n    c = entered({"lots": 10, "expiry_lots": 12, "mtm_sl_per_lot": 3500}, nxt)\n    check("normal day: 3,500/lot × 10 = 35,000", c is not None and c.mtm_sl == 35000, getattr(c, "mtm_sl", None))\n    check("normal day: leg qty 650", c is not None and all(l.qty == 650 for l in c.legs.values()))\n    c = entered({"lots": 10, "expiry_lots": 12, "mtm_sl_per_lot": 3500}, tdy)\n    check("expiry day: 3,500/lot × 12 = 42,000", c is not None and c.mtm_sl == 42000, getattr(c, "mtm_sl", None))\n    c = entered({"lots": 3, "expiry_lots": 0, "mtm_sl_per_lot": 3500}, nxt)\n    check("3 lots: 10,500 — set once, follows lots", c is not None and c.mtm_sl == 10500, getattr(c, "mtm_sl", None))\n    c = entered({"lots": 10, "expiry_lots": 15, "mtm_sl": 35000}, tdy)\n    check("legacy config (no per-lot): unchanged LD5a 52,500", c is not None and c.mtm_sl == 52500, getattr(c, "mtm_sl", None))\n    c = entered({"lots": 10, "mtm_sl_per_lot": 3500, "mtm_target": 20000, "expiry_lots": 12}, tdy)\n    check("target still ratio-scaled (20,000 × 1.2 = 24,000)", c is not None and c.mtm_target == 24000)\n    check("hard stop rides on the per-lot SL (42,000 × 1.05)",\n          c is not None and abs(c.hard_stop_level() - 44100) < 1e-6, c.hard_stop_level() if c else None)\n\n\n# ═════════════════════════════════════════════════════════════════════\n# 4. backtest runner — synthetic corpus\n# ═════════════════════════════════════════════════════════════════════\nIST = 5 * 3600 + 30 * 60\nDDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,\ntradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,\nclose REAL,volume INTEGER,oi INTEGER);\nCREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);\nCREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);\nCREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""\n\n\ndef _ds(d):\n    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST\n\n\ndef _day(ts):\n    return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST)).date()\n\n\ndef build_corpus(path, days, seed=7):\n    from app.backtest.engine.expiry_calendar import expected_expiry_for_day\n    random.seed(seed)\n    conn = sqlite3.connect(path)\n    conn.executescript(DDL)\n    rows, spot, open_m = [], 24000.0, 9 * 60 + 15\n    for d in days:\n        ds = _ds(d)\n        exp = expected_expiry_for_day(d).isoformat()\n        atm = int(round(spot / 50.0)) * 50\n        ms = []\n        # half the days quiet (theta wins → EOD), half trending (the SL trips)\n        drift = 0.0 if random.random() < 0.5 else random.choice((-1, 1)) * random.uniform(0.0002, 0.0005)\n        for m in range(open_m, 15 * 60 + 30):\n            o = spot\n            c = spot * (1 + drift + random.gauss(0, 0.0003))\n            ts = ds + m * 60\n            rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", None, None, o, max(o, c), min(o, c), c, 0, 0))\n            ms.append((ts, o, c, m))\n            spot = c\n        for K in [atm + 50 * k for k in range(-12, 13)]:\n            for side in ("CE", "PE"):\n                sym = f"NIFTY{exp.replace(\'-\', \'\')}{K}{side}"\n                for (ts, o, c, m) in ms:\n                    tv = 60.0 * (1 - (m - open_m) / 800.0)\n                    px = (lambda s: round(max(s - K, 0) + tv, 2)) if side == "CE" else (lambda s: round(max(K - s, 0) + tv, 2))\n                    a, b = px(o), px(c)\n                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp, a, max(a, b), min(a, b), b, 100, 100))\n    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)\n    conn.commit()\n    conn.close()\n\n\ndef legs(n, hedge=None):\n    h = n if hedge is None else hedge\n    return [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": n, "premium_max": 85},\n            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": n, "premium_max": 85},\n            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": h, "premium_max": 30},\n            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": h, "premium_max": 30}]\n\n\ndef key(t):\n    return (t.entry_ts, t.exit_ts, t.condition, t.qty, round(t.entry_price, 4),\n            round(t.exit_price, 4), t.exit_reason)\n\n\ndef t_backtest():\n    print("── 4. backtest runner (synthetic corpus) ──")\n    from app.utils.market_hours import is_trading_day\n    import app.backtest.data.candle_source as _cs\n    from app.backtest.tsg.backtest_tsg_runner import run_tsg_backtest as NEW\n    bak = Path(HERE.parents[2] / "backtest" / "tsg" / f"backtest_tsg_runner.py.bak-{FENCE}")\n    ORIG = None\n    if bak.exists():\n        from importlib.machinery import SourceFileLoader   # .bak has no .py suffix\n        ldr = SourceFileLoader("tsg_runner_orig", str(bak))\n        spec = importlib.util.spec_from_loader("tsg_runner_orig", ldr)\n        mod = importlib.util.module_from_spec(spec)\n        ldr.exec_module(mod)\n        ORIG = mod.run_tsg_backtest\n\n    td = tempfile.mkdtemp()\n    dbp = os.path.join(td, "bt.db")\n    days, d = [], date(2026, 1, 20)\n    while len(days) < 32:\n        if is_trading_day(d):\n            days.append(d)\n        d += timedelta(days=1)\n    build_corpus(dbp, days)\n    _cs._backtest_db_path = lambda: Path(dbp)\n    D0, D1 = days[2], days[-1]\n\n    def run(fn, cfg):\n        base = {"entry_time": "09:16", "exit_time": "15:25", "mtm_target": 0}\n        r = fn(db_path=dbp, strategy_id="TSG_V1", underlying="NIFTY", date_from=D0, date_to=D1,\n               config_override={**base, **cfg})\n        assert not r.get("aborted"), r.get("reason")\n        return r\n\n    def K(r):\n        return sorted(key(t) for t in r["trades"])\n\n    def sl_days(r):\n        return {_day(t.entry_ts) for t in r["trades"] if t.exit_reason == "MTM_SL"}\n\n    leg10 = run(NEW, {"legs": legs(10), "mtm_sl": 3000})\n    n_sl = len(sl_days(leg10))\n    n_days = len({_day(t.entry_ts) for t in leg10["trades"]})\n    check(f"corpus has both SL and non-SL days ({n_sl} MTM_SL of {n_days})", 3 <= n_sl < n_days)\n    if ORIG:\n        o = run(ORIG, {"legs": legs(10), "mtm_sl": 3000})\n        check("legacy config ≡ ORIGINAL runner row-for-row", K(o) == K(leg10))\n        check("legacy summary net identical", o["summary"]["net_pnl"] == leg10["summary"]["net_pnl"])\n        check("legacy diag keys identical (no per-lot key leaks)",\n              set(o["summary"].get("diag_tsg", {})) == set(leg10["summary"].get("diag_tsg", {})))\n    else:\n        print("  ! .bak not found — ORIGINAL comparison skipped")\n\n    pl10 = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300})\n    check("per-lot 300 × 10 lots ≡ legacy 3,000 @ 10 (row-for-row)", K(pl10) == K(leg10))\n    check("per-lot diag carries mtm_sl_per_lot 300 and base mtm_sl 3,000",\n          pl10["summary"].get("diag_tsg", {}).get("mtm_sl_per_lot") == 300\n          and pl10["summary"].get("diag_tsg", {}).get("mtm_sl") == 3000, pl10["summary"].get("diag_tsg", {}).get("mtm_sl"))\n    pl5 = run(NEW, {"legs": legs(5), "mtm_sl_per_lot": 300})\n    lg5 = run(NEW, {"legs": legs(5), "mtm_sl": 1500})\n    check("per-lot 300 × 5 lots ≡ legacy 1,500 @ 5", K(pl5) == K(lg5))\n    plh = run(NEW, {"legs": legs(5, hedge=10), "mtm_sl_per_lot": 300})\n    lgh = run(NEW, {"legs": legs(5, hedge=10), "mtm_sl": 1500})\n    check("lots counted on the SELL legs (5 short / 10 wing → 1,500)", K(plh) == K(lgh))\n    check("per-lot 0 beats a stale legacy total → no MTM_SL exits",\n          not sl_days(run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 0, "mtm_sl": 3000})))\n    check("legacy \'mtm_sl\' ignored when per-lot present",\n          K(run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "mtm_sl": 999999})) == K(pl10))\n\n    # DTE × lots: every DTE doubled ≡ a 20-lot book with a 6,000 stop\n    all2 = "0:2,1:2,2:2,3:2,4:2,5:2,6:2"\n    plx2 = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "dte_lot_mult": all2})\n    lg20 = run(NEW, {"legs": legs(20), "mtm_sl": 6000})\n    check("DTE ×2 on every day ≡ 20 lots @ 6,000 (SL follows the scaled lots)", K(plx2) == K(lg20))\n    lgx2 = run(NEW, {"legs": legs(10), "mtm_sl": 3000, "dte_lot_mult": all2})\n    check("…whereas the legacy total does NOT follow DTE scaling (the old trap)", K(lgx2) != K(lg20))\n    # mixed: only some DTEs scaled — each day must match the book of its own size\n    mix = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "dte_lot_mult": "0:2,2:2"})\n    big = {_day(t.entry_ts) for t in mix["trades"] if t.qty == 20 * 65}\n    small = {_day(t.entry_ts) for t in mix["trades"] if t.qty == 10 * 65}\n    want = sorted([key(t) for t in lg20["trades"] if _day(t.entry_ts) in big]\n                  + [key(t) for t in leg10["trades"] if _day(t.entry_ts) in small])\n    check(f"mixed DTE (0:2, 2:2): {len(big)} scaled + {len(small)} base days each stop at their own per-lot level",\n          big and small and K(mix) == want)\n\n    # lot compounding (calendar, ×2 after one month)\n    COMP = {"lot_comp_step_months": 1, "lot_comp_add_lots": 10}\n    a = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, **COMP})\n    b = run(NEW, {"legs": legs(10), "mtm_sl": 3000, **COMP})\n    check("compounding: per-lot ≡ legacy ratio-scaled total", K(a) == K(b) and any(t.qty == 1300 for t in a["trades"]))\n    c = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, **COMP, "lot_comp_scale_rs": "fixed"})\n    check("compounding \'fixed ₹\' does not freeze a per-lot SL", K(c) == K(a))\n\n    # parallel shards\n    p2 = run(NEW, {"legs": legs(10), "mtm_sl_per_lot": 300, "dte_lot_mult": "0:2,2:2", "parallel_workers": 2})\n    check("parallel ×2 ≡ serial (per-lot + DTE)", K(p2) == K(mix))\n    check("parallel diag carries mtm_sl_per_lot", p2["summary"].get("diag_tsg", {}).get("mtm_sl_per_lot") == 300)\n    return mix\n\n\n# ═════════════════════════════════════════════════════════════════════\n# 5. Trade Replay overlay\n# ═════════════════════════════════════════════════════════════════════\ndef t_overlay(mix):\n    print("── 5. Trade Replay overlay ──")\n    from types import SimpleNamespace\n    import app.backtest.replay.overlays as OV\n\n    def lv(cfg, n):\n        rows = [{"entry_ts": 1769485560, "qty": n * 65, "direction": a, "condition": f"TSG·L{i}·{a}"}\n                for i, a in ((1, "SELL"), (2, "SELL"), (3, "BUY"), (4, "BUY"))]\n        ctx = SimpleNamespace(cfg=cfg, legs=rows, underlying="NIFTY", run_from=None)\n        out = OV._tsg(ctx)\n        return {x["label"]: x["value"] for x in out["mtm_levels"]}, out\n\n    v, out = lv({"legs": legs(10), "mtm_sl_per_lot": 300, "mtm_sl_hard_mult": 1.05}, 20)\n    check("per-lot overlay uses the trade\'s own SELL lots (20 → −6,000)", -6000 in v.values(), v)\n    check("hard-stop line on the same base (−6,300)", any(abs(x + 6300) < 1e-6 for x in v.values()), v)\n    check("config brief says ₹/lot", "/lot" in out["config_brief"], out["config_brief"])\n    v, _ = lv({"legs": legs(10), "mtm_sl": 3000}, 10)\n    check("legacy overlay unchanged (−3,000)", -3000 in v.values(), v)\n\n\nif __name__ == "__main__":\n    t_resolve()\n    t_loader()\n    t_manager()\n    mix = t_backtest()\n    t_overlay(mix)\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} TSG PER-LOT SL CHECKS PASSED")\n'}


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
                die(f"prerequisite {f!r} not found in {rel}")
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
    for rel in targets:
        if FENCE not in staged[rel]:
            die(f"internal: {rel} edited without the fence marker")
    left = re.findall(r"\btsgMtmSl\b|\bsetTsgMtmSl\b", staged[B])
    if left:
        die(f"Backtest.jsx still references the old tsgMtmSl state ({len(left)}×)")
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in [(r, staged[r]) for r in PY] + list(NEW_FILES.items()):
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print(f"  ✓ py_compile ({len(PY)} backend files + test)")
        esb = next((str(c) for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild")
                    if c.exists()), None) or shutil.which("esbuild")
        if esb:
            for rel in JSX:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print("  ✓ esbuild parsed Settings / Backtest / BacktestQueue / RunComparison / SweepBuilder / TSGV1Panel")
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
        r = subprocess.run([sys.executable, "-c",
                            "import app.backtest.tsg.backtest_tsg_runner, app.engine.tsg.tsg_manager, "
                            "app.backtest.replay.overlays, app.api.tsg_v1_state_routes, app.config.strategy_loader; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("a touched module no longer imports")
        print("  ✓ runner / manager / overlays / state route / loader import")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                print(f"  ! {suite} not found — skipped")
                continue
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=3600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL  " in r.stdout:
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
    print(f"\n✓ {FENCE} applied. Rebuild (./desktop/build-scalp.sh both), then:\n"
          f"  • Settings → TSG V1 → 'MTM SL ₹ per lot' shows 3,500 (derived from your 35,000 @ 10) → press Save once.\n"
          f"  • Backtest → TSG V1 → 'MTM SL ₹ per lot' (your saved 17,500 @ 5 lots shows as 3,500).\n"
          f"  TSG is live: deploy outside 09:00–15:30 IST.")


if __name__ == "__main__":
    main()
