# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gui\\launchpad_mapper.py'],
    pathex=[],
    binaries=[('C:\\Users\\carlj\\Desktop\\Launchpadkod\\.venv\\Lib\\site-packages\\rtmidi\\_rtmidi.cp311-win_amd64.pyd', 'rtmidi')],
    datas=[('fonts', 'fonts'), ('presets', 'presets'), ('icons', 'icons'), ('config.yaml', '.')],
    hiddenimports=['rtmidi', 'rtmidi._rtmidi', 'mido.backends.rtmidi', 'importlib_metadata'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests', '__pycache__'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LaunchpadMapper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='C:\\Users\\carlj\\Desktop\\Launchpadkod\\file_version_info.txt',
    icon=['C:\\Users\\carlj\\Desktop\\Launchpadkod\\icons\\app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LaunchpadMapper',
)
