#!/usr/bin/env python3
# apply_orb_live1.py — ORB_V1 live phase, step 1 of the wiring order:
# the PURE DECISION CORE + tests + build gates (same-session rule).
#
# Fence: ORB_LIVE_20260903
#
# PREREQUISITE: the ORB backtest chain through apply_orb_pct.py (verified).
#
# WHAT THIS DOES
#   NEW  backend/app/engine/orb/ (zero-byte __init__ + orb_live_core.py +
#        test_orb_live_core.py, 19 checks) — BOTH trees.
#        The core RE-RUNS the backtest's own primitives over the growing
#        1m prefix (VET parity-by-construction doctrine) and carries the
#        LD-sheet in its header: decisions at 1m closes only; entry at
#        next 1m open; close-trigger stop via spot_breached(trigger=
#        "close"); TP as a resting limit; EOD 13:00; budgets in-core;
#        PrefixGuard freeze on restated bars or a mutated signal stream.
#   EDIT desktop/build-scalp.sh  (Gate-2 + Gate-3 REQUIRED lists)
#   EDIT .github/workflows/build-release.yml (all three CI REQUIRED lists)
#
# NEXT STEPS (checklist apply order): wiring1 (registry/loader) ->
# manager/engine/runtime + routes + EOD job -> wiring2 (api_server/kill/
# telegram/license) -> settings/dashboard -> parity harness.
#
# USAGE (repo root):
#   python3 apply_orb_live1.py --check
#   python3 apply_orb_live1.py
#   cd backend && PYTHONPATH=$PWD python3 app/engine/orb/test_orb_live_core.py

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile

FENCE = 'ORB_LIVE_20260903'
ROOT = os.path.dirname(os.path.abspath(__file__))
DESKTOP_BACKEND = os.path.join(ROOT, "desktop", "src-tauri", "backend")

PAYLOADS = {'backend/app/engine/orb/__init__.py': '', 'backend/app/engine/orb/orb_live_core.py': '# backend/app/engine/orb/orb_live_core.py\n#\n# ── ORB_V1 LIVE CORE ── pure decision core for paper/live "Outrider".\n#\n# Fence: ORB_LIVE_20260903\n#\n# DOCTRINE (docs/strategy_checklist.md, VET_V1 donor notes): parity by\n# construction — the live core does not re-implement the strategy, it\n# RE-RUNS the backtest\'s own primitives (resample_1m, compute_orb,\n# orb_signals, spot_breached, prem_levels) over the growing 1-minute day\n# prefix, and a PrefixGuard freezes the day on any restated bar or any\n# mutation of the already-emitted signal stream. No app imports beyond the\n# backtest engine module (single source of truth); no DB, no clock, no\n# config singletons. The manager owns fills, orders and persistence.\n#\n# ── LIVE PARITY CONTRACT (LD-sheet, 2026-09-03) ──────────────────────────\n#   LD1  Decisions ONLY at completed 1m bars. process() accepts a bar whose\n#        ts is the MINUTE-START of the just-completed minute and must be\n#        60s-aligned — unaligned ts is a caller bug and raises (the VET\n#        "gross 0" scar: ChainStore probes step in exact 60s increments).\n#   LD2  Entry: a signal emitted at bar ts fills at the NEXT 1m open. The\n#        manager samples the option chain at the signal bar\'s close (the\n#        bar ENDING at the fill minute is the selection instant) and buys\n#        at the next open — byte-matching backtest R1/R2.\n#   LD3  Stop: spot_sl_trigger=close — evaluated ONCE per completed spot\n#        bar via the backtest\'s own spot_breached(trigger="close"); a\n#        closing breach means market-sell at the next 1m open. No tick\n#        monitor, no GTT: a spot-close-conditional stop cannot be a broker\n#        order, and the GTT-race scar (duplicate exits) forbids dual\n#        executors anyway.\n#   LD4  Target: resting LIMIT SELL at entry_premium x (1 + target/100),\n#        placed at entry, cancelled (abort-before-flatten) before any\n#        engine exit. Backtest books AT the level on a touch; a resting\n#        limit is its live twin. Divergence ledger: a gap THROUGH the\n#        level fills the limit at-or-better vs backtest\'s at-the-open —\n#        live can only be >= backtest here.\n#   LD5  EOD 13:00 engine square-off (own job as backstop). 13:00 < the\n#        generic 15:25 sweep, so NO squareoff exemption — the generic\n#        sweep remains a harmless catastrophe backstop (checklist 2.10).\n#   LD6  Budgets in-core: max 2/day, 1/side, one position at a time;\n#        signals while a position is open are dropped and counted\n#        (runner\'s sig_dropped_open).\n#   LD7  Day gates: expected weekly expiry only (uncovered day skipped by\n#        the manager); ORB window with ANY missing bucket refuses the day\n#        (fail-closed, backtest-identical); NSE calendar gating at cron\n#        AND engine (exits fail open, entries fail closed).\n#   LD8  Trade storage: generic paper_trades (TSG-style) — checklist 2.9,\n#        2.12 become no-ops by construction.\n\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass, field\nfrom typing import Dict, List, Optional, Tuple\n\ntry:\n    from app.backtest.orb.orb_v1_engine import (\n        OrbBar, resample_1m, compute_orb, orb_signals, spot_breached,\n        prem_levels, spot_sl_level, SESSION_OPEN_MIN)\nexcept ImportError:                                        # standalone tests\n    import os, sys\n    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),\n                                    "..", "..", "backtest", "orb"))\n    from orb_v1_engine import (  # type: ignore\n        OrbBar, resample_1m, compute_orb, orb_signals, spot_breached,\n        prem_levels, spot_sl_level, SESSION_OPEN_MIN)\n\n\nclass PrefixGuard:\n    """Fail-closed stability guard (VET doctrine, adapted to ORB).\n\n    Two invariants, both prefix-stable by construction and GUARDED anyway:\n      1. A completed 1m spot bar, once seen, must never be restated with\n         different OHLC (a re-delivered identical bar is idempotent).\n      2. The signal stream produced by re-running orb_signals over the\n         prefix must only ever APPEND — an earlier signal changing ts,\n         side or flags means the inputs are unreliable.\n    On violation the guard FREEZES: the caller must stop trading the day.\n    """\n\n    def __init__(self) -> None:\n        self.bars: Dict[int, Tuple[float, float, float, float]] = {}\n        self.sig_seen: List[Tuple[int, str, bool, bool]] = []\n        self.frozen: bool = False\n        self.reason: Optional[str] = None\n\n    def _freeze(self, why: str) -> bool:\n        self.frozen, self.reason = True, why\n        return False\n\n    def check_bar(self, b: OrbBar) -> bool:\n        if self.frozen:\n            return False\n        key = (b.open, b.high, b.low, b.close)\n        prev = self.bars.get(b.ts)\n        if prev is not None and prev != key:\n            return self._freeze(f"bar {b.ts} restated {prev} -> {key}")\n        self.bars[b.ts] = key\n        return True\n\n    def check_signals(self, sigs) -> bool:\n        if self.frozen:\n            return False\n        now = [(s.ts, s.side, s.ambiguous, s.rearm_entry) for s in sigs]\n        if now[:len(self.sig_seen)] != self.sig_seen:\n            return self._freeze("signal stream mutated (not append-only)")\n        self.sig_seen = now\n        return True\n\n\n@dataclass\nclass OrbPosition:\n    side: str\n    symbol: str\n    entry_px: float\n    entry_spot: float\n    entry_ts: int\n    sl_spot: float\n    tp_prem: float\n\n\n@dataclass\nclass OrbLiveDay:\n    """One trading day of ORB_V1 decisions. The manager feeds completed 1m\n    SPOT bars in order; the core answers with a list of action tuples:\n\n      ("DAY_REFUSED", reason)        fail-closed; nothing will trade today\n      ("LEVELS", high, low)          ORB window complete, levels locked\n      ("SIGNAL", side, sig_ts)       buy `side` at the NEXT 1m open (LD2)\n      ("STOP_CLOSE_BREACH", ts)      cancel TP limit, market-sell (LD3)\n      ("EOD_SQUARE_OFF", ts)         cancel TP limit, market-sell (LD5)\n      ("FROZEN", reason)             PrefixGuard tripped — flatten & stop\n\n    Fills flow back via on_entry_fill / on_position_closed. The core never\n    talks to brokers, DBs or clocks."""\n    day_start_epoch: int\n    cfg: dict\n    prefix: List[OrbBar] = field(default_factory=list)\n    guard: PrefixGuard = field(default_factory=PrefixGuard)\n    orb_high: Optional[float] = None\n    orb_low: Optional[float] = None\n    refused: Optional[str] = None\n    consumed_sigs: int = 0\n    day_trades: int = 0\n    side_trades: Dict[str, int] = field(default_factory=lambda: {"CE": 0, "PE": 0})\n    dropped_open: int = 0\n    dropped_budget: int = 0\n    dropped_block: int = 0\n    pending_side: Optional[str] = None      # SIGNAL emitted, fill not confirmed\n    position: Optional[OrbPosition] = None\n    eod_emitted: bool = False\n    frozen_reported: bool = False\n\n    # ── derived once ──\n    def _m(self, ts: int) -> int:\n        return (ts - self.day_start_epoch) // 60\n\n    @property\n    def _orb_end_min(self) -> int:\n        return SESSION_OPEN_MIN + int(self.cfg["orb_minutes"])\n\n    @property\n    def _block_min(self) -> int:\n        h, m = str(self.cfg["entry_block_time"]).split(":")\n        return int(h) * 60 + int(m)\n\n    @property\n    def _eod_min(self) -> int:\n        h, m = str(self.cfg["eod_square_off"]).split(":")\n        return int(h) * 60 + int(m)\n\n    def process(self, bar: OrbBar) -> List[tuple]:\n        """Feed ONE completed 1m spot bar (ts = minute START, aligned)."""\n        if bar.ts % 60 != 0:\n            raise ValueError(f"unaligned bar ts {bar.ts} — completed-minute "\n                             "START epochs only (LD1)")\n        out: List[tuple] = []\n        if self.refused:\n            return out\n        if not self.guard.check_bar(bar):\n            if not self.frozen_reported:\n                self.frozen_reported = True\n                out.append(("FROZEN", self.guard.reason))\n            return out\n        if self.prefix and bar.ts == self.prefix[-1].ts:\n            return out                                    # idempotent redeliver\n        if self.prefix and bar.ts < self.prefix[-1].ts:\n            self.guard._freeze(f"bar ts regression {bar.ts}")\n            if not self.frozen_reported:\n                self.frozen_reported = True\n                out.append(("FROZEN", self.guard.reason))\n            return out\n        self.prefix.append(bar)\n        mod = self._m(bar.ts)\n\n        # ── ORB window: lock levels at the first bar AT/after orb end ──\n        if self.orb_high is None and mod >= self._orb_end_min:\n            tf = int(self.cfg["timeframe_minutes"])\n            bars_tf = resample_1m(self.prefix, day_start_epoch=self.day_start_epoch,\n                                  tf_minutes=tf)\n            orb = compute_orb(bars_tf, day_start_epoch=self.day_start_epoch,\n                              orb_minutes=int(self.cfg["orb_minutes"]),\n                              tf_minutes=tf)\n            if orb is None:\n                self.refused = "ORB window incomplete — day refused (fail-closed)"\n                out.append(("DAY_REFUSED", self.refused))\n                return out\n            self.orb_high, self.orb_low = orb\n            out.append(("LEVELS", self.orb_high, self.orb_low))\n\n        # ── position exits BEFORE new signals (runner ladder order) ──\n        if self.position is not None and not self.eod_emitted:\n            if mod >= self._eod_min:\n                self.eod_emitted = True\n                out.append(("EOD_SQUARE_OFF", bar.ts))\n            elif spot_breached(side=self.position.side,\n                               sl_level=self.position.sl_spot, spot_bar=bar,\n                               trigger=str(self.cfg.get("spot_sl_trigger",\n                                                        "close"))):\n                out.append(("STOP_CLOSE_BREACH", bar.ts))\n\n        # ── signal stream: re-run the backtest\'s own detector on the prefix ──\n        if self.orb_high is not None:\n            sigs = orb_signals(\n                self.prefix, day_start_epoch=self.day_start_epoch,\n                orb_high=self.orb_high, orb_low=self.orb_low,\n                orb_minutes=int(self.cfg["orb_minutes"]),\n                tf_minutes=int(self.cfg["timeframe_minutes"]),\n                trigger_source=str(self.cfg.get("trigger_source", "high")),\n                breakout_buffer_pts=float(self.cfg.get("breakout_buffer_pts", 0)),\n                direction=str(self.cfg.get("direction", "BOTH")),\n                both_side_policy=str(self.cfg.get("both_side_policy",\n                                                  "pessimistic")))\n            if not self.guard.check_signals(sigs):\n                if not self.frozen_reported:\n                    self.frozen_reported = True\n                    out.append(("FROZEN", self.guard.reason))\n                return out\n            for s in sigs[self.consumed_sigs:]:\n                self.consumed_sigs += 1\n                entry_min = self._m(s.ts) + 1                       # LD2\n                if self.position is not None or self.pending_side is not None:\n                    self.dropped_open += 1\n                    continue\n                if self.day_trades >= int(self.cfg["max_trades_per_day"]):\n                    self.dropped_budget += 1\n                    continue\n                if self.side_trades[s.side] >= int(self.cfg["max_trades_per_side"]):\n                    self.dropped_budget += 1\n                    continue\n                if entry_min >= self._block_min or entry_min >= self._eod_min:\n                    self.dropped_block += 1\n                    continue\n                self.pending_side = s.side\n                out.append(("SIGNAL", s.side, s.ts))\n        return out\n\n    # ── manager callbacks ──\n    def on_entry_fill(self, *, side: str, symbol: str, entry_px: float,\n                      entry_spot: float, entry_ts: int) -> OrbPosition:\n        """Called after the option buy fills at the next 1m open (LD2).\n        Computes the sealed stop/target levels with the backtest\'s own\n        arithmetic and arms the position."""\n        assert self.pending_side == side, "fill without a pending signal"\n        v = float(self.cfg["sl_points"])\n        eff = (entry_spot * v / 100.0\n               if str(self.cfg.get("sl_dist_mode", "pts")) == "pct" else v)\n        sl_spot = spot_sl_level(side=side,\n                                mode=str(self.cfg.get("spot_sl_mode", "points")),\n                                orb_high=self.orb_high, orb_low=self.orb_low,\n                                entry_spot=entry_spot, sl_points=eff)\n        tp, _ = prem_levels(entry_px=entry_px,\n                            target_mode=str(self.cfg.get("target_mode", "pct")),\n                            target_value=float(self.cfg["target_value"]),\n                            sl_prem_mode="off", sl_prem_value=0.0)\n        self.position = OrbPosition(side=side, symbol=symbol, entry_px=entry_px,\n                                    entry_spot=entry_spot, entry_ts=entry_ts,\n                                    sl_spot=sl_spot, tp_prem=tp)\n        self.pending_side = None\n        self.day_trades += 1\n        self.side_trades[side] += 1\n        return self.position\n\n    def on_entry_abandoned(self) -> None:\n        """No candidate in band / no fill — release the pending slot\n        WITHOUT consuming budget (runner\'s sig_no_candidate path)."""\n        self.pending_side = None\n\n    def on_position_closed(self) -> None:\n        self.position = None\n', 'backend/app/engine/orb/test_orb_live_core.py': '# backend/app/engine/orb/test_orb_live_core.py\n#\n# ── ORB_V1 LIVE CORE TESTS ── Fence: ORB_LIVE_20260903\n# VET donor pattern: section 2 drives 1m candles ONE AT A TIME and asserts\n# the incrementally-observed stream equals the whole-day backtest\n# computation. Run standalone: python3 test_orb_live_core.py\n\nfrom __future__ import annotations\nimport os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n\nfrom orb_live_core import OrbLiveDay, PrefixGuard          # noqa: E402\ntry:\n    from app.backtest.orb.orb_v1_engine import OrbBar, orb_signals, SESSION_OPEN_MIN\nexcept ImportError:\n    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),\n                                    "..", "..", "backtest", "orb"))\n    from orb_v1_engine import OrbBar, orb_signals, SESSION_OPEN_MIN  # type: ignore\n\nFAILS = []\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + note) if (note and not ok) else \'\'}")\n    if not ok:\n        FAILS.append(name)\n\nDS = 1_768_000_000 - (1_768_000_000 % 86400)   # any midnight-aligned epoch\ndef m1(minute, o, h, l, c):\n    return OrbBar(DS + (SESSION_OPEN_MIN + minute) * 60, o, h, l, c)\n\nSEALED = {"orb_minutes": 15, "timeframe_minutes": 5, "trigger_source": "high",\n          "breakout_buffer_pts": 0, "direction": "BOTH",\n          "both_side_policy": "pessimistic", "spot_sl_mode": "points",\n          "sl_dist_mode": "pct", "sl_points": 9.174311926605505,  # 10 pts @109\n          "spot_sl_trigger": "close", "target_mode": "pct", "target_value": 50,\n          "entry_block_time": "12:00", "eod_square_off": "13:00",\n          "max_trades_per_day": 2, "max_trades_per_side": 1}\n\ndef scripted_day():\n    bars = [m1(k, 105, 110, 100, 105) for k in range(15)]          # ORB 100..110\n    bars += [m1(k, 104, 106, 103, 105) for k in range(15, 20)]\n    bars.append(m1(20, 106, 110.5, 105, 109))                      # touch -> UP\n    bars += [m1(k, 109, 111, 108, 110) for k in range(21, 40)]\n    bars.append(m1(40, 108, 109, 97.0, 98.0))                      # CLOSES thru 99\n    bars += [m1(k, 109, 110, 108, 109) for k in range(41, 230)]    # to 13:05\n    return bars\n\nprint("── section 1: day lifecycle on the scripted day (interleaved fills) ──")\nday = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\nstream = []\npos = None\nfor b in scripted_day():\n    for a in day.process(b):\n        stream.append((day._m(b.ts) if a[0] != "LEVELS" else -1, a))\n        if a[0] == "SIGNAL":                     # manager fills at next 1m open\n            pos = day.on_entry_fill(side=a[1], symbol="TESTCE", entry_px=172.0,\n                                    entry_spot=109.0,\n                                    entry_ts=a[2] + 60)\n        elif a[0] in ("STOP_CLOSE_BREACH", "EOD_SQUARE_OFF"):\n            day.on_position_closed()\nkinds = [a[0] for _, a in stream]\ncheck("levels lock at the first post-window bar",\n      kinds[0] == "LEVELS" and stream[0][1][1:] == (110.0, 100.0))\nsig = [a for _, a in stream if a[0] == "SIGNAL"]\ncheck("exactly one SIGNAL, UP side, at the m20 touch bar",\n      len(sig) == 1 and sig[0][1] == "CE"\n      and (sig[0][2] - DS) // 60 == SESSION_OPEN_MIN + 20)\ncheck("stop level = entry_spot − 10.0 (pct arithmetic to the paisa)",\n      pos is not None and abs(pos.sl_spot - 99.0) < 1e-9, str(pos and pos.sl_spot))\ncheck("TP limit level = entry × 1.5", pos is not None and abs(pos.tp_prem - 258.0) < 1e-9)\nbreach = [(m, a) for m, a in stream if a[0] == "STOP_CLOSE_BREACH"]\ncheck("wick minutes ignored; breach fires ONLY on the m40 closing bar",\n      len(breach) == 1 and breach[0][0] == SESSION_OPEN_MIN + 40, str(breach))\ncheck("the simultaneous PE break at m40 is dropped while the position is open",\n      day.dropped_open >= 1)\ncheck("no spurious EOD after the stop closed the position",\n      "EOD_SQUARE_OFF" not in kinds)\n\nprint("── section 2: incremental == whole-day (parity by construction) ──")\nfull = orb_signals(scripted_day(), day_start_epoch=DS, orb_high=110, orb_low=100,\n                   orb_minutes=15, tf_minutes=5)\ncheck("guard-observed stream equals the whole-day engine run",\n      day.guard.sig_seen == [(s.ts, s.side, s.ambiguous, s.rearm_entry)\n                             for s in full])\nday2 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\nhalf = scripted_day()[:60]\nfor b in half:\n    day2.process(b)\ncheck("prefix stream is a prefix of the whole-day stream (append-only)",\n      day2.guard.sig_seen == [(s.ts, s.side, s.ambiguous, s.rearm_entry)\n                              for s in orb_signals(half, day_start_epoch=DS,\n                                                   orb_high=110, orb_low=100,\n                                                   orb_minutes=15, tf_minutes=5)])\n\nprint("── section 3: guards fail closed ──")\nd3 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\ntry:\n    d3.process(OrbBar(DS + (SESSION_OPEN_MIN * 60) + 1, 1, 1, 1, 1))\n    check("unaligned ts raises (LD1 / VET gross-0 scar)", False)\nexcept ValueError:\n    check("unaligned ts raises (LD1 / VET gross-0 scar)", True)\nd3 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\nd3.process(m1(0, 105, 110, 100, 105))\nacts = d3.process(m1(0, 105, 110, 100, 106))               # restated OHLC\ncheck("restated bar freezes the day",\n      acts and acts[0][0] == "FROZEN" and d3.guard.frozen)\ncheck("frozen day emits nothing further",\n      d3.process(m1(1, 105, 110, 100, 105)) == [])\nd3 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\nd3.process(m1(0, 105, 110, 100, 105))\ncheck("identical redelivery is idempotent, not a freeze",\n      d3.process(m1(0, 105, 110, 100, 105)) == [] and not d3.guard.frozen)\nd4 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\n# whole 5m bucket (m5..m9) missing — per-BUCKET fail-closed, exactly the\n# backtest\'s rule (a 4/5-minute bucket still counts; a missing bucket kills)\ngap = [m1(k, 105, 110, 100, 105) for k in range(5)] + \\\n      [m1(k, 105, 110, 100, 105) for k in range(10, 15)] + \\\n      [m1(16, 104, 106, 103, 105), m1(17, 104, 106, 103, 105)]\nref = []\nfor b in gap:\n    ref += d4.process(b)\ncheck("missing window bucket refuses the day (fail-closed)",\n      any(a[0] == "DAY_REFUSED" for a in ref) and d4.refused)\n\nprint("── section 4: budgets and pending slots (LD6) ──")\nd5 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED, max_trades_per_side=2))\nseq = [m1(k, 105, 110, 100, 105) for k in range(15)]\nseq += [m1(k, 104, 106, 103, 105) for k in range(15, 20)]\nseq.append(m1(20, 106, 110.5, 105, 108))                   # touch 1, close inside\nseq += [m1(k, 107, 109, 106, 108) for k in range(21, 25)]  # 5m closes back in -> re-arm\nseq.append(m1(26, 108, 110.6, 107, 110))                   # touch 2 after re-arm\nouts = []\nfor b in seq:\n    outs += d5.process(b)\nsigs5 = [a for a in outs if a[0] == "SIGNAL"]\ncheck("second signal while the first is PENDING is dropped (one at a time)",\n      len(sigs5) == 1 and d5.dropped_open == 1, str((len(sigs5), d5.dropped_open)))\nd5.on_entry_abandoned()\ncheck("abandoned entry releases the slot without consuming budget",\n      d5.pending_side is None and d5.day_trades == 0)\nd6 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED))\nfor b in seq:\n    d6.process(b)\nd6.on_entry_fill(side="CE", symbol="X", entry_px=172.0, entry_spot=109.0,\n                 entry_ts=DS + (SESSION_OPEN_MIN + 21) * 60)\nd6.on_position_closed()\nouts6 = []\nfor b in [m1(27, 107, 108, 106, 107.5)] + [m1(k, 107, 109, 106, 108) for k in range(28, 32)] \\\n         + [m1(33, 108, 110.7, 107, 110)]:\n    outs6 += d6.process(b)\ncheck("per-side budget 1: a fresh CE signal after the CE trade is dropped",\n      not any(a[0] == "SIGNAL" for a in outs6) and d6.dropped_budget >= 1)\n\nprint("── section 5: block time (LD2 entry minute rule) ──")\nd7 = OrbLiveDay(day_start_epoch=DS, cfg=dict(SEALED, entry_block_time="09:36"))\nouts7 = []\nfor b in scripted_day()[:30]:\n    outs7 += d7.process(b)\ncheck("signal whose NEXT-minute entry lands at/after the block is dropped",\n      not any(a[0] == "SIGNAL" for a in outs7) and d7.dropped_block == 1)\n\nprint()\nif FAILS:\n    print(f"{len(FAILS)} FAILED: {FAILS}"); sys.exit(1)\nprint("ALL CHECKS PASSED")\n'}

EDITS = [('desktop/build-scalp.sh', 'before', '    app.engine.vet.vet_selection_loop\n', '    app.engine.orb.orb_live_core\n', 1), ('desktop/build-scalp.sh', 'before', '    "app.engine.vet.vet_selection_loop",\n', '    "app.engine.orb.orb_live_core",\n', 1), ('.github/workflows/build-release.yml', 'before', '            app.engine.vet.vet_selection_loop\n', '            app.engine.orb.orb_live_core\n', 3)]

VERIFY = [('backend/app/engine/orb/orb_live_core.py', 'ORB_LIVE_20260903', 1), ('backend/app/engine/orb/test_orb_live_core.py', 'ALL CHECKS', 1), ('desktop/build-scalp.sh', 'app.engine.orb.orb_live_core', 2), ('.github/workflows/build-release.yml', 'app.engine.orb.orb_live_core', 3)]



def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def both_trees(rel, single):
    """A backend-relative path lands in both trees; frontend in one."""
    out = [os.path.join(ROOT, rel)]
    if rel.startswith("backend/") and not single:
        out.append(os.path.join(DESKTOP_BACKEND, rel[len("backend/"):]))
    return out


def stage_edit(text, kind, anchor, payload, count, path):
    n = text.count(anchor)
    if kind == "replaceall":
        if n != count:
            fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
        return text.replace(anchor, payload)
    if n != count:
        fail(f"{path}: anchor x{n}, expected x{count}: {anchor[:60]!r}")
    if kind == "replace":
        return text.replace(anchor, payload)
    if kind == "before":
        return text.replace(anchor, payload + anchor)
    if kind == "after":
        return text.replace(anchor, anchor + payload)
    fail(f"unknown edit kind {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--single-tree", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(ROOT, "backend", "app")):
        fail("run this from the scalp-app repo root")
    if not a.single_tree and not os.path.isdir(DESKTOP_BACKEND):
        fail("desktop/src-tauri/backend missing — dual-tree is a hard "
             "requirement locally; pass --single-tree only on a CI checkout")

    # ── prerequisite ──
    probe = os.path.join(ROOT, "backend", "app", "backtest", "orb",
                         "backtest_orb_runner.py")
    if not (os.path.exists(probe)
            and "ORB_PCT_20260903" in open(probe, encoding="utf-8").read()):
        fail("the ORB backtest chain (through apply_orb_pct.py) must be "
             "applied first — the live core imports its engine module")
    probe2 = os.path.join(ROOT, "backend", "app", "engine", "orb",
                          "orb_live_core.py")
    if os.path.exists(probe2):
        print(f"  SKIP   engine/orb already present — "
              f"nothing to do")
        return

    # ── stage every write in memory first ──
    staged = {}   # abs path -> new text
    for rel, body in PAYLOADS.items():
        for p in both_trees(rel, a.single_tree):
            if os.path.exists(p):
                fail(f"{p} already exists (half-applied tree?)")
            staged[p] = body
    per_file = {}
    for rel, kind, anchor, payload, count in EDITS:
        per_file.setdefault(rel, []).append((kind, anchor, payload, count))
    for rel, ops in per_file.items():
        src_path = os.path.join(ROOT, rel)
        if not os.path.exists(src_path):
            fail(f"{src_path} not found")
        text = open(src_path, encoding="utf-8").read()
        if FENCE in text:
            fail(f"{rel} already carries the fence — mixed state, resolve by hand")
        for kind, anchor, payload, count in ops:
            text = stage_edit(text, kind, anchor, payload, count, rel)
        for p in both_trees(rel, a.single_tree):
            if p != src_path and not os.path.exists(p):
                fail(f"dual-tree copy missing: {p}")
            staged[p] = text

    print(f"  OK     all anchors verified ({len(staged)} file writes staged)")

    # ── staged compile gates ──
    tmp = tempfile.mkdtemp(prefix="orv_gate_")
    jsx_targets = []
    for p, body in staged.items():
        t = os.path.join(tmp, os.path.basename(p))
        with open(t, "w", encoding="utf-8") as f:
            f.write(body)
        if p.endswith(".py"):
            try:
                py_compile.compile(t, doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"py_compile gate: {p}: {e}")
        elif p.endswith((".jsx", ".js")):
            jsx_targets.append((p, t))
    print(f"  OK     py_compile gate passed")
    esb = shutil.which("esbuild")
    npx = shutil.which("npx")
    for p, t in jsx_targets:
        cmd = None
        if esb:
            cmd = [esb, "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        elif npx:
            cmd = [npx, "--yes", "esbuild", "--loader:.jsx=jsx", "--loader:.js=jsx", t, "--outfile=/dev/null"]
        if cmd is None:
            print(f"  WARN   esbuild unavailable — JSX gate skipped for {p}")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "frontend"))
        if r.returncode != 0:
            fail(f"esbuild gate: {p}:\n{r.stderr[-2000:]}")
    if jsx_targets and (esb or npx):
        print(f"  OK     esbuild JSX gate passed ({len(jsx_targets)} files)")

    if a.check:
        for p in sorted(staged):
            print(f"  WOULD  write {p}")
        print("  CHECK  dry run complete — no files written")
        return

    # ── write, with backups for edited files ──
    for p, body in sorted(staged.items()):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, p + f".bak-{FENCE}")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"  WROTE  {p}")

    # ── grep-count verification ──
    bad = 0
    for rel, needle, mn in VERIFY:
        got = open(os.path.join(ROOT, rel), encoding="utf-8").read().count(needle)
        ok = got >= mn
        print(f"  {'OK ' if ok else 'BAD'}    {rel}: {needle!r} x{got} (need >= {mn})")
        bad += 0 if ok else 1
    if bad:
        fail(f"{bad} verification(s) failed — restore from .bak-{FENCE}")

    print()
    print(f"  DONE   ORB live core + gates applied. Next:")
    print(f"         cd backend && PYTHONPATH=$PWD python3 app/engine/orb/test_orb_live_core.py")
    print(f"         (expect ALL CHECKS PASSED incl. the integration block)")


if __name__ == "__main__":
    main()
