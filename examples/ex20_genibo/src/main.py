"""Ex20_Genibo, ported from C# (ojw5014/Examples 1/Ex20_Genibo/.../Form1.cs; copy in compat/legacy/).

The original WinForms form had three buttons:

* Connect / Disconnect  -> ``connect(5, 115200)`` (COM5, 115200 8N1)
* Move                  -> ``Cmd_Bluetooth_Play(0x20, 0, txtMotion.Text)``: play a
                           motion file from the robot's *master* area
* Move MPSU             -> ``COjwMotor.Mpsu_Play_Motion(id, number, false)`` over a
                           serial line connected directly to the MPSU controller

Run against the simulator:   genibo script src/main.py --sim
Run against the robot:        genibo script src/main.py --port COM5   (or /dev/tty.<genibo>)

Environment knobs: GENIBO_MOTION (default "7.act"), GENIBO_MPSU_MOTION (index, optional).
"""

from __future__ import annotations

import os

from genibo import Genibo, Storage


def main(robot: Genibo) -> None:
    motion = os.environ.get("GENIBO_MOTION", "7.act")
    # "Move" button: Cmd_Bluetooth_Play(0x20, 0, name) -> the robot ID comes from genibo.toml / --robot-id
    robot.play_motion(motion, Storage.MASTER)
    print(f"sent: play {motion!r} from master area to robot 0x{robot.robot_id:02x}")

    # "Move MPSU" button. The legacy example talked to the MPSU on its own serial
    # line; the same packet can be sent bare over the robot link (direct=True).
    mpsu = os.environ.get("GENIBO_MPSU_MOTION")
    if mpsu is not None:
        robot.mpsu_play_motion(int(mpsu), ready=False, direct=True)
        print(f"sent: MPSU motion #{mpsu}")
