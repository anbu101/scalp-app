#!/usr/bin/env python3
# fix_bootstatus_route_order_20260817.py
#
# ── BOOT_ISOLATION_20260817 / ROUTE_ORDER FIX ────────────────────────────
# BUG (mine, from apply_boot_isolation_20260817.py):
#   @app.get("/boot-status") was spliced in next to _run_heavy_startup(),
#   i.e. AFTER the SCALP_UI_SERVE `app.mount("/", StaticFiles(...))`.
#
#   Starlette matches app.routes in REGISTRATION ORDER and a Mount at "/"
#   matches every path. So the mount claimed /boot-status first and
#   StaticFiles returned its own 404 — indistinguishable from "route does
#   not exist": {"detail":"Not Found"}.
#
#   The existing routers escape this only because they are all registered
#   ABOVE the mount. The block comment at SCALP_UI_SERVE says mounts sit
#   last in the table; that is true of the ORIGINAL file, and it is exactly
#   the invariant my splice broke.
#
# FIX
#   Delete the route from its spliced position and re-register it
#   immediately BEFORE the SCALP_UI_SERVE block, alongside the other
#   explicit routes. Handler body is unchanged — it reads app.state at
#   request time, so position has no functional effect beyond precedence.
#
# Requires apply_boot_isolation_20260817.py to have run. Idempotent-safe:
# aborts if the route is already above the mount.
# ─────────────────────────────────────────────────────────────────────────

import ast
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
REL = Path("app/api_server.py")
TREES = [REPO / "backend", REPO / "desktop/src-tauri/backend"]

# The route exactly as apply_boot_isolation spliced it in.
OLD_ROUTE = '''

@app.get("/boot-status")
def boot_status():
    """
    D3: makes a partial boot answerable without log archaeology.
    degraded=True means the process is serving but at least one component
    did not start. failures[] names them.
    """
    return {
        "startup_complete": getattr(app.state, "startup_complete", False),
        "startup_phase": getattr(app.state, "startup_phase", "unknown"),
        "degraded": getattr(app.state, "startup_degraded", False),
        "failures": getattr(app.state, "startup_failures", []),
    }
'''

MOUNT_ANCHOR = '''# ====================================================================
# >>> SCALP_UI_SERVE BEGIN <<<'''

NEW_ROUTE = '''# ── BOOT_ISOLATION_20260817 (route) BEGIN ────────────────────────────
# MUST stay ABOVE the SCALP_UI_SERVE mount below. Starlette matches routes
# in registration order and Mount("/") matches everything, so any explicit
# route registered after it is unreachable (returns StaticFiles' 404).
@app.get("/boot-status")
def boot_status():
    """
    D3: makes a partial boot answerable without log archaeology.
    degraded=True means the process is serving but at least one component
    did not start. failures[] names them.
    """
    return {
        "startup_complete": getattr(app.state, "startup_complete", False),
        "startup_phase": getattr(app.state, "startup_phase", "unknown"),
        "degraded": getattr(app.state, "startup_degraded", False),
        "failures": getattr(app.state, "startup_failures", []),
    }
# ── BOOT_ISOLATION_20260817 (route) END ──────────────────────────────


''' + MOUNT_ANCHOR


def check(label, cond):
    print(f"  [{'ok  ' if cond else 'MISS'}] {label}")
    return cond


def pyflakes_undefined(path):
    r = subprocess.run([sys.executable, "-m", "pyflakes", str(path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode not in (0, 1) or "No module named" in r.stderr:
        raise RuntimeError(f"pyflakes could not run: {r.stderr.strip()}")
    return [l for l in r.stdout.splitlines() if "undefined name" in l]


def main():
    staged, all_ok = [], True

    for tree in TREES:
        path = tree / REL
        print(f"\n=== {path} ===")
        if not path.exists():
            print("  [SKIP] tree not present")
            continue
        t = path.read_text(encoding="utf-8")
        all_ok &= check("isolation patch applied",
                        "BOOT_ISOLATION_20260817" in t)
        all_ok &= check("spliced route present (exactly once)",
                        t.count(OLD_ROUTE) == 1)
        all_ok &= check("mount anchor unique",
                        t.count(MOUNT_ANCHOR) == 1)
        all_ok &= check("route currently BELOW mount (the bug)",
                        MOUNT_ANCHOR in t and OLD_ROUTE in t
                        and t.index(MOUNT_ANCHOR) < t.index(OLD_ROUTE))
        staged.append((path, t))

    if not staged:
        print("\nABORT: nothing to do.")
        return 1
    if not all_ok:
        print("\nABORT: pre-anchor failure — NOTHING written.")
        return 1

    print("\n=== dry run ===")
    out = []
    for path, t in staged:
        tag = path.parts[-4]
        new = t.replace(OLD_ROUTE, "\n").replace(MOUNT_ANCHOR, NEW_ROUTE)
        ok = True
        ok &= check(f"{tag}: route defined exactly once",
                    new.count('@app.get("/boot-status")') == 1)
        ok &= check(f"{tag}: route now ABOVE mount",
                    new.index('@app.get("/boot-status")')
                    < new.index('# >>> SCALP_UI_SERVE BEGIN <<<'))
        ok &= check(f"{tag}: mount block intact",
                    new.count('# >>> SCALP_UI_SERVE BEGIN <<<') == 1)
        ok &= check(f"{tag}: guard helper still present",
                    "def _boot_guard(" in new)
        ok &= check(f"{tag}: 13 crons still present",
                    new.count("scheduler.add_job(") == 13)

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(new)
            tmp = Path(tf.name)
        try:
            py_compile.compile(str(tmp), doraise=True)
            ok &= check(f"{tag}: py_compile", True)
        except py_compile.PyCompileError as e:
            print(f"  [MISS] {tag}: py_compile — {e}")
            ok = False
        try:
            ast.parse(new)
            ok &= check(f"{tag}: ast.parse", True)
        except SyntaxError as e:
            print(f"  [MISS] {tag}: ast.parse — {e}")
            ok = False
        try:
            und = pyflakes_undefined(tmp)
            ok &= check(f"{tag}: pyflakes clean", not und)
            for l in und:
                print(f"         {l}")
        except RuntimeError as e:
            print(f"  [MISS] {tag}: {e}")
            ok = False
        tmp.unlink(missing_ok=True)

        all_ok &= ok
        out.append((path, new))

    if not all_ok:
        print("\nABORT: dry-run failure — NOTHING written.")
        return 1

    print("\n=== writing ===")
    for path, new in out:
        path.write_text(new, encoding="utf-8")
        print(f"  written: {path}")

    print("\nDONE. Restart the backend, then:")
    print("  curl -s localhost:47321/boot-status | python3 -m json.tool")
    return 0


if __name__ == "__main__":
    sys.exit(main())
