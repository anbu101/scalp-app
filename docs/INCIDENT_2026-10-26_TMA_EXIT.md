# Incident 2026-10-26 — TMA_V2 live exit left a short open, then a stray long

## What happened (PAPER_LIVE, NIFTY 27-Oct expiry, 65 qty)

| Time | Event |
|---|---|
| 09:26:04 | BUY hedge 23200CE filled @4.50 |
| 09:26:08 | SELL 22400CE filled @159.85, SL 179.03 |
| 09:26:10 | `GTT_FAIL: 'ExecutionRouter' object has no attribute 'place_gtt_sl_only_short'`, so no broker-side SL |
| 09:29:01 | Candle SL fired. App BUY LIMIT 182.75 (LTP 180.95 + 1%). The market was already ~209, so the order **rested unfilled** |
| 09:29:48 | 45s fill-confirm timed out. App **moved on without cancelling**, tried to sell the hedge, and was rejected for margin (naked short) |
| 09:29:48 | App booked both legs CLOSED at model prices (−₹1,374) |
| 09:30:28–34 | User flattened manually: BUY 22400CE @209.70, SELL 23200CE @5.25 |
| 09:35:49 | The abandoned 182.75 order **filled**, opening an untracked +65 long |
| 09:46:19 | User sold it @208.80 |

Real gross P&L was about −₹1,498 (the app showed −₹1,374, by coincidence).

## Root causes

1. **Router contract gap (fleet-wide).** ACC2_W31 (2026-08-18) moved every strategy onto `ExecutionRouter`, which forwards explicitly with no `__getattr__`. Four executor methods were never forwarded:
   - `place_gtt_sl_only_short`: TMA_V1, TMA_V2 and IC shorts have had no broker SL since that date.
   - `place_gtt_sl_only_long` and `place_gtt_tp_only_long`: IC adjustment legs and HA_V1.
   - `get_basket_margin`: the TMA and IC margin guard silently "failed open".

   Manager tests use fakes that have these methods, so nothing caught the gap. This is the same bug class as TSG_ROUTER_CONTRACT_20260825.
2. **The exit abandoned unfilled orders.** It did a single confirm wait, with no re-price and no cancel.
3. **The hedge was sold without confirming the short was flat.**
4. **Model prices were booked when broker actions failed.**
5. **Latent issue:** the GTT monitor treated "triggered, fill unconfirmed for 60s" as flat. The SL GTT limit buffer was 0.3%. Once GTTs worked again, this would have reproduced the same failure.
6. **Latent issue:** entry-order timeouts cancelled without confirming. A fill that won the race went untracked.

## Fixes

| Tag | Where | What |
|---|---|---|
| ROUTER_GTT_FORWARDS_20261026 | `execution/execution_router.py` | Forwards the 4 methods. An optional argument a broker doesn't accept (Angel has no `limit_buffer`) is dropped, not fatal. |
| ROUTER_GTT_FORWARDS_20261026 | `execution/test_execution_router_contract.py` | AST-scans strategy code. The build fails if any `executor.X` call is not forwarded. |
| EXIT_ORDER_DRIVER_20261026 | `execution/exit_order_driver.py` | Re-pegs a working exit order toward the touch every 4s. Cancels at 25s and confirms the outcome. Returns FILLED, DEAD or UNRESOLVED, and never leaves an order silently working. |
| EXIT_STATE_MACHINE_20261026 | `tma/tma_trade_manager.py`, `tma2/tma2_trade_manager.py` | See below. |
| GTT_UNFILLED_ESCALATION_20261026 | `tma/tma_gtt_monitor.py`, `tma2/tma2_gtt_monitor.py` | See below. |
| BROKER_RECON_20261026 | both managers | See below. |

**EXIT_STATE_MACHINE_20261026** makes the LIVE exit a retryable two-stage sequence:
- The hedge is sold **only after the short is confirmed flat**.
- A stuck leg books nothing. The DB rows stay OPEN, a precise alert names the open leg and the manual order (buy the short first, then sell the hedge), and the exit is retried every minute.
- An order whose cancel can't be confirmed is driven again before any new order for that leg.
- Bookings use broker fills, including after a manual flatten.
- Entry timeouts cancel and confirm. A hedge unwind that doesn't fill is reported as still open, not booked.
- The SL GTT limit buffer defaults to 5% (configurable as `gtt_limit_buffer_pct`).

**GTT_UNFILLED_ESCALATION_20261026** makes the monitor read the triggered GTT's own order:
- COMPLETE: handed off as flat.
- Still working: handed to the manager, which re-pegs **that same order** (no second buy-back).
- Rejected with the position still open: the manager exits.

**BROKER_RECON_20261026** compares Kite net quantity with expectations every 2 minutes, for the open group and for symbols closed earlier that day. It alerts only after the mismatch is confirmed on two reads. It is read-only.

Tests: `tma2/test_tma2_exit_paths.py` and `tma/test_tma_exit_paths.py` replay this incident against a Kite-shaped fake that has the margin rejection and the resting-limit behaviour. Against the pre-fix code the replay reproduces the incident exactly: the 179.03 booking, the same alert, and the stray +65 long.

## Behaviour changes on deploy

- **The margin guard now runs** for TMA_V1, TMA_V2 and IC. Entries are skipped when available margin is below 1.25× required.
- **IC now places its SL/TP GTTs.** This is the designed behaviour, but it is absent live since 2026-08-18. Paper-run IC first.
- **A stuck TMA exit blocks that strategy's minute loop** for up to about 25s per leg per attempt.

## Known gaps NOT fixed (listed in the contract test's `KNOWN_GAPS`)

- **HA_V1** reads `executor.broker_manager` and `executor._relay_call`. Under the router these are missing, so **every HA live entry has been refused** ("trade session not ready") since 2026-08-18. Forwarding them would make HA start placing live orders, which needs an owner decision and a paper run.
- **BB_V1/BB_V2 `GTTMonitor`** calls `executor._kite()`. Under the router this raises, so every sweep logs FETCH_FAIL and **BB GTT fills are never detected**.
- `bb_trade_manager` and `risk_mtm_guard` also read `executor.broker_manager`.

## Follow-up review (same night): IC_V2, VET_V1, TSG_V1 before their live start

The same failure class was found in all three. All are fixed, with incident-replay tests that fail on the pre-fix code.

| Strategy | Tag | What was wrong | Fix |
|---|---|---|---|
| IC_V1/IC_V2 | IC_EXIT_CONFIRM_20261026 | See below. | See below. |
| VET_V1 | VET_LIVE_CONFIRM_20261026 | See below. | See below. |
| TSG_V1 | TSG_EXIT_CONFIRM_20261026 | See below. | See below. |

### IC_V1/IC_V2 (IC_EXIT_CONFIRM_20261026)

What was wrong:
- `_flatten_live` placed exit orders and returned FLAT immediately. Every live close was booked at LTP, filled or not, and an unfilled limit stayed working.
- Order failures were booked as closed.
- Wings were sold right after their short regardless of the short's fill.
- A bulk close that left legs open was never retried before the EOD backstop, because `on_tick` only acts on G_OPEN groups.
- Entry timeouts cancelled the order without confirming it.

Fix:
- Every exit slice is driven to a confirmed outcome. A leg that isn't confirmed stays OPEN and nothing is booked.
- An order whose cancel can't be confirmed is driven again before any new order for that leg.
- Wings are held while their same-type short is open.
- `process_due` retries a stuck G_CLOSING group.
- Entry cancels are confirmed; a partial fill on a dead leg is flattened.
- Unwinds run shorts first and page when they can't be confirmed.

### VET_V1 (VET_LIVE_CONFIRM_20261026)

What was wrong:
- **Every LIVE insert failed.** The `(order_id, limit, qty)` tuple was stored as `entry_order_id` (sqlite: "type 'tuple' is not supported"), so there were no rows, no restart adoption, no EOD card and no shadow twin. The test fakes returned plain strings, which hid it.
- No fill confirmation anywhere:
  - the short was sold without knowing the wing had filled;
  - an RMS-rejected short was still treated as held, so its exit would BUY and create a long;
  - exits were fire-and-forget, with the wing sold in the same instant as the buy-back.

Fix:
- Order IDs are normalised.
- The wing BUY is driven to a fill, and the short is sent only after it.
- The main leg is confirmed: rejected means the wing is unwound (driven, and paged on failure); unresolved means entries are frozen and a page is sent.
- Exits run the same confirmed two-stage sequence as TMA. `retry_exit` runs each minute from the coordinator while the candle is still folded, for signal parity.
- FLIP never reopens while a close is stuck.
- Bookings use broker fills.

### TSG_V1 (TSG_EXIT_CONFIRM_20261026)

The entry path was already sound (TSG_ENTRY_REPEG / TEARDOWN).

What was wrong:
- `_market_close` waited 20s, then left an unfilled order WORKING.
- Hedges were sold even when their short didn't close.
- After `begin_close()` set D_CLOSING, `on_minute` returned early, so a stuck leg waited for EOD, and that retry placed a SECOND order beside the first.

Fix:
- Exits go through a persisted pending queue, drained shorts first, with each hedge held while its short is open.
- Orders are driven and confirmed, and remembered when unresolved.
- `on_minute` drains in any state.
- The queue survives a restart (session file).

Other effects on deploy: VET entries can take up to about 35s, because the wing and the short each wait for their fills.
