"""Entry point for ``genibo studio``."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyleFactory

from genibo.errors import GeniboError
from genibo.studio.main_window import MainWindow
from genibo.studio.theme import STYLESHEET

ICON_FILE = Path(__file__).parent / "resources" / "genibo.png"


def app_icon() -> QIcon:
    return QIcon(str(ICON_FILE))


def create_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(app, QApplication)
    app.setApplicationName("Genibo Studio")
    # Wayland/X11 match windows to genibo-studio.desktop (and its hicolor icon) by this name.
    app.setDesktopFileName("genibo-studio")
    # A packaged macOS app already has its Dock icon (Assets.car / .icns); setting a window
    # icon there would replace the Liquid Glass icon with the flat PNG.
    if not (sys.platform == "darwin" and getattr(sys, "frozen", False)):
        app.setWindowIcon(app_icon())
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setStyleSheet(STYLESHEET)
    return app


def run(target: str | None = None) -> int:
    app = create_app()
    win = MainWindow()
    if target:
        win.open_project(Path(target))
    else:
        from genibo.project import Project

        with contextlib.suppress(GeniboError):
            win.open_project(Project.find(Path.cwd()).root)
    win.show()
    return app.exec()
