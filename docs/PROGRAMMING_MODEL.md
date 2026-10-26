# Programming model

The Genibo does not run Python and has no documented bytecode. Python runs on
your PC. There are two ways to write it, and they are deliberately different.

| | Compiled program (`kind = "program"`) | Host script (`kind = "script"`) |
|---|---|---|
| Entry | `@program def main(g: Program)` | `def main(robot: Genibo)` |
| Runs | once at build time, recording steps | live, for as long as the script runs |
| Output | `build/<name>.plan.json`: exact frames + timing + source lines | nothing; frames are sent as calls happen |
| Can react to the robot | no | yes (battery, playback state, replies) |
| Validated before sending | yes: syntax, model rules, joints, limits, encodings | per call (same encoders, same checks) |
| Inspect / simulate / diff | `genibo inspect`, `genibo sim`, studio Plan & Simulator | studio Robot panel, logs |

Choose a compiled program for choreography (poses, waits, LEDs, robot-side
motion files). Choose a host script when the next action depends on what the
robot reports.

## Compiled programs

```python
from genibo.program import Program, program


def wag(g: Program, n: int) -> None:  # helpers are fine
    for i in range(n):
        g.pose(tail=30 if i % 2 == 0 else -30, ms=250)


@program
def main(g: Program) -> None:
    g.torque(True)
    g.pose(head_pan=20, head_tilt=-5, ms=500, led="green")
    wag(g, 4)
    g.play_motion("7.act", "master", wait_ms=3000)
    g.head_led_buzz(led=1, buzz=1)
```

### Steps (`g.*`)

| Step | Frames | Timeline |
|---|---|---|
| `pose(targets, ms=500, *, led, wait=True, clip=False, **joints)` | one S_JOG (servo broadcast) | advances by the quantised play time (`ticks × 11.2 ms`) unless `wait=False` |
| `pose_raw(...)` | same, raw positions | same |
| `wait(ms)` | none | advances `ms` |
| `torque(on=True, joint=None)` | torque register write | – |
| `play_motion(name, storage="master", *, wait_ms=0)` | link 0x40 (motion) | advances `wait_ms` (the robot does not report motion length) |
| `play_task(name, storage, *, wait_ms=0)` | link 0x40 (task) | advances `wait_ms` |
| `stop_playback()` | link 0x42 | – |
| `head_led(n)`, `buzz(n)`, `head_led_buzz(led, buzz)` | MPSU 0x18 (tunnelled) | – |
| `mpsu_motion(index, *, ready, wait_ms)`, `mpsu_stop()` | MPSU 0x16 (tunnelled) | advances `wait_ms` |
| `raw_frame(cmd, payload)` | arbitrary link frame (warning G304) | – |
| `mark(label)` | none (annotation in listings) | – |

`g.now_ms` gives the time at which the next step will be sent.

### What Python is supported

Allowed: functions, `for` over `range`/literals, `if`, arithmetic, `math`,
lists/dicts/tuples, comprehensions, constants, f-strings.

Not allowed (a static error with the line number):

| Code | Construct | Why |
|---|---|---|
| G101 | `import` other than `genibo`, `math`, `typing`, `__future__` | host libraries cannot influence the robot after the build |
| G102 | `while` | unbounded loops cannot be compiled; use `for … in range(n)` or a host script |
| G103/G104 | `async`, `await`, generators | no runtime on the robot |
| G105/G106 | `global`, `nonlocal`, `with` | not meaningful in a build-time trace |
| G107 | `open`, `input`, `eval`, `exec`, `__import__`, `setattr`… | cannot be validated |
| G109 | `g.<unknown>` | suggests the closest valid step |

These checks enforce the model. They are **not** a security sandbox: the
program file is your own code and runs on your machine at build time.

### Diagnostics

Errors (build fails): G100 syntax, G108 bad `@program` signature, G110–G112
entry selection, G120 module raised at import, G121 program raised, G122
more than 20,000 steps, G130–G134 malformed steps, G140 value cannot be
encoded (unknown joint, out of limits, raw out of range, play time > 255
ticks, name too long), G150 the produced artifact failed verification.

Warnings: G201 `print`, G202 `try`, G203 no steps, G301 joint not verified on
hardware, G302 a pose interrupts a still-moving joint, G303 robot-side
playback with unknown duration, G304 raw frame, G306 play time rounds to 0
ticks. Info: G305 torque state at start unknown.

## Host scripts

```python
from genibo import Genibo, Storage


def main(robot: Genibo) -> None:
    if robot.battery().robot_percent > 20:
        robot.play_motion("7.act", Storage.MASTER)
    robot.move({"head_pan": 15}, 400, wait=True)
```

Run with `genibo script src/main.py --sim` or `--port COM5`. Queries
(`battery`, `is_playing`, `list_files`, `get_time`, `download_file`, …) wait for
the robot's reply and raise `ResponseTimeout` / `RobotStatusError`. Commands are
fire-and-forget like the legacy tool; pass `ack_timeout=` to require a STATUS
acknowledgement.

## Projects (`genibo.toml`)

```toml
[project]
name = "wave"
kind = "program"          # or "script"
entry = "src/main.py"
# program = "main"        # pick one when the file defines several @program functions

[robot]
id = 0x20                 # robot ID on the link
port = "COM5"             # default for genibo run/script (optional)
baudrate = 115200
encoding = "ascii"        # robot-side file names

[joints]
profile = "legacy-cgenibo"    # or "legacy-cgenibo-raw" (no calibration), "empty"

[joints.head_pan]         # per-joint override; any subset of keys
servo_id = 15
center = 512
mech_move = 1024.0        # raw counts ...
degree = 333.3            # ... per this many degrees
direction = 0             # 1 inverts
limit_up = 60.0           # 0 = no limit (legacy convention)
limit_dn = -60.0
raw_min = 0
raw_max = 1023
verified = true           # only after checking this joint on the robot

[build]
out_dir = "build"
```

Overriding a joint never inherits `verified = true`; you have to state it.
