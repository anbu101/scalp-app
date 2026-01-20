# app/utils/atm_strike.py

from typing import List

def calc_atm_strike(spot: float, step: int = 50) -> int:
    """
    Calculate ATM strike rounded to nearest step
    """
    if spot is None:
        raise ValueError("spot price cannot be None")
    return round(spot / step) * step


def calc_strike_range(
    atm: int,
    width: int = 800,
    step: int = 50
) -> List[int]:
    """
    Generate strike range ATM ± width
    """
    start = atm - width
    end = atm + width
    return list(range(start, end + step, step))
