# FVG_V1 — "Fissure" — Strategy Bible

**Status:** sealed 2026-09-16 (backtest); paper next.
**Fence:** `FVG_V1_20260916` · lab fence `FVG_LAB_20260916` (`tools/lab/fvg/`)
**Author:** Anbu (operator/decisions) + Claude (design/code), one session, 2026-09-16.
**Origin:** an experiment — "build a NIFTY intraday option-*buying* strategy from
indicators the app has never used (FVG, IFVG, Fibonacci), then fine-tune it from
the backtest". Five rounds later one configuration survived; most of the original
idea did not.

---

## 1. What it is

Long NIFTY weekly **CE**, bought at the **retest of a morning displacement gap**,
with a **structural spot stop** and a **timed exit**.

* **Signal timeframe:** 5-minute bars from 1-minute NIFTY spot on the 09:15 grid.
* **Fair Value Gap (FVG):** three consecutive 5m candles where `c[i].low > c[i-2].high`
  (bullish). The band `[c[i-2].high, c[i].low]` is the gap. Only gaps whose middle
  candle **body ≥ 1.5 × ATR(14)** and whose width ≥ 0.15 × ATR count.
* **Inverse FVG (IFVG):** a gap that is violated by a 5m **close** through it flips
  polarity (old bullish gap → bearish band and vice versa). The retest of the
  inverted band is traded in the new direction. (Only the CE side is sealed, so in
  practice IFVG longs come from violated *bearish* gaps.)
* **Entry trigger (1m):** price comes back *into* the band, then a 1m **green close
  back above** it. Buy the ATM weekly CE at the **next 1m open**. A gap is used once.
* **Stop:** gap-band bottom − 0.1 ATR, on **spot**. Floor 15 pts (widened if closer),
  skip if > 3 ATR. Trigger = **1m close** through the level; the option is sold at
  the **next minute's open** (wick-immune, ORB_SLTRIG convention).
* **Exit:** sold at the 1m close **60 minutes** after entry, or EOD 15:20. **No target.**
* **Window:** entries 09:30–11:00 only. **Max 1 trade/day.** **Expiry day skipped.**
* Costs: locked v4 Zerodha charges (`charges_model.py`).

The engine (`fvg_v1_engine.py`) is pure and decides everything from closed bars,
so a live engine can replay it incrementally (parity-by-construction).

## 2. Sealed config (`DEFAULTS` in `backtest_fvg_runner.py`)

```
tf 5 · atr_len 14 · disp_atr 1.5 · gap_min_atr 0.15 · fib_gate off
trade_fvg on · trade_ifvg on · direction CE · gap_max_age 12 · max_gaps 6
strike_offset 0 · premium band off · lots 1 (index lot)
sl_min_pts 15 · sl_max_atr 3 · sl_buf_atr 0.1 · sl_trigger close · sl_fill low (inert)
tp_mode off · be_rr 0 · hold_max 60
entry_from 09:30 · entry_until 11:00 · eod_square_off 15:20
max_trades_per_day 1 · skip_expiry_day on · dte 0–99 · warmup_sessions 5
```

**Result, 10 lots, 2020-01 → 2026-09 (lab round 5, row 1):**
175 trades · years **+6/7** (2020 −₹27k on 5 trades) · worst month **−₹41.6k** ·
4 consecutive losing months · 32/61 months positive · maxDD **₹1.18L** ·
net **₹5.49L** · net/DD **4.64** · 2020–23 ₹2.95L / 2024–26 ₹2.54L ·
SL-fill artifact ₹12k · win rate ~42% · avg win ₹16k / avg loss ₹8k.

**Calibration to expect in paper:** ~₹80k/year at 10 lots, one losing month a
quarter, up to nine losing trades in a row. Capital footprint is premium-only
(~₹65k–1L per trade). A small, capital-light morning strategy — not a fleet anchor.

## 3. The five rounds (all 10 lots, 2020–2026, full corpus)

| Round | Question | Result |
|---|---|---|
| 1 — v1 as designed | 5m, disp 1.0, fib on, FVG+IFVG, both sides, 09:30–14:30, 3/day, 2R, hold 60, touch-trigger low-fill | **Falsified.** 3,061 trades, gross ₹63k, charges ₹5.98L, net −₹5.35L, 2/7 years, 2023–26 all negative. SL-fill convention worth ₹16.7L (low −₹5.35L vs close +₹11.37L). |
| 2 — pre-registered filters | sl_trigger × dte≥3 × until 11:00 × CE-only × fib (32 runs) | One config cleared the bar: CE-only, until 11:00, close trigger, fib off — 225 trades, 6/7 years, ₹2.20L/₹2.29L split, net ₹4.49L, DD ₹1.34L. Every BOTH row lost; entries to 14:30 lose 2024–26. |
| 3 — anatomy + plateau | top-5 concentration, losing year, 27 neighbours (disp × tp_rr × until) | Net without top-5 winners +₹1.78L; all neighbours net-positive with ≥5/7 years; disp 1.2 degrades post-2024, disp ≥1.5 holds. TIME exits carry the net; 3R hit 18/225. |
| 4 — exit shape | hold 30–120 × tp 3/8 × be × skip-expiry (40 runs) | Hold is a rising plateau (30m ₹2.5L → 120m ₹7.4L, DD flat ₹1.2–1.3L). Target decorative. Breakeven rejected. Skip-expiry improves worst month (−₹63k → −₹42k) at hold ≤60. |
| 5 — control | strategy vs fixed 10:00 entry every day vs fixed 10:00 on signal days | Strategy ₹5.5L / DD ₹1.2L. Controls: every day −₹7.1L to −₹9.9L (0–2 years positive); matched days −₹1.8L to −₹2.2L (DD ₹3–4.3L). **The edge is entry location, not day selection or morning drift.** |

## 4. Falsification record — tested and rejected, do not rebuild

* **PE side / BOTH direction** — negative carry in every configuration (round 1 PE −₹10L; round 2 every BOTH row negative).
* **Entries after 11:00** — 2024–26 negative (−₹1.2L at 14:30 vs +₹2.5L at 11:00 on the same rules).
* **Fibonacci golden-pocket gate (0.382–0.786)** — never improved worst month or DD; cost 10–12% of trades and ₹20–40k of net in every pair, two rounds. The one indicator the experiment was named after adds nothing.
* **Displacement 1.0–1.2 ATR** — round 1 (1.0) falsified outright; 1.2 makes in-sample net but +₹20–44k out-of-sample (2024–26) vs +₹1.4–2.0L for 1.5.
* **Breakeven stop at +1R** — −₹0.6–1.5L at every hold, DD equal or worse.
* **Profit target (2R/3R/4R/8R)** — indistinguishable from off; 18/225 hits at 3R.
* **Touch-trigger stop with option-low fill** — not wrong, but the result is then a fill-model artifact (₹16.7L swing). Close trigger sealed; if touch is ever used, compare low vs close fill first.
* **Fixed-time entry (10:00) with or without gap-day selection** — both controls negative (round 5).

Documented variants, not sealed: **hold 90** (₹7L, net/DD 5.75, but 5/7 years and −₹50.6k worst month); **dte ≥ 3** (net/DD 4.29 on 94 trades, too sparse); **disp 2.0** (7/7 years, ₹3.3L, 65 trades).

## 5. Tripwires to read on every run (`summary.diag_fvg`)

* `time_pnl_share_pct` — the timed exit carries the net **by design**; net migrating into TP/SL means a different strategy.
* `sl_fill_artifact_net` — ≈ 0 with the sealed close trigger; anything else is a red flag.
* `top5_share_pct`, `net_without_top5` — 60% / +₹1.78L on the sealed run; the latter must stay positive.
* `years`, `pos_years`, `net_pre_split`, `net_post_split`, `worst_month`, `max_consec_losing_months` — the scoreboard in priority order.
* `fvg_net` vs `ifvg_net`, `entry_hour_hist`, DTE breakdown (0DTE was the only losing DTE).
* `sig_sl_widened` — share of stops at the 15-pt floor (18% sealed); a jump means the gaps got thinner.

## 6. Known limits and open items

* **Long-only exposure.** The strategy holds a CE for an hour on ~35 mornings a year. 2020–26 is a rising index; six positive years and a negative control are the evidence it is not pure beta, but a bear regime has not been observed. Paper is the next test.
* **Special sessions.** Sessions are "days with SPOT prints in the corpus" — a Saturday special session is traded like any other. Live must use the NSE calendar the fleet already uses.
* **BANKNIFTY** untested (index-only runner supports it if `BANKNIFTYSPOT` rows exist).
* **Strike offset +1** untested.
* **Live/paper integration** not built: needs the 1m→5m resample + continuous ATR replayed from Kite candles with VET_V1 as donor; entry order at the 1m open after a rejection close; stop check on 1m closes; hold timer; the fleet's DTE skip via `dte_lot_mult`.

## 7. How to reproduce

```
# lab (standalone, reads the corpus directly)
cd tools/lab/fvg
python3 fvg_fib_backtest.py --lots 10 --months \
  --set direction=CE entry_until=11:00 sl_trigger=close fib_gate=0 disp_atr=1.5 \
        max_trades_per_day=1 tp_rr=8 skip_expiry_day=1

# app: Backtest page → FVG V1 → Run (defaults are the sealed config)
# parity: cd backend && PYTHONPATH=$PWD python3 app/backtest/fvg/test_fvg_runner_sim.py
```
