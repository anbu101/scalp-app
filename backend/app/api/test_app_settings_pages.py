"""
test_app_settings_pages.py — PAGE_VISIBILITY_20261117 (TD-020)

Backtest / Crypto Lab / TV Alerts are OFF by default for every user and are
switched on in App Settings → Pages (settings.pages in app_settings.json).

Run:  cd backend && PYTHONPATH=. python3 app/api/test_app_settings_pages.py
Throw-away HOME; drives the route handlers directly (no TestClient).
"""
import asyncio, json, os, sys, tempfile

_home = tempfile.mkdtemp(prefix="pages_home_")
os.environ["HOME"] = _home

import app.api.app_settings_api as api   # noqa: E402  (HOME must be set first)
from pathlib import Path                 # noqa: E402

api.SETTINGS_FILE = Path(_home) / ".scalp-app" / "app_settings.json"
OFF = {"backtest": False, "crypto_lab": False, "tv_alerts": False}
fails = []


def check(tc, cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + f"{tc}  {msg}")
    if not cond:
        fails.append(tc)


def get():
    return asyncio.run(api.get_app_settings())


def post(body):
    return asyncio.run(api.save_app_settings(api.AppSettings(**body)))


def write_file(d):
    api.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    api.SETTINGS_FILE.write_text(json.dumps(d))


# TC-PAGES-0001 fresh install → all three pages off
if api.SETTINGS_FILE.exists():
    api.SETTINGS_FILE.unlink()
check("TC-PAGES-0001", get()["pages"] == OFF, "no settings file → Backtest, Crypto Lab, TV Alerts all off")

# TC-PAGES-0002 existing file from before this change (no 'pages' key) → all off
write_file({"notify_toast": True, "theme": "kolam", "dashboard_layout": "legacy"})
g = get()
check("TC-PAGES-0002", g["pages"] == OFF and g["theme"] == "kolam" and g["dashboard_layout"] == "legacy",
      "settings file without 'pages' → all off, other settings kept")

# TC-PAGES-0003 switching one page on persists only that page
r = post({**g, "pages": {"backtest": True}})
g = get()
check("TC-PAGES-0003", r["success"] and g["pages"] == {**OFF, "backtest": True}
      and json.loads(api.SETTINGS_FILE.read_text())["pages"]["backtest"] is True,
      "Backtest on → saved to app_settings.json; Crypto Lab and TV Alerts stay off")

# TC-PAGES-0004 switching it off again
post({**g, "pages": {**g["pages"], "backtest": False}})
check("TC-PAGES-0004", get()["pages"] == OFF, "Backtest off again → all off")

# TC-PAGES-0005 a save from an older frontend (no 'pages' in the body) keeps the stored pages
post({**get(), "pages": {"tv_alerts": True, "crypto_lab": True}})
old_body = {k: v for k, v in get().items() if k != "pages"}
post({**old_body, "theme": "arcade"})
g = get()
check("TC-PAGES-0005", g["pages"] == {**OFF, "tv_alerts": True, "crypto_lab": True} and g["theme"] == "arcade",
      "save without 'pages' (older client) → pages unchanged, theme saved")

# TC-PAGES-0006/0007 junk in the file → off; unknown keys dropped
write_file({"pages": {"backtest": "yes", "crypto_lab": 1, "tv_alerts": None, "levels": True}})
check("TC-PAGES-0006", get()["pages"] == OFF,
      "non-boolean values and unknown page keys → off / dropped")
write_file({"pages": ["backtest"]})
check("TC-PAGES-0007", get()["pages"] == OFF, "'pages' not an object → all off")

print(f"\n{'FAIL' if fails else 'OK'}: {7 - len(fails)}/7 checks")
sys.exit(1 if fails else 0)
