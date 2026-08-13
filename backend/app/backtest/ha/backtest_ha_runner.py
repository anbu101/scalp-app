# backend/app/backtest/ha/backtest_ha_runner.py
#
# HA_V1 backtest runner. Option BUYING on NIFTY weekly options, 1-MINUTE
# Heikin Ashi candles, indicators derived from the OPTION contract itself.
#
# FAITHFUL REPLAY — drives the SAME classes the live engine uses:
#   * HeikinAshiConverter   (real HA OHLC derivation, incl. first-bar seeding)
#   * EMA(20) on HA-low      (real SMA-seeded EMA, returns None until 20 lows)
#   * HAConditionEvaluator   (real COND1/COND2/COND3 entry logic)
# so the backtest signal == the live signal, candle for candle. We do NOT
# re-implement HA math or the entry conditions — we instantiate the live
# indicator/evaluator and feed them corpus candles exactly as the live
# SymbolState.on_tick → _on_candle_close path does.
#
# ============================================================================
# DIFFERENCES FROM THE V5 RUNNER (read before editing):
#
#   TIMEFRAME. HA_V1 is 1-MINUTE (live TIMEFRAME_SEC=60), not 3-minute. The
#   corpus 1m bars feed the HeikinAshiConverter directly — ONE HA candle per
#   1m bar. There is NO 3m aggregation here (V5's _aggregate_1m_to_3m has no
#   analogue). Intrabar SL/TP is therefore checked on the SAME 1m bar that
#   produced the HA candle (one underlying bar per HA candle), not across a
#   basket of sub-bars.
#
#   INDICATOR. HA does NOT use IndicatorEnginePineV19. It uses
#   HeikinAshiConverter + EMA(20)-of-HA-low + HAConditionEvaluator. The
#   per-symbol state mirrors the live SymbolState dataclass.
#
#   EXIT ASYMMETRY (matches live ha_trade_manager exactly):
#     TP → checked INTRABAR via the 1m HIGH (live: every tick). As soon as
#          high >= tp_price the trade exits at tp_price.
#     SL → checked on CANDLE CLOSE only via the 1m CLOSE (live:
#          check_sl_on_close, HASignalEngine.sl_hit = close <= sl_price).
#          A wick that pierces SL mid-bar but closes above it does NOT exit.
#     Ambiguous bar (high>=tp AND close<=sl in the same 1m) → pessimistic
#     SL-first, flagged ambiguous. This preserves the live TP-aggressive /
#     SL-conservative design.
#
#   TP/SL PRICES (fill-independent, matches live):
#     SL = the signal's red-candle low (HAEntrySignal.sl_price, fixed).
#     TP = entry_ltp + (entry_ltp - sl) * RR, then optional target_override
#          (entry_ltp + points). Computed from the SIGNAL entry, never moved.
#
#   ENTRY PRICE. Live enters a protected LIMIT buy and patches the recorded
#   entry to the true fill in the background. The backtest has no order book;
#   it records entry at the SIGNAL ltp (the selected contract's HA-close-bar
#   raw close at signal time — same value the live signal path passes as
#   entry_ltp). This is the faithful backtest analogue (V5 does the same:
#   entry at signal candle close).
#
# ============================================================================
# CONCURRENCY — SINGLE GLOBAL OPEN TRADE (intentional; differs from live today)
#
#   Live HA_V1 currently allows ONE open CE *and* ONE open PE concurrently
#   (_live is keyed by side). This runner deliberately models a SINGLE GLOBAL
#   open trade (one position at a time, either side), with same-candle
#   arbitration across BOTH sides electing the HIGHEST entry premium (symbol
#   string tie-break) — identical to the V5/V3 arbitration. This matches the
#   planned live conversion to a global single-trade gate (tracked as a
#   separate live change-set). When that live change lands, live and backtest
#   agree; until then, this runner reflects the TARGET behaviour, not today's
#   per-side live behaviour. This is called out so a reader comparing live
#   vs backtest trade counts understands the gap is intentional and temporary.
#
#   DAILY CAP. The live HASignalEngine enforces max_trades_per_side (default
#   10) PER SIDE. Under the global-single gate we preserve that semantics:
#   the daily COUNTER stays per-side (up to 10 CE entries + 10 PE entries
#   across the day), only CONCURRENCY goes global (never two open at once).
#   We drive the real HASignalEngine so the counting is the live counting.
#
# ============================================================================
# SELECTION + ARBITRATION — reuses the SAME backtest_selector.py as V5/V3/V1.
#
#   Live HA does NOT own a selection loop. _reload_selection() reads the
#   SCALP_V1 selection files (SCALP_V1_selected_{ce,pe}.json) and filters by
#   HA's own option_premium band. That selection IS exactly what
#   backtest_selector.build_selection_timeline replays (it reproduces the
#   live SCALP_V1 OptionSelector: premium band → expected weekly expiry →
#   median-ATM → ATM±800 → nearest-2-per-side, re-selected every 120s).
#
#   So the backtest gates HA entries on snapshot membership using HA's premium
#   band, the same way V5 does. A BUY may only enter on a contract present in
#   the selection snapshot active at the signal candle's close — exactly the
#   live behaviour (HA only evaluates signals for the currently-selected CE/PE,
#   which are themselves drawn from the SCALP_V1 selection filtered by HA's
#   band). LOCK CARVE-OUT: the held contract stays "selected" while open even
#   if its premium drifts out of band (matches live, which keeps monitoring an
#   open trade on a no-longer-selected strike via _active_trade_symbols).
#
# Read-only on the corpus. P&L LONG = (exit - entry) * qty, qty = lots * 65.
# Charges via charges_for_long_trade (STT on the exit/sell leg).

from __future__ import annotations

# Safer form — anchors for PyInstaller, but tolerant if a dep is unavailable
# at module-import time (the real import still happens lazily in the function).
try:
    import app.backtest.data.candle_source  # noqa: F401
    import app.backtest.engine.backtest_selector  # noqa: F401
except Exception:
    pass

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
LOT_SIZE = 65            # NIFTY
STRIKE_STEP = 50         # NIFTY
TIMEFRAME_SEC = 60       # HA_V1 is 1-minute (live TIMEFRAME_SEC)
EMA_PERIOD = 20          # EMA(20) of HA-low — matches live + TradingView
WARMUP_CANDLES = 100     # prior 1m bars replayed for HA+EMA convergence
                         # (live SymbolState.warmup_from_db uses WARMUP_CANDLES=100)


@dataclass
class HATrade:
    side: str                  # CE | PE
    symbol: str
    strike: float
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    qty: int
    condition: Optional[str] = None     # COND1 | COND2 | COND3 (signal reason)
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross: Optional[float] = None
    charges: Optional[float] = None
    net: Optional[float] = None
    ambiguous: bool = False
    # ── fields persist_run (non-hedge branch) reads as attributes ──
    instrument_type: str = "CE"     # CE | PE (mirrors side)
    expiry: str = ""                # ISO date of the contract
    direction: str = "LONG"         # HA is always LONG (option buying)
    max_adverse: float = 0.0        # not tracked → 0
    max_favorable: float = 0.0      # not tracked → 0

    # persist_run reads t.pnl / t.net_pnl / t.ambiguous_fill — expose as
    # read-only aliases so the SAME object serves both the repo and the UI dict.
    @property
    def pnl(self) -> Optional[float]:
        return self.gross

    @property
    def net_pnl(self) -> Optional[float]:
        return self.net

    @property
    def ambiguous_fill(self) -> bool:
        return self.ambiguous


# ----------------------------------------------------------------------
# Small helpers (shared shape with the V5 runner)
# ----------------------------------------------------------------------
def _ist_day(ep: int) -> date:
    return datetime.fromtimestamp(ep, IST).date()


def _day_bounds(d: date) -> Tuple[int, int]:
    lo = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    return lo, lo + 86400


def _hm(ep: int) -> str:
    dt = datetime.fromtimestamp(ep, IST)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _in_session(ep: int, start_hm: str, end_hm: str) -> bool:
    hm = _hm(ep)
    return start_hm <= hm <= end_hm


def _empty_summary() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
        "max_drawdown": 0.0, "ambiguous_fills": 0,
    }


# ── HA_COND_FILTER BEGIN ── canonical condition names + config parser.
# The evaluator emits exactly these strings (HAEntrySignal.condition), verified
# against app/engine/ha_options/ha_signal_engine.py. The parser is shared by
# HA_V1 and HA_SELL (each runner carries its own copy — strategy isolation).
_ALL_CONDS = ("COND1", "COND2", "COND3")


def _parse_enabled_conditions(cfg: dict) -> set:
    """Resolve cfg['entry_conditions'] into the enabled-condition set.

    BACK-COMPAT CONTRACT: an absent key, empty list, or a list containing no
    valid condition names ALL resolve to the full set — so every persisted
    config, queued job, and re-run created before this feature behaves exactly
    as before. Names are upper/strip-normalised; unknown names are dropped
    silently (they can never match the evaluator's output anyway)."""
    raw = cfg.get("entry_conditions") or []
    enabled = {str(c).strip().upper() for c in raw
               if str(c).strip().upper() in _ALL_CONDS}
    return enabled if enabled else set(_ALL_CONDS)
# ── HA_COND_FILTER END ──


def _snapshot_symbols(snap: List[dict], side: Optional[str] = None) -> set:
    """Set of tradingsymbols in a selection snapshot, optionally filtered to a
    side. Mirrors the live selection membership check (own side)."""
    out = set()
    for o in snap or []:
        if side is not None and o.get("type") != side:
            continue
        sym = o.get("tradingsymbol") or o.get("symbol")
        if sym:
            out.add(sym)
    return out


# ----------------------------------------------------------------------
# Per-symbol HA state — mirrors the live SymbolState (heikin_ashi + EMA +
# HAConditionEvaluator), minus the 1-min OHLC accumulator (the corpus already
# hands us completed 1m bars, so we feed them straight into the converter).
# ----------------------------------------------------------------------
class _HAState:
    def __init__(self, symbol: str):
        # Lazy imports so a missing dep doesn't break module import in the
        # PyInstaller bundle; resolved at first day-build in the runner.
        from app.indicators.heikin_ashi import HeikinAshiConverter
        from app.indicators.ema import EMA
        from app.engine.ha_options.ha_signal_engine import HAConditionEvaluator

        self.symbol = symbol
        self.ha_converter = HeikinAshiConverter()
        self._ema_low = EMA(EMA_PERIOD)
        self.ema_low_value: Optional[float] = None
        self.evaluator = HAConditionEvaluator()
        self.last_ha = None     # latest completed HACandle

    def warmup(self, bars_1m: List[dict]) -> None:
        """Replay prior-day 1m bars through the converter + EMA + evaluator so
        EMA20 is latched and the evaluator buffer is warm BEFORE session-start
        signals — the backtest analogue of live SymbolState.warmup_from_db()
        (which replays stored ha_candles rows). Faithful because warmup pushes
        the SAME (ha, ema) pair into the evaluator that the live warmup does.
        Out-of-order/duplicate ts inside warmup are skipped defensively (the
        converter raises on those; we never want warmup to abort a day)."""
        for b in bars_1m:
            try:
                ha = self.ha_converter.update(
                    ts=int(b["ts"]),
                    o=float(b["open"]), h=float(b["high"]),
                    l=float(b["low"]), c=float(b["close"]),
                )
            except ValueError:
                # duplicate / out-of-order in corpus warmup window — skip
                continue
            ema_val = self._ema_low.update(ha.low)
            self.ema_low_value = ema_val
            self.evaluator.push(ha, ema_val)
            self.last_ha = ha

    def on_bar(self, b: dict):
        """Feed ONE completed 1m bar. Returns (ha, ema_val, signal) where signal
        is the HAEntrySignal from the real evaluator. Mirrors the live
        _on_candle_close ordering: converter → EMA → evaluator.push.

        NOTE: in live, the evaluator is push()-ed exactly once per closed candle
        (inside the selected-symbol branch). Here we push once per bar for the
        symbol regardless of selection, which is correct because live ALSO
        builds HA candles + EMA for every universe symbol every candle — the
        ONLY thing live gates on selection is whether the ENTRY ACTION is taken,
        not whether the candle/EMA/evaluator advance. We replicate that: state
        always advances; the SELECTION gate is applied at entry-decision time in
        the runner, not here."""
        ha = self.ha_converter.update(
            ts=int(b["ts"]),
            o=float(b["open"]), h=float(b["high"]),
            l=float(b["low"]), c=float(b["close"]),
        )
        ema_val = self._ema_low.update(ha.low)
        self.ema_low_value = ema_val
        self.last_ha = ha
        signal = self.evaluator.push(ha, ema_val)
        return ha, ema_val, signal


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run_ha_backtest(
    *,
    db_path: str,
    strategy_id: str,           # "HA_V1"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Public entry — mutes audit logging for the duration of the replay, then
    delegates to the implementation. See _run_ha_backtest_impl for full docs.

    WHY MUTE: write_audit_log opens+closes the daily log file on EVERY call and
    the HA signal engine logs on every candle/rejection. Over a multi-year run
    that is hundreds of thousands of file open/close cycles on the hot path AND
    it pollutes TODAY's live audit log with mis-dated replay lines (the logger
    rotates on wall-clock date, not the simulated date). Muting is a process-
    wide no-op flag that ONLY the backtest sets, restored via context manager on
    every exit path — live logging is never affected.
    """
    # ── AUDIT_MUTE BEGIN ──
    from app.event_bus.audit_logger import audit_muted
    with audit_muted():
        return _run_ha_backtest_impl(
            db_path=db_path, strategy_id=strategy_id, underlying=underlying,
            date_from=date_from, date_to=date_to,
            config_override=config_override,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )
    # ── AUDIT_MUTE END ──


def _run_ha_backtest_impl(
    *,
    db_path: str,
    strategy_id: str,           # "HA_V1"
    underlying: str,            # "NIFTY"
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    """Run an HA_V1 backtest over the corpus.

    config keys (all optional, sane defaults):
      option_premium: {min, max}   selection band (HA's own band, applied to
                                    the SCALP_V1-style selection timeline)
      risk_reward_ratio            TP = entry + (entry-sl)*RR   (default 2.0)
      target_override: {enabled, points}   fixed TP = entry + points
      session: {primary:{start,end}}        IST HH:MM strings
      quantity: {lots}
      trade_side_mode              "BOTH" | "CE" | "PE"
      max_trades_per_side          daily per-side entry cap (default 10)
      min_sl_points                minimum SL distance (pts); 0 = disabled
      entry_conditions             list — subset of ["COND1","COND2","COND3"];
                                   absent/empty = ALL (back-compat)
      cond1_flip_side              bool — COND1-ONLY opposite-side experiment:
                                   a CE signal buys the snapshot's selected PE
                                   (and vice versa), risk transferred in points
                                   from the signal contract, TP from the flip
                                   entry. Composes with cond1_retrace (flip
                                   first, then the limit arms on the flipped
                                   contract). Default OFF → legacy behaviour.
      cond1_retrace                {enabled, frac, ttl_bars} — COND1-ONLY limit
                                   retrace entry: arm a limit at
                                   entry - frac*(entry-sl) (frac default 0.5),
                                   live for ttl_bars 1m bars (default 5), fill
                                   on bar LOW touch, TP recomputed from fill.
                                   COND2/COND3 entries are untouched. Default
                                   DISABLED → legacy bit-identical.
      condition_windows            OPTIONAL {COND1:{start,end},...} per-cond
                                   entry windows (HH:MM). Absent cond → global
                                   session; windows only NARROW the session.
      max_trades_per_day           OPTIONAL total entries/day across BOTH sides
                                   (independent of max_trades_per_side; both
                                   apply). 0/absent = disabled.
      max_loss, max_profit         PER-DAY MTM caps (NET ₹); 0 = disabled

    SELECTION + ARBITRATION replayed exactly as live SCALP_V1 selection (see
    header): per-day 120s timeline via backtest_selector.py, membership gate on
    the active snapshot, same-1m-candle highest-premium election across sides.
    SINGLE GLOBAL open trade (target live behaviour).
    """
    from app.engine.ha_options.ha_signal_engine import HASignalEngine
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.backtest_selector import (
        build_selection_timeline, active_snapshot_for_ts,
    )
    charges_for_long_trade = _resolve_charges_fn()

    cfg = config_override or {}
    prem = cfg.get("option_premium", {}) or {}
    prem_min = float(prem.get("min", 0) or 0)
    prem_max = float(prem.get("max", 1e9) or 1e9)
    rr = float(cfg.get("risk_reward_ratio", 2.0) or 2.0)
    override = cfg.get("target_override", {}) or {}
    override_on = bool(override.get("enabled")) and float(override.get("points", 0) or 0) > 0
    override_pts = float(override.get("points", 0) or 0)
    lots = int((cfg.get("quantity", {}) or {}).get("lots", 1) or 1)
    qty = lots * LOT_SIZE
    sess = ((cfg.get("session", {}) or {}).get("primary", {}) or {})
    sess_start = sess.get("start", "09:15")
    sess_end = sess.get("end", "15:20")
    side_mode = (cfg.get("trade_side_mode", "BOTH") or "BOTH").upper()
    max_trades_per_side = int(cfg.get("max_trades_per_side", 10) or 10)
    max_loss = abs(float(cfg.get("max_loss", 0) or 0))
    max_profit = abs(float(cfg.get("max_profit", 0) or 0))
    # ── MIN_SL_GATE BEGIN ── minimum SL distance (points). 0 = disabled. An
    # entry whose (entry_ltp - sl) risk distance is below this is rejected —
    # matches the live ha_tick_engine MIN SL gate. Guards against sub-rupee SLs
    # where charges exceed any realistic profit.
    min_sl = abs(float(cfg.get("min_sl_points", 0) or 0))
    # ── MIN_SL_GATE END ──
    # ── HA_COND1_RETRACE BEGIN ── COND1-only limit-retrace entry.
    # cond1_retrace: {enabled, frac, ttl_bars}. Default DISABLED → the runner is
    # bit-identical to legacy behaviour for every config that omits the key.
    #
    # WHY (C1-only backtest, 2020–2026): COND1's EMA touch happens on the RED
    # candle (N-1); entry fires only after the GREEN confirm candle closes — one
    # bar later, at an extended premium, with SL still pegged at the red low.
    # 72 of 528 trades stopped out within 5 minutes (-3.42L of the -4.14L total):
    # the classic buy-the-top-of-a-dead-cat-bounce signature. COND2/COND3 enter
    # AT the EMA touch and are unaffected by (and excluded from) this feature.
    #
    # MECHANICS: when a COND1 signal wins arbitration, do NOT enter at market.
    # Arm a resting LIMIT at
    #     limit = entry_ltp - frac * (entry_ltp - sl)        (frac default 0.5)
    # i.e. "only fill if price gives back half the confirm bounce". The order
    # lives ttl_bars 1m bars (default 5), then cancels. Fill model matches the
    # exit model exactly — pure level-touch: a later bar's LOW <= limit fills AT
    # the limit. TP is recomputed from the ACTUAL fill (fill + (fill-sl)*RR, or
    # override points from fill) — that is half the point of the fix: the same
    # RR from a lower entry is an absolute target the move can actually reach.
    # SL stays the signal's red-candle low, untouched.
    #
    # SEMANTICS (locked decisions D1–D6):
    #   * confirm_entry() fires on FILL, not on arming — an expired unfilled
    #     order never consumes a per-side daily slot. can_enter() is re-checked
    #     at fill time in case day state moved.
    #   * A NEW winning signal (any condition) REPLACES a pending order —
    #     latest information wins; a stale limit never starves fresh entries.
    #   * Fill bar low <= sl too → price-wise the fill definitely occurred
    #     (sl < limit) but touch ORDER is unknown → pessimistic: book the SL
    #     exit at the SL level, ambiguous=True (existing convention).
    #   * TP is NEVER granted on the fill bar itself (the bar's high may have
    #     printed before the retrace touch) — pessimistic, trade stays open.
    #   * Pending survives selection-snapshot churn (a live resting limit order
    #     stays working at the broker regardless of selection) but dies at
    #     session end, on day-cap block, and at day end.
    _c1r = cfg.get("cond1_retrace", {}) or {}
    c1r_on = bool(_c1r.get("enabled"))
    c1r_frac = float(_c1r.get("frac", 0.5) or 0.5)
    c1r_ttl = int(_c1r.get("ttl_bars", 5) or 5)
    if c1r_frac <= 0 or c1r_frac >= 1:
        c1r_frac = 0.5          # fail-closed to the sane default
    if c1r_ttl <= 0:
        c1r_ttl = 5
    # ── HA_COND1_RETRACE END ──
    # ── HA_COND1_FLIP BEGIN ── COND1-only OPPOSITE-SIDE entry experiment.
    # cond1_flip_side: bool. Default OFF → bit-identical legacy behaviour.
    #
    # WHAT: when a COND1 signal fires on side X, do NOT trade the signalling
    # contract — trade the SAME selection snapshot's OPPOSITE-side contract
    # (CE signal → buy the selected PE, and vice versa). The bet inverts from
    # "the EMA bounce continues" to "the bounce fails".
    #
    # MECHANICS (flip happens at CANDIDATE CONSTRUCTION, before arbitration,
    # so retrace arming / fills / exits all operate on the flipped contract
    # unchanged — the two features compose):
    #   * Flip target = opposite-side symbols in the ACTIVE snapshot that have
    #     a bar in THIS 1m bucket; if several, highest bar-close premium wins,
    #     symbol tie-break (the arbitration convention). No bar → candidate
    #     dropped (rej_flip_no_bar).
    #   * RISK TRANSFERS IN POINTS: risk = signal entry − signal red-low (the
    #     red-low is a level on the SIGNAL contract and means nothing on the
    #     flipped one). flip_sl = flip_entry − risk; TP from flip_entry at the
    #     configured RR (or override points). Both bands share the premium
    #     selection band, so point-parity is comparable across sides.
    #   * side_mode and the per-side daily cap are RE-CHECKED against the
    #     FLIPPED side (the earlier gates validated the signal side).
    # HONESTY NOTE: with C1 measuring ≈ -0.04R (a paid coin flip), theory says
    # the flip is ≈ the same coin flip minus charges — this switch exists to
    # let the DATA say so. It is an experiment knob, not a recommended mode.
    c1flip = bool(cfg.get("cond1_flip_side"))
    # ── HA_COND1_FLIP END ──
    # ── HA_COND_FILTER BEGIN ── entry-condition multi-select. Applied at the
    # runner's entry-decision point ONLY (below, next to the other entry gates):
    # the evaluator / EMA / HA state and the live HASignalEngine are untouched,
    # so state advance is candle-for-candle identical whatever the selection.
    # Filtered signals never reach arbitration or confirm_entry, so the per-side
    # daily counters stay correct automatically.
    enabled_conditions = _parse_enabled_conditions(cfg)
    # ── HA_COND_FILTER END ──
    # ── HA_COND_WINDOWS BEGIN ── OPTIONAL per-condition entry time windows.
    # condition_windows: { COND1: {start,end}, ... } — HH:MM strings. A
    # condition WITHOUT a (complete) window entry falls back to the GLOBAL
    # session, so an absent/empty key is bit-identical to legacy behaviour.
    # A window only ever NARROWS: it is checked IN ADDITION to the global
    # session gate, never instead of it. Purpose (validated on the 2020-2026
    # corpus via CSV composite): C2's edge lives in the opening half hour,
    # C1-flip's edge after 10:00; C3 earns across the session.
    _cw_raw = cfg.get("condition_windows", {}) or {}
    cond_windows = {}
    for _c in ("COND1", "COND2", "COND3"):
        try:
            _w = _cw_raw.get(_c) or {}
            _ws, _we = _w.get("start"), _w.get("end")
            if _ws and _we and str(_ws) <= str(_we):
                cond_windows[_c] = (str(_ws), str(_we))
        except Exception:
            pass          # malformed entry → fall back to global session
    # ── HA_COND_WINDOWS END ──
    # ── HA_DAILY_CAP BEGIN ── OPTIONAL total-trades-per-day cap (ACROSS both
    # sides — the existing max_trades_per_side stays independent and both
    # apply). 0 / absent = disabled = legacy behaviour. The counter increments
    # where an entry is COMMITTED (immediate entry election, retrace fill) —
    # an expired/cancelled pending order never consumes the day. With
    # max_trades_per_day=1 this implements the strict two-phase rule: once the
    # day's trade is taken, every later signal (any condition) is refused.
    try:
        max_trades_day = int(cfg.get("max_trades_per_day", 0) or 0)
    except Exception:
        max_trades_day = 0
    if max_trades_day < 0:
        max_trades_day = 0
    # ── HA_DAILY_CAP END ──

    # The selection timeline ALWAYS selects BOTH sides (so either side can be a
    # candidate); trade_side_mode gates the traded side at entry below — exactly
    # the live split (selection is side-agnostic; HA's trade_side_mode gates the
    # entry action). Premium band is HA's own band.
    sel_cfg = {
        "option_premium": {"min": prem_min, "max": prem_max},
        "trade_side_mode": "BOTH",
    }

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    src = CandleSource(db_path)

    # Sim days that have NIFTY option data in range.
    lo_all, hi_all = _day_bounds(date_from)[0], _day_bounds(date_to)[1]
    rows = cur.execute(
        """
        SELECT DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes') AS d
        FROM backtest_candles_1m
        WHERE underlying = ? AND instrument_type IN ('CE','PE')
          AND ts >= ? AND ts < ?
        ORDER BY d
        """,
        (underlying, lo_all, hi_all),
    ).fetchall()
    sim_days = [date.fromisoformat(r["d"]) for r in rows]
    if not sim_days:
        conn.close()
        try:
            src.close()
        except Exception:
            pass
        return {"run_id": None, "aborted": True,
                "reason": f"no {underlying} option data in range",
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    trades: List[HATrade] = []
    total_days = len(sim_days)

    # Diagnostics — explains a sparse result, selection-aware like V5.
    _diag = {
        "sim_days": total_days, "days_with_data": 0, "days_uncovered": 0,
        "contracts_seen": 0, "signals": 0, "accepted": 0,
        "arb_contests": 0, "arb_dropped": 0,
        "rej_single_gate": 0, "rej_session": 0, "rej_side_mode": 0,
        "rej_not_selected": 0, "rej_mtm_block": 0, "rej_cap": 0,
        "rej_sl_ge_ltp": 0, "rej_no_sl": 0, "rej_ema_warmup": 0,
        "rej_min_sl": 0, "rej_condition": 0,
        "mtm_exits": 0, "day_mtm_blocked": 0,
        "prem_seen_min": None, "prem_seen_max": None,
        # ── HA_COND1_RETRACE BEGIN ── funnel: armed → filled | expired |
        # replaced | cancelled (session/day-cap/cap-recheck). fillbar_sl counts
        # fills whose own bar also touched SL (booked SL, ambiguous=True).
        "retrace_armed": 0, "retrace_filled": 0, "retrace_expired": 0,
        "retrace_replaced": 0, "retrace_cancelled": 0, "retrace_fillbar_sl": 0,
        # ── HA_COND1_RETRACE END ──
        # ── HA_COND1_FLIP ── flip funnel (all zero when disabled)
        "flip_applied": 0, "rej_flip_no_bar": 0,
        "rej_flip_side_mode": 0, "rej_flip_cap": 0,
        # ── HA_COND_WINDOWS / HA_DAILY_CAP ── (zero when features off)
        "rej_cond_window": 0, "rej_day_cap": 0,
    }

    for di, d in enumerate(sim_days, start=1):
        if cancel_cb and cancel_cb():
            break

        lo, hi = _day_bounds(d)

        # ── MTM_DAY_RESET BEGIN ───────────────────────────────────
        # PER-DAY realised NET total for the Max Loss/Profit caps. Reset every
        # day to match the live guard, whose limit is "today's realised P&L"
        # (strategy_max_loss_guard.today_realised_pnl resets at midnight). The
        # previous code accumulated across the WHOLE run, so a multi-day cap
        # behaved as a lifetime cap and stuck breached forever. day_blocked
        # gates NEW entries for the rest of THIS day once realised crosses the
        # limit (entry-gate parity); it clears next day.
        realised_running = 0.0          # today's realised NET (charge-deducted)
        day_blocked = False             # True → block new entries rest of day
        # ── MTM_DAY_RESET END ─────────────────────────────────────

        # ── Per-day 120s SELECTION TIMELINE (reuses the SCALP_V1 selector). ──
        # ── HA_PRELOAD_SCOPE ── expiry-scoped timeline (see selector for the
        # equivalence proof). Profiled: unscoped preload_day was 61% of the
        # runner's wall clock on a 2-expiry synthetic corpus.
        timeline = build_selection_timeline(
            src=src, underlying=underlying, day_start_epoch=lo,
            cfg=sel_cfg, strategy_id=strategy_id,
            scope_to_expected_expiry=True,
        )
        if not timeline.get("covered"):
            _diag["days_uncovered"] += 1
            continue

        # WATCH ONLY THE SELECTED UNION (matches live: HA only ever evaluates
        # signals for the currently-selected CE/PE, drawn from this selection).
        watched = timeline.get("all_symbols") or set()
        if not watched:
            continue
        _diag["days_with_data"] += 1
        current_expiry = timeline.get("expected_expiry")

        # Per-symbol meta (strike / side / expiry) from the day's universe.
        # ── HA_PRELOAD_SCOPE ── same expiry scope as the timeline → served
        # from the already-loaded cache, no second preload. Equivalent: every
        # symbol the runner reads from meta_map (watched signals, flip targets,
        # retrace fills) comes from the selection, which is want-expiry only.
        meta_map = {
            c["tradingsymbol"]: {"side": c["instrument_type"], "strike": float(c["strike"])}
            for c in src.contracts_active_on_day(
                underlying, lo, expiry=timeline.get("expected_expiry"))
        }

        # Fresh per-day signal engine — drives the REAL per-side daily counter
        # and in-trade flags (live counting). reset each day = daily reset.
        signal_engine = HASignalEngine(max_trades_per_side=max_trades_per_side)

        # Build per-watched-symbol HA state, warmed on prior-day 1m bars.
        states: Dict[str, _HAState] = {}
        one_min_by_sym: Dict[str, List[dict]] = {}

        for sym in sorted(watched):
            day_candles = src.candles_1m_for_symbol_day(sym, lo)
            if not day_candles:
                continue
            if sym not in meta_map:
                continue

            bars_1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in day_candles]

            st = _HAState(sym)
            # warmup: prior-day 1m bars (mirrors live warmup_from_db continuity).
            warm = src.warmup_candles_before(sym, day_candles[0].ts, WARMUP_CANDLES)
            if warm:
                w1m = [{"ts": int(c.ts), "open": float(c.open), "high": float(c.high),
                        "low": float(c.low), "close": float(c.close)} for c in warm]
                st.warmup(w1m)

            states[sym] = st
            one_min_by_sym[sym] = bars_1m

        if not states:
            continue
        _diag["contracts_seen"] += len(states)

        # Group the day's 1m bars across watched contracts by ts, so same-candle
        # arbitration runs per 1-minute bucket.
        by_bucket: Dict[int, List[Tuple[str, dict]]] = {}
        for sym, bars in one_min_by_sym.items():
            for b in bars:
                by_bucket.setdefault(b["ts"], []).append((sym, b))
        ordered_buckets = sorted(by_bucket.keys())

        open_trade: Optional[HATrade] = None

        # ── HA_COND1_RETRACE BEGIN ── per-day pending limit order (max ONE,
        # mirroring the single-global-trade model). Dies with the day.
        pending_c1: Optional[dict] = None
        # ── HA_COND1_RETRACE END ──
        # ── HA_DAILY_CAP ── total entries committed today (both sides).
        day_trades = 0
        # ── HA_DAILY_CAP END ──

        for bucket_start in ordered_buckets:
            if cancel_cb and cancel_cb():
                break

            # ── HA_COND1_RETRACE BEGIN ── TTL expiry + session-end cancel,
            # checked ONCE per 1m bucket before any symbol work. Eligible fill
            # bars are strictly AFTER the arming bar: armed_ts + 1..ttl bars.
            if pending_c1 is not None:
                if bucket_start > pending_c1["armed_ts"] + c1r_ttl * TIMEFRAME_SEC:
                    _diag["retrace_expired"] += 1
                    pending_c1 = None
                elif not _in_session(bucket_start + TIMEFRAME_SEC, sess_start, sess_end):
                    _diag["retrace_cancelled"] += 1
                    pending_c1 = None
                elif day_blocked:
                    _diag["retrace_cancelled"] += 1
                    pending_c1 = None
            # ── HA_COND1_RETRACE END ──

            items = sorted(by_bucket[bucket_start], key=lambda t: t[0])
            # ── HA_COND1_FLIP ── O(1) bar lookup for the flip target within
            # this same 1m bucket (items is exactly this bucket's bars).
            bucket_bars = dict(items)

            # Selection snapshot in effect at this 1m candle's CLOSE (bar end).
            snap_end_ts = bucket_start + TIMEFRAME_SEC
            snap = active_snapshot_for_ts(timeline, snap_end_ts)
            sel_ce = _snapshot_symbols(snap, "CE")
            sel_pe = _snapshot_symbols(snap, "PE")
            locked_sym = open_trade.symbol if open_trade is not None else None

            entry_candidates: List[Tuple[float, str, dict]] = []

            for sym, b1 in items:
                st = states[sym]

                # Advance HA + EMA + evaluator for EVERY watched symbol every
                # bar (live builds candles/EMA universe-wide regardless of
                # selection — only the ENTRY ACTION is selection-gated).
                ha, ema_val, signal = st.on_bar(b1)

                # ── Held contract → MTM force-close (parity w/ risk_mtm_guard),
                #    then intrabar TP (1m high) then SL (1m close) ──
                if open_trade is not None and open_trade.symbol == sym:
                    # ── MTM_EXIT BEGIN ── full-parity mid-bar force close.
                    # Live risk_mtm_guard closes the OPEN trade the instant
                    # realised(net) + open-leg GROSS MTM crosses the limit. It
                    # uses gross open MTM (the open leg isn't pre-charged; the
                    # charge lands only when the trade actually closes). We probe
                    # at the bar CLOSE price (the pessimistic, candle-close basis
                    # HA already uses for SL).
                    if (max_loss > 0 or max_profit > 0):
                        open_gross = (float(b1["close"]) - open_trade.entry_price) * open_trade.qty
                        mtm_now = realised_running + open_gross
                        if (max_loss > 0 and mtm_now <= -max_loss) or \
                           (max_profit > 0 and mtm_now >= max_profit):
                            reason = "MAX_LOSS" if (max_loss > 0 and mtm_now <= -max_loss) else "MAX_PROFIT"
                            _close_trade(
                                open_trade, exit_ts=int(b1["ts"]) + TIMEFRAME_SEC,
                                exit_price=float(b1["close"]), reason=reason,
                                charges_fn=charges_for_long_trade,
                            )
                            realised_running += (open_trade.net or 0.0)
                            _diag["mtm_exits"] += 1
                            signal_engine.notify_exit(open_trade.side)
                            trades.append(open_trade)
                            open_trade = None
                            locked_sym = None
                            day_blocked = True   # block new entries rest of day
                            break
                    # ── MTM_EXIT END ──
                    exited = _try_intrabar_exit(open_trade, b1, charges_for_long_trade)
                    if exited:
                        # accumulate NET (charge-deducted) — matches live realised
                        realised_running += (open_trade.net or 0.0)
                        # mirror live: clear the side's in-trade flag on exit
                        signal_engine.notify_exit(open_trade.side)
                        trades.append(open_trade)
                        open_trade = None
                        locked_sym = None
                        # entry-gate parity: if today's realised now crosses the
                        # limit, block new entries for the rest of the day.
                        if _day_cap_hit(realised_running, max_loss, max_profit):
                            day_blocked = True
                            break

                # ── HA_COND1_RETRACE BEGIN ── pending-limit FILL check for this
                # symbol's bar. Runs after the held-trade block, before signal
                # evaluation. Pure level-touch, exit-model parity: bar LOW <=
                # limit → fill AT the limit. Only bars strictly after the arming
                # bar are eligible.
                if (
                    pending_c1 is not None
                    and open_trade is None
                    and sym == pending_c1["symbol"]
                    and int(b1["ts"]) > pending_c1["armed_ts"]
                    and float(b1["low"]) <= pending_c1["limit"]
                ):
                    # Fill-time cap re-check (confirm_entry deferred to fill —
                    # day state may have moved since arming).
                    _ok, _why = signal_engine.can_enter(pending_c1["side"])
                    # ── HA_DAILY_CAP ── fill-time re-check, symmetric with the
                    # per-side re-check: the day may have filled up while the
                    # limit was resting.
                    if _ok and max_trades_day > 0 and day_trades >= max_trades_day:
                        _ok, _why = False, "day cap reached"
                    # ── HA_DAILY_CAP END ──
                    if not _ok:
                        _diag["retrace_cancelled"] += 1
                        pending_c1 = None
                    else:
                        fill_px = pending_c1["limit"]
                        f_sl = pending_c1["sl"]
                        f_risk = fill_px - f_sl
                        f_tp = (fill_px + override_pts) if override_on \
                            else (fill_px + f_risk * rr)
                        signal_engine.confirm_entry(pending_c1["side"])
                        day_trades += 1   # ── HA_DAILY_CAP ── committed on FILL
                        _diag["retrace_filled"] += 1
                        _diag["accepted"] += 1
                        open_trade = HATrade(
                            side=pending_c1["side"], symbol=sym,
                            strike=pending_c1["strike"],
                            entry_ts=int(b1["ts"]) + TIMEFRAME_SEC,
                            entry_price=fill_px,
                            sl=f_sl, tp=f_tp, qty=qty,
                            condition="COND1",
                            instrument_type=pending_c1["side"],
                            expiry=current_expiry, direction="LONG",
                        )
                        locked_sym = sym
                        pending_c1 = None
                        # Fill-bar SL ambiguity: low <= sl (< limit) means the
                        # fill definitely occurred price-wise but touch ORDER is
                        # unknown → pessimistic SL at the level, flagged. TP is
                        # never granted on the fill bar (high may predate fill).
                        if float(b1["low"]) <= f_sl:
                            open_trade.ambiguous = True
                            _diag["retrace_fillbar_sl"] += 1
                            _close_trade(
                                open_trade,
                                exit_ts=int(b1["ts"]) + TIMEFRAME_SEC,
                                exit_price=f_sl, reason="SL",
                                charges_fn=charges_for_long_trade,
                            )
                            realised_running += (open_trade.net or 0.0)
                            signal_engine.notify_exit(open_trade.side)
                            trades.append(open_trade)
                            open_trade = None
                            locked_sym = None
                            if _day_cap_hit(realised_running, max_loss, max_profit):
                                day_blocked = True
                                break
                # ── HA_COND1_RETRACE END ──

                # ── Entry-signal evaluation (only when flat — global gate) ──
                if not signal.should_enter:
                    continue

                _diag["signals"] += 1

                # ENTRY price = raw 1m close at signal (the live entry_ltp the
                # selected symbol's candle passes to trade_manager.enter()).
                entry_ltp = float(b1["close"])
                if _diag["prem_seen_min"] is None or entry_ltp < _diag["prem_seen_min"]:
                    _diag["prem_seen_min"] = round(entry_ltp, 2)
                if _diag["prem_seen_max"] is None or entry_ltp > _diag["prem_seen_max"]:
                    _diag["prem_seen_max"] = round(entry_ltp, 2)

                # ── HA_COND_FILTER BEGIN ── entry-condition multi-select gate.
                # signal.condition is exactly COND1|COND2|COND3 from the REAL
                # evaluator. Filtered FIRST among the entry gates so the
                # rejection attribution is clean: a disabled condition never
                # inflates rej_single_gate/rej_session/etc. Default = all three
                # (see _parse_enabled_conditions), so legacy configs are
                # bit-identical to pre-feature behaviour.
                if signal.condition not in enabled_conditions:
                    _diag["rej_condition"] += 1
                    continue
                # ── HA_COND_FILTER END ──

                # ── HA_COND_WINDOWS ── optional per-condition window: checked
                # with the SAME clock (snap_end_ts) and comparator as the
                # global session gate below, which still applies after this —
                # a window can only narrow, never widen, the session.
                _cwin = cond_windows.get(signal.condition)
                if _cwin is not None and not _in_session(snap_end_ts, _cwin[0], _cwin[1]):
                    _diag["rej_cond_window"] += 1
                    continue
                # ── HA_COND_WINDOWS END ──

                # Global single-trade gate: already holding → reject.
                if open_trade is not None:
                    _diag["rej_single_gate"] += 1
                    continue

                # ── HA_DAILY_CAP ── optional total-per-day cap (0 = off).
                if max_trades_day > 0 and day_trades >= max_trades_day:
                    _diag["rej_day_cap"] += 1
                    continue
                # ── HA_DAILY_CAP END ──

                if not _in_session(snap_end_ts, sess_start, sess_end):
                    _diag["rej_session"] += 1
                    continue

                side = meta_map[sym]["side"]
                if side_mode in ("CE", "PE") and side_mode != side:
                    _diag["rej_side_mode"] += 1
                    continue

                # membership gate on the active snapshot (own side) + lock carve-out
                in_selected = (sym in sel_ce) if side == "CE" else (sym in sel_pe)
                if not in_selected and sym != locked_sym:
                    _diag["rej_not_selected"] += 1
                    continue

                # per-side daily cap + in-trade flag (REAL live signal engine).
                allowed, _reason = signal_engine.can_enter(side)
                if not allowed:
                    _diag["rej_cap"] += 1
                    continue

                # SL must exist (a prior red candle) and be below entry — live
                # SKIPs when sl_price is None or sl >= ltp.
                if signal.sl_price is None:
                    _diag["rej_no_sl"] += 1
                    continue
                if signal.sl_price >= entry_ltp:
                    _diag["rej_sl_ge_ltp"] += 1
                    continue

                # ── MIN_SL_GATE BEGIN ── reject sub-floor SL distance.
                if min_sl > 0 and (entry_ltp - float(signal.sl_price)) < min_sl:
                    _diag["rej_min_sl"] += 1
                    continue
                # ── MIN_SL_GATE END ──

                # Entry-gate: today's realised P&L crossed the limit → block new
                # entries for the rest of the day (open trade already ran to its
                # own exit). Matches strategy_max_loss_guard.evaluate_strategy_risk.
                if day_blocked or _day_cap_hit(realised_running, max_loss, max_profit):
                    day_blocked = True
                    _diag["rej_mtm_block"] += 1
                    continue

                sl_price = float(signal.sl_price)
                risk = entry_ltp - sl_price
                rr_tp = entry_ltp + risk * rr
                tp_price = (entry_ltp + override_pts) if override_on else rr_tp

                # ── HA_COND1_FLIP BEGIN ── COND1-only: replace the candidate
                # with the snapshot's OPPOSITE-side contract. Runs AFTER every
                # signal-side gate and BEFORE arbitration, so the winner (and
                # any retrace arming on it) is already the flipped contract.
                if c1flip and signal.condition == "COND1":
                    opp_side = "PE" if side == "CE" else "CE"
                    # side_mode re-check against the FLIPPED side.
                    if side_mode in ("CE", "PE") and side_mode != opp_side:
                        _diag["rej_flip_side_mode"] += 1
                        continue
                    # per-side cap + in-trade flag re-check for the FLIPPED side.
                    _fok, _freason = signal_engine.can_enter(opp_side)
                    if not _fok:
                        _diag["rej_flip_cap"] += 1
                        continue
                    # Flip target: opposite-side snapshot members with a bar in
                    # THIS bucket. Highest close premium, symbol tie-break (the
                    # arbitration convention) — deterministic.
                    _opp_sel = sel_pe if side == "CE" else sel_ce
                    _best = None
                    for _os in _opp_sel:
                        _ob = bucket_bars.get(_os)
                        if _ob is None or _os not in meta_map:
                            continue
                        _key = (float(_ob["close"]), _os)
                        if _best is None or _key > _best[0]:
                            _best = (_key, _os, _ob)
                    if _best is None:
                        _diag["rej_flip_no_bar"] += 1
                        continue
                    _, flip_sym, flip_bar = _best
                    flip_entry = float(flip_bar["close"])
                    # RISK TRANSFERS IN POINTS (signal red-low is meaningless
                    # on the flipped contract). Same risk → MIN_SL_GATE parity
                    # holds automatically; flip_sl < flip_entry since risk > 0.
                    flip_sl = flip_entry - risk
                    flip_tp = (flip_entry + override_pts) if override_on \
                        else (flip_entry + risk * rr)
                    _diag["flip_applied"] += 1
                    entry_candidates.append((flip_entry, flip_sym, {
                        "side": opp_side, "strike": meta_map[flip_sym]["strike"],
                        "entry_ts": snap_end_ts, "entry_price": flip_entry,
                        "sl": flip_sl, "tp": flip_tp,
                        "condition": "COND1",
                    }))
                    continue
                # ── HA_COND1_FLIP END ──

                entry_candidates.append((entry_ltp, sym, {
                    "side": side, "strike": meta_map[sym]["strike"],
                    "entry_ts": snap_end_ts, "entry_price": entry_ltp,
                    "sl": sl_price, "tp": tp_price,
                    "condition": signal.condition,
                }))

            # same-candle arbitration across BOTH sides: highest entry premium,
            # symbol tie-break (identical to V5/V3). One winner enters.
            if open_trade is None and entry_candidates:
                if len(entry_candidates) > 1:
                    _diag["arb_contests"] += 1
                    _diag["arb_dropped"] += (len(entry_candidates) - 1)
                winner = max(entry_candidates, key=lambda c: (c[0], c[1]))
                _ep, _sym, ctx = winner
                # ── HA_COND1_RETRACE BEGIN ── winner routing.
                # A NEW winner (any condition) supersedes a stale pending order:
                # latest information wins, a resting limit never starves fresh
                # entries. COND1 + feature on → ARM a limit, no confirm_entry,
                # no accepted++ (both land on FILL). Everything else → legacy
                # immediate entry, bit-identical.
                if pending_c1 is not None:
                    _diag["retrace_replaced"] += 1
                    pending_c1 = None
                if c1r_on and ctx["condition"] == "COND1":
                    _risk = ctx["entry_price"] - ctx["sl"]
                    pending_c1 = {
                        "symbol": _sym,
                        "side": ctx["side"],
                        "strike": ctx["strike"],
                        "limit": round(ctx["entry_price"] - c1r_frac * _risk, 2),
                        "sl": ctx["sl"],
                        "armed_ts": bucket_start,
                    }
                    _diag["retrace_armed"] += 1
                else:
                    # ── HA_COND1_RETRACE END ── (legacy path below, unchanged)
                    _diag["accepted"] += 1
                    # commit to the REAL signal engine (per-side counter + flag),
                    # exactly as live confirm_entry does on a fired signal.
                    signal_engine.confirm_entry(ctx["side"])
                    day_trades += 1   # ── HA_DAILY_CAP ── committed entry
                    open_trade = HATrade(
                        side=ctx["side"], symbol=_sym, strike=ctx["strike"],
                        entry_ts=ctx["entry_ts"], entry_price=ctx["entry_price"],
                        sl=ctx["sl"], tp=ctx["tp"], qty=qty,
                        condition=ctx["condition"],
                        instrument_type=ctx["side"], expiry=current_expiry,
                        direction="LONG",
                    )

            if open_trade is None and (day_blocked or _day_cap_hit(realised_running, max_loss, max_profit)):
                day_blocked = True
                _diag["day_mtm_blocked"] += 1
                break

        # EOD square-off the still-open trade at the held contract's last close
        # (live ha_trade_manager.eod_squareoff exits the open side).
        if open_trade is not None:
            day_bars = one_min_by_sym.get(open_trade.symbol)
            if day_bars:
                last = day_bars[-1]
                _close_trade(open_trade, exit_ts=int(last["ts"]) + TIMEFRAME_SEC,
                             exit_price=float(last["close"]), reason="EOD",
                             charges_fn=charges_for_long_trade)
                realised_running += (open_trade.net or 0.0)   # NET (matches live)
                signal_engine.notify_exit(open_trade.side)
                trades.append(open_trade)
            open_trade = None

        if progress_cb:
            progress_cb({"day": di, "total_days": total_days, "date": d.isoformat(),
                         "trades": len(trades)})

    try:
        src.close()
    except Exception:
        pass
    conn.close()

    summary = _summarize(trades)
    summary["diagnostics"] = _diag
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(
            "[BACKTEST][HA][DIAG] "
            f"days={_diag['sim_days']} with_data={_diag['days_with_data']} "
            f"uncovered={_diag['days_uncovered']} "
            f"contracts={_diag['contracts_seen']} signals={_diag['signals']} "
            f"accepted={_diag['accepted']} arb_contests={_diag['arb_contests']} "
            f"arb_dropped={_diag['arb_dropped']} "
            f"conds={'+'.join(sorted(enabled_conditions))} | rejected: "
            f"condition={_diag['rej_condition']} "
            f"single_gate={_diag['rej_single_gate']} session={_diag['rej_session']} "
            f"side_mode={_diag['rej_side_mode']} not_selected={_diag['rej_not_selected']} "
            f"cap={_diag['rej_cap']} no_sl={_diag['rej_no_sl']} "
            f"sl_ge_ltp={_diag['rej_sl_ge_ltp']} min_sl={_diag['rej_min_sl']} "
            f"mtm_block={_diag['rej_mtm_block']} mtm_exits={_diag['mtm_exits']} | "
            # ── HA_COND1_RETRACE BEGIN ── funnel (all zero when disabled)
            f"c1_retrace: armed={_diag['retrace_armed']} "
            f"filled={_diag['retrace_filled']} expired={_diag['retrace_expired']} "
            f"replaced={_diag['retrace_replaced']} "
            f"cancelled={_diag['retrace_cancelled']} "
            f"fillbar_sl={_diag['retrace_fillbar_sl']} | "
            # ── HA_COND1_RETRACE END ──
            # ── HA_COND1_FLIP ── (all zero when disabled)
            f"c1_flip: applied={_diag['flip_applied']} "
            f"no_bar={_diag['rej_flip_no_bar']} "
            f"side_mode={_diag['rej_flip_side_mode']} "
            f"cap={_diag['rej_flip_cap']} | "
            # ── HA_COND_WINDOWS / HA_DAILY_CAP ── (zero when features off)
            f"cond_window={_diag['rej_cond_window']} "
            f"day_cap={_diag['rej_day_cap']} | "
            f"signal_premium_seen={_diag['prem_seen_min']}..{_diag['prem_seen_max']}"
        )
    except Exception:
        pass

    import uuid as _uuid
    return {
        "run_id": str(_uuid.uuid4()),
        "strategy_id": strategy_id,
        "config": cfg,
        "summary": summary,
        # Return trade OBJECTS (not dicts): persist_run reads attributes
        # (t.symbol, t.entry_ts, t.pnl, t.ambiguous_fill, …).
        "trades": trades,
    }


# ----------------------------------------------------------------------
# Exit helpers
# ----------------------------------------------------------------------
def _try_intrabar_exit(trade: HATrade, bar_1m: dict, charges_fn) -> bool:
    """Exit check for the held HA trade on ONE 1m bar. PURE LEVEL-TOUCH, no
    slippage, no candle close — book AT the level the moment price touches it:
      TP → intrabar via the 1m HIGH. high >= tp → exit @ tp.
      SL → intrabar via the 1m LOW.  low  <= sl → exit @ sl.
    Ambiguous bar (high>=tp AND low<=sl) → pessimistic SL-first, flagged.
    Returns True if an exit fired (mutates trade)."""
    if trade.sl is None and trade.tp is None:
        return False

    hi = float(bar_1m["high"])
    lo = float(bar_1m["low"])
    bar_ts = int(bar_1m["ts"])

    hit_tp = trade.tp is not None and hi >= float(trade.tp)
    hit_sl = trade.sl is not None and lo <= float(trade.sl)

    if hit_tp and hit_sl:
        # Both fire on the same bar: TP is an intrabar high event, SL is a
        # close event. Live can't see both on one bar (TP would have fired on a
        # tick before the candle closed), but with only 1m bars we can't order
        # them — take the pessimistic SL and flag it.
        trade.ambiguous = True
        # SL exit price = the SL LEVEL exactly (pure level-touch, no slippage,
        # no candle close). Book at the stop the moment the low pierces it.
        _close_trade(trade, exit_ts=bar_ts + TIMEFRAME_SEC, exit_price=float(trade.sl),
                     reason="SL", charges_fn=charges_fn)
        return True
    if hit_tp:
        _close_trade(trade, exit_ts=bar_ts + TIMEFRAME_SEC, exit_price=float(trade.tp),
                     reason="TP", charges_fn=charges_fn)
        return True
    if hit_sl:
        # SL exit price = the SL LEVEL exactly (pure level-touch, no slippage,
        # no candle close).
        _close_trade(trade, exit_ts=bar_ts + TIMEFRAME_SEC, exit_price=float(trade.sl),
                     reason="SL", charges_fn=charges_fn)
        return True
    return False


# Known locations charges_for_long_trade has lived at across the tree. Mirrors
# the V5 runner's resolver so both stay in lock-step (one place to fix a move).
_CHARGES_PATHS = (
    "app.backtest.charges.charges_model",
    "app.backtest.data.charges_model",
    "app.backtest.engine.charges_model",
    "app.backtest.charges_model",
)
_CHARGES_FN = None
_CHARGES_RESOLVED = False


def _resolve_charges_fn():
    """Resolve charges_for_long_trade from whichever module path exists. Caches
    the result. Logs the resolved path, or a loud warning if none are found."""
    global _CHARGES_FN, _CHARGES_RESOLVED
    if _CHARGES_RESOLVED:
        return _CHARGES_FN
    _CHARGES_RESOLVED = True
    import importlib
    for path in _CHARGES_PATHS:
        try:
            mod = importlib.import_module(path)
            fn = getattr(mod, "charges_for_long_trade", None)
            if fn is not None:
                _CHARGES_FN = fn
                try:
                    from app.event_bus.audit_logger import write_audit_log
                    write_audit_log(f"[BACKTEST][HA][CHARGES] using {path}.charges_for_long_trade")
                except Exception:
                    pass
                return _CHARGES_FN
        except Exception:
            continue
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(
            "[BACKTEST][HA][CHARGES][WARN] charges_for_long_trade NOT FOUND in any "
            f"of {_CHARGES_PATHS} — charges will be ZERO and net P&L will equal "
            "gross. Fix the import path in backtest_ha_runner.py."
        )
    except Exception:
        pass
    _CHARGES_FN = None
    return _CHARGES_FN


def _close_trade(trade: HATrade, *, exit_ts: int, exit_price: float, reason: str, charges_fn) -> None:
    trade.exit_ts = exit_ts
    trade.exit_price = float(exit_price)
    trade.exit_reason = reason
    gross = (trade.exit_price - trade.entry_price) * trade.qty
    charges = 0.0
    if charges_fn is not None:
        try:
            res = charges_fn(entry_price=trade.entry_price, exit_price=trade.exit_price, qty=trade.qty)
            charges = float(getattr(res, "total_charges", 0.0))
            gross = float(getattr(res, "gross_pnl", gross))
        except Exception:
            charges = 0.0
    trade.gross = round(gross, 2)
    trade.charges = round(charges, 2)
    trade.net = round(gross - charges, 2)


def _day_cap_hit(realised_net_today: float, max_loss: float, max_profit: float) -> bool:
    """Entry-gate parity: True when TODAY's realised NET P&L has crossed a
    configured daily limit, so NEW entries are blocked for the rest of the day.
    Mirrors strategy_max_loss_guard.evaluate_strategy_risk (realised only).
    The mid-bar force-close (realised + open MTM) is handled inline in the held-
    trade block, mirroring risk_mtm_guard.mtm_breach_ha."""
    if max_loss > 0 and realised_net_today <= -max_loss:
        return True
    if max_profit > 0 and realised_net_today >= max_profit:
        return True
    return False


# ----------------------------------------------------------------------
# Summary + serialization (shared shape with the V5 runner)
# ----------------------------------------------------------------------
def _trade_to_dict(t: HATrade) -> dict:
    return {
        "tradingsymbol": t.symbol,
        "side": t.side,
        "strike": t.strike,
        "entry_ts": t.entry_ts,
        "entry_price": t.entry_price,
        "sl": t.sl,
        "tp": t.tp,
        "qty": t.qty,
        "condition": t.condition,
        "exit_ts": t.exit_ts,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "pnl": t.gross,
        "charges": t.charges,
        "net_pnl": t.net,
        "ambiguous_fill": t.ambiguous,
    }


def _summarize(trades: List[HATrade]) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return _empty_summary()
    nets = [t.net or 0.0 for t in closed]
    gross = sum(t.gross or 0.0 for t in closed)
    charges = sum(t.charges or 0.0 for t in closed)
    net = sum(nets)
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    amb = sum(1 for t in closed if t.ambiguous)

    eq = 0.0; peak = 0.0; mdd = 0.0
    for t in sorted(closed, key=lambda x: x.entry_ts or 0):
        eq += (t.net or 0.0)
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)

    return {
        "total_trades": len(closed),
        "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / len(closed), 2) if closed else 0.0,
        "gross_pnl": round(gross, 2),
        "total_charges": round(charges, 2),
        "net_pnl": round(net, 2),
        "max_drawdown": round(mdd, 2),
        "ambiguous_fills": amb,
    }