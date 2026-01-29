from pydantic import BaseModel
from typing import Optional

class StartRequest(BaseModel):
    symbol: str = "NIFTY"
    lots: int = 1
    lot_size: int = 65
    paper_mode: bool = True
    # additional strategy params can be persisted

class SignalRequest(BaseModel):
    symbol: str
    qty: int = 1
    price: float
    lookback: int = 240