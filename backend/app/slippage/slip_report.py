# backend/app/slippage/slip_report.py
#
# ── SLIP_VIEW_20261031 ── slippage of live fills, from the per-order stamps
# SLIP_CAPTURE records (reference = the LTP the app held when it sent the
# order; fill = Kite's average price).
#   per unit  BUY: fill − ref   SELL: ref − fill      (positive = paid away)
#   ₹         per unit × filled qty
#   delay     exchange fill time − send time (seconds)
# Orders without a reference LTP or a fill are listed but not averaged.

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

FENCE = "SLIP_VIEW_20261031"
IST = timezone(timedelta(hours=5, minutes=30))


def _name(sid: Optional[str]) -> str:
    if not sid:
        return "—"
    try:
        from app.config.strategy_display import codename
        return codename(sid)
    except Exception:
        return sid


def fills(days: int = 10, now: Optional[int] = None) -> List[dict]:
    from app.slippage.slip_store import connect
    now = int(now or time.time())
    day0 = int(datetime.fromtimestamp(now, IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    lo = day0 - (max(1, int(days)) - 1) * 86400
    c = connect()
    try:
        rows = c.execute("SELECT order_id, op, side, symbol, ref_ltp, ref_age_s, sent_at, ack_at, error, strategy_id, "
                         "status, qty, filled_qty, fill_price, order_ts, fill_ts FROM order_stamps WHERE sent_at >= ? "
                         "ORDER BY sent_at DESC", (lo,)).fetchall()
    finally:
        c.close()
    out = []
    for (oid, op, side, sym, ref, age, sent, ack, err, sid, st, qty, fq, fp, ots, fts) in rows:
        per = None
        if ref and fp and (fq or 0) > 0:
            per = round((fp - ref) if side == "BUY" else (ref - fp), 2)
        out.append({
            "order_id": oid, "op": op, "side": side, "symbol": sym, "strategy_id": sid, "name": _name(sid),
            "day": datetime.fromtimestamp(int(sent), IST).date().isoformat(),
            "time": datetime.fromtimestamp(int(sent), IST).strftime("%H:%M:%S"),
            "ref": ref, "ref_age_s": age, "fill": fp, "qty": qty, "filled_qty": fq, "status": st, "error": err,
            "slip_per_unit": per, "slip_rs": round(per * fq, 2) if per is not None else None,
            "ack_ms": round((ack - sent) * 1000) if ack and sent else None,
            "delay_s": (round(fts - sent, 1) if fts and sent else None)})
    return out


def _agg(rows: List[dict]) -> dict:
    measured = [r for r in rows if r["slip_rs"] is not None]
    units = sum(r["filled_qty"] or 0 for r in measured)
    slip = sum(r["slip_rs"] for r in measured)
    delays = [r["delay_s"] for r in measured if r["delay_s"] is not None]
    return {"orders": len(rows), "measured": len(measured), "units": units, "slip_rs": round(slip, 2),
            "per_unit": round(slip / units, 2) if units else None,
            "adverse": sum(1 for r in measured if r["slip_rs"] > 0),
            "avg_delay_s": round(sum(delays) / len(delays), 1) if delays else None,
            "rejected": sum(1 for r in rows if str(r["status"] or "").upper() in ("REJECTED", "CANCELLED") or r["error"])}


def summary(days: int = 10, now: Optional[int] = None) -> dict:
    fs = fills(days, now)
    by_sid: Dict[str, list] = defaultdict(list)
    by_day_sid: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in fs:
        k = r["strategy_id"] or "?"
        by_sid[k].append(r)
        by_day_sid[r["day"]][k].append(r)
    today = datetime.fromtimestamp(int(now or time.time()), IST).date().isoformat()
    return {
        "days": int(days), "today": today,
        "strategies": {k: {"name": _name(k if k != "?" else None), **_agg(v)} for k, v in by_sid.items()},
        "today_by_strategy": {k: {"name": _name(k if k != "?" else None), **_agg(v)}
                              for k, v in by_day_sid.get(today, {}).items()},
        "by_day": {d: {k: _agg(v) for k, v in m.items()} for d, m in sorted(by_day_sid.items(), reverse=True)},
        "total": _agg(fs),
    }
