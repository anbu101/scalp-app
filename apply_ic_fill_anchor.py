#!/usr/bin/env python3
# apply_ic_fill_anchor.py — ── IC_FILL_ANCHOR_20261102 ── (requires FILL_TRUTH_20261102)
#
# IC_V2 "Icarus" (and IC_V1 — same shared engine): LIVE SL/TP were set from
# the strike-SELECTION LTP and never re-derived from the fill ("fill-
# independent, house pattern"). The backtest derives them from its entry
# price. 2026-11-02 parity: L2·ADJ picked at 91.20, bought at 88.30 →
# live SL/TP 63.84 / 182.40 vs backtest 61.42 / 175.50 (off its 87.75 entry).
# Approved by Anbu 2026-11-02: live order levels follow the fill.
#
# THIS SCRIPT
#   engine/ic/ic_group_manager.py
#     * each leg remembers its SL/TP spec (action, val, mode) at construction
#     * _anchor_on_fill(): after the fill is confirmed (entry_price = qty-
#       weighted fill), SL/TP are recomputed with the SAME sl_price/tp_price
#       formulas the backtest uses — BEFORE any GTT is placed, so the broker
#       levels, the tick SL, the DB row and the Telegram entry all carry the
#       anchored values. Audit line [IC][…][LEVELS][FILL_ANCHOR] on change.
#     * condor entry: all legs anchored after all four fills, then shorts
#       protected (unchanged order); ·ADJ: anchored after its fill, then its
#       OCO / SL GTT placed
#     * PAPER and ADJ_ONLY phantom legs untouched (they enter at the LTP)
#     * MTC cost re-pin already used the fill — unchanged
#   engine/ic/test_ic_fill_anchor.py (NEW) short + ADJ (incl. the exact
#     91.20 → 88.30 case), fill = pick unchanged, paper untouched
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, IC suites run on a scratch copy
# first, .bak-IC_FILL_ANCHOR_20261102 backups, all-or-nothing writes, build
# copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_ic_fill_anchor.py
#   then: ./desktop/build-scalp.sh backend   (after 15:30 / non-trading hours)
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

FENCE = "IC_FILL_ANCHOR_20261102"
FENCE2 = "IC_FILL_ANCHOR_20261102"
PARENTS = {
    "backend/app/engine/ic/ic_group_manager.py": ["FILL_TRUTH_20261102", "IC_EXIT_CONFIRM_20261026",
                                                  "def _protect_adjust_long", "def _place_and_confirm"],
    "backend/app/engine/ic/ic_live_core.py": ["def sl_price", "def tp_price"],
    "backend/app/execution/fill_pick.py": ["FILL_TRUTH_20261102"],
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
    tmp = Path(tempfile.mkdtemp(prefix="ic_fill_anchor_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="ic_fill_anchor_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    ic = tmp / "backend/app/engine/ic"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [
        ("IC SL/TP anchored on the fill", pt + ["test_ic_fill_anchor.py"], ic),
        ("IC fill truth", pt + ["test_ic_fill_truth.py"], ic),
        ("IC group manager + e2e", pt + ["test_ic_group_manager.py"], ic),
        ("IC engine / GTT monitor / EOD", pt + ["test_ic_engine_monitor_eod.py"], ic),
        ("IC live core (price math parity-pinned)", pt + ["test_ic_live_core.py"], ic),
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
  2. restart Scalp Terminal — the next IC entry logs [LEVELS][FILL_ANCHOR] when fill ≠ pick
  3. commit + push (fence {FENCE})
Rollback: copy ic_group_manager.py.bak-{FENCE} back and delete engine/ic/test_ic_fill_anchor.py.""")


if __name__ == "__main__":
    main()
