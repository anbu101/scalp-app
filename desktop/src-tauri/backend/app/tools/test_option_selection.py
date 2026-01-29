import json
import os
from app.selector.option_selector import OptionSelector
from app.config.strategy_loader import load_strategy_config

INDEX_SYMBOL = "NIFTY"
ATM_RANGE = 800
STRIKE_STEP = 50
TRADE_MODE = "BOTH"


def main():
    print("\n=== OPTION SELECTION DEBUG ===\n")

    # 1️⃣ Load strategy config (from UI)
    cfg = load_strategy_config()

    price_min = cfg["option_premium"]["min"]
    price_max = cfg["option_premium"]["max"]

    print(f"[CONFIG] Premium range: {price_min} → {price_max}")

    # 2️⃣ Load static instrument master
    master_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "fetcher",
        "master_example.json",
    )

    with open(master_path, "r") as f:
        raw = json.load(f)

    instruments = list(raw.values()) if isinstance(raw, dict) else raw
    print(f"[MASTER] Instruments loaded: {len(instruments)}")

    # 3️⃣ Run selector
    selector = OptionSelector(
        instruments=instruments,
        price_min=price_min,
        price_max=price_max,
        trade_mode=TRADE_MODE,
        atm_range=ATM_RANGE,
        strike_step=STRIKE_STEP,
        index_symbol=INDEX_SYMBOL,
    )

    result = selector.select()

    if not result:
        print("❌ NO OPTIONS SELECTED")
        return

    expiry = result["expiry"]
    options = result["options"]

    print(f"\n[EXPIRY] {expiry}")
    print(f"[OPTIONS FOUND] {len(options)}\n")

    ce = next(o for o in options if o["type"] == "CE")
    pe = next(o for o in options if o["type"] == "PE")

    # 4️⃣ Persist clearly
    with open("selected_ce.json", "w") as f:
        json.dump(ce, f, indent=2)

    with open("selected_pe.json", "w") as f:
        json.dump(pe, f, indent=2)

    print("✅ CE SELECTED:", ce["symbol"], "@", ce["last_price"])
    print("✅ PE SELECTED:", pe["symbol"], "@", pe["last_price"])
    print("\nSaved to selected_ce.json / selected_pe.json\n")


if __name__ == "__main__":
    main()
