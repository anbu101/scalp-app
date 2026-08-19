# backend/app/jobs/tma2_live_eod.py
#
# ── TMA_V2 — EOD / MTM-cut / expiry square-off safety-net job ──
# ============================================================================
# api_server cron (15:25). PRIMARY exit path is candle-driven inside
# TMA2TradeManager.on_minute (EOD / expiry square-off / NEG_MTM_EOD_CUT all
# fire at exit_time on the option candle stream — the parity path); the
# coordinator's boundary check is layer two. THIS JOB is layer three:
#
#   * Manager reachable → manager.force_eod(now): trade_mode-aware —
#     INTRADAY / expiry-day → hard close; positional MTM-cut if armed and
#     marking negative; positional non-expiry carry → deliberate NO-OP
#     (a positional overnight hold is by design, never auto-flattened).
#   * Manager unreachable (loop never started / crashed) with rows OPEN:
#       - INTRADAY: paper rows → STALE (honest, no invented prices);
#         LIVE rows → CRITICAL alert for manual square-off.
#       - POSITIONAL: OPEN rows are a legitimate carry — logged, left
#         alone — EXCEPT rows whose contract expires TODAY, which MUST
#         have closed: those raise the CRITICAL alert.
# ============================================================================

import time
from datetime import datetime

from app.event_bus.audit_logger import write_audit_log


def tma2_live_eod_job():
    # ── TRADING_DAY_GATE_20260816 ── NSE-holiday guard (the cron
    # trigger is already mon-fri; this covers weekday exchange holidays).
    from app.utils.market_hours import is_trading_day
    if not is_trading_day():
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log("[EOD][TMA2] non-trading day — no-op")
        return
    try:
        from app.engine.tma2.tma2_selection_loop import get_manager
        m = get_manager()
    except Exception:
        m = None

    if m is not None and not getattr(m, "disabled", False):
        try:
            if getattr(m, "group", None) or getattr(m, "pending", None):
                m.pending = None
                m.force_eod(int(time.time()))
                write_audit_log("[TMA2_EOD] square-off decision run via manager")
            else:
                write_audit_log("[TMA2_EOD] clean — no open TMA2 group")
            return
        except Exception as e:
            write_audit_log(f"[TMA2_EOD] manager force_eod failed: {e} — "
                            f"falling through to DB hygiene")

    # ── loop unreachable: DB hygiene, trade_mode-aware ──
    try:
        from app.config.strategy_loader import load_strategy_config
        from app.engine.tma2.tma2_common import TMA2Repo
        cfg = load_strategy_config("TMA_V2") or {}
        positional = str(cfg.get("trade_mode", "INTRADAY")).upper() == "POSITIONAL"
        repo = TMA2Repo()
        rows = repo.open_legs()
        if not rows:
            write_audit_log("[TMA2_EOD] clean — no open TMA2 legs")
            return
        today_iso = datetime.now().date().isoformat()
        critical = []
        for r in rows:
            mode = str(r.get("mode", "PAPER")).upper()
            expires_today = (r.get("expiry") == today_iso)
            if positional and not expires_today:
                write_audit_log(f"[TMA2_EOD] positional carry left OPEN: "
                                f"#{r['id']} {r['tradingsymbol']} (loop down "
                                f"— will be adopted at next boot)")
                continue
            if mode == "PAPER":
                repo.mark_stale(r["id"])
                write_audit_log(f"[TMA2_EOD] paper leg #{r['id']} "
                                f"({r['tradingsymbol']}) marked STALE — loop was down")
            else:
                critical.append(r)
        if critical:
            msg = "; ".join(f"#{r['id']} {r['tradingsymbol']} "
                            f"{r['direction']} qty {r['qty']}" for r in critical)
            write_audit_log(f"[TMA2_EOD][CRITICAL] LIVE legs still OPEN and the "
                            f"loop is unreachable — SQUARE OFF MANUALLY: {msg}")
            try:
                from app.api.telegram_api import notify_system_alert
                notify_system_alert({"message": f"🚨 TMA LIVE legs OPEN at EOD, "
                                                f"loop dead — square off manually "
                                                f"NOW: {msg}",
                                     "severity": "error"})
            except Exception:
                pass
    except Exception as e:
        write_audit_log(f"[TMA2_EOD][ERROR] {repr(e)}")