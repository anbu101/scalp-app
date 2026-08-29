# edit bodies for backtest_vet_runner.py, fence STOCK_SCREENER_20260828
OLD_DEFAULTS = '''    "warmup_sessions": 10,         # prior sessions seeded before date_from'''
NEW_DEFAULTS = '''    # ── STOCK_SCREENER_20260828 ── optional daily equity screener gate.
    # Default OFF; stock-only. See app/backtest/util/screener.py.
    "screener_enabled": False,
    "screener_ema_fast": 10,
    "screener_ema_slow": 20,
    "screener_sma_trend": 40,
    "screener_vol_sma": 10,
    "screener_min_volume": 2000000,
    "screener_cross_window_days": 1,
    "warmup_sessions": 10,         # prior sessions seeded before date_from'''

OLD_COERCE = '''    cfg["warmup_sessions"] = max(1, int(cfg["warmup_sessions"] or 10))'''
NEW_COERCE = '''    # ── STOCK_SCREENER_20260828 ──
    cfg["screener_enabled"] = bool(cfg.get("screener_enabled", False))
    for _k, _lo, _dflt in (("screener_ema_fast", 1, 10),
                           ("screener_ema_slow", 1, 20),
                           ("screener_sma_trend", 1, 40),
                           ("screener_vol_sma", 1, 10),
                           ("screener_cross_window_days", 1, 1)):
        cfg[_k] = max(_lo, int(cfg.get(_k) or _dflt))
    cfg["screener_min_volume"] = max(0, int(cfg.get("screener_min_volume") or 0))
    cfg["warmup_sessions"] = max(1, int(cfg["warmup_sessions"] or 10))'''

OLD_DIAG = '''        "premium_veto_entries": 0, "premium_pct_veto_entries": 0,'''
NEW_DIAG = '''        "premium_veto_entries": 0, "premium_pct_veto_entries": 0,
        "screener_veto_entries": 0,          # STOCK_SCREENER_20260828'''

OLD_GATE_ANCHOR = '''            elif (cfg["max_trades_per_day"] > 0
                  and day_entries >= cfg["max_trades_per_day"]):
                blocked = "cap_blocked_entries"'''
NEW_GATE_ANCHOR = '''            elif (cfg["max_trades_per_day"] > 0
                  and day_entries >= cfg["max_trades_per_day"]):
                blocked = "cap_blocked_entries"
            elif screener_allowed is not None and not screener_allowed.get(d):
                # ── STOCK_SCREENER_20260828 ── the day was not selected by
                # the daily scan. Gate is fail-closed: a day with no gate
                # data is a day with no entry, never a day that trades
                # ungated. Exits, rolls and EOD are UNAFFECTED — a position
                # opened on a selected day must be managed to its own exit.
                blocked = "screener_veto_entries"'''

OLD_BUILD_ANCHOR = '''    _state_now = {"cond": 0}'''
NEW_BUILD_ANCHOR = '''    # ── STOCK_SCREENER_20260828 ── build the per-day gate once, up front.
    screener_allowed = None
    if cfg["screener_enabled"]:
        if not is_stock:
            diag["screener"] = {"skipped": "index underlying — screener is "
                                           "stock-only (equity volume filters)"}
        else:
            try:
                from app.backtest.util.screener import build_gate
            except ImportError:                       # standalone harness
                from screener import build_gate       # type: ignore
            _g = build_gate(db_path, underlying, date_from=date_from,
                            date_to=date_to, cfg=cfg)
            screener_allowed = _g["allowed"]
            diag["screener"] = {k: v for k, v in _g.items() if k != "allowed"}
            if not _g["warmup_ok"]:
                return {"run_id": None, "aborted": True,
                        "reason": (f"{underlying}: screener needs "
                                   f"{_g['warmup_required']} daily bars before "
                                   f"{date_from}, corpus has "
                                   f"{_g['warmup_bars']}. Start the run later "
                                   f"or backfill earlier spot data."),
                        "trades": [], "summary": _empty_summary(),
                        "config": cfg, "strategy_id": strategy_id}

    _state_now = {"cond": 0}'''

OLD_SUMMARY = '''        + f"skips: noStrike {diag['no_strike_entries']} / "'''
NEW_SUMMARY = '''        + (f"screener {diag['screener_veto_entries']} / "
           if cfg["screener_enabled"] else "")      # STOCK_SCREENER_20260828
        + f"skips: noStrike {diag['no_strike_entries']} / "'''
