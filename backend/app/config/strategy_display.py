"""
STRATEGY DISPLAY NAMES — BACKEND MIRROR (UI_MASK)
app/config/strategy_display.py

Backend counterpart of frontend/src/strategies/displayNames.js. Dependency-
free by design (stdlib only) so ANY module can import it without dragging in
config/db/api — same discipline as lots_whitelist.py.

WHY THIS EXISTS
---------------
The frontend masks strategy names for non-admin licenses, but Telegram had no
such layer: every notification printed the raw strategy_id ("TSG_V1") and real
names ("Iron Condor V1"), and the EOD summary card rendered raw ids straight
into the PNG. A STANDARD-tier user pointing the app at their own bot received
the real names the UI deliberately hides.

POLICY (decided 2026-08-06): Telegram is CODENAME-ONLY, unconditionally — for
admin too. Rationale: the send path has no session/role context, so a
role-aware branch would have to resolve entitlement inside a notification
callback and pick a fail direction; codename-always removes the question, and
keeps forwarded screenshots safe by construction. Admin decodes them by eye —
the decode rule is documented in displayNames.js.

⚠️ DRIFT WARNING
----------------
This map is a MIRROR of frontend/src/strategies/displayNames.js. Adding or
renaming a strategy means editing BOTH. Same class of footgun as the
paramFormat.js copies and admin_ui.html's ALL_STRATEGIES chip list. A missing
entry fails SAFE-ish (codename() returns the raw id) — which means the leak
reappears for that strategy, so treat an unmapped id as a bug, not a default.
"""

import re

# id -> (real name as shown in the admin UI, codename shown everywhere else)
STRATEGY_DISPLAY = {
    "SCALP_V1":  ("Scalp V1",       "Scala"),
    "SCALP_V2":  ("Scalp V2",       "Scarab"),     # removed; historical rows
    "SCALP_V3":  ("Scalp V3",       "Scenic"),
    "SCALP_V4":  ("Scalp V4",       "Scaffold"),   # removed; historical rows
    "SCALP_V5":  ("Scalp V5",       "Scribe"),
    "IC_V1":     ("Iron Condor V1", "Indica"),
    "IC_V2":     ("Iron Condor V2", "Icarus"),
    "TSG_V1":    ("Time Strangle",  "Tigris"),
    "GC_V1":     ("GC V1",          "Glacier"),
    "BB_V1":     ("BB V1",          "Bobbin"),
    "BB_V2":     ("BB V2",          "Baobab"),
    "HA_V1":     ("Heikin Ashi",    "Harbor"),
    "PST_SELL":  ("PST Sell",       "Pistol"),
    "PST_HEDGE": ("PST Hedge",      "Pastel"),
    "TMA_V1":    ("TMA V1",         "Tomahawk"),
}


def codename(strategy_id) -> str:
    """
    'TSG_V1' -> 'Tigris'. Unknown/blank ids return the input unchanged so a
    newly added strategy is VISIBLE (and therefore noticed) rather than
    silently blanked out of an alert.
    """
    if not strategy_id:
        return ""
    entry = STRATEGY_DISPLAY.get(str(strategy_id).strip().upper())
    return entry[1] if entry else str(strategy_id)


# ── Prose scrubber ──────────────────────────────────────────────────────
# Structured fields are masked at their call sites, but plenty of alert text
# embeds names in free prose ("HA_V1 entry order REJECTED for ...", "TSG_V1
# max-loss guard active"). Masking only the structured field would leave those
# untouched, so the final outgoing string gets one scrub pass as a backstop.
#
# Longest-first alternation, single pass: a replacement can never be re-matched
# by a later pattern (no codename contains an id or a real name).

_TOKEN_TO_CODE = {}
for _sid, (_real, _code) in STRATEGY_DISPLAY.items():
    _TOKEN_TO_CODE[_sid] = _code
    _TOKEN_TO_CODE[_real] = _code

_MASK_RE = re.compile(
    "|".join(re.escape(t) for t in
             sorted(_TOKEN_TO_CODE, key=len, reverse=True))
)


def mask_text(text) -> str:
    """
    Replace every strategy id / real name in `text` with its codename.
    Never raises — a notification must never break on a formatting helper,
    so any failure returns the original text unchanged.
    """
    if not text:
        return text
    try:
        return _MASK_RE.sub(lambda m: _TOKEN_TO_CODE[m.group(0)], str(text))
    except Exception:
        return text