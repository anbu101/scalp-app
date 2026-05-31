# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_all

# pkg_resources (setuptools) lazily imports a whole vendored tree —
# jaraco.*, platformdirs, more_itertools, packaging, etc. PyInstaller's
# static analysis misses them, causing "No module named X" at startup,
# one module at a time. collect_all('pkg_resources') grabs the entire set.
_pkgres_datas, _pkgres_binaries, _pkgres_hidden = collect_all('pkg_resources')
_setuptools_hidden = collect_submodules('setuptools')
_jaraco = collect_submodules('jaraco')

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pkgres_binaries,
    datas=[
        ('app', 'app'),
    ] + _pkgres_datas,
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
    ] + _jaraco + _pkgres_hidden + _setuptools_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter',
        'PIL',
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