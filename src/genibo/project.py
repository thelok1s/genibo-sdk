"""Genibo projects: a ``genibo.toml`` manifest plus Python sources.

Layout created by ``genibo new``::

    my-robot/
      genibo.toml        # project, robot link, joint table
      src/main.py        # @program (compiled)  - or -  def main(robot) (host script)
      build/             # command plans (generated)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from genibo.errors import GeniboError
from genibo.joints import JointMap, load_profile
from genibo.program.compiler import BuildResult, compile_source
from genibo.protocol.constants import DEFAULT_BAUDRATE
from genibo.robot import DEFAULT_ROBOT_ID

MANIFEST = "genibo.toml"
KINDS = ("program", "script")


class ProjectError(GeniboError):
    pass


def _valid_name(name: str) -> bool:
    return bool(name) and name.replace("-", "").replace("_", "").isalnum()


def _confined(root: Path, rel: str, what: str) -> str:
    """Accept only a relative path that stays inside the project root (no absolute paths, no ``..``)."""
    candidate = Path(rel)
    if not rel or candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise ProjectError(f"{what} must be a relative path inside the project, got {rel!r}")
    if not (root / candidate).resolve().is_relative_to(root.resolve()):
        raise ProjectError(f"{what} {rel!r} resolves outside the project (symlink?)")
    return rel


@dataclass
class Project:
    root: Path
    name: str
    kind: str = "program"  # "program" (compiled plan) or "script" (host script, live link)
    entry: str = "src/main.py"
    program: str | None = None
    robot_id: int = DEFAULT_ROBOT_ID
    port: str = ""
    baudrate: int = DEFAULT_BAUDRATE
    encoding: str = "ascii"
    joint_profile: str = "legacy-cgenibo"
    joint_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    out_dir: str = "build"

    # ------------------------------------------------------------ loading
    @classmethod
    def find(cls, start: Path) -> Project:
        p = start.resolve()
        if p.is_file():
            p = p.parent
        for d in (p, *p.parents):
            if (d / MANIFEST).exists():
                return cls.load(d)
        raise ProjectError(f"no {MANIFEST} found in {start} or its parents")

    @classmethod
    def load(cls, root: Path) -> Project:
        root = Path(root).resolve()
        path = root / MANIFEST
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProjectError(f"{path} not found") from None
        except tomllib.TOMLDecodeError as exc:
            raise ProjectError(f"{path}: {exc}") from exc
        proj = data.get("project", {})
        robot = data.get("robot", {})
        joints = dict(data.get("joints", {}))
        build = data.get("build", {})
        if "name" not in proj:
            raise ProjectError(f"{path}: [project] name is required")
        kind = proj.get("kind", "program")
        if kind not in KINDS:
            raise ProjectError(f"{path}: [project] kind must be one of {KINDS}")
        profile = joints.pop("profile", "legacy-cgenibo")
        overrides = {k: v for k, v in joints.items() if isinstance(v, dict)}
        stray = [k for k, v in joints.items() if not isinstance(v, dict)]
        if stray:
            raise ProjectError(f"{path}: unexpected keys in [joints]: {', '.join(stray)}")
        name = str(proj["name"])
        if not _valid_name(name):
            raise ProjectError(f"{path}: [project] name {name!r} must be alphanumeric (with - or _)")
        entry = _confined(root, str(proj.get("entry", "src/main.py")), f"{path}: [project] entry")
        out_dir = _confined(root, str(build.get("out_dir", "build")), f"{path}: [build] out_dir")
        return cls(
            root=root,
            name=name,
            kind=kind,
            entry=entry,
            program=proj.get("program"),
            robot_id=int(robot.get("id", DEFAULT_ROBOT_ID)),
            port=str(robot.get("port", "")),
            baudrate=int(robot.get("baudrate", DEFAULT_BAUDRATE)),
            encoding=str(robot.get("encoding", "ascii")),
            joint_profile=str(profile),
            joint_overrides=overrides,
            out_dir=out_dir,
        )

    # ------------------------------------------------------------ derived
    @property
    def entry_path(self) -> Path:
        return self.root / self.entry

    @property
    def plan_path(self) -> Path:
        return self.root / self.out_dir / f"{self.name}.plan.json"

    @property
    def listing_path(self) -> Path:
        return self.root / self.out_dir / f"{self.name}.lst"

    def joint_map(self) -> JointMap:
        return load_profile(self.joint_profile).with_overrides(self.joint_overrides)

    # ------------------------------------------------------------ build
    def build(self, *, write: bool = True) -> BuildResult:
        if self.kind != "program":
            raise ProjectError(
                "script projects run on the host with a live link; there is nothing to compile"
            )
        source = self.entry_path.read_text(encoding="utf-8")
        result = compile_source(
            source,
            str(self.entry_path),
            joints=self.joint_map(),
            robot_id=self.robot_id,
            encoding=self.encoding,
            entry=self.program,
            display_path=self.entry,
        )
        if write and result.plan is not None:
            result.plan.save(self.plan_path)
            self.listing_path.write_text(result.plan.listing(detail=True), encoding="utf-8")
        return result


# ------------------------------------------------------------------ scaffolding

_MANIFEST_TEMPLATE = """\
# Genibo project manifest. See docs/PROJECTS.md.
[project]
name = "{name}"
kind = "{kind}"          # "program": compiled to a command plan | "script": host script with a live link
entry = "src/main.py"

[robot]
id = 0x20               # robot ID on the Bluetooth link (legacy example used 0x20)
port = ""               # e.g. "COM5", "/dev/tty.GENIBO-SPP", "/dev/rfcomm0"
baudrate = 115200
encoding = "ascii"      # robot-side file names; the legacy tool used the Windows ANSI code page

[joints]
# Built-in profile: names/IDs from OpenJigWare CGenibo, calibration from a code
# comment. NOT verified on hardware. Override per joint and set verified = true
# once you have confirmed a joint with small moves.
profile = "legacy-cgenibo"

# [joints.head_pan]
# servo_id = 15
# center = 512
# mech_move = 1024.0
# degree = 333.3
# direction = 0
# limit_up = 60.0
# limit_dn = -60.0
# verified = false

[build]
out_dir = "build"
"""

_PROGRAM_TEMPLATE = '''\
"""{name}: a compiled Genibo program.

`genibo build` runs this function once on your PC, records every step and
turns it into a command plan (build/{name}.plan.json). `genibo sim` plays the
plan against the simulator; `genibo run --port ...` streams it to the robot.
"""

from genibo.program import Program, program


@program
def main(g: Program) -> None:
    g.torque(True)
    g.pose(head_pan=0, head_tilt=0, tail=0, ms=600)
    for _ in range(3):
        g.pose(tail=25, ms=250)
        g.pose(tail=-25, ms=250)
    g.pose(head_pan=20, ms=400)
    g.pose(head_pan=-20, ms=800)
    g.pose(head_pan=0, ms=400)
    g.head_led_buzz(led=1, buzz=1)
'''

_SCRIPT_TEMPLATE = '''\
"""{name}: a host script. It runs on your PC and talks to the robot live,
so it can react to replies (battery, playback state, ...).

Run with:  genibo script src/main.py --sim       (simulator)
           genibo script src/main.py --port COM5  (robot)
"""

from genibo import Genibo, Storage


def main(robot: Genibo) -> None:
    battery = robot.battery()
    print(f"battery: robot {{battery.robot_percent}}%  MID {{battery.mid_percent}}%")
    print("master files:", robot.list_files(Storage.MASTER))
    if battery.robot_percent > 20:
        robot.play_motion("7.act", Storage.MASTER)
'''


def create_project(root: Path, name: str | None = None, *, kind: str = "program") -> Project:
    if kind not in KINDS:
        raise ProjectError(f"kind must be one of {KINDS}")
    root = Path(root)
    if (root / MANIFEST).exists():
        raise ProjectError(f"{root / MANIFEST} already exists")
    name = name or root.name
    if not _valid_name(name):
        raise ProjectError(f"project name {name!r} must be alphanumeric (with - or _)")
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(_MANIFEST_TEMPLATE.format(name=name, kind=kind), encoding="utf-8")
    template = _PROGRAM_TEMPLATE if kind == "program" else _SCRIPT_TEMPLATE
    main = root / "src" / "main.py"
    if not main.exists():
        main.write_text(template.format(name=name), encoding="utf-8")
    (root / ".gitignore").write_text("build/\n__pycache__/\n", encoding="utf-8")
    return Project.load(root)
