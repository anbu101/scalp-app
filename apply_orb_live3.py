#!/usr/bin/env python3
# apply_orb_live3.py — ORB_V1 live phase, step 3 (FINAL): frontend +
# parity harness. Fence: ORB_LIVE3_20260903
#
# PREREQUISITE: apply_orb_live2.py — verified.
#
# Checklist coverage: 3.1 ORBPanel (two-tap square-off — no window.confirm),
# 3.2 StrategyHost (import/IDS/META/case), 3.3 api.js (offline-fallback
# state + square-off), 3.4 registry.js, 3.5 Settings (all 9 grafts; the
# form is deliberately MINIMAL — mode/lots/target only, because the
# strategy is sealed and every other key merges through backend defaults
# untouched), 3.6 PaperTrades (both spellings + CE/PE list), 3.7
# Analytics, 3.8 Connections, 3.9 AppSettingsSection. Plus the paper-vs-
# backtest parity harness (canonical_db_path, ledger-aware).
#
# USAGE (repo root):
#   python3 apply_orb_live3.py --check && python3 apply_orb_live3.py
#   then FULL tauri rebuild — the running app is a frozen bundle.

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_LIVE3_20260903'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {'frontend/src/strategies/orb/ORBPanel.jsx': '// frontend/src/strategies/orb/ORBPanel.jsx\n// ── ORB_V1 PANEL ── Fence: ORB_LIVE3_20260903. Read-only day view +\n// two-tap square-off (window.confirm is DEAD in Tauri — checklist 3.1).\nimport React, { useEffect, useState, useCallback } from "react";\nimport { getORBV1State, orbV1SquareOff } from "../../api";\n\nexport default function ORBPanel(props) {\n  const [st, setSt] = useState(null);\n  const [armKill, setArmKill] = useState(false);\n  const refresh = useCallback(async () => {\n    try { setSt(await getORBV1State()); } catch { /* keep last */ }\n  }, []);\n  useEffect(() => {\n    refresh();\n    const t = setInterval(refresh, 5000);\n    return () => clearInterval(t);\n  }, [refresh]);\n  const pos = st?.position;\n  const lv = st?.levels;\n  const chip = (txt, bg) => (\n    <span style={{ background: bg, color: "#fff", borderRadius: 4,\n                   padding: "1px 8px", fontSize: 11, marginRight: 6 }}>{txt}</span>);\n  return (\n    <div style={{ padding: 10, fontSize: 12 }}>\n      <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>\n        <b style={{ marginRight: 8 }}>ORB V1 — Outrider</b>\n        {chip(st?.mode || "…", st?.mode === "LIVE" ? "#c62828" : st?.mode === "PAPER" ? "#1565c0" : "#607d8b")}\n        {st?.frozen && chip("FROZEN", "#c62828")}\n        {st?.day?.refused && chip("DAY REFUSED", "#795548")}\n      </div>\n      <div style={{ opacity: 0.85, marginBottom: 6 }}>\n        {lv ? <>ORB {lv.low?.toFixed?.(1)} – {lv.high?.toFixed?.(1)}</> : "ORB window forming…"}\n        {"  ·  "}signals {st?.day?.signals ?? 0} · entries {st?.day?.entries ?? 0}\n        {" · exits "}{Object.entries(st?.day?.exits || {}).map(([k, v]) => `${k}:${v}`).join(" ") || "—"}\n      </div>\n      {pos ? (\n        <div style={{ border: "1px solid #455a64", borderRadius: 6, padding: 8, marginBottom: 8 }}>\n          <div><b>{pos.side}</b> {pos.symbol} × {pos.qty} @ ₹{pos.entry_price}</div>\n          <div style={{ opacity: 0.8 }}>spot stop {pos.sl_spot?.toFixed?.(2)} (1m close) · TP ₹{pos.tp_prem?.toFixed?.(2)}</div>\n        </div>\n      ) : <div style={{ opacity: 0.6, marginBottom: 8 }}>flat</div>}\n      {!armKill ? (\n        <button onClick={() => setArmKill(true)} disabled={!pos}\n                style={{ fontSize: 11 }}>Square off…</button>\n      ) : (\n        <span>\n          <button onClick={async () => { setArmKill(false); try { await orbV1SquareOff(); } catch {} refresh(); }}\n                  style={{ fontSize: 11, background: "#c62828", color: "#fff", marginRight: 6 }}>CONFIRM square-off</button>\n          <button onClick={() => setArmKill(false)} style={{ fontSize: 11 }}>cancel</button>\n        </span>\n      )}\n      <div style={{ marginTop: 8, fontSize: 10.5, opacity: 0.65 }}>\n        Sealed 2026-09-03 · entries ≤12:00 · everything closed 13:00 · docs/ORB_V1_BIBLE.pdf\n      </div>\n    </div>\n  );\n}\n', 'backend/app/engine/orb/parity_orb.py': '# backend/app/engine/orb/parity_orb.py\n#\n# ── ORB_V1 PARITY HARNESS ── Fence: ORB_LIVE3_20260903\n# Compares a day\'s PAPER rows against the backtest run of the SAME day with\n# the LIVE config. Divergences beyond the ledger are integration bugs.\n# Usage (from backend/, PYTHONPATH=$PWD):\n#   python3 app/engine/orb/parity_orb.py 2026-09-04 [2026-09-05 ...]\n\nfrom __future__ import annotations\nimport sys\nfrom datetime import date\n\n\ndef run(days):\n    from app.engine.pst.pst_common import canonical_db_path\n    from app.utils.app_paths import APP_HOME\n    from app.config.strategy_loader import STRATEGY_CONFIG\n    from app.backtest.orb.backtest_orb_runner import run_orb_backtest\n    import sqlite3\n    cfg = dict(STRATEGY_CONFIG.get("ORB_V1", {}))\n    dbp = str(APP_HOME / "backtest" / "backtest.db")\n    conn = sqlite3.connect(canonical_db_path())\n    conn.row_factory = sqlite3.Row\n    for d in days:\n        dd = date.fromisoformat(d)\n        rows = conn.execute(\n            "SELECT symbol, side, entry_price, exit_price, exit_reason,"\n            " candle_ts, qty FROM paper_trades WHERE strategy_name=\'ORB_V1\'"\n            " AND date(candle_ts, \'unixepoch\', \'+330 minutes\')=?"\n            " ORDER BY candle_ts", (d,)).fetchall()\n        bt = run_orb_backtest(db_path=dbp, strategy_id="ORB_V1",\n                              underlying=str(cfg.get("underlying", "NIFTY")),\n                              date_from=dd, date_to=dd, config_override=cfg)\n        bts = bt.get("trades", [])\n        print(f"\\n== {d}: paper {len(rows)} vs backtest {len(bts)} trades ==")\n        for i in range(max(len(rows), len(bts))):\n            p = rows[i] if i < len(rows) else None\n            b = bts[i] if i < len(bts) else None\n            if p and b:\n                dts = (p["candle_ts"] - b.entry_ts)\n                print(f"  #{i+1} side {p[\'side\']}/{b.instrument_type}"\n                      f"  entry_ts Δ{dts:+d}s"\n                      f"  entry {p[\'entry_price\']:.2f}/{b.entry_price:.2f}"\n                      f"  exit {p[\'exit_reason\']}/{b.exit_reason}"\n                      f" {p[\'exit_price\'] or 0:.2f}/{b.exit_price or 0:.2f}"\n                      + ("   <-- CHECK" if (p["side"] != b.instrument_type\n                                            or p["exit_reason"] != b.exit_reason\n                                            or abs(dts) > 120) else ""))\n            else:\n                print(f"  #{i+1} {\'PAPER-ONLY: \' + p[\'symbol\'] if p else \'BACKTEST-ONLY: \' + b.tradingsymbol}   <-- CHECK")\n        print("  ledger: entry px spread-crossed; TP exits close-vs-intrabar;"\n              " sub-second wick entries may differ. Everything else must match.")\n\n\nif __name__ == "__main__":\n    if len(sys.argv) < 2:\n        print(__doc__ or "pass ISO dates"); sys.exit(1)\n    run(sys.argv[1:])\n'}

EDITS = [('frontend/src/components/StrategyHost.jsx', 'after', 'import VETPanel     from "../strategies/vet/VETPanel.jsx";     // ── VET_V1 ──\n', 'import ORBPanel     from "../strategies/orb/ORBPanel.jsx";     // ── ORB_V1 ──\n', 1), ('frontend/src/components/StrategyHost.jsx', 'replace', 'const ACTIVE_STRATEGY_IDS = ["SCALP_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "IC_V2", "TSG_V1", "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2", "VET_V1", "BRK_V1"];\n', 'const ACTIVE_STRATEGY_IDS = ["SCALP_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "IC_V2", "TSG_V1", "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2", "VET_V1", "BRK_V1", "ORB_V1"];\n', 1), ('frontend/src/components/StrategyHost.jsx', 'after', '  VET_V1:    { name: "VET V1",        accent: "#34d399" },   // ── VET_V1 ──\n', '  ORB_V1:    { name: "ORB V1",        accent: "#f59e0b" },   // ── ORB_V1 ──\n', 1), ('frontend/src/components/StrategyHost.jsx', 'after', '    case "VET_V1":    return <VETPanel     {...common} />;   // ── VET_V1 ──\n', '    case "ORB_V1":    return <ORBPanel     {...common} />;   // ── ORB_V1 ──\n', 1), ('frontend/src/api.js', 'after', 'export const getStrategyConfig = async (strategyId) => {\n  const res = await api(`/api/config?strategy_id=${strategyId}`);\n  return res?.config;\n};\n', '\n// ── ORB_V1 BEGIN ── panel state + square-off. Same api() helper as every\n// other route; catch gives the offline fallback the dashboard needs while\n// the backend boots (40–45 s).\nexport const getORBV1State = async () => {\n  try {\n    return await api("/api/orb_v1/state");\n  } catch {\n    return { ok: false, running: false, strategy: "ORB_V1", mode: "OFF",\n             position: null, levels: null, day: {}, frozen: false };\n  }\n};\nexport const orbV1SquareOff = () =>\n  api("/api/orb_v1/square_off", { method: "POST" });\n// ── ORB_V1 END ──\n', 1), ('frontend/src/strategies/registry.js', 'before', '  // ── VET_V1 BEGIN ──\n', '  // ── ORB_V1 BEGIN ──\n  {\n    id: "ORB_V1",\n    label: "ORB V1",\n    broker: "ZERODHA",\n    timeframe: "1m",                    // static 15m ORB; decisions at 1m closes\n    modeSupported: ["PAPER", "LIVE"],\n    capabilities: {\n      hasSelection: true,   // premium band ₹150–200, weekly expiry\n      hasSlots:     false,  // OrbManager owns all state (paper_trades)\n      hasCEPE:      true,   // side = breakout direction\n    },\n  },\n  // ── ORB_V1 END ──\n', 1), ('frontend/src/pages/PaperTrades.jsx', 'after', '  "VET_V1":    "VET V1",   // ── VET_V1 ──\n', '  "ORB_V1":    "ORB V1",   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/PaperTrades.jsx', 'after', '  "VET V1": "VET_V1",\n', '  "ORB V1": "ORB_V1",\n', 1), ('frontend/src/pages/PaperTrades.jsx', 'after', '  "VET_V1", "VET V1",      // ── VET_V1 ── legs carry side CE/PE (main + wing)\n', '  "ORB_V1", "ORB V1",      // ── ORB_V1 ── single long leg, side CE/PE\n', 1), ('frontend/src/pages/Analytics.jsx', 'after', '  { id: "VET_V1",    label: "VET V1",    color: "#34d399", desc: "Dual-EMA 10/20 + regime channel @5m spot · buy or sell by config · intraday or overnight carry · exits on trend flip, no SL/TP" },   // ── VET_V1 ──\n', '  { id: "ORB_V1",    label: "ORB V1",    color: "#f59e0b", desc: "15m opening-range breakout · long weekly options · +50/60% premium target · 0.04% spot stop on 1m closes · all flat by 13:00" },   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/Connections.jsx', 'after', '  VET_V1:    "#34d399",   // ── VET_V1 ──\n', '  ORB_V1:    "#f59e0b",   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/Connections.jsx', 'after', '  { value: "VET_V1",    title: "VET V1" },   // ── VET_V1 ──\n', '  { value: "ORB_V1",    title: "ORB V1" },   // ── ORB_V1 ──\n', 1), ('frontend/src/components/AppSettingsSection.jsx', 'after', '  { id: "VET_V1",   name: "VET V1",        accent: "#34d399" },   // ── VET_V1 ── (added 2026-08-29)\n', '  { id: "ORB_V1",   name: "ORB V1",        accent: "#f59e0b" },   // ── ORB_V1 ── (added 2026-09-03)\n', 1), ('frontend/src/pages/Settings.jsx', 'before', '\n// ── VET_V1 BEGIN ──\n', '\n// ── ORB_V1 BEGIN ── SEALED strategy: only these three knobs are\n// user-facing; every other parameter is frozen backend-side by the\n// 2026-09-03 seal (docs/ORB_V1_BIBLE.pdf) and survives saves untouched\n// because the saved payload merges OVER backend defaults.\nconst DEFAULT_ORB_CONFIG = {\n  trade_execution_mode: "PAPER",\n  lots: 1,\n  target_value: 50,        // 50 = Config A (risk-first) · 60 = Config B\n};\n// ── ORB_V1 END ──\n', 1), ('frontend/src/pages/Settings.jsx', 'before', '  // ── VET_V1 BEGIN ──\n  const [vetConfig, setVetConfig] = useState(null);\n', '  // ── ORB_V1 BEGIN ──\n  const [orbConfig, setOrbConfig] = useState(null);\n  const [orbStatus, setOrbStatus] = useState("");\n  const [orbSaving, setOrbSaving] = useState(false);\n  // ── ORB_V1 END ──\n', 1), ('frontend/src/pages/Settings.jsx', 'before', '  // ── VET_V1 BEGIN ── load / update / save. Saved payload merged OVER\n', '  // ── ORB_V1 BEGIN ── load / update / save. Sealed strategy — the form\n  // exposes mode/lots/target only; everything else lives in backend\n  // defaults and merges through unharmed.\n  async function loadORB() {\n    try {\n      const d = await getStrategyConfig("ORB_V1");\n      setOrbConfig({ ...DEFAULT_ORB_CONFIG, ...d });\n    } catch { setOrbConfig({ ...DEFAULT_ORB_CONFIG }); }\n  }\n  const updateORB = (k, v) => setOrbConfig((c) => ({ ...c, [k]: v }));\n  async function saveORB() {\n    setOrbSaving(true); setOrbStatus("");\n    try {\n      await saveStrategyConfig("ORB_V1", orbConfig);\n      setOrbStatus("Saved.");\n    } catch (e) { setOrbStatus("Save failed: " + e); }\n    setOrbSaving(false);\n  }\n  // ── ORB_V1 END ──\n', 1), ('frontend/src/pages/Settings.jsx', 'replace', '  useEffect(() => { loadScalp(); loadBB(); loadBBV2(); loadHA(); loadScalpV3(); loadScalpV5(); IC_SIDS.forEach(loadIC); loadPstSell(); loadPstHedge(); loadTMA(); loadTMA2(); loadTSG(); loadVET(); loadBRK(); }, []);   // ← TSG_V1, TMA_V2, VET_V1, BRK_V1 added\n', '  useEffect(() => { loadScalp(); loadBB(); loadBBV2(); loadHA(); loadScalpV3(); loadScalpV5(); IC_SIDS.forEach(loadIC); loadPstSell(); loadPstHedge(); loadTMA(); loadTMA2(); loadTSG(); loadVET(); loadBRK(); loadORB(); }, []);   // ← TSG_V1, TMA_V2, VET_V1, BRK_V1 added\n', 1), ('frontend/src/pages/Settings.jsx', 'after', '  VET_V1:   { name: "VET V1",       sub: "NIFTY 5m trend · buy or sell, intraday or carry" },   // ── VET_V1 ──\n', '  ORB_V1:   { name: "ORB V1",       sub: "15m ORB breakout · sealed · flat by 13:00" },   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/Settings.jsx', 'after', '    { id: "VET_V1",   mode: vetConfig.trade_execution_mode },   // ── VET_V1 ──\n', '    { id: "ORB_V1",   mode: orbConfig.trade_execution_mode },   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/Settings.jsx', 'after', '    VET_V1:   { mode: vetConfig.trade_execution_mode,     onSave: saveVET,     saving: vetSaving,     status: vetStatus },   // ── VET_V1 ──\n', '    ORB_V1:   { mode: orbConfig.trade_execution_mode,     onSave: saveORB,     saving: orbSaving,     status: orbStatus },   // ── ORB_V1 ──\n', 1), ('frontend/src/pages/Settings.jsx', 'before', '      case "VET_V1": return (<>\n', '      case "ORB_V1": return (<>\n              {/* ── ORB_V1 BEGIN ── sealed strategy — three knobs only. */}\n              <div style={{ fontSize: 12, marginBottom: 10, opacity: 0.85 }}>\n                Static 15m opening-range breakout on NIFTY, long weekly options.\n                Entries ≤12:00, everything closed by 13:00, spot stop 0.04%\n                evaluated on 1m closes, max 2 trades/day (never reduce — the two\n                daily trades hedge each other\'s regimes). All of that is SEALED;\n                only the three fields below are meant to change.\n              </div>\n              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 10 }}>\n                <label style={{ fontSize: 12 }}>Mode<br/>\n                  <select value={orbConfig.trade_execution_mode}\n                          onChange={(e) => updateORB("trade_execution_mode", e.target.value)}>\n                    <option value="OFF">OFF</option>\n                    <option value="PAPER">PAPER</option>\n                    <option value="LIVE">LIVE</option>\n                  </select>\n                </label>\n                <label style={{ fontSize: 12 }}>Lots<br/>\n                  <input type="number" min="1" style={{ width: 70 }} value={orbConfig.lots}\n                         onChange={(e) => updateORB("lots", Number(e.target.value) || 1)} />\n                </label>\n                <label style={{ fontSize: 12 }}>Profit target<br/>\n                  <select value={Number(orbConfig.target_value)}\n                          onChange={(e) => updateORB("target_value", Number(e.target.value))}>\n                    <option value={50}>Config A — +50% (risk-first)</option>\n                    <option value={60}>Config B — +60% (profit-first)</option>\n                  </select>\n                </label>\n              </div>\n              <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 8 }}>\n                Promotion gate: ≥2 expiry cycles of PAPER with paper-vs-backtest\n                parity before LIVE. Full doctrine: <i>docs/ORB_V1_BIBLE.pdf</i>.\n              </div>\n              {/* ── ORB_V1 END ── */}\n            </>);\n', 1)]

VERIFY = [('frontend/src/components/StrategyHost.jsx', 'ORB_V1', 4), ('frontend/src/api.js', 'getORBV1State', 1), ('frontend/src/strategies/registry.js', '"ORB_V1"', 1), ('frontend/src/pages/PaperTrades.jsx', 'ORB', 3), ('frontend/src/pages/Analytics.jsx', '"ORB_V1"', 1), ('frontend/src/pages/Connections.jsx', 'ORB_V1', 2), ('frontend/src/components/AppSettingsSection.jsx', '"ORB_V1"', 1), ('frontend/src/pages/Settings.jsx', 'ORB_V1', 6), ('backend/app/engine/orb/parity_orb.py', 'ORB_LIVE3_20260903', 1)]



def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    """A backend-relative path lands in both trees; frontend in one."""
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def stage_edit(text, kind, anchor, payload, count, path):
    n = text.count(anchor)
    if kind == "replaceall":
        if n != count:
            fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
        return text.replace(anchor, payload)
    if n != count:
        fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
    if kind == "replace":
        return text.replace(anchor, payload)
    if kind == "before":
        return text.replace(anchor, payload + anchor)
    if kind == "after":
        return text.replace(anchor, anchor + payload)
    fail(f"unknown edit kind {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--single-tree", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard "
             "requirement locally; pass --single-tree only on a CI checkout")

    # ── prerequisite ──
    probe = os.path.join(ROOT, "backend", "app", "engine", "orb",
                         "orb_manager.py")
    if not os.path.exists(probe):
        fail("apply_orb_live2.py must be applied first")
    probe2 = os.path.join(ROOT, "frontend", "src", "strategies", "orb",
                          "ORBPanel.jsx")
    if os.path.exists(probe2):
        print(f"  SKIP   ORBPanel already present — "
              f"nothing to do")
        return

    # ── stage every write in memory first ──
    staged = {}   # abs path -> new text
    for rel, body in PAYLOADS.items():
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                fail(f"{p} already exists (half-applied tree?)")
            staged[p] = body
    per_file = {}
    for rel, kind, anchor, payload, count in EDITS:
        per_file.setdefault(rel, []).append((kind, anchor, payload, count))
    for rel, ops in per_file.items():
        src_path = os.path.join(ROOT, rel)
        if not os.path.exists(src_path):
            fail(f"{src_path} not found")
        text = open(src_path, encoding="utf-8").read()
        if FENCE in text:
            fail(f"{rel} already carries the fence — mixed state, resolve by hand")
        for kind, anchor, payload, count in ops:
            text = stage_edit(text, kind, anchor, payload, count, rel)
        for p in both_trees(rel, a.single_tree):
            if p != src_path and not os.path.exists(p):
                fail(f"dual-tree copy missing: {p}")
            staged[p] = text

    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── staged compile gates ──
    tmp = tempfile.mkdtemp(prefix="orv_gate_")
    jsx_targets = []
    for p, body in staged.items():
        t = os.path.join(tmp, os.path.basename(p))
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        elif p.endswith((".jsx", ".js")):
            jsx_targets.append((p, t))
    print(f"  OK     py_compile gate passed")
    esb = shutil.which("esbuild")
    npx = shutil.which("npx")
    for p, t in jsx_targets:
        cmd = None
        if esb:
            cmd = [esb, "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        elif npx:
            cmd = [npx, "--yes", "esbuild", "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        if cmd is None:
            print(f"  WARN   esbuild unavailable — JSX gate skipped for {p}")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "frontend"))
        if r.returncode != 0:
            fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
    if jsx_targets and (esb or npx):
        print(f"  OK     esbuild JSX gate passed ({len(jsx_targets)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  write {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write, with backups for edited files ──
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    # ── grep-count verification ──
    bad = 0
    for rel, needle, mn in VERIFY:
        got = open(os.path.join(ROOT, rel), encoding="utf-8").read().count(needle)
        ok = got >= mn
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} (need >= {mn})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   ORB_V1 frontend + parity harness applied. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD npm run tauri build   # frozen bundle — source edits are invisible until rebuilt")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
