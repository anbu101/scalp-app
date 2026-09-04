#!/usr/bin/env python3
# apply_brk_alert_kwarg_fix.py — BRK_V1 in-app alerts are silently DEAD:
# brk_manager calls record_alert(source=...) but inapp_events.record_alert
# takes strategy_id= (no source kwarg, no **kwargs). The TypeError is
# swallowed by the caller's try/except — the exact payload-keys-from-
# memory scar (2026-09-03), repeated in the alert path and found during
# the ORB/BRK clash audit 2026-09-04.
#
# Fence: BRK_ALERT_KWARG_20260904. One replacement, both trees, idempotent.
# LIVE-PATH FILE: apply outside market hours per house rules.
from __future__ import annotations
import os, shutil, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
OLD = "record_alert(source=STRATEGY_ID, code=code, message=msg,"
NEW = "record_alert(code, msg, strategy_id=STRATEGY_ID,   # \u2500\u2500 BRK_ALERT_KWARG_20260904 \u2500\u2500"
for tree in ("backend", os.path.join("desktop", "src-tauri", "backend")):
    p = os.path.join(ROOT, tree, "app", "engine", "brk", "brk_manager.py")
    t = open(p, encoding="utf-8").read()
    if "BRK_ALERT_KWARG_20260904" in t:
        print(f"  SKIP   {{p}} (already fixed)"); continue
    n = t.count(OLD)
    if n != 1:
        # also tolerate a follow-on line shape: verify manually
        print(f"  ABORT  {{p}}: anchor x{{n}} — inspect by hand"); sys.exit(2)
    shutil.copy2(p, p + ".bak-BRK_ALERT_KWARG_20260904")
    open(p, "w", encoding="utf-8").write(t.replace(OLD, NEW))
    import py_compile; py_compile.compile(p, doraise=True)
    print(f"  WROTE  {{p}}")
print("  DONE   verify: grep -n 'record_alert' backend/app/engine/brk/brk_manager.py")
