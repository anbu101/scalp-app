#!/usr/bin/env python3
# apply_pst_live_filters_ui_20260828.py
#
# ── PST_LIVE_FILTERS_20260828 ── Settings UI (paper/live). Backend half:
# apply_pst_live_filters_20260828.py.
#
# Adds to BOTH the PST Sell and PST Hedge settings cards:
#   * ENTRY LEVELS  — 7 toggle chips (S3..R3); none selected = all allowed
#   * SKIP EXPIRY DAY — checkbox
#   * CONFIRM (MIN)  — number input, 0 = off
#
# These write straight onto the live strategy config (allowed_levels,
# skip_expiry_day, confirm_minutes) via the existing updatePstSell /
# updatePstHedge path, so they persist through saveStrategyConfig and are
# read FRESH per signal by the managers' _cfg_snapshot.
#
# The sealed values are shown as helper text under each card so the sealed
# config is reproducible from the UI without opening this document:
#   PST_SELL  levels PP+S1+S3+R3 · skip expiry ON · confirm 4
#   PST_HEDGE levels PP+R3       · skip expiry ON · confirm 3
#
# DEFENSIVE READS: a config saved before this patch has none of the three
# keys, so every read is guarded (`?? []`, `|| 0`, `!!`). No migration is
# required and an un-migrated config behaves exactly as today.

import os

FENCE = "PST_LIVE_FILTERS_20260828"
REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
FRONT = os.environ.get("SCALP_FRONTEND", os.path.join(REPO, "frontend"))
SETTINGS = os.path.join(FRONT, "src", "pages", "Settings.jsx")

INPUT_STYLE = ('{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", '
               'background: "#141821", color: "#e5e9f0", fontSize: 13 }')
LBL = ('{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, '
       'color: "#8b93a7" }')


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def block(cfgvar, updfn, sealed):
    """The three-control row for one strategy card."""
    return f'''
        {{/* ── {FENCE} ── sealed entry filters, same keys the backtest uses.
            Reads are guarded: a config saved before this patch has none of
            these keys and must behave exactly as before. */}}
        <div style={{{{ display: "grid", gridTemplateColumns: "1fr", gap: 8, marginBottom: 12,
                     padding: 10, borderRadius: 8, border: "1px solid #2a3040", background: "#10141c" }}}}>
          <div style={{{{ fontSize: 11, color: "#8b93a7", letterSpacing: 0.4 }}}}>
            ENTRY FILTERS <span style={{{{ color: "#5c6672" }}}}>· sealed: {sealed}</span>
          </div>
          <div style={{{{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-end" }}}}>
            <label style={{{LBL}}}>LEVELS (none = all)
              <div style={{{{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 2 }}}}>
                {{["S3", "S2", "S1", "PP", "R1", "R2", "R3"].map((lv) => {{
                  const cur = Array.isArray({cfgvar}.allowed_levels) ? {cfgvar}.allowed_levels : [];
                  const on = cur.includes(lv);
                  return (
                    <button key={{lv}} type="button"
                      onClick={{() => {updfn}(["allowed_levels"], on ? cur.filter((x) => x !== lv) : [...cur, lv])}}
                      title={{on ? `${{lv}} allowed — click to block` : `${{lv}} blocked — click to allow`}}
                      style={{{{ padding: "5px 9px", borderRadius: 6, border: "1px solid #2a3040",
                               background: on ? "#1d3a2b" : "#141821", color: on ? "#4ade80" : "#5c6672",
                               fontSize: 12, fontWeight: on ? 700 : 400, cursor: "pointer" }}}}>
                      {{lv}}
                    </button>
                  );
                }})}}
              </div>
            </label>
            <label style={{{LBL}}}>EXPIRY DAY
              <label style={{{{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "#e5e9f0", cursor: "pointer", padding: "7px 0" }}}}>
                <input type="checkbox" checked={{!!{cfgvar}.skip_expiry_day}}
                  onChange={{(e) => {updfn}(["skip_expiry_day"], e.target.checked)}} />
                skip whole day
              </label>
            </label>
            <label style={{{LBL}}}>CONFIRM (MIN, 0=OFF)
              <input type="number" min="0" max="30" step="1"
                value={{Number({cfgvar}.confirm_minutes) || 0}}
                onChange={{(e) => {updfn}(["confirm_minutes"], Math.min(30, Math.max(0, Number(e.target.value) || 0)))}}
                title="wait N minutes after the signal; abandon the entry if spot touches the would-be SPOT_SL level during the wait"
                style={{{INPUT_STYLE}}} />
            </label>
          </div>
        </div>'''


def patch(src):
    if FENCE in src:
        print("  Settings.jsx: fence present — skipping (idempotent)")
        return src

    for who, cfgvar, updfn, sealed in (
            ("Sell", "pstSellConfig", "updatePstSell",
             "levels PP+S1+S3+R3 · skip expiry ON · confirm 4"),
            ("Hedge", "pstHedgeConfig", "updatePstHedge",
             "levels PP+R3 · skip expiry ON · confirm 3")):
        anchor = f'''          <label style={{{{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "#8b93a7" }}}}>EXIT (EOD)
            <input type="text" value={{{cfgvar}.exit_time}} onChange={{(e) => {updfn}(["exit_time"], e.target.value)}}
              style={{{{ padding: "7px 10px", borderRadius: 6, border: "1px solid #2a3040", background: "#141821", color: "#e5e9f0", fontSize: 13 }}}} />
          </label>
        </div>'''
        src = _ro(src, anchor, anchor + block(cfgvar, updfn, sealed),
                  f"UI {who}")
    return src


def main():
    if not os.path.isfile(SETTINGS):
        raise SystemExit(f"ABORT: missing {SETTINGS} — set SCALP_REPO/SCALP_FRONTEND.")
    cur = open(SETTINGS).read()
    new = patch(cur)
    # post-conditions
    if new != cur:
        for probe in ("pstSellConfig.allowed_levels", "pstHedgeConfig.allowed_levels",
                      "pstSellConfig.skip_expiry_day", "pstHedgeConfig.skip_expiry_day",
                      "pstSellConfig.confirm_minutes", "pstHedgeConfig.confirm_minutes"):
            if probe not in new:
                raise SystemExit(f"ABORT: '{probe}' missing after patch. "
                                 f"No files written.")
        open(SETTINGS, "w").write(new)
        print(f"  wrote {SETTINGS}")
    print("DONE —", FENCE, "(Settings UI)")


if __name__ == "__main__":
    main()
