# Hardware verification plan

No physical Genibo was available while building the SDK. Everything below is
**required** before the project can claim hardware compatibility (evidence
level L4 in `COMPATIBILITY.md`). Run the steps in order; each one builds on the
previous one and risks a little more.

Setup: pair the robot over Bluetooth and find its serial port (`genibo ports`).
Note the robot ID (Ex20 used `0x20`). Keep the robot on a stand or clear floor.

| # | Check | Command | Record |
|---|---|---|---|
| 1 | Link opens; heartbeat answered | `GENIBO_PORT=… uv run pytest -m hardware tests/hardware -s -k heartbeat` | RTT, reply bytes (`--log-level debug`) |
| 2 | Reply layouts: battery, time, file list | `genibo robot status --port …` and `genibo robot files --port …` | Do values and lengths match `COMPATIBILITY.md`? |
| 3 | Which commands send STATUS (0xF0) acks | `genibo --log-level debug robot play 7.act --port …` (and `stop`, `set-time`) | rx frames after each command |
| 4 | Playback of an existing motion | `GENIBO_MOTION_FILE=7.act … -k play` | Does `is_playing` go true, then false after stop? |
| 5 | File transfer (user area) | `GENIBO_UPLOAD_OK=1 … -k upload` | Round-trip bytes equal? Max size? |
| 6 | Servo IDs | `genibo robot torque on/off --port …`, then one joint: `GENIBO_JOG_JOINT=head_pan … -k joint` | Which part moved? Direction? |
| 7 | Calibration per joint | edit `[joints.<name>]` center / direction / limits; repeat step 6 at 0, +10 and −10 degrees | Measured angles; then set `verified = true` |
| 8 | Play-time quantisation | build a plan with 250 ms and 2800 ms poses; `genibo run … --allow-unverified-joints` | Motion duration ≈ ticks × 11.2 ms? |
| 9 | E-STOP | during a plan press Ctrl-C with `--on-cancel torque_free`, or studio E-STOP | Robot goes limp immediately? |
| 10 | MPSU path | `genibo robot led-buzz --led 1 --buzz 1 --port …`; `genibo robot mpsu-motion 1 --port …` | Head LED / buzzer / motion? Is MPSU reachable via 0x81 and/or bare? |
| 11 | Streaming timing | `genibo run` reports worst lateness | Lateness under load; dropped frames? |
| 12 | TCP service (if the model has one) | `python examples/tcp_action_play.py <ip>` | Do ports 6100..6105 accept connections? Auth reply? |

## Results log

Copy this block per session into this file:

```
date / robot model / firmware (if visible) / host OS / port:
1 heartbeat:
2 replies:
3 acks:
4 playback:
5 files:
6 servo ids:
7 calibration (joint: center, direction, limits):
8 timing:
9 e-stop:
10 mpsu:
11 lateness:
12 tcp:
```

Once a joint's ID and calibration are confirmed, set `verified = true` for it
in the project's `genibo.toml`. Better still, add a verified profile to
`genibo/joints.py` so every project benefits.
