STRATEGIES = {
    "SCALP_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "5m",          # changed from "1m" — drives ZerodhaTickEngine
        "timeframe_sec": 300,       # explicit seconds for engine bootstrap
        "slots": ["CE_1", "CE_2", "PE_1", "PE_2"],
    },
    "BB_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "3m",          # BB uses its own engine — DO NOT CHANGE
        "timeframe_sec": 180,
        "slots": ["CE", "PE"],
    },
}