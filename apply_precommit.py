#!/usr/bin/env python3
# =====================================================================
#  apply_precommit.py  —  fence PRECOMMIT_20261117 (TD-042)
#  REQUIRES apply_repo_map.py (REPO_MAP_20261117), committed or not.
#  REPLACES apply_repo_map_hook.py (REPO_MAP_HOOK_20261117): if that one was
#  applied, its .githooks/pre-commit, its test and its CLAUDE.md line are
#  retired and core.hooksPath is unset, because `pre-commit install` refuses
#  to run while core.hooksPath is set. Its REPO_MAP refresh lives on as a hook.
#
#   .pre-commit-config.yaml                  NEW  ruff-check (pinned) + 4 local hooks
#   ruff.toml                                NEW  errors only: E9, F63, F7, F82
#   scripts/hooks/check_forbidden_files.py   NEW  no .bak-* / .staging-* / __pycache__ / .DS_Store
#   scripts/hooks/check_dual_tree.py         NEW  backend/app + frontend == desktop/src-tauri copies
#   scripts/hooks/repo_map_refresh.py        NEW  stale REPO_MAP.md -> refreshed into the commit
#   scripts/hooks/market_hours_push.py       NEW  pre-push: no push Mon-Fri 09:00-15:30 IST
#   scripts/test_precommit_hooks.py          NEW  TC-HOOKS-0001…0028
#   .gitignore                               *.bak-* and *.staging-* appended
#   CLAUDE.md                                one line appended
#   REPO_MAP.md                              regenerated
#  Nothing under backend/ or frontend/ changes: no build, no restart.
#
#     cd ~/dev/scalp-app && python3 apply_precommit.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then, once per clone:
#     pip3 install pre-commit
#     pre-commit install --hook-type pre-commit --hook-type pre-push
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "PRECOMMIT_20261117"
PARENT = "REPO_MAP_20261117"
HOOKFENCE = "REPO_MAP_HOOK_20261117"
P = json.loads(r"""{"items": [{"path": ".pre-commit-config.yaml", "new": "4ecd2f7a00b12b626b3498d156be00e5797a1e34b06e5bb1006b2fc97b91cf0d", "blob": "eNqdVN1u0zAUvs9THG03HZqTtAMmIoo0bUNMo1q1jis0LDdxGquJbdnOIBIXe4g94Z6E46T/MFGIKrXx53O+H5/6EEJtOElVVQmHXzIXs7BhVQnPj0/w/PSIHxjfXp7fjEZXd3QQD972+/3TJdK7uyDx68FRcAhX0jpWlqBkykFzA2mpJE8QAdBCg1jga7oOWr2udhBSKDUnrtF8E95d1rUtsMVtLWHaQMFklmy1MwgQgg1JLkpuoX16do5aUBj4ZglMrq/Gw/ci+wAzLFlUhmF4FFRCiqquKHak3Tp94MYKJRM4OAkHYXwQZDxndenoQjn1PakXaBP4upZyvNJ7vyrBgtnOtvsgMFwrmwQABPzPBArntE2iCNUV9TTEjRGzzrCS2CIydZ6TjTy9QcMfEniIw/5peNIueE1tS/8QEJhSW5cWPJ3DxnOI6z4nIdsNoVNVmQA3RhmLiZXNMUiFiebiR7ChsFQpK19ikopMWTqvdXcGwZJLsop7FMIpm5NXEEHoAxFy1r5QqpuUoUJKPXQxoROnDF+Vc+lMk4BuXKEk2NQI7WzU8ketL5orMxVZxiVtiUPdrIpLJmc1hr+sXwGaWdtu9+rwaJypebBlJ6sxeGc4HiYzwjU7fjwMHoaed81lFjGtjyE3Sjp8g+EQMm7nTunImpQ4VhuBQ6cFt0f/YM7zUM+zl602gAS+bWqKfi41RWtiVn5njaX4v+m8b44GK60Cy3E8ulmz4Arm2rFASyV3HLZP+O9h+vEhFdPE8Nxw/C9vh3l7Ob6ho7NxWGWQ1sZgLHtF5NtSbEsXbffKaNf6izZyDGKN4oUwVZb/yV3FzJw7vLJqY7ur6rfZ96swUpJ8xCmI3yVxTPpvkpMYriZ30Jucn30e0/GXySd6cz3sg0IyI7J9J6Xjpy0/9Ux75bB1K3UX1n9G9AuG7QKH", "mode": 420}, {"path": "ruff.toml", "new": "1623676135ec389fb932c32a0845805639c9eacc91ff9d0179e29d6c5d87fd23", "blob": "eNptU0tu2zAQ3esUA3lRG7AkS0njftBNGwfIIk0RZBcYMU2NLMIUKQzJ2M4qh8gZerCcpEM7LlyggkBQHM6bN2+eBkChaXJvOw1vL6/w9vrCL/y6m/24vbm5vn+sJtVFWZbTY2R4f5lNzqtRMoAZkSUH1ujdF3A748UW8HAmTA1GdOjAt8LDxgZdQyOUBv6iYLzqMIc7Lg2TvJx+cAxnNwZqbETQHhx6UA66IFvYqBoJhuVkXFXn0CofSwIz+mmfoBPKjMbgLCwcapR+wXkMhtteK6l8zrd8q8wKWiQEwsZSJxhC2hq/MjuE1to1GHziIszMQZY1apsnXtAKfcbHTnG9b5D2u7NykiaMIDHDrdShRj73FBBOngEjGhsIjlc4ARqlWYyeMJO265SHXjjHJ8vdXqcEtx5NfYL6kOZSC96nY0hrdGtv+8KRzLwIpIqlkGtO+H+wIWsiXDpPkgetjJ8nB3EibgKQzj5zYmT6PrUCrm/fRxfDVxdnMT4AZZ6EVjWL1fWClLMsz5DnslG+BQFaeSShx+yc36N94vRfXGX2Cm+Yzwp6LSTCkNAHMmCDdzxYRmmCkZ4lPoH5VB3qB8OGUAaPZhpypCx4qeJyNkqODeY9UhY1ztTKWEI3Zw98F9w1Z7M9OBsED71FuX78i/q4R817tq+lvhWxkMaVkDvobB00Z6qut+SxZrg4qoOXxlBOYHvCbrFmJRYwvJxd8c/xcZTDJevtcd99pDWOuygGixcp5Un6PsFC9H2xJLtmnxXPSLZuBTNK9xaI7abz5A+w4TTo", "mode": 420}, {"path": "scripts/hooks/check_forbidden_files.py", "new": "15a71e75a654e463a509726622685e83152496fa67a43f947244548dfd6eb92f", "blob": "eNptVFFv0zAQfs+vOLKXeGrSdSCQCkNCUBASA0TH0zaCk1wa08S2bHdbNCbxI/iF/BLOTtoVMasPbu6+7+6+u/PBo+nGmmkh5BTlFejeNUo+jg7AlkZoZ6eNUms7LRss13mtTCGqCmVeixZtpnuAP79+w5/fv+gHn78sXn86PX1/lh8fHT+dzWbPtpbk7E169OSYRQfErA2mpeo64cCTz8FgvbEIXOu2T4e4UPByvdEWuKyg42UjJMKPjVyDkMBhgGdEBnCYFXydvni7+Ph68RL8eZAocQ32YB3vQUmohF1PQCrn+VbCsZGK7CshV+khDKfhbZ1eG+EcShiNFF0LtAGR57ovKT3M8+mAKHqHpaowmLM3y3zplEHYHqrl0xLeClmhgaAiOb5DZ4HyCxGwAs1dY6E2qtsT6zndhSRHvEJDRdQ1ysqn4729FEtXtaKg6to+g8UNyXsEJ1C2yOUEZnS1qkPXeIhTsJEhWBaJTivjQNntzeD2ZnsbRV++flgsCX0e+fwTgxnloynzxMQXQfuYTSB+QHRIgvmQkcMD4OTbz4uMbRUPJHsK94TedSMwXEZRVGEN102f+JrZPJBqbkiUk0EGg7rlJSbxxUVMfNOYZVa3wiX+GtwLTqN2MqDO09ll+ChqiPdaGfupGDzm5ALKjLCTf92GBPwx6DZGQrztPiR7fizeBtmx7AbjAY5hRO49ggOtHpibCbS8wNanF9pyDyZyc5NZ5KZsEh+G3dv2yAM82vvwUUkcde24kEFYO2ILXvm+J3oyaM5YSEMP6vgRFWM32E5Gv1KE+6+qo6FXfoKTOj5rkIS4bVFSrhW7C5uQWAbdxrqBA8cld1jNx9aF4JSLj/9PjC0twK2+G56k2+u7ETUY46/DvPs16+bgd55Ss2E303Tcuxe+Kv+G/P9YPIeMIGIlPWCNqMPCdqA2tDo1fPd8vKogffWdjYHHymckLwmT55J3NAzjEHmxdxNEe5YhLWwSWuD/cbO6Op/NLxmL/gLPob/p", "mode": 493}, {"path": "scripts/hooks/check_dual_tree.py", "new": "ce4fef7e5ef8f0963e576e99d2d3940fc756b71691387f8164df8b0fe502a8e9", "blob": "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", "mode": 493}, {"path": "scripts/hooks/market_hours_push.py", "new": "4f3db3602388722c33c568f2be319ad446891f3f0d44c1f87e02451b9e3b1722", "blob": "eNqFVNtu20YQfedXTGgYWSIyRdp1ixBmgMBxYCOxKVQKCvSFWIlDcyFql9hdWpGDAv6H5gv9JZ3lRbbTFBX0QHLPnJk5Z3YOXk1bo6dLIaco76DZ2UrJE+8AzEqLxppppdTaTDdcr9HmlWq1yZvWVGGzA3h8+A6P3x/oD7PfL86z6+urRX4cHf8ax/Fv4wlbfDiKfjkOvANibTQeuXBwtAlIBd3btZKPD39/1AKit0kU0XN8mpxEcDVfAOM9xirYcCFhxSUYy7UFToQaa+QGYdmKugBbcQulFigL8xp40xhoxGoNbQMbURwZNEYoGYSQ3aHWokAolQYl0ZVGSRKA+fn7z7N89mV+mWef0hhuhe2OCOGqEQa+LM7fRF15vK5hh1wDo0Y+zBfBBIxyTVmxwaN74oWCW750BVKgRCywCInpZn5BCtSi4DsDXCPFWFhLtZVQocaESgI+IqClcFshqKFox9CXeZlln/Kb7I/07GqedVlhK2wFqiwN2ndQ8jWaLnZVK9KBWTTWEHu9CzoWW9Ri2b2HcPGVWo0g7eWm3tQWiwnE9GXpol3pYtMoUp66wi4bN1DY8asy45PZGc9zcqV0HDqkE4MNzwXWlrNultLTCTkjW6orPYlIP5+i/MDLZhc3Ezj/nJFQKbC3E3CHLCY4wWD/O4Clon4HChByVbdG3KHneQWWJOs2F8ayIPEc2slBdMqENOtCKxneomX+Sy0pu8OKsoP3ge5n+2bG1sNSq40wigZowy1z2FBjU/MVMv9Pnzp5E0U0y34Q7CmIkxS4F7JUbh5uSJMn/jGH3dP0yJQUeaLQaFstCcTNXtY9YDh8XiUpwJ5ZELZ2FfwrthdrMJnZQa19qi3imuaQBXAGp8BlAc4eOCNjbOhsnBCotyBwXzvbBlJ3ZUf5XXd7R0aR3eT/mPlZ9mjE/dy04Z76AaQp+LH/FN9oIckWv99c0E0bsG82OeRweJkcXv/l7nPQ7TA38ELeUmu7Ld8lP+4AP/h5VWOKmbsvQw8J0CUib4dEBRwun6Wb0IQat3ZeVvUf6y8Ih8x9oj4PLy1q6M4nQMvLLTBbCXep8f92mP9iTGKyiITNc8k3mOedgnnuDMvzQUi6xyHSWmC9jYH3D2qt6/E=", "mode": 493}, {"path": "scripts/hooks/repo_map_refresh.py", "new": "9287c4201fc6731337e801f20d216df13445289491dcbe3e1072dc732119be78", "blob": "eNqNVM1u2zAMvvspOPdiA42TtMM2BMih2IqtGNoEQYEdhkFQbCbWakuCJDfNrQ/RJ+yTlPJfvS5dZwSQIpH8vo8iefRuXFkzXgs5RnkLeu9yJU+DI7CpEdrZca7UjR0b1IqVXDODG4M2T/Qe4PH+AR4f7ukHy9X558Xl5cU1O5mcfJhOpx+7m+j6y2hy+mnsl/cncXBEsbXBUarKUjjw4WcgNrA6Xy7Y5dkyKTMQFqzjBR6DwS1KNNwhkDGXGfAs81unwOXCUrAmUAJXeIsG1oVKbyzw9hiUJGsLaieBp6mqpIOIw44bKeSWbot9nFCQaIW64ClaSLbCNZoHLDdGlT1D9m2x+N7rTOJAlFoZgrLdzlZrbRRFez7Z2yAIMtxAyYWM4lkA9BmlHMwH5ompZPQzJArhMYQGb0eaG4v+z2hkc7UbOaULElqEv44h5dpVBpmqnK7c/NpUlDGHd802TqzL6IoWeskoHiLWizJEOdmiS3dZe03Jpls61dzlyW9FVL0pwbfl4JmQEevqgeogbFzpCaUP2vrinbDORmTbaq3RkfhKmDRU0gPSKU/kimnl+Nq/P/nX2tMc05ta8y6bN5QOqI+TBiFVGXakPMwcJq+Q6A2mA4P/4/UmmT4g/8cbUzn7ZVD9b6usWQ+k1gIBC4tgetCDNnUDvTx9Vu4/bYR00ZAQdUvbj3XHtxMAs64dadd2Y9t04bN0T+pg/B9nq6uLq68zcqmKrC6dNvAfk6AGrOSsG0z9WDpchH+970u0V9xhw0VBOqK23z2depJgFncc2qljEWGXd4A9WBBQwhmTvETGfFpDxnyrMxY2+psKEi5qBkAcPAF0RsTg", "mode": 493}, {"path": "scripts/test_precommit_hooks.py", "new": "bfcd04269c3a2c8d34a0285043840b551217ef657c9aaeaebcb75710a28b3c2a", "blob": "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", "mode": 420}], "retire": [{"path": ".githooks/pre-commit", "sha": "68483d08e161d93b22f2900ed8dd4a485a45fe90789cfbe7251c07639c6fac90", "blob": "eNp1VNFO2zAUfc9X3IUKtdKStCBtUxHbkIY2hAYITdrDNFWufdNYuLZnu4UIJvERfCFfsus0tGGFqlWT+Picc8/1zc6bYip14atkB/KZDJUxV76wDjNu5nMZAB7vH+Dx4Z6+cHl8cT75fnQx+XZ+fjrZG+69G41G759W+z++ZMP9D4Nkh7hOEa1fb8jnAvjCOdRhDEYDLtHVsFJ4C7KEUCHMmQXpwQemEEhZeuJxOEONjgUUwDT9hKCrYJodns2xZcnhJICOvDBVhl95YO1K1JMhcplrDYxzs9AB+gyumdNSz2hd1YMcjjWbKuK2RMGV0QjTGpi1qp44tGZC9iYxndzWYyKLH8qLRHQpZ/TnMG/Cu2Ch2kRJyCOIV2SYBdIkY8ohEzVI3cCKrch9kEqBW2gPpXSeEoqVr2qgAoXkEUPPfJ4EYw97/WjE4TKzzHmELPOVuc5oSVEgCvY+FgKXhV4oNYC7O8Abgg8TLiDtESjtPEtm4gU6um+8GZ0J6Z7xJdS8X5BpopqJFH7D7m68v2nut0ojwEHsnE7gNUDa+7wx1PuUlJJMoT703EkbfEHX635QKxISKyMX6qi+qcTWVEgkjdllS7A1NUTvR8TW0yaVW/KEvDKQRvqM6MegTYuAkk6NaGahe6a1oQNQIb9CkR60ygfwN0nSnq3TJ1tZ1mBgExtFuDtKHD+kApsA057jBMQ/MOpEJGNlHaL/CGLWsVU0E11X6+3b9XS9x6O4mrVYlcPSoa+2p4wmsm1NzEd5fJH459Hl2cnZV4Iu1CqWlvCZZCO00ON1M5qyiJCajKobBE3fsBPEa3Ltu8E4KJmMw9tvRyg6aN4DKAavyUbR1Vn5B0Hto3A=", "mode": 493}, {"path": "scripts/test_repo_map_hook.py", "sha": "6daf6873c0bb8a4585c919b29ef0ab7ac5dc47137513c373c62355241510d2a8", "blob": "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", "mode": 420}], "secs": {"own": {"blob": "eNodjE0KwjAUBvc9xedOwUiiqPgLUhRFS4PVda31FYu1KS/Nwp138IaexOJuBobx5i0hoI9rPwyC3Snuy/5IKTWGEEtPwDfPZ15bsCtRMYn072izyzIQs2Hbxc0lhaiZCFXCef3qojS4JunDVcjygprkuNZhHKw0mDIme+/MUDl7J4uECdfCpA+6ITDl9/3ZcA45mUrZsBpOBxK76ARXNiOLS+SvDjrW52gbh/uFuvS8H4J3P0A=", "sha": "fb063c5bc04a3e79af75923effaa3798a0375f2cfa3d65eb6b59e4489572e0bd"}, "hook": {"blob": "eNo9jUEKwjAURPc9xbhTMNF2oRsRXAiKSIsXaGr9SUPbVPJTobc3KLoaBua9SXYzIXA7Fnl5PRTlKc8vZbbONmmabiHEPhFQ0tjQDEPLq6cnUQ99b4OCJ+2JG2KoHy77h8LgQC/yE75DzFXEY3Hamhie5MdVVKHB36wWEmcNJUTdUN0qcDUxOFQdLeOTIUe+CoQ76WhA8CMH6ww66whu7O/kWSZvTVtFBw==", "sha": "77bf18ade4e543127a1603fc8829d0ba432800ca8b59e372290b81ed57ef651e"}, "parent": {"blob": "eNplU8tu2zAQvPsrpsnFQS3ZTtAWdYIAbu2gPdgxklx6MmlxZROmSIKkkurWf+gf9ku6VNIiRXVakfuYnRkOrt4UBe6Wm9vtar7Znk/O30+n0w8oiuvB6SnukwwJBwo0+5tUNmowuPVkIV4dCdQ6xFRiQwExBZlo32G461A5RVY2dAadkIOIdCCQ3WtLI+xkdUwUE0JrLYURFNWyNSlijHtKSdt9Dj+9pBW1Cw0MlyJIu6c4gpeWDKRV6PvEVqf+2LTPk1QrDVIgHhYPMpBC41RrGEeuqclWFEvM68TIpVI8EC5wzmOOap0TGRuE79LB2QvEKmif4nhPdhvIu20jfek7gaEoiupA1VEgyi5C13llHZkPaeisxAPji7P/O3H77TNs7oPN/OELN6ulNm3g4W95paaRoYOzpjsrB1mZu7zAYFBg+Uh8Ux0yGbyf9rxWxO16iUimLipnk2S21AhaUeNdIpsgpPem215lOa4z9hkUK/bCBsR6vlpuv/G3Wi0WYgRR7uSxuLpZrj8vr0WvWeuZY144OGPyvyixIKMZTU/682qQxrFSw5NMYEb/6u6ENymw+CPODKSYLZF7kVVj0asj6sAL9P+X//aNDk8hU9YfC0XxmJwfx1AVSbZBc4Ndq41i/3nNWU8HdiyndqDvmo3Ks8WeB7I2qY2C0YF6Ktnye0rYETuN2FypOmQjDCVbYCWynJKLnEfUe3t2yWxH4keSUXh2F7Pbk5gH3JFU/YWRvTVFw1IwnTZP4jbZXAiUcXJVcA0aalzocu3aIbPJz8Ebx2ZaOfvrx8+boDH5OJtMOJ6+m11M8PX+oRz8BjwkS+M=", "sha": "cc6a9248d8c30dc081b11360df394155e81b7dd991ec35ae74f4d1019cbf65ae"}, "gi": {"blob": "eNrjUlZ4NKUBiBQCglyd/X19PUPijQyMzAwNDc1hMokFBTmVusXJRZkFJQpJicnZpQXFCol5KQrFJYnpmXnpCsn5BZmpxSBupUJ+aYlCfppCemYJl5ZeUmK2rhaQhirU1eICAA73J2Y=", "sha": "62212a6159f97247a3b81898532978f74faafe3465e2e198dc7f62c243c6d5a9"}}}""")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def say(m):
    print(m, flush=True)


def die(m):
    say(f"\n✗ ABORTED — {m}\n  Nothing was changed.")
    sys.exit(1)


def rd(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


def wr(p, b, mode=None):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    t = p + ".tmp-" + FENCE
    with open(t, "wb") as f:
        f.write(b)
    if mode:
        os.chmod(t, mode)
    os.replace(t, p)


def unz(s):
    return zlib.decompress(base64.b64decode(s))


args = sys.argv[1:]
repo = os.path.abspath(os.path.expanduser(args[args.index("--repo") + 1])) if "--repo" in args else os.getcwd()
if not os.path.isdir(os.path.join(repo, "backend", "app")):
    alt = os.path.expanduser("~/dev/scalp-app")
    if os.path.isdir(os.path.join(alt, "backend", "app")):
        repo = alt
    else:
        die(f"{repo} is not the scalp-app repo (no backend/app) — pass --repo PATH")
say(f"repo: {repo}\nfence: {FENCE}")


def git(*a):
    r = subprocess.run(["git", *a], cwd=repo, capture_output=True, timeout=60)
    return r.returncode, r.stdout


rc, gd = git("rev-parse", "--git-common-dir")
if rc != 0:
    die("not a git checkout")
gd = gd.decode().strip()
gd = gd if os.path.isabs(gd) else os.path.join(repo, gd)
RETIRED = os.path.join(gd, "retired-" + HOOKFENCE + ".by-" + FENCE)     # holds the old core.hooksPath


def hooks_path():
    rc, v = git("config", "--local", "--get", "core.hooksPath")
    return v.decode().strip() if rc == 0 else ""


def set_hooks_path(v):
    if v:
        git("config", "--local", "core.hooksPath", v)
    else:
        git("config", "--local", "--unset-all", "core.hooksPath")


blobs = {}
for it in P["items"] + P["retire"]:
    b = unz(it["blob"])
    if sha(b) != it.get("new", it.get("sha")):
        die(f"payload for {it['path']} is corrupt")
    blobs[it["path"]] = b
S = {}
for k, v in P["secs"].items():
    S[k] = unz(v["blob"])
    if sha(S[k]) != v["sha"]:
        die(f"section payload {k} is corrupt")
CM, GI, MAP = (os.path.join(repo, x) for x in ("CLAUDE.md", ".gitignore", "REPO_MAP.md"))
GEN = os.path.join(repo, "scripts", "gen_repo_map.py")


def regen():
    if not os.path.exists(GEN):
        return True, ""
    r = subprocess.run([sys.executable, GEN], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def tidy():
    for d in (os.path.join(repo, "scripts", "hooks", "__pycache__"), os.path.join(repo, "scripts", "hooks"),
              os.path.join(repo, ".githooks")):
        if os.path.isdir(d):
            if d.endswith("__pycache__"):
                shutil.rmtree(d, ignore_errors=True)
            elif not os.listdir(d):
                os.rmdir(d)
    pc = os.path.join(repo, "scripts", "__pycache__")
    if os.path.isdir(pc):
        for f in os.listdir(pc):
            if f.startswith("test_precommit_hooks"):
                os.remove(os.path.join(pc, f))


def strip_once(b, s):
    return b.replace(s, b"", 1) if s in b else None


def uninstall_framework_hooks():
    """Remove the hooks `pre-commit install` wrote (restoring any .legacy it kept)."""
    n = 0
    for h in ("pre-commit", "pre-push"):
        p = os.path.join(gd, "hooks", h)
        b = rd(p)
        if b is not None and b"File generated by pre-commit" in b:
            os.remove(p)
            n += 1
            if os.path.exists(p + ".legacy"):
                os.replace(p + ".legacy", p)
    return n


# ── rollback ─────────────────────────────────────────────────────────
if "--rollback" in args:
    later, rm = [], []
    for it in P["items"]:
        p = os.path.join(repo, it["path"])
        cur = rd(p)
        if cur is None:
            continue
        if sha(cur) == it["new"]:
            rm.append(p)
        else:
            later.append(it["path"])
    gi, cm = rd(GI), rd(CM)
    gi_new = strip_once(gi, S["gi"]) if gi is not None and FENCE.encode() in gi else gi
    cm_new = strip_once(cm, S["own"]) if cm is not None and FENCE.encode() in cm else cm
    if gi_new is None:
        later.append(".gitignore (its PRECOMMIT block was edited)")
    if cm_new is None:
        later.append("CLAUDE.md (its PRECOMMIT line was edited)")
    if later:
        die("cannot roll back — changed after this script: " + ", ".join(later) + ". Undo those edits first.")
    n = uninstall_framework_hooks()
    if n:
        say(f"  removed {n} pre-commit framework hook(s) from .git/hooks")
    for p in rm:
        os.remove(p)
        say(f"  removed {os.path.relpath(p, repo)}")
    if gi_new != gi:
        wr(GI, gi_new)
        say("  removed the PRECOMMIT block from .gitignore")
    if cm_new != cm:
        wr(CM, cm_new)
        say("  removed the PRECOMMIT line from CLAUDE.md")
    prev = rd(RETIRED)
    if prev is not None:                           # put REPO_MAP_HOOK back as it was
        for it in P["retire"]:
            wr(os.path.join(repo, it["path"]), blobs[it["path"]], it["mode"])
            say(f"  restored {it['path']}")
        c = rd(CM)
        if HOOKFENCE.encode() not in c:
            wr(CM, c + (b"" if c.endswith(b"\n") else b"\n") + S["hook"])
        set_hooks_path(prev.decode().strip())
        os.remove(RETIRED)
        say(f"  restored apply_repo_map_hook.py's hook (core.hooksPath = {prev.decode().strip() or '(unset)'})")
    ok, msg = regen()
    say("  REPO_MAP.md regenerated" if ok else f"  !! generator: {msg}")
    tidy()
    say("rolled back")
    sys.exit(0)

# ── 0. prerequisites ─────────────────────────────────────────────────
g, c0, gi0 = rd(GEN), rd(CM), rd(GI)
if g is None or PARENT.encode() not in g or c0 is None or PARENT.encode() not in c0:
    die("run apply_repo_map.py first — this script builds on it")
if gi0 is None:
    die(".gitignore is missing")
hp = hooks_path()
hook_files = {it["path"]: rd(os.path.join(repo, it["path"])) for it in P["retire"]}
retiring = hp == ".githooks" or any(b is not None for b in hook_files.values()) or HOOKFENCE.encode() in c0
if hp not in ("", ".githooks"):
    die(f"core.hooksPath is set to {hp!r} — pre-commit cannot install while it is set. Unset it "
        "(git config --unset-all core.hooksPath) and re-run")
for it in P["retire"]:
    b = hook_files[it["path"]]
    if b is not None and sha(b) != it["sha"]:
        die(f"{it['path']} differs from what apply_repo_map_hook.py wrote — edited by hand? "
            "Move it aside and re-run")
if HOOKFENCE.encode() in c0 and S["hook"] not in c0:
    die("CLAUDE.md's REPO_MAP_HOOK line was edited — restore it or remove it, then re-run")

# ── 1. state ─────────────────────────────────────────────────────────
plan, done = [], 0
for it in P["items"]:
    cur = rd(os.path.join(repo, it["path"]))
    if cur is None:
        plan.append(it)
    elif sha(cur) == it["new"] or FENCE.encode() in cur:
        done += 1
    else:
        die(f"{it['path']} already exists with other content — move it aside and re-run")
gi_todo = FENCE.encode() not in gi0
cm_todo = FENCE.encode() not in c0
if not plan and not gi_todo and not cm_todo and not retiring:
    say("✓ already applied — nothing to do")
    say("  If not done yet on this clone:  pre-commit install --hook-type pre-commit --hook-type pre-push")
    sys.exit(0)

# ── 2. git status: only the REPO_MAP scripts' own edits may be pending ──
if "--allow-dirty" not in args:
    for rel, cur, allowed in (("CLAUDE.md", c0, (S["parent"], S["hook"])), (".gitignore", gi0, ())):
        rc, st = git("status", "--porcelain", "--", rel)
        if rc != 0 or not st.strip() or st.startswith(b"??"):
            continue
        rc2, head = git("show", "HEAD:" + rel)
        rest = cur[len(head):] if rc2 == 0 and cur.startswith(head) else None
        if rest is not None:
            for s_ in allowed:
                rest = rest.replace(s_, b"", 1)
            rest = rest.strip(b"\n")
        if rest != b"":
            die(f"uncommitted changes on {rel} (beyond the REPO_MAP scripts' own lines) — commit them, "
                "or re-run with --allow-dirty (the text is kept either way)")

# ── 3. compile ───────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="precommit_")
for it in plan:
    if it["path"].endswith(".py"):
        f = os.path.join(tmp, os.path.basename(it["path"]))
        with open(f, "wb") as h:
            h.write(blobs[it["path"]])
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"payload {it['path']} does not compile: {e}")
shutil.rmtree(tmp, ignore_errors=True)
say(f"payloads verified and compiled ({len(P['items'])} files)")

# ── 4. write, all or nothing ─────────────────────────────────────────
written = []                       # (path, old bytes or None, mode)
old_map = rd(MAP)


def restore():
    for p, old, mode in reversed(written):
        try:
            if old is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                wr(p, old, mode)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")
    set_hooks_path(hp)
    if os.path.exists(RETIRED):
        os.remove(RETIRED)
    tidy()


try:
    for it in plan:
        p = os.path.join(repo, it["path"])
        wr(p, blobs[it["path"]], it["mode"])
        written.append((p, None, None))
        say(f"  wrote {it['path']}")
    if gi_todo:
        wr(GI, gi0 + S["gi"])
        written.append((GI, gi0, None))
        say("  .gitignore: *.bak-* and *.staging-* added (tracked backups stay tracked)")
    cm = c0
    if HOOKFENCE.encode() in cm:
        cm = cm.replace(S["hook"], b"", 1)
    if cm_todo:
        cm = cm + S["own"]
    if cm != c0:
        wr(CM, cm)
        written.append((CM, c0, None))
        say("  CLAUDE.md: pre-commit line added" + (" (REPO_MAP_HOOK line retired)" if HOOKFENCE.encode() in c0 else ""))
    if retiring:
        with open(RETIRED, "w") as f:
            f.write(hp)
        for it in P["retire"]:
            p = os.path.join(repo, it["path"])
            if hook_files[it["path"]] is not None:
                os.remove(p)
                written.append((p, hook_files[it["path"]], it["mode"]))
                say(f"  retired {it['path']} (replaced by the repo-map-refresh hook)")
        if hp:
            set_hooks_path("")
            say("  core.hooksPath unset (pre-commit installs into .git/hooks)")
    written.append((MAP, old_map, None))
    ok, msg = regen()
    if not ok:
        raise RuntimeError("generator: " + msg)
    say("  REPO_MAP.md regenerated")
except Exception as e:
    restore()
    die(f"write failed ({e!r}) — everything restored")

# ── 5. tests ─────────────────────────────────────────────────────────
if "--skip-tests" in args:
    say("tests skipped (--skip-tests)")
else:
    for t in ("scripts/test_repo_map.py", "scripts/test_precommit_hooks.py"):
        r = subprocess.run([sys.executable, os.path.join(repo, t)], cwd=repo, capture_output=True, text=True,
                           timeout=900)
        outp = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            restore()
            die(f"{t} failed — every file restored:\n" + "\n".join(
                "    " + l for l in outp.splitlines() if "FAIL" in l or "Error" in l)[-3000:])
        say(f"  ✓ {t}  " + outp.strip().splitlines()[-1])
    pc = shutil.which("pre-commit")
    if pc:
        r = subprocess.run([pc, "validate-config", ".pre-commit-config.yaml"], cwd=repo, capture_output=True, text=True)
        if r.returncode != 0:
            restore()
            die("pre-commit validate-config failed — every file restored:\n  " + (r.stdout + r.stderr).strip())
        say("  ✓ pre-commit validate-config")

tidy()
say(f"\n✓ {FENCE} applied. Turn the hooks on (once per clone):")
say("     pip3 install pre-commit")
say("     pre-commit install --hook-type pre-commit --hook-type pre-push")
_rc, _tr = git("ls-files", "--", ".githooks")
say("  Commit: git add -A -- .pre-commit-config.yaml ruff.toml .gitignore CLAUDE.md REPO_MAP.md scripts/"
    + (" .githooks" if _tr.strip() else "") + " && git commit")
say("          (the first commit runs the hooks; the first run fetches ruff once)")
say("  Undo:   python3 apply_precommit.py --rollback")
