# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    Gui - Runtime                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Prepare Qt's local plugins when macOS marks installed libraries as hidden.

Qt skips hidden plugin files during discovery. If this occurs, Aurum makes a temporary visible copy of the
bundled plugins for the running application, leaving the virtual environment and workspace unchanged.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from pathlib import Path
import shutil
import stat
import sys
from tempfile import TemporaryDirectory

import PySide6
from PySide6.QtCore import QCoreApplication


# %% 1. Qt Startup -------------------------------------------------------------------------------------------
def prepare_qt_plugins() -> TemporaryDirectory | None:
    """
    Supply a temporary plugin directory only when macOS hidden flags prevent normal Qt discovery.

    Returns:
        A directory holder that the caller must retain until the application exits, or None when the
        installed plugins are already visible. Copies contain only the installed PySide6 plugin files.
    """
    if sys.platform != "darwin":
        return None
    source = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    files = [path for path in source.rglob("*") if path.is_file()]
    if not any(path.stat().st_flags & stat.UF_HIDDEN for path in files):
        return None
    temporary = TemporaryDirectory(prefix="aurum-qt-plugins-")
    destination = Path(temporary.name)
    try:
        for path in files:
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        QCoreApplication.addLibraryPath(str(destination))
    except Exception:
        temporary.cleanup()
        raise
    return temporary
