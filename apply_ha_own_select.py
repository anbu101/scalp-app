#!/usr/bin/env python3
# apply_ha_own_select.py — HA_V1 gets its own selection; stops depending on SCALP_V1.
#
# Fence: HA_OWN_SELECT_20260923
# PREREQUISITE (asserted): revert_ha_cond1_flip.py applied (HA_COND1_FLIP absent
#   from ha_tick_engine.py). Independent of the two Backtest-page patches.
#
# WHAT CHANGES (live path — apply outside trading hours, restart the backend):
#   NEW  backend/app/engine/ha_options/ha_selection_loop.py
#        HA_V1's own selection loop: OptionSelector (the rule every other loop
#        uses and the one backtest_selector.py replays — premium band → nearest
#        weekly expiry → median-ATM → ATM±800 → nearest 2 per side) on HA_V1's
#        OWN option_premium band, every 120s on the shared grid, saved to
#        HA_V1_selected_{ce,pe}.json. Lock carve-out: a contract with an open HA
#        trade stays selected until it closes. Opens NO WebSocket.
#   backend/app/engine/ha_options/ha_tick_engine.py
#        - selection read from HA_V1's own files: up to 2 CE + 2 PE (nearest-ATM
#          first), band re-checked, NO rows[0] fallback
#        - the evaluator now runs for every contract watched today (day union)
#          BEFORE any gate, and an ENTRY may fire only for a contract in the
#          CURRENT selection — exactly backtest_ha_runner (per-day states +
#          snapshot-membership gate). Ends the rotation resets the diag showed
#          (22 gap resets / 30 warm-ups on 2026-09-22) and the 09:30 session-
#          open reset.
#        - TP tick check + arbitration-cancel monitor cover all selected
#          contracts; _selected_ce/_selected_pe kept as nearest-ATM for logs/panel
#   backend/app/engine/ha_options/ha_runtime.py   daily reset also trims the day union
#   backend/app/strategy/strategy_runtime.py       launches ha_selection_loop next
#                                                  to start_ha_runtime (registry-gated)
#   frontend/src/strategies/ha_v1/HAPanel.jsx     panel reads strategy_id=HA_V1
#
# WHAT DOES NOT CHANGE: market data. HA still receives ticks and its universe
# from the shared weekly-universe ZerodhaTickEngine in ws_registry. Kite allows
# 3 sockets per api_key (soft limit) and the app already runs five on one key,
# and that engine also carries SCALP_V1's trading logic keyed on strategy_id,
# so HA never constructs one. Retiring SCALP_V1 later = keep that engine as the
# data spine, remove its signal path (separate task). Backtest runner untouched:
# it already models this selection and this continuity.
#
# FIRST SESSION AFTER DEPLOY: HA has no selection until ha_selection_loop's
# first save (≤ 2 min after the trade session is ready) — log tag [HA_SELECT].
# Min SL is still whatever Settings says; this patch does not change it.
#
# GATES before any write: prerequisite; 15 anchors x1; new file must not exist;
# py_compile + pyflakes (undefined names) on 4 .py results; esbuild on the JSX;
# 11-case behavioural suite in a subprocess against the staged tree (real HA /
# EMA / evaluator / OptionSelector code, stubbed I/O). All-or-nothing writes
# with .bak-FENCE backups; failed verification restores everything; mirror
# backend patched best-effort.
#
# USAGE (repo root, non-trading hours):
#   python3 apply_ha_own_select.py --check
#   python3 apply_ha_own_select.py [--allow-dirty]
# Then: ./desktop/build-scalp.sh both   and restart the backend.

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tempfile, py_compile

FENCE = "HA_OWN_SELECT_20260923"
ROOT = os.path.dirname(os.path.abspath(__file__))
NEW_FILE = "backend/app/engine/ha_options/ha_selection_loop.py"
MIRROR_BACKEND = "desktop/src-tauri/backend"

EDITS = [
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'hdr: selected line',
     '  - At candle close, signal evaluation only fires for the symbol that is\n    currently selected (read live from SCALP_V1 selection files).\n',
     "  - At candle close the evaluator is fed for every contract HA has watched\n    today (2 CE + 2 PE per selection, union over the day) and an ENTRY may\n    only fire for a contract in the CURRENT selection — HA's own\n    HA_V1_selected_*.json written by ha_selection_loop. ── HA_OWN_SELECT_20260923 ──\n"),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'hdr: flow line',
     '  4. _reload_selection()       — updates _selected_ce / _selected_pe from files\n',
     "  4. _reload_selection()       — updates _watched_ce / _watched_pe from HA_V1's own files\n"),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'typing: List',
     'from typing import Dict, Optional, Set\n',
     'from typing import Dict, List, Optional, Set   # ── HA_OWN_SELECT_20260923 ── List\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'init: slots',
     '        # Currently selected symbols for signal evaluation (1 CE + 1 PE max)\n        self._selected_ce: Optional[str] = None\n        self._selected_pe: Optional[str] = None\n        self._selection_lock = threading.Lock()\n',
     "        # ── HA_OWN_SELECT_20260923 ── current selection = up to 2 CE + 2 PE, nearest-ATM first\n        # (HA's own files). _selected_ce/_selected_pe are the nearest-ATM of each\n        # side, kept for logs and the panel. _watched_today is the day's union:\n        # the evaluator keeps running on those so a contract that rotates out and\n        # back in never resets — exactly backtest_ha_runner's per-day states.\n        self._watched_ce: List[str] = []\n        self._watched_pe: List[str] = []\n        self._watched_today: set = set()\n        self._selected_ce: Optional[str] = None\n        self._selected_pe: Optional[str] = None\n        self._selection_lock = threading.Lock()\n"),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'tick: is_selected',
     '        with self._selection_lock:\n            is_selected = (\n                symbol == self._selected_ce or symbol == self._selected_pe\n            )\n',
     '        with self._selection_lock:   # ── HA_OWN_SELECT_20260923 ── any currently selected contract\n            is_selected = symbol in self._watched_ce or symbol in self._watched_pe\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'close: step2',
     '        # ── Step 2: Is this symbol currently selected? ─────────────\n        with self._selection_lock:\n            is_selected_ce = symbol == self._selected_ce\n            is_selected_pe = symbol == self._selected_pe\n\n        if not is_selected_ce and not is_selected_pe:\n            # Not selected — stored to DB, nothing more to do\n            return\n\n        side = "CE" if is_selected_ce else "PE"\n\n        # Throttled per-candle log (only for selected symbols)\n',
     '        # ── Step 2: watched today? ── ── HA_OWN_SELECT_20260923 ──\n        # Evaluator continuity for the whole day\'s selected union (backtest\n        # parity: on_bar runs for every watched symbol; gates apply to the\n        # resulting signal, never to whether the evaluator sees the candle).\n        with self._selection_lock:\n            in_ce = symbol in self._watched_ce\n            in_pe = symbol in self._watched_pe\n            watched_today = symbol in self._watched_today\n        if not watched_today:\n            return\n\n        signal: HAEntrySignal = state.evaluator.push(ha, ema_val)\n\n        if not in_ce and not in_pe:\n            # Watched earlier today, not in the CURRENT selection: state kept\n            # warm, but no entry (snapshot-membership gate, as in the backtest).\n            return\n\n        side = "CE" if in_ce else "PE"\n\n        # Throttled per-candle log (only for selected symbols)\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'close: push',
     '        # ── Evaluate entry conditions ─────────────────────────────\n        signal: HAEntrySignal = state.evaluator.push(ha, ema_val)\n\n        if not signal.should_enter:\n',
     '        # ── Evaluate entry conditions ── ── HA_OWN_SELECT_20260923 ── evaluator already ran above\n        if not signal.should_enter:\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'monitor: selected',
     '                with self._selection_lock:\n                    selected = set(filter(None, [\n                        self._selected_ce,\n                        self._selected_pe,\n                    ]))\n',
     '                with self._selection_lock:   # ── HA_OWN_SELECT_20260923 ──\n                    selected = set(self._watched_ce) | set(self._watched_pe)\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'reload_selection',
     '    def _reload_selection(self):\n        """\n        Read SCALP_V1 selection files and update _selected_ce / _selected_pe.\n        """\n        from pathlib import Path\n        import json\n\n        state_dir = Path.home() / ".scalp-app" / "state"\n        cfg       = load_strategy_config(self.STRATEGY_ID)\n        prem_min  = cfg.get("option_premium", {}).get("min", 0)\n        prem_max  = cfg.get("option_premium", {}).get("max", 9999)\n\n        new_ce: Optional[str] = None\n        new_pe: Optional[str] = None\n\n        for suffix, label in [("ce", "CE"), ("pe", "PE")]:\n            fpath = state_dir / f"SCALP_V1_selected_{suffix}.json"\n            if not fpath.exists():\n                continue\n\n            try:\n                rows = json.loads(fpath.read_text())\n                if not rows:\n                    continue\n\n                chosen = next(\n                    (r for r in rows if prem_min <= (r.get("ltp") or 0) <= prem_max),\n                    rows[0],\n                )\n\n                symbol = (\n                    chosen.get("tradingsymbol")\n                    or chosen.get("symbol")\n                )\n                if not symbol:\n                    continue\n\n                if symbol not in self._states:\n                    token = (\n                        chosen.get("instrument_token")\n                        or chosen.get("token")\n                    )\n                    if not token:\n                        df = _load_instruments_df()\n                        if df is not None and not df.empty:\n                            rows_df = df[df["tradingsymbol"] == symbol]\n                            if not rows_df.empty:\n                                token = int(rows_df.iloc[0]["instrument_token"])\n\n                    if token:\n                        token = int(token)\n                        self._token_map[token] = symbol\n                        self._states[symbol] = SymbolState(\n                            symbol=symbol, token=token\n                        )\n                        if symbol not in self._warmed_up:\n                            self._warmed_up.add(symbol)\n                            self._states[symbol].warmup_from_db()\n\n                        write_audit_log(\n                            f"[HA][SELECTION] {label} {symbol} added to universe "\n                            f"(was not in ZerodhaTickEngine universe)"\n                        )\n                    else:\n                        write_audit_log(\n                            f"[HA][SELECTION] {label} {symbol} — "\n                            f"token not resolvable, skipping"\n                        )\n                        continue\n\n                if label == "CE":\n                    new_ce = symbol\n                else:\n                    new_pe = symbol\n\n            except Exception as e:\n                write_audit_log(f"[HA][SELECTION_ERROR] {label}: {e}")\n\n        with self._selection_lock:\n            changed = (new_ce != self._selected_ce or new_pe != self._selected_pe)\n            self._selected_ce = new_ce\n            self._selected_pe = new_pe\n\n        if changed:\n            write_audit_log(\n                f"[HA][SELECTION] Updated → CE={new_ce} PE={new_pe}"\n            )\n\n',
     '    def _reload_selection(self):\n        """\n        ── HA_OWN_SELECT_20260923 ── Read HA_V1\'s OWN selection files (written by\n        ha_selection_loop: up to 2 CE + 2 PE, nearest-ATM first, already\n        filtered to HA\'s premium band) and update the watched lists.\n        Rows are re-checked against the band (a stale file after a band change\n        must not select out-of-band contracts) but there is NO rows[0]\n        fallback — nothing in band means nothing selected.\n        """\n        from pathlib import Path\n        import json\n\n        state_dir = Path.home() / ".scalp-app" / "state"\n        cfg       = load_strategy_config(self.STRATEGY_ID)\n        prem_min  = cfg.get("option_premium", {}).get("min", 0)\n        prem_max  = cfg.get("option_premium", {}).get("max", 9999)\n\n        new_ce: List[str] = []\n        new_pe: List[str] = []\n\n        for suffix, label, out in [("ce", "CE", new_ce), ("pe", "PE", new_pe)]:\n            fpath = state_dir / f"{self.STRATEGY_ID}_selected_{suffix}.json"\n            if not fpath.exists():\n                continue\n            try:\n                rows = json.loads(fpath.read_text())\n                if not rows:\n                    continue\n                for row in rows:\n                    symbol = row.get("tradingsymbol") or row.get("symbol")\n                    if not symbol:\n                        continue\n                    ltp = row.get("ltp")\n                    # locked rows may carry ltp=None (open trade, built from\n                    # the instrument master) — keep them regardless of band.\n                    if ltp is not None and not (prem_min <= (ltp or 0) <= prem_max):\n                        continue\n                    if symbol not in self._states:\n                        token = row.get("instrument_token") or row.get("token")\n                        if not token:\n                            df = _load_instruments_df()\n                            if df is not None and not df.empty:\n                                rows_df = df[df["tradingsymbol"] == symbol]\n                                if not rows_df.empty:\n                                    token = int(rows_df.iloc[0]["instrument_token"])\n                        if not token:\n                            write_audit_log(\n                                f"[HA][SELECTION] {label} {symbol} — token not resolvable, skipping"\n                            )\n                            continue\n                        token = int(token)\n                        self._token_map[token] = symbol\n                        self._states[symbol] = SymbolState(symbol=symbol, token=token)\n                        if symbol not in self._warmed_up:\n                            self._warmed_up.add(symbol)\n                            self._states[symbol].warmup_from_db()\n                        write_audit_log(\n                            f"[HA][SELECTION] {label} {symbol} added to universe "\n                            f"(was not in the shared WS universe)"\n                        )\n                    if symbol not in out:\n                        out.append(symbol)\n            except Exception as e:\n                write_audit_log(f"[HA][SELECTION_ERROR] {label}: {e}")\n\n        with self._selection_lock:\n            changed = (new_ce != self._watched_ce or new_pe != self._watched_pe)\n            self._watched_ce = new_ce\n            self._watched_pe = new_pe\n            self._watched_today |= set(new_ce) | set(new_pe)\n            self._selected_ce = new_ce[0] if new_ce else None\n            self._selected_pe = new_pe[0] if new_pe else None\n\n        if changed:\n            write_audit_log(\n                f"[HA][SELECTION] Updated → CE={new_ce} PE={new_pe} "\n                f"(watched today: {len(self._watched_today)})"\n            )\n\n    def reset_watched_today(self):\n        """── HA_OWN_SELECT_20260923 ── new trading day: drop yesterday\'s union (called from the\n        runtime\'s daily reset next to the signal engine\'s reset)."""\n        with self._selection_lock:\n            self._watched_today = set(self._watched_ce) | set(self._watched_pe)\n\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'log: ready',
     '            f"[HA][ENGINE_READY] mode={trade_mode} "\n            f"universe_size={len(self._token_map)} "\n            f"selected_ce={self._selected_ce} "\n            f"selected_pe={self._selected_pe} "\n',
     '            f"[HA][ENGINE_READY] mode={trade_mode} "\n            f"universe_size={len(self._token_map)} "\n            f"selected_ce={self._watched_ce} "\n            f"selected_pe={self._watched_pe} "\n'),
    ('backend/app/engine/ha_options/ha_tick_engine.py', 'log: retry',
     '                    f"[HA][SUB_RETRY] universe={after} "\n                    f"selected_ce={self._selected_ce} "\n                    f"selected_pe={self._selected_pe} "\n',
     '                    f"[HA][SUB_RETRY] universe={after} "\n                    f"selected_ce={self._watched_ce} "\n                    f"selected_pe={self._watched_pe} "\n'),
    ('backend/app/engine/ha_options/ha_runtime.py', 'runtime: daily reset',
     '                try:\n                    engine._signal_engine.reset_daily()\n                except Exception as e:\n                    write_audit_log(f"[HA-RUNTIME][RESET_ERROR] {repr(e)}")\n',
     '                try:\n                    engine._signal_engine.reset_daily()\n                    engine.reset_watched_today()   # ── HA_OWN_SELECT_20260923 ──\n                except Exception as e:\n                    write_audit_log(f"[HA-RUNTIME][RESET_ERROR] {repr(e)}")\n'),
    ('backend/app/strategy/strategy_runtime.py', 'runtime: import',
     'from app.engine.ha_options.ha_runtime import start_ha_runtime\n',
     'from app.engine.ha_options.ha_runtime import start_ha_runtime\nfrom app.engine.ha_options.ha_selection_loop import ha_selection_loop   # ── HA_OWN_SELECT_20260923 ──\n'),
    ('backend/app/strategy/strategy_runtime.py', 'runtime: launch',
     '        # -------------------------------------------------\n        # HA STRATEGY\n        # HA_V1 piggybacks on the SCALP_V1 WS tick engine\n        # (which is already started by selection_loop).\n        # It only needs its own runtime loop for HA candle\n        # processing, signal evaluation, and trade management.\n        # -------------------------------------------------\n        elif strategy_id == "HA_V1":\n\n            ha_task = asyncio.create_task(\n                start_ha_runtime(broker_manager)\n            )\n\n            cls._RUNNING[strategy_id] = {\n                "ha_task": ha_task,\n                "status": "RUNNING",\n            }\n',
     '        # -------------------------------------------------\n        # HA STRATEGY\n        # ── HA_OWN_SELECT_20260923 ── HA_V1 runs its OWN selection loop (own\n        # premium band, own HA_V1_selected_*.json). Ticks and the\n        # universe still come from the shared weekly-universe WS\n        # engine in ws_registry (no extra Kite socket).\n        # -------------------------------------------------\n        elif strategy_id == "HA_V1":\n\n            ha_task = asyncio.create_task(\n                start_ha_runtime(broker_manager)\n            )\n            ha_sel_task = asyncio.create_task(\n                ha_selection_loop(broker_manager)\n            )\n\n            cls._RUNNING[strategy_id] = {\n                "ha_task": ha_task,\n                "ha_sel_task": ha_sel_task,\n                "status": "RUNNING",\n            }\n'),
    ('frontend/src/strategies/ha_v1/HAPanel.jsx', 'panel: fetch',
     '  /* ── Selection: read SCALP_V1 selection (HA uses it) ── */\n  const fetchSelection = useCallback(async () => {\n    try {\n      const res = await fetch(`${getApiBase()}/api/selection/current?strategy_id=SCALP_V1`);\n',
     "  /* ── Selection: HA_V1's OWN selection (── HA_OWN_SELECT_20260923 ──); slot shows nearest-ATM of each side */\n  const fetchSelection = useCallback(async () => {\n    try {\n      const res = await fetch(`${getApiBase()}/api/selection/current?strategy_id=HA_V1`);\n"),
]

PY_FILES = sorted({e[0] for e in EDITS if e[0].endswith(".py")}) + [NEW_FILE]
JSX_FILES = sorted({e[0] for e in EDITS if e[0].endswith(".jsx")})

NEW_FILE_SRC = r"""# backend/app/engine/ha_options/ha_selection_loop.py
#
# ── HA_OWN_SELECT_20260923 ── HA_V1's OWN selection loop.
# ============================================================================
# WHY: until 2026-09-23 HA_V1 read SCALP_V1's selection files and took the
# first in-band row per side. That coupled HA to SCALP_V1's premium band and
# loop, and — because HA fed its evaluator for ONE contract per side and reset
# it on every rotation — diverged from backtest_ha_runner, which keeps HA state
# on the whole selected union and gates only the ENTRY on snapshot membership.
#
# WHAT: the same OptionSelector rule every other loop uses and the one
# backtest_selector.py replays (premium band → nearest weekly expiry →
# median-ATM → ATM±800 → nearest 2 per side), run on HA_V1's OWN
# option_premium band every RECHECK_INTERVAL on the shared wall-clock grid,
# saved to HA_V1_selected_{ce,pe}.json. Lock carve-out: a contract with an open
# HA trade stays in the selection until it closes (matches the backtest's
# LOCK CARVE-OUT and live SCALP_V1's locked slots).
#
# MARKET DATA: this loop opens NO WebSocket. Ticks and the universe come from
# the shared weekly-universe ZerodhaTickEngine (ws_registry). Kite allows 3
# sockets per api_key as a soft limit and the app already runs five on one
# key; HA must not add a sixth. ZerodhaTickEngine also carries SCALP_V1's
# trading logic keyed on its strategy_id, so HA must NEVER construct one under
# its own name. When SCALP_V1 is retired, keep that engine as the data spine
# (remove its signal path) — separate task.
#
# Isolated: nothing here touches SCALP_V1 / V3 / V5 / TMA / VET code paths.
# Launched from StrategyRuntimeManager.start("HA_V1") next to start_ha_runtime.
# ============================================================================
import asyncio
import time
from typing import Dict, List, Optional

from app.selector.option_selector import OptionSelector
from app.fetcher.zerodha_instruments import load_nifty_weekly_options
from app.config.strategy_loader import load_strategy_config
from app.utils.selection_persistence import save_selection, load_selection
from app.event_bus.audit_logger import write_audit_log
from app.core.ha_engine_registry import HA_ENGINE_REGISTRY
from app.marketdata.ws_registry import get_ws_engines

# 🔒 LICENSE
from app.license import license_state

STRATEGY_ID      = "HA_V1"
INDEX_SYMBOL     = "NIFTY"
TRADE_MODE       = "BOTH"      # always select BOTH sides; trade_side_mode gates in the engine
ATM_RANGE        = 800
STRIKE_STEP      = 50
RECHECK_INTERVAL = 120         # seconds — same grid as SCALP_V1 / V3 / V5
PHASE_OFFSET     = 30          # :30 past every even minute, same as the other loops
PER_SIDE         = 2           # nearest-2-per-side: what backtest_selector replays

_LAST_SAVED: Optional[List[str]] = None


def _seconds_to_next_boundary(interval: int, phase_offset: int = 0) -> float:
    now = time.time()
    base = (int(now) // interval + 1) * interval + phase_offset
    if base - now < 1.0:
        base += interval
    return base - now


def _ha_locked_symbols() -> set:
    '''Contracts with an open HA trade (paper or live), from every running HA
    engine. Fail-open to an empty set: a read error must never drop the
    selection, the engine's own _active_trade_symbols keeps monitoring.'''
    locked: set = set()
    for eng in list(HA_ENGINE_REGISTRY):
        try:
            with eng._active_trade_lock:
                locked |= set(eng._active_trade_symbols)
        except Exception:
            continue
    return locked


def _locked_rows(locked: set, prev: Dict[str, List[Dict]], instruments: List[Dict]) -> List[Dict]:
    '''Rows for locked contracts: prefer the previously saved row (has ltp /
    selected_at), else build one from the instrument master.'''
    rows: List[Dict] = []
    prev_by_sym = {}
    for side in ("CE", "PE"):
        for r in prev.get(side, []) or []:
            sym = r.get("tradingsymbol") or r.get("symbol")
            if sym:
                prev_by_sym[sym] = r
    inst_by_sym = {i.get("tradingsymbol"): i for i in instruments}
    for sym in sorted(locked):
        r = prev_by_sym.get(sym)
        if r is None:
            i = inst_by_sym.get(sym)
            if i is None:
                continue
            exp = i.get("expiry")
            r = {
                "symbol": sym, "tradingsymbol": sym,
                "strike": float(i.get("strike") or 0),
                "type": i.get("instrument_type"),
                "expiry": exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                "ltp": None,
                "instrument_token": i.get("instrument_token"),
            }
        rows.append(dict(r))
    return rows


def _attach_tokens(rows: List[Dict], instruments: List[Dict]) -> None:
    '''OptionSelector rows carry no instrument_token; the engine resolves a
    missing token from the instrument master anyway, but attaching it here
    saves that lookup and lets the shared WS engine subscribe if needed.'''
    by_sym = {i.get("tradingsymbol"): i.get("instrument_token") for i in instruments}
    for r in rows:
        if not r.get("instrument_token"):
            tok = by_sym.get(r.get("tradingsymbol") or r.get("symbol"))
            if tok:
                r["instrument_token"] = int(tok)


def _ensure_subscribed(rows: List[Dict]) -> None:
    '''Best-effort: ask the shared weekly-universe engine to subscribe any
    selected token it does not already stream (a band edge just outside
    ATM±800). Never raises; the engine's universe rarely misses these.'''
    engines = get_ws_engines()
    if not engines:
        return
    eng = engines[0]
    try:
        have = set(getattr(eng, "builders", {}).keys())
        missing = [int(r["instrument_token"]) for r in rows
                   if r.get("instrument_token") and int(r["instrument_token"]) not in have]
        if missing and hasattr(eng, "subscribe_additional_tokens"):
            eng.subscribe_additional_tokens(missing)
            write_audit_log(f"[HA_SELECT] asked shared WS engine ({getattr(eng, 'strategy_id', '?')}) "
                            f"to subscribe {len(missing)} selected token(s)")
    except Exception as e:
        write_audit_log(f"[HA_SELECT][SUBSCRIBE_WARN] {e}")


def select_once(cfg: dict, instruments: List[Dict], kite_trade, locked: set,
                prev: Dict[str, List[Dict]]) -> Optional[List[Dict]]:
    '''Pure selection step (no I/O besides kite.ltp inside OptionSelector).
    Returns the final row list to persist, or None when nothing selectable.'''
    premium_cfg = cfg.get("option_premium", {}) or {}
    expiries = sorted({o["expiry"] for o in instruments})
    weekly_expiries = expiries[:2]
    instruments = [o for o in instruments if o["expiry"] in weekly_expiries]
    if not instruments:
        return None

    selector = OptionSelector(
        instruments=instruments,
        price_min=premium_cfg.get("min", 0),
        price_max=premium_cfg.get("max", 1e9),
        trade_mode=TRADE_MODE,
        atm_range=ATM_RANGE,
        strike_step=STRIKE_STEP,
        index_symbol=INDEX_SYMBOL,
        kite=kite_trade,
    )
    raw = selector.select()
    ce = list((raw or {}).get("CE", []) or [])
    pe = list((raw or {}).get("PE", []) or [])

    locked_ce = {s for s in locked if s.endswith("CE")}
    locked_pe = {s for s in locked if s.endswith("PE")}
    final: List[Dict] = _locked_rows(locked, prev, instruments)
    free_ce = [o for o in ce if o["tradingsymbol"] not in locked_ce]
    free_pe = [o for o in pe if o["tradingsymbol"] not in locked_pe]
    final.extend(free_ce[: max(0, PER_SIDE - len(locked_ce))])
    final.extend(free_pe[: max(0, PER_SIDE - len(locked_pe))])
    if not final:
        return None
    _attach_tokens(final, instruments)
    return final


async def ha_selection_loop(broker_manager, *args, **kwargs):
    '''Entry point spawned by StrategyRuntimeManager.start("HA_V1").'''
    global _LAST_SAVED
    await asyncio.sleep(0)   # yield immediately (Windows asyncio requirement)

    if not license_state.is_usable():
        write_audit_log(
            f"[HA_SELECT] License not usable ({license_state.LICENSE_STATUS}) — loop not started"
        )
        return

    write_audit_log(f"[HA_SELECT] Selection loop started ({STRATEGY_ID}) "
                    f"rule=band→weekly→median-ATM→±{ATM_RANGE}→nearest-{PER_SIDE}/side "
                    f"every {RECHECK_INTERVAL}s (own band, own files, shared WS engine)")

    while True:
        try:
            if not broker_manager.is_ready():
                write_audit_log("[HA_SELECT] Broker not ready")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            kite_trade = broker_manager.get_trade_kite()
            if not kite_trade:
                write_audit_log("[HA_SELECT] Trade session not ready (needed for kite.ltp)")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            cfg = load_strategy_config(STRATEGY_ID)
            instruments = load_nifty_weekly_options(
                api_key=kite_trade.api_key,
                access_token=kite_trade.access_token,
            )
            if not instruments:
                write_audit_log("[HA_SELECT][ERROR] No instruments loaded")
                await asyncio.sleep(RECHECK_INTERVAL)
                continue

            locked = _ha_locked_symbols()
            prev = load_selection(STRATEGY_ID)
            final = select_once(cfg, instruments, kite_trade, locked, prev)

            if not final:
                band = cfg.get("option_premium", {}) or {}
                write_audit_log(f"[HA_SELECT] selector returned empty "
                                f"(band {band.get('min')}–{band.get('max')}) — previous selection kept")
            else:
                save_selection(STRATEGY_ID, final)
                syms = [o.get("tradingsymbol") or o.get("symbol") for o in final]
                if syms != _LAST_SAVED:
                    write_audit_log(
                        f"[HA_SELECT] Updated selection ({STRATEGY_ID}): "
                        + ", ".join(f"{s}{'*' if s in locked else ''}" for s in syms)
                        + ("   (* = locked, open trade)" if locked else "")
                    )
                    _LAST_SAVED = syms
                _ensure_subscribed(final)

        except Exception as e:
            write_audit_log(f"[HA_SELECT] ERROR {repr(e)}")

        await asyncio.sleep(_seconds_to_next_boundary(RECHECK_INTERVAL, phase_offset=PHASE_OFFSET))
"""

SUITE = r"""'''HA_OWN_SELECT_20260923 behavioural suite. argv[1] = checkout root.
Engine tests (stubbed app.* deps, real evaluator / HA / EMA code):
  T1  COND1/2/3 signals on a CURRENTLY selected contract reach arbitration with signal ltp/sl
  T2  MIN_SL_GATE and HA_COND_FILTER unchanged
  T3  evaluator runs BEFORE the session gate: candles outside the session keep the
      buffer warm — first in-session candle is NOT a CANDLE_GAP_RESET
  T4  watched-today-but-not-currently-selected: evaluator keeps running, NO entry, no logs
  T5  after rotating out and back in, the contract's evaluator has NOT reset
  T6  _reload_selection reads HA_V1_selected_*.json: 2/side nearest-ATM first, band-filtered,
      no rows[0] fallback, locked row with ltp=None kept, tokens resolved, watched_today unions
  T7  TP tick check fires for every currently selected contract (2 per side) and active trades
  T8  reset_watched_today keeps only the current selection
Loop tests (select_once, fake selector via kite.ltp stub):
  T9  nearest-2 per side within HA's band on the nearest weekly expiry
  T10 lock carve-out: an open-trade contract stays selected (from prev row), free slots = 2 - locked
  T11 out-of-band everything → None (previous selection kept by the loop)
'''
import sys, types, importlib, os, json, tempfile, datetime as dt
ROOT = sys.argv[1]
sys.path.insert(0, ROOT + "/backend")
HOME = tempfile.mkdtemp(prefix="ha_home_"); os.environ["HOME"] = HOME
STATE = os.path.join(HOME, ".scalp-app", "state"); os.makedirs(STATE, exist_ok=True)

LOG = []
CFG = {"session": {"primary": {"start": "00:00", "end": "23:59"}}, "trade_side_mode": "BOTH",
       "min_sl_points": 0, "entry_conditions": [], "max_trades_per_side": 10,
       "option_premium": {"min": 150, "max": 200}}
def stub(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); m.__path__ = []; sys.modules[name] = m; return m
for pkg in ["app", "app.risk", "app.indicators", "app.engine", "app.engine.ha_options", "app.marketdata",
            "app.event_bus", "app.config", "app.core", "app.db", "app.selector", "app.fetcher", "app.utils", "app.license"]:
    if pkg not in sys.modules: stub(pkg)
sys.modules["app"].__path__ = [ROOT + "/backend/app"]
sys.modules["app.indicators"].__path__ = [ROOT + "/backend/app/indicators"]
sys.modules["app.engine.ha_options"].__path__ = [ROOT + "/backend/app/engine/ha_options"]
sys.modules["app.selector"].__path__ = [ROOT + "/backend/app/selector"]
stub("app.risk.risk_mtm_guard", mtm_breach_ha=lambda **k: None, is_day_blocked=lambda s: False, has_open_positions_ha=lambda m, t: False)
class _LTP:
    _d = {}
    @classmethod
    def update(c, s, v): c._d[s] = v
    @classmethod
    def get(c, s): return c._d.get(s)
stub("app.marketdata.ltp_store", LTPStore=_LTP)
stub("app.event_bus.audit_logger", write_audit_log=lambda m: LOG.append(m))
stub("app.marketdata.ws_registry", get_ws_engines=lambda: [])
stub("app.config.strategy_loader", load_strategy_config=lambda s: dict(CFG), load_strategy_config_ex=lambda s: (dict(CFG), False))
stub("app.config.global_loader", load_global_config=lambda: {"trade_on": True})
REG = []; stub("app.core.ha_engine_registry", HA_ENGINE_REGISTRY=REG)
stub("app.db.ha_candles_repo", init_table=lambda: None, insert_ha_candle=lambda **k: None, fetch_recent_ha_candles=lambda **k: [])
TP_CHECKS = []
class _TM:
    def __init__(self, **k): self._live = {}
    def attach_engine(self, e): pass
    def is_off(self): return False
    def _has_open_trade(self): return False
    def check_tp_on_tick(self, sym, ltp): TP_CHECKS.append(sym)
    def _mode(self): return "PAPER"
stub("app.engine.ha_options.ha_trade_manager", HATradeManager=_TM)
# selection loop deps
stub("app.fetcher.zerodha_instruments", load_nifty_weekly_options=lambda **k: [])
SAVED = {}
def _save(sid, rows): SAVED[sid] = rows
def _load(sid):
    out = {"CE": [], "PE": []}
    for side in ("CE", "PE"):
        p = os.path.join(STATE, f"{sid}_selected_{side.lower()}.json")
        if os.path.exists(p): out[side] = json.load(open(p))
    return out
stub("app.utils.selection_persistence", save_selection=_save, load_selection=_load)
stub("app.license.license_state", is_usable=lambda: True, LICENSE_STATUS="OK")
stub("app.license", license_state=sys.modules["app.license.license_state"])

eng_mod = importlib.import_module("app.engine.ha_options.ha_tick_engine")
from app.indicators.heikin_ashi import HACandle
# instrument master stub: token = strike
import pandas as pd
def _df():
    rows = [{"tradingsymbol": f"NIFTY_{k}{s}", "instrument_token": 1000 + i} for i, (k, s) in
            enumerate([(23100,"CE"),(23150,"CE"),(23200,"CE"),(23400,"PE"),(23450,"PE"),(23500,"PE")])]
    return pd.DataFrame(rows)
eng_mod._load_instruments_df = _df

OFFERS = []
def make_engine(sel_ce, sel_pe, today=None):
    e = eng_mod.HAOptionsTickEngine(executor=None, config=dict(CFG), trade_mode="PAPER")
    e._offer_to_arbitration = lambda **k: OFFERS.append(k)
    for i, s in enumerate(sel_ce + sel_pe + list(today or [])):
        if s not in e._states: e._states[s] = eng_mod.SymbolState(s, 5000 + i)
        e._token_map[e._states[s].token] = s
    e._watched_ce, e._watched_pe = list(sel_ce), list(sel_pe)
    e._watched_today = set(sel_ce) | set(sel_pe) | set(today or [])
    e._selected_ce = sel_ce[0] if sel_ce else None
    e._selected_pe = sel_pe[0] if sel_pe else None
    return e
T0 = 1_700_000_000
def feed(e, sym, seq, start=T0, ema=100.0):
    st = e._states[sym]; st.ema_low_value = ema
    for i, (o, h, l, c) in enumerate(seq):
        ha = HACandle(ts=start + 60 * i, open=o, high=h, low=l, close=c); st.last_ha = ha
        e._on_candle_close(sym, ha, st)
def reset(): OFFERS.clear(); LOG.clear(); _LTP._d.clear(); TP_CHECKS.clear()
COND1 = [(110,112,108,109),(109,109,98,99),(99,106,99,105)]
COND2 = [(110,112,108,109),(109,110,101,102),(102,104,99,103)]
COND3 = [(110,111,97,98),(98,105,102,104),(104,106,99,105)]

# T1
reset(); e = make_engine(["A_CE","B_CE"], ["A_PE","B_PE"]); _LTP.update("B_CE", 105.0)
feed(e, "B_CE", COND1); assert len(OFFERS)==1 and OFFERS[0]["symbol"]=="B_CE" and OFFERS[0]["sl_price"]==98.0 and OFFERS[0]["entry_ltp"]==105.0, OFFERS
reset(); e = make_engine(["A_CE","B_CE"], ["A_PE","B_PE"]); _LTP.update("A_PE", 103.0)
feed(e, "A_PE", COND2); assert OFFERS[-1]["condition"]=="COND2" and OFFERS[-1]["side"]=="PE", OFFERS
reset(); e = make_engine(["A_CE"], ["A_PE"]); _LTP.update("A_CE", 105.0)
feed(e, "A_CE", COND3); assert OFFERS[-1]["condition"]=="COND3" and OFFERS[-1]["sl_price"]==97.0, OFFERS
print("T1 PASS  signals on currently selected contracts (2/side) reach arbitration")

# T2
reset(); CFG["min_sl_points"]=18; e = make_engine(["A_CE"], []); _LTP.update("A_CE",105.0); feed(e,"A_CE",COND1)
assert not OFFERS and any("MIN SL 18.00" in m for m in LOG); CFG["min_sl_points"]=0
reset(); CFG["entry_conditions"]=["COND2","COND3"]; e = make_engine(["A_CE"], []); _LTP.update("A_CE",105.0); feed(e,"A_CE",COND1)
assert not OFFERS and any("condition COND1 not in enabled" in m for m in LOG); CFG["entry_conditions"]=[]
print("T2 PASS  MIN_SL_GATE + HA_COND_FILTER unchanged")

# T3 evaluator before session gate
import datetime
reset(); e = make_engine(["A_CE"], [])
real_now = eng_mod.datetime.now
class _FakeDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return cls(2026,9,23,9,25,0)
eng_mod.datetime = _FakeDT
CFG["session"]={"primary":{"start":"09:30","end":"13:00"}}
_LTP.update("A_CE",105.0)
feed(e, "A_CE", COND1[:2])                      # two candles at 09:25 (outside session)
assert any("outside session" in m for m in LOG) and not OFFERS
class _FakeDT2(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return cls(2026,9,23,9,31,0)
eng_mod.datetime = _FakeDT2
LOG.clear()
feed(e, "A_CE", COND1[2:], start=T0+120)        # third candle, in session, continuous ts
assert not any("CANDLE_GAP_RESET" in m for m in LOG), [m for m in LOG if "REJECT" in m]
assert len(OFFERS)==1 and OFFERS[0]["condition"]=="COND1", (OFFERS, LOG[-4:])
eng_mod.datetime = real_now.__self__ if hasattr(real_now, "__self__") else datetime.datetime
eng_mod.datetime = datetime.datetime
CFG["session"]={"primary":{"start":"00:00","end":"23:59"}}
print("T3 PASS  evaluator runs before the session gate — no gap reset at session open, COND1 fires on the first in-session candle")

# T4 watched today, not currently selected
reset(); e = make_engine(["A_CE"], [], today=["OLD_CE"]); _LTP.update("OLD_CE",105.0)
feed(e, "OLD_CE", COND1)
assert not OFFERS and not any("OLD_CE" in m and ("[HA][CANDLE]" in m or "NO_ENTRY" in m) for m in LOG)
assert e._states["OLD_CE"].evaluator._last_ts == T0+120, "evaluator did not run"
print("T4 PASS  watched-today-but-unselected: evaluator ran, no entry, no candle/no-entry noise")

# T5 rotate out and back: no reset
reset(); e = make_engine(["A_CE","B_CE"], []); _LTP.update("A_CE",105.0)
feed(e, "A_CE", COND1[:1])
e._watched_ce = ["B_CE","C_CE"]; e._states["C_CE"]=eng_mod.SymbolState("C_CE",7); e._watched_today.add("C_CE")
feed(e, "A_CE", COND1[1:2], start=T0+60)         # rotated out: still fed
e._watched_ce = ["A_CE","B_CE"]
LOG.clear(); feed(e, "A_CE", COND1[2:], start=T0+120)
assert not any("CANDLE_GAP_RESET" in m for m in LOG) and len(OFFERS)==1, (LOG[-3:], OFFERS)
print("T5 PASS  contract rotated out and back in keeps its evaluator state (backtest continuity)")

# T6 _reload_selection from files
reset(); e = make_engine([], [])
json.dump([{"tradingsymbol":"NIFTY_23150CE","ltp":180.0,"instrument_token":1001},
           {"tradingsymbol":"NIFTY_23200CE","ltp":160.0},                # token via master
           {"tradingsymbol":"NIFTY_23100CE","ltp":240.0}],              # out of band → dropped
          open(os.path.join(STATE,"HA_V1_selected_ce.json"),"w"))
json.dump([{"tradingsymbol":"NIFTY_23450PE","ltp":None,"instrument_token":1004},   # locked, ltp None → kept
           {"tradingsymbol":"NIFTY_23400PE","ltp":175.0,"instrument_token":1003}],
          open(os.path.join(STATE,"HA_V1_selected_pe.json"),"w"))
eng_mod.SymbolState.warmup_from_db = lambda self: None
e._reload_selection()
assert e._watched_ce==["NIFTY_23150CE","NIFTY_23200CE"], e._watched_ce
assert e._watched_pe==["NIFTY_23450PE","NIFTY_23400PE"], e._watched_pe
assert e._selected_ce=="NIFTY_23150CE" and e._selected_pe=="NIFTY_23450PE"
assert e._token_map[1002]=="NIFTY_23200CE" and e._watched_today=={"NIFTY_23150CE","NIFTY_23200CE","NIFTY_23450PE","NIFTY_23400PE"}
json.dump([{"tradingsymbol":"NIFTY_23100CE","ltp":240.0}], open(os.path.join(STATE,"HA_V1_selected_ce.json"),"w"))
e._reload_selection(); assert e._watched_ce==[] and e._selected_ce is None, "rows[0] fallback must be gone"
assert "NIFTY_23150CE" in e._watched_today
print("T6 PASS  own files: 2/side ordered, band-filtered, no rows[0] fallback, locked ltp=None kept, tokens resolved, union grows")

# T7 TP tick for all selected + active
reset(); e = make_engine(["A_CE","B_CE"], ["A_PE","B_PE"], today=["OLD_CE"]); e._active_trade_symbols={"HELD_PE"}
e._token_map[9]="HELD_PE"; e._states["HELD_PE"]=eng_mod.SymbolState("HELD_PE",9)
for sym in ("A_CE","B_CE","A_PE","B_PE","OLD_CE","HELD_PE"): e.on_tick(e._states[sym].token, 100.0, T0)
assert sorted(TP_CHECKS)==["A_CE","A_PE","B_CE","B_PE","HELD_PE"], TP_CHECKS
print("T7 PASS  TP tick check covers all 4 selected contracts + open trades, not stale ones")

# T8
e._watched_today |= {"X","Y"}; e.reset_watched_today(); assert e._watched_today=={"A_CE","B_CE","A_PE","B_PE"}
print("T8 PASS  daily reset trims watched_today to the current selection")

# ── loop
loop = importlib.import_module("app.engine.ha_options.ha_selection_loop")
class _Kite:
    api_key="k"; access_token="t"
    def __init__(self, ltps): self._l = ltps
    def ltp(self, keys): return {k: {"last_price": self._l[k.split(":")[1]]} for k in keys if k.split(":")[1] in self._l}
exp0 = dt.date(2026,9,29); exp1 = dt.date(2026,10,6)
def inst(strike, typ, exp, tok): 
    return {"tradingsymbol": f"NIFTY{strike}{typ}", "strike": strike, "instrument_type": typ, "expiry": exp, "instrument_token": tok, "exchange":"NFO", "name":"NIFTY"}
INST = [inst(23000+50*i, "CE", exp0, 1+i) for i in range(20)] + [inst(23000+50*i, "PE", exp0, 100+i) for i in range(20)] \
     + [inst(23300, "CE", exp1, 500)]
LT = {}
for i in range(20):  # CE premiums fall with strike, PE rise; band 150-200 catches a few each side around ATM-ish
    LT[f"NIFTY{23000+50*i}CE"] = 400 - 15*i
    LT[f"NIFTY{23000+50*i}PE"] = 40 + 12*i
LT["NIFTY23300CE"] = 180   # next-week duplicate symbol name irrelevant (dict), skip
kite = _Kite(LT)
cfg = {"option_premium": {"min":150,"max":200}}
fin = loop.select_once(cfg, INST, kite, locked=set(), prev={"CE":[],"PE":[]})
ce = [r["tradingsymbol"] for r in fin if r["type"]=="CE"]; pe = [r["tradingsymbol"] for r in fin if r["type"]=="PE"]
assert len(ce)==2 and len(pe)==2, fin
assert all(150 <= LT[s] <= 200 for s in ce+pe), (ce, pe)
assert all(r.get("instrument_token") for r in fin), fin
print("T9 PASS  select_once: 2 CE + 2 PE, all inside HA's band, tokens attached:", ce, pe)

# T10 lock carve-out
locked = {"NIFTY23150PE"}
prev = {"CE":[], "PE":[{"tradingsymbol":"NIFTY23150PE","symbol":"NIFTY23150PE","type":"PE","strike":23150.0,"ltp":76.0,"expiry":"2026-09-29"}]}
fin = loop.select_once(cfg, INST, kite, locked=locked, prev=prev)
pe = [r["tradingsymbol"] for r in fin if r["type"]=="PE"]
assert pe[0]=="NIFTY23150PE" and len(pe)==2, pe
assert len([r for r in fin if r["type"]=="CE"])==2
print("T10 PASS  locked open-trade contract stays selected (out of band), 1 free PE slot remains")

# T11 nothing in band
fin = loop.select_once({"option_premium":{"min":5000,"max":6000}}, INST, kite, locked=set(), prev={"CE":[],"PE":[]})
assert fin is None
print("T11 PASS  nothing in band → None (loop keeps the previous files)")
print("11/11 PASS")
"""


def fail(msg):
    print(f"  ABORT  {msg}")
    sys.exit(1)


def apply_edits(src, path):
    for p, name, old, new in EDITS:
        if p != path:
            continue
        c = src.count(old)
        if c != 1:
            fail(f"{path}: anchor [{name}] x{c}, expected x1 — file drifted from the expected "
                 f"post-revert state; inspect by hand")
        src = src.replace(old, new)
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry run, write nothing")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="proceed although git shows local edits on a target")
    a = ap.parse_args()

    targets = sorted({e[0] for e in EDITS})
    done = os.path.join(ROOT, f".{FENCE}.done")
    if os.path.exists(done):
        print(f"  SKIP   {FENCE} already applied — nothing to do")
        return
    srcs = {}
    for t in targets:
        p = os.path.join(ROOT, t)
        if not os.path.exists(p):
            fail(f"{t} not found — run from the scalp-app repo root")
        srcs[t] = open(p, encoding="utf-8").read()
    eng = srcs["backend/app/engine/ha_options/ha_tick_engine.py"]
    if FENCE in eng and os.path.exists(os.path.join(ROOT, NEW_FILE)):
        print(f"  SKIP   {FENCE} already present — already applied")
        return
    if "HA_COND1_FLIP" in eng:
        fail("HA_COND1_FLIP still in ha_tick_engine.py — run revert_ha_cond1_flip.py first")
    if os.path.exists(os.path.join(ROOT, NEW_FILE)):
        fail(f"{NEW_FILE} already exists — partial state; inspect by hand")

    try:
        r = subprocess.run(["git", "status", "--porcelain", "--"] + targets, cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        status = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        status = ""
    if status and not a.allow_dirty:
        fail("uncommitted edits on target files:\n         " + status.replace("\n", "\n         ")
             + "\n         commit the earlier HA patches first (preferred) or re-run with --allow-dirty")
    print("  OK     prerequisite verified, tree clean" + (" (dirty allowed)" if status else ""))

    staged = {t: apply_edits(s, t) for t, s in srcs.items()}
    print(f"  OK     {len(EDITS)} anchors x1 in {len(targets)} files")

    stage = tempfile.mkdtemp(prefix="ha_own_select_")
    shutil.copytree(os.path.join(ROOT, "backend", "app"), os.path.join(stage, "backend", "app"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak-*", "dist", "build", "_internal"))
    for t, s in staged.items():
        sp = os.path.join(stage, t)
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(s)
    with open(os.path.join(stage, NEW_FILE), "w", encoding="utf-8") as f:
        f.write(NEW_FILE_SRC)

    for t in PY_FILES:
        try:
            py_compile.compile(os.path.join(stage, t), doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"py_compile {t}: {e}")
    print("  OK     py_compile gate passed")
    try:
        import pyflakes  # noqa
        r = subprocess.run([sys.executable, "-m", "pyflakes"] + [os.path.join(stage, t) for t in PY_FILES],
                           capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() if "undefined name" in l]
        if bad:
            fail("pyflakes undefined names:\n         " + "\n         ".join(bad))
        print("  OK     pyflakes undefined-name gate passed")
    except ImportError:
        print("  WARN   pyflakes not installed — undefined-name gate skipped")

    tp = os.path.join(stage, "suite.py")
    with open(tp, "w", encoding="utf-8") as f:
        f.write(SUITE)
    r = subprocess.run([sys.executable, tp, stage], capture_output=True, text=True)
    if r.returncode != 0 or "11/11 PASS" not in r.stdout:
        fail(f"behavioural suite failed:\n{r.stdout[-2500:]}\n{r.stderr[-2500:]}")
    for line in r.stdout.strip().splitlines():
        if line.startswith("T") or line.endswith("PASS"):
            print("         " + line)
    print("  OK     behavioural suite 11/11")

    esb, npx = shutil.which("esbuild"), shutil.which("npx")
    cmd = ([esb] if esb else [npx, "--yes", "esbuild"] if npx else None)
    if cmd is None:
        print("  WARN   esbuild unavailable — JSX gate skipped")
    else:
        for t in JSX_FILES:
            g = subprocess.run(cmd + ["--loader:.jsx=jsx", os.path.join(stage, t), "--outfile=" + os.devnull],
                               capture_output=True, text=True, cwd=os.path.join(ROOT, "frontend"))
            if g.returncode != 0:
                fail(f"esbuild gate {t}:\n{g.stderr[-2000:]}")
        print("  OK     esbuild JSX gate passed")

    if a.check:
        for t in targets:
            print(f"  WOULD  edit   {t}")
        print(f"  WOULD  create {NEW_FILE}")
        print("  CHECK  dry run complete — no files written")
        return

    written = []
    newp = os.path.join(ROOT, NEW_FILE)
    try:
        for t in targets:
            p = os.path.join(ROOT, t)
            shutil.copy2(p, p + f".bak-{FENCE}")
            with open(p, "w", encoding="utf-8") as f:
                f.write(staged[t])
            written.append(p)
            print(f"  WROTE  {t}")
        with open(newp, "w", encoding="utf-8") as f:
            f.write(NEW_FILE_SRC)
        print(f"  WROTE  {NEW_FILE} (new)")
        for t in targets:
            if open(os.path.join(ROOT, t), encoding="utf-8").read() != staged[t]:
                raise RuntimeError(f"post-write verification failed on {t}")
        if open(newp, encoding="utf-8").read() != NEW_FILE_SRC:
            raise RuntimeError("post-write verification failed on the new file")
    except Exception as e:
        for p in written:
            shutil.copy2(p + f".bak-{FENCE}", p)
        if os.path.exists(newp):
            os.remove(newp)
        fail(f"{e} — everything restored from backup")

    for t in [x for x in PY_FILES if x != NEW_FILE]:
        mp = os.path.join(ROOT, MIRROR_BACKEND, t[len("backend/"):])
        if os.path.exists(mp):
            ms = open(mp, encoding="utf-8").read()
            if "HA_COND1_FLIP" not in ms and all(ms.count(o) == 1 for p, _, o, _ in EDITS if p == t):
                shutil.copy2(mp, mp + f".bak-{FENCE}")
                with open(mp, "w", encoding="utf-8") as f:
                    f.write(apply_edits(ms, t))
                print(f"  WROTE  mirror {t}")
            else:
                print(f"  NOTE   mirror {t} not patched (anchors differ) — build-scalp.sh re-syncs it")
    mn = os.path.join(ROOT, MIRROR_BACKEND, NEW_FILE[len("backend/"):])
    if os.path.isdir(os.path.dirname(mn)) and not os.path.exists(mn):
        with open(mn, "w", encoding="utf-8") as f:
            f.write(NEW_FILE_SRC)
        print("  WROTE  mirror new file")

    with open(done, "w") as f:
        f.write(FENCE + "\n")
    print()
    print("  DONE   HA_V1 now selects for itself (own band, own HA_V1_selected_*.json, 2 per side,")
    print("         evaluator continuous on the day's union, entry only on the current selection).")
    print("         Rebuild: ./desktop/build-scalp.sh both  — then restart the backend.")
    print("         Next session: look for [HA_SELECT] Updated selection (HA_V1) within ~2 min of the")
    print("         trade session; ha_gate_diag.py stage 2a reads it. Backups: *.bak-" + FENCE)


if __name__ == "__main__":
    main()
