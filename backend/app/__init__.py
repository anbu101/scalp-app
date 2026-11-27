# backend/app/__init__.py — ── TEST_HOME_GUARD_20261127 ── (DEF-113)
#
# A test run never reads or writes the real ~/.scalp-app.
#
# Some suites write fake Zerodha keys / a fake Telegram config into the app
# folder (test_tg_personal_alerts.py, test_kite_login_nudge.py, …). They were
# safe only when the caller pointed HOME at a temp folder; run_suites.py does
# (DEF-094), apply scripts and hand runs did not, and the real
# ~/.scalp-app/zerodha/credentials.json became {"api_key": "k1", …}: the next
# Kite login failed with "Invalid api_key" (DEF-113).
#
# Every backend module lives under this package, so this runs before any of
# them works out a path. On a test run whose HOME is still the account's real
# home, HOME (and USERPROFILE) move to a throw-away folder, so Path.home() and
# every path built on it point there.
#
#   a test run  = SCALP_TEST_MODE=1, pytest loaded, or a script named test_*.py
#   never moved = the packaged app (PyInstaller: sys.frozen), or a HOME that is
#                 already not the real one (run_suites / mutation runners / a
#                 suite's own temp HOME stays as it is, with what it seeded)
import os as _os
import sys as _sys

_APP_PATH_VARS = ("SCALP_APP_HOME", "DB_PATH", "SCALP_DATA_DIR", "SCALP_LOG_DIR", "SCALP_STATE_DIR",
                  "SCALP_CONFIG_DIR", "SCALP_ZERODHA_DIR")


def _is_test_run() -> bool:
    if _os.environ.get("SCALP_TEST_MODE") == "1":
        return True
    main = _os.path.basename(_sys.argv[0]) if getattr(_sys, "argv", None) else ""
    return "pytest" in _sys.modules or main.startswith("test_")


def _real_home():
    """The account's own home folder, whatever HOME says (None if it cannot be told)."""
    try:
        import pwd
        return _os.path.realpath(pwd.getpwuid(_os.getuid()).pw_dir)
    except Exception:
        return None


def _home_is_real(home) -> bool:
    if not home:
        return True
    home = _os.path.realpath(home)
    real = _real_home()
    if real is not None:
        return home == real
    import tempfile                                   # Windows: no pwd — a temp folder counts as isolated
    tmp = _os.path.realpath(tempfile.gettempdir())
    return not (home == tmp or home.startswith(tmp + _os.sep))


def _guard_home():
    if getattr(_sys, "frozen", False) or not _is_test_run():
        return None
    home = _os.environ.get("USERPROFILE") if _os.name == "nt" else _os.environ.get("HOME")
    if not _home_is_real(home):
        return None
    import atexit
    import shutil
    import tempfile
    real = _os.path.realpath(home) if home else ""
    tmp = tempfile.mkdtemp(prefix="scalp-test-home-")
    _os.environ["HOME"] = tmp
    _os.environ["USERPROFILE"] = tmp
    for k in _APP_PATH_VARS:                          # a path into the real app folder would bypass HOME
        v = _os.environ.get(k)
        if v and real and _os.path.realpath(v).startswith(real + _os.sep):
            _os.environ.pop(k, None)
    _os.environ["SCALP_TEST_HOME"] = tmp
    atexit.register(shutil.rmtree, tmp, True)
    return tmp


_guard_home()
