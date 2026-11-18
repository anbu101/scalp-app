#!/usr/bin/env python3
# =====================================================================
#  apply_wide_screen2.py  —  fence WIDE_SCREEN2_20261118  (DEF-059)
#  REQUIRES: apply_wide_screen.py (WIDE_SCREEN_20261117, in 11.9.4). Frontend only.
#
#  Dashboard on 32-inch monitors and TVs: the page was capped at 2000 px,
#  so a 32-inch screen (2560 px) kept 280 px empty on each side and a 4K
#  TV at 1x (3840 px) about 920 px. The cap is now 3200 px: a 32-inch fills
#  the screen, a 4K TV keeps ~320 px each side so lines stay readable.
#  Laptops and phones look the same as before.
#  (On a TV, macOS display scaling "Looks like 1920x1080" makes text bigger.)
#
#  Files: pages/CalmDashboard.jsx, uitest/test_ui_calm.py (TC-CALM-0039, 0040).
#
#  Run ONLY this script, outside market hours:
#     cd ~/dev/scalp-app && python3 apply_wide_screen2.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "WIDE_SCREEN2_20261118"
ITEMS = json.loads(r'''[{"path": "frontend/src/pages/CalmDashboard.jsx", "orig": "79a57e6211bcbaa96ff0556c52128c6134da33a0435005c42358be49096409cb", "new": "97b4f55ae149b242acbce05c53463e753c77fc57e90ab99a13e8a600f3829a87", "blob": "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"}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "ea363f8f311e4d8645be38967c755c0cf43d5611383e2bbd60111bc2621cfc45", "new": "03837c5f698b9b23af767a3d0f99390d9c89edc0c57d2746e6562c1ecc6f9672", "blob": "eNq9W91y28hyvtdTzE4qS8AmIf7oz3S4p2SZe6xaWeuSZJ+kuCwWCAxJHIIADYCiFC2rtnKRB0hOVW5ynVdI5TaP4ifJ1z0ASPBHlr3r6JzyEpiZnp7+756GlHIvUXHSm3k9x/Yn1vRefPrtb+L16fWb3tnpxdtevVo/qtVqx02RjJSIlKtibxgoV7y241E/tCNXGL59H84SISehq6JAmsILhI25tr/Xj8J5rCIRBrz+1fvzixsxiMIgUYFriWtHBSqmFxNB+/cG3l0yi5QwXIDPnwhIU1x4t0o8F+/sKQDayV6t0awevsS6gPCZqERFMW1NG+3bU28/UlOgtp+OxCMsNC1xw+ew/WxFBNyVGNlYm8QinAd7fdsZAz0RzzyMGOkjQE4ZLBOM8WOUeX0MypnW3p7An+PmJxTffy/OzlsD24+VCKYTEc0C0Z95vssz3/3TzZufL9+d3rxpWdZ+tu30PhmFQUPQ7nGyv84fITpnb65+fttu7U/tZLSfhPvOCARU3T0JdnqTaRgl4q9xGGS/wzj7Fd/nP2eR73t9a2pHsdrbw4BF0CwvALsSo1rGKv3G9aLAnigje7b7Mf3X6IE9vur1TNPMYXo9rL4l9sTivfjiv78TQfjRbor2QbW+x0JREIN0lx8v2u2b3wGzIGgpzLftm/bVdVlMSbh6YeDf43ekeuFUBWUx8e56MYnqo8D33ltxYoN4pt6HhG8eecNRYsX3gdOD7IicD3hejj8J9z3NddEizqjg1oOMWUOVGFIPQO/CSMj9WRzt971gfxiGQ19VtGzIvVen17RWjpJk2tzfr9WPrSr+V2seHDfqtX25dw2itsR7C2IOLiaJFwxjwtnCwM355Z+vez+eX7T3Brbnx2VxWRYqgva0RKdbFp0q/dPd29tz1UA4I+WMjcQpCycMXJAvHppNFvhLTBTPW6LGT9PICxLDkEJ8+s9/F1J4A14gFGkLv/0PgVM9FwP5kDgLIR4AaSFNXozJQZjwAg2b/hg5OgH0CAiYKULERdZYo18WA1+ppMVCVBZzz01GrdrBAQR+pIgXeKjiwXYnXtC6iWaqLLR9a2X2rUwWZqJa0rWjMZ4cP3TGLUmWslKrVWrHN2yZmtXqc/zbqMr08KAjqZsKEmsyhlYZ+iFOd1F3HhQ9HPOjma+AhCSql6i7xCCdttzZZBobD9LN7G9PoyebKZ5lIRk/2dR4LohUqYlmyj6sjZt6Mye5Azf7VqDmPYfMF3a89dScBLb1IJlSWMP/xR6aWnihfyxAFW+i/jkMVM9zW/I09uz9n0J/bCd2yrHpEPCxC+8wtYfKyN5bPchSGEGs6Hdql1PJ6mZzwsCQtIqnSuLKpO/aQjVXAGSsj5PIUGYHxK92TXMVQqQ+zmBQl+uj5sqm2frIgnk0iXByw5NI8jE8QZPzEkfOt2BZsKA+ZGCU2yOaGPwyn2K7bs8LvAQWJfKmUF+M2v51EkY4HAAoOzLMlznNLPYvhnz2jDFh6YXNd+37whkiazDzYZB9lpIWT0tP7nsOiOl6TmK8ty7Oz9qX1+2ygKlKZnFLfji9OH8NULe2D75pUYQd99Wt8luS1YA1k3+lqjmDlZcEIrITNfRU3OrIZ7K7BWX4lURN9oGCgmN5DGNMAb4MgVRW88NgSOZSwT/iJKuey5oFH2chNuOJVspdYo4VT30P1F1BUpqdSq2bDXyPx2qKM/1pCAWcHmQ4hoRrokiNEp4fEIxoh9GJu2IAq8txx8cMclmy6PBLPQ/aIbWE0vLFYgdzCwHLM1BL/3p89sAL3Gc7KJsy+aB6UBbpiVyVwEgCDRmEArRxlVwsVWQYJqFBrgKm0YaMzoLEgxi44YRNQpD4IVa4qXRGCj40IJ0mzc2sP2xSbEwzk5/OAWwFEZvZdAApO5ZluaEzm5A1BM+i+2vlKwdKcAq0Sx0XZqNCkQ9kkvx1haB2y5vvPddX3ZLZtSb21ICc/yCMh5SlntsUjkUroI9WKgfw67RkdUSDE61WS5RymCXYM1hAmuchvoxu8GAxxR1l7P/yS/x8f1gWJVEyy+l2cwgFxUdgEiyIQ675Ff2EHz3zPZzzCuczTIsNqJnytim++84pEmDb6Xlud7mXM4sIM2xxmiSR1yexKNmRZ1cwQk6lZOrzJBBdHKVvu0Oc2dixFQ8DvPj1V0inadHJzzS/6VWpJCAi4Br507mXjNbjF8OkgG+qGd6Hik4tDju82cTy7VngjAx1p5xZYvcRMVL02NIxC/xsNCTrUakEYSW2A7cf3pEh2dPhz6e//Yb/p94rztxUWbuSgldPfXTBGLMEQz0RzqTnlZukjcIw6UrtvQjCURV/qUfkOEbenFUoFargfU2yiyKL/Sg403LAeqILmFATNkX1u5bRCSr9MBzHa+uq5b1lKEjamjrx2L5FvvPpX//tkZTsLYcqpsxdO3mmOPO4j9NQOr4dx54jn0zI1SM8kZL1r6Ek4jKZEuFM45iTAcH6rRfOYqFPLihSQOoGnJDGTSZ2dI84kuVvG1GK4lZINOFoy/BI8TgJp7uJ92Uyx8ZsO6VWQaTDRu0wGx2pKCT1epRwNKkrdxG+IcsMhoxHz86th9SGAPSSTGp5cf6hLTPJZROog9AdG1YG3hD5VDdd/fzTv/xP/aB89KIqV4SYoPHQi/JxrcqB1NOAh2BxBNgFjWA2sQfT2sDPXszi0PeGIphN+ioqp3zsQ0tcjCcpZWIybq0n7k+TVeCo3WQ9AFllX/nhXHBwVorFCKaRz8hb8dkDxcmxSygnITgwSuPJ5RRQp3ZYrjXq+fvVQw9khgryBRhgpOt8Xn1W+D4a7jRfVLuLT7/9lylXAlOPMNbnAz280G3NlRpnJ5qPQQzjC1ld/nLyPYoQU243jQ9B4/mYUshMxA5q5fphVYupRAKVgK6xR9Sp1RCY3zINsaSGTGIgb0aQDjo0GQIWGXoApwKliU8FIBYowUYH5JyPFxkRfY9KVa1iJPPlccwIFYKuiG+HgvwgRS6+CoZIrfQuyD78vv9ZHddgYB1XqL+LbEcUHGrkW6KuaXUJOeUslSmUbsoj1RfN2upbIhzvtlaAY4jw38haBa9hguZwQTuesdATc6NLFMwjRJ3Iu5zrOR3puVKH0w5tj6lZjk9DvpLd7o4THsuyBgM48vys94G8i7x5e5r9uv5z7wP5bvmhfZP++vnqFf3q8ql37b7cWcNuvz1Ll7+6+omXF5UT0QefThCFsohTGMCkSDzUIlF7olilTKZqCM6KSYgJAy+KEy4w6Dom+QIEkDQ1BklxyFwek4kNugXEeWcr3lLrSUqGXdJxQqdZRY55D+AdSVKVEkhqvJM5xkjxjsoHqQHfmKmH6wcbwwXjTSQhSqVSpU0K0SKz17wVwmf8zG12H1G6eEgP16RTcuRa5Ntix0Ff4KB9t5OJgCYO0m5k/K6gAgqjTzNy0aAZr0H65VBKTD3UhsEBA+xoOa6FTw+fB6kx+d//FgfCV8NYrplyZGdKx+bE3L6b83ZnmrssVcAM7kxxOf1cKaXorF1uJwz4CPugghXYK2ErEiRjNbHloYfHlWxDSxbFc5MTvOfoSeznEk7CAFPMCBAjf75sU6XWfyk4SHO1CpA5sOMxHmFfijgveDrS6485GTfPSlg97FZ2XeDpLn73ITUk8JavPvioRM3Vw+KuAWXmzP1WwXHWlbnsbnWRmTVpaWS6YleW+ISw/Qmw5qP71Qj1MA9QMbArbHoq2BRQbZv5ylzDLiu2JNAmc5lD7X88vxGX7dMr7fuBLVul9tvTWqP46rj694UXQOgHYsQzUbNO1qJWKo0U4+Uv5QpQ4wQdYRsAUgmFz0UJ+ppZSOwp234tQlmCw24FtQPU0SguqS4w8DCvLaZ3plbUES7JeKbr2dqXU20c8hYGsdydXCw5O7BjiojX1OoPF8Zs44OdyWGt8dnkUMtRITfkI/MZfmiJxiZJ7aFNAkbGhK76XoLvuL/SupmTG9eBahCpeCS0kToQRG2Cqs1LvCukXlKELURXWHmmV5lg40cIcZITYhZtj4TWlALzslhogNgz7iV3T0plePLO8LpGKQyjQJFOauhSn34DVMU1jhjgSomVZrlxqh7LmLhQz4bWnKMCbJRivJzmVbg3Iaq0DOPcLZmpLmTGdZN1WvpJFeBxEXAinycfkMXs0CjlMydjoXMw4wEnWexiFmqj4HWLUtzHJLSRMcanEGU9bt0B9N3pu/bVk6DirvGpQE8vLrrLsG+6xXASsC8K/GqUTW3KGh21uz2QLsR3K+HftBj/6bR8GSiuRIL5zDXryl49uDfycLtwrmLWrymio8Y861/605dp+EhDFGNqs0hCot/rQLOsl9naYBKyGZmeVhRgu7NbkyjjkkciHIiTrEChb2xYm7NMv6H7JHQNPh3hbItzT0qbSIyDJBfjoQrX808k/Kg75xlo9qPtK37GFVPo+3/RV3a3mMnH4udHgiRKp7BXR8bwteIfWvrhFg+EHl2WiuldE1wTVFKZ2/doruCNxANmZhe0fD38vLVyO/eZshszqEJMpKLbY4VKfYW7vKs3zG9bf9tdx6ydaFe1NTjYLMpBLNbrbGwvfl+hTVfTNiOWNf+pNWer2/x8QTEvwRUWb+75bUojnI8UzIDWZmJ+M9XsLZU/SsdJj8okq1wL1jT4WgntK4iF4m10YwgM14jiea3kTxDZtKXEMLe1DVRPmoeNlbaBL5JpvgjbLtOjp9Y5d1s0Sp6pNKlN+SgrIumDx+IeBTS2cJZorFwLC6py2NFEudbqOvbeSeqv8bpen+jhz4rw5oEL8thIS1hkeRublvfJZr0gaWtMbwoiQ1kfS9cJ8VDI7Lj6o8ezeOdrJS4IgwrfvD8mW7pX5UdqMvumdrB+uNMO1qmIQG58perweRZmOcJjt17p+dnd6IyI4/Dy8hlJNoJ0vgvjVj4YAl7CrsT8WspP0YOnHqO6bh1qvFh2Dp0cHPw/3QN9m0hgPfjcMA+iwyksrFiMioxua3xSqDvwvalG+Ksc5TcviDy18jFfp/p2B7fNuxHYkrmzK4Dv0XeZ33ptPSKD2Ol8lwlbDCMYz5U5O3WQRfzzSjiQPLEJ54kwmqBO79IA+tesno2uAGfE2Gx6Ya2lmbVPb+FitpSsxbHirr9Muen6muNIVOoI4OKrVZjb2/QkLxjTLcPDIs2aUV8ekUswsiY+6TMDqHlORTAetk+/xyG6anTpxqEonXvrbN+m+ORervQl7bARulEQ7R75xD/ANGTn6SSj7ro8QrzOwskUuuVeJ/e+Mp4uoMWbupJJAuGHWaz0CAO2dhhsiHA9rTJTQZmwtwhnFSN0N3XBJi8MM9FQh6Hav/YinKTBZ4Nxvp+VufQ03IkAWC4jOe/PTq9e9960T1/nHeyoNbcvX/cuTl+1L66Xb43X7R8r1QPsr3/UzabAa1we4kbGGVdmU8Gtv7ngzFludMtorXZS/bwU6NgvbyKm4E87j/kfLhdb3UZ9dcZI2e7GNebXNmQVGq8eVmwGNBqFOLpTfkJ3ExfJdPuI7kHascZ2QL6ErKjunM18m4NCnosXnXr3Zbrz5At2niQTwHy5gn3as/aws4WMkW2K8WqnFBXPEnq34mpRUB6T18Kwfn6ONOZ3d2OtGuf8D7eH3BNW3N3Z3B3HbYpJAeriJRq8uL8r59+6/jYOuAt0zpkYVQD0ndLKhCO5jhepO4sb+5SDQqlnRGVMqhlBo/CbsE/LPmyX02W0AtEezV696VuZsrYlL9Dg8zqYrmPxEMEh1smubtUnvzUydDKJLHMN9vp5UJoh70SD/AWKeJgvuBLCHo/SS99G+ZECUN3ogRMKo6RdJC4BsTrylFuChw7Rh5SbtpV7QFoL8UQpJf1iJvWHHZyo5LklVGDwg8+Q/sYepe466t3cIKat8jnfmoUzAd9NU2BQDAB1TlWJatJGUnhFxdCvTu9RrCWzQW2rxh1BvSvIIrUtJvTdzIYru4PehzFfaOjmRnyJEvoYh/JmKqvEn4TaFWQJJBEzulFcvFzjrD4W3adDCymyKVGCrh+ZfUX7kBLV+M4mhfzOMZcWY+KhLS0YQia4adimBk8bsLjRc9fGI4+qjt8Ztg4DKXhzUPsYJNoY5i/t/KVt9cMkwUcmPBWX2Hrm8qVNLzeQztHEjoxch+eVc3hdRrXj6LcZwK62EEbRRmy3E4dpIgjR0l+npBSRZq7/GOpIYFBsCVhqma7NYrLWHPomihUrRnrDAJJwhmhT2fiHy1JQKFzi5LMRNPSpmYLh4J7Y7hfU4QvCmDyc+Mv563bv+uyq3b5cDx0OjxExhFTJrR1V0Hs0Qhuw84qswTs06xm14/oJB8xQ0Hr18Ig+k0M5AWrgKNCEexepV5H6tXVgw7FBuj3H+OEsWi2qTOyx0i/R7Y6rPoRpswlia+7MRTytJtPkHvAGeNRjmTFhQWLIb9un1++v+IOgDYXXxfSv0fBHerS3xAs7W6RzsdX4ID+g7xeeHMguSfVIwlXcgtQqOzK+ThHXEFwmA2MbEbYrvd0RqyECV3gRfBxjGFDwPpPQFhX8ymDn2janz2YCN5xby6y7TOe6aNK/DI6fr/Qz8wlq1NS8SMug9DmVj6iDkc3erSsQNxBdpK3oyGMMnGjtJBn61PAbJ1fZXPtu61zGxdzwhVpK4wzD7WTSre7mivXYqVX1TK1OmqJR13o0CfGZDNQGhacjSjrNTEnC8CVIffCTuPlAbrh2J4zGyYGe4lFmyT09GGkg9MVbEYdpBxxc7T19/elSJ/AypC/TGgTVfOVksM6WxTG1G69YtmNqeDRIhzfHTvQY8Nwce8FjhGBZNI6Kg7gbM39PArH8aq22Kw34ptnEZC18SO3KckLIfaWTjtSCqasXDt3pUsrP3RzQGfxgWVwOI1Q9yuaQ2OtY7iB7lc1G5ogn0hyG09gSDpJg00Ysr/i2gLaDXhReYdP6evBL3y+G480YUAd9dOOlqBqcfhezbr+FQYWOGJ8sdkr66KVu06oOFkhD6d28pM9d4pNkYxDgMg5ZsOvlpU3fYs3h3yZf6902k/RG6rwJCCXk2JmdkwaEvWig06wv48v+svaiP+ccyF+Ch9KPp+cXJYqR+ItMnSyUfv6ptEB/Fn8EWuHsgEfNxT6/W2iEYgnmAwx/iP6Aj1dK1l9DL58r16BK6immz5ep/deorY1Wzb3/A80aj1U="}]''')
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

# ── prerequisite: apply_wide_screen.py
if "--rollback" not in args:
    _k = os.path.join(repo, "frontend", "src", "pages", "CalmDashboard.jsx")
    if "WIDE_SCREEN_20261117" not in (open(_k, encoding="utf-8").read() if os.path.exists(_k) else ""):
        die("run apply_wide_screen.py first — this script builds on it")


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
tmp = tempfile.mkdtemp(prefix="wide_screen2_")
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
say("  Undo: python3 apply_wide_screen2.py --rollback")
