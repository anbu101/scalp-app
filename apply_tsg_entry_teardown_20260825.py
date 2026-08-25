#!/usr/bin/env python3
"""
TSG_ENTRY_TEARDOWN_20260825 — tsg_manager.py entry-abort hardening
============================================================================
Incident (2026-08-25 09:16): the AttributeError raised inside the re-peg
step of _confirm_entry_with_repeg escaped the method, was swallowed by
_place_and_confirm's PLACE_FAIL catch, and therefore SKIPPED the
cancel-on-exhaust — the working L4 BUY stayed OPEN at Kite and filled 10s
after the day closed. Untracked naked long, closed manually.

The router fix (apply_tsg_router_contract_20260825.py) removes the trigger;
this script removes the ENTIRE failure class inside TSG, so no future
exception on this path can orphan a working order again:

  E1  _place_and_confirm: if an order_id exists when ANY exception escapes,
      run the shared cancel/readback teardown instead of walking away.
  E2  _confirm_entry_with_repeg:
        - quote/MODIFY re-peg step wrapped in its own try — any failure
          degrades to "keep current limit, wait another slice"
        - whole wait loop wrapped in a belt-and-braces try — any escape
          falls THROUGH to the teardown, never past it
        - cur_limit seeds to None when place_buy's hardcoded 0.0 comes back
          (fixes the misleading "limit=0.0" log + dead unchanged-price check)
  E3  _cancel_and_readback (new): extracted cancel + 5s terminal-state
      readback, adopting a cancel-raced COMPLETE fill; if NO terminal state
      is confirmed, fires TSG_ENTRY_ORDER_UNRESOLVED (CRITICAL) instead of
      silently returning filled=0.
  E4  _enter_live preflight: fail-closed executor-contract check — every
      method this entry path calls must exist BEFORE the first order is
      placed. Missing method => TSG_EXEC_CONTRACT alert + D_ABORTED with
      ZERO orders placed (today it surfaced mid-basket, after L3 filled).
  E5  _start_post_abort_reconcile (new) + call in the abort branch:
      read-only daemon thread polls broker positions (STRICT variant) for
      today's TSG symbols, 6 x 5s. Today's orphan filled 10 SECONDS after
      the day closed — an immediate single check would have missed it.
      Alert-only (fail-closed: a background thread never places orders).

Run from repo root; applies to BOTH trees; idempotent via fence marker;
aborts without writing on any anchor miss/ambiguity.
"""
import sys
from pathlib import Path

FENCE = "TSG_ENTRY_TEARDOWN_20260825"
TREES = ["backend/app", "desktop/src-tauri/backend/app"]
REL = "engine/tsg/tsg_manager.py"

A_REGION_START = "    def _place_and_confirm(self, leg: TsgLeg) -> dict:"
A_REGION_END = "    def _flatten_entry_residual(self, leg: TsgLeg, filled_qty: int) -> None:"
A_ORDER_SEQ = '        order = ["L3", "L4", "L1", "L2"]\n'
A_REPEG_END_LINE = '    # ── TSG_ENTRY_REPEG END ────────────────────────────────────────────\n'
A_UNWIND_ALERT = (
    '                self._alert("TSG_ENTRY_UNWIND",\n'
    '                            f"TSG_V1 LIVE entry failed at {lid} \u2014 unwound",\n'
    '                            severity="error", mode="live")\n'
    "                return False\n"
)

# ---------------------------------------------------------------------------
# E1 + E2 + E3 — replacement for the region [_place_and_confirm ..
# _confirm_entry_with_repeg + its exhaust/cancel tail), keeping the
# TSG_ENTRY_REPEG history comment that precedes the region untouched.
# ---------------------------------------------------------------------------
NEW_REGION = '''    def _place_and_confirm(self, leg: TsgLeg) -> dict:
        """Returns {"ok": bool, "avg": float|None, "filled_qty": int}."""
        fail = {"ok": False, "avg": None, "filled_qty": 0}
        oid = None
        try:
            tok = (self._token_by_sym.get(leg.symbol) or {}).get(
                "instrument_token")
            if leg.is_short:
                oid, limit_px, _ = self.executor.place_sell_entry(
                    symbol=leg.symbol, token=tok, qty=leg.qty)
            else:
                out = self.executor.place_buy(leg.symbol, tok, leg.qty)
                oid, limit_px = (out[0], out[1]) if isinstance(
                    out, (tuple, list)) else (out, None)
            res = self._confirm_entry_with_repeg(leg, oid, limit_px)
            if res["ok"]:
                leg.entry_order_id = oid
            return res
        except Exception as e:
            write_audit_log(f"[TSG][ENTRY][{leg.leg_id}][PLACE_FAIL] {e!r}")
            # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 \u2500\u2500 if an order_id exists, a
            # working order may exist at the broker: cancel + read back
            # (adopting a raced fill) instead of walking away \u2014 the exact
            # walk-away that orphaned the 2026-08-25 L4 fill.
            if oid is not None:
                try:
                    res = self._cancel_and_readback(leg, oid, None)
                    if res["ok"]:
                        leg.entry_order_id = oid
                    return res
                except Exception as e2:
                    write_audit_log(
                        f"[TSG][ENTRY][{leg.leg_id}][TEARDOWN_FAIL] {e2!r}")
            return fail

    def _confirm_entry_with_repeg(self, leg: TsgLeg, oid,
                                  limit_px) -> dict:
        cfg = self._cfg()
        slice_s = max(2, int(cfg.get("entry_fill_timeout_s", 5) or 5))
        max_repegs = max(0, int(cfg.get("entry_repeg_max", 3) or 3))
        t0 = time.time()
        attempt = 0                     # 0 = initial placement
        # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 \u2500\u2500 place_buy returns (oid, 0.0,
        # qty); the 0.0 seed made the heartbeat read "limit=0.0" and defeated
        # the price-unchanged check. Treat falsy as unknown.
        cur_limit = limit_px if limit_px else None
        try:
            while attempt <= max_repegs:
                slice_t0 = time.time()
                while time.time() - slice_t0 < slice_s:
                    st = {}
                    try:
                        st = self.executor.get_order_fill(oid) or {}
                    except Exception:
                        pass
                    status = (st.get("status") or "").upper()
                    filled = int(st.get("filled_qty") or 0)
                    write_audit_log(
                        f"[TSG][ENTRY_WAIT] leg={leg.leg_id} order_id={oid} "
                        f"status={status or 'PENDING'} filled={filled}/{leg.qty} "
                        f"attempt={attempt}/{max_repegs} "
                        f"limit={cur_limit} elapsed={time.time() - t0:.0f}s")
                    if status == "COMPLETE":
                        avg = float(st.get("avg_price") or 0.0)
                        return {"ok": True,
                                "avg": avg if avg > 0 else cur_limit,
                                "filled_qty": filled or leg.qty}
                    if status in ("REJECTED", "CANCELLED", "LAPSED"):
                        write_audit_log(
                            f"[TSG][ENTRY][{leg.leg_id}] order {status} "
                            f"broker-side \u2014 no re-peg possible")
                        return {"ok": False, "avg": None,
                                "filled_qty": filled}
                    time.sleep(1.0)
                attempt += 1
                if attempt > max_repegs:
                    break
                # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 \u2500\u2500 the quote/MODIFY step of
                # the re-peg is best-effort: ANY failure here (2026-08-25:
                # AttributeError through the un-forwarding router) degrades to
                # "keep the current limit and wait another slice". It must
                # never escape this loop \u2014 escaping is what skipped the
                # cancel below and orphaned the working L4 order.
                try:
                    fresh = (self.executor.fresh_sell_entry_limit(leg.symbol)
                             if leg.is_short
                             else self.executor.fresh_buy_entry_limit(
                                 leg.symbol))
                    if fresh is None:
                        write_audit_log(
                            f"[TSG][ENTRY_REPEG] leg={leg.leg_id} "
                            f"attempt={attempt}"
                            f" \u2014 no fresh quote, keeping limit {cur_limit}")
                        continue
                    new_limit, ref, src = fresh
                    if (cur_limit is not None
                            and abs(new_limit - cur_limit) < 0.049):
                        write_audit_log(
                            f"[TSG][ENTRY_REPEG] leg={leg.leg_id} "
                            f"attempt={attempt}"
                            f" \u2014 price unchanged ({new_limit} ~ {cur_limit}), "
                            f"waiting another slice")
                        continue
                    ok = self.executor.modify_order(oid, price=new_limit,
                                                    symbol=leg.symbol)
                    if ok is None:
                        write_audit_log(
                            f"[TSG][ENTRY_REPEG] leg={leg.leg_id} "
                            f"attempt={attempt}"
                            f" \u2014 MODIFY failed, keeping limit {cur_limit}")
                        continue
                    write_audit_log(
                        f"[TSG][ENTRY_REPEG] leg={leg.leg_id} "
                        f"attempt={attempt} "
                        f"order_id={oid} {cur_limit} -> {new_limit} "
                        f"(ref={ref} src={src})")
                    cur_limit = new_limit
                except Exception as e:
                    write_audit_log(
                        f"[TSG][ENTRY_REPEG] leg={leg.leg_id} "
                        f"attempt={attempt}"
                        f" \u2014 re-peg step failed ({e!r}), keeping limit "
                        f"{cur_limit}")
                    continue
        except Exception as e:
            # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 \u2500\u2500 belt-and-braces: NOTHING
            # that goes wrong while an entry order is working may skip the
            # cancel/readback below.
            write_audit_log(
                f"[TSG][ENTRY][{leg.leg_id}][WAIT_LOOP_FAIL] {e!r} \u2014 "
                f"falling through to cancel/readback")
        # exhausted (or wait-loop failure) \u2192 cancel, then read back the
        # post-cancel fill state (D5) \u2014 shared with the PLACE_FAIL path.
        return self._cancel_and_readback(leg, oid, cur_limit)

    # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 BEGIN \u2500\u2500 shared teardown for a working
    # entry order (re-peg exhaust, wait-loop failure, or post-placement
    # exception). Invariant: once an entry order_id exists, EVERY abort path
    # runs this \u2014 cancel first, then read back a terminal state, adopting a
    # cancel-raced COMPLETE fill instead of walking away from it.
    def _cancel_and_readback(self, leg: TsgLeg, oid, cur_limit) -> dict:
        try:
            self.executor.cancel_order(oid, symbol=leg.symbol)
        except Exception as e:
            write_audit_log(f"[TSG][ENTRY][{leg.leg_id}][CANCEL_FAIL] {e!r}")
        filled, avg, status = 0, 0.0, ""
        cancel_t0 = time.time()
        while time.time() - cancel_t0 < 5:
            try:
                st = self.executor.get_order_fill(oid) or {}
                status = (st.get("status") or "").upper()
                filled = int(st.get("filled_qty") or 0)
                avg = float(st.get("avg_price") or 0.0)
                if status == "COMPLETE":
                    # cancel raced a full fill \u2014 the leg is actually ours
                    write_audit_log(
                        f"[TSG][ENTRY][{leg.leg_id}] cancel raced a "
                        f"COMPLETE fill \u2014 accepting leg")
                    return {"ok": True,
                            "avg": avg if avg > 0 else cur_limit,
                            "filled_qty": filled or leg.qty}
                if status in ("CANCELLED", "REJECTED", "LAPSED"):
                    break
            except Exception:
                pass
            time.sleep(1.0)
        if status not in ("CANCELLED", "REJECTED", "LAPSED"):
            # 2026-08-25: the L4 order was still WORKING when the day closed
            # and filled 10s later, untracked. If no terminal state is
            # confirmed, say so LOUDLY instead of a silent filled=0.
            self._alert(
                "TSG_ENTRY_ORDER_UNRESOLVED",
                f"TSG_V1: entry order {oid} on {leg.leg_id} ({leg.symbol}) "
                f"NOT confirmed cancelled \u2014 it may still be working at the "
                f"broker. CHECK KITE ORDERS NOW",
                severity="error", mode="live")
        write_audit_log(
            f"[TSG][ENTRY][{leg.leg_id}] entry timed out \u2014 cancelled "
            f"(filled={filled}/{leg.qty} avg={avg})")
        return {"ok": False,
                "avg": avg if filled > 0 else None,
                "filled_qty": filled}
    # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 END \u2500\u2500

'''

# ---------------------------------------------------------------------------
# E4 — fail-closed executor-contract preflight, inserted before the entry
# order sequence in _enter_live.
# ---------------------------------------------------------------------------
PREFLIGHT = '''        # \u2500\u2500 TSG_EXEC_CONTRACT_20260825 \u2500\u2500 fail-closed preflight: verify the
        # attached executor (raw or router-wrapped) exposes every method this
        # entry path calls, BEFORE the first order. 2026-08-25 incident: the
        # missing method surfaced mid-basket (after L3 filled); it must abort
        # with ZERO orders placed instead.
        _required = ("place_buy", "place_sell_entry", "get_order_fill",
                     "cancel_order", "modify_order", "fresh_buy_entry_limit",
                     "fresh_sell_entry_limit", "place_market_sell",
                     "place_buy_exit")
        _missing = [m for m in _required
                    if not callable(getattr(self.executor, m, None))]
        if _missing:
            self._alert("TSG_EXEC_CONTRACT",
                        f"TSG_V1 LIVE entry blocked \u2014 executor missing "
                        f"{', '.join(_missing)} (no orders placed)",
                        severity="error", mode="live")
            self._core.state = D_ABORTED
            return False
'''

# ---------------------------------------------------------------------------
# E5 — post-abort broker reconciliation (read-only daemon thread) appended
# after the TSG_ENTRY_REPEG END fence, plus its call in the abort branch.
# ---------------------------------------------------------------------------
RECONCILE_METHOD = '''
    # \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 (E5) \u2500\u2500 post-abort reconciliation.
    def _start_post_abort_reconcile(self) -> None:
        """After an entry abort/unwind, watch broker positions for today's
        TSG symbols in a READ-ONLY daemon thread (6 checks x 5s). The
        2026-08-25 orphan filled 10 SECONDS after the day closed \u2014 an
        immediate single check would have missed it. Alert-only, fail-closed:
        a background thread never places orders. Uses the STRICT positions
        read (None = could-not-read, never mistaken for flat)."""
        try:
            syms = {l.symbol for l in self._core.legs.values() if l.symbol}
        except Exception:
            syms = set()
        if not syms or self.executor is None:
            return

        def _watch():
            for _ in range(6):
                time.sleep(5)
                try:
                    fn = getattr(self.executor,
                                 "get_open_positions_or_none", None)
                    pos = fn() if callable(fn) else None
                    if pos is None:
                        continue
                    leaks = [p for p in pos
                             if p.get("tradingsymbol") in syms
                             and int(p.get("quantity") or 0) != 0]
                    if leaks:
                        det = ", ".join(
                            f"{p.get('tradingsymbol')} "
                            f"qty={p.get('quantity')}" for p in leaks)
                        self._alert(
                            "TSG_ORPHAN_POSITION",
                            f"TSG_V1: broker shows OPEN position(s) after "
                            f"abort/unwind: {det} \u2014 UNTRACKED by the app, "
                            f"CLOSE MANUALLY IN KITE NOW",
                            severity="error", mode="live")
                        return
                except Exception:
                    pass

        threading.Thread(target=_watch, daemon=True,
                         name="tsg-post-abort-reconcile").start()
'''

RECONCILE_CALL = (
    '                self._alert("TSG_ENTRY_UNWIND",\n'
    '                            f"TSG_V1 LIVE entry failed at {lid} \u2014 unwound",\n'
    '                            severity="error", mode="live")\n'
    "                self._start_post_abort_reconcile()  "
    "# \u2500\u2500 TSG_ENTRY_TEARDOWN_20260825 (E5)\n"
    "                return False\n"
)


def apply_tree(root: Path, tree: str) -> str:
    path = root / tree / REL
    if not path.exists():
        raise SystemExit(f"[ABORT] missing file: {path}")
    src = path.read_text(encoding="utf-8")

    if FENCE in src:
        return f"[SKIP] {path} — fence {FENCE} already present (idempotent)"

    # ---- pre-write asserts (abort before ANY write) -----------------------
    for name, anchor in (("region-start", A_REGION_START),
                         ("region-end", A_REGION_END),
                         ("order-seq", A_ORDER_SEQ),
                         ("repeg-end-line", A_REPEG_END_LINE),
                         ("unwind-alert", A_UNWIND_ALERT)):
        n = src.count(anchor)
        assert n == 1, (f"[ABORT] {path}: anchor '{name}' found {n}x "
                        f"(expected exactly 1). File drifted — re-inspect.")

    i0 = src.index(A_REGION_START)
    i1 = src.index(A_REGION_END)
    assert i0 < i1, f"[ABORT] {path}: region anchors out of order"
    region = src[i0:i1]
    # Sanity: the region we are replacing is the one we analysed.
    for must in ("PLACE_FAIL", "fresh_sell_entry_limit",
                 "cancel raced a", "entry timed out"):
        assert must in region, (
            f"[ABORT] {path}: expected {must!r} inside replace region — "
            "region boundaries drifted, re-inspect.")

    out = src[:i0] + NEW_REGION + src[i1:]
    out = out.replace(A_ORDER_SEQ, PREFLIGHT + A_ORDER_SEQ, 1)
    out = out.replace(A_UNWIND_ALERT, RECONCILE_CALL, 1)
    out = out.replace(A_REPEG_END_LINE,
                      A_REPEG_END_LINE + RECONCILE_METHOD, 1)

    # ---- compile gate before touching disk --------------------------------
    compile(out, str(path), "exec")

    path.write_text(out, encoding="utf-8")
    return f"[OK]   {path} — entry teardown hardening applied"


def main() -> None:
    root = Path.cwd()
    for tree in TREES:
        if not (root / tree).is_dir():
            raise SystemExit(
                f"[ABORT] tree not found: {root/tree} — run from repo root.")
    results = [apply_tree(root, t) for t in TREES]
    print("\n".join(results))

    # ---- post-write structural verification, both trees -------------------
    for tree in TREES:
        s = (root / tree / REL).read_text(encoding="utf-8")
        checks = {
            "teardown fence x2": s.count(
                "TSG_ENTRY_TEARDOWN_20260825 BEGIN") == 1
            and s.count("TSG_ENTRY_TEARDOWN_20260825 END") == 1,
            "_cancel_and_readback defined once":
                s.count("def _cancel_and_readback(") == 1,
            # 2 call sites cover 3 abort paths: PLACE_FAIL, plus the
            # exhaust + wait-loop-failure paths converging on one shared call.
            "_cancel_and_readback called from 2 sites":
                s.count("self._cancel_and_readback(") == 2,
            "preflight present":
                "TSG_EXEC_CONTRACT_20260825" in s,
            "preflight precedes order sequence":
                s.index("TSG_EXEC_CONTRACT_20260825")
                < s.index('order = ["L3", "L4", "L1", "L2"]'),
            "reconcile method defined once":
                s.count("def _start_post_abort_reconcile(") == 1,
            "reconcile called in abort branch":
                s.count("self._start_post_abort_reconcile()") == 1,
            "unresolved-order alert present":
                "TSG_ENTRY_ORDER_UNRESOLVED" in s,
        }
        bad = [k for k, v in checks.items() if not v]
        assert not bad, f"[VERIFY-FAIL] {tree}: {bad}"
    print("[VERIFY] both trees: all structural checks passed, compile OK")


if __name__ == "__main__":
    main()
