#!/usr/bin/env python3
# apply_scalp_v1_bt_filters_ui_20260823.py
#
# Frontend wiring for SCALP_V1 backtest filters — fence: SCALP_V1_BT_FILTERS_UI_20260823
# Companion to apply_scalp_v1_bt_filters_20260823.py (backend). Run AFTER it.
#
# Adds entry_blackout + max_trades_per_day to every SCALP_V1 UI surface:
#   frontend/src/pages/Backtest.jsx           state, saveParams(+deps),
#                                             buildConfig(+deps), form fields,
#                                             describeConfig blackout chip
#   frontend/src/pages/backtest/BacktestQueue.jsx   paramLine blackout token
#   frontend/src/pages/backtest/RunComparison.jsx   PARAM_KEYS blackout row
#   frontend/src/pages/backtest/SweepBuilder.jsx    V1 blackout + cap axes
#
# NOT touched: max_trades_per_day display — already generic in all four
# surfaces ("Day cap" chip / "day cap" token / day_cap PARAM_KEY).
#
# CONTRACTS HONORED:
#  * SHARED_EXEC_FIELDS lesson — keys emitted ONLY for SCALP_V1 (!hedge),
#    and OMITTED when off, so unfiltered runs stay byte-identical.
#  * Stale-closure rule — every new state read by buildConfig/saveParams
#    lands in the matching dep array in this same commit.
#  * RUN_PARAMS_DISPLAY tripwire — blackout renders a chip; a filtered run
#    whose chip is missing means the config never left the form.
#  * No eslint-disable anywhere.
#
# Idempotent: aborts if fence already present. Run from repo root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_BT_FILTERS_UI_20260823"
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "frontend" / "src"

BT = SRC / "pages" / "Backtest.jsx"
QU = SRC / "pages" / "backtest" / "BacktestQueue.jsx"
RC = SRC / "pages" / "backtest" / "RunComparison.jsx"
SW = SRC / "pages" / "backtest" / "SweepBuilder.jsx"


def _die(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def _replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        _die(f"anchor '{label}' matched {n} times (want 1) — NOTHING written")
    return text.replace(old, new, 1)


# ═══ Backtest.jsx ══════════════════════════════════════════════════════════

B1_OLD = "  const [riskMaxSl, setRiskMaxSl] = useState(saved.riskMaxSl ?? 0);"
B1_NEW = """  const [riskMaxSl, setRiskMaxSl] = useState(saved.riskMaxSl ?? 0);
  // ── SCALP_V1_BT_FILTERS_UI_20260823 ── V1-only backtest entry filters.
  // Defaults mirror backend strategy_loader defaults (OFF / 12:00–14:00 / 0).
  const [v1BoEnabled, setV1BoEnabled] = useState(saved.v1BoEnabled ?? false);
  const [v1BoStart, setV1BoStart] = useState(saved.v1BoStart ?? "12:00");
  const [v1BoEnd, setV1BoEnd] = useState(saved.v1BoEnd ?? "14:00");
  const [v1MaxTradesDay, setV1MaxTradesDay] = useState(saved.v1MaxTradesDay ?? 0);"""

B2_OLD = "      maxTradesDay });   // ── HA_DAILY_CAP ──"
B2_NEW = """      maxTradesDay,   // ── HA_DAILY_CAP ──
      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay });   // ── SCALP_V1_BT_FILTERS_UI_20260823 ──"""

B3_OLD = "      maxTradesDay]);   // ── HA_DAILY_CAP ──"
B3_NEW = """      maxTradesDay,   // ── HA_DAILY_CAP ──
      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay]);   // ── SCALP_V1_BT_FILTERS_UI_20260823 ── stale-closure rule: saveParams reads them, so they land here in the SAME commit"""

B4_OLD = """      session: { primary: { start: sessStart, end: sessEnd } },
      quantity: { lots: Number(lots) },
    };
    if (hedge) {"""
B4_NEW = """      session: { primary: { start: sessStart, end: sessEnd } },
      quantity: { lots: Number(lots) },
    };
    // ── SCALP_V1_BT_FILTERS_UI_20260823 BEGIN ── V1-only by design
    // (SHARED_EXEC_FIELDS lesson: the V3 hedge runner doesn't read these
    // keys, so they must never leak into a V3 config). Keys are OMITTED
    // when off — backend defaults are OFF — so unfiltered runs stay
    // byte-identical to pre-filter configs in RunComparison.
    if (!hedge) {
      if (v1BoEnabled) cfg.entry_blackout = { enabled: true, start: v1BoStart, end: v1BoEnd };
      if (Number(v1MaxTradesDay) > 0) cfg.max_trades_per_day = Number(v1MaxTradesDay);
    }
    // ── SCALP_V1_BT_FILTERS_UI_20260823 END ──
    if (hedge) {"""

B5_OLD = "      vapEmaPeriod, vapEmaBasis, vapVolMult, vapVolLookback]);"
B5_NEW = """      vapEmaPeriod, vapEmaBasis, vapVolMult, vapVolLookback,
      v1BoEnabled, v1BoStart, v1BoEnd, v1MaxTradesDay]);   // ── SCALP_V1_BT_FILTERS_UI_20260823 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"""

B6_OLD = """              <Field label="Risk Max SL"><input type="number" style={inputStyle} value={riskMaxSl} onChange={(e) => setRiskMaxSl(e.target.value)} /></Field>
            </>
          )}"""
B6_NEW = """              <Field label="Risk Max SL"><input type="number" style={inputStyle} value={riskMaxSl} onChange={(e) => setRiskMaxSl(e.target.value)} /></Field>
            </>
          )}
          {/* ── SCALP_V1_BT_FILTERS_UI_20260823 ── V1-only entry filters.
              Blackout is HALF-OPEN [start, end) on the entry STAMP (candle
              close): a 14:00 entry is allowed, a 12:00 entry is blocked.
              Cap 0 = off. Both default off — baseline runs are unchanged. */}
          {strategyId === "SCALP_V1" && (
            <>
              <Field label="Entry blackout">
                <select style={inputStyle} value={v1BoEnabled ? "1" : "0"} onChange={(e) => setV1BoEnabled(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>
              {v1BoEnabled && (
                <>
                  <Field label="Blackout start"><input type="text" style={inputStyle} value={v1BoStart} onChange={(e) => setV1BoStart(e.target.value)} /></Field>
                  <Field label="Blackout end"><input type="text" style={inputStyle} value={v1BoEnd} onChange={(e) => setV1BoEnd(e.target.value)} /></Field>
                </>
              )}
              <Field label="Max trades/day"><input type="number" min="0" style={inputStyle} value={v1MaxTradesDay} onChange={(e) => setV1MaxTradesDay(e.target.value)} /></Field>
            </>
          )}"""

B7_OLD = "  // ── HA_DAILY_CAP ──\n  if (Number(cfg.max_trades_per_day) > 0) add(\"Day cap\", `${cfg.max_trades_per_day}/day`);"
B7_NEW = """  // ── SCALP_V1_BT_FILTERS_UI_20260823 ── RUN_PARAMS_DISPLAY tripwire: if a
  // run was meant to skip midday entries and this chip is missing, the config
  // never left the form. (Day cap below already covers max_trades_per_day.)
  if (cfg.entry_blackout?.enabled) add("Blackout", `${cfg.entry_blackout.start}–${cfg.entry_blackout.end}`);
  // ── HA_DAILY_CAP ──
  if (Number(cfg.max_trades_per_day) > 0) add("Day cap", `${cfg.max_trades_per_day}/day`);"""

# ═══ BacktestQueue.jsx ═════════════════════════════════════════════════════

Q1_OLD = "  if (Number(cfg.max_trades_per_day) > 0) p.push(`day cap ${cfg.max_trades_per_day}`);"
Q1_NEW = """  // ── SCALP_V1_BT_FILTERS_UI_20260823 ── job-label token: a queued sweep
  // over blackout on/off must be tellable apart in the queue table.
  if (cfg.entry_blackout?.enabled) p.push(`bo ${cfg.entry_blackout.start}-${cfg.entry_blackout.end}`);
  if (Number(cfg.max_trades_per_day) > 0) p.push(`day cap ${cfg.max_trades_per_day}`);"""

# ═══ RunComparison.jsx ═════════════════════════════════════════════════════

R1_OLD = '  { key: "day_cap",          label: "Max trades/day", get: (r) => (Number(r.config?.max_trades_per_day) > 0 ? String(r.config.max_trades_per_day) : null) },'
R1_NEW = """  // ── SCALP_V1_BT_FILTERS_UI_20260823 ── a blackout-filtered run and an
  // unfiltered run must never diff as identical params in the matrix.
  { key: "entry_blackout",   label: "Entry blackout", get: (r) => (r.config?.entry_blackout?.enabled ? `${r.config.entry_blackout.start}–${r.config.entry_blackout.end}` : null) },
  { key: "day_cap",          label: "Max trades/day", get: (r) => (Number(r.config?.max_trades_per_day) > 0 ? String(r.config.max_trades_per_day) : null) },"""

# ═══ SweepBuilder.jsx ══════════════════════════════════════════════════════

S1_OLD = """  { key: "risk_max_sl", label: "Risk Max SL", strategies: [V1, V3],
    hint: "0, 10, 15", parse: _num,
    apply: (c, v) => { c.risk_max_sl_points = v; }, fmt: (v) => `rMaxSL ${v}` },"""
S1_NEW = """  { key: "risk_max_sl", label: "Risk Max SL", strategies: [V1, V3],
    hint: "0, 10, 15", parse: _num,
    apply: (c, v) => { c.risk_max_sl_points = v; }, fmt: (v) => `rMaxSL ${v}` },
  // ── SCALP_V1_BT_FILTERS_UI_20260823 ── V1 entry-filter axes. Blackout is
  // a 0/1 axis (IV12keep pattern) over the validated 12:00–14:00 window;
  // cap 0 = off, matching runner semantics. Omit-when-off both.
  { key: "v1_blackout", label: "V1 blackout 12–14 (0/1)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.entry_blackout = { enabled: true, start: "12:00", end: "14:00" }; }, fmt: (v) => (v ? "bo12-14" : "no-bo") },
  { key: "v1_cap", label: "V1 max trades/day", strategies: [V1],
    hint: "0, 10, 12, 15, 20", parse: _num,
    apply: (c, v) => { if (v > 0) c.max_trades_per_day = v; }, fmt: (v) => (v > 0 ? `cap ${v}` : "no cap") },"""


EDITS = [
    (BT, [("B1", B1_OLD, B1_NEW), ("B2", B2_OLD, B2_NEW), ("B3", B3_OLD, B3_NEW),
          ("B4", B4_OLD, B4_NEW), ("B5", B5_OLD, B5_NEW), ("B6", B6_OLD, B6_NEW),
          ("B7", B7_OLD, B7_NEW)]),
    (QU, [("Q1", Q1_OLD, Q1_NEW)]),
    (RC, [("R1", R1_OLD, R1_NEW)]),
    (SW, [("S1", S1_OLD, S1_NEW)]),
]


def main():
    if not BT.exists():
        _die("run from the scalp-app repo root (frontend/src/pages/Backtest.jsx not found)")

    texts = {}
    for path, edits in EDITS:
        t = path.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {path.name} — already applied")
        for label, old, new in edits:
            t = _replace_once(t, old, new, f"{path.name}:{label}")
        texts[path] = t

    # All anchors verified across ALL files before writing ANY.
    for path, t in texts.items():
        path.write_text(t)
        print(f"PATCHED: {path}")

    print()
    print(f"DONE — fence {FENCE} applied to 4 files.")
    print()
    print("VERIFY (their standard): from frontend/, run the esbuild JSX check:")
    print("  npx esbuild src/pages/Backtest.jsx src/pages/backtest/BacktestQueue.jsx \\")
    print("      src/pages/backtest/RunComparison.jsx src/pages/backtest/SweepBuilder.jsx \\")
    print("      --loader:.jsx=jsx --bundle=false --outdir=/tmp/esb_check")
    print()
    print("NOTES:")
    print(" * New fields render ONLY when SCALP V1 is selected: Entry blackout")
    print("   (Off/On + start/end when On) and Max trades/day (0 = off).")
    print(" * Keys are omitted when off -> baseline configs byte-identical.")
    print(" * SweepBuilder gains V1 axes: blackout 0/1 and cap list —")
    print("   e.g. cap '0, 10, 12, 15, 20' x blackout '0, 1' = 10-run sweep.")
    print(" * Backend script must be applied first (backtest_runner reads the")
    print("   keys); UI without backend would silently no-op the filters.")


if __name__ == "__main__":
    main()
