#!/usr/bin/env python3
# =====================================================================
#  apply_closed_meter.py  —  fence CLOSED_METER_20261120   (on CARD_ORDER_20261120, 12.0.9)
#
#  A strategy that was stopped out (or exited) today showed no meter on its
#  Dashboard card, so nothing said how near its next entry was. The backend
#  already answers for those strategies (20 Nov: TMA V2 "entry 24% · warming ·
#  0/4 in place"); the Dashboard never asked. Now:
#    - closed cards are asked too (same single meters call);
#    - an entry reading shows the "Entry readiness" bar, which opens the
#      why-panel like any other card;
#    - a time-based strategy (Time Strangle) shows "Next entry next session 09:16";
#    - an exit reading left over from the trade is ignored (no bar).
#
#   frontend/src/pages/CalmDashboard.jsx           meters for every card
#   frontend/src/components/calm/StrategyCard.jsx  entry bar / next-entry line on closed cards
#   frontend/uitest/test_ui_calm.py                TC-CALM-0010/0011 updated, 0042-0044 NEW
#
#  Run ONLY this script:
#     cd ~/dev/scalp-app && python3 apply_closed_meter.py
#  Options: --repo PATH  --skip-tests  --allow-dirty  --rollback
# =====================================================================
import base64, hashlib, json, os, py_compile, shutil, subprocess, sys, tempfile, zlib

FENCE = "CLOSED_METER_20261120"
ITEMS = json.loads(r'''[{"path": "frontend/src/pages/CalmDashboard.jsx", "orig": "00e9d5d2630a726104a24f7ed57c3b3094fd33256f03b810477102660a8872e4", "new": "f6c9dd3e0c5fba940048dc5745029798c47fb635c0e2d11eba032595a14b8eae", "blob": "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"}, {"path": "frontend/src/components/calm/StrategyCard.jsx", "orig": "8565fdb0abf02d5e4d4e3cfc83f860b4be5b774ca24a48c11ce55ed99b8d4fa0", "new": "144af192398d6ccc351fb0c8b7cdb1ef04058ef10bf38c554c84388e40ab578e", "blob": "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"}, {"path": "frontend/uitest/test_ui_calm.py", "orig": "64eec5134a9782aa2bfbc8be41042a4fb5bfd7573071ed5af19557c19e65933c", "new": "18d868aeb54811414d40812df7255338ad5c5aaeeca1d725f8ede3116f794ef5", "blob": "eNq9PNtu20iW7/6K6lrMiOpItG52HGXVA8dRTxvtOIHt7pmFRhAosSRxTJEKSVn2qgUM9mGxz7MD7Ms+7y8M9nU+JV+y51K8SaItJ511N2KSVaw691sdWkp5EKkwGiycwchyZ+b8QXz6y9/E29PrHwZnpxfvBo1a47her79si2iqRKBsFToTT9nirRVOh74V2MJwrQd/EQk5820VeLIsHE9YMNdyD4aBvwxVIHyP3n/z0/nFjRgHvhcpzzbF9Uh5KsQHM4H7D8bOfbQIlDBsWD65w0Xa4sK5U+KF+GDNYUErOqg327Wj1/Ceh/DMVKSCELfGjQ6tuXMYqDmAdqhHwim8WDbFDeFhufEbAcCuxNSCd6NQ+EvvYGiNbgE8ES4cGDH0LSw5p2WJYAQfgUzvh0C5snlwIOBnZCcYit/+Vpydd8aWGyrhzWciWHhiuHBcm2Z++JebH95ffji9+aFjmofxtvOHaOp7TYG7h9HhJn+E6J39cPX+XbdzOLei6WHkH46mQEDVP5DATmc294NI/Dn0vfjaD+Or8CG5XASu6wzNuRWE6uAABkxczXQ8YFdk1CrwFj+xncCzZsqI761hiL+NAbDHVYNBuVxO1nQG8PYdsicUP4ln//yT8PyPVlt0W7XGAQlFTgz0Lt9fdLs3X7BmTtD0mu+6N92r64qYo3ANfM99gOtADfy58ipi5twPQhTVRxc/+MkMIwuIV+Z9UPiWgTOZRmb44I0GIDsi4QPcp+N7wX7AXBcd5Izy7hyQMXOiIkPyAOidHwh5uAiDw6HjHU58f+KqKsuGPHhzeo3vymkUzduHh/XGS7MG/9XbrZfNRv1QHlwDUTviJxPEHLgYRY43CRFmEwZuzi9/fz34/vyiezC2HDesiMuKUAFoT0f0+hXRq+E//YODA1uNxWiqRrdGNKqIke/ZQL5wUm6TwF/CRPGiI+p0Nw8cLzIMKcSn//5PIYUzpheEQm2hp/8lAKsXYixX0WgtxApWWssyvQyTPT+iF3ht/CHgEAPQIwCgrAFCLpLGGsOKGLtKRR0SoopYOnY07dRbLRD4qUJewE0Nbix75nidm2ChKoLtWye2bxW0MDPVkbYV3MLdyPVHtx2JlrJar1frL2/IMrVrtRfwb7MmNfJAR1Q35UXm7Ba0yuCbUO+i7h1QdP+WbsvJGyAhkRpE6j4yUKdNezGbh8ZK2rH9HTB4sq3hrAhJ8Mk2w7lGUmkTTZRdbYyXebNRdA/cHJqeWg5GaL5gxztHLVFgOytJlIJ36DfswdSCB3yxBqo4M/WvvqcGjt2Rp6FjHf7ou7dWZGmOzSewPuxCO8ytiTLi5+YAZMkPQKzwWttlLVn9eI7vGRLfoqkSuTIb2pZQ7cwCMevDKDBUuQfEr/XL5ewKgfq4AIOavh+0M5vG7wcmmMcyEk5ueRKJPoYmMDkvAeVkC5IFE9QHDYyyB0gTgx4mUyzbHjieE4FFCZw5qC+MWu515AeAHCygrMAov05oZpJ/MeS33xIkJL1g823rIYdDYI4XLhhkl6SkQ9M05q4zAmLazigyfjIvzs+6l9fdigBTFS3Cjvz59OL8LSx1Z7nANxZFsOOuulNuR5IakGbSlVbNBVh5iUsEVqQmjgo7Pfmt7O8AGfxKpGaHAIICx/IYxDAF4KUVUGWZHwatVE4V/CNgkvVc5sL7uPBhM5poau4ic8xw7jpA3QyQstyr1vvxwG/htqZhxh9eIQfTSvq3IOFMFMkgwf0KghF2GL2wL8ZgdSnu+BivXJEkOvSQ54F2SJZQfH29LmBuLmD5FqjFV4/PHjue/W0BZTWTW7VWRWiMbBWBkQQwpOcLoI2t5DpVkYkf+Qa6CjCNFsjowoscEAPbn5FJ8CLXhzdsLZ2BAh/qoU6j5sbWH2xSaMxjk6/nwNoKRGxhIQJS9kzTtP3RYobWEHgWPFwrV41ACU4B7FLPBrNRxcgHZBL9dRVX7Ve2nzu2q/qlct+cWXMD5Pw7Yaw0Sx27LUYmvgH6aGo5AL+Or2RHeDnR6XREKVmzBPYMLCDOcyC+DG7gxiSKj5Rx+Kc/hS8OJxVREqVyRW+3BKHA+AiYBBZkhK75DV6CHz1zHcDzCvAzyiYZ0LLmbVt8880oT4Bd2NPcfrrXaBEgZLDFaRQFzhDFomQFjlWFEXQqpTLjE4HoAipDy54AzkbBVjQMy4tffgHpLJuI+RnzGx+VSgJEBLiG/nTpRNPN+MUoY8A3Z4YPQUXnJoUdzmJmutbCG00Nda9Gi8gaQsSI0WOHYxbws8EErUe16vnV0PLsoX+PhuSAw59Pf/sL/K+9Vxi7qQq7kpxX1z46Z4xJgkE9IZzR+Mpt0ga+H/Uley9c4bgGP9ojUhwjb86qmApV4XldkotCi/3ocmVzBKxHugAT6sLCqL7oNcSgOvT923DjvVrlIA0FUVu1Ew+tO8h3Pv37Xx9Jyd5RqFKWiWtHzxTGHvdxGsqRa4WhM5J7EzKLwp6UbHwOJSEuk5oIZwxjQgYI1u8cfxEKxlxgpACpG8AEadxsZgUPEEeS/O0iSl7ccokmONoKeKTwNvLnxcR7nsyRMdtNqewSetioH8WjUxX4qF6PEg4n9WUR4ZuyQsug8RhYifWQbAiAXpJILS/Of+7KWHLJBHIQWrBhdexMIJ/q67dffPq3/220KsevajIjxLgaDb2qvKzXKJDab3EfWBzA2jmNIDaRB2NtoHsnJHEYOhPhLWZDFVQ0H4egJTaMR5oyIRq3zp7742TljVQxWVtAVjlUrr8UFJyVQjEF00g40laEu6coObYR5MgHDkx1PJlOAerUjyr1ZiN5nkV6LGNQIF8AAwzpOuHLuILvw+Fe+1Wtv/70l/8py0xg6iDEjB/Qw/HtzlKp2xij5S0Qw3gmqyvPJ9+jABHliml8BDRe3mIKGYtYq15pHNVYTCUkUBHQNXSQOvU6BOZ3REN4pQ6ZxFjeTEE6EGk0BCQyeAOc8hQTHwtAJFCCjA6Qc3m7jonoOliq6uQjmefHMVMriPoivJsI9IMYubjKm0BqxbtA9uEO3Sd1nJcB65ihfhHZjjE4ZOA7osG0ugQ5pSyVKKQ3pZHaq3Y9+xQJR7ttFOBoRfDfkLUKeocImqwLtKMZa56YGF2kYBIhciJvU6436knHlhxOj3B7mBrn+DjkKtnv5+302enV28H7q7fdK12mbNTiIapAMivHThBGlLN7guy9rW/QzkJw9loMHwRWuOAlMhHKGk3FBMK4eQFBX8oKQw1gy/Ozwc/ozOTN9e8HP9fp6t2pfvb+6o1+9nP3Bq/6ROQiZFNEee03Vz/q17vvzuj1vC2AYIeIKZAhcYArDNg/z6sQ+KcEhkbtDGUeoUklQ5MMPYCpgHeiEdHMAs55KHujnahI1lRNjyL5PEEEs/CS9MHiPYlyrWkmGZVoCWOo+seVlnYhWzN5uNHaGs65D6QSEk/LNRs1dByxx6CtIICHy8RrDCFPECuNXBuxpNg5z8p1AaKvANGh3UtlAYkDif98DmYLSzgEPs7QcsMz3gLj0iFNTB7qgskDBlhBOs7yyMPnntaBf/xdtISrJqHccCaQHyrODkJg7tBOeFuYaKfFEjDEhUk2JcCZYg7XDeSG/l68v+6+HVCKvKXBLJZsKwSKrxXektf0hQGi6gScQAGYU7A5SAN2hMBosFwRxHtOWCBxIDdgEZWXwSUTqENKaGRTeRpaZfQ80e5U47f0PFVZuNJqvM7THkOBB8IPPBDjYnC5OdZIrYx0nvL+souFaxftah7yNT1vi9XHhHnbGCMcq2Krw4Wt/noPVB9Hi1cCiaIjnxRF4I0V0SELHQxhUkxsjNnso3uaQmIDBt9TfIoDjoWK7jFWyxowm7R5Kfs7w4jYBHYY8L4oyqT3SG32WGs5fchG8UdJEA8DRaHlvsvqheq7DGzsPovsbEqgbUEgbnb/eH4jLrunVxwfAbRkN7vvTuvN/KOXtd/kHgBA3yEjvhV182QjssfyUT6neC5XADQqYkBoCwtimYnwwiLGhuGKrDmpO4tbnASSpC3Bh3tozZa1NQyslvX1/L7Mqg2mguNl27E43sHzA9Ay3wtlcQKWcnZshZg1bKjgry6M8catwgS63nwygWY5yuXPhDLh8F1HNLdJak0sR0cEeBz6GvjuuIoS3ZTcIWjwOFDhVOt3SyC1cVU2RWFR2pFShKxJX5hJNlydwcaPEOIkIcQi2B0tbigFzIvjxTHE5+Egut8r3aPJhSlIHdM8AgHDM20UddRxA6CKa0DRm0AIhzClG2v1SPOGXM0ftOY8UjOjFMLDeVKp/MEPI5PWOLdLZa0LsSHeZh1LP6oCxARgO2dWgA4zzmtAo5RLnAwF56nGCjBZFzFr7LjA6w6WAR6T0GbMGBeDqM3YvmDRD6cfuld7rToHzPZc9PTiop8GpvMdhhMXe1ZoWseMc1vWENX+ZvQfh2WZCDQToM7zESqXLtJQNhOrJjM3rCsmQpb3YCQ5Qg6vfGWEKcJxbVIZ4UgKj7Zf6wAXhzAKZrOIQsLPORSu8GsWG0wENibTfoUTsjvFmoRZqTwW/licxEUcPtUibY6rIc1MvhKPUEZK+TmmlijGXpSI8UT5mzm6sQqXbZFk6fFF11V0H44C33X/wMead0vMkAAtun8koMIcEPbqyRB8rfjnDt/cwQ2ChwfKYn7fBq4JzCmX1gMGM7iRWMHM+BCbjtBfdDInmE+UJolBVWQiFiYfK+byMXfaz2CUv26NsrjWWz9hV7UzONguXIJYbNYiyV58WTGSK47bEcuG/2TN2ek2ny66JmXK3Mvbe36d8hFlMDkzwNqMzG9rzd5RHcVwG/WogrJK9XKmwedK6FCBWCjahrMZMFxTDPdZyfcQWd12Y5R3tVbUTtpHzUxrxbNkmg4Ld8v0dN9acLFFw/Qey7dsyqdxoY0RD8WDitjCmaKZOTrn3DaYKdvMvkfeO9L+Gh43GjMeflKEtxHOyWNTl/nQ8ja3Le/eZj0naRtMbwskQ4XR4loq3MT5bFqy4vE43vlciYOMsUrdCY/JFvfzfI+NeF/VDjaOCu1gA8sO6MYzdZGnWRjnCI+dDGr8yd1wRkRxeCW9h4Q85ISb2x3BENAr5ErKn0v5+RSsx2NU5/aq5qu0u+qk1fp/Oiv7OpHAZvC5ZR5Ej1JYsGJhqGxu/dwr1B27zpwB/ixH+dULIvtWPpabVN/t4HZ5N1y2VC7snKBegyLz26hvRmQgdpzvEmHzYQTBmZlTqIMk4k8r4VjSxDY4TwijcdX5vQ6gf4mL8EsnGk0Jmm0vzFoaW3t9UhmSpSQtDhV1RsbKjUf8FEdWxAoXXH+2ClMLIE9yvFs8iVmtddaMBVV0CUbc6ChdYgA2GKoAjIfl4vWt71ozLt2MMEqn/kPLtTA+eZCZ3q0CG8HNlNE07cT6FUxDjE8vmvY35RHE68yfzUG37OvowVXG/gKaP80slVEgXD+OlR5hwM4ujC0Rbui6NJagEXoTYVYhhO5lLtgkZWMimggUnk6wF6EkDYvfY7BhcZmLpxkrXCyRkfz52Q/d07dJl784FN3Lt4OL0zfdi+v0qfG2+3211oL9+aJRbgt4fOnfiRkEaNXFnCu1ieAsSW64rbZeP6k9LQUc+yWN1hj8sfNY/upysdNtNLIzpsqyt456P7dpLdectsrYDNDoMBJ47r5HBxgVybjFhvu0Ct6xRkC+CK0odxfHvm00dVwbHvQa/dd659kzdp5FM1jzdQZ63de3KmyzI2Db4jbbTYbFswifZVyt+A7uR2Tn+f4FpDFf3LGWNc7Jj3+nqG8uv/toe3dAty1muVXXr8Wae+AS/m3qb7NFnbJLysSwAsCnXpkJx3ITLlR3EjfyKa1cqWeKZUysGYFGwTVCr8s+ZJf1a/gGRHs4O3sWmZmysSW9wMsndTCuY9EQroOsk33+nAH91tTgZBKyzI21N/EZS/JOOEhf6YjVck2VEPJ4mF661lC5GIByMwxgKIwSu8h//B19W+AouwQe2ndVOTFtmZNKfBfE03Xjr4q0P+wBRiXHLvUh2OyVCAd9DXuU+pug9xODqD8nSPjWzuEE8G6bAgNjAFBnrUpYkzai3CMshn52ej+3PDQb2Npr3OOq9zlZxNbOCL8t2nJl96D3fkgHGtwAag1D34VxUN5YZZX4nVBFQZaAJGLhuiDsrzc4y2jhiT9oIUY2JUzQ+ZbYl7cPmqjGNxYq5DejcmoxZk4YwrYgE9RYbWETrAVrUTNs0cZTB6uO3xgWh4EYvI1MV40jNobJQyt5aJlDP4r8GU+N/DnPTB9a+HAL6ARM2JGA69G8SrJen0DtjfhpvGCfLYSRtxG77cSRTgRBtPgLHk0RWU70H4Z6EiDINy2kWsa1WZjMmoPfjZFihZDe0AKRv4Bok3o3qCwFCqW8dDYEDUMwKByiGit4N6cOzwhjknDiD+dvu4Prs6tu93IzdDh6CRGDj5Xc+nHV8QCmd9boDVqDD4EPgcLLxgkFzKCgjdrRMX5KGChUg5ECmlB/J/ZzYk87BzYUG+jtKcb3F0G2qDKzbhU/VB8Xlgsy5C5mEFtT9zLE02o2jx5gvTHc8lhsTEiQaOV33dPrn67oo6kthedi+udo+CN97DvihcI28kRsGR7ID/Abj70D2ZRUjyRc+S1QrWKUPbUU1yC4RAaCNkBoM/3vAakhBK7gRfwgMgxQ8CGR0BJVuIrXTrRtiZ8Weba/NNOsu4J4XbTxX1qO7q/4nvgEatRmXugyKH5y5kLUQcDGzzYViDrCLnS7PuQxBmC0gUkMPjZFh9FVPNe63zmXYClv+UKW0jCGcDeZ+HOAcsZ6FGpVI1ark1itXoFaWaLZYJ2a+Z4D/BZG4+gYE9ByrDC+/xqmtX4UNz+jS67fC6N50uIpDmaZ1IEEI00Ig+FpGsdXcBAiaTpnMkhRK+Il9mFnzNlL7AQ1UHG3x054DADaHntFYwhJRTSP84Otmix/SdaQfs5XL4r9v2oKMduIGbQxSSf41HA760mWRi5ZjPAgF/N8auEARYELEsB0GOLT43gOyjoHcK34UTwb0kW4Q3WhdZo7YkCUZtyIhFRCfATbgTLkHsGmjc2IFz/s9G+3Az+O9PCYS2EJWH8wtGm0hYHVjVCsZr0So17qt83aeA25Jz5blhjvEmESj4GkVgDJnDGvpIZ8hwkHpzb7Ype2o8O0LT4cXujYdQYeNNQn76Y4jZuZkk5M8ja61DMB6aJXIZJ9gJfGkQr0ZpzSU5OFjwcx1KqyBKWkYxNdU6cWqRKt4CoL9+UuOD+w+XtriI+WVmhyKYc/Mx7584dsbRHvTVupOV4Y9Amsbmt0ULVWcR9bW7zCkl7aztYW1ZMTfqIbEODJUYOfcO7QFvVaeg7eFk3UmLSUFFBDBIDRyzQLyn6q4UFPTgI/JHnGG09FdAmg9QLOVPpPHx7hDl+1vpw0ojh22Hi6E2VH+/JWONiqcz9x47MaivNVSI5LboHFWjzog7VQB38gO5BheCCK7VSm2B2iEHo8zco05ZGIg6nww4ibfxvPKTU+3e2J7ivkDtgqftZDqRz3IyRtnVgIvez+8SZp60RnhvyoDq0YzBB1CqF/nQSF+EF1lDQbUlKAGTT/sYnMgRUBq/fUZ5UstUV/K2Cw1OEhfQGa6eaFEUPqQy5k0R8sBJ0LqSrtNAN/zAoJvz/9x1+Jm4QaXDRacLO0sLo6kTtrGPqnp79NpRMtqsje48fYMt0lXppqiPy951LU5BrSF1k7bNFhHwoHZt1IZ7RPRzWzKeZR3AsXo5i2I4v8J7Exsm14zt+a4kbIG1vSN+KWjWfL+PAmYRiOOJGa4dxevxhJ3ZVKa4PLyK0cjoB98OSSDKakr5XT2yUYRZyOM0EAQt8j/NPWYLoMFSRdQCj8euFYrtfrL/m8KzlpYXF4tMlxh6m5U9GTHaNPrvzEGdD+738JloVHQ3f7dMU+sW5B4bzFXa3EWlZ4YC0dZ9/pL3tko/Wb/INYyXIPN7SHxmDj5za6aiySEzoqXulmFDaDW334O00gmg+Vx4oA4HMiCqmmD1Xu7DNWgMU3aT+fF/c7bpxrJUCyUu8iNWpHlfYtpji2n8IW29+54sPsR0mE9OWjqvdkS8MeMO9xQqe9WsZzMKlj14ERFB7tBCEByQ0xFn5Ut4UTevYM8snfgXiucyzwIVPftbUPiT/hICcBDg1+n2Cc5cxAegFabFBEi673tUESNq32r2LWKCp5rlV7vJN3S6ZaO1uan2G/Huu7yn9oU9TpsqXC+T4XK/tZinAmHnih8KlYI/2q0GCJqujydSkDUumZkpNldCw9BaeKTV1txJXxBBGAoGoarw7ijQO9diMtiA/Tw2L+Gz1j+SdvVfr+9PyihLJPf2aHpb70/sfSGlw0/WWfKh1n0Gh5fUjP1gwQWOMXApahvy62KlVEyfyz7yRz5caqEj8Uxb9JhXQ16hujIED/BwKE364="}]''')
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

# ── prerequisite: CARD_ORDER_20261120 (12.0.9, the Dashboard this patches) ─
if "--rollback" not in args:
    _pf = os.path.join(repo, "frontend", "src", "pages", "CalmDashboard.jsx")
    if not os.path.exists(_pf) or "CARD_ORDER_20261120" not in open(_pf, encoding="utf-8").read():
        die("frontend/src/pages/CalmDashboard.jsx lacks CARD_ORDER_20261120 (12.0.9) — pull the latest main first")


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
tmp = tempfile.mkdtemp(prefix="closed_meter_")
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
say("  UI check, APP CLOSED: cd frontend && CI=false npm run build && PYTHONPATH=../backend python3 uitest/test_ui_calm.py")
say("  Undo: python3 apply_closed_meter.py --rollback")
