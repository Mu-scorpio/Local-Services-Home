# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 本地服务管理 desktop app."""

import os
import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Generate the Windows icon from the same artwork used by the tray and web UI.
from backend.app_icon import write_ico

APP_ICON = write_ico(ROOT / 'frontend' / 'assets' / 'app-icon.ico')

a = Analysis(
    [str(ROOT / 'backend' / 'desktop.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'frontend'), 'frontend'),
        (str(ROOT / 'backend'), 'backend'),
        (str(ROOT / 'data'), 'data'),
    ],
    hiddenimports=[
        'uvicorn',
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
        'uvicorn.lifespan.off',
        'fastapi',
        'starlette',
        'starlette.routing',
        'starlette.staticfiles',
        'pydantic',
        'httpx',
        'psutil',
        'webview',
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'clr',
        'backend',
        'backend.main',
        'backend.config',
        'backend.services',
        'backend.scanner',
        'backend.launcher',
        'backend.port_check',
        'backend.process_info',
        'backend.process_runner',
        'backend.icon_fetcher',
        'backend.folder_picker',
        'backend.app_icon',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
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
    exclude_binaries=False,
    name='本地服务管理',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(APP_ICON),
)

