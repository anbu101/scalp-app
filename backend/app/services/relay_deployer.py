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

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            username=ssh_username,
            pkey=pkey,
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
    except paramiko.AuthenticationException:
        return False, (
            "SSH authentication failed. "
            "Make sure you're using the correct private key for this OCI instance."
        )
    except Exception as e:
        return False, (
            f"Could not connect to {host}: {e}. "
            "Check the IP address and make sure port 22 is open in your OCI Security List."
        )

    progress("Connected. Setting up relay service...")

    try:
        # --------------------------------------------------
        # Upload relay service source
        # --------------------------------------------------
        progress("Uploading relay service...")

        sftp = client.open_sftp()
        try:
            sftp.mkdir("/opt/scalp-relay")
        except IOError:
            pass  # already exists

        with sftp.open("/opt/scalp-relay/oci_order_relay.py", "w") as f:
            f.write(RELAY_SERVICE_SOURCE)

        sftp.close()

        # --------------------------------------------------
        # Install dependencies
        # --------------------------------------------------
        progress("Installing Python dependencies (this takes ~60 seconds)...")

        install_cmd = (
            "sudo apt-get update -qq && "
            "sudo apt-get install -y -qq python3 python3-pip python3-venv && "
            "python3 -m venv /opt/scalp-relay/venv && "
            "/opt/scalp-relay/venv/bin/pip install --quiet "
            "fastapi 'uvicorn[standard]' kiteconnect requests"
        )

        ok, out, err = _run(client, install_cmd, timeout=180)
        if not ok:
            return False, f"Failed to install dependencies: {err}"

        # --------------------------------------------------
        # Create systemd service
        # --------------------------------------------------
        progress("Creating system service...")

        service_content = f"""[Unit]
Description=Scalp Terminal Order Relay
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/scalp-relay
Environment=RELAY_SECRET={relay_secret}
ExecStart=/opt/scalp-relay/venv/bin/uvicorn oci_order_relay:app --host 0.0.0.0 --port 8001 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

        # Write service file via echo to avoid needing a separate upload
        escaped = service_content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        service_cmd = (
            f'printf "{escaped}" | '
            f'sudo tee /etc/systemd/system/scalp-relay.service > /dev/null'
        )

        # Simpler: write to /tmp first, then move
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
        # --------------------------------------------------
        _run(
            client,
            "sudo iptables -C INPUT -p tcp --dport 8001 -j ACCEPT 2>/dev/null "
            "|| sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT",
            timeout=10,
        )

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

    # Quick health check
    try:
        import requests
        resp = requests.get(
            f"{cfg['url']}/health",
            timeout=5,
        )
        active = resp.ok and resp.json().get("relay") == "scalp-terminal"
    except Exception:
        active = False

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
    """Run a shell command over SSH. Returns (success, stdout, stderr)."""
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        return exit_code == 0, out, err
    except Exception as e:
        return False, "", str(e)


def _load_private_key(key_text: str):
    """
    Parse a PEM private key string (RSA, ECDSA, or Ed25519).
    OCI generates RSA keys by default.
    """
    import paramiko

    key_text = key_text.strip()
    key_file = io.StringIO(key_text)

    for key_class in (
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.Ed25519Key,
        paramiko.DSSKey,
    ):
        try:
            key_file.seek(0)
            return key_class.from_private_key(key_file)
        except Exception:
            continue

    raise ValueError(
        "Could not parse SSH key. Make sure you paste the full key including "
        "the -----BEGIN ... PRIVATE KEY----- and -----END ... PRIVATE KEY----- lines."
    )