#!/usr/bin/env python3
# =====================================================================
#  apply_hero_split.py  —  fence HERO_SPLIT_20261120   (on CLOSED_METER_20261120)
#
#  Under "MTM today" the Dashboard said "Paper is ₹7,248 below today's high.
#  Closed trades lost ₹30,062; open positions are up ₹1,38,353." That is now
#  one line of labelled figures (design F, 17 px; 15 px on a phone):
#      CLOSED ▼ ₹30,062  │  OPEN ▲ ₹1,38,353  │  FROM HIGH ▼ ₹7,248
#  Arrows and figures in the profit / loss colour, "from high" in grey; a part
#  shows only when it has a value; at the high it reads "At today's high".
#  Hovering a part gives the old words. With Live and Paper both traded, the
#  "never added together" note stays under it. This week / this month keep
#  their sentence.
#
#   frontend/src/components/calm/CalmHero.jsx  HeroSplit
#   frontend/uitest/test_ui_calm.py            TC-CALM-0004 updated, TC-CALM-0045 NEW
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_hero_split.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "HERO_SPLIT_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmHero.jsx", "orig": "4a0f22eca52a5ea99b060f6f56ad4f5301f03005f3d4ffdd9acf1153f7641517", "new": "3307eb9b4fa9dc38ad82bf4a73c71348f2d1458ae5c2ed548a3013d54afc674f", "blob": "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"}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "18d868aeb54811414d40812df7255338ad5c5aaeeca1d725f8ede3116f794ef5", "new": "ace97a1cee525636cde549a820efe45fc08b4e42f93e39b2f33ff7e3aad59a21", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = []


def sha(b):
    return hashlib.sha256(b).hexdigest()


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

# ── prerequisite: CLOSED_METER_20261120 (test_ui_calm.py this extends) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "uitest", "test_ui_calm.py")
    if not os.path.exists(_pf) or "CLOSED_METER_20261120" not in open(_pf, encoding="utf-8").read():
        die("frontend/uitest/test_ui_calm.py lacks CLOSED_METER_20261120 — run apply_closed_meter.py / pull the latest main first")


def targets():
    """(item, absolute path, is_build_copy)"""
    out = []
    for it in ITEMS:
        out.append((it, os.path.join(repo, it["path"]), False))
        for a, b in BUILD_COPIES.items():
            if it["path"].startswith(a):
                p = os.path.join(repo, b + it["path"][len(a):])
                if os.path.isdir(os.path.dirname(p)):
                    out.append((it, p, True))
    return out


# ── rollback mode ────────────────────────────────────────────────────
if "--rollback" in args:
    later = []
    for it, p, build in targets():
        if build or not os.path.exists(p) or not os.path.exists(p + ".bak-" + FENCE):
            continue
        with open(p, "rb") as f:
            if sha(f.read()) != it["new"]:
                later.append(os.path.relpath(p, repo))
    if later:
        die("cannot roll back — changed again after this script (by a later fix or a local edit): "
            + ", ".join(later) + ". Roll the later fixes back first, newest first. Nothing was changed.")
    n = 0
    for it, p, _ in targets():
        bak = p + ".bak-" + FENCE
        if os.path.exists(bak):
            shutil.copy2(bak, p)
            os.remove(bak)
            n += 1
        elif it["orig"] is None and os.path.exists(p):
            with open(p, "rb") as f:
                if sha(f.read()) == it["new"]:
                    os.remove(p)
                    n += 1
    say(f"rolled back {n} file(s)")
    sys.exit(0)

# ── 1. state of every target ─────────────────────────────────────────
plan, done, skipped, later = [], 0, [], []
_applied = False                         # this script's marker is in the tree → it was applied
for it, p, build in targets():
    if not build and os.path.exists(p):
        with open(p, "rb") as f:
            if FENCE.encode() in f.read():
                _applied = True
for it, p, build in targets():
    cur = open(p, "rb").read() if os.path.exists(p) else None
    cs = sha(cur) if cur is not None else None
    rel = os.path.relpath(p, repo)
    if cs == it["new"]:
        done += 1
        continue
    if cs == it["orig"]:
        plan.append((it, p, cur, build))
        continue
    if build:
        skipped.append(rel)              # a stale build copy — the build re-syncs it
        continue
    if cur is None:
        die(f"{rel} is missing")
    if _applied:
        later.append(rel)                # applied, then changed by a later fix
        continue
    die(f"{rel} differs from GitHub main and from the patched version — local edits? "
        f"(commit/stash them or pull main, then re-run)")
if not plan:
    say(f"✓ already applied ({done} file(s) carry {FENCE}"
        + (f"; {len(later)} changed again by later fixes" if later else "") + ") — nothing to do")
    sys.exit(0)
if later:
    die("partly applied: " + ", ".join(later) + " changed after this script while other files are "
        "not patched — check the tree (git status) before going on. Nothing was changed.")

# ── 2. git status: an uncommitted change on a target is a stop sign ──
if "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for _, p, _, b in plan if not b]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        # a planned file was just proven byte-identical to its expected base (sha == orig): only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="hero_split_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, it["path"].replace("/", "_"))
        open(f, "wb").write(b)
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
say(f"payloads verified and compiled ({len(ITEMS)} files)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []


def restore():
    for p, old in reversed(written):
        try:
            if old is None:
                os.remove(p)
            else:
                open(p, "wb").write(old)
            bak = p + ".bak-" + FENCE
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        if cur is not None:
            open(p + ".bak-" + FENCE, "wb").write(cur)
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")

# ── 5. tests ─────────────────────────────────────────────────────────
backend = os.path.join(repo, "backend")
py = next((c for c in (os.path.join(backend, "venv", "bin", "python"), os.path.join(backend, ".venv", "bin", "python"),
                       os.path.join(repo, "venv", "bin", "python"), os.path.join(repo, ".venv", "bin", "python"))
           if os.path.exists(c)), sys.executable)
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    say(f"running tests with {py}")
    env = dict(os.environ, PYTHONPATH=backend, SCALP_TEST_MODE="1")
    failed, envmiss = [], []
    for t in TESTS:
        if not os.path.exists(os.path.join(backend, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.realpath(sys.executable) != os.path.realpath(py)):
            try:
                r = subprocess.run([sys.executable, t], cwd=backend, env=env, capture_output=True, text=True, timeout=900)
                outp = (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired:
                r, outp = None, "timeout"
        last = (outp.strip().splitlines() or [""])[-1]
        if r is not None and r.returncode == 0:
            say(f"  ✓ {t}  {last}")
        elif "ModuleNotFoundError" in outp and "No module named 'app" not in outp:
            envmiss.append(t)
            say(f"  ? {t}  could not run here: {last}")
        else:
            failed.append(t)
            say(f"  ✗ {t}\n" + "\n".join("      " + l for l in outp.strip().splitlines()[-25:]))
    if failed:
        restore()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: some suites need the backend's Python packages; re-run them with that Python")

# ── 5b. JSX parse gate (esbuild from frontend/node_modules, if installed) ──
_esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if os.path.exists(_esb) and "--skip-tests" not in args:
    bad = []
    for it in ITEMS:
        if it["path"].startswith("frontend/src/") and it["path"].endswith((".jsx", ".js")):
            r = subprocess.run([_esb, os.path.join(repo, it["path"]), "--loader:.js=jsx", "--loader:.jsx=jsx",
                                "--log-level=error"], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(it["path"] + ": " + (r.stderr or "").strip()[:300])
    if bad:
        restore()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Next: release with ./deploy-scalp.command (all three platforms).")
say("  UI check, APP CLOSED: cd frontend && CI=false npm run build && PYTHONPATH=../backend python3 uitest/test_ui_calm.py")
say("  Undo: python3 apply_hero_split.py --rollback")
