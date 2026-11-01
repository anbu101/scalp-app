#!/usr/bin/env python3
# apply_recorded_source_fix1.py — ── RECORDED_SOURCE_FIX1_20261031 ──
#
# After the seam check passed on Backtest → Backfill, the Run tab still said
# "run the seam check first" and showed no "Use recorded data" box: the Run
# tab read its coverage (seam_ok, recorded_from) once when the page mounted.
# It now re-reads it every time the Run tab is opened. Frontend only; both
# trees (frontend/ and desktop/src-tauri/frontend/).
#
#   cd <repo root> && python3 apply_recorded_source_fix1.py
#   then: ./desktop/build-scalp.sh frontend
import os
import sys

ANCHOR = '''  const [dataSource, setDataSource] = useState("dhan");   // ── RECORDED_SOURCE_20261031 ── "dhan" | "recorded"
'''
ADD = '''  // ── RECORDED_SOURCE_FIX1_20261031 ── coverage (seam_ok, recorded_from) was
  // read once on mount, so a seam check passed on the Backfill tab did not
  // unlock "Use recorded data" until the page was reloaded. Re-read it every
  // time the Run tab is opened.
  useEffect(() => {
    if (pageView !== "run") return;
    apiCall("/api/backtest/coverage?underlying=NIFTY").then(setCoverage).catch(() => {});
  }, [pageView]);
'''
args = [a for a in sys.argv[1:] if not a.startswith("--")]
root = os.path.abspath(args[0] if args else os.getcwd())
rel = "frontend/src/pages/Backtest.jsx"
done = 0
for base in ("", "desktop/src-tauri"):
    p = os.path.join(root, base, rel)
    if not os.path.exists(p):
        continue
    s = open(p, encoding="utf-8").read()
    if "RECORDED_SOURCE_FIX1_20261031" in s:
        print(f"  already fixed: {os.path.join(base, rel)}")
        done += 1
        continue
    if s.count(ANCHOR) != 1:
        sys.exit(f"✗ {os.path.join(base, rel)}: anchor not found once — is apply_seam_check.py applied?")
    with open(p + ".bak-RECORDED_SOURCE_FIX1_20261031", "w", encoding="utf-8") as f:
        f.write(s)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s.replace(ANCHOR, ANCHOR + ADD))
    print(f"  fixed {os.path.join(base, rel)}")
    done += 1
if not done:
    sys.exit("✗ Backtest.jsx not found — run from the repo root")
print("✓ RECORDED_SOURCE_FIX1_20261031 — now: ./desktop/build-scalp.sh frontend")
