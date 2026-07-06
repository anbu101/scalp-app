import json
import os
import tempfile
from pathlib import Path
from copy import deepcopy

from app.event_bus.audit_logger import write_audit_log

# ---------------------------------------------
# GLOBAL CONFIG PATH
# ---------------------------------------------

GLOBAL_CONFIG_PATH = Path.home() / ".scalp-app" / "global_config.json"

DEFAULT_GLOBAL_CONFIG = {
    "trade_on": False
}

# ---------------------------------------------
# LOAD
# ---------------------------------------------

def load_global_config() -> dict:
    # ── GLOBAL_READ_SAFE BEGIN ────────────────────────────────────────
    # Hardened after the 2026-07-06 incident (fd exhaustion, OSError 24):
    #
    #   OLD BEHAVIOUR: any read failure ran save_global_config(DEFAULT) —
    #   persisting trade_on=False to disk. A transient I/O fault therefore
    #   PERMANENTLY disabled trading (observed twice on 2026-07-06; the
    #   15:08 mtime on global_config.json was the second clobber). This is
    #   the same bug class as the 2026-06-15 strategy_loader paper→live
    #   flip, in the opposite (luckily safe) direction.
    #
    #   NEW BEHAVIOUR (mirrors strategy_loader's postmortem fix):
    #     - No Path.exists() pre-check. Under fd exhaustion, exists()
    #       swallows the OSError and returns False, mis-routing an EXISTING
    #       file into the seed branch. Instead we attempt the open and
    #       route on the exception type.
    #     - FileNotFoundError  → positively-confirmed absent (genuine first
    #       run) → seed the default to disk. Best-effort: if even the seed
    #       write fails, fall back to the in-memory default.
    #     - ANY OTHER failure  → degraded read → return the default
    #       IN MEMORY for this single call, file left UNTOUCHED, loud log.
    #       trade_on=False in memory means entries are paused for that one
    #       gate check and recover on the next clean read — fail-closed in
    #       the cheap direction, with zero persistence.
    # ──────────────────────────────────────────────────────────────────
    try:
        with GLOBAL_CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        # Genuine first run — seed the default. Safe: nothing to clobber.
        try:
            save_global_config(DEFAULT_GLOBAL_CONFIG)
        except Exception as e:
            write_audit_log(
                f"[CONFIG][GLOBAL_SEED_FAILED] global_config.json absent and the "
                f"seed write failed ({e!r}) — using in-memory default this call."
            )
        return deepcopy(DEFAULT_GLOBAL_CONFIG)
    except Exception as e:
        # File exists (or its existence could not be confirmed) but could not
        # be read/parsed this instant. DO NOT WRITE — the on-disk trade_on
        # must survive a transient fault. In-memory default (trade_on=False)
        # pauses entries for this one call; the next clean read recovers the
        # user's real setting.
        write_audit_log(
            f"[CONFIG][GLOBAL_READ_DEGRADED] global_config.json could not be "
            f"read ({e!r}) — using IN-MEMORY default (trade_on=False) for THIS "
            f"call only, file left UNTOUCHED. Entries pause this cycle and "
            f"recover on the next clean read. If this repeats, the machine has "
            f"an I/O / fd / disk problem that must be fixed."
        )
        return deepcopy(DEFAULT_GLOBAL_CONFIG)
    # ── GLOBAL_READ_SAFE END ──────────────────────────────────────────

    merged = deepcopy(DEFAULT_GLOBAL_CONFIG)
    merged.update(cfg)
    return merged

# ---------------------------------------------
# SAVE (ATOMIC SAFE)
# ---------------------------------------------

def save_global_config(cfg: dict):
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(GLOBAL_CONFIG_PATH.parent),
        prefix="global_config_",
        suffix=".json"
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, GLOBAL_CONFIG_PATH)

    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass