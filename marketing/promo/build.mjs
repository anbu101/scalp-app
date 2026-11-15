// Build one or more videos:  node build.mjs trailer-mass [promo-landscape …]   (no args = all)
// Bundles src/videos/<name>.jsx with the REAL app components (@app → ../../frontend/src)
// into dist/<name>.{js,css,html}; copies src/assets/* next to them.
import * as esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "../../frontend/src");
const stub = path.resolve(HERE, "src/lib/replayStub.js");
let names = process.argv.slice(2);
if (!names.length) names = fs.readdirSync(path.join(HERE, "src/videos")).filter((f) => f.endsWith(".jsx")).map((f) => f.replace(/\.jsx$/, ""));
if (!fs.existsSync(APP)) { console.error(`app source not found at ${APP}`); process.exit(1); }

await esbuild.build({
  entryPoints: Object.fromEntries(names.map((n) => [n, path.join(HERE, `src/videos/${n}.jsx`)])),
  bundle: true, outdir: path.join(HERE, "dist"), format: "iife", jsx: "automatic",
  loader: { ".js": "jsx", ".woff2": "dataurl", ".woff": "dataurl", ".png": "dataurl", ".svg": "dataurl" },
  alias: { "@app": APP }, nodePaths: [path.join(HERE, "node_modules")],
  define: { "process.env.NODE_ENV": '"production"' },
  // Trade Replay pulls in the API layer; the promo stage never needs it
  plugins: [{ name: "stub", setup(b) { b.onResolve({ filter: /replay\/ReplayHost$/ }, () => ({ path: stub })); } }],
  logLevel: "warning",
});
const assets = path.join(HERE, "src/assets");
if (fs.existsSync(assets)) for (const f of fs.readdirSync(assets)) fs.copyFileSync(path.join(assets, f), path.join(HERE, "dist", f));
for (const n of names) {
  fs.writeFileSync(path.join(HERE, `dist/${n}.html`),
    `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="${n}.css">` +
    `<style>html,body{margin:0;padding:0;overflow:hidden;background:#000!important;background-image:none!important}</style></head>` +
    `<body><div id="root"></div><script src="${n}.js"></script></body></html>`);
}
console.log("built:", names.join(", "));
