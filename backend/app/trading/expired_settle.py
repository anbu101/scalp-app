# backend/app/trading/expired_settle.py
# ── EXPIRED_CARRY_SETTLE_20261021 ──
# ============================================================================
# A POSITIONAL carry is supposed to be hard-closed ON its contract's expiry
# day by the running engine (expiry_today = expiry == today). If the app was
# NOT running that day, the next boot found expiry < today, so the equality
# never matched again: the expired legs were re-adopted as a live carry,
# marked from stale prices, and never closed (2026-10-21: a friend's TMA_V2
# 22800PE/21700PE spread, expiry 2026-10-19 — app off on expiry Monday).
#
# The exchange already settled those contracts. This module mirrors that at
# boot, BEFORE adoption: every OPEN leg whose expiry is strictly before today
# (IST) is closed at its FINAL SETTLEMENT — intrinsic value against the
# NIFTY 50 official close on the expiry date (NSE index-option settlement):
#     CE: max(0, close − strike)      PE: max(0, strike − close)
# No orders are placed in either mode (the contract no longer trades; for a
# LIVE book the broker has already settled it). exit_reason = "EXPIRED",
# exit_ts = 15:30 IST on the expiry day.
#
# If the settlement close cannot be fetched, the legs are marked STALE
# (never adopted, never priced by invention) and the user is alerted to
# review — the existing "never invent an exit" doctrine.
#
# Strategy-independent by construction: each caller passes its own repo
# callbacks; nothing here depends on another strategy running.
# ============================================================================

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

try:
    from app.event_bus.audit_logger import write_audit_log
except Exception:                                    # standalone tests
    def write_audit_log(msg: str) -> None:
        print(msg)

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_INDEX_TOKEN = 256265          # NSE:NIFTY 50 (instrument master constant)
REASON = "EXPIRED"


# ── small pure helpers ──────────────────────────────────────────────────────
def ist_today() -> date:
    return datetime.now(IST).date()


def expiry_date(row: Dict) -> Optional[date]:
    s = str(row.get("expiry") or "").strip()[:10]
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def split_expired(rows: List[Dict], today: Optional[date] = None
                  ) -> Tuple[List[Dict], List[Dict]]:
    """(expired, rest). Expired = expiry strictly before today. A row
    expiring TODAY is not expired — the engine's own expiry-day close owns
    it. A row with no parseable expiry is never touched."""
    today = today or ist_today()
    expired, rest = [], []
    for r in rows or []:
        d = expiry_date(r)
        (expired if (d is not None and d < today) else rest).append(r)
    return expired, rest


def _opt_type(row: Dict) -> str:
    t = str(row.get("instrument_type") or "").upper()
    if t in ("CE", "PE"):
        return t
    return str(row.get("tradingsymbol") or "")[-2:].upper()


_SYM = re.compile(r"^NIFTY(\d{2}[0-9OND]\d{2}|\d{2}[A-Z]{3})(\d+)(CE|PE)$")


def _strike(row: Dict) -> Optional[float]:
    k = row.get("strike")
    try:
        if k is not None and float(k) > 0:
            return float(k)
    except (TypeError, ValueError):
        pass
    m = _SYM.match(str(row.get("tradingsymbol") or "").replace(" ", "").upper())
    return float(m.group(2)) if m else None


def _is_short(row: Dict) -> bool:
    return str(row.get("direction") or "").upper() in ("SELL", "SHORT")


def intrinsic(opt_type: str, strike: float, spot: float) -> float:
    if opt_type == "CE":
        return max(0.0, spot - strike)
    if opt_type == "PE":
        return max(0.0, strike - spot)
    raise ValueError(f"unknown option type {opt_type!r}")


def settle_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).timestamp())


def nifty_close_on(kite, d: date) -> Optional[float]:
    """Official NIFTY 50 close for day d via Kite historical (day candle)."""
    if kite is None:
        return None
    try:
        rows = kite.historical_data(NIFTY_INDEX_TOKEN, d, d, "day") or []
    except Exception as e:
        write_audit_log(f"[EXPIRED_SETTLE] NIFTY close fetch for {d} failed: {e!r}")
        return None
    for r in rows:
        rd = r.get("date")
        try:
            rd = rd.date() if hasattr(rd, "date") else date.fromisoformat(str(rd)[:10])
        except Exception:
            continue
        if rd == d and r.get("close"):
            return float(r["close"])
    return None


def _leg_money(row: Dict, settle_px: float) -> Tuple[float, float, float]:
    entry = float(row["entry_price"])
    qty = int(row["qty"])
    short = _is_short(row)
    gross = (entry - settle_px) * qty if short else (settle_px - entry) * qty
    charges = 0.0
    try:
        from app.backtest.charges.charges_model import (
            charges_for_long_trade, charges_for_short_trade)
        fn = charges_for_short_trade if short else charges_for_long_trade
        # the model returns ALL-ZERO charges for exit_price 0 (falsy guard),
        # which would drop the real entry-side costs of a worthless expiry;
        # price charges at one tick instead (≈ one extra exit brokerage).
        charges = float(getattr(fn(entry_price=entry,
                                   exit_price=max(float(settle_px), 0.05),
                                   qty=qty), "total_charges", 0.0) or 0.0)
    except Exception:
        charges = 0.0
    return round(gross, 2), round(charges, 2), round(gross - charges, 2)


# ── the settlement pass ─────────────────────────────────────────────────────
def settle_open_rows(*, sid: str, rows: List[Dict], kite,
                     close_fn: Callable[..., None],
                     stale_fn: Callable[[Dict, str], None],
                     notify: Optional[Callable] = None,
                     alert: Optional[Callable] = None,
                     today: Optional[date] = None,
                     close_lookup: Optional[Callable[[date], Optional[float]]] = None,
                     reason: str = REASON) -> List[Dict]:
    """Settle every expired OPEN leg in `rows`; return the rows still to adopt.

    close_fn(row, exit_ts=, exit_price=, exit_reason=, pnl=, charges=, net_pnl=)
    stale_fn(row, note)
    """
    expired, rest = split_expired(rows, today)
    if not expired:
        return rest
    lookup = close_lookup or (lambda d: nifty_close_on(kite, d))
    closes: Dict[date, Optional[float]] = {}
    groups: Dict[str, List[Dict]] = {}
    for r in expired:
        groups.setdefault(str(r.get("group_id") or r.get("id")), []).append(r)

    for gid, legs in groups.items():
        exp = expiry_date(legs[0])
        mode = str(legs[0].get("mode") or "PAPER").upper()
        if exp not in closes:
            closes[exp] = lookup(exp)
        spot = closes[exp]
        syms = " + ".join(str(l.get("tradingsymbol")) for l in legs)

        priced = []
        if spot is not None:
            for l in legs:
                k, t = _strike(l), _opt_type(l)
                if k is None or t not in ("CE", "PE"):
                    priced = None
                    break
                priced.append((l, round(intrinsic(t, k, spot), 2)))

        if spot is None or priced is None:
            why = ("NIFTY settlement close unavailable" if spot is None
                   else "strike/type unreadable")
            for l in legs:
                try:
                    stale_fn(l, f"expired {exp} unpriced")
                except Exception as e:
                    write_audit_log(f"[{sid}][EXPIRED_SETTLE] stale mark failed "
                                    f"#{l.get('id')}: {e!r}")
            msg = (f"{sid}: carried {mode} position {syms} expired on {exp} while "
                   f"the app was not running. {why} — legs marked STALE (not "
                   f"adopted). Review the P&L manually.")
            write_audit_log(f"[{sid}][EXPIRED_SETTLE][STALE] group {gid}: {msg}")
            _emit(sid, msg, "error", notify, alert)
            continue

        net_total = 0.0
        parts = []
        ts = settle_ts(exp)
        for l, px in priced:
            gross, ch, net = _leg_money(l, px)
            try:
                close_fn(l, exit_ts=ts, exit_price=px, exit_reason=reason,
                         pnl=gross, charges=ch, net_pnl=net)
            except Exception as e:
                write_audit_log(f"[{sid}][EXPIRED_SETTLE] close failed "
                                f"#{l.get('id')}: {e!r}")
                continue
            net_total += net
            parts.append(f"{l.get('tradingsymbol')} "
                         f"{'S' if _is_short(l) else 'B'}@{px:.2f}")
        msg = (f"{sid}: carried {mode} position expired on {exp} while the app "
               f"was not running — settled at NIFTY close {spot:,.2f}: "
               f"{', '.join(parts)} · net ₹{net_total:,.0f}"
               + (" · no orders placed (exchange already settled it)"
                  if mode == "LIVE" else ""))
        write_audit_log(f"[{sid}][EXPIRED_SETTLE] group {gid}: {msg}")
        _emit(sid, msg, "warning", notify, alert)
    return rest


def _emit(sid, msg, severity, notify, alert) -> None:
    if alert is not None:
        try:
            alert(msg, severity)
        except Exception:
            pass
    if notify is not None:
        try:
            notify(msg, severity)
        except Exception:
            pass


def _bell(sid: str):
    def _a(msg, severity):
        from app.event_bus.inapp_events import record_alert
        record_alert("EXPIRED_SETTLE", msg, severity=severity, strategy_id=sid)
    return _a


# ── repo adapters (one per table family) ────────────────────────────────────
def settle_tma_family(sid: str, repo, rows: List[Dict], *, kite,
                      notify=None) -> List[Dict]:
    """TMA_V1 / TMA_V2 (tma_trades / tma2_trades — SELL/BUY legs)."""
    return settle_open_rows(
        sid=sid, rows=rows, kite=kite, notify=notify, alert=_bell(sid),
        close_fn=lambda r, **k: repo.close_leg(int(r["id"]), ambiguous=False, **k),
        stale_fn=lambda r, note: repo.mark_stale(int(r["id"])))


def settle_vet(repo, rows: List[Dict], *, kite, notify=None) -> List[Dict]:
    """VET_V1 (vet_trades — SHORT/LONG legs, MAIN/WING roles)."""
    return settle_open_rows(
        sid="VET_V1", rows=rows, kite=kite, notify=notify, alert=_bell("VET_V1"),
        reason="EXPIRY_EXIT",                      # VET's own vocabulary (R_EXPIRY)
        close_fn=lambda r, **k: repo.close_leg(int(r["id"]), **k),
        stale_fn=lambda r, note: repo.mark_stale(int(r["id"]), note))
