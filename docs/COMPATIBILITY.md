# Hardware compatibility contract

The robot only ever sees bytes. The contract is therefore: **for every command,
the SDK transmits exactly the bytes the legacy OpenJigWare implementation
transmitted**, except where the legacy code silently corrupted a frame. In that
case the SDK refuses with an error rather than sending something different.

## Evidence levels

| Level | Meaning | Status |
|---|---|---|
| L1: byte-equal to legacy source | Python output == bytes written by the unmodified `COjw_25_Genibo.cs`, compiled and run in `compat/csharp-reference` | **Verified**: 68 transmit scenarios (63 Bluetooth, 5 TCP), 11 numeric tables, 13 receive scenarios (`tests/test_golden_csharp.py`, `tests/test_legacy_shim.py`). `tests/test_reference_harness.py` regenerates the data from the C# and diffs it. |
| L2: legacy source == shipped binaries | The prebuilt `OpenJigWare.dll` used by Ex20, and the Released DLLs, contain the same Genibo code | **Verified by decompilation**: all members of `CGenibo_Bt` (289) and `CGenibo` (54) match or differ only by reviewed compiler/decompiler artefacts (`docs/evidence/binary-comparison.txt`). |
| L3: simulator | Host stack works end to end against `VirtualGenibo` | **Verified**, but the simulator's replies are inferred, so this is not hardware evidence. |
| L4: physical robot | The robot accepts the frames and behaves as described | **Not verified**: no hardware was available. See `docs/HARDWARE_TESTING.md`. |

Nothing in this repository claims L4.

## Link frames (Bluetooth serial and TCP)

```
FF | id | cmd | len (u32 big-endian) | payload | chk      chk = XOR(id .. last payload byte) & 0x7F
```

* Serial settings: 115200 baud, 8 data bits, no parity, 1 stop bit (`connect`).
  The legacy integer port `N` meant `COMN`.
* `id` is the robot ID (`drbluetooth_set_id`; Ex20 used `0x20`; `0xFE` = broadcast).
  On TCP, `id = 0x90 + channel` (channels 0..5 on ports 6100..6105).
* The checksum is masked to 7 bits, so it can never be mistaken for the `FF` header.

## Bus packets (servo / MPSU)

```
FF FF | size | id | cmd | cs1 | cs2 | data          size = 7 + len(data) <= 255
cs1 = XOR(size, id, cmd, data...) & 0xFE           cs2 = ~cs1 & 0xFE
```

Tunnelled in link command `0x81` (`Make_And_Send_Packet`, `serial_mpsu_*`) or
sent bare on the serial link (`drbluetooth_mpsu_*`).

## Bluetooth commands

| cmd | Legacy method(s) | Payload | Reply the legacy host parsed | SDK |
|---|---|---|---|---|
| 0x31 | `request_filelist` | storage(1) | 0x31: storage, count(u16 BE), count×51-byte names | `list_files` |
| 0x32 | `request_file_download` (PC→robot) | storage, index(u16 BE), total(u16 BE), name(51, last byte 0), content | none | `upload_file` |
| 0x33 | `request_file_upload` (robot→PC) | storage, name(51) | 0x33: storage, name(51), content | `download_file` |
| 0x34 | `request_file_delete(m, name)` | storage, name(51) | none | `delete_file` |
| 0x35 | `request_file_delete(m)` | storage | none | `delete_all_files(confirm=True)` |
| 0x40 | `request_motion_play[_now]`, `Cmd_Bluetooth_Play` | storage, name(51, last byte 0), unix sec(u32 BE), usec(u32 BE); zeros = now | none | `play_motion` |
| 0x40 | `request_task_play` | storage, name(51) (52 bytes) | none | `play_task` |
| 0x41 | `request_motion/task_is_playing` | – | 0x41: bool(1) | `is_playing` |
| 0x42 | `request_motion/task_stop` | – | none | `stop_playback` |
| 0x61 | `request_set_time` | unix sec, usec (UTC, from `ToFileTime`) | none | `set_time` |
| 0x62 | `request_time` | – | 0x62: sec, usec | `get_time` |
| 0x63 | `request_battery` | – | 0x63: robot %, "MID" % | `battery` |
| 0x64 | `request_charge` | – | none (effect undocumented) | `charge` |
| 0x65 | `request_remocon` | – | 0x65: length (0.125 s ticks), data | `remocon` |
| 0x71 | `request_auth` | user(21), password(21) | not parsed on Bluetooth (TCP: byte 0 ≠ 0 = OK) | `auth` (returns `None` if no reply) |
| 0x81 | `Make_And_Send_Packet`, `drvsrv`, `request_move`, `serial_mpsu_*` | bus packet | none | `move`, `jog`, `torque`, `head_led_buzz`, `mpsu_*` |
| 0xF0 | (robot→host) | for-cmd, status code 0..5 | sets `packet_status` | raised as `RobotStatusError` |
| 0xF1 | `request_heartbeat` | – | 0xF1, zero length | `heartbeat` |

Name fields are NUL-padded. The SDK requires names to fit with a terminator
(≤ 50 bytes; ≤ 20 for auth fields; ≤ 15 for TCP `ActionPlay`).

## Servo bus content

* **S_JOG (0x06)** to device 0xFE: `playtime_ticks(1)` followed by, per servo,
  `pos_lo, pos_hi, SET, servo_id`. Ticks are `round_half_even(ms / 11.2)` in
  float32. SET bits: STOP 0x01, SPEED 0x02, green 0x04, blue 0x08, red 0x10,
  NO_ACTION 0x20. Entries are in axis order (the SDK uses joint-table order).
* **Torque**: RAM write (0x03) `[52, 1, mode]`, where mode is FREE 0x00, DRIVER 0x40,
  SERVO 0x20 or ON 0x60 (`drvsrv(bDriver, bServo)`).
* **Angles**: `raw = round_half_even(f32(f32(mech·sign·angle)/degree)) + center`,
  then the legacy limit step (`raw & 0x3FFF` clipped to the converted limits,
  with bit 14 preserved). Reproduced bit-exactly in `protocol/convert.py`.

## MPSU commands

| cmd | Meaning (from legacy names) | Data |
|---|---|---|
| 0x0F | motor move (S_JOG layout, device = robot ID) | as S_JOG |
| 0x11 / 0x13 | ROM / RAM write | address, length, data |
| 0x12 / 0x14 | ROM / RAM read → reply 0x52 / 0x54 | address, length → address, length, data, status1, status2 |
| 0x16 | play motion by index (0xFE stops) | index, ready-only [, delay u16 LE] |
| 0x17 | play task (0xFE stops) | task |
| 0x18 | head LED pattern / buzzer melody | led, buzz (0 = none) |

## Legacy quirks: preserved, refused or fixed

| Legacy behaviour | SDK |
|---|---|
| `request_stop()` sets a flag and transmits **nothing** (returns early) | Shim reproduces it. Modern API: `stop_joints()` sends what `request_stop(axis)` sends; `emergency_stop()` sends the torque-free broadcast that `request_ems()` sends. |
| `reset()` transmits an empty S_JOG to 0xFE | Shim reproduces it. Modern `clear_emergency()` sends nothing. |
| Play time > 2856 ms wraps modulo 256 ticks | **Refused** (`EncodingError`) |
| Names longer than the field: cut, or no terminator | **Refused** unless `legacy_truncate=True` |
| Axis without `set_param` encodes NaN/garbage | **Refused** (`JointError`) |
| Angles outside limits are clipped silently | **Refused** unless `clip=True` (then identical bytes) |
| Receive parser drops the frame after a zero-length reply | **Fixed** (golden scenario documents the legacy loss) |
| MPSU RAM reply stored from the address byte | **Fixed** (data stored at `address`) |
| `Encoding.Default` (host ANSI code page) for names | ASCII by default; `encoding=` is configurable. Non-ASCII behaviour on the robot is unknown. |

## Timing

The legacy code never paced frames; applications sent commands back to back
(for example `DrvSrv` immediately followed by `Mpsu_Play_Motion`). Command plans
schedule frames at explicit times; frames that share a time are sent back to
back, as before. The player reports lateness per frame, so it can be measured
on hardware.

## Residual risks (unverified)

1. Robot replies and status codes are taken from the legacy *parser*. Which
   commands are acknowledged, and how fast, is unknown.
2. Servo IDs and calibration of a physical Genibo are not in the repository.
   The default joint profile is assembled from `CGenibo` constants and a code
   comment and is marked unverified. Hardware runs refuse it unless you opt in.
3. `.act`/task formats are unknown; files are moved verbatim.
4. Float rounding: golden data comes from .NET 6 (IEEE single). The original
   x86 .NET Framework JIT may have used x87 extended precision, which could
   differ by one count exactly at a rounding boundary.
5. Speed-mode semantics and negative speeds (sent as 16-bit two's complement,
   exactly as the legacy code did) are unverified.
6. Which Genibo models expose the TCP service, and whether its replies match
   the legacy `Receive_*` parsers, is unknown.
7. The maximum file size the robot accepts in one `0x32` frame is unknown.

## Reproducing the evidence

```bash
# L1: regenerate golden data from the original C# and compare (needs the .NET 6+ SDK)
uv run pytest tests/test_reference_harness.py
# or by hand:
dotnet run -c Release --project compat/csharp-reference -- tests/golden/csharp_reference.json

# L2: compare shipped DLLs with the source build (needs ilspycmd 7.2.1 and a checkout of
# the original examples, e.g. github.com/ojw5014/Examples; the DLLs are not vendored here)
python compat/compare_binaries.py --ilspy <ilspycmd> compat/csharp-reference/bin/Release/net6.0/GeniboReference.dll \
    <examples>/1/Ex20_Genibo/WindowsFormsApplication1/WindowsFormsApplication1/bin/Debug/OpenJigWare.dll
```
