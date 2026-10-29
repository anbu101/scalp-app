# backend/app/recorder/rec_routes.py
#
# ── DATA_RECORDER_20261029 ── /api/recorder/* (mounted behind the admin gate
# in api_server.py). Read-mostly: status, backup settings, a manual pull.

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.recorder import rec_db as RD
from app.recorder import rec_jobs as RJ
from app.recorder import rec_pull as RP

router = APIRouter(prefix="/api/recorder", tags=["recorder"])


@router.get("/status")
def status(days: int = 15):
    p = RD.db_path()
    folder, off = RJ.backup_dir()
    out = {"ok": True, "fence": RD.FENCE, "db_path": str(p), "exists": p.exists(),
           "cutover": RD.CUTOVER.isoformat(), "busy": RJ.busy(), "last_pass": RJ.STATE.last_pass,
           "last_error": RJ.STATE.last_error, "backup_dir": str(folder), "backup_off_machine": off,
           "keep": int(RJ.load_settings().get("keep") or RJ.BACKUP_KEEP_DEFAULT), "days": []}
    if not p.exists():
        return out
    wal = p.with_name(p.name + "-wal")
    out["size_mb"] = round((p.stat().st_size + (wal.stat().st_size if wal.exists() else 0)) / 1e6, 1)
    conn = RD.connect(p)
    try:
        cols = ("day", "band_n", "pulled_n", "lost_n", "error_n", "bars", "spot_bars", "completeness", "quality",
                "last_pull_at", "final_at", "attempts", "notes")
        out["days"] = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM day_status ORDER BY day DESC LIMIT ?", (max(1, min(int(days), 120)),))]
        for k in ("last_backup", "moved_post_cutover_options"):
            v = RD.get_meta(conn, k)
            out[k] = json.loads(v) if v else None
        out["session_config_days"] = conn.execute("SELECT COUNT(DISTINCT day) FROM session_config").fetchone()[0]
    finally:
        conn.close()
    return out


class SettingsReq(BaseModel):
    backup_dir: Optional[str] = None
    keep: Optional[int] = None


@router.post("/settings")
def settings(req: SettingsReq):
    new = {}
    if req.backup_dir is not None:
        d = Path(os.path.expanduser(req.backup_dir.strip()))
        if not req.backup_dir.strip():
            raise HTTPException(400, "backup_dir is empty")
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".scalp_recorder_probe"
            probe.write_text("ok")
            probe.unlink()
        except Exception as e:
            raise HTTPException(400, f"cannot write to {d}: {e}")
        new["backup_dir"] = str(d)
    if req.keep is not None:
        if not 1 <= int(req.keep) <= 30:
            raise HTTPException(400, "keep must be 1–30")
        new["keep"] = int(req.keep)
    return {"ok": True, "settings": RJ.save_settings(new)}


class PullReq(BaseModel):
    day: Optional[str] = None


@router.post("/pull")
def pull(req: PullReq):
    """Run a pass now (or pull one specific day) on the recorder's worker."""
    d = None
    if req.day:
        try:
            d = date.fromisoformat(req.day)
        except ValueError:
            raise HTTPException(400, "day must be YYYY-MM-DD")
        if d > RP.ist_date(RP.now_ts()):
            raise HTTPException(400, "day is in the future")
    started = RJ._spawn(RJ.run_pass, force_day=d)
    if not started:
        raise HTTPException(409, "the recorder is already running a pass")
    return {"ok": True, "started": True, "day": req.day}
