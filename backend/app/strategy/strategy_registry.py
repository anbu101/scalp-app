STRATEGIES = {
    "SCALP_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",          # changed from "5m" → 1-minute candles
        "timeframe_sec": 60,        # changed from 300 → 60 seconds
        "slots": ["CE_1", "CE_2", "PE_1", "PE_2"],
    },
    "BB_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "3m",          # BB uses its own engine — DO NOT CHANGE
        "timeframe_sec": 180,
        "slots": ["CE", "PE"],
    },
    "HA_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        # HA_V1 has no TradeStateManager slots — it manages state internally
        # via HATradeManager._live_trades, exactly like BB_V1.
        "slots": [],
    },
}