#!/usr/bin/env python3
# ── MTM_GUARD_UNREAL_20260821 ── live-guard NameError fixes
#
# FIX 1  backend/app/risk/risk_mtm_guard.py
#   Defines the missing `_unrealised_mtm(positions, executor)` that
#   _evaluate() has referenced since the module shipped (line ~321).
#   Every mtm_breach_* call raised NameError (swallowed by the tick
#   engines' try/except) — the mid-trade MTM max-loss / max-profit
#   square-off for SCALP_V1 / BB_V1 / BB_V2 / HA_V1 never functioned.
#   Semantics: DAILY by construction (_evaluate adds today_realised_pnl);
#   fail-open — ANY unresolvable LTP makes the whole reading
#   indeterminate (partial sums could fire a FALSE square-off).
#
# FIX 2  backend/app/api/app_settings_api.py
#   /debug/fire-test-alerts calls record_alert() 3x but never imports it
#   → NameError on first use. record_alert exists in inapp_events with
#   the exact signature used; the fix is the missing import.
#
# Applies to BOTH trees when present. Run from repo root:
#   python3 apply_mtm_guard_unreal_20260821.py
# Assert-anchored; any miss aborts that file untouched.
#
# DEPLOY NOTE (D2): trading-day protocol — commit now, run the
# PyInstaller + Tauri rebuild AFTER 15:30 IST.

import sys
from pathlib import Path

GUARD_EDIT = (
    # anchor: the direction-aware pnl helper (unique)
    "def _pos_pnl(entry: float, ltp: float, qty: int, direction: str) -> float:\n"
    "    if (direction or \"LONG\").upper() == \"SHORT\":\n"
    "        return (float(entry) - float(ltp)) * int(qty)\n"
    "    return (float(ltp) - float(entry)) * int(qty)\n",

    "def _pos_pnl(entry: float, ltp: float, qty: int, direction: str) -> float:\n"
    "    if (direction or \"LONG\").upper() == \"SHORT\":\n"
    "        return (float(entry) - float(ltp)) * int(qty)\n"
    "    return (float(ltp) - float(entry)) * int(qty)\n"
    "\n"
    "\n"
    "# \u2500\u2500 MTM_GUARD_UNREAL_20260821 BEGIN \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "def _unrealised_mtm(positions, executor=None) -> Tuple[float, bool]:\n"
    "    \"\"\"Sum of open-position mark-to-market. Returns (total, indeterminate).\n"
    "\n"
    "    - positions is None -> collector could not enumerate -> (0.0, True).\n"
    "    - empty list        -> flat book                     -> (0.0, False).\n"
    "    - ANY leg whose LTP cannot be resolved (LTPStore + REST both fail)\n"
    "      -> (0.0, True) IMMEDIATELY. A partial sum that omits a winning leg\n"
    "      OVERSTATES the loss and could fire a FALSE square-off; per this\n"
    "      module's fail-open philosophy the whole reading is indeterminate\n"
    "      and _evaluate() waits for the next cycle.\n"
    "\n"
    "    History: referenced by _evaluate() since this module shipped but\n"
    "    never defined \u2014 every mtm_breach_* call raised NameError (swallowed\n"
    "    by the tick engines' try/except), so the mid-trade MTM square-off\n"
    "    for SCALP_V1 / BB_V1 / BB_V2 / HA_V1 never functioned before this.\n"
    "    \"\"\"\n"
    "    if positions is None:\n"
    "        return 0.0, True\n"
    "    total = 0.0\n"
    "    for sym, entry, qty, direction in positions:\n"
    "        ltp = _resolve_ltp(sym, executor=executor)\n"
    "        if ltp is None:\n"
    "            write_audit_log(\n"
    "                f\"[MTM][INDETERMINATE] no LTP for {sym} \u2014 \"\n"
    "                f\"skipping MTM evaluation this cycle\")\n"
    "            return 0.0, True\n"
    "        total += _pos_pnl(entry, ltp, qty, direction)\n"
    "    return total, False\n"
    "# \u2500\u2500 MTM_GUARD_UNREAL_20260821 END \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n",
)

API_EDIT = (
    "from app.event_bus.inapp_events import get_events_after",
    "from app.event_bus.inapp_events import get_events_after, record_alert   "
    "# \u2500\u2500 MTM_GUARD_UNREAL_20260821 \u2500\u2500 was called by /debug/fire-test-alerts but never imported",
)

FILES = [
    ("backend/app/risk/risk_mtm_guard.py", [GUARD_EDIT]),
    ("desktop/src-tauri/backend/app/risk/risk_mtm_guard.py", [GUARD_EDIT]),
    ("backend/app/api/app_settings_api.py", [API_EDIT]),
    ("desktop/src-tauri/backend/app/api/app_settings_api.py", [API_EDIT]),
]


def apply(path: Path, edits) -> bool:
    text = path.read_text(encoding="utf-8")
    if "MTM_GUARD_UNREAL_20260821" in text:
        print(f"[SKIP] {path} \u2014 fence already present (idempotent)")
        return True
    for n, (old, new) in enumerate(edits, 1):
        cnt = text.count(old)
        if cnt != 1:
            print(f"[ABORT] {path} \u2014 edit {n}: anchor found {cnt}x "
                  f"(expected exactly 1). File NOT modified.")
            print("        anchor head: " + old.splitlines()[0][:70])
            return False
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"[OK]   {path}")
    return True


def main() -> int:
    ok = True
    seen_any = False
    for rel, edits in FILES:
        p = Path(rel)
        if not p.exists():
            if rel.startswith("backend/"):
                print(f"[ABORT] {rel} not found \u2014 run from repo root")
                return 1
            print(f"[NOTE] {rel} absent (build-generated tree) \u2014 "
                  f"build-scalp.sh will sync it")
            continue
        seen_any = True
        ok = apply(p, edits) and ok
    for rel, _ in FILES:
        p = Path(rel)
        if p.exists():
            c = p.read_text(encoding="utf-8").count("MTM_GUARD_UNREAL_20260821")
            exp = 2 if "risk_mtm_guard" in rel else 1
            print(f"[VERIFY] {rel}: fence count = {c} (expected {exp})")
            ok = ok and (c == exp)
    return 0 if (ok and seen_any) else 1


if __name__ == "__main__":
    sys.exit(main())
