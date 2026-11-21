# test_mut_suites.py for a mutation workspace — ── MUTMUT_20261117 ── (TD-043)
# Copied into <work>/<module>/tests/ by scripts/mutation/mutmut_run.py.
#
# The repo's suites are standalone scripts (exit code), and a few are pytest
# files. mutmut 3 drives pytest in-process, so each suite becomes one pytest
# test here. Each suite runs in a FORK of this process: suites monkeypatch
# time.sleep, module globals, sys.modules and env, and a fork throws all of
# that away (one process per suite, as scripts/run_suites.py does), while the
# fork still inherits mutmut's active mutant. mutmut's trampoline hits made in
# the fork are sent back over a pipe, so mutmut still learns which suite
# reaches which function and reruns only those suites per mutant.
#   script suite  runpy as __main__, cwd = backend root, its folder on sys.path
#   pytest suite  nested pytest.main on that one file, cwd = its folder
# A bare `import kill_switch` (a suite next to its module) is loaded from the
# mutated file under its package name, or mutmut would never see the hit.
# The fork exits as soon as this process is gone (a mutmut timeout kills it),
# and is killed after max(60 s, 10 × its clean run time) — a mutant that makes
# an exit loop spin (sleeps, never returns) fails its suite in a minute or two,
# not after mutmut's own whole-test-set timeout.
import importlib.abc
import importlib.util
import json
import os
import runpy
import select
import signal
import sys
import threading
import time
import traceback

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # the workspace root (= mutants/ while mutmut runs)
with open(os.path.join(HERE, "suites.json"), encoding="utf-8") as _f:
    CFG = json.load(_f)


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, aliases, root):
        self.aliases, self.root = aliases, root

    def find_spec(self, name, path=None, target=None):
        dotted = self.aliases.get(name)
        if not dotted:
            return None
        file = os.path.join(self.root, *dotted.split(".")) + ".py"
        return importlib.util.spec_from_file_location(name, file, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        dotted = self.aliases[module.__spec__.name]
        module.__name__ = dotted                   # functions get __module__ = package name
        sys.modules.setdefault(dotted, module)
        with open(module.__file__, encoding="utf-8") as f:
            code = compile(f.read(), module.__file__, "exec")
        exec(code, module.__dict__)


def _exit_code(e):
    if e.code is None:
        return 0
    return e.code if isinstance(e.code, int) else 1


def _mutmut_state():
    try:
        from mutmut.state import state
        return state()
    except Exception:                               # not under mutmut (a plain pytest run)
        return None


def _orphan_watch(parent):
    while True:
        if os.getppid() != parent:
            os._exit(3)
        time.sleep(0.5)


def _child(rel, kind, root):
    sys.meta_path.insert(0, _Alias(CFG.get("aliases", {}), root))
    os.environ["SCALP_TEST_MODE"] = "1"
    path = os.path.join(root, rel)
    folder = os.path.dirname(path)
    sys.path[:0] = [folder, root]
    if kind == "pytest":
        os.chdir(folder)
        return int(pytest.main([path, "-q", "-x", "-p", "no:cacheprovider", "-p", "no:randomly",
                                "--rootdir", folder, "--import-mode=prepend"]))
    os.chdir(root)
    sys.argv = [path]
    try:
        runpy.run_path(path, run_name="__main__")
        return 0
    except SystemExit as e:
        return _exit_code(e)


def run_suite(rel, kind, root=ROOT, limit_s=None):
    st = _mutmut_state()
    seen = set(st._stats) if st is not None else set()
    r, w = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                                    # ── the suite's own process
        os.close(r)
        threading.Thread(target=_orphan_watch, args=(os.getppid(),), daemon=True).start()
        rc = 1
        try:
            rc = _child(rel, kind, root)
        except BaseException:
            traceback.print_exc()
            rc = 1
        out = {"rc": rc}
        if st is not None:
            out["hits"] = sorted(set(st._stats) - seen)
            out["deps"] = {k: sorted(v) for k, v in getattr(st, "function_dependencies", {}).items()}
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        data = json.dumps(out).encode()
        while data:
            data = data[os.write(w, data):]
        os._exit(0)
    os.close(w)
    chunks, end = [], (time.time() + limit_s) if limit_s else None
    while True:
        left = None if end is None else end - time.time()
        if left is not None and left <= 0:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(r)
            print(f"[MUTMUT_20261117] {rel}: no result after {limit_s:.0f}s — killed (counts as caught)")
            return 124
        ready, _, _ = select.select([r], [], [], left)
        if not ready:
            continue
        b = os.read(r, 1 << 16)
        if not b:
            break
        chunks.append(b)
    os.close(r)
    _, status = os.waitpid(pid, 0)
    try:
        out = json.loads(b"".join(chunks).decode() or "{}")
    except ValueError:
        out = {}
    if st is not None:
        st._stats.update(out.get("hits", []))
        deps = getattr(st, "function_dependencies", None)
        if deps is not None:
            for k, v in out.get("deps", {}).items():
                deps.setdefault(k, set()).update(v)
    if "rc" not in out:                              # the fork died (signal / os._exit)
        return 128 + (os.WTERMSIG(status) if os.WIFSIGNALED(status) else 1)
    return out["rc"]


@pytest.mark.parametrize("rel,kind,secs", [(s["path"], s["kind"], s.get("secs", 30.0)) for s in CFG["suites"]],
                         ids=[s["path"].replace("/", "__") for s in CFG["suites"]])
def test_suite(rel, kind, secs):
    rc = run_suite(rel, kind, limit_s=max(60.0, 10.0 * secs))
    assert rc == 0, f"{rel} exited {rc}"
