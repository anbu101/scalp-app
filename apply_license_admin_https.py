#!/usr/bin/env python3
# =====================================================================
#  apply_license_admin_https.py  —  fence LICENSE_ADMIN_HTTPS_ONLY_20261127   (on main f0781780, v12.0.27)
#  TD-111 — the licence server's /admin/* only over HTTPS
#
#  The License Admin page moved to https://139-59-8-202.sslip.io:8443, but the
#  admin API still answered on http://139.59.8.202:9100, where the admin secret
#  crosses the internet in clear. Now /admin/* answers 404 unless the request
#  came through Caddy's HTTPS (X-Forwarded-Proto https, which uvicorn trusts from
#  127.0.0.1 only, so it cannot be forged from outside). The apps never call
#  /admin; activate / heartbeat / liveness / min_version keep working on :9100.
#
#   license_server/server.py                      middleware: /admin only when the scheme is https
#   license_server/deploy-license-server.command  its own /admin calls on the droplet say https
#   license_server/test_admin_https_only.py       NEW  TC-LICADM-0001…0005 (real uvicorn)
#
#  Run ONLY this script:  cd ~/dev/scalp-app && python3 apply_license_admin_https.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "LICENSE_ADMIN_HTTPS_ONLY_20261127"
ITEMS = json.loads(r'''[{"path": "license_server/server.py", "orig": "31acae3eed0297d9f6bbc3d3481c50eba86e221523e954a835699b4e340328ef", "new": "a860ab4705b4242e5f99d46a7151c9abada719f2f8aaa09059242782aa9dc474", "exec": false, "blob": "eNrlPWtz20aS3/ErZuGrMnBLQg87XosOU6sHbeui14mMNymXDgcSQxIrEOBiQMlcWVX76aru69X+hPtl+SXX3TMDDEBQr9hJqk5JWXjMNGZ6+t09I9u2rTga8URwX/Dsimcb8pc3X1pWfxTEc3Yk37M+vWBt9jYQ+e7ZIQvmc/ZHtriKRmmWMJ7k2ZLN0yjJPcvplXcs5HE05FmQ83jJTk4HLAlmPGSzIErgMwBwGIwueRJu6CdBnPEgXFr8UyRywaKE5dNIsIzPUzZOM7jj7Gx5mIg8iGMYUsjFZZ7O2XCRhDH3XMs6XySCpQm1nGfRLIDBHESTCDqcjniQsDBL5zHP4VNpMhFRyKlpxuNg2WIBfBO+m14nlliKnM9ChlgBPDBHIE7aCmeeeuy2sDFMN8uZE/JxsIhztrO1uSlfWP1/P4pyzg72PHYWwyTZ+8HgjA2XOPJoknQA/GiRRfmSxdEVVzPmrBduf/PN1k4b2/DQylPAkmDOIgEkTHgwjDm7jvJpusj1RK8AyeySL+XcAWfD9BOMIUmhRRYkAkfoWVYvCWlpRMdi7Oy0P2AbwSiXvSs/COqPsFKjaZRwPwpZ+zs2jJJQP8L7l+0wWDI5RkZjLIBOeZDlQx7k9wK9CuIoxM/D9TjjYqrAVuEF4SxKNv61OkY2AmqBnhuwfFfQHC4WSXHJP+VAWvhsHupWOIMaTMFzH377QOEiAuTBPeE0Tq+5yInU9atRmiDBZDBbWLQM6N561xswDQlRHMQbCyANQcN7G3MAdpwmUQ60K1+zYDRKF7AAgIg5jC9KJrD0ySX2+Pmf/4D/YXTng90j/4d+77zv7x4cH57425vbr7a2tv+kmljmBNRno+Qqkot4gyNATmuxLI35Lfv5v/4HyIK382jGcX6LOX2SOUnK5oEQ12kWsinMy20CjPQBk68Clg87gFKA9zmMBNLkZ57Qr0USp6PLzxmfpVf88yhIRjz25fhuAfsmzmLgc3NFga2ZYjGgd2rj1rosoioR0FN2zYcsDMR0mAZZWPQwF7b8Ofth7+hwH8QPLq7g8bitSCRZhBOOn72KRJohL8XL8vNA03E+rZEgUBehdTTlo0vL2qXBcM1lgJ+/LaKMA3YDIJwOYz+2qUm7z4F48w77VtCFkJPz5Z2Xf8q/sxyktzxIcrlwo3Q2D4D2XI8NgD4HPOaTLJip+Q+B0Z2zaQDi+qXLgP+RhkckJYHt+3mQLxClAHyRSXalFogBh+SF6vL28MfeAZOAtpHiQXqMchfFRXrJPgODXSYg2Hxk5c+K70K44p/mUUZXmr1nkZgF+WgKj0AK+VrMhJal6Px4d//94UnPPzzw3/9FkfjWjuaCkJPYRQRy1AUzkAmjIINVgXkHo6VfihHLwZEDOBaFLhtykuqGlKHXU6CLa8AftvHYrqQy+IDII6C5IfAkIsUi1qcPQEOGBCwQVwY0IKVSZgJfG7LOUWvUDpKwbQFntMplmgOnu2/YBsr5hAuBgoDPYWIcBDksEjvXMzUl+zjiMcxvFEccyWnIQQFwtrXtbXqvcarT4IoD7mE8oGED3cCzbFDvVjQjvTSFwevrvwrihGcay/tv3/mnH3rn54cHPf1MRH/nWiwD4+iuqdBXGTdBPEBcqX5IxtY4SwEZQT4F04CpF2dwWwxXmRWy4RgsjmAe6YbKAGmx98RPLVKnvU+ISByp2cUD0TRPSY6ozu8Hx0fn6mGL/Vv/9ETfqUEtQ2C2aKTb7wEPHKdgw7TYW1oGPcJwqK/0YvrXROkGVo4OP/ROev0+Q1y0N3fam1s1bABTROOlvpPC1tfK4wnYleabP+N5UDwC1QxkZ1nPWPvRP9Bp//Tk7eE75vDkqg2ckGVAEiDg3afBs3AurAs2Tu6kwgOgUZYm3oTnjg3yuH/+wccWdovZaEHZrmu9R3XUZWta41tsvenRf7Zr9Xv7571B3z84PIduSFbrvmS0BBDU0vfHUcx930XSSeMr7rgecjMYshvMVnIaR/VUdPYHu+eDH846aAeh/pcg4eJg74kYVQvsxWkQ+soKRNHsGNNzLUk28hGgxXyJM6trHhsRAPBysKAABSLPornjWuHQi8CS8cMh3BjEJp/iFTyXRA19QCT6pBOWkjd45gAAVPe+1u+uVWUf1Us3dx/GTqAogrA9C5LnggkQnSOQJKjVulpYOHmUx7xrN/k0sPRhOhL+Iou7J2AigcXE4UFxD2ttGWPY7530e4oLUfL0/dOTo5/qDMmcwUEbVJlbmq1oSDDkIBJYfebsB2G4ZJ3XL1++UPpcD4zMA/joPABTBPVPiIog428Kr4b8CClQUYOT+lekdJ0uYjBOs1RIbQ6sBhYbvCCQoxg0lcfkt+dZ+ikC4UiiDwZPPLRFygeMlLdpBsoy5GH7LEvztMOmeT4XLXY9jUDOaRH9DHyLBaqsGhCaroOelHKJwBMRKQu0NgelmwXRZJqjau0gs8OTYCkAoI0fshkHwmDRGLwxRi4PTgcUrDSjJMJgkQVLOCIV7RyF7Dc47ygDSIWO3mClht4oRDZcmtbhJedzQF92iQY53MtROWOAjE6q61kwFeZLXoGH4B2EDqqxDk4Gpgc0O+N045KvlKYxGk1MmVzkiWF7SeVIqVPHlmO2XbQjJATW7UocCFDg1p9hkt4sCsG5RdvFkdhxrUAskxEzRkQ9fMS7o3DcIqz4CTCxKwcC6MRB1OagmntA8h6Or8XMJ3JQCoAxG1N7Ojd2CNwfxXaH2ScprhgYU/ZtCxcVzE5/BEq0+3LzpWviI7gOYHGLMepxPF26Hp8e9I76TxSkoxj8INYHUTfKSec7hfZXk5/hNUwlGUcTEC43Ngw6C3DKQKDDCOZraTAHZLoqe84xgCpQIKCJUFBGoWHh4HrEPJnk0+7rFtian/TdK4Wz0vx8YMet7dey54qxLAF8ZijeCjiKTZUMvBMqCuVmy70qK9fK61xbxOBxnJ4NDk9Pdo+YEwKOkAuSRRyDmTxN0fwkiQA2NZcCRjkszylAw0AUI/mArxVkKF3wrQ7uIDeDcEih83zpqWU5Upz//29h6HvBkMfVQWrgtt04t1x0UIFA8015rxauw2ixipHiL2qgF63DyK2vfcYfg0QGr7qLL92CWfYphrN+TRrGbaBhq4aGTTX0CJ1t2ccGs+vkYPf8wKZXcoA4r8oETJf+mXwCUhzhaB3GHFL87MWrbzZBeWiobAfvBueHQMV/kl9H7ZbzCWhXiYmP8OCi/rnmjyiighml+d3DLPtvYmAwjmYRONdyACDm8dtq/nLe8yk0f9D6v9h21Re0k3h6MtjdH/iHJ29P9bOUPK4gJth8BtL/QbAloT4GNgoAlLm+ckAoiCHtop//8U+KcLVjsAJimPU1cwTn2uyaxOkQXKqiX0lz3/Pl/UKgaN2jQOIDOlTJS2Niknc3W8DuXSSccgx9nh9HyQdpfqwHbtgoD2QCWL7iIz9QXOuBQy+ZZi1nKLqQjGDwgGKAh1M/gRmDrMfYfhyMwMhIcmreYtybeOyj3d/fPTrzP2zZLXtvz/+wbV/cxxxSXoCU92EIGFntkBnW0GgR+UQz9033mfKObGhjYywOdE0o+UkGvIQf5HcDAbcPvC7pCoHgzDiFapyf4Kd9fNw+OABtB16/JloVtQa7GKx0oqY6R6/OxmDtu9XHCo/fyYYK0iLJ0wWYgWGd1R/6LcXzj//Wl2X95pjXXu8dkLJ88/v5HwZ78KqjQjptimeWATmP9afBXE+dfC1ER+kMqlCuAPygH7TIp2lGDhjAWGAGYEZxLsEwHEoeHA/fyKzRZAEEDmYVuuAAZTlPoVUSAhhpUhFcI+HDHOkRYmQWvUngYvS2ZxSthLEPYcQzl4kZ2Pme9f3J6V9O/P7gfHfQe/cTWCl9tKRprQ12L65fGNff4PXhvmqAF9t4Mei/o0cSBsoJei8FBly831VPBseVK3xpRtn6A/+8d3z6YfeIrKbNnc0d4x2LQkG25VxRpv2hN5DgDCDyGfItYEfava/b2zu0OLNICPQsoxBm/BI8S3AxNXUyEVxJRrZPz/dWwMpnFbBgTr94DNi98+9XwMpndbDbjwHbO95fAauDJMUi93862ZeW6ObWK91K9iRZDeSEYXgawdZme2vrjcyl+EqPLH30dTE5jExO9CkkraK2sG7NjwNZ9M+ODgfG/Yft2oJsAl+B43kdCD1NFZuQMkMJnmK2mHWWYLBHxv8K8hugIVIQUxQ2wPgL+uMUO1FOiiVjp4Wg6fvHuz/6ez8Nekjzr7d2tg2JhK+OTgf9MqQlLc45MD9lABhqPGCyOY0nSZO25HQdSrMOem93fzga+CenJyo+VcDssm9A+pULopr2/beHRz0dJS1jn0bEUy+AGo7wMHtgW/7KC38EXgxv9AssGTehAOVKP4dCJdhLmiO2bR/hRDEOgQFSVOPc55/4aIGCz0cfnITWPONtiksCQ7IgZxNw7DKZrUA4Dtyvfg1oyJWKg6v2HL6R8RAskHRJC4dhDXY95RRWIlAasQX65Lr2gUiDZFJm7DFQYytTW1ISW4cnHYxZi8d6sOVOQOAHqBjYCIfBxqCfebjBZ/N8yTCOS1OGzxXzi0GgU41EGIlLuMvVVJ8xh0wviQ9E7BheKSXEKGzltggMBmyKWN48Gl1i2QRbzBUYncAKiuIJMJSwuyftzGxZThFUWgC0gpRFUWzhrKFUMybtFt0xRAg8jKbZiDsIrEX05KLWIuDlp/BnHSphCNi40lZhv3jOKc/EinRTCRmT6GZs6+a2Ym802DvAvBHoRCZQk0up9gZzuFOZ8ZOqWftBSgMTy7c5hnR52JIhj0hIA0Br7HR4FaULpbll+QdIjFkA1C8ZUZdbYCQrB4fcWTEeW6s2HrEpXhSBRGpCOLb/bNNnMO1N/UrqDSKwaysZOqcaEQS/SEYOu7YEOANbhJLOGOJ+/ufntqsFiDnuiknojNKOWvL6IGlYJXWMUkUbjxuixe740cNfMVRpJkNgSNCcQ1QatiRalT8HehNpBpoErNYcRuZhSggIm7XZqpHk6gmpznePvzLeOyejBj9eGX2nGKZmFzAFHOGyG/X8Vs0GtRFYpy2U2GAFRlgj40U5n8FcOiaT1paCmq+sxoNmdO+s7pzZxxsY7u3F2uWh3HcXvPXEIZEULmZzQZQjOGjGIE8z0XXAJQVDsmO7brE01PE7tl7rf/VFA4M8BWmeYQXLDQ7ndg8GZDeR79i+WT/Q2z2pJUGiS7hRUpTJrYFmegQtZROBN8+DxHbX+F29k4PfndelfK8nZyDe947OeudPTUGQmFP1QjJH43zyzdxsRSJXhFy1HYXRU1n34amKFD+MJhgCqrZsMTM1/Ci5uFWK7igh4VzJRJaCO+RJhGKOeqtk2YwLEUyMbFnFBDyAHgEGhnT9BupCqpvcBqubjGxgW/5pDtZvlCtuAcFJCQBZEEqgZNGMSgpzsnj+Uzb+zxaprqIwUrnSwEjA3pSKpMI2sHvCQsyBrlzAqIDrEo6ZRazZnGfpEJxdAQZnFox4+zpKQnBS4nQSYXYO6/uELsh0C/NQGwm2HIzdUVMAkaIQA4/UVQsLDyjVjsUzNgW9HLz08B9QGLcaz+DLLLgDKJHasFVPTdTw3MyVThlTcSuhEdXn9KSnrdvSH+RkY7Ddo6MGz6QFI1Y+E9jp8CZXoGgSbF8VNuESF8GfDi5lUQxMRqzqZIYXvBUBWCSJTHnkyYy6vBEKjk4bYzlZhHVbNAwZaDl4waLZbJHgAqtICmaABZAVFo4ly3WxKWiBFXDotKnZY38YCj1GkSoW8FJwdEcxpS/NYU6xYlwZXL2PtjlH+8LVbbx5OndWhD7oIWmg4YgusZJNFa+Zo6CwjyhMN4BFlTA6CGq77A9dHegsZQBmOcMhNvXr4TSnYoBPqhoc4H9cHecFQJtYZVB0jeeL67I0yEgGmXR0KSj8YI8d0TwNiEXZqFoHHSjG/AhMr0iR2NplS+OwjIW5bwxQ4zilYvMUkKcKJbVLrhKN6IpTYXqepVRowA2v52DPgDWLJpkK252cDnqdmgdPtR0IKkkrYwyS5QxL/YBsDGAwGD4ek/zBkvpFguMIMH6MAT6salcWQCEdZchPOnY7OzuuV7fKCnLQ+LLdhvUsXuI6rg82PFS0lNpfJzlQQHR1wZqHD3xa+9Iy0rsVwFLuEqPAhX1RGkelvOuWl+VrzG7IfnhldjQZrgv/tFY+qQqZ+DwdTbvAE+a9YtoiGwAsKwFIHoHWJNX8ogbFKQdfKYi4KT5bKgY7vbTL4diEEVQOhK+P6v6i3gLHa7TCO7NNVaHoVhEO3YSEWMIMTgPGqjKqwypIsytYkzhRcEwsmR0wxavbyBv1+rbB/ZvNwBgp9JzpoRr3hTGxj7FEgX42yoThsiwL+mNZFoTFt7gGKGFCaX3oegQEVmhuYJkY60OF8Q0zTqNsHdsokkZHQZd2Yc10UtTHuAbIj7aqpLYv1kPVTQyIUyr5BQmp3xVAsVpPqCUIG2h0/WdUH/jMuPiOru4GW+YGYT0vYT2/0M6gAmSG/ais2kcRZ1omYOSuXa8H1YZTkbaq33Yy3kYLwaW4HWpz2vyiyrnluP7mGRIBi+oouJ0r4TuOMnAGZbFZJOoF4RrCSr3GGxnnbPgqCO8UQWuRVKMflKYaCMrTbm2EK0sDfYySEdZtHk/xBdkKDVx1+Yf6F+TLppHIHhXXHShJ6jCuP2ZIsJbq0ap9oaZDVFmo/OWXOwQIrCJPzfjsAdBNCV2aKUrwrAhYg3XXTnzdEjQsQ5XOn+grHvQ+HO6TG3x2engyeKrTSAWC8xQ8O7vYlADsiMynb7GyrlOtSdOlNU3IQ0QA7nTZr7eYz7EYV2kzKRy7jQK5lD3UaoWM5WNrLSd0aqtakx5KblSMxzuERH3lV4R2g+xbiW7Y9c0s9mqsqRCS8AXtshSbXVBi1qVBQxTF3hWXRgIXRIjcqwaCxat90rVMz/8OJMK6IohGpq2LxEaBInF9R0nZoxjwGXuLclbuZcKBoQwGJYD7YoypA76KLTMOSGa5l4ZqgPMSWFWeKGxHkgzrkkQZL2z9/BVKNTEUnnRDuwrDFfaD4rjSxPudstw9FFO3BCrbtkyzA+GUJN7W9dfasiKFaiusfjFefqjxtcqyFRYtlTTYH+BLUbZT9bEfQwlP2pvgsX4wI0twQ2+kNXZaPcMtXCVZUcm/WIxGXJXzZ3yEWzWjwselKBa8UHsm4JqCJc+YU91a4cJIRhSkAHOFhBEiIOYZmL4yfUhTJQ9boUoGTyr0rmEqcte3ktpr1bW/Kb2zB1OW2n3HIiqAkRsucAs4j4GdYepc7iYv1+ROVkKHYfWhyo45DxO6X4uwyQeeKOOxrMGu5E/NVzqPymMAX5SaYR55IsMVeY0Pc1kWP0yV505xzczJJzK8UD6n3Q627X7sbG2/vqgrVHs0DXJEXLW7flp2fvXy4rao+DN2EUkmWUdkK7rvjvQeLRDqkhZMrsWwkNGZ4rA3LyiKQwkvbKQLny9wSps1tx59+A4bZAt+q/UHzajYAV1hKhmeL1irKQPQVbsfjRpbt5oP+MKR/5ebL1p6m0OIC2hVrOLqHq4kmItpmjvu021jWV76RU1jQrU8LqCCaPlISrBKJXqLrU++rK4AhSAUFu/M4UjUrWbhkYooEy/pk7Lh9xVNEqgiDN/FqgNFAwiiLMNtZlfd8aNdtqToXrWzCbKIqN4DsBIsNDuuln8U+kEuRKEiKlxJPNgtubHykuJ6JLjgopYqBZakV3jRqgc1iwBWEaXuFshUgZ9W3YnlEh5dVV/S4nWLZdRip/WQNSwGhaveLdb/0TBcs0zlAwZ+e5jRQqOCP60sAyUed901aSuKThbxPhnAK3dBvaOkwanOAKwvQl+tPkDN0yQr63mICis3JiqeysRlJGp9EFs1UEeBrMmD0YbuIgD0ANmwFs0rKZXO+vTMrdUk/+5En2gCRHKxeSHvrouBn18B9WrvgMz5cbBlb27lJlfBHKHTwBwPzRmBl/kGQ3ujy6XKh2EteSA9TpUpVlaBwOQZO9hyMSyMxWFSbtVTU9p+D3I6M4hq4oyjIH7+7/99NSVrBXcj6wK4RxDCHSVPKCLqD4t0w7qFXNPjy9Kbya0rpXZVelspBv3CBFOrpVU7N3M+m2PJoyjOkyoTuDzEg3q+CLMWU+6sr31dYw6KvGYKrhhvX9QWuU+sq3Wv7NxfJ2BkFqIyfPlICpJy29VXta6UEaxYQWVG1vsDaJk/qvTlZVn60pTRaUZoM8L0cVUVlOmHv2ekvQ3ALfytsCb3SFVwJh9JjFX2631VpBXWq/z8fQEOadujMerWwghfHZFNZloDOdJ2wSox0iOJ2MpuwtYvUfASktz5ulHdNLSh98pR5Jz1fjzsDw5P3um4lKxfUHpZBsUWCdYMkJpdo4u/7YIqflMWs8bBIhlNpfGpyvjz4JKr2gqEUm41V7Xuj1bfymfSRTamz0Rhu8pLHSJSRTgtY7Oh+8SC6gK2LnV9TrCfo0XyXEN/bn9xh1RWAj/WG6VeX8wVVdDu9kMVJHO/6J3QzIYFRPPhfcu+ArEowNLQ9IM7/GTJj81+8jqx81B/uUzqSye0uL3Dd6apdOWifzGvWWORHlY4p4hGfgnHWn9G7j34ZZ/5ur732tzHb6ktZIqyZu3ho9/IcCk/3phd+DWwVtbv2sfKPdSpTvJLKVtGGqXMlslhizW2Tu140hWX3Xgn0d50rsAv0tX9xxyI6rFdPJZpyKG13J4kpuk1gQqYSMc5e67O2gyuAL14mtxzNgySBAyB6rmbhCjMTA3xMFHBlJBVBYw4qCiX2Q49mmW6YHQeoizVBhDzxRD8linulpKwAjxAiqVjyqs/XJebp501IN0z7jXZ3UcoxpJ2KvAnNfju7S8NpP+lt8cOdvvv9053zw+efHYd4D7mVKkOgggPJ5MJMzqmTCXMYLUJ46MWGy5yFTChDX6UII5ygDMDO0uwKVppxeloKouZycMt1XJI4656XKo6/6tlUSU4VxVpehhgrGHlfcIEtMWt6VPcOUtbEgSisg/OPY6WTnX1jATu/WcsqiPdXrh3n+nrAczi5DRdOTyTbZ8LdRobtMcztGHg5YFpjnn8I+5TJeQiutSH0utEIqT4FDukA3UFnWsMRCmKg7XKs37lYXN41q9n+WqWJ7vHPTI3KEEUxWDb2//xMWj/fbO9c6F+e3774mar9WLr9l/s8jCRMxqj/KyO5tZPydInBNfOKikPg3ipTujBaN6aE2OuIn5NR/OZZ0hs1sexSycQ/+Jx6IOM1zTb3iw2ssgl0ooCaNYfpuFSfU/v4MBHWMdcbVRsEpNbVb5Tm+rlNh1sIENAIBoeoaUUsBWwZLt8s7n9oI2LaGSUA6ANMCrFWiV09aYi1WhmDSEs8xzsir4yifxXCGnpFavw1liyp2O/69HZoRuaKfV43UY1XDlku2lS8o1UwQ2M8lVNoAfN90wdllqbsJ7SveH84ufG1sxld6TXom4LiwstBQp32MjlqhUdRX43diUvNmFXvjGxW2H/r4pdnJr2z9bMtHK8oiFnParTcLDLvSl2MPztIRgnBXIrYQR1CLuKE1RWGDySw9OT/kPg6129BaIfSz3jFfK5wbHebtyUg7yVPCT16A+Ha46ZaDpiV0WcIm+az2K7SbQsompwKnLKwofKBz35FySc1XIa8xRop9pn5YSBpi7ltoVvpy++qwy59BOkjQ8GQPE3NWj/V1n4aWPV+iJRW4vo/AmwWiLwHR08FTOdU6Qkd71vN+ArBnfW/RS11eTpp2aCgfgB7B4gITDAm87Dd58KWh2zj1jtmJszyYVAi1Cd4xLk8nCJxZwsQVW3As8nAVYmAST1Nzm0pQ+2D9XbV/0FZQmqXJ6D52C00zlP3PJ0pPkcgKFDgocaATvUPBFbeSItdC3CFIaAf7MEE4na+yg2igCcm5oRjydX3srtAIsEk8HOjAcJnUKWpPLAh8WsRX/hQHUS7PR7NEQNMl919Sq+QKfqVfw6XsT73u7R4P0vqcKRc5N/P6GslYWblRnV/SR1gAg+qPz9FftBG2OfOOHeyeD8J6o9euKk8XAXHwWj79N5vr6PmWbfV7sr1fHJHggAZLkWm4Iy7OJp5i0S7V1UIS10ZmQ4ELdYj1O7RX8yQAgfXnRlAub/AIP0ml4="}, {"path": "license_server/deploy-license-server.command", "orig": "ba6f500f2ba1512b6006254400d9f5bb408d872f462a6b1a6fa7e1fc9372df8d", "new": "b6a3b7ccac3a09ef08d1ef61204a41a1c3603ca5d0951f71f2bff64f82d79e43", "exec": false, "blob": "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"}, {"path": "license_server/test_admin_https_only.py", "orig": null, "new": "0183fe2fdffd4cf14ebecc96ea891b656a81aad9d9702dae65f863e185b19b1a", "exec": false, "blob": "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"}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["root", "license_server/test_admin_https_only.py"], ["root", "license_server/test_portal_users.py"],
         ["root", "license_server/test_machine_id_hw_server.py"], ["root", "license_server/test_strategy_sync.py"]]   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"license_server/server.py": ["PORTAL_USERS_ADMIN_20261127"],
           "license_server/deploy-license-server.command": ["FLEET_MONITOR_FIX_20261127"]}
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
                if os.path.isdir(os.path.join(repo, b)):        # the build tree exists: new folders too
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
tmp = tempfile.mkdtemp(prefix="license_admin_https_")
blobs = {}
for it in ITEMS:
    if it["new"] is None:                # a deletion: nothing to decode
        continue
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
            bak = p + ".bak-" + FENCE
            if old is None:
                os.remove(p)
            elif os.path.exists(bak):
                shutil.copy2(bak, p)                 # bytes + mode back (also for a deleted file)
            else:
                open(p, "wb").write(old)
            if os.path.exists(bak):
                os.remove(bak)
        except Exception as e:
            say(f"  !! could not restore {p}: {e}")


try:
    for it, p, cur, build in plan:
        _mode = os.stat(p).st_mode if cur is not None else None
        if cur is not None:
            shutil.copy2(p, p + ".bak-" + FENCE)            # keeps the mode for --rollback
        if it["new"] is None:                # a deletion: backed up above, then removed
            os.remove(p)
            written.append((p, cur))
            say(f"  deleted {os.path.relpath(p, repo)}{'  (build copy)' if build else ''}")
            continue
        t = p + ".tmp-" + FENCE
        os.makedirs(os.path.dirname(p), exist_ok=True)   # a new file may need a new folder
        open(t, "wb").write(blobs[it["path"]])
        if _mode is not None:
            os.chmod(t, _mode)
        os.replace(t, p)
        if it.get("exec"):
            os.chmod(p, 0o755)
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
    env["HOME"] = tempfile.mkdtemp(prefix="apply_home_")   # DEF-113: tests never get the real HOME (CL-22)
    failed, envmiss = [], []
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        if not os.path.exists(os.path.join(cwd, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
            outp = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            r, outp = None, "timeout"
        # the project venv can lag requirements.txt (PyJWT): retry such a suite with this Python
        if (r is not None and r.returncode != 0 and "ModuleNotFoundError" in outp
                and "No module named 'app" not in outp and os.path.abspath(sys.executable) != os.path.abspath(py)):
            try:
                r = subprocess.run([sys.executable, t], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
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
        refresh_master()
        die("tests failed: " + ", ".join(failed) + " — every file restored")
    if envmiss:
        say("  note: some suites need the backend's Python packages; re-run them with that Python")

# ── 5b. JSX parse gate (esbuild from frontend/node_modules, if installed) ──
_esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if os.path.exists(_esb) and "--skip-tests" not in args:
    bad = []
    for it in ITEMS:
        if it["new"] is not None and it["path"].startswith("frontend/src/") and it["path"].endswith((".jsx", ".js")):
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
say("  Server only: deploy the licence server (./license_server/deploy-license-server.command). No app release.")
say("  Undo: python3 apply_license_admin_https.py --rollback")
