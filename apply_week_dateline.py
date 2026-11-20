#!/usr/bin/env python3
# =====================================================================
#  apply_week_dateline.py  —  fence WEEK_ON_DATELINE_20261120   (on TICKERS_UNDER_20261120)
#
#  Dashboard hero de-clutter (option 2): the Today / This week / This month
#  cards are gone. The big number already is today; "Week −₹3,67,519 ·
#  Month −₹4,41,611" sit on the date line right after "closes in …", as text.
#  Clicking Week or Month shows that period's net in the big number (accent
#  underline); clicking it again goes back to today. On desktop the date line
#  now spans the whole hero (above both columns) so it stays one line with the
#  broker-check text; on a phone Week / Month wrap to a second line. The
#  pre-open hero keeps its This week / This month figures.
#
#   frontend/src/components/calm/CalmHero.jsx  period links, tabs removed, full-width date line
#   frontend/src/pages/CalmDashboard.jsx       dateLine takes the links (a function)
#   frontend/uitest/test_ui_calm.py            TC-CALM-0005 updated, TC-CALM-0046 NEW
#   frontend/uitest/test_ui_index.py           TC-IDX-0024/0025/0026 updated (no period cards)
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_week_dateline.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "WEEK_ON_DATELINE_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/components/calm/CalmHero.jsx", "orig": "64e0843416dcf77c52ed40511e79df47f5e02de5787d0e45c26242bb18f18a6d", "new": "e43f0518dbcfd4eeb3237e1bc0e2e5951911a3682ef4c92e6c8543f0d80354ba", "blob": "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"}, {"path": "frontend/src/pages/CalmDashboard.jsx", "orig": "f6c9dd3e0c5fba940048dc5745029798c47fb635c0e2d11eba032595a14b8eae", "new": "c308001940498e23c0adde5ae5cc5932387e8cc33c601fba84c696287469e55a", "blob": "eNqtW2ty40aS/t+nKDF6ZsE2BVJqu+2gxFawJcqtsV4h0u5xKBQSSBRJjECAC4CiuDQi/GsPsDFn2Cvs/z2KT7JfZhWAAkipZya2wxZeVVlZWfn4MqvYfPfujXgnjrvnF+Kk2//86ap7cyL++P3vIo5GzbkzkXHz2PFnJ048HYZO5Np/i5/Rgzr98fff8R93uycC9/ut/Q97e3vfZ1+SqRSRdGXsTQLpipyGsMJ54oWB+CRqRLzWEKNpGMtADFdEuBsMFwJkLsOnA9Gdz0VfJokXTGLxx3/+l0HGd1bhIhEdcRG6MgoaPKArx87CT+q2GOBpHsknL1zERFa1EjQp4cXiUc4T4cRgwXfi2BvVVP/Qd0XkeL5qR9/P5cQZrWq2nrYQUxmFgv9dXfbE0JuIYDEbykhY594TaI+Fl4gkclxMOgldZ9UQ0o+luHbmMjpQo+BPJIZh+KhIFv+GEJcLIklDBPIJjeLFbCbdekM4AiJKZDCSDTEgsqIpvkj5iMtFGCRTkTjDmETpRImiOoKUYqYaBpAFEQNbiZyswIRT5lFYXhILnoATuIpXEYXLWMQgiJdVRol4XXwDtlysCMjQCx7EicBwZagpJBmE+ZArmRxUCUpnNFVE8CfyZCyIo16QRKtm7xkincmEhOy4My+os5ImzhzcQy5SLKcrRXAcjhZq0vTVURRJcYja3AmkzxP0aaYsK4jcD5fC6muGP4cxhA9+h3IcRrKu1/3ESZy2+LE3EE1n7jXHvpRJU4vuOxHXMXDEa0urGvMYLD7mE69jZybFWJK8p5KNTumdUrRIOm58UJCP5Bzq3eQpx/mcaQhSihXP6t9iJRLSzOYbbzYPMZm1WMQSVuUPndFjgx5647EcJXx7IWch39zIMV/7CaYsUjGOwpmogYlRUjsokTqLL8Kh5xeNbLs5pRk2jY+VPlgzL/HlDOoab+1nNqj0vXCiR5mQsM2eIyi4fE6axddj9aboXV6+tbjoDbplEmgX0IhNs6k5PFvVDRZNE3+hu16bG74oGtC2ZjPze8efuzeD+373ond/Mdhwi9lwJ3K4mFyzPm4bpPhccMj8gXlvvrWLUsmiUUWyn6LwUUbHU0l6oR4+Oe5EZg9ncbyQ8QtzVrT7qziRMyZRXbdTaqDc0msUSi0rJLJl+cWLPWiVl6y2Ks9mM5NOEC4vvOAsTl5lg/+ehtHMKSkA+6xLMlSjs3ZkcEhN14tpzalFaf4X3ZufeljyAS38xdllAxG1bz6eXl7d9y5P6MEkPWN17kv4gTAwCR7DxTdE/7ohbronZz/3G8ILdLMX5jVCIOU/BRmKrZ8pVr3YPmthDp2J9xgepiHOXF/S3Wujmj2q1qz81yud80ZVvxO48vnaSaZf6100LCjcwJuuXrYt7lq0qZjvTa978uv9dfeyd57Z7g/ZN7Pd2eVJ769oN/i8YeJv4K+ggadXxz+TGlzddH/s3f/U+xVYpRZj9LkdGy7I5pB15mZ8cMB1xdJDTDddFccUCiVm5MiGOjsf9G7KY7g5aKMJn3o+xAwZqQ4n3V/vL+Gi+mh/W+svArLHhqgBSui7AbyBvv0i3SB/GEwXUXZ/Gnn6ru8ki4ju77IRLq4ujRH+4gTcQQ55FCeiS3ceqScm8ZdFoC4+f1tMmK6c0+UKgQkXQEK6nMgRjfNmvAhGjCRjZRsDOG1LeYC6WL8BSGFOXIT3DuDUkoK4tOr2hGLIyqoDgxBcs7hBpyNa4rffRPbwoY6ojEkFNH/5KANSbtVeDSEONwwfo+oxZ4RLK1/FrnZPBxnlB+UBRDiXAfBOIN6uZ+Ijxm6JI/Hwdn0BvbbHfhhG1gw470Ornk7Fg2iLWi2lpn/Cq3T2cCDSKmclB1Rmy3RGmxyNfEBxzcvW4V8f1yBeyI9oAsALZ8HLxXLMvmkJ8LAk4VSrD16MHi955ay66HzMp+CW1rJg/O2aol4wsVxa388htNSq1+254wLlRIm1D8Vp1eppu9wQbC8Sub0pTRBq1syTnWOkR/dXNycwNWXz+63s09h7zkBwGCHTaJMIHQV48TEiVALjDTQE3oUx76pJH4il41GCA6kAhsPj1jV0h88C8h4nnCtgKcRZwNmBIyZRuJgjXxIBYlFDxGGGdFXSMAufsIZLGu66eU44N0AqZxNOVEIcchAj2RKBqzFL2HIaYsh36qXl2J5bt7EQDqIA/Cf8km5vDekTMCS879hDekeQC/lJjIk8ISRDQ4dOLGsirWf+gNj7kdnGqCM14MgGRoYzJBRKNle7uu5d1sgKR3ay9IKjzW9HsNK22ANZ+czOPncCLHYKQrHF4muIbG6FKwgwuJp7NnFDF29t2+aed3YMylYhj5x3y6nDZopHfAazgWpZJ+3VXKnhmCWKoTRnWtmCJ1Cl8eilHq7MV10pHmVueWaVRY22eClZS0KoNOWWFucc03AZcEJSZNgcQcYcDOqkD7n4hgvPd5X4iFRDN9omRVBRNnjhzJUXpR6IY1EPSZxlRcpg33CeyD0mLHeyNysizSGprYXntgU9NjgdayOP9v0GwtqczIceyPyICLmYyOaJsCqcn/3Sq9XFxOY0riOQVnOKPbG5M7/hjhgz1mM2xIRZTfmvXh+uH9ze0Rs0zScw2ZhAxJ5IiUTxcN297t2QPmaDtkufmUX+yjy2haXvMHPdo15Mbgf0M395YAxLRkCz0TFJkzDHVK9UH0zHni/iqaVEO+FZo2+D6TRUapp1EX/+c07mCPzSq2+ULtVyutkXelPT31JDjNpyDNMDD7nKVPKil53n0gFbQ28CJ5WI3+CyflMuUyhLFn9bzObwrg5sLnDrsLLMzHS5p3ACpWqVZSqtl2WyHTOvtQxtgNTiLmXaP3sAnZ1qKltu6idzaH/WrkhMy61cvIF2h+TyG0JGUQiD4qy4U86arO9arZbRdcg5mWplZG5WzqHR9lZFX4QBmVzy7Z3qyOm9ladE3CWvCFjlsEqGgP5nyHyjJ8fXX3OKBRVEyobYU+xm668aj3zpRDkBDCfSBoyLPFnOqsa6zOupui8xmzGVRCv81eQ5AvWTMALgJR9yhizU2kDXyhXDa2Bc+MxkNC0oZK9N47/FoNG1N3qUip2f88evc7Sz8w/ytIWXsQNXtcFM5m1JLnzLo/+rssnTARZKrXt+XtvCSf7e5CRngGLWWA9Z5soi01ZclIaPN4ZHFDFFkCqHrqdMqJcnfIWbsspiuTLvQfGKI+CSQpiuAnqxgAdA8JPuNp3O/WqxxCSHHTLHbW6WY2eHrdUu8n3qomJD1k45pY4Od17gZrEu2gAqLHhqd9u6K/w8E6gbyq/eUEhkpwp7US6jYLxkPxBBciPHSla40ZLKv8/RRWENQxAl3baSaCF15DHY0AyIV5e1qt0NQd2M5WUKlMJysTP0/XC5i/Xb1aX6eBThHdVWq2lsWzjeTFBNGh+M2qzlyvgxCecZYV0I1aVWLuc2lN9RRdYEcyRQ/HQAQEwyzMYcOcEIEEFvSuxitLgIvlqq9mgRRfD02zSEmkCwlZZmi1HiowEVkcJ5Wxl5Bl+0YeGDmc0kvq1f0ZIclBvLp5jzZmB4yflwEi5GU8L19PAoV0hTg5rWLDQusItk+oj3aGE7rtt7AqPnXpzIQEYWJQsYlOD6nMRPsIuGhwsolGLgzSTWS1vTy9QjSQxtHYCDxAcd0rJJ0RpDAPEsRDZjKKhahkwgZfFrjXyT7zuoaOVE8HV6QbaVXz/1zq++lKs4pY0ptRNg+XJCpXuywYOq7sUEstXmgJdUxoeGkb+A8RA0sP99IaNVX/pwQciVa/haqx9sdvlMsBk9CXnJhDIqpJ7IOVc+wf4nZKAhJVCAMexHlDYTiMRH8u2fCP4gUTz2PYx6Qw6vbk+lN5kmwGet6ohLJ+DtMcriGTlZzMM3Yq+1wV04JqeixPriUKStu0y22n3mPP9qCiS76SnsZCsr/KxY3c30xwugMeplQZBUgVl2hkCTY0Sgw474QN6UnsRH5H+Qn6ag6P5KNRNmYVfs16v6I7JpxSRo3QWAbeIFAza/h7drmlM6f37Y7MOtAWrCXzy5BLAeyqnz5IVIUJQaE0JWd4yRnUUSwkKHVL1o0xJCkzLITP8qvjJ9weZgKcpRN8QPrfrXG33/3aut2Btp2KYCjRFTjOB3tDX6aff1lEXoav3dhLwK+auW2TaUlYWjvL5uUWKSQ9kGb9bceu7dkU39weht/vHuH84iCqTvZiifNry0GPJU3LLUTLkVz7FuK/CVBfOd+Mn24s+e68pAp6vQuMieha4UOxzhT09r9TzNyYN2/JS9uzMBFWXhHGcJYhukNRauQ4UsjRSILWKKmDMeOcpTgql6GGkCaWAHcFRPKgkTLPWRTdnnkc0rqw2m/J2T16KBwS2t3VDptU5n2xon5k1UTlYV8Gb9wBRQ5ZUGNcaynl/1eyf3UIQt6aGqkyFwwAEhD9wlLNEEAPTgPbNaGW9Sx4+8nx22yZd7kQaLsbOKESiWIshwQkC7e5K2lqGF+by49dmWqVGhhsodfZlYdM/wb+bMC/THxTBdztn4cle/yyXA9R5D3HqHt1PsllgZGxSjZeAMfYlc3jCWsRMnbSx5Bp+59N0udojyCriJ6nWZtVPeW7GyzHGjc8XqXt7sEJdnp4NfsRyfupc/qXu4SE9vVVwMLrITCVnkDieTrB5WJBeUAVhWqKqCIYc/aP6RKgK1CWaCIYoMNGm2QeqthAcYMo5kPKVisOnXgkrZvxAGbIHcmHL9+U4IpdH51sBdKt6u8xfUnV7kexpZWz55Qa0fDAuS0u1OaIrAD0dm+LWIko2uFlUREyCPJmIxXLJQ5a4yDaRCLChVPSAbtnZ2uITAwTAbZqejpITv2auPYr9VN+brjBJVJ1NOQD8eHak8JpLuYgS3CIVTShsTbLS0Umd6X6/bsfcf0mCRaheaJqapiX7DGm77MpjAh7SLArERImix9coQZZ1MmR54Szq1YVVVftSpEbfIzsoEs8/syTepK4YrTulLr/fTPZb8pDvonZ9d9jYck5MXntjjqKNBU6SlvIFC+HEwRaq6VMd0+H7GZ3V8L3iMG2qw5dQDIADwExHDI1XwrxUbMX/8/t+1ku6eQ6BkQXMZeaF7TrRERwcJMiAGAoeu98SLs5tIIEm3U6NtwF3qj9FlTTAc6qzXQm9rw89jTZ+BXhzfmwSU8sV4N6JTR7RPN3GQ1/Sv7XjWoMV//hLRi9oSlxo7v4ENg0QYHGOKfaxNW+y9R3YW+oSWju0ZgV0q77oELG9oru2iKndEpJ+ffWgQj0GAVqTpR42bDmMg9o9rbbmp+N//Eetsi4hsk5637AKmh03uqKmsDYmlGWXjLIRYYxqq9JaKZmlswYXAzloZ4JHQe0j8SPZbU/ZAJlhLC9nq2Rf2fARJUO4mcpEYswSHO9rcASsxJ3rAMAQtTRJWxgW14UNFurRIWIN22pBaJ2IShi5rAHV/yFrGTOPtWjuLNH6oqw3FbLaGxA6bUCK6NWsQk8gjKzNWbosK0eXEi6Q2jhrksJgFhhbNXALrQo3aLpGgAaglLgM5w8tEHnN3qONDJOfSSSzC2buwcaT/CIvA/hb9/77Vmj9D5Rx/ZFnwrH/CGG/XGM2fiHfiPRA+udxvOTkdR/X6Q8GPP2lQxHsnvpyd9O77xze93mUe69pUmphRCeBboWYS86bbknbjkCJI2r6lhJReRCLbfgppz15gCslKdxPvmjyvLtg/RWaJ2QIJxkjqcw0wjY+j3VGeRnBCgeRmhFwj1QEaA3ArLpHBv2B1fbU/FDHigZkNaS9UPEo517CI1E+ljW+KGn7Va7C3iJDYbHEi9NpwINqkS9b8oOUOmYvqLcycki3SSvXsVz5l96pdJhvKJSYcScl2qGxkeBeYNKuCSichKSz/07SmvNGpM/N8Uq0nJ7J2d0e79HJ34dUzxzUM3VXJ29DUPq4fbs2589SX09WdKL1W58F29SbuHVR5yMnGLmB0Es7Ij+2ogymUOIv0gZwS089GI5nnwoQef/HcZNoW7/dbrYpG7udHUtq0aYI432pRqev9/q4XIIpY+999aAlSc7KNmE6NfvuTGPxC+rv3zDoQ82FDgpUUhRBh3jUbBA+RDkNALaEz2FdsgQbee49x6GyjL8cg/cO3GFRrehios5y8VR2OwcLeB8XdhTP6RHt414iSMIR/0mlUQgXhQq0kvHKFCy3261+BrRSut0BXigIqmSD4OJp6kJczDIFsSnAW7KfGiModA1pwhZmh2pviZCuvbxRieWtwvdFWy9EeSGnODZz+AuavDn+pMkn2DTazB1HHoQ8v/Hatgkn6YFgCRxczptC/0zw6AZI5T47nswZY61IcS/W5abI0CQg/c1ZUUwW6HFJxFRNH24Pygeyvn9COlx5clqTd6bzoC3Gpg9W2Kags3Kh/dUPEh6VDimaYzjOjzjq/TSk1cDprqiukdIxiDnhK35UGcWwvSOcH5VR23FmbuXKKNUKmlL3kh1Sfmums1TVVZ54xQIF90wyQdtb6ZisjGZxj+nyX6lyts1bX1BCQQzbVWR8aB9xe4ITRyEvC2SoQ+r9sPa+dhCvp/2GsTBaDe84uNEv6nVq/qCTxMenaljhSVJtqH6sG8w/CUzpmwnjWhBbbAKpycZ+0T1aYoGolh9N90xErn9jiGGGV8A4HDUaEJbj7vUjhlQb2dL9OtAsRHDan+5WhGFRuimSE+GZ6iDw8vY6rN6aCldSZGWKsSs5S8sZvM9UwT/8j1b1SUOOBkLR+TI0jTPrQGV80KKW0JC3PqQS2NRdcTKp4xIpX5NNMtZL2UJyoVdfiHLGmqLFu6EW2/s/xFmmAk9tbLmTR4ULfr1FNRp3SaOgzDvxGHetoZKcd7u5U4nkLdOHfGelVZTLDBfQqQIBdddaPEO5qjnmpl3pi8wjpCZsmJ56PqVp8ladypzA49r3RY2ed772rPVfrsZ5uGVMU4jGRkSbPiUa8iMbOiE6f1LCKQUwnuAI6Sbk1lpg9nRHlfdWOFGe2hqg5I3HGDNpB9HvnvePBfff4uHc52Ii+75pbJySKgKiRYYx8cD9HhEga1GMR70ymgQGFkWmSvWACXoC03EtqGwYEVEwUavPQ0ymugR/f75MSrX2ANbWMmwpV31iWSgDTQeyV7+sdzhWyegkhCLN+ouu2m+ZT9pCGj3gh21Z24VcWXv9yh1AEudAXV5dzi02TugyLX/l4sf7dDx1IQ+IzpSzUdDG26DMMEMYOAwwGHiYDD/abV0VZFqQxf8qnKrytlVTZckdbjfbQPLWuzHZkg47w3OyOKtWddbFnMbK37VrQ22zfAtGXzUYBj+yTerdpwcjPaChcUj6fRQ90TdU5LXrkm1TVOYEHVLlTXRT1ND8G0VlzGkjGoNgPgwGXWjN3ogqvPIst7iTmzUsiY25XGMT4TWdNBwxeQlSv2YZ2LuyASZ2rhfKN9cl/ecBrE+VrE21dm2jr2kQvr01UWhtBWztEm66bwtkq/+hF+UevyZ9rl+nL8o7+P+S96Wc076r4Q/LPT7rhYYs3UZW8qjeZZbumuhBITkHtWrRVyYlSS7J8npT6RSWVb/QXMUbeIV1bsVcwDNygAOTHLZgykuPOWp+7SEsYgUVk/NRwG8LkHTMDSFR2fxEJ9tnt65QbWA+5XjT2w+VfSxmnAh1mQbviEM11/Iq7/hfqrRvgdbbNIStMySKa8hZjp0b7w8b0l2qae5jnVAc6ui97/e8aJUDB9lLMLrca2oVQgQOsVPXxVSytoTMH2MKGiyE2Lbk0vLLnTUidCeDrmkwjb/st6DYA+5VAZPwoj/Yd4ES27T5QxRTGXUyjvunD/sn5f9UpHFZ/z2hfR+ETFyifHH8hFatpJel7/deLnPdtjaP8CyV1KLazVtdU7VebLm7Ds9FvrQGJ6XwCHiR7aKr7+M6cfg+SuV7doDy/5osT/Piqa1mXnZ/xS8zmx6IKnq+6WQRP3/wfo6YzgQ=="}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "ace97a1cee525636cde549a820efe45fc08b4e42f93e39b2f33ff7e3aad59a21", "new": "b2a47236be2d590759b319db5d6134a67ac73331bce34901c1e90c50030bd0cb", "blob": "eNq9PNtu20iW7/6KChczojoSrZsdR171wnGUidGOE9ju7lmoBYESSxLbFMmQlGWvRsCgHxb7PDuYfdmv2JdBA/s0n5Iv2XMp3iTRlpPOegYdsqpYdercz6lT0jRtL5JhNJjbg5HpzAz/Xnz681/F65Ort4PTk/N3g0atcViv11+0RTSVIpCWDO2JKy3x2gynQ88MLKE75r03j4Q28ywZuFpZ2K4wYazp7A0DbxHKQHguff/q+7PzazEOPDeSrmWIq5F0ZYgNM4HrD8b2XTQPpNAtmD55w0na4ty+leK5+GD6MKEZ7dWb7drBMXznIjwzGckgxKVxoX3Tt/cD6QNo+6onnMKHZUNc0z5MJ/4iANilmJrwbRQKb+HuDc3RDYAnwrkNPbp6hSl9mpYQRvARyPR9CJgrG3t7Av5GVrJD8fvfi9Ozzth0QilcfyaCuSuGc9uxaOSHf71++/7iw8n1245h7MfL+vfR1HObAlcPo/11+gjRO317+f5dt7Pvm9F0P/L2R1NAoOzvaUBOe+Z7QSR+Dj03fvbC+Cm8Tx7ngePYQ8M3g1Du7UGHgbMZtgvkivRaBb7iFssOXHMm9fjdHIb4rz4A8jhyMCiXy8mc9gC+vkXyhOJ78eS/fxKu99Fsi26r1tgjpsixgVrlzXm3e/0Fc+YYTc35rnvdvbyqCB+Za+C5zj08B3Lg+dKtiJl9NwiRVR+cfO97I4xMQF6Z10HmWwT2ZBoZ4b07GgDviIQO8J727wT7HlNddJAy0r21gceMiYx0jTtA7rxAaPvzMNgf2u7+xPMmjqwyb2h7r06u8FttGkV+e3+/3nhh1OB/9XbrRbNR39f2rgCpHfG9AWwOVIwi252ECLMBHddnF3+4Grw5O+/ujU3bCSvioiJkANLTEb1+RfRq+J/+3t6eJcdiNJWjGz0aVcTIcy1AXzgpt4nhL2CgeN4RdXrzA9uNdF0T4tN//6fQhD2mD4REaaHW/xKwq+dirC2j0UqIJcy00sr0MQx2vYg+4Lnxj4DDHYAcAQBlBRBSkSRWH1bE2JEy6hATVcTCtqJpp95qAcNPJdICXmrwYloz2+1cB3NZEazfOrF+q6CGmcmOZpnBDbyNHG9009FQU1br9Wr9xTVppnat9hz+26xpavOARxQ36UbG7AakSueXUK0i72wQdO+GXsvJF8AhkRxE8i7SUaYNaz7zQ32pWbH+HTB4WlvBWREawae1Gc4VokqpaMLscq2/zIuNojug5tBw5WIwQvUFK97acoEM21lqhCn4hv6FNRhb0MAPK8CKPZP/5rlyYFsd7SS0zf3vPOfGjExFMX8C88MqtIJvTqQetxsD4CUvALbCZ6WXFWf14zGeq2v4FQ3VkCqzoWUK2c5MEJM+jAJdlnuA/Fq/XM7OEMiPc1Co6fdBO7No/H1ggHosI+K0DUuioY2hAYzOC9hysgTxggHigwpGWgPEiU6NyRDTsga2a0egUQLbB/GFXtO5irwANgcTSDPQy8cJzgyyL7r2zTcECXEv6HzLvM/tITDGcwcUskNc0qFhaueOPQJkWvYo0r83zs9OuxdX3YoAVRXNw472w8n52WuY6tZ0gG7MiqDHHXkrnY5GYkCSSU9KNOeg5TWcIjAjObFl2Olp32j9LSCDXYnkbB9AkGBYHoIYhgC8NAOKLNNDp5nKqYB/hJ1kLZcxdz/OPViMBhqKukgcI/QdG7CbAVIr96r1ftzxe3itKZjxj2fIwbTUvBvgcEaKxiDB+xKcETYYvbAvxqB1ye/4GM9c0Yh1qJHHgXRozKH4+WpVQNycw/INYIufHh49tl3rmwLMKiK3aq2KUDuyZARKEsDQXE8AbiyprVIRmXiRp6OpANVoAo/O3cgGNrC8GakEN3I8+MJS3BlIsKEuyjRKbqz9QSeFuh+rfDUG5pbAYnMTN6BpPcMwLG80n6E2BJoF91fSkSMQghMAu9SzQG1U0fMBnkR7XcVZ+5XNdttyZL9U7hsz09eBz78V+lKR1LbaYmTgFyCPhuIDsOv4SbaHpxOdTkeUkjlLoM9AA+I4G/zL4BpeDML4SOr7P/0UPt+fVERJlMoVtdwCmAL9IyASaJARmuZX+Ah29NSxYZ+XsD+9bJACLSvatsWzZ6M8Arbtnsb207VG8wAhgyVOoiiwh8gWJTOwzSr0oFEplXk/EbAubGVoWhPYs16wFHXD9OJPfwLuLBu481OmNzaVSgJYBKiG9nRhR9N1/0Uvo8PnM8GHIKK+QW6HPZ8Zjjl3R1Nd3snRPDKH4DGi99hhnwXsbDBB7VGtul41NF1r6N2hItlj9+fTX/8M/1fWK4zNVIVNSc6qKxudU8bEwSCe4M6o/WqbqA08L+prbL1whsMa/CmLSH6Mdn1axVCoCu11jUwUauwHpysbIyA94gWIUBcmevVFn+EOqkPPuwnXvqtV9lJXEKVVGfHQvIV459O//+WBkOwduSplLTHtaJnC2OI+jENt5JhhaI+0nRGZ3cKOmGx8DibBL9MUEk4ZxgQN4Kzf2t48FLxzgZ4ChG4AE4Rxs5kZ3IMfSfy3DSl5dssFmmBoK2CRwpvI84uR9zSeI2W2HVPZKVS3Xj+Ie6cy8FC8HkQcDuprRYhvahWaBpXHwEy0h8aKAPClEaq187MfulrMuaQC2QktWLA6ticQT/XV188//fJro1U5fFnTMkyMs1HXy8qLeo0cqd0m94DEAcydkwgiE1kwlgZ6t0Nih6E9Ee58NpRBRdFxCFJiQX+kMJNQ+2338v3g6sP52bVKdjRqcRfOFErUgyOa2fUWAhQMeFQuaNNTZB5Y+m//s//pb/8rYFvw3S/iPXDFRuMbDAinoCuFamX/HlfFoEzTdOTwb8X74c/ANQbGj10XaCND/cmmkjBGrkhfcC+4ShEaSYU9spUSl+vJxArimIpYKqsnH7V6EHk5HlghYKNTb+YDD1lX0b0jdbluY4ZsWCSKMnyCbB+hBZYGPYgVuOhlHe0LwRf6zOGJu0BYYqIhMbh3B6aJKVcsCy10myDq0kOf2LankUKwoFlD4cZ/kWhan1kXqEpMG/rJyH5PQyCSEb/8Wj+o1JuNwnHr4kDScHBwlHxAC69N+waMpXh79oe3ySgGKx5lzCFuCdAM42hXUgrGQsGIPCDQVEUtiJGsCI1ZhhBbWXYWyS5yDC1iUPMcXaFFUVRo3SqvC7E56N9l6K/KWiY6spECjwh5nPtQib51gW5Tno7RyuIfCniJELzDRq1QizZjJRr6jWIOm/sQ/Dqg8V38F8blaUgcrEFcCpNw8iXhGHScuIXGFPBc6wCZCpSAomThLERYGKqVEz6B3cVfFfCfgvpZR+1jg9kSBm5sZzSkfK3SbNYeHLYOAIoxdKHiPy2ii5ZnPCYuc56iJfw3JPWocrj0jsicg85AKMTEtIkTj8XUQxa/kdJn9lh4EHQQvzWeznCPs0xiLH7sdr8bvL8YvD657p6fXXQ3TMaPUt6IffBCXHCTzYBsxU0Y578BDrYex4JAg9iAjYw5k2RZTN4iOjUY4tPiYFlrOyu9xAw/igKEBUFJjIQMbM/qLGAD8eeLG1hXf+LClaerZ17t5PS0e5E3h0vM7wELodJPbOAokAB615H4ppfsUvlYYHwHxoctDAwu3ZqBXq2Oqn5go/NXLh2nEww9615lek6ntmPpUfk4DqxoORNm2LBrkbJfuFggZ8CAOizMHyyy8G0NsbLYLSXLqeB4KczRqC3MivIsNtZelAHoABzbV14UebNTtqPg84YgOG2x2BYJql4wvqtjiN8yBlY6a/qPMf+bsAweYnwms27aZ9SVixvMGcc+ZateaRzU2C/V5J0NGBKhje5ZvS4uvFtSW/BJHVT0WCNhXBc9ki4EuQQOnYxoKrQopLIERRugSBY3iR7Z1OGHW4MXhQ0S3PXAZTMEDDxHdjAgAf9jffSa2gaC9TRFTqVmMa7X0k7cF/fQK3CT0tRMjg7pkLwChniSAeaEzTEhaTQFTeySzkJ/d+4Cy+HcVqyTYWb0IVLtZUdKZw0DPDBgtcXLgjaWzgpMGi6+IrQvsSNBLM4crrHiZ2SGpujdivB2IjCzgLkgR7qTaKpWASl3hs6jURNPA4TI8G0RXx5qlRj4jmgwM15AbEB5f2JBtSj11F6269lW5Exabe1Ik2YEb+oOME/fEMqSeQGfNGLFAxPuRAwmOTc+GrEoez7qaTbyCyYoR7g8DI1PTbALbXY/b95OTy5fD95fvu5ebhg2OtNlGRnbQRjRKYirbLd6QQMKXHAshvcCzwzhIwq6pDmaikngzf0ChL7QKgw1+uBnp4MfMD2gXV/9YfBDnZ7enai295evVNsP3Wt8Yj4v2my6UZ771eV36vPuu1P6PC8VaIMRmSgbcX77Xuiwfp5W4RRtOyab2hnMPICTSgYnGXwAUWHfiUREMzQ9LvLeaOtWlPgrfBTx5xFuMAsvcR9Mnnf2eCvRAvpQtx5WWioo3xjJ3Y3WRncuIEcsIfIUX7N2R889jsFpKVIqaRw+tGDHS7W5Nu6SspF5Uq4KNvoSNjq0eikvIHKuIg9suyXwUIyVIIxQfMMjXgPh0i6FTO7qgk0BAphB2s/8yN1nrpKBf/xdtIQjJ+GaX4sZd8n5VvRHh1ZC28Kji/T4CSxd4bEFHSlkjsf4JEZbk9/z91fd1wM6dNiQYOVmk64g19QMbyhC9IQOrGoHnJIGMKegcxAHKqq7i0BzRcE9mIQCjgO+AY0o3cxeMqnPEOKZ7OEIdS0zcp5IdyrxG3Keiiw8KTFe5XGP4ec97Q9MO+9F5wP8WCKVMFKFyvuLLpYCOKhX85CvqL0tlh+LfYA6wrEs1jp8VNhf7bDVh7fFMwFHURFNukWgjRlR2QqV2uAxA5ExJrOH5mlqghy6qNTIfoNhoTKGeFcLjCtImhdaf6v3F6vADgMeO36bZxM7JIt3mGsxvc/mRQ+StCh0FHmVu06rJqpvU7Cx+SzSsymCNhmBqNn949m1uOieXLIDCtCS3uy+O6k3800var/LNQBA3yIhvhF142jN8cMDuXyW9qlUAdAoGJB3PkyIB3ep+5gXnsj0SdyZ3eK0OnHaAmy4i9psUVtBx3JRX/l3nJYIQVVwBG7ZJvs7WJEBUgZx0QORdUrZsRlidLcmgr85M8YLtwqPJOrNR48kmI82HXvaw7cd0dxEKTvHJJNYYHYMdLcdyemMBN0hSPAY/Pupku+WQGzjrKyKwqKURooR0iZ9YSTnC9UZLPwAIo4SRMyD7d7imlDAuNhfHIN/Hg6iu51ysTS4MMarYw6WQED3TClF5XVcA6jiCrboTsCFQ5jShZV4pHFDrooCpOYskjO9FEKjn5z9vvXCyKA5ziAuVrIQK+JN0jH3oyiATwC6c2YGaDDjgBEkCiJppGQohtIBm6kvYSeF+aex7QCtO3iwslPSyUEnat23L5j0w8mH7uVOs/qmv+ukJ+fn/dQx9bcoTpzsSa5pHUP6TV7DrfbXvf/YLct4oBkH1c97qHwYlLqyGV/VL0i9YyBkuvd6EiPk9pU/a2KMsF+bnDWxJ4XFgsfKwcUu9IJZLSKTcDu7whX+TGW0EdgYTbudapDeKZYkjEq1Q+GNxVF8LMZ1QiTNLsuM1szEK3EPRaSU+KAECbCxGyVsPJHeeowOkf2inSa84geVkjPCUeA5zo9cKHa7wAgJtkXvDzhUGAPCWj0tBFsr/rnDL7cLjVI5WKIn/Ls2UE1gTLkw79GZwYXEEkbGZYFUlPi8k6kJe+SwlwhURSKKOOFacDzOhYNphahe/rqnvsWn5/UjNlVbnYPNo2Bgi/XTXdIXX3a8y2e4mx7Lmv1kyXk8H/Zwiv6h9NhXSx9RBJNTAyzNSPy2kuwt583xCXEFeZUqEBgHn8uhQwlsIWkZjmZAcU3p5ICEfAeWVYXMenlbsWrtqH3QzBSrPomnqfxqO09Pd00DF2u0l3RW9suvrMqncaItPl26lxFrOEM0M8WIHNsGM2kZ2e/IekfKXkNzozHj7kdZeHPDOX5sqjQfat7mpubdWa3nOG2N6G08RcOyZNwWJ6nhJY5n05QV98f+zudyHESMVar3fIi3uEL6DV5t+Kp6sHFQqAfxVJHMeCYv8jgJ4xjhoVortX8yNxwRkR9eSd8hIA854OYLJKAI6BMyJeXPxbw/Be3xENa5YL35Mq1XP2q1/p+qj76OJ/DYMS2EednzLL5Ms5OrO3ZsVUvyWYbyqydEds18LNax/vBR4/q0pXJhLSpVbxap30Z93SMDtuN4lxCbdyMIzsyYQhkkFn9cCMcaDWyD8QQ3Gmf175QD/ac4Cb+wo9GUoNm0wiylsbZXtV8haUqS4lDSXZNYuLFokvxIPK+CCVefLcJ0qYIH0dF/RyxXKmrGhCqaBD2+OqI5RAC8siEDUB6mg883nmPOOHUzQi+dbnSYjon+yb2WqYYv0BF8PSWaprXtv4FqiPfTi6b9dX7cOK/enUHzpQClsjpk19JFCwiwta51g4UbKi+NKWiE3kCYZQiue5kTNknamJAmAomnE2FS0oQ2GwjnOHGai4fpS5ws4ZH8+dnb7snr5N6k2Bfdi9eD85NX3fOrtFV/3X1TrbVgfX5olNsCmi+8WzEDB6069zlTmzDOgviGLyrV60e1x7mAfb/k6ho6f2w8Fr85X2w1G43siKk0rY2j3s+9BpAr919mdAYXY2D9wQ419ZQk46JlLlAs+IaPvFGL8n2t2LaNsHQEGnqNflwGMnvCyrNolikEyRWDFF1cIGDb4iZbn4/JswjbMqZWfAvvI9Lz/P4cwpgvvgOQVc7JH5ZhIbD51Uebq8N222KWm5XKUuhWQUK/dflttuju0YIiMcwA8KlXZsChtg4XijuxG9mUVi7VM8U0JuaMQKLgGaFXaR/Sy+oz/AK8PRydPYvMDFlbkj7g6ZM8GOexqAvnQdJpfb4ginZrqnMwCVHm2tzr+xlrZJ2wk+49i+ViRZkQsngYXjrmUDpUn0yFn7BDoZfYRP7j72jbAltaJbDQniPLiWrLnFTit8CejhMXyyh72IMdlWyr1Adns1eiPahnWKPUXwe9nyhEdUEzoVs7tyeAd1MVrFV8YU5aj3JNmAz97PDeN11UG3hZSr/DWe9yvIiXZSK8rb1hyu5A7r2QDjT4So05DD0H+rHsTImsFP8iZJGTJSCImDsOMPvxGmXTYjOQQvRsqKyaX4l8ef2gkKo/M1Egn43KqcaY2WEIywJP0FU1E68VmTAXXS8qWnhqY9bxmW6yG4jO28hw5DhiZZg0mkmjaQyp/oyHRp7PI9NGExs3gE7AhBUJuB6NqyTz9QnU3ohb4wn7mcK1vMRt6okDFQgCa3H5rMKIqp5VXVg2vVa0sFYKjYNZcvAmPglWCOENTRB5c/A2qXaD0lIgUNJNR4PTMASFwi6qvoRvc+LwBDcmrTY9e90dXJ1edrsX667DwQvwGDzM5NYPq7YLML0zR69QG3wIPHAUXjSOyGEGAW3UDg7xxxkCiWIwkoATujGDN2TwlqCqoEVdrZYnH9+bB9mkysy8kdwoP85NBwtz5zPwrek+GPjTcuZH9zDfGF65L1YmxEg087vuydX3l92tJZ6cTP8cCX/gZuAWf6HwYt5aDSjEB48WdWaXS1H1QMCVXwLFKt6yKxfiChiX0EDQBght5kZhQGIIjitYES+IdB0EfEgoNEUVnjZqShd4Wdu1vIWRRt0V3Nd5G/9L09H7Jb8TnUCM2kwLlQalix7gdRCwcdu6AFFF2Lm6AAlxjA47WttJDD5eMwujy3isebd1LMFS3rCFzKVhDOF2NPEFy42y121S1YjF6igWq5cgVqZoNlimZp5rA72F3jg4xAC0HAuM5x3DsNZ34voHNMn1O6E3j1o8xMYokyqQoKcJbjC0pn58BTvBk6ZzJp0EtSJe4M22jDp7gWXUOgruZt8R9wFAm30vqQ8hqYjmYb6zVdPKXxI1pD+QUC/y/b9qCDFb8xmUMkkHeFStPutpzI2cshjhQS7G+VTCAYICD8SAaTf4p4fxGOR1duBacVM8GsJFeENxoXmaW3xA5GZciJhUA/8IlgNhyDXBoo11jxd/KsO72XT82NPDYy6JKWB1BXtdaQsdsxuhWM56Jd56qd82auMVxJ7Ytijxvku0k7gPOLUCm8wp80qqyLeocDBqsy82aVsqTNviw/658l2xrD5UJ++GOImLmZJKTLI2KtUzAe6iT8GTvYePxpEM1GIc0lORhYcHMVSqsgChjC9cxF1hiWZwpInrchUcFdoL+j0gsTBDg1M5/MMtI8+/z+YW8d2wpPTxQacfFVFljTaK1jKuY2uLl5jSS8vZ2qJ6dMQtqgABWg4a3MKxQ1vUa+k5eFs0UWLSVFJABREARi9TLKj1UwkPetok8ELiZ3xxZUSPAFov4Eil//jhEa7wVfPLSSGKbYWNxytRtpQvbxbo17meuPFZBcX5LCT7JXjTSLEHXYYMlfMHvAMRhgus2E55is0hMqHLw8xMUR6xOKgKvBlFxb+Np6QaH6/2RPMVcgVsFS9KUyjH9QhJWScmQi+6f7xOyjrRmCE9qkMzBjNEmULojxOnEH+iJkqKDSkooGtY9GtHmQMrAlatqc4qmWuLfn1psFDuIf2mRqaaF3p0TR1yIYl+NBF0TqTKtNIM7DELJPz76T/+QtSkrcFDowUvCxOzqxNtaw5D/fXUr33QiRZlZO/w5220dJV4asoh8i9oLERNW0H4otX2W3TYh8yBUTfiGfXTQc1oCj+Ka+HiLablyCL/IyPxZtvQzr/egQshbehWKsbZdNmjzeVRTDDssSM5w7G9fvEmVVUqzQ0mIzdzOALyQcsFKUyNfv8lfcX7dTgcRwIDhJ5L+09Lg+kxlBB0AaLw9sKhtlqtvuTCfHLSwuzwYJHjFlVzK6NHK0YfnfmRM6Ddv/+SXRYeDd3uUhX7yLxFV5y4qpVIywIPpKXj7Ft1ZUprtH6Xb4iFLNe4Jj3UBws/tdBV7SI5oaPklSpGYTW4UYe/VQWi+pD5XREAfE5ELtX0vsqVffoSdvEsredz43rHtXOtBEgW6m2oRumo0rrFGMfyU1hi85dDsDF7KYk2ffGg6D1a0rADzDuc0MWXsFPLwaiOTQd6UHi0E4QEJBfEgP3Rlxt7Qsue2Xzyy1pPNY4FNmTqOZayIfEVDjISYNDg3yP0s+wZcC9AiwWKfbp4rX6MytnQ2r+JWiOv5Kla7eFK3g2eam0taX6C/nqo7ip/0aao0mVDhPN1Lmb2WoqwJy5YofAxXyO9rqkzR1VU+rqUAan0RM7JEjrmnoJTxabKNuLMFXWXErNpPDuwN3b02o00IT5MD4v5Vw/H2k/usvTm5Oy8hLxPP1zIXF96/11pBSaafiuxSscZ1Fte7VPbigECbfxcwDT0e63LUkWUjJ89Oxmrrc2q4S1r/JVPxKteX+sFBvo/gnj1BA=="}, {"path": "frontend/uitest/test_ui_index.py", "orig": "ba2247a3fcfe1a17b74baf2de1090035d4748d860f4f58adce99e9d2604c023c", "new": "838c74934105c767d56b9545fe4c2c6792c41384f1d67a4ccd6c076c6a42e8f1", "blob": "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"}]''')
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

# ── prerequisite: TICKERS_UNDER_20261120 (the hero this patches) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "src", "components", "calm", "CalmHero.jsx")
    if not os.path.exists(_pf) or "TICKERS_UNDER_20261120" not in open(_pf, encoding="utf-8").read():
        die("frontend/src/components/calm/CalmHero.jsx lacks TICKERS_UNDER_20261120 — run apply_tickers_under.py / pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="week_dateline_")
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
say("  UI check, APP CLOSED: cd frontend && CI=false npm run build && PYTHONPATH=../backend python3 uitest/test_ui_calm.py && PYTHONPATH=../backend python3 uitest/test_ui_index.py")
say("  Undo: python3 apply_week_dateline.py --rollback")
