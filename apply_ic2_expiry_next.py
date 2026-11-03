#!/usr/bin/env python3
# apply_ic2_expiry_next.py — ── IC2_EXPIRY_NEXT_20261103 ── (backtest option only)
#
# IC_V2 "Icarus" carries every condor to the next morning (09:16) EXCEPT on
# the weekly expiry day, where it trades the contract expiring that day and
# squares off at the expiry-day time. Expiry-day shorts carry SEBI's extra 2%
# ELM on contract value (hedges don't offset it): 2026-11-03 needed ₹1,41,738
# against ₹1,37,544 available. This adds a BACKTEST option to measure the
# alternative: on expiry days build the condor on NEXT week's expiry and carry
# it to the next morning like every other day.
#
# THIS SCRIPT (live trading untouched)
#   backend/app/backtest/ic/backtest_ic_runner.py
#     config expiry_day_contract: "current" (default — byte-identical to the
#     old runner, key absent from saved configs) | "next". With "next", on
#     the expiry day yesterday's carried legs still close at 09:16 on this
#     week's scope; only the NEW entry moves to the next weekly chain, with
#     no expiry-day hard close (holiday-shifted weeks handled). Days whose
#     next-week chain is missing from the corpus are skipped and counted:
#     diag expiry_next_days / expiry_next_uncovered. NEXT_OPEN (V2) only.
#   frontend Backtest → IC_V2 → Holding: "On expiry day, trade [this week's
#     expiry — square off that day (live) | next week's expiry — carry to
#     next morning]"; config chip "Expiry day: next week's contract,
#     carried"; Compare Runs row "IC expiry day"; Queue label "expNext"
#   backend/app/backtest/ic/test_ic2_expiry_next.py (NEW)
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, IC backtest + parity suites and an
# esbuild parse of the three pages on a scratch copy first, backups,
# all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_ic2_expiry_next.py
#   then: ./desktop/build-scalp.sh both   (after 15:30)
#
# Run ONLY this script — do not hand-place any file it carries.

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

FENCE = "IC2_EXPIRY_NEXT_20261103"
FENCE2 = "IC2_EXPIRY_NEXT_20261103"
PARENTS = {
    "backend/app/backtest/ic/backtest_ic_runner.py": ["IC_ADJ_FILL_OPEN_20261101", "IC2_SHORT_TP_SKIP_EXP_20261015",
                                                      "PRELOAD_SCOPED", "MORNING_SQUARE_OFF"],
    "frontend/src/pages/Backtest.jsx": ["IC_ADJ_FILL_OPEN_20261101", "Expiry-day square-off"],
    "frontend/src/pages/backtest/RunComparison.jsx": ["ic_short_tp_exp"],
    "frontend/src/pages/backtest/BacktestQueue.jsx": ["sTP-noExpiry"],
}
PAYLOAD = "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"


def die(msg):
    print(f"\n[{FENCE}] ABORT: {msg}")
    sys.exit(1)


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def find_root(arg):
    for c in ([Path(arg)] if arg else []) + [Path.cwd(), Path("/Users/anbu/dev/scalp-app")]:
        if (c / "backend" / "app" / "api_server.py").exists() and (c / "frontend" / "src").exists():
            return c.resolve()
    die("repo root not found (run from the scalp-app folder or pass --repo)")


def build(root, data):
    """{rel: (old_text_or_None, new_text)} — every anchor exactly once."""
    out, notes = {}, []
    for rel, spec in data["edits"].items():
        p = root / rel
        if not p.exists():
            die(f"missing {rel}")
        s = p.read_text()
        if FENCE in s or FENCE2 in s:
            die(f"{rel} already carries {FENCE}/{FENCE2} — already applied (or half-applied: "
                f"restore from .bak-{FENCE} first)")
        notes.append(f"  {'pre-image = GitHub main' if sha(s) == spec['pre'] else 'pre-image DIFFERS from GitHub main (anchors decide)'}: {rel}")
        new = s
        for i, (old, rep) in enumerate(spec["hunks"]):
            n = new.count(old)
            if n != 1:
                die(f"{rel}: anchor {i + 1}/{len(spec['hunks'])} found {n}× (expected 1):\n{old[:300]}")
            new = new.replace(old, rep)
        if sha(s) == spec["pre"] and sha(new) != spec["post"]:
            die(f"{rel}: result differs from the tested build")
        out[rel] = (s, new)
    for rel, text in data["new"].items():
        if (root / rel).exists():
            die(f"{rel} already exists — already applied?")
        out[rel] = (None, text)
    return out, notes


def git_dirty(root, rels):
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=root,
                           capture_output=True, text=True, timeout=30)
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def run(cmd, cwd, env=None, timeout=900):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
    return r.returncode == 0, tail


def esbuild_bin(root):
    for c in (root / "frontend/node_modules/.bin/esbuild", root / "desktop/node_modules/.bin/esbuild"):
        if c.exists():
            return str(c)
    return shutil.which("esbuild")


def test_scratch(root, plan):
    tmp = Path(tempfile.mkdtemp(prefix="ic2_xn_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    icd = "backend/app/backtest/ic/"
    for keep in (icd + "backtest_ic_runner.py.bak-IC2_SHORT_TP_SKIP_EXP_20261015",
                 icd + "backtest_ic_runner.py.bak-IC_ADJ_FILL_OPEN_20261101",
                 icd + "ic_v1_engine.py.bak-IC_ADJ_FILL_OPEN_20261101"):
        if (root / keep).exists():
            shutil.copy2(root / keep, tmp / keep)
    (tmp / (icd + "backtest_ic_runner.py.bak-" + FENCE)).write_text(plan[icd + "backtest_ic_runner.py"][0])
    (tmp / "frontend/src/pages/backtest").mkdir(parents=True)
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="ic2_xn_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be = tmp / "backend"
    suites = [
        ("IC2 expiry-day next contract (OFF ≡ ORIGINAL, ON behaviour)", [py, "app/backtest/ic/test_ic2_expiry_next.py"], be, 900),
        ("IC short-TP skip expiry", [py, "app/backtest/ic/test_ic2_short_tp_skip_exp.py"], be, 900),
        ("IC adjustment fill", [py, "app/backtest/ic/test_ic_adjust_fill.py"], be, 900),
        ("parity (replays the IC runner)", [py, "app/parity/test_parity.py"], be, 900),
    ]
    ok_all = True
    for name, cmd, cwd, tmo in suites:
        print(f"  …     {name}", flush=True)
        ok, tail = run(cmd, cwd, env, timeout=tmo)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {tail.splitlines()[-1] if tail else ''}", flush=True)
        if not ok:
            print("      " + tail.replace("\n", "\n      "))
            ok_all = False
    eb = esbuild_bin(root)
    if eb:
        for rel in ("frontend/src/pages/Backtest.jsx", "frontend/src/pages/backtest/RunComparison.jsx",
                    "frontend/src/pages/backtest/BacktestQueue.jsx"):
            ok, tail = run([eb, str(tmp / rel), "--loader:.jsx=jsx", "--loader:.js=jsx", "--format=esm",
                            "--log-level=error", "--outfile=" + str(tmp / "x.js")], tmp)
            print(f"  {'PASS' if ok else 'FAIL'}  {rel.split('/')[-1]} parses (esbuild)")
            ok_all = ok_all and ok
    else:
        print("  WARN  esbuild not found — page parse check skipped (build-scalp.sh will compile them)")
    shutil.rmtree(home, ignore_errors=True)
    if ok_all:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="checks + scratch tests, write nothing")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"[{FENCE}] repo {root}")
    data = json.loads(zlib.decompress(base64.b64decode(PAYLOAD)))

    for rel, fences in PARENTS.items():
        p = root / rel
        if not p.exists():
            die(f"prerequisite file missing: {rel}")
        t = p.read_text()
        for f in fences:
            if f not in t:
                die(f"prerequisite {f} not found in {rel}")
    print("  prerequisites present")

    rels = list(data["edits"]) + list(data["new"])
    dirty = git_dirty(root, rels)
    if dirty and not a.allow_dirty:
        die("uncommitted changes on target files (working-copy drift stop sign):\n  "
            + "\n  ".join(dirty) + "\ncommit/stash them, or re-run with --allow-dirty if intended")
    if dirty:
        print("  --allow-dirty: proceeding over\n  " + "\n  ".join(dirty))

    plan, notes = build(root, data)
    print("\n".join(notes))
    for rel, (_, new) in plan.items():
        if rel.endswith(".py"):
            try:
                compile(new, rel, "exec")
            except SyntaxError as e:
                die(f"py_compile failed for {rel}: {e}")
    print("  all anchors matched once; py_compile OK")

    if not test_scratch(root, plan):
        die("tests failed on the scratch copy — NOTHING was written")
    if a.dry_run:
        print(f"\n[{FENCE}] dry run complete — nothing written.")
        return

    written, backups = [], []
    try:
        for rel, (old, new) in plan.items():
            p = root / rel
            if old is not None:
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append((p, b))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new)
            written.append(p)
    except Exception as e:
        for p, b in backups:
            shutil.copy2(b, p)
        for p in written:
            if not any(p == x for x, _ in backups) and p.exists():
                p.unlink()
        die(f"write failed ({e}) — rolled back")
    print(f"\n[{FENCE}] written:")
    for p in written:
        print(f"  {p.relative_to(root)}")

    # build copies (gitignored; refreshed by build-scalp.sh anyway)
    for rel, (old, new) in plan.items():
        top = "backend" if rel.startswith("backend/") else "frontend"
        bp = root / "desktop/src-tauri" / rel
        if not (root / "desktop/src-tauri" / top).exists():
            continue
        if bp.exists() and old is not None and bp.read_text() not in (old, new):
            print(f"  build copy differs, left for build-scalp.sh: desktop/src-tauri/{rel}")
            continue
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(new)
        print(f"  build copy: desktop/src-tauri/{rel}")

    print(f"""
[{FENCE}] DONE. Next:
  1. ./desktop/build-scalp.sh both      (after 15:30)
  2. Backtest → IC_V2 → Holding → "On expiry day, trade": run the seal once with
     "this week's expiry" and once with "next week's expiry", then Compare Runs
     (row "IC expiry day"); check diag expiry_next_uncovered is 0 (or small)
  3. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete backend/app/backtest/ic/test_ic2_expiry_next.py.""")


if __name__ == "__main__":
    main()
