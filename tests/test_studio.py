"""Studio smoke tests (offscreen Qt). Skipped when PySide6 is not installed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from genibo.project import create_project
from genibo.studio.app import create_app
from genibo.studio.main_window import MainWindow

pytestmark = pytest.mark.studio


@pytest.fixture
def window(tmp_path: Path):  # type: ignore[no-untyped-def]
    create_app()
    proj = create_project(tmp_path / "demo", "demo")
    w = MainWindow()
    w.open_project(proj.root)
    yield w
    for ed in w.editors.values():
        ed.document().setModified(False)
    if w.robot_panel.robot is not None:
        w.robot_panel.robot.close()
    w.close()
    QApplication.processEvents()


def test_build_fills_plan_panel(window: MainWindow) -> None:
    plan = window.build()
    assert plan is not None
    assert window.plan_panel.table.rowCount() == len(plan.frames)
    assert "verified" in window.plan_panel.header.text()
    assert window.project is not None and window.project.plan_path.exists()


def test_live_validation_marks_errors(window: MainWindow) -> None:
    ed = window.entry_editor()
    assert ed is not None
    ed.setPlainText(ed.toPlainText().replace("g.torque(True)", "g.torque(True)\n    g.wiggle()"))
    result = window.validate()
    assert result is not None and not result.ok
    assert window.problems.table.rowCount() >= 1
    item = window.problems.table.item(0, 1)
    assert item is not None and "G109" in item.text()


def test_simulate_and_connect_simulator(window: MainWindow) -> None:
    window.simulate()
    traj = window.sim_panel.traj
    assert traj is not None and traj.moving_servos()
    window.robot_panel.toggle_connection()
    robot = window.robot_panel.robot
    assert robot is not None and not robot.is_hardware
    assert "SIMULATOR" in window.link_lbl.text()
    assert robot.battery().robot_percent > 0


def test_packaged_self_test_routine(tmp_path: Path) -> None:
    from genibo.studio.selftest import run_self_test

    log = tmp_path / "selftest.log"
    shot = tmp_path / "shot.png"
    assert run_self_test(str(shot), str(log)) == 0
    assert "SELF-TEST OK" in log.read_text(encoding="utf-8")
    assert shot.stat().st_size > 10_000
