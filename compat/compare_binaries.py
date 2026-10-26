"""Check that the *shipped* OpenJigWare.dll builds contain the same Genibo code as the source.

The golden data comes from compiling ``COjw_25_Genibo.cs``. Ex20_Genibo, however,
ran against prebuilt DLLs. This script decompiles the Genibo classes from both
the reference harness build and each shipped DLL with the same decompiler
(ILSpy) and compares them member by member, so code-generation differences of
the old and new C# compilers are normalised away as far as possible.

Requirements (not installed by the SDK):
    dotnet tool install ilspycmd --version 7.2.1 --tool-path <dir>
    dotnet build -c Release compat/csharp-reference

Usage:
    python compat/compare_binaries.py --ilspy <dir>/ilspycmd \\
        compat/csharp-reference/bin/Release/net6.0/GeniboReference.dll  <shipped OpenJigWare.dll>...

Members that differ must be reviewed by hand (``--show NAME`` prints the diff).
Typical benign causes: the decompiler cannot resolve .NET Framework's
System.IO.Ports in the shipped DLL (it then prints get_/set_ accessor calls),
or the two compilers allocate temporaries differently.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import subprocess
import sys
from pathlib import Path

CLASSES = ("CGenibo_Bt", "CGenibo")


def decompile(ilspy: str, dll: Path) -> list[str]:
    env = {**os.environ, "DOTNET_ROLL_FORWARD": "Major"}
    out = subprocess.run(
        [ilspy, "-t", "OpenJigWare.Ojw", str(dll)], capture_output=True, text=True, env=env, check=True
    )
    return out.stdout.splitlines()


def extract(lines: list[str], cls: str) -> list[str]:
    start = next(
        i for i, ln in enumerate(lines) if re.match(rf"^\t(public |internal |private )?class {cls}\b", ln)
    )
    depth, block = 0, []
    for ln in lines[start:]:
        block.append(ln)
        depth += ln.count("{") - ln.count("}")
        if depth == 0 and len(block) > 1:
            break
    return block


def members(block: list[str]) -> dict[str, list[str]]:
    res: dict[str, list[str]] = {}
    cur: list[str] = []
    key: str | None = None
    for ln in block[2:-1]:
        if re.match(r"^\t\t[^\t{}]", ln) and not ln.strip().startswith(("//", "[")):
            if key is not None:
                res.setdefault(key, []).append("\n".join(cur))
            key, cur = re.sub(r"\s+", " ", ln.strip()), [ln]
        else:
            cur.append(ln)
    if key is not None:
        res.setdefault(key, []).append("\n".join(cur))
    return res


def norm(bodies: list[str]) -> list[str]:
    return [re.sub(r"\s+", " ", b) for b in bodies]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ilspy", required=True)
    ap.add_argument("--show", help="print the diff of members whose signature contains this text")
    ap.add_argument("reference")
    ap.add_argument("shipped", nargs="+")
    args = ap.parse_args()
    ref_lines = decompile(args.ilspy, Path(args.reference))
    status = 0
    for dll in args.shipped:
        lines = decompile(args.ilspy, Path(dll))
        print(f"## {dll}")
        for cls in CLASSES:
            a, b = members(extract(ref_lines, cls)), members(extract(lines, cls))
            same = [k for k in a if k in b and norm(a[k]) == norm(b[k])]
            diff = [k for k in a if k in b and norm(a[k]) != norm(b[k])]
            missing = [k for k in a if k not in b] + [f"+{k}" for k in b if k not in a]
            print(
                f"  {cls}: {len(a)} members, identical {len(same)}, different {len(diff)}, unmatched {len(missing)}"
            )
            for k in diff:
                print(f"    differs: {k[:110]}")
                if args.show and args.show in k:
                    for ln in difflib.unified_diff(
                        "\n".join(a[k]).splitlines(),
                        "\n".join(b[k]).splitlines(),
                        "source",
                        "shipped",
                        n=1,
                        lineterm="",
                    ):
                        print("      " + ln)
            for k in missing:
                print(f"    unmatched: {k[:110]}")
            if missing:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
