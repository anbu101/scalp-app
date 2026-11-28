#!/usr/bin/env python3
# =====================================================================
#  apply_router_forwarding.py  —  fence ROUTER_FORWARDING_20261128   (on main 3e2a331b, v12.0.30)
#  TD-076 (mutation round 2 — execution_router)
#
#  Every strategy places its live orders through ExecutionRouter, which forwards
#  each call to the broker executor explicitly. The existing contract test checks
#  that every method strategies call EXISTS on the router, not what each forward
#  DOES: 97 of its 383 mutants were reached by no test and 60 survived (79.0%).
#   backend/app/execution/test_execution_router_forwarding.py   NEW — TC-EXEC-0293…0297
#        (61 tests, 0.1 s): the exact executor call of every forward (positional /
#        keyword, defaults), the answer passed back untouched, which orders are noted
#        as placed by this app, place_gtt_oco's limit_buffer fallback, the single-leg
#        GTTs' optional arguments (sent only when set and accepted, dropped and logged
#        for brokers without them), and every degrade path on an executor that lacks
#        a method.
#   scripts/mutation/mutmut_run.py   the execution_router module also runs the new suite
#   scripts/mutation/prove_kills_execution_router.json   NEW — the 157 mutants main's
#        suites missed and this suite kills; `prove_kills.py execution_router` (~25 s)
#  Mutation score of execution_router.py (mutmut 3.8.1, 383 mutants): 79.0% → 100%.
#  Tests and tooling only: the app is unchanged, no release needed.
#  This script's own tests run with HOME = a temp folder (CL-22).
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_router_forwarding.py
#  Then commit with `git add -A`.
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "ROUTER_FORWARDING_20261128"
ITEMS = json.loads(r'''[{"path": "backend/app/execution/test_execution_router_forwarding.py", "orig": null, "new": "d94170c58ea329b2ac4e53a6be8bda2c9ecfaca6575ab845f793de4a71e58e9f", "blob": "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"}, {"path": "scripts/mutation/mutmut_run.py", "orig": "80d1100ec420461e5d5ce9e6fd677ea5c5c5c1b4e3e2342c79affee2eb85ef31", "new": "3e68eaf91506c5719f7300d31478ff28332d0b56e2091f64adfc2f33a18eca75", "blob": "eNq9fdt247aW4Lu+As2sLJMpiZZduR3lKNOOrUo85bK9bOVkKiothpIgiTFFMiRlW0dyr/6I/pd5n0/pL5l9AXgTJds10+1VZfMCbAAbG/sO8It/OVwm8eHICw5lcC+iVToPg7eNL0Qyjr0oTQ4Xy9RNvTDAC/jnxMvAjlbiP//9P8R//se/wz/x4dc+/HOO28ffHh0dfacfm/2zVvvrt5b4P/8bizi378+vnYurn29VyeNjYZ713rXa37+1oD2hy/U/XDu3v/V611vlvoFyUPI3Ke9kMBG6ZyKVSeoFM2FyFwW0GU5FOpcicacyXbVgKKk3dn0xcsdcNZwsfZnYAO1qOvW9QDZFLJPlwh35cOlCES8VgbyXsUjD5XguE4Lne/dSuFHUEfhqpeAIwEkivACgeWkiwodAjEPA0TKYQP1W6yGM74Q5kVN36afi3w4T6ErU0t23muLBS+fil6sPPRGFXpDKiXBTAOYGQi6idCWmoY+QoAcx9O729OTi2un3bvvOh6uzXveoKUZxeCfjw7705Sx2FyKR41hCV5IUJjGSEwA2jcMFjQGm2YvDYCGDlAbKIwnCoOWHYYQYEkkIaErFyMe/E5uwLjRtPEcZrVYi02Uk1E8YjKHPYYBI7oh7JLI3Qk2UqTtF8D/vR08pQj5IxDV18gfoBXdXXJ/0f7Fe0//Kj+v7mlyESTQik1fB47lx0ngJk3zn+b6TwHyP56/E6XKxcGPVuxTJFHG6ajLtBWE6hxXwOpBTGM1cyEcvdcIY6MuZxB4SvBCTOIxoPfhAQ1MvTlKCHEYIJ+lokj47vxGi9ae4FGbkxoAo6dOiDNKkKTS5n17/mrSOrPIMt1qj5WQm09bCC8QHYSYpNOjq5eROU+jGBwEvl7C2fyCyDeRjioMVpTkoQPS9BChKTVUTuk1/kqWXUmEofprGfusUlrdIfCkjEcZwOY5dwIIfJjC/bgpdACjYnhoILGsBDGI2Tzuaau+gbgLgeOEwmrDY3xEpPx7+nbvw46GCcPjVV/ZCpi4tNhfK4yCSOy8ilrKwxQnAUiN/mEM/YPkt47HE7nHvxXjuBjPgCokHi4l4jJ6cB+AIQA6wApBlp24Mz9yZC70ZrbCg9Ke2OIVJC5FglmkE/feYl2mKImL6QQ3ugNgXwFJlZyFyvrBubPDP9sMZoji+9+7DGNYHIr2DKx3W+MSbTi2oC8CqtbMadvqYMrvFHjFZ563d/vrhw8nNR3uBLEjcphPfGxHVw0JEqkfWjwNZJalcaLrXIyFenRTmxUYOYdkNbxGFMbC+eAZEm8jsHohcXc6BIqApfftnEgb6Okz0VeS76TSMF/o+ziAl82Xq+dmdNwvc/G45iuJwLJMMDvReX6bzWLoTXMf6gbeQjcbN1VVfdKFpO3LTuT3x4sBdSPOl9+4owb+m4+DsOI4FP42fTk7f9y7PCmD/BMljYlNNYSiealiNX3o3vT1tb8NuvOtdnmIVo6IWGA314Pr8El/zLHW7b+3v7SOjcf2RBJp6CXMJMr3b/d5+a39tNBq9pqDnIHoP5aMcL4mhGU31JJgByR1CC1dnv170bqHkuoFM4QvFfAUz30OSh2OcK3eSAIkmczeWk9Y4DNLYhecXvbOfezfwApliCyUXXI+Anict4lBviH8L5DQE3yjydqOjWqU3iA940oM6pVLAeo1mXgx6MsaacanQxlm4gTuT8afRxkE6dsZhLDeIE2cKrCulh5uCMCnCZKYBQAfUOtXa6oLI3zFSHEKKA1y8+p4Q4CACyp0v/OSFvQC4ECDOmS3deFIFpUQQq2U7oV1m5Vk4IThgeMjdGGChAMzcCl/K+J6k3AuApt4s9hKGXQN24R5ysYXLZZDCkxfA5VE5vruScVLtaD5t9W/cCUiTxI09l/A/5LaemoqOkc+RkBZM/iAbzPcwzfwQ5NzP/X6COmwrkjNUKEEdBhL0LabTf8o4nMxxPFx5N61WS+6k12rBze/8oKfvcXB3gL4pSCcHLrbo9zmaZaUEZ79KRrpNJwLKhaFW38/S1AF0etOVIu4XEG44DonGndFyOpVxFSRiFIaCA/C2lwjRCQgEGYxXL2mLRlYsX4UVS5hHaq/6umaB1TVGjDHxQfsHPsIEjXdVaHoWYwlk62hGyKWQ7n7v3Vyd/XLi3PQuTj46p1eX/ZuTU83Uj79lI++7b6z9K6NM/FXafpiD3gXzBOiYeajtoMqNwwSGpA05PeNMzpkIcGLQU+Qecq6W3EnO1YKbnn5ww/evJN4qvApu9xQEpeIBZjYja0TRzdWv/d6N8+7q5reTm7Pzy5/1DHyvZuBbaz/F1fL4/VXqF9HLV8wr18QzZF+3/l5KYAhasHGTaAKqGD37KKhSdA8JVUpu6I+TP9+AAHYnUkv3jTODOY+y23sYg75Ok1l2bWxjLjYIIST+Xk+aezG9JaPL6DSKWsdOrBUK7cTXK9SXKjhR+wqEqBulmfDFqX9/fnHhnJydXOPyQQ3ztsq7vrZ2sk+cLMDyYQG/YMHI5BUYe2pc3ZyBStklpdFUCirarj/JxJtIbYqRmYeqNZiIo/CefFCrzLdB76Ek2qcw5WiEZ9byAZqiCqsCLB1QbE2yX+ltk6xF5KikJeALGgjYOHEAVoiF1mCIyxdaJluNBwMqxNwbz7FCwAaviED5mC6DMbncyAordJ36Bp3wfbSIoS/Qrt246Z2c/kKOP0CASTjFISHSMuXdjbzsGl8k2d1E3udvwtCHN1ajAV28JL+c6nWT5Ac9kT4az+4q6bCjDiytEVr1gEHsGpj6coLoc/1EuRS//s5ipHxwx2z6iombunbjsvcPmjUm70IXSlKzqCR1QM9fev4kIRsfWiP1LNPWyNMFSEdrYwK4ru2LWgYFXFBzcyCdMEYHpjOGwfhMgdAgzitTEDWWlxMn1+fCzJGwC7IeSF6zBJmgvgCMEt4r5kcKRCDlJCkgN5ZjZIIwdB5DERZONUGKXCAzR4YTBYTsfHJoKLcMw4I69mRk4PpqNCZyKsaegwssMa0OQzUMnsI34vQcKhANHor3l1e/XTrvTs4vhKn9Y2PvEFpxAALTMrQMq4bVDuCSIboDgJqBUFyWHmpVtlBnSgk3aCCI1o847wmYIGUCRYcGL6oSWTaLJNAUU9fzUWmHtbWA0dowAGoMdAIgwwmIX5MGxNwKsNzJ2BZ5j8NIBmadLa+GiQtp7OHv6mANGBnI6BBZXddYptPW94YFYxXTTokzprGU0BWSGOg4Mac2qhOmxV2Sj2MZpcK8uu3FcRg3xe0qSN1HurFySLFMl3GAw6JHoOyIAJ00CN0ehZPCuGAOvESru2bQpKZPEnSoWOxMC1bmTKZumsYmcCzDm+D4oPMAzzRwyvE+n3N4g+2Rsy6wwe6DyolVHeWqsyUQsNZdU9xjReyED6iLYTXKe9c3Axt+L6Vlw8NFRoDVHxjMnc0OOpwvU/tYgKfVV1CTP7gb+DCzxeLDIUwDLDnzviy61Az8A3tDaN8GHLkJ03BhGhpflF3U8xCZ2BQplSWacJGufyi46JLIR7ECWtVolcsEFF924wSYPnpgBtSOaWQeE5iL2Pg5Td/Dgwu437DonnjxZurBUiCFk81ZIElVm3wPArVMqp67IlTJDUyNBIJgpQZVw7wuqX1oPlJVKoDBlQBa5r/OJgGU5k4G6Afcpu5sJieg/3vp6VyO766hDCyNjQNMR7dao5FBE0GYyrzUZuy73kLXQBdFWgSRXTrepDBgX05A5aMeX8AUXNDtxpmMnDkFgZIN6BkLaGY8DpcwFBBZTgCQ/0pXm4Km6SRSBpspMCiwkUGfCDbyMZLjFNAMpfPmig4xahSvnChMPNIyNnQLE4PNsgkxdiOkgY0XAHdDUyJ8AGxlNVSxDdbR10Y9gcdGAhoIWj7IvDY/ERH+GmCT2AJ2cti47Z/0fyWP3lGH1UqJq/xt8abdQa0RXcp0+w0Kn5Cigjimt28r919j8TuKi5WMkrffwgt0ucKqgHKt469L95Xbo2+Oy6+/+aZ0//Y7vF1FUoyRioynxm3v9KbXd3qX/4DhxNIeh4sIVGYzNt6f93sbZWpv4P/l5uTy597Fpt+76P18c/Jh079637vcMIDN9cnt7W+gVm5AyDvvex/hbf96c3F+2rscnN4Oe/WWluK+9rnVOL26/qi1srXhONFqDBqbdBxkl+glx7+2viCtBi8mvIgBl2jCcJyFSrK31iEg+EAFPmDIXzwb+e0QW4HFoNQvkE4tRKLwwxloRprtkTcDOL08xOeA/Xb7jfjwEyrSwJ0ETK8KmqBcUJOt3LwYZPkBA7IeyGJUYlWsxSQRQrESYAehDvgcWlhjRd4zyXEJGPid3aBR3PQuUJUtCVrUYHDY1D+8wC4C9bKyqqLhc+lD26ibQ88ALjDOou3AQTlLlf7v+UeKE/J2J5yaPHoHebgSSCSa4Ra4wiMKPmLsJekM1JRINx7PzfixKbYhVGQ+vikKHyPEgLahNDjE3jLBrqCCrCBAI3iHRsplGMgtNQJZLq8vWMtF2Mw2bBDwBA5nZ5lE3tgLlwlZEdQkqkAghYKJibOZq45M0BSAI+2pidNFemRMlLNHdQSCdin2yCYRB3ISwRB5YQJYaN0L0GxCEsgkaMKqHJAvQA1Ig8wSCkwKVuqOBdJSkV/OMeDnmdK4Qz3EQf6X63rNggKyrffhLNIzUKodVHShFbwk5gzXCl+OGmiXlOH87h3oyvJZxREWOMIauDaSHBV1WefD+2RYr1meU9MWW5GfSugYBDYzjb1V34GJx4qpLs6wBsN80ZSHt+mSDvtok0JlGrZhDdqg3WURMIM6T8sPB5XreznOAMZ2h94pijqTU90j0s6KCijRnmHtV7fPVX2QEjQTjqF14MlyEYHui1Aqy11PbbMyef14iQvxgB8cVCBZJdagh4+dKuMMIx/56LFvJjKBjJ7gfaFZS82nMoM0s4GVA6qtg4FhVEBNvGiC5ruQXaTQnBP8Nl/pbIfFElrHpkZSLBN0PEBTBwfURQqq45LOliDCKgRO0SNAkVJ6DhX1C1DLYAwALzaNfzPU+nqoq4mdsDRXfEAiocAwwHoozis9fIPVExltL0ADZtibyMy6NbLVRLYpWk4l4Yb9RRnPOUso6YDz1JW48EaxG6NjzDiJgJzHnJt1u4xw+mBkJdFB/acWq/2nhzX9L4xhWhyETsnSSVJrfPBk5IjCIXEfd+IE4dDEKBicOFESKIZB0vy5BLWOdoj50sV+AYdfZF0byTlaM9AhTh1RmQCcAOGy742xhikiC/eOHGsgseB9oiIhC8q8O7K/+ZtYgIaChdHpgnGTgsbCXhLUW2iRBTC6ZYzNcCLYPzGJxBY9TmCBtoLwQbk5WNrAEDG5h4WLyp9IF1GWwdHJGtWZaW6g8rj8MJjhOmWf5cLlBlBJm0hfourzMEfxqLI0UIgpv6TCfUJJbx4lwamEtUmWIwINILjIXSZZPhYaCxZAlUGmrYkp+a9GEqwTcXH1m3N2fvve+fknGDxIi0bxSVcc2+0Gzyc6WEAzhmdv2/CQeQWMu8AjAAFFFjGpZlBwKQMqGVQY6dtwONHAqHHeQGWcaGggMWGYYMFhZOWui8yyJGyVrO08lw8HCrEgfpUlDbrEnZBzUQKhWMm0scshMNGq0YOUkQOjwE4t3EfHnUkHJgoIpcIhz2hWc3qYij8mfwjfRX8dZU5itmUsmUQySKB4h6CB+z6rJ9nzbtuyxQ31JVF89AHeBivOdFIkpD1ssesBGWQ8F6m4S0kzNv4yLWQA+FCpjywK4AGXhrLt7QkhFYFnFb0WOPOT2omocpF2SWsmfQNB5eWiKq0Aaklbbux0PkH/Cygj0+ZBtKhvqC+bkWWjDUEG09+7edltzw+GH71gKavgdYe8BEcaKW0hTAsvfC+4M6MaNxUnOtnxAnUwM2oKbxbAVDsS8ZMUSDjzUAH+t8FAS7FchPcAolw8EG+64qhR8W9t4X9reNreULSMa96ZjZT6C9x0VpLyJaSrqiaVIgOAx4hMxaGHFtsKNkIVh+L4q6/etl9CHqT5FhcXTZT5kORL6arESzNO6lZ4KXNlCr7MvYUwYdbNb9toE6OmiMZGLDllkdaCZT1nF5QZWNLksBbZ9squwRS4l/qJEzkm7Xvqh0ChCdlgBj6E0oBQ5Kzsh6XkPIRsQ8mJObUGOvY3HNabGbll0RTv5apqY+iWh0UqQOwM/taGVoegVgwYWTahy26Lr8SjBY+PjvEm07UR0FBbiWMQ5YEjg3vF20duwvSzJQwwt7kr1ncdcV9yGZtYBRcb/2VenDMkQL/Kyc58yI3yIsXyufdIG913+WK9s0HNAhXSAkgTpd871zdX/+ujwYXuxL+AUXH9sf/L1SVmRBtPusdQc+Km0sT0826dKEPFCOe+mnZuHAGBMMSzq8v+bzfn/d5PH/u9U/2yutDnaRqhN/Jx1TXwunN4eHT8nQ2Yt486fwNg+DDZV6IK8Zd+/5qHuQMiFrjdW+LySr/eBn/10/88RT3h5KeLnnN+ed4/P7k4/72HaRjvnduTd70+VPvYuwUw/d+7iEui9f7vpAQnnnv4PvTv3NTVej1aQVvqhFWdCVa+umkT1TD608O/JQMJimtmMneL3os5G9SYv2rDq+NvvlXUVHUDGPFoa/nOdRfK5r5qc27P5ePEQ55kllwoCeasYOZ1U8fGKjrCJQW2lWb8Rmm/+4LbeQCbAocYT1ah7QSMCbS9loH3FwZdNGvT0LrZFSxAleVKBZJIjuGtisAPALpiE49lbyyW43nkmDoxLXgvwQKK8sxXXMqUCovlB5z7MLQGndbbocLaNFxSfxKAxYU0d7MyTQEGgpqfylZHXgHwH1z/rsyT1ZA4Fl4ypbD2oEMBIUKM+ZhzMXyH3OORWAQ8yPy+OX/BwlPyo1APtrQPrGlOgzrHAfEVeJVzHAomEr4MwM9diyz8afBCZSSWfsnsZau3qjBNcb+MwocFpSLfHdOsgGUH+Dk0rOoQAFLJPs5yEtTM+tRLnKwXdrQ2RLhDmuoev0RwFvv8qJl8thJ3Rwmp77Y7mYA55NeGBGtVppL6P3KRVPMoenHdK8qKiVjiDFtF7ZHMlmQH0cYW00qs6RAa0/zDC6IlBoqAiSXxONuukXGOfsYI1KasRBXpZFsg0GGljUrgGyBQR+j6IZOT94CBcmz+0js5Iw0KFc2cbdSwS9UXFKXoOjatfLkqYlE92K3YK76D+biFCcm4K84TJg91DPhN7UVWDTYj7btqwaqq9GYPS555qTMnmtmt3+bbEGzQKM2BAZVQaLVO4bdKHIjlfYucv/gC0WcMmwJ4IACQDm8P6bJLL5WP6rKx3zZV0bHucRvtlglc2rRDzWSm8T+MerWv0Nvb7HKHgxmBMB4odKmIhLRanIyMsNaFcEWHN0Ehfp84USfbDMM7d3DuUV1TMSZklRkFUYGqEyApRMGoYZxugmXsTeDAEi9jFtpex8SQog7NoisbjzNaOXdyxVJs/WQ9p1dvI3StU2xSTO4yFUIxIuJg7KhLLnlVLQ4fUAFfP1UCSBzDCfSS3c6UgC5iuEHFosgoJn2Z21DLANrPI8kxSm1oDvhkqraamQClKdYZ4jsCtHwdJeabLErMtzpay3dZjBhvG8/vPCyGlDSEPBoFT57ytR8Psm4Ny1YtucYrwa9CNXjtYXCM0Kd92Xm7BWAZdya0UE5KCccq8NjFviikoEUUD3KsqHuNlmGx+wluQTGGhHbg/uZRm+0ncwtcVt0C+5hbBWuLJlT1gYaRhYIUoWG3M1IboSaG8dQmObO0citJTAEuSSRQciDQh1FabXleQDEpoBCZLyYDFHFZnvPSZFIeFXqxvqS2eF9iHvMdFlK1BtjJYTYj6AKEOaEs0MKKT+r9hpk5wHhJSDWtsLGiBpspntvkJIkTkFK+fipFeXBgcVXXQ14MyiUIbHNAlUDq6F/P/E9SZeMTERtqHYBcMayh1dirTgEmcYxTY62AHIDpHYJCedAUbSSfb9sduz19ytc9ZUyTlW8SQ2jmfMka0JOhNSyOFicAA2rlrVqlbimYbzIlOgOZszlyZmnGVdaf8X2Tlx5D2otZ7BMxNewU9lctahiJAlRgFc3iYm2W1+qeLI/CGqRKGcFrGDn/UO9zUt8D12hRQK7ADEqO1fz5bhA04bsxsE1KhrWz+DAPlpFbB/PkMC0uHnhgg+X8EPDAdx5xR9xKS2VxoVqKWHwKxyNr2QjUygwBf3llIkjQBv0/l6BkPSBsBgZijUI5wXIhMf3VRD2XahqltgsN2DB6GafmEQxsQ81sVCOIWuClj/Ds2MpNuAeLC5WDo58CVYuAltJMkI0kEZhE/62ZJP9l2SkqO2akGKQSA6MdQZc8ZckLlEyAf2XkjWoU7ZFVkEgsgdC8d3hHsSkfMZzppRWnBpomfNpAtl+cYnd0xAEsDMybSkmd7GSnETS5oXLKU/EEA07LI5MFd6sgPDfR5ZvirX3U1tueKS8LI31WBhVz+QNp69jaW8E52FjrTaawwvj1iLaUPf0iE1/j7YhwZtwYFXTzELfiw3l5e0eFcmi4Mjnj+hjwVnh8XLZZV4nNufeoSmhnFZ5LoYiF2y5O6T27b3cTVhZ8L4ZH6ixfLL13lNGqTY6qnMb4Ty5XsCdVIw1qoUQeI9B88/gPUewFqYkDVhTjeME0FD92hfkWvdyWtctw26fjZjZd1VIrTtU9eZWxVEWiUmBO3NLGfEykNUHCQ/efaLdJHuVQy4eo2qwjWoui3eilKB/oUfDy7MDSopDLiH8QBSjhClGpaFWd7vqJa9Q3k7USeZT+R6kzpJEYrb/od2sZzXC/ly5U04fPAYvZwTtWWCz/WnqxRD8qna2gE32rP/lWfHLhq533dTgi4ppSWvCqI9aIoSdhrnMAT6Bf5iCerCy7zuFooI5yaqdOvZsPCqlRlJbxIMgTvTgwi8uv5MusuBspAajglCSvJC0Z2jCCmSx/Yfq+YZVV00JKDvR6se17hJ7CQCzaXKBTQYdqqBHMnXRUTnEppFef/trhE1ayjFLyObgF4c2yYySnoXZiAWzOWKWyXspuO52zSucNUeScfWImHzlikRPsTmIGYxIWvOqgaGFavjpKJRMNGNOcZGHxgsdL97RTUnw5nAXyAc2S3Q6Qqu9yy12GcCq+sh3R6fqtGROQjM4kaCpPtnahRzt8pjxK1PaXi2yOcSuI909Z8d4C4KlSJafszH7eL1ARECp+XgfX+vyIus6EwqFolQX1ASejodyibAo66afkqYEZP8UjqnR+NadAF85p+fGQHK4PMQdnsnwdsM6m3gzPzqCwDUags4QNJgTcBZE7WHeFXZ61gJN4vNOtWoq5KD5KcShMKOlWglKqQOouIsp3T+au0dnheEYAFsY8gd1BodyVCs+mEreSdAQdcPKUA3Wmte4/zpjD/G4VPWcfgT/JHWTbyo4CaNX5BtW7l7kHuZ2iY1BlyKSYlZhrguUFBpyLMyr5aCjkif5EWWSANQvFPXVjQPfDQj81ZKpJZqI6Xoo9Vuw4VecYdfikIqnS73Pjvkz+D0mtxpV1tVOth0yQamaEgjPBS6irxJFVm3m1KwPCqs3IqjR5XK6OR+Yw93NwdZQ32u1JtdDbAYoVPqc9XKGUDf5si6WSRY0K22+SjIpIHgyGTU2ztaEQXDZl+sOker0SOcN+qx/I7626eCMWf2korl1O+Cq9fJijgpclwygGo5puUjNb7UOd7aYVIgZQDf2fUOYFWU1qFpXnZ23g4HHbOzZtYNtwg3/QiYjJMR3lVy2mr7VggE1xbBX8d67vuQm5KtZboehFOQ7dEdkDus+ipQZtq7bVdsgFzqPiz5nb+On/KU3IeDBexqaIQVH+91r7dDsssvJjADQ5GmoajI6eEHT/MjrgmboCdRRYJOAVVODuUSG1LozyIHlBqyyk+SH3I/VShSmzjT3ZMDkVJsu6KYfF7eIa2oe1aAUq/59ynNppuPBfha+pTQIZFPIvxJoFEVlIMxmQD4r2Fe0/+O9TMMCd7DY/HH4K6nfnTQ+YZ/PpR+QZwz1YBlTAvf8OAZf0fF0Ux09Y4mAHSFBAQoeOxgTyzeY+MWFyrKdd1dR+AcedTJyx7zluPOPsfyeRvuStp9gNdXjA8DVwqGIrYp91hzaw4XZJb0JbMPM3MUw5zNVqN3TtcQXE+KkX+Z7ECNVbu/0p0G+Ag2GsOlXpxZ8Cw9on5j9jEVFdVC1L2vyWdaKlKhXo7E8mJgMQlxoSvEtivqP+CnV4ViVdh4IPuatDGZBreP+E7A/j7xOx5s5R/uRxu2MfTZ/Eh58we1ht1MOMft4CqOP8aMZYgJKpvwRlNpfG++W4zl6rEeNKh9Y6a2bL7ZEXzSyE/W27navSvAskOw6DHBYyO59wGfBimZCVppx1KJx424gvpynFjrQHhP0frpgBigJWlXJ9ettpscP5WfJeYOk6LTWzudj3VU51zN4MiomDJAETTUIs5/P9SjlljheArfEDqQ9FrwaXy70Zj6WFVl2CWzKOUt1AHSlv0KqeT0il8sTh2iHwJhNibzKi+LxKPyiJ8+owsPywqTFQzq3Y9tnp2l34j4v5vksepmeyKDIiU39LUftCC31+3XuMcP/8ljN3apQTgZHG1uriKansbqHAjs23FK6HSW0zqU4NPJwiKdCxSVvr18XyT5ZRjEOg3+Pdxcn7j85Nr3/zMT90Rx8OzRtmvraE8Q6A8ykZmNxMJCh489cCLhNteKrQ8eWVcnrgkaClc0T5rJrE9SbCaOevDH2UDe8G9r073JYTyXgKskNQwOoH3iSQ0tkxfBzZnPQ2PPv49KJ3oo4M6e7pbKGck+1ZN/C2d9bJ80nyGuzVnBK8Zj6sRA/UTKQ2uvGYVcsopSPzkURU2YS3Dud0Eq/oti08i2QEQj6PVQBZ6R0yHu5iDaAvkYsHmk4LjagDmTF/SvxBwP6waF9Quf+trOtAxM8ll+e9q8s4ZaUGhnpnMqOr8/XnqM2yoMgZo1LjXpJ9zxtHGYOkQjkZgRAjJJ1zsaA12lQ4ZvSWE1zIeVkXgc+2KxeqvizXvkTEn60NAgHBmju57N8K9MX2zhBDa+jv07PUt0Vq9k6lEBrqIwGpHHZXtFu6+yq5B51BuDQKvsaQdqB5E5/2pdklvYenp7En3SETUXpjTlmHV1HYWGZZRZwvk+XTWCSesrC5VUzVGJa3Elf262zleeKJxjVxGSQbI5mHOG3BkPk99vyl7P4FqUZaIBzlmXPVbTG7k+RqB2HkM1xIUIAJBsMieBJisM6ysaxCGsVBMY3igOUDanZDIdaaEhwMS5uZ3kTnQD/VEtTU+BSs8b0OLj1lxPH/f8WUbCeM/3NU4qlwfrVeMp8C6AeqBFmkHaVwnvO+e5wZx81PwlYnWNIx6tlJ3vqNK/5Q8g0J6A8CwkdizJfB3QEKXV0FHSBAM6zPgQ6szgFnfZrzF9Dbr3JzA3VkrDfzAtfXmYumyy2r49oi6WIqH8lE3FxJpDbHjU1neHIhRxPUGsVyvA+VQcekV3DcITusykdnMw2UnUZZjpcXODigrS38fsCZ6kgCuAeexmGWA8J+2c7+13+tHpmEO7AZ/rYbZgRUfFcunXUFV9/+/CTlkdJVqMOlzryp9qUORKVOS+fOE6K2jrYg1CgcIpkCUgtOS3+i16ofDI46Q4Y/6BwNEZGGaKn0GD/Izbz6dnTanfRftTcM+fELN4KRB/81Ulr3xaUktnhs44FMZmEVAiqyYUGhv4t2DqNcCYoOaOBDa1svZrm4hsKDjpsObTpKyRzPYxPD5bgf7MlQTfyYqcG6c6+AAv+pD09ZTvIyUCfnFMMzf4YjzDSjLyI4Czw4gXznTTalFYEtFngaRiUfRoVSmtr9/lwMSMVCss29xc1IFqXc7XACaDsCjdUwO0e1zmrkXXFZ3CVFhwTupeyyGcabmzAUXtkF2ahkLxbyektJjLwkC3ScpDb2IDY1iIFOH0M7D4+7C4BQVTTKvYfmEL7qK2O4Ueep4HrCXBNu8awHDpcePHFWAurL663lcZDrUQcg9bbdFbUu633SLgdIos7dJeqgQI4UuMnE3aegCz9s+qGgnVLDB19+bH25aH05EV/+0vnyQ+fLW+hv5lREFa71p1gjaT6VNDaETGMyS88c4nDIsuB9Kn3ftHZ4lF5oIdYYKrwpdlbad1FQe64JgayOYfYppWAs3MfWeA6rIpYBJRjGJo7JqtPTWLHq4kct9utkUFDGcbeocvXPrn7tKwvMCeSDo842q4SNfNqiXjgRJmHHdPbpB7t3LwPazJEpbJhLQYsFN3h2KiEO3PWCIoqO539wvdQsHWVQt2u7uPppbW7HoWeYYqf3TeecpiJ0oVRxMyvKNHj099KBC9pXDgOvj8bDCy3RZiNrR5FtYlZ0Soc9IFLN9WzEbkR1yoNOHVomgNUSBVdBV6n52b1eBa8T5uZGMzOyIw94FH/rw749//n8sl8P8LkdWVs7s7ROxtTRpyuTz67sarIA5dMFRTBQpgUqtl3kGy2Y4RaVMSxWQcw9hw0o+tG2Ri6PxFfi2zbtx8gfZQmT5WFWue8ESpAojXb5lpT6yx6lYqkntkvZ91GhvFpqqrQNxELHhIB9q6mkA4p/+DBoD2sohfbzk9RssieJP9RjPGudGegMIslQoLGXO+teS0i1FFmZOjQT6/YBvrA7z3QJz9S2dnVhPznoCWFCYsQznnMkVnH4Xq5GoRtPzsH8ieNllH4+7l7VR/5a1DN9zADwtgWDj2bNzjygZ6RXvRFbkd1KN/aoJqW0ytygRcvOr1ADyQHcclyBXmb67YKu9Nk9L7q6MjWw6Jh8TpfJ1AbrFYrBfkdbZTK3nLKsbb3OAVi8qVGgc2zumEFW/TENp/C2bnPmLi2QAzysFr/mJA5lqWxlAX3e/r8dw+toNX9HXCrvto2HIlX1XULgi/w3pTAnbgzbivPTQTKc4kBgn8Oinho8L84EiXpfTAd3I9qRyB8Js0/i2RKzaq/xLjYnkoPsqOAZWeKy/h5Z5aOT2dcD1QcxFQ7cCPdsYxiaIJtGfqRqgLHprvEVnokh/ag7NZQp1KHPEZrrAzA0eFi0scx62gmUD68zsu/xdWsPnqt8klJnMmxD+xM3XK0i2QVbKYeJu2+OmrRPYBwtHTaEacvpMTKMo53gCp8B1IDp2JgcdHtnVcrmx11TY54GPMFeomNS7saF+ubdK2txTtvr6qgTr19TRUVs1ZwbtEbUMNklqHebhGrvBz6jXQsUtTE5Y17DR+qN+PROSnUgArcKm/0Hizz9x7U1kaKrVyfAqDhHFtuAep2twP4yuAtwF4tyayQWKFk5eeJWf05RGc9D/LYhdXWbfg0+AqOL2xjYutpy2xxnjh/K2CmnIi9qNlmWjXow6XWy6zoL4AwHB9jswRBsXNLl2OlLl2r3VyGXtJixWzr0i85by9mf/ipezVJzbWL+igtSglrdWZT5OY/FfLQ8GX9KVo0+mXKNv2F8ayj9ckwW8yaYOdbkSBC6mQQLxyzl22hcu7pzpYSYRCUJVYVfMa8T4POq7FTdrvW7v6rYCPTnFch9T6jgbz2y5rYM6Ax4YE70CVogT/2B1DWiCFcFGARPImMoNQr/ToRuIVX1Xn8c0uZvU5ochDpz4wdgdGQTq3xOCk2bxtidTiWoc2kxvSw/F4q1MlSMTk/eveudX570e2dVz3NefLBVlBTbI6PsiMHPuujTIoX7ALTAn0eY0weNt2A/yvF9VOpqUwzKt0YLvzBR3nvVFDs/FjncYVuBxonTwhs/Z/fbJ2ZlE3fUGVY246r91jlDg2c1fKHWiqr3zrr2n/ir6J517ZKDts7UesZqKYcm6FyorT3vjed1wvwLqZ8VBvug9yzqb8Gayq9iPeMtPFBMnb6Pss7T5Aedo/aQw2eN7VlNVUit6G8tH2uFZ27CrDt0LIfj0KrJTh7mMTB1gRVHWhvM/v8FjNBbIw=="}, {"path": "scripts/mutation/prove_kills_execution_router.json", "orig": null, "new": "b2c0a72065626cd674717da0c1307fe6618a13698c41444dd7b27bd5ed4b10a7", "blob": "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"}]''')

BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = [["suite", "backend/app/execution/test_execution_router_forwarding.py"], ["suite", "backend/app/execution/test_execution_router_contract.py"], ["suite", "scripts/mutation/test_mutmut_run.py"], ["root", "scripts/mutation/prove_kills.py execution_router"]]   # suite = via scripts/run_suites.py; root = python3 <cmd> from the repo root   # [where, path]: 'backend' = run from backend/, 'root' = from the repo root


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
PARENTS = {"scripts/mutation/mutmut_run.py": ["MUTMUT_20261117", "ZERODHA_RELAY_CONTRACT_20261126"], "backend/app/execution/execution_router.py": ["ROUTER_GTT_FORWARDS_20261026", "WING_FAST_FILL_20261111"]}
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
tmp = tempfile.mkdtemp(prefix="router_forwarding_")
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
    _home = tempfile.mkdtemp(prefix="apply_home_")   # DEF-113: tests never get the real HOME (CL-22)
    env["HOME"] = _home
    env["USERPROFILE"] = _home
    failed, envmiss = [], []
    for where, t in TESTS:
        cwd = backend if where == "backend" else repo
        cmd = ([sys.executable, os.path.join(repo, "scripts", "run_suites.py"), t] if where == "suite" else
               [sys.executable] + t.split() if where == "root" else [py, t])
        if not os.path.exists(os.path.join(cwd, t.split()[0])):
            continue
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
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
say("  Commit with `git add -A`: tests and mutation tooling only, no release needed.")
say("  Undo: python3 apply_router_forwarding.py --rollback")
