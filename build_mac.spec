# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller .app spec for macOS — output: dist/本地服务管理.app"""

import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app_icon import write_icns

APP_ICON = write_icns(ROOT / "build" / "app.icns")

a = Analysis(
    [str(ROOT / "backend" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "frontend"), "frontend"),
    ],
    hiddenimports=[
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "fastapi",
        "starlette",
        "starlette.routing",
        "starlette.staticfiles",
        "pydantic",
        "httpx",
        "httpx._transports",
        "httpx._transports.default",
        "anyio",
        "anyio._backends._asyncio",
        "psutil",
        "webview",
        "pystray",
        "pystray._darwin",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "backend",
        "backend.main",
        "backend.config",
        "backend.services",
        "backend.scanner",
        "backend.port_check",
        "backend.process_info",
        "backend.process_runner",
        "backend.icon_fetcher",
        "backend.folder_picker",
        "backend.app_icon",
        "backend.desktop",
        "backend.mac_native",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="本地服务管理",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="本地服务管理",
)

app = BUNDLE(
    coll,
    name="本地服务管理.app",
    icon=str(APP_ICON),
    bundle_identifier="com.local.serviceshome",
    info_plist={
        "CFBundleName": "本地服务管理",
        "CFBundleDisplayName": "本地服务管理",
        "CFBundleShortVersionString": "1.4.0",
        "CFBundleVersion": "1.4.0",
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
)
