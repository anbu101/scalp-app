from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

ENTRY_CUTOFF = time(14, 30)
FORCE_EXIT   = time(15, 25)


def filter_signals(signals):
    """
    signals: iterable of dicts from cpr_e21_signal
    yields filtered signals
    """

    in_trade_day = None  # date object

    for sig in signals:
        ts = sig["ts"]
        dt = datetime.fromtimestamp(ts, IST)
        t = dt.time()
        day = dt.date()

        # ---- no new entries after 14:30
        if t > ENTRY_CUTOFF:
            continue

        # ---- one trade per day
        if in_trade_day == day:
            continue

        in_trade_day = day
        yield sig
