#!/usr/bin/env python3
# apply_tma_reentry_form.py — fence TMA_REENTRY_FORM_20260921
#
# Adds a "Continuation re-entry (experiment)" section to the TMA_V2 form on the
# Backtest page (between Hedge sourcing and Legs), so the re-entry knobs from
# TMA_REENTRY_20260921 can be set on a single run without the sweep builder.
# Frontend only: frontend/src/pages/Backtest.jsx (+ its build copy if present).
# An untouched form emits the exact same config as before.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_tma_reentry_form.py
#
# RUN ONLY THIS SCRIPT. Requires TMA_REENTRY_20260921 (already applied).
# Flags: --repo PATH  --allow-dirty

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

FORM_FENCE = "TMA_REENTRY_FORM_20260921"
PARENT_FENCE = "TMA_REENTRY_20260921"


STATE_ANCHOR = "  const [tma2MaxLoss, setTma2MaxLoss] = useState(tma2Saved.maxLoss ?? 0);\n"
STATE_INS = '''  // ── TMA_REENTRY_FORM_20260921 ── continuation re-entry (backtest experiment).
  // ONE state object on purpose: a single name to carry through the LS
  // persist + buildConfig dep arrays (stale-closure discipline).
  const [tma2Re, setTma2Re] = useState({ decay: false, frac: 0.33, tp: false, expiry: false, extGate: false, max: 1, dte: 1, fill: "NEXT_OPEN", ...(tma2Saved.re || {}) });
'''
LS_OLD, LS_NEW = "maxLoss: tma2MaxLoss, tradeMode:", "maxLoss: tma2MaxLoss, re: tma2Re, tradeMode:"
DEP_OLD = "tma2StreakK, tma2CdDays, tma2MaxLoss, tma2TradeMode,"
DEP_NEW = "tma2StreakK, tma2CdDays, tma2MaxLoss, tma2Re, tma2TradeMode,"
CFG_ANCHOR = "        max_loss_per_trade: Number(tma2MaxLoss) || 0,\n"
CFG_INS = '''        // ── TMA_REENTRY_FORM_20260921 ── keys are emitted ONLY when a trigger
        // is ticked, so an untouched form produces the exact same config as before
        ...(() => {
          const on = tma2Mode !== "SELL" ? [] : [tma2Re.decay && "DECAY", tma2Re.tp && "TP", tma2Re.expiry && tma2TradeMode === "POSITIONAL" && "EXPIRY"].filter(Boolean);
          return on.length ? {
            reentry_on: on,
            reentry_decay_frac: tma2Re.decay ? (Number(tma2Re.frac) || 0) : 0,
            reentry_ext_gate: !!tma2Re.extGate,
            reentry_max_per_trend: Math.max(0, Math.floor(Number(tma2Re.max) || 0)),
            roll_min_dte: Math.max(0, Math.floor(Number(tma2Re.dte) || 0)),
            reentry_expiry_fill: tma2Re.fill === "SAME_MINUTE" ? "SAME_MINUTE" : "NEXT_OPEN",
          } : {};
        })(),
'''
CHIP_PREFIX = '    if (Number(cfg.sl_streak_count) > 0) add("SL brake"'
CHIP_INS = '''    if (Array.isArray(cfg.reentry_on) && cfg.reentry_on.length) add("Re-entry", `${cfg.reentry_on.map((x) => (x === "DECAY" ? `DECAY@${cfg.reentry_decay_frac}` : x)).join("+")} · max ${Number(cfg.reentry_max_per_trend ?? 1) || "∞"}${cfg.reentry_ext_gate ? " · ext gate" : ""}${(cfg.reentry_on.includes("EXPIRY") || cfg.reentry_on.includes("TP")) ? ` · ${cfg.reentry_expiry_fill || "NEXT_OPEN"}` : ""}`);   // ── TMA_REENTRY_FORM_20260921 ──
'''
FORM_ANCHOR = '''              {/* ── Legs ── */}
              <div style={tmaSecLabel}>Legs</div>
              <div style={{ ...tmaSecRow, marginBottom: 6 }}>
                <Field label="SL unit">
                  <select style={{ ...inputStyle, width: 170 }} value={tma2SlUnit}'''
FORM_INS = '''              {/* ── TMA_REENTRY_FORM_20260921 ── continuation re-entry (experiment) ── */}
              <div style={tmaSecLabel}>Continuation re-entry (experiment)</div>
              {tma2Mode !== "SELL" ? (
                <div style={{ fontSize: 11, color: colors.text.tertiary, marginBottom: 10 }}>SELL mode only — switch Execution mode to SELL to use it.</div>
              ) : (
                <div style={{ ...tmaSecRow, marginBottom: 6 }}>
                  {/* NOT a <Field>: Field renders a <label>, and nesting the checkbox labels inside it would cross-toggle them */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span style={{ ...typography.label, color: colors.text.muted, fontSize: 11 }}>Re-enter after</span>
                    <div style={{ display: "flex", gap: 14, alignItems: "center", height: 34, fontSize: 12, color: colors.text.secondary }}>
                      <label style={{ display: "flex", gap: 5, alignItems: "center", cursor: "pointer" }}
                        title="Profit roll: on a completed 5m bar, when the sold leg has decayed to the fraction of its entry premium, close the spread and open a fresh one (premium < cap) in the SAME minute. Entry window only; never on the position's expiry day when min DTE is 1+.">
                        <input type="checkbox" checked={!!tma2Re.decay} onChange={(e) => setTma2Re((r) => ({ ...r, decay: e.target.checked }))} /> Decay roll
                      </label>
                      <label style={{ display: "flex", gap: 5, alignItems: "center", cursor: "pointer" }}
                        title="After a TP exit while EMA13 is still on the trend side of the exit line: re-enter the next minute (non-expiry day). A TP on the position's expiry day follows the Expiry fill setting.">
                        <input type="checkbox" checked={!!tma2Re.tp} onChange={(e) => setTma2Re((r) => ({ ...r, tp: e.target.checked }))} /> TP
                      </label>
                      <label style={{ display: "flex", gap: 5, alignItems: "center", cursor: tma2TradeMode === "POSITIONAL" ? "pointer" : "not-allowed", opacity: tma2TradeMode === "POSITIONAL" ? 1 : 0.45 }}
                        title="After the expiry-day square-off while the trend is still valid: continue in next week's contract. Positional only.">
                        <input type="checkbox" disabled={tma2TradeMode !== "POSITIONAL"} checked={!!tma2Re.expiry && tma2TradeMode === "POSITIONAL"} onChange={(e) => setTma2Re((r) => ({ ...r, expiry: e.target.checked }))} /> Expiry close
                      </label>
                    </div>
                  </div>
                  {tma2Re.decay && (
                    <>
                      <Field label="Roll at × entry premium">
                        <input type="number" step="0.05" min="0.05" max="0.95" style={{ ...inputStyle, width: 90 }} value={tma2Re.frac} onChange={(e) => setTma2Re((r) => ({ ...r, frac: Number(e.target.value) }))}
                          title="0.33 = roll once the sold premium is at or below 33% of its entry (sold at 180 → rolls at 59.4). Must be between 0 and 1." />
                      </Field>
                      <Field label="Roll min DTE (days)">
                        <input type="number" step="1" min="0" style={{ ...inputStyle, width: 80 }} value={tma2Re.dte} onChange={(e) => setTma2Re((r) => ({ ...r, dte: Number(e.target.value) }))}
                          title="Calendar days to expiry required for a decay roll. 1 = never on expiry day (where 'highest premium below cap' picks a deep-ITM strike)." />
                      </Field>
                    </>
                  )}
                  {(tma2Re.decay || tma2Re.tp || tma2Re.expiry) && (
                    <>
                      <Field label="Re-entries / trend (0=∞)">
                        <input type="number" step="1" min="0" style={{ ...inputStyle, width: 80 }} value={tma2Re.max} onChange={(e) => setTma2Re((r) => ({ ...r, max: Number(e.target.value) }))}
                          title="Generations allowed after the signal entry. Rows are labelled E1R1, E1R2… so the Entry Condition P&L block reports re-entered legs on their own." />
                      </Field>
                      <Field label="Max-extension gate">
                        <select style={{ ...inputStyle, width: 190 }} value={tma2Re.extGate ? "ON" : "OFF"} onChange={(e) => setTma2Re((r) => ({ ...r, extGate: e.target.value === "ON" }))}
                          title="ON applies the Max 13-89 extension % filter to re-entries too — a roll late in a trend is by definition an extended entry.">
                          <option value="OFF">OFF — signals only</option>
                          <option value="ON">ON — re-entries too</option>
                        </select>
                      </Field>
                    </>
                  )}
                  {(tma2Re.expiry || tma2Re.tp) && (
                    <Field label="Expiry-day fill">
                      <select style={{ ...inputStyle, width: 250 }} value={tma2Re.fill} onChange={(e) => setTma2Re((r) => ({ ...r, fill: e.target.value }))}
                        title="Next-week candles exist on only 3 of 303 expiry days in the corpus. NEXT_OPEN measures the continuation from 09:20 next session on real front-week data (no overnight leg). SAME_MINUTE rolls in the closing minute where next-week rows exist and drops + counts the rest (reentry_no_data).">
                        <option value="NEXT_OPEN">Next session 09:20 (measurable)</option>
                        <option value="SAME_MINUTE">Same minute (needs next-week data)</option>
                      </select>
                    </Field>
                  )}
                  <div style={{ alignSelf: "flex-end", fontSize: 11, color: colors.text.tertiary, paddingBottom: 8, maxWidth: 440, lineHeight: 1.45 }}>
                    All unticked = original V2, config unchanged. A re-entry is not a signal: it re-opens the SAME trend side only while EMA13 is still on the trend side of the exit line, rolls the hedge with the sold leg, respects the SL-streak brake and ignores max trades/day. Funnel counters appear in the run's DIAG (rolls_decay, reentries_taken, reentry_no_data…).
                  </div>
                </div>
              )}

'''

def _once(t, anchor):
    assert t.count(anchor) == 1, f"anchor x{t.count(anchor)}: {anchor[:60]!r}"

def patch_backtest(t):
    _once(t, STATE_ANCHOR); t = t.replace(STATE_ANCHOR, STATE_ANCHOR + STATE_INS)
    _once(t, LS_OLD); t = t.replace(LS_OLD, LS_NEW)
    assert t.count(DEP_OLD) == 2, f"dep arrays x{t.count(DEP_OLD)} (expected 2: LS persist + buildConfig)"
    t = t.replace(DEP_OLD, DEP_NEW)
    _once(t, CFG_ANCHOR); t = t.replace(CFG_ANCHOR, CFG_ANCHOR + CFG_INS)
    lines = t.split("\\n") if False else t.split("\n")
    hits = [i for i, l in enumerate(lines) if l.startswith(CHIP_PREFIX)]
    assert len(hits) == 1, f"chip anchor x{len(hits)}"
    lines.insert(hits[0] + 1, CHIP_INS.rstrip("\n"))
    t = "\n".join(lines)
    _once(t, FORM_ANCHOR); t = t.replace(FORM_ANCHOR, FORM_INS + FORM_ANCHOR)
    return t


def die(msg: str) -> None:
    print(f"\nABORT — {msg}\nNothing was written.")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    rel = "frontend/src/pages/Backtest.jsx"
    target = repo / rel
    runner = repo / "backend/app/backtest/tma/backtest_tma_v2_runner.py"
    if not target.exists() or not runner.exists():
        die(f"{repo} is not the scalp-app repo root")
    if PARENT_FENCE not in runner.read_text():
        die(f"parent fence {PARENT_FENCE} is not applied — run apply_tma_reentry.py first")
    txt = target.read_text()
    if FORM_FENCE in txt:
        print(f"{FORM_FENCE} is already applied — nothing to do.")
        return
    if not a.allow_dirty:
        try:
            out = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=repo,
                                 capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                die(f"{rel} has uncommitted changes (an M is a stop sign). Commit/stash, or re-run with "
                    f"--allow-dirty if they are yours and intended.")
        except Exception:
            pass
    writes = {}
    try:
        writes[target] = patch_backtest(txt)
    except AssertionError as e:
        die(f"Backtest.jsx anchor problem: {e}")
    mirror = repo / "desktop/src-tauri/frontend/src/pages/Backtest.jsx"
    if mirror.exists() and FORM_FENCE not in mirror.read_text():
        try:
            writes[mirror] = patch_backtest(mirror.read_text())
        except AssertionError:
            print("note: build copy skipped (anchors differ) — the next build refreshes it")

    backups = []
    for p, t in writes.items():
        b = p.with_name(p.name + f".bak-{FORM_FENCE}")
        shutil.copy2(p, b)
        backups.append((p, b))
        p.write_text(t)

    esb = repo / "frontend/node_modules/.bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if esb.exists():
        r = subprocess.run([str(esb), str(target), "--loader:.jsx=jsx", "--log-level=error"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            for p, b in backups:
                shutil.copy2(b, p)
                b.unlink()
            die(f"esbuild rejected Backtest.jsx — rolled back:\n{r.stderr[-600:]}")
        print("esbuild: Backtest.jsx parses")
    print(f"""
{FORM_FENCE} applied ({len(writes)} file{'s' if len(writes) != 1 else ''}).
  Rebuild / reload the frontend, open Backtest → TMA_V2: the new section sits
  between "Hedge sourcing" and "Legs" (SELL mode; "Expiry close" needs Positional).
""")


if __name__ == "__main__":
    main()
