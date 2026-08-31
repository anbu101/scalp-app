#!/usr/bin/env python3
# apply_brk_v1_nolevelguard_20260830.py
#
# ── BRK_V1_NO_LEVEL_GUARD_20260830 ── remove the `break_above >= select_below`
# validation from the BRK_V1 runner (both trees) and its sim assertion.
# Requested 2026-08-30 so break level can sit below the selection ceiling
# (e.g. select <180, break >=178). Semantics: "break" is simply the last
# close holding at-or-above break_above at the decision minute; whether the
# contract was below that level at selection is no longer required.
#
# Assert-anchored, replace-once, staged py_compile, .bak-FENCE backups,
# idempotent. Run from the repo root:
#     python3 apply_brk_v1_nolevelguard_20260830.py --check
#     python3 apply_brk_v1_nolevelguard_20260830.py
from __future__ import annotations
import argparse, py_compile, shutil, sys, tempfile
from pathlib import Path

FENCE = "BRK_V1_NO_LEVEL_GUARD_20260830"
TREES = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
RUNNER = "backtest/brk/backtest_brk_runner.py"
TEST = "backtest/brk/test_brk_runner_sim.py"

R_OLD = '''    if cfg["break_above"] < cfg["select_below"]:
        # A break level BELOW the selection ceiling means a contract can be
        # selected already above its own breakout level; refuse loudly.
        return _abort(cfg, strategy_id,
                      f"break_above {cfg['break_above']} must be >= "
                      f"select_below {cfg['select_below']}")
'''
R_NEW = f'''    # ── {FENCE} ── the break_above >= select_below guard was removed on
    # request: a break level below the selection ceiling is allowed, and a
    # contract already at-or-above it at 09:30 simply qualifies.
'''
T_OLD = '''r = run(build(rows), {"break_above": 150})
chk("11b. break level below selection ceiling -> aborted",
    r.get("aborted") and "break_above" in r["reason"])
'''
T_NEW = f'''r = run(build(rows), {{"break_above": 150}})
chk("11b. break level below selection ceiling is ALLOWED ({FENCE})",
    not r.get("aborted"))
'''

class Abort(Exception): pass

def patch(path: Path, old: str, new: str, check: bool) -> str:
    if not path.exists():
        raise Abort(f"missing: {path}")
    text = path.read_text()
    if FENCE in text:
        return "already fenced — skipped"
    n = text.count(old)
    if n != 1:
        raise Abort(f"{path}: anchor found {n} times, expected 1 — file drifted")
    text = text.replace(old, new, 1)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text); tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if not check:
        shutil.copy2(path, path.with_name(path.name + f".bak-{FENCE}"))
        path.write_text(text)
    return "patched" if not check else "would patch (clean)"

def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true")
    ap.add_argument("--allow-missing-tree", action="store_true")
    a = ap.parse_args()
    present = [t for t in TREES if t.exists()]
    missing = [t for t in TREES if not t.exists()]
    if missing and not a.allow_missing_tree:
        print(f"ABORTED: dual-tree not satisfiable, absent: {[str(m) for m in missing]}", file=sys.stderr); return 1
    out = []
    try:
        for t in present:
            out.append((str(t / RUNNER), patch(t / RUNNER, R_OLD, R_NEW, a.check)))
            out.append((str(t / TEST), patch(t / TEST, T_OLD, T_NEW, a.check)))
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr); return 1
    for what, how in out: print(f"  {how:<28} {what}")
    print(f"\n{FENCE} {'check complete' if a.check else 'applied'}.")
    if not a.check:
        print("\nNext: python3 backend/app/backtest/brk/test_brk_runner_sim.py .   then restart the backend")
    return 0

if __name__ == "__main__":
    sys.exit(main())
