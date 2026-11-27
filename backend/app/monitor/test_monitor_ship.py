# backend/app/monitor/test_monitor_ship.py — ── FLEET_MONITOR_20261127 ── (TD-110) TC-MON-0001…0027
#
# The app half of the Fleet Monitor: the outbox, the LIVE-only taps in
# api/telegram_api.py, the snapshot, the shipper's cadence / ack / back-off,
# and the TLS transport (bare IP + SNI name, verified). Hermetic: a temp
# outbox, stubbed Telegram fan-out, a fake poster, and a local HTTPS server
# with a throw-away CA for the transport check. No network.
#
#   cd backend && python3 app/monitor/test_monitor_ship.py
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))          # .../backend
os.environ.setdefault("SCALP_TEST_MODE", "1")
TMP = Path(tempfile.mkdtemp(prefix="mon_ship_"))
os.environ["SCALP_MONITOR_OUTBOX"] = str(TMP / "outbox.db")

from app.monitor import outbox, shipper, snapshot, tap   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  [{str(detail)[:300]}]"))
    if not cond:
        FAILS.append(name)


def fresh(name):
    os.environ["SCALP_MONITOR_OUTBOX"] = str(TMP / f"{name}.db")
    outbox._reset_for_tests()


def kinds():
    return [r["kind"] for r in outbox.pending(1000)]


# ── outbox ───────────────────────────────────────────────────────────
fresh("o1")
a = outbox.enqueue("TRADE_ENTRY", {"x": 1})
b = outbox.enqueue("CRITICAL", {"x": 2})
p = outbox.pending(10)
check("TC-MON-0001 outbox keeps events in order with rising seq",
      a and b and b > a and [r["seq"] for r in p] == [a, b] and p[1]["data"] == {"x": 2}, p)
outbox.mark_sent(a)
check("TC-MON-0002 mark_sent(seq) clears only rows up to that seq", [r["seq"] for r in outbox.pending(10)] == [b])
ep = outbox.epoch()
check("TC-MON-0003 the outbox epoch is stable for a file and new for a new file",
      ep == outbox.epoch() and len(ep) == 32 and (fresh("o1b") or outbox.epoch() != ep))

os.environ["SCALP_MONITOR_OUTBOX"] = "/dev/null/cannot/exist.db"
outbox._reset_for_tests()
check("TC-MON-0004 enqueue never raises: an unwritable outbox returns None", outbox.enqueue("X", {}) is None)

fresh("o2")
old_max = outbox.MAX_UNSENT
outbox.MAX_UNSENT = 2
for i in range(5):
    outbox.enqueue("TRADE_ENTRY", {"i": i})
s1 = outbox.enqueue("TRADE_ENTRY", {"i": 5})
outbox.mark_sent(s1 - 3)                       # i0..i2 sent
outbox.prune(now=10 ** 10)                     # far future: every sent row is past 7 days
left = [r["data"]["i"] for r in outbox.pending(10)]
check("TC-MON-0005 prune drops sent rows past 7 days and caps unsent rows, oldest first, counting the drop",
      left == [4, 5] and outbox.dropped_total() == 1, (left, outbox.dropped_total()))
outbox.MAX_UNSENT = old_max

fresh("o3")
big = outbox.enqueue("CRITICAL", {"message": "x" * 50000})
check("TC-MON-0006 an oversized payload is truncated, not lost",
      big and outbox.pending(1)[0]["data"].get("truncated") is True)

# ── taps: LIVE only, allowlisted ─────────────────────────────────────
fresh("t1")
tap.trade("TRADE_ENTRY", {"strategy_id": "IC_V2", "symbol": "NIFTY", "mode": "paper", "entry_price": 1})
tap.trade("TRADE_ENTRY", {"strategy_id": "IC_V2", "symbol": "NIFTY", "mode": "live", "entry_price": 100.5,
                          "quantity": 75, "access_token": "SECRET", "buttons": [{"url": "https://x"}]})
tap.trade("TRADE_EXIT", {"strategy_id": "TMA_V2", "symbol": "BANKNIFTY", "exit_price": 9, "pnl": -12})
rows = outbox.pending(10)
check("TC-MON-0007 a paper trade event never leaves the machine; live and unmarked (live) ones do",
      [r["kind"] for r in rows] == ["TRADE_ENTRY", "TRADE_EXIT"], rows)
blob = json.dumps(rows)
check("TC-MON-0008 trade payloads are allowlisted: no token, button or URL is shipped",
      "SECRET" not in blob and "https://x" not in blob and rows[0]["data"]["qty"] == 75
      and rows[0]["data"]["entry_price"] == 100.5, blob)

fresh("t2")
tap.alert("CRITICAL", {"message": "paper boom"}, mode="PAPER")
tap.alert("CRITICAL", {"message": "live boom", "buttons": [{"text": "Log in", "url": "https://kite"}]}, mode="LIVE")
tap.alert("CRITICAL", {"message": "off but holding"}, mode="OFF")
tap.alert("SYSTEM_ALERT", {"message": "relay down", "title": "RELAY", "severity": "warning"})
tap.alert("ORDER_REJECTED", {"status_message": "margin", "symbol": "NIFTY", "order_id": "123"}, mode="LIVE")
rows = outbox.pending(10)
check("TC-MON-0009 a PAPER critical stays home; LIVE / OFF criticals, system alerts and rejections ship",
      [r["data"].get("message") for r in rows] == ["live boom", "off but holding", "relay down", "margin"], rows)
check("TC-MON-0010 alert payloads carry no login URL or button", "https://kite" not in json.dumps(rows))

fresh("t3")
tap.group("GROUP_EXIT", {"strategy_id": "IC_V2", "mode": "live", "reason": "SL",
                         "legs": [{"symbol": f"S{i}", "action": "SELL", "qty": 75, "entry_price": 10, "secret": "x"}
                                  for i in range(20)]}, pnl=-1500)
tap.group("GROUP_ENTRY", {"strategy_id": "TSG_V1", "mode": "paper", "legs": [{"symbol": "A"}]})
rows = outbox.pending(10)
check("TC-MON-0011 basket events: live only, legs allowlisted and capped at 12, basket P&L kept",
      len(rows) == 1 and len(rows[0]["data"]["legs"]) == 12 and rows[0]["data"]["pnl"] == -1500
      and "secret" not in json.dumps(rows), rows)

# ── telegram_api calls the taps ──────────────────────────────────────
import app.api.telegram_api as T   # noqa: E402
T._fanout = lambda *a, **k: 0
T.send_telegram_message = lambda *a, **k: False
fresh("t4")
T.notify_trade_entry({"strategy_id": "IC_V2", "symbol": "NIFTY", "side": "SELL", "mode": "live",
                      "entry_price": 101, "quantity": 75})
T.notify_trade_entry({"strategy_id": "IC_V2", "symbol": "NIFTY", "mode": "paper", "entry_price": 1})
T.notify_tp_exit({"strategy_id": "TMA_V2", "symbol": "X", "mode": "live", "pnl": 500, "exit_price": 2})
T.notify_sl_exit({"strategy_id": "TMA_V2", "symbol": "X", "mode": "live", "pnl": -300, "exit_price": 1})
T.notify_manual_exit({"strategy_id": "IC_V2", "symbol": "Y", "mode": "live", "pnl": 50, "reason": "EOD"})
T.notify_critical({"message": "unprotected", "severity": "error", "mode": "LIVE", "strategy_id": "IC_V2"})
T.notify_critical({"message": "paper only", "severity": "error", "mode": "PAPER", "strategy_id": "IC_V2"})
T.notify_order_rejection({"symbol": "NIFTY", "status_message": "RMS", "order_id": "9"})
T.notify_system_alert({"message": "Kite not logged in", "severity": "warning", "title": "KITE LOGIN",
                       "buttons": [{"text": "Log in", "url": "https://kite.zerodha.com/connect/login?api_key=k"}]})
rows = outbox.pending(20)
check("TC-MON-0012 every notify_* reaches the outbox (live only): entry, TP, SL, manual exit, critical, rejection, system",
      [r["kind"] for r in rows] == ["TRADE_ENTRY", "TRADE_EXIT", "TRADE_EXIT", "TRADE_EXIT", "CRITICAL",
                                    "ORDER_REJECTED", "SYSTEM_ALERT"], [r["kind"] for r in rows])
check("TC-MON-0013 exit reasons travel: TP, SL and the manual exit's own reason",
      [r["data"].get("reason") for r in rows[1:4]] == ["TP", "SL", "EOD"], [r["data"] for r in rows[1:4]])
check("TC-MON-0014 the Kite login URL (api_key) in a system alert is never shipped",
      "api_key" not in json.dumps(rows))

fresh("t5")
T.notify_group_exit({"strategy_id": "IC_V2", "mode": "live", "reason": "TP", "expiry": "2026-12-02",
                     "legs": [{"symbol": "NIFTY26D0224000CE", "action": "SELL", "qty": 75,
                               "entry_price": 20, "exit_price": 10}]})
rows = outbox.pending(5)
check("TC-MON-0015 notify_group_exit ships the basket with the P&L it computed",
      len(rows) == 1 and rows[0]["kind"] == "GROUP_EXIT" and rows[0]["data"]["pnl"] == 750.0, rows)

orig_tap = tap.trade
tap.trade = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    T.notify_trade_entry({"strategy_id": "IC_V2", "symbol": "N", "mode": "live", "entry_price": 1})
    ok = True
except Exception as e:      # noqa: BLE001
    ok = repr(e)
tap.trade = orig_tap
check("TC-MON-0016 a tap that raises never breaks the notify call", ok is True, ok)

# ── snapshot ─────────────────────────────────────────────────────────
import app.api.fleet_today_routes as F   # noqa: E402
import app.api.closed_recent_routes as CR   # noqa: E402
_real = (F.get_fleet_today, F._legs_for, CR._day_start)
DAY0 = 1_800_000_000
F.get_fleet_today = lambda: {"ok": True, "day": "2027-01-15", "session": {"market_open": True},
                             "totals": {"LIVE": {"net": 1200, "open_legs": 2}, "PAPER": {"net": 99}},
                             "strategies": [{"id": "IC_V2", "book": "LIVE", "state": "OPEN", "net": 1200,
                                             "last_event": {"ts": 1, "kind": "entry", "text": "t"}},
                                            {"id": "IC_V2", "book": "PAPER", "state": "OPEN", "net": 99}]}
F._legs_for = lambda sid, w: [
    {"id": "LIVE:1", "book": "LIVE", "open": True, "entry_ts": DAY0 - 86400, "symbol": "A"},
    {"id": "LIVE:2", "book": "LIVE", "open": False, "entry_ts": DAY0 + 100, "exit_ts": DAY0 + 200, "symbol": "B"},
    {"id": "LIVE:3", "book": "LIVE", "open": False, "entry_ts": DAY0 - 86400, "exit_ts": DAY0 - 80000, "symbol": "C"},
    {"id": "PAPER:4", "book": "PAPER", "open": True, "entry_ts": DAY0 + 1, "symbol": "D"}]
CR._day_start = lambda now: DAY0
snap = snapshot.build()
check("TC-MON-0017 snapshot: LIVE book only (rows, totals); legs = open or touched today; paper never included",
      [r["id"] for r in snap["rows"]] == ["IC_V2"] and snap["totals"]["net"] == 1200
      and sorted(x["id"] for x in snap["legs"]) == ["LIVE:1", "LIVE:2"] and snap["market_open"] is True
      and "99" not in json.dumps(snap["totals"]), snap)
F.get_fleet_today = lambda: (_ for _ in ()).throw(RuntimeError("db gone"))
check("TC-MON-0018 a snapshot that fails reports {'error'} instead of raising", "error" in snapshot.build())
F.get_fleet_today, F._legs_for, CR._day_start = _real

# ── shipper ──────────────────────────────────────────────────────────
fresh("s1")
clock = [1000.0]
sent = []


class R:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


replies = []


def poster(body):
    sent.append(body)
    r = replies.pop(0) if replies else ("ok",)
    if r[0] == "raise":
        raise OSError("down")
    if r[0] == "code":
        return R(r[1], {})
    if r[0] == "paused":
        return R(200, {"ok": True, "acked": max([e["seq"] for e in body["events"]] or [0]) or None, "enabled": False})
    return R(200, {"ok": True, "acked": max([e["seq"] for e in body["events"]] or [0]) or None, "enabled": True})


ident = {"key": "K", "machine_id": "M", "app_version": "12.0.22"}
sh = shipper.Shipper(clock=lambda: clock[0], poster=poster, snap=lambda: {"market_open": True, "totals": {}},
                     ident=lambda: dict(ident))
check("TC-MON-0019 no license key: the shipper idles and sends nothing",
      shipper.Shipper(clock=lambda: 0, poster=poster, ident=lambda: None).step() == 60.0 and not sent)
outbox.enqueue("TRADE_ENTRY", {"a": 1})
outbox.enqueue("CRITICAL", {"a": 2})
w = sh.step()
check("TC-MON-0020 first step sends the queued events + a snapshot; the acked rows are marked sent",
      len(sent) == 1 and len(sent[0]["events"]) == 2 and "snapshot" in sent[0] and sent[0]["epoch"]
      and outbox.unsent_count() == 0 and w == shipper.TICK_S, (sent, w))
clock[0] += 5
check("TC-MON-0021 nothing queued and no snapshot due: no POST at all", sh.step() == shipper.TICK_S and len(sent) == 1)
clock[0] += 56
sh.step()
check("TC-MON-0022 market open: the snapshot (heartbeat) goes every 60 s", len(sent) == 2 and "snapshot" in sent[1])
outbox.enqueue("CRITICAL", {"a": 3})
replies[:] = [("raise",), ("raise",), ("code", 500), ("code", 401)]
w1 = sh.step()
w2 = sh.step()
w3 = sh.step()
check("TC-MON-0023 unreachable / 5xx: events kept, back-off doubles; refused key waits 30 min",
      (w1, w2, w3) == (10.0, 20.0, 40.0) and outbox.unsent_count() == 1 and sh.step() == shipper.REFUSED_WAIT_S,
      (w1, w2, w3))
replies[:] = [("paused",)]
w = sh.step()
check("TC-MON-0024 the server can pause an install: it waits 10 min, events stay acked as sent",
      w == shipper.PAUSED_WAIT_S and outbox.unsent_count() == 0, w)

# ── transport: bare IP + SNI name, certificate verified ──────────────
ossl = shutil.which("openssl")
if not ossl:
    print("  SKIP  TC-MON-0025 (no openssl on this machine)")
else:
    d = TMP / "tls"
    d.mkdir()

    def run(*a):
        subprocess.run([ossl, *a], cwd=d, check=True, capture_output=True)
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
        "-subj", "/CN=Test CA")
    (d / "ext.cnf").write_text("subjectAltName=DNS:monitor.test\nbasicConstraints=CA:FALSE\n")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=monitor.test")
    run("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "srv.pem",
        "-days", "2", "-extfile", "ext.cnf")
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            seen["host"] = self.headers.get("host")
            seen["sni"] = getattr(self.connection, "server_hostname", None) or seen.get("sni_cb")
            seen["body"] = json.loads(self.rfile.read(n))
            out = json.dumps({"ok": True, "acked": None, "enabled": True}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "srv.pem", d / "srv.key")
    ctx.sni_callback = lambda sock, name, c: seen.__setitem__("sni_cb", name)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    os.environ.update(SCALP_MONITOR_URL=f"https://127.0.0.1:{port}", SCALP_MONITOR_SNI="monitor.test",
                      SCALP_MONITOR_CA=str(d / "ca.pem"))
    r = shipper.post({"k": 1})
    good = r.status_code == 200 and seen.get("sni_cb") == "monitor.test" and seen.get("host") == "monitor.test"
    os.environ["SCALP_MONITOR_SNI"] = "evil.test"
    try:
        shipper.post({"k": 2})
        refused = False
    except Exception as e:      # noqa: BLE001
        refused = "certificate" in repr(e).lower() or "ssl" in repr(e).lower() or "hostname" in repr(e).lower()
    srv.shutdown()
    check("TC-MON-0025 transport: HTTPS to the bare IP with the name as SNI + Host, verified; a wrong name is refused",
          good and refused, (seen, refused))
    for k in ("SCALP_MONITOR_URL", "SCALP_MONITOR_SNI", "SCALP_MONITOR_CA"):
        os.environ.pop(k, None)

url, host, verify = shipper.endpoint()
check("TC-MON-0026 default endpoint: https://139.59.8.202 with SNI 139-59-8-202.sslip.io and the public CA bundle",
      url == "https://139.59.8.202/api/v1/ingest" and host == "139-59-8-202.sslip.io" and verify is True,
      (url, host, verify))

src = (HERE.parents[1] / "api_server.py").read_text(encoding="utf-8")
check("TC-MON-0027 api_server starts the ship loop under _supervise, next to the liveness ping",
      "monitor_ship_loop()" in src and '"fleet monitor"' in src)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}  ({27 - len(FAILS)}/27)")
sys.exit(1 if FAILS else 0)
