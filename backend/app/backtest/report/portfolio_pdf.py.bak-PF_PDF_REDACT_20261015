# backend/app/backtest/report/portfolio_pdf.py
#
# ── PF_SAVED_PDF_20261008 ── Backtest → Portfolio "Download PDF".
#
# LAYOUT ONLY. Every number in the PDF is computed by the page itself
# (composePortfolio in Portfolio.jsx) and arrives here in the request body
# already final — this module formats and places it, nothing else. One source
# of truth means the PDF can never disagree with what was on screen (basis,
# tax spreading, exit-realized drawdown and all), and there is no second copy
# of the portfolio math to drift.
#
# Pure module: no app.* imports, no DB, no file I/O — payload dict in, PDF
# bytes out. Uses matplotlib's PDF backend (already bundled for the Telegram
# cards; collect_submodules("matplotlib.backends") ships backend_pdf) and its
# bundled DejaVu fonts, which carry the ₹ glyph. Figures are built with the
# OO API (no pyplot), so concurrent renders don't share global figure state.
#
# Pages (A4 portrait, white, print-friendly):
#   1  header · headline figures · strategies with params/notes · contribution
#   2  equity curve (real time axis) · correlation + diversification · worst days
#   3+ exposure · month scoreboard · month-by-month table (paginates)

from __future__ import annotations

import io
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

PAGE_W, PAGE_H = 210.0, 297.0            # mm
MX, TOP, BOTTOM = 16.0, 16.0, 279.0      # margins; content stops at BOTTOM
CW = PAGE_W - 2 * MX                     # content width
PT = 0.3528                              # mm per point
IST = 19800

INK = "#1e1a2e"
MUTED = "#6b6680"
FAINT = "#9a95ab"
RULE = "#dcd8e5"
ZEBRA = "#f6f4fa"
ACCENT = "#6d28d9"
PROFIT = "#12733d"
LOSS = "#b42318"
WARN = "#a15c07"
NOTE = "#8a4b0a"

SANS = "DejaVu Sans"
MONO = "DejaVu Sans Mono"


# ─────────────────────────── formatting ───────────────────────────
def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def inr(v) -> str:
    """₹ with Indian digit grouping, no decimals — same as the page's fmtInr."""
    f = _num(v)
    if f is None:
        return "—"
    s = str(int(round(abs(f))))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return "₹" + s


def money(v) -> str:
    f = _num(v)
    if f is None:
        return "—"
    return ("+" if f >= 0 else "−") + inr(f)


def compact_inr(v) -> str:
    f = _num(v)
    if f is None:
        return ""
    a, sign = abs(f), ("−" if f < 0 else "")
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.1f} L"
    if a >= 1e3:
        return f"{sign}₹{a / 1e3:.0f}k"
    return f"{sign}₹{a:.0f}"


def pnl_color(v) -> str:
    f = _num(v)
    if f is None or f == 0:
        return INK
    return PROFIT if f > 0 else LOSS


def ratio(v, inf=False) -> str:
    if inf:
        return "∞"
    f = _num(v)
    return "—" if f is None else f"{f:.2f}"


def _s(v, default="") -> str:
    return default if v is None else str(v)


# ─────────────────────────── text metrics ───────────────────────────
_FONTS: Dict[tuple, Any] = {}


def _ft(family: str, bold: bool):
    key = (family, bold)
    if key not in _FONTS:
        from matplotlib import font_manager
        from matplotlib.ft2font import FT2Font
        path = font_manager.findfont(font_manager.FontProperties(
            family=family, weight="bold" if bold else "normal"))
        _FONTS[key] = FT2Font(path)
    return _FONTS[key]


def text_w(s: str, size: float, mono=False, bold=False) -> float:
    """Rendered width in mm."""
    if not s:
        return 0.0
    f = _ft(MONO if mono else SANS, bold)
    f.set_size(size, 72)
    f.set_text(s, 0.0)
    return f.get_width_height()[0] / 64.0 * PT


def fit(s: str, width: float, size: float, mono=False, bold=False) -> str:
    s = _s(s)
    if text_w(s, size, mono, bold) <= width:
        return s
    while s and text_w(s + "…", size, mono, bold) > width:
        s = s[:-1]
    return s.rstrip() + "…"


def wrap(s: str, width: float, size: float, max_lines: int = 99, mono=False, sep: str = " ") -> List[str]:
    """Greedy wrap. sep=" · " wraps a params string between its fields, so
    no line starts with a dangling separator."""
    raw = _s(s).split(sep) if sep != " " else _s(s).split()
    words = [w.strip() for w in raw if w.strip()]
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = (cur + sep + w) if cur else w
        if text_w(trial, size, mono) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit(lines[-1] + " …", width, size, mono)
    return [fit(x, width, size, mono) for x in lines]


def lh(size: float) -> float:
    return size * PT * 1.42


# ─────────────────────────── document ───────────────────────────
class Doc:
    def __init__(self):
        self.pages = []
        self.y = TOP
        self.fig = None
        self.ax = None

    def new_page(self):
        from matplotlib.figure import Figure
        fig = Figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))
        fig.patch.set_facecolor("white")
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, PAGE_W)
        ax.set_ylim(PAGE_H, 0)
        ax.axis("off")
        self.pages.append((fig, ax))
        self.fig, self.ax = fig, ax
        self.y = TOP
        return fig, ax

    def need(self, h: float):
        if self.fig is None or self.y + h > BOTTOM:
            self.new_page()

    def text(self, x, y, s, size=8.5, color=INK, bold=False, mono=False, ha="left", va="top",
             italic=False, ax=None):
        (ax or self.ax).text(x, y, s, fontsize=size, color=color, ha=ha, va=va,
                             family=MONO if mono else SANS,
                             fontweight="bold" if bold else "normal",
                             fontstyle="italic" if italic else "normal")

    def hline(self, x0, x1, y, color=RULE, lw=0.6):
        self.ax.plot([x0, x1], [y, y], color=color, lw=lw, solid_capstyle="butt")

    def rect(self, x, y, w, h, color, alpha=1.0, ec="none", lw=0):
        from matplotlib.patches import Rectangle
        self.ax.add_patch(Rectangle((x, y), w, h, facecolor=color, alpha=alpha,
                                    edgecolor=ec, linewidth=lw))

    def section(self, title: str, sub: str = "", keep: float = 30.0):
        self.need(10 + keep)
        if self.y > TOP + 1:
            self.y += 4
        self.text(MX, self.y, title, size=11.5, bold=True)
        if sub:
            self.text(MX + text_w(title, 11.5, bold=True) + 3, self.y + 0.9, sub, size=7.5, color=MUTED)
        self.y += lh(11.5) + 1.2

    def para(self, s: str, size=7.2, color=MUTED, width=CW, x=MX):
        for line in wrap(s, width, size):
            self.need(lh(size))
            self.text(x, self.y, line, size=size, color=color)
            self.y += lh(size)

    def table(self, cols: List[dict], rows: List[List[Any]], size=7.8, row_h=6.0,
              head_size=7.0, zebra=True, bold_last=False, chip_col: Optional[int] = None):
        """cols: [{title, w, align}] widths in mm (sum ≤ CW).
        rows: cells are str or {t, color, bold, swatch}. Repeats the header
        after a page break."""
        def header():
            self.need(row_h + 2)
            x = MX
            for c in cols:
                tx = x + (c["w"] - 1.5 if c.get("align") == "right" else 1.5)
                self.text(tx, self.y + row_h / 2, c["title"], size=head_size, color=MUTED,
                          ha="right" if c.get("align") == "right" else "left", va="center")
                x += c["w"]
            self.y += row_h
            self.hline(MX, MX + sum(c["w"] for c in cols), self.y, color=INK, lw=0.7)

        header()
        for ri, row in enumerate(rows):
            if self.y + row_h > BOTTOM:
                self.new_page()
                header()
            last = bold_last and ri == len(rows) - 1
            if zebra and ri % 2 == 1:
                self.rect(MX, self.y, sum(c["w"] for c in cols), row_h, ZEBRA)
            x = MX
            for ci, (c, cell) in enumerate(zip(cols, row)):
                d = cell if isinstance(cell, dict) else {"t": cell}
                t = _s(d.get("t"), "—")
                right = c.get("align") == "right"
                mono = d.get("mono", c.get("mono", right))
                bold = d.get("bold", False) or last
                avail = c["w"] - 3
                tx = x + 1.5
                if d.get("swatch"):
                    self.rect(x + 1.5, self.y + row_h / 2 - 1.4, 2.8, 2.8, d["swatch"])
                    tx += 4.2
                    avail -= 4.2
                t = fit(t, avail, size, mono, bold)
                self.text(x + c["w"] - 1.5 if right else tx, self.y + row_h / 2, t, size=size,
                          color=d.get("color", INK), bold=bold, mono=mono,
                          ha="right" if right else "left", va="center")
                x += c["w"]
            self.y += row_h
            if last:
                self.hline(MX, MX + sum(c["w"] for c in cols), self.y - row_h, color=INK, lw=0.5)
        self.hline(MX, MX + sum(c["w"] for c in cols), self.y, color=RULE, lw=0.6)

    def kpis(self, items: List[dict], h=19.0):
        """Headline figures in a row: [{label, value, color, sub}]."""
        self.need(h + 2)
        n = len(items)
        gap = 4.0
        w = (CW - gap * (n - 1)) / n
        for i, it in enumerate(items):
            x = MX + i * (w + gap)
            self.ax.plot([x, x], [self.y, self.y + h], color=it.get("color", INK), lw=1.6,
                         solid_capstyle="butt")
            self.text(x + 3, self.y + 0.4, it["label"], size=7.2, color=MUTED)
            size = 14.5                       # shrink to fit — a headline figure is never truncated
            while size > 8 and text_w(it["value"], size, mono=True, bold=True) > w - 4:
                size -= 0.5
            self.text(x + 3, self.y + 5.0 + (14.5 - size) * PT * 0.5, it["value"], size=size,
                      color=it.get("color", INK), bold=True, mono=True)
            if it.get("sub"):
                for k, line in enumerate(wrap(it["sub"], w - 4, 6.6, max_lines=2)):
                    self.text(x + 3, self.y + 12.4 + k * lh(6.6), line, size=6.6, color=MUTED)
        self.y += h + 3

    def chart_axes(self, x, y, w, h):
        """A real matplotlib Axes placed at a mm rectangle on the current page."""
        return self.fig.add_axes([x / PAGE_W, 1 - (y + h) / PAGE_H, w / PAGE_W, h / PAGE_H])


# ─────────────────────────── sections ───────────────────────────
def _header(doc: Doc, p: dict):
    doc.new_page()
    name = _s(p.get("name")).strip() or "Untitled portfolio"
    per = p.get("period") or {}
    strats = p.get("strategies") or []
    doc.text(MX, doc.y, "Backtest portfolio report", size=8.5, color=ACCENT, bold=True)
    doc.y += lh(8.5) + 0.8
    doc.text(MX, doc.y, fit(name, CW, 21, bold=True), size=21, bold=True)
    doc.y += lh(21) + 0.6
    basis = ("Net after tax" if p.get("basis") == "after" and p.get("any_taxed") else "Net P&L (pre-tax)"
             if p.get("any_tax_available") else "Net P&L")
    bits = [f"{_s(per.get('from'), '?')} to {_s(per.get('to'), '?')}",
            f"{len(strats)} strategies", f"Basis: {basis}"]
    if p.get("generated_label"):
        bits.append(f"Generated {p['generated_label']}")
    doc.text(MX, doc.y, "     ".join(bits), size=8.2, color=MUTED)
    doc.y += lh(8.2) + 2.5
    doc.hline(MX, MX + CW, doc.y, color=ACCENT, lw=1.1)
    doc.y += 5


def _headline(doc: Doc, p: dict):
    k = p.get("kpis") or {}
    any_taxed = bool(p.get("any_taxed"))
    ddr = _num(k.get("dd_reduction"))
    rtd_inf = bool(k.get("return_to_dd_inf"))
    rtd = _num(k.get("return_to_dd"))
    doc.kpis([
        {"label": "Combined net after tax" if any_taxed else "Combined net P&L",
         "value": money(k.get("combined_net")), "color": pnl_color(k.get("combined_net")),
         "sub": (f"sum of all strategies, tax {money(-(_num(p.get('combined_tax')) or 0))}"
                 if any_taxed else "sum of all strategies (net)")},
        {"label": "Combined max drawdown", "value": inr(k.get("combined_max_dd")), "color": LOSS,
         "sub": "on the merged exit-realized curve"},
        {"label": "Drawdown reduction", "value": "—" if ddr is None else f"{ddr:.1f}%",
         "color": INK if ddr is None else (PROFIT if ddr > 0 else LOSS),
         "sub": f"vs sum of individual DDs ({inr(k.get('sum_ind_dd'))})"},
        {"label": "Return ÷ max DD", "value": ratio(rtd, rtd_inf),
         "color": PROFIT if rtd_inf or (rtd or 0) >= 2 else (LOSS if (rtd or 0) < 1 else INK),
         "sub": f"best single strategy: {ratio(k.get('best_single'), bool(k.get('best_single_inf')))}"},
    ])


def _strategies(doc: Doc, p: dict):
    strats = p.get("strategies") or []
    doc.section("Strategies", "the runs composed, with their parameters", keep=24)
    for s in strats:
        params = wrap(_s(s.get("params")) or "—", CW - 40, 7.0, max_lines=2, sep=" · ")
        note = _s(s.get("note")).strip()
        h = 5.2 + len(params) * lh(7.0) + (lh(7.0) if note else 0) + 2.2
        doc.need(h)
        y0 = doc.y
        doc.rect(MX, y0 + 0.6, 3.0, 3.0, _s(s.get("color"), INK))
        doc.text(MX + 5, y0, fit(_s(s.get("label")), 30, 9, bold=True), size=9, bold=True)
        doc.text(MX + 5, y0 + lh(9), _s(s.get("run_id"))[:8], size=6.8, color=FAINT, mono=True)
        yy = y0 + 0.3
        for line in params:
            doc.text(MX + 40, yy, line, size=7.0, color=INK)
            yy += lh(7.0)
        if note:
            doc.text(MX + 40, yy, fit("Note: " + note, CW - 40, 7.0), size=7.0, color=NOTE, italic=True)
            yy += lh(7.0)
        doc.y = max(yy, y0 + 2 * lh(9)) + 2.2
        doc.hline(MX, MX + CW, doc.y - 1.1, color=RULE, lw=0.4)


def _contribution(doc: Doc, p: dict):
    strats = p.get("strategies") or []
    any_taxed = bool(p.get("any_taxed"))
    doc.section("Per-strategy contribution", keep=8 + 6 * (len(strats) + 1))
    cols = [{"title": "Strategy", "w": 24},
            {"title": "Net after tax" if any_taxed else "Net P&L", "w": 27, "align": "right"},
            {"title": "Tax", "w": 21, "align": "right"},
            {"title": "Share", "w": 15, "align": "right"},
            {"title": "Own max DD", "w": 24, "align": "right"},
            {"title": "Return ÷ DD", "w": 20, "align": "right"},
            {"title": "Trades", "w": 15, "align": "right"},
            {"title": "P&L in combined DD", "w": 32, "align": "right"}]
    rows = []
    for s in strats:
        share = _num(s.get("share"))
        rows.append([
            {"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},
            {"t": money(s.get("net")), "color": pnl_color(s.get("net")), "bold": True},
            {"t": money(-(_num(s.get("tax")) or 0)) if s.get("taxed") else "—",
             "color": LOSS if s.get("taxed") else FAINT},
            "—" if share is None else f"{share:.0f}%",
            {"t": inr(s.get("max_dd")), "color": LOSS},
            ratio(s.get("rdd"), bool(s.get("rdd_inf"))),
            _s(s.get("trades"), "—"),
            {"t": money(s.get("dd_contrib")), "color": pnl_color(s.get("dd_contrib"))},
        ])
    doc.table(cols, rows)
    dw = p.get("dd_window") or {}
    win = f" ({dw.get('from_label')} to {dw.get('to_label')})" if dw.get("from_label") else ""
    doc.y += 2
    doc.para("“P&L in combined DD” attributes the portfolio’s worst peak-to-trough window" + win +
             " to each strategy: the most negative cell dug the hole, a positive cell was cushioning it. "
             "Per-strategy max DD here is exit-realized, so it can differ slightly from a run’s own Advanced tab "
             "(entry-stepped).")
    if p.get("any_taxed"):
        doc.para("Where a run carries the lot-compounding tax option, each financial year’s tax is spread over "
                 "that year’s trades pro rata, so totals match Compare Runs’ “Net after tax” and no month changes sign.")


def _ist_dt(ts):
    return datetime(1970, 1, 1) + timedelta(seconds=float(ts) + IST)


def _equity(doc: Doc, p: dict):
    series = [s for s in (p.get("equity") or [])
              if isinstance(s, dict) and len(s.get("ts") or []) >= 2 and len(s.get("ts")) == len(s.get("v") or [])]
    doc.new_page()
    doc.section("Combined equity", "exit-realized, real time axis", keep=110)
    if not series:
        doc.para("No closed trades to chart.", size=8.5)
        return
    import warnings
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter
    warnings.filterwarnings("ignore", message="AutoDateLocator was unable")   # tiny ranges only
    h = 98.0
    ax = doc.chart_axes(MX + 14, doc.y + 2, CW - 16, h - 18)
    for s in sorted(series, key=lambda z: bool(z.get("thick"))):
        xs = [_ist_dt(t) for t in s["ts"]]
        ys = [(_num(v) or 0.0) for v in s["v"]]
        xs.insert(0, xs[0])
        ys.insert(0, 0.0)
        ax.plot(xs, ys, color=_s(s.get("color"), INK), lw=2.0 if s.get("thick") else 0.9,
                alpha=1.0 if s.get("thick") else 0.85, solid_joinstyle="round")
    ax.axhline(0, color=MUTED, lw=0.6, ls=(0, (3, 2)), alpha=0.7)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: compact_inr(v)))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(RULE)
    ax.tick_params(labelsize=6.8, colors=MUTED, length=2.5)
    ax.grid(axis="y", color=RULE, lw=0.4)
    ax.set_axisbelow(True)
    for lab in ax.get_yticklabels() + ax.get_xticklabels():
        lab.set_family(SANS)
    doc.y += h - 5
    # legend row (below the date ticks)
    x = MX
    for s in series:
        end = (_num(s["v"][-1]) or 0.0)
        lab = f"{_s(s.get('label'))}  {money(end)}"
        w = text_w(lab, 7.4, bold=bool(s.get("thick"))) + 9
        if x + w > MX + CW:
            x = MX
            doc.y += lh(7.4) + 1
        doc.rect(x, doc.y + 1.0, 5, 1.6 if s.get("thick") else 1.0, _s(s.get("color"), INK))
        doc.text(x + 6.5, doc.y, lab, size=7.4, bold=bool(s.get("thick")), color=INK)
        x += w + 4
    doc.y += lh(7.4) + 2


def _correlation(doc: Doc, p: dict):
    strats = p.get("strategies") or []
    corr = p.get("corr") or []
    n = len(strats)
    doc.section("Correlation and diversification", "daily net P&L, Pearson", keep=8 + 6.2 * (n + 1) + 30)
    cell, lab_w = 17.0, 24.0
    y0 = doc.y
    # matrix header
    for j, s in enumerate(strats):
        doc.text(MX + lab_w + j * cell + cell / 2, y0 + 2.5, fit(_s(s.get("label")), cell - 1, 7.2, bold=True),
                 size=7.2, bold=True, ha="center", va="center")
    for i, si in enumerate(strats):
        yy = y0 + 6 + i * 6.2
        doc.rect(MX, yy + 1.6, 2.6, 2.6, _s(si.get("color"), INK))
        doc.text(MX + 4, yy + 3.1, fit(_s(si.get("label")), lab_w - 5, 7.2, bold=True), size=7.2, bold=True,
                 va="center")
        for j in range(n):
            r = None
            try:
                r = _num(corr[i][j])
            except (IndexError, TypeError):
                r = None
            if i == j:
                col, bg = FAINT, None
            elif r is None:
                col, bg = FAINT, None
            elif r <= 0.2:
                col, bg = PROFIT, "#e7f4ec"
            elif r <= 0.5:
                col, bg = WARN, "#fbf1e1"
            else:
                col, bg = LOSS, "#fbe9e7"
            x = MX + lab_w + j * cell
            if bg:
                doc.rect(x + 0.6, yy + 0.4, cell - 1.2, 5.4, bg)
            doc.text(x + cell / 2, yy + 3.1, "—" if r is None else f"{r:.2f}", size=7.6, mono=True,
                     bold=i != j, color=col, ha="center", va="center")
    mat_h = 6 + n * 6.2
    # diversification figures to the right of the matrix
    d = p.get("days") or {}
    wc = d.get("worst_combined") or {}
    xr = MX + lab_w + n * cell + 10
    wr = MX + CW - xr
    facts = [
        ("Rescued days", _s(d.get("rescued"), "—"), PROFIT, "portfolio green while at least one strategy was red"),
        ("Cluster-loss days", _s(d.get("cluster"), "—"), LOSS, "two or more strategies red the same day"),
        ("Portfolio red days", f"{_s(d.get('red'), '—')} of {_s(d.get('total'), '—')}", INK,
         f"{_s(d.get('green'), '—')} green"),
        ("Worst combined day", money(wc.get("total")) if wc else "—", LOSS, _s(wc.get("label"))),
    ]
    yy = y0
    for label, val, col, sub in facts:
        doc.text(xr, yy, label, size=7.0, color=MUTED)
        doc.text(xr + wr, yy, val, size=8.6, color=col, bold=True, mono=True, ha="right")
        if sub:
            doc.text(xr, yy + lh(7.0), fit(sub, wr, 6.4), size=6.4, color=FAINT)
        yy += lh(7.0) + lh(6.4) + 1.6
    doc.y = max(y0 + mat_h, yy) + 3
    doc.para("Computed over the union of each pair’s trading days (a day only one side traded counts as 0 for the "
             "other). Low or negative correlation means loss days don’t coincide, which is structural drawdown "
             "reduction; high positive correlation means the reduction may be luck of the sample.")


def _worst_days(doc: Doc, p: dict):
    strats = p.get("strategies") or []
    doc.section("Worst single days", "do losses cluster?", keep=8 + 6 * (len(strats) + 1))
    cols = [{"title": "Strategy", "w": 40}, {"title": "Worst own day", "w": 46, "align": "right"},
            {"title": "On", "w": 40, "align": "right"}, {"title": "Portfolio that day", "w": 52, "align": "right"}]
    rows = []
    for s in strats:
        w = s.get("worst_day") or {}
        rows.append([{"t": _s(s.get("label")), "bold": True, "swatch": _s(s.get("color"), INK), "mono": False},
                     {"t": money(w.get("v")) if w else "—", "color": LOSS, "bold": True},
                     {"t": _s(w.get("label"), "—"), "color": MUTED},
                     {"t": money(w.get("portfolio_total")) if w and w.get("portfolio_total") is not None else "—",
                      "color": pnl_color(w.get("portfolio_total")), "bold": True}])
    doc.table(cols, rows)
    doc.y += 2
    doc.para("If the portfolio total on a strategy’s worst day is much better than that strategy’s own loss, the "
             "others were absorbing it; if it is similar or worse, the bad days coincide.")


def _exposure(doc: Doc, p: dict):
    e = p.get("exposure") or {}
    doc.new_page()
    doc.section("Exposure", "can every strategy be funded at once?", keep=30)
    ov = _num(e.get("overlap_pct"))
    doc.kpis([
        {"label": "Max concurrent trades", "value": _s(e.get("max_concurrent"), "—"), "color": INK,
         "sub": "across all strategies at once"},
        {"label": "Max strategies in trade", "value": f"{_s(e.get('max_strats'), '—')} of {_s(e.get('n_strats'), '—')}",
         "color": INK, "sub": "simultaneously holding positions"},
        {"label": "Peak premium notional", "value": inr(e.get("peak_notional")), "color": INK,
         "sub": "sum of entry price × qty of open trades"},
        {"label": "Overlap of in-trade time", "value": "—" if ov is None else f"{ov:.1f}%", "color": INK,
         "sub": f"{_s(e.get('t_multi_label'))} of {_s(e.get('t_any_label'))} with 2+ strategies open"},
    ])
    doc.para("Premium notional is the capital outlay for long option legs; for short strategies the real requirement "
             "is exchange margin, which is larger, so treat their share here as a floor. A low time overlap means the "
             "effective capital need is well below the sum of each strategy’s peak.")


def _monthly(doc: Doc, p: dict):
    strats = p.get("strategies") or []
    m = p.get("monthly") or {}
    months = m.get("months") or []
    st = m.get("stats") or {}
    any_taxed = bool(p.get("any_taxed"))
    n = len(strats)
    first_w = 34.0
    w = (CW - first_w) / (n + 1)
    head = [{"title": f"{len(months)} months", "w": first_w}] + \
           [{"title": _s(s.get("label")), "w": w, "align": "right"} for s in strats] + \
           [{"title": "Combined", "w": w, "align": "right"}]

    doc.section("Month scoreboard", "net after tax" if any_taxed else "net P&L", keep=8 + 6 * 8)
    cols_stats = list(st.get("per") or []) + [st.get("combined") or {}]
    any_idle = any((_num(x.get("idle")) or 0) > 0 for x in cols_stats if isinstance(x, dict))

    def row(label, fn):
        cells = [{"t": label, "mono": False, "color": MUTED}]
        for i, x in enumerate(cols_stats):
            c = fn(x if isinstance(x, dict) else {})
            c = c if isinstance(c, dict) else {"t": c}
            if i == len(cols_stats) - 1:
                c["bold"] = True
            cells.append(c)
        return cells

    def pct(x):
        v = _num(x.get("pct_green"))
        return "—" if v is None else f"{v:.0f}%"

    def run(x):
        k = int(_num(x.get("max_red_run")) or 0)
        return {"t": f"{k} mo" if k else "0"}

    def ext(key):
        def f(x):
            e = x.get(key) or {}
            if not e or _num(e.get("v")) is None:
                return "—"
            return {"t": money(e.get("v")), "color": pnl_color(e.get("v"))}
        return f

    rows = [row("Green months", lambda x: {"t": _s(x.get("green"), "—"), "color": PROFIT}),
            row("Red months", lambda x: {"t": _s(x.get("red"), "—"), "color": LOSS}),
            row("% green", pct),
            row("Longest red run", run),
            row("Worst month", ext("worst")),
            row("Best month", ext("best"))]
    if any_idle:
        rows.append(row("No-trade months", lambda x: {"t": _s(x.get("idle"), "—"), "color": FAINT}))
    doc.table(head, rows, zebra=False)
    runs = [(_s(s.get("label")), x.get("red_run_label")) for s, x in zip(strats, st.get("per") or [])
            if isinstance(x, dict) and x.get("red_run_label")]
    cr = (st.get("combined") or {}).get("red_run_label")
    if runs or cr:
        doc.y += 1.5
        doc.para("Longest red runs: " + "; ".join([f"{a} {b}" for a, b in runs] + ([f"Combined {cr}"] if cr else [])) + ".")

    doc.section("Month by month", "a dash means the strategy did not trade that month", keep=8 + 6 * 6)
    mrows = []
    for mo in months:
        per = mo.get("per") or []
        cnt = mo.get("n") or []
        cells = [{"t": _s(mo.get("label")), "mono": False, "color": MUTED}]
        for i in range(n):
            v = per[i] if i < len(per) else None
            traded = (cnt[i] if i < len(cnt) else 0) or 0
            cells.append({"t": money(v), "color": pnl_color(v)} if traded else {"t": "—", "color": FAINT})
        any_tr = any((x or 0) > 0 for x in cnt)
        cells.append({"t": money(mo.get("total")), "color": pnl_color(mo.get("total")), "bold": True}
                     if any_tr else {"t": "—", "color": FAINT})
        mrows.append(cells)
    doc.table(head, mrows, row_h=5.2, size=7.4)
    doc.y += 2
    doc.para("Months where one strategy’s red is covered by another’s green are the diversification working; "
             "months where every column is red are the risk that remains.")


def _footers(doc: Doc, p: dict):
    total = len(doc.pages)
    name = _s(p.get("name")).strip() or "Untitled portfolio"
    for i, (fig, ax) in enumerate(doc.pages, start=1):
        ax.plot([MX, MX + CW], [BOTTOM + 4, BOTTOM + 4], color=RULE, lw=0.5)
        ax.text(MX, BOTTOM + 6, fit(f"Scalp Terminal  |  {name}", CW * 0.6, 6.6), fontsize=6.6, color=FAINT,
                ha="left", va="top", family=SANS)
        ax.text(MX + CW, BOTTOM + 6, f"Page {i} of {total}", fontsize=6.6, color=FAINT, ha="right", va="top",
                family=SANS)
        if i == 1:
            ax.text(MX, BOTTOM + 9.4,
                    fit("Backtest composition of independently run strategies. P&L is booked at each trade’s exit; "
                        "figures are simulated, net of modelled charges, and are not a forecast.", CW, 6.0),
                    fontsize=6.0, color=FAINT, ha="left", va="top", family=SANS)


def render_portfolio_pdf(payload: Dict[str, Any]) -> bytes:
    """payload → PDF bytes. Raises ValueError on a payload with no strategies."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    strats = payload.get("strategies")
    if not isinstance(strats, list) or len(strats) < 1:
        raise ValueError("payload has no strategies")
    import matplotlib
    from matplotlib.backends.backend_pdf import PdfPages

    with matplotlib.rc_context({"pdf.fonttype": 42, "font.family": SANS, "axes.unicode_minus": True}):
        doc = Doc()
        _header(doc, payload)
        _headline(doc, payload)
        _strategies(doc, payload)
        _contribution(doc, payload)
        _equity(doc, payload)
        _correlation(doc, payload)
        _worst_days(doc, payload)
        _exposure(doc, payload)
        _monthly(doc, payload)
        _footers(doc, payload)
        buf = io.BytesIO()
        name = _s(payload.get("name")).strip() or "Untitled portfolio"
        with PdfPages(buf, metadata={"Title": f"Portfolio report: {name}", "Author": "Scalp Terminal",
                                     "Subject": "Backtest portfolio composition"}) as pdf:
            for fig, _ax in doc.pages:
                pdf.savefig(fig)
        return buf.getvalue()
