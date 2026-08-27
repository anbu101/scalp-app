#!/usr/bin/env python3
# apply_vet_daily_mtm_cap_20260827.py
#
# ── VET_V1 MAX DAILY MTM LOSS ── frontend wiring for the new
# max_daily_mtm_loss config key. Backend counterpart ships in
# apply_vet_v1_backtest_20260826.py — RUN THAT FIRST, or the UI will send a
# key the runner ignores.
#
# WHAT IT IS
#   A per-SESSION loss limit in rupees, evaluated at every timeframe close on
#   realised P&L + the open position's unrealised mark. On breach the book is
#   flattened at that bar's fill and NO further entry is taken for the rest of
#   the day. 0 = off (the default; the baseline stays naked).
#
#   It is a TRIGGER, not a guarantee: because it is evaluated at closes, the
#   realised day loss lands beyond the level by about one bar's adverse move
#   plus the exit's charges. A live MTM guard behaves identically. Size with
#   that headroom.
#
#   The figure is ABSOLUTE RUPEES at the configured lot count, not per-lot —
#   it is the number you would type into a live risk guard. Change lots and
#   the cap must be rescaled by hand; that is deliberate, because a per-lot
#   cap silently changes meaning when size changes.
#
# SURFACES (5 files, 6 edits)
#   Backtest.jsx       panel field · buildConfig key · describeConfig chip
#   SweepBuilder.jsx   vet_daycap axis
#   BacktestQueue.jsx  param line
#   RunComparison.jsx  comparison row
#
# DOCTRINE: assert-anchored, idempotent, staged esbuild check, dual-tree.
#
# USAGE
#   cd <repo root>
#   python3 apply_vet_daily_mtm_cap_20260827.py --dry-run
#   python3 apply_vet_daily_mtm_cap_20260827.py

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.getcwd()
TREES = [(os.path.join(REPO, "frontend", "src"), "frontend"),
         (os.path.join(REPO, "desktop", "src-tauri", "frontend", "src"), "desktop-fe")]
BT = os.path.join("pages", "Backtest.jsx")
SW = os.path.join("pages", "backtest", "SweepBuilder.jsx")
QU = os.path.join("pages", "backtest", "BacktestQueue.jsx")
RC = os.path.join("pages", "backtest", "RunComparison.jsx")


def die(m):
    print(f"\nABORT: {m}\nNothing was written.")
    sys.exit(1)


def one(t, needle, lbl, want=1):
    n = t.count(needle)
    if n != want:
        die(f"anchor count {n}, expected {want} [{lbl}]: {needle.strip()[:100]}")


# ── Backtest.jsx ────────────────────────────────────────────────────────
BT_STATE_OLD = ('  const [vetMaxTrades, setVetMaxTrades] = useState('
                'vetSaved.maxTrades ?? 0);\n')
BT_STATE_NEW = BT_STATE_OLD + (
    '  const [vetDayCap, setVetDayCap] = useState(vetSaved.dayCap ?? 0);'
    '   // ── DAILY_MTM_CAP ──\n')

BT_PERSIST_OLD = 'maxTrades: vetMaxTrades,'
BT_PERSIST_NEW = 'maxTrades: vetMaxTrades, dayCap: vetDayCap,'

BT_DEPS_OLD = 'vetEodSquare, vetExitTime, vetEntryCutoff, vetMaxTrades,'
BT_DEPS_NEW = 'vetEodSquare, vetExitTime, vetEntryCutoff, vetMaxTrades, vetDayCap,'

BT_CFG_OLD = ('        max_trades_per_day: Number(vetMaxTrades) || 0,\n')
BT_CFG_NEW = (
    '        max_trades_per_day: Number(vetMaxTrades) || 0,\n'
    '        max_daily_mtm_loss: Math.abs(Number(vetDayCap) || 0),\n')

BT_DESC_OLD = ('    if (Number(cfg.max_trades_per_day) > 0) add("Cap", '
               '`${cfg.max_trades_per_day}/day`);\n')
BT_DESC_NEW = BT_DESC_OLD + (
    '    if (Number(cfg.max_daily_mtm_loss) > 0) add("Daily MTM stop", '
    '`₹${Number(cfg.max_daily_mtm_loss).toLocaleString("en-IN")}/day`);\n')

# NOTE: the label alone is NOT unique — another strategy's panel uses the
# same wording. Anchor on the VET state variable, which is.
BT_FIELD_OLD = ('<Field label="Max trades / day (0 = off)"><input type="number" '
                'style={inputStyle} value={vetMaxTrades}')
BT_FIELD_NEW = (
    '                <Field label="Max daily MTM loss ₹ (0 = off)"><input type="number" '
    'style={inputStyle} value={vetDayCap} onChange={(e) => setVetDayCap(Number(e.target.value))} '
    'title="Per-SESSION loss limit in rupees at the configured lot count (NOT per lot). '
    'Checked at every timeframe close on realised P&L plus the open leg\'s mark; on breach the '
    'position is flattened at that bar and no further entry is taken that day. A TRIGGER, not a '
    'guarantee — evaluated at closes, so the realised loss overshoots the level by about one '
    'bar\'s move plus charges, exactly as a live MTM guard would. 0 = off." /></Field>\n'
    '                ' + BT_FIELD_OLD)


def edit_bt(t):
    if "vetDayCap" in t:
        return t, 0
    if "vetMaxTrades" not in t:
        die("VET panel not found — apply apply_vet_v1_frontend_20260826.py first")
    for needle, lbl in ((BT_STATE_OLD, "state"), (BT_PERSIST_OLD, "persist"),
                        (BT_CFG_OLD, "buildConfig"),
                        (BT_DESC_OLD, "describeConfig"), (BT_FIELD_OLD, "panel field")):
        one(t, needle, "Backtest:" + lbl)
    # ── TWO dep arrays carry this list: the localStorage persist effect AND
    # the buildConfig useCallback. BOTH must gain vetDayCap in this same
    # commit — a missing entry is an exhaustive-deps warning, and CI builds
    # with CI=true, where warnings are ERRORS. Assert exactly 2, patch both.
    one(t, BT_DEPS_OLD, "Backtest:dep arrays", want=2)
    t = t.replace(BT_STATE_OLD, BT_STATE_NEW, 1)
    t = t.replace(BT_PERSIST_OLD, BT_PERSIST_NEW, 1)
    t = t.replace(BT_DEPS_OLD, BT_DEPS_NEW)
    t = t.replace(BT_CFG_OLD, BT_CFG_NEW, 1)
    t = t.replace(BT_DESC_OLD, BT_DESC_NEW, 1)
    t = t.replace(BT_FIELD_OLD, BT_FIELD_NEW, 1)
    return t, 7


# ── SweepBuilder.jsx ────────────────────────────────────────────────────
SW_OLD = '  { key: "vet_sl", label: "VET SL % of premium", strategies: [VET],\n'
SW_NEW = ('''  // ── DAILY_MTM_CAP ── absolute rupees per session at the configured lot
  // count. The natural grid is a few multiples of a typical bad day, not a
  // fine sweep: too tight and it fires on ordinary noise, removing good days
  // along with bad ones.
  { key: "vet_daycap", label: "VET max daily MTM loss (₹)", strategies: [VET],
    hint: "0, 60000, 90000, 120000, 150000", parse: _num,
    apply: (c, v) => { c.max_daily_mtm_loss = Math.abs(v); },
    fmt: (v) => (v > 0 ? `day≤₹${Math.abs(v)}` : "no day cap") },
''' + SW_OLD)


def edit_sw(t):
    if "vet_daycap" in t:
        return t, 0
    one(t, SW_OLD, "Sweep:vet_sl axis")
    return t.replace(SW_OLD, SW_NEW, 1), 1


# ── BacktestQueue.jsx ───────────────────────────────────────────────────
QU_OLD = ('    if (Number(cfg.max_trades_per_day) > 0) '
          'p.push(`cap${cfg.max_trades_per_day}/day`);\n')
QU_NEW = QU_OLD + ('    if (Number(cfg.max_daily_mtm_loss) > 0) '
                   'p.push(`day≤₹${Math.round(Number(cfg.max_daily_mtm_loss) / 1000)}k`);\n')


def edit_qu(t):
    if "max_daily_mtm_loss" in t:
        return t, 0
    one(t, QU_OLD, "Queue:paramLine cap")
    return t.replace(QU_OLD, QU_NEW, 1), 1


# ── RunComparison.jsx ───────────────────────────────────────────────────
RC_OLD = ('  { key: "vet_cap",      label: "VET trades/day", get: (r) => '
          '(r.config?.trend_len != null && r.config?.range_len != null && '
          'Number(r.config?.max_trades_per_day) > 0) ? r.config.max_trades_per_day : null },\n')
RC_NEW = RC_OLD + ('  { key: "vet_daycap",   label: "VET daily MTM stop", get: (r) => '
                   '(r.config?.trend_len != null && r.config?.range_len != null && '
                   'Number(r.config?.max_daily_mtm_loss) > 0) ? '
                   '`₹${Number(r.config.max_daily_mtm_loss).toLocaleString("en-IN")}` : null },\n')


def edit_rc(t):
    if "vet_daycap" in t:
        return t, 0
    one(t, RC_OLD, "Compare:vet_cap row")
    return t.replace(RC_OLD, RC_NEW, 1), 1


EDITORS = [(BT, edit_bt), (SW, edit_sw), (QU, edit_qu), (RC, edit_rc)]


def esbuild(canary):
    cands = []
    loc = os.path.join(REPO, "frontend", "node_modules", ".bin", "esbuild")
    if os.path.isfile(loc) and os.access(loc, os.X_OK):
        cands.append(([loc], "node_modules"))
    p = shutil.which("esbuild")
    if p:
        cands.append(([p], "PATH"))
    npx = shutil.which("npx")
    if npx:
        cands += [([npx, "--no", "esbuild"], "npx"),
                  ([npx, "--no-install", "esbuild"], "npx7")]
    for c, w in cands:
        try:
            if subprocess.run(c + ["--log-level=silent", canary], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=90).returncode == 0:
                return c, w
        except Exception:
            pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-jsx-check", action="store_true")
    a = ap.parse_args()
    writes, notes = {}, []
    for root, label in TREES:
        if not os.path.isdir(root):
            notes.append(f"[{label}] NOT PRESENT — skipped")
            continue
        for rel, fn in EDITORS:
            path = os.path.join(root, rel)
            if not os.path.isfile(path):
                die(f"[{label}] missing {path}")
            src = open(path).read()
            out, n = fn(src)
            if n == 0:
                notes.append(f"[{label}] SKIP (already wired): {rel}")
            else:
                writes[path] = out
                notes.append(f"[{label}] EDIT ({n}): {rel}")
    print("── PLAN ─────────────────────────────────────────────────────")
    for n in notes:
        print("  " + n)
    if not writes:
        print("\nNothing to do.")
        return
    print("\n── JSX SYNTAX CHECK ─────────────────────────────────────────")
    if a.skip_jsx_check:
        print("  skipped by request")
    else:
        tmp = tempfile.mkdtemp(prefix="vet_cap_")
        try:
            can = os.path.join(tmp, "c.jsx")
            open(can, "w").write("const A = () => <div>{1}</div>;\n")
            cmd, where = esbuild(can)
            if cmd is None:
                print("  !! no working esbuild — check SKIPPED (not an error)")
            else:
                print(f"  esbuild via {where}")
                for i, (dest, body) in enumerate(writes.items()):
                    st = os.path.join(tmp, f"s{i}.jsx")
                    open(st, "w").write(body)
                    r = subprocess.run(cmd + ["--log-level=warning", st],
                                       capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=120)
                    if r.returncode != 0:
                        die(f"esbuild FAILED for {dest}:\n{r.stderr[:1500]}")
                print(f"  {len(writes)} file(s) parse clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if a.dry_run:
        print("\n--dry-run: no files written.")
        return
    print("\n── WRITE ────────────────────────────────────────────────────")
    for dest, body in writes.items():
        open(dest, "w").write(body)
        print("  wrote " + os.path.relpath(dest, REPO))
    print("\nDONE. Rebuild the frontend. New field: 'Max daily MTM loss ₹ (0 = off)'")
    print("Sweep axis: 'VET max daily MTM loss (₹)'")


if __name__ == "__main__":
    main()
