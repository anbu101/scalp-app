"""
Scalp Fleet Monitor — ── FLEET_MONITOR_20261127 ── (TD-110)

One read-only portal where Anbu and invited friends watch every install's
LIVE trades, critical alerts, daily summary and trade history. Runs on the
license droplet as its OWN service (scalp-monitor, 127.0.0.1:9200) with its
OWN database, behind Caddy (HTTPS on 443): a fault here can never touch the
license server that every app depends on.

  POST /api/v1/ingest      apps: {key, machine_id, legacy_machine_id?, app_version,
                           epoch, events:[{seq,ts,kind,data}], snapshot?, dropped}
                           → {ok, acked, enabled}; 401 unknown key / machine
  GET  /healthz            uptime check
  GET  /login  POST /login POST /logout
  GET  /                   Fleet: one card per install (auto-refresh 30 s)
  GET  /alerts             alerts, filter + acknowledge (POST /alerts/{id}/ack)
  GET  /trades  /trades.csv  LIVE legs from each machine's trade tables
  GET  /timeline           every event of a day, in order (investigation)
  GET  /day                daily summary per machine + fleet
  POST /devices/{key}/enabled   admin: pause / resume an install's sending
  POST /devices/{key}/relay     admin: that machine's criticals to Telegram on / off   ── FLEET_RELAY_PER_MACHINE_20261127 ──
  GET/POST /setup/{token}  one-time link: the invited person sets password + authenticator

── PORTAL_USERS_ADMIN_20261127 ── accounts are managed from the License Admin page
(license server :9100 → /admin/portal/*), which calls the internal API below on
127.0.0.1 with the token in secrets/internal_token.txt. Caddy never forwards
/internal/* (404 there), and the token is required anyway. users.py still works.
  GET  /internal/users                    accounts + pending links
  POST /internal/users/invite             {username, role} → {url, expires}
  POST /internal/users/{name}/{action}    reset | disable | enable | unlock | remove | cancel_invite
"""

from __future__ import annotations

import csv
import io
import os
import secrets
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import accounts   # ── PORTAL_USERS_ADMIN_20261127 ──
import auth
import pages
import relay
import store
import watcher   # ── FLEET_WATCH_20261127 ──

MAX_BODY = 2_000_000
MAX_EVENTS = 500
INGEST_PER_MIN = 60

app = FastAPI(title="Scalp Fleet Monitor", docs_url=None, redoc_url=None, openapi_url=None)
_ingest_hits: dict = {}

SEC_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self';"
                               " frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    accounts.init()            # ── PORTAL_USERS_ADMIN_20261127 ──
    _internal_token()
    store.housekeeping()
    store.backfill_strategies()   # ── ALERT_STRATEGY_INFER_20261127 ──
    watcher.init()             # ── FLEET_WATCH_20261127 ── (TD-112) offline / daily summary / reminders
    if os.environ.get("MONSRV_NO_WATCHER") != "1":
        watcher.start()


@app.middleware("http")
async def _headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SEC_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()[:64]
    return peer[:64]


# ── ingest (apps) ────────────────────────────────────────────────────
def _rate_ok(key: str, now: float) -> bool:
    hits = [t for t in _ingest_hits.get(key, []) if t > now - 60]
    hits.append(now)
    _ingest_hits[key] = hits
    return len(hits) <= INGEST_PER_MIN


@app.post("/api/v1/ingest")
async def ingest(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return JSONResponse({"ok": False, "error": "too large"}, status_code=413)
    try:
        import json
        body = json.loads(raw)
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    now = time.time()
    key = str(body.get("key") or "").strip().upper()[:64]
    lic = store.licence_for(key, str(body.get("machine_id") or "")[:128],
                            str(body.get("legacy_machine_id") or "")[:128] or None)
    if lic is None:
        return JSONResponse({"ok": False, "error": "unknown key or machine"}, status_code=401)
    if not _rate_ok(key, now):
        return JSONResponse({"ok": False, "error": "slow down"}, status_code=429)
    events = body.get("events") if isinstance(body.get("events"), list) else []
    epoch = str(body.get("epoch") or "none")[:64]
    dev = store.upsert_device(lic, str(body.get("machine_id"))[:128], body.get("app_version"),
                              client_ip(request), epoch, int(body.get("dropped") or 0), now)
    res = store.store_events(lic["key"], epoch, events[:MAX_EVENTS], now)
    if isinstance(body.get("snapshot"), dict):
        store.store_snapshot(lic["key"], body["snapshot"], now)
    if res["new"]:
        relay.submit(dev, res["new"])
    return {"ok": True, "acked": res["acked"], "enabled": bool(dev.get("enabled", 1))}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/static/app.css")
def css():
    return Response(pages.CSS, media_type="text/css", headers={"Cache-Control": "max-age=3600"})


# ── portal ───────────────────────────────────────────────────────────
def _user(request: Request) -> Optional[dict]:
    return auth.session(request.cookies.get(auth.COOKIE))


def _to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


async def _form(request: Request) -> dict:
    from urllib.parse import parse_qs
    raw = (await request.body())[:20000].decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(raw).items()}


def _csrf_ok(user: dict, form: dict) -> bool:
    import hmac
    return hmac.compare_digest(str(form.get("csrf") or ""), user["csrf"])


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if _user(request):
        return RedirectResponse("/", status_code=303)
    return pages.login_page()


@app.post("/login")
async def login_post(request: Request):
    f = await _form(request)
    s = auth.login(f.get("username", "")[:64], f.get("password", "")[:200], f.get("code", "")[:10], client_ip(request))
    if not s:
        return HTMLResponse(pages.login_page("Sign-in failed. Check all three fields; after 5 failures the "
                                             "account waits 15 minutes."), status_code=401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(auth.COOKIE, s["token"], max_age=auth.MAX_AGE_S, httponly=True,
                 secure=os.environ.get("MONSRV_INSECURE_COOKIE") != "1", samesite="strict", path="/")
    return r


@app.post("/logout")
async def logout(request: Request):
    u = _user(request)
    f = await _form(request)
    if u and _csrf_ok(u, f):
        auth.logout(request.cookies.get(auth.COOKIE))
        store.audit(u["username"], "logout", "", client_ip(request))
    r = _to_login()
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@app.get("/", response_class=HTMLResponse)
def fleet(request: Request):
    u = _user(request)
    if not u:
        return _to_login()
    now = time.time()
    return pages.fleet_page(u, store.devices(), store.snapshots(), store.unacked_counts(now - 7 * 86400),
                            now, pages.now_ist_day(now), relay.target_label())   # ── FLEET_SWITCH_LABEL_20261127 ──


@app.get("/alerts", response_class=HTMLResponse)
def alerts(request: Request, device: str = "", unacked: str = "", days: int = 1, strategy: str = ""):
    u = _user(request)
    if not u:
        return _to_login()
    days = days if days in (1, 7, 30, 90) else 1
    since = time.time() - days * 86400
    rows = store.alerts(device or None, unacked == "1", since, strategy=strategy[:40] or None)   # ── ALERT_STRATEGY_INFER_20261127 ──
    names = sorted({r["codename"] for r in store.alerts(device or None, False, since, limit=2000) if r.get("codename")})
    return pages.alerts_page(u, store.devices(), rows, device, unacked == "1", days, strategy[:40], names)


@app.post("/alerts/{event_id}/ack")
async def ack(request: Request, event_id: int):
    u = _user(request)
    if not u:
        return _to_login()
    f = await _form(request)
    if not _csrf_ok(u, f):
        return HTMLResponse("Form expired — go back and reload.", status_code=403)
    if store.ack(event_id, u["username"], f.get("note", "")):
        store.audit(u["username"], "ack", str(event_id), client_ip(request))
    from urllib.parse import urlsplit
    ref = urlsplit(request.headers.get("referer") or "")
    back = ref.path + (f"?{ref.query}" if ref.query else "") if ref.path == "/alerts" else "/alerts"
    return RedirectResponse(back, status_code=303)


def _day(s: str, default: str) -> str:
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return default


# ── PORTAL_USERS_ADMIN_20261127 ── one-time setup links ─────────────────
_setup_fails: dict = {}


@app.get("/setup/{token}", response_class=HTMLResponse)
def setup_get(token: str):
    inv = accounts.pending(token)
    if inv is None:
        return HTMLResponse(pages.setup_gone_page(), status_code=410)
    uri = auth.otpauth_uri(inv["username"], inv["totp_secret"])
    return pages.setup_page(inv, accounts.qr_svg(uri), uri)


@app.post("/setup/{token}")
async def setup_post(request: Request, token: str):
    ip = client_ip(request)
    now = time.time()
    if auth._ip_blocked(ip, now):
        return HTMLResponse(pages.setup_gone_page(), status_code=429)
    f = await _form(request)
    sess, err = accounts.complete(token, f.get("password", "")[:200], f.get("password2", "")[:200],
                                  f.get("code", "")[:10], ip, now)
    if not sess:
        auth._ip_fails.setdefault(ip, []).append(now)
        inv = accounts.pending(token, now)
        if inv is None:
            return HTMLResponse(pages.setup_gone_page(), status_code=410)
        uri = auth.otpauth_uri(inv["username"], inv["totp_secret"])
        return HTMLResponse(pages.setup_page(inv, accounts.qr_svg(uri), uri, err), status_code=400)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(auth.COOKIE, sess["token"], max_age=auth.MAX_AGE_S, httponly=True,
                 secure=os.environ.get("MONSRV_INSECURE_COOKIE") != "1", samesite="strict", path="/")
    return r


# ── PORTAL_USERS_ADMIN_20261127 ── internal API for the License Admin page ──
def _internal_token() -> str:
    p = relay.SECRETS_DIR / "internal_token.txt"
    try:
        t = p.read_text().strip()
        if len(t) >= 32:
            return t
    except FileNotFoundError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    p.write_text(t + "\n")
    os.chmod(p, 0o600)
    return t


def _internal_ok(request: Request) -> bool:
    import hmac
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1"):
        return False
    return hmac.compare_digest(request.headers.get("x-internal-token", ""), _internal_token())


def _by(request: Request) -> str:
    return (request.headers.get("x-admin-name") or "license-admin")[:40]


@app.get("/internal/users")
def internal_users(request: Request):
    if not _internal_ok(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, **accounts.listing()}


@app.post("/internal/users/invite")
async def internal_invite(request: Request):
    if not _internal_ok(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    out = accounts.invite(str(body.get("username") or ""), str(body.get("role") or "viewer"), _by(request))
    if isinstance(out, str):
        return JSONResponse({"ok": False, "error": out}, status_code=400)
    return {"ok": True, **out}


@app.post("/internal/users/{name}/{action}")
def internal_action(request: Request, name: str, action: str):
    if not _internal_ok(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    by = _by(request)
    if action == "reset":
        out = accounts.reset(name, by)
        if isinstance(out, str):
            return JSONResponse({"ok": False, "error": out}, status_code=400)
        return {"ok": True, **out}
    fn = {"disable": lambda: accounts.set_disabled(name, True, by),
          "enable": lambda: accounts.set_disabled(name, False, by),
          "unlock": lambda: accounts.unlock(name, by),
          "remove": lambda: accounts.remove(name, by),
          "cancel_invite": lambda: accounts.cancel_invite(name, by)}.get(action)
    if fn is None:
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    if not fn():
        return JSONResponse({"ok": False, "error": f"nothing to {action} for {name}"}, status_code=404)
    return {"ok": True}


def _trade_filters(request: Request) -> dict:
    today = pages.now_ist_day()
    q = request.query_params
    d_from = _day(q.get("from", ""), today)
    d_to = _day(q.get("to", ""), d_from if q.get("from") else today)
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    return {"device": q.get("device", "")[:64], "strategy": q.get("strategy", "").strip().upper()[:40],
            "from": d_from, "to": d_to}


@app.get("/trades", response_class=HTMLResponse)
def trades(request: Request):
    u = _user(request)
    if not u:
        return _to_login()
    f = _trade_filters(request)
    legs = store.legs(f["from"], f["to"], f["device"] or None, f["strategy"] or None)
    return pages.trades_page(u, store.devices(), legs, f)


@app.get("/trades.csv")
def trades_csv(request: Request):
    u = _user(request)
    if not u:
        return _to_login()
    f = _trade_filters(request)
    labels = {d["key"]: d["label"] for d in store.devices()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["day", "machine", "strategy", "symbol", "side", "qty", "entry", "exit", "entry_time_ist",
                "exit_time_ist", "gross", "charges", "net", "reason", "open", "leg_id"])
    for r in store.legs(f["from"], f["to"], f["device"] or None, f["strategy"] or None, limit=50000):
        x = r["data"]
        row = [r.get("day"), labels.get(r["device_key"], r["device_key"]), r.get("strategy_id"), x.get("symbol"),
               "SELL" if x.get("short") else "BUY", x.get("qty"), x.get("entry"), x.get("exit"),
               pages.dt(x.get("entry_ts")), pages.dt(x.get("exit_ts")) if x.get("exit_ts") else "",
               x.get("gross"), x.get("charges"), x.get("net"), x.get("reason"), bool(x.get("open")), x.get("id")]
        w.writerow([("'" + str(v)) if isinstance(v, str) and v[:1] in "=+-@" else v for v in row])
    store.audit(u["username"], "csv", f"{f}", client_ip(request))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="fleet-trades-{f["from"]}-{f["to"]}.csv"'})


@app.get("/timeline", response_class=HTMLResponse)
def timeline(request: Request, day: str = "", device: str = ""):
    u = _user(request)
    if not u:
        return _to_login()
    day = _day(day, pages.now_ist_day())
    rows = store.events(day, device[:64] or None, store.KNOWN_KINDS)
    return pages.timeline_page(u, store.devices(), rows, day, device)


@app.get("/day", response_class=HTMLResponse)
def day_summary(request: Request, day: str = ""):
    u = _user(request)
    if not u:
        return _to_login()
    today = pages.now_ist_day()
    day = _day(day, today)
    return pages.day_page(u, store.devices(), store.day_summaries(day), store.event_counts(day), day)


@app.post("/devices/{key}/relay")   # ── FLEET_RELAY_PER_MACHINE_20261127 ── (TD-117)
async def device_relay(request: Request, key: str):
    u = _user(request)
    if not u:
        return _to_login()
    f = await _form(request)
    if u["role"] != "admin" or not _csrf_ok(u, f):
        return HTMLResponse("Not allowed.", status_code=403)
    if store.device(key):
        on = f.get("relay") == "1"
        store.set_relay(key, on)
        store.audit(u["username"], "relay_on" if on else "relay_off", key, client_ip(request))
    return RedirectResponse("/", status_code=303)


@app.post("/devices/{key}/enabled")
async def device_enabled(request: Request, key: str):
    u = _user(request)
    if not u:
        return _to_login()
    f = await _form(request)
    if u["role"] != "admin" or not _csrf_ok(u, f):
        return HTMLResponse("Not allowed.", status_code=403)
    if store.device(key):
        on = f.get("enabled") == "1"
        store.set_enabled(key, on)
        store.audit(u["username"], "resume" if on else "pause", key, client_ip(request))
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MONSRV_PORT", "9200")),
                proxy_headers=False, access_log=False)
