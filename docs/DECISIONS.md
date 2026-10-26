# Architectural decisions

Short records of the significant choices and their trade-offs.

## D1. Compatibility is defined by bytes, proven against the original code

*Context.* No hardware, no protocol documentation, no firmware: the only
authority is the legacy C# implementation.
*Decision.* Compile the **unmodified** `COjw_25_Genibo.cs` under .NET 6
against stubs that replace only I/O endpoints (`SerialPort`, `CSocket`,
WinForms helpers), record every byte, and require byte equality from Python.
Separately, decompile the shipped DLLs to show they contain the same code.
*Trade-off.* Proves equivalence with the legacy *tool*, not correct behaviour
of the *robot*. That gap is stated everywhere (evidence level L4 is open).

## D2. No robot-side compiler; programs become host-streamed command plans

*Context.* The robot runs its own firmware and plays stored `.act`/task files
of unknown format. Nothing in the repository compiles anything for the robot.
*Decision.* A compiled program is traced at build time into an IR, then lowered
to a **command plan**: timed frames plus source mapping. The host streams it.
*Rejected:* generating `.act` files (format unknown, so an incompatible
reimplementation risk); running Python on the robot (no evidence it is
possible).
*Trade-off.* Programs cannot react to the robot at run time; host scripts
cover that case.

## D3. Trace execution plus static checks, not AST-to-code translation

*Decision.* Run the `@program` function once with a recorder, after AST checks
that reject constructs whose outcome cannot be decided at build time (`while`,
imports, I/O, ...).
*Why.* Gives exact Python semantics for the supported subset (helpers, loops,
arithmetic) with little code, and source lines come from the call stack.
*Trade-off.* Not a sandbox; documented as such.

## D4. Strict by default, legacy quirks only on request

Where the legacy code silently corrupted frames (wrapping play times,
truncated names, NaN angles) the SDK raises. The legacy shim reproduces the
bytes, including `request_stop()` sending nothing, so line-by-line ports
behave identically. `clip=True` / `legacy_truncate=True` opt back in.

## D5. Python 3.11+, uv, pyserial, minimal runtime dependencies

`tomllib` is in the standard library from 3.11. The runtime needs only
`pyserial` (cross-platform COM, `/dev/tty.*`, `/dev/rfcomm*`). Development:
pytest, hypothesis (framing properties), ruff, pyright. Build backend
`uv_build`.

## D6. Studio on PySide6-Essentials, as an optional extra

*Context.* The legacy GUI (`Tools`) was a WinForms launcher for generic
OpenJigWare tools; none of it was Genibo-specific.
*Decision.* A new Qt desktop studio (editor, diagnostics, plan inspector,
simulator plots, robot panel, logs, API reference) shipped as
`genibo[studio]`.
*Why Qt.* Cross-platform desktop (serial ports are local hardware),
first-class text editing, offscreen rendering for automated tests. Essentials
only, so WebEngine (~hundreds of MB) is not pulled in.
*Rejected:* a web UI (needs a local server plus browser serial bridging);
Tkinter (weak editor support).

## D7. Protocol-level simulator, labelled as inferred

The simulator consumes the same bytes as the robot and answers with the reply
layouts the legacy *parser* expected. Every assumption is listed in
`genibo.sim.ASSUMPTIONS` and shown by the CLI and studio. It never stands in
for hardware silently: transports report `is_hardware`, and the UI shows a
SIMULATOR / HARDWARE badge.

## D8. Joint calibration is data, and unverified until someone verifies it

The legacy class had no Genibo calibration. The SDK ships a profile assembled
from `CGenibo` motor-ID constants and a code comment (1024 counts per 333.3
degrees, centre 512), marked `verified = false`. Builds warn (G301), and
hardware runs refuse unverified joints unless the user opts in.

## D9. Legacy sources untouched; only what the tests need is vendored

The original OpenJigWare distribution was not modified. This repository
vendors exactly two upstream files, verbatim (`compat/legacy/`). One is
`COjw_25_Genibo.cs`, which the C# harness compiles; the other is Ex20's
`Form1.cs`, which the migration scanner reads. Both are stored byte for byte
(`.gitattributes`), and the golden data pins the C# file's SHA-256, so any
change is caught. The rest of the toolkit, its examples and the prebuilt DLLs
stay upstream. That keeps the repository small (a few MB instead of 1.2 GB of
binaries) and avoids redistributing third-party binaries with unknown licenses.

## D10. Packaging: PyInstaller, Inno Setup, hdiutil, nfpm; verify every artifact

*Decision.* PyInstaller one-folder bundles (its built-in PySide6 hooks are
mature, and builds take seconds), Inno Setup for the Windows installer, plain
`hdiutil` for DMGs, and nfpm for `.deb`/`.rpm`. nfpm is a single static
binary, needs no Ruby (unlike fpm) and no rpmbuild, and builds both formats
from one config. All of it is driven by `packaging/build.py`, so local and CI
builds are identical.
*Verification over assumption.* Every artifact is self-tested headless. Linux
dependencies are declared, then proven by installing into clean containers and
running `ldd` on every bundled file. The libc requirement is measured from the
binaries, not guessed.
*Rejected:* Nuitka / `pyside6-deploy` (long compile times, harder CI caching),
AppImage/Flatpak (asked for deb/rpm; can be added later from the same bundle).
*Trade-off.* Bundles are 60–75 MB and unsigned until certificates are configured.
