from datetime import datetime, timedelta

def compute_cpr_pivot(prev_day):
    """
    prev_day: dict with keys open, high, low, close
    returns CPR + Pivot levels
    """
    high = prev_day["high"]
    low = prev_day["low"]
    close = prev_day["close"]

    pivot = (high + low + close) / 3
    bc = (high + low) / 2
    tc = (pivot * 2) - bc

    r1 = (2 * pivot) - low
    s1 = (2 * pivot) - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)
    r3 = high + 2 * (pivot - low)
    s3 = low - 2 * (high - pivot)

    return {
        "pivot": pivot,
        "tc": tc,
        "bc": bc,
        "R": [r1, r2, r3],
        "S": [s1, s2, s3],
    }
