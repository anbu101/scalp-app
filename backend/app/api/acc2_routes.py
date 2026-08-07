# backend/app/api/acc2_routes.py
# ============================================================
# ACC2 BEGIN — Secondary account (Angel One) API routes
#
#   POST /api/acc2/credentials   save creds (all users; own machine)
#   GET  /api/acc2/status        connection card payload
#   POST /api/acc2/force-login   D3 Force Login button
#   GET  /api/acc2/bindings     per-strategy account bindings + sides
#   POST /api/acc2/bindings     save bindings; returns D8 conflict list
#                                (client shows two-tap warning when the
#                                caller did not pass confirm=true)
#
# The AngelManager singleton lives in executor_factory so the executor
# and these routes share ONE session object.
# ============================================================

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Optional

from app.config.account_bindings import (
    STRATEGY_SIDE,
    VALID_BROKERS,
    conflict_check,
    load_bindings,
    save_bindings,
)
from app.config.angel_credentials_store import (
    clear_credentials,
    is_enabled,
    load_credentials,
    save_credentials,
)
from app.event_bus.audit_logger import write_audit_log
from app.execution.executor_factory import get_angel_manager

router = APIRouter(prefix="/api/acc2", tags=["acc2"])


# --------------------------------------------------
# CREDENTIALS + STATUS
# --------------------------------------------------

class Acc2Credentials(BaseModel):
    api_key: str
    client_code: str
    pin: str
    totp_secret: str
    enabled: bool = True


@router.post("/credentials")
def post_credentials(body: Acc2Credentials):
    save_credentials(body.api_key, body.client_code, body.pin,
                     body.totp_secret, body.enabled)
    # Immediate login attempt so the card reflects reality right away.
    mgr = get_angel_manager()
    ok = mgr.refresh()
    return {"saved": True, "connected": ok, **mgr.status()}


@router.delete("/credentials")
def delete_credentials():
    clear_credentials()
    mgr = get_angel_manager()
    mgr.refresh()  # resets to not-ready state cleanly
    return {"cleared": True}


@router.get("/status")
def get_status():
    mgr = get_angel_manager()
    creds = load_credentials()
    return {
        **mgr.status(),
        "configured": creds is not None,
        "enabled": is_enabled(),
        "client_code": (creds or {}).get("client_code"),
    }


@router.post("/force-login")
def force_login():
    """D3: user-triggered full re-login (safe + idempotent)."""
    write_audit_log("[ACC2] Force Login requested")
    mgr = get_angel_manager()
    ok = mgr.refresh()
    return {"connected": ok, **mgr.status()}


# --------------------------------------------------
# BINDINGS (D2c) + D8 LAYER-1 CONFLICT CHECK
# --------------------------------------------------

class BindingsBody(BaseModel):
    bindings: Dict[str, str]
    confirm: bool = False  # true after the two-tap warning was accepted


@router.get("/bindings")
def get_bindings():
    b = load_bindings()
    return {
        "bindings": b,
        "sides": STRATEGY_SIDE,
        "valid_brokers": list(VALID_BROKERS),
        "conflicts": conflict_check(b),
    }


@router.post("/bindings")
def post_bindings(body: BindingsBody):
    clean = {k: v for k, v in body.bindings.items()
             if v in VALID_BROKERS and k in STRATEGY_SIDE}
    conflicts = conflict_check(clean)
    # D8 layer 1 fires only on NEWLY-INTRODUCED conflicts. Today's
    # everything-on-Zerodha reality is already a buy+sell mix; nagging a
    # user whose save doesn't worsen it teaches them to ignore the
    # warning that matters.
    existing = set(conflict_check(load_bindings()))
    new_conflicts = sorted(set(conflicts) - existing)
    if new_conflicts and not body.confirm:
        # Soft guard — do NOT save; client shows the netting warning and
        # re-posts with confirm=true if the user proceeds.
        return {"saved": False, "needs_confirm": True,
                "conflicts": new_conflicts}
    save_bindings(clean)
    write_audit_log(
        f"[ACC2] Bindings saved {clean} conflicts={conflicts} "
        f"confirmed={body.confirm}")
    return {"saved": True, "needs_confirm": False, "conflicts": conflicts,
            "bindings": clean}

# ACC2 END