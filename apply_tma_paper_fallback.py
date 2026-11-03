#!/usr/bin/env python3
# apply_tma_paper_fallback.py — ── PAPER_FALLBACK_20261103 ── (TMA_V2 + TMA_V1)
#
# Same defect as IC_V2 on 2026-11-03 (IC_PAPER_FALLBACK_20261103): TMA_V2
# "Timberwolf" and TMA_V1 run a margin pre-check before a LIVE entry. In
# PAPER_LIVE the paper book is a twin of the live rows, so a margin refusal
# silenced paper too. Now, in PAPER_LIVE only, a margin-refused signal is
# entered PAPER-only: no broker order, rows in the paper book, both legs
# sized exactly as their paper twins would have been (paper_lots), warning
# "LIVE entry blocked on margin — the paper book trades this signal on its
# own (PAPER_LIVE)". LIVE-only keeps the hard block; any OTHER live failure
# (hedge unfilled, sell rejected…) does NOT fall back; enough margin →
# unchanged.
#
# Every other fleet strategy has no margin pre-check — see the reply.
#
# FILES  backend/app/engine/tma2/tma2_trade_manager.py
#        backend/app/engine/tma/tma_trade_manager.py
#        backend/app/engine/tma2/test_tma_paper_fallback.py (NEW)
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, all TMA suites on a scratch copy
# first, backups, all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_tma_paper_fallback.py
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

FENCE = "PAPER_FALLBACK_20261103"
FENCE2 = "PAPER_FALLBACK_20261103"
PARENTS = {
    "backend/app/engine/tma2/tma2_trade_manager.py": ["skipped_margin", "def _complete_pending", "FLEET_MODES_20260923"],
    "backend/app/engine/tma/tma_trade_manager.py": ["skipped_margin", "def _complete_pending", "FLEET_MODES_20260923"],
    "backend/app/risk/execution_modes.py": ["def shadow_active"],
    "backend/app/trading/paper_lots.py": ["def resolve_twin_lots"],
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
    tmp = Path(tempfile.mkdtemp(prefix="tma_pfb_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="tma_pfb_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    t2, t1 = tmp / "backend/app/engine/tma2", tmp / "backend/app/engine/tma"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [
        ("TMA paper fallback on margin (V2 + V1)", [py, "test_tma_paper_fallback.py"], t2),
        ("TMA_V2 end to end", [py, "test_tma2_e2e.py"], t2),
        ("TMA_V2 exit paths", [py, "test_tma2_exit_paths.py"], t2),
        ("TMA_V2 entry / unwind paths", [py, "test_tma2_entry_paths.py"], t2),
        ("TMA_V2 live core", pt + ["test_tma2_live_core.py"], t2),
        ("TMA_V1 exit paths", [py, "test_tma_exit_paths.py"], t1),
        ("TMA_V1 live core", pt + ["test_tma_live_core.py"], t1),
    ]
    for f, d in (("test_tma_fill_truth.py", t2), ("test_tma_gap_close.py", t2)):
        if (d / f).exists():
            suites.append((f, [py, f], d))
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
  1. ./desktop/build-scalp.sh backend   (after 15:30)
  2. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete engine/tma2/test_tma_paper_fallback.py.""")


if __name__ == "__main__":
    main()
