#!/usr/bin/env python3
# =====================================================================
#  apply_tickers_under.py  —  fence TICKERS_UNDER_20261120   (on HERO_SPLIT_20261120)
#
#  Desktop Dashboard hero: the NIFTY / BANKNIFTY tickers move from above the
#  chart to under the Today / This week / This month cards, side by side.
#  There they drop the dash and the mini line and take the cards' 16 px inner
#  padding, so "NIFTY" starts where "Today" does. The chart takes their old
#  row (220 → 268 px). Clicking a ticker still draws / hides that index on the
#  chart; the chart footer still names the dashed index. Phone and the
#  pre-open hero are unchanged.
#
#   frontend/src/components/calm/CalmHero.jsx  IndexChips under=…, moved in the traded hero
#   frontend/uitest/test_ui_index.py           TC-IDX-0025, TC-IDX-0026 NEW
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_tickers_under.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "TICKERS_UNDER_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmHero.jsx", "orig": "3307eb9b4fa9dc38ad82bf4a73c71348f2d1458ae5c2ed548a3013d54afc674f", "new": "64e0843416dcf77c52ed40511e79df47f5e02de5787d0e45c26242bb18f18a6d", "blob": "eNq1W1tz20aWfvevaGOdMehQEClfQ4lWKZI81kayXJYy2ZRKZYEkSCECARYAStQiqJrKw1bmdWpqfsi+bE3VPu1P8S/Z75xuAI0LaSlOnJSIS19Od5/zne+cbqyvi3EY+LHjj9ajcLg+DKazwHf8OFof2t50fRd/3jphYP0ULcSnv/5DfPrHX/G/2Ns5eftxd+fw6ONGZ+NFt9t9qd48WF8Xp5eOiIOZCMYixuWeHV0OAjsc9cTxu30xcCfCn08HTihMen2J5sUgCK4eR+Lo9KjVFraYebbri8ghwYYOtRnZt64/ETeXdizcWEwd24/a4jQY2beCenQjceM4V9n1FGO6FLE9iCACqjj28FJEl8ENSeSG1GJw44uxO5mHDnr0RywqWoMUw0s7jC1x6F47wo1ELuTNpeNT53FojxxUoM7b4r09c2SDKBjeuJGzKW5c9D7AgzbX5jc8RhG5cSQGTuSO0HbcFtHU9jy8pLn1nWtcRfPp1BlZ1OL7S6xFD/PBovwsuxIRWsdoZu7wShOOWrceuFi+MBaJmEfOSWzHjkhpgafCCDEFsbFZlDh4t7f/Hx+/2//xJC9jraPagT9yFu/t+DIyNoUQEEOtuazwfuf0bW3R80Z3sSZtcfK+LT7s7B18f4LxYwhaB6RVuhTjaXzgh0UBa33sOU4s/74JwqmtCU3auEuLkzeXP0GhB8PAj2Lx7fHxdx/f7Rztiz7aPzz4y35PGDSBBtZq5/3+B9zyRBoi3VR18PTgeO8ENc7ODF5XFDZYu4zztjgzSLf4UaZo8jGrWf5c3p2fQ5Tx3B/GbuCLa9ubOyYWGh26waglkgdCuGNhPoxbInTieeiLziaeqWtZTPT7fSE7Fdsitujqo+/EYntbdESvVEr2ysX4Ui8XW5MwiCK+3XyQanJltmWS3mCVMgHbrLaFmKqnh9STnBj5Tgg5cz7mbIXMbCoRSyKFkw82tRZ4KVc2wiWKJuhWNqDm7OJR4vMQU2madEvtdNGEYaCmERmpcBZuDKuNXIxaPEq40Z9/FgYsz0gBAeMYtkWmP3Ei64I6SB9kMs6gYRFpx/mmmpfYmjn2FeYFWOZ55TkJxmOUzYqQYC2xRvfFYrTkALhdazaPLk2q9Fp0rOcQGgPKtfiMFug8JSD69Mu/HiVHsEwrDOb+iKq0rDg4DGBUsPYQAGkajr928M5opQAZj+AukJh26U4urQvMxZK2gZHloq3yDIQYkdZ1bAFPPGDdiOawA9CeVwvM/XKRzbytgVuZzLDFz+REXAy9gCop1XmUhDQvtJRTPODlRIEYK1rMB3AejSydCzkY6mle6imYAdBnATAZJoE5CEkx5ll38xl3NoKjqHU2v0tn3JPn+BM2KDV4Gjm/+ClwfRMQa7Q2dT2IzjrnaPv7GQxi144csyW+FpEVeS6MtUs3hkVV0qwTNletAcY69mjSXdCopGexR9JtTRzyR9SKhjvcgJSJRCKwKKD/7f6H448n7w8PThX0b3SyVwoVBBYdXZA7Khx8TxD4ey7+gAt4NlTSgwTS60bsJakTOwyDm0iYu3LhP/3zf2m2n3banRcb6OYXcUzr9Omf/02Pu+2nr9pPnz/lF2/IiElfs0ov2xvPXkEb4ZpmC2F2n0MC4hPkSFtt6szFIjj2iASycxSEsyfhIumOxdCegVwosWgmM4lBSmiEszAYgwesC4/seRh4wTzkxo1xJpBBZSehc2uJfeIeNL/CxqLaYQSZvNucTVza6EP6CUv8IDmMpC9XjjMrgLplKWfl+uHOgNTIvG6J/mtxUVZNzQivWyvUdLNwBsTwTmaeG5sJeQJJXIgKgjKIVIKb0l73Px30nL3bFpjhHma7MG6sMjveMYZwgtI9HRW4+hNCuedYJM+JgbknaAmSwdI6VveZM4U7jZ1FfBqC4Y1BAHpkiVDkIYzBaPNsQ7F2rekccN7mfn5wMONxT7zodMinZ6LwAt5BmJfPW3q13xPq6o4jLBlrIlzQYkNCHkZ3hZvd7GY+6wkGv7a47qllR33Mjxt7GMvF7l2R8lGS104vsKIaHNaFIVRUohzLSxJkXhVkrglyfA8gzWWZl2X5Qz2qGhpbphxaDh24JzsFvwjnjj5Acq/FEHOx6XGTd71gSF7yr9cgwU7Z5dbloHnIBagVV1OX+YGHcuTS3Yg//Uk8lJ5B4Tu1pcG9yaJujdxrxDyxvRY7UeyO+gbR8zWKJ9Yy3DFEFN96Tj9JxMiNEJhBQgPsfAF56WfPDR1GEVLjwJtPSWEmNjTm5L21iDSDjeYDkaav1SQlJXlJUyCzmc8gwJR81q0EziFM2XUIJz1nHEOQa8RP4SaD8U1IsBoSiZnN2b9wmcEtOx9V9DF5mxE6UpGe3k8wjzk6W4ih584iGZYFEuipcw4go5gt2ZbezBy7YYRiQah4o81itDjEjISPnvMeeJLzOQwwqDFUh1UB/tg3tDn5zIoQQn9uOX4IaeoNkgZPAEoT/yB2phGeDQCgJD2eAxf/rC3RFKTX9Q8xbT2xZm4AFPFiOoKJPYXTLLS4gFHCzjbPxVuFvTAQBtSvxat0trgojSpf7ak9M80Ze62kZCzSzKEqFAlYZAcwWoXxFO9Y8xk/wA+pEkHJZqmBkl4X/7aime3Dkd72k5nljlI5tyRM9oDNi274Im2aXtenga6pWV42p6zzz9vw64gzyKmR0fqBXIkaMuhTnk32BoIwcDQ4w9rzQRBCi+Xjiy6oTRR47ghYumtR92l1vvXRqxHBMaevMc6rdGudnteL4+V1hr9kjbK6Hbr2mtTVvkHYpKmgZVnsZHMrpxVMuRteQ3jDh2rtDDA0dgPgcUYmQvp5GQZFb7qrf9mBM0L3pxaoZt69pjqENro81+hzUOuwaSpaumalraLK1jqs83XzbV4sIdgtZk+fqlNr6sR2lcKQfHel7KVpy/uvsvXliRrx7uDN6Y8gmN/uvPtOXg8vCfPsAYCJIY+TX5jJEPFGW3wlw/NbgJETsvOhfpiutHN3xJAIujt1fdeGGYLHngKTKV/HEBzaN4Sq8JmXrkq0HZ0eKSQNnalDsYIzAsF1FpznkWggh7F7fHj8gUkcywsF+rfnb3Ze7T2FyeWjoKd73+w8e9bVkjrHf9n/cLjzI6W4UN+IgKQza5QlIy2X8lzHmGQYuZHVGU/j94uCWl8voc9tiEOjnc6nb0Kb3d+eO0FU1yNjndqLxjfssQvWfYQGwLlmNrHtWehcE9Eq0+0Zk0eTipAtnZ23dAidnXXPc/o0K5zpFrpiy0ObNQKgWHqQcVgMw5SdQ0PRCCjPpZu/tBfVl8SOUWCNmkAn3aLN22LeNuAGui/gR0wuiofrIixKjgjlM3eAxl0Zx4BiEVAcMkwcGemjxDRd1NTHtia6rRYafrlBoc0bd+GMEBWn7UfJLfrWH1209HC2ynyi64lkBH3j5YYhLhlW+sZGxxDXrnPzbbDoGx2Qkpcbgp6tAkHyCyeXUA+Quo6Gw1us4IsuGjLEYkN2dNvtQ1JemVTcbmg3URwGV2hQIbq6p+Q5INa+hWwC9dfz1lkpRv0EPmzsel7fINZh5M3UHWbW5A9y2F3red4ccOV60gQlpwe73+1/OPn4PWzxQy3255i/JzPnTnRFOX+VG+eU9GweRwpTCGOKDIFK28u9gsbMPQjfCMSE0+RgcvTb5nwByJrK1EeXRL/omiyR0UQm8jn5ThyPkIraeUy6CJ/p+j4EUC5WUTw35GiTq0dkf1ntrKqZgyKQbhwExPbAzDyPmvftqRPl8mDcDCqSl4IYgBjjGY2eY3kCQUDtcAhqTayT+mwVeMCJ912aKqAC+MGVQwzTJ5ydTDwnj8jbkozChsa2RzgsJ1bdMoA05O0P999kyZvuq+wVN9RjwrzGvEaQNq5xXoyWsNUYMYQBFN9A6EWBHVsGp3b6xgltsMDp8SxQ5iWfO6OB1HKpNdYN9Rp8a0iqy+OhwJEuCA3oYozZGmVedjkJzkKPaNpEiH+aQ4Dx7S5vecW9PJEBJJOTiou8e6q+xo8ZkfjW8Skyh8axFfUoJAZIqhuj2+l8pRP6pNhkkWB3VeG+Eg8p/y0XfNs6uzonIZK0YCEE71PLi2einwXHFVAvkejLCTVnEaxgFKaquqYeERRnV08EBBa9xmZgCn3ZliJjEIpuX/flfoVeFqqOwoEPnzr05oACjLMo00DNtwbzOA4UMb8CCb+dQaPkQ6VSkDCKHOAbGle0nRWGywf+rucOr/qJyfMpDQSdlumdovYk3ba4eEtY8ohqM6XJVZOT4nvgKPzyMSEQqJDiKrfBPMzJykW5+ftFCmT0UGaloJmOvRAlZc3dCKtYFlo9fQHbn4cRkUZjBp8mGyI6zH3CUhEYVmMMjXAWcVv3qUY/0VUWWXywR+4cYspdO2vmUuZBISXHdhwpwqtvILij3I6SH4+9ichCFfn6oirJwB5eTThX1hNyMYieh2MYO9lVTJk+hGSYICOTpxLlZLUUdpLwMuyh8pg1e0QRtVpo14+cWHT4vy5Lm1VMaa2Vn4SRkoGuP8mw8GT/cH/39OPO7u7+u9MacYa6TB2F3RT4XjkVkIe3EE/WyxqSPJTzdKdY6kZiSPdZNgenARTlYgMjUM4lmweNGBNa5JEER4Fw6WUhlkVQnCxFaHTFgdGDpXFjkodZXJwZssSUVmMkmRBK1KLIWmO1zG2umHXmwh1n4EPI/LWMJH/9u5EWiW8UKAjgRiv9qjHITB5mmE85smJ9iI0zF+8ngEf8pszJ+Q6/KcQiUlWdX8wdo1YxBznyZaHj0jjtw/7O3o+I097tH9Y8s48okliK4lO3jsr+EdtSjOlxpLJZGWvCA9r7zdkJn3cwObbUNxtUurio1xYOEQeOPTcbA0+NpbznfdqMpsRBDNKxZLOADzz0xZlBm/C0S87b78a5BVc+Ms0BY7cpm4DjG5xvlzaJZS6dU4O4KpfTN5fzYnks9JB6XhL5EG+STcndz7smReX+dImuUP1+Qn/T30hIdKpwds9TBpJQnLmjttxZO2+kFtcYbn4GwR19xi3T0NknF6kyHrZ8oPwpvEGMbmknUi2Bca5vv0fVrfa8NDle43zprvsy/1qwrWLfiWhL93lHTqn+okvIQNBvz+NgmT/R3UeD++NAoeb/oqn0fezzGnKcNdwsVGHgBUNa2ZVJoISXMRX/9z+itkXfBLR3620QjG4bsLaWOqNzOhS9K4w/8EPcpcXa2bNZGCxopYUhPv3tV0Zgo0GwSpbsc3CYAwvh1Kk9MSWCKO1cnkSrjai+m9h5wbuJTXynpg94V1eHZ7oOdIRiQdnSE6qp4fNIVPIKkYo992KRDyw7Q6fBJfkX/PjBDdwO7TRPHcqJkA/knY7sRml1HunBJJ1DDnRluCD6apPI5khZOd1aBLjK0XDNAh2lu2kGbQ/zm8F2CY15n/x1aePzLDtOBC4mXcY5Ws1OpZnZWSK9Agi9LI6LUmEOebSSFBOj3LFfKiVDAcxxeJsftiD0+/eT43dw6GHkmB6l8k7iILQnjgXPRtzc1NKELYKmx2cG5xSN88etzUwPdygFY7kR/5KZbKPtnsiLbooUS0fH8pKsSuldqskvoxVKlslokIdimoGSvzhXFejxFHoM8jVYcNkFH8vCKwgCywjaAu5Mjb801Kg+1Lacl4jTmoiITb/V0gaR5s5zsyR7IPOlkQz3MnHkOLQIdltGn7SjJ5/imaVnMfNtv26LQUGPjmEnV7QPeqUcG18pIy5TX5k27QmtD05WUk+lp9Rz2tLGITNSWdSNud0qsi/qIZgf/1K02U8CP1UL10/kb5qZZz9RF8QNtWha9kRplG8lEZLGVSwqaTytK/3S+TZ+fdZhB1luRB4gzVvQ+dOAlSDrpVWvy/sRffHwITdSJkK5cNsZJ8qenNcGQsd6IAFpfvVMY49SAkVumVNpubC8sorrSXbCqzvIV1fzdefZ2jFAMrpIjaGVdrEeVJHP9mVyttUpAdaO8iseFJxaxvbO5WmBltBOj1Bk1avMITXJyqIP/YRcUOnky6lFFU7oZKK81KbWludzqikzPPfcKC4nzSQ6GvdmkIUk2S6jaKKV6mjr3dhiQLDjylOYcnHvnsZhhljK4+SDVgPOgmVpTTV6WUnq5I7DBG1dwgw/R/6WMkdKqWgZlmfP5KGjHcrY0LEZZ0y5iPsnXfRUBybm/pkOVWl1ooML3S3PsZrh6iRhZf6jMswlzLiYZJkP6omcK5dp2O/NnIuOWbmt0OFUsqkiKaIxPxitymPihsaRQXggmfcfRrIbNucTdjrlxktMXIvZyqRcf5GJzNkQiqgerN7cvme2IgunC++SPGiAgq1IngAqwZo8PL/kLIseQOf7Vnc8ZaThnjpvRKMHBf38CRgtoa9QtaSxWSZzsVh49VM5S0HH6AJ3GHBIELp59qIzW1S3Ce5/egry1LKUq6g8n3uSrWyq40u+41U+OSklKZMsoEiXjBUanDm/JsP79Mu/OtVTETMtcOdDLjx6NgXPsUflA2H67snzjU7FUN4FWUYhz39ZiQyWgH8AHL7mfU2MY6Lui7P/t8KNeFH47FjoGKkMtfggPILXJIu2ZGvqLs2DsOxEY/6q4bMC3kxw7NBzaWPQiSI6AZl1oJ0VmWlTpOXQFO8iUkm/zaSyWK/fma/KPa+ctWqKkRH9ygG9reITHKZ5/eTsPM2jgn5eLVWxbT+Rv2m2w65BNW0kwxW/6jQPeum5m4RD1uw1QE5akCygzkQWZJRsokTFvsiOyzTiaae0/Ve8zPcrUejFxv2Nf4kj1Xld1XITLa2dHUEyy1a9dN+WsP1uBLR6rK+B41c2HzSGOFi90Vdm7418ELfopnqIq7Ki3btQuyqPa+J6vy03OB017YGVqWEtULkvTWxoYDVlrFW4K32s0rb7bJpV98O+mOfpoeKSY4xNGcMl9EznW9UgsV3b9SLyVS3VTBlrJAuwtOwYYf68jE01E6zzGHkSIT9pvfBWbi/IDl5Xk/137LC8ea1C0ZVLVd5zK5Ig9HkfZxbJh6mUr3RAvHCveROqceNOvk/07wTVp0eAbtqiV3e9xk8JjXzbrE2bZAzx+g4YPWvezowtmfrO+brcBjGiYOrAvUzk5w58XnLEn9HxoSLP5nMRjs/e2Xj96W+/Fly97GtrDm71MXD5LZKxmqOxTuNpq/k8sq7TVCpddbRVO9Oq7502JQLK8L4EwDkP1Sq2k47sK9olKmybC4BWlD8mu0iXzQiXN5ozBDrqLgHWTjnLX8rz/9a0wB8a7dZm6g6BK7cIphT4o/onGZXYUoKcbDtP8rXSO4WTBS3TNKjZaLfyz84EmB84H2lZn3VtSW5ViMLzrPgk8cn6g+K7G8Qjd//CpRywmHqyj3wIp/ooiGktj2KevZLHKvLv9vK0Yu0j65Sigpy/6bZFCcTGF1/E/+Vhh4Ltl1356qOemSuvpAcqh2q/JA+QFMzvD8oF3DniT8jQm4i1vi73CSa++eYbCidefVFaoHp2Ue75rTynmLnradknJ1nVlRyA1VjGWnLVm/VGZiTYHN/kxtj9JntNn2CqT7R6RVKCztKiD/k9KX1+Lo8/0ZZojBsZLJZTFcXcys2b36DE6pByTzuKrJCJD/tuFocCsdJX8mQvfHngjXgY5gY19V9/FxsvXonZolWWT84pOUmOjC3iB6bpsevzeAtK3/XigtUge1vu3/Yawmz++bJIG1KvjrQ1zC5F1bxR//903guQ"}, {"path": "frontend/uitest/test_ui_index.py", "orig": "87c452ab6115054be3528e1e1389808ad00c37b8fa0bdb1850bcc0f10f0bb0cb", "new": "ba2247a3fcfe1a17b74baf2de1090035d4748d860f4f58adce99e9d2604c023c", "blob": "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"}]''')
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

# ── prerequisite: HERO_SPLIT_20261120 (the hero this patches) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "src", "components", "calm", "CalmHero.jsx")
    if not os.path.exists(_pf) or "HERO_SPLIT_20261120" not in open(_pf, encoding="utf-8").read():
        die("frontend/src/components/calm/CalmHero.jsx lacks HERO_SPLIT_20261120 — run apply_hero_split.py / pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="tickers_under_")
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
say("  Undo: python3 apply_tickers_under.py --rollback")
