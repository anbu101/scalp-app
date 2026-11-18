#!/usr/bin/env python3
# scripts/hooks/reliability_checklist.py — ── RELIABILITY_GATE_20261118 ── (TD-048, TD-049)
#
# Diff-triggered reliability checklist + commit gate (commit-msg hook).
# Reads the staged diff, runs only the checklist items whose trigger matches,
# and stamps the commit message with one line, e.g.
#     Checklist: observe · CL-02 fail · CL-08 needs-ack · CL-07 pass
#
# Modes — scripts/checklist/MODE as committed in HEAD (default observe), or
# SCALP_GATE=off|observe|enforce for one command:
#   off      nothing runs, no stamp
#   observe  runs and stamps, never blocks; writes .git/scalp-gate.log
#   enforce  blocks while an item fails or waits for an ack
# Passing a block in enforce mode:
#   * ack the item:   a line "Ack: CL-08 <why>" in the commit message, or
#                     echo "CL-08 <why>" >> .git/scalp-ack   (works with deploy-scalp.command)
#   * "Unverified: <why>" in the commit message lets the commit through, stamped
#   * after 2 blocks within 60 minutes the next commit goes through, stamped "forced"
# The items come from scripts/checklist/items.py AS COMMITTED IN HEAD.
#
#   python3 scripts/hooks/reliability_checklist.py --list        the items
#   python3 scripts/hooks/reliability_checklist.py --dry-run     staged diff, print only
#   python3 scripts/hooks/reliability_checklist.py --worktree    working tree vs HEAD (+ untracked)
#   python3 scripts/hooks/reliability_checklist.py --range A..B  a commit range
#   python3 scripts/hooks/reliability_checklist.py --report      what observe mode saw
# Stdlib only. The fleet suites (CL-07) run with SCALP_SUITE_PYTHON or python3 on PATH.
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

MODES = ("off", "observe", "enforce")
ITEMS_PATH = "scripts/checklist/items.py"
MODE_PATH = "scripts/checklist/MODE"
SKIP_RX = re.compile(r"\.bak-|\.staging-|(^|/)node_modules/|^desktop/src-tauri/(backend|frontend)/")
VALVE_N, VALVE_WINDOW = 2, 3600


def git(*a, check=False):
    r = subprocess.run(["git", "-c", "core.quotepath=false", *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()}")
    return r


def git_dir():
    return git("rev-parse", "--git-dir", check=True).stdout.strip()


class FileChange:
    def __init__(self, path, status, old_ref, new_ref):
        self.path, self.status, self.added = path, status, []
        self._old_ref, self._new_ref = old_ref, new_ref

    def _show(self, ref):
        if ref is None:
            return ""
        if ref == "WORKTREE":
            try:
                return open(self.path, encoding="utf-8", errors="replace").read()
            except OSError:
                return ""
        r = git("show", f"{ref}{self.path}")
        return r.stdout if r.returncode == 0 else ""

    def old_text(self):
        return self._show(self._old_ref)

    def new_text(self):
        return self._show(self._new_ref)


def collect(mode, rng=None):
    """Changed files with their added lines. mode: staged | worktree | range."""
    has_head = git("rev-parse", "--verify", "-q", "HEAD").returncode == 0
    if mode == "range":
        a, b = rng.split("..", 1)
        args, old_ref, new_ref = [a, b], f"{a}:", f"{b}:"
    elif mode == "worktree":
        args, old_ref, new_ref = (["HEAD"] if has_head else []), ("HEAD:" if has_head else None), "WORKTREE"
    else:
        args = ["--cached"] + ([] if has_head else ["--root"])
        old_ref, new_ref = ("HEAD:" if has_head else None), ":"
    if not has_head and mode != "range":
        args = ["--cached", "4b825dc642cb6eb9a060e54bf8d69288fbee4904"]  # empty tree
    out = git("diff", *args, "-U0", "--no-color", "--no-ext-diff", "-M", "--diff-filter=ACMRD").stdout
    files, cur = {}, None
    status = {}
    for ln in git("diff", *args, "--name-status", "-M", "--diff-filter=ACMRD").stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = parts[0][0]
    for ln in out.splitlines():
        if ln.startswith("+++ "):
            p = ln[4:]
            if p == "/dev/null":
                cur = None
                continue
            p = p[2:] if p.startswith("b/") else p
            cur = files.setdefault(p, FileChange(p, status.get(p, "M"), old_ref, new_ref))
            n = 0
        elif ln.startswith("@@"):
            m = re.search(r"\+(\d+)", ln)
            n = int(m.group(1)) if m else 0
        elif cur is not None and ln.startswith("+"):
            cur.added.append((n, ln[1:]))
            n += 1
    for p, s in status.items():
        if s == "D" and p not in files:
            files[p] = FileChange(p, "D", old_ref, None)
    if mode == "worktree":
        for p in git("ls-files", "--others", "--exclude-standard").stdout.splitlines():
            try:
                lines = open(p, encoding="utf-8").read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            f = files.setdefault(p, FileChange(p, "A", None, "WORKTREE"))
            f.added = list(enumerate(lines, 1))
    return [f for p, f in sorted(files.items()) if not SKIP_RX.search(p)]


def head_or_work(path):
    """(text, source) — the committed version in HEAD; the working copy only if HEAD has none."""
    r = git("show", f"HEAD:{path}")
    if r.returncode == 0:
        return r.stdout, "HEAD"
    try:
        return open(path, encoding="utf-8").read(), "worktree (not in HEAD yet)"
    except OSError:
        return None, None


def load_items():
    src, where = head_or_work(ITEMS_PATH)
    if src is None:
        return [], None
    ns = {"__name__": "scalp_checklist_items"}
    exec(compile(src, ITEMS_PATH + "@" + where, "exec"), ns)
    return ns["ITEMS"], where


def gate_mode():
    env = os.environ.get("SCALP_GATE", "").strip().lower()
    if env in MODES:
        return env
    src, _ = head_or_work(MODE_PATH)
    m = (src or "").strip().lower()
    return m if m in MODES else "observe"


class Ctx:
    def __init__(self, files):
        self.files = files
        self.suite_log = []

    def run_suites(self, suites):
        """None = not runnable here; else list of failed suites. Cached per staged tree."""
        suites = [s for s in suites if os.path.exists(s)]
        if not suites:
            return []
        tree = git("write-tree").stdout.strip() or "?"
        key = hashlib.sha1((tree + "|" + ",".join(suites)).encode()).hexdigest()
        cache_p = os.path.join(git_dir(), "scalp-gate-cache.json")
        try:
            cache = json.load(open(cache_p))
        except (OSError, ValueError):
            cache = {}
        if key in cache:
            self.suite_log.append("cached")
            return cache[key]
        py = os.environ.get("SCALP_SUITE_PYTHON") or shutil.which("python3") or sys.executable
        failed, runnable = [], False
        for s in suites:
            r = subprocess.run([py, "scripts/run_suites.py", s, "--timeout", "300"], capture_output=True, text=True)
            txt = r.stdout + r.stderr
            if "not runnable here" in txt and re.search(r"\b0 passed, 0 failed", txt):
                continue
            runnable = True
            if r.returncode != 0:
                failed.append(s)
        res = failed if runnable else None
        cache[key] = res
        try:
            json.dump(dict(list(cache.items())[-50:]), open(cache_p, "w"))
        except OSError:
            pass
        return res


def evaluate(items, files):
    ctx, results = Ctx(files), []
    for it in items:
        try:
            trig, findings, note = it["fn"](ctx)
        except Exception as e:  # a broken item never blocks a commit
            results.append(dict(id=it["id"], status="error", findings=[], note=f"{type(e).__name__}: {e}"))
            continue
        if not trig:
            continue
        if note.startswith("SKIPPED"):
            st = "skipped"
        elif findings:
            st = "fail"
        elif it["kind"] == "ack" and not note.startswith("ACKED-BY"):
            st = "needs-ack"
        else:
            st = "pass"
        results.append(dict(id=it["id"], status=st, findings=findings, note=note))
    return results


def read_acks(msg):
    acks = {}
    for m in re.finditer(r"(?im)^\s*Ack:\s*(CL-\d+)\b[ \t]*(.*)$", msg):
        acks[m.group(1).upper()] = m.group(2).strip() or "acked"
    p = os.path.join(git_dir(), "scalp-ack")
    try:
        for ln in open(p, encoding="utf-8"):
            m = re.match(r"\s*(CL-\d+)\b\s*(.*)", ln)
            if m:
                acks.setdefault(m.group(1).upper(), m.group(2).strip() or "acked")
    except OSError:
        pass
    return acks


def stamp_line(mode, results, extra=""):
    if not results:
        body = "none triggered"
    else:
        body = " · ".join(f"{r['id']} {r['status']}" for r in results)
    return f"Checklist: {mode} · {body}{extra}"


def write_stamp(msg_path, msg, line):
    lines = msg.rstrip("\n").split("\n")
    lines = [ln for ln in lines if not ln.startswith("Checklist: ")]
    while lines and lines[-1].strip() == "":
        lines.pop()
    body = "\n".join(lines)
    last_para = body.rsplit("\n\n", 1)[-1] if "\n\n" in body else ""
    trailer_block = last_para and all(re.match(r"^[\w-]+: ", ln) for ln in last_para.split("\n") if ln.strip())
    sep = "\n" if trailer_block else "\n\n"
    with open(msg_path, "w", encoding="utf-8") as f:
        f.write(body + sep + line + "\n")


def log(entry):
    try:
        with open(os.path.join(git_dir(), "scalp-gate.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def valve(record_block):
    p = os.path.join(git_dir(), "scalp-gate-state.json")
    try:
        st = json.load(open(p))
    except (OSError, ValueError):
        st = {"blocks": []}
    now = time.time()
    st["blocks"] = [t for t in st.get("blocks", []) if now - t < VALVE_WINDOW]
    open_now = len(st["blocks"]) >= VALVE_N
    if open_now:
        st["blocks"] = []
    elif record_block:
        st["blocks"].append(now)
    try:
        json.dump(st, open(p, "w"))
    except OSError:
        pass
    return open_now


def print_results(results, mode, items_src, out=sys.stderr):
    by = {}
    for r in results:
        by.setdefault(r["status"], []).append(r)
    head = f"[checklist] mode {mode} · items from {items_src} · " + (
        f"{len(results)} triggered" if results else "nothing triggered")
    print(head, file=out)
    for r in results:
        line = f"  {r['id']} {r['status']}"
        if r["note"] and not r["note"].startswith("ACKED-BY"):
            line += f" — {r['note']}"
        print(line, file=out)
        for fnd in r["findings"][:8]:
            print(f"      {fnd}", file=out)


def hook(msg_path):
    mode = gate_mode()
    if mode == "off":
        return 0
    msg = open(msg_path, encoding="utf-8", errors="replace").read()
    items, src = load_items()
    if not items:
        return 0
    titles = {i["id"]: i for i in items}
    results = evaluate(items, collect("staged"))
    acks = read_acks(msg)
    for r in results:
        if r["status"] in ("fail", "needs-ack", "skipped") and r["id"] in acks:
            r["status"], r["note"] = "acked", acks[r["id"]]
    open_items = [r for r in results if r["status"] in ("fail", "needs-ack", "skipped")]
    unverified = re.search(r"(?im)^\s*Unverified:\s*\S", msg) is not None
    print_results(results, mode, src)
    subject = msg.strip().split("\n", 1)[0][:100]
    entry = dict(t=dt.datetime.now().isoformat(timespec="seconds"), mode=mode, subject=subject,
                 results=[{k: r[k] for k in ("id", "status")} for r in results],
                 would_block=bool(open_items) and not unverified)
    extra = ""
    if mode == "enforce" and open_items and not unverified:
        if valve(record_block=False):
            extra = f" · forced after {VALVE_N} blocks"
            print(f"[checklist] let through: {VALVE_N} commits were blocked in the last hour (stamped 'forced').",
                  file=sys.stderr)
        else:
            valve(record_block=True)
            entry["blocked"] = True
            log(entry)
            print("\n[checklist] COMMIT BLOCKED (enforce). For each item above, fix it or ack it:", file=sys.stderr)
            for r in open_items:
                print(f"    echo \"{r['id']} <why it is fine>\" >> .git/scalp-ack      # {titles[r['id']]['check']}",
                      file=sys.stderr)
            print("  then commit again. Or add 'Unverified: <why>' to the message. "
                  "One-off: SCALP_GATE=observe git commit ...", file=sys.stderr)
            return 1
    if unverified and open_items:
        extra += " · unverified"
    write_stamp(msg_path, msg, stamp_line(mode, results, extra))
    log(entry)
    ack_p = os.path.join(git_dir(), "scalp-ack")
    if os.path.exists(ack_p):
        os.remove(ack_p)
    return 0


def report():
    p = os.path.join(git_dir(), "scalp-gate.log")
    try:
        rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
    except OSError:
        print("No log yet (.git/scalp-gate.log).")
        return 0
    per = {}
    for row in rows:
        for r in row["results"]:
            d = per.setdefault(r["id"], {})
            d[r["status"]] = d.get(r["status"], 0) + 1
    wb = sum(1 for r in rows if r.get("would_block"))
    print(f"{len(rows)} commits logged, {wb} would have been blocked in enforce mode, "
          f"{sum(1 for r in rows if r.get('blocked'))} were blocked.")
    for k in sorted(per):
        print(f"  {k}: " + ", ".join(f"{s} {n}" for s, n in sorted(per[k].items())))
    print("Review each 'fail' / 'needs-ack': a wrong trigger is a false positive — fix the item before enforcing.")
    return 0


def main(argv):
    if "--list" in argv:
        items, src = load_items()
        print(f"{len(items)} items (from {src}); mode {gate_mode()}")
        for i in items:
            print(f"\n{i['id']} [{i['kind']}] {i['title']}\n  trigger:   {i['trigger']}\n  check:     {i['check']}"
                  f"\n  incidents: {i['incidents']}")
        return 0
    if "--report" in argv:
        return report()
    for flag, mode in (("--dry-run", "staged"), ("--worktree", "worktree"), ("--range", "range")):
        if flag in argv:
            rng = argv[argv.index(flag) + 1] if mode == "range" else None
            items, src = load_items()
            results = evaluate(items, collect(mode, rng))
            print_results(results, gate_mode(), src, out=sys.stdout)
            print(stamp_line(gate_mode(), results))
            return 1 if any(r["status"] == "fail" for r in results) else 0
    if len(argv) == 1 and os.path.isfile(argv[0]):
        return hook(argv[0])
    print(__doc__ or "usage: see the header of this file")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
