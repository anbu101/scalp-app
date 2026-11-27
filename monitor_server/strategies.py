"""
monitor_server/strategies.py — ── ALERT_STRATEGY_INFER_20261127 ──

Strategy id → codename, a COPY of the app's STRATEGY_DISPLAY
(backend/app/config/strategy_display.py; the droplet runs without the app's
code). test_monitor_server.py fails if the two drift apart. Codenames only,
like every other place outside the admin UI.

  infer(text) -> (strategy_id, codename) | (None, None)
    An alert whose app did not name a strategy: an id anywhere ("TMA_V2: SL GTT…")
    or a codename that starts the message ("Velvet: no NIFTY tick…"). A codename
    elsewhere in the text is not taken (an ordinary word, e.g. "circuit breaker").
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

CODENAMES = {
    "SCALP_V1": "Scala",
    "SCALP_V2": "Scarab",
    "SCALP_V3": "Scenic",
    "SCALP_V4": "Scaffold",
    "SCALP_V5": "Scribe",
    "IC_V1": "Indica",
    "IC_V2": "Icarus",
    "TSG_V1": "Tigris",
    "GC_V1": "Glacier",
    "BB_V1": "Bobbin",
    "BB_V2": "Baobab",
    "HA_V1": "Harbor",
    "TMA_V1": "Tomahawk",
    "TMA_V2": "Timberwolf",
    "VET_V1": "Velvet",
    "ORB_V1": "Outrider",
    "BRK_V1": "Breaker",
    "FVG_V1": "Fissure",
    "EMC_V1": "Ember",
    "LVB_V1": "Tripwire",
    "STFC_V1": "Sabre",
}

_ID = re.compile(r"\b(" + "|".join(sorted(map(re.escape, CODENAMES), key=len, reverse=True)) + r")\b")
_BY_NAME = {v.lower(): k for k, v in CODENAMES.items()}
_LEAD = re.compile(r"^\W*(" + "|".join(sorted(map(re.escape, CODENAMES.values()), key=len, reverse=True)) + r")\b")


def codename(sid: Optional[str]) -> Optional[str]:
    return CODENAMES.get(str(sid or "").upper()) if sid else None


def infer(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = str(text or "")
    m = _ID.search(t)
    if m:
        return m.group(1), CODENAMES[m.group(1)]
    m = _LEAD.match(t)
    if m:
        sid = _BY_NAME[m.group(1).lower()]
        return sid, CODENAMES[sid]
    return None, None
