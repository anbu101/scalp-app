"""
monitor_server/auth.py — ── FLEET_MONITOR_20261127 ── (TD-110)

Portal logins: username + password + a 6-digit authenticator code (TOTP,
RFC 6238, SHA-1, 30 s, ±1 step, a code is accepted once). stdlib only.

  password   scrypt (n=2**14, r=8, p=1, 16-byte salt), constant-time compare
  lockout    5 failed logins → that user is locked for 15 min;
             30 failed logins from one IP in an hour → that IP waits
  session    32 random bytes in an HttpOnly + Secure + SameSite=Strict cookie;
             only its sha256 is stored; 12 h idle, 7 days at most
  csrf       a per-session token in every form, checked on every POST
  roles      viewer (read + acknowledge) · admin (also pause/resume an install)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Dict, List, Optional

import store

LOCK_AFTER = 5
LOCK_S = 15 * 60
IP_LIMIT = 30
IP_WINDOW_S = 3600
IDLE_S = 12 * 3600
MAX_AGE_S = 7 * 86400
COOKIE = "sm_session"

_ip_fails: Dict[str, List[float]] = {}


# ── passwords ────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(bytes.fromhex(h)))
        return hmac.compare_digest(got, bytes.fromhex(h))
    except Exception:
        return False


# ── TOTP ─────────────────────────────────────────────────────────────
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_step_ok(secret: str, code: str, last_step: int, now: Optional[float] = None) -> Optional[int]:
    """The matching step (> last_step, so a code works once) or None."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    cur = int((time.time() if now is None else now) // 30)
    for st in (cur - 1, cur, cur + 1):
        if st > last_step and hmac.compare_digest(totp_at(secret, st), code):
            return st
    return None


def otpauth_uri(username: str, secret: str) -> str:
    return f"otpauth://totp/Scalp%20Fleet%20Monitor:{username}?secret={secret}&issuer=Scalp%20Fleet%20Monitor"


# ── users ────────────────────────────────────────────────────────────
def add_user(username: str, password: str, role: str = "viewer") -> str:
    if role not in ("admin", "viewer"):
        raise ValueError("role must be admin or viewer")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    secret = new_totp_secret()
    with store.conn() as c:
        c.execute("INSERT INTO users (username, pw_hash, totp_secret, role, created_at) VALUES (?,?,?,?,?)",
                  (username.strip().lower(), hash_password(password), secret, role, time.time()))
    return secret


def get_user(username: str) -> Optional[dict]:
    with store.conn() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),)).fetchone()
    return dict(r) if r else None


def _ip_blocked(ip: str, now: float) -> bool:
    xs = [t for t in _ip_fails.get(ip, []) if t > now - IP_WINDOW_S]
    _ip_fails[ip] = xs
    return len(xs) >= IP_LIMIT


def login(username: str, password: str, code: str, ip: str, now: Optional[float] = None) -> Optional[Dict[str, str]]:
    """{"token", "csrf", "username"} on success, else None. Every failure
    looks the same to the caller (no hint which part was wrong)."""
    now = time.time() if now is None else now
    if _ip_blocked(ip, now):
        store.audit(username, "login_ip_blocked", "", ip)
        return None
    u = get_user(username)
    ok = False
    step = None
    if u and not u["disabled"] and u["locked_until"] <= now:
        if check_password(password or "", u["pw_hash"]):
            step = totp_step_ok(u["totp_secret"], code, int(u["last_totp_step"]), now)
            ok = step is not None
    elif not u:
        check_password(password or "", hash_password("timing-equaliser"))
    if not ok:
        _ip_fails.setdefault(ip, []).append(now)
        if u:
            failed = int(u["failed"]) + 1
            locked = now + LOCK_S if failed >= LOCK_AFTER else u["locked_until"]
            with store.conn() as c:
                c.execute("UPDATE users SET failed=?, locked_until=? WHERE username=?",
                          (0 if failed >= LOCK_AFTER else failed, locked, u["username"]))
        store.audit(username, "login_failed", "", ip)
        return None
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    with store.conn() as c:
        c.execute("UPDATE users SET failed=0, locked_until=0, last_totp_step=? WHERE username=?",
                  (step, u["username"]))
        c.execute("INSERT INTO sessions (token_hash, username, csrf, created, last_seen, ip) VALUES (?,?,?,?,?,?)",
                  (_th(token), u["username"], csrf, now, now, ip[:64]))
    store.audit(u["username"], "login", "", ip)
    return {"token": token, "csrf": csrf, "username": u["username"]}


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session(token: Optional[str], now: Optional[float] = None) -> Optional[dict]:
    """{"username", "role", "csrf"} for a live session, else None."""
    if not token:
        return None
    now = time.time() if now is None else now
    with store.conn() as c:
        r = c.execute("SELECT s.*, u.role, u.disabled FROM sessions s JOIN users u ON u.username=s.username"
                      " WHERE s.token_hash=?", (_th(token),)).fetchone()
        if r is None:
            return None
        if r["disabled"] or now - r["last_seen"] > IDLE_S or now - r["created"] > MAX_AGE_S:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))
            return None
        if now - r["last_seen"] > 60:
            c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, _th(token)))
    return {"username": r["username"], "role": r["role"], "csrf": r["csrf"]}


def logout(token: Optional[str]) -> None:
    if token:
        with store.conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


def drop_sessions(username: str) -> None:
    with store.conn() as c:
        c.execute("DELETE FROM sessions WHERE username=?", (username,))
