#!/usr/bin/env python3
# apply_vet_sltp_live.py — fence VET_SLTP_LIVE_20261015
#
# BUG: Settings → VET V1 offers "SL % / TP % of entry premium" and the VET
# BACKTEST honours them (backtest_vet_runner step 1c), but the LIVE/PAPER
# engine never implemented them — a configured TP 90% silently did nothing
# (no level on the Trades page, "no SL/TP by design" on the panel).
#
# FIX — live mirrors the runner exactly (parity-tested, 400 random sessions):
#   * levels = % of the MAIN leg's entry premium (SHORT: SL above, TP below),
#     read from the CURRENT Settings at entry and stamped on the leg
#     (vet_trades.sl / .tp, added idempotently) — editing Settings never moves
#     an open position's levels; a position opened before this fix (no
#     levels) takes them from current Settings when adopted at boot;
#   * checked at each 5-minute decision close BEFORE the signal, on the same
#     mark VET's exits use (last 1m close); SL wins over TP; exit at that mark,
#     wing closed with it (short first);
#   * after an SL/TP exit no re-entry while the trend target is unchanged
#     (runner block_target), re-derived after a restart; reenter_after_sltp
#     honoured;
#   * both % = 0 (every sealed config) → no levels → behaviour unchanged.
#   Display: Trades page SL/TP columns, Velvet panel header / track / exits,
#   Settings help text says how live applies them.
#
#  EDIT  backend/app/engine/vet/vet_common.py          (1)
#        backend/app/engine/vet/vet_manager.py         (6)
#        backend/app/engine/vet/vet_selection_loop.py  (5)
#        backend/app/api/vet_state_routes.py           (2)
#        frontend/src/strategies/vet/VETPanel.jsx      (5)
#        frontend/src/pages/Settings.jsx               (1)
#  NEW   backend/app/engine/vet/test_vet_sltp_live.py
#  Requires STRATEGY_INDEPENDENCE_20261015 first. Mirrored to desktop/src-tauri.
#  Gates: py_compile, esbuild, imports, the new suite + VET regression suites.
#
# Usage: python3 apply_vet_sltp_live.py [--repo PATH] [--allow-dirty] [--skip-tests] [--skip-esbuild]
# Run ONLY this script.

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "VET_SLTP_LIVE_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
VC = "backend/app/engine/vet/vet_common.py"
VMG = "backend/app/engine/vet/vet_manager.py"
VL = "backend/app/engine/vet/vet_selection_loop.py"
VR = "backend/app/api/vet_state_routes.py"
VP = "frontend/src/strategies/vet/VETPanel.jsx"
ST = "frontend/src/pages/Settings.jsx"
T = "backend/app/engine/vet/test_vet_sltp_live.py"
PREREQ = {VL: ["STRATEGY_INDEPENDENCE_20261015"], VMG: ["def resume_from_db(", "def close_position("],
          VR: ["def _leg_view("]}
SUITES = ["app/engine/vet/test_vet_sltp_live.py", "app/engine/vet/test_vet_manager.py",
          "app/engine/vet/test_vet_live_core.py", "app/engine/vet/test_vet_live_dte_lots.py",
          "app/engine/vet/test_vet_live_signal_engine.py", "app/marketdata/test_strategy_independence.py"]

EDITS = [
    (VC, '    def ensure_schema(self) -> None:\n        with self._conn() as c:\n            c.executescript(VET_SCHEMA)\n',
     '    def ensure_schema(self) -> None:\n        with self._conn() as c:\n            c.executescript(VET_SCHEMA)\n            self._ensure_level_cols(c)   # ── VET_SLTP_LIVE_20261015 ──\n\n    # ── VET_SLTP_LIVE_20261015 ── per-leg SL / TP price levels, stamped at\n    # entry (the Trades page reads row["sl"] / row["tp"] for SHORT legs).\n    @staticmethod\n    def _ensure_level_cols(c) -> None:\n        have = {r[1] for r in c.execute("PRAGMA table_info(vet_trades)")}\n        for col in ("sl", "tp"):\n            if col not in have:\n                c.execute(f"ALTER TABLE vet_trades ADD COLUMN {col} REAL")\n\n    def set_levels(self, leg_id: int, sl, tp) -> None:\n        try:\n            with self._conn() as c:\n                self._ensure_level_cols(c)\n                c.execute("UPDATE vet_trades SET sl=?, tp=? WHERE id=?",\n                          (None if sl is None else float(sl),\n                           None if tp is None else float(tp), int(leg_id)))\n        except Exception as e:\n            write_audit_log(f"[VET][DB] set_levels FAILED id={leg_id}: {e}")\n', 1),
    (VMG, 'R_KILL = "KILL"\n',
     'R_KILL = "KILL"\nR_SL = "SL"   # ── VET_SLTP_LIVE_20261015 ──\nR_TP = "TP"\n\n\ndef _fresh_vet_cfg():\n    """── VET_SLTP_LIVE_20261015 ── Settings as saved NOW (SL/TP % for the next entry)."""\n    from app.config.strategy_loader import load_strategy_config\n    return load_strategy_config("VET_V1")\n', 1),
    (VMG, '                 executor=None, mode: str = "PAPER"):\n',
     '                 executor=None, mode: str = "PAPER",\n                 cfg_reader: Optional[Callable] = None):   # ── VET_SLTP_LIVE_20261015 ──\n', 1),
    (VMG, '        self.day_dte_tag: str = "base"\n',
     '        self.day_dte_tag: str = "base"\n        # ── VET_SLTP_LIVE_20261015 ── Settings reader for SL/TP % at entry\n        # (None → this manager\'s own cfg) + the runner\'s block_target, held as\n        # the blocked option side\n        # Injected ONLY by the live selection loop; a harness / test manager\n        # keeps using its own cfg and never reads the machine\'s Settings.\n        self.cfg_reader: Optional[Callable] = cfg_reader\n        self.sltp_block_side: Optional[str] = None\n', 1),
    (VMG, '        self._day_entries += 1\n        write_audit_log(\n            f"[VET][MGR] OPEN {gid} {side} "\n',
     '        self._stamp_levels(self.pos["main"], "entry")   # ── VET_SLTP_LIVE_20261015 ──\n        self._day_entries += 1\n        write_audit_log(\n            f"[VET][MGR] OPEN {gid} {side} "\n', 1),
    (VMG, '    # ── the per-bar entry point ─────────────────────────────────────────\n',
     '    # ── VET_SLTP_LIVE_20261015 BEGIN ──────────────────────────────────────\n    # The Settings page has always offered "SL % / TP % of entry premium",\n    # and the BACKTEST runner honours them (backtest_vet_runner step 1c), but\n    # live never did — a configured TP 90% silently did nothing. Live now\n    # mirrors the runner exactly:\n    #   * levels from the % of the MAIN leg\'s entry premium (SHORT: stop =\n    #     premium RISING, target = premium FALLING), stamped on the leg at\n    #     entry (sl / tp columns) from the CURRENT Settings — a later edit\n    #     never moves an open position\'s levels;\n    #   * checked at each 5-minute decision close, BEFORE the signal, on the\n    #     same mark the exits use (last 1m close, stale-carried); SL wins\n    #     over TP; exit at that mark, wing closed with it;\n    #   * after an SL/TP exit, no re-entry while the trend target is\n    #     unchanged (runner block_target) unless reenter_after_sltp.\n    # Both % = 0 (every sealed config) → no levels → behaviour unchanged.\n    @property\n    def reenter_after_sltp(self) -> bool:\n        return bool(self.cfg.get("reenter_after_sltp", False))\n\n    def _sltp_pcts(self):\n        src = None\n        try:\n            src = self.cfg_reader() if self.cfg_reader is not None else None\n        except Exception:\n            src = None\n        if not isinstance(src, dict):\n            src = self.cfg\n\n        def _f(k):\n            try:\n                return abs(float(src.get(k) or 0))\n            except (TypeError, ValueError):\n                return 0.0\n        return _f("sl_pct"), _f("tp_pct")\n\n    def sltp_levels(self, entry, sl_pct=None, tp_pct=None):\n        """(sl, tp) price levels for a MAIN-leg entry premium; None = off."""\n        if sl_pct is None or tp_pct is None:\n            sl_pct, tp_pct = self._sltp_pcts()\n        e = float(entry)\n        # UNROUNDED on purpose: the runner compares the mark to exactly this\n        # float (step 1c); rounding would flip ties at the 0.05 tick grid.\n        if self.is_sell:\n            sl = e * (1 + sl_pct / 100.0) if sl_pct > 0 else None\n            tp = e * (1 - tp_pct / 100.0) if tp_pct > 0 else None\n        else:\n            sl = e * (1 - sl_pct / 100.0) if sl_pct > 0 else None\n            tp = e * (1 + tp_pct / 100.0) if tp_pct > 0 else None\n        return sl, tp\n\n    def _stamp_levels(self, main: Dict, why: str) -> None:\n        sl, tp = self.sltp_levels(main["entry_price"])\n        main["sl"], main["tp"] = sl, tp\n        if main.get("db_id"):\n            self.repo.set_levels(main["db_id"], sl, tp)\n        if sl is not None or tp is not None:\n            write_audit_log(f"[VET][SLTP] {main.get(\'tradingsymbol\')} entry "\n                            f"{main[\'entry_price\']} → SL {None if sl is None else round(sl, 2)} · "\n                            f"TP {None if tp is None else round(tp, 2)} ({why})")\n\n    def check_sltp(self, ts: int) -> Optional[str]:\n        """Runner step 1c. Call at a 5m decision close, before decide().\n        Returns R_SL / R_TP when it closed the position, else None."""\n        p = self.pos\n        if p is None:\n            return None\n        main = p["main"]\n        sl, tp = main.get("sl"), main.get("tp")\n        if sl is None and tp is None:\n            return None\n        mark = None\n        if self.quote_fn is not None:\n            try:\n                q = self.quote_fn(main["tradingsymbol"])\n                mark = float(q) if q is not None and float(q) > 0 else None\n            except Exception:\n                mark = None\n        if mark is None:\n            write_audit_log(f"[VET][SLTP] no mark for {main[\'tradingsymbol\']} at "\n                            f"this close — levels not evaluated")\n            return None\n        if self.is_sell:\n            sl_hit = sl is not None and mark >= float(sl)\n            tp_hit = tp is not None and mark <= float(tp)\n        else:\n            sl_hit = sl is not None and mark <= float(sl)\n            tp_hit = tp is not None and mark >= float(tp)\n        hit = R_SL if sl_hit else (R_TP if tp_hit else None)\n        if hit is None:\n            return None\n        write_audit_log(f"[VET][SLTP] {hit} {main[\'tradingsymbol\']} mark {mark} "\n                        f"(SL {sl} · TP {tp}) — closing")\n        self.close_position(hit, ts=ts)\n        return hit\n\n    def sltp_gate(self, decision: Dict, hit: Optional[str]) -> bool:\n        """Runner block_target parity. False = suppress this decision\'s\n        entry. Call after decide(), with check_sltp()\'s result."""\n        want = decision.get("side") if decision.get("action") == ENTER else None\n        if hit and not self.reenter_after_sltp:\n            self.sltp_block_side = want\n        if self.pos is None and self.sltp_block_side:\n            if want == self.sltp_block_side:\n                return False\n            write_audit_log(f"[VET][SLTP] trend changed — re-entry block on "\n                            f"{self.sltp_block_side} cleared")\n            self.sltp_block_side = None\n        return True\n\n    def restore_sltp_block(self, now: Optional[int] = None) -> Optional[str]:\n        """After a restart: today\'s last exit was SL/TP and nothing was\n        entered since → block that side again (the runner\'s block survives\n        the bar loop; a restart must not erase it)."""\n        if self.pos is not None or self.reenter_after_sltp:\n            return None\n        now = int(now or now_ts())\n        d0 = ((now + 19800) // 86400) * 86400 - 19800\n        rows = [r for r in self.repo.trades_on(d0 - 10 * 86400, now + 1, self.mode)\n                if r.get("leg_role") == "MAIN"]\n        if not rows:\n            return None\n        last = max(rows, key=lambda r: max(int(r.get("entry_ts") or 0),\n                                           int(r.get("exit_ts") or 0)))\n        if (str(last.get("status") or "") == "CLOSED"\n                and str(last.get("exit_reason") or "") in (R_SL, R_TP)\n                and int(last.get("exit_ts") or 0) >= d0):\n            self.sltp_block_side = last.get("instrument_type")\n            write_audit_log(f"[VET][SLTP] restart: today\'s last exit was "\n                            f"{last.get(\'exit_reason\')} — no re-entry on "\n                            f"{self.sltp_block_side} until the trend changes")\n        return self.sltp_block_side\n    # ── VET_SLTP_LIVE_20261015 END ────────────────────────────────────────\n\n    # ── the per-bar entry point ─────────────────────────────────────────\n', 1),
    (VMG, '        write_audit_log(f"[VET][MGR] resumed {g[\'group_id\']} "\n                        f"{main.get(\'tradingsymbol\')} from DB")\n        return self.pos\n',
     '        # ── VET_SLTP_LIVE_20261015 ── a position with no levels (opened before\n        # this fix, or with both % at 0) takes them from CURRENT Settings once;\n        # a stamped position keeps its own.\n        if main.get("sl") is None and main.get("tp") is None:\n            self._stamp_levels(main, "adopted — levels from current Settings")\n        write_audit_log(f"[VET][MGR] resumed {g[\'group_id\']} "\n                        f"{main.get(\'tradingsymbol\')} from DB")\n        return self.pos\n', 1),
    (VL, 'from app.engine.vet.vet_manager import VetManager\n',
     'from app.engine.vet.vet_manager import VetManager, _fresh_vet_cfg   # ── VET_SLTP_LIVE_20261015 ──\n', 1),
    (VL, '    manager = VetManager(cfg, repo=repo, chain_fn=chain_list,\n                         quote_fn=quote, executor=executor, mode=mode)\n',
     '    manager = VetManager(cfg, repo=repo, chain_fn=chain_list,\n                         quote_fn=quote, executor=executor, mode=mode,\n                         cfg_reader=_fresh_vet_cfg)   # ── VET_SLTP_LIVE_20261015 ── SL/TP % from Settings at each entry\n', 1),
    (VL, '    state = {"cutoff_blocked": 0, "last_decision": None}\n',
     '    state = {"cutoff_blocked": 0, "last_decision": None,\n             "sltp_blocked": 0}   # ── VET_SLTP_LIVE_20261015 ──\n', 1),
    (VL, '    # ── the per-minute coordinator ──\n',
     '    # ── VET_SLTP_LIVE_20261015 ── a same-day SL/TP exit keeps blocking its\n    # side across a restart (the runner\'s block_target outlives the bar loop)\n    try:\n        manager.restore_sltp_block()\n    except Exception as _e:\n        write_audit_log(f"[VET][SLTP] restore block failed: {_e!r}")\n\n    # ── the per-minute coordinator ──\n', 1),
    (VL, '        sig = engine.on_minute(spot_candle)\n        if sig is None:\n            return\n        decision = engine.decide(manager.pos["side"] if manager.pos else None)\n        state["last_decision"] = decision\n        if decision.get("blocked"):\n            return\n',
     '        sig = engine.on_minute(spot_candle)\n        if sig is None:\n            return\n        # ── VET_SLTP_LIVE_20261015 ── runner step 1c: SL / TP at this 5m\n        # close, BEFORE the signal (a stop that fires keeps its own reason)\n        _sltp_hit = manager.check_sltp(ts)\n        decision = engine.decide(manager.pos["side"] if manager.pos else None)\n        state["last_decision"] = decision\n        if decision.get("blocked"):\n            return\n        # runner block_target: no re-entry while the trend is unchanged\n        if not manager.sltp_gate(decision, _sltp_hit):\n            state["sltp_blocked"] += 1\n            write_audit_log(f"[VET][SLTP] re-entry on {decision.get(\'side\')} "\n                            f"blocked — trend unchanged since the SL/TP exit")\n            return\n', 1),
    (VR, '        "mark": mark, "mark_src": src,\n',
     '        "mark": mark, "mark_src": src,\n        "sl": leg.get("sl"), "tp": leg.get("tp"),   # ── VET_SLTP_LIVE_20261015 ──\n', 1),
    (VR, '        "hedge_max_premium": cfg.get("hedge_max_premium"),\n    }\n',
     '        "hedge_max_premium": cfg.get("hedge_max_premium"),\n    }\n    # ── VET_SLTP_LIVE_20261015 ── what the NEXT entry will stamp (Settings now)\n    try:\n        from app.config.strategy_loader import load_strategy_config as _lsc\n        _fr = _lsc("VET_V1") or cfg\n    except Exception:\n        _fr = cfg\n    for _k in ("sl_pct", "tp_pct"):\n        try:\n            out["cfg"][_k] = abs(float(_fr.get(_k) or 0))\n        except Exception:\n            out["cfg"][_k] = 0.0\n', 1),
    (VP, '  const cfg = st?.cfg || {};\n',
     '  const cfg = st?.cfg || {};\n  // ── VET_SLTP_LIVE_20261015 ── SL/TP % from Settings, applied live at 5m closes\n  const sltpHeader = (Number(cfg.tp_pct) > 0 || Number(cfg.sl_pct) > 0)\n    ? ` · ${[Number(cfg.tp_pct) > 0 ? `TP ${cfg.tp_pct}%` : null, Number(cfg.sl_pct) > 0 ? `SL ${cfg.sl_pct}%` : null].filter(Boolean).join(" · ")} of premium at 5m closes · else exits on signal`\n    : " · no SL/TP — exits on signal";\n', 1),
    (VP, '${st.hedged ? " · wing-hedged" : ""} · no SL/TP by design — exits on signal`',
     '${st.hedged ? " · wing-hedged" : ""}${sltpHeader}`', 1),
    (VP, '<span>{isShort ? "premium → 0 (max profit)" : "+100% of premium"}</span>',
     '<span>{pos.tp != null ? `TP ${fmt2(pos.tp)}` : (isShort ? "premium → 0 (max profit)" : "+100% of premium")}</span>', 1),
    (VP, 'AGAINST the position — no stop by design; the exit is the signal`',
     'AGAINST the position — ${pos.sl != null ? `SL ${fmt2(pos.sl)}` : "no stop set; the exit is the signal"}`', 1),
    (VP, 'exits: {showParams ? "FLIP · SIGNAL EXIT" : "signal"} ·',
     'exits: {pos.tp != null ? `TP ${fmt2(pos.tp)} · ` : ""}{pos.sl != null ? `SL ${fmt2(pos.sl)} · ` : ""}{showParams ? "FLIP · SIGNAL EXIT" : "signal"} ·', 1),
    (ST, '                <Field label="SL % of entry premium (0 = off)" helper="⚠ Every sealed config runs 0 — the exit IS the signal. SL was falsified under BUY, and the SL-under-SELL sweep has NOT been run. Non-zero here is an unstudied configuration.">\n',
     '                {/* ── VET_SLTP_LIVE_20261015 ── SL/TP now applied live (backtest parity) */}\n                <Field label="SL % of entry premium (0 = off)" helper="⚠ Every sealed config runs 0 — the exit IS the signal. SL was falsified under BUY, and the SL-under-SELL sweep has NOT been run. Non-zero here is an unstudied configuration. Live: checked at each 5-minute close on the option\'s last 1-minute close (as the backtest), stamped on the position at entry; after an SL/TP exit, no re-entry until the trend changes.">\n', 1),
]

NEW_FILES = {T: '# backend/app/engine/vet/test_vet_sltp_live.py — fence VET_SLTP_LIVE_20261015\n#\n#   A. levels: SHORT / LONG math, 0 = off, read FRESH from Settings at entry\n#   B. entry stamps the levels on the leg (memory + vet_trades) → Trades page\n#   C. check_sltp: TP / SL exits at the mark, wing closed too, no-mark and\n#      no-level cases never exit\n#   D. restart: a legacy row gets levels from current Settings; a stamped row\n#      keeps its own levels after Settings change\n#   E. restart re-derives a same-day SL/TP re-entry block\n#   F. PARITY: 400 randomized sessions — the live manager (check_sltp →\n#      plan → sltp_gate → on_decision) produces exactly the runner\'s\n#      (backtest_vet_runner 1c + block_target) exits and entries\n#   G. wiring (loop order, route, panel, Settings text)\n#\n# Run: cd backend && python3 app/engine/vet/test_vet_sltp_live.py\n\nfrom __future__ import annotations\n\nimport os\nimport random\nimport sqlite3\nimport sys\nimport tempfile\nfrom pathlib import Path\n\nBACKEND = Path(__file__).resolve().parents[3]\nif str(BACKEND) not in sys.path:\n    sys.path.insert(0, str(BACKEND))\nos.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())\n\nfrom app.engine.vet.vet_common import VetRepo  # noqa: E402\nfrom app.engine.vet.vet_live_core import ENTER, plan, want_side_for  # noqa: E402\nfrom app.engine.vet import vet_manager as VM  # noqa: E402\n\nFENCE = "VET_SLTP_LIVE_20261015"\nFAILS: list = []\nOK = [0]\nTMP = tempfile.mkdtemp(prefix="vet_sltp_")\nSPOT = 24000.0\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nclass Px:\n    v = 100.0\n\n\ndef chain(side, ts):\n    out = []\n    for k in range(23000, 25050, 50):\n        far = abs(k - SPOT) >= 900\n        out.append({"tradingsymbol": f"NIFTY26O19{k}{side}", "token": 1000 + k, "strike": float(k),\n                    "expiry": "2026-10-19", "instrument_type": side,\n                    "ltp": (2.0 if far else Px.v)})\n    return out\n\n\ndef quote(sym):\n    k = int(sym[10:15])\n    return 2.0 if abs(k - SPOT) >= 900 else Px.v\n\n\ndef mk(db, sl=0.0, tp=0.0, wing=False, reenter=False, reader=None):\n    cfg = {"leg_action": "SELL", "atm_offset": 0, "eod_square": False,\n           "hedge_enabled": wing, "hedge_max_premium": 3.0 if wing else 0,\n           "quantity": {"lots": 10, "lot_size": 65}, "_spot": SPOT,\n           "sl_pct": sl, "tp_pct": tp, "reenter_after_sltp": reenter}\n    repo = VetRepo(db)\n    repo.ensure_schema()\n    return VM.VetManager(cfg, repo=repo, chain_fn=chain, quote_fn=quote, mode="PAPER",\n                         cfg_reader=(reader if reader is not None else (lambda: None)))\n\n\ndef db(name):\n    return os.path.join(TMP, name + ".db")\n\n\ndef t_levels():\n    print("── A. levels ──")\n    m = mk(db("a"), sl=20, tp=90)\n    sl, tp = m.sltp_levels(182.40)\n    check("SHORT: SL 20% → 218.88, TP 90% → 18.24", (round(sl, 2), round(tp, 2)) == (218.88, 18.24), (sl, tp))\n    m.cfg["leg_action"] = "BUY"\n    sl, tp = m.sltp_levels(100.0)\n    check("LONG: SL below, TP above entry", (round(sl, 2), round(tp, 2)) == (80.0, 190.0), (sl, tp))\n    m = mk(db("a2"))\n    check("0 / 0 (every sealed config) → no levels", m.sltp_levels(182.4) == (None, None))\n    m = mk(db("a3"), tp=0, reader=lambda: {"sl_pct": 0, "tp_pct": 90})\n    check("read FRESH from Settings: TP 90 saved after boot is used for the next entry",\n          round(m.sltp_levels(182.4)[1], 2) == 18.24)\n    m = mk(db("a4"), tp=50, reader=lambda: 1 / 0)\n    check("Settings read failing → boot config (never crashes)", round(m.sltp_levels(100.0)[1], 2) == 50.0)\n    real = VM._fresh_vet_cfg\n    try:\n        VM._fresh_vet_cfg = lambda: {"sl_pct": 0, "tp_pct": 90}     # the machine\'s Settings say TP 90\n        cfg = {"leg_action": "SELL", "quantity": {"lots": 10, "lot_size": 65}}\n        h = VM.VetManager(cfg, repo=VetRepo(db("a5")), chain_fn=chain, quote_fn=quote)\n        check("a manager built WITHOUT a reader never reads the machine\'s Settings (hermetic harnesses)",\n              h.sltp_levels(182.4) == (None, None))\n    finally:\n        VM._fresh_vet_cfg = real\n\n\ndef t_entry_stamp():\n    print("── B. entry stamps the levels ──")\n    Px.v = 182.40\n    path = db("b")\n    m = mk(path, tp=90, wing=True)\n    p = m.open_position("PE", ts=1000, bar_ts=1000, condition=1)\n    check("position opened with a wing", p is not None and p["wing"] is not None)\n    check("main leg carries TP 18.24, no SL", round(p["main"]["tp"], 2) == 18.24 and p["main"]["sl"] is None, p["main"])\n    r = sqlite3.connect(path).execute("SELECT sl, tp FROM vet_trades WHERE leg_role=\'MAIN\'").fetchone()\n    check("vet_trades MAIN row: sl NULL, tp 18.24", r is not None and r[0] is None and round(r[1], 2) == 18.24, r)\n    try:\n        from app.api.paper_trades_routes import _load_tma_paper\n        c = sqlite3.connect(path)\n        c.row_factory = sqlite3.Row\n        opn, _ = _load_tma_paper(c, table="vet_trades", strategy_name="VET_V1")\n        mains = [t for t in opn if t["trade_direction"] == "SHORT"]\n        check("Trades page row now shows TP 18.24", mains and round(mains[0]["tp_price"], 2) == 18.24,\n              [t.get("tp_price") for t in opn])\n    except ImportError as e:\n        print(f"  ! paper_trades_routes not importable here ({e}) — skipped")\n    return m, path\n\n\ndef t_exits():\n    print("── C. check_sltp ──")\n    Px.v = 182.40\n    m = mk(db("c1"), tp=90, wing=True)\n    m.open_position("PE", ts=1000, bar_ts=1000, condition=1)\n    Px.v = 30.0\n    check("premium above TP → no exit", m.check_sltp(1300) is None and m.pos is not None)\n    Px.v = 18.20\n    r = m.check_sltp(1600)\n    check("premium ≤ 18.24 → TP exit, flat", r == "TP" and m.pos is None, r)\n    rows = list(sqlite3.connect(db("c1")).execute(\n        "SELECT leg_role, exit_reason, exit_price FROM vet_trades ORDER BY id"))\n    check("both legs closed with reason TP; main at the mark", sorted(x[1] for x in rows) == ["TP", "TP"]\n          and [x for x in rows if x[0] == "MAIN"][0][2] == 18.2, rows)\n    Px.v = 100.0\n    m = mk(db("c2"), sl=25, tp=90)\n    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)\n    Px.v = 125.0\n    check("SHORT premium +25% → SL", m.check_sltp(1300) == "SL" and m.pos is None)\n    Px.v = 100.0\n    m = mk(db("c3"), tp=90)\n    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)\n    m.quote_fn = lambda s: None\n    check("no mark → levels not evaluated, position kept", m.check_sltp(1300) is None and m.pos is not None)\n    Px.v = 100.0\n    m = mk(db("c5"), sl=20, tp=30)\n    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)\n    Px.v = m.pos["main"]["tp"]\n    check("tie: mark exactly AT the TP level → TP (runner: mark <= level)", m.check_sltp(1300) == "TP")\n    Px.v = 100.0\n    m = mk(db("c6"), sl=20, tp=30)\n    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)\n    Px.v = m.pos["main"]["sl"]\n    check("tie: mark exactly AT the SL level → SL (runner: mark >= level)", m.check_sltp(1300) == "SL")\n    Px.v = 100.0\n    m = mk(db("c4"))\n    m.open_position("CE", ts=1000, bar_ts=1000, condition=-1)\n    Px.v = 0.05\n    check("0/0 config → never exits on price (sealed behaviour unchanged)",\n          m.check_sltp(1300) is None and m.pos is not None)\n\n\ndef t_restart():\n    print("── D/E. restart ──")\n    Px.v = 182.40\n    path = db("d")\n    m = mk(path)                                      # entered with TP 0 (pre-fix)\n    m.open_position("PE", ts=1000, bar_ts=1000, condition=1)\n    sqlite3.connect(path).execute("UPDATE vet_trades SET sl=NULL, tp=NULL").connection.commit()\n    m2 = mk(path, reader=lambda: {"tp_pct": 90, "sl_pct": 0})\n    m2.resume_from_db()\n    check("legacy open position (no levels) gets TP from current Settings on adopt",\n          m2.pos and round(m2.pos["main"]["tp"], 2) == 18.24, m2.pos and m2.pos["main"].get("tp"))\n    m3 = mk(path, reader=lambda: {"tp_pct": 50, "sl_pct": 0})\n    m3.resume_from_db()\n    check("a stamped position KEEPS its levels after a Settings change",\n          round(m3.pos["main"]["tp"], 2) == 18.24, m3.pos["main"].get("tp"))\n    import time as _t\n    now = int(_t.time())\n    m3.pos["main"]["entry_ts"] = now - 600\n    sqlite3.connect(path).execute("UPDATE vet_trades SET entry_ts=?", (now - 600,)).connection.commit()\n    Px.v = 10.0\n    m3.check_sltp(now - 60)\n    m4 = mk(path)\n    check("restart after today\'s TP exit → same side blocked", m4.restore_sltp_block(now) == "PE")\n    m5 = mk(path, reenter=True)\n    check("…unless reenter_after_sltp", m5.restore_sltp_block(now) is None)\n    check("…and a TP exit on a previous day blocks nothing",\n          mk(path).restore_sltp_block(now + 2 * 86400) is None)\n\n\ndef ref_run(targets, marks, sl_pct, tp_pct, reenter):\n    """backtest_vet_runner semantics (step 1c SL/TP + block_target + step 2)."""\n    ev, pos, block = [], None, None\n    for i, (t, mk_) in enumerate(zip(targets, marks)):\n        if block is not None and t != block:\n            block = None\n        if pos is not None and (sl_pct > 0 or tp_pct > 0):\n            sl_lvl = pos[1] * (1 + sl_pct / 100.0)\n            tp_lvl = pos[1] * (1 - tp_pct / 100.0)\n            hit = "SL" if (sl_pct > 0 and mk_ >= sl_lvl) else ("TP" if (tp_pct > 0 and mk_ <= tp_lvl) else None)\n            if hit:\n                ev.append((i, "X", hit, mk_))\n                if not reenter:\n                    block = t\n                pos = None\n        want = want_side_for(t, "SELL")\n        if pos is not None and pos[0] != want:\n            ev.append((i, "X", "FLIP" if want else "SIGNAL_EXIT", mk_))\n            pos = None\n            if want:\n                block = None\n        if pos is None and want is not None and block != t:\n            ev.append((i, "E", want, mk_))\n            pos = (want, mk_)\n    return ev\n\n\ndef live_run(targets, marks, sl_pct, tp_pct, reenter, path):\n    m = mk(path, sl=sl_pct, tp=tp_pct, reenter=reenter)\n    ev = []\n    oc, oo = m.close_position, m.open_position\n\n    def cp(reason, *, ts):\n        mark = Px.v\n        ev.append((ts, "X", reason, mark))\n        return oc(reason, ts=ts)\n\n    def op(side, *, ts, bar_ts, condition):\n        r = oo(side, ts=ts, bar_ts=bar_ts, condition=condition)\n        if r is not None:\n            ev.append((ts, "E", side, Px.v))\n        return r\n    m.close_position, m.open_position = cp, op\n    for i, (t, mk_) in enumerate(zip(targets, marks)):\n        Px.v = mk_\n        hit = m.check_sltp(i)\n        a, side, reason = plan(m.pos["side"] if m.pos else None, t, "SELL")\n        dec = {"action": a, "side": side, "reason": reason, "condition": t, "bar_ts": i}\n        if not m.sltp_gate(dec, hit):\n            continue\n        m.on_decision(dec, ts=i)\n    return ev\n\n\ndef t_parity():\n    print("── F. parity vs the backtest runner ──")\n    rnd = random.Random(20261015)\n    bad, n_sltp, n_block = 0, 0, 0\n    for trial in range(400):\n        n = rnd.randint(20, 70)\n        t, ts_, marks, px = rnd.choice((-1, 0, 1)), [], [], rnd.uniform(60, 220)\n        for _ in range(n):\n            if rnd.random() < 0.15:\n                t = rnd.choice((-1, 0, 1))\n            px = max(0.05, px * (1 + rnd.gauss(0, 0.08)))\n            ts_.append(t)\n            marks.append(round(round(px / 0.05) * 0.05, 2))\n        sl = rnd.choice((0, 0, 10, 25, 40))\n        tp = rnd.choice((0, 30, 60, 90))\n        re_ = rnd.random() < 0.25\n        a = ref_run(ts_, marks, sl, tp, re_)\n        b = live_run(ts_, marks, sl, tp, re_, db(f"p{trial}"))\n        n_sltp += sum(1 for e in a if e[2] in ("SL", "TP"))\n        if a != b:\n            bad += 1\n            if bad <= 2:\n                print("   ref ", a[:12])\n                print("   live", b[:12])\n    check(f"400 randomized sessions: live events == runner events ({n_sltp} SL/TP exits exercised)",\n          bad == 0 and n_sltp > 200, f"{bad} mismatching sessions")\n\n\ndef t_wiring():\n    print("── G. wiring ──")\n    s = (BACKEND / "app/engine/vet/vet_selection_loop.py").read_text(encoding="utf-8")\n    i_c, i_d = s.find("manager.check_sltp(ts)"), s.find("decision = engine.decide(")\n    i_b, i_g = s.find(\'if decision.get("blocked"):\'), s.find("manager.sltp_gate(decision, _sltp_hit)")\n    check("loop: SL/TP checked at the 5m close BEFORE the signal decision", 0 < i_c < i_d, (i_c, i_d))\n    check("loop: re-entry gate applied after the decision, before any action", 0 < i_b < i_g, (i_b, i_g))\n    check("loop: restart re-derives the block", "manager.restore_sltp_block()" in s)\n    check("loop: the LIVE manager (only) reads SL/TP % from Settings", "cfg_reader=_fresh_vet_cfg" in s)\n    r = (BACKEND / "app/api/vet_state_routes.py").read_text(encoding="utf-8")\n    check("state route: legs carry sl / tp; cfg carries sl_pct / tp_pct",\n          \'"sl": leg.get("sl")\' in r and \'"tp_pct"\' in r)\n    fe = BACKEND.parent / "frontend/src/strategies/vet/VETPanel.jsx"\n    if fe.exists():\n        p = fe.read_text(encoding="utf-8")\n        check("panel: \'no SL/TP by design\' only when both are 0", "sltpHeader" in p and FENCE in p)\n        check("panel: TP / SL levels on the track + exits line", "pos.tp != null" in p and "pos.sl != null" in p)\n\n\nif __name__ == "__main__":\n    t_levels()\n    t_entry_stamp()\n    t_exits()\n    t_restart()\n    t_parity()\n    t_wiring()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} VET SL/TP LIVE CHECKS PASSED")\n'}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-esbuild", action="store_true")
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
        for rel, txt in [(r, staged[r]) for r in targets if r.endswith(".py")] + list(NEW_FILES.items()):
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print(f"  ✓ py_compile (edited + {len(NEW_FILES)} new)")
        jsx = [r for r in targets if r.endswith((".js", ".jsx"))]
        if jsx and not a.skip_esbuild:
            esb = None
            for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild"):
                if c.exists():
                    esb = str(c); break
            esb = esb or shutil.which("esbuild")
            if not esb:
                die("esbuild not found (frontend/node_modules/.bin) — run npm install, or --skip-esbuild")
            for rel in jsx:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.js=jsx", "--loader:.jsx=jsx", "--log-level=error"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print(f"  ✓ esbuild parsed {len(jsx)} staged JSX file(s)")
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
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", SCALP_TEST_MODE="1",
                   SCALP_APP_HOME=tempfile.mkdtemp(prefix="scalp_test_home_"),
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c",
                            "import app.engine.vet.vet_common, app.engine.vet.vet_manager, app.engine.vet.vet_selection_loop, app.api.vet_state_routes; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("a touched module no longer imports")
        print("  ✓ VET common / manager / loop / state route import")
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
    print(f"\n✓ {FENCE} applied. Rebuild (deploy-scalp.command). On restart the log shows\n"
          f"  '[VET][SLTP] … → SL None · TP 18.24 (adopted — levels from current Settings)' for today's\n"
          f"  open position; the Trades page shows TP, the Velvet panel 'TP 90% of premium at 5m closes'.")

if __name__ == "__main__":
    main()
