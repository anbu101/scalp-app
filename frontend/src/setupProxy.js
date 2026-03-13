/**
 * src/setupProxy.js
 *
 * Forwards /zerodha/* to the FastAPI backend.
 * changeOrigin is intentionally NOT set — the original Host header
 * (e.g. anbarasus-macbook-air-5.tail7ecd0a.ts.net) must reach the
 * backend so it can construct the correct redirect URL after login.
 *
 * Requires DANGEROUSLY_DISABLE_HOST_CHECK=true in .env
 */

const { createProxyMiddleware } = require("http-proxy-middleware");

module.exports = function (app) {
  app.use(
    "/zerodha",
    createProxyMiddleware({
      target:   "http://localhost:47321",
      logLevel: "silent",
      // changeOrigin intentionally omitted — preserve original Host header
    })
  );
};