#!/usr/bin/env python3
# apply_parity_open_pair.py — ── PARITY_OPEN_PAIR_20261102 ──
#
# Backtest → Parity showed Icarus 2026-11-02 as "trades differ · 2 missed ·
# 2 extra". Both were the wings (23750CE / 22400PE): entered by the backtest
# and the app at 09:18; the backtest still held them (NEXT_OPEN carry), the
# app had them squared off from the Dashboard at 13:59. The matcher pairs
# CLOSED legs and OPEN legs in two separate passes, so the same leg open on
# one side and closed on the other became one MISSED + one EXTRA.
#
# THIS SCRIPT (read-only reporting; no strategy, order or backtest change)
#   backend/app/parity/parity_core.py
#     * pair_across_open(): a MISSED and an EXTRA row for the same contract,
#       side and type, entries within ENTRY_TOL_S, open on exactly one side
#       → one MATCHED row with open_side = "bt" | "app"
#     * per-row derivation factored into _finish_row(); exit delta / exit ₹
#       only when BOTH sides have exited (an app-open pair no longer crashes)
#     * summary: open-side rows never count as "same minute"; manual_exits
#       (MANUAL / KILL …) counted; new verdict "same trades, app exited
#       manually" when entries agree and EVERY exit difference is a person's
#     * status line (Telegram / card): when entries agree it names what
#       differs — "⚠ exit 2.1 min apart · 3 manual exits" instead of
#       "⚠ entry 0.4 min apart"; "⚠ manual exit · N legs" for the new verdict
#   backend/app/parity/test_parity.py  one expectation updated (the old
#       "missed + open extra" behaviour is exactly what this fixes)
#   backend/app/parity/test_parity_open_pair.py (NEW) replays Icarus 02 Nov
#   frontend/src/pages/backtest/ParityPanel.jsx  verdict colour, "backtest
#       still open" / "app still open" in the Δ exit cell, manual-exit count
#
# SAFETY: fence check, parent fences asserted, `git status` stop sign, every
# anchor exactly once, py_compile gate, parity + parity-card suites and an
# esbuild parse run on a scratch copy first, .bak-PARITY_OPEN_PAIR_20261102
# backups, all-or-nothing writes, build copies updated when present.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_parity_open_pair.py
#   then: ./desktop/build-scalp.sh both   → re-run Parity for 2026-11-02
#
# Run ONLY this script — do not hand-place any file it carries.

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

FENCE = "PARITY_OPEN_PAIR_20261102"
FENCE2 = "PARITY_OPEN_PAIR_20261102"
PARENTS = {
    "backend/app/parity/parity_core.py": ["PARITY_DAILY_20261030", "def match(", "def summarise(", "def line_for("],
    "backend/app/parity/test_parity.py": ["PARITY_V1_20261030"],
    "frontend/src/pages/backtest/ParityPanel.jsx": ["PARITY_V1_20261030", "PARITY_DAILY_20261030"],
}
PAYLOAD = "eNrtPIty27ayv4KqnWOykWk+9G6UM67j3PjWcXxt95GRNDqQCNk8kUiVpGKrvvr3u4sHCVKUH7lJO505nklMgsBisdgnduH7GvODNKn1yH1tQqcfWegf0OXyYEnjIF3LX+NpFDNruea9ljGD3zW72WGNSbszoe6sY3sdv+m0vMnE63YmnmfPHN9rTLym67a7XdfzPOr7vjuZ+I1Z02s0J7RVq5PaMkpShNWxba9NvY4982btSatj+x2/2/Kc2Yy6HnP9Rqdtt7vM7XamjQbz2k3abbNpi9GWbTeo3URYN6vwIy5jMKgdn11dfBhfvT8dX5I+cZrke9Kyh+G7w9/GR4cX8On14Qf80h6GOHJH92/JcOU2bVv8T84PL06uPozfnx+fjc8PTy7Gru22HMd2i93YHVCTULJkcRKFZEF9RozXNLmZRDT2SfL7isZsP5rN6jjDx2A+J8ltkE5vTIRjOw0YO2fXQRosaMpIzCiCmUUxSW8YgZ3ZS/gcJI2IH8xmLLZwZWc/H56OL44PL9+f4SLuhzXRNoQFDms/nZxqT+PLX0+ujt6KBjn0+LeTK9Fw+T8/H14cv3/zRn8dZ+8/Xx5fDGubHdQc1cmgRuAHEY5JEJI4uk16w5DIn0kd1tcn8WBYm6RD7I+PsCx4znuJz+NlOIdm6I5PxsQkwYxMCJsnjJxFISv2BxilAZQPoFUDOCAa+oRquKkP1jVLjWHt93Q9rJmi13bTZCBfR+SbPqH5G34s4h8kJIxSjkFptuJaxzQd4yokIKBStAp9owjsQJ/5e23iOnHNInQcyMI0Xo99Nk/peBGEOlyDZt/TBD/sc9Baiwmztew6caoAAw+W4Ro6YPyu4AZhakwKjWYOW6d51sHMd23nqpZ3Y5QAfU3Zkkrr2U0fnLAAyNAgwUcNkHhDOGWUEV9tlx/EPaELNhZiLSdM0thQHKY+mAQEaFiDB2u1BF1imKTfJ9hz8mhPbVIUwyQAFQSSaAiRQyHm8maWmPGOyyX2Hm3JxF0F495lG3Fzs1hwytwpisoWgIhPxl2Jr+qyeWvXzYem8amUCx80I+hHZs3iaIEPSUoXSwO5bHumk8sr08IRhmkFSQQUAcVq8C0sdS4riij2WayUKSjM49dDsC82EvD1yZs348uri5OfjrHN4Zr05PJSdHHx9fi3q4tDfPPwDQb/F+/a4Er1w9nV2+OrkyNsaW6EGXpIbY5nQRgkN2NoNmLzz0cv0+sxS1exQG8Y5niXmoehz2ZFpHtgraapSfZf6YoQ+RbQZXHwiflkFrC5n5BoRlCIYBgxzg9O68DBfzCSTOk8CK/rhCudBH4HyXJO14RvfmJaApaA+7iZeY6JeZZ5qTQtf65Z+Tom5auYk0mU3oyjVQpgKmyATiStWf18y/mE67cFZwT0piaMREsWlvB+wFqh2ihaLPNJJkthXqXsv4yR+nwDta1GyQ4vp8p8lcn+FPP2p5q2p5q1SpNWYc7+BFP215gxpYqXNIjHdBpHSTJG6TC4fSGnQZIOUC+PuGLOXwvq+XPCoOk8SkCjIyfhfBjSQFQUo6BCrANfYOfS20hMkzAIMzHaWdIkYaDak4hHO5eH745xpAARhbm4I1w5BTRjX5BGFgtwU2BDgpJJEz5CWD7ygr9wu2cRxHrfcfYB75MpjVdADIRyCxYmIS5EmPbRMTkQ8Fy3Ydvnx3UCRGYxTDlZc+knNCV2t+d0VOSGEDCGTmEnyQ3YMmxZEOMMJuUUU/jF8Rq2VAZ05IbKjiI4hCXNZgR5gvfIY0eYzvF6za6aDsTEV/vkkkWQIDmGK9uetIkLQWIaU/UKjz5LZMA4rFnkHBhCTApkgwgVRZdMI+AiOk3rcluA0oDlesn40uMAIECoegNbp4XM9Wx32B2Mna+zXQL9nQhI0j/ho3n3Md/FPhGyS/5XCW/BiMsV9ckgLjpGyPRc36Q0XXGW7/c1D0fKuiDAk0dLh2iUWXFUdxIFGI5vHKKmOgpeDzZc49JBl4EqkOK/YPG1WMMoV10LREWALobGEy6c0HuROy6TKJobC6leuGUr6JYJ8lq/FN7iJFw5ljGWSwt8A3YHvl9Xx6TICEG4YsUvFJwqhSDL3CmJIduJoZxTDe2rVQKOaMOS9WISzaXDMyk0qB7AK/p3+Sq/In9qX+XrkxeFe0MnSe4F6P6K5gZozdtr88nLvi4TXD8ZfGvAbnKbCdhCL75dA3tkVuAnd9LwQdrMouuoASoN3F4UMiCfxdFsHu6zRX3fgJ1fmOYPhQamr0hGFEo4ekSLLupSXnvIq3L/ewVWEPs/FlvUU+KNSxB7zk2T7L55OLzJpceC7gy9VhX4cHexSqwD7MRFVfL2CLS+APKnx0wSUyuJ4tT4yNb9OV1MfEogEjI4GoOCEgLiGVm4gtySByxmNf9J7QNz8EgsC9FkpIJ6aLUwSrELALar1aGYK4szcu34TT9frJyaO3rgSMs5nBwiOhpo3WdcqKpDBhPkwBEiUu2bazKTQSl34kAUKaS2flzTS9YwsgWVts5ExaGC2r8RIXFdsaaBpQRWxwFfkvKfeUj+icVrcYwtXBIWThlOrs7N5SG3MuSIAfQrSX1OoicuHQbuXncx+tm9+Fd9WL1UrQsarui8hFaOLmCGYY2hcQV0ut+Yj0dEAKd4qD/6KpyeKQ38gU1B55+7ZdxNV44Yam1NLh7pC6gYcmuQlDk58G0OsYegGo/68DXrUNgyvgvzufEY95e4wdQtq44kd3CFG1znXjdOCygKZObr8loLlBE+tnBtemVJzehf8oDqRQ7MT1x68hxAvGO4Lq1p8bNsUN/lYvC7fNwUt+TLICnoMeZpLISjbdiWeX/CHEoa/xxqFDYN0Sm7DmMedQDeyqH2A3o9ZoupFML7zY4Tk5KeO353NP718OLd+Oj05FwoOduzC32Km1OFDKdXvzo2N3cf3nyWyv0rll/Yjdso5u7tgt4Z4gSXn12IR+EX2luR1WxYuw9BcDf3Kb3eIPwWtXksuib3HGIPmtF+0iWNU5RhnejPmxO+iAGgVnrkST+faf1kLE2vY7Z1dMtZImaJMhEYW4rNGYyqUUjomtzeUGVKE3U8wMLrAA/reEL6k25bi1OCdMOUuXcqTJacXxypfb7ZQgTD6HfaI8dtzykdifG4+AFTm2uWomIpd90+veSxOwVRMku5rFAFsGXdtVt1lQCaZYCCfmrDivqzTuxSf+RT3GJjMBMLIffInne+WWLlET/aEzZU9uC+h+DbwWhbQ7wQQO/FGjdbp2YSZqiFYg+DgbVslI+DqN7vJXsCAq75mwyZvb1NBhu/KNBbcTJffFUO/CFxhwDyBf4nj7KGNevfURAaHJb5RVRG0Wx8Nhy5yE/iPKngcCgvN0XpCj4yNK0lA/E1pq30c3pfyJhw7n8i5z+4Oo3D1CbfA+PN2XXGcGVm+xxij0YboHdFjRMe147Fc6nGqT1tt33bYV3W9GfUazdarY5new34v9XuTBu+bbc67SbrNmy/M7MprKZNp16ny1pOq0uneo1Tc9axXZ+13GazRTvTacduua2mS51Jw+/QttdoNxq+06Ltlk/pxHP9WdfxHeb5tt/y7U6nVOOEpxjnR1a8CjGhAORHm/yLg4R/XQeSBiEsKb3pvzs8OYONmN6w6UfU5yFnCjxUF+kyrgtAfU6jxZLKw23BboE8WMcO1+CELYmBZ8W90jE3zU7i+fa5Tjc7i34hBvNDSBMwU/zAIxVwQijPeQ2UN8oPYp08cNZ7CIiFPjRcZ2kX6c7y9rudZ7tc3d9xdT9QpnWUe6ZGcVaRMwbFz9SpC27BM+n+eR4CUpizdJb8kJuBe6flPmRrthmwj+/PjnFWlWZBAEZmzfrKoNfBoJF5FF6zmJuHfMO4xwD79RjLpOhxFCaXOEkWkMdr9e3cjXELzlc24ePMUdh6u5o9BMoPdpEUkZ1cNV1lX838P8SUWRAyqtBCaQBa+lo5ZUplkUrWzVwNDoZv0S5Wzo4t/9/MLPThLI7CFBViEk9BIV6z5EDt6ME514nnNGRz69/Jna4Z/c7UnzVtj3btKfMndsNu2Y1Ga0Y7XqMLSsttTWx71m5P3E7bb878qd/pUGicsW6n05gyXTP6rZbLmNuGrp7TctrdaasDKo+5HmvYbOb53W7Db4Imbkw7Hde2vWanPfO6oGB9GEXdkmaExXOxAbYliwjcxDkwWpbaIiEePqnTAosc+mhMo3C+BvUW+L5g1obtHTTshmlxch5h9u2ATNIQ/g/C5Sq9TNdzBi90GRyBVYWn2SI9CWNUo0zk7n6UVETKWeT7g8zcf2HkPke/0Cwjh0U2WDshqyYKgorz8ilyBbKVsOsRZl1bRUUgtMRNhLU8lKdU89pVDq+qHFaqD1PPpYIz4HUbwjGYMqyUpesEYp1geiPT/wnH+svuEvcGYUtgJb8cX7w+ObrChEF+zoVpjU80Nvb3p/vJajplSWKK47UH5V8fdkvjED4qtfcE/6V6MLSWMrt6RyCowgy0veyIedoAc+UkT9KoASmo0f3FCtxFGEY2PwiW/StJscOB/TtScutAkZD7xJJWaZMZbOUAJxYiMhZ4b7ZWovUT9nFTKgCAD9wobpQ535oaZrtm5J/kX2rId6pxQ/ivf5EeD+830J6sQ1hqGkzLA7IPm0yf6eO4UikNEdFp7ktk/TXcVWIESLg5gHehEjfCKxLtW0vCU5OYYU0g88dcuv/xD7Ld+E1fHLCA+6ZjpjoJvfDdNrRNhuf2uet/9rK8l5weeUBYHqd/K5wxlD+SV+B9/RMFGwRJwv47MkyF+KuflykYQjRV/ft7YllW6tfRyKXsckmnrMeVzm1M0SncbF7dx1YpF4LBcbiCDfgnefnqPgmuQ+YbW71MfmzwchKTA9XpIsm6yQpDc/Py4JXAmpthwP3lQeq/2lVP+HzUC0d3uzAvdNqJuFYZWYE3TKbVI4Lz3CczigcIMJcffMrRnkbzKO6JX4klbQKiy0vK5A2clwcwBmdAdB+iCXyKX1UpiP/s9lfe7dxJfXiH0TZb3DZLLLWCtH5WkoYqp+TRikMGPi/6JXrjRvDH7rLbHvlr2RGCPYz2Qna766afdgrGD4jHqEHFeVjtW/KMAcPw288996DPrPb8FiMAvVLUIhdcAyeyqpMYJ0fjX1xTq/esQ3i1BEJOouhjHUtEcbyIdBEghsAkuYlu+YFWjzTK1vyRSsstP9JCqIRMfUVDNDLnH67evj87P7x62//u/NfXZLlOb6LQI08ib7BYRnFKoiR7TNb5c8oWy1kwB2vH7ZIqMibys3rnXjnjYice/5AFwxE4G+GnII7CwbD29v27Y1GsrMBai48+PhvLmM2Cu/6wpuPI6y74vLASS3xRM2tXSZHs50c7UmoyXdSwXUTnzeHJ6WUdGOjy8viS11GCIbVHw/DkEoMRhbmRrca4iVZx0m/WpdVP+p6N5x2qFFoca+EpdB0Dbx8vlKQ0mPd5JquXnSzjNy1VIRCAmcmLPtESWcsYSwRnoBd4F/Aq+AF3VoAi6wlUd74clQjGnmYlKOymQBEyuMecnEDTHPQatj3ajPgMalFXxs0NLHhRJ0nSt9Uq5Kk7QlXbbqAg1Inj1LGkLR8EPPBHEM6iPq8zz4vRFeXOeRHF2cmbqw9u68z28DxLTQ5yZyRrBMMLhlma8HrhOrlLkz6m5OooIvJJ6C/58nu67reaJXTv8xLQHjknLwiHreo+e3IWVenZw8+DfbfHqw+FQ42ts3lEU0Rr0GuOeMkAv9PSwzm3ahHy6rqeQF8r5O/hMuoku6PRE2tTNyN6uDZ8VVnRnlwiR0c51Nj8BtX9RpDtR+TdARJuWHM9z7aPVIXOsbipe2V0YZc6gHi7ZbVt2dBoQoNjd6wuL0+8PMWjXLUYBc3FYvVd0Dqe1Whig+PVSTdvGNbeXR2Nj95fXlWB9JoZgj/+/CGD2GghANdyOH7IU7yl2bYa7SfhlwFDbBwbobWttsCvAS0NaGk5VsPdDa1djRpfrGe5CHNn8p5X+/e0E2wa88Q81sWX5hFXAHbO07Ix0Xx4fv60bYV/Hm5t03Kb2dbK9Tp22+rYasGw/qatFc88vskSNuxrJ99omEsQt7zXu+Dv3HHQG5xtbMtpZrveEfCbTT7nkzEvbT+ARgJ0OpZnq9ZmV7a2HavZzC+s74a/kyEkYZApCtBRplyJ+CPAd3NBBrxdAO45HHjD3gY+yhVovAqNScpPu8zCzRuZZdpLhBPCrz/iHQqsgEa3RYSbxZuP6Vi6SsCJE54kmGCSYJLyMuzCjbZRNkQWRfDLP8ZEJG36V/GKmWUIWAhRCUVWM50fiXMQI0ME7DXlUCgJRCoJi1qLILR7XbwcQ4eCuNQlanQLNR0o4lYG3CteUcsyjQRBaN+q6rPKl1qFgS5diHIsMmGACcQ/tyonpjmR3ExHc9wO3GZJYY2sP16NstVpSwJdglRJbEFTkckJEmYALC2fi5AVbzzfWS25qnnyKLELGTMw7baeiZPvKusmX/N0GE9kGQ3uY7gVZ6smjjB3ktS1CJ2lDKIgcR9LVZPyGjnOAAeCtThxc8bbrqR7NsWR5GWK83q8nOStMpHtEpFteUwHgm4kZTqWyKhRURANFKyNtRPwDeYU699xh6kigTjK0UQ+TG8jSUJ+5yy/etXfm6R7xMiz+JisEcftMg+jcQPeYGB+fjtBv6Mz2G/3RjlufDK+kEFJXWaKeVRIhM7nRlyRcJVBeAluXf7OF8mTS+IuoyhakLV9H9ky5TluUWMuohxc3fnBKf+oLU/hUHG3uVxW9nDRvOqh3RLQAOg+Z2ld2oIAZUzJzdksBReWXuPlsT2unSVziWViam4PCctVXrg2SnW9RW7RJ5BC2islZVTa7dQR8xy+/m+swRclhJenuBDw8/LCox+Ip5/aYv3IKkyRq3O2eW6GnBNQCIxeScSHekIeYpaI2zvRR3SpUYfzpAyAFQkYEfbLq0qR6DWsnR+e41+t4S65SolD4MDddp4473E6bXIyiaw7mePy91QVFvJSgQpqU4q02BMlDjis7eQ6uK60754uXKBpcJIxbJgBq5MlAPL04pkT4wrL8LStnwefGD/2IClnrHJ3rhFj3BnsBPHw6ckveFWliJOBYMzno4aaFXe/v0P1is8Dl9+Mxg7ytU6kOe+rEAMICRH9k8pzCzWwmAKW91dROaqkQOw833ogbtxWOFvGwtHV0w2WP4nLLjkmovA3EZejMbdfuvWi7KQqpdmryILu6YLmPCZplWlUWIWOKyoAt1XF9xX1eR6/0L23g4+fKJ+6KDoFWXQ2lXKwExHOXWohqOLTKIVom6ygkYbXoqpNOGZBjDont3xct0obiWUYwQSYAmsXNQMxSbgd1/T6vvB48ha8jGJbtpPdHkoKf2JD9s8aVHf0ELgvtNsZ8ixyvaKxLxaJMxac9W0xUl0GjUyUtKZcnNTJTFBxNGPqQpQfdus1HcOwEGpUyYnWRUNGbysId1P4PRwjx7IzhGToL7Ep15vhLbHYfUCEtUBE0QHJfVt2q9ydBeB5VZbmX8Uo1wkDzwr+QwoTEaiU8DM5zyVZ2MbiOEJXm1+suTXz6rLbgT2q+Hsf0rkAYrhbworDpIcxpbySiOoyIvW7pptc87k2GY9zafwAt8FXbW/Fm/hzAetcZz9NXcsNdm8yQ8LVX54u50jCbnvPV9iAmG4K1RUMl6jJ8G9TnL2/krHGNuk8s7oKtaKXXoyIGlYkbnaTkH/XiKje60S42f1zXgDveh11oCSOOfu8xcJAKm48nyR8Gp0odLsyAbkvI0plAFamU2ObTqi25tfRQyoLPg+8XF2J10oF8GQ2OnUzT7Yn/25idBuCL4vnbFaLOyyMxljiFreeTz3E0SwWVusmPpuiwMp7RfHaE/77npDZkjXVaNp6ttjWtyCgs9LeclZa2grQ8dDNPxptkR3jK7qmS4muCEFsq5Ev8At6Au2CJ9Da7PaIt2isjjyKLvHXwksz2aCph8PwPssRbcRfsgGLc49qnid9zA0ooWAuDy3A1PBWeTylLD+2Hb/u4exikLwmvk54AtxAWVJ9D09PZV6VYF51H/Oq5Ojt8dFPlyJb9ZrPVNts/g8haTMJ"


def die(msg):
    print(f"\n[{FENCE}] ABORT: {msg}")
    sys.exit(1)


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def find_root(arg):
    for c in ([Path(arg)] if arg else []) + [Path.cwd(), Path("/Users/anbu/dev/scalp-app")]:
        if (c / "backend" / "app" / "api_server.py").exists() and (c / "frontend" / "src").exists():
            return c.resolve()
    die("repo root not found (run from the scalp-app folder or pass --repo)")


def build(root, data):
    """{rel: (old_text_or_None, new_text)} — every anchor exactly once."""
    out, notes = {}, []
    for rel, spec in data["edits"].items():
        p = root / rel
        if not p.exists():
            die(f"missing {rel}")
        s = p.read_text()
        if FENCE in s or FENCE2 in s:
            die(f"{rel} already carries {FENCE}/{FENCE2} — already applied (or half-applied: "
                f"restore from .bak-{FENCE} first)")
        notes.append(f"  {'pre-image = GitHub main' if sha(s) == spec['pre'] else 'pre-image DIFFERS from GitHub main (anchors decide)'}: {rel}")
        new = s
        for i, (old, rep) in enumerate(spec["hunks"]):
            n = new.count(old)
            if n != 1:
                die(f"{rel}: anchor {i + 1}/{len(spec['hunks'])} found {n}× (expected 1):\n{old[:300]}")
            new = new.replace(old, rep)
        if sha(s) == spec["pre"] and sha(new) != spec["post"]:
            die(f"{rel}: result differs from the tested build")
        out[rel] = (s, new)
    for rel, text in data["new"].items():
        if (root / rel).exists():
            die(f"{rel} already exists — already applied?")
        out[rel] = (None, text)
    return out, notes


def git_dirty(root, rels):
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=root,
                           capture_output=True, text=True, timeout=30)
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def run(cmd, cwd, env=None, timeout=900):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
    return r.returncode == 0, tail


def esbuild_bin(root):
    for c in (root / "frontend/node_modules/.bin/esbuild", root / "desktop/node_modules/.bin/esbuild"):
        if c.exists():
            return str(c)
    return shutil.which("esbuild")


def test_scratch(root, plan):
    tmp = Path(tempfile.mkdtemp(prefix="parity_open_pair_"))
    print(f"\n[{FENCE}] scratch copy → {tmp}")
    shutil.copytree(root / "backend", tmp / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak-*", "venv", ".venv", "*.db"))
    (tmp / "frontend/src/pages/backtest").mkdir(parents=True)
    for rel, (_, new) in plan.items():
        q = tmp / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(new)
    py = sys.executable
    home = Path(tempfile.mkdtemp(prefix="parity_open_pair_home_"))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(tmp / "backend"))
    env.pop("SCALP_APP_HOME", None)
    be = tmp / "backend"
    suites = [
        ("parity open/closed pairing (replays Icarus 02 Nov)", [py, "app/parity/test_parity_open_pair.py"], be),
        ("parity (existing, one expectation updated)", [py, "app/parity/test_parity.py"], be),
        ("slippage view + parity card", [py, "app/slippage/test_slip_view.py"], be),
    ]
    ok_all = True
    for name, cmd, cwd in suites:
        ok, tail = run(cmd, cwd, env)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {tail.splitlines()[-1] if tail else ''}")
        if not ok:
            print("      " + tail.replace("\n", "\n      "))
            ok_all = False
    eb = esbuild_bin(root)
    jsx = tmp / "frontend/src/pages/backtest/ParityPanel.jsx"
    if eb:
        ok, tail = run([eb, str(jsx), "--loader:.jsx=jsx", "--format=esm", "--log-level=error",
                        "--outfile=" + str(tmp / "sc.js")], tmp)
        print(f"  {'PASS' if ok else 'FAIL'}  ParityPanel.jsx parses (esbuild)")
        ok_all = ok_all and ok
    else:
        print("  WARN  esbuild not found — ParityPanel.jsx parse check skipped (build-scalp.sh will compile it)")
    shutil.rmtree(home, ignore_errors=True)
    if ok_all:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="checks + scratch tests, write nothing")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"[{FENCE}] repo {root}")
    data = json.loads(zlib.decompress(base64.b64decode(PAYLOAD)))

    for rel, fences in PARENTS.items():
        p = root / rel
        if not p.exists():
            die(f"prerequisite file missing: {rel}")
        t = p.read_text()
        for f in fences:
            if f not in t:
                die(f"prerequisite {f} not found in {rel}")
    print("  prerequisites present")

    rels = list(data["edits"]) + list(data["new"])
    dirty = git_dirty(root, rels)
    if dirty and not a.allow_dirty:
        die("uncommitted changes on target files (working-copy drift stop sign):\n  "
            + "\n  ".join(dirty) + "\ncommit/stash them, or re-run with --allow-dirty if intended")
    if dirty:
        print("  --allow-dirty: proceeding over\n  " + "\n  ".join(dirty))

    plan, notes = build(root, data)
    print("\n".join(notes))
    for rel, (_, new) in plan.items():
        if rel.endswith(".py"):
            try:
                compile(new, rel, "exec")
            except SyntaxError as e:
                die(f"py_compile failed for {rel}: {e}")
    print("  all anchors matched once; py_compile OK")

    if not test_scratch(root, plan):
        die("tests failed on the scratch copy — NOTHING was written")
    if a.dry_run:
        print(f"\n[{FENCE}] dry run complete — nothing written.")
        return

    written, backups = [], []
    try:
        for rel, (old, new) in plan.items():
            p = root / rel
            if old is not None:
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append((p, b))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new)
            written.append(p)
    except Exception as e:
        for p, b in backups:
            shutil.copy2(b, p)
        for p in written:
            if not any(p == x for x, _ in backups) and p.exists():
                p.unlink()
        die(f"write failed ({e}) — rolled back")
    print(f"\n[{FENCE}] written:")
    for p in written:
        print(f"  {p.relative_to(root)}")

    # build copies (gitignored; refreshed by build-scalp.sh anyway)
    for rel, (old, new) in plan.items():
        top = "backend" if rel.startswith("backend/") else "frontend"
        bp = root / "desktop/src-tauri" / rel
        if not (root / "desktop/src-tauri" / top).exists():
            continue
        if bp.exists() and old is not None and bp.read_text() not in (old, new):
            print(f"  build copy differs, left for build-scalp.sh: desktop/src-tauri/{rel}")
            continue
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(new)
        print(f"  build copy: desktop/src-tauri/{rel}")

    print(f"""
[{FENCE}] DONE. Next:
  1. ./desktop/build-scalp.sh both      (after 15:30 / non-trading hours)
  2. restart, Backtest → Parity → 2026-11-02 · Icarus → Compare (Paper and Live)
  3. commit + push (fence {FENCE})
Rollback: copy each *.bak-{FENCE} back and delete backend/app/parity/test_parity_open_pair.py.""")


if __name__ == "__main__":
    main()
