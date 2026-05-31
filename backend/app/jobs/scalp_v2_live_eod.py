# backend/app/jobs/scalp_v2_live_eod.py
#
# SCALP_V2 — Live EOD Square-Off Job
# ============================================================================
# Runs at 15:25 IST (registered in api_server scheduler). Squares off any
# open SCALP_V2 group by delegating to the group manager's own
# force_square_off_all(), which force-exits every open leg (check-then-cancel
# GTT → buy back) and finalizes the group.
#
# Works for both live and paper:
#   - live legs  → cancel GTT + place_buy_exit
#   - paper legs → close at current LTP
# (force_square_off_all handles the per-leg branch internally.)
#
# Isolated: reads only the SCALP_V2 group manager singleton via the selection
# loop accessor. No other strategy touched. If V2 isn't running or has no open
# group, it's a safe no-op.
# ============================================================================

from app.event_bus.audit_logger import write_audit_log


def scalp_v2_live_eod_job():
    try:
        from app.engine.scalp_v2.scalp_v2_selection_loop import get_group_manager

        gm = get_group_manager()
        if gm is None:
            write_audit_log("[V2_EOD] Group manager not initialized — nothing to square off")
            return

        group = gm.current_group()
        if group is None:
            write_audit_log("[V2_EOD] No open group — nothing to square off")
            return

        write_audit_log(
            f"[V2_EOD] Square-off triggered for group={group.group_id} "
            f"status={group.status}"
        )
        gm.force_square_off_all(reason="EOD_SQUAREOFF")
        write_audit_log("[V2_EOD] Square-off complete")

    except Exception as e:
        write_audit_log(f"[V2_EOD][ERROR] {repr(e)}")