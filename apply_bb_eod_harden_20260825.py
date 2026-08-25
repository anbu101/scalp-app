#!/usr/bin/env python3
# apply_bb_eod_harden_20260825.py
#
# ── BB_EOD_HARDEN_20260825 ── Assert-anchored edit script (run from repo root):
#     python3 apply_bb_eod_harden_20260825.py
#
# ⚠ SACRED-FILE NOTICE: edits engine/bb_options/bb_trade_manager.py and
#   trading/paper_trade_recorder.py. Run ONLY after explicit confirmation.
#   The manager is shared by BB_V1 and BB_V2 (V2 reuses BBTradeManager), so
#   one fix covers both. BB is paper-only in the current fleet; the LIVE
#   branch is byte-for-byte unchanged except the guard now VERIFIES before
#   flipping into it.
#
# Fixes three defects in the BB paper EOD path (2026-08-25 incident: BB_V2
# CE row open past 15:15 on v10.4.6 with the app awake):
#
#   B1  Reverse-flip guard trusted _live_state.in_trade blindly. A stale
#       state/bb_v2_*.json (e.g. from ACC2 live testing) flips a PAPER EOD
#       exit into the LIVE branch, which fails on the phantom position and
#       strands the paper row. Now: verify an open LIVE row actually exists
#       in the trades table; none → log STALE_LIVE_STATE, clear + persist
#       the state file, continue PAPER. FAIL DIRECTION: any doubt (rows
#       present, or the flip was genuine) → LIVE, protecting a real
#       position per the GTT-race doctrine.
#   B2  exit_reason was swallowed: the PAPER branch hardcoded
#       reason="SuperTrend" into force_exit and the Telegram notify, so EOD
#       closes were mislabeled in the data. Now threads exit_reason through.
#   B3  force_exit silently SKIPPED on missing LTP (FORCE_EXIT_SKIP),
#       leaving the row open with no close attempt. New OPTIONAL
#       fallback_price param (default None → other callers byte-identical);
#       BB passes entry_price so an EOD close can never no-op.
#   B4  Belt-and-braces: eod_squareoff's PAPER branch now finishes with a
#       direct sweep of any STILL-open rows for this strategy_id
#       (LTP → entry fallback, reason EOD_SQUARE_OFF), so no failure mode
#       above — nor a future one — can leave a BB paper row open past the
#       manager-level EOD.
#
# Every anchor must match EXACTLY ONCE or the script aborts with no writes.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MGR = ROOT / "backend" / "app" / "engine" / "bb_options" / "bb_trade_manager.py"
REC = ROOT / "backend" / "app" / "trading" / "paper_trade_recorder.py"

EDITS_MGR = []
EDITS_REC = []


def edit(bucket, label, old, new):
    bucket.append((label, old, new))


# ────────────────── bb_trade_manager.py ──────────────────

edit(EDITS_MGR, "B1 verify-before-flip",
'''        _live_state = self.ce_state if side == "CE" else self.pe_state
        if effective_mode == "PAPER" and _live_state is not None and _live_state.in_trade:
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][EXIT] side={side} config=PAPER but "
                f"an OPEN LIVE position exists — exiting LIVE (exits follow the "
                f"position's mode)."
            )
            effective_mode = "LIVE"''',
'''        _live_state = self.ce_state if side == "CE" else self.pe_state
        if effective_mode == "PAPER" and _live_state is not None and _live_state.in_trade:
            # ── BB_EOD_HARDEN_20260825 (B1) ── VERIFY before flipping.
            # in_trade restores from the state JSON; a stale file (e.g. left
            # by ACC2 live testing) hijacked PAPER EOD exits into the LIVE
            # branch, which failed on the phantom position and stranded the
            # paper row (2026-08-25 BB_V2 CE carry). Truth source: the
            # trades table. FAIL DIRECTION: rows present or DB doubt → LIVE
            # (protect a real position, GTT-race doctrine); provably no
            # live row → clear the stale state and stay PAPER.
            from app.db.trades_repo import get_open_trades_for_strategy
            _live_rows = get_open_trades_for_strategy(self.strategy_id)
            if _live_rows:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][EXIT] side={side} "
                    f"config=PAPER but an OPEN LIVE position exists "
                    f"(verified: {len(_live_rows)} open row(s) in trades) — "
                    f"exiting LIVE (exits follow the position's mode)."
                )
                effective_mode = "LIVE"
            else:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][EXIT][STALE_LIVE_STATE] "
                    f"side={side} state file says in_trade but the trades "
                    f"table has NO open live row — clearing stale state and "
                    f"continuing with the PAPER exit."
                )
                try:
                    _live_state.clear_trade()
                except Exception as e:
                    write_audit_log(
                        f"[STRATEGY={self.strategy_id}][EXIT][WARN] stale-state "
                        f"clear failed: {e!r} (PAPER exit continues)"
                    )''')

edit(EDITS_MGR, "B2a force_exit reason threaded",
'''                    PaperTradeRecorder.force_exit(
                        paper_trade_id=paper_trade_id,
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        reason="SuperTrend",
                    )''',
'''                    PaperTradeRecorder.force_exit(
                        paper_trade_id=paper_trade_id,
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        reason=exit_reason,        # ── BB_EOD_HARDEN_20260825 (B2)
                        fallback_price=entry_price,  # (B3) EOD close must never no-op
                    )''')

edit(EDITS_MGR, "B2b notify reason threaded",
'''                        "exit_price":  safe_exit,
                        "exit_reason": "SuperTrend",
                        "pnl":         pnl,''',
'''                        "exit_price":  safe_exit,
                        "exit_reason": exit_reason,   # ── BB_EOD_HARDEN_20260825 (B2)
                        "pnl":         pnl,''')

edit(EDITS_MGR, "B4 post-sweep in eod_squareoff",
'''            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][EOD] "
                f"Paper square-off complete (open rows closed, flags cleared)"
            )
            return''',
'''            # ── BB_EOD_HARDEN_20260825 (B4) ── belt-and-braces: no failure
            # mode above may leave a row open past the manager-level EOD.
            # Close anything still OPEN for this strategy directly
            # (LTP → entry fallback), reason EOD_SQUARE_OFF. Idempotent.
            try:
                from app.db.paper_trades_repo import (
                    get_all_open_paper_trades, close_paper_trade,
                )
                _leftovers = get_all_open_paper_trades(self.strategy_id)
                for _row in _leftovers:
                    _px = LTPStore.get(_row["symbol"]) or _row["entry_price"]
                    try:
                        close_paper_trade(
                            paper_trade_id=_row["paper_trade_id"],
                            exit_price=float(_px),
                            exit_reason="EOD_SQUARE_OFF",
                        )
                        write_audit_log(
                            f"[STRATEGY={self.strategy_id}][PAPER][EOD]"
                            f"[POST_SWEEP] closed leftover "
                            f"{_row['symbol']} trade_id={_row['paper_trade_id']} "
                            f"@ {_px}"
                        )
                    except Exception as e:
                        write_audit_log(
                            f"[STRATEGY={self.strategy_id}][PAPER][EOD]"
                            f"[POST_SWEEP][ERROR] "
                            f"trade_id={_row['paper_trade_id']} ERR={e!r}"
                        )
            except Exception as e:
                write_audit_log(
                    f"[STRATEGY={self.strategy_id}][PAPER][EOD]"
                    f"[POST_SWEEP][ERROR] sweep failed: {e!r}"
                )
            write_audit_log(
                f"[STRATEGY={self.strategy_id}][PAPER][EOD] "
                f"Paper square-off complete (open rows closed, flags cleared)"
            )
            return''')

# ────────────────── paper_trade_recorder.py ──────────────────

edit(EDITS_REC, "B3a fallback param",
'''    def force_exit(
        *,
        paper_trade_id: str,
        strategy_id: str,
        symbol: str,
        reason: str,
    ):
        ltp = LTPStore.get(symbol)

        if ltp is None:
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][FORCE_EXIT_SKIP] "
                f"LTP_MISSING symbol={symbol}"
            )
            return''',
'''    def force_exit(
        *,
        paper_trade_id: str,
        strategy_id: str,
        symbol: str,
        reason: str,
        fallback_price: float | None = None,
    ):
        # ── BB_EOD_HARDEN_20260825 (B3) ── fallback_price (OPTIONAL, default
        # None → existing callers byte-identical): a missing LTP silently
        # skipped the close and stranded the row. EOD callers pass a
        # fallback so a square-off can never no-op.
        ltp = LTPStore.get(symbol)

        if ltp is None and fallback_price is not None:
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][FORCE_EXIT] LTP_MISSING "
                f"symbol={symbol} — closing at fallback_price="
                f"{fallback_price} (reason={reason})"
            )
            ltp = float(fallback_price)

        if ltp is None:
            write_audit_log(
                f"[STRATEGY={strategy_id}][PAPER][FORCE_EXIT_SKIP] "
                f"LTP_MISSING symbol={symbol}"
            )
            return''')


def apply(path: Path, edits):
    src = path.read_text(encoding="utf-8")
    for label, old, _ in edits:
        n = src.count(old)
        if n != 1:
            print(f"ABORT [{path.name}] anchor for {label!r} matched {n}x "
                  f"(need exactly 1). NO FILES WRITTEN.")
            sys.exit(1)
    for label, old, new in edits:
        src = src.replace(old, new, 1)
        print(f"  applied {label} -> {path.name}")
    path.write_text(src, encoding="utf-8")


def main():
    for p in (MGR, REC):
        if not p.exists():
            print(f"ABORT: {p} not found — run from repo root.")
            sys.exit(1)
    apply(MGR, EDITS_MGR)
    apply(REC, EDITS_REC)
    mgr = MGR.read_text(encoding="utf-8")
    rec = REC.read_text(encoding="utf-8")
    checks = [
        (mgr, "STALE_LIVE_STATE", 1),
        (mgr, "get_open_trades_for_strategy(self.strategy_id)", 1),
        (mgr, "POST_SWEEP", 3),   # 1 close log + 2 error logs
        (mgr, 'reason=exit_reason', 1),
        (mgr, '"exit_reason": exit_reason', 1),
        (mgr, '"SuperTrend",\n                    )', 0),
        (rec, "fallback_price", 6),  # sig+comment+cond+log+cast+mention
    ]
    ok = True
    for src, needle, want in checks:
        got = src.count(needle)
        print(f"  [{'OK ' if got == want else 'FAIL'}] {needle!r} x{got} (want {want})")
        ok = ok and got == want
    if not ok:
        print("POST-CHECK FAILED — inspect before building.")
        sys.exit(1)
    print("ALL EDITS APPLIED + VERIFIED.")


if __name__ == "__main__":
    main()
