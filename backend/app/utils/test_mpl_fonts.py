# backend/app/utils/test_mpl_fonts.py — ── TEST_FIXES_20261126 ── (DEF-088 / DEF-089) TC-UTIL-0030…0036
#
#   cd backend && PYTHONPATH=$PWD python3 app/utils/test_mpl_fonts.py
import os
import re
import shutil
import subprocess
import sys
import tempfile

from app.utils import mpl_fonts as M

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        FAILS.append(name)


d = tempfile.mkdtemp(prefix="mplfonts_")
check("TC-UTIL-0030 an empty font folder: all four DejaVu files are named missing",
      M.missing_fonts(d) == list(M.REQUIRED), M.missing_fonts(d))
for f in M.REQUIRED[:2] + ("DejaVuSansDisplay.ttf",):
    open(os.path.join(d, f), "wb").close()
check("TC-UTIL-0031 the 30 Oct shape (Sans + Bold present, Mono gone): only the Mono files are missing",
      M.missing_fonts(d) == ["DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"], M.missing_fonts(d))
for f in M.REQUIRED:
    open(os.path.join(d, f), "wb").close()
check("TC-UTIL-0032 all four present: nothing missing", M.missing_fonts(d) == [])
shutil.rmtree(d, ignore_errors=True)

check("TC-UTIL-0033 this Python's matplotlib has its DejaVu fonts (else: " + M.repair_command() + ")",
      M.missing_fonts() == [], M.missing_fonts())

# require_fonts() in a child whose matplotlib data path points at an empty folder
empty = tempfile.mkdtemp(prefix="mplempty_")
code = ("import matplotlib, sys; matplotlib.get_data_path = lambda: sys.argv[1]\n"
        "from app.utils.mpl_fonts import require_fonts\nrequire_fonts('probe_suite')\nprint('NOT REACHED')\n")
r = subprocess.run([sys.executable, "-c", code, empty], capture_output=True, text=True,
                   env=dict(os.environ, PYTHONPATH=os.getcwd()), timeout=120)
out = r.stdout + r.stderr
check("TC-UTIL-0034 require_fonts(): exit 1, one FAIL line naming the suite, the missing files and the pip repair "
      "command; nothing after it runs",
      r.returncode == 1 and "probe_suite" in out and "DejaVuSans.ttf" in out and "NOT REACHED" not in out
      and re.search(r"-m pip install --force-reinstall --no-deps matplotlib==\S+", out) is not None, out)
shutil.rmtree(empty, ignore_errors=True)

check("TC-UTIL-0035 this Python's font lookup lands on matplotlib's own DejaVu Sans and Sans Mono (cache not stale)",
      M.stale_lookups() == [], M.stale_lookups())
# a stale cache: files on disk, but the cached list does not know DejaVu Sans Mono
code = ("import matplotlib.font_manager as fm\n"
        "fm.fontManager.ttflist = [f for f in fm.fontManager.ttflist if f.name != 'DejaVu Sans Mono']\n"
        "fm.fontManager._findfont_cached.cache_clear() if hasattr(fm.fontManager._findfont_cached, 'cache_clear') else None\n"
        "from app.utils.mpl_fonts import require_fonts\nrequire_fonts('probe_suite')\nprint('NOT REACHED')\n")
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                   env=dict(os.environ, PYTHONPATH=os.getcwd()), timeout=120)
out = r.stdout + r.stderr
check("TC-UTIL-0036 stale font cache (files present, cache lacks DejaVu Sans Mono): exit 1, one FAIL line naming "
      "the family and the rm command for the fontlist cache; nothing after it runs",
      r.returncode == 1 and "font cache is stale" in out and "DejaVu Sans Mono" in out and "fontlist-v" in out
      and "NOT REACHED" not in out, out)

print(f"\n{7 - len(FAILS)} passed, {len(FAILS)} failed")
if FAILS:
    sys.exit(1)
print("ALL MPL FONT CHECKS PASSED")
