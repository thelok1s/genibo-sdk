"""``genibo-studio --self-test``: prove a packaged build can start and do real work.

Used by CI on every produced artifact (run with ``QT_QPA_PLATFORM=offscreen``):
creates a project, builds and simulates it in the studio window, connects the
simulator, and optionally saves a screenshot. Exit code 0 means every step
passed; any failure prints the step and returns 1.
"""

from __future__ import annotations

import platform
import sys
import tempfile
import traceback
from pathlib import Path


class _Out:
    """Print to stdout when there is one (windowed Windows builds have none) and to an optional log file."""

    def __init__(self, log_path: str | None) -> None:
        self.fh = open(log_path, "w", encoding="utf-8") if log_path else None  # noqa: SIM115

    def __call__(self, msg: str, *, err: bool = False) -> None:
        stream = sys.stderr if err else sys.stdout
        if stream is not None:
            print(msg, file=stream, flush=True)
        if self.fh is not None:
            self.fh.write(msg + "\n")
            self.fh.flush()

    def close(self) -> None:
        if self.fh is not None:
            self.fh.close()


def run_self_test(screenshot: str | None = None, log_path: str | None = None) -> int:
    out = _Out(log_path)
    step = "import"
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion
        from PySide6.QtWidgets import QApplication

        from genibo import __version__
        from genibo.project import create_project
        from genibo.studio.app import ICON_FILE, app_icon, create_app
        from genibo.studio.main_window import MainWindow
        from genibo.transport.serial import list_ports

        out(
            f"genibo {__version__} · PySide6 {pyside_version} · Qt {qVersion()} · Python {sys.version.split()[0]} · "
            f"{platform.system()} {platform.machine()} · frozen={getattr(sys, 'frozen', False)}"
        )
        step = "serial port enumeration"
        out(f"  serial ports: {len(list_ports())}")
        step = "qt application"
        app = create_app()
        step = "application icon"
        icon = app_icon()
        assert not icon.isNull() and not icon.pixmap(64, 64).isNull(), (
            f"icon resource not loadable: {ICON_FILE}"
        )
        out(f"  app icon: {ICON_FILE.name} ok")
        with tempfile.TemporaryDirectory() as tmp:
            step = "project scaffolding"
            proj = create_project(Path(tmp) / "selftest", "selftest")
            step = "main window"
            win = MainWindow()
            win.resize(1400, 900)
            win.open_project(proj.root, trusted=True)  # created by this self-test
            win.show()
            QApplication.processEvents()
            step = "build"
            plan = win.build()
            assert plan is not None and plan.frames and not plan.verify(), "build produced no verified plan"
            out(
                f"  build: {len(plan.frames)} frames, {plan.duration_ms:.1f} ms, sha256 {plan.frames_digest()[:16]}"
            )
            step = "simulate"
            win.simulate()
            traj = win.sim_panel.traj
            assert traj is not None and traj.moving_servos(), "simulation moved no servo"
            out(f"  simulate: servos moving {traj.moving_servos()}")
            step = "simulator link"
            win.robot_panel.toggle_connection()
            robot = win.robot_panel.robot
            assert robot is not None and not robot.is_hardware
            out(f"  simulator link: battery {robot.battery().robot_percent}%")
            QApplication.processEvents()
            if screenshot:
                step = "screenshot"
                win.grab().save(screenshot)
                out(f"  screenshot: {screenshot}")
            step = "shutdown"
            for ed in win.editors.values():
                ed.document().setModified(False)
            win.close()
            app.processEvents()
        out("SELF-TEST OK")
        return 0
    except Exception:
        out(f"SELF-TEST FAILED at step: {step}", err=True)
        out(traceback.format_exc(), err=True)
        return 1
    finally:
        out.close()
