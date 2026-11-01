#!/usr/bin/env python3
# apply_ic_adj_fill_open.py — ── IC_ADJ_FILL_OPEN_20261101 ──
#
# IC_V2 backtest: the ADJUSTMENT leg (L1·ADJ / L2·ADJ, bought after a short's
# stop) is now picked and bought at the OPEN of the activation minute — where
# paper/live buy (~adjust_delay_s after the stop, part-way into that minute).
# Until now the runner picked and bought at that minute's CLOSE: on the 5 paper
# adjustments of 16–29 Oct the close was dearer every time (mean −5.5 pts vs
# paper; the open −0.85). Filled at the open, the rest of that minute's candle
# is watched for the ADJ leg's own SL / TP (live watches from its fill).
#
#   Config adjust_fill: "open" (DEFAULT — new runs, matches live)
#                       "close" (every run before 1 Nov — reproduces them exactly)
#   Backtest → IC_V2 form → "ADJ buy price": MINUTE OPEN (matches live) /
#   MINUTE CLOSE (older runs). Saved with the other IC fields; run chip "ADJ fill".
#   Shorts, wings, entries and stops are unchanged; IC_V1 (no adjustments) too.
#   !! IC_V2 results change (ADJ legs only). Re-run the seal in both modes.
#
#   EDIT backend/app/backtest/ic/ic_v1_engine.py (4) · backtest_ic_runner.py (8)
#        backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py (1 — pins "close":
#        it compares against the pre-change runner) · frontend/src/pages/Backtest.jsx (6)
#   NEW  backend/app/backtest/ic/test_ic_adjust_fill.py (30 checks)
#   Build copies under desktop/src-tauri/ are mirrored when present.
#
#   cd <repo root> && python3 apply_ic_adj_fill_open.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh both      (outside market hours)
#
# Run ONLY this script — it carries every file it writes.

import os
import shutil
import subprocess
import sys

FENCE = 'IC_ADJ_FILL_OPEN_20261101'
NEW_FILES = {'backend/app/backtest/ic/test_ic_adjust_fill.py': '# backend/app/backtest/ic/test_ic_adjust_fill.py — fence IC_ADJ_FILL_OPEN_20261101\n#\n# IC_V2 adjustment buy price (config key adjust_fill):\n#   "open"  (default) strike pick + fill at the OPEN of the activation minute,\n#           that minute\'s candle is watched (the fill came first)\n#   "close" pick + fill at that candle\'s CLOSE, watched from the next candle —\n#           exactly the runner before 1 Nov 2026\n#\n#   engine  1. spike candle stops the short IN that minute at exactly the SL\n#              (high ≥ SL, close below it) — the existing intrabar check\n#           2. the adjustment follows from that minute (+ adjust_delay_s)\n#           3. open → fill = open; close → fill = close; engine default = close\n#           4. open watches the activation candle: its low hits the ADJ SL →\n#              stopped in that minute AT the SL; close mode is not\n#           5. target mirror: activation-candle high hits the ADJ TP → TP\n#           6. synthetic adjustment (fill_price, no candles) identical in both\n#   runner  7. key absent ≡ "open" (row-for-row)\n#           8. "close" ≡ ORIGINAL runner (.bak), diag keys identical\n#           9. open vs close: shorts + wings identical, only ·ADJ legs move\n#          10. every ·ADJ entry = open (resp. close) of its own candle, and the\n#              strike is the highest such price ≤ cap at that minute\n#          11. diag adjust_fill only in open mode; IC_V1 (no adjust) identical\n#\n# Synthetic corpus in a temp dir; never touches backtest.db.\n# Run: cd backend && python3 app/backtest/ic/test_ic_adjust_fill.py\n\nfrom __future__ import annotations\n\nimport atexit\nimport importlib.util\nimport os\nimport shutil\nimport sqlite3\nimport sys\nimport tempfile\nfrom datetime import date, timedelta\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve()\nBACKEND = str(HERE.parents[3])\nif BACKEND not in sys.path:\n    sys.path.insert(0, BACKEND)\nos.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")\n\nFENCE = "IC_ADJ_FILL_OPEN_20261101"\nFAILS: list = []\nOK = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\n# ───────────────────────────── engine ─────────────────────────────\nE = 1_793_000_000 - 1_793_000_000 % 60          # a real epoch minute (real-clock rule)\n\n\ndef c(ts, o, h, l, cl):\n    return {"ts": ts, "open": o, "high": h, "low": l, "close": cl}\n\n\ndef t_engine():\n    print("── 1–6. engine ──")\n    from app.backtest.ic.ic_v1_engine import simulate_session, norm_leg, norm_adjust\n    short = [norm_leg({"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 1, "premium_max": 100,\n                       "sl_val": 42, "sl_mode": "pct"})]\n    s_c = [c(E - 60, 80, 80, 80, 80), c(E, 80, 84, 79, 82),\n           c(E + 60, 83, 120, 82, 105),             # high 120 ≥ SL 113.6, close 105 below it\n           c(E + 120, 105, 108, 101, 106), c(E + 180, 106, 107, 104, 105)]\n    acfg = {"L1": norm_adjust({"lots": 1, "premium_max": 110, "sl_val": 30, "tp_val": 100})}\n\n    def run(adj_candles, fill=None, picks_extra=None):\n        pick = {"symbol": "ADJ", "strike": 23000, "expiry": "2026-11-03", "candles": adj_candles}\n        pick.update(picks_extra or {})\n        kw = {} if fill is None else {"adjust_fill": fill}\n        r = simulate_session(short, {"L1": [dict(x) for x in s_c]}, {"L1": "S"}, E, None, exit_mode="NEXT_OPEN",\n                             adjust_on_sl=True, adjust_cfg=acfg, adjust_picks={"L1": pick}, **kw)\n        tr = {t["leg"]: t for t in r["trades"]}\n        co = r["carry_out"]\n        return tr, co\n\n    quiet = [c(E + 120, 100, 104, 99, 104), c(E + 180, 104, 106, 103, 105), c(E + 240, 105, 105, 104, 104)]\n    for fill in ("open", "close"):\n        tr, co = run(quiet, fill)\n        s = tr["L1"]\n        check(f"[{fill}] spike minute stops the short in THAT minute", s["exit_ts"] == E + 60 and s["exit_reason"] == "SL",\n              (s["exit_ts"] - E, s["exit_reason"]))\n        check(f"[{fill}] …at exactly the SL price (113.6), not the high or the close",\n              abs(s["exit_price"] - 80 * 1.42) < 1e-9, s["exit_price"])\n        a = co.get("L1A") or tr.get("L1A")\n        check(f"[{fill}] adjustment opens at stop minute + 60 s", a and a["entry_ts"] == E + 120, a and a["entry_ts"] - E)\n        want = 100.0 if fill == "open" else 104.0\n        check(f"[{fill}] adjustment filled at the candle {fill.upper()} ({want})", a and a["entry_price"] == want,\n              a and a["entry_price"])\n    tr, co = run(quiet)\n    a = co.get("L1A") or tr.get("L1A")\n    check("engine default (no kwarg) = close — every pre-1-Nov caller unchanged", a and a["entry_price"] == 104.0,\n          a and a["entry_price"])\n\n    dip = [c(E + 120, 100, 104, 69, 104), c(E + 180, 104, 106, 103, 105), c(E + 240, 105, 105, 104, 104)]\n    tr, co = run(dip, "open")\n    a = tr.get("L1A")\n    check("[open] activation candle low 69 ≤ ADJ SL 70 → stopped IN that minute",\n          a is not None and a["exit_ts"] == E + 120 and a["exit_reason"] == "SL", a and (a["exit_ts"] - E, a["exit_reason"]))\n    check("[open] …at exactly the ADJ SL price (70.0)", a is not None and abs(a["exit_price"] - 70.0) < 1e-9,\n          a and a["exit_price"])\n    tr, co = run(dip, "close")\n    check("[close] same candles: the activation candle is NOT watched (no exit there)",\n          "L1A" in co or (tr.get("L1A") and tr["L1A"]["exit_ts"] != E + 120), tr.get("L1A"))\n\n    spike = [c(E + 120, 100, 205, 99, 120), c(E + 180, 120, 121, 119, 120), c(E + 240, 120, 120, 119, 119)]\n    tr, co = run(spike, "open")\n    a = tr.get("L1A")\n    check("[open] target mirror: activation high 205 ≥ TP 200 → TP in that minute at 200",\n          a is not None and a["exit_reason"] == "TP" and a["exit_ts"] == E + 120 and abs(a["exit_price"] - 200.0) < 1e-9,\n          a and (a["exit_reason"], a["exit_ts"] - E, a["exit_price"]))\n    tr, co = run(spike, "close")\n    check("[close] target mirror off: no TP in the activation minute",\n          not (tr.get("L1A") and tr["L1A"]["exit_ts"] == E + 120 and tr["L1A"]["exit_reason"] == "TP"))\n\n    out = {}\n    for fill in ("open", "close"):\n        tr, co = run([], fill, {"symbol": "SYN-NIFTY-23000CE", "fill_price": 91.5, "synthetic": True,\n                                "synth_kind": "adjust"})\n        out[fill] = (tr, co)\n        a = co.get("L1A") or tr.get("L1A")\n        check(f"[{fill}] synthetic adjustment (no range) books its fill_price", a and a["entry_price"] == 91.5,\n              a and a["entry_price"])\n    check("synthetic adjustment identical in both modes", out["open"] == out["close"])\n\n\n# ───────────────────────────── runner ─────────────────────────────\ndef t_runner():\n    print("── 7–11. runner (synthetic corpus) ──")\n    from app.utils.market_hours import is_trading_day\n    import app.backtest.data.candle_source as _cs\n    from app.backtest.ic.backtest_ic_runner import run_ic_backtest as NEW\n    from app.backtest.ic.test_ic2_short_tp_skip_exp import build_corpus, legs, ADJ, key\n\n    bak = HERE.parent / f"backtest_ic_runner.py.bak-{FENCE}"\n    ORIG = None\n    if bak.exists():\n        from importlib.machinery import SourceFileLoader\n        ldr = SourceFileLoader("ic_runner_orig_adjfill", str(bak))\n        spec = importlib.util.spec_from_loader("ic_runner_orig_adjfill", ldr)\n        mod = importlib.util.module_from_spec(spec)\n        sys.modules["ic_runner_orig_adjfill"] = mod\n        ldr.exec_module(mod)\n        ORIG = mod.run_ic_backtest\n\n    td = tempfile.mkdtemp()\n    atexit.register(lambda: shutil.rmtree(td, ignore_errors=True))\n    dbp = os.path.join(td, "bt.db")\n    days, d = [], date(2026, 1, 12)\n    while len(days) < 34:\n        if is_trading_day(d):\n            days.append(d)\n        d += timedelta(days=1)\n    build_corpus(dbp, days)\n    _cs._backtest_db_path = lambda: Path(dbp)\n    D0, D1 = days[1], days[-1]\n\n    def run(fn, cfg, sid="IC_V2"):\n        r = fn(db_path=dbp, strategy_id=sid, underlying="NIFTY", date_from=D0, date_to=D1, config_override=cfg)\n        assert not r.get("aborted"), r.get("reason")\n        return r\n\n    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",\n          "expiry_exit_time": "15:15", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ, "legs": legs(0)}\n    K = lambda r, p=lambda t: True: sorted(key(t) for t in r["trades"] if p(t))   # noqa: E731\n    is_adj = lambda t: "ADJ" in t.condition                                        # noqa: E731\n    diag = lambda r: r["summary"].get("diag_ic", {})                               # noqa: E731\n\n    absent = run(NEW, V2)\n    op = run(NEW, {**V2, "adjust_fill": "open"})\n    cl = run(NEW, {**V2, "adjust_fill": "close"})\n    n_adj = sum(1 for t in op["trades"] if is_adj(t))\n    print(f"   adjustments in the open run: {n_adj}")\n    check("corpus exercises it: ≥ 3 adjustment legs", n_adj >= 3, n_adj)\n    check("key absent ≡ \\"open\\" (row-for-row)", K(absent) == K(op))\n    check("shorts + wings identical in open and close runs", K(op, lambda t: not is_adj(t)) == K(cl, lambda t: not is_adj(t)))\n    check("…and the ·ADJ legs differ", K(op, is_adj) != K(cl, is_adj))\n    check("diag adjust_fill = open in the open run", diag(op).get("adjust_fill") == "open")\n    check("diag has no adjust_fill key in the close run", "adjust_fill" not in diag(cl))\n\n    conn = sqlite3.connect(dbp)\n\n    def bar(sym, ts):\n        return conn.execute("SELECT open, close FROM backtest_candles_1m WHERE tradingsymbol=? AND ts=?", (sym, ts)).fetchone()\n\n    def chain(sym, ts):\n        typ, exp = conn.execute("SELECT instrument_type, expiry FROM backtest_candles_1m WHERE tradingsymbol=? LIMIT 1",\n                                (sym,)).fetchone()\n        return conn.execute("SELECT tradingsymbol, open, close FROM backtest_candles_1m WHERE instrument_type=? "\n                            "AND expiry=? AND ts=?", (typ, exp, ts)).fetchall()\n\n    cap = float(ADJ["L1"]["premium_max"])\n    for mode, r, col in (("open", op, 0), ("close", cl, 1)):\n        bad_px, bad_pick, n = [], [], 0\n        for t in r["trades"]:\n            if not is_adj(t) or t.synthetic:\n                continue\n            n += 1\n            b = bar(t.tradingsymbol, t.entry_ts)\n            if not b or abs(round(b[col], 2) - t.entry_price) > 0.011:\n                bad_px.append((t.tradingsymbol, t.entry_price, b))\n            el = sorted(((-row[1 + col], row[0]) for row in chain(t.tradingsymbol, t.entry_ts) if 0 < row[1 + col] <= cap))\n            best = (-el[0][0], el[0][1]) if el else (None, None)      # select_strike\'s tie-break: price desc, symbol asc\n            if best[1] != t.tradingsymbol:\n                bad_pick.append((t.tradingsymbol, best))\n        check(f"[{mode}] every ·ADJ entry = the {mode.upper()} of its candle at the activation minute ({n} legs)",\n              n > 0 and not bad_px, bad_px[:3])\n        check(f"[{mode}] every ·ADJ strike = highest {mode} ≤ cap at that minute", n > 0 and not bad_pick, bad_pick[:3])\n\n    if ORIG:\n        o = run(ORIG, V2)\n        check("\\"close\\" ≡ ORIGINAL runner (row-for-row)", K(cl) == K(o))\n        check("\\"close\\": diag keys identical to ORIGINAL", set(diag(cl)) == set(diag(o)))\n    else:\n        print(f"  ! {bak.name} not found — ORIGINAL comparison skipped")\n\n    V1 = {"entry_time": "09:18", "exit_time": "15:28", "legs": legs(0)}\n    v1o, v1c = run(NEW, {**V1, "adjust_fill": "open"}, "IC_V1"), run(NEW, {**V1, "adjust_fill": "close"}, "IC_V1")\n    check("IC_V1 (no adjustments): open ≡ close, no diag key", K(v1o) == K(v1c) and "adjust_fill" not in diag(v1o))\n\n\nif __name__ == "__main__":\n    t_engine()\n    t_runner()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} IC ADJ-FILL CHECKS PASSED")\n'}
EDITS = {'backend/app/backtest/ic/ic_v1_engine.py': [('                     iv_thresholds: Optional[Dict[str, float]] = None) -> dict:\n', '                     iv_thresholds: Optional[Dict[str, float]] = None,\n                     adjust_fill: str = "close") -> dict:\n'), ('    Both default None ⇒ no IV monitoring ⇒ every path below is\n    behaviourally identical to the pre-IC_IV_SL build.\n\n', '    Both default None ⇒ no IV monitoring ⇒ every path below is\n    behaviourally identical to the pre-IC_IV_SL build.\n\n    ── IC_ADJ_FILL_OPEN_20261101 param ──\n    adjust_fill: "close" (default here — every caller before 1 Nov) books a\n      real adjustment at the CLOSE of the activation-minute candle and starts\n      watching it from the NEXT candle. "open" books it at that candle\'s OPEN\n      and watches that same candle (every print after the open happened after\n      the fill). Paper/live buy ~adjust_delay_s after the stop, part-way into\n      that minute; on 5 paper adjustments (16–29 Oct) the close was dearer\n      every time (mean −5.5 pts vs paper), the open within −0.85. A synthetic\n      fill_price is unaffected (it is priced at the start of the minute).\n\n'), ('            is_synth_adj = fill_cd is None\n            aepx = float(fill_cd["close"]) if fill_cd is not None \\\n                else float(synth_fill)\n', '            is_synth_adj = fill_cd is None\n            _fill_open = adjust_fill == "open" and fill_cd is not None   # ── IC_ADJ_FILL_OPEN_20261101 ──\n            aepx = float(fill_cd["open" if _fill_open else "close"]) if fill_cd is not None \\\n                else float(synth_fill)\n'), ('            # the adjustment\'s OWN entry candle must not also exit it —\n            # monitoring starts at the NEXT candle (entry-fill convention)\n            state[aid]["watch_from"] = act_ts + 60\n', '            # the adjustment\'s OWN entry candle must not also exit it —\n            # monitoring starts at the NEXT candle (entry-fill convention).\n            # ── IC_ADJ_FILL_OPEN_20261101 ── filled at the OPEN, the rest of that candle is\n            # after the fill, so it is watched (live watches from the fill).\n            state[aid]["watch_from"] = act_ts if _fill_open else act_ts + 60\n')], 'backend/app/backtest/ic/backtest_ic_runner.py': [('      expiry_exit_time  "HH:MM"  (default "15:28")\n      adjust_on_sl      bool     (default False)\n      adjust_delay_s    int      (default 60)\n      adjust            {"L1": {...}, "L2": {...}}\n', '      expiry_exit_time  "HH:MM"  (default "15:28")\n      adjust_on_sl      bool     (default False)\n      adjust_delay_s    int      (default 60)\n      adjust            {"L1": {...}, "L2": {...}}\n      adjust_fill       "open" | "close"  (default "open") ── IC_ADJ_FILL_OPEN_20261101 ──\n                        price an adjustment\'s strike pick AND fill at the OPEN\n                        of the activation minute (where paper/live buy) or at\n                        its CLOSE (every run before 1 Nov 2026).\n'), ('def _parity_spot(pairs: dict, tau: float) -> Optional[float]:\n', 'def _open_or_close_before(src, sym: str, day_start: int, ts: int,\n                          max_stale_s: int = 900) -> Optional[float]:\n    """── IC_ADJ_FILL_OPEN_20261101 ── the price at the START of minute `ts`:\n    the OPEN of the candle stamped `ts` (its first print — no lookahead),\n    else the last close strictly before `ts` within the staleness window."""\n    best = None\n    for cd in src.candles_1m_for_symbol_day(sym, day_start):\n        cts = cd["ts"] if isinstance(cd, dict) else cd.ts\n        if cts == ts:\n            return float(cd["open"] if isinstance(cd, dict) else cd.open)\n        if ts - max_stale_s <= cts < ts and (best is None or cts > best[0]):\n            best = (cts, float(cd["close"] if isinstance(cd, dict) else cd.close))\n    return best[1] if best else None\n\n\ndef _minute_ladder_open(src, week: list, day_start: int, ts: int) -> Dict[str, list]:\n    """── IC_ADJ_FILL_OPEN_20261101 ── _minute_ladder priced at the START of the\n    minute (adjust_fill="open"), so the strike is picked on the same prices\n    the fill uses — live picks and buys from one chain snapshot."""\n    out: Dict[str, list] = {"CE": [], "PE": []}\n    for c in week:\n        px = _open_or_close_before(src, c["tradingsymbol"], day_start, ts)\n        if px and px > 0:\n            out[c["instrument_type"]].append((c["tradingsymbol"], px))\n    return out\n\n\ndef _parity_spot(pairs: dict, tau: float) -> Optional[float]:\n'), ('    short_tp_skip_expiry = bool(cfg.get("short_tp_skip_expiry", False))\n\n', '    short_tp_skip_expiry = bool(cfg.get("short_tp_skip_expiry", False))\n    # ── IC_ADJ_FILL_OPEN_20261101 ── adjustment pick + fill at the OPEN of the activation\n    # minute (default — where paper/live buy); "close" reproduces older runs.\n    adjust_fill = ("close" if str(cfg.get("adjust_fill", "open") or "open").strip().lower() == "close"\n                   else "open")\n\n'), ('        if short_tp_skip_expiry:   # ── IC2_SHORT_TP_SKIP_EXP_20261015 ── bool is the parent\'s own\n            base_diag["short_tp_skip_expiry"] = True\n', '        if short_tp_skip_expiry:   # ── IC2_SHORT_TP_SKIP_EXP_20261015 ── bool is the parent\'s own\n            base_diag["short_tp_skip_expiry"] = True\n        if adjust_on_sl and adjust_fill == "open":   # ── IC_ADJ_FILL_OPEN_20261101 ── parent\'s own\n            base_diag["adjust_fill"] = "open"\n'), ('    if short_tp_skip_expiry:\n        diag["short_tp_skip_expiry"] = True\n        diag["short_tp_expiry_days_off"] = 0      # expiry sessions whose short TP was switched off\n', '    if short_tp_skip_expiry:\n        diag["short_tp_skip_expiry"] = True\n        diag["short_tp_expiry_days_off"] = 0      # expiry sessions whose short TP was switched off\n    # ── IC_ADJ_FILL_OPEN_20261101 ── key only when it changes something (close ≡ ORIGINAL)\n    if adjust_on_sl and adjust_fill == "open":\n        diag["adjust_fill"] = "open"\n'), ('                lad = _minute_ladder(src, week, day_start, fill_ts)\n', '                lad = (_minute_ladder_open(src, week, day_start, fill_ts)   # ── IC_ADJ_FILL_OPEN_20261101 ──\n                       if adjust_fill == "open" else _minute_ladder(src, week, day_start, fill_ts))\n'), ('                    adjust_delay_s=adjust_delay_s, adjust_picks=engine_picks,\n                    entry_overrides=entry_overrides,\n                    iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr)\n                res["trades"].sort(key=lambda t: t["leg"])\n', '                    adjust_delay_s=adjust_delay_s, adjust_picks=engine_picks,\n                    entry_overrides=entry_overrides,\n                    iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr,\n                    adjust_fill=adjust_fill)   # ── IC_ADJ_FILL_OPEN_20261101 ──\n                res["trades"].sort(key=lambda t: t["leg"])\n'), ('                is_carry_day=False,\n                entry_overrides=entry_overrides,\n                iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr)\n            diag["days_entered"] += 1\n', '                is_carry_day=False,\n                entry_overrides=entry_overrides,\n                iv_by_minute=ic_iv_by_min, iv_thresholds=ic_iv_thr,\n                adjust_fill=adjust_fill)   # ── IC_ADJ_FILL_OPEN_20261101 ──\n            diag["days_entered"] += 1\n')], 'backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py': [('    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",\n          "expiry_exit_time": "15:15", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ}\n', '    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",\n          "expiry_exit_time": "15:15", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ,\n          # ── IC_ADJ_FILL_OPEN_20261101 ── this suite compares against the pre-1-Nov runner\n          # (.bak), which booked adjustments at the minute CLOSE — pin it.\n          "adjust_fill": "close"}\n')], 'frontend/src/pages/Backtest.jsx': [('      if (cfg.adjust_only) add("Exec", "ADJ-only");\n    }\n  } else if (cfg.exit_time) add("EOD", cfg.exit_time);\n', '      if (cfg.adjust_only) add("Exec", "ADJ-only");\n      if (cfg.adjust_fill) add("ADJ fill", cfg.adjust_fill === "close" ? "minute close (older runs)" : "minute open (live)");   // ── IC_ADJ_FILL_OPEN_20261101 ──\n    }\n  } else if (cfg.exit_time) add("EOD", cfg.exit_time);\n'), ('  const [icShortTpSkipExp, setIcShortTpSkipExp] = useState(icSaved.shortTpSkipExp ?? false);\n', '  const [icShortTpSkipExp, setIcShortTpSkipExp] = useState(icSaved.shortTpSkipExp ?? false);\n  // ── IC_ADJ_FILL_OPEN_20261101 ── adjustment buy price: OPEN of the activation minute (live) | its CLOSE (older runs)\n  const [icAdjFill, setIcAdjFill] = useState(icSaved.adjFill === "close" ? "close" : "open");\n'), ('adjustOnly: icAdjustOnly, workers: icWorkers, minEntryIv: icMinEntryIv, shortTpSkipExp: icShortTpSkipExp })); } catch { /* ignore */ }\n  }, [icEntryTime, icExitTime, icLegs, icWingMode, icSkewMult, icNextOpenTime, icExpiryExitTime, icAdjustOn, icAdjustDelay, icAdjust, icAdjustOnly, icWorkers, icMinEntryIv, icShortTpSkipExp]);\n', 'adjustOnly: icAdjustOnly, workers: icWorkers, minEntryIv: icMinEntryIv, shortTpSkipExp: icShortTpSkipExp, adjFill: icAdjFill })); } catch { /* ignore */ }   // ── IC_ADJ_FILL_OPEN_20261101 ──\n  }, [icEntryTime, icExitTime, icLegs, icWingMode, icSkewMult, icNextOpenTime, icExpiryExitTime, icAdjustOn, icAdjustDelay, icAdjust, icAdjustOnly, icWorkers, icMinEntryIv, icShortTpSkipExp, icAdjFill]);\n'), ('        adjust_only: !!icAdjustOn && !!icAdjustOnly,   // ── ADJ_ONLY ──\n', '        adjust_only: !!icAdjustOn && !!icAdjustOnly,   // ── ADJ_ONLY ──\n        adjust_fill: icAdjFill === "close" ? "close" : "open",   // ── IC_ADJ_FILL_OPEN_20261101 ──\n'), ('      icWorkers, icMinEntryIv, icShortTpSkipExp,   // ── IC_PARALLEL / IC_MIN_ENTRY_IV / IC2_SHORT_TP_SKIP_EXP_20261015 ── stale-closure rule: buildConfig reads them, so they land here\n', '      icWorkers, icMinEntryIv, icShortTpSkipExp, icAdjFill,   // ── IC_PARALLEL / IC_MIN_ENTRY_IV / IC2_SHORT_TP_SKIP_EXP_20261015 / IC_ADJ_FILL_OPEN_20261101 ── stale-closure rule: buildConfig reads them, so they land here\n'), ('                    <Field label="Delay after SL (s)"><input type="number" step="60" style={{ ...inputStyle, width: 90 }} value={icAdjustDelay} disabled={!icAdjustOn} onChange={(e) => setIcAdjustDelay(Number(e.target.value))} title="60 = the next 1m candle (Quantman ReExecute delay)" /></Field>\n', '                    <Field label="Delay after SL (s)"><input type="number" step="60" style={{ ...inputStyle, width: 90 }} value={icAdjustDelay} disabled={!icAdjustOn} onChange={(e) => setIcAdjustDelay(Number(e.target.value))} title="60 = the next 1m candle (Quantman ReExecute delay)" /></Field>\n                    {/* ── IC_ADJ_FILL_OPEN_20261101 ── */}\n                    <Field label="ADJ buy price">\n                      <select style={{ ...inputStyle, width: 250 }} value={icAdjFill} disabled={!icAdjustOn}\n                        onChange={(e) => setIcAdjFill(e.target.value === "close" ? "close" : "open")}\n                        title="Where the backtest buys the adjustment leg in the minute after the delay. MINUTE OPEN = the start of that minute (strike picked and bought on those prices) — paper/live buy part-way into the minute, and on 16–29 Oct paper was within ~1 pt of the open but ~5.5 pts cheaper than the close. MINUTE CLOSE = the end of that minute, used by every run before 1 Nov; pick it to reproduce older results.">\n                        <option value="open">MINUTE OPEN (matches live)</option>\n                        <option value="close">MINUTE CLOSE (older runs)</option>\n                      </select>\n                    </Field>\n')]}
PARENTS = {'backend/app/backtest/ic/ic_v1_engine.py': ['IC_IV_SL', 'ADJ_ON_MTC'], 'backend/app/backtest/ic/backtest_ic_runner.py': ['IC2_SHORT_TP_SKIP_EXP_20261015', 'LOT_COMP_EQ_20260924'], 'backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py': ['IC2_SHORT_TP_SKIP_EXP_20261015'], 'frontend/src/pages/Backtest.jsx': ['IC2_SHORT_TP_SKIP_EXP_20261015', 'TSG_MTM_INTRABAR_20261031']}
MIRRORS = ("desktop/src-tauri",)       # build copies (gitignored); refreshed if present


def die(msg, wrote=False):
    print(f"\n✗ {msg}" + ("" if wrote else "\nNothing was written."))
    sys.exit(1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    root = os.path.abspath(args[0] if args else os.getcwd())
    if not (os.path.isdir(os.path.join(root, "backend", "app")) and os.path.isdir(os.path.join(root, "frontend", "src"))):
        die(f"{root} is not the scalp-app repo root (expected backend/app and frontend/src)")
    print(f"── {FENCE} → {root}")

    # ── preflight ──
    for rel in EDITS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die(f"missing {rel}")
        txt = open(p, encoding="utf-8").read()
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied")
        for parent in PARENTS.get(rel, []):
            if parent not in txt:
                die(f"{rel} lacks parent fence {parent} — pull the latest main first")
    for rel in NEW_FILES:
        if os.path.exists(os.path.join(root, rel)):
            die(f"{rel} already exists — already applied?")
    if "--allow-dirty" not in flags and os.path.isdir(os.path.join(root, ".git")):
        try:
            st = subprocess.run(["git", "status", "--porcelain", "--"] + list(EDITS), cwd=root,
                                capture_output=True, text=True, timeout=30).stdout
        except Exception as e:
            st = ""
            print(f"  (git status unavailable: {e!r} — continuing)")
        dirty = [ln for ln in st.splitlines() if ln[:2].strip()]
        if dirty:
            die("uncommitted changes in target files (commit/stash, or pass --allow-dirty):\n  " + "\n  ".join(dirty))

    staged = {}
    for rel, edits in EDITS.items():
        txt = open(os.path.join(root, rel), encoding="utf-8").read()
        for i, (old, new) in enumerate(edits, 1):
            n = txt.count(old)
            if n != 1:
                die(f"{rel}: anchor {i}/{len(edits)} found {n}× (expected 1):\n{old[:300]}")
            txt = txt.replace(old, new)
        staged[rel] = txt
    for rel, txt in list(staged.items()) + list(NEW_FILES.items()):
        if rel.endswith(".py"):
            try:
                compile(txt, rel, "exec")
            except SyntaxError as e:
                die(f"{rel} does not compile after staging: {e}")
    print(f"  preflight ok — {sum(len(e) for e in EDITS.values())} anchored edits in {len(EDITS)} files, {len(NEW_FILES)} new file")

    # ── write ──
    touched = []          # (path, backup or None)

    def write(path, txt):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bak = None
        if os.path.exists(path):
            bak = f"{path}.bak-{FENCE}"
            shutil.copy2(path, bak)
        touched.append((path, bak))
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)

    def rollback(why):
        for path, bak in reversed(touched):
            try:
                if bak:
                    shutil.copy2(bak, path)
                    os.remove(bak)
                elif os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                print(f"  !! could not restore {path}: {e!r}")
        die(f"{why}\nEvery file it touched was restored.", wrote=True)

    for rel, txt in list(staged.items()) + list(NEW_FILES.items()):
        write(os.path.join(root, rel), txt)
        print(f"  wrote {rel}")
        for m in MIRRORS:
            if os.path.isdir(os.path.join(root, m, rel.split("/")[0])):
                write(os.path.join(root, m, rel), txt)
                print(f"    mirrored → {m}/{rel}")

    if "--skip-tests" in flags:
        print("\n  --skip-tests: suites not run")
    else:
        be = os.path.join(root, "backend")
        ic_eng = os.path.join(be, "app", "engine", "ic")
        env = dict(os.environ, PYTHONPATH=be, SCALP_LOT_SIZE_OFFLINE="1")
        env_iv = dict(env, PYTHONPATH=be + os.pathsep + os.path.join(be, "app", "backtest", "ic"))
        suites = [("app/backtest/ic/test_ic_adjust_fill.py", be, env, None),          # 30 new
                  ("app/backtest/ic/test_ic2_short_tp_skip_exp.py", be, env, None),   # 31
                  ("app/engine/ic/test_ic_iv_sl.py", be, env_iv, None),               # 18
                  ("app/backtest/tma/test_ic2_margin_today.py", be, env, None),       # 15
                  ("app/parity/test_parity.py", be, env, None),
                  ("app/backtest/replay/test_trade_replay.py", be, env, None),
                  ("app/backtest/engine/test_lot_comp_runners.py", be, env, None)]    # ~3 min
        import importlib.util as _ilu
        if _ilu.find_spec("pytest") is not None:
            for f in ("test_ic_engine_monitor_eod.py", "test_ic_group_manager.py", "test_ic_live_core.py",
                      "test_ic_orphan_reconcile.py", "test_ic_selection.py"):           # 113, standalone
                suites.append((f, ic_eng, env, ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir=."]))
        else:
            print("  (pytest not installed — the 5 engine/ic pytest suites are skipped; they don't import the changed files)")
        for suite, cwd, e, pre in suites:
            print(f"\n── running {suite}")
            r = subprocess.run([sys.executable] + (pre or []) + [suite], cwd=cwd, env=e, capture_output=True, text=True)
            tail_ = "\n".join((r.stdout or "").strip().splitlines()[-1:])
            print("  " + tail_)
            if r.returncode != 0:
                print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if "FAIL" in l or "Error" in l)[-3000:])
                rollback(f"{suite} failed")
        cu = os.path.join(root, "check_undefined_names.py")
        if os.path.exists(cu):
            r = subprocess.run([sys.executable, cu, os.path.join(be, "app", "backtest", "ic")], capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-2000:])
                rollback("check_undefined_names.py found undefined names in app/backtest/ic")
            print("  check_undefined_names: app/backtest/ic clean")
        bin_ = os.path.join(root, "frontend", "node_modules", ".bin")
        jsx = os.path.join(root, "frontend", "src", "pages", "Backtest.jsx")
        esl = os.path.join(bin_, "eslint")
        if os.path.exists(esl):
            r = subprocess.run([esl, "--no-eslintrc",
                                "-c", os.path.join(root, "frontend", "node_modules", "eslint-config-react-app", "index.js"),
                                "--quiet", jsx], cwd=os.path.join(root, "frontend"), capture_output=True, text=True)
            if r.returncode != 0:
                print((r.stdout + r.stderr)[-2500:])
                rollback("ESLint (react-app rules) reported errors in Backtest.jsx")
            print("  eslint (react-app): Backtest.jsx — 0 errors")
        esb = os.path.join(bin_, "esbuild")
        if os.path.exists(esb):
            r = subprocess.run([esb, jsx, "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stderr[-2000:])
                rollback("esbuild could not parse Backtest.jsx")
            print("  esbuild parsed Backtest.jsx")
        if not (os.path.exists(esl) or os.path.exists(esb)):
            print("  (frontend/node_modules missing — JSX checks skipped; build-scalp.sh will compile)")

    print(f"\n✓ {FENCE} applied. Backups: *.bak-{FENCE}")
    print("  Next: ./desktop/build-scalp.sh both   — outside market hours")
    print("  Then: Backtest → IC_V2, sealed config, run it twice — 'ADJ buy price' = MINUTE OPEN, then MINUTE CLOSE.")


if __name__ == "__main__":
    main()
