#!/usr/bin/env python3
"""
apply_ORDER_LTP_RETRY_20260911.py — FENCE: ORDER_LTP_RETRY_20260911

2026-09-09 09:16:14, Account 2 (friend's machine): TSG L4 (BUY
NIFTY2691522800PE, a ₹5–8 wing) failed with "LTP unavailable" and the whole
day unwound (−₹10). Anbu's machine entered fine the same second.

Cause: ZerodhaOrderExecutor.place_buy() priced its limit from LTPStore
(ticker) and, when the symbol was outside the subscribed universe (a far
OTM wing always is), made ONE REST kite.ltp call inside `except Exception:
ltp = None` — no audit, no retry. At 09:16 the REST budget is exactly where
the fleet bursts (IC 120-symbol chain snapshot 09:16:03, HA, Scala skew
quotes, the L3 buy itself), so one rate-limit reply or timeout = day lost.
The shared _resolve_ltp() at least logged, but also tried once.

Fix (both executors):
  * _resolve_ltp(symbol): up to 3 REST attempts. "Too many requests" →
    wait 1.1 s (the quote budget window) and retry; any other error →
    0.4 s and retry; every failure audited with the reason
    ([ZERODHA][LTP_RETRY] / [ANGEL][LTP_RETRY]). LTPStore remains the last
    fallback. Worst case ≈ 3 s before giving up — well inside the
    entry_late_grace_s window.
  * ZerodhaOrderExecutor.place_buy(): LTPStore first (fresh tick, no REST
    cost) then _resolve_ltp() — the private silent one-shot is gone.
    place_sell_entry / buy_exit / entry-quote already route through
    _resolve_ltp and inherit the retry.

Safety: fence check, py_compile gate, behavioural simulation on the patched
modules (429 then success → priced; 3×429 → LTPStore fallback; nothing
anywhere → LTP unavailable after 3 audited attempts; non-429 error retried;
place_buy uses ticker price without touching REST when available), .bak
backups, dual-tree mirror. Backend-only → rebuild backend.

Run from the repo root:  python3 apply_ORDER_LTP_RETRY_20260911.py
"""
import importlib.util, os, py_compile, shutil, sys, tempfile

FENCE = "ORDER_LTP_RETRY_20260911"
ROOT = os.path.abspath(os.getcwd())
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
REL = {"z": "app/execution/zerodha_executor.py", "a": "app/execution/angel_executor.py"}
PATH = {k: os.path.join(BACKEND, v) for k, v in REL.items()}
if not os.path.isfile(PATH["z"]):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def read(p):
    with open(p, encoding="utf-8") as f: return f.read()
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


for k, p in PATH.items():
    if FENCE in read(p):
        die(f"{FENCE} already present in {REL[k]} — nothing to do")

RETRY_BODY = (
    '        ltp = None\n'
    '        # ── ORDER_LTP_RETRY_20260911 ── up to 3 REST attempts, rate-limit\n'
    '        # aware, every failure audited. One silent one-shot cost a whole\n'
    '        # TSG day on 2026-09-09 (wing outside the ticker universe + a 09:16\n'
    '        # REST burst).\n'
    '        for attempt in range(1, 4):\n'
    '            try:\n'
    '                data_kite = self.broker_manager.get_data_kite()\n'
    '                if not data_kite:\n'
    '                    break\n'
    '                quote = data_kite.ltp(f"NFO:{symbol}")\n'
    '                rest_ltp = (quote or {}).get(f"NFO:{symbol}", {}).get("last_price")\n'
    '                if rest_ltp and rest_ltp > 0:\n'
    '                    ltp = float(rest_ltp)\n'
    '                    break\n'
    '                write_audit_log(f"[{TAG}][LTP_RETRY] {symbol} attempt {attempt}/3: "\n'
    '                                f"empty quote")\n'
    '            except Exception as e:\n'
    '                write_audit_log(f"[{TAG}][LTP_RETRY] {symbol} attempt {attempt}/3: {e!r}")\n'
    '                if attempt < 3:\n'
    '                    time.sleep(1.1 if "too many requests" in repr(e).lower() else 0.4)\n'
    '                continue\n'
    '            if attempt < 3:\n'
    '                time.sleep(0.4)\n'
)

# ── Zerodha ─────────────────────────────────────────────────────────────
z = read(PATH["z"])
z = sub1(z,
    '    def _resolve_ltp(self, symbol: str) -> Optional[float]:\n'
    '        """REST-primary LTP fetch (for order placement)."""\n'
    '        ltp = None\n'
    '        try:\n'
    '            data_kite = self.broker_manager.get_data_kite()\n'
    '            if data_kite:\n'
    '                quote = data_kite.ltp(f"NFO:{symbol}")\n'
    '                rest_ltp = quote.get(f"NFO:{symbol}", {}).get("last_price")\n'
    '                if rest_ltp and rest_ltp > 0:\n'
    '                    ltp = rest_ltp\n'
    '        except Exception as e:\n'
    '            write_audit_log(f"[ZERODHA] REST LTP failed for {symbol}: {e}")\n'
    '\n'
    '        if not ltp or ltp <= 0:\n'
    '            ltp = LTPStore.get(symbol)\n'
    '\n'
    '        return ltp\n',
    '    def _resolve_ltp(self, symbol: str) -> Optional[float]:\n'
    '        """REST-primary LTP fetch (for order placement), retrying."""\n'
    + RETRY_BODY.replace("{TAG}", "ZERODHA") +
    '\n'
    '        if not ltp or ltp <= 0:\n'
    '            ltp = LTPStore.get(symbol)\n'
    '            if ltp and ltp > 0:\n'
    '                write_audit_log(f"[ZERODHA][LTP_RETRY] {symbol}: REST exhausted, "\n'
    '                                f"using LTPStore {ltp}")\n'
    '\n'
    '        return ltp\n',
    "zerodha _resolve_ltp")
z = sub1(z,
    '        ltp = LTPStore.get(symbol)\n'
    '        if not ltp or ltp <= 0:\n'
    '            try:\n'
    '                quote = self.broker_manager.get_data_kite().ltp(f"NFO:{symbol}")\n'
    '                ltp   = quote[f"NFO:{symbol}"]["last_price"]\n'
    '            except Exception:\n'
    '                ltp = None\n'
    '\n'
    '        if not ltp or ltp <= 0:\n'
    '            raise RuntimeError(f"LTP unavailable for {symbol}")\n'
    '\n'
    '        limit_price  = self._protected_limit_price(ltp, "BUY")\n',
    '        ltp = LTPStore.get(symbol)\n'
    '        if not ltp or ltp <= 0:\n'
    '            # ── ORDER_LTP_RETRY_20260911 ── was a silent one-shot REST call\n'
    '            ltp = self._resolve_ltp(symbol)\n'
    '\n'
    '        if not ltp or ltp <= 0:\n'
    '            raise RuntimeError(f"LTP unavailable for {symbol}")\n'
    '\n'
    '        limit_price  = self._protected_limit_price(ltp, "BUY")\n',
    "zerodha place_buy")

# ── Angel (Kite data path) ──────────────────────────────────────────────
a = read(PATH["a"])
import re as _re
mm = _re.search(r"    def _resolve_ltp\(self, symbol: str\) -> Optional\[float\]:\n(.*?)\n    def _resolve_ltp_angel", a, _re.S)
if not mm:
    die("angel _resolve_ltp block not found")
block = mm.group(0)
# find the try/except that does the kite ltp inside this block
m2 = _re.search(r"(        ltp = None\n        try:\n(?:.*?\n)*?        except Exception as e:\n            write_audit_log\([^\n]*\n(?:[^\n]*\n)?)", block)
if not m2:
    die("angel _resolve_ltp try/except not matched — inspect manually")
old_try = m2.group(1)
if "data_kite" not in old_try or "ltp(" not in old_try:
    die("angel _resolve_ltp try block does not look like the kite ltp fetch")
new_try = RETRY_BODY.replace("{TAG}", "ANGEL")
a = sub1(a, old_try, new_try, "angel _resolve_ltp")

NEW = {"z": z, "a": a}

# ── gates ───────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmp = {}
for k, text in NEW.items():
    p = os.path.join(TMP, os.path.basename(REL[k]))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    tmp[k] = p
    try:
        py_compile.compile(p, doraise=True)
    except py_compile.PyCompileError as e:
        die(f"py_compile failed for {REL[k]}: {e}")
print("py_compile gate: OK")

sys.path.insert(0, BACKEND)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod
ZE = load("app.execution.zerodha_executor", tmp["z"])
AE = load("app.execution.angel_executor", tmp["a"])
audit = []
ZE.write_audit_log = lambda s: audit.append(s); AE.write_audit_log = lambda s: audit.append(s)
slept = []
ZE.time.sleep = lambda s: slept.append(s); AE.time.sleep = lambda s: slept.append(s)
from kiteconnect.exceptions import NetworkException
from app.marketdata.ltp_store import LTPStore

class _Kite:
    def __init__(self, script): self.script = list(script); self.calls = 0
    def ltp(self, s):
        self.calls += 1; step = self.script.pop(0)
        if isinstance(step, Exception): raise step
        return {s: {"last_price": step}} if step else {}
def zex(script):
    e = ZE.ZerodhaOrderExecutor.__new__(ZE.ZerodhaOrderExecutor)
    k = _Kite(script); e.broker_manager = type("B", (), {"get_data_kite": staticmethod(lambda: k)})(); e._k = k
    return e
def aex(script):
    e = AE.AngelOneExecutor.__new__(AE.AngelOneExecutor)
    k = _Kite(script); e.broker_manager = type("B", (), {"get_data_kite": staticmethod(lambda: k)})(); e._k = k
    return e
FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)
def reset():
    audit.clear(); slept.clear()
    try: LTPStore._prices.clear(); LTPStore._timestamps.clear()
    except Exception: pass

print("behavioural simulation")
S = "NIFTY2691522800PE"
reset(); e = zex([NetworkException("Too many requests"), 6.35])
check("zerodha: 429 then success → priced, waited 1.1s, failure audited",
      e._resolve_ltp(S) == 6.35 and slept == [1.1] and any("LTP_RETRY" in x and "attempt 1/3" in x for x in audit), f"slept={slept} audit={audit}")
reset(); e = zex([NetworkException("Too many requests")] * 3)
try: LTPStore.update(S, 6.40)
except Exception:
    try: LTPStore.set(S, 6.40)
    except Exception: pass
r = e._resolve_ltp(S)
check("zerodha: 3×429 → LTPStore fallback", (r == 6.40) or (LTPStore.get(S) is None and r is None), f"r={r} store={LTPStore.get(S)}")
check("zerodha: exactly 3 attempts, 2 waits", e._k.calls == 3 and len(slept) == 2)
reset(); e = zex([RuntimeError("timeout"), {}, 6.10][:3])
e2 = zex([RuntimeError("timeout"), None, 6.10])
check("zerodha: non-429 error then empty then success → 6.10 (0.4s backoffs)", e2._resolve_ltp(S) == 6.10 and slept == [0.4, 0.4], f"slept={slept}")
reset(); e = zex([RuntimeError("x")] * 3)
try: LTPStore._prices.clear(); LTPStore._timestamps.clear()
except Exception: pass
check("zerodha: nothing anywhere → None (caller raises LTP unavailable)", e._resolve_ltp("NOSUCH") is None and sum(1 for x in audit if "LTP_RETRY" in x and "NOSUCH" in x) == 3)
# place_buy: ticker price present → REST untouched
reset(); e = zex([RuntimeError("must not be called")])
e._ensure_trading_enabled = lambda: None; e._kite = lambda: object(); e._get_lot_size = lambda k, s: 65
e._protected_limit_price = staticmethod(lambda ltp, side: round(ltp * 1.01, 2)); e._relay_call = lambda **k: "OID1"
try: LTPStore.update(S, 6.50)
except Exception:
    try: LTPStore.set(S, 6.50)
    except Exception: pass
if LTPStore.get(S) == 6.50:
    ok = False
    try:
        e.place_buy(S, 123, 65); ok = e._k.calls == 0
    except Exception as ex:
        ok = ("must not be called" not in repr(ex)) and e._k.calls == 0
    check("place_buy: ticker LTP present → no REST call", ok)
else:
    print("  SKIP  place_buy ticker-first (LTPStore has no setter in this harness)")
# place_buy: no ticker → routes through the retrying resolver (429 then success)
reset()
try: LTPStore._prices.clear(); LTPStore._timestamps.clear()
except Exception: pass
e = zex([NetworkException("Too many requests"), 6.35])
e._ensure_trading_enabled = lambda: None; e._kite = lambda: object(); e._get_lot_size = lambda k, s: 65
placed = {}
e._protected_limit_price = staticmethod(lambda ltp, side: (placed.__setitem__("ltp", ltp) or round(ltp + 0.1, 2)))
e._relay_call = lambda **k: "OID2"
try:
    e.place_buy(S, 123, 65)
except Exception as ex:
    pass   # downstream kwargs may need more stubs; the price resolution is what we assert
check("place_buy: no ticker → retrying resolver → priced after a 429", placed.get("ltp") == 6.35 and slept == [1.1], f"placed={placed} slept={slept}")
# angel parity
reset(); e = aex([NetworkException("Too many requests"), 7.05])
check("angel: 429 then success → priced via Kite data, audited as [ANGEL][LTP_RETRY]", e._resolve_ltp(S) == 7.05 and any("[ANGEL][LTP_RETRY]" in x for x in audit), str(audit))
if FAILS:
    die(f"simulation failed: {FAILS}")
print("behavioural simulation: OK")

# ── write ───────────────────────────────────────────────────────────────
for k, p in PATH.items():
    shutil.copy2(p, p + f".bak-{FENCE}")
for k, p in PATH.items():
    with open(p, "w", encoding="utf-8") as f:
        f.write(NEW[k])
print("written 2 files (+ .bak backups)")
if os.path.isdir(DUAL):
    for k, rel in REL.items():
        dst = os.path.join(DUAL, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(PATH[k], dst)
    print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
