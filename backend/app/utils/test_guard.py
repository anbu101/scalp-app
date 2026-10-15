# backend/app/utils/test_guard.py
#
# ── TEST_ALERT_GUARD_20261015 ── a test process must never reach Telegram.
#
# 2026-10-15: four "CRITICAL ALERT · Timberwolf: SELL entry FAILED after the
# hedge filled. Hedge NIFTYX_H x650 unwound" messages reached the live
# Telegram group. NIFTYX_H is test_tma2_entry_paths.py's fake symbol: the
# suite calls the real _unwind_hedge(), which calls notify_critical(), and the
# Telegram config is read from ~/.scalp-app/telegram_config.json regardless of
# SCALP_APP_HOME — so every apply-script test gate on the Mac paged the group.
#
# outbound_blocked() is checked at the three places a request leaves for
# api.telegram.org. It is True only in a test process:
#   * SCALP_TEST_MODE=1 in the environment, or
#   * the running script is a test file (test_*.py), or pytest is loaded.
# The app (scalp-backend binary, uvicorn, api_server) matches none of them.
# Never raises; on any doubt it returns False (production keeps alerting).

from __future__ import annotations

import os
import sys


def outbound_blocked() -> bool:
    try:
        if os.environ.get("SCALP_TEST_MODE") == "1":
            return True
        a0 = os.path.basename(str(sys.argv[0] if sys.argv else ""))
        if a0.startswith("test_") or "pytest" in a0:
            return True
        if "pytest" in sys.modules or "_pytest" in sys.modules:
            return True
    except Exception:
        return False
    return False
