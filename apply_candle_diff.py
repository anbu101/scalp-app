#!/usr/bin/env python3
# apply_candle_diff.py — ── LIVE_CANDLE_DIFF_20261102 ── (requires PARITY_CFG_FALLBACK_20261102)
#
# Measure, every day, whether the live strategies decide on the same 1m NIFTY
# spot candles the backtest replays. 2026-11-02 Velvet: the recorded 14:35 bar
# opened 1.30 pts above the channel top (backtest flipped), the live
# tick-built bar did not (live flipped 5 min later). One day cannot tell
# whether 1–3 pt open differences are rare or routine — this tells us.
#
# WHAT IT DOES (read-only to trading)
#   engine/tma2/tma2_tick_engine.py  (the tick engine Velvet AND Timberwolf use)
#     every finalised SPOT minute → recorder/live_candles.record(tag, candle,
#     ticks in the minute, second the first tick landed). Queued; one daemon
#     thread writes ~/.scalp-app/backtest/recorder/live_spot.db; any failure
#     is swallowed — the tick path never blocks or raises.
#   recorder/live_candles.py (NEW)  compare(day) per feed vs the recorder's
#     Kite candles: minutes live / recorded / missing, |Δ| open/high/low/close
#     (mean, p95, max + when), 5m bars, opens off by ≥1 / ≥2 pts, worst five
#     opens, first-tick second, ticks per minute, and a boundary check: the
#     share of minutes whose live open sits nearer the recorded PREVIOUS close
#     than the recorded open — LTP-mode ticks carry no exchange time, so a
#     tick's minute is the Mac's clock at arrival; this exposes ticks landing
#     in the wrong minute.
#   parity/parity_core.py  the 15:50 parity message gets one line:
#     "🕯 Spot candles live vs recorded — Velvet feed 375/375 min · Δopen p95 …"
#   parity/parity_routes.py  GET /api/parity/candles?day=  (admin, like parity)
#   frontend Backtest → Parity  a "Spot candles — live vs recorded" table for
#     the selected day, below the comparison
#   recorder/test_live_candles.py (NEW)
#
# Data starts from the day this build runs; recorded candles arrive with the
# recorder's final pull (~15:40).
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, suites + esbuild parse on a scratch
# copy first (≈3 min), backups, all-or-nothing writes, build copies updated.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_candle_diff.py
#   then: ./desktop/build-scalp.sh both   (non-trading hours)
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

FENCE = "LIVE_CANDLE_DIFF_20261102"
FENCE2 = "LIVE_CANDLE_DIFF_20261102"
PARENTS = {
    "backend/app/engine/tma2/tma2_tick_engine.py": ["class CandleBuilder", "def _store_candle", "WS_SELFHEAL_20261021"],
    "backend/app/parity/parity_core.py": ["PARITY_CFG_FALLBACK_20261102", "def parity_daily_job"],
    "backend/app/parity/parity_routes.py": ["@router.get(\"/result\")"],
    "frontend/src/pages/backtest/ParityPanel.jsx": ["PARITY_OPEN_PAIR_20261102"],
    "backend/app/recorder/rec_db.py": ["SPOT_TOKEN = 256265"],
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
    tmp = Path(tempfile.mkdtemp(prefix="candle_diff_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for keep in ("backend/app/engine/tma2/tma2_tick_engine.py.bak-TMA_TICK_LTP_PUBLISH_20261015",
                 "backend/app/engine/tma/tma_tick_engine.py.bak-TMA_TICK_LTP_PUBLISH_20261015"):
        if (root / keep).exists():
            shutil.copy2(root / keep, tmp / keep)
    (tmp / "frontend/src/pages/backtest").mkdir(parents=True)
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="candle_diff_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be = tmp / "backend"
    t2, vet = be / "app/engine/tma2", be / "app/engine/vet"
    suites = [
        ("live vs recorded candles", [py, "app/recorder/test_live_candles.py"], be, 300),
        ("tick LTP publish (tick engine)", [py, "test_tick_ltp_publish.py"], t2, 300),
        ("TMA_V2 end to end", [py, "test_tma2_e2e.py"], t2, 300),
        ("VET end to end", [py, "test_vet_e2e.py"], vet, 300),
        ("recorder", [py, "app/recorder/test_recorder.py"], be, 600),
        ("parity", [py, "app/parity/test_parity.py"], be, 900),
        ("parity card / slippage view", [py, "app/slippage/test_slip_view.py"], be, 600),
    ]
    ok_all = True
    for name, cmd, cwd, tmo in suites:
        ok, tail = run(cmd, cwd, env, timeout=tmo)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {tail.splitlines()[-1] if tail else ''}", flush=True)
        if not ok:
            print("      " + tail.replace("\n", "\n      "))
            ok_all = False
    eb = esbuild_bin(root)
    jsx = tmp / "frontend/src/pages/backtest/ParityPanel.jsx"
    if eb:
        ok, tail = run([eb, str(jsx), "--loader:.jsx=jsx", "--format=esm", "--log-level=error",
                        "--outfile=" + str(tmp / "sc.js")], tmp)
        print(f"  {'PASS' if ok else 'FAIL'}  ParityPanel.jsx parses (esbuild)")
        ok_all = ok_all and ok
    else:
        print("  WARN  esbuild not found — ParityPanel.jsx parse check skipped (build-scalp.sh will compile it)")
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
  1. ./desktop/build-scalp.sh both      (non-trading hours)
  2. restart; recording starts with tomorrow's session (Velvet + Timberwolf feeds)
  3. after the 15:50 parity run: Telegram line + Backtest → Parity → "Spot candles — live vs recorded"
  4. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete recorder/live_candles.py + recorder/test_live_candles.py.""")


if __name__ == "__main__":
    main()
