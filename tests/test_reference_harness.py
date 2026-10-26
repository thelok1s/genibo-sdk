"""Regenerate the golden data from the legacy C# source and compare with the checked-in copy.

Runs when the .NET SDK is installed (``dotnet`` on PATH). The harness compiles the
verbatim upstream copy in ``compat/legacy/``. This proves the fixtures still
describe the original implementation, not just the Python one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "compat" / "csharp-reference"
LEGACY = ROOT / "compat" / "legacy" / "OpenJigWare" / "COjw_25_Genibo.cs"  # verbatim upstream copy

pytestmark = [
    pytest.mark.dotnet,
    pytest.mark.skipif(shutil.which("dotnet") is None, reason="dotnet SDK not installed"),
]


def test_regenerated_golden_matches_checked_in(tmp_path: Path) -> None:
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    out = tmp_path / "golden.json"
    subprocess.run(
        ["dotnet", "run", "-c", "Release", "--project", str(HARNESS), "--", str(out)],
        check=True,
        capture_output=True,
        timeout=600,
        env=env,
    )
    fresh = json.loads(out.read_text())
    stored = json.loads((ROOT / "tests" / "golden" / "csharp_reference.json").read_text())
    for key in ("source_sha256", "tx", "rx", "tables"):
        assert fresh[key] == stored[key], f"{key} differs; regenerate tests/golden/csharp_reference.json"
