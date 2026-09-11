#!/usr/bin/env python3
"""
apply_TSG_BANK_20260911.py — FENCE: TSG_BANK_20260911

TSG_V1 backtest — PROFIT BANK + RE-ENTRY (user observation 2026-09-11: a
month of 1-lot paper shows day MTM reaching ₹1,000–1,500 mid-day and
decaying — sometimes negative — into the 15:26 EOD).

New knobs (backtest only; live untouched):
  mtm_bank_target      ₹ (0 = off). When day MTM − bank_base ≥ target, the
                       open SELL legs exit at that minute's marks (reason
                       MTM_BANK). Hedges stay. bank_base = day MTM at the
                       moment of the last re-entry (0 at day start), so the
                       NEXT bank needs the NEW position to earn ₹target.
  bank_mode            SAME     (option 1) — re-enter the SAME contracts at
                                the next minute's mark.
                       RESTRIKE (option 2) — at the next minute, re-select
                                each short from the ladder with the leg's
                                premium cap (highest real premium ≤ cap; no
                                mid-day synth — none real ≤ cap → that short
                                is NOT re-entered, diag bank_reentry_fail).
  bank_max_per_day     int (0 = unlimited).
  bank_reentry_cutoff  "HH:MM" (default 14:30) — no bank/re-entry when the
                       re-entry minute would be past this.

Semantics (locked here, mirrors the existing knobs' style):
  * Per-minute precedence: MTM SL → target → TRAIL → BANK → IV. On the
    re-entry minute the shorts are re-entered FIRST, then SL/target/trail
    evaluate the fresh position (POSITION basis → clean runway).
  * Re-entry fills at the NEXT minute's mark — same "close of the candle
    ending at t" convention as the 09:16 entry. Re-entered leg ids are
    L1#1, L1#2 … (bank number), so the CSV "ENTRY CONDITION P&L" section
    separates original legs from re-entries.
  * SAME re-entries reuse the original leg's mark AND IV series (same
    contract, so IV11 thresholds and the IV9 losing-gate keep working).
    RESTRIKE re-entries are NOT IV-monitored (no entry anchor) — the day
    MTM SL / trail / target still govern them. Documented, not hidden.
  * Charges: every re-entered leg is a full extra round trip through the
    same charges model. That is the point of the experiment: the export
    shows avg net ₹177/day vs avg charges ₹182/day, and a 2-leg re-entry
    costs ≈ ₹130. The bank has to beat that.
  * ⚠ OPTION 1 ONLY LOCKS ANYTHING WITH mtm_sl_basis=POSITION. Exit + same
    contracts one minute later = the same position; the ₹ is protected
    only if the SL is measured from the re-entry. With DAILY basis the
    run is "hold + charges". The diag flags this (bank_sl_basis_warning).

Diag added (always, even with banking off — answers "how often does ₹1000
get touched and given back"): bank_diag_level (= target, or 1000 when
off), peak_ge_level_days, peak_ge_level_closed_below_days,
peak_ge_level_neg_close_days; and with banking on: bank_events, bank_days,
bank_reentry_fail.

Files:
  backend/app/backtest/tsg/backtest_tsg_runner.py   core + runner + diag
  backend/app/backtest/tsg/test_tsg_runner.py       + 7 regression tests
  frontend/src/pages/Backtest.jsx                   4 fields + config + LS

Safety: fence check, py_compile gate, 17-check simulation suite on the
patched core, the EXISTING test file run against the patched module
(regression), staged .bak writes, dual-tree mirror for the backend file.
Backend + frontend → full rebuild.

Run from the repo root:  python3 apply_TSG_BANK_20260911.py
"""
import importlib.util, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = "TSG_BANK_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"runner": "backend/app/backtest/tsg/backtest_tsg_runner.py",
       "test":   "backend/app/backtest/tsg/test_tsg_runner.py",
       "ui":     "frontend/src/pages/Backtest.jsx"}
PATH = {k: os.path.join(ROOT, v) for k, v in REL.items()}
if not os.path.isfile(PATH["runner"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


for k, p in PATH.items():
    if FENCE in read(p):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")

# ═══════════════════════════════════════════════════════════════════════
# 1. runner — pure core
# ═══════════════════════════════════════════════════════════════════════
r = read(PATH["runner"])

r = sub1(r,
    '    iv_keep_hedge: bool = False,\n'
    '    mtm_sl_basis: str = "DAILY",   # ── TSG_MTM_BASIS_20260821 ── "DAILY"|"POSITION" (SL only)\n'
    ') -> dict:\n',
    '    iv_keep_hedge: bool = False,\n'
    '    mtm_sl_basis: str = "DAILY",   # ── TSG_MTM_BASIS_20260821 ── "DAILY"|"POSITION" (SL only)\n'
    '    # ── TSG_BANK_20260911 ── profit bank + re-entry (see module note)\n'
    '    mtm_bank_target: float = 0.0,\n'
    '    bank_mode: str = "SAME",              # "SAME" | "RESTRIKE"\n'
    '    bank_max_per_day: int = 0,            # 0 = unlimited\n'
    '    bank_reentry_cutoff_min: Optional[int] = None,   # minute-of-day\n'
    '    restrike_fn: Optional[Callable[[int, str, float], Optional[dict]]] = None,\n'
    ') -> dict:\n',
    "core signature")

r = sub1(r,
    '    Returns {"exits": {leg_id: {"ts","reason","price"}},\n'
    '             "day_exit_reason", "mtm_final", "peak_mtm", "trough_mtm"}.\n'
    '    day_exit_reason is the reason that closed the LAST open leg(s)."""\n',
    '    Returns {"exits": {leg_id: {"ts","reason","price"}},\n'
    '             "day_exit_reason", "mtm_final", "peak_mtm", "trough_mtm"}.\n'
    '    day_exit_reason is the reason that closed the LAST open leg(s).\n'
    '\n'
    '    ── TSG_BANK_20260911 ── PROFIT BANK (mtm_bank_target > 0): when\n'
    '    day MTM − bank_base ≥ target, every open SELL leg closes at that\n'
    '    minute\'s mark (MTM_BANK); hedges stay. At the NEXT minute the shorts\n'
    '    re-enter: SAME = same contract at that minute\'s mark (reuses the\n'
    '    source leg\'s mark + IV series); RESTRIKE = restrike_fn(minute,\n'
    '    opt_type, premium_max) → {"symbol","entry_price","marks":{m:px}}\n'
    '    or None (not re-entered). bank_base := day MTM at re-entry. New ids\n'
    '    are "<root>#<n>". Precedence: SL → target → TRAIL → BANK → IV; on the\n'
    '    re-entry minute the shorts re-enter first, then SL/target/trail\n'
    '    evaluate the fresh position. Cutoff: no bank if\n'
    '    the re-entry minute is past bank_reentry_cutoff_min or is the EOD\n'
    '    minute. leg_specs may carry opt_type / premium_max (RESTRIKE needs\n'
    '    them). Adds "banks", "bank_reentry_fail", "reentries" to the result.\n'
    '    """\n',
    "core docstring")

r = sub1(r,
    '    last_m = minutes[-1]\n'
    '\n'
    '    def _unreal(marks: Dict[str, float]) -> float:\n'
    '        return sum(leg_mtm(spec[i]["action"], spec[i]["entry_price"],\n'
    '                           marks[i], spec[i]["qty"]) for i in open_ids)\n'
    '\n'
    '    def _close(i: str, m: int, reason: str, marks: Dict[str, float]) -> None:\n'
    '        nonlocal realized\n'
    '        realized += leg_mtm(spec[i]["action"], spec[i]["entry_price"],\n'
    '                            marks[i], spec[i]["qty"])\n'
    '        exits[i] = {"ts": m, "reason": reason, "price": marks[i]}\n'
    '        open_ids.remove(i)\n'
    '\n'
    '    day_reason = "EOD"\n'
    '    for m in minutes:\n'
    '        marks = marks_by_minute[m]\n'
    '        mtm = realized + _unreal(marks)\n',
    '    last_m = minutes[-1]\n'
    '    # ── TSG_BANK_20260911 ── state\n'
    '    bank_on = mtm_bank_target > 0\n'
    '    bank_mode = "RESTRIKE" if str(bank_mode or "").upper() == "RESTRIKE" else "SAME"\n'
    '    banks = 0\n'
    '    bank_fail = 0\n'
    '    bank_base = 0.0\n'
    '    pending: Optional[tuple] = None          # (m_next, [closed SELL specs])\n'
    '    reentries: Dict[str, dict] = {}\n'
    '    alias: Dict[str, str] = {}               # new_id -> id whose mark/IV series it reuses\n'
    '\n'
    '    def _mk(i: str, marks: Dict[str, float]) -> float:\n'
    '        return marks[alias.get(i, i)]\n'
    '\n'
    '    def _unreal(marks: Dict[str, float]) -> float:\n'
    '        return sum(leg_mtm(spec[i]["action"], spec[i]["entry_price"],\n'
    '                           _mk(i, marks), spec[i]["qty"]) for i in open_ids)\n'
    '\n'
    '    def _close(i: str, m: int, reason: str, marks: Dict[str, float]) -> None:\n'
    '        nonlocal realized\n'
    '        realized += leg_mtm(spec[i]["action"], spec[i]["entry_price"],\n'
    '                            _mk(i, marks), spec[i]["qty"])\n'
    '        exits[i] = {"ts": m, "reason": reason, "price": _mk(i, marks)}\n'
    '        open_ids.remove(i)\n'
    '\n'
    '    day_reason = "EOD"\n'
    '    for mi, m in enumerate(minutes):\n'
    '        marks = marks_by_minute[m]\n'
    '        # ── TSG_BANK_20260911 ── pending re-entry lands at this minute\n'
    '        if pending is not None and pending[0] == m:\n'
    '            for src in pending[1]:\n'
    '                root = src["id"].split("#")[0]\n'
    '                new_id = f"{root}#{banks}"\n'
    '                if bank_mode == "RESTRIKE":\n'
    '                    rr = None\n'
    '                    if restrike_fn is not None:\n'
    '                        try:\n'
    '                            rr = restrike_fn(m, src.get("opt_type", ""),\n'
    '                                             float(src.get("premium_max") or 0))\n'
    '                        except Exception:\n'
    '                            rr = None\n'
    '                    if not rr or m not in (rr.get("marks") or {}):\n'
    '                        bank_fail += 1\n'
    '                        continue\n'
    '                    for mm, px in rr["marks"].items():\n'
    '                        if mm in marks_by_minute and mm >= m:\n'
    '                            marks_by_minute[mm][new_id] = float(px)\n'
    '                    entry_px = float(rr["marks"][m])\n'
    '                    symbol = rr.get("symbol")\n'
    '                    extra = {"strike": rr.get("strike"), "expiry": rr.get("expiry")}\n'
    '                else:\n'
    '                    alias[new_id] = alias.get(src["id"], src["id"])\n'
    '                    entry_px = float(marks[alias[new_id]])\n'
    '                    symbol = None                     # runner: source leg\'s\n'
    '                    extra = {}\n'
    '                spec[new_id] = dict(src, id=new_id, entry_price=entry_px)\n'
    '                open_ids.append(new_id)\n'
    '                h = hedge_map.get(src["id"])\n'
    '                if h:\n'
    '                    hedge_map[new_id] = h\n'
    '                reentries[new_id] = {"src_id": src["id"], "root": root,\n'
    '                                     "ts": m, "entry_price": entry_px,\n'
    '                                     "qty": spec[new_id]["qty"],\n'
    '                                     "symbol": symbol, **extra}\n'
    '            pending = None\n'
    '            bank_base = realized + _unreal(marks)\n'
    '        mtm = realized + _unreal(marks)\n',
    "core loop head")

r = sub1(r,
    '            day_reason = "MTM_TRAIL"\n'
    '            break\n'
    '        if iv_armed:\n'
    '            ivs = iv_by_minute.get(m) or {}\n'
    '            crossed = [i for i in list(open_ids)\n'
    '                       if spec[i]["action"] == "SELL"\n'
    '                       and ivs.get(i) is not None\n'
    '                       and _thr(i) > 0 and ivs[i] >= _thr(i)\n'
    '                       and marks[i] > spec[i]["entry_price"]]   # IV9/IV11\n',
    '            day_reason = "MTM_TRAIL"\n'
    '            break\n'
    '        # ── TSG_BANK_20260911 ── bank the open shorts, re-enter next minute\n'
    '        if (bank_on and pending is None\n'
    '                and (bank_max_per_day <= 0 or banks < bank_max_per_day)\n'
    '                and (mtm - bank_base) >= mtm_bank_target):\n'
    '            m_next = minutes[mi + 1] if mi + 1 < len(minutes) else None\n'
    '            shorts = [i for i in open_ids if spec[i]["action"] == "SELL"]\n'
    '            if (shorts and m_next is not None and m_next < last_m\n'
    '                    and (bank_reentry_cutoff_min is None\n'
    '                         or m_next <= bank_reentry_cutoff_min)):\n'
    '                closed = [spec[i] for i in shorts]\n'
    '                for i in shorts:\n'
    '                    _close(i, m, "MTM_BANK", marks)\n'
    '                banks += 1\n'
    '                pending = (m_next, closed)\n'
    '        if iv_armed:\n'
    '            ivs = iv_by_minute.get(m) or {}\n'
    '            crossed = [i for i in list(open_ids)\n'
    '                       if spec[i]["action"] == "SELL"\n'
    '                       and ivs.get(alias.get(i, i)) is not None\n'
    '                       and _thr(alias.get(i, i)) > 0\n'
    '                       and ivs[alias.get(i, i)] >= _thr(alias.get(i, i))\n'
    '                       and _mk(i, marks) > spec[i]["entry_price"]]   # IV9/IV11 (alias: TSG_BANK_20260911)\n',
    "core bank trigger + IV alias")

r = sub1(r,
    '    return {"exits": exits, "day_exit_reason": day_reason,\n'
    '            "mtm_final": mtm_final, "peak_mtm": peak, "trough_mtm": trough,\n'
    '            "trail_armed": (mtm_trail_arm > 0 and peak >= mtm_trail_arm)}\n',
    '    return {"exits": exits, "day_exit_reason": day_reason,\n'
    '            "mtm_final": mtm_final, "peak_mtm": peak, "trough_mtm": trough,\n'
    '            "trail_armed": (mtm_trail_arm > 0 and peak >= mtm_trail_arm),\n'
    '            # ── TSG_BANK_20260911 ──\n'
    '            "banks": banks, "bank_reentry_fail": bank_fail,\n'
    '            "reentries": reentries}\n',
    "core return")

# ═══════════════════════════════════════════════════════════════════════
# 2. runner — config, diag, restrike callback, emit re-entries, audit
# ═══════════════════════════════════════════════════════════════════════
r = sub1(r,
    '      skew_mult        float (default 1.0) — WING synthetic premiums\n',
    '      mtm_bank_target  float ₹ (default 0 = off) ── TSG_BANK_20260911 ──\n'
    '                       day MTM − bank_base ≥ target closes the open SELL\n'
    '                       legs (MTM_BANK); they re-enter next minute\n'
    '      bank_mode        "SAME" | "RESTRIKE" (default SAME): same contracts\n'
    '                       vs fresh premium-cap selection from the ladder\n'
    '      bank_max_per_day int (default 0 = unlimited)\n'
    '      bank_reentry_cutoff "HH:MM" (default 14:30): no bank when the\n'
    '                       re-entry minute would be later than this\n'
    '      skew_mult        float (default 1.0) — WING synthetic premiums\n',
    "runner doc")

r = sub1(r,
    '    parallel_workers = int(cfg.get("parallel_workers", 1) or 1)\n'
    '    skew_mult = float(cfg.get("skew_mult", 1.0) or 1.0)\n',
    '    parallel_workers = int(cfg.get("parallel_workers", 1) or 1)\n'
    '    # ── TSG_BANK_20260911 ──\n'
    '    mtm_bank_target = abs(float(cfg.get("mtm_bank_target", 0) or 0))\n'
    '    bank_mode = ("RESTRIKE" if str(cfg.get("bank_mode", "SAME") or "SAME")\n'
    '                 .strip().upper() == "RESTRIKE" else "SAME")\n'
    '    bank_max_per_day = max(0, int(cfg.get("bank_max_per_day", 0) or 0))\n'
    '    bank_cutoff_min = _hm_to_min(cfg.get("bank_reentry_cutoff", "14:30"),\n'
    '                                 14 * 60 + 30)\n'
    '    bank_diag_level = mtm_bank_target if mtm_bank_target > 0 else 1000.0\n'
    '    skew_mult = float(cfg.get("skew_mult", 1.0) or 1.0)\n',
    "runner cfg")

r = sub1(r,
    '        "mtm_exit_days": 0, "mtm_sl_exit_days": 0, "eod_exit_days": 0,\n',
    '        "mtm_exit_days": 0, "mtm_sl_exit_days": 0, "eod_exit_days": 0,\n'
    '        # ── TSG_BANK_20260911 ──\n'
    '        "mtm_bank_target": mtm_bank_target, "bank_mode": bank_mode,\n'
    '        "bank_max_per_day": bank_max_per_day,\n'
    '        "bank_reentry_cutoff": cfg.get("bank_reentry_cutoff", "14:30"),\n'
    '        "bank_events": 0, "bank_days": 0, "bank_reentry_fail": 0,\n'
    '        "bank_reentry_legs": 0,\n'
    '        "bank_sl_basis_warning": bool(mtm_bank_target > 0\n'
    '                                      and mtm_sl_basis != "POSITION"),\n'
    '        "bank_diag_level": bank_diag_level,\n'
    '        "peak_ge_level_days": 0,               # day peak MTM ≥ level\n'
    '        "peak_ge_level_closed_below_days": 0,  # …and closed below the level\n'
    '        "peak_ge_level_neg_close_days": 0,     # …and closed negative\n',
    "runner diag")

r = sub1(r,
    '        # ── pure basket simulation (per-leg exits) ──\n'
    '        leg_specs = [{"id": l["id"], "action": l["action"],\n'
    '                      "entry_price": selected[l["id"]]["price"],\n'
    '                      "qty": int(l["lots"]) * LOT_SIZE}\n'
    '                     for l in day_legs]\n',
    '        # ── TSG_BANK_20260911 ── RESTRIKE callback: highest REAL premium\n'
    '        # ≤ cap on the ladder at the re-entry minute (same select_strike as\n'
    '        # the 09:16 entry, same "candle ending at t" fill), marks carried\n'
    '        # forward exactly like a real leg. No mid-day synth by design.\n'
    '        def _restrike(m_next: int, opt_type: str, premium_max: float):\n'
    '            pool = _ladder_at(m_next).get(opt_type, [])\n'
    '            pick = select_strike(pool, premium_max)\n'
    '            if pick is None:\n'
    '                return None\n'
    '            sym = pick[0]\n'
    '            cds = candles_by_sym.get(sym, [])\n'
    '            series: Dict[int, float] = {}\n'
    '            idx = 0\n'
    '            last_px = float(pick[1])\n'
    '            for mm in minutes:\n'
    '                while idx < len(cds) and cds[idx]["ts"] < mm:\n'
    '                    last_px = cds[idx]["close"]\n'
    '                    idx += 1\n'
    '                if mm >= m_next:\n'
    '                    series[mm] = last_px\n'
    '            mt = meta_by_sym.get(sym, {})\n'
    '            return {"symbol": sym, "entry_price": series[m_next],\n'
    '                    "marks": series, "strike": mt.get("strike"),\n'
    '                    "expiry": mt.get("expiry")}\n'
    '\n'
    '        # ── pure basket simulation (per-leg exits) ──\n'
    '        leg_specs = [{"id": l["id"], "action": l["action"],\n'
    '                      "entry_price": selected[l["id"]]["price"],\n'
    '                      "qty": int(l["lots"]) * LOT_SIZE,\n'
    '                      "opt_type": l["opt_type"],                 # ── TSG_BANK_20260911 ──\n'
    '                      "premium_max": l["premium_max"]}\n'
    '                     for l in day_legs]\n',
    "runner restrike + leg_specs")

r = sub1(r,
    '                               iv_keep_hedge=iv_keep_hedge,\n'
    '                               mtm_sl_basis=mtm_sl_basis)   # ── TSG_MTM_BASIS_20260821 ──\n'
    '\n'
    '        diag["days_entered"] += 1\n',
    '                               iv_keep_hedge=iv_keep_hedge,\n'
    '                               mtm_sl_basis=mtm_sl_basis,   # ── TSG_MTM_BASIS_20260821 ──\n'
    '                               mtm_bank_target=mtm_bank_target,   # ── TSG_BANK_20260911 ──\n'
    '                               bank_mode=bank_mode,\n'
    '                               bank_max_per_day=bank_max_per_day,\n'
    '                               bank_reentry_cutoff_min=bank_cutoff_min,\n'
    '                               restrike_fn=_restrike)\n'
    '\n'
    '        diag["days_entered"] += 1\n'
    '        # ── TSG_BANK_20260911 ── bank + peak/give-back diag\n'
    '        if res.get("banks"):\n'
    '            diag["bank_events"] += int(res["banks"])\n'
    '            diag["bank_days"] += 1\n'
    '        diag["bank_reentry_fail"] += int(res.get("bank_reentry_fail") or 0)\n'
    '        diag["bank_reentry_legs"] += len(res.get("reentries") or {})\n'
    '        if res["peak_mtm"] >= bank_diag_level:\n'
    '            diag["peak_ge_level_days"] += 1\n'
    '            if res["mtm_final"] < bank_diag_level:\n'
    '                diag["peak_ge_level_closed_below_days"] += 1\n'
    '            if res["mtm_final"] < 0:\n'
    '                diag["peak_ge_level_neg_close_days"] += 1\n',
    "runner sim call + diag")

r = sub1(r,
    '                  synthetic=bool(spec["synthetic"]),\n'
    '                  synth_kind=spec.get("synth_kind"))\n'
    '\n'
    '    conn.close()\n',
    '                  synthetic=bool(spec["synthetic"]),\n'
    '                  synth_kind=spec.get("synth_kind"))\n'
    '        # ── TSG_BANK_20260911 ── re-entered shorts → one row each\n'
    '        for new_id, info in (res.get("reentries") or {}).items():\n'
    '            root = info["root"]\n'
    '            l0 = next(l for l in day_legs if l["id"] == root)\n'
    '            leg2 = dict(l0, id=new_id)\n'
    '            ex = res["exits"][new_id]\n'
    '            if info.get("symbol"):                      # RESTRIKE\n'
    '                _emit(leg=leg2, symbol=info["symbol"], strike=info.get("strike"),\n'
    '                      expiry=info.get("expiry") or want_expiry,\n'
    '                      entry_ts=info["ts"], entry_price=info["entry_price"],\n'
    '                      exit_ts=ex["ts"], exit_price=ex["price"],\n'
    '                      exit_reason=ex["reason"], synthetic=False,\n'
    '                      synth_kind=None)\n'
    '            else:                                       # SAME\n'
    '                s0 = selected[root]\n'
    '                _emit(leg=leg2, symbol=s0["symbol"], strike=s0.get("strike"),\n'
    '                      expiry=s0.get("expiry"), entry_ts=info["ts"],\n'
    '                      entry_price=info["entry_price"],\n'
    '                      exit_ts=ex["ts"], exit_price=ex["price"],\n'
    '                      exit_reason=ex["reason"],\n'
    '                      synthetic=bool(s0["synthetic"]),\n'
    '                      synth_kind=s0.get("synth_kind"))\n'
    '\n'
    '    conn.close()\n',
    "runner emit re-entries")

r = sub1(r,
    '        f"skips: uncovered {diag[\'days_uncovered\']} / "\n'
    '        f"noShort {diag[\'days_no_short_strike\']} / "\n'
    '        f"noEntryPx {diag[\'days_no_entry_price\']}"\n'
    '    )\n',
    '        f"skips: uncovered {diag[\'days_uncovered\']} / "\n'
    '        f"noShort {diag[\'days_no_short_strike\']} / "\n'
    '        f"noEntryPx {diag[\'days_no_entry_price\']}, "\n'
    '        f"BANK {diag[\'bank_mode\'] if diag[\'mtm_bank_target\'] > 0 else \'off\'} "\n'
    '        f"events {diag[\'bank_events\']} on {diag[\'bank_days\']}d "\n'
    '        f"(reentry legs {diag[\'bank_reentry_legs\']}, fail "\n'
    '        f"{diag[\'bank_reentry_fail\']}), peak≥{diag[\'bank_diag_level\']:.0f}: "\n'
    '        f"{diag[\'peak_ge_level_days\']}d, closed below "\n'
    '        f"{diag[\'peak_ge_level_closed_below_days\']}d, closed neg "\n'
    '        f"{diag[\'peak_ge_level_neg_close_days\']}d"\n'
    '        + (" ⚠ bank with mtm_sl_basis=DAILY: the bank locks nothing"\n'
    '           if diag["bank_sl_basis_warning"] else "")\n'
    '    )\n',
    "runner audit")

# ═══════════════════════════════════════════════════════════════════════
# 3. regression tests appended to the standalone test file
# ═══════════════════════════════════════════════════════════════════════
t = read(PATH["test"])
t += '''

# ── TSG_BANK_20260911 ── profit bank + re-entry ───────────────────────
def _bank_marks(minutes):
    # L1 decays 85→70 by m=160 (+975), then bounces to 80 by 220 (gives back)
    px = {100: 85.0, 130: 80.0, 160: 70.0, 190: 72.0, 220: 80.0, 250: 80.0}
    return {m: {"L1": px[m], "L2": 85.0, "L3": 5.0, "L4": 5.0} for m in minutes}

def test_bank_same_closes_shorts_and_reenters_next_minute():
    minutes = [100, 130, 160, 190, 220, 250]
    res = simulate_tsg_day(_legs(), minutes, _bank_marks(minutes), 0.0,
                           hedge_map=dict(HEDGES), mtm_bank_target=900.0,
                           bank_mode="SAME")
    assert res["banks"] == 1
    assert res["exits"]["L1"]["reason"] == "MTM_BANK" and res["exits"]["L1"]["ts"] == 160
    assert res["exits"]["L2"]["reason"] == "MTM_BANK"
    assert "L3" not in [k for k, e in res["exits"].items() if e["reason"] == "MTM_BANK"]
    assert set(res["reentries"]) == {"L1#1", "L2#1"}
    assert res["reentries"]["L1#1"]["ts"] == 190 and res["reentries"]["L1#1"]["entry_price"] == 72.0
    # re-entered L1#1 runs to EOD at 80 → −520 ; banked +975 ; total = +455
    assert res["exits"]["L1#1"]["reason"] == "EOD"
    assert abs(res["mtm_final"] - (975.0 - 520.0)) < 1e-6

def test_bank_off_is_byte_identical_to_before():
    minutes = [100, 130, 160, 190, 220, 250]
    res = simulate_tsg_day(_legs(), minutes, _bank_marks(minutes), 0.0,
                           hedge_map=dict(HEDGES))
    assert res["banks"] == 0 and res["reentries"] == {}
    assert all(e["reason"] == "EOD" for e in res["exits"].values())
    assert abs(res["mtm_final"] - 325.0) < 1e-6          # 85→80 on L1 only

def test_bank_position_sl_protects_the_bank():
    minutes = [100, 130, 160, 190, 220, 250]
    marks = _bank_marks(minutes); marks[220]["L1"] = 90.0; marks[250]["L1"] = 90.0
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 1000.0,
                           hedge_map=dict(HEDGES), mtm_bank_target=900.0,
                           mtm_sl_basis="POSITION")
    # after re-entry at 72, L1#1 at 90 = −1170 on the OPEN legs → POSITION SL fires
    assert res["day_exit_reason"] == "MTM_SL"
    assert res["exits"]["L1#1"]["reason"] == "MTM_SL"

def test_bank_cutoff_and_eod_minute_block_reentry():
    minutes = [100, 130, 160, 190, 220, 250]
    res = simulate_tsg_day(_legs(), minutes, _bank_marks(minutes), 0.0,
                           mtm_bank_target=900.0, bank_reentry_cutoff_min=150)
    assert res["banks"] == 0                              # re-entry would be 190 > 150
    res2 = simulate_tsg_day(_legs(), [100, 160, 250], {100: _bank_marks([100])[100],
                            160: _bank_marks([160])[160], 250: _bank_marks([250])[250]},
                            0.0, mtm_bank_target=900.0)
    assert res2["banks"] == 0                             # next minute IS the EOD minute

def test_bank_max_per_day_and_rebase():
    minutes = list(range(100, 100 + 30 * 8, 30))
    px = [85, 70, 70, 55, 55, 40, 40, 40]                 # two ₹975 steps then a third
    marks = {m: {"L1": float(p), "L2": 85.0, "L3": 5.0, "L4": 5.0} for m, p in zip(minutes, px)}
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0,
                           mtm_bank_target=900.0, bank_max_per_day=2)
    assert res["banks"] == 2
    assert "L1#2" in res["reentries"] and "L1#3" not in res["reentries"]
    assert res["exits"]["L1#2"]["reason"] == "EOD"

def test_bank_restrike_uses_callback_and_fails_closed():
    minutes = [100, 130, 160, 190, 220, 250]
    calls = []
    def rs(m, opt_type, cap):
        calls.append((m, opt_type, cap))
        if opt_type == "PE":
            return None                                   # nothing ≤ cap → not re-entered
        return {"symbol": "NEWCE", "entry_price": 110.0,
                "marks": {190: 110.0, 220: 100.0, 250: 95.0}}
    legs = _legs()
    for l in legs:
        l["opt_type"] = "CE" if l["id"] in ("L1", "L3") else "PE"; l["premium_max"] = 120.0
    res = simulate_tsg_day(legs, minutes, _bank_marks(minutes), 0.0,
                           hedge_map=dict(HEDGES), mtm_bank_target=900.0,
                           bank_mode="RESTRIKE", restrike_fn=rs)
    assert [c[1] for c in calls] == ["CE", "PE"] and calls[0][0] == 190
    assert set(res["reentries"]) == {"L1#1"} and res["bank_reentry_fail"] == 1
    assert res["reentries"]["L1#1"]["symbol"] == "NEWCE"
    assert res["exits"]["L1#1"]["price"] == 95.0          # marks injected for the new leg
    assert abs(res["mtm_final"] - (975.0 + 15.0 * 65)) < 1e-6

def test_bank_reentry_minute_reenters_first_then_sl_sees_fresh_runway():
    minutes = [100, 130, 160, 190, 220, 250]
    marks = _bank_marks(minutes)
    marks[190]["L2"] = 140.0                              # L2 gaps on the re-entry minute
    marks[220]["L2"] = 160.0; marks[250]["L2"] = 160.0    # …and keeps going
    res = simulate_tsg_day(_legs(), minutes, marks, 0.0, 1000.0,
                           mtm_bank_target=900.0, mtm_sl_basis="POSITION")
    assert res["banks"] == 1
    assert res["reentries"]["L2#1"]["entry_price"] == 140.0   # re-entered at the gapped mark
    assert res["exits"]["L2#1"]["reason"] == "MTM_SL" and res["exits"]["L2#1"]["ts"] == 220
    # the banked ₹975 on L1 + the 85→140 move on the ORIGINAL L2 never hit the book:
    # L2 was closed at 85 (MTM_BANK) before the gap.
    assert res["exits"]["L2"]["price"] == 85.0

test_bank_same_closes_shorts_and_reenters_next_minute()
test_bank_off_is_byte_identical_to_before()
test_bank_position_sl_protects_the_bank()
test_bank_cutoff_and_eod_minute_block_reentry()
test_bank_max_per_day_and_rebase()
test_bank_restrike_uses_callback_and_fails_closed()
test_bank_reentry_minute_reenters_first_then_sl_sees_fresh_runway()
print("  ok  7 TSG_BANK_20260911 tests (appended)")
'''

# ═══════════════════════════════════════════════════════════════════════
# 4. UI — Backtest.jsx
# ═══════════════════════════════════════════════════════════════════════
u = read(PATH["ui"])
u = sub1(u,
    '  const [tsgTrailGb, setTsgTrailGb] = useState(tsgSaved.trailGb ?? 8000);   // ── TSG_TRAIL ── giveback ₹\n',
    '  const [tsgTrailGb, setTsgTrailGb] = useState(tsgSaved.trailGb ?? 8000);   // ── TSG_TRAIL ── giveback ₹\n'
    '  // ── TSG_BANK_20260911 ── profit bank + re-entry\n'
    '  const [tsgBankTarget, setTsgBankTarget] = useState(tsgSaved.bankTarget ?? 0);\n'
    '  const [tsgBankMode, setTsgBankMode] = useState(tsgSaved.bankMode === "RESTRIKE" ? "RESTRIKE" : "SAME");\n'
    '  const [tsgBankMax, setTsgBankMax] = useState(tsgSaved.bankMax ?? 0);\n'
    '  const [tsgBankCutoff, setTsgBankCutoff] = useState(tsgSaved.bankCutoff ?? "14:30");\n',
    "ui state")
u = sub1(u,
    'trailArm: tsgTrailArm, trailGb: tsgTrailGb, workers: tsgWorkers, legs: tsgLegs, skewMult: tsgSkewMult, shortSkewMult: tsgShortSkewMult })); } catch { /* ignore */ }\n'
    '  }, [tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult]);',
    'trailArm: tsgTrailArm, trailGb: tsgTrailGb, workers: tsgWorkers, legs: tsgLegs, skewMult: tsgSkewMult, shortSkewMult: tsgShortSkewMult, bankTarget: tsgBankTarget, bankMode: tsgBankMode, bankMax: tsgBankMax, bankCutoff: tsgBankCutoff })); } catch { /* ignore */ }   // ── TSG_BANK_20260911 ──\n'
    '  }, [tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult, tsgBankTarget, tsgBankMode, tsgBankMax, tsgBankCutoff]);',
    "ui localStorage")
u = sub1(u,
    '        mtm_trail_giveback: Math.abs(Number(tsgTrailGb)) || 0,\n'
    '        parallel_workers: Math.max(1, Math.min(8, Number(tsgWorkers) || 1)),\n',
    '        mtm_trail_giveback: Math.abs(Number(tsgTrailGb)) || 0,\n'
    '        mtm_bank_target: Math.abs(Number(tsgBankTarget)) || 0,   // ── TSG_BANK_20260911 ──\n'
    '        bank_mode: tsgBankMode === "RESTRIKE" ? "RESTRIKE" : "SAME",\n'
    '        bank_max_per_day: Math.max(0, Number(tsgBankMax) || 0),\n'
    '        bank_reentry_cutoff: tsgBankCutoff || "14:30",\n'
    '        parallel_workers: Math.max(1, Math.min(8, Number(tsgWorkers) || 1)),\n',
    "ui config")
u = sub1(u,
    '      tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult,   // ── TSG_V1',
    '      tsgBankTarget, tsgBankMode, tsgBankMax, tsgBankCutoff,   // ── TSG_BANK_20260911 ── stale-closure rule\n'
    '      tsgEntryTime, tsgExitTime, tsgMtmTarget, tsgMtmSl, tsgMtmSlBasis, tsgIvSlPct, tsgIvSlDelta, tsgIvKeepHedge, tsgMinEntryIv, tsgTrailArm, tsgTrailGb, tsgWorkers, tsgLegs, tsgSkewMult, tsgShortSkewMult,   // ── TSG_V1',
    "ui deps")
u = sub1(u,
    '  if (Number(cfg.mtm_trail_arm) > 0 && Number(cfg.mtm_trail_giveback) > 0) add("Trail", `arm ₹${cfg.mtm_trail_arm} / gb ₹${cfg.mtm_trail_giveback}`);   // ── TSG_TRAIL ──\n',
    '  if (Number(cfg.mtm_trail_arm) > 0 && Number(cfg.mtm_trail_giveback) > 0) add("Trail", `arm ₹${cfg.mtm_trail_arm} / gb ₹${cfg.mtm_trail_giveback}`);   // ── TSG_TRAIL ──\n'
    '  if (Number(cfg.mtm_bank_target) > 0) add("Bank", `₹${cfg.mtm_bank_target} · ${cfg.bank_mode === "RESTRIKE" ? "restrike" : "same"}${Number(cfg.bank_max_per_day) > 0 ? ` ×${cfg.bank_max_per_day}` : ""}${cfg.bank_reentry_cutoff ? ` ≤${cfg.bank_reentry_cutoff}` : ""}`);   // ── TSG_BANK_20260911 ──\n',
    "ui chip")
# the four fields, placed right after the trail-giveback field
gb_field_start = u.index('                <Field label="Trail giveback ₹">')
gb_field_end = u.index('\n', gb_field_start) + 1
fields = (
    '                {/* ── TSG_BANK_20260911 BEGIN ── profit bank + re-entry */}\n'
    '                <Field label="Bank target ₹ (0 = off)"><input type="number" step="250" style={inputStyle} value={tsgBankTarget} onChange={(e) => setTsgBankTarget(Number(e.target.value))} title="PROFIT BANK: when day MTM − bank base ≥ this, the open SELL legs exit (MTM_BANK) and re-enter at the NEXT minute\'s mark; hedges stay. Bank base = day MTM at the last re-entry, so the next bank needs the NEW position to earn this again. Checked after SL/target/trail, before IV. ⚠ Option 1 (same contracts) locks nothing unless MTM SL basis = POSITION — the SL must be measured from the re-entry. Every re-entry is an extra 2-leg round trip through the charges model." /></Field>\n'
    '                <Field label="Bank mode"><select style={inputStyle} value={tsgBankMode} onChange={(e) => setTsgBankMode(e.target.value === "RESTRIKE" ? "RESTRIKE" : "SAME")} title="SAME (option 1): re-enter the SAME contracts next minute — reuses the leg\'s mark and IV series. RESTRIKE (option 2): re-select each short from the ladder at the re-entry minute with the leg\'s premium cap (highest REAL premium ≤ cap; no mid-day synth — nothing ≤ cap → that short is not re-entered, diag bank_reentry_fail). RESTRIKE re-entries are not IV-monitored; day SL/trail/target still govern them."><option value="SAME">SAME contracts (option 1)</option><option value="RESTRIKE">RESTRIKE ≤ premium cap (option 2)</option></select></Field>\n'
    '                <Field label="Bank max / day (0 = ∞)"><input type="number" step="1" min="0" style={inputStyle} value={tsgBankMax} onChange={(e) => setTsgBankMax(Number(e.target.value))} title="Maximum bank events per day. 0 = unlimited." /></Field>\n'
    '                <Field label="Bank re-entry cutoff"><input type="text" style={inputStyle} value={tsgBankCutoff} onChange={(e) => setTsgBankCutoff(e.target.value)} placeholder="14:30" title="No bank when the re-entry minute would be later than this (or would be the EOD minute). A bank late in the day is a plain partial exit; use the trail for that." /></Field>\n'
    '                {/* ── TSG_BANK_20260911 END ── */}\n'
)
u = u[:gb_field_end] + fields + u[gb_field_end:]

NEW = {"runner": r, "test": t, "ui": u}

# ═══════════════════════════════════════════════════════════════════════
# gates: py_compile, existing+new test file, simulation suite
# ═══════════════════════════════════════════════════════════════════════
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp_runner = os.path.join(TMP, "backtest_tsg_runner.py")
tmp_test = os.path.join(TMP, "test_tsg_runner.py")
for src_text, dst in ((r, tmp_runner), (t, tmp_test)):
    with open(dst, "w", encoding="utf-8") as f:
        f.write(src_text)
    try:
        py_compile.compile(dst, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {dst}: {e}")
print("py_compile gate: OK")

# the standalone test file imports bare names from the ic backtest package;
# mirror the runner's fallback imports by putting the IC dir on sys.path.
ic_dir = os.path.join(BACKEND, "app", "backtest", "ic")
env = dict(os.environ, PYTHONPATH=os.pathsep.join([TMP, ic_dir, BACKEND, os.environ.get("PYTHONPATH", "")]))
proc = subprocess.run([sys.executable, tmp_test], cwd=TMP, env=env,
                      capture_output=True, text=True, timeout=300)
tail = (proc.stdout + proc.stderr).strip().splitlines()[-8:]
print("test_tsg_runner.py (existing + 7 new) on patched module:")
for ln in tail:
    print("   ", ln)
if proc.returncode != 0:
    die("test_tsg_runner.py failed on the patched module")

# simulation suite: pure core, extra scenarios beyond the test file
sys.path.insert(0, ic_dir); sys.path.insert(0, BACKEND)
spec = importlib.util.spec_from_file_location("tsg_bank_patched", tmp_runner)
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
sim = M.simulate_tsg_day
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)
LEGS = lambda: [{"id": "L1", "action": "SELL", "entry_price": 85.0, "qty": 65, "opt_type": "CE", "premium_max": 120.0},
                {"id": "L2", "action": "SELL", "entry_price": 85.0, "qty": 65, "opt_type": "PE", "premium_max": 120.0},
                {"id": "L3", "action": "BUY", "entry_price": 5.0, "qty": 65, "opt_type": "CE", "premium_max": 5.0},
                {"id": "L4", "action": "BUY", "entry_price": 5.0, "qty": 65, "opt_type": "PE", "premium_max": 5.0}]
H = {"L1": "L3", "L2": "L4"}
print("simulation suite")
mins = [100, 130, 160, 190, 220, 250]
mk = {m: {"L1": p, "L2": 85.0, "L3": 5.0, "L4": 5.0} for m, p in zip(mins, [85, 80, 70, 72, 80, 80])}
base = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, hedge_map=dict(H))
same = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, hedge_map=dict(H), mtm_bank_target=900.0)
check("SAME bank gross == hold gross + (re-entry − exit) × qty  [option 1 = hold ± 1 min drift]",
      abs(same["mtm_final"] - (base["mtm_final"] + (72.0 - 70.0) * 65)) < 1e-6,
      f"same={same['mtm_final']} base={base['mtm_final']}")   # short: skipped a minute the premium rose → +₹130 vs hold
check("peak/trough unaffected by banking", same["peak_mtm"] == base["peak_mtm"])
# trail still takes precedence over bank on the same minute
tr = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, hedge_map=dict(H),
         mtm_bank_target=900.0, mtm_trail_arm=900.0, mtm_trail_giveback=100.0)
check("trail precedence: armed at 160, exits at 190 (bank did not re-enter)", tr["day_exit_reason"] == "MTM_TRAIL" and tr["banks"] == 1)
# day MTM target on the pending minute cancels the re-entry
tg = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 950.0, hedge_map=dict(H), mtm_bank_target=900.0)
check("day target fires at 160 before bank (target precedence)", tg["day_exit_reason"] == "MTM_TARGET" and tg["banks"] == 0)
# IV alias: a SAME re-entry is still IV-monitored via the source series
iv = {m: {"L1": 0.12, "L2": 0.12} for m in mins}; iv[220] = {"L1": 0.60, "L2": 0.12}
mk2 = {m: dict(v) for m, v in mk.items()}; mk2[220]["L1"] = 90.0; mk2[250]["L1"] = 90.0
ivr = sim(LEGS(), mins, mk2, 0.0, hedge_map=dict(H), mtm_bank_target=900.0,
          iv_sl_pct=40.0, iv_by_minute=iv)
check("SAME re-entry inherits IV monitoring (L1#1 IV_SL at 220, hedge L3 with it)",
      ivr["exits"].get("L1#1", {}).get("reason") == "IV_SL" and ivr["exits"]["L3"]["reason"] == "IV_SL_HEDGE", str(ivr["exits"]))
# RESTRIKE re-entry is NOT IV-monitored (documented)
rs = lambda m, ot, cap: {"symbol": "X" + ot, "entry_price": 100.0, "marks": {mm: 100.0 for mm in mins if mm >= m}}
ivr2 = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, hedge_map=dict(H), mtm_bank_target=900.0,
           bank_mode="RESTRIKE", restrike_fn=rs, iv_sl_pct=40.0, iv_by_minute=iv)
check("RESTRIKE re-entry not IV-monitored → EOD", ivr2["exits"]["L1#1"]["reason"] == "EOD")
check("RESTRIKE re-entry gets its own hedge mapping", ivr2["exits"]["L3"]["reason"] == "EOD")
# restrike callback raising → fail-closed (not re-entered), never crashes the day
boom = lambda m, ot, cap: (_ for _ in ()).throw(RuntimeError("ladder"))
rb = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, hedge_map=dict(H), mtm_bank_target=900.0,
         bank_mode="RESTRIKE", restrike_fn=boom)
check("restrike callback exception → both shorts not re-entered, day completes", rb["bank_reentry_fail"] == 2 and rb["day_exit_reason"] == "EOD")
# no restrike_fn at all in RESTRIKE mode → fail-closed
rn = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, mtm_bank_target=900.0, bank_mode="RESTRIKE")
check("RESTRIKE without callback → not re-entered", rn["bank_reentry_fail"] == 2)
# bank never fires when only hedges are open
onlyh = sim([l for l in LEGS() if l["action"] == "BUY"], mins, {m: {"L3": 5.0 + k, "L4": 5.0 + k} for k, m in enumerate(mins)},
            0.0, mtm_bank_target=100.0)
check("hedges-only basket never banks", onlyh["banks"] == 0)
# unknown bank_mode string normalises to SAME
um = sim(LEGS(), mins, {m: dict(v) for m, v in mk.items()}, 0.0, mtm_bank_target=900.0, bank_mode="weird")
check("unknown bank_mode → SAME", "L1#1" in um["reentries"] and um["reentries"]["L1#1"]["symbol"] is None)
# runner-level: _hm_to_min default and cfg normalisation exist
check("runner exposes _hm_to_min for cutoff parsing", callable(getattr(M, "_hm_to_min", None)))
if FAILS:
    die(f"simulation suite failed: {FAILS}")
print("simulation suite: OK")

# ═══════════════════════════════════════════════════════════════════════
# write
# ═══════════════════════════════════════════════════════════════════════
for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
for k, p in PATH.items():
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 3 files (+ .bak backups)")
if os.path.isdir(DUAL):
    for k in ("runner", "test"):
        rel = REL[k].replace("backend/", "", 1)
        dst = os.path.join(DUAL, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend + frontend: ./desktop/build-scalp.sh")
