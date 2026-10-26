"""Exception hierarchy for the Genibo SDK."""

from __future__ import annotations

from dataclasses import dataclass


class GeniboError(Exception):
    """Base class for every error raised by the SDK."""


class EncodingError(GeniboError, ValueError):
    """A value cannot be encoded into a frame without changing its meaning."""


class FrameError(GeniboError, ValueError):
    """Bytes do not form a valid frame (header, length or checksum mismatch)."""


class TransportError(GeniboError, OSError):
    """The serial port / socket failed or is not open."""


class ResponseTimeout(GeniboError, TimeoutError):
    """The robot did not answer within the timeout."""


class RobotStatusError(GeniboError):
    """The robot answered with a non-OK status code."""

    def __init__(self, command: int, code: int, message: str) -> None:
        super().__init__(message)
        self.command = command
        self.code = code


class SafetyLatchError(GeniboError):
    """A motion command was refused because the emergency stop latch is set."""


class JointError(GeniboError, ValueError):
    """Unknown joint, missing calibration, or a target outside the joint limits."""


@dataclass(frozen=True)
class SourceLocation:
    """Position in a user's Python source file."""

    file: str
    line: int
    column: int = 0

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column + 1}"


@dataclass(frozen=True)
class Diagnostic:
    """A validation or build message tied (when possible) to the user's source."""

    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    location: SourceLocation | None = None
    hint: str = ""

    def __str__(self) -> str:
        where = f"{self.location}: " if self.location else ""
        hint = f"\n    hint: {self.hint}" if self.hint else ""
        return f"{where}{self.severity} {self.code}: {self.message}{hint}"


class BuildError(GeniboError):
    """A program failed validation or compilation. Carries all diagnostics."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        errors = [d for d in diagnostics if d.severity == "error"]
        head = str(errors[0]) if errors else "build failed"
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        super().__init__(head + more)
