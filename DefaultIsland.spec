# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = [
    'win32com.client',
    # Optional modules are imported behind guards; list them so they are always bundled.
    'island_core', 'island_i18n', 'island_settings', 'island_extras', 'island_inputs',
    'island_audio', 'island_devices', 'island_lyrics', 'island_notifications',
    'island_calls', 'island_weather',
    'psutil', 'comtypes', 'comtypes.stream', 'comtypes.client',
    # UI Automation wrappers (answering calls), generated once by
    # comtypes.client.GetModule("UIAutomationCore.dll") before the build.
    'comtypes.gen', 'comtypes.gen.UIAutomationClient', 'comtypes.gen.stdole',
    'comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0',
    'comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0',
]
for package in ('winrt', 'pycaw'):
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['island.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DefaultIsland',
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
    icon=['Dynamicisland.ico'],
)
