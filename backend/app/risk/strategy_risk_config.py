# backend/app/risk/strategy_risk_config.py

"""
Hardcoded per-strategy risk limits.
Safe, deterministic, backend-enforced.
"""

STRATEGY_MAX_DAILY_LOSS = {
    "SCALP_V1": -3000.0,     # ₹ -3000 max daily loss
    # "FUTURE_V1": -5000.0,  # example future strategy
}
