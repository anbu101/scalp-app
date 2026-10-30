#!/usr/bin/env python3
"""
apply_off_quiet.py  ── OFF_QUIET_20261030 ──

Strategies set to OFF stop paging people about data hiccups they can't act on.

  * SCALP_V1: an OFF strategy's sell signal is dropped BEFORE the ATM skew
    gate (the router dropped it anyway — MODE_OFF → DROP), so no REST quote is
    made and a quote timeout can't raise "INTERNAL ERROR". The gate's daily
    error alert now pages Telegram only in LIVE / PAPER+LIVE (OFF used to page,
    because the check was "mode != PAPER").
  * EMC_V1: while OFF with no open position, alerts go to the audit log only
    (position alerts — stale rows, sell/close failures — always stay loud).
    Alerts now carry the real mode tag (they were always tagged LIVE).
    A single spot-candle fetch timeout is audit-only in every mode;
    SPOT_MISSING still alerts if the minute really stays absent.

Run from the repo root:

    python3 apply_off_quiet.py --check
    python3 apply_off_quiet.py
    ./desktop/build-scalp.sh both      (non-trading hours)
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "OFF_QUIET_20261030"
PARENT = "FLEET_MODES_20260923"
PARENT_FILE = "backend/app/engine/signal_router.py"
NEW_FILES = {'backend/app/risk/test_off_quiet.py': '# backend/app/risk/test_off_quiet.py\n#\n# ── OFF_QUIET_20261030 ── a strategy set to OFF that holds no position never\n# raises bell/Telegram alerts for data hiccups it can\'t act on; real modes\n# keep their alerts and now carry the right PAPER/LIVE tag.\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/risk/test_off_quiet.py\n\nimport sys\nimport types\nfrom pathlib import Path\n\nFAILS = []\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if not cond:\n        FAILS.append(name)\n\n\nAPP = Path(__file__).resolve().parents[1]\n\nprint("── 1. EMC: OFF + flat → audit only")\nimport app.engine.emc.emc_manager as MG\nALERTS = []\nMG.record_alert = lambda code, msg, **k: ALERTS.append((code, k.get("mode"), k.get("severity")))\n\n\ndef mgr(mode, pos=None):\n    m = MG.EmcManager.__new__(MG.EmcManager)\n    m.pos = pos\n    m.cfg = lambda: {"trade_execution_mode": mode}\n    return m\n\n\nALERTS.clear(); mgr("OFF")._alert("SPOT_MISSING", "no spot candle", "error")\ncheck("OFF + flat: data alert not raised", ALERTS == [], ALERTS)\nALERTS.clear(); mgr("OFF")._alert("CHAIN_FAIL", "x", "error")\ncheck("OFF + flat: chain failure not raised", ALERTS == [])\nALERTS.clear(); mgr("OFF", pos=object())._alert("SPOT_MISSING", "x", "error")\ncheck("OFF with an open position: still alerts", ALERTS and ALERTS[0][0] == "SPOT_MISSING", ALERTS)\nALERTS.clear(); mgr("OFF")._alert("STALE_ROW", "old row", "error")\ncheck("OFF: stale-row (position bookkeeping) still alerts", ALERTS and ALERTS[0][0] == "STALE_ROW")\nALERTS.clear(); mgr("OFF")._alert("SELL_FAIL", "x", "critical")\ncheck("OFF: sell failure always alerts", bool(ALERTS))\n\nprint("── 2. EMC: correct mode tag (was always LIVE)")\nALERTS.clear(); mgr("PAPER")._alert("SPOT_MISSING", "x", "error")\ncheck("PAPER tagged paper", ALERTS and ALERTS[0][1] == "paper", ALERTS)\nALERTS.clear(); mgr("LIVE")._alert("SPOT_MISSING", "x", "error")\ncheck("LIVE tagged live", ALERTS and ALERTS[0][1] == "live", ALERTS)\nALERTS.clear(); mgr("PAPER_LIVE")._alert("SPOT_MISSING", "x", "error")\ncheck("PAPER+LIVE tagged live", ALERTS and ALERTS[0][1] == "live", ALERTS)\nALERTS.clear(); mgr("OFF", pos=object())._alert("SPOT_MISSING", "x", "error")\ncheck("OFF with position tagged off", ALERTS and ALERTS[0][1] == "off", ALERTS)\n\nprint("── 3. EMC: one transient spot-fetch failure no longer pages")\nsrc = (APP / "engine/emc/emc_engine.py").read_text()\ncheck("SPOT_FETCH is audit-only", \'"SPOT_FETCH"\' not in src and "[EMC][SPOT_FETCH]" in src)\ncheck("SPOT_MISSING (data really late) still alerts", \'"SPOT_MISSING"\' in src)\n\nprint("── 4. SCALP_V1 skew gate")\ntry:\n    import app.marketdata.zerodha_tick_engine as TE\nexcept ModuleNotFoundError as e:\n    TE = None\n    print(f"  SKIP  tick engine import ({e}) — run on the build Mac")\nif TE is not None:\n    TG, BELL = [], []\n    import app.api.telegram_api as T\n    import app.event_bus.inapp_events as IE\n    T.notify_critical = lambda d: TG.append(d)\n    IE.record_alert = lambda code, msg, **k: BELL.append((code, k.get("mode")))\n\n    def gate_err(mode):\n        TG.clear(); BELL.clear()\n        TE._strategy_mode = lambda sid: mode\n        fake = types.SimpleNamespace()\n        TE.ZerodhaTickEngine._skew_error_block(fake, "[SCALP_V1][SKEW] ATM quote failed (ReadTimeout)")\n        return list(TG), list(BELL)\n\n    tg, bell = gate_err("OFF")\n    check("OFF: no Telegram page (was paged: OFF != PAPER)", tg == [], tg)\n    tg, bell = gate_err("PAPER")\n    check("PAPER: bell only", tg == [] and bell and bell[0][1] == "paper", (tg, bell))\n    tg, bell = gate_err("LIVE")\n    check("LIVE: bell + Telegram", bool(tg) and bell and bell[0][1] == "live", (tg, bell))\n    tg, bell = gate_err("PAPER_LIVE")\n    check("PAPER+LIVE: bell + Telegram", bool(tg), tg)\n\n    import app.risk.execution_modes as XM\n    orig = XM.resolve_execution_plan\n    XM.resolve_execution_plan = lambda sid: ("OFF", False)\n    check("helper: clean OFF read → off", TE._strategy_is_off("SCALP_V1") is True)\n    XM.resolve_execution_plan = lambda sid: ("PAPER", True)\n    check("helper: degraded read → not off (gate still runs)", TE._strategy_is_off("SCALP_V1") is False)\n    XM.resolve_execution_plan = lambda sid: ("PAPER", False)\n    check("helper: PAPER → not off", TE._strategy_is_off("SCALP_V1") is False)\n    XM.resolve_execution_plan = orig\n\n    tsrc = (APP / "marketdata/zerodha_tick_engine.py").read_text()\n    i_off = tsrc.find("_strategy_is_off(self.strategy_id)")\n    i_gate = tsrc.find("if not self._atm_skew_ok(symbol):")\n    check("OFF drop sits before the skew gate\'s REST quote", 0 < i_off < i_gate, (i_off, i_gate))\n\nprint()\nif FAILS:\n    print(f"  {len(FAILS)} FAILED: {FAILS}")\n    sys.exit(1)\nprint("  ALL CHECKS PASSED")\n'}
EDITS = {'backend/app/engine/emc/emc_manager.py': [('    def _alert(self, code, msg, severity="warning"):\n        write_audit_log(f"[EMC][{code}] {msg}")\n        try:\n            record_alert(code, msg, severity=severity, strategy_id=STRATEGY_ID)\n        except Exception:\n            pass\n', '    # ── OFF_QUIET_20261030 ── an OFF strategy with no position has nothing to\n    # act on: its alerts stay in the audit log. Position bookkeeping alerts\n    # are always raised. Alerts carry the real mode (record_alert defaults\n    # to "live", so every EMC alert used to show a LIVE tag).\n    _LOUD_WHEN_OFF = frozenset({\n        "STALE_ROW", "STALE_FAIL", "SELL_FAIL", "CLOSE_ROW_FAIL", "BROKER_UNREADABLE",\n        "BROKER_FLAT", "DOUBLE_ENTRY", "PERSIST_DOWN", "ROW_FAIL",\n    })\n\n    def _alert(self, code, msg, severity="warning"):\n        write_audit_log(f"[EMC][{code}] {msg}")\n        try:\n            m = str(self.mode() or "LIVE").upper()\n        except Exception:\n            m = "LIVE"                                  # unknown → loud\n        if m == "OFF" and getattr(self, "pos", None) is None and code not in self._LOUD_WHEN_OFF:\n            write_audit_log(f"[EMC][{code}][OFF_QUIET] strategy OFF and flat — kept out of the bell")\n            return\n        try:\n            record_alert(code, msg, severity=severity, strategy_id=STRATEGY_ID,\n                         mode="live" if m in ("LIVE", "PAPER_LIVE") else m.lower())\n        except Exception:\n            pass\n')], 'backend/app/engine/emc/emc_engine.py': [('                self._alert_once(f"spotfetch{k // 15}", "SPOT_FETCH", f"{e!r}", "error")\n', '                # ── OFF_QUIET_20261030 ── one REST hiccup is not actionable;\n                # SPOT_MISSING below alerts if the minute really stays absent.\n                write_audit_log(f"[EMC][SPOT_FETCH] {e!r} — will retry; SPOT_MISSING alerts if data stays late")\n')], 'backend/app/marketdata/zerodha_tick_engine.py': [('from app.risk.strategy_max_loss_guard import _strategy_mode\n', 'from app.risk.strategy_max_loss_guard import _strategy_mode\n\n\ndef _strategy_is_off(strategy_id: str) -> bool:\n    """── OFF_QUIET_20261030 ── True only on a CLEAN read of mode OFF; a degraded\n    read is never treated as OFF (the gate then runs exactly as before)."""\n    try:\n        from app.risk.execution_modes import resolve_execution_plan\n        mode, degraded = resolve_execution_plan(strategy_id)\n        return (not degraded) and mode == "OFF"\n    except Exception:\n        return False\n'), ('                    if signal.is_sell and is_option:\n                        if not self._atm_skew_ok(symbol):\n                            return\n', '                    if signal.is_sell and is_option:\n                        # ── OFF_QUIET_20261030 ── OFF never enters (the router\n                        # drops it: MODE_OFF → DROP). Drop here, before the\n                        # gate\'s ATM REST quote, so an OFF strategy makes no\n                        # quote calls and raises no gate errors.\n                        if self.strategy_id == "SCALP_V1" and _strategy_is_off(self.strategy_id):\n                            write_audit_log(f"[DISPATCH][SCALP_V1] MODE_OFF → DROP {symbol} "\n                                            f"(before skew gate) ts={candle.end_ts}")\n                            return\n                        if not self._atm_skew_ok(symbol):\n                            return\n'), ('                if _mode != "PAPER":\n', '                if _mode in ("LIVE", "PAPER_LIVE"):   # ── OFF_QUIET_20261030 ── OFF never pages\n')]}

MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}


def die(msg):
    print(f"\n✗ {msg}\n  Nothing was written.")
    sys.exit(1)


def main():
    root = Path.cwd()
    check_only = "--check" in sys.argv
    allow_dirty = "--allow-dirty" in sys.argv
    if not (root / "backend/app").is_dir() or not (root / "frontend/src").is_dir():
        die("run this from the scalp-app repo root (backend/ and frontend/ not found)")

    # ── prerequisites ───────────────────────────────────────────────────
    pf = root / PARENT_FILE
    if not pf.exists() or PARENT not in pf.read_text():
        die(f"parent marker {PARENT!r} missing from {PARENT_FILE} (Account 2 wiring not present)")
    for rel in list(EDITS) + list(NEW_FILES):
        p = root / rel
        if p.exists() and FENCE in p.read_text():
            die(f"{FENCE} already present in {rel} — already applied")

    # ── working-copy drift guard (ORB_RECONCILE scar) ───────────────────
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + list(EDITS),
                            capture_output=True, text=True, cwd=root, timeout=30)
        dirty = [l for l in st.stdout.splitlines() if l.strip()]
        if dirty and not allow_dirty:
            die("uncommitted changes in target files:\n    " + "\n    ".join(dirty)
                + "\n  Commit or stash them, or re-run with --allow-dirty if they are yours.")
    except FileNotFoundError:
        print("  ! git not found — skipping the working-copy check")

    # ── stage every change in memory; every anchor must match exactly once ─
    staged = {}
    for rel, pairs in EDITS.items():
        p = root / rel
        if not p.exists():
            die(f"target missing: {rel}")
        txt = p.read_text()
        for i, (old, new) in enumerate(pairs, 1):
            n = txt.count(old)
            if n != 1:
                die(f"{rel}: anchor {i} matched {n} times (expected 1) — the file differs "
                    f"from GitHub main. First line of the anchor:\n    {old.splitlines()[0]!r}")
            txt = txt.replace(old, new, 1)
        staged[rel] = txt
    for rel, txt in NEW_FILES.items():
        if (root / rel).exists():
            die(f"{rel} already exists — refusing to overwrite")
        staged[rel] = txt

    # ── py_compile gate on staged Python ────────────────────────────────
    tmpd = Path(tempfile.mkdtemp(prefix="off_quiet_pyc_"))
    try:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                f = tmpd / rel.replace("/", "__")
                f.write_text(txt)
                try:
                    py_compile.compile(str(f), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    if check_only:
        print(f"✓ --check: {len(EDITS)} files patch cleanly, {len(NEW_FILES)} new files, py_compile OK.")
        return

    # ── write: backups first, then files, then mirrors ──────────────────
    written, backups = [], []

    def rollback():
        for rel in written:
            p = root / rel
            b = p.with_name(p.name + f".bak-{FENCE}")
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
            elif p.exists():
                p.unlink()

    try:
        for rel, txt in staged.items():
            p = root / rel
            if p.exists():
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append(str(b))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt)
            written.append(rel)
    except Exception as e:
        rollback()
        die(f"write failed ({e!r}) — rolled back")

    # ── behavioural suites on the patched tree ──────────────────────────
    env = dict(os.environ, PYTHONPATH=str(root / "backend"))
    suites = ["app/risk/test_off_quiet.py", "app/engine/emc/test_emc_live.py",
              "app/trading/test_fleet_modes.py"]
    for s in suites:
        if not (root / "backend" / s).exists():
            print(f"\n── {s}: not present on this tree — skipped")
            continue
        r = subprocess.run([sys.executable, s], cwd=root / "backend", env=env,
                           capture_output=True, text=True, timeout=300)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
        print(f"\n── {s}\n{tail}")
        if r.returncode != 0:
            rollback()
            die(f"{s} failed — every file rolled back")

    # ── JSX parse check (esbuild if present) ────────────────────────────
    esb = None
    for cand in ("frontend/node_modules/.bin/esbuild", "desktop/node_modules/.bin/esbuild",
                 "desktop/src-tauri/frontend/node_modules/.bin/esbuild"):
        if (root / cand).exists():
            esb = root / cand
            break
    jsx = [rel for rel in staged if rel.endswith(".jsx")]
    if esb:
        for rel in jsx:
            r = subprocess.run([str(esb), str(root / rel), "--loader:.jsx=jsx", "--log-level=error",
                                "--outfile=/dev/null"], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                rollback()
                die(f"esbuild could not parse {rel}:\n{r.stderr[-1500:]}\n  rolled back")
        print(f"\n✓ esbuild parsed {len(jsx)} JSX files")
    else:
        print("\n! esbuild not found — JSX is checked by the frontend build instead")

    # ── mirror into the desktop build trees, if present ─────────────────
    mirrored = 0
    for rel, txt in staged.items():
        for src, dst in MIRRORS.items():
            if rel.startswith(src):
                mp = root / (dst + rel[len(src):])
                if (root / dst).is_dir():
                    mp.parent.mkdir(parents=True, exist_ok=True)
                    mp.write_text(txt)
                    mirrored += 1

    print(f"\n✓ {FENCE} applied: {len(EDITS)} files patched, {len(NEW_FILES)} new"
          f"{f', {mirrored} build-tree copies' if mirrored else ''}.")
    print("  Backups: *.bak-" + FENCE)
    print("  Next:    ./desktop/build-scalp.sh both   (non-trading hours)")


if __name__ == "__main__":
    main()
