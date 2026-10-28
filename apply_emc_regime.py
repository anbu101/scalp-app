#!/usr/bin/env python3
# apply_emc_regime.py — EMC_REGIME_20261028
#
# EMC_V1 "Ember": VET_V1's regime channel as an OPTIONAL gate — BACKTEST ONLY.
#   Channel = SMA(len) ± ATR(len) × mult on the SAME spot candles as the EMA
#   cross (VET's sell seal: SMA36 ± ATR×0.618). A candle inside the channel
#   reads "range"; outside it reads UP (close ≥ SMA) or DOWN — VET's exact
#   dirTrend, proven bar-for-bar against vet_v1_engine.vet_states.
#
#   Regime gate (new EMC Backtest section, under Leg action):
#     off                  the sealed strategy, byte-identical (default)
#     at the arming cross  the cross candle itself must read UP (up cross) /
#                          DOWN (down cross), else the cross arms nothing — it
#                          still exits an open position / replaces a setup
#     entry waits for it   the cross arms as usual; the entry ALSO waits until
#                          the latest closed spot candle reads the right side
#   Strictness: strict = outside the channel on the trade's side (VET's rule);
#               loose  = only the opposite side blocks (inside passes).
#   SELL follows the cross (an up cross sells the PE and needs UP).
#
#   Diag tripwires: cross_regime_with / _range / _against, sig_dropped_regime,
#   setups_regime_wait → entries_after_regime_wait / regime_wait_unfilled,
#   regime_entries / regime_net. Trades get a ·regUP / ·regRNG / ·regDN tag.
#   Trade Replay draws the channel + SMA and tags each cross with its regime.
#   SweepBuilder: emc_regime (OFF, ARM, ENTRY, ARM_LOOSE, ENTRY_LOOSE),
#   emc_regime_len, emc_regime_mult. Paper/live REFUSE a non-off gate.
#
# REQUIRES apply_emc_sell.py (EMC_SELL_20261012) — already on GitHub main.
#
# Touches (backend files also in desktop/src-tauri/backend when present):
#   backend/app/backtest/emc/emc_v1_engine.py
#   backend/app/backtest/emc/backtest_emc_runner.py
#   backend/app/engine/emc/emc_live_core.py        (refusal only)
#   backend/app/backtest/replay/overlays.py        (EMC overlay)
#   backend/app/backtest/emc/test_emc_regime.py    NEW (52 checks)
#   frontend/src/pages/Backtest.jsx
#   frontend/src/pages/backtest/paramFormat.js
#   frontend/src/pages/backtest/SweepBuilder.jsx
#
# USAGE (repo root):
#   python3 apply_emc_regime.py --dry-run
#   python3 apply_emc_regime.py
#   python3 apply_emc_regime.py --revert
# Then: ./desktop/build-scalp.sh both
import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "EMC_REGIME_20261028"
PARENT = "EMC_SELL_20261012"
BAK = ".bak-" + FENCE
MIRROR = Path("desktop/src-tauri/backend")
TEST_REL = "backend/app/backtest/emc/test_emc_regime.py"

ENGINE_EDITS = [
    ('#       the day; an open position is always managed to its exit.\n#\n# Fleet convention: no imports from sibling strategy packages;',
     '#       the day; an open position is always managed to its exit.\n#   E10 Regime gate (EMC_REGIME_20261028, backtest only, default off): VET_V1\'s\n#       SMA(len) ± ATR(len)×mult channel on the SAME spot bars as the EMA cross\n#       (dirTrend: inside the channel = 0, else close >= SMA → +1, else −1;\n#       VET\'s literal loose containment test P4 kept). "arm": the cross candle\'s\n#       own regime must agree or the cross arms nothing (it still exits /\n#       replaces). "entry": the cross arms as usual and the entry also waits\n#       until the latest CLOSED spot bar\'s regime agrees. strict = +1 for an up\n#       cross / −1 for a down cross; loose = anything but the opposite regime.\n#\n# Fleet convention: no imports from sibling strategy packages;'),
    ('    return out\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  E4  SELECTION',
     '    return out\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  E10  REGIME — VET_V1\'s SMA ± ATR channel  # ── EMC_REGIME_20261028 ──\n# ─────────────────────────────────────────────────────────────────────────\nclass SpotRegime:\n    """VET_V1\'s dirTrend, incremental, on closed spot bars (continuous across\n    sessions like the spot EMAs). Same arithmetic as vet_v1_engine\n    (sma_series / atr_series / the P4 test) — test_emc_regime.py proves it bar\n    for bar. Duplicated here on purpose (fleet convention: no sibling imports).\n      SMA = Pine ta.sma(close, len): None until len closes.\n      ATR = Pine ta.atr(len) = RMA(TR, len): None until len TRs, seeded with\n            their SMA, then Wilder; the first TR (no previous close) = H − L.\n      channel = SMA ± ATR × mult\n      in_range = (open <= top or close <= top) and (open >= bot or close >= bot)\n      dir = 0 while invalid or in range, else +1 if close >= SMA else −1."""\n\n    __slots__ = ("length", "mult", "_win", "_acc", "_n", "_prev_close", "_tr_acc", "_tr_n",\n                 "atr", "sma", "top", "bot", "dir")\n\n    def __init__(self, length: int, mult: float):\n        if int(length) < 2:\n            raise ValueError("regime length must be >= 2")\n        self.length = int(length)\n        self.mult = float(mult)\n        self._win: List[float] = []\n        self._acc = 0.0\n        self._n = 0\n        self._prev_close: Optional[float] = None\n        self._tr_acc = 0.0\n        self._tr_n = 0\n        self.atr: Optional[float] = None\n        self.sma: Optional[float] = None\n        self.top: Optional[float] = None\n        self.bot: Optional[float] = None\n        self.dir = 0\n\n    def update(self, bar) -> int:\n        o, h, lo, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)\n        n = self.length\n        # SMA — vet sma_series: acc += v; once i >= n: acc -= vals[i − n]\n        self._acc += c\n        self._win.append(c)\n        if self._n >= n:\n            self._acc -= self._win.pop(0)\n        self._n += 1\n        self.sma = self._acc / n if self._n >= n else None\n        # ATR — vet atr_series / rma_series\n        tr = (h - lo) if self._prev_close is None else max(h - lo, abs(h - self._prev_close),\n                                                           abs(lo - self._prev_close))\n        self._prev_close = c\n        if self.atr is None:\n            self._tr_acc += tr\n            self._tr_n += 1\n            if self._tr_n == n:\n                self.atr = self._tr_acc / n\n        else:\n            self.atr = (self.atr * (n - 1) + tr) / n\n        if self.sma is None or self.atr is None:\n            self.top = self.bot = None\n            self.dir = 0\n            return 0\n        basis = self.atr * self.mult\n        self.top, self.bot = self.sma + basis, self.sma - basis\n        in_range = ((o <= self.top or c <= self.top) and (o >= self.bot or c >= self.bot))\n        self.dir = 0 if in_range else (1 if c >= self.sma else -1)\n        return self.dir\n\n\ndef spot_regimes(bars: Sequence[EmcBar], reg: SpotRegime) -> Dict[int, int]:\n    """Advance the continuous regime over one session\'s closed spot bars;\n    returns {bar end minute: dir} for every bar of the session."""\n    return {b.end_min: reg.update(b) for b in bars}\n\n\ndef regime_agrees(x: int, d: Optional[int], loose: bool) -> bool:\n    """x = cross direction (+1 up / −1 down), d = regime dir. strict: d == x;\n    loose: d != −x (inside the channel passes)."""\n    d = int(d or 0)\n    return d != -x if loose else d == x\n\n\n# ─────────────────────────────────────────────────────────────────────────\n#  E4  SELECTION'),
    ('    sell: bool = False\n\n\n@dataclass\nclass DayTrade:',
     '    sell: bool = False\n    # ── EMC_REGIME_20261028 ── VET regime gate: off | arm | entry; loose = only\n    # the OPPOSITE regime blocks (inside the channel passes)\n    regime_gate: str = "off"\n    regime_loose: bool = False\n\n\n@dataclass\nclass DayTrade:'),
    ('    sell: bool = False                 # ── EMC_SELL_20261012 ── a short leg\n',
     '    sell: bool = False                 # ── EMC_SELL_20261012 ── a short leg\n    regime: Optional[int] = None       # ── EMC_REGIME_20261028 ── spot regime at the entry decision (gate on only)\n'),
    ('                 diag: dict, bars_of=None, session_open: int = SESSION_OPEN_MIN, trade_net=None):\n        self.p = p',
     '                 diag: dict, bars_of=None, session_open: int = SESSION_OPEN_MIN, trade_net=None,\n                 regime_at=None):\n        self.p = p\n        # ── EMC_REGIME_20261028 ── regime_at(k) → dir (−1/0/+1) of the latest CLOSED spot\n        # bar at minute k; None = no regime source (gate must then be off)\n        self.regime_at = regime_at'),
    ('    def _bump(self, key, n=1):\n        self.D[key] = self.D.get(key, 0) + n\n',
     '    def _bump(self, key, n=1):\n        self.D[key] = self.D.get(key, 0) + n\n\n    # ── EMC_REGIME_20261028 ──\n    def _regime(self, k: int) -> Optional[int]:\n        if self.p.regime_gate == "off" or self.regime_at is None:\n            return None\n        return int(self.regime_at(k) or 0)\n\n    def _regime_ok(self, x: int, k: int) -> bool:\n        return regime_agrees(x, self._regime(k), self.p.regime_loose)\n\n    def _note_drop(self) -> None:\n        if self.setup is not None and self.setup.get("regime_wait"):\n            self._bump("regime_wait_unfilled")\n'),
    ('        elif not aligned(setup["side"], f, s):\n            return False\n        if self.can_enter(k):\n            return False',
     '        elif not aligned(setup["side"], f, s):\n            return False\n        if p.regime_gate == "entry" and not self._regime_ok(setup["x"], k):   # ── EMC_REGIME_20261028 ──\n            if not setup.get("regime_wait"):\n                setup["regime_wait"] = True\n                self._bump("setups_regime_wait")\n            return False\n        if self.can_enter(k):\n            return False'),
    ('                     entry_spot=self.spot_at(k), sell=bool(p.sell))',
     '                     entry_spot=self.spot_at(k), sell=bool(p.sell),\n                     regime=self._regime(k))   # ── EMC_REGIME_20261028 ──'),
    ('        if setup.get("unready"):\n            self._bump("entries_after_warmup_wait")\n        self.setup = None\n        return True',
     '        if setup.get("unready"):\n            self._bump("entries_after_warmup_wait")\n        if setup.get("regime_wait"):                   # ── EMC_REGIME_20261028 ──\n            self._bump("entries_after_regime_wait")\n        self.setup = None\n        return True'),
    ('            if self.setup is not None and self.setup["side"] != side:\n                self._bump("setups_replaced")\n                self.setup = None\n            if p.skip_from >= 0 and p.skip_from <= k < p.skip_to:   # ── EMC_V1_BLACKOUT_20261011 ──\n                self._bump("sig_dropped_blackout")\n            elif p.direction not in ("BOTH", dside):\n                self._bump("sig_dropped_dir")\n',
     '            if self.setup is not None and self.setup["side"] != side:\n                self._bump("setups_replaced")\n                self._note_drop()                       # ── EMC_REGIME_20261028 ──\n                self.setup = None\n            if p.regime_gate != "off":                  # ── EMC_REGIME_20261028 ── regime at every cross\n                _rd = self._regime(k)\n                self._bump("cross_regime_with" if _rd == x else\n                           "cross_regime_against" if _rd == -x else "cross_regime_range")\n            if p.skip_from >= 0 and p.skip_from <= k < p.skip_to:   # ── EMC_V1_BLACKOUT_20261011 ──\n                self._bump("sig_dropped_blackout")\n            elif p.direction not in ("BOTH", dside):\n                self._bump("sig_dropped_dir")\n            elif p.regime_gate == "arm" and not self._regime_ok(x, k):   # ── EMC_REGIME_20261028 ──\n                self._bump("sig_dropped_regime")\n'),
    ('                            self.setup = {"side": side, "c": c, "k": k, "track": self.track_of(c),\n                                          "unready": False}',
     '                            self.setup = {"side": side, "c": c, "k": k, "track": self.track_of(c),\n                                          "unready": False, "x": x}   # ── EMC_REGIME_20261028 ──'),
    ('                self._bump("setups_expired_cutoff")\n                self.setup = None',
     '                self._bump("setups_expired_cutoff")\n                self._note_drop()                       # ── EMC_REGIME_20261028 ──\n                self.setup = None'),
    ('            self._bump("setups_expired_eod")\n            if self.setup.get("unready"):\n                self._bump("setups_expired_unready")\n            self.setup = None',
     '            self._bump("setups_expired_eod")\n            if self.setup.get("unready"):\n                self._bump("setups_expired_unready")\n            self._note_drop()                           # ── EMC_REGIME_20261028 ──\n            self.setup = None'),
    ('                 trade_net=None, entry_hook=None) -> List[DayTrade]:',
     '                 trade_net=None, entry_hook=None, regime_at=None) -> List[DayTrade]:'),
    ('                      fill_exit=fill_px, diag=diag, bars_of=bars_of, session_open=session_open,\n                      trade_net=trade_net)',
     '                      fill_exit=fill_px, diag=diag, bars_of=bars_of, session_open=session_open,\n                      trade_net=trade_net, regime_at=regime_at)   # ── EMC_REGIME_20261028 ──'),
]

LIVE_EDITS = [
    ('    if m.get("carry_overnight"):                   # ── EMC_CARRY_20261012 ──\n        return "carry_overnight is backtest-only for now — refusing to trade (set it back to OFF)"\n',
     '    if m.get("carry_overnight"):                   # ── EMC_CARRY_20261012 ──\n        return "carry_overnight is backtest-only for now — refusing to trade (set it back to OFF)"\n    if str(m.get("regime_gate") or "off").strip().lower() != "off":   # ── EMC_REGIME_20261028 ──\n        return "regime_gate (VET SMA ± ATR channel) is backtest-only for now — refusing to trade (set it to off)"\n'),
]

RUNNER_EDITS = [
    ('#   R5  Sizing: lots × lot size, then DTE × lots, then lot compounding — the\n#       fleet order (DTE_LOT_MULT_20260911, LOT_COMP_20260924).\n',
     "#   R5  Sizing: lots × lot size, then DTE × lots, then lot compounding — the\n#       fleet order (DTE_LOT_MULT_20260911, LOT_COMP_20260924).\n#   R8  Regime gate (EMC_REGIME_20261028, backtest only, default off): VET_V1's\n#       SMA(regime_len) ± ATR(regime_len)×regime_mult channel on the spot_tf\n#       bars, warmed and advanced exactly like the spot EMA pair (R1). Engine\n#       rule E10. Tripwires: cross_regime_with / _range / _against (what the\n#       crosses looked like), sig_dropped_regime (arm), setups_regime_wait →\n#       entries_after_regime_wait / regime_wait_unfilled (entry), and\n#       regime_entries / regime_net by the regime at the entry decision.\n"),
    ('    from app.backtest.emc.emc_v1_engine import (\n        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,\n        simulate_day, SESSION_OPEN_MIN, SESSION_LAST_MIN)',
     '    from app.backtest.emc.emc_v1_engine import (\n        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,\n        simulate_day, SESSION_OPEN_MIN, SESSION_LAST_MIN,\n        SpotRegime, spot_regimes)   # ── EMC_REGIME_20261028 ──'),
    ('    from emc_v1_engine import (  # type: ignore\n        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,\n        simulate_day, SESSION_OPEN_MIN, SESSION_LAST_MIN)',
     '    from emc_v1_engine import (  # type: ignore\n        EmcBar, EmaPair, OptionEmaTrack, DayParams, resample_session, spot_crosses,\n        simulate_day, SESSION_OPEN_MIN, SESSION_LAST_MIN,\n        SpotRegime, spot_regimes)   # ── EMC_REGIME_20261028 ──'),
    ('    "hedge_skew_mult": 1.0,\n',
     '    "hedge_skew_mult": 1.0,\n    # ── EMC_REGIME_20261028 ── BACKTEST ONLY. VET_V1\'s regime channel as an optional\n    # gate: "off" (the sealed strategy) | "arm" (the cross candle\'s own regime\n    # must agree, else the cross arms nothing) | "entry" (the setup arms and\n    # the entry waits for the latest closed spot bar\'s regime to agree).\n    # regime_mode "strict" = up needs +1 / down needs −1; "loose" = only the\n    # opposite regime blocks. Channel = SMA(regime_len) ± ATR(regime_len) ×\n    # regime_mult on the spot_tf bars (VET\'s sell seal: 36 / 0.618).\n    "regime_gate": "off",\n    "regime_mode": "strict",\n    "regime_len": 36,\n    "regime_mult": 0.618,\n'),
    ('    cfg["hedge_skew_mult"] = cfg["hedge_skew_mult"] or 1.0\n',
     '    cfg["hedge_skew_mult"] = cfg["hedge_skew_mult"] or 1.0\n    _g = str(cfg.get("regime_gate") or "off").strip().lower()   # ── EMC_REGIME_20261028 ──\n    cfg["regime_gate"] = _g if _g in ("off", "arm", "entry") else "off"\n    cfg["regime_mode"] = "loose" if str(cfg.get("regime_mode") or "strict").strip().lower() == "loose" else "strict"\n    try:\n        cfg["regime_len"] = int(cfg.get("regime_len") if cfg.get("regime_len") is not None else DEFAULTS["regime_len"])\n    except (TypeError, ValueError):\n        cfg["regime_len"] = int(DEFAULTS["regime_len"])\n    try:\n        cfg["regime_mult"] = abs(float(cfg.get("regime_mult") if cfg.get("regime_mult") is not None\n                                       else DEFAULTS["regime_mult"]))\n    except (TypeError, ValueError):\n        cfg["regime_mult"] = float(DEFAULTS["regime_mult"])\n'),
    ('    if cfg["warmup_sessions"] > 30 or cfg["opt_warmup_sessions"] > 10:',
     '    if cfg.get("regime_gate", "off") != "off":           # ── EMC_REGIME_20261028 ──\n        if not (2 <= cfg["regime_len"] <= 200):\n            return f"regime_len {cfg[\'regime_len\']} must be 2..200 spot bars (VET\'s sell seal: 36)"\n        if not (0 < cfg["regime_mult"] <= 5):\n            return f"regime_mult {cfg[\'regime_mult\']:g} must be > 0 and <= 5 (an ATR multiple, VET: 0.618)"\n        _per = (15 * 60 + 30 - SESSION_OPEN_MIN) // max(1, cfg["spot_tf"])\n        if cfg["warmup_sessions"] * _per < cfg["regime_len"]:\n            return (f"regime_len {cfg[\'regime_len\']} needs more spot warm-up: {cfg[\'warmup_sessions\']} sessions × "\n                    f"{_per} bars of {cfg[\'spot_tf\']}m < {cfg[\'regime_len\']} — raise warmup_sessions")\n    if cfg["warmup_sessions"] > 30 or cfg["opt_warmup_sessions"] > 10:'),
    ('    spot_pair = EmaPair(cfg["ema_fast"], cfg["ema_slow"])\n',
     '    spot_pair = EmaPair(cfg["ema_fast"], cfg["ema_slow"])\n    regime = (SpotRegime(cfg["regime_len"], cfg["regime_mult"])   # ── EMC_REGIME_20261028 ── R8\n              if cfg["regime_gate"] != "off" else None)\n'),
    ('            spot_pair.update(b.close)\n            warmup_bars += 1\n',
     '            spot_pair.update(b.close)\n            if regime is not None:                     # ── EMC_REGIME_20261028 ──\n                regime.update(b)\n            warmup_bars += 1\n'),
    ('        sell=cfg["leg_action"] == "SELL")   # ── EMC_SELL_20261012 ──',
     '        sell=cfg["leg_action"] == "SELL",   # ── EMC_SELL_20261012 ──\n        regime_gate=cfg["regime_gate"], regime_loose=cfg["regime_mode"] == "loose")   # ── EMC_REGIME_20261028 ──'),
    ('        "hedge_synth_pnl_gross": 0.0, "hedge_synth_mark_fallbacks": 0, "hedge_entry_px_sum": 0.0,\n',
     '        "hedge_synth_pnl_gross": 0.0, "hedge_synth_mark_fallbacks": 0, "hedge_entry_px_sum": 0.0,\n        # ── EMC_REGIME_20261028 ──\n        "regime_gate": cfg["regime_gate"], "regime_mode": cfg["regime_mode"],\n        "regime_len": cfg["regime_len"], "regime_mult": cfg["regime_mult"],\n        "regime_entries": {"with": 0, "range": 0, "against": 0},\n        "regime_net": {"with": 0.0, "range": 0.0, "against": 0.0},\n'),
    ('        cross_at = spot_crosses(bars_tf, spot_pair)       # R1: ALWAYS advances\n',
     "        cross_at = spot_crosses(bars_tf, spot_pair)       # R1: ALWAYS advances\n        if regime is not None:                     # ── EMC_REGIME_20261028 ── R8: advances with the pair\n            reg_open = regime.dir                  # yesterday's last closed bar\n            reg_today = spot_regimes(bars_tf, regime)\n            reg_mins = sorted(reg_today)\n"),
    ('        def spot_at(k: int) -> Optional[float]:\n            b = spot_by_min.get(k)\n            return float(b.close) if b is not None else None\n',
     '        def spot_at(k: int) -> Optional[float]:\n            b = spot_by_min.get(k)\n            return float(b.close) if b is not None else None\n\n        regime_at = None\n        if regime is not None:                     # ── EMC_REGIME_20261028 ──\n            import bisect as _bisect\n\n            def regime_at(k: int, _m=reg_mins, _r=reg_today, _o=reg_open) -> int:\n                """dir of the latest spot bar CLOSED at minute k (its end minute <= k)."""\n                i = _bisect.bisect_right(_m, k)\n                return _r[_m[i - 1]] if i else _o\n'),
    ('                                  trade_net=trade_net, entry_hook=entry_hook if hedge_on else None)   # ── EMC_SELL_20261012 ──',
     '                                  trade_net=trade_net, entry_hook=entry_hook if hedge_on else None,   # ── EMC_SELL_20261012 ──\n                                  regime_at=regime_at)   # ── EMC_REGIME_20261028 ──'),
    ('            if is_sell:                                # ── EMC_SELL_20261012 ──\n                carry_tag += "·SELL" + (f"·wing{(\'~\' if wing[\'synth\'] else \'\')}₹{wing[\'px\']:g}" if wing else "·naked")\n',
     '            if is_sell:                                # ── EMC_SELL_20261012 ──\n                carry_tag += "·SELL" + (f"·wing{(\'~\' if wing[\'synth\'] else \'\')}₹{wing[\'px\']:g}" if wing else "·naked")\n            if regime is not None and dt.regime is not None:   # ── EMC_REGIME_20261028 ──\n                _bull = (dt.side == "CE") != bool(dt.sell)\n                _rel = ("with" if dt.regime == (1 if _bull else -1) else\n                        "against" if dt.regime == (-1 if _bull else 1) else "range")\n                diag["regime_entries"][_rel] += 1\n                diag["regime_net"][_rel] = round(diag["regime_net"][_rel] + net, 2)\n                carry_tag += "·reg" + {1: "UP", 0: "RNG", -1: "DN"}[dt.regime]\n'),
]

OVERLAY_EDITS = [
    ('    from app.backtest.emc.emc_v1_engine import EmcBar, EmaPair, crossed, resample_session\n    cfg = _merge_cfg(ctx.cfg)',
     '    from app.backtest.emc.emc_v1_engine import EmcBar, EmaPair, crossed, resample_session\n    from app.backtest.emc.emc_v1_engine import SpotRegime, regime_agrees   # ── EMC_REGIME_20261028 ──\n    cfg = _merge_cfg(ctx.cfg)'),
    ('    pair = EmaPair(fast, slow)\n    for wd in before:\n        for b in tf_bars(wd, stf):\n            pair.update(b.close)\n',
     '    pair = EmaPair(fast, slow)\n    reg = (SpotRegime(cfg["regime_len"], cfg["regime_mult"])   # ── EMC_REGIME_20261028 ──\n           if cfg.get("regime_gate", "off") != "off" else None)\n    r_up, r_dn, r_sma = [], [], []\n    for wd in before:\n        for b in tf_bars(wd, stf):\n            pair.update(b.close)\n            if reg is not None:\n                reg.update(b)\n'),
    ('            pf, ps = pair.values\n            f, s = pair.update(b.close)\n            if f is not None:\n                ef.append([b.ts, f])',
     '            pf, ps = pair.values\n            f, s = pair.update(b.close)\n            rd = reg.update(b) if reg is not None else 0   # ── EMC_REGIME_20261028 ──\n            if reg is not None and reg.sma is not None:\n                r_sma.append([b.ts, reg.sma])\n                r_up.append([b.ts, reg.top])\n                r_dn.append([b.ts, reg.bot])\n            if f is not None:\n                ef.append([b.ts, f])'),
    ('                if blk and blk[0] <= b.end_min < blk[1]:   # ── EMC_V1_BLACKOUT_20261011 ──\n                    tag += " (arming blackout — not armed)"\n                    ignored = True\n',
     '                if blk and blk[0] <= b.end_min < blk[1]:   # ── EMC_V1_BLACKOUT_20261011 ──\n                    tag += " (arming blackout — not armed)"\n                    ignored = True\n                if reg is not None:                    # ── EMC_REGIME_20261028 ──\n                    tag += " · regime " + {1: "UP", 0: "in channel", -1: "DOWN"}[rd]\n                    if (not ignored and cfg["regime_gate"] == "arm"\n                            and not regime_agrees(x, rd, cfg["regime_mode"] == "loose")):\n                        tag += " (regime — not armed)"\n                        ignored = True\n'),
    ('                {"type": "dots", "id": "crosses", "label": "spot crosses", "color": "warning", "tf": stf,\n                 "points": xs}]\n',
     '                {"type": "dots", "id": "crosses", "label": "spot crosses", "color": "warning", "tf": stf,\n                 "points": xs}]\n    if reg is not None:                                # ── EMC_REGIME_20261028 ── VET\'s channel, drawn like _vet\n        _lab = f"SMA{cfg[\'regime_len\']} ± ATR·{cfg[\'regime_mult\']:g}"\n        spot_ovs = [{"type": "band", "id": "regime_channel", "label": _lab, "color": "muted", "tf": stf,\n                     "upper": r_up, "lower": r_dn},\n                    {"type": "line", "id": "regime_sma", "label": f"SMA {cfg[\'regime_len\']}", "color": "c",\n                     "tf": stf, "points": r_sma, "dash": True}] + spot_ovs\n'),
    ('    notes.append("the previous session is drawn so an overnight-gap cross shows as a crossing of the lines")\n',
     '    notes.append("the previous session is drawn so an overnight-gap cross shows as a crossing of the lines")\n    if reg is not None:                                # ── EMC_REGIME_20261028 ──\n        notes.append(f"regime gate \'{cfg[\'regime_gate\']}\' ({cfg[\'regime_mode\']}): "\n                     + ("a cross arms only when ITS candle is outside the channel on its side"\n                        if cfg["regime_gate"] == "arm" else\n                        "the entry waits until the latest closed spot candle is outside the channel on its side")\n                     + (" (loose: only the opposite side blocks)" if cfg["regime_mode"] == "loose" else ""))\n'),
    ('                            f"{(\' · no arming \' + cfg[\'arm_skip_from\'] + \'–\' + cfg[\'arm_skip_to\']) if blk else \'\'}"),',
     '                            f"{(\' · no arming \' + cfg[\'arm_skip_from\'] + \'–\' + cfg[\'arm_skip_to\']) if blk else \'\'}"\n                            f"{(\' · regime \' + cfg[\'regime_gate\'] + \' SMA\' + str(cfg[\'regime_len\']) + \'±ATR×\' + format(cfg[\'regime_mult\'], \'g\')) if reg is not None else \'\'}"),   # ── EMC_REGIME_20261028 ──'),
]

BACKTEST_EDITS = [
    ('    if (cfg.leg_action === "SELL") add("Leg", cfg.hedge_enabled ?',
     '    if (cfg.regime_gate && cfg.regime_gate !== "off") add("Regime", `VET SMA${cfg.regime_len ?? 36} ± ATR×${cfg.regime_mult ?? 0.618} · ${cfg.regime_gate === "entry" ? "entry waits for it" : "at the arming cross"}${cfg.regime_mode === "loose" ? " · loose (only the opposite side blocks)" : ""}`);   // ── EMC_REGIME_20261028 ──\n    if (cfg.leg_action === "SELL") add("Leg", cfg.hedge_enabled ?'),
    ('  useEffect(() => {\n    try { localStorage.setItem(EMC_LS_KEY',
     '  // ── EMC_REGIME_20261028 ── VET_V1\'s SMA ± ATR regime channel as an optional gate (backtest only)\n  const [emcRegGate, setEmcRegGate] = useState(emcSaved.regGate ?? "off");\n  const [emcRegMode, setEmcRegMode] = useState(emcSaved.regMode ?? "strict");\n  const [emcRegLen, setEmcRegLen] = useState(emcSaved.regLen ?? 36);\n  const [emcRegMult, setEmcRegMult] = useState(emcSaved.regMult ?? 0.618);\n  useEffect(() => {\n    try { localStorage.setItem(EMC_LS_KEY'),
    ('hedgeSynth: emcHedgeSynth }))',
     'hedgeSynth: emcHedgeSynth, regGate: emcRegGate, regMode: emcRegMode, regLen: emcRegLen, regMult: emcRegMult }))'),
    ('emcLeg, emcHedgeOn, emcHedgeMax, emcHedgeSynth]);',
     'emcLeg, emcHedgeOn, emcHedgeMax, emcHedgeSynth, emcRegGate, emcRegMode, emcRegLen, emcRegMult]);'),
    ('        sel_prem_min: Number(emcSelMin) || 0, sel_prem_max: Number(emcSelMax) || 0, sel_pick: emcSelPick,',
     '        // ── EMC_REGIME_20261028 ── keys only when the gate is on: saved configs stay byte-identical\n        ...(emcRegGate !== "off" ? { regime_gate: emcRegGate, regime_mode: emcRegMode,\n                                     regime_len: Number(emcRegLen) || 36, regime_mult: Number(emcRegMult) || 0.618 } : {}),\n        sel_prem_min: Number(emcSelMin) || 0, sel_prem_max: Number(emcSelMax) || 0, sel_pick: emcSelPick,'),
    ('emcLeg, emcHedgeOn, emcHedgeMax, emcHedgeSynth,   /*',
     'emcLeg, emcHedgeOn, emcHedgeMax, emcHedgeSynth, emcRegGate, emcRegMode, emcRegLen, emcRegMult,   /* ── EMC_REGIME_20261028 ── */   /*'),
    ('no arming ${emcSkipFrom}–${emcSkipTo}` : ""}',
     'no arming ${emcSkipFrom}–${emcSkipTo}` : ""}${emcRegGate !== "off" ? ` · VET regime SMA${emcRegLen} ± ATR×${emcRegMult} (${emcRegGate === "entry" ? "entry waits" : "at arming"}${emcRegMode === "loose" ? ", loose" : ""})` : ""}'),
    ('              <div style={{ fontSize: 11, fontWeight: 700, color: colors.text.muted, margin: "2px 0 6px", letterSpacing: 0.4 }}>CONTRACT SELECTION &amp; FILL</div>',
     '              {/* ── EMC_REGIME_20261028 ── */}\n              <div style={{ fontSize: 11, fontWeight: 700, color: colors.text.muted, margin: "2px 0 6px", letterSpacing: 0.4 }}>REGIME FILTER — VET_V1 SMA ± ATR CHANNEL (BACKTEST ONLY)</div>\n              <div style={{ display: "flex", gap: spacing.md, marginBottom: spacing.md, flexWrap: "wrap" }}>\n                <Field label="Regime gate">\n                  <select style={inputStyle} value={emcRegGate} onChange={(e) => setEmcRegGate(e.target.value)} title="VET_V1\'s regime channel on the SAME spot candles as the EMA cross: SMA(len) ± ATR(len) × mult. A candle inside the channel reads \'range\'; outside it reads UP (close ≥ SMA) or DOWN. off = the sealed strategy. at the arming cross = the cross candle itself must read UP (for an up cross) / DOWN (for a down cross), otherwise the cross arms nothing — it still exits an open position and replaces a pending setup. entry waits for it = the cross arms as usual and the entry ALSO waits until the latest closed spot candle reads the right side (like the option-EMA wait); the setup still dies at the opposite cross / cutoff / EOD. SELL follows the cross: an up cross (sell PE) needs UP.">\n                    <option value="off">off (sealed)</option><option value="arm">at the arming cross</option><option value="entry">entry waits for it</option>\n                  </select>\n                </Field>\n                <Field label="Strictness">\n                  <select style={inputStyle} value={emcRegMode} disabled={emcRegGate === "off"} onChange={(e) => setEmcRegMode(e.target.value)} title="strict = the candle must be OUTSIDE the channel on the trade\'s side (VET\'s own entry rule). loose = only the OPPOSITE side blocks: inside the channel is allowed.">\n                    <option value="strict">strict (outside, same side)</option><option value="loose">loose (not the opposite side)</option>\n                  </select>\n                </Field>\n                <Field label="SMA / ATR length"><input type="number" min="2" max="200" style={inputStyle} value={emcRegLen} disabled={emcRegGate === "off"} onChange={(e) => setEmcRegLen(Number(e.target.value))} title="Spot candles (of the spot timeframe above) for BOTH the SMA and the ATR — VET\'s \'Trend length\'. VET\'s sell seal: 36. Warmed on the 5 sessions before \'Date from\', continuous across days like the EMAs." /></Field>\n                <Field label="ATR multiplier"><input type="number" min="0.05" max="5" step="0.001" style={inputStyle} value={emcRegMult} disabled={emcRegGate === "off"} onChange={(e) => setEmcRegMult(Number(e.target.value))} title="Channel half-width = ATR × this — VET\'s range multiplier (0.618)." /></Field>\n              </div>\n              <div style={{ fontSize: 11, fontWeight: 700, color: colors.text.muted, margin: "2px 0 6px", letterSpacing: 0.4 }}>CONTRACT SELECTION &amp; FILL</div>'),
]

PARAMFMT_EDITS = [
    ('  if (cfg.arm_skip_from && cfg.arm_skip_to) p.push(',
     '  if (cfg.regime_gate && cfg.regime_gate !== "off") p.push(`reg${cfg.regime_gate === "entry" ? "E" : "A"}${cfg.regime_mode === "loose" ? "~" : ""}${cfg.regime_len ?? 36}/${cfg.regime_mult ?? 0.618}`);   // ── EMC_REGIME_20261028 ──\n  if (cfg.arm_skip_from && cfg.arm_skip_to) p.push('),
]

SWEEP_EDITS = [
    ('  { key: "emc_carry", label: "EMC carry to next day", strategies: [EMC],',
     '  // ── EMC_REGIME_20261028 ── VET regime gate; OFF clears every regime key\n  { key: "emc_regime", label: "EMC VET regime gate", strategies: [EMC],\n    hint: "OFF, ARM, ENTRY, ARM_LOOSE, ENTRY_LOOSE",\n    parse: (tok) => { const v = tok.trim().toUpperCase(); return ["OFF", "ARM", "ENTRY", "ARM_LOOSE", "ENTRY_LOOSE"].includes(v) ? { v } : { err: `"${tok}" must be OFF, ARM, ENTRY, ARM_LOOSE or ENTRY_LOOSE` }; },\n    apply: (c, v) => { if (v === "OFF") { delete c.regime_gate; delete c.regime_mode; delete c.regime_len; delete c.regime_mult; } else { c.regime_gate = v.startsWith("ARM") ? "arm" : "entry"; c.regime_mode = v.endsWith("_LOOSE") ? "loose" : "strict"; if (c.regime_len == null) c.regime_len = 36; if (c.regime_mult == null) c.regime_mult = 0.618; } },\n    fmt: (v) => (v === "OFF" ? "noReg" : `reg${v.startsWith("ARM") ? "A" : "E"}${v.endsWith("_LOOSE") ? "~" : ""}`) },\n  { key: "emc_regime_len", label: "EMC regime SMA/ATR length (needs the gate on)", strategies: [EMC],\n    hint: "30, 36, 40, 50",\n    parse: (tok) => { const n = Math.round(Number(tok)); return isFinite(n) && n >= 2 && n <= 200 ? { v: n } : { err: `"${tok}" must be 2..200` }; },\n    apply: (c, v) => { c.regime_len = v; }, fmt: (v) => `regL${v}` },\n  { key: "emc_regime_mult", label: "EMC regime ATR multiplier (needs the gate on)", strategies: [EMC],\n    hint: "0.382, 0.5, 0.618, 1",\n    parse: (tok) => { const n = Number(tok); return isFinite(n) && n > 0 && n <= 5 ? { v: n } : { err: `"${tok}" must be > 0 and <= 5` }; },\n    apply: (c, v) => { c.regime_mult = v; }, fmt: (v) => `regM${v}` },\n  { key: "emc_carry", label: "EMC carry to next day", strategies: [EMC],'),
]

TEST_SRC = '# backend/app/backtest/emc/test_emc_regime.py\n#\n# ── EMC_REGIME_20261028 ── VET_V1\'s SMA ± ATR regime channel as an optional\n# EMC_V1 gate (backtest only). Standalone:\n#   cd backend && PYTHONPATH=$PWD python3 app/backtest/emc/test_emc_regime.py\n# Part 1 SpotRegime vs vet_v1_engine.vet_states (the donor), bar for bar.\n# Part 2 scripted sessions through simulate_day (arm / entry, strict / loose,\n#        SELL, blackout precedence, OFF ignores a regime source).\n# Part 3 the runner on the replay suite\'s synthetic Black-76 corpus: OFF is\n#        byte-identical, every gated trade re-checked against vet_states\n#        computed independently on the runner\'s spot bars, guards, live\n#        refusal, Trade Replay channel overlay.\n\nfrom __future__ import annotations\n\nimport os\nimport random\nimport sys\nimport tempfile\nfrom datetime import date, datetime, timedelta\nfrom types import SimpleNamespace as NS\n\nHERE = os.path.dirname(os.path.abspath(__file__))\nBACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))\nsys.path.insert(0, HERE)\nsys.path.insert(0, BACKEND)\n\nfrom emc_v1_engine import (                       # noqa: E402\n    EmcBar, DayParams, simulate_day, resample_session, SpotRegime, spot_regimes, regime_agrees,\n    SESSION_OPEN_MIN)\n\nIST = 5 * 3600 + 30 * 60\nFAILS = []\nNPASS = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        NPASS[0] += 1\n    else:\n        FAILS.append(name)\n\n\ndef ds_for(d):\n    return int((datetime(d.year, d.month, d.day) - datetime(1970, 1, 1)).total_seconds()) - IST\n\n\nO = SESSION_OPEN_MIN\nM = lambda hh, mm: hh * 60 + mm            # noqa: E731\n\n# ═══════════════════════ 1. SpotRegime vs the VET donor ═══════════════════════\nprint("── SpotRegime vs vet_v1_engine.vet_states (donor parity) ──")\ntry:\n    from app.backtest.vet.vet_v1_engine import vet_states\n    VET = True\nexcept Exception as ex:   # pragma: no cover\n    VET = False\n    check("import vet_v1_engine (donor)", False, repr(ex))\n\n\ndef walk(n, seed, start=23000.0, vol=12.0):\n    random.seed(seed)\n    out, px = [], start\n    for i in range(n):\n        o = px\n        drift = 6.0 * (1 if (i // 150) % 2 == 0 else -1)      # alternating trends → all regimes\n        c = o + drift * random.random() + random.gauss(0, vol)\n        h = max(o, c) + abs(random.gauss(0, vol / 2))\n        lo = min(o, c) - abs(random.gauss(0, vol / 2))\n        out.append(EmcBar(1_700_000_000 + i * 300, o, h, lo, c, O + 4 + 5 * (i % 75)))\n        px = c\n    return out\n\n\nif VET:\n    for (ln, mu, seed) in ((36, 0.618, 1), (10, 1.0, 2), (40, 0.3, 3), (2, 0.618, 4)):\n        bars = walk(3000, seed)\n        st = vet_states(bars, trend_len=ln, range_len=mu)\n        rg = SpotRegime(ln, mu)\n        ok, first_bad, hist = True, None, {1: 0, 0: 0, -1: 0}\n        for i, b in enumerate(bars):\n            d = rg.update(b)\n            v = st[i]\n            same = (d == v.dir_trend and (rg.sma is None) == (v.sma_t is None)\n                    and (rg.atr is None) == (v.atr is None)\n                    and (v.ch_top is None or (abs(rg.top - v.ch_top) < 1e-9 and abs(rg.bot - v.ch_bot) < 1e-9)))\n            if v.valid:\n                hist[d] += 1\n            if not same and ok:\n                ok, first_bad = False, (i, d, v.dir_trend, rg.sma, v.sma_t)\n        check(f"SMA{ln} ± ATR×{mu:g}: dir / SMA / channel identical to VET on 3,000 bars "\n              f"(up {hist[1]} · range {hist[0]} · down {hist[-1]})",\n              ok and (ln == 2 or min(hist.values()) > 0), first_bad)\n    bars = walk(900, 9)\n    a, b_ = SpotRegime(36, 0.618), SpotRegime(36, 0.618)\n    whole = [a.update(x) for x in bars]\n    split = []\n    for j in range(0, 900, 75):                # 12 sessions of 75 bars, one spot_regimes call each\n        split += list(spot_regimes(bars[j:j + 75], b_).values())\n    check("continuous: session by session == one pass", whole == split)\n    check("dir is 0 until len bars exist (VET P7 suppression)", whole[:35] == [0] * 35)\ncheck("regime_agrees: strict needs the same side", regime_agrees(1, 1, False) and not regime_agrees(1, 0, False)\n      and not regime_agrees(1, -1, False) and regime_agrees(-1, -1, False) and not regime_agrees(-1, 0, False))\ncheck("regime_agrees: loose blocks only the opposite side", regime_agrees(1, 0, True) and regime_agrees(1, 1, True)\n      and not regime_agrees(1, -1, True) and regime_agrees(-1, 0, True) and not regime_agrees(-1, 1, True))\ntry:\n    SpotRegime(1, 0.618)\n    check("length < 2 is refused", False)\nexcept ValueError:\n    check("length < 2 is refused", True)\n\n# ═══════════════════════ 2. scripted sessions ═══════════════════════\nprint("── simulate_day: scripted sessions ──")\n\n\nclass FakeTrack:\n    def __init__(self, side, from_k=0, falling=False):\n        self.side, self.from_k, self.falling = side, from_k, falling\n\n    def at(self, k, intrabar=True):\n        on = k >= self.from_k\n        if self.falling:                       # SELL: the sold option\'s fast < slow\n            return (1.0, 2.0, 30) if on else (2.0, 1.0, 30)\n        if self.side == "CE":\n            return (2.0, 1.0, 30) if on else (1.0, 2.0, 30)\n        return (1.0, 2.0, 30) if on else (2.0, 1.0, 30)\n\n\ndef flat(v):\n    return {m: NS(open=v, high=v + 1, low=v - 1, close=v) for m in range(O, M(15, 30))}\n\n\ndef P(**kw):\n    base = dict(sel_lo=150, sel_hi=200, sel_pick="highest", direction="BOTH", intrabar=True, opt_min_bars=21,\n                fill="close", sl_pts=20, tp_pts=40, trail_on=False, trail_trigger=20, trail_gap=15,\n                xover_exit=True, block_min=M(14, 30), eod_min=M(15, 15), max_trades=0, max_losses=0)\n    base.update(kw)\n    return DayParams(**base)\n\n\ndef run(p, cross_at, regime_fn, tracks=None, chain=None):\n    chain = chain or {"CE1": ("CE", 23050, 180.0), "PE1": ("PE", 22950, 175.0)}\n    bars = {s: flat(v[2]) for s, v in chain.items()}\n    tracks = tracks or {"CE1": FakeTrack("CE"), "PE1": FakeTrack("PE")}\n\n    def quotes_at(k, side):\n        return [{"sym": s, "strike": v[1], "price": v[2]} for s, v in chain.items() if v[0] == side]\n\n    dg = {}\n    tr = simulate_day(p=p, cross_at=cross_at, quotes_at=quotes_at, bars_of=lambda s: bars.get(s, {}),\n                      track_of=lambda c: tracks[c["sym"]], net_of=lambda a, b: (b - a) * 65 - 40,\n                      spot_at=lambda k: 23000.0, diag=dg, regime_at=regime_fn)\n    return tr, dg\n\n\nconst = lambda v: (lambda k: v)            # noqa: E731\nX = M(10, 4)\n# arm strict\ntr, dg = run(P(regime_gate="arm"), {X: 1}, const(0))\ncheck("arm/strict: up cross INSIDE the channel arms nothing", not tr and dg.get("sig_dropped_regime") == 1\n      and dg.get("cross_regime_range") == 1 and not dg.get("setups"), dg)\ntr, dg = run(P(regime_gate="arm"), {X: 1}, const(-1))\ncheck("arm/strict: up cross in a DOWN regime arms nothing", not tr and dg.get("cross_regime_against") == 1, dg)\ntr, dg = run(P(regime_gate="arm"), {X: 1}, const(1))\ncheck("arm/strict: up cross in an UP regime buys the CE at once, regime stamped",\n      tr and tr[0].side == "CE" and tr[0].entry_min == X and tr[0].regime == 1 and dg.get("cross_regime_with") == 1, dg)\ntr, dg = run(P(regime_gate="arm"), {X: -1}, const(-1))\ncheck("arm/strict: down cross in a DOWN regime buys the PE", tr and tr[0].side == "PE" and tr[0].regime == -1, tr)\n# arm loose\ntr, dg = run(P(regime_gate="arm", regime_loose=True), {X: 1}, const(0))\ncheck("arm/loose: inside the channel passes", tr and tr[0].side == "CE" and tr[0].regime == 0, dg)\ntr, dg = run(P(regime_gate="arm", regime_loose=True), {X: 1}, const(-1))\ncheck("arm/loose: the opposite regime blocks", not tr and dg.get("sig_dropped_regime") == 1, dg)\n# arm: the regime is read at the cross candle only — a later change does not matter\ntr, dg = run(P(regime_gate="arm"), {X: 1}, lambda k: 1 if k <= X else 0,\n             tracks={"CE1": FakeTrack("CE", M(10, 20)), "PE1": FakeTrack("PE")})\ncheck("arm: once armed, the entry does not re-check the regime (enters 10:20 in the channel)",\n      tr and tr[0].entry_min == M(10, 20) and tr[0].regime == 0, tr)\n# entry strict\ntr, dg = run(P(regime_gate="entry"), {X: 1}, lambda k: 1 if k >= M(10, 39) else 0)\ncheck("entry/strict: arms in the channel, enters when the spot candle closes above it (10:39)",\n      tr and tr[0].entry_min == M(10, 39) and tr[0].regime == 1 and not tr[0].aligned_at_setup\n      and dg.get("setups_regime_wait") == 1 and dg.get("entries_after_regime_wait") == 1, (tr, dg))\ntr, dg = run(P(regime_gate="entry"), {X: 1}, const(0))\ncheck("entry/strict: the regime never agrees → no trade, counted unfilled at the cutoff",\n      not tr and dg.get("regime_wait_unfilled") == 1 and dg.get("setups_expired_cutoff") == 1, dg)\ntr, dg = run(P(regime_gate="entry"), {X: 1, M(11, 4): -1}, lambda k: 0 if k < M(11, 4) else -1)\ncheck("entry/strict: a waiting setup is replaced by the opposite cross; the new side enters with its regime",\n      tr and tr[0].side == "PE" and tr[0].entry_min == M(11, 4) and dg.get("setups_replaced") == 1\n      and dg.get("regime_wait_unfilled") == 1, (tr, dg))\ntr, dg = run(P(regime_gate="entry", block_min=M(15, 15)), {X: 1}, const(0))\ncheck("entry: unfilled at EOD when the cutoff is the session end",\n      not tr and dg.get("regime_wait_unfilled") == 1, dg)\ntr, dg = run(P(regime_gate="entry", regime_loose=True), {X: 1}, lambda k: -1 if k < M(10, 30) else 0)\ncheck("entry/loose: waits out the opposite regime, enters once inside the channel",\n      tr and tr[0].entry_min == M(10, 30) and tr[0].regime == 0, tr)\n# SELL: an up cross sells the PE and still needs the BULLISH regime\nsell_tracks = {"CE1": FakeTrack("CE", falling=True), "PE1": FakeTrack("PE", falling=True)}\ntr, dg = run(P(regime_gate="arm", sell=True, tp_pts=40), {X: 1}, const(1), tracks=sell_tracks)\ncheck("SELL + arm: up cross in an UP regime SELLS the PE", tr and tr[0].side == "PE" and tr[0].sell and tr[0].regime == 1, tr)\ntr, dg = run(P(regime_gate="arm", sell=True, tp_pts=40), {X: 1}, const(-1), tracks=sell_tracks)\ncheck("SELL + arm: up cross in a DOWN regime sells nothing", not tr and dg.get("sig_dropped_regime") == 1, dg)\n# precedence + OFF\ntr, dg = run(P(regime_gate="arm", skip_from=M(10, 0), skip_to=M(10, 30)), {X: 1}, const(0))\ncheck("blackout is checked first (counted as blackout, regime histogram still counted)",\n      dg.get("sig_dropped_blackout") == 1 and not dg.get("sig_dropped_regime") and dg.get("cross_regime_range") == 1, dg)\nt_off, d_off = run(P(), {X: 1, M(11, 4): -1, M(13, 9): 1}, const(-1))\nt_none = simulate_day(p=P(), cross_at={X: 1, M(11, 4): -1, M(13, 9): 1},\n                      quotes_at=lambda k, s: [{"sym": "CE1" if s == "CE" else "PE1",\n                                               "strike": 23050 if s == "CE" else 22950,\n                                               "price": 180.0 if s == "CE" else 175.0}],\n                      bars_of=lambda s: flat(180.0 if s == "CE1" else 175.0),\n                      track_of=lambda c: FakeTrack("CE" if c["sym"] == "CE1" else "PE"),\n                      net_of=lambda a, b: (b - a) * 65 - 40, spot_at=lambda k: 23000.0, diag={})\nsig = lambda T: [(t.sym, t.entry_min, t.exit_min, t.reason, t.regime) for t in T]   # noqa: E731\ncheck("gate OFF ignores a regime source entirely (same trades, regime None, no regime counters)",\n      sig(t_off) == sig(t_none) and all(t.regime is None for t in t_off)\n      and not any(k.startswith(("cross_regime", "sig_dropped_regime", "setups_regime")) for k in d_off), sig(t_off))\n\n# ═══════════════════════ 3. runner on a corpus ═══════════════════════\nprint("── runner: synthetic Black-76 corpus ──")\ntry:\n    from app.backtest.replay import replay_synth\n    from app.backtest.emc.backtest_emc_runner import run_emc_backtest, _merge_cfg, validate, DEFAULTS\n    from app.utils.market_hours import is_trading_day\n    APP = True\nexcept Exception as ex:   # pragma: no cover\n    APP = False\n    check("app tree available for the runner e2e", False, repr(ex))\n\nif APP and VET:\n    tmp = tempfile.mkdtemp(prefix="emc_regime_")\n    db = os.path.join(tmp, "backtest.db")\n    replay_synth.build(db, d0=date(2025, 1, 1), d1=date(2025, 2, 28), strikes_each_side=8, with_fut=False)\n    import sqlite3\n    con = sqlite3.connect(db)\n    con.row_factory = sqlite3.Row\n    D0, D1 = date(2025, 1, 15), date(2025, 2, 28)\n    base = {"sel_prem_min": 60, "sel_prem_max": 140, "fill_mode": "close"}\n\n    def go(**kw):\n        return run_emc_backtest(db_path=db, strategy_id="EMC_V1", underlying="NIFTY", date_from=D0, date_to=D1,\n                                config_override=dict(base, **kw))\n\n    def tsig(res):\n        return [(t.tradingsymbol, t.entry_ts, t.exit_ts, t.entry_price, t.exit_price, t.exit_reason, t.net_pnl,\n                 t.condition) for t in res["trades"]]\n\n    r0 = go()\n    r_off = go(regime_gate="off", regime_mode="loose", regime_len=20, regime_mult=1.5)\n    check(f"OFF is byte-identical to a config without the keys ({len(r0[\'trades\'])} trades)",\n          r0["trades"] and tsig(r0) == tsig(r_off) and r0["summary"]["net_pnl"] == r_off["summary"]["net_pnl"])\n    check("OFF: no ·reg tag, no regime counters", not any("·reg" in t.condition for t in r0["trades"])\n          and not r0["summary"]["diag_emc"].get("cross_regime_with")\n          and r0["summary"]["diag_emc"]["regime_entries"] == {"with": 0, "range": 0, "against": 0})\n    check("defaults are VET\'s sell seal (36 / 0.618), gate off",\n          DEFAULTS["regime_gate"] == "off" and DEFAULTS["regime_len"] == 36 and DEFAULTS["regime_mult"] == 0.618)\n\n    # independent regime: vet_states over the runner\'s exact spot bar sequence\n    def spot_1m(d):\n        ds = ds_for(d)\n        return [EmcBar(r["ts"], r["open"], r["high"], r["low"], r["close"]) for r in con.execute(\n            "SELECT ts, open, high, low, close FROM backtest_candles_1m WHERE underlying=\'NIFTY\' "\n            "AND instrument_type=\'SPOT\' AND ts>=? AND ts<? ORDER BY ts", (ds, ds + 86400))]\n\n    def vet_map(tf, ln, mu, warm=5):\n        wd, d = [], D0 - timedelta(days=1)\n        while len(wd) < warm:\n            if is_trading_day(d):\n                wd.append(d)\n            d -= timedelta(days=1)\n        days, d = list(reversed(wd)), D0\n        while d <= D1:\n            if is_trading_day(d):\n                days.append(d)\n            d += timedelta(days=1)\n        bars, keys = [], []\n        for dd in days:\n            for b in resample_session(spot_1m(dd), day_start_epoch=ds_for(dd), tf_minutes=tf):\n                bars.append(b)\n                keys.append((dd, b.end_min))\n        st = vet_states(bars, trend_len=ln, range_len=mu)\n        return {k: s.dir_trend for k, s in zip(keys, st)}\n\n    VM = vet_map(5, 36, 0.618)\n\n    def ist_day(ts):\n        return (datetime(1970, 1, 1) + timedelta(seconds=ts + IST)).date()\n\n    def latest(day, k):\n        ks = [m for (dd, m) in VM if dd == day and m <= k]\n        return VM[(day, max(ks))] if ks else None\n\n    def want_of(t):\n        bull = (t.instrument_type == "CE") != (t.direction == "SELL")\n        return 1 if bull else -1\n\n    def arm_min(t):\n        hh, mm = t.condition.split("·arm")[1][:5].split(":")\n        return int(hh) * 60 + int(mm)\n\n    for mode in ("strict", "loose"):\n        ra = go(regime_gate="arm", regime_mode=mode)\n        T, dg = ra["trades"], ra["summary"]["diag_emc"]\n        bad = [t.condition for t in T if not regime_agrees(want_of(t), VM.get((ist_day(t.entry_ts), arm_min(t))),\n                                                            mode == "loose")]\n        check(f"arm/{mode}: every trade\'s ARMING candle agrees with VET\'s dirTrend ({len(T)} trades, "\n              f"{dg.get(\'sig_dropped_regime\', 0)} crosses dropped)", T and not bad and (\n                  dg.get("sig_dropped_regime", 0) > 0 if mode == "strict" else\n                  dg.get("sig_dropped_regime", 0) <= dg.get("cross_regime_against", 0)), bad[:3])\n        check(f"arm/{mode}: regime histogram covers every cross",\n              dg.get("cross_regime_with", 0) + dg.get("cross_regime_range", 0) + dg.get("cross_regime_against", 0)\n              == dg.get("crosses_up", 0) + dg.get("crosses_dn", 0), dg)\n        check(f"arm/{mode}: trades carry ·reg tags and the regime split adds up",\n              all("·reg" in t.condition for t in T) and sum(dg["regime_entries"].values()) == len(T)\n              and abs(sum(dg["regime_net"].values()) - ra["summary"]["net_pnl"]) < 1.0, dg["regime_entries"])\n    for mode in ("strict", "loose"):\n        re_ = go(regime_gate="entry", regime_mode=mode)\n        T, dg = re_["trades"], re_["summary"]["diag_emc"]\n        bad = [t.condition for t in T\n               if not regime_agrees(want_of(t), latest(ist_day(t.entry_ts), (t.entry_ts - ds_for(ist_day(t.entry_ts))) // 60),\n                                    mode == "loose")]\n        check(f"entry/{mode}: at every entry the latest CLOSED spot candle agrees with VET ({len(T)} trades, "\n              f"{dg.get(\'entries_after_regime_wait\', 0)} after a regime wait)", T and not bad, bad[:3])\n        check(f"entry/{mode}: wait bookkeeping (waits = filled after wait + unfilled)",\n              dg.get("setups_regime_wait", 0) == dg.get("entries_after_regime_wait", 0) + dg.get("regime_wait_unfilled", 0),\n              dg)\n    r_strict = go(regime_gate="entry")\n    check("entry/strict never enters against or inside the channel",\n          r_strict["summary"]["diag_emc"]["regime_entries"]["range"] == 0\n          and r_strict["summary"]["diag_emc"]["regime_entries"]["against"] == 0)\n    rs = go(regime_gate="arm", leg_action="SELL", tp_pts=40)\n    bad = [t.condition for t in rs["trades"] if not regime_agrees(want_of(t), VM.get((ist_day(t.entry_ts), arm_min(t))), False)]\n    check(f"SELL + arm: short PE on up crosses / short CE on down crosses, regime by the CROSS ({len(rs[\'trades\'])} trades)",\n          rs["trades"] and not bad and all(t.direction == "SELL" for t in rs["trades"]), bad[:3])\n    VM2 = vet_map(5, 20, 1.0)\n    r2 = go(regime_gate="arm", regime_len=20, regime_mult=1.0)\n    bad = [t.condition for t in r2["trades"] if VM2.get((ist_day(t.entry_ts), arm_min(t))) != want_of(t)]\n    check(f"length / multiplier knobs are honoured (SMA20 ± ATR×1.0, {len(r2[\'trades\'])} trades)",\n          r2["trades"] and not bad, bad[:3])\n\n    print("── guards ──")\n    ab = lambda **kw: validate(_merge_cfg(dict(base, regime_gate="arm", **kw)))   # noqa: E731\n    check("regime_len 1 aborts", bool(ab(regime_len=1)))\n    check("regime_mult 0 aborts", bool(ab(regime_mult=0)))\n    check("too little warm-up for the length aborts (60m bars × 5 sessions < 36)", bool(ab(spot_tf=60)) and\n          "warmup" in ab(spot_tf=60))\n    check("the same 60m config with the gate OFF is fine", validate(_merge_cfg(dict(base, spot_tf=60))) is None)\n    check("unknown gate value normalises to off", _merge_cfg({"regime_gate": "maybe"})["regime_gate"] == "off")\n    check("aborted runs return no trades", go(regime_gate="arm", regime_len=500).get("aborted") is True)\n    try:\n        from app.engine.emc.emc_live_core import live_problems\n        check("paper/live refuse a regime gate", bool(live_problems({"regime_gate": "arm"}))\n              and live_problems({"regime_gate": "off"}) is None and live_problems({}) is None)\n    except Exception as ex:   # pragma: no cover\n        check("paper/live refusal (import)", False, repr(ex))\n\n    print("── replay: channel overlay ──")\n    from pathlib import Path\n    import app.backtest.repo.backtest_repo as BR\n    import app.backtest.replay.replay_core as RC\n    BR._db_path = lambda: Path(db)\n    RC.runs_db_path = lambda: db\n\n    def replay_rows(res):\n        res["meta"] = {"strategy_id": "EMC_V1", "underlying": "NIFTY", "date_from": D0.isoformat(),\n                       "date_to": D1.isoformat()}\n        rid = BR.persist_run(res)\n        rows = [dict(r) for r in con.execute("SELECT * FROM backtest_trades WHERE run_id=? ORDER BY entry_ts, id",\n                                             (rid,))]\n        return rid, rows\n\n    rid, rows = replay_rows(go(regime_gate="arm"))\n    ok_band = ok_arm = ok_tag = True\n    n = 0\n    for r in rows[:8]:\n        out = RC.build_replay(rid, int(r["id"]))\n        if "spot" not in out:\n            ok_band = False\n            continue\n        n += 1\n        sp = {o.get("id"): o for o in out["spot"]["overlays"]}\n        band = sp.get("regime_channel") or {}\n        if not (band.get("type") == "band" and band.get("upper") and band.get("lower") and sp.get("regime_sma")):\n            ok_band = False\n        if not sp.get("armed", {}).get("points"):\n            ok_arm = False\n        if not any("regime" in p[2] for p in sp.get("crosses", {}).get("points", [])):\n            ok_tag = False\n    check(f"replay draws VET\'s channel band + SMA on the spot pane ({n} trades)", n and ok_band)\n    check("replay still finds the ARMING cross with the gate on", ok_arm)\n    check("replay tags each cross with its regime", ok_tag)\n    rid0, rows0 = replay_rows(go())\n    out0 = RC.build_replay(rid0, int(rows0[0]["id"]))\n    check("replay with the gate OFF draws no channel",\n          "regime_channel" not in {o.get("id") for o in out0["spot"]["overlays"]})\n    con.close()\n\nif FAILS:\n    print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n    sys.exit(1)\nprint(f"\\nALL {NPASS[0]} EMC REGIME CHECKS PASSED")\n'


TARGETS = [
    # (rel, edits, parent markers, mirrored)
    ("backend/app/backtest/emc/emc_v1_engine.py", ENGINE_EDITS, [PARENT, "EMC_CARRY_20261012"], True),
    ("backend/app/backtest/emc/backtest_emc_runner.py", RUNNER_EDITS, [PARENT], True),
    ("backend/app/engine/emc/emc_live_core.py", LIVE_EDITS, [PARENT], True),
    ("backend/app/backtest/replay/overlays.py", OVERLAY_EDITS, [PARENT], True),
    ("frontend/src/pages/Backtest.jsx", BACKTEST_EDITS, [PARENT], False),
    ("frontend/src/pages/backtest/paramFormat.js", PARAMFMT_EDITS, [PARENT], False),
    ("frontend/src/pages/backtest/SweepBuilder.jsx", SWEEP_EDITS, [PARENT], False),
]
SUITES = ("app/backtest/emc/test_emc_regime.py",
          "app/backtest/emc/test_emc_runner_sim.py",
          "app/engine/emc/test_emc_live.py")


def die(msg, code=2):
    print("\nABORTED — nothing written.\n  " + msg)
    sys.exit(code)


def find_root(arg):
    for c in ([Path(arg)] if arg else []) + [Path.cwd(), Path(__file__).resolve().parent]:
        if (c / "backend/app/backtest/emc/backtest_emc_runner.py").exists():
            return c.resolve()
    die("Could not find the repo root. Run from the repo root or pass --repo PATH.")


def dests(root, rel, mirrored):
    out = [root / rel]
    if mirrored and rel.startswith("backend/"):
        m = root / MIRROR / Path(rel).relative_to("backend")
        if m.exists() or (rel == TEST_REL and m.parent.exists()):
            out.append(m)
    return out


def git_dirty(root, rel):
    if not (root / ".git").exists() or not shutil.which("git"):
        return False
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", rel],
                       capture_output=True, text=True)
    return any(l[:2].strip() and not l.startswith("??") for l in r.stdout.splitlines())


def patch(text, edits, label):
    for i, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            die(f"{label}: anchor #{i} found {n}x (expected 1) — not the version this script "
                f"targets. Push it to GitHub and ask for a rebase.\n  anchor: "
                f"{old.strip().splitlines()[0][:90]}")
        text = text.replace(old, new, 1)
    return text


def run_suites(root, staged):
    tmp = Path(tempfile.mkdtemp(prefix="emc_regime_suite_"))
    try:
        ign = shutil.ignore_patterns("__pycache__", "*.bak-*", "*.pyc", "*.tmp-*")   # NOT "data": app/backtest/data is code
        shutil.copytree(root / "backend/app", tmp / "backend/app", ignore=ign)
        for rel, txt in staged.items():
            if rel.startswith("backend/"):
                (tmp / rel).write_text(txt, encoding="utf-8")
        results = []
        for t in SUITES:
            home = Path(tempfile.mkdtemp(prefix="home_", dir=tmp))
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       SCALP_TEST_MODE="1", PYTHONPATH=str(tmp / "backend"))
            try:
                r = subprocess.run([sys.executable, t], cwd=tmp / "backend", env=env,
                                   capture_output=True, text=True, timeout=1500)
                results.append((t, r.returncode == 0, (r.stdout + r.stderr).strip()))
            except subprocess.TimeoutExpired:
                results.append((t, False, "timed out"))
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def jsx_check(root, rel, txt):
    fe = root / "frontend"
    eb = fe / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if not eb.exists():
        return None, "esbuild not installed in frontend/ — parse check skipped (the build will catch it)"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / Path(rel).name
        f.write_text(txt, encoding="utf-8")
        r = subprocess.run([str(eb), str(f), "--loader:.jsx=jsx", "--loader:.js=jsx", "--log-level=error",
                            "--outfile=" + str(Path(td) / "o.js")], capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or "esbuild parse OK").strip()


def revert(root):
    n = 0
    for rel, _e, _p, mirrored in TARGETS:
        for p in dests(root, rel, mirrored):
            b = p.with_name(p.name + BAK)
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
                n += 1
                print(f"  restored  {p.relative_to(root)}")
    for p in dests(root, TEST_REL, True):
        if p.exists() and FENCE in p.read_text(encoding="utf-8", errors="ignore"):
            p.unlink()
            print(f"  removed   {p.relative_to(root)}")
    print(f"\nRevert done ({n} file(s) restored). Rebuild: ./desktop/build-scalp.sh both")
    return 0


def main():
    ap = argparse.ArgumentParser(description=FENCE)
    ap.add_argument("--repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-live-suite", action="store_true",
                    help="skip test_emc_live.py (needs fastapi in this Python)")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    root = find_root(a.repo)
    print(f"{FENCE}\nRepo: {root}")
    if a.revert:
        return revert(root)
    global SUITES
    if a.skip_live_suite:
        SUITES = tuple(s for s in SUITES if "test_emc_live" not in s)

    missing = [rel for rel, *_ in TARGETS if not (root / rel).exists()]
    if missing:
        die("missing files: " + ", ".join(missing))
    files = [p for rel, _e, _pa, mi in TARGETS for p in dests(root, rel, mi) if p.exists()]
    fenced = [p for p in files if FENCE in p.read_text(encoding="utf-8")]
    if fenced and len(fenced) == len(files):
        print("Already applied — nothing to do.")
        return 0
    if fenced:
        die("mixed state — already fenced:\n  " + "\n  ".join(
            str(p.relative_to(root)) for p in fenced) + "\n  Run --revert first.")

    staged = {}
    for rel, edits, parents, _mi in TARGETS:
        if git_dirty(root, rel) and not a.allow_dirty:
            die(f"{rel} has uncommitted modifications (git status). Commit/stash first, "
                f"or re-run with --allow-dirty if that is intended.")
        src = (root / rel).read_text(encoding="utf-8")
        for pf in parents:
            if pf not in src:
                die(f"{rel}: prerequisite marker '{pf}' not found — apply_emc_sell.py / "
                    f"apply_emc_carry.py must be applied first.")
        staged[rel] = patch(src, edits, rel)
    staged[TEST_REL] = TEST_SRC

    with tempfile.TemporaryDirectory() as td:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                t = Path(td) / (rel.replace("/", "_"))
                t.write_text(txt, encoding="utf-8")
                try:
                    py_compile.compile(str(t), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    print("  py_compile: OK")
    for rel in ("frontend/src/pages/Backtest.jsx", "frontend/src/pages/backtest/paramFormat.js",
                "frontend/src/pages/backtest/SweepBuilder.jsx"):
        ok, msg = jsx_check(root, rel, staged[rel])
        print(f"  {Path(rel).name}: {msg}")
        if ok is False:
            die(f"{rel} does not parse after the edit.")

    print("  running suites (a few minutes)…")
    bad = False
    for name, passed, out in run_suites(root, staged):
        summ = next((l for l in reversed(out.splitlines()) if "CHECKS" in l or "checks" in l), "")
        print(f"    {'PASS' if passed else 'FAIL'}  {name}: {summ}")
        if not passed:
            bad = True
            print("      " + "\n      ".join(out.splitlines()[-25:]))
    if bad:
        die("a suite failed (see above). If it is only test_emc_live.py failing on a missing "
            "fastapi import, re-run with --skip-live-suite.")

    plan = []
    for rel, txt in staged.items():
        mirrored = next((t[3] for t in TARGETS if t[0] == rel), True)
        for d in dests(root, rel, mirrored):
            plan.append((d, txt))
    if a.dry_run:
        for d, _ in plan:
            print(f"  would write  {d.relative_to(root)}")
        print("\nDry run OK — nothing written.")
        return 0

    backups = []
    try:
        for d, _ in plan:
            if d.exists():
                b = d.with_name(d.name + BAK)
                shutil.copy2(d, b)
                backups.append((d, b))
        for d, txt in plan:
            d.parent.mkdir(parents=True, exist_ok=True)
            tmpf = d.with_name(d.name + ".tmp-" + FENCE)
            tmpf.write_text(txt, encoding="utf-8")
            os.replace(tmpf, d)
            print(f"  wrote  {d.relative_to(root)}")
    except Exception as e:
        for d, b in backups:
            shutil.copy2(b, d)
        for d, _ in plan:
            if d not in [x for x, _ in backups] and d.exists():
                d.unlink()
        die(f"write failed ({e!r}) — every file restored.")

    print("""
Done. Next:
  1. ./desktop/build-scalp.sh both
  2. Backtest → EMC_V1 → new section "REGIME FILTER — VET_V1 SMA ± ATR CHANNEL":
     Regime gate (off / at the arming cross / entry waits for it), Strictness,
     SMA / ATR length (36), ATR multiplier (0.618).
  3. Read the run diag before the P&L: cross_regime_with / _range / _against,
     sig_dropped_regime, entries_after_regime_wait, regime_wait_unfilled.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
