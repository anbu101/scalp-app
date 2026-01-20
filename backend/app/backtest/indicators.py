def ema(values, period):
    k = 2 / (period + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def bullish_ema_ok(candles):
    closes = [c["close"] for c in candles]
    if len(closes) < 50:
        return False

    ema20 = ema(closes[-20:], 20)
    ema50 = ema(closes[-50:], 50)

    price = closes[-1]
    return price > ema20 and price > ema50 and ema20 > ema50


def bearish_ema_ok(candles):
    closes = [c["close"] for c in candles]
    if len(closes) < 50:
        return False

    ema20 = ema(closes[-20:], 20)
    ema50 = ema(closes[-50:], 50)

    price = closes[-1]
    return price < ema20 and price < ema50 and ema20 < ema50
