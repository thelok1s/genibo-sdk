"""High-level client for the Genibo Bluetooth serial link (successor of ``Ojw.CGenibo_Bt``).

Example::

    from genibo import Genibo, Storage

    with Genibo.serial("/dev/tty.GENIBO", robot_id=0x20) as robot:
        print(robot.battery())
        robot.play_motion("7.act", Storage.MASTER)

Everything the robot *does* is decided by its own firmware; this class only
sends the frames the legacy tool sent. Reply formats are those the legacy host
parser expected and have not been confirmed on hardware (see
docs/COMPATIBILITY.md).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import IntFlag
from pathlib import Path

from genibo.clock import Clock, RealClock
from genibo.errors import JointError, RobotStatusError, SafetyLatchError
from genibo.joints import JointMap, legacy_cgenibo_profile
from genibo.link import Link
from genibo.protocol import commands as cmd
from genibo.protocol.constants import (
    BROADCAST_ID,
    BUS_REPLY_MPSU_RAM,
    DEFAULT_BAUDRATE,
    JogFlag,
    LinkCommand,
    StatusCode,
    Storage,
    Torque,
)
from genibo.protocol.convert import ms_to_ticks, ticks_to_ms
from genibo.protocol.frames import BusPacket, Frame, LinkFrame
from genibo.protocol.replies import (
    Battery,
    MpsuMemory,
    Remocon,
    Status,
    parse_battery,
    parse_file_content,
    parse_file_list,
    parse_is_playing,
    parse_mpsu_memory,
    parse_remocon,
    parse_status,
    parse_time,
)
from genibo.transport.base import Transport

log = logging.getLogger("genibo.robot")

DEFAULT_ROBOT_ID = 0x20  # the ID used by the legacy Ex20_Genibo example


class Led(IntFlag):
    """Servo LED colours carried in the S_JOG SET byte."""

    OFF = 0
    GREEN = JogFlag.LED_GREEN
    BLUE = JogFlag.LED_BLUE
    RED = JogFlag.LED_RED


class Genibo:
    """Command a Genibo over a :class:`~genibo.link.Link`.

    ``robot_id`` addresses the robot on the Bluetooth link (``drbluetooth_set_id``).
    ``joints`` maps joint names to servo IDs and calibration (see
    :mod:`genibo.joints`); the default profile is explicitly unverified.
    """

    def __init__(
        self,
        link: Link,
        *,
        robot_id: int = DEFAULT_ROBOT_ID,
        joints: JointMap | None = None,
        encoding: str = cmd.DEFAULT_ENCODING,
        reply_timeout: float = 1.0,
        clock: Clock | None = None,
    ) -> None:
        self.link = link
        self.robot_id = robot_id
        self.link.robot_id = robot_id
        self.joints = joints if joints is not None else legacy_cgenibo_profile()
        self.encoding = encoding
        self.reply_timeout = reply_timeout
        self.clock: Clock = clock or RealClock()
        self._latched = False
        self._warned_unverified: set[str] = set()
        self._last_raw: dict[str, int] = {}

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_transport(cls, transport: Transport, **kw: object) -> Genibo:
        return cls(Link(transport), **kw)  # type: ignore[arg-type]

    @classmethod
    def serial(cls, port: str | int, baudrate: int = DEFAULT_BAUDRATE, **kw: object) -> Genibo:
        """Bluetooth SPP / serial port. ``port`` may be ``"COM5"``, ``5`` or ``"/dev/tty.X"``."""
        from genibo.transport.serial import SerialTransport

        return cls.from_transport(SerialTransport(port, baudrate), **kw)

    @classmethod
    def simulator(cls, device: object | None = None, **kw: object) -> Genibo:
        """Connect to an in-process :class:`~genibo.sim.VirtualGenibo` (NOT hardware)."""
        from genibo.sim import SimTransport, VirtualGenibo

        robot_id = int(kw.get("robot_id", DEFAULT_ROBOT_ID))  # type: ignore[call-overload]
        dev = device if device is not None else VirtualGenibo(robot_id=robot_id)
        return cls.from_transport(SimTransport(dev), **kw)  # type: ignore[arg-type]

    def open(self) -> Genibo:
        if not self.link.is_open:
            self.link.open()
        return self

    def close(self) -> None:
        self.link.close()

    def __enter__(self) -> Genibo:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def is_hardware(self) -> bool:
        return self.link.transport.is_hardware

    # ------------------------------------------------------------------ plumbing
    def send(self, frame: Frame) -> bytes:
        """Send a prepared frame as-is (escape hatch; no validation beyond framing)."""
        return self.link.send(frame)

    def send_raw(self, command: int, payload: bytes = b"") -> bytes:
        """``send_data(cmd, size, data)``: an arbitrary link frame to this robot."""
        return self.link.send(cmd.raw(self.robot_id, command, payload))

    def _is_reply(self, request: LinkFrame, reply_cmd: int) -> object:
        def match(f: Frame) -> bool:
            if not isinstance(f, LinkFrame):
                return False
            if f.command == reply_cmd:
                return True
            return f.command == LinkCommand.STATUS and len(f.payload) >= 2 and f.payload[0] == request.command

        return match

    def _query(
        self, request: LinkFrame, reply_cmd: int | None = None, timeout: float | None = None
    ) -> LinkFrame:
        reply_cmd = request.command if reply_cmd is None else reply_cmd
        reply = self.link.request(request, self._is_reply(request, reply_cmd), timeout or self.reply_timeout)  # type: ignore[arg-type]
        assert isinstance(reply, LinkFrame)
        if reply.command == LinkCommand.STATUS and reply_cmd != LinkCommand.STATUS:
            st = parse_status(reply.payload)
            raise RobotStatusError(
                st.command, st.code, f"robot reported {st.label} for command 0x{st.command:02x}"
            )
        return reply

    def _command(self, request: LinkFrame, ack_timeout: float | None) -> Status | None:
        """Send; optionally wait for a STATUS (0xF0) acknowledgement of this command.

        The legacy tool never required an acknowledgement for these commands,
        so the default (``ack_timeout=None``) is fire-and-forget.
        """
        if ack_timeout is None:
            self.link.send(request)
            return None

        def match(f: Frame) -> bool:
            return (
                isinstance(f, LinkFrame)
                and f.command == LinkCommand.STATUS
                and len(f.payload) >= 2
                and f.payload[0] == request.command
            )

        reply = self.link.request(request, match, ack_timeout)
        assert isinstance(reply, LinkFrame)
        st = parse_status(reply.payload)
        if not st.ok:
            raise RobotStatusError(
                st.command, st.code, f"robot reported {st.label} for command 0x{st.command:02x}"
            )
        return st

    # ------------------------------------------------------------------ playback (files on the robot)
    def play_motion(
        self,
        name: str,
        storage: Storage = Storage.MASTER,
        *,
        at: datetime | None = None,
        add_ms: int = 0,
        ack_timeout: float | None = None,
    ) -> Status | None:
        """Play a motion file stored on the robot (``request_motion_play[_now]``).

        ``at`` schedules the start (Unix time on the robot clock); ``None`` = now.
        """
        self._check_latch()
        return self._command(
            cmd.play_motion(self.robot_id, storage, name, at, add_ms=add_ms, encoding=self.encoding),
            ack_timeout,
        )

    def play_task(
        self, name: str, storage: Storage = Storage.MASTER, *, ack_timeout: float | None = None
    ) -> Status | None:
        self._check_latch()
        return self._command(cmd.play_task(self.robot_id, storage, name, encoding=self.encoding), ack_timeout)

    def stop_playback(self, *, ack_timeout: float | None = None) -> Status | None:
        """Stop motion/task playback (cmd 0x42)."""
        return self._command(cmd.stop_playback(self.robot_id), ack_timeout)

    def is_playing(self, timeout: float | None = None) -> bool:
        return parse_is_playing(self._query(cmd.is_playing(self.robot_id), timeout=timeout).payload)

    # ------------------------------------------------------------------ files
    def list_files(self, storage: Storage = Storage.MASTER, timeout: float | None = None) -> list[str]:
        reply = self._query(cmd.file_list(self.robot_id, storage), timeout=timeout)
        return list(parse_file_list(reply.payload, encoding=self.encoding).names)

    def upload_file(
        self,
        name: str,
        content: bytes | Path,
        storage: Storage = Storage.USER,
        *,
        index: int = 0,
        total: int = 1,
        ack_timeout: float | None = None,
    ) -> Status | None:
        """Store a file on the robot (legacy name: ``request_file_download``, PC -> robot).

        The bytes are sent verbatim; the SDK does not know the ``.act`` format.
        """
        data = content.read_bytes() if isinstance(content, Path) else bytes(content)
        frame = cmd.file_send(
            self.robot_id, storage, name, data, index=index, total=total, encoding=self.encoding
        )
        return self._command(frame, ack_timeout)

    def download_file(self, name: str, storage: Storage = Storage.MASTER, timeout: float = 10.0) -> bytes:
        """Fetch a file from the robot (legacy name: ``request_file_upload``, robot -> PC)."""
        reply = self._query(
            cmd.file_fetch(self.robot_id, storage, name, encoding=self.encoding), timeout=timeout
        )
        return parse_file_content(reply.payload, encoding=self.encoding).content

    def delete_file(
        self, name: str, storage: Storage = Storage.USER, *, ack_timeout: float | None = None
    ) -> Status | None:
        return self._command(
            cmd.file_delete(self.robot_id, storage, name, encoding=self.encoding), ack_timeout
        )

    def delete_all_files(
        self, storage: Storage, *, confirm: bool = False, ack_timeout: float | None = None
    ) -> Status | None:
        if not confirm:
            raise ValueError("delete_all_files erases every file in the area; pass confirm=True")
        return self._command(cmd.file_delete_all(self.robot_id, storage), ack_timeout)

    # ------------------------------------------------------------------ status
    def battery(self, timeout: float | None = None) -> Battery:
        return parse_battery(self._query(cmd.battery(self.robot_id), timeout=timeout).payload)

    def get_time(self, timeout: float | None = None) -> datetime:
        return parse_time(self._query(cmd.get_time(self.robot_id), timeout=timeout).payload).as_datetime()

    def set_time(self, when: datetime | None = None, *, ack_timeout: float | None = None) -> Status | None:
        return self._command(cmd.set_time(self.robot_id, when or datetime.now(UTC)), ack_timeout)

    def remocon(self, timeout: float | None = None) -> Remocon:
        return parse_remocon(self._query(cmd.remocon(self.robot_id), timeout=timeout).payload)

    def charge(self, *, ack_timeout: float | None = None) -> Status | None:
        """Send the legacy ``request_charge`` command (0x64). Its effect is undocumented."""
        return self._command(cmd.charge(self.robot_id), ack_timeout)

    def heartbeat(self, timeout: float | None = None) -> float:
        """Round-trip time in seconds of a heartbeat (0xF1)."""
        t0 = time.monotonic()
        self._query(cmd.heartbeat(self.robot_id), timeout=timeout)
        return time.monotonic() - t0

    def auth(self, user: str, password: str, timeout: float = 1.0) -> bool | None:
        """Send credentials (0x71). Returns the robot's verdict, or None if it did not answer.

        The Bluetooth legacy host never parsed an auth reply; the TCP host did
        (byte 0: 0 = rejected). ``None`` is therefore a normal outcome.
        """
        frame = cmd.auth(self.robot_id, user, password, encoding=self.encoding)
        try:
            reply = self._query(frame, timeout=timeout)
        except TimeoutError:
            log.info("no auth reply within %.1fs", timeout)
            return None
        return bool(reply.payload[:1] != b"\x00")

    # ------------------------------------------------------------------ servos
    def _check_latch(self) -> None:
        if self._latched:
            raise SafetyLatchError("emergency stop is latched; call clear_emergency() first")

    def _warn_unverified(self, names: list[str]) -> None:
        if not self.is_hardware:
            return
        fresh = [
            n
            for n in names
            if n in self.joints and not self.joints[n].verified and n not in self._warned_unverified
        ]
        if fresh:
            self._warned_unverified.update(fresh)
            log.warning(
                "joint calibration not verified on hardware: %s",
                ", ".join(fresh),
                extra={"event": "unverified_joints", "joints": fresh},
            )

    def jog_entries(
        self,
        targets: Mapping[str, float],
        *,
        raw: bool = False,
        clip: bool = False,
        led: Led = Led.OFF,
        flags: JogFlag = JogFlag.NONE,
    ) -> list[cmd.JogEntry]:
        """Resolve named targets to S_JOG entries, in joint-table order (legacy axis order)."""
        unknown = [n for n in targets if n not in self.joints]
        if unknown:
            raise JointError(
                f"unknown joint(s): {', '.join(map(str, unknown))}; known: {', '.join(self.joints)}"
            )
        speed = bool(flags & JogFlag.SPEED_MODE)
        entries = []
        for name, joint in self.joints.items():
            if name not in targets:
                continue
            value = targets[name]
            if raw:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise JointError(f"raw target for {name!r} must be an int, got {value!r}")
                position = value if speed else joint.check_raw(value)
            else:
                position = joint.to_raw(float(value), clip=clip, speed_mode=speed)
            entries.append(cmd.JogEntry(position, int(flags) | int(led), joint.servo_id))
        return entries

    def move(
        self,
        targets: Mapping[str, float],
        ms: int,
        *,
        raw: bool = False,
        clip: bool = False,
        led: Led = Led.OFF,
        wait: bool = False,
    ) -> bytes:
        """Move joints to angles (degrees) or raw positions in ``ms`` milliseconds.

        One S_JOG packet (servo broadcast 0xFE) tunnelled in link command 0x81,
        exactly as ``set_cmd_angle(...); request_move(ms)``. ``ms`` is limited to
        255 ticks (~2856 ms). ``wait=True`` blocks for the play time.
        """
        self._check_latch()
        ticks = ms_to_ticks(ms)
        entries = self.jog_entries(targets, raw=raw, clip=clip, led=led)
        if not entries:
            raise JointError("move() needs at least one target")
        self._warn_unverified(list(targets))
        data = self.link.send(cmd.tunnel(self.robot_id, cmd.s_jog(ticks, entries)))
        for name in targets:
            sid = self.joints[name].servo_id
            self._last_raw[name] = next(e.raw for e in entries if e.servo_id == sid)
        if wait:
            self.clock.sleep(ticks_to_ms(ticks) / 1000.0)
        return data

    def jog(self, entries: list[cmd.JogEntry], ms: int, *, device_id: int = BROADCAST_ID) -> bytes:
        """Low-level S_JOG with explicit raw entries and flags (speed mode, LEDs, stop...)."""
        self._check_latch()
        return self.link.send(
            cmd.tunnel(self.robot_id, cmd.s_jog(ms_to_ticks(ms), entries, device_id=device_id))
        )

    def stop_joints(self, joints: list[str], *, ms: int = 1000) -> bytes:
        """S_JOG entries with the STOP flag, carrying each joint's last commanded value.

        This is what legacy ``request_stop(axis)`` transmits. Legacy
        ``request_stop()`` (all axes) never transmitted anything because of an
        early return, so the robot-side effect of the STOP flag is unverified.
        """
        missing = [j for j in joints if j not in self._last_raw]
        if missing:
            raise JointError(f"no previous command for {', '.join(missing)}; move them first or use jog()")
        entries = [cmd.JogEntry(self._last_raw[j], JogFlag.STOP, self.joints[j].servo_id) for j in joints]
        return self.link.send(cmd.tunnel(self.robot_id, cmd.s_jog(ms_to_ticks(ms), entries)))

    def torque(self, on: bool = True, joint: str | None = None) -> bytes:
        """Torque on (0x60) / free (0x00) for one joint or every servo (``drvsrv``)."""
        if on:
            self._check_latch()
        return self.torque_mode(Torque.ON if on else Torque.FREE, joint)

    def torque_mode(self, mode: Torque, joint: str | None = None) -> bytes:
        if mode != Torque.FREE:
            self._check_latch()
        device = BROADCAST_ID if joint is None else self.joints[joint].servo_id
        return self.link.send(cmd.tunnel(self.robot_id, cmd.torque(device, mode)))

    def emergency_stop(self) -> bytes:
        """Torque-free broadcast and latch further motion (what legacy ``request_ems`` sends)."""
        self._latched = True
        log.warning("emergency stop: torque free broadcast", extra={"event": "ems"})
        return self.link.send(cmd.tunnel(self.robot_id, cmd.torque(BROADCAST_ID, Torque.FREE)))

    def clear_emergency(self) -> None:
        """Release the host-side latch (legacy ``reset_stop``/``reset_ems``). Sends nothing."""
        self._latched = False

    @property
    def emergency_latched(self) -> bool:
        return self._latched

    # ------------------------------------------------------------------ MPSU (motion controller)
    def _mpsu(self, packet: BusPacket, direct: bool) -> bytes:
        if direct:
            return self.link.send(packet)
        return self.link.send(cmd.tunnel(self.robot_id, packet))

    def head_led_buzz(self, led: int = 0, buzz: int = 0, *, mpsu_id: int = BROADCAST_ID) -> bytes:
        """Head LED pattern and/or buzzer melody (legacy ranges 1..63; 0 = none)."""
        return self._mpsu(cmd.mpsu_headled_buzz(mpsu_id, led, buzz), direct=False)

    def mpsu_play_motion(
        self, index: int, *, ready: bool = False, mpsu_id: int = BROADCAST_ID, direct: bool = False
    ) -> bytes:
        self._check_latch()
        return self._mpsu(cmd.mpsu_play_motion(mpsu_id, index, ready), direct)

    def mpsu_stop_motion(self, *, mpsu_id: int = BROADCAST_ID, direct: bool = False) -> bytes:
        return self._mpsu(cmd.mpsu_stop_motion(mpsu_id), direct)

    def mpsu_play_task(self, task: int, *, mpsu_id: int = BROADCAST_ID, direct: bool = False) -> bytes:
        self._check_latch()
        return self._mpsu(cmd.mpsu_play_task(mpsu_id, task), direct)

    def mpsu_write(self, address: int, data: bytes, *, rom: bool, mpsu_id: int) -> bytes:
        """Write MPSU ROM/RAM. ROM writes persist on the controller; use with care."""
        return self._mpsu(cmd.mpsu_write(mpsu_id, address, data, rom=rom), direct=False)

    def mpsu_read_ram(
        self, address: int, length: int, *, mpsu_id: int, timeout: float | None = None
    ) -> MpsuMemory:
        """Direct (untunnelled) MPSU RAM read, as ``drbluetooth_mpsu_request_battery`` does."""
        req = cmd.mpsu_ram_read(mpsu_id, address, length)

        def match(f: Frame) -> bool:
            return (
                isinstance(f, BusPacket)
                and f.command == BUS_REPLY_MPSU_RAM
                and (mpsu_id == BROADCAST_ID or f.device_id == mpsu_id)
            )

        reply = self.link.request(req, match, timeout or self.reply_timeout)
        assert isinstance(reply, BusPacket)
        return parse_mpsu_memory(reply.data)


__all__ = ["DEFAULT_ROBOT_ID", "Genibo", "Led", "StatusCode", "Storage", "Torque"]
