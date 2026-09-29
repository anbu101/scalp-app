"""
EOD SUMMARY CARD — dark-theme PNG renderer for the daily Telegram summary.

app/api/telegram_summary_card.py   (proposed location)

PURPOSE
-------
Replaces the long, per-strategy EOD text messages with ONE image: a dark card
showing a LIVE table, a PAPER table, and a single horizontal net-P&L bar chart
across all strategies. Pushed via Telegram sendPhoto. A short text caption
carries the combined headline so it shows in the notification preview.

ISOLATION / DEPENDENCIES
------------------------
- Reads ONLY via existing repos + a new self-contained V3 reader; touches no
  existing functions.
- All P&L on the card is NET (charges applied via zerodha_charges):
    * LIVE/PAPER `trades`/`paper_trades` rows  -> direction-aware net per row.
    * SCALP_V3 (own table, LONG hedge)         -> gross realized_pnl minus
      freshly-computed LONG charges per row.
- matplotlib Agg backend forced (headless; required in the bundled Tauri tree).
- Pure-Python deps (matplotlib + numpy), bundle cleanly with PyInstaller.

FAIL-OPEN CONTRACT
------------------
build_summary_card_png() returns a PNG bytes object on success, or None on any
failure. The caller MUST fall back to the existing text summary when it gets
None, so an EOD summary is never silently lost.

This file is a standalone prototype: the DB/charges reads are written behind a
small data-source seam (CardDataSource) so it can be unit-tested with synthetic
data and dropped into the backend by swapping the seam for the real repos.
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
#  DARK THEME TOKENS  (mirror the approved mockup)
# ════════════════════════════════════════════════════════════════════

BG_CARD      = "#0e1621"
BORDER       = "#1e2a38"
TXT_PRIMARY  = "#e8edf2"
TXT_MUTED    = "#9fb0c2"
TXT_DIM      = "#7d8da0"
TXT_FAINT    = "#5e6e80"
GREEN        = "#7bc043"
RED          = "#f0726f"
DOT_LIVE     = "#5dcaa5"
DOT_PAPER    = "#7d8da0"
GRID         = "#1e2a38"

CURRENCY = "\u20b9"  # ₹


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


def _fmt_k_axis(v: float) -> str:
    """Axis tick: -₹114k / ₹0 / ₹15k."""
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1000:
        return f"{sign}{CURRENCY}{av/1000:.0f}k"
    return f"{sign}{CURRENCY}{av:.0f}"


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
    # field → MUST stay LAST (dataclass rule; 2026-07-14 boot crash).
    gross: float = 0.0


@dataclass
class CardData:
    date_str: str
    live_rows: list[StrategyRow] = field(default_factory=list)
    paper_rows: list[StrategyRow] = field(default_factory=list)

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
        return self.live_subtotal + self.paper_subtotal


# ════════════════════════════════════════════════════════════════════
#  RENDERER
# ════════════════════════════════════════════════════════════════════

def build_summary_card_png(data: CardData) -> Optional[bytes]:
    """
    Render the dark EOD card to PNG bytes. Returns None on any failure so the
    caller can fall back to the text summary (fail-open).
    """
    try:
        return _render(data)
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        print(f"[CARD] render failed, caller should fall back to text: {e}")
        return None


def _render(data: CardData) -> bytes:
    import matplotlib
    matplotlib.use("Agg")  # headless — must be set before pyplot import
    import matplotlib.pyplot as plt

    all_rows = data.live_rows + data.paper_rows

    # ── Layout in PIXELS, not figure-fractions ───────────────────────
    # Spacing was previously expressed as fractions of a figure whose height
    # scaled with row count, so short/empty cards crushed the lines together.
    # Here every vertical step is a fixed pixel amount and the figure height is
    # computed from the actual content, so 0 rows and N rows space identically.
    DPI       = 200
    W_PX      = 920          # card width in px
    PAD_TOP   = 28
    PAD_BOT   = 28
    PAD_L     = 56
    PAD_R     = 56

    LINE      = 34           # standard text line height (px)
    HEADER_H  = 42           # "Daily summary" / date row
    SUB_GAP   = 38           # combined-net subtitle row
    RULE_GAP  = 18           # space around a horizontal rule
    SEC_LABEL = 34           # "LIVE"/"PAPER" label row
    COLHEAD   = 26           # column-header row
    SUBTOTAL  = 34           # subtotal row
    SEC_GAP   = 14           # gap between sections
    CHART_TITLE = 30         # "NET P&L BY STRATEGY"

    def _section_height(rows):
        n = max(1, len(rows))            # empty -> one "No trades today" line
        return SEC_LABEL + COLHEAD + n * LINE + RULE_GAP + SUBTOTAL

    # per-strategy bar = ~54px; min height keeps an empty/1-bar chart tidy
    chart_h = max(120, 54 * max(1, len(all_rows)) + 30)

    content_h = (
        PAD_TOP
        + HEADER_H + SUB_GAP + RULE_GAP
        + _section_height(data.live_rows) + SEC_GAP
        + _section_height(data.paper_rows)
        + RULE_GAP + CHART_TITLE
        + chart_h
        + PAD_BOT
    )

    H_PX = content_h
    fig = plt.figure(figsize=(W_PX / DPI, H_PX / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG_CARD)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_PX)
    ax.set_ylim(H_PX, 0)   # y increases downward — px coords from the top
    ax.axis("off")

    L = PAD_L
    R = W_PX - PAD_R
    y = PAD_TOP

    # ---- header ----
    ax.text(L, y, "Daily summary", color=TXT_PRIMARY, fontsize=15,
            weight="medium", va="top", ha="left")
    ax.text(R, y, data.date_str, color=TXT_DIM, fontsize=11,
            va="top", ha="right")
    y += HEADER_H
    ax.text(L, y, "Combined net P&L", color=TXT_DIM, fontsize=11,
            va="top", ha="left")
    comb_color = GREEN if data.combined >= 0 else RED
    ax.text(R, y - 4, _fmt_headline(data.combined), color=comb_color,
            fontsize=19, weight="medium", va="top", ha="right")
    y += SUB_GAP
    _hline(ax, L, R, y)
    y += RULE_GAP

    # column x-anchors
    x_strat = L
    x_tr    = W_PX * 0.52
    x_wl    = W_PX * 0.66
    x_net   = R

    def _section(title, dot, rows, subtotal):
        nonlocal y
        ax.scatter([L + 8], [y + 8], s=70, color=dot, zorder=5)
        ax.text(L + 28, y, title, color=TXT_MUTED, fontsize=11,
                weight="medium", va="top", ha="left")
        y += SEC_LABEL
        ax.text(x_strat, y, "STRATEGY", color=TXT_FAINT, fontsize=9, va="top", ha="left")
        ax.text(x_tr,    y, "TR",       color=TXT_FAINT, fontsize=9, va="top", ha="center")
        ax.text(x_wl,    y, "W/L",      color=TXT_FAINT, fontsize=9, va="top", ha="center")
        ax.text(x_net,   y, "NET",      color=TXT_FAINT, fontsize=9, va="top", ha="right")
        y += COLHEAD
        if not rows:
            ax.text(x_strat, y, "No trades today", color=TXT_DIM, fontsize=11,
                    va="top", ha="left", style="italic")
            y += LINE
        for r in rows:
            net_color = GREEN if r.net >= 0 else RED
            ax.text(x_strat, y, r.name, color=TXT_PRIMARY, fontsize=11, va="top", ha="left")
            ax.text(x_tr,    y, str(r.trades), color=TXT_PRIMARY, fontsize=11, va="top", ha="center")
            ax.text(x_wl,    y, f"{r.wins}/{r.losses}", color=TXT_PRIMARY, fontsize=11, va="top", ha="center")
            ax.text(x_net,   y, _fmt_signed(r.net), color=net_color, fontsize=11, va="top", ha="right")
            y += LINE
        _hline(ax, L, R, y + 4)
        y += RULE_GAP
        sub_color = GREEN if subtotal >= 0 else RED
        ax.text(x_strat, y, "Subtotal", color=TXT_MUTED, fontsize=11, weight="medium", va="top", ha="left")
        ax.text(x_net,   y, _fmt_signed(subtotal), color=sub_color, fontsize=11, weight="medium", va="top", ha="right")
        y += SUBTOTAL

    _section("LIVE", DOT_LIVE, data.live_rows, data.live_subtotal)
    y += SEC_GAP
    _section("PAPER", DOT_PAPER, data.paper_rows, data.paper_subtotal)

    # ---- chart ----
    _hline(ax, L, R, y)
    y += RULE_GAP
    ax.text(L, y, "NET P&L BY STRATEGY", color=TXT_FAINT, fontsize=9,
            va="top", ha="left")
    y += CHART_TITLE

    # chart axis placed in figure-fraction, converted from the px cursor.
    chart_top_frac    = 1.0 - (y / H_PX)
    chart_bottom_frac = PAD_BOT / H_PX
    chart_L_frac      = 0.30
    cax = fig.add_axes([
        chart_L_frac,
        chart_bottom_frac,
        (R / W_PX) - chart_L_frac,
        max(0.05, chart_top_frac - chart_bottom_frac),
    ])
    _draw_chart(cax, all_rows)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_CARD, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _hline(ax, x0, x1, y):
    ax.plot([x0, x1], [y, y], color=BORDER, linewidth=1.0, zorder=1)


def _draw_chart(cax, rows):
    cax.set_facecolor(BG_CARD)
    for spine in cax.spines.values():
        spine.set_visible(False)

    if not rows:
        cax.axis("off")
        return

    # sort by magnitude so the biggest mover reads first (top)
    ordered = sorted(rows, key=lambda r: abs(r.net), reverse=True)
    labels = [f"{r.name} ({'L' if r.mode == 'LIVE' else 'P'})" for r in ordered]
    vals = [r.net for r in ordered]
    colors = [GREEN if v >= 0 else RED for v in vals]

    ypos = range(len(ordered))
    cax.barh(list(ypos), vals, color=colors, height=0.62, zorder=3)
    cax.invert_yaxis()  # first (biggest) at top
    cax.margins(y=0.18)

    cax.set_yticks(list(ypos))
    cax.set_yticklabels(labels, color=TXT_MUTED, fontsize=8.5)
    cax.tick_params(axis="y", length=0)

    # x grid + zero line
    cax.axvline(0, color=TXT_FAINT, linewidth=0.8, zorder=2)
    cax.grid(axis="x", color=GRID, linewidth=0.6, zorder=0)
    cax.set_axisbelow(True)

    # x ticks formatted in k
    import numpy as np
    lo, hi = min(vals + [0]), max(vals + [0])
    span = hi - lo or 1
    pad = span * 0.08
    cax.set_xlim(lo - pad, hi + pad)
    ticks = cax.get_xticks()
    cax.set_xticks(ticks)
    cax.set_xticklabels([_fmt_k_axis(t) for t in ticks], color=TXT_FAINT, fontsize=7.5)
    cax.tick_params(axis="x", length=0)


# ════════════════════════════════════════════════════════════════════
#  SMOKE TEST  (synthetic data matching the approved mockup)
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    demo = CardData(
        date_str=datetime(2026, 6, 9).strftime("%d %b %Y"),
        live_rows=[
            StrategyRow("BB_V1", 12, 8, 4, 1240, "LIVE"),
        ],
        paper_rows=[
            StrategyRow("HA_V1", 24, 7, 17, -114463, "PAPER"),
            StrategyRow("SCALP_V1", 81, 31, 50, 14793, "PAPER"),
            StrategyRow("SCALP_V3", 6, 4, 2, 1180, "PAPER"),
        ],
    )
    png = build_summary_card_png(demo)
    assert png, "render returned None"
    with open("/home/claude/eod_card_demo.png", "wb") as f:
        f.write(png)
    print(f"OK — wrote {len(png):,} bytes")