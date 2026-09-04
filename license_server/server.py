"""
license_server/server.py

Scalp License Server - FastAPI app + uvicorn entry point.
(Entry point deliberately NOT named main.py - backend/main.py already
exists in this repo for the PyInstaller desktop bundle.)

Runs on the primary DigitalOcean droplet alongside the relay, as its own
systemd service (scalp-license.service), own port (default 9100), own
SQLite DB. Plain HTTP by design: security lives in the Ed25519-signed
tokens (unforgeable without the private key on this box), not transport.

Endpoints:
  POST /activate              key + machine_id -> bind machine -> 4-day signed token
  POST /heartbeat             key + machine_id -> validate -> fresh 4-day token
  POST /admin/*               create / revoke / unrevoke / extend / update / rebind
  POST /admin/set_min_version set the lowest app version considered current
  GET  /admin/list            all licenses (admin)
  GET  /admin/ui              admin web dashboard
  GET  /min_version           PUBLIC - app self-update nudge (advisory only)
  GET  /health                uptime check

Admin endpoints require header:  X-Admin-Secret: <secrets/admin_secret.txt>
(constant-time compared). The Telegram admin bot (Phase 4) is the caller.

Statuses returned to the app (this is the FIXED Phase 2 contract):
  ok | unknown_key | revoked | expired | machine_mismatch | not_activated
"""

import hmac
import json   # ── CFG_OVERRIDE ── size validation
import os
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import db
import liveness_watch   # ── LIVENESS 2026-09-01 ──
import notify
import server_meta
import signing

# --------------------------------------------------
# CONFIG (env-overridable)
# --------------------------------------------------

PORT = int(os.environ.get("LICSRV_PORT", "9100"))
HOST = os.environ.get("LICSRV_HOST", "0.0.0.0")
SECRETS_DIR = Path(os.environ.get("LICSRV_SECRETS_DIR", Path(__file__).resolve().parent / "secrets"))

# --------------------------------------------------
# STARTUP: keys + secret + DB
# --------------------------------------------------

signing.load_private_key(SECRETS_DIR)
ADMIN_SECRET = (SECRETS_DIR / "admin_secret.txt").read_text().strip()
db.init_db()
server_meta.init_meta()
notify.start_expiry_watcher(db.list_licenses)
liveness_watch.start_watcher()   # ── LIVENESS 2026-09-01 ── dead-man's switch

app = FastAPI(title="Scalp License Server", docs_url=None, redoc_url=None)

# --------------------------------------------------
# MODELS
# --------------------------------------------------

class StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class DeviceRequest(StrictModel):
    key: str = Field(min_length=8, max_length=64)
    machine_id: str = Field(min_length=8, max_length=128)


# ── LIVENESS 2026-09-01 ── telegram is OPTIONAL (dict or null); holidays
# come from the app's own NSE calendar so the droplet keeps no copy.
class LivenessRequest(StrictModel):
    key: str = Field(min_length=8, max_length=64)
    machine_id: str = Field(min_length=8, max_length=128)
    label: str = Field(default="", max_length=64)
    ts: int = 0
    telegram: dict | None = None
    holidays: list = Field(default_factory=list)


class CreateRequest(StrictModel):
    label: str = Field(min_length=1, max_length=120)
    tier: str = "STANDARD"
    days: int | None = None            # None -> tier default (ADMIN 3650 / STANDARD 90 / TRIAL 7)
    strategies: list[str] | None = None  # None -> tier default
    max_lots: int | None = None          # None -> 0 (unlimited)
    notes: str = ""
    phone: str = Field(default="", max_length=32)     # ── CONTACT_INFO ── optional
    email: str = Field(default="", max_length=128)    # ── CONTACT_INFO ── optional
    # config_overrides removed — role-level now (see /admin/global_overrides)


class KeyRequest(StrictModel):
    key: str


class ExtendRequest(StrictModel):
    key: str
    days: int = Field(gt=0, le=3650)


class SetMinVersionRequest(StrictModel):
    min_version: str = Field(min_length=1, max_length=32)


class UpdateRequest(StrictModel):
    key: str
    tier: str | None = None                 # ADMIN / STANDARD / TRIAL
    strategies: list[str] | None = None     # full replacement list, e.g. ["SCALP_V1","BB_V2"]
    max_lots: int | None = None
    live_trading: bool | None = None
    ui_level: str | None = None             # "admin" | "standard"
    expires_at: str | None = None           # SET expiry directly (YYYY-MM-DD); use /admin/extend to add days
    notes: str | None = None
    phone: str | None = Field(default=None, max_length=32)    # ── CONTACT_INFO ── None = untouched
    email: str | None = Field(default=None, max_length=128)   # ── CONTACT_INFO ── None = untouched
    # config_overrides removed — role-level now (see /admin/global_overrides)


# ── CFG_OVERRIDE BEGIN ────────────────────────────────────────────────
# D6: server-side validation. Shape-level only — the admin is the sole
# author, so value semantics are trusted; this guard catches typos and
# keeps the signed token (which carries entitlements verbatim) small.
KNOWN_STRATEGY_IDS = {
    "SCALP_V1", "SCALP_V3", "SCALP_V5", "IC_V1", "IC_V2", "TSG_V1",
    "BB_V1", "BB_V2", "HA_V1", "PST_SELL", "PST_HEDGE", "TMA_V1", "TMA_V2",
    "VET_V1",   # ── VET_V1 added 2026-08-29 — missing id = 400 on override save
    "ORB_V1",   # ── ORB_V1 added 2026-09-03 — missing id = 400 on override save
    "BRK_V1",   # ── BRK_V1 added 2026-09-02 — missing id = 400 on override save
}   # ── IC_SPLIT ── IC_V2 added 2026-08-06: it was missing, so a global
    # override for IC_V2 was rejected 400 — keep in sync with the app
CONFIG_OVERRIDES_MAX_BYTES = 8192

# ── MAX_LOTS ── default per-field lots cap for non-admin licenses
DEFAULT_NON_ADMIN_MAX_LOTS = 5


_STRATEGY_DEFAULTS_FILE = Path(__file__).parent / "strategy_defaults.json"
_strategy_defaults_cache: dict | None = None


def _load_strategy_defaults() -> dict:
    """Lots paths + trade_execution_mode are pre-stripped at generation
    (gen_strategy_defaults.py) — regenerate + redeploy the JSON whenever
    DEFAULT_STRATEGY_CONFIGS changes in the app."""
    global _strategy_defaults_cache
    if _strategy_defaults_cache:
        return _strategy_defaults_cache
    # Never cache a failed/empty read — if the JSON lands on disk later
    # (e.g. deployed after server start), the next request picks it up
    # without a service restart.
    try:
        data = json.loads(_STRATEGY_DEFAULTS_FILE.read_text())
        if isinstance(data, dict) and data:
            _strategy_defaults_cache = data
            return data
    except Exception:
        pass
    return {}


# ── CONTACT_INFO ── minimal shape guard; both fields are optional and
# admin-entered, so this only catches obvious typos, not formats.
def _validate_contact(phone: str | None, email: str | None) -> None:
    if email and "@" not in email:
        raise HTTPException(status_code=400, detail="email must contain '@'")


def _validate_config_overrides(co: dict) -> None:
    if not isinstance(co, dict):
        raise HTTPException(status_code=400,
                            detail="config_overrides must be an object")
    unknown = sorted(set(co.keys()) - KNOWN_STRATEGY_IDS)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"config_overrides: unknown strategy id(s) {unknown}")
    for sid, patch in co.items():
        if not isinstance(patch, dict):
            raise HTTPException(
                status_code=400,
                detail=f"config_overrides[{sid}] must be an object")
    size = len(json.dumps(co, separators=(",", ":")))
    if size > CONFIG_OVERRIDES_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"config_overrides too large ({size}B > "
                   f"{CONFIG_OVERRIDES_MAX_BYTES}B) — it rides inside the "
                   f"signed token, keep it lean")
# ── CFG_OVERRIDE END ──────────────────────────────────────────────────


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def _require_admin(x_admin_secret: str | None):
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret, ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="invalid admin secret")


def _denied(status: str, message: str) -> dict:
    """Denial responses are HTTP 200 with an explicit status - the desktop
    client switches on `status`, and transport-level errors stay reserved
    for actual connectivity problems (grace-window logic depends on this)."""
    return {"status": status, "message": message, "server_time": int(time.time())}


def _issue(lic: dict, machine_id: str) -> dict:
    # ── CFG_OVERRIDE (role-level) BEGIN ──
    # ONE global override set for ALL non-admin licenses, injected at mint
    # time. Clients are untouched: they already read
    # entitlements.config_overrides from the signed token. Admin tokens
    # never carry it (client-side D3 immunity is the backstop). Any
    # config_overrides stored per-license is ignored — superseded here.
    ent = dict(lic["entitlements"])
    ent.pop("config_overrides", None)   # kill legacy per-license values
    if ent.get("ui_level") != "admin":
        g = db.get_global_overrides()
        if g:
            ent["config_overrides"] = g
        # ── MAX_LOTS ── every non-admin token carries a lots cap. Legacy
        # licenses stored max_lots 0 (= "unlimited" in the old semantics);
        # floor those to the default so the cap is retroactive without a DB
        # migration. NOTE: for non-admin there is no "unlimited" anymore —
        # to effectively uncap a user, set a large explicit value (e.g. 999).
        if not ent.get("max_lots"):
            ent["max_lots"] = DEFAULT_NON_ADMIN_MAX_LOTS
    # ── CFG_OVERRIDE (role-level) END ──
    minted = signing.mint_token(
        license_key=lic["key"],
        machine_id=machine_id,
        tier=lic["tier"],
        entitlements=ent,
        license_expiry_epoch=db.expiry_epoch(lic["expires_at"]),
    )
    db.touch_heartbeat(lic["key"])
    return {
        "status": "ok",
        "token": minted["token"],
        "token_exp": minted["exp"],
        "server_time": minted["iat"],
        "tier": lic["tier"],
        "entitlements": ent,
        "license_expires_at": lic["expires_at"],
        "label": lic["label"],
    }


def _validate_common(lic: dict | None) -> dict | None:
    """Checks shared by activate + heartbeat. Returns a denial dict or None."""
    if lic is None:
        return _denied("unknown_key", "License key not found")
    if lic["revoked"]:
        return _denied("revoked", "License has been revoked")
    if db.is_expired(lic["expires_at"]):
        return _denied("expired", f"License expired on {lic['expires_at']}")
    return None


# --------------------------------------------------
# DEVICE ENDPOINTS
# --------------------------------------------------

@app.post("/activate")
def activate(req: DeviceRequest):
    lic = db.get_license(req.key.strip().upper())
    denial = _validate_common(lic)
    if denial:
        return denial

    if lic["machine_id"] and lic["machine_id"] != req.machine_id:
        return _denied(
            "machine_mismatch",
            "License is already activated on another machine. "
            "Ask the admin to rebind it.",
        )

    if not lic["machine_id"]:
        db.bind_machine(lic["key"], req.machine_id)
        lic = db.get_license(lic["key"])
        # First-time binding -> ping the admin on Telegram (fire-and-forget)
        notify.notify_activation(lic["label"], lic["tier"], lic["key"], req.machine_id)

    return _issue(lic, req.machine_id)


@app.post("/heartbeat")
def heartbeat(req: DeviceRequest):
    lic = db.get_license(req.key.strip().upper())
    denial = _validate_common(lic)
    if denial:
        return denial

    if not lic["machine_id"]:
        return _denied("not_activated", "License not activated - call /activate first")

    if lic["machine_id"] != req.machine_id:
        return _denied("machine_mismatch", "License is bound to a different machine")

    return _issue(lic, req.machine_id)


# ── LIVENESS 2026-09-01 ── dead-man's switch. Same key/machine validation
# as /heartbeat; on success the record is stored and the watcher thread
# (liveness_watch) decides whether to alert. Never issues a license token.
@app.post("/liveness")
def liveness(req: LivenessRequest):
    lic = db.get_license(req.key.strip().upper())
    denial = _validate_common(lic)
    if denial:
        return denial
    if not lic["machine_id"] or lic["machine_id"] != req.machine_id:
        return _denied("machine_mismatch", "License is bound to a different machine")
    tg = req.telegram if isinstance(req.telegram, dict) else None
    if tg is not None:
        tg = {"bot_token": str(tg.get("bot_token") or "")[:128],
              "chat_id": str(tg.get("chat_id") or "")[:64]}
    liveness_watch.record(req.key.strip().upper(), req.machine_id,
                          req.label, tg, [str(h)[:10] for h in req.holidays][:120])
    return {"ok": True}


@app.get("/admin/liveness")
def admin_liveness(x_admin_secret: str = Header(default="")):
    if not hmac.compare_digest(x_admin_secret, ADMIN_SECRET):
        raise HTTPException(403, "forbidden")
    return liveness_watch.snapshot()


# --------------------------------------------------
# ADMIN ENDPOINTS
# --------------------------------------------------

@app.post("/admin/create")
def admin_create(req: CreateRequest, x_admin_secret: str | None = Header(default=None)):
    _require_admin(x_admin_secret)
    _validate_contact(req.phone, req.email)   # ── CONTACT_INFO ──
    override = {}
    if req.strategies is not None:
        override["strategies"] = req.strategies
    if req.max_lots is not None:
        override["max_lots"] = req.max_lots
    try:
        lic = db.create_license(
            label=req.label,
            tier=req.tier,
            days=req.days,
            entitlements_override=override or None,
            notes=req.notes,
            phone=req.phone.strip(),   # ── CONTACT_INFO ──
            email=req.email.strip(),   # ── CONTACT_INFO ──
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "license": lic}


class GlobalOverridesRequest(StrictModel):
    config_overrides: dict


@app.get("/admin/global_overrides")
def admin_get_global_overrides(x_admin_secret: str | None = Header(default=None)):
    """── CFG_OVERRIDE (role-level) ── current set for ALL non-admin users."""
    _require_admin(x_admin_secret)
    return {"status": "ok", "config_overrides": db.get_global_overrides()}


@app.post("/admin/global_overrides")
def admin_set_global_overrides(req: GlobalOverridesRequest,
                               x_admin_secret: str | None = Header(default=None)):
    """── CFG_OVERRIDE (role-level) ── replace the set. {} clears (stop
    enforcing; sticky values remain on client machines per D1). Reaches
    every non-admin machine at its next heartbeat (≤6h) or app restart."""
    _require_admin(x_admin_secret)
    _validate_config_overrides(req.config_overrides)
    db.set_global_overrides(req.config_overrides)
    return {"status": "ok", "config_overrides": db.get_global_overrides()}


@app.get("/admin/strategy_defaults")
def admin_strategy_defaults(x_admin_secret: str | None = Header(default=None)):
    """── CFG_OVERRIDE ── default config templates for the overrides editor."""
    _require_admin(x_admin_secret)
    return {"status": "ok", "defaults": _load_strategy_defaults()}


@app.get("/admin/list")
def admin_list(x_admin_secret: str | None = Header(default=None)):
    _require_admin(x_admin_secret)
    return {"status": "ok", "licenses": db.list_licenses()}


@app.post("/admin/revoke")
def admin_revoke(req: KeyRequest, x_admin_secret: str | None = Header(default=None)):
    _require_admin(x_admin_secret)
    if not db.set_revoked(req.key.strip().upper(), True):
        raise HTTPException(status_code=404, detail="key not found")
    return {"status": "ok"}


@app.post("/admin/unrevoke")
def admin_unrevoke(req: KeyRequest, x_admin_secret: str | None = Header(default=None)):
    _require_admin(x_admin_secret)
    if not db.set_revoked(req.key.strip().upper(), False):
        raise HTTPException(status_code=404, detail="key not found")
    return {"status": "ok"}


@app.post("/admin/extend")
def admin_extend(req: ExtendRequest, x_admin_secret: str | None = Header(default=None)):
    _require_admin(x_admin_secret)
    lic = db.extend_license(req.key.strip().upper(), req.days)
    if not lic:
        raise HTTPException(status_code=404, detail="key not found")
    return {"status": "ok", "license": lic}


@app.post("/admin/update")
def admin_update(req: UpdateRequest, x_admin_secret: str | None = Header(default=None)):
    """Update tier / entitlements / expiry on an EXISTING license.
    Reaches the running app at its next heartbeat (<=6h); strategy launch
    changes take effect at the app's next restart."""
    _require_admin(x_admin_secret)
    if req.ui_level is not None and req.ui_level not in ("admin", "standard"):
        raise HTTPException(status_code=400, detail="ui_level must be 'admin' or 'standard'")
    _validate_contact(req.phone, req.email)   # ── CONTACT_INFO ──
    patch = {}
    if req.strategies is not None:
        patch["strategies"] = req.strategies
    if req.max_lots is not None:
        patch["max_lots"] = req.max_lots
    if req.live_trading is not None:
        patch["live_trading"] = req.live_trading
    if req.ui_level is not None:
        patch["ui_level"] = req.ui_level
    try:
        lic = db.update_license(
            req.key.strip().upper(),
            tier=req.tier,
            expires_at=req.expires_at,
            entitlements_patch=patch or None,
            notes=req.notes,
            phone=req.phone.strip() if req.phone is not None else None,   # ── CONTACT_INFO ──
            email=req.email.strip() if req.email is not None else None,   # ── CONTACT_INFO ──
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if lic is None:
        raise HTTPException(status_code=404, detail="key not found")
    return {"status": "ok", "license": lic}


@app.post("/admin/rebind")
def admin_rebind(req: KeyRequest, x_admin_secret: str | None = Header(default=None)):
    _require_admin(x_admin_secret)
    if not db.rebind(req.key.strip().upper()):
        raise HTTPException(status_code=404, detail="key not found")
    return {"status": "ok", "message": "Machine binding cleared - next /activate rebinds"}


@app.post("/admin/set_min_version")
def admin_set_min_version(req: SetMinVersionRequest, x_admin_secret: str | None = Header(default=None)):
    """Set the lowest app version considered current. Apps below this show
    a soft 'update available' banner (advisory only - never blocks trading).
    Set it to a version you have actually published, never ahead of it."""
    _require_admin(x_admin_secret)
    server_meta.set_min_version(req.min_version.strip())
    return {"status": "ok", "min_version": server_meta.get_min_version()}


# --------------------------------------------------
# ADMIN WEB DASHBOARD
# --------------------------------------------------
# Single static page; the page itself is public, but every data call it
# makes hits /admin/* and therefore requires the X-Admin-Secret header,
# entered on the page's login screen (held in sessionStorage only).

ADMIN_UI_FILE = Path(__file__).resolve().parent / "admin_ui.html"


@app.get("/admin/ui")
def admin_ui():
    if ADMIN_UI_FILE.exists():
        return HTMLResponse(ADMIN_UI_FILE.read_text())
    return HTMLResponse(
        "<h3>admin_ui.html not found next to server.py — "
        "re-run the deploy script (it copies it).</h3>",
        status_code=404,
    )


# --------------------------------------------------
# MIN VERSION (app self-update nudge)
# --------------------------------------------------
# PUBLIC read: the desktop app calls this at startup and compares against
# its own version. It is advisory only on the client (fail-open) — the app
# shows an "update available" banner, it does NOT stop trading. Returns
# {"min_version": null} when unset (meaning: no minimum, all versions OK).

@app.get("/min_version")
def min_version():
    return {"min_version": server_meta.get_min_version()}


# --------------------------------------------------
# HEALTH
# --------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "scalp-license", "server_time": int(time.time())}


# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)