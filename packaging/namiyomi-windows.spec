# Build natively on 64-bit Windows with Python 3.12; not a cross-compiled binary.
from pathlib import Path
import runpy
import sys

if sys.platform != 'win32':
    raise RuntimeError('Build this spec with 64-bit Python on Windows.')
root = Path(SPECPATH).parent
source = root/'src'
info = runpy.run_path(str(source / 'app_info.py'))

analysis = Analysis(
    [str(source / 'app.py')], pathex=[str(source)], binaries=[],
    # tkinterdnd2's PyInstaller hook chooses the Windows x64 Tcl/DND libraries.
    datas=[(str(root/name), '.') for name in ('LICENSE.txt', 'CITATION.cff', 'CITATION.txt')]
          + [(str(root/'assets'/'namiyomi.ico'), 'assets')],
    hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'scipy', 'IPython', 'pytest', 'readline'], noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name=info['APP_NAME'], debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    icon=str(root/'assets'/'namiyomi.ico'),
)
collection = COLLECT(executable, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name=info['APP_NAME'])
