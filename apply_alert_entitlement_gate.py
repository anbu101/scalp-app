#!/usr/bin/env python3
# apply_alert_entitlement_gate.py — fence ALERT_ENTITLEMENT_GATE_20261021
# ============================================================================
# REPORT 2026-10-21: a non-admin user (no Scalp entitlement) sees Scalp EOD
# alerts in the bell.
#
# ROOT CAUSE: jobs/scalp_v3_live_eod.py is registered on EVERY install
# (api_server cron 15:25, not licence-gated) and — alone among the EOD jobs —
# calls record_alert UNCONDITIONALLY:
#     "SCALP_V3: end-of-day square-off complete — 0 position(s) closed."
# even when the engine never started (manager None). The bell feed
# (GET /api/app/events) has no entitlement filter, so every user gets it.
#
# FIX (three files; backend/app and, if present, desktop/src-tauri/backend/app)
#   1. jobs/scalp_v3_live_eod.py — bell only when something was actually
#      closed (the SCALP_V1 / IC CAS_NOTIF discipline: a daily "0 closed"
#      trains alert-blindness).
#   2. event_bus/inapp_events.py — entitlement gate in record_alert(): an
#      alert tagged with a strategy the licence does not include is dropped.
#      Kept: system-wide alerts (no strategy_id); everything when the
#      licence entitlements are not known yet (fail open — an alert path
#      never goes silent on a missing licence read); admin "*" sees all;
#      error/critical always pass (a revoked strategy can still hold a
#      position, and exit failures must reach the user).
#   3. jobs/brk_live_eod.py — record_alert(source="BRK_V1", ...) raised
#      TypeError (no such kwarg) inside a bare except, so BRK's "runtime
#      unreachable with OPEN LIVE position — MANUAL SQUARE-OFF REQUIRED" never
#      reached the bell. source= → strategy_id=.
#
# Run from the repo root:   python3 apply_alert_entitlement_gate.py
# Idempotent, staged all-or-nothing, .bak-ALERT_ENTITLEMENT_GATE_20261021
# backups, py_compile gate, behavioural tests before any write.
# ============================================================================

from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import tempfile
import types

FENCE = "ALERT_ENTITLEMENT_GATE_20261021"
TREES = ["backend/app", "desktop/src-tauri/backend/app"]

F_V3_EOD = "jobs/scalp_v3_live_eod.py"
F_INAPP = "event_bus/inapp_events.py"
F_BRK_EOD = "jobs/brk_live_eod.py"
FILES = [F_V3_EOD, F_INAPP, F_BRK_EOD]

PREREQ = {
    F_V3_EOD: "TRADING_DAY_GATE_20260816",
    F_INAPP: "CRITICAL_ALERT_ROUTING_20260909",
    F_BRK_EOD: "def brk_live_eod_job",
}


def die(msg: str) -> None:
    print(f"\n[{FENCE}] ABORT: {msg}\nNothing was written.")
    sys.exit(1)


GATE_BLOCK = '''# ── ALERT_ENTITLEMENT_GATE_20261021 BEGIN ──────────────────────────────────
# A licensed user must never see bell alerts for strategies their licence does
# not include (2026-10-21: a non-admin install got the daily "SCALP_V3:
# end-of-day square-off complete — 0 position(s) closed" bell).
#   * no strategy_id                  → system-wide → shown
#   * entitlements not known yet      → shown (fail open: never silence an
#                                       alert path on a missing licence read)
#   * admin "*" / strategy licensed   → shown
#   * error / critical                → shown (a revoked strategy can still
#                                       hold a position; exit failures matter)
#   * anything else                   → dropped (one audit line per strategy)
_ENT_GATE_ALWAYS = frozenset({"error", "critical"})
_ent_gate_logged: Set[str] = set()


def _alert_entitled(strategy_id: str, severity) -> bool:
    sid = str(strategy_id or "").strip().upper()
    if not sid:
        return True
    if str(severity or "").lower() in _ENT_GATE_ALWAYS:
        return True
    try:
        from app.license import license_state
        strategies = (license_state.ENTITLEMENTS or {}).get("strategies")
    except Exception:
        return True
    if not strategies:
        return True
    allowed = {str(s).strip().upper() for s in strategies}
    if "*" in allowed or sid in allowed:
        return True
    if sid not in _ent_gate_logged:
        _ent_gate_logged.add(sid)
        try:
            from app.event_bus.audit_logger import write_audit_log
            write_audit_log(f"[INAPP][ENT_GATE] {sid} bell alerts suppressed — "
                            f"not in this licence's strategies")
        except Exception:
            pass
    return False
# ── ALERT_ENTITLEMENT_GATE_20261021 END ────────────────────────────────────


'''

EDITS = {
    F_V3_EOD: [
        ("    record_alert(\n"
         "        \"EOD_SQUAREOFF\",\n"
         "        f\"SCALP_V3: end-of-day square-off complete — {closed} position(s) closed.\",\n"
         "        severity=\"info\",\n"
         "        strategy_id=STRATEGY_ID,\n"
         "    )",
         "    # ── ALERT_ENTITLEMENT_GATE_20261021 ── bell only when something was\n"
         "    # closed (SCALP_V1 / IC CAS_NOTIF discipline). It used to fire a daily\n"
         "    # \"0 position(s) closed\" on EVERY install, licensed for V3 or not.\n"
         "    if closed > 0:\n"
         "        record_alert(\n"
         "            \"EOD_SQUAREOFF\",\n"
         "            f\"SCALP_V3: end-of-day square-off complete — {closed} position(s) closed.\",\n"
         "            severity=\"info\",\n"
         "            strategy_id=STRATEGY_ID,\n"
         "        )"),
    ],
    F_INAPP: [
        ("def record_alert(\n    code: str,\n",
         GATE_BLOCK + "def record_alert(\n    code: str,\n"),
        ("    Never raises. Fires EVERY time it is called — use record_alert_once() for\n"
         "    conditions evaluated on a loop.\n"
         "    \"\"\"\n"
         "    record_event(\n"
         "        EVENT_ALERT,\n",
         "    Never raises. Fires EVERY time it is called — use record_alert_once() for\n"
         "    conditions evaluated on a loop.\n"
         "    \"\"\"\n"
         "    try:                                   # ── ALERT_ENTITLEMENT_GATE_20261021 ──\n"
         "        if not _alert_entitled(strategy_id, severity):\n"
         "            return\n"
         "    except Exception:\n"
         "        pass                               # gate failure → show (fail open)\n"
         "    record_event(\n"
         "        EVENT_ALERT,\n"),
    ],
    F_BRK_EOD: [
        ("        record_alert(source=\"BRK_V1\", code=\"EOD_JOB\", message=msg,\n"
         "                     severity=severity)\n",
         "        # ── ALERT_ENTITLEMENT_GATE_20261021 ── was a source= kwarg: not a\n"
         "        # record_alert kwarg → TypeError swallowed → bell never got it\n"
         "        record_alert(\"EOD_JOB\", msg, severity=severity,\n"
         "                     strategy_id=\"BRK_V1\")\n"),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural tests
# ─────────────────────────────────────────────────────────────────────────────

def run_tests(staged_src: dict) -> None:
    fails = []

    def check(name, cond):
        print(f"   {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            fails.append(name)

    # fake app.license.license_state + audit logger
    audit = []
    app_pkg = types.ModuleType("app")
    lic_pkg = types.ModuleType("app.license")
    lic_state = types.ModuleType("app.license.license_state")
    lic_state.ENTITLEMENTS = {}
    ev_pkg = types.ModuleType("app.event_bus")
    al = types.ModuleType("app.event_bus.audit_logger")
    al.write_audit_log = lambda m: audit.append(m)
    saved = {k: sys.modules.get(k) for k in
             ("app", "app.license", "app.license.license_state",
              "app.event_bus", "app.event_bus.audit_logger")}
    sys.modules.update({"app": app_pkg, "app.license": lic_pkg,
                        "app.license.license_state": lic_state,
                        "app.event_bus": ev_pkg,
                        "app.event_bus.audit_logger": al})
    lic_pkg.license_state = lic_state
    try:
        mod = types.ModuleType("inapp_under_test")
        exec(compile(staged_src[F_INAPP], "<patched inapp_events>", "exec"), mod.__dict__)

        def bell(after=0):
            return [e for e in mod.get_events_after(after)["events"]
                    if e["event_type"] == "ALERT"]

        V3_MSG = "SCALP_V3: end-of-day square-off complete — 0 position(s) closed."

        # T1 friend's licence: no SCALP strategies
        lic_state.ENTITLEMENTS = {"strategies": ["TMA_V2", "IC_V2", "VET_V1"],
                                  "ui_level": "standard"}
        n0 = len(bell())
        mod.record_alert("EOD_SQUAREOFF", V3_MSG, severity="info", strategy_id="SCALP_V3")
        check("T1a non-entitled SCALP_V3 info → dropped", len(bell()) == n0)
        mod.record_alert("RISK", "warn", severity="warning", strategy_id="SCALP_V1")
        check("T1b non-entitled SCALP_V1 warning → dropped", len(bell()) == n0)
        check("T1c one audit line per suppressed strategy",
              sum("[ENT_GATE] SCALP_V3" in a for a in audit) == 1)
        mod.record_alert("EOD_SQUAREOFF", "again", severity="info", strategy_id="SCALP_V3")
        check("T1d no audit spam on repeat",
              sum("[ENT_GATE] SCALP_V3" in a for a in audit) == 1)
        mod.record_alert("EOD_SQUAREOFF", "TMA_V2 closed 1", severity="info", strategy_id="TMA_V2")
        check("T1e entitled TMA_V2 → shown", len(bell()) == n0 + 1)
        mod.record_alert("EOD_SQUAREOFF", "case", severity="info", strategy_id="vet_v1")
        check("T1f case-insensitive match (vet_v1)", len(bell()) == n0 + 2)
        mod.record_alert("RELAY", "relay down", severity="warning", strategy_id="")
        check("T1g system-wide (no strategy) → shown", len(bell()) == n0 + 3)
        mod.record_alert("EXIT_FAIL", "SCALP_V3 exit failed", severity="error", strategy_id="SCALP_V3")
        check("T1h error from non-entitled strategy → still shown", len(bell()) == n0 + 4)
        mod.record_alert("EOD_WATCHDOG", "crit", severity="critical", strategy_id="SCALP_V5")
        check("T1i critical from non-entitled strategy → still shown", len(bell()) == n0 + 5)

        # T2 admin licence sees everything
        lic_state.ENTITLEMENTS = {"strategies": ["*"], "ui_level": "admin"}
        n0 = len(bell())
        mod.record_alert("EOD_SQUAREOFF", V3_MSG, severity="info", strategy_id="SCALP_V3")
        check("T2 admin '*' → SCALP_V3 shown", len(bell()) == n0 + 1)

        # T3 entitlements unknown (boot / offline) → fail open
        lic_state.ENTITLEMENTS = {}
        n0 = len(bell())
        mod.record_alert("EOD_SQUAREOFF", V3_MSG, severity="info", strategy_id="SCALP_V3")
        check("T3a empty entitlements → shown (fail open)", len(bell()) == n0 + 1)
        lic_state.ENTITLEMENTS = None
        mod.record_alert("EOD_SQUAREOFF", V3_MSG, severity="info", strategy_id="SCALP_V3")
        check("T3b ENTITLEMENTS None → shown, no raise", len(bell()) == n0 + 2)

        # T4 record_alert_once path is gated too
        lic_state.ENTITLEMENTS = {"strategies": ["TMA_V2"]}
        n0 = len(bell())
        mod.record_alert_once("k1", "MAX_LOSS", "x", severity="warning", strategy_id="SCALP_V1")
        check("T4 record_alert_once non-entitled → dropped", len(bell()) == n0)

        # T5 trade events untouched by the gate
        n_all = len(mod.get_events_after(0)["events"])
        mod.record_event("ENTER", strategy_id="SCALP_V1", symbol="X")
        check("T5 trade events not gated", len(mod.get_events_after(0)["events"]) == n_all + 1
              or len(mod.get_events_after(0)["events"]) == mod._MAX_EVENTS)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    # T6 SCALP_V3 EOD: bell only when closed > 0 (source-level)
    v3 = staged_src[F_V3_EOD]
    check("T6a V3 EOD record_alert now guarded by 'if closed > 0'",
          "    if closed > 0:\n        record_alert(" in v3)
    check("T6b no unguarded V3 record_alert left",
          v3.count("record_alert(\n") == 1)

    # T7 BRK: call shape matches record_alert's signature
    brk = staged_src[F_BRK_EOD]
    check("T7a BRK no longer passes source=", "source=\"BRK_V1\"" not in brk)
    import inspect
    sig = inspect.signature(mod.record_alert)
    try:
        sig.bind("EOD_JOB", "msg", severity="critical", strategy_id="BRK_V1")
        ok = True
    except TypeError:
        ok = False
    check("T7b BRK call binds to record_alert's signature", ok)
    try:
        sig.bind(source="BRK_V1", code="EOD_JOB", message="m", severity="error")
        old_ok = True
    except TypeError:
        old_ok = False
    check("T7c (proof) the OLD call shape raises TypeError", not old_ok)

    if fails:
        die(f"{len(fails)} behavioural test(s) failed: {fails}")


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.path.isdir("backend/app"):
        die("run from the repo root (the folder that contains backend/)")
    trees = [t for t in TREES if os.path.isdir(t)]
    print(f"[{FENCE}] trees: {', '.join(trees)}")
    if "desktop/src-tauri/backend/app" not in trees:
        print("   note: desktop/src-tauri/backend/app not present — backend tree only")

    staged, applied = {}, 0
    for tree in trees:
        for rel in FILES:
            path = os.path.join(tree, rel)
            if not os.path.isfile(path):
                die(f"missing file {path}")
            src = open(path, encoding="utf-8").read()
            if FENCE in src:
                applied += 1
                continue
            if PREREQ[rel] not in src:
                die(f"{path}: expected marker {PREREQ[rel]!r} not found — wrong working copy?")
            new = src
            for i, (old, rep) in enumerate(EDITS[rel], 1):
                n = new.count(old)
                if n != 1:
                    die(f"{path}: edit #{i} anchor found {n} times (need exactly 1)")
                new = new.replace(old, rep, 1)
            staged[path] = (src, new)

    total = len(trees) * len(FILES)
    if applied == total:
        print(f"[{FENCE}] already applied everywhere — nothing to do.")
        return

    try:
        out = subprocess.run(["git", "status", "--porcelain", "--"] +
                             [f"backend/app/{f}" for f in FILES],
                             capture_output=True, text=True, timeout=20).stdout
        dirty = [ln for ln in out.splitlines() if ln.strip()]
        if dirty:
            die("uncommitted changes on files this script edits:\n   " +
                "\n   ".join(dirty) + "\n   commit/push or revert them first")
    except FileNotFoundError:
        print("   note: git not found — skipping working-copy check")

    if applied:
        die(f"mixed state: fence present in {applied}/{total} files")

    for path, (_, new) in staged.items():
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
            tf.write(new)
            tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            die(f"{path} does not compile after patching:\n{e}")
        finally:
            os.unlink(tmp)
    print(f"[{FENCE}] py_compile OK on {len(staged)} file(s)")

    print(f"[{FENCE}] behavioural tests")
    run_tests({rel: staged[f"backend/app/{rel}"][1] for rel in FILES})

    for path, (old, _) in staged.items():
        with open(f"{path}.bak-{FENCE}", "w", encoding="utf-8") as f:
            f.write(old)
    for path, (_, new) in staged.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"   wrote {path}")
    print(f"\n[{FENCE}] DONE — {len(staged)} file(s) patched, backups *.bak-{FENCE}.")
    print("Rebuild + ship to your friend. On his install expect, at most once per")
    print("strategy per run:  [INAPP][ENT_GATE] SCALP_V3 bell alerts suppressed ...")


if __name__ == "__main__":
    main()
