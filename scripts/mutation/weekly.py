#!/usr/bin/env python3
# scripts/mutation/weekly.py — ── WEEKLY_MUTATION_20261128 ── (TD-133)
#
# Runs mutation testing as a weekly suite on this Mac, from a launchd agent
# (~/Library/LaunchAgents/com.scalp.weekly-mutation.plist): every module from
# scratch (mutmut_run.py --fresh), then a dated snapshot of the results, compared
# with the week before, for Claude's Saturday review and for Anbu.
#
#   python3 scripts/mutation/weekly.py install                 Saturday 10:00 (local time = IST here)
#   python3 scripts/mutation/weekly.py install --day sun --time 09:00   another day / time
#                                   (install again to change it: the old job is replaced)
#   python3 scripts/mutation/weekly.py status                  installed? when? last run?
#   python3 scripts/mutation/weekly.py run-now                 start the agent's job once, now
#   python3 scripts/mutation/weekly.py run                     the job itself, in this shell
#   python3 scripts/mutation/weekly.py snapshot                keep what ~/scalp-mutation holds now as a
#                                                              run (no mutants run): a baseline to compare with
#   python3 scripts/mutation/weekly.py uninstall
#   options: --work DIR (mutmut's folder; default ~/scalp-mutation)
#            --out DIR  (weekly snapshots; default ~/scalp-mutation-weekly)
#            --keep N   (snapshots kept, 12)   --notify (macOS banner at the end)   --print (show the plist)
#
# Each run leaves <out>/<YYYY-MM-DD_HHMM>/ with report.md (scores, change since
# the last run, new survivors), result.json, SUMMARY.md, state.json,
# survivors/<module>.txt and the tail of each module's mutmut.log; <out>/latest
# points at it. Exit 0 when every module finished, 1 when one did not.
# The job runs under `caffeinate -i` (the Mac does not idle-sleep mid-run). A Mac
# that is asleep at the time runs the job when it wakes; one that is shut down
# skips that week. A full run takes about 2.5 hours (5 modules, all CPUs but one).
# Stdlib only.
import json
import os
from collections import Counter
import plistlib
import re
import shutil
import subprocess
import sys
import time

FENCE = "WEEKLY_MUTATION_20261128"
LABEL = "com.scalp.weekly-mutation"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(ROOT, "scripts", "mutation", "mutmut_run.py")
THIS = os.path.abspath(__file__)
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
RUN_DIR = re.compile(r"\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?")
COLS = ("module", "area", "mutants", "killed", "survived", "timeout", "no_tests", "suspicious",
        "not_checked", "score", "minutes", "state")


def defaults():
    return {"--day": "sat", "--time": "10:00", "--work": os.path.expanduser("~/scalp-mutation"),
            "--out": os.path.expanduser("~/scalp-mutation-weekly"), "--keep": "12"}


# ── reading mutmut_run's results ─────────────────────────────────────
def parse_summary(text):
    """Rows of SUMMARY.md's table; a sub-area row carries its module down."""
    rows, mod = [], ""
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(COLS) or cells[0] == "module" or set(cells[0]) <= {"-"} and cells[0]:
            continue
        r = dict(zip(COLS, cells))
        mod = r["module"] or mod
        r["module"] = mod
        for k in ("mutants", "killed", "survived", "timeout", "no_tests", "suspicious", "not_checked", "minutes"):
            r[k] = int(r[k]) if r[k].isdigit() else None
        try:
            r["score"] = float(r["score"])
        except ValueError:
            r["score"] = None
        rows.append(r)
    return rows


def survivor_keys(text):
    """One key per surviving mutant: its file, its function and the line it changes (old → new).
    Unlike the mutant's name (…__mutmut_8) or its line number, the key stays the same when
    other mutants are renumbered or code above it moves. Two mutants may share a key, so
    compare them as counts (compare())."""
    keys, cur, minus, plus = [], None, None, None
    for line in text.splitlines() + ["## end"]:
        if line.startswith("## "):
            if cur:
                keys.append(f"{cur} | -{minus or ''} | +{plus or ''}")
            m = re.match(r"## (\S+)\s.*?(\S+\.py):\d+\s*$", line)
            if m:
                fn = re.sub(r"__mutmut_\d+$", "", m.group(1).rsplit(".", 1)[-1])
                cur = f"{m.group(2)} {fn}"
            else:
                cur = None
            minus = plus = None
        elif cur and line.startswith("-") and not line.startswith("---") and minus is None:
            minus = line[1:].strip()
        elif cur and line.startswith("+") and not line.startswith("+++") and plus is None:
            plus = line[1:].strip()
    return keys


def survivors_by_key(text):
    """key -> the survivor's whole block (header + diff), for the report."""
    out, block, key = {}, [], None
    for line in text.splitlines() + ["## end"]:
        if line.startswith("## "):
            if block:
                k = survivor_keys("\n".join(block))
                if k:
                    out[k[0]] = "\n".join(block).strip()
            block = [line]
        elif block:
            block.append(line)
    return out


def git_head():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


# ── a snapshot of one run ────────────────────────────────────────────
def new_run_dir(out, now=None):
    """A new dated folder that sorts after every run already there (a run in the same
    minute gets -2, -3 …, never a name that a pruned run had freed)."""
    b = time.strftime("%Y-%m-%d_%H%M", time.localtime(now))
    same = [d for d in os.listdir(out) if d == b or (d.startswith(b + "-") and d[len(b) + 1:].isdigit())]
    n = max((int(d[len(b) + 1:]) if d != b else 1) for d in same) + 1 if same else 1
    d = os.path.join(out, b if n == 1 else f"{b}-{n}")
    os.makedirs(d)
    return d


def snapshot(work, run_dir, rc, started, secs, kind="run"):
    """Copy the results out of the work folder (a later --fresh run deletes them) and write result.json."""
    summary = os.path.join(work, "SUMMARY.md")
    text = open(summary, encoding="utf-8").read() if os.path.exists(summary) else ""
    if text:
        shutil.copy2(summary, os.path.join(run_dir, "SUMMARY.md"))
    state = {}
    sp = os.path.join(work, "state.json")
    if os.path.exists(sp):
        shutil.copy2(sp, os.path.join(run_dir, "state.json"))
        try:
            state = json.load(open(sp, encoding="utf-8"))
        except ValueError:
            state = {}
    rows = parse_summary(text)
    mods = sorted({r["module"] for r in rows} | set(state))
    os.makedirs(os.path.join(run_dir, "survivors"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "logs"), exist_ok=True)
    survivors = {}
    for m in mods:
        s = os.path.join(work, m, "survivors.txt")
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(run_dir, "survivors", m + ".txt"))
            survivors[m] = survivor_keys(open(s, encoding="utf-8", errors="replace").read())
        lg = os.path.join(work, m, "mutmut.log")
        if os.path.exists(lg):
            with open(lg, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-200:]
            with open(os.path.join(run_dir, "logs", m + ".log.tail"), "w", encoding="utf-8") as f:
                f.writelines(tail)
    m = re.search(r"repo ([0-9a-f]{7,40})", text)
    result = {
        "fence": FENCE, "kind": kind, "started": started, "secs": round(secs), "runner_exit": rc,
        "commit": git_head(), "summary_commit": m.group(1) if m else "",
        "rows": rows, "state": state,
        "modules": {mm: {"status": (state.get(mm) or {}).get("status", "missing"),
                         "survivors": len(survivors.get(mm, []))} for mm in mods},
        "survivor_keys": survivors,
    }
    result["complete"] = bool(mods) and rc == 0 and all(v["status"] == "done" for v in result["modules"].values())
    with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    return result


def previous(out, current_dir):
    """The newest earlier run folder that has a result.json, or None."""
    me = os.path.basename(current_dir)
    runs = sorted(d for d in os.listdir(out) if RUN_DIR.fullmatch(d) and d < me
                  and os.path.exists(os.path.join(out, d, "result.json")))
    if not runs:
        return None, None
    d = os.path.join(out, runs[-1])
    return d, json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))


def compare(cur, prev):
    """Per module ('all' rows): score and survivor change, new / gone survivors; a module
    that is no longer done is flagged."""
    if not prev:
        return None
    pa = {r["module"]: r for r in prev.get("rows", []) if r["area"] == "all"}
    out = {}
    for r in cur.get("rows", []):
        if r["area"] != "all":
            continue
        m, p = r["module"], pa.get(r["module"])
        now_k = Counter(cur.get("survivor_keys", {}).get(m, []))
        was_k = Counter(prev.get("survivor_keys", {}).get(m, []))
        out[m] = {
            "score": r["score"], "was_score": p["score"] if p else None,
            "delta": round(r["score"] - p["score"], 1) if p and r["score"] is not None
            and p["score"] is not None else None,
            "survived": r["survived"], "was_survived": p["survived"] if p else None,
            "new": sorted((now_k - was_k).elements()), "gone": sum((was_k - now_k).values()),
            "new_module": p is None,
        }
    for m in pa:
        if m not in out:
            out[m] = {"missing": True, "was_score": pa[m]["score"]}
    return out


def fmt(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


def write_report(run_dir, cur, cmp, prev_dir):
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(cur["started"]))
    alls = [r for r in cur["rows"] if r["area"] == "all"]
    not_done = [m for m, v in cur["modules"].items() if v["status"] != "done"]
    head = ("INCOMPLETE — " + ", ".join(f"{m} {cur['modules'][m]['status']}" for m in not_done)
            if not_done or not cur["complete"] else "complete")
    if cur.get("runner_exit"):
        head += f" (runner exit {cur['runner_exit']})"
    L = [f"# Weekly mutation run — {when}", "",
         f"**{head}** — {len(alls)} modules in {cur['secs'] // 3600}h {cur['secs'] % 3600 // 60:02d}m"
         + (" (snapshot of earlier results, no mutants run)" if cur["kind"] == "snapshot" else ""), "",
         f"- Commit `{cur['commit'][:8]}`" + (f" · results taken at `{cur['summary_commit'][:10]}`"
                                               if cur["summary_commit"] else ""), ""]
    L += ["| module | score % | change | survived | new survivors | gone | no tests | timeout | suspicious | state |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in alls:
        c = (cmp or {}).get(r["module"], {})
        L.append(f"| {r['module']} | {fmt(r['score'])} | "
                 + ("new" if c.get("new_module") else fmt(c.get("delta"), "") if cmp else "—")
                 + f" | {fmt(r['survived'])} | {len(c.get('new', [])) if cmp else '—'} | "
                 f"{c.get('gone', '—') if cmp else '—'} | {fmt(r['no_tests'])} | {fmt(r['timeout'])} | "
                 f"{fmt(r['suspicious'])} | {r['state']} |")
    L.append("")
    if not cmp:
        L.append("First run here: nothing to compare with yet.")
    else:
        L.append(f"Compared with `{os.path.basename(prev_dir)}`.")
        drops = [(m, c) for m, c in cmp.items() if c.get("delta") is not None and c["delta"] < 0]
        gone_mods = [m for m, c in cmp.items() if c.get("missing")]
        if drops:
            L.append("Score dropped: " + ", ".join(f"{m} {c['was_score']} → {c['score']}" for m, c in drops))
        if gone_mods:
            L.append("Not in this run: " + ", ".join(gone_mods))
        for m, c in cmp.items():
            if c.get("new") and not c.get("new_module"):
                blocks = survivors_by_key(open(os.path.join(run_dir, "survivors", m + ".txt"),
                                               encoding="utf-8", errors="replace").read())
                L += ["", f"## New survivors in {m} ({len(c['new'])})", ""]
                for k in sorted(set(c["new"]))[:40]:
                    L += ["```diff", blocks.get(k, k)[:1500], "```"]
                if len(set(c["new"])) > 40:
                    L.append(f"… and {len(set(c['new'])) - 40} more (survivors/{m}.txt)")
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return head


def set_latest(out, run_dir):
    link = os.path.join(out, "latest")
    tmp = link + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(os.path.basename(run_dir), tmp)
    os.replace(tmp, link)


def prune(out, keep):
    runs = sorted(d for d in os.listdir(out) if RUN_DIR.fullmatch(d))
    for d in runs[:-keep] if keep > 0 else []:
        shutil.rmtree(os.path.join(out, d), ignore_errors=True)


def notify(title, msg):
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')   # noqa: E731
    subprocess.run(["osascript", "-e", f'display notification "{q(msg)}" with title "{q(title)}" sound name "Glass"'],
                   capture_output=True, timeout=30)


def do_run(work, out, keep, notify_end=False, runner=RUNNER, python=sys.executable, kind="run"):
    os.makedirs(out, exist_ok=True)
    started = time.time()
    rc = 0
    if kind == "run":
        rc = subprocess.run([python, runner, "--fresh", "--work", work], cwd=ROOT).returncode
    run_dir = new_run_dir(out, started)
    cur = snapshot(work, run_dir, rc, started, time.time() - started, kind)
    prev_dir, prev = previous(out, run_dir)
    cmp = compare(cur, prev)
    with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(dict(cur, compare=cmp, compared_with=os.path.basename(prev_dir) if prev_dir else None), f, indent=1)
    head = write_report(run_dir, cur, cmp, prev_dir)
    set_latest(out, run_dir)
    prune(out, keep)
    new = sum(len(c.get("new", [])) for c in (cmp or {}).values())
    print(f"{head} — report: {os.path.join(run_dir, 'report.md')}")
    if notify_end:
        notify("Scalp mutation: " + head.split(" (")[0], f"{new} new survivors" if cmp else "first run")
    return 0 if cur["complete"] or kind == "snapshot" else 1


# ── launchd ──────────────────────────────────────────────────────────
def plist_path(agents=AGENTS):
    return os.path.join(agents, LABEL + ".plist")


def parse_when(day, hhmm):
    d = DAYS.get((day or "").lower()[:3])
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", hhmm or "")
    if d is None or not m:
        raise ValueError("--day sun..sat and --time HH:MM (24h)")
    return d, int(m.group(1)), int(m.group(2))


def build_plist(weekday, hour, minute, work, out, python=sys.executable, path_env=None):
    env = {"PATH": path_env or os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
           "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1"}
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", python, THIS, "run", "--notify",
                             "--work", work, "--out", out],
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


def describe(pl):
    c = pl.get("StartCalendarInterval", {})
    day = {v: k for k, v in DAYS.items()}.get(c.get("Weekday"), "?")
    return f"every {day.capitalize()} at {c.get('Hour', 0):02d}:{c.get('Minute', 0):02d} (local time)"


def launchctl(*a):
    r = subprocess.run(["launchctl", *a], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(open(__file__, encoding="utf-8").read().split("\nimport json")[0])
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    opt = defaults()
    flags = {x for x in rest if x in ("--print", "--notify")}
    it = iter(x for x in rest if x not in flags)
    try:
        for a in it:
            if a not in opt:
                raise ValueError(f"unknown option {a}")
            opt[a] = next(it)
        wd, hh, mm = parse_when(opt["--day"], opt["--time"])
        keep = int(opt["--keep"])
    except (StopIteration, ValueError) as e:
        print(f"bad arguments: {e}", file=sys.stderr)
        return 2
    work = os.path.abspath(os.path.expanduser(opt["--work"]))
    out = os.path.abspath(os.path.expanduser(opt["--out"]))
    p = plist_path()
    uid = str(os.getuid())

    if cmd == "run":
        return do_run(work, out, keep, "--notify" in flags)
    if cmd == "snapshot":
        return do_run(work, out, keep, False, kind="snapshot")
    if cmd == "install":
        pl = build_plist(wd, hh, mm, work, out)
        data = plistlib.dumps(pl)
        if "--print" in flags:
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
        print(f"installed {LABEL}: {describe(pl)}\n  plist   {p}\n  reports {out}/latest/report.md\n"
              f"  python  {pl['ProgramArguments'][2]}\n"
              "  A sleeping Mac runs it on wake; a shut-down one skips the week.")
        if not any(RUN_DIR.fullmatch(d) for d in os.listdir(out)) and os.path.exists(os.path.join(work, "SUMMARY.md")):
            print("  baseline: the results already in the work folder are kept as the first run to compare with")
            do_run(work, out, keep, False, kind="snapshot")
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
            print("not installed — python3 scripts/mutation/weekly.py install")
            return 1
        with open(p, "rb") as f:
            pl = plistlib.load(f)
        rc, info = launchctl("print", f"gui/{uid}/{LABEL}")
        last = re.search(r"last exit code = (.+)", info or "")
        latest = os.path.join(out, "latest")
        print(f"{LABEL}: {describe(pl)} — {'loaded' if rc == 0 else 'NOT loaded'}"
              + (f", last exit {last.group(1)}" if last else "")
              + (f"\n  latest run: {os.path.realpath(latest)}" if os.path.exists(latest) else "\n  no run yet"))
        return 0
    if cmd == "run-now":
        rc, msg = launchctl("kickstart", f"gui/{uid}/{LABEL}")
        print("started — results in " + out + "/latest" if rc == 0 else f"kickstart failed: {msg}")
        return 0 if rc == 0 else 1
    print(f"unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
