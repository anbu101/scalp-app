from typing import List, Dict

def find_candidate_sl(candles: List[Dict], buyPrice: float, slSearchDepth: int = 100, excludeCurrentBar: bool = True):
    # candles: list of dicts with keys open/high/low/close (oldest->newest)
    n = len(candles)
    start_idx = n-2 if excludeCurrentBar else n-1
    # 1) most recent red candle low < buyPrice
    for i in range(start_idx, max(-1, start_idx - slSearchDepth), -1):
        if i < 0:
            break
        c = candles[i]
        if c['close'] < c['open'] and c['low'] < buyPrice:
            return c['low']
    # 2) lowest low in window
    lows = [candles[i]['low'] for i in range(max(0, n - slSearchDepth), n) if 'low' in candles[i]]
    if lows:
        lowest = min(lows)
        if lowest < buyPrice:
            return lowest
    # 3) fallback previous bar low
    if start_idx >= 0:
        return candles[start_idx]['low']
    return candles[-1]['low']