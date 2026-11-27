#!/usr/bin/env python3
# license_server/gen_strategy_defaults.py
"""
CFG_OVERRIDE — regenerate strategy_defaults.json for the admin UI.

Extracts DEFAULT_STRATEGY_CONFIGS from the app backend (single source),
strips the friend-owned lots paths (app.config.lots_whitelist) and
trade_execution_mode (opt-in lever, not template noise), and writes
license_server/strategy_defaults.json.

Run from the REPO ROOT whenever DEFAULT_STRATEGY_CONFIGS changes, then
redeploy the JSON alongside server.py:

    PYTHONPATH=backend python3 license_server/gen_strategy_defaults.py
"""
import json
from copy import deepcopy
from pathlib import Path

from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS
from app.config.lots_whitelist import LOTS_PATHS

OUT = Path(__file__).parent / "strategy_defaults.json"

# ── LICENSE_SYNC_ADMIN_ONLY_20261127 ── (TD-114) strategies that run only on Anbu's ADMIN licence
# ("*" covers them). They are NOT licensable: not in server.py KNOWN_STRATEGY_IDS,
# not an admin_ui.html chip, no template here. check_strategy_sync.py reads this.
# To offer one to friends: add it to those two lists and remove it here.
ADMIN_ONLY = {"LVB_V1"}   # Tripwire — Anbu 27 Nov: "only for me now"


def _path_delete(obj, dotted):
    segs = dotted.split(".")
    cur = obj
    for seg in segs[:-1]:
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return
        if cur is None:
            return
    last = segs[-1]
    if isinstance(cur, list):
        try:
            cur[int(last)] = None  # keep list shape; admin sees a placeholder
        except (ValueError, IndexError):
            pass
    elif isinstance(cur, dict):
        cur.pop(last, None)


def main():
    out = {}
    for sid, cfg in DEFAULT_STRATEGY_CONFIGS.items():
        if sid in ADMIN_ONLY:                      # ── LICENSE_SYNC_ADMIN_ONLY_20261127 ──
            continue
        c = deepcopy(cfg)
        c.pop("trade_execution_mode", None)
        for p in LOTS_PATHS.get(sid, []):
            _path_delete(c, p)
        out[sid] = c
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(f"wrote {OUT} ({len(out)} strategies)")


if __name__ == "__main__":
    main()
