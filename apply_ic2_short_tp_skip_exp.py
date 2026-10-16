#!/usr/bin/env python3
# apply_ic2_short_tp_skip_exp.py — fence IC2_SHORT_TP_SKIP_EXP_20261015
#
# IC_V1 / IC_V2 BACKTEST ONLY — "Short-leg TP: skip expiry day".
#
# Why: the short-TP runs (70/80/90 %, 2026-10-15) lost net overall, but split
# by outcome the loss was all expiry-day decay (survivors run to ~₹1 by the
# expiry square-off) while non-expiry TPs gained (they beat reversals into the
# move-to-cost stop): screen at 80 % = +₹8.0L, 7/7 years, both halves. This
# switch turns the split into a real runner test.
#
# RUNNER  backend/app/backtest/ic/backtest_ic_runner.py
#   * config key `short_tp_skip_expiry` (bool, default False). True → on a day
#     whose front contract expires THAT day (the runner's own expiry calendar,
#     the same test that triggers the expiry-day square-off) the SELL legs'
#     tp_val is set to 0 before anything runs (entry-IV anchors, discovery
#     probe, session). Legs entered on other days keep their TP — including a
#     leg carried INTO expiry morning. Adjustment-leg TP is never touched.
#   * Absent / False → byte-identical to the original runner (proven against
#     the .bak in the suite). Diag keys `short_tp_skip_expiry` /
#     `short_tp_expiry_days_off` exist only when ON; parallel (EOD) merges them.
#
# FRONTEND (Backtest page, IC_V1 + IC_V2 form)
#   * Checkbox under the legs table; emitted ONLY when ticked (saved configs
#     without the key reproduce exactly). Persisted with the IC form; in the
#     buildConfig dep array (stale-closure rule).
#   * Chip "Short TP: off on expiry day" on run cards / Portfolio; Queue
#     params now show short-leg TP, ADJ TP and the flag; Compare "Legs" row
#     shows short TP + a new "IC short TP expiry" row.
#   * Sweep axes: "Short TP %" (both SELL legs, pct) and "Short TP off on
#     expiry (0/1)".
#   * CSV export fix: an array of objects (IC legs, TSG legs, …) was written
#     as "[object Object]|…" — now JSON. Plain arrays keep "a|b|c".
#
#  EDIT  backend/app/backtest/ic/backtest_ic_runner.py (6)
#        frontend/src/pages/Backtest.jsx (8)
#        frontend/src/pages/backtest/{BacktestQueue,RunComparison,SweepBuilder}.jsx
#  NEW   backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#  Gates: py_compile, esbuild parse of the 4 JSX files, runner import, the new
#  suite (helper; OFF ≡ ORIGINAL via .bak; ON split ≡ plain-TP / no-TP runs in
#  NEXT_OPEN and EOD mode; parallel ≡ serial), then test_lot_comp_runners.
#  Any failure → every file restored. Live/paper untouched.
#
# Usage: python3 apply_ic2_short_tp_skip_exp.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script (the test file is carried inside it).

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "IC2_SHORT_TP_SKIP_EXP_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
R = "backend/app/backtest/ic/backtest_ic_runner.py"
B = "frontend/src/pages/Backtest.jsx"
Q = "frontend/src/pages/backtest/BacktestQueue.jsx"
C = "frontend/src/pages/backtest/RunComparison.jsx"
W = "frontend/src/pages/backtest/SweepBuilder.jsx"
T = "backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py"
PY = [R]
JSX = [B, Q, C, W]
PREREQ = {R: ["IC_MIN_ENTRY_IV", "ADJ_ON_MTC", "LOT_COMP_EQ_20260924"],
          B: ["IC_MIN_ENTRY_IV", "ADJ_ONLY", "CBO_PARAMS_EXPORT_20260830"]}
SUITES = ["app/backtest/ic/test_ic2_short_tp_skip_exp.py", "app/backtest/engine/test_lot_comp_runners.py"]

EDITS = [
    (R, '      next_open_time    "HH:MM"  (default "09:16")\n',
     '      next_open_time    "HH:MM"  (default "09:16")\n      short_tp_skip_expiry  bool (default False) ── IC2_SHORT_TP_SKIP_EXP_20261015 ──\n                        True → SELL legs entered on the contract\'s own expiry\n                        day run with NO TP (tp_val 0); every other day keeps it.\n', 1),
    (R, '# ── house rule: session times are MINUTES, never string-compared ──\n',
     '# ── IC2_SHORT_TP_SKIP_EXP_20261015 ── expiry-day short TP switch-off.\ndef _short_tp_off(day_legs: List[dict]) -> tuple:\n    """Copy of `day_legs` with the SELL legs\' TP disabled (tp_val 0).\n    BUY legs (wings) are passed through as the same objects; the input list\n    and its dicts are never mutated. Returns (legs, n_switched_off)."""\n    out: List[dict] = []\n    n = 0\n    for leg in day_legs:\n        if leg.get("action") == "SELL" and float(leg.get("tp_val") or 0) > 0:\n            out.append(dict(leg, tp_val=0.0))\n            n += 1\n        else:\n            out.append(leg)\n    return out, n\n\n\n# ── house rule: session times are MINUTES, never string-compared ──\n', 1),
    (R, '    min_entry_iv = abs(float(cfg.get("min_entry_iv", 0) or 0))\n',
     '    min_entry_iv = abs(float(cfg.get("min_entry_iv", 0) or 0))\n    # ── IC2_SHORT_TP_SKIP_EXP_20261015 ── short-leg TP off on expiry day.\n    # Absent / False → every path below is byte-identical to the old runner.\n    short_tp_skip_expiry = bool(cfg.get("short_tp_skip_expiry", False))\n', 1),
    (R, '            "adjust_on_sl": adjust_on_sl, "adjust_only": adjust_only,\n        }\n',
     '            "adjust_on_sl": adjust_on_sl, "adjust_only": adjust_only,\n        }\n        if short_tp_skip_expiry:   # ── IC2_SHORT_TP_SKIP_EXP_20261015 ── bool is the parent\'s own\n            base_diag["short_tp_skip_expiry"] = True\n', 1),
    (R, '    trades: List[ICTrade] = []\n',
     '    # ── IC2_SHORT_TP_SKIP_EXP_20261015 ── keys exist only when ON (OFF ≡ ORIGINAL)\n    if short_tp_skip_expiry:\n        diag["short_tp_skip_expiry"] = True\n        diag["short_tp_expiry_days_off"] = 0      # expiry sessions whose short TP was switched off\n    trades: List[ICTrade] = []\n', 1),
    (R, '        # ── IC_MIN_ENTRY_IV END ──\n',
     '        # ── IC_MIN_ENTRY_IV END ──\n\n        # ── IC2_SHORT_TP_SKIP_EXP_20261015 ── the contract\'s own expiry day\n        # (same test as the expiry-day square-off): shorts entered today run\n        # with NO TP — they go to SL / move-to-cost / the expiry close. Done\n        # before the IV series, the discovery probe and the session so every\n        # pass sees the same legs. Carried-in legs (entered yesterday) are not\n        # in day_legs and keep their own TP; adjustment TP is untouched.\n        if short_tp_skip_expiry and want_expiry == d.isoformat():\n            day_legs, _n_tp_off = _short_tp_off(day_legs)\n            if _n_tp_off:\n                diag["short_tp_expiry_days_off"] += 1\n', 1),
    (B, '        else flat.push([`${prefix}${k}`, Array.isArray(v) ? v.join("|") : v]);',
     '        // ── IC2_SHORT_TP_SKIP_EXP_20261015 ── an array of OBJECTS (IC / TSG legs) used to\n        // print as "[object Object]|…" — the one row that says what the legs were. JSON now.\n        else flat.push([`${prefix}${k}`, Array.isArray(v) ? (v.some((x) => x != null && typeof x === "object") ? JSON.stringify(v) : v.join("|")) : v]);', 1),
    (B, '  const [icMinEntryIv, setIcMinEntryIv] = useState(icSaved.minEntryIv ?? 0);\n',
     "  const [icMinEntryIv, setIcMinEntryIv] = useState(icSaved.minEntryIv ?? 0);\n  // ── IC2_SHORT_TP_SKIP_EXP_20261015 ── SELL-leg TP off on the contract's expiry day\n  const [icShortTpSkipExp, setIcShortTpSkipExp] = useState(icSaved.shortTpSkipExp ?? false);\n", 1),
    (B, 'workers: icWorkers, minEntryIv: icMinEntryIv })); } catch',
     'workers: icWorkers, minEntryIv: icMinEntryIv, shortTpSkipExp: icShortTpSkipExp })); } catch', 1),
    (B, 'icAdjustOnly, icWorkers, icMinEntryIv]);',
     'icAdjustOnly, icWorkers, icMinEntryIv, icShortTpSkipExp]);', 1),
    (B, 'tp_val: Number(l.tp_val) })),\n      };\n',
     'tp_val: Number(l.tp_val) })),\n      };\n      // ── IC2_SHORT_TP_SKIP_EXP_20261015 ── emitted ONLY when ticked, so every saved\n      // config without it reproduces exactly (runner default = off).\n      if (icShortTpSkipExp) base.short_tp_skip_expiry = true;\n', 1),
    (B, 'icWorkers, icMinEntryIv,   // ── IC_PARALLEL / IC_MIN_ENTRY_IV ──',
     'icWorkers, icMinEntryIv, icShortTpSkipExp,   // ── IC_PARALLEL / IC_MIN_ENTRY_IV / IC2_SHORT_TP_SKIP_EXP_20261015 ──', 1),
    (B, '              {/* ── IC_V2 BEGIN ── carry timings + per-short adjustment legs */}',
     '              {/* ── IC2_SHORT_TP_SKIP_EXP_20261015 ── */}\n              <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, fontSize: 12, color: colors.text.secondary, cursor: "pointer", width: "fit-content" }}\n                title="On the contract\'s OWN expiry day, shorts (L1/L2) entered that day run with NO TP — to their SL, the move-to-cost stop or the expiry square-off. Every other day keeps the TP above (a leg carried into expiry morning keeps its TP too). Adjustment-leg TP is not affected. No effect unless an L1/L2 TP is set.">\n                <input type="checkbox" checked={!!icShortTpSkipExp} onChange={(e) => setIcShortTpSkipExp(e.target.checked)} />\n                Short-leg TP: skip expiry day\n                {icShortTpSkipExp && !icLegs.some((l) => l.action === "SELL" && Number(l.lots) > 0 && Number(l.tp_val) > 0) && (\n                  <span style={{ fontSize: 11, color: colors.text.muted }}>(no L1/L2 TP set — no effect)</span>\n                )}\n              </label>\n              {/* ── IC_V2 BEGIN ── carry timings + per-short adjustment legs */}', 1),
    (B, '${l.lots}L`);   /* ── IC_IV_SL ── */\n    });\n  }\n  if (cfg.option_premium)',
     '${l.lots}L`);   /* ── IC_IV_SL ── */\n    });\n  }\n  if (cfg.short_tp_skip_expiry) add("Short TP", "off on expiry day");   // ── IC2_SHORT_TP_SKIP_EXP_20261015 ──\n  if (cfg.option_premium)', 1),
    (Q, '${l.mtc_other_on_sl ? "·MTC" : ""} ${l.lots}L`);',
     '${l.tp_val ? ` TP${l.tp_val}${l.tp_mode === "pts" ? "p" : "%"}` : ""}${l.mtc_other_on_sl ? "·MTC" : ""} ${l.lots}L`);   /* ── IC2_SHORT_TP_SKIP_EXP_20261015 ── TP shown */', 1),
    (Q, '? `<${x.premium_max} ${x.lots}L SL${x.sl_val}${x.sl_mode === "pts" ? "p" : "%"}` : null;',
     '? `<${x.premium_max} ${x.lots}L SL${x.sl_val}${x.sl_mode === "pts" ? "p" : "%"}${Number(x.tp_val) ? ` TP${x.tp_val}${x.tp_mode === "pts" ? "p" : "%"}` : ""}` : null;   /* ── IC2_SHORT_TP_SKIP_EXP_20261015 ── ADJ TP shown */', 1),
    (Q, '    if (cfg.wing_mode && cfg.wing_mode !== "real_fallback") {\n',
     '    if (cfg.short_tp_skip_expiry) p.push("sTP-noExpiry");   // ── IC2_SHORT_TP_SKIP_EXP_20261015 ──\n    if (cfg.wing_mode && cfg.wing_mode !== "real_fallback") {\n', 1),
    (C, '${l.mtc_other_on_sl ? "·MTC" : ""}`).join(" ") : null },',
     '${Number(l.tp_val) ? ` TP${l.tp_val}${l.tp_mode === "pts" ? "p" : "%"}` : ""}${l.mtc_other_on_sl ? "·MTC" : ""}`).join(" ") : null },   // ── IC2_SHORT_TP_SKIP_EXP_20261015 ── TP shown', 1),
    (C, '  { key: "ic_exec",',
     '  { key: "ic_short_tp_exp",  label: "IC short TP expiry", get: (r) => r.config?.short_tp_skip_expiry ? "off on expiry day" : (Array.isArray(r.config?.legs) && r.config.legs.some((l) => l.action === "SELL" && Number(l.tp_val) > 0) ? "all days" : null) },   // ── IC2_SHORT_TP_SKIP_EXP_20261015 ──\n  { key: "ic_exec",', 1),
    (W, '    fmt: (v) => `adj+${v}s` },\n',
     '    fmt: (v) => `adj+${v}s` },\n  // ── IC2_SHORT_TP_SKIP_EXP_20261015 ── short-leg TP (both SELL legs, pct) and the\n  // expiry-day switch-off as its own 0/1 axis, so the pair can be gridded.\n  { key: "ic_short_tp", label: "Short TP % (0 = off)", strategies: [IC],\n    hint: "0, 70, 80, 90", parse: _num,\n    apply: (c, v) => { (c.legs || []).forEach((l) => { if (l.action === "SELL") { l.tp_val = Math.abs(v); l.tp_mode = "pct"; } }); },\n    fmt: (v) => `sTP ${Math.abs(v)}%` },\n  { key: "ic_short_tp_skip_exp", label: "Short TP off on expiry (0/1)", strategies: [IC],\n    hint: "0, 1", parse: _num,\n    apply: (c, v) => { if (Number(v)) c.short_tp_skip_expiry = true; else delete c.short_tp_skip_expiry; },\n    fmt: (v) => (Number(v) ? "sTP noExp" : "sTP allDays") },\n', 1),
]

NEW_FILES = {T: '# backend/app/backtest/ic/test_ic2_short_tp_skip_exp.py — fence IC2_SHORT_TP_SKIP_EXP_20261015\n#\n# IC_V1/IC_V2 backtest: "Short-leg TP: skip expiry day" (config key\n# short_tp_skip_expiry). On the contract\'s own expiry day the SELL legs\'\n# tp_val is zeroed before the session runs; every other day is untouched.\n#\n#   1. _short_tp_off (pure)            only SELL legs with TP>0, copies, no mutation\n#   2. OFF ≡ ORIGINAL runner (.bak)    key absent / False → row-for-row, same diag keys\n#   3. ON with no short TP             no-op ≡ ORIGINAL; 0 days counted\n#   4. ON with short TP (NEXT_OPEN + adjustments, the sealed shape):\n#        no short ENTERED on an expiry day exits TP (the plain-TP run has some)\n#        legs entered on non-expiry days ≡ plain-TP run row-for-row (incl. ·ADJ)\n#        legs entered on expiry days    ≡ no-TP run row-for-row   (incl. ·ADJ)\n#        adjustment-leg TP still fires on expiry day (only SELL legs touched)\n#        diag counts the expiry days it acted on\n#   5. EOD mode (IC_V1 shape): same split + parallel ×2 ≡ serial\n#\n# Synthetic corpus in a temp dir; never touches backtest.db.\n# Run: cd backend && python3 app/backtest/ic/test_ic2_short_tp_skip_exp.py\n\nfrom __future__ import annotations\n\nimport atexit\nimport importlib.util\nimport os\nimport random\nimport shutil\nimport sqlite3\nimport sys\nimport tempfile\nfrom datetime import date, datetime, timedelta\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve()\nBACKEND = str(HERE.parents[3])\nif BACKEND not in sys.path:\n    sys.path.insert(0, BACKEND)\nos.environ.setdefault("SCALP_LOT_SIZE_OFFLINE", "1")\n\nFENCE = "IC2_SHORT_TP_SKIP_EXP_20261015"\nFAILS: list = []\nOK = [0]\nIST = 5 * 3600 + 30 * 60\nOPEN_M = 9 * 60 + 15\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nDDL = """CREATE TABLE backtest_candles_1m(instrument_token INTEGER,ts INTEGER,underlying TEXT,\ntradingsymbol TEXT,instrument_type TEXT,strike REAL,expiry TEXT,open REAL,high REAL,low REAL,\nclose REAL,volume INTEGER,oi INTEGER);\nCREATE INDEX i1 ON backtest_candles_1m(tradingsymbol,ts);\nCREATE INDEX i2 ON backtest_candles_1m(underlying,expiry,ts);\nCREATE INDEX i3 ON backtest_candles_1m(underlying,instrument_type,ts);"""\n\n\ndef _ds(d):\n    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST\n\n\ndef _day(ts):\n    return (datetime(1970, 1, 1) + timedelta(seconds=int(ts) + IST)).date()\n\n\ndef build_corpus(path, days, seed=23):\n    """Spot random walk with a per-day drift (quiet / trending days) and a\n    weekly chain: premium = intrinsic + decaying time value, so shorts can\n    reach a TP, an SL, or decay quietly into the close."""\n    from app.backtest.engine.expiry_calendar import expected_expiry_for_day\n    random.seed(seed)\n    conn = sqlite3.connect(path)\n    conn.executescript(DDL)\n    rows, spot = [], 24000.0\n    for d in days:\n        ds = _ds(d)\n        exp = expected_expiry_for_day(d).isoformat()\n        atm = int(round(spot / 50.0)) * 50\n        drift = random.choice((0.0, 0.0, 1, -1))\n        drift = drift * random.uniform(0.00015, 0.0004) if drift else 0.0\n        ms = []\n        for m in range(OPEN_M, 15 * 60 + 30):\n            o = spot\n            c = spot * (1 + drift + random.gauss(0, 0.0003))\n            ts = ds + m * 60\n            rows.append((1, ts, "NIFTY", "NIFTY", "SPOT", None, None, o, max(o, c), min(o, c), c, 0, 0))\n            ms.append((ts, o, c, m))\n            spot = c\n        for K in [atm + 50 * k for k in range(-12, 13)]:\n            for side in ("CE", "PE"):\n                sym = f"NIFTY{exp.replace(\'-\', \'\')}{K}{side}"\n                for (ts, o, c, m) in ms:\n                    tv = 60.0 * (1 - (m - OPEN_M) / 700.0)\n                    f = (lambda s: round(max(s - K, 0) + tv, 2)) if side == "CE" else (lambda s: round(max(K - s, 0) + tv, 2))\n                    a, b = f(o), f(c)\n                    rows.append((2, ts, "NIFTY", sym, side, float(K), exp, a, max(a, b), min(a, b), b, 100, 100))\n    conn.executemany("INSERT INTO backtest_candles_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)\n    conn.commit()\n    conn.close()\n\n\ndef legs(short_tp=0.0):\n    return [{"id": "L1", "action": "SELL", "opt_type": "CE", "lots": 10, "premium_max": 85,\n             "sl_val": 42, "sl_mode": "pct", "tp_val": short_tp, "tp_mode": "pct",\n             "mtc_other_on_sl": True, "mtc_partner": "L2"},\n            {"id": "L2", "action": "SELL", "opt_type": "PE", "lots": 10, "premium_max": 85,\n             "sl_val": 42, "sl_mode": "pct", "tp_val": short_tp, "tp_mode": "pct",\n             "mtc_other_on_sl": True, "mtc_partner": "L1"},\n            {"id": "L3", "action": "BUY", "opt_type": "CE", "lots": 10, "premium_max": 70},\n            {"id": "L4", "action": "BUY", "opt_type": "PE", "lots": 10, "premium_max": 70}]\n\n\nADJ = {"L1": {"enabled": True, "lots": 10, "premium_max": 85, "sl_val": 25, "sl_mode": "pct",\n              "tp_val": 20, "tp_mode": "pct"},\n       "L2": {"enabled": True, "lots": 10, "premium_max": 85, "sl_val": 25, "sl_mode": "pct",\n              "tp_val": 20, "tp_mode": "pct"}}\n\n\ndef key(t):\n    return (t.entry_ts, t.exit_ts, t.condition, t.direction, t.qty, t.tradingsymbol,\n            round(t.entry_price, 4), round(t.exit_price or 0, 4), t.exit_reason)\n\n\ndef t_helper():\n    print("── 1. _short_tp_off ──")\n    from app.backtest.ic.backtest_ic_runner import _short_tp_off\n    from app.backtest.ic.ic_v1_engine import norm_leg\n    src = [norm_leg(l) for l in legs(80)]\n    snap = [dict(l) for l in src]\n    out, n = _short_tp_off(src)\n    check("2 SELL legs switched off", n == 2, n)\n    check("SELL legs tp_val → 0", all(l["tp_val"] == 0 for l in out if l["action"] == "SELL"))\n    check("BUY legs untouched (same objects)", all(o is s for o, s in zip(out, src) if s["action"] == "BUY"))\n    check("SELL legs are copies; input list not mutated", src == snap and all(o is not s for o, s in zip(out, src) if s["action"] == "SELL"))\n    check("everything else on the SELL legs unchanged",\n          all({k: v for k, v in o.items() if k != "tp_val"} == {k: v for k, v in s.items() if k != "tp_val"}\n              for o, s in zip(out, src)))\n    out0, n0 = _short_tp_off([norm_leg(l) for l in legs(0)])\n    check("no short TP → nothing counted", n0 == 0)\n\n\ndef t_runner():\n    print("── 2–5. runner (synthetic corpus) ──")\n    from app.utils.market_hours import is_trading_day\n    from app.backtest.engine.expiry_calendar import expected_expiry_for_day\n    import app.backtest.data.candle_source as _cs\n    from app.backtest.ic.backtest_ic_runner import run_ic_backtest as NEW\n\n    bak = HERE.parent / f"backtest_ic_runner.py.bak-{FENCE}"\n    ORIG = None\n    if bak.exists():\n        from importlib.machinery import SourceFileLoader\n        ldr = SourceFileLoader("ic_runner_orig", str(bak))\n        spec = importlib.util.spec_from_loader("ic_runner_orig", ldr)\n        mod = importlib.util.module_from_spec(spec)\n        sys.modules["ic_runner_orig"] = mod      # dataclasses resolve the module by name\n        ldr.exec_module(mod)\n        ORIG = mod.run_ic_backtest\n\n    td = tempfile.mkdtemp()\n    atexit.register(lambda: shutil.rmtree(td, ignore_errors=True))\n    dbp = os.path.join(td, "bt.db")\n    days, d = [], date(2026, 1, 12)\n    while len(days) < 34:\n        if is_trading_day(d):\n            days.append(d)\n        d += timedelta(days=1)\n    build_corpus(dbp, days)\n    _cs._backtest_db_path = lambda: Path(dbp)\n    D0, D1 = days[1], days[-1]\n    exp_days = {x for x in days if expected_expiry_for_day(x) == x and D0 <= x <= D1}\n\n    def run(fn, cfg, sid="IC_V2"):\n        r = fn(db_path=dbp, strategy_id=sid, underlying="NIFTY", date_from=D0, date_to=D1,\n               config_override=cfg)\n        assert not r.get("aborted"), r.get("reason")\n        return r\n\n    V2 = {"entry_time": "09:18", "exit_mode": "NEXT_OPEN", "next_open_time": "09:16",\n          "expiry_exit_time": "15:15", "adjust_on_sl": True, "adjust_delay_s": 60, "adjust": ADJ}\n    V1 = {"entry_time": "09:18", "exit_time": "15:28"}\n\n    def K(r, pred=lambda t: True):\n        return sorted(key(t) for t in r["trades"] if pred(t))\n\n    on_exp = lambda t: _day(t.entry_ts) in exp_days\n    short = lambda t: t.direction == "SELL" and "ADJ" not in t.condition\n    diag = lambda r: r["summary"].get("diag_ic", {})\n\n    for mode, BASE in (("NEXT_OPEN", V2), ("EOD", V1)):\n        sid = "IC_V2" if mode == "NEXT_OPEN" else "IC_V1"\n        tp = run(NEW, {**BASE, "legs": legs(30)}, sid)\n        notp = run(NEW, {**BASE, "legs": legs(0)}, sid)\n        skip = run(NEW, {**BASE, "legs": legs(30), "short_tp_skip_expiry": True}, sid)\n        ent_exp = {_day(t.entry_ts) for t in tp["trades"] if on_exp(t)}\n        n_tp_exp = sum(1 for t in tp["trades"] if on_exp(t) and short(t) and t.exit_reason == "TP")\n        n_tp_other = sum(1 for t in tp["trades"] if not on_exp(t) and short(t) and t.exit_reason == "TP")\n        print(f"   [{mode}] expiry days entered {len(ent_exp)}; plain-TP run: {n_tp_exp} short TPs on expiry days, {n_tp_other} elsewhere")\n        check(f"[{mode}] corpus exercises it: short TPs on expiry AND non-expiry days", n_tp_exp >= 1 and n_tp_other >= 3)\n        check(f"[{mode}] ON: no short entered on an expiry day exits TP",\n              not any(on_exp(t) and short(t) and t.exit_reason == "TP" for t in skip["trades"]))\n        check(f"[{mode}] ON: non-expiry-day legs ≡ plain-TP run (row-for-row)",\n              K(skip, lambda t: not on_exp(t)) == K(tp, lambda t: not on_exp(t)))\n        check(f"[{mode}] ON: expiry-day legs ≡ no-TP run (row-for-row)",\n              K(skip, on_exp) == K(notp, on_exp))\n        check(f"[{mode}] ON: run actually differs from plain TP", K(skip) != K(tp))\n        check(f"[{mode}] diag counts {len(ent_exp)} expiry days switched off",\n              diag(skip).get("short_tp_expiry_days_off") == len(ent_exp) and diag(skip).get("short_tp_skip_expiry") is True,\n              (diag(skip).get("short_tp_expiry_days_off"), len(ent_exp)))\n        check(f"[{mode}] OFF: no new diag keys", "short_tp_skip_expiry" not in diag(tp)\n              and "short_tp_expiry_days_off" not in diag(tp))\n        z = run(NEW, {**BASE, "legs": legs(0), "short_tp_skip_expiry": True}, sid)\n        check(f"[{mode}] ON with no short TP ≡ no-TP run, 0 days counted",\n              K(z) == K(notp) and diag(z).get("short_tp_expiry_days_off") == 0)\n        if mode == "NEXT_OPEN":\n            adj_tp_exp = [t for t in skip["trades"] if on_exp(t) and "ADJ" in t.condition and t.exit_reason == "TP"]\n            check("[NEXT_OPEN] adjustment-leg TP still fires on expiry day", len(adj_tp_exp) >= 1, len(adj_tp_exp))\n        if ORIG:\n            o = run(ORIG, {**BASE, "legs": legs(30)}, sid)\n            check(f"[{mode}] key absent ≡ ORIGINAL runner (row-for-row)", K(o) == K(tp))\n            check(f"[{mode}] key absent: diag keys identical to ORIGINAL", set(diag(o)) == set(diag(tp)))\n            f = run(NEW, {**BASE, "legs": legs(30), "short_tp_skip_expiry": False}, sid)\n            check(f"[{mode}] key False ≡ ORIGINAL", K(f) == K(o))\n        else:\n            print("  ! .bak not found — ORIGINAL comparison skipped")\n        if mode == "EOD":\n            p = run(NEW, {**BASE, "legs": legs(30), "short_tp_skip_expiry": True, "parallel_workers": 2}, sid)\n            check("[EOD] parallel ×2 ≡ serial", K(p) == K(skip))\n            check("[EOD] parallel diag carries the flag + summed count",\n                  diag(p).get("short_tp_skip_expiry") is True\n                  and diag(p).get("short_tp_expiry_days_off") == diag(skip).get("short_tp_expiry_days_off"),\n                  (diag(p).get("short_tp_skip_expiry"), diag(p).get("short_tp_expiry_days_off")))\n\n\nif __name__ == "__main__":\n    t_helper()\n    t_runner()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} IC SHORT-TP SKIP-EXPIRY CHECKS PASSED")\n'}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        staged[rel] = txt
    for rel, fences in PREREQ.items():
        for f in fences:
            if f not in staged[rel]:
                die(f"prerequisite {f!r} not found in {rel}")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — mixed state; remove it (or git checkout) and re-run")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    for rel in targets:
        if FENCE not in staged[rel]:
            die(f"internal: {rel} edited without the fence marker")
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in [(r, staged[r]) for r in PY] + list(NEW_FILES.items()):
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print(f"  ✓ py_compile (runner + test)")
        esb = next((str(c) for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild")
                    if c.exists()), None) or shutil.which("esbuild")
        if esb:
            for rel in JSX:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print("  ✓ esbuild parsed Backtest / BacktestQueue / RunComparison / SweepBuilder")
        else:
            print("  ! esbuild not found — parse gate skipped (the frontend build will check)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    for rel, txt in NEW_FILES.items():
        (repo / rel).write_text(txt, encoding="utf-8")
        print(f"  ✓ created {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c", "import app.backtest.ic.backtest_ic_runner; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("the IC runner no longer imports")
        print("  ✓ IC runner imports")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                print(f"  ! {suite} not found — skipped")
                continue
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=3600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL  " in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    for rel in targets + list(NEW_FILES):
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied (backtest only — live/paper untouched). Rebuild (./desktop/build-scalp.sh both), then\n"
          f"  Backtest → IC V2 → sealed config, L1/L2 TP 70 / 80 / 90 %, tick 'Short-leg TP: skip expiry day' (3 runs).\n"
          f"  Exports now carry the legs as JSON in CONFIG.")


if __name__ == "__main__":
    main()
