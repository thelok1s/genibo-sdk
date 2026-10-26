"""Examples are regression fixtures: compiled ones must reproduce their checked-in plans."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from genibo.cli import main
from genibo.project import Project

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PROGRAMS = ["wave", "greeting"]
SCRIPTS = ["ex20_genibo", "status_monitor"]


@pytest.mark.parametrize("name", PROGRAMS)
def test_program_example_matches_expected_plan(name: str) -> None:
    project = Project.load(EXAMPLES / name)
    result = project.build(write=False)
    assert result.ok, [str(d) for d in result.diagnostics]
    assert result.plan is not None
    expected = json.loads((EXAMPLES / name / "expected.plan.json").read_text())
    got = result.plan.to_dict()
    # Same bytes at the same times, from the same source lines.
    assert got["frames"] == expected["frames"]
    assert got["frames_sha256"] == expected["frames_sha256"]
    assert got["steps"] == expected["steps"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_example_runs_on_simulator(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENIBO_ROUNDS", "1")
    assert main(["script", str(EXAMPLES / name / "src" / "main.py"), "--sim"]) == 0


def test_ex20_legacy_port_runs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["legacy_port.py"])
    runpy.run_path(str(EXAMPLES / "ex20_genibo" / "legacy_port.py"), run_name="__main__")
    assert "is playing: True" in capsys.readouterr().out


@pytest.mark.parametrize("name", PROGRAMS)
def test_plan_does_not_depend_on_checkout_location(name: str, tmp_path: Path) -> None:
    import shutil

    a, b = tmp_path / "a" / name, tmp_path / "elsewhere" / "deeper" / name
    shutil.copytree(EXAMPLES / name, a)
    shutil.copytree(EXAMPLES / name, b)
    plan_a = Project.load(a).build(write=False).plan
    plan_b = Project.load(b).build(write=False).plan
    assert plan_a is not None and plan_b is not None
    assert plan_a.to_json() == plan_b.to_json()
    assert str(tmp_path) not in plan_a.to_json()
