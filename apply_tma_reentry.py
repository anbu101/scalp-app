#!/usr/bin/env python3
# apply_tma_reentry.py — fence TMA_REENTRY_20260921
#
# TMA_V2 BACKTEST ONLY: continuation re-entry (DECAY roll / TP / EXPIRY).
# Default OFF — an unset config is row-for-row and diag-for-diag identical to
# the current runner, and this script PROVES that on your machine before it
# keeps anything (original vs patched runner on a synthetic corpus).
# Paper/live engines are not touched.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_tma_reentry.py
#
# RUN ONLY THIS SCRIPT — it carries every payload; do not hand-place files.
#
# Writes (each with a .bak-TMA_REENTRY_20260921 backup, all-or-nothing):
#   backend/app/backtest/tma/backtest_tma_v2_runner.py   (replaced; guarded by
#       the sha256 of the main-branch file this was built against)
#   backend/app/backtest/tma/tma_v2_engine.py            (append-only helpers)
#   backend/app/backtest/tma/test_tma_v2_reentry.py      (new, 51 checks)
#   frontend/src/pages/backtest/SweepBuilder.jsx         (insert-only: 5 axes)
#   frontend/src/pages/backtest/BacktestQueue.jsx        (insert-only: chip)
#   frontend/src/pages/backtest/RunComparison.jsx        (insert-only: row)
#   + the desktop/src-tauri build copies when they exist.
# Flags: --repo PATH  --allow-dirty  --skip-tests (not recommended)
#
# The test step builds a ~250 MB temp corpus and takes 1–3 minutes.

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

FENCE = "TMA_REENTRY_20260921"
RUNNER_SHA = "c6c6185038e18324b11b3f29edc83c23969a6f22562543ad36ebfddb46d59799"
TMA = "app/backtest/tma"
RUNNER, ENGINE, TESTF = (f"{TMA}/backtest_tma_v2_runner.py",
                         f"{TMA}/tma_v2_engine.py",
                         f"{TMA}/test_tma_v2_reentry.py")
FE = "src/pages/backtest"

RUNNER_NEW = """
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
cnwl18bRlry6t12Ybw//F8ezzr8=
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
TEST_FILE = """
eNrVXFtz20aWfuev6EUqY8CmYJKyHEsVekqRqURjW1JRdCYerQoFEk0SES4MLpa4Kk3laav2dXeq9nlf5o/sP/Ev2e+cbhAAb5Y2
U1tZjUcCGo3Tp8/1O92NfCWG7uhaRt5zdzZ7TteZTLPnWeg+pwsHF86njpNIGWXJ3J7NG181vhKf//Yr/onB+0On3+udDvofnU6r
87K132kXz0ZxlPlR7mZ+HIlE7vD7B0JGEz+SYiqDmUxSEUegNnUjbyd0PQle0PZM9D+cnvb6O4H8JAMxlFP3kx/nCToLV6TzKJvK
zB9hhGSWp2KY+0FGz9AMYv3e4TshZ/FoKkZBPLoW5p/c6POv//HeTQR4fNEUg2mepJ47FzJxm2DNDYS8nfnJXIzcAJJwE9UKYuk8
HMYBD+FJ3SyOwG4gL8DRSIrnYjR1k4lMcVWyduNHE8tmUQ2mUnhy5Kf+J4m+Ehz5qTg7Phaf/+2/xFn/5PuT08N3B+JmKnkKAnoI
5iIdJf4se5KyevIZCJmFchY6yaNIJlCJPXSvd9bpwhKpn6UikreZyGItIPVak8cqhteNxFkQQxGeGCdxKPxMYK4izNNMzJLYyzHh
JL7ZGcfJDv6CGD32fHfCTXTBZFM3lCLOs1kOAik3zdwMk/eKgW78bMrtY+lmeUJ85VEqs6ZwR0mcpmLM4oqjsT9JlSD7eaS4Kgz2
QIjZPJuSVTzUdIX51zZsoSNCPzqg6d0kPvrCrP7a2WuJ99+JTIazwrJ4cjKQ1MPPrEaDh3eccU4sO47ww1mckIyiOGNDTxsN3ab+
BP7QzjM/KFp/TmHw+jp0s2lxHafFVYJB47C4S38JwN/u4na+6Ed8jv1AKp48N3NHgZumxKlmKvX8UbZ4DLOETvQzum8uWmEK+I2J
Zq66/Jc40nShtSnmULx3Tiw3fuj1e6LLNyaEASYcx7ITmcbBJ2laDXBp04u2D40mmdlqijRLTHoN7Qm0kV7uXlmWWPx8tdCpljEU
ahcKtVXIsJWPOoWPFjyhWY4y6Tn6OQzRIecG0Sj+xT0QvRetzhqqpBY78sfZ3NFergmyszu1J6LK6laqsLkFnbXuSv5w2vtzX4jH
UbU1FR0/9Rim2PZTo0oNCK+OH3ny1nHhaaM4hINKJ4XtStBuQpaZjFLYsTMbZdwng7o855Mb+B7dM5Xb+JNMIG4fU0vxHhzjuHd6
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
"""


def unpack(b: str) -> str:
    return zlib.decompress(base64.b64decode("".join(b.split()))).decode()


# ── JSX inserts (anchored, insert-only) ──────────────────────────────────
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


def die(msg: str) -> None:
    print(f"\nABORT — {msg}\nNothing was written.")
    sys.exit(1)


def git_dirty(repo: Path, rel: str) -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", rel],
                             cwd=repo, capture_output=True, text=True, timeout=20)
        return bool(out.stdout.strip()) if out.returncode == 0 else False
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    be, fe = repo / "backend", repo / "frontend"
    if not (be / RUNNER).exists() or not (fe / FE / "SweepBuilder.jsx").exists():
        die(f"{repo} is not the scalp-app repo root (run from /Users/anbu/dev/scalp-app or pass --repo)")

    # ── guards ───────────────────────────────────────────────────────────
    runner_txt = (be / RUNNER).read_text()
    engine_txt = (be / ENGINE).read_text()
    touched = [f"backend/{RUNNER}", f"backend/{ENGINE}",
               f"frontend/{FE}/SweepBuilder.jsx", f"frontend/{FE}/BacktestQueue.jsx",
               f"frontend/{FE}/RunComparison.jsx"]
    marks = [p for p in touched if FENCE in (repo / p).read_text()]
    if (be / TESTF).exists():
        marks.append(f"backend/{TESTF}")
    if marks:
        if len(marks) == len(touched) + 1:
            print(f"{FENCE} is already applied — nothing to do.")
            return
        die("MIXED STATE — the fence is present in some files but not all:\n  "
            + "\n  ".join(marks) + "\nRestore them (git checkout / delete the test file) and re-run.")
    sha = hashlib.sha256(runner_txt.encode()).hexdigest()
    if sha != RUNNER_SHA:
        die("backtest_tma_v2_runner.py differs from the GitHub main version this patch was built on\n"
            f"  expected sha256 {RUNNER_SHA[:16]}…  found {sha[:16]}…\n"
            "A local or unpushed edit is in the way. Push it (or tell Claude) so the patch can be rebuilt on it.")
    if "def sl_tp_levels(" not in engine_txt or "def xover_exit_ts_v2(" not in engine_txt:
        die("tma_v2_engine.py is not the expected engine (sl_tp_levels / xover_exit_ts_v2 missing)")
    if not a.allow_dirty:
        dirty = [p for p in touched if git_dirty(repo, p)]
        if dirty:
            die("uncommitted changes in files this patch edits (an M is a stop sign):\n  "
                + "\n  ".join(dirty) + "\nCommit/stash them, or re-run with --allow-dirty if they are yours and intended.")

    # ── stage ────────────────────────────────────────────────────────────
    new = {}
    new[be / RUNNER] = unpack(RUNNER_NEW)
    new[be / ENGINE] = engine_txt + unpack(ENGINE_APPEND)
    new[be / TESTF] = unpack(TEST_FILE)
    try:
        for name, fn in (("SweepBuilder.jsx", patch_sweep), ("BacktestQueue.jsx", patch_queue),
                         ("RunComparison.jsx", patch_cmp)):
            p = fe / FE / name
            new[p] = fn(p.read_text())
    except AssertionError as e:
        die(f"frontend anchor problem: {e}")

    # build copies (gitignored; refreshed by build-scalp.sh anyway)
    mirrors = {}
    mbe = repo / "desktop" / "src-tauri" / "backend"
    if (mbe / TMA).is_dir():
        for rel in (RUNNER, ENGINE, TESTF):
            mirrors[mbe / rel] = new[be / rel]
    mfe = repo / "desktop" / "src-tauri" / "frontend" / FE
    if mfe.is_dir():
        for name, fn in (("SweepBuilder.jsx", patch_sweep), ("BacktestQueue.jsx", patch_queue),
                         ("RunComparison.jsx", patch_cmp)):
            p = mfe / name
            if p.exists() and FENCE not in p.read_text():
                try:
                    mirrors[p] = fn(p.read_text())
                except AssertionError:
                    print(f"note: build copy {p.relative_to(repo)} skipped (anchor differs) — the next build refreshes it")
    new.update(mirrors)

    # ── py_compile gate (before ANY write) ───────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        for p, txt in new.items():
            if p.suffix == ".py":
                t = Path(td) / p.name
                t.write_text(txt)
                try:
                    py_compile.compile(str(t), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {p.name}: {e}")

    # ── write, all-or-nothing ────────────────────────────────────────────
    backups, created = [], []

    def rollback() -> None:
        for p, b in backups:
            shutil.copy2(b, p)
            b.unlink()
        for p in created:
            p.unlink(missing_ok=True)

    try:
        for p, txt in new.items():
            if p.exists():
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append((p, b))
            else:
                created.append(p)
            p.write_text(txt)
    except Exception as e:                                   # noqa: BLE001
        rollback()
        die(f"write failed ({e}) — rolled back")
    print(f"wrote {len(new)} files")

    # ── verify: behaviour suite incl. OFF ≡ ORIGINAL (uses the .bak) ─────
    if not a.skip_tests:
        print("running test_tma_v2_reentry.py (builds a temp corpus, 1–3 min)…")
        env = dict(os.environ, PYTHONPATH=str(be))
        r = subprocess.run([sys.executable, str(be / TESTF)], cwd=be, env=env,
                           capture_output=True, text=True)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-12:])
        if r.returncode != 0 or "PASSED" not in r.stdout:
            rollback()
            die("behaviour suite FAILED — everything rolled back. Send Claude this:\n" + tail)
        if "OFF ≡ ORIGINAL rows" not in r.stdout:
            rollback()
            die("the OFF ≡ ORIGINAL comparison did not run — rolled back")
        print(tail.splitlines()[-1])
        for t in ("test_tma_v2_engine.py",):
            r2 = subprocess.run([sys.executable, str(be / TMA / t)], cwd=be, env=env,
                                capture_output=True, text=True)
            if r2.returncode != 0:
                rollback()
                die(f"existing suite {t} FAILED after the patch — rolled back:\n"
                    + "\n".join((r2.stdout + r2.stderr).splitlines()[-8:]))
            print((r2.stdout.strip().splitlines() or ["ok"])[-1])

    # ── verify: JSX still parses (esbuild if the repo has it) ────────────
    esb = fe / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if esb.exists():
        for name in ("SweepBuilder.jsx", "BacktestQueue.jsx", "RunComparison.jsx"):
            r = subprocess.run([str(esb), str(fe / FE / name), "--loader:.jsx=jsx", "--log-level=error"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                rollback()
                die(f"esbuild rejected {name} — rolled back:\n{r.stderr[-600:]}")
        print("esbuild: 3 JSX files parse")
    else:
        print("note: esbuild not found in frontend/node_modules — JSX was esbuild-checked in the sandbox; "
              "`npm run build` in frontend/ is the local check")

    print(f"""
{FENCE} applied.
  • Backend change is live for the next backtest you queue (restart the backend if it is running).
  • The 5 new sweep axes need a frontend rebuild / dev-server reload.
  • .bak-{FENCE} files are the rollback; keep them out of git.
""")


if __name__ == "__main__":
    main()
