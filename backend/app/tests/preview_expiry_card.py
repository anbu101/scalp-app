#!/usr/bin/env python3
"""
LOCAL EXPIRY CARD PREVIEW — render the week + month expiry card from the REAL
database (or a synthetic fixture) and save it to a PNG. Sends nothing.

    cd backend
    python app/tests/preview_expiry_card.py                    # real DB, today's windows
    python app/tests/preview_expiry_card.py --out ~/Desktop/expiry.png
    python app/tests/preview_expiry_card.py --fixture fleet    # the mockup's numbers
    python app/tests/preview_expiry_card.py --fixture empty
    python app/tests/preview_expiry_card.py --fixture live

── EXPIRY_CARD_20260929 ── same data source + renderer the scheduler uses at
15:40 on expiry days, so this is exactly the card that will be sent. The real
DB path works on any day (the subtitle says "not an expiry day (manual)").
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(os.getcwd(), "expiry_card_preview.png"))
    parser.add_argument("--fixture", choices=("empty", "live", "fleet"), default=None,
                        help="Render a synthetic card instead of reading the real DB")
    args = parser.parse_args()

    try:
        from app.api.telegram_expiry_card import build_expiry_card_png, fixture_expiry_data, BOOKS
    except ModuleNotFoundError as e:
        print(f"[PREVIEW] import failed: {e}")
        print("[PREVIEW] Run from the backend root so `app.*` imports resolve (pip install matplotlib).")
        sys.exit(1)

    if args.fixture:
        print(f"[PREVIEW] Building synthetic data: fixture={args.fixture}")
        data = fixture_expiry_data(args.fixture)
    else:
        from app.api.telegram_expiry_data import build_expiry_card_data
        from app.api.telegram_expiry_send import is_expiry_day
        from datetime import date
        exp = is_expiry_day(date.today())
        print(f"[PREVIEW] Building from the real DB (today is {'an expiry day' if exp else 'not an expiry day'})...")
        data = build_expiry_card_data(expiry=bool(exp))

    print(f"[PREVIEW] {data.subtitle}")
    print(f"[PREVIEW] week : {data.week_range}")
    print(f"[PREVIEW] month: {data.month_range}")
    for period in ("week", "month"):
        for b in BOOKS:
            pb = data.book(period, b)
            cells = " ".join(f"{c.label}:{'—' if c.net is None else f'{c.net:+,.0f}'}" for c in pb.cells)
            print(f"[PREVIEW] {period:5} {b:5} net={pb.net:+,.0f} trades={pb.trades} wins={pb.wins}"
                  f" charges={pb.charges:,.0f}{' ≈' if pb.approx else ''}  | {cells}")
    print(f"[PREVIEW] rows: {len(data.rows)}")
    for r in data.rows:
        print(f"            {r.name:12} {r.mode:5} week={r.week_net:+,.0f} ({r.week_trades})"
              f"  month={r.month_net:+,.0f} ({r.month_trades}){' ≈' if r.approx else ''}")
    for w in data.warnings:
        print(f"[PREVIEW] warn: {w}")

    png = build_expiry_card_png(data)
    if not png:
        print("[PREVIEW] Render returned None (is matplotlib installed in this env?)")
        sys.exit(2)
    with open(args.out, "wb") as f:
        f.write(png)
    print(f"[PREVIEW] Saved: {args.out}  ({len(png):,} bytes)")
    try:
        if sys.platform == "darwin":
            os.system(f'open "{args.out}"')
        elif sys.platform.startswith("win"):
            os.startfile(args.out)  # type: ignore[attr-defined]
    except Exception:
        pass


if __name__ == "__main__":
    main()
