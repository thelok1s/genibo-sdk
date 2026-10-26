"""Status monitor: a host script that reacts to the robot's replies.

This is what compiled programs cannot do: it reads the battery and the
playback state at run time and decides what to send next.

    genibo script src/main.py --sim
    genibo script src/main.py --port COM5
"""

from __future__ import annotations

import os
import time

from genibo import Genibo, ResponseTimeout, Storage

LOW_BATTERY = 20


def main(robot: Genibo) -> None:
    rounds = int(os.environ.get("GENIBO_ROUNDS", "3"))
    print("master files:", robot.list_files(Storage.MASTER))
    for i in range(rounds):
        try:
            battery = robot.battery()
            playing = robot.is_playing()
            rtt_ms = robot.heartbeat() * 1000
        except ResponseTimeout as exc:
            print(f"[{i}] no reply: {exc}")
            continue
        print(
            f"[{i}] battery {battery.robot_percent}% · MID {battery.mid_percent}% · "
            f"{'playing' if playing else 'idle'} · heartbeat {rtt_ms:.1f} ms"
        )
        if battery.robot_percent < LOW_BATTERY and playing:
            print("battery low: stopping playback")
            robot.stop_playback()
        time.sleep(0.2)
