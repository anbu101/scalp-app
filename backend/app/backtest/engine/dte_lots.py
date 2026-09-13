# backend/app/backtest/engine/dte_lots.py
#
# ── DTE_LOT_MULT_20260911 ── per-DTE lot multiplier for backtest runners.
#   dte_lot_mult = {dte: mult}; absent → 1.0; 0 → skip the day;
#   lots_day = max(1, round(lots × mult)).
# DTE = trading sessions from the sim day to its expected expiry, from the
# corpus calendar (app.backtest.repo.trading_calendar) — the same numbers
# the Sessions-to-Expiry breakdown shows.
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def parse_dte_lot_mult(raw) -> Dict[int, float]:
    """Accepts {"0": 2, 4: 1.5}, "0:2, 1:0, 4:1.5", or [[0, 2], [1, 0]].
    Invalid entries are ignored; negatives clamp to 0."""
    out: Dict[int, float] = {}
    if raw is None:
        return out
    items = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, str):
        for part in raw.replace(";", ",").split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                items.append((k.strip(), v.strip()))
    elif isinstance(raw, (list, tuple)):
        for it in raw:
            if isinstance(it, (list, tuple)) and len(it) == 2:
                items.append((it[0], it[1]))
    for k, v in items:
        try:
            ki = int(str(k).strip().upper().replace("DTE", ""))
            vf = float(v)
        except (TypeError, ValueError):
            continue
        if ki < 0:
            continue
        out[ki] = max(0.0, vf)
    return out


def lots_for_day(base_lots: int, mult_map: Dict[int, float],
                 dte: Optional[int]) -> Tuple[int, str]:
    """→ (lots_for_this_day, tag) with tag in {"base","scaled","skip","unknown"}.
    dte None (calendar unavailable) → base lots, tag "unknown" (fail-open)."""
    base = int(base_lots or 0)
    if not mult_map:
        return base, "base"
    if dte is None:
        return base, "unknown"
    m = mult_map.get(int(dte))
    if m is None:
        return base, "base"
    if m <= 0:
        return 0, "skip"
    return max(1, int(round(base * m))), "scaled"


def dte_for_day(cal_sorted: List[str], day_iso: str, expiry_iso: str) -> Optional[int]:
    from app.backtest.repo.trading_calendar import sessions_to_expiry
    return sessions_to_expiry(cal_sorted, day_iso, expiry_iso)
