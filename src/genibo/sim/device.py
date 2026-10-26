"""VirtualGenibo: a protocol-level stand-in for the robot.

It consumes the same bytes a physical Genibo receives and answers with the
reply layouts the legacy host code parsed. It exists to test host software
(SDK, compiler output, studio) without hardware.

What it is NOT: a model of the real firmware. In particular:

* replies, status codes and their timing are INFERRED from the legacy host
  parser; the real robot may answer differently or not at all;
* robot-side motion files (``.act``) are opaque - the simulator stores their
  bytes but cannot execute them; "playing" lasts ``motion_duration_s``;
* servo motion is linear interpolation over the S_JOG play time; dynamics,
  load, speed mode and the STOP flag semantics are assumptions.

Every simulated behaviour that rests on an assumption is listed in
``ASSUMPTIONS`` and surfaced by the CLI and studio.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from genibo.clock import Clock, RealClock
from genibo.protocol.constants import (
    BROADCAST_ID,
    BUS_REPLY_MPSU_RAM,
    FILENAME_FIELD,
    MPSU_STOP_INDEX,
    BusCommand,
    JogFlag,
    LinkCommand,
    ServoRegister,
    StatusCode,
    Storage,
    Torque,
)
from genibo.protocol.convert import ticks_to_ms
from genibo.protocol.frames import BusPacket, DecodeIssue, LinkFrame, StreamDecoder

ASSUMPTIONS: tuple[str, ...] = (
    "Reply layouts and STATUS (0xF0) acknowledgements are inferred from the legacy host parser.",
    "Commands without a known reply get a STATUS acknowledgement in the simulator only.",
    "Stored motion/task files are opaque; playback lasts a fixed simulated duration.",
    "Servos move linearly over the S_JOG play time; no dynamics, no load, no collisions.",
    "Servos start with torque ON at raw position 512; torque-free servos ignore moves.",
    "Speed-mode S_JOG entries are recorded but do not move the simulated servo.",
    "The STOP flag freezes a servo at its current simulated position.",
)


@dataclass
class ServoState:
    servo_id: int
    start_raw: float = 512.0
    target_raw: float = 512.0
    t_start: float = 0.0
    duration: float = 0.0
    torque: int = int(Torque.ON)
    flags: int = 0
    mode: str = "position"

    def position(self, t: float) -> float:
        if self.duration <= 0 or t >= self.t_start + self.duration:
            return self.target_raw
        if t <= self.t_start:
            return self.start_raw
        a = (t - self.t_start) / self.duration
        return self.start_raw + (self.target_raw - self.start_raw) * a

    @property
    def led(self) -> str:
        names = [
            n
            for f, n in ((JogFlag.LED_GREEN, "green"), (JogFlag.LED_BLUE, "blue"), (JogFlag.LED_RED, "red"))
            if self.flags & f
        ]
        return "+".join(names) or "off"


@dataclass(frozen=True)
class SimEvent:
    t: float
    kind: str
    text: str


@dataclass
class VirtualGenibo:
    robot_id: int = 0x20
    clock: Clock = field(default_factory=RealClock)
    servo_ids: tuple[int, ...] = tuple(range(0, 21))
    battery_percent: tuple[int, int] = (87, 64)
    motion_duration_s: float = 2.0
    credentials: tuple[str, str] | None = None
    mpsu_id: int = 0x40
    require_torque: bool = True
    remocon_reply: tuple[int, int] = (0, 0)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._decoder = StreamDecoder()
        self.servos: dict[int, ServoState] = {sid: ServoState(sid) for sid in self.servo_ids}
        self.files: dict[tuple[int, str], bytes] = {}
        self.events: list[SimEvent] = []
        self.playing_until: float = -1.0
        self.playing_name: str = ""
        self.time_offset: float = datetime.now(UTC).timestamp() - self.clock.now()
        self.mpsu_ram = bytearray(256)
        self.mpsu_ram[70:77] = bytes((0x0C, 0x80, self.battery_percent[0], 0, 0, 0, 0))
        self.head_led = 0
        self.buzz = 0
        self.rx_frames = 0

    # ------------------------------------------------------------------ helpers
    def _log(self, kind: str, text: str) -> None:
        self.events.append(SimEvent(self.clock.now(), kind, text))

    def _status(self, command: int, code: StatusCode) -> bytes:
        return LinkFrame(self.robot_id, LinkCommand.STATUS, bytes((command, int(code)))).encode()

    def _reply(self, command: int, payload: bytes = b"") -> bytes:
        return LinkFrame(self.robot_id, command, payload).encode()

    def add_file(self, storage: Storage | int, name: str, content: bytes) -> None:
        with self._lock:
            self.files[(int(storage), name)] = bytes(content)

    @property
    def is_playing(self) -> bool:
        return self.clock.now() < self.playing_until

    def snapshot(self, t: float | None = None) -> dict[int, float]:
        """Raw positions of all servos at time *t* (default: now)."""
        with self._lock:
            now = self.clock.now() if t is None else t
            return {sid: s.position(now) for sid, s in self.servos.items()}

    # ------------------------------------------------------------------ input
    def receive(self, data: bytes) -> bytes:
        """Feed host -> robot bytes; return robot -> host bytes."""
        out = bytearray()
        with self._lock:
            for item in self._decoder.feed(data):
                if isinstance(item, DecodeIssue):
                    self._log("discard", f"{len(item.discarded)} bytes: {item.reason}")
                    continue
                self.rx_frames += 1
                if isinstance(item, BusPacket):
                    out += self._bus(item, direct=True)
                else:
                    out += self._link(item)
        return bytes(out)

    def _link(self, f: LinkFrame) -> bytes:
        if f.robot_id not in (self.robot_id, BROADCAST_ID):
            self._log("ignored", f"frame for robot 0x{f.robot_id:02x}")
            return b""
        c, p = f.command, f.payload
        if c == LinkCommand.HEARTBEAT:
            return self._reply(LinkCommand.HEARTBEAT)
        if c == LinkCommand.BATTERY:
            return self._reply(c, bytes(self.battery_percent))
        if c == LinkCommand.REMOCON:
            return self._reply(c, bytes(self.remocon_reply))
        if c == LinkCommand.GET_TIME:
            now = self.clock.now() + self.time_offset
            sec = int(now)
            usec = round((now - sec) * 1_000_000) % 1_000_000
            return self._reply(c, sec.to_bytes(4, "big") + usec.to_bytes(4, "big"))
        if c == LinkCommand.SET_TIME and len(p) == 8:
            sec = int.from_bytes(p[:4], "big") + int.from_bytes(p[4:], "big") / 1e6
            self.time_offset = sec - self.clock.now()
            self._log("time", f"robot clock set to unix {sec:.6f}")
            return self._status(c, StatusCode.RECEIVE_OK)
        if c == LinkCommand.IS_PLAYING:
            return self._reply(c, bytes((1 if self.is_playing else 0,)))
        if c == LinkCommand.STOP:
            if self.is_playing:
                self._log("playback", f"stopped {self.playing_name!r}")
            self.playing_until = -1.0
            return self._status(c, StatusCode.RECEIVE_OK)
        if c == LinkCommand.PLAY and len(p) in (52, 60):
            storage = p[0]
            name = p[1 : 1 + FILENAME_FIELD].split(b"\x00", 1)[0].decode("latin-1")
            kind = "motion" if len(p) == 60 else "task"
            if (storage, name) not in self.files:
                self._log("playback", f"{kind} {name!r} not found in {'master' if storage == 0 else 'user'}")
                return self._status(c, StatusCode.PROCESS_FAILED)
            self.playing_until = self.clock.now() + self.motion_duration_s
            self.playing_name = name
            when = ""
            if kind == "motion" and p[52:60] != bytes(8):
                when = f" scheduled at unix {int.from_bytes(p[52:56], 'big')} (start time not simulated)"
            self._log(
                "playback", f"{kind} {name!r} playing for {self.motion_duration_s:.1f}s (simulated){when}"
            )
            return self._status(c, StatusCode.RECEIVE_OK)
        if c == LinkCommand.FILE_LIST and len(p) == 1:
            names = sorted(n for (s, n) in self.files if s == p[0])
            body = bytearray((p[0],)) + len(names).to_bytes(2, "big")
            for n in names:
                body += n.encode("latin-1")[: FILENAME_FIELD - 1].ljust(FILENAME_FIELD, b"\x00")
            return self._reply(c, bytes(body))
        if c == LinkCommand.FILE_SEND and len(p) >= 56:
            name = p[5:56].split(b"\x00", 1)[0].decode("latin-1")
            self.files[(p[0], name)] = bytes(p[56:])
            self._log("file", f"stored {name!r} ({len(p) - 56} bytes) in {'master' if p[0] == 0 else 'user'}")
            return self._status(c, StatusCode.RECEIVE_OK)
        if c in (LinkCommand.FILE_FETCH, LinkCommand.FILE_DELETE) and len(p) == 52:
            name = p[1:].split(b"\x00", 1)[0].decode("latin-1")
            key = (p[0], name)
            if key not in self.files:
                return self._status(c, StatusCode.PROCESS_FAILED)
            if c == LinkCommand.FILE_DELETE:
                del self.files[key]
                self._log("file", f"deleted {name!r}")
                return self._status(c, StatusCode.RECEIVE_OK)
            return self._reply(c, p[:1] + p[1:52] + self.files[key])
        if c == LinkCommand.FILE_DELETE_ALL and len(p) == 1:
            for key in [k for k in self.files if k[0] == p[0]]:
                del self.files[key]
            self._log("file", f"deleted all files in {'master' if p[0] == 0 else 'user'}")
            return self._status(c, StatusCode.RECEIVE_OK)
        if c == LinkCommand.CHARGE:
            self._log("charge", "charge command received (effect unknown)")
            return self._status(c, StatusCode.RECEIVE_OK)
        if c == LinkCommand.AUTH and len(p) == 42:
            user = p[:21].split(b"\x00", 1)[0].decode("latin-1")
            pw = p[21:].split(b"\x00", 1)[0].decode("latin-1")
            ok = self.credentials is None or self.credentials == (user, pw)
            return self._reply(c, bytes((1 if ok else 0,)))
        if c == LinkCommand.SERVO_BUS:
            try:
                return self._bus(BusPacket.decode(p), direct=False)
            except ValueError as exc:
                self._log("error", f"bad tunnelled packet: {exc}")
                return self._status(c, StatusCode.CHECKSUM_ERROR)
        self._log("unknown", f"link command 0x{c:02x} ({len(p)} bytes)")
        return self._status(c, StatusCode.PROCESS_FAILED)

    def _targets(self, device_id: int) -> list[ServoState]:
        if device_id == BROADCAST_ID:
            return list(self.servos.values())
        s = self.servos.get(device_id)
        return [s] if s else []

    def _bus(self, pkt: BusPacket, *, direct: bool) -> bytes:
        c, d = pkt.command, pkt.data
        now = self.clock.now()
        if c in (BusCommand.S_JOG, BusCommand.MPSU_MOTOR_MOVE):
            if not d:
                self._log("servo", "empty S_JOG (what legacy reset() sends)")
                return b""
            duration = ticks_to_ms(d[0]) / 1000.0
            body = d[1:]
            for i in range(len(body) // 4):
                lo, hi, flags, sid = body[i * 4 : i * 4 + 4]
                raw = lo | (hi << 8)
                s = self.servos.get(sid)
                if s is None:
                    self._log("servo", f"S_JOG for unknown servo id {sid}")
                    continue
                if flags & JogFlag.NO_ACTION:
                    continue
                s.flags = flags
                if flags & JogFlag.STOP:
                    pos = s.position(now)
                    s.start_raw = s.target_raw = pos
                    s.duration = 0.0
                    self._log("servo", f"id {sid} stop at {pos:.1f}")
                    continue
                if flags & JogFlag.SPEED_MODE:
                    s.mode = "speed"
                    self._log("servo", f"id {sid} speed-mode value {raw} (not simulated)")
                    continue
                if self.require_torque and s.torque != Torque.ON:
                    self._log("servo", f"id {sid} ignored move to {raw}: torque is 0x{s.torque:02x}")
                    continue
                s.mode = "position"
                s.start_raw = s.position(now)
                s.target_raw = float(raw)
                s.t_start = now
                s.duration = duration
            self._log("servo", f"S_JOG {len(body) // 4} servo(s) over {duration * 1000:.0f} ms")
            return b""
        if c == BusCommand.RAM_WRITE and len(d) >= 3:
            if d[0] == ServoRegister.TORQUE_CONTROL:
                for s in self._targets(pkt.device_id):
                    pos = s.position(now)
                    s.start_raw = s.target_raw = pos
                    s.duration = 0.0
                    s.torque = d[2]
                who = "all servos" if pkt.device_id == BROADCAST_ID else f"servo {pkt.device_id}"
                self._log("torque", f"{who} -> 0x{d[2]:02x}")
            else:
                self._log("servo", f"RAM write addr {d[0]} on 0x{pkt.device_id:02x} (not simulated)")
            return b""
        if c == BusCommand.MPSU_HEADLED_BUZZ and len(d) >= 2:
            self.head_led, self.buzz = d[0], d[1]
            self._log("mpsu", f"head LED {d[0]} buzz {d[1]}")
            return b""
        if c == BusCommand.MPSU_PLAY_MOTION and len(d) >= 2:
            if d[0] == MPSU_STOP_INDEX:
                self.playing_until = -1.0
                self._log("mpsu", "motion stop")
            else:
                self.playing_until = now + self.motion_duration_s
                self.playing_name = f"mpsu#{d[0]}"
                self._log("mpsu", f"MPSU motion {d[0]} (content unknown; simulated duration)")
            return b""
        if c == BusCommand.MPSU_PLAY_TASK and d:
            self._log("mpsu", "task stop" if d[0] == MPSU_STOP_INDEX else f"MPSU task {d[0]}")
            return b""
        if c in (BusCommand.MPSU_RAM_WRITE, BusCommand.MPSU_ROM_WRITE) and len(d) >= 2:
            if c == BusCommand.MPSU_RAM_WRITE:
                self.mpsu_ram[d[0] : d[0] + d[1]] = d[2 : 2 + d[1]]
            self._log(
                "mpsu", f"{'ROM' if c == BusCommand.MPSU_ROM_WRITE else 'RAM'} write @{d[0]} len {d[1]}"
            )
            return b""
        if c == BusCommand.MPSU_RAM_READ and len(d) == 2 and direct:
            addr, n = d
            data = bytes(self.mpsu_ram[addr : addr + n])
            return BusPacket(self.mpsu_id, BUS_REPLY_MPSU_RAM, bytes((addr, n)) + data + b"\x00\x00").encode()
        self._log("unknown", f"bus command 0x{c:02x} to 0x{pkt.device_id:02x}")
        return b""
