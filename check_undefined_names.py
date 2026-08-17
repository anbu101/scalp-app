#!/usr/bin/env python3
# check_undefined_names.py
#
# ── UNDEFINED_NAME_GATE_20260817 ─────────────────────────────────────────
# WHY THIS EXISTS
#   py_compile validates SYNTAX ONLY. `gc_v1_runtime(broker_manager)` is
#   syntactically perfect and raises NameError at runtime. That single line
#   aborted _run_heavy_startup() on 2026-08-16 and 2026-08-17, killing
#   GC_V1, TMA_V1, BrokerReconciliationJob and ALL 13 EOD/morning crons —
#   silently, for a full trading session.
#
#   pyflakes F821 catches this class in milliseconds. Run it before every
#   backend build, alongside py_compile — never instead of it.
#
# FAIL-CLOSED BY DESIGN
#   If pyflakes is missing this exits NONZERO and says so. A check that
#   cannot run must never report success — that is the same failure mode
#   as the bug it is looking for.
#
# USAGE
#   python3 check_undefined_names.py                 # scans ./backend/app
#   python3 check_undefined_names.py path/to/tree    # scans a given tree
#
# EXIT CODES
#   0  clean (or only baselined hits)
#   1  undefined names found
#   2  pyflakes unavailable / scan could not run
# ─────────────────────────────────────────────────────────────────────────

import subprocess
import sys
from pathlib import Path

# Known hits that are NOT live bugs. Keep this list short and justified —
# every entry is a promise that someone checked.
BASELINE = {
    # Orphaned legacy file: class ZerodhaBroker with no __init__ and bare
    # `kite` refs. The live class is brokers/zerodha_broker.py; nothing
    # imports this module. DELETE IT, then remove this baseline entry.
    "app/brokers/zerodha.py",
}

SKIP_DIRS = {".git", "__pycache__", "node_modules", "venv", ".venv",
             "build", "dist", "site-packages"}


def pyflakes_available() -> bool:
    try:
        r = subprocess.run([sys.executable, "-m", "pyflakes", "--version"],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    # A missing module exits nonzero with an empty stdout — that is exactly
    # how the original version of this check silently passed. Trust the
    # returncode, never the emptiness of stdout.
    return r.returncode == 0


def main() -> int:
    tree = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
        else Path.cwd() / "backend" / "app"

    if not tree.exists():
        print(f"FAIL: tree not found: {tree}")
        return 2

    if not pyflakes_available():
        print("FAIL: pyflakes is not installed — the undefined-name gate "
              "CANNOT run.")
        print("      Install it, then re-run:")
        print("          pip3 install pyflakes")
        print("      (add --break-system-packages if pip refuses)")
        print("      Refusing to report success on a check that did not run.")
        return 2

    files = [p for p in tree.rglob("*.py")
             if not (SKIP_DIRS & set(p.parts))]
    if not files:
        print(f"FAIL: no .py files under {tree}")
        return 2

    print(f"Scanning {len(files)} files under {tree} ...")

    try:
        r = subprocess.run([sys.executable, "-m", "pyflakes",
                            *[str(p) for p in files]],
                           capture_output=True, text=True, timeout=600)
    except Exception as e:
        print(f"FAIL: pyflakes run errored: {e!r}")
        return 2

    hits = [ln for ln in r.stdout.splitlines() if "undefined name" in ln]

    live, baselined = [], []
    for ln in hits:
        path = ln.split(":", 1)[0]
        try:
            rel = str(Path(path).resolve().relative_to(tree.parent))
        except ValueError:
            rel = path
        (baselined if rel in BASELINE else live).append(ln)

    if baselined:
        print(f"\n{len(baselined)} baselined hit(s) ignored "
              f"(see BASELINE in this file):")
        for f in sorted({ln.split(':', 1)[0] for ln in baselined}):
            print(f"  - {f}")

    if live:
        print(f"\nFAIL: {len(live)} undefined name(s) — DO NOT BUILD:\n")
        for ln in live:
            print(f"  {ln}")
        print("\nEach of these raises NameError the moment its line executes.")
        return 1

    print("\nPASS: no undefined names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
