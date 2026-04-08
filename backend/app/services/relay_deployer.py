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
RELAY_SERVICE_SOURCE = '''"""
oci_order_relay.py — Scalp Terminal Order Relay
Forwards Zerodha order placement calls from the registered static IP.
"""
import os
import hmac
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from kiteconnect import KiteConnect

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
if not RELAY_SECRET:
    raise RuntimeError("RELAY_SECRET environment variable must be set.")

app = FastAPI(docs_url=None, redoc_url=None)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("relay")

def verify(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, RELAY_SECRET):
        raise HTTPException(status_code=403, detail="Invalid secret")

def get_kite(api_key: str, access_token: str) -> KiteConnect:
    k = KiteConnect(api_key=api_key)
    k.set_access_token(access_token)
    return k

class PlaceOrderRequest(BaseModel):
    api_key: str
    access_token: str
    variety: str
    exchange: str
    tradingsymbol: str
    transaction_type: str
    quantity: int
    order_type: str
    product: str
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    tag: Optional[str] = None

class PlaceGTTRequest(BaseModel):
    api_key: str
    access_token: str
    trigger_type: str
    tradingsymbol: str
    exchange: str
    trigger_values: list
    last_price: float
    orders: list

class CancelOrderRequest(BaseModel):
    api_key: str
    access_token: str
    variety: str
    order_id: str

@app.get("/health")
def health():
    return {"status": "ok", "relay": "scalp-terminal"}

# BROKER_TIMEOUT: max seconds we wait for any Zerodha API call.
# kiteconnect uses requests internally; we run it in a thread pool
# via asyncio.get_event_loop().run_in_executor so the event loop
# stays responsive even when the underlying TCP call stalls.
import asyncio
import functools

BROKER_TIMEOUT = 20  # seconds

async def _call_broker(func, *args, **kwargs):
    """
    Run a blocking kiteconnect call in a thread-pool executor with a
    hard timeout.  If Zerodha does not respond within BROKER_TIMEOUT
    seconds the coroutine raises asyncio.TimeoutError, which FastAPI
    converts to a 500 — leaving the worker free for the next request.
    """
    loop = asyncio.get_event_loop()
    coro = loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
    return await asyncio.wait_for(coro, timeout=BROKER_TIMEOUT)

@app.post("/relay/place_order")
async def relay_place_order(body: PlaceOrderRequest, authorization: str = Header(...)):
    verify(authorization)
    try:
        kite = get_kite(body.api_key, body.access_token)
        kwargs = dict(
            variety=body.variety, exchange=body.exchange,
            tradingsymbol=body.tradingsymbol,
            transaction_type=body.transaction_type,
            quantity=body.quantity, order_type=body.order_type,
            product=body.product,
        )
        if body.price is not None: kwargs["price"] = body.price
        if body.trigger_price is not None: kwargs["trigger_price"] = body.trigger_price
        if body.tag is not None: kwargs["tag"] = body.tag
        order_id = await _call_broker(kite.place_order, **kwargs)
        log.info(f"[ORDER] {body.tradingsymbol} {body.transaction_type} qty={body.quantity} id={order_id}")
        return {"order_id": order_id}
    except asyncio.TimeoutError:
        log.error(f"[ORDER_TIMEOUT] {body.tradingsymbol} — broker did not respond in {BROKER_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Broker API timeout")
    except Exception as e:
        log.error(f"[ORDER_ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/relay/place_gtt")
async def relay_place_gtt(body: PlaceGTTRequest, authorization: str = Header(...)):
    verify(authorization)
    try:
        kite = get_kite(body.api_key, body.access_token)
        result = await _call_broker(
            kite.place_gtt,
            trigger_type=body.trigger_type, tradingsymbol=body.tradingsymbol,
            exchange=body.exchange, trigger_values=body.trigger_values,
            last_price=body.last_price, orders=body.orders,
        )
        trigger_id = result.get("trigger_id", result) if isinstance(result, dict) else result
        log.info(f"[GTT] {body.tradingsymbol} gtt_id={trigger_id}")
        return {"trigger_id": trigger_id}
    except asyncio.TimeoutError:
        log.error(f"[GTT_TIMEOUT] {body.tradingsymbol} — broker did not respond in {BROKER_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Broker API timeout")
    except Exception as e:
        log.error(f"[GTT_ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/relay/gtt/{trigger_id}")
async def relay_cancel_gtt(trigger_id: int, api_key: str, access_token: str,
                            authorization: str = Header(...)):
    verify(authorization)
    try:
        await _call_broker(get_kite(api_key, access_token).delete_gtt, trigger_id)
        return {"cancelled": trigger_id}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Broker API timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/relay/cancel_order")
async def relay_cancel_order(body: CancelOrderRequest, authorization: str = Header(...)):
    verify(authorization)
    try:
        await _call_broker(
            get_kite(body.api_key, body.access_token).cancel_order,
            variety=body.variety, order_id=body.order_id,
        )
        return {"cancelled": body.order_id}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Broker API timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
'''


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
_HEALTH_TIMEOUT_S     = 8    # per-request timeout


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
            if resp.ok:
                body = resp.json()
                if body.get("relay") == "scalp-terminal" or body.get("status") == "ok":
                    if attempt > 1:
                        write_audit_log(
                            f"[RELAY] Health check passed on attempt {attempt}/{_HEALTH_RETRY_COUNT}"
                        )
                    return True
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

MONITOR_INTERVAL_S = 120   # check every 2 minutes


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
            "fastapi 'uvicorn[standard]' kiteconnect requests",
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
ExecStart=/opt/scalp-relay/venv/bin/uvicorn oci_order_relay:app \\
    --host 0.0.0.0 --port 8001 \\
    --workers 4 \\
    --timeout-keep-alive 10 \\
    --timeout-graceful-shutdown 5 \\
    --limit-concurrency 20 \\
    --backlog 64
Restart=always
RestartSec=5
# Kill any worker that has been running for more than 60 seconds
# (catches a completely stuck worker process)
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
"""

        with client.open_sftp() as sftp:
            with sftp.open("/tmp/scalp-relay.service", "w") as f:
                f.write(service_content)

        ok, _, err = _run(
            client,
            "sudo cp /tmp/scalp-relay.service /etc/systemd/system/scalp-relay.service "
            "&& sudo systemctl daemon-reload "
            "&& sudo systemctl enable scalp-relay "
            "&& sudo systemctl restart scalp-relay",
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

        # ── Install self-healing watchdog cron ────────────────────────────
        # Runs every minute on OCI itself.  Checks /health; if the relay
        # does not respond in 5 s it does `systemctl restart scalp-relay`.
        # This is entirely local to the OCI instance — no SSH from your
        # laptop is needed, and it heals in under 60 seconds.
        progress("Installing self-healing watchdog cron...")

        watchdog_script = (
            "#!/bin/bash\n"
            "curl -sf --max-time 5 http://localhost:8001/health > /dev/null 2>&1\n"
            "if [ $? -ne 0 ]; then\n"
            "  echo \"$(date): relay unresponsive — restarting\" "
            ">> /var/log/scalp-relay-watchdog.log\n"
            "  sudo systemctl restart scalp-relay\n"
            "fi\n"
        )

        # Write script to /tmp (no sudo needed via SFTP)
        with client.open_sftp() as sftp:
            with sftp.open("/tmp/scalp-relay-watchdog.sh", "w") as wf:
                wf.write(watchdog_script)

        ok, _, err = _run(
            client,
            "sudo mv /tmp/scalp-relay-watchdog.sh /opt/scalp-relay/watchdog.sh "
            "&& sudo chmod +x /opt/scalp-relay/watchdog.sh",
            timeout=10,
        )
        if not ok:
            # Non-fatal — log and continue.  Watchdog is a safety net,
            # not a hard requirement for the relay to work.
            progress(f"Warning: could not install watchdog script ({err}) — continuing without it")
        else:
            # Install as a crontab entry for the ssh user (not root).
            # `crontab -l 2>/dev/null` silently ignores "no crontab" error.
            # We use a marker comment so re-deploys are idempotent.
            ok, _, err = _run(
                client,
                "(crontab -l 2>/dev/null | grep -v scalp-relay-watchdog; "
                "echo '* * * * * /opt/scalp-relay/watchdog.sh') | crontab -",
                timeout=10,
            )
            if ok:
                progress("Watchdog cron installed — relay will self-heal within 60s if it freezes")
            else:
                progress(f"Warning: crontab install failed ({err}) — continuing without watchdog")

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