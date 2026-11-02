#!/usr/bin/env python3
# apply_tma_gap_close.py — ── TMA_GAP_CLOSE_20261102 ──
#
# TMA_V2 "Timberwolf" and TMA_V1 live signal engines — same class of defect
# as Velvet's (VET_GAP_CLOSE_20261102), but worse: aggregate() marks a 5m
# bucket complete only when its LAST 1m spot candle is present. If that one
# minute never arrives live (no tick / feed blip), the bucket stayed
# "incomplete" for the rest of the day and was DROPPED from the bar stream:
# its signal was lost and every EMA after it ran one bar short of the
# backtest. Proven: dropping one bucket's last minute, the old engines lost
# the signal in every randomized trial (V1 20/20, V2 39/39).
#
# FIX (live engines only — the backtest and aggregate() are untouched):
#   * a bucket that a LATER minute has started is closed on the minutes it
#     has; only the final, still-forming bucket waits for its last minute
#   * the signal of the bar THIS minute just closed is FRESH (one minute
#     late), flagged gap_close — the manager fills it on the option candle
#     stamped at the signal time, which is the backtest's own fill candle
#   * diag["gap_closes"] + audit [TMA2_LIVE|TMA_LIVE][GAP_CLOSE] per bar
#   * complete data: byte-identical (incremental == one-shot, no gap closes)
# Result: V1 20/20 trials keep the signal; V2 36/39, the other 3 moved to a
# later bar because the missing minute changed the bar's close — and the
# one-shot computation on the same data agrees.
#
# Icarus (IC_V2) needs nothing: it has no candle/indicator pipeline.
#
# FILES
#   backend/app/engine/tma2/tma2_live_signal_engine.py
#   backend/app/engine/tma/tma_live_signal_engine.py
#   backend/app/engine/tma2/test_tma_gap_close.py (NEW)
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, all TMA live suites run on a scratch
# copy first (≈2 min), backups, all-or-nothing writes, build copies updated.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_tma_gap_close.py
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

FENCE = "TMA_GAP_CLOSE_20261102"
FENCE2 = "TMA_GAP_CLOSE_20261102"
PARENTS = {
    "backend/app/engine/tma2/tma2_live_signal_engine.py": ["PREFIX-STABILITY RUNTIME GUARD", "def reference_full_day"],
    "backend/app/engine/tma/tma_live_signal_engine.py": ["PREFIX-STABILITY RUNTIME GUARD", "def reference_full_day"],
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
    tmp = Path(tempfile.mkdtemp(prefix="tma_gap_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    # the tick-publish suite's "same as the original" checks read this backup
    for keep in ("backend/app/engine/tma2/tma2_tick_engine.py.bak-TMA_TICK_LTP_PUBLISH_20261015",
                 "backend/app/engine/tma/tma_tick_engine.py.bak-TMA_TICK_LTP_PUBLISH_20261015"):
        if (root / keep).exists():
            shutil.copy2(root / keep, tmp / keep)
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="tma_gap_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    t2, t1 = tmp / "backend/app/engine/tma2", tmp / "backend/app/engine/tma"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [
        ("TMA gap close (V1 + V2)", [py, "test_tma_gap_close.py"], t2, 600),
        ("TMA_V2 live signal engine", [py, "test_tma2_live_signal_engine.py"], t2, 300),
        ("TMA_V2 end to end", [py, "test_tma2_e2e.py"], t2, 300),
        ("TMA_V2 exit paths", [py, "test_tma2_exit_paths.py"], t2, 300),
        ("TMA_V2 entry / unwind paths", [py, "test_tma2_entry_paths.py"], t2, 300),
        ("TMA_V2 live core", pt + ["test_tma2_live_core.py"], t2, 300),
        ("tick LTP publish", [py, "test_tick_ltp_publish.py"], t2, 300),
        ("TMA_V1 signal engine", [py, "test_tma_signal_engine.py"], t1, 300),
        ("TMA_V1 exit paths", [py, "test_tma_exit_paths.py"], t1, 300),
        ("TMA_V1 live core", pt + ["test_tma_live_core.py"], t1, 300),
    ]
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
  1. ./desktop/build-scalp.sh backend   (non-trading hours)
  2. restart; any [TMA2_LIVE][GAP_CLOSE] / [TMA_LIVE][GAP_CLOSE] line is a bar the old engine dropped
  3. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete backend/app/engine/tma2/test_tma_gap_close.py.""")


if __name__ == "__main__":
    main()
