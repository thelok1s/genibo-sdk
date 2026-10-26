"""Pure builders for every command the legacy host tools send.

Each function returns a :class:`LinkFrame` or :class:`BusPacket`; nothing here
performs I/O. Field layouts and truncation rules mirror ``Ojw.CGenibo_Bt`` and
``Ojw.CGenibo`` and are checked against golden bytes from the original code.

Strings: the legacy code used ``Encoding.Default`` (the Windows ANSI code page
of the host PC). The SDK defaults to strict ASCII, which is identical on every
code page; pass ``encoding="cp949"`` (Korean Windows) only if you need non-ASCII
names and have confirmed the robot accepts them.

Fixed-size name fields: by default names must fit *with* a NUL terminator, and
an :class:`EncodingError` is raised otherwise. ``legacy_truncate=True`` copies
the legacy truncation exactly (some legacy paths silently cut names or drop the
terminator); use it only to reproduce old behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from genibo.errors import EncodingError
from genibo.protocol.constants import (
    AUTH_FIELD,
    BROADCAST_ID,
    FILENAME_FIELD,
    MPSU_BATTERY_ADDRESS,
    MPSU_BATTERY_LENGTH,
    MPSU_STOP_INDEX,
    TCP_FILENAME_FIELD,
    TCP_ID_BASE,
    BusCommand,
    JogFlag,
    LinkCommand,
    ServoRegister,
    Storage,
    TcpChannel,
    TcpCommand,
    Torque,
)
from genibo.protocol.frames import BusPacket, LinkFrame

DEFAULT_ENCODING = "ascii"
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
_UNIX_EPOCH_IN_FILETIME_S = 11644473600


# --------------------------------------------------------------------------- helpers


def encode_text(text: str, encoding: str = DEFAULT_ENCODING) -> bytes:
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as exc:
        raise EncodingError(
            f"{text!r} cannot be encoded as {encoding}; robot-side names are byte strings "
            "(the legacy tool used the Windows ANSI code page)"
        ) from exc


def fixed_field(
    text: str,
    size: int,
    *,
    copy_limit: int,
    terminate_last: bool,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
    what: str = "name",
) -> bytes:
    """Encode *text* into a zero-padded field of *size* bytes.

    ``copy_limit`` is how many bytes the legacy loop copies; ``terminate_last``
    is True when the legacy code forces the last byte of the field to NUL.
    """
    raw = encode_text(text, encoding)
    if not raw:
        raise EncodingError(f"{what} must not be empty")
    if b"\x00" in raw:
        raise EncodingError(f"{what} must not contain NUL bytes")
    if not legacy_truncate and len(raw) > size - 1:
        raise EncodingError(
            f"{what} {text!r} is {len(raw)} bytes; the robot field holds {size - 1} bytes plus a terminator"
        )
    buf = bytearray(size)
    n = min(len(raw), copy_limit)
    buf[:n] = raw[:n]
    if terminate_last:
        buf[size - 1] = 0
    return bytes(buf)


def _storage(storage: Storage | int) -> int:
    value = int(storage)
    if value not in (0, 1):
        raise EncodingError(f"storage must be 0 (master) or 1 (user), got {storage!r}")
    return value


def _u8(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise EncodingError(f"{name} must be an integer 0..255, got {value!r}")
    return value


def to_filetime_ticks(when: datetime) -> int:
    """``DateTime.ToFileTime()``: 100 ns ticks since 1601-01-01 UTC.

    Naive datetimes are interpreted as local time, as .NET does for
    ``DateTimeKind.Unspecified``.
    """
    if when.tzinfo is None:
        when = when.astimezone()
    delta = when - _FILETIME_EPOCH
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


def unix_time_fields(when: datetime) -> bytes:
    """8 bytes: Unix seconds and microseconds, both big-endian 32-bit (legacy layout)."""
    ticks = to_filetime_ticks(when)
    sec = ticks // 10_000_000 - _UNIX_EPOCH_IN_FILETIME_S
    usec = (ticks // 10) % 1_000_000
    return (sec & 0xFFFFFFFF).to_bytes(4, "big") + (usec & 0xFFFFFFFF).to_bytes(4, "big")


# --------------------------------------------------------------------------- link: playback


def play_motion(
    robot_id: int,
    storage: Storage | int,
    name: str,
    at: datetime | None = None,
    *,
    add_ms: int = 0,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    """Play a motion file stored on the robot (``request_motion_play*``, cmd 0x40).

    Payload (60 bytes): storage, 51-byte name, start time (sec, usec). ``at=None``
    sends zeros, which is what ``request_motion_play_now`` does.
    """
    payload = bytearray()
    payload.append(_storage(storage))
    payload += fixed_field(
        name,
        FILENAME_FIELD,
        copy_limit=FILENAME_FIELD - 1,
        terminate_last=True,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
    )
    if at is None:
        if add_ms:
            raise EncodingError("add_ms needs a start time")
        payload += bytes(8)
    else:
        payload += unix_time_fields(at + timedelta(milliseconds=add_ms))
    return LinkFrame(robot_id, LinkCommand.PLAY, bytes(payload))


def play_task(
    robot_id: int,
    storage: Storage | int,
    name: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    """Play a task file (``request_task_play``, cmd 0x40, 52-byte payload)."""
    payload = bytes((_storage(storage),)) + fixed_field(
        name,
        FILENAME_FIELD,
        copy_limit=FILENAME_FIELD,
        terminate_last=False,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
    )
    return LinkFrame(robot_id, LinkCommand.PLAY, payload)


def is_playing(robot_id: int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.IS_PLAYING)


def stop_playback(robot_id: int) -> LinkFrame:
    """Stop motion/task playback (cmd 0x42; the legacy motion and task stop are identical)."""
    return LinkFrame(robot_id, LinkCommand.STOP)


# --------------------------------------------------------------------------- link: files


def file_list(robot_id: int, storage: Storage | int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.FILE_LIST, bytes((_storage(storage),)))


def file_send(
    robot_id: int,
    storage: Storage | int,
    name: str,
    content: bytes,
    *,
    index: int = 0,
    total: int = 1,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    """Store a file on the robot (legacy ``request_file_download``, cmd 0x32).

    Payload: storage, index (u16 BE), total (u16 BE), 51-byte name, content.
    """
    for label, v in (("index", index), ("total", total)):
        if not 0 <= v <= 0xFFFF:
            raise EncodingError(f"{label} must be 0..65535")
    payload = bytearray((_storage(storage),))
    payload += index.to_bytes(2, "big") + total.to_bytes(2, "big")
    payload += fixed_field(
        name,
        FILENAME_FIELD,
        copy_limit=FILENAME_FIELD - 1,
        terminate_last=True,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
    )
    payload += content
    return LinkFrame(robot_id, LinkCommand.FILE_SEND, bytes(payload))


def _storage_and_name(storage: Storage | int, name: str, encoding: str, legacy_truncate: bool) -> bytes:
    return bytes((_storage(storage),)) + fixed_field(
        name,
        FILENAME_FIELD,
        copy_limit=FILENAME_FIELD,
        terminate_last=False,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
    )


def file_fetch(
    robot_id: int,
    storage: Storage | int,
    name: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    """Request a file from the robot (legacy ``request_file_upload``, cmd 0x33)."""
    return LinkFrame(
        robot_id, LinkCommand.FILE_FETCH, _storage_and_name(storage, name, encoding, legacy_truncate)
    )


def file_delete(
    robot_id: int,
    storage: Storage | int,
    name: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    return LinkFrame(
        robot_id, LinkCommand.FILE_DELETE, _storage_and_name(storage, name, encoding, legacy_truncate)
    )


def file_delete_all(robot_id: int, storage: Storage | int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.FILE_DELETE_ALL, bytes((_storage(storage),)))


# --------------------------------------------------------------------------- link: status


def set_time(robot_id: int, when: datetime) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.SET_TIME, unix_time_fields(when))


def get_time(robot_id: int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.GET_TIME)


def battery(robot_id: int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.BATTERY)


def charge(robot_id: int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.CHARGE)


def remocon(robot_id: int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.REMOCON)


def heartbeat(robot_id: int) -> LinkFrame:
    return LinkFrame(robot_id, LinkCommand.HEARTBEAT)


def auth_payload(
    user: str, password: str, *, encoding: str = DEFAULT_ENCODING, legacy_truncate: bool = False
) -> bytes:
    return fixed_field(
        user,
        AUTH_FIELD,
        copy_limit=AUTH_FIELD,
        terminate_last=False,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
        what="user id",
    ) + fixed_field(
        password,
        AUTH_FIELD,
        copy_limit=AUTH_FIELD,
        terminate_last=False,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
        what="password",
    )


def auth(
    robot_id: int,
    user: str,
    password: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    return LinkFrame(
        robot_id,
        LinkCommand.AUTH,
        auth_payload(user, password, encoding=encoding, legacy_truncate=legacy_truncate),
    )


def raw(robot_id: int, command: int, payload: bytes = b"") -> LinkFrame:
    """Escape hatch: an arbitrary link frame (``send_data``)."""
    return LinkFrame(robot_id, _u8("command", command), payload)


# --------------------------------------------------------------------------- servo bus


@dataclass(frozen=True)
class JogEntry:
    """One servo in an S_JOG packet: raw position (or speed), SET flags, servo ID."""

    raw: int
    flags: int
    servo_id: int

    def encode(self) -> bytes:
        if not -0x8000 <= self.raw <= 0xFFFF:
            raise EncodingError(f"servo value {self.raw} does not fit in 16 bits")
        v = self.raw & 0xFFFF
        return bytes(
            (v & 0xFF, (v >> 8) & 0xFF, _u8("flags", int(self.flags)), _u8("servo_id", self.servo_id))
        )


def s_jog(
    ticks: int, entries: Sequence[JogEntry], *, device_id: int = BROADCAST_ID, command: int = BusCommand.S_JOG
) -> BusPacket:
    """Synchronised move: play-time byte followed by 4 bytes per servo (``request_move``)."""
    data = bytes((_u8("play time ticks", ticks),)) + b"".join(e.encode() for e in entries)
    return BusPacket(device_id, command, data)


def ram_write(device_id: int, address: int, data: bytes) -> BusPacket:
    """Servo RAM write: address, length, data (cmd 0x03)."""
    return BusPacket(
        device_id, BusCommand.RAM_WRITE, bytes((_u8("address", address), _u8("length", len(data)))) + data
    )


def torque(device_id: int, mode: Torque) -> BusPacket:
    """``drvsrv``: write the torque-control register (52)."""
    return ram_write(device_id, ServoRegister.TORQUE_CONTROL, bytes((int(mode),)))


def legacy_reset_packet() -> BusPacket:
    """What legacy ``reset()`` actually transmits: an S_JOG to 0xFE with no data.

    ``request_reset`` builds a REBOOT (0x09) buffer but passes a zero length to
    ``Make_And_Send_Packet(0xfe, 0x06, ...)``, so only an empty S_JOG goes out.
    """
    return BusPacket(BROADCAST_ID, BusCommand.S_JOG, b"")


def mpsu_headled_buzz(mpsu_id: int, led: int, buzz: int) -> BusPacket:
    """Head-LED pattern and buzzer melody (0 = none; legacy range 1..63)."""
    return BusPacket(mpsu_id, BusCommand.MPSU_HEADLED_BUZZ, bytes((_u8("led", led), _u8("buzz", buzz))))


def mpsu_play_motion(mpsu_id: int, index: int, ready: bool = False) -> BusPacket:
    """Play an MPSU-stored motion by index (``0xFE`` stops)."""
    return BusPacket(
        mpsu_id, BusCommand.MPSU_PLAY_MOTION, bytes((_u8("motion index", index), 1 if ready else 0))
    )


def mpsu_play_motion_reserved(mpsu_id: int, index: int, ready: bool, delay_ms: int) -> BusPacket:
    """``drbluetooth_mpsu_request_motion_play_reserve``: index, ready, delay (u16 little-endian)."""
    if not 0 <= delay_ms <= 0xFFFF:
        raise EncodingError("delay_ms must be 0..65535")
    return BusPacket(
        mpsu_id,
        BusCommand.MPSU_PLAY_MOTION,
        bytes((_u8("motion index", index), 1 if ready else 0, delay_ms & 0xFF, delay_ms >> 8)),
    )


def mpsu_stop_motion(mpsu_id: int) -> BusPacket:
    """``drbluetooth_mpsu_request_motion_stop``: index 0xFE, ready 0."""
    return BusPacket(mpsu_id, BusCommand.MPSU_PLAY_MOTION, bytes((MPSU_STOP_INDEX, 0)))


def mpsu_play_task(mpsu_id: int, task: int) -> BusPacket:
    return BusPacket(mpsu_id, BusCommand.MPSU_PLAY_TASK, bytes((_u8("task", task),)))


def mpsu_write(mpsu_id: int, address: int, data: bytes, *, rom: bool) -> BusPacket:
    cmd = BusCommand.MPSU_ROM_WRITE if rom else BusCommand.MPSU_RAM_WRITE
    return BusPacket(mpsu_id, cmd, bytes((_u8("address", address), _u8("length", len(data)))) + data)


def mpsu_ram_read(mpsu_id: int, address: int, length: int) -> BusPacket:
    return BusPacket(
        mpsu_id, BusCommand.MPSU_RAM_READ, bytes((_u8("address", address), _u8("length", length)))
    )


def mpsu_battery_read(mpsu_id: int) -> BusPacket:
    """``drbluetooth_mpsu_request_battery``: RAM read 70..76."""
    return mpsu_ram_read(mpsu_id, MPSU_BATTERY_ADDRESS, MPSU_BATTERY_LENGTH)


def tunnel(robot_id: int, packet: BusPacket) -> LinkFrame:
    """Wrap a bus packet in link command 0x81 (``Make_And_Send_Packet`` / ``serial_mpsu_*``)."""
    return LinkFrame(robot_id, LinkCommand.SERVO_BUS, packet.encode())


# --------------------------------------------------------------------------- TCP link


def tcp_frame(channel: TcpChannel | int, command: int, payload: bytes = b"") -> LinkFrame:
    """``CGenibo.SendData``: same framing as the serial link, ID = 0x90 + channel."""
    ch = int(channel)
    if ch not in range(len(TcpChannel)):
        raise EncodingError(f"unknown TCP channel {channel!r}")
    return LinkFrame(TCP_ID_BASE + ch, _u8("command", command), payload)


def tcp_auth(user: str, password: str, *, encoding: str = DEFAULT_ENCODING) -> list[LinkFrame]:
    """``CGenibo.Auth``: the same 42-byte payload on all six channels, in channel order."""
    payload = auth_payload(user, password, encoding=encoding)
    return [tcp_frame(ch, TcpCommand.AUTH, payload) for ch in TcpChannel]


def tcp_action_play(
    name: str,
    *,
    master: bool = True,
    sound: bool = False,
    emoticon: bool = False,
    speed_percent: int = 100,
    encoding: str = DEFAULT_ENCODING,
    legacy_truncate: bool = False,
) -> LinkFrame:
    """``CGenibo.ActionPlay``: 16-byte name, sound flag, emoticon flag, speed percent."""
    field_bytes = fixed_field(
        name,
        TCP_FILENAME_FIELD,
        copy_limit=TCP_FILENAME_FIELD - 1,
        terminate_last=True,
        encoding=encoding,
        legacy_truncate=legacy_truncate,
    )
    payload = field_bytes + bytes(
        (1 if sound else 0, 1 if emoticon else 0, _u8("speed_percent", speed_percent))
    )
    cmd = TcpCommand.ACTION_PLAY_MASTER if master else TcpCommand.ACTION_PLAY_USER
    return tcp_frame(TcpChannel.MOTION, cmd, payload)


__all__ = [
    "DEFAULT_ENCODING",
    "JogEntry",
    "JogFlag",
    "auth",
    "auth_payload",
    "battery",
    "charge",
    "encode_text",
    "file_delete",
    "file_delete_all",
    "file_fetch",
    "file_list",
    "file_send",
    "fixed_field",
    "get_time",
    "heartbeat",
    "is_playing",
    "legacy_reset_packet",
    "mpsu_battery_read",
    "mpsu_headled_buzz",
    "mpsu_play_motion",
    "mpsu_play_motion_reserved",
    "mpsu_play_task",
    "mpsu_ram_read",
    "mpsu_stop_motion",
    "mpsu_write",
    "play_motion",
    "play_task",
    "ram_write",
    "raw",
    "remocon",
    "s_jog",
    "set_time",
    "stop_playback",
    "tcp_action_play",
    "tcp_auth",
    "tcp_frame",
    "to_filetime_ticks",
    "torque",
    "tunnel",
    "unix_time_fields",
]
