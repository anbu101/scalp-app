"""
license_server/signing.py

Ed25519 JWT minting for the Scalp License Server.

Token rules (LOCKED in tracker):
  - Lifetime: 4 days from issue (offline grace window for the desktop app)
  - BUT never outlives the license itself: exp = min(now + 4d, license expiry)
  - Algorithm: EdDSA (Ed25519)

The desktop app verifies these tokens offline with the embedded public key.
"""

import time
from pathlib import Path

import jwt  # PyJWT
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# --------------------------------------------------
# CONSTANTS
# --------------------------------------------------

TOKEN_LIFETIME_SECONDS = 4 * 24 * 3600  # 4 days
ALGORITHM = "EdDSA"

# --------------------------------------------------
# KEY LOADING (once, at server startup)
# --------------------------------------------------

_private_key = None


def load_private_key(secrets_dir: Path):
    """Load the Ed25519 private key. Called once from server startup."""
    global _private_key
    pem = (secrets_dir / "private_key.pem").read_bytes()
    _private_key = load_pem_private_key(pem, password=None)
    return _private_key


# --------------------------------------------------
# TOKEN MINTING
# --------------------------------------------------

def mint_token(
    *,
    license_key: str,
    machine_id: str,
    tier: str,
    entitlements: dict,
    license_expiry_epoch: int,
) -> dict:
    """
    Returns {"token": <jwt>, "exp": <epoch>, "iat": <epoch>}.

    exp is capped at the license's own expiry so a token can never grant
    access beyond the license end date.
    """
    if _private_key is None:
        raise RuntimeError("Private key not loaded - call load_private_key() first")

    now = int(time.time())
    exp = min(now + TOKEN_LIFETIME_SECONDS, license_expiry_epoch)

    payload = {
        "sub": license_key,
        "machine_id": machine_id,
        "tier": tier,
        "entitlements": entitlements,
        "iat": now,
        "exp": exp,
        "server_time": now,  # explicit copy for the client's clock-tamper guard
    }

    token = jwt.encode(payload, _private_key, algorithm=ALGORITHM)
    return {"token": token, "exp": exp, "iat": now}