#!/usr/bin/env python3
# license_server/ic_split_entitlement_audit.py
#
# ── IC_SPLIT (2026-08-04) ── PRE-DEPLOY license audit.
# ============================================================================
# WHY THIS EXISTS
# `license_allows_strategy()` (backend) and `allowsStrategy()` (frontend)
# both match the strategy id LITERALLY against entitlements.strategies.
# After the rename, the strategy that used to trade as "IC_V1" trades as
# "IC_V2" — so any non-admin license that enumerates "IC_V1" explicitly
# will:
#     * NOT launch the IC_V2 runtime (the one carrying their tuned config
#       and their previous execution mode), and
#     * still "allow" IC_V1 — which after migration boots OFF on legacy
#       defaults.
# Net effect for that user: their iron condor silently stops trading.
# Admin licenses carry ["*"] and are unaffected.
#
# ORDERING: run --apply on the license server BEFORE distributing the
# build. Entitlements are read at app boot and on the 6h heartbeat, so
# patching first means the new build finds the right list already there.
#
# USAGE (on the license server host, next to db.py):
#     python3 ic_split_entitlement_audit.py            # dry run, report only
#     python3 ic_split_entitlement_audit.py --apply    # patch + backup
#
# WHAT --apply DOES: for every license whose strategies list contains
# "IC_V1" and not "IC_V2", it ADDS "IC_V2" (keeping IC_V1 — the reborn
# legacy condor is a real strategy they may want). Nothing else is
# touched; the DB file is copied to a timestamped .bak first.
# ============================================================================

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_CANDIDATES = [
    Path(__file__).parent / "licenses.db",
    Path.home() / "scalp-license" / "licenses.db",
    Path("/opt/scalp-license/licenses.db"),
]


def _find_db(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            sys.exit(f"DB not found: {p}")
        return p
    for p in DB_CANDIDATES:
        if p.exists():
            return p
    sys.exit("licenses.db not found — pass --db /path/to/licenses.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="write the patch (default is a dry-run report)")
    args = ap.parse_args()

    db = _find_db(args.db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT key, label, tier, entitlements_json FROM licenses"
    ).fetchall()

    affected, admin, already, no_ic = [], [], [], []
    for r in rows:
        try:
            ent = json.loads(r["entitlements_json"])
        except Exception:
            print(f"  !! UNPARSEABLE entitlements for {r['key']} — skipped")
            continue
        strategies = ent.get("strategies") or []
        if "*" in strategies:
            admin.append(r)
        elif "IC_V1" in strategies and "IC_V2" not in strategies:
            affected.append((r, ent, strategies))
        elif "IC_V2" in strategies:
            already.append(r)
        else:
            no_ic.append(r)

    print(f"\nLicense DB: {db}")
    print(f"  total licenses          : {len(rows)}")
    print(f"  admin (\"*\") — no action : {len(admin)}")
    print(f"  no IC entitlement       : {len(no_ic)}")
    print(f"  already has IC_V2       : {len(already)}")
    print(f"  NEEDS PATCH             : {len(affected)}")

    if not affected:
        print("\nNothing to do — safe to distribute the build.\n")
        return

    print("\nLicenses that would lose their iron condor:")
    for r, _ent, strategies in affected:
        print(f"  {r['key']}  [{r['tier']}]  {r['label']}")
        print(f"      strategies: {strategies}")

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to patch (a .bak is taken "
              "automatically).\n")
        return

    bak = db.with_suffix(
        db.suffix + f".pre_ic_split.{datetime.now():%Y%m%d_%H%M%S}.bak")
    shutil.copy2(db, bak)
    print(f"\nBackup written: {bak}")

    for r, ent, strategies in affected:
        ent["strategies"] = strategies + ["IC_V2"]
        conn.execute(
            "UPDATE licenses SET entitlements_json = ? WHERE key = ?",
            (json.dumps(ent), r["key"]),
        )
        print(f"  patched {r['key']} → {ent['strategies']}")
    conn.commit()
    print(f"\nDone: {len(affected)} license(s) patched. Users pick this up "
          f"at their next heartbeat (<=6h) or app restart.\n")


if __name__ == "__main__":
    main()
