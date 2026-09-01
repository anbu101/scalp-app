# backend/app/backtest/brk/backtest_brk_runner.py
#
# ── BRK_V1 RUNNER ── 09:25 premium-level breakout scalp on NIFTY weeklies.
#
# Fence: BRK_V1_20260830
#
# SPEC OF RECORD (chat, 2026-08-30), decisions D1-D8 locked before code:
#   D1  at select_time (09:25) pick ONE CE and ONE PE from the expected
#       weekly expiry: the contract whose 09:25 print is the HIGHEST premium
#       still BELOW select_below (₹180) — nearest-to-level from below.
#   D2  "breaks and sustains" is CLOSE-based: the last `sustain_candles` 1m
#       closes before the decision minute are all >= break_above (₹180).
#       A wick through the level is not a break.
#   D3  decision minutes run entry_first (09:30) .. entry_last (09:35),
#       inclusive, one check per minute. The first decision minute at which
#       a side is confirmed fills at THAT minute's 1m OPEN. Nothing fills
#       before entry_first even if the level broke at 09:26.
#   D4  both sides confirmed at the same decision minute: `first` takes the
#       side whose close crossed the level earliest since selection (tie ->
#       higher premium at the decision minute); `higher` takes the dearer;
#       `skip` takes nothing.
#   D5  SL = entry - sl_pts (20); TP = entry + tp_pts (40). Both are OPTION
#       premium levels on the bought contract. SL fills AT its level
#       (stop-trigger convention), TP fills AT its level (limit). Both in
#       one minute -> SL WINS. The entry bar itself is in the exit ladder.
#   D6  EOD square-off at eod_square_off (15:15), option 1m close.
#   D7  optional trail: once a bar's high reaches entry + trail_trigger_pts
#       the stop is raised to entry + trail_lock_pts (0 = breakeven). The
#       raise takes effect from the NEXT bar (same-bar order unknowable ->
#       pessimistic). Exit on the raised stop is reason TRAIL.
#   D8  one trade per day, no re-entry. lots default 1 (NIFTY 65).
#
# ── WHY THE DIAGNOSTICS EXIST ─────────────────────────────────────────────
# One trade a day means ~1,600 trades across the corpus and every artifact
# is visible in a summary line — but the rule has a SELECTION step and a
# WAIT step that can each silently kill days. So every no-trade day is
# attributed (no candidate / no break / both-skip / no fill) and the entry
# minute histogram shows whether the 09:30 case or the 09:31-09:35 wait
# carries the result. eod_pnl_gross is the SCALP_V5 tripwire: if EOD exits
# carry most of net, the run describes the square-off, not the breakout.
#
# ── PARITY NOTES ─────────────────────────────────────────────────────────
#   P1  Selection reads the CLOSE of the 1m bar that ENDS at select_time
#       (bar ts 09:24 for 09:25) — the LTP a live engine would see at
#       09:25:00. Live must sample the same instant.
#   P2  Decision at minute m reads only bars that CLOSED at-or-before m;
#       the fill is the open of bar m. No information from an unfinished
#       bar is ever used.
#   P3  Holiday awareness via app.utils.market_hours.is_trading_day.
#   P4  Fills use a contract's OWN 1m bars. A minute with no print is a
#       stale mark, counted; it never silently becomes a zero.
#   P5  Expected-expiry only (the weekly live would trade). A day whose
#       expected expiry is absent from the corpus is SKIPPED, never
#       substituted with a farther expiry.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

IST_OFFSET = 5 * 3600 + 30 * 60

# Index lot sizes are CONSTANTS by fleet convention (see lot_sizes.py).
INDEX_LOTS = {"NIFTY": 65, "BANKNIFTY": 35}

DEFAULTS: dict = {
    # ── selection (D1) ──
    "select_time": "09:25",
    "select_below": 180.0,             # highest premium strictly below this
    "select_min": 0.0,                 # optional floor; 0 = off

    # ── breakout (D2, D3, D4) ──
    "break_above": 180.0,
    "sustain_candles": 1,              # consecutive 1m closes >= level
    "entry_first": "09:30",
    "entry_last": "09:35",
    "both_policy": "first",            # first | higher | skip

    # ── exits (D5, D6, D7) ──
    "sl_pts": 20.0,
    "tp_pts": 40.0,
    "trail_trigger_pts": 0.0,          # lock: 0 = off | ratchet: arm after +X (0 = from entry)
    "trail_lock_pts": 0.0,             # lock mode: stop -> entry + lock once triggered
    "trail_mode": "lock",              # ── BRK_V1_RATCHET_20260831 ── lock | ratchet
    "trail_gap": 0.0,                  # ── BRK_V1_RATCHET_20260831 ── ratchet: stop = max high − gap (0 = off)
    "time_stop_min": 0,                # ── BRK_V1_TIMESTOP_20260831 ── N minutes after entry (0 = off)
    "time_stop_need_pts": 0.0,         # ── BRK_V1_TIMESTOP_20260831 ── exit unless close ≥ entry + X at that minute
    "fallback_enabled": False,         # ── BRK_V1_FALLBACK_20260831 ── no break by entry_last -> buy the side that gained most
    "fallback_min_pts": 0.0,           # ── BRK_V1_FALLBACK_20260831 ── that side must be ≥ this above its 09:25 print
    "eod_square_off": "15:15",

    # ── sizing / filters (D8) ──
    "lots": 1,
    "lot_size": 0,                     # 0 = index constant
    "skip_expiry_day": False,
}


@dataclass
class BRKTrade:
    """Attribute surface matches CBOTrade / ICTrade so backtest_repo.persist_run
    works unchanged. It reads t.symbol / t.max_adverse / t.ambiguous_fill by
    ATTRIBUTE, so this must stay an OBJECT. Deliberately NO `hedge_symbol`
    attribute — its presence diverts persist_run to the V3/V4 hedge branch."""
    tradingsymbol: str
    symbol: str
    instrument_type: str
    strike: Optional[float]
    expiry: Optional[str]
    direction: str                     # always BUY
    entry_ts: int
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]         # SL | TP | TRAIL | EOD
    qty: int
    condition: str
    ambiguous_fill: bool = False
    pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    max_adverse: Optional[float] = None
    max_favorable: Optional[float] = None
    gross: float = field(default=0.0)
    net: float = field(default=0.0)
    ambiguous: bool = field(default=False)
    synthetic: bool = field(default=False)
    synth_kind: Optional[str] = field(default=None)


def _empty_summary() -> dict:
    return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "ambiguous_fills": 0}


def _hhmm(s: str, fallback: int) -> int:
    """'HH:MM' -> minutes since midnight IST; malformed -> fallback."""
    try:
        h, m = str(s).split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 24 * 60 else fallback
    except (ValueError, AttributeError):
        return fallback


def _day_start_epoch(d: date) -> int:
    """Epoch of 00:00 IST for `d`, matching the corpus's day bucketing."""
    return int((datetime(d.year, d.month, d.day)
                - datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET


def _merge_cfg(override: Optional[dict]) -> dict:
    cfg = dict(DEFAULTS)
    for k, v in (override or {}).items():
        cfg[k] = v
    # ── normalise: a bad UI value can never silently change semantics ──
    _pol = str(cfg.get("both_policy", "first")).lower()
    cfg["both_policy"] = _pol if _pol in ("first", "higher", "skip") else "first"
    for k in ("lots", "lot_size", "sustain_candles"):
        try:
            cfg[k] = max(0, int(cfg[k] or 0))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    cfg["lots"] = cfg["lots"] or 1
    cfg["sustain_candles"] = cfg["sustain_candles"] or 1
    _tm = str(cfg.get("trail_mode", "lock")).lower()   # ── BRK_V1_RATCHET_20260831 ──
    cfg["trail_mode"] = _tm if _tm in ("lock", "ratchet") else "lock"
    try:   # ── BRK_V1_TIMESTOP_20260831 ──
        cfg["time_stop_min"] = max(0, int(cfg.get("time_stop_min") or 0))
    except (TypeError, ValueError):
        cfg["time_stop_min"] = 0
    for k in ("select_below", "select_min", "break_above", "sl_pts",
              "tp_pts", "trail_trigger_pts", "trail_lock_pts", "trail_gap",
              "time_stop_need_pts", "fallback_min_pts"):
        try:
            cfg[k] = abs(float(cfg[k] or 0.0))
        except (TypeError, ValueError):
            cfg[k] = float(DEFAULTS[k])
    cfg["skip_expiry_day"] = bool(cfg.get("skip_expiry_day", False))
    cfg["fallback_enabled"] = bool(cfg.get("fallback_enabled", False))   # ── BRK_V1_FALLBACK_20260831 ──
    return cfg


# ─────────────────────────────────────────────────────────────────────────
#  PURE HELPERS (unit-tested in test_brk_runner_sim.py)
# ─────────────────────────────────────────────────────────────────────────
def pick_candidate(prints: Dict[str, float], *, below: float,
                   floor: float = 0.0) -> Optional[str]:
    """D1: of {symbol: premium}, the symbol with the HIGHEST premium
    strictly below `below` (and >= floor when floor > 0). None if nothing
    qualifies. Deterministic tie-break on symbol name so two runs on the
    same corpus pick the same contract."""
    best: Optional[Tuple[float, str]] = None
    for sym, px in prints.items():
        if px is None or px <= 0:
            continue
        if px >= below:
            continue
        if floor > 0 and px < floor:
            continue
        key = (float(px), sym)
        if best is None or key > best:
            best = key
    return best[1] if best else None


def confirmed_at(closes: Dict[int, float], decision_min: int, *,
                 level: float, sustain: int) -> bool:
    """D2: True when the `sustain` 1m closes ENDING at decision_min (bars
    decision_min-1 .. decision_min-sustain) all exist and are >= level.
    A missing bar is NOT a break (fail-closed)."""
    for k in range(1, sustain + 1):
        c = closes.get(decision_min - k)
        if c is None or c < level:
            return False
    return True


def first_break_minute(closes: Dict[int, float], *, from_min: int,
                       to_min: int, level: float) -> Optional[int]:
    """Earliest bar minute in [from_min, to_min] whose close >= level."""
    for m in range(from_min, to_min + 1):
        c = closes.get(m)
        if c is not None and c >= level:
            return m
    return None


def choose_side(*, ce_ok: bool, pe_ok: bool, policy: str,
                ce_first: Optional[int], pe_first: Optional[int],
                ce_px: Optional[float], pe_px: Optional[float]) -> Optional[str]:
    """D4. Returns 'CE' | 'PE' | None."""
    if ce_ok and not pe_ok:
        return "CE"
    if pe_ok and not ce_ok:
        return "PE"
    if not (ce_ok and pe_ok):
        return None
    if policy == "skip":
        return None
    if policy == "first":
        if ce_first is not None and pe_first is not None and ce_first != pe_first:
            return "CE" if ce_first < pe_first else "PE"
        # same minute (or unknowable) -> fall through to the dearer one
    cp = float(ce_px or 0.0)
    pp = float(pe_px or 0.0)
    return "CE" if cp >= pp else "PE"


def resolve_exit(*, sl_px: float, tp_px: float, bar,
                 raised: bool) -> Optional[Tuple[str, float]]:
    """D5 on one 1m bar of the bought contract. SL triggers on low <= sl_px
    and fills AT sl_px; TP triggers on high >= tp_px and fills AT tp_px;
    both in one bar -> SL. `raised` marks the stop as a trail stop so the
    exit reason says TRAIL instead of SL."""
    sl_hit = float(bar.low) <= sl_px
    tp_hit = tp_px is not None and float(bar.high) >= tp_px   # ── BRK_V1_RATCHET_20260831 ── None = TP off
    if sl_hit:
        return ("TRAIL" if raised else "SL"), float(sl_px)
    if tp_hit:
        return "TP", float(tp_px)
    return None


# ─────────────────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────────────────
def run_brk_backtest(
    *,
    db_path: str,
    strategy_id: str,                  # "BRK_V1"
    underlying: str,
    date_from: date,
    date_to: date,
    config_override: Optional[dict] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> Dict:
    try:
        from app.event_bus.audit_logger import audit_muted
        with audit_muted():
            return _impl(db_path=db_path, strategy_id=strategy_id,
                         underlying=underlying, date_from=date_from,
                         date_to=date_to, config_override=config_override,
                         progress_cb=progress_cb, cancel_cb=cancel_cb)
    except ImportError:
        return _impl(db_path=db_path, strategy_id=strategy_id,
                     underlying=underlying, date_from=date_from,
                     date_to=date_to, config_override=config_override,
                     progress_cb=progress_cb, cancel_cb=cancel_cb)


def _abort(cfg, strategy_id, reason) -> Dict:
    return {"run_id": None, "aborted": True, "reason": reason,
            "trades": [], "summary": _empty_summary(),
            "config": cfg, "strategy_id": strategy_id}


def _impl(*, db_path, strategy_id, underlying, date_from, date_to,
          config_override, progress_cb, cancel_cb) -> Dict:
    from app.backtest.data.candle_source import CandleSource
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    from app.backtest.charges.charges_model import charges_for_long_trade
    from app.backtest.util.lot_sizes import resolve_lot
    from app.utils.market_hours import is_trading_day
    from app.event_bus.audit_logger import write_audit_log

    cfg = _merge_cfg(config_override)

    index_lot = INDEX_LOTS.get(underlying.upper())
    if index_lot is None:
        return _abort(cfg, strategy_id,
                      f"BRK_V1 is index-only; no lot constant for {underlying}.")
    lot_size, lot_source = resolve_lot(
        underlying=underlying, is_stock=False, cfg_lot=cfg["lot_size"],
        index_lot=index_lot, db_path=db_path)
    if lot_size is None:
        return _abort(cfg, strategy_id, f"no lot size for {underlying}")
    qty = cfg["lots"] * lot_size

    sel_min = _hhmm(cfg["select_time"], 9 * 60 + 25)
    first_min = _hhmm(cfg["entry_first"], 9 * 60 + 30)
    last_min = _hhmm(cfg["entry_last"], 9 * 60 + 35)
    eod_min = _hhmm(cfg["eod_square_off"], 15 * 60 + 15)
    sustain = cfg["sustain_candles"]
    if not (sel_min + sustain <= first_min <= last_min < eod_min):
        return _abort(cfg, strategy_id,
                      (f"time order must be select_time {cfg['select_time']} "
                       f"(+{sustain} sustain bars) <= entry_first "
                       f"{cfg['entry_first']} <= entry_last {cfg['entry_last']} "
                       f"< eod_square_off {cfg['eod_square_off']}"))
    if cfg["sl_pts"] <= 0:
        return _abort(cfg, strategy_id, "sl_pts must be > 0")
    # ── BRK_V1_RATCHET_20260831 ── tp_pts 0 = no fixed target (trail/SL/EOD only).
    if cfg["trail_mode"] == "ratchet" and cfg["trail_gap"] <= 0:
        return _abort(cfg, strategy_id, "trail_mode ratchet needs trail_gap > 0")
    if cfg["tp_pts"] <= 0 and not (cfg["trail_mode"] == "ratchet" and cfg["trail_gap"] > 0) \
            and cfg["trail_trigger_pts"] <= 0:
        # Not an error, but say it: with no target and no trail the only
        # profitable exit is EOD. Allowed (it is the diagnostic run).
        pass
    # ── BRK_V1_NO_LEVEL_GUARD_20260830 ── the break_above >= select_below guard was removed on
    # request: a break level below the selection ceiling is allowed, and a
    # contract already at-or-above it at 09:30 simply qualifies.

    src = CandleSource(db_path)

    days: List[date] = []
    d = date_from
    while d <= date_to:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)

    trades: List[BRKTrade] = []
    diag = {
        "days_total": len(days), "days_traded": 0,
        "days_uncovered": 0, "days_skipped_expiry": 0,
        "days_no_candidate_ce": 0, "days_no_candidate_pe": 0,
        "days_no_candidate_any": 0,
        "days_no_break": 0, "days_both_skip": 0, "days_no_fill": 0,
        "entries": 0, "ce_entries": 0, "pe_entries": 0,
        "fallback_entries": 0, "fallback_ce": 0, "fallback_pe": 0,   # ── BRK_V1_FALLBACK_20260831 ──
        "days_fallback_skip": 0,
        "both_confirmed_days": 0, "both_resolved_first": 0,
        "both_resolved_higher": 0,
        "entry_minute_hist": {},           # "09:30" -> count
        "sel_ce_premium_sum": 0.0, "sel_pe_premium_sum": 0.0,
        "sel_ce_days": 0, "sel_pe_days": 0,
        "sl_exits": 0, "tp_exits": 0, "trail_exits": 0, "eod_exits": 0,
        "time_exits": 0, "time_pnl_gross": 0.0,   # ── BRK_V1_TIMESTOP_20260831 ──
        "sl_pnl_gross": 0.0, "tp_pnl_gross": 0.0, "trail_pnl_gross": 0.0,
        "eod_pnl_gross": 0.0,
        "stale_marks": 0, "trail_armed": 0,
        "trail_ratchets": 0,   # ── BRK_V1_RATCHET_20260831 ── stop raises in ratchet mode
        "underlying": underlying, "lot_size": lot_size,
        "lot_source": lot_source, "qty": qty,
        "corpus_db": str(db_path).rsplit("/", 1)[-1],
    }

    def close_trade(pos: dict, ts: int, px: float, reason: str) -> None:
        gross = (px - pos["entry_px"]) * pos["qty"]
        # ChargesResult exposes total_charges (NOT .total).
        ch = charges_for_long_trade(entry_price=pos["entry_px"],
                                    exit_price=px, qty=pos["qty"]).total_charges
        net = gross - ch
        t = pos["trade"]
        t.exit_ts, t.exit_price, t.exit_reason = ts, round(px, 2), reason
        t.pnl = t.gross = round(gross, 2)
        t.charges = round(ch, 2)
        t.net_pnl = t.net = round(net, 2)
        t.max_adverse = round(pos["mae"], 2)
        t.max_favorable = round(pos["mfe"], 2)
        key = {"SL": "sl", "TP": "tp", "TRAIL": "trail", "EOD": "eod",
               "TIME": "time"}[reason]   # ── BRK_V1_TIMESTOP_20260831 ──
        diag[f"{key}_exits"] += 1
        diag[f"{key}_pnl_gross"] += round(net, 2)

    for i, day in enumerate(days):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            # Contract matches VET/TMA/PST/CBO: day = 1-based index,
            # total_days = count, date = display string.
            progress_cb({"day": i + 1, "total_days": len(days),
                         "date": day.isoformat(), "trades": len(trades)})

        ds = _day_start_epoch(day)
        want = expected_expiry_for_day(day).isoformat()
        if cfg["skip_expiry_day"] and date.fromisoformat(want) == day:
            diag["days_skipped_expiry"] += 1
            continue
        universe = src.contracts_active_on_day(underlying, ds, expiry=want)
        if not universe:
            # P5: no faithful contract -> skip, never substitute an expiry.
            diag["days_uncovered"] += 1
            continue

        # ── D1: selection at select_time ──
        sel_ts = ds + (sel_min - 1) * 60          # bar that CLOSES at select_time
        bars_by_sym: Dict[str, Dict[int, object]] = {}

        def bars(sym: str) -> Dict[int, object]:
            if sym not in bars_by_sym:
                bars_by_sym[sym] = {c.ts: c for c in
                                    src.candles_1m_for_symbol_day(sym, ds)}
            return bars_by_sym[sym]

        prints: Dict[str, Dict[str, float]] = {"CE": {}, "PE": {}}
        meta: Dict[str, dict] = {}
        for c in universe:
            itype = c.get("instrument_type")
            if itype not in ("CE", "PE"):
                continue
            b = bars(c["tradingsymbol"]).get(sel_ts)
            if b is None:
                continue
            prints[itype][c["tradingsymbol"]] = float(b.close)
            meta[c["tradingsymbol"]] = c
        ce_sym = pick_candidate(prints["CE"], below=cfg["select_below"],
                                floor=cfg["select_min"])
        pe_sym = pick_candidate(prints["PE"], below=cfg["select_below"],
                                floor=cfg["select_min"])
        if ce_sym is None:
            diag["days_no_candidate_ce"] += 1
        if pe_sym is None:
            diag["days_no_candidate_pe"] += 1
        if ce_sym is None and pe_sym is None:
            diag["days_no_candidate_any"] += 1
            continue
        if ce_sym:
            diag["sel_ce_days"] += 1
            diag["sel_ce_premium_sum"] += prints["CE"][ce_sym]
        if pe_sym:
            diag["sel_pe_days"] += 1
            diag["sel_pe_premium_sum"] += prints["PE"][pe_sym]

        closes = {}
        for side, sym in (("CE", ce_sym), ("PE", pe_sym)):
            closes[side] = ({(b.ts - ds) // 60: float(b.close)
                             for b in bars(sym).values()} if sym else {})

        # ── BRK_V1_FALLBACK_20260831 ── ONE trade-open path for breakout and fallback entries.
        # Returns the pos dict, or None when the fill bar has no print.
        def open_pos(sym: str, side: str, m: int, tag: str) -> Optional[dict]:
            fb = bars(sym).get(ds + m * 60)
            if fb is None or not fb.open:
                return None
            entry_px = float(fb.open)
            sl_px = round(entry_px - cfg["sl_pts"], 2)
            tp_px = round(entry_px + cfg["tp_pts"], 2) if cfg["tp_pts"] > 0 else None   # ── BRK_V1_RATCHET_20260831 ──
            mc = meta[sym]
            t = BRKTrade(
                tradingsymbol=sym, symbol=sym, instrument_type=side,
                strike=float(mc["strike"]) if mc.get("strike") is not None else None,
                expiry=mc.get("expiry"), direction="BUY",
                entry_ts=ds + m * 60, entry_price=round(entry_px, 2),
                sl=sl_px, tp=tp_px, exit_ts=None, exit_price=None,
                exit_reason=None, qty=qty,
                condition=f"{tag}·{side}·{m // 60:02d}:{m % 60:02d}")
            trades.append(t)
            diag["entries"] += 1
            diag["ce_entries" if side == "CE" else "pe_entries"] += 1
            hk = f"{m // 60:02d}:{m % 60:02d}"
            diag["entry_minute_hist"][hk] = diag["entry_minute_hist"].get(hk, 0) + 1
            return {"symbol": sym, "trade": t, "entry_px": entry_px,
                    "sl_px": sl_px, "tp_px": tp_px, "qty": qty,
                    "raised": False, "last_mark": entry_px,
                    "mae": 0.0, "mfe": 0.0, "entry_min": m,
                    "hh": entry_px}   # ── BRK_V1_RATCHET_20260831 ── highest high since entry

        # ── D2/D3/D4: decision loop ──
        pos: Optional[dict] = None
        saw_confirm = False
        for m in range(first_min, last_min + 1):
            ce_ok = bool(ce_sym) and confirmed_at(
                closes["CE"], m, level=cfg["break_above"], sustain=sustain)
            pe_ok = bool(pe_sym) and confirmed_at(
                closes["PE"], m, level=cfg["break_above"], sustain=sustain)
            if not (ce_ok or pe_ok):
                continue
            saw_confirm = True
            ce_first = pe_first = None
            if ce_ok and pe_ok:
                diag["both_confirmed_days"] += 1
                ce_first = first_break_minute(closes["CE"], from_min=sel_min,
                                              to_min=m - 1,
                                              level=cfg["break_above"])
                pe_first = first_break_minute(closes["PE"], from_min=sel_min,
                                              to_min=m - 1,
                                              level=cfg["break_above"])
            side = choose_side(ce_ok=ce_ok, pe_ok=pe_ok,
                               policy=cfg["both_policy"],
                               ce_first=ce_first, pe_first=pe_first,
                               ce_px=closes["CE"].get(m - 1),
                               pe_px=closes["PE"].get(m - 1))
            if side is None:
                diag["days_both_skip"] += 1
                break                      # D4 skip: no trade today
            if ce_ok and pe_ok:
                if (cfg["both_policy"] == "first" and ce_first is not None
                        and pe_first is not None and ce_first != pe_first):
                    diag["both_resolved_first"] += 1
                else:
                    diag["both_resolved_higher"] += 1
            sym = ce_sym if side == "CE" else pe_sym
            pos = open_pos(sym, side, m, "BRK")   # ── BRK_V1_FALLBACK_20260831 ── shared open path
            if pos is None:
                # No print at the decision minute: cannot fill at its open.
                # Try the next decision minute (the confirm may still hold).
                diag["days_no_fill"] += 1
                continue
            break

        if pos is None and not saw_confirm and cfg["fallback_enabled"]:
            # ── BRK_V1_FALLBACK_20260831 ── no break all window: buy the side that moved
            # most toward the level since its selection print. Decision at
            # entry_last on the last COMPLETED bar; fill at entry_last open.
            pos = None
            best = None   # (gain, side, sym)
            for side, sym in (("CE", ce_sym), ("PE", pe_sym)):
                if not sym:
                    continue
                last = closes[side].get(last_min - 1)
                if last is None:
                    continue
                gain = last - prints[side][sym]
                key = (gain, side)
                if best is None or key > (best[0], best[1]):
                    best = (gain, side, sym)
            if best is not None and best[0] >= cfg["fallback_min_pts"]:
                pos = open_pos(best[2], best[1], last_min, "BRK·FB")
                if pos is not None:
                    diag["fallback_entries"] += 1
                    diag["fallback_ce" if best[1] == "CE" else "fallback_pe"] += 1
                else:
                    diag["days_no_fill"] += 1
            if pos is None:
                diag["days_fallback_skip"] += 1
                diag["days_no_break"] += 1
                continue
        elif pos is None:
            if not saw_confirm:
                diag["days_no_break"] += 1
            continue

        # ── D5/D6/D7: exit ladder from the entry bar to EOD ──
        ob = bars(pos["symbol"])
        trig = cfg["trail_trigger_pts"]
        closed = False
        for m in range(pos["entry_min"], eod_min + 1):
            b = ob.get(ds + m * 60)
            if m >= eod_min:
                px = float(b.close) if b is not None else pos["last_mark"]
                close_trade(pos, ds + m * 60, px, "EOD")
                closed = True
                break
            if b is None:
                diag["stale_marks"] += 1
                continue
            pos["last_mark"] = float(b.close)
            pos["mae"] = min(pos["mae"], (float(b.low) - pos["entry_px"]) * qty)
            pos["mfe"] = max(pos["mfe"], (float(b.high) - pos["entry_px"]) * qty)
            ex = resolve_exit(sl_px=pos["sl_px"], tp_px=pos["tp_px"], bar=b,
                              raised=pos["raised"])
            if ex is not None:
                close_trade(pos, ds + m * 60, ex[1], ex[0])
                closed = True
                break
            # ── BRK_V1_TIMESTOP_20260831 ── time stop: at entry+N, needs close ≥ entry+X.
            # Checked AFTER the stop/target on the same bar, ONCE.
            if cfg["time_stop_min"] > 0 and m == pos["entry_min"] + cfg["time_stop_min"] \
                    and float(b.close) - pos["entry_px"] < cfg["time_stop_need_pts"]:
                close_trade(pos, ds + m * 60, float(b.close), "TIME")
                closed = True
                break
            # D7: trail arms from the NEXT bar (pessimistic ordering).
            if cfg["trail_mode"] == "ratchet":
                # ── BRK_V1_RATCHET_20260831 ── Zerodha GTT-trailing semantics on closed 1m
                # bars: stop follows the highest high by trail_gap, only up.
                pos["hh"] = max(pos["hh"], float(b.high))
                if pos["hh"] >= pos["entry_px"] + trig:
                    new_sl = round(pos["hh"] - cfg["trail_gap"], 2)
                    if new_sl > pos["sl_px"]:
                        if not pos["raised"]:
                            diag["trail_armed"] += 1
                        pos["sl_px"] = new_sl
                        pos["raised"] = True
                        pos["trade"].sl = new_sl
                        diag["trail_ratchets"] += 1
            elif trig > 0 and not pos["raised"] and \
                    float(b.high) >= pos["entry_px"] + trig:
                new_sl = round(pos["entry_px"] + cfg["trail_lock_pts"], 2)
                if new_sl > pos["sl_px"]:
                    pos["sl_px"] = new_sl
                    pos["raised"] = True
                    pos["trade"].sl = new_sl
                    diag["trail_armed"] += 1
        if not closed:
            # Ran out of prints before EOD (truncated session): book at the
            # last known mark as EOD, never leave a row open.
            last_ts = max(ob) if ob else ds + eod_min * 60
            close_trade(pos, last_ts, pos["last_mark"], "EOD")
        diag["days_traded"] += 1

    src.close()
    if diag["sel_ce_days"]:
        diag["sel_ce_premium_avg"] = round(
            diag["sel_ce_premium_sum"] / diag["sel_ce_days"], 2)
    if diag["sel_pe_days"]:
        diag["sel_pe_premium_avg"] = round(
            diag["sel_pe_premium_sum"] / diag["sel_pe_days"], 2)
    summary = _summarize(trades, diag)
    write_audit_log(
        f"[BACKTEST][{strategy_id}] {underlying} {date_from}..{date_to}: "
        f"{summary['total_trades']} trades, net {summary['net_pnl']:,.0f}, "
        f"DD {summary['max_drawdown']:,.0f}, exits SL {diag['sl_exits']} / "
        f"TP {diag['tp_exits']} / TRAIL {diag['trail_exits']} / "
        f"EOD {diag['eod_exits']} / TIME {diag['time_exits']}, fallback {diag['fallback_entries']}, "
        f"days noBreak {diag['days_no_break']} / "
        f"noCand {diag['days_no_candidate_any']} / uncovered {diag['days_uncovered']}"
    )
    return {"run_id": str(uuid.uuid4()), "summary": summary,
            "config": cfg, "trades": trades, "strategy_id": strategy_id}


def _summarize(trades: List[BRKTrade], diag: dict) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        s = _empty_summary()
        s["diag_brk"] = diag
        return s
    eq = peak = mdd = 0.0
    for t in sorted(closed, key=lambda x: (x.exit_ts or 0, x.entry_ts or 0)):
        eq += t.net_pnl
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    nets = [t.net_pnl for t in closed]
    wins = sum(1 for n in nets if n > 0)
    net = sum(nets)
    if abs(net) > 1e-9:
        for k in ("sl", "tp", "trail", "eod", "time"):   # ── BRK_V1_TIMESTOP_20260831 ──
            diag[f"{k}_pnl_share_pct"] = round(
                100.0 * diag[f"{k}_pnl_gross"] / net, 1)
    return {
        "total_trades": len(closed), "wins": wins,
        "losses": sum(1 for n in nets if n < 0),
        "win_rate": round(100.0 * wins / len(closed), 2),
        "gross_pnl": round(sum(t.pnl for t in closed), 2),
        "total_charges": round(sum(t.charges for t in closed), 2),
        "net_pnl": round(net, 2), "max_drawdown": round(mdd, 2),
        "ambiguous_fills": 0,
        "diag_brk": diag,
    }