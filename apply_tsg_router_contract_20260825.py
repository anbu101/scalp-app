#!/usr/bin/env python3
"""
TSG_ROUTER_CONTRACT_20260825 — ExecutionRouter forward gaps
============================================================================
Incident (2026-08-25 09:16, TSG_V1 LIVE): TSG_ENTRY_REPEG (2026-08-10) added
fresh_{sell,buy}_entry_limit / modify_order / cancel_order(symbol=) to
ZerodhaOrderExecutor ONLY. Live runs wrap the executor in ExecutionRouter,
which forwards methods EXPLICITLY (no __getattr__ fallback), so:

  1. fresh_buy_entry_limit  -> AttributeError at the first L4 re-peg
  2. modify_order           -> would AttributeError next (latent)
  3. cancel_order(symbol=)  -> would TypeError on every re-peg exhaust
                               (router signature has no symbol kwarg),
                               leaving the working order UN-CANCELLED

The AttributeError escaped the re-peg loop, skipped the cancel-on-exhaust,
and the still-OPEN L4 BUY filled 10s after the day closed — an untracked
naked long that had to be closed manually in Kite.

This script (run from repo root, applies to BOTH trees):
  - replaces cancel_order with a symbol-accepting forward
    (TypeError fallback keeps any non-Zerodha executor working)
  - adds modify_order / fresh_sell_entry_limit / fresh_buy_entry_limit
    forwards with the degrade-to-None contract (GTT_RACE_STRICT_20260814
    precedent): None already means "MODIFY failed / no fresh quote" to
    every caller, so an older executor degrades safely instead of raising.

Idempotent: skips a tree whose file already carries the fence marker.
Aborts without writing anything if any anchor is missing or ambiguous.
"""
import sys
from pathlib import Path

FENCE = "TSG_ROUTER_CONTRACT_20260825"
TREES = ["backend/app", "desktop/src-tauri/backend/app"]
REL = "execution/execution_router.py"

OLD_CANCEL = (
    "    def cancel_order(self, order_id: str):\n"
    "        return self._executor.cancel_order(order_id)\n"
)

NEW_BLOCK = (
    "    # \u2500\u2500 TSG_ROUTER_CONTRACT_20260825 BEGIN \u2500\u2500 (2026-08-25 TSG L4 orphan\n"
    "    # incident: TSG_ENTRY_REPEG added fresh_{sell,buy}_entry_limit /\n"
    "    # modify_order / cancel_order(symbol=) to ZerodhaOrderExecutor only.\n"
    "    # Live runs wrap the executor in this router, which forwards explicitly\n"
    "    # \u2014 no __getattr__ \u2014 so the first L4 re-peg raised AttributeError, the\n"
    "    # working order was never cancelled, and it filled 10s after the day\n"
    "    # closed: an untracked naked long. Forwards below use the same\n"
    "    # degrade-to-None contract as GTT_RACE_STRICT_20260814: None means\n"
    "    # \"not supported / no fresh quote / MODIFY failed\", which every caller\n"
    "    # already treats as \"keep the current limit\".)\n"
    "\n"
    "    def cancel_order(self, order_id: str, symbol: str = \"\"):\n"
    "        # symbol is log-cosmetics only (TSG_ENTRY_REPEG D4). Forward it when\n"
    "        # the underlying executor accepts it; degrade to the positional call\n"
    "        # so no pre-existing executor or call site can break.\n"
    "        try:\n"
    "            return self._executor.cancel_order(order_id, symbol=symbol)\n"
    "        except TypeError:\n"
    "            return self._executor.cancel_order(order_id)\n"
    "\n"
    "    def modify_order(self, order_id: str, price: float, symbol: str = \"\"):\n"
    "        # Re-peg MODIFY (TSG_ENTRY_REPEG D1). None = \"MODIFY failed\" \u2014\n"
    "        # the caller keeps waiting on the old price.\n"
    "        fn = getattr(self._executor, \"modify_order\", None)\n"
    "        if not callable(fn):\n"
    "            return None\n"
    "        return fn(order_id, price=price, symbol=symbol)\n"
    "\n"
    "    def fresh_sell_entry_limit(self, symbol: str):\n"
    "        # Re-peg price for a working SELL entry. None = \"no fresh quote\".\n"
    "        fn = getattr(self._executor, \"fresh_sell_entry_limit\", None)\n"
    "        return fn(symbol) if callable(fn) else None\n"
    "\n"
    "    def fresh_buy_entry_limit(self, symbol: str):\n"
    "        # Re-peg price for a working BUY entry. None = \"no fresh quote\".\n"
    "        fn = getattr(self._executor, \"fresh_buy_entry_limit\", None)\n"
    "        return fn(symbol) if callable(fn) else None\n"
    "    # \u2500\u2500 TSG_ROUTER_CONTRACT_20260825 END \u2500\u2500\n"
)


def apply_tree(root: Path, tree: str) -> str:
    path = root / tree / REL
    if not path.exists():
        raise SystemExit(f"[ABORT] missing file: {path}")
    src = path.read_text(encoding="utf-8")

    if FENCE in src:
        return f"[SKIP] {path} — fence {FENCE} already present (idempotent)"

    # Pre-write asserts — abort BEFORE any write on anchor miss/ambiguity.
    n = src.count(OLD_CANCEL)
    assert n == 1, (
        f"[ABORT] {path}: cancel_order anchor found {n}x (expected exactly 1)."
        " File has drifted — re-inspect before applying.")
    for must_not in ("def modify_order", "def fresh_sell_entry_limit",
                     "def fresh_buy_entry_limit"):
        assert must_not not in src, (
            f"[ABORT] {path}: '{must_not}' already defined — partial prior "
            "apply? Re-inspect before applying.")

    out = src.replace(OLD_CANCEL, NEW_BLOCK, 1)

    # Compile gate before touching disk.
    compile(out, str(path), "exec")

    path.write_text(out, encoding="utf-8")
    return f"[OK]   {path} — router contract forwards applied"


def main() -> None:
    root = Path.cwd()
    for tree in TREES:
        if not (root / tree).is_dir():
            raise SystemExit(
                f"[ABORT] tree not found: {root/tree} — run from repo root.")
    results = [apply_tree(root, t) for t in TREES]
    print("\n".join(results))

    # Post-write verification: fence + all four forwards present in BOTH trees.
    for tree in TREES:
        s = (root / tree / REL).read_text(encoding="utf-8")
        for needle in (FENCE, "def modify_order", "def fresh_sell_entry_limit",
                       "def fresh_buy_entry_limit",
                       "def cancel_order(self, order_id: str, symbol"):
            assert needle in s, f"[VERIFY-FAIL] {tree}: missing {needle!r}"
        assert s.count(FENCE) == 2, f"[VERIFY-FAIL] {tree}: fence count != 2"
    print("[VERIFY] both trees: fence + 4 forwards present, compile OK")


if __name__ == "__main__":
    main()
