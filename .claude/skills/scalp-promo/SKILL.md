---
name: scalp-promo
description: Make a promo, launch video, trailer, Reel or WhatsApp Status clip for Scalp Terminal from the REAL app components with demo data, using marketing/promo. Use for "promo video", "trailer", "reel", "brag video", "launch video", "teaser".
---

# scalp-promo — promo videos and trailers for Scalp Terminal

<!-- fence: PROMO_TOOLKIT_20261115 -->

The toolkit lives in `marketing/promo/`. It renders the app's **real** React components
(`FleetKpis`, `FleetChart`, `FleetBlotter`, `MarketBadge`, the theme tokens in `index.css`),
plus the real Telegram EOD card from the backend renderer. It feeds them **fictional demo data**
and captures every frame as a pure function of time `t`. The music is synthesised in Python on a
beat grid, so there are no licensed assets.

```
marketing/promo/
  promo.sh                 one command: build → music → render → mp4 + poster
  build.mjs                esbuild; @app → ../../frontend/src (the real components)
  src/lib/common.jsx       PhoneDash (real phone layout), Phone frame, Grain, DemoTag, FEES, easing helpers
  src/lib/demoData.js      fictional fleet day shaped like GET /api/fleet/today (fleetAt(minute))
  src/lib/runtime.jsx      mount(Video, VIDEO_META) → window.renderAt(t), window.VIDEO
  src/videos/*.jsx         one file per video (templates below)
  music/<video>.py         soundtrack per video (synthlib.py = shared synth helpers)
  scripts/render.py        Playwright frame capture + ffmpeg encode, or --stills for checks
  scripts/make_eod_card.py real EOD card (backend/app/api/telegram_summary_card.py), fictional day
  out/<video>/             <video>.mp4, <video>.jpg (poster), stills/ (git-ignored)
```

## Templates (copy the closest one)

| Video | Format | Look | Use for |
|---|---|---|---|
| `promo-landscape` | 1920×1080, 20 s | Dark theme, real desktop dashboard in a window, camera push-ins, spotlight, cursor clicks, Telegram card | X / LinkedIn / YouTube |
| `trailer-prestige` | 1080×1920, 24 s | Black and gold, Cinzel title cards, letterbox, grain, dips to black, braams, a trailer silence before the title | Reels / Status, serious tone |
| `trailer-mass` | 1080×1920, 20 s | The app's **Thalapathy** theme (crimson + gold rays), Bebas Neue slams, screen shake, flashes, confetti, the app's own target-hit whistle | Reels / Status, high energy (Anbu's favourite) |

## Workflow

1. **Ask only what changes the build:** the format (landscape or vertical), the tone or template, the
   message or feature to push, and the length. Otherwise default to `trailer-mass` at about 20 s.
2. **Get the latest code** (`git status` first. Components change: re-read `components/fleet/*`,
   `pages/ModernDashboard.jsx`, `strategies/displayNames.js` and `index.css` before reusing them).
3. **Verify every claim against the code before it goes on screen** (see Accuracy below).
4. **Write a short plan:** hook (first 2 s), beats, on-screen text, and the cut times on the beat grid
   (120 BPM = 0.5 s beats). Text must stay on screen ≥ 0.3 s per word after it settles.
5. **Make the video:** `cp src/videos/<template>.jsx src/videos/<new>.jsx` and
   `cp music/<template>.py music/<new>.py`. Edit the scenes and set `VIDEO_META`
   `{ width, height, duration, theme, poster, warmup }`. Keep music hits on the same times as the visual slams.
6. **Check stills before rendering:** `marketing/promo/promo.sh <new> --stills 0.5 3 7.2 12.5 …`. Then look
   at `out/<new>/stills/contact.png`, including mid-transition times. Fix overflow, collisions, contrast
   and wrapped headlines.
7. **Redaction check:** `marketing/promo/promo.sh <new> --check` samples every 0.5 s and fails if a
   real strategy id is on screen, or if an app-UI frame lacks the demo-data tag. It must pass before you render.
8. **Render:** `marketing/promo/promo.sh <new>` writes `out/<new>/<new>.mp4` (H.264, AAC). The poster frame
   is baked in as frame 0, and `<new>.jpg` is the cover.
9. **Deliver:** the mp4, the jpg and a 1–3 line caption with hashtags (always include
   `#CompoundingInTrading` when compounding is mentioned). Offer to redo a scene or try another tone.

Requirements on the machine: `node` 18+, `python3` with `playwright numpy scipy` (and
`playwright install chromium` once, or set `PROMO_CHROME=/path/to/chrome`), `ffmpeg`, and
matplotlib for the EOD card (the backend env has it). Ask before installing anything.

## Rules (non-negotiable)

- **Codenames only.** Render with `isAdminUi = false` (the components then show Timberwolf, Icarus,
  Tigris, Velvet, Outrider…). Never show strategy ids, configs, stops, strikes, real account ids
  or emails. The Stop column stays empty in demo data.
- **Demo data only.** Never feed real P&L, real backtest results or real account balances. Every scene
  showing app UI carries `<DemoTag/>` ("App UI: illustrative demo data").
- **No return claims.** No backtest net, CAGR or "₹X in Y years". Compounding is shown as *lot sizing*
  (an illustrative lot ladder labelled "illustrative"), never as an equity curve.
- **End card disclaimer:** "Backtests use historical data and don't guarantee future results."
  (Promoting algo performance to the public can raise SEBI issues. Keep the claims factual.)

## Accuracy — what is true today (re-verify, don't trust this list blindly)

- **Backtest data:** NIFTY 1-minute options from Aug 2020 (`backtest.db`, spot from Jan 2020). That
  is "6+ years". Check `corpus_meta` or the Backfill inventory if the claim changes.
- **Fees:** every backtest trade is charged by `backend/app/backtest/charges/charges_model.py`
  (LOCKED v4, Zerodha published rates). It covers brokerage ₹20/order, STT 0.15% sell leg,
  NSE exchange 0.03553%, SEBI ₹10/crore, stamp 0.003% buy leg and GST 18%. `FEES` in common.jsx
  mirrors it; update both together. **Slippage is not modelled**, so never claim it.
- **Compounding** (Backtest → Lot compounding): calendar-step or equity-based lots, max lots cap,
  drawdown guard (hard or proportional), tax on profits withdrawn each FY.
- **Live + Paper** are separate books, never summed (Modern dashboard). The EOD card goes to Telegram
  at the end of the session.

## Gotchas (learned the hard way)

- `background-clip: text` with gradient stops beyond 100% renders as a **solid block** in Chrome. Animate
  a shimmer with `backgroundSize: "300% 100%"` + `backgroundPosition`, and keep the gradient on an
  inner `inline-block` span with the `filter` on the parent.
- `FleetChart` reads its Fleet/Grid view and overlays from **localStorage at mount**. Set the keys
  right before the instance mounts (the landscape template resets to "fleet" in `DashScenes` and
  flips to "grid" only in the outro).
- The chart width comes from a ResizeObserver, and the cursor/scroll targets are measured from layout.
  `renderAt` therefore renders twice per frame, and the renderer warms up at `VIDEO_META.warmup`
  times, then reloads the page.
- Frames are rendered **in order** in one page, and templates may rely on state measured in earlier
  scenes (`ROWS` in trailer-mass). Stills jump around, so add warm-up times that cover those scenes.
- The Thalapathy theme animates `h1`. Don't use `h1`–`h3` in videos, or frames stop being a pure
  function of `t`. Same for any CSS animation or transition: drive everything from `t`.
- Bebas Neue at 330 px overflows 1080 px at about 9 characters. Check wrapped headlines in stills.
- Keep the music integrated loudness around −11 to −14 LUFS (`ffmpeg -af ebur128`). Brickwall drive
  above 1.2 makes it squash.
- Never rebuild a component by hand when the real one exists. Only the window chrome, the nav bar
  and the phone frame are hand-drawn.
