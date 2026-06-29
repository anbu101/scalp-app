# -*- mode: python ; coding: utf-8 -*-
#
# ONEDIR build (was --onefile). Rationale:
#   - --onefile unpacked the ENTIRE bundle to a temp dir on every launch
#     before Python ran. With numpy/pandas/matplotlib + the full
#     pkg_resources/jaraco/setuptools vendored tree, that unpack grew every
#     release and dominated cold-start (worst on macOS, where Gatekeeper
#     re-verifies the freshly-extracted Mach-O + every .so each launch).
#   - --onedir emits a directory: launcher + _internal/ already on disk.
#     No per-launch extraction; macOS can cache signature verification.
#
# UPX is OFF: on macOS it can break/slow startup and adds per-file
# decompression to the launch path. Disk savings are irrelevant here.
#
# Output: dist/scalp-backend/  (directory)
#   dist/scalp-backend/scalp-backend       <- launcher (same name as before)
#   dist/scalp-backend/_internal/...        <- libs, data, .so/.dll
#
# CI copies the CONTENTS of dist/scalp-backend/ into
# desktop/src-tauri/backend/ so the launcher lands at
# desktop/src-tauri/backend/scalp-backend (unchanged location for Tauri /
# runtime.rs) with _internal/ beside it. Tauri's resources:["backend"]
# bundles the whole dir, so _internal/ rides along automatically.
#
# ----------------------------------------------------------------------
# WINDOWS LAZY-IMPORT FIX (added 2026-06-29)
# ----------------------------------------------------------------------
# Symptom (Windows bundle only; Mac-from-source fine):
#     ModuleNotFoundError: No module named 'app.backtest.data'
# raised from a FUNCTION-LEVEL import inside run_scalpv5_backtest
# (`from app.backtest.data.candle_source import CandleSource`).
# Cause: datas=[('app','app')] ships the .py files as DATA, but Python
# imports modules from the PYZ archive (the compiled module graph), not
# from loose data files. PyInstaller's static analysis only adds modules it
# can TRACE to the PYZ; deep, lazily-imported submodules (app.backtest.data,
# app.backtest.engine, app.backtest.scalpv5, charges modules, …) are missed,
# so importing them at runtime fails even though the file sits in app/.
# Fix: collect_submodules('app') enumerates EVERY app.* submodule and forces
# them all into the PYZ as importable modules. Bracketed APP_SUBMODULES.
# ----------------------------------------------------------------------
#
# ----------------------------------------------------------------------
# WINDOWS _ssl FIX (added)
# ----------------------------------------------------------------------
# Symptom on a CLEAN Windows machine (not the CI runner):
#     ImportError: DLL load failed while importing _ssl:
#     Invalid access to memory location.
# Cause: PyInstaller bundles _ssl.pyd / _hashlib.pyd but its analysis often
# MISSES the OpenSSL DLLs they dynamically link against
# (libssl-3-x64.dll / libcrypto-3-x64.dll on modern CPython, or
# libssl-1_1.dll / libcrypto-1_1.dll on older). On the CI runner those DLLs
# exist system-wide so the import "works" there (false pass); on a machine
# without them next to the app, the load fails.
# Fix: explicitly locate and bundle those DLLs into _internal/ so the app
# is self-contained on every Windows box. (Harmless no-op on macOS, where
# the glob simply finds nothing.)
#
# ----------------------------------------------------------------------
# WINDOWS_SSL_STRIP_FIX (added 2026-06-29)
# ----------------------------------------------------------------------
# Regression after the recent release: clean Windows machines still crash
# with "_ssl: Invalid access to memory location" even though CI passed.
# Two compounding causes were fixed:
#   1) strip=True (in EXE and COLLECT) runs the Unix `strip` tool against
#      Windows DLLs/PYDs, which can corrupt _ssl.pyd / libssl / libcrypto.
#      The CI smoke test never exercises the bundled copy (the runner has
#      system OpenSSL), so the corruption ships silently. Set strip=False.
#      (The startup win comes from --onedir, NOT from stripping.)
#   2) _collect_openssl_dlls() now treats the CI `pythonLocation` env var as
#      authoritative for locating the OpenSSL DLLs, since sys.executable is
#      not always co-located with the DLLs dir under setup-python.
# All edits are bracketed with WINDOWS_SSL_STRIP_FIX BEGIN/END.

import os
import sys
import glob

from PyInstaller.utils.hooks import collect_submodules, collect_all, collect_data_files

# pkg_resources (setuptools) lazily imports a whole vendored tree —
# jaraco.*, platformdirs, more_itertools, packaging, etc. PyInstaller's
# static analysis misses them, causing "No module named X" at startup,
# one module at a time. collect_all('pkg_resources') grabs the entire set.
_pkgres_datas, _pkgres_binaries, _pkgres_hidden = collect_all('pkg_resources')
_setuptools_hidden = collect_submodules('setuptools')
_jaraco = collect_submodules('jaraco')

# APP_SUBMODULES BEGIN
# Collect EVERY app.* submodule into the PYZ. The runner uses function-level
# (lazy) imports — e.g. app.backtest.data.candle_source imported inside
# run_scalpv5_backtest — which PyInstaller's static analysis does not trace,
# so they never reach the importable module graph and fail at runtime on the
# Windows bundle with ModuleNotFoundError. This forces all of them in.
# collect_data_files('app') additionally ensures non-.py files that the code
# reads from disk (e.g. backtest/repo/schema.sql via _schema_sql()) ship too.
_app_hidden = collect_submodules('app')
_app_datas = collect_data_files('app')
# APP_SUBMODULES END

# matplotlib: the EOD summary card renders a PNG via the Agg backend. matplotlib
# ships data files (fonts, mpl-data) that PyInstaller's static analysis does not
# pick up, and the backend modules are imported lazily. Collect BOTH the data
# files and the backend submodules, and wire them into Analysis below.
# NOTE: matplotlib MUST NOT appear in `excludes` — and neither may PIL/Pillow,
# which matplotlib imports internally for image handling (Agg PNG save).
mpl_datas = collect_data_files("matplotlib")
mpl_hidden = collect_submodules("matplotlib.backends")

# ----------------------------------------------------------------------
# OpenSSL DLL collection (Windows _ssl fix). On macOS/Linux this yields an
# empty list (no libssl*.dll), so it's inert there.
# ----------------------------------------------------------------------
def _collect_openssl_dlls():
    # WINDOWS_SSL_STRIP_FIX BEGIN
    # pythonLocation is set by actions/setup-python and is the authoritative
    # root of the CI interpreter; the CI debug step already proves the
    # OpenSSL DLLs live under it. Put it (and its DLLs/ subdir) FIRST so we
    # don't depend on sys.executable being co-located with the DLLs dir.
    _py_loc = os.environ.get("pythonLocation", "")
    search_dirs = [
        _py_loc,
        os.path.join(_py_loc, "DLLs") if _py_loc else "",
        os.path.join(os.path.dirname(sys.executable), "DLLs"),
        os.path.dirname(sys.executable),
        os.path.join(sys.base_prefix, "DLLs"),
        sys.base_prefix,
        os.path.join(sys.base_prefix, "Library", "bin"),  # conda-style layouts
    ]
    # WINDOWS_SSL_STRIP_FIX END
    patterns = ("libssl*.dll", "libcrypto*.dll")
    found = []
    seen = set()
    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        for pat in patterns:
            for p in glob.glob(os.path.join(d, pat)):
                name = os.path.basename(p).lower()
                if name not in seen:
                    seen.add(name)
                    # destination "." => placed at the root of _internal/
                    found.append((p, "."))
    return found

_ssl_binaries = _collect_openssl_dlls()

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pkgres_binaries + _ssl_binaries,
    datas=[
        ('app', 'app'),
    ] + _pkgres_datas + mpl_datas + _app_datas,
    hiddenimports=[
        # FastAPI and dependencies
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',

        'logging.handlers',

        # SSL / hashing — ensure the C extensions are pulled in so their
        # accompanying OpenSSL DLLs (collected above) are actually needed
        # and loaded. Belt-and-suspenders against the _ssl load failure.
        '_ssl',
        '_hashlib',
        'ssl',
        'hashlib',

        # pkg_resources vendored deps (explicit, in case collection misses)
        'jaraco.text',
        'jaraco.functools',
        'jaraco.context',
        'jaraco.collections',
        'platformdirs',
        'more_itertools',
        'packaging',
        'packaging.version',
        'packaging.specifiers',
        'packaging.requirements',
        'pkg_resources',
        'importlib_metadata',

        # FastAPI internals
        'fastapi',
        'starlette',
        'pydantic',

        # Your app modules (explicit anchors; the full set comes from
        # _app_hidden = collect_submodules('app') merged below)
        'app.api_server',
        'app.datastore',
        'app.schemas',

        # Additional imports
        'numpy',
        'pandas',
        'httpx',
        'websockets',
        'kiteconnect',
        'apscheduler',
        'jwt',

        # matplotlib — Agg backend (headless PNG render for the EOD card)
        'matplotlib.backends.backend_agg',
    ] + _jaraco + _pkgres_hidden + _setuptools_hidden + mpl_hidden + _app_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # matplotlib and PIL deliberately NOT excluded — the EOD card needs them.
        'tkinter',
        'IPython',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ONEDIR: exclude_binaries=True moves a.binaries into COLLECT instead of
# folding them into a single self-extracting EXE. UPX off everywhere.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='scalp-backend',
    debug=False,
    bootloader_ignore_signals=False,
    # WINDOWS_SSL_STRIP_FIX BEGIN
    # Was strip=True. The Unix `strip` tool can corrupt Windows DLLs/PYDs
    # (including _ssl.pyd and the OpenSSL DLLs), producing a build that
    # passes CI (runner has system OpenSSL) but crashes on clean machines.
    strip=False,
    # WINDOWS_SSL_STRIP_FIX END
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    # WINDOWS_SSL_STRIP_FIX BEGIN
    # Was strip=True. Same reason as above — never strip the collected
    # binaries on Windows; it corrupts the bundled OpenSSL DLLs.
    strip=False,
    # WINDOWS_SSL_STRIP_FIX END
    upx=False,
    upx_exclude=[],
    name='scalp-backend',
)