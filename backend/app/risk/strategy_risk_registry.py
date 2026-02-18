from typing import Dict

# 🔒 Hardcoded limits (safe version)
STRATEGY_MAX_LOSS: Dict[str, float] = {
    "SCALP_V1": 5000,
    # Add future strategies here
    # "TREND_V1": 10000,
}


def get_max_loss(strategy_id: str) -> float:
    return STRATEGY_MAX_LOSS.get(strategy_id, 0)
