from pathlib import Path
import json
import base64
from datetime import datetime

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from app.license import license_state
from app.license.license_state import LicenseStatus
from app.event_bus.audit_logger import write_audit_log
from app.license.machine_id import get_machine_id

# --------------------------------------------------
# LICENSE FILES
# --------------------------------------------------

LICENSE_DIR = Path.home() / ".scalp-app" / "license"
LICENSE_FILE = LICENSE_DIR / "license.json"

# --------------------------------------------------
# 🔒 PUBLIC KEY (PEM — EXACT)
# --------------------------------------------------

PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0rbsZtV5GsYDxon76iul
gWjes9wDSoQM4HV3GYdLW3ZX25fUs7zxk4NYNIYfJjUjms+2bTbv+oKqnCpmtkTM
CBErU/w4msLnB0LdEqlpetb4Bs95VAaqC3s4d/78txptu0VfGN7x6SrpECZIsyEM
C6/t9WpONLKXVkueAO5mOpQ1yfbWez0FJuW2DKd3wc3dnHBY5QpSBTxvsOb08su+
1Hrn9dNfnZI9sksVoBfrQewjyzCm1WSORGZha/HJsX85k1V271YC68hA9FCOzZ6F
OtPI9oO41iaHhysL+nmY3bC81dEFUPHsV69YBEjM4S6L9ZlhmTXTI3qo7wdOjww6
LwIDAQAB
-----END PUBLIC KEY-----"""

# --------------------------------------------------
# INTERNAL
# --------------------------------------------------

def _verify_signature(payload: str, signature_b64: str):
    pubkey = load_pem_public_key(PUBLIC_KEY_PEM)
    signature = base64.b64decode(signature_b64)

    pubkey.verify(
        signature,
        payload.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

# --------------------------------------------------
# PUBLIC API
# --------------------------------------------------

def validate_license():
    """
    Non-fatal license validation.
    Updates SINGLE SOURCE OF TRUTH: license_state
    """
    try:
        LICENSE_DIR.mkdir(parents=True, exist_ok=True)

        if not LICENSE_FILE.exists():
            license_state.LICENSE_STATUS = LicenseStatus.MISSING
            license_state.LICENSE_MESSAGE = "License file missing"
            write_audit_log("[LICENSE][WARN] License file missing")
            return

        data = json.loads(LICENSE_FILE.read_text())
        machine_id = get_machine_id()

        if data.get("product") != "scalp":
            license_state.LICENSE_STATUS = LicenseStatus.INVALID
            license_state.LICENSE_MESSAGE = "Invalid product"
            return

        if data.get("machine_id") != machine_id:
            license_state.LICENSE_STATUS = LicenseStatus.INVALID
            license_state.LICENSE_MESSAGE = "License not for this machine"
            return

        expiry = datetime.strptime(data["expiry"], "%Y-%m-%d").date()
        if expiry < datetime.utcnow().date():
            license_state.LICENSE_STATUS = LicenseStatus.EXPIRED
            license_state.LICENSE_MESSAGE = f"License expired on {data['expiry']}"
            return

        payload = f"{data['product']}|{data['machine_id']}|{data['expiry']}"
        _verify_signature(payload, data["signature"])

        license_state.LICENSE_STATUS = LicenseStatus.VALID
        license_state.LICENSE_MESSAGE = "License valid"
        write_audit_log("[LICENSE] Valid license loaded")

    except Exception as e:
        license_state.LICENSE_STATUS = LicenseStatus.INVALID
        license_state.LICENSE_MESSAGE = f"License error: {e}"
        write_audit_log(f"[LICENSE][ERROR] {e}")
