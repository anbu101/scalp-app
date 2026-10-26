# backend/app/execution/test_execution_router_contract.py
#
# ── ROUTER_GTT_FORWARDS_20261026 ── ExecutionRouter forwards EXPLICITLY (no
# __getattr__, by design). Twice now an executor method was added to
# ZerodhaOrderExecutor and called by a strategy, but never forwarded:
#   2026-08-25 TSG  — modify_order / cancel_order(symbol=) → orphan order
#   2026-10-26 TMA_V2 — place_gtt_sl_only_short → live short with NO GTT,
#                        get_basket_margin → margin guard silently skipped
# Unit tests never caught it because each manager's tests use a fake
# executor that HAS the method. This test scans the real call sites instead:
# every `executor.<name>` / `self.executor.<name>` attribute access and every
# getattr/hasattr(executor, "<name>") probe in strategy code must resolve on
# ExecutionRouter. Run standalone:  python3 test_execution_router_contract.py
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

SCAN_DIRS = ["app/engine", "app/risk", "app/trading", "app/jobs"]
EXEC_NAMES = {"executor", "_executor", "ex"}

# Known, deliberately-unfixed gaps — each one is a REAL live bug in another
# strategy, listed so it cannot be forgotten. Forwarding them changes that
# strategy's live behaviour (e.g. HA_V1 live entries would START placing
# orders), so each needs its owner's decision + a paper run first.
KNOWN_GAPS = {
    # HA_V1 builds raw Kite orders through executor internals. Under the
    # router broker_manager is absent → every HA live entry is refused as
    # "trade session not ready"; exits via _relay_call raise.
    "broker_manager": "HA_V1 / BB / risk_mtm_guard — see FIXES doc",
    "_relay_call": "HA_V1 live buy/sell",
    "get_gtts_or_none": "HA_V1 (hasattr-guarded; falls back)",
    # BB GTTMonitor reads executor._kite() → raises under the router →
    # logged as FETCH_FAIL every sweep: BB GTT fills are never detected.
    "_kite": "BB_V1/BB_V2 GTTMonitor",
}


def _router_methods():
    from app.execution.execution_router import ExecutionRouter
    return {n for n in dir(ExecutionRouter) if not n.startswith("__")}


def _is_exec(node) -> bool:
    if isinstance(node, ast.Name):
        return node.id in EXEC_NAMES
    return (isinstance(node, ast.Attribute) and node.attr in EXEC_NAMES
            and isinstance(node.value, ast.Name) and node.value.id == "self")


def _uses():
    out = {}
    for d in SCAN_DIRS:
        for dp, _, files in os.walk(BACKEND / d):
            for f in files:
                if not f.endswith(".py") or f.startswith("test_") \
                        or "/tests" in dp or ".bak" in f:
                    continue
                p = Path(dp) / f
                try:
                    tree = ast.parse(p.read_text())
                except Exception:
                    continue
                rel = str(p.relative_to(BACKEND))
                for n in ast.walk(tree):
                    if isinstance(n, ast.Attribute) and _is_exec(n.value):
                        out.setdefault(n.attr, set()).add(rel)
                    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                            and n.func.id in ("getattr", "hasattr")
                            and len(n.args) >= 2 and _is_exec(n.args[0])
                            and isinstance(n.args[1], ast.Constant)
                            and isinstance(n.args[1].value, str)):
                        out.setdefault(n.args[1].value, set()).add(rel)
    return out


def test_router_forwards_every_called_method():
    have = _router_methods()
    uses = _uses()
    missing = {k: sorted(v) for k, v in uses.items()
               if k not in have and k not in KNOWN_GAPS}
    assert not missing, (
        "ExecutionRouter does not forward methods that strategy code calls "
        "(live runs get AttributeError or a silent fallback):\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(missing.items())))
    for must in ("place_gtt_sl_only_short", "place_gtt_sl_only_long",
                 "place_gtt_tp_only_long", "get_basket_margin"):
        assert must in have, must


def test_known_gaps_still_real():
    # When someone fixes a known gap, force them to delete it here too.
    have = _router_methods()
    stale = [k for k in KNOWN_GAPS if k in have]
    assert not stale, f"KNOWN_GAPS now forwarded — remove from list: {stale}"


if __name__ == "__main__":
    test_router_forwards_every_called_method()
    test_known_gaps_still_real()
    print("ROUTER CONTRACT OK — every executor call in strategy code is "
          f"forwarded ({len(KNOWN_GAPS)} documented known gaps)")
