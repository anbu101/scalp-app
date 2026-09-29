"""
EXPIRY SUMMARY CARD — week + month P/L card sent at 15:40 on expiry days.

app/api/telegram_expiry_card.py          ── EXPIRY_CARD_20260929 ──

PURPOSE
-------
One PNG per weekly expiry (NIFTY Tuesday, or the Monday before a Tuesday
holiday): the expiry-cycle week and the trailing month, per book, per
strategy. Same Midnight palette, header and footer shape as the daily card
(EOD_CARD_V2_20260929) so the two read as a set.

  header   Expiry summary · weekday date time · "weekly expiry"
  week     Live / Paper net for the 7 calendar days ending today (Wed→Tue,
           the expiry cycle) + one cell per session, per book
  month    Live / Paper net since the same calendar day last month + one
           cell per expiry week, per book
  table    per (strategy, book): week net·trades, month net·trades, month bar
  footer   best / worst session of the week, positive sessions of the month
           per book, month charges per book

The windows are the Modern dashboard's (fleet_today_routes._week_from /
_month_from), so the card agrees with the ledger's Week P/L / Month P/L.
Live and Paper are never summed.

Pure renderer: ExpiryCardData in, PNG bytes out. No DB here. Palette, pixel
layout constants and formatting helpers are imported from the daily card.

FAIL-OPEN CONTRACT
------------------
build_expiry_card_png() returns PNG bytes or None on any failure; the sender
then falls back to a short text message so the summary is never lost.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

from app.api.telegram_summary_card import (   # ── EOD_CARD_V2_20260929 ── shared tokens
    BG_CARD, TILE, BORDER, TXT_PRIMARY, TXT_MUTED, TXT_DIM, TXT_FAINT,
    UP, DN, DOT_LIVE, DOT_PAPER, CURRENCY,
    DPI, W_PX, PAD_L, PAD_R, PAD_TOP, PAD_BOT, HEADER_H, RULE_GAP, FOOTER_H,
    FS_TITLE, FS_SUB, FS_CORNER, FS_LABEL, FS_NAME, FS_TAG, FS_TSUB, FS_CHART,
    FS_FOOT_L, FS_FOOT_V, FS_FOOT_S,
    _fmt_signed, _fmt_signed_rs, _hline,
)

# strip cell tints = TILE blended 16 % towards UP / DN
CELL_UP   = "#263951"
CELL_DN   = "#3f2544"
CELL_ZERO = "#221d45"

BOOKS = ("LIVE", "PAPER")


def _fmt_k(n: float) -> str:
    """+18.2k / -8.2k / +655 — compact cell figure, explicit sign, half-up."""
    from decimal import Decimal, ROUND_HALF_UP
    n = float(n)
    sign = "-" if n < 0 else "+"
    a = Decimal(str(abs(n)))
    if a >= 100_000:
        return f"{sign}{(a / 1000).quantize(Decimal('1'), ROUND_HALF_UP)}k"
    if a >= 1000:
        return f"{sign}{(a / 1000).quantize(Decimal('0.1'), ROUND_HALF_UP)}k"
    return f"{sign}{a.quantize(Decimal('1'), ROUND_HALF_UP)}"


# ════════════════════════════════════════════════════════════════════
#  DATA MODEL
# ════════════════════════════════════════════════════════════════════

@dataclass
class Cell:
    """One strip cell: a session (week strip) or an expiry week (month strip)."""
    key: str                    # "2026-09-23" (session) / "2026-09-01" (expiry date)
    label: str                  # "Wed" / "1 Sep"
    net: Optional[float] = None # None = no closed trade in this cell
    trades: int = 0


@dataclass
class PeriodBook:
    """One book (LIVE / PAPER) inside one period (week / month)."""
    net: float = 0.0
    trades: int = 0
    wins: int = 0
    charges: float = 0.0
    approx: bool = False
    cells: list = field(default_factory=list)   # [Cell] in time order
    sessions: int = 0                           # sessions with ≥1 closed trade
    positive_sessions: int = 0                  # of those, day net > 0

    @property
    def traded(self) -> bool:
        return self.trades > 0

    @property
    def win_rate(self) -> float:
        return (100.0 * self.wins / self.trades) if self.trades else 0.0


@dataclass
class StrategyPeriodRow:
    name: str
    mode: str                   # "LIVE" | "PAPER"
    week_net: float = 0.0
    week_trades: int = 0
    month_net: float = 0.0
    month_trades: int = 0
    approx: bool = False


@dataclass
class ExpiryCardData:
    date_str: str                               # "29 Sep 2026"
    subtitle: str = ""                          # "Tue 29 Sep 2026 · 15:40 IST · weekly expiry"
    week_range: str = ""                        # "Wed 23 – Tue 29 Sep · 5 sessions"
    month_range: str = ""                       # "since Sat 29 Aug · by expiry week"
    week: dict = field(default_factory=dict)    # book -> PeriodBook
    month: dict = field(default_factory=dict)   # book -> PeriodBook
    rows: list = field(default_factory=list)    # [StrategyPeriodRow], Live first, |month| desc
    warnings: list = field(default_factory=list)

    def book(self, period: str, book: str) -> PeriodBook:
        d = self.week if period == "week" else self.month
        return d.get(book) or PeriodBook()

    @property
    def any_trades(self) -> bool:
        return any(self.book(p, b).traded for p in ("week", "month") for b in BOOKS)

    def _week_cells_with_trades(self):
        for b in BOOKS:
            for c in self.book("week", b).cells:
                if c.net is not None and c.trades:
                    yield c, b

    @property
    def best_session(self):
        cands = list(self._week_cells_with_trades())
        return max(cands, key=lambda t: t[0].net) if cands else None

    @property
    def worst_session(self):
        cands = list(self._week_cells_with_trades())
        return min(cands, key=lambda t: t[0].net) if cands else None


# ════════════════════════════════════════════════════════════════════
#  RENDERER
# ════════════════════════════════════════════════════════════════════

def build_expiry_card_png(data: ExpiryCardData) -> Optional[bytes]:
    try:
        return _render(data)
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        print(f"[EXPIRY_CARD] render failed, caller should fall back to text: {e}")
        return None


PANEL_H    = 172
PANEL_GAP  = 12
PANEL_LEFT = 300         # width of the numbers column inside a panel
ROW_H      = 30          # table row
TABLE_HEAD = 26
TABLE_GAP  = 22
EMPTY_H    = 60
FS_PANEL_V = 11          # panel Live / Paper value
FS_CELL    = 6.8


def _render(data: ExpiryCardData) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    rows = list(data.rows)
    table_h = (TABLE_HEAD + ROW_H * len(rows)) if rows else EMPTY_H

    H_PX = (PAD_TOP + HEADER_H + 2 * PANEL_H + PANEL_GAP + TABLE_GAP + table_h
            + RULE_GAP + FOOTER_H + PAD_BOT)

    fig = plt.figure(figsize=(W_PX / DPI, H_PX / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG_CARD)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_PX)
    ax.set_ylim(H_PX, 0)
    ax.axis("off")
    renderer = fig.canvas.get_renderer()

    def text(x, y, s, *, size=FS_NAME, color=TXT_PRIMARY, ha="left", va="top",
             weight="normal", style="normal", zorder=6):
        return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va,
                       weight=weight, style=style, zorder=zorder)

    def text_w(artist) -> float:
        return artist.get_window_extent(renderer).width

    def rrect(x, y, w, h, *, fc=TILE, r=12, zorder=1):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle=f"round,pad=0,rounding_size={r}",
                                    facecolor=fc, edgecolor="none", zorder=zorder,
                                    mutation_aspect=1))

    def dot(x, y, color, s=40):
        ax.scatter([x], [y], s=s, color=color, zorder=7, linewidths=0)

    L, R = PAD_L, W_PX - PAD_R
    y = PAD_TOP

    # ── header ──────────────────────────────────────────────────────
    text(L, y - 2, "Expiry summary", size=FS_TITLE, weight="medium")
    text(L, y + 38, data.subtitle or data.date_str, size=FS_SUB, color=TXT_DIM)
    text(R, y + 2,  "Scalp Terminal", size=FS_CORNER, color=TXT_FAINT, ha="right")
    text(R, y + 24, "net of charges", size=FS_CORNER, color=TXT_FAINT, ha="right")
    y += HEADER_H

    # ── period panels ───────────────────────────────────────────────
    def panel(py, title, rng, period):
        rrect(L, py, R - L, PANEL_H)
        x0 = L + 16
        text(x0, py + 12, title, size=FS_LABEL, color=TXT_MUTED)
        text(x0, py + 31, rng, size=FS_CHART, color=TXT_FAINT)
        xr = L + PANEL_LEFT - 16          # right edge of the numbers column
        yy = py + 64
        for book, label, dc in (("LIVE", "Live", DOT_LIVE), ("PAPER", "Paper", DOT_PAPER)):
            pb = data.book(period, book)
            dot(x0 + 5, yy + 12, dc if pb.traded else TXT_FAINT, s=32)
            text(x0 + 18, yy + 3, label, size=FS_TAG, color=TXT_MUTED if pb.traded else TXT_FAINT)
            if pb.traded:
                s = ("\u2248 " if pb.approx else "") + _fmt_signed_rs(pb.net)
                text(xr, yy - 4, s, size=FS_PANEL_V, color=UP if pb.net >= 0 else DN,
                     ha="right", weight="bold")
                sub = f"{pb.trades} trade{'s' if pb.trades != 1 else ''} \u00b7 {pb.win_rate:.0f}% won"
                text(xr, yy + 28, sub, size=FS_TSUB, color=TXT_DIM, ha="right")
            else:
                text(xr, yy + 3, "no trades", size=FS_TAG, color=TXT_FAINT, ha="right", style="italic")
            yy += 52

        # strips: label row + one row per book
        sx0 = L + PANEL_LEFT + 16
        sw  = R - 16 - sx0
        lab_w = 58
        cx0 = sx0 + lab_w
        cw_total = sw - lab_w
        cells_ref = data.book(period, "PAPER").cells or data.book(period, "LIVE").cells
        n = max(1, len(cells_ref))
        gap = 5
        cw = (cw_total - gap * (n - 1)) / n
        ly = py + 16
        for i, c in enumerate(cells_ref):
            text(cx0 + i * (cw + gap) + cw / 2, ly, c.label, size=FS_CHART,
                 color=TXT_FAINT, ha="center")
        ry = py + 44
        for book, label in (("LIVE", "Live"), ("PAPER", "Paper")):
            pb = data.book(period, book)
            text(sx0, ry + 9, label, size=FS_CHART, color=TXT_FAINT)
            cells = pb.cells if pb.cells else [Cell(c.key, c.label) for c in cells_ref]
            for i, c in enumerate(cells[:n]):
                cx = cx0 + i * (cw + gap)
                if c.net is None or not c.trades:
                    fc, col, s = CELL_ZERO, TXT_FAINT, "\u2014"
                else:
                    fc = CELL_UP if c.net >= 0 else CELL_DN
                    col = UP if c.net >= 0 else DN
                    s = _fmt_k(c.net)
                rrect(cx, ry, cw, 36, fc=fc, r=6, zorder=2)
                text(cx + cw / 2, ry + 18, s, size=FS_CELL, color=col, ha="center",
                     va="center", weight="bold" if c.trades else "normal")
            ry += 50

    panel(y, "This week", data.week_range, "week")
    y += PANEL_H + PANEL_GAP
    panel(y, "This month", data.month_range, "month")
    y += PANEL_H + TABLE_GAP

    # ── strategy table ──────────────────────────────────────────────
    x_name = L
    x_week = L + 372
    x_month = L + 552
    x_bar0, x_bar1 = L + 584, R
    if not rows:
        text((L + R) / 2, y + EMPTY_H / 2, "No closed trades in the month window",
             size=FS_NAME, color=TXT_DIM, ha="center", va="center", style="italic")
        y += EMPTY_H
    else:
        text(x_name, y, "Strategy", size=FS_CHART, color=TXT_FAINT)
        text(x_week, y, "Week", size=FS_CHART, color=TXT_FAINT, ha="right")
        text(x_month, y, "Month", size=FS_CHART, color=TXT_FAINT, ha="right")
        _hline(ax, L, R, y + 20)
        y += TABLE_HEAD
        max_abs = max([abs(r.month_net) for r in rows] + [1.0])
        half = (x_bar1 - x_bar0) / 2
        xc = x_bar0 + half
        for r in rows:
            ty = y + 5
            nx = x_name
            is_live = r.mode == "LIVE"
            if is_live:
                dot(nx + 4, ty + 10, DOT_LIVE, s=26)
                nx += 16
            nm = text(nx, ty, r.name, size=FS_NAME)
            text(nx + text_w(nm) + 6, ty + 3, "live" if is_live else "paper",
                 size=FS_TAG, color=DOT_LIVE if is_live else TXT_DIM)

            def numcell(xr, net, n, approx=False):
                if not n:
                    text(xr, ty, "\u2014", size=FS_NAME, color=TXT_FAINT, ha="right")
                    return
                cnt = text(xr, ty + 4, str(n), size=FS_TSUB, color=TXT_DIM, ha="right")
                s = ("\u2248 " if approx else "") + _fmt_signed(net)
                text(xr - text_w(cnt) - 6, ty, s, size=FS_NAME,
                     color=UP if net >= 0 else DN, ha="right")

            numcell(x_week, r.week_net, r.week_trades)
            numcell(x_month, r.month_net, r.month_trades, r.approx)
            # diverging bar on the month column (fleet-wide scale, 2 px floor)
            ax.plot([xc, xc], [ty + 2, ty + 20], color=BORDER, linewidth=1, zorder=2)
            if r.month_trades:
                w = max(2.0, half * abs(r.month_net) / max_abs)
                bx = xc if r.month_net >= 0 else xc - w
                ax.add_patch(Rectangle((bx, ty + 7), w, 8, facecolor=UP if r.month_net >= 0 else DN,
                                       edgecolor="none", zorder=3))
            y += ROW_H

    # ── footer ──────────────────────────────────────────────────────
    y += RULE_GAP
    _hline(ax, L, R, y)
    y += RULE_GAP
    cw4 = (R - L) / 4
    for i, (label, value, vcolor, sub) in enumerate(_footer_cells(data)):
        fx = L + i * cw4
        text(fx, y, label, size=FS_FOOT_L, color=TXT_FAINT)
        text(fx, y + 22, value, size=FS_FOOT_V, color=vcolor, weight="medium")
        text(fx, y + 52, sub, size=FS_FOOT_S, color=TXT_DIM)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_CARD, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _footer_cells(data: ExpiryCardData):
    b = data.best_session
    w = data.worst_session
    bk = lambda book: "L" if book == "LIVE" else "P"  # noqa: E731
    c1 = (("Best session", _fmt_signed_rs(b[0].net), UP if b[0].net >= 0 else DN,
           f"{b[0].label} \u00b7 {'Live' if b[1] == 'LIVE' else 'Paper'} \u00b7 week") if b
          else ("Best session", "\u2014", TXT_DIM, ""))
    c2 = (("Worst session", _fmt_signed_rs(w[0].net), UP if w[0].net >= 0 else DN,
           f"{w[0].label} \u00b7 {'Live' if w[1] == 'LIVE' else 'Paper'} \u00b7 week") if w
          else ("Worst session", "\u2014", TXT_DIM, ""))
    ml, mp = data.book("month", "LIVE"), data.book("month", "PAPER")
    if mp.sessions and ml.sessions:
        c3 = ("Positive days", f"P {mp.positive_sessions}/{mp.sessions}", TXT_PRIMARY,
              f"L {ml.positive_sessions}/{ml.sessions} \u00b7 month")
    elif mp.sessions or ml.sessions:
        x, tag = (mp, "P") if mp.sessions else (ml, "L")
        c3 = ("Positive days", f"{tag} {x.positive_sessions}/{x.sessions}", TXT_PRIMARY,
              "days traded \u00b7 month")
    else:
        c3 = ("Positive days", "\u2014", TXT_DIM, "")
    if ml.traded or mp.traded:
        c4 = ("Charges", f"{CURRENCY}{round(ml.charges + mp.charges):,}", TXT_PRIMARY,
              f"{bk('LIVE')} {CURRENCY}{round(ml.charges):,} \u00b7 {bk('PAPER')} {CURRENCY}{round(mp.charges):,}")
    else:
        c4 = ("Charges", "\u2014", TXT_DIM, "")
    return [c1, c2, c3, c4]


# ════════════════════════════════════════════════════════════════════
#  FIXTURES  (synthetic; used by the suite and preview_expiry_card.py)
# ════════════════════════════════════════════════════════════════════

def fixture_expiry_data(kind: str = "fleet") -> ExpiryCardData:
    """
    kind = "empty"  → nothing closed in the month window
           "live"   → a single live strategy, paper idle
           "fleet"  → the mockup's numbers (2 live + 12 paper strategies)
    """
    d = ExpiryCardData(date_str="29 Sep 2026",
                       subtitle="Tue 29 Sep 2026 \u00b7 15:40 IST \u00b7 weekly expiry",
                       week_range="Wed 23 \u2013 Tue 29 Sep \u00b7 5 sessions",
                       month_range="since Sat 29 Aug \u00b7 by expiry week")
    wk_keys = [("2026-09-23", "Wed"), ("2026-09-24", "Thu"), ("2026-09-25", "Fri"),
               ("2026-09-28", "Mon"), ("2026-09-29", "Tue")]
    mo_keys = [("2026-09-01", "1 Sep"), ("2026-09-08", "8 Sep"), ("2026-09-15", "15 Sep"),
               ("2026-09-22", "22 Sep"), ("2026-09-29", "29 Sep")]

    def cells(keys, nets, counts):
        return [Cell(k, lab, None if c == 0 else float(n), c)
                for (k, lab), n, c in zip(keys, nets, counts)]

    if kind == "empty":
        d.week = {b: PeriodBook(cells=cells(wk_keys, [0] * 5, [0] * 5)) for b in BOOKS}
        d.month = {b: PeriodBook(cells=cells(mo_keys, [0] * 5, [0] * 5)) for b in BOOKS}
        return d

    live_w = PeriodBook(net=965, trades=4, wins=2, charges=372,
                        cells=cells(wk_keys, [0, -1240, 2860, 0, -655], [0, 1, 2, 0, 1]),
                        sessions=3, positive_sessions=1)
    live_m = PeriodBook(net=-1395, trades=19, wins=9, charges=1860,
                        cells=cells(mo_keys, [1850, -6300, 4100, -2010, 965], [3, 5, 4, 3, 4]),
                        sessions=17, positive_sessions=8)
    if kind == "live":
        d.week = {"LIVE": live_w, "PAPER": PeriodBook(cells=cells(wk_keys, [0] * 5, [0] * 5))}
        d.month = {"LIVE": live_m, "PAPER": PeriodBook(cells=cells(mo_keys, [0] * 5, [0] * 5))}
        d.rows = [StrategyPeriodRow("Tigris", "LIVE", 1620, 3, 2015, 12),
                  StrategyPeriodRow("Outrider", "LIVE", -655, 1, -3410, 7)]
        return d

    paper_w = PeriodBook(net=45612, trades=103, wins=45, charges=9640,
                         cells=cells(wk_keys, [9400, -8220, 18150, -7423, 33705], [21, 19, 24, 17, 22]),
                         sessions=5, positive_sessions=3)
    paper_m = PeriodBook(net=130250, trades=395, wins=178, charges=39440,
                         cells=cells(mo_keys, [14300, -28700, 61200, 37838, 45612], [61, 78, 84, 69, 103]),
                         sessions=20, positive_sessions=12)
    d.week = {"LIVE": live_w, "PAPER": paper_w}
    d.month = {"LIVE": live_m, "PAPER": paper_m}
    d.rows = [
        StrategyPeriodRow("Tigris", "LIVE", 1620, 3, 2015, 12),
        StrategyPeriodRow("Outrider", "LIVE", -655, 1, -3410, 7),
        StrategyPeriodRow("Velvet", "PAPER", 64562, 1, 71300, 4),
        StrategyPeriodRow("Timberwolf", "PAPER", -12480, 2, 38900, 6),
        StrategyPeriodRow("Baobab", "PAPER", -18300, 12, -32150, 41),
        StrategyPeriodRow("Icarus", "PAPER", 14200, 16, 29400, 58),
        StrategyPeriodRow("Scenic", "PAPER", -9600, 22, 18750, 88, approx=True),
        StrategyPeriodRow("Tigris", "PAPER", 3900, 5, 12300, 19),
        StrategyPeriodRow("Scala", "PAPER", -6100, 18, -9800, 70),
        StrategyPeriodRow("Scribe", "PAPER", 5802, 11, 8100, 44),
        StrategyPeriodRow("Indica", "PAPER", 2100, 13, -4600, 47),
        StrategyPeriodRow("Harbor", "PAPER", 0, 0, -2900, 9),
        StrategyPeriodRow("Breaker", "PAPER", 1900, 2, 2180, 5),
        StrategyPeriodRow("Bobbin", "PAPER", -372, 1, -1230, 4),
    ]
    return d


if __name__ == "__main__":
    png = build_expiry_card_png(fixture_expiry_data("fleet"))
    assert png, "render returned None"
    with open("/tmp/expiry_card_demo.png", "wb") as f:
        f.write(png)
    print(f"OK \u2014 wrote {len(png):,} bytes to /tmp/expiry_card_demo.png")
