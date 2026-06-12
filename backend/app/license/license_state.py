# backend/app/license/license_state.py
"""
SINGLE SOURCE OF TRUTH for license status + entitlements.

Written ONLY by license_client (and nothing else). Read by:
  - api_server.py        (strategy launch gating)
  - system_routes.py     (/system/license for the UI)
  - any future masking logic (ui_level)

PHASE 2: statuses extended for the server-issued token model, plus the
ENTITLEMENTS object and the helpers the rest of the app uses. Keep the
module-level-globals pattern (matches v1 and the rest of the codebase).
"""

import time
from enum import Enum


class LicenseStatus(str, Enum):
    VALID = "VALID"                  # token verified, comfortably inside lifetime
    GRACE = "GRACE"                  # token verified but < GRACE_THRESHOLD days left
                                     # (server unreachable for a while) - still usable
    EXPIRED = "EXPIRED"              # token past exp, or server says license expired
    REVOKED = "REVOKED"              # server says revoked
    UNACTIVATED = "UNACTIVATED"      # no license key stored yet
    CLOCK_TAMPER = "CLOCK_TAMPER"    # local clock behind last seen server time
    INVALID = "INVALID"              # bad signature / wrong machine / unknown key


# --------------------------------------------------
# STATE (written only by license_client)
# --------------------------------------------------

LICENSE_STATUS: LicenseStatus = LicenseStatus.UNACTIVATED
LICENSE_MESSAGE: str = "License not checked"

TIER: str = ""                    # ADMIN / STANDARD / TRIAL
LABEL: str = ""                   # e.g. "Anbu - main" (from server response)
LICENSE_EXPIRES_AT: str = ""      # YYYY-MM-DD (license itself, not the token)
TOKEN_EXP: int = 0                # epoch seconds - current token expiry
ENTITLEMENTS: dict = {}           # {"strategies": [...], "max_lots": 0,
                                  #  "live_trading": true, "ui_level": "..."}


# --------------------------------------------------
# HELPERS (read-only views for the rest of the app)
# --------------------------------------------------

_USABLE = (LicenseStatus.VALID, LicenseStatus.GRACE)


def is_usable() -> bool:
    """True when the app may operate (trade, launch strategies)."""
    return LICENSE_STATUS in _USABLE


def license_allows_strategy(strategy_id: str) -> bool:
    """
    THE strategy-gating check, used at the three launch sites in
    api_server.py. ADMIN licenses carry strategies=["*"] so this returns
    True for everything -> provably identical behavior to pre-license
    builds (BB_V1 isolation argument).
    """
    if not is_usable():
        return False
    strategies = ENTITLEMENTS.get("strategies") or []
    return "*" in strategies or strategy_id in strategies


def ui_level() -> str:
    """'admin' or 'standard'. Drives response masking (Phase 2b)."""
    if not is_usable():
        return "standard"
    return ENTITLEMENTS.get("ui_level", "standard")


def grace_days_left() -> float | None:
    """Days until the current token dies (None if no token)."""
    if not TOKEN_EXP:
        return None
    return max(0.0, round((TOKEN_EXP - time.time()) / 86400, 2))