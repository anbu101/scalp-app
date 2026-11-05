#!/usr/bin/env python3
# apply_ic_margin_headroom.py — ── IC_MARGIN_HEADROOM_20261105 ── (requires IC_PAPER_FALLBACK_20261103)
#
# 2026-11-04 09:18 (IC_V2): the margin pre-check passed — Kite's basket
# estimate said ₹1,26,373 against ₹1,36,993 available — then, 5 seconds and
# three filled legs later, the 4th leg was rejected needing ₹1,38,009 (+9.2%
# over the estimate). Three real legs were bought and dumped.
#
# THIS SCRIPT  backend/app/engine/ic/ic_group_manager.py (IC_V1 + IC_V2)
#   * the pre-check now requires Kite's estimate + headroom:
#       config margin_buffer_pct (default 5 — Anbu 2026-11-05; 0 = the old
#       exact rule). NOTE: 5% would NOT have caught 2026-11-04 (₹1,32,692 <
#       ₹1,36,993; the real need was +9.2%); 10% would have. A blocked entry
#       is stopped BEFORE any order; in PAPER_LIVE the paper book trades on
#   * every check logs [IC][…][MARGIN][OK|BLOCK] required / +headroom / available
#   * the alert reads "(required ₹1,31,000 + 5% headroom = ₹1,37,550,
#     available ₹1,36,993)"
#   backend/app/engine/ic/test_ic_margin_headroom.py (NEW)
# Independent of apply_ic_entry_fail_report.py (either order).
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, IC suites on a scratch copy first,
# backups, all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_ic_margin_headroom.py
#   then: ./desktop/build-scalp.sh backend   (after 15:30)
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

FENCE = "IC_MARGIN_HEADROOM_20261105"
FENCE2 = "IC_MARGIN_HEADROOM_20261105"
PARENTS = {
    "backend/app/engine/ic/ic_group_manager.py": ["IC_PAPER_FALLBACK_20261103", "def _margin_ok",
                                                  "get_basket_margin"],
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
    tmp = Path(tempfile.mkdtemp(prefix="ic_mh_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="ic_mh_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    ic = tmp / "backend/app/engine/ic"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [(f, pt + [f], ic) for f in ("test_ic_margin_headroom.py", "test_ic_entry_fail_report.py",
                                          "test_ic_group_manager.py",
                                          "test_ic_paper_fallback.py", "test_ic_fill_truth.py",
                                          "test_ic_fill_anchor.py", "test_ic_engine_monitor_eod.py",
                                          "test_ic_live_core.py") if (ic / f).exists()]
    ok_all = True
    for name, cmd, cwd in suites:
        ok, tail = run(cmd, cwd, env)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {tail.splitlines()[-1] if tail else ''}", flush=True)
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
  1. ./desktop/build-scalp.sh backend   (after 15:30) and restart
  2. commit + push (fence {FENCE})
Rollback: copy ic_group_manager.py.bak-{FENCE} back and delete engine/ic/test_ic_margin_headroom.py.""")


if __name__ == "__main__":
    main()
