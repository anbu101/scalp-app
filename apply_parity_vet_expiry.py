#!/usr/bin/env python3
# apply_parity_vet_expiry.py — ── PARITY_VET_EXPIRY_20261103 ──
#
# Backtest → Parity, Velvet 2026-11-03: "1 missed". The replay exited the
# expiring 23350PE at 15:04 and opened next week's 23750PE at 15:04; live
# squared off at 15:15 and opened nothing. Live and the backtest runner name
# two settings differently, and the replay read neither:
#   * entry cutoff — live "entry_cutoff" (15:00); runner "entry_cutoff_time"
#     (default 15:30 = no cutoff). The replay could enter after 15:00 on ANY
#     day, not just expiry days.
#   * expiry-day exit — live closes the expiring contract at
#     min(exit_time, 15:20) and never rolls; the runner (rollover off) exits
#     at roll_time, default 15:00.
# Parity now maps both (runner decides on 5m bars, so roll_time = live exit
# minute − one bar → exit at 15:14, live's 15:15). A config that really rolls
# (rollover_enabled true) or already sets entry_cutoff_time is left alone.
# Parity-only: no strategy, order or backtest-engine change.
#
# FILES  backend/app/parity/parity_core.py (to_runner_config, VET_V1)
#        backend/app/parity/test_parity_vet_expiry.py (NEW)
#
# SAFETY: fence check, `git status` stop sign, every anchor exactly once,
# py_compile gate, parity suites on a scratch copy first, backups,
# all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_parity_vet_expiry.py
#   then: ./desktop/build-scalp.sh backend → Parity → 2026-11-03 · Velvet → Compare
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

FENCE = "PARITY_VET_EXPIRY_20261103"
FENCE2 = "PARITY_VET_EXPIRY_20261103"
PARENTS = {
    "backend/app/parity/parity_core.py": ["def to_runner_config", 'elif sid == "VET_V1":', "PARITY_DAILY_20261030"],
    "backend/app/backtest/vet/backtest_vet_runner.py": ['"entry_cutoff_time"', '"roll_time"', '"rollover_enabled"'],
    "backend/app/engine/vet/vet_selection_loop.py": ["expiry_exit_min = min(exit_min, 15 * 60 + 20)"],
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
    tmp = Path(tempfile.mkdtemp(prefix="pve_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="pve_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be = tmp / "backend"
    suites = [("Velvet expiry/cutoff parity", [py, "app/parity/test_parity_vet_expiry.py"], be, 600),
              ("parity", [py, "app/parity/test_parity.py"], be, 900)]
    for f in ("app/parity/test_parity_open_pair.py", "app/parity/test_parity_cfg_fallback.py",
              "app/parity/test_parity_tg_route.py"):
        if (be / f).exists():
            suites.append((f.split("/")[-1], [py, f], be, 600))
    ok_all = True
    for name, cmd, cwd, tmo in suites:
        ok, tail = run(cmd, cwd, env, timeout=tmo)
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
  1. ./desktop/build-scalp.sh backend
  2. Backtest → Parity → 2026-11-03 · Velvet → Compare (and 2026-11-02 / 10-30 again)
  3. commit + push (fence {FENCE})
Rollback: copy parity_core.py.bak-{FENCE} back and delete backend/app/parity/test_parity_vet_expiry.py.""")


if __name__ == "__main__":
    main()
