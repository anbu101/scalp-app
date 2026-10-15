# backend/app/api/backtest_portfolio_routes.py
#
# ── PF_SAVED_PDF_20261008 ── Backtest → Portfolio: saved portfolios + PDF.
#
#   GET    /api/backtest/portfolios               list saved portfolios (+ per-leg presence)
#   POST   /api/backtest/portfolios               save {name, run_ids, note?, replace?}
#                                                 409 on a name clash unless replace=true
#   DELETE /api/backtest/portfolios/{pf_id}       delete the saved portfolio (runs untouched)
#   POST   /api/backtest/portfolios/report.pdf    render the page's composed figures as a PDF
#
# Mounted in api_server.py with the same admin gate as the Backtest router.
# The PDF route is layout-only: the body carries the numbers the page already
# computed (see app/backtest/report/portfolio_pdf.py). A copy of every PDF is
# kept under ~/.scalp-app/backtest/reports/portfolio/ so it can be found again
# if the in-app download is dismissed.

from __future__ import annotations

import re
import time
from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.event_bus.audit_logger import write_audit_log

router = APIRouter(prefix="/api/backtest/portfolios", tags=["backtest"])

MAX_PAYLOAD_CHARS = 8_000_000


class SavePortfolioRequest(BaseModel):
    name: str
    run_ids: List[str]
    note: Optional[str] = None
    replace: bool = False


@router.get("")
def list_saved():
    from app.backtest.repo.portfolio_repo import list_portfolios
    return {"portfolios": list_portfolios()}


@router.post("")
def save(req: SavePortfolioRequest):
    from app.backtest.repo.portfolio_repo import PortfolioError, save_portfolio
    try:
        pf, replaced = save_portfolio(req.name, req.run_ids, note=req.note, replace=req.replace)
    except PortfolioError as e:
        raise HTTPException(e.status, str(e))
    write_audit_log(f"[BACKTEST][PORTFOLIO] {'replaced' if replaced else 'saved'} "
                    f"\"{pf['name']}\" ({len(pf['run_ids'])} runs)")
    return {"ok": True, "replaced": replaced, "portfolio": pf}


def _safe_name(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_-]+", "_", str(s or "")).strip("_")
    return (s or "portfolio")[:60]


@router.post("/report.pdf")
def report_pdf(payload: dict = Body(...)):
    import json
    from app.backtest.report.portfolio_pdf import render_portfolio_pdf
    try:
        if len(json.dumps(payload)) > MAX_PAYLOAD_CHARS:
            raise HTTPException(413, "report payload too large")
    except (TypeError, ValueError):
        raise HTTPException(400, "report payload is not valid JSON")
    try:
        pdf = render_portfolio_pdf(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:   # renderer bug → visible, never a silent empty file
        write_audit_log(f"[BACKTEST][PORTFOLIO][PDF][ERROR] {e!r}")
        raise HTTPException(500, f"PDF render failed: {type(e).__name__}: {e}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    per = payload.get("period") or {}
    fname = f"portfolio_{_safe_name(payload.get('name') or 'untitled')}_{_safe_name(per.get('from'))}_to_" \
            f"{_safe_name(per.get('to'))}_{stamp}.pdf"
    saved = ""
    try:
        from app.utils.app_paths import APP_HOME
        d = APP_HOME / "backtest" / "reports" / "portfolio"
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_bytes(pdf)
        saved = str(d / fname)
    except Exception as e:   # the download still works without the copy
        write_audit_log(f"[BACKTEST][PORTFOLIO][PDF] copy not saved: {e!r}")
    write_audit_log(f"[BACKTEST][PORTFOLIO][PDF] {fname} ({len(pdf)} bytes)")
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Report-File": fname,
        "X-Report-Path": saved,
        "Access-Control-Expose-Headers": "X-Report-File, X-Report-Path, Content-Disposition",
    })


@router.delete("/{pf_id}")
def delete(pf_id: str):
    from app.backtest.repo.portfolio_repo import delete_portfolio
    n = delete_portfolio(pf_id)
    if n:
        write_audit_log(f"[BACKTEST][PORTFOLIO] deleted {pf_id}")
    return {"ok": True, "pf_id": pf_id, "deleted": int(n)}
