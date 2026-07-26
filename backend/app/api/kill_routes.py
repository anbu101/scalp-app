# backend/app/api/kill_routes.py
#
# KILL SWITCH routes (2026-07-26). Thin HTTP shell over
# app/execution/kill_switch.py — all doctrine (LIVE-only gating, mode flip
# strictly after verified-flat, per-strategy locks) lives there.
#
#   GET  /api/kill/eligibility   → { SID: {eligible, mode, reason, in_flight} }
#   POST /api/kill/{strategy_id} → kill report (see kill_switch.kill)
#
# The POST runs synchronously in FastAPI's threadpool — adapters place
# market orders and return; typical latency is a few seconds. The UI's
# two-tap confirm is the consent step; there is no extra confirm here.
# ============================================================================

from fastapi import APIRouter

from app.execution import kill_switch

router = APIRouter()


@router.get("/api/kill/eligibility")
def kill_eligibility():
    return kill_switch.eligibility()


@router.post("/api/kill/{strategy_id}")
def kill_strategy(strategy_id: str):
    return kill_switch.kill(strategy_id)
