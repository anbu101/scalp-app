#!/usr/bin/env python3
# apply_fill_truth.py — ── FILL_TRUTH_20261102 ── (+ RECON_VALUE_20261102)
#
# 2026-11-02 incident (IC_V2 "Icarus" LIVE): L1·ADJ NIFTY26N0323350CE was
# stopped by its SELL GTT at 55.85; the app booked the exit at 80.15 (its own
# entry) → ₹0 instead of −₹1,579.50. App −₹3,545.75 vs Kite −₹5,125.25 gross,
# while the broker check said "Matches Zerodha" (it compared quantities only).
#
# ROOT CAUSE: ic_gtt_monitor's GTT-race fallback took "newest COMPLETE BUY on
# the symbol" — right for SHORT legs, the leg's own ENTRY for an ·ADJ LONG.
# Same family: ic_group_manager._last_fill_px took the newest fill of a side
# with no time/leg scoping (an ADJ on the stopped short's strike would be
# taken as the short's exit).
#
# THIS SCRIPT
#   backend  execution/fill_pick.py (NEW)  one exit-fill rule: exit side from
#            the leg's direction, at/after its entry, never any leg's entry or
#            another leg's exit, ONLY the GTT's own order(s) when it names them,
#            earliest qualifying fill, VWAP over freeze slices; implausible-fill
#            detector
#            engine/ic/ic_gtt_monitor.py   uses it; passes the exit order id;
#            alerts a fill far from every trigger
#            engine/ic/ic_group_manager.py refuses a close by the leg's own
#            entry order; _last_fill_px scoped to the leg; exit_order_id now
#            stored on the trade; entry ids/time survive restart
#            recon/recon_core.py + recon_routes.py  RECON_VALUE: every poll also
#            compares today's fills per symbol — quantity AND money — against
#            kite.positions() day values: PRICE / FILLS issues, two-poll
#            confirm, one alert, resolved note, own table recon_value_issues
#   frontend components/fleet/SystemChecks.jsx  badge counts value issues
#            (red for PRICE); detail rows show side, both averages, ₹ effect
#   tests    engine/ic/test_ic_fill_truth.py (NEW, replays the day's real Kite
#            order book), recon/test_recon_value.py (NEW, replays the day)
#
# SAFETY: fence check (abort if applied / half-applied), parent fences
# asserted, `git status` stop sign on every target (--allow-dirty to pass),
# every anchor must match exactly once, py_compile gate, the full test set is
# run on a SCRATCH COPY first, .bak-FILL_TRUTH_20261102 backups, all-or-nothing
# writes, build copies under desktop/src-tauri/ updated when present.
# Engines' order placement is untouched; recon stays read-only.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_fill_truth.py
#   then: ./desktop/build-scalp.sh both   (non-trading hours)
#   then: python3 repair_fill_truth_20261102.py  (look)  → --apply
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

FENCE = "FILL_TRUTH_20261102"
FENCE2 = "RECON_VALUE_20261102"
PARENTS = {
    "backend/app/recon/recon_core.py": ["BROKER_RECON_20261029"],
    "frontend/src/components/fleet/SystemChecks.jsx": ["SYSTEM_CHECKS_UI_20261029"],
    "backend/app/engine/ic/ic_group_manager.py": ["IC_GTT_RACE_20260814", "IC_EXIT_CONFIRM_20261026"],
    "backend/app/engine/ic/ic_gtt_monitor.py": ["IC_V2"],
    "backend/app/execution/testkit_fake_kite.py": ["E2E_TESTKIT_20261027"],
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
    tmp = Path(tempfile.mkdtemp(prefix="fill_truth_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    (tmp / "frontend/src/components/fleet").mkdir(parents=True)
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="fill_truth_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    ic = tmp / "backend/app/engine/ic"
    suites = [
        ("IC fill truth (replays 02 Nov)", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_ic_fill_truth.py"], ic),
        ("IC group manager + e2e", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_ic_group_manager.py"], ic),
        ("IC engine / GTT monitor / EOD", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_ic_engine_monitor_eod.py"], ic),
        ("IC live core", [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_ic_live_core.py"], ic),
        ("broker check (existing)", [py, "app/recon/test_recon.py"], tmp / "backend"),
        ("broker check value (replays 02 Nov)", [py, "app/recon/test_recon_value.py"], tmp / "backend"),
    ]
    ok_all = True
    for name, cmd, cwd in suites:
        ok, tail = run(cmd, cwd, env)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {tail.splitlines()[-1] if tail else ''}")
        if not ok:
            print("      " + tail.replace("\n", "\n      "))
            ok_all = False
    eb = esbuild_bin(root)
    jsx = tmp / "frontend/src/components/fleet/SystemChecks.jsx"
    if eb:
        ok, tail = run([eb, str(jsx), "--loader:.jsx=jsx", "--format=esm", "--log-level=error",
                        "--outfile=" + str(tmp / "sc.js")], tmp)
        print(f"  {'PASS' if ok else 'FAIL'}  SystemChecks.jsx parses (esbuild)")
        ok_all = ok_all and ok
    else:
        print("  WARN  esbuild not found — SystemChecks.jsx parse check skipped (build-scalp.sh will compile it)")
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
  1. ./desktop/build-scalp.sh both      (after 15:30 / non-trading hours)
  2. restart Scalp Terminal
  3. python3 repair_fill_truth_20261102.py           (look)
     python3 repair_fill_truth_20261102.py --apply   (fix 02 Nov's L1·ADJ row)
  4. commit + push (fences {FENCE}, {FENCE2})
Rollback: copy each *.bak-{FENCE} back and delete the three new files.""")


if __name__ == "__main__":
    main()
