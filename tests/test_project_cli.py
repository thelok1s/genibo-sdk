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


@pytest.mark.parametrize(
    ("section", "line"),
    [
        ("[build]", 'out_dir = "../outside"'),
        ("[build]", 'out_dir = "/tmp/genibo-out"'),
        ("[project]", 'entry = "../../etc/passwd"'),
    ],
)
def test_manifest_paths_must_stay_inside_the_project(tmp_path: Path, section: str, line: str) -> None:
    p = create_project(tmp_path / "bot", "bot")
    text = (p.root / "genibo.toml").read_text()
    key = line.split("=")[0].strip()
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith(f"{key} ="))
    text = text.replace(section, f"{section}\n{line}", 1)
    (p.root / "genibo.toml").write_text(text)
    with pytest.raises(ProjectError, match="inside the project"):
        Project.load(p.root)


def test_manifest_project_name_cannot_be_a_path(tmp_path: Path) -> None:
    p = create_project(tmp_path / "bot", "bot")
    m = p.root / "genibo.toml"
    m.write_text(m.read_text().replace('name = "bot"', 'name = "../evil"', 1))
    with pytest.raises(ProjectError, match="alphanumeric"):
        Project.load(p.root)


def _plan_file(tmp_path: Path) -> Path:
    root = tmp_path / "bot"
    main(["new", str(root)])
    main(["build", str(root)])
    return root / "build" / "bot.plan.json"


def _rewrite(path: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    d = json.loads(path.read_text())
    mutate(d)
    d.pop("frames_sha256", None)  # a hand-edited plan can always recompute its digest
    out = path.with_name("edited.plan.json")
    CommandPlan.from_dict(d).save(out)
    return out


def test_run_gate_uses_frames_not_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = _plan_file(tmp_path)

    def hide_motion(d: dict) -> None:
        d["steps"] = []  # metadata says nothing moves...
        for j in d["joints"]["table"].values():
            j["verified"] = False

    edited = _rewrite(plan, hide_motion)
    code = main(["run", str(edited), "--port", "/dev/null-genibo", "--yes"])
    assert code == 2
    err = capsys.readouterr().err
    assert "frames move servos" in err and "tail" in err  # ...but the frames still move the tail


def test_run_gate_refuses_hazardous_frames(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from genibo.protocol import commands as c

    plan = _plan_file(tmp_path)
    rom_write = c.tunnel(0x20, c.mpsu_write(0x40, 7, b"\x41", rom=True)).encode().hex()

    def add_rom_write(d: dict) -> None:
        for j in d["joints"]["table"].values():
            j["verified"] = True
        d["frames"].append(
            {"t_ms": d["frames"][-1]["t_ms"], "hex": rom_write, "step": 0, "line": 1, "summary": "x"}
        )

    edited = _rewrite(plan, add_rom_write)
    code = main(["run", str(edited), "--port", "/dev/null-genibo", "--yes"])
    assert code == 2
    assert "MPSU ROM" in capsys.readouterr().err


def test_frame_analysis() -> None:
    from genibo.program.safety import analyze_frames
    from genibo.protocol import commands as c
    from genibo.protocol.constants import JogFlag, Torque

    frames = [
        c.tunnel(0x20, c.torque(0xFE, Torque.ON)).encode(),
        c.tunnel(0x20, c.s_jog(10, [c.JogEntry(500, 0, 15), c.JogEntry(0, JogFlag.NO_ACTION, 16)])).encode(),
        c.play_motion(0x20, 0, "7.act").encode(),
        c.file_delete_all(0x20, 1).encode(),
        c.raw(0x20, 0x99).encode(),
    ]
    a = analyze_frames(frames)
    assert a.moved_servos == {15} and a.torque_targets == {0xFE}
    assert len(a.hazards) == 2 and "ALL files" in a.hazards[0] and "0x99" in a.hazards[1]
    table = {"head_pan": {"servo_id": 15, "verified": True}, "head_tilt": {"servo_id": 16, "verified": False}}
    assert a.unverified_servos(table) == []
    assert analyze_frames(frames[1:2]).unverified_servos({}) == ["servo 15"]
