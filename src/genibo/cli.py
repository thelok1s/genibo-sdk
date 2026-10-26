"""``genibo`` command-line interface."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from genibo import __version__
from genibo.errors import Diagnostic, GeniboError, ResponseTimeout, RobotStatusError, TransportError
from genibo.log import configure

EXIT_OK, EXIT_BUILD, EXIT_USAGE, EXIT_LINK, EXIT_ROBOT = 0, 1, 2, 3, 4

HARDWARE_BANNER = "HARDWARE"
SIM_BANNER = "SIMULATOR (inferred behaviour - not evidence of hardware compatibility)"


def _print_diags(diags: Sequence[Diagnostic], *, root: Path | None = None) -> None:
    for d in diags:
        text = str(d)
        if root is not None:
            text = text.replace(str(root) + "/", "")
        stream = sys.stderr if d.severity in ("error", "warning") else sys.stdout
        print(text, file=stream)


def _load_plan_or_project(target: str | None):  # type: ignore[no-untyped-def]
    """Return (plan, project_or_None) from a plan.json path or a project directory/file."""
    from genibo.program.plan import CommandPlan
    from genibo.project import Project

    path = Path(target or ".")
    if path.is_file() and path.suffix == ".json":
        return CommandPlan.load(path), None
    project = Project.find(path)
    result = project.build()
    _print_diags(result.diagnostics, root=project.root)
    if not result.ok or result.plan is None:
        raise SystemExit(EXIT_BUILD)
    return result.plan, project


def _robot_from_args(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from genibo.joints import load_profile
    from genibo.robot import Genibo

    joints = None
    try:
        from genibo.project import Project

        joints = Project.find(Path(getattr(args, "project", None) or ".")).joint_map()
    except GeniboError:
        joints = load_profile("legacy-cgenibo")
    robot_id = int(args.robot_id, 0) if isinstance(args.robot_id, str) else args.robot_id
    if args.sim:
        from genibo.sim import VirtualGenibo

        dev = VirtualGenibo(robot_id=robot_id)
        dev.add_file(0, "7.act", b"(opaque simulated motion)")
        print(f"[{SIM_BANNER}]", file=sys.stderr)
        return Genibo.simulator(dev, robot_id=robot_id, joints=joints)
    if not args.port:
        raise SystemExit("error: give --port (e.g. COM5, /dev/tty.GENIBO) or --sim")
    print(f"[{HARDWARE_BANNER}] {args.port} @ {args.baud}, robot 0x{robot_id:02x}", file=sys.stderr)
    return Genibo.serial(args.port, args.baud, robot_id=robot_id, joints=joints)


def _add_link_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("link")
    g.add_argument("--port", help="serial port of the paired Genibo (COM5, /dev/tty.X, /dev/rfcomm0)")
    g.add_argument("--baud", type=int, default=115200)
    g.add_argument("--robot-id", default="0x20", help="robot ID on the Bluetooth link (default 0x20)")
    g.add_argument("--sim", action="store_true", help="use the in-process simulator instead of hardware")
    g.add_argument("--project", help="project whose joint table to use (default: search from cwd)")
    g.add_argument(
        "--listen",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="after the command, keep the link open and print every frame received",
    )


# --------------------------------------------------------------------------- commands


def cmd_new(args: argparse.Namespace) -> int:
    from genibo.project import create_project

    proj = create_project(Path(args.dir), args.name, kind=args.kind)
    print(f"created {proj.kind} project {proj.name!r} in {proj.root}")
    print(
        f"next: genibo build {args.dir} && genibo sim {args.dir}"
        if proj.kind == "program"
        else f"next: genibo script {proj.entry_path} --sim"
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    from genibo.project import Project

    project = Project.find(Path(args.target or "."))
    result = project.build(write=False)
    _print_diags(result.diagnostics, root=project.root)
    n_err = len(result.errors)
    print(
        f"{project.name}: {'OK' if result.ok else 'FAILED'} ({n_err} error(s), "
        f"{sum(d.severity == 'warning' for d in result.diagnostics)} warning(s))"
    )
    return EXIT_OK if result.ok else EXIT_BUILD


def cmd_build(args: argparse.Namespace) -> int:
    from genibo.project import Project

    project = Project.find(Path(args.target or "."))
    result = project.build()
    _print_diags(result.diagnostics, root=project.root)
    if not result.ok or result.plan is None:
        print(f"{project.name}: build FAILED", file=sys.stderr)
        return EXIT_BUILD
    plan = result.plan
    print(
        f"built {project.plan_path.relative_to(project.root)}: {len(plan.frames)} frames, {plan.duration_ms:.1f} ms, "
        f"sha256 {plan.frames_digest()[:16]}"
    )
    print(f"listing: {project.listing_path.relative_to(project.root)}")
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    from genibo.protocol.describe import describe_stream

    if args.hex:
        data = bytes.fromhex("".join(args.hex).replace(" ", "").replace(":", ""))
        for d in describe_stream(data):
            print(d)
        return EXIT_OK
    plan, _ = _load_plan_or_project(args.target)
    problems = plan.verify()
    print(plan.listing(detail=args.detail), end="")
    if problems:
        print("\n".join(f"VERIFY: {p}" for p in problems), file=sys.stderr)
        return EXIT_BUILD
    print(
        f"; verified: {len(plan.frames)} frames decode and round-trip; frames sha256 {plan.frames_digest()}"
    )
    return EXIT_OK


def cmd_sim(args: argparse.Namespace) -> int:
    from genibo.clock import RealClock, VirtualClock
    from genibo.link import Link
    from genibo.program.player import play_plan
    from genibo.sim import ASSUMPTIONS, SimTransport, VirtualGenibo

    plan, project = _load_plan_or_project(args.target)
    clock = RealClock() if args.realtime else VirtualClock()
    dev = VirtualGenibo(robot_id=plan.robot_id, clock=clock)
    for name in args.robot_file or []:
        storage, _, fname = name.partition(":")
        dev.add_file(0 if storage == "master" else 1, fname, b"(opaque)")
    print(f"[{SIM_BANNER}]")
    link = Link(SimTransport(dev), robot_id=plan.robot_id).open()
    try:
        report = play_plan(plan, link, clock=clock)
    finally:
        link.close()
    for e in dev.events:
        print(f"{e.t * 1000:9.1f} ms  {e.kind:9} {e.text}")
    joints = project.joint_map() if project else None
    print("\nfinal servo positions (raw -> deg via project calibration):")
    for sid, raw in sorted(dev.snapshot().items()):
        j = joints.by_servo_id(sid) if joints else None
        if j is None:
            continue
        ang = f"{j.to_angle(round(raw)):8.2f} deg" if j.calibration else ""
        print(f"  {j.name:16} id {sid:3}  raw {raw:7.1f} {ang}")
    print(f"\nsent {report.sent}/{report.total} frames; simulated duration {plan.duration_ms:.1f} ms")
    print("simulator assumptions:\n" + "\n".join(f"  - {a}" for a in ASSUMPTIONS))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    from genibo.link import Link
    from genibo.program.player import play_plan
    from genibo.transport.serial import SerialTransport

    plan, _ = _load_plan_or_project(args.target)
    unverified = [n for n, j in plan.joints.get("table", {}).items() if not j.get("verified")]
    moves = any(s["body"]["kind"] == "pose" for s in plan.steps)
    used = {k for s in plan.steps if s["body"]["kind"] == "pose" for k, _ in s["body"]["targets"]}
    risky = sorted(used & set(unverified))
    print(
        f"[{HARDWARE_BANNER}] plan {plan.name!r}: {len(plan.frames)} frames, {plan.duration_ms:.1f} ms -> "
        f"{args.port} robot 0x{plan.robot_id:02x}"
    )
    if moves and risky and not args.allow_unverified_joints:
        print(
            "refusing: the plan moves joints whose servo ID/calibration is not verified on hardware: "
            + ", ".join(risky)
            + "\nverify them (genibo.toml verified = true) or pass --allow-unverified-joints",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not args.yes:
        answer = input("Stream this plan to the robot now? Keep the robot clear. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted")
            return EXIT_OK
    cancel = threading.Event()
    link = Link(SerialTransport(args.port, args.baud), robot_id=plan.robot_id).open()
    try:
        report = play_plan(
            plan,
            link,
            cancel=cancel,
            on_cancel=args.on_cancel,
            on_frame=lambda i, f: print(f"{f.t_ms:9.1f} ms  {f.summary}"),
        )
    except KeyboardInterrupt:
        cancel.set()
        print("interrupted", file=sys.stderr)
        return EXIT_ROBOT
    finally:
        link.close()
    print(
        f"sent {report.sent}/{report.total} frames; worst lateness {report.max_lateness_ms:.1f} ms"
        + (" (CANCELLED)" if report.cancelled else "")
    )
    return EXIT_OK if not report.cancelled else EXIT_ROBOT


def cmd_script(args: argparse.Namespace) -> int:
    path = Path(args.file)
    spec = importlib.util.spec_from_file_location("genibo_host_script", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = getattr(module, "main", None)
    if not callable(main):
        raise SystemExit(f"{path} must define main(robot)")
    with _robot_from_args(args) as robot:
        main(robot)
    return EXIT_OK


def cmd_robot(args: argparse.Namespace) -> int:
    from genibo.protocol.constants import Storage

    storage = Storage.MASTER if getattr(args, "storage", "master") == "master" else Storage.USER
    with _robot_from_args(args) as robot:
        if args.listen > 0:
            from genibo.protocol.describe import describe

            def show(direction: str, raw: bytes, item: object) -> None:
                if direction != "tx":
                    summary = describe(item).summary if item is not None else ""  # type: ignore[arg-type]
                    print(f"{direction}: {raw.hex(' ')}  {summary}", flush=True)

            robot.link.add_trace(show)
        a = args.action
        if a == "status":
            b = robot.battery()
            out: dict[str, Any] = {
                "battery_robot_percent": b.robot_percent,
                "battery_mid_percent": b.mid_percent,
                "heartbeat_rtt_ms": round(robot.heartbeat() * 1000, 1),
                "is_playing": robot.is_playing(),
                "robot_time": robot.get_time().isoformat(),
            }
            print(json.dumps(out, indent=2))
        elif a == "files":
            print("\n".join(robot.list_files(storage)) or "(no files)")
        elif a == "upload":
            robot.upload_file(
                args.name or Path(args.local).name, Path(args.local), storage, ack_timeout=args.ack
            )
            print("sent")
        elif a == "download":
            data = robot.download_file(args.name, storage)
            Path(args.output or args.name).write_bytes(data)
            print(f"saved {len(data)} bytes to {args.output or args.name}")
        elif a == "delete":
            robot.delete_file(args.name, storage, ack_timeout=args.ack)
        elif a == "play":
            (robot.play_task if args.task else robot.play_motion)(args.name, storage)
        elif a == "stop":
            robot.stop_playback()
        elif a == "set-time":
            robot.set_time()
        elif a == "torque":
            robot.torque(args.state == "on")
        elif a == "ems":
            robot.emergency_stop()
        elif a == "mpsu-motion":
            robot.mpsu_play_motion(args.index, ready=args.ready)
        elif a == "led-buzz":
            robot.head_led_buzz(args.led, args.buzz)
        if args.listen > 0:
            import time

            time.sleep(args.listen)
    return EXIT_OK


def cmd_ports(args: argparse.Namespace) -> int:
    from genibo.transport.serial import list_ports

    ports = list_ports()
    if not ports:
        print("no serial ports found (pair the Genibo over Bluetooth first)")
    for p in ports:
        print(f"{p.device:32} {p.description}  [{p.hwid}]")
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    from genibo.legacy.migrate import scan

    report = scan(Path(args.file))
    print(report.render(), end="")
    if args.output:
        Path(args.output).write_text(report.skeleton(), encoding="utf-8")
        print(f"wrote skeleton {args.output}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", sys.version_info >= (3, 11), sys.version.split()[0]))
    for mod, why in (("serial", "pyserial (hardware link)"), ("PySide6", "studio GUI (optional extra)")):
        ok = importlib.util.find_spec(mod) is not None
        checks.append((mod, ok, why))
    dotnet = shutil.which("dotnet")
    checks.append(("dotnet", dotnet is not None, "regenerate golden data from the legacy C# (optional)"))
    try:
        from genibo.transport.serial import list_ports

        n = len(list_ports())
        checks.append(("serial ports", True, f"{n} found"))
    except Exception as exc:  # pragma: no cover - platform specific
        checks.append(("serial ports", False, str(exc)))
    for name, ok, info in checks:
        print(f"{'ok ' if ok else '-- '} {name:13} {info}")
    return EXIT_OK


def cmd_studio(args: argparse.Namespace) -> int:
    try:
        from genibo.studio.app import run
    except ImportError as exc:
        print(f"the studio needs PySide6: pip install 'genibo[studio]' ({exc})", file=sys.stderr)
        return EXIT_USAGE
    return run(args.target)


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="genibo", description="Genibo SDK toolchain")
    p.add_argument("--version", action="version", version=f"genibo {__version__}")
    p.add_argument("--log-level", default="warning", choices=["debug", "info", "warning", "error"])
    p.add_argument("--log-format", default="text", choices=["text", "json"])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("new", help="create a project")
    s.add_argument("dir")
    s.add_argument("--name")
    s.add_argument("--kind", choices=["program", "script"], default="program")
    s.set_defaults(func=cmd_new)

    for name, func, helptext in (
        ("validate", cmd_validate, "check a program without writing artifacts"),
        ("build", cmd_build, "compile a program to build/<name>.plan.json"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("target", nargs="?", help="project directory (default: current)")
        s.set_defaults(func=func)

    s = sub.add_parser("inspect", help="list/verify a plan, or decode raw hex")
    s.add_argument("target", nargs="?", help="plan .json or project directory")
    s.add_argument("--hex", nargs="+", help="decode these bytes instead")
    s.add_argument("--detail", action="store_true", help="show field-level breakdown")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("sim", help="play a plan on the simulator")
    s.add_argument("target", nargs="?")
    s.add_argument("--realtime", action="store_true", help="run in wall-clock time instead of virtual time")
    s.add_argument(
        "--robot-file", action="append", help="pretend a file exists on the robot, e.g. master:7.act"
    )
    s.set_defaults(func=cmd_sim)

    s = sub.add_parser("run", help="stream a plan to the physical robot")
    s.add_argument("target", nargs="?")
    s.add_argument("--port", required=True)
    s.add_argument("--baud", type=int, default=115200)
    s.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    s.add_argument("--allow-unverified-joints", action="store_true")
    s.add_argument("--on-cancel", choices=["nothing", "torque_free"], default="nothing")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("script", help="run a host script main(robot) with a live link")
    s.add_argument("file")
    _add_link_args(s)
    s.set_defaults(func=cmd_script)

    s = sub.add_parser("robot", help="one-off robot commands")
    rs = s.add_subparsers(dest="action", required=True)
    for action in ("status", "stop", "set-time", "ems"):
        a = rs.add_parser(action)
        _add_link_args(a)
    a = rs.add_parser("files")
    a.add_argument("--storage", choices=["master", "user"], default="master")
    _add_link_args(a)
    a = rs.add_parser("upload", help="store a local file on the robot (bytes sent verbatim)")
    a.add_argument("local")
    a.add_argument("--name")
    a.add_argument("--storage", choices=["master", "user"], default="user")
    a.add_argument("--ack", type=float, default=None, help="wait this long for a status acknowledgement")
    _add_link_args(a)
    a = rs.add_parser("download", help="fetch a file from the robot")
    a.add_argument("name")
    a.add_argument("--storage", choices=["master", "user"], default="master")
    a.add_argument("-o", "--output")
    _add_link_args(a)
    a = rs.add_parser("delete")
    a.add_argument("name")
    a.add_argument("--storage", choices=["master", "user"], default="user")
    a.add_argument("--ack", type=float, default=None)
    _add_link_args(a)
    a = rs.add_parser("play")
    a.add_argument("name")
    a.add_argument("--storage", choices=["master", "user"], default="master")
    a.add_argument("--task", action="store_true")
    _add_link_args(a)
    a = rs.add_parser("torque")
    a.add_argument("state", choices=["on", "off"])
    _add_link_args(a)
    a = rs.add_parser("mpsu-motion")
    a.add_argument("index", type=int)
    a.add_argument("--ready", action="store_true")
    _add_link_args(a)
    a = rs.add_parser("led-buzz")
    a.add_argument("--led", type=int, default=0)
    a.add_argument("--buzz", type=int, default=0)
    _add_link_args(a)
    s.set_defaults(func=cmd_robot)

    s = sub.add_parser("ports", help="list serial ports")
    s.set_defaults(func=cmd_ports)

    s = sub.add_parser("migrate", help="map OpenJigWare C# Genibo calls to the SDK")
    s.add_argument("file")
    s.add_argument("-o", "--output", help="write a Python skeleton here")
    s.set_defaults(func=cmd_migrate)

    s = sub.add_parser("doctor", help="check the environment")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("studio", help="open the development studio (needs the 'studio' extra)")
    s.add_argument("target", nargs="?")
    s.set_defaults(func=cmd_studio)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure(args.log_level, args.log_format)
    try:
        return int(args.func(args) or 0)
    except TransportError as exc:
        print(f"link error: {exc}", file=sys.stderr)
        return EXIT_LINK
    except (ResponseTimeout, RobotStatusError) as exc:
        print(f"robot error: {exc}", file=sys.stderr)
        return EXIT_ROBOT
    except GeniboError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
