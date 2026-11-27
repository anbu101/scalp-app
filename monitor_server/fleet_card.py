"""
monitor_server/fleet_card.py — ── FLEET_SUMMARY_CARD_20261127 ── (TD-126)

The 15:50 fleet summary as ONE image, like the app's Daily summary card (same
Midnight palette): a machine × strategy matrix that stays readable from 1 to 15+
machines and 1 to 6+ strategies. Chosen by Anbu 27 Nov 2026 ("Matrix").

  header   Fleet summary · date · 15:50 IST;  "N machines · K strategies · LIVE"
  boxes    Fleet booked (before charges) · Net after charges · Week · Month
  matrix   one row per machine, best first, no-trade machines last ("—");
           one column per strategy anyone traded today; each cell = booked P/L
           and closed trades, teal (profit) / rose (loss), stronger = bigger;
           Booked and Net at the right; a Fleet row with each strategy's total
           and how many machines ran it.  ● = legs still open (not in totals).
  footer   best / worst machine, best strategy, open at close (+ charges)

It adapts:
  1 machine       no Fleet row (it would repeat the machine); first box is
                  "<name> booked"; footer: best / worst strategy.
  1 strategy      no grid (one column would repeat Booked): Trades + a P/L bar
                  on the fleet scale; header says "<strategy> only".
  1 × 1           one plain row: Trades, Booked, Net.
  > 6 strategies  the 5 with most machines (then most booked) + "Others".
  nobody lost     "Worst" reads "Lowest".
  big numbers     boxes shrink the font to fit (₹2,94,900); cells use 9.8k / 1.11L.

Pure: render(data) -> PNG bytes, or None on ANY failure (the caller then sends
the text summary). layout(data) is the plan render draws, for the tests.

data = {"date": "Fri 27 Nov 2026", "time": "15:50", "week": float|None, "month": float|None,
        "machines": [{"name", "booked", "charges", "trades", "open_legs", "unrealised",
                      "strategies": {name: (booked, trades)}}]}
"""

from __future__ import annotations

import io
import os
from typing import Any, Dict, List, Optional, Tuple

S = 2                     # pixel scale (920 px design → 1840 px image)
W = 920
PAD = 56
BG = (0x12, 0x0f, 0x24)
TILE = (0x1b, 0x17, 0x38)
BORDER = (0x2b, 0x25, 0x52)
TXT = (0xec, 0xeb, 0xff)
MUTED = (0xa9, 0xa4, 0xd6)
DIM = (0x73, 0x6e, 0xa6)
FAINT = (0x5b, 0x56, 0x8a)
UP = (0x5e, 0xea, 0xd4)
DN = (0xfb, 0x71, 0x85)
AMBER = (0xfb, 0xbf, 0x24)
MAX_COLS = 6
CELL_MAX = 96
NAME_MIN = 118
BOOKED_W = 128
NET_W = 108

FONT_DIRS = [os.environ.get("MONSRV_FONT_DIR", ""), "/usr/share/fonts/truetype/dejavu"]


def _font_paths() -> Tuple[Optional[str], Optional[str]]:
    dirs = list(FONT_DIRS)
    try:   # local runs: matplotlib ships DejaVu
        import matplotlib
        dirs.append(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf"))
    except Exception:
        pass
    for d in dirs:
        r, b = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
        if d and os.path.exists(r) and os.path.exists(b):
            return r, b
    return None, None


# ── numbers ──────────────────────────────────────────────────────────

def inr(x: Any, sign: bool = True) -> str:
    """Indian grouping, as the app prints it: +₹1,28,667."""
    try:
        v = round(float(x))
    except Exception:
        return "—"
    s = str(abs(v))
    if len(s) > 3:
        h, t = s[:-3], s[-3:]
        g: List[str] = []
        while len(h) > 2:
            g.insert(0, h[-2:])
            h = h[:-2]
        if h:
            g.insert(0, h)
        s = ",".join(g) + "," + t
    pre = ("+" if v > 0 else "−" if v < 0 else "") if sign else ("−" if v < 0 else "")
    return pre + "₹" + s


def short(x: float) -> str:
    """Cell form: +9.8k, −450, +1.11L."""
    a = abs(x)
    s = f"{a / 100000:.2f}L" if a >= 100000 else f"{a / 1000:.1f}k" if a >= 1000 else f"{a:.0f}"
    return ("+" if x > 0 else "−" if x < 0 else "") + s


def _col(x: float):
    return UP if x > 0 else DN if x < 0 else MUTED


def _mix(c, a: float, base=BG):
    return tuple(int(base[i] + (c[i] - base[i]) * a) for i in range(3))


# ── plan ─────────────────────────────────────────────────────────────

def layout(data: Dict[str, Any]) -> Dict[str, Any]:
    ms = []
    for m in data.get("machines") or []:
        st = {str(k): (float(v[0] or 0), int(v[1] or 0)) for k, v in (m.get("strategies") or {}).items()}
        ms.append(dict(name=str(m.get("name") or "machine"), st=st,
                       booked=float(m.get("booked") or 0), charges=float(m.get("charges") or 0),
                       trades=int(m.get("trades") or 0), open_legs=int(m.get("open_legs") or 0),
                       unrealised=float(m.get("unrealised") or 0)))
    ms.sort(key=lambda r: (r["trades"] == 0, -r["booked"], r["name"].lower()))
    names = sorted({s for r in ms for s in r["st"]})
    tot = {s: (sum(r["st"][s][0] for r in ms if s in r["st"]), sum(1 for r in ms if s in r["st"]),
               sum(r["st"][s][1] for r in ms if s in r["st"])) for s in names}
    order = sorted(names, key=lambda s: (-tot[s][1], -tot[s][0], s))
    cols, others = order, []
    if len(order) > MAX_COLS:
        cols, others = order[:MAX_COLS - 1], order[MAX_COLS - 1:]
    one_m, one_s = len(ms) == 1, len(names) <= 1
    fb = sum(r["booked"] for r in ms)
    fc = sum(r["charges"] for r in ms)
    ft = sum(r["trades"] for r in ms)
    act = [r for r in ms if r["trades"]]

    foot: List[Tuple[str, str, Any, str]] = []
    if len(act) > 1:
        b, w = act[0], min(act, key=lambda r: r["booked"])
        foot.append(("Best machine", inr(b["booked"]), _col(b["booked"]), b["name"]))
        foot.append(("Worst machine" if w["booked"] < 0 else "Lowest machine", inr(w["booked"]), _col(w["booked"]), w["name"]))
    if len(names) > 1:
        bs = max(names, key=lambda s: tot[s][0])
        ws = min(names, key=lambda s: tot[s][0])
        foot.append(("Best strategy", inr(tot[bs][0]), _col(tot[bs][0]),
                     bs if one_m else f"{bs} · {tot[bs][1]} machine{'' if tot[bs][1] == 1 else 's'}"))
        if one_m:
            foot.append(("Worst strategy" if tot[ws][0] < 0 else "Lowest strategy", inr(tot[ws][0]), _col(tot[ws][0]), ws))
    opn = [r for r in ms if r["open_legs"]]
    foot.append(("Open at close", f"{sum(r['open_legs'] for r in opn)} leg{'' if sum(r['open_legs'] for r in opn) == 1 else 's'}"
                 if opn else "none", TXT, " · ".join(f"{r['name']} {inr(r['unrealised'])}" for r in opn) or "all flat"))
    if len(foot) < 4:
        foot.append(("Charges", inr(-fc), TXT, f"{ft} trade{'' if ft == 1 else 's'}"))

    def box(label, v, sub):
        return (label, inr(v) if v is not None else "—", _col(v) if v is not None else MUTED,
                sub if v is not None else "not reported")
    boxes = [(f"{ms[0]['name']} booked" if one_m else "Fleet booked", inr(fb), _col(fb), "before charges"),
             ("Net after charges", inr(fb - fc), _col(fb - fc), f"charges {inr(-fc)}"),
             box("Week", data.get("week"), "7 days · net"),
             box("Month", data.get("month"), "1 month · net")]
    sub = (f"{len(ms)} machine{'' if one_m else 's'} · "
           + (f"{names[0]} only" if len(names) == 1 else f"{len(names)} strategies" if names else "no trades"))
    return dict(machines=ms, cols=cols, others=others, totals=tot, mode="bar" if one_s else "grid",
                bar=one_s and not one_m and bool(names), fleet_row=not one_m, booked=fb, charges=fc, trades=ft,
                boxes=boxes, foot=foot[:4], open=bool(opn), header_sub=sub)


# ── drawing ──────────────────────────────────────────────────────────

class _Pen:
    def __init__(self, img, reg, bold):
        from PIL import ImageDraw, ImageFont
        self.d = ImageDraw.Draw(img)
        self._F = ImageFont
        self.reg, self.bold = reg, bold
        self._cache: Dict[Tuple[int, bool], Any] = {}

    def font(self, size, bold=False):
        k = (size, bold)
        if k not in self._cache:
            path = self.bold if bold else self.reg
            self._cache[k] = (self._F.truetype(path, size * S) if path else self._F.load_default(size * S))
        return self._cache[k]

    def w(self, s, size, bold=False):
        return self.d.textlength(s, font=self.font(size, bold)) / S

    def text(self, x, y, s, size, fill, bold=False, right=False):
        f = self.font(size, bold)
        if right:
            x = x - self.w(s, size, bold)
        self.d.text((x * S, y * S), s, font=f, fill=fill)

    def fit(self, s, size, maxw, bold=False, floor=12):
        while size > floor and self.w(s, size, bold) > maxw:
            size -= 1
        return size

    def clip(self, s, size, maxw, bold=False):
        if self.w(s, size, bold) <= maxw:
            return s
        while s and self.w(s + "…", size, bold) > maxw:
            s = s[:-1]
        return s + "…"

    def rect(self, x0, y0, x1, y1, fill, r=12):
        self.d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=r * S, fill=fill)

    def line(self, x0, y0, x1, y1, fill, w=2):
        self.d.line((x0 * S, y0 * S, x1 * S, y1 * S), fill=fill, width=w * S)

    def dot(self, x, y, r, fill):
        self.d.ellipse(((x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S), fill=fill)


ROW_H = 51
CELL_H = 45


def _height(L) -> int:
    n = len(L["machines"]) + (1 if L["fleet_row"] else 0)
    return 40 + 90 + 98 + 22 + 30 + n * ROW_H + (26 if L["open"] else 0) + 24 + 92 + 26


def _draw(data: Dict[str, Any]) -> bytes:
    from PIL import Image
    L = layout(data)
    reg, bold = _font_paths()
    H = _height(L)
    img = Image.new("RGB", (W * S, H * S), BG)
    p = _Pen(img, reg, bold)
    R = W - PAD

    # header
    p.text(PAD, 40, "Fleet summary", 34, TXT)
    p.text(PAD, 86, f"{data.get('date') or ''} · {data.get('time') or '15:50'} IST", 19, DIM)
    p.text(R, 44, f"Scalp Fleet · {L['header_sub']} · LIVE", 16, DIM, right=True)
    p.text(R, 66, "booked P/L · before charges", 16, DIM, right=True)

    # boxes
    y = 130
    bw = (R - PAD - 3 * 12) / 4
    for i, (label, val, colr, sub) in enumerate(L["boxes"]):
        x = PAD + i * (bw + 12)
        p.rect(x, y, x + bw, y + 98, TILE)
        p.text(x + 16, y + 12, p.clip(label, 15, bw - 32), 15, DIM)   # ── FLEET_CARD_LABEL_FIT_20261127 ── long names
        p.text(x + 16, y + 34, val, p.fit(val, 30, bw - 32, bold=True), colr, bold=True)
        p.text(x + 16, y + 72, p.clip(sub, 14, bw - 32), 14, DIM)

    # matrix
    y += 98 + 22
    net_r = R - 10
    booked_r = R - NET_W - 10
    cells_r = R - NET_W - BOOKED_W
    ms = L["machines"]
    if L["mode"] == "grid":
        cols = L["cols"] + (["Others"] if L["others"] else [])
        n = len(cols)
        cw = max(64, min(CELL_MAX, (cells_r - PAD - NAME_MIN) / max(n, 1) - 3))
        x_cols = [cells_r - (n - i) * (cw + 3) for i in range(n)]
        name_w = x_cols[0] - PAD - 14 if n else cells_r - PAD
    else:
        n, cols, cw, x_cols = 0, [], 0, []
        trades_r = cells_r - (300 if L["bar"] else 0) - 10
        name_w = trades_r - 70 - PAD
    hy = y
    p.text(PAD + 10, hy, "Machine", 14, DIM)
    for c, x in zip(cols, x_cols):
        p.text(x + cw - 10, hy, p.clip(c, 14, cw - 8), 14, DIM, right=True)
    if L["mode"] == "bar":
        p.text(trades_r, hy, "Trades", 14, DIM, right=True)
    p.text(booked_r, hy, "Booked", 14, DIM, right=True)
    p.text(net_r, hy, "Net", 14, DIM, right=True)
    y = hy + 30
    mx = max([abs(r["booked"]) for r in ms] + [1.0])

    def cell(x, v):
        if v is None:
            p.dot(x + cw / 2, y + CELL_H / 2, 1.5, FAINT)
            return
        a = min(abs(v[0]) / 10000, 1) * 0.55 + 0.08
        fill = _mix(UP, a) if v[0] > 0 else _mix(DN, a) if v[0] < 0 else TILE
        p.rect(x, y, x + cw, y + CELL_H, fill, r=6)
        p.text(x + cw - 10, y + 6, short(v[0]), p.fit(short(v[0]), 16, cw - 14), TXT, right=True)
        p.text(x + cw - 10, y + 26, f"{v[1]} tr", 12, _mix(TXT, 0.7, fill), right=True)

    for r in ms:
        nm = p.clip(r["name"], 18, name_w - (18 if r["open_legs"] else 0))
        p.text(PAD + 10, y + 12, nm, 18, TXT)
        if r["open_legs"]:
            p.dot(PAD + 10 + p.w(nm, 18) + 12, y + 24, 4, AMBER)
        if L["mode"] == "grid":
            for c, x in zip(cols, x_cols):
                if c == "Others":
                    v = [r["st"][s] for s in L["others"] if s in r["st"]]
                    cell(x, (sum(a for a, _ in v), sum(b for _, b in v)) if v else None)
                else:
                    cell(x, r["st"].get(c))
        else:
            p.text(trades_r, y + 13, str(r["trades"]), 16, MUTED, right=True)
            if L["bar"]:
                bx0, bx1 = trades_r + 20, cells_r - 10
                p.rect(bx0, y + 18, bx1, y + 28, BORDER, r=3)
                wv = abs(r["booked"]) / mx * (bx1 - bx0)
                if wv > 0:
                    p.rect(bx0, y + 18, bx0 + max(wv, 6), y + 28, _col(r["booked"]), r=3)
        p.text(booked_r, y + 12, inr(r["booked"]) if r["trades"] else "—", 18, _col(r["booked"]) if r["trades"] else MUTED, bold=True, right=True)
        if r["trades"]:
            p.text(net_r, y + 13, inr(r["booked"] - r["charges"]), 16, MUTED, right=True)
        y += ROW_H

    if L["fleet_row"]:
        p.text(PAD + 10, y + 12, "Fleet", 16, DIM)
        if L["mode"] == "grid":
            for c, x in zip(cols, x_cols):
                ss = L["others"] if c == "Others" else [c]
                v = sum(L["totals"][s][0] for s in ss)
                k = len({r["name"] for r in ms for s in ss if s in r["st"]})
                p.text(x + cw - 10, y + 6, short(v), p.fit(short(v), 16, cw - 14, bold=True), _col(v), bold=True, right=True)
                p.text(x + cw - 10, y + 27, f"{k} mach.", 12, DIM, right=True)
        else:
            p.text(trades_r, y + 13, str(L["trades"]), 16, MUTED, right=True)
        fb = inr(L["booked"])
        p.text(booked_r, y + 12, fb, p.fit(fb, 18, BOOKED_W - 6, bold=True), _col(L["booked"]), bold=True, right=True)
        fn = inr(L["booked"] - L["charges"])
        p.text(net_r, y + 13, fn, p.fit(fn, 16, NET_W - 6), MUTED, right=True)
        y += ROW_H
    if L["open"]:
        p.dot(PAD + 4, y + 9, 4, AMBER)
        p.text(PAD + 14, y, f"legs still open at {data.get('time') or '15:50'} — not booked, not in the totals", 14, DIM)
        y += 26

    # footer
    y += 8
    p.line(PAD, y, R, y, BORDER)
    y += 16
    fw = (R - PAD) / max(len(L["foot"]), 1)
    for i, (label, val, colr, sub) in enumerate(L["foot"]):
        x = PAD + i * fw
        p.text(x, y, label, 15, DIM)
        p.text(x, y + 22, val, p.fit(val, 22, fw - 12), colr)
        p.text(x, y + 54, p.clip(sub, 14, fw - 12), 14, DIM)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render(data: Dict[str, Any]) -> Optional[bytes]:
    """PNG bytes, or None on any failure (fail-open: the caller sends the text summary)."""
    try:
        return _draw(data)
    except Exception as e:  # noqa: BLE001
        print(f"[CARD] fleet card failed, text summary instead: {e!r}")
        return None
