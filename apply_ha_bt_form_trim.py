#!/usr/bin/env python3
# apply_ha_bt_form_trim.py — trim the HA_V1 Backtest run form.
#
# Fence: HA_BT_FORM_TRIM_20260922        File: frontend/src/pages/Backtest.jsx only
# PREREQUISITE: revert_ha_cond1_flip.py applied (HA_COND1_FLIP absent from
#               Backtest.jsx) — the anchors here sit where that fence used to be.
#
# REMOVED from the HA_V1 form (Anbu, 2026-09-22 — falsified backtest-only
# experiments cluttering the run page):
#   Max SL cap           shown for HA Sell ONLY now (its runner is the only one
#                        that reads max_sl_points; HA_V1's buy runner ignores it).
#                        max_sl_points is emitted for HA_SELL alone, so an HA_V1
#                        run header no longer shows a meaningless "Max SL cap 20" chip.
#   C1 retrace / Retrace frac / Retrace TTL bars   fields + state + emission gone
#   C1 / C2 / C3 window                            fields + state + emission gone
# KEPT: Max trades / day, Entry Conditions chips, everything else. The runner
# is untouched: cond1_retrace / condition_windows are simply never sent, which
# is the runner's legacy bit-identical path. Old runs that carry those keys still
# render their "C1 retrace" / "Cond windows" chips in the results header and
# Compare Runs (describeConfig untouched), and the SweepBuilder axes for them
# remain available for anyone who wants to re-run the experiment from a sweep.
#
# GATES before writing: 8 anchors x1, residue scan (no orphaned c1r*/cw* state
# refs), esbuild parse of the result. .bak-FENCE backup; failed verification
# restores the file.
#
# Frontend only: ./desktop/build-scalp.sh frontend
# USAGE: python3 apply_ha_bt_form_trim.py --check && python3 apply_ha_bt_form_trim.py

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tempfile

FENCE = "HA_BT_FORM_TRIM_20260922"
ROOT = os.path.dirname(os.path.abspath(__file__))
TARGET = "frontend/src/pages/Backtest.jsx"
RESIDUE = ("c1rEnabled", "c1rFrac", "c1rTtl", "cwC1s", "cwC1e", "cwC2s", "cwC2e", "cwC3s", "cwC3e",
           "HA_COND1_RETRACE BEGIN", "HA_COND_WINDOWS BEGIN", "_pair(", "_hm(")

EDITS = [
    ('state hooks',
     '  // ── HA_COND1_RETRACE BEGIN ── COND1-only limit-retrace entry (HA_V1 ONLY —\n  // backtest_ha_sell_runner does not read the key, and the SHARED_EXEC_FIELDS\n  // lesson says hidden form state must never leak into a config that doesn\'t\n  // read it, so buildConfig emits cond1_retrace only for HA_V1 and only when\n  // enabled). frac = retrace fraction of entry-SL risk (limit = entry −\n  // frac×risk); ttl_bars = 1m bars the resting limit lives before cancelling.\n  const [c1rEnabled, setC1rEnabled] = useState(saved.c1rEnabled ?? false);\n  const [c1rFrac, setC1rFrac] = useState(saved.c1rFrac ?? 0.5);\n  const [c1rTtl, setC1rTtl] = useState(saved.c1rTtl ?? 5);\n  // ── HA_COND1_RETRACE END ──\n  // ── HA_COND_WINDOWS BEGIN ── optional per-condition entry windows (HA_V1\n  // only). Empty start OR end for a condition = no window = that condition\n  // follows the global session, so blank fields preserve existing behaviour.\n  const [cwC1s, setCwC1s] = useState(saved.cwC1s ?? "");\n  const [cwC1e, setCwC1e] = useState(saved.cwC1e ?? "");\n  const [cwC2s, setCwC2s] = useState(saved.cwC2s ?? "");\n  const [cwC2e, setCwC2e] = useState(saved.cwC2e ?? "");\n  const [cwC3s, setCwC3s] = useState(saved.cwC3s ?? "");\n  const [cwC3e, setCwC3e] = useState(saved.cwC3e ?? "");\n  // ── HA_COND_WINDOWS END ──\n',
     "  // ── HA_BT_FORM_TRIM_20260922 ── C1 retrace + per-condition window form state removed (backtest-only\n  // experiments, falsified). The runner keys cond1_retrace / condition_windows\n  // are simply never emitted now; the runner's legacy path is bit-identical.\n"),
    ('save entries',
     '      c1rEnabled, c1rFrac, c1rTtl,   // ── HA_COND1_RETRACE ──\n      cwC1s, cwC1e, cwC2s, cwC2e, cwC3s, cwC3e,   // ── HA_COND_WINDOWS ──\n',
     ''),
    ('save deps',
     '      c1rEnabled, c1rFrac, c1rTtl,   // ── HA_COND1_RETRACE ── stale-closure rule\n      cwC1s, cwC1e, cwC2s, cwC2e, cwC3s, cwC3e,   // ── HA_COND_WINDOWS ──\n',
     ''),
    ('emit max_sl',
     '        max_sl_points: Number(maxSl),\n        target_override: { enabled: !!haTargetOverride, points: Number(haTargetPoints) },\n',
     '        // ── HA_BT_FORM_TRIM_20260922 ── max_sl_points is read by the HA Sell runner only; the HA_V1\n        // buy runner ignores it, so it is emitted for HA_SELL alone (no stale cap chip).\n        ...(sid === "HA_SELL" ? { max_sl_points: Number(maxSl) } : {}),\n        target_override: { enabled: !!haTargetOverride, points: Number(haTargetPoints) },\n'),
    ('emit retrace+windows',
     '      // ── HA_COND1_RETRACE BEGIN ── HA_V1 ONLY (the sell runner doesn\'t read\n      // it — SHARED_EXEC_FIELDS lesson), and EMITTED ONLY WHEN ENABLED so a\n      // disabled form never ships a stale object the chips would then render.\n      // Key absent → runner takes the legacy bit-identical path.\n      if (sid === "HA_V1" && c1rEnabled) {\n        haCfg.cond1_retrace = {\n          enabled: true,\n          frac: Number(c1rFrac) || 0.5,\n          ttl_bars: Number(c1rTtl) || 5,\n        };\n      }\n      // ── HA_COND1_RETRACE END ──\n      // ── HA_COND_WINDOWS ── HA_V1 only; a condition is emitted ONLY with a\n      // COMPLETE start+end pair, so blank fields never ship (absent = global\n      // session in the runner — existing settings preserved).\n      if (sid === "HA_V1") {\n        // Canonical HH:MM or null: trims, pads "9:30" → "09:30" (the runner\n        // compares zero-padded strings), rejects anything malformed.\n        const _hm = (v) => {\n          const m = String(v || "").trim().match(/^(\\d{1,2}):(\\d{2})$/);\n          if (!m) return null;\n          const h = Number(m[1]), mi = Number(m[2]);\n          if (h > 23 || mi > 59) return null;\n          return `${String(h).padStart(2, "0")}:${m[2]}`;\n        };\n        const _cw = {};\n        const _pair = (k, sV, eV) => {\n          const s2 = _hm(sV), e2 = _hm(eV);\n          if (s2 && e2) _cw[k] = { start: s2, end: e2 };\n        };\n        _pair("COND1", cwC1s, cwC1e);\n        _pair("COND2", cwC2s, cwC2e);\n        _pair("COND3", cwC3s, cwC3e);\n        if (Object.keys(_cw).length) haCfg.condition_windows = _cw;\n',
     '      // ── HA_BT_FORM_TRIM_20260922 ── cond1_retrace / condition_windows no longer emitted (form removed).\n      if (sid === "HA_V1") {\n'),
    ('build deps',
     '      c1rEnabled, c1rFrac, c1rTtl,   // ── HA_COND1_RETRACE ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit\n      cwC1s, cwC1e, cwC2s, cwC2e, cwC3s, cwC3e, maxTradesDay,   // ── HA_COND_WINDOWS / HA_DAILY_CAP ── stale-closure rule\n',
     '      maxTradesDay,   // ── HA_DAILY_CAP ── stale-closure rule\n'),
    ('form max sl',
     '                  entries whose SL distance (entry − red-low) is below this. */}\n              <Field label="Risk:Reward"><input type="number" step="0.1" style={inputStyle} value={rr} onChange={(e) => setRr(e.target.value)} /></Field>\n              <Field label="Min SL pts"><input type="number" style={inputStyle} value={minSl} onChange={(e) => setMinSl(e.target.value)} /></Field>\n              <Field label="Max SL cap"><input type="number" style={inputStyle} value={maxSl} onChange={(e) => setMaxSl(e.target.value)} /></Field>\n',
     '                  entries whose SL distance (entry − red-low) is below this. */}\n              <Field label="Risk:Reward"><input type="number" step="0.1" style={inputStyle} value={rr} onChange={(e) => setRr(e.target.value)} /></Field>\n              <Field label="Min SL pts"><input type="number" style={inputStyle} value={minSl} onChange={(e) => setMinSl(e.target.value)} /></Field>\n              {/* ── HA_BT_FORM_TRIM_20260922 ── Max SL cap is a seller stop clamp: HA Sell only */}\n              {strategyId === "HA_SELL" && (\n                <Field label="Max SL cap"><input type="number" style={inputStyle} value={maxSl} onChange={(e) => setMaxSl(e.target.value)} /></Field>\n              )}\n'),
    ('form fields',
     '              {/* ── HA_COND1_RETRACE BEGIN ── HA_V1 only (sell runner doesn\'t\n                  read the key). Limit-retrace entry for COND1 signals: limit =\n                  entry − frac×(entry−SL), resting ttl bars then cancelled.\n                  COND2/COND3 always enter immediately regardless. */}\n              {strategyId === "HA_V1" && (\n                <>\n                  <Field label="C1 retrace">\n                    <select style={inputStyle} value={c1rEnabled ? "1" : "0"} onChange={(e) => setC1rEnabled(e.target.value === "1")}>\n                      <option value="0">Off (market entry)</option>\n                      <option value="1">On (limit retrace)</option>\n                    </select>\n                  </Field>\n                  <Field label="Retrace frac">\n                    <input type="number" step="0.05" min="0.05" max="0.95" style={inputStyle} value={c1rFrac} disabled={!c1rEnabled} onChange={(e) => setC1rFrac(e.target.value)} />\n                  </Field>\n                  <Field label="Retrace TTL bars">\n                    <input type="number" min="1" style={inputStyle} value={c1rTtl} disabled={!c1rEnabled} onChange={(e) => setC1rTtl(e.target.value)} />\n                  </Field>\n                  {/* ── HA_COND_WINDOWS BEGIN ── optional per-condition entry\n                      windows. Blank = that condition follows the global\n                      session (existing behaviour). Windows only NARROW the\n                      session — the global session gate still applies. */}\n                  {/* PLAIN TEXT inputs on purpose (Session start/end\n                      convention). type="time" is a WebKit segmented control\n                      whose value stays "" until every segment incl. AM/PM\n                      commits — typed values rendered but never reached state,\n                      so condition_windows was silently never emitted (the\n                      missing run-header chip is exactly what caught it). */}\n                  {[["C1", cwC1s, setCwC1s, cwC1e, setCwC1e],\n                    ["C2", cwC2s, setCwC2s, cwC2e, setCwC2e],\n                    ["C3", cwC3s, setCwC3s, cwC3e, setCwC3e]].map(([label, s, setS, e, setE]) => (\n                    <Field key={label} label={`${label} window (optional)`}>\n                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>\n                        <input type="text" placeholder="10:00" style={{ ...inputStyle, width: 64 }} value={s} onChange={(ev) => setS(ev.target.value)} />\n                        <span style={{ color: "#6b7280" }}>–</span>\n                        <input type="text" placeholder="11:00" style={{ ...inputStyle, width: 64 }} value={e} onChange={(ev) => setE(ev.target.value)} />\n                      </div>\n                    </Field>\n                  ))}\n                  {/* ── HA_COND_WINDOWS END ── */}\n                  {/* ── HA_DAILY_CAP ── total across both sides; 0 = off. */}\n                  <Field label="Max trades / day (0 = off)">\n                    <input type="number" min="0" style={inputStyle} value={maxTradesDay} onChange={(e) => setMaxTradesDay(e.target.value)} />\n                  </Field>\n                </>\n              )}\n              {/* ── HA_COND1_RETRACE END ── */}\n',
     '              {/* ── HA_BT_FORM_TRIM_20260922 ── C1 retrace + condition-window fields removed. */}\n              {/* ── HA_DAILY_CAP ── total across both sides; 0 = off. */}\n              {strategyId === "HA_V1" && (\n                <Field label="Max trades / day (0 = off)">\n                  <input type="number" min="0" style={inputStyle} value={maxTradesDay} onChange={(e) => setMaxTradesDay(e.target.value)} />\n                </Field>\n              )}\n'),
]


def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry run, write nothing")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="proceed although git shows local edits on Backtest.jsx "
                         "(expected if the flip revert is applied but not yet committed)")
    a = ap.parse_args()

    p = os.path.join(ROOT, TARGET)
    if not os.path.exists(p):
        fail(f"{TARGET} not found — run from the scalp-app repo root")
    src = open(p, encoding="utf-8").read()
    if FENCE in src:
        print(f"  SKIP   {FENCE} already present — nothing to do")
        return
    if "HA_COND1_FLIP" in src:
        fail("HA_COND1_FLIP still present in Backtest.jsx — run revert_ha_cond1_flip.py first")
    for name, old, new in EDITS:
        c = src.count(old)
        if c != 1:
            fail(f"anchor [{name}] x{c}, expected x1 — Backtest.jsx drifted from the "
                 f"post-revert GitHub main (2026-09-22); inspect by hand")

    status = ""
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--", TARGET], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        status = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        pass
    if status and not a.allow_dirty:
        fail("Backtest.jsx has uncommitted edits:\n         " + status
             + "\n         if that is just the flip revert you have not committed yet, commit it"
               "\n         first (preferred) or re-run with --allow-dirty")
    print("  OK     prerequisite + 8 anchors verified" + (" (dirty tree allowed)" if status else ""))

    new = src
    for name, old, nw in EDITS:
        new = new.replace(old, nw)
    for tok in RESIDUE:
        if tok in new:
            fail(f"internal: residue {tok!r} after edit")
    if new.count(FENCE) != 5:
        fail(f"internal: fence count {new.count(FENCE)} != 5")
    if new.count("maxTradesDay") < 5:
        fail("internal: daily-cap wiring lost")

    tmp = os.path.join(tempfile.mkdtemp(prefix="ha_trim_gate_"), "Backtest.jsx")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    esb, npx = shutil.which("esbuild"), shutil.which("npx")
    cmd = ([esb] if esb else [npx, "--yes", "esbuild"] if npx else None)
    if cmd is None:
        print("  WARN   esbuild unavailable — JSX gate skipped")
    else:
        g = subprocess.run(cmd + ["--loader:.jsx=jsx", tmp, "--outfile=" + os.devnull],
                           capture_output=True, text=True, cwd=os.path.join(ROOT, "frontend"))
        if g.returncode != 0:
            fail(f"esbuild gate:\n{g.stderr[-2000:]}")
        print("  OK     esbuild JSX gate passed")

    if a.check:
        print(f"  WOULD  edit   {p}")
        print("  CHECK  dry run complete — no files written")
        return

    shutil.copy2(p, p + f".bak-{FENCE}")
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    got = open(p, encoding="utf-8").read()
    if got != new:
        shutil.copy2(p + f".bak-{FENCE}", p)
        fail("post-write verification failed — file restored")
    print(f"  WROTE  {TARGET}   ({FENCE} x5)")
    print()
    print("  DONE   frontend only — ./desktop/build-scalp.sh frontend")
    print("         Backtest → HA V1: Max SL cap, C1 retrace, Retrace frac, Retrace TTL bars and the")
    print("         three condition windows are gone; Max trades / day stays. HA Sell keeps Max SL cap.")


if __name__ == "__main__":
    main()
