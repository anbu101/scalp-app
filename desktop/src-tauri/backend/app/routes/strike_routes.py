# routes/strike_routes.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from engine.strike_selector import pick_strikes

router = APIRouter(prefix="/api", tags=["strike"])

class Instrument(BaseModel):
    symbol: Optional[str] = None
    strike: float
    option_type: Optional[str] = Field(None, description="CE or PE")
    last_price: Optional[float] = None
    expiry: Optional[str] = None
    volume: Optional[float] = None
    # allow extra keys
    class Config:
        extra = "allow"

class PickRequest(BaseModel):
    instruments: List[Instrument]
    min_price: float
    max_price: float

class PickResultItem(BaseModel):
    symbol: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    last_price: Optional[float] = None
    expiry: Optional[str] = None
    volume: Optional[float] = None
    raw: Optional[dict] = None

class PickResponse(BaseModel):
    CE: Optional[PickResultItem] = None
    PE: Optional[PickResultItem] = None

@router.post("/pick_strikes", response_model=PickResponse)
async def pick_strikes_endpoint(req: PickRequest):
    if not req.instruments or len(req.instruments) == 0:
        raise HTTPException(status_code=400, detail="No instruments provided")

    # Convert Pydantic models to plain dicts for the selector
    instruments = []
    for inst in req.instruments:
        d = inst.dict()
        # normalize option_type to uppercase if present
        if d.get("option_type") and isinstance(d.get("option_type"), str):
            d["option_type"] = d["option_type"].upper()
        instruments.append(d)

    try:
        res = pick_strikes(instruments, float(req.min_price), float(req.max_price))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"selector error: {str(e)}")

    def _map_item(item):
        if not item:
            return None
        return {
            "symbol": item.get("symbol"),
            "strike": item.get("strike"),
            "option_type": item.get("option_type"),
            "last_price": item.get("last_price"),
            "expiry": item.get("expiry"),
            "volume": item.get("volume"),
            "raw": item
        }

    return {
        "CE": _map_item(res.get("CE")),
        "PE": _map_item(res.get("PE"))
    }
# add to routes/strike_routes.py (below existing POST handler)

from fastapi import Query

@router.get("/strikes/pick")
async def pick_strikes_get(min_price: float = Query(...), max_price: float = Query(...)):
    """
    Convenience GET: supply min_price and max_price as query params.
    NOTE: This uses instruments from a simple internal source — adjust to call your broker fetcher.
    """
    # Minimal example instruments — replace with live fetch later
    instruments = [
        {"symbol":"NIFTY-21500CE","strike":21500,"option_type":"CE","last_price":180,"volume":100},
        {"symbol":"NIFTY-21600CE","strike":21600,"option_type":"CE","last_price":160,"volume":50},
        {"symbol":"NIFTY-21500PE","strike":21500,"option_type":"PE","last_price":170,"volume":200},
        {"symbol":"NIFTY-21600PE","strike":21600,"option_type":"PE","last_price":90,"volume":10}
    ]

    try:
        res = pick_strikes(instruments, float(min_price), float(max_price))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "ce": res.get("CE"),
        "pe": res.get("PE")
    }

# convenience root GET (no /api prefix) — also returns CE/PE like the /api route
@router.get("/strikes/pick", include_in_schema=True)
async def pick_strikes_root(min_price: float = Query(...), max_price: float = Query(...)):
    try:
        # NOTE: for now use same sample instruments as the API GET — replace with live fetch when ready
        instruments = [
            {"symbol":"NIFTY-21500CE","strike":21500,"option_type":"CE","last_price":180,"volume":100},
            {"symbol":"NIFTY-21600CE","strike":21600,"option_type":"CE","last_price":160,"volume":50},
            {"symbol":"NIFTY-21500PE","strike":21500,"option_type":"PE","last_price":170,"volume":200},
            {"symbol":"NIFTY-21600PE","strike":21600,"option_type":"PE","last_price":90,"volume":10}
        ]
        res = pick_strikes(instruments, float(min_price), float(max_price))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ce": res.get("CE"), "pe": res.get("PE")}


from pydantic import BaseModel
from datastore import DataStore

# simple model for saving picks
class SavePicksRequest(BaseModel):
    ce: Optional[Dict[str, Any]] = None
    pe: Optional[Dict[str, Any]] = None

@router.post("/save_picks")
async def save_picks(req: SavePicksRequest):
    """
    Save selected CE/PE into DataStore under key 'selected_strikes'.
    """
    try:
        store = DataStore("trades.db")
        cfg = store.get_config() or {}
        cfg['selected_strikes'] = {"ce": req.ce, "pe": req.pe}
        store.save_config(cfg)
        return {"status": "saved", "selected_strikes": cfg['selected_strikes']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"save error: {str(e)}")


# routes/strike_routes.py  (append near other route functions)
from fastapi import Depends
from typing import Dict, Any

# POST /api/save_selected_strikes
@router.post("/save_selected_strikes")
async def save_selected_strikes(payload: Dict[str, Any]):
    """
    Save selected strikes into the datastore under key 'selected_strikes'.
    Payload expected: { "selected_strikes": { "ce": {...}, "pe": {...} } }
    """
    # main stores don't live in this module — we will import DataStore lazily to avoid cycles
    try:
        from datastore import DataStore
        ds = DataStore()  # if your DataStore expects a filename, adjust: DataStore("trades.db")
    except Exception:
        # If you already create a global `store` in main.py and expose it, prefer importing that.
        ds = None

    if ds is None:
        # Fallback: try to import the central store object if you have one in main
        try:
            from main import store as global_store
            global_store.save_config({"selected_strikes": payload.get("selected_strikes")})
            return {"saved": True, "selected_strikes": payload.get("selected_strikes")}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"no datastore available: {str(e)}")

    # If we have a local DataStore instance
    ds.save_selected_strikes(payload.get("selected_strikes"))
    return {"saved": True, "selected_strikes": payload.get("selected_strikes")}

from fastapi import Request

@router.post("/save_selected_strikes")
async def save_selected_strikes(payload: Dict[str, Any], request: Request):
    """
    Payload should be: { "selected_strikes": {"ce": {...}, "pe": {...}} }
    Saves into DataStore via app.state.store
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="datastore not available")

    selected = payload.get("selected_strikes") if isinstance(payload, dict) else None
    store.save_selected_strikes(selected)
    return {"saved": True, "selected_strikes": selected}

# routes/strike_routes.py  -- append these handlers to the file

from fastapi import Request
from typing import Any

# --- Save full config (payload is plain JSON object) ---
@router.post("/save_config")
async def save_config(payload: Dict[str, Any], request: Request):
    """
    Save full config object to DataStore (merges into existing config).
    Frontend 'Save Config' calls this.
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="datastore not available")

    # payload must be a dict
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    # persist
    store.save_config(payload)
    return {"saved": True, "config": store.get_config()}

# --- Return current config ---
@router.get("/config")
async def get_config(request: Request):
    """
    Return current saved config (used by 'Show Current Config' button).
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="datastore not available")
    return store.get_config() or {}

# --- Save selected strikes (same as earlier suggestion) ---
@router.post("/save_selected_strikes")
async def save_selected_strikes(payload: Dict[str, Any], request: Request):
    """
    Save selected_strikes: expected payload = { "selected_strikes": { "ce": {...}, "pe": {...} } }
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="datastore not available")

    selected = payload.get("selected_strikes") if isinstance(payload, dict) else None
    store.save_selected_strikes(selected)
    return {"saved": True, "selected_strikes": selected or {}}

# --- Get selected strikes ---
@router.get("/selected_strikes")
async def get_selected_strikes(request: Request):
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="datastore not available")
    return store.get_selected_strikes() or {}

