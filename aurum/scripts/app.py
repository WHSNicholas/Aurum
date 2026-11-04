# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    Scripts - App                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Open Aurum's desktop workspace for setup, statements, review, accounting and PDF reports.

Run this file in PyCharm, use python aurum/scripts/app.py, or open the installed macOS Aurum application.
The window uses the project's private config and data folders; choose another workspace inside the app.
Opening the app only reads saved state; imports, AI processing and configuration saves require user actions.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))


# %% 1. Desktop Launch ---------------------------------------------------------------------------------------
def main(workspace: Path | None = None) -> int:
    """
    Start the desktop, with an optional programmatic workspace for isolated development checks.
    """
    try:
        from PySide6.QtCore import QDir
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Install Aurum's dependencies: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    # Aurum imports
    from aurum.gui.controls import install_controls
    from aurum.gui.runtime import prepare_qt_plugins
    from aurum.gui.window import MainWindow

    plugin_directory = prepare_qt_plugins()
    app = QApplication(sys.argv[:1])
    install_controls(app)
    app.setApplicationName("Aurum")
    app.setApplicationDisplayName("Aurum")
    app.setOrganizationName("Aurum")
    for filename in ("Geist.ttf", "GeistMono.ttf", "Cormorant.ttf"):
        QFontDatabase.addApplicationFont(str(PROJECT_ROOT / "aurum" / "reporting" / "assets" / filename))
    app.setFont(QFont("Geist", 12))
    QDir.addSearchPath("aurumui", str(PROJECT_ROOT / "aurum" / "gui" / "assets"))
    app.setStyleSheet((PROJECT_ROOT / "aurum" / "gui" / "style.qss").read_text(encoding="utf-8"))
    window = MainWindow((workspace or PROJECT_ROOT).resolve())
    window.show()
    try:
        return app.exec()
    finally:
        if plugin_directory is not None:
            plugin_directory.cleanup()


# %% 2. Execution --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())
