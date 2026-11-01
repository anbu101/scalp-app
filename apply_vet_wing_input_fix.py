#!/usr/bin/env python3
# apply_vet_wing_input_fix.py — ── VET_WING_INPUT_FIX_20261101 ──
#
# Settings → VET V1 → "Hedge Wing Max ₹" could not be edited: every keystroke
# snapped back to the saved value. Its onChange called updateVET() twice; each
# call does structuredClone(vetConfig) of the SAME render's state and
# setVetConfig(clone), so the second call (hedge_enabled) overwrote the first
# (hedge_max_premium). Now ONE functional state update sets both. The only
# handler in frontend/src with this pattern (scanned).
# Save is unchanged (VET_HEDGE_FIX_20261012 still derives hedge_enabled from
# SELL + Wing Max > 0 at save time).
#
#   EDIT  frontend/src/pages/Settings.jsx             (1 anchored edit)
#   NEW   frontend/scripts/test_vet_wing_input.mjs      (React 18 + jsdom behaviour check;
#                                                       not under src/, never bundled)
#   Requires VET_HEDGE_FIX_20261012. Independent of VET_WING_FAR_20261101.
#
#   Checks (need node + frontend/node_modules — or the build copy's):
#     * behaviour: the REAL handler + updateVET cut from Settings.jsx, mounted in
#       jsdom, typed into → 5 / 4.5 / 0 / −2 / 5 stick (8 checks); the same
#       harness on the pre-fix .bak must REPRODUCE the bug
#     * CRA lint: eslint-config-react-app (the config react-scripts 5 builds with)
#       on Settings.jsx before vs after → no new problem, no errors; harness clean;
#       no eslint-disable added anywhere
#     * esbuild parses the patched Settings.jsx
#
#   cd <repo root> && python3 apply_vet_wing_input_fix.py [repo_root] [--allow-dirty] [--skip-tests]
#   then: ./desktop/build-scalp.sh frontend
#
# Run ONLY this script — it carries every file it writes.

import os
import re
import shutil
import subprocess
import sys

FENCE = "VET_WING_INPUT_FIX_20261101"
SETTINGS = "frontend/src/pages/Settings.jsx"
HARNESS = "frontend/scripts/test_vet_wing_input.mjs"
OLD = '                    onChange={(e) => { const v = Math.max(0, Number(e.target.value) || 0); updateVET(["hedge_max_premium"], v); updateVET(["hedge_enabled"], v > 0); }}\n'
NEW = '                    onChange={(e) => {\n                      // ── VET_WING_INPUT_FIX_20261101 ── ONE state update. Two\n                      // updateVET calls each cloned the same stale vetConfig, so the\n                      // second (hedge_enabled) overwrote the first (the ₹ value) and\n                      // every keystroke snapped back to the saved number.\n                      const v = Math.max(0, Number(e.target.value) || 0);\n                      setVetConfig((c) => ({ ...c, hedge_max_premium: v, hedge_enabled: v > 0 }));\n                    }}\n'
NEW_HARNESS = '/* eslint-env node, es2020 */\n// frontend/scripts/test_vet_wing_input.mjs — ── VET_WING_INPUT_FIX_20261101 ──\n//\n// Behavioural check for Settings → VET V1 → "Hedge Wing Max ₹". The bug: the\n// field\'s onChange called updateVET twice; each call cloned the SAME stale\n// vetConfig, so the second (hedge_enabled) overwrote the first (the ₹ value)\n// and every keystroke snapped back to the saved number.\n//\n// This mounts a REAL React 18 component in jsdom whose onChange handler and\n// updateVET are the EXACT source text cut from Settings.jsx, types into the\n// input the way a browser does (native value setter + input event), and checks\n// the state. Run against the pre-fix .bak it must reproduce the bug — proof the\n// check can fail.\n//\n//   node frontend/scripts/test_vet_wing_input.mjs <Settings.jsx> [--expect-bug]\n// Needs frontend/node_modules (react, react-dom, jsdom, esbuild — all already\n// pulled in by react-scripts 5 / the esbuild devDependency).\n\nimport { createRequire } from "module";\nimport { readFileSync } from "fs";\nimport vm from "vm";\nimport path from "path";\nimport { fileURLToPath } from "url";\n\nconst here = path.dirname(fileURLToPath(import.meta.url));\nconst require = createRequire(path.join(here, "..", "package.json"));\nconst { JSDOM } = require("jsdom");\nconst esbuild = require("esbuild");\n\nconst file = process.argv[2];\nconst expectBug = process.argv.includes("--expect-bug");\nconst src = readFileSync(file, "utf8");\n\nfunction balanced(text, openIdx) {            // openIdx points at "{"\n  let depth = 0;\n  for (let i = openIdx; i < text.length; i++) {\n    const ch = text[i];\n    if (ch === "{") depth++;\n    else if (ch === "}") { depth--; if (depth === 0) return text.slice(openIdx, i + 1); }\n  }\n  throw new Error("unbalanced braces");\n}\n\n// the Hedge Wing Max <Input>: anchored on its value binding\nconst vIdx = src.indexOf("value={vetConfig.hedge_max_premium}");\nif (vIdx < 0) throw new Error("Hedge Wing Max input not found");\nconst ocIdx = src.indexOf("onChange={", vIdx);\nconst handler = balanced(src, ocIdx + "onChange=".length).slice(1, -1).trim();\nconst uIdx = src.indexOf("function updateVET(path, value) {");\nconst updateVET = "function updateVET(path, value) " + balanced(src, src.indexOf("{", uIdx));\n\nconst harness = `\nconst React = require("react");\nmodule.exports = function makeHarness(INIT, onState) {\n  return function H() {\n    const [vetConfig, setVetConfig] = React.useState(INIT);\n    ${updateVET}\n    onState(vetConfig);\n    return <input type="number" min="0" step="0.5"\n      value={vetConfig.hedge_max_premium}\n      onChange={${handler}} />;\n  };\n};`;\nconst { code } = esbuild.transformSync(harness, { loader: "jsx", format: "cjs" });\nconst mod = { exports: {} };\nvm.runInThisContext(`(function (module, exports, require) {\\n${code}\\n})`)(mod, mod.exports, require);\n\nconst dom = new JSDOM("<!doctype html><div id=root></div>");\nglobalThis.window = dom.window;\nglobalThis.document = dom.window.document;\nif (!("navigator" in globalThis)) globalThis.navigator = dom.window.navigator;\nglobalThis.IS_REACT_ACT_ENVIRONMENT = true;\nconst React = require("react");\nconst { createRoot } = require("react-dom/client");\nconst act = React.act || require("react-dom/test-utils").act;\n\nlet state = null;\nconst H = mod.exports({ leg_action: "SELL", hedge_max_premium: 3, hedge_enabled: false },\n                      (s) => { state = s; });\nconst root = createRoot(document.getElementById("root"));\nact(() => root.render(React.createElement(H)));\nconst input = document.querySelector("input");\nconst setVal = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;\nfunction type(v) {\n  act(() => { setVal.call(input, v); input.dispatchEvent(new window.Event("input", { bubbles: true })); });\n}\n\nconst fails = [];\nlet ok = 0;\nfunction check(name, cond, detail) {\n  console.log((cond ? "  PASS  " : "  FAIL  ") + name + (cond ? "" : "  " + JSON.stringify(detail)));\n  cond ? ok++ : fails.push(name);\n}\n\ntype("5");\nconst sticks = state.hedge_max_premium === 5;\nif (expectBug) {\n  check("pre-fix handler REPRODUCES the bug (typed 5, value snaps back to 3)",\n        state.hedge_max_premium === 3 && input.value === "3", state);\n} else {\n  check("typed 5 → state hedge_max_premium = 5", sticks, state);\n  check("…and the input shows 5", input.value === "5", input.value);\n  check("…and hedge_enabled turned ON in the same update", state.hedge_enabled === true, state);\n  type("4.5");\n  check("typed 4.5 → 4.5 (half-rupee steps)", state.hedge_max_premium === 4.5, state);\n  type("0");\n  check("typed 0 → 0 and the wing switches OFF (naked)",\n        state.hedge_max_premium === 0 && state.hedge_enabled === false, state);\n  type("-2");\n  check("negative is clamped to 0", state.hedge_max_premium === 0, state);\n  type("5");\n  check("back to 5 → ON again", state.hedge_max_premium === 5 && state.hedge_enabled === true, state);\n  check("other keys untouched", state.leg_action === "SELL", state);\n}\nact(() => root.unmount());\nif (fails.length) { console.log(`\\n${ok} passed, ${fails.length} FAILED`); process.exit(1); }\nconsole.log(`\\nALL ${ok} VET WING-INPUT CHECKS PASSED`);\n'

EDITS = {SETTINGS: [(OLD, NEW)]}
PARENTS = {SETTINGS: ["VET_HEDGE_FIX_20261012"]}
NEW_FILES = {HARNESS: NEW_HARNESS}
MIRRORS = ("desktop/src-tauri",)       # build copies (gitignored); refreshed if present


def die(msg, wrote=False):
    print(f"\n✗ {msg}" + ("" if wrote else "\nNothing was written."))
    sys.exit(1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    root = os.path.abspath(args[0] if args else os.getcwd())
    if not (os.path.isdir(os.path.join(root, "backend", "app"))
            and os.path.isdir(os.path.join(root, "frontend", "src"))):
        die(f"{root} is not the scalp-app repo root (expected backend/app and frontend/src)")
    print(f"── {FENCE} → {root}")

    # ── preflight ──
    writes = list(EDITS) + list(NEW_FILES)
    for rel in EDITS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die(f"missing {rel}")
        txt = open(p, encoding="utf-8").read()
        if FENCE in txt:
            die(f"{rel} already carries {FENCE} — already applied")
        for parent in PARENTS.get(rel, []):
            if parent not in txt:
                die(f"{rel} lacks parent fence {parent} — pull the latest main first")
    for rel in NEW_FILES:
        if os.path.exists(os.path.join(root, rel)):
            die(f"{rel} already exists — already applied, or hand-placed? (remove it, run ONLY this script)")
    for m in MIRRORS:
        mp = os.path.join(root, m, SETTINGS)
        if os.path.isfile(mp) and FENCE in open(mp, encoding="utf-8").read():
            die(f"mirror {m}/{SETTINGS} already carries {FENCE} — mixed state; rebuild the mirror first")
    if "--allow-dirty" not in flags and os.path.isdir(os.path.join(root, ".git")):
        try:
            st = subprocess.run(["git", "status", "--porcelain", "--"] + writes, cwd=root,
                                capture_output=True, text=True, timeout=30).stdout
        except Exception as e:
            st = ""
            print(f"  (git status unavailable: {e!r} — continuing)")
        dirty = [ln for ln in st.splitlines() if ln[:2].strip()]
        if dirty:
            die("uncommitted changes in target files (commit/stash, or pass --allow-dirty):\n  "
                + "\n  ".join(dirty))
    staged = {}
    for rel, edits in EDITS.items():
        txt = open(os.path.join(root, rel), encoding="utf-8").read()
        for i, (old, new) in enumerate(edits, 1):
            n = txt.count(old)
            if n != 1:
                die(f"{rel}: anchor {i}/{len(edits)} found {n}× (expected 1):\n{old[:300]}")
            txt = txt.replace(old, new)
        staged[rel] = txt
    for rel, txt in list(staged.items()) + list(NEW_FILES.items()):
        if "eslint-disable" in txt and "eslint-disable" not in (
                open(os.path.join(root, rel), encoding="utf-8").read() if os.path.exists(os.path.join(root, rel)) else ""):
            die(f"internal: {rel} would gain an eslint-disable")
    print(f"  preflight ok — 1 anchored edit, {len(NEW_FILES)} new file")

    # ── write ──
    touched = []

    def write(path, txt):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bak = None
        if os.path.exists(path):
            bak = f"{path}.bak-{FENCE}"
            shutil.copy2(path, bak)
        touched.append((path, bak))
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)

    def rollback(why):
        for path, bak in reversed(touched):
            try:
                if bak:
                    shutil.copy2(bak, path)
                    os.remove(bak)
                elif os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                print(f"  !! could not restore {path}: {e!r}")
        for m in ("",) + MIRRORS:
            dp = os.path.join(root, m, "frontend", "scripts")
            if os.path.isdir(dp) and not os.listdir(dp):
                os.rmdir(dp)
        die(f"{why}\nEvery file it touched was restored.", wrote=True)

    for rel, txt in list(staged.items()) + list(NEW_FILES.items()):
        write(os.path.join(root, rel), txt)
        print(f"  wrote {rel}")
        for m in MIRRORS:
            if os.path.isdir(os.path.join(root, m, "frontend", "src")):
                write(os.path.join(root, m, rel), txt)
                print(f"    mirrored → {m}/{rel}")

    if "--skip-tests" in flags:
        print("\n  --skip-tests: checks not run")
    else:
        run_checks(root, rollback)
    print(f"\n✓ {FENCE} applied. Backups: *.bak-{FENCE}")
    print("  Next: ./desktop/build-scalp.sh frontend")
    print("  Then: Settings → VET V1 → Hedge Wing Max → type the value → Save "
          "(the red 'wing OFF' warning disappears once saved).")


def run_checks(root, rollback):
    node = shutil.which("node")
    fe = None
    for cand in (os.path.join(root, "frontend"),
                 os.path.join(root, "desktop", "src-tauri", "frontend")):
        nm = os.path.join(cand, "node_modules")
        if all(os.path.isdir(os.path.join(nm, p)) for p in
               ("react", "react-dom", "jsdom", "esbuild", "eslint", "eslint-config-react-app")):
            fe = cand
            break
    if not node or not fe:
        print("\n  ⚠ CHECKS NOT RUN: need `node` and node_modules with react/react-dom/jsdom/"
              "esbuild/eslint/eslint-config-react-app\n    (cd frontend && npm ci) and re-run, "
              "or rely on build-scalp.sh's react-scripts build.")
        return
    settings = os.path.join(root, SETTINGS)
    prev = settings + f".bak-{FENCE}"
    harness = os.path.join(fe, "scripts", "test_vet_wing_input.mjs")
    if not os.path.isfile(harness):
        os.makedirs(os.path.dirname(harness), exist_ok=True)
        with open(harness, "w", encoding="utf-8") as f:
            f.write(NEW_FILES[HARNESS])
    print(f"\n── checks (node {subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()}, "
          f"modules from {os.path.relpath(fe, root)})")

    def node_run(argv, label):
        r = subprocess.run([node] + argv, cwd=fe, capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").strip()
        print(f"── {label}\n   " + (out.splitlines()[-1] if out else ""))
        if r.returncode != 0:
            print("\n".join(l for l in (r.stdout + r.stderr).splitlines()
                            if "FAIL" in l or "Error" in l)[-3000:])
            rollback(f"{label} failed")

    node_run([harness, settings], "behaviour: patched handler")
    node_run([harness, prev, "--expect-bug"], "behaviour: pre-fix handler must reproduce the bug")

    # CRA lint: the react-app config react-scripts 5 builds with, before vs after
    cfg = os.path.join(fe, f".eslint-react-app-{FENCE}.json")
    tmp_prev = os.path.join(fe, "scripts", f"__lint_prev_Settings_{FENCE}.jsx")
    with open(cfg, "w") as f:
        f.write('{"root": true, "extends": ["react-app"]}\n')
    shutil.copy2(prev, tmp_prev)
    eslint = os.path.join(fe, "node_modules", "eslint", "bin", "eslint.js")
    env = dict(os.environ, NODE_ENV="production", BABEL_ENV="production",
               BROWSERSLIST_IGNORE_OLD_DATA="1")

    def lint(path):
        r = subprocess.run([node, eslint, "--no-eslintrc", "-c", cfg, "-f", "unix", path],
                           cwd=fe, capture_output=True, text=True, env=env, timeout=300)
        if r.returncode not in (0, 1):
            raise RuntimeError((r.stdout + r.stderr)[-1500:])
        probs = [l for l in r.stdout.splitlines() if re.search(r":\d+:\d+: ", l)]
        return probs, [l for l in probs if "[Error/" in l]

    try:
        try:
            before, _ = lint(tmp_prev)
            after, errs = lint(settings)
            h_probs, _ = lint(harness)
        except Exception as e:
            rollback(f"eslint could not run: {e}")
    finally:
        for p in (cfg, tmp_prev):
            if os.path.exists(p):
                os.remove(p)
    key = lambda l: re.sub(r"^.*?:\d+:\d+: ", "", l)
    new = sorted(set(map(key, after)) - set(map(key, before)))
    print(f"── CRA lint (react-app) Settings.jsx: {len(before)} problems before, {len(after)} after, "
          f"{len(errs)} errors, {len(new)} new")
    if errs or new:
        print("\n".join(errs + new))
        rollback("CRA lint: new problems or errors in Settings.jsx")
    print(f"── CRA lint (react-app) harness: {len(h_probs)} problems")
    if h_probs:
        print("\n".join(h_probs))
        rollback("CRA lint: harness not clean")

    esb = os.path.join(fe, "node_modules", ".bin", "esbuild")
    r = subprocess.run([esb, settings, "--loader:.jsx=jsx", "--log-level=error"],
                       cwd=fe, capture_output=True, text=True, timeout=300)
    print("── esbuild parse Settings.jsx: " + ("ok" if r.returncode == 0 else "FAILED"))
    if r.returncode != 0:
        print(r.stderr[-2000:])
        rollback("esbuild could not parse the patched Settings.jsx")


if __name__ == "__main__":
    main()
