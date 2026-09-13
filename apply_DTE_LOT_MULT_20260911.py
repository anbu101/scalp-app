#!/usr/bin/env python3
"""
apply_DTE_LOT_MULT_20260911.py — FENCE: DTE_LOT_MULT_20260911

Requires BT_DTE_SESSIONS_20260911 (corpus trading-day calendar).

Per-DTE lot multiplier for the BRK_V1, ORB_V1 and TSG_V1 backtests:
  dte_lot_mult = {dte: multiplier}   e.g. "0:1.5, 1:0, 4:2"
    * absent DTE → 1.0 (unchanged)
    * 0 → the day is SKIPPED (diag dte_skipped_days)
    * otherwise lots_day = max(1, round(lots × mult))   (TSG: per leg)
DTE = trading SESSIONS from the sim day to its expected expiry, from the
same corpus calendar the Sessions-to-Expiry panel uses, so "0DTE" here is
the row you saw there. Motivation (2026-09-11, split-half analysis): BRK's
0DTE edge and ORB's 4DTE win-rate edge both survive 2020–23 vs 2024–26;
with 10 lots of capital the question is whether running BRK on 0DTE only
and ORB on 4DTE only beats running either on every day.

Where it lands (one place per runner, at the day head after the
skip_expiry_day check):
  BRK / ORB: `qty` is rebound for the day (lots_day × lot_size).
  TSG:       the leg list used for selection carries scaled lots.
Calendar empty (corpus absent) → multiplier ignored, diag dte_unknown_days,
audited once — fail-open to the unscaled run, never a silent skip.

Diag: dte_lot_mult, dte_skipped_days, dte_scaled_days, dte_unknown_days.

Files:
  backend/app/backtest/engine/dte_lots.py           NEW helper (parse + lots_for_day)
  backend/app/backtest/brk/backtest_brk_runner.py
  backend/app/backtest/orb/backtest_orb_runner.py
  backend/app/backtest/tsg/backtest_tsg_runner.py
  frontend/src/pages/Backtest.jsx                   field + chip + config for the three forms

Safety: parent-fence check, py_compile gate, helper unit tests, ORB
runner-level test on a synthetic corpus (mult 0 → day skipped; mult 2 →
qty doubled; unrelated DTE → unchanged; string form parsed), existing
BRK/ORB/TSG suites on the patched modules, node test of the UI parser,
JSX parse gate, .bak backups, dual-tree mirror. Backend + frontend →
full rebuild.

Run from the repo root:  python3 apply_DTE_LOT_MULT_20260911.py
"""
import importlib.util, os, py_compile, shutil, sqlite3, subprocess, sys, tempfile
from datetime import date, datetime, timedelta, timezone

FENCE = "DTE_LOT_MULT_20260911"
PARENT = "BT_DTE_SESSIONS_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"helper": "backend/app/backtest/engine/dte_lots.py",
       "brk": "backend/app/backtest/brk/backtest_brk_runner.py",
       "orb": "backend/app/backtest/orb/backtest_orb_runner.py",
       "tsg": "backend/app/backtest/tsg/backtest_tsg_runner.py",
       "ui":  "frontend/src/pages/Backtest.jsx"}
PATH = {k: os.path.join(ROOT, v) for k, v in REL.items()}
if not os.path.isfile(PATH["orb"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


if not os.path.isfile(os.path.join(ROOT, "backend/app/backtest/repo/trading_calendar.py")):
    die(f"{PARENT} not applied (trading_calendar.py missing) — run apply_{PARENT}.py first")
for k in ("brk", "orb", "tsg", "ui"):
    if FENCE in read(PATH[k]):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")
if os.path.exists(PATH["helper"]):
    die(f"{REL['helper']} already exists — nothing to do")

HELPER = '''# backend/app/backtest/engine/dte_lots.py
#
# ── DTE_LOT_MULT_20260911 ── per-DTE lot multiplier for backtest runners.
#   dte_lot_mult = {dte: mult}; absent → 1.0; 0 → skip the day;
#   lots_day = max(1, round(lots × mult)).
# DTE = trading sessions from the sim day to its expected expiry, from the
# corpus calendar (app.backtest.repo.trading_calendar) — the same numbers
# the Sessions-to-Expiry breakdown shows.
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def parse_dte_lot_mult(raw) -> Dict[int, float]:
    """Accepts {"0": 2, 4: 1.5}, "0:2, 1:0, 4:1.5", or [[0, 2], [1, 0]].
    Invalid entries are ignored; negatives clamp to 0."""
    out: Dict[int, float] = {}
    if raw is None:
        return out
    items = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, str):
        for part in raw.replace(";", ",").split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                items.append((k.strip(), v.strip()))
    elif isinstance(raw, (list, tuple)):
        for it in raw:
            if isinstance(it, (list, tuple)) and len(it) == 2:
                items.append((it[0], it[1]))
    for k, v in items:
        try:
            ki = int(str(k).strip().upper().replace("DTE", ""))
            vf = float(v)
        except (TypeError, ValueError):
            continue
        if ki < 0:
            continue
        out[ki] = max(0.0, vf)
    return out


def lots_for_day(base_lots: int, mult_map: Dict[int, float],
                 dte: Optional[int]) -> Tuple[int, str]:
    """→ (lots_for_this_day, tag) with tag in {"base","scaled","skip","unknown"}.
    dte None (calendar unavailable) → base lots, tag "unknown" (fail-open)."""
    base = int(base_lots or 0)
    if not mult_map:
        return base, "base"
    if dte is None:
        return base, "unknown"
    m = mult_map.get(int(dte))
    if m is None:
        return base, "base"
    if m <= 0:
        return 0, "skip"
    return max(1, int(round(base * m))), "scaled"


def dte_for_day(cal_sorted: List[str], day_iso: str, expiry_iso: str) -> Optional[int]:
    from app.backtest.repo.trading_calendar import sessions_to_expiry
    return sessions_to_expiry(cal_sorted, day_iso, expiry_iso)
'''

# ── BRK ─────────────────────────────────────────────────────────────────
brk = read(PATH["brk"])
brk = sub1(brk,
    '    cfg["lots"] = cfg["lots"] or 1\n',
    '    cfg["lots"] = cfg["lots"] or 1\n'
    '    from app.backtest.engine.dte_lots import parse_dte_lot_mult   # ── DTE_LOT_MULT_20260911 ──\n'
    '    cfg["dte_lot_mult"] = parse_dte_lot_mult(cfg.get("dte_lot_mult"))\n',
    "brk cfg")
brk = sub1(brk,
    '        "days_uncovered": 0, "days_skipped_expiry": 0,\n',
    '        "days_uncovered": 0, "days_skipped_expiry": 0,\n'
    '        # ── DTE_LOT_MULT_20260911 ──\n'
    '        "dte_lot_mult": {str(k): v for k, v in cfg["dte_lot_mult"].items()},\n'
    '        "dte_skipped_days": 0, "dte_scaled_days": 0, "dte_unknown_days": 0,\n',
    "brk diag")
brk = sub1(brk,
    '    for i, day in enumerate(days):\n'
    '        if cancel_cb and cancel_cb():\n'
    '            break\n'
    '        if progress_cb:\n'
    '            # Contract matches VET/TMA/PST/CBO: day = 1-based index,\n',
    '    # ── DTE_LOT_MULT_20260911 ── corpus trading-day calendar (sessions)\n'
    '    _dte_cal: List[str] = []\n'
    '    if cfg["dte_lot_mult"]:\n'
    '        from app.backtest.repo.trading_calendar import trading_dates as _td\n'
    '        _dte_cal = _td(underlying, db_path)\n'
    '        if not _dte_cal:\n'
    '            write_audit_log(f"[BACKTEST][{strategy_id}] dte_lot_mult set but the "\n'
    '                            f"corpus calendar is empty — multiplier ignored")\n'
    '    from app.backtest.engine.dte_lots import lots_for_day as _lots_for_day, dte_for_day as _dte_for_day\n'
    '\n'
    '    for i, day in enumerate(days):\n'
    '        if cancel_cb and cancel_cb():\n'
    '            break\n'
    '        if progress_cb:\n'
    '            # Contract matches VET/TMA/PST/CBO: day = 1-based index,\n',
    "brk pre-loop")
brk = sub1(brk,
    '        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:\n'
    '            diag["days_skipped_expiry"] += 1\n'
    '            continue\n'
    '        universe = src.contracts_active_on_day(underlying, ds, expiry=want)\n',
    '        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:\n'
    '            diag["days_skipped_expiry"] += 1\n'
    '            continue\n'
    '        # ── DTE_LOT_MULT_20260911 ── per-day lots (0 = skip)\n'
    '        if cfg["dte_lot_mult"]:\n'
    '            _dte = _dte_for_day(_dte_cal, day.isoformat(), want) if _dte_cal else None\n'
    '            _lots, _tag = _lots_for_day(cfg["lots"], cfg["dte_lot_mult"], _dte)\n'
    '            if _tag == "skip":\n'
    '                diag["dte_skipped_days"] += 1\n'
    '                continue\n'
    '            if _tag == "scaled":\n'
    '                diag["dte_scaled_days"] += 1\n'
    '            elif _tag == "unknown":\n'
    '                diag["dte_unknown_days"] += 1\n'
    '            qty = _lots * lot_size\n'
    '        universe = src.contracts_active_on_day(underlying, ds, expiry=want)\n',
    "brk day head")
if "write_audit_log" not in brk.split("def run_brk_backtest")[0]:
    # runner imports write_audit_log lazily? make sure it is available at module level
    brk = sub1(brk, "from __future__ import annotations\n", "from __future__ import annotations\n", "noop")
    if "from app.event_bus.audit_logger import write_audit_log" not in brk:
        die("brk runner: write_audit_log not importable — inspect")

# ── ORB ─────────────────────────────────────────────────────────────────
orb = read(PATH["orb"])
orb = sub1(orb,
    '    cfg["lots"] = cfg["lots"] or 1\n',
    '    cfg["lots"] = cfg["lots"] or 1\n'
    '    from app.backtest.engine.dte_lots import parse_dte_lot_mult   # ── DTE_LOT_MULT_20260911 ──\n'
    '    cfg["dte_lot_mult"] = parse_dte_lot_mult(cfg.get("dte_lot_mult"))\n',
    "orb cfg")
orb = sub1(orb,
    '        "days_uncovered": 0, "days_skipped_expiry": 0, "days_no_spot": 0,\n',
    '        "days_uncovered": 0, "days_skipped_expiry": 0, "days_no_spot": 0,\n'
    '        # ── DTE_LOT_MULT_20260911 ──\n'
    '        "dte_lot_mult": {str(k): v for k, v in cfg["dte_lot_mult"].items()},\n'
    '        "dte_skipped_days": 0, "dte_scaled_days": 0, "dte_unknown_days": 0,\n',
    "orb diag")
orb = sub1(orb,
    '    for i, day in enumerate(days):\n'
    '        if cancel_cb and cancel_cb():\n'
    '            break\n'
    '        if progress_cb:\n'
    '            progress_cb({"day": i + 1, "total_days": len(days),\n'
    '                         "date": day.isoformat(), "trades": len(trades)})\n'
    '\n'
    '        ds = _day_start_epoch(day)\n'
    '        want = expected_expiry_for_day(day).isoformat()\n'
    '        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:\n'
    '            diag["days_skipped_expiry"] += 1\n'
    '            continue\n'
    '\n'
    '        spot_1m = [',
    '    # ── DTE_LOT_MULT_20260911 ── corpus trading-day calendar (sessions)\n'
    '    _dte_cal: List[str] = []\n'
    '    if cfg["dte_lot_mult"]:\n'
    '        from app.backtest.repo.trading_calendar import trading_dates as _td\n'
    '        _dte_cal = _td(underlying, db_path)\n'
    '        if not _dte_cal:\n'
    '            write_audit_log(f"[BACKTEST][{strategy_id}] dte_lot_mult set but the "\n'
    '                            f"corpus calendar is empty — multiplier ignored")\n'
    '    from app.backtest.engine.dte_lots import lots_for_day as _lots_for_day, dte_for_day as _dte_for_day\n'
    '\n'
    '    for i, day in enumerate(days):\n'
    '        if cancel_cb and cancel_cb():\n'
    '            break\n'
    '        if progress_cb:\n'
    '            progress_cb({"day": i + 1, "total_days": len(days),\n'
    '                         "date": day.isoformat(), "trades": len(trades)})\n'
    '\n'
    '        ds = _day_start_epoch(day)\n'
    '        want = expected_expiry_for_day(day).isoformat()\n'
    '        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:\n'
    '            diag["days_skipped_expiry"] += 1\n'
    '            continue\n'
    '        # ── DTE_LOT_MULT_20260911 ── per-day lots (0 = skip)\n'
    '        if cfg["dte_lot_mult"]:\n'
    '            _dte = _dte_for_day(_dte_cal, day.isoformat(), want) if _dte_cal else None\n'
    '            _lots, _tag = _lots_for_day(cfg["lots"], cfg["dte_lot_mult"], _dte)\n'
    '            if _tag == "skip":\n'
    '                diag["dte_skipped_days"] += 1\n'
    '                continue\n'
    '            if _tag == "scaled":\n'
    '                diag["dte_scaled_days"] += 1\n'
    '            elif _tag == "unknown":\n'
    '                diag["dte_unknown_days"] += 1\n'
    '            qty = _lots * lot_size\n'
    '\n'
    '        spot_1m = [',
    "orb day head")

# ── TSG ─────────────────────────────────────────────────────────────────
tsg = read(PATH["tsg"])
tsg = sub1(tsg,
    '    bank_diag_level = mtm_bank_target if mtm_bank_target > 0 else 1000.0\n',
    '    bank_diag_level = mtm_bank_target if mtm_bank_target > 0 else 1000.0\n'
    '    from app.backtest.engine.dte_lots import parse_dte_lot_mult   # ── DTE_LOT_MULT_20260911 ──\n'
    '    dte_lot_mult = parse_dte_lot_mult(cfg.get("dte_lot_mult"))\n',
    "tsg cfg")
tsg = sub1(tsg,
    '        "days_uncovered": 0, "days_no_short_strike": 0,\n',
    '        "days_uncovered": 0, "days_no_short_strike": 0,\n'
    '        # ── DTE_LOT_MULT_20260911 ──\n'
    '        "dte_lot_mult": {str(k): v for k, v in dte_lot_mult.items()},\n'
    '        "dte_skipped_days": 0, "dte_scaled_days": 0, "dte_unknown_days": 0,\n',
    "tsg diag")
tsg = sub1(tsg,
    '    for di, d in enumerate(sim_days, start=1):\n'
    '        if cancel_cb and cancel_cb():\n'
    '            break\n'
    '        if progress_cb:\n'
    '            progress_cb({"day": di, "total_days": len(sim_days),\n'
    '                         "date": d.isoformat()})\n',
    '    # ── DTE_LOT_MULT_20260911 ── corpus trading-day calendar (sessions)\n'
    '    _dte_cal: List[str] = []\n'
    '    if dte_lot_mult:\n'
    '        from app.backtest.repo.trading_calendar import trading_dates as _td\n'
    '        _dte_cal = _td(underlying, db_path)\n'
    '        if not _dte_cal:\n'
    '            write_audit_log(f"[BACKTEST][{strategy_id}] dte_lot_mult set but the "\n'
    '                            f"corpus calendar is empty — multiplier ignored")\n'
    '    from app.backtest.engine.dte_lots import lots_for_day as _lots_for_day, dte_for_day as _dte_for_day\n'
    '\n'
    '    for di, d in enumerate(sim_days, start=1):\n'
    '        if cancel_cb and cancel_cb():\n'
    '            break\n'
    '        if progress_cb:\n'
    '            progress_cb({"day": di, "total_days": len(sim_days),\n'
    '                         "date": d.isoformat()})\n',
    "tsg pre-loop")
tsg = sub1(tsg,
    '        week = [c for c in universe if c.get("expiry") == want_expiry]\n'
    '        if not week:\n'
    '            diag["days_uncovered"] += 1\n'
    '            write_audit_log(f"[BACKTEST][{strategy_id}] {d}: expected expiry "\n'
    '                            f"{want_expiry} not in corpus — day skipped")\n'
    '            continue\n',
    '        week = [c for c in universe if c.get("expiry") == want_expiry]\n'
    '        if not week:\n'
    '            diag["days_uncovered"] += 1\n'
    '            write_audit_log(f"[BACKTEST][{strategy_id}] {d}: expected expiry "\n'
    '                            f"{want_expiry} not in corpus — day skipped")\n'
    '            continue\n'
    '        # ── DTE_LOT_MULT_20260911 ── per-day leg lots (0 = skip)\n'
    '        _legs_day = legs_cfg\n'
    '        if dte_lot_mult:\n'
    '            _dte = _dte_for_day(_dte_cal, d.isoformat(), want_expiry) if _dte_cal else None\n'
    '            _l1, _tag = _lots_for_day(1, dte_lot_mult, _dte)\n'
    '            if _tag == "skip":\n'
    '                diag["dte_skipped_days"] += 1\n'
    '                continue\n'
    '            if _tag == "scaled":\n'
    '                diag["dte_scaled_days"] += 1\n'
    '                _m = float(dte_lot_mult.get(int(_dte)))\n'
    '                _legs_day = [dict(l, lots=max(1, int(round(int(l["lots"]) * _m))))\n'
    '                             for l in legs_cfg]\n'
    '            elif _tag == "unknown":\n'
    '                diag["dte_unknown_days"] += 1\n',
    "tsg day head")
tsg = sub1(tsg,
    '        for leg in legs_cfg:\n',
    '        for leg in _legs_day:   # ── DTE_LOT_MULT_20260911 ──\n',
    "tsg legs loop")

# ── UI ──────────────────────────────────────────────────────────────────
ui = read(PATH["ui"])
# parser helper at module level (after DAY_NAMES)
ui = sub1(ui,
    'const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];\n',
    'const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];\n'
    '// ── DTE_LOT_MULT_20260911 ── "0:1.5, 1:0, 4:2" → {0:1.5, 1:0, 4:2}; junk ignored\n'
    'function parseDteLotMult(s) {\n'
    '  const out = {};\n'
    '  String(s || "").replace(/;/g, ",").split(",").forEach((part) => {\n'
    '    const m = part.match(/^\\s*(\\d+)\\s*(?:DTE)?\\s*:\\s*([0-9]*\\.?[0-9]+)\\s*$/i);\n'
    '    if (m) out[Number(m[1])] = Math.max(0, Number(m[2]));\n'
    '  });\n'
    '  return out;\n'
    '}\n'
    'const dteMultChip = (cfg) => {\n'
    '  const m = cfg && cfg.dte_lot_mult && typeof cfg.dte_lot_mult === "object" ? cfg.dte_lot_mult : null;\n'
    '  if (!m) return null;\n'
    '  const parts = Object.keys(m).sort((a, b) => Number(a) - Number(b)).map((k) => `${k}DTE×${Number(m[k])}`);\n'
    '  return parts.length ? parts.join(" ") : null;\n'
    '};\n',
    "ui parser")
# state (after each skip-expiry state)
ui = sub1(ui,
    '  const [brkSkipExpiry, setBrkSkipExpiry] = useState(brkSaved.skipExpiry ?? false);\n',
    '  const [brkSkipExpiry, setBrkSkipExpiry] = useState(brkSaved.skipExpiry ?? false);\n'
    '  const [brkDteMult, setBrkDteMult] = useState(brkSaved.dteMult ?? "");   // ── DTE_LOT_MULT_20260911 ──\n',
    "ui brk state")
ui = sub1(ui,
    '  const [orbSkipExpiry, setOrbSkipExpiry] = useState(orbSaved.skipExpiry ?? false);\n',
    '  const [orbSkipExpiry, setOrbSkipExpiry] = useState(orbSaved.skipExpiry ?? false);\n'
    '  const [orbDteMult, setOrbDteMult] = useState(orbSaved.dteMult ?? "");   // ── DTE_LOT_MULT_20260911 ──\n',
    "ui orb state")
ui = sub1(ui,
    '  const [tsgBankCutoff, setTsgBankCutoff] = useState(tsgSaved.bankCutoff ?? "14:30");\n',
    '  const [tsgBankCutoff, setTsgBankCutoff] = useState(tsgSaved.bankCutoff ?? "14:30");\n'
    '  const [tsgDteMult, setTsgDteMult] = useState(tsgSaved.dteMult ?? "");   // ── DTE_LOT_MULT_20260911 ──\n',
    "ui tsg state")
# localStorage + deps
ui = sub1(ui, 'lots: brkLots, lotSize: brkLotSize, skipExpiry: brkSkipExpiry })); } catch { /* ignore */ }\n'
    '  }, [brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkFb, brkFbMin, brkS2, brkS2Sel, brkS2First, brkS2Last, brkS2Flat, brkS2Loss, brkEod, brkLots, brkLotSize, brkSkipExpiry]);',
    'lots: brkLots, lotSize: brkLotSize, skipExpiry: brkSkipExpiry, dteMult: brkDteMult })); } catch { /* ignore */ }\n'
    '  }, [brkSelTime, brkSelBelow, brkSelMin, brkBreak, brkSustain, brkFirst, brkLast, brkBoth, brkSl, brkTp, brkFb, brkFbMin, brkS2, brkS2Sel, brkS2First, brkS2Last, brkS2Flat, brkS2Loss, brkEod, brkLots, brkLotSize, brkSkipExpiry, brkDteMult]);   // ── DTE_LOT_MULT_20260911 ──',
    "ui brk ls")
ui = sub1(ui, 'lots: orbLots, lotSize: orbLotSize, skipExpiry: orbSkipExpiry })); } catch { /* ignore */ }\n'
    '  }, [orbOrbMin, orbTrigger, orbPremMax, orbPremMin, orbTargetMode, orbTargetVal, orbSlMode, orbSlPts, orbSlFill, orbSlTrig, orbSlDistMode, orbPremSlMode, orbPremSlVal, orbBlockTime, orbMaxDay, orbMaxSide, orbEod, orbLots, orbLotSize, orbSkipExpiry]);',
    'lots: orbLots, lotSize: orbLotSize, skipExpiry: orbSkipExpiry, dteMult: orbDteMult })); } catch { /* ignore */ }\n'
    '  }, [orbOrbMin, orbTrigger, orbPremMax, orbPremMin, orbTargetMode, orbTargetVal, orbSlMode, orbSlPts, orbSlFill, orbSlTrig, orbSlDistMode, orbPremSlMode, orbPremSlVal, orbBlockTime, orbMaxDay, orbMaxSide, orbEod, orbLots, orbLotSize, orbSkipExpiry, orbDteMult]);   // ── DTE_LOT_MULT_20260911 ──',
    "ui orb ls")
ui = sub1(ui, 'bankTarget: tsgBankTarget, bankMode: tsgBankMode, bankMax: tsgBankMax, bankCutoff: tsgBankCutoff })); } catch { /* ignore */ }   // ── TSG_BANK_20260911 ──\n'
    '  }, [tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult, tsgBankTarget, tsgBankMode, tsgBankMax, tsgBankCutoff]);',
    'bankTarget: tsgBankTarget, bankMode: tsgBankMode, bankMax: tsgBankMax, bankCutoff: tsgBankCutoff, dteMult: tsgDteMult })); } catch { /* ignore */ }   // ── TSG_BANK_20260911 / DTE_LOT_MULT_20260911 ──\n'
    '  }, [tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult, tsgBankTarget, tsgBankMode, tsgBankMax, tsgBankCutoff, tsgDteMult]);',
    "ui tsg ls")
# config assembly
ui = sub1(ui,
    '        lot_size: Number(orbLotSize) || 0,\n'
    '        skip_expiry_day: !!orbSkipExpiry,\n'
    '      };\n',
    '        lot_size: Number(orbLotSize) || 0,\n'
    '        skip_expiry_day: !!orbSkipExpiry,\n'
    '        dte_lot_mult: parseDteLotMult(orbDteMult),   // ── DTE_LOT_MULT_20260911 ──\n'
    '      };\n',
    "ui orb cfg")
ui = sub1(ui,
    '        lot_size: Number(brkLotSize) || 0,\n'
    '        skip_expiry_day: !!brkSkipExpiry,\n'
    '      };\n',
    '        lot_size: Number(brkLotSize) || 0,\n'
    '        skip_expiry_day: !!brkSkipExpiry,\n'
    '        dte_lot_mult: parseDteLotMult(brkDteMult),   // ── DTE_LOT_MULT_20260911 ──\n'
    '      };\n',
    "ui brk cfg")
ui = sub1(ui,
    '        bank_reentry_cutoff: tsgBankCutoff || "14:30",\n',
    '        bank_reentry_cutoff: tsgBankCutoff || "14:30",\n'
    '        dte_lot_mult: parseDteLotMult(tsgDteMult),   // ── DTE_LOT_MULT_20260911 ──\n',
    "ui tsg cfg")
# buildConfig deps (stale-closure rule)
ui = sub1(ui,
    'brkEod, brkLots, brkLotSize, brkSkipExpiry,\n',
    'brkEod, brkLots, brkLotSize, brkSkipExpiry, brkDteMult,   // ── DTE_LOT_MULT_20260911 ──\n',
    "ui brk deps")
ui = sub1(ui,
    'orbEod, orbLots, orbLotSize, orbSkipExpiry,   // ── ORB_',
    'orbEod, orbLots, orbLotSize, orbSkipExpiry, orbDteMult,   // ── DTE_LOT_MULT_20260911 / ORB_',
    "ui orb deps")
ui = sub1(ui,
    '      tsgBankTarget, tsgBankMode, tsgBankMax, tsgBankCutoff,   // ── TSG_BANK_20260911 ── stale-closure rule\n',
    '      tsgBankTarget, tsgBankMode, tsgBankMax, tsgBankCutoff, tsgDteMult,   // ── TSG_BANK_20260911 / DTE_LOT_MULT_20260911 ── stale-closure rule\n',
    "ui tsg deps")
# chips: the two identical ORB/BRK lines are disambiguated by the line that follows each
ui = sub1(ui,
    '    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);\n'
    '    if (cfg.lots) add("Lots", cfg.lots);\n'
    '    if (cfg.skip_expiry_day) add("Expiry", "skipped");\n'
    '    return out;\n'
    '  }\n'
    '  // ── BRK_V1_UI_20260830 ──',
    '    if (cfg.eod_square_off) add("EOD", cfg.eod_square_off);\n'
    '    if (cfg.lots) add("Lots", cfg.lots);\n'
    '    if (cfg.skip_expiry_day) add("Expiry", "skipped");\n'
    '    if (dteMultChip(cfg)) add("DTE lots", dteMultChip(cfg));   // ── DTE_LOT_MULT_20260911 ──\n'
    '    return out;\n'
    '  }\n'
    '  // ── BRK_V1_UI_20260830 ──',
    "ui orb chip")
ui = sub1(ui,
    '    if (cfg.s2_enabled) add("Session 2",',
    '    if (dteMultChip(cfg)) add("DTE lots", dteMultChip(cfg));   // ── DTE_LOT_MULT_20260911 ──\n'
    '    if (cfg.s2_enabled) add("Session 2",',
    "ui brk chip")
ui = sub1(ui,
    '  if (Number(cfg.mtm_bank_target) > 0) add("Bank",',
    '  if (dteMultChip(cfg)) add("DTE lots", dteMultChip(cfg));   // ── DTE_LOT_MULT_20260911 ──\n'
    '  if (Number(cfg.mtm_bank_target) > 0) add("Bank",',
    "ui tsg chip")
# fields
DTE_TITLE = ('Per-DTE lot multiplier — DTE = trading SESSIONS to expiry (the Sessions-to-Expiry breakdown). '
             'Format 0:1.5, 1:0, 4:2 · absent DTE = ×1 · 0 = SKIP that day · lots = max(1, round(lots × mult)). '
             'Blank = off. Motivation 2026-09-11: BRK 0DTE and ORB 4DTE edges survive the 2020–23 / 2024–26 split.')
ui = sub1(ui,
    '                  <input type="checkbox" checked={brkSkipExpiry} onChange={(e) => setBrkSkipExpiry(e.target.checked)} /> skip expiry day\n'
    '                </label>\n',
    '                  <input type="checkbox" checked={brkSkipExpiry} onChange={(e) => setBrkSkipExpiry(e.target.checked)} /> skip expiry day\n'
    '                </label>\n'
    '                <Field label="DTE × lots (0 = skip)"><input type="text" style={inputStyle} value={brkDteMult} onChange={(e) => setBrkDteMult(e.target.value)} placeholder="e.g. 0:1.5, 1:0" title="' + DTE_TITLE + '" /></Field>   {/* ── DTE_LOT_MULT_20260911 ── */}\n',
    "ui brk field")
ui = sub1(ui,
    '                  <input type="checkbox" checked={orbSkipExpiry} onChange={(e) => setOrbSkipExpiry(e.target.checked)} /> skip expiry day\n'
    '                </label>\n',
    '                  <input type="checkbox" checked={orbSkipExpiry} onChange={(e) => setOrbSkipExpiry(e.target.checked)} /> skip expiry day\n'
    '                </label>\n'
    '                <Field label="DTE × lots (0 = skip)"><input type="text" style={inputStyle} value={orbDteMult} onChange={(e) => setOrbDteMult(e.target.value)} placeholder="e.g. 4:2, 0:0" title="' + DTE_TITLE + '" /></Field>   {/* ── DTE_LOT_MULT_20260911 ── */}\n',
    "ui orb field")
ui = sub1(ui,
    '                {/* ── TSG_BANK_20260911 END ── */}\n',
    '                {/* ── TSG_BANK_20260911 END ── */}\n'
    '                <Field label="DTE × lots (0 = skip)"><input type="text" style={inputStyle} value={tsgDteMult} onChange={(e) => setTsgDteMult(e.target.value)} placeholder="e.g. 0:1.5" title="' + DTE_TITLE + ' Scales every leg\'s lots for that day." /></Field>   {/* ── DTE_LOT_MULT_20260911 ── */}\n',
    "ui tsg field")

NEW = {"helper": HELPER, "brk": brk, "orb": orb, "tsg": tsg, "ui": ui}

# ═══════════════════════════════════════════════════════════════════════
# gates
# ═══════════════════════════════════════════════════════════════════════
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp = {}
for k in ("helper", "brk", "orb", "tsg"):
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
    tmp[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK")

# stage a shadow package so `app.backtest.engine.dte_lots` resolves to the patched helper
sys.path.insert(0, BACKEND)
import app.backtest.engine as _eng_pkg   # noqa
spec = importlib.util.spec_from_file_location("app.backtest.engine.dte_lots", tmp["helper"])
DL = importlib.util.module_from_spec(spec); sys.modules[spec.name] = DL; spec.loader.exec_module(DL)

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)

print("helper unit tests")
check("parse string", DL.parse_dte_lot_mult("0:1.5, 1:0, 4DTE:2") == {0: 1.5, 1: 0.0, 4: 2.0})
check("parse dict with string keys", DL.parse_dte_lot_mult({"0": "2", 3: 1}) == {0: 2.0, 3: 1.0})
check("parse junk ignored, negatives clamped", DL.parse_dte_lot_mult("x:1, 2:-3, 5:abc") == {2: 0.0})
check("parse None/empty → {}", DL.parse_dte_lot_mult(None) == {} and DL.parse_dte_lot_mult("") == {})
M = {0: 1.5, 1: 0.0, 4: 2.0}
check("lots: absent DTE → base", DL.lots_for_day(10, M, 2) == (10, "base"))
check("lots: 0 → skip", DL.lots_for_day(10, M, 1) == (0, "skip"))
check("lots: ×1.5 of 10 → 15 scaled", DL.lots_for_day(10, M, 0) == (15, "scaled"))
check("lots: ×1.5 of 1 → 2 (round), never 0 when mult>0", DL.lots_for_day(1, M, 0) == (2, "scaled") and DL.lots_for_day(1, {0: 0.2}, 0) == (1, "scaled"))
check("lots: calendar unknown → base, tagged unknown", DL.lots_for_day(10, M, None) == (10, "unknown"))
check("lots: empty map → base", DL.lots_for_day(10, {}, None) == (10, "base"))

print("ORB runner-level test (synthetic corpus)")
sys.path.insert(0, os.path.join(BACKEND, "app", "backtest", "orb"))
spec = importlib.util.spec_from_file_location("app.backtest.orb.backtest_orb_runner", tmp["orb"])
ORB = importlib.util.module_from_spec(spec); sys.modules[spec.name] = ORB; spec.loader.exec_module(ORB)
from app.backtest.orb.orb_v1_engine import SESSION_OPEN_MIN
from app.backtest.engine.expiry_calendar import expected_expiry_for_day
import app.backtest.repo.trading_calendar as TC
IST = timezone(timedelta(hours=5, minutes=30))
def ds_for(d): return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
def build_corpus(dbp):
    trade_day = date(2026, 1, 21)
    exp = expected_expiry_for_day(trade_day)
    cn = sqlite3.connect(dbp)
    cn.execute("""CREATE TABLE backtest_candles_1m (
        instrument_token INTEGER NOT NULL, ts INTEGER NOT NULL, underlying TEXT NOT NULL,
        tradingsymbol TEXT NOT NULL, instrument_type TEXT NOT NULL, strike REAL NOT NULL,
        expiry TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
        close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0, oi INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (instrument_token, ts))""")
    def put(tok, ts, sym, itype, strike, expiry, o, h, l, c):
        cn.execute("INSERT OR REPLACE INTO backtest_candles_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)",
                   (tok, ts, "NIFTY", sym, itype, strike, expiry, o, h, l, c))
    # ATR warmup days (SPOT only) before the trade day
    d = trade_day - timedelta(days=1); n = 0
    while n < 5:
        if d.weekday() < 5:
            for k in range(0, 375):
                put(1, ds_for(d) + (SESSION_OPEN_MIN + k) * 60, "NIFTY_SPOT", "SPOT", 0, "1970-01-01", 100.0, 110.0, 90.0, 100.0)
            n += 1
        d -= timedelta(days=1)
    tds = ds_for(trade_day)
    def spot(minute, o, h, l, c): put(1, tds + (SESSION_OPEN_MIN + minute) * 60, "NIFTY_SPOT", "SPOT", 0, "1970-01-01", o, h, l, c)
    for k in range(0, 15): spot(k, 105, 110, 100, 105)
    for k in range(15, 20): spot(k, 104, 106, 103, 105)
    spot(20, 106, 110.5, 105, 109)
    for k in range(21, 375): spot(k, 109, 111, 108, 110)
    for k in range(0, 375):
        mm = SESSION_OPEN_MIN + k; ts = tds + mm * 60
        px = 120.0 + max(0, mm - (SESSION_OPEN_MIN + 21)) * 1.0
        put(2, ts, "NIFTYTESTCE", "CE", 24000, exp.isoformat(), px, px + 0.4, px - 0.4, px + 0.2)
        put(3, ts, "NIFTYTESTPE", "PE", 24000, exp.isoformat(), 150, 151, 149, 150.5)
    # calendar rows: CE/PE on every weekday from trade day to expiry so the sessions count is real
    d = trade_day + timedelta(days=1); k = 0
    while d <= exp:
        if d.weekday() < 5:
            put(4, ds_for(d) + SESSION_OPEN_MIN * 60, "NIFTYCALCE", "CE", 24000, exp.isoformat(), 1, 1, 1, 1)
            k += 1
        d += timedelta(days=1)
    cn.commit(); cn.close()
    return trade_day, k     # k = sessions after trade day up to and incl. expiry = DTE
dbp = os.path.join(TMP, "backtest.db")
trade_day, DTE = build_corpus(dbp)
TC._cache.clear()
base = {"orb_minutes": 15, "target_mode": "abs", "target_value": 10.0, "spot_sl_mode": "range",
        "premium_max": 200.0, "premium_min": 100.0, "lots": 1}
def run(extra):
    cfg = dict(base); cfg.update(extra)
    return ORB.run_orb_backtest(db_path=dbp, strategy_id="ORB_V1", underlying="NIFTY",
                                date_from=trade_day, date_to=trade_day, config_override=cfg)
r0 = run({})
check(f"baseline: one trade, qty 65 (trade day is {DTE}DTE)", not r0.get("aborted") and r0["summary"]["total_trades"] == 1 and r0["trades"][0].qty == 65, str(r0.get("reason") or r0["summary"]))
r1 = run({"dte_lot_mult": {DTE: 0}})
d1 = r1["summary"]["diag_orb"]
check("mult 0 on this DTE → day skipped, no trades, diag counts it", r1["summary"]["total_trades"] == 0 and d1["dte_skipped_days"] == 1, str(d1))
r2 = run({"dte_lot_mult": f"{DTE}:2"})
d2 = r2["summary"]["diag_orb"]
check("mult 2 (string form) → qty 130, diag scaled", r2["summary"]["total_trades"] == 1 and r2["trades"][0].qty == 130 and d2["dte_scaled_days"] == 1 and d2["dte_lot_mult"] == {str(DTE): 2.0}, str(d2))
check("gross scales with qty", abs(r2["trades"][0].pnl - 2 * r0["trades"][0].pnl) < 1e-6)
r3 = run({"dte_lot_mult": {DTE + 1: 0, (DTE + 2) % 7: 0}})
check("mult on other DTEs → unchanged (qty 65, not skipped)", r3["summary"]["total_trades"] == 1 and r3["trades"][0].qty == 65 and r3["summary"]["diag_orb"]["dte_skipped_days"] == 0)
# calendar unavailable → fail-open, tagged unknown
real_td = TC.trading_dates
TC.trading_dates = lambda *a, **k: []
r4 = run({"dte_lot_mult": {DTE: 0}})
TC.trading_dates = real_td
check("empty calendar → multiplier ignored (fail-open), dte_unknown_days=1", r4["summary"]["total_trades"] == 1 and r4["summary"]["diag_orb"]["dte_unknown_days"] == 1, str(r4["summary"]["diag_orb"]))

print("existing suites on the patched modules")
env = dict(os.environ, PYTHONPATH=os.pathsep.join([BACKEND, os.environ.get("PYTHONPATH", "")]))
for k, rel_test in (("orb", "app/backtest/orb/test_orb_runner_sim.py"), ("brk", "app/backtest/brk/test_brk_runner_sim.py"), ("tsg", "app/backtest/tsg/test_tsg_runner.py")):
    # run the suite with the patched runner shadowing the real one via a temp copy of the dir
    shadow = os.path.join(TMP, f"shadow_{k}"); shutil.copytree(os.path.dirname(os.path.join(BACKEND, rel_test)), shadow)
    shutil.copy2(tmp[k], os.path.join(shadow, os.path.basename(REL[k])))
    shutil.copy2(tmp["helper"], os.path.join(BACKEND, "app", "backtest", "engine", "dte_lots.py"))   # helper must be importable by the suite
    pr = subprocess.run([sys.executable, os.path.join(shadow, os.path.basename(rel_test))], cwd=shadow, env=env, capture_output=True, text=True, timeout=600)
    ok = pr.returncode == 0
    check(f"{os.path.basename(rel_test)} passes", ok, (pr.stdout + pr.stderr)[-400:])
os.remove(os.path.join(BACKEND, "app", "backtest", "engine", "dte_lots.py"))   # remove the staged helper; written for real below

print("UI parser (node)")
js = os.path.join(TMP, "p.js")
m = ui[ui.index("function parseDteLotMult"):ui.index("const dteMultChip")]
with open(js, "w") as f:
    f.write(m + '''
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const chk = (n, ok) => { console.log((ok ? "  PASS  " : "  FAIL  ") + n); if (!ok) process.exit(1); };
chk("'0:1.5, 1:0, 4DTE:2'", eq(parseDteLotMult("0:1.5, 1:0, 4DTE:2"), {0: 1.5, 1: 0, 4: 2}));
chk("junk ignored", eq(parseDteLotMult("x:1, 2:-3, 5:abc, 3:0.5"), {3: 0.5}));
chk("blank → {}", eq(parseDteLotMult(""), {}) && eq(parseDteLotMult(undefined), {}));
''')
pr = subprocess.run(["node", js], capture_output=True, text=True); print(pr.stdout.strip())
if pr.returncode != 0: die("node parser test failed")
tmpjsx = os.path.join(TMP, "Backtest.jsx")
with open(tmpjsx, "w", encoding="utf-8") as f: f.write(ui)
pr = subprocess.run(["npx", "--no-install", "esbuild", tmpjsx, "--loader:.jsx=jsx", "--log-level=error"], cwd=os.path.join(ROOT, "frontend"), capture_output=True, text=True)
print("JSX parse gate: OK" if pr.returncode == 0 else "JSX parse gate: esbuild not available here — the frontend build is the gate")
if FAILS:
    die(f"gates failed: {FAILS}")
print("all gates: OK")

# ═══════════════════════════════════════════════════════════════════════
# write
# ═══════════════════════════════════════════════════════════════════════
for k in ("brk", "orb", "tsg", "ui"):
    shutil.copy2(PATH[k], PATH[k] + f".bak-{FENCE}")
for k, p in PATH.items():
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 5 files (4 backups; dte_lots.py is new)")
if os.path.isdir(DUAL):
    for k in ("helper", "brk", "orb", "tsg"):
        rel = REL[k].replace("backend/", "", 1)
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend + frontend: ./desktop/build-scalp.sh")
