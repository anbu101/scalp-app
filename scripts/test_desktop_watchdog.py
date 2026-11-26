#!/usr/bin/env python3
# scripts/test_desktop_watchdog.py — ── WATCHDOG_RESTART_20261126 ── (DEF-029, TD-094) TC-DESK-0007…0010
#
# The desktop watchdog must be able to restart a crashed backend, and must stay
# away while an update installs. The behaviour is unit-tested in Rust
# (desktop/src-tauri/src/runtime.rs, TC-DESK-0001…0006: `cargo test`); this
# stdlib suite pins the wiring in the source so the weekly regression sees a
# revert without a Rust toolchain. With --cargo it also runs those Rust tests.
#   python3 scripts/test_desktop_watchdog.py [--cargo]
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "desktop", "src-tauri", "src")
F = 0


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


rt = open(os.path.join(SRC, "runtime.rs"), encoding="utf-8").read()
mn = open(os.path.join(SRC, "main.rs"), encoding="utf-8").read()

body = rt[rt.index("pub fn start_backend() -> Result<(), String> {"):rt.index("pub fn stop_backend() {")]
chk("TC-DESK-0007", "start_backend() takes the start flag through StartingGuard only (no bare store/compare_exchange "
    "that a return path can skip), and reaps an exited backend before the 'already exists' check",
    "StartingGuard::acquire()" in body and "BACKEND_STARTING.store" not in body
    and "compare_exchange" not in body and "reap_exited(&mut process_guard)" in body, body[:400])

wd = rt[rt.index("pub fn start_backend_watchdog() {"):rt.index("pub fn start_watchdog() {")]
chk("TC-DESK-0008", "the watchdog loop reads the update pause and decides through watchdog_step()",
    "WATCHDOG_PAUSED.load" in wd and "watchdog_step(paused, alive, *attempts)" in wd, wd[:400])

upd = mn[mn.index("async fn stop_backend_for_update()"):mn.index("fn main()")]
i_pause, i_stop = upd.find("runtime::pause_watchdog("), upd.find("runtime::stop_backend()")
chk("TC-DESK-0009", "stop_backend_for_update pauses the watchdog BEFORE it stops the backend; window close and "
    "destroy pause it too", 0 <= i_pause < i_stop and mn.count("runtime::pause_watchdog(") >= 3, (i_pause, i_stop))

tests = re.findall(r"fn (tc_desk_\d{4})_", rt)
chk("TC-DESK-0010", "the Rust unit tests TC-DESK-0001…0006 are in runtime.rs",
    tests == [f"tc_desk_{n:04d}" for n in range(1, 7)], tests)

if "--cargo" in sys.argv:
    cargo = shutil.which("cargo")
    if not cargo:
        print("  skip cargo test (no cargo on this machine)")
    else:
        r = subprocess.run([cargo, "test", "--bin", "app", "watchdog_restart_tests", "--", "--test-threads=1"],
                           cwd=os.path.join(ROOT, "desktop", "src-tauri"), capture_output=True, text=True, timeout=3600)
        out = r.stdout + r.stderr
        chk("TC-DESK-0001…0006", "cargo test watchdog_restart_tests", r.returncode == 0 and "6 passed" in out, out[-800:])

print("\n" + ("ALL DESKTOP WATCHDOG CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
