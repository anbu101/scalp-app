// frontend/src/utils/intlDateCache.js — ── INTL_DATE_CACHE_20261127 ── (DEF-115)
//
// WHY. On macOS 26 (Dhivakar's Mac, 26.6.2), WebKit's date formatting leaks an
// ICU TimeZone (~20 KB) on EVERY Date#toLocale*String / new Intl.DateTimeFormat
// call that builds a formatter. The dashboard formats IST times in its tables on
// every render (pollers every 3–15 s × 12-row tables × several fields), about
// 1,200 calls a second with live strategies on screen: the app's web view grew
// ~30 MB a minute to 8.2 GB and macOS killed it at 14:10 on 27 Nov — the window
// went blank grey. Measured in the system JavaScriptCore: 200,000 calls → 3.9 GB;
// one reused formatter → 14 MB. macOS 27 does not leak.
//
// WHAT. install() (called first in index.js) makes every formatter REUSED:
//   * new Intl.DateTimeFormat(locales, options) returns a cached instance per
//     (locales, options) — formatters are immutable, so sharing is safe;
//   * Date#toLocaleString / toLocaleDateString / toLocaleTimeString format with
//     the cached formatter, after adding the same default fields the spec adds
//     (ECMA-402 ToDateTimeOptions), so the text is exactly what the browser gave.
// Any error falls back to the browser's own method. Idempotent.
//
// No call site changes: the ~100 existing IST formatting calls, and new ones,
// are all covered. frontend/scripts/test_intl_date_cache.mjs checks the output is identical.

const CACHE_MAX = 400;
const DATE_FIELDS = ["weekday", "year", "month", "day"];
const TIME_FIELDS = ["dayPeriod", "hour", "minute", "second", "fractionalSecondDigits"];

export function makeCache(NativeDTF) {
  const cache = new Map();
  function get(locales, options) {
    const key = JSON.stringify([locales === undefined ? null : locales, options === undefined ? null : options]);
    let f = cache.get(key);
    if (!f) {
      f = new NativeDTF(locales, options);
      if (cache.size >= CACHE_MAX) cache.clear();
      cache.set(key, f);
    }
    return f;
  }
  get.size = () => cache.size;
  return get;
}

// ECMA-402 ToDateTimeOptions(options, required, defaults)
export function withDefaults(options, required, defaults) {
  const o = options === undefined || options === null ? {} : Object.assign({}, options);
  let need = true;
  if (required === "date" || required === "any") {
    for (const p of DATE_FIELDS) if (o[p] !== undefined) need = false;
  }
  if (required === "time" || required === "any") {
    for (const p of TIME_FIELDS) if (o[p] !== undefined) need = false;
  }
  if (o.dateStyle !== undefined || o.timeStyle !== undefined) need = false;
  if (required === "date" && o.timeStyle !== undefined) throw new TypeError("timeStyle with toLocaleDateString");
  if (required === "time" && o.dateStyle !== undefined) throw new TypeError("dateStyle with toLocaleTimeString");
  if (need && (defaults === "date" || defaults === "all")) {
    o.year = "numeric"; o.month = "numeric"; o.day = "numeric";
  }
  if (need && (defaults === "time" || defaults === "all")) {
    o.hour = "numeric"; o.minute = "numeric"; o.second = "numeric";
  }
  return o;
}

export function install(root) {
  const g = root || (typeof globalThis !== "undefined" ? globalThis : window);
  if (!g || !g.Intl || !g.Intl.DateTimeFormat || g.Intl.DateTimeFormat.__scalpCached) return false;
  const NativeDTF = g.Intl.DateTimeFormat;
  const get = makeCache(NativeDTF);

  function CachedDateTimeFormat(locales, options) {
    try {
      return get(locales, options);
    } catch (e) {
      return new NativeDTF(locales, options);   // let the browser raise its own error
    }
  }
  CachedDateTimeFormat.prototype = NativeDTF.prototype;
  CachedDateTimeFormat.supportedLocalesOf = NativeDTF.supportedLocalesOf.bind(NativeDTF);
  Object.defineProperty(CachedDateTimeFormat, "__scalpCached", { value: true });
  Object.defineProperty(CachedDateTimeFormat, "__native", { value: NativeDTF });
  g.Intl.DateTimeFormat = CachedDateTimeFormat;

  const DP = g.Date.prototype;
  const patch = (name, required, defaults) => {
    const original = DP[name];
    DP[name] = function (locales, options) {
      try {
        const t = g.Date.prototype.getTime.call(this);
        if (Number.isNaN(t)) return "Invalid Date";
        return get(locales, withDefaults(options, required, defaults)).format(t);
      } catch (e) {
        return original.call(this, locales, options);
      }
    };
  };
  patch("toLocaleString", "any", "all");
  patch("toLocaleDateString", "date", "date");
  patch("toLocaleTimeString", "time", "time");
  return true;
}
