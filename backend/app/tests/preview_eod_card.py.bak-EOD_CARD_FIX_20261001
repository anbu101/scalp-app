#!/usr/bin/env python3
"""
LOCAL EOD CARD PREVIEW — render the daily summary card from the REAL database
and save it to a PNG you can open. Does NOT send anything to Telegram.

Run from the backend root (same place the app runs, so app.* imports resolve
and get_conn() points at the real SQLite):

    python app/tests/preview_eod_card.py
    python app/tests/preview_eod_card.py --out ~/Desktop/card.png

── EOD_CARD_V2_20260929 ── synthetic days, no DB needed:

    python app/tests/preview_eod_card.py --fixture fleet    # 21 Sep 2026's numbers
    python app/tests/preview_eod_card.py --fixture empty    # no trades, no samples
    python app/tests/preview_eod_card.py --fixture one      # a single live trade
    python app/tests/preview_eod_card.py --fixture big      # 14 tiles, six-figure loser

If today has no closed trades yet, the real-DB card renders with "no trades
today" — that's correct, not an error. To preview with realistic numbers, run
it after a trading day, or use --fixture.

This imports the SAME data source + renderer the scheduler uses, so what you
see here is exactly what tomorrow's 15:30 card will look like.
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=os.path.join(os.getcwd(), "eod_card_preview.png"),
        help="Output PNG path (default: ./eod_card_preview.png)",
    )
    parser.add_argument(
        "--fixture", choices=("empty", "one", "fleet", "big"), default=None,
        help="Render a synthetic day instead of reading the real DB",
    )
    args = parser.parse_args()

    # Import the real pipeline. These must resolve from the backend root.
    try:
        from app.api.telegram_summary_card import build_summary_card_png, fixture_card_data
    except ModuleNotFoundError as e:
        print(f"[PREVIEW] import failed: {e}")
        print("[PREVIEW] Run this from the backend root so `app.*` imports resolve,")
        print("[PREVIEW] and ensure matplotlib is installed in this Python env:")
        print("[PREVIEW]   pip install matplotlib")
        sys.exit(1)

    if args.fixture:
        print(f"[PREVIEW] Building synthetic card data: fixture={args.fixture}")
        data = fixture_card_data(args.fixture)
    else:
        try:
            from app.api.telegram_summary_data import build_card_data
        except ModuleNotFoundError as e:
            print(f"[PREVIEW] import failed: {e}")
            sys.exit(1)
        print("[PREVIEW] Building card data from the real DB...")
        data = build_card_data()

    # Show what was found, so an empty card is understood not feared.
    print(f"[PREVIEW] {data.subtitle or data.date_str}")
    print(f"[PREVIEW] LIVE rows : {len(data.live_rows)}")
    for r in data.live_rows:
        print(f"            {r.name:12} {r.trades}tr  {r.wins}/{r.losses}  net={r.net:+,.2f}"
              f"  charges={r.charges:,.0f}  best={r.best}  worst={r.worst}"
              f"  exits tp/sl/other={r.exit_tp}/{r.exit_sl}/{r.exit_other}")
    print(f"[PREVIEW] PAPER rows: {len(data.paper_rows)}")
    for r in data.paper_rows:
        print(f"            {r.name:12} {r.trades}tr  {r.wins}/{r.losses}  net={r.net:+,.2f}"
              f"  charges={r.charges:,.0f}  best={r.best}  worst={r.worst}"
              f"  exits tp/sl/other={r.exit_tp}/{r.exit_sl}/{r.exit_other}")
    print(f"[PREVIEW] Live net  : {data.live_subtotal:+,.2f}   Paper net: {data.paper_subtotal:+,.2f}")
    for book, path in sorted((data.mtm_paths or {}).items()):
        print(f"[PREVIEW] MTM path {book}: {len(path)} samples"
              + (f", {path[0][0]//60:02d}:{path[0][0]%60:02d} → {path[-1][0]//60:02d}:{path[-1][0]%60:02d},"
                 f" last {path[-1][1]:+,.0f}" if path else ""))
    if not data.mtm_paths:
        print("[PREVIEW] MTM path : none (fleet_mtm_samples has no rows for today)")

    print("[PREVIEW] Rendering PNG...")
    png = build_summary_card_png(data)
    if not png:
        print("[PREVIEW] Render returned None.")
        print("[PREVIEW] Most likely matplotlib is not installed in THIS Python env.")
        print("[PREVIEW]   pip install matplotlib")
        sys.exit(2)

    with open(args.out, "wb") as f:
        f.write(png)
    print(f"[PREVIEW] Saved: {args.out}  ({len(png):,} bytes)")
    print("[PREVIEW] Open it to see exactly what tomorrow's 15:30 card will look like.")

    # Best-effort: open it automatically on macOS / Windows.
    try:
        if sys.platform == "darwin":
            os.system(f'open "{args.out}"')
        elif sys.platform.startswith("win"):
            os.startfile(args.out)  # type: ignore[attr-defined]
    except Exception:
        pass


if __name__ == "__main__":
    main()
