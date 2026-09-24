#!/usr/bin/env python3
# apply_lot_comp_tax.py — fence LOT_COMP_TAX_20260924 (requires LOT_COMP_DD2_20260924)
#
# Optional tax on realised profits inside compounding:
#   lot_comp_tax_pct             0 / absent = off. Only acts when sizing has an
#                                equity basis (equity mode, or a DD guard with
#                                capital per lot) — flat runs are untouched.
#   lot_comp_tax_fy_start_month  default 4 (Indian FY 1 Apr – 31 Mar).
#   On the first sim day on/after the FY start, the previous FY's realised
#   net (trades closed in it) is taxed; losses carry forward against later
#   FYs; the tax is withdrawn from equity permanently, so the next day's lots
#   come from the reduced equity. The DD guard's peak/trough shift by the
#   same amount (a withdrawal is not a drawdown). The open FY at run end is
#   accrued (reported as due), not withdrawn. Trades / net P&L are unchanged
#   — tax changes SIZING, and is reported alongside.
#   Reporting: persist_run stamps lot_comp_tax_paid / _accrued / _fy on the
#   summary; results page shows a Tax card (from the trades); Compare page
#   gets "Tax withdrawn" and "Net after tax" rows.
#
# Run ONLY this script:
#   cd /Users/anbu/dev/scalp-app && python3 apply_lot_comp_tax.py [--allow-dirty] [--skip-tests]
from __future__ import annotations

import base64
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

FENCE = "LOT_COMP_TAX_20260924"
ROOT = os.path.dirname(os.path.abspath(__file__))
ALLOW_DIRTY = "--allow-dirty" in sys.argv
SKIP_TESTS = "--skip-tests" in sys.argv

BACKEND = "backend/app/backtest"
DESKTOP_BACKEND = "desktop/src-tauri/backend/app/backtest"
FRONTEND = "frontend/src/pages"
DESKTOP_FRONTEND = "desktop/src-tauri/frontend/src/pages"

PREREQ = {
    f"{BACKEND}/engine/lot_compounding.py": "LOT_COMP_DD2_20260924",
    f"{BACKEND}/repo/backtest_repo.py": "LOT_COMP_EQ_20260924",
    f"{BACKEND}/engine/test_lot_comp_runners.py": "LOT_COMP_20260924",
    f"{FRONTEND}/Backtest.jsx": "LOT_COMP_DD2_20260924",
    f"{FRONTEND}/backtest/lotCompounding.js": "LOT_COMP_DD2_20260924",
    f"{FRONTEND}/backtest/RunComparison.jsx": "LOT_COMP_EQ_20260924",
    f"{FRONTEND}/backtest/SweepBuilder.jsx": "LOT_COMP_DD2_20260924",
}
PAYLOADS_B64 = {f"{BACKEND}/engine/test_lot_comp_tax.py": "IyBiYWNrZW5kL2FwcC9iYWNrdGVzdC9lbmdpbmUvdGVzdF9sb3RfY29tcF90YXgucHkKIwojIOKUgOKUgCBMT1RfQ09NUF9UQVhfMjAyNjA5MjQg4pSA4pSAIHN0YW5kYWxvbmU6CiMgICBjZCBiYWNrZW5kICYmIHB5dGhvbjMgYXBwL2JhY2t0ZXN0L2VuZ2luZS90ZXN0X2xvdF9jb21wX3RheC5weQojIE9wdGlvbmFsIHRheCBvbiByZWFsaXNlZCBwcm9maXRzLCBzZXR0bGVkIG9uY2UgcGVyIHRheCB5ZWFyIChJbmRpYW4gRlksCiMgMSBBcHJpbCDigJMgMzEgTWFyY2gsIHN0YXJ0IG1vbnRoIGNvbmZpZ3VyYWJsZSk6IG9uIHRoZSBmaXJzdCBzaW0gZGF5IG9uIG9yCiMgYWZ0ZXIgdGhlIEZZIHN0YXJ0IHRoZSBwcmV2aW91cyBGWSdzIG5ldCBpcyB0YXhlZCBhdCBsb3RfY29tcF90YXhfcGN0LAojIGxvc3NlcyBjYXJyeSBmb3J3YXJkIGFnYWluc3QgbGF0ZXIgRllzLCBhbmQgdGhlIHRheCBpcyB3aXRoZHJhd24gZnJvbSB0aGUKIyBlcXVpdHkgdGhhdCBzaXplcyB0aGUgbmV4dCBkYXkncyBsb3RzLiBUaGUgREQgZ3VhcmQncyBwZWFrL3Ryb3VnaCBhcmUKIyBzaGlmdGVkIGJ5IHRoZSBzYW1lIGFtb3VudCBzbyBhIHdpdGhkcmF3YWwgbmV2ZXIgcmVhZHMgYXMgYSBkcmF3ZG93bi4gVGhlCiMgb3BlbiBGWSBhdCB0aGUgZW5kIG9mIGEgcnVuIGlzIGFjY3J1ZWQgKHJlcG9ydGVkKSwgbm90IHdpdGhkcmF3bi4KZnJvbSBfX2Z1dHVyZV9fIGltcG9ydCBhbm5vdGF0aW9ucwoKaW1wb3J0IG9zCmltcG9ydCBzeXMKZnJvbSBkYXRldGltZSBpbXBvcnQgZGF0ZSwgZGF0ZXRpbWUsIHRpbWVkZWx0YQoKSEVSRSA9IG9zLnBhdGguZGlybmFtZShvcy5wYXRoLmFic3BhdGgoX19maWxlX18pKQpzeXMucGF0aC5pbnNlcnQoMCwgb3MucGF0aC5hYnNwYXRoKG9zLnBhdGguam9pbihIRVJFLCAiLi4iLCAiLi4iLCAiLi4iKSkpCm9zLmVudmlyb24uc2V0ZGVmYXVsdCgiU0NBTFBfTE9UX1NJWkVfT0ZGTElORSIsICIxIikKCmZyb20gYXBwLmJhY2t0ZXN0LmVuZ2luZS5sb3RfY29tcG91bmRpbmcgaW1wb3J0ICggICAjIG5vcWE6IEU0MDIKICAgIExvdENvbXBvdW5kZXIsIGZ5X29mLCBmeV90YXhfc2NoZWR1bGUpCgpGQUlMUyA9IFtdCklTVCA9IDUgKiAzNjAwICsgMzAgKiA2MAoKCmRlZiBjaGVjayhuYW1lLCBvaywgbm90ZT0iIik6CiAgICBwcmludChmIiAgeydQQVNTJyBpZiBvayBlbHNlICdGQUlMJ30gIHtuYW1lfXsoJyAg4oCUICcgKyBzdHIobm90ZSkpIGlmIChub3RlIGFuZCBub3Qgb2spIGVsc2UgJyd9IikKICAgIGlmIG5vdCBvazoKICAgICAgICBGQUlMUy5hcHBlbmQobmFtZSkKCgpkZWYgdHMoZDogZGF0ZSwgaD0xNSkgLT4gaW50OiAgICMgSVNULWFmdGVybm9vbiBlcG9jaCBmb3IgYSBkYXRlCiAgICByZXR1cm4gaW50KChkYXRldGltZShkLnllYXIsIGQubW9udGgsIGQuZGF5LCBoKSAtIGRhdGV0aW1lKDE5NzAsIDEsIDEpKS50b3RhbF9zZWNvbmRzKCkpIC0gSVNUCgoKY2xhc3MgVDoKICAgIGRlZiBfX2luaXRfXyhzZWxmLCBleGl0X2RheSwgbmV0X3BubCk6CiAgICAgICAgc2VsZi5leGl0X3RzLCBzZWxmLm5ldF9wbmwsIHNlbGYuZXhpdF9wcmljZSA9IHRzKGV4aXRfZGF5KSwgbmV0X3BubCwgMS4wCgoKcHJpbnQoIuKUgOKUgCBmeV9vZiDilIDilIAiKQpjaGVjaygiQXByIDEgc3RhcnRzIHRoZSBGWSIsIGZ5X29mKGRhdGUoMjAyMSwgNCwgMSkpID09IGRhdGUoMjAyMSwgNCwgMSkpCmNoZWNrKCJNYXIgMzEgYmVsb25ncyB0byB0aGUgcHJldmlvdXMgRlkiLCBmeV9vZihkYXRlKDIwMjIsIDMsIDMxKSkgPT0gZGF0ZSgyMDIxLCA0LCAxKSkKY2hlY2soIkphbiBpcyBpbiB0aGUgRlkgdGhhdCBzdGFydGVkIGxhc3QgQXByaWwiLCBmeV9vZihkYXRlKDIwMjIsIDEsIDE1KSkgPT0gZGF0ZSgyMDIxLCA0LCAxKSkKY2hlY2soImN1c3RvbSBzdGFydCBtb250aCAzIiwgZnlfb2YoZGF0ZSgyMDI2LCAzLCAyKSwgMykgPT0gZGF0ZSgyMDI2LCAzLCAxKSBhbmQgZnlfb2YoZGF0ZSgyMDI2LCAyLCAyNyksIDMpID09IGRhdGUoMjAyNSwgMywgMSkpCgpwcmludCgi4pSA4pSAIGZ5X3RheF9zY2hlZHVsZSAocHVyZSkg4pSA4pSAIikKdHJhZGVzID0gW1QoZGF0ZSgyMDIwLCA2LCAxKSwgMTAwMDAwKSwgVChkYXRlKDIwMjEsIDIsIDEpLCAtMTYwMDAwKSwgICAgICAgICAgIyBGWTIwLTIxOiDiiJI2MGsg4oaSIGNhcnJ5CiAgICAgICAgICBUKGRhdGUoMjAyMSwgOCwgMSksIDIwMDAwMCksICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAjIEZZMjEtMjI6IDIwMGsg4oiSIDYwayBjYXJyeSA9IDE0MGsg4oaSIDQyayB0YXgKICAgICAgICAgIFQoZGF0ZSgyMDIyLCA1LCAxKSwgNTAwMDApXSAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICMgRlkyMi0yMzogb3BlbiBhdCBydW4gZW5kIOKGkiBhY2NydWVkIDE1awpzY2ggPSBmeV90YXhfc2NoZWR1bGUodHJhZGVzLCAzMCwgcnVuX3RvPWRhdGUoMjAyMiwgOCwgNykpCmNoZWNrKCJ0aHJlZSBGWXMiLCBbZlsiZnkiXSBmb3IgZiBpbiBzY2hbImZ5cyJdXSA9PSBbIjIwMjAtMjEiLCAiMjAyMS0yMiIsICIyMDIyLTIzIl0sIFtmWyJmeSJdIGZvciBmIGluIHNjaFsiZnlzIl1dKQpmMCwgZjEsIGYyID0gc2NoWyJmeXMiXQpjaGVjaygiRlkyMC0yMSBsb3NzIOKGkiBubyB0YXgsIGNhcnJ5IOKIkjYwayIsIGYwWyJuZXQiXSA9PSAtNjAwMDAgYW5kIGYwWyJ0YXgiXSA9PSAwIGFuZCBmMFsiY2Fycnlfb3V0Il0gPT0gLTYwMDAwKQpjaGVjaygiRlkyMS0yMiB0YXhlZCBvbiAyMDBrIOKIkiA2MGsiLCBmMVsidGF4YWJsZSJdID09IDE0MDAwMCBhbmQgZjFbInRheCJdID09IDQyMDAwIGFuZCBmMVsiY2Fycnlfb3V0Il0gPT0gMCkKY2hlY2soIkZZMjItMjMgb3BlbiDihpIgYWNjcnVlZCwgbm90IHBhaWQiLCBmMlsic2V0dGxlZCJdIGlzIEZhbHNlIGFuZCBmMlsidGF4Il0gPT0gMTUwMDApCmNoZWNrKCJ0b3RhbHM6IHBhaWQgNDJrLCBhY2NydWVkIDE1ayIsIHNjaFsicGFpZCJdID09IDQyMDAwIGFuZCBzY2hbImFjY3J1ZWQiXSA9PSAxNTAwMCkKY2hlY2soInJhdGUgMCDihpIgZW1wdHkgc2NoZWR1bGUiLCBmeV90YXhfc2NoZWR1bGUodHJhZGVzLCAwKSA9PSB7Im9uIjogRmFsc2V9KQpjaGVjaygib3BlbiB0cmFkZXMgaWdub3JlZCIsIGZ5X3RheF9zY2hlZHVsZSh0cmFkZXMgKyBbdHlwZSgiTyIsICgpLCB7ImV4aXRfdHMiOiBOb25lLCAiZXhpdF9wcmljZSI6IE5vbmUsICJuZXRfcG5sIjogOWU5fSkoKV0sIDMwLCBydW5fdG89ZGF0ZSgyMDIyLCA4LCA3KSlbInBhaWQiXSA9PSA0MjAwMCkKCnByaW50KCLilIDilIAgbW9kdWxlOiBlcXVpdHkgbW9kZSwg4oK5MUwvbG90LCBiYXNlIDEwLCB0YXggMzAlIOKUgOKUgCIpCmNmZyA9IHsibG90X2NvbXBfbW9kZSI6ICJlcXVpdHkiLCAibG90X2NvbXBfY2FwaXRhbF9wZXJfbG90IjogMTAwMDAwLCAibG90X2NvbXBfdGF4X3BjdCI6IDMwfQpjID0gTG90Q29tcG91bmRlcihjZmcsIGRhdGUoMjAyMSwgMSwgNCksIDEwKQpjaGVjaygidGF4IG9uIiwgYy50YXhfb24gYW5kIGMudGF4X3JhdGUgPT0gMC4zMCBhbmQgYy50YXhfZnlfbW9udGggPT0gNCkKdHIgPSBbXQpjaGVjaygiSmFuIDQgMjAyMTogMTAgbG90cyIsIGMuYmVnaW5fZGF5KGRhdGUoMjAyMSwgMSwgNCksIHRyKSA9PSAxMCkKdHIuYXBwZW5kKFQoZGF0ZSgyMDIxLCAyLCAxKSwgNTAwMDAwKSkgICAgICAgICAgICAgICAgICAgICAjIEZZMjAtMjEgcHJvZml0IDVMIOKGkiBlcXVpdHkgMTVMCmNoZWNrKCJGZWI6IDE1IGxvdHMiLCBjLmJlZ2luX2RheShkYXRlKDIwMjEsIDIsIDIpLCB0cikgPT0gMTUpCmNoZWNrKCJNYXIgMzE6IHN0aWxsIDE1IChGWSBub3Qgc2V0dGxlZCB5ZXQpIiwgYy5iZWdpbl9kYXkoZGF0ZSgyMDIxLCAzLCAzMSksIHRyKSA9PSAxNSkKY2hlY2soIkFwciAxOiBGWTIwLTIxIHNldHRsZWQg4oaSIHRheCAxLjVMIOKGkiBlcXVpdHkgMTMuNUwg4oaSIDEzIGxvdHMiLCBjLmJlZ2luX2RheShkYXRlKDIwMjEsIDQsIDEpLCB0cikgPT0gMTMgYW5kIGMudGF4X3BhaWQgPT0gMTUwMDAwKQpjaGVjaygiQXByIDI6IG5vIGRvdWJsZSBzZXR0bGVtZW50IiwgYy5iZWdpbl9kYXkoZGF0ZSgyMDIxLCA0LCAyKSwgdHIpID09IDEzIGFuZCBjLnRheF9wYWlkID09IDE1MDAwMCkKdHIuYXBwZW5kKFQoZGF0ZSgyMDIxLCA5LCAxKSwgLTQwMDAwMCkpICAgICAgICAgICAgICAgICAgICAjIEZZMjEtMjIgbG9zcyA0TCDihpIgZXF1aXR5IDkuNUwKY2hlY2soIlNlcDogOSBsb3RzIiwgYy5iZWdpbl9kYXkoZGF0ZSgyMDIxLCA5LCAyKSwgdHIpID09IDkpCmNoZWNrKCJBcHIgMjAyMjogbG9zcyBGWSDihpIgbm8gdGF4LCBjYXJyeSDiiJI0TCIsIGMuYmVnaW5fZGF5KGRhdGUoMjAyMiwgNCwgMSksIHRyKSA9PSA5IGFuZCBjLnRheF9wYWlkID09IDE1MDAwMCBhbmQgYy50YXhfY2FycnkgPT0gLTQwMDAwMCkKdHIuYXBwZW5kKFQoZGF0ZSgyMDIyLCA2LCAxKSwgNjAwMDAwKSkgICAgICAgICAgICAgICAgICAgICAjIEZZMjItMjMgcHJvZml0IDZMOiB0YXhhYmxlIDJMCmNoZWNrKCJKdW4gMjAyMjogMTUgbG90cyAoZXF1aXR5IDE1LjVMKSIsIGMuYmVnaW5fZGF5KGRhdGUoMjAyMiwgNiwgMiksIHRyKSA9PSAxNSkKY2hlY2soIkFwciAyMDIzOiB0YXggMzAlIG9mICg2TCDiiJIgNEwpID0gNjBrIOKGkiBlcXVpdHkgMTQuOUwg4oaSIDE0IGxvdHMiLCBjLmJlZ2luX2RheShkYXRlKDIwMjMsIDQsIDMpLCB0cikgPT0gMTQgYW5kIGMudGF4X3BhaWQgPT0gMjEwMDAwIGFuZCBjLnRheF9jYXJyeSA9PSAwKQp0ci5hcHBlbmQoVChkYXRlKDIwMjMsIDUsIDEpLCAxMDAwMDApKSAgICAgICAgICAgICAgICAgICAgICMgb3BlbiBGWTIzLTI0OiArMUwg4oaSIGFjY3J1ZWQgMzBrCmMuYmVnaW5fZGF5KGRhdGUoMjAyMywgNSwgMiksIHRyKQpkZyA9IGMuZGlhZygpWyJ0YXgiXQpjaGVjaygiZGlhZzogcGFpZCAyLjFMLCBhY2NydWVkIDMwayBmb3IgdGhlIG9wZW4gRlkiLCBkZ1sicGFpZCJdID09IDIxMDAwMCBhbmQgZGdbImFjY3J1ZWQiXSA9PSAzMDAwMCBhbmQgZGdbIm9wZW5fZnkiXSA9PSAiMjAyMy0yNCIpCmNoZWNrKCJkaWFnOiAzIHNldHRsZWQgRllzIiwgW2ZbImZ5Il0gZm9yIGYgaW4gZGdbImZ5cyJdXSA9PSBbIjIwMjAtMjEiLCAiMjAyMS0yMiIsICIyMDIyLTIzIl0pCmNoZWNrKCJkZXNjcmliZSBtZW50aW9ucyB0YXgiLCAidGF4PTMwJSIgaW4gYy5kZXNjcmliZSgpKQoKcHJpbnQoIuKUgOKUgCBza2lwcGVkIHNlc3Npb25zOiBhIGdhcCBvdmVyIDEgQXByaWwgc3RpbGwgc2V0dGxlcyBvbmNlIOKUgOKUgCIpCmcgPSBMb3RDb21wb3VuZGVyKGNmZywgZGF0ZSgyMDIxLCAxLCA0KSwgMTApCnRyID0gW1QoZGF0ZSgyMDIxLCAzLCAxKSwgNTAwMDAwKV0KZy5iZWdpbl9kYXkoZGF0ZSgyMDIxLCAzLCAyKSwgdHIpCmNoZWNrKCJmaXJzdCBzZXNzaW9uIGFmdGVyIHRoZSBnYXAgc2V0dGxlcyIsIGcuYmVnaW5fZGF5KGRhdGUoMjAyMSwgNCwgMTUpLCB0cikgPT0gMTMgYW5kIGcudGF4X3BhaWQgPT0gMTUwMDAwKQpnMiA9IExvdENvbXBvdW5kZXIoY2ZnLCBkYXRlKDIwMjEsIDEsIDQpLCAxMCkKdHIyID0gW1QoZGF0ZSgyMDIxLCAzLCAxKSwgNTAwMDAwKV0KZzIuYmVnaW5fZGF5KGRhdGUoMjAyMSwgMywgMiksIHRyMikKY2hlY2soInR3byBGWSBib3VuZGFyaWVzIGluIG9uZSBnYXAg4oaSIGJvdGggc2V0dGxlZCAoc2Vjb25kIHdpdGggbm8gdHJhZGVzKSIsIGcyLmJlZ2luX2RheShkYXRlKDIwMjIsIDQsIDE1KSwgdHIyKSA9PSAxMyBhbmQgZzIudGF4X3BhaWQgPT0gMTUwMDAwIGFuZCBsZW4oZzIuZGlhZygpWyJ0YXgiXVsiZnlzIl0pID09IDIpCgpwcmludCgi4pSA4pSAIEREIGd1YXJkIGlzIG5vdCBmb29sZWQgYnkgdGhlIHdpdGhkcmF3YWwg4pSA4pSAIikKZCA9IExvdENvbXBvdW5kZXIoeyoqY2ZnLCAibG90X2NvbXBfZGRfbGltaXRfcGN0IjogMTB9LCBkYXRlKDIwMjEsIDEsIDQpLCAxMCkKdHIgPSBbVChkYXRlKDIwMjEsIDIsIDEpLCA1MDAwMDApXQpkLmJlZ2luX2RheShkYXRlKDIwMjEsIDIsIDIpLCB0cikgICAgICAgICAgICAgICAgICAgICAgICAgICMgZXF1aXR5IDE1TCA9IHBlYWsKY2hlY2soInBlYWsgMTVMIiwgZC5kZF9wZWFrID09IDE1MDAwMDApCmQuYmVnaW5fZGF5KGRhdGUoMjAyMSwgNCwgMSksIHRyKSAgICAgICAgICAgICAgICAgICAgICAgICAgIyB0YXggMS41TCB3aXRoZHJhd24gKDEwJSBvZiBlcXVpdHkhKQpjaGVjaygiQXByIDE6IHBlYWsgc2hpZnRlZCB3aXRoIHRoZSB3aXRoZHJhd2FsLCBubyBicmVhY2giLCBkLmRkX3BlYWsgPT0gMTM1MDAwMCBhbmQgbm90IGQuZGRfYWN0aXZlIGFuZCBkLmxvdHMoZGF0ZSgyMDIxLCA0LCAxKSkgPT0gMTMpCnRyLmFwcGVuZChUKGRhdGUoMjAyMSwgNCwgNSksIC0xMDAwMDApKSAgICAgICAgICAgICAgICAgICAgIyAxMi41TDogZGQgdnMgMTMuNUwgcGVhayA9IDcuNCUg4oaSIG5vIGJyZWFjaApjaGVjaygiYSBsYXRlciA3JSBkaXAgaXMgbWVhc3VyZWQgZnJvbSB0aGUgc2hpZnRlZCBwZWFrIiwgZC5iZWdpbl9kYXkoZGF0ZSgyMDIxLCA0LCA2KSwgdHIpID09IDEyIGFuZCBub3QgZC5kZF9hY3RpdmUpCgpwcmludCgi4pSA4pSAIHRheCBvZmYgLyBpbmVydCBjYXNlcyDilIDilIAiKQpjaGVjaygidGF4IDAg4oaSIG9mZiIsIG5vdCBMb3RDb21wb3VuZGVyKHsqKmNmZywgImxvdF9jb21wX3RheF9wY3QiOiAwfSwgZGF0ZSgyMDIxLCAxLCA0KSwgMTApLnRheF9vbikKY2hlY2soInRheCB3aXRob3V0IGFuIGVxdWl0eSBiYXNpcyAoY2FsZW5kYXIsIG5vIGNwbCwgbm8gZ3VhcmQpIOKGkiBpbmVydCIsCiAgICAgIG5vdCBMb3RDb21wb3VuZGVyKHsibG90X2NvbXBfc3RlcF9tb250aHMiOiAzLCAibG90X2NvbXBfYWRkX2xvdHMiOiAxLCAibG90X2NvbXBfdGF4X3BjdCI6IDMwfSwgZGF0ZSgyMDIxLCAxLCA0KSwgMTApLnRheF9vbikKayA9IExvdENvbXBvdW5kZXIoeyJsb3RfY29tcF9zdGVwX21vbnRocyI6IDMsICJsb3RfY29tcF9hZGRfbG90cyI6IDEsICJsb3RfY29tcF9jYXBpdGFsX3Blcl9sb3QiOiAxMDAwMDAsCiAgICAgICAgICAgICAgICAgICAibG90X2NvbXBfZGRfbGltaXRfcGN0IjogMTAsICJsb3RfY29tcF90YXhfcGN0IjogMzB9LCBkYXRlKDIwMjEsIDEsIDQpLCAxMCkKY2hlY2soImNhbGVuZGFyICsgZ3VhcmQgKyB0YXgg4oaSIHRheCBvbiAoZ3VhcmQgZXF1aXR5IGJhc2lzKSIsIGsudGF4X29uKQp0ciA9IFtUKGRhdGUoMjAyMSwgMiwgMSksIDUwMDAwMCldCmsuYmVnaW5fZGF5KGRhdGUoMjAyMSwgMiwgMiksIHRyKTsgay5iZWdpbl9kYXkoZGF0ZSgyMDIxLCA0LCA1KSwgdHIpCmNoZWNrKCJjYWxlbmRhciBtb2RlOiB3aXRoZHJhd2FsIHNoaWZ0cyBndWFyZCBlcXVpdHksIGxhZGRlciB1bmFmZmVjdGVkICh0aWVyIDEgPSAxMSkiLCBrLnRheF9wYWlkID09IDE1MDAwMCBhbmQgay5sb3RzKGRhdGUoMjAyMSwgNCwgNSkpID09IDExKQpvZmYgPSBMb3RDb21wb3VuZGVyKHsibG90X2NvbXBfbW9kZSI6ICJlcXVpdHkiLCAibG90X2NvbXBfY2FwaXRhbF9wZXJfbG90IjogMTAwMDAwfSwgZGF0ZSgyMDIxLCAxLCA0KSwgMTApCnRyID0gW1QoZGF0ZSgyMDIxLCAyLCAxKSwgNTAwMDAwKV0Kb2ZmLmJlZ2luX2RheShkYXRlKDIwMjEsIDIsIDIpLCB0cikKY2hlY2soIm5vIHRheCBrZXk6IEFwciAxIGNoYW5nZXMgbm90aGluZyIsIG9mZi5iZWdpbl9kYXkoZGF0ZSgyMDIxLCA0LCAxKSwgdHIpID09IDE1IGFuZCBvZmYuZGlhZygpLmdldCgidGF4IikgPT0geyJvbiI6IEZhbHNlfSkKCiMg4pSA4pSAIHJ1bm5lcnM6IEZZIHN0YXJ0IG1vbnRoIDMgZm9yY2VzIGEgc2V0dGxlbWVudCBpbnNpZGUgdGhlIHN5bnRoZXRpYyBjb3JwdXMg4pSA4pSACnByaW50KCLilIDilIAgcnVubmVycyDilIDilIAiKQp0cnk6CiAgICBzcmMgPSBvcGVuKG9zLnBhdGguam9pbihIRVJFLCAidGVzdF9sb3RfY29tcF9ydW5uZXJzLnB5IiksIGVuY29kaW5nPSJ1dGYtOCIpLnJlYWQoKQogICAgaGVhZCA9IHNyYy5zcGxpdCgiUlVOTkVSUyA9IFsiKVswXS5yZXBsYWNlKCJ3aGlsZSBsZW4oYWxsX2RheXMpIDwgNDU6IiwgIndoaWxlIGxlbihhbGxfZGF5cykgPCAzMDoiKQogICAgbnMgPSB7Il9fZmlsZV9fIjogb3MucGF0aC5qb2luKEhFUkUsICJ0ZXN0X2xvdF9jb21wX3J1bm5lcnMucHkiKSwgIl9fbmFtZV9fIjogIl9sY3JfaGVhZCJ9CiAgICBleGVjKGNvbXBpbGUoaGVhZCwgImxjcl9oZWFkIiwgImV4ZWMiKSwgbnMpCiAgICBydW5fb25lLCBfZywgZGF5X29mX3RzLCBSVU5fRlJPTSA9IG5zWyJydW5fb25lIl0sIG5zWyJfZyJdLCBuc1siZGF5X29mX3RzIl0sIG5zWyJSVU5fRlJPTSJdCiAgICBMT1QgPSA2NQogICAgVFggPSB7ImxvdF9jb21wX21vZGUiOiAiZXF1aXR5IiwgImxvdF9jb21wX2NhcGl0YWxfcGVyX2xvdCI6IDIwMDAwLCAibG90X2NvbXBfbWF4X2xvdHMiOiAzMCwKICAgICAgICAgICJsb3RfY29tcF90YXhfcGN0IjogMzAsICJsb3RfY29tcF90YXhfZnlfc3RhcnRfbW9udGgiOiAzfQogICAgZm9yIG5hbWUgaW4gWyJTVEZDX1YxIiwgIlRTR19WMSIsICJUTUFfVjIiLCAiSEFfVjEiXToKICAgICAgICB0cnk6CiAgICAgICAgICAgIHIgPSBydW5fb25lKG5hbWUsIFRYKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICAgICAgY2hlY2soZiJ7bmFtZX06IHJ1biBleGVjdXRlcyIsIEZhbHNlLCByZXByKGUpKQogICAgICAgICAgICBjb250aW51ZQogICAgICAgIHRyID0gclsidHJhZGVzIl0KICAgICAgICByZWYgPSBMb3RDb21wb3VuZGVyKFRYLCBSVU5fRlJPTSwgMTApCiAgICAgICAgY2xvc2VkID0gW3QgZm9yIHQgaW4gdHIgaWYgX2codCwgImV4aXRfdHMiKSBpcyBub3QgTm9uZSBhbmQgX2codCwgImV4aXRfcHJpY2UiKSBpcyBub3QgTm9uZV0KICAgICAgICBleHAgPSB7fQogICAgICAgIGZvciBkZF8gaW4gW3ggZm9yIHggaW4gbnNbImFsbF9kYXlzIl0gaWYgeCA+PSBSVU5fRlJPTV06CiAgICAgICAgICAgIGV4cFtkZF9dID0gcmVmLmJlZ2luX2RheShkZF8sIFt0IGZvciB0IGluIGNsb3NlZCBpZiBkYXlfb2ZfdHMoX2codCwgImV4aXRfdHMiKSkgPCBkZF9dKQogICAgICAgIGJhZCA9IFsoZGF5X29mX3RzKF9nKHQsICJlbnRyeV90cyIpKSwgaW50KF9nKHQsICJxdHkiKSksIGV4cFtkYXlfb2ZfdHMoX2codCwgImVudHJ5X3RzIikpXSAqIExPVCkKICAgICAgICAgICAgICAgZm9yIHQgaW4gdHIgaWYgaW50KF9nKHQsICJxdHkiKSBvciAwKSAhPSBleHBbZGF5X29mX3RzKF9nKHQsICJlbnRyeV90cyIpKV0gKiBMT1RdCiAgICAgICAgdHggPSByZWYuZGlhZygpWyJ0YXgiXQogICAgICAgIHByZSA9IGZ5X3RheF9zY2hlZHVsZShjbG9zZWQsIDMwLCBmeV9zdGFydF9tb250aD0zKVsiZnlzIl1bMF1bIm5ldCJdCiAgICAgICAgY2hlY2soZiJ7bmFtZX06IGV2ZXJ5IHRyYWRlIHNpemVkIHBlciB0aGUgdGF4ZWQgcnVsZSAoe2xlbih0cil9IHRyYWRlczsgRlkgc2V0dGxlZCB0YXgg4oK5e3R4WydwYWlkJ106LC4wZn0gb24g4oK5e3ByZTosLjBmfSkiLAogICAgICAgICAgICAgIHRyIGFuZCBub3QgYmFkLCBiYWRbOjNdKQogICAgICAgIGNoZWNrKGYie25hbWV9OiBzZXR0bGVtZW50IGhhcHBlbmVkIChvbmUgRlkgY2xvc2VkLCBmaXJzdCBzZXNzaW9uIOKJpSBNYXIgMSkiLAogICAgICAgICAgICAgIGxlbih0eFsiZnlzIl0pID09IDEgYW5kIHR4WyJmeXMiXVswXVsic2V0dGxlZCJdIGFuZCB0eFsiZnlzIl1bMF1bInNldHRsZWRfb24iXSA+PSAiMjAyNi0wMy0wMSIpCiAgICAgICAgY2hlY2soZiJ7bmFtZX06IHRheCA9IDMwJSBvZiB0aGUgRlkgbmV0IHdoZW4gcG9zaXRpdmUsIDAgd2hlbiBub3QiLAogICAgICAgICAgICAgICh0eFsiZnlzIl1bMF1bIm5ldCJdIDw9IDAgYW5kIHR4WyJwYWlkIl0gPT0gMCkgb3IgYWJzKHR4WyJwYWlkIl0gLSAwLjMwICogdHhbImZ5cyJdWzBdWyJuZXQiXSkgPCAxZS02KQogICAgICAgICMgdGhlIG5vLXRheCBydW4gbXVzdCBkaWZmZXIgb25seSBhZnRlciBzZXR0bGVtZW50IChzaXppbmcpLCBuZXZlciBiZWZvcmUKICAgICAgICByMCA9IHJ1bl9vbmUobmFtZSwge2s6IHYgZm9yIGssIHYgaW4gVFguaXRlbXMoKSBpZiBub3Qgay5zdGFydHN3aXRoKCJsb3RfY29tcF90YXgiKX0pCiAgICAgICAgYmVmb3JlID0gbGFtYmRhIHJyOiBbaW50KF9nKHQsICJxdHkiKSkgZm9yIHQgaW4gcnJbInRyYWRlcyJdIGlmIGRheV9vZl90cyhfZyh0LCAiZW50cnlfdHMiKSkgPCBkYXRlKDIwMjYsIDMsIDEpXSAgICMgbm9xYTogRTczMQogICAgICAgIGNoZWNrKGYie25hbWV9OiBpZGVudGljYWwgc2l6aW5nIGJlZm9yZSB0aGUgRlkgYm91bmRhcnkiLCBiZWZvcmUocikgPT0gYmVmb3JlKHIwKSkKZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgaW1wb3J0IHRyYWNlYmFjawogICAgdHJhY2ViYWNrLnByaW50X2V4YygpCiAgICBjaGVjaygicnVubmVyIGNoZWNrcyBleGVjdXRlIiwgRmFsc2UsIHJlcHIoZSkpCgpwcmludCgpCmlmIEZBSUxTOgogICAgcHJpbnQoZiJGQUlMRUQge2xlbihGQUlMUyl9OiIpCiAgICBmb3IgeCBpbiBGQUlMUzoKICAgICAgICBwcmludCgiICAtIiwgeCkKICAgIHN5cy5leGl0KDEpCnByaW50KCJBTEwgTE9UX0NPTVBfVEFYIENIRUNLUyBQQVNTRUQiKQo="}

EDITS = {}


def E(path, *ops):
    EDITS.setdefault(path, []).extend(ops)


# ═══════════════════════════ backend: shared module ═══════════════════════
E(f"{BACKEND}/engine/lot_compounding.py",
  ("replace", "from datetime import date\n", "from datetime import date, datetime, timedelta\n"),
  ("replace", 'KEY_DD_RELEASE = "lot_comp_dd_release_pct"  # hard mode: % of the drawdown regained to release (100 = new high)\n',
   'KEY_DD_RELEASE = "lot_comp_dd_release_pct"  # hard mode: % of the drawdown regained to release (100 = new high)\n'
   "# ── LOT_COMP_TAX_20260924 ── tax on realised profits, settled per tax year\n"
   'KEY_TAX = "lot_comp_tax_pct"                  # 0 / absent = off\n'
   'KEY_TAX_FY = "lot_comp_tax_fy_start_month"    # default 4 (Indian FY: 1 Apr – 31 Mar)\n'
   "_IST = 5 * 3600 + 30 * 60\n"
   "\n\n"
   "def fy_of(d: date, start_month: int = 4) -> date:\n"
   '    """Start date of the tax year containing `d`."""\n'
   "    m = int(start_month or 4)\n"
   "    return date(d.year if d.month >= m else d.year - 1, m, 1)\n"
   "\n\n"
   "def fy_next(start: date) -> date:\n"
   "    return date(start.year + 1, start.month, 1)\n"
   "\n\n"
   "def fy_label(start: date) -> str:\n"
   '    return f"{start.year}-{(start.year + 1) % 100:02d}"\n'
   "\n\n"
   "def _trade_exit_day(t) -> Optional[date]:\n"
   '    ts = _tget(t, "exit_ts")\n'
   "    if ts is None:\n"
   "        return None\n"
   "    try:\n"
   "        return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + _IST)).date()\n"
   "    except (TypeError, ValueError, OverflowError):\n"
   "        return None\n"
   "\n\n"
   "def lot_comp_tax_active(cfg) -> bool:\n"
   '    """Tax needs an equity basis: equity mode, or a DD guard with capital per lot."""\n'
   "    if not isinstance(cfg, dict):\n"
   "        return False\n"
   "    try:\n"
   "        if float(cfg.get(KEY_TAX) or 0) <= 0 or float(cfg.get(KEY_CPL) or 0) <= 0:\n"
   "            return False\n"
   "        if lot_comp_is_equity(cfg):\n"
   "            return True\n"
   "        dd = float(cfg.get(KEY_DD) or 0)\n"
   "        return 0 < dd < 100 and parse_lot_comp(cfg) != (0, 0)\n"
   "    except (TypeError, ValueError):\n"
   "        return False\n"
   "\n\n"
   "def fy_tax_schedule(trades, rate_pct, fy_start_month: int = 4, run_to=None) -> dict:\n"
   '    """Pure FY-by-FY tax schedule from a trade list (any runner shape).\n'
   "    Each FY: net of trades CLOSED in it, carry-in of prior losses, taxable,\n"
   "    tax, carry-out. A FY is `settled` when a session after its end exists\n"
   "    in the run (run_to > fy end); the last, open FY is accrued. Used by\n"
   '    persist_run for the summary stamp and mirrored in the UI."""\n'
   "    try:\n"
   "        rate = float(rate_pct or 0) / 100.0\n"
   "    except (TypeError, ValueError):\n"
   "        rate = 0.0\n"
   "    if rate <= 0:\n"
   '        return {"on": False}\n'
   "    m = int(fy_start_month or 4)\n"
   "    buckets: Dict[date, float] = {}\n"
   "    for t in (trades or ()):\n"
   "        v = _trade_net(t)\n"
   "        xd = _trade_exit_day(t)\n"
   "        if v is None or xd is None:\n"
   "            continue\n"
   "        k = fy_of(xd, m)\n"
   "        buckets[k] = buckets.get(k, 0.0) + v\n"
   "    if not buckets:\n"
   '        return {"on": True, "rate_pct": rate * 100, "fy_start_month": m, "fys": [], "paid": 0.0, "accrued": 0.0}\n'
   "    rt = _as_date(run_to)\n"
   "    fys, carry, paid, accrued = [], 0.0, 0.0, 0.0\n"
   "    start, last = min(buckets), max(buckets)\n"
   "    while start <= last:\n"
   "        nxt = fy_next(start)\n"
   "        net = buckets.get(start, 0.0)\n"
   "        taxable = net + carry\n"
   "        tax = rate * taxable if taxable > 0 else 0.0\n"
   "        carry_out = 0.0 if taxable > 0 else taxable\n"
   "        settled = (rt > (nxt - timedelta(days=1))) if rt is not None else (start != last)\n"
   '        fys.append({"fy": fy_label(start), "start": start.isoformat(), "end": (nxt - timedelta(days=1)).isoformat(),\n'
   '                    "net": net, "carry_in": carry, "taxable": taxable, "tax": tax, "carry_out": carry_out,\n'
   '                    "settled": settled})\n'
   "        if settled:\n"
   "            paid += tax\n"
   "        else:\n"
   "            accrued += tax\n"
   "        carry = carry_out\n"
   "        start = nxt\n"
   '    return {"on": True, "rate_pct": rate * 100, "fy_start_month": m, "fys": fys, "paid": paid, "accrued": accrued}\n'),
  ("replace", '                 "dd_mode", "dd_release", "dd_trough", "_dd_factor", "_dd_breached")   # ── LOT_COMP_DD2_20260924 ──\n',
   '                 "dd_mode", "dd_release", "dd_trough", "_dd_factor", "_dd_breached",   # ── LOT_COMP_DD2_20260924 ──\n'
   '                 "tax_rate", "tax_fy_month", "tax_on", "tax_paid", "tax_carry", "_tax_fy", "_tax_fys", "_tax_trades")   # ── LOT_COMP_TAX_20260924 ──\n'),
  ("replace", "        self._dd_breached = False\n        self._tier_cache: Dict[date, int] = {}\n",
   "        self._dd_breached = False\n"
   "        # ── LOT_COMP_TAX_20260924 ── FY tax withdrawal (needs an equity basis)\n"
   "        try:\n"
   "            _tx = float((cfg or {}).get(KEY_TAX) or 0) if isinstance(cfg, dict) else 0.0\n"
   "        except (TypeError, ValueError):\n"
   "            _tx = 0.0\n"
   "        self.tax_rate = _tx / 100.0 if 0 < _tx < 100 else 0.0\n"
   "        try:\n"
   "            _fm = int((cfg or {}).get(KEY_TAX_FY) or 4) if isinstance(cfg, dict) else 4\n"
   "        except (TypeError, ValueError):\n"
   "            _fm = 4\n"
   "        self.tax_fy_month = _fm if 1 <= _fm <= 12 else 4\n"
   '        self.tax_on = bool(self.on and self.tax_rate > 0 and self.cpl > 0\n'
   '                           and (self.mode == "equity" or self.dd_guard_on))\n'
   "        self.tax_paid = 0.0\n"
   "        self.tax_carry = 0.0\n"
   "        self._tax_fy: Optional[date] = None\n"
   "        self._tax_fys: List[dict] = []\n"
   "        self._tax_trades = None\n"
   "        self._tier_cache: Dict[date, int] = {}\n"),
  ("replace", "        self._eq_day = d\n        self._eq_equity = float(self.base) * self.cpl + net\n",
   "        self._eq_day = d\n"
   "        if self.tax_on:   # ── LOT_COMP_TAX_20260924 ── settle any FY that ended before today\n"
   "            self._tax_settle(d, trades)\n"
   "        self._eq_equity = float(self.base) * self.cpl + net - self.tax_paid\n"),
  ("replace", "            self._eq_lots = self.equity_lots(net)\n",
   "            self._eq_lots = self.equity_lots(net - self.tax_paid)   # ── LOT_COMP_TAX_20260924 ── after withdrawals\n"),
  ("replace", "    # ── reporting ─────────────────────────────────────────────────────\n    def peak_lots(self, date_to) -> int:\n",
   "    # ── LOT_COMP_TAX_20260924 ── FY settlement ─────────────────────────\n"
   "    def _tax_settle(self, d: date, trades) -> None:\n"
   "        self._tax_trades = trades\n"
   "        fy = fy_of(d, self.tax_fy_month)\n"
   "        if self._tax_fy is None:\n"
   "            self._tax_fy = fy\n"
   "            return\n"
   "        while self._tax_fy < fy:            # one settlement per FY, gaps included\n"
   "            start, nxt = self._tax_fy, fy_next(self._tax_fy)\n"
   "            net = 0.0\n"
   "            for t in (trades or ()):\n"
   "                v = _trade_net(t)\n"
   "                xd = _trade_exit_day(t)\n"
   "                if v is not None and xd is not None and start <= xd < nxt:\n"
   "                    net += v\n"
   "            taxable = net + self.tax_carry\n"
   "            tax = self.tax_rate * taxable if taxable > 0 else 0.0\n"
   "            carry_out = 0.0 if taxable > 0 else taxable\n"
   '            self._tax_fys.append({"fy": fy_label(start), "start": start.isoformat(),\n'
   '                                  "end": (nxt - timedelta(days=1)).isoformat(), "net": net,\n'
   '                                  "carry_in": self.tax_carry, "taxable": taxable, "tax": tax,\n'
   '                                  "carry_out": carry_out, "settled": True, "settled_on": d.isoformat()})\n'
   "            if tax > 0:\n"
   "                self.tax_paid += tax\n"
   "                self.dd_peak -= tax          # a withdrawal is not a drawdown\n"
   "                self.dd_trough -= tax\n"
   "            self.tax_carry = carry_out\n"
   "            self._tax_fy = nxt\n"
   "\n"
   "    def _tax_diag(self) -> dict:\n"
   "        if not self.tax_on:\n"
   '            return {"on": False}\n'
   "        accrued, open_fy, open_net = 0.0, None, 0.0\n"
   "        if self._tax_fy is not None:\n"
   "            start, nxt = self._tax_fy, fy_next(self._tax_fy)\n"
   "            for t in (self._tax_trades or ()):\n"
   "                v = _trade_net(t)\n"
   "                xd = _trade_exit_day(t)\n"
   "                if v is not None and xd is not None and start <= xd < nxt:\n"
   "                    open_net += v\n"
   "            taxable = open_net + self.tax_carry\n"
   "            accrued = self.tax_rate * taxable if taxable > 0 else 0.0\n"
   "            open_fy = fy_label(start)\n"
   '        return {"on": True, "rate_pct": round(self.tax_rate * 100, 4), "fy_start_month": self.tax_fy_month,\n'
   '                "paid": self.tax_paid, "carry": self.tax_carry, "fys": list(self._tax_fys),\n'
   '                "open_fy": open_fy, "open_net": open_net, "accrued": accrued}\n'
   "\n"
   "    # ── reporting ─────────────────────────────────────────────────────\n"
   "    def peak_lots(self, date_to) -> int:\n"),
  ("replace", '                "dd_guard": self._dd_diag(),   # ── LOT_COMP_DD_20260924 ──\n            }\n        return {\n',
   '                "dd_guard": self._dd_diag(),   # ── LOT_COMP_DD_20260924 ──\n'
   '                "tax": self._tax_diag(),   # ── LOT_COMP_TAX_20260924 ──\n'
   "            }\n        return {\n"),
  ("replace", '            "dd_guard": self._dd_diag(),   # ── LOT_COMP_DD_20260924 ──\n            "ladder": [{"from": d.isoformat(), "lots": n} for d, n in self._eq_ladder],\n        }\n',
   '            "dd_guard": self._dd_diag(),   # ── LOT_COMP_DD_20260924 ──\n'
   '            "tax": self._tax_diag(),   # ── LOT_COMP_TAX_20260924 ──\n'
   '            "ladder": [{"from": d.isoformat(), "lots": n} for d, n in self._eq_ladder],\n        }\n'),
  ("replace", '            return (f"lot_comp equity cpl={self.cpl:.0f} base={self.base}"\n',
   '            return (f"lot_comp equity cpl={self.cpl:.0f} base={self.base}"\n'
   '                    f"{(\' tax=\' + format(self.tax_rate * 100, \'g\') + \'%\') if self.tax_on else \'\'}"   # ── LOT_COMP_TAX_20260924 ──\n'),
  ("replace", '                f"{(\' dd=\' + format(self.dd_limit * 100, \'g\') + \'%\') if self.dd_guard_on else \'\'}"   # ── LOT_COMP_DD_20260924 ──\n                f"{\'\' if self.scale_rs_on else \' rs=fixed\'}")   # ── LOT_COMP_MAX_20260924 ──\n',
   '                f"{(\' dd=\' + format(self.dd_limit * 100, \'g\') + \'%\') if self.dd_guard_on else \'\'}"   # ── LOT_COMP_DD_20260924 ──\n'
   '                f"{(\' tax=\' + format(self.tax_rate * 100, \'g\') + \'%\') if self.tax_on else \'\'}"   # ── LOT_COMP_TAX_20260924 ──\n'
   '                f"{\'\' if self.scale_rs_on else \' rs=fixed\'}")   # ── LOT_COMP_MAX_20260924 ──\n'))

# persist_run: stamp the FY tax schedule on the summary
E(f"{BACKEND}/repo/backtest_repo.py",
  ("replace", "        if _qs and isinstance(s, dict):\n            s = dict(s, qty_min=min(_qs), qty_max=max(_qs))\n    except Exception:\n        pass\n",
   "        if _qs and isinstance(s, dict):\n            s = dict(s, qty_min=min(_qs), qty_max=max(_qs))\n    except Exception:\n        pass\n"
   "    # ── LOT_COMP_TAX_20260924 ── FY tax schedule (only when compounding has an\n"
   "    # equity basis and a tax rate); trades/net are untouched, sizing was.\n"
   "    try:\n"
   "        from app.backtest.engine.lot_compounding import fy_tax_schedule, lot_comp_tax_active\n"
   "        if isinstance(s, dict) and lot_comp_tax_active(cfg):\n"
   '            _sch = fy_tax_schedule(trades, cfg.get("lot_comp_tax_pct"), cfg.get("lot_comp_tax_fy_start_month") or 4,\n'
   '                                   run_to=(result.get("meta") or {}).get("date_to") or None)\n'
   '            if _sch.get("on"):\n'
   '                s = dict(s, lot_comp_tax_paid=_sch["paid"], lot_comp_tax_accrued=_sch["accrued"],\n'
   '                         lot_comp_tax_fy=[{k: f[k] for k in ("fy", "net", "taxable", "tax", "settled")} for f in _sch["fys"]])\n'
   "    except Exception:\n"
   "        pass\n"))

# shared synthetic-corpus harness: delete its temp corpus on exit (every
# engine suite execs this head; ~60 MB per run otherwise piles up in /tmp)
E(f"{BACKEND}/engine/test_lot_comp_runners.py",
  ("replace", "tmpdir = tempfile.mkdtemp()\n",
   "tmpdir = tempfile.mkdtemp()\n"
   "import atexit, shutil   # noqa: E402  ── LOT_COMP_TAX_20260924 ── tidy the temp corpus\n"
   "atexit.register(lambda: shutil.rmtree(tmpdir, ignore_errors=True))\n"))

# ═══════════════════════════ frontend: helper ═════════════════════════════
E(f"{FRONTEND}/backtest/lotCompounding.js",
  ("replace", "// ── LOT_COMP_EQ_20260924 ── scoreboard per BASE lot",
   "// ── LOT_COMP_TAX_20260924 ── FY tax schedule from a trade list; mirrors\n"
   "// backend fy_tax_schedule. FY = Indian tax year (start month configurable).\n"
   "// Losses carry forward; a FY is settled when a session after it exists in\n"
   "// the run (dateTo > FY end); the last open FY is accrued.\n"
   "export function taxActiveOf(cfg) {\n"
   "  const rate = Number(cfg?.lot_comp_tax_pct) || 0, cpl = Number(cfg?.lot_comp_capital_per_lot) || 0;\n"
   "  if (!(rate > 0 && rate < 100 && cpl > 0)) return false;\n"
   '  if (lotCompMode(cfg) === "equity") return true;\n'
   "  const dd = Number(cfg?.lot_comp_dd_limit_pct) || 0;\n"
   "  return dd > 0 && dd < 100 && !!lotCompParse(cfg);\n"
   "}\n"
   "export function fyTaxOf(trades, cfg, dateTo) {\n"
   "  if (!taxActiveOf(cfg)) return null;\n"
   "  const rate = Number(cfg.lot_comp_tax_pct) / 100, m = Math.min(12, Math.max(1, Math.floor(Number(cfg.lot_comp_tax_fy_start_month) || 4)));\n"
   "  const IST = 19800;\n"
   "  const netOf = (t) => (t.net_pnl != null ? Number(t.net_pnl) || 0 : (Number(t.pnl) || 0) - (Number(t.charges) || 0));\n"
   "  const fyStartOf = (d) => { const y = (d.getUTCMonth() + 1) >= m ? d.getUTCFullYear() : d.getUTCFullYear() - 1; return Date.UTC(y, m - 1, 1); };\n"
   "  const buckets = new Map();\n"
   "  for (const t of trades || []) {\n"
   "    if (!t || t.exit_price == null || t.exit_ts == null) continue;\n"
   "    const k = fyStartOf(new Date((Number(t.exit_ts) + IST) * 1000));\n"
   "    buckets.set(k, (buckets.get(k) || 0) + netOf(t));\n"
   "  }\n"
   "  const keys = [...buckets.keys()].sort((a, b) => a - b);\n"
   "  const out = { on: true, ratePct: rate * 100, fys: [], paid: 0, accrued: 0 };\n"
   "  if (!keys.length) return out;\n"
   "  const rt = dateTo ? Date.UTC(...String(dateTo).slice(0, 10).split(\"-\").map((x, i) => (i === 1 ? Number(x) - 1 : Number(x)))) : null;\n"
   "  let carry = 0;\n"
   "  for (let k = keys[0]; k <= keys[keys.length - 1];) {\n"
   "    const d = new Date(k), nxt = Date.UTC(d.getUTCFullYear() + 1, m - 1, 1), end = nxt - 86400000;\n"
   "    const net = buckets.get(k) || 0, taxable = net + carry, tax = taxable > 0 ? rate * taxable : 0;\n"
   "    const settled = rt != null ? rt > end : k !== keys[keys.length - 1];\n"
   "    out.fys.push({ fy: `${d.getUTCFullYear()}-${String((d.getUTCFullYear() + 1) % 100).padStart(2, \"0\")}`, net, carryIn: carry, taxable, tax, carryOut: taxable > 0 ? 0 : taxable, settled });\n"
   "    if (settled) out.paid += tax; else out.accrued += tax;\n"
   "    carry = taxable > 0 ? 0 : taxable;\n"
   "    k = nxt;\n"
   "  }\n"
   "  return out;\n"
   "}\n\n"
   "// ── LOT_COMP_EQ_20260924 ── scoreboard per BASE lot"),
  ("replace", '  const ddTag = ddLimit ? ` · DD≤${String(+ddLimit.toFixed(2))}%${ddShape}` : "";\n',
   '  const ddTag = ddLimit ? ` · DD≤${String(+ddLimit.toFixed(2))}%${ddShape}` : "";\n'
   "  // ── LOT_COMP_TAX_20260924 ──\n"
   "  const taxPct = taxActiveOf(cfg) ? Number(cfg.lot_comp_tax_pct) : 0;\n"
   '  const taxTag = taxPct ? ` · tax${String(+taxPct.toFixed(2))}%` : "";\n'),
  ("replace", '    const label = actualPeak != null ? `${tag} · ${base}→${peak}L` : `${tag} · from ${base}L`;\n    return { mode, step: 0, add: 0, base, max, scaleRs, cpl, ddLimit, ddMode, ddRelease, tiers: 0, peak, capped: false, mult: base > 0 ? peak / base : 1, tag, label };\n',
   '    const label = (actualPeak != null ? `${tag} · ${base}→${peak}L` : `${tag} · from ${base}L`) + taxTag;\n    return { mode, step: 0, add: 0, base, max, scaleRs, cpl, ddLimit, ddMode, ddRelease, taxPct, tiers: 0, peak, capped: false, mult: base > 0 ? peak / base : 1, tag, label };\n'),
  ("replace", "  return { mode, ...p, base, max, scaleRs, cpl, ddLimit, ddMode, ddRelease, tiers, peak, capped, mult, tag, label };\n",
   "  return { mode, ...p, base, max, scaleRs, cpl, ddLimit, ddMode, ddRelease, taxPct, tiers, peak, capped, mult, tag, label: label + taxTag };\n"))

# ═══════════════════════════ frontend: Backtest.jsx ═══════════════════════
E(f"{FRONTEND}/Backtest.jsx",
  ("replace", 'import { lotCompChip, lotCompOf, lotSizeOf, fmtL } from "./backtest/lotCompounding";',
   'import { lotCompChip, lotCompOf, lotSizeOf, fmtL, fyTaxOf } from "./backtest/lotCompounding";   // ── LOT_COMP_TAX_20260924 ──'),
  ("replace", '  const [compDdRel, setCompDdRel] = useState(() => String(loadLotComp().ddRel ?? "100"));   // ── LOT_COMP_DD2_20260924 ── release % of the drawdown\n',
   '  const [compDdRel, setCompDdRel] = useState(() => String(loadLotComp().ddRel ?? "100"));   // ── LOT_COMP_DD2_20260924 ── release % of the drawdown\n'
   '  const [compTax, setCompTax] = useState(() => loadLotComp().tax ?? "");   // ── LOT_COMP_TAX_20260924 ── tax % withdrawn each FY\n'),
  ("replace", "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs, mode: compMode, cpl: compCpl, dd: compDd, ddMode: compDdMode, ddRel: compDdRel })); } catch { /* ignore */ }\n"
              "  }, [compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd, compDdMode, compDdRel]);\n",
   "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs, mode: compMode, cpl: compCpl, dd: compDd, ddMode: compDdMode, ddRel: compDdRel, tax: compTax })); } catch { /* ignore */ }\n"
   "  }, [compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd, compDdMode, compDdRel, compTax]);\n"),
  ("replace", "    const ddRel = Number(compDdRel) || 100;\n",
   "    const ddRel = Number(compDdRel) || 100;\n"
   "    // ── LOT_COMP_TAX_20260924 ── tax only with an equity basis (equity mode, or DD guard + cpl)\n"
   "    const tax = Number(compTax) || 0;\n"
   '    const taxExtra = tax > 0 && tax < 100 && (compMode === "equity" || (dd > 0 && dd < 100 && cpl > 0)) ? { lot_comp_tax_pct: tax } : {};\n'),
  ("replace", '      ...(compDdMode !== "proportional" && ddRel > 0 && ddRel < 100 ? { lot_comp_dd_release_pct: ddRel } : {}),\n    } : {};\n',
   '      ...(compDdMode !== "proportional" && ddRel > 0 && ddRel < 100 ? { lot_comp_dd_release_pct: ddRel } : {}),\n    } : {};\n'
   "    Object.assign(ddExtra, taxExtra);   // ── LOT_COMP_TAX_20260924 ── rides on the same extras object\n"),
  ("replace", "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd, compDdMode, compDdRel]);\n",
   "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs, compMode, compCpl, compDd, compDdMode, compDdRel, compTax]);\n"),
  ("replace", '              <Field label="Max lots"><input type="number" min="0" step="1" placeholder="no cap" style={{ ...inputStyle, width: 90 }} value={compMax} onChange={(e) => setCompMax(e.target.value)} title="Ladder cap — the most lots the run will ever trade (maximum risk). Blank = no cap; a cap below the base lots is ignored." /></Field>\n',
   '              <Field label="Max lots"><input type="number" min="0" step="1" placeholder="no cap" style={{ ...inputStyle, width: 90 }} value={compMax} onChange={(e) => setCompMax(e.target.value)} title="Ladder cap — the most lots the run will ever trade (maximum risk). Blank = no cap; a cap below the base lots is ignored." /></Field>\n'
   "              {/* ── LOT_COMP_TAX_20260924 ── optional tax, withdrawn from equity each FY */}\n"
   '              {(compMode === "equity" || Number(compDd) > 0) && (\n'
   '                <Field label="Tax on profits (%)"><input type="number" min="0" max="99" step="0.1" placeholder="off" style={{ ...inputStyle, width: 90 }} value={compTax} onChange={(e) => setCompTax(e.target.value)} title="Each 1 April the previous FY\'s realised net is taxed at this rate and withdrawn from the equity that sizes lots (losses carry forward). 30 = top slab; 31.2 with cess. Trades and net P&L are unchanged — this changes sizing and is reported as a Tax card. Blank = off." /></Field>\n'
   "              )}\n"),
  ("replace", "{compPreview.ddLimit ? <>; <b>DD guard</b>: {compPreview.ddMode === \"proportional\" ? <>lots × (1 − drawdown ÷ {compPreview.ddLimit}%) every day, floored at base</> : <>below {compPreview.ddLimit}% of equity peak lots drop to base until {compPreview.ddRelease === 100 ? \"a new high\" : `${compPreview.ddRelease}% of the drawdown is regained`}</>}</> : null}; runs serially (parallel workers ignored).</>",
   "{compPreview.ddLimit ? <>; <b>DD guard</b>: {compPreview.ddMode === \"proportional\" ? <>lots × (1 − drawdown ÷ {compPreview.ddLimit}%) every day, floored at base</> : <>below {compPreview.ddLimit}% of equity peak lots drop to base until {compPreview.ddRelease === 100 ? \"a new high\" : `${compPreview.ddRelease}% of the drawdown is regained`}</>}</> : null}{compPreview.taxPct ? <>; <b>tax {compPreview.taxPct}%</b> on each FY's profit withdrawn on 1 April (losses carried forward)</> : null}; runs serially (parallel workers ignored).</>"),
  ("replace", "DD guard needs capital per lot</> : null)}.</>)",
   "DD guard needs capital per lot</> : null)}{compPreview.taxPct ? <>; <b>tax {compPreview.taxPct}%</b> on each FY's profit withdrawn on 1 April from the guard's equity</> : null}.</>)"),
  # results page: Tax card after Net P&L
  ("replace", '                  <div style={{ ...typography.label, color: colors.text.muted }}>Net P&L</div>\n'
              "                  <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(s.net_pnl) }}>\n"
              '                    {s.net_pnl >= 0 ? "+" : ""}₹{Math.round(s.net_pnl).toLocaleString("en-IN")}\n'
              "                  </div>\n                </Card>\n",
   '                  <div style={{ ...typography.label, color: colors.text.muted }}>Net P&L</div>\n'
   "                  <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(s.net_pnl) }}>\n"
   '                    {s.net_pnl >= 0 ? "+" : ""}₹{Math.round(s.net_pnl).toLocaleString("en-IN")}\n'
   "                  </div>\n                </Card>\n"
   "                {/* ── LOT_COMP_TAX_20260924 ── tax withdrawn from equity per FY (compounding with tax on) */}\n"
   "                {(() => {\n"
   "                  const tx = fyTaxOf(trades, resultConfig, dateTo);\n"
   "                  if (!tx) return null;\n"
   "                  const after = (s.net_pnl || 0) - tx.paid - tx.accrued;\n"
   "                  const fyLine = tx.fys.map((f) => `${f.fy}: ${f.tax > 0 ? \"₹\" + Math.round(f.tax).toLocaleString(\"en-IN\") : (f.taxable < 0 ? \"loss c/f\" : \"—\")}${f.settled ? \"\" : \" (due)\"}`).join(\"  ·  \");\n"
   "                  return (\n"
   "                    <Card elevated style={{ padding: spacing.lg }} title={fyLine}>\n"
   "                      <div style={{ ...typography.label, color: colors.text.muted }}>Tax {tx.ratePct}% · net after tax</div>\n"
   "                      <div style={{ fontSize: 22, fontWeight: 700, ...typography.mono, ...pnlStyle(after) }}>\n"
   '                        {after >= 0 ? "+" : ""}₹{Math.round(after).toLocaleString("en-IN")}\n'
   "                      </div>\n"
   "                      <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 2 }}>\n"
   '                        withdrawn ₹{Math.round(tx.paid).toLocaleString("en-IN")} over {tx.fys.filter((f) => f.settled).length} FY{tx.accrued > 0 ? ` · due ₹${Math.round(tx.accrued).toLocaleString("en-IN")} for the open FY` : ""}\n'
   "                      </div>\n"
   "                    </Card>\n"
   "                  );\n"
   "                })()}\n"))

# ═══════════════════════════ frontend: RunComparison KPI rows ═════════════
E(f"{FRONTEND}/backtest/RunComparison.jsx",
  ("replace", '    { key: "netPerBaseLot", group: "Capital", label: "Net / base lots", dir: +1, def: true, fmt: money, get: (m) => m?.perBaseLot?.net ?? null },\n',
   "    // ── LOT_COMP_TAX_20260924 ── from the persist_run stamp (blank for runs without tax)\n"
   '    { key: "taxPaid",     group: "Capital", label: "Tax withdrawn (FY)", dir: -1, def: true, fmt: money, get: (m, s) => (s?.lot_comp_tax_paid != null ? -Math.abs(Number(s.lot_comp_tax_paid) + Number(s.lot_comp_tax_accrued || 0)) : null) },\n'
   '    { key: "netAfterTax", group: "Capital", label: "Net after tax", dir: +1, def: true, fmt: money, get: (m, s) => (s?.lot_comp_tax_paid != null && s?.net_pnl != null ? Number(s.net_pnl) - Number(s.lot_comp_tax_paid) - Number(s.lot_comp_tax_accrued || 0) : null) },\n'
   '    { key: "netPerBaseLot", group: "Capital", label: "Net / base lots", dir: +1, def: true, fmt: money, get: (m) => m?.perBaseLot?.net ?? null },\n'))

# ═══════════════════════════ frontend: SweepBuilder axis ══════════════════
E(f"{FRONTEND}/backtest/SweepBuilder.jsx",
  ("replace", '    fmt: (v) => (v > 0 && v < 100 ? `rel${v}%` : "relHigh") },\n',
   '    fmt: (v) => (v > 0 && v < 100 ? `rel${v}%` : "relHigh") },\n'
   "  // ── LOT_COMP_TAX_20260924 ── tax % withdrawn per FY (0 = off)\n"
   '  { key: "lot_comp_tax", label: "Compound tax %", strategies: LOT_COMP_STRATS,\n'
   '    hint: "0, 30, 31.2", parse: _num,\n'
   "    apply: (c, v) => { if (v > 0 && v < 100) c.lot_comp_tax_pct = v; else delete c.lot_comp_tax_pct; },\n"
   '    fmt: (v) => (v > 0 && v < 100 ? `tax${v}%` : "taxOFF") },\n'))


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
            die(f"prerequisite {fence} not found in {rel} — apply the chain through apply_lot_comp_dd2.py first")
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
    tmp = tempfile.mkdtemp(prefix="lot_comp_tax_gate_")
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
        "app/backtest/engine/test_lot_comp_tax.py",
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
        for rel in [f"{FRONTEND}/Backtest.jsx", f"{FRONTEND}/backtest/RunComparison.jsx",
                    f"{FRONTEND}/backtest/SweepBuilder.jsx", f"{FRONTEND}/backtest/lotCompounding.js"]:
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
