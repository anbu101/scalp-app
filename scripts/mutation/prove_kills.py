#!/usr/bin/env python3
# scripts/mutation/prove_kills.py — ── MUTMUT_20261117 ── (TD-043) · BT_MUTSAFETY_20261122
#
# Proof for the tests written against surviving mutants: each mutant below is
# put into a throw-away copy of its module, and its suite must FAIL there and
# PASS on the untouched module. Offline, a few seconds, nothing in the repo or
# ~/.scalp-app is written (temp folder, HOME pointed into it).
#   python3 scripts/mutation/prove_kills.py            all
#   python3 scripts/mutation/prove_kills.py -v         also each mutant's diff
#   -j N  mutants in parallel (default: CPUs-1, at most 4)
# Exit 0 = every mutant killed and main green; 1 = a mutant survives or main
# fails; a mutant whose line is no longer in the module is reported STALE
# (the module changed: rerun mutmut_run.py for that module), not counted.
# The list was generated from mutmut 3.8.1 `mutmut show` of the sandbox pilot.
# BT_MUTSAFETY_20261122: more lists live beside this file as prove_kills_<module>.json
# ({meta: {module, suite, deps}, mutants: [...]}; deps = other modules the suite imports);
#   python3 scripts/mutation/prove_kills.py broker_truth     only that list
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
FENCE = "MUTMUT_20261117"
MUTANTS = json.loads(r"""[
 {
  "name": "x_drive_exit_order__mutmut_3",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227, TC-EXEC-0228, TC-EXEC-0230, TC-EXEC-0231, TC-EXEC-0232, TC-EXEC-0233",
  "old": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 1.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,",
  "new": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 1.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 1.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,",
  "what": "→ step_pct: float = 1.02, max_extra_pct: float = 0.10,"
 },
 {
  "name": "x_drive_exit_order__mutmut_4",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0232, TC-EXEC-0233",
  "old": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 1.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,",
  "new": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 1.1,\n                     poll_s: float = 1.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,",
  "what": "→ step_pct: float = 0.02, max_extra_pct: float = 1.1,"
 },
 {
  "name": "x_drive_exit_order__mutmut_5",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229, TC-EXEC-0230",
  "old": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 1.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,\n                     sleep: Callable[[float], None] = time.sleep,",
  "new": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 2.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,\n                     sleep: Callable[[float], None] = time.sleep,",
  "what": "→ poll_s: float = 2.0, settle_s: float = 6.0,"
 },
 {
  "name": "x_drive_exit_order__mutmut_6",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 1.0, settle_s: float = 6.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,\n                     sleep: Callable[[float], None] = time.sleep,",
  "new": "def drive_exit_order(executor, order_id: str, *, symbol: str, side: str,\n                     chase_s: float = 25.0, repeg_every_s: float = 4.0,\n                     step_pct: float = 0.02, max_extra_pct: float = 0.10,\n                     poll_s: float = 1.0, settle_s: float = 7.0,\n                     repeg: bool = True,\n                     log: Callable[[str], None] = print,\n                     sleep: Callable[[float], None] = time.sleep,",
  "what": "→ poll_s: float = 1.0, settle_s: float = 7.0,"
 },
 {
  "name": "x_drive_exit_order__mutmut_22",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227",
  "old": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "new": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if (side == \"BUY\") and False\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "what": "→ getattr(executor, (\"fresh_buy_exit_limit\" if (side == \"BUY\") and False"
 },
 {
  "name": "x_drive_exit_order__mutmut_24",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227",
  "old": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "new": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"XXfresh_buy_exit_limitXX\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "what": "→ getattr(executor, (\"XXfresh_buy_exit_limitXX\" if side == \"BUY\""
 },
 {
  "name": "x_drive_exit_order__mutmut_25",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227",
  "old": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "new": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"FRESH_BUY_EXIT_LIMIT\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "what": "→ getattr(executor, (\"FRESH_BUY_EXIT_LIMIT\" if side == \"BUY\""
 },
 {
  "name": "x_drive_exit_order__mutmut_27",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227",
  "old": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "new": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"XXBUYXX\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "what": "→ getattr(executor, (\"fresh_buy_exit_limit\" if side == \"XXBUYXX\""
 },
 {
  "name": "x_drive_exit_order__mutmut_28",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227",
  "old": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "new": "    oid = str(order_id)\n    # ── EXIT_REPEG_FAST_20261116 ── exit quotes first (time-capped)\n    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"buy\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))",
  "what": "→ getattr(executor, (\"fresh_buy_exit_limit\" if side == \"buy\""
 },
 {
  "name": "x_drive_exit_order__mutmut_36",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0228",
  "old": "    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))\n    modify = getattr(executor, \"modify_order\", None)\n    if not repeg:                       # confirm-only (entries): wait, cancel, settle",
  "new": "    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if (side == \"BUY\") and False\n                              else \"fresh_sell_entry_limit\"), None))\n    modify = getattr(executor, \"modify_order\", None)\n    if not repeg:                       # confirm-only (entries): wait, cancel, settle",
  "what": "→ or getattr(executor, (\"fresh_buy_entry_limit\" if (side == \"BUY\") and False"
 },
 {
  "name": "x_drive_exit_order__mutmut_41",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0228",
  "old": "    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))\n    modify = getattr(executor, \"modify_order\", None)\n    if not repeg:                       # confirm-only (entries): wait, cancel, settle",
  "new": "    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"XXBUYXX\"\n                              else \"fresh_sell_entry_limit\"), None))\n    modify = getattr(executor, \"modify_order\", None)\n    if not repeg:                       # confirm-only (entries): wait, cancel, settle",
  "what": "→ or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"XXBUYXX\""
 },
 {
  "name": "x_drive_exit_order__mutmut_42",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0228",
  "old": "    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"BUY\"\n                              else \"fresh_sell_entry_limit\"), None))\n    modify = getattr(executor, \"modify_order\", None)\n    if not repeg:                       # confirm-only (entries): wait, cancel, settle",
  "new": "    fresh = fresh_fn if callable(fresh_fn) else (\n        getattr(executor, (\"fresh_buy_exit_limit\" if side == \"BUY\"\n                           else \"fresh_sell_exit_limit\"), None)\n        or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"buy\"\n                              else \"fresh_sell_entry_limit\"), None))\n    modify = getattr(executor, \"modify_order\", None)\n    if not repeg:                       # confirm-only (entries): wait, cancel, settle",
  "what": "→ or getattr(executor, (\"fresh_buy_entry_limit\" if side == \"buy\""
 },
 {
  "name": "x_drive_exit_order__mutmut_60",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227, TC-EXEC-0228, TC-EXEC-0230, TC-EXEC-0231, TC-EXEC-0233",
  "old": "    start = clock()\n    deadline = start + chase_s\n    last_repeg = start\n    k = 0\n    last_px: Optional[float] = float(start_px) if start_px else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n",
  "new": "    start = clock()\n    deadline = start + chase_s\n    last_repeg = start\n    k = 1\n    last_px: Optional[float] = float(start_px) if start_px else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n",
  "what": "→ k = 1"
 },
 {
  "name": "x_drive_exit_order__mutmut_61",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0232",
  "old": "    deadline = start + chase_s\n    last_repeg = start\n    k = 0\n    last_px: Optional[float] = float(start_px) if start_px else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n\n    while clock() < deadline:",
  "new": "    deadline = start + chase_s\n    last_repeg = start\n    k = 0\n    last_px: Optional[float] = None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n\n    while clock() < deadline:",
  "what": "→ last_px: Optional[float] = None   # ── WING_FAST_FILL_20261111 ──"
 },
 {
  "name": "x_drive_exit_order__mutmut_62",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0232",
  "old": "    deadline = start + chase_s\n    last_repeg = start\n    k = 0\n    last_px: Optional[float] = float(start_px) if start_px else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n\n    while clock() < deadline:",
  "new": "    deadline = start + chase_s\n    last_repeg = start\n    k = 0\n    last_px: Optional[float] = float(start_px) if (start_px) and False else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n\n    while clock() < deadline:",
  "what": "→ last_px: Optional[float] = float(start_px) if (start_px) and False else None   # ── WING_FAST_FILL_20261111 "
 },
 {
  "name": "x_drive_exit_order__mutmut_66",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "    last_px: Optional[float] = float(start_px) if start_px else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n\n    while clock() < deadline:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":",
  "new": "    last_px: Optional[float] = float(start_px) if start_px else None   # ── WING_FAST_FILL_20261111 ──\n    info: dict = {}\n\n    while clock() <= deadline:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":",
  "what": "→ while clock() <= deadline:"
 },
 {
  "name": "x_drive_exit_order__mutmut_84",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":\n            return DriveResult(\"FILLED\", None, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_87",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), None)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 0), None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_92",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), )\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 0), )"
 },
 {
  "name": "x_drive_exit_order__mutmut_108",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "what": "→ return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_109",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", None, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "what": "→ return DriveResult(\"DEAD\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_112",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):",
  "new": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), None,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):",
  "what": "→ int(info.get(\"filled_qty\") or 0), None,"
 },
 {
  "name": "x_drive_exit_order__mutmut_113",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               None)\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_118",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ int(info.get(\"filled_qty\") or 0), str(info.get(\"status_message\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_119",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               )\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ )"
 },
 {
  "name": "x_drive_exit_order__mutmut_120",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"XXDEADXX\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "what": "→ return DriveResult(\"XXDEADXX\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_121",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"dead\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "what": "→ return DriveResult(\"dead\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_127",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 1.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 1.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_134",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(None))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ str(None))"
 },
 {
  "name": "x_drive_exit_order__mutmut_135",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") and \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ str(info.get(\"status_message\") and \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_136",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(None) or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ str(info.get(None) or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_137",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"XXstatus_messageXX\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ str(info.get(\"XXstatus_messageXX\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_138",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"STATUS_MESSAGE\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ str(info.get(\"STATUS_MESSAGE\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_139",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"XXXX\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()",
  "what": "→ str(info.get(\"status_message\") or \"XXXX\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_140",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None",
  "new": "            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify) or clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None",
  "what": "→ if (callable(fresh) and callable(modify) or clock() - last_repeg >= repeg_every_s):"
 },
 {
  "name": "x_drive_exit_order__mutmut_141",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1",
  "new": "            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) or callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1",
  "what": "→ if (callable(fresh) or callable(modify)"
 },
 {
  "name": "x_drive_exit_order__mutmut_144",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None",
  "new": "                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() + last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None",
  "what": "→ and clock() + last_repeg >= repeg_every_s):"
 },
 {
  "name": "x_drive_exit_order__mutmut_145",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231, TC-EXEC-0232, TC-EXEC-0233",
  "old": "                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None",
  "new": "                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg > repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None",
  "what": "→ and clock() - last_repeg > repeg_every_s):"
 },
 {
  "name": "x_drive_exit_order__mutmut_147",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227, TC-EXEC-0228, TC-EXEC-0230, TC-EXEC-0232, TC-EXEC-0233",
  "old": "        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None\n            _tq = clock()\n            try:",
  "new": "        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k = 1\n            q = None\n            _tq = clock()\n            try:",
  "what": "→ k = 1"
 },
 {
  "name": "x_drive_exit_order__mutmut_149",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227, TC-EXEC-0228, TC-EXEC-0230, TC-EXEC-0231, TC-EXEC-0232, TC-EXEC-0233",
  "old": "        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 1\n            q = None\n            _tq = clock()\n            try:",
  "new": "        if (callable(fresh) and callable(modify)\n                and clock() - last_repeg >= repeg_every_s):\n            last_repeg = clock()\n            k += 2\n            q = None\n            _tq = clock()\n            try:",
  "what": "→ k += 2"
 },
 {
  "name": "x_drive_exit_order__mutmut_154",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "            try:\n                q = fresh(symbol)\n            except Exception as e:\n                log(f\"[EXIT_DRIVER][QUOTE_ERR] {symbol} {e}\")\n            if clock() - _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.",
  "new": "            try:\n                q = fresh(symbol)\n            except Exception as e:\n                log(None)\n            if clock() - _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.",
  "what": "→ log(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_155",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229, TC-EXEC-0230",
  "old": "                q = fresh(symbol)\n            except Exception as e:\n                log(f\"[EXIT_DRIVER][QUOTE_ERR] {symbol} {e}\")\n            if clock() - _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.\n                info = _read(executor, oid)",
  "new": "                q = fresh(symbol)\n            except Exception as e:\n                log(f\"[EXIT_DRIVER][QUOTE_ERR] {symbol} {e}\")\n            if clock() + _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.\n                info = _read(executor, oid)",
  "what": "→ if clock() + _tq >= SLOW_QUOTE_S:"
 },
 {
  "name": "x_drive_exit_order__mutmut_156",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                q = fresh(symbol)\n            except Exception as e:\n                log(f\"[EXIT_DRIVER][QUOTE_ERR] {symbol} {e}\")\n            if clock() - _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.\n                info = _read(executor, oid)",
  "new": "                q = fresh(symbol)\n            except Exception as e:\n                log(f\"[EXIT_DRIVER][QUOTE_ERR] {symbol} {e}\")\n            if clock() - _tq > SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.\n                info = _read(executor, oid)",
  "what": "→ if clock() - _tq > SLOW_QUOTE_S:"
 },
 {
  "name": "x_drive_exit_order__mutmut_159",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "            if clock() - _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.\n                info = _read(executor, oid)\n                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "            if clock() - _tq >= SLOW_QUOTE_S:\n                # ── EXIT_REPEG_FAST_20261116 ── a slow quote: the order may\n                # have filled meanwhile — read it before modifying it.\n                info = _read(executor, None)\n                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ info = _read(executor, None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_174",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                info = _read(executor, oid)\n                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "                info = _read(executor, oid)\n                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", None, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_176",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229, TC-EXEC-0231",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       None, status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ None, status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_177",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), None)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 0), None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_181",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229, TC-EXEC-0231",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_182",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), )\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 0), )"
 },
 {
  "name": "x_drive_exit_order__mutmut_190",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "                info = _read(executor, oid)\n                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "                info = _read(executor, oid)\n                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 1.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 1.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_192",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") and 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") and 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_193",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(None) or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(None) or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_194",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"XXfilled_qtyXX\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"XXfilled_qtyXX\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_195",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"FILLED_QTY\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"FILLED_QTY\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_196",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "new": "                status = (info.get(\"status\") or \"\").upper() or None\n                if status == \"COMPLETE\":\n                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 1), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 1), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_198",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_199",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", None, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_200",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, None,\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, None,"
 },
 {
  "name": "x_drive_exit_order__mutmut_201",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       None, status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ None, status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_202",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), None,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(info.get(\"filled_qty\") or 0), None,"
 },
 {
  "name": "x_drive_exit_order__mutmut_203",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       None)\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_204",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_205",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_206",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ return DriveResult(\"DEAD\", oid, int(info.get(\"filled_qty\") or 0), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_207",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_208",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ int(info.get(\"filled_qty\") or 0), str(info.get(\"status_message\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_209",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       )\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ )"
 },
 {
  "name": "x_drive_exit_order__mutmut_210",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"XXDEADXX\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"XXDEADXX\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_211",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"dead\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"dead\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_212",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(None),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(None),"
 },
 {
  "name": "x_drive_exit_order__mutmut_213",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") and 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") and 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_214",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(None) or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(None) or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_215",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"XXavg_priceXX\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(\"XXavg_priceXX\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_216",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"AVG_PRICE\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(\"AVG_PRICE\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_217",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "new": "                    return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 1.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 1.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_218",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(None), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(None), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_219",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") and 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(info.get(\"filled_qty\") and 0), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_220",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(None) or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(info.get(None) or 0), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_221",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"XXfilled_qtyXX\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(info.get(\"XXfilled_qtyXX\") or 0), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_222",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"FILLED_QTY\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(info.get(\"FILLED_QTY\") or 0), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_223",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "new": "                                       int(info.get(\"filled_qty\") or 0), status)\n                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 1), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])",
  "what": "→ int(info.get(\"filled_qty\") or 1), status,"
 },
 {
  "name": "x_drive_exit_order__mutmut_224",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(None))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ str(None))"
 },
 {
  "name": "x_drive_exit_order__mutmut_225",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") and \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ str(info.get(\"status_message\") and \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_226",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(None) or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ str(info.get(None) or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_227",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"XXstatus_messageXX\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ str(info.get(\"XXstatus_messageXX\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_228",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"STATUS_MESSAGE\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ str(info.get(\"STATUS_MESSAGE\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_229",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0229",
  "old": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "new": "                if status in DEAD_STATUSES:\n                    return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                                       int(info.get(\"filled_qty\") or 0), status,\n                                       str(info.get(\"status_message\") or \"XXXX\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)",
  "what": "→ str(info.get(\"status_message\") or \"XXXX\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_238",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227, TC-EXEC-0228, TC-EXEC-0230, TC-EXEC-0232, TC-EXEC-0233",
  "old": "                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)\n                tgt = _tick(base * (1 + extra)) if side == \"BUY\" \\\n                    else max(TICK, _tick(base * (1 - extra)))\n                if last_px is not None:",
  "new": "                                       str(info.get(\"status_message\") or \"\"))\n            if q:\n                base = float(q[0])\n                extra = min(step_pct / k, max_extra_pct)\n                tgt = _tick(base * (1 + extra)) if side == \"BUY\" \\\n                    else max(TICK, _tick(base * (1 - extra)))\n                if last_px is not None:",
  "what": "→ extra = min(step_pct / k, max_extra_pct)"
 },
 {
  "name": "x_drive_exit_order__mutmut_243",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0227, TC-EXEC-0228, TC-EXEC-0230, TC-EXEC-0231, TC-EXEC-0232",
  "old": "            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)\n                tgt = _tick(base * (1 + extra)) if side == \"BUY\" \\\n                    else max(TICK, _tick(base * (1 - extra)))\n                if last_px is not None:\n                    tgt = max(tgt, last_px) if side == \"BUY\" else min(tgt, last_px)",
  "new": "            if q:\n                base = float(q[0])\n                extra = min(step_pct * k, max_extra_pct)\n                tgt = _tick(base / (1 + extra)) if side == \"BUY\" \\\n                    else max(TICK, _tick(base * (1 - extra)))\n                if last_px is not None:\n                    tgt = max(tgt, last_px) if side == \"BUY\" else min(tgt, last_px)",
  "what": "→ tgt = _tick(base / (1 + extra)) if side == \"BUY\" \\"
 },
 {
  "name": "x_drive_exit_order__mutmut_277",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0233",
  "old": "                if tgt != last_px:\n                    ok = None\n                    try:\n                        ok = modify(oid, price=tgt, symbol=symbol)\n                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:",
  "new": "                if tgt != last_px:\n                    ok = None\n                    try:\n                        ok = modify(oid, price=tgt, symbol=None)\n                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:",
  "what": "→ ok = modify(oid, price=tgt, symbol=None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_280",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0233",
  "old": "                if tgt != last_px:\n                    ok = None\n                    try:\n                        ok = modify(oid, price=tgt, symbol=symbol)\n                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:",
  "new": "                if tgt != last_px:\n                    ok = None\n                    try:\n                        ok = modify(oid, price=tgt, )\n                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:",
  "what": "→ ok = modify(oid, price=tgt, )"
 },
 {
  "name": "x_drive_exit_order__mutmut_281",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "                    try:\n                        ok = modify(oid, price=tgt, symbol=symbol)\n                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:\n                        last_px = tgt\n                        log(f\"[EXIT_DRIVER][REPEG] {symbol} {side} order={oid} \"",
  "new": "                    try:\n                        ok = modify(oid, price=tgt, symbol=symbol)\n                    except Exception as e:\n                        log(None)\n                    if ok:\n                        last_px = tgt\n                        log(f\"[EXIT_DRIVER][REPEG] {symbol} {side} order={oid} \"",
  "what": "→ log(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_282",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0232, TC-EXEC-0233",
  "old": "                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:\n                        last_px = tgt\n                        log(f\"[EXIT_DRIVER][REPEG] {symbol} {side} order={oid} \"\n                            f\"#{k} limit={tgt} (touch-limit {base}, +{extra:.0%})\")\n        sleep(poll_s)",
  "new": "                    except Exception as e:\n                        log(f\"[EXIT_DRIVER][MODIFY_ERR] {oid} {e}\")\n                    if ok:\n                        last_px = None\n                        log(f\"[EXIT_DRIVER][REPEG] {symbol} {side} order={oid} \"\n                            f\"#{k} limit={tgt} (touch-limit {base}, +{extra:.0%})\")\n        sleep(poll_s)",
  "what": "→ last_px = None"
 },
 {
  "name": "x_drive_exit_order__mutmut_285",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "        sleep(poll_s)\n\n    # Deadline: never leave it working unattended — cancel, then settle.\n    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:",
  "new": "        sleep(poll_s)\n\n    # Deadline: never leave it working unattended — cancel, then settle.\n    log(None)\n    try:\n        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:",
  "what": "→ log(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_287",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0233",
  "old": "    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:\n        try:\n            executor.cancel_order(oid)",
  "new": "    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(oid, symbol=None)\n    except TypeError:\n        try:\n            executor.cancel_order(oid)",
  "what": "→ executor.cancel_order(oid, symbol=None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_288",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0233",
  "old": "    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:\n        try:\n            executor.cancel_order(oid)",
  "new": "    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(symbol=symbol)\n    except TypeError:\n        try:\n            executor.cancel_order(oid)",
  "what": "→ executor.cancel_order(symbol=symbol)"
 },
 {
  "name": "x_drive_exit_order__mutmut_289",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0233",
  "old": "    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:\n        try:\n            executor.cancel_order(oid)",
  "new": "    log(f\"[EXIT_DRIVER][TIMEOUT] {symbol} {side} order={oid} unfilled after \"\n        f\"{chase_s:.0f}s — cancelling\")\n    try:\n        executor.cancel_order(oid, )\n    except TypeError:\n        try:\n            executor.cancel_order(oid)",
  "what": "→ executor.cancel_order(oid, )"
 },
 {
  "name": "x_drive_exit_order__mutmut_290",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0233",
  "old": "        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:\n        try:\n            executor.cancel_order(oid)\n        except Exception as e:\n            log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n    except Exception as e:",
  "new": "        executor.cancel_order(oid, symbol=symbol)\n    except TypeError:\n        try:\n            executor.cancel_order(None)\n        except Exception as e:\n            log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n    except Exception as e:",
  "what": "→ executor.cancel_order(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_291",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "        try:\n            executor.cancel_order(oid)\n        except Exception as e:\n            log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n    except Exception as e:\n        log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n",
  "new": "        try:\n            executor.cancel_order(oid)\n        except Exception as e:\n            log(None)\n    except Exception as e:\n        log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n",
  "what": "→ log(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_292",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "        except Exception as e:\n            log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n    except Exception as e:\n        log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n\n    settle_end = clock() + settle_s\n    while True:",
  "new": "        except Exception as e:\n            log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n    except Exception as e:\n        log(None)\n\n    settle_end = clock() + settle_s\n    while True:",
  "what": "→ log(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_294",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "    except Exception as e:\n        log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n\n    settle_end = clock() + settle_s\n    while True:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None",
  "new": "    except Exception as e:\n        log(f\"[EXIT_DRIVER][CANCEL_ERR] {oid} {e}\")\n\n    settle_end = clock() - settle_s\n    while True:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None",
  "what": "→ settle_end = clock() - settle_s"
 },
 {
  "name": "x_drive_exit_order__mutmut_310",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    while True:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:",
  "new": "    while True:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"XXCOMPLETEXX\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:",
  "what": "→ if status == \"XXCOMPLETEXX\":           # filled in the cancel race"
 },
 {
  "name": "x_drive_exit_order__mutmut_311",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    while True:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:",
  "new": "    while True:\n        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"complete\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:",
  "what": "→ if status == \"complete\":           # filled in the cancel race"
 },
 {
  "name": "x_drive_exit_order__mutmut_312",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_313",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", None, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_314",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, None,\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, None,"
 },
 {
  "name": "x_drive_exit_order__mutmut_315",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               None, status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ None, status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_316",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), None)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 0), None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_317",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_318",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_319",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ return DriveResult(\"FILLED\", oid, int(info.get(\"filled_qty\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_320",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_321",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), )\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 0), )"
 },
 {
  "name": "x_drive_exit_order__mutmut_322",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"XXFILLEDXX\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"XXFILLEDXX\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_323",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"filled\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"filled\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_324",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(None),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(None),"
 },
 {
  "name": "x_drive_exit_order__mutmut_325",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") and 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") and 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_326",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(None) or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(info.get(None) or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_327",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"XXavg_priceXX\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(info.get(\"XXavg_priceXX\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_328",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"AVG_PRICE\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(info.get(\"AVG_PRICE\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_329",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "new": "        info = _read(executor, oid)\n        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 1.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),",
  "what": "→ return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 1.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_330",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(None), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(None), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_331",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") and 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") and 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_332",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(None) or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(None) or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_333",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"XXfilled_qtyXX\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"XXfilled_qtyXX\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_334",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"FILLED_QTY\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"FILLED_QTY\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_335",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "new": "        status = (info.get(\"status\") or \"\").upper() or None\n        if status == \"COMPLETE\":           # filled in the cancel race\n            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 1), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,",
  "what": "→ int(info.get(\"filled_qty\") or 1), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_337",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231, TC-EXEC-0233",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "what": "→ return DriveResult(None, oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_338",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", None, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "what": "→ return DriveResult(\"DEAD\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_341",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231, TC-EXEC-0233",
  "old": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break",
  "new": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), None,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break",
  "what": "→ int(info.get(\"filled_qty\") or 0), None,"
 },
 {
  "name": "x_drive_exit_order__mutmut_342",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               None)\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_347",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231, TC-EXEC-0233",
  "old": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ int(info.get(\"filled_qty\") or 0), str(info.get(\"status_message\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_348",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               )\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ )"
 },
 {
  "name": "x_drive_exit_order__mutmut_349",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231, TC-EXEC-0233",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"XXDEADXX\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "what": "→ return DriveResult(\"XXDEADXX\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_350",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231, TC-EXEC-0233",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"dead\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "what": "→ return DriveResult(\"dead\", oid, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_356",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "new": "            return DriveResult(\"FILLED\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status)\n        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 1.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:",
  "what": "→ return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 1.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_363",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(None))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ str(None))"
 },
 {
  "name": "x_drive_exit_order__mutmut_364",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") and \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ str(info.get(\"status_message\") and \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_365",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(None) or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ str(info.get(None) or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_366",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"XXstatus_messageXX\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ str(info.get(\"XXstatus_messageXX\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_367",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"STATUS_MESSAGE\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ str(info.get(\"STATUS_MESSAGE\") or \"\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_368",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "new": "        if status in DEAD_STATUSES:\n            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"XXXX\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)",
  "what": "→ str(info.get(\"status_message\") or \"XXXX\"))"
 },
 {
  "name": "x_drive_exit_order__mutmut_369",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() >= settle_end:\n            break\n        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"",
  "new": "            return DriveResult(\"DEAD\", oid, float(info.get(\"avg_price\") or 0.0),\n                               int(info.get(\"filled_qty\") or 0), status,\n                               str(info.get(\"status_message\") or \"\"))\n        if clock() > settle_end:\n            break\n        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"",
  "what": "→ if clock() > settle_end:"
 },
 {
  "name": "x_drive_exit_order__mutmut_372",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "crash",
  "old": "        if clock() >= settle_end:\n            break\n        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        if clock() >= settle_end:\n            break\n        sleep(poll_s)\n    log(None)\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ log(None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_374",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", None, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", None, float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_375",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, None,\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", oid, None,"
 },
 {
  "name": "x_drive_exit_order__mutmut_376",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       None, status)",
  "what": "→ None, status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_377",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), None)",
  "what": "→ int(info.get(\"filled_qty\") or 0), None)"
 },
 {
  "name": "x_drive_exit_order__mutmut_379",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", float(info.get(\"avg_price\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_380",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", oid, int(info.get(\"filled_qty\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_381",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230, TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       status)",
  "what": "→ status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_382",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), )",
  "what": "→ int(info.get(\"filled_qty\") or 0), )"
 },
 {
  "name": "x_drive_exit_order__mutmut_387",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(None) or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", oid, float(info.get(None) or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_388",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"XXavg_priceXX\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"XXavg_priceXX\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_389",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"AVG_PRICE\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"AVG_PRICE\") or 0.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_390",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "        sleep(poll_s)\n    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 1.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "what": "→ return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 1.0),"
 },
 {
  "name": "x_drive_exit_order__mutmut_392",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") and 0), status)",
  "what": "→ int(info.get(\"filled_qty\") and 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_393",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(None) or 0), status)",
  "what": "→ int(info.get(None) or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_394",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"XXfilled_qtyXX\") or 0), status)",
  "what": "→ int(info.get(\"XXfilled_qtyXX\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_395",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0231",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"FILLED_QTY\") or 0), status)",
  "what": "→ int(info.get(\"FILLED_QTY\") or 0), status)"
 },
 {
  "name": "x_drive_exit_order__mutmut_396",
  "module": "app/execution/exit_order_driver.py",
  "suite": "app/execution/test_mutmut_safety.py",
  "tc": "TC-EXEC-0230",
  "old": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 0), status)",
  "new": "    log(f\"[EXIT_DRIVER][UNRESOLVED] {symbol} {side} order={oid} status=\"\n        f\"{status} after cancel — order may STILL BE WORKING\")\n    return DriveResult(\"UNRESOLVED\", oid, float(info.get(\"avg_price\") or 0.0),\n                       int(info.get(\"filled_qty\") or 1), status)",
  "what": "→ int(info.get(\"filled_qty\") or 1), status)"
 }
]""")


HERE = os.path.dirname(os.path.abspath(__file__))


def extra_lists(only=None):
    """Mutants of the prove_kills_<name>.json files beside this script."""
    out = []
    for fn in sorted(os.listdir(HERE)):
        if not (fn.startswith("prove_kills_") and fn.endswith(".json")):
            continue
        if only and fn[len("prove_kills_"):-len(".json")] not in only:
            continue
        with open(os.path.join(HERE, fn), encoding="utf-8") as f:
            d = json.load(f)
        meta = d.get("meta", {})
        for m in d.get("mutants", []):
            out.append(dict({"module": meta["module"], "suite": meta["suite"], "deps": meta.get("deps", [])}, **m))
    return out


def run(tmp, suite):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PYTHONPATH=tmp, HOME=os.path.join(tmp, "home"), SCALP_TEST_MODE="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([sys.executable, os.path.join(tmp, suite)], cwd=tmp, env=env, capture_output=True,
                           text=True, timeout=180)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def stage(module, suite, text, deps=()):
    tmp = tempfile.mkdtemp(prefix="prove_kills_")
    os.makedirs(os.path.join(tmp, "home"))
    for rel in [module] + list(deps):
        parts = rel.split("/")
        for i in range(1, len(parts)):
            init = os.path.join(BACKEND, *parts[:i], "__init__.py")
            dst = os.path.join(tmp, *parts[:i], "__init__.py")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                continue
            if os.path.exists(init):
                shutil.copy2(init, dst)
            else:
                open(dst, "w").close()
        if rel != module:
            shutil.copy2(os.path.join(BACKEND, rel), os.path.join(tmp, rel))
    with open(os.path.join(tmp, module), "w", encoding="utf-8") as f:
        f.write(text)
    os.makedirs(os.path.dirname(os.path.join(tmp, suite)), exist_ok=True)
    shutil.copy2(os.path.join(BACKEND, suite), os.path.join(tmp, suite))
    return tmp


def main(argv):
    verbose = "-v" in argv
    jobs = int(argv[argv.index("-j") + 1]) if "-j" in argv else max(1, min(4, (os.cpu_count() or 2) - 1))
    names = [a for i, a in enumerate(argv) if not a.startswith("-") and (i == 0 or argv[i - 1] != "-j")]
    mutants = ([] if names else list(MUTANTS)) + extra_lists(names or None)
    fails = stale = killed = 0
    by_mod = {}
    for m in mutants:
        by_mod.setdefault((m["module"], m["suite"], tuple(m.get("deps", ()))), []).append(m)
    for (module, suite, deps), ms in by_mod.items():
        with open(os.path.join(BACKEND, module), encoding="utf-8") as f:
            src = f.read()
        tmp = stage(module, suite, src, deps)
        rc, out = run(tmp, suite)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"{'ok  ' if rc == 0 else 'FAIL'} main: {suite} passes on the untouched {module}"
              + ("" if rc == 0 else f"\n{out[-1500:]}"))
        fails += rc != 0

        def one(m):
            if src.count(m["old"]) != 1:
                return m, None
            tmp = stage(module, suite, src.replace(m["old"], m["new"], 1), deps)
            rc, _out = run(tmp, suite)
            shutil.rmtree(tmp, ignore_errors=True)
            return m, rc != 0

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(jobs) as ex:
            for m, ok in ex.map(one, ms):
                if ok is None:
                    stale += 1
                    print(f"STALE {m['name']}: its line is no longer in {module} once")
                    continue
                killed += ok
                fails += not ok
                print(f"{'ok  ' if ok else 'FAIL'} {m['name']} [{m['tc']}] {m['what']}"
                      + ("" if ok else "  <- SURVIVES"))
                if verbose:
                    print("      - " + m["old"].strip().replace("\n", "\n      - ")
                          + "\n      + " + m["new"].strip().replace("\n", "\n      + "))
    print(f"\n{killed} killed, {fails} failed, {stale} stale — {len(mutants)} mutants ({FENCE})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
