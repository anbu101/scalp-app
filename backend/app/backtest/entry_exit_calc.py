def compute_entry_sl_tp(mother_candle, rr=1):
    entry = mother_candle["high"]
    sl = mother_candle["low"]

    risk = entry - sl
    if risk <= 0:
        return None

    tp = entry + (risk * rr)

    return {
        "entry_price": entry,
        "sl_price": sl,
        "tp_price": tp,
        "rr": rr,
    }
