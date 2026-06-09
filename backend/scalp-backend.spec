# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_all, collect_data_files

# pkg_resources (setuptools) lazily imports a whole vendored tree —
# jaraco.*, platformdirs, more_itertools, packaging, etc. PyInstaller's
# static analysis misses them, causing "No module named X" at startup,
# one module at a time. collect_all('pkg_resources') grabs the entire set.
_pkgres_datas, _pkgres_binaries, _pkgres_hidden = collect_all('pkg_resources')
_setuptools_hidden = collect_submodules('setuptools')
_jaraco = collect_submodules('jaraco')

# matplotlib: the EOD summary card renders a PNG via the Agg backend. matplotlib
# ships data files (fonts, mpl-data) that PyInstaller's static analysis does not
# pick up, and the backend modules are imported lazily. Collect BOTH the data
# files and the backend submodules, and wire them into Analysis below.
# NOTE: matplotlib MUST NOT appear in `excludes` — and neither may PIL/Pillow,
# which matplotlib imports internally for image handling (Agg PNG save).
mpl_datas = collect_data_files("matplotlib")
mpl_hidden = collect_submodules("matplotlib.backends")

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pkgres_binaries,
    datas=[
        ('app', 'app'),
    ] + _pkgres_datas + mpl_datas,
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

        # Your app modules
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

        # matplotlib — Agg backend (headless PNG render for the EOD card)
        'matplotlib.backends.backend_agg',
    ] + _jaraco + _pkgres_hidden + _setuptools_hidden + mpl_hidden,
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='scalp-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)
