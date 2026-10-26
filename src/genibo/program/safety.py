"""Safety analysis of a command plan, derived from the frames that will actually be sent.

The plan's ``steps`` and ``joints`` sections are descriptive metadata and can
disagree with its frames (an edited or hand-made plan). Hardware gates must
therefore look at the frames: which servos receive motion commands, and which
commands change persistent state or are not understood.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from genibo.errors import FrameError
from genibo.program.plan import CommandPlan
from genibo.protocol.constants import BROADCAST_ID, BusCommand, JogFlag, LinkCommand, ServoRegister
from genibo.protocol.frames import BusPacket, LinkFrame

#: Link commands a plan may contain without being flagged.
_SAFE_LINK = {
    LinkCommand.PLAY,
    LinkCommand.IS_PLAYING,
    LinkCommand.STOP,
    LinkCommand.SET_TIME,
    LinkCommand.GET_TIME,
    LinkCommand.BATTERY,
    LinkCommand.REMOCON,
    LinkCommand.HEARTBEAT,
    LinkCommand.FILE_LIST,
    LinkCommand.FILE_FETCH,
}
_HAZARD_LINK = {
    LinkCommand.FILE_SEND: "stores a file on the robot",
    LinkCommand.FILE_DELETE: "deletes a file on the robot",
    LinkCommand.FILE_DELETE_ALL: "deletes ALL files in a robot storage area",
    LinkCommand.CHARGE: "charge command (effect undocumented)",
    LinkCommand.AUTH: "sends credentials",
}
_HAZARD_BUS = {
    BusCommand.MPSU_ROM_WRITE: "writes MPSU ROM (persistent controller settings)",
    BusCommand.MPSU_RAM_WRITE: "writes MPSU RAM (controller settings, e.g. its ID)",
    BusCommand.MPSU_REBOOT: "reboots the MPSU controller",
    BusCommand.MPSU_SERVO_FW_UPDATE_MODE: "puts servos into firmware-update mode",
    BusCommand.REBOOT_LEGACY: "servo reboot",
}
_SAFE_BUS = {
    BusCommand.S_JOG,
    BusCommand.MPSU_MOTOR_MOVE,
    BusCommand.MPSU_PLAY_MOTION,
    BusCommand.MPSU_PLAY_TASK,
    BusCommand.MPSU_HEADLED_BUZZ,
    BusCommand.MPSU_RAM_READ,
    BusCommand.MPSU_ROM_READ,
}


@dataclass
class FrameAnalysis:
    moved_servos: set[int] = field(default_factory=set)
    torque_targets: set[int] = field(default_factory=set)  # 0xFE = every servo
    hazards: list[str] = field(default_factory=list)

    def unverified_servos(self, joints_table: Mapping[str, Mapping[str, Any]]) -> list[str]:
        """Servos moved by the frames that are not a verified joint in *joints_table*.

        ``joints_table`` is a ``genibo.toml``-style table (``JointMap.to_config()``):
        name -> {"servo_id": .., "verified": ..}. Unknown servo IDs count as unverified.
        """
        verified_ids = {int(j["servo_id"]) for j in joints_table.values() if j.get("verified")}
        names = {int(j["servo_id"]): n for n, j in joints_table.items()}
        return [names.get(sid, f"servo {sid}") for sid in sorted(self.moved_servos - verified_ids)]


def _bus(pkt: BusPacket, where: str, out: FrameAnalysis) -> None:
    c, d = pkt.command, pkt.data
    if c in (BusCommand.S_JOG, BusCommand.MPSU_MOTOR_MOVE):
        body = d[1:] if d else b""
        for i in range(len(body) // 4):
            _lo, _hi, flags, sid = body[i * 4 : i * 4 + 4]
            if flags & JogFlag.NO_ACTION:
                continue
            out.moved_servos.add(sid)
            if flags & JogFlag.SPEED_MODE:
                out.hazards.append(f"{where}: speed-mode (continuous rotation) command for servo {sid}")
        return
    if c == BusCommand.RAM_WRITE:
        if len(d) >= 3 and d[0] == ServoRegister.TORQUE_CONTROL:
            out.torque_targets.add(pkt.device_id)
        else:
            reg = d[0] if d else -1
            out.hazards.append(f"{where}: servo RAM write to register {reg} on 0x{pkt.device_id:02x}")
        return
    if c in _HAZARD_BUS:
        out.hazards.append(f"{where}: {_HAZARD_BUS[BusCommand(c)]} (0x{pkt.device_id:02x})")
        return
    if c not in _SAFE_BUS:
        out.hazards.append(f"{where}: unknown bus command 0x{c:02x} to 0x{pkt.device_id:02x}")


def analyze_frames(frames: Iterable[bytes]) -> FrameAnalysis:
    out = FrameAnalysis()
    for i, data in enumerate(frames):
        where = f"frame {i}"
        try:
            if data[:2] == b"\xff\xff":
                _bus(BusPacket.decode(data), where, out)
                continue
            frame = LinkFrame.decode(data)
        except FrameError as exc:
            out.hazards.append(f"{where}: undecodable ({exc})")
            continue
        c = frame.command
        if c == LinkCommand.SERVO_BUS:
            try:
                _bus(BusPacket.decode(frame.payload), where, out)
            except FrameError as exc:
                out.hazards.append(f"{where}: invalid tunnelled packet ({exc})")
        elif c in _HAZARD_LINK:
            out.hazards.append(f"{where}: {_HAZARD_LINK[LinkCommand(c)]}")
        elif c not in _SAFE_LINK:
            out.hazards.append(f"{where}: unknown link command 0x{c:02x}")
    return out


def analyze_plan(plan: CommandPlan) -> FrameAnalysis:
    return analyze_frames(f.data for f in plan.frames)


__all__ = ["BROADCAST_ID", "FrameAnalysis", "analyze_frames", "analyze_plan"]
