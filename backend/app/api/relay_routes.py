# backend/app/api/relay_routes.py
"""
Relay Routes
============
API endpoints for the OCI order relay feature.
Called from the Connections page UI.
"""

import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json

from app.services.relay_deployer import (
    deploy_relay,
    get_relay_status,
    disable_relay,
)
from app.event_bus.audit_logger import write_audit_log

router = APIRouter(prefix="/api/relay", tags=["relay"])


# --------------------------------------------------
# MODELS
# --------------------------------------------------

class DeployRelayRequest(BaseModel):
    host: str                    # OCI public IP, e.g. "144.24.159.177"
    ssh_username: str            # Almost always "ubuntu" for OCI Ubuntu instances
    ssh_private_key: str         # Full PEM key text pasted by user


class DisableRelayRequest(BaseModel):
    pass


# --------------------------------------------------
# GET STATUS
# --------------------------------------------------

@router.get("/status")
def relay_status():
    """
    Returns current relay configuration and whether it's reachable.
    Called by the Connections page on load and every 30s.
    """
    return get_relay_status()


# --------------------------------------------------
# DEPLOY (streaming progress)
# --------------------------------------------------

@router.post("/deploy")
async def relay_deploy(req: DeployRelayRequest):
    """
    SSHes into the OCI instance and deploys the relay service.
    Streams progress messages as newline-delimited JSON so the UI
    can show a live step-by-step progress indicator.
    """

    # Validate IP looks reasonable
    host = req.host.strip()
    if not host or len(host.split(".")) != 4:
        raise HTTPException(
            status_code=400,
            detail="Invalid IP address. Enter the Public IPv4 from your OCI instance."
        )

    if not req.ssh_private_key.strip():
        raise HTTPException(
            status_code=400,
            detail="SSH private key is required."
        )

    write_audit_log(f"[RELAY] Deploy requested for host={host}")

    async def event_stream():
        loop = asyncio.get_event_loop()
        steps = []

        def on_progress(msg: str):
            steps.append(msg)

        # Run the blocking SSH deployment in a thread pool
        # so we don't block the event loop
        success, message = await loop.run_in_executor(
            None,
            lambda: deploy_relay(
                host=host,
                ssh_username=req.ssh_username.strip() or "ubuntu",
                ssh_private_key_text=req.ssh_private_key.strip(),
                progress_callback=on_progress,
            ),
        )

        # Yield all collected progress steps
        for step in steps:
            yield json.dumps({"type": "progress", "message": step}) + "\n"

        # Final result
        yield json.dumps({
            "type": "result",
            "success": success,
            "message": message,
        }) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
    )


# --------------------------------------------------
# DISABLE
# --------------------------------------------------

@router.post("/disable")
def relay_disable():
    """
    Disables the relay without deleting config.
    Orders will go direct (will fail from April 1 if IP not registered).
    """
    try:
        disable_relay()
        return {"success": True, "message": "Relay disabled."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))