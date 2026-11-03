#!/usr/bin/env python3
# apply_ic_paper_fallback.py — ── IC_PAPER_FALLBACK_20261103 ── (requires IC_FILL_ANCHOR_20261102)
#
# 2026-11-03 09:18:03 "IC_V2 entry blocked: margin shortfall" in PAPER_LIVE
# stopped BOTH books. In PAPER_LIVE the paper book is a TWIN of the live rows
# (trading/shadow_book.py): no live row → no paper row. Paper has no margin,
# so a margin refusal must only stop LIVE.
#
# THIS SCRIPT  backend/app/engine/ic/ic_group_manager.py (shared by IC_V1/IC_V2)
#   * margin shortfall + CLEAN PAPER_LIVE read → today's group runs PAPER-only:
#     no broker order, rows in the paper book, every leg (and any ·ADJ leg)
#     sized exactly as its paper twin would have been (paper_lots), flag
#     persisted so a restart keeps it; alert (warning) says so and names the
#     numbers: "LIVE entry blocked: margin shortfall (required ₹X, available
#     ₹Y) — the paper book trades on its own today (PAPER_LIVE)"
#   * LIVE-only mode: unchanged hard block — the alert now carries the numbers
#   * enough margin: unchanged
#   backend/app/engine/ic/test_ic_paper_fallback.py (NEW)
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, IC suites on a scratch copy first,
# backups, all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_ic_paper_fallback.py
#   then: ./desktop/build-scalp.sh backend   (after 15:30 — NOT during market hours)
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

FENCE = "IC_PAPER_FALLBACK_20261103"
FENCE2 = "IC_PAPER_FALLBACK_20261103"
PARENTS = {
    "backend/app/engine/ic/ic_group_manager.py": ["IC_FILL_ANCHOR_20261102", "FILL_TRUTH_20261102",
                                                  "D8 margin guard", "def _margin_ok"],
    "backend/app/risk/execution_modes.py": ["def shadow_active"],
    "backend/app/trading/paper_lots.py": ["def resolve_twin_lots"],
}
PAYLOAD = "eNrlWo1z2jgW/1e0dG7P7jiuTYCm3LAzaZJts0ubTprZmztgPMIWxIuxiGySsJn87/eeJOMPDPnq3u3u0U4A6+lJ71O/98RdgwVhmjS65K4xpv6MxcEbuli8YfE0jNmb0If/3lTw5cKb05hOmbAXK0m9EAzeG057Mm6+dd513LYf+K3Ad1rtoENbnbftYOzsu2N3QqnjvG257eaBvz+hnc7YD/aDwO10Wo6/37BIY8GTFHm12/tvW0jQbrrvmp2g5R502L7fDigD1vvtMQs6TX980Om0/QM2GfvOwcG7/XdtZ9xstSjzkdflMp6hOINBg+hXwqKJ7dHg12WSejyOVqRHfqRRwoZxRvKKDJfNtuOov+T0yPulWX6U+JcsWEYsINRPQx4nxAjjVNCArgjy/Ac5Pj/78uXkmNCU+FSI1TDG/bxoE18Ov5ycez8e9vvvD49+9ppOs+O6zn6Zrn/6ywlhsJcVEWyyTGCLPCZzKsCCJIyL/BU/OQEmg45JykGAvydEmpiIJcglifbkFkGU9JKRBV0wQdKbMAbKJPytsGclliTwJjSK0Ic2JVNUEQXR1b665GyBWqTRIF0uIjaCOZ95zOQuDcGulqFggUXoNQ0jOo6YSfhE7gWZELCFP/vdrTeySNWJlCZ6xJjzgJHvemTYQG0OG+ZTdnN4/JN39rn/ry7xeRxwQSI2TQgVoOlLGqd8noAlp1QEEUsSFBxXq3Onx2xnl4VI5fU8D/xviF6yRTghMU9LOqBxUHhWDLPyiHI/j88MnwtmEX8yNbu5APgSDIYCj0ZMpMawAUJ8Ojz/cPrZe98/O/p5CFaYDBt3kl0CLpSy6coLg3sdguOIQxYNulkAJpdcpKh3mFhep/RK2DUTYbrqDRtMCC5wmQL3XnU5SyoGqKPwmpXsrURIlyLOorDsOb+38q4waiPwsI2gJ2Buw7Edi8Cfyo7jJVi+h5rNwx89xxm/u4PvXct2JveFfJCNwVJqzISZIBrQEoaePWzAv/jlzg0ZtJg283Q45nxGQvDd6iqYJ7NsJecIfpOANTmh2in2tJMUU7fM2ymvMkvCiMX+OgVzTgzc5p7r7sFGnXdd98C0yRc5ekkTMBbJMiwk842tlbJ9nugtmdUDEoUzJvctZbjhyygArteMjBmL7TI3UHZdaoG4ifgNC4yqZzwqtHZECL7qA6+gxa3Rd4cOdi+9xm2RqmvULVSxNB4WLMGTFeAS4TexUiYxcucwHxagEOY3VMRhPH1ZoOcJHuQHK5RtocNUpDJULRLx1ENDQwYTjP3GapjJgwTPESlWVU8YWc8x6/MyprLZk5T6DXJnbf4sHT9X6Up96EFySA0Kuh0AM54mw8bIJK/Xes4Zg5zdchp+BpMtEdet08kzk52M+/Kq2WazhI4kHjwwpE2F78EJjtY8RLXD86KbwbDNF6mXrhbwDQGeuaGTkmrxNWxI+YYA4gviVpxg2JDAAGjw3Srr9glcyqqsUK+fW98IH9XvfEMDeVRjAIPm8W1b4IsUCES6bTiDiJDEImNBVxGngT1lGKdaQRa5EEtmbs0s5Zqlhk+BAKJpKx9VRYYBMCnPzwZ2ZDcJyUEb83mYpgxZ4J6lRWgkGA1WBGRJwgQGN33hj6XPDSi+jVXBLU3zG3nfn9qsz00VJUm6m/L/udLHY6TZkVL+Fwlhe0D+H4eTMpLiF7AJKZRXyNKSWapLPmAYHa3rLbL3g+Su4YSa/nhR3p8ALqsRSG5gC5LH3RTXzV0R/52hxACLKTnqnxx+LhZLGMHEQCAtYeXNJYPsS4uomt1CbCc5SxrxeJqEQIzYDCob5KN6UTPGFoksTi6pCBRqtNUWqpiihOQFnxO6WNgiTGY2u2X+EhtAHu4oIeF8AWATICcN+I2HvaFrVltKS4OWyIwqrCy6KLv12SIlJ/INluvurs/XFliDK+UBClp1Ec1aUiM41kXAmKMs/TWHWclqPuaRNBiMbNjrAjR4taRxCoi50t1TVSvUtLBusfrDNB2y4CnaxnIJqhtbuRTC2kzXgiU8AkmkqBLvljhIKSV9DwqCW8O1JD7OhDfJmzel51oLZjVBpJFFPDzJq8sZm+XAek2sg6EyqOqyp94eqkPytbFW9PyIJklPmbC+P1OQI41MBP0lkba7E4FKv1qI3UAJxDy6DMIUZJkiPB+cHo0Gm3XXaFDODqPB19N/n3gn5+cjcqfRfJfcse/EumBGDam6YEurqWSjkkvXFqZbEpxIu+Q49NOBdPgAPo0qpav2dlW/yi/S0bG06Nb2PDWmeLAfWiaaYGdSugZEAgQG7tTGVqUNSp4nG82Nq83yKJtuV2oifJQ7GH7TAbvRXrnCjq7mUFN1v8DgYGasv+/y1e8f7ono/ove0H12g3B3dU+MQg5RrluH+fRMUNXV5uBmLll7WDqA3Y6gOk6i0GdYH6O28bNU9FXmDrmWaxZ/ICPvXmxwNap4/ZpMo5TRhgu9xDz69Kxe5jyie4WNq51XOkbMyVjwGZiLiwDAdW6rx6OIk8/HFQzxEhizAVbzLm+PTABCpQZk8QxGZWOwbWwob7aSVRdYdZB/II7sYeccsycFkvVgdxOpvmwrmy3w3pbbrW+ER18m+2h0fw/yx+xmx31wykCa0K9kUHUr3HhFnjhpGL+C/89rVuVIswuocuc1aMxxFQSX4Pe6p55Umvk2qXbWu0Q2M39pVjqVyGvzegdQGl8sYNn3ZxcfJcckS4x4TVC6F5DnKd4LmDYyO1/Ghc5ylxA/qFck+f57slillzzeJ3tz/AiKJXtXZIeCNe7KKEqX+QgkPg5jCdvqCfRs48MnwFOsCeHrLW4h46JPwyl5A1jcAmXMmAfOzuTlqoW+HPMr2iU/thz3Uaip7361SL8Jf/yI0djcTf1KUUE00WXKwdZkEt4CDmFaFJBCghHZ7tES9L2zLyefLfJBvmPGuvgnlEI94jpl1jlk7eZ3IQoCJ0A8XDpO8Bbs6e4BjbKkRgkSO6MakfswLtRUSGXMeTxjqwVN/UtQGUD2uCdLW30UFYbthKU0TYUBVMPGlroMS+OIzscB6JnH5kNMMmCST1MIB47PCIAvTyXa7QLqeE1QM2YuhPSMXJAsw8tw8PCA0YN4wBSlXEtmkRkchqBr9KAShSLYriVNgNNtvew6ZVmlx+ukhhZ1PMdxLNJSHxQPQOMM/GBuK0QKh6RR9lvDXN8M6Fv0Os9Dl8CLnykHd6CXUOLisdzsqEWmWLbY/lIIWEWFkmGW1gfEBWYBZfS0K8qUPLdDrUXD3OX3ai2qj/QSY7ynlQph17B2Qp7ywngtwQILH3y4uCitAGnT+Ggfvy/AHhPlaK3vifWoulgZba6CWRFMJGOlmHtLy9TcIOGMj/Zh/+T84muJFmLBELCidOwR+RtRQd2Dkw4xvFATS1u2r2m0ZIDhzfy6Nr/yKPCNNFwFh2p3HB0WqjSQ4T5VFUGRnRaz2SKyhNW5otNWaUIjZvXUdSoBJq/PvJjOWeKBdrx4OR8DPvvjBlTCGKbggcbGglG89v/wyS7eB25PS3iiDBsl2jw1+Xg3QuYJlH6vX8+6xMDFbDgL4Ug04DGEKq5nlOnWXYCnBLmekWDoxuw2NW6ljW/RxFJEAFXDRs15D19vzYrnVn67APqyUF+SGJfAQCkgR03m1pPVXT+vaSreIxMzJmAvScMo8rBJ5mnA8o1dSJ5csnf14DEtQZmBmaF4Tf6tUzryf7rFt6TNSipb13f1Ka5iBRbz5fQyOyCXsX9JAXIGv08MP+NAq1Fb4dxBEQs5XotYn+JLeinor6KQHLSo9rdsL2V4AaYqWCJ/ffKXQg4PogFA0QZuQqPeVntdPMqtLW4HAImxq+C6B7aTZTUee2nozwzXtYjbxIKTkCce9Cqp9F1Zrii+RTBvrL0r+BVW12ecKnj77mHVh5AqTKRjyN9vyoIz+DXHOH0Nt+UJwW+yHCsePp5lMatXLnRUEZb15C8dN3cDC9Rjgcx5dwR6fUCv/TdZimugSMBbQDaR/gW9Vd9XqTMcSih1Owtugr1PGAAOSYLzP9oXJ18vvK+nx2X1ZwxQs7V3eSb6irzi3UJSvKPLaLVum2pfp0eyk/RJladG8Xc++bYsoi56uOjN7ewjYIt04en7ANHTQAOKPB1wch+JWUmuTRsNDirIpPf0trPrPZU2ga6SSOFJpXAbxo37+/8AbZl1Aw=="


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
    tmp = Path(tempfile.mkdtemp(prefix="ic_pfb_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="ic_pfb_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    ic = tmp / "backend/app/engine/ic"
    pt = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    suites = [
        ("IC paper fallback on margin", pt + ["test_ic_paper_fallback.py"], ic),
        ("IC group manager + e2e", pt + ["test_ic_group_manager.py"], ic),
        ("IC fill truth", pt + ["test_ic_fill_truth.py"], ic),
        ("IC fill anchor", pt + ["test_ic_fill_anchor.py"], ic),
        ("IC engine / GTT monitor / EOD", pt + ["test_ic_engine_monitor_eod.py"], ic),
        ("IC live core", pt + ["test_ic_live_core.py"], ic),
    ]
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
  2. restart Scalp Terminal
  3. commit + push (fence {FENCE})
Rollback: copy ic_group_manager.py.bak-{FENCE} back and delete engine/ic/test_ic_paper_fallback.py.""")


if __name__ == "__main__":
    main()
