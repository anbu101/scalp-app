def calculate_atm_strikes(
    spot_price: float,
    atm_range: int = 800,
    strike_step: int = 50
) -> list[int]:
    """
    Returns ATM ± range strikes.
    Example: NIFTY spot 22120 →
    [21300, 21350, ..., 22900]
    """

    atm = round(spot_price / strike_step) * strike_step

    lower = atm - atm_range
    upper = atm + atm_range

    return list(range(lower, upper + strike_step, strike_step))
