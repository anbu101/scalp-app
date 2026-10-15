#!/usr/bin/env python3
# apply_tma_tick_ltp_publish.py — fence TMA_TICK_LTP_PUBLISH_20261015
#
# BUG (non-admin machines): Velvet and Timberwolf legs had no live LTP.
#   * Trades page: LTP "—" for Velvet / Timberwolf rows
#   * Timberwolf panel: LIVE LTP "—", no progress bars
#   * Velvet panel: price only moves once a minute ("last 1-minute close")
#   * Dashboard "Traded today": Velvet / Timberwolf unrealised frozen at one
#     REST snapshot ("marks as of 09:21") — the table and the panel disagree
# All four read LTPStore (/ltp_snapshot, fleet marks, VET mark_src). Icarus and
# Tigris publish their own legs (IC _poll_ltps, TSG_LTP_PUBLISH 2026-08-12).
# Velvet (VET_V1) and Timberwolf (TMA_V2) — and TMA_V1 — run their OWN
# WebSocket (TMA2TickEngine / TMATickEngine) receiving live ticks for ~122
# contracts, but only ever built candles from them, never published. On your
# machine HA's wide WS universe publishes the same symbols, which hid it.
#
# FIX: both tick engines publish every option tick to LTPStore (spot excluded,
# zero prices skipped), after releasing the engine lock, fail-open (a store
# error can never touch the candle path). Zero extra network calls.
#
# Trading paths: candles are byte-identical (parity proven vs the original
# file). TMA's LTP backstop (_fresh_main_ltp, 20s freshness) and the live GTT
# monitor's price fallback now see fresh prints on machines without HA — the
# same data they already see on yours. Their freshness guards are unchanged.
#
#  EDIT  backend/app/engine/tma2/tma2_tick_engine.py (2)   — Timberwolf + Velvet
#        backend/app/engine/tma/tma_tick_engine.py   (2)   — TMA_V1
#  NEW   backend/app/engine/tma2/test_tick_ltp_publish.py
#  Mirrored to desktop/src-tauri/backend/ when that tree exists.
#  Gates: py_compile, imports, the new suite, then test_tma2_live_core,
#  test_tma2_entry_paths, test_vet_live_core, test_vet_manager. Any failure →
#  every file restored.
#
# Usage: python3 apply_tma_tick_ltp_publish.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script.

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "TMA_TICK_LTP_PUBLISH_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/"}
E2 = "backend/app/engine/tma2/tma2_tick_engine.py"
E1 = "backend/app/engine/tma/tma_tick_engine.py"
T = "backend/app/engine/tma2/test_tick_ltp_publish.py"
PY = [E2, E1]
PREREQ = {E2: ["class TMA2TickEngine", "def _store_candle("], E1: ["class TMATickEngine", "def _store_candle("]}
SUITES = ["app/engine/tma2/test_tick_ltp_publish.py", "app/engine/tma2/test_tma2_live_core.py",
          "app/engine/tma2/test_tma2_entry_paths.py", "app/engine/vet/test_vet_live_core.py",
          "app/engine/vet/test_vet_manager.py"]

EDITS = [
    (E2, 'class CandleBuilder:\n',
     "# ── TMA_TICK_LTP_PUBLISH_20261015 ── this socket's option ticks are the only\n# live price for these legs on a machine without HA's wide WS universe\n# (Trades LTP, panels, Dashboard fleet marks, VET mark_src all read LTPStore).\n# Fail-open: a store error must never reach the candle path.\ndef _publish_ltps(pairs) -> None:\n    try:\n        from app.marketdata.ltp_store import LTPStore\n    except Exception:\n        return\n    for sym, px in pairs:\n        try:\n            LTPStore.update(sym, px)\n        except Exception:\n            pass\n\n\nclass CandleBuilder:\n", 1),
    (E2, '    def _on_ticks(self, ws, ticks):\n        now = int(time.time())\n        with self._lock:\n            for t in ticks:\n                tok = t.get("instrument_token")\n                ltp = t.get("last_price")\n                if tok not in self._builders or not ltp:\n                    continue\n                ts = t.get("exchange_timestamp") or t.get("last_trade_time")\n                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else now\n                done = self._builders[tok].add_tick(float(ltp), epoch)\n                if done:\n                    self._store_candle(tok, done)\n',
     '    def _on_ticks(self, ws, ticks):\n        now = int(time.time())\n        pub = []    # ── TMA_TICK_LTP_PUBLISH_20261015 ── (symbol, ltp) for LTPStore\n        with self._lock:\n            for t in ticks:\n                tok = t.get("instrument_token")\n                ltp = t.get("last_price")\n                if tok not in self._builders or not ltp:\n                    continue\n                ts = t.get("exchange_timestamp") or t.get("last_trade_time")\n                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else now\n                done = self._builders[tok].add_tick(float(ltp), epoch)\n                if done:\n                    self._store_candle(tok, done)\n                if tok != self._spot_token:\n                    sym = self._tok2sym.get(tok)\n                    if sym:\n                        pub.append((sym, float(ltp)))\n        if pub:\n            _publish_ltps(pub)   # outside the engine lock; never raises\n', 1),
    (E1, 'class CandleBuilder:\n',
     "# ── TMA_TICK_LTP_PUBLISH_20261015 ── this socket's option ticks are the only\n# live price for these legs on a machine without HA's wide WS universe\n# (Trades LTP, panels, Dashboard fleet marks, VET mark_src all read LTPStore).\n# Fail-open: a store error must never reach the candle path.\ndef _publish_ltps(pairs) -> None:\n    try:\n        from app.marketdata.ltp_store import LTPStore\n    except Exception:\n        return\n    for sym, px in pairs:\n        try:\n            LTPStore.update(sym, px)\n        except Exception:\n            pass\n\n\nclass CandleBuilder:\n", 1),
    (E1, '    def _on_ticks(self, ws, ticks):\n        now = int(time.time())\n        with self._lock:\n            for t in ticks:\n                tok = t.get("instrument_token")\n                ltp = t.get("last_price")\n                if tok not in self._builders or not ltp:\n                    continue\n                ts = t.get("exchange_timestamp") or t.get("last_trade_time")\n                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else now\n                done = self._builders[tok].add_tick(float(ltp), epoch)\n                if done:\n                    self._store_candle(tok, done)\n',
     '    def _on_ticks(self, ws, ticks):\n        now = int(time.time())\n        pub = []    # ── TMA_TICK_LTP_PUBLISH_20261015 ── (symbol, ltp) for LTPStore\n        with self._lock:\n            for t in ticks:\n                tok = t.get("instrument_token")\n                ltp = t.get("last_price")\n                if tok not in self._builders or not ltp:\n                    continue\n                ts = t.get("exchange_timestamp") or t.get("last_trade_time")\n                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else now\n                done = self._builders[tok].add_tick(float(ltp), epoch)\n                if done:\n                    self._store_candle(tok, done)\n                if tok != self._spot_token:\n                    sym = self._tok2sym.get(tok)\n                    if sym:\n                        pub.append((sym, float(ltp)))\n        if pub:\n            _publish_ltps(pub)   # outside the engine lock; never raises\n', 1),
]

NEW_FILES = {T: '# backend/app/engine/tma2/test_tick_ltp_publish.py — fence TMA_TICK_LTP_PUBLISH_20261015\n#\n#   1. Both tick engines (TMA2TickEngine — used by Timberwolf AND Velvet — and\n#      TMA_V1\'s clone) publish every option tick into LTPStore; spot, unknown\n#      tokens and zero prices are not published\n#   2. Candle parity: a 3,000-tick randomized stream produces IDENTICAL option\n#      and spot candles through the patched engine and the ORIGINAL (.bak)\n#   3. A failing LTPStore never touches the candle path\n#   4. Consumers now see live marks: Velvet route _mark_for → "LTP" (panel\n#      ticks), fleet _mark → live (not "marks as of"), TMA2 backstop\'s\n#      _fresh_main_ltp reads a fresh print but still rejects a stale one\n#\n# Run: cd backend && python3 app/engine/tma2/test_tick_ltp_publish.py\n\nfrom __future__ import annotations\n\nimport importlib.util\nimport os\nimport random\nimport sys\nimport tempfile\nimport time\nfrom importlib.machinery import SourceFileLoader\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve()\nBACKEND = str(HERE.parents[3])\nif BACKEND not in sys.path:\n    sys.path.insert(0, BACKEND)\nos.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())\n\nFENCE = "TMA_TICK_LTP_PUBLISH_20261015"\nFAILS: list = []\nOK = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nfrom app.marketdata.ltp_store import LTPStore  # noqa: E402\nimport app.engine.tma2.tma2_tick_engine as E2  # noqa: E402\nimport app.engine.tma.tma_tick_engine as E1  # noqa: E402\n\nSPOT = 256265\nOPTS = {9001: "NIFTY26O1922800PE", 9002: "NIFTY26O1921700PE", 9003: "NIFTY26O1922650CE"}\n\n\ndef clear():\n    with LTPStore._lock:\n        for s in list(OPTS.values()) + ["NIFTY 50", "NIFTY50", str(SPOT), "UNKNOWN"]:\n            LTPStore._prices.pop(s, None)\n            LTPStore._timestamps.pop(s, None)\n\n\ndef engine(mod, cls_name):\n    e = getattr(mod, cls_name)(None, None, lambda *a: None, capture_dir=None)\n    e._spot_token = SPOT\n    e._builders = {SPOT: mod.CandleBuilder()}\n    e._tok2sym = {}\n    for tok, sym in OPTS.items():\n        e._builders[tok] = mod.CandleBuilder()\n        e._tok2sym[tok] = sym\n    return e\n\n\ndef tick(tok, px, epoch=None):\n    t = {"instrument_token": tok, "last_price": px}\n    if epoch is not None:\n        class _TS:\n            def __init__(self, v): self.v = v\n            def timestamp(self): return self.v\n        t["exchange_timestamp"] = _TS(epoch)\n    return t\n\n\ndef load_orig(rel, name):\n    bak = Path(BACKEND) / rel\n    bak = bak.with_name(bak.name + f".bak-{FENCE}")\n    if not bak.exists():\n        return None\n    ldr = SourceFileLoader(name, str(bak))\n    spec = importlib.util.spec_from_loader(name, ldr)\n    mod = importlib.util.module_from_spec(spec)\n    ldr.exec_module(mod)\n    return mod\n\n\ndef t_publish(mod, cls_name, label):\n    print(f"── 1. {label}: publish ──")\n    clear()\n    e = engine(mod, cls_name)\n    t0 = time.time()\n    e._on_ticks(None, [tick(9001, 170.6), tick(9002, 4.65), tick(SPOT, 22671.3),\n                       tick(4242, 99.0), tick(9003, 0), tick(9003, None)])\n    got = LTPStore.get_with_timestamp(OPTS[9001])\n    check(f"{label}: sold leg published (170.60)", got is not None and got[0] == 170.6, got)\n    check(f"{label}: hedge leg published (4.65)", LTPStore.get(OPTS[9002]) == 4.65)\n    check(f"{label}: stamp is wall-clock now (fresh for every consumer)",\n          got is not None and abs(got[1] - t0) < 5, got)\n    check(f"{label}: zero / None price not published", LTPStore.get(OPTS[9003]) is None)\n    check(f"{label}: spot token NOT written into LTPStore",\n          all(LTPStore.get(k) is None for k in ("NIFTY 50", "NIFTY50", str(SPOT))))\n    check(f"{label}: token outside the universe ignored", LTPStore.get("UNKNOWN") is None)\n    e._on_ticks(None, [tick(9001, 171.25)])\n    check(f"{label}: next tick overwrites (live, tick-by-tick)", LTPStore.get(OPTS[9001]) == 171.25)\n\n    # failing store never touches candles\n    clear()\n    real = LTPStore.update\n    try:\n        LTPStore.update = classmethod(lambda cls, s, p: (_ for _ in ()).throw(RuntimeError("store down")))\n        e = engine(mod, cls_name)\n        base = (int(time.time()) // 60) * 60 - 600\n        err = None\n        try:\n            e._on_ticks(None, [tick(9001, 100.0, base + 1), tick(9001, 101.0, base + 30)])\n            e._on_ticks(None, [tick(9001, 102.0, base + 61)])      # rolls the minute\n        except Exception as x:\n            err = x\n    finally:\n        LTPStore.update = real\n    c = e.chain._c.get(OPTS[9001], {}).get(base)\n    check(f"{label}: LTPStore raising → no exception, candle still built",\n          err is None and c is not None and c["close"] == 101.0, (err, c))\n\n\ndef t_parity(mod, orig, cls_name, label):\n    print(f"── 2. {label}: candle parity vs ORIGINAL ──")\n    if orig is None:\n        print("  ! .bak not found — parity vs original skipped")\n        return\n    random.seed(11)\n    new_e, old_e = engine(mod, cls_name), engine(orig, cls_name)\n    base = (int(time.time()) // 60) * 60 - 3 * 3600\n    toks = list(OPTS) + [SPOT, 4242]\n    px = {t: 100.0 for t in toks}\n    px[SPOT] = 22600.0\n    ep = base\n    for _ in range(300):\n        batch = []\n        for _ in range(10):\n            tk = random.choice(toks)\n            px[tk] = max(0.05, px[tk] + random.gauss(0, 1.5))\n            ep += random.choice((0, 1, 2, 3))\n            batch.append(tick(tk, round(px[tk], 2) if random.random() > 0.03 else 0, ep))\n        new_e._on_ticks(None, [dict(t) for t in batch])\n        old_e._on_ticks(None, [dict(t) for t in batch])\n    same_opts = new_e.chain._c == old_e.chain._c\n    n = sum(len(v) for k, v in new_e.chain._c.items() if k in OPTS.values())\n    check(f"{label}: 3,000 random ticks → identical option candles ({n} bars)", same_opts and n > 50, n)\n    check(f"{label}: identical spot candles", new_e._spot_candles == old_e._spot_candles\n          and len(new_e._spot_candles) > 10, len(new_e._spot_candles))\n    check(f"{label}: identical builder state at the end",\n          all(vars(new_e._builders[t]) == vars(old_e._builders[t]) for t in toks if t in new_e._builders))\n\n\ndef t_consumers():\n    print("── 4. consumers see live marks ──")\n    clear()\n    e = engine(E2, "TMA2TickEngine")\n    e._on_ticks(None, [tick(9003, 148.75), tick(9001, 170.6)])\n    try:\n        import app.api.vet_state_routes as V\n        px, src = V._mark_for(OPTS[9003], lambda s: 140.0)\n        check("Velvet route: mark_src \'LTP\' → panel ticks live (was QUOTE_1M)", src == "LTP" and px == 148.75, (px, src))\n        with LTPStore._lock:\n            LTPStore._timestamps[OPTS[9003]] = time.time() - 3600\n        px, src = V._mark_for(OPTS[9003], lambda s: 140.0)\n        check("Velvet route: a stale print still loses to its own 1m quote", src == "QUOTE_1M" and px == 140.0, (px, src))\n    except ImportError as x:\n        print(f"  ! vet_state_routes not importable here ({x}) — skipped")\n    try:\n        import app.api.fleet_today_routes as F\n        now = int(time.time())\n        ltp, ts, stale = F._mark(OPTS[9001], now)\n        check("Dashboard fleet row: Timberwolf leg marked LIVE (not \'marks as of\')",\n              ltp == 170.6 and stale is False, (ltp, ts, stale))\n    except ImportError as x:\n        print(f"  ! fleet_today_routes not importable here ({x}) — skipped")\n    import app.engine.tma2.tma2_trade_manager as TM\n    m = TM.TMA2TradeManager.__new__(TM.TMA2TradeManager) if hasattr(TM, "TMA2TradeManager") else None\n    if m is not None and hasattr(m, "_fresh_main_ltp"):\n        check("TMA2 backstop reads a fresh print (≤20s)", m._fresh_main_ltp(OPTS[9001], max_age_s=20) == 170.6)\n        with LTPStore._lock:\n            LTPStore._timestamps[OPTS[9001]] = time.time() - 120\n        check("TMA2 backstop still REJECTS a stale print (overnight-trap guard intact)",\n              m._fresh_main_ltp(OPTS[9001], max_age_s=20) is None)\n    else:\n        print("  ! TMA2 trade manager class not found by name — backstop check skipped")\n    clear()\n\n\nif __name__ == "__main__":\n    o2 = load_orig("app/engine/tma2/tma2_tick_engine.py", "tma2_tick_orig")\n    o1 = load_orig("app/engine/tma/tma_tick_engine.py", "tma_tick_orig")\n    t_publish(E2, "TMA2TickEngine", "TMA2 engine (Timberwolf + Velvet)")\n    t_publish(E1, "TMATickEngine", "TMA_V1 engine")\n    t_parity(E2, o2, "TMA2TickEngine", "TMA2 engine")\n    t_parity(E1, o1, "TMATickEngine", "TMA_V1 engine")\n    t_consumers()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} TICK LTP PUBLISH CHECKS PASSED")\n'}


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
        print(f"  ✓ py_compile ({len(PY)} edited files + {len(NEW_FILES)} new)")
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
                   SCALP_APP_HOME=tempfile.mkdtemp(prefix="scalp_test_home_"),
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c",
                            "import app.engine.tma2.tma2_tick_engine, app.engine.tma.tma_tick_engine, app.engine.vet.vet_selection_loop; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("a touched module no longer imports")
        print("  ✓ tma2 / tma tick engines + vet selection loop import")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                print(f"  ! {suite} not found — skipped")
                continue
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=600)
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
    print(f"\n✓ {FENCE} applied. Rebuild (deploy-scalp.command), then on a NON-admin machine:\n"
          f"  • Trades page: Velvet / Timberwolf rows show a ticking LTP\n"
          f"  • Timberwolf panel: LIVE LTP + progress bars; Velvet panel: 'ticking · before charges'\n"
          f"  • Dashboard 'Traded today': Velvet / Timberwolf unrealised moves with the panels\n"
          f"  Candle path unchanged (parity-proven); still deploy outside 09:00–15:30 IST.")

if __name__ == "__main__":
    main()
