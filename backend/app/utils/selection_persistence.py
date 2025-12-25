import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# -------------------------------------------------
# Base directory (SAFE, absolute at runtime)
# -------------------------------------------------

BASE_DIR = Path.cwd() / "app" / "state"
BASE_DIR.mkdir(parents=True, exist_ok=True)

CE_FILE = BASE_DIR / "selected_ce.json"
PE_FILE = BASE_DIR / "selected_pe.json"

# -------------------------------------------------
# SAVE SELECTION
# -------------------------------------------------

def save_selection(options: List[Dict]):
    print("🔥 save_selection() CALLED")

    ce = []
    pe = []

    for o in options:
        o = dict(o)
        o["selected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if o.get("type") == "CE":
            ce.append(o)
        elif o.get("type") == "PE":
            pe.append(o)

    print(f"🔥 CE={len(ce)} PE={len(pe)}")
    print(f"🔥 Writing → {CE_FILE} {PE_FILE}")

    if ce:
        CE_FILE.write_text(json.dumps(ce, indent=2))
    if pe:
        PE_FILE.write_text(json.dumps(pe, indent=2))

    print("✅ save_selection() DONE")

# -------------------------------------------------
# LOAD SELECTION (🔥 REQUIRED BY API / WS)
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
