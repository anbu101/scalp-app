# backend/app/backtest/dhan/dhan_client.py
#
# DATA-ONLY Dhan client for backtest backfill. By design this class exposes
# ONLY the expired-options rolling-data endpoint. It has NO order/trade methods
# and MUST NEVER gain any — Dhan is used exclusively to backfill historical
# option candles into backtest.db. Live trading remains 100% Zerodha.
#
# Confirmed API contract (verified live against the account):
#   POST https://api.dhan.co/v2/charts/rollingoption
#   headers: access-token, client-id, Content-type, Accept
#   body: exchangeSegment NSE_FNO, interval "1", securityId 13 (NIFTY index),
#         instrument OPTIDX, expiryFlag WEEK|MONTH, expiryCode>=1 (1-BASED!),
#         strike "ATM"|"ATM±N", drvOptionType CALL|PUT,
#         requiredData [open,high,low,close,volume,strike,spot,oi,iv],
#         fromDate,toDate (<=30 days span; toDate non-inclusive)
#   ROLLING model: for each day in range, returns that day's ATM-relative option
#   for the front (expiryCode-th) weekly/monthly expiry AS OF that day. The
#   series rolls identity at each expiry. We reconstruct each day's true expiry
#   = front Tuesday for that day (expected_expiry_for_day) and synthesize the
#   Zerodha symbol ourselves.
#
# KEY GOTCHA (cost us hours): expiryCode is 1-BASED. expiryCode 0 → DH-905.

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

from app.event_bus.audit_logger import write_audit_log

_BASE = "https://api.dhan.co/v2"
_ENDPOINT = "/charts/rollingoption"

# NIFTY index securityId on Dhan (confirmed: 13 returns NIFTY option data).
NIFTY_SECURITY_ID = 13

_REQUIRED = ["open", "high", "low", "close", "volume", "strike", "spot", "oi", "iv"]

# Dhan data-API error codes worth surfacing clearly.
_ERROR_HINTS = {
    "806": "Data APIs not subscribed",
    "807": "Access token expired — regenerate on web.dhan.co",
    "808": "Authentication failed — client id or access token invalid",
    "809": "Access token invalid",
    "810": "Client id invalid",
    "811": "Invalid expiry date",
    "812": "Invalid date format (use YYYY-MM-DD)",
    "813": "Invalid securityId",
    "DH-905": "Input exception — bad/missing parameter (note: expiryCode is 1-based)",
}


class DhanDataError(Exception):
    pass


@dataclass
class RollingSeries:
    """Columnar arrays returned by Dhan for one (expiryFlag, expiryCode, strike,
    side) request over a date range. Each index i is one 1-min candle."""
    timestamp: List[int]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    volume: List[int]
    strike: List[float]   # ABSOLUTE strike, resolved per-candle by Dhan
    spot: List[float]
    oi: List[int]
    iv: List[float]

    def __len__(self) -> int:
        return len(self.timestamp)


class DhanDataClient:
    """Data-only. No order methods. Ever."""

    def __init__(self, client_id: str, access_token: str, *, timeout: int = 60,
                 throttle_s: float = 0.0):
        self._cid = str(client_id)
        self._tok = access_token
        self._timeout = timeout
        self._throttle = throttle_s
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {
            "access-token": self._tok,
            "client-id": self._cid,
            "Content-type": "application/json",
            "Accept": "application/json",
        }

    def check_token(self) -> dict:
        """Call /v2/profile to validate the token and read its expiry. Returns
        {ok, client_id, token_valid_until, hours_left, raw}. Never raises — on
        error returns ok=False with the reason. Used as a pre-flight before a
        long backfill so we don't start a multi-hour run on a dying 24h token."""
        import datetime as _dt
        try:
            r = self._session.get(
                _BASE + "/profile",
                headers={"access-token": self._tok, "client-id": self._cid,
                         "Accept": "application/json"},
                timeout=self._timeout,
            )
            j = r.json()
        except Exception as e:
            return {"ok": False, "reason": f"network error: {e!r}",
                    "hours_left": None}
        if isinstance(j, dict) and (j.get("errorCode") or
                                    (isinstance(j.get("remarks"), dict)
                                     and j["remarks"].get("error_code"))):
            err = j.get("errorCode") or j["remarks"].get("error_code")
            return {"ok": False, "reason": f"Dhan error {err} "
                    f"({_ERROR_HINTS.get(str(err), '')})", "hours_left": None}

        # tokenValidity format seen: "DD/MM/YYYY HH:MM"
        tv = j.get("tokenValidity") or j.get("token_validity")
        hours_left = None
        if tv:
            for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    exp = _dt.datetime.strptime(tv.strip(), fmt)
                    hours_left = (exp - _dt.datetime.now()).total_seconds() / 3600.0
                    break
                except Exception:
                    continue
        return {"ok": True, "client_id": j.get("dhanClientId"),
                "token_valid_until": tv, "hours_left": hours_left,
                "data_plan": j.get("dataPlan"), "raw": j}

    def fetch_rolling_option(
        self, *,
        expiry_flag: str,          # "WEEK" | "MONTH"
        expiry_code: int,          # 1-based! (1 = front)
        strike: str,               # "ATM" | "ATM+N" | "ATM-N"
        option_type: str,          # "CALL" | "PUT"
        from_date: str,            # YYYY-MM-DD
        to_date: str,              # YYYY-MM-DD (non-inclusive)
        security_id: int = NIFTY_SECURITY_ID,
        interval: str = "1",
    ) -> Optional[RollingSeries]:
        if expiry_code < 1:
            raise ValueError("expiry_code is 1-based; minimum is 1 (0 → DH-905)")
        if expiry_flag not in ("WEEK", "MONTH"):
            raise ValueError("expiry_flag must be WEEK or MONTH")
        if option_type not in ("CALL", "PUT"):
            raise ValueError("option_type must be CALL or PUT")

        body = {
            "exchangeSegment": "NSE_FNO",
            "interval": interval,
            "securityId": security_id,
            "instrument": "OPTIDX",
            "expiryFlag": expiry_flag,
            "expiryCode": expiry_code,
            "strike": strike,
            "drvOptionType": option_type,
            "requiredData": _REQUIRED,
            "fromDate": from_date,
            "toDate": to_date,
        }

        if self._throttle:
            time.sleep(self._throttle)

        try:
            r = self._session.post(
                _BASE + _ENDPOINT, headers=self._headers(),
                data=json.dumps(body), timeout=self._timeout,
            )
        except Exception as e:
            raise DhanDataError(f"network error: {e!r}")

        try:
            j = r.json()
        except Exception:
            raise DhanDataError(f"non-JSON response (HTTP {r.status_code}): {r.text[:200]}")

        # Error envelope
        if isinstance(j, dict):
            err = j.get("errorCode")
            if not err and isinstance(j.get("remarks"), dict):
                err = j["remarks"].get("error_code")
            if err:
                hint = _ERROR_HINTS.get(str(err), "")
                raise DhanDataError(f"Dhan error {err}: {hint or j}")

        block = (j.get("data") or {})
        side = "ce" if option_type == "CALL" else "pe"
        ce = block.get(side) or {}
        ts = ce.get("timestamp") or []
        if not ts:
            return None

        def col(name):
            return ce.get(name) or []

        return RollingSeries(
            timestamp=ts, open=col("open"), high=col("high"), low=col("low"),
            close=col("close"), volume=col("volume"), strike=col("strike"),
            spot=col("spot"), oi=col("oi"), iv=col("iv"),
        )


    def fetch_intraday(
        self, *,
        security_id: str,
        from_date: str,            # "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
        to_date: str,
        interval: str = "1",       # 1/5/15/25/60 minutes
        instrument: str = "FUTIDX",
        exchange_segment: str = "NSE_FNO",
        oi: bool = True,
    ) -> Optional[RollingSeries]:
        """Fetch intraday futures candles via /v2/charts/intraday for ONE
        contract's securityId. DATA-ONLY. Returns the same columnar RollingSeries
        shape (strike/iv unused for futures, left empty). <=90 days per call.

        Confirmed live: BANKNIFTY-Jun2026-FUT (securityId 62326) returns
        open/high/low/close/volume/timestamp/open_interest arrays.
        """
        body = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": interval,
            "oi": bool(oi),
            "fromDate": from_date,
            "toDate": to_date,
        }
        if self._throttle:
            time.sleep(self._throttle)
        try:
            r = self._session.post(
                _BASE + "/charts/intraday", headers=self._headers(),
                data=json.dumps(body), timeout=self._timeout,
            )
        except Exception as e:
            raise DhanDataError(f"network error: {e!r}")
        try:
            j = r.json()
        except Exception:
            raise DhanDataError(f"non-JSON response (HTTP {r.status_code}): {r.text[:200]}")

        if isinstance(j, dict):
            err = j.get("errorCode")
            if not err and isinstance(j.get("remarks"), dict):
                err = j["remarks"].get("error_code")
            if err:
                hint = _ERROR_HINTS.get(str(err), "")
                raise DhanDataError(f"Dhan error {err}: {hint or j}")

        ts = j.get("timestamp") or []
        if not ts:
            return None

        def col(name):
            return j.get(name) or []

        n = len(ts)
        return RollingSeries(
            timestamp=ts,
            open=col("open"), high=col("high"), low=col("low"),
            close=col("close"), volume=col("volume"),
            strike=[0.0] * n,                 # futures: no strike
            spot=col("close"),                # spot≈close for the future itself
            oi=col("open_interest"),
            iv=[0.0] * n,                      # futures: no iv
        )

    # Back-compat alias (the method was originally futures-only).
    fetch_intraday_futures = fetch_intraday