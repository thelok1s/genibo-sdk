"""Byte-for-byte comparison with the original OpenJigWare C# implementation.

``tests/golden/csharp_reference.json`` is produced by ``compat/csharp-reference``,
which compiles the unmodified ``COjw_25_Genibo.cs`` and records what it writes
to the serial port / sockets. Every transmit scenario must be reproduced exactly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from genibo.errors import EncodingError
from genibo.joints import Joint
from genibo.protocol import commands as c
from genibo.protocol.constants import JogFlag, Torque
from genibo.protocol.convert import (
    ServoCalibration,
    angle_to_raw,
    ms_to_ticks,
    ms_to_ticks_legacy,
    raw_to_angle,
)
from genibo.protocol.frames import BusPacket, LinkFrame, StreamDecoder
from genibo.protocol.replies import (
    parse_battery,
    parse_file_list,
    parse_is_playing,
    parse_mpsu_memory,
    parse_remocon,
    parse_status,
    parse_time,
)

GOLDEN = json.loads((Path(__file__).parent / "golden" / "csharp_reference.json").read_text())
TX = {s["id"]: s for s in GOLDEN["tx"]}
RX = {s["id"]: s for s in GOLDEN["rx"]}
TABLES = {t["id"]: t for t in GOLDEN["tables"]}

# The four axes configured by the harness's Params(): (servo id, calibration).
CAL = ServoCalibration(512, 1024.0, 333.3)
AXES = {
    0: Joint("a0", 0, CAL, raw_max=0x3FFF),
    1: Joint("a1", 1, ServoCalibration(512, 1024.0, 333.3, direction=1), raw_max=0x3FFF),
    2: Joint("a2", 15, CAL, raw_max=0x3FFF),
    3: Joint("a3", 7, ServoCalibration(500, 1024.0, 333.3, limit_up=60.0, limit_dn=-45.0), raw_max=0x3FFF),
}


def utc(s: str) -> datetime:
    # .NET ticks have 100 ns resolution; Python keeps microseconds. The encoded
    # fields only carry microseconds, so truncating the 7th digit is exact.
    head, frac = s.rstrip("Z").split(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}+00:00")


def enc(*frames: LinkFrame | BusPacket) -> list[str]:
    return [f.encode().hex() for f in frames]


def jog(rid: int, ms: int, *entries: c.JogEntry, device: int = 0xFE, command: int = 0x06) -> list[str]:
    return enc(c.tunnel(rid, c.s_jog(ms_to_ticks(ms), list(entries), device_id=device, command=command)))


BUILDERS: dict[str, Callable[[dict[str, Any], int], list[str]]] = {
    "bt.connect.ex20": lambda a, r: [],
    "bt.ex20.play": lambda a, r: enc(c.play_motion(0x20, 0, "7.act")),
    "bt.ex20.play.broadcast": lambda a, r: enc(c.play_motion(0xFE, 1, "walk.act")),
    "bt.motion_play_now.short": lambda a, r: enc(c.play_motion(r, a["storage"], a["name"])),
    "bt.motion_play_now.50": lambda a, r: enc(c.play_motion(r, a["storage"], a["name"])),
    "bt.motion_play_now.51": lambda a, r: enc(
        c.play_motion(r, a["storage"], a["name"], legacy_truncate=True)
    ),
    "bt.motion_play_now.60": lambda a, r: enc(
        c.play_motion(r, a["storage"], a["name"], legacy_truncate=True)
    ),
    "bt.motion_play_at": lambda a, r: enc(
        c.play_motion(r, a["storage"], a["name"], utc(a["utc"]), add_ms=a["add_ms"])
    ),
    "bt.action_play_alias": lambda a, r: enc(c.play_motion(r, a["storage"], a["name"])),
    "bt.motion_stop": lambda a, r: enc(c.stop_playback(r)),
    "bt.motion_is_playing": lambda a, r: enc(c.is_playing(r)),
    "bt.task_play": lambda a, r: enc(c.play_task(r, a["storage"], a["name"])),
    "bt.task_play.51": lambda a, r: enc(c.play_task(r, a["storage"], a["name"], legacy_truncate=True)),
    "bt.task_stop": lambda a, r: enc(c.stop_playback(r)),
    "bt.task_is_playing": lambda a, r: enc(c.is_playing(r)),
    "bt.filelist.master": lambda a, r: enc(c.file_list(r, a["storage"])),
    "bt.filelist.user": lambda a, r: enc(c.file_list(r, a["storage"])),
    "bt.file_delete": lambda a, r: enc(c.file_delete(r, a["storage"], a["name"])),
    "bt.file_delete_all": lambda a, r: enc(c.file_delete_all(r, a["storage"])),
    "bt.file_fetch": lambda a, r: enc(c.file_fetch(r, a["storage"], a["name"])),
    "bt.file_send": lambda a, r: enc(
        c.file_send(r, a["storage"], a["name"], bytes.fromhex(a["content_hex"]))
    ),
    "bt.file_send.indexed": lambda a, r: enc(
        c.file_send(
            r, a["storage"], a["name"], bytes.fromhex(a["content_hex"]), index=a["index"], total=a["total"]
        )
    ),
    "bt.file_send.name60": lambda a, r: enc(
        c.file_send(r, a["storage"], a["name"], bytes.fromhex(a["content_hex"]), legacy_truncate=True)
    ),
    "bt.set_time": lambda a, r: enc(c.set_time(r, utc(a["utc"]))),
    "bt.get_time": lambda a, r: enc(c.get_time(r)),
    "bt.battery": lambda a, r: enc(c.battery(r)),
    "bt.charge": lambda a, r: enc(c.charge(r)),
    "bt.remocon": lambda a, r: enc(c.remocon(r)),
    "bt.heartbeat": lambda a, r: enc(c.heartbeat(r)),
    "bt.auth": lambda a, r: enc(c.auth(r, a["user"], a["password"])),
    "bt.auth.long": lambda a, r: enc(c.auth(r, a["user"], a["password"], legacy_truncate=True)),
    "bt.send_command": lambda a, r: enc(c.raw(r, a["cmd"])),
    "bt.send_data.300": lambda a, r: enc(c.raw(r, a["cmd"], bytes.fromhex(a["data_hex"]))),
    "bt.servo.move.multi": lambda a, r: jog(
        r,
        1000,
        c.JogEntry(AXES[0].to_raw(10.0), 0, 0),
        c.JogEntry(AXES[1].to_raw(-25.5), 0, 1),
        c.JogEntry(600, 0, 15),
    ),
    "bt.servo.move.flags": lambda a, r: jog(
        r,
        500,
        c.JogEntry(AXES[0].to_raw(5.0), JogFlag.LED_GREEN | JogFlag.LED_RED, 0),
        c.JogEntry(AXES[1].to_raw(100.0, speed_mode=True), JogFlag.SPEED_MODE | JogFlag.LED_BLUE, 1),
    ),
    "bt.servo.move.single": lambda a, r: jog(r, 300, c.JogEntry(AXES[2].to_raw(30.0), 0, 15)),
    "bt.servo.move.limit_clip": lambda a, r: jog(r, 200, c.JogEntry(AXES[3].to_raw(90.0, clip=True), 0, 7)),
    "bt.servo.move.empty": lambda a, r: jog(r, 1000),
    "bt.servo.torque.on": lambda a, r: enc(c.tunnel(r, c.torque(0xFE, Torque.ON))),
    "bt.servo.torque.off": lambda a, r: enc(c.tunnel(r, c.torque(0xFE, Torque.FREE))),
    "bt.servo.torque.brake": lambda a, r: enc(c.tunnel(r, c.torque(0xFE, Torque.DRIVER))),
    "bt.servo.torque.axis": lambda a, r: enc(c.tunnel(r, c.torque(7, Torque.ON))),
    # Legacy request_stop() transmits nothing (it latches m_bStop before request_move checks it).
    "bt.servo.stop": lambda a, r: [],
    # Legacy request_ems() therefore only sends the torque-free broadcast.
    "bt.servo.ems": lambda a, r: enc(c.tunnel(r, c.torque(0xFE, Torque.FREE))),
    "bt.servo.reset": lambda a, r: enc(c.tunnel(r, c.legacy_reset_packet())),
    "bt.servo.move_after_stop": lambda a, r: [],
    "bt.servo.stop_axis": lambda a, r: jog(r, 1000, c.JogEntry(AXES[0].to_raw(10.0), JogFlag.STOP, 0)),
    "bt.mpsu.headled_buzz": lambda a, r: enc(
        c.tunnel(r, c.mpsu_headled_buzz(a["mpsu"], a["led"], a["buzz"]))
    ),
    "bt.mpsu.headled": lambda a, r: enc(c.tunnel(r, c.mpsu_headled_buzz(a["mpsu"], a["led"], 0))),
    "bt.mpsu.buzz": lambda a, r: enc(c.tunnel(r, c.mpsu_headled_buzz(a["mpsu"], 0, a["buzz"]))),
    "bt.mpsu.task": lambda a, r: enc(c.tunnel(r, c.mpsu_play_task(a["mpsu"], a["task"]))),
    "bt.mpsu.motion": lambda a, r: enc(c.tunnel(r, c.mpsu_play_motion(a["mpsu"], a["motion"], a["ready"]))),
    "bt.mpsu.rom1": lambda a, r: enc(
        c.tunnel(r, c.mpsu_write(a["mpsu"], a["address"], bytes.fromhex(a["data_hex"]), rom=True))
    ),
    "bt.mpsu.rom3": lambda a, r: enc(
        c.tunnel(r, c.mpsu_write(a["mpsu"], a["address"], bytes.fromhex(a["data_hex"]), rom=True))
    ),
    "bt.mpsu.ram1": lambda a, r: enc(
        c.tunnel(r, c.mpsu_write(a["mpsu"], a["address"], bytes.fromhex(a["data_hex"]), rom=False))
    ),
    "bt.mpsu.ram2": lambda a, r: enc(
        c.tunnel(r, c.mpsu_write(a["mpsu"], a["address"], bytes.fromhex(a["data_hex"]), rom=False))
    ),
    "bt.mpsu_direct.battery": lambda a, r: enc(c.mpsu_battery_read(r)),
    "bt.mpsu_direct.motion": lambda a, r: enc(c.mpsu_play_motion(r, a["motion"], a["ready"])),
    "bt.mpsu_direct.motion_ready": lambda a, r: enc(c.mpsu_play_motion(r, a["motion"], a["ready"])),
    "bt.mpsu_direct.motion_stop": lambda a, r: enc(c.mpsu_stop_motion(r)),
    "bt.mpsu_direct.motion_reserve": lambda a, r: enc(
        c.mpsu_play_motion_reserved(r, a["motion"], a["ready"], a["delay_ms"])
    ),
    "bt.mpsu_direct.play_mpsu": lambda a, r: enc(c.mpsu_play_motion(a["robot"], a["motion"], False)),
    "bt.mpsu_direct.motor_move": lambda a, r: jog(
        r,
        800,
        c.JogEntry(AXES[0].to_raw(10.0), 0, 0),
        c.JogEntry(AXES[1].to_raw(-25.5), 0, 1),
        device=r,
        command=0x0F,
    ),
}

TCP_BUILDERS: dict[str, Callable[[dict[str, Any]], list[tuple[int, str]]]] = {
    "tcp.auth": lambda a: [
        (f.robot_id - 0x90, f.encode().hex()) for f in c.tcp_auth(a["user"], a["password"])
    ],
    "tcp.action_play.master": lambda a: [(3, c.tcp_action_play(a["name"], master=True).encode().hex())],
    "tcp.action_play.user": lambda a: [
        (
            3,
            c.tcp_action_play(a["name"], master=False, sound=True, emoticon=True, speed_percent=50)
            .encode()
            .hex(),
        )
    ],
    "tcp.action_play.long": lambda a: [
        (
            3,
            c.tcp_action_play(a["name"], master=True, sound=True, speed_percent=255, legacy_truncate=True)
            .encode()
            .hex(),
        )
    ],
    "tcp.send_data": lambda a: [(4, c.tcp_frame(a["channel"], a["cmd"]).encode().hex())],
}


def test_every_golden_scenario_has_a_python_equivalent() -> None:
    covered = set(BUILDERS) | set(TCP_BUILDERS)
    assert set(TX) == covered, f"uncovered: {set(TX) - covered}, stale: {covered - set(TX)}"


@pytest.mark.parametrize("sid", sorted(BUILDERS))
def test_bluetooth_tx_matches_legacy(sid: str) -> None:
    sc = TX[sid]
    assert BUILDERS[sid](sc["args"], sc["robot_id"]) == sc["writes"], sc["call"]


@pytest.mark.parametrize("sid", sorted(TCP_BUILDERS))
def test_tcp_tx_matches_legacy(sid: str) -> None:
    sc = TX[sid]
    expected = [(w["channel"], w["hex"]) for w in sc["writes"]]
    assert TCP_BUILDERS[sid](sc["args"]) == expected, sc["call"]


def test_legacy_serial_settings() -> None:
    a = TX["bt.connect.ex20"]["args"]
    assert (a["port"], a["baudrate"], a["parity"], a["data_bits"], a["stop_bits"]) == (
        "COM5",
        115200,
        "None",
        8,
        "One",
    )


# --------------------------------------------------------------- strict mode refuses what legacy mangled


@pytest.mark.parametrize(
    "build",
    [
        lambda: c.play_motion(0x20, 1, "b" * 47 + ".act"),  # 51 bytes: legacy silently drops the last byte
        lambda: c.play_task(0x20, 1, "b" * 47 + ".act"),  # 51 bytes: legacy drops the terminator
        lambda: c.file_send(0x20, 1, "c" * 56 + ".act", b"x"),
        lambda: c.auth(0x20, "u" * 30, "p"),
        lambda: c.tcp_action_play("abcdefghijklmnopqrst.act"),
        lambda: c.play_motion(0x20, 0, "도"),  # non-ASCII needs an explicit encoding
    ],
)
def test_strict_mode_rejects_names_the_legacy_code_truncated(build: Callable[[], object]) -> None:
    with pytest.raises(EncodingError):
        build()


def test_strict_mode_rejects_wrapping_play_time() -> None:
    # Legacy CalcTime_ms gives 256 ticks for 2862 ms and writes 256 & 0xFF == 0.
    assert ms_to_ticks_legacy(2862) == 256
    with pytest.raises(EncodingError):
        ms_to_ticks(2862)
    assert ms_to_ticks(2856) == 255


# --------------------------------------------------------------- numeric tables


def test_calc_time_table() -> None:
    t = TABLES["calc_time_ms"]
    assert [ms_to_ticks_legacy(x) for x in t["inputs"]] == t["outputs"]


@pytest.mark.parametrize("k", range(4))
def test_angle_tables(k: int) -> None:
    fwd, back = TABLES[f"angle_to_raw.{k}"], TABLES[f"raw_to_angle.{k}"]
    cal = ServoCalibration(fwd["center"], fwd["mech"], fwd["degree"], fwd["dir"])
    assert [angle_to_raw(cal, a) for a in fwd["inputs"]] == fwd["outputs"]
    # Exact float equality: both sides are the same float32 value widened to double.
    assert [raw_to_angle(cal, r) for r in back["inputs"]] == back["outputs"]


@pytest.mark.parametrize("tid", ["set_cmd_angle.clip", "set_cmd_angle.clip.dir1"])
def test_limit_clipping_table(tid: str) -> None:
    t = TABLES[tid]
    cal = ServoCalibration(t["center"], t["mech"], t["degree"], t["dir"], t["limit_up"], t["limit_dn"])
    j = Joint("x", 1, cal, raw_max=0x3FFF)
    assert [j.to_raw(a, clip=True) for a in t["inputs"]] == t["outputs"]


# --------------------------------------------------------------- receive side


def _frames(sid: str) -> list[Any]:
    return StreamDecoder().feed(bytes.fromhex(RX[sid]["input"]))


def test_rx_replies_decode_like_legacy() -> None:
    (f,) = _frames("rx.battery")
    b = parse_battery(f.payload)
    assert (b.robot_percent, b.mid_percent) == (RX["rx.battery"]["result"]["battery_robot"], 75)
    (f,) = _frames("rx.playing")
    assert parse_is_playing(f.payload) is RX["rx.playing"]["result"]["is_playing"]
    (f,) = _frames("rx.time")
    t = parse_time(f.payload)
    assert (t.seconds, t.microseconds) == (
        RX["rx.time"]["result"]["time_sec"],
        RX["rx.time"]["result"]["time_usec"],
    )
    (f,) = _frames("rx.remocon")
    r = parse_remocon(f.payload)
    assert (r.length_ticks, r.data) == (3, 33)
    for sid in ("rx.filelist.master", "rx.filelist.user"):
        (f,) = _frames(sid)
        assert list(parse_file_list(f.payload).names) == RX[sid]["result"]["names"]
    (f,) = _frames("rx.status")
    assert parse_status(f.payload).code == RX["rx.status"]["result"]["status"]
    (f,) = _frames("rx.broadcast_id")
    assert f.robot_id == 0xFE


def test_rx_bad_checksum_is_rejected_like_legacy() -> None:
    items = _frames("rx.bad_checksum")
    assert not any(isinstance(i, LinkFrame) for i in items)
    assert RX["rx.bad_checksum"]["result"]["seq"] == 0


def test_rx_zero_length_frame_is_handled_where_legacy_lost_data() -> None:
    # Documented divergence: the legacy parser swallows the frame after a
    # zero-length one (seq stays 0). The SDK decodes both frames.
    assert RX["rx.zero_length_then_battery"]["result"]["seq"] == 0
    hb, bat = _frames("rx.zero_length_then_battery")
    assert (hb.command, hb.payload) == (0xF1, b"")
    assert parse_battery(bat.payload).robot_percent == 0x55


def test_rx_mpsu_memory_reply_offsets() -> None:
    # Documented divergence: legacy copies from the address byte (ram[70..72] = 70, 3, 0x11).
    assert RX["rx.mpsu_ram"]["result"]["ram_70_76"][:3] == [70, 3, 0x11]
    (pkt,) = _frames("rx.mpsu_ram")
    assert isinstance(pkt, BusPacket) and pkt.command == 0x54
    mem = parse_mpsu_memory(pkt.data)
    assert (mem.address, mem.data) == (70, bytes([0x11, 0x22, 0x33]))


def test_golden_file_tracks_the_legacy_source() -> None:
    src = Path(__file__).resolve().parents[1] / GOLDEN["source_file"]  # vendored in compat/legacy
    import hashlib

    assert hashlib.sha256(src.read_bytes()).hexdigest() == GOLDEN["source_sha256"], (
        "COjw_25_Genibo.cs changed; regenerate tests/golden with compat/csharp-reference"
    )
