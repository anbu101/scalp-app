# backend/app/config/lots_whitelist.py
"""
SINGLE SOURCE for the friend-owned (non-admin editable) config paths.

Consumed by BOTH:
  - app.routes.config_routes   (UI_MASK: GET masking + save whitelist-merge)
  - app.license.config_override_applier  (CFG_OVERRIDE D2: snapshot/reinstate)

Deliberately imports NOTHING — the license applier must be able to load
this without dragging in FastAPI/broker machinery (a config_routes import
here once pulled kiteconnect into the license path; never again).

Dotted paths; list indices are numeric segments ("legs.0.lots").
Frontend mirror: src/pages/LotsOnlySettings.jsx LOTS_FIELDS — verify both
together on any change.
"""

LOTS_PATHS = {
    "SCALP_V1":  ["quantity.lots"],
    "SCALP_V3":  ["quantity.lots"],
    "SCALP_V5":  ["quantity.lots"],
    "HA_V1":     ["quantity.lots"],
    "BB_V1":     ["lots"],                      # leg split derived in config_routes
    "BB_V2":     ["ce_lots", "pe_lots"],
    # ── TSG_EXPIRY_LOTS 20260819 ── expiry_lots is now friend-editable.
    # It is a LOTS path, so _clamp_lots() applies the licence max_lots
    # cap to it exactly like every other entry (non-admin ceiling 5).
    # 0/blank keeps the LD5 meaning: fall back to `lots`.
    "TSG_V1":    ["lots", "expiry_lots"],
    "TMA_V1":    ["c1.sell.lots", "c1.buy.lots"],
    "TMA_V2":    ["s1.main.lots", "s1.hedge.lots"],
    # ── VET_V1 2026-08-29 ── one lots path drives BOTH legs (the wing,
    # when selling, always matches the main leg size).
    "VET_V1":    ["quantity.lots"],
    "PST_SELL":  ["legs.0.lots", "legs.1.lots"],
    "PST_HEDGE": ["legs.0.lots", "legs.1.lots"],
    # ── IC_SPLIT ── IC_V1 is the legacy EOD condor: NO adjustment legs,
    # so no adjust.* lots paths. IC_V2 keeps the full set.
    "IC_V1":     ["legs.0.lots", "legs.1.lots", "legs.2.lots", "legs.3.lots"],
    "IC_V2":     ["legs.0.lots", "legs.1.lots", "legs.2.lots", "legs.3.lots",
                  "adjust.L1.lots", "adjust.L2.lots"],
}