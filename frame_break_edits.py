# edit bodies for GC + VET runners, fence FRAME_BREAK_GUARD_20260828
OLD = '''    if lot_size is None:
        return {"run_id": None, "aborted": True,
                "reason": unresolved_reason(underlying),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}
'''

NEW = '''    if lot_size is None:
        return {"run_id": None, "aborted": True,
                "reason": unresolved_reason(underlying),
                "trades": [], "summary": _empty_summary(),
                "config": cfg, "strategy_id": strategy_id}

    # ── FRAME_BREAK_GUARD_20260828 ── refuse a range that crosses a recorded
    # price-frame break. On the as-traded side of a split/bonus the underlying
    # genuinely changes scale overnight; a carried position books an
    # artificial gap that will dominate the P&L and look like a real trade.
    # Fail closed — the override is an explicit corpus_meta edit, not a flag.
    if is_stock:
        try:
            from app.backtest.util.corpus_health import frame_break_reason
        except ImportError:                              # standalone harness
            from corpus_health import frame_break_reason  # type: ignore
        _fb = frame_break_reason(db_path, date_from, date_to)
        if _fb:
            return {"run_id": None, "aborted": True,
                    "reason": f"{underlying}: {_fb}",
                    "trades": [], "summary": _empty_summary(),
                    "config": cfg, "strategy_id": strategy_id}
'''
