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
    "BB_V2": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "3m",
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
    # ==================================================
    # SCALP_V2 — 3-class order-splitting short strategy
    # Model B: the group manager owns ALL leg state, so there are NO
    # TradeStateManager slots (slots=[]). SCALP_V2 is launched as a
    # standalone async selection loop in api_server (NOT via
    # StrategyRuntimeManager), so the startup strategy loop skips it.
    # Same 1-minute candle cadence as SCALP_V1.
    # ==================================================
    "SCALP_V2": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    # ==================================================
    # SCALP_V3 — TEST option-BUYING hedge clone of SCALP_V1
    # Signals on one contract (e.g. 24500CE, TRACKED, never traded) and BUYS the
    # highest-premium OPPOSITE-side selected option (e.g. 24450PE) protected by
    # an SL-only GTT. One trade at a time, DB-backed gate. Manages ALL state
    # itself in scalp_v3_trades, so there are NO TradeStateManager slots
    # (slots=[]). Launched as a STANDALONE async selection loop in api_server
    # (NOT via StrategyRuntimeManager) — exactly like SCALP_V2 — so the startup
    # strategy loop skips it. Same 1-minute candle cadence as SCALP_V1.
    #
    # This is a TEST strategy. To turn it OFF cleanly, set enabled=False (the
    # api_server defer-check + standalone launch both gate on this flag). To
    # REMOVE it entirely: delete this entry, the app/engine/scalp_v3/ package,
    # app/jobs/scalp_v3_live_eod.py, the SCALP_V3 default in strategy_loader,
    # and DROP the scalp_v3_trades table.
    # ==================================================
    "SCALP_V3": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
}