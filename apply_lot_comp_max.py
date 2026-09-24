#!/usr/bin/env python3
# apply_lot_comp_max.py — fence LOT_COMP_MAX_20260924 (requires LOT_COMP_20260924)
#
# Lot-compounding follow-ups, all backtest-only:
#   1. Max lots — `lot_comp_max_lots` caps the ladder: lots(day) =
#      min(base + tier × add, max). A cap below the base is ignored (the base
#      is what the form says the strategy trades). Peak/margin/chips honour it.
#   2. ₹-knob toggle — `lot_comp_scale_rs` (default true = today's behaviour).
#      false keeps every rupee knob at its configured value while lots grow:
#      "same ₹ risk budget, more lots". Routed through the shared module, so
#      no runner needs editing except SCALP_V5 (its run-cumulative cap is
#      booked in base-lot units only when the knobs scale).
#   3. The four compounding fields move out of the Run-parameters grid into
#      their own full-width "Lot compounding" sub-section at the bottom of
#      the form, with a live ladder preview (base → peak, steps, cap) built
#      from the strategy's own config and the date range.
#
# Run ONLY this script:
#   cd /Users/anbu/dev/scalp-app && python3 apply_lot_comp_max.py [--allow-dirty] [--skip-tests]
from __future__ import annotations

import base64
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

FENCE = "LOT_COMP_MAX_20260924"
PARENT = "LOT_COMP_20260924"
ROOT = os.path.dirname(os.path.abspath(__file__))
ALLOW_DIRTY = "--allow-dirty" in sys.argv
SKIP_TESTS = "--skip-tests" in sys.argv

BACKEND = "backend/app/backtest"
DESKTOP_BACKEND = "desktop/src-tauri/backend/app/backtest"
FRONTEND = "frontend/src/pages"
DESKTOP_FRONTEND = "desktop/src-tauri/frontend/src/pages"

PREREQ = {
    f"{BACKEND}/engine/lot_compounding.py": PARENT,
    f"{BACKEND}/scalpv5/backtest_scalpv5_runner.py": PARENT,
    f"{FRONTEND}/Backtest.jsx": PARENT,
    f"{FRONTEND}/backtest/lotCompounding.js": PARENT,
    f"{FRONTEND}/backtest/SweepBuilder.jsx": PARENT,
}

PAYLOADS_B64 = {
    f"{BACKEND}/engine/test_lot_comp_max.py": "IyBiYWNrZW5kL2FwcC9iYWNrdGVzdC9lbmdpbmUvdGVzdF9sb3RfY29tcF9tYXgucHkKIwojIOKUgOKUgCBMT1RfQ09NUF9NQVhfMjAyNjA5MjQg4pSA4pSAIHN0YW5kYWxvbmU6CiMgICBjZCBiYWNrZW5kICYmIHB5dGhvbjMgYXBwL2JhY2t0ZXN0L2VuZ2luZS90ZXN0X2xvdF9jb21wX21heC5weQojIFVuaXQgY2hlY2tzIG9uIHRoZSBjYXAgKyDigrkta25vYiBzd2l0Y2gsIHRoZW4gdHdvIHJ1bm5lciBjaGVja3Mgb24gdGhlCiMgc2hhcmVkIHN5bnRoZXRpYyBjb3JwdXMgKFNURkMgbGFkZGVyIHN0b3BzIGF0IHRoZSBjYXA7IFRTRyB3aXRoIGZpeGVkIOKCuQojIGtub2JzIGtlZXBzIGl0cyBNVE0gU0wgd2hpbGUgbG90cyBkb3VibGU7IFY1IGZpeGVkIGNhcCBjb21wYXJlcyByYXcg4oK5KS4KZnJvbSBfX2Z1dHVyZV9fIGltcG9ydCBhbm5vdGF0aW9ucwoKaW1wb3J0IG9zCmltcG9ydCBzeXMKZnJvbSBkYXRldGltZSBpbXBvcnQgZGF0ZQoKSEVSRSA9IG9zLnBhdGguZGlybmFtZShvcy5wYXRoLmFic3BhdGgoX19maWxlX18pKQpzeXMucGF0aC5pbnNlcnQoMCwgb3MucGF0aC5hYnNwYXRoKG9zLnBhdGguam9pbihIRVJFLCAiLi4iLCAiLi4iLCAiLi4iKSkpCm9zLmVudmlyb24uc2V0ZGVmYXVsdCgiU0NBTFBfTE9UX1NJWkVfT0ZGTElORSIsICIxIikKCmZyb20gYXBwLmJhY2t0ZXN0LmVuZ2luZS5sb3RfY29tcG91bmRpbmcgaW1wb3J0IExvdENvbXBvdW5kZXIgICAjIG5vcWE6IEU0MDIKCkZBSUxTID0gW10KCgpkZWYgY2hlY2sobmFtZSwgb2ssIG5vdGU9IiIpOgogICAgcHJpbnQoZiIgIHsnUEFTUycgaWYgb2sgZWxzZSAnRkFJTCd9ICB7bmFtZX17KCcgIOKAlCAnICsgc3RyKG5vdGUpKSBpZiAobm90ZSBhbmQgbm90IG9rKSBlbHNlICcnfSIpCiAgICBpZiBub3Qgb2s6CiAgICAgICAgRkFJTFMuYXBwZW5kKG5hbWUpCgoKUyA9IGRhdGUoMjAyMCwgMSwgMSkKYmFzZSA9IHsibG90X2NvbXBfc3RlcF9tb250aHMiOiAzLCAibG90X2NvbXBfYWRkX2xvdHMiOiAxfQoKcHJpbnQoIuKUgOKUgCBtYXggbG90cyDilIDilIAiKQpjID0gTG90Q29tcG91bmRlcih7KipiYXNlLCAibG90X2NvbXBfbWF4X2xvdHMiOiAxNX0sIFMsIDEwKQpjaGVjaygiY2FwIHBhcnNlZCIsIGMubWF4X2xvdHMgPT0gMTUpCmNoZWNrKCJiZWxvdyBjYXAgdW5jaGFuZ2VkOiAyMDIxLTAxID0gMTQiLCBjLmxvdHMoZGF0ZSgyMDIxLCAxLCAxKSkgPT0gMTQpCmNoZWNrKCJhdCBjYXA6IDIwMjEtMDQgPSAxNSIsIGMubG90cyhkYXRlKDIwMjEsIDQsIDEpKSA9PSAxNSkKY2hlY2soImFib3ZlIGNhcCBoZWxkOiAyMDI2LTAxID0gMTUgKG5vdCAzNCkiLCBjLmxvdHMoZGF0ZSgyMDI2LCAxLCAxKSkgPT0gMTUpCmNoZWNrKCJtdWx0IGF0IGNhcCA9IDEuNSIsIGFicyhjLm11bHQoZGF0ZSgyMDI2LCAxLCAxKSkgLSAxLjUpIDwgMWUtMTIpCmNoZWNrKCJwZWFrX2xvdHMgaG9ub3VycyBjYXAiLCBjLnBlYWtfbG90cyhkYXRlKDIwMjYsIDgsIDMxKSkgPT0gMTUpCmNoZWNrKCJzY2FsZV9sb3RzIGF0IGNhcDogMTAg4oaSIDE1IiwgYy5zY2FsZV9sb3RzKDEwLCBkYXRlKDIwMjYsIDEsIDEpKSA9PSAxNSkKY2hlY2soInNjYWxlX3JzIGF0IGNhcDogMzUwMDAg4oaSIDUyNTAwIiwgYWJzKGMuc2NhbGVfcnMoMzUwMDAsIGRhdGUoMjAyNiwgMSwgMSkpIC0gNTI1MDApIDwgMWUtOSkKZCA9IGMuZGlhZygpCmNoZWNrKCJkaWFnIGNhcnJpZXMgbWF4X2xvdHMiLCBkWyJtYXhfbG90cyJdID09IDE1IGFuZCBkWyJzY2FsZV9ycyJdIGlzIFRydWUpCmMudGllcihkYXRlKDIwMjYsIDEsIDEpKQpjaGVjaygiZGlhZyB0aWVyIGxvdHMgY2FwcGVkIiwgYWxsKHRbImxvdHMiXSA8PSAxNSBmb3IgdCBpbiBjLmRpYWcoKVsidGllcnMiXSkpCmNoZWNrKCJkZXNjcmliZSBtZW50aW9ucyBtYXgiLCAibWF4PTE1IiBpbiBjLmRlc2NyaWJlKCkpCmNfbG8gPSBMb3RDb21wb3VuZGVyKHsqKmJhc2UsICJsb3RfY29tcF9tYXhfbG90cyI6IDV9LCBTLCAxMCkKY2hlY2soImNhcCBiZWxvdyBiYXNlIGlzIGlnbm9yZWQgKHVuY2FwcGVkKSIsIGNfbG8ubWF4X2xvdHMgPT0gMCBhbmQgY19sby5sb3RzKGRhdGUoMjAyNiwgMSwgMSkpID09IDM0KQpjX2VxID0gTG90Q29tcG91bmRlcih7KipiYXNlLCAibG90X2NvbXBfbWF4X2xvdHMiOiAxMH0sIFMsIDEwKQpjaGVjaygiY2FwID09IGJhc2Ug4oaSIGZsYXQgYXQgYmFzZSBmb3JldmVyIiwgY19lcS5vbiBhbmQgY19lcS5sb3RzKGRhdGUoMjAyNiwgMSwgMSkpID09IDEwKQpjX3N0ciA9IExvdENvbXBvdW5kZXIoeyoqYmFzZSwgImxvdF9jb21wX21heF9sb3RzIjogIjIwIn0sIFMsIDEwKQpjaGVjaygiY2FwIGFjY2VwdHMgYSBzdHJpbmciLCBjX3N0ci5tYXhfbG90cyA9PSAyMCkKY19qdW5rID0gTG90Q29tcG91bmRlcih7KipiYXNlLCAibG90X2NvbXBfbWF4X2xvdHMiOiAieCJ9LCBTLCAxMCkKY2hlY2soImp1bmsgY2FwIOKGkiB1bmNhcHBlZCIsIGNfanVuay5tYXhfbG90cyA9PSAwKQpjX29mZiA9IExvdENvbXBvdW5kZXIoeyJsb3RfY29tcF9tYXhfbG90cyI6IDE1fSwgUywgMTApCmNoZWNrKCJjYXAgYWxvbmUgZG9lcyBub3Qgc3dpdGNoIGNvbXBvdW5kaW5nIG9uIiwgbm90IGNfb2ZmLm9uIGFuZCBjX29mZi5sb3RzKGRhdGUoMjAyNiwgMSwgMSkpID09IDEwKQoKcHJpbnQoIuKUgOKUgCDigrkta25vYiBzd2l0Y2gg4pSA4pSAIikKZiA9IExvdENvbXBvdW5kZXIoeyoqYmFzZSwgImxvdF9jb21wX3NjYWxlX3JzIjogRmFsc2V9LCBTLCAxMCkKRCA9IGRhdGUoMjAyMCwgNCwgMSkgICAjIHRpZXIgMSwgbXVsdCAxLjEKY2hlY2soImRlZmF1bHQgaXMgc2NhbGUgT04iLCBMb3RDb21wb3VuZGVyKGJhc2UsIFMsIDEwKS5zY2FsZV9yc19vbikKY2hlY2soImZhbHNlIOKGkiBPRkYiLCBub3QgZi5zY2FsZV9yc19vbikKY2hlY2soJyJmYWxzZSIgc3RyaW5nIOKGkiBPRkYnLCBub3QgTG90Q29tcG91bmRlcih7KipiYXNlLCAibG90X2NvbXBfc2NhbGVfcnMiOiAiZmFsc2UifSwgUywgMTApLnNjYWxlX3JzX29uKQpjaGVjaygnIjAiIHN0cmluZyDihpIgT0ZGJywgbm90IExvdENvbXBvdW5kZXIoeyoqYmFzZSwgImxvdF9jb21wX3NjYWxlX3JzIjogIjAifSwgUywgMTApLnNjYWxlX3JzX29uKQpjaGVjaygidHJ1ZSBzdHJpbmcg4oaSIE9OIiwgTG90Q29tcG91bmRlcih7KipiYXNlLCAibG90X2NvbXBfc2NhbGVfcnMiOiAidHJ1ZSJ9LCBTLCAxMCkuc2NhbGVfcnNfb24pCmNoZWNrKCJsb3RzIHN0aWxsIGdyb3cgd2l0aCDigrkgZml4ZWQiLCBmLmxvdHMoRCkgPT0gMTEgYW5kIGYuc2NhbGVfbG90cygxMCwgRCkgPT0gMTEpCmNoZWNrKCJzY2FsZV9ycyBpcyBpZGVudGl0eSB3aXRoIOKCuSBmaXhlZCIsIGYuc2NhbGVfcnMoMzUwMDAsIEQpID09IDM1MDAwKQpjaGVjaygicnNfbXVsdCA9IDEgd2l0aCDigrkgZml4ZWQiLCBmLnJzX211bHQoRCkgPT0gMS4wKQpjaGVjaygicnNfbXVsdCA9IG11bHQgd2l0aCDigrkgc2NhbGluZyIsIGFicyhMb3RDb21wb3VuZGVyKGJhc2UsIFMsIDEwKS5yc19tdWx0KEQpIC0gMS4xKSA8IDFlLTEyKQpjaGVjaygicnNfbXVsdCA9IDEgd2hlbiBPRkYiLCBMb3RDb21wb3VuZGVyKHt9LCBTLCAxMCkucnNfbXVsdChEKSA9PSAxLjApCmNoZWNrKCJkaWFnIHNjYWxlX3JzIEZhbHNlIiwgZi5kaWFnKClbInNjYWxlX3JzIl0gaXMgRmFsc2UpCmNoZWNrKCJkZXNjcmliZSBzYXlzIHJzPWZpeGVkIiwgInJzPWZpeGVkIiBpbiBmLmRlc2NyaWJlKCkpCgojIOKUgOKUgCBydW5uZXIgY2hlY2tzIG9uIHRoZSBzaGFyZWQgc3ludGhldGljIGNvcnB1cyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKcHJpbnQoIuKUgOKUgCBydW5uZXJzIOKUgOKUgCIpCnRyeToKICAgIGltcG9ydCBpbXBvcnRsaWIudXRpbCBhcyBfaWx1CiAgICBzcGVjID0gX2lsdS5zcGVjX2Zyb21fZmlsZV9sb2NhdGlvbigiX2xjciIsIG9zLnBhdGguam9pbihIRVJFLCAidGVzdF9sb3RfY29tcF9ydW5uZXJzLnB5IikpCiAgICAjIFRoZSBydW5uZXIgc3VpdGUgYnVpbGRzIHRoZSBjb3JwdXMgYXQgaW1wb3J0IGFuZCBydW5zIGl0cyBvd24gY2hlY2tzOyB3ZQogICAgIyBvbmx5IHdhbnQgaXRzIGhlbHBlcnMsIHNvIGd1YXJkOiBpbXBvcnQgaW4gYSBjaGlsZCBwcm9jZXNzIHdvdWxkIHJlLXJ1bgogICAgIyBldmVyeXRoaW5nLiBCdWlsZCBhIHNtYWxsIGNvcnB1cyBoZXJlIGluc3RlYWQgKHNhbWUgZ2VuZXJhdG9yLCAzMCBkYXlzKS4KICAgIHNyYyA9IG9wZW4ob3MucGF0aC5qb2luKEhFUkUsICJ0ZXN0X2xvdF9jb21wX3J1bm5lcnMucHkiKSwgZW5jb2Rpbmc9InV0Zi04IikucmVhZCgpCiAgICBoZWFkID0gc3JjLnNwbGl0KCJCQVNFID0gMTAiKVswXSAgICAgICAgICAgICAgICAjIGNvcnB1cyBidWlsZGVyICsgbW9ua2V5cGF0Y2gsIG5vIGNoZWNrcwogICAgaGVhZCA9IGhlYWQucmVwbGFjZSgid2hpbGUgbGVuKGFsbF9kYXlzKSA8IDQ1OiIsICJ3aGlsZSBsZW4oYWxsX2RheXMpIDwgMzA6IikKICAgIG5zID0geyJfX2ZpbGVfXyI6IG9zLnBhdGguam9pbihIRVJFLCAidGVzdF9sb3RfY29tcF9ydW5uZXJzLnB5IiksICJfX25hbWVfXyI6ICJfbGNyX2hlYWQifQogICAgZXhlYyhjb21waWxlKGhlYWQsICJsY3JfaGVhZCIsICJleGVjIiksIG5zKQogICAgZGJwLCBSVU5fRlJPTSwgUlVOX1RPLCBhbGxfZGF5cyA9IG5zWyJkYnAiXSwgbnNbIlJVTl9GUk9NIl0sIG5zWyJSVU5fVE8iXSwgbnNbImFsbF9kYXlzIl0KICAgIGRheV9vZl90cyA9IG5zWyJkYXlfb2ZfdHMiXQogICAgYm91bmRhcnkgPSBuZXh0KGRkIGZvciBkZCBpbiBhbGxfZGF5cyBpZiBMb3RDb21wb3VuZGVyKHsibG90X2NvbXBfc3RlcF9tb250aHMiOiAxLCAibG90X2NvbXBfYWRkX2xvdHMiOiAxMH0sIFJVTl9GUk9NLCAxMCkudGllcihkZCkgPT0gMSkKCiAgICBmcm9tIGFwcC5iYWNrdGVzdC5zdGZjLmJhY2t0ZXN0X3N0ZmNfcnVubmVyIGltcG9ydCBydW5fc3RmY19iYWNrdGVzdAogICAgciA9IHJ1bl9zdGZjX2JhY2t0ZXN0KGRiX3BhdGg9ZGJwLCBzdHJhdGVneV9pZD0iU1RGQ19WMSIsIHVuZGVybHlpbmc9Ik5JRlRZIiwgZGF0ZV9mcm9tPVJVTl9GUk9NLCBkYXRlX3RvPVJVTl9UTywKICAgICAgICAgICAgICAgICAgICAgICAgICBjb25maWdfb3ZlcnJpZGU9eyJsb3RzIjogMTAsICJwcmVtaXVtX21pbiI6IDAsICJwcmVtaXVtX21heCI6IDEwMDAsCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAibG90X2NvbXBfc3RlcF9tb250aHMiOiAxLCAibG90X2NvbXBfYWRkX2xvdHMiOiAxMCwgImxvdF9jb21wX21heF9sb3RzIjogMTV9KQogICAgcTEgPSBzb3J0ZWQoe2ludCh0LnF0eSkgZm9yIHQgaW4gclsidHJhZGVzIl0gaWYgZGF5X29mX3RzKHQuZW50cnlfdHMpID49IGJvdW5kYXJ5fSkKICAgIHEwID0gc29ydGVkKHtpbnQodC5xdHkpIGZvciB0IGluIHJbInRyYWRlcyJdIGlmIGRheV9vZl90cyh0LmVudHJ5X3RzKSA8IGJvdW5kYXJ5fSkKICAgIGNoZWNrKCJTVEZDOiB0aWVyIDAgYXQgYmFzZSBsb3RzIiwgYm9vbChxMCkgYW5kIGFsbChxICUgMTAgPT0gMCBmb3IgcSBpbiBxMCksIHEwKQogICAgY2hlY2soIlNURkM6IHRpZXIgMSBjYXBwZWQgYXQgMTUgbG90cyAobm90IDIwKSIsIGJvb2wocTEpIGFuZCBhbGwocSA9PSBxMFswXSAvLyAxMCAqIDE1IGZvciBxIGluIHExKSwgcTEpCgogICAgZnJvbSBhcHAuYmFja3Rlc3QudHNnLmJhY2t0ZXN0X3RzZ19ydW5uZXIgaW1wb3J0IHJ1bl90c2dfYmFja3Rlc3QKICAgIGxlZ3MgPSBbeyJpZCI6ICJMMSIsICJhY3Rpb24iOiAiU0VMTCIsICJvcHRfdHlwZSI6ICJDRSIsICJsb3RzIjogMTAsICJwcmVtaXVtX21heCI6IDg1fSwKICAgICAgICAgICAgeyJpZCI6ICJMMiIsICJhY3Rpb24iOiAiU0VMTCIsICJvcHRfdHlwZSI6ICJQRSIsICJsb3RzIjogMTAsICJwcmVtaXVtX21heCI6IDg1fSwKICAgICAgICAgICAgeyJpZCI6ICJMMyIsICJhY3Rpb24iOiAiQlVZIiwgIm9wdF90eXBlIjogIkNFIiwgImxvdHMiOiAxMCwgInByZW1pdW1fbWF4IjogMzB9LAogICAgICAgICAgICB7ImlkIjogIkw0IiwgImFjdGlvbiI6ICJCVVkiLCAib3B0X3R5cGUiOiAiUEUiLCAibG90cyI6IDEwLCAicHJlbWl1bV9tYXgiOiAzMH1dCiAgICB0Y2ZnID0geyJsZWdzIjogbGVncywgIm10bV9zbCI6IDYwMDAsICJtdG1fdGFyZ2V0IjogMCwgImxvdF9jb21wX3N0ZXBfbW9udGhzIjogMSwgImxvdF9jb21wX2FkZF9sb3RzIjogMTB9CiAgICBmbGF0ID0gcnVuX3RzZ19iYWNrdGVzdChkYl9wYXRoPWRicCwgc3RyYXRlZ3lfaWQ9IlRTR19WMSIsIHVuZGVybHlpbmc9Ik5JRlRZIiwgZGF0ZV9mcm9tPVJVTl9GUk9NLCBkYXRlX3RvPVJVTl9UTywKICAgICAgICAgICAgICAgICAgICAgICAgICAgIGNvbmZpZ19vdmVycmlkZT17ImxlZ3MiOiBsZWdzLCAibXRtX3NsIjogNjAwMCwgIm10bV90YXJnZXQiOiAwfSkKICAgIHNjYWwgPSBydW5fdHNnX2JhY2t0ZXN0KGRiX3BhdGg9ZGJwLCBzdHJhdGVneV9pZD0iVFNHX1YxIiwgdW5kZXJseWluZz0iTklGVFkiLCBkYXRlX2Zyb209UlVOX0ZST00sIGRhdGVfdG89UlVOX1RPLCBjb25maWdfb3ZlcnJpZGU9dGNmZykKICAgIGZpeGQgPSBydW5fdHNnX2JhY2t0ZXN0KGRiX3BhdGg9ZGJwLCBzdHJhdGVneV9pZD0iVFNHX1YxIiwgdW5kZXJseWluZz0iTklGVFkiLCBkYXRlX2Zyb209UlVOX0ZST00sIGRhdGVfdG89UlVOX1RPLAogICAgICAgICAgICAgICAgICAgICAgICAgICAgY29uZmlnX292ZXJyaWRlPXsqKnRjZmcsICJsb3RfY29tcF9zY2FsZV9ycyI6IEZhbHNlfSkKCiAgICBkZWYgc2xfZGF5cyhyZXMpOgogICAgICAgIHJldHVybiBzb3J0ZWQoe2RheV9vZl90cyh0LmVudHJ5X3RzKSBmb3IgdCBpbiByZXNbInRyYWRlcyJdIGlmIHQuZXhpdF9yZWFzb24gPT0gIk1UTV9TTCJ9KQogICAgY2hlY2soIlRTRzogc2NhbGVkIOKCuSBrbm9icyDihpIgc2FtZSBNVE1fU0wgZGF5cyBhcyBmbGF0Iiwgc2xfZGF5cyhzY2FsKSA9PSBzbF9kYXlzKGZsYXQpKQogICAgZml4ZWRfdDEgPSBbZGQgZm9yIGRkIGluIHNsX2RheXMoZml4ZCkgaWYgZGQgPj0gYm91bmRhcnldCiAgICBmbGF0X3QxID0gW2RkIGZvciBkZCBpbiBzbF9kYXlzKGZsYXQpIGlmIGRkID49IGJvdW5kYXJ5XQogICAgY2hlY2soIlRTRzogZml4ZWQg4oK5IFNMIGF0IDLDlyBsb3RzIHRyaXBzIG9uIGF0IGxlYXN0IGFzIG1hbnkgZGF5cyBhZnRlciB0aGUgYm91bmRhcnkiLAogICAgICAgICAgc2V0KGZsYXRfdDEpIDw9IHNldChmaXhlZF90MSksIGYiZmxhdCB7ZmxhdF90MX0gZml4ZWQge2ZpeGVkX3QxfSIpCiAgICBjaGVjaygiVFNHOiBmaXhlZCDigrkgcnVuIHN0aWxsIGRvdWJsZWQgaXRzIGxvdHMiLCBhbnkoaW50KHQucXR5KSA9PSAxMzAwIGZvciB0IGluIGZpeGRbInRyYWRlcyJdIGlmIGRheV9vZl90cyh0LmVudHJ5X3RzKSA+PSBib3VuZGFyeSkpCiAgICBjaGVjaygiVFNHOiBmaXhlZCDigrkgcnVuIGRpZmZlcnMgZnJvbSB0aGUgc2NhbGVkIHJ1biAodGhlIHRvZ2dsZSBkb2VzIHNvbWV0aGluZykiLAogICAgICAgICAgbGVuKGZpeGVkX3QxKSA+IGxlbihmbGF0X3QxKSBvciBbdC5leGl0X3RzIGZvciB0IGluIGZpeGRbInRyYWRlcyJdXSAhPSBbdC5leGl0X3RzIGZvciB0IGluIHNjYWxbInRyYWRlcyJdXSkKCiAgICBmcm9tIGFwcC5iYWNrdGVzdC5zY2FscHY1LmJhY2t0ZXN0X3NjYWxwdjVfcnVubmVyIGltcG9ydCBydW5fc2NhbHB2NV9iYWNrdGVzdAogICAgdjUgPSBsYW1iZGEgZXh0cmE6IHJ1bl9zY2FscHY1X2JhY2t0ZXN0KGRiX3BhdGg9ZGJwLCBzdHJhdGVneV9pZD0iU0NBTFBfVjUiLCB1bmRlcmx5aW5nPSJOSUZUWSIsIGRhdGVfZnJvbT1SVU5fRlJPTSwgZGF0ZV90bz1SVU5fVE8sCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgY29uZmlnX292ZXJyaWRlPXsicXVhbnRpdHkiOiB7ImxvdHMiOiAxMH0sICJvcHRpb25fcHJlbWl1bSI6IHsibWluIjogMCwgIm1heCI6IDEwMDB9LAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIm1heF9sb3NzIjogMTUwMDAwLCAibG90X2NvbXBfc3RlcF9tb250aHMiOiAxLCAibG90X2NvbXBfYWRkX2xvdHMiOiAxMCwgKipleHRyYX0pCiAgICB2NV9zY2FsZSwgdjVfZml4ZWQsIHY1X2ZsYXQgPSB2NSh7fSksIHY1KHsibG90X2NvbXBfc2NhbGVfcnMiOiBGYWxzZX0pLCB2NSh7ImxvdF9jb21wX2FkZF9sb3RzIjogMH0pCiAgICBjaGVjaygiVjU6IHNjYWxlZCBjYXAgc3RvcHMgb24gdGhlIGZsYXQgcnVuJ3MgdHJhZGUiLCBsZW4odjVfc2NhbGVbInRyYWRlcyJdKSA9PSBsZW4odjVfZmxhdFsidHJhZGVzIl0pKQogICAgY2hlY2soIlY1OiBmaXhlZCDigrkgY2FwIGF0IDLDlyBsb3RzIHN0b3BzIG5vIGxhdGVyIHRoYW4gdGhlIHNjYWxlZCBvbmUiLAogICAgICAgICAgbGVuKHY1X2ZpeGVkWyJ0cmFkZXMiXSkgPD0gbGVuKHY1X3NjYWxlWyJ0cmFkZXMiXSksIGYie2xlbih2NV9maXhlZFsndHJhZGVzJ10pfSB2cyB7bGVuKHY1X3NjYWxlWyd0cmFkZXMnXSl9IikKZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgaW1wb3J0IHRyYWNlYmFjawogICAgdHJhY2ViYWNrLnByaW50X2V4YygpCiAgICBjaGVjaygicnVubmVyIGNoZWNrcyBleGVjdXRlIiwgRmFsc2UsIHJlcHIoZSkpCgpwcmludCgpCmlmIEZBSUxTOgogICAgcHJpbnQoZiJGQUlMRUQge2xlbihGQUlMUyl9OiIpCiAgICBmb3IgeCBpbiBGQUlMUzoKICAgICAgICBwcmludCgiICAtIiwgeCkKICAgIHN5cy5leGl0KDEpCnByaW50KCJBTEwgTE9UX0NPTVBfTUFYIENIRUNLUyBQQVNTRUQiKQo=",
}

EDITS = {}


def E(path, *ops):
    EDITS.setdefault(path, []).extend(ops)


# ═══════════════════════════ backend: shared module ═══════════════════════
E(f"{BACKEND}/engine/lot_compounding.py",
  ("replace", 'KEY_ANCHOR = "lot_comp_anchor"\n',
   'KEY_ANCHOR = "lot_comp_anchor"\n'
   '# ── LOT_COMP_MAX_20260924 ──\n'
   'KEY_MAX = "lot_comp_max_lots"      # int, 0 / absent = uncapped; below base = ignored\n'
   'KEY_SCALE_RS = "lot_comp_scale_rs"   # bool, absent = True (₹ knobs scale with lots)\n'),
  ("replace", '    __slots__ = ("step", "add", "start", "base", "on", "_tier_cache", "_seen")\n',
   '    __slots__ = ("step", "add", "start", "base", "on", "_tier_cache", "_seen",\n'
   '                 "max_lots", "scale_rs_on")   # ── LOT_COMP_MAX_20260924 ──\n'),
  ("replace", "        self.on = (self.step > 0 and self.add > 0 and self.base > 0\n",
   "        # ── LOT_COMP_MAX_20260924 ── ladder cap + ₹-knob switch\n"
   "        try:\n"
   "            _mx = int(float((cfg or {}).get(KEY_MAX) or 0)) if isinstance(cfg, dict) else 0\n"
   "        except (TypeError, ValueError):\n"
   "            _mx = 0\n"
   "        self.max_lots = _mx if _mx >= self.base else 0      # 0 = uncapped\n"
   "        _sr = (cfg or {}).get(KEY_SCALE_RS, True) if isinstance(cfg, dict) else True\n"
   "        self.scale_rs_on = (str(_sr).strip().lower() not in (\"0\", \"false\", \"no\", \"off\")\n"
   "                            if isinstance(_sr, str) else bool(_sr))\n"
   "        self.on = (self.step > 0 and self.add > 0 and self.base > 0\n"),
  ("replace", "        if not self.on:\n            return self.base\n        return self.base + self.tier(day) * self.add\n",
   "        if not self.on:\n            return self.base\n"
   "        n = self.base + self.tier(day) * self.add\n"
   "        return min(n, self.max_lots) if self.max_lots else n   # ── LOT_COMP_MAX_20260924 ──\n"),
  ("replace", "        if not self.on or v == 0:\n            return v\n        return v * self.mult(day)\n",
   "        if not self.on or v == 0 or not self.scale_rs_on:   # ── LOT_COMP_MAX_20260924 ── toggle\n"
   "            return v\n        return v * self.mult(day)\n"
   "\n"
   "    def rs_mult(self, day) -> float:\n"
   "        \"\"\"Ratio applied to ₹ knobs today: mult(day) when they scale, else 1.\n"
   "        Runners that book a RUN-cumulative ₹ total in base-lot units (V5)\n"
   "        divide by this, so a fixed ₹ cap is compared against raw rupees.\"\"\"\n"
   "        return self.mult(day) if (self.on and self.scale_rs_on) else 1.0\n"),
  ("replace", "        return self.base + (months_elapsed(self.start, d) // self.step) * self.add\n",
   "        n = self.base + (months_elapsed(self.start, d) // self.step) * self.add\n"
   "        return min(n, self.max_lots) if self.max_lots else n   # ── LOT_COMP_MAX_20260924 ──\n"),
  ("replace", '            "base_lots": self.base,\n',
   '            "base_lots": self.base,\n'
   '            "max_lots": self.max_lots or None, "scale_rs": self.scale_rs_on,   # ── LOT_COMP_MAX_20260924 ──\n'),
  ("replace", '            "tiers": [{"tier": t, "lots": self.base + t * self.add,\n',
   '            "tiers": [{"tier": t, "lots": (min(self.base + t * self.add, self.max_lots)\n'
   '                                         if self.max_lots else self.base + t * self.add),\n'),
  ("replace", '        return (f"lot_comp step={self.step}mo add={self.add} "\n                f"base={self.base} start={self.start.isoformat()}")\n',
   '        return (f"lot_comp step={self.step}mo add={self.add} "\n                f"base={self.base} start={self.start.isoformat()}"\n'
   '                f"{(\' max=\' + str(self.max_lots)) if self.max_lots else \'\'}"\n'
   '                f"{\'\' if self.scale_rs_on else \' rs=fixed\'}")   # ── LOT_COMP_MAX_20260924 ──\n'))

# SCALP_V5 — run-cumulative cap: base-lot units only when ₹ knobs scale
E(f"{BACKEND}/scalpv5/backtest_scalpv5_runner.py",
  ("replace_all:2", "realised_running += (open_trade.gross or 0.0) / _comp.mult(d)   # ── LOT_COMP_20260924 ── base-lot units\n",
   "realised_running += (open_trade.gross or 0.0) / _comp.rs_mult(d)   # ── LOT_COMP_20260924 / LOT_COMP_MAX_20260924 ── base-lot units when ₹ knobs scale\n"))

# ═══════════════════════════ frontend: helper ═════════════════════════════
E(f"{FRONTEND}/backtest/lotCompounding.js",
  ("replace", "export function lotCompOf(cfg, dateFrom, dateTo) {\n  const p = lotCompParse(cfg);\n  if (!p) return null;\n  const base = baseLotsOf(cfg);\n  const tiers = dateFrom && dateTo ? Math.floor(monthsElapsed(dateFrom, dateTo) / p.step) : 0;\n  const peak = base != null ? base + tiers * p.add : null;\n  const mult = base != null && base > 0 ? (base + tiers * p.add) / base : 1;\n  const tag = `+${p.add}L / ${p.step}mo`;\n  const label = peak != null && tiers > 0 ? `${tag} · ${base}→${peak}L` : (base != null ? `${tag} · from ${base}L` : tag);\n  return { ...p, base, tiers, peak, mult, tag, label };\n}\n",
   "// ── LOT_COMP_MAX_20260924 ── the ladder is capped at lot_comp_max_lots (a cap\n"
   "// below the base is ignored, as in the backend); lot_comp_scale_rs=false\n"
   "// means the ₹ knobs stay fixed while lots grow (shown as \"₹ fixed\").\n"
   "export function lotCompOf(cfg, dateFrom, dateTo) {\n"
   "  const p = lotCompParse(cfg);\n"
   "  if (!p) return null;\n"
   "  const base = baseLotsOf(cfg);\n"
   "  const rawMax = Math.floor(Number(cfg?.lot_comp_max_lots)) || 0;\n"
   "  const max = base != null && rawMax >= base ? rawMax : 0;\n"
   "  const scaleRs = !(cfg?.lot_comp_scale_rs === false || String(cfg?.lot_comp_scale_rs).toLowerCase() === \"false\");\n"
   "  const tiers = dateFrom && dateTo ? Math.floor(monthsElapsed(dateFrom, dateTo) / p.step) : 0;\n"
   "  const capLots = (n) => (max ? Math.min(n, max) : n);\n"
   "  const peak = base != null ? capLots(base + tiers * p.add) : null;\n"
   "  const capped = base != null && max > 0 && base + tiers * p.add > max;\n"
   "  const mult = base != null && base > 0 ? peak / base : 1;\n"
   "  const tag = `+${p.add}L / ${p.step}mo${max ? ` ≤${max}L` : \"\"}${scaleRs ? \"\" : \" · ₹ fixed\"}`;\n"
   "  const label = peak != null && tiers > 0 ? `${tag} · ${base}→${peak}L${capped ? \" (cap)\" : \"\"}` : (base != null ? `${tag} · from ${base}L` : tag);\n"
   "  return { ...p, base, max, scaleRs, tiers, peak, capped, mult, tag, label };\n"
   "}\n"))

# ═══════════════════════════ frontend: Backtest.jsx ═══════════════════════
E(f"{FRONTEND}/Backtest.jsx",
  ("replace", 'import { lotCompChip } from "./backtest/lotCompounding";   // ── LOT_COMP_20260924 ──',
   'import { lotCompChip, lotCompOf } from "./backtest/lotCompounding";   // ── LOT_COMP_20260924 ── ── LOT_COMP_MAX_20260924 ──'),
  # state: two more fields, same LS blob
  ("replace", '  const [compAdd, setCompAdd] = useState(() => loadLotComp().add ?? "");\n'
              "  useEffect(() => {\n"
              "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd })); } catch { /* ignore */ }\n"
              "  }, [compStep, compAdd]);\n",
   '  const [compAdd, setCompAdd] = useState(() => loadLotComp().add ?? "");\n'
   '  const [compMax, setCompMax] = useState(() => loadLotComp().max ?? "");   // ── LOT_COMP_MAX_20260924 ──\n'
   '  const [compScaleRs, setCompScaleRs] = useState(() => loadLotComp().scaleRs ?? true);   // ── LOT_COMP_MAX_20260924 ──\n'
   "  useEffect(() => {\n"
   "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs })); } catch { /* ignore */ }\n"
   "  }, [compStep, compAdd, compMax, compScaleRs]);\n"),
  # buildConfig wrapper: emit max (when > 0) and scale_rs (only when false)
  ("replace", "    if (!base || step <= 0 || add <= 0) return base;\n"
              "    return { ...base, lot_comp_step_months: step, lot_comp_add_lots: add };\n"
              "  }, [buildConfigBase, compStep, compAdd]);\n",
   "    if (!base || step <= 0 || add <= 0) return base;\n"
   "    // ── LOT_COMP_MAX_20260924 ── cap only when set; scale_rs only when OFF\n"
   "    // (absent = true), so a default form still emits exactly the two keys.\n"
   "    const max = Math.floor(Number(compMax)) || 0;\n"
   "    return { ...base, lot_comp_step_months: step, lot_comp_add_lots: add,\n"
   "      ...(max > 0 ? { lot_comp_max_lots: max } : {}),\n"
   "      ...(compScaleRs ? {} : { lot_comp_scale_rs: false }) };\n"
   "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs]);\n"
   "  // ── LOT_COMP_MAX_20260924 ── live ladder preview for the sub-section\n"
   "  const compPreview = useMemo(() => {\n"
   "    try { return lotCompOf(buildConfig(strategyId), dateFrom, dateTo); } catch { return null; }\n"
   "  }, [buildConfig, strategyId, dateFrom, dateTo]);\n"),
  # remove the two inline fields from the Run-parameters grid
  ("replace", "          {/* ── LOT_COMP_20260924 ── calendar-stepped lot compounding, every strategy.\n"
              "              Blank/0 = off. Steps every N months from Date from; +lots per step;\n"
              "              multi-leg configs scale every leg by the same ratio; ₹ knobs scale too. */}\n"
              '          <Field label="Compound step (months)"><input type="number" min="0" step="1" placeholder="off" style={inputStyle} value={compStep} onChange={(e) => setCompStep(e.target.value)} title="Add lots every N calendar months from Date from (blank = off)" /></Field>\n'
              '          <Field label="Lots per step"><input type="number" min="0" step="1" placeholder="off" style={inputStyle} value={compAdd} onChange={(e) => setCompAdd(e.target.value)} title="Lots added at each step; every leg scales by the same ratio, ₹ knobs scale with it" /></Field>\n',
   "          {/* ── LOT_COMP_MAX_20260924 ── compounding fields moved to their own\n"
   "              full-width sub-section at the bottom of this grid */}\n"),
  # the sub-section: last element of the grid, full width, every strategy
  ("replace", "          {/* ── SHARED_EXEC_FIELDS END ── */}\n        </div>\n",
   "          {/* ── SHARED_EXEC_FIELDS END ── */}\n"
   "          {/* ── LOT_COMP_20260924 / LOT_COMP_MAX_20260924 ── Lot compounding: one\n"
   "              sub-section for EVERY strategy, full width, below the strategy's own\n"
   "              parameters so it is never mixed into them. Blank step or lots = off. */}\n"
   '          <div style={{ gridColumn: "1 / -1", marginTop: 8, paddingTop: 10, borderTop: `1px solid ${colors.border.dark}` }}>\n'
   "            <div style={{ ...tmaSecLabel, marginTop: 0 }}>Lot compounding <span style={{ textTransform: \"none\", letterSpacing: 0, fontWeight: 400 }}>· all strategies · blank = off</span></div>\n"
   "            <div style={tmaSecRow}>\n"
   '              <Field label="Step (months)"><input type="number" min="0" step="1" placeholder="off" style={{ ...inputStyle, width: 90 }} value={compStep} onChange={(e) => setCompStep(e.target.value)} title="Add lots every N calendar months from Date from (anniversary-based). Blank = off." /></Field>\n'
   '              <Field label="Lots per step"><input type="number" min="0" step="1" placeholder="off" style={{ ...inputStyle, width: 90 }} value={compAdd} onChange={(e) => setCompAdd(e.target.value)} title="Lots added at each step. Every leg scales by the same ratio, rounded, min 1." /></Field>\n'
   '              <Field label="Max lots"><input type="number" min="0" step="1" placeholder="no cap" style={{ ...inputStyle, width: 90 }} value={compMax} onChange={(e) => setCompMax(e.target.value)} title="Ladder cap — the most lots the run will ever trade (maximum risk). Blank = no cap; a cap below the base lots is ignored." /></Field>\n'
   '              <Field label="₹ knobs">\n'
   '                <select style={{ ...inputStyle, width: 150 }} value={compScaleRs ? "scale" : "fixed"} onChange={(e) => setCompScaleRs(e.target.value !== "fixed")}\n'
   '                  title="Scale with lots: MTM SL/target/trail, ₹ loss caps and limits grow with the ratio (the strategy sized up). Keep fixed: same ₹ risk budget while lots grow.">\n'
   '                  <option value="scale">Scale with lots</option>\n'
   '                  <option value="fixed">Keep fixed ₹</option>\n'
   "                </select>\n"
   "              </Field>\n"
   "            </div>\n"
   '            <div style={{ fontSize: 11, color: colors.text.tertiary, lineHeight: 1.55 }}>\n'
   "              {compPreview\n"
   "                ? <>Ladder: <b>{compPreview.base ?? \"?\"} → {compPreview.peak ?? \"?\"} lots</b> over this date range ({compPreview.tiers} step{compPreview.tiers === 1 ? \"\" : \"s\"}{compPreview.capped ? `, capped at ${compPreview.max}` : \"\"}); new entries only — open positions keep their entry lots; {compPreview.scaleRs ? \"₹ knobs scale with the ratio\" : \"₹ knobs stay fixed\"}.</>\n"
   "                : <>Off — flat lots for the whole run. Set a step and lots per step to grow size over time (e.g. 3 months, +1 lot).</>}\n"
   "            </div>\n"
   "          </div>\n"
   "        </div>\n"))

# ═══════════════════════════ frontend: SweepBuilder axes ══════════════════
E(f"{FRONTEND}/backtest/SweepBuilder.jsx",
  ("replace", '    fmt: (v) => (v > 0 ? `+${Math.floor(v)}L` : "compOFF") },\n',
   '    fmt: (v) => (v > 0 ? `+${Math.floor(v)}L` : "compOFF") },\n'
   "  // ── LOT_COMP_MAX_20260924 ── ladder cap (0 = no cap) and the ₹-knob switch\n"
   '  { key: "lot_comp_max", label: "Compound max lots", strategies: LOT_COMP_STRATS,\n'
   '    hint: "0, 15, 20", parse: _num,\n'
   "    apply: (c, v) => { if (v > 0) c.lot_comp_max_lots = Math.floor(v); else delete c.lot_comp_max_lots; },\n"
   '    fmt: (v) => (v > 0 ? `≤${Math.floor(v)}L` : "noCap") },\n'
   '  { key: "lot_comp_rs", label: "Compound ₹ knobs", strategies: LOT_COMP_STRATS,\n'
   '    hint: "SCALE, FIXED", parse: (tok) => {\n'
   "      const v = tok.trim().toUpperCase();\n"
   '      return [\"SCALE\", \"FIXED\"].includes(v) ? { v: v === \"SCALE\" } : { err: `\"${tok}\" must be SCALE or FIXED` };\n'
   "    },\n"
   "    apply: (c, v) => { if (v) delete c.lot_comp_scale_rs; else c.lot_comp_scale_rs = false; },\n"
   '    fmt: (v) => (v ? "₹scale" : "₹fixed") },\n'))


# ═══════════════════════════ machinery ═══════════════════════════════════

def die(msg):
    print(f"\nABORT: {msg}")
    sys.exit(1)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def apply_ops(text, ops, rel):
    for kind, old, new in ops:
        if kind == "replace":
            n = text.count(old)
            if n != 1:
                die(f"{rel}: anchor found {n}× (need 1): {old.splitlines()[0][:90]!r}")
            text = text.replace(old, new)
        elif kind.startswith("replace_all:"):
            want = int(kind.split(":")[1])
            n = text.count(old)
            if n != want:
                die(f"{rel}: anchor found {n}× (need {want}): {old[:90]!r}")
            text = text.replace(old, new)
        else:
            die(f"bad op kind {kind}")
    return text


def git_dirty(rels):
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--"] + rels, cwd=ROOT,
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    return [l for l in out.splitlines() if l.strip() and not l.startswith("??")]


def main():
    print(f"── {FENCE} ── root {ROOT}")
    if not os.path.isdir(os.path.join(ROOT, BACKEND)) or not os.path.isdir(os.path.join(ROOT, FRONTEND)):
        die("run from the scalp-app repo root (backend/ and frontend/ not found)")
    targets = list(EDITS.keys())
    for rel in targets:
        if not os.path.exists(os.path.join(ROOT, rel)):
            die(f"missing target {rel}")
        if FENCE in read(rel):
            die(f"{FENCE} already present in {rel} — nothing to do")
    for rel in PAYLOADS_B64:
        if os.path.exists(os.path.join(ROOT, rel)):
            die(f"{rel} already exists — mixed state; remove it and re-run ONLY this script")
    for rel, fence in PREREQ.items():
        if fence not in read(rel):
            die(f"prerequisite {fence} not found in {rel} — apply apply_lot_compounding.py first")
    print(f"   prerequisites ok ({len(PREREQ)})")
    dirty = git_dirty(targets)
    if dirty:
        print("   git status shows uncommitted changes on targets:")
        for l in dirty:
            print("     ", l)
        if not ALLOW_DIRTY:
            die("an `M` on a target is a stop sign; if that M is the parent LOT_COMP patch not yet committed, re-run with --allow-dirty")
        print("   --allow-dirty: continuing")

    staged = {rel: apply_ops(read(rel), ops, rel) for rel, ops in EDITS.items()}
    for rel, b64 in PAYLOADS_B64.items():
        staged[rel] = base64.b64decode(b64).decode("utf-8")
    tmp = tempfile.mkdtemp(prefix="lot_comp_max_gate_")
    for rel, txt in staged.items():
        if rel.endswith(".py"):
            p = os.path.join(tmp, os.path.basename(rel))
            with open(p, "w", encoding="utf-8") as f:
                f.write(txt)
            try:
                py_compile.compile(p, doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"   {len(EDITS)} files edited in memory, {len(PAYLOADS_B64)} payload; py_compile gate ok")

    written = []
    for rel, txt in staged.items():
        abs_ = os.path.join(ROOT, rel)
        if os.path.exists(abs_):
            shutil.copy2(abs_, abs_ + f".bak-{FENCE}")
        os.makedirs(os.path.dirname(abs_), exist_ok=True)
        with open(abs_, "w", encoding="utf-8") as f:
            f.write(txt)
        written.append(rel)
        for src_root, dst_root in ((BACKEND, DESKTOP_BACKEND), (FRONTEND, DESKTOP_FRONTEND)):
            if rel.startswith(src_root) and os.path.isdir(os.path.join(ROOT, dst_root)):
                mirror = os.path.join(ROOT, dst_root + rel[len(src_root):])
                os.makedirs(os.path.dirname(mirror), exist_ok=True)
                with open(mirror, "w", encoding="utf-8") as f:
                    f.write(txt)
    print(f"   wrote {len(written)} files (backups: .bak-{FENCE})")
    with open(os.path.join(ROOT, f".{FENCE}.done"), "w") as f:
        f.write("applied\n")
    if SKIP_TESTS:
        print("   --skip-tests: done")
        return

    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "backend"), SCALP_LOT_SIZE_OFFLINE="1")
    suites = [
        "app/backtest/engine/test_lot_comp_max.py",
        "app/backtest/engine/test_lot_compounding.py",
        "app/backtest/engine/test_lot_comp_runners.py",
    ]
    failed = []
    for s in suites:
        r = subprocess.run([sys.executable, s], cwd=os.path.join(ROOT, "backend"),
                           env=env, capture_output=True, text=True, timeout=1800)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        print(f"   {'ok  ' if r.returncode == 0 else 'FAIL'} {s}  — {tail[:100]}")
        if r.returncode != 0:
            failed.append(s)
            print((r.stdout + r.stderr)[-3000:])
    esb = (os.environ.get("ESBUILD") or
           (os.path.join(ROOT, "frontend", "node_modules", ".bin", "esbuild")
            if os.path.exists(os.path.join(ROOT, "frontend", "node_modules", ".bin", "esbuild")) else None) or
           shutil.which("esbuild") or ("npx" if shutil.which("npx") else None))
    if esb:
        for rel in [f"{FRONTEND}/Backtest.jsx", f"{FRONTEND}/backtest/SweepBuilder.jsx",
                    f"{FRONTEND}/backtest/lotCompounding.js"]:
            cmd = ([esb, "--yes", "esbuild"] if esb == "npx" else [esb])
            r = subprocess.run(cmd + [rel, "--loader:.jsx=jsx", "--loader:.js=jsx",
                                      "--log-level=error", "--outfile=/dev/null"],
                               cwd=ROOT, capture_output=True, text=True, timeout=300)
            print(f"   {'ok  ' if r.returncode == 0 else 'FAIL'} esbuild {rel}")
            if r.returncode != 0:
                failed.append(rel)
                print(r.stderr[-2000:])
    else:
        print("   (esbuild not found — skipping JSX parse; run ./desktop/build-scalp.sh both)")
    if failed:
        print("\n   FAILURES — rolling back to the .bak files:")
        for rel in written:
            abs_ = os.path.join(ROOT, rel)
            bak = abs_ + f".bak-{FENCE}"
            if os.path.exists(bak):
                shutil.move(bak, abs_)
            else:
                os.remove(abs_)
        try:
            os.remove(os.path.join(ROOT, f".{FENCE}.done"))
        except OSError:
            pass
        die("tests failed; working copy restored")
    print(f"\n── {FENCE} applied. Next: ./desktop/build-scalp.sh both ──")


if __name__ == "__main__":
    main()
