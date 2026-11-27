# backend/app/monitor/shipper.py — ── FLEET_MONITOR_20261127 ── (TD-110)
#
# Sends the outbox and the LIVE snapshot to the Fleet Monitor on the license
# droplet. Supervised by api_server like the liveness ping.
#
# TRANSPORT. HTTPS to the droplet's bare IP with SNI + Host set to the free
# sslip.io name, verified against the normal public CA bundle for that name:
# encrypted and authenticated, with no DNS lookup at all, so the free name
# service being down never stops the data. (No paid domain: TD-110.)
#
# CADENCE. Every TICK_S: unsent events, in seq order, up to BATCH per POST.
# The snapshot rides along every SNAP_OPEN_S while the market is open and
# every SNAP_CLOSED_S outside it; it doubles as the heartbeat the portal's
# online/offline badge reads. The server acks the highest seq it stored; only
# those rows are marked sent, so a lost reply resends (the server de-dups).
#
# FAILURE. Never raises into the app. Transport errors back off 5 s → 5 min;
# a refused key/machine waits REFUSED_WAIT_S. One audit line per state change.
# Without a license key the loop idles. The server can pause an install
# ("enabled": false) without a release; events keep queueing meanwhile.
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional, Tuple

from app.event_bus.audit_logger import write_audit_log
from app.monitor import outbox, snapshot

MONITOR_IP = "139.59.8.202"
MONITOR_HOST = "139-59-8-202.sslip.io"
INGEST_PATH = "/api/v1/ingest"
TICK_S = 5
BATCH = 200
SNAP_OPEN_S = 60
SNAP_CLOSED_S = 300
BACKOFF_MAX_S = 300
REFUSED_WAIT_S = 1800
PAUSED_WAIT_S = 600
PRUNE_EVERY_S = 3600
TIMEOUT_S = 10.0


def endpoint() -> Tuple[str, str, Any]:
    """(url, sni/host name, verify). Env overrides are for tests only."""
    url = os.environ.get("SCALP_MONITOR_URL") or f"https://{MONITOR_IP}"
    host = os.environ.get("SCALP_MONITOR_SNI") or MONITOR_HOST
    verify: Any = os.environ.get("SCALP_MONITOR_CA") or True
    return url.rstrip("/") + INGEST_PATH, host, verify


def _identity() -> Optional[Dict[str, Any]]:
    try:
        from app.license.license_client import KEY_FILE, _read_text
        from app.license.machine_id import device_ids
        key = (_read_text(KEY_FILE) or "").strip()
        if not key:
            return None
        try:
            from app.license.version_check import _read_current_version
            ver = _read_current_version()
        except Exception:
            ver = None
        return {"key": key, **device_ids(), "app_version": ver}
    except Exception:
        return None


def post(body: Dict[str, Any]):
    import ssl

    import httpx
    url, host, ca = endpoint()
    verify: Any = ssl.create_default_context(cafile=ca) if isinstance(ca, str) else True
    with httpx.Client(verify=verify, timeout=TIMEOUT_S) as c:
        return c.post(url, json=body, headers={"Host": host}, extensions={"sni_hostname": host})


class Shipper:
    """One instance per process. step() does one cycle synchronously and
    returns the seconds to wait before the next; tests drive it directly."""

    def __init__(self, clock=time.time, poster=None, snap=None, ident=None):
        self.clock = clock
        self.poster = poster or post
        self.snap = snap or snapshot.build
        self.ident = ident or _identity
        self.next_snap = 0.0
        self.market_open = False
        self.backoff = TICK_S
        self.next_prune = 0.0
        self.state: Optional[str] = None

    def _log_state(self, state: str, msg: str) -> None:
        if state != self.state:
            self.state = state
            write_audit_log(f"[MONITOR] {msg}")

    def step(self) -> float:
        now = self.clock()
        ident = self.ident()
        if ident is None:
            self._log_state("nokey", "no license key — fleet monitor idle")
            return 60.0
        if now >= self.next_prune:
            self.next_prune = now + PRUNE_EVERY_S
            try:
                outbox.prune(now)
            except Exception:
                pass
        events = outbox.pending(BATCH)
        snap_due = now >= self.next_snap
        if not events and not snap_due:
            return TICK_S
        body: Dict[str, Any] = {**ident, "epoch": outbox.epoch(), "events": events,
                                "dropped": outbox.dropped_total(), "client_ts": now}
        if snap_due:
            body["snapshot"] = self.snap()
        try:
            r = self.poster(body)
            code = int(getattr(r, "status_code", 0))
            data = r.json() if code == 200 else {}
        except Exception as e:
            self.backoff = min(BACKOFF_MAX_S, max(TICK_S, self.backoff * 2))
            self._log_state("down", f"monitor unreachable ({type(e).__name__}) — events kept, retrying")
            return float(self.backoff)
        if code in (401, 403):
            self._log_state("refused", f"monitor refused this install (HTTP {code}) — retry in 30 min")
            return float(REFUSED_WAIT_S)
        if code != 200 or not data.get("ok"):
            self.backoff = min(BACKOFF_MAX_S, max(TICK_S, self.backoff * 2))
            self._log_state("down", f"monitor answered HTTP {code} — events kept, retrying")
            return float(self.backoff)
        self.backoff = TICK_S
        acked = data.get("acked")
        if acked is not None:
            outbox.mark_sent(int(acked))
        if snap_due:
            s = body.get("snapshot") or {}
            self.market_open = bool(s.get("market_open"))
            self.next_snap = now + (SNAP_OPEN_S if self.market_open else SNAP_CLOSED_S)
        if data.get("enabled") is False:
            self._log_state("paused", "monitor paused this install from the server")
            return float(PAUSED_WAIT_S)
        self._log_state("up", "connected to the fleet monitor")
        return 0.5 if len(events) >= BATCH else TICK_S


async def monitor_ship_loop() -> None:
    """Perpetual. Supervised by api_server."""
    write_audit_log("[MONITOR] ship loop started")
    sh = Shipper()
    loop = asyncio.get_event_loop()
    while True:
        try:
            wait = await loop.run_in_executor(None, sh.step)
        except Exception as e:                      # belt: never die
            write_audit_log(f"[MONITOR] loop error swallowed: {e!r}")
            wait = 60.0
        await asyncio.sleep(wait)
