"""
license_server/db.py

SQLite storage for the Scalp License Server.

Single table, WAL mode, per-call connections (volume is tiny: tens of
users x a few heartbeats/day). All timestamps stored as ISO-8601 UTC
strings except expires_at, which is a plain YYYY-MM-DD date interpreted
as end-of-day in Asia/Kolkata (matches how Anbu thinks about expiry).
"""

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

DB_PATH = Path(os.environ.get("LICSRV_DB", Path(__file__).resolve().parent / "licenses.db"))

# Unambiguous alphabet for license keys (no I/L/O/0/1)
_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# --------------------------------------------------
# TIER DEFAULTS
# --------------------------------------------------
# max_lots: 0 = unlimited (kept in schema for future use - no caps today)
# STANDARD and TRIAL are identical in capability per Anbu's decision;
# they differ only in default duration. ADMIN alone gets ui_level=admin,
# which Phase 2 uses to decide masked-vs-full BB dashboards and full
# strategy visibility.

DEFAULT_NON_ADMIN_STRATEGIES = ["SCALP_V1"]  # TODO(Anbu): confirm default list

TIER_DEFAULTS = {
    "ADMIN": {
        "strategies": ["*"],
        "max_lots": 0,
        "live_trading": True,
        "ui_level": "admin",
    },
    "STANDARD": {
        "strategies": DEFAULT_NON_ADMIN_STRATEGIES,
        "max_lots": 0,
        "live_trading": True,
        "ui_level": "standard",
    },
    "TRIAL": {
        "strategies": DEFAULT_NON_ADMIN_STRATEGIES,
        "max_lots": 0,
        "live_trading": True,
        "ui_level": "standard",
    },
}

DEFAULT_DAYS = {"ADMIN": 3650, "STANDARD": 90, "TRIAL": 7}

# --------------------------------------------------
# CONNECTION
# --------------------------------------------------

@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                key                TEXT PRIMARY KEY,
                label              TEXT NOT NULL,
                tier               TEXT NOT NULL CHECK (tier IN ('ADMIN','STANDARD','TRIAL')),
                entitlements_json  TEXT NOT NULL,
                expires_at         TEXT NOT NULL,   -- YYYY-MM-DD, end-of-day IST
                machine_id         TEXT,
                activated_at       TEXT,
                last_heartbeat_at  TEXT,
                revoked            INTEGER NOT NULL DEFAULT 0,
                notes              TEXT,
                created_at         TEXT NOT NULL
            )
            """
        )
        # ── CFG_OVERRIDE (role-level) ── generic KV settings; holds the
        # GLOBAL config-override set injected into every non-admin token.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_key() -> str:
    groups = [
        "".join(secrets.choice(_KEY_ALPHABET) for _ in range(4))
        for _ in range(3)
    ]
    return "SCLP-" + "-".join(groups)


def expiry_epoch(expires_at: str) -> int:
    """End-of-day IST for the stored YYYY-MM-DD date, as epoch seconds."""
    d = datetime.strptime(expires_at, "%Y-%m-%d")
    eod_ist = d.replace(hour=23, minute=59, second=59, tzinfo=IST)
    return int(eod_ist.timestamp())


def is_expired(expires_at: str) -> bool:
    import time
    return time.time() > expiry_epoch(expires_at)


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["entitlements"] = json.loads(d.pop("entitlements_json"))
    d["revoked"] = bool(d["revoked"])
    return d


# --------------------------------------------------
# QUERIES
# --------------------------------------------------

def create_license(
    label: str,
    tier: str,
    days: int | None = None,
    entitlements_override: dict | None = None,
    notes: str = "",
) -> dict:
    tier = tier.upper()
    if tier not in TIER_DEFAULTS:
        raise ValueError(f"Unknown tier: {tier}")

    days = days if days and days > 0 else DEFAULT_DAYS[tier]
    expires_at = (datetime.now(IST) + timedelta(days=days)).strftime("%Y-%m-%d")

    entitlements = dict(TIER_DEFAULTS[tier])
    if entitlements_override:
        entitlements.update(entitlements_override)

    key = generate_key()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO licenses
                (key, label, tier, entitlements_json, expires_at, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key, label, tier, json.dumps(entitlements), expires_at, notes, _now_iso()),
        )
    return get_license(key)


def get_license(key: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM licenses WHERE key = ?", (key,)).fetchone()
    return row_to_dict(row) if row else None


def list_licenses() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM licenses ORDER BY created_at DESC").fetchall()
    return [row_to_dict(r) for r in rows]


def bind_machine(key: str, machine_id: str):
    with _conn() as c:
        c.execute(
            "UPDATE licenses SET machine_id = ?, activated_at = ? WHERE key = ?",
            (machine_id, _now_iso(), key),
        )


def touch_heartbeat(key: str):
    with _conn() as c:
        c.execute(
            "UPDATE licenses SET last_heartbeat_at = ? WHERE key = ?",
            (_now_iso(), key),
        )


def set_revoked(key: str, revoked: bool) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE licenses SET revoked = ? WHERE key = ?",
            (1 if revoked else 0, key),
        )
        return cur.rowcount > 0


def extend_license(key: str, days: int) -> dict | None:
    lic = get_license(key)
    if not lic:
        return None
    # Extend from current expiry if still in the future, else from today.
    current = datetime.strptime(lic["expires_at"], "%Y-%m-%d").replace(tzinfo=IST)
    base = max(current, datetime.now(IST))
    new_expiry = (base + timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as c:
        c.execute("UPDATE licenses SET expires_at = ? WHERE key = ?", (new_expiry, key))
    return get_license(key)


def rebind(key: str) -> bool:
    """Clear machine binding so the next /activate from any machine rebinds."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE licenses SET machine_id = NULL WHERE key = ?", (key,)
        )
        return cur.rowcount > 0


def update_license(
    key: str,
    *,
    tier: str | None = None,
    expires_at: str | None = None,
    entitlements_patch: dict | None = None,
    notes: str | None = None,
) -> dict | None:
    """Update an existing license in place. Entitlement changes reach the
    app's token at its next heartbeat (<=6h); strategy launch changes apply
    at the app's next restart (Option A)."""
    lic = get_license(key)
    if lic is None:
        return None

    new_tier = lic["tier"]
    if tier is not None:
        tier = tier.upper()
        if tier not in TIER_DEFAULTS:
            raise ValueError(f"Unknown tier: {tier}")
        new_tier = tier

    ent = dict(lic["entitlements"])
    if entitlements_patch:
        ent.update(entitlements_patch)

    new_expires = lic["expires_at"]
    if expires_at is not None:
        # validate format strictly
        datetime.strptime(expires_at, "%Y-%m-%d")
        new_expires = expires_at

    new_notes = lic["notes"] if notes is None else notes

    with _conn() as c:
        c.execute(
            """
            UPDATE licenses
               SET tier = ?, entitlements_json = ?, expires_at = ?, notes = ?
             WHERE key = ?
            """,
            (new_tier, json.dumps(ent), new_expires, new_notes, key),
        )
    return get_license(key)

# --------------------------------------------------
# ── CFG_OVERRIDE BEGIN ── role-level settings
# --------------------------------------------------
GLOBAL_OVERRIDES_KEY = "global_config_overrides"


def get_setting(key: str, default=None):
    with _conn() as c:
        row = c.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default


def set_setting(key: str, value) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO settings (key, value_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE
               SET value_json = excluded.value_json,
                   updated_at = excluded.updated_at""",
            (key, json.dumps(value), _now_iso()),
        )


def get_global_overrides() -> dict:
    """The role-level config-override set for ALL non-admin licenses."""
    v = get_setting(GLOBAL_OVERRIDES_KEY, {})
    return v if isinstance(v, dict) else {}


def set_global_overrides(co: dict) -> None:
    set_setting(GLOBAL_OVERRIDES_KEY, co)
# ── CFG_OVERRIDE END ──
