#!/usr/bin/env python3
# =====================================================================
#  apply_hero_fit.py  —  fence HERO_FIT_20261120   (on WEEK_ON_DATELINE_20261120)
#
#  Dashboard hero, desktop: after the period cards went, the NIFTY / BANKNIFTY
#  tickers ended ~88 px above the chart's baseline, leaving empty space. Now:
#    - the MTM is 80 px (was 64); the CLOSED / OPEN / FROM HIGH line stays 17 px;
#    - the chart's plot is as tall as the left column (ResizeObserver), so the
#      tickers' bottom sits level with the chart's baseline (180–360 px).
#  When the columns stack (narrow window), the Ready panel is beside it, or on
#  a phone, the chart keeps its fixed height (268 / 120 px). With Live + Paper
#  both traded, the smaller Paper figure drops under the 80 px number and the
#  chart grows to match.
#
#   frontend/src/components/calm/CalmHero.jsx  80 px MTM, chart height = left column
#   frontend/uitest/test_ui_index.py           TC-IDX-0027 NEW
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_hero_fit.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "HERO_FIT_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmHero.jsx", "orig": "e43f0518dbcfd4eeb3237e1bc0e2e5951911a3682ef4c92e6c8543f0d80354ba", "new": "0fee3269e301e2e8fdbd861dc57bb3273929b0c3f5ec1c876a988d000ff65051", "blob": "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"}, {"path": "frontend/uitest/test_ui_index.py", "orig": "838c74934105c767d56b9545fe4c2c6792c41384f1d67a4ccd6c076c6a42e8f1", "new": "c60d6c179f5dedb52a6ce2b00558b84aad4c9863c075e27015e49558e270867f", "blob": "eNrNWt1y28ixvudTTJByCNgg+CPZlulwU1qZPquyLG9ZdDYpRcUCiSGJEASwwFASl8WqXJ0HSFLn3JynODfn/jyKnyRf9wAE+CfL+deuJWJ+enp6ur/+pkHDMCpKpqo/9/t+6Ml7J16Iz3/4szi/fNP9Tf/70953/Vaj9aLZbL5si8vzt73firr49vTynf7sJe5dKAZygslCTaR446aTQeQmXjUV73vvxXDiJqpiRrHyo1CcWcIPhSsS6QZikER3qUwE2mnmt5/OL3pilEShkqHniKuhDGXaFkM3mPVH/r2aJ1KYrKQIfHSJxqt287n4/J9/rDSP2o3nNppvpRi43liKkZSe5YgeBCfRXEnhq1QGoza6h1PIr7txjH9+nXfPUvuxqybYv1OpCPwMvbUy4he/EGfnnZEbpFKE8Uwkc2x67gcej/z+t73vPlySrTqOU88WEPFCTaLwSMx9WqK+Y2Uhrs+++/jhfbdTp4XrKqoPJ0k0kzcVA6fiz+IoUeL3aRTmn6M0/5Qu0koFvxxW2Q9hRmU2bIzQLZ6fhO5MmvmzO0jpr9mHIQPZ71uWlYuCSph9i3NwU/FJfPXPz0UY/ei2Rfe40arAYDPhwQXWB5at8vai2+39DTI3nCCTCRc9P+te2eL96cd33Z4tvv/Y7a8b40T2o1iGe2VWPjmpgmOalhYfB+7iLvHHE+Wki3DYh2OItanxXPQ/SuWKPljRoQOR4a0PP3LGUpmG7jAsESXCqM/TpD7ww/o4isaBrOnjNyrfnl7RXGOiVNyu15utl04D/zXbxy+PWs26UbmCLTvikwMfxuEp5YfjlHR20NE7v/yPq/7b84tuZeT6QWqLS1vIJEkx4/rGFtcN+nVTqVQ8OUJ4yuHUVENbDKPQs8UsHVttdupLDBTPOqLJT3Hih8o0DSE+/8+fhCH8EU8QkiKCW/9bYFfPxMhYquFKiCUkrQyLJ2NwGCmeoGXTDytHO0CsQAErU4iOrE8eZA5sMQqkVB32HRvA4flDmXbWJ3zne2rSaR4fw/Enkg6n02w28DAMouG0YxBu1ZrNWvNlj/Gh3Wg8w++jhmGLqZQwnYoSdyw7bymuMU3ddy6jUGYGgC0RPIkMlTObIqBM/ZB2eskco+W9j4COpvxorWfAS5TsK3mvTApdx5vP4tRcGl6Oi324EgDJaAtjFnkyCY2VpadjfZwR/YZzDJxQ3vWHhD8QdevLO/LGztLgXWM2/7XhJLxzNOgPK1sofyZ/wj76vtcxTlPfrb+Lgqmr3Ow44rFehleIYQAzb3f6cJQogc/QZ9+7z33mJh8QhaZBU3gc7Bi4s4HnCtkuzc4PNVWJKa1rGLxxY1llCYn8cQ44LOYn7XzFfHLizJPAItcxGKbZFeqZDxiURXiEdkA+tXwBPn0HYUF4Ib0+mcPkxg1vLHtA4ZWY73oeQNpX/XSY+DFiFhPMn6UyTZHBrvQECuZzJWdmVVUtSywFxLtB3jkMpJuY1muxNSktJtmi2qxixGp9Jg6nKdN4+rS0XxV57mLDTIkzmgcA8YDdq8PDrAdl5DZ7QEo2ZJ+cmZtMJSfILwnRKLxPBhIVtl0PIAGZ6iEZWESZn5wLBPjlVdcWAGk1TzvGr08vzt9g4q0bwKl1ACJxBfJWBh3D9WZ+aNDoxFVy7AMkro2nxo11yDCJJECvP316QJds2ePGsc0JGGHnSQXAoqgNI4F1PLmOW4gfRyoyCbaBSi5cZx4qH4p50YwjOFRBhBledtiJRBoLKQQp0HLgQwqGUqkZ5wicDYN4iX3PXdrAteM4XjSczwiWEETJ4koGcggHO4Xe1WsPYV4jqgEzUc6sMf+6EbojW+Omat04Mzc2Y9H5RsQO9cE3nazbMgiM73w12U5+pkUkIdbqDQAOscM5y5/PnMCdh8OJKe/lcK7cAVgGMY6OTni2cJMxnUqtFka11A29QXSPA6rkwGdrVCqh/9q2bNBRlCDT6Z3CCjvbZFaFzfpxmm+WmzpMU28MjYpwgs6LBn52hWfdZvN53svCaI9jEKxQJhrUv7C2YTnsXEOc1e/IJw1h5OIo0xq9s9r5m9/UoEOTejWLbj23j1+1nJcN8azhnDSeML7p9WEqYRR8+0XTfnXSco555IsXpZF2pSAjIyObPMCRMrleU/E2pfKhZLlPBJOfBfYiEwANKDtwEnhqLnn+yjqke8vgFAEU7LtKJf6Ao2uv3Q038d0a2FiaUgCIDoiNQgAbrELZ66nrWlvEuCnthk2QGeWLh8GbrMVDVXORoHEem3Y5cGsh89CdQgwWAsHozgNl070ElwYRYegTkUI6Gaas8No++jrS+fuEano7FhQ6egCJ5ngNZDhG4j9wIEeEZFoLUDZtMrqKSS+/tR067K8w65Y1jQ3L+akgAHDDsfR4fer1WIX1AHmP0MBnD0NBfXgIS862BVDQVqR0eiDSCysh1gYRBIH99jGzYDGzaJ5K/L6VJtqvDSANmCl/1OzpRjwVCB9bty2K7oxM3eCO2zqMEUc5RCg/fgxAaJ/EYBiQ6McjNpiNdobYIMGu5tnG/sM/pjC7wN2Xz5KU4vNfY8tm85P8EfzamJA/t/kUqVNPMRkibPEEcK/4kFJcJgWSLF2kzSXmrlEO+G82Gxb4jPj//xPVIiY2zwGX8+clhubTBspwsUa4G+Mxdr+LKFmUQvEB0Y8XCxa5T+yOvZ9TOiEVCryySyAN92FoI2k0otRBJlcuDoIxW6hIgG3C2RJJVmK44ViBje+iFZU2xBJyykZNJHEJ8xE045+fO1tF7twy2QtI2cX6bcswHESUngAWsImcDWQCtDgAu2EDxwVcNLO7wwOaFYrvqPYSqkHSNzlulgWKGi/SEQ3Sr6g9UQGLTpiiBFqHAkQ0WYijhkihbNhYUeDQYdhiuSNvRfHXxNCD6fWE/Onzf/2vwTeVx+BzEkUMzxqUo8ATWlkuhoEIJFKMySFNbWMiB1kEk/6zXBOuEzzrlC5zOUFzOFmYGWP7OZIn7Ku5BZG2NjTVlllIpE69QjqJ7nYTzmHKl1/489KNaRW3/o3azu4Fv3HSfn5UXPD//QLglWZMX0xsJczXbmfsWFobNwqDhU1Wl7NYLTbS6ONPMZ7AKR46EF1jOXpVlFhOjo//5dYdy2ibbZnL9K4t1nwr/9ANJD/jLh8FwQ+6ZnKLkRxO2TMlQKqSJsQmzP2cbR9hK++sahEb/jbjI2eBDwkfMdPEhYDsZh0k002qR2FH10Z6h9Txy46AuRmJuDHZbDtImAmg+DhzB7lLkGj8MPU9ybNjHLDO71nBnV1GLLHK6qsd5/Of/4D/xVX3onvW65+e4b7eW78oyHu1VwC/cR9EkuOUVxe4YQMQBKgBa0HwI4EN7nAIkwkTF0xmHPncoZt4OFeZWjaj4d3EV9plsSpVSQ3DpGD5RiypwpiCsKB1fYhDILWSmRuYVZ+KLnAHtQgkAi2IEgyu3rqJWasNcUvxZ26ysKqvCwGDyFtkRamziR94prJeZyRYLwfFIQOHfxbNYhAk74qEY5iWT8vp9G5uTRzQPPPHDd1lWfdNB/wRmmclASl+tbugJDqcgFb3oviMdwZQngfBa7HK182mL0lnhB0Vl9pQI/ftWCZ+5HW4HQ6N6JdyumcANXN/6I/UhoRyYFc3Lgv8M3DD6e7wgvuRzBFuR+Ux2l06pxcXupt47p7+i/Nfd3mAT0vsGGfjOLOTgV3EyjItI+PVqUQi74jlip+APHBESr6m4bnJVF9lh1T4waeA7wlWUTj8K2u/OmHPJJrUJC8oFcXgL1aBdfW7KAJTAfwRJeBshdY+yKcyOGeDjRp5UeX+R+z2q3jtPzz5fDkB5e5yrSY3W5kIqFSMOQCo26CqMfWHbvdd/8Nl/81pr3txftnN8LTVYHLVo6jUaOiGCzEjTmBuIOUPCEviYNMCTUGjkWfOTi/eI9Ecv8iuOVOo7AaBeXttcAQjwyCT4AmzsssLHijK8fSz3R4K4/09FLz7pXHcbk2iELulCCNTOmRAmZrW/izZoksuNM8Z+vILUlYWMfrWZvVnbSqdBzkhkUBonacl5U735iVCjHlii3m4FhJRucWLwiqy1lIsATuwaBXjq0jG+MS2zT6TYao3Fis7tUv6IoZmpK5Y84N1cu2dn73rfrzqf7p80/1YcgZPplMVxboK7YGXJRG/si+ofeqrUoVFoza7TmoL5gODBf+1K/mrS67UsC3wGRd9n8k8X258SEGQc4Mn5rHgyjAvRhKr3Pu6xCiohK6TuH5jCtPNZ+G/X6W3oJPbZHKHUhBLMFNuOpCd04P8bzPpq3uiJ6YMNuTf71KWj1RJo5dI9442xyUw9EzDYUrz12zgnlYuVoQ3XTeoXjhSpdSfUY7o/jDB+CLD3aERfIFsk9T1OwRugpfFga/aYq9QctgaDyiTiOQAh8hIQ/IAZ9imGVmhOzlcZGUm4Y978Nw2HclXkH7WHnPnCdVldxfn7TxWcHmvYkDiqLCZapPu1ozRh3uGrgTvLMy1VkyluvOB6bEbUilZQ52ZkgfuMKW0RCO5BWgOiqwruevVS9zpEelrG4XW9QhsNzqIW5wdqTrGX+Mg+NmfGagcN7422PN08jFYiH7DMF4nuWsDqIkByAtoZAekxkGkVDTLMpU7SM3yDIoidNU2ZuhGS/wy+3JE8Zpie7pekGfrpJk18dxcvbwnW+ubDZWTrCCdjdXX/K3BuYBs7GbOI4aIu1/+GoDkFK+TysngQcvbghYUkr5alV8fGdarqZ3BvopKmG8ux+sECD5KT2w+PB26B7de6HOkANDH2Mh3rV07a9zc3qHUlWtJFy8vcy9WisRDC4ozrZcWTk36004e/q778UP/7Xmv5NBULz/RV+r99qI314tU4C5MY/LXIWwocIo4iJib0V0XREzoO2+2KhtaG3cdHsofTmWCXKudlebyu+9SLs5ED4DCuoR8B6knJ1g/ew2I2HKVaB4dHaFNb3Hkq39K6lsnDD6HA8i8VU3RO7XZVg9iOWLA82+LGdu4CKx+6EL4tYDvjJCDr/yf5M5CWc77W5fKM2Ox0g7Y7oQO1bAJe3Cg1/rdL8MOP5IBGXFAh9kTuRUbyrDypBHfG0VHhnLc1XyJrgNwsu2S+/xx7Y12OWL2RAvHCeITGqy+oha9t5xG79WpUETzbY0Q9I0loWVgEeq4brcKJBoU1S39PbeR8btwWX17en5Rpfd1/FU1/Qqu+uFdddUWS/52XI1vItxrrercttL6pJBMX8+U9z6I5pYMsM6/AGUdrxs="}]''')
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

# ── prerequisite: WEEK_ON_DATELINE_20261120 (the hero this patches) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "src", "components", "calm", "CalmHero.jsx")
    if not os.path.exists(_pf) or "WEEK_ON_DATELINE_20261120" not in open(_pf, encoding="utf-8").read():
        die("frontend/src/components/calm/CalmHero.jsx lacks WEEK_ON_DATELINE_20261120 — run apply_week_dateline.py / pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="hero_fit_")
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
say("  UI check, APP CLOSED: cd frontend && CI=false npm run build && PYTHONPATH=../backend python3 uitest/test_ui_index.py")
say("  Undo: python3 apply_hero_fit.py --rollback")
