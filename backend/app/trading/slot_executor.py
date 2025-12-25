from pathlib import Path
from typing import Dict, List

from app.trading.slot_registry import SlotRegistry
from app.execution.zerodha_executor import ZerodhaOrderExecutor
from app.utils.selection_persistence import load_selection


STATE_DIR = Path("state")  # folder to store slot state files
STATE_DIR.mkdir(exist_ok=True)


class SlotExecutor:
    """
    Binds persisted option selections to fixed slots:
    CE_1, CE_2, PE_1, PE_2
    """

    def __init__(self, executor: ZerodhaOrderExecutor):
        self.registry = SlotRegistry(
            executor=executor,
            state_dir=STATE_DIR,
        )

    def bind_from_saved_selection(self):
        """
        Loads persisted selection and binds symbols to slots.
        """
        selection: List[Dict] = load_selection()
        if not selection:
            print("[SLOT] No saved selection found")
            return

        ce_opts = [o for o in selection if o["type"] == "CE"]
        pe_opts = [o for o in selection if o["type"] == "PE"]

        # -------------------------
        # Bind CE slots
        # -------------------------
        for opt, slot_name in zip(ce_opts[:2], ("CE_1", "CE_2")):
            mgr = self.registry.get_slot(slot_name)

            # Do not replace active trade
            if mgr.in_trade:
                continue

            # Bind symbol (soft bind)
            mgr.bound_symbol = opt["tradingsymbol"]
            mgr.bound_token = opt["token"]

            print(f"[SLOT] {slot_name} → {opt['tradingsymbol']}")

        # -------------------------
        # Bind PE slots
        # -------------------------
        for opt, slot_name in zip(pe_opts[:2], ("PE_1", "PE_2")):
            mgr = self.registry.get_slot(slot_name)

            if mgr.in_trade:
                continue

            mgr.bound_symbol = opt["tradingsymbol"]
            mgr.bound_token = opt["token"]

            print(f"[SLOT] {slot_name} → {opt['tradingsymbol']}")

    def get_active_slots(self):
        """
        Returns all slots that have a bound symbol.
        """
        return [
            mgr for mgr in self.registry.get_all()
            if hasattr(mgr, "bound_symbol")
        ]
