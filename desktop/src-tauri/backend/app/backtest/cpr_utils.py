def compute_cpr(prev_high, prev_low, prev_close):
    pivot = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = 2 * pivot - bc

    return {
        "pivot": pivot,
        "tc": max(tc, bc),
        "bc": min(tc, bc),
        "r1": 2 * pivot - prev_low,
        "s1": 2 * pivot - prev_high,
        "r2": pivot + (prev_high - prev_low),
        "s2": pivot - (prev_high - prev_low),
    }
