from enum import Enum

class LicenseStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"

LICENSE_STATUS: LicenseStatus = LicenseStatus.MISSING
LICENSE_MESSAGE: str = "License not checked"
