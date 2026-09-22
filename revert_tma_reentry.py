#!/usr/bin/env python3
# revert_tma_reentry.py — removes TMA_REENTRY_20260921 and (if applied)
# TMA_REENTRY_FORM_20260921. The experiment was falsified 2026-09-21: no
# re-entry variant beat the sealed TMA_V2 baseline beyond noise.
#
#   cd /Users/anbu/dev/scalp-app && python3 revert_tma_reentry.py
#
# RUN ONLY THIS SCRIPT. What it does, all-or-nothing:
#   backtest_tma_v2_runner.py  → the original file (the apply's .bak, else the
#                                copy carried in this script) — accepted ONLY
#                                if its sha256 is the GitHub-main original
#   tma_v2_engine.py           → the appended helper block is cut out
#   test_tma_v2_reentry.py     → deleted
#   SweepBuilder / BacktestQueue / RunComparison / Backtest .jsx
#                              → the exact inserted text is removed (inverse of
#                                the apply; any other edits you made are kept)
#   desktop/src-tauri build copies → same, where they carry the fence
#   *.bak-TMA_REENTRY_20260921 / *.bak-TMA_REENTRY_FORM_20260921 → deleted
# Every file it changes or deletes is first copied to
#   ~/.scalp-app/patch_backups/TMA_REENTRY_REVERT_<timestamp>/
# Then it verifies: runner+engine sha == originals, no fence left anywhere,
# engine tests pass, JSX parses. Any failure → everything is put back.
# Not touched: tools/lab/tma_roll/ (hand-placed lab scripts), backtest.db,
# past runs in the Backtest history.

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path

FENCE = "TMA_REENTRY_20260921"
FORM_FENCE = "TMA_REENTRY_FORM_20260921"
RUNNER_SHA = "c6c6185038e18324b11b3f29edc83c23969a6f22562543ad36ebfddb46d59799"
ENGINE_SHA = "fb4175f85f95d4c9b66e8e1d2b1765a036475eecb953b9df63406341849ddaf0"
TMA = "app/backtest/tma"
RUNNER_ORIG = """
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
CwAsoXw=
"""
ENGINE_APPEND = """
eNq1VttuGzcQfddXDLYoqk0lxXbgwlaiAGq8Loz6EjhqgMAQNtQuZRFek1uSG1lAHvLUDyjyhf6SnuFKa8mO47xUEKQlOZczM2eG
22r9RLdfv+BLo5Nhep4kp6PzD+nO1s5vW/s72/R78sfR6UoiM9orXQmvjCYru1J7u6CZLEpp3VLosS/8DA8OjkZH75M+aeNnSl+S
mJhPkvDsqFBaUjYT+lLmPRpS9E1nESkHS6dnIxLk1KUWBbUnlSrytF659NMOGV0sSF4r7/AIwUPsy9t//h3ZCt6s0E6x1Q45A2Ni
6qWF1OgtPSd5UyrElBXGSSxzmYkFWVMUQCnJFcbT3FRFTohA2rmCFIzBWekXsDU1Ngha6TyZaXj2Vuo87tFoJiHt54bKyrKIyBG2
9LVCpTWjcFcdEp5RAbidKKAFHO86tX9xLenvCsYB34WtzBrnkEUL6AAidB625Y2XCNNomLoUXrJluv3ylRPC54UAwDdnJ2+Pk1Fy
QLvXNBFwD9CWJhJhAKmjtjZUGHMlZgAb91q5nLJcqnQub1Lh21i43T4dK+cvcpX5MXC6PintO/Ss06L7Hz9N62Ma0OgwfZceJIfD
v45HMXVf01nJYYniAufjflCOouiIfa1yWSAUAGes8xmXaBnC0dkpeYXkMKIekP8aXMVMF7bzagBcL+nUgGTzmdRgYDAyEw6svi5R
BpnTQvoePAYNld8AIyuEJRdWAThZZmi7kLqOPY77TZQqJMftXqjxReRdNF6iqL33N7JRm1fNngRDNyUmIMhV2LHSV1azSqsVShAY
lX4ShcofKYLzSFSfDrDCQuV4dt4+UZzHy9MJ5EqtnA729jdrNTGmuCtW6LCQ4A1udgM30dF5aLyQzdF5cnoQsNFH/v1IuZF1tbi9
Z4abbI2sd2VaMhVVZkKrnuwBnsg8mp6ltbysh4aZBms3jCANAXgeD7+gcZhE7TeYRHL7Bb2m8+SQ86PYxkt6u9x/xftxh+bKz4Kl
pgVZHi1uiio4WjVdOEtOhnv7NBWq6DoxBbZAugFV4HGmcjEpakZxb23G9Bwyc2GvsYSRmDCxKBNFgd6+rgAYZUfYYCznJ1T/jq1w
8LAxO/XgQFEHoRtaS5aCyC7AumPckmMN31HqdfqzklMatNKZbK/IwDyzcQh/tdWDiPWOU9aOZBSve2CLgZi9S+kbIzWqTf5j0m82
Q8nNou8rBcWbTJYo5mhRysRaA5K/F0VVP8cPrTQhLcMqORe4j+7lgz9XcgEF1Dr9M/nwLoAu4w2J+zFBI2ajrMkRrR1FEsJRUwLW
/EYRGnMXkdzbj8bhSHRosra//SIaY8R0WBz/K4tiZS+M8B+pcFi3Bfg/CahDLw4GFL1Joho+Dl/hcDl2mjslLTP/I4PniWnzo9fB
tDBi7UL4jObYftENffaZ2+Hn1e1Q39rNVfH40Gi6+d3wJKGFsDlu1OyKHrxJVE665lJv4ocx/T/23mP1XifGqvAlXyMhQe27uyfk
IRrHzISt3tb3GcILWMEdtfUkWcTEgRJd5stzVnpG21vs4PvvkDznly+B/wGVDj1v
"""


def unpack(b: str) -> str:
    return zlib.decompress(base64.b64decode("".join(b.split()))).decode()


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


# ── the apply scripts' insert payloads, verbatim (inverse is exact removal) ──
SWEEP_ANCHOR = "  // ── XOVER_TOGGLE ── 13/89 crossover exit on/off\n"
SWEEP_INSERT = '''  // ── TMA_REENTRY_20260921 ── continuation re-entry (backtest experiment).
  // ONE token carries the triggers AND the decay fraction, so OFF never
  // multiplies with a fraction axis: "OFF, TP+EXPIRY, DECAY@0.33,
  // DECAY@0.33+TP+EXPIRY". Runner semantics: backtest_tma_v2_runner header.
  { key: "tma2_reentry", label: "Continuation re-entry", strategies: [TMA2],
    hint: "OFF, TP+EXPIRY, DECAY@0.33, DECAY@0.33+TP+EXPIRY", parse: (tok) => {
      const t = tok.trim().toUpperCase();
      if (t === "OFF") return { v: { on: [], frac: 0 } };
      const on = []; let frac = 0;
      for (const part of t.split("+").map((s) => s.trim())) {
        const m = part.match(/^DECAY@(0?\\.\\d+)$/);
        if (m) { on.push("DECAY"); frac = Number(m[1]); }
        else if (part === "TP" || part === "EXPIRY") on.push(part);
        else return { err: `"${tok}": use OFF, or TP / EXPIRY / DECAY@fraction joined by + (e.g. DECAY@0.33+TP)` };
      }
      if (on.includes("DECAY") && !(frac > 0 && frac < 1)) return { err: `"${tok}": decay fraction must be between 0 and 1` };
      return { v: { on: ["DECAY", "TP", "EXPIRY"].filter((x) => on.includes(x)), frac } };
    },
    apply: (c, v) => { c.reentry_on = v.on; c.reentry_decay_frac = v.frac; },
    fmt: (v) => (v.on.length ? "re:" + v.on.map((x) => (x === "DECAY" ? `D${v.frac}` : x === "TP" ? "TP" : "EXP")).join("+") : "reOFF") },
  { key: "tma2_re_ext", label: "Re-entry ext gate", strategies: [TMA2],
    hint: "OFF, ON", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["OFF", "ON"].includes(v) ? { v: v === "ON" } : { err: `"${tok}" must be OFF or ON` };
    },
    apply: (c, v) => { c.reentry_ext_gate = v; },
    fmt: (v) => (v ? "reExt" : "reNoExt") },
  { key: "tma2_re_max", label: "Re-entries per trend", strategies: [TMA2],
    hint: "1, 2, 0 (0 = unlimited)", parse: (tok) => {
      const v = Number(tok.trim());
      return Number.isInteger(v) && v >= 0 ? { v } : { err: `"${tok}" must be a whole number, 0 = unlimited` };
    },
    apply: (c, v) => { c.reentry_max_per_trend = v; },
    fmt: (v) => (v > 0 ? `re≤${v}` : "re∞") },
  { key: "tma2_roll_dte", label: "Decay roll min DTE (days)", strategies: [TMA2],
    hint: "1, 2, 3", parse: (tok) => {
      const v = Number(tok.trim());
      return Number.isInteger(v) && v >= 0 ? { v } : { err: `"${tok}" must be a whole number of calendar days` };
    },
    apply: (c, v) => { c.roll_min_dte = v; },
    fmt: (v) => `dte≥${v}` },
  { key: "tma2_exp_fill", label: "Expiry re-entry fill", strategies: [TMA2],
    hint: "NEXT_OPEN, SAME_MINUTE", parse: (tok) => {
      const v = tok.trim().toUpperCase();
      return ["NEXT_OPEN", "SAME_MINUTE"].includes(v) ? { v } : { err: `"${tok}" must be NEXT_OPEN or SAME_MINUTE` };
    },
    apply: (c, v) => { c.reentry_expiry_fill = v; },
    fmt: (v) => (v === "SAME_MINUTE" ? "expSameMin" : "expNextOpen") },
'''
QUEUE_ANCHOR_PREFIX = "    if (Number(cfg.sl_streak_count) > 0) p.push("
QUEUE_INSERT = '''    if (Array.isArray(cfg.reentry_on) && cfg.reentry_on.length) p.push(`re:${cfg.reentry_on.map((x) => (x === "DECAY" ? `D${cfg.reentry_decay_frac}` : x === "TP" ? "TP" : "EXP")).join("+")}${cfg.reentry_ext_gate ? "·ext" : ""}`);   // ── TMA_REENTRY_20260921 ──
'''
CMP_ANCHOR_PREFIX = '  { key: "tma2_brake", label: "TMA2 SL brake",'
CMP_INSERT = '''  { key: "tma2_reentry", label: "TMA2 re-entry", get: (r) => (r.config?.ema4 && r.config?.s1 && Array.isArray(r.config.reentry_on) && r.config.reentry_on.length) ? `${r.config.reentry_on.map((x) => (x === "DECAY" ? `DECAY@${r.config.reentry_decay_frac}` : x)).join("+")} · max ${Number(r.config.reentry_max_per_trend ?? 1) || "∞"}${r.config.reentry_ext_gate ? " · ext gate" : ""}${r.config.reentry_on.includes("EXPIRY") || r.config.reentry_on.includes("TP") ? ` · ${r.config.reentry_expiry_fill || "NEXT_OPEN"}` : ""}` : null },   // ── TMA_REENTRY_20260921 ──
'''

def insert_before(text, anchor, ins):
    assert text.count(anchor) == 1, f"anchor not unique: {anchor[:50]!r} x{text.count(anchor)}"
    return text.replace(anchor, ins + anchor)

def insert_after_line(text, prefix, ins):
    lines = text.split("\n")
    hits = [i for i, l in enumerate(lines) if l.startswith(prefix)]
    assert len(hits) == 1, f"anchor line not unique: {prefix[:50]!r} x{len(hits)}"
    i = hits[0]
    return "\n".join(lines[:i + 1]) + "\n" + ins + "\n".join(lines[i + 1:])

def patch_sweep(t): return insert_before(t, SWEEP_ANCHOR, SWEEP_INSERT)
def patch_queue(t): return insert_after_line(t, QUEUE_ANCHOR_PREFIX, QUEUE_INSERT)
def patch_cmp(t): return insert_after_line(t, CMP_ANCHOR_PREFIX, CMP_INSERT)


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


class Stop(Exception):
    pass


def cut_once(t: str, s: str, what: str, n: int = 1) -> str:
    if t.count(s) != n:
        raise Stop(f"{what}: expected the inserted text x{n}, found x{t.count(s)} — the file was edited "
                   f"after the apply; nothing was changed")
    return t.replace(s, "")


def unpatch_sweep(t): return cut_once(t, SWEEP_INSERT, "SweepBuilder.jsx")
def unpatch_queue(t): return cut_once(t, QUEUE_INSERT, "BacktestQueue.jsx")
def unpatch_cmp(t): return cut_once(t, CMP_INSERT, "RunComparison.jsx")


def unpatch_backtest(t):
    t = cut_once(t, FORM_INS, "Backtest.jsx form section")
    t = cut_once(t, CHIP_INS, "Backtest.jsx config chip")
    t = cut_once(t, CFG_INS, "Backtest.jsx buildConfig keys")
    t = cut_once(t, STATE_INS, "Backtest.jsx state")
    if t.count(LS_NEW) != 1 or t.count(DEP_NEW) != 2:
        raise Stop("Backtest.jsx persist/dep-array edits not found exactly — nothing was changed")
    return t.replace(LS_NEW, LS_OLD).replace(DEP_NEW, DEP_OLD)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    be, fe = repo / "backend", repo / "frontend"
    runner = be / TMA / "backtest_tma_v2_runner.py"
    if not runner.exists():
        print("ABORT — not the scalp-app repo root. Nothing was written.")
        sys.exit(1)

    writes, deletes = {}, []
    try:
        for root_be in (be, repo / "desktop" / "src-tauri" / "backend"):
            rp = root_be / TMA / "backtest_tma_v2_runner.py"
            ep = root_be / TMA / "tma_v2_engine.py"
            tp = root_be / TMA / "test_tma_v2_reentry.py"
            if rp.exists() and FENCE in rp.read_text():
                orig = None
                bak = rp.with_name(rp.name + f".bak-{FENCE}")
                if bak.exists() and sha(bak.read_text()) == RUNNER_SHA:
                    orig = bak.read_text()
                if orig is None:
                    orig = unpack(RUNNER_ORIG)      # the GitHub-main original, carried in this script
                    assert sha(orig) == RUNNER_SHA
                writes[rp] = orig
            if ep.exists() and FENCE in ep.read_text():
                blk = unpack(ENGINE_APPEND)
                writes[ep] = cut_once(ep.read_text(), blk, str(ep.relative_to(repo)))
            if tp.exists():
                deletes.append(tp)
        for root_fe in (fe, repo / "desktop" / "src-tauri" / "frontend"):
            for rel, fn, fence in (("src/pages/backtest/SweepBuilder.jsx", unpatch_sweep, FENCE),
                                   ("src/pages/backtest/BacktestQueue.jsx", unpatch_queue, FENCE),
                                   ("src/pages/backtest/RunComparison.jsx", unpatch_cmp, FENCE),
                                   ("src/pages/Backtest.jsx", unpatch_backtest, FORM_FENCE)):
                p = root_fe / rel
                if p.exists() and fence in p.read_text():
                    writes[p] = fn(p.read_text())
    except Stop as e:
        print(f"\nABORT — {e}\nNothing was written.")
        sys.exit(1)

    for root in (be, fe, repo / "desktop" / "src-tauri"):
        if root.exists():
            for fence in (FENCE, FORM_FENCE):
                deletes += [p for p in root.rglob(f"*.bak-{fence}") if "node_modules" not in p.parts]
    if not writes and not deletes:
        print("Nothing to revert — no TMA_REENTRY fence found.")
        return

    # staged result must be fence-free and the engine must be the original
    for p, t in writes.items():
        if FENCE in t or FORM_FENCE in t:
            print(f"\nABORT — {p.relative_to(repo)} would still carry the fence after the revert. Nothing was written.")
            sys.exit(1)
    eng = be / TMA / "tma_v2_engine.py"
    if eng in writes and sha(writes[eng]) != ENGINE_SHA:
        print("note: tma_v2_engine.py has other local edits besides this patch — they are kept; only the "
              "re-entry helper block is removed")

    bdir = Path.home() / ".scalp-app" / "patch_backups" / f"TMA_REENTRY_REVERT_{time.strftime('%Y%m%d_%H%M%S')}"
    saved = []

    def restore() -> None:
        for p, b in saved:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(b, p)

    try:
        for p in list(writes) + deletes:
            b = bdir / p.relative_to(repo)
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, b)
            saved.append((p, b))
        for p, t in writes.items():
            p.write_text(t)
        for p in deletes:
            p.unlink()
    except Exception as e:                                   # noqa: BLE001
        restore()
        print(f"\nABORT — write failed ({e}); everything was put back.")
        sys.exit(1)

    # ── verify ───────────────────────────────────────────────────────────
    problems = []
    if sha(runner.read_text()) != RUNNER_SHA:
        problems.append("runner sha is not the original")
    env = dict(os.environ, PYTHONPATH=str(be))
    r = subprocess.run([sys.executable, str(be / TMA / "test_tma_v2_engine.py")], cwd=be, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        problems.append("test_tma_v2_engine.py failed:\n" + "\n".join((r.stdout + r.stderr).splitlines()[-6:]))
    esb = fe / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if esb.exists():
        for p in writes:
            if p.suffix == ".jsx" and fe in p.parents:
                q = subprocess.run([str(esb), str(p), "--loader:.jsx=jsx", "--log-level=error"],
                                   capture_output=True, text=True)
                if q.returncode != 0:
                    problems.append(f"esbuild rejected {p.name}: {q.stderr[-300:]}")
    if problems:
        restore()
        print("\nABORT — verification failed; everything was put back:\n  " + "\n  ".join(problems))
        sys.exit(1)

    print(f"reverted {len(writes)} files, deleted {len(deletes)} (test file + .bak files)")
    print("runner sha == GitHub-main original ✓   engine tests pass ✓" + ("   JSX parses ✓" if esb.exists() else ""))
    print(f"safety copies: {bdir}")
    g = subprocess.run(["git", "status", "--porcelain", "--", "backend/app/backtest/tma", "frontend/src/pages"],
                       cwd=repo, capture_output=True, text=True)
    if g.returncode == 0:
        left = [l for l in g.stdout.splitlines() if ".bak-" not in l]
        print("git status for the touched folders: " + ("clean" if not left else "\n  " + "\n  ".join(left)
              + "\n  (expected if you had committed the patch — commit this revert; or these are your other pending edits)"))
    print("\nRestart the backend and rebuild / reload the frontend.")


if __name__ == "__main__":
    main()
