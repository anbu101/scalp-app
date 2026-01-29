from fastapi import APIRouter
from app.license import license_state
from app.license.license_state import LicenseStatus

router = APIRouter(prefix="/system", tags=["system"])

@router.get("/license")
def get_license_status():
    return {
        "status": license_state.LICENSE_STATUS.name,
        "message": license_state.LICENSE_MESSAGE,
    }
