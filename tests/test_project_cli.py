"""Project manifest, scaffolding, trajectory sampling and CLI entry points."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genibo.cli import main
from genibo.errors import JointError
from genibo.joints import load_profile
from genibo.program.plan import CommandPlan
from genibo.project import Project, ProjectError, create_project
from genibo.sim.trajectory import simulate_plan


def test_create_and_load_project(tmp_path: Path) -> None:
    p = create_project(tmp_path / "bot", "bot")
    assert p.robot_id == 0x20 and p.kind == "program" and p.entry_path.exists()
    assert Project.find(p.entry_path).root == p.root
    with pytest.raises(ProjectError):
        create_project(tmp_path / "bot", "bot")


def test_joint_overrides_from_manifest(tmp_path: Path) -> None:
    p = create_project(tmp_path / "bot", "bot")
    text = (p.root / "genibo.toml").read_text()
    text += "\n[joints.head_pan]\ncenter = 500\nlimit_up = 45.0\nlimit_dn = -45.0\nverified = true\n"
    (p.root / "genibo.toml").write_text(text)
    joints = Project.load(p.root).joint_map()
    pan = joints["head_pan"]
    assert (
        pan.verified and pan.servo_id == 15 and pan.calibration is not None and pan.calibration.center == 500
    )
    assert not joints["tail"].verified
    with pytest.raises(JointError):
        pan.to_raw(50.0)


def test_bad_manifest_keys_are_reported(tmp_path: Path) -> None:
    p = create_project(tmp_path / "bot", "bot")
    (p.root / "genibo.toml").write_text(
        (p.root / "genibo.toml").read_text() + "\n[joints.tail]\ncentre = 3\n"
    )
    with pytest.raises(JointError, match="unknown keys"):
        Project.load(p.root).joint_map()


def test_unknown_profile() -> None:
    with pytest.raises(JointError):
        load_profile("nope")


def test_trajectory_sampling(tmp_path: Path) -> None:
    p = create_project(tmp_path / "bot", "bot")
    plan = p.build().plan
    assert plan is not None
    traj = simulate_plan(plan, sample_ms=10)
    assert traj.times_ms[0] == 0 and traj.times_ms[-1] == pytest.approx(plan.duration_ms)
    assert set(traj.moving_servos()) == {17, 15}  # tail and head_pan move; head_tilt stays at 0 deg
    end = traj.at(plan.duration_ms)
    assert end[17] == p.joint_map()["tail"].to_raw(-25)


def test_cli_build_inspect_sim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "bot"
    assert main(["new", str(root)]) == 0
    assert main(["build", str(root)]) == 0
    plan_path = root / "build" / "bot.plan.json"
    plan = CommandPlan.load(plan_path)
    assert main(["inspect", str(plan_path)]) == 0
    assert main(["sim", str(plan_path)]) == 0
    out = capsys.readouterr().out
    assert "SIMULATOR" in out and f"sent {len(plan.frames)}/{len(plan.frames)}" in out
    assert main(["inspect", "--hex", "ff20630000000043"]) == 0
    assert "BATTERY" in capsys.readouterr().out


def test_cli_validate_reports_errors(tmp_path: Path) -> None:
    root = tmp_path / "bot"
    main(["new", str(root)])
    src = root / "src" / "main.py"
    src.write_text(src.read_text().replace("ms=600", "ms=6000"))
    assert main(["validate", str(root)]) == 1


def test_cli_run_refuses_unverified_joints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "bot"
    main(["new", str(root)])
    main(["build", str(root)])
    code = main(["run", str(root / "build" / "bot.plan.json"), "--port", "/dev/null-genibo", "--yes"])
    assert code == 2
    assert "not verified" in capsys.readouterr().err


def test_cli_robot_commands_against_simulator(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["robot", "status", "--sim"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["battery_robot_percent"] == 87
    assert main(["robot", "files", "--sim"]) == 0
    assert "7.act" in capsys.readouterr().out


def test_cli_script_runs_host_script(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "s"
    main(["new", str(root), "--kind", "script"])
    assert main(["script", str(root / "src" / "main.py"), "--sim"]) == 0
    out = capsys.readouterr().out
    assert "battery: robot 87%" in out and "7.act" in out
