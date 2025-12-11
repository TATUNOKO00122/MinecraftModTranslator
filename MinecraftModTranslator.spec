# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [('ui/styles.qss', 'ui')]
binaries = []
hiddenimports = ['requests']

# Collect ftb_snbt_lib and its internal ply package
hiddenimports += collect_submodules('ftb_snbt_lib')
hiddenimports += ['ftb_snbt_lib.ply', 'ftb_snbt_lib.ply.lex', 'ftb_snbt_lib.ply.yacc']
hiddenimports += ['ply', 'ply.lex', 'ply.yacc']

# Collect all ftb_snbt_lib data files (parser tables etc.)
tmp_ftb = collect_all('ftb_snbt_lib')
datas += tmp_ftb[0]; binaries += tmp_ftb[1]; hiddenimports += tmp_ftb[2]

# Collect PySide6
tmp_ret = collect_all('PySide6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
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
    name='MinecraftModTranslator',
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
