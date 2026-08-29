#!/usr/bin/env python3
# apply_pst_hedge_filters_ui_20260828.py
#
# ── PST_HEDGE_ENTRY_FILTERS_20260828 + PST_HEDGE_CONFIRM_20260828 ──
# frontend half. Backend half: apply_pst_hedge_filters_20260828.py.
# Applies ON TOP of the PST_SELL confirm UI patch, AFTER the EMA-gate UI
# revert (anchors match the post-revert state).
#
# APPROACH — SHARED CONTROLS, SEPARATE CONFIG (H1 + H5):
# The three controls (level chips, expiry skip, confirm minutes) already
# exist in the PST card, currently rendered only when isPSTSell. This patch
# widens the render guard to isPSTSell || isPSTHedge and widens the
# buildConfig emit the same way, so PST_HEDGE gets the identical knobs to
# play with from the UI.
#
# The VALUES stay separate per strategy because buildConfig is called with
# the CURRENT strategy id and the UI state is per-strategy in localStorage
# ONLY IF keyed — it is NOT. So this patch adds a SECOND state set
# (pstHedge*) rather than reusing the sell's, because the two strategies
# demonstrably want different level sets (S1: +Rs288k for the seller,
# -Rs94k for the hedge) and silently sharing one set of chips across a
# strategy switch would be the exact footgun H5 rules out.
#
# Gap sweep: describeConfig chips already read cfg.* (strategy-agnostic —
# they fire for whichever config carries the keys, so hedge runs get chips
# for free); RunComparison PARAM_KEYS gate on cfg.signal_tf which BOTH PST
# configs carry (also free); BacktestQueue tokens likewise. Only Backtest.jsx
# needs real work. SweepBuilder: the existing pst_confirm axis is widened to
# PSTH so the hedge confirm sweep can be queued.

import os

F_LVL = "PST_HEDGE_ENTRY_FILTERS_20260828"
F_CFM = "PST_HEDGE_CONFIRM_20260828"
PREV = "PST_SELL_CONFIRM_20260828"
REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
FRONT = os.environ.get("SCALP_FRONTEND", os.path.join(REPO, "frontend"))

BT = os.path.join(FRONT, "src", "pages", "Backtest.jsx")
SB = os.path.join(FRONT, "src", "pages", "backtest", "SweepBuilder.jsx")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def patch_backtest(src):
    if F_LVL in src:
        print("  Backtest.jsx: fence present — skipping (idempotent)")
        return src
    if PREV not in src:
        raise SystemExit(f"ABORT: Backtest.jsx missing prerequisite {PREV}.")
    if "PST_SELL_EMA_GATE_20260828" in src:
        raise SystemExit("ABORT: the falsified EMA-gate UI is still present. "
                         "Run revert_pst_sell_ema_gate_ui_20260828.py first.")

    # H1 — separate hedge state (NOT shared with the sell's chips)
    old = """  const [pstConfirmMin, setPstConfirmMin] = useState(Number(pstSaved.confirmMin) || 0);"""
    new = old + f"""
  // ── {F_LVL} / {F_CFM} ── PST_HEDGE gets its OWN copies of the three
  // knobs: the hedge holds the OPPOSITE contract, so its best level set
  // differs from the seller's (S1 is +Rs288k for PST_SELL, -Rs94k here).
  // Sharing one set of chips across a strategy switch would silently carry
  // the wrong filter into the other strategy.
  const [pstHAllowedLevels, setPstHAllowedLevels] = useState(Array.isArray(pstSaved.hAllowedLevels) ? pstSaved.hAllowedLevels : []);
  const [pstHSkipExpiry, setPstHSkipExpiry] = useState(!!pstSaved.hSkipExpiry);
  const [pstHConfirmMin, setPstHConfirmMin] = useState(Number(pstSaved.hConfirmMin) || 0);"""
    src = _ro(src, old, new, "H1 state")

    # H2 — LS payload
    old = "confirmMin: pstConfirmMin })"
    new = ("confirmMin: pstConfirmMin, hAllowedLevels: pstHAllowedLevels, "
           "hSkipExpiry: pstHSkipExpiry, hConfirmMin: pstHConfirmMin })")
    src = _ro(src, old, new, "H2 LS payload")

    # H3 — LS deps (stale-closure rule, same commit as H2)
    old = "pstAllowedLevels, pstSkipExpiry, pstConfirmMin]);"
    new = ("pstAllowedLevels, pstSkipExpiry, pstConfirmMin, "
           "pstHAllowedLevels, pstHSkipExpiry, pstHConfirmMin]);")
    src = _ro(src, old, new, "H3 LS deps")

    # H4 — buildConfig: PST_HEDGE branch emitting its OWN values
    old = """        ...(sid === "PST_SELL" ? { allowed_levels: pstAllowedLevels, skip_expiry_day: !!pstSkipExpiry, confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)) } : {}),   // ── PST_SELL_CONFIRM_20260828 ──"""
    new = """        ...(sid === "PST_SELL" ? { allowed_levels: pstAllowedLevels, skip_expiry_day: !!pstSkipExpiry, confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)) } : {}),   // ── PST_SELL_CONFIRM_20260828 ──
        // ── """ + F_LVL + """ / """ + F_CFM + """ ── same key NAMES on a
        // different strategy's config; the VALUES are the hedge's own.
        ...(sid === "PST_HEDGE" ? { allowed_levels: pstHAllowedLevels, skip_expiry_day: !!pstHSkipExpiry, confirm_minutes: Math.min(30, Math.max(0, Number(pstHConfirmMin) || 0)) } : {}),"""
    src = _ro(src, old, new, "H4 buildConfig")

    # H5 — buildConfig deps (stale-closure rule)
    old = """      pstConfirmMin,   // ── PST_SELL_CONFIRM_20260828 ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"""
    new = old + f"""
      pstHAllowedLevels, pstHSkipExpiry, pstHConfirmMin,   // ── {F_LVL} / {F_CFM} ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"""
    src = _ro(src, old, new, "H5 buildConfig deps")

    # H6 — render the control row for the HEDGE too, bound to hedge state.
    # The existing block stays isPSTSell-only; a sibling block is added so
    # neither strategy can read the other's state (no shared setters).
    old = """              {isPSTSell && (
                /* ── PST_SELL_ENTRY_FILTERS_20260828 ── entry filters from the 2020-2026"""
    new = """              {isPSTHedge && (
                /* ── """ + F_LVL + """ / """ + F_CFM + """ ── the same three
                   knobs as PST_SELL, bound to the HEDGE's own state. Level
                   evidence differs: PP +Rs456k, R3 +Rs71k, S3 +Rs63k are the
                   payers here, while S1 is -Rs94k (it pays the seller, not
                   the opposite-side holder). Expiry day is -Rs784k over 740
                   trades. Start from the UI and sweep. */
                <div style={{ display: "flex", gap: spacing.md, flexWrap: "wrap", alignItems: "flex-end", marginBottom: spacing.md }}>
                  <Field label="Entry levels (none = all)">
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {["S3", "S2", "S1", "PP", "R1", "R2", "R3"].map((lv) => {
                        const on = pstHAllowedLevels.includes(lv);
                        return (
                          <button
                            key={lv}
                            type="button"
                            onClick={() => setPstHAllowedLevels((prev) => (prev.includes(lv) ? prev.filter((x) => x !== lv) : [...prev, lv]))}
                            title={on ? `${lv} allowed — click to drop` : `${lv} blocked — click to allow`}
                            style={{ ...inputStyle, width: "auto", padding: "4px 10px", cursor: "pointer", opacity: on ? 1 : 0.4, fontWeight: on ? 700 : 400 }}
                          >
                            {lv}
                          </button>
                        );
                      })}
                    </div>
                  </Field>
                  <Field label="Expiry day">
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, cursor: "pointer" }}>
                      <input type="checkbox" checked={!!pstHSkipExpiry} onChange={(e) => setPstHSkipExpiry(e.target.checked)} />
                      skip whole day
                    </label>
                  </Field>
                  <Field label="Confirm (min, 0=off)">
                    <input
                      type="number" min="0" max="30" step="1"
                      style={{ ...inputStyle, width: 80 }}
                      value={pstHConfirmMin}
                      onChange={(e) => setPstHConfirmMin(Math.min(30, Math.max(0, Number(e.target.value) || 0)))}
                      title="wait N minutes after the signal; abort if spot touches the would-be SPOT_SL level during the wait"
                    />
                  </Field>
                </div>
              )}
              {isPSTSell && (
                /* ── PST_SELL_ENTRY_FILTERS_20260828 ── entry filters from the 2020-2026"""
    src = _ro(src, old, new, "H6 UI")
    return src


def patch_sweepbuilder(src):
    if F_CFM in src:
        print("  SweepBuilder.jsx: fence present — skipping (idempotent)")
        return src
    # widen the confirm axis to PST_HEDGE and add a level-free numeric axis
    old = """  { key: "pst_confirm", label: "Confirm wait (min)", strategies: [PSTS],
    hint: "1, 2, 3, 5", parse: _num,
    apply: (c, v) => { c.confirm_minutes = Math.min(30, Math.max(0, v)); }, fmt: (v) => `cfm${v}m` },   // ── PST_SELL_CONFIRM_20260828 ──"""
    new = """  { key: "pst_confirm", label: "Confirm wait (min)", strategies: [PSTS, PSTH],
    hint: "1, 2, 3, 5", parse: _num,
    apply: (c, v) => { c.confirm_minutes = Math.min(30, Math.max(0, v)); }, fmt: (v) => `cfm${v}m` },   // ── PST_SELL_CONFIRM_20260828 / """ + F_CFM + """ ── same key on both PST configs"""
    return _ro(src, old, new, "SB confirm axis")


def main():
    for p in (BT, SB):
        if not os.path.isfile(p):
            raise SystemExit(f"ABORT: missing {p} — set SCALP_REPO/SCALP_FRONTEND.")
    bt, sb = open(BT).read(), open(SB).read()
    bt2, sb2 = patch_backtest(bt), patch_sweepbuilder(sb)
    # post-condition: the sell's own state must not have been rebound
    for probe in ("setPstAllowedLevels((prev)", "value={pstConfirmMin}"):
        if probe not in bt2:
            raise SystemExit(f"ABORT: sell control '{probe}' lost. No files written.")
    for path, cur, new in ((BT, bt, bt2), (SB, sb, sb2)):
        if new != cur:
            open(path, "w").write(new)
            print(f"  wrote {path}")
    print("DONE —", F_LVL, "+", F_CFM, "(frontend)")


if __name__ == "__main__":
    main()
