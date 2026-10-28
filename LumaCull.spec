# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 光筛 LumaCull (Windows, onedir)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

project = Path(SPECPATH)
datas = [
    (str(project / "web"), "web"),
    (str(project / "assets"), "assets"),
]
binaries = []
hiddenimports = [
    "PIL._imaging",
    "PIL._webp",
]


for package in ("PIL", "pillow_heif"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception as exc:  # pragma: no cover - build-time diagnostics
        print(f"warning: could not collect {package}: {exc}")

for distribution in ("pillow-heif", "Pillow"):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

a = Analysis(
    ["app.py"],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "tkinter.test", "test", "unittest", "numpy", "scipy", "pandas", "matplotlib"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LumaCull",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project / "assets" / "icon.ico"),
    version=str(project / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LumaCull",
)
