"""Compile a Python-authored program into a command plan.

Pipeline::

    source --static check--> AST ok? --run @program with a recorder--> IR (timed steps + source lines)
           --validate + lower each step with the golden-tested encoders--> frames
           --verify (decode every frame again)--> CommandPlan

Errors are reported as :class:`~genibo.errors.Diagnostic` objects pointing at
the user's source line; nothing is silently dropped or clamped.
"""

from __future__ import annotations

import hashlib
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from genibo import __version__
from genibo.errors import Diagnostic, EncodingError, FrameError, JointError, SourceLocation
from genibo.joints import JointMap
from genibo.program.builder import Program, ProgramLimitError, ProgramSpec
from genibo.program.ir import (
    HeadLedBuzz,
    Mark,
    MpsuMotion,
    MpsuStop,
    PlayFile,
    Pose,
    ProgramIR,
    RawFrame,
    Step,
    StopPlayback,
    TorqueStep,
    Wait,
)
from genibo.program.plan import CommandPlan, PlannedFrame
from genibo.program.static_check import check_source
from genibo.protocol import commands as cmd
from genibo.protocol.constants import BROADCAST_ID, MAX_BUS_PACKET, Torque
from genibo.protocol.convert import ms_to_ticks
from genibo.protocol.describe import describe
from genibo.protocol.frames import BusPacket, LinkFrame


@dataclass
class BuildResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    ir: ProgramIR | None = None
    plan: CommandPlan | None = None

    @property
    def ok(self) -> bool:
        return self.plan is not None and not any(d.severity == "error" for d in self.diagnostics)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]


def _user_location(tb: TracebackType | None, path: str) -> SourceLocation | None:
    target = Path(path).resolve()
    found = None
    for fs in traceback.extract_tb(tb):
        if Path(fs.filename).resolve() == target:
            found = SourceLocation(path, fs.lineno or 1)
    return found


def _sorted_entries(joints: JointMap, entries: list[tuple[str, cmd.JogEntry]]) -> list[cmd.JogEntry]:
    order = {name: i for i, name in enumerate(joints)}
    return [e for _, e in sorted(entries, key=lambda ne: order.get(ne[0], 1 << 30))]


class _Lowering:
    def __init__(self, joints: JointMap, robot_id: int, encoding: str) -> None:
        self.joints = joints
        self.robot_id = robot_id
        self.encoding = encoding
        self.diags: list[Diagnostic] = []
        self.frames: list[PlannedFrame] = []
        self._busy_until: dict[str, float] = {}
        self._torque_seen = False
        self._warned_unverified: set[str] = set()

    def diag(self, sev: str, code: str, msg: str, step: Step, hint: str = "") -> None:
        self.diags.append(Diagnostic(sev, code, msg, step.location, hint))

    def emit(self, step: Step, frame: LinkFrame | BusPacket) -> None:
        line = step.location.line if step.location else None
        self.frames.append(
            PlannedFrame(
                step.at_ms,
                frame.encode(),
                step.index,
                line,
                f"{step.describe()}  <{describe(frame).summary}>",
            )
        )

    def lower(self, step: Step) -> None:
        b = step.body
        try:
            if isinstance(b, Pose):
                self._pose(step, b)
            elif isinstance(b, Wait):
                if isinstance(b.ms, bool) or not isinstance(b.ms, int) or b.ms < 0:
                    self.diag(
                        "error", "G130", f"wait() needs a non-negative integer of ms, got {b.ms!r}", step
                    )
            elif isinstance(b, TorqueStep):
                device = BROADCAST_ID if b.joint is None else self.joints[b.joint].servo_id
                self._torque_seen = self._torque_seen or (b.on and b.joint is None)
                self.emit(
                    step, cmd.tunnel(self.robot_id, cmd.torque(device, Torque.ON if b.on else Torque.FREE))
                )
            elif isinstance(b, PlayFile):
                if b.file_kind == "motion":
                    frame = cmd.play_motion(self.robot_id, b.storage, b.name, encoding=self.encoding)
                else:
                    frame = cmd.play_task(self.robot_id, b.storage, b.name, encoding=self.encoding)
                if not b.wait_ms:
                    self.diag(
                        "warning",
                        "G303",
                        f"{b.file_kind} {b.name!r} plays on the robot for an unknown time; the next step follows "
                        "immediately",
                        step,
                        "pass wait_ms= with the expected duration",
                    )
                self.emit(step, frame)
            elif isinstance(b, StopPlayback):
                self.emit(step, cmd.stop_playback(self.robot_id))
            elif isinstance(b, HeadLedBuzz):
                self.emit(step, cmd.tunnel(self.robot_id, cmd.mpsu_headled_buzz(b.mpsu_id, b.led, b.buzz)))
            elif isinstance(b, MpsuMotion):
                if not b.wait_ms:
                    self.diag(
                        "warning", "G303", f"MPSU motion #{b.index} duration is unknown; pass wait_ms=", step
                    )
                self.emit(step, cmd.tunnel(self.robot_id, cmd.mpsu_play_motion(b.mpsu_id, b.index, b.ready)))
            elif isinstance(b, MpsuStop):
                self.emit(step, cmd.tunnel(self.robot_id, cmd.mpsu_stop_motion(b.mpsu_id)))
            elif isinstance(b, RawFrame):
                self.diag("warning", "G304", f"raw frame 0x{b.command:02x} is sent unvalidated", step)
                self.emit(step, cmd.raw(self.robot_id, b.command, b.payload))
            elif isinstance(b, Mark):
                pass
        except (JointError, EncodingError, FrameError, ValueError, TypeError) as exc:
            self.diag("error", "G140", str(exc), step)

    def _pose(self, step: Step, b: Pose) -> None:
        if not b.targets:
            self.diag("error", "G131", "pose() needs at least one joint target", step)
            return
        if isinstance(b.ms, bool) or not isinstance(b.ms, int):
            self.diag("error", "G132", f"pose ms must be an integer, got {b.ms!r}", step)
            return
        ticks = ms_to_ticks(b.ms)  # raises EncodingError beyond 255 ticks
        if ticks == 0:
            self.diag("warning", "G306", f"play time {b.ms} ms rounds to 0 ticks (instant move)", step)
        entries: list[tuple[str, cmd.JogEntry]] = []
        seen: set[str] = set()
        duplicate = False
        for name, value in b.targets:
            if name in seen:
                self.diag("error", "G133", f"joint {name!r} given twice", step)
                duplicate = True
                continue
            seen.add(name)
            joint = self.joints[name]
            if b.raw:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise JointError(f"raw target for {name!r} must be an int, got {value!r}")
                raw = joint.check_raw(value)
            else:
                raw = joint.to_raw(float(value), clip=b.clip)
                if not joint.verified and name not in self._warned_unverified:
                    self._warned_unverified.add(name)
                    self.diag(
                        "warning",
                        "G301",
                        f"joint {name!r}: servo ID/calibration not verified on hardware",
                        step,
                        "confirm with small moves, then set verified = true in genibo.toml",
                    )
            if self._busy_until.get(name, -1.0) > step.at_ms + 1e-6:
                self.diag(
                    "warning",
                    "G302",
                    f"joint {name!r} is still moving from an earlier pose(wait=False)",
                    step,
                )
            entries.append((name, cmd.JogEntry(raw, b.led, joint.servo_id)))
        if duplicate:
            return
        if 8 + 4 * len(entries) > MAX_BUS_PACKET:
            self.diag("error", "G134", f"{len(entries)} joints do not fit in one S_JOG packet", step)
            return
        if not self._torque_seen:
            self._torque_seen = True
            self.diag(
                "info",
                "G305",
                "servo torque state at program start is unknown",
                step,
                "start the program with g.torque(True) if the servos may be limp",
            )
        for name in seen:
            self._busy_until[name] = step.at_ms + ticks * 112 / 10
        packet = cmd.s_jog(ticks, _sorted_entries(self.joints, entries))
        self.emit(step, cmd.tunnel(self.robot_id, packet))


#: Wall-clock budget for running a program at build time (protects the studio's live validation).
DEFAULT_TIME_LIMIT_S = 10.0


@contextmanager
def _time_budget(seconds: float) -> Iterator[None]:
    """Abort the traced code with ProgramLimitError once *seconds* have elapsed.

    Uses a line tracer, because a loop that records no steps (``for i in
    range(10**12): pass``) never reaches the step cap.
    """
    deadline = time.monotonic() + seconds
    count = 0

    def local(frame, event, arg):  # type: ignore[no-untyped-def]
        nonlocal count
        count += 1
        if count & 0x3FF == 0 and time.monotonic() > deadline:
            raise ProgramLimitError(
                f"program ran longer than {seconds:g} s at build time (infinite or very long loop?)"
            )
        return local

    previous = sys.gettrace()
    sys.settrace(lambda frame, event, arg: local)
    try:
        yield
    finally:
        sys.settrace(previous)


def run_program(
    source: str, path: str, *, entry: str | None = None, time_limit_s: float = DEFAULT_TIME_LIMIT_S
) -> tuple[ProgramIR | None, list[Diagnostic]]:
    """Static check, then execute the module and the selected @program function."""
    tree, diags = check_source(source, path)
    if tree is None or any(d.severity == "error" for d in diags):
        return None, diags
    namespace: dict[str, object] = {"__name__": "__genibo_program__", "__file__": path}
    try:
        with _time_budget(time_limit_s):
            exec(compile(tree, path, "exec"), namespace)
    except ProgramLimitError as exc:
        diags.append(Diagnostic("error", "G122", str(exc), _user_location(exc.__traceback__, path)))
        return None, diags
    except Exception as exc:
        diags.append(
            Diagnostic(
                "error",
                "G120",
                f"module failed at build time: {type(exc).__name__}: {exc}",
                _user_location(exc.__traceback__, path),
            )
        )
        return None, diags
    specs: list[ProgramSpec] = [
        getattr(v, "__genibo_program__")  # noqa: B009 - attribute set by @program
        for v in namespace.values()
        if callable(v) and isinstance(getattr(v, "__genibo_program__", None), ProgramSpec)
    ]
    if entry is not None:
        specs = [s for s in specs if s.name == entry or s.func.__name__ == entry]
        if not specs:
            diags.append(Diagnostic("error", "G111", f"no @program named {entry!r}", SourceLocation(path, 1)))
            return None, diags
    if len(specs) > 1:
        names = ", ".join(s.name for s in specs)
        diags.append(
            Diagnostic(
                "error",
                "G112",
                f"several @program functions ({names}); set [project] program",
                SourceLocation(path, 1),
            )
        )
        return None, diags
    if not specs:
        diags.append(Diagnostic("error", "G110", "no @program function found", SourceLocation(path, 1)))
        return None, diags
    spec = specs[0]
    g = Program(spec.name, source_file=path)
    try:
        with _time_budget(time_limit_s):
            spec.func(g)
    except ProgramLimitError as exc:
        diags.append(Diagnostic("error", "G122", str(exc), _user_location(exc.__traceback__, path)))
        return None, diags
    except Exception as exc:
        diags.append(
            Diagnostic(
                "error", "G121", f"{type(exc).__name__}: {exc}", _user_location(exc.__traceback__, path)
            )
        )
        return None, diags
    if not g.ir.steps:
        diags.append(Diagnostic("warning", "G203", "program records no steps", SourceLocation(path, 1)))
    return g.ir, diags


def compile_source(
    source: str,
    path: str,
    *,
    joints: JointMap,
    robot_id: int,
    encoding: str = cmd.DEFAULT_ENCODING,
    entry: str | None = None,
    display_path: str | None = None,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
) -> BuildResult:
    ir, diags = run_program(source, path, entry=entry, time_limit_s=time_limit_s)
    result = BuildResult(diags, ir)
    if ir is None:
        return result
    low = _Lowering(joints, robot_id, encoding)
    for step in ir.steps:
        low.lower(step)
    result.diagnostics.extend(low.diags)
    if any(d.severity == "error" for d in result.diagnostics):
        return result
    plan = CommandPlan(
        name=ir.name,
        robot_id=robot_id,
        frames=low.frames,
        steps=[s.to_dict(file_names={path: display_path or Path(path).name}) for s in ir.steps],
        duration_ms=round(ir.duration_ms, 1),
        source_file=display_path or Path(path).name,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        sdk_version=__version__,
        encoding=encoding,
        joints={"profile": joints.profile, "all_verified": joints.all_verified, "table": joints.to_config()},
        diagnostics=[
            {
                "severity": d.severity,
                "code": d.code,
                "message": d.message,
                "line": d.location.line if d.location else None,
            }
            for d in result.diagnostics
        ],
    )
    for problem in plan.verify():
        result.diagnostics.append(Diagnostic("error", "G150", f"artifact check failed: {problem}"))
    if any(d.severity == "error" for d in result.diagnostics):
        return result
    result.plan = plan
    return result


def compile_file(path: Path, **kw: object) -> BuildResult:
    source = Path(path).read_text(encoding="utf-8")
    return compile_source(source, str(path), **kw)  # type: ignore[arg-type]
