#!/usr/bin/env python3
# =====================================================================
#  apply_wide_screen.py  —  fence WIDE_SCREEN_20261117  (DEF-057)
#  REQUIRES: apply_display_fix.py (END_LABELS_20261117). Frontend only.
#
#  Modern Dashboard on big screens (16-inch MacBook Pro): the page was
#  capped at 1360 px, leaving ~184 px of empty space on each side at the
#  default 1728 px (and ~350 px at 'More space'). Now the page fills the
#  screen up to 2000 px, and the strategy cards form at most 4 equal
#  columns, so a wide screen gets wider cards and no empty column on the
#  right. Phone and small laptops (13-inch Air) look the same as before.
#
#  Files: pages/CalmDashboard.jsx, uitest/test_ui_calm.py (TC-CALM-0037, 0038).
#
#  Run ONLY this script, outside market hours, after apply_display_fix.py:
#     cd ~/dev/scalp-app && python3 apply_wide_screen.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "WIDE_SCREEN_20261117"
ITEMS = json.loads(r'''[{"path": "frontend/src/pages/CalmDashboard.jsx", "orig": "60e04d85a50374a692de8dc67177797125dbc649611a9fc1cc53af30061bfa56", "new": "6487da3aef08e0807eee3db2b5c3bcb3525cc780c911b27d47fbde8e4f98d519", "blob": "eNqtW2ty40aS/q9TlBieWbBNger2a4ISW6GWqGmN9QpJdo9DoWiBRJHECAS4AKjHchDhX3uAjTnDXmH/71F8kv0yswAUQErtmdgOW3hVZWVl5ePLrGL3zZsN9UYd7J+cqsP9q48fzvcvD9Vvv/5DpcmoO/cmOu0eeOHs0Eunw9hLfPdv6RN6UKff/vEr/uNun4nA53fb775/+/btD8WXbKpVon2dBpNI+6qkoZx4ngVxpD6oFhFvddRoGqc6UsNnIrwfDRcKZM7ihx21P5+rK51lQTRJ1W//+V8WmdB7jheZ6qvT2NdJ1OEBfT32FmHWdtU1nuaJfgjiRUpkpZWiSakgVfd6nikvBQuhl6bBqCX949BXiReE0o6+n+iJN3puuWbaSk11Eiv+d342UMNgoqLFbKgT5ZwED6A9VkGmssTzMeks9r3njtJhqtWFN9fJjoyCP4kaxvG9kKz+DSEuH0Syjor0Axqli9lM++2O8hRElOlopDvqmsiqrvqk9T0up3GUTVXmDVMSpZdkQnUEKaVMNY4gCyIGtjI9eQYTXp1H5QRZqngCXuQLryqJH1OVgiBeNhkl4m31NdjysSIgQy94EC8Bw42hppBkFJdDPutsp0lQe6OpEMGfJNCpIo4GUZY8dwdPEOlMZyRkz58FUZuVNPPm4B5y0epx+iwEx/FoIZOmr55QJMUhanMv0iFPMKSZsqwg8jB+VM6VYfhjnEL44Heox3Gi22bdD73M66k/D65V15sH3XGoddY1ovtOpW0MnPDa0qqmPAaLj/nE69SbaTXWJO+pZqMTvRNFS7TnpzsV+UTPod5dnnJazpmGIKV45ln9WyoiIc3sbgSzeYzJLNUi1YPxWI8wCdye6lnMN5d6zNerDLNUuRon8Uy1MO4oa+3Ueh+np/EwCKtGrtud0qS61sdGHyxTkIV6Bg1N1/azGzT6nnrJvc5IvnbPEXRaP2Xd6uuBvKl611dsqU4H1/t1EmgX0Yhdu6k9PBvSJdbJEH+hu1mOS74IDShYt1u4uoOP+5fXn6/2TwefT69XPGEx3KEeLiYXrILrBqk+Vxwyf2A+mK/tIlpYNWpI9kMS3+vkYKpH9x0lDx88f6KLh+M0Xej0hTkL7avnNNMzJtFctyNqIJ7oNQq1lg0SxbL8HKQBtCrIntcqz2ozm04UP54G0XGavcoG/z2Kk5lXUwB2U2dkm1Zn47vgg7p+kNKaU4va/E/3L38cYMmvaeFPj886CKJX9uPR2fnnwdkhPdikZ6zOVxqmH0c2wQN49Y66uuioy/3D45+uOiqITLMX5jVC7OQ/FRkKpx8pPL3YvmhhD12I9wBOpaOO/VDT3Wuj2j2a1iwu65XOZaOm34l8/XThZdMv9a4aNszw+Oxw8NfPF/vXH1dMcAP+BBpydH7wEy3T+eX+nweffxz8AvjQSkF47qaWi3A5ihz7BX2Ogb56DBBmbVfCbp68u+3Mi6GOT64Hl/Ux/BJH0VyOghBigBSkw+H+L5/P4EKu0P6mdbWIyF46qoXobu6uYa3m9pP2o/LherpIivujJDB3V162SOj+thjh9PzMGuEvXsQd9JBH8RK67M8TeWISf1lEcgn522LCdPWcLucIHLgApdHlUI9onI3xIhoxuEtFd6/hVB2x0LZabgA3MCc+Im4fCOeR4qp22u6EfPyz0wYsIATlcIN+X22rv/9dFQ/ftxEoMamI5q/vdUTKJ+1lCLW7YpgY1Yw5I6jY+Kq2jPvYKSjfiYWqeK4jQJBIfbWcqfcYe1vtqbuvlqfQO3ccxnHizAC9vt9u51N1p3qq1cqp6R/wKp/d7ai8yVnNQdTZsp3FKkejEOjY8LJ2+NfHtYhX8iOawNTKW/BysRyLb0YCPCxJODfqgxej+zNeOaet+u/LKfi1tawY/2pJUSmaOD6t78cYWuq02+7c84FCksx5B8XZbrXzXr0h2F5ken1TmiDUrPuGMW2JOQvj7amXYGwWgzKhbofR2DR+jBiqVbkHG/KYbbJNiKpU5eEiCH1ydalDpDplo0qf0VtEcOrNRYmpJdxIMgCsdZxE5LXByJl7TNBh+EzTdRI3AJSGni9V4PcUPXYYoPaQWYRhB14F05AHmj0RoRVOXJ4AWUbr5PjnQautJi4D275CosFJx8TlzvyGO2LM1IzZURNmNee/wpZkVDe39AZNywlMViaQsCKIKISHi/2LwWULZlIM2qt9Zhb5K/PYU465w8xNj3Y1uU3QL9R1xxo2e4RS92V0UDUk7DHllfTBdNz5Ip06ItoJzxp9O0ynI2C96KL++MeSzB74pVdfiw61SrrFF3rTMt9yS4xG92ngFKHNcTyMwsJj00WO5gxdcOBOkjhNae7bbdh8+dFrfmyTAW7oJw6UJr1VpW7WsnPHVsmggPF9G9Q71lpDJuk+ZRY/BYi4/SaOrzcNszl0u2hXofJ6Kx9voLsxZXlIe5MkTiDqtOhWAUHnu+3tbavrkAGptLJgq1NyaLW9EdfWQYzJzvj2VjpybuOUeJC7lOmQU/dZpObofwzYnzx4oflaUqyowA111Ftht1hdaTwKtZeUBDCcyjswHQxbsWqABPN6JPc1ZgumkOXiryEPP+uFV1mcAE2QhzgGBHdWoAu7DPIJGBeeMEP6XFIoXtumfYNBk4tgdK+FnZ/Kxy9ztLn5O3law8vYgyNaYUY8g8iFb3n0f1U2JdZiobT2T05aazgp39uclAxQWBubIetc4fWO4aI2fLoyPGKDLYJc3LWZMkEKnvA5buoqi+VqG6xJUYjj2iMFJlP1QMEKHgAhTfvrdLr0mtUSkxw2yRzXOVGOiH22VrdKdqiLeP6i3ThI0ky8LYJZEPlFJEvQjeoI7NjPLwZnLRY8tbvZvq28OBNoW8ovbyjgscuEvYjLqBiv2Q9EkKFyIbLCjZFU+X2OLrRuFEBLQdR028mShTZxxWLDMKBeXdamdiMn82vLyxQoP+DiThyikLSF9dsypcl0lOAd1ZKaOUJPecFMUQ0OH6xalINy6X0WzwvCpvBjSktcvuqI35GiUoY5PqvIQ6XUYxkWY448FArD1BRhtzBaWoVWI1V3tEgSePp1GkJNINhGS7vFKAvRgDLoeN4TIy/AiTEsfLChYha65hUtyU69sX5IOSl5nGrNyUYWL0bTWYxAi4d7/YwcIGoZzULjCplopo9ojhau5/uDBzB6EqBsEenEQa2DBqXy0JzET6CKhocLqJTiOphprJexppepJ5oYWjsAB4nvTUgrJkVrDAGksxgFX0tBZRkKgdTFbzRyo6yzSrTyEvg6syDrak8fBifnn5D6ng1O1hbipfLphHpCpUqywZ2m7qUEnaUYGmSN8aFh5C9gPAQN3H9foAh5pUO4ICQiLXxttXdWu3wkUIyehKt0doBMHrgegP4ZOATvAe+RhDCMYT8i2kwQER/Jt3+IkQQjLTgIA4x6SQ6v7U51MJlmQF/bzREfvYi3AwhKJdTVYR6+Vm+3V7iLx+RURKwvDkXausVkm91n3tMvtkCKm4FgJ1es8KOwulXoTxBBY+RlRZBUoUR/YKutdpFtkjelJ/UeKTDkZygI3V8oIWUWttS7dlN/VDGtlARtugCwTYLoms0PeRnNKZ8/3a324dYANfHPgX4EbB7qqYc9FKQfosaEf+WOEbC3yGJY6JBSwx4tITSpAMT0r+Er8xdsDpYijrqj/rTd/nKjH757tRV7IwPbJNBYMcUKfntro59xXw9FhG4WH23IG/iMsB2hxk9Mp+0KwCkC5mb64Abpx8D3dWQSPqxq4s6wK6U2OYoeHbVsWEIZKkcrAqpWZ4Mo21gIx8RbGpiGpeGtR46VlIRJDwts0zr2AeoM21mcQWB7LmVoey7Lx6hd/TsneFUDi1sypqFoh0n5egZtlU1kN6pfbEqY1VrNrbFsN+a2BgUYCB2vIXHjui6l3tgmdOjegBVb+mvgijtDpl5+DmiHDX1Jgitfbtu3JU/U4NaauNmh6VelT6dglGKOjrxhqJF5lnkMpuWlWQ/CL+Ag18l6Vbm3LJfZKNXUZPr1QqlTZEIrndu/tzKqzo6Prn9BKenD/tmPcg+TD0xdE7sZxY5iEYniyURUvsBcBaJ1nJhfODHLGTq4JyWLHsEmMESejibN+k69RXgIq+NEI0dv79TsNGrUCCthQCvJLMWVlWVTSgvLOuJtjqpY+YK604uyAFq05Z1Tan1n6TI26fYnNEXEwz07nDhEyUVXh1L2DJG0i9gCF6OkOFOnAWjPgpJsmKzJ2dzklJidezHMZl+khO/Fq/fq3Xbbmi/26KSqI+ZoHvf2BJejMr0YIVuCwonSpgSDHKPUhWWgmJYG/6EtFikXNzQxTUP0a9ZwN9TRBNbcU5i2BvDXlveIaLHNyhDlf9HemvzIrq9fZRt1gsVn9pqr1IVhS3tIR07AOGkqB4pdP3jgyW6htpgFfr9FNfgtaheiXUtxuOwvUcSQPR94MMjoCdHNC3GEgVIClIpaI9qFpyL5xAPuvbpw01mHhPn0KaEXrUdcWuxMrl0oOHKbMZTsCnPtqbffAL3HIUXTA3dGYIiKez4Bj0tCBb2qarNHpJ+eQqwIj0GAR+X5exNXd1MguvdLYwm5+t//UcuiPku6Ts9rSvD5bpc7FlSsTUG1BMtShslVtzaO4qJQfynKu6dMsZYfSfdbokukvq28kqOZaWULe5g14XhVTt+akVLLTWMqgBjgnx4wDMEMm4RTcEFteEPdlJkoYlJJG2kWyqtx7PNqU/e7omXKNL5aGkPL07u2VO6L2VrS2cXe3wPd2kFogt0VaJS1SmvUhS6HQaK5QkcaE4eLWWRpzMwn4KZk1F6NBA1ALXG51jO8zPQBd4fq3WEbWnuZQ5hrC/aBVBAhBTjQof+/2d6eP0G9vHDkOPBKf8AYqKtfuOEEpw2+Adojd/UtJyrjpN2+q/gJJx2KFm/Up+PDweerg8vB4KyMEz1KU2eUDn6rZCZwNGkMRArkArioaZ+EkhN6kUiY7/BhDygOpoA9XemGmjrPax/sHyHLwGyBZ1IkeKUG2IbGkWKvhJQMLgF0R8CduQluGIBbcbkEJRKsbig7AFQejghoD2nTAYd99JziWZAoUj9JITaqam3TQ7BnSABy1zgMem05C2O+Ncu9M3KHzFXzFiZNwJu0Up7DxqfiXtoVshl6o/sJRyGyHSohWJ4kiO5ZFSS1gKSw/A/TlnieI28WhKRaD17ibG2Ntujl1iJoF05qGPvPNc9CU3u/vLux585TxyGbW1V7LQcjth69gE5o3UKVh3ECNdgCGMziGfmsTdnjpSRK5XfkgJh+MRrJvBQm9PhT4GfTHgLg9naHUA+yFvC+rUyi8YqaPqLO8vYb7NPRkZtQjzP19k/fKghUlBBZJh8x4vNV8Rj1krffbwUR3px6ow+0kXKBjXPo6D9pzw2PTXDHrB8LtfJu3Te/A40R6FqDyMhB41ARDmIRKhpNgzmehihG1FEa2M+tEcVTImJyIZARyEZ14IpFj2xP91vwislapTbOQRb1Ev54AcuUAwqSzRbfoM5vIeo0DuEgv1qKn8/vLCVlx2+7e/p3VAYOIA3vAQfvCC8rZ1kLMbk5zkdGgE1fqMUzlb4AmoZUA8PE0Xanfk7wywcHU2znj6aatgbL2hzEJef9XFtQRSSQf21LxLu1gzR2BC0Bf39Z3uaEeL3+kg4qITFGPQSoi76LBnHYrUiXhzkk/eov7WQsxxohAShe8kNudo77S7nmchQPA1SQLi9wVn9pbtYyUqAnps93uUlB+ku5NphNxTowYuCh/IkyUr91VeXWfFCutcabVvl3631TN38nIBt6qSA4O8Cug2TiTT4YzySRsamQu9N3tjsS97PNntKpRX12nYyLagDvB1Q8euranb5rE+1KBLvd6bvGUAytVkUygpe3jbF00q8jyZWpwAEYbI9II/A+J8cH+ENWl9fOfyJZOpeAe0fY0TzmdOjAk4bmjANfDDT77df/LqHTKoAqueDCQMP5NBwQhbZ5q6Y95JJbzbU4gVuvqk4relGs/1O6Rhrg5OaGixJ0liUMW5TVy650x+zp8hvZxu4Uu7u3t5K63CDGhreS4a5QxmSGC+hVBKjx3F/eQ7jPc8xLXpqJ4TRympLljTl1uc9l8SXT4U5xhPrj6L6/LHcjZRfKuW/na8ZUlXhsfGDIM9xOF8nYG9FuewurGGF1qI7fesFt2z29EWU6zY7k0tdGgznjUQ7PJpRdDU4GB9ef9w8OBmfXK4EOByzU2n9l7DH4KEUG9K7ERYDO8liFFptpICFl5VZkL5hAEOHgR5C1VgwI2JAotOZxYJI6C0V9846UaBkCssgyripUe2VZGrHCxItXvi83GTEXGTcFazsDNzW4VfOpe0jLR7yQX4pdhI2FN2e3KWCTC31xdRlhr5rUWVyd8w5Sc/KbDt4A/k8pF7NdjKuuOOIqq+YKg4GHKeK0u/GqKOuCtOZPWUWDt6VIlS13tNZod+1DjGK2I9Ql4PL84i7Ctl5/WR4RdUZ8fMQq5VEgv6G3qLlSYyTeYjYS44tP8m7VgpGl0FC45HwehR7omsu5FHrkm1wqZQi9UjCTi1DPy43h/pKTITIGYR+pPxfrCncipTuexRp3kvJ2DpGxS88WMX7TX9KW60vg5TXbMM6FHTCpc7PUurI+5UFUXpukXJtk7doka9cmeXltktraKCrEE226rgpnrfyTF+WfvCZ/rn7lL8s7+f+Q96qfMbxLCYTkX579wcMabyK1q6Y3mRX7SKb0RU5B6t49KbzQLxnI8nlS8psaKmKYLyh+g3ffFfYqhoEbBECuw5SoD/eXZic6r2EEFpH1Y5N1CJN3Pywg0dgPQyR4x27fJJ7AekirEhyxfPxrLbkT0GGXRBsO0V7HL7jrf6HCuAJeZ+scsmBKFtGUt4v6Ldoxs6b/KNN8i3lOTaCj+7rX/65TAxRsL9XsSquhOrYEDrDS1MdXsbSBzhxgKxuuhli15NrwYs+rkLoQwJc1mUZe92ugdQD2C4HI+o0GVa7hRNbVr6luCOOuptFe9WH/5Py/6BR2mz9vcVHieOAyHU6sLbSwmr+vVyde/zELlxjWxlE+EC/HBPtLueay92i7uBXPRr+2AySmHVs8aPbQVGIJvTkdPy5cr2lQn1/3xQm+f9W1LOvOz/phTvd9VQsuV90uBecb/wdfwjEH"}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "7a5105ce9112a82584af36df85b5574b909514353391e3edd79df894248a6f55", "new": "ea363f8f311e4d8645be38967c755c0cf43d5611383e2bbd60111bc2621cfc45", "blob": "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"}]''')
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

# ── prerequisite: apply_display_fix.py
if "--rollback" not in args:
    _k = os.path.join(repo, "frontend", "src", "components", "calm", "CalmChart.jsx")
    if "END_LABELS_20261117" not in (open(_k, encoding="utf-8").read() if os.path.exists(_k) else ""):
        die("run apply_display_fix.py first — this script builds on it")


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
tmp = tempfile.mkdtemp(prefix="wide_screen_")
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
say("  Undo: python3 apply_wide_screen.py --rollback")
