#!/usr/bin/env python3
# apply_run_csv_download.py — fence RUN_CSV_DOWNLOAD_20261016
#
# Backtest → Queue and Compare Runs: download a stored run's CSV straight
# from the list, one at a time or many in one go — no need to Open it first.
#
#   * One shared downloader in Backtest.jsx (downloadRunCsvFiles): fetches
#     /api/backtest/runs/{id} and writes the SAME CSV the results page
#     exports (buildCsv + computeMetrics over the run's own trades + the
#     strategy's DIAG block). File name uses the run's OWN date range:
#     backtest_<STRATEGY>_<from>_to_<to>_<runid8>.csv. Runs are fetched and
#     saved one after another, 0.7 s apart (the webview handles one download
#     at a time reliably); duplicates are dropped; a failed run is reported
#     and the rest still download.
#   * Queue: "⬇ CSV" next to Open on every finished row; a checkbox on each
#     finished row plus a header box (all / none, partial = dash); a
#     "⬇ CSV (N)" button in the queue header; progress / result line.
#   * Compare Runs: "⬇ CSV" next to Open on every row; "⬇ CSV (N)" in the
#     toolbar downloads every ticked run (same selection as Report / Delete).
#
#  EDIT  frontend/src/pages/Backtest.jsx, backtest/BacktestQueue.jsx,
#        backtest/RunComparison.jsx. Backend untouched.
#  Mirrored to desktop/src-tauri/frontend/ when that tree exists.
#  Gate: esbuild parse. Verified in Chromium with the real components
#  (per-row, header tick, bulk; Compare per-row + bulk) and the real
#  downloader (two files, duplicate dropped, empty run and HTTP error
#  reported, CSV byte-identical in layout to the results-page export).
#
# Usage: python3 apply_run_csv_download.py [--repo PATH] [--allow-dirty]

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "RUN_CSV_DOWNLOAD_20261016"
MIRRORS = {"frontend/": "desktop/src-tauri/frontend/"}
EDITS = [
    ('frontend/src/pages/Backtest.jsx', 'function buildCsv(trades, summary, metrics, strategyId, config, diag) {',
     '// ── RUN_CSV_DOWNLOAD_20261016 BEGIN ── download stored runs as the SAME CSV the\n// results page exports (buildCsv + computeMetrics over the run\'s own trades,\n// the strategy diag block, the run\'s OWN date range in the file name), one\n// file per run. Used by the Queue and Compare Runs tabs (per row + selected).\nconst runDiagOf = (s) => s?.diag_cbo || s?.diag_brk || s?.diag_stfc || s?.diag_fvg\n  || s?.diag_emc || s?.diag_vet || s?.diag_tma2;\nexport async function downloadRunCsvFiles(apiCall, runIds, onProgress) {\n  const safe = (x) => String(x || "").replace(/[^0-9A-Za-z_-]/g, "");\n  const ids = [...new Set((runIds || []).filter(Boolean))];\n  const out = { ok: 0, fail: [], files: [] };\n  for (let i = 0; i < ids.length; i++) {\n    const rid = ids[i];\n    if (onProgress) onProgress({ i: i + 1, n: ids.length, rid });\n    try {\n      const d = await apiCall(`/api/backtest/runs/${rid}`);\n      const tr = d?.trades || [];\n      if (!tr.length) throw new Error("run has no trades");\n      const sm = d.summary || {};\n      const sid = d.strategy_id || d.meta?.strategy_id || "";\n      const csv = buildCsv(tr, sm, computeMetrics(tr), sid, d.config || d.meta?.config || null, runDiagOf(sm));\n      const df = d.date_from || d.meta?.date_from || "", dt = d.date_to || d.meta?.date_to || "";\n      const fname = `backtest_${safe(sid)}_${safe(df)}_to_${safe(dt)}_${safe(rid).slice(0, 8)}.csv`;\n      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));\n      const a = document.createElement("a");\n      a.href = url; a.download = fname;\n      document.body.appendChild(a); a.click(); document.body.removeChild(a);\n      setTimeout(() => URL.revokeObjectURL(url), 4000);\n      out.ok += 1; out.files.push(fname);\n    } catch (e) {\n      out.fail.push(`${String(rid).slice(0, 8)}: ${String(e?.message || e)}`);\n    }\n    // the webview handles one download at a time reliably — space them out\n    if (i < ids.length - 1) await new Promise((r) => setTimeout(r, 700));\n  }\n  return out;\n}\n// ── RUN_CSV_DOWNLOAD_20261016 END ──\n\nfunction buildCsv(trades, summary, metrics, strategyId, config, diag) {'),
    ('frontend/src/pages/Backtest.jsx', '        <BacktestQueue\n          colors={colors} spacing={spacing} typography={typography} Card={Card}\n          apiCall={apiCall}\n',
     '        <BacktestQueue\n          colors={colors} spacing={spacing} typography={typography} Card={Card}\n          apiCall={apiCall}\n          onDownloadRuns={(ids, prog) => downloadRunCsvFiles(apiCall, ids, prog)}   /* ── RUN_CSV_DOWNLOAD_20261016 ── */\n'),
    ('frontend/src/pages/Backtest.jsx', '          apiCall={apiCall} fmtInr={fmtInr} fmtTs={fmtTs}\n          computeMetrics={computeMetrics} EquityCurve={EquityCurve}\n',
     '          apiCall={apiCall} fmtInr={fmtInr} fmtTs={fmtTs}\n          computeMetrics={computeMetrics} EquityCurve={EquityCurve}\n          onDownloadRuns={(ids, prog) => downloadRunCsvFiles(apiCall, ids, prog)}   /* ── RUN_CSV_DOWNLOAD_20261016 ── */\n'),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '  onOpenRun,                      // (run_id) => jump to results view\n}) {',
     '  onOpenRun,                      // (run_id) => jump to results view\n  onDownloadRuns,                 // ── RUN_CSV_DOWNLOAD_20261016 ── (run_ids, onProgress) => {ok, fail}\n}) {\n  // ── RUN_CSV_DOWNLOAD_20261016 ── tick finished rows, download their CSVs in one go\n  const [dlSel, setDlSel] = useState(() => new Set());   // job_ids\n  const [dlBusy, setDlBusy] = useState(false);\n  const [dlMsg, setDlMsg] = useState(null);\n  const runDownload = useCallback(async (runIds) => {\n    if (!onDownloadRuns || !runIds.length || dlBusy) return;\n    setDlBusy(true);\n    setDlMsg({ kind: "info", text: `Preparing ${runIds.length} CSV${runIds.length > 1 ? "s" : ""}…` });\n    try {\n      const r = await onDownloadRuns(runIds, ({ i, n }) => setDlMsg({ kind: "info", text: `Downloading ${i}/${n}…` }));\n      setDlMsg(r.fail.length\n        ? { kind: "err", text: `Downloaded ${r.ok}, failed ${r.fail.length}: ${r.fail.join(" · ")}` }\n        : { kind: "ok", text: `Downloaded ${r.ok} CSV${r.ok > 1 ? "s" : ""} to your Downloads folder` });\n    } catch (e) {\n      setDlMsg({ kind: "err", text: `Download failed: ${String(e?.message || e)}` });\n    } finally {\n      setDlBusy(false);\n    }\n  }, [onDownloadRuns, dlBusy]);'),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '            <button style={smallBtn("default")} onClick={addCurrent}>+ Add current params</button>',
     '            {/* ── RUN_CSV_DOWNLOAD_20261016 ── */}\n            {dlDoneJobs.length > 0 && (\n              <button style={smallBtn("default")} disabled={dlBusy || !dlPicked.length}\n                onClick={() => runDownload(dlPicked.map((j) => j.run_id))}\n                title={dlPicked.length ? "Download the ticked finished runs as CSV files (one per run)" : "Tick finished rows (or the header box) to download several at once"}>\n                {dlBusy ? "Downloading…" : `⬇ CSV (${dlPicked.length})`}\n              </button>\n            )}\n            <button style={smallBtn("default")} onClick={addCurrent}>+ Add current params</button>'),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '              <tr>\n                {["#", "Strategy", "Period", "Params", "Status", "Result", ""].map((h, i) => (',
     '              <tr>\n                {/* ── RUN_CSV_DOWNLOAD_20261016 ── tick all finished rows */}\n                <th style={{ padding: "9px 6px 9px 10px", borderBottom: `2px solid ${c.border.light}`, width: 1 }}>\n                  {dlDoneJobs.length > 0 && (\n                    <input type="checkbox" checked={dlPicked.length === dlDoneJobs.length}\n                      ref={(el) => { if (el) el.indeterminate = dlPicked.length > 0 && dlPicked.length < dlDoneJobs.length; }}\n                      onChange={() => setDlSel(dlPicked.length === dlDoneJobs.length ? new Set() : new Set(dlDoneJobs.map((j) => j.job_id)))}\n                      title="Select every finished run for CSV download" aria-label="Select all finished runs" style={{ cursor: "pointer" }} />\n                  )}\n                </th>\n                {["#", "Strategy", "Period", "Params", "Status", "Result", ""].map((h, i) => ('),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '                    <td style={{ padding: "8px 10px", ...typography.mono, color: c.text.tertiary }}>{j.position}</td>',
     '                    <td style={{ padding: "8px 6px 8px 10px" }}>\n                      {j.run_id && j.status === "done" ? (   /* ── RUN_CSV_DOWNLOAD_20261016 ── */\n                        <input type="checkbox" checked={dlSel.has(j.job_id)} aria-label={`Select run ${j.position} for CSV download`}\n                          onChange={() => setDlSel((prev) => { const n = new Set(prev); if (n.has(j.job_id)) n.delete(j.job_id); else n.add(j.job_id); return n; })}\n                          style={{ cursor: "pointer" }} />\n                      ) : null}\n                    </td>\n                    <td style={{ padding: "8px 10px", ...typography.mono, color: c.text.tertiary }}>{j.position}</td>'),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '                        <button onClick={() => onOpenRun?.(j.run_id)}\n                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.primary, fontSize: 12, fontWeight: 600 }}>\n                          Open\n                        </button>',
     '                        <>\n                        <button onClick={() => runDownload([j.run_id])} disabled={dlBusy} title="Download this run\'s CSV"\n                          style={{ border: "none", background: "transparent", cursor: dlBusy ? "default" : "pointer", color: c.text.secondary, fontSize: 12, fontWeight: 600, marginRight: 6 }}>\n                          ⬇ CSV\n                        </button>\n                        <button onClick={() => onOpenRun?.(j.run_id)}\n                          style={{ border: "none", background: "transparent", cursor: "pointer", color: c.primary, fontSize: 12, fontWeight: 600 }}>\n                          Open\n                        </button>\n                        </>'),
    ('frontend/src/pages/backtest/RunComparison.jsx', '  onOpenRun,                 // (run_id) => void : jump to the main results view\n}) {',
     '  onOpenRun,                 // (run_id) => void : jump to the main results view\n  onDownloadRuns,            // ── RUN_CSV_DOWNLOAD_20261016 ── (run_ids, onProgress) => {ok, fail}\n}) {'),
    ('frontend/src/pages/backtest/RunComparison.jsx', '          {selected.size > 0 && (\n            <button style={smallBtn("danger")} onClick={delSelected}>Delete selected</button>\n          )}',
     '          {/* ── RUN_CSV_DOWNLOAD_20261016 ── the ticked runs as CSV files, one per run */}\n          {selected.size > 0 && onDownloadRuns && (\n            <button style={smallBtn("default")} disabled={dlBusy} onClick={() => dlRuns([...selected])}\n              title="Download every ticked run as the same CSV the results page exports (one file per run)">\n              {dlBusy ? "Downloading…" : `⬇ CSV (${selected.size})`}\n            </button>\n          )}\n          {selected.size > 0 && (\n            <button style={smallBtn("danger")} onClick={delSelected}>Delete selected</button>\n          )}'),
    ('frontend/src/pages/backtest/RunComparison.jsx', '  const smallBtn = (variant) => ({',
     '  // ── RUN_CSV_DOWNLOAD_20261016 ── per-row and bulk CSV download (shared helper in Backtest.jsx)\n  const [dlBusy, setDlBusy] = useState(false);\n  const dlRuns = useCallback(async (ids) => {\n    if (!onDownloadRuns || !ids.length || dlBusy) return;\n    setDlBusy(true);\n    setMsg({ kind: "info", text: `Preparing ${ids.length} CSV${ids.length > 1 ? "s" : ""}…` });\n    try {\n      const r = await onDownloadRuns(ids, ({ i, n }) => setMsg({ kind: "info", text: `Downloading ${i}/${n}…` }));\n      setMsg(r.fail.length\n        ? { kind: "err", text: `Downloaded ${r.ok}, failed ${r.fail.length}: ${r.fail.join(" · ")}` }\n        : { kind: "ok", text: `Downloaded ${r.ok} CSV${r.ok > 1 ? "s" : ""} to your Downloads folder` });\n    } catch (e) {\n      setMsg({ kind: "err", text: `Download failed: ${String(e?.message || e)}` });\n    } finally {\n      setDlBusy(false);\n    }\n  }, [onDownloadRuns, dlBusy]);\n\n  const smallBtn = (variant) => ({'),
    ('frontend/src/pages/backtest/RunComparison.jsx', '          onDelete={del} onOpenRun={onOpenRun} onMeta={setMeta}   /* ── RUN_FAVOURITES_20260830 ── */',
     '          onDelete={del} onOpenRun={onOpenRun} onMeta={setMeta}   /* ── RUN_FAVOURITES_20260830 ── */\n          onDownload={onDownloadRuns ? (rid) => dlRuns([rid]) : null} dlBusy={dlBusy}   /* ── RUN_CSV_DOWNLOAD_20261016 ── */'),
    ('frontend/src/pages/backtest/RunComparison.jsx', '  onMeta,                                 // ── RUN_FAVOURITES_20260830 ── (rid, {favourite?, note?})\n}) {',
     '  onMeta,                                 // ── RUN_FAVOURITES_20260830 ── (rid, {favourite?, note?})\n  onDownload, dlBusy,                     // ── RUN_CSV_DOWNLOAD_20261016 ──\n}) {'),
    ('frontend/src/pages/backtest/RunComparison.jsx', '                  <button title="Open in results"\n                    onClick={() => onOpenRun?.(r.run_id)}',
     '                  {onDownload && (   /* ── RUN_CSV_DOWNLOAD_20261016 ── */\n                    <button title="Download this run\'s CSV" disabled={dlBusy}\n                      onClick={() => onDownload(r.run_id)}\n                      style={{ border: "none", background: "transparent", cursor: dlBusy ? "default" : "pointer", color: c.text.secondary, fontSize: 12, fontWeight: 600, marginRight: 8 }}>\n                      ⬇ CSV\n                    </button>\n                  )}\n                  <button title="Open in results"\n                    onClick={() => onOpenRun?.(r.run_id)}'),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '  const finished = jobs.filter((j) => ["done", "error", "cancelled"].includes(j.status));\n',
     '  const finished = jobs.filter((j) => ["done", "error", "cancelled"].includes(j.status));\n  // ── RUN_CSV_DOWNLOAD_20261016 ── finished runs that can be downloaded, and the ticked ones\n  const dlDoneJobs = jobs.filter((j) => j.status === "done" && j.run_id);\n  const dlPicked = dlDoneJobs.filter((j) => dlSel.has(j.job_id));\n'),
    ('frontend/src/pages/backtest/BacktestQueue.jsx', '        {err && <div style={{ marginTop: spacing.md, fontSize: 12, color: c.loss }}>{err}</div>}',
     '        {err && <div style={{ marginTop: spacing.md, fontSize: 12, color: c.loss }}>{err}</div>}\n        {dlMsg && (   /* ── RUN_CSV_DOWNLOAD_20261016 ── */\n          <div style={{ marginTop: spacing.sm, fontSize: 12, fontWeight: 600,\n            color: dlMsg.kind === "ok" ? c.profit : dlMsg.kind === "err" ? c.loss : c.text.muted }}>{dlMsg.text}</div>\n        )}'),
]


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    cur = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        cur[rel] = p.read_text(encoding="utf-8")
        if FENCE in cur[rel]:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit first or --allow-dirty")
    staged = dict(cur)
    for rel, old, new in EDITS:
        n = staged[rel].count(old)
        if n != 1:
            die(f"anchor matched {n}× (need 1) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    esb = next((str(c) for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild")
                if c.exists()), None) or shutil.which("esbuild")
    if esb:
        with tempfile.TemporaryDirectory() as td:
            for rel in targets:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
        print("  ✓ esbuild parsed Backtest / BacktestQueue / RunComparison")
    else:
        print("  ! esbuild not found — parse gate skipped (the frontend build will check)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(p, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied (frontend only). Rebuild the frontend (./desktop/build-scalp.sh both) —\n"
          f"  it is safe to do while a backtest queue runs only if you don't restart the app; otherwise wait.")


if __name__ == "__main__":
    main()
