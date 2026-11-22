#!/usr/bin/env python3
# scripts/gen_repo_map.py  — ── REPO_MAP_20261117 ── (TD-038)
#                            ── REPO_MAP_FIX_20261117 ── test modes read from scripts/run_suites.py
#
# Writes REPO_MAP.md at the repo root: which file to open first for each
# strategy, the dual tree, the test suites, the shared modules and fences.
# Stdlib only (ast for Python, a line scan for JSX), deterministic, read-only
# except for REPO_MAP.md.
#
#   python3 scripts/gen_repo_map.py            regenerate REPO_MAP.md
#   python3 scripts/gen_repo_map.py --check    exit 1 if REPO_MAP.md is stale
#   python3 scripts/gen_repo_map.py --stdout   print, write nothing
#   (--repo PATH to run against another checkout)
#
# The only hand-kept facts are STRATS (which engine folder / route / EOD job
# belongs to which strategy) and SHARED (one line per shared module). Every
# path in them is verified; a missing one fails the run. Line numbers, the
# backtest runner, the defaults block, the Settings / Backtest form ranges,
# the panel and the codename are all read from the code.
import ast
import importlib.util
import os
import re
import subprocess
import sys

FENCE = "REPO_MAP_20261117"
BA = "backend/app/"

# id -> eng: engine folders/files under backend/app; key: prefix of its
# A/<key>*_state_routes.py and J/<key>_live_eod.py; tests: name filter used
# only for a backtest folder that several strategies share.
STRATS = [
    # core four (fleet-wide live changes cover these)
    ("IC_V2",  dict(eng=["engine/ic"], key="ic", core=True, also="IC_V1")),
    ("TMA_V2", dict(eng=["engine/tma2"], key="tma2", tests=r"tma2|tma_v2", core=True)),
    ("TSG_V1", dict(eng=["engine/tsg"], key="tsg", core=True)),
    ("VET_V1", dict(eng=["engine/vet"], key="vet", core=True)),
    # other live / paper strategies
    ("EMC_V1", dict(eng=["engine/emc"], key="emc")),
    ("LVB_V1", dict(eng=["engine/lvb"], key="lvb", ui=["pages/LevelBreak.jsx"])),
    ("ORB_V1", dict(eng=["engine/orb"], key="orb")),
    ("BRK_V1", dict(eng=["engine/brk"], key="brk")),
    ("HA_V1",  dict(eng=["engine/ha_options"], key="ha")),
    ("BB_V1",  dict(eng=["engine/bb_options"], key="bb", job="bb_live_eod.py")),
    ("BB_V2",  dict(eng=["engine/bb_v2"], key="bb", job="bb_live_eod_v2.py")),
    ("GC_V1",  dict(eng=["engine/gc"], key="gc")),
    ("TMA_V1", dict(eng=["engine/tma"], key="tma", tests=r"tma_v1|tma_runner|test_tma_(?!v2)")),
    ("SCALP_V1", dict(eng=["marketdata/zerodha_tick_engine.py", "engine/scalp_common"], key="scalp",
                        route="scalp_v1_state.py")),
    ("SCALP_V3", dict(eng=["engine/scalp_v3"], key="scalp_v3")),
    ("SCALP_V5", dict(eng=["engine/scalpv5"], key="scalpv5")),
    ("TVX_V1", dict(eng=["engine/tvx"], key="tvx", ui=["pages/TvAlerts.jsx"], note="TradingView alerts, paper")),
]

# (path under backend/app, symbol to locate or None, one line)
SHARED = [
    ("execution/execution_router.py", "ExecutionRouter",
     "strategies get this, not the executor; every method they call needs a forward"),
    ("execution/zerodha_executor.py", None, "Kite orders/GTTs (+angel_, base_, executor_factory, strategy_executor)"),
    ("execution/exit_order_driver.py", "drive_exit_order", "live exit re-peg/cancel/confirm until flat, never a model price"),
    ("execution/broker_truth.py", "LEDGER",
     "strict reads (BrokerUnreadable, never [] = flat); LEDGER = who holds what on a shared contract"),
    ("execution/kill_switch.py", None, "per-strategy kill: close, cancel GTTs, verify flat"),
    ("api/telegram_api.py", "notify_critical", "critical alerts: bell always, Telegram never for PAPER"),
    ("api_server.py", "BackgroundScheduler",
     "routes, launches (_boot_guard), APScheduler (misfire 3600, coalesce); jobs/scheduler_observability.py"),
    ("risk/execution_modes.py", None, "OFF/PAPER/LIVE/PAPER_LIVE; paper twin trading/shadow_book.py"),
    ("parity/parity_core.py", None, "parity ledger (recorded day via real runner vs app); recon/ = broker reconciliation"),
    ("marketdata/zerodha_tick_engine.py", "ZerodhaTickEngine", "SCALP_V1 engine + shared Kite tick spine; open no new socket"),
    ("backtest/queue_worker.py", "_dispatch_run_impl", "backtest dispatch (hand-kept twin: api/backtest_routes.py)"),
    ("backtest/engine/lot_compounding.py", None, "lot compounding/DD guard/tax for all runners; dte_lots.py"),
]

PFX = [("backend/app/engine/", "E/"), ("backend/app/backtest/", "B/"), ("backend/app/api/", "A/"),
       ("backend/app/jobs/", "J/"), ("frontend/src/", "F/")]


class Missing(Exception):
    pass


def short(p):
    for a, b in PFX:
        if p.startswith(a):
            return b + p[len(a):]
    return p


def list_files(root):
    """Tracked + untracked-not-ignored files (git), else a walk; no backups."""
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, timeout=60)
        files = out.stdout.splitlines() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        files = None
    if files is None:
        files = []
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "venv", ".venv", "target")]
            files += [os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for f in fs]
    keep = [f for f in files if ".bak-" not in f and ".tmp-" not in f and "/__pycache__/" not in f
            and os.path.isfile(os.path.join(root, f))]
    return sorted(set(keep))


def read(root, rel):
    p = os.path.join(root, rel)
    if not os.path.isfile(p):
        raise Missing(rel)
    with open(p, encoding="utf-8") as f:
        return f.read()


# ── Python (ast) ─────────────────────────────────────────────────────
def symbol_line(src, name):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
            return n.lineno
        if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == name for x in n.targets):
            return n.lineno
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name):
                return n.lineno
    return None


def dispatch_map(src):
    """queue_worker._dispatch_run_impl: id -> (runner module path, if-line)."""
    t = ast.parse(src)
    fn = next((n for n in ast.walk(t) if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_run_impl"), None)
    if fn is None:
        raise Missing("backtest/queue_worker.py:_dispatch_run_impl")
    out, default = {}, None
    for st in fn.body:
        if isinstance(st, ast.If) and isinstance(st.test, ast.Compare):
            l, c = st.test.left, st.test.comparators[0]
            if not (isinstance(l, ast.Name) and l.id == "strategy_id"):
                continue
            ids = [c.value] if isinstance(c, ast.Constant) else \
                [e.value for e in getattr(c, "elts", []) if isinstance(e, ast.Constant)]
            mod = next((n.module for n in ast.walk(st) if isinstance(n, ast.ImportFrom)
                        and (n.module or "").startswith("app.backtest.")), None)
            for i in ids:
                if mod:
                    out[i] = (mod[4:].replace(".", "/") + ".py", st.lineno)
        elif isinstance(st, ast.ImportFrom) and (st.module or "").startswith("app.backtest."):
            default = (st.module[4:].replace(".", "/") + ".py", st.lineno)
    return out, default


def defaults_map(src):
    """strategy_loader: id -> (first, last) line of its defaults dict."""
    best = {}
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Dict) and len(n.keys) > 3:
            ks = [k for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if len(ks) == len(n.keys) and all(re.fullmatch(r"[A-Z0-9]+_V\d", k.value) for k in ks):
                if len(ks) > len(best):
                    best = {k.value: (k.lineno, v.end_lineno) for k, v in zip(n.keys, n.values)}
    return best


# ── JSX (line scan) ──────────────────────────────────────────────────
def codenames(src):
    return {m.group(1): m.group(2) for m in
            re.finditer(r'^\s*([A-Z0-9_]+):\s*\{\s*real:\s*"[^"]*",\s*code:\s*"([^"]*)"', src, re.M)}


def panels(src):
    imp = {m.group(1): m.group(2) for m in re.finditer(r'^import\s+(\w+)\s+from\s+"([^"]+)"', src, re.M)}
    out = {}
    for m in re.finditer(r'case\s+"([A-Z0-9_]+)":\s*return\s*<(\w+)', src):
        p = imp.get(m.group(2))
        if p and p.startswith("../"):
            p = os.path.normpath("frontend/src/components/" + p).replace(os.sep, "/")
            out.setdefault(m.group(1), p if p.endswith((".jsx", ".js")) else p + ".jsx")
    return out


def settings_cases(src):
    """Settings.jsx renderDetailBody: id -> (first, last) line of its case."""
    lines = src.splitlines()
    start = next((i for i, l in enumerate(lines) if "function renderDetailBody" in l), None)
    if start is None:
        raise Missing("frontend/src/pages/Settings.jsx:renderDetailBody")
    cases = []
    for i in range(start + 1, len(lines)):
        m = re.match(r'\s*case\s+"([A-Z0-9_]+)":', lines[i])
        if m:
            cases.append((m.group(1), i + 1))
        elif re.match(r"\s{0,4}\}\s*$", lines[i]) and cases:
            break
    out = {}
    for j, (cid, ln) in enumerate(cases):
        k = j + 1
        while k < len(cases) and cases[k][1] == cases[k - 1][1] + 1:   # stacked `case A: case B:`
            k += 1
        end = cases[k][1] - 1 if k < len(cases) else ln
        out[cid] = (ln, end)
    return out


def backtest_forms(src):
    """Backtest.jsx: id -> (first, last) line of the largest `{isX && (` block."""
    lines = src.splitlines()
    flags = {}
    for m in re.finditer(r'const\s+(is\w+)\s*=\s*((?:strategyId\s*===\s*"[A-Z0-9_]+"\s*(?:\|\|\s*)?)+);', src):
        flags[m.group(1)] = re.findall(r'"([A-Z0-9_]+)"', m.group(2))
    out = {}
    for i, l in enumerate(lines):
        m = re.match(r'(\s*)\{(?:(is\w+)|strategyId === "([A-Z0-9_]+)") && \(\s*$', l)
        if not m or (m.group(2) and m.group(2) not in flags):
            continue
        ind = m.group(1)
        ids = flags[m.group(2)] if m.group(2) else [m.group(3)]
        end = next((j for j in range(i + 1, len(lines))
                    if lines[j].startswith(ind + ")}") or lines[j].startswith(ind + ")")
                    and len(lines[j]) - len(lines[j].lstrip()) == len(ind)), None)
        if end is None:
            continue
        for sid in ids:
            if sid not in out or (end - i) > (out[sid][1] - out[sid][0]):
                out[sid] = (i + 1, end + 1)
    return out


# ── assembly ─────────────────────────────────────────────────────────
def rng(t):
    return f"{t[0]}–{t[1]}" if t and t[0] != t[1] else (str(t[0]) if t else "—")


def tests_in(files, folder, rx=None):
    pre = folder.rstrip("/") + "/"
    hits = [f for f in files if f.startswith(pre) and "/" not in f[len(pre):]
            and re.match(r"test_.*\.py$", os.path.basename(f))
            and (rx is None or re.search(rx, os.path.basename(f)))]
    return len(hits)


def build(root):
    files = list_files(root)
    fset = set(files)

    def need(rel):
        if rel not in fset and not any(f.startswith(rel.rstrip("/") + "/") for f in files):
            raise Missing(rel)
        return rel

    names = codenames(read(root, "frontend/src/strategies/displayNames.js"))
    disp, disp_default = dispatch_map(read(root, BA + "backtest/queue_worker.py"))
    dflt = defaults_map(read(root, BA + "config/strategy_loader.py"))
    pnl = panels(read(root, "frontend/src/components/StrategyHost.jsx"))
    sett = settings_cases(read(root, "frontend/src/pages/Settings.jsx"))
    form = backtest_forms(read(root, "frontend/src/pages/Backtest.jsx"))

    o = []
    w = o.append
    w(f"<!-- {FENCE} · generated by scripts/gen_repo_map.py — do not hand-edit -->")
    w("# REPO_MAP — scalp-app")
    w("Open the file named here first. Line numbers are hints: `grep -n` to confirm, then `sed -n a,b`.")
    w("`E/` `B/` `A/` `J/` = backend/app/ engine, backtest, api, jobs · `F/` frontend/src · `SL` "
      "backend/app/config/strategy_loader.py (defaults) · `ST` F/pages/Settings.jsx · `BT` F/pages/Backtest.jsx · "
      "`QW` B/queue_worker.py")
    w("")
    w("## Strategies — live · backtest · SL/ST/BT lines · panel (F/strategies/<id>/) · test suites")
    w("Each live one also has `A/<key>_state_routes.py` and `J/<key>_live_eod.py`. "
      "core = fleet-wide live changes cover it.")
    seen = set()
    for sid, s in STRATS:
        seen.update([sid, s.get("also")])
        eng = [need(BA + e) for e in s["eng"]]
        key = s["key"]
        entry = []
        for e in eng:
            if not e.endswith(".py"):
                entry += sorted(os.path.basename(f)[:-3] for f in files
                                if f.startswith(e + "/") and "/" not in f[len(e) + 1:]
                                and not os.path.basename(f).startswith("test_")
                                and re.search(r"_(runtime|selection_loop|manager|live_core)(_v2)?\.py$", f))
        live = " + ".join(short(e) + ("" if e.endswith(".py") else "/") for e in eng)
        if entry and s.get("core"):
            live += " (" + ", ".join(entry[:3]) + ")"
        parts = [live]
        bt = disp.get(sid)
        if bt:
            parts.append(f"{short(need(BA + bt[0]))} QW:{bt[1]}")
        cfg = " ".join(f"{k}:{rng(v)}" for k, v in (("SL", dflt.get(sid)), ("ST", sett.get(sid)),
                                                     ("BT", form.get(sid))) if v)
        if cfg:
            parts.append(cfg)
        ui = [os.path.basename(need(pnl[sid]))] if sid in pnl else []
        ui += [short(need("frontend/src/" + u)) for u in s.get("ui", [])]
        if s.get("route"):
            routes = [need(BA + "api/" + s["route"])]
        else:
            routes = [f for f in files if f.startswith(BA + "api/") and re.fullmatch(
                rf"{re.escape(key)}(_v\d)?_(state_routes|routes|state)\.py", os.path.basename(f))]
        jobs = [BA + "jobs/" + s["job"]] if s.get("job") else \
            [f for f in files if f == BA + f"jobs/{key}_live_eod.py"]
        ui += [short(need(f)) for f in routes if os.path.basename(f) != f"{key}_state_routes.py"]
        [need(f) for f in jobs]
        if ui:
            parts.append(" ".join(ui))
        tparts = [f"{short(e)} {tests_in(files, e)}" for e in eng
                  if not e.endswith(".py") and tests_in(files, e)]
        if bt:
            bdir = os.path.dirname(BA + bt[0])
            n = tests_in(files, bdir, s.get("tests"))
            if n:
                tparts.append(f"{short(bdir)} {n}")
        if tparts:
            parts.append("tests " + ", ".join(tparts))
        code = names.get(sid)
        head = f"**{code + ' ' if code else ''}{sid}**"
        if s.get("also"):
            head += f" (+{names.get(s['also'], '')} {s['also']})"
        if s.get("core"):
            head += " core"
        if s.get("note"):
            head += f" ({s['note']})"
        w(f"- {head} — " + " · ".join(parts))
    rest = sorted(set(disp) - seen)
    if rest:
        w("- Backtest-only: " + " · ".join(
            (f"{names[sid]} " if sid in names else "") + f"{sid} {short(BA + disp[sid][0])}"
            + (f" BT:{rng(form[sid])}" if sid in form else "") for sid in rest))
    if disp_default:
        w(f"- SCALP_V1 backtest = fall-through {short(BA + disp_default[0])} QW:{disp_default[1]}.")
    owned = {(BA + e)[len(BA + "engine/"):].split("/")[0] for _, s in STRATS for e in s["eng"]}
    unmapped = sorted({f.split("/")[3] for f in files if f.startswith(BA + "engine/") and f.count("/") >= 4} - owned)
    if unmapped:
        w("- Shared engine folders: " + ", ".join(f"E/{u}/" for u in unmapped)
          + ". Codenames: F/strategies/displayNames.js = backend/app/config/strategy_display.py; docs use codenames only.")
    w("")

    w("## Dual tree")
    w("- `backend/` ↔ `desktop/src-tauri/backend/`, `frontend/` ↔ `desktop/src-tauri/frontend/`: the copies are "
      "git-ignored, re-synced by `./desktop/build-scalp.sh frontend|backend|both`. Edit the source; apply scripts "
      "also write the copy when it exists (`BUILD_COPIES`).")
    w("")

    # ── REPO_MAP_FIX_20261117 ── how the suites run, read from the suites themselves
    rs = os.path.join(root, "scripts", "run_suites.py")
    if not os.path.exists(rs):
        raise Missing("scripts/run_suites.py")
    spec = importlib.util.spec_from_file_location("_run_suites_for_map", rs)
    RS = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RS)
    bt = [f for f in files if f.startswith("backend/") and os.path.basename(f).startswith("test_")
          and f.endswith(".py")]
    modes = [RS.mode_of(f) for f in bt]
    n_py = sum(1 for m in modes if m["runner"] == "pytest")
    n_root = sum(1 for m in modes if m["runner"] == "script" and m["cwd"] == "root")
    py_dirs = sorted({short(os.path.dirname(f)) for f, m in zip(bt, modes) if m["runner"] == "pytest"})
    w(f"## Tests — {len(bt) - n_py} backend scripts (exit code) + {n_py} pytest files")
    w("- Failures + summary only: `python3 scripts/run_suites.py PATH...` — runs each suite its own way: "
      f"scripts from backend/; pytest files ({', '.join(py_dirs)}) via `-m pytest` from their folder; "
      f"{n_root} BRK suites from the root. A pytest file run as a plain script exits 0 untested.")
    if os.path.exists(os.path.join(root, ".github", "workflows", "ci.yml")):
        w("- CI: `.github/workflows/ci.yml` (push to main); SKIP / KNOWN_FAIL lists in `scripts/ci/run_ci_suites.py`.")
    groups = {}
    for f in files:
        b = os.path.basename(f)
        if not (b.startswith("test_") and b.endswith(".py")) or f.startswith(".claude/"):
            continue
        p = f.split("/")
        g = "/".join(p[:3]) if f.startswith(BA) and len(p) > 3 else "/".join(p[:-1])
        groups[g] = groups.get(g, 0) + 1
    big = [(g, n) for g, n in sorted(groups.items()) if n > 2]
    small = [g for g, n in sorted(groups.items()) if n <= 2]
    lab = lambda g: (g[len(BA):] if g.startswith(BA) else g) + "/"
    w(f"- {sum(groups.values())} suites (under backend/app/ unless shown): " + " · ".join(
        f"{lab(g)} {n}" for g, n in big) + (f" · {sum(groups[g] for g in small)} more in {len(small)} small folders" if small else ""))
    fleet = [f for f in files if re.fullmatch(BA + r"engine/test_(fleet_live|fleet_adversarial|safety_layers)\.py", f)]
    if fleet:
        w("- Fleet, one fake Kite account: `python3 scripts/run_suites.py " + " ".join(short(f) for f in fleet) + "`")
    w("- Before a release: `python3 scripts/run_suites.py "
      + need(BA + "execution/test_execution_router_contract.py") + "` (E/ B/ A/ J/ prefixes work).")
    if need("frontend/uitest"):
        w("- UI (Playwright, app closed): `cd frontend && CI=false npm run build && "
          "PYTHONPATH=../backend python3 uitest/test_ui_<name>.py` (frontend/uitest/README.md).")
    if os.path.exists(os.path.join(root, "scripts", "regression", "master_suite.py")):  # ── MASTER_SUITE_20261122 ──
        w("- Weekly regression, every suite + report: `python3 scripts/regression/master_suite.py run` "
          "(list: scripts/regression/MASTER_SUITE.md, kept current by pre-commit; weekly job, Fri 16:00: "
          "`scripts/regression/schedule.py install`).")
    w("- No Starlette TestClient (breaks on the build Mac); timestamp knobs get a real-epoch test.")
    w("")

    w("## Shared modules (backend/app/)")
    for rel, sym, desc in SHARED:
        src = read(root, BA + rel)
        ln = symbol_line(src, sym) if sym else None
        if sym and ln is None:
            raise Missing(f"{rel}:{sym}")
        w(f"- `{rel}`" + (f" {sym}:{ln}" if ln else "") + f" — {desc}")
    w("")

    w("## Fences and .bak-FENCE")
    w("- Fence = dated marker `NAME_YYYYMMDD` on every block a change adds (`# ── NAME_… ──`, BEGIN/END); "
      "`grep -rn NAME_` finds it. A change ships as root `apply_<name>.py`: sha-checked payloads, parent-fence and "
      "dirty-`git status` stops, py_compile/tests/esbuild gates, all-or-nothing, `--rollback`.")
    w("- Each changed file gets `<file>.bak-<FENCE>` first; `--rollback` restores them and deletes new files "
      "(newest fence first); a re-run is a no-op. Timeline: `find . -name \"*.bak-*\" | sed 's/.*bak-//' | sort -u`.")
    return "\n".join(o) + "\n"


def main(argv):
    root = os.path.abspath(os.path.expanduser(argv[argv.index("--repo") + 1])) if "--repo" in argv else \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        text = build(root)
    except Missing as e:
        print(f"gen_repo_map: expected file/symbol not found: {e} — update STRATS/SHARED in "
              f"scripts/gen_repo_map.py", file=sys.stderr)
        return 2
    out = os.path.join(root, "REPO_MAP.md")
    if "--stdout" in argv:
        sys.stdout.write(text)
        return 0
    if "--check" in argv:
        cur = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        if cur != text:
            print("REPO_MAP.md is stale — run: python3 scripts/gen_repo_map.py")
            return 1
        print("REPO_MAP.md is current")
        return 0
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote REPO_MAP.md ({len(text)} chars, ~{len(text) * 2 // 7} tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
