# backend/app/execution/test_zerodha_relay_contract.py
# ── ZERODHA_RELAY_CONTRACT_20261126 ── (TD-075)
#
# The REAL ZerodhaOrderExecutor and the REAL RelayClient against stub relays:
# small HTTP servers on 127.0.0.1 that record every request and answer as told.
# Before this suite the other executor tests used fake relay objects, so the
# HTTP request each live call sends, and how every kind of answer is read,
# were only ever tested live. No real Kite, no network beyond loopback.
#
#   TC-EXEC-0277  relay_config.json: missing / off / old single-relay form / list / bad
#   TC-EXEC-0278  the wire: path, method, body, auth header, query for every relay call
#   TC-EXEC-0279  relay answers: broker rejection, HTTP errors, bad JSON, timeout
#   TC-EXEC-0280  place_limit_buy                       TC-EXEC-0281  modify_order
#   TC-EXEC-0282  place_gtt_tp_only_long (Icarus)       TC-EXEC-0283  place_gtt_sl_only_long / _short
#   TC-EXEC-0284  place_gtt_oco LONG / SHORT            TC-EXEC-0285  get_basket_margin
#   TC-EXEC-0286  get_order_fill / get_last_avg_price   TC-EXEC-0287  relay routing and fallback
#   TC-EXEC-0288  a late order / GTT is settled         TC-EXEC-0289  'could it have been sent?'
#   TC-EXEC-0290  broker reads: positions, GTT list / status, session refresh
#   TC-EXEC-0291  every GTT builder: exact refusals, lot check, last price, direct path, answer shapes
#   TC-EXEC-0292  an unclear failure: looked up first, then watched (relay and direct)
#
# Run: python3 test_zerodha_relay_contract.py
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import types
import urllib.parse
from pathlib import Path

import pytest

os.environ["SCALP_TEST_MODE"] = "1"       # a hand run too: no instance-guard poller, no real state dir (DEF-105)
# the stub relays are on loopback: never through a proxy (the mutation sandbox points
# HTTP(S)_PROXY at a dead port to keep everything else offline)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests                                                       # noqa: E402
import app.event_bus.audit_logger as AL                               # noqa: E402

AL.write_audit_log = lambda m: None
import app.execution.zerodha_executor as ZE                           # noqa: E402

LOG = []
ZE.write_audit_log = LOG.append
ZE._LOT_CSV_DONE.set()                    # no lot-size warm-up thread reading HOME
PAGES = []
sys.modules.setdefault("app.api.telegram_api", types.ModuleType("app.api.telegram_api"))
sys.modules["app.api.telegram_api"].notify_critical = PAGES.append
import app.slippage.slip_store as SLIP                                # noqa: E402

SENDS = []
SLIP.record_send = lambda *a: SENDS.append(a)
SYM = "NIFTY26D0225000CE"


# ════════════════════════════════════════════════════════════════════════════
# stub relay: records each request, answers from a script or a default
# ════════════════════════════════════════════════════════════════════════════
class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):                    # no reverse-DNS lookup of 127.0.0.1 (getfqdn: seconds)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class StubRelay:
    def __init__(self):
        self.reqs, self.script = [], []
        self.release = threading.Event()
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                u = urllib.parse.urlsplit(self.path)
                body = json.loads(raw) if raw else None
                stub.reqs.append({"method": self.command, "path": u.path,
                                  "query": dict(urllib.parse.parse_qsl(u.query)),
                                  "auth": self.headers.get("Authorization"), "body": body})
                status, text = stub.script.pop(0) if stub.script else stub.default(u.path, body)
                if status == "hang":
                    stub.release.wait(5)
                    status, text = 200, '{"order_id": "LATE"}'
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            do_POST = do_DELETE = _go

        self.srv = _Server(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @staticmethod
    def default(path, body):
        if path == "/relay/place_order":
            return 200, json.dumps({"order_id": 4711})
        if path == "/relay/place_gtt":
            return 200, json.dumps({"trigger_id": 901})
        if path == "/relay/modify_order":
            return 200, json.dumps({"order_id": (body or {}).get("order_id")})
        return 200, json.dumps({"ok": True})

    def reset(self):
        self.reqs.clear()
        self.script.clear()
        self.release.set()
        self.release = threading.Event()


A, B = StubRelay(), StubRelay()
DEAD = "http://127.0.0.1:9"               # nothing listens: connection refused, never sent
_TMP = Path(tempfile.mkdtemp(prefix="relay_contract_"))


def set_cfg(cfg):
    """Write relay_config.json (None = no file) and drop the module's cache."""
    p = _TMP / "relay_config.json"
    if p.exists():
        p.unlink()
    if cfg is not None:
        p.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg))
    ZE._RELAY_CONFIG_PATH = p
    ZE._relay_cfg = None


def relays(*entries, **extra):
    rel = [{"url": u, "host": h, "secret": "sek", "is_primary": i == 0}
           for i, (u, h) in enumerate(entries)]
    set_cfg(dict({"enabled": True, "relays": rel}, **extra))


# ════════════════════════════════════════════════════════════════════════════
# fake Kite (the direct path and the reads)
# ════════════════════════════════════════════════════════════════════════════
class NetworkException(Exception):
    pass


class Kite:
    VARIETY_REGULAR, EXCHANGE_NFO, ORDER_TYPE_LIMIT = "regular", "NFO", "LIMIT"
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL, PRODUCT_NRML = "BUY", "SELL", "NRML"
    GTT_TYPE_OCO, GTT_TYPE_SINGLE = "two-leg", "single"
    api_key, access_token = "KEY", "TOK"

    def __init__(self):
        self.calls, self.book, self.gtts = [], [], []
        self.raise_on = {}
        self.margin_res, self.margins_res = {}, {}
        self.positions_res = {"net": []}

    def _rec(self, name, *a, **k):
        self.calls.append((name, a, k))
        if name in self.raise_on:
            raise self.raise_on[name]

    def place_order(self, **k):
        self._rec("place_order", **k)
        return "D1"

    def place_gtt(self, **k):
        self._rec("place_gtt", **k)
        return {"trigger_id": 77}

    def modify_order(self, **k):
        self._rec("modify_order", **k)
        return k.get("order_id")

    def cancel_order(self, **k):
        self._rec("cancel_order", **k)

    def delete_gtt(self, gid):
        self._rec("delete_gtt", gid)

    def orders(self):
        self._rec("orders")
        return [dict(o) for o in self.book]

    def get_gtts(self):
        self._rec("get_gtts")
        return [dict(g) for g in self.gtts]

    def basket_order_margins(self, params, consider_positions=False):
        self._rec("basket_order_margins", params, consider_positions=consider_positions)
        return self.margin_res

    def margins(self, segment):
        self._rec("margins", segment)
        return self.margins_res

    def positions(self):
        self._rec("positions")
        return self.positions_res


K = None
SLEPT = []
BG = []
LOTS = []


class _ExecutorTime:
    """The executor module's `time`: real clock, recorded sleeps (nothing else in the
    process — the stub servers, the mutation runner — loses its own time.sleep)."""
    sleep = staticmethod(SLEPT.append)

    def __getattr__(self, name):
        import time as _t
        return getattr(_t, name)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    global K
    K = Kite()
    for s in (A, B):
        s.reset()
    LOG.clear(); PAGES.clear(); SENDS.clear(); SLEPT.clear(); BG.clear(); LOTS.clear()
    monkeypatch.setattr(ZE, "time", _ExecutorTime())        # only the executor's own waits
    monkeypatch.setattr(ZE, "_start_bg", lambda fn, name: BG.append((name, fn)))
    monkeypatch.setattr(ZE.RelayClient, "TIMEOUT", 10)
    relays((A.url, "A"))
    yield
    set_cfg(None)


def executor(ready=True):
    bm = types.SimpleNamespace(is_trade_ready=lambda: ready, refresh=lambda: None,
                               get_trade_kite=lambda: K, get_data_kite=lambda: K)
    ex = ZE.ZerodhaOrderExecutor(bm)
    ex._ensure_trading_enabled = lambda: None
    ex._get_lot_size = lambda kite, symbol, qty=None: LOTS.append((kite, symbol, qty)) or 65
    return ex


def r05(x):
    return round(round(x / 0.05) * 0.05, 2)


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0277 — relay_config.json
# ════════════════════════════════════════════════════════════════════════════
def test_0277_config_missing_or_off():
    set_cfg(None)
    assert ZE._load_relay_config() is None and ZE.relay_is_active() is False
    set_cfg({"enabled": False, "relays": [{"url": A.url, "secret": "s"}]})
    assert ZE._load_relay_config() is None
    set_cfg({"enabled": True, "relays": []})
    assert ZE._load_relay_config() is None
    assert "[RELAY] relay_config.json found but no relay entries" in LOG
    set_cfg("{not json")
    assert ZE._load_relay_config() is None
    assert any(m.startswith("[RELAY] Failed to load relay_config.json ERR=") for m in LOG)


def test_0277_config_single_relay_migrated():
    set_cfg({"enabled": True, "url": "http://r1", "secret": "s1", "ssh_username": "u",
             "ssh_key": "k", "instance_id": "i-1"})
    cfg = ZE._load_relay_config()
    assert cfg["relays"] == [{"url": "http://r1", "host": "http://r1", "secret": "s1",
                              "ssh_username": "u", "ssh_key": "k", "instance_id": "i-1",
                              "is_primary": True}]
    assert "[RELAY] Migrated single-relay config to multi-relay format" in LOG
    assert "[RELAY] Order relay ENABLED -> ['http://r1']" in LOG
    set_cfg({"enabled": True, "url": "http://r1", "host": "oci-1"})
    assert ZE._load_relay_config()["relays"][0]["host"] == "oci-1"
    assert ZE._load_relay_config()["relays"][0]["secret"] == ""


def test_0277_config_cached_until_invalidated():
    relays((A.url, "A"))
    first = ZE._load_relay_config()
    assert ZE.relay_is_active() is True
    (_TMP / "relay_config.json").unlink()
    assert ZE._load_relay_config() is first                    # read once, then cached
    ZE._invalidate_relay_cache()
    assert "[RELAY] Config cache invalidated" in LOG
    assert ZE._load_relay_config() is None


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0278 — what goes over the wire
# ════════════════════════════════════════════════════════════════════════════
def _client(url=None):
    return ZE.RelayClient(url=(url or A.url) + "/", secret="sek", api_key="KEY", access_token="TOK")


def test_0278_wire_every_relay_call():
    c = _client()
    assert c.base_url == A.url
    assert c.place_order(tradingsymbol=SYM, quantity=65) == "4711"
    assert c.place_gtt(tradingsymbol=SYM) == {"trigger_id": 901}
    assert c.cancel_gtt(901) is None
    assert c.cancel_order("regular", "O9") is None
    assert c.modify_order(variety="regular", order_id="O9", price=12.5) == "O9"
    A.script.append((200, "{}"))
    assert c.modify_order(variety="regular", order_id="O8", price=1) == "O8"   # answer without id
    auth = {"KEY": "KEY", "TOK": "TOK"}
    r = A.reqs
    assert [(x["method"], x["path"]) for x in r] == [
        ("POST", "/relay/place_order"), ("POST", "/relay/place_gtt"), ("DELETE", "/relay/gtt/901"),
        ("POST", "/relay/cancel_order"), ("POST", "/relay/modify_order"), ("POST", "/relay/modify_order")]
    assert all(x["auth"] == "Bearer sek" for x in r)
    assert r[0]["body"] == {"tradingsymbol": SYM, "quantity": 65, "api_key": "KEY", "access_token": "TOK"}
    assert r[2]["query"] == {"api_key": auth["KEY"], "access_token": auth["TOK"]} and r[2]["body"] is None
    assert r[3]["body"] == {"variety": "regular", "order_id": "O9", "api_key": "KEY", "access_token": "TOK"}
    assert r[4]["body"] == {"variety": "regular", "order_id": "O9", "price": 12.5,
                            "api_key": "KEY", "access_token": "TOK"}


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0279 — every kind of answer
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("status", [422, 502])
def test_0279_broker_rejection(status):
    A.script.append((status, json.dumps({"kite_exception": "InputException",
                                         "error": "Insufficient   funds.  Learn more - https://z.in/x"})))
    with pytest.raises(ZE.RelayBrokerRejection) as e:
        _client().place_order(tradingsymbol=SYM)
    assert e.value.kite_exception == "InputException" and e.value.host == ""
    assert str(e.value) == "InputException: Insufficient funds"


@pytest.mark.parametrize("status,text", [
    (500, json.dumps({"kite_exception": "InputException", "error": "x"})),   # 500 is the relay, not Kite
    (422, json.dumps({"error": "no exception named"})),
    (401, "bad secret"),
])
def test_0279_relay_errors(status, text):
    A.script.append((status, text))
    with pytest.raises(RuntimeError) as e:
        _client().place_order(tradingsymbol=SYM)
    assert type(e.value) is RuntimeError
    assert str(e.value) == f"Relay /relay/place_order failed HTTP {status}: {text[:200]}"


def test_0279_long_error_body_cut_and_gtt_delete_path():
    A.script.append((500, "E" * 500))
    with pytest.raises(RuntimeError) as e:
        _client().cancel_gtt(55)
    assert str(e.value) == "Relay /relay/gtt/55 failed HTTP 500: " + "E" * 200


def test_0279_bad_json_and_timeout(monkeypatch):
    A.script.append((200, "<html>proxy</html>"))
    with pytest.raises(ValueError):
        _client().place_order(tradingsymbol=SYM)
    monkeypatch.setattr(ZE.RelayClient, "TIMEOUT", 0.3)
    A.script.append(("hang", None))
    with pytest.raises(requests.exceptions.ReadTimeout):
        _client().place_order(tradingsymbol=SYM)
    with pytest.raises(requests.exceptions.ConnectionError):
        _client(DEAD).place_order(tradingsymbol=SYM)


def test_0279_clean_broker_text():
    c = ZE._clean_broker_text
    assert c("  Margin  short.\n Learn more - https://support.zerodha.com/a  ") == "Margin short"
    assert c(None) == "" and c("-· ok ·-") == "ok"
    assert c("x" * 10, limit=5) == "xxxx…" and c("x" * 5, limit=5) == "xxxxx"
    assert len(c("y" * 400)) == 300


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0280 — place_limit_buy
# ════════════════════════════════════════════════════════════════════════════
def test_0280_limit_buy_request():
    ex = executor()
    assert ex.place_limit_buy(SYM, 130, 101.26) == "4711"
    b = A.reqs[0]["body"]
    tag = b.pop("tag")
    assert b == {"variety": "regular", "exchange": "NFO", "tradingsymbol": SYM, "transaction_type": "BUY",
                 "quantity": 130, "product": "MIS", "order_type": "LIMIT", "price": 101.3,
                 "api_key": "KEY", "access_token": "TOK"}
    assert tag.isalnum() and 0 < len(tag) <= 20
    assert LOG[-1] == f"[LIMIT-BUY] {SYM} x130 @101.26 product=MIS ORDER_ID=4711"
    assert any(m == f"[RELAY][LIMIT_BUY] {SYM} - SUCCESS via A" for m in LOG)
    assert SENDS and SENDS[0][0] == "LIMIT_BUY" and SENDS[0][1] == SYM and SENDS[0][5] == "4711"
    assert K.calls == []                                        # nothing direct


def test_0280_limit_buy_product_and_caller_tag():
    ex = executor()
    ex.place_limit_buy(SYM, "65", 50, product="NRML", tag="MYTAG1")
    ex.place_limit_buy(SYM, 65, 50, product="CNC")
    b1, b2 = A.reqs[0]["body"], A.reqs[1]["body"]
    assert b1["product"] == "NRML" and b1["tag"] == "MYTAG1" and b1["quantity"] == 65
    assert b2["product"] == "NRML" and b2["tag"] != "MYTAG1"
    with pytest.raises(RuntimeError, match="place_limit_buy: no trade kite"):
        executor(ready=False).place_limit_buy(SYM, 65, 50)


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0281 — modify_order (exit re-peg, Tigris)
# ════════════════════════════════════════════════════════════════════════════
def test_0281_modify_order():
    ex = executor()
    assert ex.modify_order("O77", 23.45, symbol=SYM) == "O77"
    assert A.reqs[0]["path"] == "/relay/modify_order"
    assert A.reqs[0]["body"] == {"variety": "regular", "order_id": "O77", "price": 23.45,
                                 "api_key": "KEY", "access_token": "TOK"}
    assert f"[ZERODHA-MODIFY] ORDER_ID=O77 SYMBOL={SYM} NEW_LIMIT=23.45" in LOG
    assert f"[RELAY][MODIFY_ORDER] {SYM} order_id=O77 price=23.45 - SUCCESS via A" in LOG
    assert executor(ready=False).modify_order("O77", 1) is None


def test_0281_modify_order_failure_is_none():
    ex = executor()
    A.script.append((422, json.dumps({"kite_exception": "InputException", "error": "Order completed"})))
    assert ex.modify_order("O77", 23.45) is None
    assert any(m.startswith("[ZERODHA-MODIFY-FAIL] ORDER_ID=O77 SYMBOL= price=23.45 ERR=InputException")
               for m in LOG)
    relays((DEAD, "A"))
    assert ex.modify_order("O78", 2) is None and K.calls == []   # never direct when relays are set


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0282 — place_gtt_tp_only_long (Icarus target backstop)
# ════════════════════════════════════════════════════════════════════════════
def _gtt_body(trigger_type, triggers, last, side, price, qty=130):
    return {"trigger_type": trigger_type, "tradingsymbol": SYM, "exchange": "NFO",
            "trigger_values": triggers, "last_price": last,
            "orders": [{"transaction_type": side, "quantity": qty, "order_type": "LIMIT",
                        "price": price, "product": "NRML"}],
            "api_key": "KEY", "access_token": "TOK"}


def test_0282_tp_only_long_request():
    ex = executor()
    assert ex.place_gtt_tp_only_long(SYM, 130, 120.03, last_price=100.004) == "901"
    assert A.reqs[0]["body"] == _gtt_body("single", [120.05], 100.0, "SELL", r05(120.03 * 0.997))
    assert LOG[-1] == f"[ZERODHA-GTT-PLACED] GTT_ID=901 SYMBOL={SYM} TP_ONLY_LONG TP=120.05/119.65 last=100.0"
    A.script.append((200, json.dumps(555)))                     # an answer that is just the id
    assert ex.place_gtt_tp_only_long(SYM, 130, 120, last_price=100) == "555"


@pytest.mark.parametrize("args,msg", [
    ((0, 120, 100), "INVALID_QTY_FOR_TP_GTT"), ((100, 120, 100), "TP_GTT_INVALID_QTY qty=100 lot_size=65"),
    ((130, 120, -1), "LTP unavailable for TP GTT"), ((130, 0.01, 100), "TP_GTT_INVALID_TP TP=0.0"),
    ((130, 100, 100), "Invalid TP-GTT band LAST=100 !< TP=100.0"), ((130, 90, 100), "!< TP=90.0"),
])
def test_0282_tp_only_long_refusals(args, msg):
    qty, tp, last = args
    with pytest.raises(RuntimeError) as e:
        executor().place_gtt_tp_only_long(SYM, qty, tp, last_price=last)
    assert msg in str(e.value) and A.reqs == []


def test_0282_tp_only_long_lost_answer_found_not_resent(monkeypatch):
    monkeypatch.setattr(ZE.RelayClient, "TIMEOUT", 0.3)
    A.script.append(("hang", None))
    K.gtts = [{"id": 902, "status": "active",
               "condition": {"tradingsymbol": SYM, "trigger_values": [120.05]},
               "orders": [{"quantity": 130}]}]
    assert executor().place_gtt_tp_only_long(SYM, 130, 120.03, last_price=100) == "902"
    assert len(A.reqs) == 1 and SLEPT[:1] == [0.3]
    assert any("it IS at the broker (id=902) — not sent again" in m for m in LOG)
    with pytest.raises(RuntimeError, match="BROKER_NOT_READY_FOR_TP_GTT"):
        executor(ready=False).place_gtt_tp_only_long(SYM, 130, 120, last_price=100)


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0283 — single-leg stop GTTs (Indica, Icarus adjustment legs)
# ════════════════════════════════════════════════════════════════════════════
def test_0283_sl_only_long():
    ex = executor()
    assert ex.place_gtt_sl_only_long(SYM, 130, 80.02, last_price=100) == "901"
    assert A.reqs[-1]["body"] == _gtt_body("single", [80.0], 100, "SELL", r05(80.0 / 1.003))
    assert LOG[-1] == f"[ZERODHA-GTT-PLACED] GTT_ID=901 SYMBOL={SYM} SL_ONLY_LONG SL=80.0/79.75 last=100"
    ex.place_gtt_sl_only_long(SYM, 130, 80, last_price=100, limit_buffer=1.05)
    assert A.reqs[-1]["body"]["orders"][0]["price"] == r05(80 / 1.05)
    ex.place_gtt_sl_only_long(SYM, 130, 80, last_price=100, limit_buffer=0.5)   # < 1 → no buffer
    assert A.reqs[-1]["body"]["orders"][0]["price"] == 80.0
    ex.place_gtt_sl_only_long(SYM, 130, 0.05, last_price=1, limit_buffer=5)
    assert A.reqs[-1]["body"]["orders"][0]["price"] == 0.05                     # never below a tick
    ex.place_gtt_sl_only_long(SYM, 130, 80, last_price=100, limit_buffer=None)
    assert A.reqs[-1]["body"]["orders"][0]["price"] == r05(80 / 1.003)


@pytest.mark.parametrize("args,msg", [
    ((0, 80, 100), "INVALID_QTY_FOR_SL_GTT_LONG"), ((64, 80, 100), "SL_GTT_LONG_INVALID_QTY qty=64"),
    ((130, 80, None), "LTP unavailable for SL GTT (long)"), ((130, 0.01, 100), "SL_GTT_LONG_INVALID_SL"),
    ((130, 100, 100), "Invalid SL-GTT-LONG band LAST=100 !> SL=100.0"),
])
def test_0283_sl_only_long_refusals(args, msg, monkeypatch):
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: None))
    qty, sl, last = args
    with pytest.raises(RuntimeError) as e:
        executor().place_gtt_sl_only_long(SYM, qty, sl, last_price=last)
    assert msg in str(e.value) and A.reqs == []


def test_0283_sl_only_short():
    ex = executor()
    assert ex.place_gtt_sl_only_short(SYM, 130, 142.02, last_price=100) == "901"
    assert A.reqs[-1]["body"] == _gtt_body("single", [142.0], 100, "BUY", r05(142.0 * 1.003))
    assert LOG[-1] == (f"[ZERODHA-GTT-PLACED] GTT_ID=901 SYMBOL={SYM} SL_ONLY_SHORT "
                       f"SL=142.0/{r05(142.0 * 1.003)} last=100")
    ex.place_gtt_sl_only_short(SYM, 130, 142, last_price=100, limit_buffer=1.05)
    assert A.reqs[-1]["body"]["orders"][0]["price"] == r05(142 * 1.05)
    ex.place_gtt_sl_only_short(SYM, 130, 142, last_price=100, limit_buffer=None)
    assert A.reqs[-1]["body"]["orders"][0]["price"] == r05(142 * 1.003)
    with pytest.raises(RuntimeError, match="Invalid SL-GTT band LAST=150 !< SL=142.0"):
        ex.place_gtt_sl_only_short(SYM, 130, 142, last_price=150)


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0284 — place_gtt_oco
# ════════════════════════════════════════════════════════════════════════════
def _two(trig, side, p1, p2, last=100):
    b = _gtt_body("two-leg", trig, last, side, p1)
    b["orders"].append(dict(b["orders"][0], price=p2))
    return b


def test_0284_oco_long():
    ex = executor()
    assert ex.place_gtt_oco(SYM, 130, 80.02, 130.03, last_price=100) == "901"
    assert A.reqs[-1]["body"] == _two([80.0, 130.05], "SELL", r05(80.02 * 0.995), r05(130.03 * 0.997))
    assert LOG[-1] == (f"[ZERODHA-GTT-PLACED] GTT_ID=901 SYMBOL={SYM} LONG SL=80.0/{r05(80.02 * 0.995)} "
                       f"TP=130.05/{r05(130.03 * 0.997)}")
    ex.place_gtt_oco(SYM, 130, 80, 130, last_price=100, limit_buffer=1.05)
    assert A.reqs[-1]["body"]["orders"][0]["price"] == r05(80 / 1.05)
    ex.place_gtt_oco(SYM, 130, 80, 0, last_price=100)            # no target: a single stop
    assert A.reqs[-1]["body"] == _gtt_body("single", [80.0], 100, "SELL", r05(80 * 0.995))
    assert LOG[-1].endswith("LONG SL=80.0/79.6")


def test_0284_oco_sub_rupee_legs():
    ex = executor()
    ex.place_gtt_oco(SYM, 130, 0.3, 0.8, last_price=0.5, limit_buffer=1.05)
    b = A.reqs[-1]["body"]
    assert b["trigger_type"] == "two-leg" and b["trigger_values"] == [0.3, 0.8]
    assert b["orders"][0]["price"] == r05(max(0.05, 0.3 / 1.05)) == 0.3
    ex.place_gtt_oco(SYM, 130, 0.5, 0.0, last_price=0.7)          # no target: single stop
    assert A.reqs[-1]["body"]["trigger_type"] == "single"


def test_0284_oco_short():
    ex = executor()
    ex.place_gtt_oco(SYM, 130, 142.02, 60.03, last_price=100, direction="SHORT")
    assert A.reqs[-1]["body"] == _two([60.05, 142.0], "BUY", r05(60.05 * 1.003), r05(142.0 * 1.005))
    ex.place_gtt_oco(SYM, 130, 142, 60, last_price=100, direction="SHORT", limit_buffer=1.05)
    assert A.reqs[-1]["body"]["orders"][1]["price"] == r05(142 * 1.05)
    assert LOG[-1] == f"[ZERODHA-GTT-PLACED] GTT_ID=901 SYMBOL={SYM} SHORT TP=60.0/60.2 SL=142.0/149.1"


@pytest.mark.parametrize("kw,msg", [
    (dict(qty=0, sl_price=80, tp_price=130), "INVALID_QTY_FOR_GTT"),
    (dict(qty=100, sl_price=80, tp_price=130), "GTT_INVALID_QTY qty=100"),
    (dict(qty=130, sl_price=0.01, tp_price=130), "GTT_INVALID_SL SL=0.0"),
    (dict(qty=130, sl_price=100, tp_price=130), "Invalid GTT band SL=100.0 LAST=100 TP=130.0"),
    (dict(qty=130, sl_price=80, tp_price=100), "Invalid GTT band SL=80.0 LAST=100 TP=100.0"),
    (dict(qty=130, sl_price=100, tp_price=0), "Invalid GTT band SL=100.0 LAST=100"),
    (dict(qty=130, sl_price=142, tp_price=0, direction="SHORT"), "SHORT GTT requires a valid tp_price"),
    (dict(qty=130, sl_price=100, tp_price=60, direction="SHORT"), "Invalid SHORT GTT band TP=60.0 LAST=100 SL=100.0"),
    (dict(qty=130, sl_price=142, tp_price=100, direction="SHORT"), "Invalid SHORT GTT band TP=100.0"),
])
def test_0284_oco_refusals(kw, msg):
    with pytest.raises(RuntimeError) as e:
        executor().place_gtt_oco(SYM, last_price=100, **kw)
    assert msg in str(e.value) and A.reqs == []


def test_0284_oco_no_ltp_and_not_ready(monkeypatch):
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: None))
    _raises(lambda: executor().place_gtt_oco(SYM, 130, 80, 130), "LTP unavailable for GTT")
    _raises(lambda: executor(ready=False).place_gtt_oco(SYM, 130, 80, 130, last_price=100), "BROKER_NOT_READY_FOR_GTT")
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: 100.0))
    assert executor().place_gtt_oco(SYM, 130, 80, 130) == "901"


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0285 — get_basket_margin (Icarus / Timberwolf / Tomahawk margin checks)
# ════════════════════════════════════════════════════════════════════════════
def test_0285_basket_margin():
    K.margin_res = {"final": {"total": 123456.7}, "initial": {"total": 1}}
    K.margins_res = {"available": {"live_balance": 200000.4, "cash": 1}, "net": 2}
    basket = [{"symbol": SYM, "qty": "130", "transaction_type": "SELL"},
              {"symbol": "NIFTY26D0226000CE", "qty": 130, "transaction_type": "BUY"}]
    assert executor().get_basket_margin(basket) == {"required": 123456.7, "available": 200000.4}
    name, (params,), kw = K.calls[0]
    assert name == "basket_order_margins" and kw == {"consider_positions": True}
    assert params[0] == {"exchange": "NFO", "tradingsymbol": SYM, "transaction_type": "SELL",
                         "variety": "regular", "product": "NRML", "order_type": "MARKET",
                         "quantity": 130, "price": 0, "trigger_price": 0}
    assert params[1]["tradingsymbol"] == "NIFTY26D0226000CE" and params[1]["transaction_type"] == "BUY"
    assert K.calls[1][:2] == ("margins", ("equity",))
    assert LOG[-1] == "[IC][MARGIN_CHECK] required=123457 available=200000"
    assert A.reqs == []                                         # a read: never through the relay


@pytest.mark.parametrize("res,avail,want", [
    ({"initial": {"total": 50.0}}, {"available": {"cash": 70.0}}, (50.0, 70.0)),
    ({"final": {"total": 0}, "initial": {"total": 50.0}}, {"net": 9.0}, (50.0, 9.0)),
    ({}, {}, (0.0, 0.0)),
    (None, None, (0.0, 0.0)),
])
def test_0285_basket_margin_fallbacks(res, avail, want):
    K.margin_res, K.margins_res = res, avail
    got = executor().get_basket_margin([{"symbol": SYM, "qty": 65, "transaction_type": "SELL"}])
    assert (got["required"], got["available"]) == want


def test_0285_basket_margin_raises():
    with pytest.raises(RuntimeError, match="BROKER_NOT_READY_FOR_MARGIN"):
        executor(ready=False).get_basket_margin([])
    K.raise_on["basket_order_margins"] = RuntimeError("api down")
    with pytest.raises(RuntimeError, match="api down"):
        executor().get_basket_margin([{"symbol": SYM, "qty": 65, "transaction_type": "SELL"}])


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0286 — order book reads
# ════════════════════════════════════════════════════════════════════════════
EMPTY = {"status": None, "avg_price": 0.0, "filled_qty": 0, "pending_qty": 0, "found": False,
         "status_message": ""}


def test_0286_order_fill():
    K.book = [{"order_id": "O1", "status": "COMPLETE", "average_price": "101.5", "filled_quantity": "130",
               "pending_quantity": None, "status_message": None, "status_message_raw": "raw text"},
              {"order_id": "O2", "status": "REJECTED", "average_price": None, "filled_quantity": 0,
               "pending_quantity": 65, "status_message": "Insufficient funds"}]
    ex = executor()
    assert ex.get_order_fill("O1") == {"status": "COMPLETE", "avg_price": 101.5, "filled_qty": 130,
                                       "pending_qty": 0, "found": True, "status_message": "raw text"}
    assert ex.get_order_fill("O2") == {"status": "REJECTED", "avg_price": 0.0, "filled_qty": 0,
                                       "pending_qty": 65, "found": True,
                                       "status_message": "Insufficient funds"}
    assert ex.get_order_fill("O3") == EMPTY
    assert ex.get_last_avg_price("O1") == 101.5 and ex.get_last_avg_price("O3") == 0.0
    assert executor(ready=False).get_order_fill("O1") == EMPTY
    assert executor(ready=False).get_last_avg_price("O1") == 0.0
    K.raise_on["orders"] = RuntimeError("book down")
    assert ex.get_order_fill("O1") == EMPTY
    assert "[ZERODHA][WARN] Order fill fetch failed ERR=book down" in LOG
    assert ex.get_last_avg_price("O1") == 0.0
    assert "[ZERODHA][WARN] Avg price fetch failed ERR=book down" in LOG


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0287 — relay routing and fallback
# ════════════════════════════════════════════════════════════════════════════
def test_0287_primary_first_then_next():
    set_cfg({"enabled": True, "relays": [
        {"url": B.url, "host": "B", "secret": "sek"},
        {"url": A.url, "host": "A", "secret": "sek", "is_primary": True}]})
    A.script.append((401, "bad secret"))                        # refused: certainly not sent
    executor().cancel_order("O5", symbol=SYM)
    assert len(A.reqs) == 1 and len(B.reqs) == 1                # A first (primary), then B
    assert f"[RELAY][CANCEL_ORDER] {SYM} order_id=O5 - SUCCESS via B" in LOG
    assert K.calls == []


def test_0287_entry_without_url_or_secret_skipped():
    set_cfg({"enabled": True, "relays": [{"url": "", "host": "X", "secret": "s", "is_primary": True},
                                         {"url": A.url, "host": "A", "secret": ""},
                                         {"url": A.url, "host": "A2", "secret": "sek"}]})
    executor().cancel_order("O5")
    assert "[RELAY][CANCEL_ORDER] Skipping X - missing url/secret" in LOG
    assert "[RELAY][CANCEL_ORDER] Skipping A - missing url/secret" in LOG
    assert len(A.reqs) == 1 and "[RELAY][CANCEL_ORDER] order_id=O5 - SUCCESS via A2" in LOG


def test_0287_all_failed_no_direct():
    relays((DEAD, "A"), (A.url, "B"))
    A.script.append((401, "bad secret"))
    with pytest.raises(ZE.RelayUnavailableError) as e:
        executor().cancel_order("O5")
    assert str(e.value) == ("all 2 relay(s) failed — [A] unreachable (ConnectionError); "
                            "[B] Relay /relay/cancel_order failed HTTP 401: bad secret")
    assert K.calls == []
    assert any(m.startswith("[RELAY][CANCEL_ORDER][ALL_FAILED] order_id=O5 - NO direct fallback") for m in LOG)
    set_cfg({"enabled": True, "relays": [{"url": "", "host": "X", "secret": ""}]})
    with pytest.raises(ZE.RelayUnavailableError, match="no relay had url/secret"):
        executor().cancel_order("O6")


def test_0287_direct_fallback_when_allowed():
    relays((DEAD, "A"), allow_direct_fallback=True)
    executor().cancel_order("O5")
    assert K.calls == [("cancel_order", (), {"variety": "regular", "order_id": "O5"})]
    assert "[RELAY][CANCEL_ORDER][DIRECT_OK] order_id=O5" in LOG
    set_cfg(None)                                               # no relays at all: direct
    executor().cancel_order("O6")
    assert K.calls[-1][2]["order_id"] == "O6"


def test_0287_rejection_is_final():
    relays((A.url, "A"), (B.url, "B"))
    A.script.append((422, json.dumps({"kite_exception": "InputException", "error": "Insufficient funds"})))
    with pytest.raises(ZE.RelayBrokerRejection) as e:
        executor().place_limit_buy(SYM, 65, 50)
    assert e.value.host == "A" and B.reqs == [] and K.calls == []
    assert f"[RELAY][LIMIT_BUY][BROKER_REJECT] {SYM} via A: InputException: Insufficient funds" in LOG
    assert SENDS[-1][5] is None and SENDS[-1][6].startswith("RelayBrokerRejection: InputException")


def test_0287_unclear_then_next_relay_watches_for_late():
    relays((A.url, "A"), (B.url, "B"))
    A.script.append((503, "upstream timeout"))                  # 5xx: it may have left
    assert executor().place_limit_buy(SYM, 65, 50) == "4711"
    assert len(B.reqs) == 1 and K.calls and K.calls[0][0] == "orders"   # looked it up first
    assert SLEPT == [0.3, 0.7, 1.0]
    assert [n for n, _ in BG] == ["order-once-watch"]           # sent twice? watched


def test_0287_no_kite_goes_direct():
    ex = executor()
    calls = []
    ex._kite = lambda: None
    out = ex._relay_call_inner(lambda r: calls.append("relay"), lambda: "direct", "X")
    assert out == "direct" and calls == []


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0288 — a late order / GTT
# ════════════════════════════════════════════════════════════════════════════
def test_0288_late_gtt_cancelled():
    executor()._settle_late("905", "GTT_SL_ONLY_LONG", SYM, None)
    assert A.reqs[0]["method"] == "DELETE" and A.reqs[0]["path"] == "/relay/gtt/905"
    msg = PAGES[-1]["message"]
    assert PAGES[-1]["severity"] == "warning"
    assert msg == (f"GTT_SL_ONLY_LONG {SYM}: a GTT arrived at Zerodha LATE (id 905) — the app treated "
                   "it as failed and is not tracking it. It has been CANCELLED. Verify in Kite.")
    assert LOG[-1] == f"[ORDER_ONCE][LATE] {msg}"


def test_0288_late_order_working_cancelled():
    K.book = [{"order_id": "L1", "status": "OPEN", "filled_quantity": 0}]
    executor()._settle_late("L1", "BUY_EXIT", SYM, "O2")
    assert A.reqs[0]["path"] == "/relay/cancel_order" and A.reqs[0]["body"]["order_id"] == "L1"
    assert PAGES[-1]["severity"] == "warning"
    assert "it was sent twice (the second is O2). It has been CANCELLED." in PAGES[-1]["message"]


def test_0288_late_order_filled_pages_error():
    K.book = [{"order_id": "L1", "status": "COMPLETE", "filled_quantity": 65}]
    executor()._settle_late("L1", "BUY_EXIT", SYM, None)
    assert A.reqs == [] and PAGES[-1]["severity"] == "error"
    assert PAGES[-1]["message"].endswith("It could NOT be cancelled (it may have filled). "
                                         "CHECK KITE Positions / Orders NOW.")
    K.book = [{"order_id": "L2", "status": "OPEN", "filled_quantity": 10}]   # partly filled
    executor()._settle_late("L2", "BUY_EXIT", SYM, None)
    assert A.reqs == [] and PAGES[-1]["severity"] == "error"


def test_0288_late_order_dead_nothing_to_do():
    for st in ("REJECTED", "CANCELLED", "LAPSED"):
        K.book = [{"order_id": "L1", "status": st, "filled_quantity": 0}]
        executor()._settle_late("L1", "SELL_ENTRY", SYM, None)
        assert LOG[-1] == f"[ORDER_ONCE][SELL_ENTRY] {SYM}: late order L1 is {st} — nothing to do"
    assert PAGES == [] and A.reqs == []


def test_0288_late_cancel_fails():
    A.script.append((401, "bad secret"))
    executor()._settle_late("906", "GTT", SYM, None)
    assert any(m.startswith(f"[ORDER_ONCE][GTT] {SYM}: could not cancel late GTT 906:") for m in LOG)
    assert PAGES[-1]["severity"] == "error"
    assert PAGES[-1]["message"].endswith("CHECK KITE Positions / Orders / GTT NOW.")


def test_0288_watch_late_runs_and_settles():
    ex = executor()
    seen = iter([[], ["O2"], ["O2", "L9"]])
    settled = []
    ex._settle_late = lambda *a: settled.append(a)
    ex._watch_late(lambda: next(seen), "BUY_EXIT", SYM, "O2")
    name, fn = BG[0]
    assert name == "order-once-watch"
    fn()
    assert settled == [("L9", "BUY_EXIT", SYM, "O2")] and SLEPT == [5.0, 5.0, 5.0]


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0289 — could the request have reached the broker?
# ════════════════════════════════════════════════════════════════════════════
class InputException(Exception):
    pass


@pytest.mark.parametrize("err,direct,want", [
    (requests.exceptions.ConnectTimeout("x"), False, False),
    (requests.exceptions.ConnectionError("Failed to establish a new connection: refused"), False, False),
    (requests.exceptions.ReadTimeout("Read timed out"), False, True),
    (requests.exceptions.ConnectionError("Connection aborted"), False, True),
    (NetworkException("gateway"), True, True),
    (InputException("Insufficient funds"), True, False),
    (RuntimeError("Relay /relay/place_order failed HTTP 503: x"), False, True),
    (RuntimeError("Relay /relay/place_order failed HTTP 500: x"), False, True),
    (RuntimeError("Relay /relay/place_order failed HTTP 499: x"), False, False),
    (RuntimeError("Relay /relay/place_order failed HTTP 422: x"), False, False),
    (ValueError("Expecting value: line 1"), False, True),
])
def test_0289_maybe_sent(err, direct, want):
    assert ZE._maybe_sent(err, direct=direct) is want


def test_0289_find_gtts_like():
    ex = executor()
    want = {"tradingsymbol": SYM, "trigger_values": [80.004], "orders": [{"quantity": 130}]}
    K.gtts = [{"id": 1, "status": "triggered", "condition": {"tradingsymbol": SYM, "trigger_values": [80.0]},
               "orders": [{"quantity": 130}]},
              {"id": 2, "status": "ACTIVE", "condition": {"tradingsymbol": "OTHER", "trigger_values": [80.0]},
               "orders": [{"quantity": 130}]},
              {"id": 3, "status": "active", "condition": {"tradingsymbol": SYM, "trigger_values": [80.05]},
               "orders": [{"quantity": 130}]},
              {"id": 4, "status": "active", "condition": {"tradingsymbol": SYM, "trigger_values": [80.0]},
               "orders": [{"quantity": 65}]},
              {"id": 5, "status": "Active", "condition": {"tradingsymbol": SYM, "trigger_values": [80.0]},
               "orders": [{"quantity": "130"}]}]
    assert ex._find_gtts_like(want) == ["5"]
    K.raise_on["get_gtts"] = RuntimeError("down")
    assert ex._find_gtts_like(want) == []
    assert ex._find_orders_by_tag("") == []
    K.raise_on.clear()
    K.book = [{"order_id": 7, "tag": "T1"}, {"order_id": 8, "tags": ["T1", "x"]}, {"order_id": 9, "tag": "T2"}]
    assert ex._find_orders_by_tag("T1") == ["7", "8"]


def test_0289_tag_params():
    p = {"tag": "KEEP"}
    assert ZE._tag_params(p) is None and p["tag"] == "KEEP"
    q = {}
    t = ZE._tag_params(q)
    assert t and q["tag"] == t and len(t) <= 20 and t.isalnum()
    assert ZE._once_kw(None, None) == {} and ZE._once_kw("T", None) == {"tag": "T"}


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0290 — broker reads: an unreadable answer is never "flat" / "absent"
# ════════════════════════════════════════════════════════════════════════════
def test_0290_positions():
    K.positions_res = {"net": [{"tradingsymbol": "A", "quantity": 65}, {"tradingsymbol": "B", "quantity": 0},
                               {"tradingsymbol": "C", "quantity": "-130"}, {"tradingsymbol": "D"}],
                       "day": [{"tradingsymbol": "E", "quantity": 5}]}
    ex = executor()
    assert [p["tradingsymbol"] for p in ex.get_open_positions_or_none()] == ["A", "C"]
    assert [p["tradingsymbol"] for p in ex.get_open_positions()] == ["A", "C"]
    K.positions_res = {}
    assert ex.get_open_positions_or_none() == [] and ex.get_open_positions() == []
    K.raise_on["positions"] = RuntimeError("down")
    assert ex.get_open_positions_or_none() is None
    assert "[ZERODHA][WARN] STRICT positions fetch failed ERR=down" in LOG
    nr = executor(ready=False)
    assert nr.get_open_positions() == [] and nr.get_open_positions_or_none() is None
    assert "[ZERODHA][WARN] STRICT positions fetch skipped — session not ready" in LOG


def test_0290_gtt_list_and_status():
    K.gtts = [{"id": 901, "status": "active"}, {"id": "902", "status": "triggered"}, {"id": 903, "status": None}]
    ex = executor()
    assert ex.get_gtt_status("901") == "active" and ex.get_gtt_status(902) == "triggered"
    assert ex.get_gtt_status(903) == "unknown" and ex.get_gtt_status(904) == "missing"
    assert len(ex.get_gtts()) == 3 and len(ex.get_gtts_or_none()) == 3
    K.raise_on["get_gtts"] = RuntimeError("down")
    assert ex.get_gtt_status(901) is None                      # unreadable, never "missing"
    assert ex.get_gtts() == [] and ex.get_gtts_or_none() is None
    assert "[ZERODHA][WARN] GTT fetch failed ERR=down" in LOG
    assert "[ZERODHA][WARN] STRICT GTT fetch failed ERR=down" in LOG
    nr = executor(ready=False)
    assert nr.get_gtts() == [] and nr.get_gtts_or_none() is None and nr.get_gtt_status(901) is None
    assert "[ZERODHA][WARN] STRICT GTT fetch skipped — session not ready" in LOG


def test_0290_session_refreshed_once_when_not_ready():
    state = {"ready": False, "refreshed": 0}

    def refresh():
        state["refreshed"] += 1
        state["ready"] = True
    bm = types.SimpleNamespace(is_trade_ready=lambda: state["ready"], refresh=refresh,
                               get_trade_kite=lambda: K)
    ex = ZE.ZerodhaOrderExecutor(bm)
    assert ex._kite() is K and state["refreshed"] == 1
    assert "[ZERODHA_EXECUTOR] Not ready - refreshing from disk" in LOG
    assert ex._kite() is K and state["refreshed"] == 1          # ready: no second refresh
    K.book = [{"order_id": "O1"}]
    assert ex.get_orders() == [{"order_id": "O1"}]


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0291 — the four GTT builders, one contract
# ════════════════════════════════════════════════════════════════════════════
def _sym(m):
    return m.replace("{S}", SYM)


GTTS = {   # name: (method, price kw, valid price, last, op, refusals {qty0, lot, not_ready, no_ltp, bad})
    "tp": ("place_gtt_tp_only_long", "tp_price", 120, 100, "GTT_TP_ONLY",
           {"qty0": "INVALID_QTY_FOR_TP_GTT SYMBOL={S} QTY=0", "lot": "TP_GTT_INVALID_QTY qty=100 lot_size=65 SYMBOL={S}",
            "not_ready": "BROKER_NOT_READY_FOR_TP_GTT", "no_ltp": "LTP unavailable for TP GTT", "bad": "TP_GTT_INVALID_TP TP=0.0"},
           (0.8, 0.5)),
    "sl_long": ("place_gtt_sl_only_long", "sl_price", 80, 100, "GTT_SL_ONLY_LONG",
                {"qty0": "INVALID_QTY_FOR_SL_GTT_LONG SYMBOL={S} QTY=0",
                 "lot": "SL_GTT_LONG_INVALID_QTY qty=100 lot_size=65 SYMBOL={S}",
                 "not_ready": "BROKER_NOT_READY_FOR_SL_GTT_LONG", "no_ltp": "LTP unavailable for SL GTT (long)",
                 "bad": "SL_GTT_LONG_INVALID_SL SL=0.0"},
                (0.6, 0.9)),
    "sl_short": ("place_gtt_sl_only_short", "sl_price", 142, 100, "GTT_SL_ONLY_SHORT",
                 {"qty0": "INVALID_QTY_FOR_SL_GTT SYMBOL={S} QTY=0", "lot": "SL_GTT_INVALID_QTY qty=100 lot_size=65 SYMBOL={S}",
                  "not_ready": "BROKER_NOT_READY_FOR_SL_GTT", "no_ltp": "LTP unavailable for SL GTT",
                  "bad": "SL_GTT_INVALID_SL SL=0.0"},
                 (0.8, 0.5)),
}


def _raises(fn, want):
    with pytest.raises(RuntimeError) as e:
        fn()
    assert str(e.value) == _sym(want)


@pytest.mark.parametrize("name", list(GTTS))
def test_0291_gtt_builder_contract(name, monkeypatch):
    meth, pkw, price, last, op, msg, (small_p, small_last) = GTTS[name]
    call = lambda ex=None, **k: getattr(ex or executor(), meth)(SYM, k.pop("qty", 130), **dict({pkw: price, "last_price": last}, **k))  # noqa: E731
    _raises(lambda: call(qty=0), msg["qty0"])
    _raises(lambda: call(qty=100), msg["lot"])
    _raises(lambda: call(executor(ready=False)), msg["not_ready"])
    _raises(lambda: call(**{pkw: 0}), msg["bad"])
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: 100.0 if s == SYM else None))
    _raises(lambda: call(last_price=-1), msg["no_ltp"])
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: None))
    _raises(lambda: call(last_price=None), msg["no_ltp"])
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: 0.0))
    _raises(lambda: call(last_price=None), msg["no_ltp"])        # a 0 tick is no price
    assert A.reqs == []
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: 100.0151 if s == SYM else None))
    LOTS.clear(); LOG.clear()
    assert call(last_price=None) == "901"                       # last price from the symbol's tick
    assert LOTS == [(K, SYM, 130)]
    assert A.reqs[-1]["body"]["last_price"] == 100.02
    assert f"[RELAY][{op}] {SYM} - SUCCESS via A" in LOG
    assert call(**{pkw: small_p, "last_price": small_last}) == "901"   # sub-rupee option, still valid
    A.script.append((200, "555"))                               # an answer that is just the id
    assert call() == "555"
    set_cfg(None)                                               # no relays: Kite directly
    assert call() == "77"
    name_, args, kw = K.calls[-1]
    assert name_ == "place_gtt" and kw["tradingsymbol"] == SYM and "api_key" not in kw


@pytest.mark.parametrize("name", ["sl_long", "sl_short"])
def test_0291_gtt_lost_answer_found(name, monkeypatch):
    meth, pkw, price, last, op, msg, _ = GTTS[name]
    monkeypatch.setattr(ZE.RelayClient, "TIMEOUT", 0.3)
    A.script.append(("hang", None))
    trig = r05(price)
    K.gtts = [{"id": 903, "status": "active", "condition": {"tradingsymbol": SYM, "trigger_values": [trig]},
               "orders": [{"quantity": 130}]}]
    assert getattr(executor(), meth)(SYM, 130, **{pkw: price, "last_price": last}) == "903"
    assert len(A.reqs) == 1 and f"[ORDER_ONCE][{op}] {SYM}: it IS at the broker (id=903) — not sent again" in LOG


def test_0291_oco_contract(monkeypatch):
    _raises(lambda: executor().place_gtt_oco(SYM, 130, 142, 0, last_price=100, direction="SHORT"),
            "SHORT GTT requires a valid tp_price")
    monkeypatch.setattr(ZE.LTPStore, "get", classmethod(lambda cls, s: 100.0151 if s == SYM else None))
    LOTS.clear()
    assert executor().place_gtt_oco(SYM, 130, 80, 130) == "901"
    assert LOTS == [(K, SYM, 130)] and A.reqs[-1]["body"]["last_price"] == 100.02
    assert f"[RELAY][GTT] {SYM} - SUCCESS via A" in LOG
    A.script.append((200, "556"))
    assert executor().place_gtt_oco(SYM, 130, 80, 130, last_price=100) == "556"
    set_cfg(None)
    assert executor().place_gtt_oco(SYM, 130, 80, 130, last_price=100) == "77"
    assert K.calls[-1][0] == "place_gtt" and K.calls[-1][2]["trigger_type"] == "two-leg"


# ════════════════════════════════════════════════════════════════════════════
# TC-EXEC-0292 — unclear failures: looked up first, then watched
# ════════════════════════════════════════════════════════════════════════════
def _book_has_tag(oid, status="OPEN", filled=0):
    tag = A.reqs[0]["body"]["tag"] if A.reqs else K.calls[0][2]["tag"]
    K.book = [{"order_id": oid, "tag": tag, "status": status, "filled_quantity": filled}]


def test_0292_resent_then_late_original_cancelled():
    relays((A.url, "A"), (B.url, "B"))
    A.script.append((503, "upstream timeout"))
    assert executor().place_limit_buy(SYM, 65, 50) == "4711"
    assert A.reqs[0]["auth"] == "Bearer sek" and B.reqs[0]["auth"] == "Bearer sek"
    assert (f"[ORDER_ONCE][LIMIT_BUY] {SYM} via A: outcome UNCLEAR (RuntimeError) — looking it up "
            "at the broker before resending") in LOG
    assert f"[ORDER_ONCE][LIMIT_BUY] {SYM}: not at the broker after 2.0s — treated as failed; watching for a late arrival" in LOG
    _book_has_tag("L7")                                         # the first one shows up late
    K.book.append({"order_id": "4711", "tag": K.book[0]["tag"], "status": "OPEN", "filled_quantity": 0})
    BG[0][1]()
    assert A.reqs[-1]["path"] == "/relay/cancel_order" and A.reqs[-1]["body"]["order_id"] == "L7"   # primary again
    assert PAGES[-1]["message"].startswith(f"LIMIT_BUY {SYM}: a order arrived at Zerodha LATE (id L7) — "
                                           "it was sent twice (the second is 4711).")


def test_0292_all_relays_unclear_watched_as_failed():
    A.script.append((503, "x"))
    with pytest.raises(ZE.RelayUnavailableError):
        executor().place_limit_buy(SYM, 65, 50)
    assert [n for n, _ in BG] == ["order-once-watch"]
    _book_has_tag("L8")
    BG[0][1]()
    assert "the app treated it as failed and is not tracking it" in PAGES[-1]["message"]
    assert f"LIMIT_BUY {SYM}:" in PAGES[-1]["message"]


def test_0292_direct_unclear_looked_up_then_watched():
    set_cfg(None)
    K.raise_on["place_order"] = NetworkException("Gateway timed out")
    with pytest.raises(NetworkException):
        executor().place_limit_buy(SYM, 65, 50)
    assert (f"[ORDER_ONCE][LIMIT_BUY] {SYM} via direct: outcome UNCLEAR (NetworkException) — looking it "
            "up at the broker before resending") in LOG
    assert [c[0] for c in K.calls].count("orders") == 3 and SLEPT == [0.3, 0.7, 1.0]
    _book_has_tag("L9")
    BG[0][1]()
    assert PAGES[-1]["message"].startswith(f"LIMIT_BUY {SYM}: a order arrived at Zerodha LATE (id L9)")


def test_0292_direct_found_and_direct_refused():
    set_cfg(None)
    K.raise_on["place_order"] = NetworkException("Gateway timed out")
    orig_orders = K.orders

    def orders_with_it():
        out = orig_orders()
        tag = next(c[2]["tag"] for c in K.calls if c[0] == "place_order")
        return out + [{"order_id": "F1", "tag": tag}]
    K.orders = orders_with_it
    assert executor().place_limit_buy(SYM, 65, 50) == "F1" and BG == []
    K.orders = orig_orders
    K.calls.clear()
    K.raise_on["place_order"] = InputException("Insufficient funds")
    with pytest.raises(InputException):
        executor().place_limit_buy(SYM, 65, 50)
    assert [c[0] for c in K.calls] == ["place_order"] and BG == []   # the broker said no: no lookup


def test_0292_unclear_then_direct_fallback_watched():
    relays((A.url, "A"), allow_direct_fallback=True)
    A.script.append((503, "x"))
    assert executor().place_limit_buy(SYM, 65, 50) == "D1"
    assert f"[RELAY][LIMIT_BUY][DIRECT_OK] {SYM}" in LOG and len(BG) == 1
    tag = A.reqs[0]["body"]["tag"]
    K.book = [{"order_id": "L5", "tag": tag, "status": "OPEN", "filled_quantity": 0},
              {"order_id": "D1", "tag": tag, "status": "OPEN", "filled_quantity": 0}]
    BG[0][1]()
    assert "it was sent twice (the second is D1)" in PAGES[-1]["message"]
    assert PAGES[-1]["message"].startswith(f"LIMIT_BUY {SYM}:")
    BG.clear(); A.script.append((503, "x"))
    K.raise_on["place_order"] = InputException("Insufficient funds")
    with pytest.raises(InputException):
        executor().place_limit_buy(SYM, 65, 50)
    assert len(BG) == 1                                         # the first may still arrive
    K.book = [{"order_id": "L6", "tag": A.reqs[-1]["body"]["tag"], "status": "OPEN", "filled_quantity": 0}]
    BG[0][1]()
    assert "the app treated it as failed" in PAGES[-1]["message"] and f"LIMIT_BUY {SYM}:" in PAGES[-1]["message"]


def test_0292_entry_defaults_and_no_lookup_without_tag():
    set_cfg({"enabled": True, "relays": [{"secret": "sek", "is_primary": True}, {"url": A.url},
                                         {"url": A.url, "secret": "sek"}]})
    executor().cancel_order("O5")
    assert "[RELAY][CANCEL_ORDER] Skipping  - missing url/secret" in LOG
    assert f"[RELAY][CANCEL_ORDER] Skipping {A.url} - missing url/secret" in LOG
    assert f"[RELAY][CANCEL_ORDER] order_id=O5 - SUCCESS via {A.url}" in LOG
    assert A.reqs[-1]["auth"] == "Bearer sek"
    relays((A.url, "A"), (B.url, "B"))
    A.script.append((503, "x"))
    executor().cancel_order("O6")                               # no tag: nothing to look up
    assert SLEPT == [] and not any(c[0] == "orders" for c in K.calls) and BG == []
    assert f"[ORDER_ONCE][CANCEL_ORDER]" not in " ".join(LOG)


def test_0292_routing_audit_texts():
    relays((DEAD, "A"), (A.url, "B"))
    A.script.append((401, "Z" * 300))
    with pytest.raises(ZE.RelayUnavailableError) as e:
        executor().cancel_order("O5")
    assert "[RELAY][CANCEL_ORDER] A unreachable (ConnectionError) - next relay" in LOG
    long_err = ZE._clean_broker_text("Relay /relay/cancel_order failed HTTP 401: " + "Z" * 200, 160)
    assert str(e.value).endswith("[B] " + long_err) and long_err.endswith("…") and len(long_err) == 160
    assert LOG[-1] == ("[RELAY][CANCEL_ORDER][ALL_FAILED] order_id=O5 - NO direct fallback (writes are "
                       "IP-gated); errors: [A] unreachable (ConnectionError) | [B] " + long_err)
    set_cfg({"enabled": True, "relays": [{"url": "", "host": "X", "secret": ""}]})
    with pytest.raises(ZE.RelayUnavailableError) as e2:
        executor().cancel_order("O6")
    assert str(e2.value) == "all 1 relay(s) failed — no relay had url/secret"
    assert LOG[-1].endswith("errors: none")
    relays((DEAD, "A"), allow_direct_fallback=True)
    executor().cancel_order("O7")
    assert ("[RELAY][CANCEL_ORDER][ALL_FAILED] order_id=O7 - direct fallback "
            "(allow_direct_fallback=true)") in LOG


def test_0292_settle_late_edge_states():
    ex = executor()
    K.book = [{"order_id": "L1", "status": "COMPLETE", "filled_quantity": 0}]
    ex._settle_late("L1", "BUY_EXIT", SYM, None)                # complete: never cancelled
    assert A.reqs == [] and PAGES[-1]["severity"] == "error"
    assert PAGES[-1]["message"] == (f"BUY_EXIT {SYM}: a order arrived at Zerodha LATE (id L1) — the app "
                                    "treated it as failed and is not tracking it. It could NOT be cancelled "
                                    "(it may have filled). CHECK KITE Positions / Orders NOW.")
    K.book = [{"order_id": "L2", "status": "REJECTED", "filled_quantity": 5}]
    ex._settle_late("L2", "BUY_EXIT", SYM, None)                # rejected after a part fill: tell
    assert PAGES[-1]["severity"] == "error" and len(PAGES) == 2
    K.book = []
    ex._settle_late("L3", "BUY_EXIT", SYM, None)                # not in the book: unknown, tell
    assert A.reqs == [] and len(PAGES) == 3 and PAGES[-1]["severity"] == "error"


class DataException(Exception):
    pass


class Timeout(Exception):
    pass


class ReadTimeout(Exception):
    pass


class _BadRepr(Exception):
    def __repr__(self):
        raise RuntimeError("no repr")


@pytest.mark.parametrize("err,want", [
    (DataException("x"), True), (Timeout("x"), True), (ReadTimeout("x"), True),
    (ConnectionError("x"), True), (TimeoutError("x"), True), (InputException("x"), False),
])
def test_0292_maybe_sent_direct_names(err, want):
    assert ZE._maybe_sent(err, direct=True) is want


def test_0292_maybe_sent_unreadable_error_is_not_sent():
    assert ZE._maybe_sent(_BadRepr()) is False


def test_0292_tag_fallback_without_instance_guard(monkeypatch):
    class T:
        sleep = staticmethod(SLEPT.append)

        @staticmethod
        def time():
            return 1793000000.123
    monkeypatch.setattr(ZE, "time", T())
    def _gone():
        raise RuntimeError("instance guard unavailable")
    monkeypatch.setattr(sys.modules["app.execution.instance_guard"], "take_tag_hint", _gone)
    monkeypatch.setattr(ZE, "_TAG_N", [5])
    p = {}
    assert ZE._tag_params(p) == "ST7930000001230006" == p["tag"] and ZE._TAG_N == [6]
    ZE._TAG_N[0] = 1295
    assert ZE._tag_params({}) == "ST7930000001230000"            # the counter wraps at 1296


if __name__ == "__main__":
    # pytest in a child, from this folder, as scripts/run_suites.py runs it
    import subprocess
    _here = os.path.dirname(os.path.abspath(__file__))
    _env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(Path(_here).parents[1]), _here] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              os.path.basename(__file__)], cwd=_here, env=_env))
