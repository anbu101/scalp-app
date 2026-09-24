#!/usr/bin/env python3
# apply_lot_comp_equity.py — fence LOT_COMP_EQ_20260924
# (requires LOT_COMP_20260924 + LOT_COMP_MAX_20260924)
#
#   1. Results page: a "Lots" column on the trade table (qty ÷ lot size; raw
#      qty with a "q" suffix when the lot size is unknown).
#   2. Compare page: per-BASE-lot scoreboard rows — Net / base lot, Max DD /
#      base lot, Return ÷ DD / base lot — each trade's net divided by its
#      own size ratio, so a compounded run reads on its flat twin's scale.
#   3. Equity-based sizing as a third mode of the same sub-section:
#      lots(day) = ⌊(base × capital_per_lot + realised net) ÷ capital_per_lot⌋,
#      min 1, capped by Max lots; realised net = trades closed BEFORE that
#      day; new entries only; ₹ knobs follow the same ratio switch. Path-
#      dependent, so the sharded runners (IC/TSG/V1/V3) run serially in it.
#   4. persist_run stamps qty_min / qty_max on every run summary, so the
#      Compare page can show a run's ACTUAL peak lots (both modes).
#
# Run ONLY this script:
#   cd /Users/anbu/dev/scalp-app && python3 apply_lot_comp_equity.py [--allow-dirty] [--skip-tests]
from __future__ import annotations

import base64
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

FENCE = "LOT_COMP_EQ_20260924"
ROOT = os.path.dirname(os.path.abspath(__file__))
ALLOW_DIRTY = "--allow-dirty" in sys.argv
SKIP_TESTS = "--skip-tests" in sys.argv

BACKEND = "backend/app/backtest"
DESKTOP_BACKEND = "desktop/src-tauri/backend/app/backtest"
FRONTEND = "frontend/src/pages"
DESKTOP_FRONTEND = "desktop/src-tauri/frontend/src/pages"

PREREQ = {
    f"{BACKEND}/engine/lot_compounding.py": "LOT_COMP_MAX_20260924",
    f"{BACKEND}/tsg/backtest_tsg_runner.py": "LOT_COMP_20260924",
    f"{FRONTEND}/Backtest.jsx": "LOT_COMP_MAX_20260924",
    f"{FRONTEND}/backtest/lotCompounding.js": "LOT_COMP_MAX_20260924",
    f"{FRONTEND}/backtest/RunComparison.jsx": "LOT_COMP_20260924",
    f"{FRONTEND}/backtest/SweepBuilder.jsx": "LOT_COMP_MAX_20260924",
}

PAYLOADS_B64 = {
    f"{BACKEND}/engine/test_lot_comp_equity.py": "IyBiYWNrZW5kL2FwcC9iYWNrdGVzdC9lbmdpbmUvdGVzdF9sb3RfY29tcF9lcXVpdHkucHkKIwojIOKUgOKUgCBMT1RfQ09NUF9FUV8yMDI2MDkyNCDilIDilIAgc3RhbmRhbG9uZToKIyAgIGNkIGJhY2tlbmQgJiYgcHl0aG9uMyBhcHAvYmFja3Rlc3QvZW5naW5lL3Rlc3RfbG90X2NvbXBfZXF1aXR5LnB5CiMgVW5pdCBjaGVja3Mgb24gdGhlIGVxdWl0eSBzaXppbmcgcnVsZSwgdGhlbiBldmVyeSBydW5uZXIgdGhlIHNoYXJlZAojIHN5bnRoZXRpYyBjb3JwdXMgY2FuIGRyaXZlIGlzIHJ1biBpbiBlcXVpdHkgbW9kZSBhbmQgZWFjaCB0cmFkZSdzIHF0eSBpcwojIGNoZWNrZWQgYWdhaW5zdCBhbiBJTkRFUEVOREVOVCByZWNvbXB1dGF0aW9uIGZyb20gdGhlIHRyYWRlcyBjbG9zZWQgYmVmb3JlCiMgaXRzIGVudHJ5IGRheSDigJQgcHJvdmluZyB0aGUgYmVnaW5fZGF5IGZlZWRiYWNrIGxvb3AgaXMgd2lyZWQgaW4gZWFjaCBydW5uZXIuCmZyb20gX19mdXR1cmVfXyBpbXBvcnQgYW5ub3RhdGlvbnMKCmltcG9ydCBvcwppbXBvcnQgc3lzCmZyb20gZGF0ZXRpbWUgaW1wb3J0IGRhdGUKCkhFUkUgPSBvcy5wYXRoLmRpcm5hbWUob3MucGF0aC5hYnNwYXRoKF9fZmlsZV9fKSkKc3lzLnBhdGguaW5zZXJ0KDAsIG9zLnBhdGguYWJzcGF0aChvcy5wYXRoLmpvaW4oSEVSRSwgIi4uIiwgIi4uIiwgIi4uIikpKQpvcy5lbnZpcm9uLnNldGRlZmF1bHQoIlNDQUxQX0xPVF9TSVpFX09GRkxJTkUiLCAiMSIpCgpmcm9tIGFwcC5iYWNrdGVzdC5lbmdpbmUubG90X2NvbXBvdW5kaW5nIGltcG9ydCAoICAgIyBub3FhOiBFNDAyCiAgICBMb3RDb21wb3VuZGVyLCBsb3RfY29tcF9pc19lcXVpdHksIF90cmFkZV9uZXQpCgpGQUlMUyA9IFtdCgoKZGVmIGNoZWNrKG5hbWUsIG9rLCBub3RlPSIiKToKICAgIHByaW50KGYiICB7J1BBU1MnIGlmIG9rIGVsc2UgJ0ZBSUwnfSAge25hbWV9eygnICDigJQgJyArIHN0cihub3RlKSkgaWYgKG5vdGUgYW5kIG5vdCBvaykgZWxzZSAnJ30iKQogICAgaWYgbm90IG9rOgogICAgICAgIEZBSUxTLmFwcGVuZChuYW1lKQoKCmNsYXNzIFQ6ICAgIyBtaW5pbWFsIHRyYWRlIHN0YW5kLWluCiAgICBkZWYgX19pbml0X18oc2VsZiwgZXhpdF90cywgbmV0X3BubD1Ob25lLCBwbmw9Tm9uZSwgY2hhcmdlcz0wLjAsIGV4aXRfcHJpY2U9MS4wLCBncm9zcz1Ob25lLCBuZXQ9Tm9uZSk6CiAgICAgICAgc2VsZi5leGl0X3RzLCBzZWxmLm5ldF9wbmwsIHNlbGYucG5sLCBzZWxmLmNoYXJnZXMgPSBleGl0X3RzLCBuZXRfcG5sLCBwbmwsIGNoYXJnZXMKICAgICAgICBzZWxmLmV4aXRfcHJpY2UsIHNlbGYuZ3Jvc3MsIHNlbGYubmV0ID0gZXhpdF9wcmljZSwgZ3Jvc3MsIG5ldAoKCnByaW50KCLilIDilIAgcGFyc2UgLyBvbiDilIDilIAiKQpTID0gZGF0ZSgyMDIwLCAxLCAxKQplcSA9IHsibG90X2NvbXBfbW9kZSI6ICJlcXVpdHkiLCAibG90X2NvbXBfY2FwaXRhbF9wZXJfbG90IjogMTUwMDAwfQpjID0gTG90Q29tcG91bmRlcihlcSwgUywgMTApCmNoZWNrKCJlcXVpdHkgbW9kZSBvbiB3aXRoIGJhc2UgKyBjcGwiLCBjLm9uIGFuZCBjLm1vZGUgPT0gImVxdWl0eSIgYW5kIGMuY3BsID09IDE1MDAwMCkKY2hlY2soImxvdF9jb21wX2lzX2VxdWl0eSIsIGxvdF9jb21wX2lzX2VxdWl0eShlcSkgYW5kIG5vdCBsb3RfY29tcF9pc19lcXVpdHkoeyJsb3RfY29tcF9tb2RlIjogImNhbGVuZGFyIn0pIGFuZCBub3QgbG90X2NvbXBfaXNfZXF1aXR5KHt9KSkKY2hlY2soImVxdWl0eSBtb2RlIG5lZWRzIGNwbCIsIG5vdCBMb3RDb21wb3VuZGVyKHsibG90X2NvbXBfbW9kZSI6ICJlcXVpdHkifSwgUywgMTApLm9uKQpjaGVjaygiZXF1aXR5IG1vZGUgbmVlZHMgYmFzZSIsIG5vdCBMb3RDb21wb3VuZGVyKGVxLCBTLCAwKS5vbikKY2hlY2soImNhbGVuZGFyIGtleXMgaWdub3JlZCBpbiBlcXVpdHkgbW9kZSIsIExvdENvbXBvdW5kZXIoeyoqZXEsICJsb3RfY29tcF9zdGVwX21vbnRocyI6IDMsICJsb3RfY29tcF9hZGRfbG90cyI6IDF9LCBTLCAxMCkubW9kZSA9PSAiZXF1aXR5IikKY2hlY2soIkVRVUlUWSBzdHJpbmcgYWNjZXB0ZWQiLCBMb3RDb21wb3VuZGVyKHsibG90X2NvbXBfbW9kZSI6ICJFUVVJVFkiLCAibG90X2NvbXBfY2FwaXRhbF9wZXJfbG90IjogIjE1MDAwMCJ9LCBTLCAxMCkub24pCmNoZWNrKCJjYWxlbmRhciBtb2RlIHVuYWZmZWN0ZWQgYnkgY3BsIiwgTG90Q29tcG91bmRlcih7ImxvdF9jb21wX3N0ZXBfbW9udGhzIjogMywgImxvdF9jb21wX2FkZF9sb3RzIjogMSwgImxvdF9jb21wX2NhcGl0YWxfcGVyX2xvdCI6IDE1MDAwMH0sIFMsIDEwKS5tb2RlID09ICJjYWxlbmRhciIpCmNoZWNrKCJiZWZvcmUgYW55IGJlZ2luX2RheTogbG90cyA9IGJhc2UiLCBjLmxvdHMoZGF0ZSgyMDIxLCAxLCAxKSkgPT0gMTAgYW5kIGMubXVsdChkYXRlKDIwMjEsIDEsIDEpKSA9PSAxLjApCmNoZWNrKCJ0aWVyIGlzIDAgaW4gZXF1aXR5IG1vZGUiLCBjLnRpZXIoZGF0ZSgyMDI1LCAxLCAxKSkgPT0gMCkKCnByaW50KCLilIDilIAgZXF1aXR5X2xvdHMgcnVsZSDilIDilIAiKQpjaGVjaygibm8gUCZMIOKGkiBiYXNlIiwgYy5lcXVpdHlfbG90cygwLjApID09IDEwKQpjaGVjaygiKzEgbG90IGF0ICtjcGwiLCBjLmVxdWl0eV9sb3RzKDE1MDAwMCkgPT0gMTEpCmNoZWNrKCIrMCBqdXN0IHVuZGVyIGNwbCIsIGMuZXF1aXR5X2xvdHMoMTQ5OTk5Ljk5KSA9PSAxMCkKY2hlY2soIuKIkjEgbG90IGF0IOKIkmNwbCIsIGMuZXF1aXR5X2xvdHMoLTE1MDAwMCkgPT0gOSkKY2hlY2soImZsb29ycyBhdCAxIGxvdCIsIGMuZXF1aXR5X2xvdHMoLTEwICogMTUwMDAwKSA9PSAxKQpjaGVjaygiY2FwIGhvbm91cmVkIiwgTG90Q29tcG91bmRlcih7KiplcSwgImxvdF9jb21wX21heF9sb3RzIjogMTJ9LCBTLCAxMCkuZXF1aXR5X2xvdHMoMTAgKiAxNTAwMDApID09IDEyKQpjaGVjaygiY2FwIGJlbG93IGJhc2UgaWdub3JlZCIsIExvdENvbXBvdW5kZXIoeyoqZXEsICJsb3RfY29tcF9tYXhfbG90cyI6IDV9LCBTLCAxMCkuZXF1aXR5X2xvdHMoMTAgKiAxNTAwMDApID09IDIwKQoKcHJpbnQoIuKUgOKUgCBiZWdpbl9kYXkgZmVlZGJhY2sg4pSA4pSAIikKRDAgPSAxNTc3ODM2ODAwICsgNSAqIDM2MDAgKyAzMCAqIDYwICAgIyAyMDIwLTAxLTAxIDA5OjE1IElTVC1pc2ggZXBvY2gKZGF5MSwgZGF5MiwgZGF5MyA9IGRhdGUoMjAyMCwgMSwgMSksIGRhdGUoMjAyMCwgMSwgMiksIGRhdGUoMjAyMCwgMSwgMykKdHJhZGVzID0gW10KY2hlY2soImRheSAxOiBubyB0cmFkZXMg4oaSIGJhc2UiLCBjLmJlZ2luX2RheShkYXkxLCB0cmFkZXMpID09IDEwIGFuZCBjLmxvdHMoZGF5MSkgPT0gMTApCnRyYWRlcy5hcHBlbmQoVChleGl0X3RzPUQwICsgMzYwMCwgbmV0X3BubD0xNjAwMDAuMCkpICAgICAgICAgICAjIGNsb3NlZCBkYXkgMQpjaGVjaygic2FtZSBkYXkgYWdhaW46IHVuY2hhbmdlZCAoZGF5IGd1YXJkKSIsIGMuYmVnaW5fZGF5KGRheTEsIHRyYWRlcykgPT0gMTApCmNoZWNrKCJkYXkgMiBzZWVzIGRheS0xIGNsb3NlIOKGkiAxMSBsb3RzIiwgYy5iZWdpbl9kYXkoZGF5MiwgdHJhZGVzKSA9PSAxMSBhbmQgYy5sb3RzKGRheTIpID09IDExKQpjaGVjaygibXVsdCB0cmFja3MgbG90cyIsIGFicyhjLm11bHQoZGF5MikgLSAxLjEpIDwgMWUtMTIgYW5kIGMuc2NhbGVfbG90cygxMCwgZGF5MikgPT0gMTEpCmNoZWNrKCJzY2FsZV9ycyB0cmFja3MgbG90cyIsIGFicyhjLnNjYWxlX3JzKDM1MDAwLCBkYXkyKSAtIDM4NTAwKSA8IDFlLTkpCnRyYWRlcy5hcHBlbmQoVChleGl0X3RzPUQwICsgODY0MDAgKyAzNjAwLCBwbmw9LTIwMDAwMC4wLCBjaGFyZ2VzPTEwMDAuMCkpICAgIyBkYXkgMiBsb3NzIHZpYSBwbmwtY2hhcmdlcwp0cmFkZXMuYXBwZW5kKFQoZXhpdF90cz1Ob25lLCBleGl0X3ByaWNlPU5vbmUsIG5ldF9wbmw9OTk5OTk5LjApKSAgICAgICAgICAgICAjIE9QRU4gdHJhZGUgbXVzdCBub3QgY291bnQKY2hlY2soImRheSAzOiAxNjAwMDAg4oiSIDIwMTAwMCDihpIgOSBsb3RzIiwgYy5iZWdpbl9kYXkoZGF5MywgdHJhZGVzKSA9PSA5KQpjaGVjaygib3BlbiB0cmFkZSBleGNsdWRlZCBmcm9tIGVxdWl0eSIsIGMuX2VxX2VxdWl0eSA9PSAxMCAqIDE1MDAwMCArIDE2MDAwMCAtIDIwMTAwMCkKY2hlY2soIl90cmFkZV9uZXQgcHJlZmVycyBuZXRfcG5sIiwgX3RyYWRlX25ldChUKDEsIG5ldF9wbmw9NS4wLCBwbmw9OTkuMCkpID09IDUuMCkKY2hlY2soIl90cmFkZV9uZXQgZmFsbHMgYmFjayB0byBuZXQiLCBfdHJhZGVfbmV0KFQoMSwgbmV0PTcuMCkpID09IDcuMCkKY2hlY2soIl90cmFkZV9uZXQgZmFsbHMgYmFjayB0byBncm9zc+KIkmNoYXJnZXMiLCBfdHJhZGVfbmV0KFQoMSwgZ3Jvc3M9MTAuMCwgY2hhcmdlcz0zLjApKSA9PSA3LjApCmNoZWNrKCJfdHJhZGVfbmV0IGRpY3QgdHJhZGVzIiwgX3RyYWRlX25ldCh7ImV4aXRfcHJpY2UiOiAxLCAibmV0X3BubCI6IDQuMH0pID09IDQuMCkKZGcgPSBjLmRpYWcoKQpjaGVjaygiZGlhZyBsYWRkZXIgcmVjb3JkcyBjaGFuZ2VzIiwgZGdbIm9uIl0gYW5kIGRnWyJtb2RlIl0gPT0gImVxdWl0eSIgYW5kIFt4WyJsb3RzIl0gZm9yIHggaW4gZGdbImxhZGRlciJdXSA9PSBbMTAsIDExLCA5XSkKY2hlY2soInBlYWtfbG90cyA9IGxhZGRlciBtYXgiLCBjLnBlYWtfbG90cyhkYXRlKDIwMjYsIDEsIDEpKSA9PSAxMSkKY2hlY2soImRlc2NyaWJlIiwgImVxdWl0eSIgaW4gYy5kZXNjcmliZSgpIGFuZCAiY3BsPTE1MDAwMCIgaW4gYy5kZXNjcmliZSgpKQpvZmYgPSBMb3RDb21wb3VuZGVyKHt9LCBTLCAxMCkKY2hlY2soImJlZ2luX2RheSBpcyBhIG5vLW9wIHdoZW4gT0ZGIiwgb2ZmLmJlZ2luX2RheShkYXkyLCB0cmFkZXMpID09IDEwIGFuZCBub3Qgb2ZmLm9uKQpjYWwgPSBMb3RDb21wb3VuZGVyKHsibG90X2NvbXBfc3RlcF9tb250aHMiOiAzLCAibG90X2NvbXBfYWRkX2xvdHMiOiAxfSwgUywgMTApCmNoZWNrKCJiZWdpbl9kYXkgaXMgYSBuby1vcCBpbiBjYWxlbmRhciBtb2RlIiwgY2FsLmJlZ2luX2RheShkYXRlKDIwMjAsIDQsIDEpLCB0cmFkZXMpID09IDExKQoKIyDilIDilIAgcnVubmVycyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKcHJpbnQoIuKUgOKUgCBydW5uZXJzIChlcXVpdHkgbW9kZSwgY3BsIOKCuTIwLDAwMCwgY2FwIDMwKSDilIDilIAiKQp0cnk6CiAgICBzcmMgPSBvcGVuKG9zLnBhdGguam9pbihIRVJFLCAidGVzdF9sb3RfY29tcF9ydW5uZXJzLnB5IiksIGVuY29kaW5nPSJ1dGYtOCIpLnJlYWQoKQogICAgaGVhZCA9IHNyYy5zcGxpdCgiUlVOTkVSUyA9IFsiKVswXS5yZXBsYWNlKCJ3aGlsZSBsZW4oYWxsX2RheXMpIDwgNDU6IiwgIndoaWxlIGxlbihhbGxfZGF5cykgPCAzMDoiKQogICAgbnMgPSB7Il9fZmlsZV9fIjogb3MucGF0aC5qb2luKEhFUkUsICJ0ZXN0X2xvdF9jb21wX3J1bm5lcnMucHkiKSwgIl9fbmFtZV9fIjogIl9sY3JfaGVhZCJ9CiAgICBleGVjKGNvbXBpbGUoaGVhZCwgImxjcl9oZWFkIiwgImV4ZWMiKSwgbnMpCiAgICBydW5fb25lLCBfZywgZGF5X29mX3RzLCBSVU5fRlJPTSA9IG5zWyJydW5fb25lIl0sIG5zWyJfZyJdLCBuc1siZGF5X29mX3RzIl0sIG5zWyJSVU5fRlJPTSJdCiAgICBMT1QgPSA2NQogICAgQ1BMLCBDQVAgPSAyMDAwMCwgMzAKICAgIEVRID0geyJsb3RfY29tcF9tb2RlIjogImVxdWl0eSIsICJsb3RfY29tcF9jYXBpdGFsX3Blcl9sb3QiOiBDUEwsICJsb3RfY29tcF9tYXhfbG90cyI6IENBUCwKICAgICAgICAgICJsb3RfY29tcF9zdGVwX21vbnRocyI6IDAsICJsb3RfY29tcF9hZGRfbG90cyI6IDB9CiAgICB2YXJpZWQgPSAwCiAgICBmb3IgbmFtZSBpbiBbIlNURkNfVjEiLCAiT1JCX1YxIiwgIkJSS19WMSIsICJGVkdfVjEiLCAiT1JWX1YxIiwgIkNCT19WMSIsICJHQ19WMSIsICJWRVRfVjEiLAogICAgICAgICAgICAgICAgICJUTUFfVjIiLCAiVE1BX1YxIiwgIlZBUF9WMSIsICJUU0dfVjEiLCAiSUNfVjEiLCAiSEFfVjEiLCAiU0NBTFBfVjUiLCAiU0NBTFBfVjEiLCAiU0NBTFBfVjMiXToKICAgICAgICB0cnk6CiAgICAgICAgICAgIHIgPSBydW5fb25lKG5hbWUsIEVRKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICAgICAgY2hlY2soZiJ7bmFtZX06IGVxdWl0eSBydW4gZXhlY3V0ZXMiLCBGYWxzZSwgcmVwcihlKSkKICAgICAgICAgICAgY29udGludWUKICAgICAgICB0ciA9IHJbInRyYWRlcyJdCiAgICAgICAgcmVmID0gTG90Q29tcG91bmRlcihFUSwgUlVOX0ZST00sIDEwKQogICAgICAgIGNsb3NlZCA9IFt0IGZvciB0IGluIHRyIGlmIF9nKHQsICJleGl0X3RzIikgaXMgbm90IE5vbmUgYW5kIF9nKHQsICJleGl0X3ByaWNlIikgaXMgbm90IE5vbmVdCiAgICAgICAgYmFkID0gMAogICAgICAgIGxvdHNfc2VlbiA9IHNldCgpCiAgICAgICAgZm9yIHQgaW4gc29ydGVkKHRyLCBrZXk9bGFtYmRhIHg6IF9nKHgsICJlbnRyeV90cyIpIG9yIDApOgogICAgICAgICAgICBlbnQgPSBkYXlfb2ZfdHMoX2codCwgImVudHJ5X3RzIikpCiAgICAgICAgICAgIG5ldF9iZWZvcmUgPSBzdW0oKF90cmFkZV9uZXQoeCkgb3IgMC4wKSBmb3IgeCBpbiBjbG9zZWQgaWYgZGF5X29mX3RzKF9nKHgsICJleGl0X3RzIikpIDwgZW50KQogICAgICAgICAgICB3YW50ID0gcmVmLmVxdWl0eV9sb3RzKG5ldF9iZWZvcmUpICogTE9UCiAgICAgICAgICAgIGdvdCA9IGludChfZyh0LCAicXR5Iikgb3IgMCkKICAgICAgICAgICAgbG90c19zZWVuLmFkZChnb3QgLy8gTE9UKQogICAgICAgICAgICBpZiBnb3QgIT0gd2FudDoKICAgICAgICAgICAgICAgIGJhZCArPSAxCiAgICAgICAgICAgICAgICBpZiBiYWQgPD0gMzoKICAgICAgICAgICAgICAgICAgICBwcmludChmIiAgICAgICAge25hbWV9IHtlbnR9OiBxdHkge2dvdH0gd2FudCB7d2FudH0gKG5ldCBiZWZvcmUge25ldF9iZWZvcmU6LjBmfSkiKQogICAgICAgIGNoZWNrKGYie25hbWV9OiBldmVyeSB0cmFkZSBzaXplZCBmcm9tIHRyYWRlcyBjbG9zZWQgYmVmb3JlIGl0cyBlbnRyeSBkYXkgKHtsZW4odHIpfSB0cmFkZXMsIGxvdHMge3NvcnRlZChsb3RzX3NlZW4pfSkiLCB0ciBhbmQgYmFkID09IDAsIGYie2JhZH0gbWlzbWF0Y2hlcyIpCiAgICAgICAgaWYgbGVuKGxvdHNfc2VlbikgPiAxOgogICAgICAgICAgICB2YXJpZWQgKz0gMQogICAgICAgIHNtID0gci5nZXQoInN1bW1hcnkiKSBvciB7fQogICAgY2hlY2soImxvdHMgYWN0dWFsbHkgdmFyaWVkIGluIG1vc3QgcnVubmVycyAobm90IGEgdmFjdW91cyBwYXNzKSIsIHZhcmllZCA+PSAxMCwgdmFyaWVkKQoKICAgICMgcGVyc2lzdF9ydW4gc3RhbXBzIHRoZSBxdHkgZW52ZWxvcGUgb24gdGhlIHN1bW1hcnkKICAgIGZyb20gYXBwLmJhY2t0ZXN0LnJlcG8uYmFja3Rlc3RfcmVwbyBpbXBvcnQgcGVyc2lzdF9ydW4gICAjIG5vcWE6IEY0MDEKICAgIGltcG9ydCBpbnNwZWN0CiAgICBjaGVjaygicGVyc2lzdF9ydW4gc3RhbXBzIHF0eV9taW4vcXR5X21heCIsICJxdHlfbWF4IiBpbiBpbnNwZWN0LmdldHNvdXJjZShwZXJzaXN0X3J1bikpCmV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgIGltcG9ydCB0cmFjZWJhY2sKICAgIHRyYWNlYmFjay5wcmludF9leGMoKQogICAgY2hlY2soInJ1bm5lciBjaGVja3MgZXhlY3V0ZSIsIEZhbHNlLCByZXByKGUpKQoKcHJpbnQoKQppZiBGQUlMUzoKICAgIHByaW50KGYiRkFJTEVEIHtsZW4oRkFJTFMpfToiKQogICAgZm9yIHggaW4gRkFJTFM6CiAgICAgICAgcHJpbnQoIiAgLSIsIHgpCiAgICBzeXMuZXhpdCgxKQpwcmludCgiQUxMIExPVF9DT01QX0VRIENIRUNLUyBQQVNTRUQiKQo=",
}
FRAG_B64 = {   # replacement fragments (base64) used inside EDITS below
    "lotcompof_old": "Ly8g4pSA4pSAIExPVF9DT01QX01BWF8yMDI2MDkyNCDilIDilIAgdGhlIGxhZGRlciBpcyBjYXBwZWQgYXQgbG90X2NvbXBfbWF4X2xvdHMgKGEgY2FwCi8vIGJlbG93IHRoZSBiYXNlIGlzIGlnbm9yZWQsIGFzIGluIHRoZSBiYWNrZW5kKTsgbG90X2NvbXBfc2NhbGVfcnM9ZmFsc2UKLy8gbWVhbnMgdGhlIOKCuSBrbm9icyBzdGF5IGZpeGVkIHdoaWxlIGxvdHMgZ3JvdyAoc2hvd24gYXMgIuKCuSBmaXhlZCIpLgpleHBvcnQgZnVuY3Rpb24gbG90Q29tcE9mKGNmZywgZGF0ZUZyb20sIGRhdGVUbykgewogIGNvbnN0IHAgPSBsb3RDb21wUGFyc2UoY2ZnKTsKICBpZiAoIXApIHJldHVybiBudWxsOwogIGNvbnN0IGJhc2UgPSBiYXNlTG90c09mKGNmZyk7CiAgY29uc3QgcmF3TWF4ID0gTWF0aC5mbG9vcihOdW1iZXIoY2ZnPy5sb3RfY29tcF9tYXhfbG90cykpIHx8IDA7CiAgY29uc3QgbWF4ID0gYmFzZSAhPSBudWxsICYmIHJhd01heCA+PSBiYXNlID8gcmF3TWF4IDogMDsKICBjb25zdCBzY2FsZVJzID0gIShjZmc/LmxvdF9jb21wX3NjYWxlX3JzID09PSBmYWxzZSB8fCBTdHJpbmcoY2ZnPy5sb3RfY29tcF9zY2FsZV9ycykudG9Mb3dlckNhc2UoKSA9PT0gImZhbHNlIik7CiAgY29uc3QgdGllcnMgPSBkYXRlRnJvbSAmJiBkYXRlVG8gPyBNYXRoLmZsb29yKG1vbnRoc0VsYXBzZWQoZGF0ZUZyb20sIGRhdGVUbykgLyBwLnN0ZXApIDogMDsKICBjb25zdCBjYXBMb3RzID0gKG4pID0+IChtYXggPyBNYXRoLm1pbihuLCBtYXgpIDogbik7CiAgY29uc3QgcGVhayA9IGJhc2UgIT0gbnVsbCA/IGNhcExvdHMoYmFzZSArIHRpZXJzICogcC5hZGQpIDogbnVsbDsKICBjb25zdCBjYXBwZWQgPSBiYXNlICE9IG51bGwgJiYgbWF4ID4gMCAmJiBiYXNlICsgdGllcnMgKiBwLmFkZCA+IG1heDsKICBjb25zdCBtdWx0ID0gYmFzZSAhPSBudWxsICYmIGJhc2UgPiAwID8gcGVhayAvIGJhc2UgOiAxOwogIGNvbnN0IHRhZyA9IGArJHtwLmFkZH1MIC8gJHtwLnN0ZXB9bW8ke21heCA/IGAg4omkJHttYXh9TGAgOiAiIn0ke3NjYWxlUnMgPyAiIiA6ICIgwrcg4oK5IGZpeGVkIn1gOwogIGNvbnN0IGxhYmVsID0gcGVhayAhPSBudWxsICYmIHRpZXJzID4gMCA/IGAke3RhZ30gwrcgJHtiYXNlfeKGkiR7cGVha31MJHtjYXBwZWQgPyAiIChjYXApIiA6ICIifWAgOiAoYmFzZSAhPSBudWxsID8gYCR7dGFnfSDCtyBmcm9tICR7YmFzZX1MYCA6IHRhZyk7CiAgcmV0dXJuIHsgLi4ucCwgYmFzZSwgbWF4LCBzY2FsZVJzLCB0aWVycywgcGVhaywgY2FwcGVkLCBtdWx0LCB0YWcsIGxhYmVsIH07Cn0KCg==", "lotcompof_new": "Ly8g4pSA4pSAIExPVF9DT01QX01BWF8yMDI2MDkyNCDilIDilIAgdGhlIGxhZGRlciBpcyBjYXBwZWQgYXQgbG90X2NvbXBfbWF4X2xvdHMgKGEgY2FwCi8vIGJlbG93IHRoZSBiYXNlIGlzIGlnbm9yZWQsIGFzIGluIHRoZSBiYWNrZW5kKTsgbG90X2NvbXBfc2NhbGVfcnM9ZmFsc2UKLy8gbWVhbnMgdGhlIOKCuSBrbm9icyBzdGF5IGZpeGVkIHdoaWxlIGxvdHMgZ3JvdyAoc2hvd24gYXMgIuKCuSBmaXhlZCIpLgovLyDilIDilIAgTE9UX0NPTVBfRVFfMjAyNjA5MjQg4pSA4pSAIHR3byBzaXppbmcgbW9kZXM6Ci8vICAgY2FsZW5kYXIgKGRlZmF1bHQpOiBsb3RzID0gYmFzZSArIOKMim1vbnRocy9zdGVw4oyLIMOXIGFkZAovLyAgIGVxdWl0eTogICAgICAgICAgICAgbG90cyA9IOKMiihiYXNlIMOXIGNwbCArIHJlYWxpc2VkIG5ldCkgw7cgY3Bs4oyLLCBtaW4gMSwKLy8gICAgICAgICAgICAgICAgICAgICAgIHJlY29tcHV0ZWQgZWFjaCBkYXkgZnJvbSB0cmFkZXMgY2xvc2VkIGJlZm9yZSBpdAovLyBgc3VtbWFyeWAgKG9wdGlvbmFsKSBjYXJyaWVzIHF0eV9tYXggZnJvbSBwZXJzaXN0X3J1biwgd2hpY2ggaXMgdGhlIHJ1bidzCi8vIEFDVFVBTCBwZWFrIOKAlCB1c2VkIGZvciB0aGUgcGVhayBmaWd1cmUgaW4gYm90aCBtb2RlcyB3aGVuIHByZXNlbnQuCmV4cG9ydCBmdW5jdGlvbiBsb3RTaXplT2YoY2ZnLCBzdHJhdGVneUlkLCB1bmRlcmx5aW5nKSB7CiAgY29uc3QgYyA9IE51bWJlcihjZmc/LmxvdF9zaXplKSB8fCBOdW1iZXIoY2ZnPy5xdWFudGl0eT8ubG90X3NpemUpIHx8IDA7CiAgaWYgKGMgPiAwKSByZXR1cm4gYzsKICBjb25zdCB1ID0gU3RyaW5nKHVuZGVybHlpbmcgfHwgY2ZnPy51bmRlcmx5aW5nIHx8ICIiKS50b1VwcGVyQ2FzZSgpOwogIGlmICh1ID09PSAiQkFOS05JRlRZIiB8fCBTdHJpbmcoc3RyYXRlZ3lJZCB8fCAiIikuc3RhcnRzV2l0aCgiQkIiKSkgcmV0dXJuIDMwOwogIGlmICghdSB8fCB1ID09PSAiTklGVFkiKSByZXR1cm4gNjU7CiAgcmV0dXJuIG51bGw7ICAgLy8gc3RvY2sgdW5kZXJseWluZyB3aXRob3V0IGEgY29uZmlndXJlZCBsb3Qg4oCUIHVua25vd24KfQoKZXhwb3J0IGNvbnN0IGZtdEwgPSAocnMpID0+IFN0cmluZygrKE51bWJlcihycyB8fCAwKSAvIDEwMDAwMCkudG9GaXhlZCgyKSk7ICAgLy8g4oK5IOKGkiBsYWtoLCB0cmFpbGluZyB6ZXJvcyB0cmltbWVkCgpleHBvcnQgZnVuY3Rpb24gbG90Q29tcE1vZGUoY2ZnKSB7CiAgcmV0dXJuIFN0cmluZyhjZmc/LmxvdF9jb21wX21vZGUgfHwgImNhbGVuZGFyIikudG9Mb3dlckNhc2UoKSA9PT0gImVxdWl0eSIgPyAiZXF1aXR5IiA6ICJjYWxlbmRhciI7Cn0KCmV4cG9ydCBmdW5jdGlvbiBsb3RDb21wT2YoY2ZnLCBkYXRlRnJvbSwgZGF0ZVRvLCBzdW1tYXJ5KSB7CiAgY29uc3QgYmFzZSA9IGJhc2VMb3RzT2YoY2ZnKTsKICBjb25zdCBtb2RlID0gbG90Q29tcE1vZGUoY2ZnKTsKICBjb25zdCBjcGwgPSBOdW1iZXIoY2ZnPy5sb3RfY29tcF9jYXBpdGFsX3Blcl9sb3QpIHx8IDA7CiAgY29uc3QgcCA9IGxvdENvbXBQYXJzZShjZmcpOwogIGlmIChtb2RlID09PSAiZXF1aXR5IiA/ICEoYmFzZSA+IDAgJiYgY3BsID4gMCkgOiAhcCkgcmV0dXJuIG51bGw7CiAgY29uc3QgcmF3TWF4ID0gTWF0aC5mbG9vcihOdW1iZXIoY2ZnPy5sb3RfY29tcF9tYXhfbG90cykpIHx8IDA7CiAgY29uc3QgbWF4ID0gYmFzZSAhPSBudWxsICYmIHJhd01heCA+PSBiYXNlID8gcmF3TWF4IDogMDsKICBjb25zdCBzY2FsZVJzID0gIShjZmc/LmxvdF9jb21wX3NjYWxlX3JzID09PSBmYWxzZSB8fCBTdHJpbmcoY2ZnPy5sb3RfY29tcF9zY2FsZV9ycykudG9Mb3dlckNhc2UoKSA9PT0gImZhbHNlIik7CiAgY29uc3QgY2FwTG90cyA9IChuKSA9PiAobWF4ID8gTWF0aC5taW4obiwgbWF4KSA6IG4pOwogIC8vIGFjdHVhbCBwZWFrIGZyb20gdGhlIHBlcnNpc3RlZCBydW4sIHdoZW4gd2UgaGF2ZSBpdAogIGNvbnN0IGxzID0gbG90U2l6ZU9mKGNmZywgdW5kZWZpbmVkLCBjZmc/LnVuZGVybHlpbmcpOwogIGNvbnN0IGFjdHVhbFBlYWsgPSBzdW1tYXJ5ICYmIE51bWJlcihzdW1tYXJ5LnF0eV9tYXgpID4gMCAmJiBscyA/IE1hdGgucm91bmQoTnVtYmVyKHN1bW1hcnkucXR5X21heCkgLyBscykgOiBudWxsOwogIGlmIChtb2RlID09PSAiZXF1aXR5IikgewogICAgY29uc3QgcGVhayA9IGFjdHVhbFBlYWsgPz8gYmFzZTsKICAgIGNvbnN0IHRhZyA9IGBFcXVpdHkg4oK5JHtmbXRMKGNwbCl9TC9sb3Qke21heCA/IGAg4omkJHttYXh9TGAgOiAiIn0ke3NjYWxlUnMgPyAiIiA6ICIgwrcg4oK5IGZpeGVkIn1gOwogICAgY29uc3QgbGFiZWwgPSBhY3R1YWxQZWFrICE9IG51bGwgPyBgJHt0YWd9IMK3ICR7YmFzZX3ihpIke3BlYWt9TGAgOiBgJHt0YWd9IMK3IGZyb20gJHtiYXNlfUxgOwogICAgcmV0dXJuIHsgbW9kZSwgc3RlcDogMCwgYWRkOiAwLCBiYXNlLCBtYXgsIHNjYWxlUnMsIGNwbCwgdGllcnM6IDAsIHBlYWssIGNhcHBlZDogZmFsc2UsIG11bHQ6IGJhc2UgPiAwID8gcGVhayAvIGJhc2UgOiAxLCB0YWcsIGxhYmVsIH07CiAgfQogIGNvbnN0IHRpZXJzID0gZGF0ZUZyb20gJiYgZGF0ZVRvID8gTWF0aC5mbG9vcihtb250aHNFbGFwc2VkKGRhdGVGcm9tLCBkYXRlVG8pIC8gcC5zdGVwKSA6IDA7CiAgY29uc3QgbGFkZGVyID0gYmFzZSAhPSBudWxsID8gY2FwTG90cyhiYXNlICsgdGllcnMgKiBwLmFkZCkgOiBudWxsOwogIGNvbnN0IHBlYWsgPSBhY3R1YWxQZWFrID8/IGxhZGRlcjsKICBjb25zdCBjYXBwZWQgPSBiYXNlICE9IG51bGwgJiYgbWF4ID4gMCAmJiBiYXNlICsgdGllcnMgKiBwLmFkZCA+IG1heDsKICBjb25zdCBtdWx0ID0gYmFzZSAhPSBudWxsICYmIGJhc2UgPiAwICYmIHBlYWsgIT0gbnVsbCA/IHBlYWsgLyBiYXNlIDogMTsKICBjb25zdCB0YWcgPSBgKyR7cC5hZGR9TCAvICR7cC5zdGVwfW1vJHttYXggPyBgIOKJpCR7bWF4fUxgIDogIiJ9JHtzY2FsZVJzID8gIiIgOiAiIMK3IOKCuSBmaXhlZCJ9YDsKICBjb25zdCBsYWJlbCA9IHBlYWsgIT0gbnVsbCAmJiAodGllcnMgPiAwIHx8IGFjdHVhbFBlYWsgIT0gbnVsbCkgPyBgJHt0YWd9IMK3ICR7YmFzZX3ihpIke3BlYWt9TCR7Y2FwcGVkID8gIiAoY2FwKSIgOiAiIn1gIDogKGJhc2UgIT0gbnVsbCA/IGAke3RhZ30gwrcgZnJvbSAke2Jhc2V9TGAgOiB0YWcpOwogIHJldHVybiB7IG1vZGUsIC4uLnAsIGJhc2UsIG1heCwgc2NhbGVScywgY3BsOiAwLCB0aWVycywgcGVhaywgY2FwcGVkLCBtdWx0LCB0YWcsIGxhYmVsIH07Cn0KCi8vIOKUgOKUgCBMT1RfQ09NUF9FUV8yMDI2MDkyNCDilIDilIAgc2NvcmVib2FyZCBwZXIgQkFTRSBsb3Q6IGVhY2ggdHJhZGUncyBuZXQgaXMKLy8gZGl2aWRlZCBieSBpdHMgb3duIHNpemUgcmF0aW8gKHF0eSDDtyBiYXNlIHF0eSkgc28gYSBjb21wb3VuZGVkIHJ1biByZWFkcyBvbgovLyB0aGUgc2FtZSBzY2FsZSBhcyBpdHMgZmxhdCB0d2luLiBVbi1jb21wb3VuZGVkIHJ1bnM6IHJhdGlvIDEgZXZlcnl3aGVyZSwgc28KLy8gdGhlIGZpZ3VyZXMgZXF1YWwgdGhlIGhlYWRsaW5lIG5ldCAvIGRyYXdkb3duLgpleHBvcnQgZnVuY3Rpb24gcGVyQmFzZUxvdE9mKHRyYWRlcywgY2ZnLCBsb3RTaXplKSB7CiAgY29uc3QgYmFzZSA9IGJhc2VMb3RzT2YoY2ZnKTsKICBjb25zdCBiYXNlUXR5ID0gYmFzZSA+IDAgJiYgbG90U2l6ZSA+IDAgPyBiYXNlICogbG90U2l6ZSA6IDA7CiAgY29uc3QgY2xvc2VkID0gKHRyYWRlcyB8fCBbXSkuZmlsdGVyKCh0KSA9PiB0ICYmIHQuZXhpdF9wcmljZSAhPSBudWxsKTsKICBpZiAoIWNsb3NlZC5sZW5ndGgpIHJldHVybiBudWxsOwogIGNvbnN0IG5ldE9mID0gKHQpID0+ICh0Lm5ldF9wbmwgIT0gbnVsbCA/IE51bWJlcih0Lm5ldF9wbmwpIHx8IDAgOiAoTnVtYmVyKHQucG5sKSB8fCAwKSAtIChOdW1iZXIodC5jaGFyZ2VzKSB8fCAwKSk7CiAgY29uc3QgcmF0aW9PZiA9ICh0KSA9PiAoYmFzZVF0eSA+IDAgJiYgTnVtYmVyKHQucXR5KSA+IDAgPyBOdW1iZXIodC5xdHkpIC8gYmFzZVF0eSA6IDEpOwogIGNvbnN0IGJ5VGltZSA9IFsuLi5jbG9zZWRdLnNvcnQoKGEsIGIpID0+IChhLmVudHJ5X3RzIHx8IDApIC0gKGIuZW50cnlfdHMgfHwgMCkpOwogIGxldCBlcXVpdHkgPSAwLCBwZWFrID0gMCwgbWF4REQgPSAwLCBzY2FsZWQgPSAwOwogIGZvciAoY29uc3QgdCBvZiBieVRpbWUpIHsKICAgIGNvbnN0IHIgPSByYXRpb09mKHQpOwogICAgaWYgKHIgIT09IDEpIHNjYWxlZCArPSAxOwogICAgZXF1aXR5ICs9IG5ldE9mKHQpIC8gcjsKICAgIGlmIChlcXVpdHkgPiBwZWFrKSBwZWFrID0gZXF1aXR5OwogICAgaWYgKHBlYWsgLSBlcXVpdHkgPiBtYXhERCkgbWF4REQgPSBwZWFrIC0gZXF1aXR5OwogIH0KICByZXR1cm4geyBuZXQ6IGVxdWl0eSwgbWF4REQsIHJldHVyblRvREQ6IG1heEREID4gMCA/IGVxdWl0eSAvIG1heEREIDogbnVsbCwgc2NhbGVkVHJhZGVzOiBzY2FsZWQsIGJhc2VMb3RzOiBiYXNlLCBsb3RTaXplIH07Cn0KCg==",
    "subsection_old": "ICAgICAgICAgIHsvKiDilIDilIAgTE9UX0NPTVBfMjAyNjA5MjQgLyBMT1RfQ09NUF9NQVhfMjAyNjA5MjQg4pSA4pSAIExvdCBjb21wb3VuZGluZzogb25lCiAgICAgICAgICAgICAgc3ViLXNlY3Rpb24gZm9yIEVWRVJZIHN0cmF0ZWd5LCBmdWxsIHdpZHRoLCBiZWxvdyB0aGUgc3RyYXRlZ3kncyBvd24KICAgICAgICAgICAgICBwYXJhbWV0ZXJzIHNvIGl0IGlzIG5ldmVyIG1peGVkIGludG8gdGhlbS4gQmxhbmsgc3RlcCBvciBsb3RzID0gb2ZmLiAqL30KICAgICAgICAgIDxkaXYgc3R5bGU9e3sgZ3JpZENvbHVtbjogIjEgLyAtMSIsIG1hcmdpblRvcDogOCwgcGFkZGluZ1RvcDogMTAsIGJvcmRlclRvcDogYDFweCBzb2xpZCAke2NvbG9ycy5ib3JkZXIuZGFya31gIH19PgogICAgICAgICAgICA8ZGl2IHN0eWxlPXt7IC4uLnRtYVNlY0xhYmVsLCBtYXJnaW5Ub3A6IDAgfX0+TG90IGNvbXBvdW5kaW5nIDxzcGFuIHN0eWxlPXt7IHRleHRUcmFuc2Zvcm06ICJub25lIiwgbGV0dGVyU3BhY2luZzogMCwgZm9udFdlaWdodDogNDAwIH19PsK3IGFsbCBzdHJhdGVnaWVzIMK3IGJsYW5rID0gb2ZmPC9zcGFuPjwvZGl2PgogICAgICAgICAgICA8ZGl2IHN0eWxlPXt0bWFTZWNSb3d9PgogICAgICAgICAgICAgIDxGaWVsZCBsYWJlbD0iU3RlcCAobW9udGhzKSI+PGlucHV0IHR5cGU9Im51bWJlciIgbWluPSIwIiBzdGVwPSIxIiBwbGFjZWhvbGRlcj0ib2ZmIiBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogOTAgfX0gdmFsdWU9e2NvbXBTdGVwfSBvbkNoYW5nZT17KGUpID0+IHNldENvbXBTdGVwKGUudGFyZ2V0LnZhbHVlKX0gdGl0bGU9IkFkZCBsb3RzIGV2ZXJ5IE4gY2FsZW5kYXIgbW9udGhzIGZyb20gRGF0ZSBmcm9tIChhbm5pdmVyc2FyeS1iYXNlZCkuIEJsYW5rID0gb2ZmLiIgLz48L0ZpZWxkPgogICAgICAgICAgICAgIDxGaWVsZCBsYWJlbD0iTG90cyBwZXIgc3RlcCI+PGlucHV0IHR5cGU9Im51bWJlciIgbWluPSIwIiBzdGVwPSIxIiBwbGFjZWhvbGRlcj0ib2ZmIiBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogOTAgfX0gdmFsdWU9e2NvbXBBZGR9IG9uQ2hhbmdlPXsoZSkgPT4gc2V0Q29tcEFkZChlLnRhcmdldC52YWx1ZSl9IHRpdGxlPSJMb3RzIGFkZGVkIGF0IGVhY2ggc3RlcC4gRXZlcnkgbGVnIHNjYWxlcyBieSB0aGUgc2FtZSByYXRpbywgcm91bmRlZCwgbWluIDEuIiAvPjwvRmllbGQ+CiAgICAgICAgICAgICAgPEZpZWxkIGxhYmVsPSJNYXggbG90cyI+PGlucHV0IHR5cGU9Im51bWJlciIgbWluPSIwIiBzdGVwPSIxIiBwbGFjZWhvbGRlcj0ibm8gY2FwIiBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogOTAgfX0gdmFsdWU9e2NvbXBNYXh9IG9uQ2hhbmdlPXsoZSkgPT4gc2V0Q29tcE1heChlLnRhcmdldC52YWx1ZSl9IHRpdGxlPSJMYWRkZXIgY2FwIOKAlCB0aGUgbW9zdCBsb3RzIHRoZSBydW4gd2lsbCBldmVyIHRyYWRlIChtYXhpbXVtIHJpc2spLiBCbGFuayA9IG5vIGNhcDsgYSBjYXAgYmVsb3cgdGhlIGJhc2UgbG90cyBpcyBpZ25vcmVkLiIgLz48L0ZpZWxkPgogICAgICAgICAgICAgIDxGaWVsZCBsYWJlbD0i4oK5IGtub2JzIj4KICAgICAgICAgICAgICAgIDxzZWxlY3Qgc3R5bGU9e3sgLi4uaW5wdXRTdHlsZSwgd2lkdGg6IDE1MCB9fSB2YWx1ZT17Y29tcFNjYWxlUnMgPyAic2NhbGUiIDogImZpeGVkIn0gb25DaGFuZ2U9eyhlKSA9PiBzZXRDb21wU2NhbGVScyhlLnRhcmdldC52YWx1ZSAhPT0gImZpeGVkIil9CiAgICAgICAgICAgICAgICAgIHRpdGxlPSJTY2FsZSB3aXRoIGxvdHM6IE1UTSBTTC90YXJnZXQvdHJhaWwsIOKCuSBsb3NzIGNhcHMgYW5kIGxpbWl0cyBncm93IHdpdGggdGhlIHJhdGlvICh0aGUgc3RyYXRlZ3kgc2l6ZWQgdXApLiBLZWVwIGZpeGVkOiBzYW1lIOKCuSByaXNrIGJ1ZGdldCB3aGlsZSBsb3RzIGdyb3cuIj4KICAgICAgICAgICAgICAgICAgPG9wdGlvbiB2YWx1ZT0ic2NhbGUiPlNjYWxlIHdpdGggbG90czwvb3B0aW9uPgogICAgICAgICAgICAgICAgICA8b3B0aW9uIHZhbHVlPSJmaXhlZCI+S2VlcCBmaXhlZCDigrk8L29wdGlvbj4KICAgICAgICAgICAgICAgIDwvc2VsZWN0PgogICAgICAgICAgICAgIDwvRmllbGQ+CiAgICAgICAgICAgIDwvZGl2PgogICAgICAgICAgICA8ZGl2IHN0eWxlPXt7IGZvbnRTaXplOiAxMSwgY29sb3I6IGNvbG9ycy50ZXh0LnRlcnRpYXJ5LCBsaW5lSGVpZ2h0OiAxLjU1IH19PgogICAgICAgICAgICAgIHtjb21wUHJldmlldwogICAgICAgICAgICAgICAgPyA8PkxhZGRlcjogPGI+e2NvbXBQcmV2aWV3LmJhc2UgPz8gIj8ifSDihpIge2NvbXBQcmV2aWV3LnBlYWsgPz8gIj8ifSBsb3RzPC9iPiBvdmVyIHRoaXMgZGF0ZSByYW5nZSAoe2NvbXBQcmV2aWV3LnRpZXJzfSBzdGVwe2NvbXBQcmV2aWV3LnRpZXJzID09PSAxID8gIiIgOiAicyJ9e2NvbXBQcmV2aWV3LmNhcHBlZCA/IGAsIGNhcHBlZCBhdCAke2NvbXBQcmV2aWV3Lm1heH1gIDogIiJ9KTsgbmV3IGVudHJpZXMgb25seSDigJQgb3BlbiBwb3NpdGlvbnMga2VlcCB0aGVpciBlbnRyeSBsb3RzOyB7Y29tcFByZXZpZXcuc2NhbGVScyA/ICLigrkga25vYnMgc2NhbGUgd2l0aCB0aGUgcmF0aW8iIDogIuKCuSBrbm9icyBzdGF5IGZpeGVkIn0uPC8+CiAgICAgICAgICAgICAgICA6IDw+T2ZmIOKAlCBmbGF0IGxvdHMgZm9yIHRoZSB3aG9sZSBydW4uIFNldCBhIHN0ZXAgYW5kIGxvdHMgcGVyIHN0ZXAgdG8gZ3JvdyBzaXplIG92ZXIgdGltZSAoZS5nLiAzIG1vbnRocywgKzEgbG90KS48Lz59CiAgICAgICAgICAgIDwvZGl2PgogICAgICAgICAgPC9kaXY+Cg==", "subsection_new": "ICAgICAgICAgIHsvKiDilIDilIAgTE9UX0NPTVBfMjAyNjA5MjQgLyBMT1RfQ09NUF9NQVhfMjAyNjA5MjQgLyBMT1RfQ09NUF9FUV8yMDI2MDkyNCDilIDilIAKICAgICAgICAgICAgICBMb3QgY29tcG91bmRpbmc6IG9uZSBzdWItc2VjdGlvbiBmb3IgRVZFUlkgc3RyYXRlZ3ksIGZ1bGwgd2lkdGgsCiAgICAgICAgICAgICAgYmVsb3cgdGhlIHN0cmF0ZWd5J3Mgb3duIHBhcmFtZXRlcnMgc28gaXQgaXMgbmV2ZXIgbWl4ZWQgaW50bwogICAgICAgICAgICAgIHRoZW0uIFR3byBtb2RlcyDigJQgY2FsZW5kYXIgc3RlcCAoYmxhbmsgc3RlcCBvciBsb3RzID0gb2ZmKSBhbmQKICAgICAgICAgICAgICBlcXVpdHktYmFzZWQgKGxvdHMgZm9sbG93IHJlYWxpc2VkIFAmTCDDtyBjYXBpdGFsIHBlciBsb3QpLiAqL30KICAgICAgICAgIDxkaXYgc3R5bGU9e3sgZ3JpZENvbHVtbjogIjEgLyAtMSIsIG1hcmdpblRvcDogOCwgcGFkZGluZ1RvcDogMTAsIGJvcmRlclRvcDogYDFweCBzb2xpZCAke2NvbG9ycy5ib3JkZXIuZGFya31gIH19PgogICAgICAgICAgICA8ZGl2IHN0eWxlPXt7IC4uLnRtYVNlY0xhYmVsLCBtYXJnaW5Ub3A6IDAgfX0+TG90IGNvbXBvdW5kaW5nIDxzcGFuIHN0eWxlPXt7IHRleHRUcmFuc2Zvcm06ICJub25lIiwgbGV0dGVyU3BhY2luZzogMCwgZm9udFdlaWdodDogNDAwIH19PsK3IGFsbCBzdHJhdGVnaWVzIMK3IHtjb21wTW9kZSA9PT0gImVxdWl0eSIgPyAiZXF1aXR5LWJhc2VkIHNpemluZyIgOiAiYmxhbmsgPSBvZmYifTwvc3Bhbj48L2Rpdj4KICAgICAgICAgICAgPGRpdiBzdHlsZT17dG1hU2VjUm93fT4KICAgICAgICAgICAgICA8RmllbGQgbGFiZWw9Ik1vZGUiPgogICAgICAgICAgICAgICAgPHNlbGVjdCBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogMTUwIH19IHZhbHVlPXtjb21wTW9kZX0gb25DaGFuZ2U9eyhlKSA9PiBzZXRDb21wTW9kZShlLnRhcmdldC52YWx1ZSA9PT0gImVxdWl0eSIgPyAiZXF1aXR5IiA6ICJjYWxlbmRhciIpfQogICAgICAgICAgICAgICAgICB0aXRsZT0iQ2FsZW5kYXIgc3RlcDogYWRkIGxvdHMgZXZlcnkgTiBtb250aHMuIEVxdWl0eS1iYXNlZDogbG90cyA9IGZsb29yKGVxdWl0eSDDtyBjYXBpdGFsIHBlciBsb3QpLCB3aGVyZSBlcXVpdHkgPSBiYXNlIGxvdHMgw5cgY2FwaXRhbCBwZXIgbG90ICsgcmVhbGlzZWQgbmV0IHNvIGZhci4iPgogICAgICAgICAgICAgICAgICA8b3B0aW9uIHZhbHVlPSJjYWxlbmRhciI+Q2FsZW5kYXIgc3RlcDwvb3B0aW9uPgogICAgICAgICAgICAgICAgICA8b3B0aW9uIHZhbHVlPSJlcXVpdHkiPkVxdWl0eS1iYXNlZDwvb3B0aW9uPgogICAgICAgICAgICAgICAgPC9zZWxlY3Q+CiAgICAgICAgICAgICAgPC9GaWVsZD4KICAgICAgICAgICAgICB7Y29tcE1vZGUgPT09ICJlcXVpdHkiID8gKAogICAgICAgICAgICAgICAgPEZpZWxkIGxhYmVsPSJDYXBpdGFsIHBlciBsb3QgKOKCuSkiPjxpbnB1dCB0eXBlPSJudW1iZXIiIG1pbj0iMCIgc3RlcD0iMTAwMCIgcGxhY2Vob2xkZXI9ImUuZy4gMTUwMDAwIiBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogMTIwIH19IHZhbHVlPXtjb21wQ3BsfSBvbkNoYW5nZT17KGUpID0+IHNldENvbXBDcGwoZS50YXJnZXQudmFsdWUpfSB0aXRsZT0iUnVwZWVzIG9mIGNhcGl0YWwgdGhhdCBmdW5kIE9ORSBsb3QuIFN0YXJ0aW5nIGVxdWl0eSA9IGJhc2UgbG90cyDDlyB0aGlzOyBsb3RzIGFyZSByZWNvbXB1dGVkIGVhY2ggZGF5IGZyb20gcmVhbGlzZWQgbmV0ICh0cmFkZXMgY2xvc2VkIGJlZm9yZSB0aGF0IGRheSksIG1pbiAxIGxvdC4gQmxhbmsgPSBvZmYuIiAvPjwvRmllbGQ+CiAgICAgICAgICAgICAgKSA6ICgKICAgICAgICAgICAgICAgIDw+CiAgICAgICAgICAgICAgICAgIDxGaWVsZCBsYWJlbD0iU3RlcCAobW9udGhzKSI+PGlucHV0IHR5cGU9Im51bWJlciIgbWluPSIwIiBzdGVwPSIxIiBwbGFjZWhvbGRlcj0ib2ZmIiBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogOTAgfX0gdmFsdWU9e2NvbXBTdGVwfSBvbkNoYW5nZT17KGUpID0+IHNldENvbXBTdGVwKGUudGFyZ2V0LnZhbHVlKX0gdGl0bGU9IkFkZCBsb3RzIGV2ZXJ5IE4gY2FsZW5kYXIgbW9udGhzIGZyb20gRGF0ZSBmcm9tIChhbm5pdmVyc2FyeS1iYXNlZCkuIEJsYW5rID0gb2ZmLiIgLz48L0ZpZWxkPgogICAgICAgICAgICAgICAgICA8RmllbGQgbGFiZWw9IkxvdHMgcGVyIHN0ZXAiPjxpbnB1dCB0eXBlPSJudW1iZXIiIG1pbj0iMCIgc3RlcD0iMSIgcGxhY2Vob2xkZXI9Im9mZiIgc3R5bGU9e3sgLi4uaW5wdXRTdHlsZSwgd2lkdGg6IDkwIH19IHZhbHVlPXtjb21wQWRkfSBvbkNoYW5nZT17KGUpID0+IHNldENvbXBBZGQoZS50YXJnZXQudmFsdWUpfSB0aXRsZT0iTG90cyBhZGRlZCBhdCBlYWNoIHN0ZXAuIEV2ZXJ5IGxlZyBzY2FsZXMgYnkgdGhlIHNhbWUgcmF0aW8sIHJvdW5kZWQsIG1pbiAxLiIgLz48L0ZpZWxkPgogICAgICAgICAgICAgICAgPC8+CiAgICAgICAgICAgICAgKX0KICAgICAgICAgICAgICA8RmllbGQgbGFiZWw9Ik1heCBsb3RzIj48aW5wdXQgdHlwZT0ibnVtYmVyIiBtaW49IjAiIHN0ZXA9IjEiIHBsYWNlaG9sZGVyPSJubyBjYXAiIHN0eWxlPXt7IC4uLmlucHV0U3R5bGUsIHdpZHRoOiA5MCB9fSB2YWx1ZT17Y29tcE1heH0gb25DaGFuZ2U9eyhlKSA9PiBzZXRDb21wTWF4KGUudGFyZ2V0LnZhbHVlKX0gdGl0bGU9IkxhZGRlciBjYXAg4oCUIHRoZSBtb3N0IGxvdHMgdGhlIHJ1biB3aWxsIGV2ZXIgdHJhZGUgKG1heGltdW0gcmlzaykuIEJsYW5rID0gbm8gY2FwOyBhIGNhcCBiZWxvdyB0aGUgYmFzZSBsb3RzIGlzIGlnbm9yZWQuIiAvPjwvRmllbGQ+CiAgICAgICAgICAgICAgPEZpZWxkIGxhYmVsPSLigrkga25vYnMiPgogICAgICAgICAgICAgICAgPHNlbGVjdCBzdHlsZT17eyAuLi5pbnB1dFN0eWxlLCB3aWR0aDogMTUwIH19IHZhbHVlPXtjb21wU2NhbGVScyA/ICJzY2FsZSIgOiAiZml4ZWQifSBvbkNoYW5nZT17KGUpID0+IHNldENvbXBTY2FsZVJzKGUudGFyZ2V0LnZhbHVlICE9PSAiZml4ZWQiKX0KICAgICAgICAgICAgICAgICAgdGl0bGU9IlNjYWxlIHdpdGggbG90czogTVRNIFNML3RhcmdldC90cmFpbCwg4oK5IGxvc3MgY2FwcyBhbmQgbGltaXRzIGdyb3cgd2l0aCB0aGUgcmF0aW8gKHRoZSBzdHJhdGVneSBzaXplZCB1cCkuIEtlZXAgZml4ZWQ6IHNhbWUg4oK5IHJpc2sgYnVkZ2V0IHdoaWxlIGxvdHMgZ3Jvdy4iPgogICAgICAgICAgICAgICAgICA8b3B0aW9uIHZhbHVlPSJzY2FsZSI+U2NhbGUgd2l0aCBsb3RzPC9vcHRpb24+CiAgICAgICAgICAgICAgICAgIDxvcHRpb24gdmFsdWU9ImZpeGVkIj5LZWVwIGZpeGVkIOKCuTwvb3B0aW9uPgogICAgICAgICAgICAgICAgPC9zZWxlY3Q+CiAgICAgICAgICAgICAgPC9GaWVsZD4KICAgICAgICAgICAgPC9kaXY+CiAgICAgICAgICAgIDxkaXYgc3R5bGU9e3sgZm9udFNpemU6IDExLCBjb2xvcjogY29sb3JzLnRleHQudGVydGlhcnksIGxpbmVIZWlnaHQ6IDEuNTUgfX0+CiAgICAgICAgICAgICAge2NvbXBQcmV2aWV3CiAgICAgICAgICAgICAgICA/IChjb21wUHJldmlldy5tb2RlID09PSAiZXF1aXR5IgogICAgICAgICAgICAgICAgICA/IDw+RXF1aXR5IHNpemluZzogc3RhcnQgPGI+e2NvbXBQcmV2aWV3LmJhc2UgPz8gIj8ifSBsb3RzPC9iPiBhdCDigrl7Zm10TChjb21wUHJldmlldy5jcGwpfUwgcGVyIGxvdCAo4oK5e2ZtdEwoKGNvbXBQcmV2aWV3LmJhc2UgfHwgMCkgKiBjb21wUHJldmlldy5jcGwpfUwpOyBlYWNoIGRheSBsb3RzID0g4oyKZXF1aXR5IMO3IGNhcGl0YWwgcGVyIGxvdOKMiywgbWluIDF7Y29tcFByZXZpZXcubWF4ID8gYCwgbWF4ICR7Y29tcFByZXZpZXcubWF4fWAgOiAiIn07IGVxdWl0eSBjb3VudHMgdHJhZGVzIGNsb3NlZCBiZWZvcmUgdGhhdCBkYXkgKG9wZW4gcG9zaXRpb25zIGV4Y2x1ZGVkKTsgbmV3IGVudHJpZXMgb25seTsge2NvbXBQcmV2aWV3LnNjYWxlUnMgPyAi4oK5IGtub2JzIHNjYWxlIHdpdGggdGhlIHJhdGlvIiA6ICLigrkga25vYnMgc3RheSBmaXhlZCJ9OyBydW5zIHNlcmlhbGx5IChwYXJhbGxlbCB3b3JrZXJzIGlnbm9yZWQpLjwvPgogICAgICAgICAgICAgICAgICA6IDw+TGFkZGVyOiA8Yj57Y29tcFByZXZpZXcuYmFzZSA/PyAiPyJ9IOKGkiB7Y29tcFByZXZpZXcucGVhayA/PyAiPyJ9IGxvdHM8L2I+IG92ZXIgdGhpcyBkYXRlIHJhbmdlICh7Y29tcFByZXZpZXcudGllcnN9IHN0ZXB7Y29tcFByZXZpZXcudGllcnMgPT09IDEgPyAiIiA6ICJzIn17Y29tcFByZXZpZXcuY2FwcGVkID8gYCwgY2FwcGVkIGF0ICR7Y29tcFByZXZpZXcubWF4fWAgOiAiIn0pOyBuZXcgZW50cmllcyBvbmx5IOKAlCBvcGVuIHBvc2l0aW9ucyBrZWVwIHRoZWlyIGVudHJ5IGxvdHM7IHtjb21wUHJldmlldy5zY2FsZVJzID8gIuKCuSBrbm9icyBzY2FsZSB3aXRoIHRoZSByYXRpbyIgOiAi4oK5IGtub2JzIHN0YXkgZml4ZWQifS48Lz4pCiAgICAgICAgICAgICAgICA6IDw+T2ZmIOKAlCBmbGF0IGxvdHMgZm9yIHRoZSB3aG9sZSBydW4uIFNldCBhIHN0ZXAgYW5kIGxvdHMgcGVyIHN0ZXAgKGUuZy4gMyBtb250aHMsICsxIGxvdCksIG9yIHN3aXRjaCB0byBlcXVpdHktYmFzZWQgc2l6aW5nLjwvPn0KICAgICAgICAgICAgPC9kaXY+CiAgICAgICAgICA8L2Rpdj4K",
}


def frag(k):
    return base64.b64decode(FRAG_B64[k]).decode("utf-8")


EDITS = {}


def E(path, *ops):
    EDITS.setdefault(path, []).extend(ops)


IMPORT_EQ = "from app.backtest.engine.lot_compounding import lot_comp_is_equity as _lot_comp_is_equity   # ── LOT_COMP_EQ_20260924 ──\n"

# ═══════════════════════════ backend: shared module ═══════════════════════
E(f"{BACKEND}/engine/lot_compounding.py",
  ("replace", 'KEY_SCALE_RS = "lot_comp_scale_rs"   # bool, absent = True (₹ knobs scale with lots)\n',
   'KEY_SCALE_RS = "lot_comp_scale_rs"   # bool, absent = True (₹ knobs scale with lots)\n'
   "# ── LOT_COMP_EQ_20260924 ── equity-based sizing\n"
   'KEY_MODE = "lot_comp_mode"                 # "calendar" (default) | "equity"\n'
   'KEY_CPL = "lot_comp_capital_per_lot"       # ₹ that fund ONE lot (equity mode)\n'
   "\n\n"
   "def lot_comp_is_equity(cfg) -> bool:\n"
   '    """True when the config asks for equity-based sizing with a usable\n'
   '    capital-per-lot. Path-dependent: sharded runners must go serial."""\n'
   "    if not isinstance(cfg, dict):\n"
   "        return False\n"
   '    if str(cfg.get(KEY_MODE) or "calendar").strip().lower() != "equity":\n'
   "        return False\n"
   "    try:\n"
   "        return float(cfg.get(KEY_CPL) or 0) > 0\n"
   "    except (TypeError, ValueError):\n"
   "        return False\n"
   "\n\n"
   "def _tget(t, k):\n"
   "    return t.get(k) if isinstance(t, dict) else getattr(t, k, None)\n"
   "\n\n"
   "def _trade_net(t):\n"
   '    """Realised NET of a CLOSED trade object of any runner\'s shape; None for\n'
   '    an open one (exit not yet booked). net_pnl → net → (pnl|gross) − charges."""\n'
   '    if _tget(t, "exit_price") is None and _tget(t, "exit_ts") is None:\n'
   "        return None\n"
   '    for k in ("net_pnl", "net"):\n'
   "        v = _tget(t, k)\n"
   "        if v is not None:\n"
   "            try:\n"
   "                return float(v)\n"
   "            except (TypeError, ValueError):\n"
   "                return None\n"
   '    g = _tget(t, "pnl")\n'
   "    if g is None:\n"
   '        g = _tget(t, "gross")\n'
   "    if g is None:\n"
   "        return None\n"
   "    try:\n"
   '        return float(g) - float(_tget(t, "charges") or 0.0)\n'
   "    except (TypeError, ValueError):\n"
   "        return None\n"),
  ("replace", '    __slots__ = ("step", "add", "start", "base", "on", "_tier_cache", "_seen",\n'
              '                 "max_lots", "scale_rs_on")   # ── LOT_COMP_MAX_20260924 ──\n',
   '    __slots__ = ("step", "add", "start", "base", "on", "_tier_cache", "_seen",\n'
   '                 "max_lots", "scale_rs_on",   # ── LOT_COMP_MAX_20260924 ──\n'
   '                 "mode", "cpl", "_eq_lots", "_eq_day", "_eq_equity", "_eq_ladder")   # ── LOT_COMP_EQ_20260924 ──\n'),
  ("replace", "        self.on = (self.step > 0 and self.add > 0 and self.base > 0\n"
              "                   and self.start is not None)\n",
   "        # ── LOT_COMP_EQ_20260924 ── sizing mode. Equity mode ignores step/add;\n"
   "        # lots follow realised P&L via begin_day(). Same cap / ₹-knob switch.\n"
   '        self.mode = ("equity" if isinstance(cfg, dict)\n'
   '                     and str(cfg.get(KEY_MODE) or "calendar").strip().lower() == "equity"\n'
   '                     else "calendar")\n'
   "        try:\n"
   "            self.cpl = float((cfg or {}).get(KEY_CPL) or 0) if isinstance(cfg, dict) else 0.0\n"
   "        except (TypeError, ValueError):\n"
   "            self.cpl = 0.0\n"
   "        if self.cpl <= 0:\n"
   "            self.cpl = 0.0\n"
   '        if self.mode == "equity":\n'
   "            self.on = self.base > 0 and self.cpl > 0\n"
   "        else:\n"
   "            self.on = (self.step > 0 and self.add > 0 and self.base > 0\n"
   "                       and self.start is not None)\n"
   "        self._eq_lots = self.base\n"
   "        self._eq_day = None\n"
   "        self._eq_equity = float(self.base) * self.cpl\n"
   "        self._eq_ladder: List[tuple] = []   # (day, lots) at each change\n"),
  ("replace", "    def tier(self, day) -> int:\n        if not self.on:\n            return 0\n",
   '    def tier(self, day) -> int:\n        if not self.on or self.mode == "equity":   # ── LOT_COMP_EQ_20260924 ── no calendar tiers\n            return 0\n'),
  ("replace", '        """Configured-lots equivalent for this day (base + tier × add)."""\n'
              "        if not self.on:\n            return self.base\n"
              "        n = self.base + self.tier(day) * self.add\n",
   '        """Configured-lots equivalent for this day (base + tier × add), or the\n'
   '        equity-sized lots set by begin_day() in equity mode."""\n'
   "        if not self.on:\n            return self.base\n"
   '        if self.mode == "equity":   # ── LOT_COMP_EQ_20260924 ──\n'
   "            return self._eq_lots\n"
   "        n = self.base + self.tier(day) * self.add\n"),
  ("replace", "    # ── reporting ─────────────────────────────────────────────────────\n    def peak_lots(self, date_to) -> int:\n        d = _as_date(date_to)\n        if not self.on or d is None:\n            return self.base\n",
   "    # ── LOT_COMP_EQ_20260924 ── equity-based sizing ────────────────────\n"
   "    def equity_lots(self, realised_net) -> int:\n"
   '        """lots = ⌊(base × cpl + realised_net) ÷ cpl⌋, min 1, capped."""\n'
   "        if not (self.on and self.cpl > 0):\n"
   "            return self.base\n"
   "        try:\n"
   "            eqty = float(self.base) * self.cpl + float(realised_net or 0.0)\n"
   "        except (TypeError, ValueError):\n"
   "            eqty = float(self.base) * self.cpl\n"
   "        n = int(eqty // self.cpl)\n"
   "        n = max(1, n)\n"
   "        return min(n, self.max_lots) if self.max_lots else n\n"
   "\n"
   "    def begin_day(self, day, trades) -> int:\n"
   '        """Call once per sim day BEFORE sizing (a second call for the same day\n'
   "        is a no-op). Equity mode: re-sizes from the realised net of every\n"
   "        CLOSED trade in `trades` (open ones are skipped — their exit is not\n"
   "        booked yet). Calendar mode / OFF: returns today's lots unchanged.\n"
   '        Runners pass their own trade list (any shape; see _trade_net)."""\n'
   '        if not (self.on and self.mode == "equity"):\n'
   "            return self.lots(day)\n"
   "        d = _as_date(day)\n"
   "        if d is None or d == self._eq_day:\n"
   "            return self._eq_lots\n"
   "        net = 0.0\n"
   "        for t in (trades or ()):\n"
   "            v = _trade_net(t)\n"
   "            if v is not None:\n"
   "                net += v\n"
   "        self._eq_day = d\n"
   "        self._eq_equity = float(self.base) * self.cpl + net\n"
   "        n = self.equity_lots(net)\n"
   "        if n != self._eq_lots or not self._eq_ladder:\n"
   "            self._eq_ladder.append((d, n))\n"
   "        self._eq_lots = n\n"
   "        return n\n"
   "\n"
   "    # ── reporting ─────────────────────────────────────────────────────\n"
   "    def peak_lots(self, date_to) -> int:\n"
   "        d = _as_date(date_to)\n"
   "        if not self.on or d is None:\n"
   "            return self.base\n"
   '        if self.mode == "equity":   # ── LOT_COMP_EQ_20260924 ── what the run reached so far\n'
   "            return max([n for _, n in self._eq_ladder] or [self.base])\n"),
  ("replace", '        if not self.on:\n            return {"on": False}\n        return {\n            "on": True, "step_months": self.step, "add_lots": self.add,\n',
   '        if not self.on:\n            return {"on": False}\n'
   '        if self.mode == "equity":   # ── LOT_COMP_EQ_20260924 ──\n'
   "            return {\n"
   '                "on": True, "mode": "equity", "capital_per_lot": self.cpl,\n'
   '                "base_lots": self.base, "max_lots": self.max_lots or None,\n'
   '                "scale_rs": self.scale_rs_on, "equity": self._eq_equity,\n'
   '                "ladder": [{"from": d.isoformat(), "lots": n} for d, n in self._eq_ladder],\n'
   "            }\n"
   "        return {\n"
   '            "on": True, "mode": "calendar", "step_months": self.step, "add_lots": self.add,\n'),
  ("replace", '        if not self.on:\n            return "lot_comp OFF"\n',
   '        if not self.on:\n            return "lot_comp OFF"\n'
   '        if self.mode == "equity":   # ── LOT_COMP_EQ_20260924 ──\n'
   '            return (f"lot_comp equity cpl={self.cpl:.0f} base={self.base}"\n'
   '                    f"{(\' max=\' + str(self.max_lots)) if self.max_lots else \'\'}"\n'
   '                    f"{\'\' if self.scale_rs_on else \' rs=fixed\'}")\n'))

# ═══════════════════════════ backend: runners — begin_day feed ═════════════
_BD = "        _comp.begin_day({day}, {trades})   # ── LOT_COMP_EQ_20260924 ── equity mode re-sizes from realised net\n"


def _feed(rel, line, day, trades):
    E(rel, ("replace", line, _BD.format(day=day, trades=trades) + line))


_feed(f"{BACKEND}/bb/backtest_bb_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty (EOD-boxed book)\n", "sim_day", "trades")
_feed(f"{BACKEND}/brk/backtest_brk_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty (on top of DTE)\n", "day", "trades")
_feed(f"{BACKEND}/cbo/backtest_cbo_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty + ₹ caps\n", "day", "trades")
_feed(f"{BACKEND}/fvg/backtest_fvg_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty (on top of DTE)\n", "d", "trades")
_feed(f"{BACKEND}/gc/backtest_gc_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty + ₹ caps (EOD-boxed book)\n", "d", "trades")
_feed(f"{BACKEND}/ha/backtest_ha_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty + ₹ day caps\n", "d", "trades")
_feed(f"{BACKEND}/ic/backtest_ic_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's lots for NEW entries (carried legs keep theirs)\n", "d", "trades")
_feed(f"{BACKEND}/orb/backtest_orb_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty (on top of DTE)\n", "day", "trades")
_feed(f"{BACKEND}/orv/backtest_orv_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty\n", "day", "trades")
_feed(f"{BACKEND}/runner/backtest_hedge_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty + ₹ risk limits\n", "day", "book.closed")
_feed(f"{BACKEND}/runner/backtest_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's size (day-boxed book)\n", "day", "book.closed_trades()")
_feed(f"{BACKEND}/scalpv5/backtest_scalpv5_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty; the RUN-cumulative ₹ cap below\n", "d", "trades")
_feed(f"{BACKEND}/stfc/backtest_stfc_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty (on top of DTE)\n", "day", "trades")
_feed(f"{BACKEND}/tma/backtest_tma_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── NEW entries only; carried spreads keep their lots\n", "d", "trades")
_feed(f"{BACKEND}/tma/backtest_tma_v2_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── NEW entries only (on top of DTE); carried spreads keep their lots\n", "d", "trades")
_feed(f"{BACKEND}/tsg/backtest_tsg_runner.py", "        # ── LOT_COMP_20260924 ── today's leg lots (on top of DTE) + ₹ basket knobs\n        if _comp.on:\n", "d", "trades")
_feed(f"{BACKEND}/vap/backtest_vap_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── NEW entries only; carried positions keep their lots\n", "d", "trades")
_feed(f"{BACKEND}/vet/backtest_vet_runner.py", "        if _comp.on:   # ── LOT_COMP_20260924 ── today's qty for NEW entries + ₹ day cap\n", "d", "trades")

# sharded runners: equity mode is path-dependent → serial
E(f"{BACKEND}/ic/backtest_ic_runner.py",
  ("replace", "    if parallel_workers > 1 and len(sim_days) >= parallel_workers * 2:\n",
   '    if parallel_workers > 1 and len(sim_days) >= parallel_workers * 2 and _comp.mode != "equity":   # ── LOT_COMP_EQ_20260924 ── equity sizing runs serially\n'))
E(f"{BACKEND}/tsg/backtest_tsg_runner.py",
  ("replace", "    if parallel_workers > 1 and len(sim_days) >= parallel_workers * 2:\n",
   '    if parallel_workers > 1 and len(sim_days) >= parallel_workers * 2 and _comp.mode != "equity":   # ── LOT_COMP_EQ_20260924 ── equity sizing runs serially\n'))
E(f"{BACKEND}/runner/backtest_runner.py",
  ("replace", "    if _n_workers > 1:\n",
   "    " + IMPORT_EQ +
   "    if _n_workers > 1 and not _lot_comp_is_equity(cfg):   # ── LOT_COMP_EQ_20260924 ── equity sizing runs serially\n"))
E(f"{BACKEND}/runner/backtest_hedge_runner.py",
  ("replace", "    if _n_workers > 1:\n",
   "    " + IMPORT_EQ +
   "    if _n_workers > 1 and not _lot_comp_is_equity(cfg):   # ── LOT_COMP_EQ_20260924 ── equity sizing runs serially\n"))

# persist_run: qty envelope on the summary (actual peak lots for the UI)
E(f"{BACKEND}/repo/backtest_repo.py",
  ("replace", '    s = result["summary"]\n    cfg = result.get("config", {})\n    trades = result.get("trades", [])\n',
   '    s = result["summary"]\n    cfg = result.get("config", {})\n    trades = result.get("trades", [])\n'
   "    # ── LOT_COMP_EQ_20260924 ── qty envelope so the Compare page can show the\n"
   "    # run's ACTUAL lot ladder (peak lots) without loading every trade.\n"
   "    try:\n"
   "        _qs = []\n"
   "        for _t in trades:\n"
   '            _q = _t.get("qty") if isinstance(_t, dict) else getattr(_t, "qty", None)\n'
   "            if _q:\n"
   "                _qs.append(int(_q))\n"
   "        if _qs and isinstance(s, dict):\n"
   "            s = dict(s, qty_min=min(_qs), qty_max=max(_qs))\n"
   "    except Exception:\n"
   "        pass\n"))

# ═══════════════════════════ frontend: helper ═════════════════════════════
E(f"{FRONTEND}/backtest/lotCompounding.js",
  ("replace", frag("lotcompof_old"), frag("lotcompof_new")))

# ═══════════════════════════ frontend: Backtest.jsx ═══════════════════════
E(f"{FRONTEND}/Backtest.jsx",
  ("replace", 'import { lotCompChip, lotCompOf } from "./backtest/lotCompounding";   // ── LOT_COMP_20260924 ── ── LOT_COMP_MAX_20260924 ──',
   'import { lotCompChip, lotCompOf, lotSizeOf, fmtL } from "./backtest/lotCompounding";   // ── LOT_COMP_20260924 ── ── LOT_COMP_MAX_20260924 ── ── LOT_COMP_EQ_20260924 ──'),
  ("replace", '  const [compScaleRs, setCompScaleRs] = useState(() => loadLotComp().scaleRs ?? true);   // ── LOT_COMP_MAX_20260924 ──\n'
              "  useEffect(() => {\n"
              "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs })); } catch { /* ignore */ }\n"
              "  }, [compStep, compAdd, compMax, compScaleRs]);\n",
   '  const [compScaleRs, setCompScaleRs] = useState(() => loadLotComp().scaleRs ?? true);   // ── LOT_COMP_MAX_20260924 ──\n'
   '  const [compMode, setCompMode] = useState(() => (loadLotComp().mode === "equity" ? "equity" : "calendar"));   // ── LOT_COMP_EQ_20260924 ──\n'
   '  const [compCpl, setCompCpl] = useState(() => loadLotComp().cpl ?? "");   // ── LOT_COMP_EQ_20260924 ── capital per lot (₹)\n'
   "  useEffect(() => {\n"
   "    try { localStorage.setItem(LOT_COMP_LS_KEY, JSON.stringify({ step: compStep, add: compAdd, max: compMax, scaleRs: compScaleRs, mode: compMode, cpl: compCpl })); } catch { /* ignore */ }\n"
   "  }, [compStep, compAdd, compMax, compScaleRs, compMode, compCpl]);\n"),
  ("replace", "    const step = Math.floor(Number(compStep)) || 0, add = Math.floor(Number(compAdd)) || 0;\n"
              "    if (!base || step <= 0 || add <= 0) return base;\n"
              "    // ── LOT_COMP_MAX_20260924 ── cap only when set; scale_rs only when OFF\n"
              "    // (absent = true), so a default form still emits exactly the two keys.\n"
              "    const max = Math.floor(Number(compMax)) || 0;\n"
              "    return { ...base, lot_comp_step_months: step, lot_comp_add_lots: add,\n"
              "      ...(max > 0 ? { lot_comp_max_lots: max } : {}),\n"
              "      ...(compScaleRs ? {} : { lot_comp_scale_rs: false }) };\n"
              "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs]);\n",
   "    const step = Math.floor(Number(compStep)) || 0, add = Math.floor(Number(compAdd)) || 0;\n"
   "    // ── LOT_COMP_MAX_20260924 ── cap only when set; scale_rs only when OFF\n"
   "    // (absent = true), so a default form still emits exactly the two keys.\n"
   "    const max = Math.floor(Number(compMax)) || 0;\n"
   "    const extras = { ...(max > 0 ? { lot_comp_max_lots: max } : {}), ...(compScaleRs ? {} : { lot_comp_scale_rs: false }) };\n"
   '    // ── LOT_COMP_EQ_20260924 ── equity mode: mode + capital per lot, no step/add\n'
   '    if (compMode === "equity") {\n'
   "      const cpl = Math.floor(Number(compCpl)) || 0;\n"
   "      if (!base || cpl <= 0) return base;\n"
   '      return { ...base, lot_comp_mode: "equity", lot_comp_capital_per_lot: cpl, ...extras };\n'
   "    }\n"
   "    if (!base || step <= 0 || add <= 0) return base;\n"
   "    return { ...base, lot_comp_step_months: step, lot_comp_add_lots: add, ...extras };\n"
   "  }, [buildConfigBase, compStep, compAdd, compMax, compScaleRs, compMode, compCpl]);\n"),
  ("replace", frag("subsection_old"), frag("subsection_new")),
  # results table: Lots column after Exit ₹
  ("replace", '                          ? ["Signal", "Hedge", "Entry", "Hedge ₹", "Hedge SL", "Exit", "Exit ₹", "Reason", "Gross", "Charges", "Net", "Amb"]\n'
              '                          : ["Symbol", "Cond", "Entry", "Entry ₹", "SL", "TP", "Exit", "Exit ₹", "Reason", "Gross", "Charges", "Net", "Amb"]\n',
   '                          ? ["Signal", "Hedge", "Entry", "Hedge ₹", "Hedge SL", "Exit", "Exit ₹", "Lots", "Reason", "Gross", "Charges", "Net", "Amb"]   // ── LOT_COMP_EQ_20260924 ── Lots\n'
   '                          : ["Symbol", "Cond", "Entry", "Entry ₹", "SL", "TP", "Exit", "Exit ₹", "Lots", "Reason", "Gross", "Charges", "Net", "Amb"]\n'),
  ("replace", '                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right" }}>{t.exit_price?.toFixed(2)}</td>\n',
   '                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right" }}>{t.exit_price?.toFixed(2)}</td>\n'
   "                          {/* ── LOT_COMP_EQ_20260924 ── lots traded on THIS row (qty ÷ lot size;\n"
   "                              raw qty with a q suffix when the lot size is unknown) */}\n"
   '                          <td style={{ padding: "8px", ...typography.mono, textAlign: "right", color: colors.text.secondary }} title={`qty ${t.qty ?? "—"}`}>\n'
   "                            {(() => {\n"
   "                              const q = Number(t.qty) || 0;\n"
   '                              if (!q) return "—";\n'
   "                              const ls = lotSizeOf(resultConfig, resultStrategy, resultConfig?.underlying);\n"
   '                              return ls && q % ls === 0 ? `${q / ls}L` : `${q}q`;\n'
   "                            })()}\n"
   "                          </td>\n"))

# ═══════════════════════════ frontend: RunComparison.jsx ══════════════════
E(f"{FRONTEND}/backtest/RunComparison.jsx",
  ("replace", 'import { lotCompOf, lotCompChip, scaleLots } from "./lotCompounding";   // ── LOT_COMP_20260924 ──',
   'import { lotCompOf, lotCompChip, scaleLots, lotSizeOf, perBaseLotOf } from "./lotCompounding";   // ── LOT_COMP_20260924 ── ── LOT_COMP_EQ_20260924 ──'),
  ("replace", "  const lc = lotCompOf(run?.config, run?.date_from, run?.date_to);\n",
   "  const lc = lotCompOf(run?.config, run?.date_from, run?.date_to, run?.summary);   // ── LOT_COMP_EQ_20260924 ── actual peak when persisted\n"),
  ("replace_all:3", "lotCompOf(r?.config, r?.date_from, r?.date_to)", "lotCompOf(r?.config, r?.date_from, r?.date_to, r?.summary)"),
  ("replace", "      const trades = d.trades || [];\n      const metrics = computeMetrics(trades);\n",
   "      const trades = d.trades || [];\n      const metrics = computeMetrics(trades);\n"
   "      // ── LOT_COMP_EQ_20260924 ── per-BASE-lot scoreboard (net ÷ each trade's size ratio)\n"
   "      if (metrics) {\n"
   "        const cfg0 = d.config || {};\n"
   "        const run0 = runs.find((x) => x.run_id === runId);\n"
   "        metrics.perBaseLot = perBaseLotOf(trades, cfg0, lotSizeOf(cfg0, run0?.strategy_id, run0?.underlying || cfg0.underlying));\n"
   "      }\n"),
  ("replace", "  }, [apiCall, computeMetrics, detail, detailLoading]);\n",
   "  }, [apiCall, computeMetrics, detail, detailLoading, runs]);   // ── LOT_COMP_EQ_20260924 ── runs for strategy/underlying\n"),
  ("replace", '    { key: "profitFactor",group: "Edge",     label: "Profit factor",  dir: +1, def: true,  fmt: num2,  get: (m) => m?.profitFactor },\n',
   "    // ── LOT_COMP_EQ_20260924 ── per BASE lot: a compounded run on its flat twin's\n"
   "    // scale (each trade's net ÷ its own size ratio). Equal to the headline\n"
   "    // figures when the run never changed size.\n"
   '    { key: "netPerBaseLot", group: "Capital", label: "Net / base lots", dir: +1, def: true, fmt: money, get: (m) => m?.perBaseLot?.net ?? null },\n'
   '    { key: "ddPerBaseLot",  group: "Capital", label: "Max DD / base lots", dir: -1, def: true, fmt: money, get: (m) => (m?.perBaseLot?.maxDD != null ? -Math.abs(m.perBaseLot.maxDD) : null) },\n'
   '    { key: "rddPerBaseLot", group: "Capital", label: "Return ÷ DD / base lots", dir: +1, def: true, fmt: num2, get: (m) => m?.perBaseLot?.returnToDD ?? null },\n'
   '    { key: "profitFactor",group: "Edge",     label: "Profit factor",  dir: +1, def: true,  fmt: num2,  get: (m) => m?.profitFactor },\n'))

# ═══════════════════════════ frontend: SweepBuilder axes ══════════════════
E(f"{FRONTEND}/backtest/SweepBuilder.jsx",
  ("replace", '    fmt: (v) => (v ? "₹scale" : "₹fixed") },\n',
   '    fmt: (v) => (v ? "₹scale" : "₹fixed") },\n'
   "  // ── LOT_COMP_EQ_20260924 ── sizing mode + capital per lot (equity mode)\n"
   '  { key: "lot_comp_mode", label: "Compound mode", strategies: LOT_COMP_STRATS,\n'
   '    hint: "CALENDAR, EQUITY", parse: (tok) => {\n'
   "      const v = tok.trim().toUpperCase();\n"
   '      return ["CALENDAR", "EQUITY"].includes(v) ? { v: v.toLowerCase() } : { err: `"${tok}" must be CALENDAR or EQUITY` };\n'
   "    },\n"
   '    apply: (c, v) => { if (v === "equity") c.lot_comp_mode = "equity"; else delete c.lot_comp_mode; },\n'
   '    fmt: (v) => (v === "equity" ? "eqSize" : "calSize") },\n'
   '  { key: "lot_comp_cpl", label: "Capital per lot (₹)", strategies: LOT_COMP_STRATS,\n'
   '    hint: "100000, 150000, 200000", parse: _num,\n'
   "    apply: (c, v) => { if (v > 0) c.lot_comp_capital_per_lot = Math.floor(v); else delete c.lot_comp_capital_per_lot; },\n"
   '    fmt: (v) => (v > 0 ? `₹${String(+(v / 100000).toFixed(2))}L/lot` : "cplOFF") },\n'))


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
            die(f"prerequisite {fence} not found in {rel} — apply apply_lot_compounding.py then apply_lot_comp_max.py first")
    print(f"   prerequisites ok ({len(PREREQ)})")
    dirty = git_dirty(targets)
    if dirty:
        print("   git status shows uncommitted changes on targets:")
        for l in dirty:
            print("     ", l)
        if not ALLOW_DIRTY:
            die("an `M` on a target is a stop sign; if that M is the parent LOT_COMP patches not yet committed, re-run with --allow-dirty")
        print("   --allow-dirty: continuing")

    staged = {rel: apply_ops(read(rel), ops, rel) for rel, ops in EDITS.items()}
    for rel, b64 in PAYLOADS_B64.items():
        staged[rel] = base64.b64decode(b64).decode("utf-8")
    tmp = tempfile.mkdtemp(prefix="lot_comp_eq_gate_")
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
