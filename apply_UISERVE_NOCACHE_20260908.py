#!/usr/bin/env python3
"""
apply_UISERVE_NOCACHE_20260908.py — FENCE: UISERVE_NOCACHE_20260908

Why: the SCALP_UI_SERVE mount serves the React build with Starlette's
StaticFiles, which sends ETag/Last-Modified but NO Cache-Control. Mobile
browsers then heuristically cache index.html; after a frontend rebuild the
cached shell references static/js/main.<oldhash>.js, which no longer exists,
so the phone renders a blank page until site data is cleared. Hashed assets
are immutable and can stay cacheable — only the shell must be revalidated.

What: subclass StaticFiles inside the SCALP_UI_SERVE block and stamp
`Cache-Control: no-cache` on any HTML response it serves ("/", "/index.html",
and the html=True fallback). Assets (js/css/png/woff) untouched. The class
is used in the existing app.mount() call; everything else in the block is
as before.

Safety: fence check, py_compile gate, behavioural test on the patched module
via TestClient against a temp build dir (shell no-cache, asset not stamped),
.bak backup, dual-tree mirror when present. Backend-only → PyInstaller rebuild.

Run from the repo root:  python3 apply_UISERVE_NOCACHE_20260908.py
"""
import os, py_compile, shutil, sys, tempfile

FENCE = "UISERVE_NOCACHE_20260908"
ROOT = os.path.abspath(os.getcwd())
REL = "app/api_server.py"
BACKEND = os.path.join(ROOT, "backend")
DUAL = os.path.join(ROOT, "desktop", "src-tauri", "backend")
P = os.path.join(BACKEND, REL)
if not os.path.isfile(P):
    sys.exit("ABORT: run from the scalp-app repo root")


def die(m): sys.exit(f"ABORT: {m}")
def sub1(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die(f"{label}: anchor found {n}x (need exactly 1)")
    return text.replace(old, new)


with open(P, encoding="utf-8") as f:
    src = f.read()
if FENCE in src:
    die(f"{FENCE} already present — nothing to do")

src = sub1(src,
    'from fastapi.staticfiles import StaticFiles as _UISERVE_StaticFiles\n',
    'from fastapi.staticfiles import StaticFiles as _UISERVE_StaticFiles\n'
    '\n'
    '# ── UISERVE_NOCACHE_20260908 ── the shell (index.html) must always be\n'
    '# revalidated: a cached shell after a rebuild points at a hashed bundle\n'
    '# that no longer exists → blank page on the phone. Hashed assets are\n'
    '# immutable and keep default caching. no-cache (not no-store) still lets\n'
    '# the browser use the ETag/304 path, so it costs nothing when unchanged.\n'
    'class _UISERVE_NoCacheShell(_UISERVE_StaticFiles):\n'
    '    async def get_response(self, path, scope):\n'
    '        resp = await super().get_response(path, scope)\n'
    '        ctype = resp.headers.get("content-type", "")\n'
    '        if ctype.startswith("text/html"):\n'
    '            resp.headers["Cache-Control"] = "no-cache"\n'
    '        return resp\n',
    "class insert")
src = sub1(src,
    '    app.mount(\n'
    '        "/",\n'
    '        _UISERVE_StaticFiles(directory=str(_uiserve_build_dir), html=True),\n'
    '        name="scalp_ui_serve",\n'
    '    )\n',
    '    app.mount(\n'
    '        "/",\n'
    '        _UISERVE_NoCacheShell(directory=str(_uiserve_build_dir), html=True),   # ── UISERVE_NOCACHE_20260908 ──\n'
    '        name="scalp_ui_serve",\n'
    '    )\n',
    "mount swap")

# ── py_compile gate ────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix=FENCE + "_")
tmpf = os.path.join(TMP, "api_server.py")
with open(tmpf, "w", encoding="utf-8") as f:
    f.write(src)
try:
    py_compile.compile(tmpf, doraise=True)
except py_compile.PyCompileError as e:
    die(f"py_compile failed: {e}")
print("py_compile gate: OK")

# ── behavioural test: the class itself, mounted on a throwaway app ─────
# (api_server.py is not importable standalone — it boots the engine — so the
# class body is exec'd from the patched text and tested in isolation.)
start = src.index("class _UISERVE_NoCacheShell")
end = src.index("        return resp\n", start) + len("        return resp\n")
ns = {}
from fastapi.staticfiles import StaticFiles as _UISERVE_StaticFiles
ns["_UISERVE_StaticFiles"] = _UISERVE_StaticFiles
exec(src[start:end], ns)
Cls = ns["_UISERVE_NoCacheShell"]

build = os.path.join(TMP, "build"); os.makedirs(os.path.join(build, "static", "js"))
open(os.path.join(build, "index.html"), "w").write("<!doctype html><script src=static/js/main.abc.js></script>")
open(os.path.join(build, "static", "js", "main.abc.js"), "w").write("console.log(1)")
from fastapi import FastAPI
import asyncio
tapp = FastAPI()
@tapp.get("/boot-status")
def _bs(): return {"ok": True}
tapp.mount("/", Cls(directory=build, html=True), name="scalp_ui_serve")

def GET(path):
    """Drive the ASGI app directly — no TestClient/httpx (httpx 0.28 +
    Starlette 0.36 on the build Mac broke TestClient's constructor)."""
    scope = {"type": "http", "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": [(b"host", b"test")],
             "client": ("127.0.0.1", 1), "server": ("test", 80),
             "root_path": "", "app": tapp}
    out = {"status": None, "headers": {}, "body": b""}
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")
    asyncio.run(tapp(scope, receive, send))
    return out

FAILS = []
def check(name, ok, note=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + note) if (note and not ok) else ''}")
    if not ok: FAILS.append(name)
r = GET("/")
check("shell '/' → 200 html with no-cache", r["status"] == 200 and r["headers"].get("cache-control") == "no-cache", str(r["headers"]))
r = GET("/index.html")
check("'/index.html' → no-cache", r["headers"].get("cache-control") == "no-cache")
r = GET("/static/js/main.abc.js")
check("hashed asset → 200, NOT stamped", r["status"] == 200 and "cache-control" not in r["headers"], str(r["headers"]))
check("asset still has ETag (304 path intact)", "etag" in r["headers"])
r = GET("/boot-status")
check("explicit route above the mount still wins", r["status"] == 200 and b'"ok":true' in r["body"])
r = GET("/static/js/missing.js")
check("missing asset → 404 (no html fallback for assets)", r["status"] == 404)
if FAILS:
    die(f"tests failed: {FAILS}")
print("behavioural tests: OK")

# ── write ───────────────────────────────────────────────────────────────
shutil.copy2(P, P + f".bak-{FENCE}")
with open(P, "w", encoding="utf-8") as f:
    f.write(src)
print(f"written {REL} (+ .bak-{FENCE})")
if os.path.isdir(DUAL):
    shutil.copy2(P, os.path.join(DUAL, REL)); print("dual backend tree synced")
shutil.rmtree(TMP, ignore_errors=True)
print(f"DONE — {FENCE}. Backend-only: ./desktop/build-scalp.sh backend")
