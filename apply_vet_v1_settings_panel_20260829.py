#!/usr/bin/env python3
# apply_vet_v1_settings_panel_20260829.py
#
# ── VET_V1 SETTINGS PANEL ── the nine Settings.jsx grafts (checklist Part 4)
# ============================================================================
# One strategy, four sealed configs, all reachable from THIS panel: leg
# action (BUY/SELL), trade style (intraday/positional via eod_square), the
# hedge wing budget, trend length and ATM offset. The panel carries a static
# "sealed configurations" reference card so the frozen parameter sets are
# readable exactly where they are typed — the user asked for this
# specifically ("I will forget which configs to run for what").
#
# DELIBERATELY ABSENT from the UI (LD2 pattern, backend keys only, so the UI
# can never drift them): signal_tf 5m, range_len 0.618, EMA pair 10/20,
# warmup_sessions 10, wing_mode real_fallback. These are the study's frozen
# identity; changing them is a code decision with a backtest behind it.
#
# SL/TP ARE EXPOSED BUT DEFAULT 0 with an explicit warning helper: every
# sealed config runs without them, and the SL-under-SELL sweep has not been
# run — a non-zero value leaves the studied configs.
#
# The 9 grafts (each assert-anchored on the TMA_V2/TSG blocks):
#   1 DEFAULT_VET_CONFIG   2 strategy card   3 state triple   4 boot loader
#   5 load/update/save     6 loading gate    7 mode chip      8 save map
#   9 panel case
#
# Idempotent, staged esbuild check, dual-tree.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_settings_panel_20260829.py --dry-run
#   python3 apply_vet_v1_settings_panel_20260829.py

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.getcwd()
TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
         (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"),
          "desktop-fe")]
SETTINGS = os.path.join("pages", "Settings.jsx")


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:90]}")


# 1 ── defaults (mirror strategy_loader; UI-hidden frozen keys ABSENT so a
#      save can never unset them — the merge in loadVET preserves them)
G1_A = "\n// ── TMA_V2 END ──"
G1_N = G1_A + '''
// ── VET_V1 BEGIN ──
// Mirrors backend strategy_loader defaults (sealed NIFTY Buy B, intraday,
// unhedged). Frozen study keys (signal_tf, range_len, ema pair, warmup,
// wing_mode) are deliberately NOT here and NOT rendered — the saved config
// is merged OVER these defaults, so the UI can never drop or drift them.
const DEFAULT_VET_CONFIG = {
  trade_execution_mode: "PAPER",
  leg_action: "BUY",
  eod_square: true,
  hedge_enabled: false,
  hedge_max_premium: 3,
  trend_len: 36,
  atm_offset: -1,
  entry_cutoff: "15:00",
  exit_time: "15:15",
  sl_pct: 0,
  tp_pct: 0,
  max_trades_per_day: 0,
  quantity: { lot_size: 65, lots: 10 },
};
// ── VET_V1 END ──'''

# 2 ── strategy card
G2_A = ('  TMA_V2:   { name: "TMA V2",       sub: "NIFTY weekly · 4-EMA '
        'stack credit spread" },   // ── TMA_V2 ──')
G2_N = G2_A + ('\n  VET_V1:   { name: "VET V1",       sub: "NIFTY 5m trend · '
               'buy or sell, intraday or carry" },   // ── VET_V1 ──')

# 3 ── state triple
G3_A = '''  // ── TSG_V1 BEGIN ──
  const [tsgConfig, setTsgConfig] = useState(null);'''
G3_N = '''  // ── VET_V1 BEGIN ──
  const [vetConfig, setVetConfig] = useState(null);
  const [vetStatus, setVetStatus] = useState("");
  const [vetSaving, setVetSaving] = useState(false);
  // ── VET_V1 END ──
''' + G3_A

# 4 ── boot loader
G4_A = ("loadTMA(); loadTMA2(); loadTSG(); }, []);   "
        "// ← TSG_V1, TMA_V2 added")
G4_N = ("loadTMA(); loadTMA2(); loadTSG(); loadVET(); }, []);   "
        "// ← TSG_V1, TMA_V2, VET_V1 added")

# 5 ── load / update / save
G5_A = '''  // ── TSG_V1 BEGIN ── load / update / save (legs merged by index so a
  partial saved config never renders undefined leg inputs)'''
G5_A = None  # anchor computed against the real text below

G5_ANCHOR = "  // ── TSG_V1 BEGIN ── load / update / save"
G5_N = '''  // ── VET_V1 BEGIN ── load / update / save. Saved payload merged OVER
  // defaults so backend-only frozen keys (signal_tf, range_len, EMA pair,
  // warmup_sessions, wing_mode) survive every save untouched.
  async function loadVET() {
    try {
      const d = await getStrategyConfig("VET_V1");
      setVetConfig({
        ...DEFAULT_VET_CONFIG, ...d,
        quantity: { ...DEFAULT_VET_CONFIG.quantity, ...(d?.quantity || {}) },
      });
    } catch { setVetConfig({ ...DEFAULT_VET_CONFIG }); }
  }
  function updateVET(path, value) {
    const u = structuredClone(vetConfig);
    path.reduce((o, k, i) => { if (i === path.length - 1) o[k] = value; return o[k]; }, u);
    setVetConfig(u);
  }
  async function saveVET() {
    setVetSaving(true);
    try {
      await saveStrategyConfig("VET_V1", vetConfig);
      setVetStatus("success"); setTimeout(() => setVetStatus(""), 3000);
    } catch {
      setVetStatus("error");  setTimeout(() => setVetStatus(""), 3000);
    } finally { setVetSaving(false); }
  }
  // ── VET_V1 END ──

''' + G5_ANCHOR

# 6 ── loading gate
G6_A = "|| !tmaConfig || !tma2Config || !tsgConfig) {"
G6_N = "|| !tmaConfig || !tma2Config || !tsgConfig || !vetConfig) {"

# 7 ── mode chip
G7_A = ('    { id: "TMA_V2",   mode: tma2Config.trade_execution_mode },   '
        '// ── TMA_V2 ──')
G7_N = G7_A + ('\n    { id: "VET_V1",   mode: vetConfig.trade_execution_mode },'
               '   // ── VET_V1 ──')

# 8 ── save map
G8_A = ('    TMA_V2:   { mode: tma2Config.trade_execution_mode,    onSave: '
        'saveTMA2,    saving: tma2Saving,    status: tma2Status },   '
        '// ── TMA_V2 ──')
G8_N = G8_A + ('\n    VET_V1:   { mode: vetConfig.trade_execution_mode,     '
               'onSave: saveVET,     saving: vetSaving,     status: vetStatus '
               '},   // ── VET_V1 ──')

# 9 ── the panel
G9_A = '      case "TMA_V2": return (<>'
G9_N = '''      case "VET_V1": return (<>
              {/* ── VET_V1 BEGIN ── One runtime, four sealed configs — all
                  reachable from these fields. Frozen and NOT rendered (LD2):
                  signal_tf 5m, range 0.618, EMA 10/20, warmup 10 sessions,
                  wing_mode real_fallback (live wings are REAL contracts or
                  the entry is skipped — never synthetic). */}
              <Group title="Execution">
                <Field label="Mode" helper="PAPER = simulated at candle closes · LIVE = real orders. In SELL mode the wing is BOUGHT FIRST, then the short — a failed short sells the wing back; the account is never briefly naked.">
                  <ModeToggle value={vetConfig.trade_execution_mode}
                    onChange={(v) => updateVET(["trade_execution_mode"], v)} />
                </Field>
                <Field label="Leg Action" helper="BUY: up-trend buys CE, down-trend buys PE — loss bounded at premium. SELL: up-trend SHORTS the PE, down-trend SHORTS the CE — theta becomes income, the tail becomes unbounded unless winged, and capital is SPAN margin.">
                  <Select value={vetConfig.leg_action}
                    onChange={(e) => updateVET(["leg_action"], e.target.value)}
                    style={{ maxWidth: 240 }}>
                    <option value="BUY">BUY options (long)</option>
                    <option value="SELL">SELL options (short)</option>
                  </Select>
                </Field>
                <Field label="Trade Style" helper="INTRADAY squares off daily at Exit Time. POSITIONAL carries overnight — the sealed sell config's mode; ~30% of its positions hold overnight and gap nights are real MTM calls. A contract is closed on its own expiry day in BOTH styles.">
                  <Select value={vetConfig.eod_square ? "INTRADAY" : "POSITIONAL"}
                    onChange={(e) => updateVET(["eod_square"], e.target.value === "INTRADAY")}
                    style={{ maxWidth: 260 }}>
                    <option value="INTRADAY">INTRADAY — square off daily</option>
                    <option value="POSITIONAL">POSITIONAL — carry overnight</option>
                  </Select>
                </Field>
                <Field label="Hedge Wing Max ₹ (SELL only, 0 = naked)" helper="Protective long option of the same type & expiry, dearest REAL contract at or under this. No real wing under the cap → the entry is SKIPPED, never taken bare. Backtests priced some wings synthetically; live never does — expect fewer sell entries than the study at tight caps.">
                  <Input type="number" min="0" step="0.5"
                    disabled={vetConfig.leg_action !== "SELL"}
                    value={vetConfig.hedge_max_premium}
                    onChange={(e) => { const v = Math.max(0, Number(e.target.value) || 0); updateVET(["hedge_max_premium"], v); updateVET(["hedge_enabled"], v > 0); }}
                    style={{ maxWidth: 100 }} />
                </Field>
                <Field label="Entry Cutoff" helper="New entries only. A FLIP after cutoff degrades to exit-only (backtest parity) — the book ends the window flat or holding, never freshly entered.">
                  <Input value={vetConfig.entry_cutoff}
                    onChange={(e) => updateVET(["entry_cutoff"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Exit Time" helper="INTRADAY square-off moment. Ignored by POSITIONAL except on a contract's expiry day.">
                  <Input value={vetConfig.exit_time}
                    onChange={(e) => updateVET(["exit_time"], e.target.value)}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Lots" helper="NIFTY lot 65. The sealed studies are all at 10 lots — scale to the drawdown you can hold (Buy B ₹12.6L · Sell Positional ₹10.8L at 10 lots), not the net you'd like.">
                  <Input type="number" min="1" value={vetConfig.quantity.lots}
                    onChange={(e) => updateVET(["quantity", "lots"], Math.max(1, Number(e.target.value)))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="Max Trades/Day" helper="0 = unlimited. One position is open at a time regardless.">
                  <Input type="number" min="0" value={vetConfig.max_trades_per_day}
                    onChange={(e) => updateVET(["max_trades_per_day"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 100 }} />
                </Field>
              </Group>
              <Group title="Signal">
                <Field label="Trend Length" helper="Bars in the regime SMA/ATR. The 30–52 band is one profit plateau: 36 = sealed Buy B / DIXON, 40 = sealed Buy A / Sell. Values outside the plateau leave the studied configs.">
                  <Input type="number" min="10" value={vetConfig.trend_len}
                    onChange={(e) => updateVET(["trend_len"], Math.max(10, Number(e.target.value)))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="ATM Offset (strikes)" helper="Negative = in-the-money (what the sealed configs use: −1 for B, −2 for A and Sell). Positive = out-of-the-money.">
                  <Input type="number" value={vetConfig.atm_offset}
                    onChange={(e) => updateVET(["atm_offset"], Number(e.target.value))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="SL % of entry premium (0 = off)" helper="⚠ Every sealed config runs 0 — the exit IS the signal. SL was falsified under BUY, and the SL-under-SELL sweep has NOT been run. Non-zero here is an unstudied configuration.">
                  <Input type="number" min="0" value={vetConfig.sl_pct}
                    onChange={(e) => updateVET(["sl_pct"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 90 }} />
                </Field>
                <Field label="TP % of entry premium (0 = off)" helper="⚠ Same caveat as SL — 0 in every sealed config.">
                  <Input type="number" min="0" value={vetConfig.tp_pct}
                    onChange={(e) => updateVET(["tp_pct"], Math.max(0, Number(e.target.value)))}
                    style={{ maxWidth: 90 }} />
                </Field>
              </Group>
              <Group title="Sealed configurations (reference)">
                <div style={{ fontSize: 12.5, lineHeight: 1.55, opacity: 0.85 }}>
                  <div><b>NIFTY Buy A</b> — BUY · INTRADAY · trend 40 · offset −2 · SL/TP 0 · exit 15:15 &nbsp;(net ₹43.6L · DD ₹14.3L)</div>
                  <div><b>NIFTY Buy B</b> — BUY · INTRADAY · trend 36 · offset −1 · SL/TP 0 · exit 15:15 &nbsp;(net ₹39.0L · DD ₹12.6L) ← defaults</div>
                  <div><b>Sell Positional + Wing</b> — SELL · POSITIONAL · trend 40 · offset −2 · wing ≤₹3 · SL/TP 0 &nbsp;(net ₹106.1L · DD ₹10.8L; 57% of study wings were synthetic — live takes fewer entries)</div>
                  <div><b>DIXON Monthly Buy</b> — stock options: NOT wired live; backtest only</div>
                  <div style={{ marginTop: 6 }}>Run A <b>or</b> B, never both — same trades seconds apart. Full doctrine, worked trades and honest limitations: <i>VET_V1_Strategy_Bible.pdf</i>.</div>
                </div>
              </Group>
              {/* ── VET_V1 END ── */}
            </>);
''' + G9_A


def edit_settings(t):
    if "DEFAULT_VET_CONFIG" in t:
        return t, 0
    grafts = [(G1_A, G1_N, "1 defaults"), (G2_A, G2_N, "2 card"),
              (G3_A, G3_N, "3 state"), (G4_A, G4_N, "4 boot loader"),
              (G5_ANCHOR, G5_N, "5 load/save"), (G6_A, G6_N, "6 gate"),
              (G7_A, G7_N, "7 chip"), (G8_A, G8_N, "8 save map"),
              (G9_A, G9_N, "9 panel")]
    for a, _n, lbl in grafts:
        one(t, a, "Settings:" + lbl)
    for a, n, _lbl in grafts:
        t = t.replace(a, n, 1)
    return t, 9


def find_esbuild(canary):
    cands = []
    loc = os.path.join(REPO, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.isfile(loc) and os.access(loc, os.X_OK):
        cands.append(([loc], "node_modules"))
    p = shutil.which("esbuild")
    if p:
        cands.append(([p], "PATH"))
    npx = shutil.which("npx")
    if npx:
        cands += [([npx, "--no", "esbuild"], "npx"),
                  ([npx, "--no-install", "esbuild"], "npx7")]
    for c, w in cands:
        try:
            if subprocess.run(c + ["--log-level=silent", canary],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=90).returncode == 0:
                return c, w
        except Exception:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        path = os.path.join(root, SETTINGS)
        if not os.path.isfile(path):
            die(f"[{label}] missing {path}")
        out, n = edit_settings(open(path).read())
        if n == 0:
            notes.append(f"[{label}] SKIP (already wired): {SETTINGS}")
        else:
            writes[path] = out
            notes.append(f"[{label}] EDIT ({n} grafts): {SETTINGS}")
    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── JSX SYNTAX CHECK ─────────────────────────────────────────")
    if a.skip_jsx_check:
        print("  skipped by request")
    else:
        tmp = tempfile.mkdtemp(prefix="vet_set_")
        try:
            can = os.path.join(tmp, "c.jsx")
            open(can, "w").write("const A = () => <div>{1}</div>;\n")
            cmd, where = find_esbuild(can)
            if cmd is None:
                print("  !! no working esbuild — check SKIPPED (not an error)")
            else:
                print(f"  esbuild via {where}")
                for i, (dest, body) in enumerate(writes.items()):
                    st = os.path.join(tmp, f"s{i}.jsx")
                    open(st, "w").write(body)
                    r = subprocess.run(cmd + ["--log-level=warning", st],
                                       capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=120)
                    if r.returncode != 0:
                        die(f"esbuild FAILED for {dest}:\n{r.stderr[:1500]}")
                print(f"  {len(writes)} file(s) parse clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. VET_V1 panel live in Settings (defaults = sealed Buy B).")


if __name__ == "__main__":
    main()
