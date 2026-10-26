"""Stream a command plan to a robot (or the simulator) on schedule."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from genibo.clock import Clock, RealClock
from genibo.errors import GeniboError
from genibo.link import Link
from genibo.program.plan import CommandPlan, PlannedFrame
from genibo.protocol import commands as cmd
from genibo.protocol.constants import BROADCAST_ID, Torque

log = logging.getLogger("genibo.player")


class PlanMismatchError(GeniboError):
    pass


@dataclass
class PlayReport:
    sent: int = 0
    total: int = 0
    cancelled: bool = False
    #: Per-frame lateness (actual send time minus scheduled time), seconds.
    lateness_s: list[float] = field(default_factory=list)

    @property
    def max_lateness_ms(self) -> float:
        return max(self.lateness_s, default=0.0) * 1000.0


def play_plan(
    plan: CommandPlan,
    link: Link,
    *,
    clock: Clock | None = None,
    cancel: threading.Event | None = None,
    on_frame: Callable[[int, PlannedFrame], None] | None = None,
    on_cancel: str = "nothing",
    robot_id: int | None = None,
) -> PlayReport:
    """Send every frame of *plan* at its scheduled time.

    ``on_cancel``: ``"nothing"`` stops streaming (servos keep their last
    target), ``"torque_free"`` additionally sends the torque-free broadcast
    (what legacy ``request_ems`` sends; the robot will go limp).
    """
    if on_cancel not in ("nothing", "torque_free"):
        raise ValueError("on_cancel must be 'nothing' or 'torque_free'")
    problems = plan.verify()
    if problems:
        raise PlanMismatchError("plan failed verification: " + "; ".join(problems))
    if robot_id is not None and robot_id != plan.robot_id and plan.robot_id != BROADCAST_ID:
        raise PlanMismatchError(
            f"plan targets robot 0x{plan.robot_id:02x} but the link addresses 0x{robot_id:02x}; rebuild"
        )
    clock = clock or RealClock()
    report = PlayReport(total=len(plan.frames))
    t0 = clock.now()
    log.info(
        "playing plan %r: %d frames, %.1f ms",
        plan.name,
        len(plan.frames),
        plan.duration_ms,
        extra={"event": "plan_start", "hardware": link.transport.is_hardware},
    )
    for i, pf in enumerate(plan.frames):
        due = t0 + pf.t_ms / 1000.0
        if not clock.sleep(max(0.0, due - clock.now()), cancel) or (cancel is not None and cancel.is_set()):
            report.cancelled = True
            break
        link.send(pf.decode())
        report.lateness_s.append(max(0.0, clock.now() - due))
        report.sent += 1
        if on_frame is not None:
            on_frame(i, pf)
    else:
        # Let the last move finish before reporting completion.
        end = t0 + plan.duration_ms / 1000.0
        if not clock.sleep(max(0.0, end - clock.now()), cancel):
            report.cancelled = True
    if report.cancelled:
        log.warning(
            "plan %r cancelled after %d/%d frames",
            plan.name,
            report.sent,
            report.total,
            extra={"event": "plan_cancel"},
        )
        if on_cancel == "torque_free":
            link.send(cmd.tunnel(plan.robot_id, cmd.torque(BROADCAST_ID, Torque.FREE)))
    else:
        log.info(
            "plan %r done; max lateness %.1f ms",
            plan.name,
            report.max_lateness_ms,
            extra={"event": "plan_done"},
        )
    return report
