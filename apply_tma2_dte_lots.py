#!/usr/bin/env python3
# apply_tma2_dte_lots.py — fence TMA2_DTE_LOTS_20260922
#
# TMA_V2 BACKTEST: "DTE × lots (0 = skip)" — the same per-DTE lot multiplier
# knob ORB / BRK / TSG already have (app.backtest.engine.dte_lots, shared
# module, unchanged). DTE = trading sessions from the ENTRY day to the front
# expiry, from the corpus calendar — the same number the Sessions-to-Expiry
# breakdown shows. 0 = no NEW entries that day (carried positions are still
# advanced and exited as before); the hedge scales with the sold leg.
# Default OFF — unset / blank is row-for-row and diag-for-diag identical to
# the current runner, and this script PROVES that on your machine before it
# keeps anything. Paper/live untouched.
#
#   cd /Users/anbu/dev/scalp-app && python3 apply_tma2_dte_lots.py
#
# RUN ONLY THIS SCRIPT. Requires the TMA_REENTRY revert to have been applied
# first (the runner must be the GitHub-main original, checked by sha256).
#
# Writes (each with a .bak-TMA2_DTE_LOTS_20260922 backup, all-or-nothing):
#   backend/app/backtest/tma/backtest_tma_v2_runner.py   (8 anchored edits)
#   backend/app/backtest/tma/test_tma_v2_dte_lots.py     (new, 27 checks)
#   frontend/src/pages/Backtest.jsx                      (field next to
#       "Max loss/trade", config key, LS persist, run-card chip)
#   frontend/src/pages/backtest/SweepBuilder.jsx         (one axis)
#   + the desktop/src-tauri build copies when they exist.
# Flags: --repo PATH  --allow-dirty  --skip-tests (not recommended)
# The test step builds a ~250 MB temp corpus and takes 1–3 minutes.

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

RUNNER_SHA = "c6c6185038e18324b11b3f29edc83c23969a6f22562543ad36ebfddb46d59799"
TMA = "app/backtest/tma"
RUNNER, TESTF = f"{TMA}/backtest_tma_v2_runner.py", f"{TMA}/test_tma_v2_dte_lots.py"
TEST_FILE = """
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
"""


def unpack(b: str) -> str:
    return zlib.decompress(base64.b64decode("".join(b.split()))).decode()


# ── anchored edits (shared with the sandbox build) ────────────────────────
FENCE = "TMA2_DTE_LOTS_20260922"

# ── runner edits (anchored; each must match exactly once) ────────────────
RUNNER_EDITS = [
("""# ── POSITIONAL / NEG_MTM_EOD_CUT / EXPIRY_INTRINSIC ────────────────────
""",
"""# ── TMA2_DTE_LOTS_20260922 ── per-DTE lot multiplier, same knob as ORB /
#   BRK / TSG (app.backtest.engine.dte_lots): dte_lot_mult = {dte: mult};
#   absent DTE → ×1; 0 → no NEW entries that day; lots = max(1,
#   round(lots × mult)). DTE = trading sessions from the ENTRY day to the
#   front expiry, from the corpus calendar — the exact number the
#   Sessions-to-Expiry breakdown shows for that trade. The hedge scales by
#   the same multiplier (spread stays a spread). Carried positions are
#   advanced / exited on a skipped day exactly as before: only the signal
#   loop is gated. Unset / {} = byte-identical to before. Fail-open when the
#   calendar is empty (base lots, counted in dte_unknown_days).
#
# ── POSITIONAL / NEG_MTM_EOD_CUT / EXPIRY_INTRINSIC ────────────────────
"""),
("""    max_loss_rs = max(0.0, float(cfg.get("max_loss_per_trade", 0) or 0))
""",
"""    max_loss_rs = max(0.0, float(cfg.get("max_loss_per_trade", 0) or 0))
    # ── TMA2_DTE_LOTS_20260922 ──
    from app.backtest.engine.dte_lots import (
        parse_dte_lot_mult as _parse_dlm, lots_for_day as _lots_for_day,
        dte_for_day as _dte_for_day)
    dte_lot_mult = _parse_dlm(cfg.get("dte_lot_mult"))
"""),
("""    diag["max_loss_per_trade"] = max_loss_rs if max_loss_rs > 0 else "OFF"
""",
"""    diag["max_loss_per_trade"] = max_loss_rs if max_loss_rs > 0 else "OFF"
    # ── TMA2_DTE_LOTS_20260922 ── keys only when the knob is set (an OFF
    # run's diag stays key-for-key identical)
    _dte_cal: List[str] = []
    if dte_lot_mult:
        diag["dte_lot_mult"] = {str(k): v for k, v in dte_lot_mult.items()}
        diag["dte_skipped_days"] = 0
        diag["dte_scaled_days"] = 0
        diag["dte_unknown_days"] = 0
        diag["skipped_dte"] = 0
        from app.backtest.repo.trading_calendar import trading_dates as _td
        _dte_cal = _td(underlying, db_path)
        if not _dte_cal:
            write_audit_log(f"[BACKTEST][{strategy_id}] dte_lot_mult set but "
                            f"the corpus calendar is empty — base lots used")
"""),
("""        meta = {c["tradingsymbol"]: c for c in week}
        by_side = {"CE": [c["tradingsymbol"] for c in week
""",
"""        meta = {c["tradingsymbol"]: c for c in week}
        # ── TMA2_DTE_LOTS_20260922 ── today's lots for NEW entries
        day_lots_m, day_lots_h = main_cfg["lots"], hedge_cfg["lots"]
        if dte_lot_mult:
            _dte = (_dte_for_day(_dte_cal, d.isoformat(), want_expiry)
                    if _dte_cal else None)
            day_lots_m, _tag = _lots_for_day(main_cfg["lots"], dte_lot_mult,
                                             _dte)
            day_lots_h, _ = _lots_for_day(hedge_cfg["lots"], dte_lot_mult,
                                          _dte)
            if _tag == "skip":
                diag["dte_skipped_days"] += 1
            elif _tag == "scaled":
                diag["dte_scaled_days"] += 1
            elif _tag == "unknown":
                diag["dte_unknown_days"] += 1
        by_side = {"CE": [c["tradingsymbol"] for c in week
"""),
("""        for sig in sorted(sig_res["signals"], key=lambda x: x["ts"]):
            slot = "S1"
            if sig["ts"] < pos_busy[slot]:
""",
"""        for sig in sorted(sig_res["signals"], key=lambda x: x["ts"]):
            slot = "S1"
            if day_lots_m <= 0:   # ── TMA2_DTE_LOTS_20260922 ── 0 = skip day
                diag["skipped_dte"] += 1
                continue
            if sig["ts"] < pos_busy[slot]:
"""),
("""                qty_m = int(main_cfg["lots"]) * LOT_SIZE
""",
"""                qty_m = int(day_lots_m) * LOT_SIZE   # ── TMA2_DTE_LOTS ──
"""),
("""                   "symbol": sel["symbol"], "lots": main_cfg["lots"],
""",
"""                   "symbol": sel["symbol"], "lots": day_lots_m,   # ── TMA2_DTE_LOTS ──
"""),
("""                            "h_lots": hedge_cfg["lots"],
""",
"""                            "h_lots": day_lots_h,   # ── TMA2_DTE_LOTS ──
"""),
]

# ── Backtest.jsx edits ───────────────────────────────────────────────────
JSX_EDITS = [
("""  const [tma2MaxLoss, setTma2MaxLoss] = useState(tma2Saved.maxLoss ?? 0);
""",
"""  const [tma2MaxLoss, setTma2MaxLoss] = useState(tma2Saved.maxLoss ?? 0);
  const [tma2DteMult, setTma2DteMult] = useState(tma2Saved.dteMult ?? "");   // ── TMA2_DTE_LOTS_20260922 ──
"""),
("maxLoss: tma2MaxLoss, tradeMode:", "maxLoss: tma2MaxLoss, dteMult: tma2DteMult, tradeMode:"),
("""        max_loss_per_trade: Number(tma2MaxLoss) || 0,
""",
"""        max_loss_per_trade: Number(tma2MaxLoss) || 0,
        dte_lot_mult: parseDteLotMult(tma2DteMult),   // ── TMA2_DTE_LOTS_20260922 ──
"""),
("""    if (Number(cfg.sl_streak_count) > 0) add("SL brake", `${cfg.sl_streak_count}SL/${cfg.sl_streak_cooldown_days || 5}d`);   // ── SL_STREAK_COOLDOWN ──
""",
"""    if (Number(cfg.sl_streak_count) > 0) add("SL brake", `${cfg.sl_streak_count}SL/${cfg.sl_streak_cooldown_days || 5}d`);   // ── SL_STREAK_COOLDOWN ──
    if (dteMultChip(cfg)) add("DTE lots", dteMultChip(cfg));   // ── TMA2_DTE_LOTS_20260922 ──
"""),
("""                {/* ── MAX_LOSS_PER_TRADE ── */}
                <Field label="Max loss/trade ₹ (0=off)">
""",
"""                {/* ── TMA2_DTE_LOTS_20260922 ── same knob as ORB / BRK */}
                <Field label="DTE × lots (0 = skip)"><input type="text" style={{ ...inputStyle, width: 170 }} value={tma2DteMult} onChange={(e) => setTma2DteMult(e.target.value)} placeholder="e.g. 0:0, 4:2" title="Per-DTE lot multiplier — DTE = trading SESSIONS from the ENTRY day to expiry (the Sessions-to-Expiry breakdown). Format 0:1.5, 1:0, 4:2 · absent DTE = ×1 · 0 = no new entries that day · lots = max(1, round(lots × mult)); the hedge scales by the same multiplier. Carried positions are still advanced and exited on skipped days. Blank = off." /></Field>
                {/* ── MAX_LOSS_PER_TRADE ── */}
                <Field label="Max loss/trade ₹ (0=off)">
"""),
]
# two dep arrays (LS persist + buildConfig): tma2MaxLoss → tma2MaxLoss, tma2DteMult
DEP_OLD = "tma2StreakK, tma2CdDays, tma2MaxLoss, tma2TradeMode,"
DEP_NEW = "tma2StreakK, tma2CdDays, tma2MaxLoss, tma2DteMult, tma2TradeMode,"

# ── SweepBuilder axis (tokens can't hold commas → "+" joins pairs) ───────
SWEEP_ANCHOR = "  // ── XOVER_TOGGLE ── 13/89 crossover exit on/off\n"
SWEEP_INSERT = '''  // ── TMA2_DTE_LOTS_20260922 ── per-DTE lot multiplier. Sweep tokens are
  // comma-separated, so pairs inside ONE token are joined with "+":
  //   OFF, 0:0, 0:0+3:0, 4:2, 1:1.5+2:1.5
  { key: "tma2_dte_lots", label: "DTE × lots (0 = skip)", strategies: [TMA2],
    hint: "OFF, 0:0, 0:0+3:0, 4:2", parse: (tok) => {
      const t = tok.trim().toUpperCase();
      if (t === "OFF" || t === "") return { v: {} };
      const out = {};
      for (const part of t.split("+")) {
        const m = part.trim().match(/^(\\d+)(?:DTE)?:([0-9]*\\.?[0-9]+)$/);
        if (!m) return { err: `"${tok}": use OFF or dte:mult pairs joined by + (e.g. 0:0+3:0)` };
        out[Number(m[1])] = Math.max(0, Number(m[2]));
      }
      return { v: out };
    },
    apply: (c, v) => { c.dte_lot_mult = v; },
    fmt: (v) => { const k = Object.keys(v).sort((a, b) => a - b); return k.length ? k.map((d) => `${d}DTE×${v[d]}`).join(" ") : "dteOFF"; } },
'''


def apply_pairs(text, pairs, what):
    for old, new in pairs:
        n = text.count(old)
        assert n == 1, f"{what}: anchor x{n}: {old[:60]!r}"
        text = text.replace(old, new)
    return text


def patch_runner(t):
    return apply_pairs(t, RUNNER_EDITS, "runner")


def patch_backtest(t):
    t = apply_pairs(t, JSX_EDITS, "Backtest.jsx")
    assert t.count(DEP_OLD) == 2, f"Backtest.jsx dep arrays x{t.count(DEP_OLD)} (expected 2)"
    return t.replace(DEP_OLD, DEP_NEW)


def patch_sweep(t):
    assert t.count(SWEEP_ANCHOR) == 1, "SweepBuilder.jsx anchor"
    return t.replace(SWEEP_ANCHOR, SWEEP_INSERT + SWEEP_ANCHOR)


def die(msg: str) -> None:
    print(f"\nABORT — {msg}\nNothing was written.")
    sys.exit(1)


def git_dirty(repo: Path, rel: str) -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=repo,
                             capture_output=True, text=True, timeout=20)
        return bool(out.stdout.strip()) if out.returncode == 0 else False
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    be, fe = repo / "backend", repo / "frontend"
    files = {"runner": be / RUNNER, "bt": fe / "src/pages/Backtest.jsx",
             "sweep": fe / "src/pages/backtest/SweepBuilder.jsx"}
    if not all(p.exists() for p in files.values()):
        die(f"{repo} is not the scalp-app repo root (run from /Users/anbu/dev/scalp-app or pass --repo)")
    if not (be / "app/backtest/engine/dte_lots.py").exists():
        die("app/backtest/engine/dte_lots.py (DTE_LOT_MULT_20260911) is missing — this patch reuses it")

    marks = [k for k, p in files.items() if FENCE in p.read_text()]
    if (be / TESTF).exists():
        marks.append("test")
    if marks:
        if len(marks) == 4:
            print(f"{FENCE} is already applied — nothing to do.")
            return
        die("MIXED STATE — the fence is in some files but not all: " + ", ".join(marks)
            + "\nRestore them (git checkout / delete the test file) and re-run.")
    runner_txt = files["runner"].read_text()
    sha = hashlib.sha256(runner_txt.encode()).hexdigest()
    if sha != RUNNER_SHA:
        hint = ("TMA_REENTRY_20260921 is still applied — run revert_tma_reentry.py first."
                if "TMA_REENTRY_20260921" in runner_txt else
                "A local or unpushed edit is in the way. Push it (or tell Claude) so the patch can be rebuilt on it.")
        die("backtest_tma_v2_runner.py differs from the GitHub-main version this patch was built on\n"
            f"  expected sha256 {RUNNER_SHA[:16]}…  found {sha[:16]}…\n  " + hint)
    if not a.allow_dirty:
        dirty = [str(p.relative_to(repo)) for p in files.values() if git_dirty(repo, str(p.relative_to(repo)))]
        if dirty:
            die("uncommitted changes in files this patch edits (an M is a stop sign):\n  " + "\n  ".join(dirty)
                + "\nCommit/stash them, or re-run with --allow-dirty if they are yours and intended.")

    new = {}
    try:
        new[files["runner"]] = patch_runner(runner_txt)
        new[files["bt"]] = patch_backtest(files["bt"].read_text())
        new[files["sweep"]] = patch_sweep(files["sweep"].read_text())
    except AssertionError as e:
        die(f"anchor problem: {e}")
    new[be / TESTF] = unpack(TEST_FILE)
    # build copies (gitignored; refreshed by the build anyway)
    mbe, mfe = repo / "desktop/src-tauri/backend", repo / "desktop/src-tauri/frontend"
    if (mbe / TMA).is_dir():
        new[mbe / RUNNER] = new[files["runner"]]
        new[mbe / TESTF] = new[be / TESTF]
    for rel, fn in (("src/pages/Backtest.jsx", patch_backtest), ("src/pages/backtest/SweepBuilder.jsx", patch_sweep)):
        p = mfe / rel
        if p.exists() and FENCE not in p.read_text():
            try:
                new[p] = fn(p.read_text())
            except AssertionError:
                print(f"note: build copy {p.relative_to(repo)} skipped (anchor differs) — the next build refreshes it")

    with tempfile.TemporaryDirectory() as td:
        for p, txt in new.items():
            if p.suffix == ".py":
                t = Path(td) / p.name
                t.write_text(txt)
                try:
                    py_compile.compile(str(t), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {p.name}: {e}")

    backups, created = [], []

    def rollback() -> None:
        for p, b in backups:
            shutil.copy2(b, p)
            b.unlink()
        for p in created:
            p.unlink(missing_ok=True)

    try:
        for p, txt in new.items():
            if p.exists():
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append((p, b))
            else:
                created.append(p)
            p.write_text(txt)
    except Exception as e:                                   # noqa: BLE001
        rollback()
        die(f"write failed ({e}) — rolled back")
    print(f"wrote {len(new)} files")

    if not a.skip_tests:
        print("running test_tma_v2_dte_lots.py (builds a temp corpus, 1–3 min)…")
        env = dict(os.environ, PYTHONPATH=str(be))
        r = subprocess.run([sys.executable, str(be / TESTF)], cwd=be, env=env, capture_output=True, text=True)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-12:])
        if r.returncode != 0 or "PASSED" not in r.stdout or "OFF ≡ ORIGINAL rows" not in r.stdout:
            rollback()
            die("behaviour suite FAILED — everything rolled back. Send Claude this:\n" + tail)
        print(tail.splitlines()[-1])
        r2 = subprocess.run([sys.executable, str(be / TMA / "test_tma_v2_engine.py")], cwd=be, env=env,
                            capture_output=True, text=True)
        if r2.returncode != 0:
            rollback()
            die("existing suite test_tma_v2_engine.py FAILED after the patch — rolled back:\n"
                + "\n".join((r2.stdout + r2.stderr).splitlines()[-8:]))
        print((r2.stdout.strip().splitlines() or ["ok"])[-1])

    esb = fe / "node_modules/.bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if esb.exists():
        for k in ("bt", "sweep"):
            r = subprocess.run([str(esb), str(files[k]), "--loader:.jsx=jsx", "--log-level=error"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                rollback()
                die(f"esbuild rejected {files[k].name} — rolled back:\n{r.stderr[-600:]}")
        print("esbuild: 2 JSX files parse")

    print(f"""
{FENCE} applied.
  • Backend change is live for the next backtest you queue (restart the backend if it is running).
  • Rebuild / reload the frontend: the field sits next to "Max loss/trade" in the TMA_V2 form,
    and "DTE × lots (0 = skip)" is a TMA_V2 axis in the sweep builder (pairs joined by "+").
  • .bak-{FENCE} files are the rollback; keep them out of git.
""")


if __name__ == "__main__":
    main()
