from datetime import datetime, timedelta
from app.marketdata.candle import Candle, CandleSource
from app.brokers.zerodha_manager import ZerodhaManager
from app.fetcher.zerodha_instruments import load_instruments_df


_kite = ZerodhaManager().get_kite()
_instruments_df = load_instruments_df()


def fetch_latest_1m_candle(token: int) -> Candle | None:
    """
    Fetch latest completed 1-minute candle using
    OFFICIAL instrument master token only.
    """

    # 🔒 Resolve token AGAINST instrument master
    row = _instruments_df.loc[
        _instruments_df["instrument_token"] == token
    ]

    if row.empty:
        raise RuntimeError(f"Invalid instrument_token {token}")

    now = datetime.now()
    frm = now - timedelta(minutes=5)

    data = _kite.historical_data(
        instrument_token=int(token),
        from_date=frm,
        to_date=now,
        interval="minute",
    )

    if not data:
        return None

    last = data[-1]
    ts = int(last["date"].timestamp())

    return Candle(
        start_ts=ts,
        end_ts=ts + 60,
        open=last["open"],
        high=last["high"],
        low=last["low"],
        close=last["close"],
        source=CandleSource.REST,
    )
