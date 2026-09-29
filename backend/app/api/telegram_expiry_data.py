"""
EXPIRY CARD DATA SOURCE — week + month buckets from the dashboard's legs read.

app/api/telegram_expiry_data.py          ── EXPIRY_CARD_20260929 ──

build_expiry_card_data() assembles the ExpiryCardData the renderer consumes:

  windows   day0 = 00:00 IST today; week = fleet_today_routes._week_from(day0)
            (7 calendar days ending today); month = _month_from(day0) (same
            calendar day last month). Identical to the Modern dashboard's
            Week P/L / Month P/L, so the card and the ledger agree.
  legs      fleet_today_routes._legs_for(sid) for every FLEET_IDS strategy —
            the same read the dashboard makes (paper_trades + trades + the
            V3/V5/TMA/VET mappers; ROW_LIMIT newest per store). Closed legs
            whose EXIT falls in the window count; net = gross − charges.
  counts    positions, not legs: _positions() (TSG baskets cluster) and
            _baskets() for wins, exactly as the dashboard's closed_trades.
  cells     week strip = one cell per NSE session (is_trading_day) in the
            window; month strip = one cell per expiry week, keyed by
            expected_expiry_for_day(exit day) so a Monday session before a
            Tuesday holiday lands in the same cell as its Tuesday.
  approx    a (strategy, book) is ≈ when its legs read hit ROW_LIMIT inside
            the window or any leg's charges are modelled — the dashboard's
            rule; it propagates to the row and the book total.

Read-only. Every strategy is wrapped so one failing read drops that strategy
and records a warning instead of blanking the card.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from app.event_bus.audit_logger import write_audit_log
from app.config.strategy_display import codename   # ── UI_MASK ── codenames on the PNG
from app.api.telegram_expiry_card import (
    ExpiryCardData, PeriodBook, StrategyPeriodRow, Cell, BOOKS,
)

IST_OFF = 5 * 3600 + 30 * 60


# ───────────────────────── seams (monkeypatched by the suite) ────────────

def _fl():
    from app.api import fleet_today_routes as f
    return f


def _cr():
    from app.api import closed_recent_routes as c
    return c


def _expected_expiry(d: date) -> date:
    from app.backtest.engine.expiry_calendar import expected_expiry_for_day
    return expected_expiry_for_day(d)


def _is_trading_day(d: date) -> bool:
    try:
        from app.utils.market_hours import is_trading_day
        return bool(is_trading_day(d))
    except Exception:
        return d.weekday() < 5


# ───────────────────────── calendar helpers ──────────────────────────────

def _ist_date(ts: int) -> date:
    g = time.gmtime(int(ts) + IST_OFF)
    return date(g.tm_year, g.tm_mon, g.tm_mday)


def _sessions(from_ts: int, to_ts: int) -> List[date]:
    """NSE sessions (IST dates) from `from_ts` to `to_ts`, inclusive."""
    d, end = _ist_date(from_ts), _ist_date(to_ts)
    out = []
    while d <= end:
        if _is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _day_label(d: date) -> str:
    return d.strftime("%a")


def _expiry_label(e: date) -> str:
    return f"{e.day} {e:%b}"


def _week_template(sessions: List[date]) -> List[Cell]:
    return [Cell(d.isoformat(), _day_label(d)) for d in sessions]


def _month_template(sessions: List[date]) -> List[Cell]:
    seen: Dict[str, Cell] = {}
    for d in sessions:
        e = _expected_expiry(d)
        seen.setdefault(e.isoformat(), Cell(e.isoformat(), _expiry_label(e)))
    return list(seen.values())


def _week_range(sessions: List[date]) -> str:
    if not sessions:
        return "no sessions in window"
    a, b = sessions[0], sessions[-1]
    left = f"{a:%a} {a.day}" + ("" if a.month == b.month else f" {a:%b}")
    return f"{left} \u2013 {b:%a} {b.day} {b:%b} \u00b7 {len(sessions)} session{'s' if len(sessions) != 1 else ''}"


def _month_range(month_from: int) -> str:
    d = _ist_date(month_from)
    return f"since {d:%a} {d.day} {d:%b} \u00b7 by expiry week"


# ───────────────────────── aggregation ───────────────────────────────────

def _groups(fl, sid: str, legs: List[dict]) -> List[List[dict]]:
    """Positions: TSG legs cluster into baskets; everything else is one leg."""
    if not legs:
        return []
    if sid in fl.BASKET_STRATEGIES:
        return _cr()._baskets(legs)
    return [[l] for l in legs]


def _net(l: dict) -> float:
    return float(l["gross"]) - float(l["charges"])


def _cell_of(cells: List[Cell], key: str, label: str) -> Cell:
    for c in cells:
        if c.key == key:
            return c
    c = Cell(key, label)          # exit on a day outside the session template
    cells.append(c)
    cells.sort(key=lambda x: x.key)
    return c


def _add_to_cell(c: Cell, net: float, n: int) -> None:
    c.net = (c.net or 0.0) + net
    c.trades += n


def build_expiry_card_data(now: Optional[int] = None, *, expiry: bool = True) -> ExpiryCardData:
    fl, cr = _fl(), _cr()
    now = int(now or time.time())
    day0 = cr._day_start(now)
    week_from = fl._week_from(day0)
    month_from = fl._month_from(day0)
    warns: list = []

    wk_sessions = _sessions(week_from, now)
    mo_sessions = _sessions(month_from, now)
    wk_tpl = _week_template(wk_sessions)
    mo_tpl = _month_template(mo_sessions)

    week = {b: PeriodBook(cells=[Cell(c.key, c.label) for c in wk_tpl]) for b in BOOKS}
    month = {b: PeriodBook(cells=[Cell(c.key, c.label) for c in mo_tpl]) for b in BOOKS}
    day_net: Dict[str, Dict[str, float]] = {b: {} for b in BOOKS}   # month: day key -> net
    rows: List[StrategyPeriodRow] = []

    for sid in fl.FLEET_IDS:
        try:
            legs = fl._legs_for(sid, warns)
        except Exception as e:
            warns.append(f"{sid}: {e!r}")
            continue
        for book in BOOKS:
            book_legs = [l for l in legs if l["book"] == book]
            mine = [l for l in book_legs
                    if not l["open"] and l["exit_ts"] and month_from <= int(l["exit_ts"]) <= now]
            if not mine:
                continue
            truncated = (len(book_legs) >= cr.ROW_LIMIT
                         and any(int(l["entry_ts"] or 0) >= month_from for l in book_legs[-1:]))
            approx = truncated or any(l["approx"] for l in mine)
            wk = [l for l in mine if int(l["exit_ts"]) >= week_from]

            g_m, g_w = _groups(fl, sid, mine), _groups(fl, sid, wk)
            net_m = sum(_net(l) for l in mine)
            net_w = sum(_net(l) for l in wk)
            rows.append(StrategyPeriodRow(codename(sid), book, round(net_w, 2), len(g_w),
                                          round(net_m, 2), len(g_m), approx))

            for period, groups, pb in (("month", g_m, month[book]), ("week", g_w, week[book])):
                for grp in groups:
                    gnet = sum(_net(l) for l in grp)
                    pb.net += gnet
                    pb.trades += 1
                    pb.wins += 1 if gnet >= 0 else 0
                    pb.charges += sum(float(l["charges"]) for l in grp)
                    exit_ts = max(int(l["exit_ts"]) for l in grp)
                    d = _ist_date(exit_ts)
                    if period == "week":
                        _add_to_cell(_cell_of(pb.cells, d.isoformat(), _day_label(d)), gnet, 1)
                    else:
                        e = _expected_expiry(d)
                        _add_to_cell(_cell_of(pb.cells, e.isoformat(), _expiry_label(e)), gnet, 1)
                        day_net[book][d.isoformat()] = day_net[book].get(d.isoformat(), 0.0) + gnet
                pb.approx = pb.approx or approx

    # keep the two books' strips aligned: a cell appended for one book (an
    # exit on a day outside the session template) exists, empty, for the other
    for period in (week, month):
        keys = {c.key: c.label for b in BOOKS for c in period[b].cells}
        for b in BOOKS:
            have = {c.key for c in period[b].cells}
            period[b].cells.extend(Cell(k, lab) for k, lab in keys.items() if k not in have)
            period[b].cells.sort(key=lambda c: c.key)

    for b in BOOKS:
        for pb in (week[b], month[b]):
            pb.net = round(pb.net, 2)
            pb.charges = round(pb.charges, 2)
        month[b].sessions = len(day_net[b])
        month[b].positive_sessions = sum(1 for v in day_net[b].values() if v > 0)

    # Live first, then |month net| descending — the daily card's tile order
    rows.sort(key=lambda r: (0 if r.mode == "LIVE" else 1, -abs(r.month_net)))

    for w in warns:
        write_audit_log(f"[EXPIRY_CARD][WARN] {w}")

    n = datetime.fromtimestamp(now)
    return ExpiryCardData(
        date_str=n.strftime("%d %b %Y"),
        subtitle=(n.strftime("%a %d %b %Y") + " \u00b7 " + n.strftime("%H:%M") + " IST \u00b7 "
                  + ("weekly expiry" if expiry else "not an expiry day (manual)")),
        week_range=_week_range(wk_sessions),
        month_range=_month_range(month_from),
        week=week, month=month, rows=rows, warnings=list(warns),
    )
