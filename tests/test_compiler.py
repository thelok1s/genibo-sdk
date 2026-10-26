"""Compiler: validation diagnostics, lowering, plan determinism and integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genibo.clock import VirtualClock
from genibo.joints import load_profile
from genibo.link import Link
from genibo.program import CommandPlan, compile_source, play_plan
from genibo.protocol import commands as cmd
from genibo.protocol.constants import Torque
from genibo.protocol.convert import ms_to_ticks
from genibo.sim import SimTransport, VirtualGenibo

JOINTS = load_profile("legacy-cgenibo")


def build(src: str, tmp_path: Path, **kw: object):  # type: ignore[no-untyped-def]
    path = tmp_path / "prog.py"
    path.write_text(src)
    return compile_source(src, str(path), joints=kw.pop("joints", JOINTS), robot_id=0x20, **kw)  # type: ignore[arg-type]


def codes(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.code for d in result.diagnostics]


GOOD = """
from genibo.program import Program, program

def swing(g, n):
    for i in range(n):
        g.pose(tail=20 if i % 2 == 0 else -20, ms=280)

@program
def main(g: Program) -> None:
    g.torque(True)
    g.pose({"head_pan": 10}, ms=500, head_tilt=-5)
    swing(g, 4)
    g.wait(100)
    g.play_motion("7.act", wait_ms=2000)
    g.head_led_buzz(2, 3)
"""


def test_good_program_lowers_to_expected_frames(tmp_path: Path) -> None:
    r = build(GOOD, tmp_path)
    assert r.ok, [str(d) for d in r.diagnostics]
    plan = r.plan
    assert plan is not None
    frames = [f.data for f in plan.frames]
    assert frames[0] == cmd.tunnel(0x20, cmd.torque(0xFE, Torque.ON)).encode()
    pan, tilt = JOINTS["head_pan"], JOINTS["head_tilt"]
    expected_pose = cmd.tunnel(
        0x20,
        cmd.s_jog(
            ms_to_ticks(500), [cmd.JogEntry(pan.to_raw(10), 0, 15), cmd.JogEntry(tilt.to_raw(-5), 0, 16)]
        ),
    ).encode()
    assert frames[1] == expected_pose
    assert frames[-2] == cmd.play_motion(0x20, 0, "7.act").encode()
    assert len(frames) == 1 + 1 + 4 + 1 + 1
    # timeline: pose 500 ms is 45 ticks = 504.0 ms, then four 25-tick swings (280 ms each)
    assert [f.t_ms for f in plan.frames[:3]] == [0.0, 0.0, 504.0]
    assert plan.frames[-2].t_ms == pytest.approx(504.0 + 4 * 280.0 + 100.0)
    assert plan.duration_ms == pytest.approx(504.0 + 4 * 280.0 + 100.0 + 2000.0)
    # every frame points back at the source line that produced it
    src_lines = GOOD.splitlines()
    line = plan.frames[2].line
    assert line is not None and "g.pose(tail=" in src_lines[line - 1]
    assert "G301" in codes(r)  # unverified joints warned, not hidden


def test_build_is_deterministic(tmp_path: Path) -> None:
    a = build(GOOD, tmp_path).plan
    b = build(GOOD, tmp_path).plan
    assert a is not None and b is not None
    assert a.to_json() == b.to_json()


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("import time\n", "G101"),
        ("from os import path\n", "G101"),
        ("x = 0\nwhile x < 3:\n    x += 1\n", "G102"),
        ("open('f')\n", "G107"),
        ("g.pos(tail=1)\n", "G109"),
        ("g.pose(tail=5, ms=3000)\n", "G140"),
        ("g.pose(tail=5, ms=280)\ng.pose(nose=5, ms=280)\n", "G140"),
        ("g.pose_raw(tail=5000, ms=280)\n", "G140"),
        ("g.play_motion('" + "x" * 60 + ".act')\n", "G140"),
        ("g.pose(ms=200)\n", "G131"),
        ("raise RuntimeError('boom')\n", "G121"),
        ("for i in range(100000):\n    g.wait(1)\n", "G122"),
    ],
)
def test_invalid_programs_report_the_line(body: str, code: str, tmp_path: Path) -> None:
    indented = "\n".join("    " + ln if ln else ln for ln in body.splitlines())
    src = f"from genibo.program import program\n\n@program\ndef main(g):\n{indented}\n"
    if body.startswith(("import", "from")):
        src = body + src
    r = build(src, tmp_path)
    assert not r.ok
    assert code in codes(r), [str(d) for d in r.diagnostics]
    d = next(d for d in r.diagnostics if d.code == code)
    assert d.location is not None and d.location.line >= 1


def test_missing_program_and_ambiguous_entry(tmp_path: Path) -> None:
    assert "G110" in codes(build("x = 1\n", tmp_path))
    two = "from genibo.program import program\n@program\ndef a(g):\n    pass\n@program\ndef b(g):\n    pass\n"
    assert "G112" in codes(build(two, tmp_path))
    assert build(two, tmp_path, entry="b").ok


def test_overlapping_poses_warn(tmp_path: Path) -> None:
    src = (
        "from genibo.program import program\n@program\ndef m(g):\n"
        "    g.pose(tail=10, ms=1000, wait=False)\n    g.pose(tail=-10, ms=300)\n"
    )
    r = build(src, tmp_path)
    assert r.ok and "G302" in codes(r)


def test_plan_tampering_is_detected(tmp_path: Path) -> None:
    plan = build(GOOD, tmp_path).plan
    assert plan is not None
    d = json.loads(plan.to_json())
    d["frames"][1]["hex"] = d["frames"][1]["hex"][:-2] + "00"
    with pytest.raises(ValueError, match="modified"):
        CommandPlan.from_dict(d)
    d["frames_sha256"] = None
    tampered = CommandPlan.from_dict(d)
    assert any("checksum" in p for p in tampered.verify())


def test_plan_roundtrip_and_simulation(tmp_path: Path) -> None:
    plan = build(GOOD, tmp_path).plan
    assert plan is not None
    path = tmp_path / "p.json"
    plan.save(path)
    loaded = CommandPlan.load(path)
    assert loaded.frames == plan.frames and loaded.verify() == []
    clock = VirtualClock()
    dev = VirtualGenibo(robot_id=0x20, clock=clock)
    dev.add_file(0, "7.act", b"x")
    link = Link(SimTransport(dev), robot_id=0x20).open()
    report = play_plan(loaded, link, clock=clock)
    link.close()
    assert report.sent == len(plan.frames) and not report.cancelled
    assert clock.now() == pytest.approx(plan.duration_ms / 1000.0)
    final = dev.snapshot()
    assert final[17] == JOINTS["tail"].to_raw(-20)
    assert any("playing" in e.text for e in dev.events)
    assert dev.head_led == 2 and dev.buzz == 3


def test_player_refuses_wrong_robot(tmp_path: Path) -> None:
    from genibo.program.player import PlanMismatchError

    plan = build(GOOD, tmp_path).plan
    assert plan is not None
    link = Link(SimTransport(VirtualGenibo(robot_id=0x21, clock=VirtualClock())), robot_id=0x21).open()
    with pytest.raises(PlanMismatchError):
        play_plan(plan, link, clock=VirtualClock(), robot_id=0x21)
    link.close()


def test_player_cancel_can_free_torque(tmp_path: Path) -> None:
    import threading

    plan = build(GOOD, tmp_path).plan
    assert plan is not None
    clock = VirtualClock()
    dev = VirtualGenibo(robot_id=0x20, clock=clock)
    link = Link(SimTransport(dev), robot_id=0x20).open()
    cancel = threading.Event()

    def on_frame(i: int, _f: object) -> None:
        if i == 2:
            cancel.set()

    report = play_plan(plan, link, clock=clock, cancel=cancel, on_frame=on_frame, on_cancel="torque_free")
    link.close()
    assert report.cancelled and report.sent == 3
    assert all(s.torque == Torque.FREE for s in dev.servos.values())


def test_runaway_loop_without_steps_is_stopped(tmp_path: Path) -> None:
    src = (
        "from genibo.program import program\n@program\ndef m(g):\n    for i in range(10**12):\n        pass\n"
    )
    path = tmp_path / "loop.py"
    path.write_text(src)
    import time

    t0 = time.monotonic()
    r = compile_source(src, str(path), joints=JOINTS, robot_id=0x20, time_limit_s=0.5)
    assert time.monotonic() - t0 < 5
    assert "G122" in codes(r)
    d = next(d for d in r.diagnostics if d.code == "G122")
    assert d.location is not None and d.location.line in (4, 5)


def test_runaway_module_level_loop_is_stopped(tmp_path: Path) -> None:
    src = "x = 0\nfor i in range(10**12):\n    x += 1\nfrom genibo.program import program\n@program\ndef m(g):\n    pass\n"
    path = tmp_path / "loop2.py"
    path.write_text(src)
    r = compile_source(src, str(path), joints=JOINTS, robot_id=0x20, time_limit_s=0.5)
    assert "G122" in codes(r)
