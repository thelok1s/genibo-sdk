"""``CGeniboBt``: the legacy ``Ojw.CGenibo_Bt`` API, method for method, on the new stack.

Purpose: migrate existing C# code by translating it line by line, then move to
:class:`genibo.Genibo` at your own pace. The bytes this shim transmits are
golden-tested against the original implementation, *including* its quirks:

* ``request_stop()`` latches the stop flag but transmits nothing;
* ``request_ems()`` therefore only sends the torque-free broadcast;
* ``reset()`` sends an empty S_JOG instead of the REBOOT it builds;
* ``request_move`` only includes axes set since the previous move.

Deliberate deviations (raise instead of corrupting a frame):

* an axis without ``set_param`` raises :class:`JointError` on angle commands
  (legacy encoded NaN/garbage);
* play times beyond 255 ticks raise (legacy wrapped modulo 256);
* names that do not fit their field raise unless ``legacy_truncate=True``;
* replies are decoded correctly where the legacy parser lost data (a
  zero-length frame no longer swallows the next frame; MPSU RAM replies land
  at the right offset).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from genibo.errors import JointError
from genibo.link import Link
from genibo.protocol import commands as cmd
from genibo.protocol.constants import (
    BROADCAST_ID,
    BUS_REPLY_MPSU_RAM,
    FILENAME_FIELD,
    BusCommand,
    JogFlag,
    LinkCommand,
    Torque,
)
from genibo.protocol.convert import (
    ServoCalibration,
    angle_to_raw,
    clip_raw_legacy,
    ms_to_ticks,
    raw_to_angle,
)
from genibo.protocol.frames import BusPacket, Frame, LinkFrame
from genibo.protocol.replies import parse_mpsu_memory
from genibo.transport.base import Transport

(
    _RET_RECEIVE_OK,
    _RET_PROCESS_END,
    _RET_PROCESS_FAILED,
    _RET_CHECKSUM_ERROR,
    _RET_TIME_OUT_ERROR,
    _RET_LENGTH_ERROR,
) = range(6)


@dataclass
class _Axis:
    servo_id: int = 0
    cal: ServoCalibration | None = None
    enabled: bool = False
    pos: int = 0
    flag: int = int(JogFlag.NO_ACTION)


def _default_transport(port: int | str, baudrate: int) -> Transport:
    from genibo.transport.serial import SerialTransport

    return SerialTransport(port, baudrate)


class CGeniboBt:
    """Port of ``Ojw.CGenibo_Bt`` (Bluetooth serial). Names and semantics follow the C# class."""

    _RESULT_OK = True
    _RESULT_FAIL = False
    _SIZE_FILENAME = FILENAME_FIELD

    def __init__(
        self,
        transport_factory: Callable[[int | str, int], Transport] = _default_transport,
        *,
        legacy_truncate: bool = False,
        encoding: str = "ascii",
    ) -> None:
        self._factory = transport_factory
        self._link: Link | None = None
        self._robot_id = 0
        self._axes: list[_Axis] = [_Axis() for _ in range(256)]
        self._motor_max = 30
        self._stop = False
        self._ems = False
        self.legacy_truncate = legacy_truncate
        self.encoding = encoding
        # Reply state (what the legacy Parser() stored)
        self._lock = threading.Lock()
        self._seq = 0
        self._seq_back = 0
        self._status = 0
        self._battery = (0, 0)
        self._playing = False
        self._time = (0, 0)
        self._remocon = (0, 0)
        self._files: dict[int, list[str]] = {0: [], 1: []}
        self._mpsu_ram = bytearray(256)

    # ------------------------------------------------------------------ connection
    def connect(self, nBluetoothSppPort: int | str, nBaudRate: int) -> bool:
        if self.connected():
            return True
        try:
            self._link = Link(self._factory(nBluetoothSppPort, nBaudRate), robot_id=None).open()
        except Exception:
            self._link = None
            return False
        self._link.add_listener(self._on_frame)
        return True

    def connected(self) -> bool:
        return self._link is not None and self._link.is_open

    def disconnect(self) -> None:
        if self._link is not None:
            self._link.close()
            self._link = None

    @property
    def link(self) -> Link:
        if self._link is None:
            raise ConnectionError("not connected")
        return self._link

    def _send(self, frame: Frame) -> bool:
        if not self.connected():
            return self._RESULT_FAIL
        self.link.send(frame)
        return self._RESULT_OK

    # ------------------------------------------------------------------ ids
    def drbluetooth_set_id(self, nRobotID: int) -> None:
        self._robot_id = nRobotID

    def drbluetooth_get_id(self) -> int:
        return self._robot_id

    # ------------------------------------------------------------------ axes
    def set_param(
        self,
        nAxis: int,
        nID: int,
        nDir: int,
        fLimitUp: float,
        fLimitDn: float,
        nCenterPos: int,
        fMechMove: float,
        fDegree: float,
    ) -> None:
        a = self._axes[nAxis]
        a.servo_id = nID
        a.cal = ServoCalibration(nCenterPos, fMechMove, fDegree, nDir, fLimitUp, fLimitDn)

    def set_max(self, nMotorMax: int) -> None:
        self._motor_max = nMotorMax

    def get_max(self) -> int:
        return self._motor_max

    def InitCmd(self) -> None:
        for i in range(self._motor_max):
            self._axes[i].enabled = False
            self._axes[i].flag = int(JogFlag.NO_ACTION)

    def _cal(self, nAxis: int) -> ServoCalibration:
        cal = self._axes[nAxis].cal
        if cal is None:
            raise JointError(f"axis {nAxis} has no set_param(); the legacy code would send garbage")
        return cal

    def set_cmd(self, nAxis: int, nPos: int) -> None:
        a = self._axes[nAxis]
        a.enabled, a.pos = True, nPos
        a.flag &= ~int(JogFlag.NO_ACTION)

    def set_cmd_angle(self, nAxis: int, fAngle: float) -> None:
        a = self._axes[nAxis]
        speed = bool(a.flag & JogFlag.SPEED_MODE)
        raw = angle_to_raw(self._cal(nAxis), fAngle, speed_mode=speed)
        if not speed:
            raw = clip_raw_legacy(self._cal(nAxis), raw)
        self.set_cmd(nAxis, raw)

    def get_cmd(self, nAxis: int) -> int:
        return self._axes[nAxis].pos

    def get_cmd_angle(self, nAxis: int) -> float:
        a = self._axes[nAxis]
        return raw_to_angle(self._cal(nAxis), a.pos, speed_mode=bool(a.flag & JogFlag.SPEED_MODE))

    def set_cmd_flag(self, nAxis: int, *args: object) -> None:
        if len(args) == 1:
            self._axes[nAxis].flag = int(args[0])  # type: ignore[call-overload]
            return
        stop, speed, green, blue, red, no_action = (bool(x) for x in args)
        f = 0
        for on, bit in (
            (stop, JogFlag.STOP),
            (speed, JogFlag.SPEED_MODE),
            (green, JogFlag.LED_GREEN),
            (blue, JogFlag.LED_BLUE),
            (red, JogFlag.LED_RED),
            (no_action, JogFlag.NO_ACTION),
        ):
            if on:
                f |= int(bit)
        self._axes[nAxis].flag = f

    def get_cmd_flag(self, nAxis: int) -> int:
        return self._axes[nAxis].flag

    def _set_bit(self, nAxis: int, bit: int, on: bool) -> None:
        a = self._axes[nAxis]
        a.flag = (a.flag & ~bit) | (bit if on else 0)

    def set_cmd_flag_stop(self, nAxis: int, bStop: bool) -> None:
        self._set_bit(nAxis, JogFlag.STOP, bStop)

    def set_cmd_flag_mode(self, nAxis: int, bMode_Speed: bool) -> None:
        self._set_bit(nAxis, JogFlag.SPEED_MODE, bMode_Speed)

    def set_cmd_flag_led(self, nAxis: int, bGreen: bool, bBlue: bool, bRed: bool) -> None:
        self._set_bit(nAxis, JogFlag.LED_GREEN, bGreen)
        self._set_bit(nAxis, JogFlag.LED_BLUE, bBlue)
        self._set_bit(nAxis, JogFlag.LED_RED, bRed)

    def set_cmd_flag_led_green(self, nAxis: int, b: bool) -> None:
        self._set_bit(nAxis, JogFlag.LED_GREEN, b)

    def set_cmd_flag_led_blue(self, nAxis: int, b: bool) -> None:
        self._set_bit(nAxis, JogFlag.LED_BLUE, b)

    def set_cmd_flag_led_red(self, nAxis: int, b: bool) -> None:
        self._set_bit(nAxis, JogFlag.LED_RED, b)

    def set_cmd_flag_no_action(self, nAxis: int, b: bool) -> None:
        self._set_bit(nAxis, JogFlag.NO_ACTION, b)

    def get_cmd_flag_mode(self, nAxis: int) -> int:
        return 1 if self._axes[nAxis].flag & JogFlag.SPEED_MODE else 0

    # ------------------------------------------------------------------ servo moves
    def _jog(self, axes: list[int], nTime: int, *, device_id: int, command: int, robot_id: int) -> bool:
        entries = []
        for i in axes:
            a = self._axes[i]
            if a.enabled:
                entries.append(cmd.JogEntry(a.pos, a.flag, a.servo_id))
                a.flag |= int(JogFlag.NO_ACTION)
            a.enabled = False
        packet = cmd.s_jog(ms_to_ticks(nTime), entries, device_id=device_id, command=command)
        return self._send(cmd.tunnel(robot_id, packet))

    def request_move(self, *args: int) -> bool:
        """``request_move(nTime)`` or ``request_move(nAxis, nTime)``."""
        if self._stop or self._ems:
            return self._RESULT_FAIL
        if not self.connected():
            return self._RESULT_FAIL
        if len(args) == 1:
            return self._jog(
                list(range(self._motor_max)),
                args[0],
                device_id=BROADCAST_ID,
                command=BusCommand.S_JOG,
                robot_id=self._robot_id,
            )
        n_axis, n_time = args
        return self._jog(
            [n_axis], n_time, device_id=BROADCAST_ID, command=BusCommand.S_JOG, robot_id=self._robot_id
        )

    def drbluetooth_mpsu_motor_request_move(self, nTime: int) -> bool:
        if self._stop or self._ems or not self.connected():
            return self._RESULT_FAIL
        return self._jog(
            list(range(self._motor_max)),
            nTime,
            device_id=self._robot_id,
            command=BusCommand.MPSU_MOTOR_MOVE,
            robot_id=self._robot_id,
        )

    def request_stop(self, nAxis: int | None = None) -> bool:
        if nAxis is not None:
            self.set_cmd_flag_stop(nAxis, True)
            return self.request_move(nAxis, 1000)
        # Quirk preserved: m_bStop is set *before* request_move, which then returns early.
        self._stop = True
        for i in range(self._motor_max):
            self.set_cmd_flag_stop(i, True)
        return self.request_move(1000)

    def request_ems(self) -> bool:
        ret = self.request_stop()
        self.drvsrv(False, False)
        self._ems = True
        return ret

    def reset_stop(self) -> None:
        self._stop = False
        for i in range(self._motor_max):
            self.set_cmd_flag_stop(i, False)

    def reset_ems(self) -> None:
        self._ems = False
        for i in range(self._motor_max):
            self.set_cmd_flag_stop(i, False)

    def request_reset(self, nAxis: int) -> bool:
        if self._stop or self._ems or not self.connected():
            return self._RESULT_FAIL
        if nAxis < BROADCAST_ID:
            self.set_cmd_flag(nAxis, 0)
        else:
            for j in range(self._motor_max):
                self.set_cmd_flag(j, 0)
        return self._send(cmd.tunnel(self._robot_id, cmd.legacy_reset_packet()))

    def reset(self) -> None:
        self.reset_stop()
        self.reset_ems()
        self.request_reset(BROADCAST_ID)

    def check_Ems(self) -> bool:
        return self._ems

    def check_stop(self) -> bool:
        return self._stop

    def drvsrv(self, *args: object) -> bool:
        """``drvsrv(bDriver, bServo)`` or ``drvsrv(nAxis, bDriver, bServo)``."""
        if len(args) == 2:
            device, drv, srv = BROADCAST_ID, args[0], args[1]
        else:
            n_axis = int(args[0])  # type: ignore[call-overload]
            device = BROADCAST_ID if n_axis == BROADCAST_ID else self._axes[n_axis].servo_id
            drv, srv = args[1], args[2]
        mode = Torque((Torque.DRIVER if drv else 0) | (Torque.SERVO if srv else 0))
        return self._send(cmd.tunnel(self._robot_id, cmd.torque(device, mode)))

    # ------------------------------------------------------------------ link commands
    def set_busy(self) -> None:
        self._seq_back = self._seq

    def request_motion_play_now(self, nMaster_or_User: int, strMotionFileName: str) -> bool:
        self.set_busy()
        return self._send(
            cmd.play_motion(
                self._robot_id,
                nMaster_or_User,
                strMotionFileName,
                encoding=self.encoding,
                legacy_truncate=self.legacy_truncate,
            )
        )  # type: ignore[arg-type]

    def request_motion_play(
        self,
        nMaster_or_User: int,
        strMotionFileName: str,
        dtTime: datetime | None = None,
        nAddMillisecond: int = 0,
    ) -> bool:
        self.set_busy()
        frame = cmd.play_motion(
            self._robot_id,
            nMaster_or_User,
            strMotionFileName,
            dtTime,
            add_ms=nAddMillisecond if dtTime else 0,
            encoding=self.encoding,
            legacy_truncate=self.legacy_truncate,
        )  # type: ignore[arg-type]
        return self._send(frame)

    Action_Play = request_motion_play_now

    def request_task_play(self, nMaster_or_User: int, strTaskFileName: str) -> bool:
        return self._send(
            cmd.play_task(
                self._robot_id,
                nMaster_or_User,
                strTaskFileName,
                encoding=self.encoding,
                legacy_truncate=self.legacy_truncate,
            )
        )  # type: ignore[arg-type]

    def request_motion_is_playing(self) -> bool:
        self.set_busy()
        return self._send(cmd.is_playing(self._robot_id))

    request_task_is_playing = request_motion_is_playing

    def request_motion_stop(self) -> bool:
        return self._send(cmd.stop_playback(self._robot_id))

    request_task_stop = request_motion_stop

    def request_filelist(self, nMaster_or_User: int) -> bool:
        self.set_busy()
        return self._send(cmd.file_list(self._robot_id, nMaster_or_User))

    def request_file_download(self, nMaster_or_User: int, *args: int | str) -> bool:
        """PC -> robot. ``(storage, path, name)`` or ``(storage, index, total, path, name)``."""
        if len(args) == 2:
            index, total, path, name = 0, 1, args[0], args[1]
        else:
            index, total, path, name = args
        try:
            with open(str(path), "rb") as fh:
                content = fh.read()
        except OSError:
            return self._RESULT_FAIL
        self.set_busy()
        frame = cmd.file_send(
            self._robot_id,
            nMaster_or_User,
            str(name),
            content,
            index=int(index),
            total=int(total),
            encoding=self.encoding,
            legacy_truncate=self.legacy_truncate,
        )
        return self._send(frame)

    def request_file_upload(self, nMaster_or_User: int, strTaskFileName: str) -> bool:
        """Robot -> PC (legacy naming)."""
        self.set_busy()
        return self._send(
            cmd.file_fetch(
                self._robot_id,
                nMaster_or_User,
                strTaskFileName,
                encoding=self.encoding,
                legacy_truncate=self.legacy_truncate,
            )
        )  # type: ignore[arg-type]

    def request_file_delete(self, nMaster_or_User: int, strTaskFileName: str | None = None) -> bool:
        self.set_busy()
        if strTaskFileName is None:
            return self._send(cmd.file_delete_all(self._robot_id, nMaster_or_User))
        return self._send(
            cmd.file_delete(
                self._robot_id,
                nMaster_or_User,
                strTaskFileName,
                encoding=self.encoding,
                legacy_truncate=self.legacy_truncate,
            )
        )  # type: ignore[arg-type]

    def request_set_time(self, dtTime: datetime) -> bool:
        self.set_busy()
        return self._send(cmd.set_time(self._robot_id, dtTime))

    def request_time(self) -> bool:
        self.set_busy()
        return self._send(cmd.get_time(self._robot_id))

    def request_battery(self) -> bool:
        self.set_busy()
        return self._send(cmd.battery(self._robot_id))

    def request_charge(self) -> bool:
        self.set_busy()
        return self._send(cmd.charge(self._robot_id))

    def request_remocon(self) -> bool:
        self.set_busy()
        return self._send(cmd.remocon(self._robot_id))

    def request_heartbeat(self) -> bool:
        self.set_busy()
        return self._send(cmd.heartbeat(self._robot_id))

    def request_auth(self, strID: str, strPassWord: str) -> bool:
        self.set_busy()
        return self._send(
            cmd.auth(
                self._robot_id,
                strID,
                strPassWord,
                encoding=self.encoding,
                legacy_truncate=self.legacy_truncate,
            )
        )  # type: ignore[arg-type]

    def send_command(self, nCommand: int) -> None:
        self._send(cmd.raw(self._robot_id, nCommand))

    def send_data(self, nCommand: int, nSize: int, byteArrayData: bytes | None) -> None:
        self._send(cmd.raw(self._robot_id, nCommand, bytes(byteArrayData or b"")[:nSize]))

    def Cmd_Bluetooth_Play(
        self,
        nRobot: int,
        nMaster: int,
        strFileName: str,
        dateTime: datetime | None = None,
        nAddMillisecond: int = 0,
    ) -> None:
        self.drbluetooth_set_id(nRobot)
        if dateTime is None:
            self.request_motion_play_now(nMaster, strFileName)
        else:
            self.request_motion_play(nMaster, strFileName, dateTime, nAddMillisecond)

    def Cmd_Bluetooth_Play_Mpsu(self, nRobot: int, nMotionNumber: int) -> None:
        self.drbluetooth_set_id(nRobot)
        self.drbluetooth_mpsu_request_motion_play(nMotionNumber, False)

    # ------------------------------------------------------------------ MPSU
    def _mpsu_wrapped(self, packet: BusPacket) -> bool:
        return self._send(cmd.tunnel(self._robot_id, packet))

    def serial_mpsu_play_headled_buzz(self, nMpsuID: int, nHeadLedNum_1_63: int, nBuzzNum_1_63: int) -> bool:
        return self._mpsu_wrapped(cmd.mpsu_headled_buzz(nMpsuID, nHeadLedNum_1_63, nBuzzNum_1_63))

    def serial_mpsu_play_headled(self, nMpsuID: int, nHeadLedNum_1_63: int) -> bool:
        return self._mpsu_wrapped(cmd.mpsu_headled_buzz(nMpsuID, nHeadLedNum_1_63, 0))

    def serial_mpsu_play_buzz(self, nMpsuID: int, nBuzzNum_1_63: int) -> bool:
        return self._mpsu_wrapped(cmd.mpsu_headled_buzz(nMpsuID, 0, nBuzzNum_1_63))

    def serial_mpsu_play_task(self, nMpsuID: int, nTaskNo: int) -> bool:
        return self._mpsu_wrapped(cmd.mpsu_play_task(nMpsuID, nTaskNo))

    def serial_mpsu_play_motion(self, nMpsuID: int, nMotionAddress: int, bReady: bool) -> bool:
        return self._mpsu_wrapped(cmd.mpsu_play_motion(nMpsuID, nMotionAddress, bReady))

    def serial_mpsu_write_rom(self, nMpsuID: int, nStartAddress: int, data: int | bytes) -> bool:
        payload = bytes((data,)) if isinstance(data, int) else bytes(data)
        return self._mpsu_wrapped(cmd.mpsu_write(nMpsuID, nStartAddress, payload, rom=True))

    def serial_mpsu_write_ram(self, nMpsuID: int, nStartAddress: int, data: int | bytes) -> bool:
        payload = bytes((data,)) if isinstance(data, int) else bytes(data)
        return self._mpsu_wrapped(cmd.mpsu_write(nMpsuID, nStartAddress, payload, rom=False))

    def drbluetooth_mpsu_send_data(self, nCommand: int, nSize: int, byteArrayData: bytes) -> None:
        self._send(BusPacket(self._robot_id, nCommand, bytes(byteArrayData)[:nSize]))

    def drbluetooth_mpsu_request_battery(self) -> bool:
        self.set_busy()
        return self._send(cmd.mpsu_battery_read(self._robot_id))

    def drbluetooth_mpsu_request_motion_play(self, nMotionIndex: int, bOnlyReadyPos: bool) -> bool:
        return self._send(cmd.mpsu_play_motion(self._robot_id, nMotionIndex, bOnlyReadyPos))

    def drbluetooth_mpsu_request_motion_stop(self) -> bool:
        return self._send(cmd.mpsu_stop_motion(self._robot_id))

    def drbluetooth_mpsu_request_motion_play_reserve(
        self, nMotionIndex: int, bOnlyReadyPos: bool, nMillisecond: int
    ) -> bool:
        return self._send(
            cmd.mpsu_play_motion_reserved(self._robot_id, nMotionIndex, bOnlyReadyPos, nMillisecond)
        )

    def drbluetooth_mpsu_get_data_ram(self, nAddress: int | None = None) -> int | bytes:
        with self._lock:
            return bytes(self._mpsu_ram) if nAddress is None else self._mpsu_ram[nAddress]

    # ------------------------------------------------------------------ replies (legacy Parser equivalents)
    def _on_frame(self, f: Frame) -> None:
        with self._lock:
            if isinstance(f, BusPacket):
                if f.command == BUS_REPLY_MPSU_RAM and f.device_id in (self._robot_id, BROADCAST_ID):
                    mem = parse_mpsu_memory(f.data)
                    self._mpsu_ram[mem.address : mem.address + len(mem.data)] = mem.data
                    self._seq += 1
                return
            assert isinstance(f, LinkFrame)
            if f.robot_id not in (self._robot_id, BROADCAST_ID):
                return
            p, c = f.payload, f.command
            if c == LinkCommand.STATUS and len(p) >= 2:
                self._status = p[1] if p[1] <= _RET_LENGTH_ERROR else self._status
            elif c == LinkCommand.HEARTBEAT:
                self._status = _RET_RECEIVE_OK if not p else _RET_LENGTH_ERROR
            elif c == LinkCommand.FILE_LIST and len(p) >= 3:
                n = (p[1] << 8) | p[2]
                if len(p) == 3 + FILENAME_FIELD * n:
                    names = [p[3 + i * FILENAME_FIELD : 3 + (i + 1) * FILENAME_FIELD] for i in range(n)]
                    self._files[0 if p[0] == 0 else 1] = [
                        x.split(b"\x00", 1)[0].decode(self.encoding, "replace") for x in names
                    ]
                self._status = _RET_RECEIVE_OK
            elif c == LinkCommand.IS_PLAYING:
                self._playing, self._status = (
                    (bool(p[0]), _RET_RECEIVE_OK) if len(p) == 1 else (self._playing, _RET_LENGTH_ERROR)
                )
            elif c == LinkCommand.GET_TIME:
                if len(p) == 8:
                    self._time, self._status = (
                        (int.from_bytes(p[:4], "big"), int.from_bytes(p[4:], "big")),
                        _RET_RECEIVE_OK,
                    )
                else:
                    self._status = _RET_LENGTH_ERROR
            elif c == LinkCommand.BATTERY:
                self._battery, self._status = (
                    ((p[0], p[1]), _RET_RECEIVE_OK) if len(p) == 2 else (self._battery, _RET_LENGTH_ERROR)
                )
            elif c == LinkCommand.REMOCON:
                self._remocon, self._status = (
                    ((p[0], p[1]), _RET_RECEIVE_OK) if len(p) == 2 else (self._remocon, _RET_LENGTH_ERROR)
                )
            self._seq += 1

    def is_busy(self) -> bool:
        return self.connected() and self._seq_back == self._seq

    def wait(self, nMilliSeconds: int = 100) -> bool:
        end = time.monotonic() + nMilliSeconds / 1000.0
        while self.connected() and time.monotonic() < end:
            if not self.is_busy():
                return True
            time.sleep(0.005)
        return False

    def seq(self) -> int:
        return self._seq

    def packet_status(self) -> int:
        return self._status

    def get_battery_robot(self) -> int:
        return self._battery[0]

    def get_battery_MID(self) -> int:
        return self._battery[1]

    def get_is_playing(self) -> bool:
        return self._playing

    def get_time_sec(self) -> int:
        return self._time[0]

    def get_time_usec(self) -> int:
        return self._time[1]

    def get_remocon_length(self) -> int:
        return self._remocon[0]

    def get_remocon_data(self) -> int:
        return self._remocon[1]

    def get_FileList_Count(self, nMaster_User: int) -> int:
        return len(self._files[0 if nMaster_User == 0 else 1])

    def get_FileList(self, nMaster_User: int, nFileIndex: int) -> str | None:
        files = self._files[0 if nMaster_User == 0 else 1]
        return files[nFileIndex] if nFileIndex < len(files) else None

    @staticmethod
    def packet_status_tostring(nPacketStat: int) -> str:
        return {
            0: "Receive OK",
            1: "Process End",
            2: "Process Failed",
            3: "Check Sum Error",
            4: "Time Out Error",
            5: "Length Error",
        }.get(nPacketStat, "Unknown Status")
