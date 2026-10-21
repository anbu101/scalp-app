#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_ts_funnel_scope.py  —  fence TS_FUNNEL_SCOPE_20261017

WHAT IT FIXES
  The desktop runtime ran `tailscale funnel --bg 47321`, which published the
  WHOLE backend (every API route + the UI) on the public internet at
  https://<machine>.ts.net, with no API auth behind it.

WHAT IT CHANGES
  1. desktop/src-tauri/src/runtime.rs
       start_tailscale_funnel() now:
         - reads `tailscale serve status --json`
         - removes ONLY the legacy public root handler ("/" -> :47321)
         - funnels ONLY /zerodha/callback -> 127.0.0.1:47321/zerodha/callback
       The Kite redirect URL already registered (https://<machine>.ts.net/
       zerodha/callback) keeps working — nobody has to touch the Kite console.
  2. backend/app/utils/funnel_guard.py  (NEW)
       ASGI middleware: any request that came through Tailscale serve/funnel
       may only reach GET /zerodha/callback; everything else -> 404.
       Defence in depth — protects even if an old Funnel config is still live.
  3. backend/app/api_server.py
       Registers FunnelGuard as the OUTERMOST middleware (after CORS).
  4. desktop/build-scalp.sh
       Adds app.utils.funnel_guard to the GATE 2 and GATE 3 module lists.
  5. INSTALLATION.md
       Funnel notes updated (callback-only; the old exposure warning replaced).

  If desktop/src-tauri/backend/app exists (build copy) and matches the source
  tree, the backend edits are mirrored there too (dual-tree rule). Otherwise
  build-scalp.sh re-syncs it on the next build.

UNAFFECTED
  Tauri webview (127.0.0.1:47321), phone on the tailnet (http://100.x.y.z:47321),
  LAN access, scripts on this machine. No strategy, engine or trading code.

RUN (from the repo root, ONLY this script — it carries all payloads):
    python3 apply_ts_funnel_scope.py            # apply
    python3 apply_ts_funnel_scope.py --check    # pre-flight + self-tests, no writes
    python3 apply_ts_funnel_scope.py --allow-dirty   # if target files are modified in git
THEN:
    ./desktop/build-scalp.sh both      (Rust + backend changed)
    Deploy on a non-trading day / outside 09:00–15:30.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FENCE = "TS_FUNNEL_SCOPE_20261017"
BAK = ".bak-" + FENCE

RUNTIME_RS   = Path("desktop/src-tauri/src/runtime.rs")
API_SERVER   = Path("backend/app/api_server.py")
GUARD_PY     = Path("backend/app/utils/funnel_guard.py")
BUILD_SH     = Path("desktop/build-scalp.sh")
INSTALL_MD   = Path("INSTALLATION.md")
COPY_ROOT    = Path("desktop/src-tauri/backend/app")

RUST_REGION_START = "/* =========================================================\n   TAILSCALE FUNNEL\n"
RUST_REGION_END   = "/* =========================================================\n   WATCHDOGS"
RUST_REGION_SHA   = "3764acd61b078336087309cf23fddc3751854cbaf2171708638cf72f780245d7"

# --------------------------------------------------------------------------
# PAYLOADS
# --------------------------------------------------------------------------

GUARD_SRC = r'''# backend/app/utils/funnel_guard.py
# >>> TS_FUNNEL_SCOPE_20261017 <<<
"""
Funnel guard: the backend side of the Tailscale Funnel scope fix.

Until TS_FUNNEL_SCOPE_20261017 the desktop app ran `tailscale funnel --bg 47321`,
which published EVERY route on this backend to the public internet at
https://<machine>.ts.net with no API auth behind it. The desktop runtime now
funnels only /zerodha/callback. This middleware enforces the same rule from
inside the backend, so the API stays private even if an old Funnel config is
still active (an older app build, an old Tailscale CLI, or a manual command).

Rule: a request that arrived through Tailscale's serve/funnel proxy may only be
    GET /zerodha/callback          (Kite's login redirect)
Everything else proxied by Tailscale gets a bare 404.

How a proxied request is recognised (both are set by tailscaled itself, which
also strips any client-supplied copies, so they cannot be forged from outside):
  * Tailscale-Funnel-Request: ?1     -> came in from the public internet
  * X-Forwarded-Host: *.ts.net       -> came in via `tailscale serve`/funnel

Requests that do NOT come through Tailscale's proxy are untouched: the Tauri
webview (127.0.0.1:47321), the phone on the tailnet (http://100.x.y.z:47321),
LAN access, scripts on this machine.

Pure ASGI, no dependencies, so it costs nothing on the normal path.
"""

import time

FENCE = "TS_FUNNEL_SCOPE_20261017"

CALLBACK_PATH = "/zerodha/callback"

# (method, path) pairs a Tailscale-proxied request may reach.
ALLOWED = frozenset({("GET", CALLBACK_PATH)})

_LOG_EVERY_SEC = 10.0


def _audit(msg):
    try:
        from app.event_bus.audit_logger import write_audit_log
        write_audit_log(msg)
    except Exception:
        pass


def _header_values(scope, name):
    """All values of a (lower-case, bytes) header name, decoded latin-1."""
    out = []
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            try:
                out.append(v.decode("latin-1"))
            except Exception:
                out.append("")
    return out


def is_tailscale_proxied(scope):
    """True if tailscaled's serve/funnel proxy forwarded this request."""
    if any(v.strip() for v in _header_values(scope, b"tailscale-funnel-request")):
        return True
    for v in _header_values(scope, b"x-forwarded-host"):
        host = v.split(",")[0].strip().lower()
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if host.endswith(".ts.net"):
            return True
    return False


class FunnelGuard:
    """ASGI middleware. Add it as the OUTERMOST middleware."""

    def __init__(self, app):
        self.app = app
        self._last_log = 0.0
        self._suppressed = 0

    def _log_block(self, scope, method, path):
        now = time.monotonic()
        if now - self._last_log < _LOG_EVERY_SEC:
            self._suppressed += 1
            return
        fwd = (_header_values(scope, b"x-forwarded-for") or ["?"])[0]
        extra = f" (+{self._suppressed} more suppressed)" if self._suppressed else ""
        _audit(f"[FUNNEL_GUARD] blocked {method} {path} via Tailscale proxy from {fwd}{extra}")
        self._last_log = now
        self._suppressed = 0

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        if stype not in ("http", "websocket") or not is_tailscale_proxied(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""

        # Older tailscaled builds proxy an exact-mount request to "<target>/"
        # (trailing slash). FastAPI would answer that with a 307 to an http://
        # URL that does not exist on Funnel, so normalise it here instead.
        if path == CALLBACK_PATH + "/":
            scope = dict(scope)
            scope["path"] = CALLBACK_PATH
            scope["raw_path"] = CALLBACK_PATH.encode("ascii")
            path = CALLBACK_PATH

        if stype == "http" and (scope.get("method", "").upper(), path) in ALLOWED:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "WS") if stype == "http" else "WS"
        self._log_block(scope, method, path)

        if stype == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return

        body = b"Not Found"
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
'''

RUST_BLOCK = r'''/* =========================================================
   TAILSCALE FUNNEL — ZERODHA CALLBACK ONLY
   >>> TS_FUNNEL_SCOPE_20261017 BEGIN <<<

   Before this fence the app ran `tailscale funnel --bg 47321`, which put
   the WHOLE backend (every API route + the UI) on the public internet at
   https://<machine>.ts.net, with no API auth behind it.

   Funnel now publishes exactly ONE path:
       https://<machine>.ts.net/zerodha/callback
           -> http://127.0.0.1:47321/zerodha/callback
   The Kite redirect URL users already registered keeps working unchanged.

   On every backend start:
     1. Read `tailscale serve status --json`.
     2. If the legacy root handler ("/" -> :47321) is present on :443,
        remove ONLY that mount (`tailscale serve --https=443 --set-path=/ off`).
        Any other serve/funnel config on the machine is left alone.
     3. If the callback-only Funnel handler is missing, add it.
   If the status output can't be parsed (very old CLI), steps 2-3 run
   blind; their errors are harmless and only logged.

   Phone access over the tailnet (http://<tailscale-ip>:47321) does not go
   through Funnel and is unaffected. Defence in depth: the backend also
   rejects every Tailscale-proxied request except GET /zerodha/callback
   (backend/app/utils/funnel_guard.py, same fence).
   Completely silent if Tailscale is not installed or not logged in.
   ========================================================= */

const TS_BACKEND_PORT_SUFFIX: &str = ":47321";
const TS_CALLBACK_PATH: &str = "/zerodha/callback";
const TS_CALLBACK_TARGET: &str = "http://127.0.0.1:47321/zerodha/callback";

#[derive(Debug, PartialEq, Eq)]
struct FunnelPlan {
    remove_legacy_root: bool,
    add_callback: bool,
}

fn proxies_to_backend(proxy: &str) -> bool {
    let p = proxy.trim_end_matches('/');
    p.ends_with(TS_BACKEND_PORT_SUFFIX)
}

fn proxies_to_callback(proxy: &str) -> bool {
    let p = proxy.trim_end_matches('/');
    p.ends_with(":47321/zerodha/callback")
}

/// Decide what to change from `tailscale serve status --json` output.
/// None / unparsable -> act blind (remove + add; errors only logged).
/// "null" or "{}"    -> nothing configured -> just add the callback.
fn plan_funnel_changes(status_json: Option<&str>) -> FunnelPlan {
    let blind = FunnelPlan { remove_legacy_root: true, add_callback: true };

    let text = match status_json.map(|s| s.trim()) {
        Some(s) if !s.is_empty() => s,
        _ => return blind,
    };
    let cfg: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return blind,
    };
    if cfg.is_null() {
        return FunnelPlan { remove_legacy_root: false, add_callback: true };
    }
    if !cfg.is_object() {
        return blind;
    }

    let mut remove_legacy_root = false;
    let mut callback_ok = false;

    if let Some(web) = cfg.get("Web").and_then(|w| w.as_object()) {
        for (host_port, site) in web {
            if !host_port.ends_with(":443") {
                continue;
            }
            let handlers = match site.get("Handlers").and_then(|h| h.as_object()) {
                Some(h) => h,
                None => continue,
            };
            let proxy_of = |mount: &str| -> String {
                handlers
                    .get(mount)
                    .and_then(|h| h.get("Proxy"))
                    .and_then(|p| p.as_str())
                    .unwrap_or("")
                    .to_string()
            };
            if proxies_to_backend(&proxy_of("/")) {
                remove_legacy_root = true;
            }
            let funnel_on = cfg
                .get("AllowFunnel")
                .and_then(|a| a.get(host_port.as_str()))
                .and_then(|b| b.as_bool())
                .unwrap_or(false);
            if funnel_on && proxies_to_callback(&proxy_of(TS_CALLBACK_PATH)) {
                callback_ok = true;
            }
        }
    }

    FunnelPlan { remove_legacy_root, add_callback: !callback_ok }
}

fn run_tailscale(bin: &str, args: &[&str], what: &str) -> bool {
    let result = quiet_command(bin)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output();

    match result {
        Ok(out) if out.status.success() => {
            eprintln!("[TAILSCALE] {what}: ok");
            true
        }
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            eprintln!("[TAILSCALE] {what} skipped ({}): {}", out.status, stderr.trim());
            false
        }
        Err(e) => {
            eprintln!("[TAILSCALE] {what} could not run: {e}");
            false
        }
    }
}

fn start_tailscale_funnel() {
    thread::spawn(|| {
        // Small delay — let the backend finish binding its port first
        thread::sleep(Duration::from_secs(2));

        #[cfg(target_os = "windows")]
        let tailscale_bin = "tailscale.exe";

        #[cfg(not(target_os = "windows"))]
        let tailscale_bin = "tailscale";

        let status = quiet_command(tailscale_bin)
            .args(["serve", "status", "--json"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output();

        let status_json: String = match status {
            Err(e) => {
                // Binary not found — Tailscale not installed on this machine
                eprintln!("[TAILSCALE] Not available: {e}");
                return;
            }
            Ok(out) if !out.status.success() => {
                // Not logged in / daemon not running — nothing is being served
                let stderr = String::from_utf8_lossy(&out.stderr);
                eprintln!("[TAILSCALE] Funnel skipped ({}): {}", out.status, stderr.trim());
                return;
            }
            Ok(out) => String::from_utf8_lossy(&out.stdout).into_owned(),
        };

        let plan = plan_funnel_changes(Some(status_json.as_str()));
        eprintln!("[TAILSCALE] Funnel scope plan: {:?}", plan);

        if plan.remove_legacy_root {
            run_tailscale(
                tailscale_bin,
                &["serve", "--https=443", "--set-path=/", "off"],
                "Removed legacy public root (/ -> :47321)",
            );
        }

        if plan.add_callback {
            let set_path = format!("--set-path={}", TS_CALLBACK_PATH);
            run_tailscale(
                tailscale_bin,
                &["funnel", "--bg", "--https=443", set_path.as_str(), TS_CALLBACK_TARGET],
                "Funnel active for /zerodha/callback only",
            );
        } else {
            eprintln!("[TAILSCALE] Funnel already scoped to /zerodha/callback");
        }
    });
}
/* >>> TS_FUNNEL_SCOPE_20261017 END <<< */
'''

CORS_ANCHOR = """app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
"""

API_INSERT = """
# ── TS_FUNNEL_SCOPE_20261017 ── FunnelGuard MUST stay the OUTERMOST middleware
# (Starlette runs the last-added middleware first, so keep this the LAST
# add_middleware call in this file). Anything that arrived via Tailscale
# serve/funnel may only reach GET /zerodha/callback; everything else gets a
# 404 before CORS, routers or the UI mount see it. Local, tailnet-IP and LAN
# access are untouched. See app/utils/funnel_guard.py.
from app.utils.funnel_guard import FunnelGuard as _TS_FunnelGuard
app.add_middleware(_TS_FunnelGuard)
"""

BUILD_EDITS = [
    ("    app.jobs.liveness_ping\n  \"",
     "    app.jobs.liveness_ping\n    app.utils.funnel_guard\n  \""),
    ("    \"app.jobs.liveness_ping\",\n]",
     "    \"app.jobs.liveness_ping\",\n    \"app.utils.funnel_guard\",\n]"),
]

INSTALL_EDITS = [
    ("> **Note on Funnel:** The Scalp app automatically runs Tailscale **Funnel** on port `47321` each time it launches (this is what lets Zerodha's login callback reach your machine over HTTPS). You do **not** need to run any `tailscale funnel` command yourself — the app handles it. You can confirm it is active with `tailscale funnel status`.",
     "> **Note on Funnel:** Each time it launches, the Scalp app configures Tailscale **Funnel** to publish **only** the Zerodha login callback (`/zerodha/callback`) over HTTPS; the rest of the app is not reachable from the internet. You do **not** need to run any `tailscale funnel` command yourself. You can confirm it with `tailscale funnel status`, which should list only `/zerodha/callback`."),
    ("> **Note on Funnel:** The Scalp app starts Tailscale Funnel on port `47321` automatically on launch. No manual `tailscale funnel` command is required.",
     "> **Note on Funnel:** The Scalp app configures Tailscale Funnel for the `/zerodha/callback` path only, automatically on launch. No manual `tailscale funnel` command is required."),
    ("3. The app automatically starts Tailscale Funnel on port `47321` — no manual steps needed",
     "3. The app automatically publishes the Zerodha callback (`/zerodha/callback`) via Tailscale Funnel — no manual steps needed"),
    ("> **Important — public exposure via Funnel:** To let Zerodha's login callback reach your machine, the app enables Tailscale **Funnel** on port `47321`, which exposes that port over the public internet via your Tailscale HTTPS hostname. Anyone who knows your `*.ts.net` hostname could reach the app on that port. Treat the hostname as a secret, do not share it, and avoid posting it anywhere public. (Access over the raw Tailscale IP, e.g. `100.x.y.z:47321`, remains private to your tailnet — only the Funnel hostname is publicly reachable.)",
     "> **What Funnel exposes:** exactly one path, `https://<your-machine>.ts.net/zerodha/callback`, which only accepts a Zerodha login redirect. Every other part of the app returns 404 to Funnel traffic, enforced both in the Tailscale config and inside the backend. Your phone keeps using the raw Tailscale IP (e.g. `100.x.y.z:47321`), which stays private to your tailnet. (Versions before this change exposed the whole app on port `47321`; launching the updated app removes that automatically.)"),
]

# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------

def die(msg):
    print(f"\n[ABORT] {msg}")
    print("[ABORT] Nothing was written.")
    sys.exit(1)

def ok(msg):
    print(f"  ok   {msg}")

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def read(p):
    return p.read_text(encoding="utf-8")

def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n} times (expected exactly 1). File differs from GitHub main — fetch latest and re-check.")
    return text.replace(old, new, 1)

def git_dirty(paths):
    if not Path(".git").exists():
        return None
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--"] + [str(p) for p in paths],
                             capture_output=True, text=True, timeout=20)
        return [l for l in out.stdout.splitlines() if l.strip()]
    except Exception as e:
        print(f"  warn git status failed ({e}) — skipping dirty check")
        return None

# --------------------------------------------------------------------------
# SELF-TESTS (pure ASGI — no FastAPI/Starlette needed)
# --------------------------------------------------------------------------

def run_guard_selftests(guard_path):
    spec = importlib.util.spec_from_file_location("ts_funnel_guard_staged", guard_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    seen = []

    async def inner(scope, receive, send):
        seen.append((scope.get("method"), scope["path"]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner"})

    guard = mod.FunnelGuard(inner)

    async def call(method, path, headers=()):
        scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
                 "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
        out = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(m):
            out.append(m)
        await guard(scope, receive, send)
        return [m for m in out if m["type"] == "http.response.start"][0]["status"]

    async def ws(headers=()):
        scope = {"type": "websocket", "path": "/ws",
                 "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
        out = []
        async def receive():
            return {"type": "websocket.connect"}
        async def send(m):
            out.append(m)
        await guard(scope, receive, send)
        return out

    FUN   = [("Tailscale-Funnel-Request", "?1"), ("X-Forwarded-Host", "m.tail1.ts.net"), ("X-Forwarded-For", "203.0.113.9")]
    SERVE = [("X-Forwarded-Host", "m.tail1.ts.net"), ("X-Forwarded-For", "100.64.0.2")]

    cases = [
        ("funnel GET callback passes",          "GET",  "/zerodha/callback",  FUN,   200),
        ("funnel GET callback/ normalised",     "GET",  "/zerodha/callback/", FUN,   200),
        ("funnel POST configure blocked",       "POST", "/zerodha/configure", FUN,   404),
        ("funnel POST enable-trading blocked",  "POST", "/zerodha/enable-trading", FUN, 404),
        ("funnel GET / (UI) blocked",           "GET",  "/",                  FUN,   404),
        ("funnel GET /status blocked",          "GET",  "/status",            FUN,   404),
        ("funnel POST callback blocked",        "POST", "/zerodha/callback",  FUN,   404),
        ("funnel traversal blocked",            "GET",  "/zerodha/callback/../configure", FUN, 404),
        ("serve (tailnet proxy) blocked",       "GET",  "/status",            SERVE, 404),
        ("xfh with :443 blocked",               "GET",  "/status", [("X-Forwarded-Host", "m.tail1.ts.net:443")], 404),
        ("xfh upper-case blocked",              "GET",  "/status", [("X-Forwarded-Host", "M.TAIL1.TS.NET")], 404),
        ("local desktop request passes",        "GET",  "/status",            [],    200),
        ("local POST configure passes",         "POST", "/zerodha/configure", [],    200),
        ("tailnet IP direct passes",            "GET",  "/status", [("Host", "100.122.185.95:47321")], 200),
        ("foreign xfh passes (not tailscale)",  "GET",  "/status", [("X-Forwarded-Host", "example.com")], 200),
        ("empty funnel header passes",          "GET",  "/status", [("Tailscale-Funnel-Request", "")], 200),
    ]

    async def main():
        failures = []
        for name, m, p, h, want in cases:
            got = await call(m, p, h)
            if got == want:
                ok(f"{name}: {got}")
            else:
                failures.append(name)
                print(f"  FAIL {name}: got {got}, want {want}")
        out = await ws(FUN)
        if not (out and out[-1].get("type") == "websocket.close"):
            failures.append("funnel websocket not closed")
            print("  FAIL funnel websocket not closed")
        else:
            ok("funnel websocket closed (1008)")
        if ("GET", "/zerodha/callback") not in seen:
            failures.append("normalised callback did not reach app as /zerodha/callback")
        if seen.count(("POST", "/zerodha/configure")) != 1:   # only the LOCAL case may reach the app
            failures.append("a blocked request reached the inner app")
        return failures

    failures = asyncio.run(main())
    if failures:
        die(f"guard self-tests failed: {failures}")
    print(f"  self-tests: {len(cases) + 1} scenarios passed")

# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="pre-flight + self-tests only, no writes")
    ap.add_argument("--allow-dirty", action="store_true", help="patch even if target files are modified in git")
    args = ap.parse_args()

    print(f"=== {FENCE} — Tailscale Funnel scoped to /zerodha/callback ===\n")

    # 1. Locate
    for p in (RUNTIME_RS, API_SERVER, BUILD_SH, INSTALL_MD, GUARD_PY.parent):
        if not p.exists():
            die(f"{p} not found — run this from the scalp-app repo root.")
    ok("repo layout")

    rs  = read(RUNTIME_RS)
    api = read(API_SERVER)
    bsh = read(BUILD_SH)
    md  = read(INSTALL_MD)

    # 2. Fence state
    state = {
        "runtime.rs": FENCE in rs,
        "api_server.py": FENCE in api,
        "funnel_guard.py": GUARD_PY.exists(),
        "build-scalp.sh": "app.utils.funnel_guard" in bsh,
    }
    if all(state.values()):
        print("Already applied — nothing to do.")
        return
    if any(state.values()):
        die(f"mixed state {state}. A partial apply or hand-placed file is present. "
            f"Restore from the {BAK} backups / git checkout, then re-run ONLY this script.")
    ok("fence not present anywhere (clean pre-state)")

    # 3. Git dirty check
    dirty = git_dirty([RUNTIME_RS, API_SERVER, BUILD_SH, INSTALL_MD])
    if dirty:
        print("  git reports modified target files:\n    " + "\n    ".join(dirty))
        if not args.allow_dirty:
            die("uncommitted changes on target files (working-copy drift rule). Commit/push first, or re-run with --allow-dirty.")
        print("  warn --allow-dirty given — continuing")
    elif dirty is not None:
        ok("git: target files clean")

    # 4. Pre-state checks
    try:
        s = rs.index(RUST_REGION_START)
        e = rs.index(RUST_REGION_END, s)
    except ValueError:
        die("runtime.rs: TAILSCALE FUNNEL / WATCHDOGS markers not found.")
    region = rs[s:e]
    if sha(region) != RUST_REGION_SHA:
        die("runtime.rs: start_tailscale_funnel region differs from GitHub main "
            f"(sha {sha(region)[:12]}… != {RUST_REGION_SHA[:12]}…). Fetch latest and re-check.")
    if 'args(["funnel", "--bg", "47321"])' not in region:
        die("runtime.rs: legacy funnel call not found in region.")
    ok("runtime.rs region matches GitHub main")

    if api.count("app.add_middleware(") != 1:
        die("api_server.py: expected exactly one add_middleware call (CORS). Ordering assumption broken.")

    # 5. Build new contents
    new_rs  = rs[:s] + RUST_BLOCK + "\n\n" + rs[e:]
    new_api = replace_once(api, CORS_ANCHOR, CORS_ANCHOR + API_INSERT, "api_server.py CORS block")
    new_bsh = bsh
    for old, new in BUILD_EDITS:
        new_bsh = replace_once(new_bsh, old, new, "build-scalp.sh gate list")
    new_md = md
    for old, new in INSTALL_EDITS:
        new_md = replace_once(new_md, old, new, "INSTALLATION.md funnel note")
    ok("all anchors found exactly once")

    # Rust sanity (cargo runs in build-scalp.sh; this catches gross damage)
    if new_rs.count("{") != new_rs.count("}"):
        die("runtime.rs: brace imbalance after edit.")
    if new_rs.count(FENCE) != 2 or 'args(["funnel", "--bg", "47321"])' in new_rs:
        die("runtime.rs: unexpected result after edit.")
    for needle in ("fn plan_funnel_changes", "fn run_tailscale", "fn start_tailscale_funnel",
                   '"--set-path=/", "off"', "TS_CALLBACK_TARGET"):
        if needle not in new_rs:
            die(f"runtime.rs: '{needle}' missing after edit.")
    ok("runtime.rs structural checks")

    # 6. Stage + compile
    tmp = Path(tempfile.mkdtemp(prefix="ts_funnel_scope_"))
    try:
        g = tmp / "funnel_guard.py"; g.write_text(GUARD_SRC, encoding="utf-8")
        a = tmp / "api_server.py";   a.write_text(new_api, encoding="utf-8")
        py_compile.compile(str(g), doraise=True)
        py_compile.compile(str(a), doraise=True)
        ok("py_compile: funnel_guard.py, api_server.py")

        # 7. Self-tests
        run_guard_selftests(g)
    except py_compile.PyCompileError as ex:
        die(f"py_compile failed: {ex}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Build-copy mirror decision
    mirror = []
    if COPY_ROOT.exists():
        copy_api = COPY_ROOT / "api_server.py"
        if copy_api.exists() and read(copy_api) == api and (COPY_ROOT / "utils").is_dir():
            mirror = [(copy_api, new_api), (COPY_ROOT / "utils" / "funnel_guard.py", GUARD_SRC)]
            ok("build copy matches source — will mirror")
        else:
            print("  note build copy differs from source — not mirrored; build-scalp.sh will re-sync it")

    if args.check:
        print("\n--check: all pre-flight checks and self-tests passed. No files written.")
        return

    # 8. Write (all-or-nothing with rollback)
    plan = [(RUNTIME_RS, new_rs), (API_SERVER, new_api), (BUILD_SH, new_bsh),
            (INSTALL_MD, new_md), (GUARD_PY, GUARD_SRC)] + mirror
    written, backups = [], []
    try:
        for path, content in plan:
            if path.exists():
                b = path.with_name(path.name + BAK)
                shutil.copy2(path, b)
                backups.append((path, b))
            t = path.with_name(path.name + ".tmp-" + FENCE)
            t.write_text(content, encoding="utf-8")
            if path == BUILD_SH or os.access(path, os.X_OK):
                os.chmod(t, 0o755)
            os.replace(t, path)
            written.append(path)
    except Exception as ex:
        print(f"\n[ERROR] write failed: {ex} — rolling back")
        for path, b in backups:
            shutil.copy2(b, path)
        for path, _ in plan:
            if path in written and not any(path == p for p, _ in backups):
                path.unlink(missing_ok=True)
        sys.exit(1)

    # 9. Post-verify
    for path, _ in plan:
        if path.suffix in (".rs", ".py") and FENCE not in read(path):
            die(f"post-verify: fence missing in {path}")
    print("\nWritten:")
    for p in written:
        print(f"  {p}")
    print(f"Backups: *{BAK}")

    print(f"""
NEXT
  1. ./desktop/build-scalp.sh both        (Rust + backend changed)
  2. Launch the app, then check:
       tailscale funnel status
         -> should list ONLY  /zerodha/callback  proxy http://127.0.0.1:47321/zerodha/callback
       curl -s -o /dev/null -w "%{{http_code}}\\n" https://<machine>.ts.net/status       -> 404
       curl -s -o /dev/null -w "%{{http_code}}\\n" https://<machine>.ts.net/            -> 404
     (run the two curls from your phone's mobile data or any off-tailnet machine)
  3. Do one Zerodha login from the Connections page -> "Login Successful" tab.
  4. Debug panel: look for [TAILSCALE] Funnel scope plan / [FUNNEL_GUARD] lines.
  Friends' machines are fixed automatically when they install this build.
""")

if __name__ == "__main__":
    main()
