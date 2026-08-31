#!/usr/bin/env python3
# apply_pf_favourites_20260830.py
#
# ── PF_FAVOURITES_20260830 ── favourites from Compare Runs surface on the
# Portfolio page's run picker. Requires RUN_FAVOURITES_20260830 (the
# `favourite` / `note` fields on GET /runs and the PATCH /meta endpoint).
#
#   * ★ column in the picker (click toggles — same PATCH as Compare Runs,
#     stopPropagation so it doesn't select the row)
#   * favourites float to the top of the picker (stable within group)
#   * "★ Favourites only" toggle above the picker table, with the count
#   * the note shows under Params (amber italic; edit it in Compare Runs)
#   * STRAT_LABEL gains CBO_V1 / BRK_V1 so their chips aren't raw ids
#
# Frontend only, single tree. Assert-anchored, replace-once, esbuild gate,
# .bak-FENCE backup, idempotent.
#     python3 apply_pf_favourites_20260830.py --check
#     python3 apply_pf_favourites_20260830.py

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = "PF_FAVOURITES_20260830"
PF = Path("frontend/src/pages/backtest/Portfolio.jsx")
RC = Path("frontend/src/pages/backtest/RunComparison.jsx")

# 1. labels
LBL_OLD = '  VET_V1: "VET",   // ── VET_V1 ──\n};'
LBL_NEW = f'  VET_V1: "VET",   // ── VET_V1 ──\n  CBO_V1: "CBO", BRK_V1: "BRK",   // ── {FENCE} ──\n}};'

# 2. state (after pickerOpen)
ST_OLD = '  const [pickerOpen, setPickerOpen] = useState(true);\n'
ST_NEW = ST_OLD + f'  const [favOnly, setFavOnly] = useState(false);   // ── {FENCE} ──\n'

# 3. doneRuns: favourites first, optional filter
DR_OLD = '  const doneRuns = useMemo(() => runs.filter((r) => (r.status || "done") === "done"), [runs]);'
DR_NEW = f'''  // ── {FENCE} ── favourites float to the top (stable within each group);
  // favOnly narrows the picker to starred runs.
  const favCount = useMemo(() => runs.filter((r) => r.favourite && (r.status || "done") === "done").length, [runs]);
  const doneRuns = useMemo(() => {{
    const done = runs.filter((r) => (r.status || "done") === "done" && (!favOnly || r.favourite));
    return done.map((r, i) => [r, i]).sort((a, b) => ((b[0].favourite ? 1 : 0) - (a[0].favourite ? 1 : 0)) || (a[1] - b[1])).map(([r]) => r);
  }}, [runs, favOnly]);
  // ── {FENCE} ── star toggle: optimistic, same endpoint as Compare Runs
  const toggleFav = useCallback(async (rid, next) => {{
    setRuns((rs) => rs.map((r) => (r.run_id === rid ? {{ ...r, favourite: next }} : r)));
    try {{
      await apiCall(`/api/backtest/runs/${{rid}}/meta`, {{ method: "PATCH", body: JSON.stringify({{ favourite: next }}) }});
    }} catch (e) {{
      setMsg({{ kind: "err", text: `Could not save favourite: ${{String(e.message || e)}}` }});
      setRuns((rs) => rs.map((r) => (r.run_id === rid ? {{ ...r, favourite: !next }} : r)));
    }}
  }}, [apiCall]);'''

# 4. toolbar above the picker table
TB_OLD = '''      {pickerOpen && (
      <Card style={{ overflowX: "auto", marginBottom: spacing.lg }}>
        <table style={{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}>'''
TB_NEW = f'''      {{pickerOpen && (
      <Card style={{{{ overflowX: "auto", marginBottom: spacing.lg }}}}>
        {{/* ── {FENCE} ── favourites toggle */}}
        <div style={{{{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", borderBottom: `1px solid ${{c.border.dark}}` }}}}>
          <button style={{chipBtn(favOnly, false)}} onClick={{() => setFavOnly((v) => !v)}}
            title="Only runs starred in Compare Runs (or here)">
            ★ Favourites only{{favCount ? ` (${{favCount}})` : ""}}
          </button>
          <span style={{{{ fontSize: 11, color: c.text.muted }}}}>Starred runs are listed first. Notes are edited in Compare Runs.</span>
        </div>
        <table style={{{{ width: "100%", borderCollapse: "collapse", ...typography.bodyMedium }}}}>'''

# 5. header columns (+★ at index 1; right-aligned shift to 5..7)
TH_OLD = '''              {["", "Strat", "Period", "Params", "Net", "Max DD", "Trades", ""].map((h, i) => (
                <th key={i} style={{ padding: "9px 10px", textAlign: i >= 4 && i <= 6 ? "right" : "left",'''
TH_NEW = f'''              {{["", "★", "Strat", "Period", "Params", "Net", "Max DD", "Trades", ""].map((h, i) => (   /* ── {FENCE} ── */
                <th key={{i}} style={{{{ padding: "9px 10px", textAlign: i >= 5 && i <= 7 ? "right" : i === 1 ? "center" : "left",'''

# 6. row: star cell + note under params
ROW_OLD = '''                  <td style={{ padding: "8px 10px" }}><StratChip sid={r.strategy_id} c={c} /></td>'''
ROW_NEW = f'''                  {{/* ── {FENCE} ── */}}
                  <td style={{{{ padding: "8px 4px", textAlign: "center", width: 28 }}}}>
                    <button title={{r.favourite ? "Unmark favourite" : "Mark favourite"}}
                      onClick={{(e) => {{ e.stopPropagation(); toggleFav(r.run_id, !r.favourite); }}}}
                      style={{{{ border: "none", background: "transparent", cursor: "pointer", fontSize: 15, lineHeight: 1,
                        color: r.favourite ? "#f59e0b" : c.text.muted }}}}>
                      {{r.favourite ? "★" : "☆"}}
                    </button>
                  </td>
                  <td style={{{{ padding: "8px 10px" }}}}><StratChip sid={{r.strategy_id}} c={{c}} /></td>'''
PR_OLD = '''                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={params}>
                    {params || "—"}
                  </td>'''
PR_NEW = f'''                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}}} title={{r.note ? `${{params}}\\n\\nNote: ${{r.note}}` : params}}>
                    {{params || "—"}}
                    {{/* ── {FENCE} ── note from Compare Runs */}}
                    {{r.note && <div style={{{{ fontSize: 11, fontStyle: "italic", color: "#f59e0b", marginTop: 2 }}}}>✎ {{r.note}}</div>}}
                  </td>'''

# 7. empty-state colspan 8 -> 9, and message when favOnly hides everything
CS_OLD = '''              <tr><td colSpan={8} style={{ padding: "32px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}>
                No finished runs yet — stage a portfolio above or run backtests from the Run tab.
              </td></tr>'''
CS_NEW = f'''              <tr><td colSpan={{9}} style={{{{ padding: "32px 0", textAlign: "center", color: c.text.muted, fontSize: 13 }}}}>   {{/* ── {FENCE} ── */}}
                {{favOnly ? "No starred runs — star runs in Compare Runs, or turn off the Favourites filter." : "No finished runs yet — stage a portfolio above or run backtests from the Run tab."}}
              </td></tr>'''


class Abort(Exception):
    pass


def rep(t, old, new, what):
    n = t.count(old)
    if n != 1:
        raise Abort(f"{what}: anchor found {n} times, expected 1 — file drifted")
    return t.replace(old, new)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true")
    a = ap.parse_args()
    if not PF.exists():
        print(f"ABORTED: {PF} not found — run from the repo root", file=sys.stderr)
        return 1
    if RC.exists() and "RUN_FAVOURITES_20260830" not in RC.read_text():
        print("ABORTED: apply_run_favourites_20260830.py has not been applied — run it first "
              "(this patch depends on the favourite/note fields and the PATCH /meta endpoint).",
              file=sys.stderr)
        return 1
    t = PF.read_text()
    if FENCE in t:
        print(f"  already fenced — skipped     {PF}\n\n{FENCE} no-op.")
        return 0
    try:
        for old, new, what in (
            (LBL_OLD, LBL_NEW, "STRAT_LABEL"), (ST_OLD, ST_NEW, "state"),
            (DR_OLD, DR_NEW, "doneRuns"), (TB_OLD, TB_NEW, "toolbar"),
            (TH_OLD, TH_NEW, "header"), (ROW_OLD, ROW_NEW, "star cell"),
            (PR_OLD, PR_NEW, "note under params"), (CS_OLD, CS_NEW, "empty state"),
        ):
            t = rep(t, old, new, f"Portfolio:{what}")
        for need in ("useCallback", "apiCall", "chipBtn", "setMsg"):
            if need not in t:
                raise Abort(f"Portfolio.jsx no longer defines/imports `{need}`")
        if not a.no_esbuild:
            tmp = PF.parent / "_pf_fav_stage.jsx"
            tmp.write_text(t)
            try:
                r = subprocess.run(["npx", "--yes", "esbuild", str(tmp), "--loader:.jsx=jsx",
                                    "--outfile=/dev/null"], capture_output=True, text=True)
                if r.returncode != 0:
                    raise Abort(f"esbuild rejected patched {PF}:\n{r.stderr[:2000]}")
            except FileNotFoundError:
                print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
            finally:
                tmp.unlink(missing_ok=True)
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1
    if a.check:
        print(f"  would patch (clean, esbuild OK)   {PF}\n\n{FENCE} check complete.")
        return 0
    shutil.copy2(PF, PF.with_name(PF.name + f".bak-{FENCE}"))
    PF.write_text(t)
    print(f"  patched (backup .bak-{FENCE})   {PF}\n\n{FENCE} applied.")
    print("\nNext: npm start → Portfolio tab: starred runs sit at the top with ★; toggle 'Favourites only'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
