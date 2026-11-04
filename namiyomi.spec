# Build on Apple Silicon with: python -m PyInstaller namiyomi.spec
from pathlib import Path
import runpy
import os

root = Path(SPECPATH)
app_info = runpy.run_path(str(root / 'app_info.py'))
analysis = Analysis(
    [str(root / 'app.py')],
    pathex=[str(root)],
    binaries=[(str(root / 'assets' / 'libnamiyomi-menus.dylib'),'assets')],
    # tkinterdnd2's installed hook selects the macOS/Tcl binaries automatically.
    datas=[(str(root/name),'.') for name in ('LICENSE.txt','CITATION.cff','CITATION.txt')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'scipy', 'IPython', 'pytest', 'readline'],
    noarchive=False,
)
# NumPy's Conda hook follows package metadata and may copy Python's unrelated
# readline dependency as data, even when the Python module is excluded. No app
# Mach-O links against it; do not distribute this unused optional library.
def needed(entry):
    return not any(Path(str(part)).name.startswith(('libreadline','libhistory')) for part in entry[:2])
analysis.binaries=[entry for entry in analysis.binaries if needed(entry)]
analysis.datas=[entry for entry in analysis.datas if needed(entry)]
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [],
    exclude_binaries=True,
    name=app_info['APP_NAME'],
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=os.environ.get('NAMIYOMI_CODESIGN_IDENTITY') or None,
    entitlements_file=os.environ.get('NAMIYOMI_ENTITLEMENTS') or None,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name=app_info['APP_NAME'],
)
application = BUNDLE(
    collection,
    name=f"{app_info['APP_NAME']}.app",
    bundle_identifier=app_info['APP_BUNDLE_ID'],
    version=app_info['APP_BUILD'],
    icon=str(root / 'assets' / 'namiyomi.icns'),
    info_plist={
        'CFBundleDisplayName': app_info['APP_NAME'],
        'CFBundleVersion': app_info['APP_BUILD'],
        'CFBundleShortVersionString': app_info['APP_VERSION'],
        'CFBundleDevelopmentRegion': 'en',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '15.0',
        'CFBundleDocumentTypes': [
            {'CFBundleTypeName': 'namiyomi Project', 'CFBundleTypeRole': 'Editor',
             'CFBundleTypeExtensions': ['json'], 'LSHandlerRank': 'Alternate'},
            {'CFBundleTypeName': 'ABI Chromatogram', 'CFBundleTypeRole': 'Viewer',
             'CFBundleTypeExtensions': ['ab1'], 'LSHandlerRank': 'Alternate'},
        ],
    },
)
