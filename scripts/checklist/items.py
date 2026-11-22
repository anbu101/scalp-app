# scripts/checklist/items.py — ── RELIABILITY_GATE_20261118 ── (TD-048)
#                              ── ISOLATED_HOME_20261122 ── CL-15 (DEF-094)
#
# The reliability checklist. Each item has
#   trigger  — something you can read from the staged diff (paths + added lines)
#   check    — an automatic check ("auto": findings block in enforce mode), or a
#              question a person answers ("ack": needs "Ack: CL-NN <why>")
#   incidents — the defects / scars behind it, so nobody has to guess why it exists
#
# The gate (scripts/hooks/reliability_checklist.py) loads THIS FILE FROM HEAD,
# never from the commit being checked: a change cannot loosen its own checks.
# An edit here takes effect from the next commit.
#
# Stdlib only. A check gets `ctx` (see Ctx in the gate) and returns
# (triggered: bool, findings: list[str], note: str).
import ast
import re

STRATEGY_DIRS = {"bb_options", "bb_v2", "brk", "emc", "gc", "ha_options", "ic", "lvb", "orb", "pst",
                 "scalp_v3", "scalpv5", "tma", "tma2", "tsg", "tvx", "vet"}
ENGINE_TO_BACKTEST = {"brk": "brk", "emc": "emc", "gc": "gc", "ic": "ic", "orb": "orb", "pst": "pst",
                      "scalpv5": "scalpv5", "tma": "tma", "tma2": "tma", "tsg": "tsg", "vet": "vet",
                      "ha_options": "ha", "bb_options": "bb"}
FLEET_DIRS = ("backend/app/engine/ic/", "backend/app/engine/tma2/", "backend/app/engine/tma/",
              "backend/app/engine/tsg/", "backend/app/engine/vet/", "backend/app/execution/")
FLEET_FILES = ("backend/app/engine/broker_reconciliation.py", "backend/app/engine/exit_boot.py",
               "backend/app/engine/exit_reconciliation.py", "backend/app/engine/trade_store.py",
               "backend/app/engine/order_state_store.py", "backend/app/engine/startup_reconciliation.py")
FLEET_SUITES = ["backend/app/engine/test_fleet_live.py", "backend/app/engine/test_fleet_adversarial.py",
                "backend/app/engine/test_safety_layers.py"]
EXEC_SUITES = ["backend/app/execution/test_broker_truth.py"]


def is_test(p):
    b = p.rsplit("/", 1)[-1]
    return (b.startswith("test_") or "/tests/" in p or "/uitest/" in p or "testkit" in b
            or b.endswith((".test.js", ".test.jsx")))


def backend_py(p):
    return p.startswith("backend/app/") and p.endswith(".py") and not is_test(p)


def added(ctx, pred, rx):
    """(path, line_no, text) for added lines of files matching pred whose text matches rx."""
    rx = re.compile(rx) if isinstance(rx, str) else rx
    out = []
    for f in ctx.files:
        if f.status == "D" or not pred(f.path):
            continue
        for n, t in f.added:
            s = t.strip()
            if s.startswith(("#", "//", "*")):
                continue
            if rx.search(t):
                out.append((f.path, n, s[:120]))
    return out


def hits_note(hits, k=3):
    return "; ".join(f"{p}:{n}" for p, n, _ in hits[:k]) + (f" (+{len(hits) - k} more)" if len(hits) > k else "")


# ── CL-01 secrets ─────────────────────────────────────────────────────
SECRET = re.compile(r"""(?i)\b(api_?key|api_?secret|secret(_key)?|access_?token|bot_?token|password|passwd)\b['"]?\s*[:=]\s*[rbu]?['"]([A-Za-z0-9]{16,}|\d{6,}:[A-Za-z0-9_\-]{30,})['"]""")


def cl01(ctx):
    hits = []
    for p, n, s in added(ctx, lambda p: not p.endswith((".lock", ".svg", ".min.js")), SECRET):
        v = SECRET.search(s).group(3)
        if re.search(r"(?i)x{4,}|your_|example|dummy|placeholder|changeme|<", v):
            continue
        hits.append((p, n, s))
    return bool(hits), [f"{p}:{n} looks like a hard-coded credential — read it from settings / env" for p, n, _ in hits], ""


# ── CL-02 broker truth ────────────────────────────────────────────────
BROKER_READ = re.compile(r"\.positions\(\)|\bget_gtts\(|\bget_open_positions\(|\.gtts\(\)")
BT_EXEMPT = ("backend/app/execution/broker_truth.py", "backend/app/brokers/")


def cl02(ctx):
    hits = added(ctx, lambda p: backend_py(p) and not p.startswith(BT_EXEMPT) and not p.endswith("_executor.py"),
                 BROKER_READ)
    return bool(hits), [f"{p}:{n} reads the broker directly — use broker_truth.read_positions() / read_gtts() "
                        f"(None = unreadable, never [])" for p, n, _ in hits], ""


# ── CL-03 buildConfig deps (stale closure) ────────────────────────────
STATE_DECL = re.compile(r"const\s*\[\s*(\w+)\s*,\s*set\w+\s*\]\s*=\s*(?:React\.)?useState")


def _callbacks(src, name_rx):
    for m in re.finditer(r"const\s+(" + name_rx + r")\s*=\s*useCallback\(", src):
        i, depth, j = m.end(), 1, m.end()
        while depth and j < len(src):
            c = src[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            j += 1
        call = src[i:j - 1]
        k = call.rfind("[")
        yield m.group(1), src[:m.start()].count("\n") + 1, call[:k], set(re.findall(r"\w+", call[k:]))


def cl03(ctx):
    found, trig = [], False
    for f in ctx.files:
        if f.status == "D" or not f.path.startswith("frontend/src/") or not f.path.endswith((".jsx", ".js")):
            continue
        new_states = {m.group(1) for _, t in f.added for m in [STATE_DECL.search(t)] if m}
        if not new_states:
            continue
        src = f.new_text()
        if "buildConfig" not in src:
            continue
        trig = True
        for name, ln, body, deps in _callbacks(src, r"buildConfig\w*"):
            for s in sorted(new_states):
                if re.search(r"(?<![\w.])" + s + r"\b", body) and s not in deps:
                    found.append(f"{f.path}:{ln} {name} reads new state '{s}' but it is not in the dep array")
    return trig, found, ""


# ── CL-04 boot isolation ──────────────────────────────────────────────
BOOT_OK = {"include_router", "add_middleware", "mount", "on_event", "add_event_handler", "basicConfig",
           "load_dotenv", "getLogger", "setLevel", "addHandler", "filterwarnings", "exception_handler",
           "print", "debug", "info", "warning", "error", "exception", "critical", "append", "setdefault", "start"}
BOOT_FILES = ("backend/app/api_server.py",)


def _top_calls(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out = {}

    def walk(body):
        for s in body:
            if isinstance(s, (ast.Try, ast.If)):
                walk(s.body)
                walk(getattr(s, "orelse", []))
                for h in getattr(s, "handlers", []):
                    walk(h.body)
                continue
            v = s.value if isinstance(s, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
            if not isinstance(v, ast.Call):
                continue
            fn = v.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if nm in BOOT_OK or nm[:1].isupper():   # constructors (APIRouter(), FastAPI()) are cheap
                continue
            out.setdefault(ast.unparse(s), s.lineno)
    walk(tree.body)
    return out


def cl04(ctx):
    fs = [f for f in ctx.files if f.path in BOOT_FILES and f.status != "D"]
    if not fs:
        return False, [], ""
    out = []
    for f in fs:
        old, new = _top_calls(f.old_text()), _top_calls(f.new_text())
        for code, ln in new.items():
            if code not in old:
                out.append(f"{f.path}:{ln} new import-time call `{code[:70]}` — heavy startup goes in "
                           f"_run_heavy_startup() under _boot_guard")
    return True, out, ""


# ── CL-05 scheduler ───────────────────────────────────────────────────
def cl05(ctx):
    hits = added(ctx, lambda p: backend_py(p) and p != "backend/app/api_server.py",
                 r"\b(Background|AsyncIO|Blocking)Scheduler\(")
    return bool(hits), [f"{p}:{n} new APScheduler — register jobs on the app scheduler "
                        f"(misfire_grace_time 3600, coalesce) instead" for p, n, _ in hits], ""


# ── CL-06 false CRITICAL (ack) ────────────────────────────────────────
def cl06(ctx):
    hits = [h for h in added(ctx, lambda p: backend_py(p) and not p.endswith("telegram_api.py"),
                             r"\bnotify_critical\(") if not h[2].startswith("def ")]
    return bool(hits), [], hits_note(hits)


# ── CL-07 shared-strike fleet suites ──────────────────────────────────
def cl07(ctx):
    fs = [f.path for f in ctx.files
          if f.path.endswith(".py") and not is_test(f.path) and (f.path.startswith(FLEET_DIRS) or f.path in FLEET_FILES)]
    if not fs:
        return False, [], ""
    suites = FLEET_SUITES + (EXEC_SUITES if any(p.startswith("backend/app/execution/") for p in fs) else [])
    res = ctx.run_suites(suites)
    if res is None:
        return True, [], "SKIPPED: suites not runnable with this Python"
    return True, [f"suite failed: {s}" for s in res], f"{len(suites)} suites"


# ── CL-08 real clock (ack) ────────────────────────────────────────────
CLOCK = re.compile(r"\b\w*(_ts|_epoch|epoch|_ms|minute_of_day)\b\s*(<=|>=|<|>)(?!>)|(?<![<>-])(<=|>=|<|>)\s*\w*(_ts|_epoch|epoch|_ms|minute_of_day)\b")


LIVE_DIRS = tuple("backend/app/" + d + "/" for d in ("engine", "execution", "jobs", "recon", "risk", "utils",
                                                      "marketdata", "db", "trading", "services"))


def live_py(p):
    return backend_py(p) and p.startswith(LIVE_DIRS)


def cl08(ctx):
    hits = added(ctx, live_py, CLOCK)
    return bool(hits), [], hits_note(hits)


# ── CL-09 UI layout (ack; a uitest change in the same commit counts) ──
LAYOUT = re.compile(r"\b(width|height|maxWidth|minWidth|maxHeight|minHeight|max-width|min-width|max-height|"
                    r"min-height|gridTemplate\w*|grid-template\w*|flexBasis|flex-basis|position)\s*[:=]\s*"
                    r"['\"`{]?\s*(\d|calc|min\(|max\(|clamp|repeat|absolute|fixed|sticky)")


def cl09(ctx):
    hits = added(ctx, lambda p: p.startswith("frontend/src/") and p.endswith((".jsx", ".js", ".css")) and not is_test(p),
                 LAYOUT)
    if not hits:
        return False, [], ""
    if any(f.path.startswith("frontend/uitest/") for f in ctx.files):
        return True, [], "ACKED-BY: frontend/uitest changed in this commit"
    return True, [], hits_note(hits)


# ── CL-10 test timing ─────────────────────────────────────────────────
TIMING = re.compile(r"(?i)\b\w*(elapsed|took|duration|latency|secs|seconds|_dt|_s)\b\s*<=?\s*0\.\d")


def cl10(ctx):
    hits = added(ctx, is_test, TIMING)
    return bool(hits), [f"{p}:{n} timing bound under 1 s — slower Macs miss it (DEF-037: 0.21 s vs 0.2 s); "
                        f"use ≥ 1 s or a relative check" for p, n, _ in hits], ""


# ── CL-11 apply script shape ──────────────────────────────────────────
def cl11(ctx):
    fs = [f for f in ctx.files if f.status != "D" and "/" not in f.path and re.match(r"apply_\w+\.py$", f.path)]
    if not fs:
        return False, [], ""
    need = [(r"\b[A-Z][A-Z0-9_]+_20\d{6}\b", "a dated FENCE"),
            (r"\.bak-|--rollback", "a way back (.bak-<FENCE> backups or --rollback)")]
    out = []
    for f in fs:
        src = f.new_text()
        for rx, what in need:
            if not re.search(rx, src):
                out.append(f"{f.path}: no {what}")
    return True, out, ""


# ── CL-12 close booking (ack) ─────────────────────────────────────────
def cl12(ctx):
    hits = [h for h in added(ctx, live_py, r"\bclose_trade\(|\bexit_order_id\b|['\"]CLOSED['\"]|\bBROKER_EXIT\b")
            if not h[2].startswith("def ")]
    return bool(hits), [], hits_note(hits)


# ── CL-13 live changed, backtest not (ack) ────────────────────────────
LOGIC = re.compile(r"(?i)\b(entry|exit|stop|sl|tp|target|signal|cross\w*|threshold|premium|strike|lots?)\b\s*(=|<|>|\*|\+|-|/)")


def cl13(ctx):
    paths = {f.path for f in ctx.files}
    logic = {f.path for f in ctx.files if any(LOGIC.search(t) for _, t in f.added)}
    miss = []
    for p in sorted(logic):
        m = re.match(r"backend/app/engine/(\w+)/", p)
        if not m or is_test(p) or not p.endswith(".py") or m.group(1) not in ENGINE_TO_BACKTEST:
            continue
        bt = "backend/app/backtest/" + ENGINE_TO_BACKTEST[m.group(1)] + "/"
        if not any(q.startswith(bt) for q in paths):
            miss.append(f"{m.group(1)} (no change under {bt})")
    miss = sorted(set(miss))
    return bool(miss), [], "; ".join(miss)


# ── CL-14 strategy independence (ack) ─────────────────────────────────
def cl14(ctx):
    hits = []
    for f in ctx.files:
        m = re.match(r"backend/app/engine/(\w+)/", f.path)
        if f.status == "D" or not m or is_test(f.path) or not f.path.endswith(".py"):
            continue
        for n, t in f.added:
            im = re.match(r"\s*(?:from|import)\s+(?:backend\.)?(?:app\.)?engine\.(\w+)", t)
            if im and im.group(1) in STRATEGY_DIRS and im.group(1) != m.group(1):
                hits.append((f.path, n, f"{m.group(1)} → {im.group(1)}"))
    return bool(hits), [], "; ".join(f"{p}:{n} {s}" for p, n, s in hits[:3])


# ── CL-15 suite runners give every suite a throw-away HOME ────────────
RUNNERS = ("scripts/run_suites.py", "scripts/regression/master_suite.py", "scripts/mutation/mutmut_run.py",
           "scripts/ci/run_ci_suites.py")
HOME_SET = re.compile(r"""\bHOME\s*=\s*(?!os\.path\.expanduser)\S|\[\s*["']HOME["']\s*\]\s*=|\bisolate_home\(|"""
                      r"""\bplan\([^)]*\bhome\s*=""")


def cl15(ctx):
    fs = [f for f in ctx.files if f.status != "D" and f.path in RUNNERS]
    if not fs:
        return False, [], ""
    return True, [f"{f.path}: runs suites without pointing HOME at a throw-away folder — a suite then saves into "
                  f"the real ~/.scalp-app (DEF-094)" for f in fs if not HOME_SET.search(f.new_text())], ""


ITEMS = [
    dict(id="CL-01", kind="auto", title="No hard-coded credentials", fn=cl01,
         trigger="an added line assigns a 16+ character literal to a key / secret / token / password name",
         check="blocked unless acked: move it to settings or the environment",
         incidents="DEF-047 (Kite key, secret and a token hard-coded in a readable repo)"),
    dict(id="CL-02", kind="auto", title="Broker reads go through broker_truth", fn=cl02,
         trigger="added .positions() / get_gtts( / get_open_positions( / .gtts() in backend code "
                 "outside broker_truth.py, executors and brokers/",
         check="use read_positions() / read_gtts(): None means unreadable, never an empty list",
         incidents="DEF-003, DEF-005 (false BROKER_EXIT on unreadable answers), DEF-010, DEF-015, DEF-021"),
    dict(id="CL-03", kind="auto", title="New React state read by buildConfig is in its dep array", fn=cl03,
         trigger="a .jsx file that has buildConfig gains a useState",
         check="every new state that a buildConfig* callback reads is in that callback's dep array",
         incidents="stale-closure scar (Backtest form sent an old value; doctrine: same commit as the state)"),
    dict(id="CL-04", kind="auto", title="No new import-time work in api_server.py", fn=cl04,
         trigger="backend/app/api_server.py changes",
         check="no new module-level call (routers / middleware / constructors are fine)",
         incidents="BOOT_ISOLATION (import-time corpus repair blocked the port bind; app looked dead)"),
    dict(id="CL-05", kind="auto", title="Jobs use the app scheduler", fn=cl05,
         trigger="a new Background/AsyncIO/BlockingScheduler( outside api_server.py",
         check="register on the app scheduler (misfire_grace_time 3600, coalesce)",
         incidents="silent missed EOD jobs: APScheduler default misfire_grace_time is 1 s (eod_safety.py)"),
    dict(id="CL-06", kind="ack", title="A CRITICAL fires only when it is really critical", fn=cl06,
         trigger="an added notify_critical( call",
         check="the condition is confirmed (second pass / grace / order still working), not the first look",
         incidents="DEF-030 (09:16 'square-off NOT complete' while still closing), DEF-036 ('did NOT fill' while working)"),
    dict(id="CL-07", kind="auto", title="Fleet shared-strike suites pass", fn=cl07,
         trigger="code under engine/ic, tma, tma2, tsg, vet, execution/ or the exit / reconciliation / store modules changes",
         check="runs test_fleet_live, test_fleet_adversarial, test_safety_layers (+ test_broker_truth for execution/)",
         incidents="DEF-004, DEF-007, DEF-018, DEF-019, DEF-023, DEF-027 (strategies sharing a strike)"),
    dict(id="CL-08", kind="ack", title="Time comparisons are tested on the real clock", fn=cl08,
         trigger="an added comparison with a *_ts / *_epoch / *_ms / minute_of_day value in live code (engine, execution, jobs, …)",
         check="a test uses a real epoch time, and a fail-closed branch has a test proving it can open",
         incidents="TSG_BANK_FIX1 (minute-of-day vs epoch: the guard never fired; toy-integer tests passed)"),
    dict(id="CL-09", kind="ack", title="Layout changes are checked in a real browser", fn=cl09,
         trigger="an added size / grid / flex-basis / absolute-fixed-sticky position value in frontend/src",
         check="checked in Chromium with frontend/uitest (a uitest change in the same commit counts)",
         incidents="HOST_PANEL_FIT (SSR passed, the panel overlapped in the app), DEF-042, DEF-043, DEF-057, DEF-059, DEF-064"),
    dict(id="CL-10", kind="auto", title="No sub-second timing bounds in tests", fn=cl10,
         trigger="an added test line bounds elapsed / took / latency / seconds below 1 s",
         check="use ≥ 1 s or compare against a measured baseline",
         incidents="DEF-037 (0.2 s bound, 0.21 s on the MacBook Air: the apply script aborted)"),
    dict(id="CL-11", kind="auto", title="Apply scripts have the safety shape", fn=cl11,
         trigger="an apply_*.py at the repo root is added or changed",
         check="a dated FENCE, and a way back (.bak-<FENCE> backups or --rollback)",
         incidents="ORB_RECONCILE (working-copy drift), VET_PANEL_V2 (mixed state)"),
    dict(id="CL-12", kind="ack", title="Closes carry their order id and are never silently dropped", fn=cl12,
         trigger="added close_trade( / exit_order_id / 'CLOSED' / BROKER_EXIT in live code (engine, execution, db, jobs, …)",
         check="the close stores its exit order id; a no-op close (row already closed) is logged and alerted",
         incidents="DEF-005 (false BROKER_EXIT), DEF-032 (close dropped silently), DEF-034 (no exit order id)"),
    dict(id="CL-13", kind="ack", title="Live change keeps the backtest in step", fn=cl13,
         trigger="a strategy's engine/<s>/ entry / exit / stop / target / strike logic changes with nothing under its backtest/<s>/",
         check="the backtest runner still matches live (parity suite / runner sim); backtest follows live",
         incidents="parity-by-construction; Backtest-matches-live rule (31 Oct)"),
    dict(id="CL-14", kind="ack", title="A strategy does not need another strategy running", fn=cl14,
         trigger="engine/<s>/ gains an import from another strategy's package",
         check="it is shared code only — works when that strategy is off or not licensed",
         incidents="strategy independence rule (15 Oct: any strategy can be licensed alone)"),
    dict(id="CL-15", kind="auto", title="Suite runners never hand a suite the real HOME", fn=cl15,
         trigger="a suite runner changes (run_suites.py, regression/master_suite.py, mutation/mutmut_run.py, ci/run_ci_suites.py)",
         check="each suite runs with HOME pointed at a throw-away folder (isolate_home / plan(home=…) / HOME=<tmp>)",
         incidents="DEF-094 (master suite: fake LIVE ORB trades in app.db, Zerodha / Telegram credentials replaced)"),
]
