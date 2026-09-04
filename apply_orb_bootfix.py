#!/usr/bin/env python3
# apply_orb_bootfix.py — two boot-time fixes for the 2026-09-04 morning:
#
#   1. "SCALP BOOT FAILURE / launch Outrider / NameError: broker_manager"
#      The launch block passed a name that does not exist in api_server's
#      scope; VET and BRK both pass zerodha_manager. Fixed + wrapped in
#      _supervise like the neighbours. (_boot_guard contained the failure
#      exactly as designed — no other component was affected.)
#   2. Settings page crash: the loading gate (the big !scalpConfig || ...
#      line) was missing !orbConfig, so the first render dereferenced a
#      null orbConfig in the mode rail. Gate entry added.
#
# Fence: ORB_BOOTFIX_20260904. Both trees, staged, idempotent. Needs a full rebuild
# (PyInstaller + Tauri) and app restart to take effect. LIVE-PATH file
# (api_server) — apply OUTSIDE market hours.
#
# USAGE: python3 apply_orb_bootfix.py --check && python3 apply_orb_bootfix.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_BOOTFIX_20260904'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {}

EDITS = [('backend/app/api_server.py', 'replace', '            asyncio.create_task(orb_v1_runtime(broker_manager))\n', '            # ── ORB_BOOTFIX_20260904 ── the in-scope broker handle is\n            # zerodha_manager (boot NameError 2026-09-04); _supervise per\n            # the VET/BRK house pattern so a runtime death is logged.\n            _supervise(asyncio.create_task(orb_v1_runtime(zerodha_manager)), "orb_v1_runtime")\n', 1), ('frontend/src/pages/Settings.jsx', 'replace', '|| !brkConfig) {   // ← TSG_V1, TMA_V2, BRK_V1 added\n', '|| !brkConfig || !orbConfig) {   // ← TSG_V1, TMA_V2, BRK_V1, ORB_V1 added — ── ORB_BOOTFIX_20260904 ── a config missing from this gate crashes Settings on first paint (orbConfig was null at render)\n', 1)]

VERIFY = [('backend/app/api_server.py', 'orb_v1_runtime(zerodha_manager)', 1), ('backend/app/api_server.py', 'orb_v1_runtime(broker_manager)', 0), ('frontend/src/pages/Settings.jsx', '!orbConfig', 1)]



def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    """A backend-relative path lands in both trees; frontend in one."""
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def stage_edit(text, kind, anchor, payload, count, path):
    n = text.count(anchor)
    if kind == "replaceall":
        if n != count:
            fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
        return text.replace(anchor, payload)
    if n != count:
        fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
    if kind == "replace":
        return text.replace(anchor, payload)
    if kind == "before":
        return text.replace(anchor, payload + anchor)
    if kind == "after":
        return text.replace(anchor, anchor + payload)
    fail(f"unknown edit kind {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--single-tree", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard "
             "requirement locally; pass --single-tree only on a CI checkout")

    # ── prerequisite + idempotency ──
    probe = os.path.join(ROOT, "backend", "app", "api_server.py")
    ptext = open(probe, encoding="utf-8").read()
    if "orb_v1_runtime" not in ptext:
        fail("apply_orb_live2.py must be applied first")
    if FENCE in ptext:
        print(f"  SKIP   bootfix already present — "
              f"nothing to do")
        return

    # ── stage every write in memory first ──
    staged = {}   # abs path -> new text
    for rel, body in PAYLOADS.items():
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                fail(f"{p} already exists (half-applied tree?)")
            staged[p] = body
    per_file = {}
    for rel, kind, anchor, payload, count in EDITS:
        per_file.setdefault(rel, []).append((kind, anchor, payload, count))
    for rel, ops in per_file.items():
        src_path = os.path.join(ROOT, rel)
        if not os.path.exists(src_path):
            fail(f"{src_path} not found")
        text = open(src_path, encoding="utf-8").read()
        if FENCE in text:
            fail(f"{rel} already carries the fence — mixed state, resolve by hand")
        for kind, anchor, payload, count in ops:
            text = stage_edit(text, kind, anchor, payload, count, rel)
        for p in both_trees(rel, a.single_tree):
            if p != src_path and not os.path.exists(p):
                fail(f"dual-tree copy missing: {p}")
            staged[p] = text

    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── staged compile gates ──
    tmp = tempfile.mkdtemp(prefix="orv_gate_")
    jsx_targets = []
    for p, body in staged.items():
        t = os.path.join(tmp, os.path.basename(p))
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        elif p.endswith((".jsx", ".js")):
            jsx_targets.append((p, t))
    print(f"  OK     py_compile gate passed")
    esb = shutil.which("esbuild")
    npx = shutil.which("npx")
    for p, t in jsx_targets:
        cmd = None
        if esb:
            cmd = [esb, "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        elif npx:
            cmd = [npx, "--yes", "esbuild", "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        if cmd is None:
            print(f"  WARN   esbuild unavailable — JSX gate skipped for {p}")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "frontend"))
        if r.returncode != 0:
            fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
    if jsx_targets and (esb or npx):
        print(f"  OK     esbuild JSX gate passed ({len(jsx_targets)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  write {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write, with backups for edited files ──
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    # ── grep-count verification ──
    bad = 0
    for rel, needle, mn in VERIFY:
        got = open(os.path.join(ROOT, rel), encoding="utf-8").read().count(needle)
        ok = (got == 0) if mn == 0 else (got >= mn)
        want = "must be ABSENT" if mn == 0 else f"need >= {mn}"
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} ({want})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   boot NameError + Settings gate fixed. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD FULL rebuild (PyInstaller + Tauri) then restart the app")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
