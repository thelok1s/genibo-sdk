"""Decoders for robot -> host payloads.

Layouts come from the legacy receive code (``CGenibo_Bt.Parser`` and the
``CGenibo.Receive_*`` threads). They describe what the legacy host *expected*;
none of them has been checked against a physical robot in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from genibo.errors import FrameError
from genibo.protocol.constants import FILENAME_FIELD, StatusCode, Storage


def _need(payload: bytes, n: int, what: str) -> None:
    if len(payload) != n:
        raise FrameError(f"{what} reply must be {n} bytes, got {len(payload)}")


def _cstr(raw: bytes, encoding: str) -> str:
    return raw.split(b"\x00", 1)[0].decode(encoding, errors="replace")


@dataclass(frozen=True)
class Battery:
    robot_percent: int
    mid_percent: int  # "MID" battery in the legacy code; its meaning is undocumented


def parse_battery(payload: bytes) -> Battery:
    _need(payload, 2, "battery")
    return Battery(payload[0], payload[1])


def parse_is_playing(payload: bytes) -> bool:
    _need(payload, 1, "is-playing")
    return payload[0] != 0


@dataclass(frozen=True)
class RobotTime:
    seconds: int
    microseconds: int

    def as_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.seconds, tz=UTC).replace(microsecond=self.microseconds % 1_000_000)


def parse_time(payload: bytes) -> RobotTime:
    _need(payload, 8, "time")
    return RobotTime(int.from_bytes(payload[0:4], "big"), int.from_bytes(payload[4:8], "big"))


@dataclass(frozen=True)
class Remocon:
    length_ticks: int  # 0.125 s per tick according to the legacy comment
    data: int

    @property
    def length_s(self) -> float:
        return self.length_ticks * 0.125


def parse_remocon(payload: bytes) -> Remocon:
    _need(payload, 2, "remocon")
    return Remocon(payload[0], payload[1])


@dataclass(frozen=True)
class FileList:
    storage: Storage
    names: tuple[str, ...]


def parse_file_list(payload: bytes, *, encoding: str = "ascii") -> FileList:
    if len(payload) < 3:
        raise FrameError("file-list reply shorter than 3 bytes")
    count = (payload[1] << 8) | payload[2]
    expected = 3 + FILENAME_FIELD * count
    if len(payload) != expected:
        raise FrameError(f"file-list reply announces {count} names ({expected} bytes) but has {len(payload)}")
    # Legacy: storage byte 0 means master, anything else user.
    storage = Storage.MASTER if payload[0] == 0 else Storage.USER
    names = tuple(
        _cstr(payload[3 + i * FILENAME_FIELD : 3 + (i + 1) * FILENAME_FIELD], encoding) for i in range(count)
    )
    return FileList(storage, names)


@dataclass(frozen=True)
class FileContent:
    storage: Storage
    name: str
    content: bytes


def parse_file_content(payload: bytes, *, encoding: str = "ascii") -> FileContent:
    if len(payload) <= 1 + FILENAME_FIELD:
        raise FrameError("file reply too short")
    storage = Storage.MASTER if payload[0] == 0 else Storage.USER
    name = _cstr(payload[1 : 1 + FILENAME_FIELD], encoding)
    return FileContent(storage, name, bytes(payload[1 + FILENAME_FIELD :]))


@dataclass(frozen=True)
class Status:
    command: int  # the command being acknowledged (byte 0)
    code: int

    @property
    def ok(self) -> bool:
        return self.code in (StatusCode.RECEIVE_OK, StatusCode.PROCESS_END)

    @property
    def label(self) -> str:
        try:
            return StatusCode(self.code).name
        except ValueError:
            return f"UNKNOWN_{self.code}"


def parse_status(payload: bytes) -> Status:
    if len(payload) < 2:
        raise FrameError("status reply needs at least 2 bytes")
    return Status(payload[0], payload[1])


@dataclass(frozen=True)
class MpsuMemory:
    """Reply to an MPSU RAM/ROM read: ``address, length, data..., status1, status2``."""

    address: int
    data: bytes
    status1: int
    status2: int


def parse_mpsu_memory(data: bytes) -> MpsuMemory:
    if len(data) < 4:
        raise FrameError("MPSU memory reply too short")
    address, length = data[0], data[1]
    body = data[2:-2]
    if len(body) != length:
        raise FrameError(f"MPSU memory reply announces {length} bytes but carries {len(body)}")
    return MpsuMemory(address, bytes(body), data[-2], data[-1])
