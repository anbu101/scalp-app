"""
monitor_server/accounts.py — ── PORTAL_USERS_ADMIN_20261127 ── (TD-113)

Portal accounts managed from the License Admin page (license server, :9100),
through the internal API in server.py. Nobody types a friend's password: an
invite is a one-time link (INVITE_TTL_S, used once) that the friend opens on
the HTTPS portal to choose a password and set up the authenticator. A reset
is the same link for an existing account.

  invite(username, role, by)     new account → link   (refused if the name exists)
  reset(username, by)            existing account → link (old login works until used)
  pending(token)                 the invite row for a live token, else None
  complete(token, pw, pw2, code) finish setup → login session, or an error string
  listing()                      accounts + pending invites for the admin page
  set_disabled / unlock / remove / cancel_invite
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import auth
import store

INVITE_TTL_S = 48 * 3600
MAX_TRIES = 10                      # wrong code / password attempts before a link is burnt
_tries: Dict[str, int] = {}
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
PORTAL_URL = "https://139-59-8-202.sslip.io"

SCHEMA = """
CREATE TABLE IF NOT EXISTS invites (
  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('new','reset')), totp_secret TEXT NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL, used_at REAL, created_by TEXT);
CREATE INDEX IF NOT EXISTS invites_user ON invites (username);
"""


def init() -> None:
    with store.conn() as c:
        c.executescript(SCHEMA)


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_name(username: str) -> Optional[str]:
    u = (username or "").strip().lower()
    return u if USERNAME.match(u) else None


def _new_link(c, username: str, role: str, mode: str, by: str, now: float) -> Dict[str, Any]:
    # one live invite per name: a new link replaces any unused one
    c.execute("DELETE FROM invites WHERE username=? AND used_at IS NULL", (username,))
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO invites (token_hash, username, role, mode, totp_secret, created, expires, created_by)"
              " VALUES (?,?,?,?,?,?,?,?)",
              (_th(token), username, role, mode, auth.new_totp_secret(), now, now + INVITE_TTL_S, by[:40]))
    return {"username": username, "role": role, "mode": mode, "expires": now + INVITE_TTL_S,
            "url": f"{PORTAL_URL}/setup/{token}"}


def invite(username: str, role: str, by: str = "admin", now: Optional[float] = None) -> Union[Dict[str, Any], str]:
    now = time.time() if now is None else now
    u = clean_name(username)
    if not u:
        return "Username: 2–32 characters, a–z 0–9 . _ - (starting with a letter or digit)."
    if role not in ("viewer", "admin"):
        return "Role must be viewer or admin."
    if auth.get_user(u):
        return f"{u} already has an account — use Reset to send them a new link."
    with store.conn() as c:
        out = _new_link(c, u, role, "new", by, now)
    store.audit(by, "invite", f"{u} ({role})")
    return out


def reset(username: str, by: str = "admin", now: Optional[float] = None) -> Union[Dict[str, Any], str]:
    now = time.time() if now is None else now
    usr = auth.get_user(username)
    if not usr:
        return "No such account."
    with store.conn() as c:
        out = _new_link(c, usr["username"], usr["role"], "reset", by, now)
    store.audit(by, "reset_link", usr["username"])
    return out


def pending(token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = time.time() if now is None else now
    if not token or len(token) > 100:
        return None
    with store.conn() as c:
        r = c.execute("SELECT * FROM invites WHERE token_hash=?", (_th(token),)).fetchone()
    if r is None or r["used_at"] is not None or r["expires"] < now:
        return None
    return dict(r)


def complete(token: str, password: str, password2: str, code: str, ip: str,
             now: Optional[float] = None) -> Tuple[Optional[Dict[str, str]], str]:
    """(session, "") on success, else (None, message)."""
    now = time.time() if now is None else now
    inv = pending(token, now)
    if inv is None:
        return None, "This link has expired or was already used. Ask Anbu for a new one."
    err = ""
    step = None
    if (password or "") != (password2 or ""):
        err = "The two passwords are different."
    elif len(password or "") < 10:
        err = "Choose a password of at least 10 characters."
    else:
        step = auth.totp_step_ok(inv["totp_secret"], code, 0, now)
        if step is None:
            err = "That authenticator code is not right. Scan the code again and enter the 6 digits it shows now."
    if err:
        th = _th(token)
        _tries[th] = _tries.get(th, 0) + 1
        if _tries[th] >= MAX_TRIES:
            with store.conn() as c:
                c.execute("UPDATE invites SET used_at=? WHERE token_hash=?", (now, th))
            store.audit(inv["username"], "invite_burnt", f"{MAX_TRIES} failed tries", ip)
            return None, "Too many tries — this link no longer works. Ask Anbu for a new one."
        return None, err
    u = inv["username"]
    with store.conn() as c:
        cur = c.execute("UPDATE invites SET used_at=? WHERE token_hash=? AND used_at IS NULL", (now, _th(token)))
        if cur.rowcount != 1:
            return None, "This link was just used. Ask Anbu for a new one."
        if inv["mode"] == "new":
            if c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
                return None, "This account already exists. Ask Anbu for a reset link."
            c.execute("INSERT INTO users (username, pw_hash, totp_secret, role, created_at, last_totp_step)"
                      " VALUES (?,?,?,?,?,?)", (u, auth.hash_password(password), inv["totp_secret"],
                                                inv["role"], now, step))
        else:
            c.execute("UPDATE users SET pw_hash=?, totp_secret=?, last_totp_step=?, failed=0, locked_until=0"
                      " WHERE username=?", (auth.hash_password(password), inv["totp_secret"], step, u))
            c.execute("DELETE FROM sessions WHERE username=?", (u,))
        tok = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        c.execute("INSERT INTO sessions (token_hash, username, csrf, created, last_seen, ip) VALUES (?,?,?,?,?,?)",
                  (_th(tok), u, csrf, now, now, ip[:64]))
    store.audit(u, "setup_" + inv["mode"], "", ip)
    return {"token": tok, "csrf": csrf, "username": u}, ""


def listing(now: Optional[float] = None) -> Dict[str, List[Dict[str, Any]]]:
    now = time.time() if now is None else now
    with store.conn() as c:
        users = [dict(r) for r in c.execute(
            "SELECT u.username, u.role, u.disabled, u.locked_until, u.created_at,"
            " (SELECT MAX(ts) FROM audit a WHERE a.username=u.username AND a.action IN ('login','setup_new','setup_reset'))"
            " AS last_login FROM users u ORDER BY u.username")]
        invs = [dict(r) for r in c.execute(
            "SELECT username, role, mode, created, expires, created_by FROM invites"
            " WHERE used_at IS NULL AND expires>=? ORDER BY created DESC", (now,))]
    for u in users:
        u["status"] = ("disabled" if u["disabled"] else "locked" if u["locked_until"] > now else "active")
        u["pending_reset"] = any(i["username"] == u["username"] for i in invs)
    return {"users": users, "invites": invs}


def set_disabled(username: str, disabled: bool, by: str = "admin") -> bool:
    with store.conn() as c:
        n = c.execute("UPDATE users SET disabled=? WHERE username=?", (1 if disabled else 0, username)).rowcount
        if disabled:
            c.execute("DELETE FROM sessions WHERE username=?", (username,))
    if n:
        store.audit(by, "disable" if disabled else "enable", username)
    return bool(n)


def unlock(username: str, by: str = "admin") -> bool:
    with store.conn() as c:
        n = c.execute("UPDATE users SET failed=0, locked_until=0 WHERE username=?", (username,)).rowcount
    if n:
        store.audit(by, "unlock", username)
    return bool(n)


def remove(username: str, by: str = "admin") -> bool:
    with store.conn() as c:
        n = c.execute("DELETE FROM users WHERE username=?", (username,)).rowcount
        c.execute("DELETE FROM sessions WHERE username=?", (username,))
        c.execute("DELETE FROM invites WHERE username=? AND used_at IS NULL", (username,))
    if n:
        store.audit(by, "remove", username)
    return bool(n)


def cancel_invite(username: str, by: str = "admin") -> bool:
    with store.conn() as c:
        n = c.execute("DELETE FROM invites WHERE username=? AND used_at IS NULL", (username,)).rowcount
    if n:
        store.audit(by, "cancel_invite", username)
    return bool(n)


def qr_svg(data: str) -> Optional[str]:
    """Inline SVG of a QR code, or None when the qrcode package is missing."""
    try:
        import qrcode
        import qrcode.image.svg as svg
        img = qrcode.make(data, image_factory=svg.SvgPathImage, box_size=8, border=2)
        s = img.to_string()
        s = s.decode() if isinstance(s, bytes) else s
        s = re.sub(r"^<\?xml[^>]*>\s*", "", s)
        return s
    except Exception:
        return None
