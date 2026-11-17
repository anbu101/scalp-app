#!/usr/bin/env python3
# =====================================================================
#  apply_book_strip.py  —  fence BOOK_STRIP_20261117 (TD-028)
#  REQUIRES: apply_dash_system.py (DASH_SYSTEM_20261117) — on 11.8.9 it is in.
#  Dashboard only. No backend, engine, order or strategy code.
#
#  1. One book today (Paper only, or Live only): the top card was the hero
#     alone over a 5-column facts row stretched across the card — half empty.
#     Now hero | Net · From peak · Charges · Week · Month | Traded today read
#     as ONE strip (187 -> 135 px tall), Traded today on the right edge.
#     Live + Paper together: unchanged (two blocks side by side).
#  2. Traded today: each strategy name has its own colour tick back (same
#     accent as its Fleet MTM line / Settings rail), desktop rows and phone
#     cards. Names only — figures, state and sparks stay as they are.
#
#  Files: fleet/FleetKpis.jsx, fleet/FleetBlotter.jsx,
#         uitest/test_ui_dash_system.py (TC-DSYS-0016..0020; audit allows the tick),
#         uitest/shot_dashboard.py (--paper-only, --width N).
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_book_strip.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
#  UI check (optional): cd frontend && CI=false npm run build &&
#     PYTHONPATH=../backend python3 uitest/test_ui_dash_system.py
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "BOOK_STRIP_20261117"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/fleet/FleetKpis.jsx", "orig": "e3c017537e72d8c5890b22073961fadae7ce4ac7d09c1e644a940bc2dbf0a87d", "new": "ac7d3428820bb5dd6b8a56b3181e14b32762ffacd3f179bf3d072373d0982abf", "blob": "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"}, {"path": "frontend/src/components/fleet/FleetBlotter.jsx", "orig": "33bf110c6efcc4442e1269c57fe13856798aaef123a03f58b47dc62f98112a0d", "new": "692641c2eb5bd2db8b994bc7cd17d486c78cc4262f3a63a483131a3e25e3b404", "blob": "eNrVWttu21iWffdXnCJSPVSVREmedCqRTQeOrSBG+9aWMkEhCGxaPJJYpkgOSVnSqAg0+qnnvf9lMK/zKfUlvfY+vOriW3cP0EFgkYfnss8++7LWJptNMQx9L5ae3YzCQXPgTwLfk14cNYeulHHzI/394PpxLEPjl2gufvvTX8Vvf/0T/ovjw96n67OL4+7V+fVua/dN693u79NnO82muPCkCP2ZCGQo9CgOrViOFnVx6/t3NRGPrVgErjWQtrA84Yc2esW+bS32hGO7UqQDHBkJKxKQiaZ0HczpD4VnTWRkiO69DBe8RpObxNAfTCOM4NmzJf8tEoHlSVfcStefGeKcBouRTxPG49CfjsaqLz0Q+ueT67PD3h9q6Did3MowEhMrvIOYv/33X8TACrEiBJ56aasrR5BeTHxbuq60adLB2ApHkI+uy6rq/dzrd89YVe12+6fsWShvp44bY49iTf22FY2h9o54h2fudOJFQp9BH+3dOs0+cbzGzLHjsWi337ZEMK8L14ritK8YuE4QkIJj0X79Ez3HrvqhZWP/TXEprTuoAVqHGvioWCO+78ZOUOe2KPYD4XhpjyiGPkUs5/Ee35/1z8StFQqHtImD0anRY6VRG+5oQvSo7eGvPaKjDKWY4bAjtiPfcxfi9OQ/utT91p9Le0+1ZbOjGVvxp6F6wvNZgwEU1BiGEhIFOAQ2MDKCrKu4XQgnjkTkjDxjZ8eBTsNYLEuHnJDVT4RmGE38L0ytaTsRjHLBJqLtFUOHk/jEC+tiPJ5MlAmfWjCnYiJ1Xh/9cGLF5YFnh1d/6Pave/3Dq/712cl5XXw8v7junh/TzYocbFFxT0aR43vlSa4kCXVqTb3BuDwm5PamevzJj7CyEKKwuv7V4XH3+qp7eXr4M0S46h6m1tfKra9YpHd5eNSti6OL04uruuj/fImb889naDm87NXF1cWX60+wCpwCjMy5l31rVNo+GSpE3hn4Htmf6w/uhAmLiGrCPGC16WdWPDaGru+Huo4H4kfRfve21aqJ78XbN6/poinetGq1vXSS/ifMsBSGYSgJyPAOXZxpR2ihMxrHWh2ObduON+qIm5Z4teQtGBM7Ceb5XTTB3Q2dGYWYDwhk/gTd2+gS+a5joyNv2aDYkqDjbOzEshcgMmEdz5+FVoCFhgiSXySt2hGQVSS5lMeZlKQy49a3EeNwx6obpyNS5W2V9jnybVDDjtgiNeJj7AwsN+s/cWzEVjwgVwk76rANx7urCx99cTgz9Bqjl/Q0tdRF0Y4A5wSRE2nF7k+7H/u8/7JUrhzGqco+WhPHXaDt3gr1RmPQoMbG1KmV5vjj5xN4iLJSnkupoiNaK3JOprG01bw957+wUdb4RMaWkbUVKt/FaITHLxQdO6L9htbbGcKDYjiX6FHg0Jc4kXiMI8eyb9F/jN9dnG1NLKFSJV0ASzXFYRhaC8OJ+FenUTXxnkeLjvj6bQ/dnaHQ0dlwpTdC877YrSG0x9PQE/uIUx7Cz8KV5nK5YVMiSQ4QEPeb1PGAZnNlLFwfK5NYDv1SK80/9MOuNRjr+lec7zd2ryUvfo81Xb+mht3vpW1wPqempkBbUtsrdmbZ5KJ41OBxP4iW0Xorfv1VtPdokoZJXfZo7I/qMh86p4ETXlvXJxi/GubIl/VypNvQhVacFXMuaM57FS7QXYfsSq5mISMN0ekpdNuAmPlg2gkpZ2IFUMyENFMXDk9282rp4Ky0Uw1HpZ1pyavlHLIbsf/RQcbR27UEPrbA0uWmm5rxi+94uiY0Vll6kjouBc7zfiTuHTn74M/NJZyZ3HlG04yTm0RwRjapQRmjiebi9NMUA1luKUrCURD3R453Cq9BozWNfY3sgVfCWox55m1Ta2livsvTLtomBG5B8MVudpXPj2zm38nMvHh0kqStx4jSFpmwqe2K15po5quwKdvm0kZId1zXRAzxpJYOM5cU9sm6v5YsHAfw7Wv7Wy2bnF3N1NrG7u/zmWHR9yO6hBKTnZ3mDymIAIRhGNBB4AI6cWJK9ToCEU7b9mcenDwUFuOPhj+Na0gWhAls+LrjwvTIaWAMzcKjed6LoR6WnTeUVoRnpggNwkXX8h7Q4b2RNsPSNa2wISA5cnXYS2j4gfSuqSGh5koLPKpN9hSxPWnJTTEDwyVajC++MwGGXBd9b8T//Y96+GqpkISu+sDOeJIsflArqcc0TaFdXHbPtTyGLFlh6HwRcGxej+AUgjskPstNMhNadRBdIG1dpDckdWgMnZDU4cXh4hoBjkSMHG8gISDnbn21SyYp5iXBcZdkQjd7p7/2+heXTceIZRTrSru1dcl7GEhwFAe6ugE682wH1bNS0sFGCsmKx0YhmJJndcljsuLNaWT7Ygyh5dx50ppJKasAhPad2JVVIyST+MpWhckiaV/HCoHD/FqJ4Jv1h2QBbGZsZZGWCPX85lthKwEB+NTKaiIwgmk01m+4tWRndE/xrGxjhXUW47bYZzn6YRNBFhZh0Rpcn2A8aAbQHkgC0y+G3ky4bpTP53D0/PCse314dNQ976+RIImUVmZsFAMUngd1wOy3MnJsyXMz03M8RWRsRRppFaYfET1UFIGCDPVnCst0goJhXfRkHAOFRSKkWGJ5tpKWqBHxF5qASEgHgXAEOhEx00rDFjorzoH7BTE2jkqIqhDjTnrgfLkxHLIQwBiDDFBkaMABDXKshoJZphaHU0Ra24qthhLc1LQinM8UgPn3Atm032Ro8cqynSli6C5AkSvnvXGIQMC4qUgyjke7bmS5hpQ5Auf14B0DMsFqmmgeVC36A5jOEXCfvmTSs7ITbuJYRSROg7lWkE7KEpIDeppiG84KnVVElKFnwnL1LfhombMunUsISY6WygKfAN+eYjMQmMoIdWWTJ/B33/tIl3V1wtWNpGnddu435WnSraZU/AW4Gi0pvLYI8J7EchJRNrciSWpE+4g63WQIfx6V+UiG95+y5Sw58z5pZ8rYy6oUYkn7VMBHkS09fYCBt1PQCU/cyYW5jAwHyT1eBNLUVLsGz7WiiMiuiU3eNgZjJ9CUcQ6mYUimuEz1x8dMU1BMUhbbQRHElkNsGfP63hFFAXOpswypssXvfpdd6jS4luSyiULTJbxfNk8sY3nkbZCjTPWIFkzDiPSlBQhGqEylbIPNfSxDh/pXFFxaVmTK3rCzPJuKDZwjo38bxr0BI0x5YX5qfDZka+nOcQBUJCKVpFffkedcHl52rzTOwPqrrI+ZP7nOPEsLLNQ5fiSfItWrjkCsp/5MhkewPb2W1NIcXRhAU510JlMtPwBlNSmSo+VaJJiytIKJJCmIg2MUIC73tU8SATikCIcDi4mnIOi+wLG2udEv0yh2hosjrk/S4UbEbxu3Mp5JxkAKOGecOfOvsudUFs4sIqYcvY6gKmdXTi7bolWV62/CGGVknwUDjmWssaTqyLmac59/ZoAk/VfmXDm4lXJkWrk9u/hwctpN60KtPCEHY0CnDhXdCDnaG6u4hSlwjj1CvwjmABiEMLst/IJE2yDmnx2oB7upsm3XwZ9Vuk3zEd2m35xuq/4cy8Eb4WAUdvIKHxrWVnpvfHXsb+8NGlOiwShY0gwo7t1nfDE0rCAI/Tm5Haq+WoZ8U2x0X1Og/wEbf6z2UYrtNORgeYPK2KTBml7m4S2NbnuiMZO3d07ciK0AsGE0dikSNVIzKIVJEPzcgvMJv1YCugre37jMMm+ADtpU23G8CBhpF3lKkdncKZK9NDxnq5Wfie9U9dDilW/I9ng32d5UgFDhgeBAFKcRJ+FYAVmsIMeNJTZa7loKThUNF7W0LLO6I8qzrZUSGQEqThBVr93sQud+jkHFGPBx9QXFAkrikGAo10o2xpmNyOHYCSX7CsnE5fkMKGR1ykr8USrgrB6qGs9OOYEptgm7LWjv3lqHoTXg4tXXSvZbZRoHSrs3Vzha+AI5QkgMGQKAY1PFsUp+Ve/P5d5Tr9z/SasNxiPx25//99WSq8Kc8cE50jcnTI1qnNxQvJS9GLh2pGvSa5yAEVeX+Aai6uKEdUBVV1perUJQykqpuOtGiBQa+HkQIymPeggjhX8PRgpXMVIFJxV2NQodm8wHP305QWMsj9TrIS7y4t3UXAdMag9hO1xQehomTbmGhjzwvVbfUNNdkUwUXphN9CCEy4DeA9XtKrF5uwHXPZi2NybPhwFHHiAqzlguHrc2LIFFFMETA3OZZhc6PeQXReKIS5Efo+T1BPr7QzNZX+Ah1FEGpECf215bPK+qDwzBgFWZYRWdlOTKSCGDAPIa+k2K8F2gz00TbNlWCspW0BTXHUMD9hRFtW2bXH8joraShSc1OnmWONuA1j9K0Y+ddroycgBfrR047xBPqc6V7Yzuac2s4vgqa0hWecEzzicF4T3pDmkPnp35T9qSug/Js99Tr2Mtqn6j8GRRop+Zy592ke/NZfst2cjmRZcqV2UUpFieQpwKbRSXUB1utFe53QMInLXEUycVRlNaeKUASOp7mRT/PFtZVsuPdKAbd7PK9pjxFRkwyTNLlWlUsItiLH0/qPKp/ayuwrDeXFYBOrURQOcPNwigJxnuz3NjkuU4c5leJAzeTY44yjByoVZ4i5zzK2rkUWuK7ySqpCP9NuW5tKPO31rAMoCPhpYb5RUhKpGmj4r3dwW94VVg3fibpLqgv0/bcC4DBmWXCYujcoiqv/1LcqH0NRxVQs2XvO/8h3KpMge6bdCnIRs4VbLWqzMmzxSxvZEcteD0zI+q7Gh9ms18K7Y7/EaFimyu/Qz6tbIA1ehSQXPNbudk6dgXUDNV1Lf4w5r10n751WFs3fL3WtWCdQYiFaBD8HStIJKK/PAlBT0aeWot8EKIwBm9e60mx310JiwZlEMaNW5Y7t33mqpgb3z+5vUDD7X2T9ngx9Zpt3cfmKjdev3ip29bT5SgMkup/35zXVv7eD9h2ZU547CafNGlCn8+cWbjLztwFr2UD+834/GzBhJKfPagHtHZB0f1PyUH9C4nrYQ/0vMq5aWPdvycU9hHux4pkvpov0vgn2cr4JTfPVKiXx2K+7B82Csnux8TMagUnzdWUVboL+bcR8CD5QCmeObyXVKV7jiXbktZ5cE3F9m/p9dVxBUlNqQciQ8LQfkkfbxIL9k4ePK3CcYK6Intg6pugBuSNT1sKaU8oZiypXLAyqvUDVbepiByvrBQwFyIQUb2OvnpxYMNzALnu+VMNzKRB/hz+jLxWTTamn8pZ4QtS/4/8OkNO1tj0f8Mvqxet2T4awO7eFJxn9bYRmLSvVU+E/3P9FsgdroOW5uq2tO1epebG9qNGkklvWI76gtdFP4AmSKHATe+yaFSE3yFq3k3eawof8K3oRCwnWym/vtcm91egnjRdI+7wEvo+JNnXT3qx2j836/KNbvfVG7ZUkR5ZI3+MRtrter7XqxXlrEXogtPnrGoQZdmK1WenzxfroWtnrYq/FYa9OIKNg2Hf2lP2/3GwsoLbH3LlvN8s14KwTaKvHSwpcf2nazm5Uo9olyR4K5lAINbIgf/MkWKvwFgAfQe"}, {"path": "frontend/uitest/test_ui_dash_system.py", "orig": "d805fce77850b5a61fdd88b1d9806276680ce1808c5818f5cb1054d2ffdc3f79", "new": "86c3c06e146ac51ac7c585d3f7591e99b413d0964a7c094ae6104b6c893cf74e", "blob": "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"}, {"path": "frontend/uitest/shot_dashboard.py", "orig": "6620e7918d19649f9fcfa46d000a0b54c74b7fbe44d2486748decb527513e981", "new": "a96d4b58011a83eac8934927d551c0467b86b56bae748fbc9dbadbb1d927967b", "blob": "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"}]''')
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

# ── prerequisite: apply_dash_system.py
if "--rollback" not in args:
    _k = os.path.join(repo, "frontend", "src", "components", "fleet", "FleetKpis.jsx")
    if "DASH_SYSTEM_20261117" not in (open(_k, encoding="utf-8").read() if os.path.exists(_k) else ""):
        die("run apply_dash_system.py first — this script builds on it")


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
tmp = tempfile.mkdtemp(prefix="book_strip_")
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
say("  Undo: python3 apply_book_strip.py --rollback")
