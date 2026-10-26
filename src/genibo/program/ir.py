"""Intermediate representation of a compiled Genibo program.

A program is a flat, timed list of steps. Each step records where in the user's
source it came from so diagnostics, the plan listing and the studio can point
back at the line that produced each frame.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from genibo.errors import SourceLocation


@dataclass(frozen=True)
class Pose:
    """Move joints to targets over ``ms`` (one S_JOG packet)."""

    targets: tuple[tuple[str, float], ...]
    ms: int
    raw: bool = False
    led: int = 0
    wait: bool = True
    clip: bool = False
    kind: str = "pose"


@dataclass(frozen=True)
class Wait:
    ms: int
    kind: str = "wait"


@dataclass(frozen=True)
class TorqueStep:
    on: bool
    joint: str | None = None
    kind: str = "torque"


@dataclass(frozen=True)
class PlayFile:
    """Play a file already stored on the robot (its content is opaque to the SDK)."""

    file_kind: str  # "motion" | "task"
    storage: int
    name: str
    wait_ms: int = 0
    kind: str = "play_file"


@dataclass(frozen=True)
class StopPlayback:
    kind: str = "stop_playback"


@dataclass(frozen=True)
class HeadLedBuzz:
    led: int
    buzz: int
    mpsu_id: int
    kind: str = "head_led_buzz"


@dataclass(frozen=True)
class MpsuMotion:
    index: int
    ready: bool
    mpsu_id: int
    wait_ms: int = 0
    kind: str = "mpsu_motion"


@dataclass(frozen=True)
class MpsuStop:
    mpsu_id: int
    kind: str = "mpsu_stop"


@dataclass(frozen=True)
class RawFrame:
    command: int
    payload: bytes
    kind: str = "raw"


@dataclass(frozen=True)
class Mark:
    label: str
    kind: str = "mark"


StepBody = (
    Pose | Wait | TorqueStep | PlayFile | StopPlayback | HeadLedBuzz | MpsuMotion | MpsuStop | RawFrame | Mark
)


@dataclass(frozen=True)
class Step:
    index: int
    at_ms: float  # scheduled start, relative to program start
    advance_ms: float  # how far this step moves the timeline
    body: StepBody
    location: SourceLocation | None = None

    def describe(self) -> str:
        b = self.body
        if isinstance(b, Pose):
            unit = "" if b.raw else "°"
            tg = ", ".join(f"{k}={v:g}{unit}" for k, v in b.targets)
            extra = "" if b.wait else " (no wait)"
            return f"pose {tg} in {b.ms} ms{extra}"
        if isinstance(b, Wait):
            return f"wait {b.ms} ms"
        if isinstance(b, TorqueStep):
            return f"torque {'on' if b.on else 'free'}" + (f" {b.joint}" if b.joint else " (all)")
        if isinstance(b, PlayFile):
            area = "master" if b.storage == 0 else "user"
            return f"play {b.file_kind} {b.name!r} ({area})" + (f", wait {b.wait_ms} ms" if b.wait_ms else "")
        if isinstance(b, StopPlayback):
            return "stop playback"
        if isinstance(b, HeadLedBuzz):
            return f"head LED {b.led}, buzz {b.buzz}"
        if isinstance(b, MpsuMotion):
            return f"MPSU motion #{b.index}" + (" (ready pose)" if b.ready else "")
        if isinstance(b, MpsuStop):
            return "MPSU stop"
        if isinstance(b, RawFrame):
            return f"raw frame cmd 0x{b.command:02x} ({len(b.payload)} bytes)"
        return f"mark {b.label!r}"

    def to_dict(self, *, file_names: dict[str, str] | None = None) -> dict[str, Any]:
        """Serialise for the plan artifact.

        ``file_names`` maps absolute source paths to the names stored in the plan
        (project-relative), so artifacts do not depend on where the project lives.
        """
        body = asdict(self.body)
        if isinstance(self.body, RawFrame):
            body["payload"] = self.body.payload.hex()
        if isinstance(self.body, Pose):
            body["targets"] = [[k, v] for k, v in self.body.targets]
        location = None
        if self.location is not None:
            file = (file_names or {}).get(self.location.file, self.location.file)
            location = {"file": file, "line": self.location.line}
        return {
            "index": self.index,
            "at_ms": self.at_ms,
            "advance_ms": self.advance_ms,
            "body": body,
            "location": location,
            "text": self.describe(),
        }


@dataclass
class ProgramIR:
    name: str
    steps: list[Step] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        end = 0.0
        for s in self.steps:
            end = max(end, s.at_ms + s.advance_ms)
        return end
