# backend/app/config/ic_split_migration.py
#
# ── IC_SPLIT (2026-08-04) ── one-time FILE migration for the IC_V1 → IC_V2
# identity rename (DS4/DS5 locked). The DB row retag is SQL migration 023;
# this module handles everything that lives OUTSIDE the DB:
#
#   1. ~/.scalp-app/strategies/IC_V1.json  →  copied VERBATIM to IC_V2.json
#      (the user's tuned lots/SLs/adjust AND current trade_execution_mode
#      belong to the strategy that keeps the old behavior). IC_V1.json is
#      then REWRITTEN to the new IC_V1 default (legacy EOD condor, mode OFF)
#      so the reborn IC_V1 can never inherit V2 carry/adjust settings.
#   2. ~/.scalp-app/state/IC_V1_day_latch.json / IC_V1_carry.json /
#      IC_V1_session.json  →  renamed to IC_V2_*. A carry present at
#      migration time belonged to the V2-semantics strategy; renaming it
#      lets the IC_V2 boot restore (DA1) find it. (Deployment checklist
#      still says: rebuild only on a flat, no-carry night.)
#
# IDEMPOTENT + FAIL-CLOSED-ON-REPEAT: a marker file is written LAST; if the
# marker exists nothing runs. Individual steps are additionally guarded
# (never overwrite an existing IC_V2 artifact) so a half-completed run that
# crashed before the marker can safely re-run.
#
# Runs SYNCHRONOUSLY in api_server startup, after run_migrations and BEFORE
# any strategy runtime launches (an engine booting on unmigrated files would
# resurrect the pre-split identity). Atomic writes per house rule
# (tempfile.mkstemp + fsync + os.replace).

import json
import os
import tempfile
from pathlib import Path

from app.event_bus.audit_logger import write_audit_log

STRATEGY_DIR = Path.home() / ".scalp-app" / "strategies"
STATE_DIR    = Path.home() / ".scalp-app" / "state"
MARKER_PATH  = STATE_DIR / ".ic_split_migrated"

_STATE_FILES = ("day_latch.json", "carry.json", "session.json")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ic_split_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def run_ic_split_migration() -> None:
    """Never raises out — a migration failure must not take the server
    down; it logs loudly and leaves the marker ABSENT so the next boot
    retries."""
    try:
        if MARKER_PATH.exists():
            return

        write_audit_log("[IC_SPLIT][MIGRATE] starting one-time IC_V1→IC_V2 "
                        "file migration")

        # ── 1) strategy config ────────────────────────────────────────
        v1_cfg_path = STRATEGY_DIR / "IC_V1.json"
        v2_cfg_path = STRATEGY_DIR / "IC_V2.json"
        if v1_cfg_path.exists() and not v2_cfg_path.exists():
            try:
                cfg = json.loads(v1_cfg_path.read_text())
                _atomic_write_json(v2_cfg_path, cfg)
                write_audit_log("[IC_SPLIT][MIGRATE] IC_V1.json copied → "
                                "IC_V2.json (tuned config + mode preserved)")
            except Exception as e:
                # Unreadable tuned config: do NOT guess. IC_V2 falls back to
                # loader defaults (mode OFF) — fail closed, loud.
                write_audit_log(f"[IC_SPLIT][MIGRATE][CFG_COPY_FAIL] {e!r} — "
                                f"IC_V2 will boot on defaults (mode OFF)")

        if v1_cfg_path.exists():
            try:
                from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS
                default = DEFAULT_STRATEGY_CONFIGS.get("IC_V1")
                if default:
                    _atomic_write_json(v1_cfg_path, default)
                    write_audit_log("[IC_SPLIT][MIGRATE] IC_V1.json reset to "
                                    "the new legacy-EOD default (mode OFF)")
            except Exception as e:
                write_audit_log(f"[IC_SPLIT][MIGRATE][CFG_RESET_FAIL] {e!r}")

        # ── 2) state files (latch / carry / session) ──────────────────
        for suffix in _STATE_FILES:
            src = STATE_DIR / f"IC_V1_{suffix}"
            dst = STATE_DIR / f"IC_V2_{suffix}"
            try:
                if src.exists() and not dst.exists():
                    os.replace(src, dst)
                    write_audit_log(f"[IC_SPLIT][MIGRATE] state renamed "
                                    f"{src.name} → {dst.name}")
                elif src.exists() and dst.exists():
                    # both present = re-run after partial completion; the
                    # V2 file is authoritative, retire the stale V1 one.
                    src.rename(src.with_name(src.name + ".pre_split_bak"))
                    write_audit_log(f"[IC_SPLIT][MIGRATE] stale {src.name} "
                                    f"parked as .pre_split_bak")
            except Exception as e:
                write_audit_log(f"[IC_SPLIT][MIGRATE][STATE_FAIL] "
                                f"{src.name}: {e!r}")

        # ── 3) marker LAST ────────────────────────────────────────────
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        MARKER_PATH.write_text(json.dumps(
            {"migrated_at": __import__("datetime").datetime.now()
             .isoformat(timespec="seconds")}))
        write_audit_log("[IC_SPLIT][MIGRATE] complete — marker written")

    except Exception as e:
        write_audit_log(f"[IC_SPLIT][MIGRATE][FATAL] {e!r} — will retry "
                        f"next boot (marker not written)")
