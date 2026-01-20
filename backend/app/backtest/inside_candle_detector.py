def detect_inside_candle(prev_candle, curr_candle):
    """
    prev_candle = mother
    curr_candle = inside

    Returns True if INSIDE_CANDLE pattern is valid.
    """

    # Same day check
    if prev_candle["ts"] // 86400 != curr_candle["ts"] // 86400:
        return False

    # Mother must be green (bullish only)
    if prev_candle["close"] <= prev_candle["open"]:
        return False

    # Inside candle range
    if not (
        curr_candle["high"] <= prev_candle["high"]
        and curr_candle["low"] >= prev_candle["low"]
    ):
        return False

    # Volume condition
    if prev_candle["volume"] is None or curr_candle["volume"] is None:
        return False

    if prev_candle["volume"] <= curr_candle["volume"]:
        return False

    return True
