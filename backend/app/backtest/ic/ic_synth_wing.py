# backend/app/backtest/ic/ic_synth_wing.py
#
# ── IC_SYNTH_WING ── Black–Scholes machinery for MODELED historical wings.
#
# WHY THIS EXISTS: Dhan's expired-options endpoint is hard-capped at ATM±10
# strikes, so the corpus cannot contain real ₹4-class far-OTM weekly wings
# for history. A real ₹35 substitute distorts the condor's economics by an
# order of magnitude more than a modeled ₹4 wing does.
#
# SCOPE — deliberately tiny: wings carry no SL/TP, so a wing needs exactly
# TWO prices per day (entry, EOD). No synthetic candles are ever inserted
# into the corpus; the runner computes two BS prices and books a flagged
# trade. IV is IMPLIED from the cheapest REAL strike on the same side at
# the same minute (the ±10 edge — today's fallback pick), so the model is
# anchored to observed prices, not assumptions.
#
# HONESTY CONTRACT (enforced by the runner integration):
#   * synthetic wings are used ONLY when no real strike ≤ cap exists
#   * trades are tagged (SYN- symbol prefix + wing_synthetic DIAG counters)
#   * flat vol from the edge strike ignores skew → far wings are somewhat
#     UNDERPRICED; skew_mult (default 1.0) lets you bump synthetic premiums
#     (e.g. 1.25) if you want a conservative wing-cost estimate
#   * any solver failure falls back to the existing real-cheapest behavior
#     (fail open to reality, never to the model)
#
# Pure module: stdlib math only, no app imports — fully unit-tested.

from __future__ import annotations

import math
from typing import Optional, Tuple

RISK_FREE = 0.065          # crude but adequate: ₹4 wings have ~zero rho
MIN_TAU_MIN = 5.0          # floor time-to-expiry at 5 minutes
PRICE_TICK = 0.05


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(is_call: bool, spot: float, strike: float, tau_years: float,
             iv: float, r: float = RISK_FREE) -> float:
    """Plain Black–Scholes (European; NIFTY weeklies are European)."""
    if tau_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
        return intrinsic
    sq = iv * math.sqrt(tau_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * tau_years) / sq
    d2 = d1 - sq
    if is_call:
        return spot * _norm_cdf(d1) - strike * math.exp(-r * tau_years) * _norm_cdf(d2)
    return strike * math.exp(-r * tau_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price: float, is_call: bool, spot: float, strike: float,
                tau_years: float, r: float = RISK_FREE,
                lo: float = 0.01, hi: float = 5.0,
                tol: float = 1e-4, max_iter: int = 80) -> Optional[float]:
    """Bisection IV solve — monotone in vol, so bisection is bulletproof
    where Newton can wander on tiny far-OTM prices. None when the observed
    price sits outside the [lo, hi]-vol envelope (stale/crossed print)."""
    if price <= 0 or tau_years <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    if price <= intrinsic + 1e-9:
        return None
    p_lo = bs_price(is_call, spot, strike, tau_years, lo, r)
    p_hi = bs_price(is_call, spot, strike, tau_years, hi, r)
    if not (p_lo <= price <= p_hi):
        return None
    a, b = lo, hi
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        pm = bs_price(is_call, spot, strike, tau_years, m, r)
        if abs(pm - price) < tol:
            return m
        if pm < price:
            a = m
        else:
            b = m
    return 0.5 * (a + b)


def tau_years(now_ts: int, expiry_ts: int) -> float:
    """Calendar-time tau with a small floor — on expiry afternoon a zero tau
    would degenerate every price to intrinsic."""
    mins = max(MIN_TAU_MIN, (expiry_ts - now_ts) / 60.0)
    return mins / (365.0 * 24.0 * 60.0)


def solve_wing_strike(is_call: bool, spot: float, tau: float, iv: float,
                      target_premium: float, strike_step: float = 50.0,
                      start_strike: Optional[float] = None,
                      max_steps: int = 120,
                      skew_mult: float = 1.0,
                      r: float = RISK_FREE) -> Optional[Tuple[float, float]]:
    """Walk OTM in strike_step increments from start_strike (default: first
    step beyond spot) and return (strike, premium) for the FIRST strike whose
    modeled premium is ≤ target — the synthetic analog of 'premium lesser
    than' nearest-below selection. Premium is skew_mult-adjusted and tick-
    rounded with a PRICE_TICK floor. None if even max_steps out stays above
    target (degenerate vol regime — caller falls back to reality)."""
    if iv is None or iv <= 0 or spot <= 0 or tau <= 0 or target_premium <= 0:
        return None
    if start_strike is None:
        base = math.ceil(spot / strike_step) * strike_step if is_call \
            else math.floor(spot / strike_step) * strike_step
        start_strike = base
    k = float(start_strike)
    for _ in range(max_steps):
        px = bs_price(is_call, spot, k, tau, iv, r) * skew_mult
        px = max(PRICE_TICK, round(px / PRICE_TICK) * PRICE_TICK)
        if px <= target_premium:
            return k, px
        k = k + strike_step if is_call else k - strike_step
        if k <= 0:
            return None
    return None


def price_wing(is_call: bool, spot: float, strike: float, tau: float,
               iv: float, skew_mult: float = 1.0,
               r: float = RISK_FREE) -> float:
    """Tick-rounded, floored premium for an already-chosen synthetic strike
    (used for the EOD leg of the two-price trade)."""
    px = bs_price(is_call, spot, strike, tau, iv, r) * skew_mult
    return max(PRICE_TICK, round(px / PRICE_TICK) * PRICE_TICK)


def synth_symbol(underlying: str, expiry_iso: str, strike: float,
                 is_call: bool) -> str:
    """Unmistakably-synthetic tradingsymbol: SYN-NIFTY-20260709-24500CE.
    persist_run stores it verbatim; every report/table shows the SYN- prefix."""
    return f"SYN-{underlying}-{expiry_iso.replace('-', '')}-{int(strike)}{'CE' if is_call else 'PE'}"