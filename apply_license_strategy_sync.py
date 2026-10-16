#!/usr/bin/env python3
# apply_license_strategy_sync.py — fence LICENSE_STRATEGY_SYNC_20261016
#
# License server ↔ app strategy lists.
#
# EMC_V1 has been live in the app since 2026-10-11, but the license server's
# three hand-kept strategy lists never got it:
#   * admin_ui.html ALL_STRATEGIES — no EMC_V1 chip, and a chip missing here
#     is SILENTLY STRIPPED from a license on any save through the modal;
#   * server.py KNOWN_STRATEGY_IDS — an EMC_V1 override is rejected (400);
#   * strategy_defaults.json — no EMC_V1 template (and BRK/ORB/TSG/VET
#     templates are stale — TSG lacks mtm_sl_per_lot / mtm_sl_hard_mult).
#
# This script
#   1. adds EMC_V1 to KNOWN_STRATEGY_IDS and ALL_STRATEGIES;
#   2. regenerates strategy_defaults.json from the app (gen_strategy_defaults.py);
#   3. adds license_server/check_strategy_sync.py — the three lists must be
#      one set AND equal the app's DEFAULT_STRATEGY_CONFIGS, templates current;
#   4. makes deploy-license-server.command run that check first and abort on
#      drift (SKIP_STRATEGY_SYNC=1 to override);
#   5. admin UI: warns when the saved overrides still hold a retired strategy
#      (your PST_SELL / PST_HEDGE save error) and when the templates file has
#      a strategy that is no longer offered.
#
# App untouched. Gates: py_compile, the new check must pass on the result.
# Any failure → every file restored.
#
# Usage: python3 apply_license_strategy_sync.py [--repo PATH] [--allow-dirty]
# Then deploy: license_server/deploy-license-server.command
# Run ONLY this script (the checker is carried inside it).

from __future__ import annotations
import argparse, os, py_compile, shutil, subprocess, sys, tempfile
from pathlib import Path

FENCE = "LICENSE_STRATEGY_SYNC_20261016"
SRV = "license_server/server.py"
UI = "license_server/admin_ui.html"
DEP = "license_server/deploy-license-server.command"
JS = "license_server/strategy_defaults.json"
CK = "license_server/check_strategy_sync.py"

EDITS = [
    (SRV, '    "BRK_V1",   # ── BRK_V1 added 2026-09-02 — missing id = 400 on override save\n}',
     '    "BRK_V1",   # ── BRK_V1 added 2026-09-02 — missing id = 400 on override save\n    "EMC_V1",   # ── LICENSE_STRATEGY_SYNC_20261016 ── EMC_V1 live since 2026-10-11; check_strategy_sync.py now guards this list\n}', 1),
    (UI, '"BRK_V1","ORB_V1"];',
     '"BRK_V1","ORB_V1","EMC_V1"]; /* ── LICENSE_STRATEGY_SYNC_20261016 ── EMC_V1 added (live since 2026-10-11); license_server/check_strategy_sync.py gates deploys on this list */', 1),
    (UI, '    const missing = ALL_STRATEGIES.filter(s => !(s in defs));\n',
     '    const missing = ALL_STRATEGIES.filter(s => !(s in defs));\n    /* ── LICENSE_STRATEGY_SYNC_20261016 ── the reverse drift too: a template\n       for a strategy that is no longer offered (retired, e.g. PST_*). */\n    const extra = ids.filter(s => !ALL_STRATEGIES.includes(s));\n    if (extra.length) {\n      document.getElementById("govErr").textContent =\n        "strategy_defaults.json has template(s) for strategies that are no longer offered: " +\n        extra.join(", ") + ". Regenerate (gen_strategy_defaults.py) and redeploy.";\n    }\n', 1),
    (UI, '    document.getElementById("govStatus").textContent = "";\n  } catch (e) {\n    document.getElementById("govErr").textContent =\n      "Load failed (',
     '    document.getElementById("govStatus").textContent = "";\n    /* ── LICENSE_STRATEGY_SYNC_20261016 ── overrides saved before a strategy\n       was retired still carry its block, and the server now rejects the\n       whole save with "unknown strategy id(s)". Say so up front. */\n    const retired = Object.keys(d.config_overrides || {}).filter(s => !ALL_STRATEGIES.includes(s));\n    if (retired.length) {\n      document.getElementById("govErr").textContent =\n        "Saved overrides still contain retired strategies: " + retired.join(", ") +\n        " — delete those blocks below, then Save (the server will reject them).";\n    }\n  } catch (e) {\n    document.getElementById("govErr").textContent =\n      "Load failed (', 1),
    (DEP, '# ------------------------------------------------------------------\n# 1. COPY FILES\n',
     '# ------------------------------------------------------------------\n# ── LICENSE_STRATEGY_SYNC_20261016 ── refuse to deploy strategy lists that\n# have drifted from the app (a strategy missing from admin_ui.html is\n# silently STRIPPED from a license on save). SKIP_STRATEGY_SYNC=1 overrides.\n# ------------------------------------------------------------------\nif [ -f check_strategy_sync.py ]; then\n  echo ""\n  echo "[0/4] Checking strategy lists against the app..."\n  if ! python3 check_strategy_sync.py; then\n    if [ "${SKIP_STRATEGY_SYNC:-0}" != "1" ]; then\n      echo "Fix the drift above (usually: add the id to server.py KNOWN_STRATEGY_IDS +"\n      echo "admin_ui.html ALL_STRATEGIES, then run gen_strategy_defaults.py) — or re-run"\n      echo "with SKIP_STRATEGY_SYNC=1 to deploy anyway. Aborting."\n      exit 1\n    fi\n    echo "SKIP_STRATEGY_SYNC=1 — deploying despite the drift."\n  fi\nfi\n\n# ------------------------------------------------------------------\n# 1. COPY FILES\n', 1),
]

CHECKER = '#!/usr/bin/env python3\n# license_server/check_strategy_sync.py — fence LICENSE_STRATEGY_SYNC_20261016\n"""\nDo the license server\'s strategy lists match the app?\n\nThe license server keeps three hand-maintained copies of "which strategies\nexist", and every one of them has drifted at least once:\n\n  KNOWN_STRATEGY_IDS   server.py        missing id → 400 on override save\n  ALL_STRATEGIES       admin_ui.html    missing id → chip SILENTLY STRIPPED\n                                        from a license on any modal save\n  strategy_defaults    .json            missing id → no template; extra id\n                                        (a retired strategy) → stale template\n\nThe source of truth is the app\'s DEFAULT_STRATEGY_CONFIGS. This check:\n  1. always: the three server-side lists must be the same set (no imports\n     needed — server.py is read with ast, admin_ui.html with a regex);\n  2. when the app backend imports (PYTHONPATH=backend, its deps installed):\n     that set must equal DEFAULT_STRATEGY_CONFIGS, and strategy_defaults.json\n     must equal a fresh gen_strategy_defaults.py run (stale templates).\n     If the app can\'t be imported, step 2 is reported as SKIPPED, not failed.\n\nExit 0 = in sync (or app check skipped), 1 = drift. Used as a gate by\ndeploy-license-server.command.\n\n    python3 license_server/check_strategy_sync.py      (from anywhere; finds ../backend itself)\n"""\nfrom __future__ import annotations\n\nimport ast\nimport json\nimport re\nimport sys\nfrom copy import deepcopy\nfrom pathlib import Path\n\nHERE = Path(__file__).resolve().parent\n\n\ndef server_known_ids(path: Path) -> set:\n    tree = ast.parse(path.read_text(encoding="utf-8"))\n    for node in ast.walk(tree):\n        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KNOWN_STRATEGY_IDS" for t in node.targets):\n            return set(ast.literal_eval(node.value))\n    raise SystemExit("KNOWN_STRATEGY_IDS not found in server.py")\n\n\ndef admin_all_strategies(path: Path) -> set:\n    m = re.search(r"const\\s+ALL_STRATEGIES\\s*=\\s*\\[([^\\]]*)\\]", path.read_text(encoding="utf-8"))\n    if not m:\n        raise SystemExit("ALL_STRATEGIES not found in admin_ui.html")\n    return set(re.findall(r\'"([A-Z0-9_]+)"\', m.group(1)))\n\n\ndef fresh_defaults():\n    """What gen_strategy_defaults.py would write right now, or None."""\n    try:\n        sys.path.insert(0, str(HERE))\n        backend = HERE.parent / "backend"            # repo layout; PYTHONPATH not required\n        if backend.is_dir() and str(backend) not in sys.path:\n            sys.path.insert(0, str(backend))\n        import gen_strategy_defaults as g\n        from app.config.strategy_loader import DEFAULT_STRATEGY_CONFIGS\n    except Exception as e:                           # app deps missing etc.\n        return None, f"{type(e).__name__}: {e}"\n    out = {}\n    for sid, cfg in DEFAULT_STRATEGY_CONFIGS.items():\n        c = deepcopy(cfg)\n        c.pop("trade_execution_mode", None)\n        for p in g.LOTS_PATHS.get(sid, []):\n            g._path_delete(c, p)\n        out[sid] = c\n    return out, None\n\n\ndef _fmt(s):\n    return ", ".join(sorted(s)) or "—"\n\n\ndef main(server=HERE / "server.py", admin=HERE / "admin_ui.html", defaults=HERE / "strategy_defaults.json") -> int:\n    bad = []\n    known = server_known_ids(Path(server))\n    chips = admin_all_strategies(Path(admin))\n    try:\n        dj = json.loads(Path(defaults).read_text(encoding="utf-8"))\n    except Exception as e:\n        dj = {}\n        bad.append(f"strategy_defaults.json unreadable: {e}")\n    tmpl = set(dj)\n    lists = {"server.py KNOWN_STRATEGY_IDS": known, "admin_ui.html ALL_STRATEGIES": chips,\n             "strategy_defaults.json": tmpl}\n    union = known | chips | tmpl\n    for name, s in lists.items():\n        if s != union:\n            bad.append(f"{name}: missing [{_fmt(union - s)}]")\n    fresh, why = fresh_defaults()\n    if fresh is None:\n        print(f"  app check SKIPPED (backend not importable: {why})")\n    else:\n        app = set(fresh)\n        for name, s in lists.items():\n            if s - app:\n                bad.append(f"{name}: not in the app (retired?) [{_fmt(s - app)}]")\n            if app - s:\n                bad.append(f"{name}: app strategy missing [{_fmt(app - s)}]")\n        stale = [sid for sid in sorted(app & tmpl)\n                 if json.dumps(fresh[sid], sort_keys=True) != json.dumps(dj.get(sid), sort_keys=True)]\n        if stale:\n            bad.append(f"strategy_defaults.json stale for [{\', \'.join(stale)}] — run "\n                       f"PYTHONPATH=backend python3 license_server/gen_strategy_defaults.py")\n    if bad:\n        print("✗ license-server strategy lists OUT OF SYNC:")\n        for b in bad:\n            print("   - " + b)\n        return 1\n    print(f"✓ license-server strategy lists in sync ({len(union)} strategies"\n          f"{\'\' if fresh is None else \', templates current\'})")\n    return 0\n\n\nif __name__ == "__main__":\n    sys.exit(main())\n'


def die(m):
    print(f"\n✗ {m}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("SCALP_REPO", "/Users/anbu/dev/scalp-app"))
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo).expanduser().resolve()
    targets = sorted({f for f, *_ in EDITS})
    staged = {}
    for rel in targets + [JS]:
        p = repo / rel
        if not p.exists():
            die(f"missing {rel}")
        staged[rel] = p.read_text(encoding="utf-8")
    for rel in targets:
        if FENCE in staged[rel]:
            die(f"{rel} already carries {FENCE} — already applied, nothing to do")
    if (repo / CK).exists():
        die(f"{CK} already exists — mixed state; remove it and re-run")
    try:
        dirty = [r for r in targets + [JS] if subprocess.run(["git", "status", "--porcelain", "--", r], cwd=repo,
                 capture_output=True, text=True, timeout=30).stdout.strip()]
    except Exception:
        dirty = []
    if dirty and not a.allow_dirty:
        die("uncommitted changes in " + ", ".join(dirty) + " — commit/stash first or --allow-dirty")
    original = dict(staged)
    for rel, old, new, c in EDITS:
        n = staged[rel].count(old)
        if n != c:
            die(f"anchor matched {n}× (need {c}) in {rel}:\n{old[:300]}")
        staged[rel] = staged[rel].replace(old, new)

    # regenerate strategy_defaults.json from the app into a temp file
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "strategy_defaults.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(repo / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        code = ("import sys; sys.path.insert(0, sys.argv[2]); import gen_strategy_defaults as g; "
                "from pathlib import Path; g.OUT = Path(sys.argv[1]); g.main()")
        r = subprocess.run([sys.executable, "-c", code, str(tmp), str(repo / "license_server")],
                           cwd=repo, capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0 or not tmp.exists():
            die("could not regenerate strategy_defaults.json from the app (backend import failed?):\n" + r.stderr[-2000:])
        staged[JS] = tmp.read_text(encoding="utf-8")
        for rel, txt in [(SRV, staged[SRV]), (CK, CHECKER)]:
            tp = Path(td) / Path(rel).name
            tp.write_text(txt, encoding="utf-8")
            try:
                py_compile.compile(str(tp), doraise=True)
            except py_compile.PyCompileError as e:
                die(f"py_compile failed for {rel}: {e}")
        print("  ✓ py_compile (server.py, check_strategy_sync.py)")
        r = subprocess.run(["bash", "-n", "/dev/stdin"], input=staged[DEP], capture_output=True, text=True)
        if r.returncode != 0:
            die("bash -n failed on staged deploy script:\n" + r.stderr)
        print("  ✓ bash -n (deploy script)")

    import json
    old_js, new_js = json.loads(original[JS]), json.loads(staged[JS])
    changed = sorted(k for k in new_js if k in old_js and json.dumps(new_js[k], sort_keys=True) != json.dumps(old_js[k], sort_keys=True))
    print(f"  · templates: added {sorted(set(new_js) - set(old_js)) or '—'}, removed {sorted(set(old_js) - set(new_js)) or '—'}, refreshed {changed or '—'}")

    for rel in targets + [JS]:
        p = repo / rel
        shutil.copy2(p, p.with_name(p.name + f".bak-{FENCE}"))
        p.write_text(staged[rel], encoding="utf-8")
        print(f"  ✓ wrote {rel}")
    (repo / CK).write_text(CHECKER, encoding="utf-8")
    os.chmod(repo / CK, 0o755)
    print(f"  ✓ created {CK}")

    def rollback(why):
        for rel in targets + [JS]:
            (repo / rel).write_text(original[rel], encoding="utf-8")
            (repo / rel).with_name(Path(rel).name + f".bak-{FENCE}").unlink(missing_ok=True)
        (repo / CK).unlink(missing_ok=True)
        die(why + " — every file restored")

    r = subprocess.run([sys.executable, str(repo / CK)], cwd=repo / "license_server", capture_output=True, text=True, timeout=300)
    print("    " + "\n    ".join(r.stdout.strip().splitlines()))
    if r.returncode != 0 or "SKIPPED" in r.stdout:
        rollback("check_strategy_sync.py did not pass on the result")
    print(f"\n✓ {FENCE} applied. Next: commit, then run license_server/deploy-license-server.command\n"
          f"  (it now checks the lists first). After deploy: Admin UI → Strategy config → delete the\n"
          f"  PST_HEDGE / PST_SELL blocks → Save. Then re-open any friend's license that should have\n"
          f"  EMC_V1 and tick the new chip.")


if __name__ == "__main__":
    main()
