# test_relay_recovery.py — RELAY_RECOVERY_SAFE_20261115
# The relay health monitor must not restart or reboot a relay that is healthy,
# and must never loop. Fake SSH, fake clock; no network.
#   PYTHONPATH=backend python3 backend/app/services/test_relay_recovery.py
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = os.environ.get("SCALP_BACKEND") or str(HERE.parents[2])
sys.path.insert(0, ROOT)
os.environ.setdefault("SCALP_TEST_MODE", "1")

from app.services import relay_deployer as R          # noqa: E402

PASSED, FAILS = [0], []


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"[ok  ] {name}")
    else:
        FAILS.append(name)
        print(f"[MISS] {name}\n         -> {detail}")


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class World:
    """One relay server as the app sees it over SSH."""

    def __init__(self):
        self.ssh_ok = True
        self.healthy = True            # what `curl localhost:8001/health` says on the server
        self.heal_after_restart = 0    # health polls after a restart until it answers (0 = never)
        self.cmds, self.alerts, self.tg = [], [], []
        self._polls_since_restart = None

    def run(self, client, cmd, timeout=30):
        self.cmds.append(cmd)
        if "curl" in cmd:
            if self._polls_since_restart is not None and self.heal_after_restart:
                self._polls_since_restart += 1
                if self._polls_since_restart >= self.heal_after_restart:
                    self.healthy = True
            return True, ('{"relay": "scalp-terminal"}' if self.healthy else ""), ""
        if "systemctl restart" in cmd:
            self._polls_since_restart = 0
            return True, "", ""
        return True, "", ""                       # shutdown -r / sysrq

    @property
    def restarts(self):
        return sum("systemctl restart" in c for c in self.cmds)

    @property
    def reboots(self):
        return sum("shutdown -r" in c or "sysrq" in c for c in self.cmds)


def setup():
    w, clock = World(), Clock()

    class SSHClient:
        def set_missing_host_key_policy(self, p): pass

        def connect(self, **kw):
            if not w.ssh_ok:
                raise OSError("timed out")

        def close(self): pass

    sys.modules["paramiko"] = types.SimpleNamespace(SSHClient=SSHClient, AutoAddPolicy=lambda: None)
    R._load_private_key = lambda k: object()
    R._run = w.run
    R.time = clock
    R._send_relay_telegram = lambda m: w.tg.append(m)
    R.record_alert = lambda *a, **k: w.alerts.append(a)
    R.write_audit_log = lambda m: None
    for name in ("_recovery_last", "_reboot_last", "_net_alert_last"):
        getattr(R, name, {}).clear()
    return w, clock


def recover():
    return R._attempt_relay_recovery("10.0.0.1", "root", "KEY")


def t_healthy_server_is_not_touched():
    w, clock = setup()
    out = recover()
    check("relay healthy on the server (this computer's network is the fault): NOT restarted, NOT rebooted",
          out == "HEALTHY_ON_SERVER" and w.restarts == 0 and w.reboots == 0,
          f"outcome={out} restarts={w.restarts} reboots={w.reboots}")
    check("…and the user is told once to check this computer's network",
          len(w.tg) == 1 and len(w.alerts) == 1, f"telegram={len(w.tg)} bell={len(w.alerts)}")
    for _ in range(12):                               # an hour of a broken local network
        clock.t += 301
        recover()
    check("…an hour of that: still no restart, no reboot, at most 3 notices",
          w.restarts == 0 and w.reboots == 0 and len(w.tg) <= 3,
          f"restarts={w.restarts} reboots={w.reboots} notices={len(w.tg)}")


def t_cooldown():
    w, clock = setup()
    w.healthy, w.heal_after_restart = False, 1
    a = recover()
    w.healthy = False; w._polls_since_restart = None
    clock.t += 60
    b = recover()
    check("a second failure 60 s after a recovery attempt does nothing (cooldown)",
          a == "RESTARTED" and b == "COOLDOWN" and w.restarts == 1,
          f"first={a} second={b} restarts={w.restarts}")


def t_restart_given_time():
    w, clock = setup()
    w.healthy, w.heal_after_restart = False, 3        # answers on the 3rd poll (~15 s)
    out = recover()
    check("a relay that needs 15 s after a restart is NOT rebooted",
          out == "RESTARTED" and w.restarts == 1 and w.reboots == 0,
          f"outcome={out} restarts={w.restarts} reboots={w.reboots}")


def t_reboot_once():
    w, clock = setup()
    w.healthy = False                                  # a restart never helps
    a = recover()
    clock.t += 301
    b = recover()
    check("a dead relay is rebooted once, not again 5 minutes later",
          a == "REBOOTED" and b == "REBOOT_COOLDOWN" and w.reboots == 1,
          f"first={a} second={b} reboots={w.reboots}")
    check("…and the user is told to check the server by hand", any("by hand" in m for m in w.tg), str(w.tg)[:120])
    clock.t += 1800
    c = recover()
    check("…after 30 minutes a second reboot is allowed", c == "REBOOTED" and w.reboots == 2, f"{c} {w.reboots}")


def t_ssh_down():
    w, clock = setup()
    w.ssh_ok = False
    out = recover()
    check("server unreachable over SSH: nothing is run", out == "SSH_FAILED" and not w.cmds, f"{out} {w.cmds}")


def t_monitor_loop_cannot_storm():
    """The real monitor loop body, 40 polls (20 minutes), every health check failing
    while the server itself is healthy."""
    w, clock = setup()
    cfg = {"enabled": True, "relays": [{"url": "http://10.0.0.1:8001", "host": "10.0.0.1",
                                         "is_primary": True, "ssh_username": "root", "ssh_key": "KEY"}]}

    class P:
        def exists(self): return True

        def read_text(self): return __import__("json").dumps(cfg)
    R.RELAY_CONFIG_PATH = P()
    R._check_relay_health_with_retry = lambda url: False
    R.record_alert_once = lambda *a, **k: None
    R.is_alert_active = lambda k: False
    R._relay_fail_count = 0
    for _ in range(40):
        R._run_one_relay_check()
        clock.t += 30
    check("monitor: 20 minutes of failed checks against a healthy server → 0 restarts, 0 reboots",
          w.restarts == 0 and w.reboots == 0, f"restarts={w.restarts} reboots={w.reboots}")
    w2, clock2 = setup()
    w2.healthy = False
    R._check_relay_health_with_retry = lambda url: False
    R._relay_fail_count = 0
    for _ in range(40):
        R._run_one_relay_check()
        clock2.t += 30
    check("monitor: 20 minutes against a dead server → at most 1 reboot and 4 restarts",
          w2.reboots <= 1 and w2.restarts <= 4, f"restarts={w2.restarts} reboots={w2.reboots}")


if __name__ == "__main__":
    for t in (t_healthy_server_is_not_touched, t_cooldown, t_restart_given_time, t_reboot_once,
              t_ssh_down, t_monitor_loop_cannot_storm):
        try:
            t()
        except Exception as e:
            FAILS.append(t.__name__)
            print(f"[ERROR] {t.__name__}: {e!r}")
    print(f"\n{PASSED[0]} passed, {len(FAILS)} failed")
    if FAILS:
        sys.exit(1)
    print("ALL RELAY RECOVERY CHECKS PASSED")
