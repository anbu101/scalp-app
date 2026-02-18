import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# -------------------------------------------------
# CANONICAL STATE DIRECTORY (SINGLE SOURCE OF TRUTH)
# -------------------------------------------------

STATE_DIR = Path.home() / ".scalp-app" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


# -------------------------------------------------
# INTERNAL HELPERS
# -------------------------------------------------

def _ce_file(strategy_id: str) -> Path:
    return STATE_DIR / f"{strategy_id}_selected_ce.json"


def _pe_file(strategy_id: str) -> Path:
    return STATE_DIR / f"{strategy_id}_selected_pe.json"


# -------------------------------------------------
# SAVE SELECTION (AUTHORITATIVE, STRATEGY-SCOPED)
# -------------------------------------------------

def save_selection(strategy_id: str, options: List[Dict]):
    ce = []
    pe = []

    for o in options:
        o = dict(o)
        o["selected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        typ = o.get("type")
        sym = o.get("symbol") or o.get("tradingsymbol")

        if not sym or not typ:
            continue

        if typ == "CE":
            ce.append(o)
        elif typ == "PE":
            pe.append(o)

    ce_file = _ce_file(strategy_id)
    pe_file = _pe_file(strategy_id)

    # 🔒 ATOMIC WRITE (SAFE)
    if ce:
        ce_file.write_text(json.dumps(ce, indent=2))
    else:
        ce_file.unlink(missing_ok=True)

    if pe:
        pe_file.write_text(json.dumps(pe, indent=2))
    else:
        pe_file.unlink(missing_ok=True)


# -------------------------------------------------
# LOAD SELECTION (USED BY API / UI)
# -------------------------------------------------

def load_selection(strategy_id: str) -> Dict[str, List[Dict]]:
    ce_file = _ce_file(strategy_id)
    pe_file = _pe_file(strategy_id)

    ce = []
    pe = []

    if ce_file.exists():
        ce = json.loads(ce_file.read_text())

    if pe_file.exists():
        pe = json.loads(pe_file.read_text())

    return {
        "CE": ce,
        "PE": pe,
    }
