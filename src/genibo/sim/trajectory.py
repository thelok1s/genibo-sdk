"""Deterministic offline simulation of a command plan (used by the studio and tests)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from genibo.clock import VirtualClock
from genibo.program.plan import CommandPlan
from genibo.sim.device import SimEvent, VirtualGenibo


@dataclass
class Trajectory:
    times_ms: list[float]
    raw: dict[int, list[float]]  # servo id -> raw position per sample
    events: list[SimEvent] = field(default_factory=list)
    frame_times_ms: list[float] = field(default_factory=list)

    def at(self, t_ms: float) -> dict[int, float]:
        """Linear interpolation between samples."""
        ts = self.times_ms
        if not ts:
            return {}
        if t_ms <= ts[0]:
            return {k: v[0] for k, v in self.raw.items()}
        if t_ms >= ts[-1]:
            return {k: v[-1] for k, v in self.raw.items()}
        lo, hi = 0, len(ts) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ts[mid] <= t_ms:
                lo = mid
            else:
                hi = mid
        a = (t_ms - ts[lo]) / (ts[hi] - ts[lo]) if ts[hi] > ts[lo] else 0.0
        return {k: v[lo] + (v[hi] - v[lo]) * a for k, v in self.raw.items()}

    def moving_servos(self) -> list[int]:
        return [sid for sid, v in self.raw.items() if max(v) - min(v) > 1e-9]


def simulate_plan(
    plan: CommandPlan,
    *,
    sample_ms: float = 20.0,
    robot_files: Iterable[tuple[int, str]] = (),
    tail_ms: float = 0.0,
) -> Trajectory:
    """Feed the plan's frames to a :class:`VirtualGenibo` in virtual time and sample servo positions."""
    clock = VirtualClock()
    dev = VirtualGenibo(robot_id=plan.robot_id, clock=clock)
    for storage, name in robot_files:
        dev.add_file(storage, name, b"(opaque)")
    end = plan.duration_ms + tail_ms
    n = int(end // sample_ms) + 1
    times = [round(i * sample_ms, 3) for i in range(n)]
    if not times or times[-1] < end:
        times.append(round(end, 3))
    frames = sorted(plan.frames, key=lambda f: f.t_ms)
    raw: dict[int, list[float]] = {sid: [] for sid in dev.servos}
    fi = 0

    def advance_to(t_ms: float) -> None:
        dt = t_ms / 1000.0 - clock.now()
        if dt > 0:
            clock.advance(dt)

    for t in times:
        while fi < len(frames) and frames[fi].t_ms <= t:
            advance_to(frames[fi].t_ms)
            dev.receive(frames[fi].data)
            fi += 1
        advance_to(t)
        snap = dev.snapshot()
        for sid, v in snap.items():
            raw[sid].append(v)
    return Trajectory(times, raw, list(dev.events), [f.t_ms for f in frames])
