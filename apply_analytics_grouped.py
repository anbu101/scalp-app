#!/usr/bin/env python3
# =====================================================================
#  apply_analytics_grouped.py  —  fences ANALYTICS_GROUPED_20261111 +
#                                         ANALYTICS_NO_FLICKER_20261111
#
#  Analytics → Trades:
#   1. Velvet, Timberwolf and Tigris positions use Icarus's grouped card:
#      legs sharing a group_id show as ONE position — "NIFTY Spread"
#      (Velvet / Timberwolf: short + its wing) or "NIFTY Strangle"
#      (Tigris L1..L4) — with the same per-leg table, net / realized /
#      unrealised split, replay button and expand / collapse. Leg labels
#      read "Short CE" / "Wing CE" from the leg itself. Header counts
#      kinds ("1 condor · 2 spreads"); open groups start expanded (≤ 3).
#      The "banked on closed legs" note now shows its real sign (it
#      printed "+" for a loss).
#   2. No more flicker: the 10 s refresh no longer swaps the page for
#      "Loading trades…" (which also collapsed every expanded card and
#      jumped the scroll). The spinner shows only on first load and when
#      the range / strategy filter changes; a refresh that brings the same
#      rows changes nothing on screen; prices re-render only when one
#      moved; a failed background refresh keeps the last good data.
#
#  KPIs, equity curve, breakdowns and CSV are computed exactly as before.
#  Checked in Chromium on the real page: three grouped cards, no loose
#  Velvet cards, no spinner across two refreshes, a collapsed card stays
#  collapsed (the old file shows the spinner and four loose cards).
#
#  Display only — safe to install any time.
#     cd ~/dev/scalp-app && python3 apply_analytics_grouped.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
#  Then: ./desktop/build-scalp.sh both, restart the app.
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "ANALYTICS_GROUPED_20261111"
ITEMS = json.loads(r'''[{"path": "frontend/src/pages/Analytics.jsx", "orig": "1241988f175dd35f3df312592df3688e7a60ac76645af7ec3f23da3453a2c0ee", "new": "a85379afc63de683246748c305842a04a32ca9e4e70d2eac60161563246c3639", "blob": "eNrtvdtyI0mWIPbOr/DCVHUHOkEQ4K2ywGTmMklmFqfIJIdEVXZtLkUGgSAZTRCBjgiQicXArG1NapuRHmSabs2Yyda0ehjTm170sKM109PM+35EfsF+gs7Fr3EBQGZmd1drcnqKiAj34+7Hjx8/fm6+9IsF8Qux1fd7ozTsJPVfJe+F+PCb3wuxHd0O4uA66CfhXSDasd8N+1emJFY78tPrlkjiztLAvwqSJQcMFMAy29d+H76Ju+UWPopmXRwMe2m4mKSxnwZXI3EZ9tIgbomtXk8siZPtrf2jsx+a8PPlS/N3Gf5+u4XP3i1XD3pBJxWd63CQVAnycl3s9tN4JNrhbSCeiN33Ycq/O1FveNtPhN/tBl2RRgKa7gYi9S96AVVdqYuTbw+P2/LD0c/2BXS724PSd6HPb8+6YQwthlEfehz0usKjrgroSC8R0QC/yJ6s1sXhIOiL/b0fdrlygn86NwCvh9i8D9NrMezHgd8LE3iJDWJD++2jkzSKA5H0/UFyHaVZJK4wEtdUA2pq4Fsc9LtBDMD8ROiuLvr3PoAbRElIj9QLsb11vHNCgOCf9+Fv/3Ed+h/0q9wvnxEjLv1e7wKL+xcRNJNe+ymgKQioaW70yO8HPXHRizo3dYK3Xhc7e8e72+29wzdi70Qc7R4vnrSPt9q7r38UgFHRj+4BtTAV4mTvzev9XXFy+P3x9q44fCXax9+3v22pbp2Hycl1FKfUkFc9FyHg8DoQUR+GA8MMO0A9YojYuxgRAm+BGsXWmx0q1vHjblJXwCyiOjppn53s7u/TvMEc4rRXN0po7WI4Et7+4ZvXVQ3qZQStdGBtDNPgqN+jQcnH7/WE4oc4glfQF/h7dS3CtCaSCIZ9GyYJLCQFLkta8D+fuytpseP3AWmiFwEBxCLs3wVxSiMkQsJxJ+FVv54llFXh7W1Dx/rdKBZX0IcBNLoh9rbPTo7299piubG8vth4uthYFR9++3fiAkcFH2HMT+jvcpVn4mtYVH4Hv0HbSer3O4D+IE7CJE3E4RtN3jHMK0y22N99LbxV0QuukpZYFglOIUDvjgDuMtBX/yqp1tTggcCghB8jY/HFOXXzLOyeA5L6KVJyKgJc0TURYB9S/+oK3hKRnjPiOj0/Sc41KW9CuwBA7Dfr9f3Val20r4Fs7sLgHnlAD9ZUwFR0GQ1j6iSMKsVpwTmRzAD+bR++2Tk8ros3sGRg5JJVhVAZVxNOdy9Ig94I1nAaDTvXQZfp42kdEXAitvcPT3bFVhsWw6tXu8e7b9qivXewe1IXWxIlOK0n+wJoBAbbBW4GVAFzf38dwsoL00T15J5wAzRGtA//iSWAOEQOFondwx2iLJwLOd0I+zoCDnV4tPsGK+thYbd2eNyA2wgmE1EUqIoIPrgE/qNXoRCLwh8MAj9GTFEnCOiAFv497A4BECSsuh8RKq7RJA1hVpGdCI9myhoM/qMl8u+REVIvevAMxHHh95E50mRgI90wGfT8EbPGag2Xtd0l6G8ahxewwBImjU4vShjmzxPTBK4OCZALLCaANbs3twHA6SSw4INfD8N0JDrDGJaVj1QCFbiTVFczgC1xOez1RouySYk6mJR72AX6QBMST9v8PZEL28evuu3gPWCwS2wWVk5dvJYrFDF4E+C4E+xYCuCgrloYtDEzxqO+5iCww8HCvB0AW4zeh7c4CiwHNMEoBorlOsB6Fm+D+IqpdWlhIbwdICmNkY3uXl5CT2v48yQFaqdfx8El/d2WWwE9HAS3UU10AMtpsA0TEbznavK3mIjLOLoVFSjQSSsb0MWlJfHh97+B/wGP39rZPTvePdrf+vEMt8czZETNRvNrVaLDUFTfTnrhAIUL3mkYcL2+hEsQ0NlPkyVfCR1LTtlMwyf7e7AB7O2+lQ2uNOUXg4SrIN0ahC99mPaJackfhEsX8K6y4aDrwI9vgnTHT327sOz8kvkqkQK9MX3Ze7Oz+8uzl1s7r3dPqDuNp8tfZ7rDEF763augcNTWdxqp/vewZsZi93aQjmjGYSCFTe1HJPpRmSSDhnbkJzjjorgqfX4TpeElbtcoINn1YRHgBh9tn/xQA+z3A+Syr4AB9v1bZw6GwFOSJS6e6QBIfGHaC26xObvKdRTdJEuZAnZd4ulvMg0ZRr8kORCWSDK09P3e2cHWyXc5VMIWEwGfhJXergFnG1w7tJFGN0E/C6r97e7B7tnRt1snu8sv5STlSJNEoOMAu1OI5Zg+LVnFHrrqFhaWfjH/Ev3w9/+Z+yS4ZWDPOwd7b2AL2v8ROIugLQUXIvC2KEW2dBeijDCmbcED/lYFxBCvxB1ESjOdAGUL3A5QQEQwsJouw/gWBQHgn10QCsQwPOsBLwPB7dIPe4u8oYH4hvxtJHjwL4dpCkyT5WEC1IeNE5kr7Z/3IKDxrscSGnDciwA+8xaLXA4qyubuUUJJo6iO7BK6A7TObWyn78WmywO9PmwL1Q1ZjDFzHN1/F4ygpJdWxeZzcZKirOOldZZegKO/eCHOvxyndZJzzpCXT87wORndXkQ98dd/LbgwCk70anIObVwO+7yr2CP2xgKFzHQEm8qkKsYLhEHoS4c6azi0p8cAoIQIL4X3BZaBxr6AfsZBOoxB6ITh4Gf56CEixbMLRm46GgSbFX6oCFphmxVJoinKXDTACmw+ob/Y8y+C3ub4XH0n0pk6SHxVqUzOJwvM1KL+NhDIzebYCwiPwDvqSRoNjuII6Iw4iwdUAIOoE4nZ2AfUw6eJAkX42RyPQbLq4ul1ebUmroPw6jqF38s1kFVjIJtj6MwQJNg19aIlzpuD97Cp9mDSvhy36/y63sOak/MaEQ7u0/1uS1RgMP1kADtvP63UFoT7j9hES7Trgzi89VG6BaEjwVeVQYSCb1ypgYDaT09AiGmJZqMGq6MffCs7WVluDN5DiQGcZgFhLQHfL3vB+5NroK0bfMw2iEcG4L+9LehsHwCAwAlHWwBRr9cluUyew6J+tsQT+hwAAGVMLK7wk/sf4mAnwKORaBPjXfiJDgQ5z5z7hUiG8aXfgc2LhJAkuPVh8+sQwQGFCaBXODN2eQ9BZodnr6CL4OHpFo8TiKkNkYIAvXQXRnDIWeqM/P4ScMfwKiJRUqtt/E6HNl08nID8iXqc9yhZMs/ZBo6DHOgCCFT+gzVzpWkePuEO0bI/wXMtI8PweL873N86ODta5gF/01hXn2l/gePOnc+y9wa0B/JzMOADKcrQAzpsw/ttP+62dGMgloPgLntycXXCmGvxxxQXjPomlz9XtBa9+bgT3rbMRwB6g99wDvTw8MEeOz6fBJ2W+Wi3iS8OYG/qttTXW3zCT1dxEPRbEirsrJdhql+/vGpZr19e1Yh7d1sa/3AiSeTLl1ct6yWX9W8v5Ejh9b0f9wF1+r0CLt9zjYve0EyhNTx8L1vQ77kGkpasUfmL5urFU3+dOCRTW4vfP71Y61zyeyQ/Xb6xfrHeXaX3TJGy/PrK+vplE95PNhYk/R0cvjkEEqz8/C9BSo79sJ+Ig6gf/bz281dh7IvtqBv8vHYLb4BNd1C452qvDuGoDtXu/NhbXOwsIhteHIbVQrGtjCxVH3ZAgHqzBcd+gPiucgJbA0hmtcpBJH+0h0HCv94G3b763b6Gtco/X8Ehn36c+LAH48/TjZ86Uz5R/KMbXIZ90kr+pHkzT7XUce7xXMM4xyJEQUApHmGzJSkIX8FOPBD0SokC23Ukc+R9ICYDU6gckjZZnAS9Hsqt//xP4uXWm+/e7L1q/1gRk1oO/koe/ooDn/hDTcN/ORwtXgd4toQjXYrwcaf0e2J7d+lolzWCwxGI66Q4DgrbXMu3uWa1aVZrZlAAWI5p5Rb/izLv4kUEe4fwmosdUruT+qrqNkvayQptEKrZvW2hXplmFVNRze7F0Og2a2l+aGKLhEdxHwQ3cAKBZ1ZXspYFegFvdg93RPLrIex3i9HlJfbDPVkrLaoma6uPy/k+Lrt93K4z9yru4vIcXfQO2ttwfA7TpCYiEO/6uCGhzhulSb/7q2GS0oG3On/XSQHudv3lyxx6t+vI2i1KfRkRiQaxeImCQDmlkmI9Dz+HGt4INGqwDB4i+kSlbRwntHXSdoGTtt4F/u1WQedpJzPLAETqGzjlbSXXoUH6W0K6C799kG8A3ukWNPGpnUu10AYBpBcs7kLZtaXmytLTb8S/WbsVySBKc9PcQatGCt/oDAxfyUyh1iAcm/Bc+kTQus3NK/cwN6v0ermg48uZjncaT1cvO6bjr0BipG6DaNe5EdD1tTXo/VJzdfXTjAA/N1f+5R/W1qAaiCBIxkTSxUNbzg3th912bk7gXX5OVla7K998Y4a2M/R7NLRmY2m5Af2JgyuyEl77fVQy2sMjLhizrehixBoJ4l1wToMDq09fMysQP9PaRNUtj/myFw5qaMY52V9qH+VGyEPJjfDwOL8m4V1+hJdr3wSNCzPCJowAz8GwMBdjNAiJC5iRGzQ2QN/QlKRmTJot8fWTtcbSeuMrNK7dhkM4IPjxVUAVGvXG6leMEjx047Cat6wVp5qoJLns+SliqLnSajRyA+SR5Aa4e5Dn6fAuP8Cg2eyuPjUDZKqDUeI0fo2zSDQEB5RbNC3+u+Fy4+U3zbXGh9/8brnRcAdL04o2OTSACJqm+z4B8vF8jHyTSHcfwSw3l1fQLNg+Ek+aAAk+KHJe5CZxpvG1RsBaq7mWQwCP1FGn5dGx/0N+vuFdHh2NwF8LLIr+Ea1ZND+kHONJQfTQHGkbpM+fkahnY8ljHC/BGn5zsvvLqsTJV4g0RSLIkY/wBbWtj5U9NELYOBlh/2BPzaGFRywxsryS35SOv8vvSsffzV4BjW9ay2uiH8AOnqSLUBFOg3K8TxvOasAVTmdhtKchvlA4wZlfXzMVYBgAcaXhEEZzXRLG6rqSGWjymUgQiSBQNRvYET5tCmnly6GBh5ln3iev87vOyev86LvfrK4Hl87ooXMoWNFxHS2b2Clpjk3Eh7/9x6drxiCLz9Rr4GeLB+0DHOITsfcD4wnYsvdff/9ktYouFQVzyL1UnZcHFJB6QGBZjGEv566DoMJ2LeKJ2nQrPLbYVsV9HKZp0EdysfSwluIVJJf93ddn+1svd/dPpHZhv4nCJ9lDt3fpXLi/rN8cyTcr8OYtzqoqsqpeUInJT/5I9X0a9tDqdx300DT/0z9NJf5l8Ab4y6bw7kjf66G+OboUd2JzE07n/SHKcRXxs5+JMHmFp8gAC76A7y3RqMKEagX55W2614/x61iquhEGKbe1prvy4Te/r2xodbl/kUDLB356XYefHv0gxS5AqVoa8fMP/+G/fDmGIvU02o/gBBRI9X4l6C/uvalUJ+ekRLX7ctRJvTtcpx1oYnmuTqnWvhzfQUOvUMnmQf3q5Ks8+B04WqPHkweiv4b9BT4UD7ULvegH9wLrQR3xC+BXjYY9yG7J4GoEXiDLQJX0IpxnQHSriduoj1r1CnEaeB4BC7YLUKVr2A/cWmF/mAbOKyzThMV86feSAKtNqrnhzjdUZc/Ij1OPDQEVj2+erjJ3f3DvZWc+vvf4/pPNzkRp/smfaGcY+3xqD1KQD4B330fG4yAhR4Pr4a3fX0SRH/0Z2LXAGaeE4aGA0IaNII3a9qj5dRmJwibQxvWIldAsRMvxshdFsYfjrveje68KG7EmXMlCgk4i1DK+9d97jZoEtShkg8r2RUWfifVGlf1T1HLD95PkPFNsZb2BBU0xq0NUZAkhTW7P3bWbL0WAJtfC+cYfv5KtGFA26QTv0YsP2Brs7UMUVz02mlkoVS8USg/b3+4eV/RI6Gs97Hd6Q5AYvIo5sldNHfOytJ6uYyGuYupkWy8YxAlIXRIurKNelJpB4NOLOtAZSCxvw/Taq8D+XUUS4PIv6jCf1hfTc3jaKAVyVArkyAFytFs2Brk0iv0XxSX6UZGFUzv1Kec8djHVJu+LOELhigRPdJyCM2a93FNRntQ2EFC5s6IsVc/5FUID/jCFZR+msBjRBRW6gbBAlk+AhjaMcZwcP0kAQ+cpOLGSbVpcBRF6S42kz6Q0s4CoSO6hCIrKVWs4uL7yuzJevv5t2BspVJAXJX4nV1uWW4ybGxxdF5X3Y9xN0HQt2t/unQhNPglBR0iouKQm2cMWzuMh9IMYTk1DRDQuvfz+RzFA5zRyqSSPqG6Y+KgazTAtx/U0tU3pgFDkRfUMel8AL/5+AIPY9pPA05yFSqPAQhiraOKCdRvkyqCbacVwFto5LH7Wg3alXOSl9aR3NojDTlCtMdadj+xLwN/NQkBpiWdINQHvnvOrrAcR9belJ5cLLfAnqXHmUyQ7x25osl0CerQda+ti9z1xCwE19AGRaUE7GNdlH3FcklzQNYJ8AKoSgUrPXc3hZ76aa9maPNAf1uxxmPW+9N9RvaU66scLoW8oXGUGY3GIbXYLFo6bNyHBVw6LREc1KJEMkJLgTOJwDYHdEsJ7+ZJWebUlPNI3LKqZ/IX4dToyvIVd0qtoFvOYLhZJQWEKWlRunJg96gbTOYxo79J1hZE+mgl6pnfFoN87u/NJCwx0DKQZpnIWCEpdfxdfsEiLhJf/tAmCuiZERbpuKSJe6M8ridqW6jJgDxe59ObUgkJmHRCw7FqQZRGJ+bLwNlcU0CbyReGt8Z3hltF7huDiD/yuRtew9hGXszDWXxTOlSie640C+soEEuAOBByO/HSZiw5JqdEZxuiQgkEGRFvHEfnPag91q2uOq/oUH3XaQGRsAbLVfpTaLurKMz1PdI6rvCdXQS8dHPgDm91KbyBkuYR2y2GInh2nITNnAIhW56YE+U5t9g6XrpOXUCfwlv5d8mTpqoar+hQdshp6ZqF6zh/KprYCyigmOKaiKYQ0H31QfzR54DALqeOnrMzY25be9+L18eH3R3vE//a2z9STiWHg2JbqwgI5ZUs/7E0mvCAV0aUVm5DoOAOMH9H+1J7N18M+t3+mAlbO9nZOYAfTpVGGwuCHRZ6O4TDskus9x02grEH2P+m62EVfUBYzMPaApLw+NX8xBMYKEhd7riuf/TgOOThhgbzUuF97XRyHUlKTDRCXdyeKcTGyag3tb0vs40Pel/vaZx29JIIB+kH2095IBqDIBqOLXyEEEpoSdtIX0mEcBsKuQgkzkbtERQ74vK4JI6xMbi1Il3wU2qhloEwCQ87//Yh57QgmBJCNXsbI+iW4JfpIXo+s/FDAeHfcpyASJ04AywddVSxMXqErvvS039R9qPeC/hW0jgJAAwsjW0QRe9BD81QEnO09BgNQf9RUoXPTNUxPkuox6UACHFQyvMW5sEJ/yGRlIg9AivB79z5IExjNgGdhPR6LQ2cBuUFDBJKCJxhgPwhA/gWOrUH1AZPyX8t08InwrDaAPzaqP1U1oFEGuorfw+Od3WPypNlH/Xdlf5n+u0L/XUXPmFIrtwpv0iFM0pIuQlwZ6BqJJxPdmr3+qUHleiDt+05TW2+29n9s722fMIfa3WFrRlPbd6RvMkctYpCMPhINVTQSkyAC7ZCTGs29CYzSDIhdmtHBHKN99MbrcUTQE+ITqNGv1hFWO7yK0bkZPeFlRJTohTeB2INWhskGxkZewJEv6l3CSvwh6N0FKZXGcCiCiJzAR1BssQXWx32RMWE36DR9FaSJZlDkCKwwqfAxC501y9JhDNbKvnu6YYM7a++14dC9KcbMdVtw2ifkQQ2CZr9gqGgPkEYQfEfw8R0ZqeENt2PeiIlqMUz2to+je+O+naENowlxDgi4VGEFekXjL6uCMvIXX6R1NdWowwa8I7mQVyFSLu4gkhhacj7lWVuadS4odottOvJLz0d3CcmcLL8QI5DBt2NgV+4xN4Qx28sOHVaC94eX2nOdjDdaAg6JqS82tawU2o7j2dP0C9FsAO9abqBFybNd35lxoTIvaDZYllmy7DvwQdttLGsjxp2lcOi8ZNUKcHgMMIWdR9I8GqHc8cKLfdw5PUvd5JiX3jnjPNXjmlZow5ZaAX/a13+Gl3s1rzuQYHDCNxFaRtMFGMS/uJU4347ktyP6VrFNGt1fAaSlrZ2/XAr5SJuJRGBjHPeHYGAVAwUR9K6CWhRcmS+//xH/sFKjJjUXKD6fGvKeAt8dLynjoLGqVFkblWmebpgUqFtIB5WJ+HKMWJp8OQYIk3NoLLxlFE4MAeb6gOqTiM5pUtdsKyaBPvYSjHBWxKGhGPGlwvILGaDSupFhpA3HPqNRYB9uPSSEerQR6V2oygGPjkSm4v8uRprv1+GoBpXhrHJDGwLCNSJsUL+qY6/9zogb0ZZUZgfQtor/BUYRAuaB3ZDoeRGADEJKNFhHYUwcnONgUzqpcWBjpMNnYDPpxhEC4PMjISah4Jw4RleYy8xQami2CPRp6TKMkzRzHrwYhr0uc+zECzs40KKDIIvMaAcYT4ggqWQdBog7kSf5M1OQpTFAXEpFJTD3RY0HwtNdmIQYgOmhTC47jlscfKySIC3bvqEIHsObkXDOz84SkLjPKFZHBfNMSOcPxxXZ23dQ8xRKw553Wq0Phsk19HND2lhMdI6UPzfFIWGN+CEcBRSUav3WH3jeO2p+D04cyM1P3fEe0h5BbF4yPTwlJBzHLyPZaiCF3vRhjmHGLmFXsIeYwLJieRs36Hq9Tm3U8bXn+TVxQc2prcKv4lFUPlxUq65v9EyBaMFq2JwakNPpXtQ5J4SnF2N1w6pkTgeFlSQxfGGvZEa4AqDF5k0LVj0OusNOQONlCD7sT7aSrFoTDdkRWCK2UL/JOoPs++8Q3aS8YH2vMCeUQspV/RtixwoVJXptbMgKyJmHxnicb1vrSSnsGH7b/X6yKYb8beJiiBYs5c5QJrSw72G4kME2USX3PruH7/XJtXtUdaYN2G+6KxVvFtr5rCY7+MIY7KA1q5Tbmua4JDDIsxFsC8x97X1EIVatnoVclGzmqMBHgpTC1MiXis6xRkonj5UEeR2FcytofJ6Q5z+KqwFBBFaGykUhvWCWVCyOFF/CpK7j0tSJv2XR9LvG6Yuc8lmKzbIip1owVdR7RWrq2SBTvXGOz63i07Mqq5aMejZE1MoR3Qv3vIuToqr1Mcaj+NRaWLdRVTU1RbaMz4d+h8KBIVm3SUV26pllr+Mogp4UmFIzqNeCWuaDI8jJTk5c3o5cPctCvYu66SdLu4sgmWdf2ipBCcmVUuDUdYSGu/QY3Ts9MuI5EjzmWVEaR+07YAmXadT1Rwdhl/1WrTJQsQ7QkTJ+DPzYq6LfKr06QOcB65lBEswEFgkIFE4/oCUMpa9QS5WWbSMeE/m33E6gXwDPHCNS1R+BABHEDMNlkyPHe8WGBQtvVE9kF0ems4Dspmadmc6MuAMuYmRPJlZ/0E0y15UuHROdutzqyNPtccnbqD+l27IoFNLdLwAqh+JRq7hKgfzXgezxGUdYNsRbtIlmsWyPjfxDKq3i2g8hkWa1bDpBMittAFdtrlo3uPSHvbSkzlQSmvz01eE6pZYUCNgbB1baMA5q4ja4jZBTshL8zWF7F40ze9stndgEmDpuHkpzi+l52AmY1MiWEZJ9efBcsKA3C+nGyYpvJ6sKgOgDuu+vw861ysyklNJXsQ9T4MMpBE3G1NB92F+MKamInWaF7fPd2L/vIuv3lG7654kgfZOEy/pSBIVqJlKJDFM8uSRVJ3uNthewnp79GnxpdYLjFlCNTOpFGvZg4GOXTMIWmUcLhGaZN4gPDvjpu6M9Ti8F/JcyLHVpWKTZW6B0LmFC1lu1Vdd/yk6YWWPdAdOSB0uXceQkF1DKd/1VyeEpC2t8bN4sPjaTnUwZiq3tiVTgYiZU+zBu5TKgLkkxJmO/0y0M+r2Et0dZHCVMAu6I/FafgIiPsNIm1VX9GWCVgXgubO8zjKnFsoVFn7GLqtmJU7931N+XmzVVsI4iF/ooclFz2rjHmFahN3nZOzlqtytBooupnuXKQf1jRKks5xH4JZHBJbkeWnqlu6u3Yd/uQ0JCvOqMGQcMwxkHgJalW8IFuI9RFVZvofMvTLcdiBmAsrQD8AqDNI4oINrGUnnHMlVVbyyH4Nl9sYBwLPYrEDLJOGlgPqdd2+7ekvW15XzhsupU5Q4QjkpqeM4U2OcoOeqqyFUtRLZ94FODrbpV4zC5OQpidhbYVHTA/fQ0puRs4txwCUlADEv6E6UYZIDWRzw7w77wFk/TlBNjn39dgAD41vzcV6dtXlryHD0wp2jkAbwgeWRjgvrkyYaGuaFhaizRC2r1LWYLWdCnZYaGa5YB7UtAbw2g/Sygfe6+C8j6N7YAmD5N5OlBc1b212AEIZ/COoxi+tkSi1hZIXLXTmD2RG+rErFy3yUsDhge/IIO7+wofGKxsE/P7LYsut0T9Jwk/1sWzqjCDp5xN+UUSn0Bgd+mth2O6ug35JIw2g3msSznyR4+ARZoaw1I6WcrGDb0JMsaz2lAVS2Rk0UCxwHbjBkB7jmJKaQFdx6L5eSPHtaLpiIS79P11UZDC9XCwoI98/ptjaDq8irzjEQ793rDQfVGDtVSNmfSyZw1cLclYIsKmDVy+PqcO1jV09vt2iW+UMghfKh+kI7K6UaaKPqdOBoVQc5ZLdk2yNwwYFiLLTGoycN1q8jSYRtCKhXnsKwFjlv/JniJ5I6Um2CmqFf9qkM8QFNaCazEVM0E0rwqjZW3BMhLHa3ZFwCKNLSIJ/5FdkVMPtbCGjUpGFOGnWuQPekHOkXJd8zb6SeySfihziyWkFFM7EK3ydrjBLiK1Tes95xZji6HPUDeY1XlDtCS6fc2NKG57MYCwX1HIPpVjzcAF8RE9lLOuNRL07QnQOaDalatcSFHgdki5U9phVmwFpqeWdpuMnOd5hSJL0wCjXfmvGyXUIEQ6qR9ipah71nLXbE4KRpcYtN6cdt5XdAU0101Y6ezhjYVOvm7T4Fbk8apqi0h0qn8wCL8crLnMBILR0r8tVmqG/VTgFC7MLKs8y/HXffEP1n8cixNfF3r4A/7TrNaH/jMRbzlmqg0OA7KrDo5mHc3uPDMA609GZ9yIxlKw1mCKNpYa2zGCrNAo0epMESe++yuQGf92cWwfS5k79JUojdCAX4zu1Jk5dxy8ev0CRYfhu+gG6cfB94Fv5VT7+iv6YzQlqhwRPKaNiuoz/TgfMWO16R4V1OCfs0Ahr7XpIBWUxJ4zRZAa0Yy5Wq2VFszImhNiZQ1R0DkOiRhhX0WZ1pCClwkLkEN+zWLT0b24fq4mUmZpsU7W81+hxtvy+zMNUum2GZlg82BahmeUHNXcc2a1pq107hTULPwvh8mKavQKYjqz8AR82R4sWiSTP6U9RlGofHdIMQ0YN6YzTE1IR3ZkyGc2yicWmVOdLMedsM7nThQbrJ2vr/tOqcXK0kWuC1Tg03OlQ3CTTL41ErmV2muQs3mOqb4k4Wl/Ra+YZq/Cmf721He2PCa09vDhyt/gCkLeQ+fPJf1nd5biQW/4SyDb2Vewa8bjZrJlKJTkNUoG1kbExrCfnMLzQ3ReQOVuZgAKEjTID4Z+B3ufaP+FHpuNQ7yB6FaySbPlqA3s3q23CzoGr54RbEWLcrupTvL8wZ7KffazZbYdPtC813YlzHQAArFZX1qZrFzEnSKOnXrx1dhvx0NMP05tI1wJ9wSt6tbzUSEnqBzKhCoIc5ecJkiJyVDxCyyfCRBzkeKf1RqUjh9GaUpqvybjcfRV24dUb6PvTS4TeS7xaDfVcvIbSQzaiibJS0xP+VITLijWiFiwSmv84CckUyF//VDVgu1YC0Zygxm2uYFkmk7++h0ROZRbZo0qivrtSw1qjyJatQ4uQLpC0lLLM1ANFONSl9KC6LyGZBPgD8v9rkJC/0YUWXano1966GMkewaoQd4SddP/RpPkpohTD2w0lAshaRyLIUdwr/K/v8MExQUK8+PSFpPick1JIsCVOC8K4wuM/8ChKyzxM4135KOEjqDvuxHdSxCPwiEKfYtFJOdxa+YdYh+MHirJ4Axdh6inqPGqYsSdrduIsiknB72f/B7GXcWrO0qfUGAtIux/jRfjLMqoZ5cVliUDZA7Y9O2M7zHYqEgLbMc8BN0mV0Sno1tstvCAeytpTseYdU77M5zhYQngJpF4Xl3TptL3CGs/62pP2qQQWHkOfaGAQxsx0aYB8shpHMJOl1qg3LlgBwt98nLcvDeC6s6/USzSu9GnkKz/eW8Wv9VBOitCPsIDgccf4ePktSBidhnsBYOFpvZJkYN5wVWYRQWfPy359Ygu92T4IqTwqP3XZl68yjzPR72j1hLh90iRw+OV0SvDCxEvdW+Wxpx5vAtMQKnRwmragPlj/b5NUx2WCWhKj5T5c1pVJZBF3EcQJWUOo7azgwlPM1pr78w9bn6OKd7hMrsnxhWNyzc8Tv0B6Qip9UNG2fZdlTfLM1TBrLRcLs6WiykLEzZ1umrTb+XYV/ay+QsZVZRbs6kHQyEwm3iu5sGxHMmdbULtpgh28a5sHOzDbsY8sy1Dec1YmEL/V7r6ImAUht1oWXVmdSEd6ZoRDIgWPvMPGD9m5Jq9Ye2XikNBo6CGfZBK1WEPewl8dTWSKEuhxzcE7XOlWuk7k0ovuIGeLkDrnjlZ1mSrYuQUmdyd8UsfHNMfyaSU2+O+e+kSOSiO6IqnK0S+n8Pr6T/rStcBpeJtfPhpPnxa9SQYeRq2N2sBL/Gx4p439ysNCpixH/eL/MT/GlWnG2bM9ZdXiZBCkW+qpCrXTQgUtgca6qY0NtDFD3TERSsr1RssSQLqNlAUHMBaiw7kJ4tuYOyNnZr7GOiMGlBUXPmGSCY5Xq0OQ5dGQghI2I0kwTsIJtOgT0ChsZ6+3k7QVSpT0mKmSg2x5acpl6+5Xlu1NcmGXTQVS/vNcxFsQ4wJUhoYXXCMlu/cw3oqaBgrYWmzfE3E7z1rYdNaplsYklNm2OUmpzBIU7QDIc70yJHJkzGMruTNjim1erE7iQlQrdxf6UeTMGxXJgIG7iR3M+f85O3MB25o0YxZuG91Q+DYGu0DoKb6nnHR+dR4CqblVUBNBhJQspMgem9xS+JWpLgCpVZjlexSmBzVS7auVaxAfF4rIAwQyK+sQBRnOSAmoAVjbs/ct/wVMoAwNmrWTi460P3UApR/RvUxK+UsPGrEmGj/j4ratRHZSKGheYn4pylijR5N9BbymLzNAevQK7ASo2ZJf/tuRmixN8zlGd4OSbhRHQ3x3LUhsZjnG9rJhtPcSq1fVBPJoOSEHZk/cow7nl/wWyvarESXVjKU1y4H/WDiiY4hytZ5LasnvehQMcfbFboqFax3iKC9WvTaCeMO71AdGDlAy00qpPM3RbMAqT0JCkjUwa45arCjVOwZDuelDbvyo4Tp3Xnm+mJ3fgMMcBqd2x2VCMyd99nuTLxRGbM3fcTITtZDzGpALzBbDWw5bpC7giZB24tKNk/Ec2nNuOwWai8tePjuOi4W0/RxUObf+h5roRiMplYf3gbxGEnl0wMj5PElBeKua9iuM+WQH4wp1bmE/u7r3ff7OjU6daB3e/VrJzOe+0KrZiaW0ymdVbFDvZOTioFxWQSdFXs6Pjw1Z5JJK2LxagfsDKwHmpop3ZmQW0sOI7ugTmGaUDajfdsD2CrCNCWdcrGIspQuukkQHFzS1yHmCeQLYkSHLrVUHW0AjurackUcrxrzLk3SRBaETC2Bz8EGhp+NDRaOuzyQ+AG2g/Jhgal8FRQAIx8iwqA6e2coPbIg2gmsPtYu65ZmFoSFtLliMz6czIdFulUM4rHp6V60JxuEUNtw8sR3bjUx1t76J6JRZlGL6t8xJtU6Noft8G1KTrI6dpMDZB0mU+zyjLoTL9QmbU8S4tLyirCKfpJTGA1A6j5YBdq4gw8nqOJtPjOAuxIhm4juQFYGu3Be0GmlYzae8W9M8lWXr6hXKPefVzFbWLN2ideXqmdgq82yd3wRP/K65PvI7Mbqzbi4z6efCXeHmdxMFUNm6MHmvnmaq0AO7ZqcjppOJyYOvctrPCxXmCzCcBl0gjhALiOBMEMaDJ1oA/R6CsN9NfZCV51Tp7XsJXSIszaTOQ1QNl1uDplHUr1N0izzLYnX53XstZBuY3RrWChNNqxEhSk+mS6CtzAl5ycGnDhy/1vLvgPQedMJlagRV+YixGUKsgtg8QTdbyzNpdqnlYe2Qwq3rGVxdwh0uw62db+CKQ4F+XJXbmAMqTI85GUJzfqAvhSVnoo5ZWZLrRMRVe44oWGmFCkRpt4MkW6+kOaRZvrc1hDmyvuPrSe20hzVk22FtOAJw8hNObzyzl4LtMas4jNpxfJlaUojEh0zjGySTrIsDXscWLHWm2u7d/ZOgtJ8KlZTk+zU7PskqR04shoq7JW4oLd1CiEXLcAIj1Wg6SZ4559ApDHvpTFIiJYfJwYmt0c658TSb70Cv7aih2j2eGW7WDcjEaqjPaWyy3vyoaqJ8rIRnitpGiQNvZNRHpgBxHV6d4LB+wv9ToOu9LomLMvviiOyLEsb1sm87q0vLlWPc2iu+jSV6U4wxkSfMlykXdmvo3xqXIPf2w19Fg3e+uq0ZTN5ijCjt6SY+FzHfxvlcAN044ZoaJVPsPAKLPl343QjS46JaDsG0i5yrzKYiWnVLuN6Npiy4dTypijGDAif99GFOyKGLJovjyZe/40n232PqbepfoICYOSQUJTDljqkOaozIpZjRz5pFjGtxc5TUDmltMX4jy+uvC95nqt+XSt1lz+pvbluFEHtvpETscvcE9am1TPMzVbsubyyje19af4v7KKmVOCuRkS/jk7CVPIC1HJdqlRX1mrkqoz0yR/mBQ2Ueycg8uVPCjcKrdh/630yMA9p2jRu6eNKez3oW4UtOWMmT4nYjyK824UU5pYfYgjhcJwRmeXGxDyUTUZTzJmA16UE7d3D+jwVJyQB9hqUX+yR128eUUd+ab3JbtlZTXIGeb80/Y/xcwtYh9TlUutFcmEQnh3KxSqrNLfLXLeb8oUXl346ac73N463jl7tbW//3Jr+7uz/b2DPbyRdJ1vqXmFQQSU869RryOrpTSn14G4V3fDn+yLNLrHjCPto5rJJs3JUp28TRQnTynT/P5IppqT9zcB0tEAiZHUMglw3QkMhgMHLLskaUftgUxKY8cEpwMnH3k60MnKy9KY69hdqIr5kxMKjsAEt9l7UVyhgfMUbmYztmcddaydOEG/mXSgW+QCz8q1sQPSUFJDC7zReAyEBm153qBKMpNAHc54mJcJhoFXapLXeIyZ8dtHC7zzeDKJb9LLgtL5xTG7Gmwy18AVy2HJntu3XWg/J9oHYBTVPFcQJ5Tuq2gtieKQ8EPl8S79qHVGIiXumcw63++dHWydfKceb/3kBkZ4b/Jfhl3OZttH8uzeUv5dxvkYMAdtwFYPW9Y+/YRDYLKFhb4PxYSvs9/t02mJjJ9O4rzr6P7Ij/1bFNK+0BDIyUHBcMJ3UGmMSRhRvLcqo167q9PEoShDXcfdzcPUv+Q+40TqqHzVpIeelfnPveekH8W3J6NbmSOab9qYma46n/LaznotQVJO68yywdSCqnTRVRwyCim70GSN0tXmpMSaI4OVcnQBrO4ElwTb3KRbvwwxHDOheUnqOE2bhFUrb6YdgqoycouplyHoRX2AFxuwwYCrwuEKsaiSEZjFTkPPZuHO5N/G5aszccOKW8gkpNLmkyuN9yImmr2Igbm8zdFxvQBQg3J2amKQNv5l8v0XznHbTtZU6v2k7VB+8jpNJWjMTgrsmgRSwPxGQaYhxWbLb+dB/vAL3M+Byaj7RFp4hx7eMwCbTeUqitADGzYx9ebC71YoR5a64wC5H9Qgf4slLMmus4LvSpBf2WmePpNbM/Iwszsd9vextlB83boQK0rlJzlBFh7pD0y7rg8kgZOzSAWRIuivA0xNTg6YNSkZ+VCeSag8zND6upmi6SVXVqgkKYOLS2a929pHZNPGJbAwXZfanGYX0hpb2Srgf2x2feiUedAnw2WyFiML1GZhqWi1EHiy/0n6554LgCTGRvKg3qmH+Xr36OigBffcOFeMEB0jzleskppbvtCMUzqUa1PAHybQaDkbaDRewksl7kWzpTbCJ0YAhRU4WfgIQ+ZMWwRbOrNH0qezbBLu6XLGcdTV4FqLrEijj2UOzXtM1D9IwqRCaZPSAMNc8B45YJFS/cTa7s0xI8/125DvLBNZxgxyjNLB6OUwxdyt6ebYdsNklOyTO37FH6aRVMzLOVMXeR5v7eyeHe8e7W/96NxU+7XQguCkFJUwdyfXcdi/oVjdGUvT0joCET4tsMiu2ti1V5PajaXhVbSMOVbPj1NGFLEoxqksVvnw29/RFVGkH/jw2/9FYIbjMlzLQ3aG5pdbRq5dYvlqiW9gXRKv2+2Ppf5i4s5Yd5vF+ogpis7cNLqm8q8LTeW56SybKcOozg3bYl41WW6cC9fmupCzn0/ndGU2BMwIncmuPnEJPXMwcah6unXxk6KH0ntTAqvdSsarwHw7kt8MsU/DGHubFgPmSyyKAEvZYBpCg7lRNMXk2hn5HGlmXwpp5x+gPIIzTbx5bjYV57J5Kcy64q72SbCHq0uef/iPv6PFq3MdOCJwFR5AaPAWV6uTc+Ic/9v/Id5EWOOBzGOlxTqXfbxNWh5kHscwLuCoiE6ftqHjgUb6sg1x+WkBI3dCbQuwbh2otNA7ndjcGvBUKpTNSYzTjGLTG6/gpNCe0I9IH1aZoK6W52fMf2Te2Qd0spgEVlvZa7yeiK/EbUS5nPC+p09DD/ktZP3RAtLX5QKSPJRmsFt4OiVcCXIzyB9Ntcr+S6WzN2Vg0ZXTwVgd8K076LzHeMCtzxqV1Zbq9Ie//79Zkvj7/xcoZqxVj7Kc4//7laPedwdRnTyUBc4QBKa7fiGVwckI/7x48RgaXsMbIz/89vfAxfTdNKxQLCNeVQztYUGPLia1XGdWG0648HIus4A6rZdNcllbMBlwkkmxLYphbahYVUx0wzDJ1yW14tTpN16n8ktvca3xVTVjdpvhgiinQ2kESo8AGcqy0pKRbmHylaU9LrYJTRYePPzmUzV+E8vbMNOwnpVxvvmmTKZxfOjyZ6IsQ5gxf8VHa/Vv+nyqOORGLVOtXq/bGpyxGbnxd2KtjqEEMSnSpxi9ytjCmoFi169mu2FjTSlw5nMuzfq22DPu0kIOvy8etzyayx+/PlSWgFVDV/jbJawKVMy4wukVU2h6P192VSikZ2H3LTiN+l0kvvOGwP9DgZ16z/Ew5xk8kkL3UchZVciRvX800ygWT/x7P6TUwiiAfILFvmwWOzucmbWg1IEtpXibTPU4ntGIWhLZVpRSr6Xam8yZ4OCnf68l56lGGp3nTsvDvrw6Qt+7RncBnVzj5TXWhRQ+prse9sOUzOJ4PyCKj57K171A3KZ/Q/ctWCIW3qKdhr3eor5usIoJuSmH1ZK51RBaGaISgpJ1IywfL28HWaBLl8tcRsOYbrW7UM5wdfE9Vg374uVh+1t9oTecndF87ymDs7xuEnX8KGZX9V3dsulEqg29S7yjQr6t1vg6Dl+c3/lx6PfTc+SegwVKZXZ11aOFAkCuAx9vxAluB3Caw0s2/hzScTP9SMMrT76yptVUmvxdmhqyv5F58l/NsuheF0is1PBmWIWiU+7uSUpxcC761NXTJZfE4CWw6n4shTaAgOmZiWZ/aAoKmFgSPyzjJazhVVTNXPh0ErKNFH/XretezQUvBQbS2eZRDbyaE1kA8qzasuWqa92bx6wnSwepyrEgR4bsaJadEdmDSrIgq2XuotnIpJvPlM7dJvQHNtLwQazQSiPVneUGmQLLQYFZ5VvmZ8jgOz30zEkjyYUzZys961F/Gwtujj2eYkP3nhfwBVmBJT8UqTiXtYEI8zEmKJxUBlGYcyL8RBYcC2Kpk27ZwSOvYG50UcSTxT9FEN0Mn+FHaJ8cWVGxKPIajYgnfdPoBldVqYBCHY6zpJ2YCw1INOrNNbQxmaGFfdQALXIGDuzxh7//zzPDtuax0Myw8pS67xZaA2pZrX9dufI/J42+YWxKPzy3Uv9haq2VeUIg3WO64uL6ZqmJGFvX1b7TfT8l9n4kBedKxi4x+yrhzKgeEHw5v0rIJTK2SJ7z9WZfqpHK29Qm51lMSD25W0wryp9Wp3cfccE3JPvpMFGS5+y5nDv28+uHxX6ajUk7VmQsjkVhnwW1Sm2QMGa7OKwM/Thhsfmf/wnRbna9idwCyeLgw6GeHyszCCNrHpYz1OP7zD7aGPygyNRpTHZ1al6/mWkAp+an/Ph0lGtZFaSM8eUz18MdyUuZ0Ho5E9IyVoFreV7oyjm9myKzPN/nSENoK4DV9VBL9iGTolhgTcXz2y84+6a9cp8aOcR2iimSzDB1kRVDkd+gSvXfc2X4LA2OthQ2GhGeOUQn1ZaYN1JaTpC+bqtYes7RR+mulAVURhGq3FQePXWhPyBb5zyINI6jwjM6igcjcpZP5IKry83X+tgJKDZ7OXq9Sj4k6ctxaU/yVrFcUcc4NmMa82tZnRRadNDFHZlvEjZreKzF1Skhiepo80tjpJpr/WaJhRvPBoVyDjYFElhizx8kAXup0c+CvSFFvVB5bHBRzLWqGT/PHajH7ypw5qzUKick+MGPnRB2sspfpSP47y7aheEvGvWXBN5ACg+wS2B5km/gB/CKBOTAWgV9Bk45OPc6F41rdZ8j5awYOYtNfo1sskFs8sFpmQvtGw/bGykUzVn6qGauFIMuM3/B0J4tpdd5XFerWRsMlMvMCdaE+c3O+UXUHZXtlD19t3AP86Zl06a5EZD6UjpzszT8sgIl3eLSB5KKW6EA+FQeDFAESIUGPCwuoLBL7MWf8eCfNggZVzA1pqConrxyY5OR9qI4GIBwri57b9m3eEzr0kCpmOjujxI3dxWZW6x3KgIbR71AuRvj5ebqkfvywCvOi5to71De5BlLVrkguMJh8YrZWMg1VRBja3ExGdUvaTCQV0qHxQZOwyjnErvcGF3xlVgm7LNiTfquFfBW1TeLMdfrdcBVWdEZbnnr5W555QfFUgPv5/E9zXAjTXtlhua8zOOwve68OM0locj4urC6hX2N54Lb3vlTnaR8hHXTDr12grKby9VKOfTZMzvLlfgzTd9J0JGZ+69ct51PBNOKnXphe7h9XDtyR/Bm+wZWRaZXJVNESpPS6cs1lXcqVP591YVyJw+PMKIvrS1ESHXyCaa4TF4bSNehOZQgtnOas0la3nbWDpk/UFBg/PlHjOaxfOHrz8YXJNkV6/EytDlbbZcjP0Tj4dHuG8KkvJL4D8zNLZ2uUnpMowxNzzGdQlAO8NRIJJlUPh1Fq9mb1SPLwXrKip5KQ2vFNFSQGcua+XkmGv+hT7hSc2f6q/Xc69WpHJ/5yVw6EJgHCelh85A/HDkJI7QvWU4HmDstwSs8fmfV9JdRlKJ9lL2gl0TPT9JFpCYKekmyOnvXcahIs/fg5HMP8qQqkqgfo4uQ3Ov5LvuAYyCDnwZtGLLSwRBC8EUxDXB1ffwErKFmAFX/+z4et4IrQVj8sgi2Ki7jDWDXwvOr8pgZBWmRpDFNjTs7b4ig2wk7qdQAXfq9Hq4j4ZFfD2w/mENZ3C3z9yq7IEX9xb1tEfV7o1lZBJI2VsM8AjKv278mE3iY60Xp0ZLzAy3Pdba8C2IQf/yekipUqunJDD+KvIbPTo33SdR2j1PZ5dR143dGSXciSQZ/ht1Aq+0shR3812gqsBxq7eg1KupkUYFLE2vLUCaj2JuqyZuhxXtaNG2P1eI93Lo1lwaP506FkJTmUHyQki93c0Ns86yMYi+v1BszA8ncmDFeKFXLPSxHRw7IAxJolCjhSlNplKjgHDXcvIrAEj3d47J15Op/jtQTOWzRkuJbVij5B6xdGcF4fNje3W6DnF0U1VcM6+MSjcxUsmkFWzpLvVbI0Ep0ZvOLKrlmov5BxJtbEG+OpeOXF9Tl/bptP74KcKDQmbrpCWDH4q3VUrD7gX8XPAhsyQhzTeRl1wfqsohFljGgOYLlj2W6UQ4TcFRiH+MoMf9J+cH+WCsP8sf6tAHbHxO6XRSv/XAPr2nHoY9E9tefB9mfMvz7MwaCc/R32XH2j4TwxmMQ/kn1wg6eP71ieCZepytv08eqbh+ij3xwbPe8Md4Z4pAXJ7VEo74qsxNzLFSJXuTjRzZ8qKa1OLR5pobH7cEqm1+MymehWHH0cZHSH0FhcZF28SG5lB7RpkxgX9Dq3PmlHkYPM/SU43SmhvIzaydtBWT6GPVja/YVHSaTxSdYZbN01FMOirYOzE4UUn0MKWH6kQJKmpmM5DPscE8/yw5HQps+LWWc6vV7tWjxIPWg/SevU3ZOhRMnENk5oDs65GnKxgM/7NvRgk4E5Qn6kib8WWZODvtphJGVrB9NhEce52g4xMiY4C6IR+k1OtnRJbWos0RAwfswIdc7jK7krtGtyDU4oabRsHMNAOpiS8dXwhHbB+DXQRxQvlyEK7be/IiwokuBnSI9bJhwRCV3ETWhYb/TG3apKdnD++sIehKllMsVK/k9YNNdCoeUelzvFjkYTVlQpcyFD/A8AR6j+v3P/wRHrWSA4JOK0b0Sir6DE2/C7i30nNjpezFz/Zju7OZv5sLjK0urwmXxWmPPjnW4soLZMuEOfMHcvdJXMPH0391gIJ5Hf/HKXrw9tLmhbwqWJ+3Di18FHV6eYZB4/Spret7dAImfqvskO3hj4w1sgR3xXDRxV0zkrmhujASsVDJ3FxhttLp2pB/19zrqqhH2yKfpy+qndZ7MN1iB1ApWVXV3w3P7njIoLIFJ5yj55BRW2ZA1ZLz72dSsyinIaCGKsyU87gKUhdIU9Nn8Mbk7JJbdpPQPjLFayCemL4/4oOx+/+0//f7/yjm4Zn3ZH31Pi5Q42QGZEmPL+8nmbaE5M0v8ck7WOLR43D1yFOZBzIKARaJkt0jZ5XzgORHQMAWB66wrwIkIr4538xz+/JqpTxbY+mL5YKdR6vcEojnB7BrXFGd92fNT1VEdwM1E+nOhnbbrlk0DqPl7W+9orxfo7xAEGM/vdED4plUNP/HK7BnaSTzWNKo1YVtEuBtOW/Zyc9rqOG3ZLtelgJWb/0sOrZ8TeEe72bswCbl5vFhvnuQGZJmKhgnl406K+c+zzaKk8p8pRtbvDa59T99JtrxWnSNadnbe0jzXeVDgbGkCosJo1znsG5lAk090/2Mh05YKi9W1WnPtaQ34SaPeWKtWCoJji257/JiA1+lRG3NczlSQ0EVRhsPv/H5468tQVmSyR0OU15r1NeBjICsshv3FaIjX6lCIfSDHrvON5nLilO8YU7O6FqUDlMxYCTF8W0fBIntSsJlPiiL9srt9PuvRAxIdrzamXkLCS2298CzrjbOSoNW/6qT6cbGpZW5O1ccGsJYmyNmXm3LnBuVsmJ3hoAvyZ8Liv1hOHhbD9MiUwFkGXmzgykyEKd8S4xIIuXCtTDkHoePCvemL/N1hDwqknYvInhYQmVfcH3dkH/7m77KxaG756kTlrwHJxo6peyiFJsDqxD1KSr17f5RQB7JUmyfJsvxa+bAp6yB6GcawLXsBnJpALpOnUjpvVu0oKuswMCWOyrp6sPbQJN3NZSdlgrOx6dPFi2lWRZV9IJu8zeZmdBbrFLg7PDM5a9gw2tGx4xJXm3hqYzFuc8x/J9lkNptFrBOkmpVsKrSyuapO7HlqYwoi5aRkJO0W3yEkPvztPwKeSWOQC3uzD2RmpFr8mppgrXgWr+Kwm7Ex4Kt2cDvAxGHbNJsJJUiEI0DqoZvN4mWI90eGfbzqZaUBh66aaF7G1WppGLVw9yzXSyK3+ziXvBQbtKXVNDtxS9OdOtzsZdmUaxmvMDktm3bPp7ZYndur7aeXPmyb+R5hQTB6hMc0XP2zSG5FI3O9Af/VCxBzVyVRjAH/lLrqhH87masqRmVtJ26ierAv6Hrw260HGO5UuCk9C5RELcDisBX3lJ4JdVGyGzLJFHwyYPlyTplBygeguLkTdMpRwa0gHOKpY2sg1MiGDUr1ilJuTqybleA7nYuh/wfBbeR5lipSnmLf1evyar3TOpaH8zecPRxHsF6QijsfwPgKr+Qhpa+opO8X8P2i+HsWE5WzQb8H8zhmqFYEow9jIEjWuws5LgUpHQ2C6JKqIqyEjDoG2p2fUZgyQA/+i2bcnD7VgQ0QnkF5famZJAdnkhabMEHNDbfW81m1mmhCWtTVZEmJIlbaTmrinfLoNfQridCm7va3ZyftH/d3cVhEVbheeLLM0fybOQObF2wHFJdc2eWoNwxEgcfkI+KcdT3XU3JOz8iFshDookRbSPHxSdADAc9OBDWZ5vz7dP640um+v/JqrfdhyqHqKvTWi61lRarqWNOMhd8N/f0des6ekQst/Wf3cOfs5K++3zrePTt89QpdZ0FCjttx0O9WTutsNwkSaMYCS0epLEhyzYX/lFXSboX6jXYPtIe4fbh/AuN6R0XJJMpZtYw/aEVelw3vpSux1FTXnDqWH1MFr0bXdZTXcVGdXpRSYflP10H3ZG1fq+X6Fpzp63UqNa5EvsyipI7lFOL0jd2ZP/yH/1LUORPyxl3UlTBMQdXJVvo1pkAQ+RFhaoSy3hErLcAC5kwoHZGJX3JGJBMrTMEC7ZfugIxTd3E7poqLBFll3na0v7ju2+mfj589riPrknl9xTxwtnC6O3wn1E6YalegLWGSTWWYkVEKVFvq1vpxfhfwinY09MeSflm/I6GsUinINvGJHdZZopnDYV2mUhCuLFHsKx4mb8O+vL0O4zvty7cLvNdd3/UpzvDzObTP5c6u/e83izzu7T6Ue9+X50NN/tx9uIPP4cEd/Mn5b6//eflvF/ru/EG8Xv/gPt1FMP4k3bw/yZz8ubp+T5nFz+0NPse8rP95eIiXIvmn7zRe1kJx/oxP2MIceVBsaAUzIOHnnb/nIil72lEEY+Mb6v1INMuZBKuz151zV0wRyAK6bHxtUaxNyviBRFxKGT3wcb8voOAS72e7edhPdAdKEojk70L7WFahaEjmrCiwnc/NUj56A14vTz3iSITuxJff7JMjoozexXPGXi2ubK0zLjfFF7z6x3HrLprWck/vP9UuKo3Ap+lhsXN6yQLQ2EH6t7oCRFbuzW53+WHr8vM7fv/07HFKnScOhr00XGT9rNi+DgfJn4U5To3vVdiDY6eHRhscIa4g4EbXfv8KSEoK82GQOCdx1zva7/VOZF3MYCl/Kis+yqGNIhvUVq/noSVEtea9OyULR4G1SnaV7G2WwUq1pPXC8L2qNSzqwpL3qd2rSx4uaxFopwu7mth1V7CWfDuRRq1sEbRFGZSF3VNVXpm1pmj7ilNirxY5/BW579m6wQs6IucvBdE4zt8AslC0P66hQWGlYINcbtTK/EDNtDvRgYX+k6LI8FF+K6cLHJMvSy8l277x6vBNu+y+kGzvjIJzimQkmXXpyAqFOOeaDszXLy/oWDAbdu6SEpgXzUB5Bo2fkVlzpDpMXK2hXHIdvEnTpmu9BlA1ZnHvAh1YlmbwH+nDsKq7YRQrZtVypKYKr5NI6mjldT+V3fI4FwkuFOUnYSHcJUpGzAuRWJqLEoK0iRL/zUeYqoE5aTJHl6o+KmscLc1Ued0+dBSOsfR8MZM8HRLNq/YUSkEQKbyjVurhVmXkYmE4IYy0l01+agg/J3NM/hz9eyjyDXNtRX2Y4J+yCFEUrPbm8OzV/t72d7vHeadMH+TWOLpHTwH8eef3hiBPfPjt3wHXCQZ8f2CvK/w49kdGPsGy7BImnTzGMmaKfSmMB0MaD4MNFU+1hUDqYUJ/Pb+Kiv3Mywt66Sv5BEWAC/mgYZKXDgKlJHDq7V+eHL6psw9HeEnQsSeZt+QCIjp+2rk2NRkeyAVAxsH7ASp/pAekkXW2+n5vlIadxLOFqzTCrHHkFdPGn7bP0phunuugH5X0m/HjmwDPMD46i1i3273Z2f3l2cutnde7JzQ/jafLWvv9Zu9V+8ell1tvvqNf6mbFDkm6VlvAO2ASFfOvPdLbqtTVqxN1g76PmQWfANY64SBYvITTtMCNJOPyJbxLP+RrLauPdOC6C5PwQm9lWfmWuI8SFWXaIHf4vPexPyJb+GRyIdsTLKGbg1FO1J4FxhkMASh3sBqNsuwreYIYty/ldmPtrAHb45JT66XlCPbu1HYbw3kDWq059ff55WlhfVxhNoQgjjF5gtuDXXxpd8CGgPkSbAiDOIAqmTEc0cuSMVTSqOuPHP+3zjBJo9tXcXRbM0C29cvTAiAF9dtRze7Etnx5WtgJ1/9Oyl6GiNgVL/f6NDsZuAqC20E6Qj81EgUlSN5r2/5FzerSlnp5WogXmnmnY+rGUHeG6WUJdseTzOKcbTSjlbhIKSApLdlIrkrpvz+Eld8j3o4pC/ogd2OYsnVHpt9nGx+aqPViBSGjYLG+4wa+C0aE4GP1dDqFxLjKdvo+51HomabxdnDkIi0HLDCvFmX4gHX5Thc+zQF/499B6cRqgNgBtzLFVdHzbQUPR0QvCu8i95ZDoLmx4+geGpMqQckDTlk0suMWU5AyQHAmyzn0Cn7k1l7n2o/Tt+hDQtjc1o8OOp+qez+Ri19eAkU7Dpm02drtKcuy2kI3FuyLFzBsMbgXx0ECQvXhRRLEd8hZ3wUU2b1gVp/ujEdpMaX7e00E9Q4H2h1jkDh5wADOVpYlC5aiYxzVIwbuFfaNS01rp6haPbq8hDpvrUYdhzPGC7QNx3qo30dcaSdJ6QwJKwuviRlEIICrsCXa1Ex4gp09j+8aK0Y9erD6PT7+KdkHDgXJqN8xwgQ25BldyP112AuA8PgiZnMEI/FmIXdRBm2HdH+RuAxAkPHgsHIFjGgQviRH1MkSsJizpO8PYL9Lzx1lIVIGAKhHN9VCZ5cuSCcaOhb8VRL1vYwjCQKhgqj+ZB9arodGzYgyBVSquWtczGUq8kIsleXACfZz8wwg2KrJf/BOecOQCeu05LYY04TK01iQmdHN3YiLi2DmMzRn3zxQvpYseBE9K0Fu4+u3uS1xG90F2Rw2ei/wPNiG76ZeiHODhCAxdgO8zjMjL7i9BWftJrEVf3ZVakx9hHmFkrQSPO+G+oDfKVUE5lcyCL45Nc6eWGSj7D4UU2cWiu1jqPmthXbziokUOdcRCBSwMr1YXrWLNoJomHoYAI15es3ZdcH8lxdhAa8Y6wWsDgZ5dvEQEsBtttkAeTMOQGZOrpFzYDw/9pShSaFvExkGGUVgoMk9Jh3gkP7764hypFwFxJUqUh6UcRoffvN/VuTt8ACL00P7SobGFAEJ7iWdGLnbr4a3g5rJzmKuvaWb7pVvYrdaZ2AvjfOR7DwwH0yMrM+G1Ck/ESE6v23Qq2QQApuNSdpOaAEwMNQYY84Cis3DIVM/aEXg+xgVt2LJBJOwhA9HHfxgJTHgo80hSi16G2VZ3OyjXBfFhU26bIGE18lffzk2Yql5akf4Oy8wypwltUp1cm5AYxbwVxb44o2cODPL/VxmW6bu9ngv8HKpXBxHLzNGtc/honSa1h9wSerxMj2XFMwWIznBtFu1zhrmWGHOD2aM7t6Ee94lYLQNInYatRPt6IfwGfW8NzC6nb2B66FvpZ4YEPysrNu4vtHu51kFnohKu9Fo0f+AlV/xijd5uaWEaPgK9oo9OOWEU56w8laoALayvNJa+wb+N0crGVOE3q6h1St1fDom4wSjxGJ7EglQMq5f5jDA7+YfJYFJc0PEdxYQUTaMBWtWgWY1Db871fNquYFSoqHE0nVby8cKsn9R8B0afoctG7jM0SU3r0PjntUGidty8SQytiuL7IEUZb8/3j8J/Lhzzad9W4BBmmR8V8WgDvPgVfDxLE3YTVymdpNFMhURw4g6WTGNdDVdkYpkqlF3TTU3RiFxLAKyiDQYnKEACmUquBortgft3LIg7xFL1yHQdTx6Abxwcp7p3hdKIkyvY+DtiEBe7+fftkEs/nKM3zmFmVN3TpERm3CVfCTWVQ111QfD5NqDMxl9MIrmapbi0HGHZ/gkSE0bNpkaoFJDlLqEQgM2vsU5RaU1a9Ba/dpPPKt0XgepxDYo63e7dtmcmcduYmLGphVElsjnWQpWfFkz46rCYsJXomW9K70hb4pc0o+ygmk/4nRtvOUq0bRgL7LPNkoy86yzC/TEF6hwAMniIidDkPSQsPoB9adXUdRlMkLNcge9dKwNxNmgxmYzCuq3QZKA+MERjBJZ0kzNHbvE2zF6ZqOatt8ZCUJaiWtGEWbrsoxeqkjDpDfY02nnc0s4UEQs4wfuiJOme2jpuvN7nlUSTz1mK2WFG4YDNs4aZOxapx9FAm2nB7xQgwzv9PG3ALoScbU/MKX9yyb8u0tkAH8NZ1EmtcMPFyOV/q7YziLE3sHR4XF7601bJtpb5JTyMnNDFYhaQbiKoEVoD+kEr3aSSQlhEmXyP0H3tThp/1i2lZX4GijAJ5+66ShGdH45RKpgXVidIb3hfASGMoP3eO2KvmIFgS9J7Rndqk2ZAd3EWpSpplDflOY50hdhsrd9HN17aZXdiBgPX5DTMN9flXUvkqltC9VMTkdktPpHdmWzuCt8dv1iSl90Z8LOMR4Bsjo+twuy8aqOWbV1ebB8Tbo+F8rFMOx15UePW9IJyQCW+8aGaSWzUHpWC6xpUXWwQzjqwO71CumGkYtNmKI2eCaSsl6XgBczwfMZ6iAAKaOjKD4e9vWpCnMec9OL+7uvkYkIDwiaM2OyNAEHLrriiEFdwYFr2PPjMB3VmNphTfgqbw+nyFSSSrUu2iBByOXx3dHeUvDrIVQUtyBfMjhVdNG/6kfQvQ6txGFf7iXm+HYrh5DFjAyukiP0mBZcqrDsXbf++5ddxbro4ai/ry1t2Qh1YvyyYb2FO0Do4nQFh66et/kyWuY3SWssgYCkMnoZB/5NN7oHpmN9CKF8XPwJnff1l1OHUasdyumRVn4CDOgBCcAc61/X2GlqP1TTebJnl1TVH7r1QRxRIhN65V8k8AroB2QJC6xWfcgxOLOgKH3YT3fYZ0olwGMiNrm5nDUhX2cXpAVFJ/JEjjotw1d2r0Lnh0T8ydnk9fGEttiXKe4SUq+Wi7Yv9bNZM55eMgy9wGlrIXMbW8Y/xpIgbkB2WJ/lJWP7xmAv3eoz/bZsv5ii6uS6JZNi55xk3Fj+bOXz5aL8hUL6kvOHfIdsdxv0KXI9bhxLbupfTJ8oujy6KJP0+mMmqigR6uw50RZJe044zcKM6cjXrPzF5eVlpTBBQ7mTkoMyzPu21wcmjqr9ArL+ppCqF0pzWdqXWZUnwiyIYixOuKBQOFGbaZtSqlqGHb8TAwN0clMx15maVDWXPJQTK2v+9QnSqoonBPPjEqhO93rVU7W8msU26nhz0aK3Yf9bSawY6353XcljG73OMuGe3+4e7J59d7i/dXB2tMzeLt801u1oQytTmHx7hDpmmUn0j8jGi1OYfmTqUayER6AY45vcLKvo21uSY7S5nEsy6uQFuG5mHQFbomGvjeX1XDQVQjwKYkzC4qNuXXs8PVu6bjrQB3nglVVYtw3RyHlnFieNLErMae3/c6XlzFmaHI/HZkFs0OxLABTLeEB0aUFyVefiiIJ0q49K3jstMuzDP/zPeSxOmFdhvtRH57a0mONDk4vCvEqZcSF7P8f5l+pTnVpUueXk8Rp6/AZ2+qOfgSibKYkyfumNK9mSgLyF7NUbFRkp4tNmJW1OaQRnFSD2JBCjaBiLgVkFFdcpdpBLY7pg38o7j0MfxTKywqSFHoLBe3bmA869ffKDkP6Hdtj1x6QQznMQl3GwT+JLvwtoQP++zQq5GXLmFlTJbY6lF2OdPkxYvTcVgnZWrGQg6A9O2sC8wzsMEcOWuptjdWazZyHj9T7OuSZkD3pFKSxi1ksokmHKs7IzppbIZ/5x4iEpQU3JnpFds8ovscX1LP1/tuRrpgvVApGJzIBRybmd7wdXRrhWiudOzwcRprA8ZhnRFR6aeSQLzMp5KrJ5R3B5nnx7eNymJbp/+OZ1ri+U7ccM1Ap9zpV8H6YtM0qjg8oW/Kt0ZKPj1+koW0KdjiUhWMlcsiU57LPlNMrxnNmSdt6ilnCiAzkcsDCsklhXHpROZ9QSjuYvD8rEEhaCUkGILRu9HxuymA1LqGY8N5h3tSPgYx67lF8F/QBp/VXYI/dhr+IrqeKMS6Nxq5PcVbKwyLO6ngw7wDgSDzAzoHw9UBx2DwSucnwr1TTqVNhS4Xo8OcEThbJD4X3opUe6mTd2lF/6IWUCpQCbHbw084SozpUGpD5hcuLgdJE8o4Nsjlt9qZupickIG/XVWjHu7J3+v/2n3/33gmcE96yF8qAR3iTn3RoDNBGZ7dEWuXPJnvX16ECvUe+PrfMpOx185C0wuXsYFvI0a1+ckDs/lF+QMG/a6k90l8HyDEFkvhjM5bwewLr+ZiWLr69nYFilfMreVZ7hpe+ksY3t/JU2PZzWcqVGQQJD5pKVH/VDQUk4CN4ot4EKKdbf4puCkrdA39e6ZSx5QG8KisJC1+n7Klu9Hm8kBQWlFw6VrbBHfbbYqbneiUIjTotzx/Fy5/DEm1yiOB004N1UdYIorQLFl89lprg84yjNXm2ncCtwLSpIs/9ooi1Kqf8sJP0Wer1uVvAIUeGIqU3bsUyHIcvMXk7ggxfUU87vRRUNZrT2LJfGW8x7laU6dlO032//rjjzxKwhtKOJFUmdHUI7yg9AzB5C7r4mkxI+N5XStD19JjODesi8rj0wU5HGNmpW7eu2Vkya15waYWGalgABvbwq5E6UMGbtm1pzpVFbXl2vNeor1fNsOOgM8pApKNfNzTDrc90Mw+MrvgxmOTHXv+Tue6HDyN4Pu3h0bzaSmTNftqY/40n3wXebFNw18rBExbwlZy/KcdGmD4bTb2Vxs0Fox5PNAp9Vm/kUhjsV5VFU3zbHuci7zEyPp7v55ddsQTS7KNgo8v1ET55sX0tVf+6afDrdejbPhVKFF0E5FpU5stuUWH1Kzib5rI3b6LcjPvzH/zWzoPKb5fyXpFhyMzpy/SmYR/XtHtIN3rnG41EsIaeC118UQ1wpu1On5OotpxeSvzatq7eaTy36Wp4qzbejwba7oxSyZpsNo0c9nM2eJqIX9pEmyllxLjSg5EYWOOxRoOgUZBuLUKM29QBj3RM340STReugQKaJzdVGGaNFcyXHQPGm5FfsZ5hG5KnYEmMaWKnitqTZ3L1KgPf1QqNGrhMH/k0gkmEckCfOM4yVfp7xu322RG/xiEvMAIoP8PycyOK2vy+FgagK6ETDRTIZx1WBMJFqTu3KVqqrLrr35ZnD3Q2D+O5oT7yOYfrwHl3jOZfSFZjAApWyXmpfitazbQPIXM1Tvq75Pp7pd/Cs1vC2HXvrzx58i2Sk7wYhEm2OxdMRZLPyNuz3ceEcxdGFfxH2wnRUyZWVgvLlbXrUSbXB4T7sH+NlwQVb6/Bic2yZO6BkMnmLrveWZQOdbmCj3c+8dkwj9FBwjTyR7+Y40xO0kawV3H+Wrb/0CCRBJ/fRSH6MzKkUQ9kxi1Z+wOdl+DoixyRY150U16apyB5L/J7OCnvMB0eUrPNv/ndUehWVtXJdTv7lH8rRyFzgo7F0HCY34ggENZq9eakohlpHeAcD1CkhpcrW3RVj/1/+SeBvmI/KLJqw4eKtXesuYZQVaza4HF/K+KkI6MB/L3Zi/x61tdMQY9vzbv33qsocS8wqveOPgPK6eNccNB7dC+AeN9Nmf64RkkM0DLNsiDgvkoJxQaspy48W7yxB5qcuJjAVgbURKloigwr/7gqmvForOp3yhBYkMCeVarYh6lJNZNrRroBWg/vkE1hzNmiRb+fhiEJmAvI+zAkhiuian2fj6mWQpET9NaHHYBHBBXyGrwxNmQhAAnsMtt5GGK8o8VXcFH7MtfXJ8XWw9UubsPBxPsIiZDEvys24tWwYtk1cj8aWamwGdUGjj6auGYxGehDMs0E9yrOgjBG9Jg+yJ7mqV/heYnhC87dYXIYQMpm91Wd7/bCtPq+VydwGKMQXObecIpccR4z7RFYO223Q3GjvZITWB7o5JL/yy+FX1nM3Wy/XjG2sUV/lC+5/9z/m8DUdcHO9wFjntrQub7Z35egQRWwQ6QdBHEbdh7a6UuZv1Y5HwsdzK2zlxvEFhorh1UHnhv1eMpHX9cLmC15Wy/W6lucfLHzxJ+KQPVmYR6tcmgc0QzKr89h+NUmvTrHqzeUJqy4YAk6yKNOuVFyj7Wx7FmclIk5/DuT9PwkZPul9Oc479U+qX+Zd9F6Ic9T2flnidnbOHPS8wPyElyndhcE9Nl+Bxv9G7HIsyfYwvis0WF2omAk0rOFqFDqKotRm1mN7HRb/H9hm1is0xSW9cID+rbI3f/f/iBP15rTmxFSe7O8dnf2wt/tWJltaUYGUn8VyptNK2cYz9kn/BJYzsyxt3Is/4jK0ulSKaEFpOYA59RKMQERlC6btIcUEXfVMa4SumE5QuZlpwXV5NzNPyTPVrB9RjOHSc5eNZapqEs5viWX6Dslo4uByc2ynMZp8xt2zuT7HbviR/sslG3GzUZI/vdwKs5rbMhGETZ/lmfTnch5o5PTzs32TnW4/P0E/7Rbeutco70uZtXZOwa3QL7vknoJdJJXZcmy5GFvc/VlDK+tOZtxmGsI+6q0XL3pRB/0elP68YenPMyrm/MUZeNFLNvNwLZ/lNn9S0C5xUisg/n3UD5IHjrxMEit5zVTLTJV9YBXuA/NlwogAfqDznU0kQjbHy6uNIj+AAuErJ7h/fql8HaXyRplIPsMwtlJI0DlJOI0E4eUTiZ9aYhB/CkJndkMxEs6Dd5SH6tCbl7GQ/18STaLBa5zxpsjZwys7IP1Fl+yxJELcBgx121GxE45Jbctb5K3I1gm+B/4jX8KvwmVb0v4eBtgOKWltrn03+HbyWdrf3hUofQi8vSvbvhPhO/mo8f//a6nL+ItPtNilvC/+dE6Z2QWvziiz/NU++YQW28bKFAqrM9UYLGMqhB/9bL9wS7TMgM9kWTIuunujRIpcDwXEnu3pLPIroVpD3atM3VIVg70p7H5GUJrlzGYpPZjAcdr/5EhQKgEKKPBYZeetH8XRHSmNpNZU5+2dPKeYQ/HRd3lidcqbso9nO4kvlbEFbc9+7x4NOJhMsC9zB+ahuGRRoGh7kA4t5/iVCebLNADL62PdSsud+L42MvLXcznxyS4We/E162uWH1+tTEQvlaOzMzW3QIwVuQrvpRQdrbZGHSo9sSOcN8fWw0TGRG+O+W/5hllAXzIdzt62UFlZPMoApLJho3tDtYg2i9JWlAWnTifCkqP4Y+kQI+quos9EiI8hxZxnvurhQylMqNmiEBD4O4g49UBCkaxFMzJxuZDJffb6+PD7o92dXOKzojttS0l3pnJhViRFKZYzgyG9YadQXah7wmXp5me+tJ0jBPe6E5mhAF7NsVSKtISPOem6+15muWV4ucdZFaozufenEnlQSXfZi+5hSq7DbjfoV+bYCIrjatxUJKVd+DSqtE+kOHNnAddOPjPPlLspy5srVZyRClsw+at7SjDLcRSn5VfmFhJbQVdlFtGilfGMxtjGkGF5oti0ARTSf7VQuiynhtVZdshHnYQKT0OuCXD+FZpbzQXl+NWzpbyEV+pNrN0GizJzzJb4cKNd5JyO+iYKviYGd90NkOje4w4MaxU+2cxhbC6CgN1W3zGhEs+5WzCTgCweD/t73U2Selo63LTC6IUPYw1rIvp8SYR6J++MmADNDuNOsC8dejQMC0eUOvH7uLc5lvmBcolX/UG4xGDZF/RF0gGBZlP36GfIwb8cB3304/z+eE/fPoX2nfNsxDtQiGUd0rdhcEpmi8TNND0jMn4+NkkQ/g20eBnTLT5GJByLxle1ZqPx1VjZvZsTsWY9NuqrEysRuwWE3JLlbMFKH6c6NiKO6JqKlfVGN7iqmurnyHGoW/krxP4/H04OVA=="}]''')
BUILD_COPIES = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TESTS = []   # frontend-only: parse gate below; UI verified in Chromium before release


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
plan, done, skipped = [], 0, []
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
    die(f"{rel} differs from GitHub main and from the patched version — local edits? "
        f"(commit/stash them or pull main, then re-run)")
if not plan:
    say(f"✓ already applied ({done} file(s) carry {FENCE}) — nothing to do")
    sys.exit(0)

# ── 2. git status: an uncommitted change on a target is a stop sign ──
if "--allow-dirty" not in args:
    try:
        rels = [os.path.relpath(p, repo) for _, p, _, b in plan if not b]
        st = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=repo,
                            capture_output=True, text=True, timeout=30).stdout
        dirty = [l for l in st.splitlines() if l[:2].strip() and not l.startswith("??")]
        if dirty:
            die("uncommitted changes on target files:\n  " + "\n  ".join(dirty) + "\n  (re-run with --allow-dirty to override)")
    except FileNotFoundError:
        say("  (git not found — skipping the status check)")

# ── 3. decode + compile every payload before writing anything ───────
tmp = tempfile.mkdtemp(prefix="analytics_grouped_")
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
    env = dict(os.environ, PYTHONPATH=backend)
    failed, envmiss = [], []
    for t in TESTS:
        if not os.path.exists(os.path.join(backend, t)):
            continue
        try:
            r = subprocess.run([py, t], cwd=backend, env=env, capture_output=True, text=True, timeout=600)
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

# ── Analytics.jsx must parse (esbuild from the repo's own node_modules)
_esb = os.path.join(repo, "frontend", "node_modules", ".bin", "esbuild")
if "--skip-tests" not in args and os.path.exists(_esb):
    _f = "frontend/src/pages/Analytics.jsx"
    _r = subprocess.run([_esb, os.path.join(repo, _f), "--loader:.jsx=jsx", "--jsx=automatic",
                         "--outfile=" + os.path.join(tmp, "parse.js"), "--log-level=error"],
                        capture_output=True, text=True)
    if _r.returncode != 0:
        say(_r.stderr[-1500:])
        restore()
        die(f"{_f} does not parse — every file restored")
    say(f"  ✓ {_f} parses")
elif "--skip-tests" not in args:
    say("  (frontend/node_modules/.bin/esbuild not found — the build step will catch JSX errors)")

shutil.rmtree(tmp, ignore_errors=True)
say(f"\n✓ {FENCE} applied ({len(written)} file(s)). Next: ./desktop/build-scalp.sh both, then restart the app.")
say("  Undo: python3 apply_analytics_grouped.py --rollback")
