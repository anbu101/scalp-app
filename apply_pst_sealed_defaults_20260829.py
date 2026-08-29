#!/usr/bin/env python3
# apply_pst_sealed_defaults_20260829.py
#
# ── PST_SEALED_DEFAULTS_20260829 ──
#
# WHAT THIS FIXES: the three sealed filter keys (allowed_levels,
# skip_expiry_day, confirm_minutes) were added to the managers, the Settings
# UI and license_server/strategy_defaults.json — but NOT to
# DEFAULT_STRATEGY_CONFIGS in backend/app/config/strategy_loader.py. The
# apply script that should have done it (apply_pst_live_filters_20260828.py)
# carried a patch_loader() that only printed and returned the source
# unchanged. That is why the keys are absent from the file.
#
# WHY THE DEFAULT IS THE FIX FOR NON-ADMIN USERS:
#   _load_strategy_config_ex_uncached does
#       merged = deepcopy(DEFAULT_STRATEGY_CONFIGS[sid]); deep_update(merged, cfg)
#   and deep_update only assigns keys PRESENT in the on-disk file. Every
#   existing user's PST config predates these keys, so the keys are absent
#   from their file and the DEFAULT VALUE SURVIVES THE MERGE. Putting the
#   sealed values here therefore reaches every non-admin user on the next
#   backend start, with no migration and without touching their file.
#
#   Anyone who HAS saved the keys (the admin, via Settings) keeps their own
#   values: their file has the keys, so the file wins. Correct precedence in
#   both directions.
#
# SCOPE — DELIBERATELY ONLY THE THREE FILTER KEYS. Legs, sl_pct, exit_time
# and spot_tg_points already EXIST in every user's file, so changing their
# defaults would reach nobody. Aligning those requires a migration that
# OVERWRITES user config, which this script does not do: that file's own
# 2026-06-15 postmortem is about a silent default-overwrite turning paper
# into live. Any such migration is a separate, explicitly-decided change.
#
# ⚠ LIVE-SHARED FILE (strategy_loader.py) — non-trading-day deploy.

import os
import py_compile
import tempfile

FENCE = "PST_SEALED_DEFAULTS_20260829"
REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
TREES = [os.path.join(REPO, "backend"),
         os.path.join(REPO, "desktop", "src-tauri", "backend")]
LOADER_REL = os.path.join("app", "config", "strategy_loader.py")

# Sealed values (PST_Sell_Hedge_Sealed_Config.pdf, 28-Aug-2026)
SEALED = {
    "PST_SELL": ('["PP", "S1", "S3", "R3"]', "True", "4"),
    "PST_HEDGE": ('["PP", "R3"]', "True", "3"),
}


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def patch_loader(src):
    if FENCE in src:
        print("  strategy_loader: fence present — skipping (idempotent)")
        return src

    for sid, (levels, skip, cfm) in SEALED.items():
        # anchor on the risk-limit line that closes each PST default block
        anchor = ('    "%s": {\n'
                  '        "trade_execution_mode": "PAPER",\n' % sid)
        if anchor not in src:
            raise SystemExit(f"ABORT: {sid} default block not found in the "
                             f"expected shape. No files written.")
        # insert the three keys immediately after the opening mode line so
        # they sit with the other execution knobs
        new_block = anchor + (
            '        # ── %s ── sealed entry filters. These reach EXISTING\n'
            '        # users because deep_update only overwrites keys present in\n'
            '        # their on-disk config, and no saved PST config predates\n'
            '        # 2026-08-28 has them. A user who sets them in Settings\n'
            '        # overrides these, as normal.\n'
            '        "allowed_levels": %s,\n'
            '        "skip_expiry_day": %s,\n'
            '        "confirm_minutes": %s,\n' % (FENCE, levels, skip, cfm))
        src = _ro(src, anchor, new_block, f"{sid} defaults")
    return src


def _stage_compile(label, content):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
        t.write(content)
        tmp = t.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise SystemExit(f"ABORT: staged compile failed for {label}: {e}")
    finally:
        os.unlink(tmp)


def main():
    patched_any = False
    for tree in TREES:
        p = os.path.join(tree, LOADER_REL)
        if not os.path.isfile(p):
            print(f"[skip] tree not present: {tree}")
            if "src-tauri" in tree:
                print("       (desktop tree absent — re-run there before the "
                      "next PyInstaller build)")
            continue
        print(f"[tree] {tree}")
        cur = open(p).read()
        new = patch_loader(cur)
        _stage_compile(p, new)
        if new != cur:
            open(p, "w").write(new)
            print(f"  wrote {p}")
        patched_any = True
    if not patched_any:
        raise SystemExit("ABORT: no tree found. Set SCALP_REPO.")
    print("DONE —", FENCE)
    print("\n⚠ strategy_loader.py is LIVE-SHARED — deploy on a non-trading day.")
    print("   Non-admin users pick the filters up on the next backend start;")
    print("   no config migration and no file rewrite.")


if __name__ == "__main__":
    main()
