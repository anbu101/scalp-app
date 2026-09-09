#!/usr/bin/env python3
"""
apply_SCALP_SKEW_QUOTE_20260909.py — FENCE: SCALP_SKEW_QUOTE_20260909

2026-09-09 14:10 — Scala (SCALP_V1, PAPER) raised the once-a-day skew-gate
INTERNAL ERROR alert: `NetworkException('Too many requests')` from kite.ltp
while quoting the ATM pair. Cause: the gate runs per SELL signal, in a thread
per symbol, and a candle close fires many symbols in the same second →
concurrent kite.ltp calls → Kite's ~1 req/s limit on the quote endpoint →
one blocked entry (fail-closed, by design) + the daily alert.

Fix A — one quote per burst. Every symbol in the same second needs the SAME
ATM pair, so the pair quote is now fetched behind a lock with a 2 s TTL cache:
the first thread hits Kite, the rest reuse the price. If Kite still answers
"Too many requests" (some other caller shares the budget), wait 1.1 s and
retry ONCE before giving up. Fail-closed behaviour on a genuine failure is
unchanged.

Fix B — PAPER stays off the phone. The skew-gate error alert now checks the
strategy's mode: PAPER → in-app bell (record_alert, once/day) + audit only;
LIVE → in-app + Telegram criticalAlert as before. The once-per-day guard is
shared so switching mode mid-day does not double-fire.

File: backend/app/marketdata/zerodha_tick_engine.py (fenced, anchored).
Safety: fence check, py_compile gate, behavioural tests on the patched module
(burst → one call; 429 → retry; stale cache → refetch; paper → no Telegram;
live → Telegram), .bak backup, dual-tree mirror. Backend-only → rebuild.

Run from the repo root:  python3 apply_SCALP_SKEW_QUOTE_20260909.py
"""
import importlib.util, os, py_compile, shutil, sys, tempfile, threading, time

FENCE = "SCALP_SKEW_QUOTE_20260909"
ROOT = os.path.abspath(os.getcwd())
REL = "app/marketdata/zerodha_tick_engine.py"
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
P = os.path.join(BACKEND, REL)
if not os.path.isfile(P):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


with open(P, encoding="utf-8") as f:
    src = f.read()
if FENCE in src:
    die(f"{FENCE} already present — nothing to do")

# ── Fix A: shared, cached, retrying pair quote ──────────────────────────
src = sub1(src,
    '    # ── SCALP_V1_SKEW_DFFIX_20260904 BEGIN ──────────────────────────────────────\n'
    '    def _skew_error_block(self, msg: str) -> bool:\n',
    '    # ── SCALP_SKEW_QUOTE_20260909 BEGIN ─────────────────────────────────────────\n'
    '    _SKEW_QUOTE_LOCK = threading.Lock()   # class-level: one gate per process\n'
    '    _SKEW_QUOTE_TTL_S = 2.0\n'
    '\n'
    '    def _skew_pair_quote(self, ce_sym: str, pe_sym: str):\n'
    '        """(ce_px, pe_px) for the ATM pair — ONE Kite call per burst.\n'
    '\n'
    '        A candle close fires the gate for many symbols in the same second,\n'
    '        each in its own thread, and they all need the same ATM pair. Serialise\n'
    '        behind a lock and reuse the price for TTL seconds so a burst of N\n'
    '        signals costs one request (Kite quote endpoint ≈ 1 req/s). On a\n'
    '        rate-limit reply, wait out the window once and retry; anything else\n'
    '        (or a second 429) propagates and the caller blocks fail-closed."""\n'
    '        key = (ce_sym, pe_sym)\n'
    '        with ZerodhaTickEngine._SKEW_QUOTE_LOCK:\n'
    '            c = getattr(self, "_skew_q_cache", None)\n'
    '            now = time.monotonic()\n'
    '            if c and c["key"] == key and now - c["ts"] < self._SKEW_QUOTE_TTL_S:\n'
    '                return c["px"]\n'
    '            syms = [f"NFO:{ce_sym}", f"NFO:{pe_sym}"]\n'
    '            try:\n'
    '                q = self.kite_data.ltp(syms)\n'
    '            except Exception as e:\n'
    '                if "too many requests" not in repr(e).lower():\n'
    '                    raise\n'
    '                write_audit_log("[SCALP_V1][SKEW] ATM quote rate-limited — "\n'
    '                                "retrying once after 1.1s")\n'
    '                time.sleep(1.1)\n'
    '                q = self.kite_data.ltp(syms)\n'
    '            px = (float(q[syms[0]]["last_price"]), float(q[syms[1]]["last_price"]))\n'
    '            self._skew_q_cache = {"key": key, "ts": time.monotonic(), "px": px}\n'
    '            return px\n'
    '    # ── SCALP_SKEW_QUOTE_20260909 END ───────────────────────────────────────────\n'
    '\n'
    '    # ── SCALP_V1_SKEW_DFFIX_20260904 BEGIN ──────────────────────────────────────\n'
    '    def _skew_error_block(self, msg: str) -> bool:\n',
    "pair-quote helper insert")

src = sub1(src,
    '        try:\n'
    '            q = self.kite_data.ltp([f"NFO:{ce_sym}", f"NFO:{pe_sym}"])\n'
    '            ce_px = float(q[f"NFO:{ce_sym}"]["last_price"])\n'
    '            pe_px = float(q[f"NFO:{pe_sym}"]["last_price"])\n'
    '        except Exception as e:\n',
    '        try:\n'
    '            # ── SCALP_SKEW_QUOTE_20260909 ── shared/cached/retrying\n'
    '            ce_px, pe_px = self._skew_pair_quote(ce_sym, pe_sym)\n'
    '        except Exception as e:\n',
    "quote call swap")

# ── Fix B: paper → in-app only ──────────────────────────────────────────
src = sub1(src,
    '            if getattr(self, "_skew_err_alert_date", None) != _today:\n'
    '                self._skew_err_alert_date = _today\n'
    '                from app.api.telegram_api import notify_critical\n'
    '                notify_critical({\n'
    '                    "message": (\n'
    '                        "SCALP_V1 ATM skew gate blocked an entry due to an "\n'
    '                        "INTERNAL ERROR (not the skew filter). Entries are "\n'
    '                        "being vetoed fail-closed — investigate today.\\n"\n'
    '                        f"First error: {msg}\\n"\n'
    '                        f"(counts today — allow: {stats[\'allow\']}, "\n'
    '                        f"filter blocks: {stats[\'block_filter\']}, "\n'
    '                        f"ERROR blocks: {stats[\'block_error\']}; one alert "\n'
    '                        "per day, details in audit log)"),\n'
    '                    "severity": "error",\n'
    '                    "strategy_id": "SCALP_V1",\n'
    '                })\n',
    '            if getattr(self, "_skew_err_alert_date", None) != _today:\n'
    '                self._skew_err_alert_date = _today\n'
    '                # ── SCALP_SKEW_QUOTE_20260909 ── PAPER: in-app bell only;\n'
    '                # LIVE: in-app + Telegram. Paper never pages the phone.\n'
    '                try:\n'
    '                    _mode = str(_strategy_mode("SCALP_V1") or "").upper()\n'
    '                except Exception:\n'
    '                    _mode = "LIVE"          # unknown → treat as live (loud)\n'
    '                _text = (\n'
    '                    "SCALP_V1 ATM skew gate blocked an entry due to an "\n'
    '                    "INTERNAL ERROR (not the skew filter). Entries are "\n'
    '                    "being vetoed fail-closed — investigate today.\\n"\n'
    '                    f"First error: {msg}\\n"\n'
    '                    f"(mode {_mode}; counts today — allow: {stats[\'allow\']}, "\n'
    '                    f"filter blocks: {stats[\'block_filter\']}, "\n'
    '                    f"ERROR blocks: {stats[\'block_error\']}; one alert "\n'
    '                    "per day, details in audit log)")\n'
    '                try:\n'
    '                    from app.event_bus.inapp_events import record_alert\n'
    '                    record_alert("SKEW_GATE_ERROR", _text, severity="error",\n'
    '                                 strategy_id="SCALP_V1", mode=_mode.lower())\n'
    '                except Exception as _ie:\n'
    '                    write_audit_log(f"[SCALP_V1][SKEW][INAPP_FAIL] {_ie!r}")\n'
    '                if _mode != "PAPER":\n'
    '                    from app.api.telegram_api import notify_critical\n'
    '                    notify_critical({"message": _text, "severity": "error",\n'
    '                                     "strategy_id": "SCALP_V1"})\n',
    "alert mode gate")

# ── py_compile gate ────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmpf = os.path.join(TMP, "zerodha_tick_engine.py")
with open(tmpf, "w", encoding="utf-8") as f:
    f.write(src)
try:
    py_compile.compile(tmpf, doraise=True)
except py_compile.PyCompileError as e:
    die(f"py_compile failed: {e}")
print("py_compile gate: OK")

# ── behavioural tests on the patched module ────────────────────────────
sys.path.insert(0, BACKEND)
spec = importlib.util.spec_from_file_location("app.marketdata.zerodha_tick_engine", tmpf)
M = importlib.util.module_from_spec(spec); sys.modules[spec.name] = M
spec.loader.exec_module(M)
Eng = M.ZerodhaTickEngine
audit = []
M.write_audit_log = lambda s: audit.append(s)

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)

class _Kite:
    def __init__(self, fail_first=0):
        self.calls, self.fail_first = 0, fail_first
    def ltp(self, syms):
        self.calls += 1
        if self.calls <= self.fail_first:
            from kiteconnect.exceptions import NetworkException
            raise NetworkException("Too many requests")
        return {syms[0]: {"last_price": 101.5}, syms[1]: {"last_price": 98.0}}

def mk(kite):
    e = Eng.__new__(Eng); e.kite_data = kite; return e

print("A — pair quote: burst / retry / TTL")
e = mk(_Kite()); out = []
ths = [threading.Thread(target=lambda: out.append(e._skew_pair_quote("CE1", "PE1"))) for _ in range(20)]
[t.start() for t in ths]; [t.join() for t in ths]
check("20 concurrent gate evaluations → ONE Kite call", e.kite_data.calls == 1 and len(out) == 20, f"calls={e.kite_data.calls}")
check("all threads got the same pair", all(o == (101.5, 98.0) for o in out))
e2 = mk(_Kite(fail_first=1)); M.time.sleep = lambda s: None
check("429 → retried once and succeeded", e2._skew_pair_quote("CE1", "PE1") == (101.5, 98.0) and e2.kite_data.calls == 2)
check("retry audited", any("rate-limited" in a for a in audit))
e3 = mk(_Kite(fail_first=2))
try:
    e3._skew_pair_quote("CE1", "PE1"); check("second 429 propagates (fail-closed)", False)
except Exception as ex:
    check("second 429 propagates (fail-closed)", "Too many requests" in repr(ex) and e3.kite_data.calls == 2)
e4 = mk(_Kite())
class _Bad(Exception): pass
e4.kite_data.ltp = lambda s: (_ for _ in ()).throw(_Bad("boom"))
try:
    e4._skew_pair_quote("CE1", "PE1"); check("non-429 error propagates untouched", False)
except _Bad:
    check("non-429 error propagates untouched", True)
e5 = mk(_Kite()); e5._skew_pair_quote("CE1", "PE1")
e5._skew_q_cache["ts"] -= 5           # age the cache past TTL
e5._skew_pair_quote("CE1", "PE1")
check("stale cache (>2s) → refetch", e5.kite_data.calls == 2)
e5._skew_pair_quote("CE2", "PE2")
check("different ATM pair → refetch", e5.kite_data.calls == 3)

print("B — alert routing by mode")
import app.event_bus.inapp_events as IE
import app.api.telegram_api as TG
inapp, tg = [], []
IE.record_alert = lambda code, msg, **k: inapp.append((code, k.get("mode")))
TG.notify_critical = lambda d: tg.append(d)
M._strategy_mode = lambda sid: "PAPER"
e6 = mk(_Kite())
r = e6._skew_error_block("[SCALP_V1][SKEW] ATM quote failed (x) — BLOCKED S")
check("paper: returns False (still fail-closed)", r is False)
check("paper: in-app alert recorded, NO Telegram", inapp == [("SKEW_GATE_ERROR", "paper")] and tg == [], f"inapp={inapp} tg={tg}")
e6._skew_error_block("second error same day")
check("paper: once per day", len(inapp) == 1)
inapp.clear(); tg.clear()
M._strategy_mode = lambda sid: "LIVE"
e7 = mk(_Kite()); e7._skew_error_block("[SCALP_V1][SKEW] ATM quote failed (x) — BLOCKED S")
check("live: in-app AND Telegram", len(inapp) == 1 and len(tg) == 1 and tg[0]["strategy_id"] == "SCALP_V1", f"inapp={inapp} tg={tg}")
check("live: message states mode + counts", "mode LIVE" in tg[0]["message"] and "ERROR blocks: 1" in tg[0]["message"])
inapp.clear(); tg.clear()
M._strategy_mode = lambda sid: (_ for _ in ()).throw(RuntimeError("cfg"))
e8 = mk(_Kite()); e8._skew_error_block("err")
check("mode lookup failure → treated as live (loud, not silent)", len(tg) == 1)

if FAILS:
    die(f"tests failed: {FAILS}")
print("behavioural tests: OK")

# ── write ───────────────────────────────────────────────────────────────
shutil.copy2(P, P + f".bak-{FENCE}")
with open(P, "w", encoding="utf-8") as f:
    f.write(src)
print(f"written {REL} (+ .bak-{FENCE})")
if os.path.isdir(DUAL):
    shutil.copy2(P, os.path.join(DUAL, REL)); print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
