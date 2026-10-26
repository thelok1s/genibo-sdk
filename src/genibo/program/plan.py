"""Command plan: the build artifact of a compiled program.

A plan is the exact, timed list of frames the host will send. The robot never
stores or interprets the plan; it only ever sees the frames. The container is
a host-side JSON format; the frames inside are byte-identical to what the
legacy tool would send for the same commands (golden-tested encoders).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from genibo.errors import FrameError
from genibo.protocol.constants import LinkCommand
from genibo.protocol.describe import describe
from genibo.protocol.frames import BusPacket, LinkFrame

PLAN_FORMAT = "genibo-command-plan"
PLAN_VERSION = 1

PLAN_NOTES = (
    "The host sends each frame at t_ms after start; the robot does not store or run this plan.",
    "Frames are encoded by the SDK encoders that are byte-compared with the legacy C# implementation.",
    "Robot-side motion/task files referenced by name must already exist on the robot.",
)


@dataclass(frozen=True)
class PlannedFrame:
    t_ms: float
    data: bytes
    step: int
    line: int | None
    summary: str

    def decode(self) -> LinkFrame | BusPacket:
        if self.data[:2] == b"\xff\xff":
            return BusPacket.decode(self.data)
        return LinkFrame.decode(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "t_ms": self.t_ms,
            "hex": self.data.hex(),
            "step": self.step,
            "line": self.line,
            "summary": self.summary,
        }


@dataclass
class CommandPlan:
    name: str
    robot_id: int
    frames: list[PlannedFrame]
    steps: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    source_file: str = ""
    source_sha256: str = ""
    sdk_version: str = ""
    encoding: str = "ascii"
    joints: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ integrity
    def frames_digest(self) -> str:
        h = hashlib.sha256()
        for f in self.frames:
            h.update(f"{f.t_ms:.1f}:".encode() + f.data)
        return h.hexdigest()

    def verify(self) -> list[str]:
        """Re-decode every frame and check plan-level consistency. Returns problems found."""
        problems: list[str] = []
        last = -1.0
        for i, f in enumerate(self.frames):
            try:
                frame = f.decode()
            except FrameError as exc:
                problems.append(f"frame {i}: {exc}")
                continue
            if frame.encode() != f.data:
                problems.append(f"frame {i}: does not round-trip")
            if isinstance(frame, LinkFrame) and frame.robot_id not in (self.robot_id, 0xFE):
                problems.append(
                    f"frame {i}: addressed to robot 0x{frame.robot_id:02x}, plan targets 0x{self.robot_id:02x}"
                )
            if isinstance(frame, LinkFrame) and frame.command == LinkCommand.SERVO_BUS:
                try:
                    BusPacket.decode(frame.payload)
                except FrameError as exc:
                    problems.append(f"frame {i}: tunnelled packet invalid: {exc}")
            if f.t_ms < last:
                problems.append(f"frame {i}: scheduled before the previous frame")
            last = f.t_ms
        return problems

    # ------------------------------------------------------------ serialisation
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PLAN_FORMAT,
            "format_version": PLAN_VERSION,
            "name": self.name,
            "sdk_version": self.sdk_version,
            "source": {"file": self.source_file, "sha256": self.source_sha256},
            "target": {"link": "bluetooth-serial", "robot_id": self.robot_id, "encoding": self.encoding},
            "joints": self.joints,
            "duration_ms": self.duration_ms,
            "frames_sha256": self.frames_digest(),
            "notes": list(PLAN_NOTES),
            "diagnostics": self.diagnostics,
            "steps": self.steps,
            "frames": [f.to_dict() for f in self.frames],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False, ensure_ascii=False) + "\n"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CommandPlan:
        if d.get("format") != PLAN_FORMAT:
            raise ValueError("not a genibo command plan")
        if d.get("format_version") != PLAN_VERSION:
            raise ValueError(f"unsupported plan version {d.get('format_version')}")
        frames = [
            PlannedFrame(
                float(f["t_ms"]), bytes.fromhex(f["hex"]), int(f["step"]), f.get("line"), f.get("summary", "")
            )
            for f in d["frames"]
        ]
        plan = cls(
            name=d["name"],
            robot_id=int(d["target"]["robot_id"]),
            frames=frames,
            steps=d.get("steps", []),
            duration_ms=float(d.get("duration_ms", 0.0)),
            source_file=d.get("source", {}).get("file", ""),
            source_sha256=d.get("source", {}).get("sha256", ""),
            sdk_version=d.get("sdk_version", ""),
            encoding=d["target"].get("encoding", "ascii"),
            joints=d.get("joints", {}),
            diagnostics=d.get("diagnostics", []),
        )
        expected = d.get("frames_sha256")
        if expected and expected != plan.frames_digest():
            raise ValueError("plan frames were modified (frames_sha256 mismatch)")
        return plan

    @classmethod
    def load(cls, path: Path) -> CommandPlan:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------ human-readable listing
    def listing(self, *, detail: bool = False) -> str:
        out = [
            f"; plan {self.name!r}  robot 0x{self.robot_id:02x}  {len(self.frames)} frames  {self.duration_ms:.1f} ms",
            f"; source {self.source_file} sha256 {self.source_sha256[:12]}  joints {self.joints.get('profile', '?')}",
        ]
        for f in self.frames:
            src = f"line {f.line}" if f.line else ""
            out.append(f"{f.t_ms:9.1f} ms  {src:>9}  {f.summary}")
            out.append(f"{'':12}  {'':9}  {f.data.hex(' ')}")
            if detail:
                out.extend("              " + ln for ln in describe(f.decode()).lines())
        return "\n".join(out) + "\n"
