# Architecture: what the legacy ecosystem is, and what replaces it

This document records what was found in the repository (facts, with file
references) and how the new Python stack is organised. Assumptions are marked
**[assumption]**; things that could not be determined are marked **[unknown]**.

## 1. The original OpenJigWare distribution (analysed; not part of this repository)

The analysis covered the full upstream distribution
([ojw5014/OpenJigWare](https://github.com/ojw5014/OpenJigWare), its tools, and
[ojw5014/Examples](https://github.com/ojw5014/Examples)). Paths below are relative
to that distribution. This repository vendors only the two files the tests need
(`compat/legacy/`, see its README).

| Directory | What it is | Genibo relevance |
|---|---|---|
| `OpenJigWare-master/OpenJigWare/` | C# class library `OpenJigWare` (namespace `OpenJigWare`, one `partial class Ojw`), .NET Framework 4.0, ~150k lines. Generic robotics toolkit: 3D engine on Tao/OpenGL (`COjw_12_3D.cs`, 35k lines), servo drivers (HerkuleX `COjw_19`/`27`, Dynamixel `COjw_31`/`37`, MX-28 `COjw_15`), kinematics, walking, motion editor (`Docking/MotionEditor.cs`, `.dmt` files), 3D designs (`.ojw`), camera/MJPEG streaming, joystick, speech, Leap/Myo, a launcher for external `python` processes (`COjw_38_Python.cs`). | Only **`COjw_25_Genibo.cs`** is Genibo-specific (classes `Ojw.CGenibo_Bt` and `Ojw.CGenibo`). |
| `Tool-master/Tools/` | WinForms launcher (`Main.cs`) for OpenJigWare tools: 3D modelling designer, motion editor, virtual keyboard, MJPEG streaming client/server, joystick test, speech. References a prebuilt `Released_DLL/OpenJigWare.dll`. | None. It never instantiates a Genibo class. |
| `Examples-master/1/` | 23 C# example projects (Ex1..Ex23) for OpenJigWare features, plus `Released_DLL/`. | **`Ex20_Genibo`** is the only Genibo example. `Ex21_MPSU` scans/changes an MPSU controller ID over a plain serial line. |
| `Examples-master/2/` | Course material (robot basics, D-H kinematics, Dynamixel X firmware update screenshots, forms/process/shared-memory examples, manipulator STL). | None. The "firmware update" material is for Dynamixel X servos. |
| `Examples-master/3/` | Assorted projects (Arduino, delta-dog quadruped, kinematics, OpenJigHand, snake with FlySky iBus Arduino code, ROBOTIS `pycm` Python scripts). | None found (`grep -ri genibo` matches nothing). |

There is **no Genibo IDE, no Genibo simulator, no compiler, no bytecode and no
firmware** in the repository. The generic 3D simulator and motion editor have
no Genibo model or Genibo link.

## 2. The actual execution model

```
 host PC (Windows, .NET 4.0)                                  Genibo robot
 ───────────────────────────                                  ────────────
 C# app ── Ojw.CGenibo_Bt ── SerialPort COMn 115200 8N1 ──BT SPP──► robot firmware [unknown internals]
                 │    frames: FF id cmd len32 payload chk                │
                 │                                                       ├─ plays stored motion/task files by name
                 │    cmd 0x81 tunnels a servo-bus packet ─────────────► ├─ forwards S_JOG / RAM-write to servos
                 │                                                       ├─ MPSU controller: motions by index, head LED, buzzer
                 └─ Ojw.CGenibo ── 6 TCP sockets, ports 6100..6105 ────► └─ (TCP service on some model) [unknown which]
```

* **The robot never runs host code.** Every legacy app is a host program that
  sends commands. The robot executes its own firmware, plus motion (`.act`)
  and task files stored in two areas (`master`, `user`). The format of those
  files is **[unknown]**: the repository only ever moves them as opaque bytes
  (`request_file_download` / `request_file_upload`).
* **Live servo control** goes through link command `0x81`, which carries a
  servo-bus packet (`FF FF size id cmd cs1 cs2 data`). `request_move` sends
  `S_JOG` (0x06) to the broadcast servo ID 0xFE, with one 4-byte entry per
  servo: position LSB, MSB, SET flags, servo ID. `drvsrv` writes the torque
  register (RAM address 52). This layout and the 11.2 ms play-time tick are
  those of the HerkuleX servo family that OpenJigWare also drives directly
  (`COjw_19_Herculex.cs`).
* **MPSU** (motion controller) commands (play motion by index, play task, head
  LED/buzzer, ROM/RAM write and read) are either tunnelled in `0x81`
  (`serial_mpsu_*`) or sent bare over the same link (`drbluetooth_mpsu_*`).

### Trace: Ex20 "Move" button, source to wire

1. `Form1.btnMove_Click` → `m_CGenibo.connect(5, 115200)` opens `COM5`, 115200,
   8N1 and starts a reader thread (`ReceiveDataCallback`).
2. `Cmd_Bluetooth_Play(0x20, 0, txtMotion.Text)` → `drbluetooth_set_id(0x20)` →
   `request_motion_play_now(0, name)` builds a 60-byte payload (storage byte,
   51-byte NUL-padded name, 8 zero bytes for "start now") →
   `send_data(0x40, 60, payload)` → frame `FF 20 40 00 00 00 3C … chk` →
   `SerialPort.Write`. For `"7.act"` the exact bytes are golden scenario
   `bt.ex20.play`.
3. `disconnect()` closes the port immediately afterwards (no reply awaited).

Ex20 was built against a prebuilt DLL. `docs/evidence/binary-comparison.txt`
shows that the Genibo classes in the shipped DLLs decompile identically to the
source (all differing members were reviewed: decompiler/compiler artefacts
only).

## 3. Legacy weaknesses that shaped the new design

* One 6,800-line file mixes protocol, threads, UI message boxes and file I/O;
  state is global per instance (`m_SerialPort`, `m_pSMot[256]`, busy counters).
* Silent corruption: play times above 255 ticks wrap; names are cut without a
  terminator; uncalibrated axes encode NaN; `0xFF` IDs are ambiguous.
* Dead or broken paths: `request_stop()` never transmits (early return);
  `reset()` sends an empty S_JOG instead of the REBOOT it builds; the
  receive parser loses the frame after any zero-length reply and stores MPSU
  memory at the wrong offset.
* No tests, Windows-only (WinForms, `COMn` names), .NET Framework 4.0, x86.

## 4. New architecture (`genibo-sdk/`)

```
src/genibo/
  protocol/      pure, no I/O: frames, commands, replies, float32 conversions, disassembler
  transport/     serial (pyserial), TCP, in-memory; simulator transport lives in sim/
  link.py        reader thread, stream decoder, request/reply matching, trace hooks
  robot.py       Genibo: high-level live client (successor of CGenibo_Bt)
  net.py         GeniboNet: the six-socket TCP client (successor of CGenibo)
  joints.py      joint names -> servo IDs + calibration; validation
  program/       compiled programs: builder (g.*), static check, compiler, plan, player
  sim/           VirtualGenibo (protocol-level), deterministic trajectory sampler
  legacy/        CGeniboBt shim (legacy method names), C# migration scanner
  project.py     genibo.toml projects and scaffolding
  cli.py         `genibo` command
  studio/        PySide6 development studio
compat/
  csharp-reference/    .NET 6 harness compiling the ORIGINAL COjw_25_Genibo.cs -> golden JSON
  compare_binaries.py  decompile-and-compare shipped DLLs with the source build
```

Layering: `protocol` has no dependencies; `link` depends on `protocol` and a
`Transport`; `robot`, `program`, `sim` and `legacy` sit on top; the CLI and the
studio only call those public APIs. The simulator consumes **bytes**, so the
same encoders and decoders are exercised whether the far end is simulated or
real.

### The Python → robot pipeline

```
src/main.py (@program)
   │ static check (AST): imports, while-loops, unknown g.* steps …     → diagnostics with file:line
   ▼
run once at build time with a recording Program                          → IR: timed steps + source lines
   ▼
validate + lower each step with golden-tested encoders                   → frames (bytes) or diagnostics
   ▼
verify (decode every frame, check IDs/ordering), hash                    → build/<name>.plan.json + .lst
   ▼
player: send each frame at its scheduled time over serial / simulator
```

The IR is kept because it is what users reason about (poses, waits, file
playback) and what the studio shows next to the frames; the frames are what
the robot receives. No robot-side compilation step exists to target.
