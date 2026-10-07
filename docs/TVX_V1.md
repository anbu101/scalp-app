# TVX_V1 — TradingView alert paper strategy (MCX crude options)

Fence `TVX_V1_20261007`. Paper only, admin only. There is no order path in this module.

## Purpose

Paper-trade the "Options Bulls" TradingView indicator for a few days, so you can see whether its signals are worth anything after real fills and charges. This is an evaluation, not a sealed strategy.

## Data path

```
TradingView alert ─► tv-relay (Cloudflare Worker, D1) ─► Telegram group
                                   │
                                   └─ GET /alerts?since=<id> ◄─ TVX engine (every 2 s)
```

The relay is independent of the app. The app only reads from it, using `READ_TOKEN`.

## Rules (decided by Anbu, 2026-10-07)

| Alert | Flat | Long CE | Long PE |
|---|---|---|---|
| BUY  | buy ATM CE | hold | exit PE, buy ATM CE |
| SELL | buy ATM PE | exit CE, buy ATM PE | hold |
| T1   | nothing | exit | exit |
| T2 / Custom target | logged only | logged only | logged only |

**Contract and size**

- **Option:** the nearest crude option expiry after today. On expiry day it rolls to the next month.
- **Strike:** ATM, taken from the price of the futures contract the option is written on, using 50-point strikes.
- **Size:** 1 lot = 100 bbl. Kite reports MCX `lot_size` as 1, confirmed by the 2026-10-07 probe.

**Stop-loss:** 30 points on the option premium (configurable). It triggers on the LTP and fills at the bid.

**Fills:**

- Buys fill at the ask and sells fill at the bid.
- If that side of the book is empty, the fill uses the LTP instead, and the row is flagged.
- The spread at entry and at exit is stored on each trade.

**Session:**

- New entries start at 09:00 IST.
- Square-off happens 10 minutes before the MCX close: 23:45 while US daylight saving time is on (close 23:55), 23:20 otherwise (close 23:30). Entries stop at the same time.
- If the app is closed at square-off, the position is closed at the next session's first quote and marked `EOD_LATE`. Exclude these trades from the analysis.

**Charges:** Zerodha MCX options, per round trip:

- Brokerage: ₹20 per order.
- CTT: 0.05% of the sell-side premium.
- MCX transaction charges: 0.0418% of premium turnover.
- SEBI fees: ₹10 per crore.
- Stamp duty: 0.003% of the buy-side premium.
- GST: 18% on brokerage, transaction and SEBI charges.

## Safety

- **Stale alerts:** alerts older than 90 seconds are recorded and never traded.
- **No double-processing:** each alert is processed at most once. Its row is written before any action, and the cursor is persisted.
- **One position at most:** a partial UNIQUE index in the database allows only one OPEN row.
- **Failed exits:**
  - A failed exit never leads to a new entry.
  - The pending exit is persisted and retried on every loop.
  - New entries are blocked until it clears.
- **Switching off:** turning the strategy off with a position open means the position is still managed to its normal exit, with no new entries.
- **Isolation:**
  - TVX uses its own tables (`tvx_trades`, `tvx_alerts`, `tvx_kv`) and its own config file (`~/.scalp-app/config/tvx_v1.json`).
  - It never touches `paper_trades`, NSE square-off or the MTM guard.
  - Prices come from REST quotes on the existing data session. No new Kite WebSocket is opened.

## Analysis

The **Export CSV** button writes `~/.scalp-app/exports/tvx_trades_<ts>.csv`. Each row includes:

- Entry and exit bid/ask and fill source
- Futures price and the alert price
- MFE/MAE in points
- Gross P&L, charges and net P&L

Keep two kinds of cost apart: signal quality is gross P&L at mid price, and execution cost is spread plus charges.
