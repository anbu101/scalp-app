#!/usr/bin/env python3
"""
monitor_server/users.py — ── FLEET_MONITOR_20261127 ── (TD-110)
Portal logins, run ON THE DROPLET (there is no sign-up page):

  cd /opt/scalp-monitor && ./venv/bin/python users.py add ravi            # viewer
  ./venv/bin/python users.py add anbu --admin
  ./venv/bin/python users.py list
  ./venv/bin/python users.py password ravi      # new password, signs them out
  ./venv/bin/python users.py totp ravi          # new authenticator code (lost phone)
  ./venv/bin/python users.py disable ravi | enable ravi | unlock ravi

`add` asks for the password twice, then prints the authenticator setup:
a QR code in the terminal (when qrencode is installed) and the setup key to
type into Google Authenticator / Microsoft Authenticator / 1Password.
"""

import getpass
import shutil
import subprocess
import sys
import time

import auth
import store


def _pw() -> str:
    a = getpass.getpass("Password (10+ characters): ")
    b = getpass.getpass("Again: ")
    if a != b:
        sys.exit("Passwords differ — nothing changed.")
    if len(a) < 10:
        sys.exit("Too short — nothing changed.")
    return a


def _show_totp(username: str, secret: str) -> None:
    uri = auth.otpauth_uri(username, secret)
    print(f"\nAuthenticator setup for {username}:")
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", uri])
    print(f"  setup key : {secret}")
    print("  account   : Scalp Fleet Monitor (time-based, 6 digits)\n"
          "Send the key or a photo of the QR to them privately; it is not shown again.")


def main(argv) -> None:
    store.init_db()
    if len(argv) < 2 or argv[1] not in ("add", "list", "password", "totp", "disable", "enable", "unlock"):
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "list":
        with store.conn() as c:
            for r in c.execute("SELECT username, role, disabled, locked_until, created_at FROM users ORDER BY username"):
                flags = (" DISABLED" if r["disabled"] else "") + (" LOCKED" if r["locked_until"] > time.time() else "")
                print(f"{r['username']:<20} {r['role']:<7} since {time.strftime('%Y-%m-%d', time.localtime(r['created_at']))}{flags}")
        return
    if len(argv) < 3:
        sys.exit(__doc__)
    name = argv[2].strip().lower()
    if cmd == "add":
        if auth.get_user(name):
            sys.exit(f"{name} already exists.")
        secret = auth.add_user(name, _pw(), "admin" if "--admin" in argv else "viewer")
        store.audit("cli", "user_add", name)
        _show_totp(name, secret)
        return
    if not auth.get_user(name):
        sys.exit(f"No user {name}.")
    with store.conn() as c:
        if cmd == "password":
            c.execute("UPDATE users SET pw_hash=? WHERE username=?", (auth.hash_password(_pw()), name))
        elif cmd == "totp":
            secret = auth.new_totp_secret()
            c.execute("UPDATE users SET totp_secret=?, last_totp_step=0 WHERE username=?", (secret, name))
        elif cmd == "disable":
            c.execute("UPDATE users SET disabled=1 WHERE username=?", (name,))
        elif cmd == "enable":
            c.execute("UPDATE users SET disabled=0 WHERE username=?", (name,))
        elif cmd == "unlock":
            c.execute("UPDATE users SET failed=0, locked_until=0 WHERE username=?", (name,))
    if cmd in ("password", "totp", "disable"):
        auth.drop_sessions(name)
    store.audit("cli", f"user_{cmd}", name)
    if cmd == "totp":
        _show_totp(name, secret)
    print(f"{cmd}: done for {name}.")


if __name__ == "__main__":
    main(sys.argv)
