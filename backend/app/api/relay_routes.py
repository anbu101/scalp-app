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
    relays: list

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

    print("🔥 DEPLOY API CALLED")

    relays = req.relays

    if not relays:
        raise HTTPException(
            status_code=400,
            detail="No relays provided"
        )

    for r in relays:
        if not r.get("host") or len(r["host"].split(".")) != 4:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid IP: {r.get('host')}"
            )

        if not r.get("ssh_private_key"):
            raise HTTPException(
                status_code=400,
                detail=f"Missing SSH key for {r.get('host')}"
            )

    write_audit_log(f"[RELAY] Deploy requested for relays={[r['host'] for r in relays]}")

    async def event_stream():
        print("🔥 EVENT STREAM STARTED")
        loop = asyncio.get_running_loop()

        import queue
        q = queue.Queue()

        def on_progress(msg: str):
            print("LOG:", msg, flush=True)
            q.put(msg)

        relays = req.relays

        def run_deploy():
            print("🔥 RUN_DEPLOY STARTED")
            results = []

            for r in relays:
                q.put(f"Connecting to {r['host']}...")

                success, message = deploy_relay(
                    host=r["host"],
                    ssh_username=r.get("ssh_username"),
                    ssh_private_key_text=r["ssh_private_key"],
                    instance_id=r.get("instance_id"),
                    progress_callback=on_progress,
                )

                results.append((success, message))

            overall_success = any(r[0] for r in results)

            q.put("Deployment finished for all relays")

            return overall_success, "Multi-relay deployment completed"

        task = loop.run_in_executor(None, run_deploy)

        # 🔥 STREAM LOOP (FIXED)
        while not task.done() or not q.empty():
            try:
                msg = q.get(timeout=0.5)
                yield json.dumps({
                    "type": "progress",
                    "message": msg
                }) + "\n"
            except:
                await asyncio.sleep(0.1)

        # 🔥 FINAL RESULT (ALWAYS SENT)
        success, message = await task

        yield json.dumps({
            "type": "result",
            "success": success,
            "message": message,
        }) + "\n"

        # NOTE: relay_config.json is written by deploy_relay() itself with the
        # correct per-relay secret that matches each server's RELAY_SECRET env var.
        # DO NOT write it here — overwriting would strip the secret from each relay
        # entry and cause every order to fail with 403 Forbidden.

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