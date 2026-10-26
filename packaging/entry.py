"""Entry point of the packaged application (PyInstaller).

* ``genibo-studio [PROJECT_DIR]``      open the studio
* ``genibo-studio --self-test [--screenshot PNG] [--log FILE]``   CI smoke test (QT_QPA_PLATFORM=offscreen)
* ``genibo-studio --cli ARGS...``      run the ``genibo`` command line
* invoked as ``genibo`` (Linux packages install that symlink) -> command line
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    invoked_as = Path(sys.argv[0]).stem.lower()
    if invoked_as == "genibo" or argv[:1] == ["--cli"]:
        from genibo.cli import main as cli_main

        return cli_main(argv[1:] if argv[:1] == ["--cli"] else argv)
    if argv[:1] == ["--version"]:
        from genibo import __version__

        print(f"genibo-studio {__version__}")
        return 0
    if argv[:1] == ["--self-test"]:
        from genibo.studio.selftest import run_self_test

        def opt(name: str) -> str | None:
            return argv[argv.index(name) + 1] if name in argv[:-1] else None

        return run_self_test(opt("--screenshot"), opt("--log"))
    from genibo.studio.app import run

    return run(argv[0] if argv else None)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
