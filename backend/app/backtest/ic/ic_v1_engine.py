# backend/app/backtest/strategies/ic_v1_engine.py
#
# ── IC_V1_ENGINE ── Iron Condor: time-entry premium-defined condor on
# NIFTY weeklies. Sell CE+PE nearest-below a premium cap (default ₹85) at a
# fixed entry time, buy far wings nearest-below a small cap (default ₹4),
# per-leg SL/TP in percent OR points, Move-To-Cost (MTC) cross-leg rule,
# EOD square-off.
#
# PURE MODULE by design: no app imports, no DB, no I/O. The runner shim
# (backtest_ic_runner) feeds it candles + config and persists what comes
# back — so every branch of the cross-leg state machine is unit-tested
# against synthetic candles with hand-computed expectations, per house rule.
#
# LOCKED CONVENTIONS (confirmed 2026-07-05):
#   * Entry price = CLOSE of the candle ENDING at entry time (09:18 entry →
#     close of the 09:17 candle, the day's 3rd 1m candle). Strike selection
#     uses that same close per candidate.
#   * Strike pick = highest premium ≤ cap ("premium lesser than", Quantman
#     semantics). SHORT legs fail CLOSED when nothing ≤ cap exists (selling a
#     richer premium changes the risk profile — skip the day, DIAG counts).
#     WING legs fall back to the cheapest available strike (fallback flagged,
#     DIAG counts) because the ATM±10 corpus often lacks ₹4 wings.
#   * Intrabar SL: candle range touching the trigger fills AT the trigger.
#     SL and TP inside one candle → SL fill + ambiguous_fill flag.
#   * MTC: when a short leg exits on SL, its partner short's SL is re-pinned
#     to the partner's OWN entry price, effective from the NEXT 1m candle —
#     same-candle sequencing on 1m data would be lookahead. TP (if any)
#     stays live after MTC. MTC is one-shot, not trailing.
#   * Both partner shorts breach their ORIGINAL SLs in the same candle →
#     both exit at their own SLs, MTC never activates, day flagged
#     double_sl (per-candle decisions are snapshotted BEFORE exits apply).
#   * EOD: any open leg exits at the close of its last candle strictly
#     before exit_time. Legs 3/4 (wings) have no SL/TP by default and always
#     ride to EOD.
#
# ══════════════════════════════════════════════════════════════════════
# ── IC_V2 (2026-07-20) ── two switches on top of the above. Both default
# OFF, and with both OFF this module is behaviourally IDENTICAL to IC_V1
# (simulate_day is a thin wrapper over simulate_session with the legacy
# arguments), so the IC_V1 runner path is untouched — verifiable by diff.
#
#   adjust_on_sl (D4/D5) — when a SHORT leg exits with reason "SL", a BUY
#     leg on the SAME option type is opened `adjust_delay_s` later (default
#     60s = the next 1m candle, matching Quantman's ReExecute delay). The
#     adjustment leg has its OWN premium cap, lots, SL and optional TP, all
#     runner-supplied. It is a genuine directional long on the side that
#     just broke — at the default ₹85 cap it is NOT a wing.
#       * trigger is reason == "SL" ONLY. An "MTC_COST" exit is a scratch,
#         not a loss, so it does NOT arm an adjustment (Quantman's
#         `Already Exited In Loss Is True`).
#       * double-SL day: BOTH adjustments fire (D4). Flagged
#         double_sl_adjust so the runner can bucket those days — a day that
#         ends long CE + long PE at ~₹85 × 24 lots each is the strategy's
#         worst case and deserves its own DIAG line.
#       * if no candle exists at the activation ts, the adjustment is
#         DROPPED, not slid (D-C2/b): buying next morning on yesterday's
#         15:29 signal is a different trade. Flagged adjust_dropped.
#       * the runner pre-selects the adjustment symbol/strike; if selection
#         failed it passes None and the engine flags adjust_no_strike.
#
#   exit_mode (D6) — "EOD" (IC_V1) or "NEXT_OPEN" (IC_V2).
#     NEXT_OPEN removes the daily square-off entirely: legs still open when
#     the session's candles run out CARRY overnight and close at the OPEN of
#     the candle stamped next_open_time on the next session that has data
#     for that symbol. On the contract's OWN EXPIRY DAY the position is
#     squared off intraday at expiry_exit_time instead (reason EOD).
#     A position can never outlive the backtest range: the runner closes
#     survivors with reason EOR.
#
#   GAP FILLS (TMA convention, carry only) — once positions cross a night,
#   an overnight gap can open a candle already through a level. In that
#   case the fill is at the OPEN, not at the level; the intraday at-level
#   convention is preserved for every non-carried candle.
# ══════════════════════════════════════════════════════════════════════
#
# ══════════════════════════════════════════════════════════════════════
# ── SYNTH_EVERYWHERE (2026-07-21) ── ENGINE-SIDE CHANGES ONLY.
#
# The synthetic-pricing work is overwhelmingly a RUNNER concern: the runner
# owns the corpus, so it owns IV implication, spot parity and strike walks.
# The engine needs exactly three things it did not have before:
#
#   1. ADJUSTMENT FILL PRICE MAY BE RUNNER-SUPPLIED. Previously the engine
#      filled an adjustment at the close of the candle stamped at the
#      activation minute, and DROPPED the adjustment when that candle was
#      missing. Under synth-everywhere the runner can hand over a
#      `fill_price` (a modelled premium at that exact minute) and a
#      `synthetic` flag. When `fill_price` is present the adjustment opens
#      even with no candle — a synthetic leg has no candles by definition.
#      A synthetic adjustment therefore also has no intrabar monitoring: it
#      rides to the session bound (or carries) unless the runner ALSO
#      supplies candles. This is deliberate and flagged
#      (`adjust_synth_unmonitored`) rather than silently modelled.
#
#   2. SYNTHETIC PROVENANCE MUST SURVIVE INTO THE TRADE ROW AND THROUGH A
#      CARRY. `synthetic` / `synth_kind` ride on leg state, on carry_out and
#      on the emitted trade dict, so the runner can bucket model-attributed
#      P&L (`syn_pnl_gross`) and so a synthetic leg carried across a night
#      is still recognisable as synthetic on the day it closes.
#
#   3. DARK-LEG MARKS NEED AN IV ANCHOR. A leg that goes dark takes its last
#      observed close with it; the runner rolls THAT leg's own IV forward
#      with tau decay (no fresh anchor — the whole band is dark by
#      definition). The engine preserves `last_close` / `last_ts` on
#      carry_out already; it now ALSO preserves them on the NEXT_OPEN_DARK
#      exit path so the runner can re-mark. Nothing else changes.
#
# With `fill_price` absent and no synthetic legs, every path below is
# byte-identical in behaviour to the 2026-07-20 build. `simulate_day` is
# untouched.
# ══════════════════════════════════════════════════════════════════════

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

PRICE_FLOOR = 0.05


# ──────────────────────────────────────────────────────────────────────
# leg config normalization
# ──────────────────────────────────────────────────────────────────────
def norm_leg(raw: dict) -> dict:
    """Normalize a leg config. sl/tp value 0 or None = disabled.
    mode: 'pct' | 'pts'."""
    return {
        "id": str(raw.get("id")),
        "action": str(raw.get("action", "SELL")).upper(),      # SELL | BUY
        "opt_type": str(raw.get("opt_type", "CE")).upper(),    # CE | PE
        "lots": int(raw.get("lots") or 0),
        "premium_max": float(raw.get("premium_max") or 0),
        "sl_val": float(raw.get("sl_val") or 0),
        "sl_mode": str(raw.get("sl_mode", "pct")),
        "tp_val": float(raw.get("tp_val") or 0),
        "tp_mode": str(raw.get("tp_mode", "pct")),
        "mtc_other_on_sl": bool(raw.get("mtc_other_on_sl")),
        "mtc_partner": raw.get("mtc_partner"),                 # partner leg id
    }


# ── IC_V2 BEGIN ──
def norm_adjust(raw: dict) -> dict:
    """Normalize ONE adjustment-leg config (the BUY opened when a short
    exits on SL). Shape mirrors norm_leg's SL/TP fields so sl_price/tp_price
    apply unchanged.

    `enabled` is an EXPLICIT switch so the UI can turn one short's
    adjustment off without zeroing its lots (and losing the sizing the user
    typed). lots 0 still disables — both gates must pass."""
    return {
        "enabled": bool(raw.get("enabled", True)) and int(raw.get("lots") or 0) > 0,
        "premium_max": float(raw.get("premium_max") or 0),
        "lots": int(raw.get("lots") or 0),
        "sl_val": float(raw.get("sl_val") or 0),
        "sl_mode": str(raw.get("sl_mode", "pct")),
        "tp_val": float(raw.get("tp_val") or 0),
        "tp_mode": str(raw.get("tp_mode", "pct")),
    }
# ── IC_V2 END ──


def sl_price(action: str, entry: float, val: float, mode: str) -> Optional[float]:
    if not val or val <= 0:
        return None
    if action == "SELL":   # loss when premium RISES
        return entry * (1 + val / 100.0) if mode == "pct" else entry + val
    return max(PRICE_FLOOR, entry * (1 - val / 100.0) if mode == "pct" else entry - val)


def tp_price(action: str, entry: float, val: float, mode: str) -> Optional[float]:
    if not val or val <= 0:
        return None
    if action == "SELL":   # profit when premium FALLS
        return max(PRICE_FLOOR, entry * (1 - val / 100.0) if mode == "pct" else entry - val)
    return entry * (1 + val / 100.0) if mode == "pct" else entry + val


# ──────────────────────────────────────────────────────────────────────
# strike selection
# ──────────────────────────────────────────────────────────────────────
def select_strike(candidates: List[Tuple[str, float]], premium_max: float,
                  fallback_cheapest: bool = False):
    """candidates: [(tradingsymbol, entry_candle_close)]. Returns
    (symbol, price, fallback_used) or None.
    Pick = HIGHEST premium ≤ cap; deterministic tie-break on symbol."""
    live = [(s, p) for s, p in candidates if p and p > 0]
    eligible = [(s, p) for s, p in live if p <= premium_max]
    if eligible:
        sym, px = sorted(eligible, key=lambda c: (-c[1], c[0]))[0]
        return sym, px, False
    if fallback_cheapest and live:
        sym, px = sorted(live, key=lambda c: (c[1], c[0]))[0]
        return sym, px, True
    return None


def entry_close(candles: List[dict], entry_ts: int,
                max_stale_s: int = 180) -> Optional[Tuple[int, float]]:
    """Close of the candle ENDING at entry time = latest candle with
    ts < entry_ts, within a staleness window. Returns (ts, close) or None."""
    best = None
    for cd in candles:
        if cd["ts"] < entry_ts and cd["ts"] >= entry_ts - max_stale_s:
            if best is None or cd["ts"] > best[0]:
                best = (cd["ts"], float(cd["close"]))
    return best


# ──────────────────────────────────────────────────────────────────────
# ── IC_V2 ── intrabar trigger evaluation (shared by intraday + carry)
# ──────────────────────────────────────────────────────────────────────
def _eval_candle(action: str, cd: dict, slp: Optional[float],
                 tpp: Optional[float], allow_gap: bool):
    """Decide what this candle does to an open leg.

    Returns (hit_sl, sl_fill, hit_tp, tp_fill) — fills are None when the
    corresponding leg wasn't hit.

    allow_gap=False is the IC_V1 intraday convention: a touched level fills
    AT the level, full stop. allow_gap=True adds the TMA carry rule — if the
    candle OPENS already beyond the level (an overnight gap), the fill is
    the OPEN, because there was never a print at the level to fill against.
    """
    o = float(cd["open"])
    h = float(cd["high"])
    lo = float(cd["low"])
    if action == "SELL":
        hit_sl = slp is not None and h >= slp
        hit_tp = tpp is not None and lo <= tpp
        sl_fill = (o if (allow_gap and slp is not None and o >= slp) else slp)
        tp_fill = (o if (allow_gap and tpp is not None and o <= tpp) else tpp)
    else:
        hit_sl = slp is not None and lo <= slp
        hit_tp = tpp is not None and h >= tpp
        sl_fill = (o if (allow_gap and slp is not None and o <= slp) else slp)
        tp_fill = (o if (allow_gap and tpp is not None and o >= tpp) else tpp)
    return hit_sl, sl_fill, hit_tp, tp_fill


# ──────────────────────────────────────────────────────────────────────
# session simulation
# ──────────────────────────────────────────────────────────────────────
def simulate_session(legs: List[dict],
                     candles_by_leg: Dict[str, List[dict]],
                     symbols_by_leg: Dict[str, str],
                     entry_ts: int,
                     eod_ts: Optional[int],
                     *,
                     exit_mode: str = "EOD",
                     carry_in: Optional[Dict[str, dict]] = None,
                     adjust_on_sl: bool = False,
                     adjust_cfg: Optional[Dict[str, dict]] = None,
                     adjust_delay_s: int = 60,
                     adjust_picks: Optional[Dict[str, dict]] = None,
                     hard_close_ts: Optional[int] = None,
                     hard_close_reason: str = "EOD",
                     next_open_ts: Optional[int] = None,
                     is_carry_day: bool = False,
                     entry_overrides: Optional[Dict[str, dict]] = None) -> dict:
    """Simulate ONE session for a condor — either its entry day or a
    carried day.

    ENTRY DAY (carry_in falsy): legs are opened from `legs` at entry_ts
    using the IC_V1 entry_close convention.
    CARRY DAY (carry_in given): no new entries; the supplied open-leg state
    is advanced through this session's candles.

    legs: norm_leg() dicts (only legs with lots > 0 and a selected symbol).
    candles_by_leg: leg id → ascending 1m candles [{ts, open, high, low,
      close}] for that leg's tradingsymbol, for THIS session.
    entry_ts: epoch of the entry minute (fills at close of the candle
      BEFORE it). Ignored on carry days.
    eod_ts: legacy EOD bound (exit_mode="EOD"). In NEXT_OPEN mode pass None
      and use hard_close_ts / next_open_ts instead.

    ── IC_V2 params ──
    exit_mode: "EOD" (IC_V1, daily square-off) | "NEXT_OPEN" (carry).
    carry_in: leg id → carried state dict from a previous session's
      `carry_out`. Falsy on an entry day.
    adjust_on_sl / adjust_cfg / adjust_delay_s / adjust_picks: see the
      module header. adjust_cfg maps SHORT leg id → norm_adjust() dict;
      adjust_picks maps SHORT leg id → {"symbol", "strike", "expiry",
      "candles"} pre-selected by the runner (candles = this session's 1m
      candles for the adjustment symbol). A missing pick flags
      adjust_no_strike.
    hard_close_ts: when set, every still-open leg closes at the last candle
      strictly before it, with hard_close_reason (expiry day / end of
      range). NEXT_OPEN mode only.
    next_open_ts: on a CARRY day, legs carried in close at the OPEN of the
      candle stamped this ts (or the first candle at/after it —
      next_open_fallback). NEXT_OPEN mode only.
    is_carry_day: enables gap fills for legs carried in.

    ── SYNTH_EVERYWHERE params ──
    entry_overrides: leg id → {"price": float, "symbol": str,
      "strike": float, "expiry": str, "synthetic": True,
      "synth_kind": "short"|"wing"}. When present for a leg, the engine
      SKIPS the entry_close lookup and opens that leg at the supplied
      price. Used for synthetic shorts/wings which have no corpus candles
      at all. A leg opened this way and given no candles is unmonitored —
      it rides to the session bound or carries, exactly as a wing does.

    adjust_picks entries MAY additionally carry:
      "fill_price": float — modelled premium at the activation minute.
        Present ⇒ the adjustment opens even with no candle at that minute
        (a synthetic leg has no candles), instead of being dropped.
      "synthetic": bool, "synth_kind": "adjust".

    Returns {"trades": [...], "carry_out": {...}, "flags": {...}}.
    carry_out is empty unless exit_mode == "NEXT_OPEN" and legs survived.
    Never raises on data gaps — degrades with flags."""
    legacy = exit_mode != "NEXT_OPEN"
    flags = {"double_sl": False, "mtc_activations": 0, "ambiguous": 0,
             "no_exit_data": 0,
             # ── IC_V2 ──
             "adjust_triggered": 0, "adjust_dropped": 0,
             "adjust_no_strike": 0, "double_sl_adjust": False,
             "next_open_closes": 0, "next_open_fallbacks": 0,
             "carried": 0, "gap_fills": 0,
             # ── SYNTH_EVERYWHERE ──
             "synth_entries": 0, "adjust_synth": 0,
             "adjust_synth_unmonitored": 0}

    adjust_cfg = adjust_cfg or {}
    adjust_picks = adjust_picks or {}
    carry_in = carry_in or {}
    entry_overrides = entry_overrides or {}

    state: Dict[str, dict] = {}

    # ── carried legs first: they own their leg ids, and their entry data
    # comes from the ORIGINAL entry session, not today.
    for lid, cs in carry_in.items():
        st = dict(cs)
        st["open"] = True
        st["exit"] = None
        st["carried"] = True
        st["gap_ok"] = True          # crossed a night → gap fills apply
        state[lid] = st

    # ── entry-day legs
    if not carry_in:
        for leg in legs:
            lid = leg["id"]
            ov = entry_overrides.get(lid)
            if ov is not None and ov.get("price"):
                # ── SYNTH_EVERYWHERE ── modelled entry: no corpus lookup.
                epx = float(ov["price"])
                sym = ov.get("symbol") or symbols_by_leg.get(lid)
                flags["synth_entries"] += 1
                state[lid] = {
                    "leg": leg, "entry_price": epx,
                    "sl": sl_price(leg["action"], epx, leg["sl_val"], leg["sl_mode"]),
                    "tp": tp_price(leg["action"], epx, leg["tp_val"], leg["tp_mode"]),
                    "mtc_applied": False,
                    "open": True, "last_close": epx, "last_ts": entry_ts,
                    "entry_ts": entry_ts,
                    "symbol": sym,
                    "strike": ov.get("strike"), "expiry": ov.get("expiry"),
                    "carried": False, "gap_ok": False,
                    "is_adjust": False, "adjust_of": None,
                    "synthetic": True,
                    "synth_kind": ov.get("synth_kind") or "entry",
                    "exit": None,
                }
                continue
            ec = entry_close(candles_by_leg.get(lid) or [], entry_ts)
            if ec is None:
                continue    # runner pre-validates; belt-and-braces
            _, epx = ec
            state[lid] = {
                "leg": leg, "entry_price": epx,
                "sl": sl_price(leg["action"], epx, leg["sl_val"], leg["sl_mode"]),
                "tp": tp_price(leg["action"], epx, leg["tp_val"], leg["tp_mode"]),
                "mtc_applied": False,
                "open": True, "last_close": epx, "last_ts": entry_ts,
                "entry_ts": entry_ts,
                "symbol": symbols_by_leg.get(lid),
                "carried": False, "gap_ok": False,
                "is_adjust": False, "adjust_of": None,
                "synthetic": False, "synth_kind": None,
                "exit": None,   # (ts, price, reason, ambiguous)
            }

    # pending MTC: partner leg id → activation ts (next candle after trigger)
    pending_mtc: Dict[str, int] = {}
    # ── IC_V2 ── pending adjustments: short leg id → activation ts
    pending_adjust: Dict[str, int] = {}

    # candle index per leg, rebuilt as adjustment legs join mid-session
    def _ts_index():
        lo_bound = 0 if carry_in else entry_ts
        hi_bound = eod_ts if legacy else None
        out = set()
        for lid in state:
            for cd in candles_by_leg.get(lid, []):
                ts = cd["ts"]
                if carry_in:
                    pass                       # carry day: whole session
                elif ts < lo_bound:
                    continue
                if hi_bound is not None and ts >= hi_bound:
                    continue
                if hard_close_ts is not None and ts >= hard_close_ts:
                    continue
                out.add(ts)
        return sorted(out)

    all_ts = _ts_index()
    by_leg_ts = {lid: {cd["ts"]: cd for cd in candles_by_leg.get(lid, [])}
                 for lid in state}

    for ts in all_ts:
        # ── IC_V2 ── 0) NEXT_OPEN close for carried legs, before anything
        # else: the carry exit is a scheduled event at a known minute, and
        # it outranks an SL/TP that the same candle might also show.
        if next_open_ts is not None and ts >= next_open_ts:
            for lid, st in state.items():
                if not st["open"] or not st.get("carried"):
                    continue
                cd = by_leg_ts.get(lid, {}).get(ts)
                if cd is None:
                    # ── ONE_NIGHT_MAX ── this leg has no candle at this
                    # minute. Do NOT `continue` past the close: the loop may
                    # never reach a ts this leg owns (all_ts is the UNION
                    # across legs), and a leg that is dark all session would
                    # then survive into carry_out and carry a SECOND night —
                    # observed as a 20/05 basket still open on 26/05. The
                    # close-out pass marks anything still carried at the end
                    # of the session (see NEXT_OPEN_DUE below); flag it here
                    # so that pass knows this leg was due today.
                    st["next_open_due"] = True
                    continue
                st["open"] = False
                st["exit"] = (ts, float(cd["open"]), "NEXT_OPEN", False)
                flags["next_open_closes"] += 1
                if ts > next_open_ts:
                    flags["next_open_fallbacks"] += 1

        # 1) apply due MTC re-pins BEFORE this candle's checks
        for lid, act_ts in list(pending_mtc.items()):
            st = state.get(lid)
            if st and st["open"] and ts >= act_ts and not st["mtc_applied"]:
                st["sl"] = st["entry_price"]          # cost; TP stays live
                st["mtc_applied"] = True
                flags["mtc_activations"] += 1
                del pending_mtc[lid]

        # ── IC_V2 ── 1b) open due adjustment legs BEFORE this candle's
        # checks, so a leg opened at ts is live for ts's own range.
        for src_lid, act_ts in list(pending_adjust.items()):
            if ts < act_ts:
                continue
            del pending_adjust[src_lid]
            pick = adjust_picks.get(src_lid)
            if not pick or not pick.get("symbol"):
                flags["adjust_no_strike"] += 1
                continue
            acfg = adjust_cfg.get(src_lid) or {}
            # norm_adjust folds lots>0 into `enabled`; the lots check stays
            # for raw dicts passed straight in by tests.
            if not acfg.get("enabled", True) or int(acfg.get("lots") or 0) <= 0:
                continue
            acands = pick.get("candles") or []
            fill_cd = next((c for c in acands if c["ts"] == act_ts), None)
            # ── SYNTH_EVERYWHERE ── a runner-supplied modelled fill price
            # lets the adjustment open with no candle at all. Real candle
            # wins when both exist (reality over model, always).
            synth_fill = pick.get("fill_price")
            if fill_cd is None and not synth_fill:
                # C2/b: no candle at the activation minute → DROP, never
                # slide. Buying tomorrow on today's signal is another trade.
                flags["adjust_dropped"] += 1
                continue
            aid = f"{src_lid}A"
            aleg = {
                "id": aid, "action": "BUY",
                "opt_type": state[src_lid]["leg"]["opt_type"],
                "lots": int(acfg["lots"]),
                "premium_max": float(acfg.get("premium_max") or 0),
                "sl_val": float(acfg.get("sl_val") or 0),
                "sl_mode": str(acfg.get("sl_mode", "pct")),
                "tp_val": float(acfg.get("tp_val") or 0),
                "tp_mode": str(acfg.get("tp_mode", "pct")),
                "mtc_other_on_sl": False, "mtc_partner": None,
            }
            is_synth_adj = fill_cd is None
            aepx = float(fill_cd["close"]) if fill_cd is not None \
                else float(synth_fill)
            state[aid] = {
                "leg": aleg, "entry_price": aepx,
                "sl": sl_price("BUY", aepx, aleg["sl_val"], aleg["sl_mode"]),
                "tp": tp_price("BUY", aepx, aleg["tp_val"], aleg["tp_mode"]),
                "mtc_applied": False,
                "open": True, "last_close": aepx, "last_ts": act_ts,
                "entry_ts": act_ts,
                "symbol": pick["symbol"], "strike": pick.get("strike"),
                "expiry": pick.get("expiry"),
                "carried": False, "gap_ok": False,
                "is_adjust": True, "adjust_of": src_lid,
                "synthetic": bool(is_synth_adj or pick.get("synthetic")),
                "synth_kind": ("adjust" if (is_synth_adj or pick.get("synthetic"))
                               else None),
                "exit": None,
            }
            candles_by_leg[aid] = acands
            by_leg_ts[aid] = {c["ts"]: c for c in acands}
            symbols_by_leg[aid] = pick["symbol"]
            flags["adjust_triggered"] += 1
            if state[aid]["synthetic"]:
                flags["adjust_synth"] += 1
                if not acands:
                    # no candles at all → no intrabar monitoring is possible.
                    # The leg rides to the bound (or carries). Counted so a
                    # run's unmonitored share is never invisible.
                    flags["adjust_synth_unmonitored"] += 1
            # the adjustment's OWN entry candle must not also exit it —
            # monitoring starts at the NEXT candle (entry-fill convention)
            state[aid]["watch_from"] = act_ts + 60

        # 2) SNAPSHOT decisions for every open leg at this candle (so a
        #    same-candle double SL is decided on pre-exit state)
        decisions = []
        for lid, st in state.items():
            if not st["open"]:
                continue
            cd = by_leg_ts.get(lid, {}).get(ts)
            if cd is None:
                continue
            if ts < int(st.get("watch_from") or 0):
                continue
            st["last_close"] = float(cd["close"])
            st["last_ts"] = ts
            action = st["leg"]["action"]
            allow_gap = bool(st.get("gap_ok"))
            hit_sl, sl_fill, hit_tp, tp_fill = _eval_candle(
                action, cd, st["sl"], st["tp"], allow_gap)
            if hit_sl:
                reason = "MTC_COST" if st["mtc_applied"] else "SL"
                decisions.append((lid, sl_fill, reason, hit_tp))
                if allow_gap and sl_fill != st["sl"]:
                    flags["gap_fills"] += 1
            elif hit_tp:
                decisions.append((lid, tp_fill, "TP", False))
                if allow_gap and tp_fill != st["tp"]:
                    flags["gap_fills"] += 1

        # 3) double-SL detection among MTC partner pairs (original SLs only)
        sl_ids = {lid for lid, _p, r, _a in decisions if r == "SL"}
        double_pairs = set()
        for lid in sl_ids:
            partner = state[lid]["leg"].get("mtc_partner")
            if partner in sl_ids:
                double_pairs.add(lid)

        # 4) apply exits, then schedule MTC for survivors
        for lid, px, reason, ambiguous in decisions:
            st = state[lid]
            st["open"] = False
            st["exit"] = (ts, px, reason, ambiguous)
            if ambiguous:
                flags["ambiguous"] += 1
        if double_pairs:
            flags["double_sl"] = True
        for lid, _px, reason, _a in decisions:
            if reason != "SL" or lid in double_pairs:
                continue
            st = state[lid]
            if not st["leg"]["mtc_other_on_sl"]:
                continue
            partner = st["leg"].get("mtc_partner")
            pst = state.get(partner)
            if pst and pst["open"] and not pst["mtc_applied"]:
                pending_mtc[partner] = ts + 60      # NEXT candle, not this one

        # ── IC_V2 ── 4b) arm adjustments. Trigger is reason == "SL" ONLY
        # (D5: MTC_COST is a scratch, not a loss). On a double-SL day BOTH
        # arm (D4) — the double_pairs suppression above is MTC-only.
        if adjust_on_sl:
            armed_here = [lid for lid, _px, reason, _a in decisions
                          if reason == "SL"
                          and state[lid]["leg"]["action"] == "SELL"
                          and not state[lid].get("is_adjust")]
            for lid in armed_here:
                pending_adjust[lid] = ts + int(adjust_delay_s)
            if len(armed_here) > 1:
                flags["double_sl_adjust"] = True

    # ── IC_V2 ── adjustments still pending when the session's candles run
    # out never got a fill minute → dropped (C2/b), not carried forward.
    #
    # ── SYNTH_EVERYWHERE ── EXCEPT when the runner supplied a modelled fill
    # price: a synthetic adjustment needs no candle, so a pending one whose
    # activation minute fell past the last candle in `all_ts` still opens.
    # It opens with no candles ⇒ unmonitored ⇒ rides to the bound / carries.
    for _src_lid, _act_ts in list(pending_adjust.items()):
        del pending_adjust[_src_lid]
        _pick = adjust_picks.get(_src_lid) or {}
        _acfg = adjust_cfg.get(_src_lid) or {}
        _fp = _pick.get("fill_price")
        if not (_fp and _pick.get("symbol")
                and _acfg.get("enabled", True)
                and int(_acfg.get("lots") or 0) > 0):
            flags["adjust_dropped"] += 1
            continue
        _aid = f"{_src_lid}A"
        _aleg = {
            "id": _aid, "action": "BUY",
            "opt_type": state[_src_lid]["leg"]["opt_type"],
            "lots": int(_acfg["lots"]),
            "premium_max": float(_acfg.get("premium_max") or 0),
            "sl_val": float(_acfg.get("sl_val") or 0),
            "sl_mode": str(_acfg.get("sl_mode", "pct")),
            "tp_val": float(_acfg.get("tp_val") or 0),
            "tp_mode": str(_acfg.get("tp_mode", "pct")),
            "mtc_other_on_sl": False, "mtc_partner": None,
        }
        _aepx = float(_fp)
        state[_aid] = {
            "leg": _aleg, "entry_price": _aepx,
            "sl": sl_price("BUY", _aepx, _aleg["sl_val"], _aleg["sl_mode"]),
            "tp": tp_price("BUY", _aepx, _aleg["tp_val"], _aleg["tp_mode"]),
            "mtc_applied": False,
            "open": True, "last_close": _aepx, "last_ts": _act_ts,
            "entry_ts": _act_ts,
            "symbol": _pick["symbol"], "strike": _pick.get("strike"),
            "expiry": _pick.get("expiry"),
            "carried": False, "gap_ok": False,
            "is_adjust": True, "adjust_of": _src_lid,
            "synthetic": True, "synth_kind": "adjust",
            "watch_from": _act_ts + 60,
            "exit": None,
        }
        symbols_by_leg[_aid] = _pick["symbol"]
        flags["adjust_triggered"] += 1
        flags["adjust_synth"] += 1
        flags["adjust_synth_unmonitored"] += 1

    # 5) close-out for anything still open
    trades: List[dict] = []
    carry_out: Dict[str, dict] = {}
    for lid, st in list(state.items()):
        leg = st["leg"]
        if st["open"]:
            if legacy or hard_close_ts is not None:
                # EOD square-off (IC_V1) or hard close (expiry / end of range)
                if st["last_ts"] <= st.get("entry_ts", entry_ts) and \
                        st["last_close"] == st["entry_price"]:
                    flags["no_exit_data"] += 1
                # EOD_MTC: survivor whose SL was moved to cost and never
                # breached — distinguishes "MTC rode to EOD" from a plain EOD
                # leg in the audit trail and the Exit Reasons split.
                reason = hard_close_reason if not legacy else "EOD"
                if st["mtc_applied"] and reason == "EOD":
                    reason = "EOD_MTC"
                st["exit"] = (st["last_ts"], st["last_close"], reason, False)
                st["open"] = False
            elif st.get("carried") and next_open_ts is not None:
                # ── ONE_NIGHT_MAX (2026-07-21) ── a position lives AT MOST
                # one night. Any leg carried INTO this session must leave it,
                # even if the session had no candle for it at/after
                # next_open_time (band-exit: the strike the market ran away
                # from stops being captured — carry_dark_legs). Marking it at
                # its stale last close would price the exit at yesterday's
                # premium; the runner therefore re-marks these SYNTHETICALLY
                # (rolling this leg's own IV forward with tau decay), with
                # intrinsic as the last-resort fallback. We book the leg here
                # with the stale mark and let the runner overwrite it — the
                # engine is corpus-blind and cannot price anything.
                st["open"] = False
                st["exit"] = (st["last_ts"], st["last_close"],
                              "NEXT_OPEN_DARK", False)
                flags["next_open_dark"] = flags.get("next_open_dark", 0) + 1
            else:
                # ── IC_V2 ── survives the night (ENTRY day only)
                carry_out[lid] = {
                    "leg": leg, "entry_price": st["entry_price"],
                    "sl": st["sl"], "tp": st["tp"],
                    "mtc_applied": st["mtc_applied"],
                    "last_close": st["last_close"], "last_ts": st["last_ts"],
                    "entry_ts": st.get("entry_ts", entry_ts),
                    "symbol": st.get("symbol") or symbols_by_leg.get(lid),
                    "strike": st.get("strike"), "expiry": st.get("expiry"),
                    "is_adjust": bool(st.get("is_adjust")),
                    "adjust_of": st.get("adjust_of"),
                    # ── SYNTH_EVERYWHERE ── provenance survives the night
                    "synthetic": bool(st.get("synthetic")),
                    "synth_kind": st.get("synth_kind"),
                    "watch_from": 0,
                }
                flags["carried"] += 1
                continue
        ets, epx, reason, ambiguous = st["exit"]
        trades.append({
            "leg": lid,
            "tradingsymbol": st.get("symbol") or symbols_by_leg.get(lid),
            "action": leg["action"], "opt_type": leg["opt_type"],
            "lots": leg["lots"],
            "entry_ts": st.get("entry_ts", entry_ts),
            "entry_price": st["entry_price"],
            "exit_ts": ets, "exit_price": epx, "exit_reason": reason,
            "sl_price": st["sl"], "tp_price": st["tp"],
            "mtc_applied": st["mtc_applied"],
            "ambiguous_fill": bool(ambiguous),
            # ── IC_V2 ── adjustment provenance (runner tags the row)
            "is_adjust": bool(st.get("is_adjust")),
            "adjust_of": st.get("adjust_of"),
            "strike": st.get("strike"), "expiry": st.get("expiry"),
            # ── SYNTH_EVERYWHERE ── model provenance (runner buckets P&L)
            "synthetic": bool(st.get("synthetic")),
            "synth_kind": st.get("synth_kind"),
            # last observed mark, for the runner's dark re-mark path
            "last_close": st.get("last_close"),
            "last_ts": st.get("last_ts"),
        })
    trades.sort(key=lambda t: (t["entry_ts"], t["leg"]))
    return {"trades": trades, "carry_out": carry_out, "flags": flags}


def simulate_day(legs: List[dict], candles_by_leg: Dict[str, List[dict]],
                 symbols_by_leg: Dict[str, str],
                 entry_ts: int, eod_ts: int) -> dict:
    """── IC_V1 BACK-COMPAT ── the original signature, unchanged semantics.
    Delegates to simulate_session with the legacy switches, so the IC_V1
    runner path is untouched by the IC_V2 work.

    Returns {"trades": [...], "flags": {double_sl, mtc_activations,
    ambiguous, no_exit_data}} exactly as before (extra IC_V2 flags ride
    along harmlessly; IC_V1's runner reads only the four it knows)."""
    res = simulate_session(legs, candles_by_leg, symbols_by_leg,
                           entry_ts, eod_ts, exit_mode="EOD")
    # IC_V1's runner sorts on leg id alone; preserve that ordering exactly.
    res["trades"].sort(key=lambda t: t["leg"])
    return {"trades": res["trades"], "flags": res["flags"]}


def leg_pnl(trade: dict, qty: int) -> float:
    """Gross P&L for one leg. SELL profits when premium falls."""
    d = trade["entry_price"] - trade["exit_price"]
    return (d if trade["action"] == "SELL" else -d) * qty