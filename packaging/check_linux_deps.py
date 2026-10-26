"""Verify an installed Linux package: every bundled ELF file's libraries resolve.

Run inside a *clean* container after installing the .deb/.rpm (the package
manager installs the declared dependencies). Any "not found" from ``ldd`` means
the package's dependency list is incomplete for that distribution.

    python3 check_linux_deps.py /opt/genibo-studio
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main(root: str) -> int:
    base = Path(root)
    # The PyInstaller bootloader puts its library directory on the search path at
    # run time; ldd has to see the same path or every bundled library looks missing.
    internal = base / "_internal"
    env = {**os.environ, "LD_LIBRARY_PATH": os.pathsep.join(str(p) for p in (internal, base) if p.exists())}
    missing: dict[str, set[str]] = {}
    checked = 0
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        with f.open("rb") as fh:
            if fh.read(4) != b"\x7fELF":
                continue
        checked += 1
        out = subprocess.run(["ldd", str(f)], capture_output=True, text=True, check=False, env=env).stdout
        for line in out.splitlines():
            if "not found" in line:
                missing.setdefault(line.split("=>")[0].strip(), set()).add(str(f.relative_to(base)))
    print(f"checked {checked} ELF files under {base}")
    for lib, users in sorted(missing.items()):
        print(f"MISSING {lib}  (needed by {', '.join(sorted(users)[:3])}{' ...' if len(users) > 3 else ''})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/opt/genibo-studio"))
