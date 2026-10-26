"""Wave: head turn, tail wag and a head-LED/buzzer cue, as a compiled program.

genibo build examples/wave      # -> build/wave.plan.json + build/wave.lst
genibo sim examples/wave        # simulator (inferred behaviour)
genibo run examples/wave --port COM5 --allow-unverified-joints   # hardware, see docs first
"""

from genibo.program import Program, program

WAGS = 4
TAIL_SWING = 30  # degrees either side (the project limits tail to +/-45)


def wag(g: Program, times: int, amplitude: float, ms: int) -> None:
    for i in range(times):
        g.pose(tail=amplitude if i % 2 == 0 else -amplitude, ms=ms)
    g.pose(tail=0, ms=ms)


@program
def wave(g: Program) -> None:
    g.torque(True)
    g.mark("neutral pose")
    g.pose(head_pan=0, head_tilt=0, tail=0, ms=800)

    g.mark("look left/right with green servo LEDs")
    g.pose(head_pan=25, head_tilt=-10, ms=500, led="green")
    g.pose(head_pan=-25, ms=900, led="green")
    g.pose(head_pan=0, head_tilt=0, ms=500)

    g.mark("wag")
    wag(g, WAGS, TAIL_SWING, ms=250)

    g.head_led_buzz(led=1, buzz=1)
    g.wait(300)
