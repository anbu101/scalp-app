from app.db.sqlite import get_conn
from app.event_bus.audit_logger import write_audit_log
from app.db.paper_trades_repo import close_paper_trade

EXIT_REASON_EOD = "EOD_SQUARE_OFF"

# ── IC_V2 OVERNIGHT_EXEMPT BEGIN (2026-07-29) ──────────────────────────────
# Strategies that OWN an overnight position lifecycle are exempt from this
# generic 15:25 sweep. IC_V1 (exit_mode NEXT_OPEN, ONE_NIGHT_MAX) carries
# open legs past the close BY DESIGN and squares them off at 09:16 next
# session via its own engine + morning job; this sweep was force-closing
# those carried paper legs as EOD_SQUARE_OFF every evening (reported
# 2026-07-29). Exempting IC is safe in BOTH IC modes: in legacy EOD mode
# IC's own engine backstop + dedicated EOD job close its rows at 15:28, so
# this sweep was only ever a redundant net for IC. Residual accepted: a
# paper row orphaned by a crash BEFORE carry-commit stays OPEN in the DB
# until manually closed — cosmetic, paper-only, and preferable to
# force-closing legitimate overnight carries.
OVERNIGHT_EXEMPT_STRATEGIES = ("IC_V1",)
# ── IC_V2 OVERNIGHT_EXEMPT END ─────────────────────────────────────────────



def square_off_open_paper_trades():
    """
    Force-close all OPEN paper trades at EOD.
    Safe to run multiple times.

    FIX 1: Use LTPStore.get(symbol) instead of get_ltp_for_token(token).
            bb_tick_engine.on_tick() populates LTPStore keyed by symbol,
            not OptionTickState. The old provider always returned None,
            falling back to entry_price → gross P&L always 0.

    FIX 2: Call close_paper_trade() instead of raw UPDATE.
            close_paper_trade() calculates Zerodha charges (brokerage,
            STT, GST, exchange fees) and writes pnl_value / net_pnl.
            Raw UPDATE left all charge columns as NULL → "—" in UI.
    """

    conn = get_conn()

    # ── IC_V2 OVERNIGHT_EXEMPT ── overnight-lifecycle strategies keep their
    # open rows (they close them at next-open themselves); NULL strategy_name
    # legacy rows are still swept.
    placeholders = ",".join("?" for _ in OVERNIGHT_EXEMPT_STRATEGIES)
    rows = conn.execute(
        f"""
        SELECT
            paper_trade_id,
            symbol,
            token,
            entry_price,
            qty
        FROM paper_trades
        WHERE state = 'OPEN'
          AND (strategy_name IS NULL
               OR strategy_name NOT IN ({placeholders}))
        """,
        OVERNIGHT_EXEMPT_STRATEGIES,
    ).fetchall()

    try:
        exempt_open = conn.execute(
            f"""
            SELECT strategy_name, COUNT(*) AS n
            FROM paper_trades
            WHERE state = 'OPEN' AND strategy_name IN ({placeholders})
            GROUP BY strategy_name
            """,
            OVERNIGHT_EXEMPT_STRATEGIES,
        ).fetchall()
        for r in exempt_open:
            write_audit_log(
                f"[EOD][PAPER][EXEMPT] leaving {r['n']} open "
                f"{r['strategy_name']} row(s) — overnight carry, the "
                f"strategy closes them at next-open itself"
            )
    except Exception as e:
        write_audit_log(f"[EOD][PAPER][EXEMPT][WARN] count failed: {e}")

    if not rows:
        write_audit_log("[EOD][PAPER] No open trades to square off")
        return

    write_audit_log(
        f"[EOD][PAPER] Squaring off {len(rows)} open trades"
    )

    # Import here to avoid circular import at module load time
    try:
        from app.marketdata.ltp_store import LTPStore
    except Exception as e:
        write_audit_log(f"[EOD][PAPER][ERROR] Cannot import LTPStore: {e}")
        LTPStore = None

    closed_count = 0
    skipped_count = 0

    for r in rows:
        trade_id   = r["paper_trade_id"]
        symbol     = r["symbol"]
        token      = r["token"]
        entry_price = r["entry_price"]

        # --------------------------------------------------
        # FIX 1: LTP resolution
        # Primary  : LTPStore (populated by bb_tick_engine WS ticks)
        # Secondary: entry_price fallback with a clear warning
        # --------------------------------------------------
        ltp = None

        if LTPStore is not None:
            try:
                ltp = LTPStore.get(symbol)
            except Exception as e:
                write_audit_log(
                    f"[EOD][PAPER][WARN] LTPStore.get failed "
                    f"symbol={symbol} err={e}"
                )

        if ltp is None:
            # Last resort: use entry_price so the trade closes cleanly.
            # This means P&L = 0 but is better than leaving it open.
            # Happens when WS disconnected before squareoff ran.
            ltp = entry_price
            write_audit_log(
                f"[EOD][PAPER][WARN] LTP unavailable for {symbol} "
                f"(token={token}). Using entry_price={entry_price} as fallback. "
                f"P&L will be 0 for this trade."
            )
        else:
            write_audit_log(
                f"[EOD][PAPER] {symbol} LTP={ltp} (from LTPStore)"
            )

        # --------------------------------------------------
        # FIX 2: Use close_paper_trade() so charges are computed
        # --------------------------------------------------
        try:
            close_paper_trade(
                paper_trade_id=trade_id,
                exit_price=float(ltp),
                exit_reason=EXIT_REASON_EOD,
            )
            closed_count += 1
            write_audit_log(
                f"[EOD][PAPER] Trade {trade_id} CLOSED @ {ltp}"
            )
        except Exception as e:
            skipped_count += 1
            write_audit_log(
                f"[EOD][PAPER][ERROR] Failed to close trade_id={trade_id} "
                f"symbol={symbol} err={e}"
            )

    write_audit_log(
        f"[EOD][PAPER] Square-off completed | "
        f"closed={closed_count}, skipped={skipped_count}"
    )