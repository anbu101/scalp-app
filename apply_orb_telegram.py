#!/usr/bin/env python3
# apply_orb_telegram.py — ORB_V1 entries/exits were never reaching
# Telegram: OrbManager._notify sent to an injected `notifier` that only
# the TEST SUITE ever injects (the runtime never did), so every call
# returned early. The exit also named notify_trade_exit, which telegram_api
# does not have.
#
# Fence: ORB_TELEGRAM_20260918   Anchored edits on today's manager (no replacement).
#
#   1. _notify -> BRK's production path verbatim: app.api.telegram_api
#      functions by name (the per-strategy toggle lives inside them); the
#      injected notifier remains a test seam. Failures now audit-log.
#   2. Exit routed by reason like BRK: SL -> notify_sl_exit, TP ->
#      notify_tp_exit, everything else (EOD/KILL/FROZEN) ->
#      notify_manual_exit, with the keys the formatters read.
#   3. Entry gets a `note` (which edge fired, stop is a SPOT level, TP %).
#   4. Tests: reason->function routing asserted for SL and EOD, payload
#      keys asserted, recorder catches any notify_* by name.
#
# Backend only; rebuild + restart. LIVE-path file — apply after 15:30.
# USAGE: python3 apply_orb_telegram.py --check && python3 apply_orb_telegram.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_TELEGRAM_20260918'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {}

EDITS = [('backend/app/engine/orb/orb_manager.py', 'replace', '    def _notify(self, fn_name, payload):\n        if not self.notifier:\n            return\n        try:\n            getattr(self.notifier, fn_name)(payload)\n        except Exception:\n            pass\n', '    def _notify(self, fn_name, payload):\n        # ── ORB_TELEGRAM_20260918 ── the injected notifier exists for tests\n        # only; nothing in the runtime ever injected one, so every ORB entry\n        # and exit was silently dropped here (found 2026-09-18). Production\n        # path is BRK\'s, verbatim: app.api.telegram_api functions by name\n        # (the per-strategy toggle lives inside them).\n        target = self.notifier\n        if target is None:\n            try:\n                from app.api import telegram_api as target\n            except Exception:\n                return\n        try:\n            getattr(target, fn_name)(payload)\n        except Exception as e:\n            write_audit_log(f"[ORB][NOTIFY_FAIL] {fn_name}: {e!r}")\n', 1), ('backend/app/engine/orb/orb_manager.py', 'replace', '            "strategy_id": STRATEGY_ID, "mode": mode, "symbol": symbol,\n            "side": side, "entry_price": entry_px, "quantity": qty,\n            "sl": round(core_pos.sl_spot, 2), "tp": round(core_pos.tp_prem, 2)})\n        return True\n', '            "strategy_id": STRATEGY_ID, "mode": mode, "symbol": symbol,\n            "side": side, "entry_price": entry_px, "quantity": qty,\n            "sl": round(core_pos.sl_spot, 2), "tp": round(core_pos.tp_prem, 2),\n            # ── ORB_TELEGRAM_20260918 ── the formatter prints `note`; say\n            # which edge fired and that the stop is a SPOT level.\n            "note": (f"Outrider {side} \\u00b7 touch of ORB "\n                     f"{\'high\' if side == \'CE\' else \'low\'} \\u00b7 stop is a SPOT "\n                     f"level ({core_pos.sl_spot:.2f}, 1m close) \\u00b7 TP "\n                     f"+{float(self.cfg().get(\'target_value\') or 50):g}%")})\n        return True\n', 1), ('backend/app/engine/orb/orb_manager.py', 'replace', '        self._notify("notify_trade_exit", {\n            "strategy_id": STRATEGY_ID, "mode": pos.mode, "symbol": pos.symbol,\n            "exit_price": px, "reason": reason,\n            "pnl": round(gross, 2)})\n', '        # ── ORB_TELEGRAM_20260918 ── telegram_api has no notify_trade_exit;\n        # route by reason exactly as BRK does (SL/TP/manual) with the keys\n        # the formatters read (entry_price, exit_price, side, quantity).\n        fn = {"SL": "notify_sl_exit", "TP": "notify_tp_exit"}.get(\n            reason, "notify_manual_exit")\n        self._notify(fn, {\n            "strategy_id": STRATEGY_ID, "mode": pos.mode, "symbol": pos.symbol,\n            "side": pos.side, "entry_price": pos.entry_px, "exit_price": px,\n            "quantity": pos.qty, "pnl": round(gross, 2),\n            "exit_reason": reason, "reason": reason})\n', 1), ('backend/app/engine/orb/test_orb_manager.py', 'replace', 'class RecNotifier:\n    def __init__(self): self.payloads = []\n    def notify_trade_entry(self, p): self.payloads.append(("entry", p))\n    def notify_trade_exit(self, p): self.payloads.append(("exit", p))\n', 'class RecNotifier:\n    # ── ORB_TELEGRAM_20260918 ── records ANY notify_* by name so the\n    # reason->function routing is asserted, not just the payload keys.\n    def __init__(self): self.payloads = []\n    def __getattr__(self, name):\n        if name.startswith("notify_"):\n            return lambda p: self.payloads.append((name, p))\n        raise AttributeError(name)\n', 1), ('backend/app/engine/orb/test_orb_manager.py', 'replace', 'check("every notify payload carries strategy_id (never \'strategy\')",\n      len(nrec.payloads) == 2\n      and all(p.get("strategy_id") == "ORB_V1" and "strategy" not in p\n              for _, p in nrec.payloads), str(nrec.payloads))\n', 'check("every notify payload carries strategy_id (never \'strategy\')",\n      len(nrec.payloads) == 2\n      and all(p.get("strategy_id") == "ORB_V1" and "strategy" not in p\n              for _, p in nrec.payloads), str(nrec.payloads))\ncheck("entry -> notify_trade_entry; SL exit -> notify_sl_exit (BRK routing)",\n      [n for n, _ in nrec.payloads] == ["notify_trade_entry", "notify_sl_exit"],\n      str([n for n, _ in nrec.payloads]))\ncheck("exit payload carries the keys the formatter reads",\n      all(k in nrec.payloads[1][1] for k in\n          ("entry_price", "exit_price", "side", "quantity", "pnl", "mode")))\n_m_eod = fresh(); _m_eod.notifier = RecNotifier()\ndrive(_m_eod, window() + [m1(20, 106, 110.5, 105, 109)]\n      + [m1(k, 109, 111, 108, 110) for k in range(21, 226)],\n      lambda mgr, a, bar: mgr.open_trade(symbol="NIFTYCE", token=1, side=a[1],\n                                         ltp=172.0, entry_spot=109.0, sig_ts=a[2]))\ncheck("EOD exit -> notify_manual_exit",\n      [n for n, _ in _m_eod.notifier.payloads][-1] == "notify_manual_exit")\n', 1)]

VERIFY = [('backend/app/engine/orb/orb_manager.py', 'ORB_TELEGRAM_20260918', 3), ('backend/app/engine/orb/orb_manager.py', 'from app.api import telegram_api', 1), ('backend/app/engine/orb/orb_manager.py', 'notify_manual_exit', 1), ('backend/app/engine/orb/test_orb_manager.py', 'notify_manual_exit', 1)]



def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    """A backend-relative path lands in both trees; frontend in one."""
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def stage_edit(text, kind, anchor, payload, count, path):
    n = text.count(anchor)
    if kind == "replaceall":
        if n != count:
            fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
        return text.replace(anchor, payload)
    if n != count:
        fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
    if kind == "replace":
        return text.replace(anchor, payload)
    if kind == "before":
        return text.replace(anchor, payload + anchor)
    if kind == "after":
        return text.replace(anchor, anchor + payload)
    fail(f"unknown edit kind {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--single-tree", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard "
             "requirement locally; pass --single-tree only on a CI checkout")

    # ── prerequisite + idempotency ──
    probe = os.path.join(ROOT, "backend", "app", "engine", "orb", "orb_manager.py")
    ptext = open(probe, encoding="utf-8").read()
    if "ORB_DAY1SCARS_20260904" not in ptext:
        fail("manager older than ORB_DAY1SCARS_20260904 — pull main first")
    if FENCE in ptext:
        print(f"  SKIP   telegram fix already present — "
              f"nothing to do")
        return

    # ── stage every write in memory first ──
    staged = {}   # abs path -> new text
    for rel, body in PAYLOADS.items():
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                shutil.copy2(p, p + ".bak-" + FENCE)
            staged[p] = body
    per_file = {}
    for rel, kind, anchor, payload, count in EDITS:
        per_file.setdefault(rel, []).append((kind, anchor, payload, count))
    for rel, ops in per_file.items():
        src_path = os.path.join(ROOT, rel)
        if not os.path.exists(src_path):
            fail(f"{src_path} not found")
        text = open(src_path, encoding="utf-8").read()
        if FENCE in text:
            fail(f"{rel} already carries the fence — mixed state, resolve by hand")
        for kind, anchor, payload, count in ops:
            text = stage_edit(text, kind, anchor, payload, count, rel)
        for p in both_trees(rel, a.single_tree):
            if p != src_path and not os.path.exists(p):
                fail(f"dual-tree copy missing: {p}")
            staged[p] = text

    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── staged compile gates ──
    tmp = tempfile.mkdtemp(prefix="orv_gate_")
    jsx_targets = []
    for p, body in staged.items():
        t = os.path.join(tmp, os.path.basename(p))
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        elif p.endswith((".jsx", ".js")):
            jsx_targets.append((p, t))
    print(f"  OK     py_compile gate passed")
    esb = shutil.which("esbuild")
    npx = shutil.which("npx")
    for p, t in jsx_targets:
        cmd = None
        if esb:
            cmd = [esb, "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        elif npx:
            cmd = [npx, "--yes", "esbuild", "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        if cmd is None:
            print(f"  WARN   esbuild unavailable — JSX gate skipped for {p}")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "frontend"))
        if r.returncode != 0:
            fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
    if jsx_targets and (esb or npx):
        print(f"  OK     esbuild JSX gate passed ({len(jsx_targets)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  write {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write, with backups for edited files ──
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    # ── grep-count verification ──
    bad = 0
    for rel, needle, mn in VERIFY:
        got = open(os.path.join(ROOT, rel), encoding="utf-8").read().count(needle)
        ok = got >= mn
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} (need >= {mn})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   ORB entries/exits now reach telegram_api. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD python3 app/engine/orb/test_orb_manager.py && full rebuild + restart")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
