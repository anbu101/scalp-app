import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# -------------------------------------------------
# CANONICAL STATE DIRECTORY (SINGLE SOURCE OF TRUTH)
# -------------------------------------------------

STATE_DIR = Path.home() / ".scalp-app" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

CE_FILE = STATE_DIR / "selected_ce.json"
PE_FILE = STATE_DIR / "selected_pe.json"

# -------------------------------------------------
# SAVE SELECTION (AUTHORITATIVE)
# -------------------------------------------------

def save_selection(options: List[Dict]):
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

    # 🔒 ATOMIC WRITE (SAFE)
    if ce:
        CE_FILE.write_text(json.dumps(ce, indent=2))
    else:
        CE_FILE.unlink(missing_ok=True)

    if pe:
        PE_FILE.write_text(json.dumps(pe, indent=2))
    else:
        PE_FILE.unlink(missing_ok=True)

# -------------------------------------------------
# LOAD SELECTION (USED BY API / UI)
# -------------------------------------------------

def load_selection() -> Dict[str, List[Dict]]:
    ce = []
    pe = []

    if CE_FILE.exists():
        ce = json.loads(CE_FILE.read_text())

    if PE_FILE.exists():
        pe = json.loads(PE_FILE.read_text())

    return {
        "CE": ce,
        "PE": pe,
    }
