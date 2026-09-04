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
    # ── TMA_V2 BEGIN ──
    # ==================================================
    # TMA_V2 — four-EMA STACK (13/55/89/144 @5m NIFTY SPOT) trend-following
    # CREDIT SPREAD on weekly options: SELL leg (highest premium <= cap,
    # side OPPOSITE the trend) + BUY hedge (same side, deeper OTM). ONE
    # open position at a time in EITHER direction (backtest D2 — a single
    # slot shared by E1 and E2). Signals are parity-by-construction: the
    # live engine re-runs the BACKTEST's own build_signals_v2 over the
    # growing day prefix with a FIVE-session EMA warmup (EMA144 seed
    # depth). Manages ALL state itself in tma2_trades (slots=[]).
    # Launched as a STANDALONE async selection loop in api_server (NOT via
    # StrategyRuntimeManager) — exactly like TMA_V1/PST — with its OWN
    # KiteTicker.
    #
    # Defaults ship trade_execution_mode=PAPER: deploying this wiring
    # starts PAPER trading next session; LIVE is a Settings flip. To
    # REMOVE: delete this entry, the app/engine/tma2/ package,
    # app/jobs/tma2_live_eod.py, app/api/tma2_state_routes.py, the TMA_V2
    # default in strategy_loader, and DROP the tma2_trades table.
    # ==================================================
    "TMA_V2": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",
        "timeframe_sec": 60,
        "slots": [],
    },
    # ── TMA_V2 END ──
    # ── BRK_V1 BEGIN ──
    # ==================================================
    # BRK_V1 — 09:25 premium breakout scalp (sealed 2026-09-02, see
    # docs/BRK_V1_Strategy_Bible_20260902.pdf). At 09:25 pick the CE and PE
    # printing nearest-below ₹180 on the expected weekly; whichever side's
    # 09:29 1m CLOSE holds ≥ ₹180 is BOUGHT at 09:30. SL −16 / TP +46 on the
    # bought premium, EOD 15:15. One trade per session; the optional second
    # session (Config B: 10:25→10:30, only-if-flat, only-after-losing-
    # morning) is a SETTING (s2_enabled), not a separate strategy.
    #
    # LIVE EXITS (LD4): in LIVE, SL+TP are ONE two-leg (OCO) GTT placed at
    # entry; every engine-side exit (EOD/KILL) cancels it VERIFIED first
    # (the fleet GTT-race doctrine). PAPER exits are engine ticks.
    #
    # REMOVAL RECIPE: delete the fenced BRK_V1 blocks (grep "── BRK_V1"),
    # the app/engine/brk package, app/api/brk_v1_state_routes.py,
    # app/jobs/brk_live_eod.py, the BRK_V1 default in strategy_loader, the
    # kill_switch entry and the admin_ui ALL_STRATEGIES id. No private
    # table exists (paper_trades rows are harmless history).
    # ==================================================
    "BRK_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",          # decisions ONLY at 1m closes (LD2)
        "timeframe_sec": 60,
        "slots": [],                # standalone runtime (LD5)
    },
    # ── BRK_V1 END ──
    # ── ORB_V1 BEGIN ── static 15m opening-range breakout, long weekly
    # options, engine-only exits at 1m closes (premium TP +50%/+60%,
    # spot-close SL 0.04%, EOD 13:00). Sealed 2026-09-03; see
    # docs/ORB_V1_BIBLE.pdf. Standalone runtime (slots []), no GTT layer.
    # REMOVAL: delete this entry + the ORB_V1 blocks in strategy_loader,
    # api_server, kill_switch, telegram_api, admin_ui.html; drop
    # app/engine/orb + api/orb_state_routes + jobs/orb_live_eod.
    "ORB_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",          # decisions ONLY at 1m closes (LD2)
        "timeframe_sec": 60,
        "slots": [],                # standalone runtime (LD5)
    },
    # ── ORB_V1 END ──
    # ── VET_V1 BEGIN ──
    # ==================================================
    # VET_V1 — Vivek Equity Tool: dual-EMA(10/20) + SMA(40)±ATR×0.618
    # regime channel on 5m NIFTY SPOT. Transition-only signals; a FLAT
    # (in-channel) bar CARRIES the condition and therefore HOLDS an open
    # position (RANGE-HOLD) rather than closing it. One position at a time.
    #
    # FOUR SEALED CONFIGS, ONE RUNTIME. leg_action (BUY|SELL), eod_square
    # (intraday|positional) and the hedge wing are SETTINGS, not separate
    # strategies. Defaults = NIFTY Buy B intraday unhedged (the safest).
    #
    # NO SL/TP AND NO GTT LAYER. All four sealed configs run sl_pct=0 /
    # tp_pct=0 — exits are FLIP, SIGNAL_EXIT, EXPIRY_EXIT and (intraday)
    # EOD, all decided by the engine at 5m closes. Adding a live stop would
    # be a parity break, so there is deliberately no GTT machinery here;
    # the kill path is correspondingly simple (flatten both legs).
    #
    # Signals are parity-by-construction: the live engine re-runs the
    # BACKTEST's own resample_spot + vet_states over the growing day prefix
    # with a 10-session warmup, guarded for prefix stability (freezes and
    # emits nothing on any drift). Manages ALL state itself in vet_trades
    # (slots=[]). Launched as a STANDALONE async loop in api_server, like
    # TMA_V2/PST, with its own KiteTicker.
    #
    # Defaults ship trade_execution_mode=PAPER. To REMOVE: delete this
    # entry, app/engine/vet/, app/jobs/vet_live_eod.py,
    # app/api/vet_state_routes.py, the VET_V1 default in strategy_loader,
    # the OVERNIGHT_EXEMPT entry, and DROP the vet_trades table.
    # ==================================================
    "VET_V1": {
        "enabled": True,
        "broker": "ZERODHA",
        "timeframe": "1m",          # ticks fold to 1m; decisions at 5m
        "timeframe_sec": 60,
        "slots": [],
    },
    # ── VET_V1 END ──
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
    # ── TSG_V1 END ──
}