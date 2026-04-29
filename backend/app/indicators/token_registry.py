"""
token_registry.py
==================
Persists BANKNIFTY monthly futures instrument tokens so they are
available for historical backfill even after Zerodha removes expired
contracts from their instrument master.

Written by bb_tick_engine.__init__ on every startup.
Read by backfill_futures_candles.py when resolving old contracts.

File location: ~/.scalp-app/state/futures_token_registry.json

Format:
{
  "2026-04": {"token": 17072130, "symbol": "BANKNIFTY26APRFUT", "expiry": "2026-04-28"},
  "2026-03": {"token": 16012345, "symbol": "BANKNIFTY26MARFUT", "expiry": "2026-03-26"},
  ...
}
"""

import json
from pathlib import Path
from datetime import date

from app.event_bus.audit_logger import write_audit_log

REGISTRY_PATH = Path.home() / ".scalp-app" / "state" / "futures_token_registry.json"


def save_contract(token: int, symbol: str, expiry: date):
    """
    Save this month's contract token to the registry.
    Safe to call on every startup — existing entries are never overwritten,
    so historical tokens are preserved even after contract expiry.
    """
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load existing registry
    registry = {}
    if REGISTRY_PATH.exists():
        try:
            registry = json.loads(REGISTRY_PATH.read_text())
        except Exception as e:
            write_audit_log(f"[TOKEN_REGISTRY] Failed to read registry: {e}")

    # Key is YYYY-MM (year and month of the CONTRACT, not expiry)
    # We derive it from the expiry: Apr-28 -> Apr contract -> "2026-04"
    month_key = f"{expiry.year}-{expiry.month:02d}"

    if month_key in registry:
        write_audit_log(
            f"[TOKEN_REGISTRY] {month_key} already recorded "
            f"(token={registry[month_key]['token']}) — skipping"
        )
        return

    registry[month_key] = {
        "token":  token,
        "symbol": symbol,
        "expiry": expiry.isoformat(),
    }

    try:
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
        write_audit_log(
            f"[TOKEN_REGISTRY] Saved: {month_key} -> "
            f"token={token} symbol={symbol} expiry={expiry}"
        )
    except Exception as e:
        write_audit_log(f"[TOKEN_REGISTRY] Failed to write registry: {e}")


def load_registry() -> dict:
    """
    Load the full token registry.
    Returns dict keyed by 'YYYY-MM' strings.
    """
    if not REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except Exception:
        return {}


def get_token_for_month(year: int, month: int):
    """
    Returns (token, symbol, expiry_date) for the given month,
    or (None, None, None) if not found.
    """
    from datetime import date as _date
    registry = load_registry()
    key      = f"{year}-{month:02d}"
    entry    = registry.get(key)
    if not entry:
        return None, None, None
    try:
        expiry = _date.fromisoformat(entry["expiry"])
        return int(entry["token"]), entry["symbol"], expiry
    except Exception:
        return None, None, None