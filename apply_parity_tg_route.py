#!/usr/bin/env python3
# apply_parity_tg_route.py — ── PARITY_TG_ROUTE_20261102 ──
#
# The 15:50 parity card (and its text fallback) went to the Personal Alerts
# chat only. It now goes where the 15:40 EOD card goes: every Telegram
# channel with "Daily Summary" ticked, whatever that channel's Mode filter
# (your group: Live + Daily Summary). Only when NO channel has Daily Summary
# does it fall back to the Personal Alerts chat, so it is never lost.
# The card and its lines use codenames only (Tigris, Ember, Velvet, Icarus,
# Timberwolf; "Velvet feed" / "Timberwolf feed" on the candle line).
#
# FILES  backend/app/parity/parity_card.py (send_card)
#        backend/app/parity/parity_core.py (_notify text fallback)
#        backend/app/parity/test_parity_tg_route.py (NEW)
#
# SAFETY: fence check, `git status` stop sign, every anchor exactly once,
# py_compile gate, routing + parity + parity-card suites on a scratch copy
# first, backups, all-or-nothing writes, build copies updated.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_parity_tg_route.py
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

FENCE = "PARITY_TG_ROUTE_20261102"
FENCE2 = "PARITY_TG_ROUTE_20261102"
PARENTS = {
    "backend/app/parity/parity_card.py": ["def send_card", "NOTIF_PERSONAL_ALERTS"],
    "backend/app/parity/parity_core.py": ["PARITY_DAILY_20261030", "def _notify"],
    "backend/app/api/telegram_api.py": ["NOTIF_DAILY_SUMMARY", "def _iter_active_channels", "def _fanout"],
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
    tmp = Path(tempfile.mkdtemp(prefix="tg_route_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="tg_route_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be = tmp / "backend"
    suites = [
        ("parity Telegram route", [py, "app/parity/test_parity_tg_route.py"], be, 300),
        ("parity card / slippage view", [py, "app/slippage/test_slip_view.py"], be, 600),
        ("parity", [py, "app/parity/test_parity.py"], be, 900),
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
  2. tomorrow's 15:50 parity card lands in every Daily Summary channel (your group)
  3. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete backend/app/parity/test_parity_tg_route.py.""")


if __name__ == "__main__":
    main()
