# backend/app/engine/test_mode_follow_fleet.py
#
# ── VET_MODE_FOLLOW_20261125 (DEF-102) ── fleet rule: a Settings mode change
# (PAPER → LIVE) takes effect at the next entry WITHOUT a restart.
#
# 25 Nov: VET armed PAPER at 08:30, Settings went LIVE at 08:46, the 09:20
# entry was booked PAPER. VET built its broker executor ONLY inside an
# `if mode == "LIVE":` branch at arm time, so it could not trade live until
# the next arm. Every other live engine pre-builds the executor (TMA_V2,
# TSG_V1, IC, BB, HA: "a PAPER→LIVE flip mid-session never needs a
# restart"). This suite scans every engine file and fails when a file builds
# its executor ONLY on a LIVE branch of a mode test — the shape of DEF-102.
# Behaviour (the next entry really is LIVE) is covered per strategy, e.g.
# engine/vet/test_vet_manager.py TC-VET-0332…0343.
#
# Run: python3 app/engine/test_mode_follow_fleet.py   (from backend/)
import ast
import pathlib
import re
import sys

ENGINE = pathlib.Path(__file__).resolve().parent
MODE_TEST = re.compile(r"LIVE|\bmode\b", re.I)
FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def live_only_executor(src: str):
    """Line numbers of get_executor_for_strategy( calls that sit in the BODY
    of an `if <mode/LIVE test>:` — but only when the file has no other call
    (outside such a branch) that would pre-build it for PAPER too."""
    tree = ast.parse(src)
    parent = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[c] = n
    gated, free = [], []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", "")) == "get_executor_for_strategy"):
            continue
        child, p, in_live = n, parent.get(n), False
        while p is not None:
            if isinstance(p, ast.If) and MODE_TEST.search(ast.get_source_segment(src, p.test) or ""):
                in_live = any(child is b for b in p.body)
                break
            child, p = p, parent.get(p)
        (gated if in_live else free).append(n.lineno)
    return gated if (gated and not free) else []


def engine_files():
    for f in sorted(ENGINE.rglob("*.py")):
        name = f.name
        if name.startswith("test_") or ".bak-" in name or "__pycache__" in f.parts:
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if "get_executor_for_strategy" in txt:
            yield f, txt


print("── TC-FLEET-0104 every engine pre-builds its broker executor (no LIVE-only build at arm)")
scanned, bad = 0, []
for f, txt in engine_files():
    scanned += 1
    hits = live_only_executor(txt)
    if hits:
        bad.append(f"{f.relative_to(ENGINE)}:{hits}")
check(f"TC-FLEET-0104 no engine builds its executor only when the arm-time mode is LIVE ({scanned} files)",
      scanned >= 5 and not bad, bad)

print("── TC-FLEET-0105 the scanner itself")
VET_OLD = '''
def arm(mode):
    executor = None
    if mode == "LIVE":
        executor = get_executor_for_strategy("VET_V1")
'''
BB_LIKE = '''
def arm(trade_mode):
    if trade_mode == "LIVE":
        executor = get_executor_for_strategy("BB_V1")
    else:
        try:
            executor = get_executor_for_strategy("BB_V1")
        except Exception:
            executor = None
'''
PREBUILT = '''
def arm(mode):
    try:
        executor = get_executor_for_strategy("TMA_V2")
    except Exception:
        executor = None
'''
REATTACH = '''
def loop(m):
    if m.executor is None:
        m.attach_executor(get_executor_for_strategy("TSG_V1"))
'''
check("TC-FLEET-0105 VET's old shape (executor only under `if mode == \"LIVE\"`) is caught",
      live_only_executor(VET_OLD) == [5])
check("TC-FLEET-0105 a LIVE branch plus a PAPER pre-build (BB / HA) passes", live_only_executor(BB_LIKE) == [])
check("TC-FLEET-0105 an unconditional pre-build (TMA_V2 / TSG / IC) passes", live_only_executor(PREBUILT) == [])
check("TC-FLEET-0105 a re-attach under `executor is None` passes", live_only_executor(REATTACH) == [])

if FAILS:
    print(f"\n{len(FAILS)} FAILURES")
    sys.exit(1)
print("\nALL MODE-FOLLOW FLEET CHECKS PASSED")
