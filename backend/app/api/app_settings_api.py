"""
APP SETTINGS + IN-APP EVENTS API
Path: app/api/app_settings_api.py

Two concerns, one self-contained module:

  1. App Settings — JSON-backed config for in-app notification prefs:
       notify_toast : bool                      (global toast on/off)
       notify_audio : bool                      (MASTER sound on/off)
       audio_rules  : { strategy: {PAPER, LIVE} } (per-strategy / per-mode sound)
     Stored in its OWN file (~/.scalp-app/app_settings.json) — does NOT touch
     telegram_config.json or any existing global config.

  2. In-app event feed — GET /api/app/events?after=<id> returns recent trade
     events from the in-memory ring buffer (app/event_bus/inapp_events.py).

WIRING (same pattern as the telegram router):

    from app.api.app_settings_api import router as app_settings_router
    app.include_router(app_settings_router)
"""

import json
from pathlib import Path
from typing import Dict
from fastapi import APIRouter
from pydantic import BaseModel

from app.event_bus.inapp_events import get_events_after

router = APIRouter(prefix="/api/app", tags=["app_settings"])

# ═══════════════════════════════════════════════════════════
#  STORAGE  (own file — nothing existing touched)
# ═══════════════════════════════════════════════════════════

SETTINGS_FILE = Path.home() / ".scalp-app" / "app_settings.json"

# Known strategies for the audio matrix. New strategies not listed here still
# default to ON (fail-open) via the resolver below.
STRATEGIES = ["SCALP_V1", "BB_V1", "BB_V2", "HA_V1", "SCALP_V2"]


def _default_audio_rules() -> dict:
    return {sid: {"PAPER": True, "LIVE": True} for sid in STRATEGIES}


_DEFAULTS = {
    "notify_toast": True,
    "notify_audio": True,                  # master sound switch
    "audio_rules":  _default_audio_rules(),  # per-strategy / per-mode
    "show_account_balance": True,          # show Zerodha balance in header
}


def _merge_defaults(data: dict) -> dict:
    """Merge stored data over defaults so missing keys are filled in, and the
    audio_rules map always has an entry per known strategy/mode."""
    out = {
        "notify_toast": data.get("notify_toast", True) is not False,
        "notify_audio": data.get("notify_audio", True) is not False,
        "show_account_balance": data.get("show_account_balance", True) is not False,
    }
    rules_in = data.get("audio_rules", {}) or {}
    rules_out = _default_audio_rules()
    for sid, modes in rules_in.items():
        if not isinstance(modes, dict):
            continue
        if sid not in rules_out:
            rules_out[sid] = {"PAPER": True, "LIVE": True}
        for mode in ("PAPER", "LIVE"):
            if mode in modes:
                rules_out[sid][mode] = modes[mode] is not False
    out["audio_rules"] = rules_out
    return out


def _load() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
            return _merge_defaults(data or {})
        except Exception as e:
            print(f"[APP_SETTINGS] Failed to load: {e}")
    return _merge_defaults({})


def _save(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[APP_SETTINGS] Failed to save: {e}")


# ═══════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════

class ModeRule(BaseModel):
    PAPER: bool = True
    LIVE: bool = True


class AppSettings(BaseModel):
    notify_toast: bool = True
    notify_audio: bool = True
    show_account_balance: bool = True
    # audio_rules is a free-form { strategy: {PAPER, LIVE} } map
    audio_rules: Dict[str, ModeRule] = {}


# ═══════════════════════════════════════════════════════════
#  SETTINGS ENDPOINTS
# ═══════════════════════════════════════════════════════════

@router.get("/settings")
async def get_app_settings():
    return _load()


@router.post("/settings")
async def save_app_settings(settings: AppSettings):
    # Normalise audio_rules to plain dicts and merge over defaults so the saved
    # file is always complete and well-formed.
    raw = {
        "notify_toast": settings.notify_toast,
        "notify_audio": settings.notify_audio,
        "show_account_balance": settings.show_account_balance,
        "audio_rules": {sid: rule.dict() for sid, rule in settings.audio_rules.items()},
    }
    merged = _merge_defaults(raw)
    _save(merged)
    return {"success": True, **merged}


# ═══════════════════════════════════════════════════════════
#  IN-APP EVENT FEED
# ═══════════════════════════════════════════════════════════

@router.get("/events")
async def get_events(after: int = 0):
    """
    Returns trade events newer than `after`. The client passes the last
    latest_id it saw. First call (after=0) returns no backlog — just the
    current cursor — so old events aren't replayed on page load.
    """
    return get_events_after(after)