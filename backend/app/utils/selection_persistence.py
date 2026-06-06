# backend/app/utils/selection_persistence.py
import os
import json
import tempfile
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


def _atomic_write_json(path: Path, data) -> None:
    """
    TRULY atomic write: serialize to a temp file in the SAME directory, fsync,
    then os.replace() onto the target. os.replace is atomic on POSIX (and
    Windows), so a concurrent reader sees EITHER the old complete file OR the
    new complete file — never a truncated/partial one. This is what the old
    `path.write_text(...)` did NOT guarantee (it truncated in place, exposing
    an empty/partial window that made the router read an empty selection and
    fire spurious CE_NOT_SELECTED / PE_NOT_SELECTED drops).
    """
    payload = json.dumps(data, indent=2)

    # Temp file MUST be on the same filesystem/dir as the target for
    # os.replace to be atomic (rename across filesystems is not atomic).
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))   # atomic swap
    except Exception:
        # Clean up the temp file on any failure; leave the existing target
        # file untouched (reader keeps seeing the last good selection).
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_clear(path: Path) -> None:
    """
    Atomically replace a selection file with an empty JSON array instead of
    deleting it. Writing `[]` (rather than unlink) means the reader always
    finds a valid, parseable file — it can distinguish 'deliberately empty'
    from 'mid-write' and never crashes to an empty set on a transient.
    """
    _atomic_write_json(path, [])


# -------------------------------------------------
# SAFE READ (tolerates transient empty/partial)
# -------------------------------------------------

def _safe_read_list(path: Path) -> List[Dict]:
    """
    Read a selection file, returning a list. Returns [] for a genuinely-empty
    file. Raises on a parse error so callers can decide how to handle a
    transient (the router treats a raise as 'keep previous'); with atomic
    writes in place, partial reads should no longer occur.
    """
    if not path.exists():
        return []
    raw = path.read_text().strip()
    if not raw:
        return []
    return json.loads(raw)


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

    # 🔒 TRULY ATOMIC WRITE (temp + os.replace).
    # Empty sides are written as `[]` atomically rather than unlinked, so the
    # reader never sees one side present and the other transiently missing —
    # the asymmetry that previously activated the selection filter and dropped
    # in-selection strikes.
    if ce:
        _atomic_write_json(ce_file, ce)
    else:
        _atomic_clear(ce_file)

    if pe:
        _atomic_write_json(pe_file, pe)
    else:
        _atomic_clear(pe_file)


# -------------------------------------------------
# LOAD SELECTION (USED BY API / UI)
# -------------------------------------------------

def load_selection(strategy_id: str) -> Dict[str, List[Dict]]:
    ce_file = _ce_file(strategy_id)
    pe_file = _pe_file(strategy_id)

    return {
        "CE": _safe_read_list(ce_file),
        "PE": _safe_read_list(pe_file),
    }