import csv
import json
from datetime import datetime
from app.db.sqlite import get_conn


# --------------------------------------------------
def ts_to_dt(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------
def detect_broken_level(direction, R, S, close):
    """
    Uses CLOSE price only (correct trading logic)

    BULLISH:
      - Close above R → R-break
      - Close back above S → S-reclaim

    BEARISH:
      - Close below S → S-break
      - Close back below R → R-reject
    """
    if direction == "BULLISH":
        for i, r in enumerate(R):
            if close > r:
                return f"R{i+1}"
        for i, s in enumerate(S):
            if close > s:
                return f"S{i+1}_RECLAIM"

    else:
        for i, s in enumerate(S):
            if close < s:
                return f"S{i+1}"
        for i, r in enumerate(R):
            if close < r:
                return f"R{i+1}_REJECT"

    return ""


# --------------------------------------------------
conn = get_conn()
cur = conn.cursor()

rows = cur.execute(
    """
    SELECT
        symbol,
        entry_time,
        exit_time,
        entry_price,
        exit_price,
        exit_reason,
        sl_price,
        tp_price,
        signal_meta
    FROM backtest_trades
    WHERE strategy_name='CPR_E21'
    ORDER BY entry_time
    """
).fetchall()

with open("/data/cpr_e21_validation.csv", "w", newline="") as f:
    writer = csv.writer(f)

    writer.writerow([
        "symbol",

        "entry_date",
        "entry_time",
        "exit_date",
        "exit_time",

        "entry_price",
        "exit_price",
        "exit_reason",

        "ema_cross_time",
        "ema21",
        "ema_open",
        "ema_high",
        "ema_low",
        "ema_close",

        "cpr_tc",
        "cpr_bc",
        "pivot",

        "R1", "R2", "R3", "R4",
        "S1", "S2", "S3", "S4",

        "broken_level",
        "cpr_break_time",
        "cpr_break_reason",
        "break_open",
        "break_high",
        "break_low",
        "break_close",

        "sl_index",
        "tp_index",
    ])

    for r in rows:
        meta = json.loads(r[8]) if isinstance(r[8], str) else {}

        ema = meta.get("ema", {})
        cpr = meta.get("cpr", {})
        cpr_break = meta.get("cpr_break", {})

        R = cpr.get("R", [])
        S = cpr.get("S", [])

        direction = meta.get("direction")
        break_close = cpr_break.get("close")

        broken_level = detect_broken_level(
            direction,
            R,
            S,
            break_close,
        )

        writer.writerow([
            r[0],

            ts_to_dt(r[1]).split(" ")[0],
            ts_to_dt(r[1]).split(" ")[1],
            ts_to_dt(r[2]).split(" ")[0] if r[2] else "",
            ts_to_dt(r[2]).split(" ")[1] if r[2] else "",

            r[3],
            r[4],
            r[5],

            ts_to_dt(ema.get("cross_ts")),
            ema.get("ema21"),
            ema.get("open"),
            ema.get("high"),
            ema.get("low"),
            ema.get("close"),

            cpr.get("tc"),
            cpr.get("bc"),
            cpr.get("pivot"),

            R[0] if len(R) > 0 else "",
            R[1] if len(R) > 1 else "",
            R[2] if len(R) > 2 else "",
            R[3] if len(R) > 3 else "",

            S[0] if len(S) > 0 else "",
            S[1] if len(S) > 1 else "",
            S[2] if len(S) > 2 else "",
            S[3] if len(S) > 3 else "",

            broken_level,
            ts_to_dt(cpr_break.get("ts")),
            cpr_break.get("reason"),
            cpr_break.get("open"),
            cpr_break.get("high"),
            cpr_break.get("low"),
            cpr_break.get("close"),

            r[6],
            r[7],
        ])

print("Exported /data/cpr_e21_validation.csv")
