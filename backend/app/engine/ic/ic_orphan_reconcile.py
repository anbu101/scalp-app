# backend/app/engine/ic/ic_orphan_reconcile.py
#
# IC (shared V1/V2) — ORPHAN ROW RECONCILER (2026-08-03)
# ============================================================================
# WHY: open IC rows in the DB that the engine does NOT own can exist after
# a crash/restart on a build predating the session snapshot, after a wiped
# state dir, or after a version-mismatched/refused snapshot. Nothing closes
# them: the morning machine only closes the engine's group, and the generic
# 15:25 paper sweep deliberately exempts IC_V2 (overnight carry). The
# 2026-07-31 incident left 4 such paper rows open across the weekend.
#
# WHAT (called once at engine boot, AFTER carry/session restore so the
# owned-set is accurate):
#   * PAPER rows: any OPEN paper_trades row for this sid whose paper_trade_id
#     is not owned by the restored group is closed NEUTRALLY — exit at the
#     stored entry price (LTPStore is empty at boot; a fabricated "market"
#     price would be a guess dressed as data), exit_reason "MANUAL" (an
#     existing vocabulary word — no new strings near table constraints).
#     The ORPHAN identity lives in the audit line + alert, per house rule.
#   * LIVE rows: NEVER auto-closed. The engine cannot know whether broker
#     positions/GTTs still exist behind an orphaned live row — closing the
#     ROW while a POSITION rides (or vice versa) manufactures a lie either
#     way. CRITICAL alert + Telegram with the exact rows; the human
#     reconciles against Kite. (Revisit before LIVE promotion.)
#
# Isolated try/excepts throughout — reconciliation must never block boot.
# ============================================================================

from typing import Optional

from app.event_bus.audit_logger import write_audit_log
from app.event_bus.inapp_events import record_alert

def _owned_db_ids(gm) -> set:
    ids = set()
    try:
        core = gm.current_group() if gm is not None else None
        if core is None:
            return ids
        for leg in core.legs.values():
            rt = gm.leg_runtime(leg.leg_id)
            if rt.get("db_id"):
                ids.add(str(rt["db_id"]))
    except Exception as e:
        write_audit_log(f"[IC][ORPHAN][OWNED_SET_ERR] {e!r}")
    return ids


# ── IC2_SHADOW_CARRY_20261106 BEGIN ──
# In PAPER_LIVE every LIVE leg has a paper twin ('SHD:…', trading/
# shadow_book.py). The owned-set holds the restored group's LIVE trade ids
# only, so after an overnight carry the twins looked "unowned" and were
# neutral-closed at entry at boot (2026-11-06: 4 IC_V2 twins, gross 0, the
# 09:16 NEXT_OPEN mirror_close then SKIPPED them). A twin's lifecycle is its
# LIVE row's (mirror_close closes it at the live exit price), so it is never
# an orphan while that live row is owned or still open. Any doubt → KEEP:
# a twin left open is closed by its live row; one closed early loses its
# P&L for good.
_SHADOW_PREFIX = "SHD:"


def _twin_must_stay(conn, sid: str, pid: str, symbol, owned: set) -> bool:
    if not str(pid).startswith(_SHADOW_PREFIX):
        return False
    try:
        live_ref = None
        try:
            row = conn.execute(
                "SELECT live_ref FROM shadow_links WHERE shadow_id = ?",
                (pid,),
            ).fetchone()
            live_ref = str(row[0]) if row and row[0] else None
        except Exception as e:
            if "no such table" not in str(e).lower():
                raise
        if live_ref and live_ref in owned:
            write_audit_log(
                f"[IC][ORPHAN][TWIN_KEPT] {symbol} id={pid} — paper twin of "
                f"owned live row {live_ref[:8]}; closes with it"
            )
            return True
        if live_ref:
            lr = conn.execute(
                "SELECT exit_time FROM trades WHERE trade_id = ?",
                (live_ref,),
            ).fetchone()
            if lr is not None and lr[0] is None:
                write_audit_log(
                    f"[IC][ORPHAN][TWIN_KEPT] {symbol} id={pid} — twin of OPEN "
                    f"live row {live_ref[:8]} (not in group; live branch "
                    f"flags it) — left to follow its live row"
                )
                return True
            return False          # live row closed/missing → genuine orphan
        lr = conn.execute(
            "SELECT 1 FROM trades WHERE strategy_id = ? AND symbol = ? "
            "AND exit_time IS NULL LIMIT 1",
            (sid, symbol),
        ).fetchone()
        if lr is not None:
            write_audit_log(
                f"[IC][ORPHAN][TWIN_KEPT] {symbol} id={pid} — unlinked twin but "
                f"an OPEN live {sid} row holds the same contract"
            )
            return True
        return False
    except Exception as e:
        write_audit_log(
            f"[IC][ORPHAN][TWIN_CHECK_ERR] id={pid} {e!r} — KEPT (fail-safe)"
        )
        return True
# ── IC2_SHADOW_CARRY_20261106 END ──


def reconcile_orphan_rows(gm) -> dict:
    """Returns {"paper_closed": n, "live_flagged": m} for tests/telemetry."""
    sid = gm.strategy_id   # ── IC_SPLIT ── per-instance scope
    owned = _owned_db_ids(gm)
    paper_closed = 0
    live_flagged = 0

    # ── PAPER: neutral-close unowned open rows ─────────────────────────
    try:
        from app.db.sqlite import get_conn
        from app.db.paper_trades_repo import close_paper_trade
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT paper_trade_id, symbol, entry_price
            FROM paper_trades
            WHERE state = 'OPEN' AND strategy_name = ?
            """,
            (sid,),
        ).fetchall()
        for r in rows:
            pid = str(r["paper_trade_id"])
            if pid in owned:
                continue
            # ── IC2_SHADOW_CARRY_20261106 ── twins follow their live row
            if _twin_must_stay(conn, sid, pid, r["symbol"], owned):
                continue
            try:
                close_paper_trade(
                    paper_trade_id=pid,
                    exit_price=float(r["entry_price"] or 0.0),
                    exit_reason="MANUAL",
                )
                paper_closed += 1
                write_audit_log(
                    f"[IC][ORPHAN][PAPER_CLOSED] {r['symbol']} id={pid} "
                    f"— unowned open row, neutral close at entry "
                    f"(pre-snapshot orphan)"
                )
            except Exception as e:
                write_audit_log(f"[IC][ORPHAN][PAPER_CLOSE_FAIL] id={pid} {e!r}")
    except Exception as e:
        write_audit_log(f"[IC][ORPHAN][PAPER_SCAN_ERR] {e!r}")

    if paper_closed:
        record_alert(
            "IC_ORPHAN_RECONCILE",
            f"{sid}: closed {paper_closed} orphaned open PAPER row(s) at "
            f"entry price (rows with no owning group — pre-snapshot "
            f"restart artifacts).",
            severity="warning", strategy_id=sid, mode="paper",
        )

    # ── LIVE: flag only, never touch ───────────────────────────────────
    try:
        from app.db.trades_repo import get_open_trades_for_strategy
        live_rows = get_open_trades_for_strategy(sid) or []
        stray = []
        for r in live_rows:
            tid = str(r.get("trade_id") or r.get("id") or "")
            if tid and tid not in owned:
                stray.append(f"{r.get('symbol')}({tid[:8]})")
        live_flagged = len(stray)
        if stray:
            write_audit_log(f"[IC][ORPHAN][LIVE_FLAGGED] {stray} — NOT "
                            f"auto-closed; reconcile against Kite manually")
            record_alert(
                "IC_ORPHAN_LIVE",
                f"{sid}: {live_flagged} open LIVE row(s) with NO owning "
                f"group: {', '.join(stray)}. NOT auto-closed — verify "
                f"positions/GTTs in Kite and square off manually.",
                severity="error", strategy_id=sid, mode="live",
            )
            try:
                from app.api.telegram_api import notify_critical
                notify_critical({"message":
                    f"{sid}: {live_flagged} orphaned LIVE row(s) at boot "
                    f"({', '.join(stray)}). Engine does NOT own them — "
                    f"check Kite NOW.", "severity": "error"})
            except Exception:
                pass
    except Exception as e:
        write_audit_log(f"[IC][ORPHAN][LIVE_SCAN_ERR] {e!r}")

    if not paper_closed and not live_flagged:
        write_audit_log("[IC][ORPHAN] no orphaned rows at boot")
    return {"paper_closed": paper_closed, "live_flagged": live_flagged}
