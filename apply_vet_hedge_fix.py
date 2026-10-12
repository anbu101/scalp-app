#!/usr/bin/env python3
# apply_vet_hedge_fix.py — fence VET_HEDGE_FIX_20261012
#
# VET V1: the Settings page SHOWED a hedge wing (Hedge Wing Max ₹3) while the
# saved config had hedge_enabled = false — so paper/live sold NAKED shorts.
#
# ROOT CAUSE. The live manager arms the wing only when
#     leg_action == SELL  and  hedge_enabled  and  hedge_max_premium > 0.
# The defaults are hedge_enabled = false, hedge_max_premium = 3, and the
# Settings field set hedge_enabled ONLY when the ₹ field itself was edited.
# Switching Leg Action to SELL and saving left the visible "3" in place and
# hedge_enabled false. (The backtest form derives hedge_enabled from the ₹
# field, so backtests DID hedge — the wing is folded into each row by design,
# and the CSV export did not include VET's diagnostics, so it was invisible.)
#
# FIX
#   1. Settings → VET V1 → Save derives hedge_enabled from what the form shows:
#      SELL and Wing Max > 0 → wing ON; 0 → naked. What you see is what runs.
#   2. Settings shows a red warning while the SAVED config disagrees with the
#      field ("wing shown but OFF — shorts are naked; press Save").
#   3. The VET runtime raises an in-app alert every session it runs SELL with
#      a wing cap set but hedge_enabled false (never silently naked again).
#      It does NOT flip the flag itself — a risk change needs your Save.
#   4. Backtest CSV export now includes VET's diagnostics (DIAG section:
#      hedge_real, hedge_synth, hedge_cost_total, hedge_synth_pnl_gross,
#      no_hedge_entries …), so the wing is visible in the export.
#
#  EDIT  frontend/src/pages/Settings.jsx (2 anchored edits) · frontend/src/pages/Backtest.jsx (1)
#        backend/app/engine/vet/vet_selection_loop.py (1 insert)
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Gates: py_compile, esbuild, the VET live suites (manager, DTE lots, signal engine, core).
#
# AFTER APPLYING: rebuild both, open Settings → VET V1, press Save (the
# warning disappears), then check the next entry's audit line reads
# "[VET][MGR] OPEN … + wing NIFTY…". Wing legs appear as their own rows on
# the Trades page (LONG, same group as the short).
#
# Usage: python3 apply_vet_hedge_fix.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script.

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "VET_HEDGE_FIX_20261012"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
SUITES = ["app/engine/vet/test_vet_manager.py", "app/engine/vet/test_vet_live_dte_lots.py",
          "app/engine/vet/test_vet_live_signal_engine.py", "app/engine/vet/test_vet_live_core.py"]
S = "frontend/src/pages/Settings.jsx"
B = "frontend/src/pages/Backtest.jsx"
L = "backend/app/engine/vet/vet_selection_loop.py"
EDITS = [
    (S, '''  async function saveVET() {
    setVetSaving(true);
    try {
      await saveStrategyConfig("VET_V1", vetConfig);''', '''  async function saveVET() {
    setVetSaving(true);
    try {
      // ── VET_HEDGE_FIX_20261012 ── hedge_enabled is DERIVED from what the form
      // shows (SELL + Wing Max > 0 → wing ON; 0 → naked). Before this, the flag
      // flipped only when the ₹ field itself was edited, so a SELL config could
      // display "₹3" while trading naked.
      const toSave = { ...vetConfig,
        hedge_enabled: String(vetConfig.leg_action || "").toUpperCase() === "SELL" && Number(vetConfig.hedge_max_premium) > 0 };
      await saveStrategyConfig("VET_V1", toSave);
      setVetConfig(toSave);''', 1),
    (S, '''                    onChange={(e) => { const v = Math.max(0, Number(e.target.value) || 0); updateVET(["hedge_max_premium"], v); updateVET(["hedge_enabled"], v > 0); }}
                    style={{ maxWidth: 100 }} />
                </Field>''', '''                    onChange={(e) => { const v = Math.max(0, Number(e.target.value) || 0); updateVET(["hedge_max_premium"], v); updateVET(["hedge_enabled"], v > 0); }}
                    style={{ maxWidth: 100 }} />
                  {/* ── VET_HEDGE_FIX_20261012 ── the saved flag must match what is shown */}
                  {String(vetConfig.leg_action || "").toUpperCase() === "SELL" && Number(vetConfig.hedge_max_premium) > 0 && !vetConfig.hedge_enabled && (
                    <div style={{ color: "#f87171", fontSize: 11.5, fontWeight: 600, marginTop: 6, maxWidth: 380 }}>
                      ⚠ The SAVED config has the wing OFF (hedge_enabled = false) — paper/live shorts are NAKED although ₹{vetConfig.hedge_max_premium} is shown. Press Save to switch the wing on.
                    </div>
                  )}
                  {String(vetConfig.leg_action || "").toUpperCase() === "SELL" && !(Number(vetConfig.hedge_max_premium) > 0) && (
                    <div style={{ color: "#fbbf24", fontSize: 11.5, marginTop: 6, maxWidth: 380 }}>
                      Wing Max 0 → shorts are sold NAKED (unbounded tail, full margin).
                    </div>
                  )}
                </Field>''', 1),
    (B, '''summary?.diag_emc);''', '''summary?.diag_emc || summary?.diag_vet);   /* ── VET_HEDGE_FIX_20261012 ── VET diag (wing counters) in the CSV */''', 1),
    (L, '''    manager.apply_day_lots(today)
''', '''    manager.apply_day_lots(today)
    # ── VET_HEDGE_FIX_20261012 ── a SELL config that DISPLAYS a wing cap but has
    # hedge_enabled false sells NAKED shorts. Say so every session; never flip
    # the flag here (a risk change needs the owner's Save in Settings).
    try:
        _cap = float(cfg.get("hedge_max_premium") or 0)
        if manager.is_sell and not manager.wants_wing and _cap > 0:
            _msg = (f"VET SELL is trading NAKED: hedge_enabled is false although Hedge Wing Max shows "
                    f"₹{_cap:g}. Settings → VET V1 → Save to switch the wing on (or set it to 0 to "
                    f"sell naked on purpose).")
            write_audit_log(f"[VET][HEDGE_OFF] {_msg}")
            from app.event_bus.inapp_events import record_alert
            record_alert("VET_HEDGE_OFF", _msg, severity="warning", strategy_id=STRATEGY_ID,
                         mode=("live" if str(mode).upper() == "LIVE" else "paper"))
    except Exception as _e:
        write_audit_log(f"[VET][HEDGE_OFF][CHECK_FAIL] {_e!r}")
''', 1),
]


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
    if "HEDGE_LEG" not in (repo / "backend/app/backtest/vet/backtest_vet_runner.py").read_text(encoding="utf-8"):
        die("prerequisite HEDGE_LEG not found in the VET runner")
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
        tp = Path(td) / "loop.py"
        tp.write_text(staged[L], encoding="utf-8")
        try:
            py_compile.compile(str(tp), doraise=True)
        except py_compile.PyCompileError as e:
            die(f"py_compile failed: {e}")
        print("  ✓ py_compile")
        esb = next((str(c) for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild")
                    if c.exists()), None) or shutil.which("esbuild")
        if esb:
            for rel in (S, B):
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print("  ✓ esbuild parsed Settings.jsx and Backtest.jsx")
        else:
            print("  ! esbuild not found — parse gate skipped (the frontend build will check)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c", "import app.engine.vet.vet_selection_loop; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("vet_selection_loop no longer imports")
        print("  ✓ vet_selection_loop imports")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                continue
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=1800)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL" in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    for rel in targets:
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied. Rebuild both (./desktop/build-scalp.sh both), then Settings → VET V1 → Save.")


if __name__ == "__main__":
    main()
