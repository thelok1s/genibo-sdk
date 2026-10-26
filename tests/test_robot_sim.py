"""Client <-> simulator integration and client-side safety behaviour.

These tests exercise the full host stack (encoders, link, decoder, reply
matching) against the simulator. They say nothing about the physical robot.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from genibo import Genibo, Led, Storage, Torque
from genibo.clock import VirtualClock
from genibo.errors import JointError, ResponseTimeout, RobotStatusError, SafetyLatchError
from genibo.joints import load_profile
from genibo.link import Link
from genibo.protocol import commands as cmd
from genibo.protocol.frames import LinkFrame
from genibo.sim import SimTransport, VirtualGenibo
from genibo.transport.base import MemoryTransport


@pytest.fixture
def sim() -> Iterator[tuple[Genibo, VirtualGenibo, VirtualClock]]:
    clock = VirtualClock()
    dev = VirtualGenibo(robot_id=0x20, clock=clock)
    robot = Genibo(Link(SimTransport(dev)), robot_id=0x20, clock=clock, reply_timeout=2.0).open()
    yield robot, dev, clock
    robot.close()


def test_status_queries(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, _ = sim
    b = robot.battery()
    assert (b.robot_percent, b.mid_percent) == dev.battery_percent
    assert robot.heartbeat() >= 0
    assert robot.is_playing() is False
    robot.set_time(datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert robot.get_time().replace(microsecond=0) == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_file_roundtrip_and_playback(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, clock = sim
    robot.upload_file("dance.act", b"\x00\x01opaque", Storage.USER, ack_timeout=1.0)
    assert robot.list_files(Storage.USER) == ["dance.act"]
    assert robot.download_file("dance.act", Storage.USER) == b"\x00\x01opaque"
    robot.play_motion("dance.act", Storage.USER, ack_timeout=1.0)
    assert robot.is_playing()
    clock.advance(dev.motion_duration_s + 0.1)
    assert not robot.is_playing()
    robot.delete_file("dance.act", Storage.USER, ack_timeout=1.0)
    assert robot.list_files(Storage.USER) == []


def test_missing_file_reports_status_error(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, _, _ = sim
    with pytest.raises(RobotStatusError) as exc:
        robot.play_motion("nope.act", ack_timeout=1.0)
    assert exc.value.code == 2  # PROCESS_FAILED
    with pytest.raises(RobotStatusError):
        robot.download_file("nope.act", Storage.MASTER, timeout=1.0)


def test_move_reaches_targets(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, clock = sim
    robot.torque(True)
    robot.move({"head_pan": 30.0, "tail": -10.0}, 560, led=Led.GREEN)
    clock.advance(0.28)
    mid = dev.snapshot()[15]
    clock.advance(1.0)
    end = dev.snapshot()
    pan = robot.joints["head_pan"]
    assert end[15] == pan.to_raw(30.0) and end[17] == robot.joints["tail"].to_raw(-10.0)
    assert 512 < mid < end[15]  # interpolating
    assert dev.servos[15].led == "green"


def test_torque_free_servos_do_not_move(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, clock = sim
    robot.torque(False)
    robot.move({"head_pan": 30.0}, 300)
    clock.advance(1.0)
    assert dev.snapshot()[15] == 512.0


def test_emergency_stop_latches(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, _ = sim
    sent = robot.emergency_stop()
    assert sent == cmd.tunnel(0x20, cmd.torque(0xFE, Torque.FREE)).encode()
    with pytest.raises(SafetyLatchError):
        robot.move({"head_pan": 10.0}, 300)
    with pytest.raises(SafetyLatchError):
        robot.play_motion("7.act")
    robot.stop_playback()  # stopping is always allowed
    robot.clear_emergency()
    robot.torque(True)
    robot.move({"head_pan": 10.0}, 300)
    assert dev.servos[15].torque == Torque.ON


def test_joint_validation() -> None:
    joints = load_profile("legacy-cgenibo").with_overrides(
        {"head_pan": {"limit_up": 40.0, "limit_dn": -40.0}}
    )
    robot = Genibo(Link(MemoryTransport()), joints=joints).open()
    with pytest.raises(JointError, match="outside the limits"):
        robot.move({"head_pan": 50.0}, 300)
    with pytest.raises(JointError, match="unknown joint"):
        robot.move({"head_yaw": 5.0}, 300)
    with pytest.raises(JointError, match="raw position"):
        robot.move({"tail": 4000}, 300, raw=True)
    data = robot.move({"head_pan": 50.0}, 300, clip=True)  # legacy-style clipping on request
    assert LinkFrame.decode(data).payload[8:10] == joints["head_pan"].to_raw(40.0).to_bytes(2, "little")
    robot.close()


def test_play_time_limit() -> None:
    robot = Genibo(Link(MemoryTransport())).open()
    with pytest.raises(ValueError, match="ticks"):
        robot.move({"tail": 0.0}, 3000)
    robot.close()


def test_no_reply_times_out_with_context() -> None:
    robot = Genibo(Link(MemoryTransport()), reply_timeout=0.1).open()
    with pytest.raises(ResponseTimeout, match="BATTERY"):
        robot.battery()
    robot.close()


def test_frames_for_other_robots_are_ignored() -> None:
    mem = MemoryTransport()
    robot = Genibo(Link(mem), robot_id=0x20, reply_timeout=0.3).open()
    mem.inject(LinkFrame(0x21, 0x63, b"\x01\x02").encode())
    with pytest.raises(ResponseTimeout):
        robot.battery()
    robot.close()


def test_mpsu_direct_ram_read(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, _ = sim
    mem = robot.mpsu_read_ram(70, 7, mpsu_id=dev.mpsu_id)
    assert mem.address == 70 and mem.data == bytes(dev.mpsu_ram[70:77])


def test_stop_joints_uses_last_command(sim: tuple[Genibo, VirtualGenibo, VirtualClock]) -> None:
    robot, dev, clock = sim
    with pytest.raises(JointError):
        robot.stop_joints(["head_pan"])
    robot.move({"head_pan": 30.0}, 1000)
    clock.advance(0.5)
    robot.stop_joints(["head_pan"])
    frozen = dev.snapshot()[15]
    clock.advance(1.0)
    assert dev.snapshot()[15] == frozen < robot.joints["head_pan"].to_raw(30.0)
