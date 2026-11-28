# backend/app/utils/test_test_home_guard.py — ── TEST_HOME_GUARD_20261127 ── (DEF-113) TC-UTIL-0037…0044
#
# A test run never touches the real ~/.scalp-app (backend/app/__init__.py), and
# save_credentials is all or nothing.
#
# The guard cases start small probe scripts with HOME set to the account's REAL
# home (from pwd, whatever HOME this suite itself was given). The probes only
# import and print: none of them writes, so a broken guard fails a check and
# never harms the real app folder. The save checks run in this process and
# refuse to run unless CREDENTIALS_PATH is outside the real home.
#
#   cd backend && python3 app/utils/test_test_home_guard.py
#
# ── WEEKLY_SUITE_FIX_20261128 ── (DEF-119) what must stay untouched is the real ~/.scalp-app and the
# real HOME itself, not every folder under the home: the weekly Master Suite keeps
# each suite's TMPDIR and HOME in ~/scalp-regression/<run>/tmp, inside the home.
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND))

FAILS, PASSES = [], [0]


def check(name, cond, detail=""):
    if cond:
        PASSES[0] += 1
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name}  -> {str(detail)[:500]}")


try:
    import pwd
    REAL = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
except Exception:
    REAL = None
# ── WEEKLY_SUITE_FIX_20261128 ── the folder the guard protects (the app's data, credentials, logs)
REAL_APP = os.path.realpath(os.path.join(REAL, ".scalp-app")) if REAL else None

PROBE = r"""
import json, os, sys
if os.environ.get("PROBE_FROZEN"):
    sys.frozen = True
import app
from pathlib import Path
out = {"home": str(Path.home()), "env_home": os.environ.get("HOME"),
       "test_home": os.environ.get("SCALP_TEST_HOME"),
       "app_vars": {k: os.environ.get(k) for k in ("SCALP_APP_HOME", "DB_PATH", "SCALP_LOG_DIR")}}
if os.environ.get("PROBE_PATHS"):
    from app.utils import app_paths
    from app.config import zerodha_credentials_store as cs
    out["app_home"] = str(app_paths.APP_HOME)
    out["creds"] = str(cs.CREDENTIALS_PATH)
    out["seed"] = (Path.home() / "seed.txt").read_text() if (Path.home() / "seed.txt").exists() else None
print("PROBE" + json.dumps(out))
"""

TMP = Path(tempfile.mkdtemp(prefix="home_guard_"))


def probe(name, home, extra=None, drop=()):
    d = TMP / name.replace(".py", "")
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(PROBE)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SCALP_TEST_MODE", "SCALP_TEST_HOME", "PYTEST_CURRENT_TEST") + tuple(drop)}
    env.update(HOME=home, PYTHONPATH=str(BACKEND), **(extra or {}))
    r = subprocess.run([sys.executable, name], cwd=str(d), env=env, capture_output=True, text=True, timeout=120)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("PROBE")), None)
    return (json.loads(line[5:]) if line else None), r.stdout + r.stderr


def under(p, root):
    p = os.path.realpath(p)
    return p == root or p.startswith(root + os.sep)


print("TEST_HOME_GUARD — a test run never touches the real ~/.scalp-app")
if REAL is None:
    print("  (no pwd module here: the real-home cases need a POSIX machine — skipped)")
else:
    TC = "TC-UTIL-0037"
    out, log = probe("test_probe.py", REAL, {"PROBE_PATHS": "1"})
    ok = bool(out) and os.path.realpath(out["home"]) != REAL and out["test_home"] and \
        os.path.realpath(out["home"]) == os.path.realpath(out["test_home"]) and \
        not under(out["app_home"], REAL_APP) and not under(out["creds"], REAL_APP)
    check(f"{TC} a test_*.py run started with the real HOME gets a throw-away home before any app path "
          f"is worked out (credentials, app folder, Path.home())", ok, out or log)
    check(f"{TC} … and that throw-away home is removed when the run ends",
          bool(out) and not os.path.exists(out["test_home"] or "/nonexistent-x"), out)

    TC = "TC-UTIL-0038"
    out, log = probe("plain_probe.py", REAL, {"SCALP_TEST_MODE": "1"})
    check(f"{TC} SCALP_TEST_MODE=1 counts as a test run whatever the script is called",
          bool(out) and os.path.realpath(out["home"]) != REAL, out or log)

    TC = "TC-UTIL-0039"
    out, log = probe("run_live.py", REAL)
    check(f"{TC} a live run (no test mode, no pytest, not test_*.py) keeps the real home",
          bool(out) and os.path.realpath(out["home"]) == REAL and out["test_home"] is None, out or log)

    TC = "TC-UTIL-0040"
    out, log = probe("test_frozen.py", REAL, {"SCALP_TEST_MODE": "1", "PROBE_FROZEN": "1"})
    check(f"{TC} the packaged app (sys.frozen) is never moved, even with SCALP_TEST_MODE=1 set",
          bool(out) and os.path.realpath(out["home"]) == REAL and out["test_home"] is None, out or log)

    TC = "TC-UTIL-0042"
    # ── WEEKLY_SUITE_FIX_20261128 ── outside the home (TMP may be inside it); nothing creates it
    other = os.path.join(os.path.realpath("/tmp"), "scalp-home-guard-elsewhere", "logs")
    out, log = probe("test_vars.py", REAL, {"SCALP_APP_HOME": os.path.join(REAL, ".scalp-app"),
                                            "DB_PATH": os.path.join(REAL, ".scalp-app", "data", "app.db"),
                                            "SCALP_LOG_DIR": other})
    check(f"{TC} app path variables pointing into the real home are dropped; others are kept",
          bool(out) and out["app_vars"] == {"SCALP_APP_HOME": None, "DB_PATH": None, "SCALP_LOG_DIR": other},
          out or log)

TC = "TC-UTIL-0041"
seeded = TMP / "seeded_home"
seeded.mkdir()
(seeded / "seed.txt").write_text("fixture")
out, log = probe("test_seeded.py", str(seeded), {"PROBE_PATHS": "1"})
check(f"{TC} a HOME that is already not the real one (run_suites, a suite's own temp HOME) is kept, "
      f"with what was seeded in it", bool(out) and os.path.realpath(out["home"]) == os.path.realpath(seeded)
      and out["seed"] == "fixture" and out["test_home"] is None, out or log)

# ── save_credentials: all or nothing ─────────────────────────────────
from app.config import zerodha_credentials_store as CS   # noqa: E402

if REAL_APP is not None and under(str(CS.CREDENTIALS_PATH), REAL_APP):   # ── WEEKLY_SUITE_FIX_20261128 ──
    print(f"  REFUSING to test saves: {CS.CREDENTIALS_PATH} is in the real ~/.scalp-app — the guard did not run")
    FAILS.append("save checks not run: CREDENTIALS_PATH in the real ~/.scalp-app")
else:
    TC = "TC-UTIL-0043"
    CS.save_credentials(" key123 ", " sec456 ")
    data = json.loads(CS.CREDENTIALS_PATH.read_text())
    left = [p.name for p in CS.CREDENTIALS_PATH.parent.iterdir() if p.name != "credentials.json"
            and p.name.startswith("credentials.json")]
    mode = CS.CREDENTIALS_PATH.stat().st_mode & 0o777
    check(f"{TC} save_credentials writes the trimmed key and secret, readable only by the owner, "
          f"with no temp file left", data.get("api_key") == "key123" and data.get("api_secret") == "sec456"
          and not left and (os.name == "nt" or mode == 0o600), (data, left, oct(mode)))

    TC = "TC-UTIL-0044"
    before = CS.CREDENTIALS_PATH.read_bytes()
    real_replace = os.replace

    def boom(*a, **k):
        raise OSError("power cut")

    os.replace = boom
    try:
        CS.save_credentials("new", "new")
        raised = False
    except OSError:
        raised = True
    finally:
        os.replace = real_replace
    left = [p.name for p in CS.CREDENTIALS_PATH.parent.iterdir() if p.name != "credentials.json"
            and p.name.startswith("credentials.json")]
    check(f"{TC} a save that fails half-way keeps the old credentials whole and leaves no temp file",
          raised and CS.CREDENTIALS_PATH.read_bytes() == before and CS.load_credentials()["api_key"] == "key123"
          and not left, (raised, left))

import shutil   # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSES[0]} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
