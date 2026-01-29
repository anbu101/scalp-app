from datetime import datetime
from app.backtest.indicators import ema

MAX_GAP = 8


def detect_cpr_e21_signals(index_candles, cpr_by_day):
    closes = [c["close"] for c in index_candles]

    for i in range(21, len(index_candles) - MAX_GAP):
        c = index_candles[i]
        prev = index_candles[i - 1]

        day = datetime.fromtimestamp(c["ts"]).date()

        ema21_prev = ema(closes[i - 21:i], 21)
        ema21_now  = ema(closes[i - 20:i + 1], 21)

        crossed_up   = prev["close"] <= ema21_prev and c["close"] > ema21_now
        crossed_down = prev["close"] >= ema21_prev and c["close"] < ema21_now

        if not (crossed_up or crossed_down):
            continue

        direction = "BULLISH" if crossed_up else "BEARISH"
        ema_cross_ts = c["ts"]

        # ✅ SAME DAY ONLY
        cpr = cpr_by_day.get(day)
        if not cpr:
            continue

        R = cpr["R"]
        S = cpr["S"]

        # --------------------------------------------------
        # Confirmation window
        # --------------------------------------------------
        for j in range(1, MAX_GAP + 1):
            nxt = index_candles[i + j]

            # ❌ do not allow next day confirmation
            nxt_day = datetime.fromtimestamp(nxt["ts"]).date()
            if nxt_day != day:
                break

            o, h, l, cl = nxt["open"], nxt["high"], nxt["low"], nxt["close"]

            if direction == "BULLISH":
                # R-break
                for r in R:
                    if cl > r:
                        yield {
                            "ts": nxt["ts"],
                            "direction": "BULLISH",
                            "reason": "R-break",
                            "ema_cross_ts": ema_cross_ts,
                        }
                        break

                # S-reclaim
                for s in S:
                    if l < s and cl > s:
                        yield {
                            "ts": nxt["ts"],
                            "direction": "BULLISH",
                            "reason": "S-reclaim",
                            "ema_cross_ts": ema_cross_ts,
                        }
                        break

            else:
                # S-break
                for s in S:
                    if cl < s:
                        yield {
                            "ts": nxt["ts"],
                            "direction": "BEARISH",
                            "reason": "S-break",
                            "ema_cross_ts": ema_cross_ts,
                        }
                        break

                # R-reject
                for r in R:
                    if h > r and cl < r:
                        yield {
                            "ts": nxt["ts"],
                            "direction": "BEARISH",
                            "reason": "R-reject",
                            "ema_cross_ts": ema_cross_ts,
                        }
                        break
