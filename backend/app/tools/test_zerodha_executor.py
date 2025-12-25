from execution.zerodha_executor import ZerodhaOrderExecutor

API_KEY = "ak1k5sv8byhjv53i"
API_SECRET = "la0z8p5nypblblstmits22fklpspxn6t"

print("Creating Zerodha executor...")

executor = ZerodhaOrderExecutor(
    api_key=API_KEY,
    api_secret=API_SECRET,
    dry_run=True  # 🔒 SAFE MODE
)

print("Zerodha executor created successfully")
