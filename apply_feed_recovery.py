#!/usr/bin/env python3
# apply_feed_recovery.py — ── FEED_RECOVERY_20261103 ── (requires TMA_GAP_CLOSE_20261102 + VET_GAP_CLOSE_20261102)
#
# 2026-11-02: a friend's Mac slept 09:27–10:23 with the app left running. The
# WebSocket died, ~56 spot minutes were never built, and — because only a
# RESTART backfills — Timberwolf, TMA_V1 and Velvet kept computing their EMAs
# on a series missing ~11 five-minute bars for the rest of the day (EMA55 ended
# 17 points off). His 14:20 stack check came out differently, the signal the
# backtest and the other machine saw never fired, and nothing was logged.
#
# THIS SCRIPT — live signal engines only; order placement untouched
#   engine/feed_recovery.py (NEW) FeedRecovery: when a completed spot candle
#     arrives >= 3 minutes after the previous one (1–2 missing minutes are the
#     gap-close case), re-fetch today's 1m spot candles from Kite and rebuild
#     the engine's prefix from them — the restart backfill, run live. Signals
#     from the replay are DISCARDED (nothing is entered late). FAIL CLOSED:
#     until a rebuild succeeds, signals from the distorted stream are
#     suppressed; it retries every minute (20 attempts), alerts on the first
#     failure, on success, and when it gives up. A pending rebuild never
#     crosses into the next session.
#   engine/tma2/tma2_live_signal_engine.py, engine/tma/tma_live_signal_engine.py,
#   engine/vet/vet_live_signal_engine.py   last_candle_ts() + rebuild_today()
#   engine/tma2/tma2_selection_loop.py, engine/tma/tma_selection_loop.py,
#   engine/vet/vet_selection_loop.py       wire it before each candle is folded
#   engine/test_feed_recovery.py (NEW)     40 randomized days per engine with a
#     60-minute hole: un-repaired the bars/EMAs/signals diverge (40/40); with
#     recovery bars, EMAs and every later signal equal the unbroken feed
#     (40/40, V1 and V2); Velvet 5/5; coordinator suppression; Kite failure paths
# Audit lines: [TMA2|TMA|VET][RECOVER] spot feed gap HH:MM→HH:MM … rebuilt.
# Complete data: byte-identical behaviour (no recovery ever runs).
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, all TMA + Velvet live suites on a
# scratch copy first, backups, all-or-nothing writes, build copies updated.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_feed_recovery.py
#   then: ./desktop/build-scalp.sh backend   (after 15:30) — on EVERY machine that runs the app
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

FENCE = "FEED_RECOVERY_20261103"
FENCE2 = "FEED_RECOVERY_20261103"
PARENTS = {
    "backend/app/engine/tma2/tma2_live_signal_engine.py": ["TMA_GAP_CLOSE_20261102", "def start_day"],
    "backend/app/engine/tma/tma_live_signal_engine.py": ["TMA_GAP_CLOSE_20261102", "def start_day"],
    "backend/app/engine/vet/vet_live_signal_engine.py": ["VET_GAP_CLOSE_20261102", "PrefixGuard"],
    "backend/app/engine/tma2/tma2_selection_loop.py": ["class TMA2MinuteCoordinator", "fetch_today_spot"],
    "backend/app/engine/tma/tma_selection_loop.py": ["class TMAMinuteCoordinator", "fetch_today_spot"],
    "backend/app/engine/vet/vet_selection_loop.py": ["fetch_today_spot", "VET_WING_FAR_20261101"],
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
    tmp = Path(tempfile.mkdtemp(prefix="feed_rec_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="feed_rec_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be, t2, t1, vet = tmp / "backend", tmp / "backend/app/engine/tma2", tmp / "backend/app/engine/tma", tmp / "backend/app/engine/vet"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [
        ("feed recovery (TMA_V2 + TMA_V1 + Velvet, 60-min hole)", [py, "app/engine/test_feed_recovery.py"], be, 600),
        ("TMA gap close", [py, "test_tma_gap_close.py"], t2, 600),
        ("TMA_V2 live signal engine", [py, "test_tma2_live_signal_engine.py"], t2, 300),
        ("TMA_V2 end to end", [py, "test_tma2_e2e.py"], t2, 300),
        ("TMA_V2 exit paths", [py, "test_tma2_exit_paths.py"], t2, 300),
        ("TMA_V2 entry / unwind paths", [py, "test_tma2_entry_paths.py"], t2, 300),
        ("TMA_V2 live core", pt + ["test_tma2_live_core.py"], t2, 300),
        ("TMA_V1 signal engine", [py, "test_tma_signal_engine.py"], t1, 300),
        ("TMA_V1 exit paths", [py, "test_tma_exit_paths.py"], t1, 300),
        ("TMA_V1 live core", pt + ["test_tma_live_core.py"], t1, 300),
    ]
    for f in ("test_vet_live_signal_engine.py", "test_vet_gap_close.py", "test_vet_e2e.py", "test_vet_manager.py",
              "test_vet_live_confirm.py", "test_vet_live_core.py", "test_vet_sltp_live.py", "test_vet_wing_far.py"):
        if (vet / f).exists():
            suites.append((f"Velvet: {f}", [py, f], vet, 600))
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
  1. ./desktop/build-scalp.sh backend   (after 15:30) and restart — on EVERY machine that runs the app
  2. after any feed stall / Mac sleep, grep the audit log for [TMA2|TMA|VET][RECOVER]
  3. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete engine/feed_recovery.py + engine/test_feed_recovery.py.""")


if __name__ == "__main__":
    main()
