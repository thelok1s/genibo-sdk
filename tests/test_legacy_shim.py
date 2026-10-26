"""The CGeniboBt shim replays the golden scenarios with the legacy method names."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from genibo.legacy import CGeniboBt, scan
from genibo.protocol.frames import LinkFrame
from genibo.transport.base import MemoryTransport

GOLDEN = json.loads((Path(__file__).parent / "golden" / "csharp_reference.json").read_text())
TX = {s["id"]: s for s in GOLDEN["tx"]}
REPO = Path(__file__).resolve().parents[1]


def utc(s: str) -> datetime:
    head, frac = s.rstrip("Z").split(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}+00:00")


def params(bt: CGeniboBt) -> None:
    bt.set_param(0, 0, 0, 0.0, 0.0, 512, 1024.0, 333.3)
    bt.set_param(1, 1, 1, 0.0, 0.0, 512, 1024.0, 333.3)
    bt.set_param(2, 15, 0, 0.0, 0.0, 512, 1024.0, 333.3)
    bt.set_param(3, 7, 0, 60.0, -45.0, 500, 1024.0, 333.3)


def payload_file(tmp: Path) -> str:
    p = tmp / "payload.bin"
    p.write_bytes(bytes(i % 256 for i in range(300)))
    return str(p)


SCENARIOS: dict[str, Callable[[CGeniboBt, Path], object]] = {
    "bt.ex20.play": lambda bt, t: bt.Cmd_Bluetooth_Play(0x20, 0, "7.act"),
    "bt.ex20.play.broadcast": lambda bt, t: bt.Cmd_Bluetooth_Play(0xFE, 1, "walk.act"),
    "bt.motion_play_now.short": lambda bt, t: bt.request_motion_play_now(1, "a"),
    "bt.motion_play_now.60": lambda bt, t: bt.request_motion_play_now(1, "c" * 56 + ".act"),
    "bt.motion_play_at": lambda bt, t: bt.request_motion_play(
        0, "dance.act", utc("2024-05-06T07:08:09.1234567Z"), 250
    ),
    "bt.action_play_alias": lambda bt, t: bt.Action_Play(0, "7.act"),
    "bt.task_play.51": lambda bt, t: bt.request_task_play(1, "b" * 47 + ".act"),
    "bt.motion_stop": lambda bt, t: bt.request_motion_stop(),
    "bt.filelist.user": lambda bt, t: bt.request_filelist(1),
    "bt.file_delete": lambda bt, t: bt.request_file_delete(1, "old.act"),
    "bt.file_delete_all": lambda bt, t: bt.request_file_delete(0),
    "bt.file_fetch": lambda bt, t: bt.request_file_upload(0, "7.act"),
    "bt.file_send": lambda bt, t: bt.request_file_download(1, payload_file(t), "my.act"),
    "bt.file_send.indexed": lambda bt, t: bt.request_file_download(0, 2, 5, payload_file(t), "part.act"),
    "bt.set_time": lambda bt, t: bt.request_set_time(utc("2023-11-03T12:34:56.7654321Z")),
    "bt.battery": lambda bt, t: bt.request_battery(),
    "bt.heartbeat": lambda bt, t: bt.request_heartbeat(),
    "bt.auth.long": lambda bt, t: bt.request_auth("u" * 30, "p" * 25),
    "bt.servo.move.multi": lambda bt, t: (
        params(bt),
        bt.set_cmd_angle(0, 10.0),
        bt.set_cmd_angle(1, -25.5),
        bt.set_cmd(2, 600),
        bt.request_move(1000),
    ),
    "bt.servo.move.flags": lambda bt, t: (
        params(bt),
        bt.set_cmd_angle(0, 5.0),
        bt.set_cmd_flag_led(0, True, False, True),
        bt.set_cmd_flag_mode(1, True),
        bt.set_cmd_angle(1, 100.0),
        bt.set_cmd_flag_led_blue(1, True),
        bt.request_move(500),
    ),
    "bt.servo.move.single": lambda bt, t: (params(bt), bt.set_cmd_angle(2, 30.0), bt.request_move(2, 300)),
    "bt.servo.move.limit_clip": lambda bt, t: (params(bt), bt.set_cmd_angle(3, 90.0), bt.request_move(200)),
    "bt.servo.move.empty": lambda bt, t: (params(bt), bt.request_move(1000)),
    "bt.servo.torque.on": lambda bt, t: bt.drvsrv(True, True),
    "bt.servo.torque.axis": lambda bt, t: (params(bt), bt.drvsrv(3, True, True)),
    "bt.servo.stop": lambda bt, t: (params(bt), bt.request_stop()),
    "bt.servo.ems": lambda bt, t: (params(bt), bt.request_ems()),
    "bt.servo.reset": lambda bt, t: (params(bt), bt.reset()),
    "bt.servo.move_after_stop": lambda bt, t: (
        params(bt),
        bt.request_stop(),
        bt.set_cmd_angle(0, 10.0),
        bt.request_move(1000),
    ),
    "bt.servo.stop_axis": lambda bt, t: (params(bt), bt.set_cmd_angle(0, 10.0), bt.request_stop(0)),
    "bt.mpsu.headled_buzz": lambda bt, t: bt.serial_mpsu_play_headled_buzz(0xFE, 3, 5),
    "bt.mpsu.rom3": lambda bt, t: bt.serial_mpsu_write_rom(0x40, 10, bytes([1, 2, 3])),
    "bt.mpsu.ram1": lambda bt, t: bt.serial_mpsu_write_ram(0x40, 0, 0x41),
    "bt.mpsu_direct.battery": lambda bt, t: bt.drbluetooth_mpsu_request_battery(),
    "bt.mpsu_direct.motion_reserve": lambda bt, t: bt.drbluetooth_mpsu_request_motion_play_reserve(
        5, False, 1500
    ),
    "bt.mpsu_direct.play_mpsu": lambda bt, t: bt.Cmd_Bluetooth_Play_Mpsu(0xFE, 3),
    "bt.mpsu_direct.motor_move": lambda bt, t: (
        params(bt),
        bt.set_cmd_angle(0, 10.0),
        bt.set_cmd_angle(1, -25.5),
        bt.drbluetooth_mpsu_motor_request_move(800),
    ),
}


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_shim_reproduces_legacy_bytes(sid: str, tmp_path: Path) -> None:
    sc = TX[sid]
    mem = MemoryTransport()
    bt = CGeniboBt(lambda port, baud: mem, legacy_truncate=True)
    assert bt.connect(5, 115200)
    bt.drbluetooth_set_id(sc["robot_id"])
    SCENARIOS[sid](bt, tmp_path)
    bt.disconnect()
    assert [w.hex() for w in mem.writes] == sc["writes"], sc["call"]


def test_shim_parses_replies_like_legacy_getters() -> None:
    mem = MemoryTransport()
    bt = CGeniboBt(lambda port, baud: mem)
    bt.connect(5, 115200)
    bt.drbluetooth_set_id(0x20)
    bt.request_battery()
    # zero-length heartbeat first: the legacy parser would have lost the battery frame
    mem.inject(LinkFrame(0x20, 0xF1).encode() + LinkFrame(0x20, 0x63, b"\x55\x4b").encode())
    assert bt.wait(1000)
    for _ in range(100):
        if bt.get_battery_robot():
            break
        import time

        time.sleep(0.01)
    assert (bt.get_battery_robot(), bt.get_battery_MID()) == (0x55, 0x4B)
    bt.disconnect()


def test_migration_scan_of_the_original_example() -> None:
    src = REPO / "compat/legacy/Examples/Ex20_Genibo/Form1.cs"  # verbatim upstream copy
    report = scan(src)
    assert report.variables == {"m_CGenibo": "CGenibo_Bt", "m_CMotor": "COjwMotor"}
    methods = [f.method for f in report.findings]
    assert "Cmd_Bluetooth_Play" in methods and "Mpsu_Play_Motion" in methods
    assert report.unmapped == []
    assert "def main(robot: Genibo)" in report.skeleton()
