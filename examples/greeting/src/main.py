"""Greeting: combine a motion file already stored on the robot with host-timed poses.

The robot plays "7.act" (the motion the legacy Ex20 example used). Its length
is not reported by the robot, so the program waits a fixed time before the
next step; adjust ``MOTION_MS`` after timing the real motion.
"""

from genibo.program import Program, program

MOTION_MS = 3000


@program
def greeting(g: Program) -> None:
    g.head_led(3)
    g.play_motion("7.act", "master", wait_ms=MOTION_MS)
    g.torque(True)
    g.pose(head_tilt=-15, ms=400)
    g.pose(head_tilt=10, ms=400)
    g.pose(head_tilt=0, ms=400)
    g.buzz(2)
