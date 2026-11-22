#!/usr/bin/env python3
# scripts/regression/master_suite.py — ── MASTER_SUITE_20261122 ── (TD-079)
#
# The master regression suite: every test suite in the repo, in one weekly run,
# with a detailed report. Stdlib only (pytest / playwright are needed only by
# the suites that use them).
#
#   python3 scripts/regression/master_suite.py run          run every suite, write the report
#   python3 scripts/regression/master_suite.py run --tier core   skip the UI (Playwright) suites
#   python3 scripts/regression/master_suite.py run --only E/ic    a subset (paths / prefixes, as run_suites)
#   python3 scripts/regression/master_suite.py refresh      rewrite MASTER_SUITE.md from the tree
#   python3 scripts/regression/master_suite.py check        exit 1 if MASTER_SUITE.md is stale
#   python3 scripts/regression/master_suite.py list         the suites, one per line
#   options for run: --out DIR (default ~/scalp-regression)  --timeout S (per suite, 900)
#                    --notify (macOS notification at the end)  --keep N (old runs kept, 12)
#                    --no-build (UI tier: use frontend/build as it is)
#
# The suite list is never hand-kept. It is every tracked (or new, not ignored)
# test_*.py / test_*.mjs file, read from git, minus EXCLUDE. MASTER_SUITE.md
# records that list with each file's hash and test-case IDs; the pre-commit
# hook scripts/hooks/master_suite_refresh.py rewrites it whenever a test is
# added, changed or deleted, so the manifest diff shows in that commit. A run
# always re-discovers the suites, so a stale manifest can never hide a test:
# the report names any drift.
#
# Each suite runs exactly the way scripts/run_suites.py runs it (script /
# pytest / repo root / node). UI suites (frontend/uitest) run only when the
# Scalp app is NOT listening on 127.0.0.1:47321, after `npm run build`, with
# the Python that has playwright and CHROME set for macOS. Every suite gets its
# own TMPDIR under the run folder, deleted when it ends (DEF-085: suites leave
# temp folders behind).
#
# Report: <out>/<YYYY-MM-DD_HHMM>/ report.html · report.md · results.json ·
# logs/<suite>.log (full output of every suite); <out>/latest -> that folder.
# The report compares with the previous run: newly failing, fixed, still
# failing, suites added / removed / changed since then.
# Exit 0 = nothing failed, 1 = a failure, 2 = bad arguments / could not run.
import hashlib
import html
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime

FENCE = "MASTER_SUITE_20261122"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "scripts", "regression", "MASTER_SUITE.md")
APP_PORT = int(os.environ.get("MASTER_SUITE_APP_PORT") or 47321)      # tests point it at a throw-away port
DEFAULT_OUT = os.path.expanduser("~/scalp-regression")
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Files named test_* that are not suites (or not this app's). Path -> why.
EXCLUDE = {
    "backend/test_server.py": "a 6-line FastAPI ping app, no assertions",
    "backend/app/test_selector.py": "a scratch print of pick_strikes, no assertions",
}
EXCLUDE_PREFIX = {
    ".claude/": "Claude skill bundles, not the app",
    "desktop/src-tauri/": "build copies of backend/ and frontend/ (the source is run)",
}
# Suites that take arguments (repo-relative paths are made absolute).
ARGS = {
    "frontend/scripts/test_vet_wing_input.mjs": ["frontend/src/pages/Settings.jsx"],
}
TC_RX = re.compile(r"\bTC-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{4}\b")


# ── discovery ────────────────────────────────────────────────────────
def is_suite_name(rel):
    b = rel.rsplit("/", 1)[-1]
    return b.startswith("test_") and (b.endswith(".py") or b.endswith(".mjs")) and ".bak-" not in b


def excluded(rel):
    if rel in EXCLUDE:
        return EXCLUDE[rel]
    for p, why in EXCLUDE_PREFIX.items():
        if rel.startswith(p):
            return why
    return None


def git_files(root=ROOT):
    """Tracked files plus new files git does not ignore (a test about to be committed counts)."""
    r = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("git ls-files failed: " + r.stderr.decode(errors="replace").strip())
    seen, out = set(), []
    for f in r.stdout.decode("utf-8", errors="replace").split("\0"):
        if f and f not in seen and os.path.isfile(os.path.join(root, f)):
            seen.add(f)
            out.append(f)
    return out


def tier_of(rel):
    return "ui" if rel.startswith("frontend/uitest/") else "core"


def _rs(root=ROOT):
    spec = importlib.util.spec_from_file_location("_run_suites_master", os.path.join(root, "scripts", "run_suites.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def runner_label(rel, rs):
    if rel.endswith(".mjs"):
        return "node"
    if tier_of(rel) == "ui":
        return "ui"
    m = rs.mode_of(rel) if rel.startswith("backend/") else rs.local_mode_of(rel)
    return "pytest" if m["runner"] == "pytest" else ("root" if m["cwd"] == "root" else "script")


def file_info(root, rel):
    with open(os.path.join(root, rel), "rb") as f:
        b = f.read()
    tcs = sorted(set(TC_RX.findall(b.decode("utf-8", errors="replace"))))
    return hashlib.sha256(b).hexdigest()[:12], tcs


def discover(root=ROOT):
    """[{suite, tier, runner, sha, tcs}] sorted by tier then path."""
    rs = _rs(root)
    out = []
    for rel in git_files(root):
        if not is_suite_name(rel) or excluded(rel):
            continue
        sha, tcs = file_info(root, rel)
        out.append(dict(suite=rel, tier=tier_of(rel), runner=runner_label(rel, rs), sha=sha, tcs=tcs))
    out.sort(key=lambda s: (s["tier"] != "core", s["suite"]))
    return out


def tc_span(tcs):
    """['TC-A-0001','TC-A-0002','TC-A-0003','TC-B-0007'] -> 'TC-A-0001…0003, TC-B-0007'."""
    by = {}
    for t in tcs:
        area, n = t.rsplit("-", 1)
        by.setdefault(area, []).append(int(n))
    parts = []
    for area in sorted(by):
        ns = sorted(by[area])
        runs, a, b = [], ns[0], ns[0]
        for n in ns[1:]:
            if n == b + 1:
                b = n
            else:
                runs.append((a, b))
                a = b = n
        runs.append((a, b))
        parts += [f"{area}-{x:04d}" + (f"…{y:04d}" if y != x else "") for x, y in runs]
    return ", ".join(parts)


# ── manifest ─────────────────────────────────────────────────────────
def render_manifest(suites):
    core = [s for s in suites if s["tier"] == "core"]
    ui = [s for s in suites if s["tier"] == "ui"]
    n_tc = sum(len(s["tcs"]) for s in suites)
    L = [f"<!-- {FENCE} · generated by scripts/regression/master_suite.py refresh — do not hand-edit -->",
         "# Master regression suite",
         "",
         f"{len(suites)} suites ({len(core)} core + {len(ui)} UI), {n_tc} test-case IDs named in them. "
         "Every test_*.py / test_*.mjs in git is in it; the pre-commit hook rewrites this file when a test is "
         "added, changed or deleted.",
         "",
         "Run: `python3 scripts/regression/master_suite.py run` (report in ~/scalp-regression/latest/report.html). "
         "Weekly: `python3 scripts/regression/schedule.py install`.",
         "",
         "Not suites (left out): " + "; ".join(f"`{k}` ({v})" for k, v in sorted(EXCLUDE.items()))
         + "; " + "; ".join(f"`{k}*` ({v})" for k, v in sorted(EXCLUDE_PREFIX.items())) + ".",
         ""]
    for title, rows in (("Core (backend, scripts, node)", core), ("UI (Playwright; app closed)", ui)):
        L += [f"## {title} — {len(rows)}", "", "| suite | runs as | sha | test cases |", "|---|---|---|---|"]
        L += [f"| `{s['suite']}` | {s['runner']} | {s['sha']} | {tc_span(s['tcs']) or '—'} |" for s in rows]
        L.append("")
    return "\n".join(L)


def parse_manifest(text):
    out = {}
    for m in re.finditer(r"^\| `([^`]+)` \| (\w+) \| ([0-9a-f]{12}) \|", text or "", re.M):
        out[m.group(1)] = dict(runner=m.group(2), sha=m.group(3))
    return out


def read_text(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def drift(suites, manifest_text):
    """(added, removed, changed) between the tree and a manifest."""
    old = parse_manifest(manifest_text)
    now = {s["suite"]: s for s in suites}
    added = sorted(set(now) - set(old))
    removed = sorted(set(old) - set(now))
    changed = sorted(k for k in set(now) & set(old) if now[k]["sha"] != old[k]["sha"])
    return added, removed, changed


# ── running ──────────────────────────────────────────────────────────
def port_in_use(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def py_with(mod):
    """A Python that can import `mod`: this one first, then the backend venv."""
    rs = _rs()
    for py in (sys.executable, rs.python_for_backend(), shutil.which("python3")):
        if py and subprocess.run([py, "-c", f"import {mod}"], capture_output=True).returncode == 0:
            return py
    return None


PYTEST_SUM = re.compile(r"^=*\s*((?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)"
                        r"(?:, )?)+|no tests ran) in [\d.]+s", re.M)


def details(out):
    """What failed inside a suite: pytest summary + FAILED ids, failing check lines, TC IDs on them."""
    d = {}
    m = None
    for m in PYTEST_SUM.finditer(out):
        pass
    if m:
        d["pytest"] = m.group(1)
    fl = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        if (s.startswith(("FAILED ", "ERROR ", "✗", "FAIL ", "FAIL:", "AssertionError", "E   "))
                or re.match(r"^\S+\.py:\d+: \w*Error", s) or re.search(r"\bFAIL(?:ED)?\b", s)):
            fl.append(s[:300])
    if not fl:                                    # a crash: the exception line says why
        exc = [x.strip() for x in out.splitlines()
               if re.match(r"^\s*(?:[\w.]+(?:Error|Exception|Exit)|KeyboardInterrupt|Killed)\b.*", x)]
        fl = exc[-1:]
    d["fail_lines"] = fl[:40]
    d["failed_tcs"] = sorted(set(t for line in fl for t in TC_RX.findall(line)))
    return d


def safe_name(rel):
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", rel)


def _exec(cmd, cwd, env, timeout, rel, rs):
    """(status, exit, output, missing package or None) of one suite run."""
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
        status = "pass" if rc == 0 else "fail"
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              f"\n[master_suite] TIMEOUT after {timeout}s"
        rc, status = -1, "timeout"
    except OSError as e:
        out, rc, status = f"[master_suite] could not start: {e}", -1, "fail"
    gap = None
    if status == "fail":
        m = re.search(r"python[\d.]*: No module named ([\w.]+)\s*$", out.strip(), re.M)   # python -m pytest, no pytest
        if m:
            gap = m.group(1)
        elif "ModuleNotFoundError" in out or "No module named" in out:
            gap = rs.env_gap(out)
        mm = re.search(r"Cannot find (?:module|package) '([@\w][\w./@-]*)'", out) if rel.endswith(".mjs") else None
        if mm and not mm.group(1).startswith("."):
            gap = mm.group(1) + " (run npm ci in frontend/)"
    return status, rc, out, gap


def run_one(rel, rs, timeout, ui_py, logdir):
    path = os.path.join(ROOT, rel)
    cmd, cwd, env = rs.plan(path)
    cmd = cmd + [os.path.join(ROOT, a) if os.path.exists(os.path.join(ROOT, a)) else a for a in ARGS.get(rel, [])]
    # DEF-085: suites leave temp folders behind; each gets its own TMPDIR, deleted when it ends
    tmp = os.path.join(os.path.dirname(logdir), "tmp", safe_name(rel))
    os.makedirs(tmp, exist_ok=True)
    env.update(TMPDIR=tmp + os.sep, TMP=tmp, TEMP=tmp)
    if tier_of(rel) == "ui":
        cmd = [ui_py] + cmd[1:]
        env.setdefault("CHROME", CHROME_MAC if os.path.exists(CHROME_MAC) else "")
        env["CI"] = "false"
    t0 = time.time()
    status, rc, out, gap = _exec(cmd, cwd, env, timeout, rel, rs)
    note = ""
    # The backend venv may lack a package another Python here has (pytest, jwt): run it once more with that one.
    alt = os.environ.get("MASTER_SUITE_ALT_PYTHON") or sys.executable
    if gap and cmd[0].endswith(("python", "python3")) and os.path.abspath(alt) != os.path.abspath(cmd[0]):  # not realpath: a venv python links to the same binary
        cmd2 = [alt] + cmd[1:]
        s2, rc2, out2, gap2 = _exec(cmd2, cwd, env, timeout, rel, rs)
        if not gap2:
            note = f"[master_suite] {cmd[0]} lacks '{gap}'; ran with {alt}\n\n"
            cmd, status, rc, out, gap = cmd2, s2, rc2, out2, None
    secs, why = round(time.time() - t0, 1), None
    shutil.rmtree(tmp, ignore_errors=True)
    if gap:
        status, why = "env", f"needs the package '{gap}'"
        out += f"\n[master_suite] not runnable here: {why}"
    out = note + out
    log = os.path.join(logdir, safe_name(rel) + ".log")
    with open(log, "w", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n# cwd {cwd}\n# exit {rc} · {secs}s · {status}\n\n{out}")
    res = dict(suite=rel, tier=tier_of(rel), status=status, exit=rc, secs=secs,
               log=os.path.relpath(log, os.path.dirname(logdir)))
    if why:
        res["why"] = why
    if status != "pass":
        res.update(details(out))
        res["tail"] = "\n".join(out.rstrip().splitlines()[-40:])
    else:
        d = details(out)
        if d.get("pytest"):
            res["pytest"] = d["pytest"]
    return res


def build_frontend(logdir, timeout=900):
    npm = shutil.which("npm")
    if not npm:
        return False, "npm not on PATH"
    t0 = time.time()
    try:
        r = subprocess.run([npm, "run", "build"], cwd=os.path.join(ROOT, "frontend"), capture_output=True,
                           text=True, timeout=timeout, env=dict(os.environ, CI="false"), stdin=subprocess.DEVNULL)
        out, ok = (r.stdout or "") + (r.stderr or ""), r.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        out, ok = str(e), False
    with open(os.path.join(logdir, "frontend_build.log"), "w", encoding="utf-8") as f:
        f.write(out)
    return ok, f"npm run build {'ok' if ok else 'FAILED'} in {time.time() - t0:.0f}s"


def git(*a):
    try:
        r = subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def app_version():
    try:
        with open(os.path.join(ROOT, "desktop", "src-tauri", "tauri.conf.json"), encoding="utf-8") as f:
            return json.load(f).get("version") or "?"
    except (OSError, ValueError):
        return "?"


def machine():
    """Anbu / Dhivakar / Others (the QA Register's machine column), with the host name."""
    import getpass
    try:
        who = (getpass.getuser() + " " + socket.gethostname()).lower()
    except Exception:
        who = socket.gethostname().lower()
    name = "Anbu" if "anbu" in who else ("Dhivakar" if "dhiva" in who else "Others")
    return f"{name} ({socket.gethostname()})"


def previous_results(out_root, this_dir):
    runs = sorted(d for d in os.listdir(out_root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?", d)
                  and os.path.join(out_root, d) != this_dir) if os.path.isdir(out_root) else []
    first = None
    for d in reversed(runs):                      # the latest full run; a partial one only if no full one
        p = os.path.join(out_root, d, "results.json")
        try:
            with open(p, encoding="utf-8") as f:
                R = json.load(f)
        except (OSError, ValueError):
            continue
        if not R.get("subset"):
            return R
        first = first or R
    return first


def compare(cur, prev):
    if not prev:
        return None
    ps = {r["suite"]: r for r in prev.get("results", [])}
    psha = {s["suite"]: s["sha"] for s in prev.get("suites", [])}
    bad = ("fail", "timeout")
    c = dict(prev_run=prev.get("started"), prev_commit=prev.get("commit"),
             newly_failing=[], fixed=[], still_failing=[], added=[], removed=[], changed=[])
    for r in cur["results"]:
        p = ps.get(r["suite"])
        if p is None:
            continue
        if r["status"] in bad and p["status"] not in bad:
            c["newly_failing"].append(r["suite"])
        elif r["status"] in bad:
            c["still_failing"].append(r["suite"])
        elif p["status"] in bad and r["status"] == "pass":
            c["fixed"].append(r["suite"])
    now = {s["suite"]: s["sha"] for s in cur["suites"]}
    c["added"] = sorted(set(now) - set(psha))
    c["removed"] = sorted(set(psha) - set(now))
    c["changed"] = sorted(k for k in set(now) & set(psha) if now[k] != psha[k])
    return c


def prune(out_root, keep):
    runs = sorted(d for d in os.listdir(out_root) if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?", d))
    for d in runs[:-keep] if keep > 0 else []:
        shutil.rmtree(os.path.join(out_root, d), ignore_errors=True)


def notify(title, msg):
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{q(msg)}" with title "{q(title)}" sound name "Glass"'],
                   capture_output=True, timeout=30)


# ── report ───────────────────────────────────────────────────────────
STATUS_TXT = {"pass": "passed", "fail": "FAILED", "timeout": "TIMED OUT", "env": "not runnable here",
              "skip": "skipped"}


def counts(results):
    c = {k: 0 for k in STATUS_TXT}
    for r in results:
        c[r["status"]] += 1
    return c


def fmt_secs(s):
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


def render_md(R):
    c = R["counts"]
    L = [f"# Weekly regression — {R['started'][:16].replace('T', ' ')}", "",
         f"**{c['pass']} passed · {c['fail'] + c['timeout']} failed · {c['env']} not runnable · {c['skip']} skipped** "
         f"— {len(R['results'])} suites in {fmt_secs(R['secs'])}", "",
         f"- Version {R['version']} · commit `{R['commit']}` ({R['branch']}){' · **uncommitted changes**' if R['dirty'] else ''}",
         f"- Machine {R['machine']} · {R['platform']} · Python {R['python']}",
         f"- Test-case IDs covered by the suites run: {R['n_tcs']}"]
    if R.get("subset"):
        L.append(f"- **Partial run**: {len(R['results'])} of the {len(R['suites'])} suites in the master suite")
    for n in R["notes"]:
        L.append(f"- {n}")
    L.append("")
    cmp_ = R.get("compare")
    if cmp_:
        L += [f"## Since the last run ({(cmp_['prev_run'] or '?')[:16].replace('T', ' ')}, `{cmp_['prev_commit']}`)", ""]
        for k, lab in (("newly_failing", "Newly failing"), ("fixed", "Fixed"), ("still_failing", "Still failing"),
                       ("added", "Suites added"), ("removed", "Suites removed"), ("changed", "Suites changed")):
            v = cmp_[k]
            L.append(f"- {lab} ({len(v)})" + (": " + ", ".join(f"`{x}`" for x in v[:30]) + (" …" if len(v) > 30 else "")
                                              if v else ""))
        L.append("")
    bad = [r for r in R["results"] if r["status"] in ("fail", "timeout")]
    L += [f"## Failures ({len(bad)})", ""]
    if not bad:
        L += ["None.", ""]
    for r in bad:
        L += [f"### ✗ `{r['suite']}` — {STATUS_TXT[r['status']]} (exit {r['exit']}, {r['secs']}s)", ""]
        if r.get("pytest"):
            L.append(f"- pytest: {r['pytest']}")
        if r.get("failed_tcs"):
            L.append("- Failing test cases: " + ", ".join(r["failed_tcs"]))
        if r.get("fail_lines"):
            L += ["- Failing checks:"] + [f"    - `{x}`" for x in r["fail_lines"][:15]]
        L += [f"- Full log: `{r['log']}`", "", "```", r.get("tail", ""), "```", ""]
    other = [r for r in R["results"] if r["status"] in ("env", "skip")]
    if other:
        L += [f"## Not run ({len(other)})", ""]
        L += [f"- `{r['suite']}` — {STATUS_TXT[r['status']]}: {r.get('why') or '?'}" for r in other]
        L.append("")
    L += ["## Every suite", "", "| suite | tier | result | time |", "|---|---|---|---|"]
    L += [f"| `{r['suite']}` | {r['tier']} | {STATUS_TXT[r['status']]} | {r['secs']}s |" for r in R["results"]]
    slow = sorted(R["results"], key=lambda r: -r["secs"])[:10]
    L += ["", "## Slowest 10", ""] + [f"- {r['secs']}s `{r['suite']}`" for r in slow]
    return "\n".join(L) + "\n"


def render_html(R):
    e = html.escape
    c = R["counts"]
    nbad = c["fail"] + c["timeout"]
    rows = []
    for r in R["results"]:
        cls = {"pass": "ok", "fail": "bad", "timeout": "bad"}.get(r["status"], "na")
        extra = ""
        if r["status"] != "pass":
            bits = []
            if r.get("pytest"):
                bits.append(f"<div><b>pytest:</b> {e(r['pytest'])}</div>")
            if r.get("why"):
                bits.append(f"<div>{e(r['why'])}</div>")
            if r.get("failed_tcs"):
                bits.append(f"<div><b>Failing test cases:</b> {e(', '.join(r['failed_tcs']))}</div>")
            if r.get("fail_lines"):
                bits.append("<ul>" + "".join(f"<li><code>{e(x)}</code></li>" for x in r["fail_lines"][:15]) + "</ul>")
            if r.get("tail"):
                bits.append(f"<pre>{e(r['tail'])}</pre>")
            extra = f"<tr class='det {cls}'><td colspan=4>{''.join(bits)}</td></tr>"
        rows.append(f"<tr class='{cls}' data-s='{r['status']}'><td><a href='{e(r['log'])}'>{e(r['suite'])}</a></td>"
                    f"<td>{r['tier']}</td><td>{STATUS_TXT[r['status']]}</td><td>{r['secs']}s</td></tr>{extra}")
    cmp_html = ""
    cmp_ = R.get("compare")
    if cmp_:
        items = []
        for k, lab in (("newly_failing", "Newly failing"), ("fixed", "Fixed"), ("still_failing", "Still failing"),
                       ("added", "Suites added"), ("removed", "Suites removed"), ("changed", "Suites changed")):
            v = cmp_[k]
            items.append(f"<li><b>{lab} ({len(v)})</b>" + (": " + ", ".join(f"<code>{e(x)}</code>" for x in v[:30])
                                                            if v else "") + "</li>")
        cmp_html = (f"<h2>Since the last run <small>{e((cmp_['prev_run'] or '?')[:16].replace('T', ' '))} · "
                    f"{e(cmp_['prev_commit'] or '')}</small></h2><ul>{''.join(items)}</ul>")
    notes = "".join(f"<li>{e(n)}</li>" for n in R["notes"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly regression {e(R['started'][:10])}</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--ok:#1a7f37;--bad:#cf222e;--na:#9a6700;--card:#f6f8fa}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--mut:#999;--line:#333;--ok:#3fb950;--bad:#f85149;--na:#d29922;--card:#1b1b1b}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1100px;margin:auto}}
h1{{margin:0 0 4px}} h2{{margin-top:28px}} small{{color:var(--mut);font-weight:400}}
.big{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}} .big div{{background:var(--card);border-radius:10px;padding:12px 16px;min-width:110px}}
.big b{{display:block;font-size:28px}} .ok b,.ok td:nth-child(3){{color:var(--ok)}} .bad b,tr.bad td:nth-child(3){{color:var(--bad)}} .na b,tr.na td:nth-child(3){{color:var(--na)}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}}
td:first-child{{word-break:break-all}} a{{color:inherit}} pre{{background:var(--card);padding:8px;overflow:auto;max-height:340px;font-size:12px}}
tr.det td{{background:var(--card)}} code{{font-size:12px}} .f button{{margin-right:6px}}
</style></head><body>
<h1>Weekly regression</h1><div style="color:var(--mut)">{e(R['started'][:16].replace('T', ' '))} · {fmt_secs(R['secs'])} · v{e(R['version'])} · {e(R['commit'])} ({e(R['branch'])}){' · uncommitted changes' if R['dirty'] else ''} · {e(R['machine'])}</div>
<div class="big"><div class="ok"><b>{c['pass']}</b>passed</div><div class="bad"><b>{nbad}</b>failed</div>
<div class="na"><b>{c['env'] + c['skip']}</b>not run</div><div><b>{len(R['results'])}</b>suites</div><div><b>{R['n_tcs']}</b>test-case IDs</div></div>
<ul>{notes}</ul>{cmp_html}
<h2>Suites</h2><div class="f"><button onclick="f('')">All</button><button onclick="f('bad')">Failed</button><button onclick="f('na')">Not run</button></div>
<table><tr><th>suite (link = full log)</th><th>tier</th><th>result</th><th>time</th></tr>{''.join(rows)}</table>
<script>function f(c){{document.querySelectorAll('tr.ok,tr.bad,tr.na').forEach(t=>t.style.display=(!c||t.classList.contains(c))?'':'none')}}
if({nbad})f('bad')</script></body></html>"""


# ── commands ─────────────────────────────────────────────────────────
def cmd_run(opts):
    rs = _rs()
    suites = everything = discover()
    if opts["only"]:
        want = []
        for a in opts["only"]:
            for x, y in rs.PFX.items():
                a = y + a[len(x):] if a.startswith(x) else a
            a = os.path.relpath(os.path.abspath(a) if os.path.isabs(a) else os.path.join(ROOT, a), ROOT)
            a = a.replace(os.sep, "/").rstrip("/")
            if not os.path.exists(os.path.join(ROOT, a)):
                print(f"no such file or folder: {a}", file=sys.stderr)
                return 2
            want.append(a)
        suites = [s for s in suites if any(s["suite"] == w or s["suite"].startswith(w + "/") or w == "."
                                           for w in want)]
    if opts["tier"] != "all":
        suites = [s for s in suites if s["tier"] == opts["tier"]]
    out_root = opts["out"]
    os.makedirs(out_root, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir, i = os.path.join(out_root, stamp), 1
    while os.path.exists(run_dir):
        i += 1
        run_dir = os.path.join(out_root, f"{stamp}-{i}")
    logdir = os.path.join(run_dir, "logs")
    os.makedirs(logdir)
    started, t0 = datetime.now().isoformat(timespec="seconds"), time.time()
    notes = []
    added, removed, changed = drift(everything, read_text(MANIFEST))
    if added or removed or changed:
        notes.append(f"MASTER_SUITE.md is behind the tree (+{len(added)} −{len(removed)} ~{len(changed)}); "
                     "every suite in the tree was run anyway. Commit once (the hook refreshes it) or run "
                     "`master_suite.py refresh`.")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    if dirty:
        notes.append("The checkout had uncommitted changes: this run tested them, not just the commit.")

    ui = [s for s in suites if s["tier"] == "ui"]
    ui_skip = None
    if ui:
        if port_in_use(APP_PORT):
            ui_skip = f"the Scalp app is running (port {APP_PORT} is taken) — UI suites never run beside it"
        else:
            ui_py = py_with("playwright")
            if not ui_py:
                ui_skip = "no Python here can import playwright"
            elif not (os.environ.get("CHROME") or os.path.exists(CHROME_MAC) or shutil.which("google-chrome")):
                ui_skip = "Chrome not found (set CHROME)"
            elif not opts["no_build"]:
                ok, msg = build_frontend(logdir)
                notes.append(msg + " (log: logs/frontend_build.log)")
                if not ok:
                    ui_skip = "the frontend build failed — see logs/frontend_build.log"
        if ui_skip:
            notes.append(f"UI suites skipped: {ui_skip}.")
    ui_py = None if ui_skip or not ui else py_with("playwright")

    results = []
    n = len(suites)
    for k, s in enumerate(suites, 1):
        if s["tier"] == "ui" and ui_skip:
            results.append(dict(suite=s["suite"], tier="ui", status="skip", exit=None, secs=0.0, why=ui_skip, log=""))
            continue
        r = run_one(s["suite"], rs, opts["timeout"], ui_py, logdir)
        results.append(r)
        if not opts["quiet"]:
            mark = {"pass": "✓", "fail": "✗", "timeout": "⏱", "env": "?"}[r["status"]]
            print(f"[{k}/{n}] {mark} {s['suite']} ({r['secs']}s)", flush=True)
    R = dict(fence=FENCE, started=started, secs=round(time.time() - t0, 1), version=app_version(),
             commit=git("rev-parse", "--short", "HEAD"), branch=git("rev-parse", "--abbrev-ref", "HEAD"), dirty=dirty,
             machine=machine(), platform=f"{platform.system()} {platform.release()} {platform.machine()}",
             python=platform.python_version(), suites=everything, results=results, notes=notes,
             subset=len(suites) != len(everything),
             n_tcs=len({t for s in suites for t in s["tcs"]}), counts=counts(results))
    R["compare"] = compare(R, previous_results(out_root, run_dir))
    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_md(R))
    with open(os.path.join(run_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(render_html(R))
    latest = os.path.join(out_root, "latest")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(run_dir), latest)
    except OSError:
        pass
    shutil.rmtree(os.path.join(run_dir, "tmp"), ignore_errors=True)
    prune(out_root, opts["keep"])
    c = R["counts"]
    nbad = c["fail"] + c["timeout"]
    line = (f"{c['pass']} passed, {nbad} failed, {c['env']} not runnable, {c['skip']} skipped — "
            f"{n} suites in {fmt_secs(R['secs'])}")
    print(line)
    for r in results:
        if r["status"] in ("fail", "timeout"):
            print(f"  ✗ {r['suite']}" + (f"  [{', '.join(r['failed_tcs'][:6])}]" if r.get("failed_tcs") else ""))
    print(f"report: {os.path.join(run_dir, 'report.html')}")
    if opts["notify"]:
        cm = R.get("compare") or {}
        nf = len(cm.get("newly_failing", []))
        notify("Scalp regression: " + ("all passed" if not nbad else f"{nbad} failed" + (f" ({nf} new)" if nf else "")),
               line)
    return 1 if nbad else 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(open(__file__, encoding="utf-8").read().split("\nimport hashlib")[0])
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "refresh":
        txt = render_manifest(discover())
        if read_text(MANIFEST) != txt:
            with open(MANIFEST, "w", encoding="utf-8") as f:
                f.write(txt)
            print("MASTER_SUITE.md refreshed")
        else:
            print("MASTER_SUITE.md already current")
        return 0
    if cmd == "check":
        a, r, c = drift(discover(), read_text(MANIFEST))
        cur = read_text(MANIFEST) == render_manifest(discover())
        if cur:
            print("MASTER_SUITE.md is current")
            return 0
        print(f"MASTER_SUITE.md is stale: +{len(a)} −{len(r)} ~{len(c)} — run: "
              "python3 scripts/regression/master_suite.py refresh")
        for x in a:
            print("  + " + x)
        for x in r:
            print("  − " + x)
        for x in c:
            print("  ~ " + x)
        return 1
    if cmd == "list":
        for s in discover():
            print(f"{s['tier']:4} {s['runner']:6} {s['suite']}")
        return 0
    if cmd == "run":
        opts = dict(tier="all", only=[], out=DEFAULT_OUT, timeout=900, notify=False, keep=12, quiet=False,
                    no_build=False)
        it = iter(rest)
        try:
            for a in it:
                if a == "--tier":
                    opts["tier"] = next(it)
                    if opts["tier"] not in ("all", "core", "ui"):
                        raise ValueError("--tier all|core|ui")
                elif a == "--only":
                    opts["only"].append(next(it))
                elif a == "--out":
                    opts["out"] = os.path.abspath(os.path.expanduser(next(it)))
                elif a == "--timeout":
                    opts["timeout"] = int(next(it))
                elif a == "--keep":
                    opts["keep"] = int(next(it))
                elif a in ("--notify", "--quiet", "--no-build"):
                    opts[a[2:].replace("-", "_")] = True
                else:
                    raise ValueError(f"unknown option {a}")
        except (StopIteration, ValueError) as e:
            print(f"bad arguments: {e}", file=sys.stderr)
            return 2
        return cmd_run(opts)
    print(f"unknown command {cmd} — run | refresh | check | list", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
