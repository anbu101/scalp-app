# backend/app/license/config_override_applier.py
"""
CFG_OVERRIDE — remote config control for non-admin machines.

The admin puts `entitlements.config_overrides = {strategy_id: partial_cfg}`
on a license (via the license-server admin UI). The signed token carries it
to the client at every heartbeat; this module writes those overrides into
~/.scalp-app/strategies/{ID}.json.

Locked decisions (2026-08-03):
  D1  Apply-to-DISK at heartbeat. Engines and strategy_loader's READ path
      are never touched — they keep reading the JSON files exactly as
      before (BB_V1 isolation). Overrides are "sticky": removing one on
      the server stops ENFORCING it; the last-written value stays in the
      file until overridden again.
  D2  Ownership split: the friend owns lots (the config_routes _LOTS_PATHS
      whitelist) — their values are SNAPSHOTTED before the merge and
      REINSTATED after it, which also survives wholesale list replacement
      (deep_update replaces lists, so an admin-supplied `legs` list would
      otherwise drop the friend's lots). trade_execution_mode IS
      overridable (remote force-to-PAPER lever); LIVE still requires the
      live_trading entitlement.
  D3  Admin-machine immunity: no-op unless the license is usable AND
      ui_level != "admin".
  D4a Apply IMMEDIATELY whenever a changed override set arrives — no
      market-hours deferral. Mid-session parameter changes are therefore
      possible; the admin owns that judgement when saving overrides.
  D5  SHA-256 change detection persisted in
      ~/.scalp-app/strategies/.overrides_state.json — an unchanged
      override set is never re-applied, so hand-tuned (Path-B) values in
      non-overridden keys are never re-stomped by the 6h heartbeat.

Merge semantics = strategy_loader.deep_update: nested dicts merge
recursively, EVERYTHING ELSE (including lists) replaces wholesale. To
change one IC leg, supply the whole legs list.

Every apply is audit-logged per strategy. Any failure here must never
break licensing — the caller wraps this in try/except, and this module
additionally fails soft per strategy.
"""

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from app.license import license_state
from app.event_bus.audit_logger import write_audit_log

STATE_FILE = Path.home() / ".scalp-app" / "strategies" / ".overrides_state.json"

_MODE_KEY = "trade_execution_mode"
_MODE_VALUES = {"OFF", "PAPER", "LIVE"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _canon_hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _path_get(obj, dotted):
    cur = obj
    for seg in dotted.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        else:
            return None
    return cur


def _path_set(obj, dotted, value):
    """Best-effort set; list indices must already exist (we never invent
    legs). Silently gives up on structural mismatch."""
    segs = dotted.split(".")
    cur = obj
    for seg in segs[:-1]:
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return
        elif isinstance(cur, dict):
            if seg not in cur or not isinstance(cur[seg], (dict, list)):
                cur[seg] = {}
            cur = cur[seg]
        else:
            return
    last = segs[-1]
    if isinstance(cur, list):
        try:
            cur[int(last)] = value
        except (ValueError, IndexError):
            return
    elif isinstance(cur, dict):
        cur[last] = value


def _read_state() -> dict:
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(state: dict):
    """Atomic write — same mkstemp+fsync+replace pattern as the loader."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent),
                               prefix=".overrides_state_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _owned_lots_paths(strategy_id: str):
    """Friend-owned paths — SINGLE SOURCE: app.config.lots_whitelist,
    a dependency-free module shared with config_routes. (An earlier
    draft imported config_routes here, which dragged kiteconnect into
    the license path — caught by the offline applier test.)"""
    from app.config.lots_whitelist import LOTS_PATHS
    return LOTS_PATHS.get(strategy_id, [])


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------

def apply_config_overrides():
    """Called from license_client._evaluate_local (boot + every heartbeat +
    every server response). Cheap when nothing changed (D5 hash guard)."""

    # D3 — never on admin machines, never on a non-usable license.
    if not license_state.is_usable():
        return
    if license_state.ui_level() == "admin":
        return

    overrides = license_state.ENTITLEMENTS.get("config_overrides")
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        write_audit_log("[CFG_OVERRIDE] ignored: config_overrides is not a dict")
        return

    new_hash = _canon_hash(overrides)
    state = _read_state()
    if state.get("hash") == new_hash:
        return  # D5 — unchanged set, nothing to do

    # Import here (not at module top) to keep this module importable even
    # if the loader is mid-refactor; failures are caught by the caller.
    from app.config.strategy_loader import (
        load_strategy_config,
        save_strategy_config,
        deep_update,
    )

    applied = []
    for strategy_id, patch in overrides.items():
        try:
            if not isinstance(patch, dict) or not patch:
                continue
            # Entitled strategies only — an override for a strategy this
            # license can't run is inert.
            if not license_state.license_allows_strategy(strategy_id):
                write_audit_log(
                    f"[CFG_OVERRIDE] {strategy_id}: skipped (not entitled)")
                continue

            patch = deepcopy(patch)

            # D2 (mode half) — LIVE via override requires live_trading.
            m = patch.get(_MODE_KEY)
            if m is not None:
                if m not in _MODE_VALUES:
                    patch.pop(_MODE_KEY, None)
                elif m == "LIVE" and not license_state.ENTITLEMENTS.get(
                        "live_trading", False):
                    patch[_MODE_KEY] = "PAPER"

            # load (postmortem-safe: seeds default on genuine first run,
            # never clobbers on transient I/O)
            cfg = load_strategy_config(strategy_id)
            if not isinstance(cfg, dict):
                cfg = {}

            # D2 (lots half) — snapshot friend-owned values BEFORE merge…
            owned = {
                p: _path_get(cfg, p)
                for p in _owned_lots_paths(strategy_id)
            }

            before = json.dumps(cfg, sort_keys=True)
            deep_update(cfg, patch)

            # …and reinstate AFTER. Survives wholesale list replacement:
            # if the admin replaced `legs`, the friend's lots are written
            # back into the new list's matching positions.
            for p, v in owned.items():
                if v is not None:
                    _path_set(cfg, p, v)

            if json.dumps(cfg, sort_keys=True) == before:
                write_audit_log(
                    f"[CFG_OVERRIDE] {strategy_id}: no effective change")
                continue

            save_strategy_config(strategy_id, cfg)  # atomic
            applied.append(strategy_id)
            write_audit_log(
                f"[CFG_OVERRIDE] {strategy_id}: applied override keys "
                f"{sorted(patch.keys())} (friend-owned lots preserved)")
        except Exception as e:
            # Fail soft per strategy — one bad patch must not block others.
            write_audit_log(
                f"[CFG_OVERRIDE][ERROR] {strategy_id}: {e!r}")

    _write_state({
        "hash": new_hash,
        "applied_strategies": applied,
        "applied_at": __import__("time").time(),
    })
    if applied:
        write_audit_log(
            f"[CFG_OVERRIDE] apply complete: {applied} "
            f"(hash {new_hash[:12]}…)")
