#!/usr/bin/env python3
# apply_vet_v1_gates_20260829.py
#
# ── VET_V1 LIVE WIRING, PART 5 ── the checklist-audit remainder
# ============================================================================
# Formal cross-check of docs/strategy_checklist.md against the VET_V1
# integration found four real misses. Each is the exact failure class the
# checklist documents:
#
#   Part 4.1  desktop/build-scalp.sh — Gate-2 REQUIRED_MODULES and Gate-3
#             REQUIRED must know app.engine.vet.* / app.jobs.vet_live_eod /
#             app.api.vet_state_routes, or PyInstaller can silently DROP the
#             package and the frozen app boots without VET ("this exact
#             failure shipped twice before the gates existed").
#   Part 4.2  .github/workflows/build-release.yml — the same names in every
#             CI REQUIRED list (all platform blocks that carry tma2 today).
#   2.13      backtest report short-code map — "VET_V1": "VET".
#   3.6       PaperTrades SIDE_STRATEGY_IDS — VET legs carry CE/PE
#             (side = instrument_type); without both spellings the side
#             column silently blanks for VET rows.
#
# Consciously SKIPPED, with reasons (the checklist demands a decision per
# item, not silence):
#   2.4 square_off POST — VET routes are read-only like the donor TMA2's;
#       manual flatten is the kill switch's job (single flatten authority).
#   2.11 migrations/runner.py — runner globs *.sql; nothing to register.
#   2.12 db/trades_repo.py — untouched for TMA2 too; unions live in the
#       three display files (done in part 3).
#   3.3 api.js helper — TMA2Panel fetches via getApiBase directly; VETPanel
#       does the same.
#   3.10 backtest pages — landed with the backtest phase on the working tree.
#
# Idempotent, assert-anchored, staged checks where applicable.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_v1_gates_20260829.py --dry-run
#   python3 apply_vet_v1_gates_20260829.py

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

REPO = os.getcwd()
VET_MODULES = ["app.engine.vet.vet_selection_loop",
               "app.engine.vet.vet_manager",
               "app.api.vet_state_routes",
               "app.jobs.vet_live_eod"]


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:80]}")


# ── build-scalp.sh: Gate-2 (space-indented module list) + Gate-3 (quoted) ──
BS = os.path.join(REPO, "desktop", "build-scalp.sh")
BS_G2_A = """    app.engine.tma2.tma2_selection_loop
    app.engine.tma2.tma2_trade_manager
    app.jobs.tma2_live_eod"""
BS_G2_N = BS_G2_A + "\n" + "\n".join(f"    {m}" for m in VET_MODULES)
BS_G3_A = '    "app.engine.tma2.tma2_selection_loop",'
BS_G3_N = BS_G3_A + "\n" + "\n".join(f'    "{m}",' for m in VET_MODULES)


def edit_bs(t):
    if "app.engine.vet" in t:
        return t, 0
    one(t, BS_G2_A, "build-scalp:Gate-2 block")
    one(t, BS_G3_A, "build-scalp:Gate-3 entry")
    t = t.replace(BS_G2_A, BS_G2_N, 1)
    return t.replace(BS_G3_A, BS_G3_N, 1), 2


# ── CI workflow: every platform REQUIRED block that carries tma2 ──────────
CI = os.path.join(REPO, ".github", "workflows", "build-release.yml")


def edit_ci(t):
    if "app.engine.vet" in t:
        return t, 0
    needle = "app.engine.tma2.tma2_selection_loop"
    n = t.count(needle)
    if n < 1:
        die("CI workflow: tma2 anchor absent")
    out_lines = []
    hits = 0
    for line in t.split("\n"):
        out_lines.append(line)
        if line.strip() == needle:
            indent = line[:len(line) - len(line.lstrip())]
            for m in VET_MODULES:
                out_lines.append(indent + m)
            hits += 1
    if hits != n:
        die(f"CI workflow: replaced {hits} of {n} blocks")
    return "\n".join(out_lines), hits


# ── report engine short-code ─────────────────────────────────────────────
RE = os.path.join(REPO, "backend", "app", "backtest", "report",
                  "report_engine.py")
RE_A = '    "TMA_V2": "TMA2",'
RE_N = RE_A + '\n    "VET_V1": "VET",   # ── VET_V1 2026-08-29 ──'


def edit_re(t):
    if '"VET_V1"' in t:
        return t, 0
    one(t, RE_A, "report_engine:TMA2 row")
    return t.replace(RE_A, RE_N, 1), 1


# ── PaperTrades side list ────────────────────────────────────────────────
PT_REL = os.path.join("pages", "PaperTrades.jsx")
PT_A = '  "TMA_V2", "TMA V2",      // ── TMA_V2 ── same 2-leg spread shape'
PT_N = (PT_A + '\n  "VET_V1", "VET V1",      // ── VET_V1 ── legs carry side '
        'CE/PE (main + wing)')


def edit_pt(t):
    if '"VET_V1", "VET V1"' in t:
        return t, 0
    one(t, PT_A, "PaperTrades:SIDE set TMA_V2 row")
    return t.replace(PT_A, PT_N, 1), 1


def find_esbuild(canary):
    for c in ([os.path.join(REPO, "frontend", "node_modules", ".bin",
                            "esbuild")],
              [shutil.which("esbuild") or ""],
              [shutil.which("npx") or "", "--no", "esbuild"]):
        if not c[0]:
            continue
        try:
            if subprocess.run([x for x in c if x] + ["--log-level=silent",
                                                     canary],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=90).returncode == 0:
                return [x for x in c if x]
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []

    for path, fn, lbl in ((BS, edit_bs, "build-scalp.sh"),
                          (CI, edit_ci, "build-release.yml"),
                          (RE, edit_re, "report_engine.py")):
        if not os.path.isfile(path):
            notes.append(f"[{lbl}] NOT PRESENT — apply on the working tree")
            continue
        out, n = fn(open(path).read())
        if n == 0:
            notes.append(f"[{lbl}] SKIP (already wired)")
        else:
            writes[path] = out
            notes.append(f"[{lbl}] EDIT ({n} block(s))")
    # report engine also lives in the desktop backend tree
    RE2 = os.path.join(REPO, "desktop", "src-tauri", "backend", "app",
                       "backtest", "report", "report_engine.py")
    if os.path.isfile(RE2):
        out, n = edit_re(open(RE2).read())
        if n:
            writes[RE2] = out
            notes.append("[report_engine desktop-be] EDIT")
    for root, lbl in ((os.path.join(REPO, "frontend", "src"), "frontend"),
                      (os.path.join(REPO, "desktop", "src-tauri", "frontend",
                                    "src"), "desktop-fe")):
        p = os.path.join(root, PT_REL)
        if not os.path.isfile(p):
            notes.append(f"[{lbl}] PaperTrades NOT PRESENT — skipped")
            continue
        out, n = edit_pt(open(p).read())
        if n == 0:
            notes.append(f"[{lbl}] SKIP (already wired): PaperTrades side set")
        else:
            writes[p] = out
            notes.append(f"[{lbl}] EDIT: PaperTrades side set")

    print("── PLAN ─────────────────────────────────────────────────────")
    for x in notes:
        print("  " + x)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── STAGED CHECKS ────────────────────────────────────────────")
    tmp = tempfile.mkdtemp(prefix="vet_g_")
    try:
        can = os.path.join(tmp, "c.jsx")
        open(can, "w").write("const A = () => <div>{1}</div>;\n")
        es = find_esbuild(can)
        i = 0
        for dest, body in writes.items():
            i += 1
            if dest.endswith(".py"):
                st = os.path.join(tmp, f"s{i}.py")
                open(st, "w").write(body)
                try:
                    py_compile.compile(st, doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"compile FAILED for {dest}:\n{e}")
            elif dest.endswith(".jsx") and es:
                st = os.path.join(tmp, f"s{i}.jsx")
                open(st, "w").write(body)
                r = subprocess.run(es + ["--log-level=warning", st],
                                   capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=120)
                if r.returncode != 0:
                    die(f"esbuild FAILED for {dest}:\n{r.stderr[:1000]}")
            elif dest.endswith(".sh"):
                r = subprocess.run(["bash", "-n", "/dev/stdin"],
                                   input=body, capture_output=True,
                                   text=True, timeout=60)
                if r.returncode != 0:
                    die(f"bash -n FAILED for {dest}:\n{r.stderr[:800]}")
        print("  all staged targets check clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. Build gates now guard the VET modules; report short-code "
          "and side column wired.")


if __name__ == "__main__":
    main()
