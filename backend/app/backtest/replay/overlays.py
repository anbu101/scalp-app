# backend/app/backtest/replay/overlays.py
#
# ── TRADE_REPLAY_20260928 ── per-strategy indicator overlays for the replay.
#
# RULE: every line drawn here is produced by the strategy's OWN backtest code
# (engine functions / runner helpers / the live indicator classes the runner
# drives), fed the SAME inputs the runner fed it, on the run's frozen config
# normalised by the runner's own normaliser. Nothing is re-derived from
# memory. Parity tiers, reported to the UI:
#   exact      same code, same inputs → bit-identical to what the run saw
#   converged  same code, but the indicator's state runs continuously from
#              the run's first day; replay warms it on DEEP_WARM sessions
#              (Wilder/EMA memory ~0 after that; asserted by the test suite)
#   copied     runner-inline arithmetic (no importable function) copied
#              verbatim; the suite fails if the runner's lines change
#   levels     only config / persisted trade levels (no indicator exists)
#
# Overlay shapes (points are [bar_start_ts, value, ...]; `tf` = bar minutes):
#   line  {type, id, label, color, tf, points, dash?, width?, step?}
#   band  {type, id, label, color, tf, upper, lower}
#   level {type, label, value, color, dash, from_ts?, to_ts?}
#   box   {type, label, color, from_ts, to_ts, top, bottom, dash?, fill?}
#   shade {type, label, from_ts, to_ts, color?}
#   dots  {type, label, color, points: [[ts, value, text]]}
# Colours are keys the UI maps to theme-safe hues: a b c d e f (series),
# profit, loss, warning, primary, muted.

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from app.backtest.replay.replay_core import (
    SESSION_OPEN_MIN, day_start, ist_date, mod_of,
)

DEEP_WARM = 40          # sessions of warm-up for continuous-state indicators


def _ro_source(conn):
    """The REAL CandleSource readers (candles_1m_for_symbol_day,
    warmup_candles_before) on the replay's read-only connection. Only
    __init__ is bypassed: it would run the corpus sanitizer, which may write."""
    from app.backtest.data.candle_source import CandleSource

    class _RO(CandleSource):
        def __init__(self, c):      # noqa: D401 - deliberately no super().__init__
            self._path = None
            self._c = c
            self._cache_day_start = None
            self._cache_underlying = None
            self._cache_expiry = None
            self._by_sym_min = {}
            self._by_sym_list = {}
            self._contracts = []
    return _RO(conn)

# Strategies whose "underlying" pane is not index SPOT.
UNDERLYING_SOURCE = {
    "BB_V1": {"underlying": "BANKNIFTY", "symbol": "BANKNIFTYFUT"},
    "BB_V2": {"underlying": "BANKNIFTY", "symbol": "BANKNIFTYFUT"},
}


def default_spec() -> dict:
    return {"spot_tf": 1, "prem_tf": 1, "spot_overlays": [], "prem_overlays": {},
            "osc": None, "ha": {}, "sl_basis": "premium", "tp_basis": "premium",
            "mtm_levels": [], "notes": [], "extra_symbols": [], "extra_labels": {},
            "parity": {"tier": "levels", "detail": "trade levels only"},
            "default_view": None, "prefer_signal": False, "config_brief": ""}


def _pts(series_ts: List[int], vals: List[Optional[float]], keep=None) -> List[list]:
    out = []
    for ts, v in zip(series_ts, vals):
        if v is None:
            continue
        if keep is not None and not keep(ts):
            continue
        out.append([int(ts), float(v)])
    return out


def _session_bounds(d: date, open_min: int = SESSION_OPEN_MIN, close_min: int = 15 * 60 + 30):
    ds = day_start(d)
    return ds + open_min * 60, ds + close_min * 60


def _hm(s, dflt: int) -> int:
    try:
        h, m = str(s).split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return dflt


def _entry_minute(t: dict) -> int:
    return mod_of(int(t["entry_ts"]))


# ═════════════════════════════════════════════════════════════════════
# TMA_V1 / TMA_V2 — EMA stack on 5m spot, rolling cross-day warm-up
# ═════════════════════════════════════════════════════════════════════
def _tma(ctx, v2: bool) -> dict:
    from app.backtest.pst.pst_indicators import aggregate
    from app.backtest.tma.tma_v1_engine import compute_state, warmup_bars
    cfg = ctx.cfg
    tf = int(cfg.get("tf_minutes", 5) or 5)
    if v2:
        from app.backtest.tma.backtest_tma_v2_runner import WARMUP_DAYS
        from app.backtest.tma.tma_v2_engine import REF_KEYS, compute_state_v2
        nwarm = WARMUP_DAYS
        xref = int(cfg.get("xover_exit_ref", 89) or 89) if cfg.get("xover_exit_ref") not in (None, "", 0) else 89
        series = [("e13", "EMA 13", "a"), ("e55", "EMA 55", "b"), ("e89", "EMA 89", "c"), ("e144", "EMA 144", "d")]
    else:
        from app.backtest.tma.backtest_tma_runner import WARMUP_DAYS
        nwarm = max(1, min(10, int(cfg.get("warmup_days") or WARMUP_DAYS)))
        ema = cfg.get("ema") or {}
        fast, mid, slow = int(ema.get("fast", 5) or 5), int(ema.get("mid", 13) or 13), int(ema.get("slow", 89) or 89)
        series = [("e5", f"EMA {fast}", "a"), ("e13", f"EMA {mid}", "b"), ("e89", f"EMA {slow}", "c")]
    acc: Dict[str, List[list]] = {k: [] for k, _, _ in series}
    eref_pts: List[list] = []
    for d in ctx.days:
        spot = ctx.corpus.spot_1m(d)
        if not spot:
            continue
        prev = ctx.corpus.spot_days_before(d, nwarm, bound=ctx.run_from)
        warm = warmup_bars([(ctx.corpus.spot_1m(p), day_start(p)) for p in prev], tf)
        today = [b for b in aggregate(spot, tf, day_start(d)) if b["complete"]]
        bars = warm + today
        if v2:
            st = compute_state_v2(bars, ref_period=(xref if xref not in REF_KEYS else None))
        else:
            st = compute_state(bars, fast=fast, mid=mid, slow=slow)
        ts = [b["ts"] for b in bars]
        for k, _, _ in series:
            acc[k] += _pts(ts[len(warm):], st[k][len(warm):])
        if v2 and "eref" in st:
            eref_pts += _pts(ts[len(warm):], st["eref"][len(warm):])
    ovs = [{"type": "line", "id": k, "label": lbl, "color": col, "tf": tf, "points": acc[k]}
           for k, lbl, col in series]
    if eref_pts:
        ovs.append({"type": "line", "id": "eref", "label": f"EMA {xref} (exit ref)", "color": "e",
                    "tf": tf, "points": eref_pts, "dash": True})
    brief = (f"5m EMA 13/55/89/144 · exit ref EMA{xref}" if v2 else f"{tf}m EMA {fast}/{mid}/{slow}")
    return {"spot_tf": tf, "spot_overlays": ovs, "config_brief": brief,
            "parity": {"tier": "exact", "detail": f"compute_state{'_v2' if v2 else ''} on the runner's "
                                                   f"{nwarm}-session rolling warm-up (bounded at the run start)"}}


# ═════════════════════════════════════════════════════════════════════
# VET_V1 — EMA f1/f2 + SMA ± ATR·k channel (continuous across sessions)
# ═════════════════════════════════════════════════════════════════════
def _vet_bars(ctx, days, tf):
    from app.backtest.gc.gc_v1_engine import resample_spot
    from app.backtest.vet.backtest_vet_runner import SESSION_OPEN_MIN as VOPEN
    bars, day_of = [], []
    for d in days:
        for c in resample_spot(ctx.corpus.spot_1m(d), tf, day_start(d) + VOPEN * 60):
            bars.append(c)
            day_of.append(d)
    return bars, day_of


def vet_window_days(ctx, cfg) -> tuple:
    """Sessions fed to vet_states: the runner's own start (warmup_sessions
    before date_from) when it is within DEEP_WARM of the window, else the
    DEEP_WARM sessions before the window. Returns (days, exact?)."""
    first = ctx.days[0]
    run_from = ctx.run_from or first
    run_warm = ctx.corpus.spot_days_before(run_from, int(cfg["warmup_sessions"]))
    run_start = run_warm[0] if run_warm else run_from
    before = [x for x in ctx.corpus.spot_days() if run_start <= x < first]
    exact = len(before) <= DEEP_WARM
    before = before[-DEEP_WARM:]
    return before + [d for d in ctx.days], exact


def _vet(ctx) -> dict:
    from app.backtest.vet.backtest_vet_runner import _norm_cfg
    from app.backtest.vet.vet_v1_engine import vet_states
    cfg = _norm_cfg(ctx.cfg)
    tf = int(cfg["timeframe_minutes"])
    days, exact = vet_window_days(ctx, cfg)
    bars, day_of = _vet_bars(ctx, days, tf)
    st = vet_states(bars, ema_fast1=cfg["ema_fast1"], ema_fast2=cfg["ema_fast2"],
                    trend_len=cfg["trend_len"], range_len=cfg["range_len"])
    show = set(ctx.days)
    idx = [i for i, d in enumerate(day_of) if d in show]
    e1 = [[bars[i].ts, st[i].ema_f1] for i in idx]
    e2 = [[bars[i].ts, st[i].ema_f2] for i in idx]
    sma = [[bars[i].ts, st[i].sma_t] for i in idx if st[i].sma_t is not None]
    up = [[bars[i].ts, st[i].ch_top] for i in idx if st[i].ch_top is not None]
    dn = [[bars[i].ts, st[i].ch_bot] for i in idx if st[i].ch_bot is not None]
    cond = [[bars[i].ts, 0, st[i].condition] for i in idx]
    ovs = [
        {"type": "band", "id": "channel", "label": f"SMA{cfg['trend_len']} ± ATR·{cfg['range_len']:g}",
         "color": "muted", "tf": tf, "upper": up, "lower": dn},
        {"type": "line", "id": "sma", "label": f"SMA {cfg['trend_len']}", "color": "c", "tf": tf, "points": sma, "dash": True},
        {"type": "line", "id": "ema1", "label": f"EMA {cfg['ema_fast1']}", "color": "a", "tf": tf, "points": e1},
        {"type": "line", "id": "ema2", "label": f"EMA {cfg['ema_fast2']}", "color": "b", "tf": tf, "points": e2},
        {"type": "regime", "id": "cond", "label": "condition", "tf": tf, "points": cond},
    ]
    return {"spot_tf": tf, "spot_overlays": ovs,
            "config_brief": f"{tf}m EMA {cfg['ema_fast1']}/{cfg['ema_fast2']} · SMA{cfg['trend_len']} ± ATR·{cfg['range_len']:g}",
            "parity": ({"tier": "exact", "detail": "vet_states from the runner's own continuous start"} if exact else
                       {"tier": "converged", "detail": f"vet_states warmed on {DEEP_WARM} sessions "
                                                      "(the run's state is continuous from its first day)"})}


# ═════════════════════════════════════════════════════════════════════
# ORB_V1 / ORV_V1 — opening range on spot
# ═════════════════════════════════════════════════════════════════════
def _orb(ctx) -> dict:
    from app.backtest.orb.backtest_orb_runner import _merge_cfg
    from app.backtest.orb.orb_v1_engine import OrbBar, compute_orb, resample_1m, spot_sl_level
    cfg = _merge_cfg(ctx.cfg)
    tf, om = cfg["timeframe_minutes"], cfg["orb_minutes"]
    buf = cfg["breakout_buffer_pts"]
    eod = _hm(cfg.get("eod_square_off", cfg.get("eod_time", "15:15")), 15 * 60 + 15)
    ovs, notes = [], []
    prim = ctx.primary()
    for d in ctx.days:
        ds = day_start(d)
        spot = [OrbBar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                for r in ctx.corpus.spot_1m(d)]
        if not spot:
            continue
        orb = compute_orb(resample_1m(spot, day_start_epoch=ds, tf_minutes=tf),
                          day_start_epoch=ds, orb_minutes=om, tf_minutes=tf)
        if orb is None:
            notes.append(f"{d}: opening range incomplete (runner skips such days)")
            continue
        hi, lo = orb
        t0, t1 = ds + SESSION_OPEN_MIN * 60, ds + (SESSION_OPEN_MIN + om) * 60
        ovs.append({"type": "box", "label": f"ORB {om}m  {hi:.1f} / {lo:.1f}", "color": "primary",
                    "from_ts": t0, "to_ts": t1, "top": hi, "bottom": lo, "fill": True})
        ovs.append({"type": "level", "label": f"up trigger {hi + buf:.1f}", "value": hi + buf,
                    "color": "profit", "from_ts": t1, "to_ts": ds + eod * 60})
        ovs.append({"type": "level", "label": f"down trigger {lo - buf:.1f}", "value": lo - buf,
                    "color": "loss", "from_ts": t1, "to_ts": ds + eod * 60})
        if ist_date(int(prim["entry_ts"])) == d:
            em = _entry_minute(prim)
            sb = next((b for b in spot if (b.ts - ds) // 60 == em), None)
            if sb is not None and prim.get("instrument_type") in ("CE", "PE"):
                eff = (sb.open * cfg["sl_points"] / 100.0 if cfg["sl_dist_mode"] == "pct" else cfg["sl_points"])
                sl = spot_sl_level(side=prim["instrument_type"], mode=cfg["spot_sl_mode"], orb_high=hi,
                                   orb_low=lo, entry_spot=float(sb.open), sl_points=eff)
                if prim.get("sl") is not None and abs(round(sl, 2) - float(prim["sl"])) > 0.011:
                    notes.append(f"recomputed spot SL {sl:.2f} ≠ stored {float(prim['sl']):.2f} "
                                 "(corpus changed since the run?)")
    return {"spot_tf": tf, "spot_overlays": ovs, "notes": notes, "sl_basis": "spot", "tp_basis": "premium",
            "config_brief": f"ORB {om}m on {tf}m · SL {cfg['spot_sl_mode']} · target {cfg['target_mode']} {cfg['target_value']:g}",
            "parity": {"tier": "exact", "detail": "resample_1m + compute_orb + spot_sl_level (engine)"}}


def _orv(ctx) -> dict:
    from app.backtest.orv.backtest_orv_runner import _merge_cfg
    from app.backtest.orv.orv_v1_engine import OrvBar, compute_orb, resample_1m
    cfg = _merge_cfg(ctx.cfg)
    tf, om = cfg["timeframe_minutes"], cfg["orb_minutes"]
    ovs = []
    for d in ctx.days:
        ds = day_start(d)
        spot = [OrvBar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                for r in ctx.corpus.spot_1m(d)]
        if not spot:
            continue
        orb = compute_orb(resample_1m(spot, day_start_epoch=ds, tf_minutes=tf),
                          day_start_epoch=ds, orb_minutes=om, tf_minutes=tf)
        if orb is None:
            continue
        hi, lo = orb
        t0, t1 = ds + SESSION_OPEN_MIN * 60, ds + (SESSION_OPEN_MIN + om) * 60
        ovs.append({"type": "box", "label": f"ORB {om}m  {hi:.1f} / {lo:.1f}", "color": "primary",
                    "from_ts": t0, "to_ts": t1, "top": hi, "bottom": lo, "fill": True})
        ovs.append({"type": "level", "label": f"ORB high {hi:.1f}", "value": hi, "color": "muted",
                    "from_ts": t1, "to_ts": ds + 15 * 3600 + 30 * 60})
        ovs.append({"type": "level", "label": f"ORB low {lo:.1f}", "value": lo, "color": "muted",
                    "from_ts": t1, "to_ts": ds + 15 * 3600 + 30 * 60})
    return {"spot_tf": tf, "spot_overlays": ovs, "sl_basis": "spot", "tp_basis": "spot",
            "config_brief": f"ORB {om}m reversal on {tf}m · SL ±{cfg['sl_points']:g} · target {cfg['target_mode']}",
            "parity": {"tier": "exact", "detail": "resample_1m + compute_orb (engine); SL/TP are the stored spot levels"}}


# ═════════════════════════════════════════════════════════════════════
# FVG_V1 — gap zones with their lifecycle (ATR continuous → converged)
# ═════════════════════════════════════════════════════════════════════
def _fvg(ctx) -> dict:
    from app.backtest.fvg.backtest_fvg_runner import _engine_cfg, _merge_cfg
    from app.backtest.fvg.fvg_v1_engine import ATR, Bar, FvgEngine, resample_session
    cfg = _merge_cfg(ctx.cfg)
    tf = int(cfg["tf"])
    first = ctx.days[0]
    run_from = ctx.run_from or first
    run_warm = ctx.corpus.spot_days_before(run_from, int(cfg["warmup_sessions"]))
    run_start = run_warm[0] if run_warm else run_from
    before = [x for x in ctx.corpus.spot_days() if run_start <= x < first]
    exact = len(before) <= DEEP_WARM
    before = before[-DEEP_WARM:]

    def bars_of(d):
        return [Bar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                for r in ctx.corpus.spot_1m(d)]

    eng = FvgEngine(_engine_cfg(cfg), ATR(cfg["atr_len"]))
    from app.backtest.fvg.fvg_v1_engine import session_signals
    for wd in before:
        session_signals(eng, bars_of(wd), day_start_epoch=day_start(wd), tf_minutes=tf)

    prim = ctx.primary()
    want_min, want_side = None, prim.get("instrument_type")
    # the runner stamps ENTRY minute (= trigger_min + 1) into the condition
    try:
        hh, mm = str(prim.get("condition") or "").split("·")[2].split(":")
        want_min = int(hh) * 60 + int(mm) - 1
    except Exception:
        want_min = _entry_minute(prim) - 1
    ovs, atr_pts = [], []
    traded_gid = None
    for d in ctx.days:
        ds = day_start(d)
        b1 = bars_of(d)
        # session_signals, walked here so each gap's state can be sampled
        # after every tf close (same calls, same order as the engine's own)
        bars_tf = resample_session(b1, day_start_epoch=ds, tf_minutes=tf)
        by_min = {(b.ts - ds) // 60: b for b in b1}
        eng.new_day()
        life: Dict[int, dict] = {}
        for tb in bars_tf:
            for mod in range(tb.start_min, tb.end_min + 1):
                m = by_min.get(mod)
                if m is None:
                    continue
                s = eng.on_minute(m, mod)
                if s is not None and ist_date(int(prim["entry_ts"])) == d and s.side == want_side \
                        and s.trigger_min == want_min:
                    traded_gid = s.gid
            eng.on_tf_close(tb)
            end_ts = ds + (tb.end_min + 1) * 60
            if eng.atr.value is not None:
                atr_pts.append([tb.ts, eng.atr.value])
            for g in eng.gaps:
                rec = life.get(g.gid)
                if rec is None:       # born on the bar that just closed
                    life[g.gid] = {"g": g, "segs": [],
                                   "cur": (g.state, g.side, ds + (g.born_end_min + 1) * 60)}
                    continue
                cs, cside, cstart = rec["cur"]
                if (g.state, g.side) != (cs, cside):     # inverted / died on this close
                    rec["segs"].append((cs, cside, cstart, end_ts))
                    rec["cur"] = (g.state, g.side, end_ts)
        day_end = ds + (15 * 60 + 30) * 60
        for gid, rec in life.items():
            cs, cside, cstart = rec["cur"]
            rec["segs"].append((cs, cside, cstart, day_end))
            g = rec["g"]
            for state, side, a, b in rec["segs"]:
                if state == "DEAD" or b <= a:
                    continue
                traded = gid == traded_gid and ist_date(int(prim["entry_ts"])) == d
                kind = "IFVG" if state == "INVERTED" else "FVG"
                ovs.append({"type": "box", "label": f"{kind} {'bull' if side == 'BULL' else 'bear'}"
                                                   f"{' · traded' if traded else ''}",
                            "color": "profit" if side == "BULL" else "loss",
                            "from_ts": a, "to_ts": b, "top": g.top, "bottom": g.bottom,
                            "fill": True, "dash": state == "INVERTED", "strong": traded})
    return {"spot_tf": tf, "spot_overlays": ovs, "sl_basis": "spot", "tp_basis": "spot",
            "config_brief": f"{tf}m FVG{'+IFVG' if cfg.get('trade_ifvg', True) else ''} · disp ≥{cfg['disp_atr']:g} ATR · "
                            f"gap ≥{cfg['gap_min_atr']:g} ATR · ATR{cfg['atr_len']}",
            "notes": [] if traded_gid is not None else ["traded gap not matched in the replayed day"],
            "parity": ({"tier": "exact", "detail": "FvgEngine replayed from the run's own start"} if exact else
                       {"tier": "converged", "detail": f"FvgEngine (ATR continuous) warmed on {DEEP_WARM} sessions"})}


# ═════════════════════════════════════════════════════════════════════
# STFC_V1 — SuperTrend on spot tf bars (continuous across sessions)
# ═════════════════════════════════════════════════════════════════════
def _stfc(ctx) -> dict:
    from app.backtest.stfc.backtest_stfc_runner import _merge_cfg
    from app.backtest.stfc.stfc_v1_engine import StfcBar, SuperTrend, resample_session
    from app.utils.market_hours import is_trading_day
    cfg = _merge_cfg(ctx.cfg)
    tf = int(cfg["timeframe_minutes"])
    first = ctx.days[0]
    run_from = ctx.run_from or first
    # runner R5: warmup_sessions trading days before date_from, then every
    # trading day of the range advances the indicator
    warm, d, guard = [], run_from - timedelta(days=1), 0
    while len(warm) < cfg["warmup_sessions"] and guard < 30:
        if is_trading_day(d):
            warm.append(d)
        d -= timedelta(days=1)
        guard += 1
    run_start = min(warm) if warm else run_from
    before = [x for x in ctx.corpus.spot_days() if run_start <= x < first]
    exact = len(before) <= DEEP_WARM
    before = before[-DEEP_WARM:]
    st = SuperTrend(cfg["st_len"], cfg["st_mult"])

    def tf_bars(dd):
        ds = day_start(dd)
        b1 = [StfcBar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
              for r in ctx.corpus.spot_1m(dd)]
        return resample_session(b1, day_start_epoch=ds, tf_minutes=tf)

    for wd in before:
        for b in tf_bars(wd):
            st.update(b)
    up, dn, flips = [], [], []
    prev_dir = None
    for dd in ctx.days:
        for b in tf_bars(dd):
            v, di = st.update(b)
            if v is None:
                continue
            (up if di == -1 else dn).append([b.ts, v])
            (dn if di == -1 else up).append([b.ts, None])
            if prev_dir is not None and di != prev_dir:
                flips.append([b.ts, b.close, "▲ flip up" if di == -1 else "▼ flip down"])
            prev_dir = di
    spot_basis = cfg["exit_basis"] == "spot"
    ovs = [{"type": "line", "id": "st_up", "label": f"SuperTrend({cfg['st_len']},{cfg['st_mult']:g}) up",
            "color": "profit", "tf": tf, "points": up, "step": True},
           {"type": "line", "id": "st_dn", "label": "SuperTrend down", "color": "loss", "tf": tf,
            "points": dn, "step": True},
           {"type": "dots", "id": "flips", "label": "flips", "color": "warning", "tf": tf, "points": flips}]
    return {"spot_tf": tf, "spot_overlays": ovs,
            "sl_basis": "spot" if spot_basis else "premium", "tp_basis": "spot" if spot_basis else "premium",
            "config_brief": f"{tf}m ST({cfg['st_len']},{cfg['st_mult']:g}) · exits on {cfg['exit_basis']}",
            "parity": ({"tier": "exact", "detail": "SuperTrend replayed from the run's own warm start"} if exact else
                       {"tier": "converged", "detail": f"SuperTrend (continuous) warmed on {DEEP_WARM} sessions"})}


# ═════════════════════════════════════════════════════════════════════
# CBO_V1 — previous-candle breakout (+ runner-inline VWAP / EMA filters)
# ═════════════════════════════════════════════════════════════════════
def cbo_filter_series(spot, ds, cfg, grid_anchor_min):
    """VERBATIM copy of backtest_cbo_runner's CBO_D10_FILTERS_20260830
    per-day block (VWAP = equal-weight session typical price; EMA seeded
    with the first close, day-reset). The test suite fingerprints the
    runner's lines — if they change, this copy must be re-synced."""
    vwap_at: Dict[int, float] = {}
    ema_at: Dict[int, float] = {}
    _pv = _n = 0.0
    _per = max(2, int(cfg["ema_gate"].get("period", 144) or 144))
    _al = 2.0 / (_per + 1.0)
    _ema = None
    for _b in spot:
        if (_b.ts - ds) // 60 < grid_anchor_min:
            continue                     # pre-open prints: no session
        _pv += (_b.high + _b.low + _b.close) / 3.0
        _n += 1.0
        vwap_at[_b.ts] = _pv / _n
        _ema = _b.close if _ema is None else \
            _al * _b.close + (1.0 - _al) * _ema
        ema_at[_b.ts] = _ema
    return vwap_at, ema_at


def _cbo(ctx) -> dict:
    from app.backtest.cbo.backtest_cbo_runner import GRID_ANCHOR_MIN, _merge_cfg
    from app.backtest.cbo.cbo_v1_engine import CboBar, tf_bars
    cfg = _merge_cfg(ctx.cfg)
    tf = int(cfg["timeframe_minutes"])
    hi_pts, lo_pts, vw_pts, em_pts = [], [], [], []
    for d in ctx.days:
        ds = day_start(d)
        spot = [CboBar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                for r in ctx.corpus.spot_1m(d)]
        if not spot:
            continue
        refs = tf_bars([b for b in spot if (b.ts - ds) // 60 >= GRID_ANCHOR_MIN],
                       anchor_ts=ds + GRID_ANCHOR_MIN * 60, tf_minutes=tf)
        for prev, cur in zip(refs, refs[1:]):
            hi_pts.append([cur.bucket_ts, prev.high])
            lo_pts.append([cur.bucket_ts, prev.low])
        if cfg["vwap_filter"].get("enabled") or cfg["ema_gate"].get("enabled"):
            vw, em = cbo_filter_series(spot, ds, cfg, GRID_ANCHOR_MIN)
            if cfg["vwap_filter"].get("enabled"):
                vw_pts += [[k, v] for k, v in sorted(vw.items())]
            if cfg["ema_gate"].get("enabled"):
                em_pts += [[k, v] for k, v in sorted(em.items())]
    ovs = [{"type": "line", "id": "prev_hi", "label": "prev bar high", "color": "profit", "tf": tf,
            "points": hi_pts, "step": True, "dash": True},
           {"type": "line", "id": "prev_lo", "label": "prev bar low", "color": "loss", "tf": tf,
            "points": lo_pts, "step": True, "dash": True}]
    tier = {"tier": "exact", "detail": "tf_bars (engine) prev-bar references"}
    if vw_pts:
        ovs.append({"type": "line", "id": "vwap", "label": "VWAP (session TP mean)", "color": "warning",
                    "tf": 1, "points": vw_pts})
    if em_pts:
        ovs.append({"type": "line", "id": "ema", "label": f"EMA {cfg['ema_gate'].get('period', 144)}",
                    "color": "c", "tf": 1, "points": em_pts})
    if vw_pts or em_pts:
        tier = {"tier": "copied", "detail": "prev-bar refs via engine; VWAP/EMA filter lines are the "
                                            "runner-inline block copied verbatim (fingerprinted)"}
    return {"spot_tf": tf, "spot_overlays": ovs, "sl_basis": "spot", "tp_basis": "premium",
            "config_brief": f"{tf}m prev-candle breakout · {cfg['leg_action']} · target {cfg['target_mode']} {cfg['target_value']:g}",
            "parity": tier}


# ═════════════════════════════════════════════════════════════════════
# GC_V1 — first-candle H1/L1, breakout-retest chain with SL flips
# ═════════════════════════════════════════════════════════════════════
def _gc(ctx) -> dict:
    from app.backtest.gc.backtest_gc_runner import SESSION_OPEN_MIN as GOPEN, _norm_cfg
    from app.backtest.gc.gc_v1_engine import resample_spot, simulate_gc_day
    cfg = _norm_cfg(ctx.cfg)
    tf = int(cfg["timeframe_minutes"])
    tf_s = tf * 60
    exit_min = _hm(cfg.get("exit_time", "15:15"), 15 * 60 + 15)
    cutoff_min = _hm(cfg.get("entry_cutoff_time", "13:00"), 13 * 60)
    ovs, notes = [], []
    for d in ctx.days:
        ds = day_start(d)
        session0 = ds + GOPEN * 60
        today = resample_spot(ctx.corpus.spot_1m(d), tf, session0)
        if not today:
            continue
        prev_days = ctx.corpus.spot_days_before(d, 1)
        prev_tail = []
        if prev_days:
            pd = prev_days[-1]
            prev_tail = resample_spot(ctx.corpus.spot_1m(pd), tf, day_start(pd) + GOPEN * 60)[-cfg["sl_lookback"]:]
        sim = simulate_gc_day(today, prev_tail, {
            "tf_s": tf_s, "exit_epoch": ds + exit_min * 60,
            "max_trades": cfg["max_trades_per_day"],
            "entry_cutoff_epoch": ds + cutoff_min * 60,
            "signal_mode": cfg["signal_mode"], "sl_lookback": cfg["sl_lookback"],
            "c1_range_max_pct": cfg["c1_range_max_pct"],
            "c1_skip_candles": cfg["c1_skip_candles"],
            "max_sl_pct": cfg["max_sl_pct"],
            "prev_close": (float(prev_tail[-1].close) if prev_tail else None),
        })
        # C1 exactly as simulate_gc_day scopes it (session-in-scope, skip N)
        sess = [c for c in today if (c.ts + tf_s) <= ds + exit_min * 60]
        sess = sess[max(0, int(cfg.get("c1_skip_candles") or 0)):]
        if sess:
            c1 = sess[0]
            ovs.append({"type": "box", "label": f"C1 {c1.high:.1f} / {c1.low:.1f}", "color": "primary",
                        "from_ts": c1.ts, "to_ts": c1.ts + tf_s, "top": c1.high, "bottom": c1.low, "fill": True})
            ovs.append({"type": "level", "label": f"H1 {c1.high:.1f}", "value": c1.high, "color": "profit",
                        "from_ts": c1.ts + tf_s, "to_ts": ds + exit_min * 60})
            ovs.append({"type": "level", "label": f"L1 {c1.low:.1f}", "value": c1.low, "color": "loss",
                        "from_ts": c1.ts + tf_s, "to_ts": ds + exit_min * 60})
        for k, st in enumerate(sim["trades"]):
            end = st.exit_ts or ds + exit_min * 60
            ovs.append({"type": "level", "label": f"{'flip ' + str(st.flip_seq) + ' ' if st.flip_seq else ''}"
                                                  f"{st.signal_side} SL {st.sl_level:.1f}"
                                                  f"{' (fallback)' if st.sl_fallback else ''}",
                        "value": st.sl_level, "color": "loss", "dash": True,
                        "from_ts": st.entry_ts, "to_ts": end})
    return {"spot_tf": tf, "spot_overlays": ovs, "notes": notes, "sl_basis": "spot", "tp_basis": "premium",
            "config_brief": f"{tf}m first-candle breakout-retest · SL lookback {cfg['sl_lookback']} · signal {cfg['signal_mode']}",
            "parity": {"tier": "exact", "detail": "resample_spot + simulate_gc_day with the previous session's tail"}}


# ═════════════════════════════════════════════════════════════════════
# VAP_V1 — anchored VWAP on the SIGNAL contract's premium
# ═════════════════════════════════════════════════════════════════════
def _vap_signal_symbol(ctx, cfg, d, side) -> Optional[str]:
    """Replays the runner's day-scoped signal-contract pick (SELL mode: the
    signal leg differs from the traded leg)."""
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    from app.backtest.ic.ic_v1_engine import select_strike
    ds = day_start(d)
    sel_min = _hm(cfg.get("selection_time", "09:20"), 9 * 60 + 20)
    min_prem = float(cfg.get("min_premium", 0) or 0)
    prem_max = float(cfg.get("signal_premium_max", 200) or 0)
    want = expected_expiry_for_day(d).isoformat()
    rows = ctx.corpus.c.execute(
        "SELECT tradingsymbol, close FROM backtest_candles_1m WHERE underlying=? AND instrument_type=? "
        "AND expiry=? AND ts=?", (ctx.underlying, side, want, ds + sel_min * 60 - 60)).fetchall()
    cands = [(r["tradingsymbol"], float(r["close"])) for r in rows if r["close"] and float(r["close"]) >= min_prem]
    cands.sort(key=lambda c: c[0])
    pick = select_strike(cands, prem_max)
    return pick[0] if pick else None


def _vap(ctx) -> dict:
    from app.backtest.pst.pst_indicators import aggregate
    from app.backtest.vap.vap_v1_engine import ema_at_bar_ends, vwap_by_minute
    cfg = ctx.cfg
    tf = int(cfg.get("tf_minutes", 5) or 5)
    tf_s = tf * 60
    prim = ctx.primary()
    cond = str(prim.get("condition") or "")
    sig_side = cond.split("_")[0] if cond.endswith("_SIG") else prim.get("instrument_type")
    mode = str(cfg.get("mode", "BUY")).upper()
    notes = []
    sig_sym = prim.get("tradingsymbol")
    if mode == "SELL" or sig_side != prim.get("instrument_type"):
        try:
            s = _vap_signal_symbol(ctx, cfg, ist_date(int(prim["entry_ts"])), sig_side)
            if s:
                sig_sym = s
            else:
                notes.append("signal contract could not be re-selected; VWAP drawn on the traded leg")
        except Exception as e:
            notes.append(f"signal contract re-selection failed ({type(e).__name__}); VWAP on the traded leg")
    ema_period = int(cfg.get("ema_period", 0) or 0)
    ema_basis = int(cfg.get("ema_basis_minutes", 1) or 1)
    vw_pts, em_pts = [], []
    for d in ctx.days:
        ds = day_start(d)
        session0 = ds + SESSION_OPEN_MIN * 60
        bars1 = [dict(c) for c in ctx.corpus.sym_1m(sig_sym, d) if c["ts"] >= session0]
        if not bars1:
            continue
        vm = vwap_by_minute(bars1)
        vw_pts += [[k, v] for k, v in sorted(vm.items()) if v is not None]
        if ema_period > 0:
            bars5 = [b for b in aggregate(ctx.corpus.sym_1m(sig_sym, d), tf, ds) if b["complete"]]
            em = ema_at_bar_ends(bars1, bars5, tf_s=tf_s, period=ema_period, basis_minutes=ema_basis)
            em_pts += [[k - tf_s, v] for k, v in sorted(em.items()) if v is not None]
    ovs = [{"type": "line", "id": "vwap", "label": f"anchored VWAP ({sig_sym[-7:] if sig_sym else ''})",
            "color": "warning", "tf": 1, "points": vw_pts}]
    if em_pts:
        ovs.append({"type": "line", "id": "ema", "label": f"EMA {ema_period}", "color": "a", "tf": tf, "points": em_pts})
    spec = {"prem_tf": tf, "prem_overlays": {sig_sym: ovs},
            "config_brief": f"{mode} · {tf}m close vs anchored VWAP of the {sig_side} premium",
            "notes": notes,
            "parity": {"tier": "exact", "detail": "vwap_by_minute / ema_at_bar_ends (engine) on the signal contract"}}
    if sig_sym and sig_sym != prim.get("tradingsymbol"):
        spec["extra_symbols"] = [sig_sym]
        spec["extra_labels"] = {sig_sym: f"Signal {sig_side} {sig_sym[-7:]}"}
    return spec


# ═════════════════════════════════════════════════════════════════════
# BRK_V1 — premium level breakout (config levels only)
# ═════════════════════════════════════════════════════════════════════
def _brk(ctx) -> dict:
    from app.backtest.brk.backtest_brk_runner import _merge_cfg
    cfg = _merge_cfg(ctx.cfg)
    ovs = []
    d = ist_date(int(ctx.primary()["entry_ts"]))
    ds = day_start(d)
    windows = [("s1", cfg["select_time"], cfg["entry_first"], cfg["entry_last"])]
    if cfg.get("s2_enabled"):
        windows.append(("s2", cfg["s2_select_time"], cfg["s2_entry_first"], cfg["s2_entry_last"]))
    for tag, sel, ef, el in windows:
        sm, fm, lm = _hm(sel, 565), _hm(ef, 570), _hm(el, 575)
        ovs.append({"type": "shade", "label": f"{tag} entry window {ef}–{el}", "from_ts": ds + fm * 60,
                    "to_ts": ds + (lm + 1) * 60, "color": "primary"})
        ovs.append({"type": "shade", "label": f"{tag} select {sel}", "from_ts": ds + (sm - 1) * 60,
                    "to_ts": ds + sm * 60, "color": "muted"})
        ovs.append({"type": "level", "label": f"break above {float(cfg['break_above']):g}",
                    "value": float(cfg["break_above"]), "color": "warning", "from_ts": ds + (sm - 1) * 60,
                    "to_ts": ds + (lm + 1) * 60})
    prim = ctx.primary()
    return {"prem_overlays": {prim.get("tradingsymbol"): ovs},
            "config_brief": f"select < {float(cfg['select_below']):g} at {cfg['select_time']} · break ≥ "
                            f"{float(cfg['break_above']):g} × {cfg['sustain_candles']} close · SL {cfg['sl_pts']:g} / TP {cfg['tp_pts']:g}",
            "parity": {"tier": "levels", "detail": "config levels + stored SL/TP (BRK has no indicator)"}}


# ═════════════════════════════════════════════════════════════════════
# HA_V1 — Heikin Ashi on the option (the runner's own _HAState)
# ═════════════════════════════════════════════════════════════════════
def _ha(ctx) -> dict:
    from app.backtest.ha.backtest_ha_runner import EMA_PERIOD, WARMUP_CANDLES, _HAState
    src = _ro_source(ctx.corpus.c)
    ha, ovs = {}, {}
    for t in ctx.legs:
        sym = t.get("tradingsymbol")
        bars_out, ema_pts = [], []
        for d in ctx.days:
            day = ctx.corpus.sym_1m(sym, d)
            if not day:
                continue
            st = _HAState(sym)
            warm = src.warmup_candles_before(sym, int(day[0]["ts"]), WARMUP_CANDLES)
            if warm:
                st.warmup([{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                            "low": float(c.low), "close": float(c.close)} for c in warm])
            for b in day:
                h, e, _sig = st.on_bar({"ts": int(b["ts"]), "open": float(b["open"]), "high": float(b["high"]),
                                        "low": float(b["low"]), "close": float(b["close"])})
                bars_out.append([int(b["ts"]), round(h.open, 2), round(h.high, 2), round(h.low, 2), round(h.close, 2)])
                if e is not None:
                    ema_pts.append([int(b["ts"]), e])
        ha[sym] = bars_out
        ovs[sym] = [{"type": "line", "id": "ema_ha_low", "label": f"EMA {EMA_PERIOD} of HA low",
                     "color": "warning", "tf": 1, "points": ema_pts}]
    return {"ha": ha, "prem_overlays": ovs, "config_brief": "1m Heikin Ashi · EMA20(HA low) · SL = red-candle low",
            "parity": {"tier": "exact", "detail": f"runner _HAState, {WARMUP_CANDLES}-bar warm-up per day"}}


# ═════════════════════════════════════════════════════════════════════
# SCALP_V1 / V3 / V5 — the live IndicatorEnginePineV19 on the premium
# ═════════════════════════════════════════════════════════════════════
def _gate_cfg(ctx) -> dict:
    eg = ctx.cfg.get("ema_gate")
    if isinstance(eg, dict):
        return eg
    try:
        from app.config.strategy_loader import load_strategy_config
        eg = (load_strategy_config(ctx.strategy_id) or {}).get("ema_gate") or {}
        if eg.get("enabled"):
            ctx.notes.append("gate EMA settings read from the current strategy config (not stored with this run)")
        return eg
    except Exception:
        return {}


def _pine_series(ind_vals_seq):
    out = {k: [] for k in ("ema8", "ema20_low", "ema20_high", "vwap", "gate_ema", "rsi_smoothed", "rsi_raw")}
    for ts, v in ind_vals_seq:
        if not v:
            continue
        for k in out:
            if v.get(k) is not None:
                out[k].append([ts, float(v[k])])
    return out


def _pine_overlays(ser, tf, show_vwap, gate_period):
    ovs = [{"type": "band", "id": "ema20", "label": "EMA20 low/high (SMA9)", "color": "muted", "tf": tf,
            "upper": ser["ema20_high"], "lower": ser["ema20_low"]},
           {"type": "line", "id": "ema20_high", "label": "EMA20 high", "color": "b", "tf": tf, "points": ser["ema20_high"]},
           {"type": "line", "id": "ema20_low", "label": "EMA20 low", "color": "c", "tf": tf, "points": ser["ema20_low"]},
           {"type": "line", "id": "ema8", "label": "EMA 8", "color": "a", "tf": tf, "points": ser["ema8"]}]
    if show_vwap:
        ovs.append({"type": "line", "id": "vwap", "label": "VWAP (session)", "color": "warning", "tf": tf, "points": ser["vwap"]})
    if gate_period and ser["gate_ema"]:
        ovs.append({"type": "line", "id": "gate_ema", "label": f"gate EMA {gate_period}", "color": "e", "tf": tf,
                    "points": ser["gate_ema"], "dash": True})
    return ovs


def _scalp_v1v3(ctx, hedge: bool) -> dict:
    from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
    if hedge:
        from app.backtest.runner.backtest_hedge_runner import WARMUP_CANDLES, _bt_to_md_candle
    else:
        from app.backtest.runner.backtest_runner import WARMUP_CANDLES, _bt_to_md_candle
    src = _ro_source(ctx.corpus.c)
    eg = _gate_cfg(ctx)
    gate_period = int(eg.get("period", 144) or 144) if eg.get("enabled") else None
    show_vwap = bool((ctx.cfg.get("vwap_filter") or {}).get("enabled"))
    syms = []
    for t in ctx.legs:
        s = (t.get("signal_symbol") if hedge else None) or t.get("tradingsymbol")
        if s and s not in syms:
            syms.append(s)
    prem, rsi_by = {}, {}
    for sym in syms:
        seq = []
        for d in ctx.days:
            cds = src.candles_1m_for_symbol_day(sym, day_start(d))
            if not cds:
                continue
            ind = IndicatorEnginePineV19(gate_ema_period=gate_period,
                                         gate_slope_lookback=int(eg.get("slope_lookback", 30) or 30))
            warm = src.warmup_candles_before(sym, cds[0].ts, WARMUP_CANDLES)
            if warm:
                ind.warmup([_bt_to_md_candle(c) for c in warm], use_history=True, history_lookback=WARMUP_CANDLES)
            for c in cds:
                v = ind.update(_bt_to_md_candle(c))
                seq.append((int(c.ts), dict(ind.values) if ind.values else None))
        ser = _pine_series(seq)
        prem[sym] = _pine_overlays(ser, 1, show_vwap, gate_period)
        rsi_by[sym] = ser["rsi_smoothed"]
    prim_sym = (ctx.primary().get("signal_symbol") if hedge else None) or ctx.primary().get("tradingsymbol")
    osc = {"label": "RSI (5, smooth 5)", "range": [0, 100], "guides": [30, 50, 70], "by_symbol":
           {s: [{"type": "line", "id": "rsi", "label": "RSI smoothed", "color": "a", "tf": 1, "points": p}]
            for s, p in rsi_by.items()}, "default_symbol": prim_sym}
    return {"prem_overlays": prem, "osc": osc, "prefer_signal": hedge,
            "config_brief": "1m premium · EMA8 vs EMA20 low/high · RSI(5,5)"
                            + (" · VWAP filter" if show_vwap else "") + (f" · gate EMA{gate_period}" if gate_period else ""),
            "parity": {"tier": "exact", "detail": f"live IndicatorEnginePineV19, {WARMUP_CANDLES}-candle warm-up per day"}}


def _scalp_v5(ctx) -> dict:
    from app.backtest.scalpv5.backtest_scalpv5_runner import WARMUP_CANDLES, _aggregate_1m_to_tf, _Candle
    from app.engine.indicator_engine_pine_v1_9 import IndicatorEnginePineV19
    tf = int(float(ctx.cfg.get("timeframe_minutes", 3) or 3))
    if tf not in (1, 3, 5, 10, 15, 30):
        tf = 3
    src = _ro_source(ctx.corpus.c)
    prem, rsi_by = {}, {}
    for t in ctx.legs:
        sym = t.get("tradingsymbol")
        seq = []
        for d in ctx.days:
            day = src.candles_1m_for_symbol_day(sym, day_start(d))
            if not day:
                continue
            ind = IndicatorEnginePineV19()
            wc = src.warmup_candles_before(sym, day[0].ts, WARMUP_CANDLES * tf)
            w1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high), "low": float(c.low),
                    "close": float(c.close)} for c in wc]
            if w1m:
                wcs = [_Candle(b["start_ts"], b["end_ts"], b["open"], b["high"], b["low"], b["close"], "WARMUP")
                       for b in _aggregate_1m_to_tf(w1m, tf)]
                try:
                    ind.warmup(wcs, use_history=True)
                except Exception:
                    for c in wcs:
                        ind.update(c)
            b1 = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high), "low": float(c.low),
                   "close": float(c.close)} for c in day]
            for b in _aggregate_1m_to_tf(b1, tf):
                ind.update(_Candle(b["start_ts"], b["end_ts"], b["open"], b["high"], b["low"], b["close"]))
                seq.append((int(b["start_ts"]), dict(ind.values) if ind.values else None))
        ser = _pine_series(seq)
        prem[sym] = _pine_overlays(ser, tf, False, None)
        rsi_by[sym] = ser["rsi_smoothed"]
    osc = {"label": "RSI (5, smooth 5)", "range": [0, 100], "guides": [30, 50, 70], "by_symbol":
           {s: [{"type": "line", "id": "rsi", "label": "RSI smoothed", "color": "a", "tf": tf, "points": p}]
            for s, p in rsi_by.items()}, "default_symbol": ctx.primary().get("tradingsymbol")}
    return {"prem_tf": tf, "prem_overlays": prem, "osc": osc,
            "config_brief": f"{tf}m premium · EMA8 × EMA20 high entry · close < EMA20 high exit",
            "parity": {"tier": "exact", "detail": f"live IndicatorEnginePineV19 on the runner's {tf}m bars, "
                                                  f"{WARMUP_CANDLES}×{tf} warm-up"}}


# ═════════════════════════════════════════════════════════════════════
# BB_V1 / BB_V2 — the live indicator bundle on BANKNIFTYFUT 3m
# ═════════════════════════════════════════════════════════════════════
def _bb(ctx) -> dict:
    from app.backtest.bb.bt_candle_agg import Bar, aggregate_1m_to_3m
    from app.backtest.bb.bt_indicator_driver import BBSignalReplay
    from app.backtest.bb.bt_pivots import pivots_for_day
    sym = UNDERLYING_SOURCE[ctx.strategy_id]["symbol"]
    drv = BBSignalReplay(ctx.strategy_id, sym)

    def bars3(d):
        rows = ctx.corpus.sym_1m(sym, d)
        return aggregate_1m_to_3m([Bar(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]),
                                       float(r["close"])) for r in rows])

    for wd in ctx.corpus.sym_days_before(sym, ctx.days[0], DEEP_WARM // 4):
        for b in bars3(wd):
            drv.feed(b.start_ts, b.open, b.high, b.low, b.close, act=False)
    keys = ("bb_upper", "bb_middle", "bb_lower", "supertrend", "rsi")
    acc = {k: [] for k in keys}
    piv_ovs = []
    for d in ctx.days:
        piv = None
        try:
            piv = pivots_for_day(ctx.corpus.c, d)
        except Exception:
            piv = None
        drv.set_day_pivots(piv)
        ds = day_start(d)
        for name in ("pp", "r1", "s1", "r2", "s2", "s3"):
            if piv and piv.get(name) is not None:
                piv_ovs.append({"type": "level", "label": name.upper(), "value": float(piv[name]), "color": "muted",
                                "dash": True, "from_ts": ds + SESSION_OPEN_MIN * 60, "to_ts": ds + 930 * 60})
        for b in bars3(d):
            ind, _ = drv.feed(b.start_ts, b.open, b.high, b.low, b.close, act=False)
            for k in keys:
                v = (ind or {}).get(k)
                if v is not None:
                    acc[k].append([b.start_ts, float(v)])
    ovs = [{"type": "band", "id": "bb", "label": "Bollinger", "color": "muted", "tf": 3,
            "upper": acc["bb_upper"], "lower": acc["bb_lower"]},
           {"type": "line", "id": "bb_mid", "label": "BB middle", "color": "b", "tf": 3, "points": acc["bb_middle"], "dash": True},
           {"type": "line", "id": "st", "label": "SuperTrend", "color": "warning", "tf": 3, "points": acc["supertrend"], "step": True}]
    return {"spot_tf": 3, "spot_overlays": ovs + piv_ovs, "spot_label": sym,
            "osc": {"label": "RSI", "range": [0, 100], "guides": [30, 50, 70], "pane": "spot",
                    "overlays": [{"type": "line", "id": "rsi", "label": "RSI", "color": "a", "tf": 3, "points": acc["rsi"]}]},
            "config_brief": "BANKNIFTYFUT 3m · Bollinger · SuperTrend · RSI · prior-day pivots",
            "parity": {"tier": "converged", "detail": f"live indicator bundle warmed on {DEEP_WARM // 4} FUT sessions "
                                                      "(the run's bundle is continuous)"}}


# ═════════════════════════════════════════════════════════════════════
# TSG_V1 / IC_V1 / IC_V2 — baskets: MTM against the ₹ levels
# ═════════════════════════════════════════════════════════════════════
def _rs_mult(ctx, cfg, base_lots: int, d: date, qty_ratio: Optional[float]) -> tuple:
    try:
        from app.backtest.engine.lot_compounding import LotCompounder, lot_comp_is_equity
        comp = LotCompounder(cfg, ctx.run_from or d, base_lots)
        if not comp.on:
            return 1.0, ""
        if str(cfg.get("lot_comp_scale_rs", "scale")).lower() in ("fixed", "false", "0", "off"):
            return 1.0, " (₹ knobs fixed)"
        if lot_comp_is_equity(cfg):
            return (qty_ratio or 1.0), " × lot ratio from this trade's quantity"
        return comp.rs_mult(d), f" × {comp.rs_mult(d):g} (calendar compounding)"
    except Exception:
        return 1.0, ""


def _tsg(ctx) -> dict:
    cfg = ctx.cfg
    mtm_target = float(cfg.get("mtm_target", 5000) or 0)
    mtm_sl = abs(float(cfg.get("mtm_sl", 0) or 0))
    arm = abs(float(cfg.get("mtm_trail_arm", 0) or 0))
    d = ist_date(int(ctx.legs[0]["entry_ts"]))
    legs_cfg = cfg.get("legs") or []
    base_lots = int(max([int(x.get("lots") or 1) for x in legs_cfg] or [1]))
    lot = 65 if ctx.underlying == "NIFTY" else 30
    base_qty = sum(int(x.get("lots") or 0) for x in legs_cfg) * lot if legs_cfg else 0
    qty_ratio = (sum(int(t.get("qty") or 0) for t in ctx.legs) / base_qty) if base_qty else None
    mult, how = _rs_mult(ctx, cfg, base_lots, d, qty_ratio)
    lv = []
    if mtm_sl:
        lv.append({"type": "level", "label": f"SL −{mtm_sl * mult:,.0f}", "value": -mtm_sl * mult, "color": "loss"})
        hm = float(cfg.get("mtm_sl_hard_mult", 0) or 0)
        if hm >= 1.0:
            lv.append({"type": "level", "label": "hard stop (live only)", "value": -mtm_sl * mult * hm,
                       "color": "loss", "dash": True})
    if mtm_target:
        lv.append({"type": "level", "label": f"target +{mtm_target * mult:,.0f}", "value": mtm_target * mult, "color": "profit"})
    if arm:
        lv.append({"type": "level", "label": f"trail +{arm * mult:,.0f}", "value": arm * mult, "color": "warning", "dash": True})
    basis = str(cfg.get("mtm_sl_basis", "DAILY")).upper()
    notes = [f"basket ₹ levels = run config{how}"] if how else []
    if basis != "POSITION":
        notes.append("MTM SL basis is DAILY: the runner's stop also counts the day's realised P&L")
    return {"default_view": "basket", "mtm_levels": lv, "notes": notes,
            "config_brief": f"strangle {cfg.get('entry_time', '09:16')}→{cfg.get('exit_time', '15:26')} · "
                            f"MTM SL {mtm_sl:,.0f} · target {mtm_target:,.0f}",
            "parity": {"tier": "levels", "detail": "basket of stored legs; ₹ levels from the run config"}}


def _ic(ctx) -> dict:
    return {"default_view": "basket",
            "config_brief": "iron condor legs · per-leg SL levels from the run",
            "notes": (["synthetic wings are model-priced (no candles)"] if any(t.get("synthetic") for t in ctx.legs) else []),
            "parity": {"tier": "levels", "detail": "stored per-leg levels; basket MTM from 1m closes"}}


REGISTRY = {
    "TMA_V1": lambda c: _tma(c, False),
    "TMA_V2": lambda c: _tma(c, True),
    "VET_V1": _vet,
    "ORB_V1": _orb,
    "ORV_V1": _orv,
    "FVG_V1": _fvg,
    "STFC_V1": _stfc,
    "CBO_V1": _cbo,
    "GC_V1": _gc,
    "VAP_V1": _vap,
    "BRK_V1": _brk,
    "HA_V1": _ha,
    "SCALP_V1": lambda c: _scalp_v1v3(c, False),
    "SCALP_V3": lambda c: _scalp_v1v3(c, True),
    "SCALP_V5": _scalp_v5,
    "BB_V1": _bb,
    "BB_V2": _bb,
    "TSG_V1": _tsg,
    "IC_V1": _ic,
    "IC_V2": _ic,
}
