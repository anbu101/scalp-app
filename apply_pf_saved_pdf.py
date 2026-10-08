#!/usr/bin/env python3
# apply_pf_saved_pdf.py — fence PF_SAVED_PDF_20261008
#
# Backtest → Portfolio gets two features:
#
#  1. SAVED PORTFOLIOS — "Save portfolio" above the results stores a name +
#     the exact run_ids (new table backtest_portfolios in backtest.db; never
#     app.db). A "Saved portfolios" row at the top opens one with a click.
#     Save validates server-side like PF_VALIDATE (2–5 finished runs, one per
#     strategy, same date range). Name clash → inline "Replace it". Delete is
#     two clicks and never touches the runs. Legs older than the latest-200
#     list are fetched by id; a leg deleted later shows "N missing" and the
#     portfolio refuses to open (never a silent partial composition).
#  2. DOWNLOAD PDF — A4 report: headline figures, strategies + params + notes,
#     contribution, equity chart, correlation, worst days, exposure, month
#     scoreboard, month-by-month. LAYOUT ONLY on the backend: the page sends
#     the figures composePortfolio already computed, so the PDF always equals
#     the screen (basis/tax included). matplotlib PDF backend (already bundled
#     for the Telegram cards), DejaVu fonts embedded (₹ glyph). A copy is kept
#     in ~/.scalp-app/backtest/reports/portfolio/.
#
#  Also fixes: with "Favourites only" on, a portfolio containing an unstarred
#  run silently dropped that leg — the filter now narrows the picker only.
#
#  NEW   backend/app/backtest/repo/portfolio_repo.py
#        backend/app/backtest/report/portfolio_pdf.py
#        backend/app/api/backtest_portfolio_routes.py   (/api/backtest/portfolios/*)
#        backend/app/backtest/report/test_portfolio_saved_pdf.py
#  EDIT  backend/app/api_server.py                       (mount router, admin gate)
#        frontend/src/pages/backtest/Portfolio.jsx
#  Mirrored to desktop/src-tauri/{backend,frontend}/ when those trees exist.
#
#  No live/trading path touched; no schema.sql change; no new dependency.
#
# Usage: python3 apply_pf_saved_pdf.py [--repo PATH] [--allow-dirty]
#                                      [--skip-tests] [--skip-esbuild]
# Run ONLY this script — it carries every payload itself.

from __future__ import annotations

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "PF_SAVED_PDF_20261008"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TEST = "backend/app/backtest/report/test_portfolio_saved_pdf.py"

PREREQS = {
    "frontend/src/pages/backtest/Portfolio.jsx": ["PF_NET_AFTER_TAX_20260930", "PF_FAVOURITES_20260830",
                                                  "PF_SWITCH_FIX", "PF_DELETE END", "export function composePortfolio("],
    "backend/app/api_server.py": ["from app.api.backtest_routes import router as backtest_router", "_require_admin_ui"],
    "backend/app/backtest/repo/backtest_repo.py": ["def _db_path()"],
    "frontend/src/api/base.js": ["export function getApiBase"],
    "backend/requirements.txt": ["matplotlib"],
}

NEW_FILES = {}
NEW_FILES['backend/app/backtest/repo/portfolio_repo.py'] = '# backend/app/backtest/repo/portfolio_repo.py\n#\n# ── PF_SAVED_PDF_20261008 ── named, saved portfolios for Backtest → Portfolio.\n#\n# A saved portfolio is a NAME plus the exact run_ids it composes — nothing\n# else. The composition math stays where it has always been (client-side,\n# composePortfolio in Portfolio.jsx, over the persisted trades), so opening a\n# saved portfolio re-composes the same runs and shows the same numbers as the\n# day it was saved. Nothing is snapshotted that could drift from the runs.\n#\n# Storage: table `backtest_portfolios` in backtest.db (the backtest-only DB —\n# never the live app.db). Created here with CREATE TABLE IF NOT EXISTS, so\n# schema.sql and its PyInstaller data-file path are untouched.\n#\n# Save-time validation mirrors the page\'s PF_VALIDATE rules, so a saved\n# portfolio is always composable when saved: 2..MAX_LEGS runs, all present and\n# status "done", one run per strategy, identical date_from/date_to.\n# A run deleted later in Compare Runs is reported per leg (present=False);\n# the page refuses to compose a partial portfolio rather than silently\n# dropping the leg.\n\nfrom __future__ import annotations\n\nimport json\nimport sqlite3\nimport time\nimport uuid\nfrom typing import Dict, List, Optional, Tuple\n\nMAX_LEGS = 5           # same cap as MAX_PF in Portfolio.jsx\nMAX_NAME = 80\nMAX_NOTE = 500\n\n_DDL = """\nCREATE TABLE IF NOT EXISTS backtest_portfolios (\n    pf_id         TEXT    PRIMARY KEY,\n    name          TEXT    NOT NULL,\n    name_key      TEXT    NOT NULL UNIQUE,      -- lower(trim(name)): case-insensitive uniqueness\n    run_ids_json  TEXT    NOT NULL,             -- ["run_id", ...] in display order\n    legs_json     TEXT    NOT NULL,             -- [{run_id, strategy_id, date_from, date_to}] at save time\n    note          TEXT,\n    created_at    INTEGER NOT NULL,\n    updated_at    INTEGER NOT NULL\n);\n"""\n\n\nclass PortfolioError(ValueError):\n    """Validation failure; `status` is the HTTP code the route should return."""\n\n    def __init__(self, msg: str, status: int = 400):\n        super().__init__(msg)\n        self.status = status\n\n\ndef _db_path():\n    from app.backtest.repo.backtest_repo import _db_path as p\n    return p()\n\n\ndef _connect(db_path=None) -> sqlite3.Connection:\n    c = sqlite3.connect(str(db_path or _db_path()))\n    c.row_factory = sqlite3.Row\n    c.execute("PRAGMA journal_mode=WAL;")\n    c.executescript(_DDL)\n    return c\n\n\ndef _name_key(name: str) -> str:\n    return " ".join(str(name or "").split()).lower()\n\n\ndef _clean_name(name) -> str:\n    n = " ".join(str(name or "").split())\n    if not n:\n        raise PortfolioError("Give the portfolio a name.")\n    if len(n) > MAX_NAME:\n        raise PortfolioError(f"Name is too long (max {MAX_NAME} characters).")\n    return n\n\n\ndef _runs_meta(c: sqlite3.Connection, run_ids: List[str]) -> Dict[str, dict]:\n    if not run_ids:\n        return {}\n    has = c.execute("SELECT name FROM sqlite_master WHERE type=\'table\' AND name=\'backtest_runs\'").fetchone()\n    if not has:\n        return {}\n    q = ",".join("?" for _ in run_ids)\n    rows = c.execute(\n        f"SELECT run_id, strategy_id, date_from, date_to, status FROM backtest_runs WHERE run_id IN ({q})",\n        list(run_ids),\n    ).fetchall()\n    return {r["run_id"]: dict(r) for r in rows}\n\n\ndef validate_legs(run_ids, meta: Dict[str, dict]) -> List[dict]:\n    """PF_VALIDATE, server side. Returns the legs in the given order."""\n    if not isinstance(run_ids, list) or not all(isinstance(x, str) and x for x in run_ids):\n        raise PortfolioError("run_ids must be a list of run ids.")\n    ids = list(dict.fromkeys(run_ids))            # de-dupe, keep order\n    if len(ids) < 2:\n        raise PortfolioError("A portfolio needs at least 2 runs.")\n    if len(ids) > MAX_LEGS:\n        raise PortfolioError(f"A portfolio holds at most {MAX_LEGS} runs.")\n    missing = [i for i in ids if i not in meta]\n    if missing:\n        raise PortfolioError(f"{len(missing)} run(s) not found: {\', \'.join(m[:8] for m in missing)}.", 404)\n    notdone = [i for i in ids if (meta[i].get("status") or "done") != "done"]\n    if notdone:\n        raise PortfolioError(f"Only finished runs can be saved — {\', \'.join(n[:8] for n in notdone)} is not done.")\n    seen: Dict[str, str] = {}\n    for i in ids:\n        sid = meta[i]["strategy_id"]\n        if sid in seen:\n            raise PortfolioError(f"One run per strategy — {sid} appears twice.")\n        seen[sid] = i\n    f, t = meta[ids[0]]["date_from"], meta[ids[0]]["date_to"]\n    if any(meta[i]["date_from"] != f or meta[i]["date_to"] != t for i in ids):\n        raise PortfolioError("Every run in a portfolio must cover the same date range.")\n    return [{"run_id": i, "strategy_id": meta[i]["strategy_id"],\n             "date_from": meta[i]["date_from"], "date_to": meta[i]["date_to"]} for i in ids]\n\n\ndef _row_out(r: sqlite3.Row, present: Optional[set] = None) -> dict:\n    legs = json.loads(r["legs_json"] or "[]")\n    if present is not None:\n        for leg in legs:\n            leg["present"] = leg["run_id"] in present\n    return {\n        "pf_id": r["pf_id"], "name": r["name"], "note": r["note"],\n        "run_ids": json.loads(r["run_ids_json"] or "[]"),\n        "legs": legs,\n        "missing": sum(1 for leg in legs if leg.get("present") is False),\n        "date_from": legs[0]["date_from"] if legs else None,\n        "date_to": legs[0]["date_to"] if legs else None,\n        "created_at": r["created_at"], "updated_at": r["updated_at"],\n    }\n\n\ndef list_portfolios(db_path=None) -> List[dict]:\n    with _connect(db_path) as c:\n        rows = c.execute("SELECT * FROM backtest_portfolios ORDER BY name_key ASC").fetchall()\n        all_ids = sorted({i for r in rows for i in json.loads(r["run_ids_json"] or "[]")})\n        present = set(_runs_meta(c, all_ids).keys())\n    return [_row_out(r, present) for r in rows]\n\n\ndef save_portfolio(name, run_ids, note=None, replace: bool = False, db_path=None) -> Tuple[dict, bool]:\n    """Create a portfolio, or (replace=True) overwrite the runs/note of the one\n    with the same name. Returns (portfolio, replaced). A name clash without\n    replace raises PortfolioError(status=409)."""\n    nm = _clean_name(name)\n    key = _name_key(nm)\n    if note is not None:\n        note = str(note).strip()[:MAX_NOTE] or None\n    now = int(time.time())\n    with _connect(db_path) as c:\n        legs = validate_legs(run_ids, _runs_meta(c, list(run_ids) if isinstance(run_ids, list) else []))\n        ids = [leg["run_id"] for leg in legs]\n        existing = c.execute("SELECT * FROM backtest_portfolios WHERE name_key = ?", (key,)).fetchone()\n        if existing and not replace:\n            raise PortfolioError(f\'A portfolio named "{existing["name"]}" already exists.\', 409)\n        if existing:\n            c.execute(\n                "UPDATE backtest_portfolios SET name = ?, run_ids_json = ?, legs_json = ?, note = ?, updated_at = ? "\n                "WHERE pf_id = ?",\n                (nm, json.dumps(ids), json.dumps(legs), note if note is not None else existing["note"],\n                 now, existing["pf_id"]),\n            )\n            pf_id = existing["pf_id"]\n        else:\n            pf_id = uuid.uuid4().hex[:12]\n            c.execute(\n                "INSERT INTO backtest_portfolios (pf_id, name, name_key, run_ids_json, legs_json, note, created_at, updated_at) "\n                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",\n                (pf_id, nm, key, json.dumps(ids), json.dumps(legs), note, now, now),\n            )\n        c.commit()\n        r = c.execute("SELECT * FROM backtest_portfolios WHERE pf_id = ?", (pf_id,)).fetchone()\n        present = set(_runs_meta(c, ids).keys())\n    return _row_out(r, present), bool(existing)\n\n\ndef delete_portfolio(pf_id: str, db_path=None) -> int:\n    """Delete the saved portfolio only — its backtest runs are never touched."""\n    with _connect(db_path) as c:\n        cur = c.execute("DELETE FROM backtest_portfolios WHERE pf_id = ?", (str(pf_id),))\n        c.commit()\n        return cur.rowcount or 0\n'

NEW_FILES['backend/app/backtest/report/portfolio_pdf.py'] = '# backend/app/backtest/report/portfolio_pdf.py\n#\n# ── PF_SAVED_PDF_20261008 ── Backtest → Portfolio "Download PDF".\n#\n# LAYOUT ONLY. Every number in the PDF is computed by the page itself\n# (composePortfolio in Portfolio.jsx) and arrives here in the request body\n# already final — this module formats and places it, nothing else. One source\n# of truth means the PDF can never disagree with what was on screen (basis,\n# tax spreading, exit-realized drawdown and all), and there is no second copy\n# of the portfolio math to drift.\n#\n# Pure module: no app.* imports, no DB, no file I/O — payload dict in, PDF\n# bytes out. Uses matplotlib\'s PDF backend (already bundled for the Telegram\n# cards; collect_submodules("matplotlib.backends") ships backend_pdf) and its\n# bundled DejaVu fonts, which carry the ₹ glyph. Figures are built with the\n# OO API (no pyplot), so concurrent renders don\'t share global figure state.\n#\n# Pages (A4 portrait, white, print-friendly):\n#   1  header · headline figures · strategies with params/notes · contribution\n#   2  equity curve (real time axis) · correlation + diversification · worst days\n#   3+ exposure · month scoreboard · month-by-month table (paginates)\n\nfrom __future__ import annotations\n\nimport io\nimport math\nfrom datetime import datetime, timedelta\nfrom typing import Any, Dict, List, Optional\n\nPAGE_W, PAGE_H = 210.0, 297.0            # mm\nMX, TOP, BOTTOM = 16.0, 16.0, 279.0      # margins; content stops at BOTTOM\nCW = PAGE_W - 2 * MX                     # content width\nPT = 0.3528                              # mm per point\nIST = 19800\n\nINK = "#1e1a2e"\nMUTED = "#6b6680"\nFAINT = "#9a95ab"\nRULE = "#dcd8e5"\nZEBRA = "#f6f4fa"\nACCENT = "#6d28d9"\nPROFIT = "#12733d"\nLOSS = "#b42318"\nWARN = "#a15c07"\nNOTE = "#8a4b0a"\n\nSANS = "DejaVu Sans"\nMONO = "DejaVu Sans Mono"\n\n\n# ─────────────────────────── formatting ───────────────────────────\ndef _num(v) -> Optional[float]:\n    try:\n        f = float(v)\n    except (TypeError, ValueError):\n        return None\n    return f if math.isfinite(f) else None\n\n\ndef inr(v) -> str:\n    """₹ with Indian digit grouping, no decimals — same as the page\'s fmtInr."""\n    f = _num(v)\n    if f is None:\n        return "—"\n    s = str(int(round(abs(f))))\n    if len(s) > 3:\n        head, tail = s[:-3], s[-3:]\n        parts = []\n        while len(head) > 2:\n            parts.insert(0, head[-2:])\n            head = head[:-2]\n        if head:\n            parts.insert(0, head)\n        s = ",".join(parts + [tail])\n    return "₹" + s\n\n\ndef money(v) -> str:\n    f = _num(v)\n    if f is None:\n        return "—"\n    return ("+" if f >= 0 else "−") + inr(f)\n\n\ndef compact_inr(v) -> str:\n    f = _num(v)\n    if f is None:\n        return ""\n    a, sign = abs(f), ("−" if f < 0 else "")\n    if a >= 1e7:\n        return f"{sign}₹{a / 1e7:.2f} Cr"\n    if a >= 1e5:\n        return f"{sign}₹{a / 1e5:.1f} L"\n    if a >= 1e3:\n        return f"{sign}₹{a / 1e3:.0f}k"\n    return f"{sign}₹{a:.0f}"\n\n\ndef pnl_color(v) -> str:\n    f = _num(v)\n    if f is None or f == 0:\n        return INK\n    return PROFIT if f > 0 else LOSS\n\n\ndef ratio(v, inf=False) -> str:\n    if inf:\n        return "∞"\n    f = _num(v)\n    return "—" if f is None else f"{f:.2f}"\n\n\ndef _s(v, default="") -> str:\n    return default if v is None else str(v)\n\n\n# ─────────────────────────── text metrics ───────────────────────────\n_FONTS: Dict[tuple, Any] = {}\n\n\ndef _ft(family: str, bold: bool):\n    key = (family, bold)\n    if key not in _FONTS:\n        from matplotlib import font_manager\n        from matplotlib.ft2font import FT2Font\n        path = font_manager.findfont(font_manager.FontProperties(\n            family=family, weight="bold" if bold else "normal"))\n        _FONTS[key] = FT2Font(path)\n    return _FONTS[key]\n\n\ndef text_w(s: str, size: float, mono=False, bold=False) -> float:\n    """Rendered width in mm."""\n    if not s:\n        return 0.0\n    f = _ft(MONO if mono else SANS, bold)\n    f.set_size(size, 72)\n    f.set_text(s, 0.0)\n    return f.get_width_height()[0] / 64.0 * PT\n\n\ndef fit(s: str, width: float, size: float, mono=False, bold=False) -> str:\n    s = _s(s)\n    if text_w(s, size, mono, bold) <= width:\n        return s\n    while s and text_w(s + "…", size, mono, bold) > width:\n        s = s[:-1]\n    return s.rstrip() + "…"\n\n\ndef wrap(s: str, width: float, size: float, max_lines: int = 99, mono=False, sep: str = " ") -> List[str]:\n    """Greedy wrap. sep=" · " wraps a params string between its fields, so\n    no line starts with a dangling separator."""\n    raw = _s(s).split(sep) if sep != " " else _s(s).split()\n    words = [w.strip() for w in raw if w.strip()]\n    lines: List[str] = []\n    cur = ""\n    for w in words:\n        trial = (cur + sep + w) if cur else w\n        if text_w(trial, size, mono) <= width or not cur:\n            cur = trial\n        else:\n            lines.append(cur)\n            cur = w\n    if cur:\n        lines.append(cur)\n    if len(lines) > max_lines:\n        lines = lines[:max_lines]\n        lines[-1] = fit(lines[-1] + " …", width, size, mono)\n    return [fit(x, width, size, mono) for x in lines]\n\n\ndef lh(size: float) -> float:\n    return size * PT * 1.42\n\n\n# ─────────────────────────── document ───────────────────────────\nclass Doc:\n    def __init__(self):\n        self.pages = []\n        self.y = TOP\n        self.fig = None\n        self.ax = None\n\n    def new_page(self):\n        from matplotlib.figure import Figure\n        fig = Figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))\n        fig.patch.set_facecolor("white")\n        ax = fig.add_axes([0, 0, 1, 1])\n        ax.set_xlim(0, PAGE_W)\n        ax.set_ylim(PAGE_H, 0)\n        ax.axis("off")\n        self.pages.append((fig, ax))\n        self.fig, self.ax = fig, ax\n        self.y = TOP\n        return fig, ax\n\n    def need(self, h: float):\n        if self.fig is None or self.y + h > BOTTOM:\n            self.new_page()\n\n    def text(self, x, y, s, size=8.5, color=INK, bold=False, mono=False, ha="left", va="top",\n             italic=False, ax=None):\n        (ax or self.ax).text(x, y, s, fontsize=size, color=color, ha=ha, va=va,\n                             family=MONO if mono else SANS,\n                             fontweight="bold" if bold else "normal",\n                             fontstyle="italic" if italic else "normal")\n\n    def hline(self, x0, x1, y, color=RULE, lw=0.6):\n        self.ax.plot([x0, x1], [y, y], color=color, lw=lw, solid_capstyle="butt")\n\n    def rect(self, x, y, w, h, color, alpha=1.0, ec="none", lw=0):\n        from matplotlib.patches import Rectangle\n        self.ax.add_patch(Rectangle((x, y), w, h, facecolor=color, alpha=alpha,\n                                    edgecolor=ec, linewidth=lw))\n\n    def section(self, title: str, sub: str = "", keep: float = 30.0):\n        self.need(10 + keep)\n        if self.y > TOP + 1:\n            self.y += 4\n        self.text(MX, self.y, title, size=11.5, bold=True)\n        if sub:\n            self.text(MX + text_w(title, 11.5, bold=True) + 3, self.y + 0.9, sub, size=7.5, color=MUTED)\n        self.y += lh(11.5) + 1.2\n\n    def para(self, s: str, size=7.2, color=MUTED, width=CW, x=MX):\n        for line in wrap(s, width, size):\n            self.need(lh(size))\n            self.text(x, self.y, line, size=size, color=color)\n            self.y += lh(size)\n\n    def table(self, cols: List[dict], rows: List[List[Any]], size=7.8, row_h=6.0,\n              head_size=7.0, zebra=True, bold_last=False, chip_col: Optional[int] = None):\n        """cols: [{title, w, align}] widths in mm (sum ≤ CW).\n        rows: cells are str or {t, color, bold, swatch}. Repeats the header\n        after a page break."""\n        def header():\n            self.need(row_h + 2)\n            x = MX\n            for c in cols:\n                tx = x + (c["w"] - 1.5 if c.get("align") == "right" else 1.5)\n                self.text(tx, self.y + row_h / 2, c["title"], size=head_size, color=MUTED,\n                          ha="right" if c.get("align") == "right" else "left", va="center")\n                x += c["w"]\n            self.y += row_h\n            self.hline(MX, MX + sum(c["w"] for c in cols), self.y, color=INK, lw=0.7)\n\n        header()\n        for ri, row in enumerate(rows):\n            if self.y + row_h > BOTTOM:\n                self.new_page()\n                header()\n            last = bold_last and ri == len(rows) - 1\n            if zebra and ri % 2 == 1:\n                self.rect(MX, self.y, sum(c["w"] for c in cols), row_h, ZEBRA)\n            x = MX\n            for ci, (c, cell) in enumerate(zip(cols, row)):\n                d = cell if isinstance(cell, dict) else {"t": cell}\n                t = _s(d.get("t"), "—")\n                right = c.get("align") == "right"\n                mono = d.get("mono", c.get("mono", right))\n                bold = d.get("bold", False) or last\n                avail = c["w"] - 3\n                tx = x + 1.5\n                if d.get("swatch"):\n                    self.rect(x + 1.5, self.y + row_h / 2 - 1.4, 2.8, 2.8, d["swatch"])\n                    tx += 4.2\n                    avail -= 4.2\n                t = fit(t, avail, size, mono, bold)\n                self.text(x + c["w"] - 1.5 if right else tx, self.y + row_h / 2, t, size=size,\n                          color=d.get("color", INK), bold=bold, mono=mono,\n                          ha="right" if right else "left", va="center")\n                x += c["w"]\n            self.y += row_h\n            if last:\n                self.hline(MX, MX + sum(c["w"] for c in cols), self.y - row_h, color=INK, lw=0.5)\n        self.hline(MX, MX + sum(c["w"] for c in cols), self.y, color=RULE, lw=0.6)\n\n    def kpis(self, items: List[dict], h=19.0):\n        """Headline figures in a row: [{label, value, color, sub}]."""\n        self.need(h + 2)\n        n = len(items)\n        gap = 4.0\n        w = (CW - gap * (n - 1)) / n\n        for i, it in enumerate(items):\n            x = MX + i * (w + gap)\n            self.ax.plot([x, x], [self.y, self.y + h], color=it.get("color", INK), lw=1.6,\n                         solid_capstyle="butt")\n            self.text(x + 3, self.y + 0.4, it["label"], size=7.2, color=MUTED)\n            size = 14.5                       # shrink to fit — a headline figure is never truncated\n            while size > 8 and text_w(it["value"], size, mono=True, bold=True) > w - 4:\n                size -= 0.5\n            self.text(x + 3, self.y + 5.0 + (14.5 - size) * PT * 0.5, it["value"], size=size,\n                      color=it.get("color", INK), bold=True, mono=True)\n            if it.get("sub"):\n                for k, line in enumerate(wrap(it["sub"], w - 4, 6.6, max_lines=2)):\n                    self.text(x + 3, self.y + 12.4 + k * lh(6.6), line, size=6.6, color=MUTED)\n        self.y += h + 3\n\n    def chart_axes(self, x, y, w, h):\n        """A real matplotlib Axes placed at a mm rectangle on the current page."""\n        return self.fig.add_axes([x / PAGE_W, 1 - (y + h) / PAGE_H, w / PAGE_W, h / PAGE_H])\n\n\n# ─────────────────────────── sections ───────────────────────────\ndef _header(doc: Doc, p: dict):\n    doc.new_page()\n    name = _s(p.get("name")).strip() or "Untitled portfolio"\n    per = p.get("period") or {}\n    strats = p.get("strategies") or []\n    doc.text(MX, doc.y, "Backtest portfolio report", size=8.5, color=ACCENT, bold=True)\n    doc.y += lh(8.5) + 0.8\n    doc.text(MX, doc.y, fit(name, CW, 21, bold=True), size=21, bold=True)\n    doc.y += lh(21) + 0.6\n    basis = ("Net after tax" if p.get("basis") == "after" and p.get("any_taxed") else "Net P&L (pre-tax)"\n             if p.get("any_tax_available") else "Net P&L")\n    bits = [f"{_s(per.get(\'from\'), \'?\')} to {_s(per.get(\'to\'), \'?\')}",\n            f"{len(strats)} strategies", f"Basis: {basis}"]\n    if p.get("generated_label"):\n        bits.append(f"Generated {p[\'generated_label\']}")\n    doc.text(MX, doc.y, "     ".join(bits), size=8.2, color=MUTED)\n    doc.y += lh(8.2) + 2.5\n    doc.hline(MX, MX + CW, doc.y, color=ACCENT, lw=1.1)\n    doc.y += 5\n\n\ndef _headline(doc: Doc, p: dict):\n    k = p.get("kpis") or {}\n    any_taxed = bool(p.get("any_taxed"))\n    ddr = _num(k.get("dd_reduction"))\n    rtd_inf = bool(k.get("return_to_dd_inf"))\n    rtd = _num(k.get("return_to_dd"))\n    doc.kpis([\n        {"label": "Combined net after tax" if any_taxed else "Combined net P&L",\n         "value": money(k.get("combined_net")), "color": pnl_color(k.get("combined_net")),\n         "sub": (f"sum of all strategies, tax {money(-(_num(p.get(\'combined_tax\')) or 0))}"\n                 if any_taxed else "sum of all strategies (net)")},\n        {"label": "Combined max drawdown", "value": inr(k.get("combined_max_dd")), "color": LOSS,\n         "sub": "on the merged exit-realized curve"},\n        {"label": "Drawdown reduction", "value": "—" if ddr is None else f"{ddr:.1f}%",\n         "color": INK if ddr is None else (PROFIT if ddr > 0 else LOSS),\n         "sub": f"vs sum of individual DDs ({inr(k.get(\'sum_ind_dd\'))})"},\n        {"label": "Return ÷ max DD", "value": ratio(rtd, rtd_inf),\n         "color": PROFIT if rtd_inf or (rtd or 0) >= 2 else (LOSS if (rtd or 0) < 1 else INK),\n         "sub": f"best single strategy: {ratio(k.get(\'best_single\'), bool(k.get(\'best_single_inf\')))}"},\n    ])\n\n\ndef _strategies(doc: Doc, p: dict):\n    strats = p.get("strategies") or []\n    doc.section("Strategies", "the runs composed, with their parameters", keep=24)\n    for s in strats:\n        params = wrap(_s(s.get("params")) or "—", CW - 40, 7.0, max_lines=2, sep=" · ")\n        note = _s(s.get("note")).strip()\n        h = 5.2 + len(params) * lh(7.0) + (lh(7.0) if note else 0) + 2.2\n        doc.need(h)\n        y0 = doc.y\n        doc.rect(MX, y0 + 0.6, 3.0, 3.0, _s(s.get("color"), INK))\n        doc.text(MX + 5, y0, fit(_s(s.get("label")), 30, 9, bold=True), size=9, bold=True)\n        doc.text(MX + 5, y0 + lh(9), _s(s.get("run_id"))[:8], size=6.8, color=FAINT, mono=True)\n        yy = y0 + 0.3\n        for line in params:\n            doc.text(MX + 40, yy, line, size=7.0, color=INK)\n            yy += lh(7.0)\n        if note:\n            doc.text(MX + 40, yy, fit("Note: " + note, CW - 40, 7.0), size=7.0, color=NOTE, italic=True)\n            yy += lh(7.0)\n        doc.y = max(yy, y0 + 2 * lh(9)) + 2.2\n        doc.hline(MX, MX + CW, doc.y - 1.1, color=RULE, lw=0.4)\n\n\ndef _contribution(doc: Doc, p: dict):\n    strats = p.get("strategies") or []\n    any_taxed = bool(p.get("any_taxed"))\n    doc.section("Per-strategy contribution", keep=8 + 6 * (len(strats) + 1))\n    cols = [{"title": "Strategy", "w": 24},\n            {"title": "Net after tax" if any_taxed else "Net P&L", "w": 27, "align": "right"},\n            {"title": "Tax", "w": 21, "align": "right"},\n            {"title": "Share", "w": 15, "align": "right"},\n            {"title": "Own max DD", "w": 24, "align": "right"},\n            {"title": "Return ÷ DD", "w": 20, "align": "right"},\n            {"title": "Trades", "w": 15, "align": "right"},\n            {"title": "P&L in combined DD", "w": 32, "align": "right"}]\n    rows = []\n    for s in strats:\n        share = _num(s.get("share"))\n        rows.append([\n            {"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},\n            {"t": money(s.get("net")), "color": pnl_color(s.get("net")), "bold": True},\n            {"t": money(-(_num(s.get("tax")) or 0)) if s.get("taxed") else "—",\n             "color": LOSS if s.get("taxed") else FAINT},\n            "—" if share is None else f"{share:.0f}%",\n            {"t": inr(s.get("max_dd")), "color": LOSS},\n            ratio(s.get("rdd"), bool(s.get("rdd_inf"))),\n            _s(s.get("trades"), "—"),\n            {"t": money(s.get("dd_contrib")), "color": pnl_color(s.get("dd_contrib"))},\n        ])\n    doc.table(cols, rows)\n    dw = p.get("dd_window") or {}\n    win = f" ({dw.get(\'from_label\')} to {dw.get(\'to_label\')})" if dw.get("from_label") else ""\n    doc.y += 2\n    doc.para("“P&L in combined DD” attributes the portfolio’s worst peak-to-trough window" + win +\n             " to each strategy: the most negative cell dug the hole, a positive cell was cushioning it. "\n             "Per-strategy max DD here is exit-realized, so it can differ slightly from a run’s own Advanced tab "\n             "(entry-stepped).")\n    if p.get("any_taxed"):\n        doc.para("Where a run carries the lot-compounding tax option, each financial year’s tax is spread over "\n                 "that year’s trades pro rata, so totals match Compare Runs’ “Net after tax” and no month changes sign.")\n\n\ndef _ist_dt(ts):\n    return datetime(1970, 1, 1) + timedelta(seconds=float(ts) + IST)\n\n\ndef _equity(doc: Doc, p: dict):\n    series = [s for s in (p.get("equity") or [])\n              if isinstance(s, dict) and len(s.get("ts") or []) >= 2 and len(s.get("ts")) == len(s.get("v") or [])]\n    doc.new_page()\n    doc.section("Combined equity", "exit-realized, real time axis", keep=110)\n    if not series:\n        doc.para("No closed trades to chart.", size=8.5)\n        return\n    import warnings\n    import matplotlib.dates as mdates\n    from matplotlib.ticker import FuncFormatter\n    warnings.filterwarnings("ignore", message="AutoDateLocator was unable")   # tiny ranges only\n    h = 98.0\n    ax = doc.chart_axes(MX + 14, doc.y + 2, CW - 16, h - 18)\n    for s in sorted(series, key=lambda z: bool(z.get("thick"))):\n        xs = [_ist_dt(t) for t in s["ts"]]\n        ys = [(_num(v) or 0.0) for v in s["v"]]\n        xs.insert(0, xs[0])\n        ys.insert(0, 0.0)\n        ax.plot(xs, ys, color=_s(s.get("color"), INK), lw=2.0 if s.get("thick") else 0.9,\n                alpha=1.0 if s.get("thick") else 0.85, solid_joinstyle="round")\n    ax.axhline(0, color=MUTED, lw=0.6, ls=(0, (3, 2)), alpha=0.7)\n    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_inr(v)))\n    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))\n    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))\n    for sp in ("top", "right"):\n        ax.spines[sp].set_visible(False)\n    for sp in ("left", "bottom"):\n        ax.spines[sp].set_color(RULE)\n    ax.tick_params(labelsize=6.8, colors=MUTED, length=2.5)\n    ax.grid(axis="y", color=RULE, lw=0.4)\n    ax.set_axisbelow(True)\n    for lab in ax.get_yticklabels() + ax.get_xticklabels():\n        lab.set_family(SANS)\n    doc.y += h - 5\n    # legend row (below the date ticks)\n    x = MX\n    for s in series:\n        end = (_num(s["v"][-1]) or 0.0)\n        lab = f"{_s(s.get(\'label\'))}  {money(end)}"\n        w = text_w(lab, 7.4, bold=bool(s.get("thick"))) + 9\n        if x + w > MX + CW:\n            x = MX\n            doc.y += lh(7.4) + 1\n        doc.rect(x, doc.y + 1.0, 5, 1.6 if s.get("thick") else 1.0, _s(s.get("color"), INK))\n        doc.text(x + 6.5, doc.y, lab, size=7.4, bold=bool(s.get("thick")), color=INK)\n        x += w + 4\n    doc.y += lh(7.4) + 2\n\n\ndef _correlation(doc: Doc, p: dict):\n    strats = p.get("strategies") or []\n    corr = p.get("corr") or []\n    n = len(strats)\n    doc.section("Correlation and diversification", "daily net P&L, Pearson", keep=8 + 6.2 * (n + 1) + 30)\n    cell, lab_w = 17.0, 24.0\n    y0 = doc.y\n    # matrix header\n    for j, s in enumerate(strats):\n        doc.text(MX + lab_w + j * cell + cell / 2, y0 + 2.5, fit(_s(s.get("label")), cell - 1, 7.2, bold=True),\n                 size=7.2, bold=True, ha="center", va="center")\n    for i, si in enumerate(strats):\n        yy = y0 + 6 + i * 6.2\n        doc.rect(MX, yy + 1.6, 2.6, 2.6, _s(si.get("color"), INK))\n        doc.text(MX + 4, yy + 3.1, fit(_s(si.get("label")), lab_w - 5, 7.2, bold=True), size=7.2, bold=True,\n                 va="center")\n        for j in range(n):\n            r = None\n            try:\n                r = _num(corr[i][j])\n            except (IndexError, TypeError):\n                r = None\n            if i == j:\n                col, bg = FAINT, None\n            elif r is None:\n                col, bg = FAINT, None\n            elif r <= 0.2:\n                col, bg = PROFIT, "#e7f4ec"\n            elif r <= 0.5:\n                col, bg = WARN, "#fbf1e1"\n            else:\n                col, bg = LOSS, "#fbe9e7"\n            x = MX + lab_w + j * cell\n            if bg:\n                doc.rect(x + 0.6, yy + 0.4, cell - 1.2, 5.4, bg)\n            doc.text(x + cell / 2, yy + 3.1, "—" if r is None else f"{r:.2f}", size=7.6, mono=True,\n                     bold=i != j, color=col, ha="center", va="center")\n    mat_h = 6 + n * 6.2\n    # diversification figures to the right of the matrix\n    d = p.get("days") or {}\n    wc = d.get("worst_combined") or {}\n    xr = MX + lab_w + n * cell + 10\n    wr = MX + CW - xr\n    facts = [\n        ("Rescued days", _s(d.get("rescued"), "—"), PROFIT, "portfolio green while at least one strategy was red"),\n        ("Cluster-loss days", _s(d.get("cluster"), "—"), LOSS, "two or more strategies red the same day"),\n        ("Portfolio red days", f"{_s(d.get(\'red\'), \'—\')} of {_s(d.get(\'total\'), \'—\')}", INK,\n         f"{_s(d.get(\'green\'), \'—\')} green"),\n        ("Worst combined day", money(wc.get("total")) if wc else "—", LOSS, _s(wc.get("label"))),\n    ]\n    yy = y0\n    for label, val, col, sub in facts:\n        doc.text(xr, yy, label, size=7.0, color=MUTED)\n        doc.text(xr + wr, yy, val, size=8.6, color=col, bold=True, mono=True, ha="right")\n        if sub:\n            doc.text(xr, yy + lh(7.0), fit(sub, wr, 6.4), size=6.4, color=FAINT)\n        yy += lh(7.0) + lh(6.4) + 1.6\n    doc.y = max(y0 + mat_h, yy) + 3\n    doc.para("Computed over the union of each pair’s trading days (a day only one side traded counts as 0 for the "\n             "other). Low or negative correlation means loss days don’t coincide, which is structural drawdown "\n             "reduction; high positive correlation means the reduction may be luck of the sample.")\n\n\ndef _worst_days(doc: Doc, p: dict):\n    strats = p.get("strategies") or []\n    doc.section("Worst single days", "do losses cluster?", keep=8 + 6 * (len(strats) + 1))\n    cols = [{"title": "Strategy", "w": 40}, {"title": "Worst own day", "w": 46, "align": "right"},\n            {"title": "On", "w": 40, "align": "right"}, {"title": "Portfolio that day", "w": 52, "align": "right"}]\n    rows = []\n    for s in strats:\n        w = s.get("worst_day") or {}\n        rows.append([{"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},\n                     {"t": money(w.get("v")) if w else "—", "color": LOSS, "bold": True},\n                     {"t": _s(w.get("label"), "—"), "color": MUTED},\n                     {"t": money(w.get("portfolio_total")) if w and w.get("portfolio_total") is not None else "—",\n                      "color": pnl_color(w.get("portfolio_total")), "bold": True}])\n    doc.table(cols, rows)\n    doc.y += 2\n    doc.para("If the portfolio total on a strategy’s worst day is much better than that strategy’s own loss, the "\n             "others were absorbing it; if it is similar or worse, the bad days coincide.")\n\n\ndef _exposure(doc: Doc, p: dict):\n    e = p.get("exposure") or {}\n    doc.new_page()\n    doc.section("Exposure", "can every strategy be funded at once?", keep=30)\n    ov = _num(e.get("overlap_pct"))\n    doc.kpis([\n        {"label": "Max concurrent trades", "value": _s(e.get("max_concurrent"), "—"), "color": INK,\n         "sub": "across all strategies at once"},\n        {"label": "Max strategies in trade", "value": f"{_s(e.get(\'max_strats\'), \'—\')} of {_s(e.get(\'n_strats\'), \'—\')}",\n         "color": INK, "sub": "simultaneously holding positions"},\n        {"label": "Peak premium notional", "value": inr(e.get("peak_notional")), "color": INK,\n         "sub": "sum of entry price × qty of open trades"},\n        {"label": "Overlap of in-trade time", "value": "—" if ov is None else f"{ov:.1f}%", "color": INK,\n         "sub": f"{_s(e.get(\'t_multi_label\'))} of {_s(e.get(\'t_any_label\'))} with 2+ strategies open"},\n    ])\n    doc.para("Premium notional is the capital outlay for long option legs; for short strategies the real requirement "\n             "is exchange margin, which is larger, so treat their share here as a floor. A low time overlap means the "\n             "effective capital need is well below the sum of each strategy’s peak.")\n\n\ndef _monthly(doc: Doc, p: dict):\n    strats = p.get("strategies") or []\n    m = p.get("monthly") or {}\n    months = m.get("months") or []\n    st = m.get("stats") or {}\n    any_taxed = bool(p.get("any_taxed"))\n    n = len(strats)\n    first_w = 34.0\n    w = (CW - first_w) / (n + 1)\n    head = [{"title": f"{len(months)} months", "w": first_w}] + \\\n           [{"title": _s(s.get("label")), "w": w, "align": "right"} for s in strats] + \\\n           [{"title": "Combined", "w": w, "align": "right"}]\n\n    doc.section("Month scoreboard", "net after tax" if any_taxed else "net P&L", keep=8 + 6 * 8)\n    cols_stats = list(st.get("per") or []) + [st.get("combined") or {}]\n    any_idle = any((_num(x.get("idle")) or 0) > 0 for x in cols_stats if isinstance(x, dict))\n\n    def row(label, fn):\n        cells = [{"t": label, "mono": False, "color": MUTED}]\n        for i, x in enumerate(cols_stats):\n            c = fn(x if isinstance(x, dict) else {})\n            c = c if isinstance(c, dict) else {"t": c}\n            if i == len(cols_stats) - 1:\n                c["bold"] = True\n            cells.append(c)\n        return cells\n\n    def pct(x):\n        v = _num(x.get("pct_green"))\n        return "—" if v is None else f"{v:.0f}%"\n\n    def run(x):\n        k = int(_num(x.get("max_red_run")) or 0)\n        return {"t": f"{k} mo" if k else "0"}\n\n    def ext(key):\n        def f(x):\n            e = x.get(key) or {}\n            if not e or _num(e.get("v")) is None:\n                return "—"\n            return {"t": money(e.get("v")), "color": pnl_color(e.get("v"))}\n        return f\n\n    rows = [row("Green months", lambda x: {"t": _s(x.get("green"), "—"), "color": PROFIT}),\n            row("Red months", lambda x: {"t": _s(x.get("red"), "—"), "color": LOSS}),\n            row("% green", pct),\n            row("Longest red run", run),\n            row("Worst month", ext("worst")),\n            row("Best month", ext("best"))]\n    if any_idle:\n        rows.append(row("No-trade months", lambda x: {"t": _s(x.get("idle"), "—"), "color": FAINT}))\n    doc.table(head, rows, zebra=False)\n    runs = [(_s(s.get("label")), x.get("red_run_label")) for s, x in zip(strats, st.get("per") or [])\n            if isinstance(x, dict) and x.get("red_run_label")]\n    cr = (st.get("combined") or {}).get("red_run_label")\n    if runs or cr:\n        doc.y += 1.5\n        doc.para("Longest red runs: " + "; ".join([f"{a} {b}" for a, b in runs] + ([f"Combined {cr}"] if cr else [])) + ".")\n\n    doc.section("Month by month", "a dash means the strategy did not trade that month", keep=8 + 6 * 6)\n    mrows = []\n    for mo in months:\n        per = mo.get("per") or []\n        cnt = mo.get("n") or []\n        cells = [{"t": _s(mo.get("label")), "mono": False, "color": MUTED}]\n        for i in range(n):\n            v = per[i] if i < len(per) else None\n            traded = (cnt[i] if i < len(cnt) else 0) or 0\n            cells.append({"t": money(v), "color": pnl_color(v)} if traded else {"t": "—", "color": FAINT})\n        any_tr = any((x or 0) > 0 for x in cnt)\n        cells.append({"t": money(mo.get("total")), "color": pnl_color(mo.get("total")), "bold": True}\n                     if any_tr else {"t": "—", "color": FAINT})\n        mrows.append(cells)\n    doc.table(head, mrows, row_h=5.2, size=7.4)\n    doc.y += 2\n    doc.para("Months where one strategy’s red is covered by another’s green are the diversification working; "\n             "months where every column is red are the risk that remains.")\n\n\ndef _footers(doc: Doc, p: dict):\n    total = len(doc.pages)\n    name = _s(p.get("name")).strip() or "Untitled portfolio"\n    for i, (fig, ax) in enumerate(doc.pages, start=1):\n        ax.plot([MX, MX + CW], [BOTTOM + 4, BOTTOM + 4], color=RULE, lw=0.5)\n        ax.text(MX, BOTTOM + 6, fit(f"Scalp Terminal  |  {name}", CW * 0.6, 6.6), fontsize=6.6, color=FAINT,\n                ha="left", va="top", family=SANS)\n        ax.text(MX + CW, BOTTOM + 6, f"Page {i} of {total}", fontsize=6.6, color=FAINT, ha="right", va="top",\n                family=SANS)\n        if i == 1:\n            ax.text(MX, BOTTOM + 9.4,\n                    fit("Backtest composition of independently run strategies. P&L is booked at each trade’s exit; "\n                        "figures are simulated, net of modelled charges, and are not a forecast.", CW, 6.0),\n                    fontsize=6.0, color=FAINT, ha="left", va="top", family=SANS)\n\n\ndef render_portfolio_pdf(payload: Dict[str, Any]) -> bytes:\n    """payload → PDF bytes. Raises ValueError on a payload with no strategies."""\n    if not isinstance(payload, dict):\n        raise ValueError("payload must be an object")\n    strats = payload.get("strategies")\n    if not isinstance(strats, list) or len(strats) < 1:\n        raise ValueError("payload has no strategies")\n    import matplotlib\n    from matplotlib.backends.backend_pdf import PdfPages\n\n    with matplotlib.rc_context({"pdf.fonttype": 42, "font.family": SANS, "axes.unicode_minus": True}):\n        doc = Doc()\n        _header(doc, payload)\n        _headline(doc, payload)\n        _strategies(doc, payload)\n        _contribution(doc, payload)\n        _equity(doc, payload)\n        _correlation(doc, payload)\n        _worst_days(doc, payload)\n        _exposure(doc, payload)\n        _monthly(doc, payload)\n        _footers(doc, payload)\n        buf = io.BytesIO()\n        name = _s(payload.get("name")).strip() or "Untitled portfolio"\n        with PdfPages(buf, metadata={"Title": f"Portfolio report: {name}", "Author": "Scalp Terminal",\n                                     "Subject": "Backtest portfolio composition"}) as pdf:\n            for fig, _ax in doc.pages:\n                pdf.savefig(fig)\n        return buf.getvalue()\n'

NEW_FILES['backend/app/api/backtest_portfolio_routes.py'] = '# backend/app/api/backtest_portfolio_routes.py\n#\n# ── PF_SAVED_PDF_20261008 ── Backtest → Portfolio: saved portfolios + PDF.\n#\n#   GET    /api/backtest/portfolios               list saved portfolios (+ per-leg presence)\n#   POST   /api/backtest/portfolios               save {name, run_ids, note?, replace?}\n#                                                 409 on a name clash unless replace=true\n#   DELETE /api/backtest/portfolios/{pf_id}       delete the saved portfolio (runs untouched)\n#   POST   /api/backtest/portfolios/report.pdf    render the page\'s composed figures as a PDF\n#\n# Mounted in api_server.py with the same admin gate as the Backtest router.\n# The PDF route is layout-only: the body carries the numbers the page already\n# computed (see app/backtest/report/portfolio_pdf.py). A copy of every PDF is\n# kept under ~/.scalp-app/backtest/reports/portfolio/ so it can be found again\n# if the in-app download is dismissed.\n\nfrom __future__ import annotations\n\nimport re\nimport time\nfrom typing import List, Optional\n\nfrom fastapi import APIRouter, Body, HTTPException\nfrom fastapi.responses import Response\nfrom pydantic import BaseModel\n\nfrom app.event_bus.audit_logger import write_audit_log\n\nrouter = APIRouter(prefix="/api/backtest/portfolios", tags=["backtest"])\n\nMAX_PAYLOAD_CHARS = 8_000_000\n\n\nclass SavePortfolioRequest(BaseModel):\n    name: str\n    run_ids: List[str]\n    note: Optional[str] = None\n    replace: bool = False\n\n\n@router.get("")\ndef list_saved():\n    from app.backtest.repo.portfolio_repo import list_portfolios\n    return {"portfolios": list_portfolios()}\n\n\n@router.post("")\ndef save(req: SavePortfolioRequest):\n    from app.backtest.repo.portfolio_repo import PortfolioError, save_portfolio\n    try:\n        pf, replaced = save_portfolio(req.name, req.run_ids, note=req.note, replace=req.replace)\n    except PortfolioError as e:\n        raise HTTPException(e.status, str(e))\n    write_audit_log(f"[BACKTEST][PORTFOLIO] {\'replaced\' if replaced else \'saved\'} "\n                    f"\\"{pf[\'name\']}\\" ({len(pf[\'run_ids\'])} runs)")\n    return {"ok": True, "replaced": replaced, "portfolio": pf}\n\n\ndef _safe_name(s: str) -> str:\n    s = re.sub(r"[^0-9A-Za-z_-]+", "_", str(s or "")).strip("_")\n    return (s or "portfolio")[:60]\n\n\n@router.post("/report.pdf")\ndef report_pdf(payload: dict = Body(...)):\n    import json\n    from app.backtest.report.portfolio_pdf import render_portfolio_pdf\n    try:\n        if len(json.dumps(payload)) > MAX_PAYLOAD_CHARS:\n            raise HTTPException(413, "report payload too large")\n    except (TypeError, ValueError):\n        raise HTTPException(400, "report payload is not valid JSON")\n    try:\n        pdf = render_portfolio_pdf(payload)\n    except ValueError as e:\n        raise HTTPException(400, str(e))\n    except Exception as e:   # renderer bug → visible, never a silent empty file\n        write_audit_log(f"[BACKTEST][PORTFOLIO][PDF][ERROR] {e!r}")\n        raise HTTPException(500, f"PDF render failed: {type(e).__name__}: {e}")\n\n    stamp = time.strftime("%Y%m%d_%H%M%S")\n    per = payload.get("period") or {}\n    fname = f"portfolio_{_safe_name(payload.get(\'name\') or \'untitled\')}_{_safe_name(per.get(\'from\'))}_to_" \\\n            f"{_safe_name(per.get(\'to\'))}_{stamp}.pdf"\n    saved = ""\n    try:\n        from app.utils.app_paths import APP_HOME\n        d = APP_HOME / "backtest" / "reports" / "portfolio"\n        d.mkdir(parents=True, exist_ok=True)\n        (d / fname).write_bytes(pdf)\n        saved = str(d / fname)\n    except Exception as e:   # the download still works without the copy\n        write_audit_log(f"[BACKTEST][PORTFOLIO][PDF] copy not saved: {e!r}")\n    write_audit_log(f"[BACKTEST][PORTFOLIO][PDF] {fname} ({len(pdf)} bytes)")\n    return Response(content=pdf, media_type="application/pdf", headers={\n        "Content-Disposition": f\'attachment; filename="{fname}"\',\n        "X-Report-File": fname,\n        "X-Report-Path": saved,\n        "Access-Control-Expose-Headers": "X-Report-File, X-Report-Path, Content-Disposition",\n    })\n\n\n@router.delete("/{pf_id}")\ndef delete(pf_id: str):\n    from app.backtest.repo.portfolio_repo import delete_portfolio\n    n = delete_portfolio(pf_id)\n    if n:\n        write_audit_log(f"[BACKTEST][PORTFOLIO] deleted {pf_id}")\n    return {"ok": True, "pf_id": pf_id, "deleted": int(n)}\n'

NEW_FILES['backend/app/backtest/report/test_portfolio_saved_pdf.py'] = '# backend/app/backtest/report/test_portfolio_saved_pdf.py\n#\n# ── PF_SAVED_PDF_20261008 ── behavioural suite: saved portfolios (repo +\n# routes) and the portfolio PDF renderer. No TestClient (build-Mac httpx\n# pin) — routes are called directly and once through a hand-built ASGI scope.\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/backtest/report/test_portfolio_saved_pdf.py\n\nimport asyncio\nimport json\nimport os\nimport re\nimport sqlite3\nimport sys\nimport tempfile\nimport time\nimport zlib\nfrom pathlib import Path\n\nTMP = Path(tempfile.mkdtemp(prefix="pf_saved_pdf_"))\nos.environ.setdefault("SCALP_APP_HOME", str(TMP / "home"))\n\nimport app.backtest.repo.portfolio_repo as PR               # noqa: E402\nfrom app.backtest.report.portfolio_pdf import (             # noqa: E402\n    inr, money, compact_inr, render_portfolio_pdf, text_w)\n\nFAILS, OK = [], [0]\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if cond:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nDB = TMP / "backtest.db"\nPR._db_path = lambda: DB                                      # every repo call → temp DB\n\n\ndef seed_runs():\n    c = sqlite3.connect(str(DB))\n    c.execute("""CREATE TABLE IF NOT EXISTS backtest_runs (run_id TEXT PRIMARY KEY, strategy_id TEXT,\n                 underlying TEXT, date_from TEXT, date_to TEXT, status TEXT)""")\n    rows = [("r_vet", "VET_V1", "2020-01-01", "2026-08-07", "done"),\n            ("r_tsg", "TSG_V1", "2020-01-01", "2026-08-07", "done"),\n            ("r_ic2", "IC_V2", "2020-01-01", "2026-08-07", "done"),\n            ("r_tma", "TMA_V2", "2020-01-01", "2026-08-07", "done"),\n            ("r_orb", "ORB_V1", "2020-01-01", "2026-08-07", "done"),\n            ("r_v1", "SCALP_V1", "2020-01-01", "2026-08-07", "done"),\n            ("r_vet2", "VET_V1", "2020-01-01", "2026-08-07", "done"),\n            ("r_short", "BRK_V1", "2021-01-01", "2026-08-07", "done"),\n            ("r_run", "HA_V1", "2020-01-01", "2026-08-07", "running")]\n    c.executemany("INSERT OR REPLACE INTO backtest_runs VALUES (?, ?, \'NIFTY\', ?, ?, ?)", rows)\n    c.commit()\n    c.close()\n\n\ndef raises(fn, status=None, text=None):\n    try:\n        fn()\n    except PR.PortfolioError as e:\n        return (status is None or e.status == status) and (text is None or text in str(e))\n    return False\n\n\ndef repo_suite():\n    print("\\n── saved portfolios: repo ──")\n    seed_runs()\n    pf, rep = PR.save_portfolio("  Core   four ", ["r_vet", "r_tsg", "r_ic2", "r_tma"])\n    check("save: name whitespace normalised, legs in given order",\n          pf["name"] == "Core four" and pf["run_ids"] == ["r_vet", "r_tsg", "r_ic2", "r_tma"] and not rep)\n    check("save: legs carry strategy + period and are present",\n          [l["strategy_id"] for l in pf["legs"]] == ["VET_V1", "TSG_V1", "IC_V2", "TMA_V2"]\n          and all(l["present"] for l in pf["legs"]) and pf["date_from"] == "2020-01-01" and pf["missing"] == 0)\n    check("clash: same name (case-insensitive) without replace → 409",\n          raises(lambda: PR.save_portfolio("CORE FOUR", ["r_vet", "r_tsg"]), 409, "already exists"))\n    pf2, rep2 = PR.save_portfolio("core four", ["r_vet", "r_orb"], replace=True)\n    check("replace: same pf_id, runs swapped, flagged replaced",\n          pf2["pf_id"] == pf["pf_id"] and pf2["run_ids"] == ["r_vet", "r_orb"] and rep2)\n    check("replace keeps created_at, bumps nothing else odd",\n          pf2["created_at"] == pf["created_at"] and pf2["updated_at"] >= pf["updated_at"])\n    check("1 run rejected", raises(lambda: PR.save_portfolio("x", ["r_vet"]), 400, "at least 2"))\n    check("duplicate ids collapse → still needs 2", raises(lambda: PR.save_portfolio("x", ["r_vet", "r_vet"]), 400))\n    check("two runs of one strategy rejected",\n          raises(lambda: PR.save_portfolio("x", ["r_vet", "r_vet2"]), 400, "One run per strategy"))\n    check("mismatched date range rejected",\n          raises(lambda: PR.save_portfolio("x", ["r_vet", "r_short"]), 400, "same date range"))\n    check("unfinished run rejected", raises(lambda: PR.save_portfolio("x", ["r_vet", "r_run"]), 400, "not done"))\n    check("unknown run → 404", raises(lambda: PR.save_portfolio("x", ["r_vet", "nope"]), 404, "not found"))\n    check("more than 5 rejected",\n          raises(lambda: PR.save_portfolio("x", ["r_vet", "r_tsg", "r_ic2", "r_tma", "r_orb", "r_v1"]), 400, "at most"))\n    check("blank name rejected", raises(lambda: PR.save_portfolio("   ", ["r_vet", "r_tsg"]), 400, "name"))\n    check("over-long name rejected", raises(lambda: PR.save_portfolio("n" * 81, ["r_vet", "r_tsg"]), 400, "too long"))\n    check("bad run_ids type rejected", raises(lambda: PR.save_portfolio("x", "r_vet,r_tsg"), 400))\n    PR.save_portfolio("alpha", ["r_tsg", "r_ic2"], note="  try  ")\n    lst = PR.list_portfolios()\n    check("list: alphabetical (case-insensitive)", [p["name"] for p in lst] == ["alpha", "core four"])\n    check("note trimmed", lst[0]["note"] == "try")\n    c = sqlite3.connect(str(DB))\n    c.execute("DELETE FROM backtest_runs WHERE run_id = \'r_orb\'")\n    c.commit()\n    c.close()\n    core = [p for p in PR.list_portfolios() if p["name"] == "core four"][0]\n    check("a run deleted later → leg present=False, missing=1 (portfolio kept)",\n          core["missing"] == 1 and [l["present"] for l in core["legs"]] == [True, False])\n    check("delete: removes the portfolio only", PR.delete_portfolio(core["pf_id"]) == 1\n          and [p["name"] for p in PR.list_portfolios()] == ["alpha"])\n    c = sqlite3.connect(str(DB))\n    n_runs = c.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]\n    c.close()\n    check("...and never touches backtest_runs", n_runs == 8)\n    check("delete unknown id → 0, no error", PR.delete_portfolio("zzz") == 0)\n\n\n# ───────────────────────── PDF ─────────────────────────\nACC = ["#f97316", "#d946ef", "#818cf8", "#c084fc", "#f59e0b"]\n\n\ndef payload(n=4, months=80, pts=1500, taxed=True, with_eq=True, name="Core four"):\n    t0 = 1577836800 + 4 * 3600\n    labels = ["VET", "TSG", "IC2", "TMA2", "ORB"][:n]\n    strats = []\n    eq = []\n    for i in range(n):\n        strats.append({"sid": labels[i], "label": labels[i], "color": ACC[i], "run_id": f"abcdef{i}9999",\n                       "params": "Signal EMA10/20 · SMA36 ± ATR36×0.618 · TF 5m · Leg option SELLING · Hedge window "\n                                 "09:20–15:15 · Lots 10 · " * 3,\n                       "note": "5L - Non-Expiry only" if i == 0 else None,\n                       "net": 4556945 - i * 1100000, "pre_tax_net": 4556945, "tax": 120000 if taxed else 0,\n                       "taxed": taxed and i < 2, "max_dd": 302620 + i * 1000, "rdd": 15.06 if i else None,\n                       "rdd_inf": i == 0, "trades": 2462 + i, "share": 33.0 - i,\n                       "dd_contrib": -234220 + i * 90000,\n                       "worst_day": {"label": "11 Apr 24", "v": -98000 - i, "portfolio_total": -40000 + i * 30000}})\n        if with_eq:\n            ts = [t0 + k * 86400 for k in range(pts)]\n            v = [k * (900 + i * 200) - (k % 37) * 3000 for k in range(pts)]\n            eq.append({"label": labels[i], "color": ACC[i], "thick": False, "ts": ts, "v": v})\n    if with_eq:\n        eq.append({"label": "Portfolio", "color": "#6d28d9", "thick": True, "ts": eq[0]["ts"],\n                   "v": [sum(e["v"][k] for e in eq) for k in range(pts)]})\n    ms = []\n    for k in range(months):\n        y, m = 2020 + k // 12, k % 12 + 1\n        per = [((-1) ** (k + j)) * (40000 + 1000 * j) for j in range(n)]\n        ms.append({"label": f"{[\'Jan\',\'Feb\',\'Mar\',\'Apr\',\'May\',\'Jun\',\'Jul\',\'Aug\',\'Sep\',\'Oct\',\'Nov\',\'Dec\'][m-1]} {y}",\n                   "per": per, "n": [0 if (k == 3 and j == 1) else 5 for j in range(n)], "total": sum(per)})\n    stat = {"green": 50, "red": 28, "idle": 2, "pct_green": 64.1, "max_red_run": 3,\n            "red_run_label": "Mar 2022 to May 2022", "worst": {"v": -210000, "label": "May 2022"},\n            "best": {"v": 640000, "label": "Jun 2024"}}\n    return {\n        "v": 1, "name": name, "period": {"from": "2020-01-01", "to": "2026-08-07"},\n        "generated_label": "08 Oct 2026, 07:45 IST", "basis": "after", "any_taxed": taxed,\n        "any_tax_available": taxed, "combined_tax": 240000,\n        "strategies": strats,\n        "kpis": {"combined_net": 13729110, "combined_max_dd": 371107, "dd_reduction": 58.9, "sum_ind_dd": 903256,\n                 "return_to_dd": 37.0, "return_to_dd_inf": False, "best_single": 19.27, "best_single_inf": False},\n        "dd_window": {"from_label": "11/04, 09:16", "to_label": "08/05, 09:24"},\n        "corr": [[1 if a == b else (0.12 if (a + b) % 2 else 0.61) for b in range(n)] for a in range(n)],\n        "days": {"rescued": 412, "cluster": 230, "red": 560, "green": 980, "total": 1600,\n                 "worst_combined": {"label": "04 Jun 24", "total": -310000}},\n        "exposure": {"max_concurrent": 9, "max_strats": n, "n_strats": n, "peak_notional": 1840000,\n                     "overlap_pct": 41.3, "t_multi_label": "812h 5m", "t_any_label": "1960h 40m"},\n        "monthly": {"months": ms, "stats": {"per": [stat] * n, "combined": stat}},\n        "equity": eq,\n    }\n\n\ndef pdf_pages(b: bytes) -> int:\n    return len(re.findall(rb"/Type\\s*/Page[^s]", b))\n\n\ndef pdf_text_blob(b: bytes) -> bytes:\n    out = b""\n    for m in re.finditer(rb"stream\\r?\\n(.*?)\\r?\\nendstream", b, re.S):\n        try:\n            out += zlib.decompress(m.group(1))\n        except Exception:\n            pass\n    return out\n\n\ndef pdf_suite():\n    print("\\n── portfolio PDF renderer ──")\n    check("inr: Indian grouping like fmtInr", inr(13729110) == "₹1,37,29,110" and inr(-302620) == "₹3,02,620"\n          and inr(999) == "₹999" and inr(1000) == "₹1,000" and inr(None) == "—")\n    check("money: sign + grouping", money(4556945) == "+₹45,56,945" and money(-24994) == "−₹24,994")\n    check("compact axis labels", compact_inr(13729110) == "₹1.37 Cr" and compact_inr(4556945) == "₹45.6 L"\n          and compact_inr(-12000) == "−₹12k")\n    check("₹ glyph has width in DejaVu (not a missing-glyph box)", text_w("₹", 10) > 0.5)\n\n    t = time.time()\n    b = render_portfolio_pdf(payload())\n    dt = time.time() - t\n    check("4-strategy, 80-month, 1500-pt payload renders a PDF", b[:5] == b"%PDF-" and len(b) > 20000)\n    np_ = pdf_pages(b)\n    check("multi-page layout (≥ 4 pages: overview, equity, exposure, months)", np_ >= 4, f"pages={np_}")\n    check("fonts embedded as TrueType (pdf.fonttype 42)", b"/FontFile2" in b)\n    check("DejaVu embedded", b"DejaVu" in b)\n    check(f"render time sane ({dt:.1f}s < 20s)", dt < 20)\n    (TMP / "sample_report.pdf").write_bytes(b)\n\n    b5 = render_portfolio_pdf(payload(n=5, months=12, pts=50, taxed=False, name=""))\n    check("5 strategies, untaxed, unnamed → renders", b5[:5] == b"%PDF-" and pdf_pages(b5) >= 3)\n    b2 = render_portfolio_pdf(payload(n=2, months=1, pts=2, with_eq=False))\n    check("2 strategies, no equity points → renders (no-chart message)", b2[:5] == b"%PDF-")\n    p = payload(n=3, months=200, pts=10)\n    b3 = render_portfolio_pdf(p)\n    check("200 months paginate onto extra pages", pdf_pages(b3) > pdf_pages(render_portfolio_pdf(payload(n=3, months=10, pts=10))))\n    ugly = payload(n=2, months=3, pts=3)\n    ugly["kpis"] = {"combined_net": None, "combined_max_dd": "x", "dd_reduction": None, "return_to_dd": None,\n                    "return_to_dd_inf": True}\n    ugly["strategies"][0].update({"net": None, "max_dd": None, "worst_day": None, "params": None, "color": None})\n    ugly["corr"] = [[1]]                       # short matrix\n    ugly["monthly"] = {}\n    ugly["exposure"] = None\n    ugly["days"] = None\n    check("missing / malformed fields never crash the renderer", render_portfolio_pdf(ugly)[:5] == b"%PDF-")\n    bad = 0\n    for junk in (None, [], {}, {"strategies": []}):\n        try:\n            render_portfolio_pdf(junk)\n        except ValueError:\n            bad += 1\n    check("payload without strategies → ValueError (route maps to 400)", bad == 4)\n\n\n# ───────────────────────── routes ─────────────────────────\ndef asgi_call(app, method, path, body=None):\n    raw = json.dumps(body).encode() if body is not None else b""\n    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,\n             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",\n             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),\n                         (b"host", b"test")],\n             "client": ("127.0.0.1", 5), "server": ("test", 80), "root_path": ""}\n    out = {"status": None, "headers": {}, "body": b""}\n    sent = [False]\n\n    async def receive():\n        if not sent[0]:\n            sent[0] = True\n            return {"type": "http.request", "body": raw, "more_body": False}\n        return {"type": "http.disconnect"}\n\n    async def send(msg):\n        if msg["type"] == "http.response.start":\n            out["status"] = msg["status"]\n            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}\n        elif msg["type"] == "http.response.body":\n            out["body"] += msg.get("body", b"")\n\n    asyncio.run(app(scope, receive, send))\n    return out\n\n\ndef route_suite():\n    print("\\n── routes (direct + hand-built ASGI scope) ──")\n    from fastapi import FastAPI, HTTPException\n    import app.api.backtest_portfolio_routes as R\n    import app.utils.app_paths as AP\n    AP.APP_HOME = TMP / "home"\n\n    r = R.save(R.SavePortfolioRequest(name="Route pf", run_ids=["r_vet", "r_tsg", "r_ic2"]))\n    check("POST save → ok + portfolio", r["ok"] and r["portfolio"]["name"] == "Route pf" and not r["replaced"])\n    try:\n        R.save(R.SavePortfolioRequest(name="route PF", run_ids=["r_vet", "r_tsg"]))\n        code = None\n    except HTTPException as e:\n        code = e.status_code\n    check("name clash → HTTP 409", code == 409)\n    r2 = R.save(R.SavePortfolioRequest(name="route PF", run_ids=["r_vet", "r_tsg"], replace=True))\n    check("replace=true → replaced", r2["replaced"] and r2["portfolio"]["run_ids"] == ["r_vet", "r_tsg"])\n    try:\n        R.save(R.SavePortfolioRequest(name="bad", run_ids=["r_vet", "r_vet2"]))\n        code = None\n    except HTTPException as e:\n        code = e.status_code\n    check("invalid composition → HTTP 400", code == 400)\n\n    app = FastAPI()\n    app.include_router(R.router)\n    res = asgi_call(app, "GET", "/api/backtest/portfolios")\n    names = [p["name"] for p in json.loads(res["body"])["portfolios"]] if res["status"] == 200 else []\n    check("ASGI GET /api/backtest/portfolios → 200 list", res["status"] == 200 and "route PF" in names)\n    res = asgi_call(app, "POST", "/api/backtest/portfolios/report.pdf", payload(n=3, months=24, pts=200, name="My/Mix 3"))\n    check("ASGI POST report.pdf → 200 application/pdf bytes",\n          res["status"] == 200 and res["headers"].get("content-type", "").startswith("application/pdf")\n          and res["body"][:5] == b"%PDF-")\n    fn = res["headers"].get("x-report-file", "")\n    check("filename is sanitised and dated", fn.startswith("portfolio_My_Mix_3_2020-01-01_to_2026-08-07_") and fn.endswith(".pdf"),\n          fn)\n    saved = res["headers"].get("x-report-path", "")\n    check("a copy is kept under backtest/reports/portfolio", saved and Path(saved).exists()\n          and Path(saved).parent == TMP / "home" / "backtest" / "reports" / "portfolio")\n    check("Content-Disposition is attachment", "attachment" in res["headers"].get("content-disposition", ""))\n    res = asgi_call(app, "POST", "/api/backtest/portfolios/report.pdf", {"strategies": []})\n    check("empty payload → 400 (not a blank PDF)", res["status"] == 400)\n    pid = r2["portfolio"]["pf_id"]\n    res = asgi_call(app, "DELETE", f"/api/backtest/portfolios/{pid}")\n    check("ASGI DELETE → deleted 1", res["status"] == 200 and json.loads(res["body"])["deleted"] == 1)\n    res = asgi_call(app, "DELETE", f"/api/backtest/portfolios/{pid}")\n    check("repeat DELETE is idempotent (deleted 0)", res["status"] == 200 and json.loads(res["body"])["deleted"] == 0)\n\n\ndef wiring_suite():\n    print("\\n── wiring ──")\n    here = Path(__file__).resolve()\n    backend = here.parents[3]\n    src = (backend / "app" / "api_server.py").read_text(encoding="utf-8")\n    i_bt = src.find("app.include_router(backtest_router")\n    i_pf = src.find("app.include_router(backtest_portfolio_router, dependencies=[Depends(_require_admin_ui)])")\n    i_mount = src.find("SCALP_UI_SERVE")\n    check("api_server mounts the portfolio router with the admin gate, after the Backtest router",\n          0 < i_bt < i_pf and (i_mount == -1 or i_pf < i_mount))\n\n\nif __name__ == "__main__":\n    repo_suite()\n    pdf_suite()\n    route_suite()\n    wiring_suite()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} PORTFOLIO SAVED/PDF CHECKS PASSED  (sample PDF: {TMP / \'sample_report.pdf\'})")\n'


# ── edits ──
PF = "frontend/src/pages/backtest/Portfolio.jsx"
API = "backend/app/api_server.py"
EDITS = []

# ── api_server.py: mount the router (admin gate, right after the Backtest router)
EDITS.append((API,
"""app.include_router(backtest_router, dependencies=[Depends(_require_admin_ui)])
""",
"""app.include_router(backtest_router, dependencies=[Depends(_require_admin_ui)])
# ── PF_SAVED_PDF_20261008 ── saved portfolios + portfolio PDF (/api/backtest/portfolios/*), same admin gate
from app.api.backtest_portfolio_routes import router as backtest_portfolio_router
app.include_router(backtest_portfolio_router, dependencies=[Depends(_require_admin_ui)])
"""))

# ── Portfolio.jsx ─────────────────────────────────────────────────────────
EDITS.append((PF,
"""import { fyTaxOf } from "./lotCompounding";   // ── PF_NET_AFTER_TAX_20260930 ── fallback when a run has no stamped schedule
""",
"""import { fyTaxOf } from "./lotCompounding";   // ── PF_NET_AFTER_TAX_20260930 ── fallback when a run has no stamped schedule
import { getApiBase } from "../../api/base";   // ── PF_SAVED_PDF_20261008 ── raw fetch for the PDF bytes
"""))

EDITS.append((PF,
"""/* ── PF_COMPOSE END ── */
""",
"""/* ── PF_COMPOSE END ── */

/* ── PF_SAVED_PDF_20261008 BEGIN ── the PDF report payload. Every number is
   taken from the composed `pf` exactly as the page renders it (same basis,
   same tax spreading, same exit-realized DD), and labels are formatted here
   with the page's own formatters — the backend only lays it out. So the PDF
   can never disagree with the screen. Infinity travels as null + *_inf. */
export function buildPortfolioReportPayload(pf, ctx = {}) {
  const fin = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const lab = (sid) => STRAT_LABEL[sid] || sid;
  const fmtTsL = ctx.fmtTs || ((x) => String(x));
  const rddOf = (s) => (s.maxDD > 0 ? s.net / s.maxDD : (s.net > 0 ? Infinity : 0));
  const best = pf.perStrat.length ? Math.max(...pf.perStrat.map(rddOf)) : null;
  const down = (pts, max = 1200) => {
    const step = pts.length > max ? Math.ceil(pts.length / max) : 1;
    const kept = pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
    return { ts: kept.map((p) => p.ts), v: kept.map((p) => fin(p.value) ?? 0) };
  };
  const stat = (x) => ({
    green: x.green, red: x.red, idle: x.idle, pct_green: fin(x.pctGreen), max_red_run: x.maxRedRun,
    red_run_label: x.maxRedRun ? `${fmtMonthLabel(x.redRunFrom)} to ${fmtMonthLabel(x.redRunTo)}` : null,
    worst: x.worst ? { v: fin(x.worst.v), label: fmtMonthLabel(x.worst.m) } : null,
    best: x.best ? { v: fin(x.best.v), label: fmtMonthLabel(x.best.m) } : null,
  });
  const strategies = pf.perStrat.map((s, i) => {
    const sid = s.run.strategy_id;
    const rdd = rddOf(s);
    const wi = pf.worstIndividual[i];
    const dayRow = wi ? pf.days.find((d) => d.day === wi.k) : null;
    let params = "";
    try {
      params = ctx.describeConfig ? ctx.describeConfig(s.run.config).map(([k, v]) => `${k} ${v}`).join(" · ") : "";
    } catch { params = ""; }
    return {
      sid, label: lab(sid), color: ACCENT[sid] || "#6b6680", run_id: s.run.run_id,
      params, note: s.run.note || null,
      net: fin(s.net), pre_tax_net: fin(s.preTaxNet), tax: fin(s.tax), taxed: !!s.taxed,
      max_dd: fin(s.maxDD), rdd: rdd === Infinity ? null : fin(rdd), rdd_inf: rdd === Infinity,
      trades: s.tradeCount,
      share: pf.combinedNet !== 0 ? fin((s.net / pf.combinedNet) * 100) : null,
      dd_contrib: fin(pf.ddContrib[i]),
      worst_day: wi ? { label: fmtDayLabel(wi.k), v: fin(wi.v), portfolio_total: dayRow ? fin(dayRow.total) : null } : null,
    };
  });
  return {
    v: 1,
    name: ctx.name || strategies.map((s) => s.label).join(" + "),
    period: ctx.period || {},
    generated_label: ctx.generatedLabel || `${new Date().toLocaleString("en-IN", {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
      timeZone: "Asia/Kolkata" })} IST`,
    basis: pf.afterTax ? "after" : "before",
    any_taxed: !!pf.anyTaxed, any_tax_available: !!pf.anyTaxAvailable, combined_tax: fin(pf.combinedTax),
    strategies,
    kpis: {
      combined_net: fin(pf.combinedNet), combined_max_dd: fin(pf.combinedMaxDD),
      dd_reduction: fin(pf.ddReduction), sum_ind_dd: fin(pf.sumIndDD),
      return_to_dd: pf.returnToDD === Infinity ? null : fin(pf.returnToDD), return_to_dd_inf: pf.returnToDD === Infinity,
      best_single: best === Infinity ? null : fin(best), best_single_inf: best === Infinity,
    },
    dd_window: pf.ddPeakTs ? { from_label: fmtTsL(pf.ddPeakTs), to_label: fmtTsL(pf.ddTroughTs) } : null,
    corr: pf.corr.map((row) => row.map(fin)),
    days: {
      rescued: pf.rescuedDays, cluster: pf.clusterDays, red: pf.redDays, green: pf.greenDays, total: pf.days.length,
      worst_combined: pf.worstCombinedDay ? { label: fmtDayLabel(pf.worstCombinedDay.day), total: fin(pf.worstCombinedDay.total) } : null,
    },
    exposure: {
      max_concurrent: pf.exposure.maxConcurrent, max_strats: pf.exposure.maxStrats, n_strats: pf.perStrat.length,
      peak_notional: fin(pf.exposure.peakNotional), overlap_pct: fin(pf.exposure.overlapPct),
      t_multi_label: fmtDurS(pf.exposure.tMulti), t_any_label: fmtDurS(pf.exposure.tAny),
    },
    monthly: {
      months: pf.monthly.map((m) => ({ label: fmtMonthLabel(m.month), per: m.per.map(fin), n: m.n, total: fin(m.total) })),
      stats: { per: pf.monthlyStats.per.map(stat), combined: stat(pf.monthlyStats.combined) },
    },
    equity: [
      ...pf.perStrat.map((s) => ({ label: lab(s.run.strategy_id), color: ACCENT[s.run.strategy_id] || "#6b6680",
        thick: false, ...down(s.curve) })),
      { label: "Portfolio", color: "#6d28d9", thick: true, ...down(pf.combined) },
    ].filter((s) => s.ts.length >= 2),
  };
}
// FastAPI errors arrive as '{"detail":"…"}' text inside Error.message
export function errDetail(e) {
  const t = String((e && e.message) || e || "");
  try { const j = JSON.parse(t); if (j && j.detail) return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not JSON */ }
  return t;
}
/* ── PF_SAVED_PDF_20261008 END ── */
"""))

# state
EDITS.append((PF,
"""  const [pfGroups, setPfGroups] = useState([]);      // from PF: queue labels
""",
"""  const [pfGroups, setPfGroups] = useState([]);      // from PF: queue labels
  // ── PF_SAVED_PDF_20261008 ── saved portfolios (backtest.db) + PDF
  const [savedPfs, setSavedPfs] = useState([]);
  const [activeSavedId, setActiveSavedId] = useState(null);
  const [extraRuns, setExtraRuns] = useState([]);    // saved legs older than the latest-200 list
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveConflict, setSaveConflict] = useState(null);
  const [savedBusy, setSavedBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [actMsg, setActMsg] = useState(null);        // feedback next to Save / PDF
  const [confirmDel, setConfirmDel] = useState(null); // pf_id awaiting the second click
"""))

# reload also lists saved portfolios
EDITS.append((PF,
"""        .filter((g) => g.ids.length >= 2 && g.ids.length <= MAX_PF));
    } catch { setPfGroups([]); }
  }, [apiCall]);
""",
"""        .filter((g) => g.ids.length >= 2 && g.ids.length <= MAX_PF));
    } catch { setPfGroups([]); }
    // ── PF_SAVED_PDF_20261008 ── saved portfolios (best-effort, like PF groups)
    try {
      const sp = await apiCall(`/api/backtest/portfolios`);
      setSavedPfs(sp.portfolios || []);
    } catch { /* older backend or offline — the row just stays empty */ }
  }, [apiCall]);
"""))

# favourites filter narrows the picker only; composition reads all done runs
EDITS.append((PF,
"""  const doneRuns = useMemo(() => {
    const done = runs.filter((r) => (r.status || "done") === "done" && (!favOnly || r.favourite));
    return done.map((r, i) => [r, i]).sort((a, b) => ((b[0].favourite ? 1 : 0) - (a[0].favourite ? 1 : 0)) || (a[1] - b[1])).map(([r]) => r);
  }, [runs, favOnly]);
""",
"""  // ── PF_SAVED_PDF_20261008 ── allRuns = latest-200 list + saved legs fetched by id
  const allRuns = useMemo(() => {
    if (!extraRuns.length) return runs;
    const have = new Set(runs.map((r) => r.run_id));
    return [...runs, ...extraRuns.filter((r) => !have.has(r.run_id))];
  }, [runs, extraRuns]);
  const allDoneSorted = useMemo(() => {
    const done = allRuns.filter((r) => (r.status || "done") === "done");
    return done.map((r, i) => [r, i]).sort((a, b) => ((b[0].favourite ? 1 : 0) - (a[0].favourite ? 1 : 0)) || (a[1] - b[1])).map(([r]) => r);
  }, [allRuns]);
  const doneRuns = useMemo(() => (favOnly ? allDoneSorted.filter((r) => r.favourite) : allDoneSorted), [allDoneSorted, favOnly]);
"""))

EDITS.append((PF,
"""  const selRuns = useMemo(() => doneRuns.filter((r) => selected.has(r.run_id)), [doneRuns, selected]);
""",
"""  // ── PF_SAVED_PDF_20261008 ── from ALL done runs: the Favourites filter narrows the picker, never the
  // composition (a saved or staged portfolio with an unstarred leg used to drop that leg silently)
  const selRuns = useMemo(() => allDoneSorted.filter((r) => selected.has(r.run_id)), [allDoneSorted, selected]);
"""))

EDITS.append((PF,
"""    setActivePfName(null);   // manual change → no longer "the" named portfolio
""",
"""    setActivePfName(null);   // manual change → no longer "the" named portfolio
    setActiveSavedId(null);  // ── PF_SAVED_PDF_20261008 ──
    setSaveOpen(false); setSaveConflict(null); setActMsg(null);
"""))

EDITS.append((PF,
"""    setMsg(null);
    setSelected(new Set(g.ids));
    setActivePfName(g.name);
""",
"""    setMsg(null);
    setSelected(new Set(g.ids));
    setActivePfName(g.name);
    setActiveSavedId(null); setSaveOpen(false); setSaveConflict(null); setActMsg(null);   // ── PF_SAVED_PDF_20261008 ──
"""))

# callbacks
EDITS.append((PF,
"""  /* ── PF_DELETE END ── */
""",
"""  /* ── PF_DELETE END ── */

  /* ── PF_SAVED_PDF_20261008 BEGIN ── saved portfolios: open / save / delete,
     and the PDF download. Opening resolves every leg BY ID (a leg older than
     the latest-200 list is fetched directly, trades included), and refuses
     loudly if any leg is gone — never a silent partial portfolio (same rule
     as PF_SWITCH_FIX). Deleting a saved portfolio never touches its runs. */
  const refreshSaved = useCallback(async () => {
    try { const sp = await apiCall(`/api/backtest/portfolios`); setSavedPfs(sp.portfolios || []); }
    catch { /* keep the current list */ }
  }, [apiCall]);

  const openSaved = useCallback(async (p) => {
    setMsg({ kind: "info", text: `Opening "${p.name}"…` });
    let fresh = runs;
    try { const d = await apiCall(`/api/backtest/runs?limit=200`); fresh = d.runs || []; setRuns(fresh); }
    catch { /* offline blip — fall back to the in-memory list */ }
    const have = new Map([...fresh, ...extraRuns].map((r) => [r.run_id, r]));
    const extra = [], trades = {}, gone = [];
    for (const id of p.run_ids) {
      if (have.has(id)) continue;
      try {
        const d = await apiCall(`/api/backtest/runs/${id}`);
        const { trades: tr, ...meta } = d;
        extra.push(meta);
        trades[id] = tr || [];
      } catch { gone.push(id); }
    }
    if (gone.length) {
      setMsg({ kind: "err", text: `"${p.name}": ${gone.length} of ${p.run_ids.length} runs can't be loaded (deleted in Compare Runs?). Composing a partial portfolio would be misleading; select replacement runs and save it again under the same name.` });
      refreshSaved();
      return;
    }
    const notDone = p.run_ids.filter((id) => ((have.get(id) || extra.find((r) => r.run_id === id) || {}).status || "done") !== "done");
    if (notDone.length) {
      setMsg({ kind: "err", text: `"${p.name}": ${notDone.length} run(s) are not finished.` });
      return;
    }
    if (extra.length) setExtraRuns((xs) => [...xs.filter((r) => !extra.some((e) => e.run_id === r.run_id)), ...extra]);
    if (Object.keys(trades).length) setDetail((s) => ({ ...s, ...trades }));
    setMsg(null); setActMsg(null); setSaveOpen(false); setSaveConflict(null);
    setSelected(new Set(p.run_ids));
    setActivePfName(p.name);
    setActiveSavedId(p.pf_id);
    setPickerOpen(false);
    setTab("overview");
  }, [apiCall, runs, extraRuns, refreshSaved]);

  const savePortfolio = useCallback(async (replace) => {
    const name = saveName.trim();
    if (!name) { setActMsg({ kind: "err", text: "Give the portfolio a name." }); return; }
    setSavedBusy(true);
    try {
      const r = await apiCall(`/api/backtest/portfolios`, {
        method: "POST",
        body: JSON.stringify({ name, run_ids: selRuns.map((x) => x.run_id), replace: !!replace }),
      });
      setActiveSavedId(r.portfolio.pf_id);
      setActivePfName(r.portfolio.name);
      setSaveOpen(false); setSaveConflict(null);
      setActMsg({ kind: "ok", text: `${r.replaced ? "Updated" : "Saved"} "${r.portfolio.name}". Open it any time from Saved portfolios at the top.` });
      refreshSaved();
    } catch (e) {
      const t = errDetail(e);
      if (/already exists/i.test(t)) setSaveConflict(name);
      else setActMsg({ kind: "err", text: `Couldn't save: ${t}` });
    } finally {
      setSavedBusy(false);
    }
  }, [apiCall, saveName, selRuns, refreshSaved]);

  const deleteSaved = useCallback(async (p) => {
    if (confirmDel !== p.pf_id) {
      setConfirmDel(p.pf_id);
      setTimeout(() => setConfirmDel((cur) => (cur === p.pf_id ? null : cur)), 4000);
      return;
    }
    setConfirmDel(null);
    try {
      await apiCall(`/api/backtest/portfolios/${p.pf_id}`, { method: "DELETE" });
      if (activeSavedId === p.pf_id) setActiveSavedId(null);
      setMsg({ kind: "ok", text: `Deleted saved portfolio "${p.name}". Its backtest runs are kept.` });
    } catch (e) {
      setMsg({ kind: "err", text: `Couldn't delete "${p.name}": ${errDetail(e)}` });
    }
    refreshSaved();
  }, [apiCall, confirmDel, activeSavedId, refreshSaved]);
  /* ── PF_SAVED_PDF_20261008 END ── */
"""))

# saved row in the launch card (always visible)
EDITS.append((PF,
"""        {pfGroups.length > 0 && (
          <div style={{ marginTop: spacing.md, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, color: c.text.muted }}>Finished portfolios:</span>
""",
"""        {/* ── PF_SAVED_PDF_20261008 ── saved portfolios: one click to open */}
        <div data-testid="pf-saved-row" style={{ marginTop: spacing.md, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: c.text.muted }}>Saved portfolios:</span>
          {savedPfs.length === 0 && (
            <span style={{ fontSize: 11, color: c.text.tertiary }}>
              none yet. Select runs below, then use Save portfolio above the results.
            </span>
          )}
          {savedPfs.map((p) => {
            const legs = (p.legs || []).map((l) => STRAT_LABEL[l.strategy_id] || l.strategy_id).join(" · ");
            const broken = (p.missing || 0) > 0;
            const arming = confirmDel === p.pf_id;
            return (
              <span key={p.pf_id} style={{ display: "inline-flex", alignItems: "stretch" }}>
                <button
                  style={{ ...chipBtn(activeSavedId === p.pf_id, false),
                    borderTopRightRadius: 0, borderBottomRightRadius: 0,
                    ...(broken ? { borderColor: c.loss } : {}) }}
                  onClick={() => openSaved(p)}
                  title={`${p.name}: ${legs} · ${p.date_from} → ${p.date_to}` +
                    (broken ? `\\n${p.missing} run(s) no longer exist — re-save with replacements` : "")}>
                  {p.name}
                  <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 600, color: broken ? c.loss : c.text.muted }}>
                    {broken ? `${p.missing} missing` : legs}
                  </span>
                </button>
                <button
                  style={{ ...chipBtn(false, false), borderTopLeftRadius: 0, borderBottomLeftRadius: 0, borderLeft: "none",
                    color: arming ? "#fff" : c.loss, background: arming ? c.loss : c.bg.secondary, padding: "6px 9px" }}
                  onClick={() => deleteSaved(p)}
                  title={arming ? "Click again to delete" : `Delete saved portfolio "${p.name}" (its runs are kept)`}>
                  {arming ? "Delete?" : "✕"}
                </button>
              </span>
            );
          })}
        </div>
        {pfGroups.length > 0 && (
          <div style={{ marginTop: spacing.md, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, color: c.text.muted }}>Finished portfolios:</span>
"""))

# actions row in the results header
EDITS.append((PF,
"""                </span>
              )}
            </div>
            {pf.anyTaxAvailable && (
              <div style={{ marginTop: 8, fontSize: 11, color: c.text.tertiary, lineHeight: 1.5 }}>
""",
"""                </span>
              )}
            </div>
            {/* ── PF_SAVED_PDF_20261008 ── save this composition / download it as a PDF */}
            <div data-testid="pf-actions" style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${c.border.dark}`,
              display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              {!saveOpen && (
                <button style={smallBtn(activeSavedId ? "default" : "primary")} disabled={savedBusy}
                  onClick={() => {
                    setSaveName(activePfName || selRuns.map((r) => STRAT_LABEL[r.strategy_id] || r.strategy_id).join(" + "));
                    setSaveConflict(null); setActMsg(null); setSaveOpen(true);
                  }}
                  title="Keep this set of runs under a name, and reopen it from Saved portfolios">
                  {activeSavedId ? "Save as…" : "Save portfolio"}
                </button>
              )}
              {saveOpen && (
                <>
                  <input autoFocus style={{ ...inputStyle, minWidth: 220 }} placeholder="Portfolio name" maxLength={80}
                    value={saveName}
                    onChange={(e) => { setSaveName(e.target.value); setSaveConflict(null); }}
                    onKeyDown={(e) => { if (e.key === "Enter") savePortfolio(false); if (e.key === "Escape") setSaveOpen(false); }} />
                  {!saveConflict && (
                    <button style={smallBtn("primary")} disabled={savedBusy} onClick={() => savePortfolio(false)}>
                      {savedBusy ? "Saving…" : "Save"}
                    </button>
                  )}
                  {saveConflict && (
                    <>
                      <span style={{ fontSize: 12, color: c.warning || c.loss, fontWeight: 600 }}>
                        "{saveConflict}" already exists.
                      </span>
                      <button style={{ ...smallBtn("primary"), background: c.loss }} disabled={savedBusy} onClick={() => savePortfolio(true)}>
                        Replace it
                      </button>
                    </>
                  )}
                  <button style={smallBtn("default")} onClick={() => { setSaveOpen(false); setSaveConflict(null); }}>Cancel</button>
                </>
              )}
              {activeSavedId && !saveOpen && (
                <span style={{ fontSize: 12, color: c.text.muted }}>
                  Saved as <b style={{ color: c.text.secondary }}>{activePfName}</b>
                </span>
              )}
              <button style={{ ...smallBtn("default"), marginLeft: "auto" }} disabled={pdfBusy} data-testid="pf-pdf"
                title="Download this analysis as a PDF to share. Uses the basis shown above."
                onClick={async () => {
                  setPdfBusy(true);
                  setActMsg({ kind: "info", text: "Building PDF…" });
                  try {
                    const body = buildPortfolioReportPayload(pf, {
                      name: activePfName, describeConfig, fmtTs,
                      period: { from: selRuns[0].date_from, to: selRuns[0].date_to },
                    });
                    const res = await fetch(`${getApiBase()}/api/backtest/portfolios/report.pdf`, {
                      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
                    });
                    if (!res.ok) throw new Error((await res.text()) || `API ${res.status}`);
                    const blob = await res.blob();
                    const safe = (x) => String(x || "").replace(/[^0-9A-Za-z_-]+/g, "_");
                    const fname = res.headers.get("X-Report-File") ||
                      `portfolio_${safe(body.name)}_${safe(selRuns[0].date_from)}_to_${safe(selRuns[0].date_to)}.pdf`;
                    const kept = res.headers.get("X-Report-Path");
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url; a.download = fname;
                    document.body.appendChild(a); a.click(); document.body.removeChild(a);
                    setTimeout(() => URL.revokeObjectURL(url), 1500);
                    setActMsg({ kind: "ok", text: `Downloaded ${fname}${kept ? ` · a copy is in ${kept}` : ""}` });
                  } catch (e) {
                    setActMsg({ kind: "err", text: `PDF failed: ${errDetail(e)}` });
                  } finally {
                    setPdfBusy(false);
                  }
                }}>
                {pdfBusy ? "Building PDF…" : "↓ Download PDF"}
              </button>
            </div>
            {actMsg && (
              <div style={{ marginTop: 8, fontSize: 12, fontWeight: 600, wordBreak: "break-all",
                color: actMsg.kind === "ok" ? c.profit : actMsg.kind === "err" ? c.loss : c.text.muted }}>
                {actMsg.text}
              </div>
            )}
            {pf.anyTaxAvailable && (
              <div style={{ marginTop: 8, fontSize: 11, color: c.text.tertiary, lineHeight: 1.5 }}>
"""))


def die(msg: str) -> None:
    print(f"\n✗ {msg}")
    sys.exit(1)


def git_dirty(repo: Path, rel: str) -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=repo,
                             capture_output=True, text=True, timeout=30).stdout
        return bool(out.strip())
    except Exception:
        return False


def find_esbuild(repo: Path):
    for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild"):
        if c.exists():
            return str(c)
    return shutil.which("esbuild")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-esbuild", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    print(f"[{FENCE}] repo: {repo}")

    targets = sorted({f for f, _, _ in EDITS})
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        if FENCE in p.read_text(encoding="utf-8"):
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — already applied (or a stray copy); nothing written")
    for rel, marks in PREREQS.items():
        p = repo / rel
        if not p.exists():
            die(f"prerequisite file missing: {rel}")
        txt = p.read_text(encoding="utf-8")
        for mk in marks:
            if mk not in txt:
                die(f"prerequisite '{mk}' not found in {rel}")
    dirty = [rel for rel in targets if git_dirty(repo, rel)]
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) +
            " — commit/stash first or re-run with --allow-dirty")

    staged = {rel: (repo / rel).read_text(encoding="utf-8") for rel in targets}
    original = dict(staged)
    for rel, old, new in EDITS:
        n = staged[rel].count(old)
        if n != 1:
            die(f"anchor matched {n}× (need 1) in {rel}:\n---\n{old[:300]}\n---")
        staged[rel] = staged[rel].replace(old, new, 1)

    with tempfile.TemporaryDirectory() as td:
        for rel, txt in list(NEW_FILES.items()) + [(r, staged[r]) for r in targets if r.endswith(".py")]:
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for staged {rel}: {e}")
        print("  ✓ py_compile: 4 new modules + api_server.py")
        if not a.skip_esbuild:
            esb = find_esbuild(repo)
            jsx = "frontend/src/pages/backtest/Portfolio.jsx"
            if esb:
                tp = Path(td) / "Portfolio.jsx"
                tp.write_text(staged[jsx], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.jsx=jsx", "--log-level=error"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {jsx}:\n{r.stderr[:2000]}")
                print("  ✓ esbuild parsed staged Portfolio.jsx")
            else:
                print("  ! esbuild not found — JSX parse gate skipped (npm run build will check)")

    def rollback(reason: str) -> None:
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(reason + " — every file restored to its pre-apply content, new files removed")

    written = []
    try:
        for rel in targets:
            p = repo / rel
            shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        for rel in targets:
            (repo / rel).write_text(staged[rel], encoding="utf-8")
            written.append(rel)
        for rel, txt in NEW_FILES.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(txt, encoding="utf-8")
            written.append(rel)
    except Exception as e:
        rollback(f"write failed ({e!r})")
    for rel in written:
        print(f"  ✓ wrote {rel}")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "app/backtest/report/test_portfolio_saved_pdf.py"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=600)
        last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        if r.returncode != 0 or "FAIL " in r.stdout:
            print(r.stdout[-5000:])
            print(r.stderr[-3000:])
            rollback("suite test_portfolio_saved_pdf.py FAILED")
        print(f"  ✓ test_portfolio_saved_pdf.py: {last}")

    for rel in written:
        for src_pre, dst_pre in MIRRORS.items():
            if rel.startswith(src_pre):
                dst = repo / (dst_pre + rel[len(src_pre):])
                if (repo / dst_pre).exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                    shutil.copy2(repo / rel, dst)
                    print(f"  ✓ mirrored → {dst.relative_to(repo)}")

    print(f"\n✓ {FENCE} applied. Rebuild (backend + frontend), then Backtest → Portfolio:\n"
          f"  compose runs → 'Save portfolio'; reopen from 'Saved portfolios'; '↓ Download PDF' to share.")


if __name__ == "__main__":
    main()
