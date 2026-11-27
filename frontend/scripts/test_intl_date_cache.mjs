// frontend/scripts/test_intl_date_cache.mjs — ── INTL_DATE_CACHE_20261127 ── (DEF-115) TC-UI-INTL-0001…0006
// The date-formatter cache gives EXACTLY the text the engine gives, for every way the
// app formats dates, and reuses formatters. (The leak itself is in macOS 26 WebKit;
// node/V8 has no leak, so this checks correctness and reuse; the leak measurement
// is in the defect record: 200,000 calls 3.9 GB → 14 MB with one reused formatter.)
//   node frontend/scripts/test_intl_date_cache.mjs
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const tmp = mkdtempSync(join(tmpdir(), "intlcache_"));
writeFileSync(join(tmp, "m.mjs"), readFileSync(join(HERE, "..", "src", "utils", "intlDateCache.js"), "utf8"));
const M = await import(pathToFileURL(join(tmp, "m.mjs")).href);

let fails = 0, ok = 0;
const check = (name, cond, detail = "") => {
  if (cond) { ok++; console.log("  PASS  " + name); }
  else { fails++; console.log("  FAIL  " + name + "  [" + String(detail).slice(0, 400) + "]"); }
};

const IST = "Asia/Kolkata";
const dates = [new Date(), new Date(1795744409000), new Date(Date.UTC(2026, 10, 26, 18, 30)), new Date(0), new Date(1e12)];
const locales = [undefined, "en-IN", "en-CA", "en-US", ["en-IN", "en"], "en-GB"];
const opts = [undefined, {}, { timeZone: IST }, { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: IST },
  { day: "2-digit", month: "short", timeZone: IST }, { weekday: "short", timeZone: IST }, { dateStyle: "medium", timeZone: IST },
  { timeStyle: "short", timeZone: IST }, { hour: "numeric" }, { year: "numeric", month: "2-digit", day: "2-digit", timeZone: IST },
  { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: IST }, { month: "short", year: "2-digit" },
  { dateStyle: "short", timeStyle: "short", timeZone: IST }];
const methods = ["toLocaleString", "toLocaleDateString", "toLocaleTimeString"];

const run = () => {
  const out = [];
  for (const m of methods) for (const d of dates) for (const l of locales) for (const o of opts) {
    let v;
    try { v = d[m](l, o); } catch (e) { v = "THROWS " + e.constructor.name; }
    out.push(`${m}|${d.getTime()}|${JSON.stringify(l)}|${JSON.stringify(o)} => ${v}`);
  }
  out.push("invalid " + new Date(NaN).toLocaleString("en-IN", { timeZone: IST }));
  out.push("invalid " + new Date(NaN).toLocaleDateString());
  return out;
};
const NativeDTF = Intl.DateTimeFormat;
const before = run();
const installed = M.install(globalThis);
const after = run();
const diff = before.map((b, i) => (b === after[i] ? null : `${b}  ≠  ${after[i]}`)).filter(Boolean);
check(`TC-UI-INTL-0001 identical text to the engine for ${before.length} date/time calls (3 methods × dates × locales × options, incl. IST, `
      + "dateStyle/timeStyle, default fields, the spec's TypeErrors and Invalid Date)", installed && diff.length === 0, diff.slice(0, 3).join(" | "));

const a = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: IST });
const b = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: IST });
const c = Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: IST });
check("TC-UI-INTL-0002 new Intl.DateTimeFormat with the same arguments returns ONE reused formatter (with or without new)",
      a === b && b === c);
check("TC-UI-INTL-0003 the reused formatter is a real DateTimeFormat: instanceof, resolvedOptions, formatToParts, supportedLocalesOf",
      a instanceof Intl.DateTimeFormat && a instanceof NativeDTF && a.resolvedOptions().timeZone === new NativeDTF("en-IN", { timeZone: IST }).resolvedOptions().timeZone
      && Array.isArray(a.formatToParts(new Date())) && Intl.DateTimeFormat.supportedLocalesOf(["en-IN"]).length === 1);

const get = M.makeCache(NativeDTF);
let same = true;
const f1 = get("en-IN", { timeZone: IST });
for (let i = 0; i < 1000; i++) same = same && get("en-IN", { timeZone: IST }) === f1;
for (let i = 0; i < 1000; i++) get("en-IN", { timeZone: IST, minute: i % 2 ? "2-digit" : "numeric", hour: "2-digit", second: String(i) === "x" ? "2-digit" : undefined, era: i > 997 ? "short" : undefined, fractionalSecondDigits: (i % 3) + 1 });
check("TC-UI-INTL-0004 repeated calls reuse one formatter (1,000 calls → 1 object); the cache stays bounded",
      same && get.size() <= 400, get.size());

check("TC-UI-INTL-0005 install is idempotent (a second call changes nothing)", M.install(globalThis) === false && Intl.DateTimeFormat.__native === NativeDTF);

const idx = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
const firstImport = idx.indexOf("import ");
check("TC-UI-INTL-0006 index.js installs the cache before anything renders (first import, called before root.render)",
      idx.indexOf('import { install as installIntlDateCache } from "./utils/intlDateCache"') === firstImport
      && idx.indexOf("installIntlDateCache();") > -1 && idx.indexOf("installIntlDateCache();") < idx.indexOf("root.render("));

console.log(`\n${fails ? fails + " FAILED" : "ALL PASSED"}  (${ok}/${ok + fails})`);
process.exit(fails ? 1 : 0);
