import json
import os
import tempfile
from pathlib import Path
from copy import deepcopy

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
    if not GLOBAL_CONFIG_PATH.exists():
        save_global_config(DEFAULT_GLOBAL_CONFIG)
        return deepcopy(DEFAULT_GLOBAL_CONFIG)

    try:
        with GLOBAL_CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        save_global_config(DEFAULT_GLOBAL_CONFIG)
        return deepcopy(DEFAULT_GLOBAL_CONFIG)

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
