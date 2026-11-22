#!/usr/bin/env python3
# scripts/regression/schedule.py — ── MASTER_SUITE_20261122 ── (TD-079)
#
# Runs the master regression suite every week on this Mac, from a launchd
# agent (~/Library/LaunchAgents/com.scalp.weekly-regression.plist).
#
#   python3 scripts/regression/schedule.py install                 Friday 16:00 (local time = IST here)
#   python3 scripts/regression/schedule.py install --day sat --time 22:00   another day / time
#                                   (install again to change it: the old job is replaced)
#   python3 scripts/regression/schedule.py status                  installed? when? last run?
#   python3 scripts/regression/schedule.py run-now                 start the agent's job once, now
#   python3 scripts/regression/schedule.py uninstall
#   options: --out DIR (reports; default ~/scalp-regression)  --print (show the plist, change nothing)
#
# The job runs `master_suite.py run --notify` under `caffeinate -i` (the Mac
# does not idle-sleep mid-run) with this shell's PATH, so npm / node / Chrome
# are found. A Mac that is asleep at the time runs the job when it wakes; one
# that is shut down skips that week. To wake it just before, once (needs your
# password):  sudo pmset repeat wakeorpoweron F 15:55:00
# Output of the job itself: <out>/launchd.log.
import os
import plistlib
import re
import subprocess
import sys

FENCE = "MASTER_SUITE_20261122"
LABEL = "com.scalp.weekly-regression"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUITE = os.path.join(ROOT, "scripts", "regression", "master_suite.py")
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def plist_path(agents=AGENTS):
    return os.path.join(agents, LABEL + ".plist")


def parse_when(day, hhmm):
    d = DAYS.get((day or "").lower()[:3])
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", hhmm or "")
    if d is None or not m:
        raise ValueError("--day sun..sat and --time HH:MM (24h)")
    return d, int(m.group(1)), int(m.group(2))


def build_plist(weekday, hour, minute, out, python=sys.executable, path_env=None, chrome=None):
    env = {"PATH": path_env or os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
           "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1"}
    chrome = chrome if chrome is not None else (os.environ.get("CHROME") or
                                                (CHROME_MAC if os.path.exists(CHROME_MAC) else ""))
    if chrome:
        env["CHROME"] = chrome
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", python, SUITE, "run", "--notify", "--quiet", "--out", out],
        "WorkingDirectory": ROOT,
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "EnvironmentVariables": env,
        "StandardOutPath": os.path.join(out, "launchd.log"),
        "StandardErrorPath": os.path.join(out, "launchd.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 5,
        "RunAtLoad": False,
    }


def launchctl(*a):
    r = subprocess.run(["launchctl", *a], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def describe(pl):
    c = pl.get("StartCalendarInterval", {})
    day = {v: k for k, v in DAYS.items()}.get(c.get("Weekday"), "?")
    return f"every {day.capitalize()} at {c.get('Hour', 0):02d}:{c.get('Minute', 0):02d} (local time)"


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(open(__file__, encoding="utf-8").read().split("\nimport os")[0])
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    opt = {"--day": "fri", "--time": "16:00", "--out": os.path.expanduser("~/scalp-regression")}
    show = "--print" in rest
    it = iter(x for x in rest if x != "--print")
    try:
        for a in it:
            if a not in opt:
                raise ValueError(f"unknown option {a}")
            opt[a] = next(it)
        wd, hh, mm = parse_when(opt["--day"], opt["--time"])
    except (StopIteration, ValueError) as e:
        print(f"bad arguments: {e}", file=sys.stderr)
        return 2
    out = os.path.abspath(os.path.expanduser(opt["--out"]))
    p = plist_path()
    uid = str(os.getuid())

    if cmd == "install":
        pl = build_plist(wd, hh, mm, out)
        data = plistlib.dumps(pl)
        if show:
            sys.stdout.write(data.decode())
            return 0
        if sys.platform != "darwin":
            print("launchd is macOS only", file=sys.stderr)
            return 2
        os.makedirs(AGENTS, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        launchctl("bootout", f"gui/{uid}/{LABEL}")            # replace an older install
        with open(p, "wb") as f:
            f.write(data)
        rc, msg = launchctl("bootstrap", f"gui/{uid}", p)
        if rc != 0:
            print(f"launchctl bootstrap failed: {msg}", file=sys.stderr)
            return 1
        print(f"installed {LABEL}: {describe(pl)}\n  plist   {p}\n  reports {out}/latest/report.html\n"
              f"  python  {pl['ProgramArguments'][2]}\n"
              "  A sleeping Mac runs it on wake; a shut-down one skips the week. To wake it first (once):\n"
              f"    sudo pmset repeat wakeorpoweron {'MTWRFSU'[(wd - 1) % 7]} "
              f"{(hh * 60 + mm - 5) // 60 % 24:02d}:{(hh * 60 + mm - 5) % 60:02d}:00")
        return 0
    if cmd == "uninstall":
        launchctl("bootout", f"gui/{uid}/{LABEL}")
        if os.path.exists(p):
            os.remove(p)
            print(f"removed {p}")
        else:
            print("not installed")
        return 0
    if cmd == "status":
        if not os.path.exists(p):
            print("not installed — python3 scripts/regression/schedule.py install")
            return 1
        with open(p, "rb") as f:
            pl = plistlib.load(f)
        rc, _ = launchctl("print", f"gui/{uid}/{LABEL}")
        print(f"{LABEL}: {describe(pl)} · {'loaded' if rc == 0 else 'NOT loaded (re-run install)'}")
        o = pl["ProgramArguments"][pl["ProgramArguments"].index("--out") + 1]
        latest = os.path.join(o, "latest", "results.json")
        if os.path.exists(latest):
            import json
            with open(latest, encoding="utf-8") as f:
                R = json.load(f)
            c = R["counts"]
            print(f"last run {R['started'][:16].replace('T', ' ')}: {c['pass']} passed, "
                  f"{c['fail'] + c['timeout']} failed — {os.path.join(o, 'latest', 'report.html')}")
        else:
            print("no run yet")
        return 0
    if cmd == "run-now":
        rc, msg = launchctl("kickstart", f"gui/{uid}/{LABEL}")
        print("started — watch the notification, or the report in ~/scalp-regression/latest/" if rc == 0
              else f"could not start ({msg}) — is it installed?")
        return 0 if rc == 0 else 1
    print(f"unknown command {cmd} — install | status | run-now | uninstall", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
