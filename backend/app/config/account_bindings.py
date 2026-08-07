# backend/app/config/account_bindings.py
# ============================================================
# ACC2 BEGIN — Per-strategy execution-account binding (D2c)
#
# SINGLE SOURCE: ~/.scalp-app/angelone/bindings.json
#   { "SCALP_V3": "ANGELONE", ... }   (absent strategy -> registry default)
#
# lots_whitelist.py discipline applies: this module deliberately imports
# NOTHING heavy — executor_factory and acc2_routes both consume it, and
# it must never drag FastAPI or broker SDKs into the execution path.
#
# D8 layer 1: STRATEGY_SIDE labels buy-side vs sell-side families;
# conflict_check() flags any account carrying both sides so the UI can
# show the two-tap netting warning before save.
# ============================================================

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List

BINDINGS_PATH = (Path.home() / ".scalp-app" / "angelone" / "bindings.json")

VALID_BROKERS = ("ZERODHA", "ANGELONE")
DEFAULT_BROKER = "ZERODHA"

# Buy-side = net long options; Sell-side = net short options.
# Used ONLY for the D8 warning — never to block.
STRATEGY_SIDE = {
    "SCALP_V1": "BUY", "SCALP_V3": "BUY", "SCALP_V5": "BUY",
    "BB_V1": "BUY", "BB_V2": "BUY", "HA_V1": "BUY", "TMA_V1": "BUY",
    "TSG_V1": "SELL", "IC_V1": "SELL", "IC_V2": "SELL",
    "PST_SELL": "SELL", "PST_HEDGE": "BUY",
}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_bindings() -> Dict[str, str]:
    if not BINDINGS_PATH.exists():
        return {}
    try:
        raw = json.loads(BINDINGS_PATH.read_text())
        return {k: v for k, v in raw.items() if v in VALID_BROKERS}
    except Exception:
        # Unreadable bindings -> everything falls back to registry default
        # (fail closed onto the battle-tested primary).
        return {}


def save_bindings(bindings: Dict[str, str]) -> None:
    clean = {k: v for k, v in (bindings or {}).items()
             if v in VALID_BROKERS}
    _atomic_write(BINDINGS_PATH, clean)


def resolve_broker(strategy_id: str, registry_default: str = DEFAULT_BROKER) -> str:
    b = load_bindings().get(strategy_id)
    return b if b in VALID_BROKERS else (registry_default or DEFAULT_BROKER)


def conflict_check(bindings: Dict[str, str]) -> List[str]:
    """
    Returns list of broker names that would carry BOTH buy-side and
    sell-side strategies under the given (full, merged) bindings.
    Empty list = no netting-risk warning needed.
    """
    sides_per_broker: Dict[str, set] = {}
    for sid, side in STRATEGY_SIDE.items():
        broker = bindings.get(sid, DEFAULT_BROKER)
        sides_per_broker.setdefault(broker, set()).add(side)
    return sorted(b for b, sides in sides_per_broker.items()
                  if {"BUY", "SELL"} <= sides)

# ACC2 END