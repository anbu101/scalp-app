#!/usr/bin/env python3
# =====================================================================
#  apply_recent_above_chart.py  —  fence RECENT_ABOVE_CHART_20261118  (DEF-058)
#  REQUIRES: the Dashboard build that has CHART_BELOW_PANEL_20261117. Frontend only.
#
#  Dashboard, focused strategy: the "Closed positions, today and recent"
#  drawer sat BELOW the Live chart for Tigris (and every strategy whose panel
#  has no closed table of its own), while Icarus / Timberwolf / Velvet show it
#  inside their panel, above the chart. Now it is panel -> Closed positions ->
#  Live chart for all of them.
#
#  Files: components/StrategyHost.jsx, uitest/livechart_scene.py,
#         uitest/test_ui_chart_card.py (TC-CHRT-0009).
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_recent_above_chart.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "RECENT_ABOVE_CHART_20261118"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/StrategyHost.jsx", "orig": "e0fd67be007a2a553c9ae0381882a2fb039aa62a6546428378b9bd9ee797019c", "new": "7b88b07190626056bba5f015e9276e6a79d66af4486c3dff9c7158a6ab26fcea", "blob": "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"}, {"path": "frontend/uitest/livechart_scene.py", "orig": "6f8ae45e1a2e0359db92083db5611a975beff6c97b75f1593c65038341c44547", "new": "071cdf2bf89b01dcaba835032f84c720ad0d9e59a31eb26cb4d7acc8a1141627", "blob": "eNqtWetu20YW/s+nGHBRmExp6mI7ySpVA1miYyG2ZUhys4FgEBQ5kljzFs7Ismp4sQ+xT7hPst8ZkpJ8SZumTYNoOHPmzLnfquu6FoW33F94uXSFzxNuZ2v2v//8lwls8YCpPTZLcyYXnJ0BlilgHOScJ2KRSsG8JGCSCylaLOdJwHFRm67VjaHTOcNmFnlrlqdLyVmaMI8FXPI8DpNQyNBnYp0AllZ+mmdLwYzihqsOWuyifzL+rDVi9iOuHkeef7P/5jVbcX4TrYmcMDEtEMEGl84FG4fxlOerNJoxY3zecX9pmizzMp4zkeXcCxhPpKKwechO+JQ1680j1qi3DuoW05N0pbM2Gy85w+72+KBVP2L90dhiJC6W5aHPBZtBQLM8jYlTrSDdZiMv5jti47c8X7Mpx5qzGvNmeLyUkq1pDH8uP49PBxeXnfFp27ZrU3CHU5at5SJNDtgyJMnWSNDuRlWkpMHV2L0cOif9f7HJ/j4BXWvap5zAd872fS8P7CyZW2x/toyicpkBNy/XMZdgR9i/ijSxtQIV8yKRMk8InkO/pMhC654vl14EqQc5XzFjKSCBUtOCKDVtTYdNhXGWApowVutUVCuxWMow2nx9iXDvYPO53sDJMOaaEm/gSU5frDyhb2uzaynIgEfSK5a/gTdNAyY78+TCDhPiwoB603InCPMEWjKqb28q6Ndw3VkYcdc1TbMiYhm6uA0dQhjsiv3pP/9gSfrFazHnsN5kzBAc4jwdnDsWy5eJcIOpSy+bBZ+0jMJpxeYlPv/Cc5oGg4UxVyIxNmIyFukyF+0ji8EF4ZOifVAHy1K46QwXIi+eBvBRiy0WAIktJkS73mJhIo1K6EZgr7mXQwt2nMJJaRF4690rUMZvYTJL2yDDtOmSkF6cGaa5S+ibg4bWax5arAc/ayulGuRzFmvi76FpPd06MrWLwSfii8g1erTfOLAYfnBU6c3LMptciYzZRjRJN18ufZE2j4ffKsiXcIIqz/YR+WAwAuL0OeHsjv4KziLqlT+uTxEDOIfdvwEnhQ63jMPAefbNvCvD/B0qVYiuLHa6DKPgT+AMpnbh/xWCOZfgO0m+w9h7x7CJyqN/TcPEuLLH55cI6ludTXVTOx7aldttbL2lfM3oHZtad2S7G1P5Hbhh19714B2Q3rHW7XRPHWzpNRlntW2GLXME6NDCGaiXG4L5HVKhMNRFs6XyghJmsfPICRr4+9wt3sKBi8hq+2m2ru4RqZoPUso4a5N0uS8VD76dpyt3hqCeIkdtYYbpCmf8jvsIDobec86cscNOhoNzthFNYfrCRVb+dOoMHbjjz+33usWMp35pWsrjC33JRZjMyyyosi0e8tM4DqVhavwuAxU7L4/wcnfMsB+CwD8gAHFe5ssY6d2V64y397rOHutc9ArKytVP79lZ/7w/Zg3d0p4a1IZ0hKN/gnqQzp5svUZusGdc+pRADXNSv9ZEBj2+RLYfpYL/aapHl4NxRfdjgYKEBrIYYrX1jAhPxqCBIjSqrCQwFFE1dlQ32Sv6V1sjNsc+zCYo4SDUSbN1eA0eQYFRbR213lyb2KP121YDqEfblHADebTYTFcF2f16/XAf+w/3QfBwf/NwLx/0b/JWiviQ0JnFPvUvPgD5yCDqfwRzVIJ1HR3PV3uHmz1tPPgI4Psc7KLOnDSuVX2Vg5kXZN9D0ulfYCFzL4DJiXU8TSPrkbhTFFpfVY9uPmjZ3ZZ1oVj/TiU/ImJri1gNhj1nyI4/45P1nFF3a50oFvDkU0U/FaTWubwEkVXchBORUKjAgVpJNIai9Az46AeSNfTjq896IXyzDDUQB5Bkd+rJZ9ZmKhg8tGW+fzFyhmPWvxgPmIy9pkscovA05jC/zA1RO8RpQEVamCPahCnIeaIJJX7rqf0rYwxvuFU6vcW+SPyjP/NVHTfytStBcbFSNTmuE+qM0HhyKUz2S+fsyhkx47311f/Ml0KBPv5AStAvO5fOULcqkVoMZjgR11hO9pst/NYVqRZ7fVR/SXiKvOf4WemnJPlXrGG/pqDOkBToHdZG6lBaYzyCdV1A+6S5Rt2u/wGMTj2QDpWRujaBVetdnZOZ3KNDu9eV5PUWkwBfUiMSraEXbBR+TVw/0QrOKnb1Qj/YmUWpJw0xiXhivBASdLO130R00R5rTWkVt7F4UA5MJkeGCrnayD6xMJQchA0NovlYhUi4JWGm6jQLSpRtU2AgHTnkrWeU2Jdx5k6pKol3MzK4V8c3VGps90lmah+JCJa0ezL4pA7Gw073ozN0x6doZHsAOEFjhOZCC/iMuZHMjDCAkMyW8svd3rCtmqLIQy9VNrVFpDBoG3zHlAenHvgP8TKa4lCa71i6QjArMjR8hlHX6qPfUjKEkbeeB1WFr+isqR4hsXhsleY3nFp2anhb2CirrB3U8RKUUQXi56kQJagwN9oiub9UNFD16PutR1qFunRKWzq9rpKJEoq9zMCAYbaeWf8t5cu/N2F+NZA+zZS7dJDn/L3UfU+k1/6gzi3EaZFRml/jJudyiaBgFE55i0hQrNSdIsbf+TyTzFE/sIDW07sbbyBLINuGGOhHq5pwsUB/fAfYTbl+cuY44+9rTourbTUjsEFqIAy1JA8Whjo1i2SmMrzamFDwQdk7D7nQrwsG8olOBbR+DVyTCRrP26IuoBVd3JxjOnJVxBSjarfRv6zycL6QNroY3/WykG3GEPjenn8LR9bJYR35uHsKA3GKVoQnt2EOnpCdEarUAWIYiEv4nTSMTBGaFZGsthR5bRomtXmaziO+7y9AIn9kHHqtk2VR6HukPlH7oABZVwHaaKdqXXTisBRRO/f8wegxgK6i6pN2I6NKT2UO7bgzUh3LQsqsVas1mm9sZBu70Tp8c9Bs1HSUbKSuK3oIIxEpycpJZDYOUGp9GLkn/TOHwPAEBlzSjm+QqIziQ7TH+VJldTzspjfq01TQK5pZuZJEsmMB9zoZ3DTF+MqFHhDgKEH5CKgi9PUH2MaVfdbvOihEEGdUM4RZTYRxW9TWvQDhVVe1RGku7Yn+Sr82y8ideXNuTGEhyNuz1F+Ktl6MCvUykg8dYB67nePBL47bPe0Mxy5ardeNRuNtq7gBHLkXK+34kurEqZ3wFRVhipPbkK/Iktr0RDWBQV3U1jsi9Gof0+gGAwS9cM1sThFH3ikMirZq33Z5nqc5pZPJdbWXJoZOUOoIXJYVKm/tXCA1ceoDEI25OcF0s35tbrAilPk3NrRIDs0DV010dGom9+vN/ebRWA088fdH/HtQ35Bpe0HgYmpLs+I8zGDVQORFI/SPoAdoMRIyzHePNvFKH2nd2BPYzOxSJevTFA25kmQ/2LP2fhB75jud/VAId/OeSmyG/upVDZZWm0Wcy5pMMWjasp2jGbAx2sToLlIG1K6CxzMcGAmCEvTjmM4K/nsoghDZbmNgVTXZ1lFN9nu6uUU+T2VqkO9YbOVBLssEHXhbD9LYL5yRQhsPSgmWkRaqtnC5NMYg91aJkc3LPFnCADe/9aIlGbZuoCZq/8zuKYEjbec0KIOcKAnaX5YYMI94xKmLN/YmNJhSM1wYGxUk+2r0oObA15BxhT9n78H0o8sdcL8nbueMSvY900ZhN0cR0GL1d+zBNEy9ch/FqUILsgvzhozbr+slCxgB94gplf0gci9Yq1B3gJbE6Dkn+/WDt5aqj+AU++lsxqaRl9ywfzcBUMwGVJUU5mCWxIM2AdN2MtViMrJmx4PB2L0YuEPnfHB1sfHON++KWXTxJtFJqlsLqlo8zLC5l7H5EqIwbZpTq7KqblXwxaxUjSqNMjAqkNUCY+HdM7aPa+ynivNtKg131Ml+bqPzflxebB4qF/T/VbZonxZVj18srwDrwfOapdQqRVXt69XN5vkNa0oC9IyAb2VG3ab56g7Cot79P9xyz4k="}, {"path": "frontend/uitest/test_ui_chart_card.py", "orig": "13560861c8cc7e73c57a9622e053d07abf4fbf09c85384ea6a67bf8879aaab69", "new": "c0885c587c5e5d980c20237be1ed70a0de407fc9459a350b03585622a80beb8b", "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = []   # frontend only: the JSX parse gate runs; UI suite optional (header)


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

# ── prerequisite: CHART_BELOW_PANEL (already in 11.9.x)
if "--rollback" not in args:
    _k = os.path.join(repo, "frontend", "src", "components", "StrategyHost.jsx")
    if "CHART_BELOW_PANEL_20261117" not in (open(_k, encoding="utf-8").read() if os.path.exists(_k) else ""):
        die("this build has no CHART_BELOW_PANEL_20261117 yet — update to the current release first")


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
        # the earlier scripts' output is expected to be uncommitted still: every planned
        # file was just proven byte-identical to it (sha == orig), so only other drift stops us.
        _ok = {os.path.relpath(p, repo) for it, p, _c, b in plan if not b and it["orig"] is not None}
        dirty = [l for l in dirty if l[3:].strip() not in _ok]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="recent_chart_")
blobs = {}
for it in ITEMS:
    b = zlib.decompress(base64.b64decode(it["blob"]))
    if sha(b) != it["new"]:
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
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
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.")
say("  Undo: python3 apply_recent_above_chart.py --rollback")
