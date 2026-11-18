#!/usr/bin/env python3
# =====================================================================
#  apply_wide_screen2.py  —  fence WIDE_SCREEN2_20261118  (DEF-059)   v2
#  REQUIRES: apply_wide_screen.py (WIDE_SCREEN_20261117, in 11.9.4+). Frontend only.
#
#  Dashboard on 32-inch monitors and TVs: the page was capped at 2000 px, so a
#  32-inch screen (2560 px) kept 280 px empty on each side and a 4K TV at 1x
#  (3840 px) about 920 px. The cap is now 3200 px. Laptops and phones unchanged.
#
#  v2: edits ONE line of CalmDashboard.jsx in place (and the two lines of the UI
#  test that carry the widths), so it works on any 11.9.x tree, even when other
#  lines of the file have changed since. It no longer compares whole files.
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_wide_screen2.py
#  Options: --repo PATH  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import os, shutil, subprocess, sys

FENCE = "WIDE_SCREEN2_20261118"
JSX = "frontend/src/pages/CalmDashboard.jsx"
TST = "frontend/uitest/test_ui_calm.py"
BUILD = {"frontend/": "desktop/src-tauri/frontend/"}

# (path, required, [(old, new)])  — each old must occur exactly once
EDITS = [
    (JSX, True, [(
        '<div style={{ maxWidth: 2000, margin: "0 auto",',
        '<div style={{ maxWidth: 3200 /* WIDE_SCREEN2_20261118: was 2000; a 32-inch (2560 px) fills, a 4K TV at 1x keeps readable lines */, margin: "0 auto",')]),
    (TST, False, [
        ('    for w, cap in ((1728, 70), (2056, 70)):',
         '    # ── WIDE_SCREEN2_20261118 (DEF-059): a 32-inch monitor (2560 px) fills too; a 4K TV at 1x (3840 px) is capped at 3200 px\n'
         '    for w, cap, tc in ((1728, 70, "TC-CALM-0037"), (2056, 70, "TC-CALM-0038"), (2560, 70, "TC-CALM-0039"), (3840, 360, "TC-CALM-0040")):'),
        ('        check("TC-CALM-0037" if w == 1728 else "TC-CALM-0038", ok,',
         '        check(tc, ok,')]),
]


def say(m):
    print(m, flush=True)


def die(m):
    say(f"\n✗ ABORTED — {m}\n  Nothing was changed.")
    sys.exit(1)


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo (no backend/app) — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")

rd = lambda p: open(p, encoding="utf-8").read()
rollback = "--rollback" in args


def paths(rel):
    out = [(os.path.join(repo, rel), False)]
    for a, b in BUILD.items():
        if rel.startswith(a):
            q = os.path.join(repo, b + rel[len(a):])
            if os.path.exists(q):
                out.append((q, True))
    return out


plan, done, notes = [], 0, []     # plan: (path, old_text, new_text, is_build)
for rel, required, edits in EDITS:
    for p, build in paths(rel):
        if not os.path.exists(p):
            if required and not build:
                die(f"{rel} is missing")
            continue
        cur = rd(p)
        if not rollback and rel == JSX and not build and "WIDE_SCREEN_20261117" not in cur:
            die("run apply_wide_screen.py first — this script builds on it")
        new = cur
        state = None
        for old, nw in edits:
            a, b = (nw, old) if rollback else (old, nw)
            if a in new and new.count(a) == 1:
                new = new.replace(a, b)
                state = state or "edit"
            elif b in new and not rollback:
                state = state or "done"      # already applied
            elif b in new and rollback:
                state = state or "done"      # already rolled back
            else:
                state = "missing"
                break
        if state == "edit":
            plan.append((p, cur, new, build))
        elif state == "done":
            done += 1
        else:
            msg = f"{rel}: the expected lines were not found"
            if build:
                notes.append(f"  skipped stale build copy {os.path.relpath(p, repo)} (the next build re-syncs it)")
            elif required:
                die(msg + " — this tree differs from 11.9.x; send me the output of: grep -n maxWidth " + JSX)
            else:
                notes.append(f"  note: {msg}; the UI test was left as it is (the page change still applies)")

if not plan:
    say(f"✓ {'already rolled back' if rollback else 'already applied'} ({done} file(s)) — nothing to do")
    sys.exit(0)

if not rollback and "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for p, _, _, b in plan if not b]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo, capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

written = []


def restore():
    for p, old in reversed(written):
        try:
            open(p, "w", encoding="utf-8").write(old)
            bak = p + ".bak-" + FENCE
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for p, cur, new, build in plan:
        if not rollback:
            open(p + ".bak-" + FENCE, "w", encoding="utf-8").write(cur)
        t = p + ".tmp-" + FENCE
        open(t, "w", encoding="utf-8").write(new)
        os.replace(t, p)
        written.append((p, cur))
        say(f"  {'restored' if rollback else 'wrote'} {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")

if not rollback:
    esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.exists(esb):
        r = subprocess.run([esb, os.path.join(repo, JSX), "--loader:.js=jsx", "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
        if r.returncode != 0:
            restore()
            die("JSX does not parse — every file restored:\n  " + (r.stderr or "").strip()[:300])
        say("  ✓ esbuild parsed CalmDashboard.jsx")
    else:
        say("  (esbuild not installed — the build will compile the frontend)")
    for p, build in paths(TST):
        pass
    for p, _, _, _ in plan:
        if p.endswith(".py"):
            import py_compile
            try:
                py_compile.compile(p, doraise=True)
            except py_compile.PyCompileError as e:
                restore()
                die(f"{p} does not compile: {e}")
for n in notes:
    say(n)
say(f"\n✓ {FENCE} {'rolled back' if rollback else 'applied'} ({len(written)} file(s))."
    + ("" if rollback else " Next: ./desktop/build-scalp.sh both, then restart the app.\n  Undo: python3 apply_wide_screen2.py --rollback"))
