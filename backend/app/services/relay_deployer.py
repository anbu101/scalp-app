# backend/app/services/relay_deployer.py
"""
Relay Deployer
==============
SSHes into the user's OCI instance and deploys the order relay
service automatically. Called from relay_routes.py when the user
clicks "Deploy Relay" in the Connections UI.

No manual steps needed on the OCI side after this runs.
"""

import io
import os
import json
import secrets
import time
import threading
from pathlib import Path
from typing import Tuple, Optional

from app.event_bus.audit_logger import write_audit_log

# Relay config lives next to all other app state
RELAY_CONFIG_PATH = Path.home() / ".scalp-app" / "relay_config.json"

# --------------------------------------------------
# RELAY STATE TRACKING  (in-memory, module-level)
# Tracks last known relay health to detect transitions
# and avoid flooding Telegram on every poll cycle.
# --------------------------------------------------

_last_relay_active: Optional[bool] = None   # None = never checked yet
_state_lock = threading.Lock()

# The relay service source — embedded here so the backend can
# upload it to OCI without needing a separate file on disk.
RELAY_SERVICE_SOURCE = """
#!/usr/bin/env python3
# oci_order_relay.py — Scalp Terminal Order Relay
# Pure stdlib HTTP server. No FastAPI, no uvicorn, no gunicorn.
# Each request runs in its own OS thread.
# Requests socket timeout enforced globally — threads always exit cleanly.

import http.server
import socketserver
import json
import hmac
import os
import sys
import logging
import time
import urllib.parse

# ── Enforce (5s connect, 20s read) on ALL outbound HTTP calls ──────────
# Patched before kiteconnect import so every KiteConnect call is covered.
# Socket-level timeout: OS closes connection cleanly on deadline.
import requests as _req
_orig = _req.Session.request
def _timed(self, method, url, **kw):
    if kw.get("timeout") is None:
        kw["timeout"] = (5, 20)
    return _orig(self, method, url, **kw)
_req.Session.request = _timed

from kiteconnect import KiteConnect

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
if not RELAY_SECRET:
    sys.exit("RELAY_SECRET not set")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("relay")


def _verify(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:].strip(), RELAY_SECRET)


def _kite(api_key, access_token):
    k = KiteConnect(api_key=api_key)
    k.set_access_token(access_token)
    return k


class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress default per-request logs

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        if self.path == "/health":
            # Direct response — no thread dispatch, no kiteconnect.
            # Always responds as long as the process is alive.
            self._json(200, {
                "status": "ok",
                "relay": "scalp-terminal",
                "ts": int(time.time()),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not _verify(self.headers):
            self._json(403, {"error": "forbidden"})
            return
        try:
            body = self._body()
        except Exception as e:
            self._json(400, {"error": f"bad request: {e}"})
            return

        api_key      = body.get("api_key", "")
        access_token = body.get("access_token", "")
        if not api_key or not access_token:
            self._json(400, {"error": "missing credentials"})
            return

        k    = _kite(api_key, access_token)
        path = self.path

        try:
            if path == "/relay/place_order":
                kw = {f: body[f] for f in (
                    "variety", "exchange", "tradingsymbol",
                    "transaction_type", "quantity", "order_type", "product"
                )}
                for opt in ("price", "trigger_price", "tag"):
                    if body.get(opt) is not None:
                        kw[opt] = body[opt]
                oid = k.place_order(**kw)
                log.info("[ORDER] %s %s qty=%s id=%s",
                         body.get("tradingsymbol"), body.get("transaction_type"),
                         body.get("quantity"), oid)
                self._json(200, {"order_id": oid})

            elif path == "/relay/place_gtt":
                res = k.place_gtt(
                    trigger_type=body["trigger_type"],
                    tradingsymbol=body["tradingsymbol"],
                    exchange=body["exchange"],
                    trigger_values=body["trigger_values"],
                    last_price=body["last_price"],
                    orders=body["orders"],
                )
                tid = res.get("trigger_id", res) if isinstance(res, dict) else res
                log.info("[GTT] %s gtt_id=%s", body.get("tradingsymbol"), tid)
                self._json(200, {"trigger_id": tid})

            elif path == "/relay/cancel_order":
                k.cancel_order(variety=body["variety"], order_id=body["order_id"])
                self._json(200, {"cancelled": body["order_id"]})

            else:
                self._json(404, {"error": "unknown endpoint"})

        except Exception as e:
            log.error("[ERROR] %s: %s", path, e)
            self._json(500, {"error": str(e)})

    def do_DELETE(self):
        if not _verify(self.headers):
            self._json(403, {"error": "forbidden"})
            return
        if not self.path.startswith("/relay/gtt/"):
            self._json(404, {"error": "not found"})
            return
        try:
            raw = self.path.split("?", 1)
            tid = int(raw[0].split("/")[-1])
            qs  = urllib.parse.parse_qs(raw[1]) if len(raw) > 1 else {}
            _kite(
                qs.get("api_key", [""])[0],
                qs.get("access_token", [""])[0],
            ).delete_gtt(tid)
            self._json(200, {"cancelled": tid})
        except Exception as e:
            self._json(500, {"error": str(e)})


class ThreadingRelay(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads      = True   # threads don't block clean shutdown
    allow_reuse_address = True   # fast port reuse after restart


if __name__ == "__main__":
    server = ThreadingRelay(("0.0.0.0", 8001), Handler)
    log.info("Scalp relay listening on 0.0.0.0:8001")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
"""


# --------------------------------------------------
# RELAY CONFIG CACHE  (module-level)
# --------------------------------------------------

_relay_cfg: Optional[dict] = None   # None = not yet loaded; False = loaded but absent/disabled


def _load_relay_config() -> Optional[dict]:
    global _relay_cfg

    if _relay_cfg is not None:
        return _relay_cfg if _relay_cfg else None

    if not RELAY_CONFIG_PATH.exists():
        _relay_cfg = False
        return None

    try:
        cfg = json.loads(RELAY_CONFIG_PATH.read_text())
        if cfg.get("enabled") and cfg.get("url") and cfg.get("secret"):
            _relay_cfg = cfg
            write_audit_log(f"[RELAY] Order relay ENABLED → {cfg['url']}")
            return _relay_cfg
        else:
            write_audit_log("[RELAY] relay_config.json present but disabled or incomplete")
            _relay_cfg = False
            return None
    except Exception as e:
        write_audit_log(f"[RELAY] Failed to load relay_config.json ERR={e}")
        _relay_cfg = False
        return None


def _invalidate_relay_cache():
    """
    Called by relay_deployer after writing a new relay_config.json
    so the executor picks it up without restarting the backend.
    """
    global _relay_cfg
    _relay_cfg = None
    write_audit_log("[RELAY] Config cache invalidated — will reload on next order")


# --------------------------------------------------
# HEALTH CHECK  (with retries)
# --------------------------------------------------

_HEALTH_RETRY_COUNT   = 3
_HEALTH_RETRY_DELAY_S = 5    # seconds between retries
_HEALTH_TIMEOUT_S     = 6    # per-request timeout


def _check_relay_health_with_retry(url: str) -> bool:
    """
    Attempt the relay health endpoint up to _HEALTH_RETRY_COUNT times
    with _HEALTH_RETRY_DELAY_S between attempts.

    Returns True only if at least one attempt succeeds.
    This eliminates false-positive "Unreachable" reports caused by
    transient network blips.
    """
    import requests

    for attempt in range(1, _HEALTH_RETRY_COUNT + 1):
        try:
            resp = requests.get(
                f"{url}/health",
                timeout=_HEALTH_TIMEOUT_S,
            )
            if resp.status_code == 200:
                body = resp.json()
                relay_ok = (
                    body.get("relay") == "scalp-terminal"
                    or body.get("status") == "ok"
                )
                if relay_ok:
                    if attempt > 1:
                        write_audit_log(
                            f"[RELAY] Health check passed on attempt {attempt}/{_HEALTH_RETRY_COUNT}"
                        )
                    return True
            elif resp.status_code == 503:
                write_audit_log(
                    f"[RELAY] Health check attempt {attempt} — relay returned 503 "
                    f"(thread pool exhausted)"
                )
        except Exception as e:
            write_audit_log(
                f"[RELAY] Health check attempt {attempt}/{_HEALTH_RETRY_COUNT} failed: {e}"
            )

        if attempt < _HEALTH_RETRY_COUNT:
            time.sleep(_HEALTH_RETRY_DELAY_S)

    return False


# --------------------------------------------------
# TELEGRAM HELPERS
# --------------------------------------------------

def _get_telegram_credentials() -> tuple:
    """
    Read Telegram bot_token and chat_id, trying every known source in order:

      1. Module-level TELEGRAM_CONFIG from telegram_api (fastest, already in memory)
      2. Config file at ~/.scalp-app/telegram_config.json
      3. Config file at ~/.scalp-app/config/telegram_config.json

    We try the in-memory cache first so this works after a normal startup.
    We always fall back to disk so it works even when the cache is stale or
    None (e.g. during the relay monitor startup window).

    Returns (bot_token, chat_id) or (None, None) if not configured.
    """
    # Source 1: in-memory cache from telegram_api module
    try:
        from app.api.telegram_api import TELEGRAM_CONFIG
        if TELEGRAM_CONFIG:
            bot_token = TELEGRAM_CONFIG.get("bot_token", "").strip()
            chat_id   = TELEGRAM_CONFIG.get("chat_id", "").strip()
            if bot_token and chat_id:
                return bot_token, chat_id
    except Exception:
        pass

    # Source 2 & 3: read from disk
    try:
        from app.utils.app_paths import APP_HOME
        candidate_paths = [
            APP_HOME / "telegram_config.json",
            APP_HOME / "config" / "telegram_config.json",
        ]

        for tg_path in candidate_paths:
            if not tg_path.exists():
                continue
            try:
                cfg       = json.loads(tg_path.read_text())
                bot_token = cfg.get("bot_token", "").strip()
                chat_id   = cfg.get("chat_id", "").strip()
                if bot_token and chat_id:
                    write_audit_log(
                        f"[RELAY] Telegram credentials loaded from {tg_path.name}"
                    )
                    return bot_token, chat_id
            except Exception as e:
                write_audit_log(f"[RELAY] Failed to parse {tg_path}: {e}")
                continue

    except Exception as e:
        write_audit_log(f"[RELAY] Failed to read Telegram config from disk: {e}")

    write_audit_log(
        "[RELAY] Telegram credentials not found in any source — cannot send alert"
    )
    return None, None


def _send_relay_telegram(message: str):
    """
    Send a Telegram message directly via the Bot API.
    Does NOT go through notify_system_alert or any filter layer.
    """
    import requests as _requests

    bot_token, chat_id = _get_telegram_credentials()
    if not bot_token or not chat_id:
        return

    try:
        resp = _requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if resp.ok:
            write_audit_log("[RELAY] Telegram message delivered successfully")
        else:
            write_audit_log(
                f"[RELAY] Telegram API returned {resp.status_code}: {resp.text[:200]}"
            )
    except Exception as e:
        write_audit_log(f"[RELAY] Telegram send failed: {e}")


def _notify_relay_down(host: str):
    """Fire a Telegram alert when relay transitions Active → Unreachable."""
    write_audit_log("[RELAY] Sending relay-down Telegram alert...")
    _send_relay_telegram(
        f"🔴 <b>Order Relay Unreachable</b>\n\n"
        f"Host: <code>{host}</code>\n"
        f"Orders will fail until the relay is restored.\n\n"
        f"<b>Checklist:</b>\n"
        f"• Log into OCI console and verify instance is Running\n"
        f"• If stopped, restart it — the relay service will auto-start\n"
        f"• Check Connections page and click Redeploy if needed"
    )
    write_audit_log("[RELAY] Telegram alert sent — relay down")


def _notify_relay_recovered(host: str):
    """Fire a Telegram alert when relay transitions Unreachable → Active."""
    write_audit_log("[RELAY] Sending relay-recovered Telegram alert...")
    _send_relay_telegram(
        f"✅ <b>Order Relay Recovered</b>\n\n"
        f"Host: <code>{host}</code>\n"
        f"Relay is healthy again — orders will route normally."
    )
    write_audit_log("[RELAY] Telegram alert sent — relay recovered")


# --------------------------------------------------
# TRANSITION TRACKER
# --------------------------------------------------

def _handle_health_result(is_active: bool, host: str):
    """
    Compare current health result against last known state.
    Fire Telegram notification only when the state actually changes.
    Skips notification on the very first check (startup) to avoid
    a spurious alert if the relay is already down before the app starts.
    """
    global _last_relay_active

    with _state_lock:
        previous = _last_relay_active
        _last_relay_active = is_active

    if previous is None:
        # First check after startup — establish baseline, no alert
        write_audit_log(
            f"[RELAY_MONITOR] Initial state established: "
            f"{'active' if is_active else 'unreachable'} host={host}"
        )
        return

    if previous is True and not is_active:
        # Transition: Active → Unreachable
        write_audit_log(f"[RELAY_MONITOR] TRANSITION: active → unreachable host={host}")
        _notify_relay_down(host)

    elif previous is False and is_active:
        # Transition: Unreachable → Active
        write_audit_log(f"[RELAY_MONITOR] TRANSITION: unreachable → active host={host}")
        _notify_relay_recovered(host)


# --------------------------------------------------
# BACKGROUND MONITOR
# --------------------------------------------------

MONITOR_INTERVAL_S = 60    # check every minute


def start_relay_monitor():
    """
    Starts a daemon thread that periodically checks relay health
    and fires Telegram notifications on state transitions.

    Safe to call at startup even when no relay is configured —
    the thread will simply sleep and check again.
    """
    t = threading.Thread(
        target=_relay_monitor_loop,
        daemon=True,
        name="RelayMonitor",
    )
    t.start()
    write_audit_log("[RELAY_MONITOR] Background monitor started")


def _relay_monitor_loop():
    # Brief startup delay to let the rest of the app initialise
    time.sleep(30)

    while True:
        try:
            _run_one_relay_check()
        except Exception as e:
            write_audit_log(f"[RELAY_MONITOR][ERROR] {e}")

        time.sleep(MONITOR_INTERVAL_S)


def _run_one_relay_check():
    """
    Single relay health check cycle.
    Reads config fresh from disk each cycle so that a Redeploy
    or Disable from the UI is picked up without a backend restart.
    """
    if not RELAY_CONFIG_PATH.exists():
        return

    try:
        cfg = json.loads(RELAY_CONFIG_PATH.read_text())
    except Exception:
        return

    if not cfg.get("enabled") or not cfg.get("url"):
        return

    url  = cfg["url"]
    host = cfg.get("host", url)

    is_active = _check_relay_health_with_retry(url)
    _handle_health_result(is_active, host)


# --------------------------------------------------
# STATUS CHECK  (used by the UI endpoint)
# --------------------------------------------------

def get_relay_status() -> dict:
    """
    Returns current relay status for the UI.
    Uses the same retry-based health check as the background monitor
    so the UI and monitor are always consistent.
    """
    if not RELAY_CONFIG_PATH.exists():
        return {"configured": False, "active": False}

    try:
        cfg = json.loads(RELAY_CONFIG_PATH.read_text())
    except Exception:
        return {"configured": False, "active": False}

    if not cfg.get("enabled"):
        return {"configured": True, "active": False, "host": cfg.get("host")}

    url  = cfg["url"]
    host = cfg.get("host", url)

    # UI calls get a single fast check (no retries) to keep the
    # page responsive.  The background monitor applies full retries.
    active = False
    status_error = None
    try:
        import requests
        resp = requests.get(f"{url}/health", timeout=_HEALTH_TIMEOUT_S)
        if resp.ok:
            body = resp.json()
            active = (
                body.get("relay") == "scalp-terminal"
                or body.get("status") == "ok"
            )
        else:
            status_error = f"HTTP {resp.status_code}"
            active = False
    except Exception as e:
        status_error = str(e)

    if status_error:
        write_audit_log(f"[RELAY] UI health check failed: {status_error}")

    return {
        "configured": True,
        "active":     active,
        "host":       host,
        "url":        url,
    }


def disable_relay():
    """Turn off relay without deleting config (for testing/rollback)."""
    if not RELAY_CONFIG_PATH.exists():
        return
    cfg = json.loads(RELAY_CONFIG_PATH.read_text())
    cfg["enabled"] = False
    RELAY_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

    try:
        from app.execution.zerodha_executor import _invalidate_relay_cache
        _invalidate_relay_cache()
    except Exception:
        pass

    # Reset state tracking so monitor re-establishes baseline
    global _last_relay_active
    with _state_lock:
        _last_relay_active = None


# --------------------------------------------------
# DEPLOY
# --------------------------------------------------

def deploy_relay(
    host: str,
    ssh_username: str,
    ssh_private_key_text: str,
    progress_callback=None,
) -> Tuple[bool, str]:
    """
    SSH into OCI instance and deploy the relay service.

    Returns:
        (success: bool, message: str)

    progress_callback(step: str) is called with status updates
    so the API endpoint can stream progress to the UI.
    """

    def progress(msg: str):
        write_audit_log(f"[RELAY_DEPLOY] {msg}")
        if progress_callback:
            progress_callback(msg)

    try:
        import paramiko
    except ImportError:
        return False, (
            "paramiko not installed. "
            "Run: pip install paramiko --break-system-packages"
        )

    relay_secret = secrets.token_hex(32)

    progress(f"Connecting to {host}...")

    try:
        pkey = _load_private_key(ssh_private_key_text)
    except Exception as e:
        return False, f"Could not parse SSH private key: {e}"

    client = None
    last_connect_err = None

    for attempt in range(1, 6):
        _client = paramiko.SSHClient()
        _client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            _client.connect(
                hostname=host,
                username=ssh_username,
                pkey=pkey,
                timeout=60,
                banner_timeout=60,
                auth_timeout=30,
                look_for_keys=False,
                allow_agent=False,
            )
            client = _client
            last_connect_err = None
            break
        except paramiko.AuthenticationException:
            _client.close()
            return False, (
                "SSH authentication failed. "
                "Make sure you are using the correct private key (.key file) "
                "for this OCI instance, and that the SSH username is correct "
                "(use 'opc' for Oracle Linux, 'ubuntu' for Ubuntu)."
            )
        except Exception as e:
            _client.close()
            last_connect_err = e
            if attempt < 6:
                progress(f"SSH connect attempt {attempt} failed ({e}), retrying in 8s...")
                time.sleep(8)

    if client is None:
        return False, (
            f"Could not connect to {host} after 5 attempts: {last_connect_err}. "
            "Checklist:\n"
            "1. IP address is correct (Networking tab in OCI console)\n"
            "2. Port 22 Ingress Rule exists in your OCI Security List\n"
            "3. Instance status is Running (not Stopped)\n"
            "4. SSH username is 'opc' for Oracle Linux, 'ubuntu' for Ubuntu"
        )

    progress("Connected. Setting up relay service...")

    try:
        progress("Uploading relay service...")

        ok, _, err = _run(
            client,
            "sudo mkdir -p /opt/scalp-relay && "
            f"sudo chown {ssh_username}:{ssh_username} /opt/scalp-relay",
            timeout=15,
        )
        if not ok:
            return False, f"Failed to create relay directory: {err}"

        sftp = client.open_sftp()
        with sftp.open("/tmp/oci_order_relay.py", "w") as f:
            f.write(RELAY_SERVICE_SOURCE)
        sftp.close()

        ok, _, err = _run(
            client,
            "sudo mv /tmp/oci_order_relay.py /opt/scalp-relay/oci_order_relay.py",
            timeout=10,
        )
        if not ok:
            return False, f"Failed to move relay file: {err}"

        progress("Checking Python installation...")

        _, py3_path, _ = _run(client, "which python3 || which python3.9 || which python3.11", timeout=10)
        py3_bin = py3_path.strip().split("\n")[0].strip() or "python3"

        _, pip_path, _ = _run(client, "which pip3 || which pip", timeout=10)
        pip_present = bool(pip_path.strip())

        _, venv_check, _ = _run(client, f"{py3_bin} -m venv --help 2>&1 | head -1", timeout=10)
        venv_present = "usage" in venv_check.lower() or "optional" in venv_check.lower()

        progress(f"Python: {py3_bin}  pip: {'yes' if pip_present else 'no'}  venv: {'yes' if venv_present else 'no'}")

        if not pip_present or not venv_present:
            _, pkg_out, _ = _run(client, "which dnf || which yum || which apt-get", timeout=10)
            pkg_bin = pkg_out.strip().split("\n")[0].strip()

            if "apt" in pkg_bin:
                install_sys = (
                    "sudo apt-get update -qq && "
                    "sudo apt-get install -y -qq python3 python3-pip python3-venv"
                )
                ok, _, err = _run(client, install_sys, timeout=150)
                if not ok:
                    return False, f"Failed to install system packages: {err}"

            elif "dnf" in pkg_bin or "yum" in pkg_bin:
                if not pip_present:
                    ok, _, err = _run(
                        client,
                        f"sudo {pkg_bin} install -y -q python3-pip "
                        "--disablerepo='*' --enablerepo='ol*_baseos*,ol*_appstream*,baseos,appstream' "
                        "--setopt=timeout=20 --setopt=retries=1",
                        timeout=90,
                    )
                    if not ok:
                        progress("dnf pip install failed, bootstrapping pip via get-pip.py...")
                        ok, _, err = _run(
                            client,
                            "curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && "
                            f"sudo {py3_bin} /tmp/get-pip.py --quiet",
                            timeout=60,
                        )
                        if not ok:
                            return False, f"Failed to install pip: {err}"
            else:
                return False, (
                    "Could not detect a package manager (apt/dnf/yum). "
                    "Make sure your OCI instance is running Ubuntu 22.04 or Oracle Linux 8/9."
                )
        else:
            progress("Python and pip already installed — skipping system packages")

        progress("Creating Python virtual environment...")
        ok, _, err = _run(
            client,
            f"{py3_bin} -m venv /opt/scalp-relay/venv",
            timeout=30,
        )
        if not ok:
            _, pkg_out, _ = _run(client, "which dnf || which yum || which apt-get", timeout=10)
            pkg_bin = pkg_out.strip().split("\n")[0].strip()
            if "apt" in pkg_bin:
                _run(client, "sudo apt-get install -y -qq python3-venv", timeout=60)
            ok, _, err = _run(client, f"{py3_bin} -m venv /opt/scalp-relay/venv", timeout=30)
            if not ok:
                return False, f"Failed to create Python venv: {err}"

        progress("Installing relay Python packages...")
        ok, _, err = _run(
            client,
            "/opt/scalp-relay/venv/bin/pip install --quiet "
            "kiteconnect requests",
            timeout=120,
        )
        if not ok:
            return False, f"Failed to install Python packages: {err}"

        progress("Creating system service...")

        service_content = f"""[Unit]
Description=Scalp Terminal Order Relay
After=network.target

[Service]
Type=simple
User={ssh_username}
WorkingDirectory=/opt/scalp-relay
Environment=RELAY_SECRET={relay_secret}
ExecStart=/opt/scalp-relay/venv/bin/python3 /opt/scalp-relay/oci_order_relay.py
Restart=always
RestartSec=3
# TimeoutStopSec: give in-flight requests time to complete before force-kill
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
"""

        with client.open_sftp() as sftp:
            with sftp.open("/tmp/scalp-relay.service", "w") as f:
                f.write(service_content)

        # Stop any existing relay before replacing the unit file.
        # Prevents systemd "command vanished" kill during deploy.
        _run(client, "sudo systemctl stop scalp-relay 2>/dev/null || true", timeout=15)

        ok, _, err = _run(
            client,
            "sudo cp /tmp/scalp-relay.service /etc/systemd/system/scalp-relay.service "
            "&& sudo systemctl daemon-reload "
            "&& sudo systemctl enable scalp-relay "
            "&& sudo systemctl start scalp-relay",
            timeout=30,
        )
        if not ok:
            return False, f"Failed to start relay service: {err}"

        progress("Opening firewall port 8001...")

        fw_ok, fw_out, _ = _run(
            client,
            "sudo systemctl is-active firewalld 2>/dev/null || echo inactive",
            timeout=10,
        )

        if fw_out.strip() == "active":
            _run(
                client,
                "sudo firewall-cmd --permanent --add-port=8001/tcp && "
                "sudo firewall-cmd --reload",
                timeout=20,
            )
            progress("Opened port 8001 via firewalld")
        else:
            _run(
                client,
                "sudo iptables -C INPUT -p tcp --dport 8001 -j ACCEPT 2>/dev/null "
                "|| sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT",
                timeout=10,
            )
            progress("Opened port 8001 via iptables")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Install two systemd timers:
        #
        # 1. scalp-relay-keepalive  — fires every 10 seconds
        #    Sends a local curl to the relay. This prevents OCI free-tier
        #    CPU throttle from making the process unresponsive. When Oracle
        #    sees the process is idle, it de-schedules it so aggressively
        #    that even a trivial HTTP response takes >6 seconds. A local
        #    ping every 10 seconds keeps the process warm and scheduled.
        #
        # 2. scalp-relay-watchdog   — fires every 15 seconds
        #    If the health check fails twice, restarts the relay service.
        #    Runs as root via systemd so /bin/systemctl restart works
        #    unconditionally — no sudo, no TTY, no PATH issues.
        #
        # WHY NOT CRON: cron runs in a stripped environment. On OCI Oracle
        # Linux, `sudo systemctl restart` inside cron silently fails because
        # sudo requires a TTY. The relay stays frozen indefinitely.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        progress("Installing keep-alive and watchdog timers (systemd)...")

        # ── Keep-alive script: simple local ping, no restart logic ────────
        keepalive_script = (
            "#!/bin/bash\n"
            "# Keep the relay process warm so OCI does not CPU-throttle it.\n"
            "curl -sf --max-time 3 http://localhost:8001/health > /dev/null 2>&1\n"
            "exit 0\n"
        )

        # ── Watchdog script: double-check before restart ──────────────────
        watchdog_script = (
            "#!/bin/bash\n"
            "# Returns 0 only on HTTP 200. Catches both timeout (frozen)\n"
            "# and HTTP 503 (thread pool exhausted).\n"
            "LOG=/var/log/scalp-relay-watchdog.log\n"
            "check() {\n"
            "  CODE=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://localhost:8001/health 2>/dev/null)\n"
            "  [ \"$CODE\" = \"200\" ]\n"
            "}\n"
            "if ! check; then\n"
            "  sleep 3\n"
            "  if ! check; then\n"
            "    echo \"$(date): relay unhealthy — restarting\" >> \"$LOG\"\n"
            "    /bin/systemctl restart scalp-relay\n"
            "    echo \"$(date): restart issued\" >> \"$LOG\"\n"
            "  fi\n"
            "fi\n"
            "if [ -f \"$LOG\" ] && [ \"$(wc -l < \"$LOG\")\" -gt 500 ]; then\n"
            "  tail -200 \"$LOG\" > \"${LOG}.tmp\" && mv \"${LOG}.tmp\" \"$LOG\"\n"
            "fi\n"
        )

        # ── systemd unit files ────────────────────────────────────────────
        keepalive_service = (
            "[Unit]\n"
            "Description=Scalp Relay Keep-Alive Ping\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecStart=/opt/scalp-relay/keepalive.sh\n"
        )
        keepalive_timer = (
            "[Unit]\n"
            "Description=Scalp Relay Keep-Alive Timer (every 10s)\n"
            "\n"
            "[Timer]\n"
            "OnBootSec=90\n"
            "OnUnitActiveSec=10\n"
            "AccuracySec=1\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )
        watchdog_service = (
            "[Unit]\n"
            "Description=Scalp Terminal Relay Watchdog\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecStart=/opt/scalp-relay/watchdog.sh\n"
        )
        watchdog_timer = (
            "[Unit]\n"
            "Description=Scalp Terminal Relay Watchdog Timer (every 15s)\n"
            "\n"
            "[Timer]\n"
            "OnBootSec=120\n"
            "OnUnitActiveSec=15\n"
            "AccuracySec=1\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )

        # Install scripts and unit files using SFTP + sudo cp + restorecon.
        #
        # WHY THIS PATTERN:
        # - SFTP writes to /tmp are always reliable (opc owns /tmp files)
        # - `sudo cp` to /etc/systemd/system/ creates a NEW file, letting
        #   the filesystem assign the correct SELinux context (systemd_unit_file_t)
        # - `sudo mv` copies the source context (tmp_t) — that's why previous
        #   deploys left unit files that systemd refused to read
        # - `sudo restorecon` enforces the correct label as a final safety net

        timer_ok  = True
        timer_err = ""

        # Step A: write scripts via SFTP, chmod via SSH
        try:
            sftp = client.open_sftp()
            sftp.open("/tmp/scalp-relay-keepalive.sh", "w").write(keepalive_script)
            sftp.open("/tmp/scalp-relay-watchdog.sh",  "w").write(watchdog_script)
            sftp.close()
        except Exception as e:
            timer_ok = False; timer_err = f"SFTP script write: {e}"

        if timer_ok:
            ok, _, e = _run(
                client,
                "sudo cp /tmp/scalp-relay-keepalive.sh /opt/scalp-relay/keepalive.sh "
                "&& sudo cp /tmp/scalp-relay-watchdog.sh  /opt/scalp-relay/watchdog.sh "
                "&& sudo chmod +x /opt/scalp-relay/keepalive.sh "
                "&& sudo chmod +x /opt/scalp-relay/watchdog.sh",
                timeout=10,
            )
            if not ok:
                timer_ok = False; timer_err = f"script install: {e}"

        # Step B: write unit files via SFTP, then sudo cp to systemd directory
        unit_files = [
            ("scalp-relay-keepalive.service", keepalive_service),
            ("scalp-relay-keepalive.timer",   keepalive_timer),
            ("scalp-relay-watchdog.service",  watchdog_service),
            ("scalp-relay-watchdog.timer",    watchdog_timer),
        ]
        if timer_ok:
            try:
                sftp = client.open_sftp()
                for fname, body in unit_files:
                    sftp.open(f"/tmp/{fname}", "w").write(body)
                sftp.close()
            except Exception as e:
                timer_ok = False; timer_err = f"SFTP unit file write: {e}"

        if timer_ok:
            # sudo cp (not mv) so SELinux assigns correct context to new file
            cp_cmds = " && ".join(
                f"sudo cp /tmp/{fname} /etc/systemd/system/{fname}"
                for fname, _ in unit_files
            )
            ok, _, e = _run(client, cp_cmds, timeout=15)
            if not ok:
                timer_ok = False; timer_err = f"unit file cp: {e}"

        # Step C: fix SELinux context (restorecon is a no-op if SELinux is disabled)
        if timer_ok:
            _run(
                client,
                "sudo restorecon -v /etc/systemd/system/scalp-relay-*.service "
                "/etc/systemd/system/scalp-relay-*.timer 2>/dev/null || true",
                timeout=10,
            )

        # Step D: reload systemd and verify files are visible
        if timer_ok:
            ok, _, e = _run(client, "sudo systemctl daemon-reload", timeout=15)
            if not ok:
                timer_ok = False; timer_err = f"daemon-reload: {e}"

        if timer_ok:
            ok, out, _ = _run(
                client,
                "systemctl list-unit-files scalp-relay-keepalive.timer "
                "scalp-relay-watchdog.timer 2>&1",
                timeout=10,
            )
            if "scalp-relay-keepalive.timer" not in out:
                timer_ok  = False
                timer_err = f"unit files not visible to systemd after daemon-reload: {out}"

        # Step E: enable and start both timers
        if timer_ok:
            ok, _, e = _run(
                client,
                "sudo systemctl enable --now scalp-relay-keepalive.timer "
                "&& sudo systemctl enable --now scalp-relay-watchdog.timer",
                timeout=15,
            )
            if not ok:
                timer_ok = False; timer_err = f"enable timers: {e}"

        if not timer_ok:
            progress(f"Warning: timer install failed ({timer_err}) — relay works but watchdog inactive")
        else:
            _run(client,
                 "crontab -l 2>/dev/null | grep -v scalp-relay | crontab - 2>/dev/null || true",
                 timeout=10)
            progress("Keep-alive (10s) + watchdog (15s) timers installed via systemd")

        progress("Waiting for relay to start...")
        time.sleep(4)

        ok, out, err = _run(
            client,
            "curl -s --max-time 5 http://localhost:8001/health",
            timeout=15,
        )

        if not ok or "scalp-terminal" not in out:
            _, logs, _ = _run(
                client,
                "sudo journalctl -u scalp-relay --since '1 min ago' --no-pager -n 20",
                timeout=10,
            )
            return False, (
                f"Relay started but health check failed. Service logs:\n{logs}"
            )

        progress("Relay is healthy on OCI instance.")

    except Exception as e:
        return False, f"Deployment error: {e}"

    finally:
        client.close()

    progress("Saving relay configuration...")

    RELAY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RELAY_CONFIG_PATH.write_text(
        json.dumps(
            {
                "enabled": True,
                "url": f"http://{host}:8001",
                "secret": relay_secret,
                "host": host,
            },
            indent=2,
        )
    )

    # Reset state tracking so monitor re-establishes a clean baseline
    # after a fresh deploy (prevents a spurious "Recovered" alert)
    global _last_relay_active
    with _state_lock:
        _last_relay_active = None

    try:
        from app.execution.zerodha_executor import _invalidate_relay_cache
        _invalidate_relay_cache()
    except Exception:
        pass

    write_audit_log(f"[RELAY] Deployment complete. Relay active at http://{host}:8001")

    return True, (
        f"Relay deployed successfully at {host}. "
        f"All order placement will now route through your static IP."
    )


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _run(client, cmd: str, timeout: int = 30):
    """
    Run a shell command over SSH with a hard wall-clock timeout.
    """
    import time as _time

    try:
        transport = client.get_transport()
        channel = transport.open_session()
        channel.set_combine_stderr(False)
        channel.settimeout(5.0)
        channel.exec_command(cmd)

        deadline = _time.time() + timeout
        stdout_buf = b""
        stderr_buf = b""

        while True:
            while channel.recv_ready():
                chunk = channel.recv(4096)
                if chunk:
                    stdout_buf += chunk

            while channel.recv_stderr_ready():
                chunk = channel.recv_stderr(4096)
                if chunk:
                    stderr_buf += chunk

            if channel.exit_status_ready():
                break

            if _time.time() > deadline:
                channel.close()
                return False, "", f"Command timed out after {timeout}s: {cmd[:80]}"

            _time.sleep(0.5)

        while channel.recv_ready():
            stdout_buf += channel.recv(4096)
        while channel.recv_stderr_ready():
            stderr_buf += channel.recv_stderr(4096)

        exit_code = channel.recv_exit_status()
        out = stdout_buf.decode("utf-8", errors="replace").strip()
        err = stderr_buf.decode("utf-8", errors="replace").strip()
        return exit_code == 0, out, err

    except Exception as e:
        return False, "", str(e)


def _load_private_key(key_text: str):
    """
    Parse a PEM private key string (RSA, ECDSA, or Ed25519).
    """
    import paramiko

    key_text = key_text.strip()

    if hasattr(paramiko.pkey.PKey, "from_private_key"):
        try:
            return paramiko.pkey.PKey.from_private_key(io.StringIO(key_text))
        except Exception:
            pass

    key_classes = [
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.Ed25519Key,
    ]

    dss = getattr(paramiko, "DSSKey", None)
    if dss is not None:
        key_classes.append(dss)

    last_err = None
    for key_class in key_classes:
        try:
            return key_class.from_private_key(io.StringIO(key_text))
        except Exception as e:
            last_err = e
            continue

    raise ValueError(
        "Could not parse SSH key. Make sure you paste the full key including "
        "the -----BEGIN ... PRIVATE KEY----- and -----END ... PRIVATE KEY----- lines. "
        f"Last error: {last_err}"
    )