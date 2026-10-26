"""Hardware tests: run ONLY against a physical Genibo, opt-in via environment variables.

    GENIBO_PORT=/dev/tty.GENIBO GENIBO_ROBOT_ID=0x20 uv run pytest -m hardware tests/hardware -s

Optional, each one enables more invasive checks:

    GENIBO_MOTION_FILE=7.act      play a motion that already exists in the master area
    GENIBO_UPLOAD_OK=1            write (and delete) a scratch file in the user area
    GENIBO_JOG_JOINT=head_pan     move ONE joint by +/-5 degrees around its centre
                                  (uses the project's joint table; robot must be clear)

Every assertion here records what the robot actually did. Results belong in
docs/HARDWARE_TESTING.md; until then the compatibility status stays "unverified".
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest

from genibo import Genibo, Storage

PORT = os.environ.get("GENIBO_PORT")
pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(not PORT, reason="set GENIBO_PORT to run hardware tests"),
]


@pytest.fixture(scope="module")
def robot() -> Iterator[Genibo]:
    rid = int(os.environ.get("GENIBO_ROBOT_ID", "0x20"), 0)
    with Genibo.serial(PORT or "", robot_id=rid, reply_timeout=2.0) as r:
        yield r


def test_heartbeat_reply(robot: Genibo) -> None:
    rtt = robot.heartbeat()
    print(f"heartbeat rtt {rtt * 1000:.1f} ms")


def test_battery_reply_layout(robot: Genibo) -> None:
    b = robot.battery()
    print(f"battery robot={b.robot_percent} mid={b.mid_percent}")
    assert 0 <= b.robot_percent <= 100


def test_time_roundtrip(robot: Genibo) -> None:
    before = robot.get_time()
    print("robot time", before.isoformat())


def test_file_list_master(robot: Genibo) -> None:
    names = robot.list_files(Storage.MASTER)
    print("master files:", names)


@pytest.mark.skipif(not os.environ.get("GENIBO_MOTION_FILE"), reason="set GENIBO_MOTION_FILE")
def test_play_and_stop_motion(robot: Genibo) -> None:
    name = os.environ["GENIBO_MOTION_FILE"]
    robot.play_motion(name, Storage.MASTER)
    time.sleep(0.5)
    print("is_playing after start:", robot.is_playing())
    robot.stop_playback()
    time.sleep(0.5)
    print("is_playing after stop:", robot.is_playing())


@pytest.mark.skipif(not os.environ.get("GENIBO_UPLOAD_OK"), reason="set GENIBO_UPLOAD_OK=1")
def test_upload_download_delete_user_file(robot: Genibo) -> None:
    payload = bytes(range(64))
    robot.upload_file("sdk_test.bin", payload, Storage.USER)
    time.sleep(1.0)
    assert "sdk_test.bin" in robot.list_files(Storage.USER)
    assert robot.download_file("sdk_test.bin", Storage.USER) == payload
    robot.delete_file("sdk_test.bin", Storage.USER)


@pytest.mark.skipif(not os.environ.get("GENIBO_JOG_JOINT"), reason="set GENIBO_JOG_JOINT=<joint>")
def test_single_joint_small_move(robot: Genibo) -> None:
    joint = os.environ["GENIBO_JOG_JOINT"]
    robot.torque(True, joint)
    for angle in (5.0, -5.0, 0.0):
        robot.move({joint: angle}, 800, wait=True)
        time.sleep(0.3)
    robot.torque(False, joint)
