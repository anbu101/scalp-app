from pathlib import Path
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

def _now():
    return datetime.now(IST)
    
# ✅ HARD FIX: anchor logs to backend/app/logs
BASE_DIR = Path(__file__).resolve().parents[1]   # backend/app
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _log_file():
    today = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"{today}.log"

def write_audit_log(message: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}\n"

    with _log_file().open("a") as f:
        f.write(line)
