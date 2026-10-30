#!/usr/bin/env python3
"""
apply_acct_switch.py  ── ACCT_SWITCH_20261030 ──

The execution account chosen in Settings takes effect without a restart,
and an open position never changes account.

  * Bug: IC_V2 was set to Account 1 · Zerodha, but its 09:18 entry still went
    to Angel — IC/TSG/ORB/BRK/EMC/LVB/HA/BB runtimes pick the broker ONCE at
    launch. The per-session loops (VET/TMA/TMA_V2/SCALP_V3/V5) had the mirror
    bug: a switch while a position was carried sent its EXITS to the new account.
  * Fix: get_executor_for_strategy() returns one account-aware facade per
    bindable strategy (execution/strategy_executor.py). It moves to the newly
    selected account only when the strategy has NO open LIVE row in any live
    store AND no order went out in the last 10 minutes (so a multi-leg entry or
    an unwind can never straddle two accounts). Unsure → stays put. The account
    last used for orders is pinned to disk, so an overnight carry exits on the
    account that opened it even across a restart.
  * Bell notices: "now trades on …" when it moves; "switch waits — open position
    stays on …" when it can't yet. Settings' Save message says the same.

Run from the repo root:

    python3 apply_acct_switch.py --check
    python3 apply_acct_switch.py
    ./desktop/build-scalp.sh both      (non-trading hours)
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "ACCT_SWITCH_20261030"
PARENT = "ACC2 BEGIN"
PARENT_FILE = "backend/app/execution/executor_factory.py"
NEW_FILES = {'backend/app/execution/strategy_executor.py': '# backend/app/execution/strategy_executor.py\n#\n# ── ACCT_SWITCH_20261030 ── the execution account follows Settings without a\n# restart, and an open position never changes account.\n#\n# THE BUG (2026-10-30): IC_V2 was switched to Account 1 · Zerodha in Settings,\n# but its 09:18 entry still went to Angel — ic_runtime builds the executor\n# ONCE at launch and only ever replaces a None. TSG, ORB, BRK, EMC, LVB, HA,\n# BB_V1/V2 and IC_V1 do the same; nothing told the user a restart was needed.\n# The per-session loops (VET, TMA, TMA_V2, SCALP_V3/V5) had the mirror-image\n# bug: a binding changed while a position was carried would send its EXITS\n# to the new account.\n#\n# THE FIX: get_executor_for_strategy() returns one StrategyExecutor per\n# bindable strategy. It forwards every call to the broker router it holds,\n# and re-reads the binding before a call ONLY when it is safe to move:\n#\n#   * flat      — no open LIVE row for the strategy in any live store\n#                 (trades, paper_trades LIVE rows, scalp_v3/v5, tma, tma2, vet)\n#   * quiet     — no order-type call in the last STICKY_S seconds, so a\n#                 multi-leg entry or an unwind can never straddle two accounts\n#                 (IC books its rows only after ALL legs fill)\n#\n# Anything uncertain (DB locked, query error) counts as NOT flat → stay put.\n# The account last used for orders is pinned to disk, so a position carried\n# overnight on Angel still exits on Angel after a restart even if the binding\n# was flipped in between.\n\nfrom __future__ import annotations\n\nimport json\nimport os\nimport sqlite3\nimport tempfile\nimport threading\nimport time\nfrom pathlib import Path\nfrom typing import Callable, Dict, Optional\n\nFENCE = "ACCT_SWITCH_20261030"\n\nSTICKY_S = 600          # an entry/unwind sequence never splits across accounts\nFLAT_CACHE_S = 5        # DB checks at most every 5 s per strategy\nPIN_PATH = Path.home() / ".scalp-app" / "angelone" / "active_broker.json"\n\n_ORDER_PREFIXES = ("place_", "cancel_", "modify_", "exit_", "square", "flatten")\n\n_ACCOUNT_LABEL = {"ZERODHA": "Account 1 · Zerodha", "ANGELONE": "Account 2 · Angel One"}\n\n# Strategy-agnostic live stores (strategy column) and per-strategy tables.\n_SHARED_QUERIES = (\n    "SELECT COUNT(*) FROM trades WHERE strategy_id=? AND exit_time IS NULL",\n    "SELECT COUNT(*) FROM paper_trades WHERE strategy_name=? "\n    "AND UPPER(COALESCE(trade_mode,\'\'))=\'LIVE\' AND exit_price IS NULL",\n    "SELECT COUNT(*) FROM scalp_v3_trades WHERE strategy_name=? AND paper=0 AND exit_time IS NULL",\n    "SELECT COUNT(*) FROM scalpv5_trades WHERE strategy_name=? AND paper=0 AND exit_time IS NULL",\n)\n_OWN_TABLE = {"TMA_V1": "tma_trades", "TMA_V2": "tma2_trades", "VET_V1": "vet_trades"}\n\n\ndef _audit(msg: str) -> None:\n    try:\n        from app.event_bus.audit_logger import write_audit_log\n        write_audit_log(msg)\n    except Exception:\n        pass\n\n\ndef _alert(kind: str, msg: str, sid: str, severity: str = "info") -> None:\n    try:\n        from app.event_bus.inapp_events import record_alert\n        record_alert(kind, msg, severity=severity, strategy_id=sid)\n    except Exception:\n        pass\n\n\ndef _db_path() -> str:\n    try:\n        from app.engine.pst.pst_common import canonical_db_path\n        return str(canonical_db_path())\n    except Exception:\n        from app.db.sqlite import DB_PATH\n        return str(DB_PATH)\n\n\ndef open_live_rows(strategy_id: str, db_path: Optional[str] = None) -> Optional[int]:\n    """Open LIVE rows for the strategy across every live store.\n    None = could not tell (caller must treat as NOT flat)."""\n    path = db_path or _db_path()\n    total = 0\n    if not os.path.isfile(path):\n        _audit(f"[ACCT_SWITCH][{strategy_id}][DB] {path} not found — treating as not flat")\n        return None\n    try:\n        # plain connect (SELECTs only): a read-only URI open can fail on a WAL\n        # database whose -shm is absent, which would freeze switching forever.\n        conn = sqlite3.connect(path, timeout=5)\n    except Exception as e:\n        _audit(f"[ACCT_SWITCH][{strategy_id}][DB] open failed: {e!r} — treating as not flat")\n        return None\n    try:\n        queries = [(q, (strategy_id,)) for q in _SHARED_QUERIES]\n        t = _OWN_TABLE.get(strategy_id)\n        if t:\n            queries.append((f"SELECT COUNT(*) FROM {t} WHERE UPPER(mode)=\'LIVE\' AND status=\'OPEN\'", ()))\n        for q, args in queries:\n            try:\n                total += int(conn.execute(q, args).fetchone()[0] or 0)\n            except sqlite3.OperationalError as e:\n                if "no such table" in str(e) or "no such column" in str(e):\n                    continue            # store not present on this install\n                _audit(f"[ACCT_SWITCH][{strategy_id}][DB] {e!r} — treating as not flat")\n                return None\n        return total\n    except Exception as e:\n        _audit(f"[ACCT_SWITCH][{strategy_id}][DB] {e!r} — treating as not flat")\n        return None\n    finally:\n        try:\n            conn.close()\n        except Exception:\n            pass\n\n\n# ── pin file (account last used for orders, per strategy) ───────────────\n_PIN_LOCK = threading.Lock()\n\n\ndef read_pin(strategy_id: str, path: Path = None) -> Optional[str]:\n    p = path or PIN_PATH\n    try:\n        return (json.loads(p.read_text()) or {}).get(strategy_id)\n    except Exception:\n        return None\n\n\ndef write_pin(strategy_id: str, broker: str, path: Path = None) -> None:\n    p = path or PIN_PATH\n    with _PIN_LOCK:\n        try:\n            try:\n                data = json.loads(p.read_text()) or {}\n            except Exception:\n                data = {}\n            if data.get(strategy_id) == broker:\n                return\n            data[strategy_id] = broker\n            p.parent.mkdir(parents=True, exist_ok=True)\n            fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name)\n            with os.fdopen(fd, "w") as f:\n                json.dump(data, f, indent=2)\n                f.flush()\n                os.fsync(f.fileno())\n            os.replace(tmp, str(p))\n        except Exception as e:\n            _audit(f"[ACCT_SWITCH][{strategy_id}][PIN] write failed: {e!r}")\n\n\nclass StrategyExecutor:\n    """Account-aware facade over the broker ExecutionRouters for one strategy."""\n\n    def __init__(self, strategy_id: str, *,\n                 build: Callable[[str], object],\n                 resolve: Callable[[], str],\n                 is_open: Callable[[], Optional[int]] = None,\n                 clock: Callable[[], float] = time.time,\n                 pin_path: Path = None):\n        self._sid = strategy_id\n        self._build = build\n        self._resolve = resolve\n        self._is_open = is_open or (lambda: open_live_rows(strategy_id))\n        self._clock = clock\n        self._pin_path = pin_path\n        self._routers: Dict[str, object] = {}\n        self._lock = threading.RLock()\n        self._last_order = 0.0\n        self._flat_at = -1e18\n        self._flat_val: Optional[bool] = None\n        self._pending: Optional[str] = None\n\n        want = self._safe_resolve()\n        pin = read_pin(strategy_id, self._pin_path)\n        n = self._is_open()\n        if pin and pin != want and (n is None or n > 0):\n            self._broker = pin\n            self._pending = want\n            _audit(f"[ACCT_SWITCH][{strategy_id}][BOOT] open position on {pin} — staying on it; "\n                   f"{want} applies once flat")\n        else:\n            self._broker = want\n        self._router(self._broker)          # build eagerly: same failure semantics as before\n        _audit(f"[ACCT_SWITCH][{strategy_id}][BOOT] executor on {self._broker}")\n\n    # ── helpers ─────────────────────────────────────────────────────────\n    @property\n    def broker(self) -> str:\n        return self._broker\n\n    def _safe_resolve(self) -> str:\n        try:\n            return str(self._resolve() or "ZERODHA").upper()\n        except Exception as e:\n            _audit(f"[ACCT_SWITCH][{self._sid}] binding read failed: {e!r}")\n            return getattr(self, "_broker", None) or "ZERODHA"\n\n    def _router(self, broker: str):\n        r = self._routers.get(broker)\n        if r is None:\n            r = self._build(broker)\n            self._routers[broker] = r\n        return r\n\n    def _flat(self, now: float) -> Optional[bool]:\n        if now - self._flat_at < FLAT_CACHE_S:\n            return self._flat_val\n        n = self._is_open()\n        self._flat_val = None if n is None else (n == 0)\n        self._flat_at = now\n        return self._flat_val\n\n    def _select(self, order_call: bool):\n        with self._lock:\n            now = self._clock()\n            want = self._safe_resolve()\n            if want != self._broker:\n                quiet = (now - self._last_order) >= STICKY_S\n                if quiet and self._flat(now) is True:\n                    try:\n                        self._router(want)\n                    except Exception as e:\n                        _audit(f"[ACCT_SWITCH][{self._sid}] cannot build {want} executor "\n                               f"({e!r}) — staying on {self._broker}")\n                    else:\n                        old, self._broker, self._pending = self._broker, want, None\n                        _audit(f"[ACCT_SWITCH][{self._sid}] {old} → {want} (flat; from this call)")\n                        _alert("ACCOUNT_SWITCHED",\n                               f"{self._sid} now trades on {_ACCOUNT_LABEL.get(want, want)} "\n                               f"(was {_ACCOUNT_LABEL.get(old, old)})", self._sid)\n                elif self._pending != want:\n                    self._pending = want\n                    why = "an order sequence is in progress" if not quiet else "a LIVE position is open"\n                    _audit(f"[ACCT_SWITCH][{self._sid}][PENDING] {want} requested but {why} on "\n                           f"{self._broker} — staying until flat")\n                    _alert("ACCOUNT_SWITCH_PENDING",\n                           f"{self._sid}: switch to {_ACCOUNT_LABEL.get(want, want)} waits — "\n                           f"open position stays on {_ACCOUNT_LABEL.get(self._broker, self._broker)} "\n                           f"until it exits", self._sid)\n            elif self._pending:\n                self._pending = None\n            if order_call:\n                self._last_order = now\n                self._flat_at = -1e18        # re-check after any order activity\n                write_pin(self._sid, self._broker, self._pin_path)\n            return self._router(self._broker)\n\n    # ── forwarding ──────────────────────────────────────────────────────\n    def __getattr__(self, name):\n        if name.startswith("_"):\n            raise AttributeError(name)\n        attr = getattr(self._router(self._broker), name)\n        if not callable(attr):\n            return attr\n        order_call = name.startswith(_ORDER_PREFIXES)\n\n        def _forward(*args, **kwargs):\n            return getattr(self._select(order_call), name)(*args, **kwargs)\n        _forward.__name__ = name\n        return _forward\n\n    def __repr__(self):\n        return f"<StrategyExecutor {self._sid} on {self._broker}>"\n', 'backend/app/execution/test_strategy_executor.py': '# backend/app/execution/test_strategy_executor.py\n#\n# ── ACCT_SWITCH_20261030 ── behavioural suite: the execution account follows\n# Settings without a restart; an open position or an in-flight order\n# sequence never changes account. Hermetic: fake routers, temp DB, temp pin.\n#\n#   cd backend && PYTHONPATH=$PWD python3 app/execution/test_strategy_executor.py\n\nimport json\nimport sqlite3\nimport sys\nimport tempfile\nfrom pathlib import Path\n\nFAILS = []\n\n\ndef check(name, cond, detail=""):\n    print(f"  {\'PASS\' if cond else \'FAIL\'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))\n    if not cond:\n        FAILS.append(name)\n\n\nimport app.execution.strategy_executor as SE\n\nALERTS = []\nSE._alert = lambda kind, msg, sid, severity="info": ALERTS.append((kind, msg))\nTMP = Path(tempfile.mkdtemp(prefix="acct_switch_"))\n\n\nclass FakeRouter:\n    def __init__(self, broker):\n        self.broker_name = broker\n        self.calls = []\n        self.broker_manager = f"mgr-{broker}"\n\n    def place_buy(self, symbol, token, qty):\n        self.calls.append(("place_buy", symbol))\n        return f"{self.broker_name}-oid", 0.0, qty\n\n    def get_order_fill(self, oid):\n        self.calls.append(("get_order_fill", oid))\n        return {"status": "COMPLETE"}\n\n    def cancel_gtt(self, gid):\n        self.calls.append(("cancel_gtt", gid))\n        return True\n\n\nclass Rig:\n    """One strategy with a mutable binding, open-row count and clock."""\n    T0 = 1_761_800_000.0           # real epoch scale (TSG_BANK_FIX1 real-clock rule)\n\n    def __init__(self, binding="ANGELONE", open_rows=0, pin=None, fail_build=()):\n        self.binding = binding\n        self.open = open_rows\n        self.now = self.T0\n        self.routers = {}\n        self.fail_build = set(fail_build)\n        self.pin_path = TMP / f"pin_{id(self)}.json"\n        if pin:\n            self.pin_path.write_text(json.dumps({"IC_V2": pin}))\n        self.ex = SE.StrategyExecutor(\n            "IC_V2", build=self.build, resolve=lambda: self.binding,\n            is_open=lambda: self.open, clock=lambda: self.now, pin_path=self.pin_path)\n\n    def build(self, broker):\n        if broker in self.fail_build:\n            raise RuntimeError(f"{broker} not configured")\n        return self.routers.setdefault(broker, FakeRouter(broker))\n\n    def tick(self, s):\n        self.now += s\n\n    def pin(self):\n        try:\n            return json.loads(self.pin_path.read_text()).get("IC_V2")\n        except Exception:\n            return None\n\n\nprint("── 1. the 2026-10-30 bug: switched in Settings while flat → next entry follows")\nr = Rig(binding="ANGELONE")\ncheck("boot follows binding", r.ex.broker == "ANGELONE")\nr.binding = "ZERODHA"\noid = r.ex.place_buy("NIFTY25N0424000CE", 1, 65)[0]\ncheck("first entry order goes to Zerodha, no restart", oid == "ZERODHA-oid", oid)\ncheck("Angel saw no order", "ANGELONE" not in r.routers or not any(c[0] == "place_buy" for c in r.routers["ANGELONE"].calls))\ncheck("switch announced in the bell", any(k == "ACCOUNT_SWITCHED" for k, _ in ALERTS))\ncheck("pin follows the order", r.pin() == "ZERODHA")\n\nprint("── 2. open position never changes account")\nALERTS.clear()\nr = Rig(binding="ANGELONE")\nr.ex.place_buy("A", 1, 65)\nr.open = 4                               # condor booked\nr.tick(SE.STICKY_S + 60)\nr.binding = "ZERODHA"\nr.ex.cancel_gtt("g1")\ncheck("exit-side call stays on Angel", r.routers["ANGELONE"].calls[-1] == ("cancel_gtt", "g1"))\ncheck("pending alert raised once", sum(1 for k, _ in ALERTS if k == "ACCOUNT_SWITCH_PENDING") == 1)\nr.tick(10); r.ex.get_order_fill("x"); r.tick(10); r.ex.get_order_fill("y")\ncheck("no alert spam on repeated calls", sum(1 for k, _ in ALERTS if k == "ACCOUNT_SWITCH_PENDING") == 1)\nr.open = 0\nr.tick(SE.STICKY_S + 10)\nr.ex.place_buy("B", 1, 65)\ncheck("after the position exits, next entry goes to Zerodha", r.routers["ZERODHA"].calls[-1] == ("place_buy", "B"))\n\nprint("── 3. a multi-leg entry never straddles accounts (rows booked after all legs)")\nr = Rig(binding="ANGELONE")\nr.ex.place_buy("L3", 1, 65)              # wing filled; no DB rows yet (open=0)\nr.tick(2)\nr.binding = "ZERODHA"                    # user flips mid-entry\nr.ex.place_buy("L4", 1, 65)\ncheck("second leg stays on Angel inside the sticky window", r.routers["ANGELONE"].calls[-1] == ("place_buy", "L4"))\ncheck("Zerodha untouched", "ZERODHA" not in r.routers or not r.routers["ZERODHA"].calls)\nr.tick(SE.STICKY_S - 5)\nr.ex.get_order_fill("z")\ncheck("still Angel just before the window ends", r.ex.broker == "ANGELONE")\nr.tick(10)\nr.ex.get_order_fill("z2")\ncheck("switches once quiet and flat", r.ex.broker == "ZERODHA")\n\nprint("── 4. uncertain DB → stay put")\nr = Rig(binding="ANGELONE")\nr.open = None\nr.binding = "ZERODHA"\nr.tick(SE.STICKY_S + 1)\nr.ex.place_buy("Q", 1, 65)\ncheck("DB unknown never switches", r.ex.broker == "ANGELONE")\n\nprint("── 5. restart with an overnight carry")\nr = Rig(binding="ZERODHA", open_rows=2, pin="ANGELONE")\ncheck("boot keeps the pinned account while open", r.ex.broker == "ANGELONE")\nr.open = 0\nr.tick(SE.FLAT_CACHE_S + 1)\nr.ex.get_order_fill("p")\ncheck("moves to the binding once flat", r.ex.broker == "ZERODHA")\nr = Rig(binding="ZERODHA", open_rows=0, pin="ANGELONE")\ncheck("flat at boot → binding wins over a stale pin", r.ex.broker == "ZERODHA")\nr = Rig(binding="ZERODHA", open_rows=None, pin="ANGELONE")\ncheck("unknown at boot → pinned account (fail safe)", r.ex.broker == "ANGELONE")\n\nprint("── 6. build failure on the new account → stay on the old one")\nr = Rig(binding="ZERODHA", fail_build={"ANGELONE"})\nr.binding = "ANGELONE"\nr.tick(SE.STICKY_S + 1)\nr.ex.place_buy("F", 1, 65)\ncheck("order still routed to Zerodha", r.routers["ZERODHA"].calls[-1] == ("place_buy", "F"))\n\nprint("── 7. forwarding semantics")\nr = Rig(binding="ZERODHA")\ncheck("plain attribute passes through", r.ex.broker_manager == "mgr-ZERODHA")\ntry:\n    r.ex._nope\n    check("private attr raises", False)\nexcept AttributeError:\n    check("private attr raises", True)\ncheck("bound method name kept", r.ex.place_buy.__name__ == "place_buy")\ncheck("reads do not stamp the sticky window",\n      (r.ex.get_order_fill("x") is not None) and r.ex._last_order == 0.0)\n\nprint("── 8. default wall clock")\npp = TMP / "pin_wall.json"\nstate = {"b": "ANGELONE"}\nex = SE.StrategyExecutor("IC_V2", build=lambda b: FakeRouter(b), resolve=lambda: state["b"],\n                         is_open=lambda: 0, pin_path=pp)\nex.place_buy("W", 1, 65)\nstate["b"] = "ZERODHA"\nex.place_buy("W2", 1, 65)\ncheck("real time.time(): sticky window holds right after an order", ex.broker == "ANGELONE")\ncheck("sticky stamp is epoch-scale", ex._last_order > 1_600_000_000)\n\nprint("── 9. open_live_rows across every live store")\ndb = TMP / "app.db"\nwith sqlite3.connect(db) as c:\n    c.executescript("""\n    CREATE TABLE trades (trade_id TEXT, strategy_id TEXT, exit_time INTEGER);\n    CREATE TABLE paper_trades (paper_trade_id TEXT, strategy_name TEXT, trade_mode TEXT, exit_price REAL);\n    CREATE TABLE scalp_v3_trades (v3_trade_id TEXT, strategy_name TEXT, paper INTEGER, exit_time INTEGER);\n    CREATE TABLE tma2_trades (id INTEGER, mode TEXT, status TEXT);\n    INSERT INTO trades VALUES (\'a\',\'IC_V2\',NULL), (\'b\',\'IC_V2\',123), (\'c\',\'TSG_V1\',NULL);\n    INSERT INTO paper_trades VALUES (\'SHD:x\',\'IC_V2\',\'PAPER\',NULL), (\'p\',\'ORB_V1\',\'LIVE\',NULL),\n                                    (\'q\',\'ORB_V1\',\'PAPER\',NULL), (\'r\',\'ORB_V1\',\'LIVE\',101.0);\n    INSERT INTO scalp_v3_trades VALUES (\'v\',\'SCALP_V3\',0,NULL), (\'w\',\'SCALP_V3\',1,NULL);\n    INSERT INTO tma2_trades VALUES (1,\'LIVE\',\'OPEN\'), (2,\'PAPER\',\'OPEN\'), (3,\'LIVE\',\'CLOSED\'), (4,\'LIVE\',\'STALE\');\n    """)\ncheck("IC_V2: open trades row only (twin + closed ignored)", SE.open_live_rows("IC_V2", str(db)) == 1)\ncheck("ORB_V1: LIVE paper_trades row only", SE.open_live_rows("ORB_V1", str(db)) == 1)\ncheck("SCALP_V3: paper=0 rows only", SE.open_live_rows("SCALP_V3", str(db)) == 1)\ncheck("TMA_V2: LIVE + OPEN only", SE.open_live_rows("TMA_V2", str(db)) == 1)\ncheck("VET_V1: table absent → 0", SE.open_live_rows("VET_V1", str(db)) == 0)\ncheck("BRK_V1: nothing open → 0", SE.open_live_rows("BRK_V1", str(db)) == 0)\ncheck("unreadable DB → None", SE.open_live_rows("IC_V2", str(TMP / "missing" / "x.db")) is None)\nbad = TMP / "bad.db"\nbad.write_bytes(b"not a database at all" * 100)\ncheck("corrupt DB → None", SE.open_live_rows("IC_V2", str(bad)) is None)\n\nprint("── 10. factory: one facade per bindable strategy")\ntry:\n    import app.execution.executor_factory as EF\n    EF.get_executor_for_broker = lambda b: FakeRouter(b)\n    EF._strategy_executors.clear()\n    a1 = EF.get_executor_for_strategy("IC_V2")\n    a2 = EF.get_executor_for_strategy("IC_V2")\n    check("bindable → StrategyExecutor", isinstance(a1, SE.StrategyExecutor))\n    check("same instance for every caller", a1 is a2)\n    g = EF.get_executor_for_strategy("GC_V1")\n    check("non-bindable → plain router (unchanged)", isinstance(g, FakeRouter))\nexcept ModuleNotFoundError as e:\n    print(f"  SKIP  executor_factory import ({e}) — run on the build Mac")\n\nprint()\nif FAILS:\n    print(f"  {len(FAILS)} FAILED: {FAILS}")\n    sys.exit(1)\nprint("  ALL CHECKS PASSED")\n'}
EDITS = {'backend/app/execution/executor_factory.py': [('from app.config.account_bindings import resolve_broker\n# ACC2 END\n', 'from app.config.account_bindings import resolve_broker\n# ACC2 END\n# ============================================================\n\n# ── ACCT_SWITCH_20261030 ── one account-aware facade per bindable strategy:\n# follows the Settings binding without a restart, never while a LIVE\n# position is open or an order sequence is in flight.\nimport threading\nfrom app.execution.strategy_executor import StrategyExecutor\n_strategy_executors = {}\n_se_lock = threading.Lock()\n'), ('    broker = resolve_broker(strategy_id, registry_default)\n    return get_executor_for_broker(broker)\n', '    # ── ACCT_SWITCH_20261030 ── bindable strategies share ONE facade that\n    # re-reads the binding when it is safe (flat + quiet); see\n    # execution/strategy_executor.py. Non-bindable ids are unchanged.\n    from app.config.account_bindings import BINDABLE_STRATEGIES\n    if strategy_id in BINDABLE_STRATEGIES:\n        with _se_lock:\n            se = _strategy_executors.get(strategy_id)\n            if se is None:\n                se = StrategyExecutor(\n                    strategy_id,\n                    build=lambda b: get_executor_for_broker(b),\n                    resolve=lambda: resolve_broker(strategy_id, registry_default))\n                _strategy_executors[strategy_id] = se\n            return se\n\n    broker = resolve_broker(strategy_id, registry_default)\n    return get_executor_for_broker(broker)\n')], 'frontend/src/components/AccountSelector.jsx': [('      setMsg("Saved.");', '      // ── ACCT_SWITCH_20261030 ── no restart needed; open positions stay put\n      setMsg("Saved — applies from the next entry. An open position keeps exiting on the account it was opened on.");')]}

MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}


def die(msg):
    print(f"\n✗ {msg}\n  Nothing was written.")
    sys.exit(1)


def main():
    root = Path.cwd()
    check_only = "--check" in sys.argv
    allow_dirty = "--allow-dirty" in sys.argv
    if not (root / "backend/app").is_dir() or not (root / "frontend/src").is_dir():
        die("run this from the scalp-app repo root (backend/ and frontend/ not found)")

    # ── prerequisites ───────────────────────────────────────────────────
    pf = root / PARENT_FILE
    if not pf.exists() or PARENT not in pf.read_text():
        die(f"parent marker {PARENT!r} missing from {PARENT_FILE} (Account 2 wiring not present)")
    for rel in list(EDITS) + list(NEW_FILES):
        p = root / rel
        if p.exists() and FENCE in p.read_text():
            die(f"{FENCE} already present in {rel} — already applied")

    # ── working-copy drift guard (ORB_RECONCILE scar) ───────────────────
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--"] + list(EDITS),
                            capture_output=True, text=True, cwd=root, timeout=30)
        dirty = [l for l in st.stdout.splitlines() if l.strip()]
        if dirty and not allow_dirty:
            die("uncommitted changes in target files:\n    " + "\n    ".join(dirty)
                + "\n  Commit or stash them, or re-run with --allow-dirty if they are yours.")
    except FileNotFoundError:
        print("  ! git not found — skipping the working-copy check")

    # ── stage every change in memory; every anchor must match exactly once ─
    staged = {}
    for rel, pairs in EDITS.items():
        p = root / rel
        if not p.exists():
            die(f"target missing: {rel}")
        txt = p.read_text()
        for i, (old, new) in enumerate(pairs, 1):
            n = txt.count(old)
            if n != 1:
                die(f"{rel}: anchor {i} matched {n} times (expected 1) — the file differs "
                    f"from GitHub main. First line of the anchor:\n    {old.splitlines()[0]!r}")
            txt = txt.replace(old, new, 1)
        staged[rel] = txt
    for rel, txt in NEW_FILES.items():
        if (root / rel).exists():
            die(f"{rel} already exists — refusing to overwrite")
        staged[rel] = txt

    # ── py_compile gate on staged Python ────────────────────────────────
    tmpd = Path(tempfile.mkdtemp(prefix="acct_switch_pyc_"))
    try:
        for rel, txt in staged.items():
            if rel.endswith(".py"):
                f = tmpd / rel.replace("/", "__")
                f.write_text(txt)
                try:
                    py_compile.compile(str(f), doraise=True)
                except py_compile.PyCompileError as e:
                    die(f"py_compile failed for {rel}:\n{e}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    if check_only:
        print(f"✓ --check: {len(EDITS)} files patch cleanly, {len(NEW_FILES)} new files, py_compile OK.")
        return

    # ── write: backups first, then files, then mirrors ──────────────────
    written, backups = [], []

    def rollback():
        for rel in written:
            p = root / rel
            b = p.with_name(p.name + f".bak-{FENCE}")
            if b.exists():
                shutil.copy2(b, p)
                b.unlink()
            elif p.exists():
                p.unlink()

    try:
        for rel, txt in staged.items():
            p = root / rel
            if p.exists():
                b = p.with_name(p.name + f".bak-{FENCE}")
                shutil.copy2(p, b)
                backups.append(str(b))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt)
            written.append(rel)
    except Exception as e:
        rollback()
        die(f"write failed ({e!r}) — rolled back")

    # ── behavioural suites on the patched tree ──────────────────────────
    env = dict(os.environ, PYTHONPATH=str(root / "backend"))
    suites = ["app/execution/test_strategy_executor.py", "app/trading/test_fleet_modes.py",
              "app/trading/test_paper_lots.py"]
    for s in suites:
        if not (root / "backend" / s).exists():
            print(f"\n── {s}: not present on this tree — skipped")
            continue
        r = subprocess.run([sys.executable, s], cwd=root / "backend", env=env,
                           capture_output=True, text=True, timeout=300)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
        print(f"\n── {s}\n{tail}")
        if r.returncode != 0:
            rollback()
            die(f"{s} failed — every file rolled back")

    # ── JSX parse check (esbuild if present) ────────────────────────────
    esb = None
    for cand in ("frontend/node_modules/.bin/esbuild", "desktop/node_modules/.bin/esbuild",
                 "desktop/src-tauri/frontend/node_modules/.bin/esbuild"):
        if (root / cand).exists():
            esb = root / cand
            break
    jsx = [rel for rel in staged if rel.endswith(".jsx")]
    if esb:
        for rel in jsx:
            r = subprocess.run([str(esb), str(root / rel), "--loader:.jsx=jsx", "--log-level=error",
                                "--outfile=/dev/null"], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                rollback()
                die(f"esbuild could not parse {rel}:\n{r.stderr[-1500:]}\n  rolled back")
        print(f"\n✓ esbuild parsed {len(jsx)} JSX files")
    else:
        print("\n! esbuild not found — JSX is checked by the frontend build instead")

    # ── mirror into the desktop build trees, if present ─────────────────
    mirrored = 0
    for rel, txt in staged.items():
        for src, dst in MIRRORS.items():
            if rel.startswith(src):
                mp = root / (dst + rel[len(src):])
                if (root / dst).is_dir():
                    mp.parent.mkdir(parents=True, exist_ok=True)
                    mp.write_text(txt)
                    mirrored += 1

    print(f"\n✓ {FENCE} applied: {len(EDITS)} files patched, {len(NEW_FILES)} new"
          f"{f', {mirrored} build-tree copies' if mirrored else ''}.")
    print("  Backups: *.bak-" + FENCE)
    print("  Next:    ./desktop/build-scalp.sh both   (non-trading hours)")


if __name__ == "__main__":
    main()
