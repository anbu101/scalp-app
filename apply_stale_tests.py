#!/usr/bin/env python3
# =====================================================================
#  apply_stale_tests.py  —  fence STALE_TESTS_20261126   (on main bbe17ac8, v12.0.20)
#  DEF-049 · DEF-050 / DEF-082 · DEF-051 / DEF-066 · DEF-087 · TD-101
#
#  Four suites were red on main for test reasons only (the app is right):
#   * test_expiry_card.py — "month strip keys are Tuesdays" failed on the real
#     clock: a Tuesday holiday moves the expiry to Monday (19 Oct, 9 Nov,
#     23 Nov). Keys are now checked against the app's expiry calendar.
#   * test_ic_split_invariants.py — Icarus IC_V2 shorts sell at premium_max
#     100, Indica IC_V1 at 85: intended (Anbu, 26 Nov). Templates must match
#     apart from that; IC_V2's 100 is locked.
#   * test_paper_trade_squareoff.py — the overnight-exempt set is IC_V2,
#     TSG_V1, TMA_V2, VET_V1, TMA_V1: intended (Anbu, 26 Nov); now locked.
#   * test_margin_log.py — its data is dated 19 Oct and the route counts 30
#     days back from the real clock: the route check now runs on that day.
#
#   backend/app/tests/test_expiry_card.py
#   backend/app/config/test_ic_split_invariants.py
#   backend/app/db/test_paper_trade_squareoff.py
#   backend/app/api/test_margin_log.py
#  Build copies under desktop/src-tauri/ are written too when present.
#  Every edited file gets a .bak-STALE_TESTS_20261126 copy.
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_stale_tests.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "STALE_TESTS_20261126"
ITEMS = json.loads(r'''[{"path": "backend/app/tests/test_expiry_card.py", "orig": "cbeb6a97a5dd21707ac7a7e02aaaca0d68a731a740680f77518d1be9f479fb0f", "new": "7acb1e6a7d9799f06625a8bdc9334647c2227095828366316fd573bb572dc2d7", "blob": "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"}, {"path": "backend/app/config/test_ic_split_invariants.py", "orig": "125f09f93ea9d82b4058fd756d244780bf3f4d8352ddc8c0f2152684dd190796", "new": "eb74ca7b57098299817329e6766ddb5d2bc8434e659e94d02b1cafbd97c529fd", "blob": "eNqtWf9u20YS/l9PsccADRnItKX8aCIgOLiJkhpVbSNWigaqsaDIlbQ1RTLcpRw1MNCH6BP2Se6b3aVEUbIvV5xh2BR3d3Z25tuZb0aP2DSKb0SWHEdFcRzn2UzOj7VQmsuYqyKVeMhWUSmjTKuwWHcedR6xv//6E7/s7A2/uhydjZnfP+m/ODp5eXTyLKgHr8YfPr4Zf/xwOmLvP55+eHvFZnnJ9ELQsl96x/S3z8wOIUSOF0IJFpWCxQsRFV2WiAJaiSxeH81KgSGlRKllnimWr4SVlIg4jcpIy5VgqipnUSwUZPmJmEVVqlWXlWIulS7XXXa7kFqk+NBlNzJNmX0UX8SyIKlMV0UqgpAUWeM1RiFpKuKogl60mVH1sWKzSKYV9FzmiVBG4wjirs5Gw/PxgEWZPR+WRJp9rqTQKYzGZLYQpdSKxVFZrpkSSxhUxlARVnGL+iwp86IQCZuV+dJsalRVt1LHC5gkN8fLcs3KSEKtv//8i0nNfq+UZrqMSJ/bMs/mIftQZQyrFgPjL/qJkwOeZt99x4q1XuTZU3a0pEdBou53Pzv6bCT671IcD0N5HJH5umwZQUeZzY3ai5yshj1WIjPWJd8rXU2nOJyq4AkVBp2OXBZ5qeFaXT+Won5Sa1U/6nWBgxubFJFepHLK3MglPm7EWO07ndPLS/baDPmcz2QqOA/CUqg8XQk/CAu4DIeZ9K47nS2WSTmjOowDWwJwKsdLgEvM1zzNYdzS7aqMZfPK7PjH8Q1O46Qc/O0AjYwvb/wsGHTIE0toZ44U/pwnVSrGeMagGcOpw6V5qybZNSYuzetS6KrM8KHTIUvyDKZnEw+6el1G/0JBlubTStUvrIO9a7unnNEiQg4WNjaxo/RDGnJo0dSgJfk65JwcwDkUm1x3aMnulDCqEkAmzedzUXpBeAvIC755iWVptJwmEXsSddmTJzcDdp5nomM2JcmhzOiW+yddsr0PTzp3BUCLNSS5glsFeSJj7ZcipaUDWtFlWbQU5tEZ2/O8y6ik0MIc5O1a3LmEiVWUsovz0SdGN1PnxVGKo1Bs0KLEEMlXdMuMqCzfAABhI660SLqEEr2QgERe3igybpQhfGQriXu4hOIhFLCeLWMcn47EjpnTmWCJ02jxRfvW/wZ5r+lG0MGV8LHMjhDeXrOvd+YDgSBD/KENaUk4zZP11pfwtlQwpY6yGNDCxK4ReaqUnGeBOXsqMjMS6qicC60C9vo167HfNkLqH5rcklavmZxcW8HnsHpwz9rWglAmtJPx0858e0CS5szPyT12PzxUImheBcwGIt4O351+HI2vsHAfGJ7LZq1bjCjmdfc03fnxnFyOJHY6Hr7/xN9cnL87e3/lBZ3RxfiKX56Of3x40zTXim9yjtmTedu1EPRh+P4M8j8dFlMrvdW+TmVOllPtbEiyGoHs7Ko3YNNcL1jtMmXTGdAuyvUt0pDJZ2u2pLTxQOD6n37N5bSJQ/U47U8ZRCaKFwi9FB5kxo0G3GVq391QkxoACkDZ90zupOOZfOgFW4jY/F/PrB3fJQHeV7y9Y0sJeCMBmURxrwfvEbh1TfewwIbv7hFRO/RenbYu2/ASb9d1/YFjD4golIxGw/enbz5R5EpgJUr39Fbf5oy4k6EFjoNcvHvHvtE9ffLMqocnnop5FK+5yBOe5dxQE3qIEqIUtYNWPUC0NvjEuei60zDAqjfxgDFNEBbeNd1vb3jxFn7chgS7zoJOfa5I53w2YwnY1Nqc7Hz465hfXA7PEUurNHFEibheJucL7bU3tEryPOMqxZ6w2LsoVeLeaem6PYuoFLPjFKrZNM3jGwZeA0I3oGRp+AzhMzJUYM0oXCNPsEJiYlU093IbeXWSXfXcHuYcR7Q/uxFrZS0wRQKa0rVgPjwqS2ZvSSwoxaRS1DxRZpqy385GGdIFz8GOuZZL0d6wniW+FBLeNG5pz9vB3NOBI583QhQWdlDmyJA/5hNnJNwcIQTLBJEoCbbk9VsR99Qgrs/NDg5mSA18a3u1AVt/D2z9Ftj6+2DbYMdrz9xHybisRHtWy6RW6MmrQe/FjkAltL8VCj5EDvUDkzq/eiMTuEZ9727XwM9cPFYLWdh7CrSjvEnzdc2XrbEdha+BN10TLRHpjD1g2mc21pJsjvv0z4Lqxt5Ycj3xjB7cchyQ96ahoX4rZD0fMJkQy49BmBBP2Hj48+UIcc4g250MZm9UPARE3KcqRiYXATt4rucUm8CMcBsAOsU3e9QnfLQtNE9HQz4eXiE+Ux3a6/Vf1GM+Toai9FXAzoC6SjHfmIAgnKbKsj4EU7UwrA43H8hfymrJl9EX1js5oaoRXPEsQ1KO3LZGRC9w1+Xlc1uF0T1NUNz4p9m06jKocJ6vUE4OKeVZfwrEHUPYKITXB3MBUVOdZ0qo0MXc7qGrgLg78WAYcPHu/i2ph5qQnaQTTybwHiEitfffuHKyxSv9fWr+PnOL4R1gdcPWSeyATb6Cr6+MoBtoR7LSEARniStAlPOG/QsIaVjQu9vuSiKuXcz9HA3Y8PunO/HK7OiveuYuuQ/9ADoRpFrWMnZCoRZhna0KG14jb5hgSzHi2N7URsRK5GyG0sSe0oAAV9fZaMDw0NS/abU+HRHjUUx3wl2Hq+Fo5N3thggj08WDAYHIGtk83hG5cJ0Pwpwxi1lzAHwD9tVIu7Pa3gJDpO3Ej1padtm09SawWQsDpPsfxrIEKOOnqHWIHz5+2sUM0p//hYbWRsyXLqNEZBUI6AT0hAQF+G+vPibssk6ob1bctQLGi5rjEEXetmWsa88vxnBckVPBZlwXPjE1v2L/jIq+4LtEnKsYYT7hhSj5pqRxEcWdnqITiji/CDGMsEDFvu9CfuhZyxa7lHFzOYMDjGcRqRbPME4nnG6OaExhz2nJz5SaW+AapoBWO0noH+mG6NCqEr4fNJtLoJgIVIr9cDH+sVE4/L+qg//qp+85KcOtMq7UgT4mudX+2amgvU16Om6spNpov6i+SbGuFKESURkv/NL76Ww04g02/pr9NvHDJ/8OfrtGGMQ+1DgMr4JwXuZV4fd2+Ndj5+zHZGeIphr3sTOye+cyBUB2VNsSvo4KOpJNf3mG1LgACQbPTXNURDuU5LG1hozr3B0YwWQAs9vucH87vOvilwOTa3rPB/3nQBfUgbeRtVzXU9nWB00xlIwu9Ubdb/TbS74h6HzTS8VTnFYJZW0qNA77L5keG5W4ZRu2JACDOexCXRUtH178Mvxwfvb+xzEf/grGMW7507f+DL7Vn8532Afh+rBJXKaO1s5+3mFUOJptJO0FA3tM1Sp9nOVoM+0CANUbcDVxUue6Mr9VrVD6iqoU19Byrau6XeAis14j2sx0TTsc5EQ2l9khHzdd+4rbaRwBjIpCLE1iMMGE16KbZHNmqSYcbmyMNJRFc9dsYfTaCtt+nmti8JnU+cGODE3Jy2IRZYgGqH9j8LDtYltCKKw1LxuMthUkzKbHMvbwabYPq4dbbt/QafuGbttgr9dkvgghgc3W2OBgS2pXtm522wge2nXTvE2b4+ytN7i3uWU75CF9g+HPvK+zO0c84XWYCQR4F0vew12y+jTNzS0ZrqFXl7rNQLjXrTqhdi0we0StepcEqTtQI/nIpmwGy9b4TYkAHhsQBOzeLtQJN3JNy1pxLDRZvxZbg3e+PISX40NA3sdPrO5b3Ebo3tI6blCnqG7xyeSOJ9Gam/OFvytQNBOV5ssHl5i9GtNj9eB0JQDMPNtf8MjEiJyij0tNuXGa3jYt5tSurzE8jWyB6WLfnurdOiruaLjfe61n7Sq2X6bSfi66wmuE/8YbnKHToa85OLWW6SsKXAsO98mMc3cn6GsGKgt8dxFo0J/UXxIhtBytwJOCzn8A0yzdrA=="}, {"path": "backend/app/db/test_paper_trade_squareoff.py", "orig": "c766d743d7e33fcb555c0996b8335c9d6ab7eb628a0650eb7b18402916ef4171", "new": "fb9e38b3b1eaa162cfeef38172c0566d7c3d543b5f4e3dc7f643488bc3d51ede", "blob": "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"}, {"path": "backend/app/api/test_margin_log.py", "orig": "59b4f75b0132be46dd4c2b02b4e942cd9484ea2399457498c8d7f25f69a14578", "new": "2b516785032b068d439f5dac9fc6da8e42ad9013137251ab996a5493c1c3259a", "blob": "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"}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["backend", "app/tests/test_expiry_card.py"], ["backend", "app/api/test_margin_log.py"], ["pytest", "backend/app/config/test_ic_split_invariants.py"], ["pytest", "backend/app/db/test_paper_trade_squareoff.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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

# ── parent fences: the code this patches ─────────────────────────────
PARENTS = {"backend/app/tests/test_expiry_card.py": ["EXPIRY_CARD_20260929"], "backend/app/config/test_ic_split_invariants.py": ["IC_SPLIT"], "backend/app/api/test_margin_log.py": ["MARGIN_LOG_FLEET_SEL_20261016"]}
if "--rollback" not in args:
    for _rel, _fs in PARENTS.items():
        _pf = os.path.join(repo, _rel)
        _txt = open(_pf, encoding="utf-8").read() if os.path.exists(_pf) else ""
        _miss = [f for f in _fs if f not in _txt]
        if _miss:
            die(f"{_rel} lacks {', '.join(_miss)} — pull the latest main first")


def refresh_master():
    """MASTER_SUITE.md and REPO_MAP.md follow the suites on disk (a new uitest file)."""
    for gen, a_, name in (("scripts/regression/master_suite.py", ["refresh"], "MASTER_SUITE.md"),
                          ("scripts/gen_repo_map.py", [], "REPO_MAP.md")):
        g = os.path.join(repo, gen)
        if not os.path.exists(g):
            continue
        r = subprocess.run([sys.executable, g] + a_, cwd=repo, capture_output=True, text=True, timeout=300)
        msg = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        say("  " + (msg[-1] if msg else f"{name} refreshed") if r.returncode == 0
            else f"  !! {name} refresh failed — pre-commit will refresh it")


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
    refresh_master()
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
tmp = tempfile.mkdtemp(prefix="stale_tests_")
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
        _mode = os.stat(p).st_mode if cur is not None else None
        if cur is not None:
            shutil.copy2(p, p + ".bak-" + FENCE)            # keeps the mode for --rollback
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        if _mode is not None:
            os.chmod(t, _mode)
        os.replace(t, p)
        written.append((p, cur))
        say(f"  wrote {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")
for s_ in skipped:
    say(f"  skipped stale build copy {s_} (the next build re-syncs it)")
refresh_master()

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
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        if where == "pytest":                    # run with python -m pytest from the suite's folder
            cwd, t = os.path.join(repo, os.path.dirname(t)), os.path.basename(t)
        _cmd = (lambda _p: [_p, "-m", "pytest", "-q", t]) if where == "pytest" else (lambda _p: [_p, t])
        if not os.path.exists(os.path.join(cwd, t)):
            continue
        try:
            r = subprocess.run(_cmd(py), cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and ("ModuleNotFoundError" in outp or "No module named pytest" in outp)
                and "No module named 'app" not in outp and os.path.abspath(sys.executable) != os.path.abspath(py)):
            try:
                r = subprocess.run(_cmd(sys.executable), cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
                outp = (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired:
                r, outp = None, "timeout"
        last = (outp.strip().splitlines() or [""])[-1]
        if r is not None and r.returncode == 0:
            say(f"  ✓ {t}  {last}")
        elif ("ModuleNotFoundError" in outp or "No module named pytest" in outp) and "No module named 'app" not in outp:
            envmiss.append(t)
            say(f"  ? {t}  could not run here: {last}")
        else:
            failed.append(t)
            say(f"  ✗ {t}\n" + "\n".join("      " + l for l in outp.strip().splitlines()[-25:]))
    if failed:
        restore()
        refresh_master()
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
        refresh_master()
        die("JSX does not parse — every file restored:\n  " + "\n  ".join(bad))
    say("  ✓ esbuild parsed the changed frontend files")
else:
    say("  (esbuild not installed — the build will compile the frontend)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)).")
say("  Test-only: commit; nothing here ships in the app.")
say("  Undo: python3 apply_stale_tests.py --rollback")
