def resolve_atm_slots(spot: float, step: int = 50, width: int = 200):
    """
    Returns atm_slots like: [-200, -150, ..., 0, ..., +200]
    """
    base = round(spot / step) * step
    slots = []

    v = -width
    while v <= width:
        slots.append({
            "atm_slot": v,
            "strike": base + v
        })
        v += step

    return slots
