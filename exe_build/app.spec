# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec файл для сборки EXE приложения.
Конфигурирует сборку Flask приложения в однофайловый исполняемый файл.
"""

import sys
from PyInstaller.utils.hooks import get_module_collection_mode

block_cipher = None

# Путь до корневой папки проекта (родитель для exe_build)
import os
project_root = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

a = Analysis(
    [os.path.join(project_root, 'app.py')],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'templates'), 'templates'),
        (os.path.join(project_root, 'static'), 'static'),
    ],
    hiddenimports=[
        'flask',
        'requests',
        'werkzeug',
        'jinja2',
        'click',
        'itsdangerous',
        'colorama',
        'grid',
        'mexc',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=['matplotlib', 'scipy', 'numpy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    collect_submodules=['flask', 'werkzeug'],
    collect_all=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='КалькуляторСетки',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Оставляем консоль для отладки
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

