#!/usr/bin/env python3
# license_server/check_strategy_sync.py — fence LICENSE_STRATEGY_SYNC_20261016
"""
Do the license server's strategy lists match the app?

The license server keeps three hand-maintained copies of "which strategies
exist", and every one of them has drifted at least once:

  KNOWN_STRATEGY_IDS   server.py        missing id → 400 on override save
  ALL_STRATEGIES       admin_ui.html    missing id → chip SILENTLY STRIPPED
                                        from a license on any modal save
  strategy_defaults    .json            missing id → no template; extra id
                                        (a retired strategy) → stale template

The source of truth is the app's DEFAULT_STRATEGY_CONFIGS. This check:
  1. always: the three server-side lists must be the same set (no imports
     needed — server.py is read with ast, admin_ui.html with a regex);
  2. when the app backend imports (PYTHONPATH=backend, its deps installed):
     that set must equal DEFAULT_STRATEGY_CONFIGS, and strategy_defaults.json
     must equal a fresh gen_strategy_defaults.py run (stale templates).
     If the app can't be imported, step 2 is reported as SKIPPED, not failed.

Exit 0 = in sync (or app check skipped), 1 = drift. Used as a gate by
deploy-license-server.command.

    python3 license_server/check_strategy_sync.py      (from anywhere; finds ../backend itself)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent


def server_known_ids(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KNOWN_STRATEGY_IDS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise SystemExit("KNOWN_STRATEGY_IDS not found in server.py")


def admin_all_strategies(path: Path) -> set:
    m = re.search(r"const\s+ALL_STRATEGIES\s*=\s*\[([^\]]*)\]", path.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("ALL_STRATEGIES not found in admin_ui.html")
    return set(re.findall(r'"([A-Z0-9_]+)"', m.group(1)))


def fresh_defaults():
    """What gen_strategy_defaults.py would write right now, or None."""
    try:
        sys.path.insert(0, str(HERE))
        backend = HERE.parent / "backend"            # repo layout; PYTHONPATH not required
        if backend.is_dir() and str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        import gen_strategy_defaults as g
        from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS
    except Exception as e:                           # app deps missing etc.
        return None, f"{type(e).__name__}: {e}"
    out = {}
    for sid, cfg in DEFAULT_STRATEGY_CONFIGS.items():
        c = deepcopy(cfg)
        c.pop("trade_execution_mode", None)
        for p in g.LOTS_PATHS.get(sid, []):
            g._path_delete(c, p)
        out[sid] = c
    return out, None


def _fmt(s):
    return ", ".join(sorted(s)) or "—"


def main(server=HERE / "server.py", admin=HERE / "admin_ui.html", defaults=HERE / "strategy_defaults.json") -> int:
    bad = []
    known = server_known_ids(Path(server))
    chips = admin_all_strategies(Path(admin))
    try:
        dj = json.loads(Path(defaults).read_text(encoding="utf-8"))
    except Exception as e:
        dj = {}
        bad.append(f"strategy_defaults.json unreadable: {e}")
    tmpl = set(dj)
    lists = {"server.py KNOWN_STRATEGY_IDS": known, "admin_ui.html ALL_STRATEGIES": chips,
             "strategy_defaults.json": tmpl}
    union = known | chips | tmpl
    for name, s in lists.items():
        if s != union:
            bad.append(f"{name}: missing [{_fmt(union - s)}]")
    fresh, why = fresh_defaults()
    if fresh is None:
        print(f"  app check SKIPPED (backend not importable: {why})")
    else:
        app = set(fresh)
        for name, s in lists.items():
            if s - app:
                bad.append(f"{name}: not in the app (retired?) [{_fmt(s - app)}]")
            if app - s:
                bad.append(f"{name}: app strategy missing [{_fmt(app - s)}]")
        stale = [sid for sid in sorted(app & tmpl)
                 if json.dumps(fresh[sid], sort_keys=True) != json.dumps(dj.get(sid), sort_keys=True)]
        if stale:
            bad.append(f"strategy_defaults.json stale for [{', '.join(stale)}] — run "
                       f"PYTHONPATH=backend python3 license_server/gen_strategy_defaults.py")
    if bad:
        print("✗ license-server strategy lists OUT OF SYNC:")
        for b in bad:
            print("   - " + b)
        return 1
    print(f"✓ license-server strategy lists in sync ({len(union)} strategies"
          f"{'' if fresh is None else ', templates current'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
