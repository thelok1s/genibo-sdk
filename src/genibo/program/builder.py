"""Authoring API for compiled Genibo programs.

A compiled program is a Python function decorated with :func:`program`. The
compiler calls it once, with a :class:`Program` recorder; every call records a
step. The resulting timeline is validated and lowered to the exact frames the
robot receives. Nothing in the function runs on the robot.

    from genibo.program import Program, program

    @program
    def wave(g: Program) -> None:
        g.torque(True)
        for _ in range(3):
            g.pose(head_pan=30, ms=400)
            g.pose(head_pan=-30, ms=400)
        g.play_motion("7.act", wait_ms=3000)

Because the function runs at build time, its control flow must be decidable
then: loops over ``range``/literals, arithmetic, helper functions and
conditions on those values are fine; anything that depends on the robot's
state at run time (sensors, replies, wall-clock time) belongs in a host script
(see ``docs/PROGRAMMING_MODEL.md``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, overload

from genibo.errors import SourceLocation
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
    StepBody,
    StopPlayback,
    TorqueStep,
    Wait,
)
from genibo.protocol.constants import BROADCAST_ID, Storage
from genibo.protocol.convert import ms_to_ticks_legacy
from genibo.robot import Led

#: Hard cap on recorded steps; protects the build from runaway loops.
MAX_STEPS = 20_000

_THIS_FILE = str(Path(__file__).resolve())


class ProgramLimitError(RuntimeError):
    pass


def _caller_location(source_file: str | None) -> SourceLocation | None:
    f = sys._getframe(2)
    while f is not None:
        fn = f.f_code.co_filename
        if source_file is not None and Path(fn).resolve() == Path(source_file).resolve():
            return SourceLocation(source_file, f.f_lineno)
        if source_file is None and Path(fn).resolve() != Path(_THIS_FILE):
            return SourceLocation(fn, f.f_lineno)
        f = f.f_back  # type: ignore[assignment]
    return None


def _storage(value: str | int | Storage) -> int:
    if isinstance(value, str):
        try:
            return int(Storage[value.upper()])
        except KeyError:
            raise ValueError(f"storage must be 'master' or 'user', got {value!r}") from None
    return int(value)


def _led(value: Led | str | int) -> int:
    if isinstance(value, str):
        total = 0
        for part in value.replace("|", "+").split("+"):
            part = part.strip().upper()
            if part in ("", "OFF"):
                continue
            try:
                total |= int(Led[part])
            except KeyError:
                raise ValueError(
                    f"unknown LED colour {part!r}; use green, blue, red, off or combinations"
                ) from None
        return total
    return int(value)


class Program:
    """Records the steps of a compiled program (the object passed as ``g``)."""

    def __init__(self, name: str, *, source_file: str | None = None) -> None:
        self._ir = ProgramIR(name)
        self._t = 0.0
        self._source_file = source_file

    # -------------------------------------------------------------- internals
    @property
    def ir(self) -> ProgramIR:
        return self._ir

    @property
    def now_ms(self) -> float:
        """Time (ms from program start) at which the next step will be sent."""
        return self._t

    def _add(self, body: StepBody, advance_ms: float) -> None:
        if len(self._ir.steps) >= MAX_STEPS:
            raise ProgramLimitError(f"program exceeds {MAX_STEPS} steps (runaway loop?)")
        loc = _caller_location(self._source_file)
        self._ir.steps.append(Step(len(self._ir.steps), round(self._t, 1), advance_ms, body, loc))
        self._t = round(self._t + advance_ms, 1)

    # -------------------------------------------------------------- motion
    def pose(
        self,
        targets: Mapping[str, float] | None = None,
        /,
        ms: int = 500,
        *,
        led: Led | str | int = Led.OFF,
        wait: bool = True,
        clip: bool = False,
        **joints: float,
    ) -> None:
        """Move joints to angles in degrees over ``ms`` (max ~2856 ms per pose).

        ``g.pose(head_pan=30, tail=-10, ms=400)`` or ``g.pose({"head_pan": 30}, ms=400)``.
        ``wait=False`` sends the next step immediately instead of after the move.
        ``clip=True`` reproduces the legacy silent clipping to joint limits.
        """
        merged = {**(targets or {}), **joints}
        self._pose(merged, ms, raw=False, led=led, wait=wait, clip=clip)

    def pose_raw(
        self,
        targets: Mapping[str, int] | None = None,
        /,
        ms: int = 500,
        *,
        led: Led | str | int = Led.OFF,
        wait: bool = True,
        **joints: int,
    ) -> None:
        """Like :meth:`pose` but with raw servo positions instead of degrees."""
        merged = {**(targets or {}), **joints}
        self._pose(merged, ms, raw=True, led=led, wait=wait, clip=False)

    def _pose(
        self,
        targets: Mapping[str, float],
        ms: int,
        *,
        raw: bool,
        led: Led | str | int,
        wait: bool,
        clip: bool,
    ) -> None:
        ticks = ms_to_ticks_legacy(ms) if isinstance(ms, int) and ms >= 0 else 0
        advance = ticks * 112 / 10 if wait else 0.0
        body = Pose(
            tuple((str(k), float(v) if not raw else v) for k, v in targets.items()),
            ms,
            raw,
            _led(led),
            wait,
            clip,
        )
        self._add(body, advance)

    def wait(self, ms: int) -> None:
        """Pause the timeline (the host simply sends nothing for ``ms``)."""
        self._add(Wait(ms), float(ms) if isinstance(ms, (int, float)) and ms > 0 else 0.0)

    def torque(self, on: bool = True, joint: str | None = None) -> None:
        """Servo torque on (hold position) or free (limp), for one joint or all."""
        self._add(TorqueStep(bool(on), joint), 0.0)

    # -------------------------------------------------------------- robot-side files
    def play_motion(self, name: str, storage: str | int | Storage = "master", *, wait_ms: int = 0) -> None:
        """Play a motion file stored on the robot, e.g. ``"7.act"``.

        The robot does not report how long its motion lasts, so pass
        ``wait_ms`` to keep later steps from starting too early.
        """
        self._add(PlayFile("motion", _storage(storage), name, wait_ms), float(max(wait_ms, 0)))

    def play_task(self, name: str, storage: str | int | Storage = "master", *, wait_ms: int = 0) -> None:
        self._add(PlayFile("task", _storage(storage), name, wait_ms), float(max(wait_ms, 0)))

    def stop_playback(self) -> None:
        self._add(StopPlayback(), 0.0)

    # -------------------------------------------------------------- MPSU / head
    def head_led(self, pattern: int, *, mpsu_id: int = BROADCAST_ID) -> None:
        self._add(HeadLedBuzz(pattern, 0, mpsu_id), 0.0)

    def buzz(self, melody: int, *, mpsu_id: int = BROADCAST_ID) -> None:
        self._add(HeadLedBuzz(0, melody, mpsu_id), 0.0)

    def head_led_buzz(self, led: int, buzz: int, *, mpsu_id: int = BROADCAST_ID) -> None:
        self._add(HeadLedBuzz(led, buzz, mpsu_id), 0.0)

    def mpsu_motion(
        self, index: int, *, ready: bool = False, mpsu_id: int = BROADCAST_ID, wait_ms: int = 0
    ) -> None:
        """Play a motion stored in the MPSU controller by index."""
        self._add(MpsuMotion(index, ready, mpsu_id, wait_ms), float(max(wait_ms, 0)))

    def mpsu_stop(self, *, mpsu_id: int = BROADCAST_ID) -> None:
        self._add(MpsuStop(mpsu_id), 0.0)

    # -------------------------------------------------------------- escape hatches
    def raw_frame(self, command: int, payload: bytes = b"") -> None:
        """Send an arbitrary link frame. Not validated beyond framing; prefer the typed steps."""
        self._add(RawFrame(command, bytes(payload)), 0.0)

    def mark(self, label: str) -> None:
        """Annotation shown in the plan listing; sends nothing."""
        self._add(Mark(label), 0.0)


#: Public methods a program may call on ``g`` (checked statically by the validator).
PROGRAM_METHODS = frozenset(
    name for name in vars(Program) if not name.startswith("_") and name not in ("ir",)
)


@dataclass(frozen=True)
class ProgramSpec:
    name: str
    func: Callable[[Program], Any]


F = TypeVar("F", bound=Callable[..., Any])


@overload
def program(func: F, /) -> F: ...
@overload
def program(*, name: str | None = None) -> Callable[[F], F]: ...


def program(func: Any = None, /, *, name: str | None = None) -> Any:
    """Mark ``func(g: Program)`` as a compilable Genibo program."""

    def wrap(f: F) -> F:
        f.__genibo_program__ = ProgramSpec(name or f.__name__, f)  # type: ignore[attr-defined]
        return f

    return wrap(func) if func is not None else wrap
