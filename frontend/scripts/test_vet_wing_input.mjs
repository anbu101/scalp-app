/* eslint-env node, es2020 */
// frontend/scripts/test_vet_wing_input.mjs — ── VET_WING_INPUT_FIX_20261101 ──
//
// Behavioural check for Settings → VET V1 → "Hedge Wing Max ₹". The bug: the
// field's onChange called updateVET twice; each call cloned the SAME stale
// vetConfig, so the second (hedge_enabled) overwrote the first (the ₹ value)
// and every keystroke snapped back to the saved number.
//
// This mounts a REAL React 18 component in jsdom whose onChange handler and
// updateVET are the EXACT source text cut from Settings.jsx, types into the
// input the way a browser does (native value setter + input event), and checks
// the state. Run against the pre-fix .bak it must reproduce the bug — proof the
// check can fail.
//
//   node frontend/scripts/test_vet_wing_input.mjs <Settings.jsx> [--expect-bug]
// Needs frontend/node_modules (react, react-dom, jsdom, esbuild — all already
// pulled in by react-scripts 5 / the esbuild devDependency).

import { createRequire } from "module";
import { readFileSync } from "fs";
import vm from "vm";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "..", "package.json"));
const { JSDOM } = require("jsdom");
const esbuild = require("esbuild");

const file = process.argv[2];
const expectBug = process.argv.includes("--expect-bug");
const src = readFileSync(file, "utf8");

function balanced(text, openIdx) {            // openIdx points at "{"
  let depth = 0;
  for (let i = openIdx; i < text.length; i++) {
    const ch = text[i];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return text.slice(openIdx, i + 1); }
  }
  throw new Error("unbalanced braces");
}

// the Hedge Wing Max <Input>: anchored on its value binding
const vIdx = src.indexOf("value={vetConfig.hedge_max_premium}");
if (vIdx < 0) throw new Error("Hedge Wing Max input not found");
const ocIdx = src.indexOf("onChange={", vIdx);
const handler = balanced(src, ocIdx + "onChange=".length).slice(1, -1).trim();
const uIdx = src.indexOf("function updateVET(path, value) {");
const updateVET = "function updateVET(path, value) " + balanced(src, src.indexOf("{", uIdx));

const harness = `
const React = require("react");
module.exports = function makeHarness(INIT, onState) {
  return function H() {
    const [vetConfig, setVetConfig] = React.useState(INIT);
    ${updateVET}
    onState(vetConfig);
    return <input type="number" min="0" step="0.5"
      value={vetConfig.hedge_max_premium}
      onChange={${handler}} />;
  };
};`;
const { code } = esbuild.transformSync(harness, { loader: "jsx", format: "cjs" });
const mod = { exports: {} };
vm.runInThisContext(`(function (module, exports, require) {\n${code}\n})`)(mod, mod.exports, require);

const dom = new JSDOM("<!doctype html><div id=root></div>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
if (!("navigator" in globalThis)) globalThis.navigator = dom.window.navigator;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require("react");
const { createRoot } = require("react-dom/client");
const act = React.act || require("react-dom/test-utils").act;

let state = null;
const H = mod.exports({ leg_action: "SELL", hedge_max_premium: 3, hedge_enabled: false },
                      (s) => { state = s; });
const root = createRoot(document.getElementById("root"));
act(() => root.render(React.createElement(H)));
const input = document.querySelector("input");
const setVal = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
function type(v) {
  act(() => { setVal.call(input, v); input.dispatchEvent(new window.Event("input", { bubbles: true })); });
}

const fails = [];
let ok = 0;
function check(name, cond, detail) {
  console.log((cond ? "  PASS  " : "  FAIL  ") + name + (cond ? "" : "  " + JSON.stringify(detail)));
  cond ? ok++ : fails.push(name);
}

type("5");
const sticks = state.hedge_max_premium === 5;
if (expectBug) {
  check("pre-fix handler REPRODUCES the bug (typed 5, value snaps back to 3)",
        state.hedge_max_premium === 3 && input.value === "3", state);
} else {
  check("typed 5 → state hedge_max_premium = 5", sticks, state);
  check("…and the input shows 5", input.value === "5", input.value);
  check("…and hedge_enabled turned ON in the same update", state.hedge_enabled === true, state);
  type("4.5");
  check("typed 4.5 → 4.5 (half-rupee steps)", state.hedge_max_premium === 4.5, state);
  type("0");
  check("typed 0 → 0 and the wing switches OFF (naked)",
        state.hedge_max_premium === 0 && state.hedge_enabled === false, state);
  type("-2");
  check("negative is clamped to 0", state.hedge_max_premium === 0, state);
  type("5");
  check("back to 5 → ON again", state.hedge_max_premium === 5 && state.hedge_enabled === true, state);
  check("other keys untouched", state.leg_action === "SELL", state);
}
act(() => root.unmount());
if (fails.length) { console.log(`\n${ok} passed, ${fails.length} FAILED`); process.exit(1); }
console.log(`\nALL ${ok} VET WING-INPUT CHECKS PASSED`);
