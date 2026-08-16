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
    # ==================================================
    # PST_SELL / PST_HEDGE — PAPER PHASE (change-set B)
    # Spot-signal strategies (pivot+SMA+SuperTrend). One shared standalone
    # async loop in api_server serves BOTH (one WebSocket, one signal
    # engine, two managers). Managers are PAPER-HARDWIRED — LIVE mode
    # self-disables until Phase 2. Own tables pst_sell_trades /
    # pst_hedge_trades; slots=[].
    # ==================================================
    "PST_SELL": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    "PST_HEDGE": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
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
    # ── SCALP_V5 BEGIN ──
    # ==================================================
    # SCALP_V5 — TEST option-BUYING strategy on 3-minute candles.
    # Reuses SCALP_V1's indicator engine (EMA8/EMA20_low/EMA20_high/RSI) with a
    # 4-gate LONG entry filter (green ∧ close>ema8 ∧ ema20_low<ema8 ∧
    # ema8>ema20_high) and absolute-point SL/TP (0 = disabled). Buys the
    # signalling contract itself (NO hedge). Exit = first of SL/TP/EMA_EXIT (candle
    # candle close)/MTM/EOD. Manages ALL state itself in scalpv5_trades, so there
    # are NO TradeStateManager slots (slots=[]). Launched as a STANDALONE async
    # selection loop in api_server (NOT via StrategyRuntimeManager) — exactly
    # like SCALP_V3 — so the startup strategy loop skips it.
    #
    # TEST strategy. enabled=False until you want it running; the api_server
    # defer-check + standalone launch both gate on this flag. To REMOVE: delete
    # this entry, the app/engine/scalpv5/ package, app/jobs/scalpv5_live_eod.py,
    # app/api/scalpv5_state_routes.py, app/db/scalpv5_repo.py, the SCALP_V5
    # default in strategy_loader, and DROP the scalpv5_trades table.
    # ==================================================
    "SCALP_V5": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "3m",
        "timeframe_sec": 180,
        "slots": [],
    },
    # ── SCALP_V5 END ──
    # ── IC BEGIN (IC_SPLIT: shared V1/V2) ──
    # ==================================================
    # IC — Time-entry premium-defined IRON CONDOR on NIFTY weekly options.
    # NO signal pipeline: one scheduled entry/day (default 09:18), 2 shorts
    # (~₹85 premium, 42% SL, Move-To-Cost) + 2 protective wings (~₹4). Manages
    # ALL state itself via ICGroupManager (backed by the shared trades /
    # paper_trades tables, slot=L1..L4), so there are NO TradeStateManager
    # slots (slots=[]). Launched as STANDALONE async runtimes in api_server
    # (NOT via StrategyRuntimeManager) — the startup strategy loop skips both.
    # timeframe is nominal only (no candle pipeline; REST LTP poll).
    #
    # IC_SPLIT (2026-08-04): TWO instances of ONE shared engine package
    # (app/engine/ic/), differing only by per-strategy config:
    #   IC_V1 — legacy condor: exit_mode=EOD, no adjustments, no carry
    #           (backtest IC_V1 parity).
    #   IC_V2 — exit_mode=NEXT_OPEN (ONE_NIGHT_MAX) + ADJ_ON_MTC
    #           (backtest IC_V2 parity; the pre-split live behavior).
    #
    # Config defaults ship trade_execution_mode=OFF: deploying this wiring
    # changes nothing until a mode is flipped in Settings. To REMOVE: delete
    # these entries, the app/engine/ic/ package, app/jobs/ic_live_eod.py,
    # app/api/ic_state_routes.py, and the IC defaults in strategy_loader.
    # ==================================================
    "IC_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    "IC_V2": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    # ── IC END ──
    # ── TMA_V1 BEGIN ──
    # ==================================================
    # TMA_V1 — Triple-EMA (5/13/89 @5m NIFTY SPOT) trend-following CREDIT
    # SPREAD on weekly options: SELL leg (highest premium ≤ cap, side
    # OPPOSITE the trend) + BUY hedge (same side, deeper OTM). Signals are
    # parity-by-construction: the live engine re-runs the BACKTEST's own
    # build_signals over the growing day prefix with a 3-session EMA warmup
    # (TMA_XDAY_WARMUP). Manages ALL state itself in tma_trades (slots=[]).
    # Launched as a STANDALONE async selection loop in api_server (NOT via
    # StrategyRuntimeManager) — exactly like PST — with its OWN KiteTicker.
    #
    # Defaults ship trade_execution_mode=PAPER (per the frozen build spec):
    # deploying this wiring starts PAPER trading next session; LIVE is a
    # Settings flip. To REMOVE: delete this entry, the app/engine/tma/
    # package, app/jobs/tma_live_eod.py, app/api/tma_state_routes.py, the
    # TMA_V1 default in strategy_loader, and DROP the tma_trades table.
    # ==================================================
    "TMA_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    # ── TMA_V1 END ──
    # ── TSG_V1 BEGIN ──
    # ==================================================
    # TSG_V1 — Time StranGle: daily 09:16 entry, 2 shorts (premium ≤ 85) +
    # 2 wings (≤ 5) on the NIFTY weekly. No per-leg SL/TP — exits are
    # basket-level (day-MTM SL, one-shot IV breaker Δ+4 pts over entry IV,
    # EOD 15:26), all evaluated at 1m closes for backtest parity (LD2).
    # Backtest-validated 2026-08-02 (₹46.06L/6.5y, walk-forward PASS).
    # STANDALONE async runtime in api_server (IC pattern), state in
    # ~/.scalp-app/state/TSG_V1_session.json + paper_trades rows
    # (slots=[]). Ships PAPER (LD10 Phase 1); LIVE is a Settings flip
    # gated by resolve_execution_mode. To REMOVE: delete this entry, the
    # app/engine/tsg/ package, app/jobs/tsg_live_eod.py,
    # app/api/tsg_v1_state_routes.py, and the strategy_loader default.
    # ==================================================
    "TSG_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    # ── GC_V1 BEGIN ──
    # ==================================================
    # GC_V1 — Glacier: NIFTY spot 1m C1 breakout→retest entries with
    # SL-flip re-entry chain; option BUY (signal side) or SELL (opposite
    # side, ₹-cap hedge) on the front weekly. Decisions ONLY at 1m closes:
    # the live core REPLAYS the backtest engine (gc_v1_engine) over the
    # day's candles and diffs — parity by construction (LD6). Backtest-
    # validated infra 2026-08-15; NIFTY paper campaign gates any LIVE.
    # STANDALONE async runtime (DAY_CYCLE perpetual arm→day→teardown),
    # state in ~/.scalp-app/state/GC_V1_session.json + paper_trades rows
    # (slots=[]). Ships PAPER (LD15). To REMOVE: delete this entry, the
    # app/engine/gc/ package, app/jobs/gc_live_eod.py,
    # app/api/gc_v1_state_routes.py, and the strategy_loader default.
    # ==================================================
    "GC_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "slots": [],
    },
    # ── GC_V1 END ──
    # ── TSG_V1 END ──
}