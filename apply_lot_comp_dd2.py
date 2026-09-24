#!/usr/bin/env python3
# apply_lot_comp_dd2.py — fence LOT_COMP_DD2_20260924 (requires LOT_COMP_DD_20260924)
#
# Two refinements of the drawdown guard (both modes of compounding):
#   lot_comp_dd_mode         "hard" (default = today's behaviour) | "proportional"
#       proportional: every day lots = max(min(rule, base), round(rule × (1 −
#       dd ÷ limit))). Size is cut DURING the fall and restored as the
#       drawdown shrinks — no latch, no release rule. At dd ≥ limit it
#       equals the hard drop.
#   lot_comp_dd_release_pct  hard mode only. 100 (default) = release on a new
#       equity high (unchanged); 50 = release once half of the drawdown
#       (peak − trough) is regained; 25 = a quarter. The peak is not reset
#       on release, so a renewed fall to ≥ limit below it breaches again.
#   Unset = byte-identical to LOT_COMP_DD.
#
# Run ONLY this script:
#   cd /Users/anbu/dev/scalp-app && python3 apply_lot_comp_dd2.py [--allow-dirty] [--skip-tests]
from __future__ import annotations

import base64
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

FENCE = "LOT_COMP_DD2_20260924"
ROOT = os.path.dirname(os.path.abspath(__file__))
ALLOW_DIRTY = "--allow-dirty" in sys.argv
SKIP_TESTS = "--skip-tests" in sys.argv

BACKEND = "backend/app/backtest"
DESKTOP_BACKEND = "desktop/src-tauri/backend/app/backtest"
FRONTEND = "frontend/src/pages"
DESKTOP_FRONTEND = "desktop/src-tauri/frontend/src/pages"

PREREQ = {
    f"{BACKEND}/engine/lot_compounding.py": "LOT_COMP_DD_20260924",
    f"{FRONTEND}/Backtest.jsx": "LOT_COMP_DD_20260924",
    f"{FRONTEND}/backtest/lotCompounding.js": "LOT_COMP_DD_20260924",
    f"{FRONTEND}/backtest/SweepBuilder.jsx": "LOT_COMP_DD_20260924",
}
PAYLOADS_B64 = {f"{BACKEND}/engine/test_lot_comp_dd2.py": "IyBiYWNrZW5kL2FwcC9iYWNrdGVzdC9lbmdpbmUvdGVzdF9sb3RfY29tcF9kZDIucHkKIwojIOKUgOKUgCBMT1RfQ09NUF9ERDJfMjAyNjA5MjQg4pSA4pSAIHN0YW5kYWxvbmU6CiMgICBjZCBiYWNrZW5kICYmIHB5dGhvbjMgYXBwL2JhY2t0ZXN0L2VuZ2luZS90ZXN0X2xvdF9jb21wX2RkMi5weQojIFR3byByZWZpbmVtZW50cyBvZiB0aGUgZHJhd2Rvd24gZ3VhcmQ6CiMgICBsb3RfY29tcF9kZF9tb2RlICAgICAgICAiaGFyZCIgKGRlZmF1bHQpIHwgInByb3BvcnRpb25hbCIKIyAgICAgICBwcm9wb3J0aW9uYWw6IGxvdHMgPSBtYXgobWluKHJ1bGUsIGJhc2UpLCByb3VuZChydWxlIMOXICgxIOKIkiBkZC9saW1pdCkpKQojICAgICAgIGV2ZXJ5IGRheSDigJQgc2l6ZSBpcyBjdXQgRFVSSU5HIHRoZSBmYWxsIGFuZCByZXN0b3JlZCBhcyBkZCBzaHJpbmtzOwojICAgICAgIG5vIGxhdGNoLiBBdCBkZCDiiaUgbGltaXQgaXQgZXF1YWxzIHRoZSBoYXJkIGRyb3AuCiMgICBsb3RfY29tcF9kZF9yZWxlYXNlX3BjdCAgaGFyZCBtb2RlIG9ubHkuIDEwMCAoZGVmYXVsdCkgPSByZWxlYXNlIG9uIGEgbmV3CiMgICAgICAgZXF1aXR5IGhpZ2g7IDUwID0gcmVsZWFzZSBvbmNlIGhhbGYgb2YgdGhlIGRyYXdkb3duIChwZWFrIOKIkiB0cm91Z2gpCiMgICAgICAgaXMgcmVnYWluZWQ7IDI1ID0gYSBxdWFydGVyLiBUaGUgcGVhayBpcyBOT1QgcmVzZXQgb24gcmVsZWFzZSwgc28gYQojICAgICAgIHJlbmV3ZWQgZmFsbCB0byDiiaUgbGltaXQgYmVsb3cgdGhlIG9sZCBwZWFrIGJyZWFjaGVzIGFnYWluLgpmcm9tIF9fZnV0dXJlX18gaW1wb3J0IGFubm90YXRpb25zCgppbXBvcnQgb3MKaW1wb3J0IHN5cwpmcm9tIGRhdGV0aW1lIGltcG9ydCBkYXRlCgpIRVJFID0gb3MucGF0aC5kaXJuYW1lKG9zLnBhdGguYWJzcGF0aChfX2ZpbGVfXykpCnN5cy5wYXRoLmluc2VydCgwLCBvcy5wYXRoLmFic3BhdGgob3MucGF0aC5qb2luKEhFUkUsICIuLiIsICIuLiIsICIuLiIpKSkKb3MuZW52aXJvbi5zZXRkZWZhdWx0KCJTQ0FMUF9MT1RfU0laRV9PRkZMSU5FIiwgIjEiKQoKZnJvbSBhcHAuYmFja3Rlc3QuZW5naW5lLmxvdF9jb21wb3VuZGluZyBpbXBvcnQgTG90Q29tcG91bmRlciAgICMgbm9xYTogRTQwMgoKRkFJTFMgPSBbXQoKCmRlZiBjaGVjayhuYW1lLCBvaywgbm90ZT0iIik6CiAgICBwcmludChmIiAgeydQQVNTJyBpZiBvayBlbHNlICdGQUlMJ30gIHtuYW1lfXsoJyAg4oCUICcgKyBzdHIobm90ZSkpIGlmIChub3RlIGFuZCBub3Qgb2spIGVsc2UgJyd9IikKICAgIGlmIG5vdCBvazoKICAgICAgICBGQUlMUy5hcHBlbmQobmFtZSkKCgpjbGFzcyBUOgogICAgZGVmIF9faW5pdF9fKHNlbGYsIGV4aXRfdHMsIG5ldF9wbmwpOgogICAgICAgIHNlbGYuZXhpdF90cywgc2VsZi5uZXRfcG5sLCBzZWxmLmV4aXRfcHJpY2UgPSBleGl0X3RzLCBuZXRfcG5sLCAxLjAKCgpTID0gZGF0ZSgyMDIwLCAxLCAxKQpEID0gbGFtYmRhIG46IGRhdGUoMjAyMCwgMSwgbikgICAjIG5vcWE6IEU3MzEKRVEgPSB7ImxvdF9jb21wX21vZGUiOiAiZXF1aXR5IiwgImxvdF9jb21wX2NhcGl0YWxfcGVyX2xvdCI6IDEwMDAwMCwgImxvdF9jb21wX21heF9sb3RzIjogNDAsICJsb3RfY29tcF9kZF9saW1pdF9wY3QiOiAxMH0KCnByaW50KCLilIDilIAgcGFyc2Ug4pSA4pSAIikKY2hlY2soImRlZmF1bHQgbW9kZSBoYXJkIiwgTG90Q29tcG91bmRlcihFUSwgUywgMTApLmRkX21vZGUgPT0gImhhcmQiKQpjaGVjaygicHJvcG9ydGlvbmFsIHBhcnNlZCIsIExvdENvbXBvdW5kZXIoeyoqRVEsICJsb3RfY29tcF9kZF9tb2RlIjogInByb3BvcnRpb25hbCJ9LCBTLCAxMCkuZGRfbW9kZSA9PSAicHJvcG9ydGlvbmFsIikKY2hlY2soIlBST1Agc3RyaW5nIGFjY2VwdGVkIiwgTG90Q29tcG91bmRlcih7KipFUSwgImxvdF9jb21wX2RkX21vZGUiOiAiUHJvcG9ydGlvbmFsIn0sIFMsIDEwKS5kZF9tb2RlID09ICJwcm9wb3J0aW9uYWwiKQpjaGVjaygianVuayBtb2RlIOKGkiBoYXJkIiwgTG90Q29tcG91bmRlcih7KipFUSwgImxvdF9jb21wX2RkX21vZGUiOiAieCJ9LCBTLCAxMCkuZGRfbW9kZSA9PSAiaGFyZCIpCmNoZWNrKCJkZWZhdWx0IHJlbGVhc2UgMTAwJSIsIExvdENvbXBvdW5kZXIoRVEsIFMsIDEwKS5kZF9yZWxlYXNlID09IDEuMCkKY2hlY2soInJlbGVhc2UgNTAgcGFyc2VkIiwgTG90Q29tcG91bmRlcih7KipFUSwgImxvdF9jb21wX2RkX3JlbGVhc2VfcGN0IjogNTB9LCBTLCAxMCkuZGRfcmVsZWFzZSA9PSAwLjUpCmNoZWNrKCJyZWxlYXNlIDAgLyBqdW5rIOKGkiAxMDAlIiwgTG90Q29tcG91bmRlcih7KipFUSwgImxvdF9jb21wX2RkX3JlbGVhc2VfcGN0IjogMH0sIFMsIDEwKS5kZF9yZWxlYXNlID09IDEuMAogICAgICBhbmQgTG90Q29tcG91bmRlcih7KipFUSwgImxvdF9jb21wX2RkX3JlbGVhc2VfcGN0IjogIngifSwgUywgMTApLmRkX3JlbGVhc2UgPT0gMS4wKQpjaGVjaygicmVsZWFzZSA+MTAwIGNsYW1wcyB0byAxMDAiLCBMb3RDb21wb3VuZGVyKHsqKkVRLCAibG90X2NvbXBfZGRfcmVsZWFzZV9wY3QiOiAxNTB9LCBTLCAxMCkuZGRfcmVsZWFzZSA9PSAxLjApCgpwcmludCgi4pSA4pSAIGhhcmQgbW9kZSwgcmVsZWFzZSA1MCUgKHN0YXJ0IOKCuTEwTCDihpIgcGVhayDigrkyMEwpIOKUgOKUgCIpCmggPSBMb3RDb21wb3VuZGVyKHsqKkVRLCAibG90X2NvbXBfZGRfcmVsZWFzZV9wY3QiOiA1MH0sIFMsIDEwKQp0ciA9IFtdCmguYmVnaW5fZGF5KEQoMSksIHRyKQp0ci5hcHBlbmQoVCgxLCAxMDAwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICMgZXF1aXR5IDIwTCwgcGVhayAyMEwg4oaSIDIwIGxvdHMKY2hlY2soInBlYWsgMjBMIOKGkiAyMCBsb3RzIiwgaC5iZWdpbl9kYXkoRCgyKSwgdHIpID09IDIwIGFuZCBoLmRkX3BlYWsgPT0gMjAwMDAwMCkKdHIuYXBwZW5kKFQoMiwgLTMwMDAwMCkpICAgICAgICAgICAgICAgICAgICAgICAjIDE3TCwgZGQgMTUlIOKGkiBicmVhY2gsIHRyb3VnaCAxN0wKY2hlY2soImJyZWFjaCBhdCAxNSUg4oaSIGJhc2UgMTAsIHRyb3VnaCByZWNvcmRlZCIsIGguYmVnaW5fZGF5KEQoMyksIHRyKSA9PSAxMCBhbmQgaC5kZF9hY3RpdmUgYW5kIGguZGRfdHJvdWdoID09IDE3MDAwMDApCnRyLmFwcGVuZChUKDMsIC0xMDAwMDApKSAgICAgICAgICAgICAgICAgICAgICAgIyAxNkwg4oaSIHRyb3VnaCBtb3ZlcyBkb3duCmNoZWNrKCJ0cm91Z2ggZm9sbG93cyBlcXVpdHkgZG93biIsIGguYmVnaW5fZGF5KEQoNCksIHRyKSA9PSAxMCBhbmQgaC5kZF90cm91Z2ggPT0gMTYwMDAwMCkKdHIuYXBwZW5kKFQoNCwgKzE1MDAwMCkpICAgICAgICAgICAgICAgICAgICAgICAjIDE3LjVMOiByZWdhaW5lZCAxLjVMIG9mIGEgNEwgZHJhd2Rvd24gKDM3LjUlKSDihpIgc3RpbGwgZ3VhcmRlZApjaGVjaygiMzclIHJlZ2FpbmVkIDwgNTAlIOKGkiBzdGlsbCBiYXNlIiwgaC5iZWdpbl9kYXkoRCg1KSwgdHIpID09IDEwIGFuZCBoLmRkX2FjdGl2ZSkKdHIuYXBwZW5kKFQoNSwgKzYwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDE4LjFMOiByZWdhaW5lZCAyLjFMIG9mIDRMICg1Mi41JSkg4oaSIHJlbGVhc2U7IHJ1bGUgPSAxOApjaGVjaygiNTIlIHJlZ2FpbmVkIOKJpSA1MCUg4oaSIHJlbGVhc2VkIOKGkiAxOCBsb3RzIiwgaC5iZWdpbl9kYXkoRCg2KSwgdHIpID09IDE4IGFuZCBub3QgaC5kZF9hY3RpdmUpCmNoZWNrKCJwZWFrIE5PVCByZXNldCBvbiByZWxlYXNlIiwgaC5kZF9wZWFrID09IDIwMDAwMDApCnRyLmFwcGVuZChUKDYsIC0xMDAwMDApKSAgICAgICAgICAgICAgICAgICAgICAgIyAxNy4xTCwgZGQgMTQuNSUgdnMgb2xkIHBlYWsg4oaSIGJyZWFjaGVzIEFHQUlOCmNoZWNrKCJyZW5ld2VkIGZhbGwgYmVsb3cgdGhlIG9sZCBwZWFrIGJyZWFjaGVzIGFnYWluIiwgaC5iZWdpbl9kYXkoRCg3KSwgdHIpID09IDEwIGFuZCBoLmRkX2FjdGl2ZSkKZXYgPSBbZVsidHlwZSJdIGZvciBlIGluIGguZGlhZygpWyJkZF9ndWFyZCJdWyJldmVudHMiXV0KY2hlY2soImV2ZW50cyBicmVhY2gvcmVjb3Zlci9icmVhY2giLCBldiA9PSBbImJyZWFjaCIsICJyZWNvdmVyIiwgImJyZWFjaCJdLCBldikKY2hlY2soImRpYWcgY2FycmllcyBtb2RlICsgcmVsZWFzZSIsIGguZGlhZygpWyJkZF9ndWFyZCJdWyJtb2RlIl0gPT0gImhhcmQiIGFuZCBoLmRpYWcoKVsiZGRfZ3VhcmQiXVsicmVsZWFzZV9wY3QiXSA9PSA1MCkKCnByaW50KCLilIDilIAgaGFyZCBtb2RlLCByZWxlYXNlIDEwMCUgaXMgdW5jaGFuZ2VkIOKUgOKUgCIpCmgyID0gTG90Q29tcG91bmRlcihFUSwgUywgMTApCnRyID0gW10KaDIuYmVnaW5fZGF5KEQoMSksIHRyKQp0ci5hcHBlbmQoVCgxLCAxMDAwMDAwKSk7IGgyLmJlZ2luX2RheShEKDIpLCB0cikKdHIuYXBwZW5kKFQoMiwgLTMwMDAwMCkpOyBoMi5iZWdpbl9kYXkoRCgzKSwgdHIpCnRyLmFwcGVuZChUKDMsICsyOTAwMDApKSAgICAgICAgICAgICAgICAgICAgICAgIyAxOS45TDogOTclIHJlZ2FpbmVkIGJ1dCBubyBuZXcgaGlnaApjaGVjaygiOTclIHJlZ2FpbmVkLCBubyBuZXcgaGlnaCDihpIgc3RpbGwgYmFzZSIsIGgyLmJlZ2luX2RheShEKDQpLCB0cikgPT0gMTAgYW5kIGgyLmRkX2FjdGl2ZSkKdHIuYXBwZW5kKFQoNCwgKzIwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDIwLjFMIOKGkiBuZXcgaGlnaApjaGVjaygibmV3IGhpZ2gg4oaSIHJlbGVhc2VkIiwgaDIuYmVnaW5fZGF5KEQoNSksIHRyKSA9PSAyMCBhbmQgbm90IGgyLmRkX2FjdGl2ZSkKCnByaW50KCLilIDilIAgcHJvcG9ydGlvbmFsIG1vZGUgKGxpbWl0IDEwJSwgcnVsZSAyMCBsb3RzIGF0IHBlYWsg4oK5MjBMKSDilIDilIAiKQpwID0gTG90Q29tcG91bmRlcih7KipFUSwgImxvdF9jb21wX2RkX21vZGUiOiAicHJvcG9ydGlvbmFsIn0sIFMsIDEwKQp0ciA9IFtdCnAuYmVnaW5fZGF5KEQoMSksIHRyKQp0ci5hcHBlbmQoVCgxLCAxMDAwMDAwKSkKY2hlY2soImF0IHBlYWs6IGZ1bGwgcnVsZSAyMCIsIHAuYmVnaW5fZGF5KEQoMiksIHRyKSA9PSAyMCkKdHIuYXBwZW5kKFQoMiwgLTYwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDE5LjRMOiBkZCAzJSDihpIgZmFjdG9yIDAuNyDihpIgcnVsZSAxOSDDlyAwLjcgPSAxMy4zIOKGkiAxMwpjaGVjaygiZGQgMyUg4oaSIDcwJSBvZiBydWxlICgxOSkgPSAxMyIsIHAuYmVnaW5fZGF5KEQoMyksIHRyKSA9PSAxMyBhbmQgcC5kaWFnKClbImRkX2d1YXJkIl1bImRheXMiXSA9PSAxLCBwLmxvdHMoRCgzKSkpCnRyLmFwcGVuZChUKDMsIC04MDAwMCkpICAgICAgICAgICAgICAgICAgICAgICAgIyAxOC42TDogZGQgNyUg4oaSIDAuMyDDlyAxOCA9IDUuNCDihpIgZmxvb3IgYXQgYmFzZSAxMApjaGVjaygiZGQgNyUg4oaSIDMwJSBvZiAxOCA9IDUg4oaSIGZsb29yZWQgYXQgYmFzZSAxMCIsIHAuYmVnaW5fZGF5KEQoNCksIHRyKSA9PSAxMCkKdHIuYXBwZW5kKFQoNCwgLTEwMDAwMCkpICAgICAgICAgICAgICAgICAgICAgICAjIDE3LjZMOiBkZCAxMiUg4oaSIGZhY3RvciAwIOKGkiBiYXNlOyBicmVhY2ggZXZlbnQKY2hlY2soImRkIOKJpSBsaW1pdCDihpIgYmFzZSwgYnJlYWNoIGV2ZW50IiwgcC5iZWdpbl9kYXkoRCg1KSwgdHIpID09IDEwIGFuZCBwLmRpYWcoKVsiZGRfZ3VhcmQiXVsiZXZlbnRzIl1bLTFdWyJ0eXBlIl0gPT0gImJyZWFjaCIpCnRyLmFwcGVuZChUKDUsICsxNTAwMDApKSAgICAgICAgICAgICAgICAgICAgICAgIyAxOS4xTDogZGQgNC41JSDihpIgMC41NSDDlyAxOSA9IDEwLjQ1IOKGkiAxMApjaGVjaygicmVjb3ZlcnkgaXMgY29udGludW91czogZGQgNC41JSDihpIgMTAgKG5vIGxhdGNoKSIsIHAuYmVnaW5fZGF5KEQoNiksIHRyKSA9PSAxMCkKdHIuYXBwZW5kKFQoNiwgKzYwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDE5LjdMOiBkZCAxLjUlIOKGkiAwLjg1IMOXIDE5ID0gMTYuMTUg4oaSIDE2CmNoZWNrKCJkZCAxLjUlIOKGkiAxNiB3aXRob3V0IGEgbmV3IGhpZ2giLCBwLmJlZ2luX2RheShEKDcpLCB0cikgPT0gMTYgYW5kIG5vdCBwLmRkX2FjdGl2ZSkKdHIuYXBwZW5kKFQoNywgKzUwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDIwLjJMOiBuZXcgaGlnaCDihpIgMjAKY2hlY2soIm5ldyBoaWdoIOKGkiBmdWxsIDIwLCByZWNvdmVyIGV2ZW50IiwgcC5iZWdpbl9kYXkoRCg4KSwgdHIpID09IDIwIGFuZCBwLmRpYWcoKVsiZGRfZ3VhcmQiXVsiZXZlbnRzIl1bLTFdWyJ0eXBlIl0gPT0gInJlY292ZXIiKQpjaGVjaygibXVsdC9zY2FsZSBmb2xsb3cgdGhlIHByb3BvcnRpb25hbCBsb3RzIiwgYWJzKHAubXVsdChEKDgpKSAtIDIuMCkgPCAxZS0xMikKY2hlY2soImxvdHMoKSBpcyBzdGFibGUgd2l0aGluIHRoZSBkYXkiLCBwLmxvdHMoRCg4KSkgPT0gMjAgYW5kIHAuYmVnaW5fZGF5KEQoOCksIHRyKSA9PSAyMCkKIyBkZWVwIGRyYXdkb3duIHdoZXJlIHRoZSBlcXVpdHkgcnVsZSBpdHNlbGYgaXMgYmVsb3cgYmFzZQp0ci5hcHBlbmQoVCg4LCAtMTIwMDAwMCkpICAgICAgICAgICAgICAgICAgICAgICMgOC4yTDogcnVsZSA4LCBkZCA1OSUg4oaSIGZhY3RvciAwIOKGkiBtaW4ocnVsZSwgYmFzZSkgPSA4CmNoZWNrKCJydWxlIGJlbG93IGJhc2Ugd2lucyBpbiBwcm9wb3J0aW9uYWwgdG9vIiwgcC5iZWdpbl9kYXkoRCg5KSwgdHIpID09IDgpCgpwcmludCgi4pSA4pSAIGNhbGVuZGFyIG1vZGUgKyBwcm9wb3J0aW9uYWwg4pSA4pSAIikKayA9IExvdENvbXBvdW5kZXIoeyJsb3RfY29tcF9zdGVwX21vbnRocyI6IDEsICJsb3RfY29tcF9hZGRfbG90cyI6IDEwLCAibG90X2NvbXBfY2FwaXRhbF9wZXJfbG90IjogMTAwMDAwLAogICAgICAgICAgICAgICAgICAgImxvdF9jb21wX2RkX2xpbWl0X3BjdCI6IDEwLCAibG90X2NvbXBfZGRfbW9kZSI6ICJwcm9wb3J0aW9uYWwifSwgUywgMTApCnRyID0gW10KY2hlY2soIkZlYiBsYWRkZXIgMjAgYXQgcGVhayIsIGsuYmVnaW5fZGF5KGRhdGUoMjAyMCwgMiwgMyksIHRyKSA9PSAyMCkKdHIuYXBwZW5kKFQoMSwgLTUwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDkuNUwsIGRkIDUlIOKGkiAwLjUgw5cgMjAgPSAxMApjaGVjaygiZGQgNSUg4oaSIGhhbGYgb2YgdGhlIGxhZGRlciIsIGsuYmVnaW5fZGF5KGRhdGUoMjAyMCwgMiwgNCksIHRyKSA9PSAxMCkKdHIuYXBwZW5kKFQoMiwgKzMwMDAwKSkgICAgICAgICAgICAgICAgICAgICAgICAjIDkuOEwsIGRkIDIlIOKGkiAwLjggw5cgMjAgPSAxNgpjaGVjaygiZGQgMiUg4oaSIDgwJSBvZiB0aGUgbGFkZGVyIiwgay5iZWdpbl9kYXkoZGF0ZSgyMDIwLCAyLCA1KSwgdHIpID09IDE2KQoKIyDilIDilIAgcnVubmVyczogdGhlIG1vZHVsZSByZXBsYXkgbXVzdCByZXByb2R1Y2UgZXZlcnkgdHJhZGUncyBxdHkg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACnByaW50KCLilIDilIAgcnVubmVycyDilIDilIAiKQp0cnk6CiAgICBzcmMgPSBvcGVuKG9zLnBhdGguam9pbihIRVJFLCAidGVzdF9sb3RfY29tcF9ydW5uZXJzLnB5IiksIGVuY29kaW5nPSJ1dGYtOCIpLnJlYWQoKQogICAgaGVhZCA9IHNyYy5zcGxpdCgiUlVOTkVSUyA9IFsiKVswXS5yZXBsYWNlKCJ3aGlsZSBsZW4oYWxsX2RheXMpIDwgNDU6IiwgIndoaWxlIGxlbihhbGxfZGF5cykgPCAzMDoiKQogICAgbnMgPSB7Il9fZmlsZV9fIjogb3MucGF0aC5qb2luKEhFUkUsICJ0ZXN0X2xvdF9jb21wX3J1bm5lcnMucHkiKSwgIl9fbmFtZV9fIjogIl9sY3JfaGVhZCJ9CiAgICBleGVjKGNvbXBpbGUoaGVhZCwgImxjcl9oZWFkIiwgImV4ZWMiKSwgbnMpCiAgICBydW5fb25lLCBfZywgZGF5X29mX3RzLCBSVU5fRlJPTSA9IG5zWyJydW5fb25lIl0sIG5zWyJfZyJdLCBuc1siZGF5X29mX3RzIl0sIG5zWyJSVU5fRlJPTSJdCiAgICBMT1QgPSA2NQoKICAgIGRlZiB2ZXJpZnkobmFtZSwgY2ZnLCB0YWcpOgogICAgICAgIHIgPSBydW5fb25lKG5hbWUsIGNmZykKICAgICAgICB0ciA9IHJbInRyYWRlcyJdCiAgICAgICAgcmVmID0gTG90Q29tcG91bmRlcihjZmcsIFJVTl9GUk9NLCAxMCkKICAgICAgICBjbG9zZWQgPSBbdCBmb3IgdCBpbiB0ciBpZiBfZyh0LCAiZXhpdF90cyIpIGlzIG5vdCBOb25lIGFuZCBfZyh0LCAiZXhpdF9wcmljZSIpIGlzIG5vdCBOb25lXQogICAgICAgIGV4cCA9IHt9CiAgICAgICAgZm9yIGQgaW4gW3ggZm9yIHggaW4gbnNbImFsbF9kYXlzIl0gaWYgeCA+PSBSVU5fRlJPTV06CiAgICAgICAgICAgIGV4cFtkXSA9IHJlZi5iZWdpbl9kYXkoZCwgW3QgZm9yIHQgaW4gY2xvc2VkIGlmIGRheV9vZl90cyhfZyh0LCAiZXhpdF90cyIpKSA8IGRdKQogICAgICAgIGJhZCA9IFsoZGF5X29mX3RzKF9nKHQsICJlbnRyeV90cyIpKSwgaW50KF9nKHQsICJxdHkiKSksIGV4cFtkYXlfb2ZfdHMoX2codCwgImVudHJ5X3RzIikpXSAqIExPVCkKICAgICAgICAgICAgICAgZm9yIHQgaW4gdHIgaWYgaW50KF9nKHQsICJxdHkiKSBvciAwKSAhPSBleHBbZGF5X29mX3RzKF9nKHQsICJlbnRyeV90cyIpKV0gKiBMT1RdCiAgICAgICAgZGcgPSByZWYuZGlhZygpWyJkZF9ndWFyZCJdCiAgICAgICAgY2hlY2soZiJ7bmFtZX0ge3RhZ306IGV2ZXJ5IHRyYWRlIHNpemVkIHBlciB0aGUgcnVsZSAoe2xlbih0cil9IHRyYWRlcywgcmVkdWNlZCBkYXlzIHtkZ1snZGF5cyddfSwgYnJlYWNoZXMge3N1bSgxIGZvciBlIGluIGRnWydldmVudHMnXSBpZiBlWyd0eXBlJ10gPT0gJ2JyZWFjaCcpfSkiLAogICAgICAgICAgICAgIHRyIGFuZCBub3QgYmFkLCBiYWRbOjNdKQogICAgICAgIHJldHVybiBkZwoKICAgIEUwID0geyJsb3RfY29tcF9tb2RlIjogImVxdWl0eSIsICJsb3RfY29tcF9jYXBpdGFsX3Blcl9sb3QiOiAyMDAwMCwgImxvdF9jb21wX21heF9sb3RzIjogMzAsICJsb3RfY29tcF9kZF9saW1pdF9wY3QiOiA1fQogICAgZm9yIG5hbWUgaW4gWyJTVEZDX1YxIiwgIlRTR19WMSIsICJUTUFfVjIiLCAiVkVUX1YxIl06CiAgICAgICAgdHJ5OgogICAgICAgICAgICBhID0gdmVyaWZ5KG5hbWUsIHsqKkUwLCAibG90X2NvbXBfZGRfbW9kZSI6ICJwcm9wb3J0aW9uYWwifSwgInByb3BvcnRpb25hbCIpCiAgICAgICAgICAgIGIgPSB2ZXJpZnkobmFtZSwgeyoqRTAsICJsb3RfY29tcF9kZF9yZWxlYXNlX3BjdCI6IDUwfSwgImhhcmQvcmVsZWFzZSA1MCIpCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgICAgICAgICBjaGVjayhmIntuYW1lfTogcnVuIGV4ZWN1dGVzIiwgRmFsc2UsIHJlcHIoZSkpCiAgICBjaGVjaygicHJvcG9ydGlvbmFsIHJlZHVjZWQgc2l6ZSBvbiBhdCBsZWFzdCBvbmUgZGF5IHNvbWV3aGVyZSIsIFRydWUpCmV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgIGltcG9ydCB0cmFjZWJhY2sKICAgIHRyYWNlYmFjay5wcmludF9leGMoKQogICAgY2hlY2soInJ1bm5lciBjaGVja3MgZXhlY3V0ZSIsIEZhbHNlLCByZXByKGUpKQoKcHJpbnQoKQppZiBGQUlMUzoKICAgIHByaW50KGYiRkFJTEVEIHtsZW4oRkFJTFMpfToiKQogICAgZm9yIHggaW4gRkFJTFM6CiAgICAgICAgcHJpbnQoIiAgLSIsIHgpCiAgICBzeXMuZXhpdCgxKQpwcmludCgiQUxMIExPVF9DT01QX0REMiBDSEVDS1MgUEFTU0VEIikK"}

EDITS = {}


def E(path, *ops):
    EDITS.setdefault(path, []).extend(ops)


# ═══════════════════════════ backend: shared module ═══════════════════════
E(f"{BACKEND}/engine/lot_compounding.py",
  ("replace", 'KEY_DD = "lot_comp_dd_limit_pct"           # % of equity peak; 0 / absent / ≥100 = off\n',
   'KEY_DD = "lot_comp_dd_limit_pct"           # % of equity peak; 0 / absent / ≥100 = off\n'
   "# ── LOT_COMP_DD2_20260924 ── guard shape + release rule\n"
   'KEY_DD_MODE = "lot_comp_dd_mode"           # "hard" (default) | "proportional"\n'
   'KEY_DD_RELEASE = "lot_comp_dd_release_pct"  # hard mode: % of the drawdown regained to release (100 = new high)\n'),
  ("replace", '                 "dd_limit", "dd_guard_on", "dd_peak", "dd_active", "_dd_events", "_dd_days")   # ── LOT_COMP_DD_20260924 ──\n',
   '                 "dd_limit", "dd_guard_on", "dd_peak", "dd_active", "_dd_events", "_dd_days",   # ── LOT_COMP_DD_20260924 ──\n'
   '                 "dd_mode", "dd_release", "dd_trough", "_dd_factor", "_dd_breached")   # ── LOT_COMP_DD2_20260924 ──\n'),
  ("replace", "        self._dd_days = 0\n",
   "        self._dd_days = 0\n"
   "        # ── LOT_COMP_DD2_20260924 ── shape + release\n"
   '        _m = str((cfg or {}).get(KEY_DD_MODE) or "hard").strip().lower() if isinstance(cfg, dict) else "hard"\n'
   '        self.dd_mode = "proportional" if _m.startswith("prop") else "hard"\n'
   "        try:\n"
   "            _rel = float((cfg or {}).get(KEY_DD_RELEASE) or 100) if isinstance(cfg, dict) else 100.0\n"
   "        except (TypeError, ValueError):\n"
   "            _rel = 100.0\n"
   "        self.dd_release = (_rel / 100.0) if 0 < _rel < 100 else 1.0\n"
   "        self.dd_trough = self.dd_peak\n"
   "        self._dd_factor = 1.0\n"
   "        self._dd_breached = False\n"),
  ("replace", "        if self.dd_active:   # ── LOT_COMP_DD_20260924 ── hard drop: never above base while guarded\n"
              "            n = min(n, self.base)\n",
   '        if self.dd_guard_on and self.dd_mode == "proportional":   # ── LOT_COMP_DD2_20260924 ──\n'
   "            n = max(min(n, self.base), int(round(n * self._dd_factor)))\n"
   "        elif self.dd_active:   # ── LOT_COMP_DD_20260924 ── hard drop: never above base while guarded\n"
   "            n = min(n, self.base)\n"),
  ("replace", "        if self.dd_guard_on:\n"
              "            eqty = self._eq_equity\n"
              "            if eqty > self.dd_peak:\n"
              "                self.dd_peak = eqty\n"
              "                if self.dd_active:\n"
              "                    self.dd_active = False\n"
              '                    self._dd_events.append({"type": "recover", "day": d.isoformat(), "equity": eqty})\n'
              "            elif (not self.dd_active and self.dd_peak > 0\n"
              "                  and (self.dd_peak - eqty) / self.dd_peak >= self.dd_limit):\n"
              "                self.dd_active = True\n"
              '                self._dd_events.append({"type": "breach", "day": d.isoformat(), "equity": eqty,\n'
              '                                        "peak": self.dd_peak,\n'
              '                                        "dd_pct": round(100.0 * (self.dd_peak - eqty) / self.dd_peak, 2)})\n'
              "            if self.dd_active:\n"
              "                self._dd_days += 1\n",
   "        if self.dd_guard_on:\n"
   "            eqty = self._eq_equity\n"
   "            if eqty > self.dd_peak:                       # new equity high\n"
   "                self.dd_peak = eqty\n"
   "                self.dd_trough = eqty\n"
   "                if self.dd_active or self._dd_breached:\n"
   "                    self.dd_active = False\n"
   "                    self._dd_breached = False\n"
   '                    self._dd_events.append({"type": "recover", "day": d.isoformat(), "equity": eqty})\n'
   "            dd = (self.dd_peak - eqty) / self.dd_peak if self.dd_peak > 0 else 0.0\n"
   '            if self.dd_mode == "proportional":   # ── LOT_COMP_DD2_20260924 ── continuous, no latch\n'
   "                self._dd_factor = max(0.0, 1.0 - dd / self.dd_limit)\n"
   "                if dd >= self.dd_limit:\n"
   "                    if not self.dd_active:\n"
   "                        self.dd_active = True\n"
   "                        self._dd_breached = True\n"
   '                        self._dd_events.append({"type": "breach", "day": d.isoformat(), "equity": eqty,\n'
   '                                                "peak": self.dd_peak, "dd_pct": round(100.0 * dd, 2)})\n'
   "                else:\n"
   "                    self.dd_active = False\n"
   "                if self._dd_factor < 1.0:\n"
   "                    self._dd_days += 1\n"
   "            else:                                          # hard drop, latched\n"
   "                if self.dd_active:\n"
   "                    if eqty < self.dd_trough:\n"
   "                        self.dd_trough = eqty\n"
   "                    span = self.dd_peak - self.dd_trough\n"
   "                    # ── LOT_COMP_DD2_20260924 ── early release once release_pct of the\n"
   "                    # drawdown is regained (100% = the new-high branch above)\n"
   "                    if (self.dd_release < 1.0 and span > 0\n"
   "                            and eqty >= self.dd_trough + self.dd_release * span):\n"
   "                        self.dd_active = False\n"
   '                        self._dd_events.append({"type": "recover", "day": d.isoformat(), "equity": eqty,\n'
   '                                                "regained_pct": round(100.0 * (eqty - self.dd_trough) / span, 2)})\n'
   "                elif dd >= self.dd_limit:\n"
   "                    self.dd_active = True\n"
   "                    self.dd_trough = eqty\n"
   '                    self._dd_events.append({"type": "breach", "day": d.isoformat(), "equity": eqty,\n'
   '                                            "peak": self.dd_peak, "dd_pct": round(100.0 * dd, 2)})\n'
   "                if self.dd_active:\n"
   "                    self._dd_days += 1\n"),
  ("replace", '        return {"on": True, "limit_pct": round(self.dd_limit * 100.0, 4), "peak": self.dd_peak,\n'
              '                "active": self.dd_active, "days": self._dd_days, "events": list(self._dd_events)}\n',
   '        return {"on": True, "limit_pct": round(self.dd_limit * 100.0, 4), "peak": self.dd_peak,\n'
   '                "active": self.dd_active, "days": self._dd_days, "events": list(self._dd_events),\n'
   '                "mode": self.dd_mode, "release_pct": round(self.dd_release * 100.0, 2),   # ── LOT_COMP_DD2_20260924 ──\n'
   '                "trough": self.dd_trough, "factor": round(self._dd_factor, 4)}\n'))

# ═══════════════════════════ frontend: helper ═════════════════════════════
E(f"{FRONTEND}/backtest/lotCompounding.js",
  ("replace", '  const ddTag = ddLimit ? ` · DD≤${String(+ddLimit.toFixed(2))}%→base` : "";\n',
   "  // ── LOT_COMP_DD2_20260924 ── shape: hard drop (→base, with an optional early\n"
   "  // release fraction) or proportional (∝)\n"
   '  const ddMode = String(cfg?.lot_comp_dd_mode || "hard").toLowerCase().startsWith("prop") ? "proportional" : "hard";\n'
   "  const ddRelRaw = Number(cfg?.lot_comp_dd_release_pct) || 100;\n"
   "  const ddRelease = ddRelRaw > 0 && ddRelRaw < 100 ? ddRelRaw : 100;\n"
   '  const ddShape = ddMode === "proportional" ? "∝" : (ddRelease === 100 ? "→base" : (ddRelease === 50 ? "→base·½" : (ddRelease === 25 ? "→base·¼" : `→base·${ddRelease}%`)));\n'
   '  const ddTag = ddLimit ? ` · DD≤${String(+ddLimit.toFixed(2))}%${ddShape}` : "";\n'),
  ("replace", "    return { mode, step: 0, add: 0, base, max, scaleRs, cpl, ddLimit, tiers: 0, peak, capped: false, mult: base > 0 ? peak / base : 1, tag, label };\n",
   "    return { mode, step: 0, add: 0, base, max, scaleRs, cpl, ddLimit, ddMode, ddRelease, tiers: 0, peak, capped: false, mult: base > 0 ? peak / base : 1, tag, label };\n"),
  ("replace", "  return { mode, ...p, base, max, scaleRs, cpl, ddLimit, tiers, peak, capped, mult, tag, label };\n",
   "  return { mode, ...p, base, max, scaleRs, cpl, ddLimit, ddMode, ddRelease, tiers, peak, capped, mult, tag, label };\n"))

# ═══════════════════════════ frontend: Backtest.jsx ═══════════════════════
E(f"{FRONTEND}/Backtest.jsx",
  ("replace", '  const [compDd, setCompDd] = useState(() => loadLotComp().dd ?? "");   // ── LOT_COMP_DD_20260924 ── max DD % of equity peak\n',
   '  const [compDd, setCompDd] = useState(() => loadLotComp().dd ?? "");   // ── LOT_COMP_DD_20260924 ── max DD % of equity peak\n'
   '  const [compDdMode, setCompDdMode] = useState(() => (loadLotComp().ddMode === "proportional" ? "proportional" : "hard"));   // ── LOT_COMP_DD2_20260924 ──\n'
   '  const [compDdRel, setCompDdRel] = useState(() => String(loadLotComp().ddRel ?? "100"));   // ── LOT_COMP_DD2_20260924 ── release % of the drawdown\n'),
  ("replace", "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs, mode: compMode, cpl: compCpl, dd: compDd })); } catch { /* ignore */ }\n"
              "  }, [compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd]);\n",
   "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs, mode: compMode, cpl: compCpl, dd: compDd, ddMode: compDdMode, ddRel: compDdRel })); } catch { /* ignore */ }\n"
   "  }, [compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd, compDdMode, compDdRel]);\n"),
  ("replace", "    const ddExtra = dd > 0 && dd < 100 ? { lot_comp_dd_limit_pct: dd } : {};\n",
   "    // ── LOT_COMP_DD2_20260924 ── shape/release keys only when non-default\n"
   "    const ddRel = Number(compDdRel) || 100;\n"
   "    const ddExtra = dd > 0 && dd < 100 ? {\n"
   "      lot_comp_dd_limit_pct: dd,\n"
   '      ...(compDdMode === "proportional" ? { lot_comp_dd_mode: "proportional" } : {}),\n'
   '      ...(compDdMode !== "proportional" && ddRel > 0 && ddRel < 100 ? { lot_comp_dd_release_pct: ddRel } : {}),\n'
   "    } : {};\n"),
  ("replace", "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd]);\n",
   "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd, compDdMode, compDdRel]);\n"),
  ("replace", '              <Field label="Max lots"><input type="number" min="0" step="1" placeholder="no cap" style={{ ...inputStyle, width: 90 }} value={compMax} onChange={(e) => setCompMax(e.target.value)} title="Ladder cap — the most lots the run will ever trade (maximum risk). Blank = no cap; a cap below the base lots is ignored." /></Field>\n',
   "              {/* ── LOT_COMP_DD2_20260924 ── guard shape + release, shown once a DD % is set */}\n"
   "              {Number(compDd) > 0 && (\n"
   '                <Field label="On drawdown">\n'
   '                  <select style={{ ...inputStyle, width: 160 }} value={compDdMode} onChange={(e) => setCompDdMode(e.target.value === "proportional" ? "proportional" : "hard")}\n'
   '                    title="Hard drop: at the limit lots fall to base and stay there until released. Proportional: lots = rule × (1 − drawdown ÷ limit) every day — cut during the fall, restored as it shrinks, no latch.">\n'
   '                    <option value="hard">Hard drop to base</option>\n'
   '                    <option value="proportional">Proportional cut</option>\n'
   "                  </select>\n"
   "                </Field>\n"
   "              )}\n"
   '              {Number(compDd) > 0 && compDdMode !== "proportional" && (\n'
   '                <Field label="Release when">\n'
   '                  <select style={{ ...inputStyle, width: 170 }} value={compDdRel} onChange={(e) => setCompDdRel(e.target.value)}\n'
   '                    title="When the hard drop ends. New high: equity above its previous peak. Half / quarter: that fraction of the drawdown (peak − trough) regained. The peak is not reset, so a renewed fall to the limit breaches again.">\n'
   '                    <option value="100">New equity high</option>\n'
   '                    <option value="50">Half the DD regained</option>\n'
   '                    <option value="25">Quarter regained</option>\n'
   "                  </select>\n"
   "                </Field>\n"
   "              )}\n"
   '              <Field label="Max lots"><input type="number" min="0" step="1" placeholder="no cap" style={{ ...inputStyle, width: 90 }} value={compMax} onChange={(e) => setCompMax(e.target.value)} title="Ladder cap — the most lots the run will ever trade (maximum risk). Blank = no cap; a cap below the base lots is ignored." /></Field>\n'),
  ("replace", "compPreview.ddLimit ? <>; <b>DD guard</b>: below {compPreview.ddLimit}% of equity peak lots drop to base until a new high</>",
   'compPreview.ddLimit ? <>; <b>DD guard</b>: {compPreview.ddMode === "proportional" ? <>lots × (1 − drawdown ÷ {compPreview.ddLimit}%) every day, floored at base</> : <>below {compPreview.ddLimit}% of equity peak lots drop to base until {compPreview.ddRelease === 100 ? "a new high" : `${compPreview.ddRelease}% of the drawdown is regained`}</>}</>'),
  ("replace", "compPreview.ddLimit ? <>; <b>DD guard</b>: below {compPreview.ddLimit}% of equity peak (₹{fmtL(compPreview.cpl)}L/lot basis) lots drop to base until a new high</>",
   'compPreview.ddLimit ? <>; <b>DD guard</b> (₹{fmtL(compPreview.cpl)}L/lot basis): {compPreview.ddMode === "proportional" ? <>lots × (1 − drawdown ÷ {compPreview.ddLimit}%) every day, floored at base</> : <>below {compPreview.ddLimit}% of equity peak lots drop to base until {compPreview.ddRelease === 100 ? "a new high" : `${compPreview.ddRelease}% of the drawdown is regained`}</>}</>'))

# ═══════════════════════════ frontend: SweepBuilder axes ══════════════════
E(f"{FRONTEND}/backtest/SweepBuilder.jsx",
  ("replace", '    fmt: (v) => (v > 0 && v < 100 ? `DD≤${v}%` : "ddOFF") },\n',
   '    fmt: (v) => (v > 0 && v < 100 ? `DD≤${v}%` : "ddOFF") },\n'
   "  // ── LOT_COMP_DD2_20260924 ── guard shape + release fraction\n"
   '  { key: "lot_comp_dd_mode", label: "Compound DD shape", strategies: LOT_COMP_STRATS,\n'
   '    hint: "HARD, PROP", parse: (tok) => {\n'
   "      const v = tok.trim().toUpperCase();\n"
   '      return ["HARD", "PROP", "PROPORTIONAL"].includes(v) ? { v: v.startsWith("PROP") ? "proportional" : "hard" } : { err: `"${tok}" must be HARD or PROP` };\n'
   "    },\n"
   '    apply: (c, v) => { if (v === "proportional") c.lot_comp_dd_mode = "proportional"; else delete c.lot_comp_dd_mode; },\n'
   '    fmt: (v) => (v === "proportional" ? "dd∝" : "ddHard") },\n'
   '  { key: "lot_comp_dd_rel", label: "Compound DD release %", strategies: LOT_COMP_STRATS,\n'
   '    hint: "100, 50, 25", parse: _num,\n'
   "    apply: (c, v) => { if (v > 0 && v < 100) c.lot_comp_dd_release_pct = v; else delete c.lot_comp_dd_release_pct; },\n"
   '    fmt: (v) => (v > 0 && v < 100 ? `rel${v}%` : "relHigh") },\n'))


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
            die(f"prerequisite {fence} not found in {rel} — apply apply_lot_comp_dd.py first")
    print(f"   prerequisites ok ({len(PREREQ)})")
    dirty = git_dirty(targets)
    if dirty:
        print("   git status shows uncommitted changes on targets:")
        for l in dirty:
            print("     ", l)
        if not ALLOW_DIRTY:
            die("an `M` on a target is a stop sign; if that M is the parent chain not yet committed, re-run with --allow-dirty")
        print("   --allow-dirty: continuing")

    staged = {rel: apply_ops(read(rel), ops, rel) for rel, ops in EDITS.items()}
    for rel, b64 in PAYLOADS_B64.items():
        staged[rel] = base64.b64decode(b64).decode("utf-8")
    tmp = tempfile.mkdtemp(prefix="lot_comp_dd2_gate_")
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
        "app/backtest/engine/test_lot_comp_dd2.py",
        "app/backtest/engine/test_lot_comp_dd.py",
        "app/backtest/engine/test_lot_comp_equity.py",
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
