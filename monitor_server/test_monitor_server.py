# monitor_server/test_monitor_server.py — ── FLEET_MONITOR_20261127 ── (TD-110) TC-MONSRV-0001…0027
#                                        ── FLEET_MONITOR_FIX_20261127 ── (DEF-108, DEF-109) TC-MONSRV-0028…0030
#                                        ── PORTAL_USERS_ADMIN_20261127 ── (TD-113) TC-MONSRV-0031…0040
#                                        ── MONITOR_DEPLOY_FILES_20261127 ── (DEF-110) TC-MONSRV-0041
#                                        ── PORTAL_COPY_FIX_20261127 ── TC-MONSRV-0042
#                                        ── FLEET_RELAY_PER_MACHINE_20261127 ── (TD-116, TD-117) TC-MONSRV-0006/0007 rewritten, 0043…0046
#                                        ── FLEET_OPEN_CADENCE_20261127 ── (TD-119) TC-MONSRV-0047…0048
#                                        ── FLEET_WATCH_20261127 ── (TD-112) TC-MONSRV-0049…0056
#                                        ── FLEET_SWITCH_LABEL_20261127 ── TC-MONSRV-0044 reworded, 0057
#                                        ── KITE_STATUS_PORTAL_20261127 ── (TD-112) TC-MONSRV-0058
#                                        ── LICENSE_HTTPS_20261127 ── (TD-111) TC-MONSRV-0059
#                                        ── ALERT_STRATEGY_INFER_20261127 ── TC-MONSRV-0060…0064
#                                        ── FLEET_MEMBERSHIP_20261127 ── TC-MONSRV-0065…0070
#
# The Fleet Monitor service end to end: the real FastAPI app under uvicorn on
# a free local port (no Starlette TestClient), a throw-away monitor.db and a
# fake licenses.db. Telegram is never called: the relay queue is read.
#   python3 monitor_server/test_monitor_server.py
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="monsrv_"))
(TMP / "secrets").mkdir()
os.environ.update(MONSRV_DB=str(TMP / "monitor.db"), MONSRV_LICENSE_DB=str(TMP / "licenses.db"),
                  MONSRV_SECRETS_DIR=str(TMP / "secrets"), MONSRV_LICENSE_TELEGRAM=str(TMP / "tg.json"),
                  MONSRV_INSECURE_COOKIE="1", MONSRV_NO_WATCHER="1",
                  MONSRV_NEW_MACHINES="auto")   # the older checks expect new machines straight in; 0065+ set "approval"
sys.path.insert(0, str(HERE))
FAILS, OK = [], [0]


def check(n, c, d=""):
    if c:
        OK[0] += 1
        print(f"  PASS  {n}")
    else:
        FAILS.append(n)
        print(f"  FAIL  {n}  [{str(d)[:300]}]")


with sqlite3.connect(TMP / "licenses.db") as c:
    c.execute("CREATE TABLE licenses (key TEXT PRIMARY KEY, label TEXT, tier TEXT, machine_id TEXT, revoked INTEGER)")
    c.executemany("INSERT INTO licenses VALUES (?,?,?,?,?)", [
        ("KEY-RAVI", "Ravi <b>", "STANDARD", "HW-RAVI", 0),
        ("KEY-ANBU", "Anbu main", "ADMIN", "HW-ANBU", 0),
        ("KEY-OLD", "Old Mac", "STANDARD", "MAC-OLD", 1)])
(TMP / "tg.json").write_text('{"bot_token": "123:abc", "chat_id": "999"}')

import httpx      # noqa: E402
import uvicorn    # noqa: E402
import accounts   # noqa: E402
import auth       # noqa: E402
import pages      # noqa: E402
import relay      # noqa: E402
import server     # noqa: E402
import store      # noqa: E402

relay._ensure_worker = lambda: None          # nothing is sent; the queue is read below


def drain():
    out = []
    while not relay._q.empty():
        out.append(relay._q.get_nowait())
    return out


s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
cfg = uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning")
uv = uvicorn.Server(cfg)
threading.Thread(target=uv.run, daemon=True).start()
for _ in range(100):
    if uv.started:
        break
    time.sleep(0.05)
BASE = f"http://127.0.0.1:{PORT}"
api = httpx.Client(base_url=BASE, timeout=10)


def ingest(key, mid, events=(), snapshot=None, legacy=None, epoch="E1"):
    b = {"key": key, "machine_id": mid, "app_version": "12.0.22", "epoch": epoch, "events": list(events)}
    if legacy:
        b["legacy_machine_id"] = legacy
    if snapshot is not None:
        b["snapshot"] = snapshot
    return api.post("/api/v1/ingest", json=b)


now = time.time()
ev = lambda seq, kind, **d: {"seq": seq, "ts": now, "kind": kind, "data": d}   # noqa: E731

# ── ingest ───────────────────────────────────────────────────────────
r1 = ingest("KEY-NOPE", "X")
r2 = ingest("KEY-RAVI", "HW-WRONG")
r3 = ingest("key-old", "HW-NEW", legacy="MAC-OLD")
check("TC-MONSRV-0001 unknown key or wrong machine → 401; the legacy (MAC) id and a revoked licence still report",
      r1.status_code == 401 and r2.status_code == 401 and r3.status_code == 200, (r1.text, r2.text, r3.text))

r = ingest("KEY-RAVI", "HW-RAVI", [ev(1, "TRADE_ENTRY", codename="Icarus", symbol="NIFTY", qty=75, entry_price=100),
                                   ev(2, "CRITICAL", codename="Icarus", message="exit not confirmed <script>x</script>",
                                      severity="error"),
                                   ev(3, "WHATEVER", x=1)])
j = r.json()
with store.conn() as c:
    n1 = c.execute("SELECT COUNT(*) FROM events WHERE device_key='KEY-RAVI'").fetchone()[0]
r = ingest("KEY-RAVI", "HW-RAVI", [ev(1, "TRADE_ENTRY", symbol="NIFTY"), ev(2, "CRITICAL", message="again")])
with store.conn() as c:
    n2 = c.execute("SELECT COUNT(*) FROM events WHERE device_key='KEY-RAVI'").fetchone()[0]
check("TC-MONSRV-0002 events stored once: acked = highest seq; a resend is de-duplicated; unknown kinds acked, not stored",
      j == {"ok": True, "acked": 3, "enabled": True} and n1 == 2 and n2 == 2 and r.json()["acked"] == 2, (j, n1, n2))
r = ingest("KEY-RAVI", "HW-RAVI", [ev(1, "TRADE_ENTRY", symbol="NEW EPOCH")], epoch="E2")
with store.conn() as c:
    n3 = c.execute("SELECT COUNT(*) FROM events WHERE device_key='KEY-RAVI'").fetchone()[0]
check("TC-MONSRV-0003 a new outbox epoch with seq 1 again is a new event, not a duplicate", n3 == 3, n3)

today = store.ist_day(now)
snap = {"day": today, "market_open": True,
        "totals": {"net": 1500, "realised": 1000, "unrealised": 500, "open_legs": 1, "closed_trades": 2},
        "rows": [{"id": "IC_V2", "codename": "Icarus", "state": "OPEN", "open_legs": 1, "closed_trades": 2, "net": 1500}],
        "legs": [{"id": "LIVE:1", "strategy_id": "IC_V2", "symbol": "NIFTY26D0224000CE", "short": True, "qty": 75,
                  "entry": 20, "exit": 10, "entry_ts": now - 600, "exit_ts": now - 60, "open": False, "net": 700,
                  "reason": "TP"},
                 {"id": "LIVE:2", "strategy_id": "IC_V2", "symbol": "=HYPERLINK(1)", "short": False, "qty": 75,
                  "entry": 5, "entry_ts": now - 300, "open": True}]}
ingest("KEY-RAVI", "HW-RAVI", snapshot=snap)
snap["legs"][1].update(open=False, exit=6, exit_ts=now, net=70)
ingest("KEY-RAVI", "HW-RAVI", snapshot=snap)
legs = store.legs(today, today, "KEY-RAVI", None)
check("TC-MONSRV-0004 snapshot legs are kept by leg id and updated in place (open → closed); day summary kept",
      len(legs) == 2 and {lg["leg_id"]: lg["is_open"] for lg in legs} == {"LIVE:1": 0, "LIVE:2": 0}
      and store.day_summaries(today)[0]["totals"]["net"] == 1500, legs)

# ── relay ────────────────────────────────────────────────────────────
drain()
relay._reset_for_tests()
ingest("KEY-RAVI", "HW-RAVI", [ev(10, "CRITICAL", message="m10"), ev(11, "TRADE_EXIT", symbol="X")])
q = drain()
check("TC-MONSRV-0005 a new critical from a friend's install goes to the default chat; trade events are not relayed",
      len(q) == 1 and q[0][1] == "999" and "m10" in q[0][2] and "Ravi &lt;b&gt;" in q[0][2], q)
ingest("KEY-ANBU", "HW-ANBU", [ev(1, "CRITICAL", message="anbu mac")])
q = drain()
check("TC-MONSRV-0006 an install on an ADMIN licence is relayed by default (a friend can hold one: TD-117)",
      len(q) == 1 and "anbu mac" in q[0][2], q)
(TMP / "secrets" / "monitor.json").write_text(
    '{"telegram_targets": [{"chat_id": "111", "admin_devices": false}, {"chat_id": "-100222", "admin_devices": true}]}')
ingest("KEY-ANBU", "HW-ANBU", [ev(2, "ORDER_REJECTED", message="RMS")])
q = drain()
check("TC-MONSRV-0007 monitor.json targets: every listed chat gets the alert; an old admin_devices key is ignored",
      sorted(x[1] for x in q) == ["-100222", "111"], q)
relay._reset_for_tests()
ingest("KEY-RAVI", "HW-RAVI", [ev(100 + i, "CRITICAL", message=f"c{i}") for i in range(10)])
q = [x for x in drain() if x[1] == "111"]
check("TC-MONSRV-0008 a burst is capped at 6 messages a minute per install; the rest fold into one '+N more' line",
      len(q) == 6 and all("CRITICAL" in x[2] for x in q), [x[2][:30] for x in q])
relay._sent.clear()
ingest("KEY-RAVI", "HW-RAVI", [ev(200, "SYSTEM_ALERT", message="later", title="RELAY")])
q = [x for x in drain() if x[1] == "111"]
check("TC-MONSRV-0009 the folded count is sent with the next message: '+4 more alert(s)'",
      len(q) == 2 and "➕ 4 more" in q[1][2], [x[2][:40] for x in q])
relay._reset_for_tests()
ingest("KEY-RAVI", "HW-RAVI", [{"seq": 300 + i, "ts": now - 7200, "kind": "CRITICAL", "data": {"message": "old"}}
                               for i in range(5)])
q = [x for x in drain() if x[1] == "111"]
check("TC-MONSRV-0010 a backlog (older than 30 min on arrival) becomes ONE 'reconnected with N older alerts' line",
      len(q) == 1 and "reconnected with 5 older alert(s)" in q[0][2], q)
(TMP / "secrets" / "monitor.json").unlink()
(TMP / "tg.json").unlink()
ingest("KEY-RAVI", "HW-RAVI", [ev(400, "CRITICAL", message="nobody")])
check("TC-MONSRV-0011 no bot token / no target: relay is off, ingest still works", drain() == [])

# ── auth ─────────────────────────────────────────────────────────────
check("TC-MONSRV-0012 TOTP matches RFC 6238 (SHA-1 test vector, T=59 → 287082)",
      auth.totp_at("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 1) == "287082")
h = auth.hash_password("correct horse battery")
check("TC-MONSRV-0013 passwords: scrypt hash, right one accepted, wrong one refused, hash never the password",
      h.startswith("scrypt$") and auth.check_password("correct horse battery", h)
      and not auth.check_password("wrong", h) and "horse" not in h)
sec_v = auth.add_user("ravi", "viewer-pass-123")
sec_a = auth.add_user("anbu", "admin-pass-1234", "admin")


def code(sec, dt=0):
    return auth.totp_at(sec, int((time.time() + dt) // 30))


web = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
guarded = ["/", "/alerts", "/trades", "/trades.csv", "/timeline", "/day"]
check("TC-MONSRV-0014 every portal page needs a login (303 → /login)",
      all(web.get(p).status_code == 303 and web.get(p).headers["location"] == "/login" for p in guarded))
bad1 = web.post("/login", data={"username": "ravi", "password": "nope-nope-nope", "code": code(sec_v)})
bad2 = web.post("/login", data={"username": "ravi", "password": "viewer-pass-123", "code": "000000"})
check("TC-MONSRV-0015 a wrong password or a wrong code is refused (401) with the same message",
      bad1.status_code == 401 and bad2.status_code == 401 and "Sign-in failed" in bad1.text and bad1.text == bad2.text)
os.environ.pop("MONSRV_INSECURE_COOKIE")
c1 = code(sec_v, -30)
ok = web.post("/login", data={"username": "ravi", "password": "viewer-pass-123", "code": c1})
setc = ok.headers.get("set-cookie", "").lower()
os.environ["MONSRV_INSECURE_COOKIE"] = "1"
check("TC-MONSRV-0016 a good login sets an HttpOnly, Secure, SameSite=Strict session cookie",
      ok.status_code == 303 and "httponly" in setc and "secure" in setc and "samesite=strict" in setc, setc)
web.cookies.clear()
rep = web.post("/login", data={"username": "ravi", "password": "viewer-pass-123", "code": c1})
check("TC-MONSRV-0017 an authenticator code works once (a replay is refused)", rep.status_code == 401)
for _ in range(5):
    web.post("/login", data={"username": "anbu", "password": "wrong-wrong-wrong", "code": "123456"})
locked = web.post("/login", data={"username": "anbu", "password": "admin-pass-1234", "code": code(sec_a)})
with store.conn() as c:
    c.execute("UPDATE users SET locked_until=0, failed=0 WHERE username='anbu'")
check("TC-MONSRV-0018 five failed logins lock the account for 15 min, even with the right password",
      locked.status_code == 401)

viewer = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
viewer.post("/login", data={"username": "ravi", "password": "viewer-pass-123", "code": code(sec_v)})
admin = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
admin.post("/login", data={"username": "anbu", "password": "admin-pass-1234", "code": code(sec_a, 30)})
fleet = viewer.get("/")
check("TC-MONSRV-0019 Fleet page: the friend's install with its LIVE net; device text is escaped",
      fleet.status_code == 200 and "Ravi &lt;b&gt;" in fleet.text and "+₹1,500" in fleet.text
      and "<b>" not in fleet.text.split("<main>")[1].replace("<b>Fleet Monitor</b>", ""), fleet.text[:400])
al = viewer.get("/alerts?days=1")
check("TC-MONSRV-0020 Alerts page lists criticals with the message escaped (no live <script>)",
      al.status_code == 200 and "&lt;script&gt;" in al.text and "<script>" not in al.text)
with store.conn() as c:
    eid = c.execute("SELECT id FROM events WHERE kind='CRITICAL' AND device_key='KEY-RAVI' ORDER BY id LIMIT 1").fetchone()[0]
csrf = auth.session(viewer.cookies.get(auth.COOKIE))["csrf"]
no = viewer.post(f"/alerts/{eid}/ack", data={"note": "x"})
yes = viewer.post(f"/alerts/{eid}/ack", data={"note": "called Ravi", "csrf": csrf},
                  headers={"referer": f"{BASE}/alerts?days=7&unacked=1"})
with store.conn() as c:
    ack = c.execute("SELECT username, note FROM acks WHERE event_id=?", (eid,)).fetchone()
check("TC-MONSRV-0021 acknowledge needs the form's CSRF token; it records who and the note, and keeps the filters",
      no.status_code == 403 and yes.status_code == 303 and yes.headers["location"] == "/alerts?days=7&unacked=1"
      and tuple(ack) == ("ravi", "called Ravi"), (no.status_code, yes.headers, ack))
pv = viewer.post("/devices/KEY-RAVI/enabled", data={"csrf": csrf, "enabled": "0"})
a_csrf = auth.session(admin.cookies.get(auth.COOKIE))["csrf"]
pa = admin.post("/devices/KEY-RAVI/enabled", data={"csrf": a_csrf, "enabled": "0"})
after = ingest("KEY-RAVI", "HW-RAVI").json()
admin.post("/devices/KEY-RAVI/enabled", data={"csrf": a_csrf, "enabled": "1"})
check("TC-MONSRV-0022 only an admin can pause an install; the install is told enabled=false on its next ingest",
      pv.status_code == 403 and pa.status_code == 303 and after["enabled"] is False, (pv.status_code, pa.status_code, after))
csv_txt = viewer.get(f"/trades.csv?from={today}&to={today}").text
tr = viewer.get(f"/trades?from={today}&to={today}")
check("TC-MONSRV-0023 Trades page + CSV show the legs; a cell starting with '=' is neutralised in the CSV",
      tr.status_code == 200 and "NIFTY26D0224000CE" in tr.text and "'=HYPERLINK(1)" in csv_txt, csv_txt[:300])
hdr = viewer.get("/").headers
check("TC-MONSRV-0024 security headers: strict CSP (no scripts), nosniff, no-store, DENY frames; no /docs",
      "default-src 'none'" in hdr.get("content-security-policy", "") and hdr.get("x-content-type-options") == "nosniff"
      and hdr.get("cache-control") == "no-store" and hdr.get("x-frame-options") == "DENY"
      and api.get("/docs").status_code == 404)
for _ in range(server.INGEST_PER_MIN + 1):
    last = ingest("KEY-OLD", "MAC-OLD")
check("TC-MONSRV-0025 ingest is rate-limited per licence (429 past 60 a minute)", last.status_code == 429)
lo = viewer.post("/logout", data={"csrf": csrf})
check("TC-MONSRV-0026 log out ends the session", lo.status_code == 303 and viewer.get("/").status_code == 303)
day = admin.get("/day")
tl = admin.get(f"/timeline?day={today}")
check("TC-MONSRV-0027 Day summary and Timeline pages render for a signed-in user",
      day.status_code == 200 and "Ravi &lt;b&gt;" in day.text and tl.status_code == 200 and "TRADE ENTRY" in tl.text)


# ── portal accounts from the License Admin page (PORTAL_USERS_ADMIN_20261127) ──
TOK = (TMP / "secrets" / "internal_token.txt").read_text().strip()
IH = {"X-Internal-Token": TOK}
r0 = api.get("/internal/users")
r1 = api.get("/internal/users", headers={"X-Internal-Token": "wrong" * 8})
r2 = api.get("/internal/users", headers=IH)
check("TC-MONSRV-0031 internal user API: no token or a wrong token → 403; the token file (0600) opens it",
      r0.status_code == 403 and r1.status_code == 403 and r2.status_code == 200 and r2.json()["ok"]
      and oct((TMP / "secrets" / "internal_token.txt").stat().st_mode & 0o777) == "0o600", (r0.text, r2.text))
cf = (HERE / "Caddyfile").read_text()
check("TC-MONSRV-0032 Caddy answers /internal/* with 404 before it proxies anything (never reachable from outside)",
      "respond /internal/* 404" in cf and cf.index("respond /internal/* 404") < cf.index("reverse_proxy"))
bad = api.post("/internal/users/invite", headers=IH, json={"username": "Bad Name!", "role": "viewer"})
dup = api.post("/internal/users/invite", headers=IH, json={"username": "ravi", "role": "viewer"})
inv = api.post("/internal/users/invite", headers=IH, json={"username": "Kumar", "role": "viewer"}).json()
tok1 = inv["url"].rsplit("/", 1)[1]
lst = api.get("/internal/users", headers=IH).json()
check("TC-MONSRV-0033 invite: a bad name or an existing account is refused with a reason; a good one returns a "
      "48 h HTTPS setup link and shows as pending",
      bad.status_code == 400 and "Username" in bad.json()["error"] and dup.status_code == 400
      and "already has an account" in dup.json()["error"] and inv["url"].startswith("https://139-59-8-202.sslip.io/setup/")
      and inv["username"] == "kumar" and 47.9 * 3600 < inv["expires"] - time.time() <= 48 * 3600
      and [i["username"] for i in lst["invites"]] == ["kumar"], (bad.text, dup.text, inv, lst))
anon = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
pg = anon.get(f"/setup/{tok1}")
secret_k = accounts.pending(tok1)["totp_secret"]
try:
    import qrcode  # noqa: F401
    has_qr = True
except Exception:
    has_qr = False
check("TC-MONSRV-0034 the setup page opens without a login: the name, the setup key, a tap-to-add link, a QR code "
      "(when qrcode is installed), still no scripts allowed",
      pg.status_code == 200 and "kumar" in pg.text and secret_k in pg.text and 'href="otpauth://totp/' in pg.text
      and (("<svg" in pg.text) == has_qr) and "default-src 'none'" in pg.headers.get("content-security-policy", ""),
      pg.text[:300])
e1 = anon.post(f"/setup/{tok1}", data={"password": "longpassword1", "password2": "longpassword2", "code": "000000"})
e2 = anon.post(f"/setup/{tok1}", data={"password": "short", "password2": "short", "code": "000000"})
e3 = anon.post(f"/setup/{tok1}", data={"password": "longpassword1", "password2": "longpassword1", "code": "000000"})
check("TC-MONSRV-0035 setup mistakes (passwords differ, too short, wrong code) say what is wrong and keep the link",
      e1.status_code == 400 and "different" in e1.text and e2.status_code == 400 and "10 characters" in e2.text
      and e3.status_code == 400 and "authenticator code" in e3.text and accounts.pending(tok1) is not None)
ok = anon.post(f"/setup/{tok1}", data={"password": "kumar-pass-123", "password2": "kumar-pass-123",
                                        "code": auth.totp_at(secret_k, int(time.time() // 30))})
home = anon.get("/")
again = anon.get(f"/setup/{tok1}")
u = auth.get_user("kumar")
check("TC-MONSRV-0036 a good setup creates the account (role from the invite), signs them in, and the link is spent",
      ok.status_code == 303 and home.status_code == 200 and again.status_code == 410
      and u and u["role"] == "viewer" and auth.check_password("kumar-pass-123", u["pw_hash"]), (ok.status_code, home.status_code))
rs = api.post("/internal/users/kumar/reset", headers=IH).json()
tok2 = rs["url"].rsplit("/", 1)[1]
still = auth.check_password("kumar-pass-123", auth.get_user("kumar")["pw_hash"])
sec2 = accounts.pending(tok2)["totp_secret"]
time.sleep(0.01)
done2 = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False).post(
    f"/setup/{tok2}", data={"password": "kumar-new-pass", "password2": "kumar-new-pass",
                            "code": auth.totp_at(sec2, int(time.time() // 30))})
u2 = auth.get_user("kumar")
check("TC-MONSRV-0037 reset: the old login works until the link is used; then the new password + authenticator "
      "replace it and every old session ends",
      rs["mode"] == "reset" and still and done2.status_code == 303 and u2["totp_secret"] == sec2 != secret_k
      and auth.check_password("kumar-new-pass", u2["pw_hash"]) and anon.get("/").status_code == 303, (rs, done2.status_code))
a1 = api.post("/internal/users/invite", headers=IH, json={"username": "late", "role": "admin"}).json()
a2 = api.post("/internal/users/invite", headers=IH, json={"username": "late", "role": "admin"}).json()
old_gone = anon.get(f"/setup/{a1['url'].rsplit('/', 1)[1]}").status_code
with store.conn() as c:
    c.execute("UPDATE invites SET expires=? WHERE username='late'", (time.time() - 1,))
exp_gone = anon.get(f"/setup/{a2['url'].rsplit('/', 1)[1]}").status_code
check("TC-MONSRV-0038 a new link replaces the unused one; an expired link is refused (410)",
      old_gone == 410 and exp_gone == 410, (old_gone, exp_gone))
b = api.post("/internal/users/invite", headers=IH, json={"username": "burn", "role": "viewer"}).json()
tb = b["url"].rsplit("/", 1)[1]
for _ in range(accounts.MAX_TRIES):
    accounts.complete(tb, "longpassword1", "longpassword1", "000000", "10.9.9.9")
check("TC-MONSRV-0039 ten wrong tries burn the link", accounts.pending(tb) is None)
kc = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
kc.post("/login", data={"username": "kumar", "password": "kumar-new-pass", "code": auth.totp_at(sec2, int(time.time() // 30) + 1)})
d1 = api.post("/internal/users/kumar/disable", headers=IH)
after_dis = kc.get("/").status_code
with store.conn() as c:
    c.execute("UPDATE users SET locked_until=? WHERE username='kumar'", (time.time() + 600,))
locked = [x for x in api.get("/internal/users", headers=IH).json()["users"] if x["username"] == "kumar"][0]["status"]
d2 = api.post("/internal/users/kumar/enable", headers=IH)
d3 = api.post("/internal/users/kumar/unlock", headers=IH)
st = [x for x in api.get("/internal/users", headers=IH).json()["users"] if x["username"] == "kumar"][0]
d4 = api.post("/internal/users/kumar/remove", headers=IH)
d5 = api.post("/internal/users/kumar/remove", headers=IH)
d6 = api.post("/internal/users/kumar/explode", headers=IH)
d7 = api.post("/internal/users/burn/cancel_invite", headers=IH)
check("TC-MONSRV-0040 disable signs them out; enable / unlock restore; remove deletes; repeat → 404; unknown action → 400",
      d1.status_code == 200 and after_dis == 303 and locked == "disabled" and d2.status_code == 200
      and d3.status_code == 200 and st["status"] == "active" and st["last_login"]
      and d4.status_code == 200 and auth.get_user("kumar") is None and d5.status_code == 404
      and d6.status_code == 400 and d7.status_code == 404,
      (d1.text, after_dis, locked, st, d4.text, d5.status_code, d6.status_code, d7.text))

# ── deploy scripts (FLEET_MONITOR_FIX_20261127) ──────────────────────
import shutil      # noqa: E402
import subprocess  # noqa: E402


def cron_block(path):
    """The deploy script's crontab block, from its `crontab -l` line to its check line."""
    lines = Path(path).read_text().splitlines()
    i = next(k for k, ln in enumerate(lines) if "crontab -l 2>/dev/null" in ln)
    j = next(k for k in range(i, len(lines)) if lines[k].startswith("crontab -l | grep -q"))
    return "\n".join(lines[i:j + 1])


def run_cron(block, existing):
    d = Path(tempfile.mkdtemp(prefix="cron_"))
    (d / "bin").mkdir()
    fake = d / "bin" / "crontab"
    fake.write_text('#!/bin/bash\nF="$CT_FILE"\nif [ "$1" = "-l" ]; then [ -f "$F" ] || { echo "no crontab for root" >&2; exit 1; }; '
                    'cat "$F"; exit 0; fi\nif [ "$1" = "-" ]; then D=$(cat); printf "%s\\n" "$D" > "$F"; exit 0; fi\nexit 2\n')
    fake.chmod(0o755)
    ct = d / "ct"
    if existing is not None:
        ct.write_text(existing)
    env = dict(os.environ, PATH=f"{d / 'bin'}:{os.environ['PATH']}", CT_FILE=str(ct))
    r = subprocess.run(["bash", "-c", "set -e\nDIR=/opt/x\n" + block + "\necho DONE"], env=env,
                       capture_output=True, text=True)
    out = ct.read_text() if ct.exists() else ""
    shutil.rmtree(d, ignore_errors=True)
    return r.returncode, r.stdout, out


ROOT = HERE.parent
for tc, path, marker in (("TC-MONSRV-0028", ROOT / "monitor_server" / "deploy-monitor.command", "scalp-monitor-backup"),
                         ("TC-MONSRV-0029", ROOT / "license_server" / "deploy-license-server.command", "scalp-license-backup")):
    blk = cron_block(path)
    rc0, so0, empty = run_cron(blk, None)
    rc1, so1, kept = run_cron(blk, "5 5 * * * other-job\n")
    rc2, so2, again = run_cron(blk, kept)
    check(f"{tc} {path.name}: backup cron installs on an EMPTY crontab under set -e, keeps other jobs, "
          f"and a re-run does not duplicate it (DEF-109)",
          rc0 == 0 and "DONE" in so0 and empty.count(marker) == 1
          and rc1 == 0 and "other-job" in kept and kept.count(marker) == 1
          and rc2 == 0 and again.count(marker) == 1 and "other-job" in again,
          (rc0, empty, rc1, kept, rc2, again))
dep = (ROOT / "monitor_server" / "deploy-monitor.command").read_text()
iv = dep.index("caddy validate --config")
ic = dep.find("chown -R caddy:caddy /var/log/caddy", iv)
ir = dep.find("systemctl reload caddy", iv)
check("TC-MONSRV-0030 deploy-monitor: the Caddy log folder is given back to caddy AFTER `caddy validate` "
      "(which runs as root and creates the log file) and before Caddy reloads (DEF-108)",
      iv < ic < ir, (iv, ic, ir))

fl = subprocess.run(["bash", "-c", next(ln for ln in dep.splitlines() if ln.startswith("FILES=")) + '\necho "$FILES"'],
                    cwd=HERE, capture_output=True, text=True).stdout.split()
mods = sorted(p.name for p in HERE.glob("*.py") if not p.name.startswith("test_"))
imported = sorted(m + ".py" for m in re.findall(r"^import (\w+)", (HERE / "server.py").read_text(), re.M)
                  if (HERE / (m + ".py")).exists())
check("TC-MONSRV-0041 deploy-monitor copies every server module (built from *.py: accounts.py, users.py …), "
      "every module server.py imports, and no test file (DEF-110)",
      set(mods) <= set(fl) and set(imported) <= set(fl) and "accounts.py" in fl
      and not any(x.startswith("test_") for x in fl) and {"requirements.txt", "Caddyfile"} <= set(fl), (fl, mods))

inv9 = accounts.invite("iphone", "viewer")
p9 = httpx.get(f"{BASE}/setup/{inv9['url'].rsplit('/', 1)[1]}").text
check("TC-MONSRV-0042 the setup page tells iPhone users the link opens Passwords: tap + for a new entry, then "
      "come back for the code", "opens <b>Passwords</b>" in p9 and "<b>+</b>" in p9 and "6-digit code" in p9)

# ── per-machine Telegram switch + Online window (FLEET_RELAY_PER_MACHINE_20261127) ──
(TMP / "tg.json").write_text('{"bot_token": "123:abc", "chat_id": "999"}')
relay._reset_for_tests()
drain()
store.set_relay("KEY-RAVI", False)
ingest("KEY-RAVI", "HW-RAVI", [ev(900, "CRITICAL", message="muted")])
off_q = drain()
store.set_relay("KEY-RAVI", True)
ingest("KEY-RAVI", "HW-RAVI", [ev(901, "CRITICAL", message="loud")])
on_q = drain()
check("TC-MONSRV-0043 a machine switched off sends nothing to Telegram (still stored for the portal); back on, it does",
      off_q == [] and len(on_q) == 1 and "loud" in on_q[0][2]
      and any(a["summary"] == "muted" for a in store.alerts("KEY-RAVI", False, now - 60)), (off_q, on_q))
vw = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
ad = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
auth.add_user("relayview", "relay-view-pass-1")
auth.add_user("relayadmin", "relay-admin-pass-1", "admin")
for cl, nm, pw in ((vw, "relayview", "relay-view-pass-1"), (ad, "relayadmin", "relay-admin-pass-1")):
    cl.post("/login", data={"username": nm, "password": pw,
                            "code": auth.totp_at(auth.get_user(nm)["totp_secret"], int(time.time() // 30))})
vcs = auth.session(vw.cookies.get(auth.COOKIE))["csrf"]
acs = auth.session(ad.cookies.get(auth.COOKIE))["csrf"]
r_v = vw.post("/devices/KEY-ANBU/relay", data={"csrf": vcs, "relay": "0"})
r_n = ad.post("/devices/KEY-ANBU/relay", data={"relay": "0"})
r_a = ad.post("/devices/KEY-ANBU/relay", data={"csrf": acs, "relay": "0"})
card = ad.get("/").text
vcard = vw.get("/").text
check("TC-MONSRV-0044 the Fleet-card switch: viewer 403, no CSRF 403, admin turns it off; every viewer sees "
      "'not sent to Telegram'; only admins get the button",
      r_v.status_code == 403 and r_n.status_code == 403 and r_a.status_code == 303
      and store.device("KEY-ANBU")["relay"] == 0 and "not sent to Telegram" in vcard
      and ">Send to Telegram<" in card and "Send to Telegram<" not in vcard and "Stop sending to" not in vcard, (r_v.status_code, r_n.status_code, r_a.status_code))
old_db = TMP / "old.db"
with sqlite3.connect(old_db) as c:
    c.execute("CREATE TABLE devices (key TEXT PRIMARY KEY, machine_id TEXT, label TEXT, tier TEXT, app_version TEXT,"
              " first_seen REAL, last_seen REAL, last_ip TEXT, epoch TEXT, enabled INTEGER NOT NULL DEFAULT 1,"
              " dropped INTEGER NOT NULL DEFAULT 0)")
    c.execute("INSERT INTO devices (key, label) VALUES ('K-OLD', 'Old')")
_keep = store.DB_PATH
store.DB_PATH = old_db
store.init_db()
with store.conn() as c:
    migrated = dict(c.execute("SELECT key, relay FROM devices").fetchone())
store.DB_PATH = _keep
check("TC-MONSRV-0045 an existing monitor.db gains the switch on start-up, every machine ON", migrated == {"key": "K-OLD", "relay": 1}, migrated)
from datetime import datetime as _dt  # noqa: E402
t_open = _dt(2026, 11, 27, 11, 0, tzinfo=store.IST).timestamp()      # a Friday, in market hours
t_pre = _dt(2026, 11, 27, 8, 30, tzinfo=store.IST).timestamp()       # before the open
dv = {"enabled": 1}
st = [pages.status(dict(dv, last_seen=t_pre - 240), {"market_open": 0}, t_pre)[1],
      pages.status(dict(dv, last_seen=t_pre - 500), {"market_open": 0}, t_pre)[1],
      pages.status(dict(dv, last_seen=t_open - 240), {"market_open": 1}, t_open)[1],
      pages.status(dict(dv, last_seen=t_open - 1200), {"market_open": 1}, t_open)[0]]
check("TC-MONSRV-0046 Online follows the machine's check-in cadence: market closed 4 min → Online (8 min → Late); "
      "market open 4 min → Late; 20 min in market hours → Offline (red) (TD-116)",
      st == ["Online", "Late", "Late", "bad"], st)

(TMP / "secrets" / "monitor.json").write_text('{"telegram_targets": [{"chat_id": "-100", "name": "Market Makers channel"}]}')
named = ad.get("/").text
(TMP / "secrets" / "monitor.json").unlink()
check("TC-MONSRV-0057 the switch names where it sends: 'sent to / not sent to Market Makers channel', button "
      "'Send to / Stop sending to Market Makers channel' (a portal switch, not the app's Telegram setting)",
      "not sent to Market Makers channel" in named and "sent to Market Makers channel" in named
      and "Send to Market Makers channel<" in named and "Stop sending to Market Makers channel<" in named
      and "Telegram alerts" not in named, named[:200])

chips = [pages.kite_chip(None, now), pages.kite_chip({"logged_in": True, "trade_ready": True, "checked_at": now}, now),
         pages.kite_chip({"logged_in": True, "trade_ready": False, "checked_at": now}, now),
         pages.kite_chip({"logged_in": False, "trade_ready": False, "checked_at": now}, now)]
ingest("KEY-RAVI", "HW-RAVI", snapshot=dict(snap, broker={"logged_in": False, "trade_ready": False, "checked_at": now}))
kc_page = ad.get("/").text
check("TC-MONSRV-0058 Kite status on the card: ✓ / trading off / NOT logged in (red), with the check time; nothing "
      "when the machine sent no fresh answer (older app, outside 08:30–15:30)",
      chips[0] == "" and "b-ok" in chips[1] and "Kite ✓" in chips[1] and "Kite: trading off" in chips[2]
      and "b-bad" in chips[3] and "NOT logged in" in chips[3] and "checked" in chips[1]
      and "Kite: NOT logged in" in kc_page, chips)

# ── Charges on the card, carried legs dated (FLEET_OPEN_CADENCE_20261127) ──
snap2 = dict(snap, totals={"net": -3404, "realised": -12542, "unrealised": 9581, "charges": 443,
                           "open_legs": 6, "closed_trades": 5})
y_entry = now - 86400 - 3600
snap2["legs"] = [{"id": "LIVE:carry", "strategy_id": "VET_V1", "symbol": "NIFTY26D0126000PE", "short": True, "qty": 65,
                  "entry": 198.75, "exit": 276.8, "entry_ts": y_entry, "exit_ts": now - 60, "open": False, "net": -5153,
                  "reason": "FLIP"}]
ingest("KEY-RAVI", "HW-RAVI", snapshot=snap2)
cardp = ad.get("/").text
check("TC-MONSRV-0047 the Fleet card shows Charges, so Realised + Unrealised − Charges visibly equals Net (TD-119)",
      "<small>Charges</small>" in cardp and "−₹443" in cardp and "−₹3,404" in cardp)
trp = ad.get(f"/trades?from={today}&to={today}&device=KEY-RAVI").text
yday = pages.datetime.fromtimestamp(y_entry, store.IST).strftime("%d %b")
check("TC-MONSRV-0048 a leg carried from yesterday shows its entry DATE in Trades (row dated by its exit)",
      f"{yday} " in trp and "NIFTY26D0126000PE" in trp, yday)

# ── watcher: offline / daily summary / reminders (FLEET_WATCH_20261127) ──
import json as _json      # noqa: E402
import watcher            # noqa: E402
from datetime import datetime as _wdt  # noqa: E402
_keep_db = store.DB_PATH
store.DB_PATH = TMP / "watch.db"
store.init_db()
watcher.init()


def T(h, m, day=27):
    return _wdt(2026, 11, day, h, m, tzinfo=store.IST).timestamp()


def dev(key, label, relay_on, last_seen):
    with store.conn() as c:
        c.execute("INSERT OR REPLACE INTO devices (key, label, tier, first_seen, last_seen, epoch, relay) "
                  "VALUES (?,?,?,?,?,?,?)", (key, label, "STANDARD", last_seen, last_seen, "E", 1 if relay_on else 0))


def snapw(key, ts, market_open, totals=None, rows=None):
    day = store.ist_day(ts)
    with store.conn() as c:
        c.execute("INSERT OR REPLACE INTO snapshot (device_key, ts, day, market_open, data) VALUES (?,?,?,?,?)",
                  (key, ts, day, 1 if market_open else 0, "{}"))
        if totals is not None:
            c.execute("INSERT OR REPLACE INTO day_summary (device_key, day, ts, totals, rows) VALUES (?,?,?,?,?)",
                      (key, day, ts, _json.dumps(totals), _json.dumps(rows or [])))


dev("WA", "Ravi", True, T(10, 59))
dev("WB", "Anbu - main", False, T(10, 59))
dev("WC", "Kumar", True, T(15, 44, day=26))
no_td = watcher.trading_day(T(11, 0))
snapw("WB", T(9, 16), True)           # only the switched-OFF machine says "open": still makes it a trading day
check("TC-MONSRV-0049 trading day = any machine's snapshot said the market is open (holiday-aware, no list); "
      "market hours 09:15–15:30 IST of such a day",
      no_td is False and watcher.trading_day(T(11, 0)) and not watcher.market_hours(T(9, 10))
      and watcher.market_hours(T(11, 0)) and not watcher.market_hours(T(15, 35)), no_td)
dev("WA", "Ravi", True, T(10, 56))
m1 = watcher.check_offline(T(11, 0))
m2 = watcher.check_offline(T(11, 1))
check("TC-MONSRV-0050 offline: a switched-ON machine silent > 3 min in market hours → ONE alert (a machine switched "
      "OFF never alerts); a machine that never started today counts, with its last-seen date",
      len(m1) == 2 and any("OFFLINE</b> · Ravi" in x and "last seen 10:56" in x for x in m1)
      and any("Kumar" in x and "26 Nov 15:44" in x for x in m1) and not any("Anbu" in x for x in m1) and m2 == [], m1)
dev("WA", "Ravi", True, T(11, 4))
b1 = watcher.check_offline(T(11, 5))
b2 = watcher.check_offline(T(11, 6))
check("TC-MONSRV-0051 back online: one '✅ BACK ONLINE' when it checks in again, then nothing",
      len(b1) == 1 and "BACK ONLINE</b> · Ravi" in b1[0] and b2 == [], (b1, b2))
dev("WA", "Ravi", True, T(15, 40))
check("TC-MONSRV-0052 no offline alerts outside market hours (after 15:30, before 09:15)",
      watcher.check_offline(T(16, 0)) == [] and watcher.check_offline(T(9, 0)) == [])
snapw("WA", T(15, 45), False, {"net": -3404, "realised": -12542, "charges": 443, "closed_trades": 5, "open_legs": 6},
      [{"id": "IC_V2", "codename": "Icarus", "state": "OPEN", "net": 1932},
       {"id": "TMA_V2", "codename": "Timberwolf", "state": "CLOSED", "net": -4939},
       {"id": "VET_V1", "codename": "Velvet", "state": "IDLE", "net": 0}])
snapw("WB", T(15, 45), False, {"net": 999, "realised": 999, "closed_trades": 1})
early = watcher.daily_summary(T(15, 49))
summ = watcher.daily_summary(T(15, 50))
again = watcher.daily_summary(T(15, 55))
check("TC-MONSRV-0053 daily summary: none before 15:50; ONE at 15:50 with each switched-ON machine's net, realised, "
      "charges, trades, carried legs and strategies, and the fleet net; switched-OFF machines left out",
      early is None and summ and "FLEET SUMMARY" in summ and "<b>Ravi</b>: −₹3,404" in summ and "charges −₹443" in summ
      and "5 closed" in summ and "6 leg(s) carried" in summ and "Icarus: +₹1,932" in summ and "Timberwolf: −₹4,939" in summ
      and "Velvet" not in summ and "Anbu" not in summ and "Fleet net: −₹3,404" in summ and again is None, summ)
check("TC-MONSRV-0054 no summary on a day no machine reported the market open (holiday / weekend)",
      watcher.daily_summary(T(15, 50, day=28)) is None)
with store.conn() as c:
    for i, (k, kind, ts) in enumerate((("WA", "CRITICAL", T(11, 0)), ("WA", "CRITICAL", T(11, 1)),
                                       ("WB", "CRITICAL", T(11, 0)), ("WA", "SYSTEM_ALERT", T(11, 0)),
                                       ("WA", "ORDER_REJECTED", T(8, 0)))):
        c.execute("INSERT INTO events (device_key, epoch, seq, ts, received_at, kind, summary, data) VALUES (?,?,?,?,?,?,?,?)",
                  (k, "E", 100 + i, ts, ts, kind, f"msg{i}", "{}"))
    acked_id = c.execute("SELECT id FROM events WHERE summary='msg1'").fetchone()[0]
store.ack(acked_id, "anbu", "on it")
r1 = watcher.reminders(T(11, 12))
r2 = watcher.reminders(T(11, 20))
check("TC-MONSRV-0055 reminder: a critical unacknowledged after 10 min is posted ONCE with the Alerts link; not when "
      "acknowledged, not for a switched-OFF machine, not for system alerts, not when older than 2 h",
      len(r1) == 1 and "NOT ACKNOWLEDGED</b> for 12 min · Ravi" in r1[0] and "msg0" in r1[0]
      and "/alerts?unacked=1" in r1[0] and r2 == [], (r1, r2))
relay._reset_for_tests()
drain()
dev("WA", "Ravi", True, T(13, 0))
store_n = watcher.tick(T(13, 10))
q = drain()
srv_src = (HERE / "server.py").read_text()
check("TC-MONSRV-0056 tick() sends the notices to the Telegram targets; the monitor starts the watcher at start-up",
      store_n == len(q) >= 1 and all(x[1] == "999" for x in q) and "watcher.start()" in srv_src, (store_n, q))
store.DB_PATH = _keep_db

cf2 = (HERE / "Caddyfile").read_text()
lic_block = cf2[cf2.index("139-59-8-202.sslip.io:8443 {"):]
dep2 = (HERE / "deploy-monitor.command").read_text()
check("TC-MONSRV-0059 Caddy serves the LICENCE server over HTTPS on :8443 (same name and certificate, its own "
      "origin) → 127.0.0.1:9100; the deploy opens 8443 and checks /health there (TD-111)",
      "reverse_proxy 127.0.0.1:9100" in lic_block and "reverse_proxy 127.0.0.1:9200" not in lic_block
      and "ufw allow 8443/tcp" in dep2 and ':8443/health' in dep2
      and cf2.index("respond /internal/* 404") < cf2.index("reverse_proxy 127.0.0.1:9200"), lic_block[:120])

# ── strategy on alerts (ALERT_STRATEGY_INFER_20261127) ──
import strategies   # noqa: E402
inf = [strategies.infer("TMA_V2: SL GTT on NIFTY26D0126000PE triggered"),
       strategies.infer("Velvet: no NIFTY tick for 30s — forcing a WebSocket reconnect"),
       strategies.infer("Kite logged in at 08:46 — session ready for the open."),
       strategies.infer("the circuit Breaker tripped"), strategies.infer("Exit order for IC_V2 not confirmed")]
check("TC-MONSRV-0060 strategy read from an alert's text: an id anywhere, a codename that starts it; nothing for a "
      "plain sentence or a codename-like word mid-text",
      inf == [("TMA_V2", "Timberwolf"), ("VET_V1", "Velvet"), (None, None), (None, None), ("IC_V2", "Icarus")], inf)
ingest("KEY-RAVI", "HW-RAVI", [ev(5000, "CRITICAL", message="TMA_V2: SL GTT triggered; buy-back not filled", severity="error"),
                               ev(5001, "CRITICAL", strategy_id="IC_V2", message="named by the app"),
                               ev(5002, "SYSTEM_ALERT", message="Kite logged in at 08:46", severity="info")])
with store.conn() as c:
    got = {r["seq"]: (r["strategy_id"], r["codename"]) for r in c.execute(
        "SELECT seq, strategy_id, codename FROM events WHERE device_key='KEY-RAVI' AND seq IN (5000,5001,5002)")}
check("TC-MONSRV-0061 on arrival: an alert without a strategy gets the one its text names; one the app named gets "
      "its codename; a general system alert stays without one",
      got == {5000: ("TMA_V2", "Timberwolf"), 5001: ("IC_V2", "Icarus"), 5002: (None, None)}, got)
with store.conn() as c:
    c.execute("INSERT INTO events (device_key, epoch, seq, ts, received_at, kind, summary, data) VALUES (?,?,?,?,?,?,?,?)",
              ("KEY-RAVI", "OLD", 1, now, now, "CRITICAL", "Velvet: old one", '{"message": "Velvet: old one"}'))
n_bf = store.backfill_strategies()
n_bf2 = store.backfill_strategies()
with store.conn() as c:
    old_row = tuple(c.execute("SELECT strategy_id, codename FROM events WHERE epoch='OLD'").fetchone())
check("TC-MONSRV-0062 start-up backfill names the strategy on alerts stored before this change; a second run changes nothing",
      n_bf >= 1 and n_bf2 == 0 and old_row == ("VET_V1", "Velvet") and "store.backfill_strategies()" in (HERE / "server.py").read_text(),
      (n_bf, n_bf2, old_row))
al_all = ad.get("/alerts?days=1").text
al_tw = ad.get("/alerts?days=1&strategy=Timberwolf").text
check("TC-MONSRV-0063 Alerts: the Strategy column shows the codename; the Strategy filter lists the codenames seen and "
      "narrows the list",
      "<td>Timberwolf</td>" in al_all and '<option value="Timberwolf">' in al_all and "Icarus" in al_all
      and "<td>Timberwolf</td>" in al_tw and "named by the app" not in al_tw, al_tw[:200])
sys.path.insert(0, str(HERE.parent / "backend"))
from app.config.strategy_display import STRATEGY_DISPLAY   # noqa: E402
check("TC-MONSRV-0064 the portal's codename table matches the app's STRATEGY_DISPLAY (drift guard)",
      strategies.CODENAMES == {k: v[1] for k, v in STRATEGY_DISPLAY.items()})

# ── fleet membership: approval, remove, add back (FLEET_MEMBERSHIP_20261127) ──
mig_db = TMP / "old_mem.db"
with sqlite3.connect(mig_db) as c:
    c.execute("CREATE TABLE devices (key TEXT PRIMARY KEY, machine_id TEXT, label TEXT, tier TEXT, app_version TEXT,"
              " first_seen REAL, last_seen REAL, last_ip TEXT, epoch TEXT, enabled INTEGER NOT NULL DEFAULT 1,"
              " dropped INTEGER NOT NULL DEFAULT 0, relay INTEGER NOT NULL DEFAULT 1)")
    c.execute("INSERT INTO devices (key, label) VALUES ('K-WAS', 'Was reporting')")
_k = store.DB_PATH
store.DB_PATH = mig_db
store.init_db()
with store.conn() as c:
    mig = dict(c.execute("SELECT key, membership FROM devices").fetchone())
store.DB_PATH = _k
check("TC-MONSRV-0065 machines already reporting stay in the fleet when this change starts (membership 'in')",
      mig == {"key": "K-WAS", "membership": "in"}, mig)

with sqlite3.connect(TMP / "licenses.db") as c:
    c.execute("INSERT OR REPLACE INTO licenses VALUES ('KEY-NEW', 'Newbie', 'STANDARD', 'HW-NEW', 0)")
store.NEW_MACHINES = "approval"
relay._reset_for_tests()
drain()
r_new = ingest("KEY-NEW", "HW-NEW", [ev(1, "CRITICAL", message="newbie boom"), ev(2, "TRADE_ENTRY", symbol="X")],
               snapshot=dict(snap, totals={"net": 777}))
with store.conn() as c:
    kept = c.execute("SELECT COUNT(*) FROM events WHERE device_key='KEY-NEW'").fetchone()[0] + \
        c.execute("SELECT COUNT(*) FROM snapshot WHERE device_key='KEY-NEW'").fetchone()[0]
check("TC-MONSRV-0066 a NEW install waits for approval: told to hold off (enabled false, its events acked), nothing "
      "it sent is kept, nothing goes to Telegram",
      r_new.json() == {"ok": True, "acked": 2, "enabled": False} and store.device("KEY-NEW")["membership"] == "pending"
      and kept == 0 and drain() == [], (r_new.json(), kept))

vw_page, ad_page = vw.get("/").text, ad.get("/").text
pages_v = [vw.get(u).text for u in ("/alerts?days=1", f"/trades?from={today}&to={today}", f"/timeline?day={today}", "/day")]
check("TC-MONSRV-0067 a waiting machine is on no viewer page (Fleet, Alerts, Trades, Timeline, Day); admins see it "
      "under 'Waiting for approval' with Add to fleet / Don't add",
      "Newbie" not in vw_page and "Waiting for approval" not in vw_page and all("Newbie" not in t for t in pages_v)
      and "Waiting for approval" in ad_page and "Newbie" in ad_page and "Add to fleet" in ad_page
      and "Don&#x27;t add" in ad_page, ad_page[:300])

vcs2 = auth.session(vw.cookies.get(auth.COOKIE))["csrf"]
acs2 = auth.session(ad.cookies.get(auth.COOKIE))["csrf"]
deny = vw.post("/devices/KEY-NEW/membership", data={"csrf": vcs2, "membership": "in"}).status_code
nocsrf = ad.post("/devices/KEY-NEW/membership", data={"membership": "in"}).status_code
okay = ad.post("/devices/KEY-NEW/membership", data={"csrf": acs2, "membership": "in"}).status_code
r_after = ingest("KEY-NEW", "HW-NEW", [ev(3, "CRITICAL", message="now counted")], snapshot=dict(snap, totals={"net": 777}))
check("TC-MONSRV-0068 Add to fleet (admin only, CSRF): from its next report the machine is stored and shown",
      deny == 403 and nocsrf == 403 and okay == 303 and store.device("KEY-NEW")["membership"] == "in"
      and r_after.json()["enabled"] is True and "Newbie" in vw.get("/").text
      and any(a["summary"] == "now counted" for a in store.alerts("KEY-NEW", False, now - 600)), (deny, nocsrf, okay, r_after.json()))

ad.post("/devices/KEY-RAVI/membership", data={"csrf": acs2, "membership": "out"})
hidden_v = [vw.get(u).text for u in ("/", "/alerts?days=1", f"/trades?from={today}&to={today}", f"/timeline?day={today}", "/day")]
r_out = ingest("KEY-RAVI", "HW-RAVI", [ev(9000, "CRITICAL", message="from a removed machine")])
ad_out = ad.get("/").text
check("TC-MONSRV-0069 Remove from fleet: the machine and its earlier data vanish from every page for everyone; it is "
      "told to hold off and nothing new is kept; admins see it under 'Not in the fleet' with Add back",
      all("Ravi &lt;b&gt;" not in t for t in hidden_v) and r_out.json()["enabled"] is False
      and not any(a["summary"] == "from a removed machine" for a in store.alerts(None, False, now - 600))
      and "Not in the fleet" in ad_out and "Add back" in ad_out and "Ravi &lt;b&gt;" in ad_out,
      [("Ravi" in t) for t in hidden_v])
ad.post("/devices/KEY-RAVI/membership", data={"csrf": acs2, "membership": "in"})
back = vw.get("/alerts?days=1").text
check("TC-MONSRV-0070 Add back restores the machine with its earlier data; the channel watcher never counts a "
      "machine that is waiting or removed",
      "Ravi &lt;b&gt;" in vw.get("/").text and "Ravi &lt;b&gt;" in back
      and not watcher._on({"membership": "pending"}) and not watcher._on({"membership": "out"})
      and watcher._on({"membership": "in"}))
store.NEW_MACHINES = "auto"

uv.should_exit = True
print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}  ({OK[0]}/{OK[0] + len(FAILS)})")
sys.exit(1 if FAILS else 0)
