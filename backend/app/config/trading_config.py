# =========================
# Trading Configuration
# =========================

# 🔴 MUST be True to place LIVE orders
TRADING_ENABLED = False

# Max quantity per order (Zerodha limit-safe)
MAX_QTY_PER_ORDER = 1800

# -------------------------
# Trade side mode
# -------------------------
# Allowed values:
#   "CE"   → Only CE slots can take trades
#   "PE"   → Only PE slots can take trades
#   "BOTH" → Default (CE + PE)
TRADE_SIDE_MODE = "BOTH"
