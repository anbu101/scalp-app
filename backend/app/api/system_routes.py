# backend/app/api/system_routes.py
"""
PHASE 2 REPLACEMENT - extends /system/license with tier/entitlements and
adds the activation endpoint. Response stays backward compatible (status
+ message fields unchanged) so existing LicenseBanner keeps working
until the new frontend ships.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.license import license_state
from app.license import license_client

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/license")
def get_license_status():
    return {
        "status": license_state.LICENSE_STATUS.name,
        "message": license_state.LICENSE_MESSAGE,
        "tier": license_state.TIER,
        "label": license_state.LABEL,
        "ui_level": license_state.ui_level(),
        "entitlements": license_state.ENTITLEMENTS,
        "license_expires_at": license_state.LICENSE_EXPIRES_AT,
        "grace_days_left": license_state.grace_days_left(),
    }

from app.license import version_check

@router.get("/system/version")
def system_version():
    return version_check.snapshot()
# -> {"update_available": bool, "min_version": str|None,
#     "current_version": str|None, "message": str}

class ActivateRequest(BaseModel):
    key: str

# ── ADD THIS to backend/app/api/system_routes.py (both trees) ────────
#
# The /system/version route the UpdateBanner reads. Mirrors how
# /system/license is served in this same file — a plain GET on the
# existing `router`. It just returns the advisory snapshot the
# version_check module already computed at startup; it does no work and
# never fails (snapshot() reads module state only).

from app.license import version_check


@router.get("/system/version")
def system_version():
    return version_check.snapshot()
    # -> {"update_available": bool, "min_version": str|None,
    #     "current_version": str|None, "message": str}
    
@router.post("/license/activate")
def activate_license(req: ActivateRequest):
    """
    Body: {"key": "SCLP-XXXX-XXXX-XXXX"}
    Returns {"status": "ok"|<denial>|"error", "message": "..."}.
    On "ok" the UI tells the user to restart the app (Option A:
    strategies launch at startup only).
    """
    return license_client.activate(req.key)