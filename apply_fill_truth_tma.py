#!/usr/bin/env python3
# apply_fill_truth_tma.py — ── FILL_TRUTH_TMA_20261102 ── (requires FILL_TRUTH_20261102)
#
# TMA_V1 / TMA_V2 ("Timberwolf"): the same exit-fill rule IC_V2
# got after the 2026-11-02 L1·ADJ mis-booking.
#
# THE LATENT BUG (proven on GitHub main by the new e2e test): both GTT
# monitors' race fallback, and both managers' "already flat at the broker"
# lookups, took the NEWEST complete fill of a side on the symbol. Same strike
# twice in a day — trade A closed by TP (app buy-back 9.50), trade B sold on
# the same strike, B's SL GTT fires (185.00) during the Zerodha race (GTT
# says triggered, its order not in the book yet) → B was booked at A's 9.50:
# a −₹1,625 stop booked as a +₹9,782 profit.
#
# THIS SCRIPT (backend only, engines' order placement untouched)
#   tma/tma_gtt_monitor.py, tma2/tma2_gtt_monitor.py
#       fill = execution/fill_pick.pick_exit_fill: BUY at/after THIS leg's
#       entry order (its broker timestamp; else the group's signal time),
#       never the group's own entry ids, earliest qualifying fill, ONLY the
#       GTT's own order(s) when the GTT names them
#   tma/tma_trade_manager.py, tma2/tma2_trade_manager.py
#       _last_fill_px (sell buy-back / hedge sale when already flat) scoped the
#       same way; the hedge-unwind lookup floors at the unwind's own start
#   tma2/test_tma_fill_truth.py (NEW) both monitors + both managers, plus a
#       TMA_V2 end-to-end replay of the same-strike race
#
# SAFETY: prerequisite fences asserted (FILL_TRUTH_20261102 must be applied
# first), `git status` stop sign, every anchor exactly once, py_compile gate,
# all TMA + IC fill suites run on a scratch copy first, .bak-FILL_TRUTH_TMA_20261102
# backups, all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_fill_truth_tma.py
#   then: ./desktop/build-scalp.sh backend   (non-trading hours)
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

FENCE = "FILL_TRUTH_TMA_20261102"
FENCE2 = "FILL_TRUTH_TMA_20261102"
PARENTS = {
    "backend/app/execution/fill_pick.py": ["FILL_TRUTH_20261102", "def pick_exit_fill", "def order_ts"],
    "backend/app/engine/tma/tma_gtt_monitor.py": ["GTT_UNFILLED_ESCALATION_20261026"],
    "backend/app/engine/tma2/tma2_gtt_monitor.py": ["GTT_UNFILLED_ESCALATION_20261026"],
    "backend/app/engine/tma/tma_trade_manager.py": ["TMA_GTT_RACE_20260814", "EXIT_STATE_MACHINE_20261026"],
    "backend/app/engine/tma2/tma2_trade_manager.py": ["TMA_GTT_RACE_20260814", "EXIT_STATE_MACHINE_20261026"],
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
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="fill_truth_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    e1, e2 = tmp / "backend/app/engine/tma", tmp / "backend/app/engine/tma2"
    ic = tmp / "backend/app/engine/ic"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [
        ("TMA fill truth (both strategies + same-strike e2e)", [py, "test_tma_fill_truth.py"], e2),
        ("TMA_V2 end to end", [py, "test_tma2_e2e.py"], e2),
        ("TMA_V2 exit paths", [py, "test_tma2_exit_paths.py"], e2),
        ("TMA_V2 entry / unwind paths", [py, "test_tma2_entry_paths.py"], e2),
        ("TMA_V2 live core", pt + ["test_tma2_live_core.py"], e2),
        ("TMA_V1 exit paths", [py, "test_tma_exit_paths.py"], e1),
        ("TMA_V1 live core", pt + ["test_tma_live_core.py"], e1),
        ("IC fill truth (shared rule intact)", pt + ["test_ic_fill_truth.py"], ic),
    ]
    ok_all = True
    for name, cmd, cwd in suites:
        ok, tail = run(cmd, cwd, env)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {tail.splitlines()[-1] if tail else ''}")
        if not ok:
            print("      " + tail.replace("\n", "\n      "))
            ok_all = False
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
  1. ./desktop/build-scalp.sh backend   (after 15:30 / non-trading hours)
  2. restart Scalp Terminal
  3. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete backend/app/engine/tma2/test_tma_fill_truth.py.""")


if __name__ == "__main__":
    main()
