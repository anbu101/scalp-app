#!/usr/bin/env python3
# ── TSG_MTM_BASIS_20260821 ── backend edit script
#
# Adds config key `mtm_sl_basis` ("DAILY" | "POSITION") to the TSG_V1
# backtest runner. D1: default "DAILY" (current IV6 semantics — realized +
# unrealized). D2: SL only — mtm_target, trail, peak/trough stay on day MTM.
#
# Applies to BOTH trees when present:
#   backend/app/backtest/tsg/backtest_tsg_runner.py
#   desktop/src-tauri/backend/app/backtest/tsg/backtest_tsg_runner.py
#
# Run from repo root:  python3 apply_tsg_mtm_basis_backend_20260821.py
# Every anchor is asserted; any miss aborts with a clear message and the
# file is left untouched (edits are applied to an in-memory copy first).

import sys
from pathlib import Path

TREES = [
    Path("backend/app/backtest/tsg/backtest_tsg_runner.py"),
    Path("desktop/src-tauri/backend/app/backtest/tsg/backtest_tsg_runner.py"),
]

EDITS = [
    # E1 — simulate_tsg_day signature: new kwarg
    (
        "    mtm_trail_giveback: float = 0.0,\n"
        "    iv_keep_hedge: bool = False,\n"
        ") -> dict:",
        "    mtm_trail_giveback: float = 0.0,\n"
        "    iv_keep_hedge: bool = False,\n"
        "    mtm_sl_basis: str = \"DAILY\",   # \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500 \"DAILY\"|\"POSITION\" (SL only)\n"
        ") -> dict:",
    ),
    # E2 — SL check: basis-aware comparison (target/trail untouched)
    (
        "        if mtm_sl > 0 and mtm <= -mtm_sl:",
        "        # \u2500\u2500 TSG_MTM_BASIS_20260821 BEGIN \u2500\u2500 SL basis (D2: SL only).\n"
        "        # DAILY = realized + unrealized (IV6, unchanged default);\n"
        "        # POSITION = unrealized of OPEN legs only (= mtm - realized) \u2014\n"
        "        # after a partial IV exit the survivors get a fresh SL runway.\n"
        "        # mtm_target / trail / peak / trough stay on day MTM by design.\n"
        "        _sl_mtm = mtm if mtm_sl_basis != \"POSITION\" else (mtm - realized)\n"
        "        if mtm_sl > 0 and _sl_mtm <= -mtm_sl:\n"
        "            # \u2500\u2500 TSG_MTM_BASIS_20260821 END \u2500\u2500",
    ),
    # E3 — config docstring
    (
        "      mtm_sl           float \u20b9  POSITIVE (default 0 = disabled); exit ALL\n"
        "                       legs when combined MTM <= -mtm_sl (reason MTM_SL)",
        "      mtm_sl           float \u20b9  POSITIVE (default 0 = disabled); exit ALL\n"
        "                       legs when combined MTM <= -mtm_sl (reason MTM_SL)\n"
        "      mtm_sl_basis     \"DAILY\"|\"POSITION\" (default DAILY); DAILY = day\n"
        "                       MTM (realized + unrealized, IV6); POSITION =\n"
        "                       unrealized of open legs only. SL only\n"
        "                       (\u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500)",
    ),
    # E4 — cfg read (fail-closed normalization to DAILY)
    (
        "    mtm_sl = abs(float(cfg.get(\"mtm_sl\", 0) or 0))   # sign-tolerant: -2500 \u2261 2500",
        "    mtm_sl = abs(float(cfg.get(\"mtm_sl\", 0) or 0))   # sign-tolerant: -2500 \u2261 2500\n"
        "    # \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500 anything not exactly \"POSITION\"\n"
        "    # normalizes to \"DAILY\" (fail-closed to current IV6 semantics).\n"
        "    mtm_sl_basis = (\"POSITION\" if str(cfg.get(\"mtm_sl_basis\", \"DAILY\")\n"
        "                    or \"DAILY\").strip().upper() == \"POSITION\" else \"DAILY\")",
    ),
    # E5 — serial call site passes the kwarg (parallel workers re-enter the
    # impl with child_cfg = dict(cfg), so they inherit the key automatically)
    (
        "                               mtm_trail_giveback=mtm_trail_giveback,\n"
        "                               iv_keep_hedge=iv_keep_hedge)",
        "                               mtm_trail_giveback=mtm_trail_giveback,\n"
        "                               iv_keep_hedge=iv_keep_hedge,\n"
        "                               mtm_sl_basis=mtm_sl_basis)   # \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500",
    ),
    # E6a — parallel-path diag echo
    (
        "            \"mtm_target\": mtm_target, \"mtm_sl\": mtm_sl,\n"
        "            \"iv_sl_pct\": iv_sl_pct, \"parallel_workers\": n,",
        "            \"mtm_target\": mtm_target, \"mtm_sl\": mtm_sl,\n"
        "            \"mtm_sl_basis\": mtm_sl_basis,   # \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500\n"
        "            \"iv_sl_pct\": iv_sl_pct, \"parallel_workers\": n,",
    ),
    # E6b — serial-path diag echo
    (
        "        \"mtm_target\": mtm_target, \"mtm_sl\": mtm_sl,\n"
        "        \"iv_sl_pct\": iv_sl_pct, \"iv_sl_delta_pts\": iv_sl_delta_pts,",
        "        \"mtm_target\": mtm_target, \"mtm_sl\": mtm_sl,\n"
        "        \"mtm_sl_basis\": mtm_sl_basis,   # \u2500\u2500 TSG_MTM_BASIS_20260821 \u2500\u2500\n"
        "        \"iv_sl_pct\": iv_sl_pct, \"iv_sl_delta_pts\": iv_sl_delta_pts,",
    ),
]


def apply(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "TSG_MTM_BASIS_20260821" in text:
        print(f"[SKIP] {path} \u2014 fence already present (idempotent)")
        return True
    for n, (old, new) in enumerate(EDITS, 1):
        cnt = text.count(old)
        if cnt != 1:
            print(f"[ABORT] {path} \u2014 edit E{n}: anchor found {cnt}x "
                  f"(expected exactly 1). File NOT modified.")
            print("        anchor head: " + old.splitlines()[0][:70])
            return False
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"[OK]   {path} \u2014 {len(EDITS)} edits applied")
    return True


def main() -> int:
    found = [p for p in TREES if p.exists()]
    if not found:
        print("[ABORT] runner not found in either tree \u2014 run from repo root")
        return 1
    ok = all(apply(p) for p in found)
    if len(found) == 1:
        print("[NOTE] second tree absent (desktop/src-tauri/backend is "
              "build-generated); build-scalp.sh will sync it. If your "
              "convention is to keep it checked out, run this script again "
              "after syncing.")
    # grep-count verification
    for p in found:
        c = p.read_text(encoding="utf-8").count("TSG_MTM_BASIS_20260821")
        print(f"[VERIFY] {p}: fence marker count = {c} (expected 8)")
        ok = ok and (c == 8)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
