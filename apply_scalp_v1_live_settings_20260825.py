#!/usr/bin/env python3
# apply_scalp_v1_live_settings_20260825.py
#
# LIVE DEPLOYMENT — fence: SCALP_V1_LIVE_SETTINGS_20260825
# Deploy decision (Anbu, 25 Aug 2026): the redesigned SCALP_V1 goes to
# paper/live. Three changes:
#
# 1. SETTINGS PAGE (Settings.jsx, SCALP_V1 section): new "Signal Gates
#    (Redesign)" group — EMA Gate (enabled/period/lookback/min slope),
#    TP Multiplier, VWAP Filter (enabled/min pts below). Nested keys ride
#    the existing getStrategyConfig/saveStrategyConfig round-trip (load
#    spreads the full server config; updateScalp path-sets nested values).
#    NOTE: the live surface CLAMPS min_below_pts >= 0 — the negative
#    "tolerance band" stays a backtest-only exploration, never a live one.
#
# 2. FRESH ENTRY HARDCODED ON (strategy_engine.py): per deploy decision.
#    The boundary-burst killer is now unconditional — both fail-safe
#    default and config read forced True; require_fresh_entry key ignored.
#    PARITY NOTE: this also makes every FUTURE backtest fresh-entry-only,
#    which matches the sealed config. Historical run configs keep the key
#    for display.
#
# 3. BACKTEST UI CLEANUP (Backtest.jsx, SweepBuilder.jsx): the Fresh entry
#    control, its emission, state, dep entries and sweep axis are REMOVED
#    (a knob that no longer does anything must not render). Chips, queue
#    tokens and the RunComparison row REMAIN — they display stored configs
#    of historical runs.
#
# DEPLOYMENT CLASS: live-shared path + Settings surface. Rebuild and ship
# on a NON-TRADING DAY (or after close, per house rules). After applying:
# if backend/app/config/gen_strategy_defaults.py exists in your tree, run
# it to regenerate strategy_defaults.json (admin UI surface).
#
# PREREQS: FRESH_ENTRY + EMA_GATE + VWAP fences. Idempotent. Run from root.

import sys
from pathlib import Path

FENCE = "SCALP_V1_LIVE_SETTINGS_20260825"
ROOT = Path(__file__).resolve().parent
SE_REL = "app/engine/strategy_engine.py"
SET_JSX = ROOT / "frontend" / "src" / "pages" / "Settings.jsx"
BT_JSX = ROOT / "frontend" / "src" / "pages" / "Backtest.jsx"
SW_JSX = ROOT / "frontend" / "src" / "pages" / "backtest" / "SweepBuilder.jsx"
TREES = [ROOT / "backend"]
_d = ROOT / "desktop" / "src-tauri" / "backend"
if (_d / SE_REL).exists():
    TREES.append(_d)


def _die(m):
    print(f"ABORT: {m}")
    sys.exit(1)


def _ro(t, o, n, lab, count=1):
    c = t.count(o)
    if c != count:
        _die(f"anchor '{lab}' matched {c} times (want {count}) — NOTHING written")
    return t.replace(o, n)


# ═══ strategy_engine.py — fresh entry hardcoded ON ═════════════════════════

E1_OLD = "        fresh_req    = False    # ── SCALP_V1_FRESH_ENTRY_20260824 ── fail-safe"
E1_NEW = "        fresh_req    = True     # ── SCALP_V1_LIVE_SETTINGS_20260825 ── HARDCODED ON (deploy decision: boundary-burst killer, backtest-validated)"

E2_OLD = '            fresh_req = bool(cfg.get("require_fresh_entry", False))   # ── SCALP_V1_FRESH_ENTRY_20260824 ──'
E2_NEW = "            # ── SCALP_V1_LIVE_SETTINGS_20260825 ── fresh entry is hardcoded\n            # ON above; the require_fresh_entry config key is intentionally\n            # IGNORED (retained only so historical run configs still display)."

# ═══ Settings.jsx — Signal Gates group ═════════════════════════════════════

SET1_OLD = '''              <Field label="Risk / Reward" helper="Target-to-stop multiplier">
                <Input type="number" step="0.1" min="0" value={scalpConfig.risk_reward_ratio}
                  onChange={(e) => updateScalp(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            <Group title="Risk Limits (Daily)">'''
SET1_NEW = '''              <Field label="Risk / Reward" helper="Target-to-stop multiplier">
                <Input type="number" step="0.1" min="0" value={scalpConfig.risk_reward_ratio}
                  onChange={(e) => updateScalp(["risk_reward_ratio"], Math.max(0, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
            </Group>

            {/* ── SCALP_V1_LIVE_SETTINGS_20260825 ── redesign gates (sealed
                backtest config: RR 1 · EMA gate 89/30 ≥1 · TP 3.5× · session
                10:00–15:00). Fresh entry is HARDCODED ON in the engine. */}
            <Group title="Signal Gates (Redesign)">
              <Field label="EMA Gate" helper="Sell only when the gate EMA of the premium has FALLEN ≥ min slope over the lookback. Unwarmed slope blocks entries (fail-closed).">
                <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: colors.text.secondary, userSelect: "none", cursor: "pointer" }}>
                  <input type="checkbox" checked={!!scalpConfig.ema_gate?.enabled}
                    onChange={(e) => updateScalp(["ema_gate", "enabled"], e.target.checked)}
                    style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0 }} />
                  Enabled
                </label>
              </Field>
              {!!scalpConfig.ema_gate?.enabled && (<>
                <Field label="Gate EMA Period" helper="Sealed config: 89 · keep ≤ 300 (warmup depth)">
                  <Input type="number" min="10" max="300" value={scalpConfig.ema_gate?.period ?? 144}
                    onChange={(e) => updateScalp(["ema_gate", "period"], Math.max(10, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Slope Lookback (bars)" helper="Slope measured across this many 1-minute bars · sealed: 30">
                  <Input type="number" min="1" value={scalpConfig.ema_gate?.slope_lookback ?? 30}
                    onChange={(e) => updateScalp(["ema_gate", "slope_lookback"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
                <Field label="Min Slope (pts)" helper="Required decline over the lookback · sealed: 1">
                  <Input type="number" min="0" step="0.1" value={scalpConfig.ema_gate?.min_slope_pts ?? 0}
                    onChange={(e) => updateScalp(["ema_gate", "min_slope_pts"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              </>)}
              <Field label="TP Multiplier" helper="Target sits risk × this below entry · 1 = classic prev-red-low · sealed: 3.5">
                <Input type="number" min="0.5" step="0.1" value={scalpConfig.tp_multiplier ?? 1}
                  onChange={(e) => updateScalp(["tp_multiplier"], Math.max(0.5, Number(e.target.value)))}
                  style={{ maxWidth: 120 }} />
              </Field>
              <Field label="VWAP Filter" helper="Sell only when the premium closes below its session average by ≥ min pts. OFF in the sealed config (regime-shaped in backtest); available as the labelled challenger.">
                <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: colors.text.secondary, userSelect: "none", cursor: "pointer" }}>
                  <input type="checkbox" checked={!!scalpConfig.vwap_filter?.enabled}
                    onChange={(e) => updateScalp(["vwap_filter", "enabled"], e.target.checked)}
                    style={{ width: 13, height: 13, accentColor: colors.primary, flexShrink: 0 }} />
                  Enabled
                </label>
              </Field>
              {!!scalpConfig.vwap_filter?.enabled && (
                <Field label="Min Pts Below" helper="0 = below by any amount · live surface clamps to ≥ 0">
                  <Input type="number" min="0" step="0.5" value={scalpConfig.vwap_filter?.min_below_pts ?? 0}
                    onChange={(e) => updateScalp(["vwap_filter", "min_below_pts"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 120 }} />
                </Field>
              )}
            </Group>

            <Group title="Risk Limits (Daily)">'''

# ═══ Backtest.jsx — remove the now-inert Fresh entry control ═══════════════

B1_OLD = """  // ── SCALP_V1_FRESH_ENTRY_20260824 ──
  const [v1FreshEntry, setV1FreshEntry] = useState(saved.v1FreshEntry ?? false);
"""
B1_NEW = ""

B2_OLD = "      v1FreshEntry,   // ── SCALP_V1_FRESH_ENTRY_20260824 ──\n"
B2_NEW = ""          # appears 3x: saveParams object, saveParams deps, buildConfig deps

B3_OLD = """      if (v1FreshEntry) cfg.require_fresh_entry = true;   // ── SCALP_V1_FRESH_ENTRY_20260824 ── omit-when-off
"""
B3_NEW = ""

B4_OLD = '''              {/* ── SCALP_V1_FRESH_ENTRY_20260824 ── only enter when the
                  conditions flipped true THIS candle; kills session-open
                  bursts at any boundary. */}
              <Field label="Fresh entry">
                <select style={inputStyle} value={v1FreshEntry ? "1" : "0"} onChange={(e) => setV1FreshEntry(e.target.value === "1")}>
                  <option value="0">Off</option>
                  <option value="1">On</option>
                </select>
              </Field>
'''
B4_NEW = "              {/* ── SCALP_V1_LIVE_SETTINGS_20260825 ── fresh entry is\n                  hardcoded ON in the engine; control removed. */}\n"

W1_OLD = """  // ── SCALP_V1_FRESH_ENTRY_20260824 ── 0/1 axis.
  { key: "v1_fresh", label: "V1 fresh entry (0/1)", strategies: [V1],
    hint: "0, 1", parse: _num,
    apply: (c, v) => { if (v) c.require_fresh_entry = true; }, fmt: (v) => (v ? "fresh" : "stale-ok") },
"""
W1_NEW = ""


def main():
    if not (ROOT / "backend" / SE_REL).exists():
        _die("run from the scalp-app repo root")
    staged = []
    for tree in TREES:
        p = tree / SE_REL
        t = p.read_text()
        if FENCE in t:
            _die(f"fence {FENCE} already present in {p}")
        for pf in ("SCALP_V1_FRESH_ENTRY_20260824", "SCALP_V1_EMA_GATE_20260824",
                   "SCALP_V1_VWAP_20260825"):
            if pf not in t:
                _die(f"prerequisite fence {pf} MISSING in {p}")
        t = _ro(t, E1_OLD, E1_NEW, f"{tree.name}:E1")
        t = _ro(t, E2_OLD, E2_NEW, f"{tree.name}:E2")
        staged.append((p, t))
    ts = SET_JSX.read_text()
    if FENCE in ts:
        _die(f"fence {FENCE} already present in Settings.jsx")
    ts = _ro(ts, SET1_OLD, SET1_NEW, "Settings:SET1")
    staged.append((SET_JSX, ts))
    tb = BT_JSX.read_text()
    if FENCE in tb:
        _die(f"fence {FENCE} already present in Backtest.jsx")
    tb = _ro(tb, B1_OLD, B1_NEW, "Backtest:B1")
    tb = _ro(tb, B2_OLD, B2_NEW, "Backtest:B2", count=3)
    tb = _ro(tb, B3_OLD, B3_NEW, "Backtest:B3")
    tb = _ro(tb, B4_OLD, B4_NEW, "Backtest:B4")
    staged.append((BT_JSX, tb))
    tw = SW_JSX.read_text()
    tw = _ro(tw, W1_OLD, W1_NEW, "Sweep:W1")
    staged.append((SW_JSX, tw))
    for p, t in staged:
        if p.suffix == ".py":
            try:
                compile(t, str(p), "exec")
            except SyntaxError as e:
                _die(f"staged content for {p} does not compile: {e}")
    for p, t in staged:
        p.write_text(t)
        print(f"PATCHED: {p}")
    print(f"\nDONE — fence {FENCE} applied.")
    print()
    print("POST-APPLY:")
    print(" 1. If backend/app/config/gen_strategy_defaults.py exists in your")
    print("    tree, run it (regenerates the admin defaults surface).")
    print(" 2. Rebuild BOTH trees via ./desktop/build-scalp.sh; verify the")
    print("    dual-tree diff before building. NON-TRADING-DAY ship.")
    print(" 3. In Settings > Scalp V1, set the sealed paper config:")
    print("      Mode PAPER · R:R 1 · Min SL 5 · Max SL Cap 20 · Risk Max SL 0")
    print("      EMA Gate ON, period 89, lookback 30, min slope 1")
    print("      TP Multiplier 3.5 · VWAP Filter OFF")
    print("      Session 10:00-15:00 (existing session settings)")
    print(" 4. First-session acceptance: no entries before 10:00; gate blocks")
    print("    visible in audit; trades/day ~5-6; EOD square-off 15:15.")


if __name__ == "__main__":
    main()
