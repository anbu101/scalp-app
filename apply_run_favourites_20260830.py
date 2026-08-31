#!/usr/bin/env python3
# apply_run_favourites_20260830.py
#
# ── RUN_FAVOURITES_20260830 ── three Compare-Runs / results-page items:
#
#   A. Compare Runs strategy filter chips gain CBO_V1 and BRK_V1.
#   B. Runs can be marked FAVOURITE (★) and carry an optional NOTE, both
#      persisted in backtest_runs (new columns favourite INTEGER DEFAULT 0,
#      note TEXT — self-healed on existing DBs by the additive column guard).
#      New endpoint PATCH /api/backtest/runs/{run_id}/meta. The Compare Runs
#      table gets a ★ column (click to toggle, sortable), a "★ Favourites"
#      filter chip, an inline note editor under Key params, and the search
#      box also matches notes.
#   C. Backtest results Daily / Weekly / Monthly / Yearly tabs show a
#      green/red count line, e.g. "6/7 are green years · 1 red".
#
# Read-only-ish risk class: no live-money path is touched. Backend files are
# patched in BOTH trees. Assert-anchored, replace-once, staged py_compile,
# esbuild parse gate on the two JSX files, .bak-FENCE backups, idempotent.
#
#     python3 apply_run_favourites_20260830.py --check
#     python3 apply_run_favourites_20260830.py

from __future__ import annotations

import argparse
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "RUN_FAVOURITES_20260830"
TREES = [Path("backend/app"), Path("desktop/src-tauri/backend/app")]
REPO = "backtest/repo/backtest_repo.py"
SCHEMA = "backtest/repo/schema.sql"
ROUTES = "api/backtest_routes.py"
RC = Path("frontend/src/pages/backtest/RunComparison.jsx")
BT = Path("frontend/src/pages/Backtest.jsx")


# ═════════════════════════════════════════════════════════════════════════
#  BACKEND
# ═════════════════════════════════════════════════════════════════════════
# repo: expected columns (self-heal)
REPO_COLS_OLD = '''        "finished_at": "INTEGER", "summary_json": "TEXT", "error_text": "TEXT",
    },
    "backtest_trades": {'''
REPO_COLS_NEW = f'''        "finished_at": "INTEGER", "summary_json": "TEXT", "error_text": "TEXT",
        # ── {FENCE} ── user annotations; additive, self-healed on old DBs
        "favourite": "INTEGER NOT NULL DEFAULT 0", "note": "TEXT",
    }},
    "backtest_trades": {{'''

# repo: list_runs select
REPO_LIST_OLD = '''            SELECT run_id, strategy_id, underlying, date_from, date_to,
                   fill_model, status, created_at, finished_at,
                   summary_json, config_json, error_text
            FROM backtest_runs
            ORDER BY created_at DESC'''
REPO_LIST_NEW = f'''            SELECT run_id, strategy_id, underlying, date_from, date_to,
                   fill_model, status, created_at, finished_at,
                   summary_json, config_json, error_text,
                   favourite, note   -- ── {FENCE} ──
            FROM backtest_runs
            ORDER BY created_at DESC'''

# repo: new updater, placed before delete_run
REPO_FN_ANCHOR = '''def delete_run(run_id: str) -> int:
    """Delete a run and its trades. Returns 1 if a run row was removed, else 0."""'''
REPO_FN_NEW = f'''# ── {FENCE} BEGIN ──
def update_run_meta(run_id: str, favourite: Optional[bool] = None,
                    note: Optional[str] = None) -> Optional[dict]:
    """Set the user annotations on a run. Only the fields passed (not None)
    change. Returns the updated {{run_id, favourite, note}} or None when the
    run does not exist. Note is stored trimmed; an empty string clears it."""
    sets, args = [], []
    if favourite is not None:
        sets.append("favourite = ?")
        args.append(1 if favourite else 0)
    if note is not None:
        sets.append("note = ?")
        args.append(note.strip() or None)
    with _connect() as c:
        if sets:
            args.append(run_id)
            c.execute(f"UPDATE backtest_runs SET {{', '.join(sets)}} WHERE run_id = ?", args)
            c.commit()
        r = c.execute("SELECT run_id, favourite, note FROM backtest_runs WHERE run_id = ?",
                      (run_id,)).fetchone()
    return dict(r) if r else None
# ── {FENCE} END ──


''' + REPO_FN_ANCHOR

# schema.sql
SCHEMA_OLD = '''    error_text    TEXT                          -- populated on status='error'
);'''
SCHEMA_NEW = f'''    error_text    TEXT,                         -- populated on status='error'
    favourite     INTEGER NOT NULL DEFAULT 0,   -- ── {FENCE} ── user star
    note          TEXT                          -- ── {FENCE} ── user note
);'''

# routes: request model + endpoint
ROUTES_MODEL_ANCHOR = '''class DhanCredsRequest(BaseModel):
    client_id: str'''
ROUTES_MODEL_NEW = f'''class RunMetaRequest(BaseModel):   # ── {FENCE} ──
    favourite: Optional[bool] = None
    note: Optional[str] = None

''' + ROUTES_MODEL_ANCHOR

ROUTES_EP_ANCHOR = '''@router.delete("/runs/{run_id}")
def delete_run(run_id: str):'''
ROUTES_EP_NEW = f'''@router.patch("/runs/{{run_id}}/meta")
def run_meta(run_id: str, req: RunMetaRequest):
    # ── {FENCE} ── favourite star + free-text note on a run. Partial
    # update: only the fields present in the body change.
    from app.backtest.repo.backtest_repo import update_run_meta
    if req.note is not None and len(req.note) > 2000:
        raise HTTPException(400, "note too long (max 2000 chars)")
    d = update_run_meta(run_id, favourite=req.favourite, note=req.note)
    if d is None:
        raise HTTPException(404, "run not found")
    return {{"ok": True, **d}}


''' + ROUTES_EP_ANCHOR


# ═════════════════════════════════════════════════════════════════════════
#  RunComparison.jsx
# ═════════════════════════════════════════════════════════════════════════
# A. chips
RC_CHIPS_OLD = '"TMA_V1", "TMA_V2", "TSG_V1", "GC_V1", "VET_V1" ].map((sId) => ('
RC_CHIPS_NEW = f'"TMA_V1", "TMA_V2", "TSG_V1", "GC_V1", "VET_V1", "CBO_V1", "BRK_V1" ].map((sId) => ( /* ── {FENCE} ── CBO/BRK chips */'

# B1. state
RC_STATE_OLD = '  const [fProfitableOnly, setFProfitableOnly] = useState(false);\n'
RC_STATE_NEW = RC_STATE_OLD + f'  const [fFavOnly, setFFavOnly] = useState(false);   // ── {FENCE} ──\n'

# B2. filter + search + sort + deps
RC_FILT_OLD = '    if (fProfitableOnly) rows = rows.filter((r) => (r.summary?.net_pnl ?? 0) > 0);\n'
RC_FILT_NEW = RC_FILT_OLD + f'    if (fFavOnly) rows = rows.filter((r) => !!r.favourite);   // ── {FENCE} ──\n'
RC_SEARCH_OLD = '''        (r.date_from || "").includes(q) ||
        (r.date_to || "").includes(q)
      );'''
RC_SEARCH_NEW = f'''        (r.date_from || "").includes(q) ||
        (r.date_to || "").includes(q) ||
        (r.note || "").toLowerCase().includes(q)   // ── {FENCE} ── notes are searchable
      );'''
RC_SORT_OLD = '        case "strategy_id": return r.strategy_id;\n'
RC_SORT_NEW = f'        case "favourite":   return r.favourite ? 1 : 0;   // ── {FENCE} ──\n' + RC_SORT_OLD
RC_DEPS_OLD = '  }, [runs, fStrategy, fStatus, fProfitableOnly, fSearch, sortKey, sortDir, colFilters, marginFor]);'
RC_DEPS_NEW = f'  }}, [runs, fStrategy, fStatus, fProfitableOnly, fFavOnly, fSearch, sortKey, sortDir, colFilters, marginFor]);   // ── {FENCE} ──'

# B3. setMeta callback, placed before `const del = useCallback`
RC_DEL_ANCHOR = '  const del = useCallback(async (rid) => {\n'
RC_META_NEW = f'''  // ── {FENCE} ── favourite star / note. Optimistic local update, then
  // PATCH; on failure the row is restored from the server response or reload.
  const setMeta = useCallback(async (rid, patch) => {{
    setRuns((rs) => rs.map((r) => (r.run_id === rid ? {{ ...r, ...patch }} : r)));
    try {{
      const d = await apiCall(`/api/backtest/runs/${{rid}}/meta`, {{
        method: "PATCH", body: JSON.stringify(patch),
      }});
      setRuns((rs) => rs.map((r) => (r.run_id === rid ? {{ ...r, favourite: d.favourite, note: d.note }} : r)));
    }} catch (e) {{
      setMsg({{ kind: "err", text: `Could not save: ${{String(e.message || e)}}` }});
      reload();
    }}
  }}, [apiCall, reload]);

''' + RC_DEL_ANCHOR

# B4. chip in toolbar
RC_CHIP_OLD = '''        <button style={chip(fProfitableOnly)} onClick={() => setFProfitableOnly((v) => !v)}>
          Profitable only
        </button>'''
RC_CHIP_NEW = RC_CHIP_OLD + f'''
        <button style={{chip(fFavOnly)}} onClick={{() => setFFavOnly((v) => !v)}} title="Only runs you starred">
          ★ Favourites
        </button>'''

# B5. pass onMeta into RunsTable
RC_PASS_OLD = '          onDelete={del} onOpenRun={onOpenRun}\n          STRAT_LABEL={STRAT_LABEL} STATUS_COLOR={STATUS_COLOR}\n'
RC_PASS_NEW = f'          onDelete={{del}} onOpenRun={{onOpenRun}} onMeta={{setMeta}}   /* ── {FENCE} ── */\n          STRAT_LABEL={{STRAT_LABEL}} STATUS_COLOR={{STATUS_COLOR}}\n'

# B6. RunsTable signature
RC_SIG_OLD = '''  onDelete, onOpenRun, STRAT_LABEL, STATUS_COLOR,
  marginFor, colFilters, setColFilters,   // ── MARGIN_COLUMNS / HEADER_FILTERS ──
}) {'''
RC_SIG_NEW = f'''  onDelete, onOpenRun, STRAT_LABEL, STATUS_COLOR,
  marginFor, colFilters, setColFilters,   // ── MARGIN_COLUMNS / HEADER_FILTERS ──
  onMeta,                                 // ── {FENCE} ── (rid, {{favourite?, note?}})
}}) {{'''

# B7. header: ★ column after the checkbox column
RC_TH_OLD = '''            {th("strategy_id", "Strat")}
            {th("created_at", "When")}'''
RC_TH_NEW = f'''            {{th("favourite", "★", "center")}}   {{/* ── {FENCE} ── */}}
            {{th("strategy_id", "Strat")}}
            {{th("created_at", "When")}}'''
# filter row: one more empty th (4 empties precede the params filter)
RC_FTH_OLD = '''          <tr>
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            <th style={{ borderBottom: `2px solid ${c.border.light}` }} />
            {filterCell("params", "a & b…", "left")}'''
RC_FTH_NEW = f'''          <tr>
            <th style={{{{ borderBottom: `2px solid ${{c.border.light}}` }}}} />
            <th style={{{{ borderBottom: `2px solid ${{c.border.light}}` }}}} />   {{/* ── {FENCE} ── ★ column */}}
            <th style={{{{ borderBottom: `2px solid ${{c.border.light}}` }}}} />
            <th style={{{{ borderBottom: `2px solid ${{c.border.light}}` }}}} />
            <th style={{{{ borderBottom: `2px solid ${{c.border.light}}` }}}} />
            {{filterCell("params", "a & b…", "left")}}'''

# B8. body: star cell + note under key params
RC_ROW_OLD = '''                <td style={{ padding: "8px 10px", fontWeight: 700 }}>{STRAT_LABEL[r.strategy_id] || r.strategy_id}</td>'''
RC_ROW_NEW = f'''                {{/* ── {FENCE} ── star toggle */}}
                <td style={{{{ padding: "8px 4px", textAlign: "center" }}}}>
                  <button title={{r.favourite ? "Unmark favourite" : "Mark favourite"}}
                    onClick={{() => onMeta?.(r.run_id, {{ favourite: !r.favourite }})}}
                    style={{{{ border: "none", background: "transparent", cursor: "pointer", fontSize: 15, lineHeight: 1,
                      color: r.favourite ? "#f59e0b" : c.text.muted }}}}>
                    {{r.favourite ? "★" : "☆"}}
                  </button>
                </td>
                <td style={{{{ padding: "8px 10px", fontWeight: 700 }}}}>{{STRAT_LABEL[r.strategy_id] || r.strategy_id}}</td>'''
RC_PARAMS_OLD = '''                <td style={{ padding: "8px 10px", fontSize: 11, color: c.text.secondary, maxWidth: 320 }}>{keyParams || "—"}</td>'''
RC_PARAMS_NEW = f'''                <td style={{{{ padding: "8px 10px", fontSize: 11, color: c.text.secondary, maxWidth: 320 }}}}>
                  {{keyParams || "—"}}
                  {{/* ── {FENCE} ── inline note editor */}}
                  <RunNote value={{r.note}} c={{c}} onSave={{(note) => onMeta?.(r.run_id, {{ note }})}} />
                </td>'''

# B9. RunNote component, placed before RunsTable's doc banner
RC_COMP_ANCHOR = '''/* ============================================================================
   RUNS TABLE — sortable, selectable, with inline params + headline KPIs.'''
RC_COMP_NEW = f'''/* ── {FENCE} ── one-line note under Key params. Click the text (or ✎) to
   edit; Enter / blur saves, Esc cancels. No window.prompt — Tauri's webview
   blocks it. */
function RunNote({{ value, c, onSave }}) {{
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value || "");
  useEffect(() => {{ if (!editing) setDraft(value || ""); }}, [value, editing]);
  const commit = () => {{
    setEditing(false);
    if ((draft || "").trim() !== (value || "").trim()) onSave(draft);
  }};
  if (editing) {{
    return (
      <input autoFocus type="text" value={{draft}} placeholder="Note for this run…"
        onChange={{(e) => setDraft(e.target.value)}}
        onBlur={{commit}}
        onKeyDown={{(e) => {{
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {{ setDraft(value || ""); setEditing(false); }}
        }}}}
        style={{{{ marginTop: 4, width: "100%", boxSizing: "border-box", background: c.bg.primary,
          border: `1px solid ${{c.primary}}`, borderRadius: 4, color: c.text.primary, fontSize: 11, padding: "2px 6px" }}}} />
    );
  }}
  return (
    <div onClick={{() => setEditing(true)}} title={{value ? `${{value}}\\n(click to edit)` : "Add a note"}}
      style={{{{ marginTop: 3, fontSize: 11, fontStyle: value ? "italic" : "normal", cursor: "text",
        color: value ? "#f59e0b" : c.text.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}}}>
      {{value ? `✎ ${{value}}` : "✎ add note"}}
    </div>
  );
}}

''' + RC_COMP_ANCHOR


# ═════════════════════════════════════════════════════════════════════════
#  Backtest.jsx — period green/red count line
# ═════════════════════════════════════════════════════════════════════════
BT_GRID_OLD = '''function PeriodGrid({ data }) {
  if (!data?.length) return <div style={{ color: colors.text.muted, fontSize: 13, textAlign: "center", padding: "40px 0" }}>No data</div>;
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl)), 1);
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      {data.map((m) => {'''
BT_GRID_NEW = f'''function PeriodGrid({{ data, unit = "periods" }}) {{
  if (!data?.length) return <div style={{{{ color: colors.text.muted, fontSize: 13, textAlign: "center", padding: "40px 0" }}}}>No data</div>;
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl)), 1);
  // ── {FENCE} ── green/red tally. "Green" matches the tile colouring
  // (pnl >= 0), so a flat period counts as green here too.
  const greens = data.filter((d) => d.pnl >= 0).length;
  const reds = data.length - greens;
  return (
    <>
    <div style={{{{ fontSize: 12, marginBottom: 12, color: colors.text.secondary }}}}>
      <span style={{{{ fontWeight: 700, color: colors.profit }}}}>{{greens}}/{{data.length}}</span> are green {{unit}}
      <span style={{{{ color: colors.text.muted }}}}> · </span>
      <span style={{{{ fontWeight: 700, color: colors.loss }}}}>{{reds}}</span> red
      <span style={{{{ color: colors.text.muted }}}}> · {{((100 * greens) / data.length).toFixed(0)}}% green</span>
    </div>
    <div style={{{{ display: "flex", gap: 10, flexWrap: "wrap" }}}}>
      {{data.map((m) => {{'''
BT_GRID_END_OLD = '''            <div style={{ fontSize: 10, color: colors.text.muted, marginTop: 4 }}>{m.trades} trades · {wr}% WR</div>
          </div>
        );
      })}
    </div>
  );
}'''
BT_GRID_END_NEW = f'''            <div style={{{{ fontSize: 10, color: colors.text.muted, marginTop: 4 }}}}>{{m.trades}} trades · {{wr}}% WR</div>
          </div>
        );
      }})}}
    </div>
    </>
  );
}}   // ── {FENCE} ── fragment wrapper for the tally line'''
BT_USE_OLD = '              <PeriodGrid data={metrics ? metrics[resultTab] : []} />'
BT_USE_NEW = f'              <PeriodGrid data={{metrics ? metrics[resultTab] : []}} unit={{{{ daily: "days", weekly: "weeks", monthly: "months", yearly: "years" }}[resultTab]}} />   {{/* ── {FENCE} ── */}}'


class Abort(Exception):
    pass


def rep(text, old, new, what, n=1):
    got = text.count(old)
    if got != n:
        raise Abort(f"{what}: anchor found {got} times, expected {n} — file drifted")
    return text.replace(old, new)


def stage_py(path, text):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        raise Abort(f"{path}: staged compile FAILED — {e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def esbuild_ok(path: Path, text: str):
    tmp = path.parent / f"_fav_stage{path.suffix}"
    tmp.write_text(text)
    try:
        r = subprocess.run(["npx", "--yes", "esbuild", str(tmp), "--loader:.jsx=jsx",
                            "--outfile=/dev/null"], capture_output=True, text=True)
        if r.returncode != 0:
            raise Abort(f"esbuild rejected patched {path}:\n{r.stderr[:2000]}")
    except FileNotFoundError:
        print("  WARNING: npx not found — JSX gate SKIPPED", file=sys.stderr)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-esbuild", action="store_true")
    ap.add_argument("--allow-missing-tree", action="store_true")
    a = ap.parse_args()

    present = [t for t in TREES if t.exists()]
    missing = [t for t in TREES if not t.exists()]
    if missing and not a.allow_missing_tree:
        print(f"ABORTED: dual-tree not satisfiable, absent: {[str(m) for m in missing]}", file=sys.stderr)
        return 1

    staged = {}   # Path -> text
    skipped = []
    try:
        for tree in present:
            for rel, edits, is_py in (
                (REPO, [(REPO_COLS_OLD, REPO_COLS_NEW, "repo expected cols"),
                        (REPO_LIST_OLD, REPO_LIST_NEW, "repo list_runs"),
                        (REPO_FN_ANCHOR, REPO_FN_NEW, "repo update_run_meta")], True),
                (SCHEMA, [(SCHEMA_OLD, SCHEMA_NEW, "schema.sql")], False),
                (ROUTES, [(ROUTES_MODEL_ANCHOR, ROUTES_MODEL_NEW, "routes model"),
                          (ROUTES_EP_ANCHOR, ROUTES_EP_NEW, "routes endpoint")], True),
            ):
                p = tree / rel
                if not p.exists():
                    raise Abort(f"missing: {p}")
                t = p.read_text()
                if FENCE in t:
                    skipped.append(p)
                    continue
                for old, new, what in edits:
                    t = rep(t, old, new, f"{p}:{what}")
                if is_py:
                    stage_py(p, t)
                staged[p] = t

        t = RC.read_text()
        if FENCE in t:
            skipped.append(RC)
        else:
            for old, new, what in (
                (RC_CHIPS_OLD, RC_CHIPS_NEW, "chips"), (RC_STATE_OLD, RC_STATE_NEW, "state"),
                (RC_FILT_OLD, RC_FILT_NEW, "filter"), (RC_SEARCH_OLD, RC_SEARCH_NEW, "search"),
                (RC_SORT_OLD, RC_SORT_NEW, "sort"), (RC_DEPS_OLD, RC_DEPS_NEW, "deps"),
                (RC_DEL_ANCHOR, RC_META_NEW, "setMeta"), (RC_CHIP_OLD, RC_CHIP_NEW, "fav chip"),
                (RC_PASS_OLD, RC_PASS_NEW, "onMeta prop"), (RC_SIG_OLD, RC_SIG_NEW, "signature"),
                (RC_TH_OLD, RC_TH_NEW, "header"), (RC_FTH_OLD, RC_FTH_NEW, "filter header"),
                (RC_ROW_OLD, RC_ROW_NEW, "star cell"), (RC_PARAMS_OLD, RC_PARAMS_NEW, "note cell"),
                (RC_COMP_ANCHOR, RC_COMP_NEW, "RunNote component"),
            ):
                t = rep(t, old, new, f"RunComparison:{what}")
            if "useEffect" not in t[:3000]:
                raise Abort("RunComparison.jsx does not import useEffect in its header — check the React import")
            staged[RC] = t

        t = BT.read_text()
        if FENCE in t:
            skipped.append(BT)
        else:
            t = rep(t, BT_GRID_OLD, BT_GRID_NEW, "Backtest:PeriodGrid head")
            t = rep(t, BT_GRID_END_OLD, BT_GRID_END_NEW, "Backtest:PeriodGrid tail")
            t = rep(t, BT_USE_OLD, BT_USE_NEW, "Backtest:PeriodGrid use")
            staged[BT] = t

        if not a.no_esbuild:
            for p, t in staged.items():
                if p.suffix == ".jsx":
                    esbuild_ok(p, t)
    except Abort as e:
        print(f"\nABORTED: {e}\nNo files were modified.", file=sys.stderr)
        return 1

    for p in skipped:
        print(f"  already fenced — skipped     {p}")
    for p, t in staged.items():
        if a.check:
            print(f"  would patch (clean)          {p}")
        else:
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
            p.write_text(t)
            print(f"  patched                      {p}")
    for t in missing:
        print(f"  SKIPPED (tree absent)        {t}")
    print(f"\n{FENCE} {'check complete' if a.check else 'applied'}.")
    if not a.check and staged:
        print("\nNext:")
        print("  1. python3 check_undefined_names.py")
        print("  2. restart backend — the favourite/note columns self-heal on first connect")
        print("  3. cd frontend && npm start → Compare Runs: star a run, add a note, reload; results → Yearly tab")
    return 0


if __name__ == "__main__":
    sys.exit(main())
