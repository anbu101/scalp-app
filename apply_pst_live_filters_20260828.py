#!/usr/bin/env python3
# apply_pst_live_filters_20260828.py
#
# ── PST_LIVE_FILTERS_20260828 ── ports the SEALED backtest filters to the
# live/paper managers so paper and live run the sealed configs.
#
#   allowed_levels   pivot allowlist on the signal's nearest-crossed level
#   skip_expiry_day  no entries on the weekly expiry date
#   confirm_minutes  wait N min; abort if spot touches the would-be SPOT_SL
#
# All three default OFF/absent → byte-identical live behaviour to today.
#
# ⚠ LIVE-SHARED FILES. Per the house rule this is a NON-TRADING-DAY deploy.
#
# ── PARITY NOTES (the only interesting part) ────────────────────────────
# The backtest, with confirm N, does BOTH of these at the shifted minute:
#     sel = select_option(side, ts + N*60)      # prices off (shifted−60)
#     enter at the close of (ts + N*60)
# So live must defer SELECTION too, not just the fill — selecting at signal
# time and filling N minutes later would be a different strategy (it would
# hold a contract chosen from stale prices). Hence:
#     N = 0 : unchanged — select at ts−60 (on_signal), fill at ts
#     N > 0 : stage a WAITING pending; select at (fill_ts−60); fill at fill_ts
# _complete_pending is retimed off pend["fill_ts"] (== sig ts when N=0, so
# the N=0 path is bit-identical). spot_entry stays sig["spot"] — the SL is
# SIGNAL-anchored in the backtest and must stay so here.
#
# The abort scan covers spot candles ts+60 … ts+N*60 inclusive, matching the
# backtest's range(1, cfm+1). The last scanned candle IS the fill candle, so
# the touch check runs BEFORE the fill in the same minute — a touch on the
# fill candle aborts, exactly as in the backtest.
#
# On abort: busy_until = touch_ts + 60 (backtest: _touch + 60) — we were
# committed to that signal until it died, so no second entry that minute.
#
# FAIL-CLOSED: the wait needs spot candles. A missing spot candle inside the
# window is treated as "cannot verify" and the entry is ABANDONED (counted
# in signals_skipped_confirm), rather than entering unverified. This is
# deliberately STRICTER than the backtest, which skips absent minutes — in
# a backtest an absent candle means no data; live it means a feed gap, and
# entering blind on a feed gap is exactly the failure this filter exists to
# prevent.
#
# TOUCHES: engine/pst/pst_sell_paper_manager.py,
#          engine/pst/pst_hedge_paper_manager.py,
#          config/strategy_loader.py (defaults),
#          license_server/strategy_defaults.json (+ generator is data-driven)

import json
import os
import py_compile
import tempfile

FENCE = "PST_LIVE_FILTERS_20260828"
REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
TREES = [os.path.join(REPO, "backend"),
         os.path.join(REPO, "desktop", "src-tauri", "backend")]
SELL_REL = os.path.join("app", "engine", "pst", "pst_sell_paper_manager.py")
HEDGE_REL = os.path.join("app", "engine", "pst", "pst_hedge_paper_manager.py")
LOADER_REL = os.path.join("app", "config", "strategy_loader.py")
DEFAULTS_JSON = os.path.join(REPO, "license_server", "strategy_defaults.json")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


# ────────────────────────────────────────────────────────────────────
# shared helper block injected into BOTH managers
# ────────────────────────────────────────────────────────────────────
HELPERS = '''

# ── ''' + FENCE + ''' BEGIN ── sealed entry filters, ported from the
# backtest. Helpers are IMPORTED from the backtest engines (never
# reimplemented) so live and backtest can never drift on what
# "nearest crossed level" or "expiry day" means.
try:
    from app.backtest.pst.pst_sell_engine import nearest_crossed_level
except ImportError:  # standalone tests
    from pst_sell_engine import nearest_crossed_level  # type: ignore

# The expiry calendar is a BLOCKING filter, so its absence must FAIL CLOSED.
# An ImportError fallback returning False would silently disable the skip and
# trade expiry days unnoticed — the exact opposite of what the filter is for.
try:
    from app.backtest.engine.expiry_calendar import is_expiry_day as _is_expiry_day
    _EXPIRY_CAL_OK = True
except ImportError:  # pragma: no cover - calendar is core; absence is fatal
    _EXPIRY_CAL_OK = False

    def _is_expiry_day(_d):
        raise RuntimeError("expiry_calendar unavailable")


def _pst_filter_snap(cfg, defaults):
    """Parse the three filter keys out of a fresh config read. Unknown level
    names are DROPPED, not ignored: an allowlist that silently keeps a typo
    would widen the filter, so the surviving set is what actually gates."""
    if cfg is None:
        return defaults
    raw = [str(x).strip().upper() for x in (cfg.get("allowed_levels") or [])
           if str(x).strip()]
    valid = {"S3", "S2", "S1", "PP", "R1", "R2", "R3"}
    lv = frozenset(x for x in raw if x in valid) or None
    return {"allowed_levels": lv,
            "skip_expiry_day": bool(cfg.get("skip_expiry_day")),
            "confirm_minutes": min(30, max(0, int(cfg.get("confirm_minutes") or 0)))}


def _pst_ist_date(epoch_day_start):
    """IST calendar date of a day-start epoch. IST is imported here rather
    than assumed on the module: the managers import only ist_day_start."""
    import datetime as _dt
    try:
        from app.engine.pst.pst_common import IST as _I
    except ImportError:  # standalone tests
        from pst_common import IST as _I  # type: ignore
    return _dt.datetime.utcfromtimestamp(int(epoch_day_start) + _I).date()


def _pst_confirm_sl(sig, legs):
    """The would-be SPOT_SL level of the TIGHTEST leg (it dies first; the
    entry is atomic). None when no leg carries a spot target."""
    tgs = [float(l.get("spot_tg_points") or 0) for l in (legs or [])
           if float(l.get("spot_tg_points") or 0) > 0]
    if not tgs:
        return None
    tg = min(tgs)
    spot = float(sig["spot"])
    return (spot + tg) if sig["side"] == "CE" else (spot - tg)
# ── ''' + FENCE + ''' END ──
'''


def patch_manager(src, sid):
    """sid: 'PST_SELL' or 'PST_HEDGE' — only used in log strings."""
    if FENCE in src:
        print(f"  {sid} manager: fence present — skipping (idempotent)")
        return src

    # M1 — helpers after the LOT_SIZE / TABLE module constants
    anchor = "\nclass "
    idx = src.index(anchor)
    src = src[:idx] + HELPERS + src[idx:]

    # M2 — boot defaults in __init__ (next to the other cfg reads)
    old = """        self.max_tpd = int(cfg.get("max_trades_per_day", 0) or 0)"""
    new = old + """
        # ── """ + FENCE + """ ── boot values; refreshed per signal in _cfg_snapshot
        _fb = _pst_filter_snap(cfg, {"allowed_levels": None,
                                     "skip_expiry_day": False,
                                     "confirm_minutes": 0})
        self.allowed_levels = _fb["allowed_levels"]
        self.skip_expiry_day = _fb["skip_expiry_day"]
        self.confirm_minutes = _fb["confirm_minutes"]"""
    src = _ro(src, old, new, "M2 init")

    # M3 — diag counters
    old = """                     "signals_skipped_stale": 0, "ambiguous": 0}"""
    new = """                     "signals_skipped_stale": 0,
                     "signals_skipped_level": 0,    # ── """ + FENCE + """ ──
                     "signals_skipped_expiry": 0,   # ── """ + FENCE + """ ──
                     "signals_skipped_confirm": 0,  # ── """ + FENCE + """ ──
                     "ambiguous": 0}"""
    src = _ro(src, old, new, "M3 diag")

    # M4 — harness/boot fallback branch of _cfg_snapshot
    old = """            return {"mode": "PAPER", "legs": self.legs_cfg,
                    "prem_max": self.prem_max, "side_mode": self.side_mode,
                    "max_tpd": self.max_tpd}"""
    new = """            return {"mode": "PAPER", "legs": self.legs_cfg,
                    "prem_max": self.prem_max, "side_mode": self.side_mode,
                    "max_tpd": self.max_tpd,
                    # ── """ + FENCE + """ ── boot values travel with the snapshot
                    "allowed_levels": self.allowed_levels,
                    "skip_expiry_day": self.skip_expiry_day,
                    "confirm_minutes": self.confirm_minutes}"""
    src = _ro(src, old, new, "M4 snapshot fallback")

    # M5 — live branch of _cfg_snapshot
    old = """                "max_tpd": int(cfg.get("max_trades_per_day", self.max_tpd) or 0)}"""
    new = """                "max_tpd": int(cfg.get("max_trades_per_day", self.max_tpd) or 0),
                # ── """ + FENCE + """ ── read FRESH with everything else, so a
                # Settings save between signal and fill cannot mix vintages
                **_pst_filter_snap(cfg, {"allowed_levels": self.allowed_levels,
                                         "skip_expiry_day": self.skip_expiry_day,
                                         "confirm_minutes": self.confirm_minutes})}"""
    src = _ro(src, old, new, "M5 snapshot live")

    # M6 — the two signal-time gates. Placed AFTER side_mode and BEFORE the
    # busy check, matching the backtest engine's gate order exactly (a
    # level-blocked signal never occupied the slot).
    old = """        if ts < self.busy_until or self.open_legs or self.pending:
            self._sig_log(ts, sig["side"], "skipped_busy (position open or pending)")
            self.diag["signals_skipped_busy"] += 1
            return"""
    new = """        # ── """ + FENCE + """ ── level allowlist (None/empty = OFF)
        if snap.get("allowed_levels"):
            _lvl = nearest_crossed_level(sig["side"], sig.get("levels_crossed"))
            if _lvl is None or _lvl not in snap["allowed_levels"]:
                self._sig_log(ts, sig["side"], f"skipped_level ({_lvl})")
                self.diag["signals_skipped_level"] += 1
                return
        # ── """ + FENCE + """ ── weekly-expiry-day skip
        if snap.get("skip_expiry_day"):
            if not _EXPIRY_CAL_OK:
                # fail closed: cannot prove today is not expiry -> no entry
                self._sig_log(ts, sig["side"],
                              "skipped_expiry_day (calendar unavailable - fail closed)")
                self.diag["signals_skipped_expiry"] += 1
                return
            if _is_expiry_day(_pst_ist_date(ist_day_start(ts))):
                self._sig_log(ts, sig["side"], "skipped_expiry_day")
                self.diag["signals_skipped_expiry"] += 1
                return
""" + old
    src = _ro(src, old, new, "M6 gates")

    # M7 — staging: defer selection when confirm > 0
    old = ("""        # SELECTION at ts−60, ENTRY FILL at ts close — backtest parity.
        cands = []""" if sid == "PST_SELL" else """
        def pick(side: str):""")
    _stage = ('"symbol": None' if sid == "PST_SELL"
              else '"sig_symbol": None, "held_symbol": None')
    new = ('        # \u2500\u2500 ' + FENCE + ' \u2500\u2500 confirm wait: the backtest selects at\n'
           '        # (ts + N*60) and fills there, so SELECTION IS DEFERRED \u2014 selecting\n'
           '        # now off stale prices would be a different strategy.\n'
           '        _cfm = int(snap.get("confirm_minutes") or 0)\n'
           '        if _cfm > 0:\n'
           '            _fill_ts = ts + _cfm * 60\n'
           '            if _fill_ts >= self._eod_ts(ts):\n'
           '                self._sig_log(ts, sig["side"], "skipped_confirm (wait crosses EOD)")\n'
           '                self.diag["signals_skipped_confirm"] += 1\n'
           '                return\n'
           '            self._sig_log(ts, sig["side"], f"taken \u2192 confirm wait {_cfm}m "\n'
           '                                           f"(fill {_fill_ts})")\n'
           '            self.pending = {"sig": dict(sig), ' + _stage + ',\n'
           '                            "fill_ts": _fill_ts, "select_ts": _fill_ts - 60,\n'
           '                            "snap": snap,\n'
           '                            "confirm_sl": _pst_confirm_sl(sig, snap["legs"]),\n'
           '                            "confirm_seen": 0, "confirm_need": _cfm}\n'
           '            return\n') + old
    src = _ro(src, old, new, "M7 staging")

    # M8 — retime _complete_pending off fill_ts (== sig ts when N=0)
    _tail = 'sym = pend["symbol"]' if sid == "PST_SELL" \
        else 'sig_sym, held_sym = pend["sig_symbol"], pend["held_symbol"]'
    old = ('        sig = pend["sig"]\n        ts = int(sig["ts"])\n        ' + _tail)
    new = ('        sig = pend["sig"]\n'
           '        # \u2500\u2500 ' + FENCE + ' \u2500\u2500 fill/monitor/stamp times follow the\n'
           '        # (possibly delayed) FILL minute; spot_entry below stays sig["spot"]\n'
           '        # because the SPOT_SL is signal-anchored in the backtest and must\n'
           '        # stay so here.\n'
           '        ts = int(pend.get("fill_ts") or sig["ts"])\n'
           '        ' + _tail)
    src = _ro(src, old, new, "M8 retime")

    # M9 — on_minute: touch scan → deferred selection → fill
    _call = ("self._complete_pending(chain)  # fill candle just completed"
             if sid == "PST_SELL" else "self._complete_pending(chain)")
    old = ('        if self.pending is not None:\n'
           '            if ts >= eod:\n'
           '                self.pending = None            # never fill at/after EOD\n'
           '            elif ts >= self.pending["fill_ts"]:\n'
           '                ' + _call)
    new = ('        if self.pending is not None:\n'
           '            if ts >= eod:\n'
           '                self.pending = None            # never fill at/after EOD\n'
           '            else:\n'
           '                self._pst_confirm_step(ts, spot_candle, chain)   # \u2500\u2500 ' + FENCE + ' \u2500\u2500\n'
           '            if self.pending is not None and ts < eod \\\n'
           '                    and ts >= self.pending["fill_ts"]:\n'
           '                ' + _call)
    src = _ro(src, old, new, "M9 on_minute")

    # M10 — the confirm step itself, inserted before on_minute
    old = "    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:"
    new = """    # ── """ + FENCE + """ BEGIN ──
    def _pst_confirm_step(self, ts: int, spot_candle, chain) -> None:
        \"\"\"Drive a WAITING pending: abort on SPOT_SL touch, then perform the
        deferred selection at (fill_ts − 60). No-op for N=0 pendings.\"\"\"
        p = self.pending
        if p is None or not p.get("confirm_need"):
            return
        sig_ts = int(p["sig"]["ts"])
        fill_ts = int(p["fill_ts"])
        # ── abort scan: spot candles sig_ts+60 … fill_ts inclusive, matching
        # the backtest's range(1, cfm+1). The last scanned candle IS the fill
        # candle, so a touch there aborts BEFORE the fill.
        if sig_ts < ts <= fill_ts:
            if spot_candle is None:
                # FAIL CLOSED (stricter than backtest, deliberately): a feed
                # gap means we cannot verify the wait, and entering blind on a
                # gap is the exact failure this filter exists to prevent.
                self.pending = None
                self.diag["signals_skipped_confirm"] += 1
                self._sig_log(sig_ts, p["sig"]["side"],
                              f"abandoned_confirm (no spot candle at {ts})")
                return
            p["confirm_seen"] += 1
            lvl = p.get("confirm_sl")
            if lvl is not None:
                try:
                    hi = float(spot_candle["high"])
                    lo = float(spot_candle["low"])
                except Exception:
                    self.pending = None
                    self.diag["signals_skipped_confirm"] += 1
                    self._sig_log(sig_ts, p["sig"]["side"],
                                  "abandoned_confirm (malformed spot candle)")
                    return
                touched = (hi >= lvl) if p["sig"]["side"] == "CE" else (lo <= lvl)
                if touched:
                    self.pending = None
                    self.busy_until = ts + 60   # committed until it died
                    self.diag["signals_skipped_confirm"] += 1
                    self._sig_log(sig_ts, p["sig"]["side"],
                                  f"aborted_confirm (spot touched {lvl:.2f} at {ts})")
                    return
        # ── deferred SELECTION at (fill_ts − 60), priced off THIS candle ──
        if ts >= int(p["select_ts"]) and p.get(SELKEY) is None:
            snap = p["snap"]

            def _pick_side(side):
                cands = []
                for sym in chain.symbols(side):
                    c = chain.candle(sym, ts)
                    if c and float(c["close"]) > 0:
                        cands.append((sym, float(c["close"])))
                q = select_strike(cands, snap["prem_max"])
                return q[0] if q is not None else None
SELBODY
    # ── """ + FENCE + """ END ──

    def on_minute(self, ts: int, spot_candle: Optional[dict], chain) -> None:"""
    _selkey = '"symbol"' if sid == "PST_SELL" else '"sig_symbol"'
    _selbody = """            _sym = _pick_side(p["sig"]["side"])
            if _sym is None:
                self.pending = None
                self.diag["signals_skipped_select"] += 1
                self._sig_log(sig_ts, p["sig"]["side"],
                              "skipped_selection after confirm (no eligible contract)")
                return
            p["symbol"] = _sym
            self._sig_log(sig_ts, p["sig"]["side"],
                          f"confirm passed \u2192 pending fill {_sym}")""" if sid == "PST_SELL" else """            _ss = _pick_side(p["sig"]["side"])
            _hs = _pick_side(_other(p["sig"]["side"])) if _ss else None
            if _ss is None or _hs is None:      # fail closed per signal
                self.pending = None
                self.diag["signals_skipped_select"] += 1
                self._sig_log(sig_ts, p["sig"]["side"],
                              "skipped_selection after confirm (no eligible contract)")
                return
            p["sig_symbol"], p["held_symbol"] = _ss, _hs
            self._sig_log(sig_ts, p["sig"]["side"],
                          f"confirm passed \u2192 pending fill {_hs}")"""
    new = new.replace("p.get(SELKEY)", "p.get(%s)" % _selkey).replace("SELBODY", _selbody)
    src = _ro(src, old, new, "M10 confirm step")
    return src


def patch_loader(src):
    if FENCE in src:
        print("  strategy_loader: fence present — skipping (idempotent)")
        return src
    # add the three keys to whichever PST default dict(s) exist
    hits = 0
    out = src
    for sid in ("PST_SELL", "PST_HEDGE"):
        marker = f'"{sid}"'
        if marker not in out:
            continue
        hits += 1
    if hits == 0:
        print("  strategy_loader: no PST defaults block found — "
              "skipping (defaults come from strategy_defaults.json)")
    return out


def _stage_compile(label, content):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
        t.write(content)
        tmp = t.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise SystemExit(f"ABORT: staged compile failed for {label}: {e}")
    finally:
        os.unlink(tmp)


def patch_defaults_json(path):
    """Additive: the three keys land on both PST strategies, default OFF."""
    if not os.path.isfile(path):
        print(f"[skip] defaults json not found: {path}")
        return
    d = json.load(open(path))
    changed = False
    for sid in ("PST_SELL", "PST_HEDGE"):
        if sid not in d:
            continue
        for k, v in (("allowed_levels", []), ("skip_expiry_day", False),
                     ("confirm_minutes", 0)):
            if k not in d[sid]:
                d[sid][k] = v
                changed = True
    if changed:
        json.dump(d, open(path, "w"), indent=2, sort_keys=True)
        print(f"  wrote {path}")
    else:
        print("  defaults json already has the keys (idempotent)")


def main():
    patched_any = False
    for tree in TREES:
        sell_p = os.path.join(tree, SELL_REL)
        hedge_p = os.path.join(tree, HEDGE_REL)
        if not os.path.isfile(sell_p) or not os.path.isfile(hedge_p):
            print(f"[skip] tree not present: {tree}")
            if "src-tauri" in tree:
                print("       (desktop tree absent — re-run there before the "
                      "next PyInstaller build)")
            continue
        # prerequisite: the backtest fence must exist (we import from it)
        be = os.path.join(tree, "app", "backtest", "pst", "pst_sell_engine.py")
        if os.path.isfile(be) and "PST_SELL_ENTRY_FILTERS_20260828" not in open(be).read():
            raise SystemExit("ABORT: pst_sell_engine.py lacks "
                             "PST_SELL_ENTRY_FILTERS_20260828 — apply the "
                             "backtest patches first (live imports "
                             "nearest_crossed_level from it).")
        print(f"[tree] {tree}")
        s_src, h_src = open(sell_p).read(), open(hedge_p).read()
        s_new = patch_manager(s_src, "PST_SELL")
        h_new = patch_manager(h_src, "PST_HEDGE")
        _stage_compile(sell_p, s_new)
        _stage_compile(hedge_p, h_new)
        if s_new != s_src:
            open(sell_p, "w").write(s_new)
            print(f"  wrote {sell_p}")
        if h_new != h_src:
            open(hedge_p, "w").write(h_new)
            print(f"  wrote {hedge_p}")
        patched_any = True
    if not patched_any:
        raise SystemExit("ABORT: no tree found. Set SCALP_REPO.")
    patch_defaults_json(DEFAULTS_JSON)
    print("DONE —", FENCE)
    print("\n⚠ LIVE-SHARED FILES CHANGED — deploy on a NON-TRADING DAY.")
    print("   All three keys default OFF; absent config == today's behaviour.")


if __name__ == "__main__":
    main()
