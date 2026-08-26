#!/usr/bin/env python3
# apply_scalp_v3_parallel_20260826.py
#
# SCALP_V3 PARALLEL BACKTEST (port of SCALP_V1_PARALLEL / IC_PARALLEL).
# The V3 runner is serial-only — a 6.5y run is ~14 min. This ports the
# fleet's parallel-days pattern with ONE V3-specific refinement:
#
#   MONTH-ALIGNED CHUNKS. V1 slices days contiguously, which can split a
#   calendar month across two workers. V3 carries MONTHLY risk-limit buckets
#   (monthly_max_loss/profit) that accumulate across days — a month split
#   across processes would compute wrong monthly cums whenever those caps
#   are on. Chunk boundaries therefore snap to month starts: every worker
#   gets whole months, so daily AND monthly buckets are exact in every
#   config, byte-identical to serial by construction. (Positions never
#   cross a day boundary — the EOD + STALE_FORCE_CLOSE invariant — so
#   day-level independence already holds.)
#
# Backend (backtest_hedge_runner.py — BACKTEST-ONLY, safe to apply today):
#   • module-level _hedge_parallel_worker (spawn-picklable, audit-muted,
#     child forced serial), returning trades + coverage + risk/count stats
#   • parallel branch in run_hedge_backtest: month-aligned chunking, spawn
#     pool, LOUD failure (no silent serial fallback — IC precedent), merge
#     trades sorted (entry_ts, hedge_symbol), merge coverage / risk_limits
#     (sums + months_blocked union) / trade_count_limits, _summarize over
#     the merged list, serial-identical return shape
#
# Frontend (Backtest.jsx):
#   • v3Workers state (default 4, persisted), Workers field in the V3 row,
#     cfg.parallel_workers emitted in the hedge buildConfig branch
#   • v3Workers added to saveParams object + saveParams deps + buildConfig
#     deps (all three mirrored lists, SAME commit — stale-closure rule)
#   • deliberately NOT added to RunComparison/paramFormat: workers cannot
#     change results, so runs differing only in workers must compare equal
#
# ACCEPTANCE: run the canonical baseline config with Workers=6 and diff the
# CSV against 95e70e7e — byte-identical or this patch is reverted.
#
# MECHANICS: dual-tree for backend; frontend patched at frontend/src/pages
# and any desktop mirror found; anchor-assertion replace with per-edit
# expected counts; staged py_compile for .py; chunker behavioral sim.

import glob
import os
import py_compile
import sys
import tempfile

REPO = os.getcwd()
BACKEND_TREES = ["backend", os.path.join("desktop", "src-tauri", "backend")]
RUNNER = os.path.join("app", "backtest", "runner", "backtest_hedge_runner.py")

FENCE = "SCALP_V3_PARALLEL_20260826"


def fail(msg):
    print(f"\n[ABORT] {msg}\nNothing was written.")
    sys.exit(1)


# ---------------------------------------------------------------- backend --

WORKER_FN = (
    "# \u2500\u2500 SCALP_V3_PARALLEL_20260826 BEGIN: parallel-days machinery \u2500\u2500\n"
    "# SCALP_V1_PARALLEL / IC_PARALLEL pattern: module-level worker\n"
    "# (spawn-picklable) that recursively runs its contiguous chunk SERIALLY\n"
    "# with audit muted, returning picklable HedgeClosedTrade dataclasses plus\n"
    "# the chunk's coverage and guard stats for parent-side merging.\n"
    "def _hedge_parallel_worker(strategy_id: str, underlying: str,\n"
    "                           date_from_iso: str, date_to_iso: str,\n"
    "                           cfg: dict) -> dict:\n"
    "    child_cfg = dict(cfg)\n"
    "    child_cfg[\"parallel_workers\"] = 1          # child MUST run serial\n"
    "    try:\n"
    "        from app.event_bus.audit_logger import audit_muted\n"
    "        _mute = audit_muted()\n"
    "    except Exception:                          # audit_muted unavailable \u2192 run unmuted\n"
    "        import contextlib\n"
    "        _mute = contextlib.nullcontext()\n"
    "    with _mute:\n"
    "        out = run_hedge_backtest(\n"
    "            strategy_id=strategy_id, underlying=underlying,\n"
    "            date_from=date.fromisoformat(date_from_iso),\n"
    "            date_to=date.fromisoformat(date_to_iso),\n"
    "            config_override=child_cfg, progress_cb=None)\n"
    "    _s = out.get(\"summary\") or {}\n"
    "    return {\"trades\": out[\"trades\"],\n"
    "            \"coverage\": out.get(\"coverage\") or {},\n"
    "            \"risk_limits\": _s.get(\"risk_limits\") or {},\n"
    "            \"trade_count_limits\": _s.get(\"trade_count_limits\") or {}}\n"
    "\n"
    "\n"
    "def _month_aligned_chunks(days, n_workers):\n"
    "    \"\"\"Contiguous chunks whose boundaries snap to calendar-month starts.\n"
    "    Whole months per worker \u21d2 V3's monthly risk buckets accumulate inside\n"
    "    one process \u2014 exact for every config, not just caps-off runs.\"\"\"\n"
    "    import math as _math\n"
    "    months, cur_key = [], None\n"
    "    for d in days:\n"
    "        k = (d.year, d.month)\n"
    "        if k != cur_key:\n"
    "            months.append([])\n"
    "            cur_key = k\n"
    "        months[-1].append(d)\n"
    "    chunks, cur = [], []\n"
    "    chunks_left = max(1, n_workers)\n"
    "    rem = len(days)\n"
    "    for ds in months:\n"
    "        cur.extend(ds)\n"
    "        rem -= len(ds)\n"
    "        # dynamic linear partition: close AFTER adding, against a target\n"
    "        # recomputed over the REMAINING days and chunk budget \u2014 a static\n"
    "        # target closes under-filled single-month chunks and dumps the\n"
    "        # leftover months into a bloated tail (caught by the apply sim).\n"
    "        if chunks_left > 1 and len(cur) >= _math.ceil((len(cur) + rem) / chunks_left):\n"
    "            chunks.append(cur)\n"
    "            cur = []\n"
    "            chunks_left -= 1\n"
    "    if cur:\n"
    "        chunks.append(cur)\n"
    "    return chunks\n"
    "# \u2500\u2500 SCALP_V3_PARALLEL_20260826 END: parallel-days machinery \u2500\u2500\n"
    "\n"
    "\n"
)

PARALLEL_BRANCH = (
    "    # \u2500\u2500 SCALP_V3_PARALLEL_20260826 BEGIN: shard MONTHS across processes \u2500\u2500\n"
    "    try:\n"
    "        _n_workers = int(cfg.get(\"parallel_workers\", 1) or 1)\n"
    "    except (TypeError, ValueError):\n"
    "        _n_workers = 1\n"
    "    if _n_workers > 1:\n"
    "        _all_days = _trading_days(date_from, date_to)\n"
    "        _chunks = _month_aligned_chunks(_all_days, _n_workers)\n"
    "        if len(_chunks) > 1:\n"
    "            from concurrent.futures import ProcessPoolExecutor, as_completed\n"
    "            from multiprocessing import get_context\n"
    "            write_audit_log(\n"
    "                f\"[BACKTEST_HEDGE] START run={run_id} {strategy_id}/{underlying} \"\n"
    "                f\"{date_from}..{date_to} days={len(_all_days)} \"\n"
    "                f\"PARALLEL workers={_n_workers} month_aligned_chunks={len(_chunks)}\")\n"
    "            _merged: list = []\n"
    "            _cov_m = {\"days_total\": len(_all_days), \"days_covered\": 0,\n"
    "                      \"days_skipped\": 0, \"skipped\": []}\n"
    "            _rl_m = {\"risk_exits\": 0, \"days_blocked\": 0, \"months_blocked\": set()}\n"
    "            _tc_m = {\"max_trades_per_day\": max(0, int(cfg.get(\"max_trades_per_day\") or 0)),\n"
    "                     \"max_trades_per_side_per_day\": max(0, int(cfg.get(\"max_trades_per_side_per_day\") or 0)),\n"
    "                     \"entries_blocked_day_cap\": 0, \"entries_blocked_side_cap\": 0}\n"
    "            _days_done = 0\n"
    "            try:\n"
    "                with ProcessPoolExecutor(\n"
    "                        max_workers=len(_chunks),\n"
    "                        mp_context=get_context(\"spawn\")) as _pool:\n"
    "                    _futs = {_pool.submit(\n"
    "                        _hedge_parallel_worker, strategy_id, underlying,\n"
    "                        ch[0].isoformat(), ch[-1].isoformat(), cfg): ch\n"
    "                        for ch in _chunks}\n"
    "                    for _fut in as_completed(_futs):\n"
    "                        _out = _fut.result()\n"
    "                        _merged.extend(_out[\"trades\"])\n"
    "                        _c = _out.get(\"coverage\") or {}\n"
    "                        _cov_m[\"days_covered\"] += _c.get(\"days_covered\", 0)\n"
    "                        _cov_m[\"days_skipped\"] += _c.get(\"days_skipped\", 0)\n"
    "                        _cov_m[\"skipped\"].extend(_c.get(\"skipped\", []))\n"
    "                        _r = _out.get(\"risk_limits\") or {}\n"
    "                        _rl_m[\"risk_exits\"] += int(_r.get(\"risk_exits\", 0) or 0)\n"
    "                        _rl_m[\"days_blocked\"] += int(_r.get(\"days_blocked\", 0) or 0)\n"
    "                        _rl_m[\"months_blocked\"].update(_r.get(\"months_blocked\") or [])\n"
    "                        _t = _out.get(\"trade_count_limits\") or {}\n"
    "                        _tc_m[\"entries_blocked_day_cap\"] += int(_t.get(\"entries_blocked_day_cap\", 0) or 0)\n"
    "                        _tc_m[\"entries_blocked_side_cap\"] += int(_t.get(\"entries_blocked_side_cap\", 0) or 0)\n"
    "                        _days_done += len(_futs[_fut])\n"
    "                        if progress_cb:\n"
    "                            progress_cb({\"day\": _days_done,\n"
    "                                         \"total_days\": len(_all_days),\n"
    "                                         \"date\": _futs[_fut][-1].isoformat(),\n"
    "                                         \"watched\": 0})\n"
    "            except Exception as _exc:\n"
    "                # LOUD, not silent-serial: a quiet fallback would mask a\n"
    "                # missing freeze_support guard and silently cost the user\n"
    "                # the speedup they configured (IC_PARALLEL precedent).\n"
    "                raise RuntimeError(\n"
    "                    f\"{strategy_id} parallel execution failed: {_exc!r} \u2014 \"\n"
    "                    f\"rerun with parallel_workers=1\") from _exc\n"
    "            _merged.sort(key=lambda t: (t.entry_ts, t.hedge_symbol))\n"
    "            _cov_m[\"skipped\"].sort(key=lambda s: s.get(\"date\", \"\"))\n"
    "            summary = _summarize(_merged, started)\n"
    "            summary[\"run_id\"] = run_id\n"
    "            summary[\"summary\"][\"coverage\"] = _cov_m\n"
    "            _rl_m[\"months_blocked\"] = sorted(_rl_m[\"months_blocked\"])\n"
    "            summary[\"summary\"][\"risk_limits\"] = _rl_m\n"
    "            summary[\"summary\"][\"trade_count_limits\"] = _tc_m\n"
    "            write_audit_log(\n"
    "                f\"[BACKTEST_HEDGE] DONE run={run_id} trades={len(_merged)} \"\n"
    "                f\"gross={summary['summary']['gross_pnl']:.2f} \"\n"
    "                f\"charges={summary['summary']['total_charges']:.2f} \"\n"
    "                f\"net={summary['summary']['net_pnl']:.2f} \"\n"
    "                f\"win_rate={summary['summary']['win_rate']:.1f}% \"\n"
    "                f\"workers={_n_workers} \"\n"
    "                f\"elapsed={summary['summary']['elapsed_s']}s\")\n"
    "            write_audit_log(\n"
    "                f\"[BACKTEST_HEDGE][COVERAGE] days_total={_cov_m['days_total']} \"\n"
    "                f\"covered={_cov_m['days_covered']} skipped={_cov_m['days_skipped']}\")\n"
    "            return {\"run_id\": run_id, \"summary\": summary[\"summary\"],\n"
    "                    \"trades\": _merged, \"config\": cfg, \"coverage\": _cov_m}\n"
    "    # \u2500\u2500 SCALP_V3_PARALLEL_20260826 END (serial path continues below) \u2500\u2500\n"
    "\n"
)

RUNNER_EDITS = [
    # worker + chunker before _hedge_sl_points
    (
        "def _hedge_sl_points(cfg: dict) -> float:\n",
        WORKER_FN + "def _hedge_sl_points(cfg: dict) -> float:\n",
        1,
    ),
    # parallel branch between cfg merge and the BT override install
    (
        "    if config_override:\n"
        "        cfg = _deep_merge(cfg, config_override)\n"
        "\n"
        "    # \u2500\u2500 BT_CONFIG_OVERRIDE: on_candle reads load_strategy_config(strategy_id)\n",
        "    if config_override:\n"
        "        cfg = _deep_merge(cfg, config_override)\n"
        "\n"
        + PARALLEL_BRANCH +
        "    # \u2500\u2500 BT_CONFIG_OVERRIDE: on_candle reads load_strategy_config(strategy_id)\n",
        1,
    ),
]

# --------------------------------------------------------------- frontend --

JSX_EDITS = [
    (
        "  const [v1Workers, setV1Workers] = useState(saved.v1Workers ?? 4);\n",
        "  const [v1Workers, setV1Workers] = useState(saved.v1Workers ?? 4);\n"
        "  const [v3Workers, setV3Workers] = useState(saved.v3Workers ?? 4);   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500\n",
        1,
    ),
    (
        "      cfg.hedge_sl_points = Number(hedgeSl);\n",
        "      cfg.hedge_sl_points = Number(hedgeSl);\n"
        "      // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500 always emitted (default 1 = serial);\n"
        "      // deliberately NOT in RunComparison PARAM_KEYS \u2014 cannot change results.\n"
        "      cfg.parallel_workers = Number(v3Workers) || 1;\n",
        1,
    ),
    (
        "              <Field label=\"Max Trades/Side/Day\"><input type=\"number\" min=\"0\" step=\"1\" style={inputStyle} value={v3MaxTradesSide} onChange={(e) => setV3MaxTradesSide(e.target.value)} /></Field>\n",
        "              <Field label=\"Max Trades/Side/Day\"><input type=\"number\" min=\"0\" step=\"1\" style={inputStyle} value={v3MaxTradesSide} onChange={(e) => setV3MaxTradesSide(e.target.value)} /></Field>\n"
        "              {/* \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500 1 = serial; N>1 = N processes,\n"
        "                  identical results (month-aligned day sharding). */}\n"
        "              <Field label=\"Workers\"><input type=\"number\" min=\"1\" max=\"16\" style={inputStyle} value={v3Workers} onChange={(e) => setV3Workers(e.target.value)} /></Field>\n",
        1,
    ),
    # all THREE mirrored lists (saveParams object, saveParams deps,
    # buildConfig deps) in the SAME commit — stale-closure rule.
    (
        "      v3MaxTradesDay, v3MaxTradesSide,   // \u2500\u2500 V3_TRADE_COUNT_LIMITS \u2500\u2500\n",
        "      v3MaxTradesDay, v3MaxTradesSide,   // \u2500\u2500 V3_TRADE_COUNT_LIMITS \u2500\u2500\n"
        "      v3Workers,   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500\n",
        3,
    ),
]


def apply_edits(path, edits, fence):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if fence in text:
        print(f"[SKIP] fence already present: {os.path.relpath(path, REPO)}")
        return None
    for i, (old, new, want) in enumerate(edits, 1):
        n = text.count(old)
        if n != want:
            fail(f"anchor #{i} matched {n}x (need exactly {want}) in "
                 f"{os.path.relpath(path, REPO)}")
        text = text.replace(old, new)
    return text


def sim_chunker():
    """Month-aligned chunker replica: contiguity, month atomicity, coverage,
    balance, and degenerate cases."""
    import math
    from datetime import date, timedelta

    def trading_days(d1, d2):
        out, d = [], d1
        while d <= d2:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out

    def chunks_of(days, n):
        months, cur_key = [], None
        for d in days:
            k = (d.year, d.month)
            if k != cur_key:
                months.append([]); cur_key = k
            months[-1].append(d)
        chunks, cur = [], []
        chunks_left = max(1, n)
        rem = len(days)
        for ds in months:
            cur.extend(ds)
            rem -= len(ds)
            if chunks_left > 1 and len(cur) >= math.ceil((len(cur) + rem) / chunks_left):
                chunks.append(cur); cur = []; chunks_left -= 1
        if cur:
            chunks.append(cur)
        return chunks

    for (d1, d2, n) in [(date(2020, 1, 1), date(2026, 7, 20), 6),
                        (date(2026, 3, 2), date(2026, 3, 31), 4),
                        (date(2024, 1, 1), date(2024, 12, 31), 8),
                        (date(2026, 7, 1), date(2026, 7, 3), 4)]:
        days = trading_days(d1, d2)
        ch = chunks_of(days, n)
        flat = [d for c in ch for d in c]
        assert flat == days, "chunks must cover all days exactly, in order"
        assert len(ch) <= n, "never more chunks than workers"
        for c in ch:
            assert all(c[i] < c[i + 1] for i in range(len(c) - 1))
        # month atomicity: no (year,month) appears in two chunks
        seen = {}
        for ci, c in enumerate(ch):
            for d in c:
                k = (d.year, d.month)
                assert seen.setdefault(k, ci) == ci, f"month {k} split across chunks"
        if len(ch) > 1:
            sizes = [len(c) for c in ch]
            # month atomicity bounds any chunk by target + one whole month
            month_max = max(
                sum(1 for d in days if (d.year, d.month) == k)
                for k in {(d.year, d.month) for d in days})
            assert max(sizes) <= math.ceil(len(days) / n) + month_max, \
                "grossly unbalanced"
    print("[SIM] month-aligned chunker: coverage/order/atomicity/balance OK "
          "across 4 scenarios")


def main():
    sim_chunker()

    staged = []

    # backend, dual tree
    trees = [t for t in BACKEND_TREES if os.path.isdir(os.path.join(REPO, t, "app"))]
    if not trees:
        fail("no backend tree found — run from the scalp-app repo root")
    for tree in trees:
        path = os.path.join(REPO, tree, RUNNER)
        if not os.path.isfile(path):
            fail(f"missing file: {path}")
        text = apply_edits(path, RUNNER_EDITS, FENCE)
        if text is None:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(text); tmp = tf.name
        try:
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"staged compile failed for {tree}/{RUNNER}:\n{e}")
        finally:
            os.unlink(tmp)
        staged.append((path, text))
        print(f"[OK] staged {tree}/{RUNNER} (compiles)")

    # frontend: main tree + any desktop mirror carrying Backtest.jsx
    jsx_paths = [os.path.join(REPO, "frontend", "src", "pages", "Backtest.jsx")]
    jsx_paths += sorted(set(
        glob.glob(os.path.join(REPO, "desktop", "**", "Backtest.jsx"),
                  recursive=True)) - set(jsx_paths))
    jsx_found = [p for p in jsx_paths if os.path.isfile(p)]
    if not jsx_found:
        fail("Backtest.jsx not found")
    if len(jsx_found) == 1:
        print("[WARN] only ONE Backtest.jsx found — if the desktop tree keeps "
              "a frontend mirror, rsync/diff it before building.")
    for path in jsx_found:
        text = apply_edits(path, JSX_EDITS, FENCE)
        if text is None:
            continue
        staged.append((path, text))
        print(f"[OK] staged {os.path.relpath(path, REPO)}")

    if not staged:
        print("\n[DONE] nothing to do — all fences already present.")
        return

    for path, text in staged:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[WROTE] {os.path.relpath(path, REPO)}")

    # post-write structural asserts
    for path, _ in staged:
        with open(path, "r", encoding="utf-8") as f:
            t = f.read()
        if path.endswith(".py"):
            assert "def _hedge_parallel_worker(" in t
            assert "_month_aligned_chunks(" in t
            assert "mp_context=get_context(\"spawn\")" in t
        if path.endswith(".jsx"):
            assert t.count("v3Workers,   // \u2500\u2500 SCALP_V3_PARALLEL_20260826 \u2500\u2500") == 3
            assert "cfg.parallel_workers = Number(v3Workers) || 1;" in t
            assert t.count("value={v3Workers}") == 1
    print("\n[PASS] all structural asserts hold.")
    print("Frontend needs a rebuild; run the esbuild JSX syntax check first:")
    print("  npx --no-install esbuild frontend/src/pages/Backtest.jsx --loader:.jsx=jsx --outfile=/dev/null")
    print("ACCEPTANCE: canonical baseline config + Workers=6, diff CSV vs")
    print("95e70e7e — byte-identical or revert.")


if __name__ == "__main__":
    main()
