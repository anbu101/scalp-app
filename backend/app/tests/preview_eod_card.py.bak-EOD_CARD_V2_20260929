#!/usr/bin/env python3
"""
LOCAL EOD CARD PREVIEW — render the daily summary card from the REAL database
and save it to a PNG you can open. Does NOT send anything to Telegram.

Run from the backend root (same place the app runs, so app.* imports resolve
and get_conn() points at the real SQLite):

    python preview_eod_card.py
    python preview_eod_card.py --out ~/Desktop/card.png

If today has no closed trades yet, the card renders with "No trades today"
sections — that's correct, not an error. To preview with realistic numbers,
run it after a trading day, or temporarily point at a DB that has rows.

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
    args = parser.parse_args()

    # Import the real pipeline. These must resolve from the backend root.
    try:
        from app.api.telegram_summary_data import build_card_data
        from app.api.telegram_summary_card import build_summary_card_png
    except ModuleNotFoundError as e:
        print(f"[PREVIEW] import failed: {e}")
        print("[PREVIEW] Run this from the backend root so `app.*` imports resolve,")
        print("[PREVIEW] and ensure matplotlib is installed in this Python env:")
        print("[PREVIEW]   pip install matplotlib")
        sys.exit(1)

    print("[PREVIEW] Building card data from the real DB...")
    data = build_card_data()

    # Show what was found, so an empty card is understood not feared.
    print(f"[PREVIEW] LIVE rows : {len(data.live_rows)}")
    for r in data.live_rows:
        print(f"            {r.name:12} {r.trades}tr  {r.wins}/{r.losses}  net={r.net:+,.2f}")
    print(f"[PREVIEW] PAPER rows: {len(data.paper_rows)}")
    for r in data.paper_rows:
        print(f"            {r.name:12} {r.trades}tr  {r.wins}/{r.losses}  net={r.net:+,.2f}")
    print(f"[PREVIEW] Combined  : {data.combined:+,.2f}")

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