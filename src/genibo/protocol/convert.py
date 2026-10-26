"""Numeric conversions reproduced bit-for-bit from the legacy C# code.

The legacy code computes in 32-bit ``float`` and rounds with ``Math.Round``
(round-half-to-even). Python floats are 64-bit, so every intermediate result is
explicitly narrowed to float32 here; otherwise values that sit on a rounding
boundary would encode differently from the original tool.

Golden tables generated from the original code live in
``tests/golden/csharp_reference.json`` (``calc_time_ms``, ``angle_to_raw.*``,
``raw_to_angle.*``, ``set_cmd_angle.clip*``).

Residual risk (documented in docs/COMPATIBILITY.md): the golden data comes from
.NET 6 (IEEE single precision). The original x86 .NET Framework JIT could keep
intermediates in x87 extended precision, which may differ by one count at exact
rounding boundaries.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from genibo.errors import EncodingError
from genibo.protocol.constants import MAX_PLAYTIME_TICKS, TICK_MS

_F32 = struct.Struct("<f")


def f32(x: float) -> float:
    """Round a Python float to the nearest IEEE-754 single-precision value."""
    return _F32.unpack(_F32.pack(x))[0]


def _round_half_even(x: float) -> int:
    # Python's round() on floats is round-half-to-even, same as Math.Round(double).
    return round(x)


_TICK_F32 = f32(TICK_MS)


def ms_to_ticks_legacy(ms: int) -> int:
    """``CalcTime_ms``: ``(int)Math.Round((float)nTime / 11.2f)`` (no range check)."""
    return _round_half_even(f32(f32(float(ms)) / _TICK_F32))


def ms_to_ticks(ms: int) -> int:
    """Play time in ms -> one-byte tick count, refusing values the byte cannot hold.

    The legacy code writes ``ticks & 0xFF``, so 2862 ms silently became 0 ticks.
    """
    if isinstance(ms, bool) or not isinstance(ms, int):
        raise EncodingError(f"play time must be an integer number of milliseconds, got {ms!r}")
    if ms < 0:
        raise EncodingError(f"play time cannot be negative ({ms} ms)")
    ticks = ms_to_ticks_legacy(ms)
    if ticks > MAX_PLAYTIME_TICKS:
        raise EncodingError(
            f"play time {ms} ms is {ticks} ticks; the field holds at most {MAX_PLAYTIME_TICKS} ticks "
            f"(about {int(MAX_PLAYTIME_TICKS * TICK_MS)} ms). Split the move into several steps."
        )
    return ticks


def ticks_to_ms(ticks: int) -> float:
    return ticks * TICK_MS


@dataclass(frozen=True)
class ServoCalibration:
    """Per-joint conversion parameters (legacy ``set_param`` arguments).

    ``mech_move`` raw counts correspond to ``degree`` degrees; ``center`` is
    the raw value at 0 degrees; ``direction`` 1 inverts the sign. Limits of 0
    mean "no limit", exactly as in the legacy ``CalcLimit_*`` helpers.
    """

    center: int
    mech_move: float
    degree: float
    direction: int = 0
    limit_up: float = 0.0
    limit_dn: float = 0.0

    def __post_init__(self) -> None:
        if self.direction not in (0, 1):
            raise EncodingError("direction must be 0 (normal) or 1 (inverted)")
        if self.mech_move == 0 or self.degree == 0:
            raise EncodingError("mech_move and degree must be non-zero")

    @property
    def sign(self) -> float:
        return 1.0 if self.direction == 0 else -1.0


def angle_to_raw(cal: ServoCalibration, angle: float, *, speed_mode: bool = False) -> int:
    """``CalcAngle2Evd`` in float32 arithmetic."""
    v = f32(f32(angle) * cal.sign)
    if speed_mode:
        return _round_half_even(v)
    scaled = f32(f32(f32(cal.mech_move) * v) / f32(cal.degree))
    return _round_half_even(scaled) + cal.center


def raw_to_angle(cal: ServoCalibration, raw: int, *, speed_mode: bool = False) -> float:
    """``CalcEvd2Angle`` in float32 arithmetic."""
    if speed_mode:
        return f32(f32(float(raw)) * cal.sign)
    num = f32(f32(cal.degree) * f32(float(raw - cal.center)))
    return f32(f32(num / f32(cal.mech_move)) * cal.sign)


def raw_limits(cal: ServoCalibration) -> tuple[int, int]:
    """(low, high) raw limits as ``CalcLimit_Evd`` derives them (0 = unlimited)."""
    up, dn = 100000, -100000
    if cal.limit_up != 0:
        up = angle_to_raw(cal, cal.limit_up)
    if cal.limit_dn != 0:
        dn = angle_to_raw(cal, cal.limit_dn)
    if up < dn:
        up, dn = dn, up
    return dn, up


def clip_raw_legacy(cal: ServoCalibration, raw: int) -> int:
    """``CalcLimit_Evd`` for position mode, including its bit-14 masking."""
    pulse = raw & 0x4000
    value = raw & 0x3FFF
    lo, hi = raw_limits(cal)
    value = min(value, hi)
    value = max(value, lo)
    return value | pulse
