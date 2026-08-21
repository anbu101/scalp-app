#!/usr/bin/env python3
# apply_vap_filters_20260820.py
#
# ── ENTRY_FILTERS_20260820 ── expose the two VAP_V1 entry filters as UI
# knobs, describeConfig chips, a Compare Runs column and sweep axes.
#
#   ema_period / ema_basis_minutes — the option's OWN premium EMA. Entry
#     needs close > VWAP *and* close > EMA. 0 = off, default 20.
#   vol_mult / vol_lookback — the break bar's volume must be at least
#     vol_mult x the mean of the prior vol_lookback bars. 0 = off.
#
# Both gate ENTRY ONLY and never block a leg from re-arming.
#
# WHY THE EMA BASIS DEFAULTS TO 1m: ema_series is SMA-seeded and warm at
# index period-1. On 5m bars off the 09:15 anchor, EMA20 is not warm until
# 10:55 — against a 09:30-11:00 entry window that is one usable bar a day,
# and the run would read as "the filter killed the edge" when the filter
# had never actually run. On 1m closes EMA20 is warm at 09:34. The runner
# aborts when period x basis leaves no room before the entry cutoff, and
# the diag echoes the exact warm-up clock time either way.
#
# Backend ships as whole-file replacements under backend/app/backtest/vap/.
#
# ASSERT-ANCHORED: nothing is written unless every anchor resolves exactly
# once. Run from the repo root:
#     python3 apply_vap_filters_20260820.py --dry-run
#     python3 apply_vap_filters_20260820.py
# Frontend only — no --tree pass.

from __future__ import annotations

import io
import sys
from pathlib import Path

DRY = "--dry-run" in sys.argv
EDITS: list[tuple[str, str, str, str]] = []


def edit(path: str, label: str, old: str, new: str) -> None:
    EDITS.append((path, label, old, new))


BT = "frontend/src/pages/Backtest.jsx"
BQ = "frontend/src/pages/backtest/BacktestQueue.jsx"
RC = "frontend/src/pages/backtest/RunComparison.jsx"
SB = "frontend/src/pages/backtest/SweepBuilder.jsx"

# ══════════════════════════════════════════════════════════════════════
# Backtest.jsx — state, persistence, buildConfig, deps, UI, chips
# ══════════════════════════════════════════════════════════════════════
edit(BT, "Backtest.jsx filter state",
     '''  const [vapGraceDis, setVapGraceDis] = useState(vapSaved.graceDis ?? 0);''',
     '''  const [vapGraceDis, setVapGraceDis] = useState(vapSaved.graceDis ?? 0);
  // ── ENTRY_FILTERS_20260820 ── option-premium EMA + break-bar volume.
  // Both gate ENTRY only; neither may block a leg from re-arming.
  const [vapEmaPeriod, setVapEmaPeriod] = useState(vapSaved.emaPeriod ?? 0);
  const [vapEmaBasis, setVapEmaBasis] = useState(vapSaved.emaBasis ?? 1);
  const [vapVolMult, setVapVolMult] = useState(vapSaved.volMult ?? 0);
  const [vapVolLookback, setVapVolLookback] = useState(vapSaved.volLookback ?? 12);''')

edit(BT, "Backtest.jsx filter persistence",
     '''grace: vapGrace, graceDis: vapGraceDis })); } catch { /* ignore */ }
  }, [vapMode, vapSigPrem, vapMinPrem, vapSelTime, vapBothSides, vapArmFirst, vapBuffer, vapSlMode, vapSlPct, vapAtrPeriod, vapAtrMult, vapMaxSl, vapTpMode, vapRr, vapTpPct, vapSessStart, vapSessEnd, vapExitTime, vapMain, vapHedge, vapMaxDay, vapWingMode, vapGrace, vapGraceDis]);''',
     '''grace: vapGrace, graceDis: vapGraceDis, emaPeriod: vapEmaPeriod, emaBasis: vapEmaBasis, volMult: vapVolMult, volLookback: vapVolLookback })); } catch { /* ignore */ }
  }, [vapMode, vapSigPrem, vapMinPrem, vapSelTime, vapBothSides, vapArmFirst, vapBuffer, vapSlMode, vapSlPct, vapAtrPeriod, vapAtrMult, vapMaxSl, vapTpMode, vapRr, vapTpPct, vapSessStart, vapSessEnd, vapExitTime, vapMain, vapHedge, vapMaxDay, vapWingMode, vapGrace, vapGraceDis, vapEmaPeriod, vapEmaBasis, vapVolMult, vapVolLookback]);''')

edit(BT, "Backtest.jsx buildConfig filter keys",
     '''        vwap_buffer_pct: Number(vapBuffer) || 0,''',
     '''        vwap_buffer_pct: Number(vapBuffer) || 0,
        ema_period: Number(vapEmaPeriod) || 0,               // ── ENTRY_FILTERS_20260820 ──
        ema_basis_minutes: Number(vapEmaBasis) || 1,
        vol_mult: Number(vapVolMult) || 0,
        vol_lookback: Number(vapVolLookback) || 12,''')

edit(BT, "Backtest.jsx buildConfig deps + filters",
     '''vapMain, vapHedge, vapMaxDay, vapWingMode, vapGrace, vapGraceDis]);   // ── VAP_V1 / SL_GRACE_20260820 ── stale-closure rule''',
     '''vapMain, vapHedge, vapMaxDay, vapWingMode, vapGrace, vapGraceDis,
      vapEmaPeriod, vapEmaBasis, vapVolMult, vapVolLookback]);   // ── VAP_V1 / SL_GRACE / ENTRY_FILTERS ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit''')

edit(BT, "Backtest.jsx filter UI section",
     '''              <div style={tmaSecLabel}>Stops &amp; targets</div>''',
     '''              {/* ── ENTRY_FILTERS_20260820 ── */}
              <div style={tmaSecLabel}>Entry filters (option series)</div>
              <div style={tmaSecRow}>
                <Field label="EMA period (0=off)">
                  <input type="number" min="0" max="400" style={{ ...inputStyle, width: 90 }} value={vapEmaPeriod} onChange={(e) => setVapEmaPeriod(Number(e.target.value))}
                    title="EMA of the SIGNAL option's own premium. Entry then needs the close ABOVE VWAP *and* ABOVE this EMA. Blocks entries only — a close below VWAP still re-arms the leg." />
                </Field>
                {Number(vapEmaPeriod) > 0 && (
                  <Field label="EMA basis">
                    <select style={{ ...inputStyle, width: 210 }} value={String(vapEmaBasis)} onChange={(e) => setVapEmaBasis(Number(e.target.value))}>
                      <option value="1">1m closes (warm sooner)</option>
                      <option value="5">5m closes (same bars as VWAP)</option>
                    </select>
                  </Field>
                )}
                <Field label="Volume multiple (0=off)">
                  <input type="number" step="0.25" style={{ ...inputStyle, width: 90 }} value={vapVolMult} onChange={(e) => setVapVolMult(Number(e.target.value))}
                    title="The breaking 5m bar's volume must be at least this multiple of the mean of the prior bars. A rolling window, not the session mean — the 09:15 open spike would otherwise drag the average up all morning and make every later break look thin." />
                </Field>
                {Number(vapVolMult) > 0 && (
                  <Field label="Volume lookback (bars)">
                    <input type="number" min="1" max="120" style={{ ...inputStyle, width: 90 }} value={vapVolLookback} onChange={(e) => setVapVolLookback(Number(e.target.value))}
                      title="How many prior completed 5m bars form the average. 12 = one hour. Fewer than 3 bars with real volume is treated as undecidable and blocks the entry (DIAG blocked_vol_warmup)." />
                  </Field>
                )}
                <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 440, lineHeight: 1.45 }}>
                  {Number(vapEmaPeriod) > 0 && Number(vapEmaBasis) === 5
                    ? `⚠ EMA${vapEmaPeriod} on 5m bars is not warm until ${String(Math.floor((9 * 60 + 15 + vapEmaPeriod * 5) / 60)).padStart(2, "0")}:${String((9 * 60 + 15 + vapEmaPeriod * 5) % 60).padStart(2, "0")} — check that leaves room before your ${vapSessEnd} cutoff, or use the 1m basis. The run aborts if it leaves none.`
                    : "Both filters sit on the SIGNAL contract and gate ENTRY only — neither can stop a close below VWAP from re-arming the leg. Watch blocked_ema / blocked_vol in DIAG: a filter blocking nothing is costing nothing and buying nothing."}
                </div>
              </div>

              <div style={tmaSecLabel}>Stops &amp; targets</div>''')

edit(BT, "Backtest.jsx describeConfig filter chips",
     '''    if (Number(cfg.vwap_buffer_pct) > 0) add("Buffer", `${cfg.vwap_buffer_pct}%`);''',
     '''    if (Number(cfg.vwap_buffer_pct) > 0) add("Buffer", `${cfg.vwap_buffer_pct}%`);
    if (Number(cfg.ema_period) > 0) add("EMA", `${cfg.ema_period}@${cfg.ema_basis_minutes || 1}m`);   // ── ENTRY_FILTERS_20260820 ──
    if (Number(cfg.vol_mult) > 0) add("Vol", `${cfg.vol_mult}× last ${cfg.vol_lookback || 12}`);''')

edit(BQ, "BacktestQueue paramLine filter chips",
     '''    if (Number(cfg.vwap_buffer_pct) > 0) p.push(`buf${cfg.vwap_buffer_pct}%`);''',
     '''    if (Number(cfg.vwap_buffer_pct) > 0) p.push(`buf${cfg.vwap_buffer_pct}%`);
    if (Number(cfg.ema_period) > 0) p.push(`EMA${cfg.ema_period}@${cfg.ema_basis_minutes || 1}m`);   // ── ENTRY_FILTERS_20260820 ──
    if (Number(cfg.vol_mult) > 0) p.push(`vol${cfg.vol_mult}x${cfg.vol_lookback || 12}`);''')

edit(RC, "RunComparison filter columns",
     '''  { key: "vap_sides", label: "VAP sides",''',
     '''  { key: "vap_ema", label: "VAP EMA filter", get: (r) => (r.config?.vwap && r.config?.v1 && Number(r.config.ema_period) > 0) ? `EMA${r.config.ema_period} @${r.config.ema_basis_minutes || 1}m` : null },
  { key: "vap_vol", label: "VAP vol filter", get: (r) => (r.config?.vwap && r.config?.v1 && Number(r.config.vol_mult) > 0) ? `${r.config.vol_mult}× last ${r.config.vol_lookback || 12}` : null },
  { key: "vap_sides", label: "VAP sides",''')

edit(SB, "SweepBuilder filter axes",
     '''  { key: "vap_sl_grace", label: "SL grace (min)", strategies: [VAP],''',
     '''  { key: "vap_ema_period", label: "Option EMA period (0=off)", strategies: [VAP],
    hint: "0, 9, 20, 50", parse: _num,
    apply: (c, v) => { if (c.vwap) c.ema_period = v; }, fmt: (v) => (v ? `EMA${v}` : "noEMA") },
  { key: "vap_ema_basis", label: "EMA basis (minutes)", strategies: [VAP],
    hint: "1, 5", parse: (tok) => {
      const v = Number(tok.trim());
      return [1, 5].includes(v) ? { v } : { err: `"${tok}" must be 1 or 5` };
    },
    apply: (c, v) => { if (c.vwap) c.ema_basis_minutes = v; }, fmt: (v) => `@${v}m` },
  { key: "vap_vol_mult", label: "Break-bar volume multiple (0=off)", strategies: [VAP],
    hint: "0, 1.5, 2, 3", parse: _num,
    apply: (c, v) => { if (c.vwap) c.vol_mult = v; }, fmt: (v) => (v ? `vol${v}x` : "noVol") },
  { key: "vap_vol_lookback", label: "Volume lookback (bars)", strategies: [VAP],
    hint: "6, 12, 24", parse: _num,
    apply: (c, v) => { if (c.vwap) c.vol_lookback = v; }, fmt: (v) => `lb${v}` },
  { key: "vap_sl_grace", label: "SL grace (min)", strategies: [VAP],''')


def main() -> int:
    planned: dict[Path, str] = {}
    failures: list[str] = []
    for rel, label, old, new in EDITS:
        p = Path(rel)
        if not p.exists():
            failures.append(f"MISSING FILE {rel} ({label})")
            continue
        src = planned.get(p)
        if src is None:
            src = io.open(p, encoding="utf-8").read()
        n = src.count(old)
        if n == 0:
            failures.append(f"ANCHOR MISS  {rel} :: {label}")
            continue
        if n > 1:
            failures.append(f"ANCHOR AMBIGUOUS ({n}x) {rel} :: {label}")
            continue
        if new in src:
            failures.append(f"ALREADY APPLIED {rel} :: {label}")
            continue
        planned[p] = src.replace(old, new, 1)
        print(f"  ok  {rel} :: {label}")
    if failures:
        print("\nABORTED — nothing written:")
        for f in failures:
            print(f"  {f}")
        return 1
    if DRY:
        print(f"\nDRY RUN: {len(planned)} file(s) would change. Nothing written.")
        return 0
    for p, text in planned.items():
        io.open(p, "w", encoding="utf-8").write(text)
    print(f"\nApplied {len(EDITS)} edit(s) across {len(planned)} file(s).")
    print("REMINDER: replace backend/app/backtest/vap/*.py with the new "
          "whole-file versions, in BOTH trees.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
