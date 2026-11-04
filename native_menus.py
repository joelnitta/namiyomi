"""Load the app's own AppKit menu customization, if present."""
import ctypes
import sys
from pathlib import Path

_library=None


def configure():
    global _library
    if sys.platform!='darwin':return
    path=Path(__file__).parent/'assets'/'libnamiyomi-menus.dylib'
    if not path.exists():return
    _library=ctypes.CDLL(str(path))
    _library.namiyomi_configure_menus.argtypes=[]
    _library.namiyomi_configure_menus.restype=None
    _library.namiyomi_configure_menus()


def prune():
    if _library is not None:_library.namiyomi_prune_menus()
