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
from pathlib import Path
from typing import Tuple

from app.event_bus.audit_logger import write_audit_log

# Relay config lives next to all other app state
RELAY_CONFIG_PATH = Path.home() / ".scalp-app" / "relay_config.json"

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
        order_id = kite.place_order(**kwargs)
        log.info(f"[ORDER] {body.tradingsymbol} {body.transaction_type} qty={body.quantity} id={order_id}")
        return {"order_id": order_id}
    except Exception as e:
        log.error(f"[ORDER_ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/relay/place_gtt")
async def relay_place_gtt(body: PlaceGTTRequest, authorization: str = Header(...)):
    verify(authorization)
    try:
        kite = get_kite(body.api_key, body.access_token)
        result = kite.place_gtt(
            trigger_type=body.trigger_type, tradingsymbol=body.tradingsymbol,
            exchange=body.exchange, trigger_values=body.trigger_values,
            last_price=body.last_price, orders=body.orders,
        )
        trigger_id = result.get("trigger_id", result) if isinstance(result, dict) else result
        log.info(f"[GTT] {body.tradingsymbol} gtt_id={trigger_id}")
        return {"trigger_id": trigger_id}
    except Exception as e:
        log.error(f"[GTT_ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/relay/gtt/{trigger_id}")
async def relay_cancel_gtt(trigger_id: int, api_key: str, access_token: str,
                            authorization: str = Header(...)):
    verify(authorization)
    try:
        get_kite(api_key, access_token).delete_gtt(trigger_id)
        return {"cancelled": trigger_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/relay/cancel_order")
async def relay_cancel_order(body: CancelOrderRequest, authorization: str = Header(...)):
    verify(authorization)
    try:
        get_kite(body.api_key, body.access_token).cancel_order(
            variety=body.variety, order_id=body.order_id
        )
        return {"cancelled": body.order_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
'''


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

    # --------------------------------------------------
    # Generate a strong secret for this deployment
    # --------------------------------------------------
    relay_secret = secrets.token_hex(32)

    # --------------------------------------------------
    # SSH connect
    # --------------------------------------------------
    progress(f"Connecting to {host}...")

    try:
        pkey = _load_private_key(ssh_private_key_text)
    except Exception as e:
        return False, f"Could not parse SSH private key: {e}"

    # Retry up to 5 times with a fresh SSHClient each attempt.
    # IMPORTANT: paramiko marks the transport as dead after any failure,
    # so we must create a new SSHClient object on every retry — reusing
    # the same client after a failed connect causes "No existing session".
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
            client = _client          # success — keep this client
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
        # --------------------------------------------------
        # Upload relay service source
        # Strategy: upload to /tmp first (no sudo needed for SFTP),
        # then sudo-move into /opt/scalp-relay/.
        # Direct SFTP writes to /opt/ fail because they require sudo.
        # --------------------------------------------------
        progress("Uploading relay service...")

        # Step 1: create /opt/scalp-relay with sudo via SSH exec
        ok, _, err = _run(
            client,
            "sudo mkdir -p /opt/scalp-relay && "
            f"sudo chown {ssh_username}:{ssh_username} /opt/scalp-relay",
            timeout=15,
        )
        if not ok:
            return False, f"Failed to create relay directory: {err}"

        # Step 2: upload to /tmp (always writable, no sudo needed)
        sftp = client.open_sftp()
        with sftp.open("/tmp/oci_order_relay.py", "w") as f:
            f.write(RELAY_SERVICE_SOURCE)
        sftp.close()

        # Step 3: move from /tmp to final location
        ok, _, err = _run(
            client,
            "sudo mv /tmp/oci_order_relay.py /opt/scalp-relay/oci_order_relay.py",
            timeout=10,
        )
        if not ok:
            return False, f"Failed to move relay file: {err}"

        # --------------------------------------------------
        # Detect OS package manager and install dependencies
        # OCI free tier images:
        #   Ubuntu  → ssh user "ubuntu" → apt-get
        #   Oracle Linux → ssh user "opc" → dnf (or yum fallback)
        # --------------------------------------------------
        progress("Checking Python installation...")

        # Check what is already installed — Oracle Linux and Ubuntu both
        # ship with python3. Only install system packages if missing.
        _, py3_path, _ = _run(client, "which python3 || which python3.9 || which python3.11", timeout=10)
        py3_bin = py3_path.strip().split("\n")[0].strip() or "python3"

        _, pip_path, _ = _run(client, "which pip3 || which pip", timeout=10)
        pip_present = bool(pip_path.strip())

        _, venv_check, _ = _run(client, f"{py3_bin} -m venv --help 2>&1 | head -1", timeout=10)
        venv_present = "usage" in venv_check.lower() or "optional" in venv_check.lower()

        progress(f"Python: {py3_bin}  pip: {'yes' if pip_present else 'no'}  venv: {'yes' if venv_present else 'no'}")

        # Only call the package manager if something critical is missing
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
                # pip3 on Oracle Linux — install only what is missing
                # Use --disablerepo=* --enablerepo=ol*_baseos* to skip
                # slow third-party metadata that causes the hang
                if not pip_present:
                    ok, _, err = _run(
                        client,
                        f"sudo {pkg_bin} install -y -q python3-pip "
                        "--disablerepo='*' --enablerepo='ol*_baseos*,ol*_appstream*,baseos,appstream' "
                        "--setopt=timeout=20 --setopt=retries=1",
                        timeout=90,
                    )
                    if not ok:
                        # fallback: bootstrap pip directly without dnf
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

        # Create virtual environment
        progress("Creating Python virtual environment...")
        ok, _, err = _run(
            client,
            f"{py3_bin} -m venv /opt/scalp-relay/venv",
            timeout=30,
        )
        if not ok:
            # venv module missing — try to install it then retry
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

        # --------------------------------------------------
        # Create systemd service
        # --------------------------------------------------
        progress("Creating system service...")

        service_content = f"""[Unit]
Description=Scalp Terminal Order Relay
After=network.target

[Service]
Type=simple
User={ssh_username}
WorkingDirectory=/opt/scalp-relay
Environment=RELAY_SECRET={relay_secret}
ExecStart=/opt/scalp-relay/venv/bin/uvicorn oci_order_relay:app --host 0.0.0.0 --port 8001 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

        # Write to /tmp via SFTP (no sudo), then sudo-copy to systemd
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

        # --------------------------------------------------
        # Open port 8001 in OS firewall
        # Oracle Linux uses firewalld (not iptables directly).
        # Ubuntu uses ufw or plain iptables.
        # We try firewalld first, fall back to iptables.
        # --------------------------------------------------
        progress("Opening firewall port 8001...")

        # Check if firewalld is running (Oracle Linux default)
        fw_ok, fw_out, _ = _run(
            client,
            "sudo systemctl is-active firewalld 2>/dev/null || echo inactive",
            timeout=10,
        )

        if fw_out.strip() == "active":
            # firewalld path — used by Oracle Linux
            _run(
                client,
                "sudo firewall-cmd --permanent --add-port=8001/tcp && "
                "sudo firewall-cmd --reload",
                timeout=20,
            )
            progress("Opened port 8001 via firewalld")
        else:
            # Plain iptables path — used by Ubuntu
            _run(
                client,
                "sudo iptables -C INPUT -p tcp --dport 8001 -j ACCEPT 2>/dev/null "
                "|| sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT",
                timeout=10,
            )
            progress("Opened port 8001 via iptables")

        # --------------------------------------------------
        # Wait for service to start and health check
        # --------------------------------------------------
        progress("Waiting for relay to start...")
        time.sleep(4)

        ok, out, err = _run(
            client,
            "curl -s --max-time 5 http://localhost:8001/health",
            timeout=15,
        )

        if not ok or "scalp-terminal" not in out:
            # Get service logs for diagnosis
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

    # --------------------------------------------------
    # Write relay_config.json locally
    # --------------------------------------------------
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

    # Invalidate the in-memory relay config cache in executor
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
# STATUS CHECK
# --------------------------------------------------

def get_relay_status() -> dict:
    """
    Returns current relay status for the UI.
    """
    if not RELAY_CONFIG_PATH.exists():
        return {"configured": False, "active": False}

    try:
        cfg = json.loads(RELAY_CONFIG_PATH.read_text())
    except Exception:
        return {"configured": False, "active": False}

    if not cfg.get("enabled"):
        return {"configured": True, "active": False, "host": cfg.get("host")}

    # Quick health check — log the actual failure so UI can show it
    active = False
    status_error = None
    try:
        import requests
        resp = requests.get(
            f"{cfg['url']}/health",
            timeout=8,
        )
        if resp.ok:
            body = resp.json()
            # Accept both exact match and any 200 response from our relay
            active = body.get("relay") == "scalp-terminal" or body.get("status") == "ok"
        else:
            status_error = f"HTTP {resp.status_code}"
    except Exception as e:
        status_error = str(e)

    if status_error:
        write_audit_log(f"[RELAY] Health check failed: {status_error}")

    return {
        "configured": True,
        "active": active,
        "host": cfg.get("host"),
        "url": cfg.get("url"),
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


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _run(client, cmd: str, timeout: int = 30):
    """
    Run a shell command over SSH with a hard wall-clock timeout.

    paramiko exec_command(timeout=N) only sets a socket READ timeout,
    not a process timeout — recv_exit_status() blocks forever if the
    remote command hangs (e.g. dnf waiting for metadata).

    This implementation polls the channel exit status in a loop with
    a hard deadline so the caller is never stuck indefinitely.
    """
    import time as _time

    try:
        transport = client.get_transport()
        channel = transport.open_session()
        channel.set_combine_stderr(False)
        channel.settimeout(5.0)   # short socket read slice
        channel.exec_command(cmd)

        deadline = _time.time() + timeout
        stdout_buf = b""
        stderr_buf = b""

        while True:
            # Drain available data from both streams
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

        # Drain any final bytes
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
    OCI generates RSA keys by default.

    DSSKey was removed in paramiko 3.x — we try it only if present
    so this works on both old and new paramiko versions.
    """
    import paramiko

    key_text = key_text.strip()

    # Newer paramiko (3.x+) exposes a single from_private_key() on the
    # base PKey class that auto-detects the key type. Try that first.
    if hasattr(paramiko.pkey.PKey, "from_private_key"):
        try:
            return paramiko.pkey.PKey.from_private_key(io.StringIO(key_text))
        except Exception:
            pass

    # Fallback: try each concrete key class in order.
    # DSSKey was removed in paramiko 3.x — guard with getattr.
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