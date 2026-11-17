#!/usr/bin/env python3
# scripts/test_repo_map.py  — ── REPO_MAP_20261117 ── (TD-038) TC-REPOMAP-0001…0014
#
#   python3 scripts/test_repo_map.py      (stdlib only; exit 1 on any failure)
import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "scripts", "gen_repo_map.py")
RUN = os.path.join(ROOT, "scripts", "run_suites.py")
MAP = os.path.join(ROOT, "REPO_MAP.md")
F = 0


def chk(tc, msg, ok, info=""):
    global F
    print(("  ok   " if ok else "  FAIL ") + f"{tc} {msg}" + ("" if ok else f"  -> {info}"))
    F += 0 if ok else 1


def sh(*a):
    r = subprocess.run([sys.executable, *a], cwd=ROOT, capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout + r.stderr


def load_gen():
    spec = importlib.util.spec_from_file_location("gen_repo_map_t", GEN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


text = open(MAP, encoding="utf-8").read()

# 0001 stdlib only
bad = []
for f in (GEN, RUN, __file__):
    for n in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module] if isinstance(n, ast.ImportFrom) and n.module else []
        bad += [f"{os.path.basename(f)}:{x}" for x in mods if x.split(".")[0] not in sys.stdlib_module_names]
chk("TC-REPOMAP-0001", "generator, runner and this test import the standard library only", not bad, bad)

# 0002 current + 0003 deterministic
rc, out = sh(GEN, "--check")
chk("TC-REPOMAP-0002", "REPO_MAP.md matches what the generator writes for this tree (--check)", rc == 0, out)
a, b = sh(GEN, "--stdout")[1], sh(GEN, "--stdout")[1]
chk("TC-REPOMAP-0003", "two runs give byte-identical output", a == b and a == text, len(a))

# 0004 size
chk("TC-REPOMAP-0004", f"REPO_MAP.md stays within ~2,000 tokens ({len(text)} chars ≤ 7,500)", len(text) <= 7500, len(text))

# 0005 codenames + sections
names = dict(re.findall(r'^\s*([A-Z0-9_]+):\s*\{\s*real:\s*"[^"]*",\s*code:\s*"([^"]*)"',
                        open(os.path.join(ROOT, "frontend/src/strategies/displayNames.js"), encoding="utf-8").read(), re.M))
want = ["IC_V2", "TMA_V2", "TSG_V1", "VET_V1", "EMC_V1", "LVB_V1"]
miss = [s for s in want if f"**{names.get(s)} {s}**" not in text]
chk("TC-REPOMAP-0005", "Icarus, Timberwolf, Tigris, Velvet, Ember, Tripwire each have a line under their codename",
    not miss, miss)
secs = ["## Strategies", "## Dual tree", "## Tests", "## Shared modules", "## Fences"]
chk("TC-REPOMAP-0006", "all five sections are present", all(s in text for s in secs),
    [s for s in secs if s not in text])

# 0007 every path resolves
EXP = {"E/": "backend/app/engine/", "B/": "backend/app/backtest/", "A/": "backend/app/api/",
       "J/": "backend/app/jobs/", "F/": "frontend/src/"}
panels = {os.path.basename(p): p for d, _, fs in os.walk(os.path.join(ROOT, "frontend/src/strategies"))
          for p in [os.path.join(d, f) for f in fs]}
broken = []
shared_sec = text.split("## Shared modules")[1].split("##")[0]
anywhere = {f for d in ("backend/app", "frontend/src") for _, _, fs in os.walk(os.path.join(ROOT, d)) for f in fs}
for tok in set(re.findall(r"(?<![\w/<>.-])(?:[\w./-]+\.(?:py|jsx|js|md|sh)\b|[EBAJF]/[\w/]+/)", text)):
    if tok.startswith((".", "_")) or "*" in tok or ("/" not in tok and tok in anywhere):
        continue
    t = tok
    for k, v in EXP.items():
        if t.startswith(k):
            t = v + t[len(k):]
    cands = [t, os.path.join("backend/app", t), os.path.join("backend/app/execution", t)]
    if t in panels:
        continue
    if not any(os.path.exists(os.path.join(ROOT, c)) for c in cands):
        broken.append(tok)
chk("TC-REPOMAP-0007", "every file and folder REPO_MAP names exists", not broken, broken[:10])

# 0008 line hints land on the right line
def line(rel, n):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        ls = f.read().splitlines()
    return ls[n - 1] if 0 < n <= len(ls) else ""


wrong = []
for m in re.finditer(r"^- \*\*(?:\w+ )?([A-Z0-9_]+)\*\*.*$", text, re.M):
    sid, row = m.group(1), m.group(0)
    for lab, rel, pat in (("SL", "backend/app/config/strategy_loader.py", f'"{sid}"'),
                          ("ST", "frontend/src/pages/Settings.jsx", f'case "{sid}"'),
                          ("BT", "frontend/src/pages/Backtest.jsx", "&& ("),
                          ("QW", "backend/app/backtest/queue_worker.py", "strategy_id")):
        h = re.search(rf"\b{lab}:(\d+)", row)
        if h and pat not in line(rel, int(h.group(1))):
            wrong.append(f"{sid} {lab}:{h.group(1)}")
chk("TC-REPOMAP-0008", "SL/ST/BT/QW line numbers point at that strategy's block", not wrong, wrong)
sym_wrong = [f"{p}:{s}:{n}" for p, s, n in re.findall(r"^- `([\w/.]+)` (\w+):(\d+)", shared_sec, re.M)
             if s not in line("backend/app/" + p, int(n))]
chk("TC-REPOMAP-0009", "shared-module symbol line numbers point at the symbol", not sym_wrong, sym_wrong)

# 0010 a missing hand-kept path fails loudly
g = load_gen()
g.STRATS = g.STRATS + [("ZZZ_V9", dict(eng=["engine/no_such_folder"], key="zzz"))]
try:
    g.build(ROOT)
    raised = False
except g.Missing:
    raised = True
chk("TC-REPOMAP-0010", "a STRATS path that no longer exists stops the generator (no silent gap)", raised)

# 0011 --check catches a stale map
orig = open(MAP, "rb").read()
try:
    with open(MAP, "ab") as f:
        f.write(b"stale\n")
    rc, out = sh(GEN, "--check")
finally:
    with open(MAP, "wb") as f:
        f.write(orig)
chk("TC-REPOMAP-0011", "--check exits 1 when REPO_MAP.md is out of date", rc == 1 and "stale" in out, (rc, out))

# 0012 CLAUDE.md
cm = open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read()
added = cm.split("REPO_MAP_20261117", 1)[1] if "REPO_MAP_20261117" in cm else ""
chk("TC-REPOMAP-0012", "CLAUDE.md keeps its QA Register section and adds a short REPO_MAP section (≤1,200 chars)",
    "## QA Register" in cm and "REPO_MAP.md" in added and 0 < len(added) <= 1200, len(added))

# 0013 / 0014 runner prints failures + summary only, exit codes
with tempfile.TemporaryDirectory() as d:
    open(os.path.join(d, "test_ok.py"), "w").write("print('lots of passing noise\\n' * 50)\n")
    open(os.path.join(d, "test_bad.py"), "w").write(
        "import sys\nfor i in range(40): print('line', i)\nprint('BOOM')\nsys.exit(1)\n")
    open(os.path.join(d, "test_env.py"), "w").write("import no_such_pkg_xyz\n")
    rc, out = sh(RUN, d)
    lines = out.strip().splitlines()
    ok = (rc == 1 and "✗" in out and "BOOM" in out and "line 10" not in out and "passing noise" not in out
          and lines[-1].startswith("1 passed, 1 failed, 1 not runnable here — 3 suites"))
    chk("TC-REPOMAP-0013", "run_suites shows only the failure tail + one summary line and exits 1", ok, out[-400:])
    os.remove(os.path.join(d, "test_bad.py"))
    rc, out = sh(RUN, "--quiet", d)
    chk("TC-REPOMAP-0014", "all passing (env-missing not counted) exits 0; --quiet prints just the summary",
        rc == 0 and len(out.strip().splitlines()) == 1, (rc, out))
rc, out = sh(RUN, "E/no_such_dir")
chk("TC-REPOMAP-0015", "run_suites expands E/ and rejects a missing path with exit 2", rc == 2 and "no such" in out, out)

print("\n" + ("ALL REPO_MAP CHECKS PASSED" if F == 0 else f"{F} FAILURES"))
sys.exit(1 if F else 0)
