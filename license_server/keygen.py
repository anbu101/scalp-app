#!/usr/bin/env python3
"""
license_server/keygen.py

ONE-TIME setup script for the Scalp License Server.

Generates:
  secrets/private_key.pem   Ed25519 private key  (NEVER leaves the droplet)
  secrets/public_key.pem    Ed25519 public key   (embed in desktop app - Phase 2)
  secrets/admin_secret.txt  Shared secret for /admin/* endpoints (Telegram bot uses this)

Run once on the droplet:
    python3 keygen.py

Refuses to overwrite existing keys (a new keypair would invalidate every
token already issued). Use --force only if you really mean to rotate.
"""

import argparse
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_SECRETS_DIR = Path(__file__).resolve().parent / "secrets"


def main():
    ap = argparse.ArgumentParser(description="Generate license server keys + admin secret")
    ap.add_argument("--secrets-dir", default=str(DEFAULT_SECRETS_DIR))
    ap.add_argument("--force", action="store_true", help="Overwrite existing keys (DANGEROUS)")
    args = ap.parse_args()

    sdir = Path(args.secrets_dir)
    sdir.mkdir(parents=True, exist_ok=True)

    priv_path = sdir / "private_key.pem"
    pub_path = sdir / "public_key.pem"
    adm_path = sdir / "admin_secret.txt"

    if priv_path.exists() and not args.force:
        print(f"[KEYGEN] REFUSING to overwrite existing {priv_path}")
        print("[KEYGEN] A new keypair invalidates ALL previously issued tokens.")
        print("[KEYGEN] Re-run with --force only if you intend to rotate keys.")
        sys.exit(1)

    # ----- Ed25519 keypair -----
    key = Ed25519PrivateKey.generate()

    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.write_bytes(priv_pem)
    priv_path.chmod(0o600)
    pub_path.write_bytes(pub_pem)

    # ----- Admin shared secret -----
    admin_secret = secrets.token_hex(32)
    adm_path.write_text(admin_secret + "\n")
    adm_path.chmod(0o600)

    print("[KEYGEN] Done.\n")
    print(f"  Private key : {priv_path}  (chmod 600 - never copy off this machine)")
    print(f"  Public key  : {pub_path}")
    print(f"  Admin secret: {adm_path}  (chmod 600)\n")
    print("=" * 64)
    print("PUBLIC KEY - paste this into the desktop app in Phase 2:")
    print("=" * 64)
    print(pub_pem.decode())
    print("=" * 64)
    print("ADMIN SECRET - store in your password manager; the Telegram")
    print("admin bot will send it as the X-Admin-Secret header:")
    print("=" * 64)
    print(admin_secret)


if __name__ == "__main__":
    main()