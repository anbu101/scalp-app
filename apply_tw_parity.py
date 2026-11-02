#!/usr/bin/env python3
# apply_tw_parity.py — ── TMA2_PRERANGE_WARMUP_20261102 ── ── PARITY_TW_20261102 ──
# (requires PARITY_OPEN_PAIR_20261102)
#
# Backtest → Parity, Timberwolf (TMA_V2) 2026-11-02: "trades differ · 2 extra"
# — the app (paper) sold 23400PE + bought 22750PE at 14:21, the backtest took
# no trade, "no skip reason reported". Three causes, all on the backtest side:
#
#  1. WARM-UP (the root cause). Live warms its 5m EMA 13/55/89/144 stack on
#     the 5 sessions BEFORE today, every morning. The runner only warmed on
#     sessions INSIDE its range, and parity replayed Timberwolf from the
#     previous session (10-30): one session of warm-up, EMA144 unwarmed at
#     14:20 → blocked_warmup → no signal. Proven on the synthetic corpus: a
#     2-session replay misses live's signals; a 1-day replay with the prior
#     5 sessions reproduces them minute for minute.
#  2. MODE. Live Timberwolf is SELL + hedge only and its config has no
#     "mode"; the runner defaults to BUY — any replayed signal would have
#     been a single long leg, not the spread.
#  3. DIAGNOSIS. The runner's "blocked_*" counters were not in the no-trade
#     note, so the page said "no skip reason reported".
#
# THIS SCRIPT
#   backend/app/backtest/tma/backtest_tma_v2_runner.py
#     seeds its warm-up window with the WARMUP_DAYS (5) spot sessions before
#     date_from (within 21 days) — warm-up only, never traded or counted;
#     accepts a 1-session range once those exist; config pre_range_warmup
#     (default True; False reproduces older runs). A range that starts at
#     the corpus' first day is byte-identical (trades AND diag).
#   backend/app/backtest/replay/overlays.py
#     Trade Replay's EMA overlay mirrors the same warm-up bound, so the
#     chart still shows the EMAs the runner traded on
#   backend/app/parity/parity_core.py
#     Timberwolf replays the day on its own (no forced 2-session range);
#     SELL mode for its live config; blocked_* counters in no-trade notes
#   backend/app/backtest/tma/test_tma2_prerange_warmup.py (NEW)
#
# EFFECT ON EXISTING BACKTESTS: only the first ~2 sessions of a range can
# change (they now trade on warmed EMAs, like live). Multi-year seals that
# start at the corpus' first day are unchanged.
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, the TMA_V2 / parity / Trade Replay /
# lot-compounding suites run on a scratch copy first (≈10 min), backups,
# all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_tw_parity.py
#   then: ./desktop/build-scalp.sh backend → Parity → 2026-11-02 · Timberwolf → Compare
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

FENCE = "TMA2_PRERANGE_WARMUP_20261102"
FENCE2 = "PARITY_TW_20261102"
PARENTS = {
    "backend/app/backtest/tma/backtest_tma_v2_runner.py": ["TMA2_XDAY_WARMUP", "WARMUP_DAYS = 5"],
    "backend/app/backtest/replay/overlays.py": ["def _tma(ctx, v2: bool)"],
    "backend/app/parity/parity_core.py": ["PARITY_OPEN_PAIR_20261102", "PARITY_DAILY_20261030", "def skip_notes"],
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
    tmp = Path(tempfile.mkdtemp(prefix="tw_parity_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    # the DTE-lots suite's decisive OFF ≡ ORIGINAL check reads the pre-DTE runner backup
    keep = "backend/app/backtest/tma/backtest_tma_v2_runner.py.bak-TMA2_DTE_LOTS_20260922"
    if (root / keep).exists():
        shutil.copy2(root / keep, tmp / keep)
    else:
        print(f"  note: {keep} not found — the DTE-lots OFF ≡ ORIGINAL comparison will be skipped")
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="tw_parity_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be = tmp / "backend"
    suites = [
        ("TMA_V2 pre-range warm-up + Timberwolf parity", [py, "app/backtest/tma/test_tma2_prerange_warmup.py"], be, 900),
        ("TMA_V2 engine", [py, "app/backtest/tma/test_tma_v2_engine.py"], be, 600),
        ("TMA_V2 DTE lots (OFF ≡ ORIGINAL)", [py, "test_tma_v2_dte_lots.py"], be / "app/backtest/tma", 900),
        ("parity", [py, "app/parity/test_parity.py"], be, 900),
        ("parity open/closed pairing", [py, "app/parity/test_parity_open_pair.py"], be, 300),
        ("Trade Replay (EMA overlay parity)", [py, "app/backtest/replay/test_trade_replay.py"], be, 1500),
        ("lot compounding across runners", [py, "app/backtest/engine/test_lot_comp_runners.py"], be, 1500),
    ]
    ok_all = True
    for name, cmd, cwd, tmo in suites:
        print(f"  …     {name}", flush=True)
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
  2. restart → Backtest → Parity → 2026-11-02 · Timberwolf → Compare (Paper)
  3. commit + push (fences {FENCE}, {FENCE2})
Rollback: copy each *.bak-{FENCE} back and delete backend/app/backtest/tma/test_tma2_prerange_warmup.py.""")


if __name__ == "__main__":
    main()
