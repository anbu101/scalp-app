#!/usr/bin/env python3
# apply_cbo_progress_20260829.py
#
# ── CBO_PROGRESS_20260829 ── make CBO_V1's progress_cb speak the status
# endpoint's contract.
#
# THE BUG: the runner emitted {"day": "<iso-date>", "i": ..., "n": ...}.
# The UI renders `day ${p.day}/${p.total_days} · ${p.date}` and _eta() reads
# progress["day"] / progress["total_days"] as done/total counters — so the
# bar showed `day 2026-05-22/undefined · undefined` and, with total_days
# absent, _eta returned None and no ETA ever appeared. The contract
# (established by VET/TMA/PST) is:
#     day         1-based integer index of the day being processed
#     total_days  total days in the range
#     date        ISO date string, display only
# `trades` is kept as an extra field; the status endpoint passes unknown
# keys through untouched.
#
# Idempotent, assert-anchored, dual-tree, staged py_compile — fleet standard.
#     python3 apply_cbo_progress_20260829.py --check
#     python3 apply_cbo_progress_20260829.py

from __future__ import annotations

import argparse
import py_compile
import sys
import tempfile
from pathlib import Path

FENCE = "CBO_PROGRESS_20260829"

TARGETS = [
    Path("backend/app/backtest/cbo/backtest_cbo_runner.py"),
    Path("desktop/src-tauri/backend/app/backtest/cbo/backtest_cbo_runner.py"),
]

OLD = '''        if progress_cb:
            progress_cb({"day": day.isoformat(), "i": i, "n": len(days),
                         "trades": len(trades)})'''

NEW = f'''        if progress_cb:
            # ── {FENCE} ── the status endpoint's _eta() reads
            # progress["day"]/["total_days"] as done/total counters and the
            # UI renders `day X/total · date`. Emitting the iso date under
            # "day" showed `day 2026-05-22/undefined` and killed the ETA
            # (total_days absent -> frac 0 -> eta None). Contract matches
            # VET/TMA/PST: day = 1-based index, total_days = count,
            # date = display string.
            progress_cb({{"day": i + 1, "total_days": len(days),
                         "date": day.isoformat(), "trades": len(trades)}})'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    present = [t for t in TARGETS if t.exists()]
    if not present:
        print("ABORTED: no CBO runner found — run from the repo root, after "
              "apply_cbo_v1_20260829.py", file=sys.stderr)
        return 1

    for t in TARGETS:
        if not t.exists():
            print(f"  SKIPPED (tree absent)              {t}")
            continue
        text = t.read_text()
        if FENCE in text:
            print(f"  already fenced — skipped           {t}")
            continue
        n = text.count(OLD)
        if n != 1:
            print(f"ABORTED: {t}: anchor found {n} times, expected 1 — file "
                  f"has drifted. Nothing written.", file=sys.stderr)
            return 1
        patched = text.replace(OLD, NEW, 1)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(patched)
            tmp = fh.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            print(f"ABORTED: staged compile failed for {t}: {e}",
                  file=sys.stderr)
            return 1
        finally:
            Path(tmp).unlink(missing_ok=True)
        if args.check:
            print(f"  would patch (clean)                {t}")
        else:
            t.write_text(patched)
            print(f"  patched                            {t}")

    print(f"\n{FENCE} {'check complete' if args.check else 'applied'}. "
          f"Restart the backend; no rebuild needed for a dev run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
