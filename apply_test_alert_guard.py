#!/usr/bin/env python3
# apply_test_alert_guard.py — fence TEST_ALERT_GUARD_20261015
#
# The "CRITICAL ALERT · Timberwolf: SELL entry FAILED after the hedge filled.
# Hedge NIFTYX_H x650 unwound @4.00" messages were NOT Timberwolf. NIFTYX_H is
# the fake symbol in test_tma2_entry_paths.py: that suite calls the real
# _unwind_hedge(), which calls notify_critical(), and Telegram's config is
# read from ~/.scalp-app/telegram_config.json whatever SCALP_APP_HOME says —
# so every apply-script test gate run on the Mac paged the live group (two
# alerts per run). TMA_V2 in PAPER can never reach _unwind_hedge (LIVE entry
# path only).
#
# FIX
#   * NEW backend/app/utils/test_guard.py — outbound_blocked(): True only in
#     a test process (SCALP_TEST_MODE=1, a test_*.py script, or pytest).
#   * The three places a request leaves for api.telegram.org check it first:
#     telegram_api.send_telegram_message, telegram_summary_send._send_photo,
#     relay_deployer._send_relay_telegram. The app (scalp-backend, uvicorn)
#     matches none of the conditions — proven by a subprocess probe.
#   * test_tma2_entry_paths.py stubs notify_critical / record_alert and
#     asserts the unwind still raises its alert (into the stub).
#   * This script's own test runs export SCALP_TEST_MODE=1.
#
#  NEW   backend/app/utils/test_guard.py
#        backend/app/utils/test_test_alert_guard.py
#  EDIT  backend/app/api/telegram_api.py            (1)
#        backend/app/api/telegram_summary_send.py   (1)
#        backend/app/services/relay_deployer.py     (1)
#        backend/app/engine/tma2/test_tma2_entry_paths.py (2)
#  Mirrored to desktop/src-tauri/backend/ when that tree exists.
#
# Usage: python3 apply_test_alert_guard.py [--repo PATH] [--allow-dirty] [--skip-tests]
# Run ONLY this script.

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "TEST_ALERT_GUARD_20261015"
MIRRORS = {"backend/": "desktop/src-tauri/backend/", "frontend/": "desktop/src-tauri/frontend/"}
TG = "backend/app/api/telegram_api.py"
TS = "backend/app/api/telegram_summary_send.py"
RD = "backend/app/services/relay_deployer.py"
TT = "backend/app/engine/tma2/test_tma2_entry_paths.py"
GUARD = "backend/app/utils/test_guard.py"
GT = "backend/app/utils/test_test_alert_guard.py"
PREREQ = {TG: ["def send_telegram_message("], TS: ["def _send_photo("], RD: ["def _send_relay_telegram("]}
SUITES = ["app/utils/test_test_alert_guard.py", "app/engine/tma2/test_tma2_entry_paths.py"]

EDITS = [
    (TG, 'def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:\n',
     'def send_telegram_message(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:\n    # ── TEST_ALERT_GUARD_20261015 ── a test process never reaches Telegram\n    try:\n        from app.utils.test_guard import outbound_blocked\n        if outbound_blocked():\n            print("[TELEGRAM][TEST_GUARD] suppressed — test process")\n            return False\n    except Exception:\n        pass\n', 1),
    (TS, 'def _send_photo(bot_token: str, chat_id: str, png: bytes, caption: str) -> bool:\n',
     'def _send_photo(bot_token: str, chat_id: str, png: bytes, caption: str) -> bool:\n    # ── TEST_ALERT_GUARD_20261015 ── a test process never reaches Telegram\n    try:\n        from app.utils.test_guard import outbound_blocked\n        if outbound_blocked():\n            print("[TELEGRAM][TEST_GUARD] suppressed — test process")\n            return False\n    except Exception:\n        pass\n', 1),
    (RD, 'def _send_relay_telegram(message: str):\n',
     'def _send_relay_telegram(message: str):\n    # ── TEST_ALERT_GUARD_20261015 ── a test process never reaches Telegram\n    try:\n        from app.utils.test_guard import outbound_blocked\n        if outbound_blocked():\n            print("[TELEGRAM][TEST_GUARD] suppressed — test process")\n            return\n    except Exception:\n        pass\n', 1),
    (TT, '    m.executor = _FakeExec()\n    m._unwind_hedge("NIFTYX_H", 650, 4.0, cond="E2")   # NameError before\n',
     '    m.executor = _FakeExec()\n    # ── TEST_ALERT_GUARD_20261015 ── the unwind path raises a REAL critical\n    # alert (it paged the live Telegram group on 2026-10-15 as "Timberwolf …\n    # NIFTYX_H"). Stub both sinks; the path must still raise them.\n    _alerts = []\n    TM.notify_critical = lambda d: _alerts.append(d)\n    TM.record_alert = lambda *a, **k: None\n    m._unwind_hedge("NIFTYX_H", 650, 4.0, cond="E2")   # NameError before\n', 1),
    (TT, '    check("unwind cond defaults to E1 when unknown", d[0][0] == "E1")\n',
     '    check("unwind cond defaults to E1 when unknown", d[0][0] == "E1")\n    check("each unwind still raises its critical alert (into the stub)",\n          len(_alerts) == 2, str(_alerts))\n', 1),
]

NEW_FILES = {GUARD: '# backend/app/utils/test_guard.py\n#\n# ── TEST_ALERT_GUARD_20261015 ── a test process must never reach Telegram.\n#\n# 2026-10-15: four "CRITICAL ALERT · Timberwolf: SELL entry FAILED after the\n# hedge filled. Hedge NIFTYX_H x650 unwound" messages reached the live\n# Telegram group. NIFTYX_H is test_tma2_entry_paths.py\'s fake symbol: the\n# suite calls the real _unwind_hedge(), which calls notify_critical(), and the\n# Telegram config is read from ~/.scalp-app/telegram_config.json regardless of\n# SCALP_APP_HOME — so every apply-script test gate on the Mac paged the group.\n#\n# outbound_blocked() is checked at the three places a request leaves for\n# api.telegram.org. It is True only in a test process:\n#   * SCALP_TEST_MODE=1 in the environment, or\n#   * the running script is a test file (test_*.py), or pytest is loaded.\n# The app (scalp-backend binary, uvicorn, api_server) matches none of them.\n# Never raises; on any doubt it returns False (production keeps alerting).\n\nfrom __future__ import annotations\n\nimport os\nimport sys\n\n\ndef outbound_blocked() -> bool:\n    try:\n        if os.environ.get("SCALP_TEST_MODE") == "1":\n            return True\n        a0 = os.path.basename(str(sys.argv[0] if sys.argv else ""))\n        if a0.startswith("test_") or "pytest" in a0:\n            return True\n        if "pytest" in sys.modules or "_pytest" in sys.modules:\n            return True\n    except Exception:\n        return False\n    return False\n',
             GT: '# backend/app/utils/test_test_alert_guard.py — fence TEST_ALERT_GUARD_20261015\n#\n#   1. inside a test process all three Telegram send sites refuse (no HTTP)\n#   2. the APP still sends: a subprocess that looks like scalp-backend calls\n#      send_telegram_message and the request goes out (HTTP stubbed)\n#   3. SCALP_TEST_MODE=1 blocks even a non-test script\n#   4. test_tma2_entry_paths\' unwind still raises its alert — into a stub\n#\n# Run: cd backend && python3 app/utils/test_test_alert_guard.py\n\nfrom __future__ import annotations\n\nimport os\nimport subprocess\nimport sys\nimport tempfile\nfrom pathlib import Path\n\nBACKEND = Path(__file__).resolve().parents[2]\nif str(BACKEND) not in sys.path:\n    sys.path.insert(0, str(BACKEND))\nos.environ.setdefault("SCALP_APP_HOME", tempfile.mkdtemp())\n\nFAILS: list = []\nOK = [0]\n\n\ndef check(name, ok, note=""):\n    print(f"  {\'PASS\' if ok else \'FAIL\'}  {name}{(\'  — \' + str(note)) if (note and not ok) else \'\'}")\n    if ok:\n        OK[0] += 1\n    else:\n        FAILS.append(name)\n\n\nclass Req:\n    def __init__(self):\n        self.calls = 0\n\n    def post(self, *a, **k):\n        self.calls += 1\n\n        class R:\n            status_code, ok, text = 200, True, ""\n        return R()\n\n\ndef t_in_test():\n    print("── 1. inside a test process ──")\n    from app.utils.test_guard import outbound_blocked\n    check("outbound_blocked() is True when the script is test_*.py", outbound_blocked())\n    import app.api.telegram_api as T\n    import app.api.telegram_summary_send as S\n    import app.services.relay_deployer as RD\n    rq = Req()\n    T.requests, S.requests, RD.requests = rq, rq, rq\n    r1 = T.send_telegram_message("tok", "chat", "Timberwolf: SELL entry FAILED … NIFTYX_H")\n    r2 = S._send_photo("tok", "chat", b"png", "cap")\n    RD._get_telegram_credentials = lambda: ("tok", "chat")\n    RD._send_relay_telegram("relay down")\n    check("send_telegram_message: no HTTP request, returns False", rq.calls == 0 and r1 is False, (rq.calls, r1))\n    check("EOD card _send_photo: no HTTP request", rq.calls == 0 and r2 is False, (rq.calls, r2))\n    check("relay alert: no HTTP request", rq.calls == 0)\n\n\nPROBE = r"""\nimport sys\nsys.argv = [\'{argv0}\']\nsys.path.insert(0, {backend!r})\nimport app.api.telegram_api as T\nclass R:\n    status_code = 200\nclass Q:\n    n = 0\n    def post(self, *a, **k):\n        Q.n += 1\n        return R()\nT.requests = Q()\nok = T.send_telegram_message(\'tok\', \'chat\', \'hello\')\nprint(\'SENT\', Q.n, ok)\n"""\n\n\ndef probe(argv0, env_extra=None):\n    env = {k: v for k, v in os.environ.items() if k != "SCALP_TEST_MODE"}\n    env.update(env_extra or {})\n    r = subprocess.run([sys.executable, "-c", PROBE.format(argv0=argv0, backend=str(BACKEND))],\n                       capture_output=True, text=True, env=env, timeout=120)\n    line = [l for l in r.stdout.splitlines() if l.startswith("SENT")]\n    return line[-1] if line else (r.stdout[-300:] + r.stderr[-600:])\n\n\ndef t_app():\n    print("── 2/3. the app still alerts ──")\n    out = probe("scalp-backend")\n    check("as scalp-backend (frozen app): the request goes out", out == "SENT 1 True", out)\n    out = probe("uvicorn")\n    check("as uvicorn (dev run): the request goes out", out == "SENT 1 True", out)\n    out = probe("scalp-backend", {"SCALP_TEST_MODE": "1"})\n    check("SCALP_TEST_MODE=1 blocks even a non-test process", out == "SENT 0 False", out)\n\n\ndef t_tma2_suite():\n    print("── 4. test_tma2_entry_paths is hermetic ──")\n    s = (BACKEND / "app/engine/tma2/test_tma2_entry_paths.py").read_text(encoding="utf-8")\n    check("suite stubs notify_critical + record_alert before the unwind calls",\n          "TM.notify_critical = " in s and "TM.record_alert = " in s\n          and s.find("TM.notify_critical = ") < s.find(\'m._unwind_hedge("NIFTYX_H"\'))\n    check("…and asserts both unwinds still raise their alert (into the stub)",\n          "unwind still raises its critical alert" in s)\n\n\nif __name__ == "__main__":\n    t_in_test()\n    t_app()\n    t_tma2_suite()\n    if FAILS:\n        print(f"\\n{len(FAILS)} FAILURES: {FAILS}")\n        sys.exit(1)\n    print(f"\\nALL {OK[0]} TEST ALERT GUARD CHECKS PASSED")\n'}


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-esbuild", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        txt = p.read_text(encoding="utf-8")
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
        staged[rel] = txt
    for rel, fences in PREREQ.items():
        for f in fences:
            if f not in staged[rel]:
                die(f"prerequisite {f!r} not found in {rel}")
    for rel in NEW_FILES:
        if (repo / rel).exists():
            die(f"{rel} already exists — mixed state; remove it (or git checkout) and re-run")
    try:
        dirty = [r for r in targets if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)
    for rel in targets:
        if FENCE not in staged[rel]:
            die(f"internal: {rel} edited without the fence marker")
    with tempfile.TemporaryDirectory() as td:
        for rel, txt in [(r, staged[r]) for r in targets if r.endswith(".py")] + list(NEW_FILES.items()):
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print(f"  ✓ py_compile (edited + {len(NEW_FILES)} new)")
        jsx = [r for r in targets if r.endswith((".js", ".jsx"))]
        if jsx and not a.skip_esbuild:
            esb = None
            for c in (repo / "frontend/node_modules/.bin/esbuild", repo / "desktop/node_modules/.bin/esbuild"):
                if c.exists():
                    esb = str(c); break
            esb = esb or shutil.which("esbuild")
            if not esb:
                die("esbuild not found (frontend/node_modules/.bin) — run npm install, or --skip-esbuild")
            for rel in jsx:
                tp = Path(td) / ("x_" + Path(rel).name)
                tp.write_text(staged[rel], encoding="utf-8")
                r = subprocess.run([esb, str(tp), "--loader:.js=jsx", "--loader:.jsx=jsx", "--log-level=error"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"esbuild could not parse staged {rel}:\n{r.stderr[:2000]}")
            print(f"  ✓ esbuild parsed {len(jsx)} staged JSX file(s)")
    for rel in targets:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    for rel, txt in NEW_FILES.items():
        (repo / rel).write_text(txt, encoding="utf-8")
        print(f"  ✓ created {rel}")

    def rollback(why):
        for rel in targets:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        for rel in NEW_FILES:
            (repo / rel).unlink(missing_ok=True)
        die(why + " — every file restored")

    if not a.skip_tests:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", SCALP_TEST_MODE="1",
                   SCALP_APP_HOME=tempfile.mkdtemp(prefix="scalp_test_home_"),
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-c",
                            "import app.utils.test_guard, app.api.telegram_api, app.api.telegram_summary_send, app.services.relay_deployer; print('ok')"],
                           cwd=repo / "backend", capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            print(r.stderr[-3000:]); rollback("a touched module no longer imports")
        print("  ✓ test_guard + the three Telegram senders import")
        for suite in SUITES:
            if not (repo / "backend" / suite).exists():
                print(f"  ! {suite} not found — skipped")
                continue
            print(f"  … {Path(suite).name}")
            r = subprocess.run([sys.executable, suite], cwd=repo / "backend", capture_output=True, text=True,
                               env=env, timeout=600)
            last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            if r.returncode != 0 or "  FAIL  " in r.stdout:
                print(r.stdout[-4000:]); print(r.stderr[-2000:]); rollback(f"suite {suite} FAILED")
            print(f"  ✓ {Path(suite).name}: {last}")
    for rel in targets + list(NEW_FILES):
        for sp, dp in MIRRORS.items():
            if rel.startswith(sp) and (repo / dp).exists():
                dst = repo / (dp + rel[len(sp):])
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.copy2(dst, dst.with_name(dst.name + f".bak-{FENCE}"))
                shutil.copy2(repo / rel, dst)
                print(f"  ✓ mirrored → {dst.relative_to(repo)}")
    print(f"\n✓ {FENCE} applied. From now on a test run can never page the Telegram group;\n"
          f"  the app's own alerts are unaffected (subprocess probe as scalp-backend / uvicorn).")

if __name__ == "__main__":
    main()
