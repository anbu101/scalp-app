"""
EOD SUMMARY CARD — dark-theme PNG renderer for the daily Telegram summary.

app/api/telegram_summary_card.py

PURPOSE
-------
Replaces the long, per-strategy EOD text messages with ONE image. Pushed via
Telegram sendPhoto. A short text caption carries the headline so it shows in
the notification preview.

── EOD_CARD_V2_20260929 ── "Board" layout, Midnight palette (chosen by Anbu
2026-09-29 from two layouts × four palettes):

  header   Daily summary · weekday date time · "net of charges"
  hero     Live and Paper headline nets side by side with the day's fleet
           MTM path (from fleet_mtm_samples, the Modern dashboard's sampler).
           The two books are NEVER summed — same rule as the dashboard.
  tiles    one tile per (strategy, book): net, trades, W/L, win %, and a
           thin bar on the fleet-wide |net| scale. Live tiles first.
  footer   best / worst trade, exit mix (target / stop / other), charges.

Everything the previous card said (LIVE table, PAPER table, bar chart) is
still here; the separate bar chart is gone because it repeated the tables
and hid anything under ₹1k.

ISOLATION / DEPENDENCIES
------------------------
- Pure renderer: CardData in, PNG bytes out. No DB access here.
- matplotlib Agg backend forced (headless; required in the bundled Tauri tree).
- Pure-Python deps (matplotlib + numpy), bundle cleanly with PyInstaller.

FAIL-OPEN CONTRACT
------------------
build_summary_card_png() returns a PNG bytes object on success, or None on any
failure. The caller MUST fall back to the existing text summary when it gets
None, so an EOD summary is never silently lost.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# NOTE: matplotlib is imported LAZILY inside _render() (not at module scope) so
# that a build where matplotlib failed to bundle (PyInstaller) cannot crash app
# startup at import time. If matplotlib is missing, _render raises, which
# build_summary_card_png() catches and returns None -> caller falls back to the
# text summary. The app always starts.


# ════════════════════════════════════════════════════════════════════
#  PALETTE — MIDNIGHT  (── EOD_CARD_V2_20260929 ──)
#  indigo base, teal profit, rose loss, violet = Live book identity
# ════════════════════════════════════════════════════════════════════

BG_CARD      = "#120f24"
TILE         = "#1b1738"
BORDER       = "#2b2552"
TXT_PRIMARY  = "#ecebff"
TXT_MUTED    = "#a9a4d6"
TXT_DIM      = "#736ea6"
TXT_FAINT    = "#5b568a"
UP           = "#5eead4"
DN           = "#fb7185"
DOT_LIVE     = "#a78bfa"
DOT_PAPER    = "#736ea6"
GRID         = BORDER

# Kept as aliases so nothing that imported the old names breaks.
GREEN = UP
RED   = DN

CURRENCY = "\u20b9"  # ₹

# Set True to drop a book with no trade today from the hero (the Modern
# dashboard's rule). Default shows it dimmed as "no trades today" — on an EOD
# report, confirming that nothing real traded is information.
HIDE_IDLE_BOOK = False

# Session bounds in minute-of-day IST (mirror fleet_today_routes).
SESSION_OPEN_MIN  = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 40


def _fmt_signed(n: float) -> str:
    """+1,240 / -114,463 — sign before the digits, comma grouped, no decimals."""
    n = round(n)
    sign = "-" if n < 0 else "+"
    return f"{sign}{abs(int(n)):,}"


def _fmt_headline(n: float) -> str:
    """-₹98,740 — sign before currency symbol."""
    n = round(n)
    sign = "-" if n < 0 else ""
    return f"{sign}{CURRENCY}{abs(int(n)):,}"


def _fmt_signed_rs(n: float) -> str:
    """+₹64,562 / -₹14,210 — explicit sign, currency."""
    n = round(n)
    sign = "-" if n < 0 else "+"
    return f"{sign}{CURRENCY}{abs(int(n)):,}"


def _fmt_k_axis(v: float) -> str:
    """Axis tick: -₹114k / ₹0 / ₹15k."""
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1000:
        return f"{sign}{CURRENCY}{av/1000:.0f}k"
    return f"{sign}{CURRENCY}{av:.0f}"


def _hhmm(minute_of_day: int) -> str:
    m = int(minute_of_day)
    return f"{m // 60:02d}:{m % 60:02d}"


# ════════════════════════════════════════════════════════════════════
#  DATA MODEL
# ════════════════════════════════════════════════════════════════════

@dataclass
class StrategyRow:
    name: str
    trades: int
    wins: int
    losses: int
    net: float
    mode: str  # "LIVE" | "PAPER"
    # ── GROSS_RECON ── pre-charge P&L (broker Positions basis). Defaulted
    # field → MUST stay after the required ones (dataclass rule; 2026-07-14
    # boot crash).
    gross: float = 0.0
    # ── EOD_CARD_V2_20260929 ── per-strategy day stats for the footer.
    # All defaulted; positional construction of the six fields above still
    # works exactly as before.
    charges: float = 0.0            # Σ charges over the day's closed trades
    best: Optional[float] = None    # best single trade net (None = no trades)
    worst: Optional[float] = None   # worst single trade net
    exit_tp: int = 0                # exits classified target / profit
    exit_sl: int = 0                # exits classified stop / loss
    exit_other: int = 0             # everything else (signal, EOD, manual…)

    @property
    def win_rate(self) -> float:
        return (100.0 * self.wins / self.trades) if self.trades else 0.0


@dataclass
class CardData:
    date_str: str
    live_rows: list[StrategyRow] = field(default_factory=list)
    paper_rows: list[StrategyRow] = field(default_factory=list)
    # ── EOD_CARD_V2_20260929 ──
    subtitle: str = ""              # "Mon 21 Sep 2026 · 15:30 IST" (falls back to date_str)
    # book -> [[minute_of_day, gross_mtm], ...] as fleet_today_routes stores
    # them (forward-filled per-book totals, gross = realised + unrealised,
    # pre-charge, carried legs since entry — exactly what the dashboard draws)
    mtm_paths: dict = field(default_factory=dict)

    @property
    def live_subtotal(self) -> float:
        return sum(r.net for r in self.live_rows)

    @property
    def live_gross(self) -> float:
        # ── GROSS_RECON ── broker's Positions page shows GROSS (pre-charge);
        # the card's tables are NET — this powers the caption reconciliation.
        return sum(r.gross for r in self.live_rows)

    @property
    def paper_subtotal(self) -> float:
        return sum(r.net for r in self.paper_rows)

    @property
    def combined(self) -> float:
        # Kept for callers/tests; the V2 card and caption never show it.
        return self.live_subtotal + self.paper_subtotal

    # ── EOD_CARD_V2_20260929 ── fleet-wide footer helpers ──────────────
    @property
    def all_rows(self) -> list[StrategyRow]:
        return list(self.live_rows) + list(self.paper_rows)

    def book_rows(self, book: str) -> list[StrategyRow]:
        return self.live_rows if book == "LIVE" else self.paper_rows

    def book_trades(self, book: str) -> int:
        return sum(r.trades for r in self.book_rows(book))

    def book_wins(self, book: str) -> int:
        return sum(r.wins for r in self.book_rows(book))

    def book_charges(self, book: str) -> float:
        return sum(r.charges for r in self.book_rows(book))

    @property
    def best_trade(self) -> Optional[tuple[float, StrategyRow]]:
        cands = [(r.best, r) for r in self.all_rows if r.best is not None]
        return max(cands, key=lambda t: t[0]) if cands else None

    @property
    def worst_trade(self) -> Optional[tuple[float, StrategyRow]]:
        cands = [(r.worst, r) for r in self.all_rows if r.worst is not None]
        return min(cands, key=lambda t: t[0]) if cands else None

    @property
    def exit_counts(self) -> tuple[int, int, int]:
        rows = self.all_rows
        return (sum(r.exit_tp for r in rows), sum(r.exit_sl for r in rows),
                sum(r.exit_other for r in rows))


# ════════════════════════════════════════════════════════════════════
#  RENDERER
# ════════════════════════════════════════════════════════════════════

def build_summary_card_png(data: CardData) -> Optional[bytes]:
    """
    Render the EOD card to PNG bytes. Returns None on any failure so the
    caller can fall back to the text summary (fail-open).
    """
    try:
        return _render(data)
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        print(f"[CARD] render failed, caller should fall back to text: {e}")
        return None


# ── Layout in PIXELS. Every vertical step is a fixed pixel amount and the
# figure height is computed from the content, so 0 rows and N rows space
# identically (the pre-V2 card learned this the hard way).
DPI      = 200
W_PX     = 920
PAD_L    = 56
PAD_R    = 56
PAD_TOP  = 40
PAD_BOT  = 34

HEADER_H = 72
HERO_H   = 236          # chart tile height; the two hero numbers stack beside it
HERO_GAP = 24
HERO_LEFT_W = 300       # width of the Live/Paper column
TILE_H   = 90
TILE_GAP = 14
EMPTY_H  = 64           # "No trades today" block when there are no tiles
FOOTER_H = 74
RULE_GAP = 18

# Font sizes (pt at DPI 200; 1 pt ≈ 2.8 px). Proportions follow the approved
# mockup: body ≈ 2.5 % of the card width, hero numbers ≈ 4.6 %.
FS_TITLE, FS_SUB, FS_CORNER = 13, 8, 7
FS_LABEL, FS_HERO, FS_HERO_SUB = 7.5, 16, 7
FS_NAME, FS_TAG, FS_NET, FS_TSUB = 8.5, 7, 10, 7
FS_FOOT_L, FS_FOOT_V, FS_FOOT_S = 6.8, 9.5, 6.8
FS_CHART = 6.8


def _render(data: CardData) -> bytes:
    import matplotlib
    matplotlib.use("Agg")  # headless — must be set before pyplot import
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    live_rows  = list(data.live_rows)
    paper_rows = list(data.paper_rows)
    # Live tiles first, then paper; each block biggest |net| first.
    tiles = (sorted(live_rows, key=lambda r: abs(r.net), reverse=True)
             + sorted(paper_rows, key=lambda r: abs(r.net), reverse=True))

    n_tiles  = len(tiles)
    n_rows   = (n_tiles + 1) // 2
    tiles_h  = (n_rows * TILE_H + max(0, n_rows - 1) * TILE_GAP) if n_tiles else EMPTY_H

    H_PX = (PAD_TOP + HEADER_H + HERO_H + HERO_GAP + tiles_h
            + RULE_GAP + FOOTER_H + PAD_BOT)

    fig = plt.figure(figsize=(W_PX / DPI, H_PX / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG_CARD)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_PX)
    ax.set_ylim(H_PX, 0)   # y increases downward — px coords from the top
    ax.axis("off")
    renderer = fig.canvas.get_renderer()

    def text(x, y, s, *, size=10.5, color=TXT_PRIMARY, ha="left", va="top",
             weight="normal", style="normal", zorder=6):
        return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va,
                       weight=weight, style=style, zorder=zorder)

    def text_w(artist) -> float:
        # display px == data px here (axes fill the figure, ylim == H_PX)
        return artist.get_window_extent(renderer).width

    def rrect(x, y, w, h, *, fc=TILE, r=12, zorder=1):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle=f"round,pad=0,rounding_size={r}",
                                    facecolor=fc, edgecolor="none",
                                    zorder=zorder, mutation_aspect=1))

    def dot(x, y, color, s=48):
        ax.scatter([x], [y], s=s, color=color, zorder=7, linewidths=0)

    L, R = PAD_L, W_PX - PAD_R
    y = PAD_TOP

    # ── header ──────────────────────────────────────────────────────
    text(L, y - 2, "Daily summary", size=FS_TITLE, weight="medium")
    text(L, y + 38, data.subtitle or data.date_str, size=FS_SUB, color=TXT_DIM)
    text(R, y + 2,  "Scalp Terminal", size=FS_CORNER, color=TXT_FAINT, ha="right")
    text(R, y + 24, "net of charges", size=FS_CORNER, color=TXT_FAINT, ha="right")
    y += HEADER_H

    # ── hero: Live / Paper headline column ──────────────────────────
    hero_top = y
    x0 = L

    def book_block(yb, book, label, dot_color, rows, subtotal):
        idle = not rows
        if idle and HIDE_IDLE_BOOK:
            return 0
        dot(x0 + 6, yb + 9, dot_color if not idle else TXT_FAINT, s=40)
        text(x0 + 20, yb, label, size=FS_LABEL, color=TXT_MUTED if not idle else TXT_FAINT)
        if idle:
            text(x0, yb + 26, "no trades today", size=10.5, color=TXT_FAINT, style="italic")
            return 92
        color = UP if subtotal >= 0 else DN
        text(x0, yb + 20, _fmt_signed_rs(subtotal), size=FS_HERO, color=color, weight="bold")
        tr = sum(r.trades for r in rows)
        wn = sum(r.wins for r in rows)
        pct = f" ({100.0 * wn / tr:.0f}%)" if tr else ""
        sub = f"{tr} trade{'s' if tr != 1 else ''} · {wn} won{pct}"
        text(x0, yb + 70, sub, size=FS_HERO_SUB, color=TXT_DIM)
        return 92

    yb = hero_top + 10
    used = book_block(yb, "LIVE", "Live", DOT_LIVE, live_rows, data.live_subtotal)
    yb += used + (18 if used else 0)
    book_block(yb, "PAPER", "Paper", DOT_PAPER, paper_rows, data.paper_subtotal)

    # ── hero: MTM chart tile ────────────────────────────────────────
    cx0 = L + HERO_LEFT_W
    cw  = R - cx0
    rrect(cx0, hero_top, cw, HERO_H)
    _draw_mtm(fig, ax, text, dot, cx0, hero_top, cw, HERO_H, data, renderer)
    y = hero_top + HERO_H + HERO_GAP

    # ── tiles ───────────────────────────────────────────────────────
    if not tiles:
        text((L + R) / 2, y + EMPTY_H / 2, "No trades today", size=FS_NAME,
             color=TXT_DIM, ha="center", va="center", style="italic")
        y += EMPTY_H
    else:
        max_abs = max([abs(r.net) for r in tiles] + [1.0])
        tw = (R - L - TILE_GAP) / 2
        for i, r in enumerate(tiles):
            col, row = i % 2, i // 2
            tx = L + col * (tw + TILE_GAP)
            ty = y + row * (TILE_H + TILE_GAP)
            rrect(tx, ty, tw, TILE_H)
            is_live = r.mode == "LIVE"
            nx = tx + 16
            if is_live:
                dot(nx + 4, ty + 26, DOT_LIVE, s=30)
                nx += 16
            name = text(nx, ty + 14, r.name, size=FS_NAME)
            text(nx + text_w(name) + 7, ty + 17,
                 "live" if is_live else "paper", size=FS_TAG,
                 color=DOT_LIVE if is_live else TXT_DIM)
            text(tx + tw - 16, ty + 13, _fmt_signed(r.net), size=FS_NET,
                 color=UP if r.net >= 0 else DN, ha="right", weight="bold")
            text(tx + 16, ty + 44,
                 f"{r.trades} trade{'s' if r.trades != 1 else ''} · {r.wins}W {r.losses}L",
                 size=FS_TSUB, color=TXT_DIM)
            text(tx + tw - 16, ty + 44, f"{r.win_rate:.0f}%", size=FS_TSUB,
                 color=TXT_DIM, ha="right")
            # bar on the fleet-wide scale, 2% floor so tiny rows stay visible
            by = ty + TILE_H - 16
            bw = tw - 32
            ax.add_patch(Rectangle((tx + 16, by), bw, 4, facecolor=BORDER,
                                   edgecolor="none", zorder=2))
            frac = max(0.02, abs(r.net) / max_abs)
            ax.add_patch(Rectangle((tx + 16, by), bw * frac, 4,
                                   facecolor=UP if r.net >= 0 else DN,
                                   edgecolor="none", zorder=3))
        y += tiles_h

    # ── footer ──────────────────────────────────────────────────────
    y += RULE_GAP
    _hline(ax, L, R, y)
    y += RULE_GAP
    cells = _footer_cells(data)
    cw4 = (R - L) / 4
    for i, (label, value, vcolor, sub) in enumerate(cells):
        fx = L + i * cw4
        text(fx, y, label, size=FS_FOOT_L, color=TXT_FAINT)
        text(fx, y + 22, value, size=FS_FOOT_V, color=vcolor, weight="medium")
        text(fx, y + 52, sub, size=FS_FOOT_S, color=TXT_DIM)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_CARD, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _footer_cells(data: CardData):
    best = data.best_trade
    worst = data.worst_trade
    tp, sl, other = data.exit_counts

    def tag(r: StrategyRow) -> str:
        return f"{r.name} ({'L' if r.mode == 'LIVE' else 'P'})"

    if best:
        c_best = ("Best trade", _fmt_signed_rs(best[0]), UP if best[0] >= 0 else DN, tag(best[1]))
    else:
        c_best = ("Best trade", "—", TXT_DIM, "")
    if worst:
        c_worst = ("Worst trade", _fmt_signed_rs(worst[0]), UP if worst[0] >= 0 else DN, tag(worst[1]))
    else:
        c_worst = ("Worst trade", "—", TXT_DIM, "")
    n_all = data.book_trades("LIVE") + data.book_trades("PAPER")
    if n_all:
        c_exit = ("Exits", f"{tp} TP · {sl} SL", TXT_PRIMARY, f"{other} signal/EOD")
    else:
        c_exit = ("Exits", "—", TXT_DIM, "")
    ch_l, ch_p = data.book_charges("LIVE"), data.book_charges("PAPER")
    if n_all:
        c_ch = ("Charges", f"{CURRENCY}{round(ch_l + ch_p):,}", TXT_PRIMARY,
                f"L {CURRENCY}{round(ch_l):,} · P {CURRENCY}{round(ch_p):,}")
    else:
        c_ch = ("Charges", "—", TXT_DIM, "")
    return [c_best, c_worst, c_exit, c_ch]


def _hline(ax, x0, x1, y):
    ax.plot([x0, x1], [y, y], color=BORDER, linewidth=1.0, zorder=1)


def _clean_path(series) -> list:
    """[[minute, value], ...] -> sorted, de-duplicated by minute, floats only."""
    out = {}
    for p in series or []:
        try:
            m, v = int(p[0]), float(p[1])
        except Exception:
            continue
        out[m] = v
    return sorted(out.items())


def _draw_mtm(fig, ax, text, dot, x0, y0, w, h, data: CardData, renderer):
    """Chart tile: the day's per-book fleet MTM (gross) as drawn by the
    Modern dashboard. Live = violet dashed (book identity), Paper = solid,
    coloured by where it closed. Times on the session axis 09:15 → 15:40."""
    import matplotlib.pyplot as plt  # noqa: F401  (backend already Agg)

    live  = _clean_path((data.mtm_paths or {}).get("LIVE"))
    paper = _clean_path((data.mtm_paths or {}).get("PAPER"))

    text(x0 + 16, y0 + 12, "Fleet MTM · gross", size=FS_CHART, color=TXT_FAINT)

    # legend (right-aligned in the title row)
    lx = x0 + w - 16
    if paper or live:
        items = []
        if paper:
            items.append(("Paper", UP if paper[-1][1] >= 0 else DN, "-"))
        if live:
            items.append(("Live", DOT_LIVE, "--"))
        for label, color, ls in reversed(items):
            t = text(lx, y0 + 12, label, size=FS_CHART, color=TXT_DIM, ha="right")
            lw = t.get_window_extent(renderer).width
            ax.plot([lx - lw - 20, lx - lw - 6], [y0 + 19, y0 + 19],
                    color=color, linewidth=1.6, linestyle=ls, zorder=7)
            lx = lx - lw - 30

    pad_x, top, bottom = 16, 38, 56
    ax_x0, ax_w = x0 + pad_x, w - 2 * pad_x
    ax_y0, ax_h = y0 + top, h - top - bottom

    if not paper and not live:
        text(x0 + w / 2, ax_y0 + ax_h / 2, "No MTM samples today", size=FS_NAME,
             color=TXT_FAINT, ha="center", va="center", style="italic")
        text(ax_x0, y0 + h - 38, _hhmm(SESSION_OPEN_MIN), size=FS_CHART, color=TXT_FAINT)
        text(ax_x0 + ax_w, y0 + h - 38, _hhmm(SESSION_CLOSE_MIN), size=FS_CHART,
             color=TXT_FAINT, ha="right")
        return

    m_lo = SESSION_OPEN_MIN
    m_hi = max(SESSION_CLOSE_MIN, max([m for m, _ in paper + live]))
    vals = [v for _, v in paper + live] + [0.0]
    v_lo, v_hi = min(vals), max(vals)
    span = (v_hi - v_lo) or 1.0
    v_lo -= span * 0.10
    v_hi += span * 0.10

    cax = fig.add_axes([ax_x0 / W_PX, 1 - (ax_y0 + ax_h) / _fig_h(fig),
                        ax_w / W_PX, ax_h / _fig_h(fig)])
    cax.set_facecolor("none")
    cax.patch.set_alpha(0)
    for sp in cax.spines.values():
        sp.set_visible(False)
    cax.set_xticks([]); cax.set_yticks([])
    cax.set_xlim(m_lo, m_hi)
    cax.set_ylim(v_lo, v_hi)
    cax.axhline(0, color=BORDER, linewidth=1.0, zorder=1)

    def _line(series, color, ls):
        xs = [m for m, _ in series]
        ys = [v for _, v in series]
        cax.plot(xs, ys, color=color, linewidth=1.8, linestyle=ls,
                 solid_joinstyle="round", solid_capstyle="round", zorder=3)
        cax.scatter([xs[-1]], [ys[-1]], s=28, color=color, zorder=4,
                    linewidths=1.5, edgecolors=TILE)

    if live:
        _line(live, DOT_LIVE, "--")
    if paper:
        _line(paper, UP if paper[-1][1] >= 0 else DN, "-")

    # time axis labels (px, drawn on the card axes)
    def mx(m):
        return ax_x0 + (m - m_lo) / (m_hi - m_lo) * ax_w
    ty = y0 + h - 38
    text(mx(m_lo), ty, _hhmm(m_lo), size=FS_CHART, color=TXT_FAINT)
    text(mx(12 * 60), ty, "12:00", size=FS_CHART, color=TXT_FAINT, ha="center")
    text(mx(m_hi), ty, _hhmm(m_hi), size=FS_CHART, color=TXT_FAINT, ha="right")

    # low / high of the primary book (paper if it has samples, else live)
    prim, plabel = (paper, "Paper") if paper else (live, "Live")
    lo = min(prim, key=lambda p: p[1])
    hi = max(prim, key=lambda p: p[1])
    text(ax_x0, y0 + h - 17,
         f"{plabel} low {_fmt_signed_rs(lo[1])} · {_hhmm(lo[0])}",
         size=FS_CHART, color=TXT_FAINT)
    text(ax_x0 + ax_w, y0 + h - 17,
         f"high {_fmt_signed_rs(hi[1])} · {_hhmm(hi[0])}",
         size=FS_CHART, color=TXT_FAINT, ha="right")


def _fig_h(fig) -> float:
    return fig.get_figheight() * fig.dpi


# ════════════════════════════════════════════════════════════════════
#  FIXTURES  (synthetic days — used by the test suite and preview script)
# ════════════════════════════════════════════════════════════════════

def _demo_paths(kind: str) -> dict:
    """A plausible session path per book, minute-of-day IST 09:15 → 15:30."""
    if kind == "flat":
        return {}
    pts_paper = [(555, 0), (570, -1200), (600, -6800), (630, -12000), (660, -18500),
                 (690, -22400), (705, -20100), (720, -22200), (735, -16000),
                 (765, -8000), (780, 5000), (810, 9800), (840, 12500), (855, 24000),
                 (870, 41100), (885, 38000), (900, 35500), (915, 34000), (930, 33705)]
    pts_live = [(555, 0), (660, 0), (668, -400), (700, -655), (930, -655)]
    if kind == "one":
        return {"LIVE": [[m, v] for m, v in pts_live]}
    return {"PAPER": [[m, v] for m, v in pts_paper],
            "LIVE": [[m, v] for m, v in pts_live]}


def fixture_card_data(kind: str = "fleet") -> CardData:
    """
    kind = "empty"  → no trades, no MTM samples
           "one"    → a single live trade, live path only
           "fleet"  → 21 Sep 2026's numbers (1 live + 9 paper strategies)
           "big"    → 14 tiles, one six-figure loser (layout stress)
    """
    sub = "Mon 21 Sep 2026 · 15:30 IST"
    if kind == "empty":
        return CardData(date_str="21 Sep 2026", subtitle=sub)

    outrider = StrategyRow("Outrider", 1, 0, 1, -655, "LIVE", gross=-561,
                           charges=94, best=-655, worst=-655, exit_sl=1)
    if kind == "one":
        return CardData(date_str="21 Sep 2026", subtitle=sub, live_rows=[outrider],
                        mtm_paths=_demo_paths("one"))

    paper = [
        StrategyRow("Velvet", 1, 1, 0, 64562, "PAPER", charges=412, best=64562, worst=64562, exit_other=1),
        StrategyRow("Baobab", 4, 2, 2, -24890, "PAPER", charges=760, best=3100, worst=-14210, exit_tp=1, exit_sl=2, exit_other=1),
        StrategyRow("Scenic", 6, 3, 3, -17451, "PAPER", charges=1180, best=4900, worst=-9800, exit_tp=3, exit_sl=3),
        StrategyRow("Icarus", 5, 2, 3, 11973, "PAPER", charges=640, best=9950, worst=-2200, exit_tp=2, exit_sl=2, exit_other=1),
        StrategyRow("Scala", 5, 1, 4, -9425, "PAPER", charges=520, best=1800, worst=-4400, exit_tp=1, exit_sl=4),
        StrategyRow("Scribe", 4, 2, 2, 5802, "PAPER", charges=390, best=4100, worst=-1900, exit_tp=2, exit_sl=1, exit_other=1),
        StrategyRow("Indica", 4, 1, 3, 4163, "PAPER", charges=610, best=8200, worst=-1600, exit_tp=1, exit_sl=1, exit_other=2),
        StrategyRow("Tigris", 4, 2, 2, -659, "PAPER", charges=286, best=1450, worst=-1300, exit_other=4),
        StrategyRow("Bobbin", 1, 0, 1, -372, "PAPER", charges=42, best=-372, worst=-372, exit_sl=1),
    ]
    if kind == "big":
        paper = paper + [
            StrategyRow("Timberwolf", 2, 0, 2, -114463, "PAPER", charges=1310, best=-31000, worst=-83463, exit_sl=2),
            StrategyRow("Tomahawk", 3, 2, 1, 7420, "PAPER", charges=505, best=6100, worst=-2400, exit_tp=2, exit_sl=1),
            StrategyRow("Harbor", 9, 4, 5, -3120, "PAPER", charges=980, best=1900, worst=-2300, exit_tp=4, exit_sl=5),
            StrategyRow("Breaker", 1, 1, 0, 2660, "PAPER", charges=120, best=2660, worst=2660, exit_tp=1),
        ]
        live = [outrider, StrategyRow("Tigris", 2, 2, 0, 1240, "LIVE", gross=1512,
                                      charges=272, best=800, worst=440, exit_other=2)]
    else:
        live = [outrider]
    return CardData(date_str="21 Sep 2026", subtitle=sub, live_rows=live,
                    paper_rows=paper, mtm_paths=_demo_paths("fleet"))


# ════════════════════════════════════════════════════════════════════
#  SMOKE TEST
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    png = build_summary_card_png(fixture_card_data("fleet"))
    assert png, "render returned None"
    with open("/tmp/eod_card_demo.png", "wb") as f:
        f.write(png)
    print(f"OK — wrote {len(png):,} bytes to /tmp/eod_card_demo.png")
