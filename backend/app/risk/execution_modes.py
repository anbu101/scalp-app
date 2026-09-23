# backend/app/risk/execution_modes.py
#
# ── FLEET_MODES_20260923 ── ONE vocabulary for trade_execution_mode.
#
#   OFF         no NEW entries; an open position is still managed to its
#               normal exit (never orphaned because someone clicked OFF).
#   PAPER       simulated book only.
#   LIVE        real orders only.
#   PAPER_LIVE  real orders, PLUS a paper twin of every live position
#               (app.trading.shadow_book) so the paper track record keeps
#               growing while the strategy trades live. Same lots.
#
# Every engine keeps its two execution branches (paper / live). This module
# answers the only two questions those branches need:
#     entries_allowed(mode)  — may a NEW position be opened right now?
#     wants_live(mode)       — does the primary book place broker orders?
# and one for the persistence layer:
#     shadow_active(sid)     — should a LIVE row get a paper twin?
#
# Fail-closed everywhere: an unknown / unreadable value is PAPER, never LIVE.
# Old code that does not know PAPER_LIVE and compares `== "LIVE"` therefore
# papers on a half-applied fleet — the safe direction.

from __future__ import annotations

from typing import Tuple

OFF = "OFF"
PAPER = "PAPER"
LIVE = "LIVE"
PAPER_LIVE = "PAPER_LIVE"
ALL_MODES = (OFF, PAPER, LIVE, PAPER_LIVE)

# spellings the UI / operators may hand us; the canonical form is stored
_ALIASES = {
    "PAPER+LIVE": PAPER_LIVE, "PAPER_LIVE": PAPER_LIVE, "PAPERLIVE": PAPER_LIVE,
    "PAPER LIVE": PAPER_LIVE, "LIVE+PAPER": PAPER_LIVE, "BOTH": PAPER_LIVE,
    "PAPER-LIVE": PAPER_LIVE, "PAPER&LIVE": PAPER_LIVE,
    "OFF": OFF, "PAPER": PAPER, "LIVE": LIVE, "DISABLED": OFF, "NONE": OFF,
}


def normalize(raw, default: str = PAPER) -> str:
    """Canonical mode string. Unknown / None → `default` (PAPER unless the
    caller has a startup mode to hold)."""
    if raw is None:
        return default
    m = _ALIASES.get(str(raw).strip().upper())
    return m if m in ALL_MODES else (default if default in ALL_MODES else PAPER)


def wants_live(mode) -> bool:
    return normalize(mode) in (LIVE, PAPER_LIVE)


def wants_paper(mode) -> bool:
    return normalize(mode) in (PAPER, PAPER_LIVE)


def entries_allowed(mode) -> bool:
    return normalize(mode) != OFF


def is_shadow(mode) -> bool:
    return normalize(mode) == PAPER_LIVE


def book(mode) -> str:
    """The primary position's book: 'LIVE' or 'PAPER'. OFF → 'PAPER' (an
    OFF strategy that still holds a paper row manages it as paper)."""
    return LIVE if wants_live(mode) else PAPER


def boot_mode(raw, *, allow_off: bool) -> str:
    """Startup value for runtimes that build their engine once at boot.
    PAPER_LIVE boots the LIVE engine (the twin is a persistence concern).
    OFF boots the PAPER engine where the engine has no OFF notion; entries
    are refused at signal time by the manager's fresh config read."""
    m = normalize(raw)
    if m == PAPER_LIVE:
        return LIVE
    if m == OFF and not allow_off:
        return PAPER
    return m


def strategy_mode(strategy_id: str, default: str = PAPER) -> str:
    """Canonical mode from the strategy's config file. Fail-closed."""
    try:
        from app.config.strategy_loader import load_strategy_config
        cfg = load_strategy_config(strategy_id) or {}
        return normalize(cfg.get("trade_execution_mode"), default)
    except Exception:
        return default


def resolve_execution_plan(strategy_id: str) -> Tuple[str, bool]:
    """(mode, degraded). `mode` is the canonical 4-value string; LIVE /
    PAPER_LIVE are returned ONLY on a clean read (resolve_execution_mode
    doctrine) — a degraded read that was configured live comes back
    ('PAPER', True) so callers can alert."""
    try:
        from app.config.strategy_loader import load_strategy_config_ex
        cfg, degraded = load_strategy_config_ex(strategy_id)
    except Exception:
        return PAPER, True
    m = normalize((cfg or {}).get("trade_execution_mode"))
    if degraded:
        return PAPER, wants_live(m)
    return m, False


def shadow_active(strategy_id: str) -> bool:
    """True only on a CLEAN PAPER_LIVE read — a degraded read never creates
    a paper twin nobody asked for."""
    m, degraded = resolve_execution_plan(strategy_id)
    return (not degraded) and m == PAPER_LIVE


def lot_size_for(strategy_id: str, default: int = 65) -> int:
    try:
        from app.config.strategy_loader import load_strategy_config
        q = (load_strategy_config(strategy_id) or {}).get("quantity") or {}
        return int(q.get("lot_size") or default)
    except Exception:
        return default
