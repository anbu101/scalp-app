#!/usr/bin/env python3
# apply_pst_sell_confirm_ui_20260828.py
#
# ── PST_SELL_CONFIRM_20260828 ── frontend half. Backend half:
# apply_pst_sell_confirm_20260828.py. Applies ON TOP of the
# PST_SELL_ENTRY_FILTERS_20260828 UI patch (anchors reference it).
#
# Adds "Confirm (min, 0=off)" number input to the PST_SELL filter row →
# config key confirm_minutes. Gap sweep: describeConfig chip (covers
# Portfolio via prop), RunComparison PARAM_KEYS row, BacktestQueue token.
# Stale-closure rule: buildConfig reads pstConfirmMin, so LS deps + the
# buildConfig dep array gain it in this SAME change. SweepBuilder: a
# numeric axis for confirm_minutes IS added this time (1..N sweeps are the
# whole point of making it configurable) — appended to the PST_SELL axes.

import os
import sys

FENCE = "PST_SELL_CONFIRM_20260828"
PREV = "PST_SELL_ENTRY_FILTERS_20260828"
REPO = os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app")
FRONT = os.environ.get("SCALP_FRONTEND", os.path.join(REPO, "frontend"))

BT = os.path.join(FRONT, "src", "pages", "Backtest.jsx")
RC = os.path.join(FRONT, "src", "pages", "backtest", "RunComparison.jsx")
BQ = os.path.join(FRONT, "src", "pages", "backtest", "BacktestQueue.jsx")
SB = os.path.join(FRONT, "src", "pages", "backtest", "SweepBuilder.jsx")


def _ro(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{tag}]: anchor found {n}x (need exactly 1). "
                         f"No files written.")
    return src.replace(old, new, 1)


def patch_backtest(src):
    if FENCE in src:
        print("  Backtest.jsx: fence present — skipping (idempotent)")
        return src
    if PREV not in src:
        raise SystemExit(f"ABORT: Backtest.jsx missing prerequisite {PREV}.")

    # B1 — state
    old = """  const [pstSkipExpiry, setPstSkipExpiry] = useState(!!pstSaved.skipExpiry);"""
    new = old + f"""
  // ── {FENCE} ── N-minute delayed entry with SL-touch abort (0 = off)
  const [pstConfirmMin, setPstConfirmMin] = useState(Number(pstSaved.confirmMin) || 0);"""
    src = _ro(src, old, new, "B1 state")

    # B2 — LS payload
    old = "allowedLevels: pstAllowedLevels, skipExpiry: pstSkipExpiry })"
    new = ("allowedLevels: pstAllowedLevels, skipExpiry: pstSkipExpiry, "
           "confirmMin: pstConfirmMin })")
    src = _ro(src, old, new, "B2 LS payload")

    # B3 — LS deps (stale-closure rule: same commit as B2)
    old = "pstAllowedLevels, pstSkipExpiry]);"
    new = "pstAllowedLevels, pstSkipExpiry, pstConfirmMin]);"
    src = _ro(src, old, new, "B3 LS deps")

    # B4 — buildConfig spread gains confirm_minutes (PST_SELL only)
    old = """        ...(sid === "PST_SELL" ? { allowed_levels: pstAllowedLevels, skip_expiry_day: !!pstSkipExpiry } : {}),"""
    new = """        ...(sid === "PST_SELL" ? { allowed_levels: pstAllowedLevels, skip_expiry_day: !!pstSkipExpiry, confirm_minutes: Math.min(30, Math.max(0, Number(pstConfirmMin) || 0)) } : {}),   // ── """ + FENCE + """ ──"""
    src = _ro(src, old, new, "B4 buildConfig")

    # B5 — buildConfig dep array (stale-closure rule)
    old = """      pstAllowedLevels, pstSkipExpiry,   // ── PST_SELL_ENTRY_FILTERS_20260828 ── stale-closure rule: buildConfig reads them, so they land here in the SAME commit"""
    new = old + f"""
      pstConfirmMin,   // ── {FENCE} ── stale-closure rule: buildConfig reads it, so it lands here in the SAME commit"""
    src = _ro(src, old, new, "B5 buildConfig deps")

    # B6 — describeConfig chip
    old = """    if (cfg.skip_expiry_day) add("ExpDay", "skip");   // ── PST_SELL_ENTRY_FILTERS_20260828 ──"""
    new = old + f"""
    if (Number(cfg.confirm_minutes) > 0) add("Confirm", `${{cfg.confirm_minutes}}m wait`);   // ── {FENCE} ──"""
    src = _ro(src, old, new, "B6 chip")

    # B7 — UI field after the expiry-day Field
    old = """                  <Field label="Expiry day">
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, cursor: "pointer" }}>
                      <input type="checkbox" checked={!!pstSkipExpiry} onChange={(e) => setPstSkipExpiry(e.target.checked)} />
                      skip whole day
                    </label>
                  </Field>"""
    new = old + """
                  {/* ── """ + FENCE + """ ── delayed entry with SL-touch
                      abort: wait N min after the signal; if spot touches the
                      signal-anchored SL level, skip the trade (53.8% of
                      SPOT_SLs died ≤10min; median SL 9min vs TP 46min).
                      NOT level-hold confirmation — spot falling back through
                      the level is the TP path and never aborts. */}
                  <Field label="Confirm (min, 0=off)">
                    <input
                      type="number" min="0" max="30" step="1"
                      style={{ ...inputStyle, width: 80 }}
                      value={pstConfirmMin}
                      onChange={(e) => setPstConfirmMin(Math.min(30, Math.max(0, Number(e.target.value) || 0)))}
                      title="wait N minutes after the signal; abort if spot touches the would-be SL level during the wait"
                    />
                  </Field>"""
    src = _ro(src, old, new, "B7 UI")
    return src


def patch_runcomparison(src):
    if FENCE in src:
        print("  RunComparison.jsx: fence present — skipping (idempotent)")
        return src
    old = """  { key: "pst_expskip", label: "Expiry day", get: (r) => (r.config?.signal_tf && r.config?.skip_expiry_day ? "SKIP" : null) },   // ── PST_SELL_ENTRY_FILTERS_20260828 ──"""
    new = old + f"""
  {{ key: "pst_confirm", label: "Confirm",    get: (r) => (r.config?.signal_tf && Number(r.config?.confirm_minutes) > 0 ? `${{r.config.confirm_minutes}}m` : null) }},   // ── {FENCE} ──"""
    return _ro(src, old, new, "RC PARAM_KEYS")


def patch_queue(src):
    if FENCE in src:
        print("  BacktestQueue.jsx: fence present — skipping (idempotent)")
        return src
    old = """  if (cfg.skip_expiry_day) p.push("noExpDay");"""
    new = old + f"""
  if (Number(cfg.confirm_minutes) > 0) p.push(`cfm${{cfg.confirm_minutes}}m`);   // ── {FENCE} ── sweep rows over N must be tellable apart"""
    return _ro(src, old, new, "BQ paramLine")


def patch_sweepbuilder(src):
    if FENCE in src:
        print("  SweepBuilder.jsx: fence present — skipping (idempotent)")
        return src
    # numeric axis for confirm_minutes, PST_SELL only (the hedge runner
    # does not read the key) — sweeping 1..N is the point of the knob
    old = """  { key: "pst_tg2", label: "L2 spot target", strategies: [PSTS, PSTH],
    hint: "40, 50, 70, 100", parse: _num,
    apply: (c, v) => { const l = (c.legs || [])[1]; if (l) l.spot_tg_points = v; }, fmt: (v) => `TG2 ${v}p` },"""
    new = old + f"""
  {{ key: "pst_confirm", label: "Confirm wait (min)", strategies: [PSTS],
    hint: "1, 2, 3, 5", parse: _num,
    apply: (c, v) => {{ c.confirm_minutes = Math.min(30, Math.max(0, v)); }}, fmt: (v) => `cfm${{v}}m` }},   // ── {FENCE} ──"""
    return _ro(src, old, new, "SB axis")


def main():
    for p in (BT, RC, BQ, SB):
        if not os.path.isfile(p):
            raise SystemExit(f"ABORT: missing {p} — set SCALP_REPO/SCALP_FRONTEND.")
    bt, rc, bq, sb = open(BT).read(), open(RC).read(), open(BQ).read(), open(SB).read()
    bt2, rc2, bq2, sb2 = patch_backtest(bt), patch_runcomparison(rc), patch_queue(bq), patch_sweepbuilder(sb)
    for path, cur, new in ((BT, bt, bt2), (RC, rc, rc2), (BQ, bq, bq2), (SB, sb, sb2)):
        if new != cur:
            open(path, "w").write(new)
            print(f"  wrote {path}")
    print("DONE —", FENCE, "(frontend)")


if __name__ == "__main__":
    main()
