import numpy as np

def ema(series, period):
    """Simple EMA, input is list or 1D-array (oldest first). Returns full numpy array."""
    vals = np.asarray(series, dtype=float)
    alpha = 2.0 / (period + 1)
    out = np.full(len(vals), np.nan)
    if len(vals) == 0:
        return out
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = alpha * vals[i] + (1 - alpha) * out[i-1]
    return out


def sma(series, length):
    import numpy as np
    a = np.asarray(series, dtype=float)
    if len(a) < length:
        return np.full(len(a), np.nan)
    out = np.convolve(a, np.ones(length)/length, mode='valid')
    # pad front with nan to keep same length
    return np.concatenate((np.full(len(a)-len(out), np.nan), out))


def rsi_wilder(close, length=5):
    import numpy as np
    close = np.asarray(close, dtype=float)
    if len(close) < length+1:
        return np.full(len(close), np.nan)
    deltas = np.diff(close)
    gains = np.where(deltas>0, deltas, 0)
    losses = np.where(deltas<0, -deltas, 0)
    avg_gain = np.zeros(len(gains))
    avg_loss = np.zeros(len(gains))
    # first value
    avg_gain[length-1] = gains[:length].mean()
    avg_loss[length-1] = losses[:length].mean()
    for i in range(length, len(gains)):
        avg_gain[i] = (avg_gain[i-1]*(length-1) + gains[i]) / length
        avg_loss[i] = (avg_loss[i-1]*(length-1) + losses[i]) / length
    rsi = np.full(len(close), np.nan)
    for i in range(length, len(close)):
        ag = avg_gain[i-1]
        al = avg_loss[i-1]
        rsi[i] = 100.0 if al == 0 else (100.0 - (100.0 / (1.0 + ag / al)))
    return rsi


def rsi_cutler(close, length=5):
    import numpy as np
    close = np.asarray(close, dtype=float)
    if len(close) < length+1:
        return np.full(len(close), np.nan)
    deltas = np.diff(close)
    gains = np.where(deltas>0, deltas, 0)
    losses = np.where(deltas<0, -deltas, 0)
    avg_gain = np.convolve(gains, np.ones(length)/length, mode='full')[:len(gains)]
    avg_loss = np.convolve(losses, np.ones(length)/length, mode='full')[:len(gains)]
    rsi = np.full(len(close), np.nan)
    for i in range(length, len(close)):
        ag = avg_gain[i-1]
        al = avg_loss[i-1]
        rsi[i] = 100.0 if al == 0 else (100.0 - (100.0 / (1.0 + ag / al)))
    return rsi